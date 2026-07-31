---
name: agbridge
description: How to use, configure and troubleshoot agbridge — Claude Code agent status from remote hosts in agterm's sidebar. Use for adding a cluster host, running a second Mac, removing a host, rows that are missing/stale/duplicated/blank, notifications, workspaces, attaching to an agent, or any "how do I …" about agb, agb-refresh, agb-claude, the bridge, the feed or the statedir.
allowed-tools: Read, Grep, Glob, Bash
---

# agbridge

Agents on remote Linux hosts write their state to a shared directory. The Mac **pulls** that state
over one long-lived `ssh` and renders it as rows in [agterm](https://github.com/umputun/agterm)'s
sidebar. No reverse tunnel, no sockets, no daemon on the cluster.

**Answer from this file where it is covered. For anything else, read the docs listed at the bottom
rather than guessing — this project's own rule is that a claim about agterm or the wire is verified,
never inferred.**

## The mental model

Three machines, one direction of flow:

```
cluster host: agb hook   -> writes <statedir>/sessions/<host>/<key>.{json,state}
cluster host: agb feed   -> reads them, streams NDJSON over the Mac's ssh
Mac:          agb bridge -> owns key -> agterm row, calls agtermctl
Mac:          agb pane   -> what a row runs when you click it
```

Two facts explain most confusion:

- **Liveness is proven, never inferred.** No age is ever turned into a status. A row is removed only
  when death is proven (`kill(pid,0)` plus a `starttime` match), which is why nothing disappears on
  a timeout.
- **A row's state only moves when Claude Code fires a hook** (`UserPromptSubmit`/`PostToolUse` →
  `active`, `Notification` `permission_prompt` → `blocked`, `Stop` → `completed`). Attaching,
  detaching, scrolling and pressing enter at a shell change nothing, correctly.

## Where things live

| | path |
|---|---|
| shared statedir (cluster) | `~/.agbridge` by default, or `statedir` in the config |
| config (both sides) | `~/.config/agbridge/config` |
| Mac: key → row map | `~/.config/agbridge/rows` |
| Mac: remembered workspaces | `~/.config/agbridge/placements` |
| Mac: installed code | `~/.local/lib/agbridge/{agb,agb_mac,agb_ops,agb-refresh}` |
| Mac: bridge log | `~/Library/Logs/agbridge/bridge.err.log` |
| cluster: breadcrumbs | `<statedir>/err/<host>.<key>.log` |

## First move for any problem

```sh
agb doctor            # on a cluster host. Probes; does not merely check existence
```

It reports the bridge beat (is the Mac connected?), every session it knows, and any entry it cannot
adjudicate. `bridge beat` more than a few seconds old means the Mac side is not reading.

## Recipes

### Add another cluster host

The cluster side is **not** a no-op even on a shared mount: NFS carries the binary, not the config or
the hooks.

```sh
# on the new host, with the SAME mac-id and statedir as the others
sh /path/to/agbridge/install.sh farm --mac-id <the id the Mac printed> --statedir <shared path>
```

Then on the **Mac**, so click-to-attach can reach it — a record's `host` is a *hostname*, not an ssh
alias:

```sh
echo 'host_newbox01 = newbox-ssh-alias' >> ~/.config/agbridge/config
```

Takes effect on the next click; no restart. If the host is only reachable through another, set
`jump_host` too. Rows appear on their own once an agent runs there — nothing needs registering.

### Run a second Mac against the same cluster

Works, and needs **no cluster-side change**. Each Mac mints its own `mac_id`, writes its own
`bridge/<mac-id>.beat`, and keeps its **own** `rows` map — rows are per-Mac, not shared.

```sh
# on the second Mac only
sh install.sh mac --feed-host <cluster host> --agb-remote-path <path to agb on the cluster> \
                  --statedir <shared path>
```

⚠️ **Do not re-run `install.sh farm` with the second Mac's id.** The cluster config holds one
`mac_id`, and it is read by `agb doctor` and `agb status-line` only — the feed receives its id on the
command line. Overwriting it just points those two at the other Mac. If you want a cluster host's
tmux segment to follow the second Mac, pass `--mac-id` to `status-line` there instead of rewriting
the config.

### Stop tracking a cluster host

Nothing ages out by itself — removal requires proof. In order:

```sh
agb list                                  # find the keys, on any cluster host
agb prune --via-ssh <that host>           # re-issue removal ON the owning host, where kill(pid,0) means something
```

`--via-ssh` turns the age heuristic into a proof. If the host is gone for good and cannot be
reached, name the entries explicitly from another host — `agb prune --key <host>/<key>` — and
confirm each. There is no `--force`, deliberately.

Then on the Mac: delete the `host_<name>` line from the config, and `agb close-done` to reclaim the
`[done]` rows.

### A row is missing, stale, duplicated, or shows no glyph

| symptom | cause | fix |
|---|---|---|
| all rows `[?]` | the feed is not talking — VPN, ssh, or the cluster | check `agb doctor`'s beat; `ssh <feed host> true` |
| a row vanished after you typed `exit` | agterm destroys a session when its command exits, and `exit`/`q` ends `agb pane` | expected; `agb-refresh` brings it back |
| rows gone after closing/reinstalling agterm | agterm lost its sessions; the map still names them | `agb-refresh` |
| duplicate rows | a previous refresh forgot bindings without closing the old rows | `agb-refresh` (it closes before forgetting) |
| no glyph at all | `idle` renders as nothing — a `[?]` or `[done]` row, or a row not yet painted | check the title prefix |
| a row never updates | it may be an orphan not in the map | compare `cat ~/.config/agbridge/rows` against the sidebar |

`agb-refresh` on the Mac is the general repair: stop the bridge → forget the bindings → start it.
Nothing on the cluster is touched and rows come back with the same identities.

### After a Mac reboot

Nothing on the cluster noticed — agents, tmux sessions and state files are untouched, and the bridge
restarts itself (`RunAtLoad`). Only agterm loses its sessions, so: **open agterm, then
`agb-refresh`.**

### After upgrading the Mac's files

`git pull` is not enough — the bridge loads `agb_mac` from `~/.local/lib/agbridge/`, not the
checkout:

```sh
sh install.sh mac …    # same flags as the original install; idempotent
agb-refresh            # existing rows keep the `agb pane` code they were CREATED with
```

### Start an agent so it gets a row

A row appears on the first **hook**, not at launch, and click-to-attach needs a **named tmux
session**:

```sh
agb-claude my-task            # starts Claude Code in tmux session "my-task"
agb-claude -d my-task         # detached, with a greeting so the row appears immediately
agb-claude work -- --resume <id>    # anything after -- goes to claude
```

### Working with a row

Click it and `agb pane` offers:

```
[enter] attach   [s] split   [d] drawer   [q] quit >
```

`enter` joins the agent's tmux pane (detaching returns to the prompt). `s` opens a shell **beside**
it, `d` the same shell in agterm's scratch drawer **over** it. `q`/`quit`/`exit` leaves — and
destroys the row, because agterm closes a session when its command exits.

### Rename a row

```sh
agb rename a-better-name          # the agent you are sitting in
agb rename b7ed a-better-name     # another, by key prefix
agb list                          # if you need the keys
```

### Notifications

On by default; each has its own config key:

| | key | when |
|---|---|---|
| banner: an agent needs you | `notify_on_blocked` | a transition into `blocked` |
| banner: a new agent appeared | `notify_on_new_row` | a row is minted (silent for 3 s after a bridge start/reconnect, or a refresh would banner every row) |
| the unseen badge is cleared | `notify_on_blocked` | the agent leaves `blocked` |

Whether the **Dock bounces** is agterm's setting, not agbridge's: Settings ▸ Notifications ▸
Dock-icon bounce. ⚠️ agterm never raises a badge on the row you are **currently viewing**, so test
notifications on a row that is not selected.

### Workspaces

Set `workspace = <name>` on the Mac and new rows land there (created if absent). A row you drag
elsewhere keeps its place: `agb-refresh` records each row's workspace before closing it and restores
it afterwards, via `~/.config/agbridge/placements`.

## Config keys

`~/.config/agbridge/config`, `key = value`, never read on the hot path.

| key | side | meaning |
|---|---|---|
| `statedir` | both | the shared directory; `$AGB_STATEDIR` overrides |
| `mac_id` | both | names `bridge/<mac-id>.beat`; minted by `install.sh mac` |
| `feed_host` | Mac | ssh target that runs the feed |
| `agb_remote_path`, `remote_python` | Mac | absolute cluster-side paths |
| `jump_host` | Mac | for hosts not directly reachable |
| `workspace` | Mac | agterm workspace for new rows, by name |
| `notify_on_blocked`, `notify_on_new_row` | Mac | `0`/`no`/`off`/`false` to disable |
| `host_<hostname> = <ssh target>` | Mac | a record's host is a hostname, not an alias |

## Things that surprise people

- **agterm closes a session when its command exits.** So `q`/`quit`/`exit` at the `agb pane` prompt
  destroys the row of a live agent. The bridge notices (`no such session`), says so **once**, and
  stops writing to it — the binding is kept, so the row stays gone until `agb-refresh`.
- **`idle` renders as no glyph**, and agents never report `idle` — only the bridge does, for `[?]`
  (feed quiet) and `[done]` (agent gone). A blank row is one of those, or a row not yet painted.
- **A row's `agb pane` code is fixed at creation.** New features do not reach existing rows until
  `agb-refresh` re-mints them.
- **`agb` is byte-capped** (`tests/conftest.py`'s `AGB_PARSE_BUDGET`) because it is re-parsed on every
  hook. Never add to it casually.
- **`agb prune` is the only destructive command**, and asks per entry.

## Documentation

All paths are relative to the **agbridge checkout**. If this skill is reached through a symlink in
`~/.claude/skills/`, resolve them there — `git -C <checkout> grep` is the reliable way to search
them.

| file | for |
|---|---|
| [`docs/cookbook.md`](../../../docs/cookbook.md) | zero to working sidebar, then troubleshooting |
| [`docs/commands.md`](../../../docs/commands.md) | every command and flag, with the real defaults |
| [`docs/design.md`](../../../docs/design.md) | the authority on *why* — state model, liveness, wire, failure modes |
| [`docs/agtermctl.md`](../../../docs/agtermctl.md) | what agterm's CLI does, each clause tagged CONFIRMED or ASSUMED |
| [`docs/tmux.md`](../../../docs/tmux.md) | the `bridge:UP` status-line segment |
| [`CLAUDE.md`](../../../CLAUDE.md) | invariants, conventions, current state — read before changing code |
| [`CHANGELOG.md`](../../../CHANGELOG.md) | every change with the reason it was made |

⚠️ **When a question is about agterm's behaviour, check `docs/agtermctl.md` first and run the command
on the Mac if it is not there.** `agterm.com/commands` has twice described behaviour the installed
binary does not have.
