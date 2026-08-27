"""Shared test seams.

Every fixture here exists because of a specific hazard, not for tidiness:

* ``agb`` -- the tool has **no ``.py`` extension** (it is invoked as
  ``<python> -S -E agb``), so it cannot be imported normally.
* ``mac`` -- Task 4c moved the Mac-side commands into a second file, ``agb_mac``,
  which the hook must never load. It is reached through ``agb``'s own lazy
  loader so the tests exercise the real path rather than a parallel one.
* ``ops`` -- Task 6a did the same for the operator/diagnostic commands
  (``doctor``, and later ``prune``/``pane``/``status-line``/``install-hooks``)
  in ``agb_ops``, for the same measured reason and through the same loader.
* ``fake_home`` -- autouse, because without it a bug in ``install-hooks`` would
  rewrite the developer's real ``~/.claude/settings.json``.
* ``set_host`` / ``set_agent_pid`` -- ``monkeypatch`` cannot cross a subprocess
  boundary, so host and agent-pid overrides have to be environment variables.
* ``stub_bin`` -- ``agtermctl`` and ``ssh`` do not exist on the farm box, so the
  Mac-side logic is only testable against recording stubs on ``PATH``.

The AST helpers below span **all three** files (``all_trees``). A structural
guard that stopped at a file boundary when Task 4c split the tool -- or when
Task 6a split it again -- would still look green while covering a fraction of
the code it was written for, which is worse than not having it.
"""

import ast
import importlib.util
import os
import subprocess
import sys
from importlib.machinery import SourceFileLoader

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AGB_PATH = os.path.join(REPO_ROOT, "agb")
MAC_PATH = os.path.join(REPO_ROOT, "agb_mac")
OPS_PATH = os.path.join(REPO_ROOT, "agb_ops")

# Every invocation of agb as a subprocess must use the same flags the installed
# hook uses, or the tests validate a configuration nobody runs.
AGB_ARGV = [sys.executable, "-S", "-E", AGB_PATH]

# The ceiling on `agb`'s source size, in characters. It lives here rather than
# in one test file because six files assert against it: a change that
# legitimately needs a byte or two must fail on the ONE test that is about the
# size of `agb` (`test_mac_split.test_the_mac_side_bulk_is_not_in_agb`, where
# the reasoning is), not on six unrelated ones whose cheapest fix looks like
# bumping six numbers -- which is exactly the move that comment forbids.
#
# The number itself is not arbitrary and RAISING IT IS ALMOST ALWAYS THE WRONG
# ANSWER: `agb` has no `.py` extension and is run as a script, so CPython caches
# no bytecode for it and the whole file is re-parsed on every hook. Six tasks
# during the original build declined to raise it and paid for new code by moving
# other code into the lazily loaded siblings instead. See test_mac_split.py.
#
# It has been raised twice deliberately:
#   +126 chars (measured): the statedir default became `default_statedir()`
#     where it had been a hardcoded constant. +0.015 ms on a 5.9 ms baseline.
#   +716 chars (measured): `cmd_instances` with its try/except and mandatory
#     reason-comment, plus the USAGE line and the dispatch arm. `OPS_COMMANDS`
#     was the rejected route: it avoided a dispatch arm but forced `agb_mac` ->
#     `agb_ops`, an edge that does not exist and is asserted absent. +0 ms (3.9
#     ms post-change vs 5.9 ms prior -- different machine, not a real delta).
#     Size: 103135 chars. Headroom at 103200: 65 chars.
#
# ⚠️ AND 63 OF THOSE 65 ARE ALREADY SPENT -- `agb` was 103198, headroom **2**.
# Swapping `agb-refresh`'s reader for `agb instances` found `cmd_instances`
# catching `(ImportError, AttributeError)` where a tree with no `agb_mac` raises
# `FileNotFoundError`, so the catch had to widen and the reason had to be
# written down. It was NOT paid for by raising this again: the code delta is
# ~10 chars and the rest was prose, which moved into
# `agb_mac.run_instances`' docstring -- the sibling is not capped, and "pay for
# new code by moving other code out" applies to comments too.
#
#   +2100 chars (measured), the third raise: `real_host()`/`host_is_observed()`
#     and the two gates that stop a remotely launched agent (`{env}`) reaping
#     the host it impersonates. This one could NOT be paid the usual way and
#     could not go in a sibling: `maybe_sweep` is on the transition path, and
#     the hook must never load `agb_mac`/`agb_ops` (invariant, test_mac_split).
#     Roughly 1400 of it is the reason -- why the override is an assertion, why
#     the old guard could not see it, why the opt-in points the way it does --
#     and the full trace moved to `docs/design.md` rather than living here.
#     Hot path unchanged: `real_host()` is reached only from `maybe_sweep` and
#     `_require_own_host`, never on the no-change path. 5.0 ms hook, against
#     4.9 ms before -- inside the noise on this box.
#     Size: 105269 chars. Headroom at 105300: 30 chars.
AGB_PARSE_BUDGET = 105300

# Nothing here may block forever. A regression that makes a subprocess stop
# answering must fail the run, not hang it: an unbounded `communicate()` turns
# one broken EOF path into a suite that never finishes and reports nothing.
SUBPROCESS_TIMEOUT = 30


def communicate(proc, stdin=None, timeout=SUBPROCESS_TIMEOUT):
    """`proc.communicate()` that can never hang, and always reaps the child.

    On timeout the child is killed and drained before the error propagates, so
    a hung subprocess cannot outlive the test that started it.
    """
    try:
        return proc.communicate(stdin, timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, err = proc.communicate()
        raise AssertionError(
            "subprocess did not exit within %ss: %r\nstdout=%r\nstderr=%r"
            % (timeout, getattr(proc, "args", None), out, err))


@pytest.fixture(scope="session")
def repo_root():
    return REPO_ROOT


@pytest.fixture(scope="session")
def agb_path():
    return AGB_PATH


@pytest.fixture(scope="session")
def mac_path():
    return MAC_PATH


@pytest.fixture(scope="session")
def ops_path():
    return OPS_PATH


@pytest.fixture(scope="session")
def agb():
    """Import `agb` despite it having no `.py` extension."""
    loader = SourceFileLoader("agb", AGB_PATH)
    spec = importlib.util.spec_from_file_location("agb", AGB_PATH, loader=loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules["agb"] = module
    loader.exec_module(module)
    return module


# ⚠️ The key is a CROSS-FILE AGREEMENT with `agb-peer-setup`'s PEER_MODULE
# (CLAUDE.md invariant 14). If the two disagree, `load_peer` builds a SECOND
# module object and `setup.PeerError is peer.PeerError` fails -- which reads
# like a loader bug rather than a naming disagreement.
PEER_MODULE = "agb_peer"
PEER_PATH = os.path.join(REPO_ROOT, "agb-peer")
SETUP_PATH = os.path.join(REPO_ROOT, "agb-peer-setup")
# ⚠️ MAY NOT EXIST YET -- `agb-dashboard` is created by a later task of the
# agb-dashboard plan. It is named here so the guards that must span both cell
# emitters have one spelling of the path; a guard reading it is responsible for
# skipping a tree that is absent and for asserting it still covered something.
DASH_PATH = os.path.join(REPO_ROOT, "agb-dashboard")


@pytest.fixture(scope="session")
def peer():
    """`agb-peer` as a module, REGISTERED under the shared key.

    ⚠️ The registration is the point, and this fixture did not always do it.
    `agb-peer-setup.load_peer` returns `sys.modules[PEER_MODULE]` when it is
    there; without this line it loads its own copy, and the two modules have
    different `PeerError` classes -- so `except peer.PeerError` around a call
    into the setup script does not catch. Same shape as the `agb` fixture
    above, for the same reason.
    """
    module = sys.modules.get(PEER_MODULE)
    if module is not None:
        return module
    loader = SourceFileLoader(PEER_MODULE, PEER_PATH)
    spec = importlib.util.spec_from_file_location(PEER_MODULE, PEER_PATH,
                                                  loader=loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules[PEER_MODULE] = module
    loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def setup(peer):
    """`agb-peer-setup`, loaded after `peer` so it adopts that module object.

    Depending on `peer` is not decoration: it forces the shared registration to
    happen first, which is exactly the property the identity test asserts.
    """
    loader = SourceFileLoader("agb_peer_setup", SETUP_PATH)
    spec = importlib.util.spec_from_file_location("agb_peer_setup", SETUP_PATH,
                                                  loader=loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def mac(agb):
    """The Mac-side module, loaded through `agb._load_mac()`.

    Deliberately not loaded independently: the loader is itself part of what
    Task 4c has to get right (one `agb` module object shared by both halves, not
    two copies with separate state), so every Mac-side test exercises it.
    """
    return agb._load_mac()


@pytest.fixture(scope="session")
def ops(agb):
    """The operator-side module, loaded through `agb._load_ops()`.

    Same reasoning as `mac`: the loader is part of what Task 6a has to get
    right, so every `doctor` test exercises the real door rather than a
    parallel import that would keep passing if the door broke.
    """
    return agb._load_ops()


@pytest.fixture(scope="session")
def agb_source():
    with open(AGB_PATH) as handle:
        return handle.read()


@pytest.fixture(scope="session")
def mac_source():
    with open(MAC_PATH) as handle:
        return handle.read()


@pytest.fixture(scope="session")
def ops_source():
    with open(OPS_PATH) as handle:
        return handle.read()


@pytest.fixture(scope="session")
def agb_tree(agb_source):
    """The parsed source.

    Several constraints in the plan ("no argparse", "json only inside the
    transition branch", "one hostname source") are structural claims about the
    code. Grepping the text asserts them against comments and docstrings too,
    which makes the guards pass for the wrong reason -- so they are checked on
    the AST.
    """
    return ast.parse(agb_source, filename=AGB_PATH)


@pytest.fixture(scope="session")
def mac_tree(mac_source):
    """The parsed Mac-side source, for the guards that span every file."""
    return ast.parse(mac_source, filename=MAC_PATH)


@pytest.fixture(scope="session")
def ops_tree(ops_source):
    """The parsed operator-side source (Task 6a)."""
    return ast.parse(ops_source, filename=OPS_PATH)


@pytest.fixture(scope="session")
def all_trees(agb_tree, mac_tree, ops_tree):
    """Every parsed file, in load order.

    Named for what it is rather than for how many files there happen to be
    today: Task 4c made it two and Task 6a made it three, and each time a
    guard that quietly kept covering only the first file would have been worse
    than no guard at all.
    """
    return (agb_tree, mac_tree, ops_tree)


def toplevel_imports(tree):
    """Module names imported at module scope."""
    names = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.add(node.module.split(".")[0])
    return names


def all_imports(*trees):
    """Module names imported anywhere in `trees`, at any nesting depth."""
    names = set()
    for tree in trees:
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    names.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    names.add(node.module.split(".")[0])
    return names


def _named_functions(tree):
    """({name: FunctionDef}, {names defined at module or class scope}).

    The second set is what a cross-file collision check may use: a closure
    called `warn` inside `feed_loop` and another inside `run_bridge` are two
    private locals, not a duplicated helper.
    """
    found = {}
    top = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            found[node.name] = node
        if isinstance(node, (ast.Module, ast.ClassDef)):
            for child in node.body:
                if isinstance(child, ast.FunctionDef):
                    top.add(child.name)
    return found, top


def functions(*trees):
    """{name: FunctionDef} for every function in `trees` -- every file.

    Task 4c split `agb` in two and Task 6a split it again, and every guard
    phrased as "the only place that calls X" or "the only function that imports
    Y" has to span the set: one that stopped at a boundary would keep passing
    while the code it was written for moved out from under it.

    A *named* helper defined in **more than one** file is an error rather than a
    silent shadow -- that is exactly the duplication the split forbids (the
    sibling files consume the shared primitives, they do not copy them). Two
    exemptions, both because the name cannot be reached from outside its own
    function anyway: dunders (every class has an `__init__`, and `ast.walk`
    already collides those within one file) and nested closures such as the
    `warn` that both `feed_loop` and `run_bridge` define locally.
    """
    found = {}
    claimed = set()
    for tree in trees:
        seen, top = _named_functions(tree)
        for name, node in seen.items():
            if (name in claimed and name in top and not name.startswith("__")):
                raise AssertionError(
                    "%s is defined in more than one file: the sibling modules "
                    "must consume agb's helpers, never duplicate them" % (name,))
            found[name] = node
        claimed |= top
    return found


def calls(node):
    """[(base, attr)] for every call under `node`; base is None for bare names."""
    found = []
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        func = child.func
        if isinstance(func, ast.Attribute):
            base = func.value.id if isinstance(func.value, ast.Name) else None
            found.append((base, func.attr))
        elif isinstance(func, ast.Name):
            found.append((None, func.id))
    return found


def usage_commands(agb):
    """The command names `agb` actually offers, read off `USAGE`.

    `USAGE` is the declared surface because it is the copy a user is shown.
    There used to be a `COMMANDS` tuple beside it holding the same eleven names
    for a `not implemented yet` branch in `main` that nothing could reach --
    every name being routed above it -- so it was a second list to keep in step
    with this one and no way to notice when it drifted.
    """
    names = []
    for line in agb.USAGE.splitlines():
        if line.startswith("  ") and line.strip():
            names.append(line.split()[0])
    return names


CROSS_MODULE_BASES = ("agb",)


def reachable_from(tree_or_funcs, root, cross=CROSS_MODULE_BASES):
    """The call graph under `root`, across every file.

    Several guards are about what a *command* can reach rather than about one
    function: "the hook never reaches json", "the poll never lists a foreign
    directory", "the bridge never touches the statedir". They all need the same
    walk, so it lives here rather than being re-derived per test file.

    Two kinds of edge are followed, and the second is what keeps those guards
    honest after Task 4c:

    * a bare-name call, `foo(...)` -- including `_load_mac().run_bridge(argv)`,
      whose attribute base is a call rather than a name, so it reads as
      `run_bridge` and carries the walk into `agb_mac` (and the same for
      `_load_ops().run_doctor(argv)` into `agb_ops`);
    * `agb.foo(...)`, which is how the sibling modules reach back into the
      shared primitives. Without this edge a guard such as "nothing reachable
      from `cmd_bridge` touches the statedir" would be trivially satisfiable by
      writing `agb.statedir()` -- passing while doing the forbidden thing.
    """
    funcs = (tree_or_funcs if isinstance(tree_or_funcs, dict)
             else functions(tree_or_funcs))
    seen = set([root])
    frontier = [root]
    while frontier:
        node = funcs.get(frontier.pop())
        if node is None:
            continue
        for base, attr in calls(node):
            if base is not None and base not in cross:
                continue
            if attr in funcs and attr not in seen:
                seen.add(attr)
                frontier.append(attr)
    return seen


@pytest.fixture(autouse=True)
def fake_home(tmp_path, monkeypatch):
    """Point $HOME at a scratch dir and clear every ambient identity variable.

    Autouse for two reasons: a test that forgot it would read (or write) the
    real dotfiles, and `$TMUX`/`$TMUX_PANE` are *inherited* -- running the suite
    from inside tmux would otherwise give a different session anchor than
    running it outside, which is the worst kind of test flake.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for name in ("AGB_STATEDIR", "AGB_HOST", "AGB_HOST_LOCAL", "AGB_AGENT_PID",
                 "TMUX", "TMUX_PANE"):
        monkeypatch.delenv(name, raising=False)
    return home


@pytest.fixture
def set_tmux(monkeypatch):
    """Set (or clear) the tmux variables the session anchor derives from."""
    def apply(tmux=None, pane=None):
        for name, value in (("TMUX", tmux), ("TMUX_PANE", pane)):
            if value is None:
                monkeypatch.delenv(name, raising=False)
            else:
                monkeypatch.setenv(name, value)
    return apply


def proc_stat_line(pid, comm, ppid, starttime):
    """A `/proc/<pid>/stat` line whose field 22 is `starttime`.

    Field 3 is the first one after the comm's closing paren, so field 22 sits at
    index 19 -- the arithmetic the parser has to get right, spelled out here so
    the fixture and the parser cannot drift together.
    """
    fields = ["S", str(ppid)] + ["0"] * 17 + [str(starttime)] + ["0"] * 10
    return "%d (%s) %s\n" % (pid, comm, " ".join(fields))


def self_starttime(pid=None):
    """`/proc/<pid>/stat` field 22 for a **real** process (default: this one).

    Deliberately not `agb.proc_starttime`: the liveness tests are the ones that
    decide whether a file gets deleted, so the value they compare against must
    come from somewhere other than the code under test. Parsed from the last
    `)`, because field 2 is an unescaped comm.
    """
    if pid is None:
        pid = os.getpid()
    with open("/proc/%d/stat" % (pid,)) as handle:
        rest = handle.read().rpartition(")")[2]
    return int(rest.split()[19])


def live_agent():
    """(pid, starttime) for a process that provably exists -- this one.

    Every test that writes a session which is *supposed* to survive must use
    this. A made-up pid is overwhelmingly likely to be dead, which the feed's
    sweep would (correctly) act on -- so a fabricated pid silently turns a test
    about the wire protocol into a test about reaping.
    """
    return (os.getpid(), self_starttime())


def dead_agent():
    """(pid, starttime) for a process that provably does not exist.

    Forked, exited and reaped, so the kernel has positively released the pid.
    Verified with `kill(pid, 0)` before it is handed out: a pid recycled between
    the wait and the assertion would make the test pass or fail for a reason
    that has nothing to do with the code.
    """
    for _attempt in range(20):
        pid = os.fork()
        if pid == 0:                      # no atexit, no pytest teardown
            os._exit(0)
        os.waitpid(pid, 0)
        try:
            os.kill(pid, 0)
        except OSError:
            return (pid, 9182736)
    raise AssertionError("could not obtain a provably dead pid")


@pytest.fixture
def fake_proc(tmp_path, monkeypatch, agb):
    """Build a fake `/proc` tree and point agb at it.

    The agent-pid walk is untestable against the real one: pytest may itself be
    running under the `claude`/`node` process the walk looks for, so the answer
    would depend on how the suite was launched.

    `procs` maps pid -> {comm, ppid, starttime, cmdline}; `me` names the pid
    that `/proc/self` points at.
    """
    root = tmp_path / "proc"

    def build(procs, me=None):
        if not root.exists():
            root.mkdir()
        for pid, spec in procs.items():
            comm = spec.get("comm", "python3")
            ppid = spec.get("ppid", 1)
            starttime = spec.get("starttime", 0)
            cmdline = spec.get("cmdline", [comm])
            entry = root / str(pid)
            if not entry.exists():
                entry.mkdir()
            with open(str(entry / "status"), "w") as handle:
                handle.write("Name:\t%s\nPPid:\t%d\n" % (comm, ppid))
            with open(str(entry / "comm"), "w") as handle:
                handle.write(comm + "\n")
            with open(str(entry / "cmdline"), "w") as handle:
                handle.write("\0".join(cmdline) + "\0")
            with open(str(entry / "stat"), "w") as handle:
                handle.write(proc_stat_line(pid, comm, ppid, starttime))
        if me is not None:
            link = root / "self"
            if link.is_symlink() or link.exists():
                os.unlink(str(link))
            os.symlink(str(root / str(me)), str(link))
        monkeypatch.setattr(agb, "PROC", str(root))
        return root

    build.root = root
    return build


@pytest.fixture
def config_file(fake_home):
    """Write `~/.config/agbridge/config`; returns the path."""
    path = fake_home / ".config" / "agbridge" / "config"

    def write(text):
        os.makedirs(str(path.parent), exist_ok=True)
        with open(str(path), "w") as handle:
            handle.write(text)
        return path

    write.path = path
    return write


@pytest.fixture
def instance_config(fake_home):
    """Write a config for a *named* instance, or for the default one.

    The layout `install.sh mac --instance <name>` produces:
    `~/.config/agbridge/<name>/config`, with `name=None` giving
    `~/.config/agbridge/config` -- which under `fake_home` is exactly
    `agb.config_path()`, so both halves of a two-instance comparison come from
    one helper and the default half is the real default path rather than a
    look-alike.

    Returns a `str`, because that is what a `--config` value is everywhere it
    is passed: an argv word.

    Three test modules span the same shape (which instance's rows, which
    instance's `host_<name>` table, which instance's map a row command
    resolves through) and had three copies of it, two byte-identical.
    """
    def write(name=None, text=""):
        base = fake_home / ".config" / "agbridge"
        if name:
            base = base / name
        os.makedirs(str(base), exist_ok=True)
        path = base / "config"
        with open(str(path), "w") as handle:
            handle.write(text)
        return str(path)

    return write


@pytest.fixture
def statedir_path(tmp_path, monkeypatch):
    """A statedir location exported via $AGB_STATEDIR. **Not** created."""
    path = tmp_path / "state"
    monkeypatch.setenv("AGB_STATEDIR", str(path))
    return path


@pytest.fixture
def statedir(agb, statedir_path):
    """A created, validated statedir with its subdirectories."""
    agb.ensure_statedir(str(statedir_path))
    return statedir_path


@pytest.fixture
def set_host(monkeypatch):
    """Override own_host(), including across a subprocess boundary.

    `AGB_HOST_LOCAL` comes with it: an overridden host is unadjudicable by
    default (`agb.host_is_observed`), and the suite's whole model of "another
    machine" is a different `host` written by *this* process, whose forked
    agents really are local pids. Without the opt-in every sweep test would
    silently stop sweeping and pass by proving nothing.

    ⚠️ A test *about* the guard must set `AGB_HOST` on its own rather than take
    this fixture, or it opts straight out of the thing it is checking.
    """
    def apply(name):
        monkeypatch.setenv("AGB_HOST", name)
        monkeypatch.setenv("AGB_HOST_LOCAL", "1")
        return name
    return apply


@pytest.fixture
def set_agent_pid(monkeypatch):
    """Override agent-pid resolution; the sweep regression tests need it."""
    def apply(pid):
        monkeypatch.setenv("AGB_AGENT_PID", str(pid))
        return pid
    return apply


class StubBin(object):
    """A directory prepended to $PATH holding recording stubs."""

    UNIT = "\x1f"

    def __init__(self, path):
        self.path = path

    def install(self, name, body=None, exit_code=0):
        """Install a stub named `name`; returns the path of its call log."""
        script = self.path / name
        log = self.path / (name + ".log")
        if body is None:
            body = (
                "#!/bin/sh\n"
                "{ for a in \"$@\"; do printf '%s\\037' \"$a\"; done; "
                "printf '\\n'; } >> \"" + str(log) + "\"\n"
                "exit " + str(exit_code) + "\n"
            )
        with open(str(script), "w") as handle:
            handle.write(body)
        os.chmod(str(script), 0o755)
        return log

    def calls(self, name):
        """Return the recorded invocations as a list of argv lists."""
        log = self.path / (name + ".log")
        if not log.exists():
            return []
        with open(str(log)) as handle:
            raw = handle.read()
        out = []
        for line in raw.splitlines():
            if not line:
                out.append([])
                continue
            args = line.split(self.UNIT)
            if args and args[-1] == "":
                args.pop()
            out.append(args)
        return out


@pytest.fixture
def stub_bin(tmp_path, monkeypatch):
    path = tmp_path / "bin"
    path.mkdir()
    monkeypatch.setenv("PATH", str(path) + os.pathsep + os.environ.get("PATH", ""))
    return StubBin(path)


@pytest.fixture
def agtermctl(stub_bin, monkeypatch, repo_root):
    """`tests/stubs/agtermctl` on $PATH, recording into a StubBin log.

    Here rather than in `test_bridge_rows.py` because two files need it: the
    row-rendering tests, and the end-to-end one that runs a real `agb feed` into
    a real `agb bridge` and has to watch what actually reaches agterm.
    """
    with open(os.path.join(repo_root, "tests", "stubs", "agtermctl")) as handle:
        body = handle.read()
    log = stub_bin.install("agtermctl", body=body)
    monkeypatch.setenv("AGB_AGTERMCTL_LOG", str(log))

    class Stub(object):
        def calls(self):
            return stub_bin.calls("agtermctl")

        def verbs(self):
            return [call[1] for call in self.calls() if len(call) > 1]

        def fail(self, verb):
            monkeypatch.setenv("AGB_AGTERMCTL_FAIL", verb)

        def force_id(self, value):
            monkeypatch.setenv("AGB_AGTERMCTL_ID", value)

    return Stub()


@pytest.fixture
def run_agb():
    """Run agb in a subprocess exactly as the installed hook does.

    Bounded: `agb pane` reads stdin, and a regression in its EOF arm would spin
    here forever rather than fail. See `communicate()` above.
    """
    def run(args, env=None, stdin=None, timeout=SUBPROCESS_TIMEOUT):
        environ = dict(os.environ)
        if env:
            environ.update(env)
        proc = subprocess.Popen(
            AGB_ARGV + list(args),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environ,
        )
        out, err = communicate(proc, stdin, timeout)
        return proc.returncode, out, err
    return run
