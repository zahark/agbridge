"""`agb-codex` -- the same wrapper for Codex.

Driven through a recording `tmux` stub rather than a real server: what matters
is the argv it constructs, and a real tmux would make that invisible.

⚠️ It is a near-copy of `agb-claude` and that is deliberate, not laziness. The
two are expected to DIVERGE: Codex has no "is this a project you trust?" prompt
(the one thing that makes `-d` fragile for Claude), it has `resume`/`queue`
that Claude has no equivalent of, and it fires no agbridge hooks at all. The
same reasoning is recorded for `open_split`/`open_drawer` in
`tests/test_identity.py`. What must NOT diverge is the pre-mint, and the tests
below are the ones that pin it.
"""

import os
import subprocess

import pytest

import conftest


SCRIPT = os.path.join(conftest.REPO_ROOT, "agb-codex")


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
    (binder / "codex").write_text("#!/bin/sh\nexit 0\n")
    # An `agb` that records being called. The wrapper must NEVER reach it: the
    # pre-mint belongs inside the new session, where the pane anchor is right.
    agb_log = tmp_path / "agb.log"
    (binder / "agb").write_text(
        "#!/bin/sh\n"
        "{ for a in \"$@\"; do printf '%s\\037' \"$a\"; done; printf '\\n'; }"
        " >> \"" + str(agb_log) + "\"\nexit 0\n")
    for name in ("tmux", "codex", "agb"):
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


def test_it_execs_codex_not_claude(wrapper):
    wrapper.run(["-d", "bot"])
    call = wrapper.new_session()[0]
    assert any("exec codex" in w for w in call), call
    assert not any("exec claude" in w for w in call), call


def test_passthrough_args_come_after_codex(wrapper):
    wrapper.run(["-d", "bot", "--", "--model", "gpt-5.6-terra"])
    call = wrapper.new_session()[0]
    assert call[-2:] == ["--model", "gpt-5.6-terra"], call


def test_every_launch_path_mints_the_row_first(wrapper):
    """The whole point of the wrapper for Codex, and more so than for Claude:
    Codex fires NO agbridge hooks, so without the pre-mint the row would never
    exist at all -- not on the first prompt, not ever."""
    for args, kw in ((["-d", "bot"], {}), (["bot"], {}),
                     (["bot"], {"inside_tmux": True})):
        wrapper.run(args, **kw)
    for call in wrapper.new_session():
        assert any("agb hook completed" in w for w in call), call


def test_the_premint_runs_inside_the_new_session(wrapper):
    """The anchor is (host, tmux server pid, %PANE): hooking from the caller's
    pane would mint a row for the CALLER's pane."""
    wrapper.run(["-d", "bot"])
    assert wrapper.agb_calls() == [], wrapper.agb_calls()
    assert any("agb hook" in w for w in wrapper.new_session()[0])


def test_the_premint_carries_its_own_pid_so_codex_adopts_the_row(wrapper):
    """`exec` preserves pid AND starttime, so the identity the shell records is
    Codex's a moment later and `bind_key` adopts rather than minting a second
    row. Dropping either the pid or the `exec` gives two rows."""
    wrapper.run(["-d", "bot"])
    call = wrapper.new_session()[0]
    premint = [w for w in call if "agb hook" in w][0]
    assert "AGB_AGENT_PID=$$" in premint, premint
    assert premint.strip().split(";")[-1].strip().startswith("exec "), premint


def test_the_premint_state_is_completed_not_active(wrapper):
    """A session at an empty prompt is waiting for you. `active` would claim it
    is working and blink a transition that never happened."""
    call = (wrapper.run(["-d", "bot"]), wrapper.new_session()[0])[1]
    premint = [w for w in call if "agb hook" in w][0]
    assert "agb hook completed" in premint and "agb hook active" not in premint


def test_a_broken_agb_costs_a_row_and_never_a_codex(wrapper):
    """`;` not `&&`, and stderr discarded."""
    call = (wrapper.run(["-d", "bot"]), wrapper.new_session()[0])[1]
    premint = [w for w in call if "agb hook" in w][0]
    assert "&&" not in premint, premint
    assert "2>/dev/null" in premint, premint


def test_the_no_hooks_caveat_is_documented_loudly(wrapper):
    """Codex fires no agbridge hooks, so the row it mints stays `completed` for
    ever -- the glyph never moves and agb-peer's status gate is useless for it.
    That is the single most surprising thing about a Codex row, and the file
    must say so rather than leave it to be discovered."""
    body = open(SCRIPT, encoding="utf-8").read()
    head = body[:body.index("set -e")]
    assert "FIRES NO agbridge HOOKS" in head
    assert "status gate" in head


def test_it_refuses_when_codex_is_missing(wrapper, tmp_path):
    code, _out, err = wrapper.run(["-d", "bot"], no_codex=True)
    assert code != 0
    assert "codex is not installed" in err
