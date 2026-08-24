"""agb-peer -- the Mac-side peer delivery script.

The flow tests drive a fake `Ctl` rather than the `agtermctl` stub, because
what is worth pinning here is the ORDER of the gates and what happens when one
of them says no -- and the stub knows nothing of `tree`, `session text` or
`surface cursor`. Every fake records its calls so a test can assert that
something did NOT happen, which is the shape most of these are.
"""

import importlib.util
import io
import os

import pytest
from importlib.machinery import SourceFileLoader

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PEER_PATH = os.path.join(REPO_ROOT, "agb-peer")


@pytest.fixture(scope="session")
def peer():
    loader = SourceFileLoader("agb_peer", PEER_PATH)
    spec = importlib.util.spec_from_file_location("agb_peer", PEER_PATH,
                                                  loader=loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


MENU = "  [enter] attach   [s] split   [d] drawer   [q] quit > "
COMPOSER = "\n❯ \n  auto mode on\n[host:claude*   14:05]\n"


def session(sid="AAAA1111", name="row · cwd · %0", status="completed",
            kinds=("left",)):
    return {
        "id": sid, "name": name, "status": status,
        "surfaces": [{"kind": k, "id": "surface:%s:%s" % (sid, k),
                      "visible": True, "active": k == "left"} for k in kinds],
    }


def tree_of(*sessions):
    return {"result": {"tree": {"workspaces": [
        {"name": "farm", "sessions": list(sessions)}]}}}


class FakeCtl(object):
    """Records every call; `texts` is a queue so a pane can change over time."""

    def __init__(self, sessions=(), texts=None, cursors=None):
        self._sessions = list(sessions) or [session()]
        self.texts = list(texts) if texts is not None else [COMPOSER]
        self.cursors = list(cursors) if cursors is not None else [2]
        self.typed = []
        self.slept = []

    def sleep(self, seconds):
        self.slept.append(seconds)

    def tree(self):
        return tree_of(*self._sessions)

    def text(self, target, pane, lines):
        value = self.texts[0] if len(self.texts) == 1 else self.texts.pop(0)
        if isinstance(value, Exception):
            return None, str(value)
        return value, None

    def cursor(self, surface):
        value = self.cursors[0] if len(self.cursors) == 1 else self.cursors.pop(0)
        return value, None

    def type(self, target, pane, text):
        self.typed.append(text)
        return True


# --------------------------------------------------------------- parse_args

def test_value_args_take_both_spellings(peer):
    assert peer.parse_args(["--to", "x"])["to"] == "x"
    assert peer.parse_args(["--to=x"])["to"] == "x"


def test_empty_inline_value_is_a_missing_value(peer):
    # The convention every other parser in this repo follows. An empty --to
    # would match the first row in the tree rather than nothing.
    with pytest.raises(peer.PeerError):
        peer.parse_args(["--to="])


def test_unknown_option_is_refused(peer):
    with pytest.raises(peer.PeerError):
        peer.parse_args(["--nope"])


def test_flag_with_a_value_is_refused(peer):
    with pytest.raises(peer.PeerError):
        peer.parse_args(["--stdin=1"])


def test_bare_words_become_the_message(peer):
    assert peer.parse_args(["hello", "there"])["message"] == "hello there"


def test_double_dash_stops_option_parsing(peer):
    assert peer.parse_args(["--", "--to", "x"])["message"] == "--to x"


# ------------------------------------------------------------ match_sessions

def test_an_exact_id_is_never_made_ambiguous_by_a_name(peer):
    # The tier order is the whole point: `AAAA1111` is also a substring of the
    # other row's NAME, and without tiering this would be ambiguous.
    a = session("AAAA1111", name="alpha")
    b = session("BBBB2222", name="mentions AAAA1111 in its title")
    got = peer.match_sessions([a, b], "AAAA1111")
    assert [s["id"] for s in got] == ["AAAA1111"]


def test_a_name_substring_matches_case_insensitively(peer):
    rows = [session("AAAA1111", name="Deploy Review")]
    assert peer.match_sessions(rows, "review")


def test_an_ambiguous_prefix_returns_every_match(peer):
    rows = [session("AAAA1111"), session("AAAA2222")]
    assert len(peer.match_sessions(rows, "AAAA")) == 2


def test_an_empty_want_matches_nothing(peer):
    assert peer.match_sessions([session()], "") == []


# ---------------------------------------------------------------- surface_of

def test_the_surface_id_is_read_not_built(peer):
    # Proven by giving `surfaces` an id that concatenation would never produce.
    row = session()
    row["surfaces"] = [{"kind": "left", "id": "surface:SOMETHING-ELSE:left"}]
    assert peer.surface_of(row, "left") == "surface:SOMETHING-ELSE:left"


def test_a_missing_pane_has_no_surface(peer):
    assert peer.surface_of(session(kinds=("left",)), "right") is None


# ------------------------------------------------------------------ classify

def test_the_menu_prompt_is_menu_mode(peer):
    assert peer.classify(MENU) == peer.MODE_MENU


def test_a_composer_glyph_is_composer_mode(peer):
    assert peer.classify(COMPOSER) == peer.MODE_COMPOSER


def test_neither_marker_is_unknown_not_a_guess(peer):
    assert peer.classify("$ ls -l\ntotal 0\n") == peer.MODE_UNKNOWN
    assert peer.classify(None) == peer.MODE_UNKNOWN


def test_the_menu_wins_when_both_markers_are_on_screen(peer):
    # Scrollback can hold an old composer above a live menu. Reading that as
    # "composer" would type a message into `agb pane`'s prompt.
    assert peer.classify(COMPOSER + MENU) == peer.MODE_MENU


# ----------------------------------------------------------------- peer_busy

def test_active_and_blocked_are_busy(peer):
    assert peer.peer_busy("active")
    assert peer.peer_busy("blocked")


def test_completed_is_not_busy(peer):
    assert peer.peer_busy("completed") is None


def test_idle_is_allowed_through(peer):
    # `idle` is the bridge's word for "no current information" -- a quiet feed
    # or a removed agent -- not a statement about the agent. Refusing it would
    # strand every row whose bridge blinked.
    assert peer.peer_busy("idle") is None
    assert peer.peer_busy(None) is None


# ------------------------------------------------------------------- compose

def test_the_message_is_signed(peer):
    assert peer.compose("hi", "alpha") == "[chat from alpha] hi"


def test_an_empty_message_is_refused(peer):
    with pytest.raises(peer.PeerError):
        peer.compose("   ", "alpha")


def test_newlines_are_flattened_because_a_newline_is_the_submit_key(peer):
    got = peer.compose("one\n\ntwo", "alpha")
    assert "\n" not in got
    assert got == "[chat from alpha] one two"


@pytest.mark.parametrize("word", ["q", "quit", "exit", "s", "split", "d",
                                  "drawer", "scratch", "  QUIT  "])
def test_a_message_agb_pane_would_act_on_is_refused(peer, word):
    with pytest.raises(peer.PeerError):
        peer.compose(word, "alpha")


def test_that_refusal_is_not_a_blanket_ban_on_the_substring(peer):
    # The check is on the whole stripped message, not on containment, or every
    # sentence with "exit" in it would be undeliverable.
    assert peer.compose("does it exit cleanly?", "alpha")


# ------------------------------------------------------- the word-list guard

def test_pane_words_is_exactly_agb_ops_three_word_sets(peer, ops):
    """A cross-file agreement with no single source of truth (invariant 14).

    `agb-peer` cannot import `agb_ops` -- it is a standalone Mac script and
    `agb_ops` is loaded through `agb` -- so it spells the list itself. If
    `agb pane` grows a fourth word and this copy does not, a message that is
    exactly that word reaches a pane that acts on it.
    """
    expected = set(ops.PANE_QUIT_WORDS) | set(ops.PANE_SPLIT_WORDS) | set(
        ops.PANE_DRAWER_WORDS)
    assert expected, "the walk found nothing -- the fixture is wrong"
    assert set(peer.PANE_WORDS) == expected


# ------------------------------------------------------------ ensure_composer

def test_a_menu_row_is_armed_with_a_bare_newline(peer):
    ctl = FakeCtl(texts=[MENU, COMPOSER])
    peer.ensure_composer(ctl, session(), "left", True, 40, lambda m: None)
    assert ctl.typed == ["\n"], "the arming keystroke must be a bare newline"


def test_no_arm_refuses_a_menu_row_without_typing(peer):
    ctl = FakeCtl(texts=[MENU])
    with pytest.raises(peer.PeerError):
        peer.ensure_composer(ctl, session(), "left", False, 40, lambda m: None)
    assert ctl.typed == []


def test_an_unknown_pane_is_refused_rather_than_armed(peer):
    ctl = FakeCtl(texts=["$ ls\n"])
    with pytest.raises(peer.PeerError):
        peer.ensure_composer(ctl, session(), "left", True, 40, lambda m: None)
    assert ctl.typed == []


def test_a_composer_row_is_not_touched(peer):
    ctl = FakeCtl(texts=[COMPOSER])
    peer.ensure_composer(ctl, session(), "left", True, 40, lambda m: None)
    assert ctl.typed == []


# ----------------------------------------------------------------- wait_ready

def test_an_empty_composer_passes_immediately(peer):
    ctl = FakeCtl(cursors=[2])
    peer.wait_ready(ctl, session(), "left", 5, 8, False, 40, lambda m: None)
    assert ctl.slept == []


def test_a_dirty_composer_is_never_typed_into(peer):
    ctl = FakeCtl(cursors=[41])
    with pytest.raises(peer.PeerError) as caught:
        peer.wait_ready(ctl, session(), "left", 2, 0, False, 40, lambda m: None)
    assert caught.value.code == 3
    assert ctl.typed == []


def test_a_busy_peer_is_not_even_asked_for_its_cursor(peer):
    # The status gate is first on purpose: an agent mid-turn has no composer to
    # measure, so a cursor reading there is noise.
    ctl = FakeCtl(sessions=[session(status="active")], cursors=[2])
    with pytest.raises(peer.PeerError):
        peer.wait_ready(ctl, session(status="active"), "left", 1, 0, False, 40,
                        lambda m: None)


def test_force_overrides_the_status_gate_but_not_the_composer_gate(peer):
    # The companion this pair needs: same input, one variable changed.
    busy = session(status="active")
    peer.wait_ready(FakeCtl(cursors=[2]), busy, "left", 1, 0, True, 40,
                    lambda m: None)
    with pytest.raises(peer.PeerError):
        peer.wait_ready(FakeCtl(cursors=[9]), busy, "left", 0, 0, True, 40,
                        lambda m: None)


def test_a_missing_pane_is_reported_before_anything_is_read(peer):
    ctl = FakeCtl()
    with pytest.raises(peer.PeerError) as caught:
        peer.wait_ready(ctl, session(kinds=("left",)), "right", 1, 0, False, 40,
                        lambda m: None)
    assert caught.value.code == 2


# -------------------------------------------------------------------- deliver

def test_the_return_is_a_second_call_after_verification(peer):
    body = "[chat from alpha] hello"
    ctl = FakeCtl(texts=[COMPOSER + body])
    peer.deliver(ctl, session(), "left", body, True, 40, lambda m: None)
    assert ctl.typed == [body, "\n"]


def test_no_send_types_but_never_presses_return(peer):
    body = "[chat from alpha] hello"
    ctl = FakeCtl(texts=[COMPOSER + body])
    peer.deliver(ctl, session(), "left", body, False, 40, lambda m: None)
    assert ctl.typed == [body]


def test_text_that_never_rendered_is_never_submitted(peer):
    # The failure this exists for: a permission dialog appearing between the
    # cursor check and the keystrokes, swallowing them. Pressing Return then
    # answers the dialog.
    body = "[chat from alpha] hello"
    ctl = FakeCtl(texts=[COMPOSER])
    with pytest.raises(peer.PeerError) as caught:
        peer.deliver(ctl, session(), "left", body, True, 40, lambda m: None)
    assert caught.value.code == 4
    assert ctl.typed == [body], "it must not press Return after a failed verify"


def test_a_wrapped_long_line_still_verifies(peer):
    # agterm wraps; the whole body is then never a single substring. Matching
    # on the tail is what keeps a long message from failing verification.
    body = "[chat from alpha] " + ("x" * 200) + "END-OF-MESSAGE"
    ctl = FakeCtl(texts=[COMPOSER + body[-40:]])
    peer.deliver(ctl, session(), "left", body, True, 40, lambda m: None)
    assert ctl.typed[-1] == "\n"


# ----------------------------------------------------------------------- main

def test_dry_run_touches_nothing(peer):
    ctl = FakeCtl()
    out = io.StringIO()
    assert peer.main(["--to", "AAAA", "--dry-run", "hi"], out, ctl) == 0
    assert ctl.typed == []
    assert "dry run" in out.getvalue()


def test_a_pane_the_binary_rejects_is_refused_locally(peer):
    # `primary` is in agterm 0.24.0's --help and is rejected by the binary.
    with pytest.raises(peer.PeerError):
        peer.main(["--to", "AAAA", "--pane", "primary", "hi"], io.StringIO(),
                  FakeCtl())


def test_an_unmatched_peer_exits_two(peer):
    with pytest.raises(peer.PeerError) as caught:
        peer.main(["--to", "nothing-like-this", "hi"], io.StringIO(), FakeCtl())
    assert caught.value.code == 2


def test_the_whole_flow_types_then_sends(peer):
    body = "[chat from alpha] hello"
    ctl = FakeCtl(texts=[COMPOSER, COMPOSER + body])
    rc = peer.main(["--to", "AAAA", "--from", "alpha", "hello"], io.StringIO(),
                   ctl)
    assert rc == 0
    assert ctl.typed == [body, "\n"]
