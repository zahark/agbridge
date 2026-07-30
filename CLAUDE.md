# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```sh
python3 -m pytest tests/ -q                                    # full suite (1271 tests, ~20 s)
python3 -m pytest tests/test_hook.py -q                        # one file
python3 -m pytest tests/test_hook.py::test_beat_refresh_is_throttled -q   # one test
python3 -m pytest tests/ -q -k "prune and not ssh"             # by expression
sh -n install.sh                                               # shell syntax check
```

There is no build step, no linter config, and no dependency install: the tool is stdlib-only and
the suite needs only `pytest`. Tests require no network, no second host and no Mac — a remote
machine is simulated by writing entries whose `host` differs from the local one.

**Python 3.6.8 is the floor.** No f-strings with `=`, no dataclasses, no walrus, no
`subprocess.run(capture_output=)`. This is a hard target, not a preference: the tool runs on
cluster hosts whose interpreter you do not control.

## Architecture

### Three files, and why it is not over-engineering

| File | Loaded by | Contains |
|---|---|---|
| `agb` | **every** hook invocation | hot path, feed, identity, the shared unlink authority, primitives |
| `agb_mac` | `agb bridge`, `agb close-done` | the Mac side: transport, watchdog, row bijection, rendering |
| `agb_ops` | `doctor`/`prune`/`pane`/`status-line`/`install-*` | operator and diagnostic commands |

Plus **`agb-claude`**, a standalone POSIX-sh script that is not part of `agb` at all: it starts
Claude Code in a named tmux session. It exists because the session name is resolved once, at an
agent's first hook, so it has to be set before the agent starts.

`agb hook` runs on **every Claude Code tool call**, over a network filesystem. `agb` has no `.py`
extension and runs as `__main__`, so **CPython caches no bytecode for it** — the whole file is
re-parsed every single invocation. (This is a property of being `__main__`, not of the missing
extension: a `.py` script run directly is equally uncached, while an extension-less file loaded
through `importlib` *is* cached. That is exactly why the two siblings are cheap on reload.)

The siblings are reached by a **one-statement lazy hop** from `agb`'s dispatch and are never opened
by a hook. `tests/test_mac_split.py` proves this directly — it `chmod 000`s the sibling and runs a
hook, rather than asserting a timing.

`conftest.AGB_PARSE_BUDGET` caps `agb`'s source size. **Raising it is almost always the wrong
answer** — pay for new code by moving other code into a sibling. It has been raised exactly once, on
a measurement; the comment above the constant records the bar.

### The data flow

Agents write to a shared statedir; the Mac pulls over one long-lived `ssh`. Read
[`docs/design.md`](docs/design.md) before changing any of this — it is the authority and is
reconciled against the implementation.

- `sessions/<host>/<key>.state` — five bare lines; **its mtime is the beat**. Written *in place*
  because a rename would make the mtime uncontrollable.
- `sessions/<host>/<key>.json` — the record. Written temp+rename because torn reads matter.
- `gen/<host>.marker` — the live key list **as content**, temp+rename.
- `bridge/<mac-id>.beat` — the Mac's reverse heartbeat, touched every poll.

**The owning host is in the path.** That is what makes every marker rebuild a `readdir` with zero
opens, and makes "only sweep your own entries" structural rather than a runtime check.

## Invariants that are easy to break

These are not style preferences. Each one has a test, and most were re-learned the hard way.

1. **Liveness is proven, never inferred.** No age is ever converted into a status. `liveness()` is
   three-valued (`DEAD`/`ALIVE`/`UNKNOWN`) precisely so "not provably alive" cannot collapse into
   "dead". Collapsing it is the delete-everything bug.
2. **Removal requires positive proof.** A short/malformed/empty read is *no information this poll*,
   never "gone". `ENOENT` on a random key name is the one carve-out — it is a positive server
   answer, and random keys mean no cached negative dentry can manufacture it.
3. **There are exactly two session unlink sites**: `reap_entry` (lexically gated by
   `proof_of_death`, own-host only, enforced by a raise not an `assert` — `python -O` strips
   asserts) and `prune_remove` (gated by a human typing a key). A structural test pins this list.
4. **The hot path touches exactly two files** on a no-change invocation and imports no `json`. The
   `json` import lives inside the transition branch. Config is never read on the hot path.
5. **The hook never writes to stdout** — Claude Code injects `UserPromptSubmit` stdout into the
   prompt — and **always exits 0**, leaving a breadcrumb instead.
6. **Freshness-critical reads are `open()` + `os.fstat(fd)`, never `os.stat`.** On NFS, `stat` is
   served from the attribute cache; close-to-open forces a real `GETATTR` on `open()`. Measured:
   5 × `os.stat` → 1 GETATTR RPC, 5 × `open`+`fstat` → 5–6 GETATTRs.
7. **Never `readdir` another host's session directory.** Directory listings can be served stale for
   up to the attribute-cache lifetime. Cross-host discovery goes through the marker's *content*.
   Own-host `readdir` **is** authoritative and is what marker rebuilds use.
8. **All ages are computed in one clock domain.** `beat` is set with `os.utime(path, None)` so the
   *server* stamps it; the feed derives its `now` by `fstat`ing the beat file it just wrote.
9. **A snapshot may only authorise removals when it is complete.** The feed sets `complete` false
   whenever any read failed, and re-emits a full snapshot once reads recover (`FeedState.owed`).
   The bridge treats only `complete is True` as authority — a truthy string is not.
10. **A `[done]` row can be rebound** when the feed positively re-asserts the key. The removal was
    never proof, so refusing to rebind strands a live agent's row forever.
11. **`agb bridge` has no statedir default.** `agb`'s own `~/.agbridge` is right for a process on
    the farm and wrong for the Mac, which would resolve `~` locally and ship a path meaning
    something else. There is no value that side can invent, so it asks. This shipped broken once.
12. **`agtermctl session split` is used as `on`, never `toggle`**, and the split must exist before
    anything is typed into it (`--pane right` errors otherwise, and `--select` is main-pane only).
    Both are `--help`-verified, recorded in `docs/agtermctl.md`, and mutation-tested.

## Testing conventions

**Structural guards.** Many tests parse the source with `ast` and assert properties like "`json` is
never imported at module top level" or "only these functions unlink a session". Use the helpers in
`conftest.py` — `all_trees`, `functions`, `calls`, `toplevel_imports`, `reachable_from`.

⚠️ **Never write a structural guard as a substring grep of the source.** It will silently pass by
matching the explanatory *comment* that describes the prohibition. Four guards shipped green this
way before being caught. For the same reason, a guard that searches for a bare name (`_unlink_quiet`)
misses the qualified form (`agb._unlink_quiet`) that a sibling file must use — `reachable_from`
follows `agb.<name>` edges for exactly this reason.

**Assert non-vacuity.** A reachability guard must assert its walk actually ran (`assert "hook_apply"
in reachable`) before asserting what is absent; otherwise renaming the root makes it pass while
covering nothing. Same for loops: assert the collection is non-empty before asserting over it.

**Never fabricate a pid.** A made-up pid is almost certainly a *dead* pid, which silently converts a
liveness test into something else. Use `conftest.live_agent()` / `dead_agent()` — the latter forks,
exits, reaps, then verifies the pid is actually free.

**Mutation-check new guards.** Break the property, confirm a *named* test fails, restore. Every
review round in this repo's history found guards that passed vacuously; running the suite green
proves the tests ran, not that they hold anything up.

**Always pass `timeout=` to `communicate()`.** `conftest.communicate()` wraps this. Without it a
regression that wedges a subprocess hangs the suite instead of failing it.

## Hard-won environment facts

Non-obvious things that cost real time to discover. Verify before relying on any of them in a new
environment — several are version- or mount-specific.

- **`python3 -X importtime` is a silent no-op on 3.6.8** (exit 0, no output), so importtime-based
  assertions pass *vacuously*. The working import guard is `python3 -S -E -v` grepped on **stderr**
  for `^import 'json'`. Any such guard needs a negative control, or it passes because the harness
  never reached the branch under test.
- **`os.path.isdir` / `os.path.exists` swallow every `stat` errno**, not just `ENOENT` — `ENOTDIR`,
  `EACCES`, `ESTALE`, `EIO` all return `False`. Using one as an error-handling branch reports a
  broken filesystem as "does not exist yet". This shipped once and was caught in review.
- **`os.utime` accepts an `O_RDONLY` fd**, which saves a second `LOOKUP` on the hot path.
- **`/proc/<pid>/exe` returns `…/tmux (deleted)`** after the binary is upgraded under a running
  process. It still passes a naive basename check, then fails to exec. Strip the suffix and require
  `os.access(X_OK)`, with a `$PATH` fallback.
- **tmux `select-pane -t %N` does not switch the session's active *window*.** Two agents in two
  windows of one session both land on whichever window was last active. `select-window -t %N`
  accepts a pane id; `attach-session -t %N` resolves the session owning that pane. (Verified on
  tmux 3.5a.)
- **`$TMUX` is session-level, not pane-level.** Per-pane identity comes from `$TMUX_PANE`.
- **`socket.gethostname()` returns the FQDN** on many clusters. `own_host()` uses `os.uname()[1]`
  and strips the domain — importing `socket` would cost ~4 ms on every hook.
- **NFSv3 `O_APPEND` is not atomic**, which is why breadcrumbs are per-session files bounded by
  truncate-and-restart rather than a shared appended log.
- **In-place `O_TRUNC` opens a zero-length window** that is visible to other hosts, which is why the
  marker — where an empty read would mean "this host has no sessions" — is written temp+rename.

## Conventions

- **`Task N` references** in comments and docs are provenance from the original build plan's
  phases. That plan is not published; the markers are retained because they group related decisions.
  Treat [`docs/design.md`](docs/design.md) as the authority for *why*, not the task numbers.
- **Comments carry the reason, not just the rule.** A withdrawn rule keeps its reasoning on purpose
  — a rule without its reason gets re-litigated. Preserve this when editing.
- Hand-rolled argv parsing is deliberate (`argparse` costs ~10 ms of import on the hot path). The
  nine parsers share a `*_FLAGS` / `*_VALUE_ARGS` table convention; `--opt=` with an empty inline
  value is a missing-value error in all of them.
- Function-local imports (`json`, `select`, `subprocess`, `re`, `fcntl`) are deliberate. `agb`'s
  module-level imports are pinned to exactly `{errno, os, sys, time}` by a test.

## Known gaps

- **Verified against a live agterm.** `session new` returning the row id on stdout, `session
  split`, `session type`, `session close` and `--blink` are all **CONFIRMED** — see
  [`docs/agtermctl.md`](docs/agtermctl.md), which tags every clause. What is left: repeated
  `rename` (**ASSUMED**, with a fallback recorded — but the bridge does it on every update, so a
  failure would be constant rather than subtle), whether `blink` is sticky or one-shot (it is only
  ever sent on a transition, correct either way), and the spelling of `--auto-reset`, which
  agbridge never emits.
- **Long-running behaviour is still unexercised** — reconnects, the watchdog firing, `prune`
  against a genuinely dead host. This is why the version is 0.2.0 and not 1.0.0.
- **Two doors to `agtermctl`, deliberately.** `agb_mac._run_command` is the renderer's single door;
  `agb_ops.open_split` is a second one, because `agb pane` runs on the Mac but lives in `agb_ops`,
  which never loads `agb_mac`. Both obey the same rule: a failure is written out and returned,
  never raised.
- `agb <cmd> --help` is not implemented — it would need a `--help` arm in nine hand-rolled parsers
  across three files, against the byte cap. [`docs/commands.md`](docs/commands.md) is the reference.
- The nine `parse_*_args` functions share scaffolding that could collapse into one helper. It was
  deliberately not done: the helper would have to live in `agb`, the byte-capped file, so that two
  *lazily loaded* siblings could share it — inverting the constraint the cap exists to enforce.
