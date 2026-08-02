"""Task 1 -- statedir, file primitives, dispatch skeleton."""

import ast
import errno
import os
import socket
import sys
import threading

import pytest

import conftest


# ---------------------------------------------------------------------------
# dispatch lives behind `if __name__ == "__main__"`
# ---------------------------------------------------------------------------

def test_importing_agb_does_not_execute_a_command(agb_path, tmp_path):
    """Importing must not dispatch -- every test seam depends on this."""
    import subprocess
    probe = (
        "import sys\n"
        "from importlib.machinery import SourceFileLoader\n"
        "sys.argv = ['agb', 'doctor', '--nonsense']\n"
        "m = SourceFileLoader('agb', %r).load_module()\n"
        "assert m.__name__ == 'agb'\n"
        % (agb_path,)
    )
    proc = subprocess.Popen(
        [sys.executable, "-S", "-E", "-c", probe],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, err = conftest.communicate(proc)
    assert proc.returncode == 0, err
    assert out == b""


def test_no_args_prints_usage_to_stderr(run_agb):
    rc, out, err = run_agb([])
    assert rc == 2
    assert out == b""
    assert b"usage:" in err


def test_version_command(run_agb, agb):
    rc, out, err = run_agb(["version"])
    assert rc == 0
    assert out.decode().strip() == "agb " + agb.VERSION


def test_unknown_command_is_rejected(run_agb):
    rc, out, err = run_agb(["frobnicate"])
    assert rc == 2
    assert out == b""
    assert b"unknown command" in err


def test_known_but_unimplemented_command_is_explicit(agb, capsys):
    """⚠️ **Reworked by Task 9a**, and the claim -- a *known* command that is
    not built yet says so, rather than reading as a typo -- is unchanged.

    It was amended four times before this and each time only its example moved:
    `doctor` -> `prune` -> `pane` -> `status-line` -> `install-hooks`. Task 9a
    builds the last name in `OPS_COMMANDS`, so there is no unbuilt command left
    to point it at, and moving it a fifth time is not available.

    Deleting it is the wrong answer too: the code path it guards is still there,
    and the day someone adds a sixth name to the table is exactly the day it
    matters. So it is aimed at the **answer** instead of at an example, by
    driving the real door with a name no release implements -- which is the
    precise shape a new `OPS_COMMANDS` entry has on the day it is added. The
    companion assertion, that no name in the table can currently reach this
    answer, lives in `tests/test_install_hooks.py`.

    `agb.cmd_ops` rather than a subprocess because the name cannot be reached
    through `main` at all -- `main` only routes what is in `OPS_COMMANDS` -- and
    `run_agb` would therefore be testing the exit-2 "unknown command" branch,
    which `test_unknown_command_is_rejected` above already owns. The two answers
    are deliberately different: exit 3 with a reason for a name the tool knows,
    exit 2 with the usage for one it does not.
    """
    assert agb.cmd_ops("future-command", []) == 3
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "not implemented" in captured.err
    assert "future-command" in captured.err


def test_agb_has_no_shebang(agb_source, agb_path):
    """Constraint #1: hooks run as `<abs-python> -S -E agb hook <state>`.

    A shebang invites `./agb`, which cannot pass `-S -E` (`env` does not forward
    interpreter flags) and reintroduces a bare `python3` lookup -- the exact
    failure that kills a hook before any breadcrumb is written.
    """
    assert not agb_source.startswith("#!")
    assert not os.access(agb_path, os.X_OK)


def test_dispatch_uses_no_argparse(all_trees):
    """Constraint #3: argparse must never reach the hot path.

    Both files, because `agb_mac`'s `parse_bridge_args` is exactly the kind of
    parser that invites it, and importing argparse there would put it one
    `_load_mac()` away rather than out of reach."""
    assert "argparse" not in conftest.all_imports(*all_trees)


def test_json_is_not_imported_at_module_top(agb_tree):
    """Constraint #2 -- the module-level guard; Task 2b adds the runtime one."""
    assert "json" not in conftest.toplevel_imports(agb_tree)


def test_module_top_imports_only_cheap_modules(agb_tree):
    """The hot-path budget in one assertion.

    Measured with `-S -E`: `pass` 4.1 ms, `import os,sys,errno` 5.6 ms,
    `import os,sys,errno,time` 5.6 ms, `import socket` 9.6 ms, `import json`
    8.9 ms. Anything added here is paid on every single `PostToolUse`, hundreds
    of times per session.

    `time` is free because it is compiled into the interpreter -- pinned by the
    assertion below rather than by the measurement above, which drifts.
    """
    assert conftest.toplevel_imports(agb_tree) == set(
        ["errno", "os", "sys", "time"])


def test_the_added_imports_are_builtin_modules(agb_tree):
    """Why `errno` and `time` are affordable and `json`/`socket` are not: a
    builtin module is compiled into the interpreter and costs no filesystem
    access at all, which is what dominates under `-S` on an NFS $HOME. (`os` is
    a real .py file and is unavoidable.)"""
    for name in conftest.toplevel_imports(agb_tree) - set(["os"]):
        assert name in sys.builtin_module_names, name
    assert "json" not in sys.builtin_module_names


# ---------------------------------------------------------------------------
# own_host()
# ---------------------------------------------------------------------------

def test_own_host_strips_the_domain(agb, monkeypatch):
    monkeypatch.setattr(os, "uname", lambda: (
        "Linux", "worker01.cluster.example.com", "", "", ""))
    assert agb.own_host() == "worker01"


def test_own_host_passes_through_a_short_name(agb, monkeypatch):
    monkeypatch.setattr(os, "uname", lambda: ("Linux", "box2", "", "", ""))
    assert agb.own_host() == "box2"


def test_own_host_honours_the_env_override(agb, set_host, monkeypatch):
    monkeypatch.setattr(os, "uname", lambda: ("Linux", "real.example.com", "", "", ""))
    set_host("machine3")
    assert agb.own_host() == "machine3"


def test_own_host_override_is_also_normalized(agb, set_host):
    set_host("worker03.cluster.example.com")
    assert agb.own_host() == "worker03"


def test_own_host_matches_socket_gethostname(agb):
    """Pins the ~5.5 ms optimization: own_host() reads os.uname()[1] instead of
    importing socket, which is only legitimate while the two agree."""
    assert agb.own_host() == socket.gethostname().split(".")[0]


def test_own_host_is_the_only_hostname_source(all_trees):
    """Constraint #13: one helper. A second gethostname() call site is how a
    writer and a sweeper come to disagree, silently -- and entries written under
    one name and swept under another are simply never swept.

    Across both files: a second source in `agb_mac` would be just as silent."""
    holders = set()
    total = 0
    for name, node in conftest.functions(*all_trees).items():
        for base, attr in conftest.calls(node):
            if attr in ("uname", "gethostname"):
                holders.add(name)
                total += 1
    assert holders == set(["own_host"])
    assert total == 2  # os.uname() plus the non-POSIX socket fallback


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------

def test_parse_config_reads_the_documented_keys(agb, ops):
    """The documented key list lives in `agb_ops` beside `known_config_key`, its
    only reader: `agb.parse_config` is generic, and every byte in `agb` is
    re-parsed on every hook."""
    values, malformed = agb.parse_config(
        "# a comment\n"
        "\n"
        "statedir = /shared/.agbridge\n"
        "mac_id = m-1234\n"
        "feed_host = vncbox\n"
        "agb_remote_path = /opt/agbridge/agb\n"
        "remote_python = /bin/python3\n"
        "jump_host = vncbox\n"
        "workspace = agents\n"
        "notify_on_blocked = 1\n"
        "notify_on_new_row = 1\n"
        "notify_on_completed_after = 300\n"
        "row_fields = label,cwd:base,pane\n"
        "host_machine3 = m3.example.com\n"
    )
    assert malformed == []
    for key in ops.CONFIG_KEYS:
        assert key in values
    assert values["host_machine3"] == "m3.example.com"
    assert values["statedir"] == "/shared/.agbridge"


def test_the_documented_key_list_is_pinned_by_name(ops):
    """⚠️ The two tests either side of this one both iterate `CONFIG_KEYS` and
    assert things about its members, so **dropping a key makes them weaker
    rather than red**. That is not hypothetical: `notify_on_blocked` and
    `notify_on_new_row` were documented in four places and missing from this
    list for two releases, and `agb doctor` called them typos the whole time
    while every test stayed green.

    Spelled out by hand for that reason. A key removed here is a key `agb
    doctor` starts warning about, which is user-visible, so it should cost a
    red test rather than a silent downgrade.
    """
    assert set(ops.CONFIG_KEYS) == {
        "statedir", "mac_id", "feed_host", "agb_remote_path", "remote_python",
        "jump_host", "workspace",
        "notify_on_blocked", "notify_on_new_row", "notify_on_completed_after",
        "row_fields",
    }
    assert ops.CONFIG_KEY_PREFIXES == ("host_",)


def test_parse_config_collects_malformed_lines_without_raising(agb):
    values, malformed = agb.parse_config(
        "mac_id = ok\n"
        "this line has no equals sign\n"
        " = novalue\n"
        "feed_host = vncbox\n"
    )
    assert values == {"mac_id": "ok", "feed_host": "vncbox"}
    assert [lineno for lineno, _raw in malformed] == [2, 3]


def test_parse_config_keeps_equals_signs_in_values(agb):
    values, malformed = agb.parse_config("agb_remote_path = /x/y=z\n")
    assert malformed == []
    assert values["agb_remote_path"] == "/x/y=z"


def test_known_config_key_covers_the_documented_list(ops):
    for key in ops.CONFIG_KEYS:
        assert ops.known_config_key(key)
    assert ops.known_config_key("host_machine3")
    assert not ops.known_config_key("host_")
    assert not ops.known_config_key("feed_hsot")


def test_read_config_missing_file_is_not_an_error(agb):
    assert agb.read_config() == {}


def test_read_config_records_warnings(agb, config_file):
    """The malformed lines go into the caller's own list.

    They used to accumulate in a module global that every reader shared and
    every reader had to remember to clear. `parse_config` already returns them,
    so the global was a second copy of an answer the function had in hand -- and
    one whose staleness nothing could see.
    """
    config_file("mac_id = m-1\nbroken line\n")
    warnings = []
    values = agb.read_config(None, warnings)
    assert values == {"mac_id": "m-1"}
    assert len(warnings) == 1
    assert warnings[0][1].strip() == "broken line"

    # A second reader's list is its own: a clean file leaves it empty rather
    # than inheriting the answer above.
    config_file("mac_id = m-1\n")
    second = []
    agb.read_config(None, second)
    assert second == []
    assert len(warnings) == 1                  # and the first is not disturbed


def test_read_config_wants_no_list_at_all_by_default(agb, config_file):
    """Every caller but `doctor` -- including `statedir()` on the hook path --
    ignores the malformed lines, and must not have to pass anything to do so."""
    config_file("statedir = /tmp/x\nbroken line\n")
    assert agb.read_config() == {"statedir": "/tmp/x"}


def test_a_missing_config_leaves_the_warning_list_alone(agb, tmp_path):
    """ENOENT is not a parse failure. Appending to the list here -- or clearing
    a list the caller had already put something in -- would both be wrong."""
    warnings = ["untouched"]
    assert agb.read_config(str(tmp_path / "nope"), warnings) == {}
    assert warnings == ["untouched"]


# ---------------------------------------------------------------------------
# statedir resolution
# ---------------------------------------------------------------------------

def test_statedir_prefers_the_environment(agb, config_file, monkeypatch, tmp_path):
    config_file("statedir = /from/config\n")
    monkeypatch.setenv("AGB_STATEDIR", str(tmp_path / "from-env"))
    assert agb.statedir() == str(tmp_path / "from-env")


def test_statedir_falls_back_to_config(agb, config_file):
    config_file("statedir = /from/config\n")
    assert agb.statedir() == "/from/config"


def test_statedir_falls_back_to_the_default(agb):
    assert agb.statedir() == agb.default_statedir()


def test_statedir_expands_a_tilde_from_config(agb, config_file, fake_home):
    config_file("statedir = ~/agb-state\n")
    assert agb.statedir() == os.path.join(str(fake_home), "agb-state")


def test_config_is_not_read_when_the_environment_is_set(agb, monkeypatch):
    """The hook command bakes $AGB_STATEDIR in precisely so the hot path never
    opens a config file (constraint: config is never read on the hot path)."""
    calls = []

    def boom(path=None):
        calls.append(path)
        raise AssertionError("config must not be read when $AGB_STATEDIR is set")

    monkeypatch.setattr(agb, "read_config", boom)
    monkeypatch.setenv("AGB_STATEDIR", "/tmp/whatever")
    assert agb.statedir() == "/tmp/whatever"
    assert calls == []


def test_config_is_read_when_the_environment_is_unset(agb, monkeypatch):
    seen = []

    def fake(path=None):
        seen.append(path)
        return {"statedir": "/from/fake"}

    monkeypatch.setattr(agb, "read_config", fake)
    assert agb.statedir() == "/from/fake"
    assert seen == [None]


# ---------------------------------------------------------------------------
# statedir creation, ownership and mode
# ---------------------------------------------------------------------------

def _mode(path):
    return os.stat(str(path)).st_mode & 0o7777


def test_ensure_statedir_creates_0700_and_the_subdirs(agb, statedir_path):
    agb.ensure_statedir()
    assert _mode(statedir_path) == 0o700
    for name in agb.SUBDIRS:
        sub = statedir_path / name
        assert sub.is_dir()
        assert _mode(sub) == 0o700


def test_ensure_statedir_ignores_the_umask(agb, statedir_path):
    old = os.umask(0o022)
    try:
        agb.ensure_statedir()
    finally:
        os.umask(old)
    assert _mode(statedir_path) == 0o700


def test_ensure_statedir_is_idempotent(agb, statedir_path):
    agb.ensure_statedir()
    agb.ensure_statedir()
    assert _mode(statedir_path) == 0o700


def test_ensure_statedir_rejects_a_pre_existing_wrong_mode(agb, statedir_path):
    """The parent is group-writable, so another group member could have
    pre-created a world-readable statedir. Existence is not ownership."""
    os.makedirs(str(statedir_path))
    os.chmod(str(statedir_path), 0o777)
    with pytest.raises(agb.AgbError) as excinfo:
        agb.ensure_statedir()
    assert "mode" in str(excinfo.value)


def test_ensure_statedir_rejects_a_pre_existing_wrong_owner(agb, statedir_path, monkeypatch):
    os.makedirs(str(statedir_path), 0o700)
    os.chmod(str(statedir_path), 0o700)
    monkeypatch.setattr(os, "getuid", lambda: os.stat(str(statedir_path)).st_uid + 1)
    with pytest.raises(agb.AgbError) as excinfo:
        agb.ensure_statedir()
    assert "owned by uid" in str(excinfo.value)


def test_ensure_statedir_rejects_a_non_directory(agb, statedir_path):
    with open(str(statedir_path), "w") as handle:
        handle.write("not a directory")
    with pytest.raises(agb.AgbError) as excinfo:
        agb.ensure_statedir()
    assert "not a directory" in str(excinfo.value)


def test_ensure_statedir_reports_an_unwritable_parent(agb, tmp_path, monkeypatch):
    parent = tmp_path / "locked"
    parent.mkdir()
    os.chmod(str(parent), 0o500)
    monkeypatch.setenv("AGB_STATEDIR", str(parent / "state"))
    try:
        with pytest.raises(agb.AgbError) as excinfo:
            agb.ensure_statedir()
    finally:
        os.chmod(str(parent), 0o700)
    assert "cannot create" in str(excinfo.value)


def test_ensure_statedir_reports_an_uncreatable_parent(agb, monkeypatch):
    monkeypatch.setenv("AGB_STATEDIR", "/proc/agb-cannot-exist/state")
    with pytest.raises(agb.AgbError):
        agb.ensure_statedir()


def test_ensure_session_dir_puts_the_host_in_the_path(agb, statedir_path, set_host):
    set_host("machine3")
    path = agb.ensure_session_dir()
    assert path == os.path.join(str(statedir_path), "sessions", "machine3")
    assert os.path.isdir(path)
    assert _mode(path) == 0o700


def test_session_paths_are_host_scoped(agb, statedir_path, set_host):
    set_host("box2")
    sd = str(statedir_path)
    assert agb.state_path(sd, "a3f9") == os.path.join(
        sd, "sessions", "box2", "a3f9.state")
    assert agb.record_path(sd, "a3f9") == os.path.join(
        sd, "sessions", "box2", "a3f9.json")
    # An explicit host wins, which is how a foreign host's entries are addressed.
    assert agb.state_path(sd, "a3f9", host="machine3") == os.path.join(
        sd, "sessions", "machine3", "a3f9.state")


# ---------------------------------------------------------------------------
# write_in_place -- the mtime IS the data
# ---------------------------------------------------------------------------

def test_write_in_place_keeps_the_inode_stable(agb, statedir, tmp_path):
    path = str(tmp_path / "x.state")
    agb.write_in_place(path, "active\n")
    first = os.stat(path).st_ino
    agb.write_in_place(path, "blocked\n")
    assert os.stat(path).st_ino == first


def test_write_in_place_truncates(agb, tmp_path):
    path = str(tmp_path / "x.state")
    agb.write_in_place(path, "a-very-long-previous-payload\n")
    agb.write_in_place(path, "ok\n")
    with open(path, "rb") as handle:
        assert handle.read() == b"ok\n"


def test_write_in_place_returns_the_write_fds_stat(agb, tmp_path):
    path = str(tmp_path / "x.state")
    st = agb.write_in_place(path, "active\n")
    on_disk = os.stat(path)
    assert st.st_ino == on_disk.st_ino
    assert st.st_size == len("active\n")


def test_write_in_place_creates_with_0600(agb, tmp_path):
    path = str(tmp_path / "x.state")
    old = os.umask(0o000)
    try:
        agb.write_in_place(path, "active\n")
    finally:
        os.umask(old)
    assert os.stat(path).st_mode & 0o777 == 0o600


def test_atomic_write_gives_the_file_exactly_the_mode_it_was_asked_for(
        agb, tmp_path):
    """`os.open(..., mode)` filters `mode` through the process umask, so a
    caller that passes a mode with group or other bits -- `agb_ops.write_settings`
    preserving somebody's 0664 dotfile -- silently got a tightened file. Only
    ever tightening, so cosmetic rather than dangerous, but the docstring
    promises preservation and an unkept promise in a tool about not lying is
    worth one `fchmod`. 0664 under umask 0022, because 0644 would pass either
    way and that is exactly why the existing tests did."""
    path = str(tmp_path / "settings.json")
    old = os.umask(0o022)
    try:
        agb.atomic_write(path, "{}\n", 0o664)
    finally:
        os.umask(old)
    assert os.stat(path).st_mode & 0o7777 == 0o664


def test_write_in_place_accepts_bytes(agb, tmp_path):
    path = str(tmp_path / "x.state")
    agb.write_in_place(path, b"active\n")
    with open(path, "rb") as handle:
        assert handle.read() == b"active\n"


# ---------------------------------------------------------------------------
# atomic_write -- the content must never tear
# ---------------------------------------------------------------------------

def _temps(directory):
    return [n for n in os.listdir(str(directory)) if ".tmp." in n]


def test_atomic_write_writes_content_and_leaves_no_temp(agb, tmp_path):
    path = str(tmp_path / "host.marker")
    agb.atomic_write(path, "a3f9\n#end 1\n")
    with open(path) as handle:
        assert handle.read() == "a3f9\n#end 1\n"
    assert _temps(tmp_path) == []


def test_atomic_write_temp_is_in_the_same_directory(agb, tmp_path, monkeypatch, set_host):
    set_host("box2")
    path = str(tmp_path / "sub" / "host.marker")
    os.makedirs(os.path.dirname(path))
    seen = []
    real_rename = os.rename

    def spy(src, dst):
        seen.append((src, dst))
        return real_rename(src, dst)

    monkeypatch.setattr(os, "rename", spy)
    agb.atomic_write(path, "x\n")
    (src, dst) = seen[0]
    assert dst == path
    assert os.path.dirname(src) == os.path.dirname(path)
    assert os.path.basename(src).startswith("host.marker.tmp.box2.%d." % os.getpid())


def test_temp_names_are_unique_under_concurrency(agb, tmp_path):
    path = str(tmp_path / "host.marker")
    names = set()
    lock = threading.Lock()

    def worker():
        local = [agb.temp_name(path) for _ in range(200)]
        with lock:
            names.update(local)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(names) == 8 * 200


def test_concurrent_atomic_writes_never_produce_a_torn_read(agb, tmp_path):
    path = str(tmp_path / "host.marker")
    payloads = ["key%03d\n%s\n#end 1\n" % (i, "x" * 4096) for i in range(16)]
    agb.atomic_write(path, payloads[0])
    errors = []
    stop = threading.Event()

    def writer():
        try:
            for _ in range(40):
                for payload in payloads:
                    agb.atomic_write(path, payload)
        except Exception as exc:  # pragma: no cover - reported via `errors`
            errors.append(exc)
        finally:
            stop.set()

    reads = []

    def reader():
        try:
            while not stop.is_set():
                data, _st = agb.read_fresh(path)
                reads.append(1)
                assert data.decode() in payloads
        except Exception as exc:  # pragma: no cover - reported via `errors`
            errors.append(exc)

    threads = [threading.Thread(target=writer), threading.Thread(target=reader)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    # The whole assertion lives inside `while not stop.is_set()`, and the writer
    # sets `stop` in its `finally`. A writer that finished before the reader was
    # first scheduled -- plausible on a loaded box -- would leave `errors` empty
    # having read the file zero times, and this test would pass without ever
    # having looked at it.
    assert reads, "the reader never ran; the torn-read assertion was vacuous"
    assert _temps(tmp_path) == []


def test_atomic_write_failure_leaves_no_target_and_no_temp(agb, tmp_path, monkeypatch):
    path = str(tmp_path / "host.marker")

    def boom(fd, payload):
        raise OSError(errno.ENOSPC, "no space")

    monkeypatch.setattr(agb, "_write_all", boom)
    with pytest.raises(OSError):
        agb.atomic_write(path, "x\n")
    assert not os.path.exists(path)
    assert _temps(tmp_path) == []


def test_atomic_write_rename_failure_cleans_up(agb, tmp_path, monkeypatch):
    path = str(tmp_path / "host.marker")

    def boom(src, dst):
        raise OSError(errno.EXDEV, "cross-device")

    monkeypatch.setattr(os, "rename", boom)
    with pytest.raises(OSError):
        agb.atomic_write(path, "x\n")
    assert not os.path.exists(path)
    assert _temps(tmp_path) == []


def test_atomic_write_never_clobbers_an_existing_temp(agb, tmp_path, monkeypatch):
    """O_EXCL on the temp: a collision must fail loudly, never silently share."""
    path = str(tmp_path / "host.marker")
    fixed = str(tmp_path / "host.marker.tmp.fixed")
    monkeypatch.setattr(agb, "temp_name", lambda _p: fixed)
    with open(fixed, "w") as handle:
        handle.write("someone else")
    with pytest.raises(OSError) as excinfo:
        agb.atomic_write(path, "x\n")
    assert excinfo.value.errno == errno.EEXIST


# ---------------------------------------------------------------------------
# read_fresh -- open() + fstat(fd), ESTALE retried, never skipped
# ---------------------------------------------------------------------------

def test_read_fresh_returns_content_and_the_fd_stat(agb, tmp_path):
    path = str(tmp_path / "x.state")
    st_write = agb.write_in_place(path, "active\nbox2\n1\n2\n3\n")
    data, st = agb.read_fresh(path)
    assert data == b"active\nbox2\n1\n2\n3\n"
    assert st.st_ino == st_write.st_ino
    assert st.st_mtime == st_write.st_mtime


def test_read_fresh_handles_a_large_file(agb, tmp_path):
    path = str(tmp_path / "big")
    payload = os.urandom(300000)
    agb.write_in_place(path, payload)
    data, _st = agb.read_fresh(path)
    assert data == payload


def test_read_fresh_retries_once_on_estale_from_open(agb, tmp_path, monkeypatch):
    path = str(tmp_path / "x.state")
    agb.write_in_place(path, "active\n")
    real_open = os.open
    calls = []

    def flaky(target, *args, **kwargs):
        if target == path:
            calls.append(target)
            if len(calls) == 1:
                raise OSError(errno.ESTALE, "Stale file handle")
        return real_open(target, *args, **kwargs)

    monkeypatch.setattr(os, "open", flaky)
    data, _st = agb.read_fresh(path)
    assert data == b"active\n"
    assert len(calls) == 2


def test_read_fresh_retries_once_on_estale_from_fstat(agb, tmp_path, monkeypatch):
    path = str(tmp_path / "x.state")
    agb.write_in_place(path, "active\n")
    real_fstat = os.fstat
    calls = []

    def flaky(fd):
        calls.append(fd)
        if len(calls) == 1:
            raise OSError(errno.ESTALE, "Stale file handle")
        return real_fstat(fd)

    monkeypatch.setattr(os, "fstat", flaky)
    data, _st = agb.read_fresh(path)
    assert data == b"active\n"
    assert len(calls) == 2


def test_read_fresh_raises_when_estale_persists(agb, tmp_path, monkeypatch):
    """Never a silent skip: a flapping row is exactly the agr failure mode."""
    path = str(tmp_path / "x.state")
    agb.write_in_place(path, "active\n")
    real_open = os.open
    calls = []

    def always(target, *args, **kwargs):
        if target == path:
            calls.append(target)
            raise OSError(errno.ESTALE, "Stale file handle")
        return real_open(target, *args, **kwargs)

    monkeypatch.setattr(os, "open", always)
    with pytest.raises(OSError) as excinfo:
        agb.read_fresh(path)
    assert excinfo.value.errno == errno.ESTALE
    assert len(calls) == 2


@pytest.mark.skipif(not os.path.isdir("/proc/self/fd"), reason="needs /proc")
def test_read_fresh_does_not_leak_fds_when_a_read_fails(agb, tmp_path, monkeypatch):
    path = str(tmp_path / "x.state")
    agb.write_in_place(path, "active\n")
    before = len(os.listdir("/proc/self/fd"))
    real_fstat = os.fstat

    def always(fd):
        raise OSError(errno.ESTALE, "Stale file handle")

    monkeypatch.setattr(os, "fstat", always)
    with pytest.raises(OSError):
        agb.read_fresh(path)
    monkeypatch.setattr(os, "fstat", real_fstat)
    assert len(os.listdir("/proc/self/fd")) == before


def test_read_fresh_propagates_enoent(agb, tmp_path):
    """ENOENT is a positive server answer (constraint #8) and must not be
    swallowed into "no information" here -- callers decide."""
    with pytest.raises(OSError) as excinfo:
        agb.read_fresh(str(tmp_path / "missing"))
    assert excinfo.value.errno == errno.ENOENT


def test_read_fresh_does_not_use_os_stat(agb_tree):
    """Constraint #6: os.stat/os.scandir attributes are served from the NFS
    attribute cache, so a cross-host reader can be handed the old inode's
    attributes silently. Only open()+fstat() forces a real GETATTR."""
    node = conftest.functions(agb_tree)["read_fresh"]
    made = conftest.calls(node)
    assert ("os", "stat") not in made
    assert ("os", "scandir") not in made
    assert ("os", "fstat") in made
    assert ("os", "open") in made


# ---------------------------------------------------------------------------
# constraint #17 -- nothing next to `agb` may shadow a stdlib module
# ---------------------------------------------------------------------------

def _stdlib_module_names():
    names = set(sys.builtin_module_names)
    libdir = os.path.dirname(os.__file__)
    for entry in os.listdir(libdir):
        full = os.path.join(libdir, entry)
        if entry.endswith(".py"):
            names.add(entry[:-3])
        elif os.path.isdir(full) and not entry.startswith("_"):
            names.add(entry)
    return names


def test_nothing_next_to_agb_shadows_a_stdlib_module(repo_root):
    """`-S -E` does NOT strip sys.path[0] (`-P` arrives in 3.11), so a file named
    `json.py` beside `agb` would be imported instead of the stdlib's.

    Extension-less files are checked too, even though the import machinery
    cannot pick one up: Task 4c added `agb_mac` beside `agb`, and the rule that
    protects the pair should not depend on remembering which of them happens to
    end in `.py` today.
    """
    stdlib = _stdlib_module_names()
    offenders = []
    for entry in os.listdir(repo_root):
        if entry.startswith("."):
            continue
        full = os.path.join(repo_root, entry)
        if entry.endswith(".py"):
            candidate = entry[:-3]
        elif os.path.isdir(full) or os.path.isfile(full):
            candidate = entry
        else:
            continue
        if candidate in stdlib:
            offenders.append(entry)
    assert offenders == []


def test_the_mac_side_file_is_also_checked_by_that_guard(repo_root, mac_path):
    """Task 4c's constraint-#17 checkbox, made non-vacuous: the guard above only
    covers `agb_mac` if it is actually one of the entries it walks."""
    assert os.path.basename(mac_path) in os.listdir(repo_root)
    assert not mac_path.endswith(".py")
    assert os.path.basename(mac_path) not in _stdlib_module_names()


def test_the_guard_would_actually_catch_a_shadowing_file(repo_root):
    """Negative control: without it the test above could pass vacuously."""
    stdlib = _stdlib_module_names()
    assert "json" in stdlib
    assert "socket" in stdlib


# ---------------------------------------------------------------------------
# the nine hand-rolled parsers agree on `--opt=` and `--flag=`
# ---------------------------------------------------------------------------
#
# `argparse` is banned (constraint #3), so every command parses its own argv.
# Nine parsers is nine chances to disagree, and they DID: `--flag=` -- an `=`
# with nothing after it -- was a set flag in `parse_install_args`, a
# missing-value error in `parse_status_args`, and in every value-taking parser
# it silently consumed the NEXT argv word as the value. `doctor --statedir=
# --mac-id mac-0001` read the statedir as the string `--mac-id`.
#
# One rule, asserted against all nine at once rather than nine times in nine
# files: an `=` present with nothing after it is a missing value, and a boolean
# flag never takes one.

# (module fixture, parser, a valid baseline argv, a value option, a bool flag)
PARSERS = [
    ("agb", "parse_feed_args", ["mac-0001"], "--poll-interval", None),
    ("mac", "parse_bridge_args", [], "--statedir", "--from-stdin"),
    ("mac", "parse_close_done_args", [], "--rows", "--dry-run"),
    ("ops", "parse_doctor_args", [], "--statedir", None),
    ("ops", "parse_prune_args", [], "--statedir", "--dry-run"),
    ("ops", "parse_pane_args", ["a3f9c1e0", "--host", "box3"], "--tmux", None),
    ("ops", "parse_status_args", [], "--statedir", None),
    ("ops", "parse_install_args", [], "--statedir", "--dry-run"),
    ("ops", "parse_config_args", [], "--statedir", "--dry-run"),
]

PARSER_IDS = [row[1] for row in PARSERS]


def _parser(request, row):
    return getattr(request.getfixturevalue(row[0]), row[1])


@pytest.mark.parametrize("row", PARSERS, ids=PARSER_IDS)
def test_every_parser_accepts_its_own_baseline(request, row):
    """Non-vacuity for the three tests below: each baseline really does parse,
    so a refusal there is about the thing being tested."""
    assert _parser(request, row)(list(row[2])) is not None


@pytest.mark.parametrize("row", PARSERS, ids=PARSER_IDS)
def test_an_empty_inline_value_is_a_missing_value_everywhere(request, row, agb):
    """`--opt=` never reaches into the rest of the argv.

    There is deliberately a word AFTER the `--opt=`, because that is the only
    arrangement in which the old behaviour is visible: with nothing following,
    both the old code and the new one say "needs a value" and the test would
    pass against the bug. Here the old code takes `sentinel` as the value and
    most of these parsers then return happily.

    The message matters as much as the refusal for the two parsers that do
    still fail: they fail with "not a number: sentinel", which describes the
    wrong argument entirely.
    """
    argv = [row[3] + "=", "sentinel"] + list(row[2])
    with pytest.raises(agb.AgbError) as excinfo:
        _parser(request, row)(argv)
    assert "needs a value" in str(excinfo.value), argv
    assert row[3] in str(excinfo.value)


@pytest.mark.parametrize("row", PARSERS, ids=PARSER_IDS)
def test_a_value_option_with_nothing_after_it_is_a_missing_value(request, row,
                                                                  agb):
    argv = list(row[2]) + [row[3]]
    with pytest.raises(agb.AgbError) as excinfo:
        _parser(request, row)(argv)
    assert "needs a value" in str(excinfo.value), argv


@pytest.mark.parametrize("row", [r for r in PARSERS if r[4]],
                         ids=[r[1] for r in PARSERS if r[4]])
def test_a_boolean_flag_never_takes_a_value(request, row, agb):
    """`--dry-run=` used to set the flag and swallow the `=` in silence, which
    is the worst answer available for a flag whose whole job is to say "write
    nothing"."""
    for spelling in (row[4] + "=", row[4] + "=yes", row[4] + "=no"):
        with pytest.raises(agb.AgbError) as excinfo:
            _parser(request, row)(list(row[2]) + [spelling])
        assert "takes no value" in str(excinfo.value), spelling
    # ...and the bare flag still works, so the refusal is not just "always no".
    assert _parser(request, row)(list(row[2]) + [row[4]]) is not None


# (a callable that must refuse an unusable value, and a label for the id)
def _pane_value(ops, value):
    return ops.parse_pane_args(["a3f9c1e0", "--host", "box3", "--tmux", value])


def _install_value(ops, value):
    return ops.parse_install_args(["--settings", value])


def _config_value(ops, value):
    return ops.check_config_value("feed_host", value)


VALUE_CHECKERS = [("pane", _pane_value), ("install-hooks", _install_value),
                  ("install-config", _config_value)]


@pytest.mark.parametrize("hostile", ["one two\rthree", "one\nthree",
                                     "one\tthree", "one\x01three",
                                     "one\x7fthree", " padded", "padded ",
                                     ""])
@pytest.mark.parametrize("checker", VALUE_CHECKERS, ids=[c[0] for c
                                                         in VALUE_CHECKERS])
def test_every_value_gate_refuses_the_same_unusable_values(ops, agb, checker,
                                                            hostile):
    """⚠️ Three commands asked one question and gave three answers: `pane`
    refused an embedded `\\r`, `install-hooks` did not (it tested only `\\n`),
    and `install-config` walked the string for control characters. So a
    carriage return sailed through `--settings`/`--agb` and was refused by
    `pane` -- one predicate, two verdicts, which is the drift this file already
    kills for `--opt=` above.

    Every one of these values ends up either on a remote command line a shell
    re-splits or on a `key = value` line a line-oriented parser reads back, so
    the answer has to be the same everywhere: `agb_ops._usable_value`."""
    with pytest.raises(agb.AgbError):
        checker[1](ops, hostile)


@pytest.mark.parametrize("checker", VALUE_CHECKERS, ids=[c[0] for c
                                                         in VALUE_CHECKERS])
def test_every_value_gate_still_accepts_an_ordinary_value(ops, checker):
    """Non-vacuity: the shared predicate is not simply refusing everything."""
    assert checker[1](ops, "/opt/agbridge/agb") is not None


def _blocking_reads(tree):
    """`x.communicate(...)`, `x.stdout.read()` and `x.stderr.read()` in a tree.

    AST rather than a text search, so this guard cannot trip over its own
    docstring -- and so a comment mentioning the hazard is not a violation.
    """
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func,
                                                            ast.Attribute):
            continue
        attr, base = node.func.attr, node.func.value
        if attr == "communicate" and not (isinstance(base, ast.Name)
                                          and base.id == "conftest"):
            found.append((node.lineno, "communicate"))
        elif (attr == "read" and isinstance(base, ast.Attribute)
              and base.attr in ("stdout", "stderr")):
            found.append((node.lineno, base.attr + ".read"))
    return found


def test_no_test_blocks_on_a_subprocess_without_a_bound(repo_root):
    """A regression that makes a subprocess stop answering must FAIL the run,
    not hang it.

    Reproduced before this existed: deleting `pane_wait`'s EOF arm made
    `agb pane` spin on a closed stdin, and with no bound anywhere the suite ran
    at 30% CPU until it was killed by hand three minutes later, having reported
    nothing at all. Every site now goes through `conftest.communicate()`, which
    kills and drains the child on timeout, so the same mutation now fails by
    name in 30 s.

    The two `.read()` forms are on the list for a second reason: draining one
    pipe to EOF while the other is an undrained pipe deadlocks the moment the
    child writes more than one pipe buffer to the one nobody is reading -- and
    it deadlocks on exactly the condition such a test usually asserts against.
    """
    import io
    offenders = []
    for entry in sorted(os.listdir(os.path.join(repo_root, "tests"))):
        if not entry.startswith("test_") or not entry.endswith(".py"):
            continue
        with io.open(os.path.join(repo_root, "tests", entry),
                     encoding="utf-8") as handle:
            tree = ast.parse(handle.read(), filename=entry)
        for lineno, what in _blocking_reads(tree):
            offenders.append("%s:%d: %s" % (entry, lineno, what))
    assert offenders == [], (
        "use conftest.communicate(proc, ...) -- it cannot hang:\n"
        + "\n".join(offenders))


def test_that_guard_would_actually_catch_an_unbounded_call(repo_root):
    """Negative control: the walk above finds nothing today, so without this it
    could be looking at the wrong node type and nobody would know."""
    import textwrap
    tree = ast.parse(textwrap.dedent("""
        out, err = proc.communicate(b"")
        data = proc.stdout.read()
        ok = conftest.communicate(proc)
    """))
    assert [what for _line, what in _blocking_reads(tree)] == [
        "communicate", "stdout.read"]
