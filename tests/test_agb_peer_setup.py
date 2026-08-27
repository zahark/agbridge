"""agb-peer-setup -- the interactive roster builder.

The flows drive injected `read_line` and `Ctl` fakes rather than a terminal or
a real `agtermctl`, so a test can assert what was NOT prompted and what was NOT
written -- which is the shape most of these are.

Plan: docs/plans/20260826-agb-peer-setup-roster-builder.md
"""

import ast
import importlib.util
import io
import os
import sys

import pytest
from importlib.machinery import SourceFileLoader

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SETUP_PATH = os.path.join(REPO_ROOT, "agb-peer-setup")
PEER_PATH = os.path.join(REPO_ROOT, "agb-peer")


class Out(object):
    def __init__(self):
        self.text = ""

    def write(self, s):
        self.text += s


# ---------------------------------------------------------------------------
# loading -- one module object, shared with agb-peer's own tests
# ---------------------------------------------------------------------------

def test_the_two_modules_share_one_PeerError(setup, peer):
    """⚠️ The named identity guard, and the reason the fixture moved.

    `load_peer` returns `sys.modules[PEER_MODULE]` when it is there. If the
    conftest fixture did not register it -- as the old copy in
    `test_agb_peer.py` did not -- this builds a second module object whose
    `PeerError` is a different class, so `except peer.PeerError` around a call
    into this script silently does not catch.
    """
    assert setup.load_peer() is peer
    assert setup.load_peer().PeerError is peer.PeerError
    assert setup.load_peer().RosterConflict is peer.RosterConflict


def test_the_sys_modules_key_agrees_with_conftest(setup):
    """A cross-file agreement with no single source of truth (invariant 14):
    the fixture and the script must spell the key identically."""
    from conftest import PEER_MODULE
    assert setup.PEER_MODULE == PEER_MODULE


def test_load_peer_resolves_what_the_flows_need(setup):
    """Non-vacuity first: assert the module actually loaded before asserting
    what is on it."""
    mod = setup.load_peer()
    assert mod is not None
    for name in ("parse_roster_text", "parse_participants", "match_sessions",
                 "render_roster_lines", "roster_bytes", "write_roster_file",
                 "write_draft_file", "RosterConflict", "Ctl", "PANE_KINDS"):
        assert hasattr(mod, name), name


def test_peer_path_resolves_through_a_symlink(setup, tmp_path):
    """⚠️ The documented install is a SYMLINK onto `$PATH` beside `agb-peer`.

    `dirname(__file__)` on an unresolved path looks in the symlink's directory
    and finds nothing. A test run from the checkout never notices, because
    there the two coincide -- which is exactly why this test builds the
    symlink case explicitly.
    """
    bindir = tmp_path / "bin"
    bindir.mkdir()
    link = str(bindir / "agb-peer-setup")
    os.symlink(SETUP_PATH, link)
    loader = SourceFileLoader("agb_peer_setup_linked", link)
    spec = importlib.util.spec_from_file_location("agb_peer_setup_linked",
                                                  link, loader=loader)
    linked = importlib.util.module_from_spec(spec)
    loader.exec_module(linked)
    assert linked.peer_path() == PEER_PATH
    assert os.path.exists(linked.peer_path())


def test_a_failed_load_leaves_nothing_registered(setup, tmp_path):
    """Without the `del`, the next call returns a half-initialised module as
    if it had loaded, so the second failure is a baffling AttributeError."""
    broken = tmp_path / "broken-peer"
    broken.write_text("this is not python(\n")
    key = setup.PEER_MODULE
    saved = sys.modules.pop(key, None)
    try:
        with pytest.raises(SyntaxError):
            setup.load_peer(str(broken))
        assert key not in sys.modules
    finally:
        if saved is not None:
            sys.modules[key] = saved


# ---------------------------------------------------------------------------
# dispatch
# ---------------------------------------------------------------------------

def test_no_argument_is_a_usage_error_that_writes_nothing(setup, tmp_path):
    before = sorted(os.listdir(str(tmp_path)))
    out = Out()
    with pytest.raises(setup.SetupError) as err:
        setup.main([], out=out)
    assert err.value.code == 1
    assert "usage" in str(err.value)
    assert sorted(os.listdir(str(tmp_path))) == before
    assert out.text == ""


def test_version_is_answered_without_loading_the_sibling(setup):
    out = Out()
    assert setup.main(["--version"], out=out) == 0
    assert out.text.startswith("agb-peer-setup ")


def test_validate_takes_exactly_one_file(setup):
    with pytest.raises(setup.SetupError):
        setup.main(["validate"], out=Out())
    with pytest.raises(setup.SetupError):
        setup.main(["validate", "a", "b"], out=Out())


def test_an_unknown_flag_is_a_usage_error(setup):
    with pytest.raises(setup.SetupError) as err:
        setup.main(["--nope"], out=Out())
    assert "usage" in str(err.value)


def test_the_guard_names_every_class_that_can_reach_it(setup):
    """⚠️ Four classes escape into this script, not one, and each was a real
    escape: PeerError/RosterConflict from the sibling, AgbError from
    `agb_ops.parse_pane_args` on a row's arbitrary argv, and OSError/IOError
    from `agb.read_config`, which re-raises every errno but ENOENT/ENOTDIR.

    Structural because the alternative is provoking each one through a menu.
    """
    tree = ast.parse(io.open(SETUP_PATH, encoding="utf-8").read())

    def literal(node):
        # ast.Str on 3.6-3.7, ast.Constant from 3.8. This project's floor is
        # 3.6.8 and CI may be newer, so both spellings have to work.
        if isinstance(node, ast.Str):
            return node.s
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        return None

    names = set()
    for node in ast.walk(tree):
        # `type(error).__name__ in (...)` -- the left side is an Attribute
        # over a Call, not a Call, which is what the first version of this
        # guard got wrong: it matched nothing and asserted over an empty set.
        if not isinstance(node, ast.Compare):
            continue
        for cmp_node in node.comparators:
            if isinstance(cmp_node, ast.Tuple):
                names.update(v for v in (literal(e) for e in cmp_node.elts)
                             if v is not None)
    assert names, "no class-name tuple found -- the walk is broken"
    for cls in ("PeerError", "RosterConflict", "AgbError", "OSError", "IOError"):
        assert cls in names, "%s can reach __main__ and is not handled" % (cls,)


# ---------------------------------------------------------------------------
# row_value / row_is_unique / discovery -- Task 4. This is the feature: what
# text from a picked row becomes the roster's `<row>` field.
# ---------------------------------------------------------------------------

def session(sid="AAAA1111", name="work · box01 · /home/me · %7 · 3s",
            cwd="/home/me", status="active"):
    return {"id": sid, "name": name, "cwd": cwd, "status": status}


def test_row_value_takes_the_label_from_a_default_title(setup):
    label, reason = setup.row_value(session())
    assert (label, reason) == ("work", None)


@pytest.mark.parametrize("prefix", ["[?] ", "[done] "])
def test_row_value_strips_the_stale_and_done_prefixes(setup, prefix):
    """⚠️ Without this, EVERY stale or finished row is unpickable.

    `agb_mac.row_title` is `prefix + TITLE_SEP.join(...)`, and both prefixes
    end in a space -- so the first component is "[?] work", which the
    whitespace rule then refuses for a reason that reads like a bug. A fixture
    whose title has every field populated and no prefix hides this completely,
    which is why it is parametrized rather than assumed.
    """
    label, reason = setup.row_value(session(name=prefix + "work · box01 · %7"))
    assert (label, reason) == ("work", None)


def test_row_value_refuses_a_label_with_whitespace(setup):
    """`parse_roster_text` splits on whitespace, so such a label cannot be a
    spec at all -- measured: it parses as two words and errors on the second."""
    label, reason = setup.row_value(session(name="my row · box01 · %7"))
    assert label is None
    assert "whitespace" in reason


@pytest.mark.parametrize("bad", ["a@b", "a:b"])
def test_row_value_refuses_grammar_characters(setup, bad):
    label, reason = setup.row_value(session(name=bad + " · box01"))
    assert label is None
    assert repr(bad) in reason


def test_row_value_refuses_an_empty_label(setup):
    label, reason = setup.row_value(session(name=""))
    assert label is None and reason


def test_row_value_never_returns_the_row_id(setup):
    """The id works today and breaks at the next `agb-refresh`, which re-mints
    every row. `docs/commands.md` says to name a label substring."""
    for name in ("work · box01 · %7", "[?] work · box01", "", "my row · x"):
        sess = session(sid="DEADBEEF", name=name)
        label, _reason = setup.row_value(sess)
        assert label != "DEADBEEF"
        assert label is None or label in name


def test_row_is_unique_accepts_the_picked_row(setup, peer):
    picked = session(sid="AAAA1111", name="work · box01")
    others = [picked, session(sid="BBBB2222", name="other · box02")]
    ok, reason = setup.row_is_unique(peer, others, "work", picked)
    assert (ok, reason) == (True, None)


def test_row_is_unique_refuses_a_label_matching_two_rows(setup, peer):
    picked = session(sid="AAAA1111", name="work · box01")
    sessions = [picked, session(sid="BBBB2222", name="work-two · box02")]
    ok, reason = setup.row_is_unique(peer, sessions, "work", picked)
    assert ok is False
    assert "2 rows" in reason


def test_row_is_unique_refuses_a_UNIQUE_match_on_the_WRONG_row(setup, peer):
    """⚠️ The finding that `len(matches) == 1` is not "the right row".

    `match_sessions` returns the first NON-EMPTY tier -- exact id, then id
    prefix, then name substring. A label that prefixes another row's id wins on
    the id tier and never reaches the name tier, so the check sees exactly one
    match and it is a row the user did not pick.
    """
    picked = session(sid="AAAA1111", name="work · box01")
    decoy = session(sid="work99", name="something else · box02")
    sessions = [picked, decoy]
    matches = peer.match_sessions(sessions, "work")
    assert len(matches) == 1 and matches[0] is decoy, "premise: id tier wins"
    ok, reason = setup.row_is_unique(peer, sessions, "work", picked)
    assert ok is False
    assert "not to the row you picked" in reason


def test_row_is_unique_refuses_a_label_matching_nothing(setup, peer):
    picked = session()
    ok, reason = setup.row_is_unique(peer, [picked], "nosuch", picked)
    assert ok is False and "no row" in reason


def test_format_candidates_is_pure_and_numbers_nothing(setup):
    sessions = [session(sid="AAAA1111", name="work · box01"),
                session(sid="BBBB2222", name="other · box02")]
    rows = setup.format_candidates(sessions)
    assert len(rows) == 2, "non-empty before asserting content"
    for (display, sess) in rows:
        assert sess in sessions
        assert not display.startswith("1")
    assert "work" in rows[0][0] and "AAAA1111" in rows[0][0]


def test_format_candidates_still_shows_an_unusable_row(setup):
    """It has to be visible to be reported on: refusing to LIST it would look
    like the row does not exist."""
    rows = setup.format_candidates([session(name="my row · box01")])
    assert "my row" in rows[0][0]


class FakeCtl(object):
    def __init__(self, sessions=None, error=None):
        self.sessions, self.error, self.calls = sessions or [], error, 0

    def tree(self):
        self.calls += 1
        if self.error:
            raise self.error
        return {"result": {"tree": {"workspaces": [{"sessions": self.sessions}]}}}


def test_discover_rows_returns_the_live_rows(setup, peer):
    ctl = FakeCtl([session()])
    said = []
    assert len(setup.discover_rows(peer, ctl, said.append)) == 1
    assert said == []


def test_discovery_failure_reports_and_does_not_raise(setup, peer):
    """A discovery failure must never abort the editor: typing a label by hand
    is the fallback the whole tool degrades to."""
    ctl = FakeCtl(error=peer.PeerError("tree failed: no agterm"))
    said = []
    assert setup.discover_rows(peer, ctl, said.append) == []
    assert said and "type a row label" in said[0]


def test_discovery_is_fresh_on_every_call(setup, peer):
    """⚠️ Rows are exactly what changes while somebody sets this up. A cache
    held for the session would offer a row that has since been re-minted."""
    ctl = FakeCtl([session()])
    setup.discover_rows(peer, ctl, lambda _m: None)
    setup.discover_rows(peer, ctl, lambda _m: None)
    assert ctl.calls == 2


def test_the_title_separator_agrees_with_agb_mac(setup, mac):
    """A cross-file agreement (invariant 14): spelled here because `agb_mac` is
    a sibling of `agb`, not of this script. A disagreement shows up as every
    row being refused for containing whitespace."""
    assert setup.TITLE_SEP == mac.TITLE_SEP


def test_the_title_prefixes_agree_with_agb_mac(setup, mac):
    assert set(setup.TITLE_PREFIXES) == {mac.TITLE_STALE, mac.TITLE_DONE}
