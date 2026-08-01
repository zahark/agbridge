# Onboarding cookbook

The shortest path from nothing to a working sidebar. Roughly 10 minutes.

Throughout: **`buildbox01`** is a Linux host where your agents run, and
**`myfarm`** is the ssh alias *your Mac* uses to reach it. Substitute your own.

> A record's `host` is the machine's **hostname**; `--feed-host` is an **ssh alias**. They are
> often different, and the installer now derives the mapping between them for you.

---

## Before you start

| Need | Check |
|---|---|
| Python 3.6+ on the Linux host | `python3 -V` |
| A directory every agent host sees as the same files | `touch ~/.agb-probe` on one host, `ls ~/.agb-probe` on another |
| `agterm` on the Mac, with `agtermctl` | `agtermctl --help` |
| `ssh` from the Mac to the host, non-interactively | `ssh myfarm hostname -s` |
| `tmux` on the Linux host | `tmux -V` |

If the shared-directory check fails, pick a path that *is* shared and pass it as `--statedir`
everywhere below. If **no** path is shared — the machine has no disk in common with the others at
all — it does not have to be left out: it becomes its own instance, with its own statedir and its own
bridge, rendering into the same sidebar. See
[A machine with no shared disk](#a-machine-with-no-shared-disk).

---

## Step 1 — Get the code (Linux host)

Clone somewhere every agent host can see:

```sh
cd ~ && git clone https://github.com/zahark/agbridge.git
cd agbridge && python3 -m pytest tests/ -q      # optional, ~20 s
pwd                                              # note this path
```

## Step 2 — Get the code (Mac)

```sh
cd ~ && git clone https://github.com/zahark/agbridge.git
```

## Step 3 — Install on the Mac

```sh
cd ~/agbridge
sh install.sh mac \
    --feed-host myfarm \
    --agb-remote-path ~/agbridge/agb        # the path from Step 1, absolute
```

It copies three files, mints a `mac_id`, writes the config, loads the launchd job — and ssh's once
to `myfarm` to learn its hostname so it can map `host_buildbox01 = myfarm` for you. That mapping is
what makes rows clickable; `--no-probe` skips it if you would rather set it by hand.

**Copy the `mac_id` it prints.**

## Step 4 — Install on the Linux host

```sh
cd ~/agbridge
sh install.sh farm --mac-id <the id from step 3>
```

Run this on **every** host that runs agents. It writes the config and merges four hooks into
`~/.claude/settings.json`.

> ⚠️ It removes any pre-existing `agr` hooks — they do the same job and both firing doubles the
> per-tool-call cost. The previous file is copied to `~/.claude/settings.json.agb.bak`.

## Step 5 — Check

`install.sh farm` writes an `agb` wrapper into `~/.local/bin`. If that is not on your `$PATH` it
says so — add it, or use the full form below.

```sh
agb doctor
# or, without the wrapper:
/usr/bin/python3 -S -E ~/agbridge/agb doctor
```

> `agb` itself is deliberately **not executable and has no shebang**: a hook must pass `-S -E`, and
> neither a shebang nor `env` can. The wrapper is what makes `agb <cmd>` work.

Every line should be `[ok]`. The one that proves the whole chain is **`bridge beat`** — a fresh
beat means your Mac is connected and consuming. `no beat file at all` means the bridge is not
running; see [Troubleshooting](#troubleshooting).

## Step 6 — Start an agent

```sh
./agb-claude my-first-task
```

That opens a tmux session named `my-first-task` and starts Claude Code in it. Then **type
something** — a row appears within a couple of seconds.

Put it on your `PATH` to drop the `./`:

```sh
ln -s ~/agbridge/agb-claude ~/bin/agb-claude
```

---

## Daily use

```sh
agb-claude api-refactor       # new named session, or re-attach if it exists
agb-claude                    # named after the current directory
agb-claude docs -- --model opus   # everything after `--` goes to claude
agb-claude work -- --resume <session-id>   # options need the `--`
agb-claude -d review          # start it in the background; the row appears on its own
```

Your sidebar then reads something like:

```
● api-refactor · buildbox01 · /work/api  · %0     working
◐ db-migration · buildbox01 · /work/db   · %1     waiting for you
  docs-pass    · buildbox01 · /work/docs · %2     idle
```

**Click the row that wants you**, then choose at the prompt:

```
[enter] attach   [s] split   [d] drawer   [q] quit >
```

- **Enter** — attach to the agent's own tmux pane. `Ctrl-b d` detaches and returns you here, so you
  can re-attach without going back to the sidebar.
- **`s`** — open agterm's split pane beside this one with a plain shell on the same host, in the
  agent's directory. Both panes belong to the same row.
- **`d`** — the same shell in agterm's scratch drawer, which lies *over* this pane instead of taking
  width from it. Hide it and it stays alive; `d` brings it back.

Which to reach for: `s` when you want to watch the agent **while** you type — a test run beside the
output that provoked it. `d` for a look-and-leave: `git log`, `ls`, checking a path. The drawer
cannot show you both at once, and the split cannot give the agent its full width back.

Rows are titled `label · host · cwd · pane`, where the label is the tmux session name. To change it
afterwards, on the host:

```sh
agb rename a-better-name          # the agent you are sitting in
agb rename b7ed a-better-name     # another row, by key prefix
```

`agb list` shows every session and its key; `agb rename` with no arguments prints the same table
alongside the usage.

Renaming the tmux session does **not** work — the name was read once when the agent started, and
changing it only breaks click-to-attach.

When an agent finishes its row is marked `[done]`. Reclaim them on the Mac with:

```sh
agb close-done
```

---

## The three rules

1. **Name the tmux session before starting the agent.** The name is resolved once, at the agent's
   first hook, and never refreshed. `agb-claude` exists so you cannot forget. Renaming a session
   afterwards leaves the row pointing at a name that no longer exists.
2. **A row appears on the first hook, not at launch.** Starting `claude` writes nothing — type a
   prompt and the row appears. There is deliberately no `SessionStart` hook, because a row with no
   glyph would be indistinguishable from a finished one. `agb-claude -d` handles this for you: it
   starts the session in the background with an opening prompt, so the row appears without you
   attaching. (Not in a directory Claude has not been trusted in yet — it waits on that prompt.)
3. **An agent started outside tmux gets a status-only row.** It shows state correctly but has
   nothing to attach to. That is by design, not a failure.

---

## A machine with no shared disk

The check at the top of this page — `touch ~/.agb-probe` here, `ls ~/.agb-probe` there — has one
failure that is not a misconfiguration: a machine that genuinely shares no filesystem with the
others. A box on another network, a cloud instance, a laptop under a desk. It cannot write into the
first statedir, so it gets **its own** — and with it its own feed and its own bridge.

That pair is an **instance**. Rows from every instance land in the same sidebar; nothing about the
machines you already have changes, and no cluster-side change is needed on any of them.

### Step 1 — Get the code onto the new machine

Exactly as Step 1 above, on the new machine:

```sh
cd ~ && git clone https://github.com/zahark/agbridge.git
cd agbridge && pwd            # note this path
```

### Step 2 — Add the instance on the Mac

```sh
cd ~/agbridge
sh install.sh mac --instance hostb \
    --statedir /home/you/.agbridge \
    --feed-host hostb-alias \
    --agb-remote-path /home/you/agbridge/agb      # the path from Step 1
```

`hostb` is a name you choose — letters, digits, `-` and `_`. It becomes a launchd label, a plist
filename, a log directory and a config directory, which is why nothing else is allowed in it.

⚠️ **`--statedir` is required here.** It is the whole point — the new machine's own directory — and
without it the instance would inherit the *first* cluster's path: ssh to the right machine, read the
wrong directory, then create it and report an empty farm for ever. Spell it as it exists **on that
machine**, absolutely: a `~` is expanded by the Mac's shell, and the Mac's home is not the path the
feed will be given.

What the installer writes, all of it new:

| | |
|---|---|
| config | `~/.config/agbridge/hostb/config` |
| launchd job | `com.agbridge.hostb` (plist in `~/Library/LaunchAgents`) |
| logs | `~/Library/Logs/agbridge/hostb/` |

And what appears **later**, created by the bridge itself the first time it has something to record —
so an empty `~/.config/agbridge/hostb/` straight after the install is normal, not a failed install:

| | |
|---|---|
| rows map | `~/.config/agbridge/hostb/rows` — written when the first row is minted |
| remembered workspaces | `~/.config/agbridge/hostb/placements` — written by `agb-refresh` |

Your existing instance is untouched, and the **code** is shared: one `~/.local/lib/agbridge`, N
configurations. It also **adopts a `mac_id` rather than minting one** when you do not pass
`--mac-id`: this instance's own config first (so re-running the install on an existing instance
never changes its id), then the default instance's. That id names your Mac, and each cluster's beat
file lives in its own statedir, so the same id in both places is correct. If neither config has one —
a Mac whose *first* install is a named instance — it mints one, and the `next:` hint carries it.

**Copy the `next:` command it prints.** It already carries the mac-id *and* this instance's statedir.

### Step 3 — Install the farm side on the new machine

Paste that command there. It is the ordinary farm install — config plus the four hooks — pointed at
the new machine's own statedir:

```sh
cd ~/agbridge
sh install.sh farm --mac-id <the id> --statedir /home/you/.agbridge
```

### Step 4 — Check both halves

On the **new machine**:

```sh
agb doctor          # `bridge beat` a few seconds old means this instance's bridge is connected
```

On the **Mac**, if it is not:

```sh
tail -30 ~/Library/Logs/agbridge/hostb/bridge.err.log
launchctl print gui/$(id -u)/com.agbridge.hostb | head
```

Note the paths: **this instance's** log and **this instance's** label. The plain
`~/Library/Logs/agbridge/bridge.err.log` belongs to the first machine and will look perfectly
healthy while this one is broken.

### Step 5 — Make its rows clickable

The `host_<hostname>` mapping goes in **that instance's** config, not the default one:

```sh
echo 'host_hostb01 = hostb-alias' >> ~/.config/agbridge/hostb/config
```

That is the whole reason a row's command carries `--config`: clicking a row runs `agb pane`, which
resolves the hostname through a config of its own, and without the flag it would read the *first*
instance's table and take you to the wrong machine — or nowhere.

⚠️ Rows minted before the Mac was upgraded to 0.5.0 carry no such flag. `agb-refresh --instance hostb`
re-mints them; nothing updates a live row in place.

### Step 6 — Tell them apart in the sidebar

Nothing in a row says which machine it came from. Give each instance a workspace:

```sh
echo 'workspace = hostb' >> ~/.config/agbridge/hostb/config
```

New rows land there, and one you drag elsewhere stays where you put it.

### Living with more than one

| | |
|---|---|
| **pass `--instance` to every helper** | `agb-refresh --instance hostb`, `agb close-done --config ~/.config/agbridge/hostb/config`. Without it they act on the **default** instance — successfully, which is the trap |
| **read their first line** | `agb-refresh` prints `instance: hostb -- label com.agbridge.hostb, config …/hostb/config`; `close-done` and `forget-rows` print the config, map and placements file they opened. Always, before doing anything, because acting on the wrong instance otherwise looks exactly like acting on the right one |
| **upgrades: install once, refresh each** | `sh install.sh mac …` updates the shared code, but a running bridge keeps the copy it started with. Run `agb-refresh` for the default instance **and** `agb-refresh --instance hostb` for each named one |
| **`agb doctor` on the Mac reads the default config only** | it has no `--config`. Diagnose an instance from *its* machine (`agb doctor` there) and from its own log |
| **rows are per instance** | refreshing one never moves the other's rows. Correct, and surprising the first time |
| **around four machines this stops being pleasant** | four launchd jobs, four ssh connections, four logs. It is a deliberate ceiling — see [`design.md`](design.md) §5 for the alternative that was rejected and why |

---

## Troubleshooting

**No rows at all, and `doctor` says `no beat file at all`** — the bridge is not running. On the Mac:

```sh
tail -30 ~/Library/Logs/agbridge/bridge.err.log
launchctl print gui/$(id -u)/com.agbridge | head
```

Most common cause: `agtermctl` is not on the **launchd job's** `PATH`, which is not your login
`PATH`. Reinstall with `--launch-path "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"`.

**`doctor` is all `[ok]` but no rows appear** — the Mac and the host disagree about the statedir.
Compare `agb doctor`'s statedir line against `statedir` in the Mac's `~/.config/agbridge/config`;
they must be the same path.

**Rows gone after closing agterm**, or `error: no such session: <uuid>` in the bridge log — agterm
has forgotten rows the map still names. On the Mac:

```sh
agb-refresh              # all rows
agb-refresh --key <key>  # just one, if the others are still live
```

`install.sh mac` links it into `~/.local/bin` beside `agb`. If your install predates that, or you
passed `--no-wrapper`, it is `~/.local/lib/agbridge/agb-refresh`.

Nothing on the farm is touched; rows come back with the same identities on the next snapshot.
Reopen agterm first if it is closed.

**After upgrading the Mac's files**, run it too: a row's `agb pane` loads its code when the row is
created and keeps it, so new features do not reach existing rows until they are recreated.

**After rebooting the Mac** — the same thing, and the agents are fine. Nothing on the farm noticed:
the tmux sessions, the agents inside them and their state files are untouched, because the Mac holds
none of it. The bridge restarts itself (the LaunchAgent has `RunAtLoad`), reconnects, and its beat
resumes within a second or two. Confirm from any farm host:

```sh
agb doctor          # bridge beat: seconds old, and every agent still listed
```

What does *not* survive is agterm: it comes back as a fresh app with no sessions, while the Mac's
map still names the row ids from before. So the sequence is **open agterm, then `agb-refresh`** —
without it the bridge logs `no such session: <uuid>` against every row and the sidebar stays empty.
Rows return on the next snapshot with the same identities.

A reboot needs no re-install and no farm-side command. If the prompt comes back reading `[s] shell`
rather than `[s] split   [d] drawer`, that is unrelated — the Mac is on a pre-0.3.0 build; run
`install.sh mac` and then `agb-refresh` again.

**Clicking a row says `Could not resolve hostname`** — the `host_<hostname>` mapping is missing.
Add it to the Mac's config (takes effect on the next click, no restart):

```sh
echo 'host_buildbox01 = myfarm' >> ~/.config/agbridge/config
```

For a host reachable only through another, add `jump_host = myfarm` as well.

**A row is stuck and nothing clears it** — `agb doctor` lists *unadjudicable* entries, and
`agb prune` removes them under per-entry confirmation. It is the only destructive command, and it
never removes an entry whose process it can prove is alive.

**Clicking a second instance's row lands on the wrong machine** — that row was minted before the
Mac's files were upgraded, so its command carries no `--config` and `agb pane` fell back to the
default instance's `host_<name>` table. `agb-refresh --instance <name>` re-mints it — a row's command
is fixed when the row is created, and nothing updates a live one in place. If it still goes to the
wrong machine afterwards, the `host_<hostname>` line is in the wrong file: it belongs in
`~/.config/agbridge/<name>/config`, not the default config.

**Every row appeared twice after an upgrade, and you install with `--config <path>`** — a non-default
config, no `--instance`. That flag used to be ignored by the launchd job; now the plist carries it,
so the bridge looks for its rows map beside *that* config, finds nothing, and mints a second row for
every agent. Clear the orphans, which closes them in agterm as it forgets them:

```sh
agb forget-rows --rows ~/.config/agbridge/rows
```

Moving `rows` and `placements` next to the config *before* reinstalling avoids it entirely.
`agb-refresh --config <path>` works on such an install — it needs no instance name.

---

## What next

- [`commands.md`](commands.md) — every command and flag
- [`tmux.md`](tmux.md) — the `bridge:UP`/`bridge:DOWN` status-line segment
- [`design.md`](design.md) — why liveness is proven rather than inferred, and the rest of the design
