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
    --instance buildbox01 \
    --statedir /home/you/.agbridge \
    --feed-host myfarm \
    --agb-remote-path /home/you/agbridge/agb    # the path from Step 1, absolute
```

It copies three files, mints a `mac_id`, writes the config, loads the launchd job — and ssh's once
to `myfarm` to learn its hostname so it can map `host_buildbox01 = myfarm` for you. That mapping is
what makes rows clickable; `--no-probe` skips it if you would rather set it by hand.

**Copy the `mac_id` it prints.**

⚠️ **`--instance` is required — there is no unnamed install.** The name is yours to choose (letters,
digits, `-`, `_`); it becomes this install's config directory, launchd label and log directory, and
it is what `agb instances` lists and what `agb-refresh --instance …` takes. `--instance auto` reads it
off `--feed-host` instead, using the same ssh the mapping above needs, and prints what it decided.
A nameless `sh install.sh mac …` is **refused and writes nothing** — every Mac-side instance is named
so that no command has to guess which one you meant.

⚠️ **`--statedir` is required the first time**, and it is the **shared directory from *Before you
start*, spelled absolutely as it exists on the Linux host**. A `~` is expanded by the *Mac's* shell,
and the Mac's home is not the path the feed will be given. Re-running this later to pick up new code
does **not** need the flag again — the installer reads it back out of this instance's own config and
says so:

```
instance: buildbox01 -- label com.agbridge.buildbox01, config …/agbridge/buildbox01/config
statedir: adopted /home/you/.agbridge from …/agbridge/buildbox01/config
```

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
agb-claude -d review          # background; the row is minted before Claude even starts
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

Rows are titled `label · host · cwd · pane` by default, where the label is the tmux session name.
To change the label afterwards, on the host:

```sh
agb rename a-better-name          # the agent you are sitting in
agb rename b7ed a-better-name     # another row, by key prefix
```

### Shortening the rows

That default runs to about 70 characters, and on a single-host setup roughly a third of it is the
hostname repeated on every row. `row_fields`, in the **Mac's** config, picks the fields and their
order:

```sh
echo 'row_fields = label,cwd:base,pane' >> ~/.config/agbridge/config
agb-refresh
```

```
agbridge_dev · dev01-container-xterm-032 · /home/zk/agbridge-public · %15    73 chars
agbridge_dev · agbridge-public · %15                                        36
agbridge_dev · %15                                       row_fields = label,pane
```

Any of `label`, `host`, `cwd`, `pane`, `beat`, `key`, comma-separated, `cwd:base` for the
directory's basename alone. A name it does not recognise refuses the **whole** list, keeps the
default, and says why in `~/Library/Logs/agbridge/bridge.err.log` — so "nothing changed at all" is
the symptom of a typo, and the log is the only place the reason appears.

Worth knowing before you drop fields:

- **`host` is safe to drop only while you have one machine.** Add a second and two agents with the
  same label render identical rows.
- **`beat` costs nothing to keep.** It renders only when an agent has gone quiet, and it is the only
  place agbridge tells you *how* quiet — it refuses to turn an age into a status, so the number in
  the title is what it offers instead.
- **`pane` is what separates two agents in one tmux session.** They share label, host, cwd and tmux;
  the pane id is the difference.

⚠️ **`agb-refresh` is not optional.** Every Mac-side key is read once, when the bridge starts. Edit
the file and nothing happens, with no error — see *Troubleshooting* if that catches you.

`agb list` shows every session and its key; `agb rename` with no arguments prints the same table
alongside the usage.

Renaming the tmux session does **not** work — the name was read once when the agent started, and
changing it only breaks click-to-attach.

When an agent finishes its row is marked `[done]`. Reclaim them on the Mac with:

```sh
agb close-done
```

With more than one instance installed that reclaims `[done]` rows in **all** of them, one banner
apiece; `--config <path>` narrows it to one. On a Mac with a single install it says
`no instances found …; acting on the default map` and behaves exactly as it always has.

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

⚠️ **The check is about the DISK, not the path.** Two sites that both mount home directories under
the same convention give you the same `--statedir` string on both machines — `/home/you/.agbridge`
in each case — while the two are entirely separate filesystems. That is the normal shape of this and
is fine. What you must not do is point a second instance at a statedir the first one can *see*: both
bridges then read every session and **every agent gets two rows**, one per instance, each with its
own `agb pane` command. Nothing errors; the sidebar just doubles. So run the probe rather than
comparing the paths by eye:

```sh
touch ~/.agb-probe                    # on the machine you already have
ls -l ~/.agb-probe                    # on the new one
```

Found it? Then this is not a no-shared-disk machine at all, and you want the much shorter recipe:
`install.sh farm --mac-id <the id>` over there and nothing on the Mac — the bridge you already have
picks its agents up on the next snapshot.

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

Or let the machine name itself, using the hostname the installer already ssh's for anyway:

```sh
sh install.sh mac --instance auto \
    --statedir /home/you/.agbridge \
    --feed-host hostb-alias \
    --agb-remote-path /home/you/agbridge/agb
```

It prints what it decided — `instance: auto -> hostb01 (read back from hostb-alias)` — and that name
is what you pass to `agb-refresh --instance …` from then on, so it is worth reading. If the machine
will not answer, or its hostname is not a usable name, the install **refuses** rather than falling
back to the default instance; the reasons are in
[`commands.md`](commands.md#--instance-auto--let-the-machine-say).

⚠️ **`--statedir` is required here.** It is the whole point — the new machine's own directory — and
without it the instance would inherit the *first* cluster's path: ssh to the right machine, read the
wrong directory, then create it and report an empty farm for ever. Spell it as it exists **on that
machine**, absolutely: a `~` is expanded by the Mac's shell, and the Mac's home is not the path the
feed will be given.

Required the **first** time only. Re-run this later to pick up new code and the flag can be dropped:
the installer reads the value back out of this instance's own config and prints
`statedir: adopted … from …` under the `instance:` banner. ⚠️ **Unless you also pass `--config`** — an
explicit `--config` may name any file at all, including another instance's, so nothing is adopted
through it and `--statedir` is demanded exactly as on a first install.

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

Or add **`--farm hostb-alias`** and skip the copy entirely — the installer ssh's over and runs that
exact command itself, so Step 3 below is already done when it finishes.

⚠️ **`--feed-host` and `--farm` name the same machine here, so give them the same string.** They are
not the same *kind* of thing, which is why both exist:

| | stored? | who uses it |
|---|---|---|
| `--feed-host hostb-alias` | **yes**, as `feed_host` | the **bridge**, on every connection, for the life of the install |
| `--farm hostb-alias` | **no** — used once, then forgotten | the **installer**, right now, to run the farm side for you |

They differ only on a *shared-disk* cluster, where the farm install runs on every agent host but only
one of them is the feed host. A no-shared-disk instance is one machine, so it is one value typed
twice. Getting the second one wrong is loud, not silent — the Mac half installs correctly and the
run ends with `ssh: Could not resolve hostname …` plus `the farm side failed; the Mac is configured,
the farm is not`. Fix the alias and re-run, or just do Step 3 by hand; nothing needs undoing.

### Step 3 — Install the farm side on the new machine

**Skip this if you used `--farm`** — it has already run.

Otherwise paste the printed command there. It is the ordinary farm install — config plus the four
hooks — pointed at the new machine's own statedir:

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

**Usually already done.** The installer ssh's `--feed-host` once to read its hostname back and writes
the mapping into **that instance's** config for you — it is the `probed:` line in Step 2's output:

```
probed:   hostb-alias is 'hostb01' -> host_hostb01 = hostb-alias
set:      host_hostb01 = hostb-alias
```

Add it by hand only if that line is missing — you passed `--no-probe`, the machine would not answer
(the installer says so and names the flag), or it has been renamed since:

```sh
echo 'host_hostb01 = hostb-alias' >> ~/.config/agbridge/hostb/config
```

It has to be **that instance's** config, not the default one. That is the whole reason a row's
command carries `--config`: clicking a row runs `agb pane`, which resolves the hostname through a
config of its own, and without the flag it would read the *first* instance's table and take you to
the wrong machine — or nowhere. Takes effect on the next click; no restart.

⚠️ Rows minted before the Mac was upgraded to 0.5.0 carry no such flag. `agb-refresh --instance hostb`
re-mints them; nothing updates a live row in place.

### Step 6 — Tell them apart in the sidebar

Nothing in a row says which machine it came from. Give each instance a workspace:

```sh
echo 'workspace = hostb' >> ~/.config/agbridge/hostb/config
```

New rows land there, and one you drag elsewhere stays where you put it.

`row_fields` is per instance in the same way, and can differ between them — dropping `host` is safe
on a machine whose rows are all yours, and a bad idea on one that carries several:

```sh
echo 'row_fields = label,cwd:base,pane' >> ~/.config/agbridge/hostb/config
agb-refresh --instance hostb
```

⚠️ **Put it in the config whose `statedir` holds the rows you are looking at** — which is not
necessarily the default one. This is easy to get wrong and reads as "the feature does not work":
the key lands in a config that is perfectly valid, the bridge that owns your rows never sees it, and
nothing anywhere reports a problem. One command answers it:

```sh
grep -H 'statedir\|row_fields' ~/.config/agbridge/config ~/.config/agbridge/*/config
```

The instance whose `statedir` matches the one your agents write to is the one that renders them.

### Living with more than one

| | |
|---|---|
| **the bare helpers act on ALL of them** | `agb-refresh` and `agb close-done` with no flags sweep every instance. `--instance`/`--label`/`--config`/`--rows` narrow a run to one; `--key` does not, because a key read out of a log does not say which instance minted it |
| **`agb forget-rows` is the exception** | a bare run is **refused** and names `--all`. Not because it closes rows — `agb-refresh` closes every row it forgets too — but because nothing restarts the bridges afterwards, so those rows stay closed until you bounce each one by hand |
| **`agb instances` says what you have** | one row per instance: name, label, config. Run it first when you are not sure what this Mac is carrying |
| **read their first line** | `agb-refresh` prints `instance: hostb -- label com.agbridge.hostb, config …/hostb/config`, once per instance under a sweep; `close-done` and `forget-rows` print the config, map and placements file they opened. It matters most on a **narrowed** run — naming the wrong one still looks exactly like naming the right one |
| **upgrades: install once, refresh each** | `sh install.sh mac …` updates the shared code, but a running bridge keeps the copy it started with. One bare `agb-refresh` now bounces them all |
| **`agb doctor` on the Mac reads the default config only** | it has no `--config`, so it always describes the unnamed instance — and on a Mac where every instance is named, that config does not exist at all. Diagnose an instance from *its* machine (`agb doctor` there) and from its own log |
| **rows are per instance** | the maps never merge; refreshing *one* never moves the other's rows. What changed is that the bare commands visit all of them |
| **so is every Mac-side key** | `workspace`, `row_fields` and the notification switches are read from *that instance's* config. A key in the wrong one is not an error — it is a valid setting on a bridge that renders different rows, and nothing reports it. Match on `statedir`, not on which config you happened to open |
| **around four machines this stops being pleasant** | four launchd jobs, four ssh connections, four logs. It is a deliberate ceiling — see [`design.md`](design.md) §5 for the alternative that was rejected and why |

---

## Troubleshooting

**A config change did nothing** — no error, no warning, the file plainly says what you wrote. Two
causes, in the order to check them.

**1. The bridge has not re-read it.** Every Mac-side key is read *once*, when the bridge starts:

```sh
ls -lT ~/.config/agbridge/config          # when you edited it
ps -o lstart= -p $(pgrep -f 'agb bridge' | head -1)   # when the bridge started
```

If the bridge is older than the file, that is the whole answer — `agb-refresh`. ⚠️ And check the
pid actually changed: with several instances, a bare `agb-refresh` sweeps them all, but
`--instance <name>` bounces exactly one and reports success either way.

**2. It is in the wrong instance's config.** The one that matters is the one whose `statedir` holds
the rows you are watching, which is not necessarily the default:

```sh
grep -H 'statedir\|feed_host' ~/.config/agbridge/config ~/.config/agbridge/*/config
```

A key in the wrong file is not an error. It is a valid setting on a bridge that renders *different*
rows — everything looks right and nothing changes. ⚠️ If two instances name the **same** statedir,
you have two bridges rendering the same agents twice, which is its own problem: see
[`README.md`](../README.md)'s configuration table.

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

**`[enter]` says `open terminal failed: missing or unsuitable terminal: <name>`**, then
`ssh exited 1 -- nothing was attached`. The ssh worked; **tmux** refused, because that host's
terminfo database has no entry for the terminal you are attaching from.

agbridge never sets `TERM` — it runs `ssh -t`, which carries yours across, and tmux will not start
against a name it cannot look up. Modern terminals ship their own terminfo (Ghostty, Kitty, WezTerm
all do), and an ordinary cluster box has none of them. Copy yours over, **from a local shell inside
agterm**:

```sh
infocmp -x "$TERM" | ssh <ssh-target> -- tic -x -
```

⚠️ **From inside agterm, and `"$TERM"` rather than a name you type.** agterm is the terminal that
runs the row, so it is the only place `$TERM` holds the value that will actually reach the far side
— on the Mac that produced this recipe, agterm reports `xterm-ghostty` while the same user's login
shell reports `xterm-kitty`, and copying the second would have installed the wrong entry and
reported success. A row's own `[s]` and `[d]` shells are no good for this: both ssh to the *agent's*
host. Use a session you opened in agterm yourself.

It writes `~/.terminfo/` on the remote — no root, nothing system-wide. `tic` printing
`older tic versions may treat the description field as an alias` is a **warning**; the entry is
written. Confirm and go:

```sh
ssh <ssh-target> "infocmp -1 $TERM" >/dev/null 2>&1 && echo INSTALLED || echo MISSING
```

⚠️ **The redirects are outside the quotes on purpose, and so is `$TERM`.** A farm login shell is
often **tcsh**, which has no `2>&1` and answers `Ambiguous output redirect.` — so anything of the
form `ssh host 'cmd >/dev/null 2>&1'` fails on the shell rather than on the command. Keeping the
redirect local sidesteps the remote shell entirely, and expanding `$TERM` locally is the same rule as
above: it is *your* terminal's name that has to travel, not whatever a non-interactive remote shell
happens to set. (The copy itself is safe either way — `tic -x -` has no redirects to misparse.)

Two caveats. It fixes **the host you copied to** — machine #3 behind a jump host, and every other
agent host on a shared-disk cluster, each need their own copy. And if `infocmp` on the Mac cannot
find the entry either (`couldn't open terminfo file …`), take it from somewhere that has it: the
terminal's own bundle (`TERMINFO=/Applications/Ghostty.app/Contents/Resources/terminfo infocmp -x
xterm-ghostty | …`), or a cluster host where attaching already works —
`ssh <good-host> 'infocmp -x xterm-ghostty' | ssh <new-host> 'tic -x -'`.

The blunt alternative, if `tic` is not available over there, is to stop sending the name — in
`~/.ssh/config`:

```
Host <ssh-target>
    SetEnv TERM=xterm-256color
```

That works everywhere and costs you the terminal's extras (truecolor, styled underlines) inside
those sessions. The terminfo copy is better wherever it is possible.

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

Naming a map is what makes that a **one-map** run: `--rows` narrows it, and alone it still implies
the default config — the one place the old default survives. A bare `agb forget-rows` is refused and
asks for `--all`.

Moving `rows` and `placements` next to the config *before* reinstalling avoids it entirely.
`agb-refresh --config <path>` works on such an install — it needs no instance name.

---

## What next

- [`commands.md`](commands.md) — every command and flag
- [`tmux.md`](tmux.md) — the `bridge:UP`/`bridge:DOWN` status-line segment
- [`design.md`](design.md) — why liveness is proven rather than inferred, and the rest of the design
