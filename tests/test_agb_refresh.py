"""`agb-refresh` -- stop the bridge, forget the row bindings, start it again.

Driven through recording `launchctl`/`pgrep` stubs and a fake `agb`, because
what matters is the *order* of the three steps and a real launchd would make
that invisible. The ordering is the whole point of the script: a forget that
lands while the old bridge is still alive can be undone by that bridge's next
map write.
"""

import os
import subprocess

import pytest

import conftest


SCRIPT = os.path.join(conftest.REPO_ROOT, "agb-refresh")


@pytest.fixture
def refresh(tmp_path):
    """`agb-refresh` with every external command stubbed and recording.

    The stubs append to one shared log, so the assertions can be about
    *sequence* across different programs rather than about each in isolation.
    """
    binder = tmp_path / "bin"
    binder.mkdir()
    log = tmp_path / "calls.log"
    alive = tmp_path / "alive"          # exists => `pgrep` reports the bridge

    def stub(name, body):
        path = binder / name
        path.write_text(
            "#!/bin/sh\n"
            "printf '%s" + name + " %s\\n' '' \"$*\" >> \"" + str(log) + "\"\n"
            + body)
        os.chmod(str(path), 0o755)

    stub("launchctl", "exit 0\n")
    # `pgrep -f "<agb> bridge"` succeeds (bridge alive) while the marker file
    # exists. The `launchctl` stub cannot remove it, so a test decides how many
    # polls the bridge survives by removing it from a counter file.
    stub("pgrep",
         "n=$(cat '" + str(tmp_path / "polls") + "' 2>/dev/null || echo 0)\n"
         "echo $((n + 1)) > '" + str(tmp_path / "polls") + "'\n"
         "[ -f '" + str(alive) + "' ] || exit 1\n"
         "[ $((n + 1)) -lt \"${AGBR_ALIVE_POLLS:-0}\" ] || exit 1\n"
         "exit 0\n")
    stub("sleep", "exit 0\n")           # so the poll loop costs no wall clock

    # The script refuses to restart what it cannot find a plist for, which is
    # correct and would make every ordering assertion below vacuous.
    agents = tmp_path / "Library" / "LaunchAgents"
    agents.mkdir(parents=True)
    (agents / "com.agbridge.plist").write_text("<plist/>\n")

    agb = tmp_path / "agb"
    agb.write_text("# not executed: the script runs `$python $agb ...`\n")
    python = binder / "fakepython"
    python.write_text(
        "#!/bin/sh\n"
        "printf 'agb %s\\n' \"$*\" >> \"" + str(log) + "\"\n"
        "exit 0\n")
    os.chmod(str(python), 0o755)

    class Refresh(object):
        def run(self, args=(), alive_polls=0):
            if alive_polls:
                alive.write_text("")
            env = dict(os.environ)
            env["PATH"] = str(binder) + os.pathsep + env.get("PATH", "")
            env["AGBR_ALIVE_POLLS"] = str(alive_polls)
            env["HOME"] = str(tmp_path)
            proc = subprocess.Popen(
                ["sh", SCRIPT, "--agb", str(agb), "--python", str(python)]
                + list(args),
                cwd=str(tmp_path), env=env, stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            out, err = conftest.communicate(proc, b"")
            return proc.returncode, out.decode(), err.decode()

        def calls(self):
            if not log.exists():
                return []
            return [line for line in log.read_text().splitlines() if line]

        def index(self, needle):
            for position, line in enumerate(self.calls()):
                if needle in line:
                    return position
            return -1

    return Refresh()


def test_the_three_steps_happen_in_order(refresh):
    """stop, then forget, then start. Each step is only safe once the previous
    one has landed."""
    rc, out, _err = refresh.run()
    assert rc == 0
    stop = refresh.index("launchctl bootout")
    forget = refresh.index("agb forget-rows")
    start = refresh.index("launchctl bootstrap")
    assert -1 < stop < forget < start, refresh.calls()
    assert "stopped:" in out and "started:" in out


def test_the_forget_waits_for_the_bridge_to_actually_exit(refresh):
    """`bootout` returns when launchd accepts the request, not when the process
    is gone -- and the bridge is normally blocked reading its ssh.

    A forget that lands while the old bridge is still alive is exactly what this
    script exists to prevent: that bridge holds the row map in memory and
    merges-then-writes on every save, so it can re-mint rows against ids
    `forget-rows` has just closed.
    """
    rc, _out, _err = refresh.run(alive_polls=3)
    assert rc == 0
    forget = refresh.index("agb forget-rows")
    polls = [n for n, line in enumerate(refresh.calls())
             if line.startswith("pgrep")]
    assert polls, "the bridge was never polled for"
    assert max(polls) < forget, \
        "forget ran before the last liveness poll: %s" % (refresh.calls(),)


def test_a_bridge_that_will_not_die_is_named_not_waited_on_for_ever(refresh):
    """A recovery command that hangs is worse than one that proceeds with the
    risk stated: the bound is 10s, then it says so and does the useful work."""
    rc, out, _err = refresh.run(alive_polls=10 ** 6)
    assert rc == 0
    assert "WARNING:" in out and "still running" in out
    assert refresh.index("agb forget-rows") > -1, "it must still forget"
    assert refresh.index("launchctl bootstrap") > -1, "and still restart"


def test_a_dry_run_changes_nothing(refresh):
    """No stop, no start -- a dry run that bounced the bridge would be a dry run
    with a side effect, and this one is reached by people who are unsure."""
    rc, out, _err = refresh.run(["--dry-run"])
    assert rc == 0
    assert refresh.index("launchctl bootout") == -1
    assert refresh.index("launchctl bootstrap") == -1
    assert "--dry-run" in "\n".join(refresh.calls())
    assert "nothing was changed" in out
