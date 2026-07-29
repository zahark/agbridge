"""Task 3b -- feed-side liveness proving.

This is the most dangerous code in the tool: it is the only thing that deletes a
session, and everything downstream of it (the bridge's row unbinding, the
`[done]` marker) is driven by that deletion. So the tests here are weighted
towards what must **not** happen -- a foreign host touched, an unresolvable pid
unlinked, a torn read adjudicated -- rather than towards the happy path, which
is one assertion.

Every "dead" pid in here is forked, exited and reaped, so it is dead by
construction rather than by assumption; every "live" one is this process.
"""

import ast
import errno
import io
import json
import os

import pytest

import conftest


HOST = "box2"
FOREIGN = "box3"          # "machine #3": nothing here may ever touch it
MAC = "mac-abc123"

LIVE_PID, LIVE_START = conftest.live_agent()


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def sd(agb, statedir, set_host):
    set_host(HOST)
    return str(statedir)


def write_session(agb, sd, host, key, pid, starttime, state="active", seq=1,
                  rebuild=True):
    """One session, written the way a hook writes it: `.json`, `.state`, marker."""
    agb.ensure_session_dir(sd, host)
    rec = {
        "v": 1, "key": key, "label": "lbl-" + key, "host": host, "pid": pid,
        "starttime": starttime, "tmux": "sess", "pane": "%24",
        "cwd": "/shared/x", "state": state, "seq": seq, "updated": 1.0,
    }
    agb.atomic_write(agb.record_path(sd, key, host),
                     json.dumps(rec, sort_keys=True) + "\n")
    agb.write_in_place(agb.state_path(sd, key, host),
                       agb.format_state(state, host, pid, starttime, seq))
    if rebuild:
        agb.rebuild_marker(sd, host)
    return key


def age_file(path, seconds):
    st = os.stat(path)
    os.utime(path, (st.st_atime, st.st_mtime - seconds))
    return st.st_mtime - seconds


def exists(path):
    return os.path.exists(path)


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


def kinds(lines):
    return [line["t"] for line in lines]


# ---------------------------------------------------------------------------
# liveness -- three-valued, because "not provably alive" is not "dead"
# ---------------------------------------------------------------------------

def test_a_reaped_pid_is_proven_dead(agb):
    pid, starttime = conftest.dead_agent()
    assert agb.liveness(pid, starttime) == agb.LIVENESS_DEAD
    assert agb.proof_of_death(pid, starttime) is True
    assert agb.proof_of_life(pid, starttime) is False


def test_this_process_is_proven_alive(agb):
    assert agb.liveness(LIVE_PID, LIVE_START) == agb.LIVENESS_ALIVE
    assert agb.proof_of_death(LIVE_PID, LIVE_START) is False
    assert agb.proof_of_life(LIVE_PID, LIVE_START) is True


def test_a_reused_pid_is_proven_dead_by_its_starttime(agb):
    """The pid exists, so `kill` says nothing -- but it started at a different
    moment, so it is not the process the entry was written for. Without this,
    one recycled pid keeps a row bound to a dead agent forever."""
    assert agb.liveness(LIVE_PID, LIVE_START + 1) == agb.LIVENESS_DEAD


@pytest.mark.parametrize("pid,starttime", [
    (None, None),                    # the fail-safe entry: no pid recorded
    (None, 12345),
    (LIVE_PID, None),                # alive, but nothing rules out pid reuse
    (0, 1),                          # kill(0, …) addresses the process GROUP
    (-1, 1),                         # and a negative pid addresses one by id
    (1, 1),                          # init is never an agent
    ("nonsense", 1),
], ids=[
    # Spelled out because `LIVE_PID` is `os.getpid()`: left to pytest, one of
    # these node ids would carry this run's pid and change on every run. A
    # failing id copied out of a log could never be re-run, `--lf` would always
    # be stale, and xdist workers (different pids) would collect different id
    # sets and abort with "Different tests were collected".
    "no-pid-no-starttime", "no-pid-with-starttime", "live-pid-no-starttime",
    "pid-zero-is-the-process-group", "negative-pid-is-a-group-by-id",
    "pid-one-is-init", "pid-is-not-a-number",
])
def test_absence_of_evidence_is_never_death(agb, pid, starttime):
    """Constraint #11. Each of these is a real shape the data takes, and every
    one of them must land in the middle answer rather than in `dead`."""
    assert agb.liveness(pid, starttime) == agb.LIVENESS_UNKNOWN
    assert agb.proof_of_death(pid, starttime) is False
    assert agb.proof_of_life(pid, starttime) is False


def test_a_pid_owned_by_another_user_is_not_death_by_itself(agb, monkeypatch):
    """EPERM proves the pid exists, not that it is ours. Only a positively
    disagreeing starttime makes it death."""
    def eperm(_pid, _sig):
        raise OSError(errno.EPERM, "Operation not permitted")

    monkeypatch.setattr(os, "kill", eperm)
    assert agb.liveness(LIVE_PID, LIVE_START) == agb.LIVENESS_UNKNOWN
    assert agb.liveness(LIVE_PID, None) == agb.LIVENESS_UNKNOWN
    assert agb.liveness(LIVE_PID, LIVE_START + 1) == agb.LIVENESS_DEAD


def test_an_unexpected_kill_error_is_not_death(agb, monkeypatch):
    def eintr(_pid, _sig):
        raise OSError(errno.EINTR, "Interrupted system call")

    monkeypatch.setattr(os, "kill", eintr)
    assert agb.liveness(LIVE_PID, LIVE_START) == agb.LIVENESS_UNKNOWN


def test_an_unreadable_proc_is_not_death(agb, monkeypatch, tmp_path):
    """`kill` succeeded, so a process holds the pid; `/proc` then failed, so the
    reuse guard cannot be evaluated. That is the middle answer, not death."""
    monkeypatch.setattr(agb, "PROC", str(tmp_path / "no-proc"))
    assert agb.liveness(LIVE_PID, LIVE_START) == agb.LIVENESS_UNKNOWN


# ---------------------------------------------------------------------------
# the shared helper: own host only, enforced at runtime
# ---------------------------------------------------------------------------

def test_reap_entry_refuses_a_foreign_host(agb, sd):
    """The precondition is executable. `prune` (Task 6b) is the foreign-host
    path; a rebuild here would use `readdir`, which constraint #5 makes
    authoritative only locally."""
    key = write_session(agb, sd, FOREIGN, "b1b2b3b4", *conftest.dead_agent())
    with pytest.raises(agb.AgbError):
        agb.reap_entry(sd, FOREIGN, key)
    assert exists(agb.state_path(sd, key, FOREIGN))


def test_sweep_entry_refuses_a_foreign_host(agb, sd):
    pid, starttime = conftest.dead_agent()
    key = write_session(agb, sd, FOREIGN, "b1b2b3b4", pid, starttime)
    with pytest.raises(agb.AgbError):
        agb.sweep_entry(sd, FOREIGN, key, pid, starttime)
    assert exists(agb.state_path(sd, key, FOREIGN))


def test_the_refusal_names_the_foreign_host_and_the_local_one(agb, sd):
    with pytest.raises(agb.AgbError) as excinfo:
        agb.reap_entry(sd, FOREIGN, "b1b2b3b4")
    message = str(excinfo.value)
    assert FOREIGN in message and HOST in message and "prune" in message


def test_sweep_entry_returns_the_three_liveness_answers(agb, sd):
    dead_pid, dead_start = conftest.dead_agent()
    write_session(agb, sd, HOST, "a3f9c1e0", LIVE_PID, LIVE_START)
    write_session(agb, sd, HOST, "b1b2b3b4", dead_pid, dead_start)
    write_session(agb, sd, HOST, "c0ffee11", None, None)

    assert agb.sweep_entry(sd, HOST, "a3f9c1e0", LIVE_PID,
                           LIVE_START) == agb.SWEEP_ALIVE
    assert agb.sweep_entry(sd, HOST, "b1b2b3b4", dead_pid,
                           dead_start) == agb.SWEEP_REAPED
    assert agb.sweep_entry(sd, HOST, "c0ffee11", None,
                           None) == agb.SWEEP_SKIPPED


def test_a_reap_removes_the_state_the_record_and_the_marker_entry(agb, sd):
    dead_pid, dead_start = conftest.dead_agent()
    live = write_session(agb, sd, HOST, "a3f9c1e0", LIVE_PID, LIVE_START)
    doomed = write_session(agb, sd, HOST, "b1b2b3b4", dead_pid, dead_start)

    assert agb.sweep_entry(sd, HOST, doomed, dead_pid,
                           dead_start) == agb.SWEEP_REAPED

    assert not exists(agb.state_path(sd, doomed, HOST))
    assert not exists(agb.record_path(sd, doomed, HOST))
    assert exists(agb.state_path(sd, live, HOST))
    with open(agb.marker_path(sd, HOST), "rb") as handle:
        assert agb.parse_marker(handle.read()) == [live]


def test_a_reap_leaves_a_breadcrumb(agb, sd):
    """"Why did this row disappear?" has to be answerable after the fact -- an
    unexplained vanishing row is the same silent failure as an unexplained
    missing one."""
    dead_pid, dead_start = conftest.dead_agent()
    key = write_session(agb, sd, HOST, "a3f9c1e0", dead_pid, dead_start)
    agb.sweep_entry(sd, HOST, key, dead_pid, dead_start)

    with open(agb.err_log_path(sd, key, HOST)) as handle:
        text = handle.read()
    assert "reaped" in text and str(dead_pid) in text


def test_a_second_reap_of_the_same_entry_is_harmless(agb, sd):
    """Two sweepers (the feed and a hook) race by design -- there is no lock."""
    dead_pid, dead_start = conftest.dead_agent()
    key = write_session(agb, sd, HOST, "a3f9c1e0", dead_pid, dead_start)

    assert agb.reap_entry(sd, HOST, key) is True
    assert agb.reap_entry(sd, HOST, key) is False
    with open(agb.marker_path(sd, HOST), "rb") as handle:
        assert agb.parse_marker(handle.read()) == []


# ---------------------------------------------------------------------------
# the feed loop: a dead own-host agent
# ---------------------------------------------------------------------------

def test_a_dead_own_host_pid_is_unlinked_and_produces_exactly_one_remove(
        agb, sd, feed):
    """The flagship: the feed is the only agb process running continuously on
    box #2, so an agent that died while blocked -- firing no further hooks -- is
    provable here and nowhere else."""
    pid, starttime = conftest.dead_agent()
    key = write_session(agb, sd, HOST, "a3f9c1e0", LIVE_PID, LIVE_START)
    assert len(feed.poll()[0]["sessions"]) == 1

    # the agent dies: rewrite `.state` with the dead identity, as a real
    # transition would have left it
    write_session(agb, sd, HOST, key, pid, starttime, seq=2)

    lines = feed.poll()
    assert kinds(lines) == ["remove"]
    assert lines[0]["key"] == key
    assert not exists(agb.state_path(sd, key, HOST))
    assert not exists(agb.record_path(sd, key, HOST))

    # exactly one: the marker was rebuilt, so nothing re-discovers it
    assert kinds(feed.poll()) == ["tick"]
    assert kinds(feed.poll()) == ["tick"]


def test_a_dead_entry_never_reaches_a_snapshot(agb, sd, feed):
    """It is reaped during the first poll, so the bridge never binds a row to an
    agent that was already gone when the feed started."""
    pid, starttime = conftest.dead_agent()
    write_session(agb, sd, HOST, "a3f9c1e0", pid, starttime)
    live = write_session(agb, sd, HOST, "b1b2b3b4", LIVE_PID, LIVE_START)

    lines = feed.poll()
    assert kinds(lines) == ["snapshot"]
    assert [s["key"] for s in lines[0]["sessions"]] == [live]
    assert not exists(agb.state_path(sd, "a3f9c1e0", HOST))


def test_a_reused_agent_pid_is_reaped_by_the_feed(agb, sd, feed):
    """Same pid, different starttime: the row belonged to the process that used
    to hold it."""
    key = write_session(agb, sd, HOST, "a3f9c1e0", LIVE_PID, LIVE_START)
    feed.poll()

    write_session(agb, sd, HOST, key, LIVE_PID, LIVE_START + 1, seq=2)
    assert kinds(feed.poll()) == ["remove"]
    assert not exists(agb.state_path(sd, key, HOST))


def test_only_the_dead_entry_is_reaped(agb, sd, feed):
    pid, starttime = conftest.dead_agent()
    live = write_session(agb, sd, HOST, "a3f9c1e0", LIVE_PID, LIVE_START)
    doomed = write_session(agb, sd, HOST, "b1b2b3b4", LIVE_PID, LIVE_START)
    assert len(feed.poll()[0]["sessions"]) == 2

    write_session(agb, sd, HOST, doomed, pid, starttime, seq=2)
    lines = feed.poll()
    assert [line.get("key") for line in lines if line["t"] == "remove"] == [
        doomed]
    assert exists(agb.state_path(sd, live, HOST))
    assert list(feed.state.entries) == [live]


def test_the_reap_lists_only_its_own_session_directory(agb, sd, feed,
                                                        monkeypatch):
    """The runtime half of the structural guard: a foreign `readdir` can be
    served from a 60 s old cache, so even the rebuild after a reap must stay
    local."""
    pid, starttime = conftest.dead_agent()
    write_session(agb, sd, HOST, "a3f9c1e0", pid, starttime)
    write_session(agb, sd, FOREIGN, "b1b2b3b4", LIVE_PID, LIVE_START)

    listed = []
    real = os.listdir

    def spy(path=".", *args, **kwargs):
        listed.append(str(path))
        return real(path, *args, **kwargs)

    monkeypatch.setattr(os, "listdir", spy)
    feed.poll()

    assert set(listed) == set([agb.gen_dir(sd), agb.session_dir(sd, HOST)])


# ---------------------------------------------------------------------------
# what must NOT be reaped
# ---------------------------------------------------------------------------

def test_a_foreign_host_entry_with_a_dead_pid_is_never_touched(agb, sd, feed,
                                                                capsys):
    """Machine #3's pids mean nothing in this pid namespace, and its directory
    cannot be re-listed authoritatively. Its terminal path is `agb prune`.

    The last two assertions are the ones with teeth. `sweep_entry` would refuse
    a foreign host anyway -- that is the point of the precondition -- so a poll
    that *tried* and was refused would leave every file intact and pass a
    survival-only test, while filling stderr and the breadcrumb log once per
    poll forever. Foreign entries must not be adjudicated at all.
    """
    pid, starttime = conftest.dead_agent()
    key = write_session(agb, sd, FOREIGN, "b1b2b3b4", pid, starttime)

    lines = feed.poll(iterations=3)
    assert [s["key"] for s in lines[0]["sessions"]] == [key]
    assert exists(agb.state_path(sd, key, FOREIGN))
    assert exists(agb.record_path(sd, key, FOREIGN))
    assert [line for line in lines if line["t"] == "remove"] == []
    assert capsys.readouterr().err == ""
    assert not exists(agb.err_log_path(sd, key, FOREIGN))


def test_an_entry_with_no_pid_is_never_unlinked(agb, sd, feed):
    """The fail-safe entry: a hook that could not identify its agent stores `-`
    (Task 2a), and nothing may ever prove that entry dead."""
    key = write_session(agb, sd, HOST, "a3f9c1e0", None, None)

    lines = feed.poll(iterations=3)
    assert [s["key"] for s in lines[0]["sessions"]] == [key]
    assert lines[0]["sessions"][0]["pid"] is None
    assert exists(agb.state_path(sd, key, HOST))


def test_an_entry_whose_state_is_torn_is_never_adjudicated(agb, sd, feed):
    """The two fail-safes compose: the pid on disk is provably dead, but the
    read that would have told us so failed validation, so there is no
    information this poll -- and no information never unlinks."""
    pid, starttime = conftest.dead_agent()
    key = write_session(agb, sd, HOST, "a3f9c1e0", LIVE_PID, LIVE_START)
    feed.poll()

    with open(agb.state_path(sd, key, HOST), "wb") as handle:
        handle.write(b"")                    # a peer's in-place O_TRUNC window

    lines = feed.poll()
    assert kinds(lines) == ["tick"]
    assert exists(agb.state_path(sd, key, HOST))
    assert list(feed.state.entries) == [key]
    del pid, starttime


def test_nothing_is_unlinked_when_liveness_cannot_decide(agb, sd, feed,
                                                          monkeypatch):
    """The mutation guard for the predicate itself: with `liveness` pinned to the
    middle answer, a provably dead pid must still survive every poll."""
    pid, starttime = conftest.dead_agent()
    key = write_session(agb, sd, HOST, "a3f9c1e0", pid, starttime)
    monkeypatch.setattr(agb, "liveness",
                        lambda _pid, _start: agb.LIVENESS_UNKNOWN)

    lines = feed.poll(iterations=3)
    assert [s["key"] for s in lines[0]["sessions"]] == [key]
    assert exists(agb.state_path(sd, key, HOST))


def test_a_live_blocked_session_with_an_old_beat_is_still_shown(agb, sd, feed):
    """Design amendment 1: age is never converted into a state, and never into a
    removal. A `blocked` agent waiting on the user for half an hour is the exact
    case aging used to destroy."""
    key = write_session(agb, sd, HOST, "a3f9c1e0", LIVE_PID, LIVE_START,
                        state="blocked")
    age_file(agb.state_path(sd, key, HOST), 1800)

    session = feed.poll()[0]["sessions"][0]
    assert session["key"] == key and session["state"] == "blocked"
    assert exists(agb.state_path(sd, key, HOST))


# ---------------------------------------------------------------------------
# the beat: refreshed for live own-host entries, throttled
# ---------------------------------------------------------------------------

def test_a_live_own_host_entrys_beat_is_refreshed(agb, sd, feed):
    """This is what makes box-#2 liveness independent of hooks firing: a
    20-minute build fires none, and the beat is what the bridge puts in the row
    title."""
    key = write_session(agb, sd, HOST, "a3f9c1e0", LIVE_PID, LIVE_START)
    path = agb.state_path(sd, key, HOST)
    stale = age_file(path, 600)
    before = os.stat(path)

    feed.poll()

    after = os.stat(path)
    assert after.st_mtime > stale
    assert after.st_ino == before.st_ino          # in place, never renamed
    assert after.st_size == before.st_size        # a touch, not a rewrite


def test_the_beat_refresh_is_throttled(agb, sd, feed):
    """The beat says "alive", not "a tool call happened": refreshing it every
    poll would be a write per session per two seconds, on NFS, forever."""
    key = write_session(agb, sd, HOST, "a3f9c1e0", LIVE_PID, LIVE_START)
    path = agb.state_path(sd, key, HOST)
    feed.poll()
    first = os.stat(path).st_mtime

    feed.poll(iterations=3)
    assert os.stat(path).st_mtime == first


def test_a_foreign_hosts_beat_is_never_refreshed(agb, sd, feed):
    """Only the owning host may assert that its agents are alive."""
    key = write_session(agb, sd, FOREIGN, "b1b2b3b4", LIVE_PID, LIVE_START)
    path = agb.state_path(sd, key, FOREIGN)
    stale = age_file(path, 600)

    feed.poll(iterations=2)
    assert os.stat(path).st_mtime == stale


def test_an_unresolvable_entrys_beat_is_never_refreshed(agb, sd, feed):
    """A beat asserts "alive as of now" to every reader. An entry with no pid
    cannot support that claim, so `proof_of_life` -- not `not proof_of_death` --
    is what gates the refresh."""
    key = write_session(agb, sd, HOST, "a3f9c1e0", None, None)
    path = agb.state_path(sd, key, HOST)
    stale = age_file(path, 600)

    feed.poll(iterations=2)
    assert os.stat(path).st_mtime == stale


def test_the_refreshed_beat_reaches_the_wire_on_the_next_poll(agb, sd, feed):
    key = write_session(agb, sd, HOST, "a3f9c1e0", LIVE_PID, LIVE_START)
    age_file(agb.state_path(sd, key, HOST), 600)
    first = feed.poll()[0]["sessions"][0]["beat"]

    lines = feed.poll()
    assert kinds(lines) == ["upsert"]
    assert lines[0]["session"]["beat"] > first
    assert lines[0]["session"]["seq"] == 1        # no transition happened


def test_the_beat_is_refreshed_without_rewriting_the_record(agb, sd, feed):
    key = write_session(agb, sd, HOST, "a3f9c1e0", LIVE_PID, LIVE_START)
    record = agb.record_path(sd, key, HOST)
    age_file(agb.state_path(sd, key, HOST), 600)
    before = os.stat(record)

    feed.poll()
    after = os.stat(record)
    assert (after.st_mtime, after.st_ino) == (before.st_mtime, before.st_ino)


# ---------------------------------------------------------------------------
# degradation -- a failed reap must not cost the rest of the poll
# ---------------------------------------------------------------------------

def test_a_failing_reap_is_contained_to_its_own_entry(agb, sd, feed, capsys,
                                                       monkeypatch):
    """Losing every other host's updates because one unlink hit EACCES would be
    a larger lie than the row it failed to remove.

    `monkeypatch`, not a hand-rolled try/finally: `agb.os` IS the stdlib `os`
    module, so this breaks `os.unlink` process-wide -- for pytest's own
    machinery too -- and a restore that an early return or a `pytest.skip` could
    step over would leave it broken for every test after this one. The `agb`,
    `mac` and `ops` fixtures are session-scoped, so such a leak is permanent.
    """
    pid, starttime = conftest.dead_agent()
    write_session(agb, sd, HOST, "a3f9c1e0", pid, starttime)
    other = write_session(agb, sd, FOREIGN, "b1b2b3b4", LIVE_PID, LIVE_START)

    def boom(*_args, **_kwargs):
        raise OSError(errno.EACCES, "Permission denied")

    monkeypatch.setattr(agb.os, "unlink", boom)
    lines = feed.poll()
    monkeypatch.undo()

    assert kinds(lines) == ["snapshot"]
    assert [s["key"] for s in lines[0]["sessions"]] == ["a3f9c1e0", other]
    assert "agb feed:" in capsys.readouterr().err
    with open(agb.err_log_path(sd, "a3f9c1e0", HOST)) as handle:
        assert "cannot adjudicate" in handle.read()


def test_a_reap_that_cannot_rebuild_the_marker_is_reported(agb, sd, feed,
                                                            capsys,
                                                            monkeypatch):
    pid, starttime = conftest.dead_agent()
    write_session(agb, sd, HOST, "a3f9c1e0", pid, starttime)

    def boom(*_args, **_kwargs):
        raise OSError(errno.EACCES, "Permission denied")

    # `agb` is a session-scoped fixture: an attribute left patched here is
    # patched for the rest of the run.
    monkeypatch.setattr(agb, "rebuild_marker", boom)
    lines = feed.poll()
    monkeypatch.undo()

    assert kinds(lines) == ["snapshot"]
    assert "cannot adjudicate" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# structural guards -- one implementation, one authority
# ---------------------------------------------------------------------------

def bare_calls(node):
    return [attr for base, attr in conftest.calls(node) if base is None]


def test_only_one_function_unlinks_a_session(all_trees):
    """Task 3b's first bullet, made enforceable rather than aspirational: Task 5
    reuses `reap_entry`, and if it grows its own `os.unlink` of a `.state` this
    fails. Two implementations of the delete path is how the delete-everything
    bug gets in.

    Every file: `agb_mac` has no unlink authority at all (constraint #11), and
    counting the unlinkers across the set is what keeps that true as code grows
    in the siblings.

    ⚠️ **Amended by Task 5, and deliberately narrowed rather than widened.**
    That task has to reap three things that are *not* sessions -- stale `idx/`
    entries, breadcrumbs whose session is gone, and abandoned `*.tmp.*` files --
    so the set of functions that unlink *something* necessarily grew. The claim
    worth keeping is the one in the test's name, so it is now asserted directly:
    the closed set is still named, and every member of it other than
    `reap_entry` is required not to name a session file at all. A Task-5 sweep
    that grew its own `state_path(...)` unlink fails on the second assertion
    even though the first one would have room for it.

    ⚠️ **Amended by Task 6a, and the amendment closes a hole rather than making
    room.** `doctor`'s atomicity probe removes the file it just wrote, and it
    does so as `agb._unlink_quiet(...)` -- which the *qualified* form of this
    search did not see at all, so the sibling files could have unlinked anything
    they liked and stayed green. The detector now follows the `agb.` qualifier,
    exactly as `conftest.reachable_from` does, and `probe_atomicity` is named
    below like the other temp-cleaners.

    ⚠️ **Amended by Task 6b, which adds the tool's second session unlinker on
    purpose.** `prune_remove` is the terminal path design amendment 4 owes:
    without it a `kill -9` on machine #3 leaves entries nothing ever reclaims.
    The name of this test is still the claim worth keeping, so it is now stated
    as the *two* authorities and what each is gated by -- `reap_entry` by proof
    of death, `prune_remove` by a human, per entry, and by nothing else. A third
    one, or either of them growing a second door, fails here. `prune_remove` is
    also required below never to reach the own-host reap machinery, because its
    entries are on hosts where `_require_own_host` would (correctly) refuse.
    """
    funcs = conftest.functions(*all_trees)
    unlinkers = set()
    for name, node in funcs.items():
        made = conftest.calls(node)
        if (("os", "unlink") in made
                or (None, "_unlink_quiet") in made
                or ("agb", "_unlink_quiet") in made):
            unlinkers.add(name)

    # `_unlink_quiet` is the primitive; `atomic_write`, `link_idx` and
    # `probe_atomicity` clean up their own temps, which are files they created
    # microseconds earlier and which no other process can observe.
    assert unlinkers == set(
        ["_unlink_quiet", "atomic_write", "link_idx", "reap_entry",
         "sweep_idx", "sweep_err_logs", "sweep_debris", "probe_atomicity",
         "prune_remove"])

    # Exactly two functions may unlink a **session**, and they are gated by the
    # two different things a session can be ended by. The rest are named above
    # because they remove the debris around one, and none of them may so much as
    # name the files that *are* one.
    for name in unlinkers - set(["reap_entry", "prune_remove", "_unlink_quiet"]):
        made = set(attr for _base, attr in conftest.calls(funcs[name]))
        assert "state_path" not in made, name
        assert "record_path" not in made, name

    # `reap_entry` is gated by proof of death (`sweep_entry` calls it under
    # `proof_of_death`, and it re-checks the host); `prune_remove` is gated by a
    # human and must never borrow the own-host machinery, whose precondition its
    # entries would fail by construction.
    pruner = set(attr for _base, attr in conftest.calls(funcs["prune_remove"]))
    for forbidden in ("reap_entry", "sweep_entry", "_require_own_host",
                      "rebuild_marker", "list_session_keys", "proof_of_death"):
        assert forbidden not in pruner


def test_the_debris_reapers_never_call_the_liveness_authority_themselves(
        agb_tree):
    """`idx/`, `err/` and `*.tmp.*` are not sessions, and their removal must not
    look like an adjudication: only `sweep_idx` consults liveness at all, and it
    does so through the shared predicate rather than through a second rule."""
    funcs = conftest.functions(agb_tree)
    for name in ("sweep_err_logs", "sweep_debris"):
        made = set(attr for _base, attr in conftest.calls(funcs[name]))
        assert "liveness" not in made and "proof_of_death" not in made, name
        assert "reap_entry" not in made, name

    made = set(attr for _base, attr in conftest.calls(funcs["sweep_idx"]))
    assert "proof_of_life" in made
    assert "idx_anchor_liveness" in made
    assert "reap_entry" not in made


def test_every_destructive_helper_checks_the_host_at_runtime(agb_tree):
    """"Own host only" is a comment in half the systems that get this wrong.
    Here it is a call, in both entry points, and it is a raise rather than an
    `assert` statement so `python -O` cannot remove it."""
    funcs = conftest.functions(agb_tree)
    for name in ("reap_entry", "sweep_entry"):
        assert (None, "_require_own_host") in conftest.calls(funcs[name]), name

    guard = funcs["_require_own_host"]
    assert not [n for n in ast.walk(guard) if isinstance(n, ast.Assert)]
    assert [n for n in ast.walk(guard) if isinstance(n, ast.Raise)]


def test_the_unlink_is_reachable_only_through_proof_of_death(agb_tree):
    """Every unlink traces to a positive proof. `sweep_entry` is the only caller
    of `reap_entry`, and it calls it only under `liveness`."""
    funcs = conftest.functions(agb_tree)
    callers = set(name for name, node in funcs.items()
                  if "reap_entry" in bare_calls(node))
    assert callers == set(["sweep_entry"])

    made = bare_calls(funcs["sweep_entry"])
    assert "proof_of_death" in made
    # and the beat refresh is gated by the *positive* predicate, not by the
    # negation of the destructive one -- "not provably dead" is not "alive".
    assert "proof_of_life" in made


def test_liveness_is_the_only_place_kill_is_called(all_trees):
    """One predicate, one place. A second `os.kill` in *either* file would be a
    second liveness rule, which is the thing this task exists to prevent."""
    funcs = conftest.functions(*all_trees)
    killers = set(name for name, node in funcs.items()
                  if ("os", "kill") in conftest.calls(node))
    assert killers == set(["liveness"])


def test_the_beat_refresh_never_stamps_an_explicit_time(agb_tree):
    """Constraint #12: `os.utime(path, None)`. An explicit time would put every
    age back into the writer's clock domain, across hosts."""
    funcs = conftest.functions(agb_tree)
    node = funcs["refresh_beat"]
    utimes = [child for child in ast.walk(node)
              if isinstance(child, ast.Call)
              and isinstance(child.func, ast.Attribute)
              and child.func.attr == "utime"]
    # The call has to EXIST, or the loop below is vacuous: delegating the stamp
    # to a `_touch(path)` helper that did `os.utime(path, (t, t))` internally
    # would leave this guard green while putting every cross-host age back into
    # the writer's clock domain. `refresh_beat` stamps the beat itself.
    assert len(utimes) == 1
    for child in utimes:
        assert len(child.args) == 2
        assert isinstance(child.args[1], ast.NameConstant)
        assert child.args[1].value is None


def test_the_sweep_never_opens_a_record(agb_tree):
    """The sweep reads `host`/`pid`/`starttime` from `.state` only -- which is
    what keeps it json-free, and what lets Task 5 run it from the hook."""
    funcs = conftest.functions(agb_tree)
    reachable = set(["sweep_entry"])
    frontier = ["sweep_entry"]
    while frontier:
        node = funcs.get(frontier.pop())
        if node is None:
            continue
        for name in bare_calls(node):
            if name in funcs and name not in reachable:
                reachable.add(name)
                frontier.append(name)
    assert "_json" not in reachable
    assert "read_record" not in reachable
    assert "record_path" in reachable            # unlinked, never opened


def test_the_module_top_still_imports_only_the_cheap_four(agb_tree):
    """Task 3b adds `os.kill` and `os.utime`, both already in `os`. Anything new
    at module scope is paid by every hook invocation on the hot path. `agb` only
    -- `agb_mac` is never loaded by a hook (Task 4c)."""
    assert conftest.toplevel_imports(agb_tree) == set(
        ["errno", "os", "sys", "time"])
