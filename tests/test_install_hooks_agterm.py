"""`agb install-hooks --agterm` -- the same four hooks for a Mac-side agent.

A Mac agent has no statedir and no bridge: it IS an agterm session, so it sets
its own row's status directly. The hard part -- rewriting a live, hand-edited
`~/.claude/settings.json` without losing somebody's tooling -- is reused
wholesale; only the four commands and the "which hooks are ours" predicate
differ.
"""

import io
import json
import os

import pytest


def _settings(tmp_path, data):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(data))
    return str(path)


# ------------------------------------------------------------- the command

def test_the_command_carries_the_marker_and_an_absolute_agtermctl(ops):
    got = ops.agterm_hook_command("/opt/homebrew/bin/agtermctl", "blocked")
    assert got.startswith(ops.AGTERM_HOOK_VAR + "=1 ")
    assert "/opt/homebrew/bin/agtermctl" in got
    assert "session status blocked" in got


def test_the_target_stays_a_shell_VARIABLE(ops):
    """`shlex.quote`ing it would install a hook that targets a session
    literally named `$AGTERM_SESSION_ID`. It must reach the shell unquoted
    enough to expand -- MEASURED that the variable is set inside tmux inside
    agterm, which is what makes this work at all."""
    got = ops.agterm_hook_command("/x/agtermctl", "completed")
    assert '--target "$AGTERM_SESSION_ID"' in got
    assert "'$AGTERM_SESSION_ID'" not in got


def test_blink_is_on_active_only(ops):
    """Blink is an event, not a level -- the same rule the bridge follows."""
    assert ops.agterm_hook_command("/x/agtermctl", "active").endswith("--blink")
    for state in ("blocked", "completed"):
        assert not ops.agterm_hook_command("/x/agtermctl", state).endswith(
            "--blink")


def test_a_path_with_a_space_is_quoted(ops):
    got = ops.agterm_hook_command("/Applications/My Tools/agtermctl", "active")
    assert "'/Applications/My Tools/agtermctl'" in got


# ------------------------------------------------------------ the predicate

def test_an_agterm_hook_is_recognised_by_its_marker(ops):
    command = ops.agterm_hook_command("/x/agtermctl", "active")
    assert ops.classify_hook(command) == ops.HOOK_OURS_AGTERM


def test_a_hand_rolled_agtermctl_status_hook_is_NOT_ours(ops):
    """The whole reason for the marker. `agb ... hook` is unambiguously this
    tool's; `agtermctl session status` is something somebody may reasonably
    have wired themselves, and deleting it would be the silent-tooling-loss
    this installer refuses everywhere else.
    """
    assert ops.classify_hook(
        '/opt/homebrew/bin/agtermctl session status active --target foo'
    ) == ops.HOOK_OTHER


def test_the_two_modes_do_not_remove_each_other(ops):
    assert ops.HOOK_OURS not in ops.removable_kinds(agterm=True)
    assert ops.HOOK_AGR not in ops.removable_kinds(agterm=True)
    assert ops.HOOK_OURS_AGTERM not in ops.removable_kinds(agterm=False)


def test_a_farm_run_keeps_agterm_hooks(ops):
    """A machine could be both a Mac and a farm host."""
    mine = ops.agterm_hook_command("/x/agtermctl", "completed")
    settings = {"hooks": {"Stop": [{"hooks": [
        {"type": "command", "command": mine}]}]}}
    _merged, removed, kept = ops.merge_hooks(
        settings, {s: "c-" + s for _e, _m, s in ops.HOOK_EVENTS}, False)
    assert not removed
    assert [k[1] for k in kept] == [ops.HOOK_OURS_AGTERM]


def test_an_agterm_run_keeps_farm_hooks(ops):
    settings = {"hooks": {"Stop": [{"hooks": [
        {"type": "command",
         "command": "AGB_STATEDIR=/s /usr/bin/python3 -S -E /x/agb hook completed"}]}]}}
    _merged, removed, kept = ops.merge_hooks(
        settings, {s: "c-" + s for _e, _m, s in ops.HOOK_EVENTS}, True)
    assert not removed
    assert [k[1] for k in kept] == [ops.HOOK_OURS]


# ------------------------------------------------------------- the command

def run_hooks(ops, argv, out=None):
    out = out if out is not None else io.StringIO()
    ops.run_install_hooks(argv, out=out, run=lambda a: (0, "", ""))
    return out.getvalue()


def test_an_unrelated_hook_survives(ops, tmp_path):
    path = _settings(tmp_path, {"hooks": {"Stop": [{"hooks": [
        {"type": "command", "command": "/opt/mine/notify done"}]}]}})
    run_hooks(ops, ["--agterm", "--settings", path,
                    "--agtermctl", "/bin/echo"])
    commands = [e["command"]
                for groups in json.loads(io.open(path).read())["hooks"].values()
                for g in groups for e in g["hooks"]]
    assert "/opt/mine/notify done" in commands


def test_a_second_run_is_byte_identical(ops, tmp_path):
    """Removal-then-append is the whole idempotency argument, and it has to
    hold for the new kind too."""
    path = _settings(tmp_path, {"hooks": {}})
    argv = ["--agterm", "--settings", path, "--agtermctl", "/bin/echo"]
    run_hooks(ops, argv)
    first = io.open(path).read()
    run_hooks(ops, argv)
    assert io.open(path).read() == first


def test_dry_run_writes_nothing(ops, tmp_path):
    path = _settings(tmp_path, {"hooks": {}})
    before = io.open(path).read()
    run_hooks(ops, ["--agterm", "--settings", path, "--agtermctl", "/bin/echo",
                    "--dry-run"])
    assert io.open(path).read() == before


def test_no_statedir_is_reported_rather_than_invented(ops, tmp_path):
    path = _settings(tmp_path, {"hooks": {}})
    body = run_hooks(ops, ["--agterm", "--settings", path,
                           "--agtermctl", "/bin/echo"])
    assert "statedir: none" in body


def test_the_interpreter_note_is_not_printed(ops, tmp_path):
    """It is entirely about the python path, and this mode installs none."""
    path = _settings(tmp_path, {"hooks": {}})
    body = run_hooks(ops, ["--agterm", "--settings", path,
                           "--agtermctl", "/bin/echo"])
    assert "note:" not in body


@pytest.mark.parametrize("extra", [["--statedir", "/s"],
                                   ["--python", "/usr/bin/python3"],
                                   ["--agb", "/x/agb"]])
def test_flags_that_would_be_ignored_are_refused(ops, tmp_path, extra):
    """A flag accepted and silently ignored is worse than one that errors."""
    path = _settings(tmp_path, {"hooks": {}})
    with pytest.raises(Exception):
        run_hooks(ops, ["--agterm", "--settings", path,
                        "--agtermctl", "/bin/echo"] + extra)


def test_agtermctl_without_agterm_is_refused(ops, tmp_path):
    path = _settings(tmp_path, {"hooks": {}})
    with pytest.raises(Exception):
        run_hooks(ops, ["--settings", path, "--agtermctl", "/bin/echo"])


def test_a_relative_agtermctl_is_refused(ops, tmp_path):
    """A hook runs under `bash --noprofile --norc` with no reliable PATH --
    measured on macOS, where even `tmux` could not be found by name."""
    path = _settings(tmp_path, {"hooks": {}})
    with pytest.raises(Exception):
        run_hooks(ops, ["--agterm", "--settings", path,
                        "--agtermctl", "agtermctl"])


def test_a_broken_agtermctl_is_refused_before_anything_is_written(ops, tmp_path):
    """An existence check is what every one of agr's silent no-ops would have
    passed. The binary is run, and `session status` must be in it."""
    path = _settings(tmp_path, {"hooks": {}})
    before = io.open(path).read()
    out = io.StringIO()
    with pytest.raises(Exception):
        ops.run_install_hooks(
            ["--agterm", "--settings", path, "--agtermctl", "/bin/echo"],
            out=out, run=lambda a: (1, "", "unknown command"))
    assert io.open(path).read() == before
