"""Task 7 -- `agb pane`: status-only rows, attach on demand.

This is the command Task 4b wrote into the agterm row, so the tests come in two
halves that have to meet in the middle.

**The contract with the row.** `agb_mac.pane_argv` builds the command line and
`agb_ops.parse_pane_args` reads it. They are one contract across two files, so
the round-trip is asserted directly rather than by two lists that happen to
agree today.

**The three ways this command can lie.** It runs on the Mac, which cannot read
the shared statedir at all (constraint #10) -- so anything it learns must arrive
on its own command line, and a statedir helper reachable from `run_pane` would
pass every test on this box and fail on the only machine that runs it. It must
not `exec`, because `exec ssh` makes `C-b d` end the row's terminal instead of
returning to a prompt. And an agent with no tmux target must be *told* so, not
handed an ssh that lands in a fresh login shell and looks like it worked.

The tmux command shape is not a guess: `select-pane -t %N` was measured on tmux
3.5a on this box to leave the session's active *window* alone, so an agent in a
second window would be attached-to and never shown. `select-window -t %N`
accepts a pane id and moves the window, and the pair lands on the right pane.
"""

import ast
import os
import sys

import pytest

import conftest


HOST = "box3"               # "machine #3": reachable only through box #2
JUMP = "box2"

KEY = "a3f9c1e0"


class Out(object):
    """A collecting `out`, so assertions are about text rather than capsys."""

    def __init__(self):
        self.text = ""

    def write(self, data):
        self.text += data

    def flush(self):
        pass


class Ask(object):
    """A scripted `ask`: one answer per prompt, then None (EOF).

    None is what `pane_wait` returns at EOF, so a script that runs out ends the
    loop exactly the way a closed stdin does.
    """

    def __init__(self, *answers):
        self.answers = list(answers)
        self.prompts = []

    def __call__(self, prompt):
        self.prompts.append(prompt)
        if not self.answers:
            return None
        return self.answers.pop(0)


class Run(object):
    """A recording `run`, standing in for `subprocess.call`."""

    def __init__(self, *codes):
        self.codes = list(codes)
        self.calls = []

    def __call__(self, argv):
        self.calls.append(list(argv))
        if not self.codes:
            return 0
        return self.codes.pop(0)


def args(key=KEY, host=HOST, tmux="build", pane="%24", jump=None):
    """The row command's arguments, in the order `agb_mac.pane_argv` emits."""
    argv = [key, "--host", host]
    if tmux:
        argv += ["--tmux", tmux]
    if pane:
        argv += ["--pane", pane]
    if jump:
        argv += ["--jump", jump]
    return argv


# ---------------------------------------------------------------------------
# the constructed argv -- pure, so the tests are list comparisons
# ---------------------------------------------------------------------------

def test_the_ssh_argv_is_exact(ops):
    assert ops.pane_ssh_argv("box3.example", "build", "%24") == [
        "ssh", "-t", "box3.example",
        "tmux select-window -t %24 ; tmux select-pane -t %24 ; "
        "exec tmux attach-session -t build",
    ]


def test_the_ssh_argv_carries_the_jump_host(ops):
    """Machine #3 is routable only from box #2, so `-J` is how it is reached at
    all -- and it must come before the target, as ssh's own option."""
    argv = ops.pane_ssh_argv("box3", "build", "%24", jump=JUMP)
    assert argv[:5] == ["ssh", "-t", "-J", JUMP, "box3"]
    assert len(argv) == 6


def test_without_a_jump_host_there_is_no_dash_j(ops):
    argv = ops.pane_ssh_argv("box3", "build", "%24")
    assert "-J" not in argv


def test_the_pane_produces_the_select_pane_step(ops):
    """The plan's checkbox: two agents sharing a tmux session are only
    distinguishable by pane, so `--pane` has to reach the attach."""
    command = ops.pane_remote_command("build", "%24")
    assert "select-pane -t %24" in command


def test_the_window_is_selected_before_the_pane_and_the_attach_is_last(ops):
    """Measured on tmux 3.5a: `select-pane` alone leaves the session's active
    *window* where it was, so an agent in a second window would be attached-to
    and never shown. And the selects must precede the attach -- after
    `attach-session` the client is attached and the command has not returned,
    so nothing after it runs until the human detaches."""
    command = ops.pane_remote_command("build", "%24")
    order = [command.index(step) for step in
             ("select-window -t %24", "select-pane -t %24",
              "attach-session -t build")]
    assert order == sorted(order)


def test_a_failed_select_still_falls_through_to_the_attach(ops):
    """`;` rather than `&&`: a pane that has since been closed is a reason to
    land in the session anyway, not a reason to refuse to attach. (`tmux
    select-window -t %999` exits 1 with `can't find pane` -- verified on this
    box.)"""
    command = ops.pane_remote_command("build", "%24")
    assert "&&" not in command
    assert command.count(";") == 2


def test_without_a_pane_only_the_session_is_attached(ops):
    """A tmux agent whose pane id was never recorded still has a session to
    join; the row simply cannot say which pane."""
    command = ops.pane_remote_command("build")
    assert command == "exec tmux attach-session -t build"
    assert "select-pane" not in command


def test_without_a_session_the_pane_id_is_the_attach_target(ops):
    """MEASURED on tmux 3.5a on this box, not assumed: `attach-session -t %2`
    attached a client to the session that owns `%2` (`list-clients` reported
    it), while `-t %99` failed with `can't find pane: %99` -- so the pane id
    really does resolve as a session target and does not silently land
    elsewhere. That is what turns a record whose `tmux` is null (any hook whose
    environment could not run tmux) from a permanently status-only row back
    into an attachable one."""
    command = ops.pane_remote_command(None, "%24")
    assert command == ("tmux select-window -t %24 ; tmux select-pane -t %24 ; "
                       "exec tmux attach-session -t %24")


def test_the_session_name_is_preferred_over_the_pane_id_when_there_is_one(ops):
    """It survives the pane's death; a pane id does not."""
    assert ops.pane_remote_command("build", "%24").endswith(
        "exec tmux attach-session -t build")


def test_no_session_and_no_pane_is_refused_rather_than_attached_blindly(ops,
                                                                        agb):
    with pytest.raises(agb.AgbError):
        ops.pane_remote_command(None, None)


def test_a_session_name_with_a_space_is_quoted_for_the_remote_shell(ops):
    """`ssh host cmd` re-splits the command in a shell, and a tmux session is
    named by a human. Refusing to attach to `rq buf` would be a bug, not a
    safeguard -- so these two words are quoted rather than whitelisted."""
    command = ops.pane_remote_command("rq buf", "%24")
    assert command.endswith("exec tmux attach-session -t 'rq buf'")


def test_a_session_name_that_would_re_split_cannot_add_a_command(ops):
    command = ops.pane_remote_command("x ; rm -rf /", "%24")
    assert command.endswith("exec tmux attach-session -t 'x ; rm -rf /'")
    assert "rm -rf" not in command.split("'")[0]


@pytest.mark.parametrize("bad", ["box3; rm -rf /", "box 3", "$(id)", ""])
def test_a_target_a_shell_would_re_split_is_refused(ops, agb, bad):
    with pytest.raises(agb.AgbError):
        ops.pane_ssh_argv(bad, "build", "%24")


@pytest.mark.parametrize("word", ["-oProxyCommand=curl evil", "-oProxyCommand"])
def test_a_target_that_ssh_would_read_as_an_option_is_refused(ops, agb, word):
    """Every character of `-oProxyCommand` is in the whitelist, and the word
    reaches ssh's own argv rather than a shell -- so the whitelist is not the
    check that catches it. This value can arrive from a config `host_<name>`."""
    with pytest.raises(agb.AgbError):
        ops.pane_ssh_argv(word, "build", "%24")
    with pytest.raises(agb.AgbError):
        ops.pane_ssh_argv("box3", "build", "%24", jump=word)


def test_the_printed_attach_line_can_be_pasted(ops):
    """The remote command is **one** argv word. Printing it unquoted would show
    a line that does something else when copied -- a display that lies, in a
    tool built to stop displays lying."""
    argv = ops.pane_ssh_argv("box3", "rq buf", "%24")
    shown = ops.pane_display(argv)
    import shlex

    assert shlex.split(shown) == argv


# ---------------------------------------------------------------------------
# the contract with the row command Task 4b writes
# ---------------------------------------------------------------------------

def test_the_row_command_this_parser_reads_is_the_one_the_bridge_writes(mac,
                                                                        ops):
    """The round trip, across the file boundary: `agb_mac` builds the row's
    command line and `agb_ops` reads it back. Two lists that agree today are not
    a contract; this is."""
    session = {"key": KEY, "host": HOST, "tmux": "build", "pane": "%24",
               "label": "build", "cwd": "/shared/work/task"}
    argv = mac.pane_argv(session, agb_path="/opt/agb/agb", python="/py",
                         jump=JUMP)
    assert argv[4] == "pane"
    opts = ops.parse_pane_args(argv[5:])
    assert opts == {"key": KEY, "host": HOST, "tmux": "build", "pane": "%24",
                    "jump": JUMP, "cwd": "/shared/work/task"}


def test_a_non_tmux_row_command_round_trips_too(mac, ops):
    """The tier-2/3 anchor writes `tmux` and `pane` as null, so the row command
    omits both -- and the parser must accept that rather than requiring them."""
    session = {"key": KEY, "host": HOST, "tmux": None, "pane": None}
    argv = mac.pane_argv(session, agb_path="/a/agb", python="/py")
    opts = ops.parse_pane_args(argv[5:])
    assert (opts["tmux"], opts["pane"], opts["jump"]) == (None, None, None)


@pytest.mark.parametrize("tmux,pane", [
    ("build", "%24"),          # both: the ordinary tmux agent
    (None, None),               # neither: no tmux at all
    (None, "%24"),              # a pane whose session could not be resolved
    ("build", None),           # a session with no pane id in the record
])
def test_every_tmux_pane_combination_a_record_can_hold_round_trips(mac, ops,
                                                                   tmux, pane):
    """⚠️ The two middle rows were the hole. `pane_argv` emits `--tmux` and
    `--pane` from **independent** `if session.get(...)` tests, so all four
    combinations are representable -- but the old round-trip test covered only
    both-set and both-null, and both sides had been written from the same wrong
    assumption that those were the only two. The (None, "%24") record is real:
    `resolve_tmux_session()` returns None whenever tmux cannot answer, while
    `build_record` re-takes `pane` from the live anchor. Its row command exited
    1."""
    session = {"key": KEY, "host": HOST, "tmux": tmux, "pane": pane}
    argv = mac.pane_argv(session, agb_path="/a/agb", python="/py")
    opts = ops.parse_pane_args(argv[5:])
    assert (opts["key"], opts["host"]) == (KEY, HOST)
    assert (opts["tmux"], opts["pane"]) == (tmux, pane)


# ---------------------------------------------------------------------------
# argument parsing
# ---------------------------------------------------------------------------

def test_the_inline_form_parses_too(ops):
    opts = ops.parse_pane_args([KEY, "--host=box3", "--tmux=build",
                                "--pane=%24"])
    assert (opts["host"], opts["tmux"], opts["pane"]) == ("box3", "build",
                                                          "%24")


def test_a_key_is_required(ops, agb):
    with pytest.raises(agb.AgbError):
        ops.parse_pane_args(["--host", HOST])


def test_a_host_is_required(ops, agb):
    """The Mac cannot read the statedir, so there is nowhere else to learn it
    from -- which is why the row command carries it."""
    with pytest.raises(agb.AgbError):
        ops.parse_pane_args([KEY])


@pytest.mark.parametrize("bad", ["zzzz", "", "a3f9c1e0!", "../etc"])
def test_a_key_that_is_not_a_minted_key_is_refused(ops, agb, bad):
    with pytest.raises(agb.AgbError):
        ops.parse_pane_args([bad, "--host", HOST])


@pytest.mark.parametrize("bad", ["24", "%", "%abc", "pane24"])
def test_a_pane_that_is_not_a_tmux_pane_id_is_refused(ops, agb, bad):
    """The same `%<n>` form the anchor is minted under (`tmux_anchor_parts`);
    tmux always sets it."""
    with pytest.raises(agb.AgbError):
        ops.parse_pane_args([KEY, "--host", HOST, "--tmux", "s",
                             "--pane", bad])


def test_a_pane_without_a_session_is_accepted(ops, agb):
    """It used to be refused, and that refusal broke a record the tool DOES
    write: `resolve_tmux_session()` returns None whenever `$TMUX` is unset or
    `tmux display-message` cannot answer, while `pane` survives from
    `$TMUX_PANE` and from the previous record. `agb_mac.pane_argv` emits the
    two flags independently, so such a record produced a row whose command
    exited 1 -- a row that says nothing at all."""
    opts = ops.parse_pane_args([KEY, "--host", HOST, "--pane", "%24"])
    assert opts["pane"] == "%24"
    assert opts["tmux"] is None


@pytest.mark.parametrize("argv", [
    [KEY, "--host", HOST, "--nope"],
    [KEY, "--host", HOST, "extra"],
    [KEY, "--host"],
    [KEY, "--host", HOST, "--tmux", ""],
    [KEY, "--host", HOST, "--tmux", " build "],
])
def test_malformed_argument_lists_are_refused(ops, agb, argv):
    with pytest.raises(agb.AgbError):
        ops.parse_pane_args(argv)


# ---------------------------------------------------------------------------
# host -> ssh target, and the jump host
# ---------------------------------------------------------------------------

def test_the_host_is_mapped_to_an_ssh_target(ops):
    """A record's `host` is what the agent's own `uname` said -- a hostname, not
    an ssh alias. `host_<name>` is how it is reached."""
    opts = ops.parse_pane_args(args())
    target, _jump = ops.pane_settings(opts, {"host_box3": "user@box3.example:22"})
    assert target == "user@box3.example:22"


def test_an_unmapped_host_is_used_as_the_target(ops):
    opts = ops.parse_pane_args(args())
    target, _jump = ops.pane_settings(opts, {})
    assert target == HOST


def test_the_config_supplies_a_jump_host_when_the_row_did_not(ops):
    """`agb_mac.jump_for` withholds the hint for a session on the feed's own
    host, so a hand-typed invocation is the normal way the config's value is
    used."""
    opts = ops.parse_pane_args(args())
    _target, jump = ops.pane_settings(opts, {"jump_host": JUMP})
    assert jump == JUMP


def test_the_row_hint_wins_over_the_config(ops):
    opts = ops.parse_pane_args(args(jump="hinted"))
    _target, jump = ops.pane_settings(opts, {"jump_host": JUMP})
    assert jump == "hinted"


@pytest.mark.parametrize("config", [
    {"jump_host": HOST},
    {"jump_host": "user@box3.example", "host_box3": "user@box3.example"},
])
def test_a_jump_host_that_is_the_target_is_dropped(ops, config):
    """Reaching box #2 *through* box #2 is an extra hop for nothing."""
    opts = ops.parse_pane_args(args())
    _target, jump = ops.pane_settings(opts, config)
    assert jump is None


def test_the_config_file_is_read_when_no_config_is_injected(ops, config_file):
    """`pane` runs on the Mac and reads the Mac's own config -- the local file
    is emphatically not the shared statedir."""
    config_file("host_box3 = user@box3.example\njump_host = box2\n")
    out, run = Out(), Run()
    ops.run_pane(args(), out=out, ask=Ask(), run=run)
    assert "ssh target user@box3.example" in out.text
    assert "via jump host box2" in out.text


# ---------------------------------------------------------------------------
# the loop: attach, detach, come back
# ---------------------------------------------------------------------------

def test_the_identity_is_printed_before_anything_is_attached(ops):
    out, ask = Out(), Ask()
    assert ops.run_pane(args(), out=out, ask=ask, run=Run(), config={}) == 0
    head = out.text.split("[enter]")[0]
    for expected in (KEY, HOST, "build", "%24"):
        assert expected in head


def test_a_detach_returns_to_the_prompt_rather_than_exiting(ops):
    """The plan's checkbox, and the reason for `subprocess.call` in a loop:
    `C-b d` is the ordinary way to leave tmux, and with `exec` it would end the
    row's command and take the terminal with it."""
    out, ask, run = Out(), Ask("", ""), Run()
    assert ops.run_pane(args(), out=out, ask=ask, run=run, config={}) == 0
    assert len(run.calls) == 2
    assert len(ask.prompts) == 3          # attach, attach, then EOF
    assert "detached" in out.text


def test_the_same_argv_is_used_for_every_attach(ops):
    out, run = Out(), Run()
    ops.run_pane(args(jump=JUMP), out=out, ask=Ask("", ""), run=run, config={})
    assert run.calls[0] == run.calls[1]
    assert run.calls[0][:5] == ["ssh", "-t", "-J", JUMP, HOST]


def test_a_failed_ssh_is_reported_and_prompted_again(ops):
    """The farm being briefly unreachable is not a reason to close a row, and
    the human is standing right here."""
    out, run = Out(), Run(255, 0)
    assert ops.run_pane(args(), out=out, ask=Ask("", ""), run=run,
                        config={}) == 0
    assert "ssh exited 255" in out.text
    assert len(run.calls) == 2


@pytest.mark.parametrize("answer", ["q", "quit", "exit", "Q"])
def test_a_quit_word_ends_the_command(ops, answer):
    out, run = Out(), Run()
    assert ops.run_pane(args(), out=out, ask=Ask(answer), run=run,
                        config={}) == 0
    assert run.calls == []


def test_eof_ends_the_command_without_attaching(ops):
    """A row command whose stdin is not a terminal must end rather than spin on
    a prompt nobody can answer."""
    out, run = Out(), Run()
    assert ops.run_pane(args(), out=out, ask=Ask(), run=run, config={}) == 0
    assert run.calls == []


# ---------------------------------------------------------------------------
# `pane_wait` itself: the translation from a closed stdin into "stop"
# ---------------------------------------------------------------------------
#
# Every test above injects `Ask`, which *asserts* that None means EOF. Nothing
# proved the real default `ask` ever produces it. Deleting `pane_wait`'s
# `if not line: return None` therefore passed the whole suite while making
# `agb pane` spin at 30% CPU forever against a closed stdin -- observed, and
# it hung the run rather than failing it because no `communicate()` had a
# timeout. Both halves of that are fixed; this is the named half.

def _stdin(monkeypatch, text):
    import io
    monkeypatch.setattr(sys, "stdin", io.StringIO(text))


def test_pane_wait_returns_none_at_eof(ops, monkeypatch, capsys):
    """The contract `Ask()` stands in for, against the real function."""
    _stdin(monkeypatch, "")
    assert ops.pane_wait("go? ") is None
    assert "go? " in capsys.readouterr().out


def test_pane_wait_returns_the_typed_line_stripped(ops, monkeypatch):
    _stdin(monkeypatch, "  yes  \n")
    assert ops.pane_wait("go? ") == "yes"


def test_pane_wait_distinguishes_an_empty_line_from_eof(ops, monkeypatch):
    """A bare Enter is "attach"; EOF is "stop". Collapsing the two would make a
    piped stdin attach, or a human's Enter quit."""
    _stdin(monkeypatch, "\n")
    assert ops.pane_wait("go? ") == ""
    _stdin(monkeypatch, "")
    assert ops.pane_wait("go? ") is None


def test_a_closed_stdin_ends_agb_pane_through_the_real_prompt(run_agb):
    """End to end, through the installed argv: no injected `ask` anywhere, so
    this is the one test in which `pane_wait` reads a genuinely closed stdin.
    It must exit, and `run_agb`'s bounded `communicate()` is what turns a
    regression here into a failure instead of a hung suite."""
    rc, out, err = run_agb(["pane"] + args(), stdin=b"")
    assert rc == 0, err
    assert b"attach" in out                       # it really did reach the prompt
    assert b"nothing here changes any agent's state" in out


# ---------------------------------------------------------------------------
# the degraded path: no tmux target
# ---------------------------------------------------------------------------

def test_an_agent_with_no_tmux_target_is_told_so_and_nothing_is_attached(ops):
    """Decided here rather than discovered at attach time: the tier-2/3 anchors
    record `tmux`/`pane` as null precisely because there is no terminal to
    join."""
    out, run, ask = Out(), Run(), Ask("", "")
    rc = ops.run_pane([KEY, "--host", HOST], out=out, ask=ask, run=run,
                      config={})
    assert rc == 0
    assert run.calls == []
    assert ask.prompts == []
    assert "no attach target" in out.text


def test_the_degraded_path_still_prints_the_identity(ops):
    """A status-only row must still answer "which agent is this, and where does
    it live" -- that is the whole difference between a dashboard and a
    dashboard that lies."""
    out = Out()
    ops.run_pane([KEY, "--host", HOST], out=out, ask=Ask(), run=Run(),
                 config={})
    assert KEY in out.text and HOST in out.text


# ---------------------------------------------------------------------------
# end to end, through the real dispatch and a real ssh stub
# ---------------------------------------------------------------------------

def test_pane_runs_end_to_end_through_the_shared_door(run_agb, stub_bin,
                                                      config_file):
    """The whole path: `agb pane` -> `cmd_ops` -> `agb_ops.run_ops`, with a
    recording `ssh` on `$PATH` and a real stdin. Two blank lines attach twice
    and EOF ends it -- the detach loop, observed from outside the process."""
    stub_bin.install("ssh")
    config_file("host_box3 = user@box3.example\n")
    rc, out, err = run_agb(["pane", KEY, "--host", HOST, "--tmux", "build",
                            "--pane", "%24"], stdin=b"\n\n")
    assert rc == 0, err
    calls = stub_bin.calls("ssh")
    assert len(calls) == 2
    assert calls[0] == [
        "-t", "user@box3.example",
        "tmux select-window -t %24 ; tmux select-pane -t %24 ; "
        "exec tmux attach-session -t build",
    ]
    assert b"agb pane" in out


def test_pane_needs_no_statedir_at_all(run_agb, stub_bin, monkeypatch):
    """Constraint #10 from the outside: no `$AGB_STATEDIR`, no config, no NFS
    path in existence -- and the command still works, because everything it
    knows came in on its own command line."""
    stub_bin.install("ssh")
    rc, out, err = run_agb(["pane", KEY, "--host", HOST, "--tmux", "s"],
                           stdin=b"")
    assert rc == 0, err
    assert stub_bin.calls("ssh") == []
    assert b"attach: ssh -t box3" in out


def test_a_malformed_invocation_exits_one_and_says_why(run_agb, stub_bin):
    stub_bin.install("ssh")
    rc, out, err = run_agb(["pane", KEY, "--host", HOST, "--pane", "24"])
    assert rc == 1
    assert b"--pane takes a tmux pane id" in err
    assert stub_bin.calls("ssh") == []


def test_a_pane_with_no_tmux_session_attaches_on_the_pane_id_alone(run_agb,
                                                                   stub_bin):
    """The whole point of accepting the pair -- and the pane id is enough to
    act on, not merely to print. Verified against tmux 3.5a on this box:
    `attach-session -t %24` resolves the session that owns the pane. Before
    this, a record whose `tmux` was null (every hook whose `$PATH` had no tmux)
    produced a permanently status-only row for an agent that WAS in tmux."""
    stub_bin.install("ssh")
    rc, out, err = run_agb(["pane", KEY, "--host", HOST, "--pane", "%24"],
                           stdin=b"")
    assert rc == 0, err
    assert b"%24" in out
    assert b"attach:" in out
    assert b"attach-session -t %24" in out


def test_a_record_with_neither_a_session_nor_a_pane_is_status_only(run_agb,
                                                                   stub_bin):
    """The non-tmux identity tiers -- plain ssh on machine #3, and the
    session-leader fallback -- record both as null on purpose. Such a row says
    so rather than opening an ssh that lands in a fresh login shell."""
    stub_bin.install("ssh")
    rc, out, err = run_agb(["pane", KEY, "--host", HOST], stdin=b"")
    assert rc == 0, err
    assert b"no attach target" in out
    assert b"attach:" not in out
    assert stub_bin.calls("ssh") == []


# ---------------------------------------------------------------------------
# structural guards
# ---------------------------------------------------------------------------

def test_pane_is_reached_through_the_one_shared_operator_door(agb_tree, agb):
    """Task 6b's consolidation, collected: `pane` cost `agb` nothing at all --
    no `cmd_pane`, no new dispatch line, no bytes on a path every hook
    re-parses."""
    funcs = conftest.functions(agb_tree)
    assert "pane" in agb.OPS_COMMANDS
    assert "cmd_pane" not in funcs
    assert (None, "cmd_ops") in conftest.calls(funcs["main"])


def pane_reachable(all_trees):
    """The call graph behind `agb pane`, across every file.

    It starts at `cmd_ops` in `agb` and crosses into `agb_ops` through
    `_load_ops().run_ops(name, argv)`; the `agb.<helper>` calls the operator
    side makes back into the shared primitives are followed too. The
    non-vacuity assertions are the point -- a walk that failed to cross the file
    boundary would satisfy every guard below while checking nothing.
    """
    funcs = conftest.functions(*all_trees)
    reachable = conftest.reachable_from(funcs, "run_pane")
    assert "pane_attach" in reachable
    assert "pane_ssh_argv" in reachable
    assert "read_config" in reachable          # ...and back into agb's helpers
    return funcs, reachable


def test_the_pane_graph_spans_the_files(all_trees):
    pane_reachable(all_trees)


def test_pane_never_reads_the_shared_statedir(all_trees):
    """Constraint #10: this command runs on the Mac, where the NFS path does not
    exist. A statedir helper reachable from `run_pane` would compile and pass
    every unit test on this box, then hang or lie on the only machine that runs
    it. The local *config* file is a different thing and is deliberately not on
    this list.
    """
    _funcs, reachable = pane_reachable(all_trees)
    forbidden = set([
        "statedir", "ensure_statedir", "ensure_session_dir", "state_path",
        "record_path", "session_dir", "marker_path", "sweep_marker_path",
        "bridge_beat_path", "read_state_entry", "read_marker_keys",
        "list_marker_hosts", "list_session_keys", "rebuild_marker",
        "breadcrumb", "unadjudicable_entries", "entry_for", "describe_entry",
    ])
    assert reachable & forbidden == set()


def test_pane_removes_nothing_and_writes_nothing(all_trees):
    """It is a viewer. The only side effect it may have is a child ssh."""
    funcs, reachable = pane_reachable(all_trees)
    for name in reachable:
        made = conftest.calls(funcs[name])
        for forbidden in ("unlink", "rename", "utime", "mkdir", "makedirs"):
            assert ("os", forbidden) not in made, (name, forbidden)
        assert "atomic_write" not in [attr for _base, attr in made], name
        assert "write_in_place" not in [attr for _base, attr in made], name


EXEC_NAMES = ("execv", "execve", "execvp", "execvpe", "execl", "execle",
              "execlp", "execlpe")


def test_nothing_in_the_tool_ever_execs(all_trees):
    """`docs/design.md` §3 said `exec ssh -t`, and Task 10 reconciles it.
    `exec` replaces this process, so detaching from tmux -- the ordinary `C-b d`
    -- would end the row's command and take its terminal with it. The rule is
    checked over all three files rather than over `pane` alone: there is no
    place in this tool where replacing the process is the right answer.

    Bare names are checked as well as attributes: `os.execvp(...)` was the only
    spelling this saw, so `from os import execvp` followed by `execvp(...)`
    walked straight past it.
    """
    for tree in all_trees:
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Attribute):
                assert func.attr not in EXEC_NAMES, ast.dump(func)
            elif isinstance(func, ast.Name):
                assert func.id not in EXEC_NAMES, ast.dump(func)


def test_the_attach_is_a_loop_around_subprocess_call(ops_tree):
    """The two halves of the plan's checkbox, structurally: it is
    `subprocess.call` (not `Popen`, not `os.system`, not `exec`), and it is
    inside a loop -- so the function returns to its prompt when the attach
    ends."""
    node = conftest.functions(ops_tree)["pane_attach"]
    assert [child for child in ast.walk(node)
            if isinstance(child, ast.While)]
    attributes = set()
    for child in ast.walk(node):
        if (isinstance(child, ast.Attribute)
                and isinstance(child.value, ast.Name)):
            attributes.add((child.value.id, child.attr))
    assert ("subprocess", "call") in attributes
    assert ("subprocess", "Popen") not in attributes


def test_the_ssh_word_rules_have_one_home(ops_tree):
    """`pane` and `prune --via-ssh` both build an ssh command line, and they
    check their words with the same predicate. A second copy is how the
    destructive one and the harmless one come to disagree about what is safe."""
    funcs = conftest.functions(ops_tree)
    users = set(name for name, node in funcs.items()
                if (None, "_ssh_word_ok") in conftest.calls(node))
    assert users == set(["pane_ssh_argv", "prune_ssh_argv"])
    callers = set(name for name, node in funcs.items()
                  if (None, "ssh_target_for") in conftest.calls(node))
    assert callers == set(["pane_settings", "prune_via_ssh"])


def test_pane_never_imports_json_or_argparse(ops_tree):
    """Constraints #2 and #3 do not stop applying because a command is rare."""
    assert "argparse" not in conftest.all_imports(ops_tree)
    assert "json" not in conftest.toplevel_imports(ops_tree)


def test_the_operator_file_still_carries_the_bulk(ops_source, agb_source):
    """Task 7 added a command and `agb` grew nothing at all."""
    assert len(ops_source) > 60000
    assert len(agb_source) < conftest.AGB_PARSE_BUDGET


# ---------------------------------------------------------------------------
# `[s] split` and `[d] drawer` -- agterm's split pane and its scratch drawer
# ---------------------------------------------------------------------------

class _Out(object):
    """A collecting `out`, so assertions are about text rather than capsys."""

    def __init__(self):
        self.text = ""

    def write(self, data):
        self.text += data

    def flush(self):
        pass

def test_the_shell_line_is_an_ssh_into_the_agents_own_directory(ops):
    assert ops.split_shell_line("box2", "/work/api") == \
        "ssh -t box2 'cd /work/api && exec $SHELL -l'"


def test_the_shell_line_carries_the_jump_host(ops):
    assert ops.split_shell_line("box3", "/work/api", jump="gate") == \
        "ssh -t -J gate box3 'cd /work/api && exec $SHELL -l'"


def test_a_row_with_no_recorded_cwd_still_gets_a_shell(ops):
    """Rows created before `--cwd` existed have none. Landing in the home
    directory is a smaller surprise than landing somewhere invented."""
    assert ops.split_shell_line("box2") == "ssh -t box2"


def test_a_cwd_with_a_space_survives_the_remote_shell(ops):
    """`session type` injects a shell LINE, so an unquoted path would become
    two arguments and `cd` would fail on the first half."""
    line = ops.split_shell_line("box2", "/work/my project")
    assert "'cd '\"'\"'/work/my project'\"'\"' && exec $SHELL -l'" in line \
        or "/work/my project" in line.replace("'\\''", "'")


def test_the_split_is_opened_before_anything_is_typed_into_it(ops):
    """`--pane right` is an error when the session has no split, and `--select`
    is documented as main-pane only -- so the order is load-bearing, not
    stylistic."""
    calls = []
    ops.open_split("ssh -t box2", run=lambda argv: calls.append(argv) or 0,
                   out=_Out())
    assert [c[1:3] for c in calls] == [["session", "split"], ["session", "type"]]


def test_the_split_is_turned_on_not_toggled(ops):
    """Asked for twice, `toggle` would CLOSE the pane the second time."""
    calls = []
    ops.open_split("ssh -t box2", run=lambda argv: calls.append(argv) or 0,
                   out=_Out())
    assert calls[0] == ["agtermctl", "session", "split", "on",
                        "--target", "active"]
    assert calls[1][:6] == ["agtermctl", "session", "type", "--target",
                            "active", "--pane"]
    assert calls[1][6] == "right"
    assert calls[1][7] == "ssh -t box2\n"      # a newline, or nothing runs


def test_a_failed_split_leaves_the_row_alone_and_says_so(ops):
    out = _Out()
    calls = []
    code = ops.open_split("ssh -t box2",
                          run=lambda argv: calls.append(argv) or 3, out=out)
    assert code == 3
    assert len(calls) == 1                     # never typed into a pane that is not there
    assert "was not opened" in out.text


def test_the_drawer_is_shown_before_anything_is_typed_into_it(ops):
    """Mirrors the split's ordering test. Whether `--pane scratch` really errors
    before the scratch exists is ASSUMED rather than observed -- the recorded
    help constrains only `--select`, and only for a split -- but the ordering
    costs nothing and the split's precedent says keep it."""
    calls = []
    ops.open_drawer("ssh -t box2", run=lambda argv: calls.append(argv) or 0,
                    out=_Out())
    assert [c[1:3] for c in calls] == [["session", "scratch"],
                                       ["session", "type"]]


def test_the_drawer_is_turned_on_not_toggled(ops):
    """Asked for twice, `toggle` would CLOSE the drawer the second time -- the
    same rule that already governs the split."""
    calls = []
    ops.open_drawer("ssh -t box2", run=lambda argv: calls.append(argv) or 0,
                    out=_Out())
    assert calls[0] == ["agtermctl", "session", "scratch", "on",
                        "--target", "active"]
    assert calls[1][:6] == ["agtermctl", "session", "type", "--target",
                            "active", "--pane"]
    assert calls[1][6] == "scratch"
    assert calls[1][7] == "ssh -t box2\n"      # a newline, or nothing runs


def test_a_failed_drawer_leaves_the_row_alone_and_says_so(ops):
    out = _Out()
    calls = []
    code = ops.open_drawer("ssh -t box2",
                           run=lambda argv: calls.append(argv) or 3, out=out)
    assert code == 3
    assert len(calls) == 1                     # never typed into a pane that is not there
    assert "was not opened" in out.text


def test_the_drawer_never_uses_the_command_flag(ops):
    """`scratch on --command <line>` is the nicer single call and is rejected:
    its help says it "respawns the scratch if one is already open", so a second
    press of `[d]` would destroy a shell in use. Typing into the existing shell
    nests an ssh instead, which `exit` undoes."""
    calls = []
    ops.open_drawer("ssh -t box2", run=lambda argv: calls.append(argv) or 0,
                    out=_Out())
    assert not any("--command" in argv for argv in calls)


def test_the_prompt_offers_both_panes(ops):
    assert "[s] split" in ops.PANE_PROMPT
    assert "[d] drawer" in ops.PANE_PROMPT


def test_the_three_key_word_sets_are_pairwise_disjoint(ops):
    """The dispatch is keyed on string membership and the branches are tested in
    order, so whichever matches first wins and the rest become unreachable --
    silently, with no error anywhere and every unit test still green.

    Not hypothetical: `shell`, `split` and `scratch` all start with `s`, and
    `scratch` is a plausible synonym for either pane. `PANE_QUIT_WORDS` is in
    here too because it is matched in the same loop, after both others.
    """
    sets = {"split": set(ops.PANE_SPLIT_WORDS),
            "drawer": set(ops.PANE_DRAWER_WORDS),
            "quit": set(ops.PANE_QUIT_WORDS)}
    for left in sets:
        for right in sets:
            if left < right:
                assert not sets[left] & sets[right], \
                    "%s and %s share %s" % (left, right,
                                            sorted(sets[left] & sets[right]))


def _dispatch(ops, monkeypatch, answer, have=True):
    """Drive `pane_attach`'s key loop and return the recorded agtermctl calls.

    Three things this has to get right, and each of them is a way the test goes
    vacuous rather than red:

    * `pane_attach`, not `run_pane` -- only the former takes `ctl`, so only the
      former can be given a recorder.
    * `_have` forced, never inherited. It scans $PATH, and `agtermctl` is absent
      on the farm and present on the Mac: left to the ambient value this test
      records nothing on one machine and opens a real pane on the other.
    * `split_line` supplied, or guard #1 swallows the branch before either of
      the others is reached.
    """
    monkeypatch.setattr(ops, "_have", lambda _program: have)
    ctl = Run()
    out = Out()
    ops.pane_attach(["ssh", "box2"], out, ask=Ask(answer, "q"),
                    run=Run(), split_line="ssh -t box2", ctl=ctl)
    return ctl.calls, out.text


def test_d_reaches_the_drawer_and_not_the_split(ops, monkeypatch):
    """The unit tests above prove `open_drawer`; this proves the key gets there.
    Without it, pointing the `[d]` branch at `open_split` passes everything."""
    calls, _text = _dispatch(ops, monkeypatch, "d")
    # Non-vacuity FIRST. "no call mentions split" is true of an empty list, and
    # an empty list is exactly what a wrong `_have` produces.
    assert calls, "no agtermctl call was recorded at all"
    assert calls[0][1:3] == ["session", "scratch"]
    assert not any("split" in word for argv in calls for word in argv)


def test_shell_still_opens_the_split(ops, monkeypatch):
    """The compatibility promise: the prompt's label moved from `shell` to
    `split`, but the WORD keeps its old meaning. Pinned so a later tidy-up
    cannot quietly reassign it to the drawer."""
    calls, _text = _dispatch(ops, monkeypatch, "shell")
    assert calls, "no agtermctl call was recorded at all"
    assert calls[0][1:3] == ["session", "split"]
    assert not any("scratch" in word for argv in calls for word in argv)


def test_the_drawer_says_so_when_agtermctl_is_missing(ops, monkeypatch):
    """The guard most likely to be forgotten in a copied branch. `_have` is
    forced False rather than trusted: on a Mac the real one returns True and
    this test would spawn agtermctl against a live agterm."""
    calls, text = _dispatch(ops, monkeypatch, "d", have=False)
    assert calls == []
    assert "agtermctl is not on PATH" in text
    # The message must not name the split when `[d]` was pressed.
    assert "the split" not in text
