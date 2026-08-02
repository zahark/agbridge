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

# What `mac_args` derives, spelled once. `install.sh mac` requires `--instance`,
# the fixture names it `HOST`, and `$label` defaults to `$DEFAULT_LABEL.$instance`
# -- so the launchd label, and with it the plist FILENAME, follow the fixture
# rather than being constants of the tool. Every test that names the rendered
# plist is naming this, and a test still spelling `com.agbridge.plist` is naming
# a file no successful install can now produce.
MAC_LABEL = "com.agbridge." + HOST
MAC_PLIST = MAC_LABEL + ".plist"

INSTALL_SH = os.path.join(conftest.REPO_ROOT, "install.sh")
REFRESH_SH = os.path.join(conftest.REPO_ROOT, "agb-refresh")
# ⚠️ The template's FILENAME is not a claim about which instance it renders.
# Its placeholders include `@LABEL@` and `@CONFIG@`, so one file renders every
# instance's plist -- `com.agbridge.plist` is only what the first one happened
# to be called. This constant, and the two shape oracles in
# `tests/test_agb_refresh.py` that pin a fixture's argv against it, deliberately
# keep pointing here even though no install now writes a file by that name.
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
# --print-statedir: the same channel, but a *pure query*
#
# `install.sh mac --instance <name>` reads it to adopt an existing instance's
# statedir instead of re-demanding `--statedir` on every upgrade, and the run
# has to write nothing at all.
#
# ⚠️ **Three statuses, not two**, and this comment used to claim two: 0 with the
# value on stdout, `PRINT_STATEDIR_NONE` for "this file carries none of its
# own", and 1 for "I could not read it" -- unreadable, not UTF-8, an option this
# `agb` does not know. The installer swallows only the middle one. Folding it
# into "non-zero" reported an unreadable config as *carries none to adopt* and
# sent the operator after `--statedir`, a flag that was not the problem: the
# same "'I could not answer' is not 'the answer is nothing'" this project keeps
# paying for.
# ---------------------------------------------------------------------------

def print_statedir_argv(config, *extra):
    return ["install-config", "--config", str(config),
            "--print-statedir"] + list(extra)


def test_print_statedir_puts_only_the_files_own_statedir_on_stdout(
        run_agb, config_path):
    config = config_path("statedir = /shared/.agbridge\nmac_id = m-0001\n")
    code, out, err = run_agb(print_statedir_argv(config))
    assert code == 0
    assert out.decode() == "/shared/.agbridge\n"   # nothing else on stdout
    assert err == b""                             # and no report either


def test_print_statedir_is_refused_when_the_file_carries_no_statedir(
        run_agb, config_path, ops):
    """Non-zero must mean *no own statedir*, and stdout must stay empty: the
    installer would otherwise adopt an error message as a path.

    The status is `PRINT_STATEDIR_NONE` **exactly**, not merely non-zero -- see
    the test below for the half that distinction buys.
    """
    config = config_path("mac_id = m-0001\n")
    code, out, err = run_agb(print_statedir_argv(config))
    assert code == ops.PRINT_STATEDIR_NONE
    assert out == b""
    assert b"statedir" in err


def test_a_file_that_cannot_be_read_is_not_the_same_answer_as_carrying_none(
        run_agb, config_path, ops):
    """⚠️ The two must not share a status, and they did.

    `install.sh` swallows *no own statedir* -- that is an answer, and the flag
    it asks for next is the right one. It must NOT swallow "I could not read
    that file": measured, a config the installer could not read was reported as
    `carries none to adopt` and the operator was sent after `--statedir`, which
    would then have installed the instance against a config nothing can read.
    Same shape as `agb-refresh`'s four-status `plist_read_ok` rule, and the same
    reason.

    Not-UTF-8 rather than `chmod 000`, deliberately: a suite that happens to run
    as root reads a mode-000 file perfectly well, and a guard that quietly stops
    firing is worse than one that was never written.
    """
    config = config_path("")
    with open(str(config), "wb") as handle:
        handle.write(b"statedir = /shared/\xff\xfe\n")
    code, out, err = run_agb(print_statedir_argv(config))
    assert code != 0
    assert code != ops.PRINT_STATEDIR_NONE     # ...and this is the whole point
    assert out == b""
    assert b"UTF-8" in err
    assert str(config).encode() in err         # it names the file it could not read


def test_print_statedir_is_refused_when_the_config_does_not_exist(run_agb,
                                                                  tmp_path):
    missing = tmp_path / "cfg" / "config"
    code, out, err = run_agb(print_statedir_argv(missing))
    assert code != 0
    assert out == b""
    assert b"does not exist" in err


def test_print_statedir_answers_the_named_file_never_the_default_one(
        run_agb, instance_config, agb):
    """Both files present, holding **different** statedirs, so the answer
    cannot be right by coincidence.

    This is the whole point of the flag: `install_config_values`' own
    precedence ends `or agb.statedir()`, which reads the *default-path* config
    -- so an instance with no statedir of its own would silently be told to use
    another cluster's directory, and exit 0 saying so.
    """
    instance_config(None, "statedir = /shared/DEFAULT\n")
    named = instance_config("hostb", "statedir = /shared/HOSTB\n")
    assert agb.read_config(agb.config_path())["statedir"] == "/shared/DEFAULT"

    code, out, _err = run_agb(print_statedir_argv(named))
    assert code == 0
    assert out.decode() == "/shared/HOSTB\n"

    # ... and the fallback really is reachable, so the assertion above is not
    # asserting the absence of something that could never have happened: the
    # ordinary write path, on an instance with no statedir of its own, does
    # report the default config's.
    empty = instance_config("hostc", "mac_id = m-0001\n")
    code, out, _err = run_agb(print_statedir_argv(empty))
    assert code != 0 and out == b""
    code, out, _err = run_agb(["install-config", "--config", str(empty),
                               "--dry-run"])
    assert code == 0
    assert "set:      statedir = /shared/DEFAULT" in out.decode()


def test_print_statedir_answers_a_config_that_has_no_mac_id(run_agb,
                                                             config_path):
    """The measured regression behind the placement: run below
    `install_config_values` and this file raises MAC_ID_MISSING_NOTE instead of
    answering, so `install.sh` would demand `--statedir` for a config that
    carries one."""
    config = config_path("statedir = /shared/.agbridge\n")
    code, out, _err = run_agb(print_statedir_argv(config))
    assert code == 0
    assert out.decode() == "/shared/.agbridge\n"


@pytest.mark.parametrize("text,answers", [
    ("statedir = /shared/.agbridge\nmac_id = m-0001\n", True),
    ("mac_id = m-0001\n", False),
])
def test_print_statedir_leaves_the_config_byte_identical(run_agb, config_path,
                                                         text, answers):
    """**Without** `--dry-run`, in both the answering and the "carries none"
    case.

    The flag is read-only by construction, not by remembering to pass a flag:
    measured, a version that emitted after `write_settings` left a
    statedir-less config *rewritten with the default config's statedir* while
    exiting non-zero -- the failure the flag exists to prevent, caused by the
    flag.
    """
    config = config_path(text)
    before = read_bytes(config)
    code, _out, _err = run_agb(print_statedir_argv(config))
    assert (code == 0) is answers
    assert read_bytes(config) == before
    assert os.listdir(os.path.dirname(config)) == ["config"]   # no backup


def test_the_two_print_flags_are_refused_together(install_config, agb):
    """One stdout, two unlabelled answers: a caller reading "the line" would
    get the other one's and could not tell."""
    with pytest.raises(agb.AgbError) as excinfo:
        install_config("--print-mac-id", "--print-statedir")
    assert "--print-statedir" in str(excinfo.value)
    assert "--print-mac-id" in str(excinfo.value)


def config_option_names(ops):
    """Every option name `parse_config_args` knows, from its own tables.

    Enumerated rather than written out: `CONFIG_VALUE_ARGS` has seven entries
    and a hand-kept list silently misses `--agb-remote-path`, `--remote-python`
    and `--jump-host` -- and misses whatever is added next.
    """
    return sorted(list(ops.CONFIG_VALUE_ARGS) + list(ops.CONFIG_FLAGS)
                  + [ops.CONFIG_HOST_ARG])


def test_print_statedir_refuses_every_option_that_would_write(ops, agb,
                                                              config_path):
    """Allowed set: `--config` and `--dry-run`. Everything else is refused
    rather than ignored -- measured, `--statedir /new --feed-host zzz
    --print-statedir` printed the *old* value, exited 0 and wrote nothing.
    "You asked me to write and I silently did not" is the same family as
    "'I could not answer' is not 'the answer is nothing'".
    """
    names = config_option_names(ops)
    assert len(names) >= 11                     # non-vacuity: the tables ran
    for expected in ("--agb-remote-path", "--remote-python", "--jump-host",
                     "--host", "--generate-mac-id"):
        assert expected in names                # the ones a hand list misses

    config = config_path("statedir = /shared/.agbridge\n")
    before = read_bytes(config)
    # ⚠️ The allowed set is READ from the parser, for the same reason the option
    # names above are: a hand-written tuple here would be a second copy of
    # `PRINT_STATEDIR_ALLOWED` inside the test whose own docstring argues
    # against hand-kept lists, and widening the real one would make this test
    # fail rather than check the widening.
    allowed = ops.PRINT_STATEDIR_ALLOWED
    assert set(allowed) <= set(names)            # non-vacuity: same vocabulary
    assert "--print-statedir" in allowed and len(allowed) < len(names)
    # ⚠️ AND THE LITERAL, which is the half reading the constant cannot be.
    # Read alone, `allowed` turns the loop below into "whatever the parser
    # permits, parses" -- measured: appending `--feed-host` and
    # `--generate-mac-id` to `PRINT_STATEDIR_ALLOWED` widens this query back
    # into a silent write and the whole suite still passes. A hand-written
    # tuple alone has the opposite failure, drifting from the parser's tables
    # with nothing to say so. Both are asserted, and they are different
    # properties: the literal pins WHICH names may ever be exempt, the constant
    # is what the loop exercises, and this equality is what stops the two
    # diverging. Widening the query is therefore a deliberate edit here, in a
    # test whose docstring says why the set is small.
    assert set(allowed) == set(["--print-statedir", "--config", "--dry-run"])
    refused = []
    for name in names:
        # `a=b` satisfies `--host`'s own `<name>=<target>` check too, so every
        # value-taking flag reaches the refusal rather than an earlier one.
        extra = [name] if name in ops.CONFIG_FLAGS else [name, "a=b"]
        argv = ["--config", str(config), "--print-statedir"] + extra
        if name in allowed:
            assert ops.parse_config_args(argv)   # parses, does not raise
            continue
        with pytest.raises(agb.AgbError) as excinfo:
            ops.parse_config_args(argv)
        assert name in str(excinfo.value)
        refused.append(name)
    assert len(refused) == len(names) - len(allowed)
    # ⚠️ This one is about the PARSER and cannot say more: it never opens a
    # file, so "a refusal writes nothing" is trivially true here. The
    # behavioural half is the test below, which runs the command.
    assert read_bytes(config) == before


def test_a_refused_print_statedir_really_does_write_nothing(run_agb,
                                                            config_path):
    """⚠️ The companion the parser test above cannot be.

    `ops.parse_config_args(argv)` raises before anything is opened, so its
    `read_bytes(config) == before` assertion holds with the whole write path
    deleted -- it reads as end-to-end coverage of a claim nothing exercises.
    This runs the real command, with the option that measured worst: a
    `--statedir` beside `--print-statedir` used to print the *old* value, exit 0
    and write nothing, which is "you asked me to write and I silently did not".
    """
    config = config_path("statedir = /shared/OLD\nmac_id = m-0001\n")
    before = read_bytes(config)
    code, out, err = run_agb(print_statedir_argv(config, "--statedir", "/new"))
    assert code != 0
    assert out == b""                             # not the old value either
    assert b"--statedir" in err
    assert read_bytes(config) == before
    assert os.listdir(os.path.dirname(config)) == ["config"]   # and no backup


def test_print_statedir_accepts_config_and_dry_run(run_agb, config_path):
    """The two exemptions, exercised end to end rather than only through the
    parser: `--config` names *which* file to read (it is exactly what
    `install.sh` passes) and `--dry-run` is a no-op for a read, so refusing the
    obvious first guess would only trap the caller."""
    config = config_path("statedir = /shared/.agbridge\n")
    code, out, _err = run_agb(print_statedir_argv(config, "--dry-run"))
    assert code == 0
    assert out.decode() == "/shared/.agbridge\n"


# A locale the machine does not have falls back to `C`, so the `C` arm is not
# redundant with the third: it is the one that bites everywhere. Same three
# names as the two sibling guards (`tests/test_bridge_rows.py`,
# `tests/test_agb_refresh.py`) on purpose -- three copies of one rule that
# should move together.
NON_ASCII_LOCALES = ["C", "POSIX", "en_US.ISO-8859-1"]


@pytest.mark.parametrize("locale", NON_ASCII_LOCALES)
def test_print_statedir_answers_in_bytes_whatever_the_locale_says(run_agb,
                                                                  config_path,
                                                                  locale):
    """⚠️ The answer is UTF-8 **bytes** on `sys.stdout.buffer`, never
    `sys.stdout.write` -- the third value in this tool to owe that rule, after
    `agb instances --arg` (contract 1) and for the same reason.

    `-E` ignores `PYTHON*` and does not touch `LC_ALL`, so the locale picks
    stdout's encoding. Measured on the 3.6.8 floor with `sys.stdout.write`:
    under `LC_ALL=C` a non-ASCII statedir raised `UnicodeEncodeError` and the
    command exited **1** -- the status that means *I could not read the file at
    all*, for a file it read perfectly, which `install.sh` turns into
    `die "could not read <config> ... unreadable, or not UTF-8"`. Under
    ISO-8859-1 it was worse because it SUCCEEDED: exit 0 with the path
    transcoded to one byte where the file holds two, a value naming nowhere.

    `install.sh` would not have installed that second value -- `shell_safe`'s
    allowlist is ASCII -- but that guard lives in another file and is about
    remote shells, not encodings; this is a documented query whose own failure
    message tells the operator to run it by hand.
    """
    statedir = "/farm/café/agb"
    assert statedir.encode("utf-8") != statedir.encode("latin-1")  # non-vacuity
    config = config_path("statedir = %s\nmac_id = m-0001\n" % (statedir,))
    env = {"LC_ALL": locale, "LANG": locale}
    code, out, err = run_agb(print_statedir_argv(config), env=env)
    assert (code, out, err) == (0, statedir.encode("utf-8") + b"\n", b""), locale


@pytest.mark.parametrize("locale", NON_ASCII_LOCALES)
def test_the_carries_none_status_survives_a_path_the_locale_cannot_spell(
        run_agb, tmp_path, ops, locale):
    """⚠️ ...and the PROSE deliberately did **not** move to bytes with it.

    The rule above is about a machine-readable *value* that becomes a
    filesystem path. The `PRINT_STATEDIR_NONE` message is not one: it goes to
    the injected `out` seam (stderr by default) that every other line of
    `run_install_config` reports through, `install.sh` discards it with
    `2>/dev/null`, and the answer it carries is the exit **status**, not its
    text. `sys.stderr`'s default error handler is `backslashreplace`, so it
    cannot raise and cannot degrade a `PRINT_STATEDIR_NONE` into a `1`; encoding
    it strictly onto stdout instead is the *wrong* fix for the sibling above,
    and this is what would catch it -- stdout must stay empty even when the
    only thing there is to say contains a character this locale has no byte for.
    """
    home = tmp_path / "café"                 # non-ASCII in the PATH itself
    os.makedirs(str(home))
    config = home / "config"
    with open(str(config), "w") as handle:
        handle.write("mac_id = m-0001\n")         # ...and no statedir
    env = {"LC_ALL": locale, "LANG": locale}
    code, out, err = run_agb(print_statedir_argv(config), env=env)
    assert (code, out) == (ops.PRINT_STATEDIR_NONE, b""), locale
    assert b"carries no statedir" in err, locale


# ---------------------------------------------------------------------------
# install.sh
# ---------------------------------------------------------------------------

def extract_sh_function(name, script=INSTALL_SH):
    """The text of one shell function, taken out of the file itself.

    A couple of refusals are unreachable end to end on this box -- `find_python`
    only gives up when none of its four absolute candidates exist, and
    `/usr/bin/python3` does. Extracting the function is how they get exercised
    without a chroot; restating the body in the test would mean testing the
    test's own copy of it.
    """
    with open(script) as handle:
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
    """The Mac role, with every path that could escape the test pinned down.

    ⚠️ **A default test install is a NAMED one.** `install.sh mac` refuses to
    run without `--instance`, so there is no nameless shape left for a fixture
    to state -- the name is not decoration here, it is what makes the argv legal
    at all. `HOST` is the fixture's own `--feed-host`, so this reads as "what
    `--instance auto` would have named it" rather than as an arbitrary token.

    `--instance` only *defaults* `$config`, `$logdir` and `$label`, each
    `[ -n ... ] ||`, so the pinned `--config` and `--log-dir` still win and test
    isolation is unchanged. The one derived value that moves is the **label**,
    to `com.agbridge.box2` -- and with it the plist FILENAME, which is why a
    handful of tests name `com.agbridge.box2.plist` rather than
    `com.agbridge.plist`.

    ⚠️ And because `--config` is pinned here, it is **never** the path
    `--instance` would have derived -- so `install.sh`'s statedir adoption
    cannot fire for this fixture, by design. That is convenient (every test
    below keeps saying exactly what statedir it means) but it is also a trap:
    a test written against `mac_args` directly is silently exercising the
    *non*-adopting branch. The adoption tests go through `_instance_args`,
    which drops `--config`.
    """
    def build(**over):
        args = {
            "--dest": str(tmp_path / "dest"),
            "--config": str(tmp_path / "cfg" / "config"),
            "--launch-agents": str(tmp_path / "agents"),
            "--log-dir": str(tmp_path / "logs"),
            "--statedir": str(tmp_path / "state"),
            "--feed-host": "box2",
            "--instance": HOST,
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
    the `--config` seam, because the default is what the user gets.

    ⚠️ The property survives; the path it names does not. "The user's own
    dotfile" for the mac role is now `~/.config/agbridge/<name>/config` -- with
    `--instance` required, `$config` defaults through the instance convention
    (`install.sh`'s `[ -n "$config" ] || config="$DEFAULT_CONFIG_DIR/$instance/
    config"`) and never through `$DEFAULT_CONFIG`. What is still being asserted
    is that an install with no `--config` at all writes a real, readable config
    where the operator will look for it.
    """
    code, out, err = run_sh(mac_args(**{"--config": None}))
    assert code == 0, err
    path = os.path.join(str(fake_home), ".config", "agbridge", HOST, "config")
    assert agb.valid_mac_id(agb.read_config(path)["mac_id"])
    # ...and not at the old nameless path, which no install creates any more.
    assert not os.path.exists(
        os.path.join(str(fake_home), ".config", "agbridge", "config"))


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


# ⚠️ `test_the_printed_farm_command_omits_the_statedir_when_none_was_given`
# stood here, and it is DELETED rather than inverted -- deliberately, and this
# note is the reason so the deletion is not read as a violation of the house
# rule that a withdrawn claim keeps its reasoning.
#
# It asserted that the printed `install.sh farm ...` hint drops `--statedir`
# when none was given, "the flag is forwarded, not invented". That shape no
# longer exists: `--instance` is required for the mac role, `--instance`
# requires `--statedir`, so every argv that reaches the hint carries one and
# `install.sh`'s conditional around it is dead code (it is now unconditional).
# Its own argv, `mac_args(**{"--statedir": None})`, is *refused*, so it cannot
# be re-pointed either.
#
# ⚠️ Its successor landed in the NEXT task, so the replacement spans two:
# `test_the_printed_farm_command_carries_the_adopted_statedir`, down in the
# `--instance` section (it needs `_instance_args`). The subject that remains is
# *the hint carries the ADOPTED statedir* -- a value that never appeared on the
# argv, which `test_the_printed_farm_command_carries_the_statedir_the_mac_recorded`
# above cannot distinguish from a forwarded one.


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


def test_install_sh_farm_defaults_the_config_to_the_users_own_dotfile(
        run_sh, tmp_path, fake_home, agb):
    """⚠️ The one RUNTIME exercise left of `install.sh`'s
    `[ -n "$config" ] || config="$DEFAULT_CONFIG"`.

    Measured while making `--instance` mandatory: replacing that line with a
    `die` caused **zero** failures in the whole suite. Every mac test derives
    its config from `--instance` now, and every farm test pinned `--config` --
    so a line deciding where a real install writes was left covered only by the
    *string* comparison in
    `test_the_default_config_path_is_spelled_the_same_in_all_three_places`,
    which cannot tell a live default from a dead one.

    The farm role is where the fall-through still lives: a farm host has exactly
    one identity, takes no instance sugar, and `agb hook` resolves
    `agb.config_path()` on every invocation -- so this path is not a leftover,
    it is the only one those hooks will ever read.
    """
    code, out, err = run_sh(["farm", "--mac-id", "mac-0001",
                             "--statedir", str(tmp_path / "state"),
                             "--settings", str(tmp_path / "settings.json"),
                             "--python", sys.executable])
    assert code == 0, err
    path = fake_home / ".config" / "agbridge" / "config"
    assert agb.read_config(str(path))["mac_id"] == "mac-0001"
    assert agb.config_path() == str(path)      # ...and it is agb's own default


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
    plist = tmp_path / "agents" / MAC_PLIST
    with open(str(plist), "rb") as handle:
        raw = handle.read()
    parsed = plistlib.loads(raw)
    assert parsed["ProgramArguments"] == [
        sys.executable, "-S", "-E", str(tmp_path / "dest" / "agb"), "bridge",
        "--config", str(tmp_path / "cfg" / "config")]
    assert parsed["StandardOutPath"].startswith(str(tmp_path / "logs"))
    assert parsed["Label"] == MAC_LABEL
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
    plist = tmp_path / "agents" / MAC_PLIST
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
    with open(str(tmp_path / "agents" / MAC_PLIST), "rb") as handle:
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
    # ⚠️ The name the fixture now renders. Spelled `com.agbridge.plist` this
    # assertion kept passing and stopped asserting: no successful `mac_args()`
    # install can produce that file any more, so it would have held nothing up
    # while the `*.tmp.*` companion carried the test alone.
    assert not (tmp_path / "agents" / MAC_PLIST).exists()
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
    # The name the fixture renders -- see the note in the test above.
    assert not (tmp_path / "agents" / MAC_PLIST).exists()
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
    plist = str(tmp_path / "agents" / MAC_PLIST)
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
    assert (tmp_path / "agents" / MAC_PLIST).exists()


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
    first keeps the plist inside the test's tree, the second is what the refusal
    test removes.

    ⚠️ `--statedir` is **not** unconditionally required by `--instance` any
    more, and this docstring used to say it was. It is required for a *new*
    instance; a re-install adopts the value out of that instance's own config.
    Dropping `--config` is what arms that adoption at all -- `install.sh` reads
    a statedir back only from the config `--instance` derived, so a test built
    on `mac_args` directly (whose `--config` is pinned into `tmp_path`) is
    silently exercising the non-adopting branch.
    """
    args = {"--config": None, "--log-dir": None, "--instance": name}
    args.update(over)
    return mac_args(**args)


def _instance_config_path(fake_home, name="hostb"):
    """WHERE the installer should have put it -- a path, not a written file.

    Named apart from `conftest.instance_config`, which writes one: these tests
    assert what `install.sh` created (or, twice, that it created nothing), so
    a helper that wrote the file would make every one of them vacuous.
    """
    return fake_home / ".config" / "agbridge" / name / "config"


def _seed_default_config(run_agb, fake_home):
    """A real config at the DEFAULT (nameless) path, and the id in it.

    ⚠️ `install.sh` cannot be the seeder any more. These tests used to write it
    with `run_sh(mac_args(**{"--config": None}))`, i.e. through the installer --
    and with `--instance` required there is no installer argv that creates a
    nameless instance at all. The file still has to exist, because
    `install.sh`'s mac-id adoption probes `$DEFAULT_CONFIG` second and a Mac
    upgraded from 0.5.0 really does have one.

    `agb install-config` is not a weaker substitute for the installer here, it
    is the SAME WRITER: `role_mac` shells out to exactly this command for every
    config it writes, so what lands is the shape the adoption reads on a real
    Mac rather than a hand-rolled look-alike.
    """
    path = str(fake_home / ".config" / "agbridge" / "config")
    code, out, err = run_agb(["install-config", "--config", path,
                              "--generate-mac-id", "--print-mac-id"])
    assert code == 0, err
    mac_id = out.decode().strip()
    assert mac_id
    return path, mac_id


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

    config = _instance_config_path(fake_home)
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

    # Non-vacuity, and the isolation claim -- ⚠️ RE-ANCHORED TWICE, and the
    # second time as a COUNT rather than a name. It used to say "the DEFAULT
    # instance was not written", which was the isolation an instance install
    # needed when a nameless install was the ordinary case; `install.sh mac`
    # refuses one now, so that claim was about a file nothing can create. But
    # naming the OTHER instance (`com.agbridge.box2`, the fixture's own token)
    # is barely stronger: the positive assertions above already exclude it, so
    # mutating the label derivation fails `Label ==` first and the negative one
    # never speaks. What actually holds anything up is "these are the ONLY two
    # things written", which catches a stray write of any shape -- including
    # the nameless one, and including a name nobody predicted.
    assert [str(p) for p in fake_home.rglob("config")] == [str(config)]
    assert os.listdir(str(tmp_path / "agents")) == ["com.agbridge.hostb.plist"]
    assert not (tmp_path / "agents" / MAC_PLIST).exists()   # spelled out too
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
    assert not _instance_config_path(fake_home).exists()


def test_an_instance_adopts_the_macs_existing_mac_id(run_sh, mac_args,
                                                     run_agb, fake_home, agb):
    """Decision 4. The id names THIS MAC, not this connection.

    Each instance's bridge writes `bridge/<mac-id>.beat` inside its OWN
    statedir, and those two statedirs share no disk -- so the same id in both is
    the truth. Minting a second one would leave the new cluster's
    `agb status-line` reading `bridge:DOWN` until every farm host there was
    re-installed with the new id, which is the exact failure `install.sh` prints
    the id to prevent.

    ⚠️ The seed is `agb install-config`, not the installer: `install.sh mac`
    requires `--instance` and so can no longer write a nameless config at all.
    See `_seed_default_config` -- the writer is the same one `role_mac` calls.
    """
    _path, seeded = _seed_default_config(run_agb, fake_home)

    code, out, err = run_sh(_instance_args(mac_args))
    assert code == 0, err
    assert agb.read_config(str(_instance_config_path(fake_home)))["mac_id"] \
        == seeded
    assert "adopted %s" % (seeded,) in out


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
        agb.read_config(str(_instance_config_path(fake_home)))["mac_id"])
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

    ⚠️ This is the NEW-instance case, and it is the one the adoption below
    cannot rescue: there is no config at the derived path to read a statedir
    out of, so the flag is the only source there is. Its companion --
    `test_an_existing_instance_adopts_the_statedir_from_its_own_config` --
    differs in exactly one thing, the presence of that file.
    """
    assert not _instance_config_path(fake_home).exists()   # nothing to adopt
    code, out, err = run_sh(_instance_args(mac_args, **{"--statedir": None}))
    assert code != 0
    assert "--statedir" in err
    assert "adopted" not in out
    assert not _instance_config_path(fake_home).exists()
    assert not (tmp_path / "dest").exists()
    assert not (tmp_path / "agents").exists()


# ---------------------------------------------------------------------------
# ...and an EXISTING instance reads it back out of its own config
#
# The re-typing hazard is not hypothetical: the owner's own migration put a
# `feed_host` typo into one instance because every upgrade meant retyping the
# farm-side flags. `--statedir` is the one of them whose wrong value is silent
# -- the bridge connects to the right machine and reads the wrong directory --
# so it is the one worth reading back rather than re-asking for.
#
# ⚠️ Adopted ONLY from the config `--instance` derived. `--instance hostb
# --config <another instance's config>` is a legal shape (documented, not
# closed), and adopting through it would hand hostb's bridge the other
# cluster's disk: the precise failure the requirement exists to prevent,
# arriving by the one route the requirement cannot see.
# ---------------------------------------------------------------------------


def test_an_existing_instance_adopts_the_statedir_from_its_own_config(
        run_sh, mac_args, instance_config, fake_home, agb):
    """The ergonomic point of the whole change: a re-install of an instance
    that already exists does not re-demand the one flag whose wrong value is
    silent.

    Non-vacuous by construction, and that is why the argv passes no
    `--statedir` at all: without the adoption this install is *refused* and
    there is no exit-0 run to assert anything about.
    """
    config = instance_config("hostb",
                             "statedir = /shared/HOSTB\nmac_id = mac-b0b0\n")
    code, out, err = run_sh(_instance_args(mac_args, **{"--statedir": None}))
    assert code == 0, err
    # Announced, not silently used: which directory a bridge ends up watching
    # is exactly what an operator re-running an installer needs told.
    assert "statedir: adopted /shared/HOSTB from %s" % (config,) in out
    assert agb.read_config(config)["statedir"] == "/shared/HOSTB"


def test_an_instance_whose_own_config_carries_no_statedir_is_still_refused(
        run_sh, mac_args, instance_config, tmp_path, fake_home):
    """A config that exists is not a config that answers.

    `--print-statedir` reports the file's **own** value and raises when there
    is none, rather than falling through to `agb.statedir()` -- which reads the
    *default-path* config, i.e. the other cluster's directory. So a half-written
    instance config gets the flag demanded of it, not another machine's disk.

    Its companion is the test above: same argv, same instance, and the only
    difference is whether that file carries a `statedir` line.
    """
    instance_config("hostb", "mac_id = mac-b0b0\n")
    code, out, err = run_sh(_instance_args(mac_args, **{"--statedir": None}))
    assert code != 0
    assert "--statedir" in err
    assert "adopted" not in out
    assert not (tmp_path / "dest").exists()
    assert not (tmp_path / "agents").exists()


def test_a_new_instance_never_inherits_the_default_configs_statedir(
        run_sh, mac_args, instance_config, tmp_path, fake_home):
    """⚠️ NOT a mirror of the mac-id adoption, which probes the default config
    second on purpose.

    One Mac has one identity, so sharing a `mac_id` across instances is the
    truth; sharing a STATEDIR is the failure -- two clusters share no disk, so
    the default config's path names a directory this instance's farm cannot
    see, `agb feed` creates it over there, and the farm reads as empty for
    ever. So the statedir adoption has exactly one candidate and never a loop.

    The non-vacuity is the second half: the very same run reads that very same
    file for the mac-id, so "the statedir did not come from it" is a statement
    about the rule and not about an unreadable file.
    """
    instance_config(None, "statedir = /shared/DEFAULT\nmac_id = mac-0001\n")
    assert not _instance_config_path(fake_home).exists()   # a NEW instance
    code, out, err = run_sh(_instance_args(mac_args, **{"--statedir": None}))
    assert code != 0
    assert "--statedir" in err
    assert "/shared/DEFAULT" not in out
    assert not (tmp_path / "dest").exists()

    # Same argv plus the flag: the install succeeds and the mac-id adoption
    # reaches into that default config, which is what proves it was reachable
    # and parseable all along.
    code, out, err = run_sh(_instance_args(mac_args))
    assert code == 0, err
    assert "adopted mac-0001 from" in out
    assert "adopted /shared/DEFAULT" not in out


def test_an_instance_pointed_at_another_config_still_needs_a_statedir(
        run_sh, mac_args, instance_config, tmp_path, fake_home, agb):
    """⚠️ The measured hole, and the reason the adoption is conditional at all.

    `--instance hostb --config ~/.config/agbridge/config` is legal today and
    this plan does not close it -- `--instance` only *defaults* `$config`. A
    naive adoption reads whatever `$config` names, so this argv would report
    `statedir: adopted /shared/DEFAULT` and exit 0: a bridge to hostb's machine
    reading the FIRST cluster's directory, which is the exact failure the
    requirement exists to prevent.

    So the rule is about whether `--config` was **typed**, not about which file
    it names -- and the second half here is what makes that assertable rather
    than a coincidence of the fixture.
    """
    default = instance_config(
        None, "statedir = /shared/DEFAULT\nmac_id = mac-0001\n")
    code, out, err = run_sh(_instance_args(
        mac_args, **{"--statedir": None, "--config": default}))
    assert code != 0
    assert "--statedir" in err
    assert "adopted" not in out
    assert "/shared/DEFAULT" not in out
    assert not (tmp_path / "dest").exists()

    # Non-vacuity, and the whole subject: the SAME content at the DERIVED path
    # is adopted. Drop the "was --config typed" condition and the first half
    # above starts succeeding with the other cluster's directory -- which is
    # what this second half turns into a named failure.
    instance_config("hostb", "statedir = /shared/DEFAULT\nmac_id = mac-0001\n")
    code, out, err = run_sh(_instance_args(mac_args, **{"--statedir": None}))
    assert code == 0, err
    assert "statedir: adopted /shared/DEFAULT" in out


def test_the_adopted_statedir_is_the_instances_own_never_the_defaults(
        run_sh, mac_args, instance_config, fake_home, agb):
    """Both files present, holding **different** statedirs, so the answer
    cannot be right by coincidence -- the shape `install_config_values`' own
    `or agb.statedir()` fallback would get wrong, and report `exit 0` doing
    it."""
    instance_config(None, "statedir = /shared/DEFAULT\nmac_id = mac-0001\n")
    named = instance_config("hostb",
                            "statedir = /shared/HOSTB\nmac_id = mac-b0b0\n")
    code, out, err = run_sh(_instance_args(mac_args, **{"--statedir": None}))
    assert code == 0, err
    assert "adopted /shared/HOSTB" in out
    assert "/shared/DEFAULT" not in out
    assert agb.read_config(named)["statedir"] == "/shared/HOSTB"


def test_an_explicit_statedir_beats_the_adoption(run_sh, mac_args,
                                                 instance_config, tmp_path,
                                                 fake_home, agb):
    """⚠️ The adoption is a fall-back, and MOVING an instance's statedir is the
    most plausible reason anyone re-installs one.

    The guard is `[ -z "$statedir" ] && [ "$config_given" = no ]`, and only its
    second half was pinned. With the first half gone the flag is silently
    discarded: the config keeps the OLD directory, and the printed
    `install.sh farm …` hint tells the operator to install the farm side
    against the old one too -- "ssh to the right machine, read the wrong disk",
    arriving out of the very feature that exists to prevent it.

    So three assertions, not one: what was written, that nothing was announced
    as adopted, and what the hint carries. The hint is the half that reaches
    another machine.
    """
    config = instance_config("hostb",
                             "statedir = /shared/OLD\nmac_id = mac-b0b0\n")
    code, out, err = run_sh(_instance_args(mac_args))   # --statedir is pinned
    assert code == 0, err
    new = str(tmp_path / "state")
    assert agb.read_config(config)["statedir"] == new
    assert "statedir: adopted" not in out      # not `"adopted"`: the mac-id is
    assert "/shared/OLD" not in out
    argv = _farm_hint(out)
    assert argv[argv.index("--statedir") + 1] == new


@pytest.mark.parametrize("value,message", [
    ("relative/dir", "absolute path"),
    ("/shared/has space", "would not survive a remote shell"),
])
def test_an_adopted_statedir_is_re_checked_before_anything_is_written(
        run_sh, mac_args, instance_config, tmp_path, value, message):
    """⚠️ Defence in depth that is not redundant, and it is about WHEN.

    The top-level `shell_safe`/`absolute` pair runs long before this value
    exists, so an adopted one would reach the config and the farm hint
    unchecked. `agb install-config` does refuse it eventually -- but that is
    after `mkdir -p "$dest"` and the three-file copy, so the tree is half made
    when the refusal arrives. Measured with the two lines deleted: `$dest`
    contained `agb`, `agb_mac`, `agb_ops` and `agb-refresh`.

    A config carrying either value is not exotic: hand-edited, or copied from
    another Mac where the path was right.
    """
    instance_config("hostb", "statedir = %s\nmac_id = mac-b0b0\n" % (value,))
    code, out, err = run_sh(_instance_args(mac_args, **{"--statedir": None}))
    assert code != 0
    assert "the adopted statedir" in err        # named as adopted, not as a flag
    assert message in err
    assert not (tmp_path / "dest").exists()     # refusals write nothing
    assert not (tmp_path / "agents").exists()


def test_a_config_that_cannot_be_read_is_not_reported_as_carrying_none(
        run_sh, mac_args, instance_config, tmp_path, fake_home):
    """⚠️ Four different failures used to arrive as one sentence.

    `statedir=$(… --print-statedir) || statedir=""` swallowed *no statedir
    key*, *no file*, *file unreadable* and *file not UTF-8* alike, and the
    refusal below then told the operator that `<config>` "carries none to
    adopt" -- misdirecting them to `--statedir`, which would have installed
    this instance against a config nothing here can read. The query answers
    `PRINT_STATEDIR_NONE` for the first two and 1 for the rest, and only the
    first is swallowed.

    Not-UTF-8 rather than `chmod 000`: a suite running as root reads a mode-000
    file, and a guard that quietly stops firing is worse than none.
    """
    config = instance_config("hostb", "")
    with open(config, "wb") as handle:
        handle.write(b"statedir = /shared/\xff\xfe\n")
    code, out, err = run_sh(_instance_args(mac_args, **{"--statedir": None}))
    assert code != 0
    assert "could not read %s" % (config,) in err
    assert "carries none" not in err            # the misdirection itself
    assert not (tmp_path / "dest").exists()


def test_the_statedir_refusal_says_why_THIS_run_needs_the_flag(
        run_sh, mac_args, instance_config, tmp_path, fake_home):
    """⚠️ One message served three runs and was false in two of them.

    It was written for "a SECOND instance without `--statedir`". Making
    `--instance` mandatory turned it into the message every FIRST install gets,
    where "a second machine shares no disk with the first" describes a first
    machine that does not exist; and it claimed "<config> carries none to
    adopt" even when `--config` was typed and that file plainly carried one.
    The reason is what tells the operator what to DO -- pass the flag, fix the
    file, or drop `--config` -- so the three runs get three reasons.
    """
    # (1) a brand-new instance: there is no file to have read anything out of.
    code, out, err = run_sh(_instance_args(mac_args, **{"--statedir": None}))
    assert code != 0
    assert "NEW instance" in err
    assert "does not exist yet" in err

    # (2) the file is there and says nothing about a statedir.
    instance_config("hostb", "mac_id = mac-b0b0\n")
    code, out, err = run_sh(_instance_args(mac_args, **{"--statedir": None}))
    assert code != 0
    assert "carries no statedir of its own" in err
    assert "NEW instance" not in err

    # (3) `--config` was typed, and the reason is that -- not the file, which
    # here carries a perfectly good statedir the installer still will not take.
    other = instance_config(None, "statedir = /shared/DEFAULT\nmac_id = m-1\n")
    code, out, err = run_sh(_instance_args(
        mac_args, **{"--statedir": None, "--config": other}))
    assert code != 0
    assert "nothing is adopted when --config is given" in err
    assert "carries no statedir of its own" not in err
    assert "/shared/DEFAULT" not in err          # and it did not go reading it

    assert not (tmp_path / "dest").exists()      # none of the three wrote


def test_the_statedir_is_adopted_from_the_installers_own_tree(
        run_sh, mac_args, instance_config, tmp_path):
    """⚠️ It reads `$SELF/agb`, never `$dest/agb`.

    A refused install must write nothing, so the read has to happen before the
    three files are copied -- and `$dest/agb` does not exist until the `copied:`
    line, so the ORDER of the two lines is the assertion. A test that let the
    adoption read a copied tree would be asserting the wrong file, and would
    still pass on every run where the two trees happen to agree.

    The companion for the other direction is
    `test_an_instance_without_a_statedir_is_refused_and_installs_nothing`,
    which asserts `$dest` is never created at all when the adoption finds
    nothing.
    """
    instance_config("hostb", "statedir = /shared/HOSTB\nmac_id = mac-b0b0\n")
    code, out, err = run_sh(_instance_args(mac_args, **{"--statedir": None}))
    assert code == 0, err
    lines = out.splitlines()

    def first(prefix):
        hits = [i for i, line in enumerate(lines) if line.startswith(prefix)]
        assert hits, (prefix, out)
        return hits[0]

    assert first("statedir: adopted") < first("copied:")
    assert (tmp_path / "dest" / "agb").exists()      # ...and it really did copy

    # ⚠️ AND THE OUTPUT SHAPE THE PLACEMENT WAS CHOSEN TO BUY, which was a
    # decision with nothing holding it up. The adoption proves `$SELF/agb`
    # before trusting an answer out of it, and its own `verified:` report is
    # suppressed so that `statedir: adopted …` sits directly under `instance:
    # …` -- one statement about one instance. Unpinned, the `>/dev/null` comes
    # off in the next edit and a `verified:` line lands between the two.
    assert out.count("verified:") == 1
    assert lines[first("statedir: adopted") - 1].startswith("instance:")


def _recording_python(stub_bin):
    """A `--python` that records every argv and then really runs it.

    The proofs `verify_tree` performs are invisible in the output (the
    adoption's report is suppressed, and the memo re-reports the same sentence
    either way), so both memo tests below count `agb version` calls instead --
    it is run by nothing else, and its argv names the tree it was asked about.
    """
    log = stub_bin.install("python3", body=(
        "#!/bin/sh\n"
        "{ for a in \"$@\"; do printf '%s\\037' \"$a\"; done; printf '\\n'; } "
        ">> \"" + str(stub_bin.path / "python3.log") + "\"\n"
        "exec " + sys.executable + " \"$@\"\n"))
    assert not log.exists()                      # nothing has run it yet
    return str(stub_bin.path / "python3")


def _version_probes(stub_bin):
    """The tree each `agb version` call named: `<python> -S -E <agb> version`."""
    return [call[-2] for call in stub_bin.calls("python3")
            if call[-1] == "version"]


def test_a_dry_run_verifies_the_installer_tree_once_not_twice(
        run_sh, mac_args, instance_config, stub_bin):
    """⚠️ Two callers, one tree, and `verify_tree` is three interpreter starts.

    On a `--dry-run` adopting install both the adoption and the dry branch
    prove `$SELF/agb` -- the same file, in the same run, which nothing can
    change in between. The second call re-reports the first one's answer
    instead of launching six interpreters to say one thing.

    ⚠️ Counted through a recording `--python`, because the OUTPUT cannot see
    this: the adoption's report is suppressed either way, so `verified:` appears
    once whether the work was done twice or not. A test asserting the line
    count passes with the memo deleted -- measured -- which is exactly the
    "assert something that cannot fail" trap this file keeps finding. `agb
    version` is run by nothing else, so counting it counts the proofs.

    Non-vacuous in the other direction too: the `verified:` line must still be
    there, and the answer in it must be real. ⚠️ And the companion below is
    what makes this test's *key* meaningful: both callers here ask about the
    same tree, so this one cannot see what the memo is keyed on.
    """
    python = _recording_python(stub_bin)

    instance_config("hostb", "statedir = /shared/HOSTB\nmac_id = mac-b0b0\n")
    code, out, err = run_sh(
        _instance_args(mac_args, **{"--statedir": None,
                                    "--python": python})
        + ["--dry-run"])
    assert code == 0, err
    assert "statedir: adopted /shared/HOSTB" in out
    assert out.count("verified:") == 1, out
    assert "verified: agb " in out               # a real answer, not an echo

    probes = _version_probes(stub_bin)
    assert probes == [os.path.realpath(
        os.path.join(conftest.REPO_ROOT, "agb"))], probes

    # ⚠️ AND THE TEXT, not only the count. The single `verified:` line above is
    # the MEMO's -- the first call's report is suppressed -- so if the hit path
    # spelled the sentence itself the two spellings could drift with nothing
    # here to notice: a count is one either way. Compared against the line a run
    # with no memo hit prints, which is the same claim about the same file.
    fresh_code, fresh_out, fresh_err = run_sh(mac_args() + ["--dry-run"])
    assert fresh_code == 0, fresh_err
    memo_lines = [l for l in out.splitlines() if l.startswith("verified:")]
    fresh_lines = [l for l in fresh_out.splitlines()
                   if l.startswith("verified:")]
    assert len(fresh_lines) == 1, fresh_out       # non-vacuity: it really ran
    assert memo_lines == fresh_lines, (memo_lines, fresh_lines)


def test_a_real_install_proves_the_tree_it_installed_not_only_the_installers(
        run_sh, mac_args, instance_config, stub_bin, tmp_path):
    """⚠️ THE MEMO IS KEYED ON THE TREE, and the `--dry-run` test cannot say so.

    Both of that test's callers ask about `$SELF/agb`, so a memo keyed on the
    interpreter alone answers it identically -- measured: re-key `verify_tree`
    on `"$vpython"` and every test in this suite passes. A REAL adopting
    install is the shape that separates the two keys. Two DIFFERENT trees are
    asked about: the adoption proves `$SELF/agb` before it trusts a statedir
    out of it, and the copy then proves `$dest/agb`, which is the file this
    installer just wrote and the one every later `agb` invocation will load.

    Mis-keyed, the second call is a memo hit: zero interpreters started against
    `$dest/agb`, and `verified: agb <v> at <dest>/agb, with agb_mac and agb_ops
    beside it` printed anyway. That line is `verify_tree`'s whole claim -- "this
    script copies all three and then RUNS the installed tree through all three
    of them" -- reduced to a sentence, and the failure it exists to catch (a
    copy that landed short or broken) comes back at the first `agb bridge`
    instead of at install time.

    So the count is asserted as **one probe per distinct tree**, attributed by
    the argv rather than merely counted: a count alone would also pass if both
    probes named the same file.
    """
    python = _recording_python(stub_bin)

    instance_config("hostb", "statedir = /shared/HOSTB\nmac_id = mac-b0b0\n")
    code, out, err = run_sh(_instance_args(mac_args,
                                           **{"--statedir": None,
                                              "--python": python}))
    assert code == 0, err
    assert "statedir: adopted /shared/HOSTB" in out    # the adoption really ran
    installed = str(tmp_path / "dest" / "agb")
    assert os.path.exists(installed)                   # ...and the copy landed

    probes = _version_probes(stub_bin)
    assert sorted(probes) == sorted(
        [os.path.realpath(os.path.join(conftest.REPO_ROOT, "agb")),
         installed]), probes

    # Non-vacuity, and the half that makes the mis-key a printed lie rather
    # than only a missing proof: exactly one `verified:` line is printed (the
    # adoption's is suppressed), and it names the INSTALLED tree.
    verified = [l for l in out.splitlines() if l.startswith("verified:")]
    assert len(verified) == 1, out
    assert installed in verified[0], verified


def test_a_tree_that_cannot_run_agb_says_so_rather_than_demanding_a_statedir(
        run_sh, mac_args, instance_config, tmp_path, fake_home):
    """⚠️ Why the adoption proves the tree BEFORE it reads an answer out of it.

    `--print-statedir` is run through `$SELF/agb`, and a `$SELF` tree that
    cannot run `agb` at all answers non-zero for a reason that has nothing to
    do with this config. Without the proof the operator is told to pass
    `--statedir` for an instance whose config already records one -- and the
    statedir refusal fires *before* the `verified:` line further down, so the
    real problem never gets named at all.

    `agb_ops` is the file broken here, not `agb_mac`: `install-config` needs
    `agb_ops`, so a broken `agb_mac` fails the *later* verify with the same
    message and the test would pass with this proof deleted.
    """
    tree = tmp_path / "broken"
    tree.mkdir()
    shutil.copy(INSTALL_SH, str(tree / "install.sh"))
    for name in DIST_FILES:
        shutil.copy(os.path.join(conftest.REPO_ROOT, name), str(tree / name))
    with open(str(tree / "agb_ops"), "w") as handle:
        handle.write("this is not python(\n")

    instance_config("hostb", "statedir = /shared/HOSTB\nmac_id = mac-b0b0\n")
    code, out, err = run_sh(_instance_args(mac_args, **{"--statedir": None}),
                            script=str(tree / "install.sh"))
    assert code != 0
    assert "agb_ops" in err, err          # the real problem, named
    assert "--statedir" not in err        # ...and not the flag that is not it
    assert not (tmp_path / "dest").exists()


def test_the_printed_farm_command_carries_the_adopted_statedir(
        run_sh, mac_args, instance_config):
    """⚠️ The successor to `…omits_the_statedir_when_none_was_given`, deleted
    with the refusal in the previous task.

    Its subject is NOT "the hint always carries `--statedir`" --
    `test_the_printed_farm_command_carries_the_statedir_the_mac_recorded`
    already asserts that, with the flag passed explicitly. This one is about a
    value that never appeared on the argv at all, which that test cannot tell
    apart from a forwarded one. It matters because the hint is what gets
    pasted onto every farm host of the cluster: a hint carrying the wrong
    statedir installs hooks against a directory the bridge never looks at, and
    the feed reports an empty farm for ever.
    """
    instance_config("hostb", "statedir = /shared/HOSTB\nmac_id = mac-b0b0\n")
    code, out, err = run_sh(_instance_args(mac_args, **{"--statedir": None}))
    assert code == 0, err
    argv = _farm_hint(out)
    assert argv[argv.index("--statedir") + 1] == "/shared/HOSTB"


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


def test_an_empty_instance_name_is_refused_rather_than_ignored(
        run_sh, mac_args, tmp_path, fake_home):
    """`--instance ""` must not read as "not given" and install the DEFAULT
    instance while echoing the name back -- the silent-wrong-instance failure
    the whole flag exists to prevent, arriving from the one input that looks
    like an accident rather than an attack.

    `need` is what refuses it now (see
    `test_an_empty_value_is_a_missing_value_for_every_installer_flag`);
    `instance_ok` keeps its own empty case behind that, because the two
    validators are compared arm for arm against `agb-refresh`'s.
    """
    code, out, err = run_sh(_instance_args(mac_args, name=""))
    assert code != 0
    assert "--instance" in err
    # Nothing was installed AT ALL -- not the named instance, and not the
    # default one it would have fallen through to.
    assert not (tmp_path / "dest").exists()
    assert not (tmp_path / "agents").exists()
    assert list(fake_home.rglob("config")) == []


def _installer_value_flags():
    """Every installer option that consumes `$2`, derived from the script.

    Derived rather than listed, because the trap this guards is per-flag: a
    list written by hand covers the flags somebody remembered.
    """
    flags = []
    with open(INSTALL_SH) as handle:
        for line in handle.read().splitlines():
            stripped = line.strip()
            if stripped.startswith("--") and ") need $#" in stripped:
                flags.append(stripped.split(")")[0])
    return flags


@pytest.mark.parametrize("flag", _installer_value_flags())
def test_an_empty_value_is_a_missing_value_for_every_installer_flag(
        flag, run_sh, tmp_path, fake_home):
    """⚠️ `--config "$cfg"` with `$cfg` unset is ONE EMPTY ARGUMENT.

    A `need` that only counted arguments therefore saw a value where there was
    none -- and every one of these flags has a default waiting a few lines
    later (`[ -n "$config" ] || config="$DEFAULT_CONFIG"`, the `--instance`
    conventions, `find_python`). So the empty value is silently replaced and
    the install SUCCEEDS against something other than what was named, which on
    `--config` means a second instance installed straight over the first.
    `--statedir ""` is worse still: it reads as "not given", so the instance
    inherits the default config's farm path -- ssh to the right machine and
    read the wrong directory, the one failure `--instance` refuses to install
    without.

    Every flag, not just the two: the rule belongs to `need`, and a fix applied
    to one flag leaves the same trap on the other seventeen.
    """
    code, _out, err = run_sh(["mac", flag, ""])
    assert code != 0
    assert "%s needs a value" % (flag,) in err
    # Nothing was written -- not the named thing, and not the default it would
    # have fallen through to.
    assert not (tmp_path / "dest").exists()
    assert not (tmp_path / "agents").exists()
    assert list(fake_home.rglob("config")) == []


@pytest.mark.parametrize("path", ["relcfg/config", "~/relcfg/config",
                                  "./config"])
def test_a_config_path_that_is_not_absolute_is_refused(run_sh, mac_args,
                                                       tmp_path, path):
    """⚠️ `--config` is the one path that reaches launchd, and it is rendered
    into ProgramArguments UNCONDITIONALLY.

    The job runs with `WorkingDirectory /tmp`, so `--config relcfg/config`
    writes a real config to `$PWD/relcfg/config`, reports success, and then
    hands the bridge `/tmp/relcfg/config` -- read as `{}`, dying in a KeepAlive
    restart loop whose error names a path that exists where the operator was
    standing. `/tmp` is world-writable, so that path is plantable, and a config
    feeds `feed_host`/`remote_python`/`jump_host` into the ssh argv.

    The quoted `~` form is the same bug with a worse tell: `install-config`
    expands it and the plist does not, so the two halves of one install disagree
    about which file they mean.

    Every other path here already went through `absolute`; this one did not.
    """
    code, out, err = run_sh(mac_args(**{"--config": path}))
    assert code != 0
    assert "--config" in err and "absolute" in err
    # Refused before anything was copied or rendered.
    assert not (tmp_path / "dest").exists()
    assert not (tmp_path / "agents").exists()


def test_an_instance_install_bounces_only_its_own_launchd_job(
        run_sh, mac_args, tmp_path, stub_bin):
    """⚠️ Which job is stopped and started is a claim no test made.

    `bootout`/`bootstrap` are built from `$label`, and with the label wrong an
    instance install would stop the OTHER instance's bridge and leave it down --
    a routine upgrade taking out the sidebar of a machine nobody touched, with
    every other line of the install identical.
    """
    stub_bin.install("launchctl")
    argv = [arg for arg in _instance_args(mac_args) if arg != "--no-load"]
    code, out, err = run_sh(argv)
    assert code == 0, err
    calls = stub_bin.calls("launchctl")
    assert [call[0] for call in calls] == ["bootout", "bootstrap"]
    assert calls[0][1].endswith("/com.agbridge.hostb")
    assert calls[1][-1] == str(tmp_path / "agents"
                               / "com.agbridge.hostb.plist")
    # Non-vacuity, and the failure itself: the OTHER instance's label is not
    # what was bounced, and its plist is not what was bootstrapped. Re-anchored
    # from `com.agbridge` -- with a nameless install refused there is no default
    # job left to name, and `mac_args`' own `--instance box2` is the job an
    # install of `hostb` must not take out.
    assert not any(arg.endswith("/" + MAC_LABEL) for call in calls
                   for arg in call)
    assert not any(arg.endswith("/" + MAC_PLIST) for call in calls
                   for arg in call)


def test_reinstalling_an_instance_keeps_the_mac_id_it_already_has(
        run_sh, mac_args, run_agb, fake_home, agb):
    """⚠️ Adoption fires on EVERY `--instance` run without `--mac-id`, which
    means on a routine upgrade -- and `resolve_mac_id` gives `given` priority
    over `existing`.

    So probing only the default config REPLACES an id this instance already
    recorded. Every farm host of that cluster still watches
    `bridge/<old-id>.beat`, so `agb status-line` reads `bridge:DOWN` for ever
    and `agb doctor` reports no beat -- out of an install that was asked to
    change nothing.

    ⚠️ This is the load-bearing pin of the "own config FIRST, the default one
    only after" ordering, and it is worth keeping straight against the statedir
    adoption that imitates its shape: the mac-id deliberately falls back to
    `$DEFAULT_CONFIG` (sharing an id across instances is the TRUTH -- one Mac,
    one id), while a statedir must never be adopted from another instance's
    file, because sharing a statedir is precisely the failure `--instance`
    refuses to install without. Same loop, opposite second step.

    ⚠️ The default config is seeded by `agb install-config`, not by the
    installer -- `install.sh mac` no longer writes a nameless one.
    """
    code, out, err = run_sh(_instance_args(mac_args,
                                           **{"--mac-id": "mac-b0b0"}))
    assert code == 0, err
    _path, default = _seed_default_config(run_agb, fake_home)
    assert default != "mac-b0b0", "the two configs must disagree to test this"

    code, out, err = run_sh(_instance_args(mac_args))           # the upgrade
    assert code == 0, err
    assert agb.read_config(
        str(_instance_config_path(fake_home)))["mac_id"] == "mac-b0b0"
    assert "adopted mac-b0b0 from %s" % (_instance_config_path(fake_home),) in out
    assert default not in out


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


# ---------------------------------------------------------------------------
# and now the name is REQUIRED: no nameless instance can be created
# ---------------------------------------------------------------------------
#
# Every Mac-side command stopped privileging the unnamed instance one release
# ago -- a bare `agb-refresh` and a bare `agb close-done` sweep all of them, a
# bare `agb forget-rows` is refused, `agb instances` lists what exists. But
# `install.sh mac` with no `--instance` still CREATED a nameless one, so the
# symmetry was a convention rather than a guarantee. These three close that.
#
# ⚠️ What they do NOT claim: that a nameless instance is unreachable. A plist on
# disk outlives the installer that wrote it, so every legacy reader stays --
# `agb_mac.instance_display_name`'s `(default)` spelling, `bind_label_to_config`'s
# "no --config implies the default config" branch, `_is_agbridge_instance`'s
# label-space clause. What changed is CREATABILITY, and not even that airtight:
# `--instance X --config <the default path>` still writes the nameless config,
# which is exactly what
# `test_what_a_default_install_renders_leaves_the_bridge_where_it_was` relies on.

def test_a_nameless_mac_install_is_refused_and_writes_nothing(
        run_sh, mac_args, tmp_path, fake_home, stub_bin):
    """⚠️ The point of the change.

    A hard error and not a warning: a warning on a FIRST install gets ignored,
    and the asymmetry it warned about is then permanent on that Mac -- every
    Mac-side command guessing which instance was meant, and `agb instances`
    unable to say what exists.

    ⚠️ Asserted as ABSENCE, not as an exit code. A refusal that exits non-zero
    after copying three files, writing a config and rendering a plist has still
    installed something, and a half-tree is the state nothing can diagnose
    later. So: every path this argv names, plus a `launchctl` stub that must
    record no call at all.
    """
    stub_bin.install("launchctl")
    argv = [a for a in mac_args(**{"--instance": None}) if a != "--no-load"]
    code, out, err = run_sh(argv)
    assert code != 0
    assert "--instance" in err
    assert "--instance auto" in err            # ...and the one-word fix
    # ⚠️ THE BACKTICKS SURVIVED, and this is the only thing that can catch it.
    # The message names `agb instances`, so the `die` argument is SINGLE-quoted:
    # in double quotes those backticks are command substitution and `die` would
    # RUN `agb instances` -- off `$PATH`, during a refusal, with the output
    # spliced into the middle of the sentence. Both assertions above sit
    # outside the backticks and survive the mangling intact, which is how the
    # mistake reaches a green suite.
    assert "`agb instances` can say what exists" in err
    assert not (tmp_path / "dest").exists()
    assert not (tmp_path / "agents").exists()
    assert not (tmp_path / "cfg" / "config").exists()
    assert not (tmp_path / "logs").exists()
    assert list(fake_home.rglob("config")) == []
    assert stub_bin.calls("launchctl") == []


def test_both_ways_of_naming_an_instance_satisfy_the_requirement(
        run_sh, mac_args, stub_bin, fake_home):
    """The refusal names two fixes, and one that named a fix nobody could use
    would be worse than one that named none.

    Both spellings in one test so the pair cannot drift: an explicit
    `--instance <name>`, and `--instance auto` reading the name off
    `--feed-host`. Asserted through the CONFIG each run wrote, because the
    config directory is what the name actually decides -- two runs, two
    directories, and the nameless one created by neither.
    """
    _ssh_answering(stub_bin, "hostb01")
    code, out, err = run_sh(_instance_args(mac_args, name="named"))
    assert code == 0, err
    assert _instance_config_path(fake_home, "named").exists()

    code, out, err = run_sh(_auto_args(mac_args))
    assert code == 0, err
    assert _instance_config_path(fake_home, "hostb01").exists()

    # ⚠️ A COUNT, not a name. "the nameless config was not written" names the
    # one path the fixture's own overrides already exclude, so it went quiet
    # the moment no install could produce it. Two runs must leave exactly two
    # configs, and any third -- whatever it is called -- is the failure.
    assert sorted(str(p) for p in fake_home.rglob("config")) == sorted([
        str(_instance_config_path(fake_home, "named")),
        str(_instance_config_path(fake_home, "hostb01"))])
    assert not (fake_home / ".config" / "agbridge" / "config").exists()


def test_install_sh_farm_still_installs_with_no_instance(run_sh, tmp_path, agb):
    """⚠️ The requirement belongs to the MAC role alone, and the asymmetry is
    the design rather than an omission.

    A farm host has exactly one identity: `agb hook` and `agb status-line`
    resolve `agb.config_path()` -- the default path -- on every invocation, so a
    named farm config is a file nothing opens. That is why `--instance` is
    *refused* for the farm role, and requiring one there would refuse every farm
    install in existence to buy nothing.
    """
    code, out, err = run_sh(_farm(tmp_path))
    assert code == 0, err
    assert "--instance" not in err
    assert agb.read_config(str(tmp_path / "config"))["mac_id"] == "mac-0001"


def test_install_sh_farm_still_installs_with_no_statedir_either(
        run_sh, tmp_path, agb):
    """⚠️ The transitive half of the same asymmetry, and the one property the
    statedir move could have broken.

    `--statedir` is now required for every *mac* install, and the rule that
    says so lives inside `role_mac`. The farm role has always been allowed to
    go without -- `agb hook` resolves the statedir through `$AGB_STATEDIR`, the
    config, then agb's own default -- and `role_farm`'s two
    `if [ -n "$statedir" ]` conditionals are live for exactly that reason,
    which is what the comment at the mac hint asserts and nothing tested.
    Structurally safe (the `die` is lexically inside `role_mac`), and that is
    an argument, not a test.
    """
    args = _farm(tmp_path, **{"--statedir": None})
    assert "--statedir" not in args              # non-vacuity: it really is out
    code, out, err = run_sh(args)
    assert code == 0, err
    assert "--statedir" not in err
    assert agb.read_config(str(tmp_path / "config"))["mac_id"] == "mac-0001"


def test_the_installed_tree_still_reads_a_plist_it_can_no_longer_write(
        run_sh, mac_args, tmp_path, fake_home):
    """⚠️ CREATABILITY is not REACHABILITY, asserted rather than assumed.

    The three tests above say no *new* nameless instance can be made. The
    paragraph introducing them says the legacy readers all stay, because a
    plist on disk outlives the installer that wrote it -- and that half had no
    test, which is how a later "cleanup" retires a branch this change never
    retired.

    So: the nameless plist is rendered from `dist/com.agbridge.plist`, the file
    `install.sh` itself renders and the only shape that certainly exists on a
    Mac installed before this change; and it is read back by the `agb` the
    installer has *just installed*, not by the checkout. Both halves matter --
    a constant here would drift from the template, and the checkout would not
    be the tree an upgraded Mac actually runs.

    `com.agbridge` is the label whose name column is `(default)`, and it is the
    one the derivation gets wrong by answering "": the default label does not
    merely start with the prefix, it IS the prefix. That was a live bug once
    (`test_instances_listing_names_the_default_instance`), found by running the
    listing on the owner's real Mac.
    """
    code, _out, err = run_sh(mac_args())
    assert code == 0, err

    legacy_dir = tmp_path / "legacy-agents"
    legacy_dir.mkdir()
    default_config = _sh_default_config(INSTALL_SH, fake_home)
    with open(PLIST_TEMPLATE) as handle:
        rendered = handle.read()
    for holder, value in (("@LABEL@", "com.agbridge"),
                          ("@PYTHON@", sys.executable),
                          ("@AGB@", str(tmp_path / "dest" / "agb")),
                          ("@CONFIG@", default_config),
                          ("@PATH@", "/usr/bin:/bin"),
                          ("@LOGDIR@", str(tmp_path / "logs"))):
        rendered = rendered.replace(holder, value)
    # Non-vacuity: an unrendered placeholder would leave a plist whose argv is
    # not this argv, and the installer refuses such a file for the same reason.
    assert "@" not in rendered.split("<plist")[1]
    legacy_plist = legacy_dir / "com.agbridge.plist"
    with open(str(legacy_plist), "wb") as handle:
        handle.write(rendered.encode("utf-8"))
    assert plistlib.loads(read_bytes(legacy_plist))["Label"] == "com.agbridge"

    def instances(args):
        proc = subprocess.Popen(
            [sys.executable, "-S", "-E", str(tmp_path / "dest" / "agb"),
             "instances", "--launch-agents", str(legacy_dir)] + args,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        out, err = conftest.communicate(proc)
        assert proc.returncode == 0, err
        return out.decode("utf-8")

    # The listing: `(default)` in the NAME column, not a blank one, and the
    # config the legacy job actually runs on. `split()` rather than `in`,
    # because a blank name column still contains the label and the path.
    assert [line.split() for line in instances([]).splitlines() if line.strip()
            ] == [["(default)", "com.agbridge", default_config]]
    # ...and `--labels`, which is what the sweeps consume: an instance missing
    # from this list is one a bare `agb-refresh`/`agb close-done` cannot visit.
    assert instances(["--labels"]).split() == ["com.agbridge"]

    # The other half of the claim, and the reason this test is here rather than
    # beside the listing's own tests: that filename is one no install in this
    # suite can now produce.
    assert not (tmp_path / "agents" / "com.agbridge.plist").exists()
    assert (tmp_path / "agents" / MAC_PLIST).exists()


def test_an_install_is_the_plist_it_always_was_plus_the_config_flag(
        run_sh, mac_args, tmp_path, fake_home):
    """The upgrade claim, stated key by key.

    The config flag renders for EVERY install (decision 5: the installer gets
    one path, not a conditional exercised only on the second machine) -- so the
    guarantee for an existing install is not "no change" but "this change and no
    other".

    ⚠️ Was `..._a_default_install_...`, and the inversion is the reasoning, not
    the paths. It asserted `"instance:" not in out` as the *definition* of a
    default install: no banner meant the nameless instance, and that was the one
    shape this test was about. There is no such shape any more -- `--instance`
    is required -- so the banner is printed on every run, and asserting its
    absence would be asserting a state the installer cannot reach. The plist
    filename, the rendered `--config` and the `Label` all move with it, because
    all three follow the name.
    """
    code, out, err = run_sh(mac_args(**{"--config": None}))
    assert code == 0, err
    parsed = plistlib.loads(read_bytes(tmp_path / "agents" / MAC_PLIST))
    assert parsed["ProgramArguments"] == [
        sys.executable, "-S", "-E", str(tmp_path / "dest" / "agb"), "bridge",
        "--config", str(_instance_config_path(fake_home, HOST))]
    assert parsed["Label"] == MAC_LABEL
    assert parsed["StandardOutPath"] == str(tmp_path / "logs" / "bridge.log")
    assert parsed["StandardErrorPath"] == str(tmp_path / "logs"
                                              / "bridge.err.log")
    assert parsed["KeepAlive"] is True
    assert parsed["RunAtLoad"] is True
    assert parsed["ThrottleInterval"] == 10
    assert parsed["ProcessType"] == "Background"
    assert parsed["WorkingDirectory"] == "/tmp"
    assert parsed["EnvironmentVariables"]["PATH"].startswith("/opt/homebrew")
    assert "instance: %s" % (HOST,) in out


def test_what_a_default_install_renders_leaves_the_bridge_where_it_was(
        run_sh, mac_args, tmp_path, fake_home, mac):
    """⚠️ The other half of "a default install behaves exactly as before", and
    the half no assertion about the plist can reach: what the BRIDGE then does
    with the path the installer wrote.

    Two files spell that path independently -- `install.sh`'s `DEFAULT_CONFIG`
    and `agb.config_path()` -- and since the flag is unconditional, every
    default install now starts its bridge with it. If the two spellings ever
    disagree the result is not an error anyone would see: `render_settings`
    quietly moves the rows map somewhere new, and `pane_argv` starts emitting
    `--config` on every row of every default install, re-minting commands that
    were fine beside rows agterm is still showing. So the value is taken from
    the RENDERED plist rather than rebuilt here, and run through both.

    ⚠️ The default config is reached by NAMING it, not by omitting `--instance`.
    `install.sh mac --instance X --config <the default path>` is still legal --
    `--instance` only *defaults* `$config`, it does not own it -- so this
    coverage is recoverable, and is recovered here rather than given up.
    Dropping the test would have surrendered the only END-TO-END exercise of
    invariant 14's first cross-file agreement (`install.sh`'s `DEFAULT_CONFIG`
    against `agb.config_path()`); the string comparison in
    `test_the_default_config_path_is_spelled_the_same_in_all_three_places`
    cannot see what the bridge then does with the value.
    """
    # ⚠️ Taken from `install.sh`'s OWN `DEFAULT_CONFIG`, not rebuilt out of
    # `fake_home` here. Rebuilt, the test would compare its own spelling of the
    # default against `agb.config_path()` and the installer's would drop out of
    # the loop entirely -- which is the half of invariant 14 this test exists
    # for, and it would go quiet without failing.
    default_config = _sh_default_config(INSTALL_SH, fake_home)
    code, _out, err = run_sh(mac_args(**{"--config": default_config}))
    assert code == 0, err
    rendered = plistlib.loads(
        read_bytes(tmp_path / "agents" / MAC_PLIST)
    )["ProgramArguments"][-1]
    assert rendered == default_config

    settings = mac.render_settings(mac.parse_bridge_args(["--config",
                                                          rendered]))
    # The same FILE, compared as a file: `rows_path` derives from the config's
    # directory verbatim, so a `$HOME` the two spellings disagree about -- a
    # trailing slash is the realistic one -- gives a path that differs by a
    # separator and opens the same map. That is harmless here and is exactly
    # what `pane_argv`'s `normpath` exists to absorb where it is NOT harmless.
    assert os.path.normpath(settings["rows"]) == mac.rows_path()
    assert os.path.normpath(settings["placements"]) == mac.placements_path()

    session = {"key": "aaaa1111", "host": "box2", "tmux": "build",
               "pane": "%24"}
    before = mac.pane_argv(session, agb_path="/a/agb", python="/py")
    assert mac.pane_argv(session, agb_path="/a/agb", python="/py",
                         config=settings["config"]) == before
    assert "--config" not in before

    # Non-vacuity: the comparison inside `pane_argv` can come out the other
    # way. A named instance's config -- the same installer, the same `$HOME` --
    # moves all three, so none of the three assertions above is true of any
    # path at all.
    other = str(fake_home / ".config" / "agbridge" / "hostb" / "config")
    moved = mac.render_settings(mac.parse_bridge_args(["--config", other]))
    assert moved["rows"] != settings["rows"]
    assert moved["placements"] != settings["placements"]
    assert "--config" in mac.pane_argv(session, agb_path="/a/agb",
                                       python="/py", config=other)


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
    # ⚠️ THE SYNOPSIS LINE, not the file. Nothing else tests `usage()`'s prose,
    # and the `--instance` refusal tells the operator to read a synopsis -- one
    # still showing the command it now refuses sends them back into the same
    # wall. A bare `"--instance <name>" in err` is NOT this assertion: the
    # option table below spells the same words, so deleting the flag from the
    # synopsis leaves it green (measured).
    synopsis = [l for l in err.splitlines()
                if l.startswith("usage: install.sh mac")]
    assert len(synopsis) == 1, err
    assert "--instance <name>" in synopsis[0]
    # ...and `--statedir`, which became required for a FIRST mac install too.
    assert "--statedir" in synopsis[0]


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
# --instance auto: the same probe, spent on the name
# ---------------------------------------------------------------------------
#
# The hostname was already being read back for `host_<name>`. `auto` reuses that
# one answer as the instance name -- so the interesting assertions are not that
# it works, but the three things that must NOT happen: a second ssh, a silent
# fall-back to the default instance when the machine will not answer, and the
# word `auto` becoming redundant.

def _auto_args(mac_args, **over):
    """`--instance auto`, probing, with the paths the instance decides dropped.

    `--config` and `--log-dir` have to go or the sugar has nothing to set --
    every default below it is `[ -n ... ] ||`, so `mac_args`' pinned `--config`
    would win and the test would pass while asserting nothing about `auto`.
    """
    args = {"--config": None, "--log-dir": None, "--instance": "auto"}
    args.update(over)
    return _probing_args(mac_args, **args)


def test_a_feed_host_that_is_an_ssh_option_is_refused_before_auto_probes(
        run_sh, mac_args, stub_bin, tmp_path, fake_home):
    """⚠️ ORDER, and `--instance auto` is what made it matter.

    `shell_safe "--feed-host"` used to run inside `role_mac`, while the `auto`
    block above it already calls `probe_farmhost` -- an
    `ssh "$feedhost" 'hostname -s'`. So the value reached ssh's own option
    parser before the check that exists to keep it out of there:
    `--feed-host -oProxyCommand=…` is the shape, and every character of it is
    inside `shell_safe`'s allowed set except the leading dash it refuses.

    Making `--instance` mandatory promoted `auto` from a rarity to a primary
    path, which is why the ordering stopped being academic. The assertion is
    that **no ssh happened at all** -- an exit code alone cannot tell a refusal
    before the connection from one after it.
    """
    _ssh_answering(stub_bin, "hostb01")
    code, out, err = run_sh(_auto_args(
        mac_args, **{"--feed-host": "-oProxyCommand=/tmp/x"}))
    assert code != 0
    assert "must not start with '-'" in err
    assert stub_bin.calls("ssh") == []
    assert list(fake_home.rglob("config")) == []


def test_instance_auto_names_the_instance_after_the_machine(
        run_sh, mac_args, stub_bin, tmp_path, fake_home, agb):
    """The name, and everything it decides, come off the machine.

    Asserted as a set for the reason the explicit-name test gives: any one of
    the label, the config and the logs can be right while the set is incoherent,
    and it is the set that makes a second bridge a second bridge.
    """
    _ssh_answering(stub_bin, "hostb01")
    code, out, err = run_sh(_auto_args(mac_args))
    assert code == 0, err

    config = _instance_config_path(fake_home, "hostb01")
    plist = tmp_path / "agents" / "com.agbridge.hostb01.plist"
    parsed = plistlib.loads(read_bytes(plist))
    assert parsed["Label"] == "com.agbridge.hostb01"
    assert parsed["ProgramArguments"][-2:] == ["--config", str(config)]
    assert agb.read_config(str(config))["statedir"] == str(tmp_path / "state")

    # Said out loud, and said as a DERIVATION -- `auto` is not a name and a
    # banner that printed it would name nothing.
    assert "instance: auto -> hostb01" in out

    # Non-vacuity, and the isolation claim -- re-anchored on the OTHER instance
    # for the reason given in `test_an_instance_install_agrees_about_its_label
    # _config_and_logs`: there is no default install to be isolated from any
    # more, and `mac_args`' `--instance box2` is what `auto` had to override.
    assert not _instance_config_path(fake_home, HOST).exists()
    assert not (tmp_path / "agents" / MAC_PLIST).exists()


def test_instance_auto_asks_the_machine_once_and_both_readers_use_the_answer(
        run_sh, mac_args, stub_bin, fake_home, agb):
    """ONE ssh, two readers -- and they cannot disagree.

    The name and the `host_<name>` mapping are the same fact, so asking twice
    would be both a wasted round trip and a way for a machine that renamed
    itself between the two calls to produce a config whose mapping does not
    match its own directory. The mapping is asserted because it is the *second*
    reader: if it re-probed, this would still pass -- so the call count is what
    holds it up, and the mapping is what proves the second reader ran at all.
    """
    _ssh_answering(stub_bin, "hostb01")
    code, out, err = run_sh(_auto_args(mac_args))
    assert code == 0, err
    calls = stub_bin.calls("ssh")
    assert len(calls) == 1, calls
    assert calls[0][-2:] == ["box2", "hostname -s"]
    values = agb.read_config(str(_instance_config_path(fake_home, "hostb01")))
    assert values["host_hostb01"] == "box2"


def test_instance_auto_refuses_rather_than_falling_back_to_the_default(
        run_sh, mac_args, stub_bin, tmp_path, fake_home):
    """⚠️ The one that matters. A machine that will not answer is a REFUSAL.

    The probe is best-effort for the host mapping -- it prints a note and you
    pass `--host` yourself (`test_a_failing_probe_is_not_fatal_and_says_what_to
    _do`). It cannot be best-effort for the NAME, because the fall-back is the
    DEFAULT instance: that run would rewrite the first machine's `feed_host` and
    `statedir`, boot out its launchd job and point its bridge at the new box --
    reporting success in the same words a correct run uses.

    So: non-zero, and nothing written anywhere.
    """
    _ssh_answering(stub_bin, "", exit_code=255)
    code, out, err = run_sh(_auto_args(mac_args))
    assert code != 0
    assert "--instance auto" in err
    assert "DEFAULT instance" in err            # it says WHY it is refusing
    assert not (fake_home / ".config" / "agbridge" / "config").exists()
    assert not (tmp_path / "agents").exists()
    assert not (tmp_path / "dest").exists()


def test_instance_auto_refuses_a_hostname_that_is_not_a_usable_name(
        run_sh, mac_args, stub_bin, tmp_path, fake_home):
    """A hostname may hold a `.`; an instance name may not.

    `probe_farmhost` allows one, because a `.` is fine in a `host_<name>` key
    and this is the same answer serving both readers -- so `auto` re-asks with
    the narrower rule. The message names the HOST, since `auto` is what the
    operator typed and a complaint about `weird.name` would otherwise read as
    being about something they wrote.
    """
    _ssh_answering(stub_bin, "weird.name")
    code, out, err = run_sh(_auto_args(mac_args))
    assert code != 0
    assert "weird.name" in err
    assert "box2" in err                        # ...and where it came from
    assert not (fake_home / ".config" / "agbridge" / "weird.name").exists()
    assert not (tmp_path / "dest").exists()


@pytest.mark.parametrize("over,wanted", [
    ({"--feed-host": None}, "--feed-host"),
    ({}, "--no-probe"),
])
def test_instance_auto_refuses_when_it_could_not_ask_at_all(
        run_sh, mac_args, stub_bin, tmp_path, over, wanted):
    """Two ways of asking for a derived name while removing the derivation.

    Both are refused up front rather than at the ssh, so neither can reach the
    fall-back the test above is about. The `--no-probe` case rebuilds the argv
    from `mac_args` (which carries it) instead of `_auto_args` (which strips
    it) -- the flag under test is the one being put back.
    """
    _ssh_answering(stub_bin, "hostb01")
    args = {"--config": None, "--log-dir": None, "--instance": "auto"}
    args.update(over)
    argv = (mac_args(**args) if wanted == "--no-probe"
            else _auto_args(mac_args, **over))
    code, out, err = run_sh(argv)
    assert code != 0
    assert wanted in err
    assert stub_bin.calls("ssh") == []          # refused before asking
    assert not (tmp_path / "dest").exists()


def test_instance_auto_is_refused_on_the_farm_role(run_sh, tmp_path, stub_bin):
    """Nothing on the farm reads a per-instance config, `auto` or otherwise."""
    _ssh_answering(stub_bin, "hostb01")
    code, out, err = run_sh(_farm(tmp_path, **{"--instance": "auto"}))
    assert code != 0
    assert "mac role" in err
    assert stub_bin.calls("ssh") == []


def test_an_absent_instance_never_reaches_the_probe_that_could_name_it(
        run_sh, mac_args, stub_bin, tmp_path, fake_home, agb):
    """⚠️ Why `auto` is a WORD, restated for a mandatory `--instance`.

    This was `test_an_absent_instance_is_the_default_one_even_when_the_probe
    _answers`, and its subject -- an absent `--instance` meaning the default
    instance -- was deleted outright: the mac role refuses a nameless install.
    The rule it protected survives and is what is asserted here. A name is never
    DERIVED from a machine nobody asked about, so an install that omits
    `--instance` cannot quietly become `hostb01` (a new config, a new launchd
    job, a new rows map, every row duplicated) on the strength of an answer the
    operator never saw.

    ⚠️ And the assertable form is stronger than "refused even when the probe
    answers": measured, the refusal at the top of `role_mac` fires *before*
    `probe_farmhost`, so the ssh is never made at all. There is no answer to
    ignore -- which is why the old non-vacuity line (`assert "hostb01" in out`)
    cannot come along, and why its replacement is a companion run rather than an
    assertion about this one.
    """
    _ssh_answering(stub_bin, "hostb01")
    code, out, err = run_sh(_probing_args(mac_args, **{"--instance": None,
                                                       "--config": None,
                                                       "--log-dir": None}))
    assert code != 0
    assert "--instance" in err
    # The probe was never consulted, so no name could have been invented.
    assert stub_bin.calls("ssh") == []
    # Nothing was written -- not the derivable name, and not the nameless
    # instance it used to fall through to.
    assert not _instance_config_path(fake_home, "hostb01").exists()
    assert not (tmp_path / "dest").exists()
    assert not (tmp_path / "agents").exists()
    assert list(fake_home.rglob("config")) == []

    # ⚠️ The companion, differing only in the variable under test: the same argv
    # WITH `--instance auto` does reach the probe and does install. Without it
    # the assertions above would hold just as well against a stub that cannot
    # answer, an installer that never probes on any path, or a `--no-probe` that
    # slipped back into the argv.
    code, out, err = run_sh(_auto_args(mac_args))
    assert code == 0, err
    assert len(stub_bin.calls("ssh")) == 1
    assert "instance: auto -> hostb01" in out
    assert agb.read_config(
        str(_instance_config_path(fake_home, "hostb01")))["mac_id"]


# ---------------------------------------------------------------------------
# the `agb` wrapper -- what makes every doc example true
# ---------------------------------------------------------------------------

def _farm(tmp_path, **over):
    """The farm role with every path pinned inside tmp_path.

    `None` drops a flag, the same spelling `mac_args` uses -- the farm role
    genuinely takes some of these optionally (`--statedir` above all), and a
    helper that could only ever ADD flags cannot state that.
    """
    args = {"--mac-id": "mac-0001",
            "--config": str(tmp_path / "config"),
            "--statedir": str(tmp_path / "state"),
            "--settings": str(tmp_path / "settings.json"),
            "--python": sys.executable}
    args.update(over)
    argv = ["farm"]
    for name, value in sorted(args.items()):
        if value is None:
            continue
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


# ---------------------------------------------------------------------------
# the two cross-file agreements nothing else can catch
# ---------------------------------------------------------------------------
#
# ⚠️ Neither of these has a single source of truth, and neither can: `agb` is
# Python under a byte cap, `install.sh` and `agb-refresh` are POSIX sh, and no
# one of the three can import the others. So the agreement is asserted here
# instead. Both failures are silent -- a wrong answer that installs cleanly.


def _sh_assignment(script, name):
    """The right-hand side of `NAME=...` in a POSIX-sh script."""
    with open(script) as handle:
        for line in handle.read().splitlines():
            if line.startswith(name + "="):
                return line[len(name) + 1:].strip()
    raise AssertionError("%s not assigned in %s" % (name, script))


def _sh_default_config(script, home):
    """`$DEFAULT_CONFIG` as that script spells it, resolved against `home`.

    `"$DEFAULT_CONFIG_DIR/config"` -> the real path. Shared with
    `test_what_a_default_install_renders_leaves_the_bridge_where_it_was`, which
    has to *pass the installer its own spelling* rather than rebuild one.
    """
    spelled = _sh_assignment(script, "DEFAULT_CONFIG").strip('"')
    resolved = spelled.replace(
        "$DEFAULT_CONFIG_DIR",
        _sh_assignment(script, "DEFAULT_CONFIG_DIR").strip('"'))
    return resolved.replace("$HOME", str(home))


def test_the_default_config_path_is_spelled_the_same_in_all_three_places(agb):
    """⚠️ `agb.config_path()`, `install.sh` and `agb-refresh` each spell it
    independently, and a disagreement is invisible at install time.

    `pane_argv` emits `--config` only when the path differs from
    `agb.config_path()`, so an installer that spelled the default even slightly
    differently would make **every install that names it** re-mint **every row**
    -- and the install itself would report success. `agb-refresh`'s copy decides
    which map a plain refresh repairs.

    ⚠️ `install.sh`'s RUNTIME use of `DEFAULT_CONFIG` narrowed with this change,
    and the difference is worth stating rather than discovering. The mac role
    can no longer fall through to it (`--instance` is required, and `$config`
    then comes from the instance convention), so the fall-through line survives
    for the `farm` role -- kept exercised by
    `test_install_sh_farm_defaults_the_config_to_the_users_own_dotfile`, which
    passes no `--config` at all. The value is still reached END TO END on the
    mac side too, by being *named*:
    `test_what_a_default_install_renders_leaves_the_bridge_where_it_was` feeds
    this very spelling to the installer and reads it back out of the plist. So
    this test remains the string guard, and is not the only guard.
    """
    expected = agb.config_path()
    home = agb.home_dir()
    for script in (INSTALL_SH, REFRESH_SH):
        assert _sh_default_config(script, home) == expected, script


def test_the_print_statedir_none_status_is_the_same_number_in_both_files(ops):
    """⚠️ A cross-file agreement with a NUMBER in it (invariant 14).

    `agb install-config --print-statedir` answers `PRINT_STATEDIR_NONE` for
    "this file carries no statedir of its own" and 1 for "I could not read it
    at all", and `install.sh` swallows only the first. It cannot import
    `agb_ops`, so it spells the number itself -- and a disagreement is silent
    in the worst direction: every unreadable config would be reported as
    *carries none to adopt* and the operator sent after `--statedir`, which is
    the exact misdirection the two statuses were separated to remove.

    Compared as the value, not as the text: what matters is the number the two
    sides act on.
    """
    spelled = _sh_assignment(INSTALL_SH, "PRINT_STATEDIR_NONE").strip('"')
    assert int(spelled) == ops.PRINT_STATEDIR_NONE
    # ...and it is not one of the statuses that already mean something else:
    # 1 is every AgbError, 2 is `agb`'s unknown-command answer, 3 is
    # `run_ops`' known-but-unbuilt one.
    assert ops.PRINT_STATEDIR_NONE not in (0, 1, 2, 3)


def test_the_two_instance_name_validators_accept_exactly_the_same_names():
    """⚠️ A name `agb-refresh` accepts and `install.sh` refuses names a plist
    that was never rendered; the other way round is worse -- the installer would
    write four things somewhere they were not meant to go.

    The *messages* deliberately differ (one writes those four things, the other
    goes looking for three of them), so what is compared is the rule: the `case`
    patterns, which is the whole of the accept/reject decision.
    """
    def patterns(script):
        body = extract_sh_function("instance_ok", script)
        found = [line.split(")")[0].strip()
                 for line in body.splitlines()
                 if ")" in line and "die" in line or line.strip().endswith(")")]
        # Only the arms, i.e. lines inside the `case`, not `case`/`esac`.
        return [p for p in found if p and not p.startswith(("case", "esac"))]

    install = patterns(INSTALL_SH)
    refresh = patterns(REFRESH_SH)
    assert install, "no case arms found -- the extraction stopped working"
    assert install == refresh, (install, refresh)


def test_the_two_missing_value_checks_are_the_same_check():
    """⚠️ `need` is the third thing spelled twice, and the failure is silent.

    An empty value is a MISSING value -- `--config "$cfg"` with `$cfg` unset is
    one empty argument, so a check that counts arguments passes it, the flag's
    default takes over, and the install or the refresh succeeds against
    something other than what was named. `agb`'s nine Python parsers have always
    refused it (`if not inline: raise ... needs a value`); the two shell scripts
    each spell their own, because neither can import the other.

    Compared as text, unlike `instance_ok` above: this one IS one line and there
    is nothing in it that may legitimately differ between the two scripts.
    """
    def one_liner(script):
        with open(script) as handle:
            found = [line.strip() for line in handle.read().splitlines()
                     if line.startswith("need() {")]
        assert len(found) == 1, "need() not found in %s: %s" % (script, found)
        return found[0]

    bodies = [one_liner(script) for script in (INSTALL_SH, REFRESH_SH)]
    assert "-n " in bodies[0], (
        "the emptiness half is gone: %s" % (bodies[0],))
    assert bodies[0] == bodies[1], bodies


@pytest.mark.parametrize("script", [INSTALL_SH, REFRESH_SH])
def test_every_option_that_consumes_a_value_asks_need_for_it(script):
    """The completeness half, which the two tests above cannot give.

    `need` being right is worth nothing on a flag that does not call it, and
    the next flag added to either loop is exactly where that would happen. So
    the rule is asserted against the SHAPE of the option loop: any arm that
    reads `$2` must have called `need` first. Arms that take no value never
    mention `$2` and are not the subject.

    ⚠️ Arms are split on their INDENTATION, not on `;;`. `--host` contains a
    nested `case "$2" in … esac` whose own `;;` would cut that arm in half and
    leave the tail looking like an arm that reads `$2` without asking `need` --
    a guard that fails on the one flag with the most validation in it.
    """
    import re
    with open(script) as handle:
        text = handle.read()
    start = text.index("while [ $# -gt 0 ]")
    loop = text[start:text.index("\ndone\n", start)]
    arms = []
    for line in loop.splitlines():
        if re.match(r"^ {8}[-*][^)]*\)", line):
            arms.append([line])
        elif arms:
            arms[-1].append(line)
    assert len(arms) > 5, "the option loop was not found: %r" % (loop[:200],)
    consuming = ["\n".join(arm) for arm in arms if "$2" in "\n".join(arm)]
    assert len(consuming) > 5, "no value-taking arm was found: %s" % (arms,)
    for arm in consuming:
        assert "need $#" in arm, arm


def test_the_launch_agents_dir_and_label_prefix_are_spelled_the_same_everywhere(
        agb, mac):
    """⚠️ A fourth cross-file agreement (invariant 14).

    `agb_mac.default_agents_dir()` and `INSTANCES_LABEL_PREFIX` are a third
    spelling beside install.sh and agb-refresh. A disagreement makes
    `agb instances --labels` scan the wrong directory or mis-classify plists
    while the installers write to the right place -- and it is invisible at
    install time because neither file can import the other.

    Patterns compared, never substring greps.
    """
    home = agb.home_dir()
    expected_dir = mac.default_agents_dir()
    expected_label = mac.INSTANCES_LABEL_PREFIX
    # Non-vacuity: the values must be non-empty and meaningful.
    assert "Library/LaunchAgents" in expected_dir
    assert expected_label == "com.agbridge"
    for script in (INSTALL_SH, REFRESH_SH):
        agents_raw = _sh_assignment(script, "DEFAULT_AGENTS").strip('"')
        agents = agents_raw.replace("$HOME", home)
        assert agents == expected_dir, (script, agents, expected_dir)
        label = _sh_assignment(script, "DEFAULT_LABEL").strip('"')
        assert label == expected_label, (script, label, expected_label)


# ⚠️ EVERY `install.sh:<line>` CITATION IN THE TREE, AND WHAT THE CITED LINE MUST
# SAY. Comments in `agb-refresh`, `agb_mac` and two test files point at
# `install.sh` by line number, and those numbers have drifted twice inside one
# branch: a reader following one lands in the middle of an unrelated block and
# concludes the rule moved or was dropped. Nothing noticed, because a stale
# number is still a number.
#
# The entry is keyed on a PHRASE from the citation's own comment rather than on
# the number, so correcting a number cannot silently re-point the entry that
# checks it. `expect` is what the cited line (or any line of a cited range) must
# contain -- chosen as the thing the sentence is actually about, so the guard
# fails when the code moves rather than when the file merely grows.
#
# ⚠️ The same drift exists for `agb-refresh:<line>` and `agb_mac:<line>`
# citations; they are not pinned here because two of them were already wrong
# before this table existed and re-anchoring them is a separate reading job.
# Adding rows for them is the way to cover them, not a second table.
INSTALL_SH_CITATIONS = (
    ("agb-refresh", "derives its own from", 'DEFAULT_CONFIG_DIR="'),
    ("agb-refresh", "a name is a launchd label component", "instance_ok() {"),
    ("agb-refresh", "ASK THE PLIST rather than rebuild",
     'config="$DEFAULT_CONFIG_DIR/$instance/config"'),
    ("agb_mac", "no shape rule on a label", "--label)"),
    ("tests/test_agb_refresh.py", "escapes it (`xml_escape`", "xml_escape() {"),
    ("tests/test_agb_refresh.py", "`install.sh` does it",
     'config="$DEFAULT_CONFIG_DIR/$instance/config"'),
    ("tests/test_agb_refresh.py",
     "so `weird.label` is a real install and the sweep is", "--label)"),
    ("tests/test_bridge_rows.py",
     "so `weird.label` is a real install with no name but its", "--label)"),
)

# Spelled apart from the literal so this file does not cite itself into its own
# scan. `install.sh` followed by a colon and a line number, optionally a range.
_CITATION_RE = re.compile(r"install\.sh" + ":" + r"(\d+)(?:-(\d+))?")

# How far from the anchor phrase the citation may sit. Every real one is on the
# same line; the window is a little slack for a rewrap, not a search.
_CITATION_WINDOW = 200


def _repo_text(relative):
    with open(os.path.join(conftest.REPO_ROOT, relative)) as handle:
        return handle.read()


def test_every_install_sh_line_citation_points_at_what_it_claims():
    """⚠️ A cross-file line number is a claim, and nothing checked it.

    Four of these were stale at once after a refactor moved `install.sh` by six
    lines: `instance_ok` cited at 171 when it was at 189, `xml_escape` at 348
    when it was at 354, the `--label` arm at 426 when it was at 432. Each was
    found by a human reading the file, which is the expensive way.

    Two properties, and they are different: every citation in the table lands on
    a line that still says what the citing comment says it says, AND every
    citation *in the tree* is in the table -- so a new one added without a row
    here fails rather than joining the drift unnoticed.
    """
    install_lines = _repo_text("install.sh").split("\n")
    assert len(install_lines) > 500                # non-vacuity: it really read

    seen = 0
    for relative, anchor, expect in INSTALL_SH_CITATIONS:
        text = _repo_text(relative)
        assert text.count(anchor) == 1, (relative, anchor, text.count(anchor))
        at = text.index(anchor)
        window = text[max(0, at - _CITATION_WINDOW):at + _CITATION_WINDOW]
        found = _CITATION_RE.findall(window)
        assert len(found) == 1, (relative, anchor, found)
        first, last = found[0]
        lines = install_lines[int(first) - 1:int(last or first)]
        assert lines, (relative, anchor, first, last)
        assert any(expect in line for line in lines), \
            (relative, anchor, first, last, lines)
        seen += 1
    assert seen == len(INSTALL_SH_CITATIONS)

    # ...and nothing cites `install.sh` from outside the table. `docs/plans/` is
    # skipped: those are dated records of what was true when they were written
    # and are deliberately not re-anchored.
    total = 0
    for root, dirs, files in os.walk(conftest.REPO_ROOT):
        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__")]
        if os.path.relpath(root, conftest.REPO_ROOT).startswith(
                os.path.join("docs", "plans")):
            continue
        for name in files:
            path = os.path.join(root, name)
            try:
                with open(path, "rb") as handle:
                    body = handle.read().decode("utf-8")
            except (UnicodeDecodeError, IOError, OSError):
                continue
            if os.path.samefile(path, os.path.abspath(__file__)):
                continue          # this file's own table and regex, not a cite
            total += len(_CITATION_RE.findall(body))
    assert total == len(INSTALL_SH_CITATIONS), total
