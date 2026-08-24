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
        if "show-options" in argv:
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

def bell(ident):
    return "\nclaude [peer #%s]\n" % (ident,)


def test_a_new_doorbell_triggers_a_fetch_and_a_delivery(peer):
    ctl = RelayCtl(panes(alice=bell("aaa")))
    fetch = Fetcher(framed(("aaa", "bob", "hello")))
    peer.relay_tick(ctl, PEOPLE, {}, [], 500, ctl.say, fetch)
    assert fetch.calls, "the doorbell rang and nothing was fetched"
    bodies = [t for (_, _, t) in ctl.typed if t != "\n"]
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
    assert [t for (_, _, t) in ctl.typed if t != "\n"] == [
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
    assert [t for (_, _, t) in ctl.typed if t != "\n"] == ["[chat from alice] held"]


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


def test_the_skill_carries_the_deadlock_rule(peer):
    """agterm's own cookbook calls this out, and it is the failure mode this
    arrangement is most prone to: two agents each waiting on the other."""
    body = io.open(SKILL_PATH, encoding="utf-8").read().lower()
    assert "deadlock" in body
    assert "never poll" in body


def test_the_claude_specific_constants_are_marked_as_such(peer):
    """They read as universal and are not, which is how a Codex participant
    would silently be classified as "not an agent" and never written to.

    Pinned as a test rather than left as a comment because the comment is the
    only thing standing between the next reader and a guessed second profile --
    and this project's rule is that agterm behaviour is measured, not read.
    """
    body = io.open(PEER_PATH, encoding="utf-8").read()
    head = body[:body.index('COMPOSER_GLYPH = "❯"')]
    assert "CLAUDE CODE'S, not every agent's" in head
    for name in ("COMPOSER_GLYPH", "EMPTY_COLUMN", "submit key"):
        assert name in head, name


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
    bodies = [t for (_, _, t) in ctl.typed if t != "\n"]
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


def test_a_usual_home_is_preferred_over_the_bare_name(peer, monkeypatch):
    """agterm spawns `bash --noprofile --norc`, so a pane inherits only what
    `login` gives it -- NOT the user's PATH. Measured: an agent inside tmux
    inside agterm has $TMUX_PANE set and still cannot exec `tmux`."""
    monkeypatch.setattr(peer.os, "access",
                        lambda p, m: p == "/opt/homebrew/bin/tmux")
    assert peer.tmux_binary({}) == "/opt/homebrew/bin/tmux"


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
