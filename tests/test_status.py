"""Task 8 -- `agb status-line`: the tmux status segment.

Three things separate this command from every other one in `agb_ops`, and the
tests are organised around them.

**It repeats.** tmux re-runs it every `status-interval` seconds forever, so it is
a second hot path with its own budget: no json, no subprocess, no argparse, no
writes -- asserted structurally over the reachable call graph rather than by
grepping a file whose comments discuss all four.

**It must never go blank.** A status segment that printed nothing would be
indistinguishable from tmux not running the command at all, which is the exact
silent no-op this project exists to kill. So a bad option, an unreadable beat and
a missing statedir all render one bounded line on **stdout** and are asserted
that way, including through a real subprocess.

**It must not overclaim.** `UP` means "a file another machine wrote is recent",
never "a process exists". `never` is kept distinct from an old age; a future
beat is marked rather than clamped to zero; and a configured `mac_id` whose beat
is missing is `DOWN`, never some other Mac's fresh beat.
"""

import ast
import os
import stat

import pytest

import conftest


HOST = "box2"
MAC = "my-mac"
OTHER = "other-mac"

NOW = 1785000000.0


@pytest.fixture
def sd(agb, statedir, set_host):
    set_host(HOST)
    return str(statedir)


def write_beat(agb, sd, mac_id, mtime=None):
    """A `bridge/<mac-id>.beat`, optionally back- or future-dated."""
    path = agb.bridge_beat_path(sd, mac_id)
    agb.write_in_place(path, b"")
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


class Out(object):
    """A collecting `out`, so assertions are about text rather than capsys."""

    def __init__(self):
        self.text = ""

    def write(self, data):
        self.text += data

    def flush(self):
        pass


# ---------------------------------------------------------------------------
# the humanized age
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("seconds,text", [
    (0, "0s"),
    (0.9, "0s"),
    (2, "2s"),
    (59, "59s"),
    (59.9, "59s"),
    (60, "1m"),
    (119, "1m"),
    (840, "14m"),
    (3599, "59m"),
    (3600, "1h"),
    (86399, "23h"),
    (86400, "1d"),
    (172800, "2d"),
])
def test_the_age_is_compact_and_truncated(ops, seconds, text):
    """Every character is a column of the status bar, and truncation is the
    honest direction: the beat is *at least* this old."""
    assert ops.status_age(seconds) == text


def test_the_age_never_rounds_up_into_the_next_unit(ops):
    """The boundary that matters: 59.9 s must not read `1m`, or a bridge that
    just beat would look a minute stale."""
    assert ops.status_age(59.9) == "59s"
    assert ops.status_age(3599.9) == "59m"


def test_a_future_age_is_marked_rather_than_clamped(ops):
    """Clamping a future-dated beat to `0s` would hide a clock disagreement
    silently, which is the one thing this tool may not do."""
    assert ops.status_age(-3) == "+3s"
    assert ops.status_age(-120) == "+2m"


def test_no_age_at_all_is_never_zero(ops):
    """`never` and `0s` are opposite answers. The absence of a beat file must
    not render as the freshest possible one."""
    assert ops.status_age(None) == "never"


def test_the_segment_age_is_not_doctors_prose(ops):
    """`doctor` says "14 min" in a paragraph of diagnostics; the bar says `14m`.
    Two renderings on purpose, and the test says so rather than a comment."""
    assert ops.age_text(840) == "14 min"
    assert ops.status_age(840) == "14m"


# `age_text` is the OTHER renderer, and the line above used to be its only
# assertion anywhere -- four of its six branches were unproven. It is not
# decoration: it renders the age in `prune`'s per-entry delete prompt, which is
# the number a human reads before authorising an irreversible unlink. So it gets
# the same table `status_age` has above, boundaries included.
@pytest.mark.parametrize("seconds,text", [
    (None, "unknown"),             # never "gone", never "0"
    (-42, "42 s in the future"),   # a future-dated beat is named, not clamped
    (0, "0 s"),
    (2, "2 s"),
    (89, "89 s"),                  # the <90 boundary, from below
    (90, "2 min"),                 # and from above: `<= 90` would say "90 s"
    (91, "2 min"),
    (840, "14 min"),
    (5399, "90 min"),              # the <5400 boundary, from below
    (5400, "1.5 h"),               # and from above
    (172799, "48.0 h"),            # the <172800 boundary, from below
    (172800, "2.0 d"),             # and from above -- /86400, not /3600
    (259200, "3.0 d"),
])
def test_the_doctor_age_covers_every_branch(ops, seconds, text):
    """One unit per row, each boundary from both sides.

    The `h` and `d` rows are what pin the divisor: dividing days by 3600 is a
    24x overstatement that still reads as a perfectly plausible number, and an
    operator deciding whether an entry is old enough to delete has no way to
    tell it from the truth.
    """
    assert ops.age_text(seconds) == text


def test_no_age_at_all_is_never_rendered_as_a_number(ops):
    """`unknown` and `0 s` are opposite answers, and this is the renderer the
    delete prompt uses. "0 s" would read as a beat that had just landed;
    "gone" -- the word the docstring forbids -- would read as evidence of death
    where there is none. The absence of a beat is neither."""
    assert ops.age_text(None) == "unknown"
    assert "0" not in ops.age_text(None)
    assert "gone" not in ops.age_text(None)


# ---------------------------------------------------------------------------
# UP / DOWN
# ---------------------------------------------------------------------------

def test_a_fresh_beat_renders_up(ops):
    assert ops.render_status(2) == "bridge:UP 2s"


def test_a_stale_beat_renders_down(ops):
    assert ops.render_status(840) == "bridge:DOWN 14m"


def test_no_beat_renders_down_never(ops):
    assert ops.render_status(None) == "bridge:DOWN never"


def test_a_future_beat_is_up_and_marked(ops):
    """Somebody wrote that file, and no amount of clock skew says otherwise --
    but the `+` is what tells the operator the two clocks disagree."""
    assert ops.render_status(-3) == "bridge:UP +3s"


@pytest.mark.parametrize("age,expected", [
    (29.9, "bridge:UP 29s"),
    (30.0, "bridge:DOWN 30s"),
])
def test_the_threshold_is_inclusive_at_stale(ops, age, expected):
    assert ops.render_status(age) == expected


def test_the_bar_and_doctor_use_one_threshold(ops):
    """A bar reading `bridge:UP` beside a `doctor` reporting a stale beat is a
    dashboard arguing with itself."""
    assert ops.STATUS_STALE_AFTER == ops.DOCTOR_BEAT_STALE


# ---------------------------------------------------------------------------
# reading the beat
# ---------------------------------------------------------------------------

def test_a_recent_beat_is_up_end_of_story(agb, ops, sd):
    write_beat(agb, sd, MAC, NOW - 2)
    assert ops.status_line(sd, MAC, NOW) == "bridge:UP 2s"


def test_an_old_beat_is_down_with_its_age(agb, ops, sd):
    write_beat(agb, sd, MAC, NOW - 840)
    assert ops.status_line(sd, MAC, NOW) == "bridge:DOWN 14m"


def test_a_missing_beat_is_down_never(agb, ops, sd):
    assert ops.status_line(sd, MAC, NOW) == "bridge:DOWN never"


def test_a_missing_statedir_is_down_never_rather_than_an_error(ops, tmp_path):
    """`status-line` runs on a machine where the shared directory may simply not
    be mounted yet. That is a `DOWN`, not a traceback -- and emphatically not a
    reason to create anything."""
    absent = str(tmp_path / "no-such-statedir")
    assert ops.status_line(absent, MAC, NOW) == "bridge:DOWN never"
    assert not os.path.exists(absent)


def test_a_future_dated_beat_reads_up_with_a_marked_age(agb, ops, sd):
    write_beat(agb, sd, MAC, NOW + 3)
    assert ops.status_line(sd, MAC, NOW) == "bridge:UP +3s"


def test_the_beat_is_read_with_open_and_fstat_not_stat(agb, ops, sd,
                                                        monkeypatch):
    """Constraint #6. This mtime *is* the segment, and `os.stat` is served from
    the NFS attribute cache -- a cached answer here is a bar that keeps saying
    `UP` about a Mac that stopped a minute ago.

    The spy **records** rather than failing in place: raising inside a patched
    `os.stat` takes pytest's own traceback machinery down with it (`linecache`
    stats the source file), so the mutation would be caught as an internal error
    instead of as this assertion.
    """
    write_beat(agb, sd, MAC, NOW - 2)
    statted = []
    real = os.stat

    def spy(path, *args, **kwargs):
        statted.append(str(path))
        return real(path, *args, **kwargs)

    monkeypatch.setattr(os, "stat", spy)
    assert ops.status_line(sd, MAC, NOW) == "bridge:UP 2s"
    assert [path for path in statted if path.startswith(sd)] == []


def test_one_tick_opens_exactly_one_file(agb, ops, sd, monkeypatch):
    """The tick's own budget: the segment reads the beat and nothing else when
    both the statedir and the mac-id are on the command line."""
    write_beat(agb, sd, MAC, NOW - 2)
    opened = []
    real = os.open

    def spy(path, *args, **kwargs):
        if str(path).startswith(sd):
            opened.append(str(path))
        return real(path, *args, **kwargs)

    monkeypatch.setattr(os, "open", spy)
    ops.run_status_line(["--statedir", sd, "--mac-id", MAC], Out(), NOW)
    assert opened == [agb.bridge_beat_path(sd, MAC)]


def test_the_segment_supplies_now_when_the_caller_does_not(agb, ops, sd):
    """`now` is injectable so the tests are not races against the clock -- and
    the default has to be exercised, or the injection is the only tested path."""
    write_beat(agb, sd, MAC)
    assert ops.status_line(sd, MAC).startswith("bridge:UP")


# ---------------------------------------------------------------------------
# which Mac
# ---------------------------------------------------------------------------

def test_the_mac_id_comes_from_the_config_when_not_given(agb, ops, sd,
                                                          config_file):
    config_file("mac_id = %s\n" % (MAC,))
    write_beat(agb, sd, MAC, NOW - 2)
    write_beat(agb, sd, OTHER, NOW - 900)
    out = Out()
    assert ops.run_status_line(["--statedir", sd], out, NOW) == 0
    assert out.text == "bridge:UP 2s\n"


def test_the_option_beats_the_config(agb, ops, sd, config_file):
    config_file("mac_id = %s\n" % (OTHER,))
    write_beat(agb, sd, MAC, NOW - 2)
    write_beat(agb, sd, OTHER, NOW - 900)
    out = Out()
    assert ops.run_status_line(["--statedir", sd, "--mac-id", MAC], out,
                               NOW) == 0
    assert out.text == "bridge:UP 2s\n"


def test_with_no_mac_id_anywhere_the_newest_beat_wins(agb, ops, sd):
    """The fallback, before `install.sh` has written a `mac_id`. Single-Mac
    topology is assumed by the plan, so there is nothing to arbitrate."""
    write_beat(agb, sd, OTHER, NOW - 900)
    write_beat(agb, sd, MAC, NOW - 2)
    assert ops.status_line(sd, None, NOW) == "bridge:UP 2s"


def test_a_configured_mac_id_is_never_second_guessed(agb, ops, sd):
    """The failure this rule prevents: reporting a *different* machine as this
    one. A named Mac with no beat is `DOWN never`, even with another Mac
    beating two seconds ago.
    """
    write_beat(agb, sd, OTHER, NOW - 2)
    assert ops.status_line(sd, MAC, NOW) == "bridge:DOWN never"


def test_the_fallback_ignores_temps_and_non_beats(agb, ops, sd):
    """A half-written temp is not a Mac. `bridge/` is written in place, but the
    filter is the same one `doctor` uses and the two must not drift."""
    bridge = os.path.join(sd, "bridge")
    for name in ("readme.txt", "%s.beat.tmp.box2.1.abcd" % (MAC,),
                 "x.tmp.box2.1.ab.beat"):
        agb.write_in_place(os.path.join(bridge, name), b"")
        os.utime(os.path.join(bridge, name), (NOW, NOW))
    assert ops.status_line(sd, None, NOW) == "bridge:DOWN never"
    write_beat(agb, sd, MAC, NOW - 900)
    assert ops.status_line(sd, None, NOW) == "bridge:DOWN 15m"


def test_an_empty_bridge_directory_is_never(agb, ops, sd):
    assert ops.status_line(sd, None, NOW) == "bridge:DOWN never"


def test_the_config_is_not_read_when_the_mac_id_is_given(agb, ops, sd,
                                                          monkeypatch):
    """The documented tmux.conf line carries `--mac-id` and `AGB_STATEDIR` for
    exactly this reason: at one tick every few seconds forever, an avoided
    `open()` of an NFS `$HOME` is worth spelling out."""
    write_beat(agb, sd, MAC, NOW - 2)
    monkeypatch.setattr(agb, "read_config",
                        lambda *a, **k: pytest.fail("read the config"))
    assert ops.run_status_line(["--statedir", sd, "--mac-id", MAC], Out(),
                               NOW) == 0


def test_the_config_is_read_once_when_it_is_needed(agb, ops, sd, monkeypatch):
    """...and exactly once. `agb.statedir()` reads it only when `$AGB_STATEDIR`
    is unset, which the fixture does set, so the mac-id lookup is the only
    reader left."""
    write_beat(agb, sd, MAC, NOW - 2)
    reads = []
    real = agb.read_config
    monkeypatch.setattr(agb, "read_config",
                        lambda *a, **k: (reads.append(1), real(*a, **k))[1])
    ops.run_status_line([], Out(), NOW)
    assert len(reads) == 1


# ---------------------------------------------------------------------------
# it never goes blank
# ---------------------------------------------------------------------------

def test_an_unreadable_beat_renders_an_error_not_a_traceback(agb, ops, sd):
    path = write_beat(agb, sd, MAC, NOW - 2)
    os.chmod(path, 0)
    if os.access(path, os.R_OK):                    # running as root
        pytest.skip("cannot make a file unreadable as this user")
    out = Out()
    assert ops.run_status_line(["--statedir", sd, "--mac-id", MAC], out,
                               NOW) == 1
    assert out.text == "bridge:ERR EACCES\n"


def test_an_unreadable_bridge_directory_renders_an_error(agb, ops, sd):
    """The fallback path's version of the same hazard: it is the one place the
    command reads a directory."""
    bridge = os.path.join(sd, "bridge")
    os.chmod(bridge, 0)
    try:
        if os.access(bridge, os.R_OK):
            pytest.skip("cannot make a directory unreadable as this user")
        out = Out()
        assert ops.run_status_line(["--statedir", sd], out, NOW) == 1
        assert out.text == "bridge:ERR EACCES\n"
    finally:
        os.chmod(bridge, stat.S_IRWXU)


def test_a_bad_option_is_shown_in_the_bar_rather_than_on_stderr(ops):
    """`agb`'s top-level handler prints an `AgbError` to stderr and exits 1,
    which tmux renders as an empty segment: a bar saying nothing at all about a
    bridge that may well be down. A typo in `~/.tmux.conf` has to be visible
    where the operator is looking.
    """
    out = Out()
    assert ops.run_status_line(["--bogus"], out) == 1
    assert out.text.startswith("bridge:ERR ")
    assert "--bogus" in out.text


def test_every_answer_is_exactly_one_line(agb, ops, sd):
    for argv in (["--statedir", sd, "--mac-id", MAC],      # never
                 ["--statedir", sd],                       # fallback, empty
                 ["--nonsense"]):                          # error
        out = Out()
        ops.run_status_line(argv, out, NOW)
        assert out.text.endswith("\n")
        assert out.text.count("\n") == 1, argv


def test_an_error_line_is_bounded_in_width(ops):
    """A 300-character exception message would push the clock off the end of the
    status bar.

    The bound is a literal, not `len(STATUS_ERR) + 1 + STATUS_ERR_LIMIT`. Derived
    from the constants, this test defined its own pass condition: setting
    `STATUS_ERR_LIMIT = 1000` kept it green while defeating the entire point.
    60 columns is the claim -- a share of an 80-column status bar that still
    leaves room for the rest of the segment.
    """
    long_mac = "m" * 200
    out = Out()
    ops.run_status_line(["--mac-id=" + long_mac], out)
    assert len(out.text.rstrip("\n")) <= 60
    # ...and the constants really are what produce that, so a future change to
    # either one fails here rather than silently widening the bar.
    assert len(ops.STATUS_ERR) + 1 + ops.STATUS_ERR_LIMIT <= 60


def test_an_error_line_is_collapsed_onto_one_line(ops, agb):
    """A newline inside the message would make tmux render a second line, or
    swallow the rest of the format."""
    assert "\n" not in ops.status_error(agb.AgbError("a\nb\tc"))
    assert ops.status_error(agb.AgbError("a\nb\tc")) == "a b c"


def test_an_oserror_renders_as_its_errno_name(ops):
    """`EACCES` is the word an operator greps for, and it is the same width
    every time."""
    assert ops.status_error(OSError(13, "Permission denied")) == "EACCES"
    assert ops.status_error(OSError(116, "Stale file handle")) == "ESTALE"


def test_an_unnumbered_oserror_still_says_something(ops):
    assert ops.status_error(OSError("no errno here")) == "no errno here"


# ---------------------------------------------------------------------------
# the parser
# ---------------------------------------------------------------------------

def test_the_argument_parser_accepts_both_option_forms(ops):
    assert ops.parse_status_args(["--mac-id", MAC])["mac_id"] == MAC
    assert ops.parse_status_args(["--mac-id=" + MAC])["mac_id"] == MAC
    assert ops.parse_status_args(["--statedir=/tmp/x"])["statedir"] == "/tmp/x"


def test_the_statedir_option_expands_a_tilde(ops, fake_home):
    got = ops.parse_status_args(["--statedir", "~/state"])["statedir"]
    assert got == os.path.join(str(fake_home), "state")


@pytest.mark.parametrize("argv", [
    ["--mac-id"],
    ["--statedir"],
    ["--mac-id", "../escape"],
    ["--mac-id", ""],
    ["--unknown"],
    ["stray-argument"],
])
def test_bad_arguments_raise_a_described_error(ops, agb, argv):
    with pytest.raises(agb.AgbError):
        ops.parse_status_args(argv)


def test_the_mac_id_is_validated_because_it_becomes_a_path(ops, agb):
    """`bridge/<mac-id>.beat`. The same predicate `feed` and `doctor` use."""
    with pytest.raises(agb.AgbError):
        ops.parse_status_args(["--mac-id", "../../etc/passwd"])


# ---------------------------------------------------------------------------
# end to end, through the real dispatch
# ---------------------------------------------------------------------------

def test_status_line_runs_end_to_end(agb, sd, run_agb):
    write_beat(agb, sd, MAC)
    rc, out, err = run_agb(["status-line", "--mac-id", MAC])
    assert (rc, err) == (0, b"")
    assert out.startswith(b"bridge:UP ")


def test_a_missing_beat_end_to_end_is_down_never_and_exits_zero(agb, sd,
                                                                 run_agb):
    rc, out, err = run_agb(["status-line", "--mac-id", MAC])
    assert (rc, out, err) == (0, b"bridge:DOWN never\n", b"")


def test_a_bad_option_end_to_end_prints_on_stdout_and_exits_one(run_agb):
    """Not stderr: tmux reads stdout, and a segment that puts its diagnosis
    somewhere tmux does not read is a blank bar."""
    rc, out, err = run_agb(["status-line", "--bogus"])
    assert rc == 1
    assert out.startswith(b"bridge:ERR ")
    assert err == b""


def test_the_statedir_comes_from_the_environment_end_to_end(agb, sd, run_agb):
    """The documented tmux.conf line sets `AGB_STATEDIR` inline, because `#()`
    is an `sh` command line and tmux sources no profile."""
    write_beat(agb, sd, MAC)
    rc, out, err = run_agb(["status-line", "--mac-id", MAC],
                           env={"AGB_STATEDIR": sd})
    assert (rc, err) == (0, b"")
    assert out.startswith(b"bridge:UP ")


# ---------------------------------------------------------------------------
# structural guards
# ---------------------------------------------------------------------------

def test_status_line_is_reached_through_the_one_shared_operator_door(agb_tree,
                                                                      agb):
    """Task 6b's consolidation, collected again: `status-line` cost `agb` no
    bytes at all -- no `cmd_status_line`, no new dispatch line."""
    funcs = conftest.functions(agb_tree)
    assert "status-line" in agb.OPS_COMMANDS
    assert "cmd_status_line" not in funcs
    assert "run_status_line" not in funcs


def status_reachable(all_trees):
    """The call graph behind `agb status-line`, across every file."""
    funcs = conftest.functions(*all_trees)
    reachable = conftest.reachable_from(funcs, "run_status_line")
    assert "render_status" in reachable
    assert "bridge_beat_age" in reachable
    assert "_stat_fresh" in reachable          # ...and back into agb's helpers
    return funcs, reachable


def test_the_status_graph_spans_the_files(all_trees):
    status_reachable(all_trees)


def test_the_tick_imports_no_json_and_no_subprocess(all_trees):
    """The plan's checkbox -- "keep it cheap enough for `status-interval`" --
    as a structural claim rather than a comment. Both are function-local imports
    elsewhere in this same file (`_json`, `pane_attach`, `prune_via_ssh`), so a
    module-top guard would not see them.
    """
    funcs, reachable = status_reachable(all_trees)
    for name in reachable:
        for node in ast.walk(funcs[name]):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name.split(".")[0] not in (
                        "json", "subprocess", "argparse", "socket"), name
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert node.module.split(".")[0] not in (
                    "json", "subprocess", "argparse", "socket"), name
    assert "_json" not in reachable


def test_the_tick_writes_nothing_and_removes_nothing(all_trees):
    """It is a display. A segment that created directories, or that raced the
    feed for a filename, would be a display with side effects -- and it runs
    every few seconds forever, on every host.
    """
    funcs, reachable = status_reachable(all_trees)
    for name in reachable:
        made = conftest.calls(funcs[name])
        for forbidden in ("unlink", "rename", "utime", "mkdir", "makedirs",
                          "link", "chmod"):
            assert ("os", forbidden) not in made, (name, forbidden)
        bare = [attr for _base, attr in made]
        for forbidden in ("atomic_write", "write_in_place", "ensure_statedir",
                          "ensure_session_dir", "breadcrumb", "reap_entry",
                          "rebuild_marker"):
            assert forbidden not in bare, (name, forbidden)


def test_the_tick_reads_one_directory_and_only_in_the_fallback(all_trees):
    """`readdir` is the laggy path (`acdirmax=60`), so it is confined to
    `newest_beat_age` -- the branch taken only when no `mac_id` is configured.
    A listing on the configured path would make every tick pay that lag.

    The listing itself lives in `beat_mtimes`, shared with `doctor`'s
    `probe_bridge_beats` so the "is this name a beat, or `atomic_write`'s
    temp?" filter is written once. That sharing is only safe while nothing on
    the *configured* path can reach it, which is what the second assertion
    checks -- `beat_mtimes` reaching `bridge_beat_age` would put the readdir
    back on every tick.
    """
    funcs, reachable = status_reachable(all_trees)
    listers = set(name for name, node in funcs.items()
                  if name in reachable
                  and ("_listdir_quiet" in [a for _b, a in conftest.calls(node)]
                       or ("os", "listdir") in conftest.calls(node)
                       or ("os", "scandir") in conftest.calls(node)))
    assert listers == set(["beat_mtimes", "_listdir_quiet"])
    callers = set(name for name, node in funcs.items()
                  if name in reachable
                  and (None, "beat_mtimes") in conftest.calls(node))
    assert callers == set(["newest_beat_age"])
    beat_age = conftest.reachable_from(funcs, "bridge_beat_age")
    assert "newest_beat_age" not in beat_age
    assert "beat_mtimes" not in beat_age


def test_no_hook_path_function_can_reach_the_segment(all_trees):
    """`status-line` is a repeating path, but it is not the *hook's* path: the
    hook must still never load `agb_ops` at all."""
    funcs = conftest.functions(*all_trees)
    reachable = conftest.reachable_from(funcs, "cmd_hook")
    assert "hook_apply" in reachable                  # the walk really ran
    assert "run_status_line" not in reachable
    assert "status_line" not in reachable


def test_the_segment_never_names_a_process_or_a_socket(ops_source):
    """`UP` is a claim about a *file another machine wrote*. `agr`'s whole
    failure class was claims of the other shape -- "a process exists", "a socket
    is there" -- so the strings this command can print are checked for them."""
    for word in ("bridge:UP", "bridge:DOWN", "bridge:ERR"):
        assert word in ops_source
    assert "bridge:ALIVE" not in ops_source
    assert "bridge:CONNECTED" not in ops_source


def test_the_operator_file_still_carries_the_bulk(ops_source, agb_source):
    """Task 8 added a command and `agb` grew nothing at all."""
    assert len(ops_source) > 70000
    assert len(agb_source) < conftest.AGB_PARSE_BUDGET


def test_the_documentation_exists_and_states_the_lag(repo_root):
    """The plan's checkbox: the segment must not be trusted below `acdirmax`,
    and that has to be written down where an operator configuring tmux will
    read it -- not only in a source comment.
    """
    with open(os.path.join(repo_root, "docs", "tmux.md")) as handle:
        text = handle.read()
    assert "acdirmax" in text
    assert "status-interval" in text
    assert "-S -E" in text
    assert "60 s" in text
