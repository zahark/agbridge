"""The launchers' pre-mint runs the `agb` the LAUNCHER resolved, by its path.

The session's shell takes its PATH from the caller or from the tmux server, and
that PATH need not carry install.sh's bin dir: run by its full path a launcher
works without it. The pre-mint then failed inside the pane, `2>/dev/null` hid
"agb: not found", and `-d` announced a row that did not exist -- found on a host
whose login PATH had ~/local/bin and not ~/.local/bin.

One file for all three launchers, because the resolver is the same block in
each and a fix that reached two of them is the shape this repo keeps shipping.
"""

import os
import pathlib
import re
import subprocess

import pytest

import conftest


LAUNCHERS = ("agb-claude", "agb-codex", "agb-tmux")
SYSTEM_PATH = "/usr/bin:/bin"


def _stub(path, log=None):
    body = "#!/bin/sh\n"
    if log is not None:
        body += ("{ for a in \"$@\"; do printf '%s\\037' \"$a\"; done; "
                 "printf '\\n'; } >> \"" + str(log) + "\"\n")
    body += "case \"$1\" in has-session) exit 1 ;; esac\nexit 0\n"
    path.write_text(body)
    os.chmod(str(path), 0o755)


def _records(log):
    if not log.exists():
        return []
    return [line.split("\037")[:-1]
            for line in log.read_text().splitlines() if line]


def _install_bindir(home):
    """install.sh's DEFAULT_BINDIR, with $HOME bound to `home`: the launchers'
    fallback is a copy of it (it cannot be imported into sh), so the test reads
    the installer's own spelling rather than a third one."""
    with open(os.path.join(conftest.REPO_ROOT, "install.sh")) as f:
        m = re.search(r'^DEFAULT_BINDIR="([^"]*)"$', f.read(), re.M)
    assert m, "install.sh no longer assigns DEFAULT_BINDIR"
    assert m.group(1).startswith("$HOME/"), m.group(1)
    return os.path.join(str(home), m.group(1)[len("$HOME/"):])


@pytest.fixture
def launch(tmp_path):
    tools = tmp_path / "tools"          # tmux, claude, codex -- never agb
    tools.mkdir()
    tmux_log = tmp_path / "tmux.log"
    agb_log = tmp_path / "agb.log"
    _stub(tools / "tmux", tmux_log)
    for name in ("claude", "codex"):
        _stub(tools / name)
    home = tmp_path / "home"            # conftest's scratch $HOME
    home.mkdir(exist_ok=True)
    work = tmp_path / "work"
    work.mkdir()

    class Launch(object):
        def __init__(self):
            self.home, self.work = home, work

        def agb_in(self, d):
            os.makedirs(str(d))
            _stub(d / "agb", agb_log)
            return str(d / "agb")

        def env(self, extra=()):
            env = dict(os.environ)
            for var in ("AGB_CLAUDE_CUSTOM", "AGB_CODEX_CUSTOM", "TMUX",
                        "TMUX_PANE", "AGB_HOST"):
                env.pop(var, None)
            env["HOME"] = str(home)
            env["PATH"] = os.pathsep.join(
                [str(d) for d in extra] + [str(tools), SYSTEM_PATH])
            return env

        def run(self, script, extra=()):
            # `-- true` gives agb-tmux a command that is not your login shell;
            # claude and codex are stubs, so it is harmless to them.
            args = ["-d", "bot"] + (["--", "true"] if script == "agb-tmux"
                                    else [])
            proc = subprocess.Popen(
                ["sh", os.path.join(conftest.REPO_ROOT, script)] + args,
                cwd=str(work), env=self.env(extra), stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            out, err = conftest.communicate(proc, b"")
            return proc.returncode, out.decode(), err.decode()

        def new_session(self):
            calls = [c for c in _records(tmux_log) if "new-session" in c]
            assert len(calls) == 1, calls
            return calls[0]

        def premint(self):
            return [w for w in self.new_session() if " hook completed" in w][0]

        def run_premint(self, script, premint=None):
            """Execute the pane's command with a PATH that has NO agb on it --
            the pane the bug was found in."""
            cmd = ["sh", "-c", premint or self.premint(), script]
            if script == "agb-tmux":
                cmd.append("true")
            proc = subprocess.Popen(cmd, cwd=str(work), env=self.env(),
                                    stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE)
            conftest.communicate(proc, b"")
            return proc.returncode

        def agb_calls(self):
            return _records(agb_log)

    return Launch()


@pytest.mark.parametrize("script", LAUNCHERS)
def test_the_premint_reaches_an_agb_the_pane_cannot_find(launch, script):
    elsewhere = launch.work.parent / "elsewhere"
    agb = launch.agb_in(elsewhere)
    rc, out, err = launch.run(script, extra=[elsewhere])
    assert rc == 0, err
    assert "'%s' hook completed" % agb in launch.premint()
    # Non-vacuity: the pane's PATH really cannot find `agb`, so the bare
    # spelling the pre-mint used to carry fails here...
    assert launch.run_premint(script, "agb hook completed") == 127
    assert launch.agb_calls() == []
    # ...and the one it carries now does not.
    launch.run_premint(script)
    assert launch.agb_calls() == [["hook", "completed"]], launch.agb_calls()
    assert "the row is there now" in out and "WARNING" not in err


@pytest.mark.parametrize("script", LAUNCHERS)
def test_agb_falls_back_to_the_installers_bin_dir(launch, script):
    agb = launch.agb_in(pathlib.Path(_install_bindir(launch.home)))
    rc, out, err = launch.run(script)
    assert rc == 0, err
    assert "'%s' hook completed" % agb in launch.premint()
    launch.run_premint(script)
    assert launch.agb_calls() == [["hook", "completed"]], launch.agb_calls()


@pytest.mark.parametrize("script", LAUNCHERS)
def test_no_agb_anywhere_warns_and_claims_no_row(launch, script):
    """Best-effort still: the session starts. But `-d` must not say the row is
    there when nothing could have minted it -- that sentence is what made the
    failure look like a bridge problem."""
    rc, out, err = launch.run(script)
    assert rc == 0, err
    assert launch.new_session(), "a missing agb must never cost the session"
    assert "WARNING agb is not on $PATH" in err, err
    assert "the row is there now" not in out, out


@pytest.mark.parametrize("script", LAUNCHERS)
def test_an_agb_path_with_a_quote_and_a_space_survives(launch, script):
    odd = launch.work.parent / "it's a dir"
    launch.agb_in(odd)
    rc, _, err = launch.run(script, extra=[odd])
    assert rc == 0, err
    launch.run_premint(script)
    assert launch.agb_calls() == [["hook", "completed"]], launch.agb_calls()
