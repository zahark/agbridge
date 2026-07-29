"""Task 5 -- the hook-side sweep.

This is the same delete authority the feed uses, driven from the one place that
runs on **machine #3**: a hook. #3 has no feed (the Mac's single ssh lands on
box #2), so without this pass nothing there could ever prove an agent dead, and
every row and every `idx/` entry it created would be permanent.

The weighting of the tests reflects that. The happy path is two assertions; the
rest are about what must **not** happen -- a foreign host touched, a live
agent's just-written record reaped, an unresolvable pid unlinked, a torn read
adjudicated, an idx entry dropped inside the mint race.

Every "dead" pid here is forked, exited and reaped, so it is dead by
construction; every "live" one is this process. A fabricated pid is
overwhelmingly likely to be dead, which would silently turn a test about
survival into a test about reaping -- the same trap that changed
`tests/test_feed.py` at Task 3b and `tests/test_hook.py` at this one.
"""

import ast
import errno
import io
import json
import os

import pytest

import conftest


HOST = "box2"
FOREIGN = "box3"          # "machine #3" from box #2's point of view
MAC = "mac-abc123"

LIVE_PID, LIVE_START = conftest.live_agent()

REAL_TMUX = "/tmp/tmux-148808/default,1244192,23"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def sd(agb, statedir, set_host):
    set_host(HOST)
    return str(statedir)


@pytest.fixture
def agent(agb, sd, set_tmux, set_agent_pid):
    """A live, tmux-anchored agent whose hooks write into `sd`.

    The tmux **server** pid in `$TMUX` is this process, not the plan's recorded
    1244192: the idx sweep asks whether that pid is alive, and a fabricated one
    would make every test here depend on whether some unrelated process happens
    to hold it today.
    """
    set_tmux("/tmp/tmux-148808/default,%d,23" % (LIVE_PID,), "%24")
    set_agent_pid(LIVE_PID)
    return sd


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


def write_idx(agb, sd, host, spid, tag, key, pid, starttime, age=None):
    """One `idx/<host>-<spid>-<tag>` entry, optionally back-dated."""
    path = os.path.join(sd, "idx", "%s-%d-%s" % (host, spid, tag))
    agb.write_in_place(path, agb.format_idx(key, pid, starttime))
    if age:
        age_file(path, age)
    return path


def age_file(path, seconds):
    st = os.stat(path)
    os.utime(path, (st.st_atime - seconds, st.st_mtime - seconds))
    return os.stat(path).st_mtime


def exists(path):
    return os.path.exists(path)


def err_text(agb, sd, key, host=HOST):
    path = agb.err_log_path(sd, key, host)
    if not os.path.exists(path):
        return ""
    with open(path) as handle:
        return handle.read()


def marker_keys(agb, sd, host=HOST):
    with open(agb.marker_path(sd, host), "rb") as handle:
        return agb.parse_marker(handle.read())


def idx_names(sd):
    return sorted(os.listdir(os.path.join(sd, "idx")))


# ---------------------------------------------------------------------------
# the dangerous case first: another host's entries are never swept
# ---------------------------------------------------------------------------

def test_sweep_host_refuses_a_foreign_host(agb, sd):
    """Constraint #11, at the top of the sweep rather than only per entry: the
    rebuild that follows a reap reads `readdir`, which is authoritative only
    locally, and a foreign pid means nothing in this pid namespace."""
    key = write_session(agb, sd, FOREIGN, "b1b2b3b4", *conftest.dead_agent())
    with pytest.raises(agb.AgbError):
        agb.sweep_host(sd, FOREIGN)
    assert exists(agb.state_path(sd, key, FOREIGN))


def test_the_refusal_names_both_hosts_and_the_operator_path(agb, sd):
    with pytest.raises(agb.AgbError) as excinfo:
        agb.sweep_host(sd, FOREIGN)
    message = str(excinfo.value)
    assert FOREIGN in message and HOST in message and "prune" in message


def test_nothing_of_a_foreign_hosts_survives_by_accident(agb, sd):
    """Every artefact class at once, all of them provably reapable if they were
    ours: a dead-pid session, a stale idx entry, an orphan breadcrumb and an
    abandoned temp. A sweep of *this* host must leave every one of them alone
    -- and must not even try, or the logs fill once per transition forever."""
    dead_pid, dead_start = conftest.dead_agent()
    session = write_session(agb, sd, FOREIGN, "b1b2b3b4", dead_pid, dead_start)
    idx = write_idx(agb, sd, FOREIGN, dead_pid, "p%d" % (dead_start,),
                    "b1b2b3b4", dead_pid, dead_start, age=600)
    err = agb.err_log_path(sd, "c1c2c3c4", FOREIGN)
    agb.breadcrumb(sd, "c1c2c3c4", "old news", FOREIGN)
    age_file(err, agb.SWEEP_ERR_GRACE + 60)
    temp = os.path.join(sd, "gen", "%s.marker.tmp.%s.99.abcd" % (FOREIGN,
                                                                 FOREIGN))
    agb.write_in_place(temp, "junk\n")
    age_file(temp, agb.SWEEP_TEMP_GRACE + 60)

    result = agb.sweep_host(sd, HOST)

    assert exists(agb.state_path(sd, session, FOREIGN))
    assert exists(agb.record_path(sd, session, FOREIGN))
    assert exists(idx) and exists(err) and exists(temp)
    assert result == {"reaped": [], "idx": [], "err": [], "temps": []}
    # ...and no breadcrumb was written about any of it: a sweep that *tried* and
    # was refused leaves every file intact too, and would pass a survival-only
    # test while filling `err/` once per transition.
    assert err_text(agb, sd, "b1b2b3b4") == ""
    assert err_text(agb, sd, None) == ""


def test_the_sweep_lists_only_the_directories_this_host_owns(agb, sd,
                                                             monkeypatch):
    write_session(agb, sd, FOREIGN, "b1b2b3b4", *conftest.dead_agent())
    write_session(agb, sd, HOST, "a3f9c1e0", LIVE_PID, LIVE_START)

    listed = []
    real = os.listdir

    def spy(path=".", *args, **kwargs):
        if str(path).startswith(sd):
            listed.append(str(path))
        return real(path, *args, **kwargs)

    monkeypatch.setattr(os, "listdir", spy)
    agb.sweep_host(sd, HOST)

    assert agb.session_dir(sd, FOREIGN) not in listed
    assert set(listed) == set([agb.session_dir(sd, HOST), agb.gen_dir(sd),
                               os.path.join(sd, "idx"),
                               os.path.join(sd, "err")])


# ---------------------------------------------------------------------------
# the revision-1 regression: a live agent's own record survives its own hook
# ---------------------------------------------------------------------------

def test_a_just_written_record_survives_an_immediate_sweep(agb, agent):
    """The delete-everything bug in one test, moved here from Task 2b because
    that task had no sweep to run it against.

    Revision 1 stored the hook's *parent* pid -- the transient `sh -c` Claude
    spawns hooks through, dead before the sweep looks at it -- so the very hook
    that recorded a state proved it dead microseconds later.
    """
    sd = agent
    assert agb.cmd_hook(["active"]) == 0

    keys = agb.list_session_keys(sd, HOST)
    assert len(keys) == 1
    key = keys[0]
    assert exists(agb.state_path(sd, key, HOST))
    assert exists(agb.record_path(sd, key, HOST))
    assert marker_keys(agb, sd) == [key]
    assert len(idx_names(sd)) == 1

    # ...and again on the next transition, with the throttle window opened so
    # the sweep really does run a second time.
    age_file(agb.sweep_marker_path(sd, HOST), agb.SWEEP_INTERVAL + 1)
    assert agb.cmd_hook(["blocked"]) == 0
    assert agb.list_session_keys(sd, HOST) == [key]
    assert marker_keys(agb, sd) == [key]
    assert len(idx_names(sd)) == 1
    assert "error:" not in err_text(agb, sd, key)


def test_a_direct_sweep_of_a_live_entry_changes_nothing_but_the_beat(agb, sd):
    key = write_session(agb, sd, HOST, "a3f9c1e0", LIVE_PID, LIVE_START)
    record = os.stat(agb.record_path(sd, key, HOST))
    aged = age_file(agb.state_path(sd, key, HOST), 600)

    assert agb.sweep_host(sd, HOST) == {"reaped": [], "idx": [], "err": [],
                                        "temps": []}
    assert agb.list_session_keys(sd, HOST) == [key]
    assert os.stat(agb.state_path(sd, key, HOST)).st_mtime > aged
    after = os.stat(agb.record_path(sd, key, HOST))
    assert (after.st_mtime, after.st_ino) == (record.st_mtime, record.st_ino)


def test_a_live_blocked_session_with_a_thirty_minute_beat_age_is_still_shown(
        agb, sd):
    """Design amendment 1: no age, and no host silence, is ever evidence. A
    `blocked` agent waiting on the user fires no hooks for as long as it takes
    the user to answer, and half an hour of that must not remove it."""
    key = write_session(agb, sd, HOST, "a3f9c1e0", LIVE_PID, LIVE_START,
                        state="blocked")
    age_file(agb.state_path(sd, key, HOST), 1800)

    agb.sweep_host(sd, HOST)
    assert agb.list_session_keys(sd, HOST) == [key]

    # "shown" means the feed still emits it, not merely that the file survived.
    out = io.StringIO()
    agb.feed_loop(sd, MAC, out=out, iterations=1, sleep=lambda _s: None)
    line = json.loads(out.getvalue().splitlines()[0])
    assert line["t"] == "snapshot"
    assert [(s["key"], s["state"]) for s in line["sessions"]] == [
        (key, "blocked")]


# ---------------------------------------------------------------------------
# proven death -- the only thing that unlinks
# ---------------------------------------------------------------------------

def test_a_dead_own_host_entry_is_reaped(agb, sd):
    pid, starttime = conftest.dead_agent()
    key = write_session(agb, sd, HOST, "a3f9c1e0", pid, starttime)

    assert agb.sweep_host(sd, HOST)["reaped"] == [key]
    assert not exists(agb.state_path(sd, key, HOST))
    assert not exists(agb.record_path(sd, key, HOST))
    assert marker_keys(agb, sd) == []
    assert "reaped:" in err_text(agb, sd, key)


def test_a_reused_agent_pid_is_reaped_by_its_starttime(agb, sd):
    """The pid exists, so `kill` proves nothing -- but it started at a different
    moment, so the process holding it is not ours."""
    key = write_session(agb, sd, HOST, "a3f9c1e0", LIVE_PID, LIVE_START + 1)
    assert agb.sweep_host(sd, HOST)["reaped"] == [key]
    assert not exists(agb.state_path(sd, key, HOST))


def test_only_the_dead_entry_is_reaped(agb, sd):
    dead = write_session(agb, sd, HOST, "a3f9c1e0", *conftest.dead_agent())
    live = write_session(agb, sd, HOST, "b1b2b3b4", LIVE_PID, LIVE_START)

    assert agb.sweep_host(sd, HOST)["reaped"] == [dead]
    assert agb.list_session_keys(sd, HOST) == [live]
    assert marker_keys(agb, sd) == [live]


def test_an_entry_with_no_pid_is_never_unlinked(agb, sd):
    """The fail-safe entry: a hook that could not identify the agent records no
    pid, so nothing can ever prove it dead. `agb prune` is its terminal path
    (amendment 4), and a guess here would be exactly what amendment 1 forbids."""
    key = write_session(agb, sd, HOST, "a3f9c1e0", None, None)
    assert agb.sweep_host(sd, HOST) == {"reaped": [], "idx": [], "err": [],
                                        "temps": []}
    assert exists(agb.state_path(sd, key, HOST))


@pytest.mark.parametrize("raw", [
    b"",                                              # the O_TRUNC window
    b"active\n",                                      # short
    b"active\nbox2\n1\n2\n3\n4\n",                    # long
    b"nonsense\nbox2\n1\n2\n3\n",                     # not in the vocabulary
])
def test_a_state_that_fails_validation_is_never_adjudicated(agb, sd, raw):
    """Constraint #8. A peer mid-`O_TRUNC` is indistinguishable from a corrupt
    file, and reaping on one is how a live agent's row disappears."""
    key = write_session(agb, sd, HOST, "a3f9c1e0", *conftest.dead_agent())
    agb.write_in_place(agb.state_path(sd, key, HOST), raw)

    assert agb.sweep_host(sd, HOST)["reaped"] == []
    assert exists(agb.state_path(sd, key, HOST))


def test_a_state_unlinked_between_the_listing_and_the_open_is_not_an_error(
        agb, sd, monkeypatch):
    key = write_session(agb, sd, HOST, "a3f9c1e0", LIVE_PID, LIVE_START)
    real = agb.read_state_entry

    def vanish(sd_, host, key_):
        os.unlink(agb.state_path(sd_, key_, host))
        return real(sd_, host, key_)

    monkeypatch.setattr(agb, "read_state_entry", vanish)
    assert agb.sweep_host(sd, HOST)["reaped"] == []


def test_a_failing_reap_is_contained_to_its_own_entry(agb, sd, monkeypatch):
    """One `EACCES` must not cost the rest of the pass -- nor go unrecorded."""
    dead = write_session(agb, sd, HOST, "a3f9c1e0", *conftest.dead_agent())
    live = write_session(agb, sd, HOST, "b1b2b3b4", LIVE_PID, LIVE_START)

    def boom(sd_, host, key, *args, **kwargs):
        if key == dead:
            raise OSError(errno.EACCES, "Permission denied")
        return agb.SWEEP_ALIVE

    monkeypatch.setattr(agb, "sweep_entry", boom)
    assert agb.sweep_host(sd, HOST)["reaped"] == []
    assert sorted(agb.list_session_keys(sd, HOST)) == sorted([dead, live])
    assert "cannot adjudicate" in err_text(agb, sd, dead)


# ---------------------------------------------------------------------------
# `idx/` -- the mapping `agr` never collected (failure mode #3)
# ---------------------------------------------------------------------------

def test_an_idx_entry_whose_key_is_gone_is_dropped(agb, sd):
    dead_pid, dead_start = conftest.dead_agent()
    path = write_idx(agb, sd, HOST, LIVE_PID, "%24", "a3f9c1e0",
                     dead_pid, dead_start, age=600)

    assert agb.sweep_host(sd, HOST)["idx"] == [os.path.basename(path)]
    assert not exists(path)


def test_a_dead_agents_session_and_idx_are_both_gone_after_one_pass(agb, sd):
    """The two halves of one agent, reaped in the right order: the session pass
    runs first, so the idx pass sees a key that positively no longer exists."""
    pid, starttime = conftest.dead_agent()
    key = write_session(agb, sd, HOST, "a3f9c1e0", pid, starttime)
    path = write_idx(agb, sd, HOST, LIVE_PID, "%24", key, pid, starttime,
                     age=600)

    result = agb.sweep_host(sd, HOST)
    assert result["reaped"] == [key]
    assert result["idx"] == [os.path.basename(path)]
    assert idx_names(sd) == []


def test_an_idx_entry_whose_key_exists_is_kept(agb, sd):
    key = write_session(agb, sd, HOST, "a3f9c1e0", None, None)
    path = write_idx(agb, sd, HOST, LIVE_PID, "%24", key, None, None, age=600)

    assert agb.sweep_host(sd, HOST)["idx"] == []
    assert exists(path)


def test_a_fresh_idx_entry_is_never_dropped(agb, sd):
    """The mint race: `link_idx` creates the idx file microseconds before the
    first `.state` exists, so during that window the key genuinely does not
    exist. Dropping it there hands a live agent a second key on its next hook
    -- a duplicate row for one agent, which is the bug the bijection exists to
    prevent."""
    path = write_idx(agb, sd, HOST, LIVE_PID, "%24", "a3f9c1e0", None, None)
    assert agb.sweep_host(sd, HOST)["idx"] == []
    assert exists(path)


def test_a_live_agents_idx_entry_survives_even_with_no_session_file(agb, sd):
    """The same race for an entry old enough to be past the grace: the recorded
    agent is **provably alive**, and that outranks everything else about it."""
    path = write_idx(agb, sd, HOST, LIVE_PID, "%24", "a3f9c1e0",
                     LIVE_PID, LIVE_START, age=6000)
    assert agb.sweep_host(sd, HOST)["idx"] == []
    assert exists(path)


def test_a_corrupt_idx_entry_is_kept(agb, sd):
    """Constraint #8: a failed read is no information. `bind_key` re-mints over
    a corrupt idx anyway, so deleting one buys nothing and risks everything."""
    path = os.path.join(sd, "idx", "%s-%d-%%24" % (HOST, LIVE_PID))
    agb.write_in_place(path, "not an idx file\n")
    age_file(path, 6000)
    assert agb.sweep_host(sd, HOST)["idx"] == []
    assert exists(path)


def test_a_foreign_hosts_idx_entry_is_never_dropped(agb, sd):
    dead_pid, dead_start = conftest.dead_agent()
    path = write_idx(agb, sd, FOREIGN, dead_pid, "p%d" % (dead_start,),
                     "b1b2b3b4", dead_pid, dead_start, age=6000)
    assert agb.sweep_host(sd, HOST)["idx"] == []
    assert exists(path)


# --- the per-kind anchor predicate ----------------------------------------

def test_the_non_tmux_anchor_predicate_uses_pid_and_starttime(agb):
    """Machine #3's plain-ssh anchor, which revision 3 left with no predicate at
    all: here the anchor pid **is** the agent pid and the tag carries its
    starttime, so the full pid-reuse guard applies."""
    dead_pid, dead_start = conftest.dead_agent()
    assert agb.idx_anchor_liveness("pid", dead_pid, "p%d" % (dead_start,)) == \
        agb.LIVENESS_DEAD
    assert agb.idx_anchor_liveness("pid", LIVE_PID, "p%d" % (LIVE_START,)) == \
        agb.LIVENESS_ALIVE
    # a recycled pid: alive, but not the process the anchor was built for
    assert agb.idx_anchor_liveness("pid", LIVE_PID,
                                   "p%d" % (LIVE_START + 1,)) == \
        agb.LIVENESS_DEAD
    # and with no starttime recorded, only ESRCH counts
    assert agb.idx_anchor_liveness("pid", LIVE_PID, "p-") == \
        agb.LIVENESS_UNKNOWN


def test_the_tmux_anchor_predicate_only_answers_to_esrch(agb):
    """No starttime is recorded for a tmux **server**, so a reused server pid
    comes back UNKNOWN -- the safe direction. `bind_key` already re-mints when
    the recorded agent disagrees, so nothing depends on guessing here."""
    dead_pid, _dead_start = conftest.dead_agent()
    assert agb.idx_anchor_liveness("tmux", dead_pid, "%24") == \
        agb.LIVENESS_DEAD
    assert agb.idx_anchor_liveness("tmux", LIVE_PID, "%24") == \
        agb.LIVENESS_UNKNOWN
    assert agb.idx_anchor_liveness("sid", LIVE_PID, "s") == \
        agb.LIVENESS_UNKNOWN
    assert agb.idx_anchor_liveness(None, LIVE_PID, "?") == \
        agb.LIVENESS_UNKNOWN


def test_a_non_tmux_idx_entry_with_a_dead_anchor_is_dropped(agb, sd):
    """Isolating the anchor predicate from the key check: the key still exists
    (the entry has no pid, so it can never be proven dead), but the *anchor*
    names a process that is provably gone."""
    dead_pid, dead_start = conftest.dead_agent()
    key = write_session(agb, sd, HOST, "a3f9c1e0", None, None)
    path = write_idx(agb, sd, HOST, dead_pid, "p%d" % (dead_start,), key,
                     None, None, age=600)

    assert agb.sweep_host(sd, HOST)["idx"] == [os.path.basename(path)]
    assert not exists(path)
    # ...and the session it pointed at is untouched: no pid, no proof, no reap.
    assert exists(agb.state_path(sd, key, HOST))


def test_a_non_tmux_idx_entry_with_a_live_anchor_is_kept(agb, sd):
    key = write_session(agb, sd, HOST, "a3f9c1e0", None, None)
    path = write_idx(agb, sd, HOST, LIVE_PID, "p%d" % (LIVE_START,), key,
                     None, None, age=600)
    assert agb.sweep_host(sd, HOST)["idx"] == []
    assert exists(path)


def test_a_tmux_anchor_whose_server_pid_was_reused_is_kept(agb, sd):
    """The pid is held by *something*, and nothing here can tell whether it is
    the same tmux server. UNKNOWN is not death."""
    key = write_session(agb, sd, HOST, "a3f9c1e0", None, None)
    path = write_idx(agb, sd, HOST, LIVE_PID, "%24", key, None, None, age=600)
    assert agb.sweep_host(sd, HOST)["idx"] == []
    assert exists(path)


def test_a_stale_idx_file_cannot_resurrect_an_old_key(agb, sd):
    """End to end, and the reason the idx sweep exists at all: agent 1 dies in
    pane %24, and the pane id is never reused within a tmux server -- so agent 2
    lands on the very same anchor. It must get a new key, and the row the bridge
    already marked `[done]` must not be rebound."""
    dead_pid, dead_start = conftest.dead_agent()
    old_key = write_session(agb, sd, HOST, "a3f9c1e0", dead_pid, dead_start)
    anchor = agb.Anchor(HOST, "tmux", LIVE_PID, "%24", pane="%24")
    path = agb.idx_path(sd, anchor)
    agb.link_idx(path, old_key, dead_pid, dead_start)
    age_file(path, 600)

    agb.sweep_host(sd, HOST)
    assert not exists(agb.state_path(sd, old_key, HOST))
    assert not exists(path)

    key, minted = agb.bind_key(sd, anchor, LIVE_PID, LIVE_START)
    assert minted and key != old_key


# ---------------------------------------------------------------------------
# `err/` -- breadcrumbs outlive their session, but not forever
# ---------------------------------------------------------------------------

def test_a_breadcrumb_outlives_its_session_by_the_grace_window(agb, sd):
    """The last line written to one is usually the reap itself, so "why did
    this row disappear?" has to stay answerable after it did."""
    pid, starttime = conftest.dead_agent()
    key = write_session(agb, sd, HOST, "a3f9c1e0", pid, starttime)

    result = agb.sweep_host(sd, HOST)
    assert result["reaped"] == [key] and result["err"] == []
    assert "reaped:" in err_text(agb, sd, key)


def test_an_orphan_breadcrumb_past_the_grace_is_reaped(agb, sd):
    """Nothing else reaps them, and they are per-session by construction: one
    file per agent that ever ran on this host, forever."""
    agb.breadcrumb(sd, "a3f9c1e0", "long gone", HOST)
    path = agb.err_log_path(sd, "a3f9c1e0", HOST)
    age_file(path, agb.SWEEP_ERR_GRACE + 60)

    assert agb.sweep_host(sd, HOST)["err"] == [os.path.basename(path)]
    assert not exists(path)


def test_a_breadcrumb_whose_session_still_exists_is_never_reaped(agb, sd):
    key = write_session(agb, sd, HOST, "a3f9c1e0", None, None)
    agb.breadcrumb(sd, key, "still live", HOST)
    age_file(agb.err_log_path(sd, key, HOST), agb.SWEEP_ERR_GRACE * 10)

    assert agb.sweep_host(sd, HOST)["err"] == []
    assert exists(agb.err_log_path(sd, key, HOST))


def test_the_keyless_breadcrumb_log_is_never_reaped(agb, sd):
    """`err/<host>.-.log` holds the breadcrumbs of invocations that never had a
    key -- a rejected state, a statedir that could not be created. It belongs to
    no session, so "its session is gone" is not a thing that can be true of it."""
    agb.breadcrumb(sd, None, "ignored: state 'nonsense'", HOST)
    path = agb.err_log_path(sd, None, HOST)
    age_file(path, agb.SWEEP_ERR_GRACE * 10)

    assert agb.sweep_host(sd, HOST)["err"] == []
    assert exists(path)


# ---------------------------------------------------------------------------
# `*.tmp.*` -- debris from a writer that was killed mid-write
# ---------------------------------------------------------------------------

def _temp(agb, sd, directory, name, host=HOST, age=None):
    path = os.path.join(directory, "%s.tmp.%s.4242.abcd1234" % (name, host))
    agb.write_in_place(path, "half a file")
    if age:
        age_file(path, age)
    return path


def test_abandoned_temps_are_reaped_in_every_directory_that_gets_them(agb, sd):
    agb.ensure_session_dir(sd, HOST)
    paths = [
        _temp(agb, sd, agb.session_dir(sd, HOST), "a3f9c1e0.json",
              age=agb.SWEEP_TEMP_GRACE + 60),
        _temp(agb, sd, agb.gen_dir(sd), HOST + ".marker",
              age=agb.SWEEP_TEMP_GRACE + 60),
        _temp(agb, sd, os.path.join(sd, "idx"), "%s-1-%%24" % (HOST,),
              age=agb.SWEEP_TEMP_GRACE + 60),
    ]
    assert sorted(agb.sweep_host(sd, HOST)["temps"]) == sorted(paths)
    assert [p for p in paths if exists(p)] == []


def test_a_fresh_temp_is_never_reaped(agb, sd):
    """`atomic_write`'s window is microseconds wide, but it is a real window and
    the file inside it is about to be renamed into place."""
    agb.ensure_session_dir(sd, HOST)
    path = _temp(agb, sd, agb.session_dir(sd, HOST), "a3f9c1e0.json")
    assert agb.sweep_host(sd, HOST)["temps"] == []
    assert exists(path)


def test_another_hosts_temp_is_never_reaped(agb, sd):
    """`gen/` holds every host's marker, so its temps are the one place a sweep
    could reach across hosts. The writing host is in the temp's own name."""
    path = _temp(agb, sd, agb.gen_dir(sd), FOREIGN + ".marker", host=FOREIGN,
                 age=agb.SWEEP_TEMP_GRACE * 10)
    assert agb.sweep_host(sd, HOST)["temps"] == []
    assert exists(path)


def test_a_temp_never_appears_as_a_session(agb, sd):
    """Exact-suffix filtering: a temp, a record and a non-hex name must all be
    invisible to the listing that becomes the marker and drives the sweep."""
    agb.ensure_session_dir(sd, HOST)
    key = write_session(agb, sd, HOST, "a3f9c1e0", LIVE_PID, LIVE_START)
    for junk in ("a3f9c1e0.state.tmp.box2.12.abcd", "b1b2b3b4.json",
                 "notakey.state", "README", ".state"):
        with open(os.path.join(agb.session_dir(sd, HOST), junk), "w") as handle:
            handle.write("x")

    assert agb.list_session_keys(sd, HOST) == [key]
    assert agb.rebuild_marker(sd, HOST) == [key]
    assert agb.sweep_host(sd, HOST)["reaped"] == []


# ---------------------------------------------------------------------------
# the throttle, and where the sweep hangs off the hook
# ---------------------------------------------------------------------------

def test_the_throttle_suppresses_a_second_sweep_inside_the_window(agb, agent):
    """Once per 60 s per host. Without it, a host with a hundred transitions a
    minute would `readdir` four directories on every one of them."""
    sd = agent
    assert agb.cmd_hook(["active"]) == 0            # claims the window

    dead = write_session(agb, sd, HOST, "b1b2b3b4", *conftest.dead_agent())
    assert agb.cmd_hook(["blocked"]) == 0           # transition, but throttled
    assert exists(agb.state_path(sd, dead, HOST))

    age_file(agb.sweep_marker_path(sd, HOST), agb.SWEEP_INTERVAL + 1)
    assert agb.cmd_hook(["completed"]) == 0
    assert not exists(agb.state_path(sd, dead, HOST))


def test_the_sweep_runs_on_the_transition_path(agb, agent, monkeypatch):
    sd = agent
    seen = []
    monkeypatch.setattr(agb, "sweep_host",
                        lambda sd_, host=None, now=None: seen.append(host))
    assert agb.cmd_hook(["active"]) == 0
    assert seen == [HOST]


def test_the_sweep_never_runs_on_the_no_change_path(agb, agent, monkeypatch):
    """The hot-path budget: two files and no `readdir`. A sweep here would make
    every tool call pay four listings on a hard NFS mount."""
    sd = agent
    assert agb.cmd_hook(["active"]) == 0
    age_file(agb.sweep_marker_path(sd, HOST), agb.SWEEP_INTERVAL + 1)

    def boom(*args, **kwargs):
        raise AssertionError("the sweep must not touch the hot path")

    monkeypatch.setattr(agb, "sweep_host", boom)
    assert agb.cmd_hook(["active"]) == 0             # no change, no sweep


def test_a_failing_sweep_never_fails_the_hook(agb, agent, monkeypatch):
    """It is a maintenance pass. A hook that recorded its state successfully
    must not then fail -- and must not fail *silently* either.

    `monkeypatch`, not a hand-rolled try/finally: `agb` is session-scoped, so an
    attribute left patched -- which one early return above the `try` is enough
    to arrange -- would leave the sweep broken for every test after this one.
    """
    sd = agent

    def boom(*args, **kwargs):
        raise OSError(errno.EACCES, "Permission denied")

    monkeypatch.setattr(agb, "sweep_host", boom)
    assert agb.cmd_hook(["active"]) == 0
    monkeypatch.undo()

    keys = agb.list_session_keys(sd, HOST)
    assert len(keys) == 1
    assert agb.parse_state(
        open(agb.state_path(sd, keys[0], HOST), "rb").read())["state"] == \
        "active"
    assert "sweep failed" in err_text(agb, sd, None)


def test_machine_three_reaps_its_own_entries_with_no_feed_running(agb, sd,
                                                                  set_host,
                                                                  set_tmux,
                                                                  set_agent_pid):
    """The scenario this task exists for. #3 is reachable only through box #2,
    so no feed ever runs there and a hook is the only agb code that does."""
    three = "machine3"
    set_host(three)
    set_tmux(None, None)                    # plain ssh: no tmux anywhere
    set_agent_pid(LIVE_PID)

    stale_pid, stale_start = conftest.dead_agent()
    stale = write_session(agb, sd, three, "a3f9c1e0", stale_pid, stale_start)
    stale_idx = write_idx(agb, sd, three, stale_pid, "p%d" % (stale_start,),
                          stale, stale_pid, stale_start, age=600)

    assert agb.cmd_hook(["active"]) == 0

    assert not exists(agb.state_path(sd, stale, three))
    assert not exists(stale_idx)
    keys = agb.list_session_keys(sd, three)
    assert keys == marker_keys(agb, sd, three)
    assert len(keys) == 1 and keys[0] != stale


# ---------------------------------------------------------------------------
# structural guards
# ---------------------------------------------------------------------------

def test_the_sweep_opens_no_record(agb, sd, monkeypatch):
    """Runtime half. `.state` carries host/pid/starttime precisely so that the
    sweep -- which runs inside a hook -- never has to import json."""
    write_session(agb, sd, HOST, "a3f9c1e0", *conftest.dead_agent())
    write_session(agb, sd, HOST, "b1b2b3b4", LIVE_PID, LIVE_START)

    opened = []
    real = os.open

    def spy(path, *args, **kwargs):
        if str(path).startswith(sd):
            opened.append(str(path))
        return real(path, *args, **kwargs)

    monkeypatch.setattr(os, "open", spy)
    agb.sweep_host(sd, HOST)
    assert [p for p in opened if p.endswith(".json")] == []


def test_the_sweep_reaches_no_json_structurally(agb_tree):
    """Structural half, on the merged call graph rather than on a grep: the
    file's comments discuss json throughout, and the first cut of every guard
    like this one passed against its own prose."""
    funcs = conftest.functions(agb_tree)
    reachable = conftest.reachable_from(funcs, "sweep_host")

    assert "sweep_entry" in reachable and "read_state_entry" in reachable
    assert "_json" not in reachable
    assert "read_record" not in reachable
    assert "record_path" in reachable          # unlinked, never opened


def test_the_sweep_has_one_door_and_it_is_the_throttle(agb_tree, all_trees):
    """`maybe_sweep` claims the 60 s window *before* the sweep runs, so an
    aborted sweep cannot spin once per transition. A second caller of
    `sweep_host` would be a second, unthrottled door."""
    funcs = conftest.functions(*all_trees)
    callers = set(name for name, node in funcs.items()
                  if (None, "sweep_host") in conftest.calls(node))
    assert callers == set(["maybe_sweep"])

    callers = set(name for name, node in funcs.items()
                  if (None, "maybe_sweep") in conftest.calls(node))
    assert callers == set(["hook_transition"])

    body = funcs["maybe_sweep"].body
    claim = [i for i, node in enumerate(body)
             if "write_in_place" in [attr for _b, attr in conftest.calls(node)]]
    sweep = [i for i, node in enumerate(body)
             if "sweep_host" in [attr for _b, attr in conftest.calls(node)]]
    assert claim and sweep and max(claim) < min(sweep)


def test_every_sweep_entry_point_checks_the_host_at_runtime(agb_tree):
    """`_require_own_host` raises rather than asserting, so `python -O` cannot
    remove the one guard between this tool and another host's live sessions."""
    funcs = conftest.functions(agb_tree)
    for name in ("sweep_host", "sweep_entry", "reap_entry"):
        assert (None, "_require_own_host") in conftest.calls(funcs[name]), name


def test_the_sweep_never_writes_to_stdout_or_stderr(agb_tree):
    """It runs inside a hook: stdout is injected into Claude's prompt context
    (constraint #15) and stderr is noise in the transcript. Breadcrumbs are the
    channel.

    `print` is checked as well as `sys.stdout`. Inspecting attribute names alone
    left the shortest possible spelling of the hazard invisible -- a bare
    `print("swept %s" % (host,))` inside `sweep_host` passed this guard
    untouched, which is the one line someone debugging the sweep would actually
    reach for.
    """
    funcs = conftest.functions(agb_tree)
    for name in conftest.reachable_from(funcs, "sweep_host"):
        assert (None, "print") not in conftest.calls(funcs[name]), name
        for child in ast.walk(funcs[name]):
            if isinstance(child, ast.Attribute):
                assert child.attr not in ("stdout", "stderr"), name


def test_the_module_top_still_imports_only_the_cheap_four(agb_tree):
    """Every module-scope import is paid on every tool call. The sweep needs
    nothing beyond `os`/`errno`/`time`, and if it ever seems to, that is the
    signal to move it, not to import."""
    assert conftest.toplevel_imports(agb_tree) == set(
        ["errno", "os", "sys", "time"])


# ---------------------------------------------------------------------------
# the grace gates: a file dated AHEAD of `now` is younger, never fully aged
# ---------------------------------------------------------------------------
#
# `hook_apply` samples `now` locally, once, at the top; the mtimes these gates
# compare it against are stamped by the NFS server several round trips later.
# So a file another hook creates inside that window is future-dated with no
# clock skew whatsoever. `interval_elapsed` counts that as due -- correct for a
# rate limiter, catastrophic for a gate that authorises `unlink` -- which is
# why the three deletion gates use `grace_elapsed` instead.

def future_file(path, seconds=300):
    """Stamp `path` `seconds` into the future, as a concurrent writer would."""
    st = os.stat(path)
    os.utime(path, (st.st_atime + seconds, st.st_mtime + seconds))
    return os.stat(path).st_mtime


def test_grace_elapsed_has_no_future_arm_and_interval_elapsed_does(agb):
    """The split itself. Note the `age == interval` case on both: nothing in
    the suite pinned the boundary before, so `>=` could be quietly weakened to
    `>` -- moving every grace by one whole tick -- and stay green."""
    assert agb.grace_elapsed(100.0, 85.0, 15.0)          # exactly due
    assert agb.grace_elapsed(100.0, 80.0, 15.0)
    assert not agb.grace_elapsed(100.0, 90.0, 15.0)
    assert not agb.grace_elapsed(100.0, 500.0, 15.0)     # ahead of now: keep

    assert agb.interval_elapsed(100.0, 85.0, 15.0)       # exactly due
    assert agb.interval_elapsed(100.0, 80.0, 15.0)
    assert not agb.interval_elapsed(100.0, 90.0, 15.0)
    assert agb.interval_elapsed(100.0, 500.0, 15.0)      # ahead of now: due


def test_a_concurrent_hooks_in_flight_temp_survives_the_debris_sweep(agb, sd):
    """The failure this gate exists to stop: `sweep_debris` unlinking a temp
    another hook is mid-`atomic_write` on. The rename then fails ENOENT, and in
    `link_idx` that propagates out of `bind_key` into `cmd_hook`'s catch-all --
    the invocation records nothing but a breadcrumb."""
    temp = os.path.join(sd, "gen", "%s.marker.tmp.%s.4242.abcd" % (HOST, HOST))
    agb.write_in_place(temp, "half a marker\n")
    mtime = future_file(temp)

    dropped = agb.sweep_debris(sd, HOST, now=mtime - 300.0)

    assert dropped == []
    assert exists(temp)


def test_a_just_minted_anchor_survives_the_idx_sweep_it_is_graced_by(agb, sd):
    """`SWEEP_IDX_GRACE` exists for exactly the window in which an anchor has
    been linked but its `.state` has not landed yet. The future arm bypassed
    the grace *inside* that window: the anchor is dropped, the next hook mints
    a SECOND key for the same pane, and the duplicate row the grace exists to
    prevent is what the user sees."""
    dead_pid, dead_start = conftest.dead_agent()
    idx = write_idx(agb, sd, HOST, 4242, "s", "a3f9c1e0", dead_pid, dead_start)
    mtime = future_file(idx)

    dropped = agb.sweep_idx(sd, HOST, set(), now=mtime - 300.0)

    assert dropped == []
    assert exists(idx)


def test_a_breadcrumb_log_dated_ahead_of_now_is_kept(agb, sd):
    err = agb.err_log_path(sd, "c1c2c3c4", HOST)
    agb.breadcrumb(sd, "c1c2c3c4", "something happened", HOST)
    mtime = future_file(err)

    assert agb.sweep_err_logs(sd, HOST, set(), now=mtime - 300.0) == []
    assert exists(err)


def test_the_grace_gates_are_the_only_thing_the_deletion_paths_ask(agb_tree):
    """Structural, because the fix is one identifier and re-typing the old one
    would restore the bug silently. Every gate that guards an `unlink` calls
    `grace_elapsed`; `interval_elapsed` is for rate limiters and appears in
    none of them."""
    funcs = conftest.functions(agb_tree)
    for name in ("sweep_idx", "sweep_err_logs", "sweep_debris"):
        called = set(attr for _base, attr in conftest.calls(funcs[name]))
        assert "grace_elapsed" in called, name
        assert "interval_elapsed" not in called, name
    for name in ("maybe_sweep", "hook_apply"):
        called = set(attr for _base, attr in conftest.calls(funcs[name]))
        assert "grace_elapsed" not in called, name
