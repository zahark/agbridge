"""Task 4b -- `agb bridge`: row binding and rendering, plus `agb close-done`.

This is the half of the bridge that needs a Mac, tested on a farm box that has
none. Three seams carry it, and each is load-bearing:

* a **recording `run`** injected into `RowRenderer`, so the ops -> `agtermctl`
  mapping is a list comparison rather than an integration test;
* `tests/stubs/agtermctl`, a stub whose job is to **fail loudly** on anything
  the recorded contract forbids -- a permissive stub would validate a fiction
  such as `unknown` and the suite would go green while the bridge emitted a
  status agterm rejects;
* `--rows <path>`, so no test ever touches the developer's real row map.

The assertions that matter most are again about what does *not* happen: a row is
never created twice, a bound row is never rebound, `remove` never closes a row,
`--auto-reset` is never passed, no status outside the four-word vocabulary is
ever emitted, and no age anywhere is computed in the Mac's own clock.
"""

import ast
import fcntl
import json
import os
import threading
import time

import pytest

import conftest


# The feed's clock. Every age below is a subtraction inside *this* domain: the
# wire's `now` is the mtime the feed read back from the bridge beat it wrote,
# and `beat` is `.state`'s server-stamped mtime (constraint #12).
NOW = 1753716100.0
FRESH = NOW - 5.0            # beating normally
LATE = NOW - 20 * 60         # twenty minutes: a blocked agent on a quiet host

HOST = "box2"


def wire(key, state="active", seq=1, beat=FRESH, **extra):
    record = {
        "v": 1, "key": key, "label": "build", "host": HOST,
        "pid": 48213, "starttime": 9182736, "tmux": "build", "pane": "%24",
        "cwd": "/shared/work/project", "state": state, "seq": seq,
        "updated": 1753716123.4, "beat": beat,
    }
    record.update(extra)
    return record


# ---------------------------------------------------------------------------
# seams
# ---------------------------------------------------------------------------

class Runner(object):
    """A recording stand-in for `_run_command`: (rc, stdout, stderr).

    Returning data rather than raising is the contract `_run_command` itself
    keeps, which is what makes "an `agtermctl` failure must not wedge the
    bridge" a property of the renderer rather than of luck.
    """

    def __init__(self, ids=None, fail=(), err="induced failure"):
        self.calls = []
        self.fail = set(fail)
        # What a failing call writes to stderr. Defaults to something obviously
        # synthetic: a test that wants agterm's real "no such session" answer
        # has to ask for it, so the narrow match cannot be satisfied by accident.
        self.err = err
        self.ids = None if ids is None else list(ids)
        self.n = 0

    def __call__(self, argv, timeout=None):
        self.calls.append(list(argv))
        if argv[0] != "agtermctl":
            return (0, "", "")                       # osascript, etc.
        verb = argv[2] if len(argv) > 2 else ""
        if "all" in self.fail or verb in self.fail:
            return (1, "", self.err)
        if verb == "new":
            if self.ids is not None:
                out = self.ids.pop(0) if self.ids else ""
            else:
                self.n += 1
                out = "0E3D894C-7C14-4C45-83FF-%012d" % (self.n,)
            return (0, out + "\n", "")
        return (0, "", "")

    # -- views over the recording ----------------------------------------

    def agterm(self):
        return [call for call in self.calls if call[0] == "agtermctl"]

    def verbs(self):
        return [call[2] for call in self.agterm()]

    def news(self):
        return [_options(call) for call in self.agterm() if call[2] == "new"]

    def renames(self):
        return [(call[3], _options(call).get("--target"))
                for call in self.agterm() if call[2] == "rename"]

    def statuses(self):
        out = []
        for call in self.agterm():
            if call[2] != "status":
                continue
            out.append((call[3], _options(call).get("--target"),
                        "--blink" in call))
        return out

    def titles(self):
        return [title for title, _target in self.renames()]

    def others(self):
        return [call for call in self.calls if call[0] != "agtermctl"]


def _options(argv):
    """`--flag value` pairs out of a recorded argv."""
    opts = {}
    for index, word in enumerate(argv):
        if word.startswith("--"):
            value = argv[index + 1] if index + 1 < len(argv) else None
            opts[word] = None if (value or "").startswith("--") else value
    return opts


class Harness(object):
    """A model + a row map + a renderer, driven by wire events."""

    def __init__(self, mac, path, runner=None, settings=None):
        self.mac = mac
        self.path = str(path)
        self.model = mac.BridgeModel()
        self.rows = mac.load_rows(self.path)
        self.run = runner if runner is not None else Runner()
        self.warned = []
        self.renderer = mac.RowRenderer(self.model, self.rows, run=self.run,
                                        warn=self.warned.append,
                                        settings=settings or {})
        self.model.adopt(self.rows.bound_keys())

    def send(self, kind, now=NOW, **fields):
        event = {"t": kind, "now": now}
        event.update(fields)
        self.renderer(self.model.apply(event))
        return self

    def upsert(self, session, now=NOW):
        return self.send("upsert", now=now, session=session)

    def remove(self, key, now=NOW):
        return self.send("remove", now=now, key=key)

    def snapshot(self, sessions, now=NOW):
        return self.send("snapshot", now=now, sessions=sessions)

    def tick(self, now=NOW):
        return self.send("tick", now=now)

    def stale(self, reason="eof"):
        self.renderer(self.model.mark_stale(reason))
        return self


@pytest.fixture
def rows_file(tmp_path):
    return tmp_path / "rows"


@pytest.fixture
def bridge(mac, rows_file):
    def build(runner=None, settings=None, path=None):
        return Harness(mac, path or rows_file, runner, settings)
    return build


# ---------------------------------------------------------------------------
# the row map: a bijection, persisted
# ---------------------------------------------------------------------------

def test_a_row_is_created_exactly_once_per_key(bridge):
    """Every repaint after the first reuses the bound row. `agr` minted a fresh
    target whenever its mapping went stale, which is how one agent ended up
    owning several rows."""
    b = bridge()
    for seq in (1, 2, 3):
        b.upsert(wire("aaaa1111", state="blocked", seq=seq))
    assert b.run.verbs().count("new") == 1
    assert b.rows.row_for("aaaa1111")


def test_two_keys_get_two_rows(bridge):
    b = bridge()
    b.upsert(wire("aaaa1111"))
    b.upsert(wire("bbbb2222"))
    rows = set([b.rows.row_for("aaaa1111"), b.rows.row_for("bbbb2222")])
    assert len(rows) == 2 and None not in rows


def test_a_second_key_cannot_bind_a_bound_row(bridge, mac):
    """The bijection invariant, driven by the one thing that could break it: an
    `agtermctl` that hands out an id it has already used. Binding it anyway
    would put two agents on one row -- `agr` failure mode #3 exactly."""
    b = bridge(Runner(ids=["ROW-1", "ROW-1"]))
    b.upsert(wire("aaaa1111"))
    b.upsert(wire("bbbb2222"))
    assert b.rows.row_for("aaaa1111") == "ROW-1"
    assert b.rows.row_for("bbbb2222") is None
    assert any("already bound" in text for text in b.warned)


def test_the_map_refuses_to_rebind_a_key(mac, agb):
    rows = mac.RowMap()
    rows.bind("aaaa1111", "ROW-1")
    with pytest.raises(agb.AgbError):
        rows.bind("aaaa1111", "ROW-2")


def test_the_map_refuses_to_move_a_buried_key_to_a_different_row(mac, agb):
    """`bind` still refuses a key it has seen, `[done]` or not: keys are minted
    and never reused (Task 2a), so binding one to a *second* row would orphan
    the first. Returning it to its own row is `rebind`, tested below."""
    rows = mac.RowMap()
    rows.bind("aaaa1111", "ROW-1")
    rows.unbind("aaaa1111")
    with pytest.raises(agb.AgbError):
        rows.bind("aaaa1111", "ROW-2")


def test_rebind_returns_a_done_row_to_its_own_key(mac):
    rows = mac.RowMap()
    rows.bind("aaaa1111", "ROW-1")
    rows.unbind("aaaa1111")
    assert rows.row_for("aaaa1111") is None
    assert rows.rebind("aaaa1111") == "ROW-1"
    assert rows.row_for("aaaa1111") == "ROW-1"


def test_rebind_refuses_a_key_that_is_unknown_or_already_bound(mac):
    """It is the counterpart to `unbind`, not a way around `bind`'s bijection."""
    rows = mac.RowMap()
    rows.bind("aaaa1111", "ROW-1")
    assert rows.rebind("aaaa1111") is None       # already bound
    assert rows.rebind("bbbb2222") is None       # never seen


def test_a_done_key_the_feed_re_asserts_gets_its_row_back(bridge):
    """`[done]` was never proof the agent finished -- it is what a `remove`
    renders, and a `remove` also comes from an incomplete snapshot and from
    `agb prune`, which the tool documents as expected to hit live agents. A
    positive upsert outranks that earlier absence (constraint #8 on the Mac
    side); refusing it left a live `active` agent idle + `[done]` for ever."""
    b = bridge()
    b.upsert(wire("aaaa1111"))
    row = b.rows.row_for("aaaa1111")
    b.remove("aaaa1111")
    before = len(b.run.calls)
    b.upsert(wire("aaaa1111", seq=9))
    assert b.rows.row_for("aaaa1111") == row          # the same row, not a new one
    assert b.run.verbs().count("new") == 1
    renamed = [title for title, target in b.run.renames() if target == row]
    assert not renamed[-1].startswith("[done] ")
    assert [state for state, _r, _b in b.run.statuses()][-1] == "active"
    assert b.run.calls[before:] != []


def test_the_map_round_trips(mac):
    rows = mac.RowMap()
    rows.bind("aaaa1111", "0E3D894C-7C14-4C45-83FF-5F633A17EE74", "build · a")
    rows.bind("bbbb2222", "ROW-2")
    rows.unbind("bbbb2222")
    parsed = mac.parse_rows(rows.serialize())
    assert parsed == [("bound", "aaaa1111",
                       "0E3D894C-7C14-4C45-83FF-5F633A17EE74", "build · a"),
                      ("done", "bbbb2222", "ROW-2", "")]


def test_a_version_1_map_still_loads_and_is_rewritten_as_version_2(mac,
                                                                   rows_file):
    """The fourth field is a compatible bump: an old file parses with an empty
    title rather than being discarded, which would unbind every row at once."""
    rows_file.write_text("agbridge-rows 1\nbound\taaaa1111\tROW-1\n#end 1\n")
    rows = mac.load_rows(str(rows_file))
    assert rows.row_for("aaaa1111") == "ROW-1"
    assert rows.title_for("aaaa1111") == ""
    rows.set_title("aaaa1111", "build · box2")
    rows.save()
    assert rows_file.read_text().startswith("agbridge-rows 2\n")
    assert mac.load_rows(str(rows_file)).title_for("aaaa1111") == "build · box2"


def test_a_title_can_never_break_the_line_format(mac):
    """A title is free text from the farm. One tab in it would split a line into
    five fields and make the WHOLE map unparseable -- every row, not one."""
    rows = mac.RowMap()
    rows.bind("aaaa1111", "ROW-1", "a\tb\nc" + "x" * 900)
    parsed = mac.parse_rows(rows.serialize())
    assert parsed is not None
    assert len(parsed) == 1
    assert "\t" not in parsed[0][3] and "\n" not in parsed[0][3]
    assert len(parsed[0][3]) <= mac.ROW_TITLE_MAX


def test_the_map_survives_a_bridge_restart(bridge, rows_file):
    """The bridge is a launchd job: it restarts. An in-memory map would mint a
    second row for every live agent every time."""
    first = bridge()
    first.upsert(wire("aaaa1111"))
    row = first.rows.row_for("aaaa1111")
    assert os.path.exists(str(rows_file))

    second = bridge()
    assert second.rows.row_for("aaaa1111") == row
    second.upsert(wire("aaaa1111", state="blocked", seq=2))
    assert second.run.verbs().count("new") == 0


@pytest.mark.parametrize("text", [
    "",                                                   # empty
    "agbridge-rows 1\n",                                  # no sentinel
    "agbridge-rows 1\nbound\taaaa1111\tROW-1\n",           # truncated mid-write
    "agbridge-rows 1\nbound\taaaa1111\tROW-1\n#end 2\n",   # count disagrees
    "agbridge-rows 9\nbound\taaaa1111\tROW-1\n#end 1\n",   # another version
    "agbridge-rows 1\nbound\tnothex\tROW-1\n#end 1\n",     # not a session key
    "agbridge-rows 1\nbound\taaaa1111\t\n#end 1\n",        # no row id
    "agbridge-rows 1\nbound\taaaa1111\tROW-1\n"
    "bound\tbbbb2222\tROW-1\n#end 2\n",                    # not a bijection
])
def test_an_unreadable_map_is_discarded_never_half_believed(mac, text):
    assert mac.parse_rows(text) is None


def test_a_discarded_map_is_reported_rather_than_silently_forgotten(mac,
                                                                    rows_file):
    """Losing the map costs rows that `close-done` can no longer reclaim, so it
    is exactly the kind of degradation this project refuses to let pass
    quietly."""
    with open(str(rows_file), "w") as handle:
        handle.write("agbridge-rows 1\nbound\taaaa1111\tROW-1\n")
    warned = []
    rows = mac.load_rows(str(rows_file), warned.append)
    assert rows.bound_keys() == []
    assert any("unreadable" in text for text in warned)


def test_a_missing_map_is_not_a_warning(mac, rows_file):
    warned = []
    rows = mac.load_rows(str(rows_file), warned.append)
    assert (rows.bound_keys(), warned) == ([], [])


def test_a_row_closed_by_close_done_is_not_resurrected_by_the_bridge(mac,
                                                                     rows_file):
    """`close-done` is a separate process by design, so two processes write this
    file (each read-modify-write under `_rows_lock`, the merge included). A
    bridge that rewrote its own in-memory copy over the top would bring back
    every entry `close-done` had just closed -- and then try to close an
    already-closed row forever."""
    running = mac.load_rows(str(rows_file))
    running.bind("aaaa1111", "ROW-1")
    running.bind("bbbb2222", "ROW-2")
    running.unbind("bbbb2222")
    running.save()

    reclaimer = mac.load_rows(str(rows_file))
    assert reclaimer.done_entries() == [("bbbb2222", "ROW-2")]
    reclaimer.forget("bbbb2222")
    reclaimer.save()

    running.bind("cccc3333", "ROW-3")          # the bridge carries on
    running.save()
    assert sorted(mac.load_rows(str(rows_file)).entries) == ["aaaa1111",
                                                             "cccc3333"]


def test_a_bind_made_by_another_process_is_not_lost(mac, rows_file):
    """The mirror image: whoever saves last must not drop what the other one
    added while it was holding its own copy."""
    first = mac.load_rows(str(rows_file))
    first.bind("aaaa1111", "ROW-1")
    first.save()

    second = mac.load_rows(str(rows_file))
    first.bind("bbbb2222", "ROW-2")            # first process, still running
    first.save()
    second.bind("cccc3333", "ROW-3")
    second.save()

    assert sorted(mac.load_rows(str(rows_file)).entries) == [
        "aaaa1111", "bbbb2222", "cccc3333"]


def test_an_entry_this_process_never_changed_defers_to_the_disk_copy(mac,
                                                                      rows_file):
    """Holding a copy is not the same as having an opinion.

    The lock makes one process's merge-and-write atomic; it does nothing about
    the *time between reads*, and `close-done` reads once and then spends an
    `agtermctl session close` (seconds) per row. Every `[done]` the bridge
    records in that window is an entry the reclaimer holds as `bound` and never
    touched -- so the disk's is the newer one and must win."""
    running = mac.load_rows(str(rows_file))
    running.bind("aaaa1111", "ROW-1")
    running.bind("bbbb2222", "ROW-2")
    running.save()

    reclaimer = mac.load_rows(str(rows_file))    # reads, then works for a while
    running.unbind("bbbb2222")                   # the bridge, meanwhile
    running.save()

    reclaimer.set_title("aaaa1111", "whatever")  # something to save
    reclaimer.save()
    assert mac.load_rows(str(rows_file)).done_entries() == [("bbbb2222",
                                                             "ROW-2")]


def test_an_entry_this_process_did_change_still_wins_over_the_disk_copy(
        mac, rows_file):
    """The other half, and the reason `touched` exists rather than "disk always
    wins": an edit this process has not managed to save yet is newer than
    anything on disk."""
    running = mac.load_rows(str(rows_file))
    running.bind("aaaa1111", "ROW-1")
    running.save()

    stale = mac.load_rows(str(rows_file))
    running.unbind("aaaa1111")                   # someone else's older opinion
    running.save()

    stale.set_title("aaaa1111", "still here")    # OUR edit, unsaved
    stale.rebind("aaaa1111")
    stale.save()
    after = mac.load_rows(str(rows_file))
    assert (after.bound_keys(), after.title_for("aaaa1111")) == (["aaaa1111"],
                                                                 "still here")


def test_a_saved_edit_stops_being_this_processs_unsaved_opinion(mac,
                                                                 rows_file):
    """`touched` is the *unsaved* half of the map, so a write that lands empties
    it. Two reasons, and each alone is enough: after the write the disk copy IS
    ours, so a later disagreement is genuinely the other process's -- and the
    bridge is a launchd-resident process that binds every key it ever sees, so
    a set that only grows is one more per-key leak with no reclamation path."""
    rows = mac.load_rows(str(rows_file))
    rows.bind("aaaa1111", "ROW-1")
    assert rows.touched == set(["aaaa1111"])
    rows.save()
    assert rows.touched == set()


def test_the_map_is_not_written_when_nothing_changed(mac, rows_file):
    rows = mac.load_rows(str(rows_file))
    assert rows.save() is False
    assert not os.path.exists(str(rows_file))


# ---------------------------------------------------------------------------
# the row command -- the only thing connecting Task 7 to the system
# ---------------------------------------------------------------------------

def test_the_row_command_is_the_agb_pane_invocation(mac):
    assert mac.pane_argv(wire("aaaa1111"), agb_path="/opt/agb/agb",
                         python="/usr/bin/python3") == [
        "/usr/bin/python3", "-S", "-E", "/opt/agb/agb",
        "pane", "aaaa1111", "--host", HOST, "--tmux", "build",
        "--cwd", "/shared/work/project", "--pane", "%24"]


def test_the_row_command_names_an_interpreter_because_agb_has_no_shebang(mac,
                                                                         agb):
    """Constraint #1 on the Mac: `agb` is deliberately not executable and has no
    shebang, so a row command of `agb pane …` would simply not run."""
    argv = mac.pane_argv(wire("aaaa1111"))
    assert argv[1:3] == ["-S", "-E"]
    assert os.path.basename(argv[3]) == "agb"
    assert not os.access(argv[3], os.X_OK)


def test_the_row_command_carries_the_pane(mac):
    """Two agents in two panes of one tmux session share label, host, cwd and
    tmux. Without `--pane` the second is unreachable from its own row."""
    argv = mac.pane_argv(wire("aaaa1111", pane="%31"))
    assert argv[argv.index("--pane") + 1] == "%31"


def test_a_session_with_no_tmux_target_gets_no_tmux_arguments(mac):
    """The non-tmux (plain ssh, machine #3) tier: `tmux` and `pane` are both
    null on the wire, and Task 7's degraded path is "print identity, do not
    attach" -- which it can only take if the row command does not claim a
    target that does not exist."""
    argv = mac.pane_argv(wire("aaaa1111", tmux=None, pane=None))
    assert "--tmux" not in argv and "--pane" not in argv


def test_the_jump_hint_is_only_added_for_a_host_the_feed_is_not_on(mac):
    settings = {"jump_host": "vncbox", "feed_host": "user@vncbox.example.com"}
    assert mac.jump_for(wire("aaaa1111", host="vncbox"), settings) is None
    assert mac.jump_for(wire("aaaa1111", host="machine3"), settings) == "vncbox"
    assert mac.jump_for(wire("aaaa1111", host="machine3"), {}) is None


def test_the_row_command_is_quoted_for_the_shell_that_runs_it(mac):
    command = mac.pane_command(wire("aaaa1111", tmux="my session"),
                               agb_path="/opt/agb/agb", python="/usr/bin/python3")
    assert "'my session'" in command
    assert command.startswith("/usr/bin/python3 -S -E /opt/agb/agb pane ")


def test_the_created_row_is_given_the_command_the_cwd_and_the_title(bridge):
    b = bridge(settings={"agb_path": "/opt/agb/agb", "python": "/py"})
    b.upsert(wire("aaaa1111"))
    created = b.run.news()[0]
    assert created["--cwd"] == "/shared/work/project"
    assert created["--command"].startswith("/py -S -E /opt/agb/agb pane aaaa1111")
    assert created["--name"].startswith("build")


# ---------------------------------------------------------------------------
# the row command carries the instance's config
# ---------------------------------------------------------------------------
#
# Clicking a row runs `agb pane`, which resolves `--host` through its OWN config
# read on the far side of the click. With two bridges on one Mac, a row command
# that does not name its config resolves against the DEFAULT `host_<name>`
# table: the ssh reaches the wrong machine, or nowhere, while every test in this
# file passes -- nothing here performs that read. So the flag is asserted at
# both ends, and the renderer end is the one that matters.

def test_the_row_command_carries_a_non_default_config(mac, tmp_path):
    path = str(tmp_path / "hostb" / "config")
    argv = mac.pane_argv(wire("aaaa1111"), agb_path="/a/agb", python="/py",
                         config=path)
    assert argv[argv.index("--config") + 1] == path


def test_a_default_install_mints_the_command_it_always_did(mac, agb):
    """Three spellings of "this is the default config", all of which must leave
    the command byte-identical.

    `None` is the one an implementer drops: it is the parameter's default and
    what `settings.get("config")` returns for every bridge started without the
    flag, and `None != agb.config_path()` is True -- so a predicate missing its
    `config and` half puts the literal string `None` on every row command on
    every Mac. `normpath` covers the third: `install.sh` renders the path
    independently, and a `$HOME` with a trailing slash would otherwise have
    every default install re-mint every row command it already has.
    """
    base = mac.pane_argv(wire("aaaa1111"), agb_path="/a/agb", python="/py")
    assert "--config" not in base
    same_file = os.path.join(os.path.dirname(agb.config_path()), ".", "config")
    for spelling in (None, agb.config_path(), same_file):
        assert mac.pane_argv(wire("aaaa1111"), agb_path="/a/agb",
                             python="/py", config=spelling) == base


def test_the_minted_row_command_carries_the_instances_config(mac, tmp_path):
    """The whole link -- `render_settings` -> `RowRenderer` -> `pane_command` --
    which is why this goes through `bridge_sink` and not the `bridge` fixture.

    That fixture hands `RowRenderer` a settings dict written by hand and never
    calls `render_settings`, so it could only ever prove that the renderer
    passes on what it was given. The seam that breaks silently is the published
    `config` key and the call site that reads it, and neither exists on the
    fixture's path.
    """
    home = tmp_path / "hostb"
    os.makedirs(str(home))
    path = str(home / "config")
    with open(path, "w") as handle:
        handle.write("")
    runner = Runner()
    model = mac.BridgeModel()
    sink, renderer = mac.bridge_sink(model, {"config": path}, run=runner)
    assert renderer is not None
    sink(model.apply({"t": "upsert", "now": NOW, "session": wire("aaaa1111")}))
    created = runner.news()[0]
    # Not `startswith`: an omission is at the END of the command, so a prefix
    # assertion is green either way.
    assert " --config %s" % (path,) in created["--command"]


# ---------------------------------------------------------------------------
# the two acceptance criteria, each as ONE test rather than a chain of links
# ---------------------------------------------------------------------------
#
# Everything above tests a single hop. Both failures this feature exists to
# prevent live *between* hops, and a chain of individually-green links is
# exactly what the silent version of each looks like: every unit test passes
# while the row on screen reaches the wrong machine. So these two drive the
# whole path -- `bridge` argv in, `agtermctl` calls and an ssh target out --
# and each carries the other instance as its control, because an isolated
# instance and a shared one are indistinguishable unless the two answers differ.


def test_two_instances_side_by_side_paint_only_their_own_rows(
        mac, config_file, instance_config):
    """Acceptance: two bridges run side by side, rows from both appear, and
    each updates from its own machine.

    Driven as two independent bridges because that is what they are: two
    models, two sinks, two recording `agtermctl`s, one per ssh. What must not
    exist between them is any shared mutable state -- one bijection would put
    both farms' agents in one map, where the first `close-done` closes the
    other's rows.
    """
    config_file("workspace = farm-a\n")                  # the default instance
    hostb = instance_config("hostb")
    with open(hostb, "w") as handle:
        handle.write("workspace = farm-b\n")

    run_a, run_b = Runner(ids=["ROW-A1"]), Runner(ids=["ROW-B1"])
    model_a, model_b = mac.BridgeModel(), mac.BridgeModel()
    sink_a, _ren_a = mac.bridge_sink(model_a, mac.parse_bridge_args([]),
                                     run=run_a)
    sink_b, _ren_b = mac.bridge_sink(
        model_b, mac.parse_bridge_args(["--config", hostb]), run=run_b)

    sink_a(model_a.apply({"t": "upsert", "now": NOW,
                          "session": wire("aaaa1111", host="box-a")}))
    sink_b(model_b.apply({"t": "upsert", "now": NOW,
                          "session": wire("bbbb2222", host="box-b")}))

    # Rows from both appeared, one each, and each was born in its own config's
    # workspace -- which is the assertion that the two sinks read two files.
    assert run_a.verbs().count("new") == 1
    assert run_b.verbs().count("new") == 1
    assert run_a.news()[0]["--workspace-name"] == "farm-a"
    assert run_b.news()[0]["--workspace-name"] == "farm-b"

    # Each updates from its own machine: B's agent blocks, B's row is repainted
    # and A's is not touched at all.
    sink_b(model_b.apply({"t": "upsert", "now": NOW,
                          "session": wire("bbbb2222", host="box-b",
                                          state="blocked", seq=2)}))
    assert ("blocked", "ROW-B1") in [(state, target)
                                     for state, target, _blink
                                     in run_b.statuses()]
    assert "ROW-B1" not in [target for _s, target, _b in run_a.statuses()]
    assert "blocked" not in [state for state, _t, _b in run_a.statuses()]

    # ...and the bijections are two files on disk, each holding one key.
    assert mac.load_rows(mac.rows_path()).bound_keys() == ["aaaa1111"]
    assert mac.load_rows(mac.rows_path(hostb)).bound_keys() == ["bbbb2222"]
    assert mac.rows_path() != mac.rows_path(hostb)


def test_a_click_on_an_instances_row_reaches_that_instances_host(
        mac, ops, config_file, instance_config):
    """⚠️ Acceptance, and the failure three review passes were aimed at:
    clicking a row on instance B must reach **B's** host, resolved from B's
    own `host_<name>` table.

    Every hop of this is tested on its own elsewhere, and that is precisely the
    problem: the bug is that the hops do not meet. So this one starts at the
    `bridge` argv, takes the command agterm was *actually handed*, and runs it
    back through the parser and the resolver `agb pane` uses on the far side of
    a click. The same host name maps to a different target in each config, so a
    shared read cannot look like an isolated one.
    """
    import shlex

    config_file("host_box2 = user@instance-a.example\n")
    hostb = instance_config("hostb")
    with open(hostb, "w") as handle:
        handle.write("host_box2 = user@instance-b.example\n")

    def minted(argv):
        runner = Runner()
        model = mac.BridgeModel()
        sink, renderer = mac.bridge_sink(model, mac.parse_bridge_args(argv),
                                         run=runner)
        assert renderer is not None
        sink(model.apply({"t": "upsert", "now": NOW,
                          "session": wire("aaaa1111")}))
        return shlex.split(runner.news()[0]["--command"])

    command = minted(["--config", hostb])
    assert command[4] == "pane"                  # the shape the parser expects
    target, _jump = ops.pane_settings(ops.parse_pane_args(command[5:]))
    assert target == "user@instance-b.example"

    # The control, and the non-vacuity check for the line above: the default
    # instance's identical row resolves to the OTHER machine, and its command
    # carries no `--config` at all -- so neither answer can be the one the
    # resolver would give by ignoring the flag.
    plain = minted([])
    assert "--config" not in plain
    plain_target, _jump = ops.pane_settings(ops.parse_pane_args(plain[5:]))
    assert plain_target == "user@instance-a.example"


# ---------------------------------------------------------------------------
# titles: identity, and the beat age that must never become a state
# ---------------------------------------------------------------------------

def test_the_title_carries_label_host_cwd_and_pane(mac):
    title = mac.row_title(wire("aaaa1111"), NOW)
    for part in ("build", HOST, "/shared/work/project", "%24"):
        assert part in title


def test_two_agents_in_one_tmux_session_get_different_titles(mac):
    """Everything but `pane` is identical for these two, which is exactly the
    case that makes a sidebar useless: two rows, same text."""
    one = mac.row_title(wire("aaaa1111", pane="%24"), NOW)
    two = mac.row_title(wire("bbbb2222", pane="%31"), NOW)
    assert one != two


def test_a_healthy_beat_puts_no_age_in_the_title(mac):
    """A beat is refreshed every `BEAT_INTERVAL` by whoever can prove the agent
    is alive, so an age under two intervals says nothing -- and printing it
    would repaint every row on every tick."""
    assert mac.beat_age_text(mac.beat_age(wire("aaaa1111", beat=FRESH), NOW)) == ""
    assert str(int(NOW - FRESH)) not in mac.row_title(wire("aaaa1111"), NOW)


def test_a_late_beat_puts_its_age_in_the_title(mac):
    assert mac.row_title(wire("aaaa1111", beat=LATE), NOW).endswith("20m")


@pytest.mark.parametrize("age,text", [
    (0, ""), (5, ""), (29, ""),
    (30, "30s"), (46, "45s"),
    (60, "1m"), (20 * 60, "20m"),
    (3600, "1h"), (5 * 3600, "5h"),
    (86400, "1d"), (3 * 86400, "3d"),
])
def test_the_beat_age_is_bucketed(mac, age, text):
    assert mac.beat_age_text(age) == text


def test_the_beat_age_is_computed_in_the_feeds_clock(mac, monkeypatch):
    """Constraint #12. The Mac's clock is a third domain, skewed against both
    the writer's and the NFS server's -- so it is never consulted."""
    monkeypatch.setattr(time, "time", lambda: NOW + 10 * 3600)
    assert mac.beat_age(wire("aaaa1111", beat=NOW - 120), NOW) == 120
    assert mac.row_title(wire("aaaa1111", beat=NOW - 120), NOW).endswith("2m")


def test_an_unknown_now_or_beat_produces_no_age_rather_than_a_wrong_one(mac):
    assert mac.beat_age(wire("aaaa1111"), None) is None
    assert mac.beat_age(wire("aaaa1111", beat=None), NOW) is None
    assert mac.beat_age(wire("aaaa1111", beat=NOW + 30), NOW) == 0.0


def test_a_tick_repaints_a_row_whose_age_has_moved(bridge):
    """The reason `tick` is an op at all. A `blocked` agent on machine #3 beats
    nothing -- there is no feed there -- so without this its title would show
    the age it had at its last transition, forever."""
    b = bridge()
    b.upsert(wire("aaaa1111", state="blocked", beat=NOW - 10))
    assert b.run.titles()[-1].endswith("%24")           # healthy: no age
    b.tick(now=NOW + 5 * 60)
    assert b.run.titles()[-1].endswith("5m")
    b.tick(now=NOW + 20 * 60)
    assert b.run.titles()[-1].endswith("20m")


def test_a_tick_that_changes_no_title_emits_nothing(bridge):
    b = bridge()
    b.upsert(wire("aaaa1111"))
    before = len(b.run.calls)
    b.tick(now=NOW + 1)
    b.tick(now=NOW + 2)
    assert b.run.calls[before:] == []


def test_a_repeated_identical_upsert_costs_nothing(bridge):
    b = bridge()
    b.upsert(wire("aaaa1111"))
    before = len(b.run.calls)
    b.upsert(wire("aaaa1111"))
    assert b.run.calls[before:] == []


def test_a_beat_only_upsert_does_not_repaint_a_healthy_row(bridge):
    """The feed emits an upsert whenever the beat moves. A rename per beat would
    be one agtermctl process every fifteen seconds per row, for no change."""
    b = bridge()
    b.upsert(wire("aaaa1111"))
    before = len(b.run.calls)
    b.upsert(wire("aaaa1111", beat=FRESH + 3))
    assert b.run.calls[before:] == []


# ---------------------------------------------------------------------------
# status: the vocabulary is closed
# ---------------------------------------------------------------------------

def test_the_state_is_applied_to_the_bound_row(bridge):
    b = bridge()
    b.upsert(wire("aaaa1111", state="blocked"))
    row = b.rows.row_for("aaaa1111")
    assert b.run.statuses() == [("blocked", row, False)]


@pytest.mark.parametrize("state", ["unknown", "", None, "ACTIVE", "done", 7])
def test_no_status_outside_the_vocabulary_is_ever_emitted(bridge, state):
    """Amendment 2: there is no `unknown`. Forwarding one would either be
    rejected by agterm or -- worse -- silently ignored, leaving the row showing
    the previous state while the bridge believed it had repainted it."""
    b = bridge()
    b.upsert(wire("aaaa1111", state=state))
    assert b.run.statuses() == []
    assert any("not one of" in text for text in b.warned)


def test_every_status_the_renderer_can_emit_is_in_the_vocabulary(bridge, agb):
    b = bridge()
    for index, state in enumerate(agb.AGENT_STATES):
        b.upsert(wire("aaaa1111", state=state, seq=index + 1))
    b.remove("aaaa1111")
    # A renderer that rejected every state would emit nothing, and a loop over
    # nothing asserts nothing. So the collection is proven non-empty first.
    assert b.run.statuses()
    for state, _row, _blink in b.run.statuses():
        assert state in agb.STATUS_VOCABULARY


def test_blink_is_passed_only_on_a_transition_into_active(bridge):
    """Adopted from the live `agr` config, but transitions only: it is not
    established whether agterm's `blink` is sticky or a one-shot animation, and
    a row that has been quietly active for an hour must not flash on every
    reconnect."""
    b = bridge()
    b.upsert(wire("aaaa1111", state="active", seq=1))          # first paint
    b.upsert(wire("aaaa1111", state="blocked", seq=2))
    b.upsert(wire("aaaa1111", state="active", seq=3))          # a transition
    assert [(state, blink) for state, _row, blink in b.run.statuses()] == [
        ("active", False), ("blocked", False), ("active", True)]


def test_a_snapshot_repaint_never_blinks(bridge):
    """The case the transitions-only rule exists for: a reconnect re-asserts the
    level state of a row that was already active."""
    b = bridge()
    b.upsert(wire("aaaa1111", state="active"))
    b.snapshot([wire("aaaa1111", state="active", seq=2)])
    assert [blink for _s, _r, blink in b.run.statuses()] == [False]


def test_a_freshly_minted_row_is_painted_even_if_nothing_changed(bridge):
    """Reported live: a row that showed no glyph and agterm's own default name
    until the agent was typed at.

    `_title` and `_status` both suppress a repaint that matches what this
    process last emitted, and that memory is per *key*. Forget the binding under
    a running bridge -- which `agb forget-rows` does, in another process, with
    no `save()` here to run `_forget_unmapped` -- and the next upsert mints a
    brand new agterm row while the memory still describes the old one. The
    unchanged state must not be read as "already applied": the new row is blank.
    """
    b = bridge()
    b.upsert(wire("aaaa1111", state="active", seq=1))
    first = b.run.statuses()[0][1]
    b.rows.forget("aaaa1111")
    b.upsert(wire("aaaa1111", state="active", seq=2))    # same state, new row

    second = b.rows.row_for("aaaa1111")
    assert second and second != first, "a new row should have been minted"
    assert b.run.statuses()[-1] == ("active", second, False)
    assert b.run.renames()[-1][1] == second
    # A first paint never blinks -- and now genuinely so: before this, a stale
    # `applied` could make the new row's opening paint flash.
    assert [blink for _s, row, blink in b.run.statuses() if row == second] \
        == [False]


def test_a_rebound_done_row_keeps_its_applied_memory(bridge):
    """The counter-case, so the fix above is not over-applied. `rebind` reuses
    the *same* agterm row, which still carries the `[done]` title and the `idle`
    the removal painted on it -- that memory is about this row and is still
    true, so it must survive and keep suppressing redundant repaints."""
    b = bridge()
    b.upsert(wire("aaaa1111", state="active", seq=1))
    row = b.rows.row_for("aaaa1111")
    b.remove("aaaa1111")
    b.upsert(wire("aaaa1111", state="active", seq=2))
    assert b.rows.row_for("aaaa1111") == row
    assert b.run.verbs().count("new") == 1


def test_auto_reset_is_never_passed(bridge):
    """Deliberately dropped (docs/agtermctl.md): it lets agterm repaint a row on
    its own timer with no notification back, which is exactly the
    model-versus-display divergence `[done]` exists to prevent."""
    b = bridge()
    for index, state in enumerate(("active", "blocked", "completed")):
        b.upsert(wire("aaaa1111", state=state, seq=index + 1))
    b.remove("aaaa1111")
    b.stale()
    # Without this the whole subject of the test is unverifiable: a renderer
    # that stopped invoking agtermctl altogether would satisfy every iteration
    # of a loop that never runs.
    assert b.run.agterm()
    for call in b.run.agterm():
        assert "--auto-reset" not in call
        assert "--autoReset" not in call


def test_a_completed_row_stays_completed_until_something_changes_it(bridge):
    """The consequence of dropping `--auto-reset`, stated as a test."""
    b = bridge()
    b.upsert(wire("aaaa1111", state="completed"))
    before = len(b.run.calls)
    b.tick(now=NOW + 1)
    assert b.run.calls[before:] == []
    assert b.renderer.applied["aaaa1111"] == "completed"


# ---------------------------------------------------------------------------
# remove -> [done]
# ---------------------------------------------------------------------------

def test_remove_unbinds_clears_the_glyph_and_marks_the_title(bridge):
    b = bridge()
    b.upsert(wire("aaaa1111"))
    row = b.rows.row_for("aaaa1111")
    b.remove("aaaa1111")
    assert b.rows.row_for("aaaa1111") is None
    assert b.rows.done_entries() == [("aaaa1111", row)]
    assert b.run.titles()[-1].startswith("[done] ")
    assert b.run.statuses()[-1] == ("idle", row, False)


def test_remove_never_closes_or_kills_the_row(bridge):
    """Scoped deliberately: `close-done` must emit exactly `session close`, so a
    blanket "no close anywhere" assertion would be wrong. On the `remove` path
    it must never happen -- the row is the user's, and taking it away the
    instant an agent finishes is not the bridge's call."""
    b = bridge()
    b.upsert(wire("aaaa1111"))
    b.remove("aaaa1111")
    assert "close" not in b.run.verbs()
    assert "kill" not in b.run.verbs()


def test_a_done_row_is_distinguishable_from_a_live_idle_row(bridge, mac):
    """`idle` renders as *no glyph*, so without the title marker a finished
    agent's row is pixel-identical to a live idle one -- the dashboard-that-lies
    failure in a new costume."""
    b = bridge()
    b.upsert(wire("aaaa1111"))
    b.upsert(wire("bbbb2222"))
    b.remove("aaaa1111")
    done_row = b.rows.done_entries()[0][1]
    live_row = b.rows.row_for("bbbb2222")

    titles = dict((target, title) for title, target in b.run.renames())
    assert titles[done_row].startswith(mac.TITLE_DONE)
    assert not titles[live_row].startswith(mac.TITLE_DONE)
    # ...and the status alone genuinely does not distinguish them.
    b.upsert(wire("bbbb2222", state="idle", seq=2))
    assert b.renderer.applied == {"aaaa1111": "idle", "bbbb2222": "idle"}


def test_removing_a_key_that_was_never_bound_is_a_no_op(bridge):
    b = bridge()
    b.upsert(wire("aaaa1111"))
    before = len(b.run.calls)
    b.remove("bbbb2222")
    assert b.run.calls[before:] == []


# ---------------------------------------------------------------------------
# staleness: feed death, and nothing else
# ---------------------------------------------------------------------------

def test_feed_death_paints_every_row_idle_with_a_question_mark(bridge):
    b = bridge()
    b.upsert(wire("aaaa1111", state="active"))
    b.upsert(wire("bbbb2222", state="blocked"))
    b.stale("eof")
    assert [title for title in b.run.titles()[-2:]
            if title.startswith("[?] ")] != []
    assert set(state for state, _r, _b in b.run.statuses()[-2:]) == set(["idle"])


@pytest.mark.parametrize("reason", ["eof", "watchdog", "spawn-failed"])
def test_every_way_the_feed_can_die_gets_the_same_treatment(bridge, reason):
    """Process exit, the app-level watchdog and a failed spawn are one fact:
    there is no feed. The bridge owns the ssh, so this is the one staleness
    trigger it can *prove* (amendment 1)."""
    b = bridge()
    b.upsert(wire("aaaa1111"))
    b.stale(reason)
    assert b.run.titles()[-1].startswith("[?] ")


def test_exactly_one_notification_per_outage(bridge):
    b = bridge()
    b.upsert(wire("aaaa1111"))
    b.stale("eof")
    b.stale("eof")                                  # the supervisor retries
    b.stale("watchdog")
    assert len(b.run.others()) == 1
    assert b.run.others()[0][0] == "osascript"
    assert any("NOTICE" in text for text in b.warned)


def test_the_notification_is_rate_limited_across_reconnect_cycles(bridge):
    """`mark_stale` is idempotent, but the model's flag is per-CONNECTION and
    the first line of the next one clears it. A farm-side NFS stall that ends
    the connection, gets respawned and stalls again therefore fired one desktop
    banner per backoff cycle -- roughly one every 12-16 s, for a condition the
    user can do nothing about."""
    b = bridge()
    now = [1000.0]
    b.renderer.clock = lambda: now[0]
    b.upsert(wire("aaaa1111"))
    for _cycle in range(5):
        b.stale("watchdog")
        b.upsert(wire("aaaa1111"))               # the reconnect lifts `stale`
        now[0] += 12.0
    assert len(b.run.others()) == 1

    now[0] += b.mac.NOTIFY_INTERVAL
    b.stale("watchdog")
    assert len(b.run.others()) == 2


def test_the_stderr_notice_is_never_rate_limited(bridge):
    """It is the launchd log, and it is what makes the banner's absence
    diagnosable rather than a second silence."""
    b = bridge()
    b.renderer.clock = lambda: 1000.0
    b.upsert(wire("aaaa1111"))
    for _cycle in range(3):
        b.stale("watchdog")
        b.upsert(wire("aaaa1111"))
    assert len([t for t in b.warned if "NOTICE" in t]) == 3
    assert len(b.run.others()) == 1


def test_the_notice_survives_the_real_warn_channel(mac, capsys):
    """⚠️ The test above proves nothing about production on its own, and for a
    release it proved the opposite of the truth.

    It hands the renderer a plain `list.append`, while `run_bridge` hands it a
    `_warn_once` closure that dedups BY EXACT TEXT FOR EVER. So five outages
    over two hours reached the launchd log once, while the docstring claimed the
    stderr line always fires. This drives the real closure.
    """
    reported = set()
    for _outage in range(5):
        mac._bridge_warn(reported, mac.NOTICE + "the feed is gone (watchdog)")
        mac._bridge_warn(reported, "agtermctl session rename failed")
    err = capsys.readouterr().err
    assert err.count("NOTICE") == 5
    # ...and the ordinary warning is still deduplicated, which is why the
    # exemption has to be narrow: that one would be a line per poll.
    assert err.count("agtermctl session rename failed") == 1


def test_recovery_repaints_every_row_even_when_nothing_changed(bridge):
    """A reconnect is a full repaint, and this pins *why* -- it is not obvious.

    A row's state only moves when Claude Code fires a hook, so an idle agent's
    state is usually identical either side of an outage, and `_status` skips a
    repaint that matches what was last emitted. What makes the repaint land
    anyway is that `_render_stale` painted `idle` on the way down, so the
    remembered status differs from the real one on the way back up.

    Break that pairing -- stop painting `idle` when going stale, or start
    forcing the repaint on the way back -- and the row silently keeps agterm's
    `[?]`/no-glyph rendering, which is indistinguishable from a live idle agent.
    """
    b = bridge()
    b.upsert(wire("aaaa1111", state="active"))
    b.stale("eof")                       # the connection died: `[?]` + idle
    assert b.renderer.applied["aaaa1111"] == "idle"    # the load-bearing step
    painted = len(b.run.statuses())
    b.upsert(wire("aaaa1111", state="active", seq=2))   # reconnect, same state

    states = [state for state, _row, _blink in b.run.statuses()][painted:]
    assert "active" in states, "the row was never given its status back"


def test_a_recovery_repaint_does_not_blink(bridge):
    """Forcing the repaint must not turn every VPN hiccup into a flash: a
    recovery is not a transition into `active`."""
    b = bridge()
    b.upsert(wire("aaaa1111", state="active"))
    b.stale("eof")
    b.upsert(wire("aaaa1111", state="active", seq=2))
    assert [blink for _s, _r, blink in b.run.statuses()] == [False] * len(
        b.run.statuses())


class _Clock(object):
    """A hand-cranked monotonic clock, for the re-assert interval."""

    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def _reassert_bridge(mac, rows_file):
    clock = _Clock()
    b = Harness(mac, rows_file)
    b.renderer.clock = clock
    b.renderer.reasserted = clock()
    return b, clock


def _notifies(bridge_obj):
    """The `agtermctl notify` calls, as (body, title, target)."""
    out = []
    for call in bridge_obj.run.agterm():
        if call[1] != "notify":
            continue
        opts = _options(call)
        out.append((call[2], opts.get("--title"), opts.get("--target")))
    return out


def test_a_block_raises_one_desktop_banner(bridge):
    """`blocked` is the only state that means *you* are the blocker -- a
    permission prompt waiting for an answer. A glyph is enough for `active`; it
    is not enough for this, because the point of the row is that you are not
    looking at it."""
    b = bridge()
    b.upsert(wire("aaaa1111", state="active"))
    b.upsert(wire("aaaa1111", state="blocked", seq=2))

    sent = _notifies(b)
    assert len(sent) == 1
    body, title, target = sent[0]
    assert "waiting" in body
    assert target == b.rows.row_for("aaaa1111")   # attributed to the right row
    assert title


def test_a_block_that_persists_is_announced_once(bridge):
    """It stays blocked until somebody answers it, and every snapshot repeats
    the state. One banner per block, not one per poll."""
    b = bridge()
    for seq in range(1, 6):
        b.upsert(wire("aaaa1111", state="blocked", seq=seq))
    assert len(_notifies(b)) == 1


def test_a_disconnect_does_not_re_announce_a_still_blocked_agent(bridge):
    """⚠️ The reason this has a memory of its own instead of using `applied`.

    `_render_stale` paints every row `idle` on any disconnect -- including a
    10 s quiet spell, which is a routine event. Gated on `applied`, an agent
    that simply stayed blocked would be announced again on every reconnect. The
    gate must track what the AGENT did, not what we painted.
    """
    b = bridge()
    b.upsert(wire("aaaa1111", state="blocked"))
    assert len(_notifies(b)) == 1
    b.stale("eof")                                  # rows go idle + `[?]`
    b.upsert(wire("aaaa1111", state="blocked", seq=2))   # same block, still open
    assert len(_notifies(b)) == 1, "a hiccup re-announced an unchanged block"


def test_answering_and_blocking_again_announces_again(bridge):
    """The counter-case, so the memory is not simply never cleared: a second
    permission prompt is a second thing waiting for you."""
    b = bridge()
    b.upsert(wire("aaaa1111", state="blocked"))
    b.upsert(wire("aaaa1111", state="active", seq=2))    # you answered it
    b.upsert(wire("aaaa1111", state="blocked", seq=3))   # and it asks again
    assert len(_notifies(b)) == 2


def test_the_banner_can_be_turned_off(bridge):
    """Unsolicited UI needs an off switch. The state is still tracked, so
    turning it back on does not produce a backlog."""
    b = bridge(settings={"notify_blocked": False})
    b.upsert(wire("aaaa1111", state="blocked"))
    assert _notifies(b) == []
    assert "aaaa1111" in b.renderer.blocked


def _past_quiet(bridge_obj):
    """Move the renderer past its new-row quiet window."""
    bridge_obj.renderer.quiet_until = 0
    return bridge_obj


def test_a_new_agent_gets_a_banner_naming_its_directory(mac, bridge):
    """The counterpart to the blocked banner: that one says *you are needed*,
    this one says *something new exists*. The cwd is in it because two agents
    on one host share everything else -- the directory is what tells you which
    piece of work turned up."""
    b = _past_quiet(bridge())
    b.upsert(wire("aaaa1111", label="build", host="box2",
                  cwd="/work/api", state="active"))

    sent = _notifies(b)
    assert len(sent) == 1
    body, title, target = sent[0]
    assert "build" in body and "/work/api" in body
    assert "box2" in title
    assert target == b.rows.row_for("aaaa1111")


def test_a_burst_of_rows_at_startup_is_silent(bridge):
    """⚠️ The reason the quiet window exists. `agb-refresh` forgets every
    binding and the bridge re-mints all of them; so does a first install or a
    lost rows file. Nine rows would be nine banners and nine Dock bounces for
    agents that have been running since breakfast -- which is how a feature
    gets switched off on its first day."""
    b = bridge()                          # NOT past the window
    for n in range(5):
        b.upsert(wire("aaaa111%d" % (n,), state="active"))
    assert _notifies(b) == []
    assert len(list(b.rows.bound_keys())) == 5, "the rows were still created"


def test_a_slow_connect_does_not_expire_the_quiet_window(mac, bridge):
    """⚠️ Why the window is armed by the first BATCH and not at construction.

    The renderer is built before ssh connects; the burst arrives with the
    snapshot, whenever that is. Armed at construction, a slow connect -- a VPN,
    a cold host -- lets the window expire before a single row exists, so the
    burst banners anyway: the thing it protects against is exactly the thing
    that would defeat it.
    """
    clock = _Clock()
    b = bridge()
    b.renderer.clock = clock
    b.renderer.quiet_until = None                 # nothing armed yet
    clock.advance(mac.NEW_ROW_QUIET * 10)         # ssh took its time

    for n in range(3):
        b.upsert(wire("aaaa111%d" % (n,), state="active"))
    assert _notifies(b) == [], "the burst banners after a slow connect"


def test_the_window_is_armed_once_not_per_batch(mac, bridge):
    """Otherwise every batch would re-arm it and no new agent would ever be
    announced -- the feature would be silent for ever, with nothing to see."""
    clock = _Clock()
    b = bridge()
    b.renderer.clock = clock
    b.renderer.quiet_until = None
    b.upsert(wire("aaaa1111", state="active"))    # arms; inside the window
    assert _notifies(b) == []

    clock.advance(mac.NEW_ROW_QUIET + 1)
    b.upsert(wire("bbbb2222", state="active"))    # a genuinely new agent
    assert len(_notifies(b)) == 1


def test_a_reconnect_re_arms_the_quiet_window(bridge):
    """A reconnect is the other catch-up moment: the snapshot that follows can
    mint rows for keys a previous bridge had bound."""
    b = _past_quiet(bridge())
    b.stale("eof")
    b.upsert(wire("aaaa1111", state="active"))   # lifts stale, then the row
    assert _notifies(b) == []


def test_a_done_row_coming_back_is_not_a_new_agent(bridge):
    """`rebind` reuses the row of an agent we already knew about. It is a
    return, not an arrival."""
    b = _past_quiet(bridge())
    b.upsert(wire("aaaa1111", state="active"))
    before = len(_notifies(b))
    b.remove("aaaa1111")                          # `[done]`, still in the map
    b.upsert(wire("aaaa1111", state="active", seq=2))
    assert len(_notifies(b)) == before


def test_new_row_banners_can_be_turned_off_alone(bridge):
    """Its own switch: wanting to hear about a new agent but not about a
    blocked one -- or the reverse -- is an ordinary preference."""
    b = _past_quiet(bridge(settings={"notify_new_row": False}))
    b.upsert(wire("aaaa1111", state="active"))
    assert _notifies(b) == []
    b.upsert(wire("aaaa1111", state="blocked", seq=2))
    assert len(_notifies(b)) == 1, "the blocked banner is a separate switch"


def _seens(bridge_obj):
    """The `agtermctl session seen` calls, as targets."""
    return [_options(call).get("--target") for call in bridge_obj.run.agterm()
            if call[1:3] == ["session", "seen"]]


def test_leaving_blocked_clears_the_badge_it_raised(bridge):
    """The badge must not outlive the thing it announced. Answering the prompt
    in a terminal you already had open never touches the agterm row, so agterm
    never clears it on its own."""
    b = bridge()
    b.upsert(wire("aaaa1111", state="blocked"))
    row = b.rows.row_for("aaaa1111")
    b.upsert(wire("aaaa1111", state="active", seq=2))     # somebody answered it
    assert _seens(b) == [row]


def test_a_row_that_was_never_blocked_is_never_cleared(bridge):
    """The membership test is what makes this a transition. A bare `discard`
    would fire on every non-blocked upsert -- every poll of every healthy agent
    -- and `session seen` being idempotent means nothing would ever look wrong.
    It would just be one wasted subprocess per row per poll, for ever."""
    b = bridge()
    for seq in range(1, 6):
        b.upsert(wire("aaaa1111", state="active", seq=seq))
    b.upsert(wire("aaaa1111", state="completed", seq=6))
    assert _seens(b) == []


def test_a_disconnect_does_not_clear_the_badge(bridge):
    """`_render_stale` paints `idle`, which is not an answer. Same immunity the
    banner has, and for free: both read the set, which only changes when the
    AGENT's state does."""
    b = bridge()
    b.upsert(wire("aaaa1111", state="blocked"))
    b.stale("eof")
    assert _seens(b) == []
    b.upsert(wire("aaaa1111", state="blocked", seq=2))    # still the same block
    assert _seens(b) == []


def test_with_banners_off_the_badge_is_left_alone(bridge):
    """Unwind what you did, and nothing else: with `notify_on_blocked = 0` the
    bridge neither raises the badge nor clears it. One switch, whole feature."""
    b = bridge(settings={"notify_blocked": False})
    b.upsert(wire("aaaa1111", state="blocked"))
    b.upsert(wire("aaaa1111", state="active", seq=2))
    assert _notifies(b) == []
    assert _seens(b) == []


def test_a_row_status_is_re_asserted_periodically(mac, rows_file):
    """Reported live: attach to a row for the first time and its glyph
    disappears. agterm resets a session's status when the session's command
    starts, and because the remembered status still matched, the bridge never
    repainted -- the row stayed blank until the agent's state next moved, which
    for an idle agent can be hours.

    So the memory is an optimisation, not a description of the screen. Anything
    that resets a row out from under us self-heals within one interval.
    """
    b, clock = _reassert_bridge(mac, rows_file)
    b.upsert(wire("aaaa1111", state="active"))
    painted = len(b.run.statuses())

    clock.advance(mac.REASSERT_INTERVAL + 1)
    b.tick()

    after = b.run.statuses()[painted:]
    assert [state for state, _row, _blink in after] == ["active"]


def test_a_re_assert_never_blinks(mac, rows_file):
    """It is not a transition. Otherwise every row on the sidebar would flash
    once per interval, for ever."""
    b, clock = _reassert_bridge(mac, rows_file)
    b.upsert(wire("aaaa1111", state="active"))
    for _round in range(3):
        clock.advance(mac.REASSERT_INTERVAL + 1)
        b.tick()
    assert [blink for _s, _r, blink in b.run.statuses()] == [False] * 4


def test_a_re_assert_is_rate_limited_to_the_interval(mac, rows_file):
    """A tick arrives every couple of seconds. Re-asserting on each one would
    be one `agtermctl` per row per tick, for a display that is almost always
    already right."""
    b, clock = _reassert_bridge(mac, rows_file)
    b.upsert(wire("aaaa1111", state="active"))
    painted = len(b.run.statuses())
    for _tick in range(10):
        clock.advance(1.0)
        b.tick()
    assert len(b.run.statuses()) == painted, "no interval elapsed; no re-assert"


def test_a_re_assert_does_not_undo_the_stale_rendering(mac, rows_file):
    """While the feed is gone, `idle` + `[?]` is the correct rendering. Putting
    the last known status back would be the bridge asserting something it can no
    longer see -- exactly the inference this design refuses to make."""
    b, clock = _reassert_bridge(mac, rows_file)
    b.upsert(wire("aaaa1111", state="active"))
    b.stale("eof")
    painted = len(b.run.statuses())
    clock.advance(mac.REASSERT_INTERVAL + 1)
    b.renderer._render_tick()          # a tick cannot really arrive here
    assert len(b.run.statuses()) == painted


def test_every_bridge_warning_carries_a_timestamp(mac, capsys):
    """The launchd log is appended across every restart for the life of the
    machine, while the dedup is per process -- so without a stamp, a block of
    `no such session` from three restarts ago reads exactly like one happening
    now. It cost a diagnosis round: a row map that had already been repaired
    read as broken.
    """
    reported = set()
    mac._bridge_warn(reported, "agtermctl session rename failed")
    mac._bridge_warn(reported, mac.NOTICE + "the feed is gone (watchdog)")
    lines = [line for line in capsys.readouterr().err.splitlines() if line]
    assert len(lines) == 2
    for line in lines:
        stamp = line.split()[2]        # "agb bridge: <stamp> ..."
        assert stamp.endswith("Z") and stamp.count(":") == 2, line
        # Parsed, not pattern-matched: a stamp that is not a real time is worse
        # than none, because it will be believed.
        time.strptime(stamp.split(".")[0], "%Y-%m-%dT%H:%M:%S")


def test_the_timestamp_does_not_disable_the_dedup(mac, capsys, monkeypatch):
    """The trap in stamping: hand `_warn_once` the stamped line and every line
    is unique, so the dedup silently stops working and the broken-agtermctl
    warning becomes one line per poll. The dedup key must stay the raw text.

    The clock is **forced to advance**. Written against the real one this test
    passes either way: five polls in a tight loop land inside the same
    millisecond, `iso_stamp` returns the same string, and the broken version
    dedups by accident -- a mutation of the code under test left it green.
    """
    ticks = iter(["2026-07-30T00:00:%02d.000Z" % (n,) for n in range(5)])
    monkeypatch.setattr(mac.agb, "iso_stamp", lambda now=None: next(ticks))
    reported = set()
    for _poll in range(5):
        mac._bridge_warn(reported, "agtermctl session rename failed")
    assert capsys.readouterr().err.count("rename failed") == 1


def test_a_quiet_period_does_not_announce_a_death(bridge, mac):
    """`BRIDGE_QUIET` (10 s) renders `[?]` and keeps reading; only the watchdog
    and a real EOF end the connection. Announcing "the feed is gone" at 10 s
    trains the reader to ignore the notification that matters."""
    b = bridge()
    b.upsert(wire("aaaa1111"))
    b.stale(mac.BRIDGE_QUIET_REASON)
    notice = [text for text in b.warned if "NOTICE" in text][-1]
    assert "is gone" not in notice
    assert "still open" in notice
    assert b.run.titles()[-1].startswith("[?] ")


def test_a_real_outage_still_says_the_feed_is_gone(bridge):
    b = bridge()
    b.upsert(wire("aaaa1111"))
    b.stale("watchdog")
    assert "the feed is gone (watchdog)" in \
        [text for text in b.warned if "NOTICE" in text][-1]


def test_the_feed_coming_back_lifts_the_marker_and_reasserts_the_state(bridge):
    b = bridge()
    b.upsert(wire("aaaa1111", state="blocked"))
    b.stale("watchdog")
    b.snapshot([wire("aaaa1111", state="blocked", seq=1)])
    assert not b.run.titles()[-1].startswith("[?] ")
    assert b.run.statuses()[-1][0] == "blocked"
    assert b.renderer.stale is False


def test_a_notification_that_cannot_be_posted_is_reported_not_swallowed(bridge):
    b = bridge(Runner(fail=("all",)))
    b.rows.bind("aaaa1111", "ROW-1")
    b.stale("eof")
    assert any("NOTICE" in text for text in b.warned)


def test_no_beat_age_however_old_ever_changes_a_status(bridge):
    """Amendment 1 as a test, on the rendering side: a `blocked` agent waiting
    on the user beats nothing, and 30 minutes of that must still render as
    `blocked` -- with the age in the title and nowhere else."""
    b = bridge()
    b.upsert(wire("aaaa1111", state="blocked", beat=NOW - 30 * 60))
    b.tick(now=NOW + 3600)
    assert [state for state, _r, _b in b.run.statuses()] == ["blocked"]
    assert b.run.titles()[-1].endswith("1h")
    assert not b.run.titles()[-1].startswith("[?] ")


def test_a_row_bound_before_a_restart_is_reclaimed_when_its_agent_is_gone(
        mac, rows_file):
    """Without seeding the model from the persisted map, the first snapshot has
    nothing to compare against and the row stays bound and visible forever --
    `agr` failure mode #3 rebuilt out of the map.

    The remembered title is what lets the reclaimed row still say what it WAS:
    `self.seen` is empty in a process that has only just started, so before the
    map carried a title this rename replaced the row's identity with its raw
    hex key on every launchd restart.
    """
    seed = mac.load_rows(str(rows_file))
    seed.bind("aaaa1111", "ROW-1", "build · box2 · /shared/x · %24")
    seed.save()

    b = Harness(mac, rows_file)
    b.snapshot([wire("bbbb2222")])
    assert b.rows.done_entries() == [("aaaa1111", "ROW-1")]
    assert [title for title, target in b.run.renames()
            if target == "ROW-1"] == ["[done] build · box2 · /shared/x · %24"]


def test_a_title_painted_by_one_bridge_survives_into_the_next(bridge,
                                                              rows_file):
    """The whole restart path, end to end and with nothing seeded by hand: one
    bridge paints a row, the process dies, the next one's FIRST snapshot no
    longer carries the key. `self.seen` is empty over there, so the map's
    remembered title is the only thing that can carry the identity into the
    `[done]` rename."""
    first = bridge()
    first.upsert(wire("aaaa1111"))
    row = first.rows.row_for("aaaa1111")
    # The identity moves after the row was created -- a renamed tmux session, a
    # `cd`. What the map has to carry is the LAST title painted, not the first.
    first.upsert(wire("aaaa1111", label="ia_split", cwd="/shared/other",
                      seq=2))
    painted = [t for t, target in first.run.renames() if target == row][-1]
    assert "ia_split" in painted

    second = bridge()                      # a fresh process: `seen` is empty
    assert second.renderer.seen == {}
    second.snapshot([])
    assert [t for t, target in second.run.renames() if target == row] == [
        "[done] " + painted]


def test_a_reclaimed_row_with_no_remembered_title_is_marked_with_its_key(
        mac, rows_file):
    """The one case the map cannot help with -- a version-1 file, or a row bound
    by a bridge that died before its first rename.

    `[done]` is worth having even at the price of a hex string, because the
    `idle` next to it is applied either way: a row with `idle` and no `[done]`
    reads as a live idle agent, and there is no command that would ever tell the
    reader otherwise. The identity is not lost for ever -- the next upsert paints
    the real one -- whereas a row that lies is not self-correcting at all."""
    seed = mac.load_rows(str(rows_file))
    seed.bind("aaaa1111", "ROW-1")
    seed.save()

    b = Harness(mac, rows_file)
    b.snapshot([wire("bbbb2222")])
    assert b.rows.done_entries() == [("aaaa1111", "ROW-1")]
    assert [title for title, target in b.run.renames()
            if target == "ROW-1"] == ["[done] aaaa1111"]
    assert ("idle", "ROW-1", False) in b.run.statuses()


def test_the_bridge_seeds_its_model_from_the_persisted_map(mac, run_agb,
                                                           agtermctl,
                                                           rows_file):
    """The same fact end to end, because the seeding is *wiring*: a unit test
    that calls `adopt` itself proves the model, not the command. Without the
    line in `run_bridge`, this row survives the snapshot and is never
    reclaimable by anything."""
    seed = mac.load_rows(str(rows_file))
    seed.bind("aaaa1111", "ROW-1", "build · box2 · /shared/x · %24")
    seed.save()
    stdin = json.dumps({"t": "snapshot", "now": NOW,
                        "sessions": [wire("bbbb2222")]}).encode() + b"\n"
    rc, out, err = run_agb(["bridge", "--from-stdin", "--rows",
                            str(rows_file)], stdin=stdin)
    assert rc == 0, err
    assert "remove aaaa1111" in out.decode()
    assert mac.load_rows(str(rows_file)).done_entries() == [("aaaa1111",
                                                             "ROW-1")]
    renamed = [call[2] for call in agtermctl.calls()
               if call[1] == "rename" and "ROW-1" in call]
    assert renamed and renamed[-1].startswith("[done] ")


# ---------------------------------------------------------------------------
# `agb close-done`
# ---------------------------------------------------------------------------

class Out(object):
    def __init__(self):
        self.text = ""

    def write(self, text):
        self.text += text

    def flush(self):
        pass


def test_close_done_closes_only_done_rows(mac, rows_file):
    rows = mac.load_rows(str(rows_file))
    rows.bind("aaaa1111", "ROW-1")
    rows.bind("bbbb2222", "ROW-2")
    rows.unbind("bbbb2222")
    rows.save()

    runner = Runner()
    out = Out()
    assert mac.run_close_done(["--rows", str(rows_file)], run=runner,
                              out=out) == 0
    assert runner.agterm() == [["agtermctl", "session", "close",
                                "--target", "ROW-2"]]
    after = mac.load_rows(str(rows_file))
    assert after.bound_keys() == ["aaaa1111"]
    assert after.done_entries() == []
    assert "closed bbbb2222" in out.text


def test_close_done_keeps_a_row_it_could_not_close(mac, rows_file):
    """The documented degradation if `session close` turns out not to exist:
    tell the operator what to close by hand rather than forget the row, which
    would leave an orphan nothing remembers."""
    rows = mac.load_rows(str(rows_file))
    rows.bind("aaaa1111", "ROW-1")
    rows.unbind("aaaa1111")
    rows.save()

    out = Out()
    assert mac.run_close_done(["--rows", str(rows_file)],
                              run=Runner(fail=("close",)), out=out) == 0
    assert mac.load_rows(str(rows_file)).done_entries() == [("aaaa1111",
                                                             "ROW-1")]
    assert "close by hand: aaaa1111" in out.text


def test_close_done_dry_run_closes_nothing(mac, rows_file):
    rows = mac.load_rows(str(rows_file))
    rows.bind("aaaa1111", "ROW-1")
    rows.unbind("aaaa1111")
    rows.save()

    runner = Runner()
    out = Out()
    mac.run_close_done(["--rows", str(rows_file), "--dry-run"], run=runner,
                       out=out)
    assert runner.calls == []
    assert "would close aaaa1111" in out.text
    assert mac.load_rows(str(rows_file)).done_entries() == [("aaaa1111",
                                                             "ROW-1")]


def test_close_done_keeps_a_row_the_bridge_rebound_while_it_worked(mac,
                                                                    rows_file):
    """`close-done` reads the map once and then spends a subprocess per row, and
    the bridge is a second writer: `RowMap.rebind` returns a `[done]` row to a
    live agent the moment the feed re-asserts its key. Closing it would take the
    pane away, and `session close` does not undo.

    The rebind must also be followed in memory -- otherwise `save()` writes our
    stale `done` back over the bridge's `bound` and orphans a live row.
    """
    rows = mac.load_rows(str(rows_file))
    rows.bind("aaaa1111", "ROW-1")
    rows.bind("bbbb2222", "ROW-2")
    rows.unbind("aaaa1111")
    rows.unbind("bbbb2222")
    rows.save()

    class Rebinder(Runner):
        """A bridge that rebinds the *second* key while the first is closing."""

        def __call__(self, argv, timeout=None):
            if argv[0] == "agtermctl" and len(self.calls) == 0:
                other = mac.load_rows(str(rows_file))
                other.rebind("bbbb2222")
                other.save()
            return Runner.__call__(self, argv, timeout)

    runner = Rebinder()
    out = Out()
    assert mac.run_close_done(["--rows", str(rows_file)], run=runner,
                              out=out) == 0
    assert runner.agterm() == [["agtermctl", "session", "close",
                                "--target", "ROW-1"]]
    assert "kept bbbb2222" in out.text
    after = mac.load_rows(str(rows_file))
    assert after.bound_keys() == ["bbbb2222"]
    assert after.done_entries() == []


def test_close_done_does_not_revert_a_done_the_bridge_wrote_while_it_worked(
        mac, rows_file):
    """The mirror of the rebind case, and the one the lock does NOT close.

    `close-done`'s read and its `save()` are separated by one `agtermctl
    session close` subprocess per row, and the feed polls every couple of
    seconds -- so a `[done]` the bridge records in between is an entry the
    reclaimer holds as `bound` and never touched. Writing that copy back means
    the row renders `[done]` on screen while `close-done` reports "no [done]
    rows to close (1 still bound)" and never reclaims it. It heals on the
    bridge's next map write, which on an idle farm -- exactly when this command
    gets run -- can be never.
    """
    rows = mac.load_rows(str(rows_file))
    rows.bind("aaaa1111", "ROW-1")
    rows.bind("bbbb2222", "ROW-2")
    rows.unbind("aaaa1111")
    rows.save()

    class Unbinder(Runner):
        """A bridge that marks the *other* key `[done]` mid-close."""

        def __call__(self, argv, timeout=None):
            if argv[0] == "agtermctl" and len(self.calls) == 0:
                other = mac.load_rows(str(rows_file))
                other.unbind("bbbb2222")
                other.save()
            return Runner.__call__(self, argv, timeout)

    out = Out()
    assert mac.run_close_done(["--rows", str(rows_file)], run=Unbinder(),
                              out=out) == 0
    after = mac.load_rows(str(rows_file))
    assert after.done_entries() == [("bbbb2222", "ROW-2")]
    assert after.bound_keys() == []


def test_close_done_with_nothing_to_do_says_so(mac, rows_file):
    rows = mac.load_rows(str(rows_file))
    rows.bind("aaaa1111", "ROW-1")
    rows.save()
    runner = Runner()
    out = Out()
    mac.run_close_done(["--rows", str(rows_file)], run=runner, out=out)
    assert runner.calls == []
    assert "no [done] rows" in out.text


@pytest.mark.parametrize("argv", [["--nonsense"], ["extra"], ["--rows"]])
def test_a_bad_close_done_invocation_is_refused(mac, agb, argv):
    with pytest.raises(agb.AgbError):
        mac.parse_close_done_args(argv)


# ---------------------------------------------------------------------------
# errors: agtermctl is the one thing here we do not own
# ---------------------------------------------------------------------------

def test_an_agtermctl_that_fails_everything_does_not_wedge_the_bridge(bridge):
    b = bridge(Runner(fail=("all",)))
    b.upsert(wire("aaaa1111"))
    b.upsert(wire("bbbb2222", state="blocked"))
    b.remove("aaaa1111")
    b.stale("eof")
    assert b.rows.bound_keys() == []          # nothing was bound...
    assert b.warned                           # ...and it said so, every time


def test_a_row_map_that_cannot_be_written_does_not_wedge_the_bridge(bridge,
                                                                    tmp_path):
    """Losing the map costs reclamation after the next restart. Losing the
    transport costs the whole dashboard, so the second must never follow from
    the first -- and the entry stays dirty, so a transient failure heals."""
    wall = tmp_path / "not-a-directory"
    with open(str(wall), "w") as handle:
        handle.write("")
    b = bridge(path=wall / "rows")
    b.upsert(wire("aaaa1111"))
    assert b.rows.row_for("aaaa1111")            # the row itself was created
    b.upsert(wire("bbbb2222"))
    assert b.rows.dirty is True
    assert any("persist the row map" in text for text in b.warned)


def test_a_row_that_could_not_be_created_is_retried_on_the_next_upsert(bridge):
    """A transient failure must not cost the row permanently: the bridge holds
    the level state and the next event re-applies it."""
    runner = Runner(fail=("new",))
    b = bridge(runner)
    b.upsert(wire("aaaa1111"))
    assert b.rows.row_for("aaaa1111") is None
    runner.fail = set()
    b.upsert(wire("aaaa1111", seq=2))
    assert b.rows.row_for("aaaa1111")


@pytest.mark.parametrize("printed", ["", "\n", "   \n", "x" * 200])
def test_a_session_new_that_prints_no_usable_id_is_reported(bridge, printed):
    """The diagnosis has to name `session new`, not the row map: an id that
    never arrived is a contract violation by the tool we do not own, and
    docs/agtermctl.md carries three recorded fallbacks for exactly it. Asserting
    only that *something* was warned would pass just as well against a renderer
    that handed the empty string to `bind` and let the map complain."""
    b = bridge(Runner(ids=[printed]))
    b.upsert(wire("aaaa1111"))
    assert b.rows.row_for("aaaa1111") is None
    assert any("no usable row id" in text for text in b.warned), b.warned


def test_the_row_id_is_never_parsed_only_echoed_back(bridge):
    """agterm's ids are opaque by contract, so the bijection cannot come to
    depend on their format."""
    weird = "not-a-uuid::{}[]"
    b = bridge(Runner(ids=[weird]))
    b.upsert(wire("aaaa1111"))
    assert b.rows.row_for("aaaa1111") == weird
    assert b.run.renames()[-1][1] == weird


def test_a_hung_agtermctl_is_killed_rather_than_waited_on(mac):
    """A wedged local binary must not become a wedged bridge: the failure has to
    come back as data, like every other one."""
    started = time.time()
    rc, _out, err = mac._run_command(["sh", "-c", "sleep 30"], timeout=0.3)
    assert rc is None
    assert "timed out" in err
    assert time.time() - started < 10


def test_a_missing_agtermctl_is_data_not_an_exception(mac):
    rc, _out, err = mac._run_command(["/nonexistent/agtermctl", "session"])
    assert rc is None and err


# ---------------------------------------------------------------------------
# against the recording stub, end to end
# ---------------------------------------------------------------------------

def test_the_bridge_drives_agtermctl_end_to_end(run_agb, agtermctl, rows_file,
                                                tmp_path):
    """Everything above with the real subprocess machinery underneath, against
    a stub that rejects anything the recorded contract forbids."""
    lines = []
    for event in (
        {"t": "snapshot", "now": NOW, "sessions": [wire("aaaa1111")]},
        {"t": "upsert", "now": NOW, "session": wire("aaaa1111",
                                                    state="blocked", seq=2)},
        {"t": "remove", "now": NOW, "key": "aaaa1111"},
    ):
        lines.append(json.dumps(event).encode())
    rc, out, err = run_agb(["bridge", "--from-stdin", "--rows", str(rows_file)],
                           stdin=b"\n".join(lines) + b"\n")
    assert rc == 0, err
    # the stub records argv *without* argv[0], so a call reads
    # ["session", "<verb>", ...]
    verbs = [(call[0], call[1]) for call in agtermctl.calls()]
    assert ("session", "new") in verbs
    assert verbs.count(("session", "new")) == 1
    assert ("session", "close") not in verbs
    statuses = [call[2] for call in agtermctl.calls() if call[1] == "status"]
    assert statuses == ["active", "blocked", "idle"]
    # the map is persisted, and the row is reclaimable
    with open(str(rows_file)) as handle:
        assert handle.read().startswith("agbridge-rows 2\ndone\taaaa1111\t")


def test_close_done_closes_the_row_the_bridge_left_behind(run_agb, agtermctl,
                                                          rows_file):
    stdin = json.dumps({"t": "snapshot", "now": NOW,
                        "sessions": [wire("aaaa1111")]}).encode() + b"\n"
    stdin += json.dumps({"t": "remove", "now": NOW,
                         "key": "aaaa1111"}).encode() + b"\n"
    rc, _out, err = run_agb(["bridge", "--from-stdin", "--rows",
                             str(rows_file)], stdin=stdin)
    assert rc == 0, err

    rc, out, err = run_agb(["close-done", "--rows", str(rows_file)])
    assert rc == 0, err
    assert b"closed aaaa1111" in out
    closes = [call for call in agtermctl.calls() if call[1] == "close"]
    assert len(closes) == 1 and "--target" in closes[0]
    with open(str(rows_file)) as handle:
        assert handle.read() == "agbridge-rows 2\n#end 0\n"


def test_the_stub_rejects_a_status_outside_the_vocabulary(agtermctl, stub_bin):
    """The stub is only worth its assertions if it really does refuse: without
    this, every "no bad status is emitted" test above could be passing against a
    stub that would have accepted one."""
    import subprocess
    proc = subprocess.Popen(
        [str(stub_bin.path / "agtermctl"), "session", "status", "unknown",
         "--target", "ROW-1"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=dict(os.environ))
    _out, err = conftest.communicate(proc)
    assert proc.returncode != 0
    assert b"not a status" in err

    proc = subprocess.Popen(
        [str(stub_bin.path / "agtermctl"), "session", "status", "idle"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=dict(os.environ))
    _out, err = conftest.communicate(proc)
    assert proc.returncode != 0                      # no --target
    assert b"no --target" in err


def test_an_agtermctl_that_fails_does_not_stop_the_bridge_end_to_end(
        run_agb, agtermctl, rows_file):
    agtermctl.fail("all")
    stdin = json.dumps({"t": "snapshot", "now": NOW,
                        "sessions": [wire("aaaa1111")]}).encode() + b"\n"
    rc, out, err = run_agb(["bridge", "--from-stdin", "--rows",
                            str(rows_file)], stdin=stdin)
    assert rc == 0
    assert out.decode().splitlines() == ["upsert aaaa1111 active", "stale eof"]
    assert b"agtermctl session new failed" in err


def test_no_agterm_keeps_the_transport_usable_on_its_own(run_agb, agtermctl,
                                                         rows_file):
    """The seam that keeps a transport problem diagnosable separately from a
    rendering one."""
    stdin = json.dumps({"t": "snapshot", "now": NOW,
                        "sessions": [wire("aaaa1111")]}).encode() + b"\n"
    rc, out, err = run_agb(["bridge", "--from-stdin", "--no-agterm",
                            "--rows", str(rows_file)], stdin=stdin)
    assert rc == 0, err
    assert out.decode().splitlines() == ["upsert aaaa1111 active", "stale eof"]
    assert agtermctl.calls() == []
    assert not os.path.exists(str(rows_file))


def test_the_row_map_lives_beside_the_config_on_the_mac(mac, agb, fake_home):
    assert mac.rows_path() == os.path.join(str(fake_home), ".config",
                                           "agbridge", "rows")
    assert os.path.dirname(mac.rows_path()) == os.path.dirname(
        agb.config_path())


def test_the_bridge_writes_the_map_under_a_home_it_can_create(mac, rows_file,
                                                              fake_home):
    """`~/.config/agbridge` may not exist yet on a fresh Mac."""
    path = fake_home / ".config" / "agbridge" / "rows"
    rows = mac.load_rows(str(path))
    rows.bind("aaaa1111", "ROW-1")
    assert rows.save() is True
    assert mac.load_rows(str(path)).bound_keys() == ["aaaa1111"]


def test_the_map_is_replaced_by_a_rename_not_rewritten_in_place(mac, rows_file):
    """This file's CONTENT is the data -- the whole key <-> row bijection -- and
    nothing anywhere reads its mtime, so content atomicity dominates (the
    project's own write-discipline table). A torn in-place write loses the
    bijection: orphan rows the bridge no longer knows about, keys whose row is
    gone, and `agb close-done` a silent no-op.

    The inode is the observable difference: `rename()` replaces it, an in-place
    `O_TRUNC` keeps it -- and it is that O_TRUNC window a concurrent reader
    would see as an empty map.
    """
    rows = mac.load_rows(str(rows_file))
    rows.bind("aaaa1111", "ROW-1")
    rows.save()
    before = os.stat(str(rows_file)).st_ino

    rows.bind("bbbb2222", "ROW-2")
    rows.save()
    assert os.stat(str(rows_file)).st_ino != before
    assert mac.load_rows(str(rows_file)).bound_keys() == ["aaaa1111", "bbbb2222"]


def test_the_map_write_leaves_no_temp_behind(mac, rows_file):
    """`~/.config/agbridge` is not the statedir, but a directory that fills up
    with `rows.tmp.*` is still a bug -- and a leftover temp is the evidence that
    `rename()` did not happen."""
    rows = mac.load_rows(str(rows_file))
    rows.bind("aaaa1111", "ROW-1")
    rows.save()
    siblings = os.listdir(os.path.dirname(str(rows_file)))
    assert [name for name in siblings if ".tmp." in name] == []


# ---------------------------------------------------------------------------
# the map has TWO writers: `agb bridge` and `agb close-done`
# ---------------------------------------------------------------------------

def _probe_lock(path):
    """True when nobody holds the map's lock right now."""
    fd = os.open(path + ".lock", os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (IOError, OSError):
        return False
    finally:
        os.close(fd)
    return True


def test_the_whole_read_modify_write_is_held_under_one_lock(
        mac, agb, rows_file, monkeypatch):
    """`save()` merges the disk copy and then writes it. Merging is not enough
    on its own: if `agb close-done` renames its file into place *between* the
    bridge's merge and the bridge's rename, the bridge writes the just-closed
    entry back -- and that does NOT self-heal, because `_merge_disk` drops an
    entry only when disk no longer has it and the bridge has just put it there.
    The result is a `[done]` entry naming a row agterm has already closed,
    which `close-done` can only keep failing on for ever.

    So the assertion is that the lock is held at BOTH ends of the window: while
    the disk copy is being read, and while the replacement is being written.
    """
    rows = mac.load_rows(str(rows_file))
    rows.bind("aaaa1111", "ROW-1")
    rows.save()                     # creates the directory and the lock file
    assert _probe_lock(str(rows_file)), "the lock must not outlive a save"

    free = []
    real_merge = mac.RowMap._merge_disk
    real_write = agb.atomic_write

    def merge(self):
        free.append(("merge", _probe_lock(str(rows_file))))
        return real_merge(self)

    def write(path, payload, *args, **kwargs):
        free.append(("write", _probe_lock(str(rows_file))))
        return real_write(path, payload, *args, **kwargs)

    monkeypatch.setattr(mac.RowMap, "_merge_disk", merge)
    monkeypatch.setattr(agb, "atomic_write", write)
    rows.bind("bbbb2222", "ROW-2")
    rows.save()
    assert free == [("merge", False), ("write", False)]


def test_the_other_writer_waits_for_the_map_lock(mac, rows_file):
    """Mutual exclusion from the outside, which is what makes the merge sound:
    while one process is inside its read-modify-write, the other's `save()`
    must not be able to reach its rename.

    The test plays `agb bridge`'s part by holding the lock itself, then runs a
    `save()` in a thread and asserts it is still waiting. Bounded joins
    throughout: a regression that never takes the lock finishes early and fails
    the `is_alive` assertion, and one that never releases it fails the second
    join rather than hanging the suite.
    """
    rows = mac.load_rows(str(rows_file))
    rows.bind("aaaa1111", "ROW-1")
    rows.save()

    held = os.open(str(rows_file) + ".lock", os.O_CREAT | os.O_WRONLY, 0o600)
    fcntl.flock(held, fcntl.LOCK_EX)
    other = mac.load_rows(str(rows_file))
    other.bind("bbbb2222", "ROW-2")
    # daemon: a regression that never releases the lock must fail the join
    # assertion below, not leave a live thread for the interpreter to wait on
    # at exit -- a hung suite reports nothing (see conftest).
    thread = threading.Thread(target=other.save)
    thread.daemon = True
    thread.start()
    try:
        thread.join(0.5)
        assert thread.is_alive(), "save() wrote the map while another process held the lock"
        assert mac.load_rows(str(rows_file)).bound_keys() == ["aaaa1111"]
    finally:
        os.close(held)
    thread.join(10)
    assert not thread.is_alive()
    assert mac.load_rows(str(rows_file)).bound_keys() == ["aaaa1111", "bbbb2222"]


def test_a_close_that_lands_mid_save_is_not_undone(mac, agb, rows_file):
    """The bug itself, end to end: `close-done` forgets a `[done]` row while
    the bridge is inside its own `save()`. The closed row must stay closed.

    Both orderings are correct once the window is one critical section -- the
    point is that the bridge can no longer write its stale entry over the top
    of a rename that has already happened.
    """
    seed = mac.load_rows(str(rows_file))
    seed.bind("aaaa1111", "ROW-1", "one")
    seed.bind("bbbb2222", "ROW-2", "two")
    seed.unbind("aaaa1111")                 # [done], what close-done reclaims
    seed.save()

    bridge_map = mac.load_rows(str(rows_file))
    bridge_map.set_title("bbbb2222", "renamed")

    def close_done():
        closer = mac.load_rows(str(rows_file))
        closer.forget("aaaa1111")           # agtermctl closed ROW-1
        closer.save()

    threads = []
    real_write = agb.atomic_write

    def racing(path, payload, *args, **kwargs):
        if not threads:
            thread = threading.Thread(target=close_done)
            thread.daemon = True
            threads.append(thread)
            thread.start()
            time.sleep(0.3)                 # room for an unlocked writer to land
        return real_write(path, payload, *args, **kwargs)

    agb.atomic_write = racing
    try:
        bridge_map.save()
    finally:
        agb.atomic_write = real_write
    threads[0].join(10)
    assert not threads[0].is_alive()
    assert mac.load_rows(str(rows_file)).known("aaaa1111") is False


# ---------------------------------------------------------------------------
# structural guards
# ---------------------------------------------------------------------------

def test_nothing_in_the_mac_module_unlinks_or_renames(mac_tree):
    """Stronger than the reachability form of this guard, and deliberately so:
    the renderer's methods are reached through instance attributes, which a
    call-graph walk cannot follow. The Mac side has **no** removal authority
    over the SHARED statedir -- every removal in this system is proven on the
    machine that owns the entry (constraint #11).

    ⚠️ Narrowed, not weakened. It used to forbid `atomic_write` outright, which
    also forced the row map to be written in place -- and that file's CONTENT is
    the whole key <-> row bijection, so a torn write loses it (orphan rows,
    duplicates, and `agb close-done` a silent no-op). `atomic_write` renames a
    temp *it created itself* over a Mac-local file under `~/.config`; it removes
    nothing of anyone else's, and it reaches no NFS path from here (the
    companion guard below forbids every statedir helper). So the exemption is
    granted by name to exactly the functions listed below, and the assertion
    that the list is exactly those is the part that keeps this honest.
    """
    users = []
    for name, node in conftest.functions(mac_tree).items():
        made = conftest.calls(node)
        for forbidden in (("os", "unlink"), ("os", "rename"), ("os", "remove"),
                          ("os", "rmdir")):
            assert forbidden not in made, "%s in %s" % (forbidden, name)
        if ("agb", "atomic_write") in made or (None, "atomic_write") in made:
            users.append(name)
    # `write_placements` is the second, and the exemption is granted on the
    # same terms: a placement file is CONTENT whose torn read would silently
    # move rows to the wrong workspace, it is Mac-local under ~/.config beside
    # the map, and it removes nothing of anyone else's. Sorted, because the
    # claim is which functions -- not what order the walk happened to find them.
    assert sorted(users) == ["save", "write_placements"], users


def test_nothing_in_the_mac_module_touches_the_shared_statedir(mac_tree):
    """Constraint #10: the Mac cannot read the NFS statedir at all -- it only
    ever sees the feed stream. Task 4a's version of this guard walks the call
    graph from `cmd_bridge`; this one covers the renderer, whose methods that
    walk cannot reach."""
    forbidden = set([
        "statedir", "ensure_statedir", "ensure_session_dir", "session_dir",
        "state_path", "record_path", "marker_path", "sweep_marker_path",
        "bridge_beat_path", "read_state_entry", "read_marker_keys",
        "list_marker_hosts", "rebuild_marker", "reap_entry", "sweep_entry",
        "feed_poll", "breadcrumb", "own_host",
    ])
    for name, node in conftest.functions(mac_tree).items():
        for _base, attr in conftest.calls(node):
            assert attr not in forbidden, "%s calls %s" % (name, attr)


def test_the_renderer_never_consults_the_macs_own_clock(mac_tree):
    """Every age on screen is `feed now - beat`, both server-stamped. The Mac's
    clock is a third domain and is used for exactly one thing in this file: the
    watchdog, which is a local timeout and is `time.monotonic` (Task 4a)."""
    funcs = conftest.functions(mac_tree)
    for name in ("beat_age", "beat_age_text", "row_title", "_render_upsert",
                 "_render_remove", "_render_stale", "_render_live",
                 "_render_tick", "_title", "_status", "__call__"):
        made = conftest.calls(funcs[name])
        assert ("time", "time") not in made, name
        assert ("time", "monotonic") not in made, name


def test_the_status_vocabulary_has_exactly_one_source(mac_tree, agb):
    """A second, hand-written copy of the four words is how the renderer and the
    rest of the tool come to disagree about what `idle` means -- and the
    vocabulary is the one thing here that is fixed by a program we do not own.

    The search covers the three ways a second copy would actually get written:
    a tuple/list/set of literals, a dict keyed by them, and one string split at
    runtime. Walking only the first shape left `"active blocked completed
    idle".split()` -- the shortest of the three -- invisible.
    """
    node = conftest.functions(mac_tree)["_status"]
    names = [child.attr for child in ast.walk(node)
             if isinstance(child, ast.Attribute)]
    assert "STATUS_VOCABULARY" in names

    vocabulary = set(agb.STATUS_VOCABULARY)
    assert len(vocabulary) > 1                 # a 1-word set would match "any"
    for child in ast.walk(mac_tree):
        words = set()
        if isinstance(child, (ast.Tuple, ast.List, ast.Set)):
            words = set(item.s for item in child.elts
                        if isinstance(item, ast.Str))
        elif isinstance(child, ast.Dict):
            words = set(item.s for item in child.keys
                        if isinstance(item, ast.Str))
        elif isinstance(child, ast.Str):
            # `"active blocked completed idle".split()` and its comma form.
            words = set(child.s.replace(",", " ").split())
        assert not vocabulary <= words, ast.dump(child)[:120]


def test_close_done_is_its_own_command_not_a_bridge_subcommand(agb_tree,
                                                               mac_tree):
    """`bridge` is the long-lived launchd job, so `agb bridge close-done` would
    start a second one."""
    funcs = conftest.functions(agb_tree)
    assert (None, "cmd_close_done") in conftest.calls(funcs["main"])
    mac_funcs = conftest.functions(mac_tree)
    assert "run_close_done" in mac_funcs
    assert (None, "run_close_done") not in conftest.calls(
        mac_funcs["run_bridge"])
    assert (None, "bridge_supervise") not in conftest.calls(
        mac_funcs["run_close_done"])


def test_the_row_map_is_not_json(mac_tree):
    """`agb._json()` is the tool's single import site and every caller of it is
    on a path that has to speak NDJSON. A three-field line needs no parser."""
    for name in ("format_rows", "parse_rows", "read_rows_file"):
        node = conftest.functions(mac_tree)[name]
        assert "_json" not in [attr for _base, attr in conftest.calls(node)]


def test_the_stub_exists_and_is_executable(repo_root):
    path = os.path.join(repo_root, "tests", "stubs", "agtermctl")
    assert os.path.exists(path)
    with open(path) as handle:
        body = handle.read()
    # the vocabulary rejection is the stub's whole reason for existing
    assert "active|blocked|completed|idle" in body
    assert "not a status" in body


# ---------------------------------------------------------------------------
# a restart during an outage: the persisted map without the per-process memory
# ---------------------------------------------------------------------------
#
# ⚠️ Every test above calls `b.upsert(...)` before asserting anything about
# `[?]`, so `RowRenderer.seen` is always populated and the case that actually
# happens in production was invisible. `seen` is per-process; the row map is a
# file. A launchd restart therefore starts with bound rows and an empty model,
# and the FIRST thing `bridge_supervise` reports when the Mac wakes before the
# farm is reachable is `spawn-failed` -- which is a `stale` op.

def persisted(mac, path, *pairs):
    """A rows file as a previous bridge process left it behind."""
    with open(str(path), "w") as handle:
        handle.write(mac.format_rows([(mac.ROW_BOUND, key, row)
                                      for key, row in pairs]))
    return path


def test_a_marked_row_with_no_identity_is_still_marked_with_its_key(
        mac, bridge, rows_file):
    """A version-1 map persists `kind\tkey\trow` and nothing else, so after a
    restart there is no identity to prefix. The `idle` below lands regardless --
    it needs no identity -- and a row painted `idle` with no `[?]` is
    pixel-identical to a live idle agent, which is the exact failure `[?]`
    exists to prevent. So the key stands in: A REFUSED RENAME AND AN APPLIED
    IDLE MUST NOT COEXIST."""
    persisted(mac, rows_file, ("a3f9c1e0", "ROW-1"))
    b = bridge()
    b.stale("spawn-failed")

    assert b.run.renames() == [("[?] a3f9c1e0", "ROW-1")]
    assert b.run.statuses() == [("idle", "ROW-1", False)]


def test_a_key_used_as_a_stand_in_is_never_remembered_as_the_identity(
        mac, bridge, rows_file):
    """The other half of the rule: the hex key is a last resort for *this*
    paint, not the row's name. Writing it into the map would make it
    permanent and defeat the upgrade to a version-2 file."""
    persisted(mac, rows_file, ("a3f9c1e0", "ROW-1"))
    b = bridge()
    b.stale("spawn-failed")
    assert b.rows.title_for("a3f9c1e0") == ""

    b.upsert(wire("a3f9c1e0"))
    assert b.rows.title_for("a3f9c1e0").startswith("build")


def test_the_feed_coming_back_does_not_retitle_a_row_it_never_saw_either(
        mac, bridge, rows_file):
    """`_render_live` applies no marker and no status to a row it has no record
    for, so there is nothing to keep in step: renaming it to its raw key would
    be a pure loss."""
    persisted(mac, rows_file, ("a3f9c1e0", "ROW-1"))
    b = bridge()
    b.stale("eof")
    before = len(b.run.renames())
    b.tick()                        # any line at all lifts the `[?]`

    assert len(b.run.renames()) == before


def test_a_tick_does_not_retitle_a_row_it_never_saw(mac, bridge, rows_file):
    """A tick fires every poll, so without this the raw key would be re-applied
    for as long as the bridge stayed up."""
    persisted(mac, rows_file, ("a3f9c1e0", "ROW-1"))
    b = bridge()
    b.tick(now=NOW + 3600)

    assert b.run.renames() == []


def test_the_first_real_record_titles_the_row_properly(mac, bridge, rows_file):
    """The other half: withholding the title must not become withholding it for
    ever. One upsert and the row gets its real identity."""
    persisted(mac, rows_file, ("a3f9c1e0", "ROW-1"))
    b = bridge()
    b.stale("spawn-failed")
    b.upsert(wire("a3f9c1e0", state="blocked"))

    assert b.run.renames()[-1][1] == "ROW-1"
    assert "build" in b.run.titles()[-1]
    assert b.rows.row_for("a3f9c1e0") == "ROW-1"        # not a second row
    assert b.run.news() == []


# ---------------------------------------------------------------------------
# the row map's own concurrency rules
# ---------------------------------------------------------------------------

def test_an_unreadable_rows_file_merges_nothing_rather_than_emptying_the_map(
        mac, bridge, rows_file, monkeypatch):
    """`_merge_disk`'s "unreadable = no information" arm. Changing its
    `return` to `entries = []` passes every other test in this file, and with
    that regression a transient read failure makes `save()` write back a map
    missing everything `agb close-done` had recorded -- defeating the lock-free
    concurrency argument in `RowMap.save`'s own docstring."""
    persisted(mac, rows_file, ("aaaa1111", "ROW-1"), ("bbbb2222", "ROW-2"))
    rows = mac.load_rows(str(rows_file))
    assert rows.loaded == set(["aaaa1111", "bbbb2222"])

    # The re-read at save time fails: a transient EIO, a half-written file, a
    # sibling process mid-rename. Nothing may be concluded from it.
    monkeypatch.setattr(mac, "read_rows_file", lambda path, warn=None: None)
    rows.bind("cccc3333", "ROW-3")
    rows.save()
    monkeypatch.undo()

    on_disk = mac.read_rows_file(str(rows_file))
    assert sorted(entry[1] for entry in on_disk) == [
        "aaaa1111", "bbbb2222", "cccc3333"]


def test_per_key_memory_is_reclaimed_when_the_map_forgets_a_key(mac, bridge,
                                                                rows_file):
    """`seen`/`applied`/`titles` are three dicts in a launchd-resident process
    that is restarted only by a crash or a reboot, so "one entry per agent ever
    seen, for ever" is a leak with no reclamation path at all.

    The map is the authority: every reader here reaches a row through
    `bound_keys()`/`row_for()`, so a key the map does not hold can never be
    rendered again. A `[done]` entry is deliberately still IN the map, so its
    memory survives until `agb close-done` actually closes it."""
    b = bridge()
    b.upsert(wire("aaaa1111", state="active"))
    b.remove("aaaa1111")                       # `[done]`: still in the map
    assert "aaaa1111" in b.renderer.seen
    assert "aaaa1111" in b.renderer.applied
    assert "aaaa1111" in b.renderer.titles

    b.rows.forget("aaaa1111")                  # what `close-done` does
    b.tick()

    assert b.renderer.seen == {}
    assert b.renderer.applied == {}
    assert b.renderer.titles == {}


def test_reclamation_never_drops_a_key_the_map_still_holds(mac, bridge):
    """The dangerous direction: dropping `applied` for a live row would make
    `--blink` fire on the next repaint of an agent that has been quietly active
    for an hour, and dropping `titles` would repaint every row on every tick."""
    b = bridge()
    b.upsert(wire("aaaa1111", state="active"))
    b.upsert(wire("bbbb2222", state="blocked"))
    b.tick()
    b.tick()
    b.upsert(wire("aaaa1111", state="blocked", seq=2))
    b.upsert(wire("aaaa1111", state="active", seq=3))

    assert sorted(b.renderer.seen) == ["aaaa1111", "bbbb2222"]
    assert sorted(b.renderer.applied) == ["aaaa1111", "bbbb2222"]
    assert sorted(b.renderer.titles) == ["aaaa1111", "bbbb2222"]
    # `applied` survived: the first `active` did not blink (a first paint never
    # does), the ticks emitted no status at all, and the return to `active`
    # blinked because the previous applied status was still remembered.
    assert [blink for _s, _r, blink in b.run.statuses()] == [
        False, False, False, True]


def _gone(*verbs):
    """A `Runner` that answers the way agterm does for a row it has forgotten."""
    return Runner(fail=verbs, err="error: no such session: ROW-1")


def test_a_row_agterm_has_forgotten_is_named_once_with_the_way_back(mac, bridge,
                                                                    rows_file):
    """A bound entry whose row agterm no longer knows -- closed by hand, or lost
    with an agterm restart. The failure has to be legible, and "legible" means
    naming the row, the map and the command that recovers it."""
    persisted(mac, rows_file, ("a3f9c1e0", "ROW-1"))
    b = bridge(_gone("rename", "status"))
    b.upsert(wire("a3f9c1e0"))

    hints = [text for text in b.warned if str(rows_file) in text]
    assert hints, b.warned
    assert "ROW-1" in hints[0]
    assert "agb-refresh" in hints[0]


def test_a_dead_row_is_never_written_to_again(mac, bridge, rows_file):
    """The whole point. Before this, every poll re-sent a rename and a status to
    a row agterm had already refused, filling the launchd log with thousands of
    identical lines and hiding the one that mattered -- twice in one week."""
    persisted(mac, rows_file, ("a3f9c1e0", "ROW-1"))
    b = bridge(_gone("rename", "status"))
    b.upsert(wire("a3f9c1e0"))
    after_first = len(b.run.agterm())
    assert after_first, "nothing was even attempted"

    warned_first = len(b.warned)

    for seq in range(2, 8):
        b.upsert(wire("a3f9c1e0", state="active", seq=seq))
    assert len(b.run.agterm()) == after_first, b.run.verbs()
    # The first failure says two things -- what failed, and the way back. Six
    # more polls must add neither.
    assert len(b.warned) == warned_first, b.warned[warned_first:]
    assert len([t for t in b.warned if "agb-refresh" in t]) == 1


def test_a_dead_row_keeps_its_binding_so_the_row_stays_gone(mac, bridge,
                                                            rows_file):
    """Closing a row is how a human dismisses it. Forgetting the binding would
    have the bridge mint a replacement within seconds, which is the opposite of
    what closing it meant. `agb-refresh` is the deliberate way back."""
    persisted(mac, rows_file, ("a3f9c1e0", "ROW-1"))
    b = bridge(_gone("rename", "status"))
    b.upsert(wire("a3f9c1e0"))
    b.upsert(wire("a3f9c1e0", state="active", seq=2))

    assert b.rows.row_for("a3f9c1e0") == "ROW-1"
    assert "new" not in b.run.verbs(), "a replacement row was minted"


def test_an_ordinary_failure_never_marks_a_row_dead(mac, bridge, rows_file):
    """⚠️ The guard. `agtermctl` exits 1 for *every* failure, so the match is on
    agterm's own words. A missing binary, a hung call or a permissions problem
    must keep being retried -- otherwise one broken `agtermctl` would silently
    stop the bridge painting anything at all, which is far worse than the noise
    this replaces."""
    persisted(mac, rows_file, ("a3f9c1e0", "ROW-1"))
    b = bridge(Runner(fail=("rename", "status")))       # generic stderr
    b.upsert(wire("a3f9c1e0"))
    after_first = len(b.run.agterm())
    b.upsert(wire("a3f9c1e0", state="active", seq=2))

    assert len(b.run.agterm()) > after_first, "it gave up on a transient failure"
    assert b.renderer.dead == set()


def test_a_failure_with_no_target_does_not_produce_the_hint(mac, bridge):
    """`session new` has no `--target`: there is no stale row id to blame, and
    telling somebody to delete their row map because agterm would not start a
    row would be advice that loses data for nothing."""
    b = bridge(Runner(fail=("new",)))
    b.upsert(wire("a3f9c1e0"))

    assert [text for text in b.warned if "delete" in text] == []


# ---------------------------------------------------------------------------
# `agb forget-rows` -- the recovery when agterm has dropped its rows
# ---------------------------------------------------------------------------

def _seeded(mac, tmp_path):
    rows = mac.RowMap(str(tmp_path / "rows"))
    rows.bind("aaaa1111", "ROW-1", "build · box2")
    rows.bind("bbbb2222", "ROW-2", "docs · box2")
    rows.save(force=True)
    return str(tmp_path / "rows")


def test_forget_rows_drops_every_binding_by_default(mac, tmp_path):
    """agterm was closed or reset: every id in the map is dead, so the whole
    map goes and the next snapshot re-creates the rows."""
    path = _seeded(mac, tmp_path)
    out = _RowOut()
    assert mac.run_forget_rows(["--rows", path], out=out) == 0
    assert mac.load_rows(path).bound_keys() == []
    assert "aaaa1111" in out.text and "bbbb2222" in out.text


def test_forget_rows_can_target_one_key(mac, tmp_path):
    """One row closed while the others are still live: dropping the whole map
    would mint duplicates for the survivors."""
    path = _seeded(mac, tmp_path)
    assert mac.run_forget_rows(["--rows", path, "--key", "aaaa1111"],
                               out=_RowOut()) == 0
    rows = mac.load_rows(path)
    assert rows.bound_keys() == ["bbbb2222"]
    assert rows.row_for("bbbb2222") == "ROW-2"


def test_forget_rows_keeps_the_sentinel_correct(mac, tmp_path):
    """The reason this is not `sed`: the map ends in `#end <count>`, and a
    hand-edited line leaves the count wrong -- which makes the whole map read as
    corrupt and discards the bindings that were meant to survive."""
    path = _seeded(mac, tmp_path)
    mac.run_forget_rows(["--rows", path, "--key", "aaaa1111"], out=_RowOut())
    text = open(path).read()
    assert text.rstrip().endswith("#end 1")
    assert mac.load_rows(path).known("bbbb2222")     # still readable


def test_forget_rows_dry_run_changes_nothing(mac, tmp_path):
    path = _seeded(mac, tmp_path)
    before = open(path).read()
    out = _RowOut()
    assert mac.run_forget_rows(["--rows", path, "--dry-run"], out=out) == 0
    assert open(path).read() == before
    assert "would forget" in out.text


def test_forget_rows_names_a_key_it_does_not_hold(mac, tmp_path):
    """A typo must be said out loud rather than reported as a successful
    no-op -- the operator is trying to fix a row that will not come back."""
    path = _seeded(mac, tmp_path)
    out = _RowOut()
    code = mac.run_forget_rows(["--rows", path, "--key", "nosuchkey"], out=out)
    assert code == 1
    assert "not in the map" in out.text
    assert mac.load_rows(path).bound_keys() == ["aaaa1111", "bbbb2222"]


def test_forget_rows_on_an_empty_map_is_not_an_error(mac, tmp_path):
    out = _RowOut()
    assert mac.run_forget_rows(["--rows", str(tmp_path / "none")],
                               out=out) == 0
    assert "already empty" in out.text


class _RowOut(object):
    def __init__(self):
        self.text = ""

    def write(self, data):
        self.text += data

    def flush(self):
        pass


def test_forget_rows_uses_the_default_map_when_none_is_named(mac, tmp_path,
                                                             monkeypatch):
    """⚠️ Regression, found in live use. `read_rows_file(None)` answers `[]` --
    "there is no map" -- so passing the unresolved `--rows` default straight
    through reported EVERY map as empty and forgot nothing, while saying it had
    succeeded. `agb-refresh` never worked, silently, which is the exact failure
    class this tool exists to remove.

    Every earlier test passed `--rows` explicitly, so none of them went near the
    default. That is what made it invisible.
    """
    home = tmp_path / "home"
    (home / ".config" / "agbridge").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    rows = mac.RowMap(mac.rows_path())
    rows.bind("aaaa1111", "ROW-1", "one")
    rows.save(force=True)

    out = _RowOut()
    assert mac.run_forget_rows([], out=out) == 0        # no --rows at all
    assert "already empty" not in out.text
    assert "aaaa1111" in out.text
    assert mac.load_rows(mac.rows_path()).bound_keys() == []


def test_forget_rows_also_forgets_done_entries(mac, tmp_path):
    """⚠️ Regression: `done_entries()` yields (key, row) PAIRS, and this read
    them as dicts. Every earlier test seeded only `bound` rows, so the line
    never executed until a real map with a `[done]` entry reached it -- and then
    it raised TypeError rather than doing anything.

    It matters that they are included: a `[done]` entry is exactly what survives
    when agterm has forgotten the row, which is what `agb-refresh` is for.
    """
    path = str(tmp_path / "rows")
    rows = mac.RowMap(path)
    rows.bind("aaaa1111", "ROW-1", "live")
    rows.bind("bbbb2222", "ROW-2", "finished")
    rows.unbind("bbbb2222")                     # -> done
    rows.save(force=True)
    assert mac.load_rows(path).done_entries()   # the fixture really has one

    out = _RowOut()
    assert mac.run_forget_rows(["--rows", path], out=out) == 0
    after = mac.load_rows(path)
    assert after.bound_keys() == [] and after.done_entries() == []
    assert "bbbb2222" in out.text


def test_forget_rows_names_the_row_id_of_a_done_entry(mac, tmp_path):
    """`row_for` answers only for BOUND rows, so a [done] entry printed `?`.
    The id is in the entry, and naming it is the whole point of the line."""
    path = str(tmp_path / "rows")
    rows = mac.RowMap(path)
    rows.bind("bbbb2222", "ROW-2", "finished")
    rows.unbind("bbbb2222")
    rows.save(force=True)
    out = _RowOut()
    mac.run_forget_rows(["--rows", path, "--dry-run"], out=out)
    assert "ROW-2" in out.text
    assert "?" not in out.text


def test_forget_rows_closes_the_agterm_session_before_forgetting_it(mac,
                                                                    tmp_path):
    """⚠️ Two claims, and the second is the subtle one.

    First: it closes at all. The original version only dropped the mapping, so
    agterm kept the session and the bridge minted a fresh row beside it -- the
    operator was left closing duplicates by hand.

    Second: the row id is READ from the map before anything forgets it. That
    read is the load-bearing step, not the order of the close and the forget:
    the id lives only in the map, so a version that forgot first would close
    nothing and could never name that session again. Mutating the *read* past
    the forget fails this test; swapping the two calls does not, because the id
    is already in hand by then."""
    path = str(tmp_path / "rows")
    rows = mac.RowMap(path)
    rows.bind("aaaa1111", "ROW-1", "one")
    rows.save(force=True)

    seen = []

    def run(argv):
        # what the map still holds AT THE MOMENT of the close
        seen.append((argv, mac.load_rows(path).known("aaaa1111")))
        return (0, "", "")

    out = _RowOut()
    assert mac.run_forget_rows(["--rows", path], out=out, run=run) == 0
    closes = [(argv, known) for argv, known in seen
              if argv[1:3] == ["session", "close"]]
    assert len(closes) == 1
    argv, still_known = closes[0]
    assert argv == ["agtermctl", "session", "close", "--target", "ROW-1"]
    assert still_known                        # closed while it was still named
    assert mac.load_rows(path).bound_keys() == []
    assert "closed row ROW-1" in out.text


def test_a_close_that_fails_is_not_fatal(mac, tmp_path):
    """agterm having already dropped the row is the ORIGINAL reason this command
    exists, so a failing close is the expected answer there, not an error."""
    path = str(tmp_path / "rows")
    rows = mac.RowMap(path)
    rows.bind("aaaa1111", "ROW-1", "one")
    rows.save(force=True)
    out = _RowOut()
    assert mac.run_forget_rows(
        ["--rows", path], out=out,
        run=lambda argv: (1, "", "error: no such session: ROW-1")) == 0
    assert mac.load_rows(path).bound_keys() == []      # forgotten anyway
    assert "not closed" in out.text


def test_no_close_leaves_the_session_alone(mac, tmp_path):
    path = str(tmp_path / "rows")
    rows = mac.RowMap(path)
    rows.bind("aaaa1111", "ROW-1", "one")
    rows.save(force=True)
    calls = []
    # The runner answers (rc, stdout, stderr): `tree_workspaces` reads all three.
    assert mac.run_forget_rows(
        ["--rows", path, "--no-close"], out=_RowOut(),
        run=lambda argv: (calls.append(argv), (0, "", ""))[1]) == 0
    assert [c for c in calls if c[1:3] == ["session", "close"]] == []
    assert mac.load_rows(path).bound_keys() == []


def test_a_dry_run_closes_nothing(mac, tmp_path):
    path = str(tmp_path / "rows")
    rows = mac.RowMap(path)
    rows.bind("aaaa1111", "ROW-1", "one")
    rows.save(force=True)
    calls = []
    mac.run_forget_rows(["--rows", path, "--dry-run"], out=_RowOut(),
                        run=lambda argv: (calls.append(argv), (0, "", ""))[1])
    assert calls == []
    assert mac.load_rows(path).bound_keys() == ["aaaa1111"]


def test_a_new_row_never_steals_the_selection(bridge):
    """`--no-select`: an agent starting on the farm is something to notice in
    the sidebar, not an interruption. Without it every row creation focuses the
    new session, and a refresh recreating several yanks the selection once per
    row."""
    b = bridge()
    b.upsert(wire("aaaa1111"))
    created = [c for c in b.run.agterm() if c[1:3] == ["session", "new"]][0]
    assert "--no-select" in created


def test_rows_go_to_the_configured_workspace(bridge):
    """Without a workspace agterm uses whichever one is current, so rows
    recreated by a refresh land wherever the operator happened to be looking."""
    b = bridge(settings={"workspace": "agents"})
    b.upsert(wire("aaaa1111"))
    created = [c for c in b.run.agterm() if c[1:3] == ["session", "new"]][0]
    assert "--workspace-name" in created
    assert created[created.index("--workspace-name") + 1] == "agents"
    # by NAME with --create-workspace: an id is not something a human puts in a
    # config file, and creating-if-absent makes the setting idempotent.
    assert "--create-workspace" in created
    assert "--workspace" not in created        # mutually exclusive with the name


def test_no_workspace_configured_means_no_workspace_flag(bridge):
    b = bridge()
    b.upsert(wire("aaaa1111"))
    created = [c for c in b.run.agterm() if c[1:3] == ["session", "new"]][0]
    assert not [a for a in created if a.startswith("--workspace")]
    assert "--create-workspace" not in created


# ---------------------------------------------------------------------------
# remembered placements: a refresh must put rows back where they were
# ---------------------------------------------------------------------------

def bridge_with(mac, tmp_path, settings):
    """A Harness whose renderer sees `settings` (workspace, placements path)."""
    return Harness(mac, tmp_path / "rows-ws", None, settings)

TREE_JSON = (
    '{"ok":true,"result":{"tree":{"workspaces":['
    '{"name":"working repos","id":"W1","sessions":['
    '{"id":"ROW-1","name":"one"},{"id":"ROW-2","name":"two"}]},'
    '{"name":"agbridge","id":"W2","sessions":[{"id":"ROW-3","name":"three"}]},'
    '{"name":"empty","id":"W3","sessions":[]}]}}}')


def test_tree_workspaces_maps_row_ids_to_workspace_names(mac):
    got = mac.tree_workspaces(run=lambda argv: (0, TREE_JSON, ""))
    assert got == {"ROW-1": "working repos", "ROW-2": "working repos",
                   "ROW-3": "agbridge"}


@pytest.mark.parametrize("answer", [
    (1, TREE_JSON, "boom"),          # agtermctl failed
    (0, "", ""),                     # nothing on stdout
    (0, "not json", ""),             # unparseable
    (0, '{"ok":true}', ""),          # no tree
    (0, '{"result":{"tree":{"workspaces":"nope"}}}', ""),
])
def test_an_unreadable_tree_is_none_not_empty(mac, answer):
    """None means "could not ask", which must leave remembered placements alone.
    An empty dict would erase every one of them."""
    assert mac.tree_workspaces(run=lambda argv: answer) is None


def test_forget_rows_records_where_each_row_lived(mac, tmp_path):
    path = str(tmp_path / "rows")
    places = str(tmp_path / "placements")
    rows = mac.RowMap(path)
    rows.bind("aaaa1111", "ROW-1", "one")
    rows.bind("bbbb2222", "ROW-3", "three")
    rows.save(force=True)

    def run(argv):
        if argv[1] == "tree":
            return (0, TREE_JSON, "")
        return (0, "", "")

    out = _RowOut()
    assert mac.run_forget_rows(["--rows", path, "--placements", places],
                               out=out, run=run) == 0
    assert mac.read_placements(places) == {"aaaa1111": "working repos",
                                           "bbbb2222": "agbridge"}
    assert "remembered the workspace of 2 rows" in out.text


def test_the_tree_is_read_before_any_row_is_closed(mac, tmp_path):
    """Once a session is closed its workspace is as unknowable as its id, so the
    order here is the difference between remembering and not."""
    path = str(tmp_path / "rows")
    rows = mac.RowMap(path)
    rows.bind("aaaa1111", "ROW-1", "one")
    rows.save(force=True)
    order = []

    def run(argv):
        order.append(argv[1])
        return (0, TREE_JSON if argv[1] == "tree" else "", "")

    mac.run_forget_rows(["--rows", path, "--placements",
                         str(tmp_path / "p")], out=_RowOut(), run=run)
    assert order.index("tree") < order.index("session")


def test_a_tree_that_cannot_be_read_leaves_placements_alone(mac, tmp_path):
    """Erasing them would scatter every row on the next snapshot -- worse than
    the problem this feature exists to solve."""
    path = str(tmp_path / "rows")
    places = str(tmp_path / "placements")
    mac.write_placements({"aaaa1111": "kept"}, places)
    rows = mac.RowMap(path)
    rows.bind("aaaa1111", "ROW-1", "one")
    rows.save(force=True)
    out = _RowOut()
    mac.run_forget_rows(["--rows", path, "--placements", places], out=out,
                        run=lambda argv: (1, "", "no"))
    assert mac.read_placements(places) == {"aaaa1111": "kept"}
    assert "left as they stand" in out.text


def test_a_remembered_placement_beats_the_configured_workspace(mac, tmp_path):
    """The operator moved that row on purpose; `workspace` is only where rows
    are born."""
    places = str(tmp_path / "placements")
    mac.write_placements({"aaaa1111": "working repos"}, places)
    b = bridge_with(mac, tmp_path, {"workspace": "agents",
                                    "placements": places})
    b.upsert(wire("aaaa1111"))
    created = [c for c in b.run.agterm() if c[1:3] == ["session", "new"]][0]
    assert created[created.index("--workspace-name") + 1] == "working repos"


def test_a_key_with_no_placement_falls_back_to_the_config(mac, tmp_path):
    places = str(tmp_path / "placements")
    mac.write_placements({"zzzz9999": "elsewhere"}, places)
    b = bridge_with(mac, tmp_path, {"workspace": "agents",
                                    "placements": places})
    b.upsert(wire("aaaa1111"))
    created = [c for c in b.run.agterm() if c[1:3] == ["session", "new"]][0]
    assert created[created.index("--workspace-name") + 1] == "agents"


@pytest.mark.parametrize("name", ["", "has=equals", "with\nnewline",
                                  " leading", "trailing ", "x" * 101])
def test_workspace_names_that_would_break_the_file_are_refused(mac, name):
    """The placement file is `key = value`, so `=` would split the line in the
    wrong place and a control character would break it outright."""
    assert not mac.valid_workspace(name)


@pytest.mark.parametrize("name", ["agents", "working repos", "w4", "Ünïcode"])
def test_ordinary_workspace_names_are_accepted(mac, name):
    assert mac.valid_workspace(name)


def test_placements_survive_a_round_trip(mac, tmp_path):
    path = str(tmp_path / "placements")
    places = {"aaaa1111": "working repos", "bbbb2222": "agbridge"}
    mac.write_placements(places, path)
    assert mac.read_placements(path) == places


def test_a_malformed_placement_line_is_skipped_not_fatal(mac, tmp_path):
    """Losing a placement costs a row its position, not its row."""
    path = tmp_path / "placements"
    path.write_text("aaaa1111 = good\nnot a key = x\nbbbb2222 = also good\n")
    assert mac.read_placements(str(path)) == {"aaaa1111": "good",
                                              "bbbb2222": "also good"}


# ---------------------------------------------------------------------------
# `--config` on the row-map commands
# ---------------------------------------------------------------------------
#
# Two bridges on one Mac keep two maps, and these are the two commands that
# repair a map. Given only `--rows`, repairing instance B means naming three
# paths by hand and getting all three right; `--config` names the instance once
# and derives them.
#
# ⚠️ The one that must not be skipped is `placements`. `agb-refresh` passes no
# `--placements` at all, so a `--config` that derived only `rows` would have
# `forget-rows` read instance A's placements, add instance B's rows to them and
# write the result back over A's file -- the recovery command corrupting the
# instance it was not run against, while reporting success in the usual words.

def _done_map(mac, path, key, row):
    rows = mac.RowMap(path)
    rows.bind(key, row, "title")
    rows.unbind(key)
    rows.save(force=True)


def _bound_map(mac, path, key, row):
    rows = mac.RowMap(path)
    rows.bind(key, row, "title")
    rows.save(force=True)


def _tree_run(argv):
    """agterm answers the tree, and closes whatever it is asked to close."""
    return (0, TREE_JSON if argv[1] == "tree" else "", "")


def test_close_done_reclaims_the_map_beside_its_config(mac, agb,
                                                       instance_config):
    """The rows map follows `--config`, and the *other* instance's map is not
    touched -- which is what makes the default map's surviving `[done]` entry
    the non-vacuity check: there was something there to close by mistake."""
    default = instance_config()
    hostb = instance_config("hostb")
    _done_map(mac, mac.rows_path(default), "aaaa1111", "ROW-1")
    _done_map(mac, mac.rows_path(hostb), "bbbb2222", "ROW-3")

    runner = Runner()
    out = Out()
    assert mac.run_close_done(["--config", hostb], run=runner, out=out) == 0
    assert runner.agterm() == [["agtermctl", "session", "close",
                                "--target", "ROW-3"]]
    assert mac.load_rows(mac.rows_path(hostb)).done_entries() == []
    assert mac.load_rows(mac.rows_path(default)).done_entries() == [
        ("aaaa1111", "ROW-1")]


def test_forget_rows_follows_its_config_to_the_map_and_the_placements(
        mac, agb, instance_config):
    """⚠️ The worst defect this task closes, asserted at both ends.

    `agb-refresh --instance hostb` runs exactly this. If `--config` derived
    only the rows path, instance B's `key = workspace` lines would land in
    instance A's placements file -- so the assertion that matters most is the
    one about the file that was NOT named on the command line.
    """
    default = instance_config()
    hostb = instance_config("hostb")
    mac.write_placements({"aaaa1111": "kept"}, mac.placements_path(default))
    _bound_map(mac, mac.rows_path(default), "aaaa1111", "ROW-1")
    _bound_map(mac, mac.rows_path(hostb), "bbbb2222", "ROW-3")

    out = _RowOut()
    assert mac.run_forget_rows(["--config", hostb], out=out,
                               run=_tree_run) == 0
    # the instance's own two files, both moved
    assert mac.load_rows(mac.rows_path(hostb)).bound_keys() == []
    assert mac.read_placements(mac.placements_path(hostb)) == {
        "bbbb2222": "agbridge"}
    # ...and the other instance's two files, neither touched
    assert mac.load_rows(mac.rows_path(default)).bound_keys() == ["aaaa1111"]
    assert mac.read_placements(mac.placements_path(default)) == {
        "aaaa1111": "kept"}


def test_an_explicit_rows_or_placements_still_beats_the_config(
        mac, tmp_path, instance_config):
    """Both flags predate `--config` and are a debugging seam: pointing one
    somewhere else is a deliberate act, so it wins."""
    hostb = instance_config("hostb")
    _bound_map(mac, mac.rows_path(hostb), "bbbb2222", "ROW-3")
    rows = str(tmp_path / "elsewhere-rows")
    places = str(tmp_path / "elsewhere-placements")
    _bound_map(mac, rows, "aaaa1111", "ROW-1")

    out = _RowOut()
    assert mac.run_forget_rows(["--config", hostb, "--rows", rows,
                                "--placements", places], out=out,
                               run=_tree_run) == 0
    assert mac.load_rows(rows).bound_keys() == []
    assert mac.read_placements(places) == {"aaaa1111": "working repos"}
    # the config's own files are exactly as they were
    assert mac.load_rows(mac.rows_path(hostb)).bound_keys() == ["bbbb2222"]
    assert not os.path.exists(mac.placements_path(hostb))


def test_close_done_with_an_explicit_rows_ignores_the_config(
        mac, tmp_path, instance_config):
    hostb = instance_config("hostb")
    _done_map(mac, mac.rows_path(hostb), "bbbb2222", "ROW-3")
    rows = str(tmp_path / "elsewhere-rows")
    _done_map(mac, rows, "aaaa1111", "ROW-1")

    runner = Runner()
    assert mac.run_close_done(["--config", hostb, "--rows", rows], run=runner,
                              out=Out()) == 0
    assert runner.agterm() == [["agtermctl", "session", "close",
                                "--target", "ROW-1"]]
    assert mac.load_rows(mac.rows_path(hostb)).done_entries() == [
        ("bbbb2222", "ROW-3")]


def _run_row_command(mac, name, argv, out):
    if name == "close-done":
        return mac.run_close_done(argv, run=Runner(), out=out)
    return mac.run_forget_rows(argv, out=out, run=_tree_run)


@pytest.mark.parametrize("name", ["close-done", "forget-rows"])
def test_a_row_map_command_says_which_instance_it_acted_on(
        mac, name, instance_config):
    """The mitigation for the one silent failure this shape has: a helper run
    without `--instance` repairs the *default* instance and reports success in
    exactly the words it would have used on the right one. So the banner is
    unconditional, and it names the paths rather than the flag."""
    hostb = instance_config("hostb")
    out = _RowOut()
    _run_row_command(mac, name, ["--config", hostb], out)
    assert hostb in out.text
    assert mac.rows_path(hostb) in out.text


@pytest.mark.parametrize("name", ["close-done", "forget-rows"])
def test_the_banner_names_the_default_config_when_no_flag_is_passed(
        mac, agb, name, instance_config):
    """`opts["config"]` is `None` on every run that passes no flag, and the
    banner is printed on every one of those runs -- so resolving it is not
    cosmetic: the unresolved value prints `config None`, which names no file
    and is worse than saying nothing."""
    # Non-vacuity, and the one place `instance_config`'s no-name branch is
    # pinned: it has to BE `agb.config_path()`, or this sets up a file the
    # command never reads while asserting on a path it prints regardless.
    assert instance_config() == agb.config_path()
    out = _RowOut()
    _run_row_command(mac, name, [], out)
    assert agb.config_path() in out.text
    assert "None" not in out.text


def test_forget_rows_names_the_placements_file_it_will_write(mac,
                                                             instance_config):
    """The file nobody named on the command line is the one worth printing:
    it is the one that would be wrong if `--config` derived only `rows`."""
    hostb = instance_config("hostb")
    out = _RowOut()
    mac.run_forget_rows(["--config", hostb], out=out, run=_tree_run)
    assert mac.placements_path(hostb) in out.text


@pytest.mark.parametrize("name", ["close-done", "forget-rows"])
def test_a_dry_run_says_which_instance_it_would_have_acted_on(
        mac, name, instance_config):
    """⚠️ The dry run is the run that most needs the banner, and the easiest
    one to lose it on.

    `--dry-run` is what an operator who is unsure which instance they are on
    actually types, and both commands answer it from the map they resolved --
    so a banner printed after the dry-run branch, or skipped for it, would
    withhold the answer from precisely the question being asked. It is emitted
    before anything is read for exactly that reason.
    """
    hostb = instance_config("hostb")
    _done_map(mac, mac.rows_path(hostb), "bbbb2222", "ROW-3")
    out = _RowOut()
    _run_row_command(mac, name, ["--config", hostb, "--dry-run"], out)
    assert hostb in out.text
    assert mac.rows_path(hostb) in out.text
    # Non-vacuity: it really was a dry run -- the entry is still there.
    assert mac.load_rows(mac.rows_path(hostb)).done_entries() == [
        ("bbbb2222", "ROW-3")]


# ---------------------------------------------------------------------------
# the stale-row hint names the instance whose log it is written into
# ---------------------------------------------------------------------------

def test_the_stale_row_hint_names_this_instances_own_refresh(
        mac, instance_config):
    """⚠️ A fixed `Run agb-refresh` is instance A's recipe in instance B's log.

    Followed literally from B's log it *succeeds*: it stops `com.agbridge`,
    forgets A's bindings and restarts it, while B's stale row is as stale as it
    was. Limitation 1's declared mitigation is that the output names the
    instance, and the bridge is the one process that certainly knows its own
    config path.

    ⚠️ This asserts the string's SHAPE, and that is not enough on its own: the
    recipe is a command a human types, and `--config` does not move the launchd
    label by itself. What the string *does* -- run through `agb-refresh`'s own
    resolution -- is asserted in
    `tests/test_agb_refresh.py::test_the_bridges_own_stale_row_recipe_acts_on_the_bridge_that_printed_it`,
    which is the guard that matters. An earlier version of this recipe passed
    the assertion below while bouncing the default job and forgetting THIS
    instance's map underneath its own live bridge.
    """
    hostb = instance_config("hostb")
    warnings = []
    renderer = mac.RowRenderer(
        mac.BridgeModel(), mac.RowMap(mac.rows_path(hostb)),
        run=Runner(fail=["rename"], err="error: no such session: ROW-3"),
        warn=warnings.append, settings={"config": hostb})
    renderer._agtermctl(["session", "rename", "t", "--target", "ROW-3"])
    hint = [line for line in warnings if "is gone from agterm" in line]
    assert hint, warnings
    assert "agb-refresh --config %s" % (hostb,) in hint[0]


def test_the_stale_row_hint_stays_short_for_the_default_instance(
        mac, agb, instance_config):
    """The other half, and the reason the flag is conditional: a default
    install's advice must not grow a flag it does not need -- the same rule
    `pane_argv` follows, so that nothing about a one-instance Mac changes."""
    instance_config()
    warnings = []
    renderer = mac.RowRenderer(
        mac.BridgeModel(), mac.RowMap(mac.rows_path(agb.config_path())),
        run=Runner(fail=["rename"], err="error: no such session: ROW-1"),
        warn=warnings.append, settings={"config": agb.config_path()})
    renderer._agtermctl(["session", "rename", "t", "--target", "ROW-1"])
    hint = [line for line in warnings if "is gone from agterm" in line]
    assert hint, warnings
    assert "Run agb-refresh to" in hint[0]
    assert "--config" not in hint[0]
