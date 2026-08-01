"""`agb-refresh` -- stop the bridge, forget the row bindings, start it again.

Driven through recording `launchctl`/`pgrep` stubs and a fake `agb`, because
what matters is the *order* of the three steps and a real launchd would make
that invisible. The ordering is the whole point of the script: a forget that
lands while the old bridge is still alive can be undone by that bridge's next
map write.
"""

import os
import plistlib
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

    # ⚠️ It can be told to FAIL for one subcommand on one job, because the
    # sweep's whole contract is about what happens when ONE instance's refresh
    # goes wrong while the others still have to be swept -- and against a stub
    # that always succeeds, "instance A failed" is unsayable, so every such test
    # would pass by never reaching the branch it is about.
    #
    # `AGBR_LC_FAIL` is a space-separated list of subcommands; the restart tries
    # `bootstrap` and then falls back to `load -w`, so making a restart fail
    # needs BOTH names. `AGBR_LC_FAIL_MATCH` narrows it to the jobs whose argv
    # contains that text (empty matches every job, which is what a bare
    # `AGBR_LC_FAIL` means).
    stub("launchctl",
         "for c in ${AGBR_LC_FAIL:-}; do\n"
         "    [ \"$1\" = \"$c\" ] || continue\n"
         "    case \"$*\" in *\"${AGBR_LC_FAIL_MATCH:-}\"*) exit 1 ;; esac\n"
         "done\n"
         "exit 0\n")
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
    # because `bridge_alive` feeds those pids to `ps` to find out what each
    # process was actually started with. A stub that answered only with a status
    # makes every question about the attribution unaskable in the safe-looking
    # direction. `$AGBR_ALIVE_CMDLINE` may therefore carry SEVERAL command
    # lines, one per line, which is how "an untagged bridge and a tagged one are
    # both up" is spelled.
    #
    # ⚠️ The pid ENCODES THE LINE -- 4200 + n is the nth line of
    # `$AGBR_ALIVE_CMDLINE` -- which is what makes the `ps` stub below able to
    # answer "which spelling is that process running", the question the whole
    # attribution turns on. `grep -En | cut -d: -f1` is the line number of every
    # match, in order.
    stub("pgrep",
         "n=$(cat '" + str(tmp_path / "polls") + "' 2>/dev/null || echo 0)\n"
         "echo $((n + 1)) > '" + str(tmp_path / "polls") + "'\n"
         "[ -f '" + str(alive) + "' ] || exit 1\n"
         "[ $((n + 1)) -lt \"${AGBR_ALIVE_POLLS:-0}\" ] || exit 1\n"
         "hits=$(printf '%s\\n' \"${AGBR_ALIVE_CMDLINE:-}\" "
         "| grep -En -- \"$2\" | cut -d: -f1 || :)\n"
         "[ -n \"$hits\" ] || exit 1\n"
         "for i in $hits; do echo $((4200 + i)); done\n")
    # `ps -ww -o args= -p <pid>[,<pid>…]` -- the command line of each pid, one
    # per line, which is exactly what the real one prints for that argv.
    #
    # ⚠️ It exists because a pattern cannot answer the question `bridge_alive`
    # asks: `pgrep -f` matches a regex against whatever spelling a process was
    # started with, so a bridge over THIS map spelled `<dir>/./config` matches
    # no narrow pattern and cannot be told from another instance's. Reading the
    # command lines back is the fix, and a stub without `ps` would make every
    # test of it pass by never reaching it.
    #
    # `$AGBR_PS_SILENT` makes it answer nothing while `pgrep` still matches --
    # the "ps will not say" branch, which must wait rather than assume gone.
    stub("ps",
         "[ -z \"${AGBR_PS_SILENT:-}\" ] || exit 1\n"
         "pids=\"\"; prev=\"\"\n"
         "for a in \"$@\"; do\n"
         "    if [ \"$prev\" = -p ]; then pids=$a; fi\n"
         "    prev=$a\n"
         "done\n"
         "[ -n \"$pids\" ] || exit 1\n"
         "IFS=,\n"
         "for p in $pids; do\n"
         "    printf '%s\\n' \"${AGBR_ALIVE_CMDLINE:-}\" "
         "| sed -n \"$((p - 4200))p\"\n"
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

    # ⚠️ A SYMLINK TO THE REAL `agb`, not a stub, and it is load-bearing in one
    # direction only: `$python $agb ...` is still never executed (the fake
    # interpreter below records that call and returns), but `plist_arg` now
    # LOADS `agb_mac` from beside this path to ask
    # `agb_mac.parse_bridge_args` what each plist's argv means. `os.path.realpath`
    # takes the link back to the repo, where `agb_mac` lives -- the same hop
    # `agb.sibling_path` makes for an `agb` symlinked into a `bin/` directory.
    #
    # A stub file here would make every plist read exit 3 ("cannot load
    # agb_mac"), which is the failure the script now dies on rather than
    # guessing through.
    agb = tmp_path / "agb"
    os.symlink(conftest.AGB_PATH, str(agb))
    # ⚠️ `--python` names ONE interpreter and the script has TWO jobs for it:
    # running `agb forget-rows` (`"$python" -S -E "$agb" forget-rows ...`),
    # which is what this stub RECORDS and must never really run, and running
    # `agb instances` (the probe and every plist read), which must be REAL or
    # every plist test would be asserting on a stub`s idea of a parser rather
    # than on the reader that ships.
    #
    # ⚠️ THE DISCRIMINATOR IS `$4`, THE COMMAND WORD, and it used to be
    # `[ "$3" = -c ]` because the reader was an embedded `-c` program. There is
    # no `-c` program any more -- `plist_arg` and the probe both call
    # `agb instances` -- so a stub still routing on `-c` would swallow the probe
    # (empty stdout, no `instances-ok`) and every single test in this file would
    # die at it. `$3` is `$agb` for both commands now; only `$4` tells them
    # apart. The `-c` arm is kept because a `-c` here would be a REGRESSION
    # worth seeing rather than worth stubbing.
    #
    # ⚠️ THE ROUTED CALL IS NOT LOGGED, exactly as the `-c` reader was not.
    # `refresh.calls()` is asserted as an ORDERED SEQUENCE across programs
    # (`test_the_three_steps_happen_in_order`) and as EMPTY by every test whose
    # point is that the script refused before step 1 -- and a plist read is not
    # a step, it is what decides which instance the three steps act on. Logging
    # it would put a variable number of lines in front of `launchctl bootout`
    # and turn `assert refresh.calls() == []` into an assertion about how many
    # plists happened to be in the directory.
    #
    # ⚠️ TWO ARMS FOR THE SWEEP, both keyed on a substring of the recorded argv
    # (in practice the instance's config path, which is the one thing that tells
    # one instance's `forget-rows` from another's):
    #
    #   `AGBR_FORGET_FAIL` -- exit 1, which is exactly what the real
    #     `agb forget-rows` returns when a `--key` was not in the map it opened
    #     (`agb_mac:2892`). It is therefore both "this instance failed" and
    #     "this instance did not hold that key" -- the sweep cannot tell them
    #     apart from a status either, which is the point of the two `--key`
    #     tests below.
    #   `AGBR_INTERRUPT` -- signal the agb-refresh shell that started this
    #     process, which lands the signal exactly INSIDE the window between its
    #     `bootout` and its restart. That window is the only reason the trap
    #     exists, and it cannot be hit reliably from outside: sending a signal
    #     from the test would be a race with the child's own progress.
    python = binder / "fakepython"
    python.write_text(
        "#!/bin/sh\n"
        "if [ \"$3\" = -c ]; then exec '" + sys.executable + "' \"$@\"; fi\n"
        "if [ \"${4:-}\" = instances ]; then exec '"
        + sys.executable + "' \"$@\"; fi\n"
        "printf 'agb %s\\n' \"$*\" >> \"" + str(log) + "\"\n"
        "[ -z \"${AGBR_EAT_STDIN:-}\" ] || cat >/dev/null\n"
        "case \"$*\" in *\"${AGBR_INTERRUPT:-@@nothing@@}\"*)"
        " kill -INT $PPID ;; esac\n"
        "case \"$*\" in *\"${AGBR_FORGET_FAIL:-@@nothing@@}\"*)"
        " exit 1 ;; esac\n"
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

            ⚠️ AND IT CARRIES THE INTERPRETER PREFIX, because `ProgramArguments`
            is the whole command line and not the bridge's argv: the shipped
            template is `<python> -S -E <agb> bridge --config <path>`, so four
            elements go by before the first one `agb bridge` ever sees. This
            helper rendered `["bridge", ...]` for a long time and every plist
            test in this file inherited the shortcut -- which is how a reader
            that walked the WHOLE array, and so read `<agb> --config X bridge`
            (a job that starts no bridge at all) as a claimant of X's map,
            passed a corpus built to catch exactly that class. A harness that
            models the input as simpler than it is proves the property on
            inputs the property is not about.
            """
            value = (config or self.config(instance))
            for raw, entity in (("&", "&amp;"), ("<", "&lt;"), (">", "&gt;")):
                value = value.replace(raw, entity)
            args = ["    <string>%s</string>" % (python,),
                    "    <string>-S</string>",
                    "    <string>-E</string>",
                    "    <string>%s</string>" % (agb,),
                    "    <string>bridge</string>"]
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

        def run(self, args=(), alive_polls=0, alive_cmdline=None,
                ps_silent=False, no_python=False, lc_fail=None,
                lc_fail_match=None, forget_fail=None, interrupt=None,
                cwd=None, script=None, eat_stdin=False):
            """`no_python` omits `--python`, so the script RESOLVES one.

            `script` and `cwd` are what the `$0` tests move: everything else in
            this file invokes an ABSOLUTE path, so the sweep's re-exec would
            never see the two no-slash spellings a human types.

            ⚠️ Every other test passes `--python`, which means the `if [ -z
            "$python" ]` block never runs in them -- so WHERE that block sits
            relative to `bind_label_to_config` is invisible to them, and moving
            it below (where it used to be, before the reader needed an
            interpreter) passes the whole file. Exactly one test omits the flag
            for that reason; it pays for it by getting the real `python3`, which
            runs the fake `agb` for real (a comment, so it does nothing and
            records nothing) -- so it may only assert on the STUBS.
            """
            if alive_polls:
                alive.write_text("")
            env = dict(os.environ)
            env["PATH"] = str(binder) + os.pathsep + env.get("PATH", "")
            env["AGBR_ALIVE_POLLS"] = str(alive_polls)
            # `pgrep` says a bridge is up and `ps` will not say which -- the
            # one branch where nothing can be attributed at all.
            if ps_silent:
                env["AGBR_PS_SILENT"] = "1"
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
            # Which launchctl calls fail, and which `agb forget-rows` call fails
            # or interrupts its own shell -- see the two stubs above. Set only
            # when asked, so every other test keeps the "everything succeeds"
            # environment it was written against.
            if lc_fail:
                env["AGBR_LC_FAIL"] = lc_fail
            if lc_fail_match:
                env["AGBR_LC_FAIL_MATCH"] = lc_fail_match
            if forget_fail:
                env["AGBR_FORGET_FAIL"] = forget_fail
            if interrupt:
                env["AGBR_INTERRUPT"] = interrupt
            if eat_stdin:
                env["AGBR_EAT_STDIN"] = "1"
            argv = ["sh", script or SCRIPT, "--agb", str(agb)]
            if not no_python:
                argv += ["--python", str(python)]
            proc = subprocess.Popen(
                argv + list(args),
                cwd=cwd or str(tmp_path), env=env, stdin=subprocess.PIPE,
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

        def count(self, needle):
            """How many recorded calls contain `needle`.

            The sweep's questions are counting ones -- "was every instance
            bounced exactly once", "did a child re-sweep" -- which `index` and
            `call`, both of which answer about the FIRST match, cannot ask.
            """
            return len([line for line in self.calls() if needle in line])

    made = Refresh()
    # Exposed for the `$0` tests (a copy of this script on `$PATH` needs a
    # directory that is on it) and for tests that write a plist somewhere other
    # than the conventional place.
    made.bindir = binder
    made.agentsdir = agents
    return made


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

    ⚠️ A bare run SWEEPS now, so this banner is a child's and "(default)" is
    what the child says about the one instance this fixture installs -- not a
    claim that a flagless run means the default one. That reading is gone; see
    the sweep section at the bottom of this file.
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
    failure than the one it fixes.

    ⚠️ It is also the guard that keeps "an instance left without a running
    bridge is an error" a SWEEP rule. This run ends with no bridge started (no
    plist to bootstrap) and must still exit 0, because it is a documented
    recipe; making it fail would repeal that promise to buy a rule that earns
    its keep only where a whole Mac is being swept. See
    `test_an_instance_left_without_a_bridge_fails_the_sweep`.
    """
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

    ⚠️ Re-reasoned for the sweep, because "a plain refresh ignores a named
    instance" is no longer a property of the COMMAND: a plain refresh visits
    every instance that is installed. It is still a property of each CHILD, and
    that is what is asserted here -- hostb has a running bridge and no plist, so
    the sweep visits one label and the default instance's child must not wait
    for a bridge that is not its own. The version where both are installed and
    exactly one may wait is
    `test_one_instances_live_bridge_does_not_hold_another_instances_wait`.
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


def test_a_config_path_with_a_blank_survives_the_recipe_being_retyped(
        refresh, mac, tmp_path):
    """⚠️ The stale-row hint is printed to be RETYPED, so it has to be a valid
    shell command -- and a config path may contain a blank or a `$`
    (`install.sh --config` asks only that it be absolute).

    Unquoted, `agb-refresh --config /Users/z/My Configs/config` is `--config
    /Users/z/My` plus an unexpected word: the script refuses it, or -- worse --
    acts on a map nobody named. `pane_command` has always `shlex.quote`d the
    row's own command for exactly this reason; the recipe printed beside that
    row did not, in the same file.
    """
    import shlex
    weird = str(tmp_path / "My Configs" / "hostb" / "config")
    refresh.write_plist("com.agbridge.sp", config=weird)
    recipe = mac.refresh_recipe(weird)
    # The whole finding in one assertion: retyped, it is still three words.
    assert shlex.split(recipe) == ["agb-refresh", "--config", weird], recipe
    rc, _out, _err = refresh.run(shlex.split(recipe)[1:], alive_polls=10 ** 6,
                                 alive_cmdline=refresh.cmdline(config=weird))
    assert rc == 0
    # ...and the path having survived as ONE word is what let the label be
    # adopted from the plist naming it, and the forget reach that map.
    assert refresh.call("launchctl bootout").endswith("/com.agbridge.sp"), \
        refresh.calls()
    assert "--config %s" % (weird,) in refresh.call("agb forget-rows")


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

    The reader is asked about each plist in the directory, and an ASSIGNMENT
    from a command substitution takes the status of the command it ran -- so one
    unreadable plist (mode 000, or root-owned) makes the reader exit 2 and takes
    the whole script with it: a recovery command that prints nothing and does
    nothing, on account of a file belonging to someone else. The one it is
    looking for is still found, and the rest of the run happens.

    ⚠️ Which is why `bind_label_to_config` spells the read as an `if` and not as
    `value=$(...) || value=""`: the status is ALSO the answer to "did this file
    say anything at all", and the guard above it needs that (a file that says
    nothing must not go on to imply the DEFAULT config).
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


def test_the_interpreter_is_resolved_before_the_label_is_bound(refresh,
                                                               tmp_path):
    """⚠️ ORDER, and it is invisible to every other test in this file.

    `plist_arg` reads a plist with `plistlib`, so it needs `$python` -- and the
    resolution block used to sit BELOW `bind_label_to_config`, beside the `$agb`
    check, because nothing before it had ever needed an interpreter. Left there,
    every read inside `bind_label_to_config` runs `"" -S -E -c ...`: a "command
    not found" per plist, so every plist answers nothing, the label falls through
    to the DEFAULT one, and the run bounces the default job and forgets its
    bindings while hostb's bridge is live. Silently -- the note it prints is the
    same one a Mac with no hostb plist gets.

    Every other test passes `--python`, which skips that block entirely, so the
    mistake is unreachable from them: moving the block back down leaves the file
    green. This one omits the flag and gets the real `python3`, which is why it
    asserts only on the recording stubs.
    """
    real = _existing_config(refresh, "hostb")
    refresh.write_plist("com.agbridge.hostb", instance="hostb")
    rc, out, _err = refresh.run(["--config", real], no_python=True)
    assert rc == 0, out
    # The label came out of hostb's plist, which took a working interpreter.
    assert "instance: hostb" in out, out
    assert refresh.call("launchctl bootout").endswith("/com.agbridge.hostb"), \
        refresh.calls()
    assert "no plist in" not in out, out


def test_a_plist_that_cannot_be_parsed_claims_nothing_either(refresh, tmp_path):
    """⚠️ The THIRD reading of the same empty string, and the one the parser
    added: "this file is readable and is not a plist".

    A plist truncated by a full disk, or some other file that ended up under a
    `com.agbridge.*` name, is perfectly readable -- so the `-r` test this guard
    used to be made of passed it straight through to "carries no `--config`,
    therefore stands for the DEFAULT config". That is a file which says nothing
    about any map claiming one, with a real job's label on it: the run bounces
    THAT label and forgets the default instance's bindings.

    So the guard is the reader's STATUS, not `-r`: exit 2 means the file said
    nothing, and a file that says nothing is skipped. Nothing is lost by
    skipping it -- launchd cannot load it either, so no bridge was ever started
    from it.
    """
    shared = tmp_path / ".config" / "agbridge"
    shared.mkdir(parents=True)
    (shared / "config").write_text("")
    agents = tmp_path / "Library" / "LaunchAgents"
    # Readable, in the label space, and not a plist. Named so it sorts BEFORE
    # `com.agbridge.plist`: the glob order is what would hand it the run.
    (agents / "com.agbridge.aaa.plist").write_text("<plist version=\"1.0\">"
                                                   "<dict><key>Program")
    rc, out, _err = refresh.run(["--config", refresh.config()])
    assert rc == 0, out
    # The default job's own (flagless) plist is the one claimant there is.
    assert refresh.call("launchctl bootout").endswith("/com.agbridge"), \
        refresh.calls()
    assert "more than one" not in out
    assert "aaa" not in out
    assert "com.agbridge.aaa" not in "\n".join(refresh.calls())


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


def test_each_running_bridge_is_attributed_rather_than_counted(refresh):
    """Two bridges, one attributable elsewhere and one not, and only one counts.

    "Is any bridge up?" is the pre-instance question and would wait for another
    instance's bridge on every run -- the 10-second warning that narrowing the
    pattern removed. What is asked instead is one question per PROCESS, off its
    own command line: this run repairs the default map, so the untagged bridge
    (which can hold no other) is this run's to wait for, while the hostb-tagged
    one is not. With a tagged bridge ALSO up, an implementation that stopped at
    "some bridge is up" answers the same, so the difference is only visible
    here.
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


def test_a_bridge_over_this_map_under_another_spelling_is_waited_for(refresh,
                                                                    tmp_path):
    """⚠️ The liveness pattern is TEXT; a map is a canonical DIRECTORY.

    `pgrep -f` matches a regex against whatever spelling the process was started
    with, and the far side of a regex match cannot be canonicalised -- so a
    bridge over THIS instance's map, started with `<dir>/./config` (a
    re-rendered plist, a symlinked `$HOME`, a relative path), matches no narrow
    pattern. The subtraction that used to follow a narrow miss then counted it
    on both sides -- "it carries `--config`, so it is somebody else's" -- and
    did not wait: `forget-rows` under a live bridge, which merges-then-writes
    and re-mints rows against the ids it has just closed. That is the
    label-side bug `same_map` was extracted to fix, arriving on the process
    side, and no pattern can close it. Reading the command lines back with `ps`
    and attributing each one with `same_map` is what closes it.
    """
    (tmp_path / ".config" / "agbridge" / "hostb").mkdir(parents=True)
    refresh.write_plist("com.agbridge.hostb", instance="hostb")
    spelled = refresh.config("hostb").replace("/hostb/", "/hostb/./")
    assert spelled != refresh.config("hostb"), "the spellings must differ"
    rc, out, _err = refresh.run(["--instance", "hostb"],
                                alive_polls=10 ** 6,
                                alive_cmdline=refresh.cmdline(config=spelled))
    assert rc == 0
    # It waited, which is the whole property: the forget came after every poll.
    polls = [n for n, line in enumerate(refresh.calls())
             if line.startswith("pgrep")]
    assert len(polls) > 1, refresh.calls()
    assert max(polls) < refresh.index("agb forget-rows"), refresh.calls()
    # ...and reported it as what it is -- this instance's bridge under another
    # spelling, not an untagged one and not `$label`.
    assert "resolves to this same map" in out
    assert "names this same map is still running" in out
    assert "carries no" not in out
    assert "com.agbridge.hostb is still running" not in out
    # Non-vacuity: the narrow pattern really did miss it, which is the premise.
    assert refresh.index("pgrep") > -1, "the bridge was never polled for"
    assert spelled not in refresh.call("pgrep")


def test_another_instances_bridge_is_not_waited_for_when_both_paths_resolve(
        refresh, tmp_path):
    """The negative control for the test above, and it needs its own tree.

    `test_the_wait_ignores_another_instances_bridge` passes even if `same_map`
    cannot resolve either side, because failing to resolve gives the same
    answer as resolving to two different directories -- "not this map". Here
    BOTH config directories exist, so the comparison that decides it is a real
    one and an attribution that simply said yes would be caught.
    """
    (tmp_path / ".config" / "agbridge" / "hostb").mkdir(parents=True)
    refresh.write_plist("com.agbridge.hostb", instance="hostb")
    rc, out, _err = refresh.run(["--instance", "hostb"],
                                alive_polls=10 ** 6,
                                alive_cmdline=refresh.cmdline())
    assert rc == 0
    assert "still running" not in out
    assert "resolves to this same map" not in out


def test_a_bridge_ps_will_not_name_is_waited_for_rather_than_assumed_gone(
        refresh):
    """⚠️ Unattributable is not gone -- the same rule as a narrow miss.

    `pgrep` says a bridge is up and `ps` will not say what it was started with
    (no `ps`, a `ps` that refuses `-ww -o args=`, a locked-down box). Nothing
    can be attributed at all then, and the safe answer is the bounded wait: the
    alternative is `forget-rows` under a live bridge on every run of a recovery
    command. The bridge here is ANOTHER instance's, which is exactly the one a
    working `ps` would rule out -- so this cannot pass by accident.
    """
    refresh.write_plist("com.agbridge.hostb", instance="hostb")
    rc, out, _err = refresh.run(["--instance", "hostb"],
                                alive_polls=10 ** 6,
                                alive_cmdline=refresh.cmdline(),
                                ps_silent=True)
    assert rc == 0
    polls = [n for n, line in enumerate(refresh.calls())
             if line.startswith("pgrep")]
    assert len(polls) > 1, refresh.calls()
    assert "ps would not say" in out
    assert "ps would not name is still running" in out


def test_a_config_path_with_a_blank_in_it_is_still_attributed(refresh,
                                                              tmp_path):
    """⚠️ `ps` prints the arguments FLATTENED, so the value of `--config`
    cannot be delimited: a config path containing a blank is indistinguishable
    from the flag that follows it, and blanks are allowed (`install.sh
    --config` asks only that the path be absolute).

    So every blank-terminated prefix is offered to `same_map`, longest first.
    An over-match costs a bounded 10 s wait; an under-match is the forget
    landing under a live bridge, so it errs in the direction it can afford.
    The trailing option here carries a `/` on purpose -- without one the first
    candidate's dirname is already right and the walk never runs.
    """
    home = tmp_path / "My Configs" / "hostb"
    home.mkdir(parents=True)
    declared = str(home / "config")
    refresh.write_plist("com.agbridge.sp", config=declared)
    rc, out, _err = refresh.run(
        ["--label", "com.agbridge.sp"], alive_polls=10 ** 6,
        alive_cmdline=refresh.cmdline(config=str(home) + "/./config")
        + " --rows /tmp/somewhere/rows")
    assert rc == 0
    assert "resolves to this same map" in out
    assert "names this same map is still running" in out


def test_a_bridge_that_repeats_the_config_flag_is_read_the_way_agb_reads_it(
        refresh, tmp_path):
    """⚠️ `agb bridge` keeps the LAST `--config`; attribution read the FIRST.

    `parse_bridge_args` (agb_mac) reads its value flags into a dict with no
    duplicate check -- `opts[name] = inline` -- so `… bridge --config /old
    --config <this instance's>` is a bridge holding THIS map. Attributing it
    from the first occurrence gave `/old`, and the blank-prefix walk that
    follows only shortens (`/old --config <this>`, `/old --config`, `/old`), so
    the winning value was never offered: not ours, zero waits, and `forget-rows`
    under a live bridge -- which merges-then-writes and re-mints rows against
    the ids it has just closed. The narrow pattern cannot save it either: it is
    `<agb> bridge --config <path>`, and here the flag that follows `bridge` is
    the other one.
    """
    (tmp_path / ".config" / "agbridge" / "hostb").mkdir(parents=True)
    refresh.write_plist("com.agbridge.hostb", instance="hostb")
    rc, out, _err = refresh.run(
        ["--instance", "hostb"], alive_polls=10 ** 6,
        alive_cmdline=refresh.cmdline(config="/tmp/somewhere-else/config")
        + " --config " + refresh.config("hostb"))
    assert rc == 0
    # It waited: every poll came before the forget, and there were many.
    polls = [n for n, line in enumerate(refresh.calls())
             if line.startswith("pgrep")]
    assert len(polls) > 4, refresh.calls()
    assert max(polls) < refresh.index("agb forget-rows"), refresh.calls()
    assert "names this same map is still running" in out
    # Non-vacuity: the narrow pattern really did miss it, which is the premise --
    # otherwise this passes without `cmdline_is_ours` being consulted at all.
    assert refresh.index("pgrep") > -1, "the bridge was never polled for"
    assert "--config %s" % (refresh.quoted(refresh.config("hostb")),) \
        in refresh.call("pgrep")
    assert "ps -ww" in " ".join(refresh.calls()), \
        "the command lines were never read back: %s" % (refresh.calls(),)


def test_a_bridge_repeating_the_flag_over_two_other_maps_is_not_waited_for(
        refresh, tmp_path):
    """The negative control for the test above, and it is the one that matters:
    offering EVERY `--config` on the line must not degenerate into "a repeated
    flag is always ours".

    Both directories exist, so `same_map` really resolves both sides and an
    attribution that just said yes to any duplicate would be caught here rather
    than passing because nothing could be resolved.
    """
    for name in ("hostb", "hostc"):
        (tmp_path / ".config" / "agbridge" / name).mkdir(parents=True)
    (tmp_path / "elsewhere").mkdir()
    refresh.write_plist("com.agbridge.hostb", instance="hostb")
    rc, out, _err = refresh.run(
        ["--instance", "hostb"], alive_polls=10 ** 6,
        alive_cmdline=refresh.cmdline(config=str(tmp_path / "elsewhere"
                                                 / "config"))
        + " --config " + refresh.config("hostc"))
    assert rc == 0
    assert "still running" not in out
    assert "resolves to this same map" not in out


def test_a_plist_that_repeats_the_config_flag_is_read_the_way_launchd_runs_it(
        refresh, tmp_path):
    """The same rule on the other side of the same question.

    A plist's `ProgramArguments` IS the argv, so a repeated `--config` there is
    resolved by `agb bridge` last-one-wins too. Reading the first pair makes
    both halves of this script act on a path no process is running on: the job
    whose bridge holds this map is not recognised as a claimant (so the DEFAULT
    label is booted out -- the wrong-job bounce `bind_label_to_config` exists to
    prevent), and the liveness pattern is built from the loser.

    `install.sh` renders exactly one pair, so only a hand-edited plist gets
    here -- the same population as the hand-started bridge above.
    """
    (tmp_path / ".config" / "agbridge" / "hostb").mkdir(parents=True)
    agents = tmp_path / "Library" / "LaunchAgents"
    (agents / "com.agbridge.hostb.plist").write_text(
        "<plist version=\"1.0\"><dict>\n"
        "  <key>ProgramArguments</key>\n  <array>\n"
        "    <string>bridge</string>\n"
        "    <string>--config</string>\n"
        "    <string>/tmp/decoy/config</string>\n"
        "    <string>--config</string>\n"
        "    <string>%s</string>\n"
        "  </array>\n</dict></plist>\n" % (refresh.config("hostb"),))
    rc, out, _err = refresh.run(["--config", refresh.config("hostb")])
    assert rc == 0
    # The label came from the pair launchd actually starts the bridge with.
    assert "stopped:  com.agbridge.hostb" in out, out
    assert "com.agbridge is still" not in out
    # ...and so did the liveness pattern.
    assert "--config %s" % (refresh.quoted(refresh.config("hostb")),) \
        in refresh.call("pgrep")
    assert "/tmp/decoy/config" not in refresh.call("pgrep")


def test_an_inline_config_flag_is_offered_even_before_a_later_one(refresh,
                                                                 tmp_path,
                                                                 mac):
    """⚠️ `agb bridge` takes `--config=<path>` as well as `--config <path>`,
    and the attribution scanned for the two spellings with two `case` arms.

    `case` picks by ARM order, never by where the text sits, so a line carrying
    an INLINE occurrence *before* a space-form one took the space arm and cut
    there -- discarding the inline value and every prefix of it. Under the
    one-flag-with-blanks reading this walk exists for, that discarded value is
    exactly the one `parse_bridge_args` keeps (asserted below, so the premise
    cannot rot), so the winning candidate was never offered: not ours, zero
    waits, `forget-rows` under a live bridge.

    The earlier round's permutation check could not see this: it only ever built
    lines out of SEPARATE flags, and in that reading the parser keeps the LAST
    value, which the space arm does reach. The loss only becomes an UNDER-match
    when the two markers are one argument.
    """
    # A directory whose name contains the literal text the flag is spelled
    # with. Contrived, and the whole reason the blank walk exists: `ps` flattens
    # argv, so this is byte-for-byte a line carrying two flags.
    home = tmp_path / "conf --config b"
    home.mkdir()
    declared = str(home / "config")
    # The premise, in the parser's own words: ONE argument, value kept whole.
    assert mac.parse_bridge_args(["--config=" + declared])["config"] == declared
    refresh.write_plist("com.agbridge.sp", config=declared)
    rc, out, _err = refresh.run(
        ["--label", "com.agbridge.sp"], alive_polls=10 ** 6,
        alive_cmdline=refresh.cmdline(config=declared).replace(
            " --config ", " --config=", 1))
    assert rc == 0
    assert "names this same map is still running" in out
    # Non-vacuity: the narrow pattern is built with a blank after `--config`, so
    # it cannot match the inline spelling -- the answer came from reading the
    # command line back, which is the code under test.
    assert "ps -ww" in " ".join(refresh.calls()), \
        "the command lines were never read back: %s" % (refresh.calls(),)


def test_config_as_a_substring_of_another_word_is_not_the_flag(refresh):
    """The marker the scan cuts on is now the bare `--config`, shared by both
    spellings -- so what FOLLOWS it is what says this is the flag at all.

    Without that check `--configs/b` inside somebody's `--statedir` makes a
    genuinely untagged bridge read as tagged, and a tagged line that names no
    map is "somebody else's": the default-map bridge stops being waited for,
    and `forget-rows` lands under it. The direction matters -- an untagged
    bridge can only be holding the default instance's map, so on a default run
    it is always ours.
    """
    refresh.write_plist()              # so the narrow pattern misses and the
    rc, out, _err = refresh.run(       # command line is actually read back
        alive_polls=6,
        alive_cmdline=refresh.cmdline(with_config=False)
        + " --statedir /net/a --configs/b")
    assert rc == 0
    polls = [n for n, line in enumerate(refresh.calls())
             if line.startswith("pgrep")]
    assert len(polls) > 1, \
        "the substring was read as a --config: %s" % (refresh.calls(),)
    assert max(polls) < refresh.index("agb forget-rows"), refresh.calls()
    # ...and it is DESCRIBED as what it is. The note is read off the answer the
    # attribution computed, not off a second `case` that would call this bridge
    # one whose `--config` is "spelled differently".
    assert "carries no" in out and "--config" in out
    assert "resolves to this same map" not in out


def test_a_plist_whose_config_flag_is_inline_still_names_its_instance(refresh,
                                                                     tmp_path,
                                                                     mac):
    """A plist's `ProgramArguments` IS the argv, and `parse_bridge_args`
    partitions each argument on `=` -- so `<string>--config=/path</string>`
    starts the bridge on `/path` just as the two-element form does.

    Reading only the pair answered `""` for such a plist, and `""` is not "no
    answer" downstream: `bind_label_to_config` reads it as the DEFAULT config,
    so this instance's own job claimed the default map, the run fell through to
    the DEFAULT label, and it forgot hostb's bindings with hostb's bridge still
    up -- the wrong-job bounce, arriving through the reader.
    """
    (tmp_path / ".config" / "agbridge" / "hostb").mkdir(parents=True)
    assert mac.parse_bridge_args(
        ["--config=" + refresh.config("hostb")])["config"] \
        == refresh.config("hostb")
    agents = tmp_path / "Library" / "LaunchAgents"
    (agents / "com.agbridge.hostb.plist").write_text(
        "<plist version=\"1.0\"><dict>\n"
        "  <key>ProgramArguments</key>\n  <array>\n"
        "    <string>bridge</string>\n"
        "    <string>--config=%s</string>\n"
        "  </array>\n</dict></plist>\n" % (refresh.config("hostb"),))
    rc, out, _err = refresh.run(["--config", refresh.config("hostb")])
    assert rc == 0
    assert "stopped:  com.agbridge.hostb" in out, out
    # ...and the liveness pattern was narrowed on the value it carries, rather
    # than left broad because the plist "said nothing".
    assert "--config %s" % (refresh.quoted(refresh.config("hostb")),) \
        in refresh.call("pgrep")
    assert "matches ANY" not in out


def test_a_plist_mixing_the_two_spellings_is_read_last_one_wins(refresh,
                                                                tmp_path):
    """Last-wins has to hold ACROSS the spellings, not within each.

    `--config /decoy --config=<real>` leaves the bridge on `<real>`; a reader
    that only knows the pair answers `/decoy` -- the loser -- and both halves of
    this script then act on a path no process is running on.
    """
    (tmp_path / ".config" / "agbridge" / "hostb").mkdir(parents=True)
    agents = tmp_path / "Library" / "LaunchAgents"
    (agents / "com.agbridge.hostb.plist").write_text(
        "<plist version=\"1.0\"><dict>\n"
        "  <key>ProgramArguments</key>\n  <array>\n"
        "    <string>bridge</string>\n"
        "    <string>--config</string>\n"
        "    <string>/tmp/decoy/config</string>\n"
        "    <string>--config=%s</string>\n"
        "  </array>\n</dict></plist>\n" % (refresh.config("hostb"),))
    rc, out, _err = refresh.run(["--config", refresh.config("hostb")])
    assert rc == 0
    assert "stopped:  com.agbridge.hostb" in out, out
    assert "--config %s" % (refresh.quoted(refresh.config("hostb")),) \
        in refresh.call("pgrep")
    assert "/tmp/decoy/config" not in refresh.call("pgrep")


def test_the_value_of_a_config_pair_is_never_re_read_as_a_flag(refresh,
                                                               tmp_path, mac):
    """`parse_bridge_args` takes the argument after `--config` VERBATIM -- it
    never looks at whether that argument itself looks like a flag -- so a pair
    whose value is the text `--config=<path>` puts the bridge on a config file
    literally named that (asserted below, because the claim is the parser's and
    not this test's).

    Now that the reader knows the inline spelling too, the value line it just
    consumed must not fall through into the inline rule, or the reader answers
    `<path>` where the bridge is running on `--config=<path>`: a different file
    in a different directory, hence a different map.
    """
    value = "--config=" + refresh.config("hostb")
    assert mac.parse_bridge_args(["--config", value])["config"] == value
    agents = tmp_path / "Library" / "LaunchAgents"
    (agents / "com.agbridge.hostb.plist").write_text(
        "<plist version=\"1.0\"><dict>\n"
        "  <key>ProgramArguments</key>\n  <array>\n"
        "    <string>bridge</string>\n"
        "    <string>--config</string>\n"
        "    <string>%s</string>\n"
        "  </array>\n</dict></plist>\n" % (value,))
    rc, out, _err = refresh.run(["--label", "com.agbridge.hostb"])
    assert rc == 0
    assert "config %s" % (value,) in out, out
    assert "bridge --config %s" % (refresh.quoted(value),) \
        in refresh.call("pgrep")


def test_a_config_flag_after_the_argv_array_does_not_overwrite_it(refresh,
                                                                  tmp_path):
    """⚠️ A plist is not an argv -- only ONE of its keys is.

    Every other key carries `<string>`s too, and reading them let a
    `<string>--config</string><string>/decoy</string>` pair sitting in an
    `EnvironmentVariables` dict OVERWRITE the real one in `ProgramArguments`,
    because the reader takes the last occurrence (which is right, inside argv,
    where a repeated flag really does overwrite). Nothing about it was loud:
    `bind_label_to_config` then failed to recognise hostb's own job, fell
    through to the DEFAULT label, and forgot hostb's bindings with hostb's
    bridge still up -- the wrong-job bounce, arriving through the reader.
    """
    real = _existing_config(refresh, "hostb")
    agents = tmp_path / "Library" / "LaunchAgents"
    (agents / "com.agbridge.hostb.plist").write_text(
        "<plist version=\"1.0\"><dict>\n"
        "  <key>ProgramArguments</key>\n  <array>\n"
        "    <string>bridge</string>\n"
        "    <string>--config</string>\n"
        "    <string>%s</string>\n"
        "  </array>\n"
        "  <key>EnvironmentVariables</key>\n  <dict>\n"
        "    <key>NOTE</key>\n    <string>--config</string>\n"
        "    <key>NOTE2</key>\n    <string>/tmp/decoy/config</string>\n"
        "  </dict>\n</dict></plist>\n" % (real,))
    rc, out, _err = refresh.run(["--config", real])
    assert rc == 0, out
    assert "stopped:  com.agbridge.hostb" in out, out
    assert "--config %s" % (refresh.quoted(real),) in refresh.call("pgrep")
    assert "/tmp/decoy/config" not in out
    assert "/tmp/decoy/config" not in "\n".join(refresh.calls())


def test_a_config_flag_before_the_argv_array_manufactures_nothing(refresh,
                                                                  tmp_path):
    """The same boundary, from the other end: a plist whose argv carries no
    `--config` must not be given one by a string belonging to another key.

    `WatchPaths` is a real launchd key holding an array of strings, and a
    manufactured config is worse than a missing one -- the plist stands for the
    DEFAULT config when it says nothing, which is what the bridge it starts
    resolves, while a manufactured one narrows the liveness pattern onto a path
    no process carries: zero waits, no warning, and the forget landing under the
    live bridge.
    """
    agents = tmp_path / "Library" / "LaunchAgents"
    (agents / "com.agbridge.plist").write_text(
        "<plist version=\"1.0\"><dict>\n"
        "  <key>WatchPaths</key>\n  <array>\n"
        "    <string>--config</string>\n"
        "    <string>/tmp/manufactured/config</string>\n"
        "  </array>\n"
        "  <key>ProgramArguments</key>\n  <array>\n"
        "    <string>bridge</string>\n"
        "  </array>\n</dict></plist>\n")
    rc, out, _err = refresh.run(
        alive_polls=3, alive_cmdline=refresh.cmdline(with_config=False))
    assert rc == 0, out
    # It read the plist as saying nothing, waited broadly, and said so.
    assert "no --config in" in out
    assert "--config" not in refresh.call("pgrep")
    assert "/tmp/manufactured" not in out
    forget = refresh.index("agb forget-rows")
    polls = [n for n, line in enumerate(refresh.calls())
             if line.startswith("pgrep")]
    assert polls and max(polls) < forget, refresh.calls()


@pytest.mark.parametrize("nested", ["an-array", "a-string"])
def test_a_key_named_program_arguments_elsewhere_opens_no_argv(refresh,
                                                               tmp_path,
                                                               nested):
    """Only the TOP-LEVEL `ProgramArguments` is argv.

    A key of that name can also sit in a nested `<dict>` under some other key,
    with a value that is an array of strings (`an-array`) or not an array at
    all (`a-string`, where the decoy array then arrives under a DIFFERENT key
    entirely). Either way launchd runs the top-level one and nothing else, and
    a reader that walked strings by proximity would run somebody else's.

    ⚠️ The decoy is AFTER the real argv on purpose. In front of it the reader
    answers correctly for the wrong reason -- last-wins overwrites the decoy
    with the real value -- and the guard passes with no boundary at all.

    ⚠️ Both shapes are VALID plists, and that is not incidental: this test used
    to build one that was not (two values under one key), so `plistlib` refused
    the file and the "no decoy" assertion below held because nothing was read at
    all. A decoy launchd would reject is not a decoy.
    """
    real = _existing_config(refresh, "hostb")
    decoy = ("    <array>\n"
             "      <string>--config</string>\n"
             "      <string>/tmp/decoy/config</string>\n"
             "    </array>\n")
    if nested == "an-array":
        inner = "    <key>ProgramArguments</key>\n" + decoy
    else:
        inner = ("    <key>ProgramArguments</key>\n"
                 "    <string>set by hand</string>\n"
                 "    <key>OTHER</key>\n" + decoy)
    text = (("<plist version=\"1.0\"><dict>\n"
             "  <key>ProgramArguments</key>\n  <array>\n"
             "    <string>bridge</string>\n"
             "    <string>--config</string>\n"
             "    <string>%s</string>\n"
             "  </array>\n"
             "  <key>EnvironmentVariables</key>\n  <dict>\n" % (real,))
            + inner + "  </dict>\n</dict></plist>\n")
    # Non-vacuity: launchd reads this file, its argv is the real one, and the
    # decoy really is in it.
    doc = plistlib.loads(text.encode())
    assert doc["ProgramArguments"][-2:] == ["--config", real], doc
    assert "/tmp/decoy/config" in repr(doc["EnvironmentVariables"]), doc
    agents = tmp_path / "Library" / "LaunchAgents"
    (agents / "com.agbridge.hostb.plist").write_text(text)
    rc, out, _err = refresh.run(["--config", real])
    assert rc == 0, out
    assert "stopped:  com.agbridge.hostb" in out, out
    assert "/tmp/decoy/config" not in "\n".join(refresh.calls()) + out


def test_a_flag_dangling_at_the_end_of_argv_consumes_nothing_after_it(refresh,
                                                                      tmp_path):
    """`</array>` ends the argv, so a value-taking flag left last in it takes
    no value at all.

    Such a plist starts no bridge on anything -- `agb bridge --config` with
    nothing after it is the parser's own "needs a value" -- so the honest answer
    is "no config", not the next `<string>` in the FILE, which belongs to
    another key entirely. Reading that one would hand hostb's label to a job
    whose bridge never started.
    """
    real = _existing_config(refresh, "hostb")
    agents = tmp_path / "Library" / "LaunchAgents"
    (agents / "com.agbridge.hostb.plist").write_text(
        "<plist version=\"1.0\"><dict>\n"
        "  <key>ProgramArguments</key>\n  <array>\n"
        "    <string>bridge</string>\n"
        "    <string>--config</string>\n"
        "  </array>\n"
        "  <key>WorkingDirectory</key>\n  <string>%s</string>\n"
        "</dict></plist>\n" % (real,))
    rc, out, _err = refresh.run(["--config", real])
    assert rc == 0, out
    # hostb's plist claims nothing, so no job claims this map and the run says
    # so rather than bouncing hostb on the strength of a WorkingDirectory.
    assert "stopped:  com.agbridge\n" in out, out
    assert "com.agbridge.hostb" not in "\n".join(refresh.calls())


@pytest.mark.parametrize("shape", ["empty", "nested", "second-array"])
def test_what_an_array_token_means_is_the_whole_boundary(refresh, tmp_path,
                                                         shape):
    """Three shapes the `<array>` rules exist for, each with its own direction.

    * `<array/>` is an EMPTY argv, opened and closed at once -- treating it as
      an open leaves the walk running over every key after it.
    * an array nested inside argv is an ELEMENT, and one that is not a string.
      launchd refuses such a job outright, so it starts nothing and holds no map
      -- and it must not be FLATTENED into the strings around it, which is the
      bug this arm baits.
    * a second `ProgramArguments` array starts a fresh argv, so a flag left
      dangling at the end of the first must not eat its first element.

    ⚠️ The nested arm expected `com.agbridge.hostb` while the reader WALKED the
    array itself: that walk stepped over a non-string in argument position and
    read the `--config` after it. `agb bridge` does not -- a nested array is a
    stray positional to `parse_bridge_args`, refused exactly like the `bridge
    --config /a bridge` shape below -- and launchd never loads the job either.
    So it is now "carries no `--config`", with the loud note that says the label
    and the config came from different places. That is a real cost when this is
    the ONLY plist naming the instance (the default job is bounced instead), and
    it is the direction taken deliberately: the alternative made a job that
    starts no bridge an exact, declaring, rank-1 claimant that outranked one that
    does, silently.
    """
    real = _existing_config(refresh, "hostb")
    # ⚠️ The bait is hostb's OWN config, not some `/tmp/decoy` path. A claim on
    # a map this run is not repairing is dropped either way, so both readings
    # end at the default label and the guard passes without holding anything
    # up. Baited with the path being repaired, reading too much bounces hostb
    # and reading correctly does not -- which is a difference the run prints.
    bait = ("  <key>N1</key>\n  <string>--config</string>\n"
            "  <key>N2</key>\n  <string>%s</string>\n" % (real,))
    if shape == "empty":
        body = "  <key>ProgramArguments</key>\n  <array/>\n" + bait
        expect = "com.agbridge"            # claims nothing; not hostb's job
    elif shape == "nested":
        # ⚠️ The bait is INSIDE the nested array, so the two readings differ:
        # flattening it yields `bridge --config <real>` and bounces hostb, while
        # treating it as the one non-string element it is yields "no --config"
        # and the default label. A plist launchd will not load, either way.
        body = ("  <key>ProgramArguments</key>\n  <array>\n"
                "    <string>bridge</string>\n"
                "    <array>\n      <string>--config</string>\n"
                "      <string>%s</string>\n    </array>\n  </array>\n"
                % (real,))
        expect = "com.agbridge"
    else:
        # A flag dangling at the end of the first argv, and the second argv
        # opening with a path that is an ARGUMENT, not its value.
        body = ("  <key>ProgramArguments</key>\n  <array>\n"
                "    <string>bridge</string>\n"
                "    <string>--config</string>\n  </array>\n"
                "  <key>ProgramArguments</key>\n  <array>\n"
                "    <string>%s</string>\n"
                "    <string>bridge</string>\n  </array>\n" % (real,))
        expect = "com.agbridge"
    agents = tmp_path / "Library" / "LaunchAgents"
    (agents / "com.agbridge.hostb.plist").write_text(
        "<plist version=\"1.0\"><dict>\n" + body + "</dict></plist>\n")
    rc, out, _err = refresh.run(["--config", real])
    assert rc == 0, out
    assert "stopped:  %s\n" % (expect,) in out, out


def test_a_minified_plist_reads_like_an_indented_one(refresh, tmp_path):
    """⚠️ A boundary that is too TIGHT is not the safe side here.

    Missing a real `ProgramArguments` demotes that plist to "carries no
    `--config`", which for a NAMED instance means its label is never found and
    the run bounces the DEFAULT job while that instance's bridge is live -- the
    same accident as reading too much, from the other end. A plist minified onto
    one line is the shape most likely to be missed by a reader that thinks in
    lines, and `plutil -convert xml1` writes indentation nobody promised anyway.
    """
    real = _existing_config(refresh, "hostb")
    body = ("<plist version=\"1.0\"><dict>"
            "<key>Label</key><string>com.agbridge.hostb</string>"
            "<key>ProgramArguments</key><array>"
            "<string>bridge</string><string>--config</string>"
            "<string>%s</string>" % (real,)
            + "</array><key>ProcessType</key><string>Background</string>"
              "</dict></plist>\n")
    assert plistlib.loads(body.encode())["ProgramArguments"][-1] == real
    agents = tmp_path / "Library" / "LaunchAgents"
    (agents / "com.agbridge.hostb.plist").write_text(body)
    rc, out, _err = refresh.run(["--config", real])
    assert rc == 0, out
    assert "stopped:  com.agbridge.hostb" in out, out
    assert "--config %s" % (refresh.quoted(real),) in refresh.call("pgrep")


def test_a_file_that_is_not_a_plist_says_nothing_rather_than_something(
        refresh, tmp_path):
    """⚠️ THE DELIBERATE RESOLUTION OF A PARSE ERROR, and it changed with the
    parser.

    The token scan kept the last COMPLETE value it had seen in a truncated file.
    That reads like the generous, too-tight-is-worse choice and was not one: cut
    this plist one element later and the scan answered `--workspace` -- a flag
    name -- as the config, so the banner named it, the liveness pattern was
    built from it and matched no process, and `forget-rows` landed under the live
    bridge. A partial parse of a file nobody can load is not information.

    `plistlib` raises, `plist_arg` exits 2, and `bind_label_to_config` treats
    that exactly as it treats an unreadable plist: the file says NOTHING. It does
    not declare a config and it does not imply the default one either. A file
    that is not a plist is one launchd cannot load, so no bridge was started from
    it -- there is nothing to bounce and nothing to wait for.

    The cost is named rather than hidden: hostb's label is not found, so the run
    falls back to the DEFAULT label and SAYS SO. That is the loud direction.
    """
    real = _existing_config(refresh, "hostb")
    body = ("<plist version=\"1.0\"><dict>"
            "<key>Label</key><string>com.agbridge.hostb</string>"
            "<key>ProgramArguments</key><array>"
            "<string>bridge</string><string>--config</string>"
            "<string>%s</string>"
            "<string>--work" % (real,))       # cut off mid-element
    # Non-vacuity: this really is unloadable, so nothing is running from it.
    with pytest.raises(Exception):
        plistlib.loads(body.encode())
    agents = tmp_path / "Library" / "LaunchAgents"
    (agents / "com.agbridge.hostb.plist").write_text(body)
    rc, out, _err = refresh.run(["--config", real])
    assert rc == 0, out
    # Not hostb -- and not silently: the fallback names itself.
    assert "stopped:  com.agbridge\n" in out, out
    assert "no plist in" in out and "--instance" in out, out
    assert "com.agbridge.hostb" not in "\n".join(refresh.calls())
    # And above all it did not act on the half it managed to read.
    assert "--work" not in refresh.call("pgrep")


# ---------------------------------------------------------------------------
# the three answers `plist_arg` can give, and the one that was being lost
# ---------------------------------------------------------------------------

def test_an_interpreter_that_is_not_python_is_refused_before_anything_moves(
        refresh, tmp_path):
    """⚠️ `--python /bin/false`: an ORDINARY operator mistake, not a hand-edit.

    The reader exits 127/126/1 for a `--python` that names a shell script, a
    missing file or an interpreter with no `plistlib`, and every one of those
    was read as "this plist says nothing". So every plist said nothing, no job
    claimed the config, and the run fell through to the DEFAULT label and the
    CONVENTIONAL config -- `stopped: com.agbridge`, exit 0, in the same words it
    uses when it is right, while the named instance's bridge kept running over
    the map it had just forgotten.
    """
    real = _existing_config(refresh, "hostb")
    refresh.write_plist(label="com.agbridge.hostb", instance="hostb")
    rc, out, err = refresh.run(["--config", real, "--python", "/bin/false"])
    assert rc != 0, out
    assert "not a usable python3" in err, err
    assert "/bin/false" in err, err
    # Nothing was bounced, forgotten or started: it refused before step 1.
    assert refresh.calls() == [], refresh.calls()
    assert "stopped:" not in out and "instance:" not in out, out


def test_an_interpreter_that_exits_zero_without_reading_anything_is_refused(
        refresh, tmp_path):
    """The half a STATUS cannot see, which is why the probe asks a question.

    `--python /bin/echo` is executable, exits 0, and prints its own arguments --
    so every plist would "answer" a config path made of the reader's own source
    code, with no failing status anywhere. That is the unsafe polarity: a path
    that exists nowhere, carried into the banner, the liveness pattern and
    `forget-rows`. `plist_read_ok` cannot catch it; only asking a question with
    a known answer can.
    """
    real = _existing_config(refresh, "hostb")
    rc, out, err = refresh.run(["--config", real, "--python", "/bin/echo"])
    assert rc != 0, out
    assert "not a usable python3" in err, err
    assert refresh.calls() == [], refresh.calls()


def _partial_python(tmp_path, refuse):
    """An interpreter that works, except when `refuse` is among its arguments.

    The probe at the top of the script asks a question no plist read asks, so
    this passes it and then fails a particular READ -- which is the only shape
    that reaches `plist_read_ok` at all, and the reason each of its three call
    sites needs its own case.
    """
    path = tmp_path / "bin" / ("python-refusing-" + refuse.lstrip("-"))
    path.write_text(
        "#!/bin/sh\n"
        "for a in \"$@\"; do\n"
        "    case $a in " + refuse + ") exit 127 ;; esac\n"
        "done\n"
        "exec '" + sys.executable + "' \"$@\"\n")
    os.chmod(str(path), 0o755)
    return str(path)


def test_an_interpreter_that_writes_to_stderr_is_still_usable(refresh,
                                                              tmp_path):
    """The too-tight half of the probe, which is the easy mistake to make.

    Capturing stderr into the answer (`2>&1`) would refuse an interpreter that
    works but says something on the way -- and `--python` is typed by hand, so
    the refusal would land on a working setup. Discarding it (`2>/dev/null`) is
    what the awk this reader replaced did, and it swallowed the traceback that
    explains why. The decision is stdout alone; stderr flows through.
    """
    real = _existing_config(refresh, "hostb")
    refresh.write_plist(label="com.agbridge.hostb", instance="hostb")
    noisy = tmp_path / "bin" / "noisy-python"
    noisy.write_text("#!/bin/sh\n"
                     "echo 'note: a deprecation, or a shim, or a wrapper' >&2\n"
                     "exec '" + sys.executable + "' \"$@\"\n")
    os.chmod(str(noisy), 0o755)
    rc, out, err = refresh.run(["--config", real, "--python", str(noisy)])
    assert rc == 0, err
    assert "not a usable python3" not in err, err
    assert "stopped:  com.agbridge.hostb" in out, out
    # Non-vacuity: it really did chatter, and the chatter really did reach us.
    assert "a deprecation" in err, err


def test_a_reader_that_breaks_after_the_probe_is_fatal_not_no_config(
        refresh, tmp_path):
    """Non-vacuity for `plist_read_ok`, which the probe alone would hide.

    The probe runs once, at the top; a reader can still fail on a particular
    file afterwards (a traceback in the program, an interpreter that goes away
    mid-run). That status is still not an answer ABOUT THE PLIST, and folding it
    into "this file says nothing" is the same wrong-job bounce reached later.

    ⚠️ Asserted on an EMPTY stdout, not just on the exit status. The read that
    fails here is the one inside `bind_label_to_config`, and dropping the guard
    there is invisible to a status assertion: the run carries on, prints "no
    plist in <dir> names this config, so the label below is the default one" --
    a sentence about which job claims the map, from a run that has not read a
    single plist -- and only dies at the LAST read. The advisory is the damage.
    """
    real = _existing_config(refresh, "hostb")
    refresh.write_plist(label="com.agbridge.hostb", instance="hostb")
    rc, out, err = refresh.run(["--config", real,
                                "--python", _partial_python(tmp_path,
                                                            "--config")])
    assert rc != 0, out
    assert "cannot read a plist" in err, err
    assert "exited 127" in err, err
    assert out == "", out
    # Still before step 1: the label is bound from the plists, and that is
    # where the first read happens.
    assert refresh.calls() == [], refresh.calls()


def test_a_reader_that_breaks_on_the_rows_read_is_fatal_too(refresh, tmp_path,
                                                            mac):
    """The third call site, which the other two cannot reach.

    `bind_label_to_config` asks a second question of a plist whose `--config`
    names another map: does its `--rows` name THIS one (`agb bridge --rows`
    overrides the rows half of a map, so such a job is writing the file this run
    rewrites). Reading a reader failure there as "no `--rows`" drops the only
    job that holds the map and boots out the default one instead.

    ⚠️ The STATUS is asserted, not just the refusal, and that is what pins the
    spelling of the guard: `if ! cmd; then` sets `$?` to the NEGATION of the
    command's status, so `plist_read_ok` would be handed 0 for every failure --
    still fatal, by accident, and saying "exited 0" about an interpreter that
    exited 127. The site above uses `if cmd; then :; else` for the same reason.
    """
    (tmp_path / ".config" / "agbridge" / "hostb").mkdir(parents=True)
    (tmp_path / "Library" / "LaunchAgents" / "com.agbridge.plist").unlink()
    _rows_plist(refresh, tmp_path, mac, "com.agbridge.hostb", "hostb")
    rc, out, err = refresh.run(["--config", refresh.config(),
                                "--python", _partial_python(tmp_path,
                                                            "--rows")])
    assert rc != 0, out
    assert "cannot read a plist" in err, err
    assert "exited 127" in err, err
    assert refresh.calls() == [], refresh.calls()


def test_a_reader_that_breaks_on_the_selected_plist_is_fatal_too(refresh,
                                                                 tmp_path):
    """The second call site, reached when the first one never runs.

    `bind_label_to_config` is skipped whenever the label is already known --
    `--instance hostb`, or a bare `--label` -- so on the commonest instance
    invocation there is exactly ONE plist read, and it is this one. Its answer
    decides which map is repaired AND how the liveness pattern is narrowed, so
    a reader failure read as "no `--config`" repairs the conventional path and
    waits for a bridge that is running on something else.
    """
    refresh.write_plist(label="com.agbridge.hostb", instance="hostb")
    rc, out, err = refresh.run(["--instance", "hostb",
                                "--python", _partial_python(tmp_path,
                                                            "--config")])
    assert rc != 0, out
    assert "cannot read a plist" in err, err
    assert refresh.calls() == [], refresh.calls()
    assert out == "", out


def test_an_unreadable_plist_is_not_a_guess_about_which_map_to_repair(
        refresh, tmp_path):
    """⚠️ `--instance hostb` + a corrupt `com.agbridge.hostb.plist`.

    The plist is the ONLY record of where `install.sh mac --instance hostb
    --config <elsewhere>` put the config, and "there is no plist" and "the plist
    is there and unreadable" answered the same exit 2. The convention is the
    right fall-back for the first and a guess for the second: this run repaired
    `~/.config/agbridge/hostb/config`, a map that never existed, so `forget-rows`
    reported "already empty" and exited 0 -- while the liveness pattern, built
    from the same empty answer, waited for a bridge whose command line names the
    real config. Success, twice, on the wrong instance.
    """
    elsewhere = str(tmp_path / "elsewhere" / "config")
    os.makedirs(os.path.dirname(elsewhere))
    with open(elsewhere, "w") as handle:
        handle.write("{}\n")
    body = ("<plist version=\"1.0\"><dict><key>ProgramArguments</key><array>"
            "<string>bridge</string><string>--config</string>"
            "<string>" + elsewhere + "</string><string>--work")
    with pytest.raises(Exception):                     # really is unloadable
        plistlib.loads(body.encode())
    agents = tmp_path / "Library" / "LaunchAgents"
    (agents / "com.agbridge.hostb.plist").write_text(body)
    rc, out, err = refresh.run(["--instance", "hostb"])
    assert rc != 0, out
    assert "could not be read as a plist" in err, err
    assert "--config" in err, err
    # It did not guess, and it did not bounce anything while guessing.
    assert refresh.config("hostb") not in out, out
    assert refresh.calls() == [], refresh.calls()


def test_a_plist_that_is_not_there_at_all_still_falls_back_to_the_convention(
        refresh, tmp_path):
    """The not-too-tight half, and the reason `[ -e ]` is the weaker test.

    A Mac whose plist was never rendered -- or was hand-deleted -- can still be
    refreshed by name, which is the documented fall-back and the case exit 2 was
    conflating with the one above. Nothing exists to contradict the convention
    here, so the convention stands.

    ⚠️ The `rc == 0` is load-bearing twice over now: this run also ends with no
    bridge started at all, and it must stay a warning. "An instance left without
    a running bridge is an error" is a rule about the SWEEP; see
    `test_an_instance_left_without_a_bridge_fails_the_sweep`.
    """
    rc, out, _err = refresh.run(["--instance", "hostb"])
    assert rc == 0, out
    assert refresh.config("hostb") in out, out
    assert "stopped:  com.agbridge.hostb" in out, out


def test_an_unreadable_plist_with_a_config_given_says_so_and_waits_broadly(
        refresh, tmp_path):
    """With `--config` there is nothing to guess, so it runs -- and says WHY.

    The liveness note used to read "no --config in <plist>", with the advice
    that goes with a plist predating the flag, for a file that had not been read
    at all. The wait is broad either way (the safe direction); the sentence
    somebody acts on was the wrong one.
    """
    real = _existing_config(refresh, "hostb")
    agents = tmp_path / "Library" / "LaunchAgents"
    (agents / "com.agbridge.plist").write_text("<plist version=\"1.0\"><di")
    rc, out, _err = refresh.run(["--config", real])
    assert rc == 0, out
    assert "could not be read, so the wait below matches ANY" in out, out
    assert "no --config in" not in out, out
    assert "stopped:  com.agbridge" in out, out


def _raw_plist(tmp_path, label, elements):
    """A plist with a `ProgramArguments` spelled element by element.

    `refresh.write_plist` renders the shape `install.sh` renders; this one
    renders the shapes it does not, which is what the reader exists for.
    """
    body = "".join("    <string>%s</string>\n" % (e,) for e in elements)
    (tmp_path / "Library" / "LaunchAgents" / (label + ".plist")).write_text(
        "<plist version=\"1.0\"><dict>\n"
        "  <key>ProgramArguments</key>\n  <array>\n"
        + body + "  </array>\n</dict></plist>\n")


def test_the_fixture_renders_the_argv_shape_the_installer_does(refresh):
    """⚠️ THE HARNESS ITSELF, pinned against `dist/com.agbridge.plist`.

    `write_plist` is what almost every plist test in this file goes through,
    and it rendered `["bridge", "--config", <path>]` -- an array four elements
    shorter than any launchd runs. Nothing FAILS when a harness models its
    input as simpler than reality; the tests all pass, on a shape the property
    is not about, which is exactly how a reader that walked the whole array
    survived a review round built to catch it.

    Nothing downstream depends on the prefix (the reader answers both shapes
    alike, deliberately), so only a guard like this one keeps the harness
    honest. Compared against the SHIPPED template, not against a constant here.
    """
    refresh.write_plist()
    argv = plistlib.loads(refresh.plist_text().encode())["ProgramArguments"]
    with open(os.path.join(conftest.REPO_ROOT, "dist",
                           "com.agbridge.plist"), "rb") as handle:
        shipped = plistlib.loads(handle.read())["ProgramArguments"]
    assert argv.index("bridge") == shipped.index("bridge"), argv
    assert argv[argv.index("bridge") + 1:] == ["--config", refresh.config()]
    # ...and the flagless shape is the same array minus the pair, not a
    # different array: it is what a Mac installed before instances still has.
    refresh.write_plist(with_config=False)
    bare = plistlib.loads(refresh.plist_text().encode())["ProgramArguments"]
    assert bare == argv[:argv.index("bridge") + 1], bare


def test_a_flag_before_the_command_word_is_not_the_bridges_config(refresh,
                                                                  tmp_path):
    """⚠️ `<agb> --config X bridge` STARTS NO BRIDGE, so it holds no map.

    `agb` dispatches on the argument after the script path, so a flag in front
    of the command name IS the command name -- refused as unknown, and under
    `KeepAlive` restarted once every `ThrottleInterval` for ever. (Run, not
    assumed: `test_install_pkg.py::
    test_the_templates_config_flag_comes_after_the_command_name`.)

    Reading `/real/config` off that array made the dead job an EXACT, DECLARING
    claimant -- rank 1, the top of the table -- so with `com.agbridge.aaa`
    sorting before `com.agbridge.hostb` the run bounced the job that was not
    running, never waited for the one that was, and forgot the map underneath a
    live bridge. Both jobs are still reported as claimants; which one is *used*
    is the assertion.
    """
    real = _existing_config(refresh, "hostb")
    _raw_plist(tmp_path, "com.agbridge.aaa",
               ["/usr/bin/python3", "-S", "-E", "/Users/me/agb",
                "--config", real, "bridge"])
    _raw_plist(tmp_path, "com.agbridge.hostb",
               ["/usr/bin/python3", "-S", "-E", "/Users/me/agb",
                "bridge", "--config", real])
    rc, out, _err = refresh.run(["--config", real])
    assert rc == 0, out
    assert "stopped:  com.agbridge.hostb" in out, out
    # ...and the broken job is not even reported as a rival claimant, because
    # it claims nothing: its argv carries no bridge flags at all.
    assert "more than one launchd job" not in out, out


def test_an_argv_with_no_command_word_still_implies_the_default_config(refresh,
                                                                      tmp_path):
    """The fail direction for "no `bridge` in the array", chosen and pinned.

    It is "this argv carries no `--config`", NOT "this file says nothing" --
    the same answer a plist predating the flag gives. Exit 2 would make such a
    job invisible, and the shape that would swallow is `<plist/>`: the state
    every Mac installed before instances existed is still in.

    Asserted where the difference shows: a default-map run must still find the
    default job and bounce it.
    """
    _raw_plist(tmp_path, "com.agbridge",
               ["/usr/bin/python3", "-S", "-E", "/Users/me/agb", "close-done"])
    default = refresh.config()
    os.makedirs(os.path.dirname(default))
    open(default, "w").write("")
    rc, out, _err = refresh.run(["--config", default])
    assert rc == 0, out
    assert "stopped:  com.agbridge" in out, out
    assert "no plist in" not in out, out


def test_the_plist_the_installer_actually_renders_is_read(refresh, tmp_path):
    """The not-too-tight guard, on the only plist shape that certainly exists.

    `install.sh` renders `dist/com.agbridge.plist` verbatim apart from six
    placeholders -- XML comment and all -- so that file, rendered, is what the
    reader has to answer for. A boundary drawn to a schema rather than to what
    is on disk fails here and nowhere else in this file.
    """
    real = _existing_config(refresh, "hostb")
    with open(os.path.join(conftest.REPO_ROOT, "dist",
                           "com.agbridge.plist")) as handle:
        template = handle.read()
    rendered = template
    for holder, value in (("@LABEL@", "com.agbridge.hostb"),
                          ("@PYTHON@", "/usr/bin/python3"),
                          ("@AGB@", "/Users/me/.local/bin/agb"),
                          ("@CONFIG@", real),
                          ("@PATH@", "/usr/bin:/bin"),
                          ("@LOGDIR@", "/Users/me/Library/Logs/agbridge")):
        rendered = rendered.replace(holder, value)
    # Non-vacuity: it really is the shipped template, comment and all, and no
    # placeholder survived to make the reader answer about something simpler.
    assert "<!--" in rendered and "<key>ProgramArguments</key>" in rendered
    assert "@" not in rendered.split("<plist")[1]
    agents = tmp_path / "Library" / "LaunchAgents"
    (agents / "com.agbridge.hostb.plist").write_text(rendered)
    rc, out, _err = refresh.run(["--config", real])
    assert rc == 0, out
    assert "stopped:  com.agbridge.hostb" in out, out
    assert "--config %s" % (refresh.quoted(real),) in refresh.call("pgrep")
    # ...and none of the OTHER keys' strings reached the walk: the template
    # carries a `ProcessType` of `Background` and a `WorkingDirectory` of
    # `/tmp` after the array, and the map repaired is the one argv names.
    assert "Background" not in refresh.call("pgrep")
    assert refresh.call("agb forget-rows").endswith("--config " + real)


@pytest.mark.parametrize("where", ["between", "spanning", "same-line",
                                   "inside-argv", "inside-a-value",
                                   "spanning-a-value",
                                   "pi-between", "pi-after-a-comment",
                                   "cdata-spanning"])
def test_a_comment_is_not_argv_however_much_it_reads_like_it(refresh, tmp_path,
                                                             where):
    """⚠️ THREE XML REGIONS CAN CARRY MARKUP launchd does not run any of.

    `<!-- old argv shape: <array><string>bridge</string></array> -->` between
    `<key>ProgramArguments</key>` and the real `<array>` is a VALID plist that
    launchd starts normally -- and tokenized, its `<array>` OPENED argv and its
    `</array>` CLOSED it, so the real array arrived disarmed and the whole file
    read as carrying no `--config`. That is the too-tight failure this reader is
    most exposed to: hostb's label is never found and the run bounces the
    DEFAULT job while hostb's bridge is live.

    A processing instruction (`<?...?>`) and a CDATA section can hold the same
    text. The nine shapes are the places one can sit and change the answer: the
    six for a comment, a PI in the position the finding names, a PI *inside* a
    comment, and a CDATA section spanning lines.

    ⚠️ `spanning-a-value` is the one that outlived the token scan, and it is
    the reason the reader is now `plistlib` rather than a fifth tokenizer rule.
    A comment may split character data across LINES:
    `<string>/tmp/a<!--` newline `-->b/config</string>` is the string
    `/tmp/ab/config`. The scan carried its "inside a comment" state from record
    to record but not the text BEFORE the opener, so the two halves were never
    joined, the element vanished, and the dangling `--config` was spent on the
    next one -- `--workspace`, a path no bridge is running on.

    ⚠️ A COMMENT still cannot MANUFACTURE a config, because XML forbids `--`
    inside one and every flag name contains one: `<!-- <string>--config</string>
    -->` is not a well-formed plist at all (`plistlib` refuses it; so does
    `plutil`). But the older comment here drew the wrong conclusion from that --
    "a comment could only ever HIDE argv" is true only of a comment sitting
    BETWEEN elements, and `spanning-a-value` is the counterexample: it changes a
    value without spelling a flag. A PI and a CDATA section have no such rule at
    all -- either can spell a flag, and the CDATA one is covered separately.
    """
    real = _existing_config(refresh, "hostb")
    argv = ("    <string>bridge</string>\n"
            "    <string>--config</string>\n"
            "    <string>%s</string>\n" % (real,))
    if where == "between":
        body = ("  <key>ProgramArguments</key>\n"
                "  <!-- old argv shape: <array><string>bridge</string></array>"
                " -->\n  <array>\n" + argv + "  </array>\n")
    elif where == "spanning":
        body = ("  <key>ProgramArguments</key>\n  <!--\n    <array>\n"
                "      <string>bridge</string>\n    </array>\n  -->\n"
                "  <array>\n" + argv + "  </array>\n")
    elif where == "same-line":
        body = ("  <key>ProgramArguments</key>"
                "<!-- <array><string>x</string></array> --><array>"
                "<string>bridge</string><string>--config</string>"
                "<string>%s</string></array>\n" % (real,))
    elif where == "inside-argv":
        body = ("  <key>ProgramArguments</key>\n  <array>\n"
                "    <string>bridge</string>\n"
                "    <!-- was: </array><key>Other</key> -->\n"
                "    <string>--config</string>\n"
                "    <string>%s</string>\n  </array>\n" % (real,))
    elif where == "pi-between":
        body = ("  <key>ProgramArguments</key>\n"
                "  <?note <array><string>x</string></array> ?>\n"
                "  <array>\n" + argv + "  </array>\n")
    elif where == "pi-after-a-comment":
        # ⚠️ The `<?` is INSIDE the comment. Looking for it first would end the
        # region at that PI's `?>` -- which is not there -- and swallow the
        # rest of the file, so the openers are tried in position order.
        body = ("  <key>ProgramArguments</key>\n"
                "  <!-- a <? that is inside a comment --><array>\n"
                + argv + "  </array>\n")
    elif where == "spanning-a-value":
        # ⚠️ The comment splits the value across RECORDS, and a `--workspace`
        # follows so the old failure is visible rather than merely absent: the
        # scan lost the element and spent the dangling `--config` on the next
        # one, answering `--workspace` as the config path.
        head, tail = real[:4], real[4:]
        body = ("  <key>ProgramArguments</key>\n  <array>\n"
                "    <string>bridge</string>\n"
                "    <string>--config</string>\n"
                "    <string>%s<!--\n      why this path\n    -->%s</string>\n"
                "    <string>--workspace</string>\n"
                "    <string>farm</string>\n  </array>\n" % (head, tail))
    elif where == "cdata-spanning":
        # ⚠️ AFTER the real argv, and multi-line. In front of it the reader
        # answers correctly for the wrong reason -- last-wins overwrites the
        # decoy with the real value -- and a walk with no state across records
        # passes with nothing holding it up.
        body = ("  <key>ProgramArguments</key>\n  <array>\n"
                + argv + "  </array>\n"
                "  <key>Note</key>\n  <string><![CDATA[\n"
                "<key>ProgramArguments</key>\n<array>\n"
                "<string>--config</string>\n<string>/tmp/decoy/config</string>\n"
                "</array>\n]]></string>\n")
    else:
        # A comment INSIDE character data. XML says the value is the text
        # either side joined, and that is what the bridge is started on.
        head, tail = real[:4], real[4:]
        body = ("  <key>ProgramArguments</key>\n  <array>\n"
                "    <string>bridge</string>\n"
                "    <string>--config</string>\n"
                "    <string>%s<!-- why -->%s</string>\n  </array>\n"
                % (head, tail))
    text = "<plist version=\"1.0\"><dict>\n" + body + "</dict></plist>\n"
    # Non-vacuity, and the whole premise: this really is a plist launchd runs,
    # and the argv it runs really is the one asserted below.
    argv_read = plistlib.loads(text.encode())["ProgramArguments"]
    assert argv_read.count("--config") == 1, argv_read
    assert argv_read[argv_read.index("--config") + 1] == real, argv_read
    agents = tmp_path / "Library" / "LaunchAgents"
    (agents / "com.agbridge.hostb.plist").write_text(text)
    rc, out, _err = refresh.run(["--config", real])
    assert rc == 0, out
    assert "stopped:  com.agbridge.hostb" in out, out
    assert "--config %s" % (refresh.quoted(real),) in refresh.call("pgrep")
    assert refresh.call("agb forget-rows").endswith("--config " + real)
    assert "/tmp/decoy/config" not in "\n".join(refresh.calls()) + out
    assert "--workspace" not in refresh.call("pgrep")


@pytest.mark.parametrize("shape", ["manufacturing", "carrying-the-value"])
def test_a_cdata_section_is_character_data_and_is_read_as_such(refresh,
                                                               tmp_path,
                                                               shape):
    """CDATA is the region whose contents are DATA, and both directions matter.

    * `manufacturing` -- a `<![CDATA[<key>ProgramArguments</key><array>…]]>`
      under some OTHER key must NOT hand a config to a plist whose argv has
      none. That is the unsafe polarity: the liveness pattern would be narrowed
      onto a path no process carries, so the poll matches nothing, the run waits
      zero times and `forget-rows` lands under the live bridge.
    * `carrying-the-value` -- a config delivered *as* CDATA IS the config.
      `<string><![CDATA[/x]]></string>` is the string `/x`, which is what
      launchd hands the bridge.

    Both used to be wrong, in opposite ways and for the same reason: the token
    scan dropped CDATA sections whole, so the first was right by accident and
    the second lost its value and then spent the dangling `--config` on the NEXT
    element -- `--workspace`, a manufactured path. `plistlib` does neither: a
    section under another key is under another key, and one inside a `<string>`
    is that string's text.
    """
    real = _existing_config(refresh, "hostb")
    if shape == "manufacturing":
        text = ("<plist version=\"1.0\"><dict>\n"
                "  <key>Note</key>\n  <string><![CDATA[<key>ProgramArguments"
                "</key><array><string>--config</string><string>%s</string>"
                "</array>]]></string>\n"
                "  <key>ProgramArguments</key>\n  <array>\n"
                "    <string>bridge</string>\n  </array>\n"
                "</dict></plist>\n" % (real,))
        # Non-vacuity: launchd starts this job with no `--config` at all, and
        # the bait is the map this run repairs, so reading the CDATA bounces
        # hostb and reading it correctly does not.
        assert plistlib.loads(text.encode())["ProgramArguments"] == ["bridge"]
    else:
        text = ("<plist version=\"1.0\"><dict>\n"
                "  <key>ProgramArguments</key>\n  <array>\n"
                "    <string>bridge</string>\n    <string>--config</string>\n"
                "    <string><![CDATA[%s]]></string>\n"
                "    <string>--workspace</string>\n"
                "    <string>farm</string>\n  </array>\n"
                "</dict></plist>\n" % (real,))
        argv = plistlib.loads(text.encode())["ProgramArguments"]
        assert argv[2] == real and argv[3] == "--workspace", argv
    agents = tmp_path / "Library" / "LaunchAgents"
    (agents / "com.agbridge.hostb.plist").write_text(text)
    rc, out, _err = refresh.run(["--config", real])
    assert rc == 0, out
    if shape == "manufacturing":
        # hostb's plist claims nothing, so the run says so rather than acting
        # on a value it read out of another key's character data.
        assert "stopped:  com.agbridge\n" in out, out
        assert "com.agbridge.hostb" not in "\n".join(refresh.calls())
    else:
        assert "stopped:  com.agbridge.hostb" in out, out
        assert "--config %s" % (refresh.quoted(real),) in refresh.call("pgrep")
    # ...and above all neither narrowed the poll onto a manufactured path.
    assert "--workspace" not in refresh.call("pgrep")


@pytest.mark.parametrize("tag", ["start", "end"])
def test_whitespace_inside_a_tag_is_valid_xml_and_is_read(refresh, tmp_path,
                                                          tag):
    """⚠️ XML allows a blank before the `>` of either kind of tag.

    A start tag is `<` Name (S Attribute)* S? `>` and an end tag is `</` Name S?
    `>`, so `<string >` and `</array >` are both well-formed and `plistlib`
    reads them without comment. The token scan matched neither literal, and both
    failed towards a path no bridge is running on -- the unsafe direction, where
    the liveness poll matches nothing and `forget-rows` lands under a live
    bridge:

    * `start` -- `<string >/real/config</string>` matched no token at all, so
      the element VANISHED and the dangling `--config` was spent on the next
      one, answering `--workspace`.
    * `end` -- `</array >` never closed argv, so the walk stayed inside it and
      the `WatchPaths` array that follows OVERWROTE the real config with its
      own strings (last-wins), answering `/tmp/decoy/config`.

    A hand-edited plist is the whole reason this reader exists, and neither
    spelling is exotic: several XML editors emit them.
    """
    real = _existing_config(refresh, "hostb")
    if tag == "start":
        text = ("<plist version=\"1.0\"><dict>\n"
                "  <key>ProgramArguments</key>\n  <array>\n"
                "    <string>bridge</string>\n    <string>--config</string>\n"
                "    <string >%s</string>\n"
                "    <string>--workspace</string>\n"
                "    <string>farm</string>\n  </array>\n"
                "</dict></plist>\n" % (real,))
    else:
        text = ("<plist version=\"1.0\"><dict>\n"
                "  <key>ProgramArguments</key>\n  <array>\n"
                "    <string>bridge</string>\n    <string>--config</string>\n"
                "    <string>%s</string>\n  </array >\n"
                "  <key>WatchPaths</key>\n  <array>\n"
                "    <string>--config</string>\n"
                "    <string>/tmp/decoy/config</string>\n  </array>\n"
                "</dict></plist>\n" % (real,))
    # Non-vacuity: launchd runs this, and the argv it runs is the real one.
    doc = plistlib.loads(text.encode())
    assert doc["ProgramArguments"][-2:] != ["--config", "/tmp/decoy/config"]
    assert doc["ProgramArguments"][2] == real, doc
    agents = tmp_path / "Library" / "LaunchAgents"
    (agents / "com.agbridge.hostb.plist").write_text(text)
    rc, out, _err = refresh.run(["--config", real])
    assert rc == 0, out
    assert "stopped:  com.agbridge.hostb" in out, out
    assert "--config %s" % (refresh.quoted(real),) in refresh.call("pgrep")
    joined = "\n".join(refresh.calls()) + out
    assert "/tmp/decoy/config" not in joined
    assert "--workspace" not in refresh.call("pgrep")


def test_a_binary_plist_is_read_like_an_xml_one(refresh, tmp_path):
    """`plutil -convert binary1` produces one, and launchd runs it.

    The token scan could not read a byte of it -- there is no `<string>` in a
    binary plist -- so such a job answered "carries no --config", which for a
    named instance is the quiet wrong-job bounce. It is not a hypothetical
    format: it is what Xcode, `PlistBuddy` and `defaults write` all leave
    behind, and `plistlib` sniffs it with no flag.
    """
    real = _existing_config(refresh, "hostb")
    raw = plistlib.dumps({"Label": "com.agbridge.hostb",
                          "ProgramArguments": ["bridge", "--config", real]},
                         fmt=plistlib.FMT_BINARY)
    assert raw.startswith(b"bplist"), raw[:16]     # non-vacuity: really binary
    agents = tmp_path / "Library" / "LaunchAgents"
    (agents / "com.agbridge.hostb.plist").write_bytes(raw)
    rc, out, _err = refresh.run(["--config", real])
    assert rc == 0, out
    assert "stopped:  com.agbridge.hostb" in out, out
    assert "--config %s" % (refresh.quoted(real),) in refresh.call("pgrep")


def test_a_flag_name_written_as_a_character_reference_is_still_that_flag(
        refresh, tmp_path):
    """`&#45;-config` is `--config`, and launchd hands the bridge a `--config`.

    The token scan decoded three named entities, in the VALUE only, and compared
    flag names raw -- so it answered "no config" here and the old comment listed
    that as a permanent limitation with a reason ("the decoder that would have
    to run before the comparison is a second, differently-shaped entity table").
    A parser has that table already; nothing about it is this script's problem.
    """
    real = _existing_config(refresh, "hostb")
    text = ("<plist version=\"1.0\"><dict>\n"
            "  <key>ProgramArguments</key>\n  <array>\n"
            "    <string>bridge</string>\n"
            "    <string>&#45;&#45;config</string>\n"
            "    <string>%s</string>\n  </array>\n"
            "</dict></plist>\n" % (real,))
    # Non-vacuity: the reference really is in the file, and the argv launchd
    # runs really does carry a decoded `--config`.
    assert "&#45;" in text
    assert plistlib.loads(text.encode())["ProgramArguments"][1] == "--config"
    agents = tmp_path / "Library" / "LaunchAgents"
    (agents / "com.agbridge.hostb.plist").write_text(text)
    rc, out, _err = refresh.run(["--config", real])
    assert rc == 0, out
    assert "stopped:  com.agbridge.hostb" in out, out
    assert "--config %s" % (refresh.quoted(real),) in refresh.call("pgrep")


# ---------------------------------------------------------------------------
# the differential corpus: `plist_arg` against real authorities
# ---------------------------------------------------------------------------

def _extract_sh(name, script=SCRIPT):
    """One shell function, taken out of `agb-refresh` itself.

    Extracted rather than copied so the corpus below runs the SHIPPED reader.
    A restatement here would be a test of the test.
    """
    with open(script) as handle:
        lines = handle.read().splitlines()
    start = lines.index(name + "() {")
    end = start
    while lines[end] != "}":
        end += 1
    return "\n".join(lines[start:end + 1])


@pytest.fixture
def plist_arg(tmp_path):
    """`agb-refresh`'s `plist_arg`, on one file, without forking the script.

    ⚠️ Driven this way ONLY here. Every other plist test in this file goes
    through the whole script, because what a value does downstream -- which job
    is bounced, what the liveness pattern narrows onto -- is the thing that
    matters. This fixture exists so the corpus can be forty cases instead of
    forty end-to-end runs, and it takes both the function and the flag list out
    of the file so neither can drift from what ships.

    Returns `(status, value)`: status 2 is "this file says nothing at all",
    which is a different answer from status 0 with an empty value, and status 3
    is "the parser could not be loaded from beside `$agb`".

    ⚠️ `agb=` is not decoration, and it does more than it used to. `plist_arg`
    is now two lines -- it RUNS `"$python" -S -E "$agb" instances --plist ...`
    -- so this path selects the whole reader, not just the parser beside it.
    The corpus below therefore runs the shipped `agb_mac.run_instances` end to
    end through the shipped shell function, which is the point of driving it
    here rather than calling `run_instances` in-process.

    ⚠️ `agentsdir=` exists because `plist_arg` forwards `--launch-agents`, and
    an EMPTY value is a missing-value error rather than a default -- so a
    harness that omitted it would fail every case with exit 1 and look like a
    reader bug. It is inert under `--plist`; see the comment at the call site.
    """
    harness = tmp_path / "plist_arg.sh"
    harness.write_text("python=%s\nagb=%s\nagentsdir=%s\n"
                       % (sys.executable, conftest.AGB_PATH, tmp_path)
                       + _extract_sh("plist_arg") + "\n"
                       + "plist_arg \"$1\" \"$2\"\n")
    counter = [0]

    def call(raw, flag="--config", locale=None):
        counter[0] += 1
        target = tmp_path / ("case%d.plist" % (counter[0],))
        target.write_bytes(raw if isinstance(raw, bytes) else raw.encode())
        env = dict(os.environ)
        if locale is not None:
            # ⚠️ `-E` ignores `PYTHON*` but NOT `LC_ALL`, so the locale still
            # picks Python's stdout encoding. See the byte-writing test below.
            env["LC_ALL"] = env["LANG"] = locale
        proc = subprocess.Popen(["sh", str(harness), str(target), flag],
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, env=env)
        out, err = conftest.communicate(proc)
        # ⚠️ The reader carries no `2>/dev/null` -- the awk it replaced did, and
        # that swallowed the interpreter's own diagnostics, so a broken program
        # read as "this plist names no config" at every call site. Anything on
        # stderr here is a bug in the reader, not an answer.
        #
        # ⚠️ AND IT IS A MUCH STRONGER CLAIM THAN IT WAS, deliberately kept.
        # `agb` writes `USAGE` and every `AgbError` to stderr, so this now says
        # that `run_instances` reaches NONE of those paths for any shape in the
        # corpus -- not a bad flag, not a bad combination, not a traceback --
        # for a plist a human could leave behind. The four-status contract is
        # the whole answer; anything printed alongside it is a second answer
        # nobody reads.
        assert err == b"", err
        return proc.returncode, out.decode("utf-8").rstrip("\n")
    return call


_UNDECIDED = object()


def _bridge_would_resolve(mac, raw, flag="--config"):
    """What `agb bridge` resolves for `flag`, from authorities that are not ours.

    `plistlib` says what the argv IS; `agb_mac.parse_bridge_args` says what
    `agb bridge` DOES with it. Neither was `agb-refresh`'s code, which is what
    made the corpus below differential rather than a restatement of the reader.

    ⚠️ HALF OF THAT IS NO LONGER TRUE, AND SAYING SO IS THE POINT. The reader
    moved into `agb_mac.run_instances`, which CALLS `parse_bridge_args` -- so
    for the argv half this oracle now approaches `f(x) == f(x)` and proves
    nothing on its own. What survives is worth keeping and is not that half:

      * `plistlib` is still a genuine external authority for "what is this
        argv", and it is the half four review rounds of hand-rolled tokenizer
        got wrong. `run_instances` uses it, but this compares against it
        INDEPENDENTLY, including the sniff-then-`FMT_XML` retry.
      * the PLUMBING is not shared with the reader at all: where the argv
        starts (everything up to and including `bridge` is the command line,
        not the bridge's argv), which key the flag lands under
        (`BRIDGE_VALUE_ARGS`, not a name derived here), the four-status
        mapping, and the byte encoding of the answer.
      * `dist/com.agbridge.plist` is a third authority, unchanged, and it is
        what the `as-installed` axis is read out of.

    So the corpus stays here, driven through the SHIPPED `plist_arg` rather
    than calling `run_instances` in-process: the plumbing above is exactly the
    part an in-process call would skip, and it is the part that changed.

    `_UNDECIDED` where those authorities have no opinion: a file `plistlib`
    refuses, or a plist with no argv at all. Those cases still assert their
    declared expectation -- they simply cannot be cross-checked, and saying so is
    the point of the sentinel.

    ⚠️ AN ARGV THE PARSER REJECTS IS NOT ONE OF THEM, and treating it as one is
    how this harness came to CODIFY a bug rather than merely miss it. `bridge
    --config=/real/config --config=` is a missing-value error, so `agb bridge`
    exits and launchd restarts it for ever under `KeepAlive`: that job runs no
    bridge and holds no map, which makes "carries no `--config`" the one right
    answer and `parse_bridge_args` raising the authority that says so. Marking it
    `_UNDECIDED` let the declared expectation `/real/config` stand unchallenged --
    a dead job as an exact, declaring, rank-1 claimant, outranking the live one.
    """
    data = raw if isinstance(raw, bytes) else raw.encode()
    try:
        doc = plistlib.loads(data)
    except Exception:
        # ⚠️ The same two-step the reader does, and for the same reason: the
        # authority here is `plistlib`'s PARSER, not its format SNIFFER. The
        # sniffer looks at the first 32 bytes and knows only `<?xml`, `<plist`
        # and the binary magic, so a plist opening with its DOCTYPE -- valid
        # XML, and launchd loads it -- is refused before parsing. Naming the
        # format skips the sniff; a file that is not XML still fails below.
        try:
            doc = plistlib.loads(data, fmt=plistlib.FMT_XML)
        except Exception:
            return _UNDECIDED
    argv = doc.get("ProgramArguments") if isinstance(doc, dict) else None
    if not isinstance(argv, list) or not argv:
        return _UNDECIDED
    if not all(isinstance(el, str) for el in argv):
        # launchd refuses a job whose argv is not all strings, so it never
        # reaches `parse_bridge_args` at all -- which would happily hand back
        # the integer as a config path. No authority, and no bridge either.
        return _UNDECIDED
    # ⚠️ WHERE THE BRIDGE'S ARGV STARTS, and this used to be a hard-coded
    # `argv[1:]` -- the shortcut that made the whole corpus a test of a shape
    # no plist has. `ProgramArguments` is the whole command line, and `agb`
    # dispatches on the argument after the script path (`agb.main`, and the
    # wrong order is RUN in `test_install_pkg.py::
    # test_the_templates_config_flag_comes_after_the_command_name`), so
    # everything up to and including `bridge` is the interpreter, its options
    # and the `agb` path.
    if "bridge" not in argv:
        # No bridge is started, so `parse_bridge_args` is not the authority for
        # this argv -- `agb.main` is, and it refuses. Nothing is running from
        # it, and nothing carries a config: the same answer as "no such flag".
        return ""
    try:
        opts = mac.parse_bridge_args(argv[argv.index("bridge") + 1:])
    except Exception:
        # DECIDED, and this is the case the sentinel used to swallow. The parser
        # refusing means `agb bridge` exits: no bridge, no map, no claim. See the
        # docstring.
        return ""
    return opts[flag.lstrip("-").replace("-", "_")] or ""


def _wrap(body, head="<plist version=\"1.0\">"):
    return head + "<dict>\n" + body + "</dict></plist>\n"


def _argv(*elements):
    return ("  <key>ProgramArguments</key>\n  <array>\n"
            + "".join("    <string>%s</string>\n" % (e,) for e in elements)
            + "  </array>\n")


# The four elements the shipped template puts in front of `bridge`.
_PREFIX_ELEMENTS = ["/usr/bin/python3", "-S", "-E",
                    "/Users/me/.local/lib/agbridge/agb"]


def _as_installed(raw):
    """The same plist with the interpreter prefix the real template carries.

    ⚠️ THE CORPUS BELOW MODELLED `ProgramArguments` AS `["bridge", ...]`, and
    the real one is `[<python>, -S, -E, <agb>, bridge, ...]`. Every case here
    was therefore checked against an array four elements shorter than any that
    exists, which is why forty of them missed a reader that walked the WHOLE
    array and so answered `/real/config` for `<agb> --config /real/config
    bridge` -- a job `agb` refuses as an unknown command, that starts no bridge
    and holds no map, and that then outranked the job which did.

    Applied as a second axis rather than by rewriting the cases, so both shapes
    are checked and neither can quietly become the only one.

    Inserted with NO added whitespace, so the minified case stays minified: the
    reader is a real parser now, but the corpus still holds the shapes that
    broke the scan it replaced and they are worth keeping honest.
    """
    marker = "<string>bridge</string>"
    if marker not in raw:
        return raw
    at = raw.index(marker)
    insert = "".join("<string>%s</string>" % (e,) for e in _PREFIX_ELEMENTS)
    return raw[:at] + insert + raw[at:]


# (name, plist source, expected value, expected exit status)
#
# ⚠️ Every entry is a plist a HUMAN could leave behind -- `install.sh` renders
# exactly one shape and this reader exists for the other thirty-nine. The names
# marked "was wrong" are the ones the awk token scan this replaced got wrong;
# they are kept in the corpus rather than deleted with it, because a corpus that
# only holds the cases the current reader passes proves nothing about the next
# one.
_CORPUS = [
    ("plain", _wrap(_argv("bridge", "--config", "/a/config")),
     "/a/config", 0),
    ("minified",
     "<plist version=\"1.0\"><dict><key>ProgramArguments</key><array>"
     "<string>bridge</string><string>--config</string>"
     "<string>/a/config</string></array></dict></plist>",
     "/a/config", 0),
    ("inline-spelling", _wrap(_argv("bridge", "--config=/a/config")),
     "/a/config", 0),
    ("both-spellings-last-wins",
     _wrap(_argv("bridge", "--config", "/decoy/config", "--config=/a/config")),
     "/a/config", 0),
    ("repeated-pair-last-wins",
     _wrap(_argv("bridge", "--config", "/decoy/config",
                 "--config", "/a/config")),
     "/a/config", 0),
    ("value-that-looks-like-a-flag",
     _wrap(_argv("bridge", "--config", "--config=/decoy/config")),
     "--config=/decoy/config", 0),
    ("flag-in-value-position",
     _wrap(_argv("bridge", "--workspace", "--config=/decoy/config")),
     "", 0),
    ("entity-amp", _wrap(_argv("bridge", "--config", "/a&amp;b/config")),
     "/a&b/config", 0),
    ("entity-lt", _wrap(_argv("bridge", "--config", "/a&lt;b/config")),
     "/a<b/config", 0),
    ("entity-escaped-lt",
     _wrap(_argv("bridge", "--config", "/a&amp;lt;b/config")),
     "/a&lt;b/config", 0),
    ("charref-in-value",
     _wrap(_argv("bridge", "--config", "&#47;a/config")), "/a/config", 0),
    # was wrong: flag names were compared raw against three named entities.
    ("charref-in-flag-name",
     _wrap(_argv("bridge", "&#45;&#45;config", "/a/config")), "/a/config", 0),
    # was wrong: `<string >` matched no token, so the element vanished.
    ("blank-in-start-tag",
     _wrap("  <key>ProgramArguments</key>\n  <array>\n"
           "    <string>bridge</string>\n    <string>--config</string>\n"
           "    <string >/a/config</string>\n"
           "    <string>--workspace</string>\n"
           "    <string>farm</string>\n  </array>\n"),
     "/a/config", 0),
    # was wrong: `</array >` never closed argv, so WatchPaths overwrote it.
    ("blank-in-end-tag",
     _wrap("  <key>ProgramArguments</key>\n  <array>\n"
           "    <string>bridge</string>\n    <string>--config</string>\n"
           "    <string>/a/config</string>\n  </array >\n"
           "  <key>WatchPaths</key>\n  <array>\n"
           "    <string>--config</string>\n"
           "    <string>/decoy/config</string>\n  </array>\n"),
     "/a/config", 0),
    # was wrong: a tag matched within one record only.
    ("tag-spanning-lines",
     _wrap("  <key>ProgramArguments</key>\n  <array>\n"
           "    <string>bridge</string>\n    <string\n     >--config</string>\n"
           "    <string>/a/config</string>\n  </array>\n"),
     "/a/config", 0),
    ("comment-between-key-and-array",
     _wrap("  <key>ProgramArguments</key>\n"
           "  <!-- was: <array><string>x</string></array> -->\n"
           + _argv("bridge", "--config", "/a/config").split("\n", 1)[1]),
     "/a/config", 0),
    ("comment-spanning-lines",
     _wrap("  <key>ProgramArguments</key>\n  <!--\n    <array>\n    </array>\n"
           "  -->\n"
           + _argv("bridge", "--config", "/a/config").split("\n", 1)[1]),
     "/a/config", 0),
    ("comment-inside-argv",
     _wrap("  <key>ProgramArguments</key>\n  <array>\n"
           "    <string>bridge</string>\n"
           "    <!-- was: </array><key>Other</key> -->\n"
           "    <string>--config</string>\n"
           "    <string>/a/config</string>\n  </array>\n"),
     "/a/config", 0),
    ("comment-inside-a-value",
     _wrap(_argv("bridge", "--config", "/a<!-- why -->/config")),
     "/a/config", 0),
    # was wrong: the state crossed records, the text before the opener did not.
    ("comment-spanning-a-value",
     _wrap("  <key>ProgramArguments</key>\n  <array>\n"
           "    <string>bridge</string>\n    <string>--config</string>\n"
           "    <string>/a<!--\n      why\n    -->/config</string>\n"
           "    <string>--workspace</string>\n"
           "    <string>farm</string>\n  </array>\n"),
     "/a/config", 0),
    ("pi-between-key-and-array",
     _wrap("  <key>ProgramArguments</key>\n"
           "  <?note <array><string>x</string></array> ?>\n"
           + _argv("bridge", "--config", "/a/config").split("\n", 1)[1]),
     "/a/config", 0),
    ("pi-inside-a-comment",
     _wrap("  <key>ProgramArguments</key>\n"
           "  <!-- a <? inside a comment -->\n"
           + _argv("bridge", "--config", "/a/config").split("\n", 1)[1]),
     "/a/config", 0),
    # was wrong: the section was dropped whole, so the value was lost.
    ("cdata-carrying-the-value",
     _wrap("  <key>ProgramArguments</key>\n  <array>\n"
           "    <string>bridge</string>\n    <string>--config</string>\n"
           "    <string><![CDATA[/a/config]]></string>\n"
           "    <string>--workspace</string>\n"
           "    <string>farm</string>\n  </array>\n"),
     "/a/config", 0),
    ("cdata-manufacturing-under-another-key",
     _wrap("  <key>Note</key>\n  <string><![CDATA[<key>ProgramArguments</key>"
           "<array><string>--config</string><string>/decoy/config</string>"
           "</array>]]></string>\n" + _argv("bridge")),
     "", 0),
    # ⚠️ DOCTYPE FIRST, no `<?xml` declaration. Valid XML, launchd loads it,
    # and `plistlib`'s format SNIFFER refuses it -- see the fallback in the
    # reader. The declaration-first spelling below is the shipped one.
    ("doctype-first",
     _wrap(_argv("bridge", "--config", "/a/config"),
           head=("<!DOCTYPE plist PUBLIC \"-//Apple//DTD PLIST 1.0//EN\" "
                 "\"http://www.apple.com/DTDs/PropertyList-1.0.dtd\">\n"
                 "<plist version=\"1.0\">")),
     "/a/config", 0),
    ("declaration-then-doctype",
     _wrap(_argv("bridge", "--config", "/a/config"),
           head=("<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
                 "<!DOCTYPE plist PUBLIC \"-//Apple//DTD PLIST 1.0//EN\" "
                 "\"http://www.apple.com/DTDs/PropertyList-1.0.dtd\">\n"
                 "<plist version=\"1.0\">")),
     "/a/config", 0),
    ("doctype-with-an-internal-subset",
     _wrap(_argv("bridge", "--config", "/a/config"),
           head=("<!DOCTYPE plist [<!ENTITY decoy \"/decoy/config\">]>\n"
                 "<plist version=\"1.0\">")),
     "/a/config", 0),
    ("watchpaths-after-argv",
     _wrap(_argv("bridge", "--config", "/a/config")
           + "  <key>WatchPaths</key>\n  <array>\n"
             "    <string>--config</string>\n"
             "    <string>/decoy/config</string>\n  </array>\n"),
     "/a/config", 0),
    ("strings-before-argv",
     _wrap("  <key>Label</key>\n  <string>--config</string>\n"
           "  <key>ProcessType</key>\n  <string>/decoy/config</string>\n"
           + _argv("bridge", "--config", "/a/config")),
     "/a/config", 0),
    ("nested-program-arguments",
     _wrap(_argv("bridge", "--config", "/a/config")
           + "  <key>EnvironmentVariables</key>\n  <dict>\n"
             "    <key>ProgramArguments</key>\n    <array>\n"
             "      <string>--config</string>\n"
             "      <string>/decoy/config</string>\n    </array>\n  </dict>\n"),
     "/a/config", 0),
    ("empty-argv",
     _wrap("  <key>ProgramArguments</key>\n  <array/>\n"), "", 0),
    ("dangling-flag",
     _wrap(_argv("bridge", "--config")), "", 0),
    ("empty-inline-value",
     _wrap(_argv("bridge", "--config=", "--workspace", "farm")), "", 0),
    # ⚠️ THE SIX BELOW ARE ARGVS `agb bridge` REFUSES, and every one of them read
    # back a real-looking config until the reader stopped simulating the parser
    # and started calling it. A refused argv starts no bridge -- launchd restarts
    # it once every `ThrottleInterval` for ever under `KeepAlive` -- so the job
    # holds no map and "carries no --config" is the right answer, exactly as it is
    # for a flag sitting in front of the command word. This one was the worst of
    # them, because the corpus DECLARED `/a/config` for it and the oracle was told
    # to have no opinion.
    ("rejected-empty-inline-after-a-real-one",
     _wrap(_argv("bridge", "--config=/a/config", "--config=")),
     "", 0),
    # `bridge --config /a/config bridge --config /decoy/config`: the second
    # `bridge` is a stray positional (`unexpected argument`). The walk kept the
    # LAST value, which is right for a repeated flag and wrong here.
    ("rejected-stray-positional",
     _wrap(_argv("bridge", "--config", "/a/config",
                 "bridge", "--config", "/decoy/config")),
     "", 0),
    ("rejected-unknown-option",
     _wrap(_argv("bridge", "--config", "/a/config", "--bogus")), "", 0),
    # ⚠️ A rule NO argv walk would ever have had: `--watchdog` is parsed as a
    # float and refused when it is not one. Simulating that needs the parser's
    # numeric validation, not its flag table.
    ("rejected-watchdog-that-is-not-a-number",
     _wrap(_argv("bridge", "--config", "/a/config", "--watchdog", "soon")),
     "", 0),
    ("rejected-connections-below-one",
     _wrap(_argv("bridge", "--config", "/a/config", "--connections", "0")),
     "", 0),
    # ⚠️ And this one needs the BOOLEAN flag list, which the shell never had:
    # `--from-stdin` takes no value, so `=x` is an error.
    ("rejected-value-on-a-boolean-flag",
     _wrap(_argv("bridge", "--from-stdin=x", "--config", "/a/config")),
     "", 0),
    # The positive control for the pair above: the boolean flags are KNOWN, so a
    # legitimate one is not mistaken for an unknown option and does not erase the
    # config beside it.
    ("a-boolean-flag-is-not-an-unknown-option",
     _wrap(_argv("bridge", "--no-agterm", "--config", "/a/config")),
     "/a/config", 0),
    ("no-argv-key", _wrap("  <key>Label</key>\n  <string>x</string>\n"),
     "", 0),
    ("root-is-not-a-dict",
     "<plist version=\"1.0\"><array><string>--config</string>"
     "<string>/decoy/config</string></array></plist>\n", "", 0),
    ("empty-plist", "<plist/>\n", "", 0),
    ("non-string-element",
     _wrap("  <key>ProgramArguments</key>\n  <array>\n"
           "    <string>bridge</string>\n    <string>--config</string>\n"
           "    <integer>7</integer>\n"
           "    <string>--workspace</string>\n"
           "    <string>farm</string>\n  </array>\n"),
     "", 0),
    ("utf8-in-the-value",
     _wrap(_argv("bridge", "--config", "/été/config")),
     "/été/config", 0),
    ("crlf",
     _wrap(_argv("bridge", "--config", "/a/config")).replace("\n", "\r\n"),
     "/a/config", 0),
    ("blank-in-the-value",
     _wrap(_argv("bridge", "--config", "/a b/config")), "/a b/config", 0),
    # ⚠️ THE COMMAND WORD. `ProgramArguments` is the whole command line, and
    # only what follows `bridge` is the bridge's argv. A flag in FRONT of it is
    # the command name -- `agb` refuses it as unknown, launchd restarts the job
    # once every `ThrottleInterval` for ever, and no bridge is ever started.
    # Reading a config off that array made a dead job an exact, declaring
    # claimant of a live instance's map.
    ("flag-before-the-command-word",
     _wrap(_argv("/usr/bin/python3", "-S", "-E", "/Users/me/agb",
                 "--config", "/decoy/config", "bridge")),
     "", 0),
    ("flag-both-before-and-after-the-command-word",
     _wrap(_argv("/usr/bin/python3", "-S", "-E", "/Users/me/agb",
                 "--config", "/decoy/config", "bridge",
                 "--config", "/a/config")),
     "/a/config", 0),
    # `agb` is not `bridge`, however much its path ends in something similar --
    # only an element EQUAL to `bridge` opens the walk.
    ("a-path-that-merely-contains-the-command-word",
     _wrap(_argv("/usr/bin/python3", "-S", "-E", "/Users/bridge/bin/agb",
                 "bridge", "--config", "/a/config")),
     "/a/config", 0),
    # No `bridge` at all: this argv starts something else (or nothing). Not an
    # error -- the same answer as an argv with no `--config`, which is what a
    # plist predating the flag gives and what the caller ranks below every job
    # that names a config.
    ("no-command-word-at-all",
     _wrap(_argv("/usr/bin/python3", "-S", "-E", "/Users/me/agb",
                 "close-done", "--config", "/decoy/config")),
     "", 0),
    ("a-wrapper-that-hides-the-argv",
     _wrap(_argv("/bin/sh", "-c",
                 "exec /usr/bin/python3 /Users/me/agb bridge "
                 "--config /decoy/config")),
     "", 0),
    # exit 2: this file says nothing, which is not the same as "no --config".
    ("truncated",
     "<plist version=\"1.0\"><dict><key>ProgramArguments</key><array>"
     "<string>bridge</string><string>--config</string>"
     "<string>/a/config</string><string>--work", "", 2),
    ("not-xml-at-all", "this is a note to self\n", "", 2),
]


def test_the_corpus_covers_every_shape_it_claims_to():
    """Non-vacuity for the parametrisation itself: names unique, and the corpus
    really does hold both polarities and both exit statuses."""
    names = [case[0] for case in _CORPUS]
    assert len(names) == len(set(names)), names
    assert len(_CORPUS) >= 40, len(_CORPUS)
    assert sum(1 for case in _CORPUS if case[3] == 2) >= 2
    assert sum(1 for case in _CORPUS if case[2]) >= 25
    assert sum(1 for case in _CORPUS if not case[2] and case[3] == 0) >= 8
    # ⚠️ And it holds the class the reader stopped simulating: argvs `agb bridge`
    # refuses outright, which the walk answered a real-looking config for.
    assert sum(1 for case in _CORPUS
               if case[0].startswith("rejected-")) >= 6, names


def test_an_argv_the_bridge_refuses_is_decided_and_not_excused(mac):
    """⚠️ NON-VACUITY FOR THE ORACLE`S FIX, and it is the whole finding.

    `_bridge_would_resolve` used to answer `_UNDECIDED` whenever
    `parse_bridge_args` raised, and the corpus then let the *declared*
    expectation stand with nothing checking it -- which is how
    `bridge --config=/a/config --config=` came to declare `/a/config` for an argv
    that starts no bridge at all. A harness that encodes the bug is worse than one
    that misses it, so this asserts the sentinel is gone from that class: every
    `rejected-*` case must be cross-checked, and must be cross-checked to `""`.

    The premise is asserted first, from the parser itself, so this cannot pass by
    the cases quietly becoming acceptable ones.
    """
    rejected = [case for case in _CORPUS if case[0].startswith("rejected-")]
    assert len(rejected) >= 6, [case[0] for case in rejected]
    for name, raw, expected, _status in rejected:
        argv = plistlib.loads(raw.encode())["ProgramArguments"]
        with pytest.raises(Exception):
            mac.parse_bridge_args(argv[argv.index("bridge") + 1:])
        assert expected == "", name
        for shape in (raw, _as_installed(raw)):
            assert _bridge_would_resolve(mac, shape) == "", (name, shape)


def test_the_corpus_really_does_hold_both_argv_shapes():
    """Non-vacuity for the `as-installed` axis below.

    ⚠️ `_as_installed` is a no-op on a plist with no `<string>bridge</string>`
    in it, which is right (there is nothing to put a prefix in front of) and
    would also be right if the transform were broken. So this asserts that it
    really does change most of the corpus, and that what it produces really is
    the shape the shipped template has -- read out of `dist/com.agbridge.plist`
    rather than restated here, because a prefix this file invents proves the
    reader against this file's idea of a plist and not against launchd's.
    """
    changed = [case[0] for case in _CORPUS
               if _as_installed(case[1]) != case[1]]
    assert len(changed) >= 30, changed
    with open(os.path.join(conftest.REPO_ROOT, "dist",
                           "com.agbridge.plist"), "rb") as handle:
        shipped = plistlib.loads(handle.read())["ProgramArguments"]
    assert shipped[:shipped.index("bridge")] == \
        ["@PYTHON@", "-S", "-E", "@AGB@"]
    assert len(_PREFIX_ELEMENTS) == shipped.index("bridge")


@pytest.mark.parametrize("shape", ["bare", "as-installed"])
@pytest.mark.parametrize("name,raw,expected,status",
                         _CORPUS, ids=[case[0] for case in _CORPUS])
def test_plist_arg_answers_what_the_bridge_would_resolve(plist_arg, mac, name,
                                                         raw, expected,
                                                         status, shape):
    """⚠️ THE ACCEPTANCE BAR FOR REPLACING THE TOKEN SCAN WITH `plistlib`.

    Four consecutive review rounds produced findings of exactly one kind: a
    hand-rolled XML tokenizer is not an XML parser. Each round added a rule and
    the next round found the rule it did not have -- whitespace in tags,
    comments spanning a value, CDATA, processing instructions, DOCTYPE,
    character references, minification, nesting. This corpus is what says the
    replacement is not a fifth rule: it holds every shape the scan got wrong
    AND every shape it got right, and both are compared against authorities that
    are not `agb-refresh`'s own code.

    The cross-check is the point. `plistlib` answers "what is this argv" and
    `agb_mac.parse_bridge_args` answers "what does `agb bridge` do with it"; the
    declared expectation is only what stands where those two have no opinion
    (see `_bridge_would_resolve`), and the assertion below refuses to let that
    happen silently for a case that could have been checked.

    ⚠️ AND SINCE THE READER MOVED INTO `agb instances`, HALF THE CROSS-CHECK IS
    SELF-REFERENTIAL -- `run_instances` calls `parse_bridge_args`. The corpus is
    kept, and kept HERE, because what it still holds up is the part that is not
    shared: `plistlib` as an independent authority, and the whole hop from a
    shell function to a status and a byte string. Every case runs the shipped
    `plist_arg`, which is the thing `bind_label_to_config` actually depends on.
    See `_bridge_would_resolve` for the itemised list of what survives.

    ⚠️ TWICE, once per argv SHAPE, and the second is the one the corpus did not
    have: `["bridge", ...]` is not what a plist holds. See `_as_installed`.
    """
    if shape == "as-installed":
        raw = _as_installed(raw)
    got_status, got = plist_arg(raw)
    assert (got_status, got) == (status, expected), name
    truth = _bridge_would_resolve(mac, raw)
    if truth is not _UNDECIDED:
        assert truth == expected, (name, truth)
    else:
        # Only the shapes that genuinely have no authority may land here: a file
        # `plistlib` refuses, or a plist carrying no argv for the parser to be
        # asked about. ⚠️ An argv `agb bridge` REFUSES is no longer on this list
        # -- the refusal IS the answer, and excusing it here is what let
        # `empty-inline-after-a-real-one` declare `/a/config` unchallenged.
        assert name in ("truncated", "not-xml-at-all",
                        "empty-argv", "no-argv-key", "root-is-not-a-dict",
                        "empty-plist", "non-string-element"), name


def _lib_with(tmp_path, name, edit=None, drop_mac=False, agb_edit=None):
    """A copy of the installed tree, optionally with `agb_mac` edited or absent.

    ⚠️ A COPY, never a symlink to the repo: the point is a DIFFERENT parser, and
    a link would hand the reader the real one back.

    ⚠️ `agb_edit=` reaches the OTHER file, and it exists for the probe. Since
    the reader moved, `agb` is on the path too -- it is what dispatches
    `instances` at all -- so "an installed tree too old to have this command" is
    a question only an edit to `agb` can ask. `edit=` cannot: an `agb_mac`
    without `run_instances` is an `AttributeError` inside `cmd_instances`, which
    is exit 3, whereas an `agb` without the dispatch arm is `unknown command`,
    exit 2 with EMPTY STDOUT -- and telling those two apart is the probe's
    entire job.
    """
    import shutil
    lib = tmp_path / name
    lib.mkdir()
    if agb_edit is None:
        shutil.copy(conftest.AGB_PATH, str(lib / "agb"))
    else:
        text = open(conftest.AGB_PATH, encoding="utf-8").read()
        before, after = agb_edit
        assert before in text, before            # non-vacuity: it really edits
        with open(str(lib / "agb"), "w", encoding="utf-8") as handle:
            handle.write(text.replace(before, after, 1))
    if not drop_mac:
        text = open(conftest.MAC_PATH, encoding="utf-8").read()
        if edit is not None:
            before, after = edit
            assert before in text, before        # non-vacuity: it really edits
            text = text.replace(before, after, 1)
        with open(str(lib / "agb_mac"), "w", encoding="utf-8") as handle:
            handle.write(text)
    return str(lib / "agb")


def _read_with(tmp_path, agb, raw, flag="--config"):
    """`plist_arg` against one plist, with `$agb` pointed somewhere chosen.

    ⚠️ `$agb` now selects the ENTIRE reader (`plist_arg` execs
    `"$python" -S -E "$agb" instances`), not merely the `agb_mac` a separate
    `-c` program loaded from beside it. That makes `_lib_with(edit=...)` a
    sharper instrument than it was -- an edit to the copied `agb_mac` is the
    only `agb_mac` in play -- and it is why the probe's own control below can
    edit the copied tree and watch the script refuse.
    """
    harness = tmp_path / ("read_%s.sh" % (abs(hash(agb)),))
    harness.write_text("python=%s\nagb=%s\nagentsdir=%s\n"
                       % (sys.executable, agb, tmp_path)
                       + _extract_sh("plist_arg") + "\n"
                       + "plist_arg \"$1\" \"$2\"\n")
    target = tmp_path / ("read_%s.plist" % (abs(hash(agb)),))
    target.write_bytes(raw.encode())
    proc = subprocess.Popen(["sh", str(harness), str(target), flag],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, err = conftest.communicate(proc)
    assert err == b"", err
    return proc.returncode, out.decode("utf-8").rstrip("\n")


def test_the_reader_really_asks_agb_mac_and_not_a_walk_of_its_own(tmp_path):
    """⚠️ THE NON-VACUITY GUARD FOR THE WHOLE DESIGN, and it is behavioural
    rather than a grep of the source (a substring guard would pass by matching
    the comment that explains the rule).

    The reader loads `agb_mac` from beside `$agb` and hands it the post-`bridge`
    argv. So an `agb_mac` whose `BRIDGE_VALUE_ARGS` does not know `--config` must
    make the reader answer "carries no `--config`" for a plist that plainly
    contains one -- which is also the right answer in that world, since no
    `agb bridge` there would accept the flag either. A reader that walked the
    array itself could not tell the two trees apart.

    Both directions are asserted from the same plist, so this cannot pass by the
    reader answering "" for some unrelated reason.
    """
    raw = _wrap(_argv("/usr/bin/python3", "-S", "-E", "/Users/me/agb",
                      "bridge", "--config", "/a/config"))
    real = _lib_with(tmp_path, "real")
    assert _read_with(tmp_path, real, raw) == (0, "/a/config")
    blind = _lib_with(tmp_path, "blind",
                      edit=("    \"--config\": \"config\",\n", ""))
    assert _read_with(tmp_path, blind, raw) == (0, "")


def test_a_tree_with_no_agb_mac_beside_agb_is_fatal_not_silent(tmp_path,
                                                               refresh):
    """⚠️ "I could not answer" is not "the answer is nothing", and the parser
    failing to load is a third thing again: it is a statement about `--agb`, not
    about the plist and not about `--python`.

    It has to be loud. A tree missing `agb_mac` cannot run step 2 either -- `agb
    forget-rows` goes through `agb._load_mac()` -- so answering "no config" for
    every plist would bounce whichever job the unread plists left unclaimed and
    then fail the forget anyway, after the bootout.
    """
    lonely = _lib_with(tmp_path, "lonely", drop_mac=True)
    raw = _wrap(_argv("/usr/bin/python3", "-S", "-E", "/Users/me/agb",
                      "bridge", "--config", "/a/config"))
    assert _read_with(tmp_path, lonely, raw) == (3, "")
    # ...and the script turns that status into a `die` naming the right flag,
    # rather than into a silent "this plist says nothing".
    #
    # ⚠️ It is the PROBE that reaches it now, not the first plist read --
    # `--probe` runs `run_instances` too, so a tree with no `agb_mac` cannot
    # answer it either. The probe forwards exit 3 to this same `plist_read_ok`
    # message on purpose: folding it into the probe's own text would send an
    # operator to replace a `python3` that is working perfectly.
    refresh.write_plist()
    rc, out, err = refresh.run(["--agb", lonely])
    assert rc == 1, (rc, out, err)
    assert "cannot load agb_mac" in err, err
    assert "--agb" in err, err
    assert "launchctl bootout" not in " ".join(refresh.calls()), refresh.calls()


def test_the_missing_sibling_is_an_oserror_and_not_an_importerror(tmp_path):
    """⚠️ THE SHAPE OF THE EXIT-3 CATCH, pinned from the failure that produced it.

    `agb._load_sibling` loads `agb_mac` BY PATH, so a tree without it raises
    `FileNotFoundError` -- an `OSError`. `cmd_instances` shipped catching
    `(ImportError, AttributeError)`, which does not include it: the exception
    escaped, `agb` printed a traceback and exited 1, and `agb-refresh` reads 1 as
    "the reader itself failed", i.e. blames `--python` for a tree that is missing
    a file. This asserts both halves of the contract at once -- the status AND
    the silence -- because either alone passes for the wrong reason.

    The premise is asserted first, from the interpreter rather than from us: the
    load really does raise something that is not an `ImportError`.
    """
    lonely = _lib_with(tmp_path, "lonely-direct", drop_mac=True)
    with pytest.raises(OSError) as caught:            # non-vacuity: the premise
        open(os.path.join(os.path.dirname(lonely), "agb_mac")).read()
    assert not isinstance(caught.value, ImportError), caught.value
    proc = subprocess.Popen([sys.executable, "-S", "-E", lonely, "instances",
                             "--plist", str(tmp_path / "nope.plist"),
                             "--arg", "--config"],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, err = conftest.communicate(proc)
    assert proc.returncode == 3, (proc.returncode, err)
    assert out == b"", out
    assert err == b"", err


def test_a_failure_inside_the_reader_is_not_disguised_as_a_missing_tree(
        tmp_path):
    """The other half of that catch, and the reason it wraps the LOAD only.

    Catching around `run_instances` as well would turn every bug in the reader
    into exit 3 -- `agb-refresh`'s "cannot load agb_mac, pass --agb", which is a
    sentence about a file that is sitting right there and is fine. A tree that
    loads must be allowed to fail loudly.
    """
    broken = _lib_with(tmp_path, "broken-run",
                       edit=("def run_instances(argv, out=None):",
                             "def run_instances(argv, out=None):\n"
                             "    raise ValueError('boom')"))
    proc = subprocess.Popen([sys.executable, "-S", "-E", broken, "instances",
                             "--probe"],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    _out, err = conftest.communicate(proc)
    assert proc.returncode != 3, err
    assert b"boom" in err, err


def test_the_fake_interpreter_really_runs_the_tree_the_flag_names(refresh,
                                                                  tmp_path):
    """⚠️ NON-VACUITY FOR THE FIXTURE RESHAPE, which is otherwise invisible.

    The fake interpreter used to route only `-c` to a real python; every plist
    read now goes through `agb instances`, so it routes on `$4` instead. If that
    arm were wrong the whole file would die at the probe -- loudly -- but if it
    routed `instances` to a REAL python while the reader silently answered from
    somewhere other than `--agb`'s tree, nothing would fail at all.

    So: the same `_lib_with(edit=...)` instrument the direct reader test uses,
    driven END TO END through the script this time. An `agb_mac` whose
    `BRIDGE_VALUE_ARGS` does not know `--config` must make every plist answer
    "carries no config", which sends the run to the default label -- and the
    unedited copy of the same tree must find `hostb`. Two runs, one difference.
    """
    real = _existing_config(refresh, "hostb")
    refresh.write_plist(label="com.agbridge.hostb", instance="hostb")

    seeing = _lib_with(tmp_path, "seeing")
    rc, out, err = refresh.run(["--config", real, "--agb", seeing])
    assert rc == 0, err
    assert "stopped:  com.agbridge.hostb" in out, out

    blind = _lib_with(tmp_path, "blind-e2e",
                      edit=("    \"--config\": \"config\",\n", ""))
    rc, out, err = refresh.run(["--config", real, "--agb", blind])
    assert rc == 0, err
    assert "stopped:  com.agbridge\n" in out, out
    assert "com.agbridge.hostb" not in out, out


def test_an_agb_too_old_to_have_instances_is_refused_at_the_probe(refresh,
                                                                  tmp_path):
    """⚠️ THE WHOLE REASON THE PROBE HAS A LITERAL ANSWER, end to end.

    An installed `agb` from 0.5.0 or earlier answers `agb instances` with
    `unknown command`: exit **2**, `USAGE` on stderr, and EMPTY STDOUT -- which
    is byte-identical to a current `agb` saying "this plist carries no
    `--config`". Without a probe every plist would read as silent, no job would
    claim the config, and the run would bounce `com.agbridge` and forget the
    default map while the named instance's bridge kept running, reporting
    success in the words it uses when it is right.

    A status alone cannot decide it either -- exit 2 is a legitimate answer from
    the new command -- so the probe compares STDOUT against a literal.

    Simulated by removing the dispatch arm from a copy of `agb`, which is
    exactly what an older one does not have.
    """
    old = _lib_with(tmp_path, "old-agb",
                    agb_edit=("    if cmd == \"instances\":\n"
                              "        return cmd_instances(rest)\n", ""))
    # Non-vacuity, from `agb` itself: that tree really does answer exit 2 with
    # nothing on stdout, i.e. the collision this probe exists to break.
    proc = subprocess.Popen([sys.executable, "-S", "-E", old, "instances",
                             "--probe"],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, err = conftest.communicate(proc)
    assert (proc.returncode, out) == (2, b""), (proc.returncode, out)
    assert b"unknown command" in err, err

    real = _existing_config(refresh, "hostb")
    refresh.write_plist(label="com.agbridge.hostb", instance="hostb")
    rc, out, err = refresh.run(["--config", real, "--agb", old])
    assert rc != 0, out
    assert "install.sh mac" in err, err
    # Nothing was bounced, forgotten or started, and it did not fall through to
    # the default label -- which is the failure it is refusing to perform.
    assert refresh.calls() == [], refresh.calls()
    assert "instance:" not in out, out


def test_the_probe_is_answer_compared_and_not_status_compared(refresh,
                                                              tmp_path):
    """The probe's own negative control, and the half a status cannot see.

    `--python /bin/echo` covers an interpreter that exits 0 meaning nothing; this
    covers a TREE that exits 0 meaning something else. A probe written as `run it
    and check the status` passes here, because this `agb instances --probe`
    succeeds -- it just does not say `instances-ok`.

    Paired with an unedited copy of the same tree so the difference is the one
    line under test and not the copying.
    """
    wrong = _lib_with(tmp_path, "wrong-answer",
                      edit=("instances-ok\\n", "instances-ok-ish\\n"))
    proc = subprocess.Popen([sys.executable, "-S", "-E", wrong, "instances",
                             "--probe"],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, _err = conftest.communicate(proc)
    assert (proc.returncode, out) == (0, b"instances-ok-ish\n"), out

    refresh.write_plist()
    rc, _out, err = refresh.run(["--agb", wrong])
    assert rc != 0, err
    assert "did not answer instances-ok" in err, err
    assert refresh.calls() == [], refresh.calls()
    # The control: the same copy, unedited, gets all the way through.
    rc, out, err = refresh.run(["--agb", _lib_with(tmp_path, "right-answer")])
    assert rc == 0, err
    assert "stopped:  com.agbridge" in out, out


def test_plist_arg_really_forwards_the_launch_agents_directory(tmp_path):
    """⚠️ NON-VACUITY FOR A FLAG THAT IS INERT TODAY, which is the only kind
    that can stop being forwarded without anything failing.

    `plist_arg` passes `--launch-agents "$agentsdir"`, and `--plist` mode never
    lists a directory -- so no plist read can observe it, and the whole suite
    stays green if the forwarding is dropped. What CAN observe it is `agb`'s
    own parser: `--launch-agents` with an empty value is a missing-value error,
    not a default. So a harness that leaves `$agentsdir` empty must fail, and
    fail naming the flag.

    Drop the forwarding and this call answers `(0, "/a/config")` instead, which
    is what the mutation check confirmed.
    """
    harness = tmp_path / "no_agentsdir.sh"
    harness.write_text("python=%s\nagb=%s\nagentsdir=\n"
                       % (sys.executable, conftest.AGB_PATH)
                       + _extract_sh("plist_arg") + "\n"
                       + "plist_arg \"$1\" \"$2\"\n")
    target = tmp_path / "forwarded.plist"
    target.write_text(_wrap(_argv("/usr/bin/python3", "-S", "-E",
                                  "/Users/me/agb", "bridge",
                                  "--config", "/a/config")))
    proc = subprocess.Popen(["sh", str(harness), str(target), "--config"],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, err = conftest.communicate(proc)
    assert proc.returncode == 1, (proc.returncode, out, err)
    assert b"--launch-agents" in err, err
    assert out == b"", out
    # The control: the same plist, the same harness shape, one value supplied.
    assert _read_with(tmp_path, conftest.AGB_PATH,
                      _wrap(_argv("/usr/bin/python3", "-S", "-E",
                                  "/Users/me/agb", "bridge",
                                  "--config", "/a/config"))) == (0, "/a/config")


def test_an_argv_the_bridge_refuses_does_not_claim_the_map(refresh, tmp_path):
    """⚠️ THE FINDING, end to end: a hand-edited plist whose argv `agb bridge`
    REFUSES must not outrank the job that is actually running.

    `bridge --config <real> --bogus` is `unknown option`, so launchd restarts
    that job once every `ThrottleInterval` for ever and no bridge is started from
    it. Reading `<real>` off it made it an exact, DECLARING claimant -- rank 1 --
    and `*.plist` expands in collating order, so `com.agbridge.aaa` won over the
    live `com.agbridge.hostb` naming the same file: the run bounced the dead job,
    waited for nothing, and forgot the map with hostb's bridge still merging.
    """
    real = refresh.config("hostb")
    (tmp_path / ".config" / "agbridge" / "hostb").mkdir(parents=True)
    refresh.write_plist(label="com.agbridge.hostb", instance="hostb")
    text = refresh.plist_text("com.agbridge.hostb").replace(
        "  </array>", "    <string>--bogus</string>\n  </array>")
    (tmp_path / "Library" / "LaunchAgents"
     / "com.agbridge.aaa.plist").write_text(text)
    rc, out, _err = refresh.run(["--config", real])
    assert rc == 0, out
    assert "stopped:  com.agbridge.hostb" in out, out
    assert "com.agbridge.aaa" not in out, out


# ⚠️ `test_the_embedded_reader_is_ascii_and_apostrophe_free` STOOD HERE AND IS
# GONE, deleted rather than repointed, and the reason is worth more than the
# test was. It guarded two rules on the text of the `-c` program `plist_arg`
# used to embed: no apostrophe (it sat inside `'...'` in POSIX sh, so one would
# close the quoting and break the whole script) and pure ASCII (Python decoded a
# `-c` program with the LOCALE`s filesystem encoding, so a single `⚠️` in a
# comment was `Unable to decode the command from the command line` -- the reader
# never ran, and every caller read "this plist names no config", the quiet
# wrong-job bounce; measured on 3.6.8).
#
# There is no embedded program left to assert on: `plist_arg` calls
# `agb instances`, and the script runs no `-c` program at all. Every non-vacuity
# assertion the guard carried (`"plistlib" in program`, `"parse_bridge_args" in
# program`, `len(program) > 500`) is now unsatisfiable by anything that remains,
# so repointing it at another program would mean inventing a subject for it.
#
# ⚠️ ONLY ONE OF THE TWO RULES WAS ABOUT THE PROGRAM`S TEXT, though, and it is
# the one that survives: the value must still leave the reader as UTF-8 BYTES,
# because `-E` does not touch `LC_ALL`. That is contract 1, it is stated in
# `agb_mac.run_instances`` docstring where the code now lives, and it is guarded
# BEHAVIOURALLY by `test_the_value_comes_back_as_bytes_whatever_the_locale_says`
# below (three locales, through the shipped shell function) and by its twin in
# `tests/test_bridge_rows.py`. A behavioural guard is what the source-text one
# should have been all along.


@pytest.mark.parametrize("locale", ["C", "POSIX", "en_US.ISO-8859-1"])
def test_the_value_comes_back_as_bytes_whatever_the_locale_says(plist_arg,
                                                                locale):
    """⚠️ `print` encodes with the LOCALE, and `-E` does not touch `LC_ALL`.

    A config path with a non-ASCII character in it -- ordinary on a Mac, where
    the filesystem is UTF-8 by fiat -- came back two different kinds of wrong
    once the reader became a Python program. Under `LC_ALL=C` Python's stdout is
    ASCII, so `print` raises `UnicodeEncodeError`: the reader dies, prints a
    traceback, and every caller reads "this plist names no config" -- the quiet
    wrong-job bounce for a named instance. Under an ISO-8859-1 locale it is
    worse, because it SUCCEEDS: the path comes out transcoded into bytes that
    name nothing, so the banner, the liveness pattern and `forget-rows` all act
    on a path that exists nowhere, with no error anywhere.

    The awk this replaced passed bytes through untouched, and so must this: the
    value is encoded to UTF-8 and written to `sys.stdout.buffer`.
    """
    raw = _wrap(_argv("bridge", "--config", "/été/config"))
    status, got = plist_arg(raw, "--config", locale=locale)
    assert (status, got) == (0, "/été/config"), locale


@pytest.mark.parametrize("flag", ["--config", "--rows"])
def test_the_corpus_reads_the_other_value_flag_the_same_way(plist_arg, mac,
                                                           flag):
    """`plist_arg` is called for `--rows` too (`bind_label_to_config`), and the
    walk must not be `--config`-shaped. `--rows` is in `$BRIDGE_VALUE_FLAGS`, so
    it consumes its own argument whichever flag is being asked about."""
    raw = _wrap(_argv("bridge", "--config", "/a/config",
                      "--rows", "/a/rows", "--workspace", "farm"))
    status, got = plist_arg(raw, flag)
    assert status == 0
    assert got == {"--config": "/a/config", "--rows": "/a/rows"}[flag]
    assert _bridge_would_resolve(mac, raw, flag) == got


def test_a_bridge_holding_this_map_through_rows_is_waited_for(refresh, tmp_path,
                                                              mac):
    """⚠️ `--config` is not the only flag that decides which map a bridge holds.

    `agb bridge` also takes `--rows`, and `render_settings` spends it before the
    config: `opts.get("rows") or rows_path(config)`. So `bridge --config
    <other>/config --rows <this map's rows>` is a bridge writing the very file
    this run is about to rewrite, while its `--config` names somewhere else
    entirely. Attributing on the config alone answered "not ours": zero waits,
    and `forget-rows` landing under a live bridge that merges-then-writes and
    re-mints rows against the ids it has just closed.
    """
    (tmp_path / ".config" / "agbridge").mkdir(parents=True)
    (tmp_path / "other").mkdir()
    elsewhere = str(tmp_path / "other" / "config")
    default_rows = mac.rows_path(refresh.config())
    # The premise, in the parser's own words: `--rows` wins over the config's
    # own derivation, so THIS is the map that bridge holds.
    assert mac.render_settings(
        {"config": elsewhere, "rows": default_rows})["rows"] == default_rows
    refresh.write_plist()              # the default job, modern shape
    rc, out, _err = refresh.run(
        alive_polls=6,
        alive_cmdline=refresh.cmdline(config=elsewhere)
        + " --rows " + default_rows)
    assert rc == 0
    # ⚠️ Asserted on the SLEEPS, not on the poll count: one `bridge_alive` call
    # runs `pgrep` twice (narrow, then broad), so "more than one poll" is true
    # of a run that waited for nothing at all. A sleep only happens inside the
    # loop, which is the property.
    sleeps = [n for n, line in enumerate(refresh.calls())
              if line.startswith("sleep")]
    assert sleeps, \
        "the --rows bridge was read as somebody else's: %s" % (refresh.calls(),)
    assert max(sleeps) < refresh.index("agb forget-rows"), refresh.calls()
    # ...and it is described as what it is, not as an untagged bridge (it
    # carries a `--config`) and not as a differently-spelled one (that config
    # names another map).
    assert "--rows names this same map" in out
    assert "carries no" not in out


def _rows_plist(refresh, tmp_path, mac, label, instance):
    """A hand-edited plist: a config of its own, and this run's rows file.

    Carries the interpreter prefix, like every real `ProgramArguments` -- only
    what follows `bridge` is the bridge's argv, and a helper that leaves it out
    checks the reader against a shape no plist has.
    """
    agents = tmp_path / "Library" / "LaunchAgents"
    (agents / (label + ".plist")).write_text(
        "<plist version=\"1.0\"><dict>\n"
        "  <key>ProgramArguments</key>\n  <array>\n"
        "    <string>/usr/bin/python3</string>\n"
        "    <string>-S</string>\n    <string>-E</string>\n"
        "    <string>/Users/me/agb</string>\n"
        "    <string>bridge</string>\n"
        "    <string>--config</string>\n"
        "    <string>%s</string>\n"
        "    <string>--rows</string>\n"
        "    <string>%s</string>\n"
        "  </array>\n</dict></plist>\n"
        % (refresh.config(instance), mac.rows_path(refresh.config())))


def test_a_plist_whose_rows_names_this_map_is_the_job_that_holds_it(
        refresh, tmp_path, mac):
    """The label side of the same flag: `--rows` decides which map a job holds.

    A job started `--config <elsewhere> --rows <this map's rows>` is the one
    writing the file this run rewrites, so it is the one that has to be stopped
    -- and looking only at `--config` left it running while the default label
    was booted out instead. Ranked LAST (see the table in
    `bind_label_to_config`): `install.sh` renders no `--rows`, so this may only
    ever replace the "nothing claims this config" fallback.
    """
    (tmp_path / ".config" / "agbridge" / "hostb").mkdir(parents=True)
    agents = tmp_path / "Library" / "LaunchAgents"
    (agents / "com.agbridge.plist").unlink()   # nothing claims the config
    _rows_plist(refresh, tmp_path, mac, "com.agbridge.hostb", "hostb")
    rc, out, _err = refresh.run(["--config", refresh.config()])
    assert rc == 0
    assert refresh.call("launchctl bootout").endswith("/com.agbridge.hostb"), \
        refresh.calls()
    assert "no plist in" not in out


def test_a_plist_that_names_this_config_beats_one_that_only_holds_its_rows(
        refresh, tmp_path, mac):
    """The negative control: a `--rows` claim must not outbid the real job.

    Ranking and matching are two questions (invariant 12). Both of these jobs
    write into this map, so both are claimants and both are named -- but the one
    whose `--config` IS this config is the instance's own, and bouncing the other
    instead would leave it running over the map `forget-rows` is about to
    rewrite.
    """
    (tmp_path / ".config" / "agbridge" / "hostb").mkdir(parents=True)
    refresh.write_plist()                    # declares this exact config
    _rows_plist(refresh, tmp_path, mac, "com.agbridge.hostb", "hostb")
    rc, out, _err = refresh.run(["--config", refresh.config()])
    assert rc == 0
    assert refresh.call("launchctl bootout").endswith("/com.agbridge"), \
        refresh.calls()
    # ...and the other one is not silently ignored: it is still holding the same
    # rows file after this run, which is exactly what the warning is for.
    assert "more than one launchd job claims" in out
    assert "com.agbridge.hostb" in out


def test_the_rows_file_this_run_was_given_is_part_of_its_map(refresh,
                                                             tmp_path):
    """`agb-refresh --rows <path>` rewrites THAT file, not the config's own.

    `instance_paths` lets an explicit `--rows` win on this side too, so the
    files this run touches are `dirname(<config>)/placements` and the rows file
    it was handed. A bridge writing that rows file is one to wait for however
    its own flags are spelled -- and asking only about the config's directory
    missed it.
    """
    (tmp_path / ".config" / "agbridge" / "hostb").mkdir(parents=True)
    (tmp_path / "elsewhere").mkdir()
    elsewhere_rows = str(tmp_path / "elsewhere" / "rows")
    refresh.write_plist()              # the default job, modern shape
    rc, out, _err = refresh.run(
        ["--rows", elsewhere_rows], alive_polls=6,
        alive_cmdline=refresh.cmdline("hostb") + " --rows " + elsewhere_rows)
    assert rc == 0
    sleeps = [n for n, line in enumerate(refresh.calls())
              if line.startswith("sleep")]
    assert sleeps, \
        "the rows file this run was given was not its map: %s" \
        % (refresh.calls(),)
    assert "--rows names this same map" in out
    # Non-vacuity: that really is the file being repaired.
    assert "--rows %s" % (elsewhere_rows,) in refresh.call("agb forget-rows")


def test_a_config_flag_inside_another_flags_value_is_not_proof_of_one(
        refresh, tmp_path, mac):
    """⚠️ `ps` flattens argv, so ` --config ` BYTES are not a `--config` FLAG.

    `bridge --workspace "farm --config /other/config"` leaves `config` unset --
    that bridge resolves `agb.config_path()` and is holding the DEFAULT map --
    but `ps` prints a line indistinguishable from one carrying the flag. Reading
    those bytes as proof skipped the default-map question entirely and answered
    "not ours" on a default refresh: zero waits, `forget-rows` under a live
    bridge over the map being repaired.

    Undecidable from the line alone, so it is resolved towards the wait: a
    `--config` preceded by another value-taking flag might be inside that flag's
    value, and the cost of being wrong that way is a bounded 10 s.
    """
    (tmp_path / ".config" / "agbridge").mkdir(parents=True)
    (tmp_path / "other").mkdir()
    elsewhere = str(tmp_path / "other" / "config")
    # The premise, from the parser: this argv carries NO config at all.
    assert mac.parse_bridge_args(
        ["--workspace", "farm --config " + elsewhere])["config"] is None
    refresh.write_plist()              # the default job, modern shape
    rc, out, _err = refresh.run(
        alive_polls=6,
        alive_cmdline=refresh.cmdline(with_config=False)
        + " --workspace farm --config " + elsewhere)
    assert rc == 0
    # The sleeps, for the reason spelled out in the test above: two `pgrep`
    # calls happen per poll whether or not anything was waited for.
    sleeps = [n for n, line in enumerate(refresh.calls())
              if line.startswith("sleep")]
    assert sleeps, \
        "the value's bytes were read as a flag: %s" % (refresh.calls(),)
    assert max(sleeps) < refresh.index("agb forget-rows"), refresh.calls()
    assert "part of another flag's value" in out


def test_a_config_flag_first_on_the_line_is_still_proof_of_one(refresh,
                                                               tmp_path):
    """The negative control for the test above, and the one that keeps it from
    degenerating into "any bridge is ours on a default run".

    Nothing precedes the `--config` in a line launchd started -- the plist's
    `ProgramArguments` put it immediately after `bridge` -- so there is no value
    it could be part of, and another instance's bridge is still not this run's
    to wait for. Without that, every plain `agb-refresh` on a two-instance Mac
    waits the full 10 s and warns that the forget may have been undone.
    """
    for name in ("", "hostb"):
        (tmp_path / ".config" / "agbridge" / name).mkdir(parents=True,
                                                         exist_ok=True)
    refresh.write_plist()              # the default job, modern shape
    rc, out, _err = refresh.run(alive_polls=10 ** 6,
                                alive_cmdline=refresh.cmdline("hostb"))
    assert rc == 0
    assert "still running" not in out
    assert refresh.index("pgrep") > -1, "the bridge was never polled for"


def test_a_config_flag_inside_the_agb_path_is_not_the_bridges(refresh,
                                                              tmp_path):
    """⚠️ argv starts after `<agb> bridge`, and so does the proof.

    `pgrep -f` matches the WHOLE command line, so the text before the command
    name is on it too. An agb installed under a directory containing the literal
    ` --config ` therefore put a marker in front of every argument -- with
    nothing before it, which is exactly the shape that reads as proof. A
    genuinely untagged bridge (no config flag at all, holding the default map)
    then answered "somebody else's" on a default refresh: zero waits,
    `forget-rows` under it.
    """
    (tmp_path / ".config" / "agbridge").mkdir(parents=True)
    home = tmp_path / "a --config b"
    home.mkdir()
    weird = str(home / "agb")
    # A symlink for the same reason the fixture's own `agb` is one: never
    # executed, but `plist_arg` resolves `agb_mac` from beside it.
    os.symlink(conftest.AGB_PATH, weird)
    refresh.write_plist()              # the default job, modern shape
    rc, out, _err = refresh.run(
        ["--agb", weird], alive_polls=6,
        alive_cmdline=refresh.cmdline(with_config=False).replace(
            str(tmp_path / "agb"), weird))
    assert rc == 0
    sleeps = [n for n, line in enumerate(refresh.calls())
              if line.startswith("sleep")]
    assert sleeps, \
        "the agb path was read as an argument: %s" % (refresh.calls(),)
    assert max(sleeps) < refresh.index("agb forget-rows"), refresh.calls()
    assert "carries no" in out


def test_a_config_flag_inside_another_arguments_value_is_not_the_plists(
        refresh, tmp_path, mac):
    """The same false positive on the plist side, where it is NOT undecidable.

    A plist's `ProgramArguments` is a real argv array, so
    `<string>--workspace</string><string>--config=/other/config</string>` is a
    workspace value and nothing else -- `agb bridge` never sees a config flag
    there. Reading it as one made every path in this script act on a map no
    process is running on: the banner named it, the liveness pattern was built
    from it, and `forget-rows` repaired it -- reporting "the map is already
    empty" and exiting 0 while the real map kept its stale bindings and the
    `no such session` spam that sent you here carried on.
    """
    assert mac.parse_bridge_args(
        ["--workspace", "--config=/other/config"])["config"] is None
    agents = tmp_path / "Library" / "LaunchAgents"
    (agents / "com.agbridge.plist").write_text(
        "<plist version=\"1.0\"><dict>\n"
        "  <key>ProgramArguments</key>\n  <array>\n"
        "    <string>bridge</string>\n"
        "    <string>--workspace</string>\n"
        "    <string>--config=/other/config</string>\n"
        "  </array>\n</dict></plist>\n")
    rc, out, _err = refresh.run()
    assert rc == 0
    # The banner, the forget and the pattern all name the map this job's bridge
    # actually resolves -- the default one -- and none of them name the value.
    assert "config %s" % (refresh.config(),) in out, out
    assert "--config %s" % (refresh.config(),) \
        in refresh.call("agb forget-rows")
    assert "/other/config" not in "\n".join(refresh.calls())
    assert "/other/config" not in out


def test_the_value_taking_flags_are_the_ones_agb_bridge_has(refresh):
    """⚠️ A cross-file agreement with no single source of truth (invariant 14).

    `agb-refresh` has to know which `agb bridge` flags CONSUME the next argument
    -- the plist reader walks `ProgramArguments` the way the parser walks argv,
    and the attribution asks whether a `--config` could be inside an earlier
    flag's value. Neither script can import the other, so the list is spelled in
    the shell and pinned here. A flag added to `BRIDGE_VALUE_ARGS` and not here
    makes the reader consume its value as an argument: a plist whose last
    element is that flag hands its value to the reader as a config path, and the
    attribution stops seeing that value as a place a `--config` can hide.
    """
    import agb_mac
    with open(SCRIPT) as handle:
        for line in handle.read().splitlines():
            if line.startswith("BRIDGE_VALUE_FLAGS="):
                spelled = line.split("=", 1)[1].strip('"').split()
                break
        else:
            raise AssertionError("no BRIDGE_VALUE_FLAGS in agb-refresh")
    assert sorted(spelled) == sorted(agb_mac.BRIDGE_VALUE_ARGS)


def _refresh_shown(label):
    """Run `agb-refresh`'s OWN name-from-label block for one label.

    The block is lifted out of the script by text and executed, rather than
    re-spelled here: a re-spelling would be a third copy of the rule and could
    drift from both. Extraction runs from `shown=$instance` through the banner
    line that consumes it, so the `${shown:-(default)}` fallback is the
    script's too and not this harness's.
    """
    with open(SCRIPT) as handle:
        lines = handle.read().splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.strip() == "shown=$instance":
            start = i
            break
    assert start is not None, "no `shown=$instance` in agb-refresh"
    end = None
    for i in range(start, len(lines)):
        if lines[i].lstrip().startswith('say "instance: '):
            end = i
            break
    assert end is not None, "no `say \"instance: ` after it"
    block = "\n".join(lines[start:end + 1])
    # Non-vacuity: the extracted text is the rule, not an empty slice.
    assert "case $label in" in block and "DEFAULT_LABEL" in block, block
    prog = ('say() { printf "%s\\n" "$*"; }\n'
            'DEFAULT_LABEL=com.agbridge\n'
            'instance=\n'
            'config=CONFIG\n'
            'label=$1\n' + block + "\n")
    proc = subprocess.Popen(["sh", "-c", prog, "sh", label],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, err = conftest.communicate(proc)
    assert proc.returncode == 0, (out, err)
    text = out.decode()
    assert "instance: " in text and " -- label" in text, text
    return text.split("instance: ", 1)[1].split(" -- label")[0]


@pytest.mark.parametrize("label", ["com.agbridge", "com.agbridge.hostb",
                                   "weird.label", "com.agbridge.a.b",
                                   "com.agbridgeX"])
def test_the_listing_names_an_instance_the_way_the_banner_does(mac, label):
    """⚠️ One rule, two languages, and it has already drifted once.

    `agb-refresh`'s banner and `agb instances`' listing both turn a launchd
    label into the name shown for an instance. Task 5 settled the rule in the
    shell -- only `com.agbridge` itself is "(default)", a custom label shows
    the label -- and the listing, written from the same rule, kept a third
    answer the rule forbids: the empty string, for both the default instance
    and a custom label. It reached the owner's Mac as a blank name column.

    The shell block is EXECUTED here rather than described, so the two cannot
    part company silently: a change to either side that the other does not
    follow fails this test by name.
    """
    assert mac.instance_display_name(label) == _refresh_shown(label)


def test_the_name_shown_for_a_label_is_not_the_same_for_every_label(mac):
    """Non-vacuity for the agreement above: it compares two constants only if
    both sides really do answer differently per label."""
    answers = [mac.instance_display_name(x)
               for x in ("com.agbridge", "com.agbridge.hostb", "weird.label")]
    assert answers == ["(default)", "hostb", "weird.label"], answers
    assert len(set(answers)) == 3, answers


def test_the_ten_second_warning_names_what_was_actually_still_running(refresh):
    """The 10s warning is a claim, and it was false in the case it fires most.

    A wait driven by the untagged probe named `$label` -- the job that was
    booted out, which is precisely the process that is NOT what the poll is
    still matching. The probe waits for a bridge it cannot attribute to a label
    at all, so the warning says that instead.

    ⚠️ Re-reasoned for the sweep: this runs bare, so the warning is a child's,
    and `rc == 0` now carries a second claim it did not before -- that a 10 s
    warning is NOT a sweep failure. Only a bridge that could not be started
    again is (the child's exit 4). The warning says the forget may have been
    undone, which is something to read; it does not leave an instance down.
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
    """`--instance ""` must not read as "not given" and refresh the DEFAULT
    instance while echoing the name back -- the exact silent-wrong-instance
    failure the flag exists to prevent.

    `need` is what refuses it now (see the parametrised test below);
    `instance_ok` keeps its own empty case behind that, because the two
    validators are compared arm for arm against `install.sh`'s.
    """
    rc, _out, err = refresh.run(["--instance", ""])
    assert rc != 0
    assert "--instance" in err
    assert refresh.calls() == []


def _value_flags(script):
    """Every option in a POSIX-sh option loop that consumes `$2`.

    Derived rather than listed, because the trap this guards is per-flag: a
    list written by hand covers the flags somebody remembered.
    """
    flags = []
    with open(script) as handle:
        for line in handle.read().splitlines():
            stripped = line.strip()
            if not stripped.startswith("--") or ") need $#" not in stripped:
                continue
            flags.append(stripped.split(")")[0])
    return flags


@pytest.mark.parametrize("flag", _value_flags(SCRIPT))
def test_an_empty_value_is_a_missing_value_for_every_flag(refresh, flag):
    """⚠️ `--config "$cfg"` with `$cfg` unset is ONE EMPTY ARGUMENT.

    A `need` that only counted arguments therefore saw a value where there was
    none, and every one of these flags has a default waiting: `$config` falls
    through to the plist's config or `$DEFAULT_CONFIG`, `$label` to
    `com.agbridge`, `$rows` to "beside the config". So the run stops the wrong
    job, forgets the wrong map, and prints output identical to the run that was
    meant -- the silent wrong-instance hazard the banner exists to make loud,
    arriving before the banner is composed.

    Every flag, not just `--config`: the rule belongs to `need`, and a fix
    applied to one flag leaves the same trap on the other seven. `agb`'s nine
    Python parsers have always refused both `--opt=` and `--opt ""`; this is
    the shell agreeing with them (invariant 14 -- neither can import the other).
    """
    rc, _out, err = refresh.run([flag, ""])
    assert rc != 0
    assert "%s needs a value" % (flag,) in err
    # Nothing ran at all: not the bootout, not the forget.
    assert refresh.calls() == []


# ---------------------------------------------------------------------------
# the sweep: a bare run acts on EVERY instance
# ---------------------------------------------------------------------------
#
# A bare `agb-refresh` used to act on the unnamed instance and report success in
# exactly the words it would have used for the one you meant. It now re-execs
# itself once per label, so every test below is about a PARENT that touches
# nothing and N children that do all the work -- which is also why the failure
# questions ("A failed, was B still swept?", "was A started again anyway?") are
# askable at all.
#
# ⚠️ WHAT THIS DID TO THE ~19 BARE RUNS ELSEWHERE IN THIS FILE, because "they
# all still pass" is the answer that would need checking rather than the one
# that settles it. The fixture installs exactly ONE instance (`com.agbridge`),
# so a bare run there is a sweep of one: the same child, doing the same three
# steps, with two lines of parent output around it. Every property those tests
# assert is a property of the CHILD -- the ordering of the three steps, which
# bridge the poll waits for, which config reaches `forget-rows` -- and none of
# them moved.
#
# What a sweep of one cannot show is that those properties survive a sweep of
# several, and that is this section's job rather than theirs. The two that were
# specifically re-reasoned carry a note of their own:
# `test_a_plain_refresh_ignores_a_named_instances_bridge` (whose multi-instance
# twin is `test_one_instances_live_bridge_does_not_hold_another_instances_wait`)
# and `test_the_ten_second_warning_names_what_was_actually_still_running`.


def _two_instances(refresh):
    """The default job and one named instance, both in the modern shape.

    The fixture ships a `<plist/>` for `com.agbridge` -- an install predating
    the `--config` flag -- and this replaces it with one that carries the flag,
    so that both children take the same path through the reader. `*.plist`
    expands in collating order, so `com.agbridge.hostb` is swept FIRST.
    """
    refresh.write_plist()
    refresh.write_plist("com.agbridge.hostb", instance="hostb")


def test_a_bare_run_sweeps_every_instance_in_order(refresh):
    """The headline: no flag, and every instance is repaired.

    All the Mac-side instances have the same standing -- any of them can be
    closed by hand, killed, or come back with its rows forgotten -- so the
    command that repairs that should not have to be told which. The default
    instance was never more than an artifact of install order.
    """
    _two_instances(refresh)
    rc, out, _err = refresh.run()
    assert rc == 0, out
    # Both banners, and in the order the directory lists them.
    assert "instance: hostb" in out, out
    assert "instance: (default)" in out, out
    assert out.index("instance: hostb") < out.index("instance: (default)"), out
    assert "swept:    2 instances" in out, out
    # Both were really bounced, over their OWN maps -- the banner is a claim,
    # and `--config` reaching `forget-rows` is what makes it true.
    joined = "\n".join(refresh.calls())
    assert "--config %s" % (refresh.config(),) in joined, refresh.calls()
    assert "--config %s" % (refresh.config("hostb"),) in joined, refresh.calls()
    # Exactly once each: a child that swept again would show up here as four
    # bootouts, which is the runaway the `--label` it is given rules out.
    assert refresh.count("launchctl bootout") == 2, refresh.calls()
    assert refresh.count("launchctl bootstrap") == 2, refresh.calls()


def test_a_custom_label_instance_is_not_reported_as_the_default_one(refresh):
    """⚠️ The banner is the whole mitigation for acting on the wrong instance,
    so it may not call a custom-label instance "(default)".

    `install.sh mac --label <anything>` puts no shape rule on a label
    (`install.sh:369`), so `weird.label` is a real install and the sweep is
    required to visit it. The name shown is read back out of the label, and the
    fall-through used to answer "(default)" for every label outside the
    `com.agbridge` space -- so a bare run reported TWO default instances, one of
    which was somebody's named machine, in the one line whose entire job is to
    say which instance moved.

    It needed the sweep to become reachable without a mistake: before it, the
    only way to land on such a label was to type `--label weird.label`, and the
    operator who typed it knew what they had asked for. Now the sweep types it,
    once per plist in the directory.
    """
    other = str(refresh.config("weird"))
    refresh.write_plist()                       # the real default instance
    refresh.write_plist("weird.label", config=other)
    rc, out, _err = refresh.run()
    assert rc == 0, out
    assert "swept:    2 instances" in out, out
    # Named by the only name it has, and NOT called the default one.
    assert "instance: weird.label -- label weird.label" in out, out
    assert out.count("instance: (default)") == 1, out
    # ...and the one "(default)" left is the job whose label really is that.
    assert "instance: (default) -- label com.agbridge" in out, out
    # Non-vacuity: both instances were really swept, over their own maps, so
    # the assertion above is about a banner that was printed for a real child
    # rather than about a sweep that skipped the custom label entirely.
    joined = "\n".join(refresh.calls())
    assert "--config %s" % (other,) in joined, refresh.calls()
    assert "--config %s" % (refresh.config(),) in joined, refresh.calls()
    assert refresh.count("launchctl bootout") == 2, refresh.calls()


def test_a_failing_instance_is_named_and_the_rest_are_still_swept(refresh):
    """One instance's failure may not cost the others their refresh.

    A sweep that stopped at the first failure would leave every instance after
    it un-refreshed -- and, worse, the failing one is exactly the instance whose
    bridge must still be started again, because a dark sidebar is what this
    command exists to cure and it must not cause one.
    """
    _two_instances(refresh)
    # hostb's `forget-rows` exits non-zero. That is what a real failure looks
    # like from here: the child's own output says what went wrong.
    rc, out, _err = refresh.run(forget_fail=refresh.config("hostb"))
    assert rc != 0, out
    assert "WARNING:  failed:" in out, out
    assert "com.agbridge.hostb(exit 1)" in out, out
    # The other instance was swept anyway...
    assert "instance: (default)" in out, out
    assert "--config %s" % (refresh.config(),) in "\n".join(refresh.calls())
    # ...and NOTHING was left stopped: the failing instance's job was started
    # again, which is the half a "stop at the first failure" sweep would lose.
    assert refresh.count("launchctl bootstrap") == 2, refresh.calls()
    assert "com.agbridge.hostb.plist" in refresh.call("launchctl bootstrap")


def test_an_instance_left_without_a_bridge_fails_the_sweep(refresh):
    """The rule that justifies bare-is-all: no instance may end down.

    A sweep that reported success while one instance's bridge never came back
    is the same silent failure as refreshing the wrong instance -- the rows are
    forgotten and there is nothing running to re-mint them, so that sidebar
    stays dark until somebody notices. `agb forget-rows` succeeded here, so the
    exit status is carrying a fact nothing else would have said.

    ⚠️ It is a SWEEP rule. A run that names one instance (`--instance hostb`
    against a Mac whose plist was never rendered) still warns and exits 0 --
    that is a documented, working recipe, and the two tests above
    `test_a_plist_that_is_not_there_at_all_still_falls_back_to_the_convention`
    are its guards.
    """
    _two_instances(refresh)
    # Both spellings of the restart fail, and only for hostb: the script falls
    # back to `load -w` when `bootstrap` fails.
    rc, out, _err = refresh.run(lc_fail="bootstrap load",
                                lc_fail_match="com.agbridge.hostb")
    assert rc != 0, out
    assert "could not start com.agbridge.hostb" in out, out
    assert "no bridge was started again for: com.agbridge.hostb" in out, out
    # The other instance was swept and IS up, so this is not "the sweep broke".
    assert "started:  com.agbridge" in out, out
    assert "swept:    2 instances" in out, out


def test_a_failed_bootout_is_not_a_failure_and_the_sweep_carries_on(refresh):
    """The stop phase is allowed to fail: "not running" is a fine state.

    `launchctl bootout` on a job that is already down is an error, and this is a
    recovery command -- refusing to run because the bridge was already stopped
    would be the opposite of helpful. So the phase that CAN fail harmlessly is
    the one that does not fail the sweep, while the phase that leaves the Mac
    worse off (the restart, above) does.
    """
    _two_instances(refresh)
    rc, out, _err = refresh.run(lc_fail="bootout",
                               lc_fail_match="com.agbridge.hostb")
    assert rc == 0, out
    assert "swept:    2 instances" in out, out
    # Non-vacuity: the bootout that failed really was attempted, and the rest of
    # that instance's refresh happened anyway.
    assert "com.agbridge.hostb" in refresh.call("launchctl bootout")
    assert "--config %s" % (refresh.config("hostb"),) \
        in "\n".join(refresh.calls())
    assert refresh.count("launchctl bootstrap") == 2, refresh.calls()


def test_no_instances_at_all_still_refreshes_the_default_map(refresh,
                                                             tmp_path):
    """⚠️ The regression this task was most able to cause.

    The commonest thing this command is run on is a Mac with no plist at all --
    agterm forgot its rows, `install.sh mac --no-load` was used, or the plist
    was never rendered -- and a bare `agb-refresh` there still forgets the
    default map and warns that nothing was restarted. That is the recipe in
    `SKILL.md`, and "no instances found" must not turn it into a refusal or into
    a run that sweeps nothing and reports success.
    """
    os.remove(str(refresh.agentsdir / "com.agbridge.plist"))
    rc, out, _err = refresh.run()
    assert rc == 0, out
    assert "no agbridge instance is installed" in out, out
    # It fell THROUGH to the single default run rather than exiting.
    assert "instance: (default)" in out, out
    assert "--config %s" % (refresh.config(),) in refresh.call("agb forget-rows")
    assert "the bridge was not restarted" in out, out
    # Non-vacuity: no sweep happened, so this is the fall-through and not a
    # one-instance sweep that happened to look the same.
    assert "swept:" not in out, out


def test_a_child_that_reads_stdin_does_not_eat_the_remaining_instances(
        refresh):
    """⚠️ The loop's own stdin IS the here-document holding the labels.

    A child that read one byte of it would consume the instances that had not
    been swept yet -- silently, and with the sweep reporting success for the
    ones it did reach. Nothing agb-refresh runs today reads stdin, which is
    exactly why this is worth pinning: the day one of them does, the symptom is
    "the last instance stopped being refreshed" and nothing points here.
    """
    _two_instances(refresh)
    rc, out, _err = refresh.run(eat_stdin=True)
    assert rc == 0, out
    assert "swept:    2 instances" in out, out
    assert refresh.count("launchctl bootout") == 2, refresh.calls()


def test_a_launch_agents_directory_that_cannot_be_listed_is_fatal(refresh,
                                                                  tmp_path):
    """⚠️ "I could not list them" is not "there are none" (invariant 12).

    Collapsing the two would make a Mac whose LaunchAgents directory is
    momentarily unreadable sweep NOTHING, fall through to the default job, and
    report success in the words it uses when it is right -- the wrong-instance
    hazard this whole command is built to make loud.

    A plain FILE where the directory should be is `ENOTDIR`, which is the
    "cannot list" side of the errno split (`ENOENT` is the ordinary Mac with no
    LaunchAgents yet, and is the test above). It is used rather than `chmod 000`
    because a suite running as root would defeat the permission version and pass
    vacuously.
    """
    notadir = tmp_path / "not-a-directory"
    notadir.write_text("")
    rc, _out, err = refresh.run(["--launch-agents", str(notadir)])
    assert rc != 0
    assert "cannot list the instances" in err, err
    # Nothing was touched: this is refused before the first bootout.
    assert refresh.calls() == []


# ---------------------------------------------------------------------------
# every flag is forwarded to the children, explicitly
# ---------------------------------------------------------------------------
#
# A child is a fresh process with fresh defaults, so a flag that is not passed
# on does not merely lose its effect -- it silently reverts to a default that
# looks like a working run. `--dry-run` is the one that matters most and comes
# first: a sweep that dropped it would perform a REAL refresh of every instance
# for a command that promised to change nothing.


def test_the_sweep_forwards_dry_run(refresh):
    """A dry run that bounced every bridge would be a dry run with N side
    effects -- and it is reached by exactly the people who are unsure."""
    _two_instances(refresh)
    rc, out, _err = refresh.run(["--dry-run"])
    assert rc == 0, out
    assert refresh.index("launchctl bootout") == -1, refresh.calls()
    assert refresh.index("launchctl bootstrap") == -1, refresh.calls()
    # Both instances were reported on, and both `forget-rows` calls carried the
    # flag rather than only the parent knowing about it.
    assert out.count("nothing was changed") == 2, out
    assert refresh.count("--dry-run") == 2, refresh.calls()


def test_the_sweep_forwards_no_close(refresh):
    """`--no-close` is the difference between leaving agterm's sessions open and
    closing them; a child that lost it would close rows the operator asked to
    keep, in a command whose whole subject is rows."""
    _two_instances(refresh)
    rc, _out, _err = refresh.run(["--no-close"])
    assert rc == 0
    assert refresh.count("--no-close") == 2, refresh.calls()


def test_the_sweep_forwards_the_agb_it_was_given(refresh, tmp_path):
    """⚠️ The easiest one to lose sight of, and the worst to lose.

    A child with no `--agb` falls back to `~/.local/lib/agbridge/agb`, which on
    a real Mac EXISTS -- so the sweep would quietly act through a different tree
    from the one the parent was told to use, with every line of output looking
    right.
    """
    _two_instances(refresh)
    rc, _out, _err = refresh.run()
    assert rc == 0
    # Every `forget-rows` really ran through the agb this run was given.
    assert refresh.count(str(tmp_path / "agb") + " forget-rows") == 2, \
        refresh.calls()


def test_the_sweep_forwards_the_python_it_was_given(refresh):
    """A child without `--python` resolves `python3` off `$PATH`.

    In this fixture that is the REAL interpreter, which runs the real `agb`
    against the fake tree and records nothing -- so the sweep would look like it
    worked while none of it was observable. On a Mac it is whichever python3 the
    operator's `$PATH` happens to name, which is the flag's whole reason.
    """
    _two_instances(refresh)
    rc, _out, _err = refresh.run()
    assert rc == 0
    assert refresh.count("agb forget-rows") == 2, refresh.calls()


def test_the_sweep_forwards_the_launch_agents_directory(refresh, tmp_path):
    """The parent lists that directory; the child reads a plist OUT of it.

    Dropped, the child looks in `~/Library/LaunchAgents` instead -- and in this
    fixture `$HOME` is the tmp tree, so the default very nearly works, which is
    exactly how a flag stops working while the suite stays green. Here the plist
    lives somewhere else entirely, so a child that ignored the flag would find
    no plist for the label it was handed.
    """
    alt = tmp_path / "Alt Agents"
    alt.mkdir()
    refresh.write_plist("com.agbridge.hostb", instance="hostb")
    (alt / "com.agbridge.hostb.plist").write_text(
        refresh.plist_text("com.agbridge.hostb"))
    # ⚠️ And REMOVED from the conventional directory, which is the whole
    # experiment: with a copy left in both, a child that ignored the flag would
    # find the plist anyway and this test passed against the forwarding being
    # dropped -- mutation-caught, exactly the "the default happens to land in
    # the right place" trap the docstring above is about.
    os.remove(str(refresh.agentsdir / "com.agbridge.hostb.plist"))
    rc, out, _err = refresh.run(["--launch-agents", str(alt)])
    assert rc == 0, out
    assert "instance: hostb" in out, out
    assert "started:  com.agbridge.hostb" in out, out
    assert "the bridge was not restarted" not in out, out
    # Non-vacuity: only the alternative directory was swept -- the conventional
    # one still holds `com.agbridge.plist`, which was NOT visited.
    assert "swept:    1 instance" in out, out
    assert (refresh.agentsdir / "com.agbridge.plist").exists()


# ---------------------------------------------------------------------------
# --key sweeps; --rows narrows
# ---------------------------------------------------------------------------


def test_a_key_is_looked_for_in_every_instance(refresh):
    """⚠️ `--key` sweeps, and that is what keeps the documented recipe working.

    `agb-refresh --key a3f9c1e0` is typed by someone reading a key out of a
    bridge log; nothing in that log says which instance minted it, and "you
    should not have to know which instance" is the whole thesis. A key belongs
    to exactly one map, so the run succeeds when any instance had it.
    """
    _two_instances(refresh)
    # hostb answers 1 -- exactly what `agb forget-rows` returns for a key that
    # was not in the map it opened.
    rc, out, _err = refresh.run(["--key", "a3f9c1e0"],
                                forget_fail=refresh.config("hostb"))
    assert rc == 0, out
    assert "keys:     forgotten by: com.agbridge" in out, out
    # It really was asked of both, with the key forwarded to each.
    assert refresh.count("--key a3f9c1e0") == 2, refresh.calls()


def test_a_key_no_instance_has_fails_the_sweep(refresh):
    """The other half, and it needs its own test: "found it somewhere" and
    "found it nowhere" must not be the same answer.

    A sweep that always exited 0 because some instance was bound to answer 0
    would make `--key` useless -- a mistyped key would read as a successful
    repair.
    """
    _two_instances(refresh)
    # Every `forget-rows` answers 1: no map held that key.
    rc, out, _err = refresh.run(["--key", "deadbeef"],
                                forget_fail="forget-rows")
    assert rc != 0, out
    assert "no instance had all of these keys: deadbeef" in out, out
    assert "keys:     forgotten by" not in out, out


def test_naming_a_rows_map_narrows_the_run_to_one_instance(refresh, tmp_path):
    """Naming a map IS naming what to act on, so `--rows` keeps today's
    semantics exactly: that map, and the config the default would have chosen.

    Sweeping it instead would hand ONE rows file to every instance in turn --
    each child forgetting another instance's bindings into it -- which is the
    cross-instance damage this plan exists to remove, arriving through the flag
    that was supposed to be the narrow one.
    """
    _two_instances(refresh)
    rows = tmp_path / "somewhere" / "rows"
    rc, out, _err = refresh.run(["--rows", str(rows)])
    assert rc == 0, out
    assert "sweep:" not in out, out
    assert "instance: (default)" in out, out
    assert refresh.count("launchctl bootout") == 1, refresh.calls()
    assert "com.agbridge.hostb" not in "\n".join(refresh.calls())


# ---------------------------------------------------------------------------
# how the sweep names this script again
# ---------------------------------------------------------------------------
#
# ⚠️ `$0` is whatever the caller typed. Everywhere else in this file the script
# is invoked by ABSOLUTE path, so `$0` always has a slash and the re-exec is
# free -- a harness simpler than reality, which is the shape this file has been
# bitten by before. The two spellings below both leave `$0` with no slash and
# need OPPOSITE resolutions.


@pytest.fixture
def sweep_self(tmp_path):
    """`agb-refresh`'s `sweep_self`, on one name, without forking the script.

    Extracted rather than restated, like the `plist_arg` fixture above: what
    ships is what is asked. Driven directly because the interesting inputs are
    the ones the whole script cannot be given -- a `$0` that resolves NOWHERE is
    a script that would not have started.

    Returns `(status, answer)`; status 1 is "this name cannot be resolved", and
    the caller turns it into a `die` before anything is stopped.
    """
    harness = tmp_path / "sweep_self.sh"
    harness.write_text(_extract_sh("sweep_self") + "\nsweep_self \"$1\"\n")

    def call(name, cwd=None, path=None):
        env = dict(os.environ)
        if path is not None:
            env["PATH"] = path
        proc = subprocess.Popen(["sh", str(harness), name],
                                cwd=str(cwd or tmp_path), env=env,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE)
        out, err = conftest.communicate(proc)
        assert err == b"", err
        return proc.returncode, out.decode()

    return call


def test_a_path_is_used_as_it_was_given(sweep_self):
    """Anything with a slash is already an answer -- absolute or relative, the
    child inherits the cwd, so it resolves to the same file."""
    assert sweep_self("/opt/agbridge/agb-refresh") == (
        0, "/opt/agbridge/agb-refresh")
    assert sweep_self("./tools/agb-refresh") == (0, "./tools/agb-refresh")


def test_a_bare_name_is_taken_from_the_current_directory_first(sweep_self,
                                                               tmp_path):
    """⚠️ The cwd before `$PATH`, because that is the order `sh <name>` used to
    find this script in the first place.

    With a copy in both places, resolving through `$PATH` would sweep with a
    DIFFERENT copy of the script than the one running -- a version skew nothing
    would report. Both exist here, so the answer says which won.
    """
    here = tmp_path / "here"
    here.mkdir()
    (here / "agb-refresh-copy").write_text("")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    other = elsewhere / "agb-refresh-copy"
    other.write_text("")
    os.chmod(str(other), 0o755)
    status, answer = sweep_self("agb-refresh-copy", cwd=here,
                                path=str(elsewhere) + os.pathsep
                                + os.environ.get("PATH", ""))
    assert (status, answer) == (0, "./agb-refresh-copy")


def test_a_bare_name_that_is_not_here_is_looked_for_on_the_path(sweep_self,
                                                                tmp_path):
    """The installed spelling: `agb-refresh` typed at a prompt, resolved by the
    caller's own shell through `$PATH`, run from wherever they were standing."""
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    target = elsewhere / "agb-refresh-copy"
    target.write_text("")
    os.chmod(str(target), 0o755)
    empty = tmp_path / "empty"
    empty.mkdir()
    status, answer = sweep_self("agb-refresh-copy", cwd=empty,
                                path=str(elsewhere) + os.pathsep
                                + os.environ.get("PATH", ""))
    assert (status, answer) == (0, str(target))


def test_a_name_that_resolves_nowhere_is_refused_rather_than_guessed(
        sweep_self, tmp_path):
    """⚠️ One loud refusal, before anything is stopped.

    Answering the raw name anyway would push the failure into the loop, where it
    arrives as `sh: can't open agb-refresh` once per instance -- after the first
    job has already been booted out and its rows forgotten.
    """
    empty = tmp_path / "empty-too"
    empty.mkdir()
    # The inherited `$PATH` (which has to stay: `sh` itself is found through
    # it), and a name nothing on it could answer.
    status, answer = sweep_self("agb-refresh-copy", cwd=empty)
    assert status == 1
    assert answer == ""


def _named_copy(refresh, where, name="agb-refresh-copy"):
    """A copy of the script under a name nothing else could resolve.

    Deliberately not `agb-refresh`: a developer with agbridge installed has one
    on `$PATH`, and the cwd test would then silently exercise the `$PATH` branch
    (or, worse, sweep through an older installed script).
    """
    target = where / name
    with open(SCRIPT) as handle:
        target.write_text(handle.read())
    os.chmod(str(target), 0o755)
    return target


def test_the_sweep_finds_this_script_again_when_it_was_run_from_the_cwd(
        refresh, tmp_path):
    """`sh agb-refresh` typed in the script's own directory.

    `$0` is a bare name that is NOT on `$PATH`, so the re-exec has to fall back
    to the current directory -- the child inherits the cwd, so it resolves the
    same file. Without this, a sweep started that way dies at the first child.
    """
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    _named_copy(refresh, checkout)
    _two_instances(refresh)
    rc, out, _err = refresh.run(cwd=str(checkout), script="agb-refresh-copy")
    assert rc == 0, out
    assert "swept:    2 instances" in out, out
    # Non-vacuity: the name really was resolvable only through the cwd.
    assert not (tmp_path / "agb-refresh-copy").exists()


def test_the_sweep_finds_this_script_again_when_it_was_run_from_the_path(
        refresh, tmp_path):
    """`agb-refresh` found on `$PATH` -- the installed spelling.

    `$0` is a bare name again, and this time the cwd has no such file: `$PATH`
    is how the caller's own shell resolved it and is how the sweep must.

    The source tries `$PATH` BEFORE the cwd, because `./` would otherwise run
    whatever happens to be sitting in the directory the operator is standing in.
    That order is a decision recorded there rather than a property this test can
    pin: with a copy in both places, which file the PARENT itself is becomes
    ambiguous too (`sh <name>` tries the cwd first and `$PATH` second), so the
    experiment cannot be set up.
    """
    _named_copy(refresh, refresh.bindir)
    _two_instances(refresh)
    rc, out, _err = refresh.run(script="agb-refresh-copy")
    assert rc == 0, out
    assert "swept:    2 instances" in out, out
    # Non-vacuity: the cwd (which `run` sets to the tmp tree) holds no copy, so
    # only the `$PATH` lookup can have answered.
    assert not (tmp_path / "agb-refresh-copy").exists()


# ---------------------------------------------------------------------------
# the interrupt, and whose bridge it leaves down
# ---------------------------------------------------------------------------


def test_an_interrupted_child_starts_its_own_bridge_again(refresh):
    """⚠️ Ctrl-C between the bootout and the restart is the one way this command
    causes the dark sidebar it exists to cure.

    The trap has to live in the CHILD, because the child is the process that
    stopped a job: the sweeping parent boots nothing out, so a trap there could
    not repair anything, and one in both would bootstrap the same job twice.

    The signal is delivered from inside the fake `agb forget-rows` -- step 2,
    which runs squarely inside that window -- because sending it from the test
    would be a race with the child's own progress.
    """
    _two_instances(refresh)
    rc, out, _err = refresh.run(interrupt=refresh.config("hostb"))
    assert rc != 0, out
    assert "interrupted (SIGINT)" in out, out
    # Its own job was started again before it exited: nothing left stopped.
    assert "com.agbridge.hostb.plist" in refresh.call("launchctl bootstrap")
    # ...and the sweep stopped there rather than bouncing the instances the
    # operator interrupted it to protect.
    assert "was interrupted, so the sweep stopped" in out, out
    assert "NOT visited: com.agbridge" in out, out
    assert refresh.count("launchctl bootout") == 1, refresh.calls()
    assert refresh.count("launchctl bootstrap") == 1, refresh.calls()


def test_one_instances_live_bridge_does_not_hold_another_instances_wait(
        refresh):
    """⚠️ A 10 s wait PER INSTANCE, on the commonest invocation there is.

    `cmdline_is_ours` treats a bridge it cannot attribute as ours -- rightly, an
    under-match is `forget-rows` landing under a live bridge -- so a sweep is
    the run where that generosity could compound: N instances, each waiting out
    the full bound for a bridge belonging to one of the others, every one of
    them ending in a warning that the forget may have been undone.

    Only hostb's bridge is up here, and it never dies, so hostb's own wait is
    the positive control: exactly one instance may wait, and it is that one.
    """
    _two_instances(refresh)
    rc, out, _err = refresh.run(alive_polls=10 ** 6,
                                alive_cmdline=refresh.cmdline("hostb"))
    assert rc == 0, out
    assert out.count("still running") == 1, out
    assert "com.agbridge.hostb is still running" in out, out
    assert "com.agbridge is still running" not in out, out
    # Non-vacuity: the poll ran for both children, so the default instance's run
    # really did ask and really did decide that bridge was not its own.
    assert refresh.count("pgrep") > 1, refresh.calls()
    assert "swept:    2 instances" in out, out


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
