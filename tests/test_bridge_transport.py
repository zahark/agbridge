"""Task 4a -- `agb bridge`: transport, supervision, watchdog.

This is the Mac side, tested on a farm box with no Mac, no agterm and no ssh.
Three seams make that possible and each one is load-bearing rather than
convenient: `--from-stdin` (the whole event pipeline with no transport), a
scripted line source plus a fake clock (the watchdog with no sleeps), and the
recording `ssh` stub (the argv, without a network).

The tests that matter most here are again about what does *not* happen: silence
must be caught (a half-open ssh is `agr` failure mode #1), a reconnect must
recover the edges lost while offline (failure mode #5), and a feed that dies
immediately must not be respawned in a tight loop.
"""

import ast
import json
import os

import pytest

import conftest


HOST = "vncbox"
MAC = "mac-abc123"
SD = "/shared/.agbridge"
REMOTE = "/opt/agbridge/agb"
PY = "/bin/python3"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def wire(key, state="active", seq=1, beat=1753716000.0, **extra):
    """One session as the feed puts it on the wire."""
    record = {
        "v": 1, "key": key, "label": "lbl-" + key, "host": "box2",
        "pid": 48213, "starttime": 9182736, "tmux": "sess", "pane": "%24",
        "cwd": "/shared/work/project", "state": state, "seq": seq,
        "updated": 1753716123.4, "beat": beat,
    }
    record.update(extra)
    return record


def line(kind, now=1753716001.0, **fields):
    """One NDJSON wire line, as bytes -- what a LineSource hands back."""
    payload = {"t": kind, "now": now}
    payload.update(fields)
    return json.dumps(payload, sort_keys=True).encode("utf-8")


class Clock(object):
    """A fake monotonic clock. The watchdog is a *local* timeout, so it is the
    one thing in agb that may be tested against a clock we control."""

    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class ScriptedSource(object):
    """A `LineSource` stand-in driven by a script of (lines, eof) answers.

    The fake clock is what makes this honest, and it is advanced the way a real
    `select` would: a wait that returns nothing burns the whole timeout, and one
    that returns data burns `gap` -- the interval between lines. Without a real
    `gap`, every "the watchdog stays quiet while ticks arrive" test would pass
    even with the deadline refresh deleted, because no time would ever pass
    between ticks. That mutation survived the first cut of this file.

    A `gap` longer than the timeout is delivered as a *timeout*: real data that
    arrives after the deadline does not un-fire the watchdog.

    The wait counter is a fail-fast: `bridge_run` has no iteration bound by
    design, so a regression in an exit condition must fail the suite rather than
    hang it.
    """

    def __init__(self, clock, script=(), then=None, limit=200, gap=0.01):
        self.clock = clock
        self.script = list(script)
        self.then = then if then is not None else ([], True)
        self.limit = limit
        self.gap = gap
        self.timeouts = []

    def wait(self, timeout):
        self.timeouts.append(timeout)
        if len(self.timeouts) > self.limit:
            raise AssertionError(
                "bridge_run did not exit after %d waits" % (self.limit,))
        lines, eof = self.script[0] if self.script else self.then
        if (lines or eof) and self.gap > timeout:
            self.clock.advance(timeout)      # the deadline came first
            return ([], False)
        if self.script:
            self.script.pop(0)
        self.clock.advance(self.gap if (lines or eof) else timeout)
        return (list(lines), eof)


class FakeConn(object):
    """What `connect()` returns: a source and a shutdown, nothing else."""

    def __init__(self, source):
        self.source = source
        self.closed = 0

    def close(self):
        self.closed += 1


class Sink(object):
    """Records the ops the transport produces. Task 4b renders them instead."""

    def __init__(self, stop_after=None):
        self.ops = []
        self.batches = 0
        self.stop_after = stop_after

    def __call__(self, ops):
        self.batches += 1
        self.ops.extend(ops)
        if self.stop_after is not None and self.batches >= self.stop_after:
            return False
        return None

    def kinds(self):
        return [op for op, _payload in self.ops]


@pytest.fixture
def ssh_stub(stub_bin, tmp_path, monkeypatch, repo_root):
    """Install `tests/stubs/ssh` on $PATH, wired to a StubBin-parsable log."""
    with open(os.path.join(repo_root, "tests", "stubs", "ssh")) as handle:
        body = handle.read()
    log = stub_bin.install("ssh", body=body)
    monkeypatch.setenv("AGB_SSH_LOG", str(log))

    class Stub(object):
        def script(self, lines):
            path = tmp_path / "ssh.script"
            with open(str(path), "wb") as out:
                for raw in lines:
                    out.write(raw + b"\n")
            monkeypatch.setenv("AGB_SSH_SCRIPT", str(path))
            return path

        def hold(self):
            monkeypatch.setenv("AGB_SSH_HOLD", "1")

        def calls(self):
            return stub_bin.calls("ssh")

    return Stub()


# ---------------------------------------------------------------------------
# the ssh invocation
# ---------------------------------------------------------------------------

def test_the_ssh_argv_is_the_documented_invocation(mac):
    assert mac.bridge_ssh_argv(HOST, SD, MAC, REMOTE, PY) == [
        "ssh",
        "-o", "ServerAliveInterval=15",
        "-o", "ServerAliveCountMax=3",
        "-o", "ConnectTimeout=20",
        "-o", "BatchMode=yes",
        HOST,
        "env", "AGB_STATEDIR=/shared/.agbridge",
        "/bin/python3", "-S", "-E", "/opt/agbridge/agb",
        "feed", MAC,
    ]


def test_the_ssh_argv_carries_the_statedir_because_ssh_sources_no_profile(mac):
    """`ssh host cmd` runs a non-login shell: without `env AGB_STATEDIR=…` the
    feed would resolve the statedir from a config file that install.sh writes
    for interactive use, or fall back to the default -- silently the wrong one
    the moment the two disagree."""
    argv = mac.bridge_ssh_argv(HOST, "/scratch/agb", MAC, REMOTE, PY)
    assert argv[argv.index("env") + 1] == "AGB_STATEDIR=/scratch/agb"


def test_the_ssh_argv_keeps_the_interpreter_flags(mac):
    """Constraint #1 on the far side: `-S -E` is the measured startup win, and
    an absolute interpreter path is constraint #14."""
    argv = mac.bridge_ssh_argv(HOST, SD, MAC, REMOTE, PY)
    assert argv[argv.index(PY) + 1:argv.index(PY) + 3] == ["-S", "-E"]
    assert argv[argv.index(PY) - 1].startswith("AGB_STATEDIR=")
    assert argv[-2:] == ["feed", MAC]


def test_the_ssh_argv_supervises_the_connection(mac):
    """ServerAlive is not the watchdog -- it is the layer below it. It catches a
    dead TCP; the app-level watchdog catches a live TCP with a wedged feed."""
    argv = mac.bridge_ssh_argv(HOST, SD, MAC, REMOTE, PY)
    assert "ServerAliveInterval=%d" % (mac.BRIDGE_ALIVE_INTERVAL,) in argv
    assert "ServerAliveCountMax=%d" % (mac.BRIDGE_ALIVE_COUNT,) in argv


def test_the_ssh_argv_bounds_the_connect_and_never_prompts(mac):
    """The two things ServerAlive cannot do, both seen in a real launchd log.

    ServerAlive only starts once a session exists, so it does not bound the
    *connect*: a laptop that loses its VPN sat in the kernel's TCP timeout and
    reported `Operation timed out` minutes later. And a LaunchAgent has no tty,
    so an ssh that decides to ask for a passphrase or a host-key confirmation
    blocks on a prompt nobody can answer -- a hung bridge with a live process,
    which reads from the outside exactly like a quiet farm.
    """
    argv = mac.bridge_ssh_argv(HOST, SD, MAC, REMOTE, PY)
    assert "ConnectTimeout=%d" % (mac.BRIDGE_CONNECT_TIMEOUT,) in argv
    assert "BatchMode=yes" in argv
    # Before the host, or ssh reads them as arguments to the remote command.
    for option in ("ConnectTimeout=%d" % (mac.BRIDGE_CONNECT_TIMEOUT,),
                   "BatchMode=yes"):
        assert argv.index(option) < argv.index(HOST)


@pytest.mark.parametrize("args", [
    ("", SD, MAC, REMOTE, PY),                     # no feed host
    ("-oProxyCommand=x", SD, MAC, REMOTE, PY),     # an option, not a host
    ("box 2", SD, MAC, REMOTE, PY),                # unquotable host
    (HOST, SD, "", REMOTE, PY),                    # no mac-id
    (HOST, SD, "../evil", REMOTE, PY),             # mac-id names a file
    (HOST, "relative/dir", MAC, REMOTE, PY),       # statedir must be absolute
    (HOST, "~/state", MAC, REMOTE, PY),            # `~` means the *Mac's* home
    (HOST, "/tmp/a b", MAC, REMOTE, PY),           # the remote shell re-splits
    (HOST, SD, MAC, "/tmp/agb; rm -rf /", PY),     # command injection
    (HOST, SD, MAC, REMOTE, "python3"),            # constraint #14
])
def test_an_unusable_ssh_target_fails_loudly(mac, agb, args):
    """Every one of these would otherwise produce a connection that fails on the
    far side, where nothing on the Mac can see why -- the silent-failure class
    this project exists to remove."""
    with pytest.raises(agb.AgbError):
        mac.bridge_ssh_argv(*args)


# ---------------------------------------------------------------------------
# LineSource
# ---------------------------------------------------------------------------

def test_a_partial_line_is_buffered_until_it_completes(mac):
    """A `write()` on the far side is not a message boundary: NDJSON arrives cut
    wherever the pipe felt like cutting it."""
    read_fd, write_fd = os.pipe()
    source = mac.LineSource(read_fd)
    try:
        os.write(write_fd, b'{"t":"ti')
        assert source.wait(0) == ([], False)
        os.write(write_fd, b'ck"}\n{"t":"tick","n":2}\n')
        lines, eof = source.wait(0)
        assert not eof
        assert [json.loads(raw.decode()) for raw in lines] == [
            {"t": "tick"}, {"t": "tick", "n": 2}]
    finally:
        os.close(read_fd)
        os.close(write_fd)


def test_a_closed_write_end_is_reported_as_eof(mac):
    read_fd, write_fd = os.pipe()
    source = mac.LineSource(read_fd)
    try:
        os.write(write_fd, b'{"t":"tick"}\n')
        assert source.wait(0)[0]
        os.close(write_fd)
        assert source.wait(0) == ([], True)
    finally:
        os.close(read_fd)


def test_a_quiet_stream_is_not_eof(mac):
    """The distinction the whole watchdog rests on: quiet is not dead."""
    read_fd, write_fd = os.pipe()
    source = mac.LineSource(read_fd)
    try:
        assert source.wait(0) == ([], False)
    finally:
        os.close(read_fd)
        os.close(write_fd)


def test_blank_lines_are_ignored(mac):
    read_fd, write_fd = os.pipe()
    source = mac.LineSource(read_fd)
    try:
        os.write(write_fd, b'\n\n{"t":"tick"}\n\n')
        assert source.wait(0)[0] == [b'{"t":"tick"}']
    finally:
        os.close(read_fd)
        os.close(write_fd)


def test_a_runaway_line_is_dropped_rather_than_the_process(mac, monkeypatch):
    """An unterminated line would otherwise grow the buffer without bound. It is
    safe to drop: an unparsable line is "no information", never a removal."""
    monkeypatch.setattr(mac, "BRIDGE_MAX_LINE", 32)
    read_fd, write_fd = os.pipe()
    source = mac.LineSource(read_fd)
    try:
        os.write(write_fd, b"x" * 64)
        assert source.wait(0) == ([], False)
        os.write(write_fd, b'tail-of-the-runaway\n{"t":"tick"}\n')
        assert source.wait(0)[0] == [b'{"t":"tick"}']
    finally:
        os.close(read_fd)
        os.close(write_fd)


def test_a_source_with_no_fd_is_eof_not_a_crash(mac):
    assert mac.LineSource(None).wait(0) == ([], True)


# ---------------------------------------------------------------------------
# decoding
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw", [
    b"not json at all",
    b"{",
    b'"a string"',
    b"[1, 2, 3]",
    b'{"no":"t field"}',
    b'{"t":""}',
    b"\xff\xfe",
])
def test_an_unusable_line_is_dropped_with_a_warning(mac, raw):
    warned = []
    assert mac.bridge_decode(raw, warned.append) is None
    assert warned


def test_a_good_line_decodes(mac):
    event = mac.bridge_decode(line("tick"))
    assert event["t"] == "tick" and event["now"] == 1753716001.0


# ---------------------------------------------------------------------------
# the model: snapshot, upsert, remove, resync
# ---------------------------------------------------------------------------

def test_a_snapshot_produces_one_upsert_per_session(mac):
    model = mac.BridgeModel()
    ops = model.apply(json.loads(
        line("snapshot", sessions=[wire("aaaa1111"), wire("bbbb2222")]).decode()))
    assert [op for op, _p in ops] == ["upsert", "upsert"]
    assert sorted(model.sessions) == ["aaaa1111", "bbbb2222"]


def test_an_unchanged_upsert_produces_nothing(mac):
    """Task 4b turns every op into an `agtermctl` invocation, so a model that
    re-emitted identical records would repaint the sidebar on every poll."""
    model = mac.BridgeModel()
    model.apply(json.loads(line("upsert", session=wire("aaaa1111")).decode()))
    ops = model.apply(json.loads(line("upsert", session=wire("aaaa1111")).decode()))
    assert ops == []


def test_a_changed_upsert_produces_one_op(mac):
    model = mac.BridgeModel()
    model.apply(json.loads(line("upsert", session=wire("aaaa1111")).decode()))
    ops = model.apply(json.loads(
        line("upsert", session=wire("aaaa1111", state="blocked", seq=2)).decode()))
    assert [op for op, _p in ops] == ["upsert"]
    assert ops[0][1]["state"] == "blocked"


def test_a_remove_of_an_unknown_key_produces_nothing(mac):
    model = mac.BridgeModel()
    assert model.apply(json.loads(line("remove", key="ffff9999").decode())) == []


def test_a_remove_drops_the_session(mac):
    model = mac.BridgeModel()
    model.apply(json.loads(line("upsert", session=wire("aaaa1111")).decode()))
    ops = model.apply(json.loads(line("remove", key="aaaa1111").decode()))
    assert ops == [("remove", "aaaa1111")]
    assert model.sessions == {}


def test_a_reconnect_snapshot_removes_what_ended_while_offline(mac):
    """`agr` failure mode #5, as an executable claim.

    Agents start and finish while the bridge is disconnected. A push transport
    loses those edges permanently; a merge-only reconnect would leave every
    session that ended during the outage on screen forever. The snapshot is the
    whole truth, so it is applied as a replacement.
    """
    model = mac.BridgeModel()
    model.apply(json.loads(line(
        "snapshot", sessions=[wire("aaaa1111"), wire("bbbb2222")]).decode()))

    ops = model.apply(json.loads(line("snapshot", sessions=[
        wire("bbbb2222", state="blocked", seq=7),   # changed while offline
        wire("cccc3333"),                           # started while offline
    ]).decode()))

    assert ("remove", "aaaa1111") in ops                      # ended offline
    assert [op for op, _p in ops].count("upsert") == 2
    assert sorted(model.sessions) == ["bbbb2222", "cccc3333"]


def test_a_reconnect_snapshot_is_quiet_about_what_did_not_change(mac):
    model = mac.BridgeModel()
    session = wire("aaaa1111")
    model.apply(json.loads(line("snapshot", sessions=[session]).decode()))
    assert model.apply(json.loads(line("snapshot", sessions=[session]).decode())) == []


def test_every_line_updates_the_feeds_clock(mac):
    """Constraint #12: ages are computed in the feed's server-stamped domain, so
    the bridge tracks `now` from the wire and never from its own clock."""
    model = mac.BridgeModel()
    model.apply(json.loads(line("tick", now=17.5).decode()))
    assert model.now == 17.5
    model.apply(json.loads(line("tick", now=18.5).decode()))
    assert model.now == 18.5


def test_a_malformed_session_entry_is_skipped_not_fatal(mac):
    model = mac.BridgeModel()
    ops = model.apply(json.loads(line("snapshot", sessions=[
        "not a dict", {"no": "key"}, wire("aaaa1111")]).decode()))
    assert [op for op, _p in ops] == ["upsert"]


# ---------------------------------------------------------------------------
# an INCOMPLETE snapshot removes nothing (constraint #8, on the wire)
# ---------------------------------------------------------------------------

def test_an_incomplete_snapshot_upserts_but_never_removes(mac):
    """The feed sets `complete: false` when the poll behind the snapshot could
    not read something. Poll 1 has nothing retained to cover that, so an
    unreadable key and a gone key arrive as the identical absence -- and on the
    Mac a wrong `remove` marks the row `[done]`, which only `agb close-done`
    undoes. So the removals wait for a snapshot that can back them up."""
    model = mac.BridgeModel()
    model.adopt(["aaaa1111"])
    ops = model.apply(json.loads(line("snapshot", complete=False,
                                      sessions=[wire("bbbb2222")]).decode()))
    assert [op for op, _p in ops] == ["upsert"]
    assert "aaaa1111" in model.sessions


def test_the_next_complete_snapshot_still_removes(mac):
    """Deferred, not cancelled: `agr` failure mode #5 (rows that outlive their
    agents across an outage) must still be fixed by the resync.

    ⚠️ This proves only THIS side of the contract, and for one release it was
    read as proving the whole of it. It hands the model a second snapshot; the
    feed emitted exactly one per connection, so in production that second line
    did not exist and the deferral was a cancellation for the life of the ssh.
    What a snapshot means is checked here; what actually arrives is checked by
    the two real-process tests at the end of `tests/test_feed.py`, and neither
    test is worth much without the other.
    """
    model = mac.BridgeModel()
    model.adopt(["aaaa1111"])
    model.apply(json.loads(line("snapshot", complete=False, sessions=[]).decode()))
    ops = model.apply(json.loads(line("snapshot", sessions=[]).decode()))
    assert ops == [("remove", "aaaa1111")]


def test_a_snapshot_with_no_complete_field_is_treated_as_complete(mac):
    """Backward tolerance, in the direction that matters: a feed older than the
    field still resyncs exactly as it always did."""
    model = mac.BridgeModel()
    model.adopt(["aaaa1111"])
    event = json.loads(line("snapshot", sessions=[]).decode())
    assert "complete" not in event
    assert model.apply(event) == [("remove", "aaaa1111")]


@pytest.mark.parametrize("value", [False, None, 0, "", "false", "no", 1,
                                   ["yes"]])
def test_anything_but_a_positive_complete_defers_the_removals(mac, value):
    """The fail-safe direction: a garbled value defers removals rather than
    authorising them on a guess.

    The four TRUTHY cases are the ones that were wrong. `not complete` reads
    the JSON **string** `"false"` -- what a re-encoding proxy or a hand-edited
    replay produces -- as authority to remove, because a non-empty string is
    truthy in Python: `complete: "false"` marked every absent row `[done]`,
    which only `agb close-done` undoes. Removal authority has to be granted by
    the boolean, not merely left un-withheld.
    """
    model = mac.BridgeModel()
    model.adopt(["aaaa1111"])
    ops = model.apply(json.loads(line("snapshot", complete=value,
                                      sessions=[]).decode()))
    assert ops == []
    assert "aaaa1111" in model.sessions


# ---------------------------------------------------------------------------
# staleness -- feed death is its only trigger (amendment 2)
# ---------------------------------------------------------------------------

def test_marking_stale_is_idempotent(mac):
    """One notification per outage, not one per reconnect attempt."""
    model = mac.BridgeModel()
    assert model.mark_stale("eof") == [("stale", "eof")]
    assert model.mark_stale("eof") == []
    assert model.stale is True


def test_the_first_line_after_an_outage_lifts_the_stale_treatment(mac):
    """`live` comes first, before whatever the line itself implies: the `[?]`
    treatment is lifted by the *arrival*, not by the content.

    The trailing `tick` op is Task 4b's: the row title carries the beat age, so
    the renderer needs a heartbeat in the feed's clock or a quiet host's age
    freezes at whatever it was when that host last said something."""
    model = mac.BridgeModel()
    model.mark_stale("watchdog")
    ops = model.apply(json.loads(line("tick").decode()))
    assert ops == [("live", None), ("tick", None)]
    assert model.stale is False


def test_no_age_and_no_silence_ever_marks_a_session_stale(mac):
    """Amendment 1 as a test: a `blocked` agent waiting on the user beats
    nothing, and nothing in the model may convert that into a state. Only the
    bridge losing its own ssh -- which it can prove -- sets `stale`."""
    model = mac.BridgeModel()
    model.apply(json.loads(line(
        "upsert", now=1e9, session=wire("aaaa1111", state="blocked",
                                        beat=1.0)).decode()))
    assert model.stale is False
    assert model.sessions["aaaa1111"]["state"] == "blocked"


# ---------------------------------------------------------------------------
# bridge_run: the watchdog
# ---------------------------------------------------------------------------

def test_the_watchdog_fires_on_silence(mac):
    """ServerAlive cannot see this: the TCP is fine and the feed has wedged."""
    clock = Clock()
    source = ScriptedSource(clock, then=([], False))
    reason, events = mac.bridge_run(source, mac.BridgeModel(), None,
                                    watchdog=10.0, clock=clock)
    assert reason == mac.BRIDGE_SILENT
    assert events == 0


def test_the_watchdog_does_not_fire_while_ticks_arrive(mac):
    """The tick exists precisely so that "nothing is happening" and "nothing is
    alive" are different observations on the wire.

    The ticks are spaced at 4 s against a 10 s watchdog and run for 80 s of fake
    time: a deadline that is set once and never refreshed expires on the third
    tick, so this fails loudly if the refresh is dropped.
    """
    clock = Clock()
    ticks = [([line("tick")], False)] * 20
    source = ScriptedSource(clock, script=ticks, then=([], True), gap=4.0)
    started = clock()
    reason, events = mac.bridge_run(source, mac.BridgeModel(), None,
                                    watchdog=10.0, clock=clock)
    assert reason == mac.BRIDGE_EOF
    assert events == 20
    assert clock() - started > 10.0


def test_a_line_that_arrives_after_the_deadline_does_not_save_the_connection(mac):
    """Ticks spaced *wider* than the watchdog are a feed that is not keeping its
    own contract -- so the watchdog fires rather than being un-fired by a late
    arrival."""
    clock = Clock()
    source = ScriptedSource(clock, script=[([line("tick")], False)] * 5,
                            gap=30.0)
    reason, events = mac.bridge_run(source, mac.BridgeModel(), None,
                                    watchdog=10.0, clock=clock)
    assert (reason, events) == (mac.BRIDGE_SILENT, 0)


def test_the_watchdog_fires_once_the_ticks_stop(mac):
    clock = Clock()
    source = ScriptedSource(clock, script=[([line("tick")], False)],
                            then=([], False))
    reason, _events = mac.bridge_run(source, mac.BridgeModel(), None,
                                     watchdog=4.0, clock=clock)
    assert reason == mac.BRIDGE_SILENT


def test_garbage_on_the_wire_does_not_count_as_liveness(mac):
    """A feed spewing non-protocol bytes is a broken feed. Counting them as
    liveness is how a wedged feed keeps a dashboard looking healthy."""
    clock = Clock()
    source = ScriptedSource(clock, script=[([b"garbage"], False)] * 50,
                            then=([], False))
    warned = []
    reason, events = mac.bridge_run(source, mac.BridgeModel(), None,
                                    watchdog=1.0, clock=clock,
                                    warn=warned.append)
    assert reason == mac.BRIDGE_SILENT
    assert events == 0
    assert warned


def test_the_watchdog_timeout_is_what_gets_waited_on(mac):
    """Otherwise the source would block past the deadline and the watchdog would
    only fire when the feed happened to say something."""
    clock = Clock()
    source = ScriptedSource(clock, then=([], False))
    mac.bridge_run(source, mac.BridgeModel(), None, watchdog=7.0, clock=clock)
    assert source.timeouts[0] == 7.0


# ---------------------------------------------------------------------------
# quiet is not death: the two thresholds
# ---------------------------------------------------------------------------
#
# `/shared` is a hard mount with `timeo=600,retrans=10` and no `intr`, and
# `feed_poll` does one `open()` per marker and per `.state` on it. A ten-second
# watchdog therefore fired on an ORDINARY server hiccup: the ssh was torn down,
# a desktop notification fired, every row went `[?]`, and 1 s later the whole
# thing repeated -- for a stall respawning ssh cannot influence at all, because
# it is farm-side. So the rendering threshold and the teardown threshold were
# split.

def test_silence_marks_the_rows_stale_without_ending_the_connection(mac):
    clock = Clock()
    sink = Sink()
    source = ScriptedSource(clock, script=[([], False),
                                           ([line("tick")], False)],
                            then=([], True))
    reason, events = mac.bridge_run(source, mac.BridgeModel(), sink,
                                    watchdog=100.0, quiet=5.0, clock=clock)
    assert reason == mac.BRIDGE_EOF
    assert sink.ops[0] == ("stale", mac.BRIDGE_QUIET_REASON)
    assert events >= 1


def test_a_line_after_a_quiet_spell_lifts_the_stale_without_a_reconnect(mac):
    """The whole reason for the split: an NFS hiccup that clears on its own must
    repaint the rows, not respawn the transport."""
    clock = Clock()
    sink = Sink()
    source = ScriptedSource(clock, script=[([], False),
                                           ([line("tick")], False)],
                            then=([], True))
    reason, _events = mac.bridge_run(source, mac.BridgeModel(), sink,
                                     watchdog=100.0, quiet=5.0, clock=clock)
    assert reason == mac.BRIDGE_EOF
    assert [op for op, _p in sink.ops] == ["stale", "live", "tick"]


def test_the_rows_are_marked_stale_once_per_quiet_spell(mac):
    """A spell that spans several waits -- including one carrying bytes that are
    not protocol, which must not count as liveness -- produces one op, not one
    per wait."""
    clock = Clock()
    sink = Sink()
    source = ScriptedSource(clock, script=[([], False), ([b"garbage"], False),
                                           ([], False)], then=([], True))
    reason, events = mac.bridge_run(source, mac.BridgeModel(), sink,
                                    watchdog=100.0, quiet=5.0, clock=clock,
                                    warn=lambda _t: None)
    assert (reason, events) == (mac.BRIDGE_SILENT, 0)
    assert [op for op, _p in sink.ops] == ["stale"]


def test_the_quiet_threshold_never_outlives_the_watchdog(mac):
    """A caller that asks for a short watchdog (`--watchdog`, every test above)
    gets exactly the timeout it asked for, not a longer quiet period first."""
    clock = Clock()
    source = ScriptedSource(clock, then=([], False))
    reason, _events = mac.bridge_run(source, mac.BridgeModel(), None,
                                     watchdog=3.0, clock=clock)
    assert reason == mac.BRIDGE_SILENT
    assert source.timeouts[0] == 3.0


def test_the_shipped_watchdog_is_anchored_to_the_mount_not_the_poll(mac, agb):
    """The number is the point: the documented worst case for one uninterruptible
    NFS `open()` on this mount is `timeo=600` deciseconds x `retrans=10` = 600 s.
    Anything near `FEED_POLL_INTERVAL * 5` reopens the reconnect storm."""
    assert mac.BRIDGE_QUIET == agb.FEED_POLL_INTERVAL * mac.BRIDGE_QUIET_TICKS
    assert mac.BRIDGE_WATCHDOG >= 600.0
    assert mac.BRIDGE_WATCHDOG > mac.BRIDGE_QUIET * 10


def test_eof_ends_the_connection_and_reports_what_it_saw(mac):
    clock = Clock()
    source = ScriptedSource(clock, script=[
        ([line("snapshot", sessions=[wire("aaaa1111")])], False),
        ([], True),
    ])
    sink = Sink()
    reason, events = mac.bridge_run(source, mac.BridgeModel(), sink,
                                    watchdog=10.0, clock=clock)
    assert (reason, events) == (mac.BRIDGE_EOF, 1)
    assert sink.kinds() == ["upsert"]


def test_a_sink_that_says_stop_stops_the_connection(mac):
    clock = Clock()
    source = ScriptedSource(clock, script=[
        ([line("upsert", session=wire("aaaa1111"))], False)] * 5,
        then=([], False))
    sink = Sink(stop_after=1)
    reason, _events = mac.bridge_run(source, mac.BridgeModel(), sink,
                                     watchdog=10.0, clock=clock)
    assert reason == mac.BRIDGE_STOPPED
    assert sink.batches == 1


def test_several_lines_in_one_read_are_all_applied(mac):
    clock = Clock()
    burst = [line("upsert", session=wire("aaaa1111")),
             line("upsert", session=wire("bbbb2222")),
             line("remove", key="aaaa1111")]
    source = ScriptedSource(clock, script=[(burst, False), ([], True)])
    sink = Sink()
    mac.bridge_run(source, mac.BridgeModel(), sink, watchdog=10.0, clock=clock)
    assert sink.kinds() == ["upsert", "upsert", "remove"]


# ---------------------------------------------------------------------------
# backoff and supervision
# ---------------------------------------------------------------------------

def test_the_backoff_is_exponential_and_capped(mac):
    delays = [mac.bridge_backoff(n) for n in range(0, 9)]
    assert delays == [0.0, 1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 60.0, 60.0]
    assert mac.bridge_backoff(10 ** 6) == mac.BRIDGE_BACKOFF_CAP


def test_repeated_connection_failures_back_off(mac, agb):
    """A farm box that is down must not be hammered once per second forever."""
    slept = []
    warned = []

    def connect():
        raise agb.AgbError("nope")

    rc = mac.bridge_supervise(connect, mac.BridgeModel(), None, connections=4,
                              sleep=slept.append, warn=warned.append)
    assert rc == 0
    assert slept == [1.0, 2.0, 4.0]        # nothing is slept after the last one
    assert warned


def test_a_productive_connection_restarts_the_exponent_but_never_zeroes_it(mac):
    """A feed that dies right after its snapshot would otherwise be respawned in
    a tight loop -- an outage turned into a fork bomb."""
    clock = Clock()
    slept = []

    def connect():
        source = ScriptedSource(clock, script=[
            ([line("snapshot", sessions=[wire("aaaa1111")])], False), ([], True)])
        return FakeConn(source)

    mac.bridge_supervise(connect, mac.BridgeModel(), None, connections=3,
                         sleep=slept.append, clock=clock, watchdog=10.0)
    assert slept == [1.0, 1.0]


def test_a_watchdog_kill_backs_off_even_though_it_saw_events(mac):
    """The exception to the rule above, and the reason for it: a watchdog kill
    is the one ending respawning cannot help with -- the stall is farm-side NFS.
    Restarting the exponent there gave a 1 s reconnect every watchdog period for
    as long as the server sulked."""
    clock = Clock()
    slept = []

    def connect():
        return FakeConn(ScriptedSource(clock, script=[
            ([line("snapshot", sessions=[wire("aaaa1111")])], False)],
            then=([], False)))

    mac.bridge_supervise(connect, mac.BridgeModel(), None, connections=4,
                         sleep=slept.append, clock=clock, watchdog=10.0)
    assert slept == [1.0, 2.0, 4.0]


def test_a_connection_is_always_closed(mac):
    """Closing is what tells the remote feed to exit; an orphan would keep
    touching `bridge/<mac-id>.beat` and make `bridge:UP` a lie."""
    clock = Clock()
    conns = []

    def connect():
        conn = FakeConn(ScriptedSource(clock, then=([], True)))
        conns.append(conn)
        return conn

    mac.bridge_supervise(connect, mac.BridgeModel(), None, connections=2,
                         sleep=lambda _s: None, clock=clock)
    assert [conn.closed for conn in conns] == [1, 1]


def test_a_connection_is_closed_even_when_the_sink_raises(mac):
    clock = Clock()
    conn = FakeConn(ScriptedSource(clock, script=[
        ([line("upsert", session=wire("aaaa1111"))], False)]))

    def boom(_ops):
        raise RuntimeError("renderer exploded")

    with pytest.raises(RuntimeError):
        mac.bridge_supervise(lambda: conn, mac.BridgeModel(), boom,
                             connections=1, sleep=lambda _s: None, clock=clock)
    assert conn.closed == 1


def test_every_connection_end_marks_the_rows_stale(mac):
    """Feed death is the only inference-free staleness trigger, and the bridge
    owns the ssh -- so it is the one place that can prove it."""
    clock = Clock()
    sink = Sink()

    def connect():
        return FakeConn(ScriptedSource(clock, then=([], True)))

    mac.bridge_supervise(connect, mac.BridgeModel(), sink, connections=1,
                         sleep=lambda _s: None, clock=clock)
    assert sink.ops == [("stale", mac.BRIDGE_EOF)]


def test_a_failed_spawn_still_marks_the_rows_stale(mac):
    """An ssh that will not even start is exactly as stale as one that died."""
    sink = Sink()
    mac.bridge_supervise(lambda: (_ for _ in ()).throw(OSError("no ssh")),
                         mac.BridgeModel(), sink, connections=1,
                         sleep=lambda _s: None, warn=lambda _t: None)
    assert sink.ops == [("stale", mac.BRIDGE_NO_CONNECTION)]


def test_the_watchdog_reason_reaches_the_sink(mac):
    clock = Clock()
    sink = Sink()

    def connect():
        return FakeConn(ScriptedSource(clock, then=([], False)))

    mac.bridge_supervise(connect, mac.BridgeModel(), sink, connections=1,
                         watchdog=5.0, sleep=lambda _s: None, clock=clock)
    assert sink.ops == [("stale", mac.BRIDGE_SILENT)]


def test_reconnecting_resyncs_from_the_snapshot(mac):
    """The whole point of the reconnect path, end to end through `supervise`:
    the second connection's snapshot both revives the row (`live`) and reclaims
    the session that ended while the bridge was away."""
    clock = Clock()
    sink = Sink()
    snapshots = [
        [wire("aaaa1111"), wire("bbbb2222")],
        [wire("bbbb2222")],
    ]

    def connect():
        sessions = snapshots.pop(0)
        return FakeConn(ScriptedSource(clock, script=[
            ([line("snapshot", sessions=sessions)], False), ([], True)]))

    mac.bridge_supervise(connect, mac.BridgeModel(), sink, connections=2,
                         watchdog=10.0, sleep=lambda _s: None, clock=clock)
    assert sink.kinds() == [
        "upsert", "upsert",          # first snapshot
        "stale",                     # the connection dropped
        "live",                      # ...and came back
        "remove",                    # aaaa1111 ended while we were away
        "stale",                     # and the second connection ended too
    ]
    assert [payload for op, payload in sink.ops if op == "remove"] == ["aaaa1111"]


def test_the_supervisor_stops_when_the_sink_says_so(mac):
    clock = Clock()
    sink = Sink(stop_after=1)

    def connect():
        return FakeConn(ScriptedSource(clock, script=[
            ([line("upsert", session=wire("aaaa1111"))], False)], then=([], False)))

    rc = mac.bridge_supervise(connect, mac.BridgeModel(), sink, connections=None,
                              watchdog=10.0, sleep=lambda _s: None, clock=clock)
    assert rc == 0


# ---------------------------------------------------------------------------
# argument parsing and settings
# ---------------------------------------------------------------------------

def test_bridge_args_default_to_nothing_assumed(mac):
    opts = mac.parse_bridge_args([])
    assert opts["from_stdin"] is False
    assert opts["feed_host"] is None and opts["mac_id"] is None
    assert opts["watchdog"] is None and opts["connections"] is None


@pytest.mark.parametrize("argv", [
    ["--from-stdin", "--feed-host", "vncbox", "--watchdog", "2.5",
     "--connections", "3"],
    ["--feed-host=vncbox", "--watchdog=2.5", "--connections=3", "--from-stdin"],
])
def test_bridge_args_accept_both_spellings_and_either_order(mac, argv):
    opts = mac.parse_bridge_args(argv)
    assert opts["from_stdin"] is True
    assert opts["feed_host"] == "vncbox"
    assert opts["watchdog"] == 2.5 and opts["connections"] == 3


@pytest.mark.parametrize("argv", [
    ["--nonsense"],
    ["stray-positional"],
    ["--feed-host"],
    ["--watchdog", "soon"],
    ["--watchdog", "0"],
    ["--connections", "0"],
    ["--connections", "many"],
])
def test_bridge_args_are_rejected_loudly(mac, agb, argv):
    with pytest.raises(agb.AgbError):
        mac.parse_bridge_args(argv)


def test_settings_come_from_the_config_when_the_cli_is_silent(mac,
                                                              config_file):
    config_file(
        "feed_host = vncbox\n"
        "mac_id = mac-abc123\n"
        "statedir = /shared/.agbridge\n"
        "agb_remote_path = /opt/agbridge/agb\n"
        "remote_python = /usr/bin/python3\n"
    )
    settings = mac.bridge_settings(mac.parse_bridge_args([]))
    assert settings == {
        "feed_host": "vncbox", "mac_id": "mac-abc123",
        "statedir": "/shared/.agbridge", "remote_path": "/opt/agbridge/agb",
        "remote_python": "/usr/bin/python3",
    }


def test_the_cli_overrides_the_config(mac, config_file):
    config_file("feed_host = vncbox\nmac_id = mac-abc123\n"
                "statedir = /shared/.agbridge\n")
    settings = mac.bridge_settings(mac.parse_bridge_args(
        ["--feed-host", "otherbox", "--mac-id", "mac-zzz"]))
    assert settings["feed_host"] == "otherbox"
    assert settings["mac_id"] == "mac-zzz"


def test_the_defaults_are_the_farm_paths(mac, agb, config_file):
    config_file("feed_host = vncbox\nmac_id = mac-abc123\n"
                "statedir = /shared/.agbridge\n")
    settings = mac.bridge_settings(mac.parse_bridge_args([]))
    assert settings["statedir"] == "/shared/.agbridge"
    assert settings["remote_path"] == mac.DEFAULT_AGB_REMOTE_PATH
    assert settings["remote_python"] == mac.DEFAULT_REMOTE_PYTHON


def test_the_statedir_is_never_defaulted_from_this_machines_home(
        mac, agb, config_file, monkeypatch):
    """The statedir names a directory on the FARM. `agb.default_statedir()` is
    `~/.agbridge` resolved against **this** process's `$HOME`, so defaulting to
    it here would ship `/Users/<someone>/.agbridge` across in `env
    AGB_STATEDIR=`; `cmd_feed` creates whatever it is given, so the farm would
    grow an empty statedir and report an empty farm for ever -- silently, which
    is the whole failure class this tool exists to remove.

    Regression guard: this defaulted to `agb.default_statedir()` and shipped a
    Mac-local path to the farm.
    """
    monkeypatch.setenv("HOME", "/Users/somebody")
    with pytest.raises(agb.AgbError) as excinfo:
        mac.bridge_settings(mac.parse_bridge_args([]),
                            {"feed_host": "vncbox", "mac_id": "mac-abc123"})
    message = str(excinfo.value)
    assert "statedir" in message
    # It may name this machine's *config path* -- that is the file to edit. What
    # it must never do is invent the farm-side statedir from this machine's home.
    assert agb.default_statedir() not in message
    assert "farm" in message.lower()             # and says whose path it is


@pytest.mark.parametrize("missing", ["feed_host", "mac_id", "statedir"])
def test_a_missing_essential_setting_is_named(mac, agb, config_file, missing):
    """A bridge that starts, finds no ssh target and quietly never connects is
    `agr` failure mode #1 rebuilt from scratch."""
    lines = {"feed_host": "feed_host = vncbox\n", "mac_id": "mac_id = m-1\n",
             "statedir": "statedir = /shared/.agbridge\n"}
    del lines[missing]
    config_file("".join(lines.values()))
    with pytest.raises(agb.AgbError) as excinfo:
        mac.bridge_settings(mac.parse_bridge_args([]))
    assert missing in str(excinfo.value)


def test_the_environment_can_supply_the_remote_statedir(mac, monkeypatch,
                                                        config_file):
    config_file("feed_host = vncbox\nmac_id = mac-abc123\n")
    monkeypatch.setenv("AGB_STATEDIR", "/scratch/agb")
    settings = mac.bridge_settings(mac.parse_bridge_args([]))
    assert settings["statedir"] == "/scratch/agb"


def test_the_remote_statedir_is_never_expanded_against_the_macs_home(mac, agb):
    """`~` would resolve to the *Mac's* home and then be sent to a machine where
    it means something else -- so it is refused rather than expanded."""
    with pytest.raises(agb.AgbError):
        mac.bridge_ssh_argv(HOST, "~/.agbridge", MAC, REMOTE, PY)


# ---------------------------------------------------------------------------
# `--config`: one path, and everything an instance owns derived from it
# ---------------------------------------------------------------------------
#
# A Mac talking to two farms that share no disk needs two statedirs, so two
# feeds, so two bridges. The whole of that second instance hangs off ONE flag:
# the rows map, the placements file and the `host_<name>` table its rows resolve
# against are all derived from the config's directory. These tests assert the
# derivation itself, because the derivation *is* the isolation -- a `--config`
# that were merely read and not derived from would give two bridges one shared
# bijection, which is worse than no isolation at all.

def test_the_config_flag_moves_everything_the_instance_owns(
        mac, config_file, instance_config):
    """Rows, placements and the values themselves come from that one file."""
    config_file("workspace = default-space\njump_host = default-jump\n")
    path = instance_config("hostb", "workspace = hostb-space\n")
    settings = mac.render_settings(mac.parse_bridge_args(["--config", path]))
    assert settings["rows"] == os.path.join(os.path.dirname(path), "rows")
    assert settings["placements"] == os.path.join(os.path.dirname(path),
                                                  "placements")
    assert settings["config"] == path
    # Non-vacuity, and the point of the whole flag: the settings were read from
    # the instance's config and not from the default one, which has both keys.
    assert settings["workspace"] == "hostb-space"
    assert settings["jump_host"] is None


def test_without_the_flag_every_path_is_exactly_what_it_was(mac, agb,
                                                            fake_home):
    """The default install must not move by a byte -- including the published
    `config`, which stays None so that no row command starts carrying a flag."""
    settings = mac.render_settings(mac.parse_bridge_args([]))
    assert settings["rows"] == mac.rows_path()
    assert settings["placements"] == mac.placements_path()
    assert settings["config"] is None
    assert os.path.dirname(settings["rows"]) == os.path.dirname(
        agb.config_path())


def test_an_explicit_rows_file_still_wins(mac, tmp_path, instance_config):
    """`--rows` predates this and is a debugging seam; it keeps working."""
    path = instance_config("hostb")
    elsewhere = str(tmp_path / "scratch-rows")
    settings = mac.render_settings(mac.parse_bridge_args(
        ["--config", path, "--rows", elsewhere]))
    assert settings["rows"] == elsewhere
    # ...and drags nothing else with it. Deriving the config path from
    # `dirname(rows)` -- the obvious-looking shortcut -- would name
    # `<tmp>/config` here: a file that does not exist, so `agb pane` would fall
    # back to the bare hostname and click-to-attach would reach nothing.
    assert settings["config"] == path
    assert settings["placements"] == os.path.join(os.path.dirname(path),
                                                  "placements")


def test_the_published_config_key_is_independent_of_rows(mac,
                                                        instance_config):
    """The one-line version of the rule above, stated where Task 2 reads it."""
    path = instance_config("hostc")
    assert mac.render_settings({"config": path})["config"] == path
    assert mac.render_settings(
        {"config": path, "rows": "/tmp/rows"})["config"] == path


def test_the_sink_binds_the_instances_own_row_map(mac, instance_config):
    """render_settings -> RowRenderer, which is where a decorative flag would
    show up as a shared bijection."""
    path = instance_config("hostb")
    _sink, renderer = mac.bridge_sink(
        mac.BridgeModel(), mac.parse_bridge_args(["--config", path]))
    assert renderer is not None
    assert renderer.rows.path == os.path.join(os.path.dirname(path), "rows")
    assert renderer.settings["placements"] == os.path.join(
        os.path.dirname(path), "placements")


def test_the_missing_value_message_names_the_instances_own_config(
        mac, agb, config_file, instance_config):
    """An error that points at the wrong file is worse than none: it is
    believed. The default config here is complete, so reaching the failure at
    all proves the instance's config was the one read."""
    config_file("feed_host = vncbox\nmac_id = mac-abc123\n"
                "statedir = /shared/.agbridge\n")
    path = instance_config("hostb")
    with pytest.raises(agb.AgbError) as excinfo:
        mac.bridge_settings(mac.parse_bridge_args(["--config", path]))
    message = str(excinfo.value)
    assert path in message
    assert agb.config_path() not in message


def test_the_missing_value_message_still_names_the_default_config(mac, agb):
    """The other half: with no `--config` it names the file it always named."""
    with pytest.raises(agb.AgbError) as excinfo:
        mac.bridge_settings(mac.parse_bridge_args([]))
    assert agb.config_path() in str(excinfo.value)


def test_the_bridge_reads_its_config_exactly_once(mac, agb, monkeypatch,
                                                  instance_config):
    """Two reads are two chances to read two different files.

    Shaped deliberately around the **ssh** path: `run_bridge` reaches
    `render_settings` on every path but `bridge_settings` only when it is about
    to connect, so a `--from-stdin` drive would count one read both before and
    after this change and prove nothing. `bridge_supervise` is stubbed out at
    the last moment, which is also the non-vacuity assertion below.
    """
    path = instance_config("hostb",
                            "feed_host = vncbox\nmac_id = mac-abc123\n"
                            "statedir = /shared/.agbridge\n")
    reads = []
    real_read = agb.read_config

    def counting(config_path=None, warnings=None):
        reads.append(config_path)
        return real_read(config_path, warnings)

    monkeypatch.setattr(agb, "read_config", counting)

    supervised = []

    def no_supervise(connect, model, on_ops=None, **kwargs):
        supervised.append(connect)
        return 0

    monkeypatch.setattr(mac, "bridge_supervise", no_supervise)

    assert mac.run_bridge(["--config", path]) == 0
    assert supervised                     # the ssh path really did run
    assert reads == [path]                # it was two before this change


# ---------------------------------------------------------------------------
# the command, end to end
# ---------------------------------------------------------------------------

def test_bridge_is_no_longer_an_unimplemented_command(agb_tree, mac_tree):
    """`main` still dispatches `bridge`, and `cmd_bridge` is now the one lazy
    hop into `agb_mac` (Task 4c) rather than the implementation itself."""
    funcs = conftest.functions(agb_tree)
    assert "cmd_bridge" in funcs
    assert (None, "cmd_bridge") in conftest.calls(funcs["main"])
    made = conftest.calls(funcs["cmd_bridge"])
    assert (None, "_load_mac") in made
    assert (None, "run_bridge") in made
    assert "run_bridge" in conftest.functions(mac_tree)


def test_from_stdin_runs_the_whole_pipeline_without_ssh(run_agb):
    """The seam that makes every Mac-side rule testable on a farm box."""
    stdin = b"\n".join([
        line("snapshot", sessions=[wire("aaaa1111"), wire("bbbb2222")]),
        line("upsert", session=wire("aaaa1111", state="blocked", seq=2)),
        line("tick"),
        line("remove", key="bbbb2222"),
    ]) + b"\n"
    rc, out, err = run_agb(["bridge", "--from-stdin"], stdin=stdin)
    assert rc == 0, err
    assert out.decode().split("\n")[:5] == [
        "upsert aaaa1111 active",
        "upsert bbbb2222 active",
        "upsert aaaa1111 blocked",
        "remove bbbb2222",
        "stale eof",
    ]


def test_from_stdin_survives_a_garbage_line(run_agb):
    stdin = b"garbage\n" + line("tick") + b"\n"
    rc, out, err = run_agb(["bridge", "--from-stdin"], stdin=stdin)
    assert rc == 0, err
    assert out.decode().strip() == "stale eof"
    assert b"unparsable" in err


def test_a_bad_bridge_invocation_exits_non_zero_with_a_message(run_agb):
    rc, out, err = run_agb(["bridge", "--nonsense"])
    assert rc == 1
    assert out == b""
    assert b"unknown option" in err


def test_a_bridge_with_nothing_configured_says_so(run_agb):
    rc, out, err = run_agb(["bridge"])
    assert rc == 1
    assert b"feed_host" in err


def test_the_bridge_spawns_the_documented_ssh(mac, run_agb, ssh_stub):
    """The recording stub: `ssh` cannot be run for real from here, so the claim
    under test is what the bridge *asked* for."""
    ssh_stub.script([line("snapshot", sessions=[wire("aaaa1111")])])
    rc, out, err = run_agb([
        "bridge", "--connections", "1", "--feed-host", HOST, "--mac-id", MAC,
        "--statedir", SD, "--remote-path", REMOTE, "--remote-python", PY,
    ])
    assert rc == 0, err
    assert ssh_stub.calls() == [
        mac.bridge_ssh_argv(HOST, SD, MAC, REMOTE, PY)[1:]]
    assert out.decode().splitlines() == ["upsert aaaa1111 active", "stale eof"]


def test_a_held_connection_ends_on_the_watchdog_and_is_shut_down(run_agb,
                                                                 ssh_stub):
    """End to end with a connection that stays *open* and says nothing -- the
    half-open case ServerAlive cannot see. The stub drains stdin until EOF, so
    the run only terminates if the bridge closes it, which is also what tells a
    real feed to stop touching `bridge/<mac-id>.beat`.
    """
    ssh_stub.script([line("snapshot", sessions=[wire("aaaa1111")])])
    ssh_stub.hold()
    rc, out, err = run_agb([
        "bridge", "--connections", "1", "--watchdog", "0.5",
        "--feed-host", HOST, "--mac-id", MAC, "--statedir", SD,
        "--remote-path", REMOTE, "--remote-python", PY,
    ])
    assert rc == 0, err
    assert out.decode().splitlines() == ["upsert aaaa1111 active",
                                         "stale watchdog"]


def test_the_bridge_reconnects_after_the_feed_dies(run_agb, ssh_stub):
    """A feed that exits immediately is respawned -- once, after a backoff, not
    in a loop. This is the only test here that pays a real backoff (one second),
    because it is the only one asserting that the *process* reconnects."""
    ssh_stub.script([line("tick")])
    rc, out, err = run_agb([
        "bridge", "--connections", "2", "--feed-host", HOST, "--mac-id", MAC,
        "--statedir", SD, "--remote-path", REMOTE, "--remote-python", PY,
    ])
    assert rc == 0, err
    assert len(ssh_stub.calls()) == 2
    assert out.decode().splitlines() == ["stale eof", "live", "stale eof"]


def test_a_missing_ssh_binary_is_reported_rather_than_crashing(run_agb,
                                                               stub_bin,
                                                               monkeypatch):
    """`bridge_spawn` turns the ENOENT into an AgbError so the supervisor can
    warn, mark the rows stale and retry -- rather than dying with a traceback
    inside launchd where nobody reads it."""
    monkeypatch.setenv("PATH", str(stub_bin.path))     # no `ssh` installed
    rc, out, err = run_agb([
        "bridge", "--connections", "1", "--feed-host", HOST, "--mac-id", MAC,
        "--statedir", SD, "--remote-path", REMOTE, "--remote-python", PY,
    ])
    assert rc == 0
    assert b"cannot start the feed" in err
    assert out.decode().strip() == "stale spawn-failed"


# ---------------------------------------------------------------------------
# structural guards
# ---------------------------------------------------------------------------

def test_the_module_top_still_imports_only_the_cheap_four(agb_tree):
    """Task 4a is Mac-side code in a file every hook re-compiles (there is no
    `__pycache__` for an extension-less script). Nothing it needs may be paid
    for at module scope.

    Task 4c moved that code to `agb_mac`, which is why this assertion is still
    about `agb` alone: `agb_mac` may import whatever it likes, because a hook
    never loads it."""
    assert conftest.toplevel_imports(agb_tree) == set(
        ["errno", "os", "sys", "time"])


def bridge_reachable(all_trees):
    """The whole call graph behind `agb bridge`, across both files.

    It starts at `cmd_bridge` in `agb` and crosses into `agb_mac` through
    `_load_mac().run_bridge(argv)`; the `agb.<helper>` calls the Mac side makes
    back into the shared primitives are followed too. The non-vacuity
    assertions are the point: after Task 4c a guard that failed to cross the
    file boundary would pass while checking nothing at all.
    """
    funcs = conftest.functions(*all_trees)
    reachable = conftest.reachable_from(funcs, "cmd_bridge")
    assert "run_bridge" in reachable          # crossed into agb_mac
    assert "bridge_supervise" in reachable
    assert "bridge_settings" in reachable
    assert "read_config" in reachable         # ...and back into agb's helpers
    return funcs, reachable


def test_the_bridge_graph_spans_both_files(all_trees):
    """The guards below are only worth their assertions if the walk reaches the
    Mac-side module; this is that precondition, named."""
    bridge_reachable(all_trees)


def test_the_bridge_never_reads_the_shared_statedir(all_trees):
    """Constraint #10: the Mac cannot read the NFS statedir -- it only ever sees
    the feed stream. A statedir helper reachable from `cmd_bridge` would compile
    and pass every unit test on the farm box, then hang or lie on the Mac, where
    the path does not exist. The local *config* file is a different thing and is
    deliberately not on this list.
    """
    _funcs, reachable = bridge_reachable(all_trees)
    forbidden = set([
        "ensure_statedir", "ensure_session_dir", "state_path", "record_path",
        "session_dir", "marker_path", "sweep_marker_path", "bridge_beat_path",
        "read_state_entry", "read_marker_keys", "list_marker_hosts",
        "rebuild_marker", "reap_entry", "sweep_entry", "feed_poll",
        "breadcrumb", "statedir",
    ])
    assert reachable & forbidden == set()


def test_the_bridge_cannot_unlink_anything(all_trees):
    """The Mac side has no unlink authority at all: every removal in this system
    is proven on the machine that owns the entry (constraint #11)."""
    funcs, reachable = bridge_reachable(all_trees)
    for name in reachable:
        made = conftest.calls(funcs[name])
        assert ("os", "unlink") not in made, name
        assert ("os", "rename") not in made, name


def test_the_watchdog_is_the_only_thing_that_ends_a_quiet_connection(mac_tree):
    """No `time.sleep` inside the connection loop: a sleeping reader is a reader
    that cannot notice EOF, and `bridge_run` must never be the reason a
    disconnect goes unnoticed."""
    node = conftest.functions(mac_tree)["bridge_run"]
    assert ("time", "sleep") not in conftest.calls(node)
    for child in ast.walk(node):
        if isinstance(child, ast.Import):
            raise AssertionError("bridge_run must not import anything")


def test_the_bridge_uses_a_monotonic_clock_for_its_own_timeout(mac_tree):
    """The wire's `now` is the NFS server's clock and drives the *ages*; this
    timeout is local and must not be affected by a clock step."""
    node = conftest.functions(mac_tree)["bridge_run"]
    names = [attr for base, attr in conftest.calls(node)]
    assert "monotonic" in [
        child.attr for child in ast.walk(node)
        if isinstance(child, ast.Attribute)] or "monotonic" in names
    assert ("time", "time") not in conftest.calls(node)


# ---------------------------------------------------------------------------
# the bridge's own log: bounded, because nothing else bounds it
# ---------------------------------------------------------------------------

def test_the_log_is_truncated_once_it_passes_the_cap(mac, tmp_path):
    """launchd's `StandardErrorPath` is append-only with no rotation, and `ssh`'s
    stderr goes into it inherited -- outside the dedup and outside the stamp. An
    afternoon of `Could not resolve hostname` made the last 30 seconds
    unfindable, three diagnosis rounds running."""
    path = tmp_path / "bridge.err.log"
    path.write_bytes(b"x" * 5000)
    fd = os.open(str(path), os.O_WRONLY | os.O_APPEND)
    try:
        assert mac.cap_stderr_log(fd, cap=1000) is True
    finally:
        os.close(fd)
    body = path.read_text()
    assert "x" * 100 not in body, "the history should be gone"
    # A short log must never be mistakable for a quiet one.
    assert "truncated" in body and "before this line is gone" in body
    assert body.count("\n") == 1


def test_a_log_under_the_cap_is_left_alone(mac, tmp_path):
    path = tmp_path / "bridge.err.log"
    path.write_bytes(b"x" * 500)
    fd = os.open(str(path), os.O_WRONLY | os.O_APPEND)
    try:
        assert mac.cap_stderr_log(fd, cap=1000) is False
    finally:
        os.close(fd)
    assert path.read_bytes() == b"x" * 500


def test_a_non_regular_stderr_is_never_truncated(mac):
    """A tty, a pipe or /dev/null is somebody else's fd. This is also what stops
    it truncating a test runner's captured output when fd 2 is a pipe."""
    read_fd, write_fd = os.pipe()
    try:
        assert mac.cap_stderr_log(write_fd, cap=0) is False
    finally:
        os.close(read_fd)
        os.close(write_fd)
    assert mac.cap_stderr_log(-1, cap=0) is False


def test_the_truncation_line_is_stamped(mac, tmp_path):
    """Same reason every other bridge line is: this log has no other clock."""
    path = tmp_path / "bridge.err.log"
    path.write_bytes(b"x" * 5000)
    fd = os.open(str(path), os.O_WRONLY | os.O_APPEND)
    try:
        mac.cap_stderr_log(fd, cap=1000, now=0)
    finally:
        os.close(fd)
    assert "1970-01-01T00:00:00.000Z" in path.read_text()
