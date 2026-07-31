# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```sh
python3 -m pytest tests/ -q                                    # full suite (1557 tests, ~38 s)
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

### Everything the bridge tells agterm

Every one of these is a subprocess call through `_run_command`, and every one is best-effort — a
failure is written to the log and returned, never raised. `docs/agtermctl.md` tags each clause with
its evidence class; nothing here may be added from a guess.

| When | Call |
|---|---|
| a key with no row | `session new --name … --cwd … --command … --no-select [--workspace-name …]` |
| identity or marker changed | `session rename <title> --target <row>` |
| status changed, or every 30 s | `session status <state> --target <row> [--blink]` |
| transition **into** `blocked` | `notify <body> --title … --target <row>` |
| `agb close-done` | `session close --target <row>` |

Two of these are gated on a **transition**, not on the level state, and for the same reason: they
are events, not renderings. `--blink` fires only on a real move into `active`; the banner only on a
real move into `blocked`. Both would otherwise repeat on every snapshot and on the 30 s re-assert.

The banner is the only one with a config gate — `notify_on_blocked`, **on by default**, read through
`agb_mac.config_flag` (which lives there and not in `agb`: the hot path never reads a flag, and
`agb` has no bytes to spare). It spells out what counts as false, because `"0"` is truthy in Python
and a key that silently means its opposite is worse than one that does not exist. Whether the Dock
icon *bounces* is agterm's own setting, not ours — which events are worth announcing is this tool's
business; how loudly the machine interrupts you is the machine's.

`agb pane` adds two more calls from `agb_ops` — `session split` / `session scratch` plus
`session type` — which is why there are **three** doors to `agtermctl` rather than one.

### The status vocabulary is closed

`active | blocked | completed | idle`, and **there is no `unknown`**. Agents report only the first
three (hooks: `UserPromptSubmit`/`PostToolUse` → `active`, `Notification` `permission_prompt` →
`blocked`, `Stop` → `completed`). `idle` is emitted by the *bridge alone*, for two renderings that
must not be confusable with a live agent, which is why each also carries a title prefix:

| Bridge emits `idle` | Title prefix |
|---|---|
| feed went quiet / connection lost | `[?] ` |
| agent removed (finished, reaped, pruned) | `[done] ` |

`idle` renders as **no glyph**, so a row showing nothing is either one of those two cases or a row
that has not been painted yet. "No glyph" is never evidence about the agent.

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
12. **Everything the Mac side owns derives from ONE path: the config.** `rows`, `placements` and the
    `host_<name>` table are `dirname(<config>)/…` or keys inside it, which is what makes a second
    instance (`agb bridge --config <path>`, `install.sh mac --instance <name>`) a directory rather
    than a concept. Anything new that a bridge persists on the Mac belongs there too. ⚠️ The row's
    own command must carry `--config` for a non-default instance, or `agb pane` resolves `--host`
    through the **default** config and click-to-attach reaches the wrong machine with every test
    green; `pane_argv` emits it only when `normpath` says it differs, so default installs are
    byte-identical. ⚠️ **Corollary, and it has been got wrong twice: "the same instance" is a
    comparison of the resolved DIRECTORY, nothing more.** The basename never reaches `rows` or
    `placements`, so an equivalence test that keeps it — or that compares the path as text — is
    narrower than the map it guards, and the failure always has the same shape: the right map under
    the *wrong* label, i.e. bounce instance A while forgetting B's bindings under B's live bridge.
    `agb-refresh`'s `same_map` is the one place that comparison lives, over `config_map_dir`, which
    is the canonicaliser it is spelled in terms of. ⚠️ **But *matching* the
    map and *choosing* between the matches are two questions**, and running them together is how the
    fourth instance of that failure arrived: the basename and "does this plist declare a config or
    merely imply one" break the tie among candidates that have already matched, so the job naming
    this exact file wins over one that only shares the directory instead of the winner falling out of
    `*.plist`'s collating order. Ranking is not matching, and it narrows nothing.
    `docs/design.md` §5, *One Mac, several instances*, is the authority.
13. **`agtermctl session split` and `session scratch` are used as `on`, never `toggle`** — either
    key can be pressed twice, and a toggle would close the pane the second time — and the pane must
    exist before anything is typed into it (`--pane right` errors otherwise, and `--select` is
    main-pane only; the same claim for `--pane scratch` is **ASSUMED** and unobservable, since
    `scratch on` always goes first). Recorded in `docs/agtermctl.md` and mutation-tested.
    `session scratch --command` is **deliberately unused**: it respawns an already-open scratch, so
    a second `[d]` would destroy a shell in use.
14. **Two cross-file agreements have no single source of truth, and both fail silently.** `agb` is
    Python under a byte cap; `install.sh` and `agb-refresh` are POSIX sh; none of the three can
    import the others, so each spells the shared value itself.
    - **The default config path is spelled three times** — `agb.config_path()`, `install.sh`'s
      `DEFAULT_CONFIG_DIR`/`DEFAULT_CONFIG`, `agb-refresh`'s copy of the same pair. `pane_argv`
      emits `--config` only when the path *differs* from `agb.config_path()`, so a disagreement
      between the first two makes **every default install re-mint every row**, reporting success.
    - **`instance_ok()` exists in both shell scripts** and must accept exactly the same names. A
      name `agb-refresh` accepts and the installer refuses points at a plist that was never
      rendered; the reverse writes four things where they were not meant to go. The *messages*
      differ on purpose (one writes those four things, the other goes looking for three).

    Both are pinned by `tests/test_install_pkg.py` — the path agreement compares the resolved
    strings, the validator agreement compares the `case` **patterns**, not the bodies.

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
- **`RowRenderer.applied`/`.titles` are an optimisation, never a source of truth.** They record what
  the bridge last *sent*, which is not what agterm is *showing* — agterm resets a row's status when
  the row's command starts, so attaching clears the glyph with no error anywhere. `_reassert` re-sends
  every status every `REASSERT_INTERVAL` (30 s) so no divergence can be permanent. If you add another
  suppress-if-unchanged path, give it the same escape hatch.
- ⚠️ **`applied` is also the wrong gate for "did the AGENT change".** It is the right gate for
  `--blink`, which is about what was painted — but `_render_stale` writes `idle` into it on *any*
  disconnect, including a routine 10 s quiet spell. Anything that should fire once per real agent
  transition needs its own memory, or a network hiccup replays it. `RowRenderer.blocked` (the set
  behind the `blocked` banner) is the worked example; substituting `applied` there fails five named
  tests. This distinction has now caused two separate bugs — assume it will cause a third.

## Where the project is (2026-07-31)

`VERSION` is **0.5.0**, unreleased: `CHANGELOG.md`'s `Unreleased` section holds it and the tag has
not been cut. The feature is **instances** — a machine that shares no disk with the first is now an
install (`install.sh mac --instance <name> --statedir …`), one independent bridge per machine, all
rendering into the same sidebar. One flag, `--config`, carries it everywhere: bridge, `close-done`,
`forget-rows`, the row's own `agb pane` command, the launchd plist and `agb-refresh`. Invariant 12
and `docs/design.md` §5 hold the reasoning; the seven limitations are written out there, the first of
which — a helper without `--instance` succeeding on the wrong instance — is mitigated only by the
banner those commands now print on every run.

Released **0.4.0** — notifications: a banner when an agent blocks, one when a new agent appears, and
the unseen badge cleared when a block is answered. `agb` is at 102,429 of its 102,500-byte parse
budget — **71 bytes of headroom**, which is the single hardest constraint on any change to the hot
path (0.5.0 added nothing to it: the version string is the same length, and every new line landed in
`agb_mac`/`agb_ops`). 1557 tests.

Verified against a live agterm, in this order of confidence: row creation and the returned id,
`rename`, `status`, `--blink`, `close`, `split`+`type`, click-to-attach reaching the right host and
pane, `[s] shell` opening a split, `notify --target` producing a banner, and **all three
notification paths end to end** — a blocked agent, a new agent, and the badge clearing when the
block was answered.

⚠️ **Two of those three needed a fix *after* the live test, in ways 1400 tests could not catch.**
The badge test was run against the row that was selected, and agterm never badges the session you
are looking at — so a working feature read as broken. The new-row quiet window was armed at
construction rather than at the first op batch, and was long enough to swallow a real agent started
9 s after a reinstall. Neither was reachable from the test suite. **Live-test anything that talks to
agterm, and think about which row you test it on.**

**Still unverified, and the honest list:**

- **`session scratch`'s behaviour** — the `[d]` drawer added in 0.3.0. Its spelling is
  `--help`-verified and its call path is mutation-tested against the `[s]` split it copies, but
  nobody has watched a drawer open, be hidden, and come back with **the same shell still alive**.
  That claim is the entire reason `scratch` was chosen over `overlay`, so it is the one worth
  checking first. `README.md`'s verification table carries unchecked rows for it.
- **A second instance, live.** The whole of 0.5.0 is covered by tests only — nobody has yet run two
  bridges on one Mac. The check that matters is **clicking a row from each instance and landing on
  the right machine**, because that path (`pane_argv` → the row's command → `agb pane`'s own config
  read) is exactly the one whose failure every unit test passes through. Worth trying the wrong
  command on purpose too: a plain `agb-refresh` while both are up, to see whether the banner actually
  tells you which one it acted on.
- **Long-running behaviour** — reconnects, the watchdog firing, `prune` against a genuinely dead
  host. This is why the version is 0.x.

⚠️ **agterm closes a session when its command exits** (confirmed live 2026-07-31, in isolation).
`q`/`quit`/`exit` at the `agb pane` prompt therefore **destroys the row** of a live agent — not a
dismissal, an accident — and leaves a bound entry naming a row that no longer exists, which is
almost certainly the source of the `no such session` spam in the bridge log. Full write-up in
[`docs/agtermctl.md`](docs/agtermctl.md) → "agterm closes a session when its command exits". Read it
before reasoning about row lifetime; it is not in agterm's own reference and nothing here assumed it.

⚠️ **Never design against `agterm.com/commands` without running the command on the Mac first.**
It cost two reversed decisions in one day. First it documented `events` and `pick`, which the
installed build did not have (they arrive in agterm **v0.16.0**, along with `session restore`).
After upgrading they existed — and the page was *still* wrong about behaviour: it says `events`
returns a batch and exits, and it **follows**. A full design had been built on that sentence, and an
earlier, correct objection to it had been marked ✅ wrong on the page's authority. Findings in
[`docs/agtermctl.md`](docs/agtermctl.md) → "What `agtermctl events` actually does"; the shelved
design is in `docs/plans/blocked/`. The "capture the real output first" task is what caught both,
and is why such a task belongs in any plan that touches agterm.

**agbridge uses 8 of agterm's ~70 documented subcommands.** The unused surface was surveyed on 2026-07-31 and
the useful part is recorded in [`docs/agtermctl.md`](docs/agtermctl.md) → "What agbridge does not use
yet", with what each would buy. The standout is **`events`**, agterm's control-event ring: the
bridge is write-only today, which is why nothing notices when a row is closed by hand or agterm
forgets its sessions, and why `agb-refresh` exists. Read that section before adding any new call —
it may already say why a thing was passed over.

**Considered and not built:** bringing agterm to the front on `blocked`. `agtermctl window select`
is recorded verbatim in `docs/agtermctl.md` with its trap — given no id it raises whichever window
is *already active*, so targeting the blocked row's window needs the id from `tree --json`, which
`tree_workspaces` already parses. Deferred deliberately: a bouncing Dock says "come here when you
are ready" and a window jumping in front says "stop what you are doing", and the banner may well
turn out to be enough. If it is built, it wants a config gate and an **off** default — focus-stealing
is a thing to choose, not to inherit.

## Changelog and releases

**Every user-visible change gets a `CHANGELOG.md` entry in the same commit as the code.** Not at
release time — by then the reason is gone, and the reason is the whole point of the file.

Entries accumulate under `## Unreleased` until a release renames that heading to
`## <version> — <date>`. Sub-headings are `### Added` / `### Fixed`; 0.3.0 also uses
`### Decisions recorded, because they will look like mistakes later` and `### Not verified`, both
worth copying when they apply.

**The house style is that an entry says *why*.** A rule without its reason gets re-litigated, and in
this project most reasons are a failure somebody actually hit — so entries name the symptom, not
just the fix ("a row's status glyph disappeared on first attach and never came back", not "fixed
status handling"). Where a decision will look wrong to a future reader, say what was rejected and
why: `--command` and the duplicated pane openers are both in there for that reason. And **carry the
caveats forward** rather than summarising them away — that the log cap truncates instead of
rotating, that `session scratch` is unverified.

Releasing, in order:

1. `agb:24` `VERSION` — the **only** place it lives, and load-bearing: both installers probe
   `agb <version>` and refuse to write anything without the right answer back. New key or feature →
   minor; fixes only → patch.
2. `wc -c agb` against `AGB_PARSE_BUDGET` in `tests/conftest.py`. A same-length version string
   leaves it unchanged, which is the expected result for a release that touches no other line of
   `agb` — if the number moves, something else got in.
3. Rename `## Unreleased` to the version and date.
4. `git commit -m "release: <version>"`, then an **annotated** tag `v<version>` whose message is a
   short prose summary (not a commit list — that is what the changelog is for).
5. `git push origin HEAD && git push origin v<version>`.
6. GitHub Release from the tag, title `agbridge <version>`. The body is a **short summary plus a
   link to `CHANGELOG.md`** — never a copy of it. Two places saying the same thing is two places to
   keep in step, and the one nobody can `git log` is the one that goes stale. Link the **tag**, not
   `main`, so it keeps showing the changelog as of that release:
   `https://github.com/zahark/agbridge/blob/v<version>/CHANGELOG.md#<anchor>` — the anchor is the
   heading lowercased with dots and the em dash dropped, e.g. `## 0.4.0 — 2026-07-31` →
   `#040--2026-07-31`.

⚠️ **A tag is only as good as what it points at.** Fixes landing after the tag are not in the
release; move the tag with `git tag -f -a` and `git push --force origin v<version>` *before* cutting
the GitHub Release, or say plainly that they are not included.

⚠️ **A release is not installed by pulling.** The Mac loads `agb_mac`/`agb_ops` from
`~/.local/lib/agbridge/`, not from the checkout, so `sh install.sh mac …` is required — and existing
rows keep the `agb pane` code they were *created* with until `agb-refresh` re-mints them. Say this in
the release notes every time; it is the single most common way a fix appears not to work.

## Known gaps

- **What is and is not verified against a live agterm** is above, under "Where the project is".
  [`docs/agtermctl.md`](docs/agtermctl.md) tags every individual clause. Three clauses stay
  **ASSUMED** and are fine that way: repeated `rename` (the bridge does it on every update, so a
  failure would be constant rather than subtle), whether `blink` is sticky or one-shot (it is only
  ever sent on a transition, so it is correct under either reading), and the spelling of
  `--auto-reset`, which agbridge never emits.
- **Three doors to `agtermctl`, deliberately.** `agb_mac._run_command` is the renderer's single
  door; `agb_ops.open_split` and `agb_ops.open_drawer` are the other two, because `agb pane` runs
  on the Mac but lives in `agb_ops`, which never loads `agb_mac`. All obey the same rule: a failure
  is written out and returned, never raised.
- **`open_split` and `open_drawer` are duplicated on purpose — merging them is not a tidy-up.**
  They differ in two constants and one noun, which normally argues for a parameter. The reason not
  to: they are expected to **diverge**. `session scratch` takes a `--command` that `session split`
  has no equivalent for, so the drawer may yet become a single call while the split cannot. The
  same reasoning is recorded at the call site and in `tests/test_identity.py`'s enumeration.
- `agb <cmd> --help` is not implemented — it would need a `--help` arm in nine hand-rolled parsers
  across three files, against the byte cap. [`docs/commands.md`](docs/commands.md) is the reference.
- The nine `parse_*_args` functions share scaffolding that could collapse into one helper. It was
  deliberately not done: the helper would have to live in `agb`, the byte-capped file, so that two
  *lazily loaded* siblings could share it — inverting the constraint the cap exists to enforce.
