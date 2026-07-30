# agbridge

At-a-glance status for [Claude Code](https://claude.com/claude-code) agents running on **remote
Linux hosts**, surfaced in [agterm](https://github.com/umputun/agterm)'s sidebar on your Mac.

Agents write their state to a shared directory. The Mac *pulls* that state over one long-lived
`ssh`. No reverse tunnel, no sockets, no daemon on the cluster.

```
  cluster host (box #2)            cluster host (machine #3)
     agb hook                          agb hook
          │ write                           │ write
          ▼                                 ▼
   ┌──────────────────────────────────────────────┐
   │  <statedir>/  — one shared directory         │
   │    sessions/<host>/<key>.json   the record   │
   │    sessions/<host>/<key>.state  MTIME = BEAT │
   │    gen/<host>.marker            live key list│
   │    bridge/<mac-id>.beat         reverse ping │
   └──────────────────────────────────────────────┘
          ▲ agb feed: reads the marker by name, opens each key by
          │ name, never lists another host's directory
     ssh (ServerAlive + an application-level watchdog)
          │
   ┌──────┴────────────────────────────────┐
   │  Mac: agb bridge (launchd)            │
   │    key → agterm row, bijective        │
   │    agtermctl session new / status     │
   │    row command = agb pane <key> …     │
   └───────────────────────────────────────┘
```

## Why not a reverse tunnel

The obvious approach — forward agterm's unix socket to the remote host — is what
[`agr`](https://github.com/k0nsta/agterm-remote) does, and it fails **silently** in seven distinct
ways. agbridge inverts the transport, which addresses all seven structurally rather than by
adding checks:

| # | Failure mode | Addressed by |
|---|---|---|
| 1 | stacked silent no-op paths | breadcrumbs + reverse heartbeat + a probing `doctor` |
| 2 | `[ -S $sock ]` is true for a dead socket | no sockets at all |
| 3 | target mappings never GC'd → row collision | Mac-owned bijection, minted keys, swept index |
| 4 | tunnel lifetime ≠ session lifetime | no tunnel |
| 5 | push loses state that changed while offline | level state + snapshot-on-connect |
| 6 | `AF_UNIX` does not work across NFS | no sockets at all |
| 7 | agents launched outside the wrapper never bind | rows are auto-created from state |

## Requirements

- **Cluster side:** Python **3.6.8+**, stdlib only. No packages, no virtualenv, nothing to build.
- **Mac side:** Python 3, `agterm` with `agtermctl` on the launchd job's `PATH`, `ssh` to the
  cluster host that will run the feed.
- **A shared directory** that every agent host and the feed resolve to the same files. A network
  home satisfies this on most clusters; see [Configuration](#configuration) if yours differs.
- Optional: `tmux`, for the status-line segment and for click-to-attach.

## Install

**New here? Follow [`docs/cookbook.md`](docs/cookbook.md)** — nothing to working sidebar in about
ten minutes. The rest of this section is the reference.

`agbridge` is three files that must live in the same directory. `install.sh` has two roles.

**1. On the Mac** — copies the three files (plus the `agb-refresh` helper), mints a `mac_id`,
writes the config, renders and loads the launchd job:

```sh
sh install.sh mac \
    --feed-host box2.example.com \
    --agb-remote-path /opt/agbridge/agb
```

It prints the minted `mac_id` and the exact farm-side command to run next. Add `--farm
<ssh-target>` to run that step over `ssh` immediately instead of printing it.

**2. On every cluster host that runs agents** — writes the config and merges the four hooks into
`~/.claude/settings.json`:

```sh
sh install.sh farm --mac-id <the id the mac role printed>
```

This side is **not** a no-op even when the binary is on a shared mount: the hooks and the
config are per-host.

> Before either installer rewrites a file it copies the previous contents to `<path>.agb.bak`,
> preserving the mode. A file it cannot parse is never rewritten at all.

Then check it:

```sh
agb doctor          # probes, not existence checks — see below
```

## Commands

| Command | Runs on | Purpose |
|---|---|---|
| `agb hook <state>` | cluster | the hot path — invoked by Claude Code on every tool call |
| `agb feed <mac-id>` | cluster | long-lived; streams NDJSON to the Mac over the bridge's ssh |
| `agb bridge` | Mac | long-lived launchd job; owns the key → row bijection |
| `agb close-done` | Mac | reclaim rows whose agent has finished |
| `agb forget-rows` | Mac | drop `key → row` bindings so rows are re-created |
| `agb pane <key> …` | Mac | what a row's command runs: print identity, attach on demand |
| `agb rename <key> <label>` | cluster | set a row's label |
| `agb doctor` | cluster | diagnostics that **probe** rather than check existence |
| `agb prune` | cluster | the only destructive command; per-entry confirmation |
| `agb status-line` | cluster | tmux segment: `bridge:UP 2s` / `bridge:DOWN 14m` |
| `agb install-hooks` | cluster | merge the four Claude Code hooks idempotently |
| `agb install-config` | both | write/merge `~/.config/agbridge/config` |
| `agb version` | both | load-bearing: both installers probe with it |
| `agb-claude [name]` | cluster | helper: starts Claude Code in a **named tmux session**, so its row is attachable |
| `agb-refresh` | Mac | helper: stop bridge → forget bindings → start, after agterm loses its rows |

Full flag reference: [`docs/commands.md`](docs/commands.md).

## Configuration

`~/.config/agbridge/config`, `key = value`, read lazily and **never on the hot path**.

| Key | Used by | Meaning |
|---|---|---|
| `statedir` | all | the shared directory. Overridden by `$AGB_STATEDIR` |
| `mac_id` | `bridge`, `status-line` | names `bridge/<mac-id>.beat`; minted at install |
| `feed_host` | `bridge` | ssh target that runs the feed |
| `agb_remote_path` | `bridge`, `prune --via-ssh` | absolute path of `agb` on the cluster |
| `remote_python` | `bridge`, `prune --via-ssh` | absolute cluster-side interpreter |
| `jump_host` | `pane`, `prune --via-ssh` | ssh jump host for hosts you cannot reach directly |
| `host_<name> = <ssh-target>` | `pane`, `prune` | a record's `host` is a hostname, not an ssh alias |

**Environment overrides:**

| Variable | Effect |
|---|---|
| `AGB_STATEDIR` | overrides `statedir`. Baked into the hook command at install time, which is what lets the hot path skip reading the config |
| `AGB_HOST` | overrides the short hostname used for `sessions/<host>/` and for the sweep's own-host check. **Test seam** — setting it in a live shell orphans entries |
| `AGB_AGENT_PID` | overrides agent-pid resolution; `-`/`none`/`0` mean "no pid". **Test seam** |

### Where the statedir lives

Default `~/.agbridge`. The requirement is a directory every agent host and the feed resolve to the
**same underlying files**. A network home satisfies this on most clusters; where `$HOME` is
host-local it does not, and the two halves will silently watch different directories. Set
`$AGB_STATEDIR` or the `statedir` key, and confirm with `agb doctor` — it prints the resolved path,
its ownership and mode, and the mount options behind it.

⚠️ **Do not point it at a scratch volume without checking the purge policy.** Volumes that reap
files by age would silently delete the state of a long-idle session — manufacturing exactly the
removals the design refuses to infer.

## Design principles

The interesting constraints, all of which the code and tests enforce:

- **Liveness is proven, never inferred.** No age is ever converted into a status. Death is
  established only by `kill(pid,0)` plus a `starttime` match. A `blocked` agent that has been
  waiting on you for 30 minutes still renders as `blocked`, because "quiet" and "dead" are not the
  same claim.
- **Removal requires positive proof.** A short, malformed or empty read means *no information this
  poll* — never "gone". `ENOENT` on a random key name is the one exception, and it is a positive
  answer from the server rather than an absence of one.
- **The hot path is budgeted.** `agb hook` runs on every Claude Code tool call, over a network
  filesystem. A no-change invocation touches exactly **two** files, imports no `json`, and uses no
  `argparse`. The tool is split into three files so the hook never parses the Mac-side or operator
  code — an extension-less script gets no cached bytecode, so every byte is re-parsed every time.
  A test enforces the size ceiling.
- **Nothing fails silently.** Every failure leaves a breadcrumb; `doctor` probes rather than
  checking existence (it really writes a file, renames it, and reads it back); the hook exits 0 on
  every path and never writes to stdout, because Claude Code injects hook stdout into the prompt.
- **One row, two panes.** Clicking a row runs `agb pane`, which prints the agent's identity and
  offers `[enter] attach   [s] shell   [q] quit`. Enter joins the agent's own tmux pane; `s` opens
  agterm's split beside it with a shell on the same host, in the agent's directory. Detaching
  returns to the prompt rather than closing the row.
- **A dead row must not look like a live one.** `agterm`'s `idle` renders as *no glyph*, so a
  removed row would be pixel-identical to a live idle agent. Removed rows are marked `[done]` and
  stale ones `[?]` in the title.

## Documentation

| File | Contents |
|---|---|
| [`docs/design.md`](docs/design.md) | the full design: state model, liveness rules, wire protocol, failure modes |
| [`docs/commands.md`](docs/commands.md) | every command, flag and default |
| [`docs/tmux.md`](docs/tmux.md) | the status-line segment and its achievable resolution |
| [`docs/agtermctl.md`](docs/agtermctl.md) | the `agtermctl` contract the bridge codes against |
| [`docs/cookbook.md`](docs/cookbook.md) | **start here** — step-by-step onboarding and troubleshooting |
| [`CLAUDE.md`](CLAUDE.md) | architecture and invariants, for working on this codebase |

## Development

```sh
python3 -m pytest tests/ -q          # 1271 tests, no network, no second host, no Mac required
python3 -m pytest tests/test_hook.py -q
python3 -m pytest tests/test_hook.py::test_beat_refresh_is_throttled -q
sh -n install.sh                     # shell syntax check
```

The suite is stdlib + `pytest` only and runs entirely on one Linux host — a second machine is
simulated by writing entries whose `host` differs from the local one. See [`CLAUDE.md`](CLAUDE.md)
before changing anything on the hot path or in the removal logic.

## Status

**Running end to end against a live agterm**, across two Linux hosts and a Mac. 1287 tests, no
network or second machine required to run them.

Verified in real use, not just in tests:

| | |
|---|---|
| hooks firing on multiple hosts | ✅ |
| one shared statedir, cross-host discovery | ✅ |
| `ssh` feed → bridge → reverse heartbeat | ✅ |
| rows created and updated in agterm | ✅ |
| clicking a row runs `agb pane` with the right identity | ✅ |
| `agtermctl session new` returns the row id on stdout | ✅ — the largest assumption in the design; it held |
| `session split` / `session type` | ✅ `--help`-verified before being coded against |

Not yet exercised, and worth knowing before you rely on them:

- **`agb close-done`** — `session close` is still `ASSUMED`. If it does not exist, `close-done`
  degrades to printing the rows you should close by hand.
- **`[s] shell`** — the split pane was built after the last live run and has only been tested
  against a stub.
- **`--blink` / `--auto-reset` spellings**, and whether `session rename` may be called repeatedly on
  an existing row.
- **Long-running behaviour** — reconnects, watchdog firing, `prune` against a genuinely dead host.

[`docs/agtermctl.md`](docs/agtermctl.md) tags every clause `CONFIRMED` or `ASSUMED` and records a
fallback for each assumption. If your `agtermctl` differs, that file is the one to correct.

## License

[MIT](LICENSE).
