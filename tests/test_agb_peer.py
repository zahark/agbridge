"""agb-peer -- the Mac-side peer delivery script.

The flow tests drive a fake `Ctl` rather than the `agtermctl` stub, because
what is worth pinning here is the ORDER of the gates and what happens when one
of them says no -- and the stub knows nothing of `tree`, `session text` or
`surface cursor`. Every fake records its calls so a test can assert that
something did NOT happen, which is the shape most of these are.
"""

import ast
import importlib.util
import io
import os
import re

import pytest
from importlib.machinery import SourceFileLoader

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PEER_PATH = os.path.join(REPO_ROOT, "agb-peer")


# ⚠️ The `peer` fixture moved to conftest.py, and the move was not cosmetic.
# The copy that lived here never registered the module in `sys.modules`, so
# `agb-peer-setup.load_peer` would load a SECOND copy with its own PeerError
# class. A fixture defined here would also shadow conftest's, quietly undoing
# the fix. See conftest.PEER_MODULE.


MENU = "  [enter] attach   [s] split   [d] drawer   [q] quit > "
COMPOSER = "\n❯ \n  auto mode on\n[host:claude*   14:05]\n"


FOREGROUND = ["/usr/bin/python3", "-S", "-E", "/opt/agb", "pane",
              "--host", "buildbox01", "--pane", "%7", "--tmux", "work"]


def session(sid="AAAA1111", name="row · cwd · %0", status="completed",
            kinds=("left",), foreground=None):
    return {
        "id": sid, "name": name, "status": status,
        "foreground": foreground if foreground is not None else FOREGROUND,
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
        self.text_lines = []

    def sleep(self, seconds):
        self.slept.append(seconds)

    def tree(self):
        return tree_of(*self._sessions)

    def text(self, target, pane, lines):
        self.text_lines.append(lines)
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
    assert ctl.typed == [body, "\r"]


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


BUSY_SCREEN = "\n\u203a Ask Codex to do anything\n" \
    "\u2022 Working (6s \u2022 esc to interrupt)\n"

CLAUDE_BUSY_SCREEN = COMPOSER + \
    "\u23f5\u23f5 bypass permissions on (shift+tab to cycle) \u00b7 " \
    "esc to interrupt \u00b7 \u2190 for agents\n"


def test_a_working_peer_is_read_off_its_pane(peer):
    """⚠️ MEASURED 2026-08-26 on live panes, both agents. The relay's other busy
    gate reads the agterm row status, which comes from the agent's own agbridge
    hooks -- and Codex fires none, so its row says `completed` for ever. This is
    the only gate that can see a working Codex."""
    assert peer.pane_busy(BUSY_SCREEN)
    assert peer.pane_busy(CLAUDE_BUSY_SCREEN)


def test_an_idle_pane_is_not_read_as_working(peer):
    """The companion, and not decoration: a mark that matched everything would
    hold every message for ever and every busy test above would still pass."""
    assert peer.pane_busy(COMPOSER) is None
    assert peer.pane_busy("\n\u203a Ask Codex to do anything\n") is None
    assert peer.pane_busy("") is None
    assert peer.pane_busy(None) is None


def test_a_working_peer_is_held_even_with_a_clean_status(peer):
    """⚠️ The whole point: status is `completed` and the composer is EMPTY --
    cursor at column 2 -- which both read as ready. Only the pane says
    otherwise. Held (code 3), never dropped, so the next tick retries."""
    ctl = RelayCtl({"AAAA1111": BUSY_SCREEN, "BBBB2222": COMPOSER})
    with pytest.raises(peer.PeerError) as caught:
        peer.wait_ready(ctl, session(status="completed"), "left", 0, 0, False,
                        500, lambda m: None)
    assert caught.value.code == 3
    assert "working" in str(caught.value), caught.value


def test_the_same_peer_is_ready_once_it_stops_working(peer):
    """The differ-by-one-variable companion. Without it the test above passes
    against a gate that can never open."""
    ctl = RelayCtl({"AAAA1111": COMPOSER, "BBBB2222": COMPOSER})
    peer.wait_ready(ctl, session(status="completed"), "left", 0, 0, False,
                    500, lambda m: None)


def test_force_still_bypasses_a_working_peer(peer):
    """`--force` means "type anyway", and it has always bypassed the status
    gate. A new gate it did not bypass would silently narrow the flag."""
    ctl = RelayCtl({"AAAA1111": BUSY_SCREEN, "BBBB2222": COMPOSER})
    peer.wait_ready(ctl, session(status="active"), "left", 0, 0, True,
                    500, lambda m: None)


def test_an_unreadable_pane_holds_rather_than_passing(peer):
    """A read failure is no information, and no information may not mean ready."""

    class Unreadable(RelayCtl):
        def text(self, target, pane, lines, whole=False):
            return "", "boom"

    ctl = Unreadable({"AAAA1111": COMPOSER, "BBBB2222": COMPOSER})
    with pytest.raises(peer.PeerError) as caught:
        peer.wait_ready(ctl, session(status="completed"), "left", 0, 0, False,
                        500, lambda m: None)
    assert caught.value.code == 3


def test_the_submit_key_is_a_carriage_return(peer):
    """⚠️ MEASURED 2026-08-26 on live panes, after a Codex peer spent an evening
    receiving messages it never acted on:

        raw 0x0A into Codex   -> a newline is INSERTED
        raw 0x0A into Claude  -> a newline is INSERTED
        raw 0x0D into Codex   -> SUBMITTED
        agterm `type "\n"`    -> Claude submits; Codex INSERTS A NEWLINE

    The last row was read by counting blank lines: an empty Codex composer
    renders one above the model line, the loaded one rendered two. So the Return
    was going out and arriving -- as a newline. CR is what a real Return key
    sends, and it is the only one of the two both TUIs agree about.

    Asserted against a literal rather than against the constant: reading
    `peer.SUBMIT_KEY` here would make the test say only that the code equals
    itself.
    """
    assert peer.SUBMIT_KEY == "\r"


def test_the_menu_is_armed_with_a_newline_not_the_submit_key(peer):
    """⚠️ The asymmetry is deliberate and a merge would break it silently.

    `agb pane`'s menu is a shell `read` on a tty in CANONICAL mode, where the
    line discipline's ICRNL makes CR and LF equivalent -- and that path is
    verified as it stands. A TUI puts the tty in RAW mode and decodes keys
    itself, so the equivalence does not hold there. Same keystroke, two readers;
    only the raw-mode one is picky.
    """
    body = io.open(PEER_PATH, encoding="utf-8").read()
    assert body.count('ctl.type(target, pane, SUBMIT_KEY)') == 1, \
        "exactly one composer submit"
    assert body.count('ctl.type(target, pane, "\\n")') == 1, \
        "the menu attach must still be a literal newline"
    assert body.count('ctl.type(row, pane, "\\n")') == 1, \
        "and so must the relay's own menu attach"


def test_a_wrapped_long_line_still_verifies(peer):
    # agterm wraps; the whole body is then never a single substring. Matching
    # on the tail is what keeps a long message from failing verification.
    body = "[chat from alpha] " + ("x" * 200) + "END-OF-MESSAGE"
    ctl = FakeCtl(texts=[COMPOSER + body[-40:]])
    peer.deliver(ctl, session(), "left", body, True, 40, lambda m: None)
    assert ctl.typed[-1] == "\r"


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
    assert ctl.typed == [body, "\r"]


# ---------------------------------------------------------------- the listing

def test_the_listing_reads_the_same_window_the_flow_does(peer):
    """MEASURED 2026-08-24: it did not, and the listing lied about a live row.

    `cmd_list` used a hardcoded 6 lines while the flow used 40. A `blocked`
    agent's permission dialog pushed the composer glyph off the 6-line window,
    so `--list` reported `unknown` for a row `--dry-run` reported as
    `composer` -- a diagnostic disagreeing with the thing it diagnoses, in the
    direction that reads as safe.
    """
    ctl = FakeCtl(sessions=[session("AAAA1111"), session("BBBB2222")])
    peer.cmd_list(ctl, "left", 40, io.StringIO())
    assert ctl.text_lines, "no pane was read -- the loop did not run"
    assert set(ctl.text_lines) == {40}


def test_the_listing_names_every_row(peer):
    ctl = FakeCtl(sessions=[session("AAAA1111", name="one"),
                            session("BBBB2222", name="two")])
    out = io.StringIO()
    peer.cmd_list(ctl, "left", 40, out)
    body = out.getvalue()
    assert "AAAA1111" in body and "BBBB2222" in body


# ===========================================================================
# phase 1: the marker protocol and the relay
# ===========================================================================

def wrap(text, width=24):
    """agterm's wrapping, with the right-padding it adds to short lines."""
    return "\n".join(text[i:i + width].ljust(width)
                     for i in range(0, len(text), width))


class RelayCtl(object):
    """Panes with real content: typing into one CHANGES what a later read sees.

    That is the whole point -- loop suppression and the post-type verification
    are both claims about what ends up on the recipient's screen, and a fake
    whose panes never change cannot test either.
    """

    def __init__(self, panes, cursors=None):
        self.current = dict(panes)
        self.typed = []
        self.said = []
        self.cursors = list(cursors) if cursors else [2]
        self.sleeps = 0
        self.dashboards = []
        self.dashboard_out = ""
        self.closes = 0

    def sleep(self, seconds):
        self.sleeps += 1

    def tree(self):
        return tree_of(*[session(row, foreground=FOREGROUND)
                         for row in sorted(self.current)])

    def text(self, target, pane, lines, whole=False):
        return self.current.get(target, ""), None

    def cursor(self, surface):
        value = self.cursors[0] if len(self.cursors) == 1 else self.cursors.pop(0)
        return value, None

    def type(self, target, pane, text):
        self.typed.append((target, pane, text))
        self.current[target] = self.current.get(target, "") + "\n" + text
        return True

    def dashboard(self, members):
        self.dashboards.append(list(members))
        return True, self.dashboard_out, ""

    def dashboard_close(self):
        self.closes += 1
        return True, ""

    def say(self, message):
        self.said.append(message)


PEOPLE = {"alice": ("AAAA1111", "left", "farmbox", None),
          "bob": ("BBBB2222", "left", "local", None)}


def panes(alice="", bob=""):
    return {"AAAA1111": COMPOSER + alice, "BBBB2222": COMPOSER + bob}


# ---------------------------------------------------- the doorbell protocol

class Fetcher(object):
    """Stands in for ssh. Records argv so a test can assert WHERE it ran.

    Answers the LIST call with its canned output and every `set -pu` with
    success, because `drain` is now one listing plus one unset per message.
    """

    def __init__(self, output="", rc=0, unset_rc=0):
        self.output, self.rc, self.unset_rc, self.calls = output, rc, unset_rc, []

    def __call__(self, argv):
        self.calls.append(argv)
        # `show-options` is the tmux transport, `cat` the file one. Both are
        # reads and both answer with the canned output; everything else is a
        # write (`set -pu`, `rm`) and answers like one.
        if "show-options" in argv or "cat" in argv:
            return self.rc, self.output, ""
        return self.unset_rc, "", "refused" if self.unset_rc else ""

    @property
    def unsets(self):
        return [a for a in self.calls if "-pu" in a]


def framed(*messages):
    """What `tmux show-options -p` prints: one line per option, tmux-escaped."""
    lines = ["some-other-option 42"]
    for ident, to, text in messages:
        value = ("%s\n%s" % (to, text)).replace("\\", "\\\\")
        value = value.replace('"', '\\"').replace("\n", "\\n")
        lines.append('%s%s "%s"' % (peer_prefix(), ident, value))
    return "\n".join(lines) + "\n"


_PREFIX = [None]


def peer_prefix():
    return _PREFIX[0]


@pytest.fixture(autouse=True)
def _prefix(peer):
    _PREFIX[0] = peer.OPTION_PREFIX
    return peer.OPTION_PREFIX


def test_the_option_value_is_plain_and_readable(peer):
    # `tmux show-options -pqv @agbpeer_msg_x` is a thing a human debugs with.
    value = peer.option_value("bob", "hello  there")
    assert value == "bob\nhello there"


@pytest.mark.parametrize("bad", ["", "bob", "bob\n", "\nhello", "   \n  "])
def test_a_half_written_option_is_not_data(peer, bad):
    assert peer.parse_option_value(bad) is None


def test_the_doorbell_keeps_the_original_window_name(peer):
    # Or a second send would render `claude [peer #a] [peer #b]` for ever.
    assert peer.doorbell_name("claude", "k3n9x2") == "claude [peer #k3n9x2]"


def test_the_doorbell_is_read_from_anywhere_in_the_capture(peer):
    """Not just the last line: the status bar is normally last, but a pane
    caught mid-redraw can put it elsewhere and a missed doorbell costs a round
    trip."""
    assert peer.read_doorbell("x\nclaude [peer #abc12]\nmore") == "abc12"
    assert peer.read_doorbell("nothing here") is None


def test_the_newest_doorbell_wins(peer):
    assert peer.read_doorbell("[peer #old11]\n[peer #new22]") == "new22"


def test_a_listing_round_trips_one_message(peer):
    got = peer.parse_show_options(framed(("k3n9x2", "bob", "hello there")))
    assert len(got) == 1
    assert got[0]["to"] == "bob" and got[0]["text"] == "hello there"


def test_other_options_on_the_pane_are_ignored(peer):
    # A pane carries options that are none of our business.
    assert peer.parse_show_options("status on\nmode-keys vi\n") == []


def test_a_listing_takes_everything_pending(peer):
    """The doorbell shows only the LATEST id, so a tick that missed one would
    otherwise lose it. Sweeping every option makes a missed doorbell harmless.
    """
    got = peer.parse_show_options(framed(("aaa", "bob", "one"),
                                         ("bbb", "bob", "two")))
    assert [g["text"] for g in got] == ["one", "two"]


def test_tmux_escapes_are_undone(peer):
    """`show-options -p` renders each option on ONE line, escaping newline,
    quote and backslash -- which is exactly why the Mac can parse it with no
    help from the far side, and no remote shell."""
    got = peer.parse_show_options(
        '%sxyz "bob\\nsaid \\"hi\\" and a \\\\slash"\n' % (peer.OPTION_PREFIX,))
    assert got[0]["text"] == 'said "hi" and a \\slash'


def test_a_bare_unquoted_value_is_read_too(peer):
    # tmux only quotes when it has to.
    assert peer.parse_show_options("%sxyz bob\n" % (peer.OPTION_PREFIX,)) == []


@pytest.mark.parametrize("bad", ["", "garbage\n", '%sxyz ""\n', '%sxyz "bob"\n'])
def test_a_malformed_option_is_skipped_not_raised(peer, bad):
    assert peer.parse_show_options(bad % (peer.OPTION_PREFIX,)
                                   if "%s" in bad else bad) == []


def test_the_fetch_uses_no_remote_shell(peer):
    """MEASURED: the first version ran a POSIX `for` loop over ssh and failed
    with `Illegal variable name.` -- tcsh, because ssh hands the command to the
    remote LOGIN shell. There is no remote script now, only argv."""
    argv = peer.ssh_argv("farmbox", peer.list_argv("%7"))
    assert argv[:1] == ["ssh"]
    assert "tmux" in argv
    for word in argv:
        assert not any(c in word for c in "$();|&<>"), word


def test_the_drain_unsets_what_it_read(peer):
    """Or the same message is fetched for ever, and re-delivered every time
    any later doorbell rings."""
    fetch = Fetcher(framed(("aaa", "bob", "one")))
    peer.drain(fetch, "alice", "farmbox", "%7", lambda m: None)
    assert fetch.unsets, fetch.calls
    assert peer.OPTION_PREFIX + "aaa" in fetch.unsets[0]


def test_a_failed_unset_is_reported_not_swallowed(peer):
    said = []
    fetch = Fetcher(framed(("aaa", "bob", "one")), unset_rc=1)
    got = peer.drain(fetch, "alice", "farmbox", "%7", said.append)
    assert got, "the message is still delivered -- the clear is what failed"
    assert any("could not clear" in s for s in said)


def test_pane_argv_field_reads_the_rows_own_command_line(peer):
    assert peer.pane_argv_field(FOREGROUND, "--host") == "buildbox01"
    assert peer.pane_argv_field(FOREGROUND, "--pane") == "%7"
    assert peer.pane_argv_field(FOREGROUND, "--nope") is None
    assert peer.pane_argv_field(None, "--host") is None


def test_pane_argv_field_takes_the_inline_spelling_too(peer):
    assert peer.pane_argv_field(["pane", "--host=box9"], "--host") == "box9"


# ------------------------------------------------------ parse_participants

def test_participants_default_to_the_left_pane_and_no_target(peer):
    assert peer.parse_participants(["a=R1", "b=R2"]) == {
        "a": ("R1", "left", None, None), "b": ("R2", "left", None, None)}


def test_a_participant_can_name_its_ssh_target(peer):
    got = peer.parse_participants(["a=alpha@farmbox", "b=beta@local"])
    assert got["a"] == ("alpha", "left", "farmbox", None)
    assert got["b"] == ("beta", "left", "local", None)


def test_a_pane_suffix_is_agterms_own_spelling(peer):
    assert peer.parse_participants(["a=R1:right", "b=R2"])["a"] == (
        "R1", "right", None, None)


@pytest.mark.parametrize("words", [
    ["a=R1"], ["aR1", "b=R2"], ["a=", "b=R2"], ["=R1", "b=R2"],
    ["a=R1:primary", "b=R2"], ["a=R1", "a=R2"], ["a=R1@", "b=R2"],
])
def test_a_malformed_participant_list_is_refused(peer, words):
    with pytest.raises(peer.PeerError):
        peer.parse_participants(words)


# ---------------------------------------------------------------- cmd_send

class LocalRun(object):
    def __init__(self, window="claude", base=""):
        self.calls, self.window, self.base = [], window, base

    def __call__(self, argv):
        self.calls.append(argv)
        # Matched on the VERB, not on argv[0]: `tmux_binary` may resolve an
        # absolute path, and a fake that only knows the bare name would send
        # every call down its default branch and quietly change the test.
        verb = argv[1] if len(argv) > 1 else ""
        if verb == "show-options" and "-pqv" in argv:
            return 0, self.base, ""
        if verb == "display-message":
            return 0, self.window, ""
        return 0, "", ""


def test_send_writes_a_tmux_option_and_never_the_screen(peer):
    """The whole lesson of the first design: Claude Code does not render tool
    output onto the pane, so a printed message is invisible to everything."""
    run, out = LocalRun(), io.StringIO()
    peer.cmd_send("bob", "hello there", run, out, now=1.0,
                  env={"TMUX_PANE": "%7"})
    sets = [c for c in run.calls
            if os.path.basename(c[0]) == "tmux" and c[1] == "set"]
    assert any(c[5].startswith(peer.OPTION_PREFIX) for c in sets), sets
    assert any(c[1] == "rename-window" for c in run.calls)
    assert "hello there" not in out.getvalue(), \
        "the message must not be printed -- nothing can read it there"


def test_send_refuses_outside_tmux(peer):
    with pytest.raises(peer.PeerError):
        peer.cmd_send("bob", "hi", LocalRun(), io.StringIO(), env={})


def test_send_remembers_the_window_name_only_once(peer):
    run = LocalRun(base="claude")          # already remembered
    peer.cmd_send("bob", "hi", run, io.StringIO(), env={"TMUX_PANE": "%7"})
    assert not any(c[1] == "display-message" for c in run.calls), \
        "it must not re-read the window name once a base is stored"


@pytest.mark.parametrize("word", ["q", "quit", "exit", "  SPLIT "])
def test_send_refuses_a_word_agb_pane_acts_on(peer, word):
    with pytest.raises(peer.PeerError):
        peer.cmd_send("bob", word, LocalRun(), io.StringIO(),
                      env={"TMUX_PANE": "%7"})


def test_send_refuses_an_empty_message(peer):
    with pytest.raises(peer.PeerError):
        peer.cmd_send("bob", "   ", LocalRun(), io.StringIO(),
                      env={"TMUX_PANE": "%7"})


# ------------------------------------------------------------------- drain

def test_a_named_target_is_reached_over_ssh(peer):
    fetch = Fetcher(framed(("aaa", "bob", "hi")))
    got = peer.drain(fetch, "alice", "farmbox", "%7", lambda m: None)
    assert got[0]["text"] == "hi"
    assert fetch.calls[0][0] == "ssh" and "farmbox" in fetch.calls[0]


def test_local_means_no_ssh_at_all(peer):
    """A Mac-side participant uses the identical mechanism minus the ssh --
    which is what keeps one design covering all three pairings."""
    fetch = Fetcher(framed(("aaa", "bob", "hi")))
    peer.drain(fetch, "bob", "local", "%7", lambda m: None)
    assert os.path.basename(fetch.calls[0][0]) == "tmux", fetch.calls[0]


def test_a_failed_fetch_says_why_and_returns_nothing(peer):
    said = []
    assert peer.drain(Fetcher("", rc=255), "alice", "box", "%7", said.append) == []
    assert any("fetch failed" in s for s in said)


def test_no_tmux_pane_is_reported_rather_than_guessed(peer):
    said = []
    assert peer.drain(Fetcher(), "alice", "box", None, said.append) == []
    assert any("no tmux pane" in s for s in said)


# --------------------------------------------------------------- the relay

def framed_bare(*messages):
    """What `tmux show-options -p` prints when the value needs NO quotes.

    ⚠️ tmux quotes only when it has to, so a value with no space comes back
    bare -- and `framed` above always emits the QUOTED form, which is why the
    whole suite exercised one of the two shapes for the life of this transport.
    A single-word message is the common bare case, and `agb-peer who` is one by
    design.
    """
    lines = ["some-other-option 42"]
    for ident, to, text in messages:
        value = ("%s\n%s" % (to, text)).replace("\\", "\\\\")
        value = value.replace("\n", "\\n")
        assert " " not in value, "a bare value by definition has no space"
        lines.append("%s%s %s" % (peer_prefix(), ident, value))
    return "\n".join(lines) + "\n"


def bell(ident):
    return "\nclaude [peer #%s]\n" % (ident,)


def test_a_new_doorbell_triggers_a_fetch_and_a_delivery(peer):
    ctl = RelayCtl(panes(alice=bell("aaa")))
    fetch = Fetcher(framed(("aaa", "bob", "hello")))
    peer.relay_tick(ctl, PEOPLE, {}, [], 500, ctl.say, fetch)
    assert fetch.calls, "the doorbell rang and nothing was fetched"
    bodies = [t for (_, _, t) in ctl.typed if t not in ("\n", "\r")]
    assert bodies == ["[chat from alice] hello"]


def test_an_unchanged_doorbell_costs_no_ssh(peer):
    """The entire point of the doorbell: watching is free, fetching is not."""
    ctl = RelayCtl(panes(alice=bell("aaa")))
    fetch = Fetcher(framed(("aaa", "bob", "hello")))
    seen, pending = {}, []
    for _ in range(4):
        peer.relay_tick(ctl, PEOPLE, seen, pending, 500, ctl.say, fetch)
    listings = [a for a in fetch.calls if "show-options" in a]
    assert len(listings) == 1, "four ticks, one ring, one listing"


def test_no_doorbell_costs_no_ssh(peer):
    ctl = RelayCtl(panes())
    fetch = Fetcher()
    peer.relay_tick(ctl, PEOPLE, {}, [], 500, ctl.say, fetch)
    assert fetch.calls == []


def test_priming_fetches_and_discards(peer):
    """Leaving stale options in tmux would mean the first real message's drain
    swept up an hour-old conversation and delivered it as if it were new."""
    ctl = RelayCtl(panes(alice=bell("old")))
    fetch = Fetcher(framed(("old", "bob", "stale")))
    seen, pending = {}, []
    peer.relay_tick(ctl, PEOPLE, seen, pending, 500, ctl.say, fetch,
                    deliver_new=False)
    assert fetch.calls, "priming must still DRAIN, or the next fetch replays it"
    assert ctl.typed == [], "but it must deliver none of it"
    assert any("discarded" in s and "#old" in s for s in ctl.said), ctl.said


def test_the_sender_is_the_pane_the_doorbell_rang_in(peer):
    # The option value names the RECIPIENT; the sender is the place, which an
    # agent cannot misstate because it cannot ring another agent's doorbell.
    ctl = RelayCtl(panes(alice=bell("aaa")))
    peer.relay_tick(ctl, PEOPLE, {}, [], 500, ctl.say,
                    Fetcher(framed(("aaa", "bob", "trust me"))))
    assert [t for (_, _, t) in ctl.typed if t not in ("\n", "\r")] == [
        "[chat from alice] trust me"]


def test_a_detached_participant_is_ARMED_not_merely_reported(peer):
    """Every row `agb-refresh` re-mints comes back detached, and a menu is not
    a tmux screen -- so the bar is absent and the doorbell cannot be seen.
    Complaining and waiting would make every refresh a manual re-attach of
    every participant.
    """
    ctl = RelayCtl({"AAAA1111": MENU, "BBBB2222": COMPOSER})
    fetch = Fetcher()
    peer.relay_tick(ctl, PEOPLE, {}, [], 500, ctl.say, fetch)
    assert fetch.calls == [], "nothing to fetch from a pane we cannot read"
    assert ("AAAA1111", "left", "\n") in ctl.typed, \
        "it must attach the row with the bare-newline primitive"
    assert any("detached" in s for s in ctl.said)


def test_arming_is_not_retried_every_single_tick(peer):
    # An ssh that cannot connect would otherwise be hammered once a second.
    ctl = RelayCtl({"AAAA1111": MENU, "BBBB2222": COMPOSER})
    notes = {}
    for _ in range(9):
        peer.relay_tick(ctl, PEOPLE, {}, [], 500, ctl.say, Fetcher(),
                        notes=notes)
    assert len(ctl.typed) == 1, ctl.typed


def test_a_missing_row_is_eventually_reported_not_silently_skipped(peer):
    """A row is briefly absent while it is re-minted, so not on tick one. But a
    relay that never said anything would leave "gone" looking like "quiet"."""
    ctl = RelayCtl(panes())
    people = {"alice": ("VANISHED", "left", None, None),
              "bob": ("BBBB2222", "left", "local", None)}
    notes = {}
    peer.relay_tick(ctl, people, {}, [], 500, ctl.say, Fetcher(), notes=notes)
    assert not any("VANISHED" in s or "alice" in s for s in ctl.said), \
        "one missed tick is a refresh in progress, not news"
    for _ in range(2):
        peer.relay_tick(ctl, people, {}, [], 500, ctl.say, Fetcher(),
                        notes=notes)
    assert any("alice" in s for s in ctl.said), ctl.said


def test_a_row_that_comes_back_clears_its_absence_count(peer):
    ctl = RelayCtl(panes())
    notes = {}
    gone = {"alice": ("VANISHED", "left", None, None),
            "bob": ("BBBB2222", "left", "local", None)}
    for _ in range(2):
        peer.relay_tick(ctl, gone, {}, [], 500, ctl.say, Fetcher(), notes=notes)
    peer.relay_tick(ctl, PEOPLE, {}, [], 500, ctl.say, Fetcher(), notes=notes)
    assert ("gone", "alice") not in notes


def test_send_pins_automatic_rename_off(peer):
    """MEASURED: tmux's global default is `automatic-rename on`. The doorbell
    survives today only as a side effect of renaming the window, which is an
    undocumented dependency -- if it ever flips back, tmux wipes the doorbell
    and the relay goes deaf with no error anywhere.
    """
    run = LocalRun()
    peer.cmd_send("bob", "hi", run, io.StringIO(), env={"TMUX_PANE": "%7"})
    pinned = [c for c in run.calls
              if c[1:] == ["set", "-w", "-t", "%7", "automatic-rename", "off"]]
    assert pinned, run.calls


def test_a_busy_recipient_holds_the_message_instead_of_blocking(peer):
    ctl = RelayCtl(panes(alice=bell("aaa")), cursors=[41, 2])
    fetch = Fetcher(framed(("aaa", "bob", "held")))
    seen, pending = {}, []
    assert peer.relay_tick(ctl, PEOPLE, seen, pending, 500, ctl.say, fetch) == 1
    assert ctl.typed == []
    peer.relay_tick(ctl, PEOPLE, seen, pending, 500, ctl.say, fetch)
    assert [t for (_, _, t) in ctl.typed if t not in ("\n", "\r")] == ["[chat from alice] held"]


def test_a_message_to_a_stranger_is_dropped_with_a_reason(peer):
    ctl = RelayCtl(panes(alice=bell("aaa")))
    seen, pending = {}, []
    peer.relay_tick(ctl, PEOPLE, seen, pending, 500, ctl.say,
                    Fetcher(framed(("aaa", "carol", "who?"))))
    assert ctl.typed == [] and pending == []
    assert any("carol" in s for s in ctl.said)


def test_the_relay_never_types_a_doorbell(peer):
    ctl = RelayCtl(panes(alice=bell("aaa")))
    seen, pending = {}, []
    for _ in range(4):
        peer.relay_tick(ctl, PEOPLE, seen, pending, 500, ctl.say,
                        Fetcher(framed(("aaa", "bob", "no loops"))))
    for _, _, text in ctl.typed:
        assert "[peer #" not in text


# ------------------------------------------------------------- resolve_all

def test_resolve_all_binds_labels_to_current_ids(peer):
    ctl = RelayCtl(panes())
    spec = {"alice": ("AAAA1111", "left", None, None)}
    assert peer.resolve_all(ctl, spec, lambda m: None)["alice"][0] == "AAAA1111"


def test_a_vanished_row_keeps_its_previous_binding(peer):
    """A row is briefly absent while `agb-refresh` re-mints it. Dropping the
    participant on that transient would make the relay deaf until restart."""
    ctl = RelayCtl(panes())
    spec = {"alice": ("NOSUCHROW", "left", None, None)}
    previous = {"alice": ("AAAA1111", "left", None, None)}
    got = peer.resolve_all(ctl, spec, lambda m: None, previous)
    assert got["alice"] == previous["alice"]


def test_an_unresolvable_new_participant_is_reported(peer):
    said = []
    peer.resolve_all(RelayCtl(panes()), {"z": ("NOPE", "left", None, None)}, said.append)
    assert said


# --------------------------------------------------------------- cmd_relay

def test_cmd_relay_primes_before_it_delivers(peer):
    ctl = RelayCtl(panes(alice=bell("old")))
    out = io.StringIO()
    peer.cmd_relay(ctl, ["alice=AAAA1111", "bob=BBBB2222"], 500, 0, True, out,
                   fetch=Fetcher(framed(("old", "bob", "stale"))))
    assert ctl.typed == []
    assert "primed" in out.getvalue()


def test_cmd_relay_opens_a_dashboard_when_asked(peer):
    ctl = RelayCtl(panes())
    peer.cmd_relay(ctl, ["alice=AAAA1111", "bob=BBBB2222"], 500, 0, True,
                   io.StringIO(), dashboard=True, fetch=Fetcher())
    assert ctl.dashboards == [["AAAA1111:left", "BBBB2222:left"]]


def test_cmd_relay_opens_no_dashboard_by_default(peer):
    ctl = RelayCtl(panes())
    peer.cmd_relay(ctl, ["alice=AAAA1111", "bob=BBBB2222"], 500, 0, True,
                   io.StringIO(), fetch=Fetcher())
    assert ctl.dashboards == []


# The relay used to leave its grid on the screen after it exited -- agterm has
# exactly one grid, so the next thing that wanted one found the dead relay's
# cells in it. Defect 1 of the agb-dashboard plan; Task 2a.

class InterruptedRelayCtl(RelayCtl):
    """Ctrl-C at the first sleep -- the relay's own documented exit, which
    reaches neither of `cmd_relay`'s in-loop `return`s."""

    def sleep(self, seconds):
        raise KeyboardInterrupt


def test_cmd_relay_closes_the_grid_it_opened_on_ctrl_c(peer):
    ctl = InterruptedRelayCtl(panes())
    with pytest.raises(KeyboardInterrupt):
        peer.cmd_relay(ctl, ["alice=AAAA1111", "bob=BBBB2222"], 500, 0, False,
                       io.StringIO(), dashboard=True, fetch=Fetcher())
    assert ctl.dashboards, "the grid never opened -- the test proves nothing"
    assert ctl.closes == 1


def test_cmd_relay_closes_the_grid_it_opened_on_return(peer):
    """`once=True` returns from inside the loop; the `finally` covers it too."""
    ctl = RelayCtl(panes())
    peer.cmd_relay(ctl, ["alice=AAAA1111", "bob=BBBB2222"], 500, 0, True,
                   io.StringIO(), dashboard=True, fetch=Fetcher())
    assert ctl.dashboards and ctl.closes == 1


def test_cmd_relay_closes_no_grid_it_did_not_open(peer):
    """agterm has ONE grid and no ownership token, so a relay that never
    opened one must not close whatever somebody else has up."""
    ctl = InterruptedRelayCtl(panes())
    with pytest.raises(KeyboardInterrupt):
        peer.cmd_relay(ctl, ["alice=AAAA1111", "bob=BBBB2222"], 500, 0, False,
                       io.StringIO(), fetch=Fetcher())
    assert ctl.dashboards == [] and ctl.closes == 0


def test_cmd_relay_closes_nothing_when_the_open_failed(peer):
    """Nothing was opened, so there is no ownership to claim."""

    class NoGrid(RelayCtl):
        def dashboard(self, members):
            self.dashboards.append(list(members))
            return False, "", "no agterm"

    ctl = NoGrid(panes())
    peer.cmd_relay(ctl, ["alice=AAAA1111", "bob=BBBB2222"], 500, 0, True,
                   io.StringIO(), dashboard=True, fetch=Fetcher())
    assert ctl.dashboards and ctl.closes == 0


def test_cmd_relay_survives_a_close_that_reports_failure(peer):
    ctl = RelayCtl(panes())
    ctl.dashboard_close = lambda: (False, "no such grid")
    out = io.StringIO()
    peer.cmd_relay(ctl, ["alice=AAAA1111", "bob=BBBB2222"], 500, 0, True, out,
                   dashboard=True, fetch=Fetcher())
    assert "dashboard: no such grid" in out.getvalue()


def test_cmd_relay_survives_a_close_that_raises(peer):
    """The close runs from a `finally`; an exception there would replace the
    real exit -- a KeyboardInterrupt, or a clean return -- with a traceback
    out of the cleanup."""

    def boom():
        raise RuntimeError("agtermctl vanished")

    ctl = RelayCtl(panes())
    ctl.dashboard_close = boom
    out = io.StringIO()
    assert peer.cmd_relay(ctl, ["alice=AAAA1111", "bob=BBBB2222"], 500, 0,
                          True, out, dashboard=True, fetch=Fetcher()) == 0
    assert "could not close" in out.getvalue()


def test_close_grid_survives_a_say_that_raises(peer):
    """`out` can be closed by the time the `finally` runs."""

    def boom(message):
        raise ValueError("I/O operation on closed file")

    class Broken(object):
        def dashboard_close(self):
            return True, ""

    peer.close_grid(Broken(), boom)


# --------------------------------------------------------------- the skill

SKILL_PATH = os.path.join(REPO_ROOT, "skills", "agb-peer", "SKILL.md")


def test_the_skill_exists_and_has_frontmatter(peer):
    body = io.open(SKILL_PATH, encoding="utf-8").read()
    assert body.startswith("---\n"), "a skill needs YAML frontmatter"
    head = body.split("---", 2)[1]
    assert "name:" in head and "description:" in head


def test_every_flag_the_skill_names_is_a_real_flag(peer):
    """A cross-file agreement with no single source of truth.

    The skill is prose an agent follows literally. A flag renamed in the parser
    and left in the skill sends every agent down a path that exits 1 with
    `unknown option`, and nothing in the suite would notice -- the skill is not
    code and is never executed.
    """
    import re
    body = io.open(SKILL_PATH, encoding="utf-8").read()
    known = set(peer.PEER_FLAGS) | set(peer.PEER_VALUE_ARGS)
    named = set()
    for line in body.splitlines():
        if "agb-peer" not in line:
            continue
        named.update(re.findall(r"--[a-z][a-z-]*", line))
    assert named, "the walk found no flags -- the skill or this parser changed"
    assert named <= known, "the skill names flags agb-peer does not have: %s" % (
        sorted(named - known),)


def test_the_verbs_the_skill_names_are_dispatched(peer):
    body = io.open(SKILL_PATH, encoding="utf-8").read()
    assert "agb-peer send" in body, "the skill must tell an agent how to send"
    # `send` reaching the parser at all is what this proves: an undispatched
    # verb would be read as the message and refused for having no --to.
    with pytest.raises(peer.PeerError):
        peer.main(["send", "hi"], io.StringIO(), None)
    # ⚠️ `who` CANNOT use the same shape. An undispatched `who` raises too, via
    # `--to is required`, so `pytest.raises` alone passes with cmd_who deleted.
    # The distinguishing evidence is the message. See
    # test_who_is_dispatched_by_main for the hermetic version.
    assert "agb-peer who" in body, "the skill must tell an agent how to ask"


def test_the_skill_says_a_relay_answer_needs_no_reply(peer):
    """⚠️ The loop mitigation. `## Receiving` tells the agent to reply to
    anything shaped `[chat from <name>]`, and the answer is shaped exactly like
    that. The relay's token match is the mechanism; this is the instruction."""
    body = " ".join(io.open(SKILL_PATH, encoding="utf-8").read().split())
    assert "is NOT a peer talking to you, and needs no reply" in body


def test_the_skill_says_silence_is_not_an_error(peer):
    """The one way an asynchronous command goes wrong: an agent that retries."""
    body = " ".join(io.open(SKILL_PATH, encoding="utf-8").read().split())
    assert "No answer at all is not an error" in body
    assert "neither is worth retrying" in body


def test_the_skill_extends_the_file_doorbell_rule_to_who(peer):
    """`who` travels the same path as `send`, so on an unreachable machine it
    prints the same marker -- and the relay never sees the question unless the
    agent repeats it. The rule was written about `send` only."""
    body = " ".join(io.open(SKILL_PATH, encoding="utf-8").read().split())
    assert "applies to `agb-peer who` exactly as it does to `send`" in body


def test_the_skill_carries_the_deadlock_rule(peer):
    """agterm's own cookbook calls this out, and it is the failure mode this
    arrangement is most prone to: two agents each waiting on the other."""
    body = io.open(SKILL_PATH, encoding="utf-8").read().lower()
    assert "deadlock" in body
    assert "never poll" in body


def test_the_agent_specific_surface_is_recorded_as_measured(peer):
    """It turned out to be one constant, and the note says so with evidence.

    Pinned as a test because the comment is the only thing standing between the
    next reader and a third glyph added from documentation -- which is how this
    project has been wrong about agterm twice, and about Codex once.
    """
    body = io.open(PEER_PATH, encoding="utf-8").read()
    head = body[:body.index("COMPOSER_GLYPHS = (")]
    assert "measured, not assumed" in head
    for name in ("EMPTY_COLUMN", "submit key", "PASTE_MARKS"):
        assert name in head, name


def test_both_agents_composers_are_recognised(peer):
    """MEASURED: Claude draws `❯`, codex-cli 0.149.1 draws `›`. Everything else
    about reading a pane is shared between them."""
    assert peer.classify("\n❯ \n") == peer.MODE_COMPOSER
    assert peer.classify("\n› Ask Codex to do anything\n") == peer.MODE_COMPOSER
    assert peer.classify("$ ls -l\ntotal 0\n") == peer.MODE_UNKNOWN


def test_the_menu_still_wins_for_either_glyph(peer):
    """`agb pane`'s menu is agent-agnostic, and a detached row must not be read
    as a composer whichever agent is behind it."""
    for glyph in peer.COMPOSER_GLYPHS:
        assert peer.classify("\n%s x\n%s" % (glyph, MENU)) == peer.MODE_MENU


def test_the_skill_has_nothing_to_fill_in(peer):
    """It is installed as a SYMLINK into this repo, like every other skill
    here, so anything an operator is told to edit is a repo modification.

    The first draft had three fill-in lines. This pins that they are gone:
    the command is on $PATH and the names are asked for, not edited in.
    """
    body = io.open(SKILL_PATH, encoding="utf-8").read()
    assert "/path/to/agb-peer" not in body
    assert "<your participant name>" not in body
    # ⚠️ Whitespace-collapsed before matching, and the reason is the joke this
    # project keeps landing on: prose wraps, so `ask the user` was `ask the\n
    # user` and a literal substring check failed on a sentence that was there.
    # The same class of bug as the one the wire format exists to avoid.
    flat = " ".join(body.lower().split())
    assert "ask the user" in flat, \
        "the agent must be told to ASK for a name it was not given"


# ------------------------------------------------- the agtermctl argv contract

def assert_text_argv(argv):
    """What `agtermctl session text` actually refuses, measured 2026-08-24.

    ⚠️ A fake that accepts any argv is not a fake of a real tool. `Ctl.text`
    shipped sending `--all --lines N` together -- which the binary rejects with
    `Error: use either --all or --lines, not both`, and which the recorded
    `--help` does not mention -- so every relay read would have failed on the
    first live tick with 2050 tests green. This is that gap closed.
    """
    assert argv[:2] == ["session", "text"], argv
    assert not ("--all" in argv and "--lines" in argv), \
        "agtermctl refuses --all together with --lines"
    assert "--target" in argv and "--pane" in argv, argv
    pane = argv[argv.index("--pane") + 1]
    # measured: primary/top/split/bottom are in the --help and REJECTED
    assert pane in ("left", "right", "scratch"), pane


class ArgvCtl(object):
    """A `Ctl` over a recording runner, so the real argv is built and checked."""

    def __init__(self):
        self.calls = []

    def run(self, argv):
        self.calls.append(argv)
        return 0, "", ""


def test_every_read_is_the_whole_visible_screen(peer):
    """MEASURED, and reading a tail hid Codex completely: Claude anchors its
    composer to the BOTTOM of the pane, Codex draws from the TOP and leaves
    the bottom blank. `--lines 40` finds Claude every time and missed Codex's
    composer entirely, so `classify` answered `unknown` for a healthy Codex row
    and the relay would never have delivered to it. The doorbell kept working
    throughout, because tmux's status bar is always the last line.

    The tail bought nothing anyway: the alternate screen has no scrollback, so
    `--all`, `--lines 400` and the bare default all return the same lines.
    """
    recorder = ArgvCtl()
    ctl = peer.Ctl(run=recorder.run)
    ctl.text("ROW", "left")
    ctl.text("ROW", "left", 40, whole=True)
    assert recorder.calls, "no read was made"
    for argv in recorder.calls:
        assert_text_argv(argv)
        assert "--lines" not in argv, argv
        assert "--all" not in argv, argv


def test_a_bounded_read_never_asks_for_all_and_lines_together(peer):
    recorder = ArgvCtl()
    ctl = peer.Ctl(run=recorder.run)
    ctl.text("ROW", "left", 400, whole=True)
    ctl.text("ROW", "left", 40)
    assert recorder.calls, "no call was made"
    for argv in recorder.calls:
        assert_text_argv(argv)


def test_the_relay_builds_a_legal_read_for_every_participant(peer):
    """End to end through relay_tick, because the bug was at the seam between
    the relay's `whole=True` and the flag builder -- neither alone was wrong."""
    recorder = ArgvCtl()

    class Wired(peer.Ctl):
        def tree(self):
            return tree_of(session("AAAA1111"), session("BBBB2222"))

        def text(self, target, pane, lines, whole=False):
            peer.Ctl.text(self, target, pane, lines, whole)
            return COMPOSER, None

    ctl = Wired(run=recorder.run, sleep=lambda n: None)
    peer.relay_tick(ctl, PEOPLE, set(), [], 400, lambda m: None,
                    deliver_new=False)
    reads = [a for a in recorder.calls if a[:2] == ["session", "text"]]
    assert len(reads) == 2, reads
    for argv in reads:
        assert_text_argv(argv)


def test_the_relay_has_a_real_fetcher_when_none_is_injected(peer, monkeypatch):
    """The seam nothing exercised: `fetch=None` is what production passes, and
    every other test hands in a fake. The first live relay died on it."""
    calls = []

    def fake_local(argv):
        calls.append(argv)
        return 0, "", ""

    monkeypatch.setattr(peer, "run_local", fake_local)
    ctl = RelayCtl(panes(alice=bell("aaa")))
    peer.relay_tick(ctl, PEOPLE, {}, [], 500, ctl.say)   # no fetch=
    assert calls, "drain must fall back to the real runner, not None"


def test_drain_with_no_fetcher_does_not_raise(peer, monkeypatch):
    monkeypatch.setattr(peer, "run_local", lambda argv: (0, "", ""))
    assert peer.drain(None, "alice", "local", "%7", lambda m: None) == []


def test_a_message_is_never_delivered_twice(peer):
    """An option the far side would not let us unset comes back on the next
    ring. Fetching it twice is harmless; DELIVERING it twice would put the same
    sentence in a composer again.
    """
    ctl = RelayCtl(panes(alice=bell("aaa")))
    fetch = Fetcher(framed(("msg1", "bob", "only once")), unset_rc=1)
    seen, pending, notes = {}, [], {}
    peer.relay_tick(ctl, PEOPLE, seen, pending, 500, ctl.say, fetch, notes=notes)
    ctl.current["AAAA1111"] = COMPOSER + bell("bbb")          # a second ring
    peer.relay_tick(ctl, PEOPLE, seen, pending, 500, ctl.say, fetch, notes=notes)
    listings = [a for a in fetch.calls if "show-options" in a]
    assert len(listings) == 2, "both rings must have fetched"
    bodies = [t for (_, _, t) in ctl.typed if t not in ("\n", "\r")]
    assert bodies == ["[chat from alice] only once"], bodies


def test_a_mac_native_participant_names_its_tmux_session(peer):
    """An agterm session that is NOT an agbridge row has no `agb pane` argv, so
    its tmux pane id cannot be read out of `foreground` -- its foreground is a
    shell. Without an explicit target, `@local` could never work at all.
    """
    got = peer.parse_participants(["a=macbot@local:macbot", "b=R2"])
    assert got["a"] == ("macbot", "left", "local", "macbot")


def test_an_explicit_tmux_target_beats_the_rows_argv(peer):
    ctl = RelayCtl(panes(alice=bell("aaa")))
    fetch = Fetcher(framed(("aaa", "bob", "hi")))
    people = {"alice": ("AAAA1111", "left", "local", "macbot"),
              "bob": ("BBBB2222", "left", "local", None)}
    peer.relay_tick(ctl, people, {}, [], 500, ctl.say, fetch)
    listing = [a for a in fetch.calls if "show-options" in a][0]
    assert "macbot" in listing, listing
    assert "%7" not in listing, "the explicit target must win over foreground"


# ------------------------------------------------- finding the tmux binary

def test_agb_tmux_overrides_everything(peer):
    assert peer.tmux_binary({"AGB_TMUX": "/somewhere/tmux"}) == "/somewhere/tmux"


def test_a_bare_name_is_the_last_resort(peer, monkeypatch):
    monkeypatch.setattr(peer.os, "access", lambda p, m: False)
    assert peer.tmux_binary({}) == "tmux"


def test_a_usual_home_is_used_when_PATH_is_stripped(peer, monkeypatch):
    """agterm spawns `bash --noprofile --norc`, so a pane inherits only what
    `login` gives it -- NOT the user's PATH. Measured: an agent inside tmux
    inside agterm has $TMUX_PANE set and still cannot exec `tmux`."""
    monkeypatch.setattr(peer.os, "access",
                        lambda p, m: p == "/opt/homebrew/bin/tmux")
    assert peer.tmux_binary({}) == "/opt/homebrew/bin/tmux"


def test_PATH_beats_the_hardcoded_homes(peer, monkeypatch):
    """MEASURED, and the order matters more than it looks: on a cluster host
    `/usr/bin/tmux` is **2.7** while the running server is **3.5a** from a PATH
    directory. The old binary has no `show-options -p`, cannot speak to a 3.5a
    server, and BLOCKS trying to start its own -- which reads as "tmux is
    wedged". Preferring the candidates broke the farm while fixing the Mac.
    """
    monkeypatch.setattr(peer.os, "access", lambda p, m: p in (
        "/shared/tools/tmux-3.5a/bin/tmux", "/usr/bin/tmux"))
    got = peer.tmux_binary({"PATH": "/shared/tools/tmux-3.5a/bin:/usr/bin"})
    assert got == "/shared/tools/tmux-3.5a/bin/tmux", got


def test_the_first_PATH_entry_wins(peer, monkeypatch):
    monkeypatch.setattr(peer.os, "access", lambda p, m: True)
    assert peer.tmux_binary({"PATH": "/a:/b"}) == "/a/tmux"


def test_send_uses_the_resolved_binary(peer, monkeypatch):
    monkeypatch.setattr(peer.os, "access",
                        lambda p, m: p == "/opt/homebrew/bin/tmux")
    run = LocalRun()
    peer.cmd_send("bob", "hi", run, io.StringIO(), env={"TMUX_PANE": "%7"})
    assert run.calls and all(c[0] == "/opt/homebrew/bin/tmux" for c in run.calls), \
        run.calls


def test_only_the_local_branch_resolves_a_path(peer, monkeypatch):
    """Over ssh the remote login shell finds its own tmux, and a Mac path would
    be nonsense there."""
    monkeypatch.setattr(peer.os, "access",
                        lambda p, m: p == "/opt/homebrew/bin/tmux")
    assert peer.ssh_argv("local", ["tmux", "ls"], {})[0] == "/opt/homebrew/bin/tmux"
    assert peer.ssh_argv("farmbox", ["tmux", "ls"], {})[-2:] == ["tmux", "ls"]


# ------------------------------------------------------- subprocess timeouts

def test_a_wedged_command_times_out_instead_of_hanging(peer):
    """MEASURED: `tmux display-message -t %N` blocked indefinitely while an
    agterm dashboard had a wedged view-only client -- `list-clients` answered
    and everything that must NOTIFY a client did not. `send` hung for two
    minutes with nothing written. This repo's rule about `communicate(timeout=)`
    exists for exactly that, and this file broke it in two places.
    """
    rc, out, err = peer.run_local(
        ["sh", "-c", "sleep 30"], timeout=1)
    assert rc == peer.TIMED_OUT
    assert "did not answer" in err


def test_a_normal_command_is_unaffected(peer):
    # The companion the timeout test needs: same call, one variable changed.
    rc, out, err = peer.run_local(["sh", "-c", "printf hello"], timeout=10)
    assert (rc, out) == (0, "hello")


def test_both_runners_share_the_timing_out_spawner(peer):
    import ast
    tree = ast.parse(io.open(PEER_PATH, encoding="utf-8").read())
    for name in ("run_local", "run_ctl"):
        fn = [n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == name]
        assert fn, name
        body = ast.dump(fn[0])
        assert "_spawn" in body, "%s must not start its own Popen" % (name,)


def test_a_failed_tmux_read_refuses_instead_of_guessing(peer):
    """MEASURED: one transient tmux stall made `send` fall back to a hardcoded
    base name and then STORE it, permanently renaming the pane's doorbell base
    to a guess. Converting "could not read" into data is the failure this
    project writes invariants against."""

    class Failing(LocalRun):
        def __call__(self, argv):
            self.calls.append(argv)
            return 124, "", "did not answer"

    run = Failing()
    with pytest.raises(peer.PeerError):
        peer.cmd_send("bob", "hi", run, io.StringIO(), env={"TMUX_PANE": "%7"})
    assert not any(c[1] == "set" for c in run.calls), \
        "nothing may be written after a failed read"


def test_a_failed_window_name_read_refuses_too(peer):
    """The SECOND guard, which the test above cannot reach.

    That one's fake fails every call, so the options read raises first and the
    window-name fallback is never exercised — the mutation that restored the
    old guess-and-store survived it. Here the options read SUCCEEDS with an
    empty value (a pane that has no base stored yet, the normal first-send
    case) and only the window-name read fails.
    """

    class EmptyThenFailing(LocalRun):
        def __call__(self, argv):
            self.calls.append(argv)
            verb = argv[1] if len(argv) > 1 else ""
            if verb == "show-options":
                return 0, "", ""          # no base stored yet: a real answer
            if verb == "display-message":
                return 124, "", "did not answer"
            return 0, "", ""

    run = EmptyThenFailing()
    with pytest.raises(peer.PeerError):
        peer.cmd_send("bob", "hi", run, io.StringIO(), env={"TMUX_PANE": "%7"})
    assert not any(c[1] == "set" for c in run.calls), \
        "a guessed base must never be stored"


def test_a_pane_with_no_window_name_still_works(peer):
    """The companion: a SUCCESSFUL read of an empty name is a different thing
    from a failed read, and must not refuse."""

    run = LocalRun(window="", base="")
    peer.cmd_send("bob", "hi", run, io.StringIO(), env={"TMUX_PANE": "%7"})
    stored = [c for c in run.calls if c[1] == "set" and peer.OPTION_BASE in c]
    assert stored and stored[0][-1] == "agent", stored


def test_a_pasted_message_still_gets_its_return(peer):
    """MEASURED: Claude Code collapses a long, fast injection into
    `❯ [Pasted text #1]`, so the body is not on screen to verify against.
    Return submits it fine -- the verification was refusing to press it, which
    is why every long message needed a human. agterm's own cookbook checks for
    the paste indicator for exactly this reason.
    """
    body = "[chat from alice] " + ("x" * 900)

    class Pasting(RelayCtl):
        def type(self, target, pane, text):
            self.typed.append((target, pane, text))
            if text != "\n":
                self.current[target] = COMPOSER + "❯ [Pasted text #1]"
            return True

    ctl = Pasting(panes())
    peer.deliver(ctl, session(), "left", body, True, 500, lambda m: None)
    assert ctl.typed[-1][2] == "\r", "a pasted message must still be submitted"


class SlowRender(RelayCtl):
    """A pane that only finishes rendering after `after` reads.

    ⚠️ MEASURED 2026-08-26: a ~2.8 KB delivery to a Codex row was still
    rendering one second after it was typed -- the screen held two paste
    placeholders and the tail had not arrived yet. `type` deliberately does NOT
    change the pane here; the reads do.
    """

    def __init__(self, panes, after, tail):
        RelayCtl.__init__(self, panes)
        self.after, self.tail, self.reads = after, tail, 0

    def type(self, target, pane, text):
        self.typed.append((target, pane, text))
        return True

    def text(self, target, pane, lines, whole=False):
        self.reads += 1
        # ⚠️ So an UNBOUNDED wait fails by name instead of hanging the suite.
        # Mutating the loop to `while True` otherwise wedges pytest, and a hang
        # is not a mutation result anyone can read.
        assert self.reads < 50, "deliver() is reading without a bound"
        if self.reads > self.after:
            return self.current.get(target, "") + self.tail, None
        return self.current.get(target, ""), None


def test_a_slow_render_is_waited_out(peer):
    """⚠️ The read used to happen once, one second after typing, and a pane that
    had not finished rendering was indistinguishable from a swallowed message.
    That answer is expensive: it is exit 4, which `try_deliver` DROPS rather
    than retries, so a slow render cost the whole message."""
    body = "[chat from alice] " + ("x" * 900)
    # The first read is the `before` baseline, so the body lands on read 4.
    ctl = SlowRender(panes(), 3, "\n" + body + "\n")
    peer.deliver(ctl, session(), "left", body, True, 500, lambda m: None)
    assert ctl.typed[-1][2] == "\r", "a slow render must still be submitted"


def test_a_render_that_never_arrives_is_still_refused(peer):
    """The companion: waiting longer must not become waiting for ever, and the
    refusal must still be the exit status `try_deliver` drops on."""
    body = "[chat from alice] " + ("x" * 900)
    ctl = SlowRender(panes(), 99, "never")
    with pytest.raises(peer.PeerError) as caught:
        peer.deliver(ctl, session(), "left", body, True, 500, lambda m: None)
    assert caught.value.code == 4
    assert [x[2] for x in ctl.typed] == [body], "Return must not be pressed"
    assert ctl.sleeps == peer.VERIFY_READS, \
        "it must be bounded, not open-ended: %d" % (ctl.sleeps,)


def test_a_prompt_render_costs_one_read(peer):
    """⚠️ A relay must not block, so the retries may not become latency on the
    ordinary path. Everything that renders promptly still pays exactly one
    second -- the same as before this loop existed."""
    body = "[chat from alice] hello"
    ctl = RelayCtl(panes())
    peer.deliver(ctl, session(), "left", body, True, 500, lambda m: None)
    assert ctl.sleeps == 1, "the fast path must not have got slower"


def test_a_codex_paste_placeholder_still_gets_its_return(peer):
    """MEASURED 2026-08-26 on a live Codex row: a 1461-character delivery
    collapsed to `› [Pasted Content 1461 chars]`.

    ⚠️ The companion above is the SAME test for Claude, and the pair is the
    point: the block at the top of `agb-peer` had recorded (at ~900 characters)
    that Codex renders a long injection in full, concluded that a Claude-shaped
    mark was a harmless no-op there, and so shipped a `deliver` that found
    neither the body nor a mark. Exit 4 is dropped rather than retried, so the
    message sat in the composer for a human to submit.
    """
    body = "[chat from alice] " + ("x" * 1443)

    class Pasting(RelayCtl):
        def type(self, target, pane, text):
            self.typed.append((target, pane, text))
            if text != "\n":
                self.current[target] = (
                    "\n\u203a [Pasted Content 1461 chars]\n[host:codex 14:05]\n")
            return True

    ctl = Pasting(panes())
    peer.deliver(ctl, session(), "left", body, True, 500, lambda m: None)
    assert ctl.typed[-1][2] == "\r", "a pasted message must still be submitted"


def test_a_stale_codex_placeholder_is_not_evidence_either(peer):
    """The Codex half of the stale-placeholder guard. Without it the mark could
    be added and the count comparison dropped, and every failed delivery to a
    Codex that had ever received a long message would read as a success."""
    body = "[chat from alice] " + ("x" * 1443)
    stale = "\n\u203a [Pasted Content 900 chars]\n[host:codex 14:05]\n"

    class Swallowing(RelayCtl):
        def type(self, target, pane, text):
            self.typed.append((target, pane, text))
            return True

    ctl = Swallowing({"AAAA1111": stale, "BBBB2222": COMPOSER})
    with pytest.raises(peer.PeerError) as caught:
        peer.deliver(ctl, session(), "left", body, True, 500, lambda m: None)
    assert caught.value.code == 4
    assert [x[2] for x in ctl.typed] == [body], \
        "it must not press Return on a placeholder that was already there"


def test_the_placeholder_is_matched_whatever_its_case(peer):
    """⚠️ `cat -A` on the live pane read `[Pasted Content 1461 chars]`; the
    operator watching that same row read `[Pasted content ...]`. Two readings of
    one screen disagreeing on one letter -- the same one-sample problem as the
    mark itself, one level down. Case distinguishes nothing here, so betting on
    it buys nothing and can only lose a message."""
    body = "[chat from alice] " + ("x" * 1443)

    for rendering in ("[Pasted Content 1461 chars]",
                      "[Pasted content 1461 chars]",
                      "[PASTED CONTENT 1461 CHARS]",
                      "[Pasted Text #1]"):

        class Pasting(RelayCtl):
            mark = rendering

            def type(self, target, pane, text):
                self.typed.append((target, pane, text))
                if text != "\n":
                    self.current[target] = (
                        "\n\u203a " + self.mark + "\n[host:codex 14:05]\n")
                return True

        ctl = Pasting(panes())
        peer.deliver(ctl, session(), "left", body, True, 500, lambda m: None)
        assert ctl.typed[-1][2] == "\r", rendering


def test_each_mark_is_counted_on_its_own(peer):
    """⚠️ Not a total. A total can stay level while one placeholder appears and
    another is cleared, and the delivery would then read as swallowed. The two
    spellings are independent evidence, so they are compared independently."""
    body = "[chat from alice] " + ("x" * 1443)

    class Swapping(RelayCtl):
        def type(self, target, pane, text):
            self.typed.append((target, pane, text))
            if text != "\n":
                self.current[target] = (
                    "\n\u203a [Pasted Content 1461 chars]\n[host:codex 14:05]\n")
            return True

    # Before: a Claude-shaped mark. After: a Codex-shaped one, and no Claude
    # one. Total placeholders: 1 -> 1.
    ctl = Swapping({"AAAA1111": COMPOSER + "\u276f [Pasted text #1]\n",
                    "BBBB2222": COMPOSER})
    peer.deliver(ctl, session(), "left", body, True, 500, lambda m: None)
    assert ctl.typed[-1][2] == "\r", "the new mark is evidence on its own"


def test_a_stale_paste_placeholder_is_not_evidence(peer):
    """It has to be a placeholder that WASN'T there before. Otherwise one
    earlier long message makes every later failure look like a success."""
    body = "[chat from alice] " + ("x" * 900)

    class Swallowing(RelayCtl):
        """Types and NOTHING appears -- the real failure this gate is for."""

        def type(self, target, pane, text):
            self.typed.append((target, pane, text))
            return True

    ctl = Swallowing({"AAAA1111": COMPOSER + "❯ [Pasted text #1]",
                      "BBBB2222": COMPOSER})
    with pytest.raises(peer.PeerError) as caught:
        peer.deliver(ctl, session(), "left", body, True, 500, lambda m: None)
    assert caught.value.code == 4
    assert [x[2] for x in ctl.typed] == [body], \
        "it must not press Return on a placeholder that was already there"


def test_a_held_message_says_its_reason_once(peer):
    """A relay retries every tick, and a peer whose composer has a draft in it
    stays that way until a human looks -- which produced the identical line
    every eight seconds for as long as nobody did."""
    ctl = RelayCtl(panes(alice=bell("aaa")), cursors=[41])
    fetch = Fetcher(framed(("aaa", "bob", "held")))
    seen, pending, notes = {}, [], {}
    for _ in range(5):
        peer.relay_tick(ctl, PEOPLE, seen, pending, 500, ctl.say, fetch,
                        notes=notes)
    holds = [s for s in ctl.said if "composer is not empty" in s]
    assert len(holds) == 1, holds


def test_the_reason_is_said_again_after_a_delivery(peer):
    """Once is not never: a new hold after a success is news again."""
    ctl = RelayCtl(panes(alice=bell("aaa")), cursors=[41, 2, 41])
    fetch = Fetcher(framed(("aaa", "bob", "held")))
    seen, pending, notes = {}, [], {}
    peer.relay_tick(ctl, PEOPLE, seen, pending, 500, ctl.say, fetch, notes=notes)
    peer.relay_tick(ctl, PEOPLE, seen, pending, 500, ctl.say, fetch, notes=notes)
    ctl.current["AAAA1111"] = COMPOSER + bell("bbb")
    peer.relay_tick(ctl, PEOPLE, seen, pending, 500, ctl.say, fetch, notes=notes)
    assert ("said", "bob") not in notes


def test_the_relays_refusal_does_not_mention_zero_checks(peer):
    ctl = RelayCtl(panes(), cursors=[41])
    with pytest.raises(peer.PeerError) as caught:
        peer.wait_ready(ctl, session(), "left", 0, 0, False, 40, lambda m: None)
    assert "0 checks" not in str(caught.value)


def test_a_wrapped_message_still_gets_its_return(peer):
    """The second way verification fails, and the one that survived the paste
    fix: the body IS on screen, but agterm wrapped it, so a 40-character tail
    probe straddles the break and matches nothing. Every long message that was
    typed rather than pasted needed a human to press Enter.
    """
    body = "[chat from alice] " + " ".join("word%d" % i for i in range(40))

    class Wrapping(RelayCtl):
        def type(self, target, pane, text):
            self.typed.append((target, pane, text))
            if text not in ("\n", "\r"):
                wrapped = "\n".join(text[i:i + 24] for i in range(0, len(text), 24))
                self.current[target] = COMPOSER + wrapped
            return True

    ctl = Wrapping(panes())
    peer.deliver(ctl, session(), "left", body, True, 500, lambda m: None)
    assert ctl.typed[-1][2] == "\r", "a wrapped message must still be submitted"


def test_text_that_truly_never_arrived_is_still_refused(peer):
    """The companion: stripping whitespace must not turn the gate off."""
    class Swallowing(RelayCtl):
        def type(self, target, pane, text):
            self.typed.append((target, pane, text))
            return True

    ctl = Swallowing(panes())
    with pytest.raises(peer.PeerError) as caught:
        peer.deliver(ctl, session(), "left", "[chat from alice] hello there",
                     True, 500, lambda m: None)
    assert caught.value.code == 4


# =========================================================================
# the NFS fallback -- an agent whose tmux is on another machine
# =========================================================================

def test_the_socket_check_is_a_positive_signal(peer, tmp_path):
    """Three failures reach the same rc != 0 and only one means "use files".

    MEASURED, all three: a sandbox answers `Operation not permitted` with the
    socket right there; a wedged agterm client answers with a timeout; and a
    pool machine answers `No such file or directory` because `$TMUX` was
    inherited through a job submission and the socket is on another machine's
    /tmp. Only the last may fall back, and it is checked directly rather than
    inferred from an error string.
    """
    live = tmp_path / "sock"
    live.write_text("")
    assert peer.socket_is_missing({"TMUX": str(live) + ",1,0"}) is False
    assert peer.socket_is_missing({"TMUX": "/no/such/sock,1,0"}) is True


def test_no_TMUX_at_all_is_not_the_pool_case(peer):
    """We cannot tell, so the caller refuses -- the answer it gave before any
    of this existed."""
    assert peer.socket_is_missing({}) is False


def test_a_missing_socket_writes_a_file_and_echoes_a_doorbell(peer, tmp_path):
    run = LocalRun()
    run.__class__ = type("Failing", (LocalRun,), {
        "__call__": lambda self, argv: (self.calls.append(argv), (1, "", "No such file or directory"))[1]})
    out = io.StringIO()
    env = {"TMUX_PANE": "%99", "TMUX": "/no/such/sock,1,99",
           "AGB_STATEDIR": str(tmp_path)}
    peer.cmd_send("bob", "hello from the pool", run, out, now=1.0, env=env)
    body = out.getvalue()
    assert "via file" in body
    assert peer.read_doorbell(body), "the doorbell must be echoed to the screen"
    written = list((tmp_path / "chat").iterdir())
    assert len(written) == 1, written
    assert written[0].read_text() == "bob\nhello from the pool"


def test_the_fallback_is_announced_not_silent(peer, tmp_path):
    """A fallback nobody is told about is how a message quietly stops
    arriving."""
    run = type("Failing", (LocalRun,), {
        "__call__": lambda self, argv: (self.calls.append(argv), (1, "", "No such file or directory"))[1]})()
    out = io.StringIO()
    peer.cmd_send("bob", "hi", run, out, now=1.0,
                  env={"TMUX_PANE": "%99", "TMUX": "/no/such/sock,1,99",
                       "AGB_STATEDIR": str(tmp_path)})
    assert "tmux is unreachable" in out.getvalue()
    assert str(tmp_path) in out.getvalue(), "it must say where it put the file"


def test_the_file_is_written_temp_then_renamed(peer, tmp_path):
    """A torn read is a real possibility on NFS."""
    import ast
    tree = ast.parse(io.open(PEER_PATH, encoding="utf-8").read())
    fn = [n for n in ast.walk(tree)
          if isinstance(n, ast.FunctionDef) and n.name == "write_chat_file"]
    assert fn, "write_chat_file is gone"
    # ⚠️ An actual CALL, not a substring of the AST dump. The first version of
    # this guard checked `"rename" in ast.dump(fn)` and passed against the
    # DOCSTRING, which says "temp+rename" -- a structural test matching its own
    # explanation, which is the failure CLAUDE.md warns about and the third of
    # its kind today.
    calls = [n for n in ast.walk(fn[0]) if isinstance(n, ast.Call)]
    names = [n.func.attr for n in calls if isinstance(n.func, ast.Attribute)]
    assert "rename" in names, "it must temp+rename, not write in place: %s" % (
        names,)


# ------------------------------------------------------------ the file drain

def test_a_file_participant_is_read_over_ssh_to_a_REACHABLE_host(peer):
    """Never to the machine the agent is on -- that is the whole point."""
    fetch = Fetcher()
    fetch.output = "bob\nhello from the pool"
    got = peer.drain(fetch, "pool", "container", peer.NFS_TARGET, lambda m: None,
                     ident="k3n9x2", chat_dir="/home/user/.agbridge/chat")
    assert got == [{"id": "k3n9x2", "key": "k3n9x2", "to": "bob",
                    "text": "hello from the pool"}]
    assert fetch.calls[0][0] == "ssh" and "container" in fetch.calls[0]
    assert "cat" in fetch.calls[0]


def test_the_file_drain_names_exactly_one_file(peer):
    """The doorbell carries the id, so no listing and no globbing -- which also
    keeps it argv-only, because `ssh <host> <cmd>` hands the command to the
    remote LOGIN shell and a farm login shell is often tcsh."""
    argv = peer.file_argv("/s/chat", "abc12")
    assert argv == ["cat", "/s/chat/abc12.msg"]
    for word in argv:
        assert not any(c in word for c in "$();|&<>*?"), word


def test_the_file_is_removed_after_it_is_read(peer):
    fetch = Fetcher()
    fetch.output = "bob\nhi"
    peer.drain(fetch, "pool", "container", peer.NFS_TARGET, lambda m: None,
               ident="abc12", chat_dir="/s/chat")
    assert any("rm" in a for a in fetch.calls), fetch.calls


def test_no_chat_dir_is_reported_rather_than_guessed(peer):
    """The statedir is not under $HOME here -- measured, $HOME is
    /home/exampleuser while the statedir is /home/user/.agbridge -- so `~` would
    expand to the wrong place on the far side."""
    said = []
    assert peer.drain(Fetcher(), "pool", "container", peer.NFS_TARGET,
                      said.append, ident="abc12", chat_dir=None) == []
    assert any("--chat-dir" in s for s in said)


# ---------------------------------------------------------------------------
# membership is the roster, not what resolved
# ---------------------------------------------------------------------------
#
# `people` is the RESOLVED map -- names whose row is in agterm's tree right now.
# Using it to answer "is this a participant?" silently discarded every message
# addressed to a participant whose agent had not started yet, with the sender
# seeing `queued for <name>` and exit 0. A fixed participant list masks it (you
# start the relay once the agents exist); a roster does not.

ROSTER = {"alice", "bob", "carol"}
CAROL = ("CCCC3333", "left", "local", None)


def queued(to, text="hi", ident="m1", sender="alice"):
    return [(sender, {"id": ident, "to": to, "text": text})]


def test_a_message_to_a_roster_member_with_no_row_is_held(peer):
    ctl = RelayCtl(panes())
    pending = queued("carol")
    peer.relay_tick(ctl, PEOPLE, {}, pending, 500, ctl.say, Fetcher(),
                    notes={}, members=ROSTER)
    assert len(pending) == 1, "held means still queued, not delivered or dropped"
    assert ctl.typed == []
    assert any("no row yet" in s for s in ctl.said), ctl.said


def test_a_held_message_is_delivered_once_the_row_appears(peer):
    """The companion. Without it, a hold is only a slower drop and the test
    above would pass against a relay that never delivers anything."""
    notes, pending = {}, queued("carol")
    first = RelayCtl(panes())
    peer.relay_tick(first, PEOPLE, {}, pending, 500, first.say, Fetcher(),
                    notes=notes, members=ROSTER)
    assert len(pending) == 1, "precondition: it was held"

    surfaces = panes()
    surfaces[CAROL[0]] = COMPOSER
    later = RelayCtl(surfaces)
    peer.relay_tick(later, dict(PEOPLE, carol=CAROL), {}, pending, 500,
                    later.say, Fetcher(), notes=notes, members=ROSTER)
    assert pending == [], "delivered, so no longer queued"
    assert [t for (_, _, t) in later.typed if t not in ("\n", "\r")] == [
        "[chat from alice] hi"]


def test_a_message_to_a_name_outside_the_roster_is_still_dropped(peer):
    """The other half of the distinction: not in the roster is a typo or a name
    nobody added, and holding those for ever would be the opposite mistake."""
    ctl = RelayCtl(panes())
    pending = queued("nobody")
    peer.relay_tick(ctl, PEOPLE, {}, pending, 500, ctl.say, Fetcher(),
                    notes={}, members=ROSTER)
    assert pending == [], "dropped"
    assert any("not a participant" in s for s in ctl.said), ctl.said


def test_without_members_the_resolved_map_still_answers(peer):
    """`members=None` keeps every existing caller -- and the direct one-shot
    path, which has no roster at all -- on the old behaviour."""
    ctl = RelayCtl(panes())
    pending = queued("carol")
    peer.relay_tick(ctl, PEOPLE, {}, pending, 500, ctl.say, Fetcher(),
                    notes={})
    assert pending == [], "no roster to consult, so carol is not a participant"
    assert any("not a participant" in s for s in ctl.said), ctl.said


def test_the_hold_complaint_is_throttled(peer):
    ctl = RelayCtl(panes())
    notes, pending = {}, queued("carol")
    for _ in range(100):
        peer.relay_tick(ctl, PEOPLE, {}, pending, 500, ctl.say, Fetcher(),
                        notes=notes, members=ROSTER)
    holds = [s for s in ctl.said if "no row yet" in s]
    assert len(pending) == 1, "still held at 100 ticks, well inside the bound"
    assert holds, "non-vacuous: it complained at least once"
    assert len(holds) <= 6, "100 ticks, a ladder, not a line every tick: %r" % (
        holds,)


def test_the_hold_ages_by_ticks_not_by_messages(peer):
    """Three messages for one absent name must not age the hold three times as
    fast -- the bound would then depend on how much mail is queued."""
    ctl = RelayCtl(panes())
    notes = {}
    pending = (queued("carol", ident="m1") + queued("carol", ident="m2")
               + queued("carol", ident="m3"))
    peer.relay_tick(ctl, PEOPLE, {}, pending, 500, ctl.say, Fetcher(),
                    notes=notes, members=ROSTER)
    assert len(pending) == 3, "all three held"
    count, _tick = notes[("held", "carol")]
    assert count == 1, "one tick is one tick, whatever the queue depth"
    assert len([s for s in ctl.said if "no row yet" in s]) == 1, ctl.said


def test_the_hold_is_bounded_and_says_so_when_it_gives_up(peer):
    """Unbounded, a name that never resolves accumulates mail for the life of
    the relay with only the ladder to say so."""
    ctl = RelayCtl(panes())
    notes, pending = {}, queued("carol")
    for _ in range(peer.HOLD_TICKS_MAX + 1):
        peer.relay_tick(ctl, PEOPLE, {}, pending, 500, ctl.say, Fetcher(),
                        notes=notes, members=ROSTER)
    assert pending == [], "the bound expired, so it was dropped"
    assert any("giving up" in s for s in ctl.said), ctl.said


def test_every_message_for_a_given_up_name_goes_in_the_same_tick(peer):
    """The note is deliberately NOT popped when the bound expires: popping it
    restarts the count at 1 for the second message and holds it another half
    hour."""
    ctl = RelayCtl(panes())
    notes = {}
    pending = (queued("carol", ident="m1") + queued("carol", ident="m2")
               + queued("carol", ident="m3"))
    for _ in range(peer.HOLD_TICKS_MAX + 1):
        peer.relay_tick(ctl, PEOPLE, {}, pending, 500, ctl.say, Fetcher(),
                        notes=notes, members=ROSTER)
    assert pending == [], "all three, not just the first"
    assert len([s for s in ctl.said if "giving up" in s]) == 1, \
        "said once for the name, not once per message"


# ---------------------------------------------------------------------------
# a drain says whether it FETCHED, not just what it found
# ---------------------------------------------------------------------------
#
# `[]` means two different things -- "the fetch failed" and "the fetch worked
# and nothing was pending" -- and priming a joiner treats them oppositely. A
# failure leaves us still not knowing what is on that pane; an empty success
# says there is nothing stale to discard.


def test_a_successful_empty_drain_is_not_a_failure(peer):
    outcome = {}
    assert peer.drain(Fetcher(), "alice", "farmbox", "%7", lambda m: None,
                      outcome=outcome) == []
    assert outcome["fetch"] == peer.FETCH_OK


def test_a_failed_drain_says_so(peer):
    """The companion, and the one that matters: same [] return, opposite fact."""
    outcome = {}
    assert peer.drain(Fetcher(rc=1), "alice", "farmbox", "%7", lambda m: None,
                      outcome=outcome) == []
    assert outcome["fetch"] == peer.FETCH_FAILED


def test_a_drain_that_found_messages_is_ok(peer):
    outcome = {}
    got = peer.drain(Fetcher(framed(("aaa", "bob", "hello"))), "alice",
                     "farmbox", "%7", lambda m: None, outcome=outcome)
    assert len(got) == 1, "non-vacuous: it really did find one"
    assert outcome["fetch"] == peer.FETCH_OK


def test_a_missing_pane_is_a_failed_fetch(peer):
    outcome = {}
    assert peer.drain(Fetcher(), "alice", "farmbox", "", lambda m: None,
                      outcome=outcome) == []
    assert outcome["fetch"] == peer.FETCH_FAILED


def test_the_file_transport_reports_its_outcome_too(peer):
    outcome = {}
    got = peer.drain(Fetcher(framed(("aaa", "bob", "pooled"))), "pool",
                     "container", peer.NFS_TARGET, lambda m: None,
                     ident="aaa", chat_dir="/s/chat", outcome=outcome)
    assert len(got) == 1, "non-vacuous"
    assert outcome["fetch"] == peer.FETCH_OK
    failed = {}
    assert peer.drain(Fetcher(), "pool", "container", peer.NFS_TARGET,
                      lambda m: None, ident="aaa", chat_dir=None,
                      outcome=failed) == []
    assert failed["fetch"] == peer.FETCH_FAILED


def test_a_caller_that_asks_for_no_outcome_is_unchanged(peer):
    """The seam must stay invisible: seven tests assert on drain's return."""
    said = []
    assert peer.drain(Fetcher(rc=1), "alice", "farmbox", "%7",
                      said.append) == []
    assert any("fetch failed" in s for s in said), said


# ---------------------------------------------------------------------------
# `seen` records what was READ, so a failed fetch retries
# ---------------------------------------------------------------------------
#
# It used to be written one line ABOVE the fetch, so a fetch that failed left
# the participant marked caught-up having read nothing: the doorbell guard
# short-circuited every later tick and those messages were lost until the agent
# happened to send again and moved the doorbell.


class FlakyFetcher(Fetcher):
    """Fails its first N listings, then behaves. Anything else would test a
    permanent outage, where "retried" and "never retried" look identical."""

    def __init__(self, output="", fail_first=1):
        Fetcher.__init__(self, output)
        self.left = fail_first

    def __call__(self, argv):
        if ("show-options" in argv or "cat" in argv) and self.left:
            self.left -= 1
            self.calls.append(argv)
            return 1, "", "ssh: connection refused"
        return Fetcher.__call__(self, argv)


def test_a_failed_fetch_is_retried_on_the_next_tick(peer):
    ctl = RelayCtl(panes(alice=bell("aaa")))
    fetch = FlakyFetcher(framed(("aaa", "bob", "hello")), fail_first=1)
    seen, pending, notes = {}, [], {}
    peer.relay_tick(ctl, PEOPLE, seen, pending, 500, ctl.say, fetch,
                    notes=notes)
    assert ctl.typed == [], "precondition: the first fetch failed"
    assert seen == {}, "nothing was read, so nothing may be recorded as read"

    peer.relay_tick(ctl, PEOPLE, seen, pending, 500, ctl.say, fetch,
                    notes=notes)
    # ⚠️ Asserted on what was TYPED, not on `pending`: a tick that fetches also
    # delivers, so a delivered message leaves `pending` empty -- the same value
    # a lost one leaves.
    assert [t for (_, _, t) in ctl.typed if t not in ("\n", "\r")] == [
        "[chat from alice] hello"], \
        "the doorbell never moved; only the retry can have found this"


def test_a_successful_fetch_is_not_re_drained(peer):
    """The companion. Without it the test above passes against a relay that
    re-fetches every tick for ever, which is what the doorbell exists to stop."""
    ctl = RelayCtl(panes(alice=bell("aaa")))
    fetch = Fetcher(framed(("aaa", "bob", "hello")))
    seen, pending, notes = {}, [], {}
    for _ in range(4):
        peer.relay_tick(ctl, PEOPLE, seen, pending, 500, ctl.say, fetch,
                        notes=notes)
    listings = [a for a in fetch.calls if "show-options" in a]
    assert len(listings) == 1, "four ticks, one ring, one listing"
    assert seen == {"alice": "aaa"}


def test_a_persistent_fetch_failure_says_it_once(peer):
    ctl = RelayCtl(panes(alice=bell("aaa")))
    fetch = Fetcher(framed(("aaa", "bob", "hello")), rc=1)
    seen, pending, notes = {}, [], {}
    for _ in range(20):
        peer.relay_tick(ctl, PEOPLE, seen, pending, 500, ctl.say, fetch,
                        notes=notes)
    complaints = [s for s in ctl.said if "fetch failed" in s]
    assert len([a for a in fetch.calls if "show-options" in a]) == 20, \
        "non-vacuous: it really did retry every tick"
    assert len(complaints) == 1, "20 retries, one line: %r" % (complaints,)


def test_a_healthy_participant_never_complains(peer):
    """The companion to the throttle: a test counting lines needs a case where
    the count is zero, or it passes against a relay that never says anything."""
    ctl = RelayCtl(panes(alice=bell("aaa")))
    seen, pending, notes = {}, [], {}
    for _ in range(20):
        peer.relay_tick(ctl, PEOPLE, seen, pending, 500, ctl.say,
                        Fetcher(framed(("aaa", "bob", "hi"))), notes=notes)
    assert [s for s in ctl.said if "fetch failed" in s] == []


def test_the_complaint_returns_after_a_recovery(peer):
    """Said once per unchanged reason, not once per relay: a failure that
    recovers and fails again is new information."""
    ctl = RelayCtl(panes(alice=bell("aaa")))
    seen, pending, notes = {}, [], {}
    peer.relay_tick(ctl, PEOPLE, seen, pending, 500, ctl.say,
                    Fetcher(framed(("aaa", "bob", "hi")), rc=1), notes=notes)
    peer.relay_tick(ctl, PEOPLE, seen, pending, 500, ctl.say,
                    Fetcher(framed(("aaa", "bob", "hi"))), notes=notes)
    ctl.current["AAAA1111"] = COMPOSER + bell("bbb")
    peer.relay_tick(ctl, PEOPLE, seen, pending, 500, ctl.say,
                    Fetcher(framed(("bbb", "bob", "hi again")), rc=1),
                    notes=notes)
    assert len([s for s in ctl.said if "fetch failed" in s]) == 2


def test_an_unreadable_pane_says_it_once(peer):
    """The pre-existing unthrottled path beside the new one. It always fired
    every tick; moving the `seen` write only made the shape obvious."""

    class Unreadable(RelayCtl):
        def text(self, target, pane, lines, whole=False):
            return "", "agterm: no such session"

    ctl = Unreadable(panes())
    notes = {}
    for _ in range(20):
        peer.relay_tick(ctl, PEOPLE, {}, [], 500, ctl.say, Fetcher(),
                        notes=notes)
    per_name = [s for s in ctl.said if "cannot read alice" in s]
    assert len(per_name) == 1, "20 ticks, one line per name: %r" % (per_name,)


# ---------------------------------------------------------------------------
# a scan says HOW it ended, and the five ways it never reached the doorbell
# ---------------------------------------------------------------------------
#
# The distinction priming turns on: "we did not read the pane" leaves it
# suspect, "we read it and nothing was announced" proves there is nothing stale
# on it. SCAN_DETACHED looks like the second and is the first.


def scan(peer, ctl, name="alice", binding=None, seen=None, fetch=None,
         notes=None, chat_dir=None):
    sessions = {}
    for s in peer.sessions_of(ctl.tree()):
        sessions[s["id"]] = s
    return peer.scan_participant(
        ctl, sessions, name, binding or PEOPLE[name], {} if seen is None else seen,
        500, ctl.say, fetch or Fetcher(), {} if notes is None else notes,
        chat_dir)


def test_a_scan_of_a_missing_row_says_no_row(peer):
    ctl = RelayCtl({})
    assert scan(peer, ctl)[0] == peer.SCAN_NO_ROW


def test_a_scan_of_an_unreadable_pane_says_unreadable(peer):
    class Unreadable(RelayCtl):
        def text(self, target, pane, lines, whole=False):
            return "", "agterm: no such session"

    assert scan(peer, Unreadable(panes()))[0] == peer.SCAN_UNREADABLE


def test_a_detached_row_is_not_a_clean_pane(peer):
    """⚠️ The one that has to be right. A detached row's doorbell is not
    VISIBLE, which is not the same as ABSENT -- and every row `agb-refresh`
    re-mints comes back detached, so getting this wrong delivers a joiner's
    backlog on the ordinary path."""
    ctl = RelayCtl(dict(panes(), AAAA1111=MENU))
    assert scan(peer, ctl)[0] == peer.SCAN_DETACHED
    assert peer.SCAN_DETACHED not in peer.SCAN_PANE_IS_CLEAN


def test_a_pane_with_no_doorbell_is_clean(peer):
    """The companion: read, and nothing announced, is a FACT about the pane."""
    result, messages = scan(peer, RelayCtl(panes()))
    assert result == peer.SCAN_NO_DOORBELL
    assert messages == []
    assert peer.SCAN_NO_DOORBELL in peer.SCAN_PANE_IS_CLEAN


def test_an_unchanged_doorbell_says_caught_up(peer):
    ctl = RelayCtl(panes(alice=bell("aaa")))
    assert scan(peer, ctl, seen={"alice": "aaa"})[0] == peer.SCAN_CAUGHT_UP


def test_a_successful_drain_says_fetched_and_records_seen(peer):
    ctl = RelayCtl(panes(alice=bell("aaa")))
    seen = {}
    result, messages = scan(peer, ctl, seen=seen,
                            fetch=Fetcher(framed(("aaa", "bob", "hi"))))
    assert result == peer.SCAN_FETCHED
    assert [m["text"] for m in messages] == ["hi"]
    assert seen == {"alice": "aaa"}, "the scan owns `seen`"


def test_a_failed_drain_says_so_and_records_nothing(peer):
    """The companion to the one above, and the reason the scan owns `seen`: a
    caller cannot mark a participant read without having read it."""
    ctl = RelayCtl(panes(alice=bell("aaa")))
    seen = {}
    result, messages = scan(peer, ctl, seen=seen, fetch=Fetcher(rc=1))
    assert result == peer.SCAN_FETCH_FAILED
    assert messages == []
    assert seen == {}
    assert peer.SCAN_FETCH_FAILED not in peer.SCAN_PANE_IS_CLEAN


def test_the_seven_scan_outcomes_are_distinct(peer):
    """Two of them collapsing into one is the whole failure mode."""
    outcomes = [peer.SCAN_NO_ROW, peer.SCAN_UNREADABLE, peer.SCAN_DETACHED,
                peer.SCAN_NO_DOORBELL, peer.SCAN_CAUGHT_UP, peer.SCAN_FETCHED,
                peer.SCAN_FETCH_FAILED]
    assert len(set(outcomes)) == len(outcomes)


# ---------------------------------------------------------------------------
# the participant name alphabet
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", ["a/b", "a,b", "a@b", "a:b", "a b", "aéb"])
def test_a_name_outside_the_alphabet_is_refused(peer, bad):
    with pytest.raises(peer.PeerError) as caught:
        peer.parse_participants(["%s=R1" % (bad,), "c=R2"])
    assert "participant name" in str(caught.value)


def test_a_slash_in_the_ROW_is_still_fine(peer):
    """⚠️ The companion, and the reason the rule is scoped to the name. `<row>`
    is a row-title substring and the default `row_fields` renders `cwd`
    unshortened, so this is a spec people actually have."""
    people = peer.parse_participants(["bob=/home/you/agbridge", "c=R2"])
    assert people["bob"][0] == "/home/you/agbridge"


def test_the_alphabet_is_a_positive_list_not_a_denylist(peer):
    """A denylist grows a hole every time somebody invents a metacharacter."""
    assert peer.valid_participant_name("Agent_9.x-y")
    assert not peer.valid_participant_name("")
    assert not peer.valid_participant_name("a$b")
    assert not peer.valid_participant_name("a`b")


def test_an_equals_in_a_name_is_unreachable_not_refused(peer):
    """⚠️ Documented rather than tested as a refusal, because `partition("=")`
    takes the FIRST `=` -- so a name containing one cannot be built through this
    front door at all, and a test asserting a refusal would assert something
    that never fires. Whitespace is the same: relay words come from `.split()`."""
    people = peer.parse_participants(["a=b=R1", "c=R2"])
    assert people["a"][0] == "b=R1", "the second = landed in the ROW"


# ---------------------------------------------------------------------------
# --roster: the file, and the refusals that only apply at startup
# ---------------------------------------------------------------------------


def roster_file(tmp_path, text):
    path = tmp_path / "peers"
    path.write_text(text)
    return str(path)


def test_a_roster_line_parses_exactly_like_a_positional(peer, tmp_path):
    """The whole reason the file hands its words to parse_participants: one
    grammar, read from two places, so it cannot drift."""
    spec = "alice=agbridge bob=api@box:nfs"
    from_file = peer.parse_roster_text(spec)
    assert from_file == peer.parse_participants(spec.split())


def test_comments_and_blank_lines_are_ignored(peer):
    people = peer.parse_roster_text(
        "# who is in this chat\n\nalice=RowA\n\n   # indented comment\nbob=RowB\n")
    assert sorted(people) == ["alice", "bob"]


def test_a_hash_inside_a_row_is_not_a_comment(peer):
    """⚠️ `<row>` is a row-title substring and may contain `#`. Stripping from
    the first one would silently truncate a legitimate spec."""
    people = peer.parse_roster_text("alice=build#7\nbob=RowB\n")
    assert people["alice"][0] == "build#7"


def test_the_startup_refusals_are_distinguishable(peer, tmp_path):
    missing = str(tmp_path / "nope")
    with pytest.raises(peer.PeerError) as gone:
        peer.read_roster_file(missing)
    assert "cannot read the roster" in str(gone.value)

    with pytest.raises(peer.PeerError) as empty:
        peer.parse_roster_text("# nothing but a comment\n")
    assert "empty" in str(empty.value)

    with pytest.raises(peer.PeerError) as junk:
        peer.parse_roster_text("this is not a participant\nb=R2\n")
    assert "participants are name=" in str(junk.value)

    with pytest.raises(peer.PeerError) as thin:
        peer.parse_roster_text("alice=RowA\n")
    assert "at least 2 participants" in str(thin.value)


def test_a_roster_that_is_not_utf8_is_a_peer_error_not_a_crash(peer):
    """⚠️ UnicodeDecodeError is a ValueError, not an OSError, so a caller
    guarding the read with `except IOError` lets it through -- and at runtime
    that kills the relay over a half-written file."""
    with pytest.raises(peer.PeerError) as caught:
        peer.parse_roster_text(b"alice=\xff\xfe\n")
    assert "not UTF-8" in str(caught.value)


def test_the_runtime_minimum_allows_one_participant(peer):
    """A relay may not START with one, but it may DROP to one -- otherwise a
    roster edit could not remove a participant without stopping the relay."""
    people = peer.parse_roster_text("alice=RowA\n", minimum=1)
    assert sorted(people) == ["alice"]


def test_a_roster_and_positional_participants_are_refused_together(peer,
                                                                   tmp_path):
    path = roster_file(tmp_path, "alice=RowA\nbob=RowB\n")
    ctl = RelayCtl(panes())
    with pytest.raises(peer.PeerError) as caught:
        peer.cmd_relay(ctl, ["c=R3", "d=R4"], 500, 8, True, io.StringIO(),
                       roster=path)
    assert "not both" in str(caught.value)


def test_a_relay_reads_its_participants_from_the_roster(peer, tmp_path):
    path = roster_file(tmp_path, "# the chat\nalice=AAAA1111\nbob=BBBB2222\n")
    ctl = RelayCtl(panes())
    out = io.StringIO()
    assert peer.cmd_relay(ctl, [], 500, 8, True, out, roster=path) == 0
    assert "alice" in out.getvalue() and "bob" in out.getvalue()


# ---------------------------------------------------------------------------
# re-reading the roster: the byte gate, and holding on every bad read
# ---------------------------------------------------------------------------


class CountingReader(object):
    """Wraps a RosterReader and counts how often the file was actually parsed.

    A change gate can only be tested by counting the work it skips; asserting
    on the RESULT passes whether or not the gate exists.
    """

    def __init__(self, peer, path, minimum=1):
        self.reader = peer.RosterReader(path, minimum=minimum)
        self.peer, self.parses = peer, 0
        real = peer.parse_roster_text

        def counted(data, minimum=2):
            self.parses += 1
            return real(data, minimum=minimum)
        self.counted = counted

    def poll(self, say, notes=None):
        real = self.peer.parse_roster_text
        self.peer.parse_roster_text = self.counted
        try:
            return self.reader.poll(say, notes)
        finally:
            self.peer.parse_roster_text = real


def test_an_unchanged_roster_is_not_re_parsed(peer, tmp_path):
    path = roster_file(tmp_path, "alice=RowA\nbob=RowB\n")
    reader, said = CountingReader(peer, path), []
    assert reader.poll(said.append, {}) is not None
    for _ in range(9):
        assert reader.poll(said.append, {}) is None
    assert reader.parses == 1, "ten polls, one edit, one parse"


def test_changed_bytes_are_re_parsed(peer, tmp_path):
    """The companion. Without it the gate could refuse everything and pass."""
    path = roster_file(tmp_path, "alice=RowA\nbob=RowB\n")
    reader, said = CountingReader(peer, path), []
    reader.poll(said.append, {})
    open(path, "w").write("alice=RowA\nbob=RowB\ncarol=RowC\n")
    spec = reader.poll(said.append, {})
    assert sorted(spec) == ["alice", "bob", "carol"]
    assert reader.parses == 2


def test_a_same_size_same_second_rewrite_is_seen(peer, tmp_path):
    """⚠️ The case a stat key gets wrong. Identical length, written inside the
    same second, different content -- a (mtime, size) gate would call it
    unchanged and the join would never be applied."""
    path = roster_file(tmp_path, "alice=RowA\nbob=RowB\n")
    reader, said = CountingReader(peer, path), []
    reader.poll(said.append, {})
    open(path, "w").write("alice=RowA\nbob=RowC\n")
    spec = reader.poll(said.append, {})
    assert spec is not None and spec["bob"][0] == "RowC"


@pytest.mark.parametrize("content,reason", [
    ("alice=RowA\nthis is not a spec\n", "participants are name="),
    ("", "empty"),
    ("# only a comment\n", "empty"),
])
def test_a_bad_read_holds_the_roster_already_running(peer, tmp_path,
                                                     content, reason):
    path = roster_file(tmp_path, "alice=RowA\nbob=RowB\n")
    reader = peer.RosterReader(path)
    said, notes = [], {}
    assert reader.poll(said.append, notes) is not None
    open(path, "w").write(content)
    assert reader.poll(said.append, notes) is None, "held, not applied"
    assert reader.spec is not None and sorted(reader.spec) == ["alice", "bob"], \
        "the roster already running is untouched"
    assert any(reason in s and "keeping" in s for s in said), said


def test_a_missing_roster_holds_rather_than_emptying_the_chat(peer, tmp_path):
    """⚠️ Deliberately NOT treating ENOENT as positive proof: `mv`ing the file
    away must not dissolve a live conversation."""
    path = roster_file(tmp_path, "alice=RowA\nbob=RowB\n")
    reader, said, notes = peer.RosterReader(path), [], {}
    reader.poll(said.append, notes)
    os.remove(path)
    assert reader.poll(said.append, notes) is None
    assert sorted(reader.spec) == ["alice", "bob"]


def test_a_hold_is_said_once_not_every_tick(peer, tmp_path):
    path = roster_file(tmp_path, "alice=RowA\nbob=RowB\n")
    reader, said, notes = peer.RosterReader(path), [], {}
    reader.poll(said.append, notes)
    os.remove(path)
    for _ in range(20):
        reader.poll(said.append, notes)
    assert len([s for s in said if "keeping" in s]) == 1, said


def test_a_roster_restored_after_a_hold_is_applied(peer, tmp_path):
    """⚠️ The two-state property: the gate advanced past the bad bytes, but the
    diff base did not, so the next good edit is still seen as a change."""
    path = roster_file(tmp_path, "alice=RowA\nbob=RowB\n")
    reader, said, notes = peer.RosterReader(path), [], {}
    reader.poll(said.append, notes)
    open(path, "w").write("garbage\n")
    assert reader.poll(said.append, notes) is None
    open(path, "w").write("alice=RowA\nbob=RowB\ncarol=RowC\n")
    spec = reader.poll(said.append, notes)
    assert spec is not None and "carol" in spec


def test_a_drop_to_one_participant_is_announced_not_refused(peer, tmp_path):
    path = roster_file(tmp_path, "alice=RowA\nbob=RowB\n")
    reader, said, notes = peer.RosterReader(path), [], {}
    reader.poll(said.append, notes)
    open(path, "w").write("alice=RowA\n")
    spec = reader.poll(said.append, notes)
    assert sorted(spec) == ["alice"], "applied, because people do leave"
    assert any("down to 1" in s for s in said), said


# ---------------------------------------------------------------------------
# leaving, and how a rebind differs from it
# ---------------------------------------------------------------------------


# ⚠️ Kept beside the implementation's own list rather than read from it, and
# compared against it below. A note left behind after a leave is not cosmetic:
# ("held", name) would resume a half-hour message hold for a name that came
# back, and ("prime", name) would make its next join give up early.
LEAVE_CLEARS_NOTES = [
    ("gone", "bob"), ("menu", "bob"), ("said", "bob"), ("held", "bob"),
    ("prime", "bob"), ("said", ("fetch", "bob")), ("said", ("read", "bob")),
    ("said", ("gone-row", "bob"))]


def test_the_per_name_notes_list_is_complete(peer):
    assert set(peer._name_notes("bob")) == set(LEAVE_CLEARS_NOTES)


def leave_state(peer, extra_notes=None):
    seen = {"alice": "aaa", "bob": "bbb"}
    pending = [("alice", {"id": "m1", "to": "bob", "text": "for bob"}),
               ("bob", {"id": "m2", "to": "alice", "text": "from bob"})]
    resolved = dict(PEOPLE)
    notes = {"delivered": {("bob", "m9"), ("alice", "m8")}}
    for index, key in enumerate(LEAVE_CLEARS_NOTES):
        notes[key] = index + 1
    notes.update(extra_notes or {})
    return seen, pending, resolved, set(), notes


def test_a_leave_forgets_everything_pane_specific(peer):
    seen, pending, resolved, needs_prime, notes = leave_state(peer)
    said = []
    peer.apply_leaves({"bob"}, set(), seen, pending, resolved, needs_prime,
                      notes, said.append)
    assert "bob" not in seen
    assert "bob" not in resolved, "or resolve_all keeps the old row"
    # ⚠️ A LITERAL, not `peer._name_notes("bob")`. Reading the constant out of
    # the implementation makes this a tautology -- whatever the code purges,
    # purges -- and widening or shrinking that list is exactly what such a test
    # stops seeing.
    assert [k for k in LEAVE_CLEARS_NOTES if k in notes] == []
    assert notes["delivered"] == {("alice", "m8")}, "only bob's ids go"


def test_a_leave_drops_its_queued_mail_and_names_it(peer):
    """Load-bearing rather than duplicate: try_deliver HOLDS for a roster
    member now, so without this a leaver's mail waits for ever."""
    seen, pending, resolved, needs_prime, notes = leave_state(peer)
    said = []
    peer.apply_leaves({"bob"}, set(), seen, pending, resolved, needs_prime,
                      notes, said.append)
    assert [m["id"] for (_, m) in pending] == ["m2"], "messages FROM bob stay"
    assert any("#m1" in s for s in said), said


def test_a_message_from_a_leaver_is_kept(peer):
    """It was already unset from the sender's pane; leaving does not unsay it."""
    seen, pending, resolved, needs_prime, notes = leave_state(peer)
    peer.apply_leaves({"bob"}, set(), seen, pending, resolved, needs_prime,
                      notes, lambda m: None)
    assert [s for (s, _) in pending] == ["bob"]


def test_a_rebind_keeps_the_mail_queued_for_it(peer):
    """⚠️ The distinction. A rebound participant moved; it did not leave, and
    its queued messages exist nowhere else."""
    seen, pending, resolved, needs_prime, notes = leave_state(peer)
    said = []
    peer.apply_leaves(set(), {"bob"}, seen, pending, resolved, needs_prime,
                      notes, said.append)
    assert [m["id"] for (_, m) in pending] == ["m1", "m2"], "nothing dropped"
    assert said == [], "and nothing announced as lost"


def test_a_rebind_still_forgets_the_old_pane(peer):
    """The companion to the one above: it keeps the mail and nothing else."""
    seen, pending, resolved, needs_prime, notes = leave_state(peer)
    peer.apply_leaves(set(), {"bob"}, seen, pending, resolved, needs_prime,
                      notes, lambda m: None)
    assert "bob" not in seen and "bob" not in resolved
    assert notes["delivered"] == {("alice", "m8")}


def test_a_rebind_asks_to_be_primed_again(peer):
    """The new pane may hold a conversation this name was never part of."""
    seen, pending, resolved, needs_prime, notes = leave_state(peer)
    peer.apply_leaves(set(), {"bob"}, seen, pending, resolved, needs_prime,
                      notes, lambda m: None)
    assert needs_prime == {"bob"}


def test_a_leave_does_not_ask_to_be_primed(peer):
    """The companion: a name that is gone must not be queued for a prime that
    will never resolve."""
    seen, pending, resolved, needs_prime, notes = leave_state(peer)
    needs_prime.add("bob")
    peer.apply_leaves({"bob"}, set(), seen, pending, resolved, needs_prime,
                      notes, lambda m: None)
    assert needs_prime == set()


def test_a_leave_touches_nobody_else(peer):
    seen, pending, resolved, needs_prime, notes = leave_state(peer)
    peer.apply_leaves({"bob"}, set(), seen, pending, resolved, needs_prime,
                      notes, lambda m: None)
    assert seen == {"alice": "aaa"} and "alice" in resolved


# ---------------------------------------------------------------------------
# priming a joiner: needs_prime as state that survives ticks
# ---------------------------------------------------------------------------
#
# needs_prime MEANS: there may be content on this pane that predates the join,
# so the next successful drain must be discarded rather than delivered. Every
# rule below is that sentence.


def prime(peer, ctl, needs_prime, people=None, seen=None, fetch=None,
          notes=None):
    sessions = {}
    for s in peer.sessions_of(ctl.tree()):
        sessions[s["id"]] = s
    return peer.prime_joiners(
        ctl, sessions, needs_prime, PEOPLE if people is None else people,
        {} if seen is None else seen, 500, ctl.say, fetch or Fetcher(),
        {} if notes is None else notes)


def test_a_joiners_backlog_is_discarded_not_delivered(peer):
    ctl = RelayCtl(panes(alice=bell("old")))
    needs_prime = {"alice"}
    discarded = prime(peer, ctl, needs_prime,
                      fetch=Fetcher(framed(("old", "bob", "stale"))))
    assert ctl.typed == [], "priming must deliver none of it"
    assert any("#old" in d for d in discarded), discarded
    assert needs_prime == set(), "read, so no longer suspect"


def test_a_joiner_with_no_doorbell_is_primed_immediately(peer):
    """⚠️ The inverse failure. A fresh joiner has never sent anything, so there
    is no doorbell -- and treating that as "not yet primed" would leave it
    pending until the bound, throwing away its FIRST REAL MESSAGE as a backlog."""
    ctl = RelayCtl(panes())
    needs_prime = {"alice"}
    assert prime(peer, ctl, needs_prime) == []
    assert needs_prime == set(), "the pane was read; there is nothing stale"


def test_a_detached_joiner_stays_pending(peer):
    """⚠️ The one that must NOT clear. A menu hides the status bar, so the
    doorbell is not visible -- not absent -- and every row `agb-refresh`
    re-mints comes back detached."""
    ctl = RelayCtl(dict(panes(), AAAA1111=MENU))
    needs_prime = {"alice"}
    prime(peer, ctl, needs_prime)
    assert needs_prime == {"alice"}


def test_a_failed_drain_keeps_the_joiner_pending_then_discards(peer):
    """The retry, across ticks, with no second roster edit."""
    ctl = RelayCtl(panes(alice=bell("old")))
    needs_prime, seen, notes = {"alice"}, {}, {}
    prime(peer, ctl, needs_prime, seen=seen, fetch=Fetcher(rc=1), notes=notes)
    assert needs_prime == {"alice"}, "the fetch failed; we still do not know"
    discarded = prime(peer, ctl, needs_prime, seen=seen, notes=notes,
                      fetch=Fetcher(framed(("old", "bob", "stale"))))
    assert needs_prime == set()
    assert any("#old" in d for d in discarded), discarded


def test_an_unresolved_joiner_is_primed_on_a_later_tick(peer):
    """No second roster edit is needed: needs_prime survives ticks."""
    absent = RelayCtl({})
    needs_prime, seen, notes = {"alice"}, {}, {}
    prime(peer, absent, needs_prime, people={}, seen=seen, notes=notes)
    assert needs_prime == {"alice"}
    ctl = RelayCtl(panes(alice=bell("old")))
    prime(peer, ctl, needs_prime, seen=seen, notes=notes,
          fetch=Fetcher(framed(("old", "bob", "stale"))))
    assert needs_prime == set()


def test_priming_gives_up_without_discarding(peer):
    """⚠️ Bounded, and cleared WITHOUT a drain. While pending, the ordinary scan
    skips this name, so persisting for ever means every message it sends is
    thrown away by the prime that finally works."""
    ctl = RelayCtl(dict(panes(), AAAA1111=MENU))
    needs_prime, notes, seen = {"alice"}, {}, {}
    for _ in range(peer.PRIME_ATTEMPTS_MAX):
        prime(peer, ctl, needs_prime, seen=seen, notes=notes)
    assert needs_prime == set(), "gave up"
    assert any("gave up priming alice" in s for s in ctl.said), ctl.said
    assert any("DELIVERED rather than discarded" in s for s in ctl.said)


def test_an_unresolved_joiner_does_not_burn_its_attempts(peer):
    """The companion to the bound: a row that is merely absent must not use up
    the budget meant for a pane we could actually look at."""
    needs_prime, notes, seen = {"alice"}, {}, {}
    for _ in range(peer.PRIME_ATTEMPTS_MAX * 2):
        prime(peer, RelayCtl({}), needs_prime, people={}, seen=seen, notes=notes)
    assert needs_prime == {"alice"}


def test_priming_walks_a_copy_of_the_set(peer):
    """It removes from the set it is iterating."""
    ctl = RelayCtl(panes())
    needs_prime = {"alice", "bob"}
    prime(peer, ctl, needs_prime)
    assert needs_prime == set()


# ---------------------------------------------------------------------------
# the relay loop: roster changes applied mid-run
# ---------------------------------------------------------------------------


class TickingCtl(RelayCtl):
    """A relay ctl whose `sleep` runs a callback, so a test can change the
    world between ticks. `cmd_relay`'s `once=True` returns after the PRIMING
    tick, so without this there is no way to observe a second one at all."""

    def __init__(self, panes_, on_tick=None):
        RelayCtl.__init__(self, panes_)
        self.on_tick = on_tick or (lambda n: None)
        self.tick = 0

    def sleep(self, seconds):
        self.tick += 1
        self.on_tick(self.tick)


def test_a_participant_added_mid_run_joins_without_a_restart(peer, tmp_path):
    path = roster_file(tmp_path, "alice=AAAA1111\nbob=BBBB2222\n")
    surfaces = dict(panes(), CCCC3333=COMPOSER)

    def edit(tick):
        if tick == 1:
            open(path, "w").write(
                "alice=AAAA1111\nbob=BBBB2222\ncarol=CCCC3333\n")
    ctl = TickingCtl(surfaces, edit)
    out = io.StringIO()
    peer.cmd_relay(ctl, [], 500, 8, False, out, roster=path, ticks=3)
    assert "+carol" in out.getvalue(), out.getvalue()


def test_a_joiners_backlog_is_not_delivered_by_the_loop(peer, tmp_path):
    """⚠️ End to end: the joiner's pane already has an old message on it, and
    the tick that admits it must discard rather than deliver."""
    path = roster_file(tmp_path, "alice=AAAA1111\nbob=BBBB2222\n")
    surfaces = dict(panes(), CCCC3333=COMPOSER + bell("old"))

    def edit(tick):
        if tick == 1:
            open(path, "w").write(
                "alice=AAAA1111\nbob=BBBB2222\ncarol=CCCC3333\n")
    ctl = TickingCtl(surfaces, edit)
    out = io.StringIO()
    # ⚠️ ticks=2 is the assertion: the joiner must be resolved AND primed on the
    # one iteration that follows the edit. Reading the roster after
    # `resolve_all` would leave it unresolved that tick and cost another.
    peer.cmd_relay(ctl, [], 500, 8, False, out, roster=path, ticks=2,
                   fetch=Fetcher(framed(("old", "bob", "ancient"))))
    assert [t for (_, _, t) in ctl.typed if t not in ("\n", "\r")] == [], out.getvalue()
    assert "predate a join" in out.getvalue(), out.getvalue()


def test_a_participant_removed_mid_run_stops_being_scanned(peer, tmp_path):
    path = roster_file(tmp_path, "alice=AAAA1111\nbob=BBBB2222\n")

    def edit(tick):
        if tick == 1:
            open(path, "w").write("alice=AAAA1111\n")
    ctl = TickingCtl(panes(), edit)
    out = io.StringIO()
    peer.cmd_relay(ctl, [], 500, 8, False, out, roster=path, ticks=3)
    assert "-bob" in out.getvalue(), out.getvalue()
    assert "down to 1" in out.getvalue(), out.getvalue()


def test_a_positional_relay_never_reads_a_roster(peer, tmp_path, monkeypatch):
    """The backward-compatibility guarantee, named at the seam so it cannot
    patch the wrong one and pass."""
    calls = []
    real = peer.read_roster_file
    monkeypatch.setattr(peer, "read_roster_file",
                        lambda p: calls.append(p) or real(p))
    ctl = TickingCtl(panes())
    peer.cmd_relay(ctl, ["alice=AAAA1111", "bob=BBBB2222"], 500, 8, False,
                   io.StringIO(), ticks=3)
    assert calls == []


def test_two_names_resolving_to_one_row_are_refused(peer):
    """⚠️ `resolve` refuses an ambiguous LABEL, but two different labels can
    each unambiguously match the same row. Two names on one pane drain it
    twice, and try_deliver's self-guard compares NAMES, so a message to the
    alias is typed into its own sender's composer."""
    ctl = RelayCtl(panes())
    said, notes = [], {}
    spec = {"alice": ("AAAA1111", "left", None, None),
            "clone": ("AAAA1111", "left", None, None)}
    resolved = peer.resolve_all(ctl, spec, said.append, {}, notes)
    assert sorted(resolved) == ["alice"], "the first by name is kept"
    assert any("both resolve to row" in s for s in said), said


def test_distinct_rows_are_all_kept(peer):
    """The companion: the collision guard must not narrow an ordinary map."""
    ctl = RelayCtl(panes())
    resolved = peer.resolve_all(ctl, dict(PEOPLE), lambda m: None, {}, {})
    assert sorted(resolved) == ["alice", "bob"]


def test_an_unresolvable_participant_is_not_reported_every_tick(peer):
    ctl = RelayCtl(panes())
    said, notes = [], {}
    spec = dict(PEOPLE, ghost=("NOSUCHROW", "left", None, None))
    for _ in range(20):
        peer.resolve_all(ctl, spec, said.append, {}, notes)
    assert len([s for s in said if s.startswith("ghost:")]) == 1, said


def test_a_name_still_being_primed_can_still_receive(peer):
    """⚠️ The skip is scoped to the doorbell SCAN, never to delivery. A
    participant that only ever receives would otherwise be unreachable for as
    long as its own pane could not be read."""
    ctl = RelayCtl(panes())
    pending = [("alice", {"id": "m1", "to": "bob", "text": "hi"})]
    peer.relay_tick(ctl, {"alice": PEOPLE["alice"]}, {}, pending, 500, ctl.say,
                    Fetcher(), notes={}, members={"alice", "bob"},
                    deliver_to=PEOPLE)
    assert [t for (_, _, t) in ctl.typed if t not in ("\n", "\r")] == [
        "[chat from alice] hi"]
    assert pending == []


def test_delivery_defaults_to_the_scanned_set(peer):
    """The companion: absent `deliver_to`, nothing is widened and every other
    caller behaves exactly as before."""
    ctl = RelayCtl(panes())
    pending = [("alice", {"id": "m1", "to": "bob", "text": "hi"})]
    peer.relay_tick(ctl, {"alice": PEOPLE["alice"]}, {}, pending, 500, ctl.say,
                    Fetcher(), notes={}, members={"alice", "bob"})
    assert ctl.typed == [], "bob is not in the scanned map, so he is held"
    assert len(pending) == 1


def test_a_leavers_queued_mail_is_dropped_and_named_by_the_loop(peer, tmp_path):
    """apply_leaves' half of a removal, end to end. `try_deliver` HOLDS for a
    roster member whose row has not appeared, so without the leave path this
    message would wait for ever instead of being dropped."""
    path = roster_file(tmp_path, "alice=AAAA1111\nghost=NOSUCHROW\n")
    ctl = TickingCtl(panes())

    def edit(tick):
        if tick == 1:
            # Ring AFTER the priming pass, or the message is discarded as a
            # backlog and never reaches the queue at all.
            ctl.current["AAAA1111"] = COMPOSER + bell("aaa")
        if tick == 2:
            open(path, "w").write("alice=AAAA1111\nbob=BBBB2222\n")
    ctl.on_tick = edit
    out = io.StringIO()
    peer.cmd_relay(ctl, [], 500, 8, False, out, roster=path, ticks=3,
                   fetch=Fetcher(framed(("aaa", "ghost", "into the void"))))
    text = out.getvalue()
    assert "ghost is in the roster but has no row yet" in text, text
    assert "ghost left" in text and "#aaa" in text, text


def test_a_rebind_keeps_its_queued_mail_through_the_loop(peer, tmp_path):
    """⚠️ End to end, because the leave/rebind distinction lives at the CALL
    site: a mutation that passes `left | rebound` is invisible to a unit test of
    `apply_leaves` itself. A rebound participant moved; it did not leave, and
    its queued messages exist nowhere else."""
    path = roster_file(tmp_path, "alice=AAAA1111\nghost=NOSUCHROW\n")
    ctl = TickingCtl(panes())

    def edit(tick):
        if tick == 1:
            ctl.current["AAAA1111"] = COMPOSER + bell("aaa")
        if tick == 2:
            open(path, "w").write("alice=AAAA1111\nghost=BBBB2222\n")
    ctl.on_tick = edit
    out = io.StringIO()
    peer.cmd_relay(ctl, [], 500, 8, False, out, roster=path, ticks=3,
                   fetch=Fetcher(framed(("aaa", "ghost", "still for you"))))
    assert [t for (_, _, t) in ctl.typed if t not in ("\n", "\r")] == [
        "[chat from alice] still for you"], out.getvalue()
    assert "will not be delivered" not in out.getvalue(), out.getvalue()


def test_a_rebind_stops_routing_to_the_old_row(peer, tmp_path):
    """⚠️ The case `resolve_all`'s keep-previous makes dangerous. A rebind keeps
    the same NAME, so if the new label does not resolve this tick, the old
    binding would be kept -- and the message would be typed into the pane the
    participant just moved away from. Dropping `resolved[name]` is what stops
    it, and only a name that WAS resolved can show that."""
    path = roster_file(tmp_path, "alice=AAAA1111\nbob=BBBB2222\n")
    ctl = TickingCtl(panes())

    def edit(tick):
        if tick == 1:
            open(path, "w").write("alice=AAAA1111\nbob=NOSUCHROW\n")
            ctl.current["AAAA1111"] = COMPOSER + bell("aaa")
    ctl.on_tick = edit
    out = io.StringIO()
    peer.cmd_relay(ctl, [], 500, 8, False, out, roster=path, ticks=2,
                   fetch=Fetcher(framed(("aaa", "bob", "wrong pane"))))
    assert [t for (t_, p, t) in ctl.typed if t not in ("\n", "\r")] == [], (
        "bob moved; nothing may be typed into the row he left: %s"
        % (out.getvalue(),))
    assert "bob is in the roster but has no row yet" in out.getvalue()


def test_a_held_message_for_a_vanished_row_says_it_once(peer):
    """⚠️ The last holding path in this file that still spoke every tick. It
    returns False, so it is re-reached on every tick -- and `resolve_all`
    deliberately keeps a stale binding across a re-mint, so "missing" lasts."""
    ctl = RelayCtl({})
    pending = [("alice", {"id": "m1", "to": "bob", "text": "hi"})]
    notes = {}
    for _ in range(20):
        peer.relay_tick(ctl, PEOPLE, {}, pending, 500, ctl.say, Fetcher(),
                        notes=notes, members={"alice", "bob"})
    gone = [s for s in ctl.said if "row is gone from the tree" in s]
    assert len(pending) == 1, "non-vacuous: it really was held every tick"
    assert len(gone) == 1, gone


# ---------------------------------------------------------------------------
# `relay` is reserved, and the reservation is exact
# ---------------------------------------------------------------------------
#
# `agb-peer who` sends WHO_REQUEST to RELAY_NAME, so a participant of that name
# would shadow the relay itself. ⚠️ Both comparisons -- this refusal and the
# intercept that answers -- are exact and case-sensitive, and they must AGREE: a
# case-insensitive refusal with an exact intercept would accept `Relay` and then
# never intercept it, leaving a name that can be rostered and never addressed.


def test_the_relay_name_is_refused_as_a_participant(peer):
    with pytest.raises(peer.PeerError) as caught:
        peer.parse_participants(["%s=RowR" % (peer.RELAY_NAME,), "a=RowA"])
    assert "reserved" in str(caught.value)
    assert "who" in str(caught.value), "the message must say what claims it"


@pytest.mark.parametrize("name", ["Relay", "RELAY", "relayed", "prerelay"])
def test_a_near_miss_is_an_ordinary_participant(peer, name):
    """⚠️ The companion that is actually at risk. "an ordinary name still works"
    would prove nothing -- a blanket refusal already fails ~15 roster tests --
    but a case-folding or substring refusal fails only these."""
    people = peer.parse_participants(["%s=RowR" % (name,), "a=RowA"])
    assert sorted(people) == sorted([name, "a"])


def test_the_reserved_name_and_the_request_token_are_distinct_constants(peer):
    """Read once here so a later task cannot quietly conflate them: the token is
    what stops the reply loop, the name is what routes to the relay."""
    assert peer.RELAY_NAME == "relay"
    assert peer.WHO_REQUEST == "who"
    assert peer.WHO_REQUEST not in peer.PANE_WORDS, (
        "cmd_send refuses PANE_WORDS, so a token in that set could never be sent")


# ---------------------------------------------------------------------------
# the text of a `who` answer
# ---------------------------------------------------------------------------


def test_the_answer_names_everyone_and_marks_exactly_one_you(peer):
    got = peer.roster_answer("alice", {"alice", "bob", "carol"})
    assert got == "you=alice peer=bob peer=carol"
    assert got.count("you=") == 1


def test_a_one_participant_roster_answers_with_no_peers(peer):
    """`RosterReader` explicitly permits a runtime drop to one, so this is a
    state the relay reaches rather than a degenerate input."""
    assert peer.roster_answer("alice", {"alice"}) == "you=alice"


def test_the_answer_is_ordered_so_it_can_be_compared(peer):
    """⚠️ Asserted as a PROPERTY of the output, not against a literal, and with
    six peers rather than two. `PYTHONHASHSEED` randomises string hashing per
    process, so an unsorted implementation is FLAKY rather than reliably wrong:
    a literal comparison passes on whichever runs happen to hash into order, and
    a mutation check that samples one run reads that as "not caught". Six peers
    make an accidental pass a 1-in-720 event, and reading the order back out of
    the answer needs nothing from the implementation."""
    names = {"me", "zeta", "alpha", "Mid", "beta", "Omega", "delta"}
    got = peer.roster_answer("me", names)
    peers = [w.split("=", 1)[1] for w in got.split() if w.startswith("peer=")]
    assert len(peers) == 6, got
    assert peers == sorted(peers), got


def test_a_name_outside_the_membership_never_appears(peer):
    got = peer.roster_answer("alice", {"alice", "bob"})
    assert "carol" not in got


def test_the_answer_accepts_a_dict_membership(peer):
    """⚠️ The path that is NOT the one everyone tests. `relay_tick`'s
    `members=None` fallback substitutes `people`, a dict -- and a bare
    `members - {you}` raises TypeError on it. `try_deliver` gets away with the
    same fallback only because it does `in` tests."""
    assert peer.roster_answer("alice", {"alice": 1, "bob": 2}) == \
        "you=alice peer=bob"


# ---------------------------------------------------------------------------
# the relay answers a `who`
# ---------------------------------------------------------------------------
#
# The asker is the pane the doorbell rang in -- try_deliver's rule -- which is
# why this needs no identity of its own. Only the exact token is answered, and
# that is what stops the reply loop SKILL.md's "reply to a peer" rule would
# otherwise start.


def who_from(sender, text=None, ident="w1"):
    """A drained `who` request, as parse_show_options would build it."""
    return framed((ident, "relay", "who" if text is None else text))


def test_a_who_request_is_answered_to_the_asker(peer):
    ctl = RelayCtl(panes(alice=bell("w1")))
    peer.relay_tick(ctl, PEOPLE, {}, [], 500, ctl.say,
                    Fetcher(who_from("alice")), notes={},
                    members={"alice", "bob"})
    typed = [(t, body) for (t, _p, body) in ctl.typed if body not in ("\n", "\r")]
    assert len(typed) == 1, typed
    target, body = typed[0]
    assert target == PEOPLE["alice"][0], "the answer goes to the asker"
    assert body == "[chat from relay] you=alice peer=bob", body


def test_the_request_itself_is_never_routed(peer):
    """The companion. Without it, "answered" passes against a relay that also
    delivers the request to a participant called `relay` -- or to nobody, with a
    dropped-message line nobody reads."""
    ctl = RelayCtl(panes(alice=bell("w1")))
    said = []
    peer.relay_tick(ctl, PEOPLE, {}, [], 500, said.append,
                    Fetcher(who_from("alice")), notes={},
                    members={"alice", "bob"})
    assert not any("not a participant" in s for s in said), said
    assert any("asked who is here" in s for s in said), said


def test_the_answer_carries_the_relay_prefix(peer):
    """⚠️ `[chat from relay] ` is load-bearing in three other places: SKILL.md's
    "this is not a peer" rule, and two walkthrough checks. It comes from the
    reply's SENDER being RELAY_NAME."""
    ctl = RelayCtl(panes(alice=bell("w1")))
    peer.relay_tick(ctl, PEOPLE, {}, [], 500, ctl.say,
                    Fetcher(who_from("alice")), notes={},
                    members={"alice", "bob"})
    bodies = [b for (_t, _p, b) in ctl.typed if b not in ("\n", "\r")]
    assert bodies and bodies[0].startswith("[chat from relay] "), bodies


def test_the_answer_is_not_dropped_as_addressed_to_itself(peer):
    """⚠️ The failure the sender field guards. try_deliver drops a message whose
    recipient equals its sender, so signing the reply with the asker's own name
    loses it permanently -- with a line blaming the asker."""
    ctl = RelayCtl(panes(alice=bell("w1")))
    said = []
    peer.relay_tick(ctl, PEOPLE, {}, [], 500, said.append,
                    Fetcher(who_from("alice")), notes={},
                    members={"alice", "bob"})
    assert not any("addressed to itself" in s for s in said), said


def test_each_asker_gets_its_own_you(peer):
    """Two askers in one tick: `you` is per-pane, not a constant."""
    ctl = RelayCtl(panes(alice=bell("w1"), bob=bell("w2")))

    class TwoAskers(Fetcher):
        def __call__(self, argv):
            if "show-options" in argv:
                # Same request from both panes; `done` keys on (name, id), so
                # each is processed and each must get its OWN `you`.
                self.calls.append(argv)
                return 0, framed(("w1", "relay", "who")), ""
            return Fetcher.__call__(self, argv)

    peer.relay_tick(ctl, PEOPLE, {}, [], 500, ctl.say, TwoAskers(),
                    notes={}, members={"alice", "bob"})
    bodies = sorted(b for (_t, _p, b) in ctl.typed if b not in ("\n", "\r"))
    assert len(bodies) == 2, bodies
    assert bodies[0] == "[chat from relay] you=alice peer=bob", bodies
    assert bodies[1] == "[chat from relay] you=bob peer=alice", bodies


def test_a_non_token_message_to_the_relay_is_dropped_and_named(peer):
    """⚠️ The loop guard. SKILL.md tells an agent to reply to anything shaped
    `[chat from <name>]`, so a polite answer to the answer would ask again."""
    ctl = RelayCtl(panes(alice=bell("w1")))
    said = []
    peer.relay_tick(ctl, PEOPLE, {}, [], 500, said.append,
                    Fetcher(who_from("alice", text="thanks")), notes={},
                    members={"alice", "bob"})
    assert ctl.typed == [], "a non-token message must produce no answer"
    assert any("not 'who'" in s and "thanks" in s for s in said), said


def test_a_who_on_the_priming_pass_is_discarded_not_answered(peer):
    """⚠️ Placement. Above the `if deliver_new:` branch the relay would answer a
    request that predates it AND deliver on the priming tick, because the
    delivery loop runs unconditionally."""
    ctl = RelayCtl(panes(alice=bell("w1")))
    said = []
    peer.relay_tick(ctl, PEOPLE, {}, [], 500, said.append,
                    Fetcher(who_from("alice")), notes={},
                    members={"alice", "bob"}, deliver_new=False)
    assert ctl.typed == [], "nothing may be delivered while priming"
    assert any("discarded" in s and "#w1" in s for s in said), said


def test_a_who_survives_the_members_default(peer):
    """⚠️ `members=None` is relay_tick's default and ~40 call sites omit it, so
    the fallback is the ordinary path in tests rather than an edge."""
    ctl = RelayCtl(panes(alice=bell("w1")))
    peer.relay_tick(ctl, PEOPLE, {}, [], 500, ctl.say,
                    Fetcher(who_from("alice")), notes={})
    bodies = [b for (_t, _p, b) in ctl.typed if b not in ("\n", "\r")]
    assert bodies == ["[chat from relay] you=alice peer=bob"], bodies


def test_the_answer_lists_the_roster_not_the_resolved_set(peer):
    """A row absent for a moment while agb-refresh re-mints it must not read as
    `left the chat` on everyone else's `who`."""
    ctl = RelayCtl(panes(alice=bell("w1")))
    peer.relay_tick(ctl, PEOPLE, {}, [], 500, ctl.say,
                    Fetcher(who_from("alice")), notes={},
                    members={"alice", "bob", "carol"})
    bodies = [b for (_t, _p, b) in ctl.typed if b not in ("\n", "\r")]
    assert bodies == ["[chat from relay] you=alice peer=bob peer=carol"], bodies


def test_a_refetched_who_is_answered_once(peer):
    """`done.add` runs before the branch, so a re-fetched request is deduped by
    the same set that stops a message being delivered twice."""
    ctl = RelayCtl(panes(alice=bell("w1")))
    notes = {}
    for _ in range(3):
        peer.relay_tick(ctl, PEOPLE, {}, [], 500, ctl.say,
                        Fetcher(who_from("alice")), notes=notes,
                        members={"alice", "bob"})
    bodies = [b for (_t, _p, b) in ctl.typed if b not in ("\n", "\r")]
    assert len(bodies) == 1, bodies


def test_an_ordinary_message_is_still_routed(peer):
    """Labelled regression companion: routing is covered by ~15 existing tests,
    so this is here to prove the intercept did not swallow the normal path."""
    ctl = RelayCtl(panes(alice=bell("m1")))
    peer.relay_tick(ctl, PEOPLE, {}, [], 500, ctl.say,
                    Fetcher(framed(("m1", "bob", "hello"))), notes={},
                    members={"alice", "bob"})
    bodies = [b for (_t, _p, b) in ctl.typed if b not in ("\n", "\r")]
    assert bodies == ["[chat from alice] hello"], bodies


# ---------------------------------------------------------------------------
# `agb-peer who` -- the agent side
# ---------------------------------------------------------------------------


class Recorder(object):
    """A `run` fake for cmd_who. Records argv; answers tmux like a live one."""

    def __init__(self, base="claude"):
        self.calls, self.options = [], {}
        self.base = base

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        if "show-options" in argv:
            return 0, self.options.get(argv[-1], ""), ""
        if "display-message" in argv:
            return 0, self.base, ""
        return 0, "", ""


def test_who_asks_the_relay_with_the_token(peer):
    run, out = Recorder(), io.StringIO()
    assert peer.cmd_who(run, out, env={"TMUX_PANE": "%7"}) == 0
    sets = [c for c in run.calls if c[1:2] == ["set"] and "-p" in c]
    assert sets, run.calls
    value = sets[-1][-1]
    assert value.split("\n")[0] == peer.RELAY_NAME
    assert value.split("\n")[1] == peer.WHO_REQUEST


def test_who_says_the_answer_arrives_later(peer):
    """The whole ergonomic risk of an asynchronous command: a reader who thinks
    the roster should have printed here will call it broken."""
    out = io.StringIO()
    peer.cmd_who(Recorder(), out, env={"TMUX_PANE": "%7"})
    assert "later turn" in out.getvalue()
    assert "no relay is running" in out.getvalue()


def test_who_refuses_outside_tmux_in_its_own_words(peer):
    """⚠️ cmd_send's message begins "send must run inside tmux", which reads as
    nonsense from `who`. Delegating first is what would leak it."""
    with pytest.raises(peer.PeerError) as caught:
        peer.cmd_who(Recorder(), io.StringIO(), env={})
    assert str(caught.value).startswith("who must run inside tmux")


def test_who_makes_no_agtermctl_call(peer, monkeypatch):
    """It runs on the agent's host, where agterm does not exist."""
    seen = []
    monkeypatch.setattr(peer, "run_ctl",
                        lambda *a, **k: seen.append(a) or (0, "", ""))
    peer.cmd_who(Recorder(), io.StringIO(), env={"TMUX_PANE": "%7"})
    assert seen == []


def test_the_agtermctl_tripwire_would_have_fired(peer, monkeypatch):
    """The companion. Without it the test above passes against a fake that can
    never record anything."""
    seen = []
    monkeypatch.setattr(peer, "run_ctl",
                        lambda *a, **k: seen.append(a) or (0, "", ""))
    peer.run_ctl(["tree", "--json"])
    assert seen, "the tripwire must be able to fire"


def test_who_is_dispatched_by_main(peer, monkeypatch):
    """⚠️ Not spelled as `pytest.raises(PeerError)`: an UNdispatched `who` would
    raise too, via `--to is required`, so that shape passes with cmd_who
    deleted. Assert who's OWN message instead."""
    tripwire = []
    monkeypatch.setattr(peer, "os", peer.os)
    monkeypatch.setattr(peer.os, "environ", {}, raising=False)
    monkeypatch.setattr(peer, "run_local",
                        lambda *a, **k: tripwire.append(a) or (0, "", ""))
    with pytest.raises(peer.PeerError) as caught:
        peer.main(["who"], io.StringIO(), None)
    assert str(caught.value).startswith("who must run inside tmux"), caught.value
    assert tripwire == [], "it must refuse before running anything"


def test_usage_lists_every_verb_main_dispatches(peer):
    """⚠️ Passes before this change too -- `send` and `relay` were already
    listed. Its value is prospective: it fails when a verb is added and its
    USAGE line is not. Kept deliberately, not free-on-branch dead weight."""
    import ast
    tree = ast.parse(io.open(PEER_PATH, encoding="utf-8").read())
    main = next(n for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == "main")
    verbs = set()
    for node in ast.walk(main):
        if (isinstance(node, ast.Compare) and node.comparators
                and isinstance(node.comparators[0], ast.Str)):
            verbs.add(node.comparators[0].s)
    assert verbs, "non-vacuous: the walk must find some"
    for verb in verbs:
        assert "agb-peer %s" % (verb,) in peer.USAGE, verb


# ---------------------------------------------------------------------------
# an UNQUOTED option value -- the shape the suite never exercised
# ---------------------------------------------------------------------------
#
# ⚠️ Found live, not here. tmux quotes an option value only when it has to, so
# `bob\nhello there` comes back quoted and `bob\nhello` comes back bare -- and
# BOTH render the newline as a literal backslash-n. Unescaping only the quoted
# form meant every single-word message was silently skipped for the life of this
# transport. `agb-peer who` hits it every time, its token being one word.


def test_a_bare_value_is_parsed(peer):
    got = peer.parse_show_options(framed_bare(("aaa", "bob", "hello")))
    assert got == [{"id": "aaa", "key": peer.OPTION_PREFIX + "aaa",
                    "to": "bob", "text": "hello"}], got


def test_a_quoted_value_is_still_parsed(peer):
    """The companion: the fix must not break the shape that always worked."""
    got = peer.parse_show_options(framed(("aaa", "bob", "hello there")))
    assert got == [{"id": "aaa", "key": peer.OPTION_PREFIX + "aaa",
                    "to": "bob", "text": "hello there"}], got


def test_a_single_word_message_survives_the_round_trip(peer):
    """⚠️ The user-visible bug: `agb-peer send --to bob hello` never arrived,
    while `--to bob 'hello there'` did. The difference was a space."""
    assert " " not in peer.option_value("bob", "hello")
    got = peer.parse_show_options(framed_bare(("aaa", "bob", "hello")))
    assert got and got[0]["text"] == "hello"


def test_a_who_request_survives_the_round_trip(peer):
    """The token is one word by design, so `who` was 100% lost."""
    value = peer.option_value(peer.RELAY_NAME, peer.WHO_REQUEST)
    assert " " not in value, "which is exactly why tmux leaves it unquoted"
    got = peer.parse_show_options(
        framed_bare(("w1", peer.RELAY_NAME, peer.WHO_REQUEST)))
    assert got and got[0]["to"] == peer.RELAY_NAME
    assert got[0]["text"] == peer.WHO_REQUEST


def test_a_bare_value_keeps_its_backslashes(peer):
    """Unescaping now runs on the bare form too, so it must not corrupt one."""
    got = peer.parse_show_options(framed_bare(("aaa", "bob", "a\\b")))
    assert got and got[0]["text"] == "a\\b", got


def test_nothing_non_ascii_reaches_stdout(peer):
    """⚠️ Found live, in a plain tcsh login shell: a warning glyph in `who`'s
    output raised UnicodeEncodeError AFTER the request had already been sent, so
    the message went out and the command reported a traceback.

    `sys.stdout` is `strict` in CPython and `-E` does not touch `LC_ALL`, so any
    non-ASCII byte written through `out` raises under an ASCII locale. The
    comments and docstrings in this file may hold anything -- and do -- but what
    leaves through `out.write` or `say` may not. CLAUDE.md records the same trap
    for `agb instances --arg` and `install-config --print-statedir`.
    """
    import ast
    tree = ast.parse(io.open(PEER_PATH, encoding="utf-8").read())
    offenders, calls = [], 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "write":
            pass
        elif isinstance(func, ast.Name) and func.id == "say":
            pass
        else:
            continue
        calls += 1
        for inner in ast.walk(node):
            if isinstance(inner, ast.Str) and any(ord(c) > 127 for c in inner.s):
                offenders.append((node.lineno, inner.s[:60]))
    assert calls > 20, "non-vacuous: the walk must actually find the calls"
    assert offenders == [], offenders


def test_the_docstrings_are_allowed_to_be_non_ascii(peer):
    """The companion. Without it the guard above would pass against a file that
    had been stripped of every ⚠️ -- which is most of how this codebase warns."""
    import ast
    tree = ast.parse(io.open(PEER_PATH, encoding="utf-8").read())
    rich = [n for n in ast.walk(tree)
            if isinstance(n, ast.Str) and any(ord(c) > 127 for c in n.s)]
    assert len(rich) > 10, "the file's own warnings should still be there"


# ---------------------------------------------------------------------------
# a doorbell that outlives its file
# ---------------------------------------------------------------------------
#
# ⚠️ Found live. The file transport names its file by the doorbell id, and the
# relay DELETES that file once it has read it -- but it cannot clear the
# doorbell, because that transport exists precisely because the tmux is
# unreachable. So every later relay primes on a stale id and fetches something
# rightly gone. Treated as a failure, `seen` never advanced and the retry
# re-fired at the relay interval for ever.


class RcFetcher(object):
    """A fetch fake that answers reads with a chosen exit status."""

    def __init__(self, rc):
        self.rc, self.calls = rc, []

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        if "cat" in argv:
            return self.rc, "", "boom"
        return 0, "", ""


def test_a_missing_file_is_gone_not_failed(peer):
    outcome, said = {}, []
    assert peer.drain(RcFetcher(1), "pool", "box", peer.NFS_TARGET, said.append,
                      ident="aaa", chat_dir="/s/chat", outcome=outcome) == []
    assert outcome["fetch"] == peer.FETCH_GONE
    assert any("already collected" in s for s in said), said


def test_an_unreachable_host_is_still_a_failure(peer):
    """⚠️ The companion, and the whole point of using the exit status: ssh's own
    255 must stay retryable, or a network blip would discard a real message."""
    outcome, said = {}, []
    assert peer.drain(RcFetcher(255), "pool", "box", peer.NFS_TARGET,
                      said.append, ident="aaa", chat_dir="/s/chat",
                      outcome=outcome) == []
    assert outcome["fetch"] == peer.FETCH_FAILED
    assert any("cannot reach" in s for s in said), said


def test_a_gone_doorbell_stops_the_relay_re_asking(peer):
    """The behaviour that matters: `seen` advances, so the next tick does not
    fetch again. Measured live as an ssh every two seconds, for ever."""
    ctl = RelayCtl(panes(alice=bell("aaa")))
    people = {"alice": ("AAAA1111", "left", "box", peer.NFS_TARGET),
              "bob": PEOPLE["bob"]}
    seen, notes = {}, {}
    fetch = RcFetcher(1)
    for _ in range(5):
        peer.relay_tick(ctl, people, seen, [], 500, ctl.say, fetch,
                        notes=notes, chat_dir="/s/chat")
    reads = [c for c in fetch.calls if "cat" in c]
    assert seen.get("alice") == "aaa", "the doorbell must be marked handled"
    assert len(reads) == 1, "five ticks, one fetch: %r" % (reads,)


def test_an_unreachable_host_keeps_retrying(peer):
    """The companion to the one above -- without it, "stops re-asking" would
    pass against a relay that gave up on everything."""
    ctl = RelayCtl(panes(alice=bell("aaa")))
    people = {"alice": ("AAAA1111", "left", "box", peer.NFS_TARGET),
              "bob": PEOPLE["bob"]}
    seen, notes = {}, {}
    fetch = RcFetcher(255)
    for _ in range(5):
        peer.relay_tick(ctl, people, seen, [], 500, ctl.say, fetch,
                        notes=notes, chat_dir="/s/chat")
    reads = [c for c in fetch.calls if "cat" in c]
    assert "alice" not in seen, "a transport failure must not be recorded as read"
    assert len(reads) == 5, "five ticks, five retries: %r" % (reads,)


# ---------------------------------------------------------------------------
# two doorbells between one tick -- the file transport's blind spot
# ---------------------------------------------------------------------------
#
# ⚠️ Found live: a Codex rang twice inside one relay interval and its FIRST
# message was orphaned in the chat directory for ever. `drain` sweeps every
# option off a pane, so a missed tmux doorbell is harmless; `drain_files` must
# fetch BY NAME, because the chat directory is shared by every participant and a
# message carries its recipient but not its sender -- a blind glob would credit
# one agent's message to whichever pane happened to ring.


class FileFetcher(object):
    """Serves `cat <dir>/<id>.msg` from a dict; `rm` removes. 255 = unreachable."""

    def __init__(self, files, unreachable=False):
        self.files, self.calls, self.unreachable = dict(files), [], unreachable

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        if self.unreachable:
            return 255, "", "ssh: could not resolve"
        path = argv[-1]
        ident = path.rsplit("/", 1)[-1][:-len(".msg")]
        if "cat" in argv:
            if ident not in self.files:
                return 1, "", "cat: %s: No such file or directory" % (path,)
            return 0, self.files[ident], ""
        if "rm" in argv:
            self.files.pop(ident, None)
        return 0, "", ""


def test_every_doorbell_on_the_screen_is_read(peer):
    fetch = FileFetcher({"aaa": "bob\nfirst", "bbb": "bob\nsecond"})
    got = peer.drain(fetch, "pool", "box", peer.NFS_TARGET, lambda m: None,
                     ident="bbb", chat_dir="/s/chat",
                     idents=["aaa", "bbb"])
    assert [m["text"] for m in got] == ["first", "second"], got


def test_only_the_announced_one_would_lose_the_other(peer):
    """The companion, and the bug as it was: announce only the newest and the
    earlier message is never fetched at all."""
    fetch = FileFetcher({"aaa": "bob\nfirst", "bbb": "bob\nsecond"})
    got = peer.drain(fetch, "pool", "box", peer.NFS_TARGET, lambda m: None,
                     ident="bbb", chat_dir="/s/chat")
    assert [m["text"] for m in got] == ["second"], got
    assert "aaa" in fetch.files, "the first message is still orphaned"


def test_an_already_delivered_id_is_not_re_fetched(peer):
    """Bounds the cost: a long transcript keeps every marker it ever printed, so
    without this each ring would cost an ssh per marker."""
    fetch = FileFetcher({"bbb": "bob\nsecond"})
    got = peer.drain(fetch, "pool", "box", peer.NFS_TARGET, lambda m: None,
                     ident="bbb", chat_dir="/s/chat", idents=["aaa", "bbb"],
                     done=set([("pool", "aaa")]))
    assert [m["text"] for m in got] == ["second"], got
    reads = [c for c in fetch.calls if "cat" in c]
    assert len(reads) == 1, "the known id must not be fetched: %r" % (reads,)


def test_a_transport_failure_on_any_id_is_still_a_failure(peer):
    """It must not be downgraded to `gone` just because it was one of several."""
    outcome = {}
    fetch = FileFetcher({}, unreachable=True)
    assert peer.drain(fetch, "pool", "box", peer.NFS_TARGET, lambda m: None,
                      ident="bbb", chat_dir="/s/chat", idents=["aaa", "bbb"],
                      outcome=outcome) == []
    assert outcome["fetch"] == peer.FETCH_FAILED


def test_a_repeated_marker_is_fetched_once(peer):
    """⚠️ Not hypothetical, and not a tidy-up: `skills/agb-peer/SKILL.md` tells a
    file-transport sender to REPEAT the printed doorbell in its own answer, so
    the same id is on the screen twice by design. The first fetch unlinks the
    file, so the second can only fail -- and it fails saying `normal after a
    relay restart`, which is a lie about what happened."""
    fetch = FileFetcher({"aaa": "bob\nonce"})
    got = peer.drain(fetch, "pool", "box", peer.NFS_TARGET, lambda m: None,
                     ident="aaa", chat_dir="/s/chat",
                     idents=["aaa", "aaa"])
    assert [m["text"] for m in got] == ["once"], got
    reads = [c for c in fetch.calls if "cat" in c]
    assert len(reads) == 1, "the repeat must not cost an ssh: %r" % (reads,)


def test_two_distinct_ids_are_still_both_fetched(peer):
    """The companion to the dedupe: it must collapse repeats, not neighbours."""
    fetch = FileFetcher({"aaa": "bob\nfirst", "bbb": "bob\nsecond"})
    got = peer.drain(fetch, "pool", "box", peer.NFS_TARGET, lambda m: None,
                     ident="bbb", chat_dir="/s/chat",
                     idents=["aaa", "bbb", "aaa"])
    assert [m["text"] for m in got] == ["first", "second"], got
    reads = [c for c in fetch.calls if "cat" in c]
    assert len(reads) == 2, "one cat per distinct id: %r" % (reads,)


def test_read_doorbells_returns_them_oldest_first(peer):
    text = "noise [peer #aaa] more\nlines [peer #bbb] end"
    assert peer.read_doorbells(text) == ["aaa", "bbb"]
    assert peer.read_doorbell(text) == "bbb", "the newest is still the newest"


def test_the_relay_reads_both_doorbells_off_a_pane(peer):
    """⚠️ Through relay_tick, not drain: the fix is only real if
    scan_participant passes EVERY id it saw. A test that calls drain with a
    hand-built list proves the parameter works and nothing about the wiring."""
    surfaces = dict(panes())
    surfaces["AAAA1111"] = COMPOSER + bell("aaa") + bell("bbb")
    ctl = RelayCtl(surfaces)
    people = {"alice": ("AAAA1111", "left", "box", peer.NFS_TARGET),
              "bob": PEOPLE["bob"]}
    fetch = FileFetcher({"aaa": "bob\nfirst", "bbb": "bob\nsecond"})
    pending = []
    peer.relay_tick(ctl, people, {}, pending, 500, ctl.say, fetch,
                    notes={}, chat_dir="/s/chat", members={"alice", "bob"})
    bodies = sorted(b for (_t, _p, b) in ctl.typed if b not in ("\n", "\r"))
    assert bodies == ["[chat from alice] first",
                      "[chat from alice] second"], bodies


# ---------------------------------------------------------------------------
# roster_bytes / render_roster_lines -- the primitives `agb-peer-setup` writes
# through. Plan: docs/plans/20260826-agb-peer-setup-roster-builder.md, Task 1.
# ---------------------------------------------------------------------------

def test_roster_bytes_returns_the_content(peer, tmp_path):
    path = tmp_path / "roster"
    path.write_bytes(b"alice=RowA\nbob=RowB\n")
    assert peer.roster_bytes(str(path)) == b"alice=RowA\nbob=RowB\n"


def test_roster_bytes_answers_absent_for_a_missing_file(peer, tmp_path):
    assert peer.roster_bytes(str(tmp_path / "nope")) is peer.ROSTER_ABSENT


def test_roster_bytes_answers_absent_for_enotdir(peer, tmp_path):
    """ENOTDIR is the same positive answer as ENOENT: there is no file there."""
    notafile = tmp_path / "roster"
    notafile.write_bytes(b"alice=RowA\n")
    assert peer.roster_bytes(str(notafile / "sub")) is peer.ROSTER_ABSENT


def test_roster_bytes_RAISES_on_an_unreadable_file(peer, tmp_path):
    """⚠️ The whole reason this is not `read_roster_file`.

    Folding unreadable into absent would make `write_roster_file` compare
    `None == None` and rename over a roster nobody could read -- the gate goes
    vacuous exactly when it matters. Invariant 12: "I could not answer" is not
    "the answer is nothing".
    """
    path = tmp_path / "roster"
    path.write_bytes(b"alice=RowA\n")
    os.chmod(str(path), 0o000)
    try:
        with pytest.raises(peer.PeerError):
            peer.roster_bytes(str(path))
    finally:
        os.chmod(str(path), 0o600)


def test_an_empty_file_is_not_the_absent_answer(peer, tmp_path):
    """`b""` is a file that really is there, and the relay cares: an empty
    roster gets `parse_roster_text`'s own refusal, not "no file"."""
    path = tmp_path / "roster"
    path.write_bytes(b"")
    assert peer.roster_bytes(str(path)) == b""
    assert peer.roster_bytes(str(path)) is not peer.ROSTER_ABSENT


def test_an_identical_rewrite_is_not_a_change(peer, tmp_path):
    """The gate compares BYTES, so re-saving the same content in an editor --
    new mtime, new inode after a `mv` -- must not read as a conflict. This is
    the property a `(mtime, size, inode)` key would get wrong."""
    path = tmp_path / "roster"
    path.write_bytes(b"alice=RowA\n")
    before = peer.roster_bytes(str(path))
    other = tmp_path / "other"
    other.write_bytes(b"alice=RowA\n")
    os.rename(str(other), str(path))
    assert peer.roster_bytes(str(path)) == before


ROUND_TRIP = [
    ("alice", ("myrow", "left", None, None)),
    ("bob", ("codex", "right", "poolnode07", None)),
    ("mac", ("macrow", "left", "local", "work")),
    ("scratchy", ("srow", "scratch", "box3", "%24")),
]


def test_render_round_trips_semantically(peer):
    """⚠️ SEMANTIC, not textual, and it has to be.

    `parse_participants` normalises a missing pane to "left" and a missing
    `@`/`:` to None, so `alice=myrow` and `alice=myrow:left` parse identically
    and only one can be rendered back. Asserting on the TEXT would pin whatever
    the implementation happened to emit -- a tautology. Covers all four
    transport shapes and all three pane kinds.
    """
    lines = peer.render_roster_lines(ROUND_TRIP)
    assert len(lines) == len(ROUND_TRIP)
    back = peer.parse_roster_text("\n".join(lines).encode("utf-8"), minimum=1)
    assert back == dict(ROUND_TRIP)


def test_render_omits_every_default(peer):
    """The canonical spelling, pinned against literals -- the companion the
    semantic round trip needs, because it is the half a round trip cannot see."""
    assert peer.render_roster_lines(
        [("alice", ("myrow", "left", None, None))]) == ["alice=myrow"]


def test_render_spells_each_optional_part(peer):
    assert peer.render_roster_lines(ROUND_TRIP) == [
        "alice=myrow",
        "bob=codex:right@poolnode07",
        "mac=macrow@local:work",
        "scratchy=srow:scratch@box3:%24",
    ]


def test_render_returns_str_not_bytes(peer):
    """A picker echoes the line it just built and the raw hatch pre-fills a
    prompt with it. `bytes` would print as `b'alice=row'`."""
    for line in peer.render_roster_lines(ROUND_TRIP):
        assert isinstance(line, str)


def test_render_refuses_a_tmux_target_with_no_ssh_target(peer):
    """⚠️ The grammar cannot express it: `<row>:<tmux>` with no `@` reparses as
    a PANE. Refuse where the entry is built, not three steps later."""
    with pytest.raises(peer.PeerError):
        peer.render_roster_lines([("x", ("r", "left", None, "tmux-session"))])


def test_render_keeps_the_lists_order(peer):
    """⚠️ The order is the LIST's, not a dict's.

    `parse_participants` returns a dict whose order is a CPython 3.6
    implementation detail -- and 3.6 is this project's floor -- so an editor
    that deletes "the second one" cannot be built on it.
    """
    entries = [("c", ("r3", "left", None, None)),
               ("a", ("r1", "left", None, None)),
               ("b", ("r2", "left", None, None))]
    assert peer.render_roster_lines(entries) == ["c=r3", "a=r1", "b=r2"]


def test_roster_conflict_is_a_peer_error(peer):
    """Catchable as either: the relay's handlers keep working, and the setup
    tool can single it out for recovery."""
    assert issubclass(peer.RosterConflict, peer.PeerError)
    assert peer.RosterConflict("x").code == 1


# ---------------------------------------------------------------------------
# write_roster_file / write_draft_file -- Task 2.
# ---------------------------------------------------------------------------

def test_write_roster_file_creates_when_absent(peer, tmp_path):
    path = str(tmp_path / "roster")
    peer.write_roster_file(path, ["alice=A", "bob=B"], peer.ROSTER_ABSENT)
    assert io.open(path, encoding="utf-8").read() == "alice=A\nbob=B\n"


def test_write_roster_file_replaces_on_a_matching_gate(peer, tmp_path):
    path = str(tmp_path / "roster")
    peer.write_roster_file(path, ["alice=A"], peer.ROSTER_ABSENT)
    peer.write_roster_file(path, ["alice=A", "bob=B"], peer.roster_bytes(path))
    assert io.open(path, encoding="utf-8").read() == "alice=A\nbob=B\n"


def test_a_stale_gate_refuses_and_leaves_the_file_byte_identical(peer, tmp_path):
    path = str(tmp_path / "roster")
    peer.write_roster_file(path, ["alice=A"], peer.ROSTER_ABSENT)
    stale = peer.roster_bytes(path)
    peer.write_roster_file(path, ["alice=A", "bob=B"], stale)   # somebody else
    now = peer.roster_bytes(path)
    with pytest.raises(peer.RosterConflict):
        peer.write_roster_file(path, ["mine=M"], stale)
    assert peer.roster_bytes(path) == now


def test_a_refused_write_leaves_no_temp_behind(peer, tmp_path):
    path = str(tmp_path / "roster")
    peer.write_roster_file(path, ["alice=A"], peer.ROSTER_ABSENT)
    with pytest.raises(peer.RosterConflict):
        peer.write_roster_file(path, ["mine=M"], b"something else")
    assert [f for f in os.listdir(str(tmp_path)) if ".tmp." in f] == []


def test_an_unreadable_roster_raises_ROSTER_CONFLICT_specifically(peer, tmp_path):
    """⚠️ The CLASS is the assertion, not merely that it raises.

    An unreadable roster is a reason not to write, exactly like a changed one.
    It arrives from `roster_bytes` as a bare `PeerError`, which sails past the
    caller's `except RosterConflict` -- so the draft never reaches a recovery
    file and is lost silently, with every test green. `pytest.raises(PeerError)`
    passes on the unconverted code and proves nothing.
    """
    path = str(tmp_path / "roster")
    peer.write_roster_file(path, ["alice=A"], peer.ROSTER_ABSENT)
    gate = peer.roster_bytes(path)
    os.chmod(path, 0o000)
    try:
        with pytest.raises(peer.RosterConflict):
            peer.write_roster_file(path, ["bob=B"], gate)
    finally:
        os.chmod(path, 0o600)


def test_the_written_mode_is_the_chosen_literal(peer, tmp_path):
    """0600, restated on every write -- so a roster loosened by hand tightens
    again on the next save. Asserted as a number because inheriting a
    precedent's default is how the mode question gets skipped."""
    path = str(tmp_path / "roster")
    peer.write_roster_file(path, ["alice=A"], peer.ROSTER_ABSENT)
    assert os.stat(path).st_mode & 0o777 == 0o600
    os.chmod(path, 0o644)
    peer.write_roster_file(path, ["alice=A", "bob=B"], peer.roster_bytes(path))
    assert os.stat(path).st_mode & 0o777 == 0o600


def test_the_mode_survives_a_umask_that_strips_owner_bits(peer, tmp_path):
    """⚠️ This is the test that makes `fchmod` non-vacuous, and it was added
    because a mutation proved the previous one was not.

    `O_CREAT`'s mode argument is umask-FILTERED. Under the ordinary 022 nobody
    notices, because 022 does not touch owner bits -- so deleting the `fchmod`
    left every mode assertion green. MEASURED under `umask 0600`: `O_CREAT`
    with 0600 produces **0000**, a roster the relay cannot read, and the
    `fchmod` is the only thing that fixes it.

    The general form is CLAUDE.md's: a test that exercises one value of the
    variable is a test that variable does not appear in.
    """
    old = os.umask(0o600)
    try:
        path = str(tmp_path / "roster")
        peer.write_roster_file(path, ["alice=A"], peer.ROSTER_ABSENT)
        assert os.stat(path).st_mode & 0o777 == 0o600
    finally:
        os.umask(old)


def test_the_file_ends_with_a_newline(peer, tmp_path):
    path = str(tmp_path / "roster")
    peer.write_roster_file(path, ["alice=A"], peer.ROSTER_ABSENT)
    assert io.open(path, encoding="utf-8").read().endswith("\n")


def test_two_temps_in_one_directory_do_not_collide(peer, tmp_path):
    """The concurrency this whole feature exists to survive: two setup
    sessions, or one plus a hand edit. `write_chat_file`'s fixed `.tmp` name
    would have them share one path and publish a torn read."""
    path = str(tmp_path / "roster")
    names = set(peer._roster_temp(path) for _ in range(200))
    assert len(names) == 200
    for name in names:
        assert os.path.dirname(name) == str(tmp_path)


def test_write_draft_file_is_ungated(peer, tmp_path):
    """⚠️ Its caller is the RosterConflict handler.

    A gated writer here would raise `RosterConflict` from inside the conflict
    handler, losing the draft it was called to save. The path was just minted
    and belongs to nobody, so there is nothing to compare against.
    """
    path = str(tmp_path / "roster.conflict.draft")
    peer.write_draft_file(path, ["alice=A", "bob=B"])
    assert io.open(path, encoding="utf-8").read() == "alice=A\nbob=B\n"
    peer.write_draft_file(path, ["changed=C"])           # no gate, no refusal
    assert io.open(path, encoding="utf-8").read() == "changed=C\n"


def _fn(peer_path, name):
    tree = ast.parse(io.open(peer_path, encoding="utf-8").read())
    found = [n for n in ast.walk(tree)
             if isinstance(n, ast.FunctionDef) and n.name == name]
    assert found, "%s is gone" % (name,)
    return found[0]


def test_both_writers_temp_then_rename(peer):
    """⚠️ An actual `rename` CALL, not a substring of the AST dump -- the
    existing guard's comment records that its first version passed against the
    docstring, which says "temp+rename"."""
    for name in ("write_roster_file", "write_draft_file", "_write_roster_temp"):
        fn = _fn(PEER_PATH, name)
        calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call)]
        attrs = [n.func.attr for n in calls if isinstance(n.func, ast.Attribute)]
        assert attrs, "%s makes no calls at all" % (name,)
        if name == "_write_roster_temp":
            assert "open" in attrs and "fchmod" in attrs, attrs
        else:
            assert "rename" in attrs, "%s must rename: %s" % (name, attrs)


def test_write_draft_file_consults_no_gate(peer):
    """The structural half of "ungated": it must not read the target back."""
    fn = _fn(PEER_PATH, "write_draft_file")
    names = [n.id for n in ast.walk(fn) if isinstance(n, ast.Name)]
    assert names, "write_draft_file references nothing"
    assert "roster_bytes" not in names, names
    assert "RosterConflict" not in names, names


def test_write_roster_file_converts_to_roster_conflict(peer):
    """The structural companion to the unreadable test: the handler exists."""
    fn = _fn(PEER_PATH, "write_roster_file")
    raised = [n.exc.func.id for n in ast.walk(fn)
              if isinstance(n, ast.Raise) and isinstance(n.exc, ast.Call)
              and isinstance(n.exc.func, ast.Name)]
    assert raised, "write_roster_file raises nothing"
    assert set(raised) == {"RosterConflict"}, raised


def test_agb_peer_never_imports_tempfile(peer):
    """⚠️ Measured at 12-14 ms on this project's 3.6.8 floor, because it pulls
    `shutil` -- and `agb-peer send` is parsed by every agent on every message.
    Same order as the `argparse` import this project already refuses.
    `_roster_temp` is what replaces it."""
    tree = ast.parse(io.open(PEER_PATH, encoding="utf-8").read())
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
    assert imported, "no imports found at all -- the walk is broken"
    assert "tempfile" not in imported, imported


# ---------------------------------------------------------------------------
# dashboard_cells and the stdout-carrying Ctl.dashboard -- agb-dashboard Task 1.
# Plan: docs/plans/20260827-agb-dashboard.md
# ---------------------------------------------------------------------------

def test_dashboard_cells_never_emits_a_bare_id(peer):
    """⚠️ Not a style rule. A bare id takes EVERY pane of its session and the
    9-cap counts panes, so a row somebody opened a split on costs two cells --
    the same rows fit or do not depending on state nobody is looking at."""
    cells, _excluded = peer.dashboard_cells([("AAAA", "left"), ("BBBB", "right")])
    assert cells, "nothing was built"
    for cell in cells:
        assert ":" in cell, cell


def test_dashboard_cells_preserves_the_pane(peer):
    """⚠️ Preserved, not forced to `left`. A participant may legitimately live
    in the right-hand pane, and rewriting it points the cell at the wrong half
    of somebody's screen."""
    cells, _ = peer.dashboard_cells([("AAAA", "left"), ("BBBB", "right")])
    assert cells == ["AAAA:left", "BBBB:right"]


def test_dashboard_cells_keeps_the_given_order(peer):
    cells, _ = peer.dashboard_cells(
        [("CCCC", "left"), ("AAAA", "left"), ("BBBB", "right")])
    assert cells == ["CCCC:left", "AAAA:left", "BBBB:right"]


def test_dashboard_cells_reports_what_it_dropped(peer):
    """A list of strings cannot say what is NOT in it, and two callers need to
    know: the relay reports the absence, agb-dashboard refuses on it."""
    cells, excluded = peer.dashboard_cells(
        [("AAAA", "left"), ("BBBB", "scratch")])
    assert cells == ["AAAA:left"]
    assert excluded == [("BBBB", "scratch")]


def test_the_dashboard_pane_vocabulary_is_narrower_than_PANE_KINDS(peer):
    """⚠️ The gap is the point, and it is one constant wide.

    A participant may be in the scratch drawer -- `PANE_KINDS` allows it and
    `agb pane`'s `[d]` puts an agent there -- while agterm's grid documents
    only `:left`/`:right`. `DASHBOARD_PANES` is the single line that a
    measurement of `:scratch` would change; this test pins that it IS narrower,
    not which way the measurement went.
    """
    assert set(peer.DASHBOARD_PANES) <= set(peer.PANE_KINDS)
    assert "left" in peer.DASHBOARD_PANES and "right" in peer.DASHBOARD_PANES


def test_dashboard_cells_excludes_every_pane_outside_the_vocabulary(peer):
    """Parametrised over the constant rather than over a literal, so the
    measurement can move `DASHBOARD_PANES` without this becoming a lie -- and
    asserted non-empty first, or it would pass by testing nothing."""
    outside = [k for k in peer.PANE_KINDS if k not in peer.DASHBOARD_PANES]
    assert outside, "no pane is outside the vocabulary -- premise gone"
    pairs = [("AAAA", "left")] + [("X%d" % i, k) for i, k in enumerate(outside)]
    cells, excluded = peer.dashboard_cells(pairs)
    assert cells == ["AAAA:left"]
    assert [p for _r, p in excluded] == outside


def test_the_cap_is_agterms_own_and_counts_agents_once_panes_are_explicit(peer):
    assert peer.DASHBOARD_MAX_CELLS == 9
    cells, _ = peer.dashboard_cells([("R%d" % i, "left") for i in range(9)])
    assert len(cells) == peer.DASHBOARD_MAX_CELLS


def test_Ctl_dashboard_returns_stdout(peer):
    """⚠️ The whole reason it is a three-tuple.

    agterm exits 0 when only SOME cells resolve, opens the grid without the
    rest, and names the casualties on STDOUT alone. A caller that sees only the
    status gets a grid quietly missing the agent it was opened to watch.
    """
    seen = []

    def run(argv):
        seen.append(list(argv))
        return 0, "unresolved: DEADBEEF\n", ""

    ok, out, why = peer.Ctl(run=run).dashboard(["AAAA:left", "DEADBEEF:left"])
    assert seen == [["dashboard", "AAAA:left", "DEADBEEF:left"]]
    assert ok is True, "agterm really does exit 0 for a partial grid"
    assert "unresolved: DEADBEEF" in out
    assert why == "exit 0" or why == "", why


def test_Ctl_dashboard_close_asks_for_the_one_grid(peer):
    seen = []

    def run(argv):
        seen.append(list(argv))
        return 0, "", ""

    ok, _why = peer.Ctl(run=run).dashboard_close()
    assert ok is True
    assert seen == [["dashboard", "--close"]]


def test_the_relay_still_opens_a_grid_after_the_return_shape_changed(peer):
    """The conversion of `Ctl.dashboard`'s only caller, pinned. Without it that
    line raises ValueError: too many values to unpack."""
    src = io.open(PEER_PATH, encoding="utf-8").read()
    tree = ast.parse(src)
    fns = [n for n in ast.walk(tree)
           if isinstance(n, ast.FunctionDef) and n.name == "cmd_relay"]
    assert fns, "cmd_relay is gone"
    targets = [n for n in ast.walk(fns[0]) if isinstance(n, ast.Assign)]
    assert targets, "cmd_relay assigns nothing"
    unpacks = [t for t in targets
               if isinstance(t.targets[0], ast.Tuple)
               and isinstance(t.value, ast.Call)
               and isinstance(t.value.func, ast.Attribute)
               and t.value.func.attr == "dashboard"]
    assert unpacks, "no ctl.dashboard(...) unpack found in cmd_relay"
    for u in unpacks:
        assert len(u.targets[0].elts) == 3, "must unpack (ok, out, why)"


# ---------------------------------------------------------------------------
# The relay builds its cells through dashboard_cells -- agb-dashboard Task 2b-i.
# Plan: docs/plans/20260827-agb-dashboard.md
# ---------------------------------------------------------------------------

def test_the_relays_cells_are_unchanged_by_the_routing(peer):
    """⚠️ The no-behaviour-change proof, and the only thing 2b-i owes.

    The inline comprehension this replaced spelled the same string for a
    `left`/`right` participant, so a green suite proves nothing unless a test
    pins the OUTPUT rather than the route. `right` is in here deliberately: a
    routing that forced `:left` would still pass over an all-`left` roster.
    """
    ctl = RelayCtl(panes())
    peer.cmd_relay(ctl, ["alice=AAAA1111", "bob=BBBB2222:right"], 500, 0, True,
                   io.StringIO(), dashboard=True, fetch=Fetcher())
    assert ctl.dashboards == [["AAAA1111:left", "BBBB2222:right"]]


# A cell is `<id>:<pane>`, and the pane half is load-bearing: a BARE id takes
# every pane of its session, so agterm's 9-cell cap starts counting somebody
# else's split. `dashboard_cells` is where that spelling lives, and this guard
# is what stops a second caller growing its own copy -- which is exactly how
# `cmd_relay` and `agb-dashboard` would drift apart.
#
# ⚠️ It spans TWO files. `agb-dashboard` is created by Task 3 of the plan and
# does not exist yet; an absent tree is skipped rather than failed, which is
# why the non-vacuity assertions below matter -- without them the guard would
# read a missing file as a clean bill of health for both.
#
# ⚠️ Two walks, one tree each, NOT `conftest.functions(peer_tree, dash_tree)`:
# that helper raises on any non-dunder name defined in two trees, and both
# files define `main`.

# A cell FORMAT is placeholders joined by colons and nothing else -- `"%s:%s"`,
# `"{}:{}"`. Deliberately not every string containing a colon: `render_roster`
# legitimately builds `":%s"` fragments for the roster grammar, which is a
# different string that happens to share a character.
CELL_FORMAT = re.compile(r"^(%s|\{\d*\})(:(%s|\{\d*\}))+$")


def _owned_strings(tree):
    """{owning function name or None: [str literals]}, innermost owner wins.

    Attributing a literal to the innermost `def` is what makes the answer
    "which function spells this", rather than "does the file contain it" --
    the second is the substring grep this file's conventions forbid.
    """
    owned = {}

    def visit(node, owner):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef):
                visit(child, child.name)
                continue
            if isinstance(child, ast.Str):
                owned.setdefault(owner, []).append(child.s)
            visit(child, owner)

    visit(tree, None)
    return owned


def test_cell_strings_are_spelled_only_in_dashboard_cells():
    from conftest import DASH_PATH, PEER_PATH as CONF_PEER_PATH

    checked, found = [], []
    for path in (CONF_PEER_PATH, DASH_PATH):
        if not os.path.exists(path):
            # Task 3 creates `agb-dashboard`. Until then there is one emitter,
            # and skipping is right -- but only because `found` below still has
            # to be non-empty.
            continue
        checked.append(path)
        tree = ast.parse(io.open(path, encoding="utf-8").read(), filename=path)
        for owner, literals in _owned_strings(tree).items():
            for text in literals:
                if CELL_FORMAT.match(text):
                    found.append((os.path.basename(path), owner, text))

    assert checked, "neither cell emitter was parsed -- the guard covered nothing"
    assert found, "no cell format found at all -- the pattern stopped matching"
    strays = [f for f in found if f[1] != "dashboard_cells"]
    assert not strays, (
        "a cell string is built outside dashboard_cells: %r" % (strays,))


def test_the_relay_asks_dashboard_cells_for_its_cells():
    """The complement of the guard above: absence of a stray format proves
    nothing on its own, because a caller could pass a bare id and never spell a
    colon at all. `cmd_relay` must actually CALL the one builder."""
    tree = ast.parse(io.open(PEER_PATH, encoding="utf-8").read())
    fns = [n for n in ast.walk(tree)
           if isinstance(n, ast.FunctionDef) and n.name == "cmd_relay"]
    assert fns, "cmd_relay is gone"
    names = [n.func.id for n in ast.walk(fns[0])
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
    assert names, "cmd_relay calls no bare-name function -- walk is wrong"
    assert "dashboard_cells" in names


# ---------------------------------------------------------------------------
# A scratch participant is dropped from the grid, and SAID -- Task 2b-ii.
# Plan: docs/plans/20260827-agb-dashboard.md
#
# agterm refuses a `:scratch` cell at parse time (measured 2026-08-27), so the
# whole `dashboard` call failed and the relay opened NO GRID AT ALL -- every
# participant lost, because one of them was in the drawer. Routing through
# `dashboard_cells` fixed that; these pin the half that is left, which is that
# the drop must not be silent.
# ---------------------------------------------------------------------------

def test_a_scratch_participant_does_not_stop_the_grid_opening(peer):
    ctl = RelayCtl(dict(panes(), CCCC3333=COMPOSER))
    peer.cmd_relay(ctl, ["alice=AAAA1111", "bob=BBBB2222",
                         "carol=CCCC3333:scratch"], 500, 0, True,
                   io.StringIO(), dashboard=True, fetch=Fetcher())
    assert ctl.dashboards == [["AAAA1111:left", "BBBB2222:left"]]


def test_an_excluded_participant_is_named(peer):
    """⚠️ By NAME, not by row id. The operator wrote `carol=...:scratch`; a
    hex prefix would make them go and look up which participant vanished.

    ⚠️ Asserted on the REPORT LINE, not on the whole output, and that is what
    the mutation check found: the relay already prints `carol  CCCC3333:scratch`
    in the roster it lists every re-resolve, so `"carol" in out.getvalue()` was
    green with the report deleted.
    """
    out = io.StringIO()
    ctl = RelayCtl(dict(panes(), CCCC3333=COMPOSER))
    peer.cmd_relay(ctl, ["alice=AAAA1111", "bob=BBBB2222",
                         "carol=CCCC3333:scratch"], 500, 0, True, out,
                   dashboard=True, fetch=Fetcher())
    report = [line for line in out.getvalue().splitlines()
              if "not shown" in line]
    assert len(report) == 1, out.getvalue()
    assert "carol" in report[0] and "scratch" in report[0], report[0]


def test_nothing_is_said_when_every_participant_fits(peer):
    """The companion the "nothing happened" assertion needs: this differs from
    the test above in the one variable under test, so it cannot pass against a
    report that can never fire."""
    out = io.StringIO()
    ctl = RelayCtl(panes())
    peer.cmd_relay(ctl, ["alice=AAAA1111", "bob=BBBB2222"], 500, 0, True, out,
                   dashboard=True, fetch=Fetcher())
    assert "not shown" not in out.getvalue(), out.getvalue()


def test_the_exclusion_is_reported_once_not_per_reopen(peer, tmp_path):
    """⚠️ The grid is re-opened on every membership change, and the excluded
    participant is still excluded each time. Unthrottled, a long-lived relay
    repeats the same line for ever and buries everything else it says."""
    path = roster_file(
        tmp_path, "alice=AAAA1111\nbob=BBBB2222\ncarol=CCCC3333:scratch\n")
    surfaces = dict(panes(), CCCC3333=COMPOSER, DDDD4444=COMPOSER)

    def edit(tick):
        if tick == 1:
            open(path, "w").write("alice=AAAA1111\nbob=BBBB2222\n"
                                  "carol=CCCC3333:scratch\ndave=DDDD4444\n")
    ctl = TickingCtl(surfaces, edit)
    out = io.StringIO()
    peer.cmd_relay(ctl, [], 500, 8, False, out, roster=path, ticks=3,
                   dashboard=True, fetch=Fetcher())
    assert len(ctl.dashboards) > 1, (
        "the grid never re-opened -- the throttle was not exercised")
    assert out.getvalue().count("not shown") == 1, out.getvalue()
