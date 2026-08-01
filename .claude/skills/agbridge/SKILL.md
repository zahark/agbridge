---
name: agbridge
description: How to use, configure and troubleshoot agbridge — Claude Code agent status from remote hosts in agterm's sidebar. Use for adding a cluster host, adding a machine that shares no disk (a second statedir, feed and bridge — the --instance install), running a second Mac, removing a host, rows that are missing/stale/duplicated/blank, notifications, workspaces, attaching to an agent, or any "how do I …" about agb, agb-refresh, agb-claude, the bridge, the feed or the statedir.
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

That whole column is **one instance**. A machine that shares no disk with the first gets its own
statedir, feed and bridge — a second launchd job, rendering into the same sidebar — so read every
path below as "the default instance's", and see *Add a machine that shares no disk* for the rest.

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

On the Mac those are the **default instance's** paths. A second machine that shares no disk gets its
own instance, and the config, the rows map, the placements file and the log all move under its name
— see *Add a machine that shares no disk* below.

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

### Add a machine that shares no disk (a second instance)

The recipe above assumes the new host can write the **same** statedir. When it cannot — another
network, a cloud box, no common filesystem at all — it needs its own statedir, so its own feed and
its own bridge. That is an **instance**: an independent launchd job whose rows land in the same
sidebar. Nothing on the existing machines changes.

```sh
# on the Mac. --statedir is REQUIRED with --instance, and it is the NEW
# machine's own path -- absolute, and never spelled with a `~` the Mac would
# expand against its own home. `--instance auto` names it after the machine.
sh install.sh mac --instance hostb \
    --statedir /home/you/.agbridge \
    --feed-host hostb-alias \
    --agb-remote-path /path/to/agb/on/hostb \
    --farm hostb-alias                        # optional: run the farm side too
```

⚠️ **`--feed-host` and `--farm` are the same machine here — give them the same string.**
`--feed-host` is *stored* (`feed_host`) and used by the bridge on every connection; `--farm` is used
**once**, by the installer, to run the farm side instead of printing it, and is never written
anywhere. They differ only on a shared-disk cluster, where the farm install runs on every agent host
while one of them is the feed host. A wrong `--farm` is loud: the Mac half installs, then
`ssh: Could not resolve hostname …` and `the farm side failed; the Mac is configured, the farm is
not`. Fix the alias and re-run, or run the printed command by hand — nothing needs undoing.

Without `--farm`, run the `next:` command it prints on the new machine (it carries the mac-id *and*
this instance's statedir). The **host mapping is already written for you** — the installer ssh's
`--feed-host` for its hostname and records `host_<name>` in this instance's config (the `probed:`
line). Add it by hand only after `--no-probe`, a machine that would not answer, or a rename. What is
worth adding is the workspace:

```sh
echo 'workspace = hostb' >> ~/.config/agbridge/hostb/config   # nothing else groups rows by machine
```

`--instance <name>` is sugar over `--config`, `--label` and `--log-dir`. **Everything else follows
the config path**, because it is all derived from that file's directory:

| | default | `--instance hostb` |
|---|---|---|
| config | `~/.config/agbridge/config` | `~/.config/agbridge/hostb/config` |
| rows map | `~/.config/agbridge/rows` | `~/.config/agbridge/hostb/rows` |
| placements | `~/.config/agbridge/placements` | `~/.config/agbridge/hostb/placements` |
| `host_<name>`, `workspace`, `statedir` | in that config | in that config |
| launchd label | `com.agbridge` | `com.agbridge.hostb` |
| logs | `~/Library/Logs/agbridge/` | `~/Library/Logs/agbridge/hostb/` |
| the three code files, `~/.local/bin` | **shared** | **shared** — one install, N configurations |
| `mac_id` | minted | **adopted** — this instance's own config first, then the default's, minted only if neither has one; it names the Mac, not the connection |

Everyday operation:

```sh
agb instances                         # what this Mac carries: name, label, config
agb-refresh                           # repair EVERY instance
agb close-done                        # reclaim [done] rows in EVERY instance
agb-refresh --instance hostb          # repair THAT one; the others are untouched
agb close-done --config ~/.config/agbridge/hostb/config
```

⚠️ **A bare run is a sweep, not the default instance.** It used to be: `agb-refresh` stopped
`com.agbridge`, forgot the default instance's bindings and restarted it while you were trying to fix
`hostb`, reporting success in the same words. What narrows a run is naming a **map** —
`--instance`, `--label`, `--config`, `--rows`. **`--key` does not**: a key read out of a bridge log
does not say which instance minted it, so it is swept for and fails only when no instance had it.

⚠️ **`agb forget-rows` is the one command that refuses a bare run**, naming `--all`. Not because it
closes rows (`agb-refresh` closes every row it forgets too) but because nothing restarts the bridges
afterwards, so those rows stay closed until each one is bounced by hand. `--all` opts in; `--key`
sweeps; `--rows`/`--placements`/`--config` narrow, and `--rows` alone still implies the default
config.

⚠️ **An instance left without a running bridge fails the sweep** (exit 4, `no bridge was started
again for: <label>`), and a child failing does not stop the others — everything stopped is started
again and the summary names what failed.

Every one of these prints the instance and config it is acting on, once per instance — **read it**,
because on a *narrowed* run it is still the only thing that distinguishes the run you meant:

```
instance: hostb -- label com.agbridge.hostb, config /Users/you/.config/agbridge/hostb/config
```

Also worth knowing:

- **`--instance auto` names it after the machine**, reusing the hostname the installer already ssh's
  for the `host_<name>` mapping — one ssh, two readers. It prints `instance: auto -> hostb01 (read
  back from <alias>)`, and *that* is the name `agb-refresh --instance …` wants afterwards. It is a
  word you type on purpose: an **absent** `--instance` still means the default instance, because
  re-running `install.sh mac` is the upgrade path and auto-naming by default would mint a new
  instance on every upgrade. Any failure — unreachable machine, no `--feed-host`, `--no-probe`, a
  hostname that is not a usable label name — is a **refusal with nothing written**, never a
  fall-back to the default instance, which would repoint the machine you already have.
- **`--instance` without `--statedir` is refused**, and `install.sh farm --instance …` too — the
  first would silently inherit the other cluster's directory, the second writes a config nothing on
  the farm reads. Names are letters, digits, `-`, `_`; it is a label, a filename and two directories.
- **`agb-refresh --config <path>` is equivalent when you have the path but not the name** — it is
  what the bridge's own `no such session` hint prints, since a config path is all a bridge knows
  about itself. It looks the path up in the plists and bounces the job that holds its **map**, so the
  label moves with the config; the banner then names the instance it resolved. The match is on the
  **map** — the resolved directory, which is all `rows`/`placements` are derived from — not on the
  spelling, so `//`, a `.` segment, a relative path, a symlinked `$HOME`, a bare `<dir>/` and even
  `<dir>/some-other-name` all still find it. Where several jobs share one directory the one naming
  this exact file is used and **all of them are named out loud**, because they share a rows file and
  the others keep running over it. A plist with no `--config` counts as naming the default config
  (that is what its bridge resolves), so an old default job is not invisible to the search; one whose
  `--config` is elsewhere but whose `--rows` is in this map counts too, ranked last, because
  `agb bridge --rows` overrides the rows file the config would have chosen. If no plist claims the
  path it says so, keeps `com.agbridge`, and warns that it is about to bounce that job with this
  config's bindings. The plists are **parsed** (`plistlib`), not text-searched, so a hand-edited one
  is read the way launchd reads it — comments, CDATA, entities, a DOCTYPE, minification and a binary
  `plutil -convert binary1` copy all included. Only the part of `ProgramArguments` **after the
  `bridge` command word** counts, because that array is the whole command line and `agb` reads its
  command from the first argument: `<agb> --config X bridge` is `unknown command: --config`, a job
  that starts no bridge, and it used to claim X's map and outrank the job really holding it. What
  those arguments *mean* is asked of `agb bridge`'s own parser (loaded from beside the `--agb` path),
  not of a walk that imitates it — so an argv the bridge would **refuse** (`--config=` with no value,
  a stray positional, an unknown option, a `--watchdog` that is not a number) counts as naming no
  config, which is right: launchd restarts such a job for ever and no bridge ever starts. A file
  that is *not* a plist (truncated, or something else under a `com.agbridge.*` name) is skipped
  entirely rather than counted as naming the default config — and if `--python` names something that
  is not a usable python3, or `--agb` names a tree with no `agb_mac` beside it, the run **refuses**
  instead of reading every plist as silent and bouncing the default job. Likewise `agb-refresh --instance <name>` against an *unreadable* plist refuses and
  asks for `--config`, rather than guessing `~/.config/agbridge/<name>/config`; a plist that is not
  there at all still falls back to that convention.
- **An upgrade is one `install.sh mac`, but each job must be restarted**: `agb-refresh` for the
  default and `agb-refresh --instance <name>` for each other. A running bridge holds the code it
  started with.
- **`agb doctor` and `agb status-line` on the Mac read the default config only** — no `--config`. To
  diagnose an instance, run `agb doctor` on *its* machine and read *its* log
  (`~/Library/Logs/agbridge/<name>/bridge.err.log`).
- **A row's `agb pane` command carries `--config`** for a non-default instance; that is what makes
  click-to-attach reach the right machine. Rows minted before this existed carry none and fall back
  to the default config — `agb-refresh --instance <name>` re-mints them.
- **Around four machines this stops being pleasant** (N jobs, N ssh, N logs). One bridge with several
  feeds was designed and rejected — it is a data-model change, and one machine's outage would blank
  every row. `docs/design.md` §5, *One Mac, several instances*, has that and the seven limitations.

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
| duplicate rows | a previous refresh forgot bindings without closing the old rows | `agb-refresh` (it closes before forgetting), which sweeps every instance. To do one only: `agb-refresh --instance <name>`, or `--config <path>` for an install that has no instance name |
| not sure which instances this Mac has, or one seems to be missing from a sweep | a plist outside `~/Library/LaunchAgents`, or a job that was never rendered | `agb instances`. A job is swept iff its label is in the `com.agbridge` space **or** its `ProgramArguments` runs `<…>/agb bridge` |
| a bare `agb-refresh` exits non-zero saying `no bridge was started again for: <label>` | that instance's rows were forgotten and `launchctl` then refused both `bootstrap` and `load -w`, so its sidebar has nothing to re-mint it | re-run `install.sh mac` for that instance. This is exit **4** and is always an error, `--key` or not |
| `agb forget-rows` refuses to run and names `--all` | a bare run would forget every row of every instance, and nothing restarts the bridges afterwards | `--all` to mean it, `--key <key>` for one row wherever it lives, or `--config <path>` for one instance |
| every row duplicated right after upgrading to 0.5.0, on an install made with `install.sh mac --config <path>` and no `--instance` | the plist used to ignore that flag and now carries it, so the bridge looks for its map beside *that* config and mints everything again | `agb forget-rows --rows ~/.config/agbridge/rows` clears the orphans (it closes each as it forgets it); moving `rows` and `placements` beside the config before reinstalling avoids it. `--rows` **narrows** the run to that one map, which is why this recipe needs no `--all` |
| no glyph at all | `idle` renders as nothing — a `[?]` or `[done]` row, or a row not yet painted | check the title prefix |
| a row never updates | it may be an orphan not in the map | compare `cat ~/.config/agbridge/rows` against the sidebar |
| a config change seems to be ignored — a new `workspace`, or `notify_on_*` that never fires | the bridge reads its config **once, at startup**; the file changed, the running process did not. No error is printed | `agb-refresh`, which bounces every instance (or `--instance <name>` for one). ⚠️ Check `pgrep -f 'agb bridge'` shows a **different** pid afterwards — on a *narrowed* run, naming the wrong instance bounces the wrong one and still reports success |
| `[enter]` prints `open terminal failed: missing or unsuitable terminal: <name>` then `ssh exited 1 -- nothing was attached` | the ssh worked; **tmux** refused. That host has no terminfo entry for agterm's terminal, and `ssh -t` carries `TERM` across (agbridge never sets it). Ghostty, Kitty and WezTerm all ship their own; a cluster box has none | from a local shell **inside agterm** — not a row's `[s]`/`[d]`, which ssh to the agent's host — run `infocmp -x "$TERM" \| ssh <target> -- tic -x -`. `"$TERM"`, never a typed name: agterm's differs from your login shell's. Writes `~/.terminfo` remotely, no root. `older tic versions may treat the description field as an alias` is a warning, not a failure. Verify with `ssh <target> "infocmp -1 $TERM" >/dev/null 2>&1 && echo INSTALLED` — redirects **outside** the quotes, since a farm login shell is often tcsh and answers `Ambiguous output redirect.` to `2>&1`. Fixes that host only — repeat per machine. Details and fallbacks in [`docs/cookbook.md`](../../../docs/cookbook.md) |
| clicking a row prints `WARNING: this row's config could not be read` | `agb pane` resolves `host_<name>` and `jump_host` through that config; unread, the ssh target is the bare hostname and any jump host is gone. The errno on the line says which failure | fix the file's mode (or the mount); if the instance moved, `agb-refresh --config <path>` re-mints the rows with the new path |

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
| banner: an agent finished | `notify_on_completed_after` | a turn ends **that ran at least N seconds** — default 300. The number is the switch; `off`/`0` disables |
| the unseen badge is cleared | `notify_on_blocked` | the agent leaves `blocked` |

⚠️ The finished-turn banner is thresholded because `completed` fires **once per turn**: ungated it
announces the "yes" you typed three seconds ago, and there is no "only when I'm away" to fall back
on, since agterm banners and bounces even for the row you are looking at. A turn that started *and*
finished while the bridge was down is never announced — the one case people expect and do not get.

Whether the **Dock bounces** is agterm's setting, not agbridge's: Settings ▸ Notifications ▸
Dock-icon bounce. ⚠️ agterm never raises a badge on the row you are **currently viewing**, so test
notifications on a row that is not selected.

### Workspaces

Set `workspace = <name>` on the Mac and new rows land there (created if absent). A row you drag
elsewhere keeps its place: `agb-refresh` records each row's workspace before closing it and restores
it afterwards, via `placements` **beside that instance's config** —
`~/.config/agbridge/placements` for the default one, `~/.config/agbridge/<name>/placements` for any
other.

## Config keys

`key = value`, never read on the hot path. `~/.config/agbridge/config` for the default instance;
`~/.config/agbridge/<name>/config` (or whatever `install.sh mac --instance <name> --config <path>`
was told) for any other, and the Mac-side keys below are **per instance**.

| key | side | meaning |
|---|---|---|
| `statedir` | both | the shared directory; `$AGB_STATEDIR` overrides |
| `mac_id` | both | names `bridge/<mac-id>.beat`; minted by `install.sh mac` |
| `feed_host` | Mac | ssh target that runs the feed |
| `agb_remote_path`, `remote_python` | Mac | absolute cluster-side paths |
| `jump_host` | Mac | for hosts not directly reachable |
| `workspace` | Mac | agterm workspace for new rows, by name |
| `notify_on_blocked`, `notify_on_new_row` | Mac | `0`/`no`/`off`/`false` to disable |
| `notify_on_completed_after` | Mac | seconds a turn must run before finishing is announced; default `300`, and `0`/`off`/`no`/`false`/negative disables |
| `host_<hostname> = <ssh target>` | Mac | a record's host is a hostname, not an alias |

⚠️ **A bridge-side key needs `agb-refresh` before it means anything.** The bridge reads its config
**once, at startup**, so editing `workspace`, `feed_host` or any `notify_on_*` leaves the running
process on the old value — silently, with no error and no warning. `host_<hostname>` and `pane`'s
use of `jump_host` are the exception: they are read per click, so they take effect immediately.
If a config change appears to have been ignored, this is why.

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
- **With several instances, a helper without `--instance` succeeds on the wrong one.** It stops
  `com.agbridge` and forgets the default instance's bindings, in the same words it would have used
  for the one you meant. The first line of its output names what it acted on; that banner is the
  whole mitigation.

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
