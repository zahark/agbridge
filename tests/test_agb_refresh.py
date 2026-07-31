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
    # `pgrep -f "<pattern>"` succeeds (bridge alive) while the marker file
    # exists. The `launchctl` stub cannot remove it, so a test decides how many
    # polls the bridge survives by removing it from a counter file.
    #
    # ⚠️ And it HONOURS ITS ARGV. An earlier stub ignored it entirely, which
    # made every question about *which* bridge the poll matches unaskable: the
    # pattern could name the wrong instance, or every instance, and no test
    # could tell. `$2` is the pattern (`pgrep -f <pattern>`), matched as a
    # substring of the command line the test says is running, which is what
    # `pgrep -f` does against a real one. A quoted `case` pattern is literal, so
    # a path is compared as a path and not as a glob.
    stub("pgrep",
         "n=$(cat '" + str(tmp_path / "polls") + "' 2>/dev/null || echo 0)\n"
         "echo $((n + 1)) > '" + str(tmp_path / "polls") + "'\n"
         "[ -f '" + str(alive) + "' ] || exit 1\n"
         "[ $((n + 1)) -lt \"${AGBR_ALIVE_POLLS:-0}\" ] || exit 1\n"
         "case \"${AGBR_ALIVE_CMDLINE:-}\" in\n"
         "    *\"$2\"*) exit 0 ;;\n"
         "    *) exit 1 ;;\n"
         "esac\n")
    stub("sleep", "exit 0\n")           # so the poll loop costs no wall clock

    # The script refuses to restart what it cannot find a plist for, which is
    # correct and would make every ordering assertion below vacuous.
    #
    # Written as a literal `<plist/>`, i.e. one with no `--config` in it: that
    # is a plist rendered BEFORE per-instance installs existed, and it is the
    # normal state of the default job right after `install.sh mac --instance`
    # (which renders only the new instance's plist, and does not restart the
    # default one). So the fixture's default shape is the stale one, and a test
    # that wants the modern shape asks for it with `write_plist`.
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
        agb_path = str(agb)

        def config(self, instance=None):
            """Where `install.sh` would have put this instance's config."""
            base = tmp_path / ".config" / "agbridge"
            if instance:
                base = base / instance
            return str(base / "config")

        def cmdline(self, instance=None, with_config=True):
            """The command line launchd would have started that bridge with."""
            if not with_config:
                return "%s -S -E %s bridge" % (python, agb)
            return "%s -S -E %s bridge --config %s" % (
                python, agb, self.config(instance))

        def write_plist(self, label="com.agbridge", with_config=True,
                        instance=None):
            """Render the shape `install.sh` renders, or the shape it used to.

            Only the `--config` argument line is load-bearing: `agb-refresh`
            greps for it to decide whether it may narrow the liveness pattern.
            """
            args = ["    <string>bridge</string>"]
            if with_config:
                args += ["    <string>--config</string>",
                         "    <string>%s</string>" % (self.config(instance),)]
            (agents / (label + ".plist")).write_text(
                "<plist version=\"1.0\"><dict>\n"
                "  <key>ProgramArguments</key>\n  <array>\n"
                + "\n".join(args) + "\n  </array>\n</dict></plist>\n")

        def run(self, args=(), alive_polls=0, alive_cmdline=None):
            if alive_polls:
                alive.write_text("")
            env = dict(os.environ)
            env["PATH"] = str(binder) + os.pathsep + env.get("PATH", "")
            env["AGBR_ALIVE_POLLS"] = str(alive_polls)
            # Which bridge is running, for the `pgrep` stub to match the
            # script's pattern against. The default is the DEFAULT instance's,
            # which is what "a bridge is up" meant before instances existed.
            env["AGBR_ALIVE_CMDLINE"] = (self.cmdline() if alive_cmdline is None
                                         else alive_cmdline)
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

        def call(self, needle):
            """The first recorded call containing `needle`, whole."""
            for line in self.calls():
                if needle in line:
                    return line
            return ""

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


# ---------------------------------------------------------------------------
# instances: which map is being repaired, and which bridge is being waited on
# ---------------------------------------------------------------------------
#
# A second Mac-side instance is a second label, a second config and a second
# rows map, sharing ONE code install. That sharing is what makes both failures
# below possible: the same `agb-refresh` binary, and the same `agb` path in
# every instance's command line.


def test_an_instance_moves_the_label_and_the_config_together(refresh):
    """The whole reason `--instance` is sugar rather than two flags.

    A label without its config stops instance B and then forgets instance A's
    bindings -- and both halves report success, because each is individually a
    perfectly good thing to have done.
    """
    refresh.write_plist("com.agbridge.hostb", instance="hostb")
    rc, _out, _err = refresh.run(["--instance", "hostb"])
    assert rc == 0
    assert "com.agbridge.hostb" in refresh.call("launchctl bootout")
    assert "com.agbridge.hostb.plist" in refresh.call("launchctl bootstrap")
    # ...and the forget names hostb's config, from which `forget-rows` derives
    # both the rows map and the placements file.
    assert "--config %s" % (refresh.config("hostb"),) \
        in refresh.call("agb forget-rows")
    # Non-vacuity: the default instance's config is a real, different path, and
    # it is nowhere in what ran.
    assert refresh.config() != refresh.config("hostb")
    assert refresh.config() not in "\n".join(refresh.calls())


def test_the_banner_names_the_instance_and_the_config_it_acted_on(refresh):
    """Limitation 1's only mitigation, and it has to be unconditional.

    Refreshing the wrong instance succeeds. Every other line of output is
    identical to the run that was meant, so without this line there is nothing
    at all to tell the operator that the sidebar they were fixing was not the
    one that moved.
    """
    refresh.write_plist("com.agbridge.hostb", instance="hostb")
    _rc, out, _err = refresh.run(["--instance", "hostb"])
    assert "instance: hostb" in out
    assert refresh.config("hostb") in out
    assert "com.agbridge.hostb" in out


def test_the_default_run_names_the_default_instance(refresh):
    """The banner is printed for the default instance too.

    A banner that appeared only with `--instance` would say nothing on exactly
    the run that needs it: the one where you forgot the flag.
    """
    _rc, out, _err = refresh.run()
    assert "instance: (default)" in out
    assert refresh.config() in out
    # And nothing about the default run moved: same label, same map.
    assert "com.agbridge" in refresh.call("launchctl bootout")
    assert "com.agbridge.hostb" not in "\n".join(refresh.calls())
    assert "--config %s" % (refresh.config(),) in refresh.call("agb forget-rows")


def test_a_bare_config_flag_needs_no_instance_name(refresh):
    """An `install.sh mac --config <nondefault>` install has no instance name.

    That flag predates instances, so such an install cannot spell itself as
    `--instance <x>` -- and with only the sugar available it would have no way
    to refresh against its own map. The label stays the default one, because
    that install's plist is still `com.agbridge`.
    """
    _rc, out, _err = refresh.run(["--config", "/tmp/elsewhere/config"])
    assert "--config /tmp/elsewhere/config" in refresh.call("agb forget-rows")
    assert "/tmp/elsewhere/config" in out
    assert "com.agbridge" in refresh.call("launchctl bootout")
    assert "com.agbridge." not in refresh.call("launchctl bootout")


def test_an_explicit_label_still_beats_the_instance_sugar(refresh):
    """Sugar, not a replacement: the flags it fills in still win when given."""
    refresh.write_plist("weird.label", instance="hostb")
    rc, _out, _err = refresh.run(
        ["--instance", "hostb", "--label", "weird.label"])
    assert rc == 0
    assert "weird.label" in refresh.call("launchctl bootout")
    # The config half still followed the instance.
    assert "--config %s" % (refresh.config("hostb"),) \
        in refresh.call("agb forget-rows")


def test_the_wait_ignores_another_instances_bridge(refresh):
    """`--dest` is shared, so every instance's bridge is `<same agb> bridge`.

    A pattern that stopped there would match instance A's bridge for ever while
    B is being refreshed: B boots out at once, the poll never clears, and every
    single run ends in "still running after 10s" -- a warning that the forget
    may have been undone, in a command reached by someone already annoyed.
    """
    refresh.write_plist("com.agbridge.hostb", instance="hostb")
    rc, out, _err = refresh.run(["--instance", "hostb"],
                                alive_polls=10 ** 6,
                                alive_cmdline=refresh.cmdline())
    assert rc == 0
    assert "still running" not in out
    # Non-vacuity twice over: the poll DID run, and it ran with the narrow
    # pattern -- an unmatched broad pattern would look identical from the
    # output alone.
    assert refresh.index("pgrep") > -1, "the bridge was never polled for"
    assert "--config %s" % (refresh.config("hostb"),) in refresh.call("pgrep")


def test_a_plain_refresh_ignores_a_named_instances_bridge(refresh):
    """The mirror image, and the one an empty `$config` default would break.

    `agb-refresh` spells an unset path as "", and the plist's `--config` is
    unconditional -- so a `$config` left empty makes the pattern
    `<agb> bridge --config `, a prefix of EVERY instance's command line. A plain
    refresh while instance B was up would then poll a live process for the full
    10 s and warn, on the most common invocation this command has.
    """
    refresh.write_plist()               # the default job, modern shape
    rc, out, _err = refresh.run(alive_polls=10 ** 6,
                                alive_cmdline=refresh.cmdline("hostb"))
    assert rc == 0
    assert "still running" not in out
    assert refresh.index("pgrep") > -1, "the bridge was never polled for"
    assert "--config %s" % (refresh.config(),) in refresh.call("pgrep")


def test_the_wait_still_sees_the_bridge_it_is_actually_replacing(refresh):
    """The positive control for the two tests above.

    Both of them pass if the poll simply never matches anything, which is the
    dangerous failure rather than the safe one. This is the same shape with the
    instance's OWN bridge running: the wait must still see it.
    """
    refresh.write_plist("com.agbridge.hostb", instance="hostb")
    rc, out, _err = refresh.run(["--instance", "hostb"],
                                alive_polls=10 ** 6,
                                alive_cmdline=refresh.cmdline("hostb"))
    assert rc == 0
    assert "still running" in out


def test_a_plist_without_the_config_flag_falls_back_to_a_broad_wait(refresh):
    """⚠️ The narrow pattern is DERIVED from the plist, never assumed.

    The fixture's plist is a literal `<plist/>` -- a plist rendered before this
    existed, which is the normal state of the default job right after
    `install.sh mac --instance hostb`: that renders only hostb's plist and does
    not restart the default one, while it DOES install this newer agb-refresh
    (shared `--dest`). So the running default bridge has no `--config` in its
    command line, and a narrow pattern would match nothing: zero waits, no
    warning, and the forget landing while that bridge is still alive to re-mint
    rows against the ids it has just closed. That is the `no such session` spam
    this script exists to cure, restored by a fix aimed at a cosmetic warning.
    """
    rc, out, _err = refresh.run(
        alive_polls=3, alive_cmdline=refresh.cmdline(with_config=False))
    assert rc == 0
    # It waited: the forget came after the last poll, which is the property the
    # whole script is built around.
    forget = refresh.index("agb forget-rows")
    polls = [n for n, line in enumerate(refresh.calls())
             if line.startswith("pgrep")]
    assert polls, "the bridge was never polled for"
    assert max(polls) < forget, refresh.calls()
    # Broad, and said out loud rather than silently: `--config` is absent from
    # the pattern entirely.
    assert "--config" not in refresh.call("pgrep")
    assert "no --config in" in out


@pytest.mark.parametrize("name", ["../../evil", "a/b", ".hidden", "-x",
                                  "host b", "host;rm"])
def test_an_instance_name_that_would_escape_its_own_directories_is_refused(
        refresh, name):
    """The same rule and the same words as `install.sh`'s `instance_ok`.

    The name becomes a launchd label component, a plist filename and a config
    directory here too, so it is refused for the same reasons -- and the two
    validators must agree, because a name this one accepts and the installer
    refuses names a plist that was never rendered.
    """
    rc, _out, err = refresh.run(["--instance", name])
    assert rc != 0
    assert "--instance" in err
    # Refused BEFORE anything was touched: no stop, no forget, no start.
    assert refresh.calls() == []


def test_an_empty_instance_name_is_refused_rather_than_ignored(refresh):
    """`need` only counts arguments, so `--instance ""` would otherwise read as
    "not given" and refresh the DEFAULT instance while echoing the name back --
    the exact silent-wrong-instance failure the flag exists to prevent."""
    rc, _out, err = refresh.run(["--instance", ""])
    assert rc != 0
    assert "--instance" in err
    assert refresh.calls() == []
