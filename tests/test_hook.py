"""Task 2b -- `agb hook <state>`: hot path, transition, breadcrumbs.

The expensive guards in here (the syscall budget, the json-import guard and its
negative control, the marker-rebuild race) are the ones that stop the hot path
from eroding one convenient line at a time. Every one of them was cheap to write
and would be very hard to reconstruct after the fact.
"""

import ast
import errno
import json
import os
import re
import stat
import subprocess
import sys

import pytest

import conftest


REAL_TMUX = "/tmp/tmux-100000/default,1200000,23"
HOST = "box2"

# ⚠️ A **real, live** agent pid, not a fabricated one -- and this changed with
# Task 5, exactly as `tests/test_feed.py`'s did with Task 3b. A made-up pid is
# overwhelmingly likely to be dead, and once a transition runs the hook-side
# sweep, every session written under one is *correctly* reaped the moment it is
# written. That silently turns every test in this file into a reaping test.
PID, STARTTIME = conftest.live_agent()


@pytest.fixture
def agent(agb, statedir, set_tmux, set_host, set_agent_pid, monkeypatch):
    """A resolvable agent in a tmux pane, with a created statedir.

    Bundled because every hook test needs the same five overrides, and a test
    that forgot one would silently exercise the developer's own environment.
    """
    set_host(HOST)
    set_tmux(REAL_TMUX, "%24")
    set_agent_pid(PID)
    monkeypatch.setattr(agb, "proc_starttime", lambda pid: STARTTIME)
    return str(statedir)


def ident_of(agb, sd):
    return agb.resolve_identity(sd)


def read_state_file(agb, sd, key, host=HOST):
    with open(agb.state_path(sd, key, host), "rb") as handle:
        return handle.read()


def read_marker(agb, sd, host=HOST):
    with open(agb.marker_path(sd, host), "rb") as handle:
        return handle.read()


def read_record_file(agb, sd, key, host=HOST):
    import json
    with open(agb.record_path(sd, key, host)) as handle:
        return json.load(handle)


def err_log(agb, sd, key, host=HOST):
    path = agb.err_log_path(sd, key, host)
    if not os.path.exists(path):
        return ""
    with open(path) as handle:
        return handle.read()


# ---------------------------------------------------------------------------
# `.state` -- five bare lines, or no information at all
# ---------------------------------------------------------------------------

def test_state_roundtrips(agb):
    raw = agb.format_state("active", HOST, 48213, 9182736, 47)
    assert raw.splitlines() == ["active", HOST, "48213", "9182736", "47"]
    assert agb.parse_state(raw.encode())["pid"] == 48213

    raw = agb.format_state("active", HOST, PID, STARTTIME, 47)
    assert agb.parse_state(raw.encode()) == {
        "state": "active", "host": HOST, "pid": PID,
        "starttime": STARTTIME, "seq": 47}


def test_state_carries_a_missing_pid_as_a_dash(agb):
    raw = agb.format_state("blocked", HOST, None, None, 1)
    assert raw.splitlines()[2:4] == ["-", "-"]
    parsed = agb.parse_state(raw.encode())
    assert parsed["pid"] is None and parsed["starttime"] is None


@pytest.mark.parametrize("raw", [
    b"",                                   # the in-place O_TRUNC window
    b"active\n",
    b"active\n" + HOST.encode() + b"\n1\n2\n",          # four lines
    b"active\n" + HOST.encode() + b"\n1\n2\n3\n4\n",    # six lines
    b"active\n\n1\n2\n3\n",                             # no host
    b"nonsense\n" + HOST.encode() + b"\n1\n2\n3\n",     # not in the vocabulary
    b"active\n" + HOST.encode() + b"\nx\n2\n3\n",       # unparseable pid
    b"active\n" + HOST.encode() + b"\n1\n2\nx\n",       # unparseable seq
    b"active\n" + HOST.encode() + b"\n1\n2\n-4\n",      # negative seq
    b"\xff\xfe\x00\n\n\n\n\n",                          # not utf-8
])
def test_a_bad_state_read_is_no_information_never_empty(agb, raw):
    """Constraint #8. `None` here means "retain what you had"; anything that
    returned a *value* for these inputs would remove a live agent's row."""
    assert agb.parse_state(raw) is None


def test_state_accepts_idle_even_though_no_hook_writes_it(agb):
    """Liberal in what it reads: the bridge emits `idle` for `[?]` and `[done]`,
    so the parser must not treat it as corruption if it ever appears."""
    assert agb.parse_state(
        agb.format_state("idle", HOST, None, None, 0).encode()) is not None


# ---------------------------------------------------------------------------
# the marker -- the live key list, as content
# ---------------------------------------------------------------------------

def test_marker_roundtrips_with_its_sentinel(agb):
    raw = agb.format_marker(["a3f9c1e0", "b1b2b3b4"])
    assert raw.splitlines()[-1] == "#end 2"
    assert agb.parse_marker(raw.encode()) == ["a3f9c1e0", "b1b2b3b4"]


def test_an_empty_marker_is_a_valid_empty_list(agb):
    """Distinct from a *failed* read: a host with no sessions must be
    expressible, or its last key could never be removed."""
    assert agb.parse_marker(agb.format_marker([]).encode()) == []


@pytest.mark.parametrize("raw", [
    b"",
    b"a3f9c1e0\n",                       # no sentinel
    b"a3f9c1e0\n#end\n",                 # sentinel without a count
    b"a3f9c1e0\n#end 2\n",               # count mismatch
    b"a3f9c1e0\n#end x\n",               # unparseable count
    b"a3f9c1e0\nnot-a-key\n#end 2\n",    # a key that cannot be one
    b"\xff\xfe\n#end 1\n",
])
def test_a_bad_marker_read_is_no_information(agb, raw):
    """The revision-3 bug in one assertion: reading a torn marker as "no keys"
    emits `remove` for every live row on that host."""
    assert agb.parse_marker(raw) is None


# ---------------------------------------------------------------------------
# the first transition
# ---------------------------------------------------------------------------

def test_first_hook_writes_record_state_and_marker(agb, agent):
    sd = agent
    ident = ident_of(agb, sd)
    assert agb.hook_apply(sd, ident, "active") == "transition"

    parsed = agb.parse_state(read_state_file(agb, sd, ident.key))
    assert parsed["state"] == "active"
    assert parsed["host"] == HOST
    assert parsed["pid"] == PID
    assert parsed["starttime"] == STARTTIME
    assert parsed["seq"] == 1
    assert agb.parse_marker(read_marker(agb, sd)) == [ident.key]

    rec = read_record_file(agb, sd, ident.key)
    assert rec["v"] == 1
    assert rec["key"] == ident.key
    assert rec["state"] == "active"
    assert rec["seq"] == 1
    assert rec["pane"] == "%24"          # two panes of one session need this
    assert rec["host"] == HOST
    assert rec["pid"] == PID
    assert rec["starttime"] == STARTTIME
    assert isinstance(rec["updated"], float)
    assert set(rec) == set(["v", "key", "label", "host", "pid", "starttime",
                            "tmux", "pane", "cwd", "state", "seq", "updated"])


def test_the_record_carries_no_beat_field(agb, agent):
    """`beat` is `.state`'s mtime, synthesized by the feed (amendment 3). A
    field would be the writer's clock and would drift against every other age."""
    sd = agent
    ident = ident_of(agb, sd)
    agb.hook_apply(sd, ident, "active")
    assert "beat" not in read_record_file(agb, sd, ident.key)


def test_the_hook_creates_the_session_directory_on_demand(agb, agent):
    sd = agent
    assert not os.path.exists(agb.session_dir(sd, HOST))
    ident = ident_of(agb, sd)
    agb.hook_apply(sd, ident, "active")
    assert os.path.isdir(agb.session_dir(sd, HOST))


# ---------------------------------------------------------------------------
# transition gating -- the whole reason the hot path is cheap
# ---------------------------------------------------------------------------

def test_an_unchanged_state_writes_nothing(agb, agent):
    sd = agent
    ident = ident_of(agb, sd)
    agb.hook_apply(sd, ident, "active")
    before = [os.stat(p) for p in (agb.state_path(sd, ident.key, HOST),
                                   agb.record_path(sd, ident.key, HOST),
                                   agb.marker_path(sd, HOST))]

    assert agb.hook_apply(sd, ident_of(agb, sd), "active") == "unchanged"

    after = [os.stat(p) for p in (agb.state_path(sd, ident.key, HOST),
                                  agb.record_path(sd, ident.key, HOST),
                                  agb.marker_path(sd, HOST))]
    for old, new in zip(before, after):
        assert (old.st_mtime_ns, old.st_ino, old.st_size) == (
            new.st_mtime_ns, new.st_ino, new.st_size)


def test_a_changed_state_bumps_seq_and_rewrites_both_files(agb, agent):
    sd = agent
    ident = ident_of(agb, sd)
    agb.hook_apply(sd, ident, "active")
    assert agb.hook_apply(sd, ident_of(agb, sd), "blocked") == "transition"

    parsed = agb.parse_state(read_state_file(agb, sd, ident.key))
    assert parsed["state"] == "blocked"
    assert parsed["seq"] == 2
    rec = read_record_file(agb, sd, ident.key)
    assert rec["state"] == "blocked"
    assert rec["seq"] == 2


def test_seq_resumes_from_the_record_when_the_state_file_is_lost(agb, agent):
    """`_next_seq` reads BOTH sidecars, and the `.json` arm was undetected:
    deleting it left every test green.

    `.state` is the cheap one and is the only file the hot path reads, so it is
    also the one a stray `rm`, a truncating writer or a half-restored backup
    loses first. Restarting `seq` at 1 there is not a cosmetic renumbering:
    `agb_mac.BridgeModel` upserts on seq **movement**, so a fresh transition
    arriving with a seq the bridge has already seen is treated as stale and the
    row simply stops updating -- the silent-staleness failure this whole design
    exists to eliminate.
    """
    sd = agent
    ident = ident_of(agb, sd)
    agb.hook_apply(sd, ident, "active")
    record_path = agb.record_path(sd, ident.key, HOST)
    record = read_record_file(agb, sd, ident.key)
    record["seq"] = 7
    agb.atomic_write(record_path, json.dumps(record, sort_keys=True) + "\n")
    os.unlink(agb.state_path(sd, ident.key, HOST))

    assert agb.hook_apply(sd, ident_of(agb, sd), "blocked") == "transition"

    assert agb.parse_state(read_state_file(agb, sd, ident.key))["seq"] == 8
    assert read_record_file(agb, sd, ident.key)["seq"] == 8


def test_a_state_file_written_by_a_previous_agent_pid_is_repaired(agb, agent,
                                                                  monkeypatch):
    """A first hook that could not resolve the agent records `-`; a later one
    that can must repair it, or the entry stays permanently unsweepable."""
    sd = agent
    monkeypatch.setenv("AGB_AGENT_PID", "-")
    ident = ident_of(agb, sd)
    agb.hook_apply(sd, ident, "active")
    assert agb.parse_state(read_state_file(agb, sd, ident.key))["pid"] is None

    monkeypatch.setenv("AGB_AGENT_PID", str(PID))
    again = ident_of(agb, sd)
    assert again.key == ident.key           # same anchor, no evidence to re-mint
    assert agb.hook_apply(sd, again, "active") == "transition"
    assert agb.parse_state(read_state_file(agb, sd, ident.key))["pid"] == PID


def test_a_corrupt_state_file_is_repaired_rather_than_trusted(agb, agent):
    sd = agent
    ident = ident_of(agb, sd)
    agb.hook_apply(sd, ident, "active")
    agb.write_in_place(agb.state_path(sd, ident.key, HOST), "active\n")

    assert agb.hook_apply(sd, ident_of(agb, sd), "active") == "transition"
    assert agb.parse_state(read_state_file(agb, sd, ident.key)) is not None


# ---------------------------------------------------------------------------
# the beat
# ---------------------------------------------------------------------------

def age_file(path, seconds):
    st = os.stat(path)
    os.utime(path, (st.st_atime - seconds, st.st_mtime - seconds))
    return os.stat(path).st_mtime_ns


def test_beat_refresh_moves_the_state_mtime_but_not_the_record(agb, agent):
    sd = agent
    ident = ident_of(agb, sd)
    agb.hook_apply(sd, ident, "active")
    state = agb.state_path(sd, ident.key, HOST)
    record = agb.record_path(sd, ident.key, HOST)
    aged = age_file(state, 20)
    record_before = os.stat(record).st_mtime_ns
    seq_before = agb.parse_state(read_state_file(agb, sd, ident.key))["seq"]

    assert agb.hook_apply(sd, ident_of(agb, sd), "active") == "beat"

    assert os.stat(state).st_mtime_ns > aged
    assert os.stat(record).st_mtime_ns == record_before
    assert agb.parse_state(read_state_file(agb, sd, ident.key))["seq"] == seq_before


def test_beat_refresh_is_throttled(agb, agent):
    """At most once per 15 s: the beat exists so a reader can see the session is
    alive, not to timestamp every tool call."""
    sd = agent
    ident = ident_of(agb, sd)
    agb.hook_apply(sd, ident, "active")
    state = agb.state_path(sd, ident.key, HOST)

    age_file(state, 20)
    assert agb.hook_apply(sd, ident_of(agb, sd), "active") == "beat"
    fresh = os.stat(state).st_mtime_ns
    assert agb.hook_apply(sd, ident_of(agb, sd), "active") == "unchanged"
    assert os.stat(state).st_mtime_ns == fresh

    # 12, not 14, against a 15 s BEAT_INTERVAL. `hook_apply` samples `now`
    # itself, so the age it computes is 12 plus however long the four lines
    # above took; at 14 that margin was under one second, and two intervening
    # `hook_apply` calls plus several stats on a loaded NFS box spend it.
    age_file(state, 12)
    assert agb.hook_apply(sd, ident_of(agb, sd), "active") == "unchanged"
    age_file(state, 4)
    assert agb.hook_apply(sd, ident_of(agb, sd), "active") == "beat"


def test_beat_uses_the_servers_clock_not_an_explicit_time(agb_tree):
    """Constraint #12: `os.utime(fd, None)` lets the NFS server stamp the mtime,
    which is what removes skew *between writer hosts*. An explicit time would
    put every host's own clock into the same comparison."""
    node = conftest.functions(agb_tree)["hook_apply"]
    utimes = [call for call in ast.walk(node)
              if isinstance(call, ast.Call)
              and isinstance(call.func, ast.Attribute)
              and call.func.attr == "utime"]
    assert len(utimes) == 1
    (second,) = utimes[0].args[1:]
    assert isinstance(second, ast.NameConstant) and second.value is None


def test_interval_elapsed_treats_a_future_mtime_as_due(agb):
    """A badly skewed pair must not freeze a beat forever."""
    assert agb.interval_elapsed(100.0, 80.0, 15.0)
    assert not agb.interval_elapsed(100.0, 90.0, 15.0)
    assert agb.interval_elapsed(100.0, 500.0, 15.0)


# ---------------------------------------------------------------------------
# the marker rebuild
# ---------------------------------------------------------------------------

def test_the_marker_is_rebuilt_from_the_directory_not_from_memory(agb, agent):
    """The lost-update race: a second unlocked writer on this host appeared
    between our two transitions. Rebuilding from an in-memory list would drop
    its key, and the feed would emit `remove` for a live agent."""
    sd = agent
    ident = ident_of(agb, sd)
    agb.hook_apply(sd, ident, "active")
    peer = "deadbeef"
    # A **live** peer agent, for the same reason PID is: with Task 5's sweep on
    # the transition path, a fabricated pid would make this a test about reaping
    # a second agent rather than about how the marker is rebuilt.
    agb.write_in_place(agb.state_path(sd, peer, HOST),
                       agb.format_state("blocked", HOST, PID, STARTTIME, 1))

    agb.hook_apply(sd, ident_of(agb, sd), "blocked")
    assert agb.parse_marker(read_marker(agb, sd)) == sorted([ident.key, peer])


def test_the_marker_rebuild_opens_no_files(agb, agent, monkeypatch):
    """Zero open()s is the point of putting the owning host in the path: with a
    flat layout the rebuild had to open every file to attribute it, and a peer
    inside its O_TRUNC window could not be attributed at all."""
    sd = agent
    ident = ident_of(agb, sd)
    agb.hook_apply(sd, ident, "active")

    sessions = agb.session_dir(sd, HOST)
    opened = []
    real_open = os.open

    def spy(path, *args, **kwargs):
        if str(path).startswith(sessions):
            opened.append(str(path))
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(os, "open", spy)
    agb.rebuild_marker(sd, HOST)
    assert opened == []


def test_the_marker_ignores_temps_and_records(agb, agent):
    sd = agent
    ident = ident_of(agb, sd)
    agb.hook_apply(sd, ident, "active")
    sessions = agb.session_dir(sd, HOST)
    for junk in ("a3f9c1e0.state.tmp.box2.12.abcd", "a3f9c1e0.json",
                 "notakey.state", "README"):
        with open(os.path.join(sessions, junk), "w") as handle:
            handle.write("x")

    assert agb.rebuild_marker(sd, HOST) == [ident.key]


def test_the_marker_rebuild_recreates_a_missing_gen_directory(agb, agent):
    sd = agent
    ident = ident_of(agb, sd)
    agb.hook_apply(sd, ident, "active")
    os.unlink(agb.marker_path(sd, HOST))
    os.rmdir(os.path.join(sd, "gen"))

    assert agb.rebuild_marker(sd, HOST) == [ident.key]
    assert agb.parse_marker(read_marker(agb, sd)) == [ident.key]


def test_the_record_is_written_before_the_state(agb, agent, monkeypatch):
    """The feed re-reads `.json` only when `.state`'s seq moves, so publishing
    the new seq first would pair it with the old record."""
    sd = agent
    ident = ident_of(agb, sd)
    order = []
    real_atomic = agb.atomic_write
    real_in_place = agb.write_in_place

    def note_atomic(path, data, **kwargs):
        order.append(("atomic", str(path)))
        return real_atomic(path, data, **kwargs)

    def note_in_place(path, data, **kwargs):
        order.append(("in_place", str(path)))
        return real_in_place(path, data, **kwargs)

    monkeypatch.setattr(agb, "atomic_write", note_atomic)
    monkeypatch.setattr(agb, "write_in_place", note_in_place)
    agb.hook_apply(sd, ident, "active")

    kinds = [(kind, os.path.basename(path)) for kind, path in order]
    record = kinds.index(("atomic", ident.key + ".json"))
    state = kinds.index(("in_place", ident.key + ".state"))
    marker = kinds.index(("atomic", HOST + ".marker"))
    assert record < state < marker


# ---------------------------------------------------------------------------
# labels, tmux and the mint path
# ---------------------------------------------------------------------------

def test_label_and_tmux_are_resolved_at_mint_and_then_preserved(agb, agent,
                                                                stub_bin):
    """A tmux subprocess per transition is not affordable, so `label`/`tmux` are
    resolved once. Every later transition must carry them forward rather than
    quietly downgrading the row's name to the cwd basename."""
    sd = agent
    stub_bin.install("tmux", "#!/bin/sh\necho build\n")
    ident = ident_of(agb, sd)
    assert ident.minted
    agb.hook_apply(sd, ident, "active")
    assert read_record_file(agb, sd, ident.key)["tmux"] == "build"
    assert read_record_file(agb, sd, ident.key)["label"] == "build"

    later = ident_of(agb, sd)
    assert not later.minted and later.tmux is None
    agb.hook_apply(sd, later, "blocked")
    rec = read_record_file(agb, sd, ident.key)
    assert rec["tmux"] == "build" and rec["label"] == "build"


def test_a_failing_tmux_binary_still_yields_a_label(agb, agent, stub_bin):
    sd = agent
    stub_bin.install("tmux", "#!/bin/sh\nexit 1\n")
    ident = ident_of(agb, sd)
    agb.hook_apply(sd, ident, "active")
    rec = read_record_file(agb, sd, ident.key)
    assert rec["tmux"] is None
    assert rec["label"] == agb.default_label(rec["cwd"], HOST)


# ---------------------------------------------------------------------------
# the sweep throttle -- transition path only
# ---------------------------------------------------------------------------

def test_the_sweep_throttle_suppresses_a_second_pass_inside_the_window(agb,
                                                                       agent):
    sd = agent
    assert agb.maybe_sweep(sd, HOST) is True
    assert agb.maybe_sweep(sd, HOST) is False
    age_file(agb.sweep_marker_path(sd, HOST), 61)
    assert agb.maybe_sweep(sd, HOST) is True


def test_the_sweep_marker_is_claimed_before_the_sweep_runs(agb, agent):
    """Claiming the window first is what stops an aborted sweep from spinning
    once per transition."""
    sd = agent
    agb.maybe_sweep(sd, HOST)
    assert os.path.exists(agb.sweep_marker_path(sd, HOST))


def test_the_sweep_throttle_is_checked_on_the_transition_path(agb, agent,
                                                              monkeypatch):
    sd = agent
    ident = ident_of(agb, sd)
    seen = []
    monkeypatch.setattr(agb, "maybe_sweep",
                        lambda sd_, host=None, now=None: seen.append(host))
    agb.hook_apply(sd, ident, "active")
    assert seen == [HOST]


def test_the_sweep_throttle_is_never_checked_on_the_no_change_path(agb, agent,
                                                                   monkeypatch):
    """Hot-path budget: this check would make the no-change path three files,
    and Task 5's sweep behind it would make that three files plus a readdir."""
    sd = agent
    agb.hook_apply(sd, ident_of(agb, sd), "active")

    def boom(*args, **kwargs):
        raise AssertionError("the sweep throttle must not touch the hot path")

    monkeypatch.setattr(agb, "maybe_sweep", boom)
    assert agb.hook_apply(sd, ident_of(agb, sd), "active") == "unchanged"


# ---------------------------------------------------------------------------
# the hot-path NFS budget
# ---------------------------------------------------------------------------

def test_the_no_change_path_touches_exactly_two_files(agb, agent, monkeypatch):
    """The whole budget in one assertion: `idx/<anchor>` and
    `sessions/<host>/<key>.state`, once each. On a hard mount with
    `timeo=600,retrans=10` every additional round trip is an independent point
    at which Claude appears to freeze."""
    sd = agent
    agb.hook_apply(sd, ident_of(agb, sd), "active")

    opened = []
    stated = []
    listed = []
    real_open, real_stat, real_listdir = os.open, os.stat, os.listdir

    def spy_open(path, *args, **kwargs):
        if str(path).startswith(sd):
            opened.append(str(path))
        return real_open(path, *args, **kwargs)

    def spy_stat(path, *args, **kwargs):
        if isinstance(path, str) and path.startswith(sd):
            stated.append(path)
        return real_stat(path, *args, **kwargs)

    def spy_listdir(path=".", *args, **kwargs):
        if str(path).startswith(sd):
            listed.append(str(path))
        return real_listdir(path, *args, **kwargs)

    monkeypatch.setattr(os, "open", spy_open)
    monkeypatch.setattr(os, "stat", spy_stat)
    monkeypatch.setattr(os, "listdir", spy_listdir)
    ident = ident_of(agb, sd)
    assert agb.hook_apply(sd, ident, "active") == "unchanged"

    assert opened == [os.path.join(sd, "idx", ident.anchor.name()),
                      agb.state_path(sd, ident.key, HOST)]
    assert stated == []
    assert listed == []


def test_the_beat_refresh_costs_no_extra_open(agb, agent, monkeypatch):
    """`os.utime(fd, None)` on the fd we already hold; `os.utime(path, None)`
    would cost a second LOOKUP on the same budgeted file."""
    sd = agent
    agb.hook_apply(sd, ident_of(agb, sd), "active")
    age_file(agb.state_path(sd, ident_of(agb, sd).key, HOST), 30)

    opened = []
    real_open = os.open

    def spy(path, *args, **kwargs):
        if str(path).startswith(sd):
            opened.append(str(path))
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(os, "open", spy)
    assert agb.hook_apply(sd, ident_of(agb, sd), "active") == "beat"
    assert len(opened) == 2


def test_the_hot_path_reads_no_config(agb, agent, monkeypatch):
    """Config is never read on the hot path -- that is why $AGB_STATEDIR is
    baked into the hook command at install time."""
    sd = agent
    agb.hook_apply(sd, ident_of(agb, sd), "active")

    def boom(path=None):
        raise AssertionError("the hot path must not read the config")

    monkeypatch.setattr(agb, "read_config", boom)
    assert agb.cmd_hook(["active"]) == 0
    assert agb.hook_apply(sd, ident_of(agb, sd), "active") == "unchanged"


# ---------------------------------------------------------------------------
# constraint #2 -- json stays off the hot path (structural, then runtime)
# ---------------------------------------------------------------------------

def test_json_has_exactly_one_import_site(all_trees):
    """Across both files: `agb_mac` reads the same NDJSON and could trivially
    have grown its own `import json`, which would leave two sites to keep in
    step and this guard covering one of them.

    Counted over the whole of both trees, not just over their functions. The
    first version of this walked `functions()` only, and a module-level
    `import json` in `agb_mac` sailed straight through it -- which is precisely
    the "the guard still looks green while the code moved out from under it"
    failure the split has to avoid.
    """
    holders = set()
    for name, node in conftest.functions(*all_trees).items():
        for child in ast.walk(node):
            if isinstance(child, ast.Import):
                for alias in child.names:
                    if alias.name == "json":
                        holders.add(name)
    assert holders == set(["_json"])

    total = 0
    for tree in all_trees:
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                total += len([a for a in node.names if a.name == "json"])
            elif isinstance(node, ast.ImportFrom) and node.module == "json":
                total += 1
    assert total == 1


def test_json_is_reached_only_through_the_transition_branch(agb_tree,
                                                            all_trees):
    """Structural half of the guard. Checked on the AST rather than by grepping
    the text, because the text of this file *discusses* json all over -- the
    first cut of every one of these guards passed against its own comments.

    Two claims: only the transition-path functions call `_json`, and
    `hook_apply` reaches the transition function from exactly one statement,
    placed **after** the compare loop that owns the `unchanged`/`beat` returns.

    `feed_line` is on the list because the wire format *is* NDJSON. That is not
    a hot path -- `feed` is one long-lived process on box #2, not a per-tool-call
    invocation -- and the teeth of this guard are the per-function assertions
    below, which name the functions the hook actually reaches. `bridge_decode`
    (Task 4a) is the same argument on the Mac: it reads the same NDJSON, and
    since Task 4c it is not even in the same *file* -- it calls `agb._json()`
    from `agb_mac`, which is why the caller search matches on the attribute name
    regardless of what it is qualified by.

    ⚠️ `tree_workspaces` is on the list because agterm answers `tree --json`
    in json, and parsing it is the only way to learn which workspace a row is
    in. Mac-side, in `agb_mac`, never reachable from `cmd_hook`.

    ⚠️ `run_rename` is on the list because the record it rewrites **is** json --
    the same file `hook_transition` writes, through the same `atomic_write`. It
    is in `agb_ops`, which no hook ever loads, so the hot path pays nothing for
    it; the point of listing it is that the record has exactly two writers and
    both are named here.

    ⚠️ Task 9a adds `read_settings` and `settings_text` in `agb_ops`:
    `~/.claude/settings.json` is JSON, and `install-hooks` is run by a human
    once per host. Listing them here rather than exempting the file is the
    point of the guard -- what makes them harmless is that no hook-path
    function reaches them, which the per-function assertions below and
    `test_no_hook_path_function_can_reach_the_operator_module` both check.
    """
    funcs = conftest.functions(*all_trees)
    callers = set(name for name, node in funcs.items()
                  if "_json" in [attr for _base, attr in conftest.calls(node)]
                  ) - set(["_json"])
    assert callers == set(["read_record", "hook_transition", "feed_line",
                           "bridge_decode", "read_settings", "settings_text",
                           "run_rename", "tree_workspaces"])

    for name in ("hook_apply", "parse_state", "bind_key", "read_idx",
                 "resolve_identity", "cmd_hook", "main"):
        called = set(attr for base, attr in conftest.calls(funcs[name]))
        assert "_json" not in called, name
        assert "read_record" not in called, name

    body = funcs["hook_apply"].body
    loop = [i for i, node in enumerate(body) if isinstance(node, ast.While)]
    transition = [i for i, node in enumerate(body)
                  if (None, "hook_transition") in conftest.calls(node)]
    assert len(loop) == 1 and len(transition) == 1
    assert transition[0] > loop[0]
    # ...and nowhere else in the function, so no branch inside the loop can
    # smuggle json onto the no-change path.
    assert sum(1 for base, attr in conftest.calls(funcs["hook_apply"])
               if attr == "hook_transition") == 1


def _seed_hot_path(agb, sd, host, pid, starttime, state="active"):
    """Pre-seed idx/ and `.state` so a subprocess call is a genuine no-change
    invocation. A first-ever hook is a transition and *will* import json, which
    is exactly the observation that invites weakening this assertion."""
    agb.ensure_session_dir(sd, host)
    anchor = agb.Anchor(host, "tmux", 1200000, "%24", pane="%24")
    key = agb.new_key()
    agb.link_idx(agb.idx_path(sd, anchor), key, pid, starttime)
    agb.write_in_place(agb.state_path(sd, key, host),
                       agb.format_state(state, host, pid, starttime, 1))
    agb.rebuild_marker(sd, host)
    return key


def _hook_env(sd, host, pid):
    env = dict(os.environ)
    env.update({"AGB_STATEDIR": sd, "AGB_HOST": host, "AGB_AGENT_PID": str(pid),
                "TMUX": REAL_TMUX, "TMUX_PANE": "%24"})
    return env


def _run_verbose(agb_path, args, env):
    proc = subprocess.Popen(
        [sys.executable, "-S", "-E", "-v", agb_path] + args,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=env)
    out, err = conftest.communicate(proc, b"")
    return proc.returncode, out, err


IMPORT_JSON = re.compile(br"^import 'json'", re.MULTILINE)


def test_the_no_change_hot_path_does_not_import_json(agb, agb_path, statedir):
    """The runtime guard. Never `-X importtime`: verified a silent no-op that
    exits 0 on this 3.6.8, so an importtime-based test passes vacuously."""
    sd = str(statedir)
    pid = os.getpid()
    starttime = agb.proc_starttime(pid)
    _seed_hot_path(agb, sd, HOST, pid, starttime)

    rc, out, err = _run_verbose(agb_path, ["hook", "active"],
                                _hook_env(sd, HOST, pid))
    assert rc == 0
    assert out == b""
    assert IMPORT_JSON.search(err) is None


def test_the_transition_path_does_import_json(agb, agb_path, statedir):
    """The negative control. Without it the assertion above could pass because
    the harness never ran agb at all."""
    sd = str(statedir)
    pid = os.getpid()
    starttime = agb.proc_starttime(pid)
    _seed_hot_path(agb, sd, HOST, pid, starttime)

    rc, out, err = _run_verbose(agb_path, ["hook", "blocked"],
                                _hook_env(sd, HOST, pid))
    assert rc == 0
    assert out == b""
    assert IMPORT_JSON.search(err) is not None


def test_no_inotify_anywhere(all_trees):
    """Constraint #4: inotify does not see NFS writes from another host, so a
    watch-based feed would be silent exactly when it matters."""
    imports = conftest.all_imports(*all_trees)
    assert not [name for name in imports if "inotify" in name]


# ---------------------------------------------------------------------------
# stdout, stdin and exit status
# ---------------------------------------------------------------------------

def test_the_hook_writes_nothing_to_stdout_on_any_path(agb, agb_path, statedir,
                                                       run_agb):
    """Constraint #15: Claude Code injects a `UserPromptSubmit` hook's stdout
    straight into the prompt context."""
    sd = str(statedir)
    pid = os.getpid()
    env = _hook_env(sd, HOST, pid)

    rc, out, err = run_agb(["hook", "active"], env=env)      # transition
    assert (rc, out) == (0, b"")
    rc, out, err = run_agb(["hook", "active"], env=env)      # no change
    assert (rc, out) == (0, b"")
    rc, out, err = run_agb(["hook", "nonsense"], env=env)    # rejected
    assert (rc, out) == (0, b"")

    os.chmod(agb.session_dir(sd, HOST), 0o500)               # induced failure
    try:
        rc, out, err = run_agb(["hook", "blocked"], env=env)
        assert (rc, out) == (0, b"")
    finally:
        os.chmod(agb.session_dir(sd, HOST), 0o700)


def test_the_hook_never_reads_stdin(agb, statedir, run_agb):
    """Constraint #16: the payload carries nothing agb needs, and the hot path
    must not block on it. Exiting without reading gives Claude an EPIPE, which
    is expected and harmless -- and is what this test provokes."""
    sd = str(statedir)
    env = _hook_env(sd, HOST, os.getpid())
    payload = b'{"session_id":"x","tool_name":"Bash","junk":"' + b"j" * 300000 + b'"}'
    rc, out, err = run_agb(["hook", "active"], env=env, stdin=payload)
    assert rc == 0
    assert out == b""


def test_the_hook_path_never_references_stdin_or_stdout(all_trees):
    """The structural half: `feed` (Task 3a) legitimately reads stdin, so the
    guard is scoped to the functions the hook can actually reach.

    Over the merged call graph of both files, so that a hook path which grew a
    hop into `agb_mac` -- where `bridge_report` writes to stdout -- would be
    caught here rather than in production."""
    funcs = conftest.functions(*all_trees)
    reachable = set(["cmd_hook"])
    frontier = ["cmd_hook"]
    while frontier:
        node = funcs.get(frontier.pop())
        if node is None:
            continue
        for base, attr in conftest.calls(node):
            name = attr if base is None else None
            if name and name in funcs and name not in reachable:
                reachable.add(name)
                frontier.append(name)

    assert "hook_apply" in reachable and "hook_transition" in reachable
    for name in reachable:
        for child in ast.walk(funcs[name]):
            if isinstance(child, ast.Attribute):
                assert child.attr not in ("stdin", "stdout"), name


def test_the_hook_exits_zero_when_the_statedir_is_unwritable(agb, statedir,
                                                             run_agb):
    sd = str(statedir)
    env = _hook_env(sd, HOST, os.getpid())
    os.chmod(sd, 0o500)
    try:
        rc, out, err = run_agb(["hook", "active"], env=env)
    finally:
        os.chmod(sd, 0o700)
    assert rc == 0
    assert out == b""


def test_the_hook_exits_zero_when_the_statedir_cannot_be_created(agb, tmp_path,
                                                                 run_agb):
    missing = tmp_path / "nope" / "state"
    env = _hook_env(str(missing), HOST, os.getpid())
    os.chmod(str(tmp_path), 0o500)
    try:
        rc, out, err = run_agb(["hook", "active"], env=env)
    finally:
        os.chmod(str(tmp_path), 0o700)
    assert rc == 0
    assert out == b""


# ---------------------------------------------------------------------------
# breadcrumbs
# ---------------------------------------------------------------------------

def test_a_failed_write_leaves_a_breadcrumb(agb, agent):
    """The non-silence thesis: a hook that cannot write must say so somewhere,
    or agb reproduces `agr`'s five stacked silent no-ops."""
    sd = agent
    assert agb.cmd_hook(["active"]) == 0
    ident = ident_of(agb, sd)
    os.chmod(agb.session_dir(sd, HOST), 0o500)
    try:
        assert agb.cmd_hook(["blocked"]) == 0
    finally:
        os.chmod(agb.session_dir(sd, HOST), 0o700)

    text = err_log(agb, sd, ident.key)
    assert "error:" in text
    assert "Permission denied" in text or "EACCES" in text


def test_a_rejected_state_is_breadcrumbed_and_writes_nothing(agb, agent):
    sd = agent
    assert agb.cmd_hook(["unknown"]) == 0
    assert os.listdir(os.path.join(sd, "idx")) == []
    assert agb.list_session_keys(sd, HOST) == []
    assert "not one of" in err_log(agb, sd, None)


def test_a_minted_key_is_breadcrumbed(agb, agent):
    sd = agent
    assert agb.cmd_hook(["active"]) == 0
    ident = ident_of(agb, sd)
    assert "minted key" in err_log(agb, sd, ident.key)
    assert agb.parse_state(read_state_file(agb, sd, ident.key))["state"] == "active"


def test_breadcrumbs_are_per_session_files(agb, agent):
    """NFSv3 O_APPEND is not atomic, so a shared log would be corruptible -- and
    a corruptible breadcrumb undermines the whole point of having one."""
    sd = agent
    agb.breadcrumb(sd, "a3f9c1e0", "one", HOST)
    agb.breadcrumb(sd, "b1b2b3b4", "two", HOST)
    assert sorted(os.listdir(os.path.join(sd, "err"))) == [
        "box2.a3f9c1e0.log", "box2.b1b2b3b4.log"]


def test_breadcrumbs_append(agb, agent):
    sd = agent
    agb.breadcrumb(sd, "a3f9c1e0", "one", HOST)
    agb.breadcrumb(sd, "a3f9c1e0", "two", HOST)
    assert len(err_log(agb, sd, "a3f9c1e0").splitlines()) == 2


def test_breadcrumbs_truncate_and_restart_at_the_limit(agb, agent):
    """Truncate-and-restart rather than rotation: a read-modify-write would
    conflict with the single-os.write discipline."""
    sd = agent
    path = agb.err_log_path(sd, "a3f9c1e0", HOST)
    agb.write_in_place(path, b"x" * (agb.ERR_LOG_LIMIT - 10))
    agb.breadcrumb(sd, "a3f9c1e0", "over the line", HOST)
    text = err_log(agb, sd, "a3f9c1e0")
    assert "over the line" in text
    assert len(text) < agb.ERR_LOG_LIMIT
    assert "x" not in text


def test_breadcrumbs_recreate_a_missing_err_directory(agb, agent):
    sd = agent
    os.rmdir(os.path.join(sd, "err"))
    assert agb.breadcrumb(sd, "a3f9c1e0", "still here", HOST) is True


def test_a_breadcrumb_never_raises(agb, agent):
    """The one call that must not be able to turn a degraded run into a failed
    one."""
    sd = agent
    os.chmod(os.path.join(sd, "err"), 0o500)
    try:
        assert agb.breadcrumb(sd, "a3f9c1e0", "nope", HOST) is False
    finally:
        os.chmod(os.path.join(sd, "err"), 0o700)
    assert agb.breadcrumb(None, "a3f9c1e0", "no statedir", HOST) is False


def test_breadcrumb_lines_are_single_lines(agb, agent):
    sd = agent
    agb.breadcrumb(sd, "a3f9c1e0", "line one\nline two", HOST)
    assert len(err_log(agb, sd, "a3f9c1e0").splitlines()) == 1


# ---------------------------------------------------------------------------
# end to end, through the real `-S -E` invocation the installed hook uses
# ---------------------------------------------------------------------------

def test_hook_end_to_end_through_a_subprocess(agb, statedir, run_agb):
    sd = str(statedir)
    pid = os.getpid()
    env = _hook_env(sd, HOST, pid)

    assert run_agb(["hook", "active"], env=env)[0] == 0
    keys = agb.list_session_keys(sd, HOST)
    assert len(keys) == 1
    key = keys[0]
    assert agb.parse_marker(read_marker(agb, sd)) == [key]
    assert agb.parse_state(read_state_file(agb, sd, key))["state"] == "active"

    assert run_agb(["hook", "completed"], env=env)[0] == 0
    parsed = agb.parse_state(read_state_file(agb, sd, key))
    assert parsed["state"] == "completed"
    assert parsed["seq"] == 2
    assert read_record_file(agb, sd, key)["state"] == "completed"


def test_the_state_file_is_written_in_place(agb, agent):
    """Its mtime is the data, so a rename() -- which would make the mtime
    uncontrollable and the inode churn -- is the wrong discipline for it."""
    sd = agent
    ident = ident_of(agb, sd)
    agb.hook_apply(sd, ident, "active")
    path = agb.state_path(sd, ident.key, HOST)
    inode = os.stat(path).st_ino

    agb.hook_apply(sd, ident_of(agb, sd), "blocked")
    assert os.stat(path).st_ino == inode


def test_the_record_is_written_by_rename(agb, agent):
    """Torn reads matter for the record, and its mtime does not."""
    sd = agent
    ident = ident_of(agb, sd)
    agb.hook_apply(sd, ident, "active")
    path = agb.record_path(sd, ident.key, HOST)
    inode = os.stat(path).st_ino

    agb.hook_apply(sd, ident_of(agb, sd), "blocked")
    assert os.stat(path).st_ino != inode


def test_session_files_are_not_group_readable(agb, agent):
    sd = agent
    ident = ident_of(agb, sd)
    agb.hook_apply(sd, ident, "active")
    for path in (agb.state_path(sd, ident.key, HOST),
                 agb.record_path(sd, ident.key, HOST),
                 agb.marker_path(sd, HOST)):
        assert stat.S_IMODE(os.stat(path).st_mode) == agb.FILE_MODE
