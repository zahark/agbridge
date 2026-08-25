"""`agb-claude` -- the wrapper that starts Claude Code in a named tmux session.

Driven through a recording `tmux` stub rather than a real server: what matters
is the argv it constructs, and a real tmux would make that invisible.
"""

import os
import subprocess

import pytest

import conftest


SCRIPT = os.path.join(conftest.REPO_ROOT, "agb-claude")


@pytest.fixture
def wrapper(tmp_path):
    """`agb-claude` with recording `tmux` and `claude` stubs on $PATH."""
    binder = tmp_path / "bin"
    binder.mkdir()
    work = tmp_path / "work"
    work.mkdir()
    log = tmp_path / "tmux.log"

    (binder / "tmux").write_text(
        "#!/bin/sh\n"
        "{ for a in \"$@\"; do printf '%s\\037' \"$a\"; done; printf '\\n'; }"
        " >> \"" + str(log) + "\"\n"
        "case \"$1\" in has-session) exit ${AGBC_HAS_SESSION:-1} ;; esac\n"
        "exit 0\n")
    (binder / "claude").write_text("#!/bin/sh\nexit 0\n")
    # A stand-in launcher for AGB_CLAUDE_CUSTOM, recording its argv so a test
    # can assert what the eval'ed command line actually resolved to -- the
    # nesting is the whole point of the placeholders, and a string comparison
    # against the pre-mint would not see it.
    submit_log = tmp_path / "submit.log"
    (binder / "submit").write_text(
        "#!/bin/sh\n"
        "{ for a in \"$@\"; do printf '%s\\037' \"$a\"; done; printf '\\n'; }"
        " >> \"" + str(submit_log) + "\"\nexit 0\n")
    # An `agb` that records being called. The wrapper must NEVER reach it: the
    # pre-mint belongs inside the new session, where the pane anchor is right.
    agb_log = tmp_path / "agb.log"
    (binder / "agb").write_text(
        "#!/bin/sh\n"
        "{ for a in \"$@\"; do printf '%s\\037' \"$a\"; done; printf '\\n'; }"
        " >> \"" + str(agb_log) + "\"\nexit 0\n")
    for name in ("tmux", "claude", "agb", "submit"):
        os.chmod(str(binder / name), 0o755)

    class Wrapper(object):
        cwd = str(work)

        def run(self, args, inside_tmux=False, has_session=False,
                custom=None):
            env = dict(os.environ)
            env["PATH"] = str(binder) + os.pathsep + env.get("PATH", "")
            # ⚠️ Popped unconditionally, not merely left alone. A developer who
            # actually uses AGB_CLAUDE_CUSTOM would otherwise take it into every
            # test in this file, and the ones that pin the DEFAULT path would
            # fail on their machine and nowhere else.
            env.pop("AGB_CLAUDE_CUSTOM", None)
            if custom is not None:
                env["AGB_CLAUDE_CUSTOM"] = custom
            env["TMUX"] = "/tmp/fake,1,0" if inside_tmux else ""
            env["AGBC_HAS_SESSION"] = "0" if has_session else "1"
            if not inside_tmux:
                env.pop("TMUX", None)
            proc = subprocess.Popen(["sh", SCRIPT] + list(args), cwd=str(work),
                                    env=env, stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE)
            out, err = conftest.communicate(proc, b"")
            return proc.returncode, out.decode(), err.decode()

        def calls(self):
            if not log.exists():
                return []
            return [line.split("\037")[:-1]
                    for line in log.read_text().splitlines() if line]

        def new_session(self):
            return [c for c in self.calls() if "new-session" in c]

        def agb_calls(self):
            if not agb_log.exists():
                return []
            return [line.split("\037")[:-1]
                    for line in agb_log.read_text().splitlines() if line]

        def premint(self):
            call = self.new_session()[0]
            return [w for w in call if "agb hook" in w][0]

        def run_premint(self):
            """Execute the command tmux was handed and return the launcher's
            argv. A string assertion cannot see whether the nesting survived --
            `eval` decides that at run time, inside the session."""
            env = dict(os.environ)
            env["PATH"] = str(binder) + os.pathsep + env.get("PATH", "")
            proc = subprocess.Popen(["sh", "-c", self.premint(), "agb-claude"],
                                    cwd=str(work), env=env,
                                    stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE)
            conftest.communicate(proc, b"")
            if not submit_log.exists():
                return []
            return [line.split("\037")[:-1]
                    for line in submit_log.read_text().splitlines() if line]

    return Wrapper()


@pytest.mark.parametrize("inside", [False, True])
def test_passthrough_args_come_after_claude_not_before(wrapper, inside):
    """`--` args belong to `claude`, not to tmux. Placed before it, tmux reads
    the first one as its own option or as the command to run -- so
    `agb-claude x -- --resume <id>` silently starts the wrong thing.

    Regression guard: the in-tmux branch had them the wrong way round while the
    other branch was right, so the bug only appeared when the wrapper was run
    from inside tmux.
    """
    wrapper.run(["work", "--", "--resume", "abc123"], inside_tmux=inside)
    argv = wrapper.new_session()[0]
    # The command tmux runs is now `sh -c <premint> agb-claude <args...>`, so
    # the passthrough sits after the inner shell's $0 rather than after the
    # literal word `claude`. The property is unchanged and is the one that
    # actually broke once: anything placed BEFORE the command is read by tmux
    # as its own option or as the thing to run.
    assert argv[-3:] == ["agb-claude", "--resume", "abc123"]
    assert argv.index("sh") < argv.index("agb-claude"), argv


@pytest.mark.parametrize("inside", [False, True])
def test_the_session_is_named_and_started_in_the_current_directory(wrapper,
                                                                   inside):
    wrapper.run(["my-task"], inside_tmux=inside)
    argv = wrapper.new_session()[0]
    assert "my-task" in argv
    assert wrapper.cwd in argv


def test_a_bare_option_is_refused_with_the_fix_in_the_message(wrapper):
    """`agb-claude --resume <id>` is the natural thing to try, so the refusal
    has to name `--` rather than just say 'unknown option'."""
    code, out, err = wrapper.run(["--resume", "abc123"])
    assert code != 0
    assert "--resume" in err
    assert "--" in err and "claude" in err


def test_two_names_are_refused(wrapper):
    code, _out, err = wrapper.run(["one", "two"])
    assert code != 0
    assert "only one name" in err


@pytest.mark.parametrize("inside", [False, True])
def test_an_existing_session_is_joined_and_never_restarted(wrapper, inside):
    """Re-running must not start a second agent in the same session."""
    code, out, _err = wrapper.run(["work"], inside_tmux=inside,
                                  has_session=True)
    assert code == 0
    assert wrapper.new_session() == []
    assert "existing session" in out


@pytest.mark.parametrize("inside", [False, True])
def test_arguments_to_an_existing_session_are_reported_as_ignored(wrapper,
                                                                  inside):
    """Nothing is restarted, so `--resume <id>` cannot take effect. Saying so is
    the difference between a no-op and a silent one."""
    _code, out, _err = wrapper.run(["work", "--", "--resume", "abc123"],
                                   inside_tmux=inside, has_session=True)
    assert "ignored" in out
    assert "--resume abc123" in out


def test_names_tmux_cannot_address_are_rewritten(wrapper):
    """tmux targets are delimited by `.` and `:`, so a name carrying either
    cannot be reached by `attach-session -t`."""
    wrapper.run(["my.proj:v2"], inside_tmux=False)
    argv = wrapper.new_session()[0]
    assert "my-proj-v2" in argv


# ---------------------------------------------------------------------------
# -d: a row without attaching
# ---------------------------------------------------------------------------

def test_detach_starts_the_session_in_the_background(wrapper):
    """`-d` returns immediately, and the row no longer waits on a prompt.

    It used to hand claude an opening "hi" purely so a hook would fire and the
    row would exist. The session's own shell mints the row now, so a detached
    start spends no turn and no API call unless a greeting was actually asked
    for -- and, more importantly, a row appears even when claude never gets as
    far as a hook.
    """
    code, out, _err = wrapper.run(["-d", "work"])
    assert code == 0
    argv = wrapper.new_session()[0]
    assert "-d" in argv                      # tmux's detach flag
    assert argv[-1] == "agb-claude"          # nothing after the inner $0
    assert "hi" not in argv
    assert "detached" in out


def test_the_greeting_is_the_last_word_after_claudes_own_options(wrapper):
    """`claude [options] [prompt]` -- a prompt placed before the options would
    be read as one."""
    wrapper.run(["-d", "work", "--greet", "say OK", "--", "--model", "opus"])
    argv = wrapper.new_session()[0]
    assert argv[-1] == "say OK"
    assert argv[-3:] == ["--model", "opus", "say OK"]


def test_detach_never_attaches_or_switches(wrapper):
    """The whole point: it returns immediately."""
    wrapper.run(["-d", "work"])
    verbs = [c[0] for c in wrapper.calls()]
    assert "attach-session" not in verbs
    assert "switch-client" not in verbs


def test_detach_does_not_start_a_second_agent_in_an_existing_session(wrapper):
    code, out, _err = wrapper.run(["-d", "work"], has_session=True)
    assert code == 0
    assert wrapper.new_session() == []
    assert "already exists" in out


def test_greet_without_detach_is_refused(wrapper):
    """Without `-d` you are about to type in the session anyway, so a greeting
    would be a silently ignored argument."""
    code, _out, err = wrapper.run(["work", "--greet", "hello"])
    assert code != 0
    assert "-d" in err


def test_greet_needs_a_value(wrapper):
    code, _out, err = wrapper.run(["-d", "work", "--greet"])
    assert code != 0
    assert "needs a value" in err


# ---------------------------------------------------------------------------
# the pre-mint: the row exists before claude does
# ---------------------------------------------------------------------------
# ⚠️ Every property below was measured before it was written, and each one is
# load-bearing in a way that is invisible from reading the line. Getting any of
# them wrong yields TWO rows instead of one -- a stranded marker plus claude's
# own -- which is exactly the collision `agb-ralphex` has to use a separate tmux
# session to avoid.

def _premint(argv):
    """The inner `sh -c` script tmux is told to run."""
    for word in argv:
        if "agb hook" in word:
            return word
    return ""


@pytest.mark.parametrize("args", [["work"], ["-d", "work"],
                                  ["work", "--", "--model", "opus"]])
def test_every_launch_path_mints_the_row_first(wrapper, args):
    """Three branches start a session -- detached, inside tmux, and plain -- and
    a pre-mint missing from any one of them is a row that silently never
    appears on that path."""
    wrapper.run(args)
    argv = wrapper.new_session()[0]
    assert "agb hook" in _premint(argv), argv


def test_the_premint_runs_inside_the_new_session(wrapper):
    """The anchor is (host, tmux server pid, %PANE). Hooking from the CALLER's
    pane would mint a row for the caller's pane -- a row pointing at the wrong
    terminal, which is worse than no row at all."""
    wrapper.run(["work"])
    argv = wrapper.new_session()[0]
    # The hook is part of the command tmux is asked to RUN...
    assert "new-session" in argv
    assert "agb hook" in _premint(argv)
    # ...and the wrapper never runs `agb` itself. This is the assertion that
    # matters: hooking here would be the obvious simplification, it would
    # appear to work, and every row would point at the terminal you launched
    # from instead of the one the agent is in.
    assert wrapper.agb_calls() == [], wrapper.agb_calls()


def test_the_premint_carries_its_own_pid_so_claude_adopts_the_row(wrapper):
    """`exec` preserves BOTH pid and starttime, so the identity the shell
    records IS claude's a moment later, and `bind_key` adopts rather than
    minting a second key.

    Two ways to get this wrong, both of which produce a stranded row: drop the
    `exec` (claude becomes a child with a different pid), or drop
    `AGB_AGENT_PID=$$` (the walk finds no agent, the entry is written with no
    pid, and nothing but `agb prune` can ever remove it if claude fails to
    start).
    """
    wrapper.run(["work"])
    script = _premint(wrapper.new_session()[0])
    assert "AGB_AGENT_PID=$$" in script, script
    assert "exec claude" in script, script


def test_the_premint_state_is_completed_not_active(wrapper):
    """A session sitting at an empty prompt is waiting for you, which is what
    the `completed` glyph means. `active` would claim it is working and blink a
    transition that never happened.

    It also raises no banner: the finished-turn banner measures from a
    preceding `active`, and a freshly minted key has none.

    ⚠️ **A Mac-side config key now depends on this line, so it is a
    cross-file agreement and not only a rendering choice.**
    `notify_on_new_row = completed` means *announce the sessions I started
    with `agb-claude`, not every `claude` on the cluster*, and the only thing
    that distinguishes them is that this premint says `completed` while a bare
    `claude`'s first hook can only be `active`. Changing the word here does not
    break a test over there -- it silently inverts which sessions announce
    themselves -- so this test is where that cost has to be visible.
    `agb_mac.parse_new_row_states` carries the other half of the reasoning.
    """
    wrapper.run(["work"])
    script = _premint(wrapper.new_session()[0])
    assert "hook completed" in script, script
    assert "hook active" not in script, script


def test_a_broken_agb_costs_a_row_and_never_a_claude(wrapper):
    """Best-effort by construction. `;` rather than `&&`, so a missing or
    failing `agb` still reaches `exec claude` -- the wrapper's job is to start
    an agent, and a sidebar row is not worth refusing to."""
    wrapper.run(["work"])
    script = _premint(wrapper.new_session()[0])
    assert "&&" not in script, script
    assert script.index("agb hook") < script.index("exec claude")


# --------------------------------------------------------------------------
# AGB_CLAUDE_CUSTOM -- the claude command line, replaced wholesale, so the
# agent can be started through a scheduler, container or pool launcher whose
# spelling is site-specific and cannot live in a file that ships publicly.
#
# ⚠️ It is NOT the Codex feature with a different name. Codex fires no agbridge
# hooks, so a pre-minted row is the only row it will ever have. Claude hooks
# from wherever it actually runs, which is why `{env}` exists and why the tests
# below care about it.
# --------------------------------------------------------------------------

NESTED = 'submit -q big -I "{env} claude {}"'


def test_a_custom_command_replaces_claude_entirely(wrapper):
    wrapper.run(["-d", "bot"], custom='submit -I "claude"')
    premint = wrapper.premint()
    assert "exec claude" not in premint, premint
    assert "eval exec" in premint, premint


def test_the_custom_command_is_embedded_not_inherited(wrapper):
    """⚠️ It reaches the session as part of the command tmux is HANDED. A
    session created against an already-running tmux server takes its
    environment from the SERVER's, plus `update-environment` -- so a variable
    exported a moment ago in the caller's shell is simply not there, and a
    version relying on inheritance would exec nothing on every machine where a
    server was already up."""
    wrapper.run(["-d", "bot"], custom='submit -I "claude"')
    assert 'submit -I "claude"' in " ".join(wrapper.new_session()[0])


def test_switches_reach_the_agent_through_the_placeholder(wrapper):
    wrapper.run(["-d", "bot", "--", "--model", "opus", "--resume", "abc-123"],
                custom=NESTED)
    argv = wrapper.run_premint()
    assert len(argv) == 1 and argv[0][:3] == ["-q", "big", "-I"], argv
    assert argv[0][3].endswith("claude --model opus --resume abc-123"), argv


def test_the_env_placeholder_spells_agbs_own_host(wrapper, agb):
    """⚠️ A cross-file agreement (CLAUDE.md invariant 14). A POSIX-sh wrapper
    cannot import `agb`, so it spells `own_host()`'s resolution itself. A
    disagreement raises no error: the remote agent reports a host the Mac has
    no mapping for, and you get a SECOND row rather than the one just minted."""
    wrapper.run(["-d", "bot"], custom=NESTED)
    assert "AGB_HOST=%s " % (agb.own_host(),) in wrapper.premint()


def test_the_env_placeholder_makes_the_remote_hook_adopt_rather_than_remint(wrapper):
    """`AGB_AGENT_PID=none` is the load-bearing half and the easy one to drop.
    `AGB_HOST` alone gets the anchor right, and then `bind_key` finds a pid that
    does not match, REPLACES the index and orphans the row this wrapper just
    minted. A pid-less hook adopts instead -- absence of evidence must never
    re-mint."""
    wrapper.run(["-d", "bot"], custom=NESTED)
    assert "AGB_AGENT_PID=none" in wrapper.premint()


def test_arguments_without_a_placeholder_are_refused_and_it_is_named(wrapper):
    code, _out, err = wrapper.run(["-d", "bot", "--", "--model", "opus"],
                                  custom='submit -I "claude"')
    assert code != 0
    assert "no {} placeholder" in err and "--model opus" in err
    assert wrapper.new_session() == []


def test_an_argument_that_would_change_the_parsing_is_refused(wrapper):
    for bad in ("hello world", 'a"b', "a$b", "a;b", "a`b`", "a'b"):
        code, _out, err = wrapper.run(["-d", "bot", "--", bad], custom=NESTED)
        assert code != 0, bad
        assert "verbatim" in err, (bad, err)
        assert wrapper.new_session() == [], bad


def test_greet_is_refused_with_a_custom_command(wrapper):
    """Prose always needs quoting, and `{}` splices verbatim -- so the one
    argument that could never go through the placeholder stays refused."""
    code, _out, err = wrapper.run(["-d", "bot", "--greet", "hi"], custom=NESTED)
    assert code != 0
    assert "--greet has nowhere to go" in err
    assert wrapper.new_session() == []


def test_a_custom_command_whose_program_is_missing_is_refused(wrapper):
    code, _out, err = wrapper.run(["-d", "bot"], custom='nosuchlauncher -I x')
    assert code != 0
    assert "nosuchlauncher" in err and "not on $PATH" in err
    assert wrapper.new_session() == []


def test_the_program_check_steps_over_leading_assignments(wrapper):
    code, _out, err = wrapper.run(["-d", "bot"], custom='{env} submit -I "claude"')
    assert code == 0, err
    assert wrapper.new_session() != []


def test_an_empty_variable_is_the_default_path(wrapper):
    code, _out, err = wrapper.run(["-d", "bot"], custom="")
    assert code == 0, err
    assert "exec claude" in wrapper.premint()


def test_the_premint_is_unchanged_by_a_custom_command(wrapper):
    wrapper.run(["-d", "bot"], custom=NESTED)
    premint = wrapper.premint()
    assert premint.startswith("AGB_AGENT_PID=$$ agb hook completed"), premint
    assert "&&" not in premint and "2>/dev/null" in premint, premint
    assert wrapper.agb_calls() == [], wrapper.agb_calls()


def test_the_remote_row_caveat_is_documented_loudly(wrapper):
    """The single most surprising thing about launching Claude elsewhere: it
    hooks from there, so without `{env}` you get a second row on a host the Mac
    cannot reach. The file must say so rather than leave it to be discovered."""
    body = open(SCRIPT, encoding="utf-8").read()
    head = body[:body.index("set -e")]
    assert "DIFFERS FROM CODEX" in head
    assert "second row" in head
    assert "agb prune" in head
