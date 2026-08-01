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

One statedir, one feed, one bridge — **per instance.** A machine that shares no disk with this one
gets its own copy of the whole picture above, driven by a second launchd job, rendering into the
same agterm sidebar. See *A second machine* below.


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
- **A shared directory** that every agent host and the feed resolve to the same files — **per
  instance**, not per Mac. A network home satisfies this on most clusters; see
  [Configuration](#configuration) if yours differs. A machine that shares no disk with the others
  does not have to be left out: give it its own instance
  (`install.sh mac --instance <name> --statedir <path>`, below) and its rows join the same sidebar.
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

**3. A machine that shares no disk with the first** — its own statedir means its own feed and its
own bridge, so it is a second **instance** on the same Mac:

```sh
sh install.sh mac --instance hostb \
    --feed-host hostb-alias --agb-remote-path /opt/agbridge/agb \
    --statedir /home/you/.agbridge
```

`--instance` is sugar over `--config`, `--label` and `--log-dir`: the config moves to
`~/.config/agbridge/hostb/config`, and the rows map, the remembered workspaces and the `host_<name>`
table move with it, because all three live beside the config. `--dest` stays shared — one code
install, N configurations. Repair that one with `agb-refresh --instance hostb`; run the helpers
**without** `--instance` and they act on the default instance and say so, which is the first of the
seven limitations in [`docs/design.md`](docs/design.md) §5.

Both roles write an `agb` wrapper into `~/.local/bin` (`--bin-dir` to change it, `--no-wrapper` to
skip). It exists because `agb` is deliberately **not executable and has no shebang** — a hook must
pass `-S -E`, and neither a shebang nor `env` can — so `agb <cmd>` needs something to supply them.
The installer warns if the directory is not on your `$PATH`.

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
| `agb list` | cluster | every session the statedir knows about, with addressable keys |
| `agb rename <key> <label>` | cluster | set a row's label |
| `agb doctor` | cluster | diagnostics that **probe** rather than check existence |
| `agb prune` | cluster | the only destructive command; per-entry confirmation |
| `agb status-line` | cluster | tmux segment: `bridge:UP 2s` / `bridge:DOWN 14m` |
| `agb install-hooks` | cluster | merge the four Claude Code hooks idempotently |
| `agb install-config` | both | write/merge `~/.config/agbridge/config` |
| `agb version` | both | load-bearing: both installers probe with it |
| `agb-claude [name]` | cluster | helper: starts Claude Code in a **named tmux session**, so its row is attachable. `-d` starts it in the background with the row already showing |
| `agb-refresh` | Mac | helper: stop bridge → forget bindings → start, after agterm loses its rows — a close, a reset, a reinstall, **a Mac reboot**, or an upgrade of the Mac's files |

Full flag reference: [`docs/commands.md`](docs/commands.md).

## Configuration

`~/.config/agbridge/config`, `key = value`, read lazily and **never on the hot path**. On the Mac
that is the *default* path: `--config <path>` (or `--instance <name>`, which spells
`~/.config/agbridge/<name>/config`) points the bridge at another one, and the `rows` map, the
`placements` file and this table's `host_<name>` keys all live beside whichever config is in use.

| Key | Used by | Meaning |
|---|---|---|
| `statedir` | all | the shared directory. Overridden by `$AGB_STATEDIR`. One per instance — two instances that named the same statedir would be two bridges rendering the same agents twice |
| `mac_id` | `bridge`, `status-line` | names `bridge/<mac-id>.beat`; minted at install |
| `feed_host` | `bridge` | ssh target that runs the feed |
| `agb_remote_path` | `bridge`, `prune --via-ssh` | absolute path of `agb` on the cluster |
| `remote_python` | `bridge`, `prune --via-ssh` | absolute cluster-side interpreter |
| `jump_host` | `pane`, `prune --via-ssh` | ssh jump host for hosts you cannot reach directly |
| `workspace` | `bridge` | agterm workspace for new rows, by **name**; created if absent. Without it they land in whichever workspace is current when the row is made. A row you have moved keeps its own place — see below |
| `notify_on_blocked` | `bridge` | desktop banner when an agent starts waiting for you. **On by default**; `0`, `no`, `off` or `false` disables it. Whether the Dock icon bounces is agterm's own setting |
| `notify_on_new_row` | `bridge` | desktop banner when an agent appears that had no row — naming its label, host and directory. **On by default**. Rows minted in a burst (a bridge start, a reconnect, `agb-refresh`) are silent for 3 seconds, or a nine-row refresh would be nine banners |
| `notify_on_completed_after` | `bridge` | desktop banner when an agent **finishes a turn that ran at least this many seconds**. **On by default at `300`** (5 minutes); `0`, `off`, `no`, `false` or a negative value disables it. The number is the switch, so there is no separate on/off key. A threshold rather than a plain banner because `completed` fires *once per turn* — ungated it would announce every "yes" you type, three seconds later |
| `host_<name> = <ssh-target>` | `pane`, `prune` | a record's `host` is a hostname, not an ssh alias |

**Environment overrides:**

| Variable | Effect |
|---|---|
| `AGB_STATEDIR` | overrides `statedir`. Baked into the hook command at install time, which is what lets the hot path skip reading the config |
| `AGB_HOST` | overrides the short hostname used for `sessions/<host>/` and for the sweep's own-host check. **Test seam** — setting it in a live shell orphans entries |
| `AGB_AGENT_PID` | overrides agent-pid resolution; `-`/`none`/`0` mean "no pid". **Test seam** |

### Where rows live

Drag a row to another workspace and it stays there — the bridge only sets a workspace when a row is
*created*, and never moves one afterwards. `agb-refresh` genuinely destroys and recreates rows, so
before closing them it records where each one was (`~/.config/agbridge/placements`, beside the
config in use) and puts them back. The `workspace` config key is the fallback for rows that have no
remembered place — and with several instances it is also the practical way to tell them apart, since
nothing in a row says which machine it came from: set `workspace = <cluster>` per instance and the
sidebar groups by machine.

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
- **One row, three panes.** Clicking a row runs `agb pane`, which prints the agent's identity and
  offers `[enter] attach   [s] split   [d] drawer   [q] quit`. Enter joins the agent's own tmux
  pane; `s` opens agterm's split *beside* it with a shell on the same host, in the agent's
  directory; `d` puts that shell in the scratch drawer *over* it instead, which costs no width and
  stays alive while hidden. Neither replaces the other — the split shows you the agent and the
  shell at once, the drawer gives the agent its full width back. Detaching returns to the prompt
  rather than closing the row.
- **A dead row must not look like a live one.** `agterm`'s `idle` renders as *no glyph*, so a
  removed row would be pixel-identical to a live idle agent. Removed rows are marked `[done]` and
  stale ones `[?]` in the title.
- **Every row's status is re-sent every 30 s**, changed or not. The bridge remembers what it last
  painted and skips redundant repaints, but that memory describes what it *sent*, not what agterm is
  showing — and agterm changes rows for its own reasons. It resets a session's status when the
  session's command starts, so attaching to a row used to clear its glyph until the agent's state
  next changed, which for an idle agent can be hours. The re-assert never blinks, never runs while
  the feed is stale, and costs one call per row per interval.

## Ask Claude Code how to use it

The repo ships a [Claude Code skill](.claude/skills/agbridge/SKILL.md) covering the recipes people
actually need — adding a cluster host, adding a machine that shares no disk, running a second Mac,
removing a host, rows that are missing or stale or duplicated, notifications, workspaces — plus
pointers into the docs below.

It works automatically when Claude Code is run **inside this checkout**. To have it everywhere,
symlink it once:

```sh
mkdir -p ~/.claude/skills
ln -sfn "$PWD/.claude/skills/agbridge" ~/.claude/skills/agbridge
```

A symlink rather than a copy, so `git pull` updates it. Then ask normally — *"how do I add another
cluster host to agbridge?"* — or invoke it directly with `/agbridge`.

## Documentation

| File | Contents |
|---|---|
| [`docs/design.md`](docs/design.md) | the full design: state model, liveness rules, wire protocol, failure modes |
| [`docs/commands.md`](docs/commands.md) | every command, flag and default |
| [`docs/tmux.md`](docs/tmux.md) | the status-line segment and its achievable resolution |
| [`docs/agtermctl.md`](docs/agtermctl.md) | the `agtermctl` contract the bridge codes against |
| [`docs/cookbook.md`](docs/cookbook.md) | **start here** — step-by-step onboarding and troubleshooting |
| [`CHANGELOG.md`](CHANGELOG.md) | what changed in each release, and why |
| [`CLAUDE.md`](CLAUDE.md) | architecture and invariants, for working on this codebase |

## Development

```sh
python3 -m pytest tests/ -q          # 1777 tests, no network, no second host, no Mac required
python3 -m pytest tests/test_hook.py -q
python3 -m pytest tests/test_hook.py::test_beat_refresh_is_throttled -q
sh -n install.sh                     # shell syntax check
```

The suite is stdlib + `pytest` only and runs entirely on one Linux host — a second machine is
simulated by writing entries whose `host` differs from the local one. See [`CLAUDE.md`](CLAUDE.md)
before changing anything on the hot path or in the removal logic.

## Status

**Running end to end against a live agterm**, across two Linux hosts and a Mac. 1777 tests, no
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
| `session split` / `session type` | ✅ `--help`-verified, then run |
| `session scratch` | ⬜ `--help`-verified, **not yet run** |
| a second instance: two bridges on one Mac | ⬜ tests only, **not yet run** |
| clicking a row from each instance → the right machine | ⬜ tests only, **not yet run** |
| `notify --target <row>` → banner on the right row | ✅ run by hand |
| the bridge sending it on a `blocked` transition → banner + Dock bounce | ✅ |
| the badge clearing when the agent leaves `blocked` | ✅ — on a row that is **not** selected; agterm never badges the row you are viewing |
| a new agent's row → banner + Dock bounce, naming its directory | ✅ |
| click-to-attach → right host, session **and pane** | ✅ |
| detach returns to the prompt, row survives | ✅ |
| `[s] split` → split pane with a shell on the agent's host | ✅ |
| `[d] drawer` → the same shell in the scratch drawer | ⬜ not yet run |
| a finished agent's row goes `[done]`, stays visible | ✅ |
| `agb close-done` → `agtermctl session close` | ✅ |
| automatic reap of dead own-host agents | ✅ |
| `--blink` on a transition into `active` | ✅ — observed blinking a live row |

**Every `agtermctl` clause this tool depends on has been exercised against a live agterm, except
`session scratch`** — the two rows marked ⬜ above. Its spelling is recorded verbatim from `--help`
and its call path is mutation-tested, but nobody has yet watched a drawer open, be hidden, and come
back with the same shell alive.

Still not exercised, and worth knowing:

- **A second instance, live** (0.5.0). Nobody has yet run two bridges on one Mac. The check that
  matters is **clicking a row from each instance and landing on the right machine** — that path
  (`pane_argv` → the row's command → `agb pane`'s own config read) is precisely the one every unit
  test passes through without performing. Worth running a bare `agb-refresh` while both are up on
  purpose too, to see whether the banner really does tell you which one it acted on.
- **`session scratch`'s behaviour**, as above. The `[s]` split it is modelled on *is* verified, and
  the two are the same two calls with different constants, so the risk is narrow — but "narrow" is
  not "none", which is the whole reason this table exists.
- **Whether `blink` is sticky or a one-shot animation.** The flag is confirmed accepted; agbridge
  sends it only on an actual transition into `active`, which is correct under either reading.
  `--auto-reset` is never sent — it was deliberately dropped, so its spelling stays unverified.
- **Long-running behaviour** — reconnects, the watchdog firing, `prune` against a genuinely dead
  host.

[`docs/agtermctl.md`](docs/agtermctl.md) tags every clause `CONFIRMED` or `ASSUMED` and records a
fallback for each assumption. If your `agtermctl` differs, that file is the one to correct.

## License

[MIT](LICENSE).
