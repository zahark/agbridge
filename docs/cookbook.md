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
everywhere below.

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

```sh
agb doctor
```

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
```

Your sidebar then reads something like:

```
● api-refactor · buildbox01 · /work/api  · %0     working
◐ db-migration · buildbox01 · /work/db   · %1     waiting for you
  docs-pass    · buildbox01 · /work/docs · %2     idle
```

**Click the row that wants you**, then choose at the prompt:

```
[enter] attach   [s] shell   [q] quit >
```

- **Enter** — attach to the agent's own tmux pane. `Ctrl-b d` detaches and returns you here, so you
  can re-attach without going back to the sidebar.
- **`s`** — open agterm's split pane beside this one with a plain shell on the same host, in the
  agent's directory. Both panes belong to the same row.

Rows are titled `label · host · cwd · pane`, where the label is the tmux session name. To change it
afterwards, on the host:

```sh
agb rename a-better-name          # the agent you are sitting in
agb rename b7ed a-better-name     # another row, by key prefix
```

`agb doctor` lists the keys this host can see.

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
   glyph would be indistinguishable from a finished one.
3. **An agent started outside tmux gets a status-only row.** It shows state correctly but has
   nothing to attach to. That is by design, not a failure.

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
~/.local/lib/agbridge/agb-refresh              # all rows
~/.local/lib/agbridge/agb-refresh --key <key>  # just one, if the others are still live
```

Nothing on the farm is touched; rows come back with the same identities on the next snapshot.
Reopen agterm first if it is closed.

**Clicking a row says `Could not resolve hostname`** — the `host_<hostname>` mapping is missing.
Add it to the Mac's config (takes effect on the next click, no restart):

```sh
echo 'host_buildbox01 = myfarm' >> ~/.config/agbridge/config
```

For a host reachable only through another, add `jump_host = myfarm` as well.

**A row is stuck and nothing clears it** — `agb doctor` lists *unadjudicable* entries, and
`agb prune` removes them under per-entry confirmation. It is the only destructive command, and it
never removes an entry whose process it can prove is alive.

---

## What next

- [`commands.md`](commands.md) — every command and flag
- [`tmux.md`](tmux.md) — the `bridge:UP`/`bridge:DOWN` status-line segment
- [`design.md`](design.md) — why liveness is proven rather than inferred, and the rest of the design
