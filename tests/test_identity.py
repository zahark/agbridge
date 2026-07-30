"""Task 2a -- session identity, agent pid resolution, key minting."""

import ast
import multiprocessing
import os
import sys

import pytest

import conftest


# ---------------------------------------------------------------------------
# $TMUX / $TMUX_PANE parsing
# ---------------------------------------------------------------------------

REAL_TMUX = "/tmp/tmux-148808/default,1244192,23"


def test_tmux_anchor_parses_the_server_pid_and_the_pane(agb, set_tmux):
    set_tmux(REAL_TMUX, "%24")
    assert agb.tmux_anchor_parts() == (1244192, "%24")


def test_tmux_anchor_needs_both_variables(agb, set_tmux):
    set_tmux(REAL_TMUX, None)
    assert agb.tmux_anchor_parts() is None
    set_tmux(None, "%24")
    assert agb.tmux_anchor_parts() is None
    set_tmux(None, None)
    assert agb.tmux_anchor_parts() is None


@pytest.mark.parametrize("tmux", [
    "",
    "/tmp/tmux-148808/default",
    "/tmp/tmux-148808/default,1244192",       # session field missing
    "/tmp/tmux-148808/default,notapid,23",
    "/tmp/tmux-148808/default,0,23",
    "/tmp/tmux-148808/default,-5,23",
])
def test_tmux_anchor_rejects_a_malformed_tmux(agb, set_tmux, tmux):
    set_tmux(tmux, "%24")
    assert agb.tmux_anchor_parts() is None


@pytest.mark.parametrize("pane", ["", "24", "%", "%abc", "pane24", "  "])
def test_tmux_anchor_rejects_a_malformed_pane(agb, set_tmux, pane):
    """tmux always sets `%<n>`; the leading `%` is what makes an idx name's
    kind unambiguous, so anything else counts as no pane at all."""
    set_tmux(REAL_TMUX, pane)
    assert agb.tmux_anchor_parts() is None


# ---------------------------------------------------------------------------
# the anchor, and its three tiers
# ---------------------------------------------------------------------------

def test_tmux_anchor_is_host_serverpid_pane(agb, set_tmux):
    set_tmux(REAL_TMUX, "%24")
    anchor = agb.resolve_anchor("box2", 48213, 9182736)
    assert (anchor.kind, anchor.spid, anchor.pane) == ("tmux", 1244192, "%24")
    assert anchor.name() == "box2-1244192-%24"


def test_non_tmux_anchor_is_the_agent_pid_and_starttime(agb, set_tmux):
    """Machine #3 over plain ssh: distinct per agent process, no attach target."""
    set_tmux(None, None)
    anchor = agb.resolve_anchor("machine3", 48213, 9182736)
    assert (anchor.kind, anchor.spid) == ("pid", 48213)
    assert anchor.name() == "machine3-48213-p9182736"
    assert anchor.pane is None


def test_anchor_falls_back_to_the_session_leader(agb, set_tmux):
    """No tmux and no identifiable agent: still a stable anchor, but nothing
    that can prove death -- `agb prune` is its terminal path."""
    set_tmux(None, None)
    anchor = agb.resolve_anchor("machine3", None, None)
    assert (anchor.kind, anchor.spid) == ("sid", os.getsid(0))
    assert anchor.pane is None


def test_anchor_defaults_to_own_host(agb, set_tmux, set_host):
    set_tmux(REAL_TMUX, "%24")
    set_host("box2")
    assert agb.resolve_anchor().host == "box2"


def test_idx_name_round_trips_through_a_dashed_hostname(agb):
    anchor = agb.Anchor("worker01", "tmux", 1244192, "%24",
                        pane="%24")
    name = anchor.name()
    assert name == "worker01-1244192-%24"
    assert agb.parse_idx_name(name) == (
        "worker01", 1244192, "%24")


@pytest.mark.parametrize("name", ["", "nodashes", "host-notanint-%24", "-1-%24"])
def test_parse_idx_name_rejects_junk(agb, name):
    assert agb.parse_idx_name(name) is None


def test_idx_kind_selects_the_liveness_predicate(agb):
    """Task 5 needs to know which pid an idx file's `spid` actually is."""
    assert agb.idx_kind("%24") == "tmux"
    assert agb.idx_kind("p9182736") == "pid"
    assert agb.idx_kind("s") == "sid"
    assert agb.idx_kind("") is None
    assert agb.idx_tag_starttime("p9182736") == 9182736
    assert agb.idx_tag_starttime("%24") is None
    assert agb.idx_tag_starttime("p-") is None


def test_idx_tag_never_contains_the_field_separator(agb):
    """The name is split from the right, so a `-` in the tag would be read as a
    field boundary and mis-attribute the entry."""
    assert agb._idx_safe("a/b-c d") == "a_b_c_d"
    anchor = agb.Anchor("box2", "tmux", 7, "%2-4", pane="%2-4")
    assert agb.parse_idx_name(anchor.name()) == ("box2", 7, "%2_4")


# ---------------------------------------------------------------------------
# agent pid resolution -- the PPid walk
# ---------------------------------------------------------------------------

def _chain(agent_comm="node", agent_cmdline=None):
    """hook python <- transient `sh -c` <- the agent."""
    return {
        100: {"comm": "python3", "ppid": 200, "starttime": 500},
        200: {"comm": "sh", "ppid": 300, "starttime": 400,
              "cmdline": ["sh", "-c", "python3 agb hook active"]},
        300: {"comm": agent_comm, "ppid": 1, "starttime": 9182736,
              "cmdline": agent_cmdline or [agent_comm]},
    }


def test_agent_pid_walks_past_the_transient_shell(agb, fake_proc):
    """`PPid` alone is the `sh -c` Claude spawns hooks through: it dies
    immediately, and a sweep believing it was the agent deletes everything."""
    fake_proc(_chain(), me=100)
    assert agb.resolve_agent() == (300, 9182736)


def test_agent_pid_matches_a_claude_comm(agb, fake_proc):
    fake_proc(_chain(agent_comm="claude"), me=100)
    assert agb.resolve_agent() == (300, 9182736)


def test_agent_pid_matches_the_cmdline_basename(agb, fake_proc):
    """A long comm is truncated to 15 chars by the kernel, so argv[0] is the
    second source rather than a nicety."""
    procs = _chain(agent_comm="some-wrapper-nam",
                   agent_cmdline=["/usr/local/bin/claude", "--foo"])
    fake_proc(procs, me=100)
    assert agb.resolve_agent() == (300, 9182736)


def test_agent_pid_ignores_a_matching_name_deeper_in_the_argv(agb, fake_proc):
    """Only argv[0] counts: `sh -c 'claude ...'` is a shell, not the agent."""
    procs = _chain(agent_comm="sh", agent_cmdline=["sh", "-c", "claude"])
    procs[300]["ppid"] = 1
    fake_proc(procs, me=100)
    assert agb.resolve_agent() == (None, None)


def test_agent_pid_is_none_when_nothing_in_the_chain_matches(agb, fake_proc):
    procs = _chain(agent_comm="bash")
    fake_proc(procs, me=100)
    assert agb.resolve_agent() == (None, None)


def test_agent_pid_walk_stops_at_init(agb, fake_proc):
    fake_proc({
        100: {"comm": "python3", "ppid": 1, "starttime": 5},
        1: {"comm": "systemd", "ppid": 0, "starttime": 1},
    }, me=100)
    assert agb.resolve_agent() == (None, None)


def test_agent_pid_walk_terminates_on_a_cycle(agb, fake_proc):
    """A bounded walk, because the hot path must never hang on a weird /proc."""
    fake_proc({
        100: {"comm": "python3", "ppid": 200, "starttime": 5},
        200: {"comm": "bash", "ppid": 100, "starttime": 6},
    }, me=100)
    assert agb.resolve_agent() == (None, None)


def test_agent_pid_survives_an_unreadable_proc(agb, fake_proc):
    fake_proc({100: {"comm": "python3", "ppid": 999999, "starttime": 5}}, me=100)
    assert agb.resolve_agent() == (None, None)


def test_agent_pid_env_override(agb, fake_proc, set_agent_pid):
    fake_proc({4242: {"comm": "claude", "ppid": 1, "starttime": 777}})
    set_agent_pid(4242)
    assert agb.resolve_agent() == (4242, 777)


@pytest.mark.parametrize("value", ["-", "0", "", "  ", "garbage"])
def test_agent_pid_override_can_express_unresolvable(agb, monkeypatch, value):
    """The flagship sweep regression test needs to force the fail-safe path."""
    monkeypatch.setenv("AGB_AGENT_PID", value)
    assert agb.resolve_agent() == (None, None)


def test_agent_pid_override_of_a_dead_pid_has_no_starttime(agb, fake_proc,
                                                           set_agent_pid):
    fake_proc({100: {"comm": "python3", "ppid": 1, "starttime": 5}}, me=100)
    set_agent_pid(999999)
    assert agb.resolve_agent() == (999999, None)


# ---------------------------------------------------------------------------
# starttime -- /proc/<pid>/stat field 22
# ---------------------------------------------------------------------------

def test_starttime_is_field_22(agb, fake_proc):
    fake_proc({7: {"comm": "claude", "ppid": 1, "starttime": 9182736}})
    assert agb.proc_starttime(7) == 9182736


def test_starttime_survives_a_comm_with_spaces_and_parens(agb, fake_proc):
    """field 2 is the comm and is neither escaped nor quoted, which is why the
    parser splits on the *last* `)` rather than on whitespace."""
    fake_proc({7: {"comm": "we (ird) proc", "ppid": 1, "starttime": 4242}})
    assert agb.proc_starttime(7) == 4242


def test_starttime_of_a_missing_pid_is_none(agb, fake_proc):
    fake_proc({7: {"comm": "claude", "ppid": 1, "starttime": 1}})
    assert agb.proc_starttime(999999) is None


def test_starttime_of_a_truncated_stat_is_none(agb, fake_proc, tmp_path):
    root = fake_proc({7: {"comm": "claude", "ppid": 1, "starttime": 1}})
    with open(str(root / "7" / "stat"), "w") as handle:
        handle.write("7 (claude) S 1 0 0\n")
    assert agb.proc_starttime(7) is None


def test_starttime_of_a_stat_without_a_paren_is_none(agb, fake_proc):
    root = fake_proc({7: {"comm": "claude", "ppid": 1, "starttime": 1}})
    with open(str(root / "7" / "stat"), "w") as handle:
        handle.write("garbage\n")
    assert agb.proc_starttime(7) is None


def test_proc_helpers_read_the_real_proc_by_default(agb):
    """The fake tree is a test seam, not the production path."""
    assert agb.PROC == "/proc"
    assert agb.proc_ppid("self") == os.getppid()
    assert agb.proc_starttime(os.getpid()) > 0


# ---------------------------------------------------------------------------
# the idx file: format, validation, minting
# ---------------------------------------------------------------------------

def _read(path):
    with open(str(path), "rb") as handle:
        return handle.read()


def test_idx_format_and_parse_round_trip(agb):
    rec = agb.parse_idx(agb.format_idx("a3f9c1e0", 48213, 9182736).encode())
    assert rec == {"key": "a3f9c1e0", "pid": 48213, "starttime": 9182736}


def test_idx_format_records_an_unknown_agent_as_a_dash(agb):
    """Fail safe: no pid stored means every sweep must skip the entry."""
    text = agb.format_idx("a3f9c1e0", None, None)
    assert text == "a3f9c1e0\n-\n-\n"
    assert agb.parse_idx(text.encode()) == {
        "key": "a3f9c1e0", "pid": None, "starttime": None}


@pytest.mark.parametrize("data", [
    b"",
    b"a3f9c1e0\n",
    b"a3f9c1e0\n48213\n",
    b"a3f9c1e0\n48213\n9182736\nextra\n",
    b"\n48213\n9182736\n",
    b"not-hex!\n48213\n9182736\n",
    b"a3f9c1e0\nnotapid\n9182736\n",
    b"a3f9c1e0\n48213\nnotatime\n",
    b"\xff\xfe\n48213\n9182736\n",
])
def test_parse_idx_rejects_anything_but_exactly_three_valid_lines(agb, data):
    assert agb.parse_idx(data) is None


def test_valid_key_accepts_only_hex(agb):
    assert agb.valid_key(agb.new_key())
    assert not agb.valid_key("")
    assert not agb.valid_key("../../etc/passwd")
    assert not agb.valid_key("g" * 8)
    # The length cap, which nothing pinned: a key is a filename component, and
    # removing `len(key) > 64` passed the whole suite.
    assert agb.valid_key("a" * 64)
    assert not agb.valid_key("a" * 65)


def test_valid_mac_id_rejects_the_two_names_that_are_not_names(agb):
    """A mac-id and a host both become path components (`bridge/<id>.beat`,
    `sessions/<host>/`), so `.` and `..` are the two strings that would make
    `os.path.join` climb out of the statedir. Both refusals were untested --
    deleting the `in (".", "..")` line passed everything."""
    assert agb.valid_mac_id("mac-a1b2c3d4")
    assert not agb.valid_mac_id(".")
    assert not agb.valid_mac_id("..")
    assert not agb.valid_mac_id("")
    assert not agb.valid_mac_id("a/b")
    assert not agb.valid_mac_id("a" * 65)


def test_new_key_is_random_and_hex(agb):
    keys = set(agb.new_key() for _ in range(500))
    assert len(keys) == 500
    assert all(len(k) == 2 * agb.KEY_BYTES for k in keys)


def test_the_key_is_wide_enough_to_be_a_global_identity(agb):
    """64 bits, not 32, because the key is never qualified by host.

    `feed_poll` keys its probe set and `FeedState.entries` on the BARE key
    across every host, the wire's `remove` carries a bare key, and the Mac's
    row map is a bare-key -> row bijection. So a collision between two hosts
    does not produce a confusing row -- it produces **no row at all** for the
    second agent, which is silently invisible to the bridge. On one host it is
    worse: `bind_key` takes `EEXIST` on the *anchor*, never on
    `sessions/<host>/<key>.state`, so the two agents would share one `.state`
    and one `.json` and the sweep would judge one of them by the other's pid.

    The bound is `m*n / 2**b` for `n` live keys and `m` lifetime mints. At 32
    bits with a pessimistic n=500, m=1e5 that is ~1.2% -- a 1-in-86 chance of
    an invisible agent, which is not a bound to carry in a tool whose whole
    purpose is removing silent failure. At 64 bits the same numbers give
    3e-12. Widening cost nothing: `valid_key` already allowed 64 hex
    characters, so no file layout moved and older keys stay valid.
    """
    assert agb.KEY_BYTES >= 8
    assert agb.valid_key(agb.new_key())


def test_mint_writes_key_pid_and_starttime(agb, statedir, set_tmux):
    set_tmux(REAL_TMUX, "%24")
    anchor = agb.resolve_anchor("box2", 48213, 9182736)
    key, minted = agb.bind_key(str(statedir), anchor, 48213, 9182736)
    assert minted
    path = statedir / "idx" / anchor.name()
    assert _read(path) == ("%s\n48213\n9182736\n" % key).encode()


def test_binding_twice_adopts_the_same_key(agb, statedir, set_tmux):
    set_tmux(REAL_TMUX, "%24")
    anchor = agb.resolve_anchor("box2", 48213, 9182736)
    first, minted_first = agb.bind_key(str(statedir), anchor, 48213, 9182736)
    second, minted_second = agb.bind_key(str(statedir), anchor, 48213, 9182736)
    assert first == second
    assert (minted_first, minted_second) == (True, False)


def test_two_panes_in_one_tmux_session_get_two_distinct_keys(agb, statedir,
                                                             set_tmux):
    """`$TMUX` is session-level, so without `$TMUX_PANE` these two agents would
    share an anchor, a key and a row."""
    sd = str(statedir)
    set_tmux(REAL_TMUX, "%24")
    key_a, _ = agb.bind_key(sd, agb.resolve_anchor("box2", 48213, 1), 48213, 1)
    set_tmux(REAL_TMUX, "%25")
    key_b, _ = agb.bind_key(sd, agb.resolve_anchor("box2", 48299, 2), 48299, 2)
    assert key_a != key_b
    assert sorted(os.listdir(str(statedir / "idx"))) == [
        "box2-1244192-%24", "box2-1244192-%25"]


def test_two_non_tmux_agents_on_one_host_get_distinct_keys(agb, statedir,
                                                           set_tmux):
    sd = str(statedir)
    set_tmux(None, None)
    key_a, _ = agb.bind_key(sd, agb.resolve_anchor("machine3", 100, 11), 100, 11)
    key_b, _ = agb.bind_key(sd, agb.resolve_anchor("machine3", 200, 22), 200, 22)
    assert key_a != key_b
    assert len(os.listdir(str(statedir / "idx"))) == 2


def test_a_second_agent_in_the_same_pane_gets_a_new_key(agb, statedir, set_tmux):
    """tmux pane ids are never reused within a server, so the anchor survives
    the first agent. Inheriting its key would rebind a row already marked
    [done] -- and contradict "a minted key is never reused"."""
    sd = str(statedir)
    set_tmux(REAL_TMUX, "%24")
    anchor = agb.resolve_anchor("box2", 48213, 9182736)
    first, _ = agb.bind_key(sd, anchor, 48213, 9182736)
    second, minted = agb.bind_key(sd, anchor, 51000, 9199999)
    assert second != first
    assert minted
    assert _read(statedir / "idx" / anchor.name()) == (
        "%s\n51000\n9199999\n" % second).encode()


def test_agent_pid_reuse_is_caught_by_the_starttime(agb, statedir, set_tmux):
    """Same pid, different starttime: a different process, hence a new key."""
    sd = str(statedir)
    set_tmux(REAL_TMUX, "%24")
    anchor = agb.resolve_anchor("box2", 48213, 9182736)
    first, _ = agb.bind_key(sd, anchor, 48213, 9182736)
    second, _ = agb.bind_key(sd, anchor, 48213, 9199999)
    assert second != first


def test_an_unknown_current_agent_never_re_mints(agb, statedir, set_tmux):
    """Absence of evidence is not evidence: re-minting on a guess orphans the
    previous entry, which is the failure class this project removes."""
    sd = str(statedir)
    set_tmux(REAL_TMUX, "%24")
    anchor = agb.resolve_anchor("box2", 48213, 9182736)
    first, _ = agb.bind_key(sd, anchor, 48213, 9182736)
    again, minted = agb.bind_key(sd, anchor, None, None)
    assert again == first
    assert not minted


def test_an_entry_recorded_without_a_pid_is_adopted_not_re_minted(agb, statedir,
                                                                  set_tmux):
    sd = str(statedir)
    set_tmux(REAL_TMUX, "%24")
    anchor = agb.resolve_anchor("box2", None, None)
    first, _ = agb.bind_key(sd, anchor, None, None)
    again, minted = agb.bind_key(sd, anchor, 48213, 9182736)
    assert again == first
    assert not minted


def test_a_missing_starttime_is_not_positive_evidence(agb, statedir, set_tmux):
    sd = str(statedir)
    set_tmux(REAL_TMUX, "%24")
    anchor = agb.resolve_anchor("box2", 48213, 9182736)
    first, _ = agb.bind_key(sd, anchor, 48213, 9182736)
    again, _ = agb.bind_key(sd, anchor, 48213, None)
    assert again == first


@pytest.mark.parametrize("junk", [b"", b"garbage\n", b"a3f9c1e0\n48213\n"])
def test_a_corrupt_idx_file_is_re_minted(agb, statedir, set_tmux, junk):
    sd = str(statedir)
    set_tmux(REAL_TMUX, "%24")
    anchor = agb.resolve_anchor("box2", 48213, 9182736)
    path = statedir / "idx" / anchor.name()
    with open(str(path), "wb") as handle:
        handle.write(junk)
    key, _ = agb.bind_key(sd, anchor, 48213, 9182736)
    assert agb.valid_key(key)
    assert agb.parse_idx(_read(path))["key"] == key


def test_re_minting_leaves_no_temp_behind(agb, statedir, set_tmux):
    sd = str(statedir)
    set_tmux(REAL_TMUX, "%24")
    anchor = agb.resolve_anchor("box2", 48213, 9182736)
    agb.bind_key(sd, anchor, 48213, 9182736)
    agb.bind_key(sd, anchor, 51000, 9199999)
    assert [n for n in os.listdir(str(statedir / "idx")) if ".tmp." in n] == []


def test_bind_key_gives_up_rather_than_spinning(agb, statedir, set_tmux,
                                                monkeypatch):
    """A pathological idx that never converges must raise, not loop forever."""
    sd = str(statedir)
    set_tmux(REAL_TMUX, "%24")
    anchor = agb.resolve_anchor("box2", 48213, 9182736)
    monkeypatch.setattr(agb, "read_idx", lambda _p: None)  # always "corrupt"
    with pytest.raises(agb.AgbError):
        agb.bind_key(sd, anchor, 48213, 9182736)


def test_mint_creates_the_statedir_on_demand(agb, statedir_path, set_tmux):
    """The mint path may be the very first agb invocation on a host."""
    set_tmux(REAL_TMUX, "%24")
    anchor = agb.resolve_anchor("box2", 48213, 9182736)
    key, minted = agb.bind_key(str(statedir_path), anchor, 48213, 9182736)
    assert minted
    assert (statedir_path / "idx" / anchor.name()).exists()


def test_idx_content_is_present_before_the_link(agb, statedir, set_tmux,
                                                monkeypatch):
    """The whole reason minting is temp+link rather than `O_CREAT|O_EXCL`:
    O_EXCL is atomic *creation*, not creation-with-content, so a loser can read
    an empty file and end up with no key at all."""
    set_tmux(REAL_TMUX, "%24")
    anchor = agb.resolve_anchor("box2", 48213, 9182736)
    seen = []
    real_link = os.link

    def spy(src, dst):
        seen.append(_read(src))
        return real_link(src, dst)

    monkeypatch.setattr(os, "link", spy)
    key, minted = agb.bind_key(str(statedir), anchor, 48213, 9182736)
    assert minted
    assert len(seen) == 1
    assert agb.parse_idx(seen[0]) == {
        "key": key, "pid": 48213, "starttime": 9182736}


def test_the_loser_of_a_link_race_adopts_the_winners_key(agb, statedir,
                                                         set_tmux, monkeypatch):
    """Deterministic sibling of the multi-process race: EEXIST must resolve by
    reading the file, never by failing and never by minting a second key."""
    sd = str(statedir)
    set_tmux(REAL_TMUX, "%24")
    anchor = agb.resolve_anchor("box2", 48213, 9182736)
    winner = agb.new_key()
    real_link = os.link
    raced = []

    def interpose(src, dst):
        # A competitor lands between our read_idx() and our link(). Built with
        # the real link(), not agb.link_idx(), so the spy cannot recurse.
        if not raced:
            raced.append(dst)
            tmp = dst + ".competitor"
            with open(tmp, "w") as handle:
                handle.write(agb.format_idx(winner, 48213, 9182736))
            real_link(tmp, dst)
            os.unlink(tmp)
        return real_link(src, dst)

    monkeypatch.setattr(os, "link", interpose)
    key, minted = agb.bind_key(sd, anchor, 48213, 9182736)
    assert key == winner
    assert not minted


def test_bind_key_touches_exactly_one_statedir_file_when_bound(agb, statedir,
                                                               set_tmux,
                                                               monkeypatch):
    """Half the hot path's two-file NFS budget. Every extra round trip on this
    path is an independent stall point on a hard mount."""
    sd = str(statedir)
    set_tmux(REAL_TMUX, "%24")
    anchor = agb.resolve_anchor("box2", 48213, 9182736)
    key, _ = agb.bind_key(sd, anchor, 48213, 9182736)

    opened = []
    real_open = os.open

    def spy(path, *args, **kwargs):
        if str(path).startswith(sd):
            opened.append(str(path))
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(os, "open", spy)
    again, _ = agb.bind_key(sd, anchor, 48213, 9182736)
    assert again == key
    assert opened == [os.path.join(sd, "idx", anchor.name())]


def test_bind_key_does_not_stat_the_statedir_when_bound(agb, statedir, set_tmux,
                                                        monkeypatch):
    """ensure_statedir() belongs to the mint branch: on the bound path it would
    add several NFS round trips to a budget of two files."""
    sd = str(statedir)
    set_tmux(REAL_TMUX, "%24")
    anchor = agb.resolve_anchor("box2", 48213, 9182736)
    key, minted = agb.bind_key(sd, anchor, 48213, 9182736)
    assert minted

    def boom(path=None):
        raise AssertionError("ensure_statedir must not run on the bound path")

    monkeypatch.setattr(agb, "ensure_statedir", boom)
    again, minted_again = agb.bind_key(sd, anchor, 48213, 9182736)
    # `boom` not firing is only half the claim. Without these two, a `bind_key`
    # that returned None -- or quietly minted a second key -- would satisfy the
    # negative and still have broken the thing the negative exists to protect.
    assert again == key
    assert not minted_again


# ---------------------------------------------------------------------------
# the mint race, across real processes
# ---------------------------------------------------------------------------

def _mint_worker(agb_path, sd, host, pid, starttime, barrier, queue):
    from importlib.machinery import SourceFileLoader
    module = SourceFileLoader("agb_child", agb_path).load_module()
    os.environ["AGB_HOST"] = host
    os.environ["AGB_STATEDIR"] = sd
    anchor = module.Anchor(host, "tmux", 1244192, "%24", pane="%24")
    path = module.idx_path(sd, anchor)
    try:
        barrier.wait(timeout=30)
        key, minted = module.bind_key(sd, anchor, pid, starttime)
        with open(path, "rb") as handle:
            queue.put((key, minted, handle.read(), None))
    except Exception as exc:  # pragma: no cover - reported through the queue
        queue.put((None, None, b"", repr(exc)))


@pytest.mark.skipif(sys.platform != "linux", reason="fork-based race")
def test_concurrent_mints_produce_exactly_one_key(agb, agb_path, statedir):
    """Eight processes, one barrier: one winner, and every loser walks away with
    the winner's key rather than a second key or an empty read."""
    sd = str(statedir)
    workers = 8
    barrier = multiprocessing.Barrier(workers)
    queue = multiprocessing.Queue()
    procs = [
        multiprocessing.Process(
            target=_mint_worker,
            args=(agb_path, sd, "box2", 48213, 9182736, barrier, queue))
        for _ in range(workers)
    ]
    for proc in procs:
        proc.start()
    # try/finally, because everything below can fail: a worker that dies before
    # its `queue.put` makes `queue.get` raise Empty after a 60 s stall, and a
    # `join` that times out leaves `exitcode` None and fails the assert. Either
    # way the remaining processes would never be joined -- orphans outliving the
    # test run, from a test that starts eight of them.
    try:
        results = [queue.get(timeout=60) for _ in range(workers)]
        for proc in procs:
            proc.join(timeout=60)
            assert proc.exitcode == 0
    finally:
        for proc in procs:
            if proc.is_alive():
                proc.terminate()
            proc.join(timeout=10)

    assert [r[3] for r in results] == [None] * workers
    keys = set(r[0] for r in results)
    assert len(keys) == 1
    assert agb.valid_key(keys.pop())
    assert sum(1 for r in results if r[1]) == 1
    for _key, _minted, raw, _err in results:
        assert raw != b""
        assert agb.parse_idx(raw) is not None
    assert len(os.listdir(str(statedir / "idx"))) == 1


# ---------------------------------------------------------------------------
# tmux session name -- resolved once, at mint time
# ---------------------------------------------------------------------------

def _tmux_stub(stub_bin, output="build", exit_code=0):
    log = stub_bin.path / "tmux.log"
    body = (
        "#!/bin/sh\n"
        "{ for a in \"$@\"; do printf '%s\\037' \"$a\"; done; printf '\\n'; } "
        ">> \"" + str(log) + "\"\n"
        "printf '" + output + "\\n'\n"
        "exit " + str(exit_code) + "\n"
    )
    stub_bin.install("tmux", body=body)
    return log


def test_tmux_session_name_comes_from_display_message(agb, stub_bin):
    _tmux_stub(stub_bin)
    assert agb.resolve_tmux_session("%24") == "build"
    assert stub_bin.calls("tmux") == [
        ["display-message", "-p", "-t", "%24", "#S"]]


def test_tmux_session_name_without_a_pane(agb, stub_bin):
    _tmux_stub(stub_bin)
    assert agb.resolve_tmux_session() == "build"
    assert stub_bin.calls("tmux") == [["display-message", "-p", "#S"]]


def test_tmux_failure_is_never_fatal(agb, stub_bin):
    _tmux_stub(stub_bin, output="", exit_code=1)
    assert agb.resolve_tmux_session("%24") is None


def test_missing_tmux_binary_is_never_fatal(agb, stub_bin, monkeypatch):
    monkeypatch.setenv("PATH", str(stub_bin.path))
    assert agb.resolve_tmux_session("%24") is None


def test_empty_tmux_output_is_none(agb, stub_bin):
    _tmux_stub(stub_bin, output="")
    assert agb.resolve_tmux_session("%24") is None


# ---------------------------------------------------------------------------
# ...and it says WHY it failed. Constraint #14's whole premise is that a hook
# runs in a minimal environment where a bare program name may not resolve --
# and this was the one call in the tool that relied on exactly that, then
# collapsed every failure into a bare None with no breadcrumb anywhere.
# ---------------------------------------------------------------------------

def test_a_tmux_that_will_not_run_records_the_reason(agb, stub_bin, monkeypatch):
    monkeypatch.setenv("PATH", str(stub_bin.path))
    why = []
    assert agb.resolve_tmux_session("%24", why=why) is None
    assert why and "FileNotFoundError" in why[0]


def test_a_tmux_that_exits_non_zero_records_the_reason(agb, stub_bin):
    _tmux_stub(stub_bin, output="", exit_code=1)
    why = []
    assert agb.resolve_tmux_session("%24", why=why) is None
    assert why and "CalledProcessError" in why[0]


def test_a_tmux_that_prints_nothing_records_the_reason(agb, stub_bin):
    _tmux_stub(stub_bin, output="")
    why = []
    assert agb.resolve_tmux_session("%24", why=why) is None
    assert why and "no session name" in why[0]


def test_a_successful_resolution_records_no_reason(agb, stub_bin):
    _tmux_stub(stub_bin)
    why = []
    assert agb.resolve_tmux_session("%24", why=why) == "build"
    assert why == []


def test_tmux_is_run_from_the_servers_own_exe_when_the_pid_resolves(agb,
                                                                    stub_bin,
                                                                    tmp_path,
                                                                    monkeypatch):
    """`$TMUX` carries the tmux server's pid, so `/proc/<spid>/exe` is the very
    binary that owns this pane -- not a `$PATH` guess a hook may not have."""
    real = tmp_path / "opt" / "tmux"
    os.makedirs(str(real.parent))
    _tmux_stub(stub_bin)
    os.rename(str(stub_bin.path / "tmux"), str(real))
    proc = tmp_path / "proc" / "4242"
    os.makedirs(str(proc))
    os.symlink(str(real), str(proc / "exe"))
    monkeypatch.setattr(agb, "PROC", str(tmp_path / "proc"))
    monkeypatch.setenv("PATH", str(stub_bin.path))     # nothing named tmux here
    assert agb.resolve_tmux_session("%24", spid=4242) == "build"


def test_an_exe_that_is_not_tmux_is_ignored(agb, stub_bin, tmp_path,
                                            monkeypatch):
    """A pid is recycled; the basename is what stops `/proc/<spid>/exe` from
    handing an arbitrary program a `display-message` command line."""
    _tmux_stub(stub_bin)
    proc = tmp_path / "proc" / "4242"
    os.makedirs(str(proc))
    os.symlink("/bin/sh", str(proc / "exe"))
    monkeypatch.setattr(agb, "PROC", str(tmp_path / "proc"))
    assert agb.resolve_tmux_session("%24", spid=4242) == "build"
    assert stub_bin.calls("tmux") == [["display-message", "-p", "-t", "%24",
                                       "#S"]]


def test_an_upgraded_tmux_falls_back_to_the_one_on_path(agb, stub_bin,
                                                         tmp_path, monkeypatch):
    """After ANY tmux upgrade the running server's binary is unlinked and
    `/proc/<spid>/exe` reads `/usr/bin/tmux (deleted)`.

    That basename still starts with "tmux", so it passed the name check and then
    failed to exec -- and every agent minted in that server permanently lost its
    session name, because `tmux` is resolved once and never again. Reproduced
    against a real tmux server whose binary had been deleted underneath it.
    """
    real = tmp_path / "opt" / "tmux"
    os.makedirs(str(real.parent))
    _tmux_stub(stub_bin)                        # a working tmux ON $PATH
    proc = tmp_path / "proc" / "4242"
    os.makedirs(str(proc))
    os.symlink(str(real), str(proc / "exe"))    # ...pointing at nothing
    monkeypatch.setattr(agb, "PROC", str(tmp_path / "proc"))

    why = []
    assert agb.resolve_tmux_session("%24", spid=4242, why=why) == "build"
    assert why == []


def test_a_deleted_exe_whose_replacement_is_in_place_is_used(agb, stub_bin,
                                                              tmp_path,
                                                              monkeypatch):
    """The usual shape of an upgrade: the path is re-created by the new package,
    so stripping the suffix lands on a binary that works -- and it is preferred
    over `$PATH`, which a hook may not have (constraint #14)."""
    real = tmp_path / "opt" / "tmux"
    os.makedirs(str(real.parent))
    _tmux_stub(stub_bin)
    os.rename(str(stub_bin.path / "tmux"), str(real))
    proc = tmp_path / "proc" / "4242"
    os.makedirs(str(proc))
    # the link a deleted-then-replaced binary leaves behind
    os.symlink(str(real) + " (deleted)", str(proc / "exe"))
    monkeypatch.setattr(agb, "PROC", str(tmp_path / "proc"))
    monkeypatch.setenv("PATH", str(stub_bin.path))     # nothing named tmux here
    assert agb.resolve_tmux_session("%24", spid=4242) == "build"


def test_an_exe_that_is_not_executable_is_not_used(agb, stub_bin, tmp_path,
                                                    monkeypatch):
    """The name check alone was never enough: `/proc/<spid>/exe` has to resolve
    to something that can actually be run."""
    real = tmp_path / "opt" / "tmux"
    os.makedirs(str(real.parent))
    _tmux_stub(stub_bin)
    with open(str(real), "w") as handle:
        handle.write("not executable\n")
    os.chmod(str(real), 0o600)
    proc = tmp_path / "proc" / "4242"
    os.makedirs(str(proc))
    os.symlink(str(real), str(proc / "exe"))
    monkeypatch.setattr(agb, "PROC", str(tmp_path / "proc"))
    assert agb.resolve_tmux_session("%24", spid=4242) == "build"


def test_an_unresolvable_tmux_session_is_breadcrumbed_at_the_mint(
        agb, statedir, set_tmux, set_host, set_agent_pid, fake_proc, stub_bin,
        monkeypatch):
    """The row degrades to `--pane` with no `--tmux`, once, silently and for
    ever -- `resolve_tmux_session` runs only when a key is minted and every
    later transition takes `tmux` from the stored record. This line is the only
    report there will ever be."""
    fake_proc({48213: {"comm": "claude", "ppid": 1, "starttime": 9182736}})
    set_agent_pid(48213)
    set_host("box2")
    set_tmux(REAL_TMUX, "%24")
    monkeypatch.setenv("PATH", str(stub_bin.path))     # no tmux anywhere
    ident = agb.resolve_identity(str(statedir))
    assert (ident.pane, ident.tmux) == ("%24", None)
    with open(agb.err_log_path(str(statedir), ident.key, "box2")) as handle:
        log = handle.read()
    assert "no tmux session for pane %24" in log
    assert "FileNotFoundError" in log


def test_a_resolved_tmux_session_is_not_breadcrumbed(agb, statedir, set_tmux,
                                                     set_host, set_agent_pid,
                                                     fake_proc, stub_bin):
    _tmux_stub(stub_bin)
    fake_proc({48213: {"comm": "claude", "ppid": 1, "starttime": 9182736}})
    set_agent_pid(48213)
    set_host("box2")
    set_tmux(REAL_TMUX, "%24")
    ident = agb.resolve_identity(str(statedir))
    assert ident.tmux == "build"
    assert not os.path.exists(agb.err_log_path(str(statedir), ident.key,
                                               "box2"))


# ---------------------------------------------------------------------------
# resolve_identity -- the whole thing wired together
# ---------------------------------------------------------------------------

def test_identity_carries_the_pane_and_the_tmux_session(agb, statedir, set_tmux,
                                                        set_host, set_agent_pid,
                                                        fake_proc, stub_bin):
    _tmux_stub(stub_bin)
    fake_proc({48213: {"comm": "claude", "ppid": 1, "starttime": 9182736}})
    set_agent_pid(48213)
    set_host("box2")
    set_tmux(REAL_TMUX, "%24")
    ident = agb.resolve_identity(str(statedir))
    assert ident.minted
    assert (ident.host, ident.pid, ident.starttime) == ("box2", 48213, 9182736)
    assert (ident.pane, ident.tmux, ident.label) == ("%24", "build", "build")
    assert ident.cwd == os.getcwd()


def test_identity_resolves_tmux_only_at_mint_time(agb, statedir, set_tmux,
                                                  set_host, set_agent_pid,
                                                  fake_proc, stub_bin):
    """A subprocess per transition is not affordable; the documented cost is
    that renaming the tmux session afterwards does not update `label`."""
    _tmux_stub(stub_bin)
    fake_proc({48213: {"comm": "claude", "ppid": 1, "starttime": 9182736}})
    set_agent_pid(48213)
    set_host("box2")
    set_tmux(REAL_TMUX, "%24")
    first = agb.resolve_identity(str(statedir))
    second = agb.resolve_identity(str(statedir))
    assert second.key == first.key
    assert not second.minted
    assert (second.tmux, second.label) == (None, None)
    assert len(stub_bin.calls("tmux")) == 1


def test_identity_without_tmux_has_no_attach_target(agb, statedir, set_tmux,
                                                    set_host, set_agent_pid,
                                                    fake_proc, stub_bin):
    """Machine #3 over plain ssh: defined up front, not discovered at runtime."""
    _tmux_stub(stub_bin)
    fake_proc({48213: {"comm": "claude", "ppid": 1, "starttime": 9182736}})
    set_agent_pid(48213)
    set_host("machine3")
    set_tmux(None, None)
    ident = agb.resolve_identity(str(statedir))
    assert (ident.pane, ident.tmux) == (None, None)
    assert stub_bin.calls("tmux") == []


def test_identity_label_falls_back_to_the_cwd_basename(agb, statedir, set_tmux,
                                                       set_host, set_agent_pid,
                                                       fake_proc, tmp_path,
                                                       monkeypatch):
    fake_proc({48213: {"comm": "claude", "ppid": 1, "starttime": 9182736}})
    set_agent_pid(48213)
    set_host("machine3")
    set_tmux(None, None)
    workdir = tmp_path / "feature-branch"
    workdir.mkdir()
    monkeypatch.chdir(str(workdir))
    ident = agb.resolve_identity(str(statedir))
    assert ident.label == "feature-branch"


def test_default_label_falls_back_to_the_host(agb, set_host):
    set_host("machine3")
    assert agb.default_label("/shared/work/project/", None) == "project"
    assert agb.default_label("/", None) == "machine3"
    assert agb.default_label("", None) == "machine3"


def test_identity_without_an_identifiable_agent_stores_no_pid(agb, statedir,
                                                              set_tmux,
                                                              set_host,
                                                              monkeypatch):
    """Fail safe (constraint #11): no pid means no sweep may ever unlink it."""
    monkeypatch.setenv("AGB_AGENT_PID", "-")
    set_host("box2")
    set_tmux(REAL_TMUX, "%24")
    ident = agb.resolve_identity(str(statedir), want_tmux=False)
    assert (ident.pid, ident.starttime) == (None, None)
    raw = _read(statedir / "idx" / ident.anchor.name())
    assert raw.decode().splitlines()[1:] == ["-", "-"]
    assert agb.parse_idx(raw)["pid"] is None


def test_identity_uses_the_resolved_statedir_when_none_is_given(agb, statedir,
                                                                set_tmux,
                                                                set_host,
                                                                monkeypatch):
    monkeypatch.setenv("AGB_AGENT_PID", "-")
    set_host("box2")
    set_tmux(REAL_TMUX, "%24")
    ident = agb.resolve_identity(want_tmux=False)
    assert (statedir / "idx" / ident.anchor.name()).exists()


# ---------------------------------------------------------------------------
# structural guards
# ---------------------------------------------------------------------------

def test_subprocess_is_imported_only_inside_the_farm_and_bridge_sites(
        agb_tree, all_trees):
    """A module-level `import subprocess` would be paid by every hook; a
    subprocess on the transition path would be paid by every state change.

    Nine legitimate sites and no more, counted across **all three** files: the
    tmux resolver (once per mint, on the farm), the two halves of the bridge's
    ssh handling, Task 4b's `_run_command` -- the single door to `agtermctl`
    and to the notifier (Mac only, in `agb_mac`, and never reachable from
    `cmd_hook`) -- Task 6b's `prune_via_ssh`, Task 7's `pane_attach` and Task
    9a's `_probe_run`, plus the two pane openers. `_wait_or_kill` is a named
    function precisely so it can be listed here rather than hiding inside a
    method called `close`, which would collide by name with every other `close`
    in the file.

    ⚠️ `_probe_run` was added by Task 9a and is the site that makes
    `install-hooks` a *probe* rather than an existence check: it runs
    `<python> -S -E <agb> version` and requires the right answer back before a
    hook command is written anywhere. `os.access` on the interpreter answers a
    question about mode bits; every one of `agr`'s five silent no-ops would have
    passed that. It takes an injectable `run` like the other spawning sites, so
    the argv a test sees is a list comparison.

    ⚠️ `pane_attach` was added by Task 7 and is the one site that runs its child
    **in a loop**: `agb pane` is the row's own command, so `os.exec*` would end
    the row's terminal on the first `C-b d`. It takes an injectable `run` for
    the same reason `bridge_spawn` and `prune_via_ssh` do -- the argv is a list
    comparison in a test rather than something only a real ssh could reveal.

    ⚠️ `prune_via_ssh` was added by Task 6b and is the one site that spawns a
    *destructive* command: `agb prune` re-run on the owning host, where
    `kill(pid, 0)` is meaningful. It is a named function with an injectable
    `run` for the same reason `bridge_spawn` is -- so the argv is a string
    comparison in a test rather than something only an ssh could reveal.

    That `_run_command` is *one* function is the point of listing it: every
    `agtermctl` invocation in the renderer goes through it, so "a failed
    invocation is data, never an exception and never a hang" is a property of
    one place rather than of every call site.

    ⚠️ `open_split` and `open_drawer` open agterm's split pane and its scratch
    drawer for `agb pane`'s `[s]` and `[d]`. They are the *second and third*
    doors to `agtermctl`, which needs saying: `agb_mac`'s `_run_command` is the
    renderer's single door, and these exist because `agb pane` runs on the Mac
    but lives in `agb_ops`, which never loads `agb_mac`. All three obey the same
    rule -- a failure is written out and returned, never raised, so a missing or
    broken `agtermctl` costs the row its pane and nothing else. Both take an
    injectable `run` like every other spawning site.

    The pair is duplicated rather than parametrised, and deliberately: they are
    expected to diverge, since `session scratch` takes a `--command` that
    `session split` has no equivalent for. Merging them is not a tidy-up.

    The three bridge sites keep their imports function-local even though
    `agb_mac`'s module scope costs a hook nothing -- so that this stays one rule
    over one list, instead of one rule per file that has to be re-read to see
    whether the pair still adds up.
    """
    assert "subprocess" not in conftest.toplevel_imports(agb_tree)
    holders = set()
    for name, node in conftest.functions(*all_trees).items():
        for child in ast.walk(node):
            if isinstance(child, ast.Import):
                for alias in child.names:
                    if alias.name.split(".")[0] == "subprocess":
                        holders.add(name)
    assert holders == set(["resolve_tmux_session", "bridge_spawn",
                           "_wait_or_kill", "_run_command", "prune_via_ssh",
                           "pane_attach", "_probe_run", "open_split",
                           "open_drawer"])


def test_minting_never_uses_o_excl_to_create_the_idx_file(agb_tree):
    """O_EXCL is atomic creation, not creation-with-content: the loser of the
    race can read an empty file. The idx file is created by link()."""
    node = conftest.functions(agb_tree)["link_idx"]
    made = conftest.calls(node)
    assert ("os", "link") in made
    assert ("os", "open") in made  # ...on the *temp*, which is then linked


def test_bind_key_is_the_only_key_minting_path(all_trees):
    """One minting path, as with own_host(): a second one is how an agent ends
    up with two keys and two rows.

    ⚠️ **Amended by Task 9b**, and the claim is unchanged. `new_key()` is the
    tool's only source of random hex, and `generate_mac_id` borrows it for the
    random half of a **mac-id** -- which is not a session key: it never reaches
    `idx/`, never appears in `sessions/`, and cannot bind a row. So it is
    *listed* rather than exempted, and the assertion below keeps the property
    that matters by naming the only function that can turn a `new_key()` into a
    session: `bind_key`, through `link_idx`. A third name appearing here is
    still the bug this test was written for.
    """
    holders = set()
    for name, node in conftest.functions(*all_trees).items():
        if name == "new_key":
            continue
        for _base, attr in conftest.calls(node):
            if attr == "new_key":
                holders.add(name)
    assert holders == set(["bind_key", "generate_mac_id"])

    # The half of the claim that a listed name could otherwise erode: only
    # `bind_key` turns a minted key into an idx entry.
    funcs = conftest.functions(*all_trees)
    linkers = set(name for name, node in funcs.items()
                  if "link_idx" in [attr for _base, attr
                                    in conftest.calls(node)])
    assert linkers == set(["bind_key"])
    assert "link_idx" not in conftest.reachable_from(funcs, "generate_mac_id")


def test_idle_is_not_an_agent_reportable_state(agb):
    """Amendment 2: there is no `unknown`, and `idle` renders as *no glyph*, so
    an agent reporting it would be indistinguishable from a [done] row. The
    bridge still emits it for the [?] and [done] renderings."""
    assert agb.STATUS_VOCABULARY == ("active", "blocked", "completed", "idle")
    assert "unknown" not in agb.STATUS_VOCABULARY
    assert agb.AGENT_STATES == ("active", "blocked", "completed")
    assert "idle" not in agb.AGENT_STATES
    for state in agb.AGENT_STATES:
        assert state in agb.STATUS_VOCABULARY
