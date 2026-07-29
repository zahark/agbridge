"""Task 6a -- `agb doctor`: probes, not existence checks.

Two claims are worth more than the rest of this file put together.

**It probes.** `agr` failed silently in five stacked places and every one of
them would have passed an existence check: a socket file that existed and was
dead, a tunnel that was up and forwarding nothing. So the tests here refuse to
accept presence as an answer -- the atomicity probe must really rename a file
and read it back, the entry report must carry an *age*, the skew measurement
must compare the server's mtime against the writer's own field rather than
asking whether anything is future-dated.

**It never claims proof.** The entries `doctor` lists as *unadjudicable* are
entries on a host this machine cannot speak for. That is an age heuristic. On
machine #3 there is no feed, so the only beat source is hooks -- and a `blocked`
agent waiting for you fires none, which means its beat freezes while it is
perfectly alive. Naming that "orphaned" or "provably dead" is exactly what would
invite a blanket `--force` in Task 6b, so the wording is pinned by tests rather
than left to a reviewer's memory.

The third section is the split: `doctor` lives in `agb_ops`, a third file the
hook never opens. That is tested the way Task 4c tested `agb_mac` -- by making
the file unreadable and running a hook against it.
"""

import ast
import errno
import json
import os
import shutil
import subprocess
import sys
import time

import pytest

import conftest


HOST = "box2"
FOREIGN = "box3"           # "machine #3" from box #2's point of view
MAC = "mac-abc123"

LIVE_PID, LIVE_START = conftest.live_agent()

NOW = 1785000000.0

REAL_MOUNTS = """\
/dev/sdc1 / xfs rw,relatime,attr2,inode64,noquota 0 0
nfs01:/vol/home /home nfs rw,relatime,vers=3,rsize=65536,hard,proto=tcp,\
timeo=600,retrans=10,sec=sys,local_lock=none 0 0
nfs02:/vol/shared /shared nfs rw,relatime,vers=3,rsize=65536,hard,proto=tcp,\
timeo=600,retrans=10,sec=sys,local_lock=none 0 0
nfs03:/vol/tools /home/tools nfs ro,relatime,vers=3,actimeo=3600,hard,\
proto=tcp,timeo=600,retrans=10 0 0
"""


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def sd(agb, statedir, set_host):
    set_host(HOST)
    return str(statedir)


def write_session(agb, sd, host, key, state="active", seq=1, pid=None,
                  starttime=None, updated=None, cwd="/shared/work/task",
                  pane="%24", label=None):
    """One session, written the way a hook writes it: `.json`, `.state`, marker."""
    if pid is None:
        pid = LIVE_PID
    if starttime is None:
        starttime = LIVE_START
    agb.ensure_session_dir(sd, host)
    record = {
        "v": 1, "key": key, "label": label or ("lbl-" + key), "host": host,
        "pid": pid, "starttime": starttime, "tmux": "sess", "pane": pane,
        "cwd": cwd, "state": state, "seq": seq,
        "updated": NOW if updated is None else updated,
    }
    agb.atomic_write(agb.record_path(sd, key, host),
                     json.dumps(record, sort_keys=True) + "\n")
    agb.write_in_place(agb.state_path(sd, key, host),
                       agb.format_state(state, host, pid, starttime, seq))
    agb.rebuild_marker(sd, host)
    return key


def set_mtime(path, when):
    """Stamp a file's mtime explicitly.

    Only a *test* may do this: the tool always uses `os.utime(path, None)` so
    the NFS server stamps the file (constraint #12). Here it is how a
    server-stamped mtime that disagrees with the writer's clock is simulated at
    all.
    """
    os.utime(path, (when, when))
    return when


def touch_sweep_marker(agb, sd, host, when):
    path = agb.sweep_marker_path(sd, host)
    agb.write_in_place(path, "x\n")
    return set_mtime(path, when)


def touch_bridge_beat(agb, sd, mac_id, when):
    path = agb.bridge_beat_path(sd, mac_id)
    agb.write_in_place(path, "x\n")
    return set_mtime(path, when)


def probe_named(probes, name):
    for probe in probes:
        if probe.name == name:
            return probe
    raise AssertionError("no probe named %r in %s"
                         % (name, [p.name for p in probes]))


def probe_text(probe):
    return "\n".join([probe.summary] + probe.lines)


# ---------------------------------------------------------------------------
# it probes: atomicity for real
# ---------------------------------------------------------------------------

def test_the_atomicity_probe_renames_a_real_file_and_reads_it_back(agb, ops, sd,
                                                                   monkeypatch):
    """Not `os.access`, not "is the directory there": a write, a rename and a
    byte-for-byte read-back. Every silent failure this tool exists to kill was a
    mode-bit answer that did not survive contact with the filesystem."""
    renames = []
    real_rename = os.rename
    monkeypatch.setattr(os, "rename", lambda a, b: (renames.append((a, b)),
                                                    real_rename(a, b))[1])
    probe, now = ops.probe_atomicity(sd)
    assert probe.status == ops.PROBE_OK, probe_text(probe)
    assert len(renames) == 1
    source, target = renames[0]
    assert agb.TEMP_INFIX in source
    assert target.endswith(ops.PROBE_BASENAME % (HOST,))
    assert os.path.dirname(source) == os.path.dirname(target)   # same-dir temp
    assert now is not None


def test_the_atomicity_probe_leaves_the_statedir_exactly_as_it_found_it(
        agb, ops, sd):
    """A diagnostic that leaves debris behind is one more thing to clean up by
    hand -- and `gen/` is the directory the feed enumerates hosts from."""
    before = sorted(os.listdir(agb.gen_dir(sd)))
    probe, _now = ops.probe_atomicity(sd)
    assert probe.status == ops.PROBE_OK
    assert sorted(os.listdir(agb.gen_dir(sd))) == before


def test_the_leftover_temp_is_looked_for_with_an_open_not_a_stat(agb, ops, sd,
                                                                 monkeypatch):
    """⚠️ `os.path.exists` is `os.stat`, and over NFS its answer comes out of
    the attribute cache -- inside the ONE probe whose entire job is proving
    temp+rename works over NFS. `agb.read_fresh`'s docstring states the rule
    ("never os.stat/os.scandir") and `agb._stat_fresh` exists to keep it.

    Reproduced by making the two halves disagree the way NFS can: `rename` is
    replaced by a copy that leaves the source behind (the failure being probed)
    while `os.path.exists` keeps answering False (the stale cache). A probe that
    stats reports `[ok]` on a filesystem where rename does not rename."""
    def copy_not_rename(source, target):
        with open(source, "rb") as handle:
            data = handle.read()
        with open(target, "wb") as handle:
            handle.write(data)
        # `source` is deliberately left in place.

    monkeypatch.setattr(os, "rename", copy_not_rename)
    monkeypatch.setattr(os.path, "exists", lambda path: False)

    probe, now = ops.probe_atomicity(sd)

    assert probe.status == ops.PROBE_FAIL, probe_text(probe)
    assert "left the temp" in probe.summary
    assert now is None
    # ...and it still cleaned up after itself.
    assert sorted(os.listdir(agb.gen_dir(sd))) == []


def test_the_probe_file_can_never_be_mistaken_for_a_host(agb, ops, sd):
    """`list_marker_hosts` reads `gen/`, so the probe's name must not end in
    `.marker`. If it did, a doctor run would invent a host on the wire."""
    assert not (ops.PROBE_BASENAME % (HOST,)).endswith(agb.SUFFIX_MARKER)
    agb.write_in_place(os.path.join(agb.gen_dir(sd),
                                    ops.PROBE_BASENAME % (HOST,)), "x\n")
    assert agb.list_marker_hosts(sd) == []


def test_the_now_used_for_every_age_is_the_probe_files_server_mtime(agb, ops,
                                                                    sd,
                                                                    monkeypatch):
    """Constraint #12, applied to `doctor`: the feed takes its `now` from the
    `fstat` of the beat it just wrote, and this does the same with the probe
    file. A `time.time()` here would make every age a cross-clock subtraction
    between this host and whoever wrote the entry."""
    real = agb.read_fresh

    class Stamped(object):
        st_mtime = 424242.0

    def stamped_read(path, *args, **kwargs):
        data, _st = real(path, *args, **kwargs)
        return (data, Stamped())

    monkeypatch.setattr(agb, "read_fresh", stamped_read)
    _probe, now = ops.probe_atomicity(sd)
    assert now == 424242.0


def test_an_unwritable_statedir_is_reported_rather_than_raised(agb, ops, sd):
    """The error case the plan names. `doctor` is what somebody runs *because*
    something is broken, so a traceback out of probe three would hide probes
    four through nine."""
    os.chmod(agb.gen_dir(sd), 0o500)
    try:
        probe, now = ops.probe_atomicity(sd)
    finally:
        os.chmod(agb.gen_dir(sd), 0o700)
    assert probe.status == ops.PROBE_FAIL
    assert agb.gen_dir(sd) in probe.summary
    assert "Errno" in probe.summary
    assert now is None


def test_a_statedir_that_does_not_exist_yet_is_a_warning_not_a_failure(agb, ops,
                                                                        sd):
    """A fresh farm host has run no agent, so there is no tree to write into --
    and `install.sh farm` prints `agb doctor` as its documented next step. It
    reported `[fail]` and exited 1 on a correct install, two lines after the
    statedir probe had called the same fact a warning."""
    missing = sd + "-nonexistent"
    probe, now = ops.probe_atomicity(missing)
    assert probe.status == ops.PROBE_WARN
    assert "does not exist yet" in probe.summary
    assert now is None


def test_a_gen_that_exists_and_is_unusable_is_a_failure_not_a_warning(agb, ops,
                                                                        sd):
    """⚠️ The regression the degrade shipped with, and the one case no test
    covered. Written as `os.path.isdir(gen)`, the degrade swallowed EVERY
    `os.stat` failure -- ENOTDIR, EACCES, ESTALE, EIO, ELOOP -- and reported a
    `gen/` that exists and cannot be written as "does not exist yet": a false
    sentence, at `warn`, from the probe whose entire job is to catch exactly
    that. Here `gen` is a regular file, so no marker can ever be written and the
    farm is invisible to the feed; `doctor` called it healthy and exited 0."""
    gen = agb.gen_dir(sd)
    shutil.rmtree(gen)
    with open(gen, "w") as handle:
        handle.write("not a directory\n")

    probe, now = ops.probe_atomicity(sd)

    assert probe.status == ops.PROBE_FAIL
    assert "does not exist yet" not in probe.summary
    assert gen in probe.summary
    assert "Errno" in probe.summary          # the real one, not a guess
    assert now is None


def test_doctor_exits_one_when_gen_exists_and_cannot_be_written(agb, sd,
                                                                 run_agb):
    """End to end, because that is where it mattered: `doctor` is what
    `install.sh farm` prints as the next step and the tool's own diagnostic
    authority, and it reported a genuinely broken statedir as warnings."""
    gen = agb.gen_dir(sd)
    shutil.rmtree(gen)
    with open(gen, "w") as handle:
        handle.write("not a directory\n")

    rc, out, err = run_agb(["doctor", "--statedir", sd])

    assert rc == 1, err
    assert b"[fail]" in out
    assert b"does not exist yet" not in out


def test_the_doctor_does_not_create_the_statedir_it_is_reporting_on(agb, ops,
                                                                     sd, tmp_path):
    """It reports; it does not repair. Creating the tree would silently paper
    over the misconfiguration `probe_statedir` exists to surface -- and
    `doctor --statedir /typo` would leave a `/typo` behind on every run."""
    missing = str(tmp_path / "never-created")
    ops.probe_atomicity(missing)
    ops.doctor_probes(missing)
    assert not os.path.exists(missing)


def test_a_fresh_install_reports_no_failure_and_exits_zero(agb, ops, sd,
                                                            tmp_path, run_agb):
    missing = str(tmp_path / "not-yet")
    rc, out, err = run_agb(["doctor", "--statedir", missing])
    assert rc == 0, err
    assert b"[fail]" not in out
    assert b"atomicity" in out
    assert not os.path.exists(missing)


def test_a_readback_that_returns_different_bytes_is_a_failure(agb, ops, sd,
                                                              monkeypatch):
    """⚠️ The probe's whole reason to exist, and it was untested: replacing
    `if data != payload.encode("utf-8"):` with `if False:` passed the entire
    suite. A filesystem that accepts the write and hands back different bytes
    on read-back is precisely the close-to-open failure this probe is for, and
    with that regression it reports `atomicity: ok` -- after which `agb prune`,
    which gates on this probe before offering any deletion, proceeds against a
    statedir that is silently corrupting data."""
    real = agb.read_fresh

    def corrupt(path, *args, **kwargs):
        _data, st = real(path, *args, **kwargs)
        return (b"not what we wrote\n", st)

    monkeypatch.setattr(agb, "read_fresh", corrupt)
    probe, now = ops.probe_atomicity(sd)
    assert probe.status == ops.PROBE_FAIL
    assert "byte for byte" in probe.summary
    assert now is None


def test_a_rename_that_leaves_its_temp_behind_is_a_failure(agb, ops, sd,
                                                           monkeypatch):
    """The second detection, equally undetected: neutering `if
    os.path.exists(tmp):` also passed everything. A `rename()` that copies
    instead of moving is not atomic, and the temp still sitting there is the
    evidence. It must be reaped as well as reported -- `gen/` is the directory
    the feed enumerates hosts from."""
    real_rename = os.rename

    def copying_rename(source, target):
        real_rename(source, target)
        with open(target, "rb") as handle:
            data = handle.read()
        with open(source, "wb") as handle:          # put the "temp" back
            handle.write(data)

    monkeypatch.setattr(os, "rename", copying_rename)
    probe, now = ops.probe_atomicity(sd)
    monkeypatch.undo()

    assert probe.status == ops.PROBE_FAIL
    assert "left the temp" in probe.summary
    assert now is None
    assert [name for name in os.listdir(agb.gen_dir(sd))
            if agb.TEMP_INFIX in name] == []


def test_a_probe_that_raises_becomes_a_reported_failure(ops, monkeypatch):
    def explode():
        raise OSError(errno.EIO, "the server is sulking")

    probe = ops._probe_guard("mount", explode)
    assert probe.status == ops.PROBE_FAIL
    assert "sulking" in probe.summary


# ---------------------------------------------------------------------------
# it reports age, never presence
# ---------------------------------------------------------------------------

def test_own_entries_are_reported_by_age_not_by_presence(agb, ops, sd):
    key = write_session(agb, sd, HOST, "aaaa1111", state="blocked")
    set_mtime(agb.state_path(sd, key, HOST), NOW - 1800)

    probe = ops.probe_own_entries(sd, NOW, HOST)
    text = probe_text(probe)
    assert key in text
    assert "blocked" in text
    assert "30 min" in text          # the age, not "present"
    assert "oldest beat 30 min" in probe.summary


def test_a_torn_state_is_no_information_and_never_reads_as_gone(agb, ops, sd):
    """The truncate window, at the diagnostic. A peer mid-`O_TRUNC` leaves a
    zero-length `.state`; `doctor` must say it could not tell, not that the
    session ended."""
    key = write_session(agb, sd, HOST, "aaaa1111")
    agb.write_in_place(agb.state_path(sd, key, HOST), "")

    probe = ops.probe_own_entries(sd, NOW, HOST)
    text = probe_text(probe).lower()
    assert "no information" in text
    assert "gone" not in text
    assert probe.status == ops.PROBE_WARN


def test_no_sessions_is_said_plainly(agb, ops, sd):
    probe = ops.probe_own_entries(sd, NOW, HOST)
    assert probe.status == ops.PROBE_OK
    assert "no sessions" in probe.summary


# ---------------------------------------------------------------------------
# is the Mac actually consuming?
# ---------------------------------------------------------------------------

def test_a_fresh_bridge_beat_is_reported_as_fresh(agb, ops, sd):
    touch_bridge_beat(agb, sd, MAC, NOW - 3)
    probe = ops.probe_bridge_beats(sd, NOW, MAC)
    assert probe.status == ops.PROBE_OK
    assert "STALE" not in probe_text(probe)
    assert "3 s old" in probe_text(probe)


def test_a_stale_bridge_beat_is_reported_as_stale(agb, ops, sd):
    """The plan's own test. The beat is touched once per feed poll, so an old
    one means nothing is consuming -- the only thing the farm side can say
    about the Mac at all."""
    touch_bridge_beat(agb, sd, MAC, NOW - 600)
    probe = ops.probe_bridge_beats(sd, NOW, MAC)
    assert probe.status == ops.PROBE_WARN
    text = probe_text(probe)
    assert "STALE" in text
    assert "10 min" in text
    assert MAC in text


def test_a_configured_mac_id_with_no_beat_at_all_is_named(agb, ops, sd):
    """Distinct from stale: nothing has ever connected under this id. Reported
    rather than silently absent, because a mac-id typo is otherwise invisible."""
    touch_bridge_beat(agb, sd, "some-other-mac", NOW - 1)
    probe = ops.probe_bridge_beats(sd, NOW, MAC)
    assert probe.status == ops.PROBE_WARN
    assert "no beat" in probe.summary and MAC in probe.summary


def test_no_bridge_beat_at_all_is_reported(agb, ops, sd):
    probe = ops.probe_bridge_beats(sd, NOW, None)
    assert probe.status == ops.PROBE_WARN
    assert "nothing is consuming the feed" in probe.summary


# ---------------------------------------------------------------------------
# clock skew, measured rather than assumed
# ---------------------------------------------------------------------------

def test_skew_is_detected_by_comparing_the_mtime_against_the_updated_field(
        agb, ops, sd):
    """The plan's second named test, and the reason it is phrased that way: both
    stamps here are in the **past**, so a "is anything future-dated?" check
    would see nothing at all. The mtime is the NFS server's clock; `updated` is
    the writer's."""
    key = write_session(agb, sd, HOST, "aaaa1111", updated=NOW - 420)
    set_mtime(agb.record_path(sd, key, HOST), NOW - 300)

    probe = ops.probe_clock_skew(sd, NOW)
    assert probe.status == ops.PROBE_WARN
    text = probe_text(probe)
    assert "+120.0 s" in text
    assert HOST in text
    assert "worst 120.0 s" in probe.summary


def test_a_host_whose_clocks_agree_is_not_flagged(agb, ops, sd):
    key = write_session(agb, sd, HOST, "aaaa1111", updated=NOW - 300)
    set_mtime(agb.record_path(sd, key, HOST), NOW - 300)

    probe = ops.probe_clock_skew(sd, NOW)
    assert probe.status == ops.PROBE_OK
    assert "worst 0.0 s" in probe.summary


def test_skew_is_measured_for_a_foreign_host_too(agb, ops, sd):
    """Skew *between* hosts is the failure this catches: box #2 and machine #3
    stamping `updated` from clocks 3 minutes apart makes every age one side
    reports wrong."""
    key = write_session(agb, sd, FOREIGN, "bbbb2222", updated=NOW - 480)
    set_mtime(agb.record_path(sd, key, FOREIGN), NOW - 300)

    probe = ops.probe_clock_skew(sd, NOW)
    assert FOREIGN in probe_text(probe)
    assert "+180.0 s" in probe_text(probe)


def test_a_record_without_an_updated_field_is_skipped_not_guessed(agb, ops, sd):
    key = write_session(agb, sd, HOST, "aaaa1111")
    agb.atomic_write(agb.record_path(sd, key, HOST),
                     json.dumps({"v": 1, "key": key}) + "\n")
    probe = ops.probe_clock_skew(sd, NOW)
    assert probe.status == ops.PROBE_OK
    assert "no record carried a usable `updated` field" in probe_text(probe)


# ---------------------------------------------------------------------------
# the mount -- the acknowledged, un-engineerable risk
# ---------------------------------------------------------------------------

def test_the_mount_table_parses_into_fields(ops):
    mounts = ops.parse_mount_table(REAL_MOUNTS)
    assert [entry["mountpoint"] for entry in mounts] == [
        "/", "/home", "/shared", "/home/tools"]
    assert mounts[2]["fstype"] == "nfs"
    assert "retrans=10" in mounts[2]["options"]


def test_an_escaped_mountpoint_is_unescaped(ops):
    mounts = ops.parse_mount_table(
        "dev /mnt/with\\040space nfs rw,hard 0 0\n")
    assert mounts[0]["mountpoint"] == "/mnt/with space"


def test_the_most_specific_mount_wins(ops):
    mounts = ops.parse_mount_table(REAL_MOUNTS)
    assert ops.mount_for_path(mounts, "/shared/.agbridge/gen")["mountpoint"] \
        == "/shared"
    assert ops.mount_for_path(mounts, "/home/other")["mountpoint"] == "/home"
    assert ops.mount_for_path(mounts, "/var/tmp")["mountpoint"] == "/"
    # ...and a prefix that is not a path component boundary is not a match:
    # `/shared2` must not match the `/shared` mount, so it falls all the way
    # back to `/`. A raw-prefix implementation would answer `/shared` here.
    assert ops.mount_for_path(mounts, "/shared2")["mountpoint"] == "/"


def test_the_mount_probe_prints_the_options_that_explain_a_freeze(ops):
    """The plan's "acknowledged, un-engineerable risk": a hard mount with no
    `intr` blocks uninterruptibly, `signal.alarm` cannot break it, and every
    Claude tool call traverses this path. Printing the options is how "Claude
    froze" gets diagnosed in seconds instead of blamed on agb."""
    probe = ops.probe_mount("/shared/.agbridge", REAL_MOUNTS)
    text = probe_text(probe)
    assert "hard" in probe.summary
    assert "timeo=600" in probe.summary and "retrans=10" in probe.summary
    assert "uninterruptibly" in text
    assert "acdirmax=60" in text          # the ac* defaults that apply
    assert "gen/<host>.marker" in text    # ...and why discovery works as it does


def test_an_explicit_actimeo_suppresses_the_defaults_note(ops):
    """Negative control for the note above: `/home/tools` sets `actimeo=3600`
    explicitly, which is what proves the `ac*` tuning on this box is deliberate
    -- so claiming the kernel defaults apply there would be wrong."""
    probe = ops.probe_mount("/home/tools/bin", REAL_MOUNTS)
    text = probe_text(probe)
    assert "actimeo=3600" in probe.summary
    assert "the kernel defaults apply" not in text


def test_the_real_mount_table_is_readable_and_covers_the_statedir(ops, sd):
    """The synthetic table above proves the parser; this proves the source."""
    text = ops.read_mount_table()
    assert text is not None and "/" in text
    probe = ops.probe_mount(sd)
    assert probe.status == ops.PROBE_OK
    assert probe.lines and probe.lines[0].startswith("all options: ")


def test_an_unreadable_mount_table_is_a_warning_not_a_crash(ops, monkeypatch):
    monkeypatch.setattr(ops, "MOUNTS", "/nonexistent/mounts")
    probe = ops.probe_mount("/tmp")
    assert probe.status == ops.PROBE_WARN
    assert "cannot read" in probe.summary


# ---------------------------------------------------------------------------
# statedir ownership and mode
# ---------------------------------------------------------------------------

def test_the_statedir_probe_reports_ownership_and_mode(agb, ops, sd):
    probe = ops.probe_statedir(sd)
    assert probe.status == ops.PROBE_OK
    text = probe_text(probe)
    assert "0700" in text
    assert "uid %d" % (os.getuid(),) in text
    for name in agb.SUBDIRS:
        assert name in text


def test_a_group_writable_statedir_fails_the_probe(agb, ops, sd):
    """The parent is group-writable, so another member could have pre-created
    the directory. Existence is not ownership."""
    os.chmod(sd, 0o770)
    try:
        probe = ops.probe_statedir(sd)
    finally:
        os.chmod(sd, 0o700)
    assert probe.status == ops.PROBE_FAIL
    assert "0770" in probe.summary


def test_a_missing_statedir_is_a_warning_with_the_reason(ops, tmp_path):
    probe = ops.probe_statedir(str(tmp_path / "nope"))
    assert probe.status == ops.PROBE_WARN
    assert "does not exist yet" in probe.summary


def test_a_missing_subdirectory_is_reported(agb, ops, sd):
    os.rmdir(os.path.join(sd, "sweep"))
    probe = ops.probe_statedir(sd)
    assert probe.status == ops.PROBE_WARN
    assert "missing subdirectories: sweep" in probe_text(probe)


# ---------------------------------------------------------------------------
# breadcrumbs
# ---------------------------------------------------------------------------

def test_the_breadcrumb_tails_are_printed(agb, ops, sd):
    for index in range(5):
        agb.breadcrumb(sd, "aaaa1111", "line %d" % (index,), HOST)
    probe = ops.probe_breadcrumbs(sd, tail=2)
    text = probe_text(probe)
    assert "box2.aaaa1111.log" in text
    assert "line 4" in text and "line 3" in text
    assert "line 2" not in text


def test_every_hosts_breadcrumbs_are_shown(agb, ops, sd):
    """The point of a breadcrumb is that somebody *else* can read it: machine
    #3's log is exactly the one nobody can otherwise reach."""
    agb.breadcrumb(sd, "bbbb2222", "from machine 3", FOREIGN)
    assert "from machine 3" in probe_text(ops.probe_breadcrumbs(sd))


def test_no_breadcrumbs_is_not_an_error(ops, sd):
    probe = ops.probe_breadcrumbs(sd)
    assert probe.status == ops.PROBE_OK
    assert "no breadcrumbs" in probe.summary


def test_an_unreadable_breadcrumb_is_a_warning_not_an_ok(agb, ops, sd):
    """A breadcrumb nobody can read is the silent no-op this probe exists to
    surface, so it may not be reported under `[ok]` -- the one status a reader
    skips. Every sibling probe escalates on the same class of event
    (`probe_own_entries` returns WARN on an unreadable entry, `probe_config` on
    a malformed line, `probe_statedir` on a missing subdirectory), and `doctor`
    exits 1 only on `fail`, so the warning costs nothing but attention."""
    agb.breadcrumb(sd, "aaaa1111", "readable", HOST)
    agb.breadcrumb(sd, "cccc3333", "unreadable", HOST)
    victim = os.path.join(agb.err_dir(sd), "%s.cccc3333.log" % (HOST,))
    os.chmod(victim, 0o000)
    try:
        probe = ops.probe_breadcrumbs(sd)
    finally:
        os.chmod(victim, 0o600)

    assert probe.status == ops.PROBE_WARN, probe_text(probe)
    assert "1 unreadable" in probe.summary
    assert "cccc3333.log: unreadable" in probe_text(probe)
    assert "readable" in probe_text(probe)          # the good one still shown


def test_readable_breadcrumbs_alone_stay_ok(agb, ops, sd):
    """Non-vacuity for the test above: the WARN is about the unreadable file,
    not about there being breadcrumbs at all."""
    agb.breadcrumb(sd, "aaaa1111", "line", HOST)
    probe = ops.probe_breadcrumbs(sd)
    assert probe.status == ops.PROBE_OK
    assert "unreadable" not in probe.summary


# ---------------------------------------------------------------------------
# unadjudicable entries -- listed, and labelled as a heuristic
# ---------------------------------------------------------------------------

def test_a_quiet_foreign_hosts_entries_are_listed_as_unadjudicable(agb, ops,
                                                                    sd):
    key = write_session(agb, sd, FOREIGN, "bbbb2222", state="active",
                        cwd="/shared/work/proj", pane="%7")
    touch_sweep_marker(agb, sd, FOREIGN, NOW - 3600)
    set_mtime(agb.state_path(sd, key, FOREIGN), NOW - 3600)

    entries = ops.unadjudicable_entries(sd, NOW)
    assert [(e["host"], e["key"]) for e in entries] == [(FOREIGN, key)]
    entry = entries[0]
    # Everything Task 6b must display before it removes one.
    assert entry["state"] == "active"
    assert entry["cwd"] == "/shared/work/proj"
    assert entry["pane"] == "%7"
    assert abs(entry["beat_age"] - 3600) < 1
    assert abs(entry["quiet_age"] - 3600) < 1


def test_our_own_entries_are_never_unadjudicable(agb, ops, sd):
    """We can `kill(pid, 0)` here, so an own-host entry always has a real
    answer available -- the sweep's, not a heuristic's. Listing one would
    invite `prune` to remove something this machine can adjudicate."""
    write_session(agb, sd, HOST, "aaaa1111")
    touch_sweep_marker(agb, sd, HOST, NOW - 86400)
    assert ops.unadjudicable_entries(sd, NOW) == []


def test_a_host_that_swept_recently_is_not_quiet(agb, ops, sd):
    write_session(agb, sd, FOREIGN, "bbbb2222")
    touch_sweep_marker(agb, sd, FOREIGN, NOW - 30)
    assert ops.unadjudicable_entries(sd, NOW) == []


def test_a_host_with_no_sweep_marker_at_all_counts_as_quiet(agb, ops, sd):
    """A host whose hooks have never completed a transition has no marker. It is
    the least adjudicable case there is, so treating "absent" as "recent" would
    hide exactly the entries this list exists for."""
    write_session(agb, sd, FOREIGN, "bbbb2222")
    entries = ops.unadjudicable_entries(sd, NOW)
    assert [e["key"] for e in entries] == ["bbbb2222"]
    assert entries[0]["quiet_age"] is None
    assert "absent" in probe_text(ops.probe_unadjudicable(sd, NOW))


def test_the_quiet_threshold_is_the_sweep_markers_mtime(agb, ops, sd):
    """Named in the plan because the three candidate signals disagree: `.state`
    mtimes answer a per-entry question, the `gen/` marker is rewritten by any
    transition on any key, and this one says when that host last swept."""
    key = write_session(agb, sd, FOREIGN, "bbbb2222")
    touch_sweep_marker(agb, sd, FOREIGN, NOW - 300)
    set_mtime(agb.state_path(sd, key, FOREIGN), NOW - 86400)   # ancient beat

    assert ops.unadjudicable_entries(sd, NOW, quiet_after=600) == []
    assert len(ops.unadjudicable_entries(sd, NOW, quiet_after=60)) == 1


def test_the_list_is_labelled_unadjudicable_and_never_orphaned(agb, ops, sd):
    """The wording is the safety feature. `prune` is the only destructive
    command in the tool and it operates on exactly this list -- calling it
    "orphaned" or "provably dead" is what would make a blanket `--force` look
    reasonable, which is amendment 1's withdrawn rule relocated into a
    destructive command."""
    write_session(agb, sd, FOREIGN, "bbbb2222")
    probe = ops.probe_unadjudicable(sd, NOW)
    text = probe_text(probe).lower()
    assert "unadjudicable" in text
    assert "an age heuristic, not proof of death" in text
    assert "orphan" not in text
    assert "provable" not in text and "provably" not in text
    assert probe.status == ops.PROBE_WARN     # a question, never a verdict


def test_a_blocked_entry_carries_the_live_agent_warning(agb, ops, sd):
    """The specific way this heuristic is wrong: on #3 there is no feed, so a
    `blocked` agent waiting on you beats nothing while being perfectly alive."""
    write_session(agb, sd, FOREIGN, "bbbb2222", state="blocked")
    text = probe_text(ops.probe_unadjudicable(sd, NOW))
    assert "may be a live agent waiting for input" in text


def test_nothing_unadjudicable_says_so_rather_than_staying_silent(ops, sd):
    probe = ops.probe_unadjudicable(sd, NOW)
    assert probe.status == ops.PROBE_OK
    assert "no unadjudicable entries" in probe.summary


def test_the_foreign_key_list_comes_from_the_marker_not_from_readdir(
        agb, ops, sd, monkeypatch):
    """Constraint #5. `readdir` of another host's directory can be served from
    cache for up to `acdirmax=60` s, so a listing here would silently drop a key
    that host created a minute ago -- and Task 6b would then prune a live
    agent's entry. The listing is stubbed stale; the entry must still be found.
    """
    key = write_session(agb, sd, FOREIGN, "bbbb2222")
    seen = []
    real = os.listdir
    stale = os.path.join("sessions", FOREIGN)

    def spy(path):
        seen.append(str(path))
        if str(path).endswith(stale):
            return []                      # a cached, out-of-date listing
        return real(path)

    monkeypatch.setattr(os, "listdir", spy)
    entries = ops.unadjudicable_entries(sd, NOW)
    assert [e["key"] for e in entries] == [key]
    assert not [path for path in seen if path.endswith(stale)]


def test_a_marker_that_fails_validation_yields_no_entries_rather_than_all(
        agb, ops, sd):
    """Constraint #8 at the diagnostic: a torn marker is *no information*, and
    a `doctor` that turned it into "every key on that host is unadjudicable"
    would hand Task 6b a list of live agents."""
    write_session(agb, sd, FOREIGN, "bbbb2222")
    agb.atomic_write(agb.marker_path(sd, FOREIGN), "bbbb2222\n")   # no #end
    assert ops.unadjudicable_entries(sd, NOW) == []


# ---------------------------------------------------------------------------
# assembly, status and the command
# ---------------------------------------------------------------------------

def test_every_probe_runs_and_the_order_is_the_reading_order(agb, ops, sd):
    probes, now = ops.doctor_probes(sd, mounts=REAL_MOUNTS, now=NOW)
    assert [probe.name for probe in probes] == [
        "config", "statedir", "atomicity", "mount", "own entries",
        "bridge beat", "clock skew", ops.UNADJUDICABLE, "breadcrumbs"]
    assert now == NOW


def test_the_probe_supplies_now_when_the_caller_does_not(agb, ops, sd):
    probes, now = ops.doctor_probes(sd, mounts=REAL_MOUNTS)
    assert abs(now - time.time()) < 60
    assert "clock" not in [probe.name for probe in probes]


def test_a_failed_write_probe_says_the_ages_are_local(agb, ops, sd):
    os.chmod(agb.gen_dir(sd), 0o500)
    try:
        probes, _now = ops.doctor_probes(sd, mounts=REAL_MOUNTS)
    finally:
        os.chmod(agb.gen_dir(sd), 0o700)
    clock = probe_named(probes, "clock")
    assert clock.status == ops.PROBE_WARN
    assert "this host's clock" in clock.summary


def test_the_status_is_the_worst_answer_any_probe_gave(ops):
    ok = ops.Probe("a", ops.PROBE_OK, "")
    warn = ops.Probe("b", ops.PROBE_WARN, "")
    fail = ops.Probe("c", ops.PROBE_FAIL, "")
    assert ops.doctor_status([ok, ok]) == ops.PROBE_OK
    assert ops.doctor_status([ok, warn]) == ops.PROBE_WARN
    assert ops.doctor_status([warn, fail, ok]) == ops.PROBE_FAIL


def test_config_warnings_and_unknown_keys_are_surfaced(ops, config_file):
    """Task 1 kept `CONFIG_WARNINGS` for exactly this. A silently skipped
    `feed_hsot` is a typo that produces a *different* silent failure later."""
    config_file("mac_id = m1\nnonsense line\nfeed_hsot = box2\n")
    probe, values = ops.probe_config()
    assert values["mac_id"] == "m1"
    text = probe_text(probe)
    assert "nonsense line" in text
    assert "feed_hsot" in text
    assert probe.status == ops.PROBE_WARN


def test_the_mac_id_comes_from_the_config_when_not_given(agb, ops, sd,
                                                          config_file):
    config_file("mac_id = %s\n" % (MAC,))
    probes, _now = ops.doctor_probes(sd, mounts=REAL_MOUNTS, now=NOW)
    assert MAC in probe_text(probe_named(probes, "bridge beat"))


def test_the_argument_parser_accepts_both_option_forms(ops):
    opts = ops.parse_doctor_args(["--statedir", "/tmp/x", "--mac-id=" + MAC,
                                  "--quiet-after", "30", "--tail=1"])
    assert opts == {"statedir": "/tmp/x", "mac_id": MAC,
                    "quiet_after": 30.0, "tail": 1}


@pytest.mark.parametrize("argv", [
    ["--nonsense"],
    ["--tail"],
    ["--quiet-after", "soon"],
    ["--mac-id", "../escape"],
    ["extra"],
])
def test_bad_arguments_raise_a_described_error(ops, agb, argv):
    with pytest.raises(agb.AgbError):
        ops.parse_doctor_args(argv)


def test_doctor_runs_end_to_end_and_exits_zero_on_a_healthy_tree(agb, sd,
                                                                  run_agb):
    write_session(agb, sd, HOST, "aaaa1111")
    touch_bridge_beat(agb, sd, MAC, time.time())
    rc, out, err = run_agb(["doctor", "--statedir", sd, "--mac-id", MAC])
    assert rc == 0, err
    text = out.decode()
    for name in ("config:", "statedir:", "atomicity:", "mount:", "own entries:",
                 "bridge beat:", "clock skew:", "unadjudicable:",
                 "breadcrumbs:"):
        assert name in text, name
    assert "[fail]" not in text
    assert err == b""


def test_warnings_alone_do_not_make_doctor_exit_non_zero(agb, sd, run_agb):
    """An exit status that cries wolf is one nobody reads. A stale beat and an
    unadjudicable entry are questions for an operator, not breakage."""
    write_session(agb, sd, FOREIGN, "bbbb2222")
    touch_bridge_beat(agb, sd, MAC, time.time() - 3600)
    rc, out, err = run_agb(["doctor", "--statedir", sd, "--mac-id", MAC])
    assert rc == 0, err
    assert "[warn]" in out.decode()


def test_an_unwritable_statedir_is_reported_and_the_run_continues(agb, sd,
                                                                   run_agb):
    """The plan's error case, end to end: reported, not crashed on -- and every
    probe after the failing one still runs."""
    os.chmod(sd, 0o500)
    try:
        rc, out, err = run_agb(["doctor", "--statedir", sd])
    finally:
        os.chmod(sd, 0o700)
    text = out.decode()
    assert rc == 1
    assert "[fail]" in text
    assert "mount:" in text and "breadcrumbs:" in text   # it kept going
    assert b"Traceback" not in err


def test_a_bad_option_exits_one_with_a_message(run_agb):
    rc, out, err = run_agb(["doctor", "--nonsense"])
    assert rc == 1
    assert out == b""
    assert b"unknown option" in err


def test_the_rendering_is_greppable(ops):
    probes = [ops.Probe("mount", ops.PROBE_WARN, "summary here", ["detail"])]
    text = ops.format_probes(probes, "header")
    assert text.splitlines() == ["header",
                                 "[warn] mount:         summary here",
                                 "       detail"]


# ---------------------------------------------------------------------------
# the split: `doctor` lives in a third file the hook never opens
# ---------------------------------------------------------------------------

@pytest.fixture
def poisoned_tree(tmp_path, repo_root):
    """A copy of the three files in which `agb_ops` cannot be read.

    Mode 000 rather than a syntax error, for Task 4c's reason: it fails at
    `open()` rather than at exec, so it covers "open, read or compile" in one
    stroke.
    """
    tree = tmp_path / "tree"
    tree.mkdir()
    for name in ("agb", "agb_mac", "agb_ops"):
        shutil.copyfile(os.path.join(repo_root, name), str(tree / name))
    os.chmod(str(tree / "agb_ops"), 0o000)

    class Tree(object):
        agb = str(tree / "agb")
        ops = str(tree / "agb_ops")

        def readable(self, yes=True):
            os.chmod(self.ops, 0o600 if yes else 0o000)

        def run(self, args, env=None, stdin=None):
            environ = dict(os.environ)
            if env:
                environ.update(env)
            proc = subprocess.Popen(
                [sys.executable, "-S", "-E", self.agb] + list(args),
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, env=environ)
            out, err = conftest.communicate(proc, stdin)
            return proc.returncode, out, err

    yield Tree()
    os.chmod(str(tree / "agb_ops"), 0o600)


def seed_hot_path(agb, sd, host=HOST):
    """Pre-seed `idx/` and `.state` so a hook invocation is a genuine no-change
    hot path -- a first-ever hook is a transition, which touches more code."""
    agb.ensure_session_dir(sd, host)
    anchor = agb.Anchor(host, "tmux", 1200000, "%24", pane="%24")
    key = agb.new_key()
    agb.link_idx(agb.idx_path(sd, anchor), key, LIVE_PID, LIVE_START)
    agb.write_in_place(agb.state_path(sd, key, host),
                       agb.format_state("active", host, LIVE_PID, LIVE_START, 1))
    agb.rebuild_marker(sd, host)
    return key


def hook_env(sd, host=HOST):
    return {"AGB_STATEDIR": sd, "AGB_HOST": host,
            "AGB_AGENT_PID": str(LIVE_PID),
            "TMUX": "/tmp/tmux-100000/default,1200000,23", "TMUX_PANE": "%24"}


def test_the_hook_never_opens_the_operator_file(agb, sd, poisoned_tree):
    """Task 6a's half of Task 4c's claim: `doctor` is parsed by every hook and
    run by essentially none, so it must not be in `agb` at all."""
    key = seed_hot_path(agb, sd)
    env = hook_env(sd)

    rc, out, err = poisoned_tree.run(["hook", "active"], env=env)    # no change
    assert (rc, out, err) == (0, b"", b"")
    rc, out, err = poisoned_tree.run(["hook", "blocked"], env=env)   # transition
    assert (rc, out, err) == (0, b"", b"")

    parsed = agb.parse_state(open(agb.state_path(sd, key, HOST), "rb").read())
    assert parsed["state"] == "blocked"
    log = agb.err_log_path(sd, key, HOST)
    assert "error" not in (open(log).read() if os.path.exists(log) else "")


def test_the_poison_is_real(sd, poisoned_tree):
    """Negative control: without it the test above passes against a hook that
    was never going to load anything -- which is to say, vacuously."""
    rc, out, err = poisoned_tree.run(["doctor", "--statedir", sd])
    assert rc != 0
    assert b"agb_ops" in err
    assert os.strerror(errno.EACCES).encode() in err or b"denied" in err.lower()


def test_the_same_tree_works_once_the_file_is_readable(sd, poisoned_tree):
    poisoned_tree.readable(True)
    rc, out, err = poisoned_tree.run(["doctor", "--statedir", sd])
    assert rc == 0, err
    assert b"atomicity:" in out


def test_the_hooks_verbose_import_trace_never_mentions_the_operator_file(
        agb, sd, agb_path):
    """The second, independent probe: `-v` reports every module the interpreter
    imports, on stderr."""
    seed_hot_path(agb, sd)
    environ = dict(os.environ)
    environ.update(hook_env(sd))
    proc = subprocess.Popen([sys.executable, "-S", "-E", "-v", agb_path,
                             "hook", "active"],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, env=environ)
    out, err = conftest.communicate(proc, b"")
    assert (proc.returncode, out) == (0, b"")
    assert b"agb_ops" not in err
    assert b"import 'importlib" not in err


def test_the_operator_loader_is_reached_only_from_its_own_commands(agb_tree,
                                                                    agb):
    """One door -- and after Task 6b it is *one* for all five operator
    commands, not one per command.

    ⚠️ Amended by Task 6b. `cmd_doctor` became `cmd_ops(name, argv)`: Task 6a
    left ~600 bytes under the parse budget, and four more `cmd_*` stubs would
    have spent it on hops rather than on anything a hook uses. Which names go
    through the door is `agb.OPS_COMMANDS`, and which of them are implemented is
    `agb_ops.run_ops`' business.
    """
    funcs = conftest.functions(agb_tree)
    callers = set(name for name, node in funcs.items()
                  if (None, "_load_ops") in conftest.calls(node))
    assert callers == set(["cmd_ops"])
    assert (None, "cmd_ops") in conftest.calls(funcs["main"])
    assert set(agb.OPS_COMMANDS) <= set(conftest.usage_commands(agb))
    assert "hook" not in agb.OPS_COMMANDS and "feed" not in agb.OPS_COMMANDS


def test_no_hook_path_function_can_reach_the_operator_module(all_trees,
                                                              ops_tree):
    """The structural mirror of the poisoned-tree test, across all three files:
    nothing the hook calls, at any depth, reaches `agb_ops`."""
    funcs = conftest.functions(*all_trees)
    reachable = conftest.reachable_from(funcs, "cmd_hook")
    assert "hook_apply" in reachable               # the walk really ran
    assert "_load_ops" not in reachable
    assert "run_doctor" not in reachable
    assert not reachable & set(conftest.functions(ops_tree))


def test_the_command_is_a_one_statement_hop(agb_tree):
    """The byte count notices a Mac-side or operator-side function creeping
    back into `agb` eventually; this notices immediately."""
    funcs = conftest.functions(agb_tree)
    body = [node for node in funcs["cmd_ops"].body
            if not isinstance(node, ast.Expr)]     # drop the docstring
    assert len(body) == 1
    assert isinstance(body[0], ast.Return)


def test_the_doctor_bulk_really_is_in_the_operator_file(ops_source):
    assert len(ops_source) > 15000


def test_both_siblings_load_through_one_loader_and_share_one_module(agb, ops,
                                                                     mac):
    """Two copies of `agb` would mean two `CONFIG_WARNINGS` lists and two
    versions of every rule -- the split's version of the writer and the sweeper
    disagreeing about the hostname."""
    assert agb._load_ops() is ops
    assert ops.agb is agb
    assert sys.modules["agb_ops"] is ops
    assert mac.agb is ops.agb


def test_a_failed_operator_load_is_not_cached_as_a_success(agb, monkeypatch):
    monkeypatch.delitem(sys.modules, "agb_ops", raising=False)
    monkeypatch.setattr(agb, "ops_path", lambda: "/nonexistent/agb_ops")
    with pytest.raises(Exception):
        agb._load_ops()
    assert "agb_ops" not in sys.modules


def test_the_operator_module_is_not_importable_as_a_module(repo_root):
    """Constraint #17: `-S -E` leaves `sys.path[0]` in place, and this file has
    no `.py` extension precisely so nothing can pick it up by name."""
    proc = subprocess.Popen(
        [sys.executable, "-S", "-E", "-c", "import agb_ops"],
        cwd=repo_root, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    _out, err = conftest.communicate(proc)
    assert proc.returncode != 0
    assert b"ImportError" in err or b"ModuleNotFoundError" in err


def test_the_operator_file_is_covered_by_the_stdlib_shadow_guard(repo_root,
                                                                 ops_path):
    """Constraint #17 from the other side: the guard in `tests/test_core.py`
    only covers this file if it is one of the entries that guard walks."""
    import test_core

    assert os.path.basename(ops_path) in os.listdir(repo_root)
    assert not ops_path.endswith(".py")
    assert os.path.basename(ops_path) not in test_core._stdlib_module_names()


def test_the_operator_file_has_no_shebang_and_is_not_executable(ops_source,
                                                                ops_path):
    assert not ops_source.startswith("#!")
    assert not os.access(ops_path, os.X_OK)


# ---------------------------------------------------------------------------
# structural: consumed, never duplicated -- and never destructive
# ---------------------------------------------------------------------------

OPS_CONSUMED = ("own_host", "read_fresh", "read_state_entry",
                "read_marker_keys", "list_marker_hosts", "list_session_keys",
                "_stat_fresh", "_listdir_quiet", "verify_statedir",
                "read_config", "AgbError", "statedir")


def test_the_operator_module_consumes_agbs_primitives(ops_source, ops):
    """It reaches back into `agb` for every shared rule rather than owning a
    second copy. `conftest.functions()` raises on a duplicated definition, so
    this is the positive half: each of these is actually used, qualified."""
    for name in OPS_CONSUMED:
        assert ("agb." + name) in ops_source, name
        assert name not in vars(ops), name


def test_the_operator_module_never_kills_or_adjudicates(ops_tree):
    """`doctor` reports; it never concludes. A `kill(pid, 0)` here would be a
    second liveness rule, and one that answers about the wrong pid namespace
    for every entry it would be used on."""
    funcs = conftest.functions(ops_tree)
    for name, node in funcs.items():
        made = conftest.calls(node)
        assert ("os", "kill") not in made, name
        for _base, attr in made:
            assert attr not in ("proof_of_death", "reap_entry", "sweep_entry",
                                "sweep_host", "liveness"), name


def test_the_operator_module_removes_nothing_but_its_own_probe(ops_tree):
    """`doctor` reports and removes nothing; the only destructive code in this
    file is `prune`'s, and it is one named function.

    ⚠️ Amended by Task 6b, and narrowed rather than widened. `prune_remove` is
    added to the closed set, and everything else in the file -- `doctor`
    included -- is then required not to so much as *name* a session file. A
    diagnostic that grew a `state_path(...)` unlink fails on the second
    assertion even though the first has room for it, and `prune`'s own rules
    (per-entry consent, proof of life, no foreign `readdir`) are asserted in
    `tests/test_prune.py` rather than inherited from here.
    """
    funcs = conftest.functions(ops_tree)
    unlinkers = set()
    for name, node in funcs.items():
        made = conftest.calls(node)
        if ("os", "unlink") in made or ("agb", "_unlink_quiet") in made:
            unlinkers.add(name)
    assert unlinkers == set(["probe_atomicity", "prune_remove"])
    for name in unlinkers - set(["prune_remove"]):
        made = set(attr for _base, attr in conftest.calls(funcs[name]))
        assert "state_path" not in made, name
        assert "record_path" not in made, name


def test_the_operator_module_never_lists_a_session_directory_itself(ops_tree):
    """Every directory read goes through an `agb` helper, so "own-host readdir
    only" stays one rule in one place. A bare `os.listdir(session_dir(host))`
    here would be a foreign readdir with a cached answer (constraint #5)."""
    funcs = conftest.functions(ops_tree)
    for name, node in funcs.items():
        made = conftest.calls(node)
        assert ("os", "listdir") not in made, name
        assert ("os", "scandir") not in made, name
        assert "session_dir" not in [attr for _base, attr in made], name


FORBIDDEN_WORDS = ("orphan", "provable", "provably", "abandoned", "stale entr")


def test_no_string_this_module_can_print_calls_an_entry_orphaned(ops_tree):
    """The vocabulary rule, checked where it matters: on every **string
    literal** in the file rather than on its text.

    A grep of the source would fail on the header comment, which exists
    precisely to explain why these words are banned -- and a guard that forces
    the explanation to be deleted is a guard that removes the reasoning while
    keeping the rule. What must never happen is *emitting* one of them: Task 6b
    reads this output before it writes the only destructive command in the tool,
    and "orphaned" is what makes a blanket `--force` look reasonable.
    """
    printable = []
    for node in ast.walk(ops_tree):
        if isinstance(node, ast.Str):
            printable.append(node.s.lower())
    assert printable                      # the walk really ran
    for text in printable:
        for word in FORBIDDEN_WORDS:
            assert word not in text, (word, text)
    assert [text for text in printable if "unadjudicable" in text]
