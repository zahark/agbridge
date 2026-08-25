"""`agb-tmux` -- the same wrapper for any command.

Driven through a recording `tmux` stub rather than a real server: what matters
is the argv it constructs, and a real tmux would make that invisible.

⚠️ Third of a family, and the general case: `agb-claude` and `agb-codex` exist
because they know their agent's name and its caveats, while this one runs
whatever it is given. Three near-copies is a lot, and if a fourth appears they
should be collapsed -- what stops that today is that the two agent wrappers are
live and verified, and rewriting a working thing for tidiness is the trade this
project keeps declining. What must NOT diverge is the pre-mint, which is what
the tests below pin.
"""

import os
import subprocess

import pytest

import conftest


SCRIPT = os.path.join(conftest.REPO_ROOT, "agb-tmux")


@pytest.fixture
def wrapper(tmp_path):
    """`agb-codex` with recording `tmux` and `claude` stubs on $PATH."""
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
    (binder / "sh_stub").write_text("#!/bin/sh\nexit 0\n")
    # An `agb` that records being called. The wrapper must NEVER reach it: the
    # pre-mint belongs inside the new session, where the pane anchor is right.
    agb_log = tmp_path / "agb.log"
    (binder / "agb").write_text(
        "#!/bin/sh\n"
        "{ for a in \"$@\"; do printf '%s\\037' \"$a\"; done; printf '\\n'; }"
        " >> \"" + str(agb_log) + "\"\nexit 0\n")
    for name in ("tmux", "sh_stub", "agb"):
        os.chmod(str(binder / name), 0o755)

    class Wrapper(object):
        cwd = str(work)

        def run(self, args, inside_tmux=False, has_session=False,
                no_codex=False):
            env = dict(os.environ)
            env["PATH"] = str(binder) + os.pathsep + env.get("PATH", "")
            env["TMUX"] = "/tmp/fake,1,0" if inside_tmux else ""
            env["AGBC_HAS_SESSION"] = "0" if has_session else "1"
            if no_codex:
                # ⚠️ Removing the stub is not enough: the REAL codex is on the
                # developer's $PATH, so the check would pass against it and the
                # test would prove nothing. The path is narrowed to the stub
                # directory plus the coreutils the script itself needs.
                os.remove(str(binder / "codex"))
                env["PATH"] = os.pathsep.join([str(binder), "/usr/bin", "/bin"])
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

    return Wrapper()


def test_the_default_command_is_the_login_shell(wrapper):
    """The common case, and the one that makes this useful: a farm shell you
    can click on from the sidebar."""
    wrapper.run(["-d", "bot"])
    call = wrapper.new_session()[0]
    assert any("exec \"$@\"" in w for w in call), call
    assert call[-1].endswith("sh") or "/" in call[-1], call


def test_a_given_command_is_what_runs(wrapper):
    wrapper.run(["-d", "bot", "--", "make", "-j8"])
    call = wrapper.new_session()[0]
    assert call[-2:] == ["make", "-j8"], call


def test_it_execs_whatever_it_was_given_not_an_agent(wrapper):
    wrapper.run(["-d", "bot", "--", "make"])
    call = wrapper.new_session()[0]
    premint = [w for w in call if "agb hook" in w][0]
    assert "exec claude" not in premint and "exec codex" not in premint, premint


def test_every_launch_path_mints_the_row_first(wrapper):
    """More load-bearing here than anywhere: a shell fires NO hooks ever, so
    without the pre-mint there is no row at all -- not later, not once you
    typed, never."""
    for args, kw in ((["-d", "bot"], {}), (["bot"], {}),
                     (["bot"], {"inside_tmux": True})):
        wrapper.run(args, **kw)
    for call in wrapper.new_session():
        assert any("agb hook completed" in w for w in call), call


def test_the_premint_runs_inside_the_new_session(wrapper):
    wrapper.run(["-d", "bot"])
    assert wrapper.agb_calls() == [], wrapper.agb_calls()
    assert any("agb hook" in w for w in wrapper.new_session()[0])


def test_the_premint_carries_its_own_pid_and_execs(wrapper):
    wrapper.run(["-d", "bot"])
    premint = [w for w in wrapper.new_session()[0] if "agb hook" in w][0]
    assert "AGB_AGENT_PID=$$" in premint, premint
    assert premint.strip().split(";")[-1].strip().startswith("exec "), premint


def test_a_broken_agb_costs_a_row_and_never_your_command(wrapper):
    premint = [w for w in (wrapper.run(["-d", "bot"]),
                           wrapper.new_session()[0])[1] if "agb hook" in w][0]
    assert "&&" not in premint and "2>/dev/null" in premint, premint


def test_greet_is_not_an_option_here(wrapper):
    """It sends an opening PROMPT, which means nothing without an agent."""
    code, _out, err = wrapper.run(["-d", "bot", "--greet", "hi"])
    assert code != 0 and "unknown option" in err


def test_the_caveats_are_documented_loudly(wrapper):
    """A shell row's status never moves and it is not an agb-peer participant.
    Both are surprising and both must be said, not discovered."""
    head = open(SCRIPT, encoding="utf-8").read()
    head = head[:head.index("set -e")]
    assert "status never changes" in head.lower()
    assert "agb-peer" in head and "unknown" in head
