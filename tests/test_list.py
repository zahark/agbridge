"""`agb list` -- the sessions this statedir knows about.

Exists because every other command takes a key and `doctor` was the only place
to see one: a diagnostic report is the wrong place to look up an identifier you
need several times a day.
"""

import json

import pytest

import conftest


HOST = "box2"
OTHER = "box3"


class Out(object):
    def __init__(self):
        self.text = ""

    def write(self, data):
        self.text += data

    def flush(self):
        pass


def _session(agb, sd, host, key, label, state="active", pid=None,
             starttime=None):
    if pid is None:
        pid, starttime = conftest.live_agent()
    agb.ensure_session_dir(sd, host)
    agb.atomic_write(agb.record_path(sd, key, host), json.dumps(
        {"v": 1, "key": key, "label": label, "host": host, "pane": "%1",
         "cwd": "/work/x", "seq": 1}, sort_keys=True).encode("utf-8"))
    agb.write_in_place(agb.state_path(sd, key, host),
                       agb.format_state(state, host, pid, starttime, 1))
    agb.rebuild_marker(sd, host)


@pytest.fixture
def populated(agb, statedir, set_host):
    sd = str(statedir)
    set_host(HOST)
    _session(agb, sd, HOST, "aaaa1111" + "0" * 8, "mine")
    _session(agb, sd, OTHER, "bbbb2222" + "0" * 8, "theirs", state="blocked")
    return sd


def test_list_shows_every_host_not_just_this_one(ops, populated):
    out = Out()
    assert ops.run_list(["--statedir", populated], out=out) == 0
    assert "mine" in out.text and "theirs" in out.text
    assert "2 sessions" in out.text


def test_a_foreign_host_is_marked_as_such(ops, populated):
    """The sidebar shows them all; which ones this machine can act on is a
    different question, and `prune`/`rename` both depend on the answer."""
    out = Out()
    ops.run_list(["--statedir", populated], out=out)
    theirs = [l for l in out.text.splitlines() if "theirs" in l][0]
    mine = [l for l in out.text.splitlines() if "mine" in l][0]
    assert "not this host" in theirs
    assert "not this host" not in mine


def test_the_key_is_shown_as_an_addressable_prefix(ops, populated):
    """Truncated on purpose: nobody retypes 16 hex characters, and every
    command that takes a key takes a prefix."""
    out = Out()
    ops.run_list(["--statedir", populated], out=out)
    assert "aaaa1111" in out.text
    assert "aaaa1111" + "0" * 8 not in out.text        # not the whole key
    assert "unique prefix" in out.text


def test_a_prefix_from_the_list_actually_addresses_the_row(ops, agb,
                                                           populated,
                                                           set_host):
    """The listing is only useful if what it prints can be pasted into the next
    command -- so that round trip is the test, not the formatting."""
    set_host(HOST)
    out = Out()
    ops.run_list(["--statedir", populated], out=out)
    shown = [l.split()[0] for l in out.text.splitlines()
             if l.startswith("aaaa")][0]
    assert ops.run_rename([shown, "renamed", "--statedir", populated],
                          out=Out()) == 0
    with open(agb.record_path(populated, "aaaa1111" + "0" * 8, HOST)) as fh:
        assert json.load(fh)["label"] == "renamed"


def test_host_filters(ops, populated):
    out = Out()
    ops.run_list(["--statedir", populated, "--host", OTHER], out=out)
    assert "theirs" in out.text
    assert "mine" not in out.text


def test_an_empty_statedir_says_so_rather_than_printing_a_bare_header(
        ops, statedir):
    out = Out()
    assert ops.run_list(["--statedir", str(statedir)], out=out) == 0
    assert "no sessions" in out.text
    assert "KEY" not in out.text


def test_a_key_with_no_readable_state_is_not_listed(ops, agb, populated):
    """Same rule as everywhere: a short or malformed read is no information,
    and inventing a row from it would be a claim nothing supports."""
    key = "cccc3333" + "0" * 8
    agb.write_in_place(agb.state_path(populated, key, HOST), b"truncated")
    agb.rebuild_marker(populated, HOST)
    out = Out()
    ops.run_list(["--statedir", populated], out=out)
    assert "cccc3333" not in out.text


def test_list_never_readdirs_a_foreign_session_directory(all_trees):
    """Keys come from marker CONTENT. A foreign `readdir` can be served from the
    attribute cache for up to acdirmax, which would hide a key created a minute
    ago (constraint #5)."""
    funcs = conftest.functions(*all_trees)
    reachable = conftest.reachable_from(funcs, "all_entries")
    assert "read_marker_keys" in reachable          # the walk really ran
    assert "list_session_keys" not in reachable


def test_bare_rename_prints_the_usage_and_the_list(ops, populated,
                                                   monkeypatch, capsys):
    """No arguments is someone looking for the shape and probably for a key
    too, so it answers both rather than with an argument error."""
    monkeypatch.setattr(ops.agb, "statedir", lambda: populated)
    assert ops.run_ops("rename", []) == 0
    text = capsys.readouterr().out
    assert "usage: agb rename" in text
    assert "mine" in text
    assert text.index("usage") < text.index("KEY")     # usage first


def test_list_is_reachable_through_the_shared_ops_door(agb):
    assert "list" in agb.OPS_COMMANDS
