"""agb-dashboard -- open agterm's grid by NAME rather than by row id.

Task 3 is the skeleton: sibling loading, shared module identity, and the
parser. Resolution (Task 4) and the lifecycle (Task 5) are stubs that refuse,
so every test here drives `main` with fakes that would RAISE if a subprocess
were run -- which is how the refusal tests prove nothing was opened rather than
merely that nothing was printed.

Plan: docs/plans/20260827-agb-dashboard.md
"""

import ast
import importlib.util
import io
import os
import sys

import pytest
from importlib.machinery import SourceFileLoader

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DASH_PATH = os.path.join(REPO_ROOT, "agb-dashboard")
PEER_PATH = os.path.join(REPO_ROOT, "agb-peer")


class Out(object):
    def __init__(self):
        self.text = ""

    def write(self, s):
        self.text += s


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


def test_version_is_answered_without_loading_the_sibling(dashboard):
    """A version query is one of the things asked in order to find out whether
    an install is sound, so it must answer in a tree where `agb-peer` is
    missing rather than dying on the load."""
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


def test_an_accepted_argv_reaches_a_stub_rather_than_a_refusal(dashboard):
    """The non-vacuity companion to the refusals above: without it every test
    in this file would pass against a `main` that refused EVERYTHING.

    ⚠️ Task 4 replaced `run_grid`, so the selector and roster argvs no longer
    stop here -- they are covered by the whole `run_grid` section below, which
    asserts on the argv handed to `agtermctl`. `--mru` is Task 5's, and the
    exit code 70 is what still says "accepted, not yet built"."""
    with pytest.raises(dashboard.DashError) as err:
        dashboard.main(["--mru"], out=Out(), ctl=NoCtl(),
                       read_line=no_read_line)
    assert err.value.code == 70
    assert "not implemented yet" in str(err.value)


# ---------------------------------------------------------------------------
# the __main__ guard
# ---------------------------------------------------------------------------

def test_the_guard_names_every_class_that_can_reach_it():
    """⚠️ Matched BY CLASS NAME, not in an `except` clause, because naming them
    there would require importing the sibling -- and a usage error is raised
    with no module in hand.

    Structural because the alternative is provoking each one for real.
    """
    tree = ast.parse(io.open(DASH_PATH, encoding="utf-8").read())

    def literal(node):
        # ast.Str on 3.6-3.7, ast.Constant from 3.8. The floor is 3.6.8 and CI
        # may be newer, so both spellings have to work.
        if isinstance(node, ast.Str):
            return node.s
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        return None

    names = set()
    for node in ast.walk(tree):
        # `type(error).__name__ in (...)` -- the left side is an Attribute over
        # a Call, not a Call, which is what the same guard in
        # test_agb_peer_setup.py got wrong first: it matched nothing and
        # asserted over an empty set.
        if not isinstance(node, ast.Compare):
            continue
        for cmp_node in node.comparators:
            if isinstance(cmp_node, ast.Tuple):
                names.update(v for v in (literal(e) for e in cmp_node.elts)
                             if v is not None)
    assert names, "no class-name tuple found -- the walk is broken"
    for cls in ("PeerError", "RosterConflict", "AgbError", "OSError", "IOError"):
        assert cls in names, "%s can reach __main__ and is not handled" % (cls,)


def test_the_guard_handles_KeyboardInterrupt_in_a_clause_of_its_own():
    """⚠️ `KeyboardInterrupt` is not an `Exception`, so the class-name match
    above cannot see it -- and `Ctrl-C` is the documented way to end the
    foreground hold Task 5 adds, i.e. an exit rather than a crash."""
    tree = ast.parse(io.open(DASH_PATH, encoding="utf-8").read())
    handled = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler) and isinstance(node.type,
                                                              ast.Name):
            handled.add(node.type.id)
    assert handled, "no except handler found -- the walk is broken"
    assert "KeyboardInterrupt" in handled
    assert "DashError" in handled


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
    return dashboard.main(argv, out=out or Out(), ctl=ctl,
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
    got, problems = dashboard.resolve_selectors(
        rows(("AAAA1111", "alice")), ["alice"], peer)
    assert problems == []
    assert got == [("AAAA1111", "left")]


def test_unresolved_and_ambiguous_are_told_apart(dashboard, peer):
    """⚠️ Classified on `len(match_sessions(...))`, not by calling `resolve`:
    that raises one `PeerError` code 2 for unresolved, ambiguous and
    no-sessions-at-all alike, so telling them apart through it would mean
    string-matching an error message."""
    sessions = rows(("AAAA1111", "api"), ("BBBB2222", "api-refactor"))
    got, problems = dashboard.resolve_selectors(
        sessions, ["api", "nobody"], peer)
    assert got == []
    assert len(problems) == 2
    assert "matches 2 rows" in problems[0] and "api-refactor" in problems[0]
    assert "no row matches" in problems[1] and "nobody" in problems[1]


def test_two_selectors_naming_one_cell_are_DEDUPED_not_refused(dashboard, peer):
    """Two ways of naming one row is not a user error; spending two of the nine
    cells on it is the bug. First-seen order is kept."""
    sessions = rows(("AAAA1111", "alice"), ("BBBB2222", "bob"))
    got, problems = dashboard.resolve_selectors(
        sessions, ["alice", "bob", "AAAA1111", "ALIC"], peer)
    assert problems == []
    assert got == [("AAAA1111", "left"), ("BBBB2222", "left")]


def test_the_dedupe_key_is_id_AND_pane_not_the_id_alone(dashboard, peer):
    """⚠️ `X:left` and `X:right` are two legitimate, distinct cells, and a
    roster may hold `alice=<label>` beside `split=<same label>:right`. Deduping
    by id would silently drop one -- the same missing-cell class this command
    exists to remove."""
    sessions = rows(("AAAA1111", "alice"))
    got, problems = dashboard.resolve_selectors(
        sessions, [("alice", "left"), ("alice", "right"), ("alice", "left")],
        peer)
    assert problems == []
    assert got == [("AAAA1111", "left"), ("AAAA1111", "right")]


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


# --- opening, and the strict check on stdout ------------------------------

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
    to say so rather than read as a tidy refusal."""
    ctl = GridCtl(rows(("AAAA1111", "alice")), said="unresolved: ZZZZ\n",
                  close=(False, "no such dashboard"))
    with pytest.raises(dashboard.DashError) as err:
        grid(dashboard, ["alice"], ctl)
    assert "STILL UP" in str(err.value)
    assert "no such dashboard" in str(err.value)


def test_a_clean_open_is_not_read_as_unresolved(dashboard):
    """The companion to the two above: a test that something did not happen
    needs one that differs only in the variable under test, or it passes
    against a check that can never fire."""
    ctl = GridCtl(rows(("AAAA1111", "alice")), said="opened 1 cell\n")
    assert grid(dashboard, ["alice"], ctl) == 0
    assert ("close",) not in ctl.calls
