"""Task 9b -- packaging: `agb install-config`, the launchd plist, `install.sh`.

Three things are being asserted here, and each of them is a way the tool could
be installed and then lie:

**The distribution is THREE files.** `agb`, `agb_mac`, `agb_ops`, in the same
directory, because `agb` resolves its siblings from `realpath(__file__)`. A copy
that misses one installs cleanly and then fails at the first `agb bridge` or the
first `agb doctor` -- the worst possible moment for a missing file. So the tests
do not check that files were copied; they **run the installed tree** through all
three of them, and there is a negative control that removes one and requires the
installer to refuse.

**`mac_id` is minted once and never again.** Nothing else in the tool produces
one: the Mac's bridge writes `bridge/<mac-id>.beat` and the farm's
`agb status-line` reads that exact name. A second, separately generated id would
leave both halves healthy and the segment reading `bridge:DOWN` for ever, which
is exactly the class of failure this project exists to remove. So: generated
when absent, **kept** on every later run, validated when given, and loud when
deliberately replaced.

**The farm side is not a no-op.** NFS shares the three files, not the
configuration; `agb status-line` runs under tmux's `status-interval` where
neither `$AGB_STATEDIR` nor ssh's `env` exists. The farm therefore needs its own
`~/.config/agbridge/config` carrying `statedir` and `mac_id`, and the hooks
installed -- both of which `install.sh farm` does and both of which are checked.

⚠️ **Nothing here may touch the real machine.** `install.sh` is a real
installer: every invocation in this file is given `--dest`, `--config`,
`--launch-agents`, `--log-dir`, `--settings` and `--statedir` under the test's
own `tmp_path`, `conftest.fake_home` is autouse, and `launchctl`/`ssh` are only
ever reached as recording stubs on `$PATH`. The developer's real
`~/.claude/settings.json` is read once, byte for byte, and asserted unchanged.
"""

import hashlib
import json
import os
import plistlib
import re
import shutil
import stat
import subprocess
import sys

import pytest

import conftest


HOST = "box2"

INSTALL_SH = os.path.join(conftest.REPO_ROOT, "install.sh")
PLIST_TEMPLATE = os.path.join(conftest.REPO_ROOT, "dist", "com.agbridge.plist")
DIST_FILES = ("agb", "agb_mac", "agb_ops")

# Captured before the autouse `fake_home` fixture moves `$HOME`, so it is the
# developer's *real* file. Read-only, and only to prove nothing here writes it.
REAL_SETTINGS = os.path.expanduser("~/.claude/settings.json")
REAL_CONFIG = os.path.expanduser("~/.config/agbridge/config")


def digest(path):
    """sha256 of a file, or None if it does not exist."""
    if not os.path.exists(path):
        return None
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


REAL_SETTINGS_DIGEST = digest(REAL_SETTINGS)
REAL_CONFIG_DIGEST = digest(REAL_CONFIG)


class Out(object):
    """A collecting `out`, so assertions are about text rather than capsys."""

    def __init__(self):
        self.text = ""

    def write(self, data):
        self.text += data

    def flush(self):
        pass


def read_bytes(path):
    with open(str(path), "rb") as handle:
        return handle.read()


def read_text(path):
    return read_bytes(path).decode("utf-8")


@pytest.fixture
def config_path(tmp_path):
    """A config file path under the test's own tree. Never the real one."""
    path = tmp_path / "cfg" / "config"
    os.makedirs(str(path.parent))

    def write(text):
        with open(str(path), "w") as handle:
            handle.write(text)
        return path

    write.path = str(path)
    return write


@pytest.fixture
def install_config(ops, config_path, set_host):
    """Run `install-config` against that path, collecting the report."""
    set_host(HOST)

    def run(*args):
        out = Out()
        argv = ["--config", config_path.path] + list(args)
        code = ops.run_install_config(argv, out=out)
        return (code, out.text)

    return run


# ---------------------------------------------------------------------------
# mac_id -- generated once, then kept
# ---------------------------------------------------------------------------

def test_a_mac_id_is_generated_and_persisted(install_config, config_path, agb):
    """The checkbox, and the whole reason this command exists: nothing else in
    the tool produces a mac-id."""
    code, text = install_config("--generate-mac-id")
    assert code == 0
    values = agb.read_config(config_path.path)
    assert agb.valid_mac_id(values["mac_id"])
    assert "(generated)" in text
    assert values["mac_id"] in text


def test_the_mac_id_is_never_regenerated_over_an_existing_one(
        install_config, config_path, agb):
    """A second `--generate-mac-id` run must keep the first id.

    Regenerating would be silent in the worst available way: the Mac would write
    one `bridge/<mac-id>.beat`, the farm would watch another, both halves would
    look healthy and the segment would read `bridge:DOWN` for ever.
    """
    install_config("--generate-mac-id")
    first = agb.read_config(config_path.path)["mac_id"]
    before = read_bytes(config_path.path)

    code, text = install_config("--generate-mac-id")
    assert code == 0
    assert agb.read_config(config_path.path)["mac_id"] == first
    assert "(kept)" in text
    # Not merely equal: byte for byte, so a re-install cannot churn the file.
    assert read_bytes(config_path.path) == before
    assert "unchanged:" in text


def test_two_generated_mac_ids_differ(ops):
    """The random half is what stops two Macs -- or one restored from a backup
    -- sharing a beat file and each looking alive to the other."""
    assert ops.generate_mac_id("box") != ops.generate_mac_id("box")


def test_a_generated_mac_id_names_its_host_and_is_a_usable_path_component(
        ops, agb):
    mac_id = ops.generate_mac_id("my-macbook")
    assert mac_id.startswith("my-macbook-")
    assert agb.valid_mac_id(mac_id)


def test_a_hostile_hostname_still_yields_a_usable_mac_id(ops, agb):
    """It becomes a path component in a directory shared by every host, so the
    host part is filtered rather than trusted."""
    for host in ("../../etc", "host/with/slashes", "", "!!!", "x" * 200):
        mac_id = ops.generate_mac_id(host)
        assert agb.valid_mac_id(mac_id), host
        assert "/" not in mac_id and ".." not in mac_id


def test_a_given_mac_id_is_validated_and_nothing_is_written(
        ops, install_config, config_path, agb):
    with pytest.raises(agb.AgbError):
        install_config("--mac-id", "../evil")
    assert not os.path.exists(config_path.path)


def test_replacing_a_mac_id_is_reported_loudly(install_config, config_path):
    install_config("--generate-mac-id")
    code, text = install_config("--mac-id", "second-mac-0001")
    assert code == 0
    assert "(replaced)" in text
    assert "warning:" in text
    assert "every other machine" in text.lower()


def test_the_farm_refuses_to_invent_a_mac_id(install_config, config_path, agb):
    """`--generate-mac-id` is the Mac's flag. A fresh id on the farm names a
    beat file nothing writes."""
    with pytest.raises(agb.AgbError) as excinfo:
        install_config("--statedir", "/tmp/x")
    assert "mac_id" in str(excinfo.value)
    assert "bridge:DOWN" in str(excinfo.value)
    assert not os.path.exists(config_path.path)


def test_an_unusable_recorded_mac_id_is_not_silently_replaced(
        install_config, config_path, agb):
    config_path("mac_id = ../evil\n")
    before = read_bytes(config_path.path)
    with pytest.raises(agb.AgbError):
        install_config("--generate-mac-id")
    assert read_bytes(config_path.path) == before


def test_an_unusable_recorded_mac_id_can_be_replaced_deliberately(
        install_config, config_path, agb):
    config_path("mac_id = ../evil\n")
    code, _text = install_config("--mac-id", "clean-0001")
    assert code == 0
    assert agb.read_config(config_path.path)["mac_id"] == "clean-0001"


# ---------------------------------------------------------------------------
# the farm-side config: statedir + mac_id
# ---------------------------------------------------------------------------

def test_the_farm_config_is_written_with_statedir_and_mac_id(
        install_config, config_path, agb, tmp_path):
    """The second checkbox. NFS shares the files, not the configuration: the
    segment runs under tmux's status-interval with no `$AGB_STATEDIR` and no ssh
    `env`, so these two keys are the only thing that can point it anywhere."""
    sd = str(tmp_path / "state")
    code, text = install_config("--mac-id", "mac-0001", "--statedir", sd)
    assert code == 0
    values = agb.read_config(config_path.path)
    assert values["mac_id"] == "mac-0001"
    assert values["statedir"] == sd
    assert "set:      statedir = %s" % (sd,) in text


def test_the_statedir_falls_back_to_agbs_own_resolution(
        install_config, config_path, agb, statedir_path):
    """`$AGB_STATEDIR` -> config -> default is decided in `agb.statedir()` and
    is not re-derived here."""
    code, _text = install_config("--generate-mac-id")
    assert code == 0
    assert agb.read_config(config_path.path)["statedir"] == str(statedir_path)


def test_an_installed_statedir_is_not_repointed_by_the_environment(
        install_config, config_path, agb, monkeypatch, tmp_path):
    """A re-install from a shell that happens to carry `$AGB_STATEDIR` must not
    silently move an installed configuration to another directory."""
    config_path("statedir = /shared/.agbridge\nmac_id = mac-0001\n")
    monkeypatch.setenv("AGB_STATEDIR", str(tmp_path / "elsewhere"))
    code, _text = install_config()
    assert code == 0
    assert agb.read_config(config_path.path)["statedir"] == "/shared/.agbridge"


def test_a_relative_farm_path_is_refused(install_config, agb):
    for option, value in (("--statedir", "state"),
                          ("--agb-remote-path", "agb"),
                          ("--remote-python", "python3")):
        with pytest.raises(agb.AgbError) as excinfo:
            install_config("--generate-mac-id", option, value)
        assert "absolute" in str(excinfo.value), option


def test_every_documented_config_key_can_be_written(
        install_config, config_path, agb, ops):
    """The plan's config table, end to end -- including `host_<name>`, which is
    the one open-ended key."""
    code, _text = install_config(
        "--generate-mac-id", "--statedir", "/shared/.agbridge",
        "--feed-host", "box2", "--agb-remote-path", "/opt/agbridge/agb",
        "--remote-python", "/bin/python3", "--jump-host", "box2",
        "--host", "machine3=box3-via-box2")
    assert code == 0
    values = agb.read_config(config_path.path)
    assert values["feed_host"] == "box2"
    assert values["agb_remote_path"] == "/opt/agbridge/agb"
    assert values["remote_python"] == "/bin/python3"
    assert values["jump_host"] == "box2"
    assert values["host_machine3"] == "box3-via-box2"
    for key in values:
        assert ops.known_config_key(key), key


# ---------------------------------------------------------------------------
# merging: this is somebody's hand-edited file
# ---------------------------------------------------------------------------

def test_comments_and_unknown_keys_survive_verbatim(install_config,
                                                    config_path):
    original = ("# my notes\n"
                "\n"
                "feed_host = box2\n"
                "something_else = keep me\n")
    config_path(original)
    code, _text = install_config("--generate-mac-id")
    assert code == 0
    text = read_text(config_path.path)
    # An ordered subsequence, not membership. "verbatim" is a claim about order
    # and multiplicity too: a merge that reversed the file, or duplicated every
    # line, passed a membership check while destroying exactly what a
    # hand-edited config is kept for.
    remaining = text.splitlines()
    for line in original.splitlines():
        assert line in remaining, (line, text)
        remaining = remaining[remaining.index(line) + 1:]
    for line in original.splitlines():
        if line:
            assert text.splitlines().count(line) == 1, line


def test_hand_formatting_of_an_unchanged_key_is_left_alone(install_config,
                                                           config_path):
    config_path("statedir=/shared/.agbridge\nmac_id=mac-0001\n")
    before = read_bytes(config_path.path)
    code, text = install_config("--statedir", "/shared/.agbridge")
    assert code == 0
    assert read_bytes(config_path.path) == before
    assert "unchanged:" in text


def test_a_duplicate_key_line_is_dropped_rather_than_left_to_win(
        install_config, config_path, agb):
    """`agb.parse_config` takes the *last* occurrence, so a stale duplicate left
    behind would make the file say something other than what was reported."""
    config_path("statedir = /a\nmac_id = mac-0001\nstatedir = /b\n")
    code, _text = install_config("--statedir", "/c")
    assert code == 0
    text = read_text(config_path.path)
    assert len([l for l in text.splitlines()
                if l.startswith("statedir")]) == 1
    assert agb.read_config(config_path.path)["statedir"] == "/c"


def test_a_malformed_line_is_kept_and_named(install_config, config_path):
    config_path("this line has no equals sign\nmac_id = mac-0001\n")
    code, text = install_config()
    assert code == 0
    assert "this line has no equals sign" in read_text(config_path.path)
    assert "kept:     line 1" in text


def test_what_is_written_parses_back_to_what_was_reported(ops, agb):
    """The round trip is checked before the file is written, with the same
    reader every other command uses."""
    with pytest.raises(agb.AgbError) as excinfo:
        ops.verify_config_text("mac_id = other\n", {"mac_id": "wanted"})
    assert "refusing to write" in str(excinfo.value)


def test_a_value_the_file_could_not_carry_is_refused(ops, agb, install_config,
                                                     config_path):
    for value in ("has\nnewline", "has\ttab-and-\x01control"):
        with pytest.raises(agb.AgbError):
            install_config("--generate-mac-id", "--feed-host", value)
    assert not os.path.exists(config_path.path)


def test_the_previous_config_is_backed_up_and_its_mode_preserved(
        install_config, config_path, agb):
    config_path("mac_id = mac-0001\n")
    os.chmod(config_path.path, 0o644)
    before = read_bytes(config_path.path)
    code, text = install_config("--statedir", "/shared/other")
    assert code == 0
    backup = config_path.path + ops_backup_suffix()
    assert read_bytes(backup) == before
    assert "backup:" in text
    mode = stat.S_IMODE(os.stat(config_path.path).st_mode)
    assert mode == 0o644


def ops_backup_suffix():
    return ".agb.bak"


def test_a_config_that_cannot_be_decoded_is_left_exactly_as_it_stands(
        install_config, config_path, agb):
    with open(config_path.path, "wb") as handle:
        handle.write(b"mac_id = \xff\xfe\n")
    before = read_bytes(config_path.path)
    with pytest.raises(agb.AgbError) as excinfo:
        install_config("--generate-mac-id")
    assert "UTF-8" in str(excinfo.value)
    assert read_bytes(config_path.path) == before
    assert not os.path.exists(config_path.path + ops_backup_suffix())


def test_dry_run_reports_and_writes_nothing(install_config, config_path):
    code, text = install_config("--generate-mac-id", "--dry-run")
    assert code == 0
    assert "dry run: nothing was written" in text
    assert "wrote:" not in text
    assert not os.path.exists(config_path.path)


def test_unknown_options_are_rejected(install_config, agb):
    for bad in ("--frobnicate", "positional"):
        with pytest.raises(agb.AgbError):
            install_config(bad)
    with pytest.raises(agb.AgbError):
        install_config("--mac-id")


def test_a_host_mapping_wants_name_equals_target(install_config, agb):
    with pytest.raises(agb.AgbError) as excinfo:
        install_config("--generate-mac-id", "--host", "machine3")
    assert "<hostname>=<ssh-target>" in str(excinfo.value)


# ---------------------------------------------------------------------------
# --print-mac-id: stdout is a machine-readable channel
# ---------------------------------------------------------------------------

def test_print_mac_id_puts_only_the_id_on_stdout(run_agb, tmp_path, agb):
    """`install.sh` reads the id back through this rather than re-implementing
    `key = value` parsing in shell -- a second reader is one that drifts."""
    config = str(tmp_path / "config")
    code, out, err = run_agb(["install-config", "--config", config,
                              "--generate-mac-id", "--print-mac-id"])
    assert code == 0
    mac_id = out.decode().strip()
    assert agb.valid_mac_id(mac_id)
    assert out.decode() == mac_id + "\n"          # nothing else on stdout
    assert b"install-config" in err               # the report is still shown
    assert agb.read_config(config)["mac_id"] == mac_id


# ---------------------------------------------------------------------------
# install.sh
# ---------------------------------------------------------------------------

def extract_sh_function(name):
    """The text of one `install.sh` function, taken out of the file itself.

    A couple of refusals are unreachable end to end on this box -- `find_python`
    only gives up when none of its four absolute candidates exist, and
    `/usr/bin/python3` does. Extracting the function is how they get exercised
    without a chroot; restating the body in the test would mean testing the
    test's own copy of it.
    """
    with open(INSTALL_SH) as handle:
        lines = handle.read().splitlines()
    start = lines.index(name + "() {")
    end = start
    while lines[end] != "}":
        end += 1
    return "\n".join(lines[start:end + 1])


@pytest.fixture
def run_sh(tmp_path, fake_home):
    """Run `install.sh`, always inside the test's own tree."""
    def run(args, script=INSTALL_SH, env=None):
        environ = dict(os.environ)
        environ["HOME"] = str(fake_home)
        if env:
            environ.update(env)
        proc = subprocess.Popen(["/bin/sh", script] + list(args),
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, env=environ,
                                cwd=str(tmp_path))
        out, err = conftest.communicate(proc)
        return (proc.returncode, out.decode("utf-8", "replace"),
                err.decode("utf-8", "replace"))
    return run


@pytest.fixture
def mac_args(tmp_path):
    """The Mac role, with every path that could escape the test pinned down."""
    def build(**over):
        args = {
            "--dest": str(tmp_path / "dest"),
            "--config": str(tmp_path / "cfg" / "config"),
            "--launch-agents": str(tmp_path / "agents"),
            "--log-dir": str(tmp_path / "logs"),
            "--statedir": str(tmp_path / "state"),
            "--feed-host": "box2",
            "--agb-remote-path": "/opt/agbridge/agb",
            "--python": sys.executable,
        }
        args.update(over)
        # `--no-probe` for the same reason every path above is pinned: the probe
        # is an outbound ssh, and a test that reaches the network is a test that
        # fails for reasons unrelated to its subject. The probe has its own
        # tests, which opt back in with a recording stub.
        argv = ["mac", "--no-load", "--no-probe"]
        for name in sorted(args):
            if args[name] is None:
                continue
            argv += [name, args[name]]
        return argv
    return build


def test_install_sh_copies_all_three_files(run_sh, mac_args, tmp_path):
    """Task 4c made the distribution two files and Task 6a made it three."""
    code, out, err = run_sh(mac_args())
    assert code == 0, err
    dest = tmp_path / "dest"
    for name in DIST_FILES:
        assert (dest / name).exists(), name
        assert read_bytes(dest / name) == read_bytes(
            os.path.join(conftest.REPO_ROOT, name))
        # No shebang, never executable: `agb` is always run as an argument to
        # the interpreter, which is the only way to pass `-S -E`.
        assert not os.access(str(dest / name), os.X_OK)
    assert "copied:" in out


def test_the_installed_tree_actually_runs_all_three_files(run_sh, mac_args,
                                                          tmp_path, set_host):
    """The point of the whole task: files being present is not the claim.

    A tree missing `agb_mac` runs `agb version` perfectly and dies at the first
    `agb bridge`; one missing `agb_ops` dies at the first `agb doctor`. So the
    installed copy is driven through all three -- `hook` (agb), `bridge`
    (agb_mac) and `status-line` (agb_ops).
    """
    code, out, err = run_sh(mac_args())
    assert code == 0, err
    assert "verified:" in out
    installed = str(tmp_path / "dest" / "agb")
    sd = str(tmp_path / "hookstate")
    env = dict(os.environ)
    env["HOME"] = str(tmp_path / "home")
    env["AGB_STATEDIR"] = sd
    env["AGB_HOST"] = HOST

    def run(args, stdin=b""):
        proc = subprocess.Popen([sys.executable, "-S", "-E", installed] + args,
                                stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, env=env)
        stdout, stderr = conftest.communicate(proc, stdin)
        return (proc.returncode, stdout, stderr)

    assert run(["hook", "active"])[0] == 0
    sessions = os.path.join(sd, "sessions", HOST)
    assert [n for n in os.listdir(sessions) if n.endswith(".state")]

    rc, out_bridge, err_bridge = run(
        ["bridge", "--from-stdin", "--no-agterm", "--feed-host", "box2",
         "--mac-id", "probe"])
    assert rc == 0, err_bridge

    rc, out_status, err_status = run(["status-line", "--mac-id", "probe"])
    assert rc == 0, err_status
    assert out_status.startswith(b"bridge:")


def test_an_incomplete_source_tree_is_refused(run_sh, mac_args, tmp_path):
    """Negative control for the check above: a tree with only `agb` in it must
    not install at all."""
    partial = tmp_path / "partial"
    partial.mkdir()
    shutil.copy(INSTALL_SH, str(partial / "install.sh"))
    shutil.copy(os.path.join(conftest.REPO_ROOT, "agb"), str(partial / "agb"))
    code, out, err = run_sh(mac_args(), script=str(partial / "install.sh"))
    assert code == 1
    assert "agb_mac" in err or "agb_ops" in err
    assert not (tmp_path / "dest").exists()


def test_a_tree_that_cannot_run_its_siblings_is_refused(run_sh, tmp_path):
    """And the runtime half of the same control: two of the three files present,
    so the file check passes and the *probe* has to catch it."""
    broken = tmp_path / "broken"
    broken.mkdir()
    for name in ("agb", "agb_mac"):
        shutil.copy(os.path.join(conftest.REPO_ROOT, name), str(broken / name))
    code, out, err = run_sh(["farm", "--mac-id", "mac-0001",
                             "--agb", str(broken / "agb"),
                             "--config", str(tmp_path / "config"),
                             "--statedir", str(tmp_path / "state"),
                             "--settings", str(tmp_path / "settings.json"),
                             "--python", sys.executable])
    assert code == 1
    assert "agb_ops" in err
    assert not (tmp_path / "config").exists()


def test_install_sh_mac_writes_the_config_with_a_generated_mac_id(
        run_sh, mac_args, tmp_path, agb):
    code, out, err = run_sh(mac_args())
    assert code == 0, err
    values = agb.read_config(str(tmp_path / "cfg" / "config"))
    assert agb.valid_mac_id(values["mac_id"])
    assert values["feed_host"] == "box2"
    assert values["agb_remote_path"] == "/opt/agbridge/agb"
    assert values["statedir"] == str(tmp_path / "state")
    assert values["mac_id"] in out


def test_install_sh_mac_defaults_the_config_to_the_users_own_dotfile(
        run_sh, mac_args, fake_home, agb):
    """The default path is exercised (under a faked `$HOME`) rather than only
    the `--config` seam, because the default is what the user gets."""
    code, out, err = run_sh(mac_args(**{"--config": None}))
    assert code == 0, err
    path = os.path.join(str(fake_home), ".config", "agbridge", "config")
    assert agb.valid_mac_id(agb.read_config(path)["mac_id"])


def test_install_sh_mac_prints_the_farm_command_including_the_mac_id(
        run_sh, mac_args, tmp_path, agb):
    """The farm side is not a no-op, and the two sides have to agree on the id."""
    code, out, err = run_sh(mac_args())
    assert code == 0, err
    mac_id = agb.read_config(str(tmp_path / "cfg" / "config"))["mac_id"]
    assert "install.sh farm --mac-id %s" % (mac_id,) in out
    assert "NOT a no-op" in out


def test_the_printed_farm_command_carries_the_statedir_the_mac_recorded(
        run_sh, mac_args, tmp_path, agb):
    """The printed hint and the `--farm` ssh must be the SAME command.

    Dropping `--statedir` from the printed one is not cosmetic: the copy-pasted
    install writes hooks against agb's default statedir, while the bridge's ssh
    sets `AGB_STATEDIR` to the configured one and `cmd_feed` creates it. The
    feed then reports an empty farm for ever and `agb status-line` on the farm
    reads `bridge:DOWN never` with a healthy bridge running -- the same class of
    silent disagreement the mac-id prose in `role_farm` warns about."""
    sd = str(tmp_path / "state")
    code, out, err = run_sh(mac_args())
    assert code == 0, err
    assert agb.read_config(str(tmp_path / "cfg" / "config"))["statedir"] == sd
    hint = [l for l in out.splitlines() if "install.sh farm" in l]
    assert hint and "--statedir %s" % (sd,) in hint[0]


def test_the_printed_farm_command_omits_the_statedir_when_none_was_given(
        run_sh, mac_args):
    """The flag is forwarded, not invented: with no `--statedir` both halves
    fall through to `agb.statedir()`'s own resolution, which is one rule."""
    code, out, err = run_sh(mac_args(**{"--statedir": None}))
    assert code == 0, err
    hint = [l for l in out.splitlines() if "install.sh farm" in l]
    assert hint and "--statedir" not in hint[0]


FARM_SIDE_OPTIONS = {
    "--mac-id": "mac-0001",
    "--remote-python": "/bin/python3",
    "--jump-host": "gate",
    "--host": "box3=box3.example",
}


def _farm_hint(out):
    """The farm command as the hint prints it, split back into an argv."""
    hint = [l for l in out.splitlines() if "install.sh farm" in l
            and not l.startswith("farm:")]
    assert len(hint) == 1, out
    return hint[0].split()


def test_the_farm_command_carries_every_farm_side_option(
        run_sh, mac_args, tmp_path, agb):
    """Three options used to stop at the Mac, and each is a silent divergence.

    `--remote-python` is the interpreter the bridge's ssh runs `agb feed`
    under (constraint #14: absolute, because `ssh host cmd` sources no
    profile). If it does not travel, `install.sh farm` falls through to
    `find_python()` and bakes a DIFFERENT interpreter into the hook command --
    one runtime for the feed and another for every hook on the same box,
    decided by that host's PATH, with nothing to report the mismatch.

    `--jump-host` and `--host` are the ssh routes `agb prune --via-ssh` needs
    to reach the host that owns an entry, which is the only path that turns
    the heuristic into a proof. The Mac's config got them; the farm's did not.
    """
    code, out, err = run_sh(mac_args(**FARM_SIDE_OPTIONS))
    assert code == 0, err
    argv = _farm_hint(out)
    for name, value in (("--python", "/bin/python3"),
                        ("--jump-host", "gate"),
                        ("--host", "box3=box3.example"),
                        ("--statedir", str(tmp_path / "state"))):
        assert name in argv, (name, argv)
        assert argv[argv.index(name) + 1] == value, (name, argv)
    # ...and the Mac's own config records the same routes it just forwarded.
    values = agb.read_config(str(tmp_path / "cfg" / "config"))
    assert values["remote_python"] == "/bin/python3"
    assert values["jump_host"] == "gate"


def test_the_printed_farm_command_and_the_sshed_one_are_the_same_argv(
        run_sh, mac_args, stub_bin):
    """ONE argv, built once. The two halves diverging is not hypothetical --
    `--statedir` was dropped from the printed form alone once already, and
    hooks were then installed against a statedir the bridge never looked at.
    Comparing the two forms token for token is the only assertion that cannot
    be satisfied by fixing one of them.

    `--mac-id` is pinned so the two runs are comparable at all: without it each
    run mints a fresh id and the argvs differ for an uninteresting reason.
    """
    printed = run_sh(mac_args(**FARM_SIDE_OPTIONS))
    assert printed[0] == 0, printed[2]
    stub_bin.install("ssh")
    opts = dict(FARM_SIDE_OPTIONS)
    opts["--farm"] = "box2"
    sshed = run_sh(mac_args(**opts))
    assert sshed[0] == 0, sshed[2]
    calls = stub_bin.calls("ssh")
    assert len(calls) == 1
    assert calls[0][1:] == _farm_hint(printed[1])


def test_install_sh_mac_runs_the_farm_side_over_ssh_when_asked(
        run_sh, mac_args, tmp_path, stub_bin, agb):
    stub_bin.install("ssh")
    code, out, err = run_sh(mac_args(**{"--farm": "box2"}))
    assert code == 0, err
    mac_id = agb.read_config(str(tmp_path / "cfg" / "config"))["mac_id"]
    calls = stub_bin.calls("ssh")
    assert len(calls) == 1
    argv = calls[0]
    assert argv[0] == "box2"
    assert argv[1:4] == ["sh", "/opt/agbridge/install.sh", "farm"]
    assert "--mac-id" in argv and mac_id in argv


def test_a_failing_farm_side_fails_the_install(run_sh, mac_args, stub_bin):
    """Half an install is the state nothing can diagnose later."""
    stub_bin.install("ssh", exit_code=3)
    code, out, err = run_sh(mac_args(**{"--farm": "box2"}))
    assert code == 1
    assert "farm side failed" in err


def test_install_sh_farm_writes_the_config_and_the_hooks(run_sh, tmp_path,
                                                          agb):
    """Both farm-side checkboxes in one run: the config carries `statedir` and
    `mac_id`, and `~/.claude/settings.json` gets the four hooks."""
    config = str(tmp_path / "farm" / "config")
    settings = str(tmp_path / "farm" / "settings.json")
    sd = str(tmp_path / "state")
    os.makedirs(str(tmp_path / "farm"))
    code, out, err = run_sh(["farm", "--mac-id", "mac-0001",
                             "--statedir", sd, "--config", config,
                             "--settings", settings,
                             "--python", sys.executable])
    assert code == 0, err
    values = agb.read_config(config)
    assert values["mac_id"] == "mac-0001"
    assert values["statedir"] == sd

    with open(settings) as handle:
        hooks = json.load(handle)["hooks"]
    assert set(hooks) == set(["UserPromptSubmit", "PostToolUse",
                              "Notification", "Stop"])
    command = hooks["Stop"][0]["hooks"][0]["command"]
    assert command.startswith("AGB_STATEDIR=")
    assert sd in command and "-S -E" in command


def test_install_sh_farm_refuses_without_a_mac_id(run_sh, tmp_path):
    code, out, err = run_sh(["farm", "--config", str(tmp_path / "config"),
                             "--statedir", str(tmp_path / "state")])
    assert code == 1
    assert "--mac-id is required" in err
    assert "bridge:DOWN" in err
    assert not (tmp_path / "config").exists()


def test_install_sh_farm_can_write_the_config_only(run_sh, tmp_path, agb):
    config = str(tmp_path / "config")
    settings = str(tmp_path / "settings.json")
    code, out, err = run_sh(["farm", "--mac-id", "mac-0001",
                             "--config", config, "--settings", settings,
                             "--statedir", str(tmp_path / "state"),
                             "--no-hooks", "--python", sys.executable])
    assert code == 0, err
    assert agb.read_config(config)["mac_id"] == "mac-0001"
    assert not os.path.exists(settings)


def test_install_sh_dry_run_writes_nothing_at_all(run_sh, mac_args, tmp_path):
    code, out, err = run_sh(mac_args() + ["--dry-run"])
    assert code == 0, err
    assert "dry run" in out
    for path in ("dest", "cfg/config", "agents", "logs"):
        assert not (tmp_path / path).exists(), path


def test_install_sh_rejects_an_unknown_role_and_unknown_options(run_sh,
                                                                mac_args):
    code, out, err = run_sh(["frobnicate"])
    assert code == 1
    assert "unknown role" in err
    code, out, err = run_sh(mac_args() + ["--frobnicate"])
    assert code == 1
    assert "unknown option" in err


def test_install_sh_mac_requires_the_two_values_nothing_can_invent(
        run_sh, mac_args):
    """A bridge that starts, finds no feed host and quietly never connects is
    `agr` failure mode #1 rebuilt from scratch."""
    code, out, err = run_sh(mac_args(**{"--feed-host": None}))
    assert code == 1 and "--feed-host is required" in err
    code, out, err = run_sh(mac_args(**{"--agb-remote-path": None}))
    assert code == 1 and "--agb-remote-path is required" in err


def test_install_sh_refuses_values_a_remote_shell_would_resplit(run_sh,
                                                                mac_args):
    code, out, err = run_sh(mac_args(**{"--feed-host": "box2; rm -rf /"}))
    assert code == 1
    assert "would not survive a remote shell" in err


@pytest.mark.parametrize("option", ["--farm", "--feed-host", "--jump-host",
                                    "--mac-id", "--statedir"])
def test_install_sh_refuses_a_value_ssh_would_read_as_an_option(
        run_sh, mac_args, option):
    """`-oProxyCommand=/tmp/x` is every-character-legal under `shell_safe`'s
    allowed set (`-` has to stay in it: host names and paths carry one), and
    `ssh "$farm" "$@"` hands its first word straight to ssh's option parser --
    so it runs /tmp/x instead of connecting anywhere.

    `--farm` matters most: `--feed-host` and `--jump-host` are refused a second
    time on the Python side (`agb_mac.bridge_ssh_argv`, `agb_ops._ssh_word_ok`,
    both of which reject a leading `-` by name), but `--farm` is consumed only
    by this script, so this is its only gate."""
    code, out, err = run_sh(mac_args(**{option: "-oProxyCommand=/tmp/x"}))
    assert code == 1
    assert "must not start with '-'" in err
    assert "would not survive a remote shell" not in err


def test_the_farm_role_refuses_the_same_jump_host_the_mac_role_does(run_sh,
                                                                    tmp_path):
    """`--jump-host` used to be checked inside `role_mac` only, and the farm
    role takes it directly too (that is how `role_mac` forwards it), so
    `install.sh farm --jump-host -oProxyCommand=/tmp/x` wrote it straight into
    the config. Both consumers refuse it at use, so this was late rather than
    unguarded -- but one value, one rule, and the rule belongs where the value
    is parsed."""
    code, out, err = run_sh(["farm", "--mac-id", "mac-0001",
                             "--jump-host", "-oProxyCommand=/tmp/x",
                             "--config", str(tmp_path / "config"),
                             "--statedir", str(tmp_path / "state"),
                             "--no-hooks", "--python", sys.executable])
    assert code == 1
    assert "must not start with '-'" in err
    assert not (tmp_path / "config").exists()


def test_install_sh_still_allows_a_dash_inside_a_value(run_sh, mac_args,
                                                       tmp_path):
    """The refusal above is about the FIRST character only. `box-2` and
    `/opt/agb-bridge/agb` are ordinary, and a gate that took the whole
    class out would refuse most real host names."""
    code, out, err = run_sh(mac_args(**{
        "--feed-host": "box-2",
        "--agb-remote-path": "/opt/agb-bridge/agb"}))
    assert code == 0, err
    assert "feed_host = box-2" in read_text(tmp_path / "cfg" / "config")


# ---------------------------------------------------------------------------
# the launchd plist
# ---------------------------------------------------------------------------

def test_the_template_is_a_valid_plist_and_carries_every_placeholder():
    """It is parsed, not grepped: a plist `plutil` rejects is a job launchd
    silently never starts."""
    with open(PLIST_TEMPLATE, "rb") as handle:
        parsed = plistlib.loads(handle.read())
    assert parsed["Label"] == "@LABEL@"
    assert parsed["ProgramArguments"] == ["@PYTHON@", "-S", "-E", "@AGB@",
                                          "bridge", "--config", "@CONFIG@"]
    assert parsed["EnvironmentVariables"]["PATH"] == "@PATH@"
    assert "@LOGDIR@" in parsed["StandardOutPath"]


def test_the_templates_config_flag_comes_after_the_command_name(tmp_path):
    """⚠️ The order is the whole thing, and getting it wrong is a restart loop.

    `agb` dispatches on its FIRST argument, so `agb --config X bridge` is not a
    bridge with a flag -- it is the command `--config`, refused as unknown. Under
    `KeepAlive <true/>` launchd would then restart that job once every
    `ThrottleInterval` for ever, and the only evidence would be a log nobody
    reads. Asserted against the real dispatch rather than by eye: the wrong
    order is *run* here and has to fail."""
    with open(PLIST_TEMPLATE, "rb") as handle:
        args = plistlib.loads(handle.read())["ProgramArguments"]
    assert args.index("--config") == args.index("bridge") + 1

    proc = subprocess.Popen(
        [sys.executable, "-S", "-E", conftest.AGB_PATH,
         "--config", str(tmp_path / "config"), "bridge", "--from-stdin",
         "--no-agterm"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE)
    _out, err = conftest.communicate(proc, b"")
    assert proc.returncode != 0                    # the order this test forbids
    assert b"unknown command: --config" in err


def test_the_plist_declares_keepalive_and_runatload():
    with open(PLIST_TEMPLATE, "rb") as handle:
        parsed = plistlib.loads(handle.read())
    # Unconditional: a cleanly exited bridge is exactly the case the `[?]`
    # rendering exists for, and the useful answer is to reconnect.
    assert parsed["KeepAlive"] is True
    assert parsed["RunAtLoad"] is True
    assert parsed["ThrottleInterval"] >= 1


def test_the_rendered_plist_names_the_installed_agb(run_sh, mac_args,
                                                    tmp_path):
    code, out, err = run_sh(mac_args())
    assert code == 0, err
    plist = tmp_path / "agents" / "com.agbridge.plist"
    with open(str(plist), "rb") as handle:
        raw = handle.read()
    parsed = plistlib.loads(raw)
    assert parsed["ProgramArguments"] == [
        sys.executable, "-S", "-E", str(tmp_path / "dest" / "agb"), "bridge",
        "--config", str(tmp_path / "cfg" / "config")]
    assert parsed["StandardOutPath"].startswith(str(tmp_path / "logs"))
    assert parsed["Label"] == "com.agbridge"
    assert b"@" not in raw.split(b"<plist")[1]     # no placeholder survived
    assert (tmp_path / "logs").is_dir()


def test_a_dest_containing_a_double_hyphen_still_renders_valid_xml(
        run_sh, mac_args, tmp_path):
    """⚠️ The exact failure the template's own comment warns about, reproduced.

    The comment used to spell the placeholder names out literally, and `sed
    ... g` substitutes into a comment as happily as into an element -- so an
    install path containing `--` (perfectly legal, and `shell_safe` allows `-`)
    put a double hyphen inside an XML comment, which XML forbids. The old
    post-render guard only grepped for LEFTOVER placeholders, so it passed, and
    launchd silently never starts a job whose plist it cannot parse."""
    dest = tmp_path / "my--tools" / "agbridge"
    code, out, err = run_sh(mac_args(**{"--dest": str(dest)}))
    assert code == 0, err
    plist = tmp_path / "agents" / "com.agbridge.plist"
    with open(str(plist), "rb") as handle:
        raw = handle.read()
    parsed = plistlib.loads(raw)          # raises on invalid XML
    assert parsed["ProgramArguments"][3] == str(dest / "agb")


def test_a_label_containing_xml_metacharacters_is_escaped(run_sh, mac_args,
                                                          tmp_path):
    """`xml_escape` is real code that no test ever fed a value needing it."""
    label = "com.agbridge.a&b<c>d"
    code, out, err = run_sh(mac_args(**{"--label": label}))
    assert code == 0, err
    plist = tmp_path / "agents" / (label + ".plist")
    with open(str(plist), "rb") as handle:
        raw = handle.read()
    assert b"&amp;" in raw and b"&lt;" in raw and b"&gt;" in raw
    assert plistlib.loads(raw)["Label"] == label


def test_a_log_dir_containing_a_sed_metacharacter_survives_rendering(
        run_sh, mac_args, tmp_path):
    """`rep()` escapes what `sed` treats as special in a REPLACEMENT -- `&`,
    the `|` delimiter and a backslash. `&` in a sed replacement means "the
    whole match", so an unescaped one would silently splice the placeholder
    back into the file."""
    logdir = tmp_path / "logs&more"
    code, out, err = run_sh(mac_args(**{"--log-dir": str(logdir)}))
    assert code == 0, err
    with open(str(tmp_path / "agents" / "com.agbridge.plist"), "rb") as handle:
        parsed = plistlib.loads(handle.read())
    assert parsed["StandardOutPath"] == str(logdir / "bridge.log")
    assert parsed["StandardErrorPath"] == str(logdir / "bridge.err.log")


def test_a_template_with_an_unfillable_placeholder_installs_nothing(
        run_sh, mac_args, tmp_path):
    """The refusal path of the post-render guard, which nothing exercised. A
    plist keeping a literal `@SOMETHING@` is a plist whose meaning is a lie, so
    the temp is removed and no file is installed."""
    broken = tmp_path / "dist"
    broken.mkdir()
    shutil.copy(INSTALL_SH, str(tmp_path / "install.sh"))
    with open(PLIST_TEMPLATE) as handle:
        text = handle.read()
    with open(str(broken / "com.agbridge.plist"), "w") as handle:
        handle.write(text.replace("<key>Label</key>",
                                  "<key>@UNFILLED@</key>"))
    for name in DIST_FILES:
        shutil.copy(os.path.join(conftest.REPO_ROOT, name), str(tmp_path))

    code, out, err = run_sh(mac_args(), script=str(tmp_path / "install.sh"))

    assert code != 0
    assert "placeholder" in err
    assert not (tmp_path / "agents" / "com.agbridge.plist").exists()
    assert list((tmp_path / "agents").glob("*.tmp.*")) == []


def test_plutil_lints_the_rendered_plist_where_it_exists(run_sh, mac_args,
                                                         tmp_path, stub_bin):
    """The macOS half of the guard, reachable on Linux only through a stub.
    `plutil` is what actually knows what launchd will accept, so where it
    exists it is asked -- about the RENDERED file, not the template."""
    stub_bin.install("plutil")
    code, out, err = run_sh(mac_args())
    assert code == 0, err
    calls = stub_bin.calls("plutil")
    assert len(calls) == 1
    assert calls[0][0] == "-lint"
    assert calls[0][1].startswith(str(tmp_path / "agents"))
    assert ".tmp." in calls[0][1]                # the temp, before the rename


def test_a_plist_plutil_rejects_installs_nothing(run_sh, mac_args, tmp_path,
                                                 stub_bin):
    """A job launchd silently never starts is the failure this whole tool is
    against, so a `plutil` refusal has to abort the install rather than be
    logged past."""
    stub_bin.install("plutil", exit_code=1)
    code, out, err = run_sh(mac_args())
    assert code != 0
    assert "not a valid plist" in err
    assert not (tmp_path / "agents" / "com.agbridge.plist").exists()
    assert list((tmp_path / "agents").glob("*.tmp.*")) == []


def test_the_plist_template_does_not_name_its_own_placeholders(run_sh,
                                                               mac_args,
                                                               tmp_path):
    """The structural half of the same fix: the guard above only holds while
    the comment does not spell the placeholders out, because `sed` cannot tell
    a comment from an element."""
    with open(PLIST_TEMPLATE) as handle:
        text = handle.read()
    comment = text.split("<!--", 1)[1].split("-->", 1)[0]
    # ⚠️ Every placeholder, and a new one has to be added here BY HAND: a name
    # missing from this list is not a weaker assertion, it is no assertion at
    # all for that placeholder -- the guard passes vacuously for exactly the one
    # nobody thought about, which is the class it was written for. Derived from
    # the template so the count cannot drift, then checked against the literal
    # names so a template that stopped using at-signs could not empty the list.
    named = set(re.findall(r"@[A-Z_][A-Z_]*@", text))
    assert named == set(["@LABEL@", "@PYTHON@", "@AGB@", "@LOGDIR@", "@PATH@",
                         "@CONFIG@"]), named
    for name in sorted(named):
        assert name not in comment, name


def test_the_plist_label_and_paths_are_overridable(run_sh, mac_args, tmp_path):
    code, out, err = run_sh(mac_args(**{"--label": "com.agbridge.test"}))
    assert code == 0, err
    plist = tmp_path / "agents" / "com.agbridge.test.plist"
    with open(str(plist), "rb") as handle:
        assert plistlib.loads(handle.read())["Label"] == "com.agbridge.test"


def test_install_sh_loads_the_plist_through_launchctl(run_sh, mac_args,
                                                      tmp_path, stub_bin):
    stub_bin.install("launchctl")
    argv = [arg for arg in mac_args() if arg != "--no-load"]
    code, out, err = run_sh(argv)
    assert code == 0, err
    calls = stub_bin.calls("launchctl")
    assert [call[0] for call in calls] == ["bootout", "bootstrap"]
    plist = str(tmp_path / "agents" / "com.agbridge.plist")
    assert calls[1][-1] == plist
    assert "bootstrapped" in out


def test_install_sh_falls_back_to_the_legacy_load(run_sh, mac_args, tmp_path,
                                                  stub_bin):
    """`bootstrap` is the modern spelling; older systems only have `load -w`."""
    log = str(tmp_path / "bin" / "launchctl.log")
    stub_bin.install("launchctl", body=(
        "#!/bin/sh\n"
        "{ for a in \"$@\"; do printf '%s\\037' \"$a\"; done; printf '\\n'; }"
        " >> \"" + log + "\"\n"
        "case \"$1\" in bootstrap) exit 5 ;; esac\nexit 0\n"))
    argv = [arg for arg in mac_args() if arg != "--no-load"]
    code, out, err = run_sh(argv)
    assert code == 0, err
    verbs = [call[0] for call in stub_bin.calls("launchctl")]
    assert verbs == ["bootout", "bootstrap", "unload", "load"]
    assert "legacy load -w" in out


def test_install_sh_does_not_load_when_told_not_to(run_sh, mac_args,
                                                   stub_bin):
    stub_bin.install("launchctl")
    code, out, err = run_sh(mac_args())
    assert code == 0, err
    assert stub_bin.calls("launchctl") == []
    assert "not loaded" in out


def test_a_missing_launchctl_writes_the_plist_and_says_so(run_sh, mac_args,
                                                          tmp_path):
    """The third arm of the load branch, and the one this box is actually in:
    `launchctl` is macOS-only. A Linux run reaches it with no stub at all, and
    the plist still has to be written -- an installer that silently skipped the
    file because it could not load it would leave nothing to load later."""
    argv = [arg for arg in mac_args() if arg != "--no-load"]
    code, out, err = run_sh(argv)
    assert code == 0, err
    assert "launchctl not found" in out
    assert (tmp_path / "agents" / "com.agbridge.plist").exists()


def test_a_launchctl_that_can_load_nothing_at_all_is_fatal(run_sh, mac_args,
                                                           tmp_path, stub_bin):
    """`bootstrap` failing falls back to `load -w`; `load -w` failing too is the
    end of the line. A job that is neither bootstrapped nor loaded is the silent
    no-op this whole tool exists to remove, so it exits non-zero rather than
    printing a success line over it."""
    stub_bin.install("launchctl", exit_code=1)
    argv = [arg for arg in mac_args() if arg != "--no-load"]
    code, out, err = run_sh(argv)
    assert code != 0
    assert "could not load" in err
    assert "loaded" not in out.replace("not loaded", "")


# ---------------------------------------------------------------------------
# --instance: a second machine, which shares no disk with the first
# ---------------------------------------------------------------------------
#
# Everything an instance is, is three paths -- a config, a launchd label and a
# log directory -- and all three already had flags. `--instance <name>` is sugar
# that moves them TOGETHER, which is the whole point: a plist and a config that
# disagree is a bridge writing rows against a map nobody reads, with an install
# that reported success. The three refusals below are the other half of the
# same claim, and each of them exists because the failure it prevents is silent.


def _instance_args(mac_args, name="hostb", **over):
    """The instance shape of `mac_args`.

    ⚠️ `--config` and `--log-dir` are dropped, because deriving those two is the
    feature: a test that kept the fixture's pinned values would assert the sugar
    while overriding it. `--launch-agents` and `--statedir` stay pinned -- the
    first keeps the plist inside the test's tree, the second is *required* by
    `--instance` and pinning it is what the refusal test removes.
    """
    args = {"--config": None, "--log-dir": None, "--instance": name}
    args.update(over)
    return mac_args(**args)


def _instance_config(fake_home, name="hostb"):
    return fake_home / ".config" / "agbridge" / name / "config"


def test_an_instance_install_agrees_about_its_label_config_and_logs(
        run_sh, mac_args, tmp_path, fake_home, stub_bin, agb):
    """One flag, and the label, the config and the logs all follow it.

    They are asserted together on purpose: any one of them alone can be right
    while the set is incoherent, and an incoherent set is the failure -- a job
    called `com.agbridge.hostb` reading the default config would drive the
    default rows map from the second machine's feed.
    """
    stub_bin.install("plutil")            # the macOS half of the render guard
    code, out, err = run_sh(_instance_args(mac_args))
    assert code == 0, err

    config = _instance_config(fake_home)
    plist = tmp_path / "agents" / "com.agbridge.hostb.plist"
    parsed = plistlib.loads(read_bytes(plist))       # parses == valid XML
    assert parsed["Label"] == "com.agbridge.hostb"
    assert parsed["ProgramArguments"][-2:] == ["--config", str(config)]
    assert parsed["StandardOutPath"] == str(
        fake_home / "Library" / "Logs" / "agbridge" / "hostb" / "bridge.log")
    # ...and `plutil` was asked about the rendered file, where it exists.
    assert [call[0] for call in stub_bin.calls("plutil")] == ["-lint"]

    # The config is real, and it carries the statedir that was demanded.
    assert agb.read_config(str(config))["statedir"] == str(tmp_path / "state")
    assert "instance: hostb" in out

    # Non-vacuity, and the isolation claim: the DEFAULT instance was not
    # written -- no config beside it, no plist, no log directory.
    assert not (fake_home / ".config" / "agbridge" / "config").exists()
    assert not (tmp_path / "agents" / "com.agbridge.plist").exists()
    assert (fake_home / "Library" / "Logs" / "agbridge" / "hostb").is_dir()


def test_the_farm_hint_for_an_instance_names_that_instances_statedir(
        run_sh, mac_args, tmp_path):
    """A confirmation, not a change: the hint is built from one argv that always
    carried `--statedir`. It matters more here than anywhere else -- the whole
    reason this instance exists is that its farm keeps its state somewhere the
    other one cannot see."""
    code, out, err = run_sh(_instance_args(mac_args))
    assert code == 0, err
    argv = _farm_hint(out)
    assert argv[argv.index("--statedir") + 1] == str(tmp_path / "state")


def test_an_explicit_flag_still_beats_the_instance_sugar(run_sh, mac_args,
                                                         tmp_path, fake_home):
    """Sugar, not a mode: each of the three is still `[ -n ... ] ||`, so an
    operator who wants an instance whose plist lives elsewhere keeps saying
    so."""
    code, out, err = run_sh(_instance_args(
        mac_args, **{"--label": "com.agbridge.custom",
                     "--config": str(tmp_path / "cfg" / "config"),
                     "--log-dir": str(tmp_path / "logs")}))
    assert code == 0, err
    plist = tmp_path / "agents" / "com.agbridge.custom.plist"
    parsed = plistlib.loads(read_bytes(plist))
    assert parsed["ProgramArguments"][-1] == str(tmp_path / "cfg" / "config")
    assert parsed["StandardOutPath"].startswith(str(tmp_path / "logs"))
    assert not _instance_config(fake_home).exists()


def test_an_instance_adopts_the_macs_existing_mac_id(run_sh, mac_args,
                                                     fake_home, agb):
    """Decision 4. The id names THIS MAC, not this connection.

    Each instance's bridge writes `bridge/<mac-id>.beat` inside its OWN
    statedir, and those two statedirs share no disk -- so the same id in both is
    the truth. Minting a second one would leave the new cluster's
    `agb status-line` reading `bridge:DOWN` until every farm host there was
    re-installed with the new id, which is the exact failure `install.sh` prints
    the id to prevent.
    """
    code, out, err = run_sh(mac_args(**{"--config": None}))     # the default
    assert code == 0, err
    default = agb.read_config(str(fake_home / ".config" / "agbridge" / "config"))

    code, out, err = run_sh(_instance_args(mac_args))
    assert code == 0, err
    assert agb.read_config(str(_instance_config(fake_home)))["mac_id"] \
        == default["mac_id"]
    assert "adopted %s" % (default["mac_id"],) in out


def test_the_first_instance_on_a_mac_with_no_default_config_still_mints_one(
        run_sh, mac_args, fake_home, agb):
    """⚠️ The fall-back has to catch a NON-ZERO EXIT, not an empty answer.

    With no default config `resolve_mac_id` *raises* rather than answering
    nothing, and under `set -e` an unguarded command substitution would abort
    the install outright -- so a Mac whose first instance is a named one would
    be unable to install anything at all.
    """
    assert not (fake_home / ".config" / "agbridge" / "config").exists()
    code, out, err = run_sh(_instance_args(mac_args))
    assert code == 0, err
    assert agb.valid_mac_id(
        agb.read_config(str(_instance_config(fake_home)))["mac_id"])
    assert "adopted" not in out


def test_an_instance_without_a_statedir_is_refused_and_installs_nothing(
        run_sh, mac_args, tmp_path, fake_home):
    """⚠️ Decision 6, and the worst failure this plan can produce.

    Without `--statedir`, `agb install-config` falls back to `agb.statedir()`,
    which reads the DEFAULT config -- so the new instance would inherit the
    FIRST machine's farm path: ssh to the right machine, look at the wrong
    directory. `agb feed` would then create that directory over there and report
    an empty farm for ever, which is what `bridge_settings`' required-statedir
    rule exists to prevent, arriving by the one route that rule cannot see.

    Refused before anything is copied, so a failed install leaves no half-tree.
    """
    code, out, err = run_sh(_instance_args(mac_args, **{"--statedir": None}))
    assert code != 0
    assert "--statedir" in err
    assert not _instance_config(fake_home).exists()
    assert not (tmp_path / "dest").exists()
    assert not (tmp_path / "agents").exists()


@pytest.mark.parametrize("name", ["../../evil", "a/b", ".hidden", "-x",
                                  "host b", "host;rm"])
def test_an_instance_name_that_would_escape_its_own_directories_is_refused(
        run_sh, mac_args, tmp_path, fake_home, name):
    """⚠️ `shell_safe` is the wrong gate here and would pass `../../evil`.

    It keeps `.` and `/` for the inside of paths and host names, which is right
    for a value on a command line and wrong for this one: the name becomes a
    launchd label component, a plist FILENAME, a log directory and a config
    directory, so a `/` or a leading `.` in it writes all four somewhere other
    than where they were meant to go -- and reports success.
    """
    code, out, err = run_sh(_instance_args(mac_args, name=name))
    assert code != 0
    assert "--instance" in err
    assert not (tmp_path / "dest").exists()
    assert not (tmp_path / "agents").exists()
    # Non-vacuity, and the escape itself: `../../evil` would resolve to
    # `$HOME/evil/config`, which is neither under `.config/agbridge` nor
    # anywhere this test names -- so the whole home is swept for a config file
    # rather than one expected path.
    assert list(fake_home.rglob("config")) == []


def test_the_farm_role_refuses_the_instance_sugar(run_sh, tmp_path):
    """⚠️ The option loop is ROLE-AGNOSTIC and `$config` is used by both roles.

    ⚠️ `mac_args` cannot express this: it hardcodes `argv = ["mac", ...]`.

    Nothing on the farm reads a per-instance config -- `agb hook` and
    `agb status-line` resolve `agb.config_path()` and nothing else -- so
    `install.sh farm --instance x` would write a real config to a path no
    farm-side reader ever opens, and say `wrote:` about it.
    """
    code, out, err = run_sh(["farm", "--mac-id", "mac-0001",
                             "--instance", "hostb",
                             "--statedir", str(tmp_path / "state"),
                             "--config", str(tmp_path / "config"),
                             "--settings", str(tmp_path / "settings.json"),
                             "--python", sys.executable])
    assert code != 0
    assert "--instance" in err and "mac" in err
    assert not (tmp_path / "config").exists()
    assert not (tmp_path / "settings.json").exists()


def test_a_default_install_is_the_plist_it_always_was_plus_the_config_flag(
        run_sh, mac_args, tmp_path, fake_home):
    """The upgrade claim, stated key by key.

    The config flag renders for EVERY install, the default one included
    (decision 5: the installer gets one path, not a conditional exercised only
    on the second machine) -- so the guarantee for an existing install is not
    "no change" but "this change and no other".
    """
    code, out, err = run_sh(mac_args(**{"--config": None}))
    assert code == 0, err
    parsed = plistlib.loads(
        read_bytes(tmp_path / "agents" / "com.agbridge.plist"))
    assert parsed["ProgramArguments"] == [
        sys.executable, "-S", "-E", str(tmp_path / "dest" / "agb"), "bridge",
        "--config", str(fake_home / ".config" / "agbridge" / "config")]
    assert parsed["Label"] == "com.agbridge"
    assert parsed["StandardOutPath"] == str(tmp_path / "logs" / "bridge.log")
    assert parsed["StandardErrorPath"] == str(tmp_path / "logs"
                                              / "bridge.err.log")
    assert parsed["KeepAlive"] is True
    assert parsed["RunAtLoad"] is True
    assert parsed["ThrottleInterval"] == 10
    assert parsed["ProcessType"] == "Background"
    assert parsed["WorkingDirectory"] == "/tmp"
    assert parsed["EnvironmentVariables"]["PATH"].startswith("/opt/homebrew")
    assert "instance:" not in out


# ---------------------------------------------------------------------------
# the refusals: every `die` on the way in
# ---------------------------------------------------------------------------
#
# These are the paths that stop an install rather than performing one, and they
# are the half that had almost no coverage: the argument checks, the two `usage`
# exits and the `agb_mac` arm of `verify_tree`. A refusal that stopped refusing
# is silent by construction -- the install just proceeds.

def test_a_tree_that_cannot_run_agb_mac_is_refused_by_name(run_sh, tmp_path):
    """The `agb_mac` arm of `verify_tree`, which nothing reached: the sibling
    test drops `agb_ops`, so `status-line` fails first and the `bridge` probe
    below it never runs. Here `agb_ops` is present and `agb_mac` is not, so the
    third probe is the one that has to catch it -- and has to say which file."""
    broken = tmp_path / "no_mac"
    broken.mkdir()
    for name in ("agb", "agb_ops"):
        shutil.copy(os.path.join(conftest.REPO_ROOT, name), str(broken / name))
    code, out, err = run_sh(["farm", "--mac-id", "mac-0001",
                             "--agb", str(broken / "agb"),
                             "--config", str(tmp_path / "config"),
                             "--statedir", str(tmp_path / "state"),
                             "--settings", str(tmp_path / "settings.json"),
                             "--python", sys.executable])
    assert code == 1
    assert "agb_mac" in err
    assert "agb_ops" not in err                  # it really is the bridge probe
    assert not (tmp_path / "config").exists()


@pytest.mark.parametrize("argv", [[], ["-h"], ["--help"], ["help"]])
def test_no_role_and_the_help_flags_print_usage_and_exit_two(run_sh, argv):
    """Exit 2, not 0: `install.sh` with no role has installed nothing, and a
    caller that scripts it must be able to tell that from a success."""
    code, out, err = run_sh(argv)
    assert code == 2, (out, err)
    assert "usage: install.sh mac" in err
    assert "install.sh farm" in err


@pytest.mark.parametrize("argv", [["mac", "-h"], ["farm", "--help"]])
def test_the_help_flags_work_after_a_role_too(run_sh, argv):
    code, out, err = run_sh(argv)
    assert code == 2, (out, err)
    assert "usage: install.sh mac" in err


@pytest.mark.parametrize("flag,value", [
    ("--agb-remote-path", "relative/agb"),
    ("--agb-remote-path", "./agb"),
    ("--remote-python", "python3"),
])
def test_a_relative_path_where_an_absolute_one_is_required_is_refused(
        run_sh, mac_args, tmp_path, flag, value):
    """`absolute()`. These values are resolved on a *different machine*, in a
    working directory nothing here can predict, so a relative one does not fail
    at install time -- it fails at the first ssh, months later."""
    code, out, err = run_sh(mac_args(**{flag: value}))
    assert code == 1
    assert "absolute path" in err
    assert not (tmp_path / "dest").exists()


def test_a_relative_agb_is_refused_on_the_farm_side_too(run_sh, tmp_path):
    code, out, err = run_sh(["farm", "--mac-id", "mac-0001",
                             "--agb", "agb",
                             "--config", str(tmp_path / "config"),
                             "--python", sys.executable])
    assert code == 1
    assert "absolute path" in err
    assert not (tmp_path / "config").exists()


def test_a_relative_interpreter_is_refused(run_sh, mac_args, tmp_path):
    """The interpreter is baked verbatim into the hook command, which runs with
    no reliable working directory and no PATH worth trusting."""
    code, out, err = run_sh(mac_args(**{"--python": "python3"}))
    assert code == 1
    assert "absolute path" in err


def test_an_interpreter_that_is_not_executable_is_refused(run_sh, mac_args,
                                                          tmp_path):
    fake = tmp_path / "notpython"
    fake.write_text("")
    code, out, err = run_sh(mac_args(**{"--python": str(fake)}))
    assert code == 1
    assert "not executable" in err


def test_find_python_falls_back_when_the_path_has_none(run_sh, mac_args,
                                                       tmp_path):
    """`--python` omitted and `command -v python3` answering nothing: the four
    absolute candidates are the fallback, and one of them must be found. The
    PATH still carries the coreutils `install.sh` itself needs -- only `python3`
    is hidden, by pointing PATH at a directory that shadows it."""
    body = extract_sh_function("find_python")
    script = ("PATH=\n"
              "die() { printf 'install.sh: %%s\\n' \"$*\" >&2; exit 1; }\n"
              "%s\n"
              "find_python\n" % (body,))
    proc = subprocess.Popen(["/bin/sh", "-c", script],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, err = conftest.communicate(proc)
    assert proc.returncode == 0, err
    assert out.startswith(b"/")                  # an absolute candidate
    assert os.access(out.decode(), os.X_OK)


def test_find_python_refuses_rather_than_guessing(run_sh, tmp_path):
    """The `die` at the end of `find_python`. On this box `/usr/bin/python3`
    exists, so the refusal is unreachable end to end -- it is reached by running
    the extracted function with every absolute path in it re-rooted under a
    directory that does not exist. Re-rooted by rewriting the extracted text
    rather than by listing the candidates here, so a fifth one added later is
    covered without touching this test."""
    root = str(tmp_path / "nowhere")
    body = re.sub(r"(?<=[ \t])/(?=[A-Za-z])", root + "/",
                  extract_sh_function("find_python"))
    assert body.count(root) >= 4                 # all four candidates moved
    script = ("PATH=%s\n"
              "die() { printf 'install.sh: %%s\\n' \"$*\" >&2; exit 1; }\n"
              "%s\n"
              "find_python\n" % (root, body))
    proc = subprocess.Popen(["/bin/sh", "-c", script],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, err = conftest.communicate(proc)
    assert proc.returncode == 1
    assert b"no python3 found" in err
    assert b"--python" in err                    # it names the way out
    assert out == b""                            # and prints no candidate


# ---------------------------------------------------------------------------
# safety: nothing here may touch the real machine
# ---------------------------------------------------------------------------

def test_the_real_dotfiles_are_untouched(run_sh, mac_args, tmp_path, agb):
    """The installer is real. Every path it writes is redirected into the
    test's own tree, `$HOME` is faked, and this asserts the two files that
    would actually matter are byte for byte what they were."""
    code, _out, err = run_sh(mac_args())
    assert code == 0, err
    code, _out, err = run_sh(["farm", "--mac-id", "mac-0001",
                              "--config", str(tmp_path / "c2"),
                              "--settings", str(tmp_path / "s2.json"),
                              "--statedir", str(tmp_path / "state"),
                              "--python", sys.executable])
    assert code == 0, err
    assert digest(REAL_SETTINGS) == REAL_SETTINGS_DIGEST
    assert digest(REAL_CONFIG) == REAL_CONFIG_DIGEST


def test_the_settings_seam_survives_the_subprocess_boundary(run_sh, tmp_path,
                                                            fake_home):
    """`--settings` is the only seam `monkeypatch` cannot provide across a
    process boundary, and `install.sh farm` is exactly such a boundary."""
    settings = str(tmp_path / "own" / "settings.json")
    os.makedirs(str(tmp_path / "own"))
    code, out, err = run_sh(["farm", "--mac-id", "mac-0001",
                             "--config", str(tmp_path / "config"),
                             "--settings", settings,
                             "--statedir", str(tmp_path / "state"),
                             "--python", sys.executable])
    assert code == 0, err
    assert os.path.exists(settings)
    assert not os.path.exists(os.path.join(str(fake_home), ".claude",
                                           "settings.json"))


# ---------------------------------------------------------------------------
# structural guards
# ---------------------------------------------------------------------------

def test_install_config_is_reached_through_the_one_shared_operator_door(
        agb_tree, agb):
    """Task 9b is the first new *name* since Task 6b built the door, and it
    costs `agb` one table entry and one usage line -- no `cmd_install_config`,
    no dispatch arm."""
    funcs = conftest.functions(agb_tree)
    assert "install-config" in agb.OPS_COMMANDS
    assert "install-config" in conftest.usage_commands(agb)
    assert "install-config" in agb.USAGE
    assert "cmd_install_config" not in funcs
    assert "run_install_config" not in funcs


def test_agb_is_still_under_the_parse_cap(agb_source):
    """Every hook re-parses this file, and Task 9b spends ~130 bytes of the
    remaining budget on a table entry and a usage line."""
    assert len(agb_source) < conftest.AGB_PARSE_BUDGET


def test_no_hook_path_function_can_reach_the_config_installer(all_trees):
    """It rewrites a file in `$HOME`. None of that may be one call away from a
    hook."""
    funcs = conftest.functions(*all_trees)
    reachable = conftest.reachable_from(funcs, "cmd_hook")
    assert "hook_apply" in reachable                   # the walk really ran
    for name in ("run_install_config", "install_config_values",
                 "merge_config_text", "generate_mac_id", "read_config_text"):
        assert name not in reachable, name


def test_the_config_installer_writes_through_the_one_shared_writer(all_trees):
    """Same rule as `install-hooks`: back the old file up, then temp+rename.
    An `open(..., "w")` here would be the truncate window applied to a file the
    user hand-edits."""
    funcs = conftest.functions(*all_trees)
    reachable = conftest.reachable_from(funcs, "run_install_config")
    writers = set()
    for name in reachable:
        for base, attr in conftest.calls(funcs[name]):
            if attr in ("atomic_write", "write_in_place"):
                writers.add((name, attr))
            assert (base, attr) != (None, "open"), name
    assert writers == set([("write_settings", "atomic_write")])


def test_the_config_installer_never_touches_the_statedir(all_trees):
    """On the Mac the statedir names a directory on another machine
    (constraint #10), so creating it here would either fail or, worse, silently
    create a local decoy that everything then agrees about."""
    funcs = conftest.functions(*all_trees)
    reachable = conftest.reachable_from(funcs, "run_install_config")
    assert "statedir" in reachable                     # resolving it is fine
    for name in ("ensure_statedir", "ensure_session_dir", "_mkdir_owned",
                 "reap_entry", "prune_remove", "rebuild_marker", "sweep_host"):
        assert name not in reachable, name
    # Stated as "the only reachable unlink is the shared atomic write's own
    # temp cleanup" rather than "no unlink at all": `atomic_write` removes its
    # temp when a rename fails, and a guard loose enough to trip on that would
    # have to be relaxed rather than fixed the first time it fired.
    unlinkers = set(name for name in reachable
                    if ("os", "unlink") in conftest.calls(funcs[name]))
    assert unlinkers == set(["_unlink_quiet"])
    callers = set(name for name in reachable
                  if "_unlink_quiet" in [attr for _base, attr
                                         in conftest.calls(funcs[name])])
    assert callers == set(["atomic_write"])


def test_the_installer_scripts_are_part_of_the_repo():
    """`install.sh` and the plist template are source, not build output: a
    `.gitignore` rule that swallowed `dist/` would make the plist invisible to
    every checkout but this one."""
    assert os.access(INSTALL_SH, os.X_OK)
    assert os.path.isfile(PLIST_TEMPLATE)
    proc = subprocess.Popen(["git", "check-ignore", INSTALL_SH,
                             PLIST_TEMPLATE], cwd=conftest.REPO_ROOT,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, _err = conftest.communicate(proc)
    assert out == b"", out


def test_install_sh_is_posix_sh(agb):
    """macOS ships bash 3.2 and the farm's login shell is tcsh; the script
    assumes neither."""
    with open(INSTALL_SH) as handle:
        first = handle.readline().strip()
    assert first == "#!/bin/sh"
    proc = subprocess.Popen(["/bin/sh", "-n", INSTALL_SH],
                            stderr=subprocess.PIPE)
    _out, err = conftest.communicate(proc)
    assert proc.returncode == 0, err


def test_install_sh_names_all_three_files_and_both_configs():
    """The two facts the script exists to encode, asserted where a future edit
    that drops one would be caught."""
    with open(INSTALL_SH) as handle:
        text = handle.read()
    assert 'FILES="agb agb_mac agb_ops"' in text
    assert "install-config" in text and "install-hooks" in text


# ---------------------------------------------------------------------------
# --probe: deriving host_<hostname> from the feed host
# ---------------------------------------------------------------------------

def _probing_args(mac_args, **over):
    """`mac_args` opts out of probing; these tests are about it, so opt back in."""
    argv = mac_args(**over)
    return [a for a in argv if a != "--no-probe"]


def _ssh_answering(stub_bin, hostname, exit_code=0):
    """An ssh stub that records its argv AND answers the `hostname -s` probe."""
    log = stub_bin.path / "ssh.log"
    stub_bin.install("ssh", body=(
        "#!/bin/sh\n"
        "{ for a in \"$@\"; do printf '%s\\037' \"$a\"; done; printf '\\n'; } >> \""
        + str(log) + "\"\n"
        "printf '%s\\n' " + hostname + "\n"
        "exit " + str(exit_code) + "\n"))


def test_the_probe_maps_the_feed_hosts_real_hostname_to_its_ssh_alias(
        run_sh, mac_args, stub_bin, tmp_path, agb):
    """A record's `host` is the farm's hostname; `--feed-host` is an ssh alias.
    Without `host_<hostname>` the row renders and then refuses to open, which is
    a confusing place to discover a missing config line -- so the one mapping
    that can be derived is derived."""
    _ssh_answering(stub_bin, "buildbox07")
    code, out, err = run_sh(_probing_args(mac_args))
    assert code == 0, err
    assert "buildbox07" in out
    values = agb.read_config(str(tmp_path / "cfg" / "config"))
    assert values["host_buildbox07"] == "box2"


def test_the_probe_asks_the_feed_host_and_nothing_else(
        run_sh, mac_args, stub_bin):
    """Read-only, one call, and to the feed host by name."""
    _ssh_answering(stub_bin, "buildbox07")
    assert run_sh(_probing_args(mac_args))[0] == 0
    calls = stub_bin.calls("ssh")
    assert len(calls) == 1
    assert calls[0][-2:] == ["box2", "hostname -s"]


def test_an_explicit_host_mapping_beats_the_probe(
        run_sh, mac_args, stub_bin, tmp_path, agb):
    """The operator's own answer is never overwritten by a derived one."""
    _ssh_answering(stub_bin, "buildbox07")
    code, out, err = run_sh(_probing_args(mac_args,
                                          **{"--host": "buildbox07=chosen"}))
    assert code == 0, err
    values = agb.read_config(str(tmp_path / "cfg" / "config"))
    assert values["host_buildbox07"] == "chosen"
    assert "already mapped explicitly" in out


def test_a_failing_probe_is_not_fatal_and_says_what_to_do(
        run_sh, mac_args, stub_bin, tmp_path, agb):
    """The probe is a convenience. A farm that will not answer must not stop an
    install -- it must leave a working config and name the missing flag."""
    _ssh_answering(stub_bin, "", exit_code=255)
    code, out, err = run_sh(_probing_args(mac_args))
    assert code == 0, err
    assert "--host" in out
    values = agb.read_config(str(tmp_path / "cfg" / "config"))
    assert values["mac_id"]
    assert not [k for k in values if k.startswith("host_")]


def test_a_garbage_hostname_is_refused_rather_than_written(
        run_sh, mac_args, stub_bin, tmp_path, agb):
    """Whatever comes back becomes a CONFIG KEY (`host_<name>`), so it is
    validated before it is trusted -- an ssh banner or a shell error message
    must not end up as one."""
    _ssh_answering(stub_bin, "'not a hostname; rm -rf /'")
    code, out, err = run_sh(_probing_args(mac_args))
    assert code == 0, err
    values = agb.read_config(str(tmp_path / "cfg" / "config"))
    assert not [k for k in values if k.startswith("host_")]
    assert "--host" in out


def test_no_probe_makes_no_ssh_call_at_all(run_sh, mac_args, stub_bin):
    _ssh_answering(stub_bin, "buildbox07")
    assert run_sh(mac_args())[0] == 0          # mac_args already has --no-probe
    assert stub_bin.calls("ssh") == []


# ---------------------------------------------------------------------------
# the `agb` wrapper -- what makes every doc example true
# ---------------------------------------------------------------------------

def _farm(tmp_path, **over):
    """The farm role with every path pinned inside tmp_path."""
    args = {"--mac-id": "mac-0001",
            "--config": str(tmp_path / "config"),
            "--statedir": str(tmp_path / "state"),
            "--settings": str(tmp_path / "settings.json"),
            "--python": sys.executable}
    args.update(over)
    argv = ["farm"]
    for name, value in sorted(args.items()):
        argv.extend([name, value])
    return argv


def test_the_farm_role_writes_a_working_agb_wrapper(run_sh, tmp_path):
    """`agb` has no shebang and is not executable on purpose (constraint #1):
    a hook must pass `-S -E`, and neither a shebang nor `env` can. Every doc
    writes `agb doctor`, so something has to make that true."""
    bindir = tmp_path / "bin"
    code, out, err = run_sh(_farm(tmp_path, **{"--bin-dir": str(bindir)}))
    assert code == 0, err
    wrapper = bindir / "agb"
    assert wrapper.exists()
    assert os.access(str(wrapper), os.X_OK)
    assert "wrapper:" in out

    # ...and it is not merely present: it runs, through all three files.
    proc = subprocess.Popen([str(wrapper), "version"], stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, stdin=subprocess.PIPE)
    stdout, stderr = conftest.communicate(proc, b"")
    assert proc.returncode == 0, stderr
    assert stdout.startswith(b"agb ")


def test_the_wrapper_passes_the_interpreter_flags(run_sh, tmp_path):
    """`-S -E` is the whole reason the wrapper exists rather than a symlink."""
    bindir = tmp_path / "bin"
    run_sh(_farm(tmp_path, **{"--bin-dir": str(bindir)}))
    text = (bindir / "agb").read_text()
    assert "-S" in text and "-E" in text
    assert text.startswith("#!")


def test_a_bin_dir_that_is_not_on_path_is_called_out(run_sh, tmp_path):
    """A wrapper nobody can reach is the silent-no-op shape this tool exists to
    remove, so its absence from $PATH is said rather than left to be noticed."""
    bindir = tmp_path / "nowhere"
    _code, out, _err = run_sh(_farm(tmp_path, **{"--bin-dir": str(bindir)}))
    assert "not on your $PATH" in out


def test_no_wrapper_writes_none(run_sh, tmp_path):
    bindir = tmp_path / "bin"
    argv = _farm(tmp_path, **{"--bin-dir": str(bindir)}) + ["--no-wrapper"]
    code, out, err = run_sh(argv)
    assert code == 0, err
    assert not (bindir / "agb").exists()
    assert "wrapper:" not in out


def test_a_dry_run_writes_no_wrapper(run_sh, tmp_path):
    bindir = tmp_path / "bin"
    argv = _farm(tmp_path, **{"--bin-dir": str(bindir)}) + ["--dry-run"]
    _code, out, _err = run_sh(argv)
    assert not (bindir / "agb").exists()
    assert "would write the wrapper" in out
