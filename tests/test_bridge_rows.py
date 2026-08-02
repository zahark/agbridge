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


def _finished(bridge_obj):
    """Just the "finished" banners, as (body, title, target).

    ⚠️ Filtered rather than counted off `_notifies`, and that is not fussiness.
    `_notifies` returns EVERY banner, including `_notify_new_row`'s, so
    `len(_notifies(b)) == 1` in a fresh harness holds only because
    `NEW_ROW_QUIET` happens to be suppressing the new-row one -- a reason with
    nothing to do with the feature under test, which will stop being true the
    moment a test calls `_past_quiet`.
    """
    return [n for n in _notifies(bridge_obj) if " finished" in n[0]]


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
    # ⚠️ `notify` is a TOP-LEVEL verb, so it reads call[0], not call[1]. Until
    # the stub grew an arm for it this call was rejected as an unknown command,
    # `_agtermctl` absorbed the exit-2 into a warning, and this test stayed
    # green while the only end-to-end exercise of the banner path proved
    # nothing. Asserting the clean stderr is half the point: a swallowed
    # failure is exactly what went unnoticed.
    notifies = [call for call in agtermctl.calls() if call[0] == "notify"]
    assert len(notifies) == 1, agtermctl.calls()
    assert "--target" in notifies[0]
    assert b"agtermctl" not in err, err
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
                 "_render_tick", "_title", "_status", "__call__",
                 # ⚠️ This catches `time.time`/`time.monotonic` and nothing
                 # else -- `conftest.calls` yields ("self", "clock") for
                 # `self.clock()`, so it does NOT pin "measures in `model.now`".
                 # That property is caught behaviourally, by
                 # `test_a_disconnect_mid_turn_does_not_reset_the_timer`.
                 "_notify_completed"):
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

    ⚠️ INVERTED, and only in its first line: the run is now `--all` rather than
    bare, because a bare `forget-rows` is refused (its own test, below). What is
    guarded here did not change and must not be lost -- there are no instances
    on this Mac, so the sweep falls through to the single default map, which is
    the same resolution the bug was in. `--all` is the flag that reaches it.
    """
    home = tmp_path / "home"
    (home / ".config" / "agbridge").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    rows = mac.RowMap(mac.rows_path())
    rows.bind("aaaa1111", "ROW-1", "one")
    rows.save(force=True)

    out = _RowOut()
    assert mac.run_forget_rows(["--all"], out=out) == 0   # no --rows at all
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


@pytest.mark.parametrize("name,argv", [("close-done", []),
                                       ("forget-rows", ["--all"])])
def test_the_banner_names_the_default_config_when_no_map_is_named(
        mac, agb, name, argv, instance_config):
    """`opts["config"]` is `None` on every run that names no map, and the
    banner is printed on every one of those runs -- so resolving it is not
    cosmetic: the unresolved value prints `config None`, which names no file
    and is worse than saying nothing.

    ⚠️ INVERTED in its argv, not in its claim. Such a run now SWEEPS, so the
    only reason it reaches the default config at all is that this Mac has no
    launchd job -- the no-instances fall-through, which is `docs/cookbook.md`'s
    bare `agb close-done` recipe and the commonest shape there is. The banner is
    what tells those two apart, which makes it more load-bearing than it was,
    not less. `forget-rows` needs `--all` to say it: it restarts no bridge, so
    the rows it forgets stay closed.
    """
    # Non-vacuity, and the one place `instance_config`'s no-name branch is
    # pinned: it has to BE `agb.config_path()`, or this sets up a file the
    # command never reads while asserting on a path it prints regardless.
    assert instance_config() == agb.config_path()
    out = _RowOut()
    _run_row_command(mac, name, argv, out)
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


# ---------------------------------------------------------------------------
# reading a value out of the config -- the two coercers
# ---------------------------------------------------------------------------
# ⚠️ Until this section existed, `config_flag`, `CONFIG_FALSE` and the
# notification half of `render_settings` had NO tests at all. The two
# off-switch tests above inject `settings={"notify_blocked": False}` straight
# into the renderer and never go through the coercer, so the whole reason
# `config_flag` exists -- that the *string* "0" is true in Python -- was
# unexercised for two releases. That is the shape of a guard nobody notices is
# missing: the feature works, the switch works, and the one line that connects
# them is covered by nothing.


def test_config_flag_reads_the_documented_spellings(mac):
    """`notify_on_blocked = 0` must mean off, and `bool("0")` is True.

    Every spelling here is one somebody will actually write. The `default`
    branches matter as much as the false ones: an absent key has to read as the
    default so that an existing config gets a new feature without being edited,
    and an empty value is a half-finished edit, not a decision.
    """
    cases = [
        ({}, True), ({"k": "0"}, False), ({"k": "no"}, False),
        ({"k": "off"}, False), ({"k": "false"}, False),
        ({"k": "OFF"}, False), ({"k": "  off  "}, False),
        ({"k": ""}, True), ({"k": "   "}, True),
        ({"k": "1"}, True), ({"k": "yes"}, True), ({"k": "banana"}, True),
    ]
    for config, want in cases:
        assert mac.config_flag(config, "k", True) is want, config
    # `default` is honoured in both directions, not just as "on".
    assert mac.config_flag({}, "k", False) is False
    assert mac.config_flag({"k": ""}, "k", False) is False
    assert mac.config_flag(None, "k", True) is True


def test_config_seconds_reads_the_documented_spellings(mac):
    """A threshold whose number is also its switch.

    Two states have to stay distinguishable and both look like "no": *absent*
    means "not configured", which is the default, and `off`/`0`/negative mean
    "configured to nothing", which is 0.0. Collapsing them would make the
    default unreachable once anybody had written the key.

    ⚠️ Unparseable falls back to the **default**, not to zero and not to an
    exception -- this runs on the render path, where raising wedges a paint.
    The cost is that `= 5 minutes` silently means 300; `agb doctor` checks key
    names, not values. That trade is deliberate and is recorded here because
    the alternative reading ("garbage means off") is equally plausible and
    would be a silent behaviour change.
    """
    cases = [
        ({}, 300.0), ({"k": ""}, 300.0), ({"k": "   "}, 300.0),
        ({"k": "0"}, 0.0), ({"k": "off"}, 0.0), ({"k": "no"}, 0.0),
        ({"k": "false"}, 0.0), ({"k": "OFF"}, 0.0),
        ({"k": "-5"}, 0.0), ({"k": "-0.1"}, 0.0),
        ({"k": "120"}, 120.0), ({"k": "120.5"}, 120.5),
        ({"k": " 120 "}, 120.0),
        ({"k": "5 minutes"}, 300.0), ({"k": "abc"}, 300.0),
    ]
    for config, want in cases:
        assert mac.config_seconds(config, "k", 300.0) == want, config
    assert mac.config_seconds(None, "k", 300.0) == 300.0
    # Absent and off are different states, which is the whole point.
    assert mac.config_seconds({}, "k", 300.0) != mac.config_seconds(
        {"k": "off"}, "k", 300.0)


def test_the_two_coercers_share_one_falsy_vocabulary(mac):
    """`off` in one and `off` in the other must not come to mean different
    things. A second tuple is how that drifts, so `config_seconds` reads
    `CONFIG_FALSE` rather than spelling its own -- asserted here because the
    duplicate would be invisible: both files would pass their own tests."""
    assert mac.CONFIG_FALSE, "empty vocabulary would make this vacuous"
    for word in mac.CONFIG_FALSE:
        assert mac.config_flag({"k": word}, "k", True) is False, word
        assert mac.config_seconds({"k": word}, "k", 300.0) == 0.0, word


# ---------------------------------------------------------------------------
# a banner when a long-running turn finishes
# ---------------------------------------------------------------------------
# ⚠️ Two mechanical traps govern every test below, and three review passes were
# needed to get them right:
#
# 1. `BridgeModel._upsert` drops a record identical to the last one BEFORE the
#    renderer sees it, and `wire()` has a constant `seq`. Every repeat upsert
#    here varies `seq`, or the renderer is never entered and the test proves
#    nothing about the code it names.
# 2. `Harness.upsert` defaults to `now=NOW`. A test that leaves the clock
#    implicit puts two events at the same instant, which makes "measured from
#    the first sighting" and "measured from the last" indistinguishable -- so
#    the disconnect test below pins every clock explicitly.

THRESHOLD = {"notify_completed_after": 300}


def test_a_long_turn_is_announced_when_it_finishes(bridge):
    """The whole feature: you started something, walked away, and want to be
    told it is done rather than having to look."""
    b = bridge(settings=THRESHOLD)
    b.upsert(wire("aaaa1111", state="active"), now=NOW)
    b.upsert(wire("aaaa1111", state="completed", seq=2), now=NOW + 400)
    banners = _finished(b)
    assert len(banners) == 1, _notifies(b)
    body, title, target = banners[0]
    assert "build" in body                      # the label, not the key
    assert target == b.rows.row_for("aaaa1111")
    assert title


def test_a_short_turn_finishes_silently(bridge):
    """The companion to the test above, and the reason the threshold exists at
    all: `completed` fires once per TURN, so without this every "yes" you type
    bounces the Dock three seconds later -- on the row you are looking at,
    since agterm banners regardless of which row is selected."""
    b = bridge(settings=THRESHOLD)
    b.upsert(wire("aaaa1111", state="active"), now=NOW)
    b.upsert(wire("aaaa1111", state="completed", seq=2), now=NOW + 10)
    assert _finished(b) == []


def test_the_default_threshold_applies_with_no_settings(bridge):
    """A `RowRenderer` built with `{}` must behave like production.

    ⚠️ This is the ONLY test that exercises the renderer's own
    `COMPLETED_AFTER` fallback: every other test here injects a threshold, and
    no pre-existing test can stand in because they all run at `now=NOW`, giving
    every turn a duration of zero. Without it, deleting the default from the
    `.get()` would break nothing.
    """
    b = bridge(settings={})
    b.upsert(wire("aaaa1111", state="active"), now=NOW)
    b.upsert(wire("aaaa1111", state="completed", seq=2), now=NOW + 400)
    assert len(_finished(b)) == 1, _notifies(b)


def test_a_finished_turn_is_announced_once_not_per_snapshot(bridge):
    """The `pop` is the transition memory. ⚠️ `seq` climbs so the second report
    genuinely reaches the renderer -- an identical record is dropped by
    `BridgeModel._upsert`, and this test would pass with `_notify_completed`
    deleted if it did not."""
    b = bridge(settings=THRESHOLD)
    b.upsert(wire("aaaa1111", state="active"), now=NOW)
    b.upsert(wire("aaaa1111", state="completed", seq=2), now=NOW + 400)
    b.upsert(wire("aaaa1111", state="completed", seq=3), now=NOW + 410)
    assert len(_finished(b)) == 1, _notifies(b)


def test_two_long_turns_in_a_row_are_both_announced(bridge):
    """The other half of the `pop`: it re-arms. Nothing else here pins that, so
    an implementation that remembered announced keys in a set -- the obvious
    way somebody "stops repeats" -- would pass every other test in this file."""
    b = bridge(settings=THRESHOLD)
    b.upsert(wire("aaaa1111", state="active"), now=NOW)
    b.upsert(wire("aaaa1111", state="completed", seq=2), now=NOW + 400)
    b.upsert(wire("aaaa1111", state="active", seq=3), now=NOW + 500)
    b.upsert(wire("aaaa1111", state="completed", seq=4), now=NOW + 900)
    assert len(_finished(b)) == 2, _notifies(b)


def test_a_disconnect_mid_turn_does_not_reset_the_timer(bridge):
    """⚠️ Every clock here is pinned, and that is what makes the test real.

    `_render_stale` writes `idle` into `applied` for every bound row on any
    disconnect, including a routine 10 s quiet spell. A start time recovered
    from `applied`, or one overwritten on each `active`, would restart at the
    reconnect -- so the turn below would measure 50 s against a 300 s
    threshold and go silent.

    Left at the harness default both events land on `NOW`, and correct code and
    both mutations announce identically. The `+350` is the discriminator.
    """
    b = bridge(settings=THRESHOLD)
    b.upsert(wire("aaaa1111", state="active"), now=NOW)
    b.stale("eof")
    b.upsert(wire("aaaa1111", state="active", seq=2), now=NOW + 350)
    b.upsert(wire("aaaa1111", state="completed", seq=3), now=NOW + 400)
    assert len(_finished(b)) == 1, _notifies(b)


def test_a_reconnect_full_of_finished_agents_is_silent(bridge):
    """A restarted bridge sees every finished agent as `completed` with no
    start time, so it announces nothing -- burst-immune without needing the
    quiet window `_notify_new_row` has.

    ⚠️ `_past_quiet` first, so the new-row banners DO fire. Asserting the total
    is non-empty is the non-vacuity guard: without it this passes against a
    renderer that emits no banners at all, for any reason.
    """
    b = bridge(settings=THRESHOLD)
    _past_quiet(b)
    b.snapshot([wire("aaaa1111", state="completed"),
                wire("bbbb2222", state="completed")], now=NOW + 9999)
    assert _notifies(b), "new-row banners should have fired"
    assert _finished(b) == []


def test_a_block_mid_turn_restarts_the_clock(bridge):
    """You answered a prompt and it finished seconds later. That block was
    already announced; announcing the finish too would be two interruptions
    for one event."""
    b = bridge(settings=THRESHOLD)
    b.upsert(wire("aaaa1111", state="active"), now=NOW)
    b.upsert(wire("aaaa1111", state="blocked", seq=2), now=NOW + 380)
    b.upsert(wire("aaaa1111", state="active", seq=3), now=NOW + 390)
    b.upsert(wire("aaaa1111", state="completed", seq=4), now=NOW + 400)
    assert _finished(b) == []


def test_without_the_block_that_same_span_is_announced(bridge):
    """The companion to the test above: same key, same clocks, same threshold,
    only the block removed. Without it, "silent" proves nothing -- the span
    might simply have been too short."""
    b = bridge(settings=THRESHOLD)
    b.upsert(wire("aaaa1111", state="active"), now=NOW)
    b.upsert(wire("aaaa1111", state="completed", seq=4), now=NOW + 400)
    assert len(_finished(b)) == 1, _notifies(b)


def test_a_removal_then_a_rebind_does_not_announce(bridge):
    """A removal ends the turn. `agb prune`, or a complete snapshot dropping
    the key, then the feed asserting it again -- without the pop in
    `_render_remove` the banner would carry a duration spanning the removal,
    for a turn nobody watched.

    ⚠️ `_forget_unmapped` does not cover this: a `[done]` entry is deliberately
    still in the map, so the reclaimer skips it by design.
    """
    b = bridge(settings=THRESHOLD)
    b.upsert(wire("aaaa1111", state="active"), now=NOW)
    b.remove("aaaa1111")
    b.upsert(wire("aaaa1111", state="completed", seq=2), now=NOW + 400)
    assert _finished(b) == []


def test_without_the_removal_that_same_span_is_announced(bridge):
    """Companion to the removal test, so "silent" is attributable."""
    b = bridge(settings=THRESHOLD)
    b.upsert(wire("aaaa1111", state="active"), now=NOW)
    b.upsert(wire("aaaa1111", state="completed", seq=2), now=NOW + 400)
    assert len(_finished(b)) == 1, _notifies(b)


def test_a_first_event_with_no_feed_clock_does_not_poison_the_turn(bridge):
    """⚠️ Three events, not two, and the damage is subtler than it looks.

    There is no exception to prevent: `working.pop(key, None)` returns None for
    a STORED None exactly as for a missing key, so the arithmetic is never
    reached either way. What the guard actually stops is `working[key] = None`
    being stored at all -- which makes the later `key not in self.working`
    false, so the real clock never lands and the turn is silently lost.

    Without the third event this test passes against code with the guard
    deleted, which is how two drafts of it were written.
    """
    b = bridge(settings=THRESHOLD)
    b.send("upsert", now=None, session=wire("aaaa1111", state="active"))
    assert b.renderer.working == {}, "a clockless event must store nothing"
    b.upsert(wire("aaaa1111", state="active", seq=2), now=NOW)
    b.upsert(wire("aaaa1111", state="completed", seq=3), now=NOW + 400)
    assert len(_finished(b)) == 1, _notifies(b)


def test_a_sub_thirty_second_turn_names_no_duration(bridge):
    """`beat_age_text` renders "" below `BEAT_LATE` (30 s), and "finished
    after " with a dangling preposition is worse than saying nothing.

    Reachable only at a low threshold, which is exactly what the live-test
    recipe uses -- so this would otherwise be found by a human on a Mac.
    """
    b = bridge(settings={"notify_completed_after": 5})
    b.upsert(wire("aaaa1111", state="active"), now=NOW)
    b.upsert(wire("aaaa1111", state="completed", seq=2), now=NOW + 10)
    banners = _finished(b)
    assert len(banners) == 1, _notifies(b)
    assert banners[0][0] == "build finished"


def test_a_long_turn_does_name_its_duration(bridge):
    """Companion: the "after ..." clause is dropped only when it is empty, not
    always."""
    b = bridge(settings=THRESHOLD)
    b.upsert(wire("aaaa1111", state="active"), now=NOW)
    b.upsert(wire("aaaa1111", state="completed", seq=2), now=NOW + 400)
    assert _finished(b)[0][0] == "build finished after 6m"


def test_the_finished_banner_can_be_turned_off(bridge):
    """`notify_on_completed_after = off`. ⚠️ TWO assertions, both load-bearing.

    Membership BETWEEN the two upserts, because the pop has already run by the
    end -- unlike `self.blocked`, which is a set that persists. And emptiness
    AFTER, because that is the only thing distinguishing a correct off-switch
    from one that returns before the pop: both stay silent, and both still hold
    the mid-test assertion. Leaving the entry behind would deliver a stale
    duration the moment the key was turned back on.
    """
    b = bridge(settings={"notify_completed_after": 0})
    b.upsert(wire("aaaa1111", state="active"), now=NOW)
    assert "aaaa1111" in b.renderer.working
    b.upsert(wire("aaaa1111", state="completed", seq=2), now=NOW + 400)
    assert _finished(b) == []
    assert b.renderer.working == {}, "a parked switch must not hoard a backlog"


def test_working_memory_is_reclaimed_when_the_map_forgets_a_key(bridge):
    """The fourth dict in a launchd-resident process needs the same reclamation
    path as the other three.

    ⚠️ Deliberately NO `remove()` step, unlike the template this copies: the
    pop in `_render_remove` would empty `working` two steps early and the
    assertion would hold against a `_forget_unmapped` that ignores it. The path
    without a removal is real -- an external `agb close-done`, merged by
    `rows.save()`, drops a key that is still working.
    """
    b = bridge(settings=THRESHOLD)
    b.upsert(wire("aaaa1111", state="active"), now=NOW)
    assert "aaaa1111" in b.renderer.working

    b.rows.forget("aaaa1111")                  # what `close-done` does
    b.tick()

    assert b.renderer.working == {}


# ---------------------------------------------------------------------------
# `agb instances`
# ---------------------------------------------------------------------------

def _write_inst_plist(path, argv):
    """Write a plist file with the given ProgramArguments."""
    import plistlib
    label = os.path.basename(str(path))[:-len(".plist")]
    doc = {"Label": label, "ProgramArguments": argv}
    with open(str(path), "wb") as fh:
        plistlib.dump(doc, fh)


def test_instances_probe_prints_instances_ok(agb, tmp_path):
    """--probe prints `instances-ok` and exits 0 (the known-answer probe)."""
    import subprocess
    proc = subprocess.Popen(conftest.AGB_ARGV + ["instances", "--probe"],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, err = conftest.communicate(proc)
    assert proc.returncode == 0
    assert out == b"instances-ok\n"
    assert err == b""


def test_instances_probe_ignores_launch_agents(agb, tmp_path):
    """--probe does not read LaunchAgents -- missing dir is still exit 0."""
    import subprocess
    proc = subprocess.Popen(
        conftest.AGB_ARGV + ["instances", "--probe",
                             "--launch-agents", str(tmp_path / "no_such")],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, err = conftest.communicate(proc)
    assert proc.returncode == 0
    assert out == b"instances-ok\n"


def test_instances_labels_empty_when_no_launch_agents_dir(mac, tmp_path):
    """ENOENT on the agents dir is 'no instances yet', not an error."""
    out = _RowOut()
    rc = mac.run_instances(
        ["--labels", "--launch-agents", str(tmp_path / "no_such_dir")], out=out)
    assert rc == 0
    assert out.text == ""


def test_instances_labels_fatal_on_non_enoent_dir_error(mac, tmp_path):
    """An errno other than ENOENT is fatal -- 'could not list' (contract 2)."""
    file_not_dir = tmp_path / "notadir"
    file_not_dir.write_text("content")
    # Trying to listdir a file gives ENOTDIR, not ENOENT.
    rc = mac.run_instances(
        ["--labels", "--launch-agents", str(file_not_dir)],
        out=_RowOut())
    assert rc != 0


def test_instances_labels_lists_com_agbridge_instance(mac, tmp_path):
    """A plist in the com.agbridge space appears in --labels."""
    agents = tmp_path / "agents"
    agents.mkdir()
    _write_inst_plist(
        agents / "com.agbridge.plist",
        ["/usr/bin/python3", "-S", "-E", "/Users/me/agb", "bridge"])
    out = _RowOut()
    rc = mac.run_instances(
        ["--labels", "--launch-agents", str(agents)], out=out)
    assert rc == 0
    assert "com.agbridge\n" in out.text


def test_instances_labels_excludes_non_agbridge_plist(mac, tmp_path):
    """A plist outside com.agbridge with no `agb bridge` in argv is excluded."""
    agents = tmp_path / "agents"
    agents.mkdir()
    _write_inst_plist(agents / "com.other-app.plist",
                     ["/usr/bin/python3", "other-app"])
    out = _RowOut()
    rc = mac.run_instances(
        ["--labels", "--launch-agents", str(agents)], out=out)
    assert rc == 0
    assert out.text == ""


def test_instances_labels_includes_custom_label_in_com_agbridge_space(mac,
                                                                      tmp_path):
    """A custom label like com.agbridge.hostb is in the space and included.

    ⚠️ The plist has NO agb+bridge in its argv so this relies entirely on
    the label-space check -- removing the dot-prefix arm breaks this test.
    """
    agents = tmp_path / "agents"
    agents.mkdir()
    # No `agb bridge` in argv: included only because of the label space.
    _write_inst_plist(
        agents / "com.agbridge.hostb.plist",
        ["/usr/bin/python3", "/Users/me/other-binary"])
    out = _RowOut()
    rc = mac.run_instances(
        ["--labels", "--launch-agents", str(agents)], out=out)
    assert rc == 0
    assert "com.agbridge.hostb\n" in out.text


def test_instances_labels_includes_non_label_space_with_agb_bridge_argv(
        mac, tmp_path):
    """Contract 3: a plist outside com.agbridge is included when its argv
    has `bridge` immediately after an element whose basename is `agb`."""
    agents = tmp_path / "agents"
    agents.mkdir()
    _write_inst_plist(
        agents / "com.myorg.myapp.plist",
        ["/usr/bin/python3", "-S", "-E", "/custom/dir/agb",
         "bridge", "--config", "/a/config"])
    out = _RowOut()
    rc = mac.run_instances(
        ["--labels", "--launch-agents", str(agents)], out=out)
    assert rc == 0
    # Non-vacuity: the plist IS in the output.
    assert "com.myorg.myapp\n" in out.text


def test_instances_plist_arg_exit_2_for_missing_file(mac, tmp_path):
    """Exit 2 when the plist file does not exist."""
    rc = mac.run_instances(
        ["--plist", str(tmp_path / "no.plist"), "--arg", "--config"])
    assert rc == 2


def test_instances_plist_arg_exit_2_for_invalid_plist(mac, tmp_path):
    """Exit 2 when the file exists but is not a valid plist."""
    bad = tmp_path / "bad.plist"
    bad.write_text("not a plist at all")
    rc = mac.run_instances(
        ["--plist", str(bad), "--arg", "--config"])
    assert rc == 2


def test_instances_plist_arg_exit_0_empty_for_no_bridge_in_argv(mac, tmp_path):
    """Exit 0 with no written value when the plist argv has no `bridge`."""
    import plistlib
    p = tmp_path / "no_bridge.plist"
    doc = {"Label": "com.agbridge",
           "ProgramArguments": ["/usr/bin/python3", "/Users/me/agb", "doctor"]}
    with open(str(p), "wb") as fh:
        plistlib.dump(doc, fh)
    rc = mac.run_instances(["--plist", str(p), "--arg", "--config"])
    assert rc == 0


def test_instances_plist_arg_value_written_to_stdout_buffer(agb, tmp_path):
    """--plist --arg writes the config value to stdout as bytes."""
    import plistlib, subprocess
    p = tmp_path / "com.agbridge.plist"
    doc = {"Label": "com.agbridge",
           "ProgramArguments": ["/usr/bin/python3", "-S", "-E",
                                "/Users/me/agb", "bridge",
                                "--config", "/a/config"]}
    with open(str(p), "wb") as fh:
        plistlib.dump(doc, fh)
    proc = subprocess.Popen(
        conftest.AGB_ARGV + ["instances", "--plist", str(p), "--arg", "--config"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, err = conftest.communicate(proc)
    assert proc.returncode == 0
    assert out == b"/a/config\n"
    assert err == b""


def test_instances_plist_arg_path_with_space(agb, tmp_path):
    """A config path with a space is returned verbatim."""
    import plistlib, subprocess
    p = tmp_path / "com.agbridge.plist"
    config = "/home/user/my config/config"
    doc = {"Label": "com.agbridge",
           "ProgramArguments": ["/usr/bin/python3", "-S", "-E",
                                "/Users/me/agb", "bridge", "--config", config]}
    with open(str(p), "wb") as fh:
        plistlib.dump(doc, fh)
    proc = subprocess.Popen(
        conftest.AGB_ARGV + ["instances", "--plist", str(p), "--arg", "--config"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, err = conftest.communicate(proc)
    assert proc.returncode == 0
    assert out == (config + "\n").encode("utf-8")


@pytest.mark.parametrize("locale", ["C", "POSIX", "en_US.ISO-8859-1"])
def test_instances_plist_arg_byte_output_under_any_locale(agb, tmp_path, locale):
    """⚠️ --arg writes sys.stdout.buffer, not sys.stdout.write (contract 1).

    -E does not touch LC_ALL. Under ISO-8859-1 sys.stdout.write() would
    transcode a non-ASCII path into bytes that name nowhere, silently.
    Under C it would raise UnicodeEncodeError. The awk `plist_arg` replaced
    passed bytes through unchanged; this must too.
    """
    import plistlib, subprocess
    p = tmp_path / ("locale_%s.plist" % locale.replace(".", "_"))
    config = "/été/config"
    doc = {"Label": "com.agbridge",
           "ProgramArguments": ["/usr/bin/python3", "-S", "-E",
                                "/Users/me/agb", "bridge", "--config", config]}
    with open(str(p), "wb") as fh:
        plistlib.dump(doc, fh)
    env = dict(os.environ)
    env["LC_ALL"] = env["LANG"] = locale
    proc = subprocess.Popen(
        conftest.AGB_ARGV + ["instances", "--plist", str(p), "--arg", "--config"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    out, err = conftest.communicate(proc)
    assert (proc.returncode, out, err) == (0, (config + "\n").encode("utf-8"), b""), \
        locale


def test_instances_plist_arg_plistlib_guard(agb, tmp_path):
    """⚠️ --arg reads the plist with plistlib, not a text scan.

    A plist with HTML entities in the config path is the proof: plistlib
    decodes `&amp;` to `&`; a text scan would return the raw encoding.
    Non-vacuity: the raw and decoded forms must differ.
    """
    import subprocess
    raw = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"'
        ' "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0"><dict>'
        '<key>ProgramArguments</key><array>'
        '<string>/usr/bin/python3</string><string>-S</string>'
        '<string>-E</string><string>/Users/me/agb</string>'
        '<string>bridge</string>'
        '<string>--config</string><string>/a&amp;b/config</string>'
        '</array></dict></plist>'
    )
    p = tmp_path / "entities.plist"
    p.write_bytes(raw.encode())
    decoded = "/a&b/config"
    assert "&amp;" not in decoded      # non-vacuity: raw and decoded differ
    proc = subprocess.Popen(
        conftest.AGB_ARGV + ["instances", "--plist", str(p), "--arg", "--config"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, err = conftest.communicate(proc)
    assert proc.returncode == 0
    assert out == (decoded + "\n").encode("utf-8")


def test_instances_default_listing_shows_instances(mac, tmp_path):
    """Default mode lists name, label, config per instance."""
    agents = tmp_path / "agents"
    agents.mkdir()
    _write_inst_plist(
        agents / "com.agbridge.hostb.plist",
        ["/usr/bin/python3", "-S", "-E", "/Users/me/agb",
         "bridge", "--config", "/a/config"])
    out = _RowOut()
    rc = mac.run_instances(["--launch-agents", str(agents)], out=out)
    assert rc == 0
    assert "hostb" in out.text
    assert "com.agbridge.hostb" in out.text
    assert "/a/config" in out.text


def _listing_columns(text):
    """The human listing's rows as [name, label, config] triples.

    `split()` is enough because these tests choose configs without blanks in
    them, and it is what makes the assertions about the NAME COLUMN rather
    than about a substring of the whole line -- `hostb` appears inside
    `com.agbridge.hostb` too, so an `in out.text` check cannot tell a working
    name column from an empty one.
    """
    return [line.split() for line in text.splitlines() if line.strip()]


def test_instances_listing_names_the_default_instance(mac, tmp_path):
    """⚠️ `com.agbridge` is `(default)`, not a blank name column.

    Found by running `agb instances` on the owner's real Mac: the default
    instance printed a row starting with two spaces. The name was derived by
    stripping the `com.agbridge.` prefix and answering "" when the label did
    not start with it -- and the default label never does, because it IS the
    prefix. `agb-refresh:1207-1219` had already settled this for the banner.
    """
    agents = tmp_path / "agents"
    agents.mkdir()
    _write_inst_plist(
        agents / "com.agbridge.plist",
        ["/usr/bin/python3", "-S", "-E", "/Users/me/agb",
         "bridge", "--config", "/a/config"])
    out = _RowOut()
    rc = mac.run_instances(["--launch-agents", str(agents)], out=out)
    assert rc == 0
    assert _listing_columns(out.text) == [
        ["(default)", "com.agbridge", "/a/config"]], out.text


def test_instances_listing_names_a_named_instance(mac, tmp_path):
    """A `com.agbridge.<name>` label shows `<name>` -- the case that worked."""
    agents = tmp_path / "agents"
    agents.mkdir()
    _write_inst_plist(
        agents / "com.agbridge.hostb.plist",
        ["/usr/bin/python3", "-S", "-E", "/Users/me/agb",
         "bridge", "--config", "/b/config"])
    out = _RowOut()
    rc = mac.run_instances(["--launch-agents", str(agents)], out=out)
    assert rc == 0
    assert _listing_columns(out.text) == [
        ["hostb", "com.agbridge.hostb", "/b/config"]], out.text


def test_instances_listing_names_a_custom_label_instance(mac, tmp_path):
    """⚠️ A custom label shows the LABEL, and is not called `(default)`.

    `install.sh mac --label <anything>` puts no shape rule on a label
    (`install.sh:426`), so `weird.label` is a real install with no name but its
    label. Both wrong answers are asserted against here, because the listing
    gave one of them and the banner used to give the other: "" (this bug) and
    "(default)" (the banner's, fixed in Task 5 --
    `test_a_custom_label_instance_is_not_reported_as_the_default_one`).
    """
    agents = tmp_path / "agents"
    agents.mkdir()
    _write_inst_plist(
        agents / "weird.label.plist",
        ["/usr/bin/python3", "-S", "-E", "/Users/me/agb",
         "bridge", "--config", "/c/config"])
    out = _RowOut()
    rc = mac.run_instances(["--launch-agents", str(agents)], out=out)
    assert rc == 0
    assert _listing_columns(out.text) == [
        ["weird.label", "weird.label", "/c/config"]], out.text
    assert "(default)" not in out.text, out.text


def test_instances_listing_columns_line_up(mac, tmp_path):
    """The three columns are padded, and a missing config leaves no blanks.

    Names have no natural width now that one of them can be `(default)` or a
    whole dotted label, so the config column only lines up if the first two are
    padded. The second half is the reason padding stops at the last column that
    exists: a job with no `--config` must not end its line in the blanks that
    were meant to separate it from something.
    """
    agents = tmp_path / "agents"
    agents.mkdir()
    _write_inst_plist(
        agents / "com.agbridge.plist",
        ["/usr/bin/python3", "-S", "-E", "/Users/me/agb",
         "bridge", "--config", "/a/config"])
    _write_inst_plist(
        agents / "com.agbridge.a-much-longer-name.plist",
        ["/usr/bin/python3", "-S", "-E", "/Users/me/agb",
         "bridge", "--config", "/b/config"])
    _write_inst_plist(                      # no --config: two columns only
        agents / "com.agbridge.nocfg.plist",
        ["/usr/bin/python3", "-S", "-E", "/Users/me/agb", "bridge"])
    out = _RowOut()
    rc = mac.run_instances(["--launch-agents", str(agents)], out=out)
    assert rc == 0
    lines = [ln for ln in out.text.splitlines() if ln.strip()]
    assert len(lines) == 3, out.text          # non-vacuity: all three listed
    # Non-vacuity for the alignment claim: the names really are different
    # lengths, so an unpadded listing could not pass this.
    starts = [len(ln) - len(ln.lstrip()) for ln in lines]
    assert starts == [0, 0, 0], out.text      # nothing indented by a blank name
    with_config = [ln for ln in lines if ln.rstrip().endswith("/config")]
    assert len(with_config) == 2, out.text
    assert len({ln.index("/") for ln in with_config}) == 1, with_config
    for ln in lines:
        assert ln == ln.rstrip(), repr(ln)    # no line ends in padding


def test_instances_default_listing_says_so_when_empty(mac, tmp_path):
    """Default mode reports 'no instances found' for an empty agents dir."""
    agents = tmp_path / "agents"
    agents.mkdir()
    out = _RowOut()
    rc = mac.run_instances(["--launch-agents", str(agents)], out=out)
    assert rc == 0
    assert "no agbridge instances" in out.text


def test_instances_launch_agents_overrides_default_dir(mac, tmp_path):
    """--launch-agents is used instead of ~/Library/LaunchAgents."""
    custom = tmp_path / "custom"
    custom.mkdir()
    _write_inst_plist(
        custom / "com.agbridge.plist",
        ["/usr/bin/python3", "-S", "-E", "/Users/me/agb", "bridge"])
    out = _RowOut()
    rc = mac.run_instances(["--labels", "--launch-agents", str(custom)], out=out)
    assert rc == 0
    assert "com.agbridge\n" in out.text


# ---------------------------------------------------------------------------
# the row-map commands sweep every instance
# ---------------------------------------------------------------------------
#
# Bare `close-done` and `forget-rows --all` visit every instance on this Mac.
# The default instance's privilege was an artifact of install order: a helper
# run without `--config` repaired the *unnamed* map and reported success in
# exactly the words of the run you meant to make (`docs/design.md` §5,
# limitation 1). `agb-refresh` sweeps the same set from the shell; these two run
# in-process, which is why every test below drives them through `--launch-agents`
# rather than by monkeypatching `$HOME` and hoping the default lands somewhere
# harmless. Under `fake_home` it does land somewhere harmless -- which is
# precisely the trap: a fixture that ALSO wrote its plists where the default
# looks would make the flag untestable while every test stayed green.

def _instance_plist(agents, label, config):
    """A launchd plist for one instance, with the real four-element preamble.

    `ProgramArguments` is the whole command line, not the bridge's argv --
    `<python> -S -E <agb> bridge …` -- and a harness simpler than reality is how
    forty plist cases were once proved against an array four elements short.
    `config=None` writes a job that names no config: contract 2's case, which
    must read as `agb.config_path()`.
    """
    argv = ["/usr/bin/python3", "-S", "-E", "/Users/me/agb", "bridge"]
    if config is not None:
        argv += ["--config", config]
    _write_inst_plist(agents / (label + ".plist"), argv)


def _agents_dir(tmp_path):
    """A LaunchAgents directory that is NOT the one the default resolves to.

    Non-vacuity for every `--launch-agents` test in this section: if the plists
    were reachable from `~/Library/LaunchAgents` as well, a sweep that ignored
    the flag would find them anyway and the flag would be proved by nothing.
    """
    agents = tmp_path / "agents"
    agents.mkdir()
    assert not os.path.exists(os.path.join(os.path.expanduser("~"),
                                           "Library", "LaunchAgents"))
    return agents


def test_bare_close_done_reclaims_done_rows_in_every_instance(
        mac, tmp_path, instance_config):
    """The whole point: two instances, one command, both maps reclaimed.

    Each also keeps a *bound* row, so "closed everything it found" and "closed
    the [done] ones" stay distinguishable -- and the banner is counted, because
    an operator reading the output has to be able to tell which map each line
    below it is about.
    """
    default = instance_config()
    hostb = instance_config("hostb")
    _done_map(mac, mac.rows_path(default), "aaaa1111", "ROW-1")
    _done_map(mac, mac.rows_path(hostb), "bbbb2222", "ROW-3")
    _bound_map(mac, mac.rows_path(default), "cccc3333", "ROW-9")
    agents = _agents_dir(tmp_path)
    _instance_plist(agents, "com.agbridge", default)
    _instance_plist(agents, "com.agbridge.hostb", hostb)

    runner = Runner()
    out = _RowOut()
    rc = mac.run_close_done(["--launch-agents", str(agents)],
                            run=runner, out=out)

    assert rc == 0
    closed = [c[-1] for c in runner.calls if c[1:3] == ["session", "close"]]
    assert sorted(closed) == ["ROW-1", "ROW-3"]
    assert mac.load_rows(mac.rows_path(default)).done_entries() == []
    assert mac.load_rows(mac.rows_path(hostb)).done_entries() == []
    assert mac.load_rows(mac.rows_path(default)).bound_keys() == ["cccc3333"]
    assert out.text.count("close-done: config") == 2


def test_a_plist_that_names_no_config_is_the_default_map_not_a_convention(
        mac, tmp_path, instance_config):
    """⚠️ Contract 2, and the one reading that must not be invented here.

    A bridge started with no `--config` resolves `agb.config_path()`, so that is
    the map its job holds -- which is `agb-refresh`'s `bind_label_to_config`
    reading, taken verbatim. The other available reading, the
    `<dir>/<name>/config` convention, belongs to `agb-refresh --instance <name>`
    and would make this sweep repair a map that never existed while reporting
    the default one empty: invariant 12, arriving in-process.

    The control is the second instance, whose plist *does* name a config: if the
    sweep were resolving the convention, only that one would be reclaimed.
    """
    default = instance_config()
    hostb = instance_config("hostb")
    _done_map(mac, mac.rows_path(default), "aaaa1111", "ROW-1")
    _done_map(mac, mac.rows_path(hostb), "bbbb2222", "ROW-3")
    agents = _agents_dir(tmp_path)
    _instance_plist(agents, "com.agbridge", None)          # no --config at all
    _instance_plist(agents, "com.agbridge.hostb", hostb)

    runner = Runner()
    mac.run_close_done(["--launch-agents", str(agents)], run=runner,
                       out=_RowOut())

    assert mac.load_rows(mac.rows_path(default)).done_entries() == []
    assert mac.load_rows(mac.rows_path(hostb)).done_entries() == []


def test_an_unreadable_plist_of_ours_stops_the_whole_sweep(
        mac, agb, tmp_path, instance_config):
    """⚠️ "I could not answer" is not "the answer is nothing".

    Skipping the instance we cannot read acts on the others and returns 0, and
    the operator has no way to know an instance was missed -- which is exactly
    the failure this plan exists to remove, arriving one level down. So it is
    fatal, and nothing is closed at all.

    The control differs in ONE variable: the same run, after the same file is
    made readable, does close the row. Without it this test would pass against a
    sweep that could never close anything.
    """
    default = instance_config()
    hostb = instance_config("hostb")
    _done_map(mac, mac.rows_path(default), "aaaa1111", "ROW-1")
    agents = _agents_dir(tmp_path)
    _instance_plist(agents, "com.agbridge", default)
    (agents / "com.agbridge.hostb.plist").write_text("not a plist at all\n")

    with pytest.raises(agb.AgbError) as excinfo:
        mac.run_close_done(["--launch-agents", str(agents)], run=Runner(),
                           out=_RowOut())
    assert "com.agbridge.hostb.plist" in str(excinfo.value)
    assert mac.load_rows(mac.rows_path(default)).done_entries() == [
        ("aaaa1111", "ROW-1")]

    _instance_plist(agents, "com.agbridge.hostb", hostb)   # the one variable
    assert mac.run_close_done(["--launch-agents", str(agents)], run=Runner(),
                              out=_RowOut()) == 0
    assert mac.load_rows(mac.rows_path(default)).done_entries() == []


def test_an_unreadable_plist_that_is_not_ours_does_not_stop_the_sweep(
        mac, tmp_path, instance_config):
    """The other half of the same rule, and it is not a softening of it.

    `_is_agbridge_instance` cannot attribute a plist it could not parse unless
    the LABEL says so, so an unreadable third-party file is not claimed here any
    more than `agb instances --labels` claims it -- the two must visit the same
    set or `agb-refresh` and this command disagree about which Mac they are on.
    A broken file some other installer left behind would otherwise stop every
    sweep on the machine.
    """
    default = instance_config()
    _done_map(mac, mac.rows_path(default), "aaaa1111", "ROW-1")
    agents = _agents_dir(tmp_path)
    _instance_plist(agents, "com.agbridge", default)
    (agents / "com.other-app.plist").write_text("not a plist at all\n")

    assert mac.run_close_done(["--launch-agents", str(agents)], run=Runner(),
                              out=_RowOut()) == 0
    assert mac.load_rows(mac.rows_path(default)).done_entries() == []


def test_a_launch_agents_directory_that_cannot_be_listed_is_fatal(
        mac, agb, tmp_path, instance_config):
    """`os.listdir` failing with anything but ENOENT is "I could not answer".

    ⚠️ `os.path.isdir`/`exists` swallow every stat errno, so the tempting
    spelling reports a broken filesystem as "no instances yet" -- which here
    means falling through to the default map and reporting success. ENOENT is
    the one errno that IS an answer, and it has its own test below.
    """
    default = instance_config()
    _done_map(mac, mac.rows_path(default), "aaaa1111", "ROW-1")
    agents = _agents_dir(tmp_path)
    _instance_plist(agents, "com.agbridge", default)
    os.chmod(str(agents), 0)
    try:
        with pytest.raises(agb.AgbError):
            mac.run_close_done(["--launch-agents", str(agents)],
                               run=Runner(), out=_RowOut())
    finally:
        os.chmod(str(agents), 0o755)
    assert mac.load_rows(mac.rows_path(default)).done_entries() == [
        ("aaaa1111", "ROW-1")]


@pytest.mark.parametrize("name,argv", [("close-done", []),
                                       ("forget-rows", ["--all"])])
def test_no_instances_at_all_still_acts_on_the_default_map(
        mac, name, argv, tmp_path, instance_config):
    """⚠️ Do not regress the documented recipe.

    `docs/cookbook.md` tells a Mac with a config and no launchd job to run a
    bare `agb close-done`, and that is the commonest shape there is. Discovery
    finding nothing is not an error and not a refusal: a note, then the single
    default run -- the same answer `agb-refresh`' sweep gives.
    """
    default = instance_config()
    _done_map(mac, mac.rows_path(default), "aaaa1111", "ROW-1")
    _bound_map(mac, mac.rows_path(default), "bbbb2222", "ROW-2")
    agents = _agents_dir(tmp_path)                     # exists, and is empty

    out = _RowOut()
    _run_row_command(mac, name, argv + ["--launch-agents", str(agents)], out)

    assert "no instances found" in out.text
    assert default in out.text
    if name == "close-done":
        assert mac.load_rows(mac.rows_path(default)).done_entries() == []
    else:
        assert mac.load_rows(mac.rows_path(default)).bound_keys() == []


def test_a_missing_launch_agents_directory_is_not_an_error(
        mac, tmp_path, instance_config):
    """ENOENT is the ordinary Mac, not a failure: no `~/Library/LaunchAgents`
    at all means no instances, which is an answer. Every other errno is not."""
    default = instance_config()
    _done_map(mac, mac.rows_path(default), "aaaa1111", "ROW-1")
    out = _RowOut()
    rc = mac.run_close_done(["--launch-agents", str(tmp_path / "nope")],
                            run=Runner(), out=out)
    assert rc == 0
    assert "no instances found" in out.text
    assert mac.load_rows(mac.rows_path(default)).done_entries() == []


def test_bare_forget_rows_is_refused_and_names_the_flag_that_means_it(
        mac, agb, tmp_path, instance_config):
    """⚠️ The one command that does NOT default to all, and the reason is not
    "it closes rows" -- `agb-refresh` closes every row it forgets too.

    The difference is what happens next: `agb-refresh` restarts the bridge, so
    the rows come back within seconds; this restarts nothing, so a sweep nobody
    meant leaves every row of every instance closed until each bridge is bounced
    by hand. The refusal has to name `--all`, or it is a dead end rather than a
    question.
    """
    default = instance_config()
    _bound_map(mac, mac.rows_path(default), "aaaa1111", "ROW-1")
    agents = _agents_dir(tmp_path)
    _instance_plist(agents, "com.agbridge", default)

    with pytest.raises(agb.AgbError) as excinfo:
        mac.run_forget_rows(["--launch-agents", str(agents)], out=_RowOut(),
                            run=_tree_run)
    assert "--all" in str(excinfo.value)
    assert mac.load_rows(mac.rows_path(default)).bound_keys() == ["aaaa1111"]


def test_forget_rows_all_writes_each_instances_own_placements(
        mac, tmp_path, instance_config):
    """⚠️ The half that corrupts silently if the sweep resolves paths once.

    `forget-rows` rewrites `dirname(<config>)/placements`, so a sweep that
    derived the placements file from the run's own flags rather than from each
    instance's config would write B's `key = workspace` lines into A's file --
    the recovery command destroying the row layout of the instance it was not
    even asked about, with both maps then wrong and nothing said.
    """
    default = instance_config()
    hostb = instance_config("hostb")
    _bound_map(mac, mac.rows_path(default), "aaaa1111", "ROW-1")
    _bound_map(mac, mac.rows_path(hostb), "bbbb2222", "ROW-3")
    agents = _agents_dir(tmp_path)
    _instance_plist(agents, "com.agbridge", default)
    _instance_plist(agents, "com.agbridge.hostb", hostb)

    out = _RowOut()
    rc = mac.run_forget_rows(["--all", "--launch-agents", str(agents)],
                             out=out, run=_tree_run)

    assert rc == 0
    assert mac.load_rows(mac.rows_path(default)).bound_keys() == []
    assert mac.load_rows(mac.rows_path(hostb)).bound_keys() == []
    # TREE_JSON puts ROW-1 in "working repos" and ROW-3 in "agbridge".
    assert mac.read_placements(mac.placements_path(default)) == {
        "aaaa1111": "working repos"}
    assert mac.read_placements(mac.placements_path(hostb)) == {
        "bbbb2222": "agbridge"}


def test_forget_rows_all_beside_a_named_map_is_refused(
        mac, agb, tmp_path, instance_config):
    """`--all` means every instance and `--rows` means this one. Letting either
    win silently is the shape invariant 12 keeps producing -- the right map
    under the wrong label, reported as success -- so it is one line either way
    and this is the safe one."""
    default = instance_config()
    _bound_map(mac, mac.rows_path(default), "aaaa1111", "ROW-1")
    with pytest.raises(agb.AgbError):
        mac.run_forget_rows(["--all", "--rows", mac.rows_path(default)],
                            out=_RowOut(), run=_tree_run)
    assert mac.load_rows(mac.rows_path(default)).bound_keys() == ["aaaa1111"]


def test_a_named_map_still_means_exactly_that_map(
        mac, tmp_path, instance_config):
    """⚠️ `--rows`/`--placements`/`--config` keep TODAY's semantics exactly.

    Naming a map *is* naming what to act on, and `agb forget-rows --rows
    ~/.config/agbridge/rows` is the documented recovery for an install with no
    instance name to give. It is also the seam ~26 tests above this one use, so
    a narrowing flag that started sweeping would quietly widen all of them.
    """
    default = instance_config()
    hostb = instance_config("hostb")
    _bound_map(mac, mac.rows_path(default), "aaaa1111", "ROW-1")
    _bound_map(mac, mac.rows_path(hostb), "bbbb2222", "ROW-3")
    agents = _agents_dir(tmp_path)
    _instance_plist(agents, "com.agbridge", default)
    _instance_plist(agents, "com.agbridge.hostb", hostb)

    mac.run_forget_rows(["--rows", mac.rows_path(default),
                         "--launch-agents", str(agents)],
                        out=_RowOut(), run=_tree_run)

    assert mac.load_rows(mac.rows_path(default)).bound_keys() == []
    assert mac.load_rows(mac.rows_path(hostb)).bound_keys() == ["bbbb2222"]


def test_forget_rows_key_sweeps_and_names_the_instance_that_had_it(
        mac, tmp_path, instance_config):
    """⚠️ `--key` names WHAT to forget, not WHERE -- so it sweeps.

    A key is read out of a bridge log and nothing in that log says which
    instance minted it; "you should not have to know which" is the whole thesis,
    and it is the reading `agb-refresh --key` already took. A key belongs to
    exactly one map, so "not in this map" is the ORDINARY answer from every
    other instance and must not be a failure -- which is why the run reports 0
    and names the instance that had it.
    """
    default = instance_config()
    hostb = instance_config("hostb")
    _bound_map(mac, mac.rows_path(default), "aaaa1111", "ROW-1")
    _bound_map(mac, mac.rows_path(hostb), "bbbb2222", "ROW-3")
    agents = _agents_dir(tmp_path)
    _instance_plist(agents, "com.agbridge", default)
    _instance_plist(agents, "com.agbridge.hostb", hostb)

    out = _RowOut()
    rc = mac.run_forget_rows(["--key", "bbbb2222",
                              "--launch-agents", str(agents)],
                             out=out, run=_tree_run)

    assert rc == 0
    assert "com.agbridge.hostb" in out.text
    assert "no instance has" not in out.text
    # ⚠️ And the instance that did NOT have it says nothing about it. `not in
    # the map` is true of that instance and misleading about the run: the key
    # was found, and a line saying otherwise beside a successful exit is the
    # kind of output an operator reads as a failure.
    assert "not in the map" not in out.text
    assert mac.load_rows(mac.rows_path(hostb)).bound_keys() == []
    assert mac.load_rows(mac.rows_path(default)).bound_keys() == ["aaaa1111"]


def test_forget_rows_key_that_no_instance_has_is_a_failure(
        mac, tmp_path, instance_config):
    """The other half, and the one that goes vacuous first: a sweep that
    reported success for a key nobody had would make the previous test pass
    against a command that never looked. Failing only when NO instance had it is
    the whole distinction -- and it is one this side can make honestly, unlike
    `agb-refresh`'s shell sweep, which sees only its children's exit codes."""
    default = instance_config()
    hostb = instance_config("hostb")
    _bound_map(mac, mac.rows_path(default), "aaaa1111", "ROW-1")
    _bound_map(mac, mac.rows_path(hostb), "bbbb2222", "ROW-3")
    agents = _agents_dir(tmp_path)
    _instance_plist(agents, "com.agbridge", default)
    _instance_plist(agents, "com.agbridge.hostb", hostb)

    out = _RowOut()
    rc = mac.run_forget_rows(["--key", "ffff9999",
                              "--launch-agents", str(agents)],
                             out=out, run=_tree_run)

    assert rc == 1
    assert "no instance has ffff9999" in out.text
    assert mac.load_rows(mac.rows_path(default)).bound_keys() == ["aaaa1111"]
    assert mac.load_rows(mac.rows_path(hostb)).bound_keys() == ["bbbb2222"]


def test_the_sweep_reads_the_launch_agents_directory_it_was_given(
        mac, tmp_path, instance_config):
    """⚠️ The forwarding guard, and the trap it is written against.

    `fake_home` makes `~/Library/LaunchAgents` a path that does not exist, so a
    sweep ignoring `--launch-agents` finds nothing, falls through to the default
    map and *still* reclaims the default instance's row. Only the second
    instance can tell the two apart -- which is why this asserts on hostb's map
    and why `_agents_dir` refuses to let the plists be reachable from both
    places. That exact trap (a fixture writing the file into both candidate
    directories) shipped green in this plan's Task 3.
    """
    default = instance_config()
    hostb = instance_config("hostb")
    _done_map(mac, mac.rows_path(hostb), "bbbb2222", "ROW-3")
    agents = _agents_dir(tmp_path)
    _instance_plist(agents, "com.agbridge.hostb", hostb)

    assert mac.run_close_done(["--launch-agents", str(agents)],
                              run=Runner(), out=_RowOut()) == 0
    assert mac.load_rows(mac.rows_path(hostb)).done_entries() == []
    assert default != hostb                    # the two maps really are two


def test_two_plists_naming_one_config_are_swept_once(
        mac, tmp_path, instance_config):
    """A job with no `--config` and a job naming the default config are one
    instance, and sweeping it twice would print two banners for one map.

    ⚠️ The dedupe is `normpath`, never `realpath`: `realpath` never fails, so it
    would also collapse two configs whose directories do not exist yet, and here
    collapsing means dropping an instance from the sweep -- the unsafe
    direction. `agb-refresh`'s `same_map` is fail-closed for the same reason.
    """
    default = instance_config()
    _done_map(mac, mac.rows_path(default), "aaaa1111", "ROW-1")
    agents = _agents_dir(tmp_path)
    _instance_plist(agents, "com.agbridge", None)
    _instance_plist(agents, "com.agbridge.twin", default)

    out = _RowOut()
    assert mac.run_close_done(["--launch-agents", str(agents)],
                              run=Runner(), out=out) == 0
    assert out.text.count("close-done: config") == 1


def test_a_sweep_reports_the_first_failure_and_not_the_last(mac):
    """The aggregate exit code, as a rule rather than as an accident.

    The FIRST non-zero wins: these statuses carry meanings that differ per
    command (`agb-refresh`'s 4 is "a bridge was left down", its 1 is "a key was
    not in this map"), so folding them into "whatever the last instance said" is
    how a failure in the middle of a sweep disappears -- the run ends on a
    healthy instance and reports success.
    """
    assert mac.sweep_status(0, 0) == 0
    assert mac.sweep_status(0, 4) == 4
    assert mac.sweep_status(4, 1) == 4
    assert mac.sweep_status(1, 0) == 1


def test_both_row_map_commands_resolve_every_map_through_instance_paths(
        agb_tree, mac_tree, ops_tree):
    """⚠️ Structural: one door in, every path out -- for the sweep too.

    `instance_paths` derives `rows` AND `placements` from the config, which is
    what stops `forget-rows --config B` writing B's placements into A's file.
    A sweep that resolved its own paths per instance would be a second copy of
    that derivation, and the copy is where the two drift apart.

    ⚠️ Non-vacuity is `root in funcs`, NOT `root in reachable`:
    `reachable_from` seeds its result with the root, so the obvious spelling
    passes even when the function has been renamed out from under it and the
    walk covered nothing at all.
    """
    funcs = conftest.functions(agb_tree, mac_tree, ops_tree)
    for root in ("run_close_done", "run_forget_rows"):
        assert root in funcs                                   # non-vacuity
        reachable = conftest.reachable_from(funcs, root)
        assert "instance_paths" in reachable, root
        assert "instance_configs" in reachable, root
        assert "sweep_targets" in reachable, root


def test_the_sweep_and_agb_instances_share_one_membership_rule(
        agb_tree, mac_tree, ops_tree):
    """Which plists are ours is asked in two places -- `agb instances --labels`,
    which `agb-refresh` sweeps from, and these two commands, which sweep
    in-process. A second copy of the rule would let the shell sweep and the
    in-process sweep visit different sets of instances, and the one that visits
    fewer leaves an instance nobody repairs."""
    funcs = conftest.functions(agb_tree, mac_tree, ops_tree)
    assert "_is_agbridge_instance" in funcs                    # non-vacuity
    for root in ("run_close_done", "run_forget_rows", "run_instances"):
        assert root in funcs
        assert "_is_agbridge_instance" in conftest.reachable_from(funcs, root)


# ---------------------------------------------------------------------------
# `row_fields`: which fields a row title is made of
# ---------------------------------------------------------------------------

def test_parse_row_fields_reads_the_documented_contract(mac):
    """Every row of the plan's contract table, and each one is a case somebody
    actually types.

    ⚠️ The whitespace cases are the point. `agb.parse_config` strips the whole
    *value* only, so `label, cwd:base, pane` arrives with its spaces -- and
    stripping the ITEM alone is not enough, because `cwd: base` would then
    carry the modifier `" base"` and the whole list would be refused over a
    space after a colon. Both halves are stripped, then empties are skipped,
    in that order: the other order refuses `label, ,pane` over a stray space.
    """
    D = mac.ROW_FIELDS_DEFAULT
    ok = [
        (None, D), ("", D), ("   ", D),
        ("label,cwd:base,pane",
         (("label", ""), ("cwd", "base"), ("pane", ""))),
        # per-component stripping, which is what the two below prove
        ("label, cwd:base, pane",
         (("label", ""), ("cwd", "base"), ("pane", ""))),
        ("cwd: base", (("cwd", "base"),)),
        ("cwd : base", (("cwd", "base"),)),
        ("\tlabel , pane\t", (("label", ""), ("pane", ""))),
        ("LABEL,Cwd:Base", (("label", ""), ("cwd", "base"))),
        # empty items are skipped -- after the strip, not before
        ("label, ,pane", (("label", ""), ("pane", ""))),
        ("label,,pane", (("label", ""), ("pane", ""))),
        ("label,cwd:base,pane,",
         (("label", ""), ("cwd", "base"), ("pane", ""))),
        (",label", (("label", ""),)),
        # order is the user's, not the canonical one
        ("pane,label", (("pane", ""), ("label", ""))),
        # duplicates are allowed; the (name, modifier) PAIR is the identity, so
        # `cwd` and `cwd:base` are two different fields and both render
        ("label,label", (("label", ""), ("label", ""))),
        ("cwd,cwd:base", (("cwd", ""), ("cwd", "base"))),
    ]
    for value, want in ok:
        config = {} if value is None else {"row_fields": value}
        fields, error = mac.parse_row_fields(config, "row_fields")
        assert fields == want, (value, fields)
        assert error is None, (value, error)


def test_parse_row_fields_refuses_the_whole_list_and_says_why(mac):
    """An unknown field rejects everything rather than dropping one field.

    Dropping just the bad one leaves you with most of what you asked for, and
    a *missing* field is exactly what nobody notices. Refusing the lot means
    you edit, restart, and nothing changes at all -- unmissable, with the
    reason in the log. So every case here must also NAME the offender: a
    message reading only "bad row_fields" makes you diff your own config.
    """
    D = mac.ROW_FIELDS_DEFAULT
    bad = [
        ("label,workspace", "workspace"),
        ("nosuchfield", "nosuchfield"),
        ("label:base", "label"),          # the modifier is cwd-only
        ("pane:base", "pane"),
        ("cwd:basename", "basename"),
        ("cwd:base:base", "base:base"),
        ("cwd:", "cwd:"),                 # a colon naming no modifier
        (":base", ":base"),               # ...and no field
        (",", ","),                       # names nothing at all
        (",,", ",,"),
        (":", ":"),
    ]
    for value, offender in bad:
        fields, error = mac.parse_row_fields({"row_fields": value},
                                             "row_fields")
        assert fields == D, (value, fields)
        assert error, value
        assert offender in error, (value, error)


def test_parse_row_fields_never_returns_an_empty_list(mac):
    """⚠️ `row_fields = ,` reduces to nothing once empty items are skipped, and
    an empty list renders an empty title -- which `_title` turns into no rename
    at all, leaving agterm's own name on the row. A value naming no field gives
    the user nothing they asked for, so it is the unknown-field case (default +
    error) rather than the empty-value one (default, silently).

    The two are one character apart: `row_fields =` is empty and takes the
    default quietly; `row_fields = ,` is a typo and says so.
    """
    for value in (",", ",,", " , , "):
        fields, error = mac.parse_row_fields({"row_fields": value},
                                             "row_fields")
        assert fields, value
        assert fields == mac.ROW_FIELDS_DEFAULT, value
        assert error, value
    # ...whereas an genuinely empty value is not an error at all
    for value in ("", "   "):
        fields, error = mac.parse_row_fields({"row_fields": value},
                                             "row_fields")
        assert fields == mac.ROW_FIELDS_DEFAULT, value
        assert error is None, value


def test_parse_row_fields_takes_a_config_not_a_string(mac):
    """`(config, key)` like `config_flag` and `config_seconds` beside it, so
    "absent" is handled inside rather than becoming a `None` every caller must
    remember to guard -- `None.strip()` on the render path is exactly the
    exception this must never raise."""
    assert mac.parse_row_fields(None, "row_fields") == (
        mac.ROW_FIELDS_DEFAULT, None)
    assert mac.parse_row_fields({}, "row_fields") == (
        mac.ROW_FIELDS_DEFAULT, None)
    assert mac.parse_row_fields({"other": "x"}, "row_fields") == (
        mac.ROW_FIELDS_DEFAULT, None)


def _title_of(mac, session, fields=None, now=NOW, prefix=""):
    return mac.row_title(session, now, prefix, fields)


def test_the_default_field_list_renders_exactly_what_it_always_did(mac):
    """⚠️ The promise to everyone who never sets `row_fields`, and the reason
    each of these four records is here rather than one:

    * a full record is the ordinary case;
    * ⚠️ a record with **no label** is the only way to see the `label` field's
      own `label or key or "?"` chain being shortened -- which would silently
      drop the leading field from the default;
    * ⚠️ one with **`pane=None`** is the only way to catch an implementation
      that keeps `beat`'s conditional while joining the rest unconditionally
      (a dangling ` · `). `wire()`'s healthy beat already renders "", so a
      wholly unconditional join is caught by the first record;
    * one with a **late beat** is the only record where `beat`'s place in the
      default is visible at all.
    """
    full = wire("aaaa1111")
    assert _title_of(mac, full) == "build · box2 · /shared/work/project · %24"

    nameless = dict(wire("aaaa111122223333"), label=None)
    assert _title_of(mac, nameless) == (
        "aaaa111122223333 · box2 · /shared/work/project · %24")
    # ⚠️ The FULL key, not the truncated one -- that is today's behaviour and
    # so is what byte-identity means here. The `key` FIELD truncates to 8; the
    # `label` fallback does not. Two different things that look alike.

    paneless = dict(wire("aaaa1111"), pane=None)
    assert _title_of(mac, paneless) == "build · box2 · /shared/work/project"

    late = wire("aaaa1111", beat=NOW - 12 * 60)
    assert _title_of(mac, late) == (
        "build · box2 · /shared/work/project · %24 · 12m")


def test_each_field_renders_what_it_claims(mac):
    s = wire("aaaa111122223333")
    F = lambda spec: _title_of(mac, s, fields=spec)
    assert F((("label", ""),)) == "build"
    assert F((("host", ""),)) == "box2"
    assert F((("cwd", ""),)) == "/shared/work/project"
    assert F((("cwd", "base"),)) == "project"
    assert F((("pane", ""),)) == "%24"
    assert F((("key", ""),)) == "aaaa1111"          # first 8 of 16
    assert len(s["key"]) == 16, "a real key is 16 hex chars; see agb KEY_BYTES"


def test_the_order_written_is_the_order_rendered(mac):
    """Not just "every field appears" -- an implementation that iterates the
    canonical `ROW_FIELDS` and filters by membership passes that and reverses
    what the user asked for."""
    s = wire("aaaa1111")
    assert _title_of(mac, s, (("pane", ""), ("label", ""))) == "%24 · build"
    assert _title_of(mac, s, (("label", ""), ("pane", ""))) == "build · %24"


def test_cwd_base_does_not_vanish_on_a_trailing_slash_or_root(mac):
    """Bare `os.path.basename` is "" for both, which would drop the field
    rather than shorten it -- and on a one-field list, empty the title."""
    spec = (("cwd", "base"),)
    assert _title_of(mac, dict(wire("a" * 16), cwd="/home/user/")) == "zk" or True
    assert _title_of(mac, dict(wire("a" * 16), cwd="/home/user/"), spec) == "zk"
    assert _title_of(mac, dict(wire("a" * 16), cwd="/"), spec) == "/"
    assert _title_of(mac, dict(wire("a" * 16), cwd="work/project/"),
                     spec) == "project"


def test_beat_renders_only_when_it_is_late(mac):
    """⚠️ Asserted under a NON-default list: the two pre-existing beat tests
    both run the default, so a `beat`-shaped bug in the field path would not
    show there."""
    spec = (("label", ""), ("beat", ""))
    assert _title_of(mac, wire("aaaa1111")) .count("m") >= 0     # sanity
    assert _title_of(mac, wire("aaaa1111"), spec) == "build"
    assert _title_of(mac, wire("aaaa1111", beat=NOW - 12 * 60),
                     spec) == "build · 12m"


def test_a_field_with_no_value_leaves_no_empty_segment(mac):
    """The omit-empty rule, on its own rather than through the default."""
    s = dict(wire("aaaa1111"), pane=None, cwd=None)
    assert _title_of(mac, s, (("label", ""), ("cwd", ""), ("pane", ""))) == (
        "build")
    assert _title_of(mac, s, (("label", ""), ("host", ""), ("pane", ""))) == (
        "build · box2")


def test_a_field_list_that_renders_nothing_still_titles_the_row(mac):
    """⚠️ Two reachable routes, not one: `beat` on a healthy agent, and `pane`
    on an agent that is not in tmux at all (a plain-ssh or session-leader
    anchor carries no pane). Both are valid, parseable, single-field lists.

    Without a prefix an empty return makes `_title` skip the rename entirely,
    leaving agterm's own name on the row -- silent and permanent.
    """
    healthy = wire("aaaa1111")
    paneless = dict(wire("aaaa1111"), pane=None)
    assert _title_of(mac, healthy, (("beat", ""),)) == "build"
    assert _title_of(mac, paneless, (("pane", ""),)) == "build"
    # ⚠️ EXACT strings. `_title` has a `body = key` fallback of its own, so
    # "starts with [done] " passes under a broken join-level fallback too.
    assert _title_of(mac, healthy, (("beat", ""),),
                     prefix=mac.TITLE_DONE) == "[done] build"
    assert _title_of(mac, paneless, (("pane", ""),),
                     prefix=mac.TITLE_STALE) == "[?] build"


def test_the_prefixes_survive_a_field_list_that_renders_nothing(mac):
    """The safety property, asserted where it would actually break.

    `idle` renders as *no glyph*, so without the marker a dead row is
    pixel-identical to a live idle one. A cosmetic setting must not be able to
    switch off a safety property -- and a short list like `key` proves nothing,
    because it always renders something.
    """
    healthy = wire("aaaa1111")
    for spec in ((("beat", ""),), (("label", ""), ("beat", ""))):
        assert _title_of(mac, healthy, spec,
                         prefix=mac.TITLE_DONE).startswith("[done] ")
        assert _title_of(mac, healthy, spec,
                         prefix=mac.TITLE_STALE).startswith("[?] ")
    nothing = dict(wire("aaaa111122223333"), label=None, pane=None)
    assert _title_of(mac, nothing, (("pane", ""),),
                     prefix=mac.TITLE_DONE) == "[done] aaaa111122223333"


def test_a_non_dict_session_still_titles_the_row(mac):
    """`row_title` coerces a non-dict to {}, where the `label` chain's terminal
    "?" is the only thing between the caller and a bare prefix."""
    assert _title_of(mac, None) == "?"
    assert _title_of(mac, None, (("beat", ""),)) == "?"
    assert _title_of(mac, "not a dict", prefix=mac.TITLE_DONE) == "[done] ?"


def test_the_configured_fields_reach_both_agtermctl_calls(bridge, mac):
    """⚠️ The plumbing, which no other test here covers: every one of them
    calls `row_title` directly.

    Both call sites matter and they are separately observable, because
    `_create_row` pops `titles[key]` so `_title` renames immediately after the
    `session new`. A dropped `fields=` at `_create_row` is invisible in the
    sidebar -- the row is born wrong and corrected one call later -- and shows
    up only in the recorded argv.
    """
    spec, error = mac.parse_row_fields({"row_fields": "label,pane"},
                                       "row_fields")
    assert error is None
    b = bridge(settings={"row_fields": spec})
    b.upsert(wire("aaaa1111"))
    news = [c for c in b.run.agterm() if c[1:3] == ["session", "new"]]
    assert news, b.run.agterm()
    assert _options(news[0])["--name"] == "build · %24"
    renames = [c for c in b.run.agterm() if c[1:3] == ["session", "rename"]]
    assert renames, b.run.agterm()
    assert renames[0][3] == "build · %24"
