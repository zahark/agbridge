"""Task 9a -- `agb install-hooks`: wiring Claude Code to the tool.

This is the one command that edits a file it does not own, and the file is
**live**: it is the running configuration of the session executing the command.
So the tests are organised around the four ways that can go wrong, and every one
of them is a way this tool could lie or lose data rather than a style question.

**It must not touch the developer's real settings.** `--settings <path>` is the
seam that works across a subprocess boundary and `conftest.fake_home` is
autouse for the rest; both are asserted here rather than assumed, because a bug
in this command with neither in place is unrecoverable.

**It must supersede `agr`, and only `agr`.** The box this was written on carries
four live `agr` hook entries on the same four events. Left in place, both tools
fire on every tool call and the whole measured hot-path budget is fiction. But
"remove the agr hooks" and "remove somebody's tooling" are one sloppy predicate
apart and the wrong answer is silent, so the removal is checked from both sides:
the four real entries go, and `agrep`/`agrippa`/`sh -c '... agr ...'` stay.

**It must produce a command that runs.** The generated string is
`AGB_STATEDIR=<sd> <abs-python> -S -E <abs-agb> hook <state>` -- never a
shebang, never bare `python3` (constraint #14: a hook that cannot resolve its
interpreter dies *before* `agb` starts, so nothing is recorded and no breadcrumb
is written, which is the exact silent-failure class this project exists to
kill). The last test in this file takes the string the command installed, feeds
it to `/bin/sh`, and requires a session to appear in the statedir.

**It must never rewrite what it could not read.** Malformed JSON, a `hooks`
value of the wrong shape, an unreadable file: each aborts with the reason and
leaves the file byte for byte as it was.
"""

import ast
import json
import os
import shutil
import stat
import subprocess
import sys

import pytest

import conftest


HOST = "box2"

# Captured at import time, before the autouse `fake_home` fixture moves `$HOME`
# -- so it is the developer's *real* file. It is only ever read, and only to
# prove that nothing here writes it.
REAL_SETTINGS = os.path.expanduser("~/.claude/settings.json")

# The four entries `~/.claude/settings.json` really carries on this box today,
# transcribed verbatim (read-only) from the live file. The removal is tested
# against the actual thing being replaced, not against a paraphrase of it.
LIVE_AGR = {
    "UserPromptSubmit": [
        {"hooks": [{"type": "command",
                    "command": "$HOME/.local/bin/agr status active --blink"}]}
    ],
    "PostToolUse": [
        {"hooks": [{"type": "command",
                    "command": "$HOME/.local/bin/agr status active --blink"}]}
    ],
    "Notification": [
        {"matcher": "permission_prompt",
         "hooks": [{"type": "command",
                    "command": "$HOME/.local/bin/agr status blocked"}]}
    ],
    "Stop": [
        {"hooks": [{"type": "command",
                    "command":
                        "$HOME/.local/bin/agr status completed --auto-reset"}]}
    ],
}


class Out(object):
    """A collecting `out`, so assertions are about text rather than capsys."""

    def __init__(self):
        self.text = ""

    def write(self, data):
        self.text += data

    def flush(self):
        pass


@pytest.fixture
def runner(agb):
    """A recording stand-in for `_probe_run`.

    The real probe spawns `<python> -S -E <agb> version`; injecting it keeps the
    argv a list comparison, and lets a test make the probe *fail* -- which is
    the only way to check that nothing is written when the hook command cannot
    be shown to work.
    """
    class Runner(object):
        def __init__(self):
            self.calls = []
            self.code = 0
            self.out = "agb %s\n" % (agb.VERSION,)
            self.err = ""

        def __call__(self, argv):
            self.calls.append(list(argv))
            return (self.code, self.out, self.err)

    return Runner()


@pytest.fixture
def settings_file(tmp_path):
    """A settings.json path under the test's own tree. Never the real one."""
    path = tmp_path / "claude" / "settings.json"
    os.makedirs(str(path.parent))

    def write(data):
        text = data if isinstance(data, str) else json.dumps(data, indent=2)
        with open(str(path), "w") as handle:
            handle.write(text)
        return str(path)

    write.path = str(path)
    return write


@pytest.fixture
def sd(agb, tmp_path):
    """A created statedir, passed explicitly so no test can fall back to the
    real `/shared/.agbridge` through `agb.statedir()`."""
    path = str(tmp_path / "state")
    agb.ensure_statedir(path)
    return path


@pytest.fixture
def install(ops, agb_path, sd, settings_file, runner, set_host):
    """Run the command with every dangerous default pinned to the test tree."""
    set_host(HOST)

    def run(*extra, **kwargs):
        out = kwargs.pop("out", None) or Out()
        argv = ["--settings", settings_file.path, "--statedir", sd,
                "--python", sys.executable, "--agb", agb_path]
        argv.extend(extra)
        code = ops.run_install_hooks(argv, out=out, run=runner)
        return code, out.text

    run.out = Out
    return run


def read_json(path):
    with open(path) as handle:
        return json.load(handle)


def commands_of(settings, event):
    """Every hook command installed on `event`, in file order."""
    found = []
    for group in settings.get("hooks", {}).get(event, []):
        for entry in group.get("hooks", []):
            found.append(entry.get("command"))
    return found


# ---------------------------------------------------------------------------
# the generated command -- constraints #1 and #14
# ---------------------------------------------------------------------------

def test_the_command_is_the_documented_shape_word_for_word(ops):
    """`AGB_STATEDIR=<sd> <abs-python> -S -E <abs-agb> hook <state>`.

    Asserted as the shell's own tokenisation rather than as a substring: the
    string is re-split by `sh`, so `"-S -E"` appearing *somewhere* in it is not
    the same claim as the interpreter receiving two separate flags.
    """
    import shlex

    command = ops.hook_command("/bin/python3", "/opt/agb/agb", "/nfs/state",
                               "active")
    assert shlex.split(command) == [
        "AGB_STATEDIR=/nfs/state", "/bin/python3", "-S", "-E",
        "/opt/agb/agb", "hook", "active"]


def test_the_interpreter_is_an_absolute_path_and_never_bare_python3(ops, sd,
                                                                    agb_path):
    """Constraint #14. Hooks run in a minimal non-interactive environment, so a
    bare `python3` that fails to resolve kills the hook *before* `agb` runs --
    no state, no breadcrumb, no trace. The regression guard is that no word of
    the installed command is ever a bare interpreter name."""
    import shlex

    for state in ("active", "blocked", "completed"):
        words = shlex.split(ops.hook_command(sys.executable, agb_path, sd,
                                             state))
        assert words[1].startswith("/")
        assert words[1] == sys.executable
        for word in words:
            assert word not in ("python", "python3", "env", "./agb")


def test_the_statedir_is_baked_in_so_the_hot_path_never_reads_the_config(ops,
                                                                         sd,
                                                                         agb_path):
    """The measured hot-path win, as a regression guard: `AGB_STATEDIR` in the
    command is what lets `agb.statedir()` answer without touching the config
    file on a two-file NFS budget."""
    command = ops.hook_command(sys.executable, agb_path, sd, "active")
    assert command.startswith("AGB_STATEDIR=")
    assert sd in command


def test_a_statedir_with_a_space_survives_being_re_split_by_a_shell(ops,
                                                                    agb_path):
    """`shlex.quote`, not string concatenation: an unquoted path with a space
    installs a hook that runs a *different* command than the one printed in the
    report -- quiet divergence between what a tool says and what it did, which
    is the whole failure class here."""
    import shlex

    weird = "/shared/my state dir"
    words = shlex.split(ops.hook_command(sys.executable, agb_path, weird,
                                         "active"))
    assert words[0] == "AGB_STATEDIR=" + weird


def test_the_command_carries_no_shebang_and_no_interpreter_flags_are_dropped(
        ops, sd, agb_path):
    """`-S` skips `site.py` (which scans user site-packages over NFS) and `-E`
    ignores `PYTHONPATH`/`PYTHONHOME`. A shebang cannot carry either -- `env`
    does not forward interpreter flags -- which is why the command names the
    interpreter itself."""
    command = ops.hook_command(sys.executable, agb_path, sd, "active")
    assert not command.startswith("#!")
    assert " -S -E " in command
    assert command.endswith(" hook active")


# ---------------------------------------------------------------------------
# the event mapping -- and the matcher that keeps `blocked` honest
# ---------------------------------------------------------------------------

def test_the_four_events_map_to_the_documented_states(ops):
    assert ops.HOOK_EVENTS == (
        ("UserPromptSubmit", None, "active"),
        ("PostToolUse", None, "active"),
        ("Notification", "permission_prompt", "blocked"),
        ("Stop", None, "completed"),
    )


def test_notification_carries_the_permission_prompt_matcher(ops, install,
                                                            settings_file):
    """⚠️ The one matcher that is load-bearing. Unmatched, `Notification` fires
    for more than permission prompts, and every one of those would paint the row
    `blocked` -- a dashboard reporting "waiting for you" when nothing is, which
    is precisely the lie this project exists to prevent."""
    install()
    settings = read_json(settings_file.path)
    groups = settings["hooks"]["Notification"]
    assert len(groups) == 1
    assert groups[0]["matcher"] == "permission_prompt"
    assert "hook blocked" in groups[0]["hooks"][0]["command"]


def test_only_notification_carries_a_matcher(ops, install, settings_file):
    """The other three take every occurrence of their event. A matcher on
    `Stop` or `UserPromptSubmit` would silently drop transitions."""
    install()
    settings = read_json(settings_file.path)
    for event in ("UserPromptSubmit", "PostToolUse", "Stop"):
        for group in settings["hooks"][event]:
            assert "matcher" not in group, event


def test_no_hook_produces_idle(ops, agb, install, settings_file):
    """Amendment 2 and Task 2a's decision, checked where it could regress: the
    vocabulary has no `unknown` and `idle` renders as *no glyph*, so an
    agent-reported `idle` would be pixel-identical to a `[done]` row."""
    assert set(state for _e, _m, state in ops.HOOK_EVENTS) == set(
        agb.AGENT_STATES)
    install()
    settings = read_json(settings_file.path)
    for event in settings["hooks"]:
        for command in commands_of(settings, event):
            assert not command.endswith("hook idle")


def test_every_installed_entry_is_a_command_hook(ops, install, settings_file):
    """The shape Claude Code actually reads. A `type` this tool invented would
    be ignored silently -- an install that reports success and wires nothing."""
    install()
    settings = read_json(settings_file.path)
    installed = 0
    for event in settings["hooks"]:
        for group in settings["hooks"][event]:
            for entry in group["hooks"]:
                assert set(entry) == set(["type", "command"])
                assert entry["type"] == "command"
                installed += 1
    assert installed == len(ops.HOOK_EVENTS)


# ---------------------------------------------------------------------------
# merging: idempotent, and it supersedes `agr`
# ---------------------------------------------------------------------------

def test_installing_into_an_empty_file_writes_exactly_the_four_hooks(
        install, settings_file):
    settings_file({})
    code, text = install()
    assert code == 0
    settings = read_json(settings_file.path)
    assert sorted(settings["hooks"]) == sorted(
        ["Notification", "PostToolUse", "Stop", "UserPromptSubmit"])
    assert "wrote:" in text


def test_installing_twice_leaves_the_file_byte_for_byte_identical(
        install, settings_file):
    """Idempotency stated as bytes rather than as "looks the same": the second
    run removes exactly what the first wrote and puts back exactly the same
    thing, so there is nothing left for a diff to show."""
    settings_file(dict(LIVE_AGR and {"hooks": LIVE_AGR}))
    install()
    with open(settings_file.path, "rb") as handle:
        first = handle.read()
    code, text = install()
    with open(settings_file.path, "rb") as handle:
        second = handle.read()
    assert code == 0
    assert first == second
    assert "unchanged:" in text


def test_installing_twice_from_a_path_with_a_space_is_still_idempotent(
        install, settings_file, repo_root, tmp_path):
    """⚠️ The one shape that separates `shlex.split` from `str.split`.

    Every command the rest of this file installs is quote-free, so replacing
    `command_words`' `shlex.split` with `command.split()` passed the entire
    suite. It breaks here. The installer `shlex.quote`s every word it writes,
    so an install directory with a space is written as `'/a b/agb'`; a naive
    tokeniser splits that into `'/a` and `b/agb'`, whose basename is `agb'` and
    not `agb`, so `classify_hook` never recognises our own entry. `merge_hooks`
    then keeps the old one AND appends a fresh one -- a duplicate hook group
    per install, for ever, on the second-most-likely install path there is
    (`~/Library/Application Support`, `/Volumes/My Disk`).
    """
    spaced = tmp_path / "a b"
    spaced.mkdir()
    for name in ("agb", "agb_mac", "agb_ops"):
        shutil.copyfile(os.path.join(repo_root, name), str(spaced / name))
    settings_file({})
    install("--agb", str(spaced / "agb"))
    with open(settings_file.path, "rb") as handle:
        first = handle.read()
    assert b"'" in first, "the fixture is only meaningful if it needs quoting"
    code, text = install("--agb", str(spaced / "agb"))
    with open(settings_file.path, "rb") as handle:
        second = handle.read()
    assert code == 0
    assert first == second
    assert "unchanged:" in text
    assert len(commands_of(read_json(settings_file.path), "Stop")) == 1


@pytest.mark.parametrize("word,is_assignment", [
    ("AGB_STATEDIR=/s", True),
    ("FOO=", True),
    ("_x1=y", True),
    ("./x=y", False),           # a path, not a name: the program itself
    ("FOO-BAR=1", False),       # `-` is not a name character
    ("1FOO=x", False),          # a name may not start with a digit
    ("=x", False),
    ("/opt/agb/agb", False),
])
def test_an_assignment_prefix_is_a_shell_NAME_not_merely_a_word_with_an_equals(
        ops, word, is_assignment):
    """`command_program` skips leading assignments to find the program, so
    every word wrongly called one shifts its answer to the next word -- and the
    answer decides whether an entry is deleted. Replacing `_is_assignment` with
    `"=" in word` passed the whole suite before these rows existed."""
    assert ops._is_assignment(word) is is_assignment


def test_installing_twice_does_not_stack_a_second_entry(install,
                                                        settings_file):
    settings_file({})
    install()
    install()
    settings = read_json(settings_file.path)
    for event in settings["hooks"]:
        assert len(commands_of(settings, event)) == 1, event


def test_the_four_live_agr_entries_are_removed(ops, install, settings_file):
    """⚠️ The plan's checkbox, against the real entries. They are not
    "unrelated existing hooks" to preserve -- they are the tool being replaced.
    Left in place, both fire on every tool call and `agr` keeps writing the
    stale target mappings this project exists to eliminate.

    The survivors are checked with the classifier rather than with `"agr" not
    in command`: the temp directory a test runs in can itself contain the
    letters, which is a small live demonstration of why the removal predicate is
    not a substring search either.
    """
    settings_file({"hooks": LIVE_AGR})
    code, text = install()
    assert code == 0
    settings = read_json(settings_file.path)
    for event in LIVE_AGR:
        installed = commands_of(settings, event)
        assert len(installed) == 1
        assert ops.classify_hook(installed[0]) == ops.HOOK_OURS
        assert "/agr" not in installed[0] and not installed[0].startswith("agr")
    assert text.count("removed:  ") == 4


def test_every_removal_is_named_in_the_report(install, settings_file):
    """This command's whole job is to make the tool audible, so it may not be
    quiet about the one file it edits."""
    settings_file({"hooks": LIVE_AGR})
    _code, text = install()
    for event in LIVE_AGR:
        assert event in text
    assert "$HOME/.local/bin/agr status blocked" in text


def test_an_agr_entry_on_a_fifth_event_is_removed_too(install,
                                                      settings_file):
    """The sweep runs over **every** event in the file, not only the four we
    write: an `agr` entry left on `SessionStart` would keep agr running, which
    is the whole point of removing them."""
    hooks = dict(LIVE_AGR)
    hooks["SessionStart"] = [
        {"hooks": [{"type": "command",
                    "command": "$HOME/.local/bin/agr status active"}]}]
    settings_file({"hooks": hooks})
    install()
    settings = read_json(settings_file.path)
    assert "SessionStart" not in settings["hooks"]


def test_a_stale_agb_entry_from_an_older_install_is_replaced_not_stacked(
        ops, install, settings_file, sd, agb_path):
    """A re-install after the statedir, the interpreter or the checkout moved
    must replace the old line. Our own entries are recognised structurally -- a
    word whose basename is `agb` followed by `hook` -- and that, and nothing
    else, is what makes this command idempotent across a move."""
    old = ops.hook_command("/usr/bin/python3", "/old/checkout/agb",
                           "/old/state", "active")
    settings_file({"hooks": {"UserPromptSubmit": [
        {"hooks": [{"type": "command", "command": old}]}]}})
    install()
    settings = read_json(settings_file.path)
    installed = commands_of(settings, "UserPromptSubmit")
    assert len(installed) == 1
    assert "/old/state" not in installed[0]
    assert sd in installed[0]


def test_an_unrelated_hook_on_the_same_event_is_preserved_byte_for_byte(
        install, settings_file):
    """Our groups are **appended**, never merged into somebody else's: Claude
    Code runs every matching group, so appending preserves the neighbour exactly
    -- matcher, ordering and all -- instead of editing a structure this tool did
    not write."""
    neighbour = {"matcher": "Bash",
                 "hooks": [{"type": "command",
                            "command": "/usr/local/bin/mytool log"}]}
    settings_file({"hooks": {"PostToolUse": [dict(neighbour)]}})
    install()
    settings = read_json(settings_file.path)
    assert settings["hooks"]["PostToolUse"][0] == neighbour
    assert len(settings["hooks"]["PostToolUse"]) == 2


def test_an_unrelated_event_is_left_entirely_alone(install, settings_file):
    other = {"PreToolUse": [{"matcher": "Write",
                             "hooks": [{"type": "command",
                                        "command": "/opt/audit/hook"}]}]}
    settings_file({"hooks": dict(other)})
    install()
    settings = read_json(settings_file.path)
    assert settings["hooks"]["PreToolUse"] == other["PreToolUse"]


def test_unrelated_top_level_settings_survive_in_their_original_order(
        install, settings_file):
    """Not `sort_keys`: this is somebody's hand-edited file, and reordering
    every key would make the diff of a four-line change unreadable."""
    settings_file({"model": "opus", "hooks": {}, "theme": "dark",
                   "permissions": {"allow": ["Bash(ls:*)"]}})
    install()
    settings = read_json(settings_file.path)
    assert list(settings) == ["model", "hooks", "theme", "permissions"]
    assert settings["permissions"] == {"allow": ["Bash(ls:*)"]}
    assert settings["theme"] == "dark"


# ---------------------------------------------------------------------------
# the removal predicate: structural, never a substring
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("command,kind", [
    ("$HOME/.local/bin/agr status active --blink", "agr"),
    ("agr status blocked", "agr"),
    ("env AGTERM=1 /usr/bin/agr status active", "agr"),
    ("FOO=bar /usr/bin/agr status active", "agr"),
    ("AGB_STATEDIR=/s /bin/python3 -S -E /opt/agb/agb hook active", "agb"),
    ("/opt/agb/agb hook completed", "agb"),
    # ⚠️ The argv-SHAPE half. `merge_hooks` removes every `agb` entry across
    # every event, so a classifier that answered from the program basename
    # alone would silently delete somebody's `agb status-line` or `agb doctor`
    # hook on the next install-hooks. Without these two rows, deleting the
    # `words[index + 1:index + 2] == ["hook"]` test passes the whole suite.
    ("/opt/agb/agb version", "other"),
    ("/opt/agb/agb feed mac-1", "other"),
    ("AGB_STATEDIR=/s /bin/python3 -S -E /opt/agb/agb status-line", "other"),
    ("/opt/agb/agb", "other"),                    # nothing after the program
    ("sh -c '$HOME/.local/bin/agr status active'", "suspect"),
    ("/usr/bin/agrep -q pattern file", "other"),
    ("/opt/agrippa/run --now", "other"),
    ("/usr/local/bin/mytool log", "other"),
    ("", "other"),
    (None, "other"),
])
def test_the_classifier_answers_from_the_program_not_the_text(ops, command,
                                                              kind):
    """The two kinds that get removed are recognised from the program a shell
    would actually run and from the argv shape. `suspect` exists as the middle
    answer -- reported, kept -- because reporting an entry costs nothing and
    deleting the wrong one is unrecoverable."""
    assert ops.classify_hook(command) == kind


def test_a_command_that_merely_mentions_agr_is_kept_and_flagged(install,
                                                                settings_file):
    wrapped = "sh -c '$HOME/.local/bin/agr status active'"
    settings_file({"hooks": {"Stop": [
        {"hooks": [{"type": "command", "command": wrapped}]}]}})
    _code, text = install()
    settings = read_json(settings_file.path)
    assert wrapped in commands_of(settings, "Stop")
    assert "check by hand" in text


def test_a_neighbour_whose_name_merely_contains_agr_is_untouched(
        install, settings_file):
    """`agrep` and `/opt/agrippa/run` are somebody else's tooling. A substring
    predicate would delete both, silently."""
    keep = ["/usr/bin/agrep -q pattern file", "/opt/agrippa/run --now"]
    settings_file({"hooks": {"Stop": [
        {"hooks": [{"type": "command", "command": c} for c in keep]}]}})
    _code, text = install()
    settings = read_json(settings_file.path)
    for command in keep:
        assert command in commands_of(settings, "Stop")
    assert "check by hand" not in text


def test_an_emptied_group_carrying_anything_undocumented_is_kept(
        install, settings_file):
    """A matcher group emptied by a removal is dropped only when its keys are
    exactly the documented `matcher`/`hooks`. Anything else is kept with an
    empty hook list, which is inert -- and readable by whoever put it there."""
    settings_file({"hooks": {"Stop": [
        {"matcher": "x", "comment": "mine",
         "hooks": [{"type": "command",
                    "command": "/usr/bin/agr status completed"}]}]}})
    install()
    settings = read_json(settings_file.path)
    survivor = [g for g in settings["hooks"]["Stop"] if "comment" in g]
    assert len(survivor) == 1
    assert survivor[0]["hooks"] == []
    assert survivor[0]["comment"] == "mine"


# ---------------------------------------------------------------------------
# refusals: never rewrite what could not be read
# ---------------------------------------------------------------------------

def test_malformed_json_aborts_and_leaves_the_file_byte_for_byte(
        agb, install, settings_file):
    """A tool that "repairs" a settings file by overwriting the parts it could
    not parse destroys settings it never read."""
    settings_file("{ this is not json ")
    with open(settings_file.path, "rb") as handle:
        before = handle.read()
    with pytest.raises(agb.AgbError) as excinfo:
        install()
    with open(settings_file.path, "rb") as handle:
        assert handle.read() == before
    assert "not valid JSON" in str(excinfo.value)
    assert not os.path.exists(settings_file.path + ".agb.bak")


@pytest.mark.parametrize("data", [
    '[]',
    '"a string"',
    '{"hooks": []}',
    '{"hooks": {"Stop": {}}}',
    '{"hooks": {"Stop": ["not a group"]}}',
    '{"hooks": {"Stop": [{"hooks": "not a list"}]}}',
    '{"hooks": {"Stop": [{"hooks": ["not an entry"]}]}}',
])
def test_a_settings_file_of_the_wrong_shape_aborts_untouched(agb, install,
                                                             settings_file,
                                                             data):
    settings_file(data)
    with open(settings_file.path, "rb") as handle:
        before = handle.read()
    with pytest.raises(agb.AgbError):
        install()
    with open(settings_file.path, "rb") as handle:
        assert handle.read() == before


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores the mode bits")
def test_an_unreadable_settings_file_aborts_rather_than_replacing_it(
        agb, install, settings_file):
    settings_file({"hooks": LIVE_AGR})
    os.chmod(settings_file.path, 0o000)
    try:
        with pytest.raises(agb.AgbError) as excinfo:
            install()
    finally:
        os.chmod(settings_file.path, 0o600)
    assert "cannot read" in str(excinfo.value)


def test_a_missing_settings_file_is_created_along_with_its_directory(
        ops, install, settings_file, tmp_path):
    fresh = str(tmp_path / "nowhere" / "deeper" / "settings.json")
    code, text = install("--settings", fresh)
    assert code == 0
    assert "does not exist yet" in text
    assert commands_of(read_json(fresh), "Stop")
    assert not os.path.exists(fresh + ops.BACKUP_SUFFIX)


def test_nothing_is_written_when_the_hook_command_cannot_be_run(
        agb, install, settings_file, runner):
    """It **probes** rather than checks existence. `agr` failed silently in five
    stacked places and every one would have passed an existence check, so a hook
    command is written only once it has been *run* -- a broken one fails before
    `agb` starts and leaves no trace at all."""
    settings_file({"hooks": LIVE_AGR})
    with open(settings_file.path, "rb") as handle:
        before = handle.read()
    runner.code = 1
    runner.out = ""
    runner.err = "python3: cannot open file"
    with pytest.raises(agb.AgbError) as excinfo:
        install()
    with open(settings_file.path, "rb") as handle:
        assert handle.read() == before
    assert "Nothing is installed" in str(excinfo.value)


def test_a_probe_that_answers_the_wrong_version_is_a_refusal(agb, install,
                                                             runner):
    """Exit 0 is not the answer -- the *output* is. A stale `agb` on the path
    would exit 0 and wire a tool that speaks a different wire format."""
    runner.out = "agb 0.0.1-ancient\n"
    with pytest.raises(agb.AgbError):
        install()


def test_the_probe_runs_the_exact_hook_invocation(install, runner, agb_path):
    """One round trip proves the interpreter runs, that it accepts `-S -E`, and
    that it can find and execute *this* `agb` -- the three things whose failure
    would otherwise show up as a hook that quietly does nothing."""
    install()
    assert runner.calls == [[sys.executable, "-S", "-E", agb_path, "version"]]


# ---------------------------------------------------------------------------
# the interpreter predicate -- constraint #14, and what it is NOT
# ---------------------------------------------------------------------------

def test_a_relative_interpreter_is_refused_with_the_reason(agb, ops):
    with pytest.raises(agb.AgbError) as excinfo:
        ops.check_interpreter("python3")
    assert "absolute path" in str(excinfo.value)


def test_a_nonexistent_interpreter_is_refused(agb, ops):
    with pytest.raises(agb.AgbError):
        ops.check_interpreter("/nonexistent/python3")


def test_a_non_executable_interpreter_is_refused(agb, ops, tmp_path):
    path = tmp_path / "python3"
    path.write_text("#!/bin/sh\n")
    os.chmod(str(path), 0o644)
    with pytest.raises(agb.AgbError):
        ops.check_interpreter(str(path))
    assert ops.check_interpreter(sys.executable) == sys.executable


def test_the_predicate_is_the_same_path_on_every_host_not_a_shared_mount(ops):
    """⚠️ Constraint #14's exact wording, because the plausible-sounding wrong
    predicate is expensive: requiring a *shared mount* would push toward an NFS
    interpreter and undo the entire hot-path budget. `sys.executable` here is a
    local path and almost certainly right on a uniform farm; `--python` is the
    per-host override."""
    note = ops.INSTALL_PORTABILITY_NOTE
    assert "same absolute path on every host" in note
    assert "shared mount" in note
    assert "--python" in note


def test_the_note_is_printed_on_every_run_rather_than_buried(install):
    _code, text = install()
    assert "same absolute path on every host" in text
    _code, text = install()
    assert "same absolute path on every host" in text


def test_the_installer_never_asks_whether_the_interpreter_is_on_a_mount(
        all_trees):
    """The structural half: `agb_ops` has a whole mount-table parser one
    screen away (`doctor` prints the mount options), so "check it is on a shared
    mount" is a two-line change. It must not be reachable from here."""
    funcs = conftest.functions(*all_trees)
    reachable = conftest.reachable_from(funcs, "run_install_hooks")
    assert "check_interpreter" in reachable          # the walk really ran
    assert "mount_for_path" not in reachable
    assert "read_mount_table" not in reachable
    assert "parse_mount_table" not in reachable


# ---------------------------------------------------------------------------
# the file it edits: backup, mode, and never the real one
# ---------------------------------------------------------------------------

def test_the_previous_file_is_backed_up_before_it_is_replaced(
        ops, install, settings_file):
    """The file is live and hand-edited. One copy of what was there costs
    nothing and is the difference between a mistake and a loss."""
    settings_file({"hooks": LIVE_AGR, "theme": "dark"})
    with open(settings_file.path, "rb") as handle:
        before = handle.read()
    _code, text = install()
    backup = settings_file.path + ops.BACKUP_SUFFIX
    assert "backup:" in text
    with open(backup, "rb") as handle:
        assert handle.read() == before


def test_the_mode_of_an_existing_settings_file_is_preserved(install, ops,
                                                            settings_file):
    """Silently tightening somebody's dotfile to 0600 is a surprise, and this
    command has no business having an opinion about it.

    0664, not 0644: the bit that matters is **group write**, which the default
    umask (0022) strips. `atomic_write` creates its temp with `os.open(...,
    mode)`, and O_CREAT's mode is filtered through the umask -- so a test that
    only ever asks for 0644 under umask 0022 passes whether or not the mode is
    preserved at all, and this claim went unverified until it was written this
    way. The backup gets the same treatment for the same reason.
    """
    settings_file({"hooks": {}})
    os.chmod(settings_file.path, 0o664)
    old = os.umask(0o022)
    try:
        install()
    finally:
        os.umask(old)
    assert stat.S_IMODE(os.stat(settings_file.path).st_mode) == 0o664
    backup = settings_file.path + ops.BACKUP_SUFFIX
    assert stat.S_IMODE(os.stat(backup).st_mode) == 0o664


def test_the_settings_path_is_derived_from_home_so_the_fixture_can_move_it(
        ops, fake_home):
    """⚠️ The single most important assertion in this file. `agb.home_dir()`
    reads `$HOME`, which the autouse `fake_home` fixture moves -- and that is
    the only thing standing between a bug in this command and the developer's
    real, live `~/.claude/settings.json`. `os.path.expanduser` would consult
    `pwd` on some paths and walk straight past the fixture."""
    assert ops.settings_path() == os.path.join(str(fake_home), ".claude",
                                               "settings.json")
    assert str(fake_home) in ops.settings_path()


def test_the_settings_seam_exists_across_a_subprocess_boundary(ops):
    """`monkeypatch` cannot cross into a subprocess, so the *option* is the
    seam. Without it the end-to-end tests below would run against the real
    file."""
    assert "--settings" in ops.INSTALL_VALUE_ARGS


def test_the_real_settings_file_is_not_touched_by_a_default_run(
        ops, agb, fake_home, sd, agb_path, runner, set_host):
    """The default path -- no `--settings` at all -- exercised with `$HOME`
    moved. Anything that reached the real file would have to bypass
    `agb.home_dir()` to do it, and this is the test that would notice."""
    set_host(HOST)
    before = None
    if os.path.exists(REAL_SETTINGS):
        with open(REAL_SETTINGS, "rb") as handle:
            before = handle.read()
    out = Out()
    code = ops.run_install_hooks(["--statedir", sd, "--python", sys.executable,
                                  "--agb", agb_path], out=out, run=runner)
    assert code == 0
    assert os.path.exists(os.path.join(str(fake_home), ".claude",
                                       "settings.json"))
    if before is None:
        assert not os.path.exists(REAL_SETTINGS)
    else:
        with open(REAL_SETTINGS, "rb") as handle:
            assert handle.read() == before


# ---------------------------------------------------------------------------
# argument parsing and --dry-run
# ---------------------------------------------------------------------------

def test_dry_run_reports_everything_and_writes_nothing(install,
                                                       settings_file):
    settings_file({"hooks": LIVE_AGR})
    with open(settings_file.path, "rb") as handle:
        before = handle.read()
    code, text = install("--dry-run")
    assert code == 0
    assert "removed:" in text and "command:" in text
    assert "dry run: nothing was written" in text
    assert "wrote:" not in text
    with open(settings_file.path, "rb") as handle:
        assert handle.read() == before


@pytest.mark.parametrize("argv", [
    ["--settings"],
    ["--python"],
    ["--bogus"],
    ["extra-argument"],
    ["--settings="],
])
def test_bad_arguments_are_refused_with_the_reason(agb, ops, argv):
    with pytest.raises(agb.AgbError):
        ops.parse_install_args(argv)


def test_both_option_spellings_parse(ops):
    inline = ops.parse_install_args(["--settings=/a/b", "--statedir=/c/d"])
    spaced = ops.parse_install_args(["--settings", "/a/b", "--statedir",
                                     "/c/d"])
    assert inline == spaced
    assert inline["settings"] == "/a/b"
    assert inline["dry_run"] is False


def test_a_relative_agb_path_or_statedir_is_refused(agb, ops, runner,
                                                    settings_file):
    """A hook runs with no reliable working directory: a relative path in the
    command would resolve against whatever Claude Code happened to be in."""
    for extra in ({"agb": "agb"}, {"statedir": "state"}):
        opts = {"settings": settings_file.path, "statedir": None,
                "python": sys.executable, "agb": None, "dry_run": False}
        opts.update(extra)
        opts.setdefault("statedir", None)
        if opts["statedir"] is None:
            opts["statedir"] = "/tmp/agb-state"
        with pytest.raises(agb.AgbError) as excinfo:
            ops.install_settings(opts)
        assert "absolute" in str(excinfo.value)


# ---------------------------------------------------------------------------
# end to end -- through the real door, and then through /bin/sh
# ---------------------------------------------------------------------------

def test_installing_end_to_end_through_the_real_dispatch(run_agb, sd,
                                                         settings_file,
                                                         set_host, agb_path):
    """`agb install-hooks` -> `main` -> `cmd_ops` -> `agb_ops.run_ops`, with a
    real interpreter probe rather than an injected one."""
    settings_file({"hooks": LIVE_AGR})
    rc, out, err = run_agb(["install-hooks", "--settings", settings_file.path,
                            "--statedir", sd, "--agb", agb_path],
                           env={"AGB_HOST": HOST})
    assert (rc, err) == (0, b"")
    assert b"wrote:" in out
    settings = read_json(settings_file.path)
    assert len(commands_of(settings, "Stop")) == 1
    rc, out, err = run_agb(["install-hooks", "--settings", settings_file.path,
                            "--statedir", sd, "--agb", agb_path],
                           env={"AGB_HOST": HOST})
    assert (rc, err) == (0, b"")
    assert b"unchanged:" in out


def test_a_malformed_file_end_to_end_exits_one_and_says_why(run_agb, sd,
                                                            settings_file,
                                                            agb_path):
    settings_file("}{")
    rc, out, err = run_agb(["install-hooks", "--settings", settings_file.path,
                            "--statedir", sd, "--agb", agb_path],
                           env={"AGB_HOST": HOST})
    assert rc == 1
    assert b"not valid JSON" in err


def test_the_installed_command_actually_records_a_transition(
        agb, run_agb, sd, settings_file, agb_path, set_agent_pid):
    """⚠️ The payoff, and the only test here that proves the *string* is right
    rather than merely well-formed: the command the installer wrote is handed to
    `/bin/sh` exactly as Claude Code would run it, and a session must appear in
    the statedir.

    Everything else in this file could pass with a command that dies before
    `agb` starts -- which is the failure mode constraint #14 exists for, and the
    one that leaves no breadcrumb because nothing ever ran.
    """
    settings_file({})
    rc, _out, err = run_agb(["install-hooks", "--settings", settings_file.path,
                             "--statedir", sd, "--agb", agb_path],
                            env={"AGB_HOST": HOST})
    assert rc == 0, err

    command = commands_of(read_json(settings_file.path), "UserPromptSubmit")[0]
    environ = dict(os.environ)
    environ["AGB_HOST"] = HOST
    environ.pop("AGB_STATEDIR", None)          # the command carries its own
    proc = subprocess.Popen(["/bin/sh", "-c", command], env=environ,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE)
    out, err = conftest.communicate(proc, b'{"hook_event_name":"UserPromptSubmit"}')
    assert (proc.returncode, out) == (0, b""), err

    states = [name for name in os.listdir(agb.session_dir(sd, HOST))
              if name.endswith(".state")]
    assert len(states) == 1
    with open(os.path.join(agb.session_dir(sd, HOST), states[0])) as handle:
        assert handle.read().splitlines()[0] == "active"


# ---------------------------------------------------------------------------
# structural guards
# ---------------------------------------------------------------------------

def test_install_hooks_is_reached_through_the_one_shared_operator_door(
        agb_tree, agb):
    """Task 6b's consolidation, collected for the last time: `install-hooks`
    cost `agb` no bytes at all -- no `cmd_install_hooks`, no new dispatch
    line."""
    funcs = conftest.functions(agb_tree)
    assert "install-hooks" in agb.OPS_COMMANDS
    assert "cmd_install_hooks" not in funcs
    assert "run_install_hooks" not in funcs


def test_every_name_in_the_table_is_now_implemented(agb, ops_tree):
    """The companion to `test_known_but_unimplemented_command_is_explicit`:
    that one proves the "not implemented yet" answer still exists, this one
    proves no name currently in `OPS_COMMANDS` can reach it.

    Read off `run_ops`' own dispatch rather than by running five commands,
    because running them needs five different environments and the question here
    is only whether the door knows the name. A table entry with no arm is the
    failure this catches -- `agb` would route it and the sibling would answer
    "not implemented yet" for a command that shipped.
    """
    dispatched = set()
    for node in ast.walk(conftest.functions(ops_tree)["run_ops"]):
        if (isinstance(node, ast.Compare) and isinstance(node.left, ast.Name)
                and node.left.id == "name"):
            for other in node.comparators:
                if isinstance(other, ast.Str):
                    dispatched.add(other.s)
    assert dispatched == set(agb.OPS_COMMANDS)


def test_no_hook_path_function_can_reach_the_installer(all_trees):
    """It imports json, spawns a subprocess and rewrites a file in `$HOME`.
    None of that may be one call away from a hook."""
    funcs = conftest.functions(*all_trees)
    reachable = conftest.reachable_from(funcs, "cmd_hook")
    assert "hook_apply" in reachable                  # the walk really ran
    for name in ("run_install_hooks", "install_settings", "hook_command",
                 "read_settings", "write_settings", "_probe_run"):
        assert name not in reachable, name


def test_the_installer_writes_exactly_one_file_and_only_through_agb(
        all_trees):
    """`agb.atomic_write` -- temp + rename -- because a torn `settings.json` is
    a Claude Code that cannot start, and the file is being read by the very
    session running this command. An `open(..., "w")` here would be the
    truncate window applied to the one file nobody can afford to lose."""
    funcs = conftest.functions(*all_trees)
    reachable = conftest.reachable_from(funcs, "run_install_hooks")
    writers = set()
    for name in reachable:
        for base, attr in conftest.calls(funcs[name]):
            if attr in ("atomic_write", "write_in_place"):
                writers.add((name, attr))
            assert (base, attr) != (None, "open"), name
    assert writers == set([("write_settings", "atomic_write")])


def test_the_installer_removes_nothing_from_the_statedir(all_trees):
    """It creates the statedir and stops there. `install-hooks` is not a
    terminal path -- `prune` is -- and an unlink reachable from an *installer*
    is how a re-install becomes destructive.

    Stated as "the only reachable unlink is the shared atomic write's own temp
    cleanup" rather than as "no unlink at all": `atomic_write` removes its temp
    when a rename fails, and a guard phrased loosely enough to trip on that
    would have to be relaxed rather than fixed the first time it fired.
    """
    funcs = conftest.functions(*all_trees)
    reachable = conftest.reachable_from(funcs, "run_install_hooks")
    unlinkers = set(name for name in reachable
                    if ("os", "unlink") in conftest.calls(funcs[name]))
    assert unlinkers == set(["_unlink_quiet"])
    callers = set(name for name in reachable
                  if "_unlink_quiet" in [a for _b, a
                                         in conftest.calls(funcs[name])])
    assert callers == set(["atomic_write"])
    for name in reachable:
        bare = [attr for _base, attr in conftest.calls(funcs[name])]
        for forbidden in ("reap_entry", "prune_remove", "sweep_host",
                          "sweep_entry", "rebuild_marker"):
            assert forbidden not in bare, (name, forbidden)


def test_a_statedir_that_cannot_be_created_does_not_block_the_install(
        install, settings_file, monkeypatch, agb):
    """A sulking NFS mount now is no reason to leave Claude Code unwired: the
    first hook creates the statedir on its own transition anyway. Reported, not
    fatal -- and reported as the *transient* it is."""
    def boom(path=None):
        raise agb.AgbError("nfs is having a day")

    monkeypatch.setattr(agb, "ensure_statedir", boom)
    code, text = install()
    assert code == 0
    assert "NOT ready" in text and "the first hook will try again" in text
    assert "No hook will ever repair this" not in text
    assert commands_of(read_json(settings_file.path), "Stop")


def test_an_unusable_existing_statedir_is_not_promised_a_retry(
        install, settings_file, monkeypatch, agb, tmp_path):
    """The other half of the same exception, and the reason it had to be split.
    `verify_statedir` refuses a wrong-mode or wrong-owner directory on EVERY
    call, so "the first hook will try again" was a promise nothing could keep:
    every hook would no-op for ever with that line as the only clue. The
    statedir here EXISTS, which is what tells the two cases apart."""
    sd = tmp_path / "statedir-0777"
    sd.mkdir(mode=0o777)
    os.chmod(str(sd), 0o777)

    def boom(path=None):
        raise agb.AgbError("statedir %s has mode 0777, expected 0700" % (sd,))

    monkeypatch.setattr(agb, "ensure_statedir", boom)
    code, text = install("--statedir", str(sd))
    assert code == 0
    assert "NOT usable" in text
    assert "No hook will ever repair this" in text
    assert "chmod 0700 %s" % (sd,) in text
    assert "will try again" not in text
    # Still installed: the hooks are correct, it is the directory that is not.
    assert commands_of(read_json(settings_file.path), "Stop")


@pytest.mark.parametrize("shape,unrepairable", [
    ("dir-0700", False),        # exactly right
    ("dir-0755", True),         # somebody else can read it: refused for ever
    ("dir-0777", True),
    ("file", True),             # exists and is not a directory
    ("missing", False),         # the first hook creates it
])
def test_only_the_permanent_refusals_are_called_unrepairable(ops, tmp_path,
                                                             shape,
                                                             unrepairable):
    """A false "this will never work" is as bad as the false "it will retry"
    the split exists to remove, so the transient shapes must answer False."""
    path = tmp_path / "sd"
    if shape.startswith("dir-"):
        path.mkdir()
        os.chmod(str(path), int(shape[4:], 8))
    elif shape == "file":
        path.write_text("not a directory")
    assert ops.statedir_is_unrepairable(str(path)) is unrepairable


def test_the_operator_file_carries_the_installer_and_agb_stayed_under_the_cap(
        ops_source, agb_source):
    """Task 9a added the last command in the table and `agb` grew nothing."""
    assert "install-hooks" in ops_source
    assert len(ops_source) > 85000
    assert len(agb_source) < conftest.AGB_PARSE_BUDGET
