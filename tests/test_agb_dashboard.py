"""agb-dashboard -- open agterm's grid by NAME rather than by row id.

Three sections, in the order the command runs: the skeleton (sibling loading,
shared module identity, the parser), resolution and the strict `unresolved:`
check, and the lifecycle (the foreground hold, `--detach`, `--mru`).

Every test drives `main` with fakes that would RAISE if a subprocess were run,
which is how a refusal test proves nothing was OPENED rather than merely that
nothing was printed.

Plan: docs/plans/completed/20260827-agb-dashboard.md
"""

import ast
import importlib.util
import io
import os
import sys

import pytest
from importlib.machinery import SourceFileLoader

# ⚠️ `DASH_PATH` comes from `conftest`, which exists so the guards spanning both
# cell emitters have one spelling of it. A second copy here would drift from the
# one the cross-file guards use, and the divergence would show up as a guard
# reading a file nobody else is testing.
from conftest import DASH_PATH, PEER_PATH

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Out(object):
    def __init__(self):
        self.text = ""

    def write(self, s):
        self.text += s


class Pipe(Out):
    """An `out` that dies part-way through, the way `| head` makes stdout die.

    ⚠️ **A fake that cannot fail describes a world where the bug cannot
    happen** -- the same reason `Exploding`'s close RAISES. `agb-dashboard
    alice | head` closes stdout the instant `head` exits, and the next write
    raises `BrokenPipeError`; every line printed after the grid goes up was
    outside any guard, so that exception unwound past the close and the run
    ended with an orphaned grid. `ok` is how many writes succeed first, which
    is what lets one test target the cell report and another the hold's own
    banner.
    """

    def __init__(self, ok=0):
        Out.__init__(self)
        self.ok = ok

    def write(self, s):
        if self.ok <= 0:
            raise BrokenPipeError("closed")
        self.ok -= 1
        Out.write(self, s)


class NoCtl(object):
    """A `Ctl` that fails the test if anything is asked of it.

    A refusal test that only checks stdout would pass against an implementation
    that opened the grid and then complained, which is the exact failure mode
    this command exists to remove -- so the fake makes "no agtermctl call" an
    assertion rather than an inference.
    """

    def run(self, argv, **kw):
        raise AssertionError("agtermctl was called: %r" % (argv,))

    def __getattr__(self, name):
        raise AssertionError("ctl.%s was used" % (name,))


def no_read_line():
    raise AssertionError("stdin was read")


# ---------------------------------------------------------------------------
# loading -- one module object, shared with agb-peer's and agb-peer-setup's
# ---------------------------------------------------------------------------

def test_the_two_modules_share_one_PeerError(dashboard, peer):
    """`load_peer` returns `sys.modules[PEER_MODULE]` when it is there. If the
    key disagreed with the fixture's, this would build a second module object
    whose `PeerError` is a different class -- so `except peer.PeerError` around
    a call into this script would silently not catch."""
    assert dashboard.load_peer() is peer
    assert dashboard.load_peer().PeerError is peer.PeerError
    assert dashboard.load_peer().RosterConflict is peer.RosterConflict


def test_the_sys_modules_key_is_a_THREE_way_agreement(dashboard, setup):
    """⚠️ Three files now spell this key, not two (CLAUDE.md invariant 14):
    `agb-peer-setup`, `tests/conftest.py` and `agb-dashboard`. Any one of them
    drifting produces a second `agb-peer` module object."""
    from conftest import PEER_MODULE
    assert dashboard.PEER_MODULE == PEER_MODULE
    assert dashboard.PEER_MODULE == setup.PEER_MODULE


def test_load_peer_resolves_what_the_later_tasks_need(dashboard):
    """Non-vacuity first: assert the module actually loaded before asserting
    what is on it."""
    mod = dashboard.load_peer()
    assert mod is not None
    for name in ("sessions_of", "match_sessions", "parse_roster_text",
                 "dashboard_cells", "DASHBOARD_MAX_CELLS", "DASHBOARD_PANES",
                 "Ctl", "PeerError"):
        assert hasattr(mod, name), name


def test_peer_path_resolves_through_a_symlink(dashboard, tmp_path):
    """⚠️ The documented install is a SYMLINK onto `$PATH` beside `agb-peer`.

    `dirname(__file__)` on an unresolved path looks in the symlink's directory
    and finds nothing. A test run from the checkout never notices, because
    there the two coincide -- which is exactly why this builds the symlink case
    explicitly rather than trusting the happy path.
    """
    bindir = tmp_path / "bin"
    bindir.mkdir()
    link = str(bindir / "agb-dashboard")
    os.symlink(DASH_PATH, link)
    loader = SourceFileLoader("agb_dashboard_linked", link)
    spec = importlib.util.spec_from_file_location("agb_dashboard_linked", link,
                                                  loader=loader)
    linked = importlib.util.module_from_spec(spec)
    loader.exec_module(linked)
    assert linked.peer_path() == PEER_PATH
    assert os.path.exists(linked.peer_path())


def test_a_failed_load_leaves_nothing_registered(dashboard, tmp_path):
    """Without the `del`, the next call returns a half-initialised module as if
    it had loaded, so the second failure is a baffling AttributeError."""
    broken = tmp_path / "broken-peer"
    broken.write_text("this is not python(\n")
    key = dashboard.PEER_MODULE
    saved = sys.modules.pop(key, None)
    try:
        with pytest.raises(SyntaxError):
            dashboard.load_peer(str(broken))
        assert key not in sys.modules
    finally:
        if saved is not None:
            sys.modules[key] = saved


# ---------------------------------------------------------------------------
# the parser -- syntax
# ---------------------------------------------------------------------------

def test_selectors_are_collected_in_order(dashboard):
    opts = dashboard.parse_args(["alice", "bob"])
    assert opts["selectors"] == ["alice", "bob"]
    assert opts["roster"] is None
    assert opts["mru"] is False
    assert opts["detach"] is False


def test_detach_parses_beside_selectors(dashboard):
    opts = dashboard.parse_args(["--detach", "alice"])
    assert opts["detach"] is True
    assert opts["selectors"] == ["alice"]


def test_roster_takes_a_value_either_spelling(dashboard):
    assert dashboard.parse_args(["--roster", "/tmp/r"])["roster"] == "/tmp/r"
    assert dashboard.parse_args(["--roster=/tmp/r"])["roster"] == "/tmp/r"


def test_an_empty_inline_value_is_a_missing_value_error(dashboard):
    """The convention every parser in this project shares: `--roster=` is a
    missing value, not a file called "" -- which would be read as a refusal
    about a path nobody typed."""
    with pytest.raises(dashboard.DashError) as err:
        dashboard.parse_args(["--roster="])
    assert "needs a value" in str(err.value)


def test_a_trailing_value_flag_is_a_missing_value_error(dashboard):
    with pytest.raises(dashboard.DashError):
        dashboard.parse_args(["--roster"])


def test_a_flag_refuses_a_value(dashboard):
    with pytest.raises(dashboard.DashError) as err:
        dashboard.parse_args(["--mru=yes"])
    assert "takes no value" in str(err.value)


def test_an_unknown_option_names_itself(dashboard):
    with pytest.raises(dashboard.DashError) as err:
        dashboard.parse_args(["--font-size", "12"])
    assert "--font-size" in str(err.value)


def test_a_double_dash_ends_the_flags(dashboard):
    """A label may legitimately begin with a dash, and `--` is how it is named
    rather than refused as an unknown option."""
    opts = dashboard.parse_args(["--detach", "--", "--weird", "bob"])
    assert opts["selectors"] == ["--weird", "bob"]
    assert opts["detach"] is True


# ---------------------------------------------------------------------------
# the parser -- which question is being asked
# ---------------------------------------------------------------------------

def test_each_mode_is_recognised_on_its_own(dashboard):
    assert dashboard.select_mode(dashboard.parse_args(["a"])) == "selectors"
    assert dashboard.select_mode(
        dashboard.parse_args(["--roster", "/tmp/r"])) == "roster"
    assert dashboard.select_mode(dashboard.parse_args(["--mru"])) == "mru"


@pytest.mark.parametrize("argv,names", [
    (["--mru", "alice"], ("selectors", "--mru")),
    (["--mru", "--roster", "/tmp/r"], ("--roster", "--mru")),
    (["--roster", "/tmp/r", "alice"], ("selectors", "--roster")),
])
def test_two_modes_at_once_are_refused_NAMING_BOTH(dashboard, argv, names):
    """⚠️ Naming both is the requirement, not just refusing. The modes answer
    different questions, and a message naming only one of them reads as "that
    flag is broken" rather than as "you asked for two things"."""
    with pytest.raises(dashboard.DashError) as err:
        dashboard.select_mode(dashboard.parse_args(argv))
    for name in names:
        assert name in str(err.value), name


def test_no_mode_at_all_is_refused_and_says_what_to_do(dashboard):
    """`agb-dashboard --detach` names no rows, so there is nothing to open --
    and the message has to offer the two flags that would have worked, or the
    user is told only that they are wrong."""
    with pytest.raises(dashboard.DashError) as err:
        dashboard.select_mode(dashboard.parse_args(["--detach"]))
    assert "--roster" in str(err.value)
    assert "--mru" in str(err.value)


# ---------------------------------------------------------------------------
# dispatch
# ---------------------------------------------------------------------------

def test_no_argument_is_a_usage_error_that_writes_nothing(dashboard):
    out = Out()
    with pytest.raises(dashboard.DashError) as err:
        dashboard.main([], out=out, ctl=NoCtl(), read_line=no_read_line)
    assert err.value.code == 1
    assert "usage" in str(err.value)
    assert out.text == ""


def test_version_is_answered_without_loading_the_sibling(dashboard, peer,
                                                         monkeypatch):
    """A version query is one of the things asked in order to find out whether
    an install is sound, so it must answer in a tree where `agb-peer` is
    missing rather than dying on the load.

    ⚠️ **The registration has to be UNDONE, or this tests nothing.** The
    `dashboard` fixture depends on `peer`, which puts the module in
    `sys.modules` before any test runs -- so `load_peer` returns early and
    adding `and load_peer()` to the `--version` branch SURVIVED the suite. With
    the key removed and `peer_path` pointed at a file that is not there, a load
    is a `FileNotFoundError`, which is exactly the tree this claim is about.
    """
    monkeypatch.delitem(sys.modules, dashboard.PEER_MODULE)
    monkeypatch.setattr(dashboard, "peer_path",
                        lambda: os.path.join(REPO_ROOT, "no-such-agb-peer"))
    with pytest.raises(Exception):
        dashboard.load_peer()          # the arrangement is genuinely hostile
    out = Out()
    assert dashboard.main(["--version"], out=out, ctl=NoCtl()) == 0
    assert out.text.startswith("agb-dashboard ")


def test_version_is_answered_even_beside_a_broken_request(dashboard):
    """`--version` is answered BEFORE the mode rules, which is why it is not
    folded into the parser: an argv naming no mode is still a legal way to ask
    for the version."""
    out = Out()
    assert dashboard.main(["--version", "--mru", "alice"], out=out,
                          ctl=NoCtl()) == 0
    assert out.text.startswith("agb-dashboard ")


def test_help_prints_the_usage_and_succeeds(dashboard):
    out = Out()
    assert dashboard.main(["--help"], out=out, ctl=NoCtl()) == 0
    assert "usage" in out.text
    assert "--roster" in out.text
    assert "--mru" in out.text


def test_the_usage_does_not_offer_a_font_size(dashboard):
    """⚠️ An earlier revision of the plan invented `--font-size` with no
    evidence that agterm's dashboard accepts one. Inventing a flag is the exact
    thing this project's rule about designing against a page forbids, so its
    absence is pinned rather than left to a reader."""
    assert "font" not in dashboard.USAGE
    assert "--font-size" not in dashboard.DASH_FLAGS
    assert "--font-size" not in dashboard.DASH_VALUE_ARGS


@pytest.mark.parametrize("argv", [
    [],
    ["--detach"],
    ["--mru", "alice"],
    ["--mru", "--roster", "/tmp/r"],
    ["--roster", "/tmp/r", "alice"],
    ["--nope"],
    ["--roster"],
])
def test_a_refusal_opens_nothing_and_writes_nothing(dashboard, argv):
    """The companion every refusal test needs: not only that it complained, but
    that no `agtermctl` ran and no stdin was read on the way to complaining."""
    out = Out()
    with pytest.raises(dashboard.DashError):
        dashboard.main(argv, out=out, ctl=NoCtl(), read_line=no_read_line)
    assert out.text == ""


def test_an_accepted_argv_is_ACTED_ON_rather_than_refused(dashboard):
    """The non-vacuity companion to the refusals above: without it every test
    in this file would pass against a `main` that refused EVERYTHING.

    ⚠️ Tasks 4 and 5 replaced both stubs, so this no longer looks for an exit
    code -- it looks for the one thing every accepted argv does and no refused
    one does: call `agtermctl dashboard`."""
    ctl = GridCtl()
    assert dashboard.main(["--mru", "--detach"], out=Out(), ctl=ctl,
                          read_line=no_read_line) == 0
    assert ctl.opened() == [["--mru"]]


# ---------------------------------------------------------------------------
# the __main__ guard
# ---------------------------------------------------------------------------

def test_the_guard_names_every_class_that_can_reach_it(dashboard):
    """⚠️ Matched BY CLASS NAME, not in an `except` clause, because naming them
    there would require importing the sibling -- and a usage error is raised
    with no module in hand.

    ⚠️ **Asserted against a LITERAL set, not only against the implementation's
    own tuple.** A loop over `dashboard.HANDLED_ERRORS` asserts that whatever
    the guard lists is listed -- so widening it, which is the thing this test
    exists to notice, is exactly what it stops seeing.
    """
    assert set(dashboard.HANDLED_ERRORS) == set(
        ["PeerError", "AgbError", "OSError"])


@pytest.mark.parametrize("error", [
    OSError(13, "denied"),
    FileNotFoundError(2, "no such file"),      # what a missing roster raises
    PermissionError(13, "denied"),             # what an unreadable one raises
])
def test_a_REAL_filesystem_error_is_handled_not_a_traceback(dashboard, error):
    """🔴 The reachability half, and the defect it caught. The guard matched
    `type(error).__name__`, and no filesystem error is ever spelled `OSError`:
    it arrives as `FileNotFoundError` or `PermissionError`. Both entries
    written to catch these were INERT, so an unreadable roster ended in a
    traceback -- and the structural test above passed the whole time, because
    it asserted the implementation's own list was present and nothing about
    whether anything could match it."""
    assert dashboard.is_handled(error), (
        "%s reaches __main__ as a traceback" % (type(error).__name__,))


def test_a_PeerError_SUBCLASS_is_handled_through_its_parent(dashboard, peer):
    """`RosterConflict` used to need an entry of its own, because an exact
    `__name__` match cannot see inheritance. Over the MRO it falls out of
    `PeerError` -- which is the same reason the `OSError` entry now works."""
    assert dashboard.is_handled(peer.RosterConflict("busy"))
    assert dashboard.is_handled(peer.PeerError("nope"))


def test_an_unrelated_error_is_NOT_swallowed(dashboard):
    """The companion the assertions above need. Without it they would pass
    against an `is_handled` that returns True for everything -- which would
    turn every real bug in this script into a one-line message and an exit
    code, the shape a crash must not be able to hide in."""
    assert not dashboard.is_handled(ValueError("a genuine bug"))
    assert not dashboard.is_handled(KeyError("k"))


def test_the_guard_calls_is_handled_rather_than_re_spelling_it(dashboard):
    """Structural, because the alternative is running the script's `__main__`
    for each class. The check lives in a function so it can be tested at all;
    a copy of it inlined in the handler would be untested again."""
    tree = ast.parse(io.open(DASH_PATH, encoding="utf-8").read())
    main_block = [n for n in tree.body if isinstance(n, ast.If)]
    assert main_block, "no `if __name__ == \"__main__\":` block found"
    called = set()
    for node in ast.walk(main_block[-1]):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            called.add(node.func.id)
    assert called, "the walk found no calls -- it is broken"
    assert "is_handled" in called


def test_the_guard_handles_KeyboardInterrupt_in_a_clause_of_its_own():
    """⚠️ `KeyboardInterrupt` is not an `Exception`, so the class-name match
    above cannot see it -- and `Ctrl-C` is the documented way to end the
    foreground hold Task 5 adds, i.e. an exit rather than a crash.

    ⚠️ **Scoped to the `__main__` block, like its sibling above**, and it was
    not: walking the whole tree meant a `KeyboardInterrupt` clause ANYWHERE in
    the file answered this -- so moving the handler into `hold`, where it would
    catch the Ctrl-C the hold is required to let propagate, kept the test
    green. That is exactly the "mutation that MOVES a guard" `CLAUDE.md`
    names, written into the guard itself.
    """
    tree = ast.parse(io.open(DASH_PATH, encoding="utf-8").read())
    main_block = [n for n in tree.body if isinstance(n, ast.If)]
    assert main_block, "no `if __name__ == \"__main__\":` block found"
    handled = set()
    for node in ast.walk(main_block[-1]):
        if isinstance(node, ast.ExceptHandler) and isinstance(node.type,
                                                              ast.Name):
            handled.add(node.type.id)
    assert handled, "no except handler found -- the walk is broken"
    assert "KeyboardInterrupt" in handled
    assert "DashError" in handled


# ---------------------------------------------------------------------------
# the __main__ block, run for real
#
# ⚠️ Everything above drives `main` in-process, so the `__main__` block -- the
# handler, the exit codes, the shebang, the exec bit -- was covered by AST alone.
# `agb-codex` and `agb-tmux` both have a subprocess test for the same reason: a
# script that cannot be executed passes every structural guard ever written.
# ---------------------------------------------------------------------------

def run_script(*args):
    """Execute `agb-dashboard` as the shell would -> (rc, stdout, stderr).

    ⚠️ `$PATH` is narrowed to coreutils, so an `agtermctl` on the developer's
    machine cannot answer for one of these. None of these argvs should reach it
    anyway -- which is a claim worth making unfalsifiable rather than assuming.
    """
    import subprocess
    import conftest
    env = dict(os.environ)
    env["PATH"] = os.pathsep.join(["/usr/bin", "/bin"])
    proc = subprocess.Popen([DASH_PATH] + list(args), env=env,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE)
    out, err = conftest.communicate(proc, b"")
    return proc.returncode, out.decode(), err.decode()


def test_the_script_RUNS_and_answers_its_version():
    """The shebang, the exec bit and the dispatch, end to end. Everything else
    in this file imports the module and would pass on a file `sh` cannot run."""
    rc, out, err = run_script("--version")
    assert rc == 0, err
    assert out.startswith("agb-dashboard "), out


def test_a_usage_error_exits_1_on_STDERR_writing_nothing_to_stdout():
    """⚠️ stdout is where a caller looks for the grid it asked about, so a
    refusal must not appear there. The in-process tests assert `out.text == ""`
    against an injected seam; this asserts it of the real stream."""
    rc, out, err = run_script("--nope")
    assert rc == 1
    assert out == ""
    assert "unknown option" in err and "--nope" in err


def test_a_SIBLING_error_is_a_message_and_an_exit_code_not_a_traceback(
        tmp_path):
    """🔴 The `__main__` handler, doing its job for real: `PeerError` is raised
    by a module this file cannot name in an `except` clause. A regression here
    is a Python traceback where a one-line message belongs -- and the AST guard
    cannot see it, because a handler that matches nothing is still a handler."""
    rc, out, err = run_script("--roster", str(tmp_path / "nope"))
    assert rc == 1
    assert out == ""
    assert "cannot read the roster" in err
    assert "Traceback" not in err, err


# ---------------------------------------------------------------------------
# Task 4: resolution, the preflight, and the strict `unresolved:` failure
#
# This is the command's whole reason to exist. agterm opens the grid WITHOUT
# the cells it could not resolve, says so on stdout, and exits 0 -- so a
# wrapper that trusts the status shows a grid quietly missing the agent it was
# opened to watch.
# ---------------------------------------------------------------------------

def tree_of(sessions):
    """A `tree --json` reply carrying these sessions, in agterm's shape."""
    return {"result": {"tree": {"workspaces": [{"sessions": list(sessions)}]}}}


def rows(*pairs):
    return [{"id": row, "name": name} for row, name in pairs]


class GridCtl(object):
    """Records what was asked of agterm, and answers what the test says.

    ⚠️ `dashboard` returns the THREE-tuple `Ctl.dashboard` returns -- Task 1
    widened it precisely so stdout is visible -- and `dashboard_close` the
    two-tuple. A fake that modelled either as a bool would make the strict
    check untestable, which is the failure `agb-peer-setup`'s spinning
    `read_line` fake is the family's other example of.
    """

    def __init__(self, sessions=(), said="", ok=True, why="exit 1",
                 close=(True, "")):
        self.sessions = list(sessions)
        self._said, self._ok, self._why, self._close = said, ok, why, close
        self.calls = []

    def tree(self):
        self.calls.append(("tree",))
        return tree_of(self.sessions)

    def dashboard(self, members):
        self.calls.append(("dashboard", list(members)))
        return self._ok, self._said, self._why

    def dashboard_close(self):
        self.calls.append(("close",))
        return self._close

    def opened(self):
        return [c[1] for c in self.calls if c[0] == "dashboard"]


def grid(dashboard, argv, ctl, out=None):
    """Task 4's driver: `--detach`, so these tests stay about RESOLUTION.

    ⚠️ Task 5 made the foreground hold the default, so without this every
    success case here would block on `read_line` and every "nothing was
    closed" assertion would be about the hold's own tidy-up rather than about
    the strict `unresolved:` check it was written for. The lifecycle has its
    own section below; `no_read_line` keeps it out of this one.
    """
    return dashboard.main(["--detach"] + list(argv), out=out or Out(), ctl=ctl,
                          read_line=no_read_line)


# --- resolve_selectors itself ---------------------------------------------

def test_resolve_selectors_takes_sessions_not_a_ctl(dashboard):
    """⚠️ The signature is the property. Taking a `ctl` would mean one
    `agtermctl tree --json` per selector -- nine subprocesses for a full grid,
    and the row set can move between them, so two cells could be answered by
    two different worlds."""
    import inspect
    names = list(inspect.signature(dashboard.resolve_selectors).parameters)
    assert names[:2] == ["sessions", "selectors"]
    assert "ctl" not in names


def test_a_bare_selector_defaults_to_the_left_pane(dashboard, peer):
    """⚠️ And the NAME of a bare selector is the selector itself. Every report
    about a cell has to name it with the word the user wrote -- a row-id prefix
    is the one thing this command exists to stop people looking up."""
    got, problems, folded = dashboard.resolve_selectors(
        rows(("AAAA1111", "alice")), ["alice"], peer)
    assert problems == [] and folded == []
    assert got == [("alice", "AAAA1111", "left")]


def test_unresolved_and_ambiguous_are_told_apart(dashboard, peer):
    """⚠️ Classified on `len(match_sessions(...))`, not by calling `resolve`:
    that raises one `PeerError` code 2 for unresolved, ambiguous and
    no-sessions-at-all alike, so telling them apart through it would mean
    string-matching an error message."""
    sessions = rows(("AAAA1111", "api"), ("BBBB2222", "api-refactor"))
    got, problems, _folded = dashboard.resolve_selectors(
        sessions, ["api", "nobody"], peer)
    assert got == []
    assert len(problems) == 2
    assert "matches 2 rows" in problems[0] and "api-refactor" in problems[0]
    assert "no row matches" in problems[1] and "nobody" in problems[1]


def test_two_selectors_naming_one_cell_are_DEDUPED_not_refused(dashboard, peer):
    """Two ways of naming one row is not a user error; spending two of the nine
    cells on it is the bug. First-seen order is kept."""
    sessions = rows(("AAAA1111", "alice"), ("BBBB2222", "bob"))
    got, problems, folded = dashboard.resolve_selectors(
        sessions, ["alice", "bob", "AAAA1111", "ALIC"], peer)
    assert problems == []
    assert got == [("alice", "AAAA1111", "left"), ("bob", "BBBB2222", "left")]
    # ⚠️ And WHAT was folded comes back, because a silent drop is the worse
    # bug: the relay reports the identical situation by name, and until this
    # nothing anywhere said which of two names for one cell had gone.
    assert folded == [("AAAA1111", "alice"), ("ALIC", "alice")]


def test_the_dedupe_key_is_id_AND_pane_not_the_id_alone(dashboard, peer):
    """⚠️ `X:left` and `X:right` are two legitimate, distinct cells, and a
    roster may hold `alice=<label>` beside `split=<same label>:right`. Deduping
    by id would silently drop one -- the same missing-cell class this command
    exists to remove."""
    sessions = rows(("AAAA1111", "alice"))
    got, problems, folded = dashboard.resolve_selectors(
        sessions, [("a", "alice", "left"), ("b", "alice", "right"),
                   ("c", "alice", "left")], peer)
    assert problems == []
    assert got == [("a", "AAAA1111", "left"), ("b", "AAAA1111", "right")]
    assert folded == [("c", "a")], "only the third names a cell already taken"


# --- the refusals, none of which may call `agtermctl dashboard` ------------

def test_one_bad_selector_among_three_opens_NOTHING(dashboard):
    """⚠️ "no `agtermctl dashboard` call", NOT "no agtermctl call": the
    `tree --json` has already run by design, and a test asserting otherwise
    would push an implementer back into the per-selector-subprocess shape."""
    ctl = GridCtl(rows(("AAAA1111", "alice"), ("BBBB2222", "bob")))
    out = Out()
    with pytest.raises(dashboard.DashError) as err:
        grid(dashboard, ["alice", "carol", "bob"], ctl, out)
    assert err.value.code != 0
    assert "carol" in str(err.value)
    assert ctl.opened() == []
    assert ("tree",) in ctl.calls, "the tree fetch is the design, not a defect"
    assert out.text == ""


def test_an_ambiguous_selector_is_refused_and_NAMES_ITS_MATCHES(dashboard):
    ctl = GridCtl(rows(("AAAA1111", "api"), ("BBBB2222", "api-refactor")))
    with pytest.raises(dashboard.DashError) as err:
        grid(dashboard, ["api"], ctl)
    assert "api-refactor" in str(err.value)
    assert ctl.opened() == []


def test_no_rows_at_all_says_so_rather_than_naming_every_selector(dashboard):
    ctl = GridCtl([])
    with pytest.raises(dashboard.DashError) as err:
        grid(dashboard, ["alice", "bob"], ctl)
    assert "no sessions" in str(err.value)
    assert ctl.opened() == []


def test_ten_selectors_are_refused_before_the_dashboard_call(dashboard, peer):
    """The cap, from the side that must refuse. It names the cap, because
    "too many" without the number is a refusal you cannot act on."""
    sessions = rows(*[("ROW%d" % n, "agent%d" % n) for n in range(10)])
    ctl = GridCtl(sessions)
    with pytest.raises(dashboard.DashError) as err:
        grid(dashboard, ["agent%d" % n for n in range(10)], ctl)
    assert str(peer.DASHBOARD_MAX_CELLS) in str(err.value)
    assert ctl.opened() == []


def test_nine_selectors_are_accepted(dashboard, peer):
    """The other side of the same boundary. Without it the refusal above passes
    against an implementation that refuses every grid."""
    assert peer.DASHBOARD_MAX_CELLS == 9, "the boundary moved; so must this"
    sessions = rows(*[("ROW%d" % n, "agent%d" % n) for n in range(9)])
    ctl = GridCtl(sessions)
    assert grid(dashboard, ["agent%d" % n for n in range(9)], ctl) == 0
    assert len(ctl.opened()) == 1
    assert len(ctl.opened()[0]) == 9


def test_ten_selectors_that_DEDUPE_to_nine_are_opened(dashboard, peer):
    """⚠️ The cap is counted AFTER the dedupe, and nothing tested that: both cap
    tests used distinct rows, so no case existed where deduping brought the
    count under nine. `len(resolved)` -> `len(wanted)` survived the suite.

    Two spellings of one cell is not a user error; spending two of the nine on
    it is the bug -- so the tenth selector must cost nothing at all."""
    sessions = rows(*[("ROW%d" % n, "agent%d" % n) for n in range(9)])
    ctl = GridCtl(sessions)
    argv = ["agent%d" % n for n in range(9)] + ["ROW0"]
    assert len(argv) > peer.DASHBOARD_MAX_CELLS, "the tenth selector is missing"
    assert grid(dashboard, argv, ctl) == 0
    assert len(ctl.opened()) == 1 and len(ctl.opened()[0]) == 9


def test_a_scratch_participant_is_a_SHORTFALL_here(dashboard, tmp_path):
    """⚠️ Unlike in the relay, where the same exclusion is reported and the grid
    opens anyway. The relay's grid is an adjunct to carrying messages; this
    command's entire output IS the grid, so a roster naming a participant the
    grid cannot show must refuse rather than open one quietly missing them."""
    roster = tmp_path / "peers"
    roster.write_text("alice=alice\ndrawer=bob:scratch\n")
    ctl = GridCtl(rows(("AAAA1111", "alice"), ("BBBB2222", "bob")))
    with pytest.raises(dashboard.DashError) as err:
        grid(dashboard, ["--roster", str(roster)], ctl)
    assert "scratch" in str(err.value)
    assert ctl.opened() == []


def test_a_ROSTER_refusal_names_the_participant_AND_the_label(dashboard,
                                                              tmp_path):
    """🔴 Two roster lines pointing at one dead label produced two BYTE-IDENTICAL
    refusals, naming neither line to edit -- the same "go and look up who
    vanished" the row-id spelling caused, in the function whose own docstring
    argues against it. The participant says which line; the label says what on
    it is wrong, so a refusal carries both."""
    roster = tmp_path / "peers"
    roster.write_text("carol=oldrow\ndave=oldrow:right\n")
    ctl = GridCtl(rows(("AAAA1111", "alice")))
    with pytest.raises(dashboard.DashError) as err:
        grid(dashboard, ["--roster", str(roster)], ctl)
    body = str(err.value)
    assert "carol (oldrow)" in body, body
    assert "dave (oldrow)" in body, body
    assert ctl.opened() == []


def test_an_ambiguous_ROSTER_line_names_the_participant_too(dashboard,
                                                            tmp_path):
    """The other refusal in the same function, which had the identical gap."""
    roster = tmp_path / "peers"
    roster.write_text("carol=api\n")
    ctl = GridCtl(rows(("AAAA1111", "api"), ("BBBB2222", "api-refactor")))
    with pytest.raises(dashboard.DashError) as err:
        grid(dashboard, ["--roster", str(roster)], ctl)
    assert "carol (api)" in str(err.value), str(err.value)


def test_a_POSITIONAL_selector_is_still_named_once(dashboard):
    """⚠️ The companion, and the reason `asked()` is a conditional rather than
    an unconditional pair: a bare selector IS its own name, so the second token
    would be the first one repeated -- `alice (alice)`."""
    ctl = GridCtl(rows(("AAAA1111", "alice")))
    with pytest.raises(dashboard.DashError) as err:
        grid(dashboard, ["carol"], ctl)
    assert "carol: no row matches" in str(err.value), str(err.value)
    assert "carol (carol)" not in str(err.value), str(err.value)


def test_the_excluded_participant_is_named_by_NAME_not_by_row_id(dashboard,
                                                                 tmp_path):
    """⚠️ The relay's argued rule, which this side did not carry: *by NAME, not
    by row id -- the operator wrote `drawer=...:scratch`, and a hex prefix
    would make them go and look up which participant vanished*. It applies
    HARDER here, because this REFUSES: the token in the message is the only
    clue which roster line to edit, and `agb-refresh` re-mints every id, which
    is the trap the whole command routes around.

    ⚠️ The test that shipped asserted only `"scratch" in str(err.value)`, so
    the id-prefix spelling passed it. The row id is asserted ABSENT here, which
    is the half that pins the rule."""
    roster = tmp_path / "peers"
    roster.write_text("alice=alice\ndrawer=bob:scratch\n")
    ctl = GridCtl(rows(("AAAA1111", "alice"), ("BBBB2222", "bob")))
    with pytest.raises(dashboard.DashError) as err:
        grid(dashboard, ["--roster", str(roster)], ctl)
    assert "drawer (scratch)" in str(err.value), str(err.value)
    assert "BBBB2222" not in str(err.value), str(err.value)


def test_the_cell_CAP_is_counted_after_the_pane_exclusion(dashboard, peer,
                                                          tmp_path):
    """⚠️ The order is the diagnosis. Ten participants of which two are in the
    drawer is EIGHT gridable cells -- a roster one edit from working -- and the
    cap ran first, so it refused with "shows 9 cells; got 10" and never
    mentioned the two `:scratch` lines. Two round trips to fix one roster, the
    second of them chasing a number that was never the problem. The relay
    applies its cap after the exclusion for the same reason."""
    assert peer.DASHBOARD_MAX_CELLS == 9, "the boundary moved; so must this"
    names = ["p%d=agent%d" % (n, n) for n in range(8)]
    roster = tmp_path / "peers"
    roster.write_text("\n".join(names + ["d1=agent8:scratch",
                                         "d2=agent9:scratch"]) + "\n")
    ctl = GridCtl(rows(*[("ROW%d" % n, "agent%d" % n) for n in range(10)]))
    with pytest.raises(dashboard.DashError) as err:
        grid(dashboard, ["--roster", str(roster)], ctl)
    assert "d1 (scratch)" in str(err.value), str(err.value)
    assert "got 10" not in str(err.value), str(err.value)
    assert ctl.opened() == []


def test_the_cap_still_fires_when_nothing_is_excluded(dashboard, peer,
                                                      tmp_path):
    """The companion the reorder needs: it differs in the one variable under
    test, so the assertion above cannot pass against a cap that never runs."""
    roster = tmp_path / "peers"
    roster.write_text("\n".join("p%d=agent%d" % (n, n)
                                for n in range(10)) + "\n")
    ctl = GridCtl(rows(*[("ROW%d" % n, "agent%d" % n) for n in range(10)]))
    with pytest.raises(dashboard.DashError) as err:
        grid(dashboard, ["--roster", str(roster)], ctl)
    assert "got 10" in str(err.value), str(err.value)
    assert str(peer.DASHBOARD_MAX_CELLS) in str(err.value)
    assert ctl.opened() == []


def test_two_names_for_one_cell_are_REPORTED_not_silently_folded(dashboard,
                                                                 tmp_path):
    """⚠️ The dedupe is right -- two ways of naming one cell is not a user
    error, spending two of the nine on it is the bug -- but it was SILENT.
    A roster is the relay's own membership grammar, and there the identical
    situation is reported by name (`_one_name_per_row`). Here `carol` was
    simply not on the screen, with nothing saying which name went."""
    roster = tmp_path / "peers"
    roster.write_text("alice=alice\ncarol=alice\n")
    ctl = GridCtl(rows(("AAAA1111", "alice")))
    out = Out()
    assert grid(dashboard, ["--roster", str(roster)], ctl, out) == 0
    assert ctl.opened() == [["AAAA1111:left"]], "the dedupe itself must stand"
    assert "carol" in out.text and "alice" in out.text, out.text
    assert "same cell" in out.text, out.text


def test_a_grid_with_no_folded_names_says_nothing_about_them(dashboard):
    """The companion: without it the assertion above passes against a line
    that can never be printed."""
    ctl = GridCtl(rows(("AAAA1111", "alice"), ("BBBB2222", "bob")))
    out = Out()
    assert grid(dashboard, ["alice", "bob"], ctl, out) == 0
    assert "same cell" not in out.text, out.text


# --- opening, and the strict check on stdout ------------------------------

@pytest.mark.parametrize("argv,sessions,code", [
    (["--nope"], [], 1),                       # a usage error
    ([], [], 1),                               # no mode at all
    (["--mru", "alice"], [], 1),               # two modes
    (["carol"], [("AAAA1111", "alice")], 2),   # a shortfall it detected
    (["api"], [("A", "api"), ("B", "api2")], 2),
    (["alice"], [], 2),                        # no rows at all
])
def test_the_exit_CODE_says_which_kind_of_refusal_it_was(dashboard, argv,
                                                         sessions, code):
    """⚠️ Pinned to the NUMBER, not to "non-zero". Every refusal test here
    asserted `code != 0`, so changing a 2 to a 1 survived the suite -- and the
    two mean different things to a script wrapping this one: 1 is *you typed
    something I cannot act on*, 2 is *I could not show what you asked for*."""
    with pytest.raises(dashboard.DashError) as err:
        grid(dashboard, argv, GridCtl(rows(*sessions)))
    assert err.value.code == code


def test_a_ROSTER_the_sibling_refuses_exits_1_not_2(dashboard, tmp_path):
    """⚠️ MEASURED, and it is not what the flag table used to say: an
    unreadable or malformed roster, and an agtermctl that will not start, all
    arrive as `PeerError` and carry ITS code, which is 1. Only the shortfalls
    this script detects for itself are 2. Documented as measured rather than as
    intended -- the alternative is a doc that describes a exit code nothing
    produces."""
    roster = tmp_path / "peers"
    roster.write_text("=broken\n")
    ctl = GridCtl(rows(("AAAA1111", "alice")))
    with pytest.raises(Exception) as err:
        grid(dashboard, ["--roster", str(roster)], ctl)
    assert not isinstance(err.value, dashboard.DashError)
    assert getattr(err.value, "code", None) == 1
    assert ctl.opened() == []


def test_a_grid_is_opened_with_explicit_panes_and_reported(dashboard):
    ctl = GridCtl(rows(("AAAA1111", "alice"), ("BBBB2222", "bob")))
    out = Out()
    assert grid(dashboard, ["alice", "bob"], ctl, out) == 0
    assert ctl.opened() == [["AAAA1111:left", "BBBB2222:left"]]
    # On the record: what was opened, by row and pane, with the label that
    # named it -- so a screenshot of the terminal explains the screen.
    assert "AAAA1111" in out.text and "alice" in out.text
    assert "bob" in out.text


def test_a_roster_pane_is_PRESERVED_not_forced_to_left(dashboard, tmp_path):
    roster = tmp_path / "peers"
    roster.write_text("alice=alice\nsplit=alice:right\n")
    ctl = GridCtl(rows(("AAAA1111", "alice")))
    assert grid(dashboard, ["--roster", str(roster)], ctl) == 0
    assert ctl.opened() == [["AAAA1111:left", "AAAA1111:right"]]


def test_a_ONE_participant_roster_is_accepted(dashboard, tmp_path):
    """⚠️ `minimum=1`, not the parser's default 2. A relay needs somebody to
    talk to; a grid does not, and agterm accepts a one-cell grid (measured)."""
    roster = tmp_path / "peers"
    roster.write_text("alice=alice\n")
    ctl = GridCtl(rows(("AAAA1111", "alice")))
    assert grid(dashboard, ["--roster", str(roster)], ctl) == 0
    assert ctl.opened() == [["AAAA1111:left"]]


def test_an_EMPTY_roster_is_refused_before_anything_opens(dashboard, tmp_path):
    """A file with no participants in it -- all comments, or truncated by the
    editor that was writing it. `minimum=1` is what answers this: a grid needs
    nobody to talk to, but it does need somebody to show."""
    roster = tmp_path / "peers"
    roster.write_text("# everyone left\n\n")
    ctl = GridCtl(rows(("AAAA1111", "alice")))
    with pytest.raises(Exception) as err:
        grid(dashboard, ["--roster", str(roster)], ctl)
    assert "roster is empty" in str(err.value)
    assert ctl.opened() == []


def test_a_session_with_NO_NAME_is_reported_by_id_rather_than_crashing(
        dashboard):
    """⚠️ agterm's `name` is what the bridge renamed the row to, and a row it
    has not renamed yet has none. The report is the record of what went on the
    screen, so it must survive that -- by id and pane, with the label blank."""
    ctl = GridCtl([{"id": "AAAA1111"}])
    out = Out()
    assert grid(dashboard, ["AAAA1111"], ctl, out) == 0
    assert ctl.opened() == [["AAAA1111:left"]]
    assert "AAAA1111 left" in out.text, out.text


def test_a_dashboard_that_will_not_open_is_reported_and_not_closed(dashboard):
    """agterm refuses an invalid id, and a wholly unresolvable set, BEFORE
    opening anything -- so there is no grid to close on this path, and closing
    would dismiss whatever somebody else had up."""
    ctl = GridCtl(rows(("AAAA1111", "alice")), ok=False, why="boom")
    with pytest.raises(dashboard.DashError) as err:
        grid(dashboard, ["alice"], ctl)
    assert "boom" in str(err.value)
    assert ("close",) not in ctl.calls


def test_unresolved_on_STDOUT_is_a_failure_despite_exit_zero(dashboard):
    """🔴 The regression for shipping the bug the command exists to fix.

    agterm exits 0 while printing `unresolved: <id>` and opening the grid
    without those cells, so this is the one place the exit status is
    deliberately not trusted."""
    ctl = GridCtl(rows(("AAAA1111", "alice"), ("BBBB2222", "bob")),
                  said="unresolved: BBBB2222\n")
    out = Out()
    with pytest.raises(dashboard.DashError) as err:
        grid(dashboard, ["alice", "bob"], ctl, out)
    assert err.value.code != 0
    assert "BBBB2222" in str(err.value)


def test_the_strict_failure_CLOSES_the_grid_before_exiting(dashboard):
    """🔴 `unresolved:` is printed AFTER agterm has already opened the grid with
    the rest, so refusing and exiting would leave exactly the partially
    populated grid this command exists to remove -- and with no hold running,
    nothing else would ever close it."""
    ctl = GridCtl(rows(("AAAA1111", "alice"), ("BBBB2222", "bob")),
                  said="unresolved: BBBB2222\n")
    with pytest.raises(dashboard.DashError):
        grid(dashboard, ["alice", "bob"], ctl)
    kinds = [c[0] for c in ctl.calls]
    assert "close" in kinds, "the partial grid was left on the screen"
    assert kinds.index("close") > kinds.index("dashboard")


def test_a_close_that_FAILS_is_said_out_loud(dashboard):
    """The grid is still up and nothing else will close it, so the message has
    to say so rather than read as a tidy refusal.

    ⚠️ **And it prints the literal close command**, which it did not: that was
    reserved for `--detach` and for a failed close in the HOLD, on the argument
    that this is the moment a user most needs it and least wants to go and look
    it up. It is the same moment. Two docs said it was printed here already."""
    ctl = GridCtl(rows(("AAAA1111", "alice")), said="unresolved: ZZZZ\n",
                  close=(False, "no such dashboard"))
    with pytest.raises(dashboard.DashError) as err:
        grid(dashboard, ["alice"], ctl)
    assert "STILL UP" in str(err.value)
    assert "no such dashboard" in str(err.value)
    assert dashboard.CLOSE_COMMAND in str(err.value), str(err.value)


def test_a_close_that_WORKS_does_not_print_the_command(dashboard):
    """The companion: a run that tidied up after itself has nothing for the
    user to do, and an unconditional command reads as one."""
    ctl = GridCtl(rows(("AAAA1111", "alice")), said="unresolved: ZZZZ\n")
    with pytest.raises(dashboard.DashError) as err:
        grid(dashboard, ["alice"], ctl)
    assert "closed it again" in str(err.value)
    assert dashboard.CLOSE_COMMAND not in str(err.value), str(err.value)


class Exploding(GridCtl):
    """A `Ctl` whose close RAISES, which is what the real one does.

    ⚠️ `Ctl.dashboard_close` returns a status for a tool that RAN and refused,
    and `_spawn` RAISES `PeerError` when agtermctl cannot be started at all: a
    removed binary, a `$PATH` an agterm pane did not inherit, the
    `/proc/<pid>/exe (deleted)` case after an upgrade. A fake that can only
    return a two-tuple describes a world where that cannot happen.
    """

    def dashboard_close(self):
        self.calls.append(("close",))
        raise RuntimeError("agtermctl: [Errno 2] No such file or directory")


def test_a_close_that_RAISES_still_names_the_grid_it_left_up(dashboard):
    """🔴 The failure this command exists to prevent, reached through its own
    cleanup. `unresolved:` means the grid is ALREADY UP; if the close raises,
    the `DashError` naming it is never constructed and the user gets an errno
    with no word that a partial grid is on their screen."""
    ctl = Exploding(rows(("AAAA1111", "alice")), said="unresolved: ZZZZ\n")
    with pytest.raises(dashboard.DashError) as err:
        grid(dashboard, ["alice"], ctl)
    assert "STILL UP" in str(err.value)
    assert "Errno 2" in str(err.value), "the reason the close failed is lost"
    assert ("close",) in ctl.calls


def test_a_close_that_RAISES_during_the_hold_is_said_not_raised(dashboard):
    """The same call in the hold's `finally`, where a raise replaces the exit
    -- an Enter, an EOF or a `Ctrl-C` -- with a traceback out of the cleanup."""
    ctl = Exploding(rows(("AAAA1111", "alice")))
    out = Out()
    assert dashboard.main(["alice"], out=out, ctl=ctl,
                          read_line=Reader("")) == 0
    assert "IT IS STILL UP" in out.text
    assert dashboard.CLOSE_COMMAND in out.text


def test_close_grid_never_raises_and_says_why(dashboard):
    """The unit behind both, and the companion that keeps them honest: it must
    still pass a working close through unchanged."""

    class RaisingClose(object):
        # ⚠️ Named for what it does rather than `Boom`, which is the module
        # level reader fake fifty lines below: two different classes of that
        # name, one shadowing the other inside one function, is a trap for
        # whoever moves either.
        def dashboard_close(self):
            raise RuntimeError("no agtermctl")

    class Fine(object):
        def dashboard_close(self):
            return True, ""

    closed, why = dashboard.close_grid(RaisingClose())
    assert closed is False and "no agtermctl" in why
    assert dashboard.close_grid(Fine()) == (True, "")


def test_a_clean_open_is_not_read_as_unresolved(dashboard):
    """The companion to the two above: a test that something did not happen
    needs one that differs only in the variable under test, or it passes
    against a check that can never fire."""
    ctl = GridCtl(rows(("AAAA1111", "alice")), said="opened 1 cell\n")
    assert grid(dashboard, ["alice"], ctl) == 0
    assert ("close",) not in ctl.calls


# ---------------------------------------------------------------------------
# Task 5: the lifecycle -- the foreground hold, `--detach`, and `--mru`
#
# agterm has exactly one grid and no ownership token, so something has to own
# the one this run opened. The default is to hold it in the foreground; the
# close is in a `finally` so that neither `Ctrl-C` nor an exhausted stdin can
# orphan it.
# ---------------------------------------------------------------------------

class Reader(object):
    """A canned sequence of lines, modelling `sys.stdin.readline` EXACTLY.

    ⚠️ **Lines come back WITH their newline, and exhaustion returns `""`.**
    `readline` never raises `EOFError` -- that is `input()`. `agb-peer-setup`'s
    first fake raised it anyway, so the harness described a world where an
    exhausted stdin was impossible; the real `""` was treated as an ordinary
    answer and every re-prompting loop in that file spun, measured at 305,869
    menu prints in six seconds, while its EOF test passed the whole time.

    ⚠️ **And the read count is BOUNDED**, so a spin fails loudly here instead
    of hanging the suite -- an infinite loop under pytest looks like a stuck
    machine, not like a red test.
    """

    LIMIT = 20

    def __init__(self, *lines):
        self.lines = list(lines)
        self.reads = 0

    def __call__(self):
        self.reads += 1
        if self.reads > self.LIMIT:
            raise AssertionError(
                "read_line called %d times -- this is the agb-peer-setup spin"
                % (self.reads,))
        if not self.lines:
            return ""                      # readline at EOF, not EOFError
        return self.lines.pop(0) + "\n"


class Boom(object):
    """A reader that raises what a real terminal raises: `Ctrl-C`."""

    def __init__(self, error=KeyboardInterrupt):
        self.error = error
        self.reads = 0

    def __call__(self):
        self.reads += 1
        raise self.error()


def test_the_reader_fake_models_readline_not_input(dashboard):
    """The guard on the harness itself. A fake simpler than reality fails
    nothing, and this is the exact simplification that shipped a spin once."""
    reader = Reader("x")
    assert reader() == "x\n", "the newline is the EOF discriminator"
    assert reader() == "", "exhaustion returns the empty string"
    assert reader() == "", "and keeps returning it -- it never raises"


# --- the hold -------------------------------------------------------------

def test_the_hold_closes_the_grid_on_enter(dashboard):
    ctl = GridCtl(rows(("AAAA1111", "alice")))
    reader = Reader("")
    out = Out()
    assert dashboard.main(["alice"], out=out, ctl=ctl, read_line=reader) == 0
    assert ("close",) in ctl.calls
    assert reader.reads == 1
    assert "press enter" in out.text
    # ⚠️ The discriminator is the NEWLINE, and this is the companion that
    # proves it is tested on the RAW value: enter gives "\n", which strips to
    # "" and is NOT end of input. Strip first and the two become
    # indistinguishable -- and the dangerous one becomes the harmless one.
    assert "stdin ended" not in out.text


def test_the_hold_closes_the_grid_on_EOF(dashboard):
    """⚠️ `readline` returns `""` at end of input and does NOT raise. A run
    with no stdin -- a pipe that ran out, a closed stdin, a here-doc shorter
    than the prompts -- must close the grid rather than treat the empty string
    as an answer it can go back and ask for again."""
    ctl = GridCtl(rows(("AAAA1111", "alice")))
    reader = Reader()                       # exhausted from the start
    out = Out()
    assert dashboard.main(["alice"], out=out, ctl=ctl, read_line=reader) == 0
    assert ("close",) in ctl.calls
    assert "stdin ended" in out.text


def test_EOF_is_read_ONCE_and_cannot_spin(dashboard):
    """The anti-spin guard, and the reason `Reader` counts. `agb-peer-setup`
    spun 305,869 times on exactly this input; a bounded fake turns that into a
    named failure instead of a hung suite."""
    ctl = GridCtl(rows(("AAAA1111", "alice")))
    reader = Reader()
    dashboard.main(["alice"], out=Out(), ctl=ctl, read_line=reader)
    assert reader.reads == 1
    assert len([c for c in ctl.calls if c[0] == "close"]) == 1


def test_the_hold_closes_the_grid_on_KeyboardInterrupt(dashboard):
    """⚠️ `Ctrl-C` is the documented way out of a foreground wait and is NOT an
    `Exception`, so the close has to be in a `finally`. An `except` clause
    would miss it and orphan the grid -- the one failure the hold exists to
    prevent."""
    ctl = GridCtl(rows(("AAAA1111", "alice")))
    with pytest.raises(KeyboardInterrupt):
        dashboard.main(["alice"], out=Out(), ctl=ctl, read_line=Boom())
    assert ("close",) in ctl.calls


def test_an_input_shaped_reader_that_raises_EOFError_still_closes(dashboard):
    """The real `readline` cannot get here, but a caller injecting an
    `input()`-shaped reader can -- and a traceback out of the wait would leave
    the grid up just as surely as a missing `finally` would."""
    ctl = GridCtl(rows(("AAAA1111", "alice")))
    out = Out()
    assert dashboard.main(["alice"], out=out, ctl=ctl,
                          read_line=Boom(EOFError)) == 0
    assert ("close",) in ctl.calls
    assert "stdin ended" in out.text


def test_the_hold_says_the_grid_does_NOT_follow_and_names_what_does(dashboard):
    """⚠️ In the tool's own output, not only in the docs. `agb-refresh`
    re-mints every row id, so a refresh under a held grid leaves dead cells --
    and the person who needs to know that is looking at the grid."""
    ctl = GridCtl(rows(("AAAA1111", "alice")))
    out = Out()
    dashboard.main(["alice"], out=out, ctl=ctl, read_line=Reader(""))
    assert "agb-refresh" in out.text
    assert "does NOT follow" in out.text
    assert "agb-peer relay --dashboard" in out.text


def test_the_hold_says_to_run_it_OUTSIDE_agterm(dashboard):
    """That is the condition Task 0's measurement holds under: a terminal
    outside agterm stayed responsive while a grid was up. Whether a shell
    inside one does is untested, so the tool names the measured route."""
    ctl = GridCtl(rows(("AAAA1111", "alice")))
    out = Out()
    dashboard.main(["alice"], out=out, ctl=ctl, read_line=Reader(""))
    assert "OUTSIDE agterm" in out.text


def test_a_close_that_fails_during_the_hold_is_said_and_gives_the_command(
        dashboard):
    """The grid is still up and the hold is over, so nothing else will close
    it. That is the moment the literal command is worth the most."""
    ctl = GridCtl(rows(("AAAA1111", "alice")), close=(False, "no such grid"))
    out = Out()
    assert dashboard.main(["alice"], out=out, ctl=ctl,
                          read_line=Reader("")) == 0
    assert "STILL UP" in out.text
    assert "no such grid" in out.text
    assert "dashboard --close" in out.text


def test_a_close_that_SUCCEEDS_says_none_of_that(dashboard):
    """The companion the "nothing happened" test needs: without it the two
    assertions above would pass against a message that can never be printed."""
    ctl = GridCtl(rows(("AAAA1111", "alice")))
    out = Out()
    dashboard.main(["alice"], out=out, ctl=ctl, read_line=Reader(""))
    assert "STILL UP" not in out.text


def test_the_hold_defaults_to_real_stdin(dashboard, monkeypatch):
    """The wiring, which every other test in this section bypasses by
    injecting a reader. An EOF-at-once stdin is the safe way to prove it: a
    default that was never hooked up would raise a TypeError instead."""
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    ctl = GridCtl(rows(("AAAA1111", "alice")))
    assert dashboard.main(["alice"], out=Out(), ctl=ctl) == 0
    assert ("close",) in ctl.calls


# --- --detach -------------------------------------------------------------

def test_detach_leaves_the_grid_open_and_prints_the_close_command(dashboard):
    """⚠️ The literal command, because after a detached run nothing owns the
    grid: agterm has exactly one and no ownership token, so the user is the
    only thing left that can close it."""
    ctl = GridCtl(rows(("AAAA1111", "alice"), ("BBBB2222", "bob")))
    out = Out()
    assert dashboard.main(["--detach", "alice", "bob"], out=out, ctl=ctl,
                          read_line=no_read_line) == 0
    assert ctl.opened() == [["AAAA1111:left", "BBBB2222:left"]]
    assert ("close",) not in ctl.calls
    assert "agtermctl dashboard --close" in out.text


def test_detach_still_reports_the_resolved_cells(dashboard):
    ctl = GridCtl(rows(("AAAA1111", "alice")))
    out = Out()
    dashboard.main(["--detach", "alice"], out=out, ctl=ctl,
                   read_line=no_read_line)
    assert "AAAA1111" in out.text and "alice" in out.text
    assert "left" in out.text


def test_detach_reads_no_stdin_at_all(dashboard):
    """`no_read_line` raises, so this is an assertion rather than an
    inference: a detached run that waited would hang a terminal for a grid it
    has already handed over."""
    ctl = GridCtl(rows(("AAAA1111", "alice")))
    assert dashboard.main(["--detach", "alice"], out=Out(), ctl=ctl,
                          read_line=no_read_line) == 0


def test_a_detached_run_does_not_promise_to_follow(dashboard):
    """The follow note belongs to the hold: a detached grid is nobody's, and
    telling its owner what `agb-peer relay --dashboard` would have done is
    advice about a run that is already over."""
    ctl = GridCtl(rows(("AAAA1111", "alice")))
    out = Out()
    dashboard.main(["--detach", "alice"], out=out, ctl=ctl,
                   read_line=no_read_line)
    assert "press enter" not in out.text


# --- --mru ----------------------------------------------------------------

def test_mru_hands_agterm_the_flag_and_resolves_NOTHING(dashboard):
    """⚠️ No `tree --json` either: with no selector there is no question to
    ask about the row set, and fetching it anyway would be a subprocess run to
    populate a variable nobody reads."""
    ctl = GridCtl(rows(("AAAA1111", "alice")))
    out = Out()
    assert dashboard.main(["--mru"], out=out, ctl=ctl,
                          read_line=Reader("")) == 0
    assert ctl.opened() == [["--mru"]]
    assert ("tree",) not in ctl.calls


def test_mru_says_that_no_membership_was_asserted(dashboard):
    """Otherwise a two-cell grid from `--mru` reads exactly like a two-cell
    grid from two selectors, and only one of them was checked."""
    ctl = GridCtl()
    out = Out()
    dashboard.main(["--mru"], out=out, ctl=ctl, read_line=Reader(""))
    assert "no membership" in out.text.lower()


def test_mru_does_NOT_apply_the_strict_unresolved_check(dashboard):
    """⚠️ Everywhere else `unresolved:` on stdout is a failure, because the
    user named a membership and agterm fell short of it. `--mru` names nobody,
    so there is no shortfall to detect -- and refusing here would close a grid
    the user can perfectly well use, over a row they never asked for."""
    ctl = GridCtl(said="unresolved: ZZZZ9999\n")
    out = Out()
    assert dashboard.main(["--mru", "--detach"], out=out, ctl=ctl,
                          read_line=no_read_line) == 0
    assert ("close",) not in ctl.calls


def test_the_strict_check_still_fires_for_SELECTORS(dashboard):
    """The companion that keeps the exemption above honest: the same stdout,
    the only difference being whether a membership was asserted."""
    ctl = GridCtl(rows(("AAAA1111", "alice")), said="unresolved: ZZZZ9999\n")
    with pytest.raises(dashboard.DashError):
        dashboard.main(["--detach", "alice"], out=Out(), ctl=ctl,
                       read_line=no_read_line)


def test_mru_holds_in_the_foreground_like_any_other_grid(dashboard):
    """The grid still needs an owner, and `--mru` opened one."""
    ctl = GridCtl()
    reader = Reader("")
    assert dashboard.main(["--mru"], out=Out(), ctl=ctl,
                          read_line=reader) == 0
    assert ("close",) in ctl.calls
    assert reader.reads == 1


def test_mru_that_will_not_open_is_refused_and_nothing_is_closed(dashboard):
    """agterm refuses before opening anything, so there is no grid to close --
    and closing would dismiss whatever somebody else had up."""
    ctl = GridCtl(ok=False, why="no recent sessions")
    with pytest.raises(dashboard.DashError) as err:
        dashboard.main(["--mru"], out=Out(), ctl=ctl, read_line=no_read_line)
    assert "no recent sessions" in str(err.value)
    assert ("close",) not in ctl.calls


# ---------------------------------------------------------------------------
# Nothing printed after the grid goes up may orphan it
#
# 🔴 SHAPE A, the fourth instance in this area: a user-facing write sitting
# OUTSIDE the cleanup guard. `agb-dashboard alice | head` closes stdout the
# moment `head` exits, and every line between `ctl.dashboard(...)` answering ok
# and the hold's `finally` was unguarded -- so a `BrokenPipeError` unwound past
# the close, `__main__` reported it as a handled `OSError`, and the run exited
# with a grid on the only screen agterm has and nothing owning it. Demonstrated
# with a probe before it was fixed:
#
#     BrokenPipeError closed
#     [('tree',), ('dashboard', ['AAAA1111:left'])]      # opened, never closed
#
# `agb-peer` had `_quiet` for the relay's `say`, written for this exact reason,
# and `close_grid`'s docstring spells it out. The newer file did not carry it.
# ---------------------------------------------------------------------------

def test_a_lost_stdout_does_not_orphan_the_grid_the_run_opened(dashboard):
    """The cell report is the first thing written after the grid goes up."""
    ctl = GridCtl(rows(("AAAA1111", "alice")))
    assert dashboard.main(["alice"], out=Pipe(0), ctl=ctl,
                          read_line=Reader("")) == 0
    assert ("close",) in ctl.calls, ctl.calls


def test_hold_closes_the_grid_even_if_its_own_BANNER_cannot_be_printed(
        dashboard):
    """⚠️ A different write and a separate fix: `hold` printed `HOLD_NOTE`
    OUTSIDE its own `try`, so the function whose entire job is the close could
    be aborted before ever reaching it.

    ⚠️ **Driven through `hold` directly, and that is the whole test.** Through
    `main` the caller has already wrapped `out`, so moving this write back
    outside the `try` changes nothing observable and the check reads as a pass
    -- a guard covering the caller's fix instead of this one. `hold`'s contract
    is "the grid is closed whatever happens", and it may not depend on who
    called it."""
    ctl = GridCtl(rows(("AAAA1111", "alice")))
    with pytest.raises(BrokenPipeError):
        dashboard.hold(Pipe(0), ctl, Reader(""))
    assert ("close",) in ctl.calls, ctl.calls


def test_a_lost_stdout_at_the_HOLD_banner_does_not_orphan_it_either(dashboard):
    """The same failure through `main`, where the caller's wrapper is what
    answers -- two mechanisms, deliberately, because they cover different
    entries: one guards writes `hold` does not control (the cell report,
    `--detach`'s line, anything added later), the other guards `hold` itself."""
    ctl = GridCtl(rows(("AAAA1111", "alice")))
    # One successful write -- the cell report -- then the pipe dies on the
    # hold's banner.
    assert dashboard.main(["alice"], out=Pipe(2), ctl=ctl,
                          read_line=Reader("")) == 0
    assert ("close",) in ctl.calls, ctl.calls


def test_a_lost_stdout_does_not_orphan_an_mru_grid(dashboard):
    """`--mru` opens a grid too, and owns it exactly as much."""
    ctl = GridCtl()
    assert dashboard.main(["--mru"], out=Pipe(0), ctl=ctl,
                          read_line=Reader("")) == 0
    assert ("close",) in ctl.calls, ctl.calls


def test_a_lost_stdout_still_prints_what_it_can_BEFORE_the_pipe_dies(
        dashboard):
    """The companion the three above need: a fake that swallowed everything
    would satisfy them while proving the report is never written at all.
    `Pipe(1)` takes exactly one line, so the guard is shown to be about the
    write that RAISES rather than about writing nothing."""
    out = Pipe(1)
    ctl = GridCtl(rows(("AAAA1111", "alice")))
    assert dashboard.main(["alice"], out=out, ctl=ctl,
                          read_line=Reader("")) == 0
    assert "1 cell(s)" in out.text, out.text
    assert ("close",) in ctl.calls


def test_a_write_BEFORE_the_grid_is_up_is_still_fatal(dashboard):
    """⚠️ The guard is applied at the moment the grid goes up, not to the whole
    run, and that boundary is the point: before it there is nothing to orphan,
    and a `--version` or `--help` that cannot be printed has failed. Swallowing
    those would turn a broken stdout into a silent exit 0."""
    ctl = NoCtl()
    with pytest.raises(BrokenPipeError):
        dashboard.main(["--version"], out=Pipe(0), ctl=ctl)


# ---------------------------------------------------------------------------
# The `peer=None` injection seams, which no test and no caller exercised
# ---------------------------------------------------------------------------

def test_the_peer_default_loads_the_sibling_for_itself(dashboard, peer):
    """⚠️ `peer = peer or load_peer()` is in three functions and every test
    passed one in, so all three defaults were unexecuted code. `agb-peer-setup`
    exercises its equivalent; this did not. The fixture has already registered
    the module, so the default resolves to the same object rather than loading
    a second one."""
    ctl = GridCtl(rows(("AAAA1111", "alice")))
    out = Out()
    assert dashboard.main(["--detach", "alice"], out=out, ctl=ctl,
                          read_line=no_read_line) == 0
    assert ctl.opened() == [["AAAA1111:left"]]
    got, problems, folded = dashboard.resolve_selectors(
        rows(("AAAA1111", "alice")), ["alice"])
    assert got == [("alice", "AAAA1111", "left")]
    assert problems == [] and folded == []


def test_the_mru_path_loads_the_sibling_for_itself_too(dashboard):
    """The third seam. `--mru` resolves nothing, so its `peer` is used only for
    `Ctl` -- which a test passing `ctl` covers over, leaving the line
    unexecuted."""
    ctl = GridCtl()
    assert dashboard.main(["--mru", "--detach"], out=Out(), ctl=ctl,
                          read_line=no_read_line) == 0
    assert ctl.opened() == [["--mru"]]
