"""Task 6b -- `agb prune`: the operator terminal path.

This is the only destructive command in the tool, and it operates on exactly
the entries the tool can prove nothing about. So the tests here are not really
about removal working; they are about the two ways review found to get it
wrong, and about the fact that neither can be reintroduced quietly.

**Age is not death.** The candidate list is an age heuristic on a host nothing
can currently speak for. On machine #3 there is no feed, so the only beat source
is hooks -- and a `blocked` agent waiting on you fires none, so its beat freezes
while it is perfectly alive. A blanket `--force` over that list would be design
amendment 1's own withdrawn rule relocated into a *destructive* command, so
`--force` does not exist and `--yes` is refused unless the operator named the
entries. The mirror of Task 5's "a live `blocked` at 30 minutes is still shown"
is here: a live `blocked` foreign entry is still *kept*.

**Own-host authority does not extend to a foreign marker.** Rebuilding
`gen/#3.marker` from `readdir` on box #2 uses a view that can be `acdirmax=60`
stale, which would drop a key #3 created a minute ago, emit `remove` for a live
agent, and let #3's next transition mint a duplicate row. The marker is
therefore derived by subtracting the pruned keys from that marker's **own**
last-read content -- asserted here against a deliberately stale listing, the
same way `tests/test_doctor.py` asserts it for discovery.
"""

import ast
import json
import os
import shutil
import sys
import time

import pytest

import conftest


HOST = "box2"
FOREIGN = "box3"            # "machine #3" from box #2's point of view

LIVE_PID, LIVE_START = conftest.live_agent()
DEAD_PID, DEAD_START = conftest.dead_agent()

NOW = 1785000000.0


@pytest.fixture
def sd(agb, statedir, set_host):
    set_host(HOST)
    return str(statedir)


def write_session(agb, sd, host, key, state="active", seq=1, pid=None,
                  starttime=None, cwd="/shared/work/task", pane="%24",
                  label=None):
    """One session, written the way a hook writes it: `.json`, `.state`, marker."""
    if pid is None:
        pid = DEAD_PID
    if starttime is None:
        starttime = DEAD_START
    agb.ensure_session_dir(sd, host)
    record = {
        "v": 1, "key": key, "label": label or ("lbl-" + key), "host": host,
        "pid": pid, "starttime": starttime, "tmux": "sess", "pane": pane,
        "cwd": cwd, "state": state, "seq": seq, "updated": NOW,
    }
    agb.atomic_write(agb.record_path(sd, key, host),
                     json.dumps(record, sort_keys=True) + "\n")
    agb.write_in_place(agb.state_path(sd, key, host),
                       agb.format_state(state, host, pid, starttime, seq))
    agb.rebuild_marker(sd, host)
    return key


class Out(object):
    """A collecting `out`, so assertions are about text rather than capsys."""

    def __init__(self):
        self.text = ""

    def write(self, data):
        self.text += data

    def flush(self):
        pass


class Ask(object):
    """A scripted `ask`, recording every prompt it was shown."""

    def __init__(self, answer=False):
        self.answer = answer
        self.prompts = []

    def __call__(self, prompt):
        self.prompts.append(prompt)
        if callable(self.answer):
            return self.answer(prompt)
        return self.answer


def exists(agb, sd, host, key):
    return (os.path.exists(agb.state_path(sd, key, host)),
            os.path.exists(agb.record_path(sd, key, host)))


def quiet(agb, sd, host, age=3600.0):
    """Make `host` look quiet: an old `sweep/<host>.marker`.

    Ages are stamped relative to **real** time, not to `NOW`: `run_prune` takes
    its `now` from the write probe's server-stamped mtime (constraint #12), so a
    fabricated epoch here would make every age a year old and every host quiet.
    """
    path = agb.sweep_marker_path(sd, host)
    agb.write_in_place(path, "x\n")
    when = time.time() - age
    os.utime(path, (when, when))
    return path


# ---------------------------------------------------------------------------
# listing, confirming, removing
# ---------------------------------------------------------------------------

def test_prune_lists_the_candidates_with_everything_needed_to_recognise_them(
        agb, ops, sd):
    """State, beat age, host, cwd and pane -- the checkbox's list, because an
    operator cannot consent to deleting something they cannot identify."""
    write_session(agb, sd, FOREIGN, "bbbb2222", state="active",
                  cwd="/shared/work/proj", pane="%7", label="build")
    quiet(agb, sd, FOREIGN)
    out, ask = Out(), Ask(False)

    assert ops.run_prune([], out=out, ask=ask) == 0

    assert "box3/bbbb2222" in out.text
    assert "active" in out.text
    assert "/shared/work/proj" in out.text
    assert "%7" in out.text
    assert "build" in out.text
    assert "beat" in out.text
    assert ops.UNADJUDICABLE_CAVEAT in out.text


def test_nothing_is_removed_when_the_answer_is_no(agb, ops, sd):
    key = write_session(agb, sd, FOREIGN, "bbbb2222")
    quiet(agb, sd, FOREIGN)
    ask = Ask(False)

    assert ops.run_prune([], out=Out(), ask=ask) == 0

    assert ask.prompts                                  # it really did ask
    assert exists(agb, sd, FOREIGN, key) == (True, True)
    assert agb.read_marker_keys(sd, FOREIGN) == [key]


def test_a_confirmed_entry_is_removed_and_leaves_the_marker(agb, ops, sd):
    key = write_session(agb, sd, FOREIGN, "bbbb2222")
    quiet(agb, sd, FOREIGN)

    assert ops.run_prune([], out=Out(), ask=Ask(True)) == 0

    assert exists(agb, sd, FOREIGN, key) == (False, False)
    assert agb.read_marker_keys(sd, FOREIGN) == []


def test_the_prompt_is_per_entry_and_each_answer_is_obeyed(agb, ops, sd):
    """Per entry, not per run: the whole point is that consent is about one
    specific thing at a time."""
    for key in ("aaaa1111", "bbbb2222", "cccc3333"):
        write_session(agb, sd, FOREIGN, key)
    quiet(agb, sd, FOREIGN)
    ask = Ask(lambda prompt: "bbbb2222" in prompt)

    ops.run_prune([], out=Out(), ask=ask)

    assert len(ask.prompts) == 3
    assert exists(agb, sd, FOREIGN, "aaaa1111") == (True, True)
    assert exists(agb, sd, FOREIGN, "bbbb2222") == (False, False)
    assert exists(agb, sd, FOREIGN, "cccc3333") == (True, True)


def test_pruning_one_entry_removes_no_other_key_from_that_hosts_marker(
        agb, ops, sd):
    """The named test. A marker rewrite that dropped a sibling would emit
    `remove` on the wire for a row nobody touched."""
    for key in ("aaaa1111", "bbbb2222", "cccc3333"):
        write_session(agb, sd, FOREIGN, key)
    quiet(agb, sd, FOREIGN)

    ops.run_prune([], out=Out(), ask=Ask(lambda p: "bbbb2222" in p))

    assert agb.read_marker_keys(sd, FOREIGN) == ["aaaa1111", "cccc3333"]


def test_a_blocked_entry_is_flagged_before_the_prompt_not_after(agb, ops, sd):
    """The specific way this heuristic is wrong, said where it can still change
    the answer."""
    write_session(agb, sd, FOREIGN, "bbbb2222", state="blocked")
    quiet(agb, sd, FOREIGN)
    out, ask = Out(), Ask(False)

    ops.run_prune([], out=out, ask=ask)

    warning = out.text.index(ops.PRUNE_BLOCKED_WARNING)
    assert warning < len(out.text)
    assert "waiting for your input" in ops.PRUNE_BLOCKED_WARNING
    # ...and shown before anything was asked: the prompt is the last thing.
    assert out.text.rstrip().endswith("kept")


def test_nothing_to_prune_says_so_rather_than_staying_silent(ops, sd):
    out = Out()
    assert ops.run_prune([], out=out, ask=Ask(True)) == 0
    assert "nothing to prune" in out.text


def test_a_dry_run_removes_nothing(agb, ops, sd):
    key = write_session(agb, sd, FOREIGN, "bbbb2222")
    quiet(agb, sd, FOREIGN)
    out = Out()

    assert ops.run_prune(["--dry-run"], out=out, ask=Ask(True)) == 0

    assert "dry run: 1 of 1" in out.text
    assert exists(agb, sd, FOREIGN, key) == (True, True)


def test_a_breadcrumb_records_that_a_human_ended_the_row(agb, ops, sd):
    """"Why did this row disappear?" must stay answerable, and for a pruned
    entry the answer is the one no other part of the tool can produce."""
    key = write_session(agb, sd, FOREIGN, "bbbb2222")
    quiet(agb, sd, FOREIGN)

    ops.run_prune([], out=Out(), ask=Ask(True))

    with open(agb.err_log_path(sd, key, FOREIGN)) as handle:
        log = handle.read()
    assert "pruned by an operator on %s" % (HOST,) in log
    assert "no proof of death" in log


# ---------------------------------------------------------------------------
# age is not death: no blanket force, and proof of life outranks the prompt
# ---------------------------------------------------------------------------

def test_yes_without_an_explicit_key_list_is_refused(ops, agb):
    """The blanket `--force`, under its real name. Amendment 1's withdrawn rule
    relocated into a destructive command is still amendment 1's withdrawn
    rule."""
    with pytest.raises(agb.AgbError) as excinfo:
        ops.parse_prune_args(["--yes"])
    assert "--key" in str(excinfo.value)


def test_force_is_rejected_with_the_reason_rather_than_as_a_typo(ops, agb):
    """The next person to want one will type it before they read the file."""
    with pytest.raises(agb.AgbError) as excinfo:
        ops.parse_prune_args(["--force"])
    message = str(excinfo.value)
    assert "no --force" in message
    assert "not known to be dead" in message


def test_yes_with_a_key_list_needs_no_prompt(agb, ops, sd):
    key = write_session(agb, sd, FOREIGN, "bbbb2222")
    quiet(agb, sd, FOREIGN)
    ask = Ask(False)

    ops.run_prune(["--key", "%s/%s" % (FOREIGN, key), "--yes"],
                  out=Out(), ask=ask)

    assert ask.prompts == []
    assert exists(agb, sd, FOREIGN, key) == (False, False)


def test_prune_refuses_to_remove_a_live_own_host_entry(agb, ops, sd):
    """Proof of life outranks anything typed at a prompt. Here the pid is in
    our own namespace, so the answer is real rather than a coincidence."""
    key = write_session(agb, sd, HOST, "aaaa1111", pid=LIVE_PID,
                        starttime=LIVE_START)
    out = Out()

    ops.run_prune(["--key", "%s/%s" % (HOST, key), "--yes"], out=out,
                  ask=Ask(True))

    assert exists(agb, sd, HOST, key) == (True, True)
    assert "KEPT" in out.text
    assert "alive on this host" in out.text


def test_a_live_blocked_foreign_entry_survives_a_declined_confirmation(
        agb, ops, sd):
    """The executable form of amendment 1, and the mirror of Task 5's "a live
    `blocked` at 30 minutes is still shown".

    Its pid is #3's, so nothing here can adjudicate it; its beat is 30 minutes
    old because a `blocked` agent waiting on you fires no hooks. It is offered,
    it is flagged, and without a `y` it stays.
    """
    key = write_session(agb, sd, FOREIGN, "bbbb2222", state="blocked")
    beat = time.time() - 1800
    os.utime(agb.state_path(sd, key, FOREIGN), (beat, beat))
    quiet(agb, sd, FOREIGN)
    out = Out()

    ops.run_prune([], out=out, ask=Ask(False))

    assert exists(agb, sd, FOREIGN, key) == (True, True)
    assert agb.read_marker_keys(sd, FOREIGN) == [key]
    assert "30 min" in out.text
    assert ops.PRUNE_BLOCKED_WARNING in out.text


def test_our_own_entries_are_never_candidates_by_themselves(agb, ops, sd):
    """The unadjudicable list excludes them because this host can adjudicate
    them for real -- so an unqualified `prune` must never offer one."""
    key = write_session(agb, sd, HOST, "aaaa1111")
    quiet(agb, sd, HOST)
    ask = Ask(True)

    ops.run_prune([], out=Out(), ask=ask)

    assert ask.prompts == []
    assert exists(agb, sd, HOST, key) == (True, True)


def test_a_recently_swept_host_offers_nothing(agb, ops, sd):
    write_session(agb, sd, FOREIGN, "bbbb2222")
    quiet(agb, sd, FOREIGN, age=10.0)
    out = Out()
    ops.run_prune([], out=out, ask=Ask(True))
    assert "nothing to prune" in out.text


def test_a_torn_state_is_never_offered_by_the_derived_list(agb, ops, sd):
    """Constraint #8 where it belongs: a peer mid-`O_TRUNC` looks exactly like a
    corrupt file, so nothing the tool DERIVES may include one. The heuristic
    list is that derivation, and it must not so much as prompt about it."""
    key = write_session(agb, sd, FOREIGN, "bbbb2222")
    agb.write_in_place(agb.state_path(sd, key, FOREIGN), "")
    quiet(agb, sd, FOREIGN, age=4000.0)
    ask = Ask(True)
    out = Out()

    ops.run_prune([], out=out, ask=ask)

    assert ask.prompts == []
    assert os.path.exists(agb.state_path(sd, key, FOREIGN))
    assert "nothing to prune" in out.text


def test_a_named_unreadable_state_is_removable_and_says_why(agb, ops, sd):
    """⚠️ The rule above used to apply to `--key` too, and that left a file NO
    COMMAND IN THE TOOL could clear. `write_in_place` opens `.state` with
    `O_TRUNC`, so a hook killed in that window leaves a zero-length file; if its
    agent is dead the sweep skips it for ever, the feed retains it and withholds
    every snapshot's removal authority, and prune refused it by name as well.
    Rows adopted after a launchd restart could then never be reclaimed -- `agr`
    failure mode #3 rebuilt. `--key` is the named, human-gated path built for
    entries nothing can adjudicate, so it is the one that may clear this."""
    key = write_session(agb, sd, FOREIGN, "bbbb2222")
    agb.write_in_place(agb.state_path(sd, key, FOREIGN), "")
    ask = Ask(True)
    out = Out()

    ops.run_prune(["--key", "%s/%s" % (FOREIGN, key)], out=out, ask=ask)

    assert not os.path.exists(agb.state_path(sd, key, FOREIGN))
    assert not os.path.exists(agb.record_path(sd, key, FOREIGN))
    # The prompt itself carries the risk, not only the block above it.
    assert "CANNOT BE READ" in ask.prompts[0]
    assert "may be a live peer caught mid-write" in ask.prompts[0]
    assert "UNREADABLE" in out.text
    assert "cannot be read or parsed" in out.text


def test_an_unreadable_entry_is_kept_when_the_operator_says_no(agb, ops, sd):
    """The confirmation is real: `--key` offers it, the answer decides it."""
    key = write_session(agb, sd, FOREIGN, "bbbb2222")
    agb.write_in_place(agb.state_path(sd, key, FOREIGN), "")
    out = Out()

    ops.run_prune(["--key", "%s/%s" % (FOREIGN, key)], out=out, ask=Ask(False))

    assert os.path.exists(agb.state_path(sd, key, FOREIGN))
    assert "kept" in out.text


def test_a_live_agent_is_kept_even_when_its_state_is_unreadable(agb, ops, sd):
    """⚠️ The relaxation that lets `--key` clear an unreadable `.state` must not
    take the proof-of-life rule with it -- and it did.

    A torn `.state` is *caused by an agent writing*, so this is exactly where a
    live agent is most likely. `.json` is written by `atomic_write` (temp +
    rename) in the same `_write_session` as the in-place `.state`, so it never
    tears and still carries the same `pid`/`starttime`. `describe_unreadable`
    used to throw them away and build the entry with `pid: None`, which made
    `proof_of_life` return False and `prune_refusal` refuse **nothing**: this
    exact command removed a live agent's entry with no prompt and no refusal,
    while `kill(pid, 0)` on the pid the intact `.json` named still succeeded.
    """
    key = write_session(agb, sd, FOREIGN, "bbbb2222", state="blocked",
                        pid=LIVE_PID, starttime=LIVE_START)
    agb.write_in_place(agb.state_path(sd, key, FOREIGN), "")
    ask = Ask(True)
    out = Out()

    ops.run_prune(["--key", "%s/%s" % (FOREIGN, key), "--yes"], out=out,
                  ask=ask)

    assert exists(agb, sd, FOREIGN, key) == (True, True)
    assert "KEPT" in out.text
    assert str(LIVE_PID) in out.text
    assert ask.prompts == []                  # --yes never even got the chance


def test_an_unreadable_entry_shows_what_its_record_still_says(agb, ops, sd):
    """The `.json` survives the torn `.state`, so the block is not blank: it
    names the state and pid the proof-of-life rule was run against, and carries
    `PRUNE_BLOCKED_WARNING` when that state is `blocked` -- the one class this
    design most wants protected (a blocked agent fires no hooks, so its files
    may not come back until the human answers) used to lose both."""
    key = write_session(agb, sd, FOREIGN, "bbbb2222", state="blocked")
    agb.write_in_place(agb.state_path(sd, key, FOREIGN), "")
    out = Out()

    ops.run_prune(["--key", "%s/%s" % (FOREIGN, key), "--yes"], out=out,
                  ask=Ask(False))

    assert "UNREADABLE" in out.text
    assert "blocked" in out.text
    assert str(DEAD_PID) in out.text
    assert ops.PRUNE_BLOCKED_WARNING in out.text
    # A genuine orphan still goes: the relaxation is preserved intact.
    assert exists(agb, sd, FOREIGN, key) == (False, False)


def test_an_entry_that_is_already_gone_is_reported_not_invented(agb, ops, sd):
    write_session(agb, sd, FOREIGN, "bbbb2222")
    out = Out()
    ops.run_prune(["--key", "%s/%s" % (FOREIGN, "dddd4444"), "--yes"],
                  out=out, ask=Ask(True))
    assert "already gone" in out.text


# ---------------------------------------------------------------------------
# the foreign marker: subtraction from its own content, never a readdir
# ---------------------------------------------------------------------------

def test_the_foreign_marker_is_derived_from_its_own_content_not_from_readdir(
        agb, ops, sd, monkeypatch):
    """The second named defect. `readdir(sessions/<foreign>/)` can be served
    from cache for up to `acdirmax=60` s, so a marker rebuilt from one would
    silently drop a key that host created a minute ago -- `remove` for a live
    agent, and then a duplicate row when #3's next transition restores it.

    The listing is stubbed stale (it hides `cccc3333` entirely); the surviving
    key must still be in the marker afterwards, and the session directory must
    not have been listed at all.
    """
    for key in ("bbbb2222", "cccc3333"):
        write_session(agb, sd, FOREIGN, key)
    quiet(agb, sd, FOREIGN)

    seen = []
    real = os.listdir
    foreign_dir = os.path.join("sessions", FOREIGN)

    def spy(path):
        seen.append(str(path))
        if str(path).endswith(foreign_dir):
            return ["bbbb2222.state"]           # a cached, out-of-date listing
        return real(path)

    monkeypatch.setattr(os, "listdir", spy)
    ops.run_prune([], out=Out(), ask=Ask(lambda p: "bbbb2222" in p))

    assert agb.read_marker_keys(sd, FOREIGN) == ["cccc3333"]
    assert not [path for path in seen if path.endswith(foreign_dir)]


def test_an_unreadable_marker_leaves_it_alone_and_still_removes_the_entry(
        agb, ops, sd):
    """A marker that fails validation is no information, so it is not rewritten
    from a guess. The removal still stands: it is proven to the feed per key by
    name (`ENOENT`), which is what makes the rewrite an optimisation rather than
    the mechanism.
    """
    key = write_session(agb, sd, FOREIGN, "bbbb2222")
    quiet(agb, sd, FOREIGN)
    marker = agb.marker_path(sd, FOREIGN)
    agb.atomic_write(marker, "bbbb2222\n")             # no #end sentinel
    out = Out()

    ops.run_prune(["--key", "%s/%s" % (FOREIGN, key), "--yes"], out=out,
                  ask=Ask(True))

    assert exists(agb, sd, FOREIGN, key) == (False, False)
    with open(marker) as handle:
        assert handle.read() == "bbbb2222\n"           # untouched
    assert "left as it stands" in out.text
    assert agb.read_state_entry(sd, FOREIGN, key)[0] is agb.STATE_GONE


def test_the_marker_subtraction_is_a_pure_function_of_its_own_content(agb, ops,
                                                                      sd):
    """Unit form: a key present in the marker but with no session file behind it
    is *kept* unless it was one of the ones removed. Only the named keys go."""
    agb.atomic_write(agb.marker_path(sd, FOREIGN),
                     agb.format_marker(["aaaa1111", "bbbb2222", "cccc3333"]))
    kept = ops.prune_marker(sd, FOREIGN, set(["bbbb2222"]))
    assert kept == ["aaaa1111", "cccc3333"]
    assert agb.read_marker_keys(sd, FOREIGN) == ["aaaa1111", "cccc3333"]


def test_the_idx_entry_of_a_pruned_foreign_session_is_left_alone(agb, ops, sd):
    """A foreign host's anchors are read only by its own hooks, and dropping one
    inside that host's mint race would hand a live agent a second key. Its own
    sweep collects them."""
    key = write_session(agb, sd, FOREIGN, "bbbb2222")
    quiet(agb, sd, FOREIGN)
    anchor = agb.Anchor(FOREIGN, "tmux", 4242, "%9", pane="%9")
    agb.link_idx(agb.idx_path(sd, anchor), key, DEAD_PID, DEAD_START)
    before = sorted(os.listdir(agb.idx_dir(sd)))

    ops.run_prune([], out=Out(), ask=Ask(True))

    assert sorted(os.listdir(agb.idx_dir(sd))) == before


def test_pruning_a_live_agent_is_undone_by_its_very_next_hook(agb, ops, sd,
                                                              set_host,
                                                              set_tmux,
                                                              set_agent_pid,
                                                              fake_proc):
    """The consequence `PRUNE_BLOCKED_WARNING` warns about, followed through.

    `prune_remove` leaves `idx/` deliberately, so a live agent's next hook binds
    the SAME key, sees ENOENT on `.state` and writes it again -- and the feed
    emits an upsert for a key the bridge has just marked `[done]`. That is why
    `RowRenderer` binds such a row back instead of refusing it for ever; here
    the farm half is checked, and `test_bridge_rows` has the Mac half.
    """
    live_pid, live_start = conftest.live_agent()
    set_host(HOST)
    set_tmux("/tmp/tmux-1000/default,4242,0", "%9")
    fake_proc({live_pid: {"comm": "claude", "ppid": 1,
                          "starttime": live_start}})
    set_agent_pid(live_pid)
    agb.cmd_hook(["blocked"])
    key = agb.read_marker_keys(sd, HOST)[0]

    ops.prune_remove(sd, HOST, key)
    assert not os.path.exists(agb.state_path(sd, key, HOST))

    agb.cmd_hook(["active"])
    assert os.path.exists(agb.state_path(sd, key, HOST))
    assert agb.read_marker_keys(sd, HOST) == [key]


def test_the_blocked_warning_names_the_way_back(ops):
    """A warning that describes a hazard and not its recovery is half a
    warning: the row the operator is about to strand reads `[done]` on the Mac
    until `agb close-done` reclaims it."""
    assert "close-done" in ops.PRUNE_BLOCKED_WARNING


# ---------------------------------------------------------------------------
# --via-ssh: make the decision where kill(pid, 0) means something
# ---------------------------------------------------------------------------

def test_the_via_ssh_argv_is_exact(ops):
    argv = ops.prune_ssh_argv("box3.example", "/shared/.agbridge", FOREIGN,
                              ["aaaa1111", "bbbb2222"], "/bin/python3",
                              "/opt/agbridge/agb")
    assert argv == [
        "ssh", "box3.example", "/bin/python3", "-S", "-E", "/opt/agbridge/agb",
        "prune", "--statedir", "/shared/.agbridge",
        "--key", "box3/aaaa1111", "--key", "box3/bbbb2222", "--yes",
    ]


def test_the_via_ssh_argv_carries_the_jump_host(ops):
    argv = ops.prune_ssh_argv("box3", "/s", FOREIGN, ["aaaa1111"],
                              "/bin/python3", "/a/agb", jump="box2")
    assert argv[:4] == ["ssh", "-J", "box2", "box3"]


@pytest.mark.parametrize("bad", [
    {"target": "box3; rm -rf /"},
    {"sd": "/shared/state dir"},
    {"python": "/bin/python3 $(id)"},
    {"agb_path": "relative/agb"},
])
def test_a_word_that_a_remote_shell_would_re_split_is_refused(ops, agb, bad):
    """`ssh host cmd` re-splits the command in a shell, and this is the argv of
    the only destructive command in the tool -- so the check is a whitelist and
    fails closed."""
    args = {"target": "box3", "sd": "/s", "host": FOREIGN,
            "keys": ["aaaa1111"], "python": "/bin/python3",
            "agb_path": "/a/agb"}
    args.update(bad)
    with pytest.raises(agb.AgbError):
        ops.prune_ssh_argv(args["target"], args["sd"], args["host"],
                           args["keys"], args["python"], args["agb_path"])


def test_via_ssh_passes_only_the_confirmed_keys(agb, ops, sd):
    """The remote is handed named entries and `--yes` -- the same contract the
    local path enforces. What it adds is that over there the pid is in the right
    namespace, so `prune_refusal` becomes a proof rather than a coincidence."""
    for key in ("aaaa1111", "bbbb2222"):
        write_session(agb, sd, FOREIGN, key)
    quiet(agb, sd, FOREIGN)
    calls = []

    def run(argv):
        calls.append(argv)
        return 0

    rc = ops.run_prune(["--via-ssh", FOREIGN], out=Out(),
                       ask=Ask(lambda p: "bbbb2222" in p), run=run)

    assert rc == 0
    assert len(calls) == 1
    argv = calls[0]
    assert "--key" in argv and "box3/bbbb2222" in argv
    assert "box3/aaaa1111" not in argv
    assert argv[-1] == "--yes"
    # Nothing was removed *here*: the owning host decides.
    assert exists(agb, sd, FOREIGN, "bbbb2222") == (True, True)


def test_via_ssh_with_nothing_confirmed_spawns_nothing(agb, ops, sd):
    write_session(agb, sd, FOREIGN, "bbbb2222")
    quiet(agb, sd, FOREIGN)
    calls = []
    out = Out()

    ops.run_prune(["--via-ssh", FOREIGN], out=out, ask=Ask(False),
                  run=lambda argv: calls.append(argv))

    assert calls == []
    assert "nothing confirmed" in out.text


def test_via_ssh_uses_the_configured_target_python_and_jump_host(agb, ops, sd,
                                                                  config_file):
    config_file("host_box3 = box3.example.com\n"
                "jump_host = box2.example\n"
                "remote_python = /usr/bin/python3\n"
                "agb_remote_path = /opt/agbridge/agb\n")
    write_session(agb, sd, FOREIGN, "bbbb2222")
    quiet(agb, sd, FOREIGN)
    calls = []

    ops.run_prune(["--via-ssh", FOREIGN], out=Out(), ask=Ask(True),
                  run=lambda argv: calls.append(argv) or 0)

    argv = calls[0]
    assert argv[:6] == ["ssh", "-J", "box2.example", "box3.example.com",
                        "/usr/bin/python3", "-S"]
    assert "/opt/agbridge/agb" in argv


@pytest.mark.parametrize("mine,host", [("box2", FOREIGN),   # jump == this host
                                       ("box9", "box2")])   # jump == the target
def test_via_ssh_never_hops_through_the_target_or_through_itself(
        agb, ops, sd, config_file, set_host, mine, host):
    """`install.sh` copies the Mac's `--jump-host` into the FARM's config, so
    `jump_host = box2` is now the default on box #2 itself -- and from there
    machine #3 is direct. Both siblings already refuse the pointless hop
    (`agb_mac.jump_for`, `pane_settings`); this one deletes files.

    The cost is not a wasted hop: `--via-ssh` does NOT remove anything locally,
    so `ssh -J box2 box2` failing (no ssh-to-self key, an unaccepted host key
    in a non-interactive run) means the entries a human confirmed one by one
    are silently not removed -- on the only route where `kill(pid, 0)` answers
    in the right pid namespace."""
    config_file("host_box2 = box2.example\n"
                "host_box3 = box3.example\n"
                "jump_host = box2.example\n")
    set_host(mine)
    write_session(agb, sd, host, "5555555555555555")
    quiet(agb, sd, host)
    calls = []

    ops.run_prune(["--via-ssh", host], out=Out(), ask=Ask(True),
                  run=lambda argv: calls.append(argv) or 0)

    assert calls and "-J" not in calls[0], calls
    assert calls[0][1] == "%s.example" % (host,)


def test_via_ssh_defaults_to_the_paths_it_is_running_from(agb, ops, sd,
                                                           repo_root):
    """Not a config lookup: box #2 and machine #3 see the same NFS `agb`, and
    `sys.executable` is the absolute interpreter constraint #14 already requires
    on every host that runs hooks."""
    write_session(agb, sd, FOREIGN, "bbbb2222")
    quiet(agb, sd, FOREIGN)
    calls = []

    ops.run_prune(["--via-ssh", FOREIGN], out=Out(), ask=Ask(True),
                  run=lambda argv: calls.append(argv) or 0)

    argv = calls[0]
    assert argv[1] == FOREIGN                     # no host_<name> configured
    assert argv[2] == sys.executable
    assert argv[5] == os.path.join(repo_root, "agb")


def test_via_ssh_names_a_confirmed_entry_it_cannot_carry(agb, ops, sd):
    """`--key` reaches past the via-ssh host's own candidate list, so the
    operator can confirm an entry this ssh will never mention. It used to be
    filtered out in silence: a yes to a removal, a command that exited 0, and
    nothing removed -- the tool lying about what it did, in the one place where
    it deletes files."""
    write_session(agb, sd, FOREIGN, "bbbb2222")
    write_session(agb, sd, "box4", "cccc3333")
    quiet(agb, sd, FOREIGN)
    quiet(agb, sd, "box4")
    out = Out()
    calls = []

    rc = ops.run_prune(["--via-ssh", FOREIGN, "--key", "box4/cccc3333",
                        "--key", "box3/bbbb2222"],
                       out=out, ask=Ask(True),
                       run=lambda argv: calls.append(argv) or 0)

    assert rc == 0
    assert "box4/cccc3333" in out.text
    assert "NOT removed" in out.text
    assert "box3/bbbb2222" in calls[0]
    assert "box4/cccc3333" not in calls[0]


def test_via_ssh_says_so_when_the_only_confirmation_was_for_another_host(
        agb, ops, sd):
    """The shape the reviewer reproduced: everything confirmed belongs to
    somebody else, so `nothing confirmed for box3` was the *whole* report."""
    write_session(agb, sd, "box4", "cccc3333")
    quiet(agb, sd, "box4")
    out = Out()
    calls = []

    ops.run_prune(["--via-ssh", FOREIGN, "--key", "box4/cccc3333"],
                  out=out, ask=Ask(True),
                  run=lambda argv: calls.append(argv) or 0)

    assert calls == []
    assert "NOT removed" in out.text and "box4/cccc3333" in out.text
    assert "nothing confirmed" in out.text


def test_via_ssh_only_offers_that_hosts_entries(agb, ops, sd):
    write_session(agb, sd, FOREIGN, "bbbb2222")
    write_session(agb, sd, "box4", "cccc3333")
    quiet(agb, sd, FOREIGN)
    quiet(agb, sd, "box4")
    ask = Ask(True)

    ops.run_prune(["--via-ssh", FOREIGN], out=Out(), ask=ask,
                  run=lambda argv: 0)

    assert len(ask.prompts) == 1
    assert "bbbb2222" in ask.prompts[0]


# ---------------------------------------------------------------------------
# argument parsing
# ---------------------------------------------------------------------------

def test_both_option_forms_parse(ops, sd):
    opts = ops.parse_prune_args(["--statedir=" + sd, "--quiet-after", "60",
                                 "--key=box3/aaaa1111", "--dry-run"])
    assert opts["statedir"] == sd
    assert opts["quiet_after"] == 60.0
    assert opts["keys"] == [("box3", "aaaa1111")]
    assert opts["dry_run"] is True


@pytest.mark.parametrize("argv", [
    ["--key"],
    ["--key", "aaaa1111"],              # no host
    ["--key", "box3/zzzz"],             # not a hex key
    ["--key", "../etc/aaaa1111"],       # host is a path component
    ["--via-ssh", "a/b"],
    ["--quiet-after", "-1"],
    ["--quiet-after", "soon"],
    ["--nonsense"],
    ["extra"],
])
def test_bad_arguments_raise_a_described_error(ops, agb, argv):
    with pytest.raises(agb.AgbError) as excinfo:
        ops.parse_prune_args(argv)
    assert "prune:" in str(excinfo.value)


def test_a_key_spec_round_trips(ops):
    assert ops.parse_key_spec("box3/aaaa1111") == ("box3", "aaaa1111")


# ---------------------------------------------------------------------------
# end to end, through the real dispatch
# ---------------------------------------------------------------------------

def test_prune_runs_end_to_end_and_a_typed_yes_removes_the_entry(agb, sd,
                                                                  run_agb):
    key = write_session(agb, sd, FOREIGN, "bbbb2222")
    quiet(agb, sd, FOREIGN)

    rc, out, err = run_agb(["prune", "--statedir", sd], stdin=b"y\n")

    assert rc == 0, err
    assert b"box3/bbbb2222" in out
    assert exists(agb, sd, FOREIGN, key) == (False, False)


def test_a_closed_stdin_is_a_no_and_never_a_yes(agb, sd, run_agb):
    """A `prune` whose stdin is a closed pipe must not remove anything: EOF is
    the absence of an answer, and the absence of an answer is not consent."""
    key = write_session(agb, sd, FOREIGN, "bbbb2222")
    quiet(agb, sd, FOREIGN)

    rc, out, err = run_agb(["prune", "--statedir", sd], stdin=b"")

    assert rc == 0, err
    assert exists(agb, sd, FOREIGN, key) == (True, True)


@pytest.mark.parametrize("answer", [b"n\n", b"no\n", b"\n", b"later\n",
                                    b"  \n"])
def test_a_typed_refusal_keeps_the_entry(agb, sd, run_agb, answer):
    """⚠️ No test ever typed a refusal before this one, so replacing
    `confirm_stdin`'s `in ("y", "yes")` with `bool(line.strip())` -- every
    non-empty answer is consent -- passed all of it. Every other prune test
    injects a fake `ask`; the only two that reach the real prompt type `y` and
    EOF. An operator answering `n` at the per-entry prompt of this tool's ONLY
    unlink-without-proof path would have the entry removed, and CI would stay
    green."""
    key = write_session(agb, sd, FOREIGN, "bbbb2222")
    quiet(agb, sd, FOREIGN)

    rc, out, err = run_agb(["prune", "--statedir", sd], stdin=answer)

    assert rc == 0, err
    assert b"kept" in out
    assert exists(agb, sd, FOREIGN, key) == (True, True)


def test_confirm_stdin_answers_only_to_yes(ops, monkeypatch):
    """The predicate on its own, so the accepted set is pinned rather than
    inferred from one end-to-end run."""
    class FakeStdin(object):
        def __init__(self, line):
            self.line = line

        def readline(self):
            return self.line

    for yes in ("y\n", "Y\n", "yes\n", "YES\n", " y \n"):
        monkeypatch.setattr(sys, "stdin", FakeStdin(yes))
        assert ops.confirm_stdin("? ") is True, yes
    for no in ("n\n", "N\n", "no\n", "\n", "", "yep\n", "sure\n", "1\n",
               "yesterday\n"):
        monkeypatch.setattr(sys, "stdin", FakeStdin(no))
        assert ops.confirm_stdin("? ") is False, no


def test_a_bad_option_exits_one_with_a_message(run_agb):
    rc, out, err = run_agb(["prune", "--force"])
    assert rc == 1
    assert out == b""
    assert b"no --force" in err


def test_prune_on_a_statedir_it_cannot_write_says_so_and_exits_one(sd,
                                                                    run_agb):
    """Before offering to delete anything it proves it could write: a probe that
    renames a file, not an `os.access` answer.

    ⚠️ It stops on a WARN too. `doctor` may now degrade the same probe to a
    warning -- a statedir that does not exist yet is not a broken install, and
    `doctor` is a report -- but `prune` deletes, so it needs the probe to have
    actually succeeded, both for the writability proof and for the
    server-stamped `now` every age it prints is measured against.
    """
    rc, out, err = run_agb(["prune", "--statedir", sd + "-nonexistent"])
    assert rc == 1
    assert b"atomicity" in out
    assert b"will not run without a write probe that succeeded" in out


def test_prune_names_the_real_errno_when_gen_exists_and_is_unusable(agb, sd,
                                                                     run_agb):
    """`prune` inherits `doctor`'s probe, so it inherited the degrade too: it
    refused to run and told the operator "gen does not exist yet" about a `gen`
    that plainly does exist. Refusing is right; the sentence was false."""
    gen = agb.gen_dir(sd)
    shutil.rmtree(gen)
    with open(gen, "w") as handle:
        handle.write("not a directory\n")

    rc, out, err = run_agb(["prune", "--statedir", sd])

    assert rc == 1
    assert b"does not exist yet" not in out
    assert b"cannot write in" in out


# ---------------------------------------------------------------------------
# structural: one door, one destructive path, no borrowed authority
# ---------------------------------------------------------------------------

def test_prune_is_reached_through_the_one_shared_operator_door(agb_tree, agb):
    """Tasks 6b-9a share `cmd_ops` rather than adding a `cmd_*` stub each: the
    parse budget had ~600 bytes left, and `agb` is re-parsed by every hook."""
    funcs = conftest.functions(agb_tree)
    assert "prune" in agb.OPS_COMMANDS
    assert "cmd_prune" not in funcs
    assert (None, "cmd_ops") in conftest.calls(funcs["main"])


def test_no_hook_or_feed_path_can_reach_the_prune_machinery(all_trees):
    """The destructive command must be unreachable from everything that runs
    unattended. A `prune_remove` reachable from a hook would be a deletion
    nobody consented to."""
    funcs = conftest.functions(*all_trees)
    # Each root carries a canary: something that MUST be reachable from it.
    # `reachable_from` answers `{root}` for a name it does not know, so a root
    # that was renamed would leave this asserting `"prune_remove" not in
    # {"cmd_feed"}` -- trivially true, and the guard would keep passing while
    # covering nothing at all.
    canaries = {
        "cmd_hook": "hook_apply",
        "cmd_feed": "feed_poll",
        "sweep_host": "sweep_entry",
        "feed_poll": "_feed_sweep",
    }
    for root, canary in sorted(canaries.items()):
        reachable = conftest.reachable_from(funcs, root)
        assert canary in reachable, (root, canary)      # the walk really ran
        assert "prune_remove" not in reachable, root
        assert "run_prune" not in reachable, root


def test_prune_never_lists_a_directory_and_never_rebuilds_a_marker(all_trees):
    """The second named defect, structurally: `rebuild_marker` and
    `list_session_keys` both read `readdir(sessions/<host>/)`, which constraint
    #5 makes authoritative only for the local host -- and every entry `prune`
    handles is, by construction, on a host that is not this one."""
    funcs = conftest.functions(*all_trees)
    reachable = conftest.reachable_from(funcs, "run_prune")
    assert "prune_remove" in reachable                 # the walk really ran
    for forbidden in ("rebuild_marker", "list_session_keys", "reap_entry",
                      "sweep_entry", "sweep_host", "_require_own_host"):
        assert forbidden not in reachable, forbidden


def test_prune_asks_only_the_refusing_liveness_question(all_trees, ops_tree):
    """`proof_of_life` refuses a removal; `proof_of_death` would authorise one.
    For a foreign entry the pid is in another host's namespace, so the positive
    question can only ever be answered by coincidence -- and a coincidence must
    never be allowed to delete anything."""
    funcs = conftest.functions(*all_trees)
    reachable = conftest.reachable_from(funcs, "run_prune")
    assert "proof_of_life" in reachable
    assert "proof_of_death" not in reachable
    assert "liveness" in conftest.reachable_from(funcs, "proof_of_life")


def test_the_candidate_list_has_exactly_one_derivation(ops_tree):
    """`prune` consumes Task 6a's list rather than re-deriving "which entries
    look old". A second derivation is a second place for an age heuristic to
    drift into a claim."""
    funcs = conftest.functions(ops_tree)
    callers = set(name for name, node in funcs.items()
                  if "unadjudicable_entries" in
                  [attr for _base, attr in conftest.calls(node)])
    assert callers == set(["probe_unadjudicable", "prune_candidates"])


def test_the_two_entry_views_are_built_by_one_function(ops_tree):
    """`unadjudicable_entries` and `entry_for` must produce the same shape, or
    the prompt would show different fields depending on how the entry was
    found."""
    funcs = conftest.functions(ops_tree)
    for name in ("unadjudicable_entries", "entry_for"):
        assert "describe_entry" in [attr for _base, attr
                                    in conftest.calls(funcs[name])], name


def test_prune_removes_a_record_before_its_state(ops_tree):
    """`reap_entry`'s order, for its reason: a failure between the two leaves a
    `.state` with no record, which the feed degrades to a stale label, whereas
    the other order leaves a `.json` that nothing ever collects -- and on a
    foreign host nothing here may ever sweep it."""
    node = conftest.functions(ops_tree)["prune_remove"]
    order = [attr for _base, attr in conftest.calls(node)
             if attr in ("record_path", "state_path")]
    assert order == ["record_path", "state_path"]


def test_the_prune_prose_never_promotes_the_heuristic_to_a_claim(ops_source):
    """`tests/test_doctor.py` pins the banned words on every string literal in
    the file, which now covers `prune`'s prompts too. This is the positive half:
    the caveat is actually attached to the destructive rendering."""
    assert "PRUNE_BLOCKED_WARNING" in ops_source
    tree = ast.parse(ops_source)
    funcs = conftest.functions(tree)
    shown = [attr for _base, attr in conftest.calls(funcs["format_prune_entry"])]
    assert "age_text" in shown
    body = ast.dump(funcs["format_prune_entry"])
    assert "UNADJUDICABLE_CAVEAT" in body


def test_the_operator_file_still_carries_the_bulk(ops_source, agb_source):
    """Task 6b added a command and `agb` grew only a dispatch line."""
    assert len(ops_source) > 40000
    assert len(agb_source) < conftest.AGB_PARSE_BUDGET


def test_prune_never_imports_json_or_argparse(all_trees, ops_tree):
    """Constraints #2 and #3 do not stop applying because a command is rare."""
    assert "argparse" not in conftest.all_imports(ops_tree)
    assert "json" not in conftest.toplevel_imports(ops_tree)
