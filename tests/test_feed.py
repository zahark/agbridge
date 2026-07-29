"""Task 3a -- `agb feed`: poll loop, cross-host discovery, wire protocol.

The load-bearing tests in here are the ones about what does **not** happen: no
`readdir` of a foreign session directory (the acdirmax=60 trap), no `remove`
from a short read (the truncate-window trap), and exactly one `remove` from an
ENOENT (the strand-a-row-forever trap). Each of those is a bug that would look
like "agterm is flaky" rather than like a defect in this file.
"""

import ast
import errno
import io
import json
import os
import select
import subprocess

import pytest

import conftest


HOST = "box2"
FOREIGN = "box3"          # "machine #3" -- simulated by a differing `host`
MAC = "mac-abc123"

# A **live** agent: this process. Task 3b makes the feed prove own-host
# liveness every poll, so a fabricated pid here would be provably dead and every
# own-host session in this file would be reaped out from under the test it
# belongs to -- turning wire-protocol tests into reaping tests without saying so.
# Sessions that are *meant* to be dead say so explicitly (test_feed_sweep.py).
PID, STARTTIME = conftest.live_agent()


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def sd(agb, statedir, set_host):
    """A created statedir with own_host() pinned, as a plain string."""
    set_host(HOST)
    return str(statedir)


def write_session(agb, sd, host, key, state="active", seq=1, pid=PID,
                  starttime=STARTTIME, label=None, record=True, rebuild=True):
    """Write one session the way a hook would: `.json`, then `.state`, then the
    marker. Returns the key."""
    agb.ensure_session_dir(sd, host)
    if record:
        rec = {
            "v": 1, "key": key, "label": label or ("lbl-" + key), "host": host,
            "pid": pid, "starttime": starttime, "tmux": "sess", "pane": "%24",
            "cwd": "/shared/work/project", "state": state, "seq": seq,
            "updated": 1753716123.4,
        }
        agb.atomic_write(agb.record_path(sd, key, host),
                         json.dumps(rec, sort_keys=True) + "\n")
    agb.write_in_place(agb.state_path(sd, key, host),
                       agb.format_state(state, host, pid, starttime, seq))
    if rebuild:
        agb.rebuild_marker(sd, host)
    return key


def write_marker(agb, sd, host, keys):
    """Write `gen/<host>.marker` directly, bypassing the readdir rebuild.

    Used wherever the point of the test is that discovery works from the
    marker's *content* and not from a directory listing.
    """
    agb.atomic_write(agb.marker_path(sd, host), agb.format_marker(keys))


def age_file(path, seconds):
    """Move a file's mtime `seconds` into the past."""
    st = os.stat(path)
    os.utime(path, (st.st_atime, st.st_mtime - seconds))
    return st.st_mtime - seconds


def bump_mtime(path, seconds=5):
    """Move a file's mtime forward -- a beat refresh, without a content write."""
    st = os.stat(path)
    os.utime(path, (st.st_atime, st.st_mtime + seconds))
    return st.st_mtime + seconds


def bounded_sleep(limit=50):
    """A no-op `sleep` that fails the test after `limit` polls.

    Every test of an *exit condition* runs `feed_loop` with no `iterations`
    bound -- that is the point of them. Without this, a regression in the exit
    condition would hang the suite instead of failing it, and a test that hangs
    is a test nobody runs.
    """
    calls = []

    def sleep(_seconds):
        calls.append(1)
        if len(calls) > limit:
            raise AssertionError(
                "feed_loop did not exit after %d polls" % (limit,))

    return sleep


class Runner(object):
    """A feed with a persistent FeedState, driven one poll at a time."""

    def __init__(self, agb, sd, mac=MAC):
        self.agb = agb
        self.sd = sd
        self.mac = mac
        self.state = agb.FeedState()
        self.out = io.StringIO()
        self._seen = 0

    def poll(self, iterations=1, **kwargs):
        kwargs.setdefault("sleep", lambda _s: None)
        self.rc = self.agb.feed_loop(self.sd, self.mac, out=self.out,
                                     iterations=iterations, state=self.state,
                                     **kwargs)
        return self.lines()

    def lines(self):
        raw = self.out.getvalue().splitlines()
        fresh = raw[self._seen:]
        self._seen = len(raw)
        return [json.loads(line) for line in fresh if line]


@pytest.fixture
def feed(agb, sd):
    return Runner(agb, sd)


def only(lines, kind):
    return [line for line in lines if line["t"] == kind]


# ---------------------------------------------------------------------------
# snapshot
# ---------------------------------------------------------------------------

def test_the_snapshot_is_the_first_line_and_carries_existing_sessions(agb, sd,
                                                                      feed):
    key = write_session(agb, sd, HOST, "a3f9c1e0")
    lines = feed.poll()

    assert lines[0]["t"] == "snapshot"
    assert [line["t"] for line in lines] == ["snapshot"]
    keys = [s["key"] for s in lines[0]["sessions"]]
    assert keys == [key]


def test_the_snapshot_is_empty_rather_than_absent_when_nothing_has_run(agb, sd,
                                                                       feed):
    """"No sessions" and "no feed" must be distinguishable on the wire from the
    very first line, or the bridge cannot tell a fresh farm from a dead one.

    But an empty `gen/` is **not** authority to remove anything: the feed read
    no marker at all, so it has no evidence about the farm, and `complete` says
    so. `ensure_statedir` creates `gen/`, which is why a statedir the Mac and
    the farm disagree about would otherwise arrive as "the farm is empty".
    """
    lines = feed.poll()
    assert lines == [{"t": "snapshot", "now": lines[0]["now"], "sessions": [],
                      "complete": False}]


def test_the_snapshot_carries_every_host(agb, sd, feed):
    write_session(agb, sd, HOST, "a3f9c1e0")
    write_session(agb, sd, FOREIGN, "b1b2b3b4")

    sessions = feed.poll()[0]["sessions"]
    assert sorted(s["host"] for s in sessions) == [HOST, FOREIGN]


def test_a_foreign_host_entry_is_discovered_without_listing_its_directory(
        agb, sd, feed, monkeypatch):
    """Machine #3: it writes to the same NFS path and costs the feed nothing
    beyond one `open()` per key."""
    write_session(agb, sd, FOREIGN, "b1b2b3b4")

    listed = []
    real = os.listdir

    def spy(path=".", *args, **kwargs):
        listed.append(str(path))
        return real(path, *args, **kwargs)

    monkeypatch.setattr(os, "listdir", spy)
    sessions = feed.poll()[0]["sessions"]

    assert [s["host"] for s in sessions] == [FOREIGN]
    assert listed == [agb.gen_dir(sd)]


def test_the_wire_record_carries_the_beat_the_stored_record_never_does(agb, sd,
                                                                      feed):
    """Design amendment 3: `beat` is `.state`'s mtime, synthesized here."""
    key = write_session(agb, sd, HOST, "a3f9c1e0")
    session = feed.poll()[0]["sessions"][0]

    with open(agb.record_path(sd, key, HOST)) as handle:
        stored = json.load(handle)
    assert "beat" not in stored
    assert session["beat"] == os.stat(agb.state_path(sd, key, HOST)).st_mtime


def test_the_wire_record_merges_the_record_and_the_state_file(agb, sd, feed):
    key = write_session(agb, sd, HOST, "a3f9c1e0", state="blocked", seq=7,
                        label="build")
    session = feed.poll()[0]["sessions"][0]

    assert session["label"] == "build"          # from `.json`
    assert session["cwd"] == "/shared/work/project"
    assert session["pane"] == "%24"
    assert session["state"] == "blocked"         # from `.state`
    assert session["seq"] == 7
    assert session["pid"] == PID
    assert session["starttime"] == STARTTIME
    assert session["key"] == key and session["host"] == HOST


def test_an_unreadable_record_still_yields_a_row(agb, sd, feed):
    """A row with a poor label beats no row at all: the session is provably
    live, and dropping it would be an inference from a failed read."""
    key = write_session(agb, sd, HOST, "a3f9c1e0", record=False)
    session = feed.poll()[0]["sessions"][0]

    assert session["key"] == key
    assert session["label"] == key
    assert session["state"] == "active"


# ---------------------------------------------------------------------------
# `now` -- one clock domain for every subtraction (constraint #12)
# ---------------------------------------------------------------------------

def test_now_is_the_bridge_beats_server_stamped_mtime(agb, sd, feed):
    """Both sides of every age comparison have to be stamped by the same clock.
    `time.time()` here would make each one a cross-clock subtraction between the
    feed's host and the writer's."""
    write_session(agb, sd, HOST, "a3f9c1e0")
    line = feed.poll()[0]

    assert line["now"] == os.stat(agb.bridge_beat_path(sd, MAC)).st_mtime


def test_every_line_carries_now(agb, sd, feed):
    key = write_session(agb, sd, HOST, "a3f9c1e0")
    lines = feed.poll()                                   # snapshot
    write_session(agb, sd, HOST, key, state="blocked", seq=2)
    lines += feed.poll()                                  # upsert
    lines += feed.poll()                                  # tick
    os.unlink(agb.state_path(sd, key, HOST))
    lines += feed.poll()                                  # remove

    assert sorted(set(line["t"] for line in lines)) == [
        "remove", "snapshot", "tick", "upsert"]
    for line in lines:
        assert isinstance(line["now"], float), line


def test_the_bridge_beat_is_touched_every_poll(agb, sd, feed):
    feed.poll()
    path = agb.bridge_beat_path(sd, MAC)
    first = os.stat(path)
    aged = age_file(path, 60)

    feed.poll()
    assert os.stat(path).st_mtime > aged
    assert os.stat(path).st_ino == first.st_ino   # in place, never renamed


def test_the_bridge_beat_directory_is_created_on_demand(agb, sd, feed):
    import shutil
    shutil.rmtree(os.path.join(sd, "bridge"))
    feed.poll()
    assert os.path.exists(agb.bridge_beat_path(sd, MAC))


# ---------------------------------------------------------------------------
# upsert / tick
# ---------------------------------------------------------------------------

def test_a_seq_change_emits_exactly_one_upsert(agb, sd, feed):
    key = write_session(agb, sd, HOST, "a3f9c1e0")
    feed.poll()

    write_session(agb, sd, HOST, key, state="blocked", seq=2)
    lines = feed.poll()

    assert [line["t"] for line in lines] == ["upsert"]
    assert lines[0]["session"]["state"] == "blocked"
    assert lines[0]["session"]["seq"] == 2


def test_no_change_emits_only_a_tick(agb, sd, feed):
    write_session(agb, sd, HOST, "a3f9c1e0")
    feed.poll()

    lines = feed.poll()
    assert [line["t"] for line in lines] == ["tick"]
    assert set(lines[0]) == set(["t", "now"])


def test_a_beat_refresh_emits_an_upsert_with_the_new_beat(agb, sd, feed):
    """A beat that moved is a change, so it goes on the wire: the bridge shows
    beat age in the row title, and a title that ages while the agent is beating
    is the "dashboard that lies" failure in miniature. The `.json` is *not*
    re-read for it -- only a moving `seq` costs an open (checked below)."""
    key = write_session(agb, sd, HOST, "a3f9c1e0")
    feed.poll()

    fresh = bump_mtime(agb.state_path(sd, key, HOST), 20)
    lines = feed.poll()

    assert [line["t"] for line in lines] == ["upsert"]
    assert lines[0]["session"]["beat"] == fresh
    assert lines[0]["session"]["seq"] == 1


def test_the_record_is_read_only_at_snapshot_time_and_when_seq_moves(agb, sd,
                                                                     feed,
                                                                     monkeypatch):
    key = write_session(agb, sd, HOST, "a3f9c1e0")

    opened = []
    real = os.open

    def spy(path, *args, **kwargs):
        if str(path).endswith(".json"):
            opened.append(str(path))
        return real(path, *args, **kwargs)

    monkeypatch.setattr(os, "open", spy)

    feed.poll()                                   # snapshot: one read
    assert len(opened) == 1

    bump_mtime(agb.state_path(sd, key, HOST), 20)
    feed.poll()                                   # beat only: no read
    assert len(opened) == 1

    feed.poll()                                   # nothing at all: no read
    assert len(opened) == 1

    write_session(agb, sd, HOST, key, state="completed", seq=2)
    feed.poll()                                   # seq moved: one read
    assert len(opened) == 2


# ---------------------------------------------------------------------------
# discovery -- the marker's content, never a foreign readdir (constraint #5)
# ---------------------------------------------------------------------------

def test_a_new_key_is_discovered_from_the_marker_content_alone(agb, sd, feed,
                                                               monkeypatch):
    """The acdirmax=60 case, made deterministic: every session listing is
    stubbed stale, so a readdir-based discovery would find nothing at all."""
    agb.ensure_session_dir(sd, FOREIGN)
    real = os.listdir

    def stale(path=".", *args, **kwargs):
        if os.path.join(sd, "sessions") in str(path):
            return []          # a 60 s old cached listing
        return real(path, *args, **kwargs)

    monkeypatch.setattr(os, "listdir", stale)

    key = "b1b2b3b4"
    agb.write_in_place(agb.state_path(sd, key, FOREIGN),
                       agb.format_state("active", FOREIGN, PID, STARTTIME, 1))
    write_marker(agb, sd, FOREIGN, [key])

    sessions = feed.poll()[0]["sessions"]
    assert [s["key"] for s in sessions] == [key]


def test_the_feed_lists_only_the_gen_directory(agb, sd, feed, monkeypatch):
    """`readdir` of a foreign session directory can be served from cache for up
    to acdirmax=60 s, so the feed must never perform one. `gen/` is listed to
    enumerate *hosts* -- which is exactly the up-to-60 s new-host discovery
    latency the plan accepts and documents."""
    write_session(agb, sd, HOST, "a3f9c1e0")
    write_session(agb, sd, FOREIGN, "b1b2b3b4")

    listed = []
    real = os.listdir

    def spy(path=".", *args, **kwargs):
        listed.append(str(path))
        return real(path, *args, **kwargs)

    monkeypatch.setattr(os, "listdir", spy)
    feed.poll(iterations=3)

    assert set(listed) == set([agb.gen_dir(sd)])


def test_a_marker_temp_is_not_mistaken_for_a_host(agb, sd, feed):
    write_session(agb, sd, HOST, "a3f9c1e0")
    stray = os.path.join(agb.gen_dir(sd), "weird.tmp.box9.7.deadbeef.marker")
    with open(stray, "w") as handle:
        handle.write("nonsense\n")

    assert agb.list_marker_hosts(sd) == [HOST]
    assert [s["host"] for s in feed.poll()[0]["sessions"]] == [HOST]


def test_a_marker_name_that_is_not_a_usable_host_is_not_a_host(agb, sd, feed):
    """`list_marker_hosts` was the one host source in the tool that returned
    whatever preceded `.marker` without a validator, and that string goes
    straight into `session_dir`/`marker_path`/`prune_remove` as a path
    component. Not exploitable -- the statedir is 0700 with a uid check -- but
    every OTHER host source is validated, and an odd exception in the one place
    that builds paths from a directory listing is not a distinction worth
    keeping."""
    write_session(agb, sd, HOST, "a3f9c1e0")
    for bad in ("..", ".", "with space", "a" * 65):
        with open(os.path.join(agb.gen_dir(sd), bad + ".marker"), "w") as fh:
            fh.write("#end 0\n")

    assert agb.list_marker_hosts(sd) == [HOST]
    assert [s["host"] for s in feed.poll()[0]["sessions"]] == [HOST]


def test_a_missing_gen_directory_is_not_an_error(agb, sd, feed):
    import shutil
    shutil.rmtree(agb.gen_dir(sd))
    assert agb.list_marker_hosts(sd) == []
    assert feed.poll()[0]["sessions"] == []


def test_an_orphan_temp_never_appears_in_a_snapshot(agb, sd, feed):
    key = write_session(agb, sd, HOST, "a3f9c1e0", rebuild=False)
    orphan = agb.state_path(sd, "c0ffee11", HOST) + ".tmp.box2.999.abcd1234"
    with open(orphan, "w") as handle:
        handle.write(agb.format_state("active", HOST, PID, STARTTIME, 1))
    agb.rebuild_marker(sd, HOST)

    sessions = feed.poll()[0]["sessions"]
    assert [s["key"] for s in sessions] == [key]


# ---------------------------------------------------------------------------
# removal -- positive proof only (constraint #8)
# ---------------------------------------------------------------------------

def test_enoent_on_a_marker_listed_key_emits_exactly_one_remove(agb, sd, feed):
    """The sibling of the truncate-window bug, and the one that would otherwise
    strand a row permanently: the marker still lists the key (its rebuild is not
    atomic with the unlink), so nothing but this ENOENT can clear the row."""
    key = write_session(agb, sd, HOST, "a3f9c1e0")
    assert len(feed.poll()[0]["sessions"]) == 1

    os.unlink(agb.state_path(sd, key, HOST))     # marker deliberately untouched
    assert agb.parse_marker(open(agb.marker_path(sd, HOST), "rb").read()) == [key]

    lines = feed.poll()
    assert [line["t"] for line in lines] == ["remove"]
    assert lines[0]["key"] == key

    assert [line["t"] for line in feed.poll()] == ["tick"]
    assert [line["t"] for line in feed.poll()] == ["tick"]


def test_a_removed_key_is_absent_from_a_later_snapshot(agb, sd, feed):
    key = write_session(agb, sd, HOST, "a3f9c1e0")
    feed.poll()
    os.unlink(agb.state_path(sd, key, HOST))
    agb.rebuild_marker(sd, HOST)
    feed.poll()

    fresh = Runner(agb, sd)
    assert fresh.poll()[0]["sessions"] == []


def test_a_key_unlinked_between_the_marker_and_the_open_is_simply_absent(agb, sd,
                                                                        feed):
    """First poll, so there is nothing to remove -- the key must not appear and
    must not raise."""
    write_marker(agb, sd, HOST, ["a3f9c1e0"])
    agb.ensure_session_dir(sd, HOST)
    lines = feed.poll()
    assert lines[0]["sessions"] == []
    assert only(lines, "remove") == []


def test_a_key_that_leaves_the_marker_is_still_probed(agb, sd, feed):
    """Otherwise an entry that vanished from a marker without its `.state` being
    unlinked would be retained forever, with nothing able to clear it."""
    key = write_session(agb, sd, HOST, "a3f9c1e0")
    feed.poll()

    write_marker(agb, sd, HOST, [])              # marker says nothing
    assert [line["t"] for line in feed.poll()] == ["tick"]   # `.state` is alive

    os.unlink(agb.state_path(sd, key, HOST))
    lines = feed.poll()
    assert [line["t"] for line in lines] == ["remove"]
    assert lines[0]["key"] == key


@pytest.mark.parametrize("payload", [
    b"",                                          # the in-place O_TRUNC window
    b"active\n",                                  # a short read
    b"active\nbox2\n1\n2\n3\n4\n",                # six lines
    b"nonsense\nbox2\n1\n2\n3\n",                 # out of vocabulary
])
def test_a_torn_state_read_is_no_information_and_never_removes(agb, sd, feed,
                                                               payload):
    """Revision 3's bug, as an executable guard. A peer hook mid-`O_TRUNC`
    leaves `.state` momentarily zero-length; reading that as "empty" rather than
    as "no information" emits `remove` for a live agent."""
    key = write_session(agb, sd, HOST, "a3f9c1e0")
    before = feed.poll()[0]["sessions"][0]

    with open(agb.state_path(sd, key, HOST), "wb") as handle:
        handle.write(payload)

    lines = feed.poll()
    assert only(lines, "remove") == []
    assert [line["t"] for line in lines] == ["tick"]
    assert feed.state.entries[key] == before      # the previous value, retained


# ---------------------------------------------------------------------------
# snapshot completeness -- constraint #8 for the ONE poll retention cannot cover
# ---------------------------------------------------------------------------
#
# Retention makes an unreadable file harmless from poll 2 on: the previous value
# stands. Poll 1 has nothing to retain, so a key it could not read is simply
# ABSENT from the snapshot -- and the bridge reads a snapshot as the whole truth
# about the farm and removes everything missing from it. On the Mac that removal
# is irreversible without `agb close-done`. The `complete` flag is what makes
# "I could not read this" distinguishable from "this is gone" on the wire.
#
# ⚠️ `complete` alone is only half the contract, and the half that was missing
# cost a live row. A snapshot is the feed's CLAIM to removal authority; the
# claim also has to ARRIVE. The feed used to emit exactly one snapshot per
# connection, so an incomplete poll 1 withdrew the authority for the whole ssh
# -- days, under launchd -- and an entry that was in no marker was then
# unreachable by every command there is. `owed` is the retry.

def test_a_snapshot_that_read_everything_says_so(agb, sd, feed):
    write_session(agb, sd, HOST, "a3f9c1e0")
    assert feed.poll()[0]["complete"] is True


def test_a_torn_marker_makes_the_first_snapshot_incomplete(agb, sd, feed):
    """A brand-new feed process after a reconnect: every foreign host is new to
    it, so there is nothing retained to cover an unreadable marker."""
    write_session(agb, sd, HOST, "a3f9c1e0")
    write_marker(agb, sd, FOREIGN, ["b1b2b3b4"])
    with open(agb.marker_path(sd, FOREIGN), "wb") as handle:
        handle.write(b"b1b2b3b4\n")               # sentinel gone: no information

    snapshot = feed.poll()[0]
    assert snapshot["complete"] is False
    assert [s["key"] for s in snapshot["sessions"]] == ["a3f9c1e0"]


def test_a_torn_state_makes_the_snapshot_incomplete(agb, sd, feed):
    key = write_session(agb, sd, HOST, "a3f9c1e0")
    write_session(agb, sd, HOST, "b1b2b3b4")
    with open(agb.state_path(sd, key, HOST), "wb") as handle:
        handle.write(b"")                         # the in-place O_TRUNC window

    snapshot = feed.poll()[0]
    assert snapshot["complete"] is False
    assert [s["key"] for s in snapshot["sessions"]] == ["b1b2b3b4"]


def test_a_poll_that_raised_makes_the_snapshot_incomplete(agb, sd, feed,
                                                          monkeypatch):
    """The sulking hard mount: `feed_poll` itself raises, `events` becomes []
    and the snapshot would otherwise claim the farm is empty."""
    def boom(*_a, **_k):
        raise OSError(errno.EIO, "the server is not answering")

    monkeypatch.setattr(agb, "feed_poll", boom)
    snapshot = feed.poll()[0]
    assert (snapshot["sessions"], snapshot["complete"]) == ([], False)


def test_completeness_recovers_once_the_reads_succeed(agb, sd, feed):
    """`complete` is per-poll, not sticky: a flag that latched would make every
    later snapshot in the process unable to authorise a removal.

    ⚠️ Recovering is necessary and not sufficient -- nothing here consumes the
    recovered flag, and for one release nothing in production did either. The
    tests below (`owed`) are what turn it into a snapshot that arrives.
    """
    key = write_session(agb, sd, HOST, "a3f9c1e0")
    with open(agb.state_path(sd, key, HOST), "wb") as handle:
        handle.write(b"")
    feed.poll()
    assert feed.state.complete is False

    write_session(agb, sd, HOST, key)
    feed.poll()
    assert feed.state.complete is True


# -- the claim has to arrive: `owed` ----------------------------------------

def test_an_incomplete_snapshot_is_re_emitted_on_the_first_complete_poll(
        agb, sd, feed):
    """The fix for the defect the flag itself introduced. `complete` recovering
    is worth nothing if NOTHING CONSUMES IT: the feed emitted one snapshot per
    connection, so a poll-1 short read forfeited removal until the ssh dropped.
    """
    key = write_session(agb, sd, HOST, "a3f9c1e0")
    with open(agb.state_path(sd, key, HOST), "wb") as handle:
        handle.write(b"")                          # the O_TRUNC window
    first = feed.poll()
    assert (first[0]["t"], first[0]["complete"]) == ("snapshot", False)

    write_session(agb, sd, HOST, key)
    second = only(feed.poll(), "snapshot")
    assert len(second) == 1
    assert second[0]["complete"] is True
    assert [s["key"] for s in second[0]["sessions"]] == [key]
    # ...and exactly once. A snapshot per poll would replace the delta stream.
    assert only(feed.poll(), "snapshot") == []


def test_the_re_emitted_snapshot_carries_every_entry_not_just_this_polls(
        agb, sd, feed):
    """A snapshot is a REPLACEMENT, so a second one built from the poll's own
    upserts would remove every session that had not changed since poll 1."""
    write_session(agb, sd, HOST, "a3f9c1e0")
    write_marker(agb, sd, FOREIGN, ["b1b2b3b4"])
    with open(agb.marker_path(sd, FOREIGN), "wb") as handle:
        handle.write(b"b1b2b3b4\n")                # no sentinel: no information
    assert feed.poll()[0]["complete"] is False

    write_session(agb, sd, FOREIGN, "b1b2b3b4")
    snapshot = only(feed.poll(), "snapshot")[0]
    assert snapshot["complete"] is True
    assert sorted(s["key"] for s in snapshot["sessions"]) == ["a3f9c1e0",
                                                              "b1b2b3b4"]


def test_a_snapshot_that_could_claim_authority_owes_nothing_further(agb, sd,
                                                                     feed):
    write_session(agb, sd, HOST, "a3f9c1e0")
    assert feed.poll()[0]["complete"] is True
    assert feed.state.owed is False
    assert only(feed.poll(), "snapshot") == []


def test_a_later_incomplete_poll_re_arms_the_owed_snapshot(agb, sd, feed):
    """`owed` was only ever CLEARED, never re-armed, so an incomplete poll after
    the first complete snapshot scheduled no re-sync at all. Nothing reachable
    lost data through it, but the invariant -- "a snapshot is owed whenever the
    last one no longer speaks for the farm" -- was false as written."""
    key = write_session(agb, sd, HOST, "a3f9c1e0")
    assert feed.poll()[0]["complete"] is True
    assert feed.state.owed is False

    with open(agb.state_path(sd, key, HOST), "wb") as handle:
        handle.write(b"")                          # the O_TRUNC window
    assert only(feed.poll(), "snapshot") == []     # nothing to claim WITH...
    assert feed.state.owed is True                 # ...but one is owed now

    write_session(agb, sd, HOST, key)
    again = only(feed.poll(), "snapshot")
    assert len(again) == 1
    assert again[0]["complete"] is True


# -- a withheld authority is never silent -----------------------------------
#
# ⚠️ Both denials used to be silent, which is tolerable only while the condition
# is transient -- and it need not be. `write_in_place` opens `.state` with
# `O_TRUNC`, so a hook killed in that window leaves a ZERO-LENGTH file, and if
# its agent is dead nothing in the tool could then remove it: the sweep skips an
# unparseable `.state`, the feed only removes on a positive ENOENT, and `prune`
# refused it. Every poll of every feed process then said `complete: false` with
# not one byte of diagnostic, for ever. `_warn_once` dedups by exact text, so
# saying it costs one launchd-log line per process.

def test_a_marker_it_cannot_read_is_said_out_loud(agb, sd):
    write_session(agb, sd, HOST, "a3f9c1e0")
    write_marker(agb, sd, FOREIGN, ["b1b2b3b4"])
    with open(agb.marker_path(sd, FOREIGN), "wb") as handle:
        handle.write(b"b1b2b3b4\n")                # sentinel gone
    said = []

    state = agb.FeedState()
    agb.feed_poll(sd, state, warn=said.append)

    assert state.complete is False
    assert [line for line in said
            if "gen/%s.marker" % (FOREIGN,) in line], said


def test_a_state_it_cannot_read_is_said_out_loud(agb, sd):
    key = write_session(agb, sd, HOST, "a3f9c1e0")
    write_session(agb, sd, HOST, "b1b2b3b4")
    with open(agb.state_path(sd, key, HOST), "wb") as handle:
        handle.write(b"")
    said = []

    state = agb.FeedState()
    agb.feed_poll(sd, state, warn=said.append)

    assert state.complete is False
    named = [line for line in said if "%s/%s.state" % (HOST, key) in line]
    assert len(named) == 1, said
    assert "no snapshot may remove anything it names" in named[0]
    # ...and only about the entry it really could not read.
    assert [line for line in said if "b1b2b3b4" in line] == []


def test_a_permanent_denial_reaches_stderr_once_not_once_per_poll(agb, sd,
                                                                   feed,
                                                                   capsys):
    """The cost of saying it, measured: one line for a condition that lasts for
    the life of the process. Diagnostics go to stderr; stdout is the protocol."""
    key = write_session(agb, sd, HOST, "a3f9c1e0")
    with open(agb.state_path(sd, key, HOST), "wb") as handle:
        handle.write(b"")

    feed.poll(iterations=5)

    captured = capsys.readouterr()
    assert captured.err.count("%s/%s.state" % (HOST, key)) == 1
    assert "no snapshot may remove anything it names" in captured.err


# -- authority needs a marker source at all ---------------------------------

def test_an_empty_gen_never_claims_the_farm_is_empty(agb, sd, feed):
    """The one path where the flag said `true` on a pure absence of data. A
    statedir the Mac and the farm disagree about, a recreated one, or an ssh
    that lands before the automount is up all arrive here -- and `cmd_feed`
    calls `ensure_statedir`, which CREATES the tree, so "missing" and "empty"
    are the same thing by the time the poll looks. Claiming completeness would
    mark every live row [done], undoable only by `agb close-done`."""
    assert os.path.isdir(agb.gen_dir(sd))          # created, and still empty
    snapshot = feed.poll()[0]
    assert (snapshot["sessions"], snapshot["complete"]) == ([], False)


def test_one_marker_is_enough_to_restore_authority(agb, sd, feed):
    """The other half: a farm where an agent has run is authoritative again on
    the next poll, so a genuinely finished session is still reclaimable."""
    feed.poll()
    assert feed.state.complete is False

    write_session(agb, sd, HOST, "a3f9c1e0")
    snapshot = only(feed.poll(), "snapshot")[0]
    assert snapshot["complete"] is True
    assert [s["key"] for s in snapshot["sessions"]] == ["a3f9c1e0"]


def test_a_host_seen_once_keeps_its_marker_source_across_a_gen_outage(agb, sd,
                                                                       feed):
    """`fs.markers` is the memory: once a host is known, an empty `gen/` is a
    torn read of a farm we have evidence about, not an absence of evidence --
    and `read_marker_keys` denies completeness for its own reason."""
    write_session(agb, sd, HOST, "a3f9c1e0")
    feed.poll()
    os.unlink(agb.marker_path(sd, HOST))
    feed.poll()
    assert feed.state.markers[HOST] == ["a3f9c1e0"]
    assert feed.state.complete is False


@pytest.mark.parametrize("payload", [
    b"",                                          # no sentinel at all
    b"a3f9c1e0\n",                                # sentinel missing
    b"a3f9c1e0\n#end 2\n",                        # count mismatch
    b"a3f9c1e0\n#end\n",                          # malformed sentinel
    b"a3f9c1e0\nnot-a-key\n#end 2\n",             # a key that cannot be one
])
def test_a_marker_that_fails_validation_removes_nothing(agb, sd, feed, payload):
    """One misreading of a marker would otherwise emit `remove` for every row on
    the host at once -- which is why the marker is temp+rename and why this
    returns "no information" rather than an empty list."""
    key = write_session(agb, sd, HOST, "a3f9c1e0")
    feed.poll()

    with open(agb.marker_path(sd, HOST), "wb") as handle:
        handle.write(payload)

    lines = feed.poll()
    assert only(lines, "remove") == []
    assert list(feed.state.entries) == [key]
    assert feed.state.markers[HOST] == [key]      # last good content, retained


@pytest.mark.parametrize("break_it", ["missing", "unreadable"])
def test_a_marker_that_cannot_be_read_is_no_information_never_an_empty_list(
        agb, sd, break_it):
    """The contract, asserted directly on the reader rather than through the
    loop: `[]` here would mean "this host has no sessions". The marker's name is
    *not* random, so constraint #5's "no cached negative dentry can manufacture
    a false ENOENT" argument does not cover it -- and it does not need to,
    because removal is proven per key, by name.

    Asserted on `read_marker_keys` because `feed_poll` masks the difference: it
    re-probes a key that left a marker, so a wrong `[]` here is invisible at
    loop level until the *first* poll of a fresh feed drops the whole host.
    """
    write_session(agb, sd, HOST, "a3f9c1e0")
    path = agb.marker_path(sd, HOST)
    if break_it == "missing":
        os.unlink(path)
    else:
        os.chmod(path, 0o000)
    try:
        assert agb.read_marker_keys(sd, HOST) is None
    finally:
        if break_it == "unreadable":
            os.chmod(path, 0o600)


def test_a_vanished_marker_removes_nothing_by_itself(agb, sd, feed):
    key = write_session(agb, sd, HOST, "a3f9c1e0")
    feed.poll()
    os.unlink(agb.marker_path(sd, HOST))

    assert [line["t"] for line in feed.poll()] == ["tick"]
    assert list(feed.state.entries) == [key]
    assert feed.state.markers[HOST] == [key]      # last good content, retained


def test_an_unreadable_state_file_removes_nothing(agb, sd, feed):
    key = write_session(agb, sd, HOST, "a3f9c1e0")
    feed.poll()
    path = agb.state_path(sd, key, HOST)
    os.chmod(path, 0o000)
    try:
        lines = feed.poll()
    finally:
        os.chmod(path, 0o600)
    assert only(lines, "remove") == []
    assert list(feed.state.entries) == [key]


def test_a_persistent_estale_removes_nothing(agb, sd, feed, monkeypatch):
    key = write_session(agb, sd, HOST, "a3f9c1e0")
    feed.poll()

    path = agb.state_path(sd, key, HOST)
    real = os.open

    def stale(target, *args, **kwargs):
        if str(target) == path:
            raise OSError(errno.ESTALE, "Stale file handle")
        return real(target, *args, **kwargs)

    monkeypatch.setattr(os, "open", stale)
    lines = feed.poll()

    assert only(lines, "remove") == []
    assert list(feed.state.entries) == [key]


def test_estale_is_retried_rather_than_skipped(agb, sd, feed, monkeypatch):
    """Constraint #9: skipping turns a transient NFS condition into a row that
    flaps. `read_fresh` re-looks-up and retries, so the entry is never missed."""
    key = write_session(agb, sd, HOST, "a3f9c1e0")
    path = agb.state_path(sd, key, HOST)
    real = os.open
    hits = []

    def flaky(target, *args, **kwargs):
        if str(target) == path and not hits:
            hits.append(1)
            raise OSError(errno.ESTALE, "Stale file handle")
        return real(target, *args, **kwargs)

    monkeypatch.setattr(os, "open", flaky)
    sessions = feed.poll()[0]["sessions"]

    assert hits == [1]
    assert [s["key"] for s in sessions] == [key]


# ---------------------------------------------------------------------------
# exit conditions
# ---------------------------------------------------------------------------

def test_the_feed_exits_on_stdin_eof(agb, sd, feed):
    """`iterations` is unset: only the EOF can end this loop."""
    read_fd, write_fd = os.pipe()
    os.close(write_fd)
    try:
        rc = agb.feed_loop(sd, MAC, out=feed.out, stdin_fd=read_fd,
                           sleep=bounded_sleep())
    finally:
        os.close(read_fd)
    assert rc == 0
    assert [line["t"] for line in feed.lines()] == ["snapshot"]


def test_stdin_data_does_not_end_the_feed(agb, sd, feed):
    """A readable fd is not EOF -- only a `read()` of `b""` proves that."""
    read_fd, write_fd = os.pipe()
    os.write(write_fd, b'{"hello":1}\n')
    try:
        rc = agb.feed_loop(sd, MAC, out=feed.out, stdin_fd=read_fd,
                           iterations=3, sleep=lambda _s: None)
    finally:
        os.close(read_fd)
        os.close(write_fd)
    assert rc == 0
    assert [line["t"] for line in feed.lines()] == ["snapshot", "tick", "tick"]


def test_the_feed_stops_when_the_stream_breaks(agb, sd):
    """EPIPE means the Mac is gone. An orphaned feed would keep touching
    `bridge/<mac-id>.beat`, which makes `bridge:UP` a lie on the farm side."""

    class Broken(object):
        def __init__(self):
            self.writes = 0

        def write(self, _text):
            self.writes += 1
            raise IOError(errno.EPIPE, "Broken pipe")

        def flush(self):
            pass

    out = Broken()
    rc = agb.feed_loop(sd, MAC, out=out, sleep=bounded_sleep())
    assert rc == 0
    assert out.writes == 1


def test_the_feed_stops_on_a_broken_pipe_mid_burst(agb, sd, feed):
    key = write_session(agb, sd, HOST, "a3f9c1e0")
    write_session(agb, sd, HOST, "b1b2b3b4")
    feed.poll()
    write_session(agb, sd, HOST, key, state="blocked", seq=2)
    write_session(agb, sd, HOST, "b1b2b3b4", state="blocked", seq=2)

    class Broken(object):
        def __init__(self):
            self.writes = 0

        def write(self, _text):
            self.writes += 1
            raise IOError(errno.EPIPE, "Broken pipe")

        def flush(self):
            pass

    out = Broken()
    rc = agb.feed_loop(sd, MAC, out=out, state=feed.state,
                       sleep=bounded_sleep())
    assert rc == 0
    assert out.writes == 1        # stops at the first failure, not after all


def test_the_feed_exits_when_its_stdout_pipe_closes(agb, agb_path, statedir,
                                                    set_host):
    """End to end, through the real interpreter: this is what a `bridge`
    shutdown looks like from the farm side."""
    set_host(HOST)
    sd = str(statedir)
    write_session(agb, sd, HOST, "a3f9c1e0")
    env = dict(os.environ)
    env.update({"AGB_STATEDIR": sd, "AGB_HOST": HOST})

    proc = subprocess.Popen(
        conftest.AGB_ARGV + ["feed", MAC, "--poll-interval", "0.05"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, env=env)
    try:
        first = json.loads(proc.stdout.readline().decode())
        assert first["t"] == "snapshot"
        assert [s["key"] for s in first["sessions"]] == ["a3f9c1e0"]
        proc.stdout.close()
        assert proc.wait(timeout=30) == 0
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            if stream is not None and not stream.closed:
                stream.close()


def test_the_feed_exits_when_its_stdin_closes_end_to_end(agb, agb_path,
                                                         statedir, set_host):
    set_host(HOST)
    sd = str(statedir)
    env = dict(os.environ)
    env.update({"AGB_STATEDIR": sd, "AGB_HOST": HOST})

    proc = subprocess.Popen(
        conftest.AGB_ARGV + ["feed", MAC, "--poll-interval", "0.05"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, env=env)
    try:
        assert json.loads(proc.stdout.readline().decode())["t"] == "snapshot"
        proc.stdin.close()
        assert proc.wait(timeout=30) == 0
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            if stream is not None and not stream.closed:
                stream.close()


# ---------------------------------------------------------------------------
# argument parsing and the environment (`ssh host cmd` sources no profile)
# ---------------------------------------------------------------------------

def test_feed_args_defaults(agb):
    opts = agb.parse_feed_args([MAC])
    assert opts == {"mac_id": MAC, "iterations": None,
                    "poll_interval": agb.FEED_POLL_INTERVAL}


@pytest.mark.parametrize("argv", [
    ["--poll-interval", "0.5", "--iterations", "3", MAC],
    ["--poll-interval=0.5", "--iterations=3", MAC],
    [MAC, "--poll-interval", "0.5", "--iterations", "3"],
])
def test_feed_args_accept_both_spellings_and_either_order(agb, argv):
    opts = agb.parse_feed_args(argv)
    assert opts["mac_id"] == MAC
    assert opts["poll_interval"] == 0.5
    assert opts["iterations"] == 3


@pytest.mark.parametrize("argv", [
    [],                                 # no mac-id
    ["--once", MAC],                    # dropped: it is `--iterations 1`
    ["--nope", MAC],
    ["--iterations"],                   # missing value
    ["--iterations", "x", MAC],
    ["--iterations", "0", MAC],
    ["--poll-interval", "0", MAC],
    ["--poll-interval", "-1", MAC],
    [MAC, "extra"],
    ["../escape"],                      # a mac-id is a path component
    ["mac/1"],
])
def test_feed_args_are_rejected_loudly(agb, argv):
    with pytest.raises(agb.AgbError):
        agb.parse_feed_args(argv)


def test_the_feed_takes_its_statedir_from_the_environment(agb, statedir,
                                                          set_host, run_agb):
    """`ssh host cmd` sources no profile, so the bridge passes
    `env AGB_STATEDIR=… agb feed`. Nothing else supplies it."""
    set_host(HOST)
    sd = str(statedir)
    write_session(agb, sd, HOST, "a3f9c1e0")
    rc, out, err = run_agb(["feed", MAC, "--iterations", "1"],
                           env={"AGB_STATEDIR": sd, "AGB_HOST": HOST})
    assert rc == 0, err
    line = json.loads(out.decode().splitlines()[0])
    assert line["t"] == "snapshot"
    assert [s["key"] for s in line["sessions"]] == ["a3f9c1e0"]


def test_a_bad_feed_invocation_exits_non_zero_with_a_message(run_agb):
    rc, out, err = run_agb(["feed"])
    assert rc != 0
    assert out == b""
    assert b"mac-id" in err


# ---------------------------------------------------------------------------
# error cases -- degraded, never dead
# ---------------------------------------------------------------------------

def test_a_missing_statedir_still_snapshots_and_ticks(agb, tmp_path, set_host,
                                                      monkeypatch):
    """A feed that exited on a missing directory would look, from the Mac,
    exactly like a dead ssh -- which is the diagnosis this tool exists to make
    unnecessary."""
    set_host(HOST)
    missing = str(tmp_path / "gone" / "state")
    out = io.StringIO()
    rc = agb.feed_loop(missing, MAC, out=out, iterations=2,
                       sleep=lambda _s: None)
    lines = [json.loads(line) for line in out.getvalue().splitlines()]
    assert rc == 0
    assert [line["t"] for line in lines] == ["snapshot", "tick"]
    assert lines[0]["sessions"] == []


def test_an_unwritable_statedir_degrades_to_ticks_and_says_so(agb, tmp_path,
                                                              set_host, capsys):
    set_host(HOST)
    parent = tmp_path / "ro"
    parent.mkdir()
    target = str(parent / "state")
    os.chmod(str(parent), 0o500)
    out = io.StringIO()
    try:
        rc = agb.feed_loop(target, MAC, out=out, iterations=3,
                           sleep=lambda _s: None)
    finally:
        os.chmod(str(parent), 0o700)

    lines = [json.loads(line) for line in out.getvalue().splitlines()]
    assert rc == 0
    assert [line["t"] for line in lines] == ["snapshot", "tick", "tick"]
    captured = capsys.readouterr()
    assert "agb feed:" in captured.err
    # deduplicated: one line, not one per poll
    assert len([l for l in captured.err.splitlines() if l]) == 1


def test_a_malformed_state_file_is_skipped_not_fatal(agb, sd, feed):
    good = write_session(agb, sd, HOST, "a3f9c1e0")
    agb.write_in_place(agb.state_path(sd, "b1b2b3b4", HOST), "garbage\n")
    agb.rebuild_marker(sd, HOST)

    sessions = feed.poll()[0]["sessions"]
    assert [s["key"] for s in sessions] == [good]


def test_stdout_carries_nothing_but_ndjson(agb, statedir, set_host):
    """The protocol owns stdout; diagnostics go to stderr. A stray print would
    desynchronize the bridge's line reader.

    stdin is held open on purpose: `run_agb`'s `communicate()` closes it, and
    the feed -- correctly -- treats that as the bridge hanging up, so it would
    exit after one poll. So stdin is a raw pipe whose write end this process
    keeps, rather than `subprocess.PIPE` -- which lets `communicate()` drain
    stdout AND stderr concurrently without closing it.

    Draining both is not tidiness. Reading stdout to EOF while stderr is an
    undrained pipe deadlocks on precisely the regression this test exists to
    catch: a feed that wrote more than one pipe buffer to stderr would block
    writing it, this process would block reading stdout, and the suite would
    hang instead of failing.
    """
    set_host(HOST)
    sd = str(statedir)
    write_session(agb, sd, HOST, "a3f9c1e0")
    env = dict(os.environ)
    env.update({"AGB_STATEDIR": sd, "AGB_HOST": HOST})

    read_fd, write_fd = os.pipe()
    try:
        proc = subprocess.Popen(
            conftest.AGB_ARGV + ["feed", MAC, "--iterations", "3",
                                 "--poll-interval", "0.01"],
            stdin=read_fd, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, env=env)
        os.close(read_fd)
        read_fd = None
        try:
            out, err = conftest.communicate(proc)
            assert proc.returncode == 0, err
            assert err == b""
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            for stream in (proc.stdout, proc.stderr):
                if stream is not None and not stream.closed:
                    stream.close()
    finally:
        if read_fd is not None:
            os.close(read_fd)
        os.close(write_fd)

    lines = out.decode().splitlines()
    assert len(lines) == 3
    assert [json.loads(line)["t"] for line in lines] == [
        "snapshot", "tick", "tick"]


def test_a_closed_stdin_ends_the_feed_before_its_iterations(agb, statedir,
                                                            set_host, run_agb):
    """The other half of the same fact, asserted rather than left implicit: an
    orphaned feed would keep touching `bridge/<mac-id>.beat` and make
    `bridge:UP` a lie."""
    set_host(HOST)
    sd = str(statedir)
    rc, out, err = run_agb(["feed", MAC, "--iterations", "50"],
                           env={"AGB_STATEDIR": sd, "AGB_HOST": HOST})
    assert rc == 0
    assert len(out.decode().splitlines()) == 1


# ---------------------------------------------------------------------------
# structural guards
# ---------------------------------------------------------------------------

def test_no_inotify_or_pyinotify_anywhere(all_trees):
    """Constraint #4. `inotify` is delivered by the local kernel from local VFS
    events, so an NFS write from machine #3 produces no event on box #2 -- a
    watch-based feed would be silent exactly for the machine that is hardest to
    reach."""
    imports = conftest.all_imports(*all_trees)
    assert not [name for name in imports if "inotify" in name.lower()]


def test_select_is_imported_inside_the_function_that_needs_it(agb_tree,
                                                              all_trees):
    """`feed` is not the hot path, but `agb` is one file: anything at module
    scope is paid by every `hook` invocation too.

    Task 4a gave the bridge a second reader that has to notice silence, and both
    go through `_select_readable` rather than growing a second import site --
    one site is what keeps this guard a single name instead of a growing list
    nobody rereads. Task 4c moved that second reader into `agb_mac`, which is
    exactly when a "just import select here, it is Mac-side anyway" would have
    slipped in, so the search now spans both files.
    """
    for tree in all_trees:
        assert "select" not in conftest.toplevel_imports(tree)
    assert "select" not in conftest.toplevel_imports(agb_tree)
    holders = set()
    for name, node in conftest.functions(*all_trees).items():
        for child in ast.walk(node):
            if isinstance(child, ast.Import):
                for alias in child.names:
                    if alias.name == "select":
                        holders.add(name)
    assert holders == set(["_select_readable"])


def test_the_poll_never_lists_a_foreign_session_directory_structurally(agb_tree):
    """The runtime guard above proves the current code path; this one stops a
    convenient `os.listdir(session_dir(host))` from being added to the poll.

    Narrowed by Task 3b, and narrowed precisely: the poll now reaps its own
    host's dead entries, and a reap rebuilds the marker from
    `readdir(sessions/<own_host>/)`, which constraint #5's scope note makes
    authoritative. So one listing path is legitimate -- and it is legitimate
    *only* because it is gated on `_require_own_host`. That gate is what this
    test pins; without it the exemption would be an unconditional licence to
    list any host's directory.
    """
    funcs = conftest.functions(agb_tree)
    reachable = conftest.reachable_from(funcs, "feed_poll")

    assert "read_state_entry" in reachable and "list_marker_hosts" in reachable
    # The one legitimate chain, and nothing else may join it:
    #   reap_entry -> rebuild_marker -> list_session_keys -> os.listdir
    for name in reachable:
        made = conftest.calls(funcs[name])
        bare = [attr for base, attr in made]
        if ("os", "listdir") in made:
            assert name in ("list_marker_hosts", "list_session_keys"), name
        if "list_session_keys" in bare:
            assert name == "rebuild_marker", name
        if "rebuild_marker" in bare:
            assert name == "reap_entry", name
            assert (None, "_require_own_host") in made, name
    assert (None, "_require_own_host") in conftest.calls(funcs["reap_entry"])


def test_the_feed_reads_beats_with_fstat_never_os_stat(agb_tree):
    """Constraint #6: `os.stat` is served from the attribute cache, so a
    cross-host reader can be handed the old inode's mtime silently -- and the
    beat *is* the mtime."""
    funcs = conftest.functions(agb_tree)
    for name in ("read_state_entry", "read_marker_keys", "feed_poll",
                 "touch_bridge_beat"):
        made = conftest.calls(funcs[name])
        assert ("os", "stat") not in made, name
        assert ("os", "scandir") not in made, name


def test_the_beat_is_never_stamped_with_an_explicit_time(agb_tree):
    """Constraint #12: `os.utime(path, None)` and `write_in_place` let the NFS
    server stamp the file. An explicit time would put every age back into the
    writer's clock domain."""
    funcs = conftest.functions(agb_tree)
    node = funcs["touch_bridge_beat"]
    for child in ast.walk(node):
        if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute):
            assert child.func.attr != "utime"
    assert (None, "write_in_place") in conftest.calls(node)


def test_the_feed_does_not_sleep_when_iterations_are_exhausted(agb, sd):
    """Otherwise `--iterations 1` would still pay a full poll interval, and
    every test using it would carry a real sleep."""
    slept = []
    rc = agb.feed_loop(sd, MAC, out=io.StringIO(), iterations=2,
                       sleep=lambda seconds: slept.append(seconds))
    assert rc == 0
    assert slept == [agb.FEED_POLL_INTERVAL]


def test_the_poll_interval_is_what_gets_slept(agb, sd):
    slept = []
    agb.feed_loop(sd, MAC, out=io.StringIO(), iterations=3, poll_interval=0.25,
                  sleep=lambda seconds: slept.append(seconds))
    assert slept == [0.25, 0.25]


# ---------------------------------------------------------------------------
# end to end: a real `agb feed` process into a real `agb bridge --from-stdin`
# ---------------------------------------------------------------------------
#
# ⚠️ These exist because the unit tests LIED. `complete` had a test asserting it
# recovers and the bridge had a test asserting the next complete snapshot still
# removes -- both green, both hand-feeding their own side, and between them a
# live row that could never be reclaimed on any real connection. Nothing
# consumed the recovered flag: the feed emitted one snapshot per connection, so
# the second snapshot the bridge's test relied on did not exist in production.
#
# So this pair drives the two real binaries through a real pipe. It is the only
# place in the suite where the wire contract is checked by the processes that
# implement it rather than by two tests that agree with each other.

def _feed_proc(sd, extra=()):
    """Start `agb feed` with a stdin that stays OPEN.

    `feed_stdin_eof` ends the loop when the bridge goes, and under pytest fd 0
    is already at EOF -- inheriting it would end the feed after poll 1 and make
    a multi-poll test unable to fail. The read end of a pipe nobody writes to is
    what an ssh gives it. It is passed as an fd rather than as `PIPE` so that
    `conftest.communicate` has no stdin to close: only `--iterations` should
    decide when the feed stops.
    """
    env = dict(os.environ)
    env["AGB_STATEDIR"] = sd
    read_fd, write_fd = os.pipe()
    try:
        proc = subprocess.Popen(
            conftest.AGB_ARGV + ["feed", MAC, "--poll-interval", "0.05"]
            + list(extra),
            stdin=read_fd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=env)
    finally:
        os.close(read_fd)
    proc.hold = write_fd
    return proc


def _bridge_proc(rows_path):
    return subprocess.Popen(
        conftest.AGB_ARGV + ["bridge", "--from-stdin", "--rows", rows_path,
                             "--watchdog", "20"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=os.environ.copy())


def _pump(feed_proc, bridge_proc, heal, lines=6):
    """Forward `lines` feed lines into the bridge, running `heal` after the
    first one.

    Event-driven rather than timed -- the heal happens once the first line has
    been seen, so this is not a race against a poll interval -- and bounded by
    `select`, so a feed that stops answering fails the test instead of hanging
    it (see `test_no_test_blocks_on_a_subprocess_without_a_bound`).
    """
    seen = []
    while len(seen) < lines:
        ready, _w, _x = select.select([feed_proc.stdout], [], [],
                                      conftest.SUBPROCESS_TIMEOUT)
        assert ready, "the feed stopped emitting after %d lines" % (len(seen),)
        raw = feed_proc.stdout.readline()
        if not raw:
            break
        seen.append(json.loads(raw.decode()))
        bridge_proc.stdin.write(raw)
        bridge_proc.stdin.flush()
        if len(seen) == 1 and heal is not None:
            heal()
    return seen


@pytest.fixture
def e2e_rows(mac, tmp_path):
    """A rows file as a previous bridge left it: one live key, one orphan."""
    path = tmp_path / "rows"

    def build(live_key, orphan_key):
        rows = mac.load_rows(str(path))
        rows.bind(live_key, "ROW-A", "build")
        rows.bind(orphan_key, "ROW-B", "finished")
        rows.save()
        return str(path)

    return build


def test_an_incomplete_first_poll_still_reclaims_the_row_end_to_end(
        agb, sd, mac, e2e_rows, agtermctl):
    """A live agent, an orphan row whose agent finished while the bridge was
    down, and a marker that cannot be read on poll 1.

    The orphan is in no marker and in no `fs.entries`, so the feed never probes
    it: only a snapshot can reclaim it. Before `owed`, the connection emitted
    its one snapshot at `complete: false` and the row stayed `bound` for ever --
    unreachable by `close-done` (it only touches unbound entries), by `prune`
    (there is no statedir entry) and by the feed itself.
    """
    key = write_session(agb, sd, HOST, "a3f9c1e0")
    rows_path = e2e_rows(key, "deadbeef")
    marker = agb.marker_path(sd, HOST)
    os.chmod(marker, 0o000)

    feed = _feed_proc(sd)
    bridge = _bridge_proc(rows_path)
    try:
        seen = _pump(feed, bridge, lambda: os.chmod(marker, 0o600))
    finally:
        feed.terminate()
        os.close(feed.hold)
        conftest.communicate(feed)
        out, _err = conftest.communicate(bridge)

    snapshots = [line for line in seen if line["t"] == "snapshot"]
    assert [s["complete"] for s in snapshots] == [False, True]
    assert [s["key"] for s in snapshots[1]["sessions"]] == [key]
    assert "remove deadbeef" in out.decode()
    after = mac.load_rows(rows_path)
    assert after.done_entries() == [("deadbeef", "ROW-B")]
    assert after.bound_keys() == [key]
    renamed = [call[2] for call in agtermctl.calls()
               if call[1] == "rename" and "ROW-B" in call]
    assert renamed and renamed[-1].startswith("[done] ")


def test_a_feed_with_no_marker_source_reclaims_nothing_end_to_end(
        agb, sd, mac, e2e_rows, agtermctl, tmp_path):
    """The mirror image, and the reason `owed` must not become a licence to
    remove: a statedir with no marker in it is an absence of DATA, not a farm
    with no sessions. Both rows survive and nothing is renamed `[done]`."""
    rows_path = e2e_rows("a3f9c1e0", "deadbeef")
    drifted = str(tmp_path / "elsewhere")          # the statedir they disagree on

    feed = _feed_proc(drifted, ["--iterations", "4"])
    bridge = _bridge_proc(rows_path)
    try:
        feed_out, _err = conftest.communicate(feed)
        out, _berr = conftest.communicate(bridge, feed_out)
    finally:
        os.close(feed.hold)

    lines = [json.loads(raw) for raw in feed_out.decode().splitlines() if raw]
    assert [line["t"] for line in lines] == ["snapshot", "tick", "tick", "tick"]
    assert lines[0]["complete"] is False
    assert "remove" not in out.decode()
    assert mac.load_rows(rows_path).done_entries() == []
    assert [call for call in agtermctl.calls()
            if call[1] == "rename" and call[2].startswith("[done]")] == []
