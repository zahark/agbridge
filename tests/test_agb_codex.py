"""`agb-codex` -- the same wrapper for Codex.

Driven through a recording `tmux` stub rather than a real server: what matters
is the argv it constructs, and a real tmux would make that invisible.

⚠️ It is a near-copy of `agb-claude` and that is deliberate, not laziness. The
two are expected to DIVERGE: Codex has no "is this a project you trust?" prompt
(the one thing that makes `-d` fragile for Claude), it has `resume`/`queue`
that Claude has no equivalent of, and it fires no agbridge hooks at all. The
same reasoning is recorded for `open_split`/`open_drawer` in
`tests/test_identity.py`. What must NOT diverge is the pre-mint, and the tests
below are the ones that pin it.
"""

import os
import subprocess

import pytest

import conftest


SCRIPT = os.path.join(conftest.REPO_ROOT, "agb-codex")


@pytest.fixture
def wrapper(tmp_path):
    """`agb-codex` with recording `tmux` and `claude` stubs on $PATH."""
    binder = tmp_path / "bin"
    binder.mkdir()
    work = tmp_path / "work"
    work.mkdir()
    log = tmp_path / "tmux.log"

    (binder / "tmux").write_text(
        "#!/bin/sh\n"
        "{ for a in \"$@\"; do printf '%s\\037' \"$a\"; done; printf '\\n'; }"
        " >> \"" + str(log) + "\"\n"
        "case \"$1\" in has-session) exit ${AGBC_HAS_SESSION:-1} ;; esac\n"
        "exit 0\n")
    (binder / "codex").write_text("#!/bin/sh\nexit 0\n")
    # A stand-in launcher for AGB_CODEX_CUSTOM, recording its argv so a test
    # can assert what the eval'ed command line actually resolved to -- the
    # quoting is the whole point of that feature and a string comparison
    # against the pre-mint would not see it.
    submit_log = tmp_path / "submit.log"
    (binder / "submit").write_text(
        "#!/bin/sh\n"
        "{ for a in \"$@\"; do printf '%s\\037' \"$a\"; done; printf '\\n'; }"
        " >> \"" + str(submit_log) + "\"\nexit 0\n")
    # An `agb` that records being called. The wrapper must NEVER reach it: the
    # pre-mint belongs inside the new session, where the pane anchor is right.
    agb_log = tmp_path / "agb.log"
    (binder / "agb").write_text(
        "#!/bin/sh\n"
        "{ for a in \"$@\"; do printf '%s\\037' \"$a\"; done; printf '\\n'; }"
        " >> \"" + str(agb_log) + "\"\nexit 0\n")
    for name in ("tmux", "codex", "agb", "submit"):
        os.chmod(str(binder / name), 0o755)

    class Wrapper(object):
        cwd = str(work)

        def run(self, args, inside_tmux=False, has_session=False,
                no_codex=False, custom=None):
            env = dict(os.environ)
            env["PATH"] = str(binder) + os.pathsep + env.get("PATH", "")
            # ⚠️ Popped unconditionally, not merely left alone. A developer who
            # actually uses AGB_CODEX_CUSTOM would otherwise take it into every
            # test in this file, and the ones that pin the DEFAULT path would
            # fail on their machine and nowhere else.
            env.pop("AGB_CODEX_CUSTOM", None)
            if custom is not None:
                env["AGB_CODEX_CUSTOM"] = custom
            env["TMUX"] = "/tmp/fake,1,0" if inside_tmux else ""
            env["AGBC_HAS_SESSION"] = "0" if has_session else "1"
            if no_codex:
                # ⚠️ Removing the stub is not enough: the REAL codex is on the
                # developer's $PATH, so the check would pass against it and the
                # test would prove nothing. The path is narrowed to the stub
                # directory plus the coreutils the script itself needs.
                os.remove(str(binder / "codex"))
                env["PATH"] = os.pathsep.join([str(binder), "/usr/bin", "/bin"])
            if not inside_tmux:
                env.pop("TMUX", None)
            proc = subprocess.Popen(["sh", SCRIPT] + list(args), cwd=str(work),
                                    env=env, stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE)
            out, err = conftest.communicate(proc, b"")
            return proc.returncode, out.decode(), err.decode()

        def calls(self):
            if not log.exists():
                return []
            return [line.split("\037")[:-1]
                    for line in log.read_text().splitlines() if line]

        def new_session(self):
            return [c for c in self.calls() if "new-session" in c]

        def agb_calls(self):
            if not agb_log.exists():
                return []
            return [line.split("\037")[:-1]
                    for line in agb_log.read_text().splitlines() if line]

        def premint(self):
            call = self.new_session()[0]
            return [w for w in call if "agb hook" in w][0]

        def run_premint(self):
            """Actually execute the command tmux was handed, and return the
            argv the launcher received.

            A string assertion on the pre-mint cannot see whether the quoting
            survives -- that is decided by `eval`, at run time, in the session.
            """
            env = dict(os.environ)
            env["PATH"] = str(binder) + os.pathsep + env.get("PATH", "")
            proc = subprocess.Popen(["sh", "-c", self.premint(), "agb-codex"],
                                    cwd=str(work), env=env,
                                    stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE)
            conftest.communicate(proc, b"")
            if not submit_log.exists():
                return []
            return [line.split("\037")[:-1]
                    for line in submit_log.read_text().splitlines() if line]

    return Wrapper()


def test_it_execs_codex_not_claude(wrapper):
    wrapper.run(["-d", "bot"])
    call = wrapper.new_session()[0]
    assert any("exec codex" in w for w in call), call
    assert not any("exec claude" in w for w in call), call


def test_passthrough_args_come_after_codex(wrapper):
    wrapper.run(["-d", "bot", "--", "--model", "gpt-5.6-terra"])
    call = wrapper.new_session()[0]
    assert call[-2:] == ["--model", "gpt-5.6-terra"], call


def test_every_launch_path_mints_the_row_first(wrapper):
    """The whole point of the wrapper for Codex, and more so than for Claude:
    Codex fires NO agbridge hooks, so without the pre-mint the row would never
    exist at all -- not on the first prompt, not ever."""
    for args, kw in ((["-d", "bot"], {}), (["bot"], {}),
                     (["bot"], {"inside_tmux": True})):
        wrapper.run(args, **kw)
    for call in wrapper.new_session():
        assert any("agb hook completed" in w for w in call), call


def test_the_premint_runs_inside_the_new_session(wrapper):
    """The anchor is (host, tmux server pid, %PANE): hooking from the caller's
    pane would mint a row for the CALLER's pane."""
    wrapper.run(["-d", "bot"])
    assert wrapper.agb_calls() == [], wrapper.agb_calls()
    assert any("agb hook" in w for w in wrapper.new_session()[0])


def test_the_premint_carries_its_own_pid_so_codex_adopts_the_row(wrapper):
    """`exec` preserves pid AND starttime, so the identity the shell records is
    Codex's a moment later and `bind_key` adopts rather than minting a second
    row. Dropping either the pid or the `exec` gives two rows."""
    wrapper.run(["-d", "bot"])
    call = wrapper.new_session()[0]
    premint = [w for w in call if "agb hook" in w][0]
    assert "AGB_AGENT_PID=$$" in premint, premint
    assert premint.strip().split(";")[-1].strip().startswith("exec "), premint


def test_the_premint_state_is_completed_not_active(wrapper):
    """A session at an empty prompt is waiting for you. `active` would claim it
    is working and blink a transition that never happened."""
    call = (wrapper.run(["-d", "bot"]), wrapper.new_session()[0])[1]
    premint = [w for w in call if "agb hook" in w][0]
    assert "agb hook completed" in premint and "agb hook active" not in premint


def test_a_broken_agb_costs_a_row_and_never_a_codex(wrapper):
    """`;` not `&&`, and stderr discarded."""
    call = (wrapper.run(["-d", "bot"]), wrapper.new_session()[0])[1]
    premint = [w for w in call if "agb hook" in w][0]
    assert "&&" not in premint, premint
    assert "2>/dev/null" in premint, premint


def test_the_no_hooks_caveat_is_documented_loudly(wrapper):
    """Codex fires no agbridge hooks, so the row it mints stays `completed` for
    ever -- the glyph never moves and agb-peer's status gate is useless for it.
    That is the single most surprising thing about a Codex row, and the file
    must say so rather than leave it to be discovered."""
    body = open(SCRIPT, encoding="utf-8").read()
    head = body[:body.index("set -e")]
    assert "FIRES NO agbridge HOOKS" in head
    assert "status gate" in head


def test_it_refuses_when_codex_is_missing(wrapper, tmp_path):
    code, _out, err = wrapper.run(["-d", "bot"], no_codex=True)
    assert code != 0
    assert "codex is not installed" in err


# --------------------------------------------------------------------------
# AGB_CODEX_CUSTOM -- the codex command line, replaced wholesale.
#
# It exists because the interesting Codex is often not the one on this host: it
# may live behind a batch scheduler or on a pool machine picked at submit time,
# and that launcher is site-specific, so it cannot live in a file that ships
# publicly. The variable is the seam, and these pin its edges.
# --------------------------------------------------------------------------

CUSTOM = 'submit -q big -I "codex --yolo"'


def test_a_custom_command_replaces_codex_entirely(wrapper):
    wrapper.run(["-d", "bot"], custom=CUSTOM)
    premint = wrapper.premint()
    assert "exec codex" not in premint, premint
    assert "eval exec" in premint, premint


def test_the_custom_command_is_embedded_not_inherited(wrapper):
    """⚠️ It reaches the session as part of the command tmux is HANDED, never
    through the environment. A session created against an already-running tmux
    server takes its environment from the SERVER's, plus `update-environment` --
    so a variable exported in the caller's shell a moment ago is not there, and
    a version of this that relied on inheritance would exec nothing at all on
    every machine where a tmux server was already up."""
    wrapper.run(["-d", "bot"], custom=CUSTOM)
    assert CUSTOM in " ".join(wrapper.new_session()[0])


def test_the_custom_commands_own_quoting_survives_to_the_launcher(wrapper):
    """The point of `eval`, and the reason a string assertion is not enough:
    `-I "codex --yolo"` must reach the launcher as ONE argument. Word-splitting
    it would hand the launcher `"codex` and `--yolo"`, which is not a mistake
    any downstream error message would explain."""
    wrapper.run(["-d", "bot"], custom=CUSTOM)
    assert wrapper.run_premint() == [["-q", "big", "-I", "codex --yolo"]]


def test_a_single_quote_in_the_custom_command_survives(wrapper):
    """The value is embedded as a single-quoted shell literal, so a `'` in it
    is the one character that can end the quoting early and turn the rest of
    the command line into something else."""
    wrapper.run(["-d", "bot"], custom="""submit -I "codex 'a b'\"""")
    assert wrapper.run_premint() == [["-I", "codex 'a b'"]]


def test_passthrough_args_without_a_placeholder_are_refused_and_it_is_named(wrapper):
    """There is no honest place to append them: with `-I "codex --yolo"` the
    agent is inside somebody else's argument, so a trailing word lands on the
    launcher instead. Refusing says so; appending would be a silent guess. The
    refusal names `{}`, because "there is nowhere to put this" is only useful
    with "here is how to say where"."""
    code, _out, err = wrapper.run(["-d", "bot", "--", "--model", "x"],
                                  custom=CUSTOM)
    assert code != 0
    assert "no {} placeholder" in err and "--model x" in err
    assert wrapper.new_session() == []


def test_greet_is_refused_with_a_custom_command(wrapper):
    code, _out, err = wrapper.run(["-d", "bot", "--greet", "hi"], custom=CUSTOM)
    assert code != 0
    assert "--greet has nowhere to go" in err
    assert wrapper.new_session() == []


def test_a_custom_command_whose_program_is_missing_is_refused(wrapper):
    """Only the first word can be checked -- the rest is the launcher's own
    business -- but checking it turns a typo into an error here rather than a
    session that opens, execs nothing and closes again."""
    code, _out, err = wrapper.run(["-d", "bot"], custom="nosuchlauncher -I x")
    assert code != 0
    assert "nosuchlauncher" in err and "not on $PATH" in err
    assert wrapper.new_session() == []


def test_a_custom_command_that_names_no_command_is_refused(wrapper):
    code, _out, err = wrapper.run(["-d", "bot"], custom="   ")
    assert code != 0
    assert "names no command" in err


def test_an_empty_variable_is_the_default_path(wrapper):
    """`${AGB_CODEX_CUSTOM:-}`, so an exported-but-empty variable -- what you
    get from `export AGB_CODEX_CUSTOM=` to turn it off for one shell -- behaves
    exactly as unset rather than as a custom command naming nothing."""
    code, _out, err = wrapper.run(["-d", "bot"], custom="")
    assert code == 0, err
    assert "exec codex" in wrapper.premint()


def test_the_premint_is_unchanged_by_a_custom_command(wrapper):
    """Everything the pre-mint exists for still holds: it runs first, inside
    the new session, carrying its own pid, best-effort, and `completed`. The
    launcher is now the pane's own process -- which is the RIGHT one to record,
    since its death is what should reap the row even when the agent itself is
    running on another machine."""
    wrapper.run(["-d", "bot"], custom=CUSTOM)
    premint = wrapper.premint()
    assert premint.startswith("AGB_AGENT_PID=$$ agb hook completed"), premint
    assert "&&" not in premint and "2>/dev/null" in premint, premint
    assert "agb hook active" not in premint, premint
    assert wrapper.agb_calls() == [], wrapper.agb_calls()


def test_the_custom_seam_is_documented_where_it_is_looked_for(wrapper):
    """A variable nothing on the command line mentions is invisible: `--help`
    is where somebody goes to find out that it exists at all."""
    code, _out, err = wrapper.run(["--help"])
    assert code == 0
    assert "AGB_CODEX_CUSTOM" in err


# --------------------------------------------------------------------------
# The two placeholders. `{}` is where THIS invocation's agent flags go; `{env}`
# is the identity a remotely-launched agent must report. Without `{}` there is
# no position the wrapper could pick that is not a guess, which is why args
# were refused outright before it existed.
# --------------------------------------------------------------------------

NESTED = 'submit -q big -I "codex --yolo {}"'


def test_switches_reach_the_agent_through_the_placeholder(wrapper):
    """The whole point: `-I "codex --yolo {}"` must still hand the launcher ONE
    argument, with this invocation's flags inside it. Asserting on the pre-mint
    string cannot see that -- the nesting is decided by `eval`, at run time."""
    wrapper.run(["-d", "bot", "--", "--model", "gpt-5.6", "--sandbox"],
                custom=NESTED)
    assert wrapper.run_premint() == [
        ["-q", "big", "-I", "codex --yolo --model gpt-5.6 --sandbox"]]


def test_the_env_placeholder_carries_what_a_remote_agent_must_report(wrapper):
    wrapper.run(["-d", "bot"], custom='submit -I "{env} codex"')
    premint = wrapper.premint()
    assert "AGB_HOST=" in premint and "AGB_AGENT_PID=none" in premint


def test_the_env_placeholder_spells_agbs_own_host(wrapper, agb):
    """⚠️ A cross-file agreement (CLAUDE.md invariant 14). A POSIX-sh wrapper
    cannot import `agb`, so it spells `own_host()`'s resolution itself --
    `$AGB_HOST` first, else `uname -n`, domain stripped. A disagreement raises
    no error anywhere: the remote agent reports a host nothing has a mapping
    for, and you get a SECOND row instead of the one just minted."""
    wrapper.run(["-d", "bot"], custom='submit -I "{env} codex"')
    assert "AGB_HOST=%s " % (agb.own_host(),) in wrapper.premint()


def test_an_argument_that_would_change_the_parsing_is_refused(wrapper):
    """Spliced verbatim, so the allowed set is a POSITIVE list rather than a
    list of things to escape. The wrapper cannot know whether `{}` sits inside
    quotes, so it cannot quote for you -- and mangling somebody's command line
    quietly is worse than refusing it loudly."""
    for bad in ("hello world", 'a"b', "a$b", "a;b", "a`b`", "a'b"):
        code, _out, err = wrapper.run(["-d", "bot", "--", bad], custom=NESTED)
        assert code != 0, bad
        assert "verbatim" in err, (bad, err)
        assert wrapper.new_session() == [], bad


def test_a_placeholder_with_no_arguments_is_not_an_error(wrapper):
    """`{}` in the variable is a *position*, not a requirement. A launcher line
    written once must still work on the ordinary run that passes no flags."""
    code, _out, err = wrapper.run(["-d", "bot"], custom=NESTED)
    assert code == 0, err
    assert wrapper.run_premint() == [["-q", "big", "-I", "codex --yolo "]]


def test_every_occurrence_of_the_placeholder_is_replaced(wrapper):
    code, _out, err = wrapper.run(["-d", "bot", "--", "--yolo"],
                                  custom='submit {} -I "codex {}"')
    assert code == 0, err
    assert wrapper.run_premint() == [["--yolo", "-I", "codex --yolo"]]


def test_the_program_check_steps_over_leading_assignments(wrapper):
    """`{env}` puts two `VAR=value` words in front of the agent, and no shell
    would call those the command. Taking the first word blindly would refuse a
    perfectly good launcher line, naming a variable as a missing program."""
    code, _out, err = wrapper.run(["-d", "bot"], custom='{env} submit -I "codex"')
    assert code == 0, err
    assert wrapper.new_session() != []
