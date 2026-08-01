"""Task 4c -- the Mac side lives in a second file the hook never loads.

The whole task is one claim: **a hook must never open, read or compile
`agb_mac`.** That claim is worth exactly as much as the test behind it, and the
obvious test -- time the hook and assert it got faster -- is the wrong one:
timings on an NFS box drift by milliseconds between runs, so it would flake in
both directions and eventually be deleted or loosened.

So the claim is tested by *removing the file's readability*. A copy of the tree
is made with `agb_mac` at mode 000; if a hook so much as `open()`s it, the read
fails. The negative control is the same tree run through `agb bridge`, which
must fail -- otherwise the poison proves nothing and the whole file is a
tautology.

The rest is the structural half: the loader is reachable from `cmd_bridge` and
from nowhere else, the two files do not duplicate each other's helpers, and both
halves share one module object rather than two copies of the same state.
"""

import ast
import errno
import os
import shutil
import subprocess
import sys

import pytest

import conftest


HOST = "box2"
PID = 48213
STARTTIME = 9182736


# ---------------------------------------------------------------------------
# a poisoned copy of the tree
# ---------------------------------------------------------------------------

@pytest.fixture
def poisoned_tree(tmp_path, repo_root):
    """A copy of `agb` + `agb_mac` in which the Mac-side file cannot be read.

    Mode 000 rather than a syntax error or a `raise`, because it fails at
    `open()` rather than at exec: the checkbox says the hook must never *open,
    read or compile* the file, and only an unreadable file tests all three at
    once.
    """
    tree = tmp_path / "tree"
    tree.mkdir()
    agb_copy = tree / "agb"
    mac_copy = tree / "agb_mac"
    shutil.copyfile(os.path.join(repo_root, "agb"), str(agb_copy))
    shutil.copyfile(os.path.join(repo_root, "agb_mac"), str(mac_copy))
    os.chmod(str(mac_copy), 0o000)

    class Tree(object):
        agb = str(agb_copy)
        mac = str(mac_copy)

        def readable(self, yes=True):
            os.chmod(self.mac, 0o600 if yes else 0o000)

        def run(self, args, env=None, stdin=None):
            environ = dict(os.environ)
            if env:
                environ.update(env)
            proc = subprocess.Popen(
                [sys.executable, "-S", "-E", self.agb] + list(args),
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, env=environ)
            out, err = conftest.communicate(proc, stdin)
            return proc.returncode, out, err

    yield Tree()
    os.chmod(str(mac_copy), 0o600)          # so tmp_path teardown can clean up


def seed_hot_path(agb, sd, host=HOST, pid=PID, starttime=STARTTIME):
    """Pre-seed `idx/` and `.state` so a hook invocation is a genuine no-change
    hot path. A first-ever hook is a transition, which touches more of the file
    and would make a weaker claim."""
    agb.ensure_session_dir(sd, host)
    anchor = agb.Anchor(host, "tmux", 1200000, "%24", pane="%24")
    key = agb.new_key()
    agb.link_idx(agb.idx_path(sd, anchor), key, pid, starttime)
    agb.write_in_place(agb.state_path(sd, key, host),
                       agb.format_state("active", host, pid, starttime, 1))
    agb.rebuild_marker(sd, host)
    return key


def hook_env(sd, host=HOST, pid=PID):
    return {"AGB_STATEDIR": sd, "AGB_HOST": host, "AGB_AGENT_PID": str(pid),
            "TMUX": "/tmp/tmux-100000/default,1200000,23", "TMUX_PANE": "%24"}


# ---------------------------------------------------------------------------
# the claim
# ---------------------------------------------------------------------------

def test_the_hook_never_opens_the_mac_side_file(agb, statedir, poisoned_tree,
                                                monkeypatch):
    """The task, in one assertion: an unreadable `agb_mac` does not disturb a
    hook in the slightest, on the hot path or on a transition."""
    sd = str(statedir)
    monkeypatch.setattr(agb, "proc_starttime", lambda pid: STARTTIME)
    key = seed_hot_path(agb, sd)
    env = hook_env(sd)

    rc, out, err = poisoned_tree.run(["hook", "active"], env=env)     # no change
    assert (rc, out, err) == (0, b"", b"")

    rc, out, err = poisoned_tree.run(["hook", "blocked"], env=env)    # transition
    assert (rc, out, err) == (0, b"", b"")

    # ...and it really did the work, rather than failing quietly into a
    # breadcrumb, which would satisfy "exit 0" while proving nothing.
    parsed = agb.parse_state(open(agb.state_path(sd, key, HOST), "rb").read())
    assert parsed["state"] == "blocked"
    assert "error" not in _err_log(agb, sd, key)


def test_the_poison_is_real(poisoned_tree):
    """Negative control. Without it the test above passes just as well against a
    hook that was never going to load anything -- which is to say, vacuously."""
    rc, out, err = poisoned_tree.run(["bridge", "--from-stdin"], stdin=b"")
    assert rc != 0
    assert b"agb_mac" in err
    assert os.strerror(errno.EACCES).encode() in err or b"denied" in err.lower()


def test_the_same_tree_works_once_the_file_is_readable(poisoned_tree):
    """...and the control is a control, not a broken copy: the identical tree
    runs the bridge end to end as soon as `agb_mac` can be read."""
    poisoned_tree.readable(True)
    rc, out, err = poisoned_tree.run(
        ["bridge", "--from-stdin"],
        stdin=b'{"t":"upsert","now":1.0,"session":{"key":"aaaa1111",'
              b'"state":"active"}}\n')
    assert rc == 0, err
    assert out.decode().splitlines() == ["upsert aaaa1111 active", "stale eof"]


def test_the_hooks_verbose_import_trace_never_mentions_the_mac_file(
        agb, statedir, agb_path, monkeypatch):
    """The second, independent probe: `-v` reports every module the interpreter
    imports, on stderr. It catches a lazy `import agb_mac` that a readability
    test could miss if the file were ever given a `.py` extension and picked up
    out of `sys.path[0]` (constraint #17)."""
    sd = str(statedir)
    monkeypatch.setattr(agb, "proc_starttime", lambda pid: STARTTIME)
    seed_hot_path(agb, sd)
    environ = dict(os.environ)
    environ.update(hook_env(sd))
    proc = subprocess.Popen([sys.executable, "-S", "-E", "-v", agb_path,
                             "hook", "active"],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, env=environ)
    out, err = conftest.communicate(proc, b"")
    assert (proc.returncode, out) == (0, b"")
    assert b"agb_mac" not in err
    # `-v` prints `import '<name>'` -- quoted -- for every real module import.
    # `_frozen_importlib` is in that stream unconditionally, so the loader's own
    # `import importlib.util` has to be matched in its quoted form.
    assert b"import 'importlib" not in err


def _err_log(agb, sd, key, host=HOST):
    path = agb.err_log_path(sd, key, host)
    if not os.path.exists(path):
        return ""
    with open(path) as handle:
        return handle.read()


# ---------------------------------------------------------------------------
# the loader
# ---------------------------------------------------------------------------

def test_the_loader_is_reached_only_from_the_mac_side_commands(agb_tree):
    """One door per Mac-side command, and no other caller.

    `cmd_close_done` (Task 4b) is the second: `close-done` is Mac-side too --
    it drives `agtermctl` against the locally persisted row map -- and it is
    deliberately not a `bridge` subcommand, which would start a second
    long-lived launchd-owned bridge.

    `cmd_forget_rows` is the third, and is here for the same reason as the
    second: it edits that same locally persisted map, which only `agb_mac`
    knows the format of. It is a command rather than a shell script precisely
    because the map ends in an `#end <count>` sentinel -- a hand-edited line
    leaves the count wrong, and the whole map then reads as corrupt, discarding
    the bindings that were meant to survive.

    All four are `agb` stubs; the implementations are in `agb_mac`, which no
    hook ever loads.
    """
    funcs = conftest.functions(agb_tree)
    callers = set(name for name, node in funcs.items()
                  if (None, "_load_mac") in conftest.calls(node))
    assert callers == set(["cmd_bridge", "cmd_close_done",
                           "cmd_forget_rows", "cmd_instances"])
    for name in callers:
        assert (None, "main") not in conftest.calls(funcs[name])
        assert (None, name) in conftest.calls(funcs["main"])


def test_no_hook_path_function_can_reach_the_loader(all_trees):
    """The structural mirror of the poisoned-tree test: nothing the hook calls,
    at any depth and across both files, reaches `_load_mac` -- so the runtime
    result is a property of the code rather than of one seeded statedir."""
    funcs = conftest.functions(*all_trees)
    reachable = conftest.reachable_from(funcs, "cmd_hook")
    assert "hook_apply" in reachable            # the walk really ran
    assert "_load_mac" not in reachable
    assert "run_bridge" not in reachable
    assert "run_close_done" not in reachable
    assert not reachable & set(conftest.functions(all_trees[1]))


def test_the_loader_is_idempotent_and_shares_one_module(agb, mac):
    """Two copies of `agb` would mean two `CONFIG_WARNINGS` lists, two caches
    and two versions of every rule -- the split's version of the writer and the
    sweeper disagreeing about the hostname."""
    assert agb._load_mac() is mac
    assert mac.agb is agb
    assert sys.modules["agb_mac"] is mac


def test_the_loader_finds_the_file_beside_agb(agb, repo_root):
    assert agb.mac_path() == os.path.join(repo_root, "agb_mac")
    assert os.path.exists(agb.mac_path())


def test_a_failed_load_is_not_cached_as_a_success(agb, monkeypatch):
    """A half-initialized module left in `sys.modules` would make the *second*
    attempt report a baffling AttributeError instead of the real failure."""
    monkeypatch.delitem(sys.modules, "agb_mac", raising=False)
    monkeypatch.setattr(agb, "mac_path", lambda: "/nonexistent/agb_mac")
    with pytest.raises(Exception):
        agb._load_mac()
    assert "agb_mac" not in sys.modules


def test_the_command_still_dispatches_through_the_hop(agb, monkeypatch):
    """`cmd_bridge` forwards argv unchanged and returns what the Mac side
    returns -- the hop is a hop, not a place where behaviour hides."""
    seen = []

    class FakeMac(object):
        def run_bridge(self, argv):
            seen.append(argv)
            return 7

    monkeypatch.setattr(agb, "_load_mac", FakeMac)
    assert agb.cmd_bridge(["--from-stdin", "--watchdog", "3"]) == 7
    assert seen == [["--from-stdin", "--watchdog", "3"]]


# ---------------------------------------------------------------------------
# consumed, never duplicated
# ---------------------------------------------------------------------------

# `default_statedir` was here and was deliberately removed: the Mac side must
# NOT derive a farm-side path from its own `$HOME`, so `bridge_settings` now
# requires an explicit statedir instead of defaulting to it. That leaves it
# consumed only inside `agb`, which makes it an internal helper rather than a
# shared primitive -- said out loud here, as this list's own docstring demands.
SHARED_PRIMITIVES = ("AgbError", "read_config", "config_path", "valid_mac_id",
                     "_json", "_select_readable", "_warn_once", "_stdin_fd",
                     "FEED_POLL_INTERVAL")


def test_the_shared_primitives_stay_in_agb(agb, mac):
    """The plan's second checkbox. Each of these has exactly one definition, in
    `agb`, and the Mac side reaches it through the module rather than owning a
    second copy that can drift."""
    for name in SHARED_PRIMITIVES:
        assert hasattr(agb, name), name
        assert name not in vars(mac), name


def test_the_mac_module_names_agb_for_every_shared_primitive(mac_source):
    """A cheap textual cross-check on the assertion above: each primitive is
    actually used from the Mac side, and every use is qualified.

    The point is that the list is not aspirational -- if one of these ever stops
    being consumed, it is a shared primitive with only one user and the split's
    boundary has moved without anyone saying so."""
    for name in SHARED_PRIMITIVES:
        assert ("agb." + name) in mac_source, name


def test_neither_file_defines_a_name_the_other_does(all_trees):
    """`conftest.functions()` raises on a non-dunder collision, which is what
    keeps the merged call graph used by every structural guard unambiguous: a
    shadowed name would silently drop half a guard."""
    merged = conftest.functions(*all_trees)
    assert "run_bridge" in merged and "cmd_hook" in merged


def test_the_mac_module_is_not_importable_as_a_module(repo_root):
    """Constraint #17 from the other direction: `agb_mac` has no `.py`
    extension, so even though `-S -E` leaves `sys.path[0]` in place, nothing can
    pick it up by name -- it is loaded by path or not at all."""
    proc = subprocess.Popen(
        [sys.executable, "-S", "-E", "-c", "import agb_mac"],
        cwd=repo_root, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    _out, err = conftest.communicate(proc)
    assert proc.returncode != 0
    assert b"ImportError" in err or b"ModuleNotFoundError" in err


# ---------------------------------------------------------------------------
# the file itself
# ---------------------------------------------------------------------------

def test_the_mac_file_has_no_shebang_and_is_not_executable(mac_source,
                                                           mac_path):
    """It is a library loaded by `agb`, never a command in its own right."""
    assert not mac_source.startswith("#!")
    assert not os.access(mac_path, os.X_OK)


# The tripwire behind the measurement, in bytes of `agb` source. `agb` has no
# `.py` extension and is run as a script, so CPython writes no `__pycache__`
# entry and **re-parses the whole file on every hook**. Measured on the farm box
# at 100 KB: interpreter floor 4.3 ms, `compile()` 3.8 ms, no-change hook
# 11.1 ms -- the top of the plan's ~8-11 ms band.
#
# ⚠️ Raised from 95_000 by Task 5, and the reason matters: the hook-side sweep
# is code the **hook itself runs**, so unlike Tasks 4a/4b it cannot be moved to
# `agb_mac`. The next breach must not be paid by bumping this number again. It
# must be paid the way Task 4c paid the last one -- by moving the rare-path
# commands (`doctor`, `prune`, `pane`, `status-line`, `install-hooks`, all of
# which a hook parses and never runs) behind a lazy loader of their own.
#
# ✅ **Task 6a did exactly that, and the number is deliberately unchanged.**
# `doctor` was written straight into a third sibling, `agb_ops`, which no hook
# loads: 39 KB of diagnostics that `agb` never carries. Measured on the farm
# box, `compile()` of `agb` alone is 3.8 ms and of `agb` + `agb_ops` 5.3 ms, so
# the split is worth **1.6 ms on every hook** -- the same trade Task 4c made,
# taken before the code was ever in the wrong file. All `agb` paid was the
# one-statement `cmd_doctor` hop.
#
# Headroom is now ~600 bytes, which is about one more hop. Tasks 6b-9a add four
# commands, so if they each want their own `cmd_*` stub the *hops* (not the
# implementations) are what to consolidate -- one shared door into `agb_ops`
# costs one dispatch line per command. Raising this number is still the wrong
# answer.
#
# The number itself lives in `conftest.py`, because six files assert against it.
# Written out six times, a change needing one more byte failed six tests in six
# files -- none of them about the change -- and the cheapest-looking fix was to
# bump six numbers, which is precisely what the paragraph above forbids. Now it
# fails here, next to the reasoning.
AGB_PARSE_BUDGET = conftest.AGB_PARSE_BUDGET


def test_the_mac_side_bulk_is_not_in_agb(agb_source, mac_source, agb_tree):
    """The measurement Task 4c exists for, expressed structurally: the hook
    re-parses `agb` on every invocation, so what matters is that the Mac-side
    bulk actually left it -- and stays gone.

    Two claims rather than one number, because the number alone drifts with
    every legitimately hook-side task. The doors into `agb_mac` must stay
    one-statement hops (a Mac-side function creeping back into `agb` fails
    here long before the byte count notices), and `agb` must stay under the
    parse budget above.
    """
    assert len(mac_source) > 15000

    funcs = conftest.functions(agb_tree)
    for name in ("cmd_bridge", "cmd_close_done"):
        body = [node for node in funcs[name].body
                if not isinstance(node, ast.Expr)]      # drop the docstring
        assert len(body) == 1, name
        assert isinstance(body[0], ast.Return), name

    assert len(agb_source) < AGB_PARSE_BUDGET
