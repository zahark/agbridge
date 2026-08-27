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


# ---------------------------------------------------------------------------
# transport hint and instance-correct host resolution -- Task 5.
# ---------------------------------------------------------------------------

PANE_ARGV = ["/usr/bin/python3", "-S", "-E", "/opt/agb", "pane", "abcd1234",
             "--host", "box01", "--pane", "%7", "--tmux", "work"]


def test_load_ops_goes_through_agbs_own_door(setup):
    """⚠️ `agb._load_ops()`, not a second loader.

    conftest routes through that door deliberately so a broken door fails
    tests rather than being bypassed; a second copy here would be free to
    drift. Structural, because the alternative is asserting on a module object
    that a reimplementation would produce identically.
    """
    tree = ast.parse(io.open(SETUP_PATH, encoding="utf-8").read())
    fns = [n for n in ast.walk(tree)
           if isinstance(n, ast.FunctionDef) and n.name == "load_ops"]
    assert fns, "load_ops is gone"
    attrs = [n.func.attr for n in ast.walk(fns[0])
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)]
    assert attrs, "load_ops calls nothing"
    assert "_load_ops" in attrs, attrs


def test_load_ops_refuses_a_missing_agb_by_name(setup, tmp_path):
    with pytest.raises(setup.SetupError) as err:
        setup.load_ops(str(tmp_path / "nope"))
    assert err.value.code == 3
    assert "--agb" in str(err.value)


def test_the_default_agb_path_matches_agb_refresh(setup):
    """A cross-file agreement: the bridge loads its modules from the INSTALL,
    not a checkout, so a tool reading the same config must look there too."""
    text = io.open(os.path.join(REPO_ROOT, "agb-refresh"),
                   encoding="utf-8").read()
    assert 'DEFAULT_AGB="$HOME/.local/lib/agbridge/agb"' in text
    assert setup.DEFAULT_AGB.endswith(os.path.join(
        ".local", "lib", "agbridge", "agb"))


def test_agbridge_hint_reads_a_real_pane_argv(setup, ops):
    hint = setup.agbridge_hint(ops, {"foreground": PANE_ARGV})
    assert hint is not None
    assert hint["host"] == "box01"
    assert hint["pane"] == "%7"


def test_agbridge_hint_on_a_bare_shell_is_none(setup, ops):
    assert setup.agbridge_hint(ops, {"foreground": ["/bin/zsh", "-l"]}) is None
    assert setup.agbridge_hint(ops, {}) is None


# ⚠️ Each of these is an argv `agb pane` REFUSES but a naive `--flag value`
# walk reads happily -- measured. That gap is the whole reason this calls the
# real parser: a trailing `--host` with no value would NOT discriminate, since
# both answer None, and a test built on it passes against the imitation.
REFUSED_ARGV = [
    (["key", "--bogus", "x", "--host", "box01"], "unknown option"),
    (["key", "--host", "box01 evil"], "a host containing whitespace"),
    (["key", "extra", "--host", "box01"], "a stray positional"),
    (["key", "--host", "box01", "--pane", "notapane"], "a bad pane id"),
]


@pytest.mark.parametrize("argv,why", REFUSED_ARGV)
def test_agbridge_hint_swallows_a_refusal_rather_than_raising(setup, ops,
                                                              argv, why):
    """⚠️ An argv `agb pane` rejects describes a row whose settings cannot be
    trusted, and the refusal must not traceback out of a menu.

    The premise is asserted first: the parser really does refuse, and a naive
    walk really would return a host. Without that, this passes against an
    imitation -- which is what CLAUDE.md's rule about calling the parser rather
    than simulating it is about.
    """
    with pytest.raises(Exception) as err:
        ops.parse_pane_args(argv)
    assert type(err.value).__name__ == "AgbError", why
    naive = {}
    for i, a in enumerate(argv):
        if a.startswith("--") and i + 1 < len(argv):
            naive[a[2:]] = argv[i + 1]
    assert naive.get("host"), "premise: a walk would read a host from %r" % (why,)
    assert setup.agbridge_hint(ops, {"foreground": ["/opt/agb", "pane"] + argv}) is None


def test_row_target_threads_the_CALLERS_unreadable_list(setup, ops, tmp_path):
    """⚠️ The finding: passing `pane_settings` a fresh `[]` looks identical and
    reports nothing.

    The caller's list stays empty, so the "could not read the config" branch
    downstream never fires and an EACCES config degrades silently to a bare
    hostname -- the wrong-machine bug with the diagnostic removed.
    """
    bad = tmp_path / "config"
    bad.write_text("host_box01 = box01-alias\n")
    os.chmod(str(bad), 0o000)
    unreadable = []
    try:
        hint = {"host": "box01", "jump": None, "config": str(bad)}
        setup.row_target(ops, hint, unreadable)
    finally:
        os.chmod(str(bad), 0o600)
    assert unreadable, "the caller's list was not threaded through"


def test_row_target_resolves_through_the_rows_own_config(setup, ops, tmp_path):
    cfg = tmp_path / "config"
    cfg.write_text("host_box01 = user@box01.example\n")
    hint = {"host": "box01", "jump": None, "config": str(cfg)}
    assert setup.row_target(ops, hint, []) == "user@box01.example"


def test_host_choices_reads_the_rows_own_instance(setup, agb, ops, tmp_path):
    """⚠️ A Mac running two bridges has two `host_<name>` tables. Reading the
    default one would resolve instance B's rows through instance A's."""
    a = tmp_path / "a"
    a.mkdir()
    (a / "config").write_text("host_boxA = a-alias\n")
    b = tmp_path / "b"
    b.mkdir()
    (b / "config").write_text("host_boxB = b-alias\nhost_boxB2 = b2-alias\n")
    hint = {"host": "boxB", "jump": None, "config": str(b / "config")}
    choices = setup.host_choices(agb, ops, hint, [])
    assert choices == [("boxB", "b-alias"), ("boxB2", "b2-alias")]
    assert all(name != "boxA" for name, _t in choices)


def test_host_choices_on_a_row_with_no_config_uses_the_default(setup, agb, ops,
                                                               fake_home):
    """A row minted by a DEFAULT install carries no `--config` -- that is every
    default install, not a legacy case -- and `read_config(None)` is exactly
    the right answer for it."""
    path = agb.config_path()
    if not os.path.isdir(os.path.dirname(path)):
        os.makedirs(os.path.dirname(path))
    io.open(path, "w", encoding="utf-8").write(u"host_boxD = d-alias\n")
    hint = {"host": "boxD", "jump": None}
    assert setup.host_choices(agb, ops, hint, []) == [("boxD", "d-alias")]


def test_host_choices_reports_an_unreadable_config_and_offers_nothing(
        setup, agb, ops, tmp_path):
    """⚠️ Invariant 12: "I could not answer" is not "the answer is nothing".
    Silence here is a bare hostname the user then types by hand."""
    cfg = tmp_path / "config"
    cfg.write_text("host_boxE = e-alias\n")
    os.chmod(str(cfg), 0o000)
    unreadable = []
    try:
        hint = {"host": "boxE", "jump": None, "config": str(cfg)}
        assert setup.host_choices(agb, ops, hint, unreadable) == []
    finally:
        os.chmod(str(cfg), 0o600)
    assert unreadable, "the read failure was swallowed"


def test_host_choices_with_no_hint_offers_nothing_QUIETLY(setup, agb, ops):
    """The companion the test above needs: same empty result, different reason,
    and the `unreadable` list is what tells them apart."""
    unreadable = []
    assert setup.host_choices(agb, ops, None, unreadable) == []
    assert unreadable == []


def test_the_agbridge_default_is_withheld_when_a_mapping_applies(setup, ops):
    """⚠️ The relay does NOT apply `host_<name>`: `scan_participant` hands
    `--host` to ssh verbatim. So on a host needing an alias, `[a]` produces a
    roster that parses, validates, prints a working-looking next command, and
    silently never delivers."""
    hint = {"host": "box01"}
    assert setup.offer_agbridge_default(ops, hint, {}) is True
    assert setup.offer_agbridge_default(
        ops, hint, {"host_box01": "box01.example"}) is False


def test_the_agbridge_default_needs_a_host_at_all(setup, ops):
    assert setup.offer_agbridge_default(ops, None, {}) is False
    assert setup.offer_agbridge_default(ops, {"host": None}, {}) is False


def test_scan_participant_really_does_use_the_host_verbatim(setup, peer):
    """The premise the gate rests on, pinned against `agb-peer` itself rather
    than restated -- if the relay ever learns `ssh_target_for`, this fails and
    the gate can be relaxed."""
    src = io.open(PEER_PATH, encoding="utf-8").read()
    tree = ast.parse(src)
    fns = [n for n in ast.walk(tree)
           if isinstance(n, ast.FunctionDef) and n.name == "scan_participant"]
    assert fns, "scan_participant is gone"
    names = [n.id for n in ast.walk(fns[0]) if isinstance(n, ast.Name)]
    assert "pane_argv_field" in names, names
    assert "ssh_target_for" not in names, names


# ---------------------------------------------------------------------------
# Draft state and loading -- Task 6.
# ---------------------------------------------------------------------------

def roster(tmp_path, text, name="roster"):
    path = tmp_path / name
    path.write_bytes(text if isinstance(text, bytes) else text.encode("utf-8"))
    return str(path)


def test_a_missing_file_opens_an_empty_draft(setup, peer, tmp_path):
    said = []
    draft = setup.load_draft(peer, str(tmp_path / "nope"), said.append)
    assert draft.entries == []
    assert draft.dirty is False
    assert draft.loaded is peer.ROSTER_ABSENT
    assert said and "does not exist yet" in said[0]


def test_a_one_participant_roster_LOADS(setup, peer, tmp_path):
    """⚠️ It is a legal state for a RUNNING relay -- somebody left -- so
    refusing to open one would mean this tool cannot add the second back.
    `minimum=2` here would have died on exactly the file that needs editing."""
    said = []
    draft = setup.load_draft(peer, roster(tmp_path, "solo=RowA\n"), said.append)
    assert draft.names() == ["solo"]
    assert any("cannot START" in m for m in said)


def test_the_load_keeps_the_files_line_order(setup, peer, tmp_path):
    """Order is a contract: `[d]` deletes by position, `[e]` edits in place.

    ⚠️ **This does not distinguish the line-walk from plain dict order**, and
    saying so is more useful than implying it does. On CPython 3.6+ a dict
    preserves insertion order, which here is file order -- measured: swapping
    `parse_ordered` for a dict comprehension leaves the suite green. What this
    pins is the OBSERVABLE property downstream code relies on, which is worth
    a test whichever implementation provides it.
    """
    path = roster(tmp_path, "carol=R3\nalice=R1\nbob=R2\n")
    draft = setup.load_draft(peer, path, lambda _m: None)
    assert draft.names() == ["carol", "alice", "bob"]


def test_comments_and_blanks_do_not_disturb_the_order(setup, peer, tmp_path):
    path = roster(tmp_path, "# who\n\ncarol=R3\n  # note\nalice=R1\n")
    draft = setup.load_draft(peer, path, lambda _m: None)
    assert draft.names() == ["carol", "alice"]


@pytest.mark.parametrize("content,why", [
    (b"", "empty"),
    (b"this is not a roster\n", "malformed"),
    (b"alice=\xff\xfe\n", "not UTF-8"),
])
def test_a_broken_roster_opens_for_REPAIR(setup, peer, tmp_path, content, why):
    """These are precisely the files somebody needs an editor for. `loaded` is
    still set from the raw bytes, so a later write still gates against what was
    really there -- only the parsed entries are dropped."""
    said = []
    path = roster(tmp_path, content, name="broken-" + why.replace(" ", "-"))
    draft = setup.load_draft(peer, path, said.append)
    assert draft.entries == []
    assert draft.loaded == content, "the gate must still hold the real bytes"
    assert said and "could not be read as a roster" in said[0]


def test_an_UNREADABLE_roster_is_not_a_repair_path(setup, peer, tmp_path):
    """⚠️ The asymmetry is the point.

    Opening it would mean a later write gates against nothing and renames over
    a file nobody could read -- the vacuous gate this whole design avoids.
    Empty and malformed are repairable because their bytes are known; this
    one's are not.
    """
    path = roster(tmp_path, "alice=RowA\n", name="unreadable")
    os.chmod(path, 0o000)
    try:
        with pytest.raises(peer.PeerError):
            setup.load_draft(peer, path, lambda _m: None)
    finally:
        os.chmod(path, 0o600)


def test_the_absent_answer_is_distinguishable_from_an_empty_file(setup, peer,
                                                                 tmp_path):
    """`ROSTER_ABSENT` and `b""` are different states, and a write gates on
    the difference: one file is not there, the other is there and empty."""
    missing = setup.load_draft(peer, str(tmp_path / "nope"), lambda _m: None)
    empty = setup.load_draft(peer, roster(tmp_path, b"", name="e"),
                             lambda _m: None)
    assert missing.loaded is peer.ROSTER_ABSENT
    assert empty.loaded == b""
    assert empty.loaded is not peer.ROSTER_ABSENT


def test_a_loaded_draft_round_trips_through_render(setup, peer, tmp_path):
    """The join between Task 1's renderer and this loader, which is where an
    order or shape mismatch would show up."""
    text = "carol=R3:right@box3\nalice=R1\nbob=R2@local:work\n"
    draft = setup.load_draft(peer, roster(tmp_path, text), lambda _m: None)
    lines = peer.render_roster_lines(draft.entries)
    assert lines == ["carol=R3:right@box3", "alice=R1", "bob=R2@local:work"]
    assert peer.parse_roster_text("\n".join(lines).encode(), minimum=1) == \
        dict(draft.entries)


def test_index_of_finds_and_misses(setup, peer, tmp_path):
    draft = setup.load_draft(peer, roster(tmp_path, "a=R1\nb=R2\n"),
                             lambda _m: None)
    assert draft.index_of("b") == 1
    assert draft.index_of("nope") == -1


# ---------------------------------------------------------------------------
# the add flow -- Task 7.
# ---------------------------------------------------------------------------

class Script(object):
    """A canned sequence of answers, plus everything that was said to us."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.said = []

    def read_line(self):
        if not self.answers:
            raise EOFError()
        return self.answers.pop(0)

    def say(self, message):
        self.said.append(message)

    @property
    def text(self):
        return "\n".join(self.said)


def draft_of(setup, entries=(), path="/tmp/roster"):
    return setup.Draft(path, list(entries), None)


AGBRIDGE_ROW = {"id": "AAAA1111", "name": "work · box01 · /w · %7 · 3s",
                "cwd": "/w", "status": "active", "foreground": PANE_ARGV}
PLAIN_ROW = {"id": "BBBB2222", "name": "shell · mac · /home · %0 · 1s",
             "cwd": "/home", "status": "completed",
             "foreground": ["/bin/zsh", "-l"]}


def test_add_via_the_picker_writes_the_expected_line(setup, peer, ops):
    """`[a]` -- the relay reads the row's own host -- so no `@` is written."""
    ctl = FakeCtl([AGBRIDGE_ROW])
    s = Script("1", "alice", "", "a")
    draft = draft_of(setup)
    assert setup.cmd_add(peer, draft, ctl, s.read_line, s.say, ops=ops) is True
    assert peer.render_roster_lines(draft.entries) == ["alice=work"]
    assert draft.dirty is True


def test_add_with_an_explicit_ssh_target(setup, peer, ops):
    ctl = FakeCtl([AGBRIDGE_ROW])
    s = Script("1", "bob", "right", "s", "poolnode07")
    draft = draft_of(setup)
    setup.cmd_add(peer, draft, ctl, s.read_line, s.say, ops=ops)
    assert peer.render_roster_lines(draft.entries) == ["bob=work:right@poolnode07"]


def test_add_a_mac_side_participant(setup, peer, ops):
    """⚠️ A Mac-side agent NEEDS the tmux target: an agterm row that is not an
    agbridge row has no `--pane` argv to read one from."""
    ctl = FakeCtl([PLAIN_ROW])
    s = Script("1", "mac", "", "l", "work")
    draft = draft_of(setup)
    setup.cmd_add(peer, draft, ctl, s.read_line, s.say, ops=ops)
    assert peer.render_roster_lines(draft.entries) == ["mac=shell@local:work"]


def test_add_with_an_explicit_tmux_target_over_ssh(setup, peer, ops):
    ctl = FakeCtl([AGBRIDGE_ROW])
    s = Script("1", "far", "scratch", "t", "box3", "%24")
    draft = draft_of(setup)
    setup.cmd_add(peer, draft, ctl, s.read_line, s.say, ops=ops)
    assert peer.render_roster_lines(draft.entries) == ["far=work:scratch@box3:%24"]


def test_a_duplicate_name_is_refused_AGAINST_THE_WHOLE_DRAFT(setup, peer, ops):
    """⚠️ The regression that matters.

    `parse_participants` builds a fresh `people = {}` per call, so its "named
    twice" refusal only sees the words handed to THAT call -- measured, two
    single-word calls both accept `alice`. Validating one word checks the
    alphabet and the reserved name and silently checks NOTHING for duplicates.
    """
    assert peer.parse_participants(["alice=r1"], minimum=1)      # premise:
    assert peer.parse_participants(["alice=r2"], minimum=1)      # both accepted
    ctl = FakeCtl([AGBRIDGE_ROW])
    s = Script("1", "alice", "bob", "", "a")
    draft = draft_of(setup, [("alice", ("other", "left", None, None))])
    setup.cmd_add(peer, draft, ctl, s.read_line, s.say, ops=ops)
    assert draft.names() == ["alice", "bob"]
    assert "named twice" in s.text


def test_the_reserved_name_relay_is_refused_at_the_prompt(setup, peer, ops):
    ctl = FakeCtl([AGBRIDGE_ROW])
    s = Script("1", "relay", "alice", "", "a")
    draft = draft_of(setup)
    setup.cmd_add(peer, draft, ctl, s.read_line, s.say, ops=ops)
    assert draft.names() == ["alice"]
    assert "reserved" in s.text


def test_a_bad_alphabet_is_refused_at_the_prompt(setup, peer, ops):
    ctl = FakeCtl([AGBRIDGE_ROW])
    s = Script("1", "not a name", "alice", "", "a")
    draft = draft_of(setup)
    setup.cmd_add(peer, draft, ctl, s.read_line, s.say, ops=ops)
    assert draft.names() == ["alice"]


@pytest.mark.parametrize("pane", ["left", "right", "scratch"])
def test_every_pane_kind_is_reachable(setup, peer, ops, pane):
    ctl = FakeCtl([AGBRIDGE_ROW])
    s = Script("1", "p", pane, "a")
    draft = draft_of(setup)
    setup.cmd_add(peer, draft, ctl, s.read_line, s.say, ops=ops)
    line = peer.render_roster_lines(draft.entries)[0]
    assert (":" + pane in line) == (pane != "left"), line


def test_a_bad_pane_kind_is_refused(setup, peer, ops):
    ctl = FakeCtl([AGBRIDGE_ROW])
    s = Script("1", "p", "middle", "left", "a")
    draft = draft_of(setup)
    setup.cmd_add(peer, draft, ctl, s.read_line, s.say, ops=ops)
    assert draft.entries
    assert "pane must be one of" in s.text


def test_a_row_whose_label_has_a_space_is_refused_with_the_reason(setup, peer,
                                                                  ops):
    spaced = dict(AGBRIDGE_ROW, id="CCCC3333", name="my row · box01 · %7")
    ctl = FakeCtl([spaced])
    s = Script("1", "2", "typed", "alice", "", "a")
    draft = draft_of(setup)
    setup.cmd_add(peer, draft, ctl, s.read_line, s.say, ops=ops)
    assert "whitespace" in s.text


def test_the_agbridge_default_is_withheld_and_ssh_preselected(setup, peer, ops,
                                                              tmp_path):
    """⚠️ The `[a]` gate, end to end. The relay hands `--host` to ssh verbatim,
    so on a host with an alias `[a]` would write a roster that parses,
    validates, prints a working next command and never delivers."""
    cfg = tmp_path / "config"
    cfg.write_text("host_box01 = box01.example\n")
    argv = list(PANE_ARGV) + ["--config", str(cfg)]
    row = dict(AGBRIDGE_ROW, foreground=argv)
    ctl = FakeCtl([row])
    s = Script("1", "alice", "", "s", "1")
    draft = draft_of(setup)
    setup.cmd_add(peer, draft, ctl, s.read_line, s.say, ops=ops,
                  agb=sys.modules["agb"])
    assert "[a]" not in s.text, "the row default must not be offered"
    assert "uses the row's host VERBATIM" in s.text
    assert peer.render_roster_lines(draft.entries) == ["alice=work@box01.example"]


def test_the_host_list_offers_both_sides(setup, peer, ops, tmp_path):
    cfg = tmp_path / "config"
    cfg.write_text("host_box01 = user@box01.example\n")
    argv = list(PANE_ARGV) + ["--config", str(cfg)]
    ctl = FakeCtl([dict(AGBRIDGE_ROW, foreground=argv)])
    s = Script("1", "alice", "", "s", "1")
    setup.cmd_add(peer, draft_of(setup), ctl, s.read_line, s.say, ops=ops,
                  agb=sys.modules["agb"])
    assert "box01" in s.text and "user@box01.example" in s.text


def test_an_unreadable_config_warns_and_still_lets_you_type(setup, peer, ops,
                                                            tmp_path):
    cfg = tmp_path / "config"
    cfg.write_text("host_box01 = x\n")
    os.chmod(str(cfg), 0o000)
    argv = list(PANE_ARGV) + ["--config", str(cfg)]
    ctl = FakeCtl([dict(AGBRIDGE_ROW, foreground=argv)])
    s = Script("1", "alice", "", "s", "typed-target")
    draft = draft_of(setup)
    try:
        setup.cmd_add(peer, draft, ctl, s.read_line, s.say, ops=ops,
                      agb=sys.modules["agb"])
    finally:
        os.chmod(str(cfg), 0o600)
    assert "could not be read" in s.text
    assert peer.render_roster_lines(draft.entries) == ["alice=work@typed-target"]


def test_discovery_failure_falls_back_to_typing(setup, peer, ops):
    """The tool has to stay usable with no agterm at all."""
    ctl = FakeCtl(error=peer.PeerError("tree failed"))
    s = Script("hand-typed", "alice", "", "s", "box9")
    draft = draft_of(setup)
    assert setup.cmd_add(peer, draft, ctl, s.read_line, s.say, ops=ops) is True
    assert peer.render_roster_lines(draft.entries) == ["alice=hand-typed@box9"]
    assert "not checked against a live row" in s.text


def test_a_typed_label_matching_two_rows_is_refused(setup, peer, ops):
    ctl = FakeCtl([AGBRIDGE_ROW, dict(AGBRIDGE_ROW, id="DDDD4444",
                                      name="work-two · box02 · %8")])
    s = Script("3", "work", "work-two", "alice", "", "a")
    draft = draft_of(setup)
    setup.cmd_add(peer, draft, ctl, s.read_line, s.say, ops=ops)
    assert "matches 2 rows" in s.text
    assert peer.render_roster_lines(draft.entries) == ["alice=work-two"]


def test_cancelling_at_any_prompt_adds_nothing(setup, peer, ops):
    for answers in (["q"], ["1", "q"], ["1", "alice", "q"],
                    ["1", "alice", "", "q"]):
        ctl = FakeCtl([AGBRIDGE_ROW])
        s = Script(*answers)
        draft = draft_of(setup)
        assert setup.cmd_add(peer, draft, ctl, s.read_line, s.say,
                             ops=ops) is False
        assert draft.entries == []
        assert draft.dirty is False


def test_eof_is_a_cancel_not_a_crash(setup, peer, ops):
    ctl = FakeCtl([AGBRIDGE_ROW])
    s = Script()
    draft = draft_of(setup)
    assert setup.cmd_add(peer, draft, ctl, s.read_line, s.say, ops=ops) is False
    assert draft.entries == []


def test_picking_a_row_whose_label_resolves_ELSEWHERE_is_refused(setup, peer,
                                                                 ops):
    """⚠️ The picker's own uniqueness check, which no other add test reaches.

    Every other flow here picks a label that happens to be unique, so removing
    the check changes nothing and the mutation reads as a pass. Here the picked
    row's label prefixes a *different* row's id, so `match_sessions` wins on the
    id tier: exactly one match, on a row the user did not pick. Without the
    check this silently writes a roster entry pointing at the decoy.
    """
    picked = {"id": "AAAA1111", "name": "work · box01 · %7", "cwd": "/w",
              "status": "active", "foreground": PANE_ARGV}
    decoy = {"id": "work99", "name": "something else · box02 · %8",
             "cwd": "/x", "status": "active", "foreground": PANE_ARGV}
    sessions = [picked, decoy]
    matches = peer.match_sessions(sessions, "work")
    assert len(matches) == 1 and matches[0] is decoy, "premise: id tier wins"

    ctl = FakeCtl(sessions)
    s = Script("1", "3", "something", "alice", "", "a")
    draft = draft_of(setup)
    setup.cmd_add(peer, draft, ctl, s.read_line, s.say, ops=ops)
    assert "not to the row you picked" in s.text
    assert peer.render_roster_lines(draft.entries) == ["alice=something"]
