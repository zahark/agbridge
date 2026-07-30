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
    for name in ("tmux", "claude"):
        os.chmod(str(binder / name), 0o755)

    class Wrapper(object):
        cwd = str(work)

        def run(self, args, inside_tmux=False, has_session=False):
            env = dict(os.environ)
            env["PATH"] = str(binder) + os.pathsep + env.get("PATH", "")
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
    assert argv[argv.index("claude") + 1:] == ["--resume", "abc123"]


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
    """A row appears on the first HOOK, not at launch, so a detached session
    with nothing typed into it stays invisible. `-d` hands claude an opening
    prompt for exactly that reason."""
    code, out, _err = wrapper.run(["-d", "work"])
    assert code == 0
    argv = wrapper.new_session()[0]
    assert "-d" in argv                      # tmux's detach flag
    assert argv[-1] == "hi"                  # the prompt claude is given
    assert argv[argv.index("claude") + 1] == "hi"
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
