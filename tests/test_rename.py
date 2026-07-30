"""`agb rename <key> <label>` -- change the label a row is titled from.

The interesting half is not the write; it is *when the change is published*.
The feed re-reads `.json` only when `seq` moves, and `seq` lives in `.state`
whose mtime is the beat -- so publishing costs a liveness claim, and this
command may only make one it can prove.
"""

import json
import os

import pytest

import conftest


KEY = "a3f9c1e0b4d27561"
HOST = "box2"


class Out(object):
    def __init__(self):
        self.text = ""

    def write(self, data):
        self.text += data

    def flush(self):
        pass


@pytest.fixture
def seeded(agb, statedir):
    """A record and a `.state` for a **live** agent on this host."""
    sd = str(statedir)
    pid, starttime = conftest.live_agent()
    agb.ensure_session_dir(sd, HOST)
    # The record deliberately DISAGREES with `.state`: a record can legitimately
    # be stale (the feed takes state/pid/starttime from `.state` alone), and a
    # fixture where the two agree cannot tell which source the code used.
    record = {"v": 1, "key": KEY, "label": "old-name", "host": HOST,
              "pid": 999999, "starttime": 1, "tmux": "build",
              "pane": "%24", "cwd": "/work/api", "state": "completed",
              "seq": 2, "updated": 1.0}
    agb.atomic_write(agb.record_path(sd, KEY, HOST),
                     json.dumps(record, sort_keys=True).encode("utf-8"))
    agb.write_in_place(agb.state_path(sd, KEY, HOST),
                       agb.format_state("active", HOST, pid, starttime, 4))
    return sd, pid, starttime


def _record(agb, sd):
    with open(agb.record_path(sd, KEY, HOST)) as handle:
        return json.load(handle)


# ---------------------------------------------------------------------------
# the label
# ---------------------------------------------------------------------------

def test_rename_sets_the_label_in_the_record(ops, agb, seeded, set_host):
    sd = seeded[0]
    set_host(HOST)
    out = Out()
    assert ops.run_rename([KEY, "new-name", "--statedir", sd], out=out) == 0
    assert _record(agb, sd)["label"] == "new-name"
    assert "old-name" in out.text and "new-name" in out.text


def test_a_live_agent_on_this_host_publishes_immediately(ops, agb, seeded,
                                                         set_host):
    """`seq` moves in BOTH files, which is what makes the feed re-read the
    record. The beat that refreshes is honest: the agent is provably alive."""
    sd = seeded[0]
    set_host(HOST)
    ops.run_rename([KEY, "new-name", "--statedir", sd], out=Out())
    parsed, _beat = agb.read_state_entry(sd, HOST, KEY)
    assert parsed["seq"] == 5
    assert _record(agb, sd)["seq"] == 5


def test_a_foreign_host_never_gets_its_beat_refreshed(ops, agb, seeded,
                                                      set_host):
    """The whole point: publishing costs a beat, and a beat is a claim that the
    agent is alive. This host cannot prove that about another host's agent, so
    it updates the record and says the row lags."""
    sd = seeded[0]
    set_host("somewhere-else")
    before = os.stat(agb.state_path(sd, KEY, HOST)).st_mtime
    out = Out()
    assert ops.run_rename([KEY, "new", "--statedir", sd, "--host", HOST],
                          out=out) == 0
    assert os.stat(agb.state_path(sd, KEY, HOST)).st_mtime == before
    assert _record(agb, sd)["label"] == "new"          # record still updated
    assert "next state change" in out.text


def test_a_dead_agent_on_this_host_also_gets_no_beat(ops, agb, statedir,
                                                     set_host):
    """Own host is not enough -- `proof_of_life` has to say so."""
    sd = str(statedir)
    pid, starttime = conftest.dead_agent()
    set_host(HOST)
    agb.ensure_session_dir(sd, HOST)
    agb.atomic_write(agb.record_path(sd, KEY, HOST), json.dumps(
        {"v": 1, "key": KEY, "label": "old", "host": HOST, "seq": 2},
        sort_keys=True).encode("utf-8"))
    agb.write_in_place(agb.state_path(sd, KEY, HOST),
                       agb.format_state("active", HOST, pid, starttime, 2))
    before = os.stat(agb.state_path(sd, KEY, HOST)).st_mtime
    out = Out()
    assert ops.run_rename([KEY, "new", "--statedir", sd], out=out) == 0
    assert os.stat(agb.state_path(sd, KEY, HOST)).st_mtime == before
    assert "kill(pid) does not answer" in out.text


def test_the_state_fields_are_never_taken_from_the_record(ops, agb, seeded,
                                                          set_host):
    """`state`/`pid`/`starttime` reach the wire from `.state`, so the republished
    `.state` must carry what was read there rather than the record's copy --
    which can legitimately be stale."""
    sd = seeded[0]
    _sd, pid, starttime = seeded
    set_host(HOST)
    ops.run_rename([KEY, "new-name", "--statedir", sd], out=Out())
    parsed, _beat = agb.read_state_entry(sd, HOST, KEY)
    assert (parsed["state"], parsed["pid"], parsed["starttime"]) == \
        ("active", pid, starttime)


# ---------------------------------------------------------------------------
# refusals
# ---------------------------------------------------------------------------

def test_an_unknown_key_is_reported_not_created(ops, agb, statedir, set_host):
    set_host(HOST)
    out = Out()
    assert ops.run_rename(["b" * 16, "new", "--statedir", str(statedir)],
                          out=out) == 1
    assert "no record" in out.text


def test_an_unreadable_state_changes_nothing(ops, agb, statedir, set_host):
    """Without a readable `.state` the label could not reach the row anyway, and
    a partial write would be worse than a refusal."""
    sd = str(statedir)
    set_host(HOST)
    agb.ensure_session_dir(sd, HOST)
    agb.atomic_write(agb.record_path(sd, KEY, HOST), json.dumps(
        {"v": 1, "key": KEY, "label": "old", "seq": 1},
        sort_keys=True).encode("utf-8"))
    agb.write_in_place(agb.state_path(sd, KEY, HOST), b"truncated")
    out = Out()
    assert ops.run_rename([KEY, "new", "--statedir", sd], out=out) == 1
    assert _record(agb, sd)["label"] == "old"
    assert "nothing was changed" in out.text


@pytest.mark.parametrize("label", [
    "",                       # empty
    " leading",               # leading space
    "trailing ",              # trailing space
    "has · separator",   # the title's own field separator
    "with\nnewline",
    "with\ttab",
    "x" * 41,                 # over the cap
])
def test_labels_that_would_break_a_title_are_refused(ops, label):
    assert not ops.valid_label(label)


@pytest.mark.parametrize("label", ["a", "api-refactor", "x" * 40,
                                   "two words", "UPPER_and-1"])
def test_ordinary_labels_are_accepted(ops, label):
    assert ops.valid_label(label)


def test_a_bad_label_never_reaches_the_record(ops, agb, seeded, set_host):
    sd = seeded[0]
    set_host(HOST)
    with pytest.raises(agb.AgbError):
        ops.run_rename([KEY, "bad · label", "--statedir", sd], out=Out())
    assert _record(agb, sd)["label"] == "old-name"


def test_the_key_must_be_a_minted_one(ops, agb, seeded, set_host):
    sd = seeded[0]
    set_host(HOST)
    with pytest.raises(agb.AgbError):
        ops.run_rename(["../../etc/passwd", "new", "--statedir", sd],
                       out=Out())


@pytest.mark.parametrize("argv", [[KEY, "a", "b"], []])
def test_it_needs_a_label_and_at_most_a_key(ops, agb, argv):
    with pytest.raises(agb.AgbError):
        ops.parse_rename_args(argv)


def test_a_lone_key_is_refused_rather_than_used_as_a_label(ops, agb):
    """`agb rename <16 hex>` reads as "rename this key" and would otherwise
    parse as "label the current row <16 hex>" -- the wrong row, named something
    nobody meant, with no error anywhere."""
    with pytest.raises(agb.AgbError) as excinfo:
        ops.parse_rename_args([KEY])
    assert "looks like a key" in str(excinfo.value)
    assert KEY in str(excinfo.value)


def test_a_short_hex_label_is_still_a_label(ops):
    """`ab` is hex, but nobody means it as a key. The refusal is for things long
    enough to actually be one."""
    assert ops.parse_rename_args(["ab"])["label"] == "ab"


def test_rename_is_reachable_through_the_shared_ops_door(agb):
    assert "rename" in agb.OPS_COMMANDS


# ---------------------------------------------------------------------------
# addressing: nobody retypes 16 hex characters
# ---------------------------------------------------------------------------

def test_one_argument_renames_the_row_this_terminal_is_in(ops, agb, seeded,
                                                          set_host, set_tmux,
                                                          monkeypatch):
    """The common case: you are sitting in the agent you want to rename, and
    requiring its key would be the tool's least usable moment."""
    sd = seeded[0]
    set_host(HOST)
    set_tmux("/tmp/tmux-1/default,4242,0", "%24")
    anchor = agb.resolve_anchor(HOST)
    agb.link_idx(agb.idx_path(sd, anchor), KEY, seeded[1], seeded[2])
    out = Out()
    assert ops.run_rename(["from-here", "--statedir", sd], out=out) == 0
    assert _record(agb, sd)["label"] == "from-here"


def test_one_argument_never_mints_a_key(ops, agb, statedir, set_host,
                                        set_tmux):
    """`resolve_identity` would create one. Naming a thing must not be what
    brings it into existence."""
    sd = str(statedir)
    set_host(HOST)
    set_tmux("/tmp/tmux-1/default,4242,0", "%24")
    out = Out()
    assert ops.run_rename(["whatever", "--statedir", sd], out=out) == 1
    assert "no agent recorded for this terminal" in out.text
    assert agb.list_session_keys(sd, HOST) == []
    assert not os.path.exists(agb.idx_dir(sd)) or \
        os.listdir(agb.idx_dir(sd)) == []


def test_a_key_prefix_addresses_the_row(ops, agb, seeded, set_host):
    sd = seeded[0]
    set_host(HOST)
    assert ops.run_rename([KEY[:4], "by-prefix", "--statedir", sd],
                          out=Out()) == 0
    assert _record(agb, sd)["label"] == "by-prefix"


def test_an_ambiguous_prefix_is_refused_rather_than_guessed(ops, agb, statedir,
                                                            set_host):
    """Two keys, one prefix: picking either would rename the wrong row, and the
    operator would not find out until they looked at the sidebar."""
    sd = str(statedir)
    set_host(HOST)
    agb.ensure_session_dir(sd, HOST)
    pid, starttime = conftest.live_agent()
    for key in ("abcd0000" + "0" * 8, "abcd1111" + "0" * 8):
        agb.atomic_write(agb.record_path(sd, key, HOST), json.dumps(
            {"v": 1, "key": key, "label": "x", "seq": 1},
            sort_keys=True).encode("utf-8"))
        agb.write_in_place(agb.state_path(sd, key, HOST),
                           agb.format_state("active", HOST, pid, starttime, 1))
    with pytest.raises(agb.AgbError) as excinfo:
        ops.run_rename(["abcd", "new", "--statedir", sd], out=Out())
    assert "matches 2 keys" in str(excinfo.value)


def test_a_whole_key_is_not_treated_as_a_prefix_of_another(ops, agb, statedir,
                                                           set_host):
    """An exact key that exists wins outright, even if it prefixes others."""
    sd = str(statedir)
    set_host(HOST)
    agb.ensure_session_dir(sd, HOST)
    pid, starttime = conftest.live_agent()
    short = "abcd0000" + "0" * 8
    for key in (short, short[:-1] + "f"):
        agb.atomic_write(agb.record_path(sd, key, HOST), json.dumps(
            {"v": 1, "key": key, "label": "x", "seq": 1},
            sort_keys=True).encode("utf-8"))
        agb.write_in_place(agb.state_path(sd, key, HOST),
                           agb.format_state("active", HOST, pid, starttime, 1))
    assert ops.run_rename([short, "exact", "--statedir", sd], out=Out()) == 0
    with open(agb.record_path(sd, short, HOST)) as handle:
        assert json.load(handle)["label"] == "exact"


@pytest.mark.parametrize("given", ["", "zz", "nothex", "g" * 4])
def test_something_that_is_not_hex_is_refused(ops, agb, given):
    with pytest.raises(agb.AgbError):
        ops.parse_rename_args([given, "label"])
