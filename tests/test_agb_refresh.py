"""`agb-refresh` -- stop the bridge, forget the row bindings, start it again.

Driven through recording `launchctl`/`pgrep` stubs and a fake `agb`, because
what matters is the *order* of the three steps and a real launchd would make
that invisible. The ordering is the whole point of the script: a forget that
lands while the old bridge is still alive can be undone by that bridge's next
map write.
"""

import os
import subprocess
import sys

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
    # could tell. `$2` is the pattern (`pgrep -f <pattern>`), matched against
    # the command line the test says is running.
    #
    # ⚠️ Matched as an unanchored EXTENDED REGULAR EXPRESSION -- `grep -E`, not
    # a `case` glob -- because that is what `pgrep -f` does. An earlier stub
    # compared it as a literal substring, which is *nearly* the same thing and
    # differs on exactly the question that matters here: the script anchors its
    # pattern with a trailing `([[:space:]]|$)` so the default instance's
    # `.../agbridge/config` is not a prefix match for an instance named
    # `configb`. Against a literal comparison that anchor is text nobody's
    # command line contains, so every narrow-pattern test would go vacuous in
    # the safe-looking direction: no match, no wait, no warning.
    #
    # ⚠️ And it PRINTS ONE PID PER MATCHING PROCESS, as the real one does,
    # because `bridge_alive`'s fallback probe subtracts two counts rather than
    # reading two exit statuses. A stub that answered only with a status makes
    # both counts 0, the difference 0, and every question about the probe
    # unaskable in the safe-looking direction again. `$AGBR_ALIVE_CMDLINE` may
    # therefore carry SEVERAL command lines, one per line, which is how "an
    # untagged bridge and a tagged one are both up" is spelled.
    stub("pgrep",
         "n=$(cat '" + str(tmp_path / "polls") + "' 2>/dev/null || echo 0)\n"
         "echo $((n + 1)) > '" + str(tmp_path / "polls") + "'\n"
         "[ -f '" + str(alive) + "' ] || exit 1\n"
         "[ $((n + 1)) -lt \"${AGBR_ALIVE_POLLS:-0}\" ] || exit 1\n"
         "hits=$(printf '%s\\n' \"${AGBR_ALIVE_CMDLINE:-}\" "
         "| grep -Ec -- \"$2\" || :)\n"
         "[ \"$hits\" -gt 0 ] || exit 1\n"
         "i=0\n"
         "while [ $i -lt \"$hits\" ]; do i=$((i + 1)); echo $((4200 + i)); "
         "done\n")
    stub("sleep", "exit 0\n")           # so the poll loop costs no wall clock

    # ⚠️ `agtermctl`, and the acceptance test at the bottom of this file does
    # not work without it. `forget-rows` asks `agtermctl tree --json` where the
    # rows live BEFORE closing them, and `None` -- which is what an `agtermctl`
    # that is not on `$PATH` produces -- means "could not ask", so
    # `write_placements` is never reached at all. A test asserting that the
    # OTHER instance's placements file was left alone then holds up nothing:
    # the file is untouched because no code path wrote any placements anywhere.
    #
    # It answers from a file that no test writes by default, so `tree` still
    # fails (and placements are still left alone) everywhere else. A test that
    # wants the tree says so with `write_tree`.
    tree = tmp_path / "tree.json"
    stub("agtermctl",
         "if [ \"$1\" = tree ]; then cat '" + str(tree) + "' 2>/dev/null "
         "|| exit 1; fi\n"
         "exit 0\n")

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
        def config(self, instance=None):
            """Where `install.sh` would have put this instance's config."""
            base = tmp_path / ".config" / "agbridge"
            if instance:
                base = base / instance
            return str(base / "config")

        def cmdline(self, instance=None, with_config=True, config=None):
            """The command line launchd would have started that bridge with."""
            if not with_config:
                return "%s -S -E %s bridge" % (python, agb)
            return "%s -S -E %s bridge --config %s" % (
                python, agb, config or self.config(instance))

        def write_tree(self, placed):
            """What `agtermctl tree --json` answers: {row id: workspace}."""
            import json
            spaces = {}
            for row, space in sorted(placed.items()):
                spaces.setdefault(space, []).append({"id": row})
            tree.write_text(json.dumps({"result": {"tree": {"workspaces": [
                {"name": name, "sessions": sessions}
                for name, sessions in sorted(spaces.items())]}}}))

        def write_plist(self, label="com.agbridge", with_config=True,
                        instance=None, config=None):
            """Render the shape `install.sh` renders, or the shape it used to.

            Only the `--config` pair is load-bearing: `agb-refresh` reads BOTH
            halves out of it -- the flag's presence decides whether the liveness
            pattern may be narrowed, and the VALUE is the config it narrows on
            and (absent an explicit `--config`) the map it repairs. `config=`
            overrides the conventional path, which is what an install made with
            `--instance <name> --config <elsewhere>` leaves behind.

            ⚠️ The value is XML-ESCAPED here, exactly as `install.sh`'s `rep()`
            escapes it (`xml_escape`, install.sh:258) -- so a test names the
            real path and this renders what the installer would have written.
            Without it a test for `&` in a config path would have to spell
            `&amp;` itself, which asserts on a plist nobody could have produced
            rather than on the one `install.sh mac --config '/tmp/a&b/config'`
            actually writes.
            """
            value = (config or self.config(instance))
            for raw, entity in (("&", "&amp;"), ("<", "&lt;"), (">", "&gt;")):
                value = value.replace(raw, entity)
            args = ["    <string>bridge</string>"]
            if with_config:
                args += ["    <string>--config</string>",
                         "    <string>%s</string>" % (value,)]
            (agents / (label + ".plist")).write_text(
                "<plist version=\"1.0\"><dict>\n"
                "  <key>ProgramArguments</key>\n  <array>\n"
                + "\n".join(args) + "\n  </array>\n</dict></plist>\n")

        def plist_text(self, label="com.agbridge"):
            """What is actually on disk, for asserting on the escaping."""
            return (agents / (label + ".plist")).read_text()

        def quoted(self, path):
            """`path` as it must appear INSIDE the `pgrep -f` pattern.

            ⚠️ `pgrep -f` matches an EXTENDED REGULAR EXPRESSION against the
            whole command line, so `agb-refresh` escapes every character of an
            interpolated path that ERE calls special (`ere_quote`) -- a config
            at `/tmp/a+b/config` left raw yields `a+b`, which matches `ab` and
            not the path it came from. A test asserting the RAW path in the
            pattern is therefore asserting that the quoting is absent, which is
            why the mirror lives here rather than the paths being spelled by
            hand at each call site.
            """
            import re
            return re.sub(r"([][(){}.*+?^$|\\])", r"\\\1", path)

        def run(self, args=(), alive_polls=0, alive_cmdline=None):
            if alive_polls:
                alive.write_text("")
            env = dict(os.environ)
            env["PATH"] = str(binder) + os.pathsep + env.get("PATH", "")
            env["AGBR_ALIVE_POLLS"] = str(alive_polls)
            # Which bridge is running, for the `pgrep` stub to match the
            # script's pattern against. The default is the DEFAULT instance's,
            # which is what "a bridge is up" meant before instances existed.
            # A LIST is several processes, one per line -- the shape the
            # liveness probe's subtraction is about.
            if alive_cmdline is None:
                alive_cmdline = self.cmdline()
            elif not isinstance(alive_cmdline, str):
                alive_cmdline = "\n".join(alive_cmdline)
            env["AGBR_ALIVE_CMDLINE"] = alive_cmdline
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


def test_an_explicit_config_still_beats_the_instance_sugar(refresh):
    """The mirror of the `--label` test above, and it needs its own.

    `--instance` fills in a config only when none was given, exactly as
    `install.sh` does it (install.sh:355) -- so an install whose config lives
    somewhere the convention does not name can still be repaired by naming both.
    Made unconditional, the sugar would silently redirect the forget to a path
    that may not even exist, and `forget-rows` answers "the map is already
    empty" and returns 0 for a file that is not there.
    """
    refresh.write_plist("com.agbridge.hostb", instance="hostb")
    rc, out, _err = refresh.run(["--instance", "hostb",
                                 "--config", "/tmp/elsewhere/config"])
    assert rc == 0
    assert "--config /tmp/elsewhere/config" in refresh.call("agb forget-rows")
    assert "/tmp/elsewhere/config" in out
    # The label half still followed the instance, and the conventional config
    # path is nowhere in what ran.
    assert "com.agbridge.hostb" in refresh.call("launchctl bootout")
    assert "--config %s" % (refresh.config("hostb"),) \
        not in refresh.call("agb forget-rows")


def test_the_config_is_read_from_the_plist_not_rebuilt_by_convention(refresh,
                                                                     tmp_path):
    """⚠️ `--instance hostb` does NOT imply `<config dir>/hostb/config`.

    `install.sh mac --instance hostb --config <elsewhere>` is supported and
    documented -- the explicit flag wins there too -- and the plist is the only
    record of where that install actually put the file. Rebuilding the
    conventional path instead names a config that does not exist, and both
    halves then fail quietly: `forget-rows` reports "the map is already empty"
    and returns 0, so the recovery command repairs nothing and calls it a
    success, while the real map keeps the stale bindings that sent you here.
    """
    custom = str(tmp_path / "custom-c" / "config")
    refresh.write_plist("com.agbridge.hostc", instance="hostc", config=custom)
    rc, out, _err = refresh.run(["--instance", "hostc"])
    assert rc == 0
    assert "--config %s" % (custom,) in refresh.call("agb forget-rows")
    assert custom in out
    # Non-vacuity: the conventional path is a real, different string, and it is
    # nowhere in what ran or was said.
    assert refresh.config("hostc") != custom
    assert refresh.config("hostc") not in "\n".join(refresh.calls())


def test_the_convention_is_still_the_fallback_when_no_plist_answers(refresh):
    """A Mac whose plist was never rendered -- or was deleted by hand -- can
    still be refreshed by name. The plist is the better answer, not the only
    one, and losing the sugar entirely on a missing file would be a worse
    failure than the one it fixes."""
    rc, out, _err = refresh.run(["--instance", "hostb"])   # no hostb plist
    assert rc == 0
    assert "--config %s" % (refresh.config("hostb"),) \
        in refresh.call("agb forget-rows")


def test_a_dry_run_still_names_the_instance_it_would_have_acted_on(refresh):
    """The dry run is what an operator who is unsure which instance they are on
    actually types, so it is the run that most needs the banner -- and the one
    it is easiest to lose, because the dry-run branch exits early."""
    refresh.write_plist("com.agbridge.hostb", instance="hostb")
    rc, out, _err = refresh.run(["--instance", "hostb", "--dry-run"])
    assert rc == 0
    assert "instance: hostb" in out
    assert refresh.config("hostb") in out
    assert "would stop com.agbridge.hostb" in out
    # Non-vacuity: it really was a dry run.
    assert refresh.index("launchctl") == -1


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
    # ...and the fallback probe did not drag it back in either: A's bridge
    # CARRIES a `--config`, so it is attributable to A and is not this run's to
    # wait for. A probe that waited for it would be the same every-run warning
    # by another route.
    assert "carries no" not in out
    # Non-vacuity twice over: the poll DID run, and it ran with the narrow
    # pattern -- an unmatched broad pattern would look identical from the
    # output alone.
    assert refresh.index("pgrep") > -1, "the bridge was never polled for"
    assert "--config %s" % (refresh.quoted(refresh.config("hostb")),) \
        in refresh.call("pgrep")


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
    assert "--config %s" % (refresh.quoted(refresh.config()),) \
        in refresh.call("pgrep")


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


def test_the_pattern_uses_the_plists_config_and_not_this_runs(refresh):
    """⚠️ The plist is asked for the VALUE too, not just the flag's presence.

    Deriving "is there a `--config`" from the plist and then interpolating the
    config *this run* resolved is the same silent failure as assuming the flag,
    reached from the other end: with `--config <elsewhere>` given explicitly
    the pattern would name a path no bridge was started with, match nothing,
    wait zero times, warn about nothing -- and forget under a live bridge.

    The plist's value is the right one in every case, because the plist is what
    launchd started the process with. `--config` on THIS command line answers a
    different question: which map to repair.
    """
    refresh.write_plist("com.agbridge.hostb", instance="hostb")
    rc, out, _err = refresh.run(["--instance", "hostb",
                                 "--config", "/tmp/elsewhere/config"],
                                alive_polls=10 ** 6,
                                alive_cmdline=refresh.cmdline("hostb"))
    assert rc == 0
    # The poll saw the bridge it was replacing: the pattern came from the plist.
    assert "still running" in out
    assert "--config %s" % (refresh.quoted(refresh.config("hostb")),) \
        in refresh.call("pgrep")
    assert "/tmp/elsewhere" not in refresh.call("pgrep")
    # ...while the forget went to the map that was named.
    assert "--config /tmp/elsewhere/config" in refresh.call("agb forget-rows")


def test_the_bridges_own_stale_row_recipe_acts_on_the_bridge_that_printed_it(
        refresh, mac):
    """⚠️ The stale-row hint is a string a human types, so it is tested by
    TYPING IT -- through this script's own resolution, not by looking at it.

    `agb_mac.refresh_recipe` prints `agb-refresh --config <this instance's
    config>` into this instance's log, because a config path is the only
    identity a bridge has. `--config` does not move the launchd label by
    itself, and while it did not, that recipe was worse than the fixed
    `Run agb-refresh` it replaced: it booted out `com.agbridge`, waited for
    the DEFAULT bridge to exit, then ran `forget-rows` against B's map while
    B's bridge was still alive -- the one condition the wait exists to prevent,
    since a live bridge merges-then-writes and re-mints against the ids
    `forget-rows` has just closed.

    ⚠️ An assertion that the recipe merely *contains* `--config <B>` passes
    against every word of that. The question is what the string DOES.
    """
    import shlex
    refresh.write_plist()                                    # the default job
    refresh.write_plist("com.agbridge.hostb", instance="hostb")
    recipe = mac.refresh_recipe(refresh.config("hostb"))
    argv = shlex.split(recipe)
    assert argv[0] == "agb-refresh", recipe
    rc, out, _err = refresh.run(argv[1:], alive_polls=10 ** 6,
                                alive_cmdline=refresh.cmdline("hostb"))
    assert rc == 0
    # B's job was bounced, not A's. The bootout target is `gui/<uid>/<label>`,
    # so the label is the tail -- `in` would also accept `com.agbridge`.
    assert refresh.call("launchctl bootout").endswith("/com.agbridge.hostb"), \
        refresh.calls()
    assert "com.agbridge.hostb.plist" in refresh.call("launchctl bootstrap")
    # ...the wait was for B's bridge: the pattern names B's config, and it
    # matched -- a pattern that matched nothing would forget under a live one.
    assert "--config %s" % (refresh.quoted(refresh.config("hostb")),) \
        in refresh.call("pgrep")
    assert "still running" in out
    # ...and B's map is what was forgotten.
    assert "--config %s" % (refresh.config("hostb"),) \
        in refresh.call("agb forget-rows")
    # The banner names the instance it landed on, though no name was typed:
    # limitation 1's only mitigation, on the invocation that has no --instance.
    assert "instance: hostb" in out
    # Non-vacuity: A's config is a real, different path and is nowhere in what
    # ran -- neither bounced, nor waited on, nor forgotten.
    assert refresh.config() != refresh.config("hostb")
    assert refresh.config() not in "\n".join(refresh.calls())


def test_a_config_no_plist_names_keeps_the_default_label_and_says_so(refresh):
    """The other half of binding the label to the config, and why it warns
    rather than dies.

    An install made with `install.sh mac --config <nondefault>` before the
    plist carried the flag is a plain `com.agbridge` job, so the default is the
    right answer there and refusing to run would break a documented case. But
    the other reading of the same silence -- a named instance whose plist was
    deleted -- is the wrong-job bounce, so it is said out loud instead of
    assumed.
    """
    _rc, out, _err = refresh.run(["--config", "/tmp/elsewhere/config"])
    assert refresh.call("launchctl bootout").endswith("/com.agbridge")
    assert "no plist in" in out and "--instance" in out
    assert "--config /tmp/elsewhere/config" in refresh.call("agb forget-rows")
    # ...and it says what it is about to DO, not only what it did not find: the
    # note above reads as reassurance while the destructive steps run anyway.
    assert "stops com.agbridge" in out and "forgets the" in out
    # ⚠️ And it NAMES the config rather than pointing at it. This said "the
    # config above" for a while, with nothing above it -- the banner naming the
    # config prints later -- in the one branch whose whole job is to be precise
    # about which config is being forgotten.
    assert "bindings of /tmp/elsewhere/config" in out
    assert "above" not in out


def _existing_config(refresh, instance):
    """This instance's conventional config path, with its directory created.

    The directory has to be real for either side of the comparison below to
    have a canonical form at all -- which is the point: the fallback when it
    has none is the DEFAULT label, so a test on a path that cannot resolve
    would pass without the matching it means to check.
    """
    path = refresh.config(instance)
    os.makedirs(os.path.dirname(path))
    with open(path, "w") as handle:
        handle.write("")
    return path


@pytest.mark.parametrize("spelling", ["trailing-slash", "doubled-trailing",
                                      "other-basename"])
def test_a_different_final_component_still_names_the_same_map_and_label(
        refresh, tmp_path, spelling):
    """⚠️ The map is `os.path.dirname(config) + "/rows"`. The BASENAME plays no
    part in it, so a comparison that keeps the basename is strictly narrower
    than the equivalence it claims to mirror.

    All three spellings here open exactly the files `--config <B's config>`
    opens, and all three used to fall through to the DEFAULT label: boot out
    instance A, wait for A's bridge, and forget B's bindings while B's bridge is
    live and merging them back. The `note:` printed on the way makes it loud,
    but every destructive step still ran.

    `<dir>/` is not exotic -- it is what tab-completing the instance's directory
    leaves on the command line, on precisely the invocation
    `pane_config_warning` tells the operator to type by hand.
    """
    refresh.write_plist()                                    # the default job
    refresh.write_plist("com.agbridge.hostb", instance="hostb")
    real = _existing_config(refresh, "hostb")
    if spelling == "trailing-slash":
        spelled = os.path.dirname(real) + "/"
    elif spelling == "doubled-trailing":
        spelled = os.path.dirname(real) + "//"
    else:
        spelled = os.path.join(os.path.dirname(real), "config.bak")
    rc, out, _err = refresh.run(["--config", spelled], alive_polls=10 ** 6,
                                alive_cmdline=refresh.cmdline("hostb"))
    assert rc == 0, out
    assert "no plist in" not in out
    # B's job, B's bridge, B's map -- all three halves agree.
    assert refresh.call("launchctl bootout").endswith("/com.agbridge.hostb"), \
        refresh.calls()
    assert "--config %s" % (refresh.quoted(refresh.config("hostb")),) \
        in refresh.call("pgrep")
    assert "--config %s" % (spelled,) in refresh.call("agb forget-rows")


def test_a_config_naming_the_instance_directory_itself_takes_the_parents_map(
        refresh, tmp_path):
    """The equivalence cuts both ways, and the honest answer is the consistent
    one rather than the intended one.

    `--config ~/.config/agbridge/hostb` with NO trailing slash names a
    directory, and `os.path.dirname` of it is `~/.config/agbridge` -- so
    `forget-rows` opens the DEFAULT instance's map. Whoever typed it probably
    meant hostb, and this is not a spelling of hostb's config: the label that
    goes with that map is the default one, and matching it to hostb's plist
    would be the wrong-label-with-the-wrong-map bounce arriving as a
    convenience. The banner and the note are what say so.
    """
    refresh.write_plist()                                    # the default job
    refresh.write_plist("com.agbridge.hostb", instance="hostb")
    real = _existing_config(refresh, "hostb")
    spelled = os.path.dirname(real)                          # no trailing slash
    rc, out, _err = refresh.run(["--config", spelled])
    assert rc == 0, out
    assert refresh.call("launchctl bootout").endswith("/com.agbridge"), \
        refresh.calls()
    assert "--config %s" % (spelled,) in refresh.call("agb forget-rows")
    # Non-vacuity: hostb's plist really is on disk and really names its config,
    # so the loop had the tempting wrong answer available and did not take it.
    assert refresh.config("hostb") in refresh.plist_text("com.agbridge.hostb")


@pytest.mark.parametrize("spelling", ["doubled-slash", "dot-segment",
                                      "relative"])
def test_a_non_canonical_spelling_of_the_config_still_finds_its_label(
        refresh, tmp_path, spelling):
    """⚠️ The label is matched against the plist's TEXT; the map is derived on
    the other side of `forget-rows` by `os.path.dirname`, which NORMALISES.

    So a spelling that still names the right map can fail to name the right
    label -- and the fallback is the default one. That pair is the accident the
    whole function exists to prevent, reached from the other end: boot out
    instance A, wait for A's bridge (the liveness pattern is built from A's
    plist), then forget B's bindings while B's bridge is live and merging its
    map back over them.

    Every spelling here reaches the same file as `--config <B's config>`: `//`
    and a `.` segment are what a shell tab-completion or a hand-typed path
    produce, and the relative one is `cd ~/.config/agbridge/hostb &&
    agb-refresh --config config` -- all three reachable by someone following
    `pane_config_warning`'s advice to type the path by hand.
    """
    refresh.write_plist()                                    # the default job
    refresh.write_plist("com.agbridge.hostb", instance="hostb")
    real = _existing_config(refresh, "hostb")
    if spelling == "doubled-slash":
        spelled = os.path.dirname(real) + "//" + os.path.basename(real)
    elif spelling == "dot-segment":
        spelled = os.path.join(os.path.dirname(real), ".",
                               os.path.basename(real))
    else:
        # `run` uses tmp_path as its cwd, which is the $HOME these paths are
        # under -- so this is the instance's config named from one level up.
        spelled = os.path.relpath(real, str(tmp_path))
    rc, out, _err = refresh.run(["--config", spelled], alive_polls=10 ** 6,
                                alive_cmdline=refresh.cmdline("hostb"))
    assert rc == 0, out
    # B's job was bounced, not A's -- the bootout target is `gui/<uid>/<label>`.
    assert refresh.call("launchctl bootout").endswith("/com.agbridge.hostb"), \
        refresh.calls()
    assert "no plist in" not in out
    # ...and the wait was for B's bridge, so the two halves agree: the label,
    # the liveness pattern and the map are all instance B's.
    assert "--config %s" % (refresh.quoted(refresh.config("hostb")),) \
        in refresh.call("pgrep")
    assert "--config %s" % (spelled,) in refresh.call("agb forget-rows")


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores the mode bits")
def test_an_unreadable_plist_in_the_directory_does_not_kill_the_run(refresh,
                                                                    tmp_path):
    """⚠️ `set -e` plus an assignment from a command substitution is a trap.

    The scan reads each plist into a variable, and an assignment takes the
    status of the command it ran -- so one unreadable plist (mode 000, or
    root-owned) makes `awk` exit 2 and takes the whole script with it, with its
    stderr discarded: a recovery command that prints nothing and does nothing,
    on account of a file belonging to someone else. The one it is looking for
    is still found, and the rest of the run happens.
    """
    refresh.write_plist()                                    # the default job
    refresh.write_plist("com.agbridge.hostb", instance="hostb")
    locked = tmp_path / "Library" / "LaunchAgents" / "com.agbridge.locked.plist"
    locked.write_text("<plist/>\n")
    os.chmod(str(locked), 0)
    try:
        rc, out, _err = refresh.run(["--config", refresh.config("hostb")])
    finally:
        os.chmod(str(locked), 0o644)
    assert rc == 0, out
    assert "instance: hostb" in out
    assert refresh.call("launchctl bootout").endswith("/com.agbridge.hostb"), \
        refresh.calls()


def test_a_plist_that_names_no_config_stands_for_the_default_one(refresh,
                                                                 tmp_path):
    """"No config at all" is the DEFAULT config -- and never the cwd.

    A plist rendered before the flag existed carries no `--config`, so its value
    is the empty string. Two readings of that were both wrong. Skipping it
    outright is the silent wrong-job bounce the test above covers. Passing it
    through unchanged is worse: `config_map_dir ""` is a perfectly good answer,
    the *current directory*, so `cd <dir> && agb-refresh --config <dir>/` would
    adopt the label of a job that names no config at all.

    The value is the default config, which is what that job's bridge actually
    resolves -- so here, where the run names `$HOME` and the default config
    lives two directories below it, there is no match and the default label with
    its note is the answer.
    """
    (tmp_path / ".config" / "agbridge").mkdir(parents=True)
    refresh.write_plist("com.agbridge.hostb", with_config=False)
    _rc, out, _err = refresh.run(["--config", str(tmp_path) + "/"])
    assert "no plist in" in out
    assert refresh.call("launchctl bootout").endswith("/com.agbridge"), \
        refresh.calls()
    # Non-vacuity twice: that plist is really there and really has no --config,
    # and the config it stands for really does resolve -- so a comparison
    # against the cwd is the only way it could have matched.
    assert "--config" not in refresh.plist_text("com.agbridge.hostb")
    assert os.path.isdir(os.path.dirname(refresh.config()))


def test_two_configs_that_cannot_be_resolved_are_not_the_same_file(refresh,
                                                                   tmp_path):
    """⚠️ The canonicalisation must not FAIL OPEN.

    A config whose directory does not exist (a dangling symlink, an instance
    not installed yet, a typo) has no canonical form -- and "no answer" that
    compared equal to another "no answer" would match the first plist on the
    Mac, which is the wrong label with somebody else's map: precisely the
    bounce this matching exists to prevent, manufactured by the fix for it.
    Neither of these two paths exists, they are not the same file, and the
    only safe answer is the default label with the note that says so.
    """
    refresh.write_plist("com.agbridge.hosta",
                        config=str(tmp_path / "gone-a" / "config"))
    _rc, out, _err = refresh.run(["--config",
                                  str(tmp_path / "gone-b" / "config")])
    assert "no plist in" in out
    assert refresh.call("launchctl bootout").endswith("/com.agbridge"), \
        refresh.calls()
    # Non-vacuity: the plist really is there and really does carry a --config,
    # so the loop had something to compare against.
    assert "gone-a" in refresh.plist_text("com.agbridge.hosta")


def test_a_plist_from_before_the_flag_still_claims_the_default_map(refresh,
                                                                   tmp_path):
    """⚠️ A plist with no `--config` is not "no answer": it is an answer of the
    DEFAULT config.

    The bridge such a plist starts resolves `agb.config_path()` itself, so the
    map it holds is `~/.config/agbridge/rows` -- and skipping it in the scan is
    how a silent wrong-job bounce arrived. `install.sh mac --instance hostb
    --config ~/.config/agbridge/hostb-config` puts a second job's config in the
    default *directory*, which means both jobs share one map. With the default
    job's plist invisible to the scan, `agb-refresh --config <default config>`
    matched only hostb's, adopted `com.agbridge.hostb` and bounced it -- and
    since it was then the ONLY match, `others` was empty and nothing warned.
    The default bridge kept running over the map that was rewritten under it.
    """
    shared = tmp_path / ".config" / "agbridge"
    shared.mkdir(parents=True)
    (shared / "config").write_text("")
    other = shared / "hostb-config"
    other.write_text("")
    # The fixture's own `com.agbridge.plist` is the flagless one.
    refresh.write_plist("com.agbridge.hostb", config=str(other))
    rc, out, _err = refresh.run(["--config", refresh.config()])
    assert rc == 0, out
    assert refresh.call("launchctl bootout").endswith("/com.agbridge"), \
        refresh.calls()
    assert "instance: (default)" in out
    # ...and the sharing was reported rather than resolved in silence.
    assert "more than one" in out and "com.agbridge.hostb" in out
    # Non-vacuity: the other plist really is on disk and really names a config
    # in the same directory, and the default one really carries no `--config`.
    assert str(other) in refresh.plist_text("com.agbridge.hostb")
    assert "--config" not in refresh.plist_text()


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores the mode bits")
def test_a_plist_that_cannot_be_read_claims_nothing(refresh, tmp_path):
    """⚠️ "No `--config` in it" and "could not read it at all" are the same
    empty string, and only the first is an answer.

    `plist_config` discards its stderr, so a plist at mode 000 or owned by
    another user comes back exactly as one rendered before the flag existed.
    Reading that silence as the default config lets a plist which says nothing
    about any map claim one -- the wrong-job bounce arriving through the fix for
    the wrong-job bounce.
    """
    shared = tmp_path / ".config" / "agbridge"
    shared.mkdir(parents=True)
    (shared / "config").write_text("")
    locked = tmp_path / "Library" / "LaunchAgents" / "com.agbridge.locked.plist"
    locked.write_text("<plist/>\n")
    os.chmod(str(locked), 0)
    try:
        rc, out, _err = refresh.run(["--config", refresh.config()])
    finally:
        os.chmod(str(locked), 0o644)
    assert rc == 0, out
    # The default job's own (flagless) plist is the one claimant there is.
    assert refresh.call("launchctl bootout").endswith("/com.agbridge"), \
        refresh.calls()
    assert "more than one" not in out
    assert "locked" not in out


def test_another_programs_launch_agent_claims_nothing(refresh, tmp_path):
    """⚠️ `~/Library/LaunchAgents` belongs to every program on the Mac, not to
    agbridge.

    None of those plists carries a `--config`, so reading "no `--config`" as
    "the default config" makes each of them a claimant of agbridge's map: a
    warning naming somebody's updater, and -- with a name that sorts earlier --
    a `launchctl bootout` aimed at it. The implication is therefore confined to
    the `com.agbridge` label space, which is the only place `install.sh` puts a
    job of its own.
    """
    shared = tmp_path / ".config" / "agbridge"
    shared.mkdir(parents=True)
    (shared / "config").write_text("")
    foreign = tmp_path / "Library" / "LaunchAgents" / "com.aardvark.sync.plist"
    foreign.write_text("<plist version=\"1.0\"><dict>\n"
                       "  <key>Label</key><string>com.aardvark.sync</string>\n"
                       "</dict></plist>\n")
    rc, out, _err = refresh.run(["--config", refresh.config()])
    assert rc == 0, out
    assert refresh.call("launchctl bootout").endswith("/com.agbridge"), \
        refresh.calls()
    assert "more than one" not in out
    assert "aardvark" not in out
    # Non-vacuity: it really is in the directory that was scanned, it really
    # carries no `--config`, and it really sorts before every agbridge label.
    assert foreign.exists() and "--config" not in foreign.read_text()
    assert "com.aardvark.sync.plist" < "com.agbridge.plist"


@pytest.mark.parametrize("spelling", ["as-written", "doubled-slash"])
def test_the_job_that_names_this_exact_path_wins_over_the_glob_order(refresh,
                                                                     spelling):
    """Several jobs can share one map, so the winner has to be CHOSEN rather
    than fall out of the order `*.plist` happens to expand in.

    `com.agbridge.aaa` names a different file in the same directory -- a real
    match, since the map is the directory -- and sorts first. Picking it bounces
    a job that merely shares the rows file over the one whose config this
    literally is.

    Both spellings, because "names this exact path" cannot be a string
    comparison either: `<dir>//config` is the same file as `<dir>/config` and
    the same map as `<dir>/config.bak`, so the preference has to survive
    canonicalisation or it evaporates on the tab-completed spellings this
    command is most often typed with.
    """
    real = _existing_config(refresh, "hostb")
    refresh.write_plist("com.agbridge.aaa",
                        config=os.path.join(os.path.dirname(real),
                                            "config.bak"))
    refresh.write_plist("com.agbridge.hostb", instance="hostb")
    if spelling == "doubled-slash":
        spelled = os.path.dirname(real) + "//" + os.path.basename(real)
    else:
        spelled = real
    rc, out, _err = refresh.run(["--config", spelled])
    assert rc == 0, out
    assert refresh.call("launchctl bootout").endswith("/com.agbridge.hostb"), \
        refresh.calls()
    # The loser is still reported: its bridge holds the same rows file.
    assert "more than one" in out
    assert "com.agbridge.aaa" in out


def test_a_plist_that_names_this_config_beats_one_that_only_implies_it(refresh,
                                                                       tmp_path):
    """⚠️ The implication above must not hand the glob order back the decision
    it was just taken away from.

    A flagless plist stands for the default config, so on a default-map run it
    matches the default config *exactly* -- and `com.agbridge.old.plist`, a
    0.4-era install under a label of its own, sorts before `com.agbridge.plist`.
    Ranking only by exactness would bounce that stale job while the real default
    bridge kept running over the map. A plist that SAYS which config it uses is
    evidence; one that says nothing is inference, and evidence wins.
    """
    shared = tmp_path / ".config" / "agbridge"
    shared.mkdir(parents=True)
    (shared / "config").write_text("")
    refresh.write_plist()                          # declares the default config
    stale = tmp_path / "Library" / "LaunchAgents" / "com.agbridge.old.plist"
    stale.write_text("<plist version=\"1.0\"><dict>\n"
                     "  <key>ProgramArguments</key>\n  <array>\n"
                     "    <string>bridge</string>\n  </array>\n"
                     "</dict></plist>\n")
    rc, out, _err = refresh.run(["--config", refresh.config()])
    assert rc == 0, out
    assert refresh.call("launchctl bootout").endswith("/com.agbridge"), \
        refresh.calls()
    # Both are still reported -- they do share the map.
    assert "more than one" in out and "com.agbridge.old" in out
    # Non-vacuity: the stale one really is a claimant (it is readable, in the
    # label space, and carries no --config) and really does sort first.
    assert "--config" not in stale.read_text()
    assert "com.agbridge.old.plist" < "com.agbridge.plist"


def test_several_jobs_over_one_map_are_named_rather_than_chosen_in_silence(
        refresh):
    """The documented mitigation for two instances installed into one
    directory, which nothing pinned: `grep -rn "more than one" tests/` returned
    nothing while three doc sites promised the warning.

    Both jobs hold the same rows file, so whichever is bounced leaves the other
    running over the map `forget-rows` is about to rewrite. That cannot be
    resolved here -- it can only be said, and then the recovery has to happen
    anyway, which is why it is a warning and not a refusal.
    """
    real = _existing_config(refresh, "hostb")
    refresh.write_plist("com.agbridge.hostb", instance="hostb")
    refresh.write_plist("com.agbridge.zz", config=real)
    rc, out, _err = refresh.run(["--config", real])
    assert rc == 0, out
    assert "more than one" in out
    assert "com.agbridge.hostb" in out and "com.agbridge.zz" in out
    assert refresh.index("agb forget-rows") > -1
    assert refresh.call("launchctl bootout").endswith("/com.agbridge.hostb"), \
        refresh.calls()


def test_the_claimants_line_is_indented_like_the_rest_of_its_warning(refresh):
    """⚠️ It lined up by ACCIDENT, and the accident was in the data.

    Three of the four lines of this block indent by ten spaces; the claimants
    line indented by nine and borrowed the tenth from `$claimants`, which is
    accumulated as `"$claimants $name"` and so always carries a leading space.
    Either half of that is a thing somebody tidies -- the odd nine, or the
    accumulator's stray space -- and tidying either one alone re-indents the
    block with no test to say so.
    """
    real = _existing_config(refresh, "hostb")
    refresh.write_plist("com.agbridge.hostb", instance="hostb")
    refresh.write_plist("com.agbridge.zz", config=real)
    _rc, out, _err = refresh.run(["--config", real])
    block = [line for line in out.split("\n")
             if "more than one" in line or "com.agbridge.zz" in line
             or "keep running and hold" in line or "Pass --label" in line]
    assert len(block) == 4, out
    # The heading is `WARNING:  `; the three continuations align under it.
    for line in block[1:]:
        assert line.startswith(" " * 10), repr(line)
        assert not line.startswith(" " * 11), repr(line)


def test_a_config_in_the_plist_survives_xml_escaping(refresh):
    """⚠️ `install.sh` XML-escapes what it writes, so an `&` in a config path
    reaches the plist as `&amp;` -- and `plutil -lint` accepts it.

    The comment this test replaces claimed such a path "never got written in
    the first place", which is how the missing decode survived review. Left
    undecoded both halves fail quietly: `$config` names a file that does not
    exist, so `forget-rows` answers "the map is already empty" and exits 0
    against a map it never opened, and the liveness pattern matches no bridge
    at all -- zero waits, no warning, forget under a live bridge.
    """
    weird = "/tmp/a&b/config"
    refresh.write_plist("com.agbridge.amp", config=weird)
    rc, out, _err = refresh.run(["--label", "com.agbridge.amp"],
                                alive_polls=10 ** 6,
                                alive_cmdline=refresh.cmdline(config=weird))
    assert rc == 0
    assert "--config %s" % (weird,) in refresh.call("agb forget-rows")
    assert weird in out
    # The wait saw the bridge, which is the half a decoded-but-unused value
    # would still get wrong.
    assert "still running" in out
    # Non-vacuity: the entity really was in the file, and nothing carries it on.
    assert "&amp;" in refresh.plist_text("com.agbridge.amp")
    assert "&amp;" not in "\n".join(refresh.calls())


def test_a_regex_metacharacter_in_the_config_path_is_quoted(refresh):
    """⚠️ `pgrep -f` reads its pattern as an EXTENDED regular expression.

    A config at `/tmp/a+b/config` interpolated raw yields `a+b`, which matches
    `ab` and `aab` and NOT the path it came from: `bridge_alive` answers false
    on the first call, the poll exits with zero waits and no warning, and the
    forget lands under a live bridge -- the same silent failure as an undecoded
    entity, reached from a third direction.
    """
    weird = "/tmp/a+b/config"
    refresh.write_plist("com.agbridge.plus", config=weird)
    rc, out, _err = refresh.run(["--label", "com.agbridge.plus"],
                                alive_polls=10 ** 6,
                                alive_cmdline=refresh.cmdline(config=weird))
    assert rc == 0
    assert refresh.index("pgrep") > -1, "the bridge was never polled for"
    assert "still running" in out, \
        "the pattern did not match the bridge it was built from: %s" \
        % (refresh.call("pgrep"),)


def test_a_quoted_pattern_still_refuses_a_different_bridge(refresh):
    """The negative control for the quoting above: escaping every
    metacharacter must not turn the pattern into one that matches anything.
    Same path, a bridge that is NOT it."""
    weird = "/tmp/a+b/config"
    refresh.write_plist("com.agbridge.plus", config=weird)
    rc, out, _err = refresh.run(["--label", "com.agbridge.plus"],
                                alive_polls=10 ** 6,
                                alive_cmdline=refresh.cmdline(
                                    config="/tmp/aab/config"))
    assert rc == 0
    assert refresh.index("pgrep") > -1, "the bridge was never polled for"
    assert "still running" not in out


@pytest.mark.parametrize("running,waited", [(None, True), ("configb", False)])
def test_the_narrow_pattern_stops_at_a_path_boundary(refresh, running, waited):
    """⚠️ `pgrep -f` matches an unanchored regex against the whole command line.

    The default instance's `.../agbridge/config` is therefore a PREFIX of an
    instance named `configb`'s `.../agbridge/configb/config`, and without the
    trailing boundary a plain `agb-refresh` polls that live process for the full
    10 s and warns that the forget may have been undone -- on the most common
    invocation this command has.

    Both directions, because the fix has a bad failure mode of its own: a
    boundary that matched nothing at all would look identical from the output
    and would forget under the bridge it was meant to wait for.
    """
    refresh.write_plist()               # the default job, modern shape
    rc, out, _err = refresh.run(alive_polls=10 ** 6,
                                alive_cmdline=refresh.cmdline(running))
    assert rc == 0
    assert refresh.index("pgrep") > -1, "the bridge was never polled for"
    assert ("still running" in out) is waited


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


def test_a_narrow_miss_is_unproven_not_proof_that_the_bridge_is_gone(refresh):
    """⚠️ The pattern comes from the PLIST; the question is about the PROCESS.

    Nothing keeps those in step. `install.sh mac --no-load` writes the plist and
    deliberately leaves the running bridge alone (install.sh says so); a bridge
    started by hand for debugging carries whatever was typed; a re-rendered
    plist describes a process that has not restarted. In each the plist carries
    `--config` and the live bridge does not -- so the narrow pattern misses on
    the FIRST poll, the loop ends with zero waits and no output, and `stopped:`
    is printed as a claim nobody checked. The forget then lands under a live
    bridge, which merges-then-writes and re-mints rows against the ids it has
    just closed: the `no such session` spam this script exists to cure.

    On main the pattern was the broad `<agb> bridge` and this bridge WAS waited
    for, which makes taking a narrow miss as proof a regression rather than a
    gap. So a miss asks one more question -- is a bridge up carrying no
    `--config` at all? -- which is answerable because such a bridge resolves
    `agb.config_path()` itself: the DEFAULT map is the only one it can hold.
    This run repairs that map, so the wait is this run's to make. (The mirror
    image, an untagged bridge during a *named* instance's refresh, is
    `test_an_untagged_bridge_is_not_a_named_instances_to_wait_for`.)
    """
    refresh.write_plist()              # the plist DOES carry --config
    rc, out, _err = refresh.run(alive_polls=6,
                                alive_cmdline=refresh.cmdline(with_config=False))
    assert rc == 0
    forget = refresh.index("agb forget-rows")
    polls = [n for n, line in enumerate(refresh.calls())
             if line.startswith("pgrep")]
    assert polls, "the bridge was never polled for"
    # The whole finding in one assertion: a narrow miss used to end the loop
    # after exactly one poll, so the forget ran with nothing waited for.
    assert len(polls) > 1, \
        "the narrow miss ended the wait: %s" % (refresh.calls(),)
    assert max(polls) < forget, refresh.calls()
    # ...and it said why it was waiting for a bridge it could not attribute --
    # ONCE, not on every one of up to forty polls.
    assert "carries no" in out and "--config" in out
    assert out.count("carries no") == 1, out


def test_the_probe_subtracts_rather_than_asking_whether_any_bridge_is_up(
        refresh):
    """Two bridges, one attributable and one not, and only one of them counts.

    "Is any bridge up?" is the pre-instance question and would wait for another
    instance's bridge on every run -- the 10-second warning that narrowing the
    pattern removed. "Is a bridge up that carries no `--config`?" is the one
    worth asking, and because ERE cannot spell "does not contain", it is asked
    by subtracting the tagged bridges from all of them. With a tagged instance
    bridge ALSO running, a probe that compared against zero would answer the
    same as one that subtracted, so the difference is only visible here.
    """
    refresh.write_plist()              # the default job, modern shape
    rc, out, _err = refresh.run(
        alive_polls=6,
        alive_cmdline=[refresh.cmdline("hostb"),          # tagged: not ours
                       refresh.cmdline(with_config=False)])  # untagged
    assert rc == 0
    polls = [n for n, line in enumerate(refresh.calls())
             if line.startswith("pgrep")]
    assert len(polls) > 1, refresh.calls()
    assert max(polls) < refresh.index("agb forget-rows"), refresh.calls()
    assert "carries no" in out


def test_an_untagged_bridge_is_not_a_named_instances_to_wait_for(refresh):
    """⚠️ An untagged bridge holds the DEFAULT map, so a named instance must
    neither wait for it nor warn about it.

    `install.sh mac --instance hostb` does not restart the default job, so on a
    Mac whose default plist predates the flag the default bridge runs with no
    `--config` for ever. The probe is right that such a bridge cannot be told
    apart from this instance's *by its command line* -- but it can be attributed
    anyway: a bridge with no `--config` resolves `agb.config_path()` itself, so
    the only map it can hold is the default one, which is not the map this run
    repairs. Waiting for it was 10 seconds on EVERY hostb refresh, ending in
    "com.agbridge.hostb is still running after 10s" -- provably false, since
    that bridge had exited before the first poll.
    """
    refresh.write_plist("com.agbridge.hostb", instance="hostb")
    rc, out, _err = refresh.run(
        ["--instance", "hostb"], alive_polls=10 ** 6,
        alive_cmdline=refresh.cmdline(with_config=False))
    assert rc == 0
    assert "still running" not in out
    assert "carries no" not in out
    # Non-vacuity: the poll really did run, and it really did ask the narrow
    # question -- an unmatched broad pattern would look the same from the output.
    assert refresh.index("pgrep") > -1, "the bridge was never polled for"
    assert "--config %s" % (refresh.quoted(refresh.config("hostb")),) \
        in refresh.call("pgrep")


def test_the_ten_second_warning_names_what_was_actually_still_running(refresh):
    """The 10s warning is a claim, and it was false in the case it fires most.

    A wait driven by the untagged probe named `$label` -- the job that was
    booted out, which is precisely the process that is NOT what the poll is
    still matching. The probe waits for a bridge it cannot attribute to a label
    at all, so the warning says that instead.
    """
    refresh.write_plist()               # the default job, modern shape
    rc, out, _err = refresh.run(
        alive_polls=10 ** 6, alive_cmdline=refresh.cmdline(with_config=False))
    assert rc == 0
    # The wait itself is the positive control for the gate above: the DEFAULT
    # map is the one an untagged bridge holds, so this run must still wait.
    assert "still running" in out
    assert "no --config is still running" in out
    assert "com.agbridge is still running" not in out


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


# ---------------------------------------------------------------------------
# the acceptance criterion, through the REAL `agb`
# ---------------------------------------------------------------------------

def test_an_instance_refresh_leaves_the_other_instances_map_alone(refresh, mac,
                                                                  tmp_path):
    """⚠️ Acceptance: `agb-refresh --instance hostb` leaves instance A's rows
    untouched -- asserted end to end, against the real `agb`.

    Everywhere else in this file `agb` is a stub that records its argv, which
    can only prove that the right *flag* was passed. That is one half of the
    claim; the other half lives in `agb_mac.instance_paths`, and between the
    two halves is the failure -- a `--config` that reached `forget-rows` and
    was then spent on only one of the two files it owns. So this run swaps the
    stub for the real thing and asks the question the operator asks: after
    repairing B, is A's map exactly as it was?

    Byte-identical, not merely "still has its keys": a rewritten-but-equivalent
    map would mean the other instance's file was opened for writing at all,
    which is the thing that must not happen while its bridge is running.

    ⚠️ `write_tree` is what makes the PLACEMENTS half of that claim real. Left
    out, `tree_workspaces` answers None ("could not ask"), `write_placements` is
    never called at all, and the assertion that A's placements file is unchanged
    holds up nothing -- it passes against an `instance_paths` that derives
    placements from the default config, because no placement is written
    anywhere. It shipped that way once.
    """
    default_rows = mac.rows_path(refresh.config())
    hostb_rows = mac.rows_path(refresh.config("hostb"))
    for path, key in ((default_rows, "aaaa1111"), (hostb_rows, "bbbb2222")):
        rows = mac.RowMap(path)
        rows.bind(key, "ROW-" + key[:2], "title")
        rows.save(force=True)
    mac.write_placements({"aaaa1111": "farm-a"},
                         mac.placements_path(refresh.config()))
    refresh.write_tree({"ROW-aa": "farm-a", "ROW-bb": "farm-b"})
    before = open(default_rows, "rb").read()
    before_placements = open(
        mac.placements_path(refresh.config()), "rb").read()

    refresh.write_plist("com.agbridge.hostb", instance="hostb")
    rc, out, err = refresh.run(["--instance", "hostb", "--no-close",
                                "--agb", conftest.AGB_PATH,
                                "--python", sys.executable])
    assert rc == 0, err

    # B was repaired: its binding is gone, and the banner said whose it was.
    assert mac.load_rows(hostb_rows).bound_keys() == []
    assert "forget bbbb2222" in out
    assert refresh.config("hostb") in out
    # Non-vacuity for the placements half: B's workspace really was remembered,
    # which is the write that the assertion below says did not land on A.
    assert mac.read_placements(
        mac.placements_path(refresh.config("hostb"))) == {"bbbb2222": "farm-b"}
    # ...and A was not touched, by either of the two files `--config` derives.
    assert open(default_rows, "rb").read() == before
    assert open(mac.placements_path(refresh.config()),
                "rb").read() == before_placements
    assert mac.load_rows(default_rows).bound_keys() == ["aaaa1111"]
