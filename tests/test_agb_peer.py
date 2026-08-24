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

    def sleep(self, seconds):
        self.sleeps += 1

    def tree(self):
        return tree_of(*[session(row) for row in sorted(self.current)])

    def text(self, target, pane, lines, whole=False):
        return self.current.get(target, ""), None

    def cursor(self, surface):
        value = self.cursors[0] if len(self.cursors) == 1 else self.cursors.pop(0)
        return value, None

    def type(self, target, pane, text):
        self.typed.append((target, pane, text))
        self.current[target] = self.current.get(target, "") + "\n" + text
        return True

    def say(self, message):
        self.said.append(message)


PEOPLE = {"alice": ("AAAA1111", "left"), "bob": ("BBBB2222", "left")}


def panes(alice="", bob=""):
    return {"AAAA1111": COMPOSER + alice, "BBBB2222": COMPOSER + bob}


# ------------------------------------------------------------ encode/decode

def test_the_wire_format_is_readable(peer):
    """The requirement, not an aesthetic: these lines land in a human's
    transcript. Every field is plain and the text is verbatim."""
    line = peer.encode("bob", "hello there")
    assert line.startswith("[peer bob ")
    assert line.endswith("] hello there")
    assert "=" not in line.split("]")[0], "the header stays positional and short"


def test_a_message_round_trips(peer):
    got = peer.find_markers(peer.encode("bob", "hello there"))
    assert len(got) == 1
    assert got[0]["to"] == "bob"
    assert got[0]["text"] == "hello there"
    assert got[0]["ok"]


def test_a_long_message_is_split_on_word_boundaries(peer):
    """Never mid-word, because rejoining a split word has to guess about a
    space -- and guessing is the whole thing this format avoids."""
    text = " ".join("word%d" % i for i in range(40))
    lines = peer.encode("bob", text).splitlines()
    assert len(lines) > 1, "the harness message was not long enough to split"
    for line in lines:
        body = line.split("] ", 1)[1]
        assert all(w in text.split() for w in body.split())
    assert peer.find_markers("\n".join(lines))[0]["text"] == text


def test_every_chunk_fits_an_eighty_column_pane(peer):
    # If header + chunk exceeded the narrowest pane anyone runs, the format
    # would corrupt itself by construction.
    text = " ".join("word%d" % i for i in range(60))
    for line in peer.encode("bob", text).splitlines():
        assert len(line) <= 80, line


def test_a_word_longer_than_a_chunk_is_left_intact(peer):
    long_word = "x" * 120
    got = peer.find_markers(peer.encode("b", "see " + long_word))
    assert got[0]["text"] == "see " + long_word


def test_an_incomplete_message_is_not_returned_yet(peer):
    # A pane caught mid-print. Not an error -- the next poll sees the rest.
    lines = peer.encode("b", " ".join("word%d" % i for i in range(40)))
    partial = "\n".join(lines.splitlines()[:-1])
    assert peer.find_markers(partial) == []


def test_a_chunk_that_wrapped_is_refused_not_delivered_short(peer):
    """The one corruption the format cannot prevent, made loud.

    A pane narrower than the chunk width wraps a line, and the continuation
    has no header, so it is dropped. The reassembly is then SHORT -- and the
    declared length is what catches it.
    """
    lines = peer.encode("b", "one two three four five six seven").splitlines()
    # simulate the continuation of the last chunk being lost to a wrap
    lines[-1] = lines[-1][:len(lines[-1]) - 6]
    got = peer.find_markers("\n".join(lines))
    assert len(got) == 1
    assert got[0]["ok"] is False, "a short reassembly must be flagged"


def test_a_repeated_line_is_not_a_second_message(peer):
    # Claude Code shows a command and its output, so the same line can appear
    # twice on one screen.
    line = peer.encode("b", "echoed twice")
    got = peer.find_markers(line + "\n" + line)
    assert len(got) == 1 and got[0]["text"] == "echoed twice"


def test_prose_that_merely_looks_like_a_header_is_ignored(peer):
    for junk in ["[peer bob] hi", "[peer bob abc 1/1] hi", "peer bob x 1/1 2] hi",
                 "  [peer bob x 1/1 2] indented so not at line start"]:
        assert peer.find_markers(junk) == [], junk


def test_ids_are_short_readable_and_distinct(peer):
    early, late = peer.message_id(1.0), peer.message_id(2.0)
    assert early != late
    assert early.isalnum() and len(early) < 12


def test_ids_are_not_sortable_and_the_code_must_not_assume_they_are(peer):
    """Written as a test because it is a trap, not a property.

    Base36 of a millisecond clock stops being lexicographically ordered as
    soon as the digit count changes -- and real timestamps all render eight
    digits until about 2059, so a sort would look correct for decades. The
    relay compares ids for equality only; this pins that it may keep doing so.
    """
    assert peer.message_id(1e12) < peer.message_id(1.0), (
        "if this ever becomes True the ids were made sortable -- good, but "
        "update the note in message_id() that says they are not")


def test_chunk_words_keeps_every_word_and_their_order(peer):
    words = ["alpha", "beta", "gamma", "delta", "epsilon"] * 6
    lines = peer.chunk_words(" ".join(words), 20)
    assert " ".join(lines).split() == words


def test_chunk_words_on_an_empty_message_is_empty(peer):
    assert peer.chunk_words("   ") == []


# ------------------------------------------------------ parse_participants

def test_participants_default_to_the_left_pane(peer):
    assert peer.parse_participants(["a=R1", "b=R2"]) == {
        "a": ("R1", "left"), "b": ("R2", "left")}


def test_a_pane_suffix_is_agterms_own_spelling(peer):
    assert peer.parse_participants(["a=R1:right", "b=R2"])["a"] == ("R1", "right")


@pytest.mark.parametrize("words", [
    ["a=R1"],                       # one participant is not a conversation
    ["aR1", "b=R2"],                # no `=`
    ["a=", "b=R2"],                 # no row
    ["=R1", "b=R2"],                # no name
    ["a=R1:primary", "b=R2"],       # a pane agterm rejects
    ["a=R1", "a=R2"],               # named twice
])
def test_a_malformed_participant_list_is_refused(peer, words):
    with pytest.raises(peer.PeerError):
        peer.parse_participants(words)


# ------------------------------------------------------------- the relay

def test_priming_delivers_nothing(peer):
    """A pane's scrollback holds the whole conversation.

    A relay that delivered on its first look would replay every message ever
    sent into somebody's composer. The first pass marks everything seen and
    sends none of it.
    """
    ctl = RelayCtl(panes(alice=peer.encode("bob", "old news")))
    seen, pending = set(), []
    peer.relay_tick(ctl, PEOPLE, seen, pending, 500, ctl.say, deliver_new=False)
    assert len(seen) == 1, "it must still REMEMBER what it saw"
    assert ctl.typed == [], "and it must not have delivered any of it"
    assert pending == []


def test_a_marker_that_appears_after_priming_is_delivered(peer):
    ctl = RelayCtl(panes())
    seen, pending = set(), []
    peer.relay_tick(ctl, PEOPLE, seen, pending, 500, ctl.say, deliver_new=False)
    ctl.current["AAAA1111"] += "\n" + peer.encode("bob", "new news")
    peer.relay_tick(ctl, PEOPLE, seen, pending, 500, ctl.say)
    bodies = [t for (target, pane, t) in ctl.typed if t != "\n"]
    assert bodies == ["[chat from alice] new news"]
    assert ctl.typed[0][0] == "BBBB2222", "it must go to the RECIPIENT's row"


def test_a_message_is_delivered_once_however_often_the_pane_is_read(peer):
    ctl = RelayCtl(panes(alice=peer.encode("bob", "once please")))
    seen, pending = set(), []
    for _ in range(4):
        peer.relay_tick(ctl, PEOPLE, seen, pending, 500, ctl.say)
    assert len([t for (_, _, t) in ctl.typed if t != "\n"]) == 1


def test_the_relay_never_types_a_marker(peer):
    """Loop suppression, and it is structural rather than a rule.

    What the relay types lands on the recipient's screen and is read again on
    the next poll. If it echoed the marker the message would bounce for ever,
    so it types the DECODED prose and the recipient's pane matches nothing.
    """
    ctl = RelayCtl(panes(alice=peer.encode("bob", "no loops")))
    seen, pending = set(), []
    for _ in range(5):
        peer.relay_tick(ctl, PEOPLE, seen, pending, 500, ctl.say)
    for _, _, text in ctl.typed:
        assert peer.PEER_TAG not in text
    assert len([t for (_, _, t) in ctl.typed if t != "\n"]) == 1, \
        "five passes over a pane holding the delivered text produced one send"


def test_the_sender_is_the_pane_and_the_wire_carries_no_other_claim(peer):
    """A pane is a place, and an agent cannot print into another agent's pane.

    So the participant name of the pane a message was found in is the only part
    of the envelope that cannot be misstated, and it is what signs the message.
    There is deliberately no sender field on the wire to disagree with it --
    this pins both halves: the line carries no name, and the delivered body
    carries the pane's.
    """
    line = peer.encode("bob", "trust me")
    assert "alice" not in line and "eve" not in line, \
        "the wire must carry no sender at all"
    ctl = RelayCtl(panes(alice=line))
    seen, pending = set(), []
    peer.relay_tick(ctl, PEOPLE, seen, pending, 500, ctl.say)
    bodies = [t for (_, _, t) in ctl.typed if t != "\n"]
    assert bodies == ["[chat from alice] trust me"]


def test_a_busy_recipient_holds_the_message_instead_of_blocking(peer):
    """The direct command waits 40 s for a busy peer; a relay must not.

    A refusal leaves the message pending and the next tick tries again, so one
    busy participant cannot stall every other conversation.
    """
    ctl = RelayCtl(panes(alice=peer.encode("bob", "held")),
                   cursors=[41, 2])
    seen, pending = set(), []
    left = peer.relay_tick(ctl, PEOPLE, seen, pending, 500, ctl.say)
    assert left == 1 and ctl.typed == [], "nothing may be typed into a dirty composer"
    peer.relay_tick(ctl, PEOPLE, seen, pending, 500, ctl.say)
    assert [t for (_, _, t) in ctl.typed if t != "\n"] == ["[chat from alice] held"]
    assert pending == []


def test_a_detached_participant_is_reported_and_read_as_silent(peer):
    """The failure this will hit most often, and it must not look like quiet.

    A farm participant whose row got detached shows `agb pane`'s menu. A menu
    holds no markers, so silence there means "gone", not "nothing to say".
    """
    ctl = RelayCtl({"AAAA1111": MENU, "BBBB2222": COMPOSER})
    seen, pending = set(), []
    peer.relay_tick(ctl, PEOPLE, seen, pending, 500, ctl.say)
    assert any("menu" in line for line in ctl.said)
    assert ctl.typed == []


def test_a_message_to_a_stranger_is_dropped_with_a_reason(peer):
    ctl = RelayCtl(panes(alice=peer.encode("carol", "who?")))
    seen, pending = set(), []
    peer.relay_tick(ctl, PEOPLE, seen, pending, 500, ctl.say)
    assert ctl.typed == []
    assert pending == [], "an undeliverable message must not accumulate for ever"
    assert any("carol" in line for line in ctl.said)


def test_a_self_addressed_message_is_dropped(peer):
    ctl = RelayCtl(panes(alice=peer.encode("alice", "hello me")))
    seen, pending = set(), []
    peer.relay_tick(ctl, PEOPLE, seen, pending, 500, ctl.say)
    assert ctl.typed == []
    assert pending == []


def test_the_relay_reads_scrollback_not_just_the_screen(peer):
    """`--all`, because a marker can scroll off before the next poll."""
    seen_calls = []

    class Recorder(RelayCtl):
        def text(self, target, pane, lines, whole=False):
            seen_calls.append(whole)
            return RelayCtl.text(self, target, pane, lines)

    ctl = Recorder(panes())
    peer.relay_tick(ctl, PEOPLE, set(), [], 500, ctl.say, deliver_new=False)
    assert seen_calls, "no pane was read"
    assert all(seen_calls), "every relay read must pass --all"


# ------------------------------------------------------------- the send verb

def test_send_prints_a_marker_and_touches_no_agtermctl(peer):
    out = io.StringIO()
    assert peer.main(["send", "--to", "bob", "hi"], out, None) == 0
    got = peer.find_markers(out.getvalue())
    assert len(got) == 1 and got[0]["to"] == "bob" and got[0]["text"] == "hi"


def test_send_refuses_an_empty_message(peer):
    with pytest.raises(peer.PeerError):
        peer.main(["send", "--to", "bob", "   "], io.StringIO(), None)


def test_send_requires_a_recipient(peer):
    with pytest.raises(peer.PeerError):
        peer.main(["send", "hi"], io.StringIO(), None)


def test_cmd_relay_primes_before_it_delivers(peer):
    """The priming gap `relay_tick`'s own test cannot see.

    `test_priming_delivers_nothing` calls `relay_tick(deliver_new=False)`
    directly, so it proves the parameter works and says nothing about whether
    `cmd_relay` passes it. Flipping that call to True left every relay_tick
    test green while a real relay replayed the whole scrollback on startup --
    found by mutation, not by reading.
    """
    ctl = RelayCtl(panes(alice=peer.encode("bob", "old news")))
    out = io.StringIO()
    rc = peer.cmd_relay(ctl, ["alice=AAAA1111", "bob=BBBB2222"], 500, 0, True,
                        out)
    assert rc == 0
    assert ctl.typed == [], "the first look must deliver nothing"
    assert "primed: 1" in out.getvalue()


def test_cmd_relay_reports_what_each_name_resolved_to(peer):
    ctl = RelayCtl(panes())
    out = io.StringIO()
    peer.cmd_relay(ctl, ["alice=AAAA1111", "bob=BBBB2222"], 500, 0, True, out)
    body = out.getvalue()
    assert "alice" in body and "bob" in body and "AAAA1111"[:8] in body


def test_send_refuses_a_from_rather_than_ignoring_it(peer):
    # A flag that silently does nothing is worse than no flag: it looks like
    # it changed who the message is signed by, and cannot.
    with pytest.raises(peer.PeerError):
        peer.main(["send", "--to", "bob", "--from", "eve", "hi"], io.StringIO(),
                  None)


def test_send_writes_the_message_in_plain_sight(peer):
    """No companion echo line: the wire format is already readable, and a
    second copy would only be a second thing to keep in step."""
    out = io.StringIO()
    peer.main(["send", "--to", "bob", "hello there"], out, None)
    body = out.getvalue()
    assert "hello there" in body
    assert len([l for l in body.splitlines() if l.strip()]) == 1



def test_what_send_prints_is_exactly_one_message(peer):
    out = io.StringIO()
    peer.main(["send", "--to", "bob", "hello there"], out, None)
    assert len(peer.find_markers(out.getvalue())) == 1


def test_the_relay_refuses_a_corrupt_message(peer):
    """A short reassembly must be reported, never delivered.

    A pane narrower than the chunk width wraps a line and the continuation
    loses its header, so the message reassembles short. Delivering it would put
    a truncated sentence in somebody's composer, indistinguishable from a
    complete one.
    """
    lines = peer.encode("bob",
                        "one two three four five six seven").splitlines()
    lines[-1] = lines[-1][:len(lines[-1]) - 6]
    ctl = RelayCtl(panes(alice="\n".join(lines)))
    seen, pending = set(), []
    peer.relay_tick(ctl, PEOPLE, seen, pending, 500, ctl.say)
    assert ctl.typed == [], "a corrupt message must not be delivered"
    assert pending == [], "nor held for ever"
    assert any("reassemble" in line for line in ctl.said), ctl.said


def test_a_header_must_start_its_line(peer):
    """`re.match` anchors at position 0, so the `^` is belt-and-braces --
    but the anchoring itself is load-bearing: prose quoting a header mid-line
    must not become a message."""
    quoted = 'he wrote "[peer bob abc12 1/1 2] hi" in passing'
    assert peer.find_markers(quoted) == []


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
