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


@pytest.mark.parametrize("argv", [["alice", "bob"], ["--roster", "/tmp/r"],
                                  ["--mru"]])
def test_an_accepted_argv_reaches_a_stub_rather_than_a_refusal(dashboard, argv):
    """The non-vacuity companion to the refusals above: without it every test
    in this file would pass against a `main` that refused EVERYTHING. Tasks 4
    and 5 replace the stubs; the exit code 70 is what says "accepted, not yet
    built"."""
    with pytest.raises(dashboard.DashError) as err:
        dashboard.main(argv, out=Out(), ctl=NoCtl(), read_line=no_read_line)
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
