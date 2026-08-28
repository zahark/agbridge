# `agb` — command and flag reference

Every flag of every command, with the default the code actually uses. Companion to the command table
in [`../README.md`](../README.md) and to [`design.md`](design.md) §0.

## `agb <command> --help` is not implemented

There is no per-command help. `agb doctor --help` answers
`agb: doctor: unknown option: --help` and exits 1, because every parser here is hand-rolled and
rejects anything it does not know. `agb`, `agb -h`, `agb --help` and `agb help` print the list of
command *names* on stderr and exit 2; that list carries no flags.

This is **deferred, not an oversight.** `argparse` is a ~10 ms import on a hot path measured in
milliseconds (constraint #3), so there are **thirteen** hand-rolled parsers — `feed`, `bridge`,
`close-done`, `forget-rows`, `instances`, `pane`, `doctor`, `prune`, `status-line`, `install-hooks`,
`install-config`, `list`, `rename` (`hook` reads one positional and parses nothing) — spread over
three files, and a `--help` arm would have to be added, and kept correct, in each. `agb` is also
within **2 characters** of the parse-cost cap, which has been raised twice on a measurement and which
several tasks declined to raise at all. **This file is the reference instead.** If `--help` is ever
added, it is added here first.

## `agb` is not directly executable

It has no shebang and is not executable on purpose: a hook is invoked as
`<abs-python> -S -E <path>/agb hook <state>`, and neither a shebang nor `env` can pass `-S -E`.
Every `agb <cmd>` below therefore assumes the wrapper `install.sh` writes into `~/.local/bin`:

```sh
#!/bin/sh
exec /usr/bin/python3 -S -E /path/to/agb "$@"
```

Without it, spell the whole thing: `/usr/bin/python3 -S -E /path/to/agb doctor`.

## Conventions shared by every command

- Every option takes both spellings: `--flag value` and `--flag=value`. An `=` with nothing after it
  is a **missing value**, never an instruction to read the next word: `doctor --statedir= --mac-id x`
  is refused rather than quietly setting the statedir to the string `--mac-id`.
- Boolean flags take no value in either spelling. `--dry-run=`, `--dry-run=1` and `--dry-run=no` are
  all refused with `--dry-run takes no value` — in particular `--dry-run=no` does **not** turn the
  flag off, and does not turn it on either.
- An unknown option, a missing value or an unexpected positional is an error naming the command
  (`prune: unknown option: --forse`), printed on stderr, exit **1**. The one exception is
  `status-line`, which renders its own errors into the tmux bar (see below).
- Commands are always run as `<absolute-python> -S -E <path>/agb <command>`. `agb` has no shebang
  and is not executable on purpose.
- The statedir resolves the same way everywhere, from one helper: `--statedir` (where the command
  has one) → `$AGB_STATEDIR` → the `statedir` config key → `~/.agbridge`. ⚠️ That last default is
  only correct where `$HOME` is the *same* directory on every agent host; `agb bridge` therefore has
  no default at all, because it names a path on the **other** machine. `install-config` is
  the one command that inserts a step — see its entry.

---

## `agb hook <state>` — farm, hot path

```
agb hook active|blocked|completed
```

**No flags at all**, by design: this runs hundreds of times per session and parses nothing it does
not have to. One positional, and `idle` is *not* in it — no hook produces `idle`; only the bridge
emits it, for the `[?]` and `[done]` renderings.

Anything else — a wrong word, a missing argument, a broken statedir, any exception — is written as a
breadcrumb under `err/` and the command still exits **0** and prints **nothing on stdout**. A
non-zero hook is a Claude-visible error, and stdout from a `UserPromptSubmit` hook is injected
straight into the prompt.

## `agb feed <mac-id>` — farm (box #2)

```
agb feed [--poll-interval S] [--iterations N] <mac-id>
```

| Flag | Default | Meaning |
|---|---|---|
| `--poll-interval <seconds>` | `2.0` | how often to poll the statedir and touch `bridge/<mac-id>.beat`. Must be > 0 |
| `--iterations <n>` | unset — run until stdin closes | stop after `n` polls. Must be ≥ 1. There is no `--once`: it is `--iterations 1` |

`<mac-id>` is required and validated (it becomes a path component). There is **no `--statedir`**:
the feed is spawned as `ssh … env AGB_STATEDIR=… <python> -S -E <agb> feed <mac-id>`, so the
environment carries it. A broken statedir is a warning on stderr, not an exit — a feed that died on
a missing directory would look, from the Mac, exactly like a dead ssh.

## `agb bridge` — Mac

```
agb bridge [--config P] [--from-stdin] [--no-agterm] [--feed-host H] [--mac-id M] [--statedir P]
           [--remote-path P] [--remote-python P] [--watchdog S] [--connections N] [--rows P]
           [--workspace N]
```

| Flag | Default | Meaning |
|---|---|---|
| `--config <path>` | `~/.config/agbridge/config` | which config this bridge is. **One flag for a whole instance**: the rows map, the `placements` file and the `host_<name>` table a row's `agb pane` resolves against all live beside it, so a second bridge on the same Mac — a second machine that shares no disk with the first — needs exactly this one path to be right. The launchd plist renders it unconditionally, default install included. See [`design.md`](design.md) §5, *One Mac, several instances* |
| `--from-stdin` | off | read the NDJSON wire from stdin instead of spawning ssh. No feed host or mac-id is needed or looked up; one "connection", then exit. A test seam first, a debugging tool second |
| `--no-agterm` | off | consume and log the wire, touch no rows. This is what makes a transport problem diagnosable separately from a rendering one |
| `--feed-host <target>` | config `feed_host` | ssh target of the farm box. **Required**: with neither, the bridge refuses to start rather than starting and never connecting |
| `--mac-id <id>` | config `mac_id` | names `bridge/<mac-id>.beat`. **Required**, same reason |
| `--statedir <path>` | `$AGB_STATEDIR` → config `statedir`, **no default** | the **farm-side** statedir, sent across in `env AGB_STATEDIR=…`. Never expanded against the Mac's `$HOME`: `~` would resolve locally and then be shipped to a machine where it means something else. **Required** for exactly that reason — `agb`'s own `~/.agbridge` default is right for a process running *on* the farm and wrong for this one, so the bridge asks rather than inventing a path that is correct on the wrong machine |
| `--remote-path <path>` | config `agb_remote_path` → `/opt/agbridge/agb` | absolute path of `agb` on the farm |
| `--remote-python <path>` | config `remote_python` → `/bin/python3` | absolute farm-side interpreter (`ssh host cmd` sources no profile) |
| `--watchdog <seconds>` | `10.0` (five poll intervals) | no line at all — **including a tick** — for this long means the feed is dead: mark every row stale and reconnect. Must be > 0 |
| `--connections <n>` | unset — reconnect for ever | stop after `n` connections. Must be ≥ 1 |
| `--rows <path>` | beside the config — `dirname(--config)/rows`, so `~/.config/agbridge/rows` by default | the persisted `key → agterm row` map, on the Mac. An explicit path still wins over the derivation, which is what keeps it usable as a debugging seam |
| `--workspace <name>` | config `workspace`, else agterm's current one | which agterm workspace new rows are created in (`session new --workspace-name`). A **remembered placement** for a key — written by `forget-rows` before it closed that row — wins over this, so a refresh puts rows back where they were rather than herding them all into one place |

### The blocked banner

On a transition into **`blocked`** the bridge sends `agtermctl notify <body> --title … --target
<row>`: a macOS banner attributed to that row, which also raises the row's unseen badge and jumps to
the pane when clicked. `blocked` is the only state where *you* are the blocker — a permission prompt
sitting unanswered — and the whole premise of a sidebar row is that you are not watching it.

Config `notify_on_blocked`, **on by default**; `0`, `no`, `off` or `false` turns it off. Whether the
Dock icon *bounces* is agterm's setting, not agbridge's (Settings ▸ Notifications: off, once, or
until you focus agterm). Which events are worth announcing is this tool's business; how loudly the
machine interrupts you is the machine's.

⚠️ **Every config key on this page that the bridge reads takes effect only after `agb-refresh`.**
`run_bridge` reads the config **once**, before it connects, and hands that one dict to both
`bridge_settings` and `render_settings` — deliberately, so that a single process can never end up
holding two different configs. The cost is that a live edit is invisible: the file says
`notify_on_completed_after = 20`, the running bridge still uses what it read at startup, and nothing
reports the difference. Found the slow way during the first live test of the finished-turn banner,
where a config edited nine minutes after the bridge started looked like a broken feature for the
better part of an hour.

`host_<name>` and `jump_host` are the exception **when a row is clicked**: `agb pane` is a fresh
process per click and reads the config itself, so those need no restart. The same key read by the
*bridge* (`jump_host` for the feed's ssh) does.

⚠️ **The transition is tracked separately from the painted status**, which is not the obvious
implementation. `--blink` gates on `applied`, the last status emitted — but `_render_stale` paints
every row `idle` on *any* disconnect, including a routine 10 s quiet spell. Gated on `applied`, an
agent that merely stayed blocked would be announced again after every hiccup. The banner's memory
changes only when the **agent's** state does, so it is one banner per block regardless of the
network. A test asserts exactly this, and the wrong gate fails five of them.

### What a row's title shows

`row_fields` picks the fields and their order. Default `label,host,cwd,pane,beat` — exactly the
title agbridge has always rendered, so this is invisible until you set it.

```ini
row_fields = label,cwd:base,pane      # agbridge_dev · agbridge-public · %15
```

| field | renders | note |
|---|---|---|
| `label` | `agbridge_dev` | the tmux session name, or what `agb rename` set. Falls back to the key, then `?` |
| `host` | `buildbox01` | the short hostname. **Constant on a single-host setup**, where it is the largest thing on the line and says nothing |
| `cwd` | `/home/you/project` | `cwd:base` renders `project` alone — the only modifier, and only on `cwd` |
| `pane` | `%15` | ⚠️ two agents in two panes of one tmux session share label, host, cwd **and** tmux. This is the only thing telling their rows apart, and it is last in the default — so it is the first thing agterm clips |
| `beat` | `12m` | how long since the agent last wrote. **Empty unless it is late**, so dropping it saves no width |
| `key` | `a9c35465` | the 8 characters `agb rename` takes. Not in the default |

**An unknown field refuses the whole list**, falls back to the default, and logs why. Blunt on
purpose: dropping only the bad field leaves you with most of what you asked for, and a *missing*
field is exactly what goes unnoticed — whereas "I edited it, restarted, and nothing changed" is
unmissable. ⚠️ The bridge log is the **only** place that reason appears: `agb doctor` validates key
*names*, not values, and it runs on the cluster while this is a Mac-side key.

Whitespace is fine (`label, cwd:base, pane`, and even `cwd: base`); empty items are skipped, so a
trailing comma is harmless. ⚠️ **An inline `#` is not a comment** — `#` starts one only at the
beginning of a line, so `row_fields = label,pane # short` makes `pane # short` an unknown field and
refuses the list.

⚠️ **Dropping `beat` costs more than it looks.** `docs/design.md` calls the age in the title the
compensation for the first invariant: agbridge refuses to turn an age into a *status*, and the
number is what it offers instead. A sidebar without it keeps the refusal and loses the answer.

⚠️ **Like every bridge-side key, it is read once at startup** — `agb-refresh` (or
`agb-refresh --instance <name>`) after editing, or nothing changes and nothing says so. And the
first `[?]` paint after a restart shows titles built with the *previous* list, because the rendered
body is persisted in the rows map; the next update from the agent corrects it.

### The finished-turn banner

On a transition into **`completed`** — the `Stop` hook — the bridge sends
`agtermctl notify "<label> finished after <duration>" --title "agbridge: <host>" --target <row>`,
**but only if the turn ran for at least `notify_on_completed_after` seconds**. Config
`notify_on_completed_after`, **on by default at 300** (5 minutes); `0`, `off`, `no`, `false` or a
negative number turns it off. The number is the switch, so there is no second key.

**The threshold is the feature, not a refinement.** `blocked` is rare and always means a human is
required, but a turn ends *every time an agent answers you*. Ungated this announces the "yes" you
typed three seconds ago — and there is no falling back on "only when I'm away", because agterm
raises the banner and bounces the Dock **even for the row you are currently looking at**. At five
minutes it announces the job you walked away from and nothing else.

The memory is a `pop`: the start time is recorded on the first `active` sighting and removed when
the turn ends, so a second report of the same finished turn finds nothing and stays silent. Three
properties fall out of that shape rather than being enforced — a disconnect cannot restart the clock
(the first sighting wins across a reconnect), a bridge restart announces nothing (a fresh renderer
sees finished agents with no start time at all), and a `blocked` in the middle resets it, so
answering a prompt and finishing seconds later is silent. That block was already announced.

⚠️ **The duration is a heuristic, not a measurement**, in five ways worth knowing before tuning it:

| | |
|---|---|
| **sub-poll floor** | the bridge polls every 2 s, so a turn shorter than one poll is never seen as `active` and can never announce. The right answer for a short turn, reached for the wrong reason |
| **restart floor** | a bridge that restarts mid-turn first sees the agent as `active` *then*, so a three-hour turn interrupted by a restart announces as whatever elapsed since |
| **bridge was down** | a turn that both started **and finished** while the bridge was not running is never announced at all — the feature is quietest exactly when you were away longest. Distinct from a *connection* loss, where the process survives, the start time survives with it, and the banner does fire |
| **outage inflation** | keeping the original start across a 600 s watchdog outage means the announced duration can be mostly outage. Deliberate — the alternative is losing the turn — but it is wall time, not work |
| **replay** | `agb bridge --from-stdin` against a captured feed re-announces every long turn in the recording |

Below 30 s (`BEAT_LATE`) the duration renders as nothing, so the body is just `"<label> finished"` —
`"finished after "` with a dangling preposition is worse than saying nothing about how long it took.

### The new-agent banner

When a key arrives that has no row, the bridge mints one and sends
`agtermctl notify "<label> started in <cwd>" --title "agbridge: <host>" --target <row>`. The
directory is in the body because two agents on one host share everything else — it is what tells you
which piece of work turned up. Config `notify_on_new_row`, **on by default**, separate from
`notify_on_blocked`.

That key also takes a **list of states** instead of on/off, and then only an agent whose
*first-seen* state is one of them is announced — the row is still created either way. The point is
that the first state **is** the launcher: `agb-claude` mints the row before Claude runs, with
`completed`, while a bare `claude` mints on its first hook and so can only arrive `active`. So:

```
notify_on_new_row = completed      # announce `agb-claude` sessions, not every `claude`
```

`idle` is refused — the bridge emits it for `[?]` and `[done]`, both of which are about a row that
already exists, so no new row can arrive in it. An unknown state refuses the **whole** list and
falls back to **on**, logging why: a typo must restore today's behaviour and say so, never switch
notifications off in silence. ⚠️ `agb doctor` validates key *names*, not values, and runs on the
farm — the bridge log is the only place this warning appears.

⚠️ **Rows are minted in bursts, and a burst is silent.** `agb-refresh` forgets every binding and the
bridge re-mints all of them; so does a first install or a lost rows file. Rows created within
`NEW_ROW_QUIET` (3 s) of the first op batch of a connection send nothing — otherwise a nine-row refresh
is nine banners for agents that have been running all day. A heuristic, deliberately: an agent that
genuinely starts inside that window is missed, which is the safe direction to fail. A `[done]` row
reported again is a **return, not an arrival**, and never banners.

### A row agterm has forgotten

`agtermctl` answers `error: no such session: <id>` (exit 1) for a row that no longer exists —
closed by hand, or lost when its pane's command exited, which is what typing `exit` at the
`agb pane` prompt does. On that answer the bridge **marks the row dead**: one line naming the row,
the map and `agb-refresh`, and nothing is ever sent to that row again.

⚠️ **The binding is kept, so the row stays gone.** Forgetting it would mint a replacement within
seconds, and closing a row is how you dismiss it. `agb-refresh` is the deliberate way back.

⚠️ **The match is narrow.** `agtermctl` exits 1 for every failure, so this keys on agterm's own
words. A missing binary, a hung call or a permissions problem keeps being retried — otherwise one
broken `agtermctl` would stop the bridge painting anything at all, silently, which is far worse than
the noise it replaces.

**Two intervals have no flag**, deliberately — neither is a tuning knob, and a flag would invite
setting them wrong:

| Constant | Value | What it does |
|---|---|---|
| `REASSERT_INTERVAL` | 30 s | re-sends **every** row's status, changed or not. agterm resets a session's status when the session's command starts, so attaching to a row clears its glyph; without this the bridge would not repaint, because the status it last *sent* is still correct. Ticks only, never while stale, never blinks |
| `NOTIFY_INTERVAL` | 300 s | at most one desktop banner per 5 minutes, across reconnect cycles. The stderr `NOTICE` line is not limited |

## `agb close-done` — Mac

```
agb close-done [--config PATH] [--rows PATH] [--launch-agents DIR] [--dry-run]
```

| Flag | Default | Meaning |
|---|---|---|
| `--config <path>` | **every instance** — see below | which instance's map to work on; `--rows` is derived from its directory. Given, it **narrows** the run to that one map |
| `--rows <path>` | derived from `--config` | the row map to read and rewrite. An explicit path wins, and also narrows |
| `--launch-agents <dir>` | `~/Library/LaunchAgents` | where to look for instances. Says where to **look**, not which one to act on, so it does not narrow |
| `--dry-run` | off | print what would be closed, close nothing, rewrite nothing |

⚠️ **With no map flag this acts on EVERY instance on this Mac**, one banner apiece. It used to mean
the unnamed one: with two bridges up it reclaimed the *default* instance's rows and reported success,
while the other sidebar kept growing a row per agent with nothing to say so. It is safe to default to
all here **by construction** — a `[done]` row is one whose agent is already gone, so reclaiming it in
an instance you did not have in mind costs nothing you can lose. (`agb forget-rows` is the command
where that is not true; see its refusal below.)

Instances are discovered through `agb instances`, so both sweeps agree about which plists are ours.
⚠️ **A Mac with a config and no launchd job prints a note and acts on the default map** — unchanged,
and the commonest shape there is. ⚠️ **One instance whose plist cannot be read stops the whole
sweep**; a plist that is not ours and cannot be read is ignored, exactly as `agb instances --labels`
ignores it.

Only `[done]` entries are touched; a bound row is never closed. A row is forgotten only if
`agtermctl` reports it closed — otherwise it stays in the map and is printed as "close by hand".

**It says which map it is acting on, on every run**, before anything else including the `--dry-run`
exit:

```
close-done: config /Users/you/.config/agbridge/hostb/config; rows /Users/you/.config/agbridge/hostb/rows
```

Unconditional, and one line per instance under a sweep — which is also what tells a sweep apart from
the no-instances fall-through. It matters most on a **narrowed** run: pass `--config` while meaning a
different instance and this reclaims that one's rows and reports success in exactly the words you
were expecting.

## `agb instances` — Mac

```
agb instances [--labels | --probe | --plist <path> --arg <flag>] [--launch-agents <dir>]
```

Says which agbridge instances this Mac has, by reading `~/Library/LaunchAgents`. It is the single
answer both sweeps ask — `agb-refresh` from POSIX sh, `close-done`/`forget-rows` in process — so the
two cannot disagree about which plists are ours.

| Mode | Prints | Exit |
|---|---|---|
| *(none)* | one row per instance: `name  label  config`, padded into columns. The name is `agb-refresh`'s banner name for that label — `hostb` for `com.agbridge.hostb`, `(default)` for `com.agbridge` itself, and the **label** for anything outside that space, since such an install has no other name. The config is empty for a job whose argv carries none, and its line then stops after the label rather than ending in the padding | 0 |
| `--labels` | one label per line — what the sweep iterates | see below |
| `--plist <path> --arg <flag>` | that flag's value out of that plist's `agb bridge` argv | 0 answered, **2** this file says nothing, **3** `agb_mac` is not beside `agb`, anything else the reader failed |
| `--probe` | the literal `instances-ok` | 0 |
| `--launch-agents <dir>` | — | overrides `~/Library/LaunchAgents` in every mode |

**`--probe` is a known-answer probe, and its answer is load-bearing.** `agb-refresh` runs it once
before reading any plist. A status alone cannot see `--python /bin/echo` (exits 0, prints its own
arguments), and `agb` answers an **unknown command** with exit 2 and empty stdout — byte-identical to
`--arg` saying "this plist names no config". So an `agb` predating this command would make every
plist silent and the run would succeed on the wrong instance. Comparing stdout to `instances-ok` is
what makes exit 2 unambiguous afterwards.

⚠️ **`--labels` has its own status contract, and the split is deliberate.** A **missing**
LaunchAgents directory is **exit 0 with empty output** — "there are no instances", the ordinary Mac.
**Every other errno is exit 1 with the reason on stderr, and fatal at the caller.** Reading the
second as the first is "I could not answer" collapsing into "the answer is nothing": a Mac with a
momentarily unreadable directory would sweep nothing, fall back to the default job and report
success.

⚠️ **What counts as an instance is a wider question than the `com.agbridge` guard `agb-refresh` uses
to pick a label**, and both are right. That one is a *claimant* rule and is correctly narrow — a
third-party LaunchAgent must not be able to stand for agbridge's default config. This one is asking
who to sweep, and `install.sh --label <name>` puts no shape rule on a label, so a custom-label
install is a real one:

> A plist is an agbridge instance iff **its label is in the `com.agbridge` space**, **or** its
> `ProgramArguments` contains the command word `bridge` immediately after an element whose
> **basename is `agb`** — any tree, not `realpath`-equal to this one, since a plist naming an `agb`
> elsewhere is deliberately supported.

Over-listing costs a bounded refresh of something that turns out not to be ours; under-listing is an
instance nobody sweeps. The looser half is the safe direction.

`--plist … --arg` is the plist reader `agb-refresh`'s `plist_arg` calls; the rules it applies to an
argv — only `ProgramArguments`, only after the `bridge` command word, and `agb bridge`'s own parser
rather than an imitation of it — are documented under [`agb-refresh`](#agb-refresh--mac-a-convenience).

## `agb pane <key>` — Mac

```
agb pane <key> --host <host> [--tmux <session>] [--pane %N] [--cwd <path>] [--jump <host>]
              [--config <path>]
```

This is the row's own command, built by the bridge; the flags mirror `agb_mac.pane_argv` word for
word, and the two are tested against each other.

| Flag | Default | Meaning |
|---|---|---|
| `<key>` (positional) | — | **required**, and validated as a minted key |
| `--host <hostname>` | — | **required**. The Mac cannot read the shared statedir, so the identity has to arrive on the command line. It is a *hostname*; `host_<name>` in the config maps it to an ssh target, and without such a key the hostname is used as the target |
| `--tmux <session>` | none | the tmux session to attach to. **Without it there is no attach at all**: `pane` prints the identity, says so, and exits 0 without prompting |
| `--pane %N` | none | tmux pane id, `%` plus digits. Accepted without `--tmux` (that record exists), and then rendered in the identity like any other field |
| `--cwd <path>` | none | the agent's working directory, used by `[s]` and `[d]` below so the shell opens there. Optional: rows created before it existed carry none, and the shell then lands wherever ssh does |
| `--jump <host>` | config `jump_host` | ssh jump host for machine #3. The bridge's hint wins over the config, and either is dropped when it names the resolved target or the `--host` value itself — hopping through the box you are already going to |
| `--config <path>` | `~/.config/agbridge/config` | which config `host_<name>` and `jump_host` are read from. **Emitted by the bridge only for a non-default instance**, so a default install's row commands are unchanged and rows minted before this flag existed keep working — they carry none and fall back to the default, which is correct for them |

⚠️ **`--config` is the difference between real isolation and decorative isolation**, and its absence
fails silently. The Mac cannot read the shared statedir, so `--host` arrives as a *hostname* and is
turned into an ssh target by this command's **own** config read. With two instances on one Mac that
read would hit the default config, so instance B's rows would resolve their target from instance A's
`host_<name>` table: the wrong machine, or `Could not resolve hostname`, with nothing anywhere
reporting a mismatch. See [`design.md`](design.md) §5, *One Mac, several instances*.

**A config that cannot be read prints a `WARNING` line into the row and carries on** — never a
traceback, because agterm closes a session when its command exits and that would take a live
agent's row off the sidebar over a file mode. The line names the errno and the file, because the
fallback (`{}`, so the bare hostname and no `jump_host`) is otherwise indistinguishable from a
healthy resolve. It is keyed on the read **failing**, so it covers the default config of a row that
carries no `--config` at all; a config that is merely *absent* is silent, which is the ordinary
state of an install with no config file.

### The prompt

```
[enter] attach   [s] split   [d] drawer   [q] quit >
```

| Key | Effect |
|---|---|
| **enter** | `ssh -t <target> 'tmux select-window -t %N ; tmux select-pane -t %N ; exec tmux attach-session -t <session>'`. Runs in a **loop**, not `exec`: detaching returns to this prompt instead of closing the row's terminal. A non-zero exit is reported and prompted again rather than being fatal |
| **s** / `shell` / `split` | opens agterm's split pane **beside** this one and starts `ssh -t [-J <jump>] <target> 'cd <cwd> && exec $SHELL -l'` in it. Both panes belong to the same row |
| **d** / `drawer` / `scratch` | the same shell in agterm's scratch **drawer**, which overlays this pane rather than taking width from it. Hidden, it stays alive; `[d]` brings it back |
| **q** / `quit` / `exit`, or EOF | leaves without attaching. Changes nothing about the agent |

Both keys are two `agtermctl` calls in a fixed order — `session split on` then
`session type --pane right`, or `session scratch on` then `session type --pane scratch`, always
`--target active` — because `--pane right` is an error when there is no split yet. `on` rather than
`toggle`, since pressing the key twice would otherwise close the pane. `--target active` needs no
row id: this command is running *inside* the row's own session. A missing or failing `agtermctl`
costs the row its pane and nothing else.

**`shell` stays a synonym for the split**, not the drawer: the label changed, the word did not.
Pressing either key twice types a second ssh line into the shell already there, nesting an ssh that
`exit` undoes — which is why `session scratch`'s `--command` is *not* used, despite being a tidier
single call: it *"respawns the scratch if one is already open"*, so a second press would destroy a
shell in use.

Values are rejected at parse time if empty or if they carry surrounding whitespace or a newline;
the ssh target and the jump host are checked again before the attach, against a character whitelist
**and a leading `-`** — every character of `-oProxyCommand=…` is otherwise acceptable, and ssh would
read that word as an option rather than as a host.

## `agb doctor` — farm

```
agb doctor [--statedir P] [--mac-id ID] [--quiet-after S] [--tail N]
```

| Flag | Default | Meaning |
|---|---|---|
| `--statedir <path>` | `$AGB_STATEDIR` → config → `~/.agbridge` | which statedir to probe |
| `--mac-id <id>` | config `mac_id` | which `bridge/<mac-id>.beat` must exist. Without one, every beat found is still reported with its age — but a *missing* beat cannot be reported, which is how a mac-id typo hides |
| `--quiet-after <seconds>` | `900.0` (15 min) | how old `sweep/<host>.marker` must be before that host's entries are listed as **unadjudicable**. A host with no marker at all is always quiet. Same default and meaning as `prune --quiet-after` |
| `--tail <n>` | `3` | lines of each `err/<host>.<key>.log` breadcrumb to print, over at most 20 logs |

Exit **1** only on a failed probe. Warnings — unadjudicable entries, a stale bridge beat — exit 0:
an exit status that cries wolf is one nobody reads.

⚠️ **There is no `--config`** (design.md §5, limitation 3). This runs on the farm, where the config
is always `~/.config/agbridge/config` — a farm host belongs to exactly one Mac-side instance, so
there is nothing to choose between. What that costs you: on a Mac with several instances, `doctor`
run against the *wrong* cluster's statedir reports that cluster's truth. `--statedir` and `--mac-id`
are the two values to check when the answer looks wrong.

## `agb list` — farm

```
agb list [--host <host>] [--statedir <path>]
```

Every session the statedir can account for, on **every** host — which is what the sidebar shows,
so it is what this shows.

```
KEY       LABEL             STATE       BEAT    PANE   HOST
6926895e  deploy-review     active      9.5 h   %6     buildbox03  (not this host)
523760c2  api-refactor      completed   16 s    %0     buildbox01
b7ed51ad  my-agent          active      4 s     %2     buildbox01
```

The key is truncated to 8 characters on purpose: nobody retypes 16, and every command taking
`<key>` takes a **unique prefix**, so what this prints can be pasted straight into the next one.
Foreign hosts are marked, because whether this machine can act on an entry is a different question
from whether it can see it — `rename` and `prune` both depend on the answer.

Keys come from each host's marker **content**, never from `readdir(sessions/<host>/)`: that listing
can be served from the attribute cache for up to `acdirmax` seconds and would hide a key created a
minute ago (constraint #5). An entry whose `.state` cannot be read is **not listed** — a short or
malformed read is no information, and inventing a row from it would be a claim nothing supports.

| Flag | Default | Meaning |
|---|---|---|
| `--host <host>` | every host | show one host's sessions |
| `--statedir <path>` | `$AGB_STATEDIR` → config → `~/.agbridge` | which statedir |

## `agb rename <key> <label>` — farm

```
agb rename <label>                 # the row this terminal is in
agb rename <key> <label>           # any row; <key> may be a unique prefix
agb rename <key> <label> [--host <host>] [--statedir <path>]
```

Sets the **label** a row is titled from (`label · host · cwd · pane` by default; `row_fields`
chooses the fields).

`agb list` prints the keys; `agb rename` with no arguments prints it too, alongside the usage.
Keys are 16 random hex characters, so there are two ways not to type one: **omit it** and the row
this terminal's agent owns is renamed (resolved from the tmux/pid anchor, and it will never *mint* a
key — naming a thing must not create it), or give a **unique prefix**, which is refused rather than
guessed if it matches more than one. `agb doctor` lists the keys this host can see.

⚠️ A lone argument that looks like a key (8+ hex characters) is **refused**, because
`agb rename b7ed51ad0b5d2952` reads as "rename this key" but would otherwise parse as "label the
current row `b7ed51ad0b5d2952`" — the wrong row, named something nobody meant, silently.

| Flag | Default | Meaning |
|---|---|---|
| `<key>` (positional) | this terminal's own row | a whole key or a unique prefix |
| `<label>` (positional) | — | **required**. 1–40 characters, no control characters, no leading or trailing space, and not containing ` · ` — a label carrying the title's own separator would read as two fields |
| `--host <host>` | this host | whose entry to rename |
| `--statedir <path>` | `$AGB_STATEDIR` → config → `~/.agbridge` | which statedir |

**Why the label and not the row.** The bridge repaints the title from the record on every update, so
an `agtermctl session rename` is overwritten within seconds. The record is the durable thing:
`build_record` is `ident.label or old.get("label")`, and `ident.label` is populated only at mint
time, so every later transition carries this forward.

⚠️ **Publishing costs a beat, so it is only taken when it can be proven.** The feed re-reads the
record only when `seq` moves, and `seq` lives in `.state` — whose mtime *is* the beat. Rewriting it
to publish a label would assert the agent is alive. So `.state` is touched only when `kill(pid,0)`
answers on this host and the `starttime` matches; otherwise the record alone is updated and the row
picks the new label up at the agent's next state change. Either way the command says which happened,
rather than looking like a no-op.

Renaming the **tmux session** does not do this, and breaks `agb pane`: the session name was resolved
once at mint time, so the row would keep trying to attach to a name that no longer exists.

## `agb prune` — farm, the only destructive command

```
agb prune [--statedir P] [--quiet-after S] [--key <host>/<key>]… [--yes] [--dry-run] [--via-ssh H]
```

| Flag | Default | Meaning |
|---|---|---|
| `--statedir <path>` | `$AGB_STATEDIR` → config → `~/.agbridge` | which statedir to work in |
| `--quiet-after <seconds>` | `900.0` (15 min) | the same age heuristic `doctor` uses, from the same constant — `prune` consumes `doctor`'s list rather than deriving a second one |
| `--key <host>/<key>` | none — the quiet-host list is used instead | name one entry explicitly. Repeatable. Named entries are read by name, so this also reaches entries the heuristic would not list |
| `--yes` | off | skip the per-entry prompt. **Refused unless at least one `--key` was given**: consent has to be about something specific, never about a list built from an age heuristic |
| `--dry-run` | off | prompt as usual, then report `n of m would be removed` and remove nothing. Takes precedence over `--via-ssh` |
| `--via-ssh <host>` | off | re-issue the confirmed entries **on the owning host** instead of removing them here (below) |

There is **no `--force`.** It is not merely absent: passing it prints why, because the next person to
want one will type it before reading anything. Two further refusals are unconditional and are not
reachable from any flag — an entry whose pid is provably alive on this host is printed as `KEPT`
without the prompt being offered at all (`--yes` included), and a foreign host's marker is rewritten
by subtracting the pruned keys from its own last-read content, never from a `readdir`.

### `--via-ssh <host>` — turning the heuristic into a proof

The entries `prune` offers are *unadjudicable*: they live on a host this machine cannot speak for, so
`kill(pid, 0)` here says nothing about a pid over there. Age is the only signal available, and age is
not death — a `blocked` agent waiting for your input beats nothing at all.

`--via-ssh` closes that gap. After the local confirmations, it runs **this same command on the owning
host**:

```
ssh [-J <jump>] <target> <remote-python> -S -E <agb> prune --statedir <sd> \
    --key <host>/<key> [--key …] --yes
```

Over there `kill(pid, 0)` is asked in the right pid namespace, so an entry whose agent is still
running is **refused by the remote side** even though this side had no way to know. design.md §2
calls it the terminal answer for exactly that reason.

Four values shape that command line, and all four default to "the paths I am running from" rather
than to a config lookup — box #2 and machine #3 see the same NFS `agb`, and the interpreter is
already required to exist at the same absolute path on every host that runs hooks:

| Value | Default | Config key that overrides it |
|---|---|---|
| ssh target | the hostname itself | `host_<name> = <ssh-target>` |
| jump host | none | `jump_host`, **dropped when it names the target or this host** (in either spelling: hostname or ssh target) — hopping through the box you are already on, or already going to, is at best wasted and at worst a route that does not exist. `install.sh` copies the Mac's `--jump-host` into the farm's config, so this is the normal case there. Same rule as `pane` |
| interpreter | `sys.executable` — the one running the local `prune` | `remote_python` |
| `agb` path | the `agb` beside the running one | `agb_remote_path` |

⚠️ **Those config keys are read from `~/.config/agbridge/config` and there is no `--config`**
(design.md §5, limitation 3). `prune --via-ssh` runs on the farm, where one host belongs to one
Mac-side instance, so a per-instance config would have nothing to select between. On a Mac with two
instances, `host_<name>`/`jump_host` come from the default config only.

Anything you confirmed that belongs to a *different* host is **not** removed and is named in the
output, with the `--via-ssh <that host>` re-run to make: silently dropping it would mean the operator
answered "yes", watched the command succeed, and nothing happened.

## `agb status-line` — farm, every tmux `status-interval`

```
agb status-line [--statedir P] [--mac-id ID]
```

| Flag | Default | Meaning |
|---|---|---|
| `--statedir <path>` | `$AGB_STATEDIR` → config → `~/.agbridge` | passing it (or `$AGB_STATEDIR` inline, as `tmux.md` recommends) means the config is not read to find the statedir |
| `--mac-id <id>` | config `mac_id`, then the newest `bridge/*.beat` | which beat to read. A configured id is never second-guessed: a missing beat reads `bridge:DOWN never`, not "some other Mac is fresh" |

⚠️ **No `--config` here either** (design.md §5, limitation 3): the config it falls back to is always
`~/.config/agbridge/config`. That is right on the farm — one host, one instance — and it is why the
Mac-side instances **share a `mac_id`** rather than minting one each: every cluster's farm hosts then
watch the same `bridge/<mac-id>.beat` name inside their own statedir.

Give **both** and the config file is not opened at all — one avoided NFS `open()` per tick, forever.

There is no `--host`: the beat is written by the Mac, and which host reads it changes nothing but the
lag. This is the one command that catches its own errors — a bad option renders as
`bridge:ERR status-line: unknown option: …` **in the bar**, exit 1, because a blank segment is
indistinguishable from tmux not running it at all. Exit 0 when it could answer, 1 when it could not;
one line either way. See [`tmux.md`](tmux.md).

## `agb install-hooks` — farm, once per host

```
agb install-hooks [--settings P] [--statedir P] [--python P] [--agb P] [--dry-run]
```

| Flag | Default | Meaning |
|---|---|---|
| `--settings <path>` | `~/.claude/settings.json` | the file to merge into. Also the seam that keeps the test suite off the developer's live settings across a subprocess boundary |
| `--statedir <path>` | `$AGB_STATEDIR` → config → `~/.agbridge` | baked into the hook command as `AGB_STATEDIR=…`, which is what lets the hot path skip the config read. Must be absolute |
| `--python <path>` | `sys.executable` — the interpreter running this command | baked into the hook command. Must be absolute, existing and executable, and must resolve at the *same* absolute path on every host that runs hooks (printed as a note, never guessed at) |
| `--agb <path>` | the `agb` beside the running one | which `agb` the hook command names. Must be absolute |
| `--dry-run` | off | print the whole report — what was verified, removed, kept — and write nothing |

Paths are `expanduser`'d. Before writing anything it **runs** `<python> -S -E <agb> version` and
requires `agb <VERSION>` back: a hook command is installed only once it has been executed, because a
broken one fails before `agb` starts and leaves no breadcrumb at all. The previous
`settings.json` is copied to `settings.json.agb.bak` and the `backup:` line names it. A run that
would change nothing writes neither.

## `agb install-config` — both sides, once per host

```
agb install-config [--config P] [--statedir P] [--mac-id ID] [--feed-host H] [--agb-remote-path P]
                   [--remote-python P] [--jump-host H] [--host <name>=<target>]…
                   [--generate-mac-id] [--print-mac-id] [--print-statedir] [--dry-run]
```

| Flag | Default | Meaning |
|---|---|---|
| `--config <path>` | `~/.config/agbridge/config` | the file to merge into. **The only path `expanduser`'d** — the others name things on the farm, where `~` means something else |
| `--statedir <path>` | the value already in the file, else `$AGB_STATEDIR` → config → `~/.agbridge` | the file's own value comes first on purpose, so a re-install from a shell that happens to carry `$AGB_STATEDIR` cannot silently repoint an installed configuration. Must be absolute |
| `--mac-id <id>` | the value already in the file (**kept, never regenerated**) | setting a *different* one is allowed and reported as a loud `warning:` — it invalidates every other machine's config |
| `--feed-host <target>` | not written | config `feed_host` |
| `--agb-remote-path <path>` | not written | config `agb_remote_path`. Must be absolute |
| `--remote-python <path>` | not written | config `remote_python`. Must be absolute |
| `--jump-host <target>` | not written | config `jump_host` |
| `--host <name>=<target>` | none | writes `host_<name> = <target>`. Repeatable |
| `--generate-mac-id` | off | mint one (`<short-host>-<16 hex>`) **only if the file has none**. The Mac's flag: the farm refuses to invent an id, because a second one would name a beat file nothing writes and `status-line` would read `bridge:DOWN` for ever |
| `--print-mac-id` | off | put the mac-id alone on **stdout** and the report on stderr, so `install.sh` can read it back without re-implementing `key = value` in shell |
| `--print-statedir` | off | put **that file's own** `statedir` alone on stdout, and **write nothing at all**. Exits **4** when the file carries none (or is not there) and **1** when it could not be read — see below, the difference is the point |
| `--dry-run` | off | print the full report and write nothing |

With no `--mac-id`, no id in the file and no `--generate-mac-id`, the command **fails** and explains
why. The merge preserves comments, layout and keys this tool does not know; the text it is about to
write is re-parsed and refused if it would not read back as the values reported. The previous file is
copied to `config.agb.bak`, and a run that would change nothing writes neither.

### `--print-statedir` — a read-only query, and why that is a rule rather than an accident

`install.sh mac` reads it to adopt an existing instance's statedir (below), for the same reason the
mac-id adoption uses `--print-mac-id`: a second reader of the `key = value` format is a second reader
that drifts from the first.

It is **not** `--print-mac-id` with a different noun, and four properties keep it honest:

| | why |
|---|---|
| it prints the file's **own** value, never the fallback | `--statedir`'s ordinary resolution ends at the *default-path* config. Reporting that for an instance file that carries nothing is the exact answer `install.sh` must not get — it names another machine's directory, on a disk this instance's farm cannot see |
| the value leaves as **UTF-8 bytes**, so the locale cannot touch it | `-E` does not touch `LC_ALL`, and a statedir is a filesystem path with no ASCII guarantee. Through a text stream a non-ASCII one exited **1** under `LC_ALL=C` — the status meaning *I could not read the file*, for a file read perfectly — and exited **0** under ISO-8859-1 with the path *transcoded*, naming nowhere. Same rule, and same reason, as `agb instances --plist … --arg`. (`--print-mac-id` needs no such care: `valid_mac_id` refuses anything outside an ASCII alphabet.) The **prose** on stderr stays text on purpose — it carries no value a caller parses, and stderr's `backslashreplace` means it can neither raise nor change the status |
| **"carries none" has its own exit status, and is not "I could not read it"** | it is handled the instant the config has been parsed and **returns** there, so no later failure can be mistaken for that answer. Run any further on and a file with a `statedir` but no `mac_id` would raise about the *mac-id*, and the installer would then demand `--statedir` for a file that carries one |
| it **writes nothing**, with or without `--dry-run` | the query returns before the merge and the write. Bolted onto the tail instead, a statedir-less config would be *rewritten with the default config's statedir* on the way to the error — the failure the flag exists to prevent, caused by the flag |

**It refuses company.** Only `--config` and `--dry-run` may accompany it — `--config` because it names
*which* file to read, `--dry-run` because it is a no-op for a query and refusing the obvious first
guess would be unkind. Every other option asks for a write this command will not perform, and
"you asked me to write and I silently did not" is the class of failure this project keeps removing.
`--print-mac-id` and `--print-statedir` together are refused too: both own stdout, and neither answer
says which one it is.

```console
$ agb install-config --config ~/.config/agbridge/hostb/config --print-statedir
/home/you/.agbridge
```

**Three statuses, and the third one is why there are three.**

| exit | means | what `install.sh` does |
|---|---|---|
| `0` | the value is on stdout | adopts it |
| `4` | that file carries none of its own — including *it is not there*, which is what a first install looks like | falls through to requiring `--statedir` |
| `1` | **it could not be read**: unreadable, not UTF-8, or an option this `agb` does not know | **fatal**, naming the file and the query |

The last row is a distinction, not a formality. With one non-zero status for everything,
`install.sh` reported a config it could not open as *carries none to adopt* and sent the operator
after `--statedir` — a flag that was not the problem, and one that would have installed the instance
against a config nothing there can read. That is the same *"'I could not answer' is not 'the answer
is nothing'"* that `agb-refresh`'s four-status plist reader exists for. `4` rather than `2` or `3`
because `agb` already answers an unknown command with `2` and a known-but-unbuilt one with `3`;
`install.sh` spells the number itself (it cannot import `agb_ops`) and a test compares the two.

## `install.sh mac --instance <name>` — Mac, every install

```
sh install.sh mac --instance <name> --statedir <path> --feed-host <target> \
                  --agb-remote-path <farm path> [the usual options]
```

⚠️ **`--instance` is REQUIRED, and a nameless `install.sh mac` is refused.** Every Mac-side instance
is named, so `agb instances` can say what exists and no command has to guess which one you meant.
Pass `--instance <name>`, or `--instance auto` to name it after `--feed-host`.

The refusal fires at the top of the `mac` role — **before any file is written and before the probe
ssh** — so nothing is installed, nothing is copied, and no name can be invented from a machine that
was never asked. It is a hard error rather than a warning because a warning on a *first* install gets
ignored, and the asymmetry it warned about then becomes permanent on that Mac.

**`install.sh farm` is unaffected and takes no `--instance` at all.** A farm host has exactly one
identity: `agb hook` and `agb status-line` resolve `agb.config_path()` — the default path — on every
invocation, so a named farm config is a file nothing opens. Both halves of that asymmetry come from
the one fact.

A **machine that shares no disk with the first** needs its own statedir, so it needs its own feed and
its own bridge. `--instance` is that install: an independent launchd job whose rows appear in the
same agterm sidebar. It is **sugar over three flags that already existed**, and passing any of them
explicitly wins over the sugar:

| | without `--instance` | `--instance hostb` |
|---|---|---|
| `--config` | *refused* — was `~/.config/agbridge/config` | `~/.config/agbridge/hostb/config` |
| `--label` | *refused* — was `com.agbridge` | `com.agbridge.hostb` |
| `--log-dir` | *refused* — was `~/Library/Logs/agbridge` | `~/Library/Logs/agbridge/hostb` |

⚠️ **The left column is what a pre-0.6.0 Mac still has on disk, not something you can still create.**
Refusing to *create* a nameless instance does not remove the ones already installed: a plist on disk
outlives the installer that wrote it, so `agb instances` still lists such a job as `(default)`,
`agb-refresh` still claims and sweeps it, and every legacy reader stays. What changed is
**creatability**, not reachability.

⚠️ And creatability is not airtight: `install.sh mac --instance hostb --config ~/.config/agbridge/config`
still writes the unnamed config, because `--instance` only *defaults* `--config` rather than owning
it. Refusing that would forbid a legitimate shape — adopting an existing file under a name — to
prevent a deliberate act, so it is documented rather than closed. It is also exactly why the statedir
adoption below refuses to fire through that route.

Everything else the Mac side owns — the `rows` bijection, the `placements` file, the `host_<name>`
table — lives beside that config and therefore moves with it. There is no fourth path to pass and
nothing that can drift out of step.

**`--dest` and `--bin-dir` stay shared on purpose**: the three files are identical per instance, so
this is one code install and N configurations, and an upgrade is one `install.sh mac`, not one per
machine. The `mac_id` is **adopted** rather than minted — from **this instance's own config first**,
and only then from the default one. It names *this Mac*, not this connection, and each cluster's
`bridge/<mac-id>.beat` lives in its own statedir, so the same id in both places is the truth rather
than a collision.

Own-config-first is not a detail: the adoption fires on **every** `--instance` run without
`--mac-id`, i.e. on a routine upgrade, and `--mac-id` beats an existing value — so probing only the
default config would *replace* an id this instance had already published. Every farm host of that
cluster still watches `bridge/<old-id>.beat`, so `agb status-line` reads `bridge:DOWN` for ever and
`agb doctor` reports no beat, out of an install that changed nothing anybody asked to change. If
neither config has one, a fresh id is minted.

### `--statedir` is required once, then adopted

A **new** instance must be told its statedir. A **re-install** of one that already exists reads the
value back out of that instance's own config, so a routine upgrade is the original command minus the
flag:

```console
$ sh install.sh mac --instance hostb --feed-host hostb-alias --agb-remote-path /opt/agbridge/agb
agb install (mac) -- from /Users/you/agbridge
python:   /usr/bin/python3
instance: hostb -- label com.agbridge.hostb, config /Users/you/.config/agbridge/hostb/config
statedir: adopted /home/you/.agbridge from /Users/you/.config/agbridge/hostb/config
```

The `statedir:` line sits directly under the `instance:` banner on purpose — the two read as one
statement about one instance, and a statedir named before any instance has been printed names
nothing. It is read back through `agb install-config --print-statedir`, never by parsing
`key = value` in shell, for the reason `install.sh` gives at its own parse site: a second reader of
this format is a second reader that drifts.

⚠️ **It adopts only from the config `--instance` DERIVED, never when `--config` was given.** With an
explicit `--config`, `--statedir` is still required exactly as before. This is the one rule that
looks like an inconsistency and is not:

```sh
install.sh mac --instance hostb --config ~/.config/agbridge/config \
               --feed-host boxb --agb-remote-path /opt/agbridge/agb    # no --statedir
#   still refused, and deliberately
```

`--config` may name *any* file — the default one, another instance's — and adopting a statedir out of
it is precisely the failure the requirement exists to prevent: a bridge to `boxb` reading the other
cluster's directory, arriving through the one route the guard cannot see. `$config` is "this
instance's own" by convention only, never by construction, and the flag having been typed is the only
surviving evidence of which of the two it is. A small ergonomic loss for a shape nobody uses, and the
difference between a guard and a hole.

⚠️ **It is deliberately NOT a mirror of the `mac_id` adoption above.** That one falls back to the
default config on purpose, because one Mac has one identity and sharing an id across instances is the
truth. Sharing a *statedir* is the exact failure being refused — two clusters share no disk. So the
statedir adoption has **one** candidate and never a loop.

Four refusals, each because the alternative is silent:

| Refused | Why |
|---|---|
| `install.sh mac` with no `--instance` | the nameless instance it used to create is the asymmetry every other Mac-side command spent a release removing; nothing is written and the probe is never asked |
| a **new** instance without `--statedir` | it would fall back to the **default** config's statedir: ssh to the right machine, read the wrong directory, then create it and report an empty farm for ever. A re-install adopts it instead (above); an explicit `--config` still requires it. The refusal has **three wordings** — a new instance, a config that carries no statedir, and `--config` having been typed — because the reason is what says whether to pass the flag, fix the file or drop the flag |
| `install.sh farm --instance <name>` | nothing on the farm reads a per-instance config — `agb hook` and `agb status-line` resolve `~/.config/agbridge/config` and nothing else — so it would write a config no one opens and report success |
| a name with `/`, `.`, a leading `-`, or empty | it becomes a launchd label component, a plist *filename*, a log directory **and** a config directory. Letters, digits, `-` and `_` only; the general `shell_safe` check permits `.` and `/`, so `--instance ../../evil` would otherwise pass |

### `--instance auto` — let the machine say

```
sh install.sh mac --instance auto --statedir <path> --feed-host <target> …
```

The installer already ssh's `--feed-host` for its hostname, to derive the `host_<name>` mapping a
row needs to be clickable. `auto` spends that same answer on the instance name, so the config, the
label and the log directory all follow what the machine calls itself:

```
instance: auto -> hostb01 (read back from hostb-alias)
instance: hostb01 -- label com.agbridge.hostb01, config …/agbridge/hostb01/config
```

**One ssh, two readers.** The probe is asked once and the `host_<name>` mapping finds it already
answered — so there is no second round trip, and no way for a box that renamed itself between two
calls to produce a config whose mapping does not match its own directory.

⚠️ **It is a word you type, and it can never be the default.** If an absent `--instance` meant "name
it after whatever the feed host calls itself", every upgrade of an existing install would mint a
*new* instance beside it — new config, new launchd job, new rows map, and every row duplicated in the
sidebar. An absent `--instance` is **refused** instead: no name is invented, and the probe is never
even asked.

> ⚠️ **Withdrawn, and kept here because a reason that is deleted gets re-proposed.** This used to
> continue *"re-running `install.sh mac` with the original flags is how you pick up new code, so an
> absent `--instance` has to keep meaning the default instance."* The premise stands and the
> conclusion no longer does — the upgrade path is now re-running it **with the same `--instance`**,
> which is a word you already typed once. What survives untouched is the OPT-IN half above: auto
> naming is something you ask for, never something a bare install falls into.

⚠️ **Every failure is a refusal, and nothing is written.** Falling back to the default instance is
precisely the accident this avoids: that run would rewrite the first machine's `feed_host` and
`statedir`, boot out its launchd job and point its bridge at the new box — in the same words a
correct run uses. So the probe being best-effort for the *mapping* (a note, and you pass `--host`
yourself) does not carry over to the *name*:

| Refused | Why |
|---|---|
| the machine will not answer | the fall-back would be the default instance, above |
| `--instance auto` with no `--feed-host` | there is nothing to ask |
| `--instance auto --no-probe` | the probe **is** the name |
| a hostname that is not a usable name | `probe_farmhost` allows a `.` because a `.` is fine in a `host_<name>` key; a launchd label component is stricter, so `auto` re-asks with the narrow rule and names the host in the message — you typed `auto`, not `weird.name` |

One consequence: the literal name `auto` is unavailable. A machine really called that needs its
instance spelled some other way — or the three flags the sugar stands for.

The `next:` hint it prints carries **this instance's** statedir into the farm-side command, so the
copy-paste is right for the machine you just added.

### `--farm <ssh-target>` — run the farm side instead of printing it

```
sh install.sh mac … --farm <ssh-target>
```

Optional. Without it the installer ends by *printing* the farm-side command; with it, it ssh's to
that target and runs it. Same command either way —
`test_the_printed_farm_command_and_the_sshed_one_are_the_same_argv` compares the two forms token for
token, because they diverged once and hooks were installed against a statedir the bridge never read.

⚠️ **It is not `--feed-host`, and for a one-machine instance you want the same string in both.**
They are different kinds of thing, which is the whole reason both exist:

| | written to the config? | who uses it, and when |
|---|---|---|
| `--feed-host <target>` | **yes**, as `feed_host` | the **bridge**, on every connection, for the life of the install — it is the transport |
| `--farm <target>` | **no**, used once and forgotten | the **installer**, during this command, to save you a paste |

They legitimately differ only on a **shared-disk** cluster, where the farm install runs on *every*
agent host while only one of them is the feed host. A no-shared-disk instance is a single machine
playing both roles, so it is one value typed twice.

Mistyping the `--farm` half is **loud, and stops at a safe point**: the Mac half is already
configured, so the run ends `ssh: Could not resolve hostname …` followed by `the farm side failed;
the Mac is configured, the farm is not`. Nothing needs undoing — fix the alias and re-run (the mac
half is idempotent), or run the printed command on the machine by hand.

A failing farm side is fatal on purpose. Half an install is the state nothing can diagnose later:
the plist is loaded and the bridge is up, so the sidebar looks alive while the farm has no hooks and
never writes a session.

Then repair it with `agb-refresh --instance <name>` — or with a plain `agb-refresh`, which sweeps
every instance. Read [`design.md`](design.md) §5, *One Mac, several instances* for the seven
limitations; the first of them, a helper run **without** `--instance` silently acting on the default
one, is what the sweeps removed.

## `agb forget-rows` — Mac

```
agb forget-rows [--key <key>]... [--all] [--config <path>] [--rows <path>]
                [--placements <path>] [--launch-agents <dir>] [--no-close] [--dry-run]
```

Drops `key → row` bindings so the next snapshot re-creates the rows. The recovery for **agterm
having forgotten its rows** — closed, reset or reinstalled — while the map still names them. Every
`rename`/`status` then fails with `error: no such session`, and nothing else clears a *bound* entry:
`close-done` only touches `[done]` rows, and `prune` works from farm-side state.

| Flag | Default | Meaning |
|---|---|---|
| `--key <key>` | — | forget one; repeatable. Use it when only one row was closed — dropping the whole map mints duplicates for rows that are still live. **It sweeps**: see below |
| `--all` | off | every binding of **every** instance. The opt-in a bare run refuses |
| `--config <path>` | — | which instance to repair; **narrows** the run to it. **Both** `--rows` and `--placements` are derived from its directory |
| `--rows <path>` | derived from `--config` | the map. An explicit path wins, and narrows |
| `--placements <path>` | derived from `--config` | remembered `key = workspace` file. An explicit path wins, and narrows |
| `--launch-agents <dir>` | `~/Library/LaunchAgents` | where to look for instances. Says where to **look**, not which one to act on, so it does not narrow |
| `--no-close` | off | leave agterm's sessions open. They become duplicates as soon as the bridge mints fresh rows, so this is for the case where you are about to close them yourself |
| `--dry-run` | off | name the bindings and change nothing |

⚠️ **A run naming no map is REFUSED, and names `--all`. This is the one command that does not default
to every instance**, and the reason is *not* that it closes rows — `agb-refresh` closes every row it
forgets too (`--no-close` is passed only when asked). The difference is what happens next:
`agb-refresh` **restarts the bridge**, so the rows it forgot are re-minted within seconds, and this
command restarts nothing. A sweep nobody meant would leave every row of every instance closed until
each bridge was bounced by hand. So the sweep that ends in a restart may default to all; the one that
does not, may not.

⚠️ **`--key` is the other way in, and it SWEEPS.** A key is read out of a bridge log, and nothing in
that log says which instance minted it — an operator naming the key has already said what they mean.
A key belongs to exactly one map, so the run finds it wherever it lives, prints
`forget-rows: <key> was in <label>`, and exits non-zero only when **no** instance had it. Running
in-process, this side can tell "not in this map" from "in no map at all", which
`agb-refresh --key`'s shell sweep cannot.

⚠️ **`--rows` alone still implies the DEFAULT config** — the one place the old default survives, and
deliberately. Naming a map *is* naming what to act on, and
`agb forget-rows --rows ~/.config/agbridge/rows` is the documented recovery for an install that has
no instance name to give. Having just read that the default is gone, expect this to look
inconsistent: it is the same rule as `agb-refresh --rows`.

⚠️ **`--all` beside a map flag is an error**, not a silent winner either way. The operator has said
two contradictory things, and letting one win quietly is the failure this whole change removes: the
right map under the wrong label, reported as success.

There is **no `--workspace`** here — the parser refuses it. Where a row comes back is decided by the
*placements* file this command writes, not by a flag on it; `--workspace` is `agb bridge`'s.

⚠️ **`--config` has to derive the placements file as well, and that is the load-bearing half.**
`agb-refresh` passes neither override, so a `--config` that moved only `rows` would have
`agb-refresh --instance hostb` forget instance B's bindings and then write B's `key = workspace`
lines into the **default** instance's placements file — the normal recovery command silently
scrambling the other instance's row layout, with both files then wrong. One helper resolves all
three, so the two row-map commands cannot disagree about what `--config` means.

It prints all three on every run, before the `--dry-run` exit, for the same reason `close-done` does:

```
forget-rows: config …/hostb/config; rows …/hostb/rows; placements …/hostb/placements
```

The placements file is named explicitly because it is the one nobody passed on the command line.

Exits 1 if a named key was in **no** instance's map (and says so), 0 otherwise. **Nothing on the farm
is touched** — agents, keys and state are untouched, so rows return with the same identities.

⚠️ Not a file deletion, and not a `sed`. The map ends in an `#end <count>` sentinel; a hand-edited
line leaves the count wrong, the whole map then reads as corrupt, and the bindings you meant to keep
go with it. It **records which workspace each row is in** before closing it (`agtermctl tree --json`) and
writes them to `~/.config/agbridge/placements`, so the next snapshot puts every row back where you
had it. A tree it cannot read leaves the remembered placements **alone** rather than erasing them —
erasing would scatter every row, which is worse than the problem this solves.

It also **closes each agterm session as it forgets it** (`--no-close` to skip). Dropping the
mapping alone leaves agterm's session and its running `agb pane` in place, so the bridge mints a
fresh row beside it and you are left closing duplicates by hand. A close that fails is not an error:
agterm having already dropped the row is the original reason this command exists.

⚠️ A row's behaviour is **frozen when the row is created** — its `agb pane` process loads the code
that existed then. After upgrading the Mac's files, `agb-refresh` is what gets rows onto the new
code; nothing refreshes a running row in place.

Stop the bridge first — it holds the map in memory and merges-then-writes on every save.
`agb-refresh` does the whole sequence.

## `agb-refresh` — Mac, a convenience

```
agb-refresh [--key <key>]... [--dry-run] [--no-close] [--instance <name>] [--config <path>]
            [--label <name>] [--launch-agents <dir>] [--agb <path>] [--python <path>]
            [--rows <path>]
```

| Flag | Default | Meaning |
|---|---|---|
| `--key <key>` | every binding | forget one; repeatable. Passed straight through to `forget-rows`. ⚠️ **Does not narrow** — it sweeps; see below |
| `--dry-run` | off | say what would happen; stop and start nothing, change nothing. Forwarded to every child of a sweep |
| `--no-close` | off | passed through to `forget-rows`: leave agterm's sessions open |
| `--instance <name>` | **every instance** — see below | act on the instance `install.sh mac --instance <name>` created: label `com.agbridge.<name>`, and the config **that instance's plist names**. **Sugar for `--label` and `--config`, which must always move together** — a label without its config stops one instance and forgets another's bindings. An explicit `--label`/`--config` still wins. Same name rule as the installer: letters, digits, `-`, `_` |
| `--config <path>` | the config `<label>.plist` was rendered with; failing that `~/.config/agbridge[/<name>]/config` | the config to repair against; `forget-rows` derives the rows map and the placements file from it. Accepted on its own for an install made with `install.sh mac --config <path>` and no `--instance`, which has no instance *name* to pass. **Given alone it also moves the label**: the plists are scanned for the job that holds **the map this path names**, and its label adopted, so the bridge's own `agb-refresh --config …` hint acts on the bridge that printed it. The match is on the **map**, not on the spelling: the **directory** is resolved (`//`, `.`, `..`, a relative path, a symlinked `$HOME`) and **nothing else is matched on**, because `rows` and `placements` are `dirname(config)/rows` and `dirname(config)/placements` — the basename plays no part in either. So `<dir>/config`, `<dir>/`, `<dir>//` and `<dir>/anything` all name the same map and all find the same label; keeping the basename made the match *narrower* than the map it guards, and `--config ~/.config/agbridge/hostb/` (what tab-completion leaves you holding) bounced the **default** job with hostb's map. Among several matches the basename does break the **tie** — the job naming this exact file wins over one that merely shares the directory, rather than the winner falling out of `*.plist`'s collating order. A plist carrying no `--config` counts as naming the **default** config, since that is what its bridge resolves; only if it is readable and its label is in the `com.agbridge` space, so another program's LaunchAgent cannot claim agbridge's map. A path whose directory does not exist has no canonical form and matches nothing. When more than one job claims the map, all of them are named, the chosen one is said, and the run continues — they share a rows file, so there is nothing to choose. If no plist claims it, the default label is used and a `note:` says so — and names the job it is about to bounce |
| `--label <name>` | `com.agbridge` | the launchd job to boot out and back in. Narrows, and is what the sweep hands each child |
| `--launch-agents <dir>` | `~/Library/LaunchAgents` | where that job's plist lives — **and where the sweep looks for instances**. Says where to look, not which one to act on, so it does not narrow |
| `--agb <path>` | `~/.local/lib/agbridge/agb` | the installed `agb` to run `forget-rows` through — **and the tree the plist reader asks what each job's arguments mean**: it loads `agb_mac.parse_bridge_args` from this file's directory rather than imitating it, so an argv `agb bridge` would refuse (`--config=` with no value, a stray positional, an unknown option, a `--watchdog` that is not a number) counts as naming no config, which is what a job launchd restarts for ever without ever starting a bridge holds. A tree with no `agb_mac` beside this path is **fatal** and says so: `agb forget-rows` could not run there either |
| `--python <path>` | the first `python3` on `$PATH` | interpreter to run it with. `agb` has no shebang on purpose |
| `--rows <path>` | derived from `--config` | passed through to `forget-rows` |

⚠️ **`--instance <name>` does not mean `~/.config/agbridge/<name>/config`** — it means *whatever
that instance's plist was rendered with*. `install.sh mac --instance hostb --config <elsewhere>` is
supported, and rebuilding the conventional path instead would name a file that does not exist:
`forget-rows` answers `no rows to forget: the map is already empty`, exits 0, and the recovery
command reports success for a map it never touched. The convention is the fall-back for a Mac whose
plist was never rendered.

⚠️ **With none of those flags it sweeps EVERY instance on this Mac.** It used to act on the unnamed
one and report success in exactly the words it would have used for the one you meant, leaving the
other sidebar broken with nothing to say so. What narrows it is naming a map — `--instance`,
`--label`, `--config`, `--rows`. **`--key` does not**: it is typed by someone reading a key out of a
bridge log, which does not say which instance minted it, so the sweep finds it wherever it lives and
fails only when no instance had it.

```
sweep:    every agbridge instance in /Users/you/Library/LaunchAgents

instance: (default) -- label com.agbridge, config /Users/you/.config/agbridge/config
…
instance: hostb -- label com.agbridge.hostb, config /Users/you/.config/agbridge/hostb/config
…
swept:    2 instances
```

The sweep **re-execs this script once per label** rather than looping in place — this is 1,600 lines
of `set -eu` with per-run globals and a `die` on most error paths, and an in-process loop would carry
one instance's state into the next and could end the sweep with jobs booted out and never started
again. Every flag is forwarded explicitly, `--dry-run` above all. A child's failure is recorded, the
sweep continues, everything stopped is started again, and the run exits non-zero with a summary
naming what failed.

⚠️ **An instance left without a running bridge is an error**, and a distinct one — the child exits
**4** and the parent says `no bridge was started again for: <label>`. It is the rule that makes
bare-is-all safe: forgetting an instance's rows and then not starting its bridge again leaves that
sidebar dark with nothing to re-mint it. It is a **sweep** rule; `agb-refresh --instance <name>` on a
Mac whose plist was never rendered still warns and exits 0, which is a documented recipe.

⚠️ **Ctrl-C is safe.** The `trap` that restarts the bridge lives in the process that did the
`bootout`, which under a sweep is the child; the sweep then stops there rather than bouncing the
instances you interrupted it to protect, and says which ones it did not visit.

⚠️ **A Mac with no instance plists is unchanged** — a note, then the single default run, which still
forgets the default map and warns that nothing was restarted. **"I could not list them" is not "there
are none"**: an unreadable `~/Library/LaunchAgents` is fatal, because taking it as none would fall
back to the default instance and report success for a run that swept nothing.

**It names what it is acting on, first line of each instance, every run** — including under
`--dry-run`:

```
instance: hostb -- label com.agbridge.hostb, config /Users/you/.config/agbridge/hostb/config
```

⚠️ This remains the whole mitigation for a **narrowed** run, which is still exactly as silent as it
always was: `--instance` naming the wrong one stops that job, forgets its bindings and restarts it,
succeeding and saying so in the words you were expecting. Nothing can detect the intent, so it states
the answer instead. An instance whose label is outside the `com.agbridge` space is named by its
**label** — it has no other name, and calling it `(default)` was a bug the sweep made reachable.

⚠️ **The liveness poll below is matched per instance**,
`pgrep -f "<agb> bridge --config <the plist's config>([[:space:]]|$)"`, because `--dest` is shared by
every instance and a bare `<agb> bridge` matches all of them — the other instance's live bridge would
keep the poll busy for its full 10 s and produce a `still running after 10s` warning on a perfectly
correct refresh. Three details, each closing a failure:

- The pattern is used **only when the instance's plist actually contains `--config`**; against a
  plist rendered before 0.5.0 the script says so and waits on the broad pattern instead. A narrow
  pattern assumed rather than checked would match nothing, return instantly, and let the forget land
  while the bridge is still alive — the failure the wait exists to prevent.
- The **value** comes from the plist too, not from whatever `--config` this run resolved. They can
  differ (an explicit `--config` names the map to repair; the plist names what launchd started), and
  a pattern built from the wrong one matches nothing — the same silent failure from the other end.
- The trailing `([[:space:]]|$)` is a boundary. `pgrep -f` matches an unanchored regex against the
  whole command line, so `…/agbridge/config` is a *prefix* of an instance named `configb`'s
  `…/agbridge/configb/config`, and without it a plain `agb-refresh` polls that live process for the
  full 10 s and warns — on the most common invocation this command has.
- A **miss is "not proven gone", not "gone"**: the pattern comes from the plist and the question is
  about the process, and `install.sh mac --no-load`, a hand-started bridge or a re-rendered plist all
  leave those disagreeing. ⚠️ **And the follow-up question cannot be another pattern** — `pgrep -f`
  matches a regex against whatever spelling the process was started with, and the far side of a regex
  match cannot be canonicalised. So a miss reads the command lines instead: `pgrep -f` for the pids,
  `ps -ww -o args=` for what each was started with, and `same_map` to attribute each one to a map.
  A bridge carrying `--config X` is this run's iff `X` names a file this run rewrites; one carrying
  no **provable** `--config` is this run's iff this run repairs the **default** map (the only one it
  can hold — it resolves `agb.config_path()` itself); and one `ps` will not name is waited for
  anyway, because unattributable is not gone. The wait is announced once, saying which of the five it
  is, and the 10 s warning names that rather than the label that was booted out.
  ⚠️ **`--rows` counts too**, because it is not `--config` alone that decides which map a bridge
  holds: `render_settings` spends `--rows` first (`opts.get("rows") or rows_path(config)`), so
  `bridge --config <elsewhere> --rows <this map's rows>` is writing the file this run rewrites and is
  waited for whatever its config says. The reverse does not hold — a `--rows` does not answer the
  untagged question, since such a bridge still resolves the default config for its *placements*.
  A plist that names this map only through `--rows` claims the label the same way, ranked last.
  ⚠️ **` --config ` bytes are not a `--config` flag.** `ps` flattens argv, so
  `bridge --workspace "farm --config /other/config"` — a bridge with no config flag at all, holding
  the **default** map — prints a line identical to one carrying it. Reading those bytes as proof
  skipped the default-map question and answered "not ours" on a default refresh: zero waits, the
  forget under a live bridge. It is undecidable from the line, so it is resolved towards the wait:
  a `--config` is proof only when no other value-taking flag precedes it. Nothing changes for a
  launchd-started bridge, whose `--config` comes straight after `bridge`; a hand-started
  `agb bridge --feed-host X --config Y` is now waited for on a default-map run.
  ⚠️ **Every `--config` on the line is offered, not the first**, because `agb bridge` keeps the
  **last** one (its parser overwrites a repeated value flag), so
  `… bridge --config /old --config <this instance's>` is a bridge holding *this* map. It cannot
  simply read the last one either: `ps` flattens the arguments, so `--config "/a --config /b"` is one
  path containing that text and the two readings are indistinguishable. Offering all of them is a
  superset of both, and its only cost is an over-match — a bounded wait. The same last-wins rule
  applies to a hand-edited plist that repeats the flag, where there is no ambiguity at all.
  ⚠️ **Both spellings count, and in position order.** `agb bridge` takes `--config=<path>` as well as
  `--config <path>`, so both readers have to know both. The `ps` reader scans one marker — the bare
  `--config` — and lets the character after it say which spelling it is; the plist reader gets it for
  free, because it hands the argv to `agb bridge`'s own parser rather than imitating it (below). Two
  `case` arms, one per spelling, picked by *arm* order instead: a line whose inline occurrence came
  first cut at the later space form and never offered the inline value, which under the one-argument
  reading is the one the parser keeps. On the plist side the
  inline form read as *nothing*, and nothing there means "the default config", so an instance's own
  job claimed the default map and the run bounced the wrong label.
  ⚠️ **On the plist side an element is a flag only in flag position**, and that side has no
  ambiguity to trade against: `ProgramArguments` is a real argv array, so the elements after the
  `bridge` command word are handed to `agb bridge`'s own parser — `agb_mac.parse_bridge_args`, loaded
  by path from beside `--agb` — instead of being walked by a second implementation of it. A
  value-taking flag consumes the next element whatever it looks like, and an argv the bridge would
  *refuse* names no config at all, which is the class no imitation could reach.
  `--workspace` followed by `--config=/other/config` is a workspace *name*; answering
  `/other/config` for it made the banner, the liveness pattern and `forget-rows` all act on a map no
  process runs on, reporting `the map is already empty` for a map that was never opened.
  ⚠️ **And only inside `ProgramArguments`** — a plist is not an argv, only one of its keys is, and
  every other key carries strings too (`ProcessType`, the log paths, an `EnvironmentVariables`
  value, a `WatchPaths` array). A hand-edited `--config` pair *after* the array overwrote the real
  value, because last-wins is right inside argv; one *before* it manufactured a config for a job
  whose argv has none.
  ⚠️ **And only the part of it after the `bridge` command word**, because `ProgramArguments` is the
  whole command line — `<python> -S -E <agb> bridge --config <path>` — and `agb` reads its command
  from `argv[1]`. A flag in *front* of the command name **is** the command name: `<agb> --config
  /real/config bridge` is `unknown command: --config`, so that job starts no bridge and holds no
  map — and reading `/real/config` off it made it an exact, declaring claimant that outranked the
  job actually holding the map. No `bridge` in the array is "carries no `--config`", the same answer
  a plist predating the flag gives, so such a job ranks *below* every job that names one rather than
  disappearing.
  ⚠️ **Four exit statuses, and only the first two are about the plist.** 0 is an answer (an empty
  value means "no such flag"); **2** means "this file says nothing" — unreadable, or not a plist;
  **3** means `agb_mac` could not be loaded from beside `--agb`, so the parser the reader asks is not
  there; anything else means the *reader* failed, which is not a statement about the plist at all.
  Folding the last two into the second made `--python /bin/false` — an ordinary mistake — read every
  plist as silent, fall through to the default label and the conventional config, and report success.
  Both are fatal now, at all three call sites: `plist_read_ok` spells the rule once — **2 is an
  answer, anything else is fatal** — and names `--agb` for 3. ⚠️ **For the rest it names two files,
  not one**, because the reader is two files now: exit 1 is also what `agb` returns for its own
  errors, so blaming `$python` alone would send you to replace a working interpreter.
  ⚠️ **A known-answer probe runs once before any plist is read**, to catch what a status cannot see:
  `agb instances --probe` must answer the literal `instances-ok`. It is **stdout-compared**, because
  `--python /bin/echo` exits 0 while printing its own arguments — and because `agb` answers an
  *unknown command* with exit 2, empty stdout and `USAGE` on stderr, which is byte-identical to a
  plist that names no config. Without the probe, an `agb` predating this command (0.5.0 and earlier)
  would make every plist silent and the run would succeed on the wrong instance.
  ⚠️ **A missing plist and an unreadable one are different questions.** Both answer exit 2, and the
  conventional path is the right fall-back only for the first. `agb-refresh --instance hostb` with a
  corrupt `com.agbridge.hostb.plist` repaired a map that never existed and reported it empty; it now
  refuses and asks for `--config`.
  ⚠️ **The plist is PARSED, with `plistlib`, and that is what stopped this class.** It was a
  hand-rolled XML token scan for four review rounds and each round found the rule the previous
  round's rule did not have: an XML comment or a `<?…?>` between the key and the array (the project
  ships a comment there) read as *no config at all*; whitespace inside a tag — `<string >`,
  `</array >`, both perfectly valid XML — made an element vanish or let a later `WatchPaths` array
  overwrite the real config; a comment splitting a value across lines
  (`<string>/tmp/a<!--`⏎`-->b/config</string>` *is* the string `/tmp/ab/config`) lost the element and
  spent the dangling `--config` on the next one. `plistlib` is stdlib on both sides and
  `agb-refresh` already requires a python3, so it costs no new dependency, and it retires the whole
  limitation list this section used to carry: **binary** plists, a config delivered **as CDATA**,
  **character references** even in a flag name (`&#45;-config`), the **DOCTYPE**, tags spanning
  lines, minification. What is left is a file that is not a plist at all — unreadable, truncated,
  or not XML and not binary — which answers "this file says nothing", and is then skipped rather
  than standing for the default config.
  ⚠️ **And the reader itself is no longer in this script**: `plist_arg` is two lines of shell calling
  [`agb instances --plist <path> --arg <flag>`](#agb-instances--mac), which is where the parse, the
  sniff-retry and the `parse_bridge_args` call live. The statuses above are unchanged — that contract
  and its twelve tests predate the move, and only *where* the answer is computed changed. Everything
  else in `agb-refresh` is untouched: the map comparison, the five ranks, the multi-claimant warning
  and the `[ -e ]` split all still read the same four statuses from the same three call sites.
  ⚠️ The **subtraction** this replaces (`pgrep -f "<agb> bridge"` minus
  `pgrep -f "<agb> bridge --config"`, because ERE cannot spell "does not contain") could only find
  the untagged case, and counted a bridge over *this* map spelled `<dir>/./config` as somebody
  else's — so the forget ran under it. The untagged gate itself is unchanged and still matters:
  ungated, the commonest untagged bridge there is — the default job, still running from a pre-0.5.0
  plist because `install.sh mac --instance` does not restart it — made **every**
  `agb-refresh --instance <name>` wait 10 s and then report `com.agbridge.<name> is still running
  after 10s`, which was false, since that job's bridge had exited before the first poll.

Stop the bridge → `agb forget-rows` → start it again. A separate POSIX-sh script, installed beside
`agb` on the Mac. It restarts the bridge **whatever the middle step reported**: leaving it down
because one key was not in the map would turn a small surprise into a dark sidebar. A bridge that
was already stopped is a fine starting state, not an error.

**The stop is waited on, not just requested.** `launchctl bootout` returns once launchd has accepted
the request, not once the process is gone, and the bridge is normally blocked reading its ssh. A
forget that lands while the old bridge is still alive is the thing this script exists to prevent:
that bridge holds the row map in memory and merges-then-writes on every save, so it can re-mint rows
against ids `forget-rows` has just closed — reinstating the `no such session` spam that sent you
here. So it polls (with the per-instance `pgrep` pattern above) until the process is actually gone,
for at most **10 seconds**; past that it says so and goes on, because a recovery command that hangs
is worse than one that proceeds with the risk named.

**When to reach for it.** All three cases are the same underlying one — agterm no longer knows a row
id the Mac's map still names — and nothing else clears a *bound* entry: `close-done` only touches
`[done]` rows, and `prune` works from cluster-side state.

| | |
|---|---|
| agterm was closed, reset or reinstalled | its sessions are gone; the map is not |
| **the Mac rebooted** | agterm comes back as a fresh app with no sessions |
| the Mac's files were upgraded | rows keep the `agb pane` code they were *created* with, so new features do not reach them until they are recreated |

A reboot is worth stating plainly because it looks alarming and is not: **nothing on the cluster
notices.** The tmux sessions, the agents in them and their state files are untouched — the Mac holds
none of that. The bridge restarts itself (`RunAtLoad` in the LaunchAgent) and its beat resumes within
a second or two, which `agb doctor` on any cluster host will show. Open agterm, run this, and the
rows return on the next snapshot with the same identities.

## `agb-tmux [name]` — farm, a convenience

```
agb-tmux [-d] [name] [-- <command>...]
```

The general case of the family: a named tmux session plus an agterm row, running whatever you give
it — default your login shell. `agb-claude` and `agb-codex` are the two special cases that know their
agent's name and its caveats.

```sh
agb-tmux -d shellrow              # a farm shell you can click on from the sidebar
agb-tmux -d build -- make -j8     # a long build with a row of its own
```

⚠️ **A plain tmux row has no agent, so its status never changes.** agbridge's state machine is driven
by Claude Code's hooks and a shell fires none, so the row stays `completed` for its whole life: the
glyph never moves, and a long build looks exactly like an idle prompt. If you want a moving glyph,
something has to call `agb hook` itself — which needs no Claude and is exactly what this script does
once, at launch.

⚠️ **And it is deliberately not an `agb-peer` participant.** `classify` reads a shell as `unknown`,
so the relay will never type into it. That is the right answer: a shell would *execute* what it was
sent.

⚠️ The pre-mint matters most here of the three. Claude would eventually mint a row on its first hook;
Codex never would; a shell never would either, and unlike an agent there is nothing that could later
change its mind.

## `agb-codex [name]` — farm, a convenience

```
agb-codex [-d] [name] [--greet <text>] [-- <codex args>...]
```

`agb-claude` for Codex, and a near-copy of it deliberately — the two are expected to diverge (Codex
has no trust prompt, has `resume`/`queue`, and fires no agbridge hooks). What must not diverge is the
pre-mint, which is what five mutation-checked tests pin.

⚠️ **Codex fires NO agbridge hooks, so the row it mints stays `completed` for ever.** agbridge's
state machine is driven by Claude Code's four hooks; Codex has its own hook system and nothing wires
it to `agb hook`. The row is real, attachable and addressable — which is the whole point — but:

- the sidebar glyph never moves, so you cannot see whether a Codex agent is working or blocked;
- **`agb-peer`'s status gate protects a Codex peer not at all**, since it always reads `completed`.
  The mode and caret checks still apply, and Codex is the safer peer anyway: MEASURED, it does not
  paste-collapse a long injection, so delivery verification is *more* reliable than for Claude.

Wiring Codex's own hooks to `agb hook` would fix the first and is unexplored.

⚠️ And the pre-mint matters more here than for Claude: Claude would eventually mint a row on its
first hook, so the wrapper only makes it *earlier*. Codex never would, so without this the row would
not exist at all.

### `AGB_CODEX_CUSTOM` — the codex command line, replaced wholesale

| Variable | Default | Meaning |
|---|---|---|
| `AGB_CODEX_CUSTOM` | unset | a shell command line run **instead of** `codex`, `eval`ed inside the session |

```sh
export AGB_CODEX_CUSTOM='submit -q big -I "codex --yolo"'
agb-codex -d pool
```

The interesting Codex is often not the one on this host — it may live behind a batch scheduler, in a
container, or on a pool machine picked at submit time. That launcher is **site-specific**, so it
cannot live in a file that ships publicly; this variable is the seam, and everything about the shape
of it follows from that one fact.

The value is a **shell command line**, not a program name: it is `eval`ed, so its own quoting is
honoured. That is what lets the agent be an *argument* to the launcher (`-I "codex --yolo"`) rather
than the program being run — and word-splitting it instead would hand the launcher `"codex` and
`--yolo`, which no downstream error message would explain.

### The two placeholders

| | becomes |
|---|---|
| `{}` | this invocation's `--` arguments, spliced **verbatim** |
| `{env}` | `AGB_HOST=<this host> AGB_AGENT_PID=none` |

```sh
export AGB_CODEX_CUSTOM='submit -q big -I "codex --yolo {}"'
agb-codex -d pool -- --model gpt-5.6
```

⚠️ **Without `{}`, `--` arguments are refused rather than appended.** With an opaque launcher there
is no position this wrapper could append at that is not a guess: the agent is inside somebody else's
argument, so a trailing word lands on the *launcher*. The refusal names `{}`, because "there is
nowhere to put this" is only useful alongside "here is how to say where".

⚠️ **`{}` splices verbatim, so the allowed set is a positive list**, not a list of characters to
escape: `[A-Za-z0-9._:/=+@,-]`. The wrapper cannot know whether `{}` sits inside quotes —
`-I "codex {}"` says it does, `docker run img codex {}` says it does not — so it cannot quote for
you, and an argument that would change how the line parses is refused instead of silently mangled.
`--greet` stays refused for the same reason: it is prose, so it always needs quoting.

`{env}` is **inert for Codex**, which fires no hooks and so can never disturb its row. It is
supported here only because these two wrappers are deliberate near-copies; the reason it exists is
[`AGB_CLAUDE_CUSTOM`](#agb_claude_custom--the-claude-command-line-replaced-wholesale) below.

⚠️ **It is embedded in the command tmux is handed, never inherited.** A session created against an
already-running tmux server takes its environment from the **server's**, plus `update-environment` —
so a variable exported in your shell a moment ago is simply not there, and a version that relied on
inheritance would exec nothing at all on every machine where a server was already up.

The first word is checked against `$PATH` and a missing one is refused, which turns a typo into an
error here rather than a session that opens, execs nothing and closes again. Only the first word —
the rest is the launcher's business — so that word must *be* the program: an inline assignment or a
redirect in front of it reads as a missing command.

The pre-mint is unchanged, and still correct: `exec` keeps the pid and starttime of the pane's own
process, which is now the launcher. That is the right process to record, because its death is what
should reap the row even when the agent itself ends up on another machine. The consequence is the
one already true of every Codex row — the glyph never moves — plus one more: `agb-peer` reads that
pane, so a peer started this way is reachable exactly as far as the pane is.

## `agb-claude [name]` — farm, a convenience

```
agb-claude [name] [-- <claude args>...]
```

Not part of `agb` — a separate POSIX-sh script beside it. It starts Claude Code inside a **named
tmux session**, which is what makes the resulting row attachable.

| Argument | Default | Meaning |
|---|---|---|
| `name` | the current directory's name | tmux session name. `.`, `:` and spaces become `-`, because tmux cannot address them as a target |
| `-d`, `--detach` | off | start it in the background and return immediately. The row is there either way |
| `--greet <text>` | none | an opening prompt for `-d` to send. **Not sent by default** — the row no longer needs one. Refused without `-d`, where it would be silently ignored |
| `--` | — | everything after it is passed to `claude` untouched. **Required for anything starting with `-`**: `agb-claude --resume <id>` is refused, `agb-claude work -- --resume <id>` is not |

### The row is minted before Claude starts

A hook is what mints a key, so a row used to appear only once somebody typed — and in a directory
Claude has not been trusted in, *never*, because it stops on *"Is this a project you trust?"* and
submits nothing. So the session's own shell hooks first and then `exec`s Claude:

```sh
tmux new-session … sh -c 'AGB_AGENT_PID=$$ agb hook completed 2>/dev/null; exec claude "$@"' …
```

### `AGB_CLAUDE_CUSTOM` — the claude command line, replaced wholesale

The same seam as `AGB_CODEX_CUSTOM`, with the same two placeholders — and one difference that is the
whole reason `{env}` exists.

```sh
export AGB_CLAUDE_CUSTOM='submit -q big -I "{env} claude {}"'
agb-claude work -- --model opus
```

⚠️ **Claude fires hooks from wherever it actually runs, and Codex does not.** A Codex on a pool node
can never disturb its row, because it never writes one. A Claude there resolves a **different
anchor** — `own_host()` returns that machine — and mints a **second row**, on a host the Mac has no
`host_<name>` mapping for and whose pane it cannot reach.

`AGB_HOST` alone does not fix it. `$TMUX`/`$TMUX_PANE` do survive job submission, so the anchor
matches again — but `bind_key` adopts only when `idx_matches`, the pid differs, and it **replaces**
the index, orphaning the row the wrapper minted. `AGB_AGENT_PID=none` is what makes it adopt:
*absence of evidence must never re-mint*. `{env}` sets both.

⚠️ **And that has a price, so do not use `{env}` for a launcher that keeps the agent on this
machine.** Every hook rewrites the record's pid, so the entry becomes pid-less and can no longer be
reaped by proof of death: when the job ends the row sits at its last state until `agb prune`.
Liveness is proven, never inferred — nothing will tidy it up for you.

⚠️ **An agent started with `{env}` also stops sweeping**, and that is the fix for a real failure
rather than a limitation. Because `AGB_HOST` makes `own_host()` an *assertion*, such an agent used
to sweep the statedir of the host it was impersonating and adjudicate that host's pids against its
own machine — `ESRCH` for every live one, read as proof of death. Measured: eleven live rows reaped
by one hook, plus a duplicate of the agent's own row when the same sweep dropped its anchor.
`host_is_observed()` now refuses, so **something genuinely local must run the sweep for that host**
— which is normally true (its own agents' hooks, and the feed), but is worth knowing if every agent
on a host is remotely launched. `AGB_HOST_LOCAL=1` is the opt-in for an override that really does
name this machine, and `{env}` deliberately does not set it. `docs/design.md` §2 has the trace.

⚠️ **`{env}` spells `own_host()`'s resolution in shell** (`$AGB_HOST`, else `uname -n`, domain
stripped). That is a cross-file agreement with `agb` (CLAUDE.md invariant 14), pinned by a test that
compares against `agb.own_host()` itself — a disagreement produces a second row, not an error.

Three properties make that produce **one** row rather than two, and each was measured rather than
reasoned about:

| | |
|---|---|
| it runs **inside** the new session | the anchor is `(host, tmux-server-pid, %PANE)`. Hooking from the caller's pane would mint a row pointing at the wrong terminal — which is worse than no row, and looks like it works |
| `exec` preserves **pid and starttime** | so the identity the shell records *is* Claude's a moment later, `bind_key` finds a matching record and **adopts** the key instead of minting a second one |
| it records a real pid | a pid-less entry also adopts — *"absence of evidence must never re-mint"* — but nothing except `agb prune` could remove it if Claude never started at all |

**`completed`, not `active`**: a session sitting at an empty prompt is waiting for you, which is
what that glyph means. `active` would claim it is working and blink a transition that never
happened. It raises no banner — the finished-turn banner measures from a preceding `active`, and a
fresh key has none.

Best-effort by construction: `;` rather than `&&`, stderr discarded. A missing or broken `agb`
costs a row, never a Claude.

```sh
agb-claude -d api-refactor                    # row appears; no prompt is sent
agb-claude -d api-refactor --greet "ready?"   # …unless you ask for one
```

⚠️ The trust prompt is still **not** answered for you — that is a security decision belonging to the
human, and a wrapper that clicked through it would be doing what this tool exists to stop. What
changed is only that the row now exists while it waits, so you can see the session and click into
it. Attach (`tmux attach -t <name>`), answer it, and the agent takes over its own row.

Re-running with the same name **attaches** to the existing session rather than starting a second
agent in it, matched exactly (`-t "=name"`), so `agb-claude api` will not attach to `api-refactor`.
Nothing is restarted in that case, so any `--` arguments cannot take effect — they are reported as
ignored rather than dropped silently.
Run from inside tmux it creates a *sibling* session and switches: a nested session cannot be
attached to from outside, and agbridge would record the outer session's name for the inner agent.

It exists because the tmux session name is resolved **once**, at the agent's first hook, and never
refreshed — so naming has to happen before the agent starts, and forgetting is easy.

## `agb-peer` — not installed by default

⚠️ **`relay` runs on the Mac; `send` and `who` run on the agent's own machine**, over no ssh and
with no agterm. All three come from a checkout on both sides — `install.sh` does not install
`agb-peer`, so the two copies can drift, and `agb-peer --version` is how you tell.

```
agb-peer --to <peer> [<message> | --stdin] [options]
agb-peer --list
```

Types a message into **another agent's composer** — agterm's own
[`cookbook/two-agent-chat`](https://github.com/umputun/agterm/tree/master/cookbook/two-agent-chat)
pointed at agbridge rows, where the agent is in tmux on a cluster host rather than in the next pane.
It works because keystrokes survive agterm → `ssh -t` → tmux → the composer (CONFIRMED live
2026-08-24; `docs/agtermctl.md` has the transcript).

⚠️ **It is not wired into `install.sh` and nothing in `agb`/`agb_mac`/`agb_ops` imports it.** Copy it
onto `$PATH` yourself. It is an experiment on top of agbridge, not part of the bridge.

| flag | |
|---|---|
| `--to <peer>` | row id, id prefix, or a substring of the row's title. Tried in that order, first non-empty tier wins |
| `--stdin` | read the message from stdin instead of an argument |
| `--from <label>` | how the message is signed; default `$AGB_PEER_FROM`, then `peer` |
| `--pane <kind>` | `left` \| `right` \| `scratch`, default `left` |
| `--no-send` | type the message but do not press Return |
| `--no-arm` | refuse an unattached row instead of attaching it |
| `--force` | deliver even when the peer's status says it is busy |
| `--retries <n>` / `--interval <secs>` | re-checks of a busy peer; default 5 × 8 s |
| `--dry-run` | say what would happen; touch nothing |
| `--list` | every row with its status and its **mode** |

Exit codes are distinct because the caller is usually another agent: **0** delivered, **1**
usage/environment, **2** no such peer or ambiguous, **3** peer busy after the retries, **4** typed
but not verified — *and therefore not sent*.

### The three gates, and why they are three

- **Mode.** A row's pane is either `agb pane`'s menu (unattached) or the agent's composer
  (attached), and the same bytes mean different things in each. `session text` is the only thing
  that can tell them apart — ⚠️ **`foreground` cannot**, because it is the argv the session was
  *launched* with and an agbridge row always reports `agb pane`. A menu row is armed with a **bare
  newline**, which is the one input `agb pane` cannot act on (`""` after its `.strip()`), so arming
  can never hit the branch that closes the row.
- **Status.** `tree` carries the status the bridge last set, which came from the peer's own Claude
  Code hooks — a fact about the *agent*, not a guess about a screen, and the one signal the cookbook
  has no equivalent for. `active` and `blocked` refuse. ⚠️ **This gate is not redundant with the mode
  check, and that is measured rather than argued**: a live `blocked` agent reads as `composer`,
  because its permission dialog is on screen *and* the composer glyph is still in the buffer. There
  is somewhere to type, and it is the dialog — the cookbook's "Dialog Window Vulnerability". Neither
  the mode check nor the cursor check can catch that one. ⚠️ `idle` is allowed through: it is the
  bridge's word for *no current information*, so refusing it would strand every row whose feed
  blinked.
- **Composer.** `surface cursor` must report the empty-composer column. This is the gate that
  matters in practice — the very first row this was ever pointed at had an unsubmitted draft sitting
  in it, which a naive `session type` would have appended to and submitted together on the next
  Return.

Then it types, **re-reads the pane to confirm the text rendered**, and only then sends Return as a
*second* call. That ordering is not defensive coding: a permission dialog can appear between the
cursor check and the keystrokes and swallow them, and pressing Return would then be answering the
dialog.

⚠️ **A message that strips to a word `agb pane` acts on — `q`, `quit`, `exit`, `s`, `shell`,
`split`, `d`, `drawer`, `scratch` — is refused outright, in either mode.** Mode detection is a
screen read and a screen read can be wrong; the cost of being wrong on those nine words is a
destroyed row rather than a lost message. That list is a cross-file agreement with `agb_ops`'
`PANE_QUIT_WORDS`/`PANE_SPLIT_WORDS`/`PANE_DRAWER_WORDS`, pinned by a test.

### `agb-peer who` — ask the relay who is in this conversation

```sh
agb-peer who          # run by the AGENT, inside its own tmux session
```

Sends the relay a request and prints that it did. ⚠️ **The answer arrives as a message on a later
turn, not on this command's output** — there is no channel back to a command that has already
exited. It looks like this:

```
[chat from relay] you=alice peer=bob peer=carol
```

`you=` is the asking agent's own participant name; the relay knows it because it knows which pane
asked, which is `try_deliver`'s rule that a sender is the pane a marker was found in.

| | |
|---|---|
| **Silence** | no relay is running, **or** this pane is not one of its participants. Indistinguishable, and neither worth retrying |
| **Outside tmux** | refused — `$TMUX_PANE` is what the relay answers into |
| **Unreachable tmux** | takes the file path like `send`, and prints a `[peer #…]` marker the agent must repeat on its visible screen |

⚠️ **The relay answers only the single word `who`.** Anything else addressed to `relay` is dropped
and logged — a loop guard, because `SKILL.md` tells an agent to reply to anything arriving as
`[chat from …]` and the answer looks exactly like that. `relay` is a reserved participant name for
the same reason; `Relay` and `relayed` are ordinary.

### `agb-peer send` / `agb-peer relay` — one mechanism for all three pairings

```
agb-peer send  --to <name> <message>            # prints a marker; needs no agtermctl
agb-peer relay alice=<row> bob=<row>[:pane] …   # carries markers between panes
```

**The transport is the screen.** Every agent worth talking to already has a pane on the Mac — that
is what agbridge is *for* — so the Mac is the one place a farm agent and a local agent coexist. An
agent sends by **printing a line**; the relay reads panes and types the payload into the recipient's
composer. Nothing in it knows where an agent runs, which is what makes one mechanism cover
farm↔farm, farm↔Mac and Mac↔Mac. `agb-peer send` calls no `agtermctl` at all and runs anywhere.

⚠️ **CORRECTED 2026-08-27.** This section used to say `dashboard` was a modal overlay that blocked
all input, and therefore that `--dashboard` was "for an unattended run, not for a session you are
also talking in". **That is wrong for the path that matters, and it was never sourced** — the claim
lived here alone, with no entry in `agtermctl.md`, which is the one file whose job is tagging each
clause with its evidence. **MEASURED with a grid open**: `session type --target <row> --pane left`
returns ok and the text lands, verified by reading it back with `session text`. That is the exact
call the relay makes, so a dashboard does **not** stop delivery and `--dashboard` is usable during a
live conversation.

⚠️ What remains **ASSUMED** is narrower and worth keeping: whether the grid takes *physical keyboard*
focus in the GUI is untested — nobody has typed at the machine with one open. The correction is
about the control socket, not about what your hands can do.

**What `--dashboard` does, tick by tick.** It is an **adjunct to a message pump**, not the point of
the run, and that decides its whole error policy: no grid outcome may ever stop a message. Every
`agtermctl` call it makes is best-effort — a failure is said on stdout and the relay carries on.

| | |
|---|---|
| **it closes what it opened** | on `Ctrl-C`, on a clean exit, and when membership falls to nobody. ⚠️ **Only a grid this run opened** — agterm has exactly one grid and no ownership token, so reaching for one it did not open would close somebody else's |
| **a partial grid is MARKED, not hidden** | a member with no row gets `dashboard: no row for carol -- the grid shows the other 2`, once, and the rest are gridded. The alternative — a tidy grid silently missing an agent — is the failure this whole area exists to remove |
| **and that includes the cells AGTERM drops** | `dashboard: open -- but agterm dropped unresolved: BBBB2222`. ⚠️ agterm exits **0** here and says it on **stdout alone**, so the status the relay reads says the grid is fine. This was the one cause of a partial grid the relay could not see, while this table already promised it was marked. ⚠️ **And it is RETRIED**, for the reason the row below gives: a partial open is an incomplete outcome, and the documented cause of one — a `:right` cell whose split is briefly absent — is transient |
| **over nine participants, nine are gridded** | `dashboard: p9 not shown -- agterm's grid takes 9 cells`. agterm's cap is nine **cells**; handing it ten used to cost everybody the grid. ⚠️ The opposite of `agb-dashboard`, which refuses over the cap — same sentence, different answer: an adjunct shows nine of ten, a grid that *is* the point does not open short |
| **a failed open, or a PARTIAL one, is RETRIED** | the cell set is recorded as shown only on an open that worked **in full**, so a transient failure is tried again on the next tick. The *message* is throttled instead — one persistent failure says so once, not once per tick. ⚠️ The partial case shipped without this: agterm exits 0, so the cells went down as shown and the `fresh == shown` gate skipped every later tick, leaving the grid partial for the rest of the run |
| **a `scratch` participant is excluded, and said** | `dashboard: not shown -- carol (scratch); agterm's grid takes only left/right panes`. agterm rejects `:scratch` at **parse time**, so before this a single scratch participant meant `--dashboard` produced no grid at all, for the whole run, with the reason going only into the log |
| **an excluded participant is not a MISSING one** | it resolved fine; its pane is just not something a grid can show, and it has already been named on its own line. A second line calling it "no row for carol" would be a contradictory diagnosis of one situation, and the wrong one. ⚠️ This row used to say that counting it as missing "would have kept the grid permanently shut" — **false of the code**: `missing` drives one throttled message and gates nothing. That consequence belonged to a *close rule* the plan rejected. `design.md` §6 |
| **every one of these messages is throttled** | ⚠️ **No count here, deliberately** — this row carried one, and it was corrected three times on this branch and went stale a fourth, each time because a later commit added one more throttle. The rule instead: *every* grid line the relay prints runs on every tick, so each is deduped on its message **text**. Unthrottled, one typo prints a line per tick for the life of the relay; deduped on the text, a change in *who* is missing still gets through |
| **…and every throttle is CLEARED when its condition goes away** | otherwise the throttle that stops a line repeating stops it ever firing again: a participant who leaves the drawer and returns, or a roster that crosses the cap twice, would be dropped in silence the second time. The exclusion note shipped without it — measured: carol in the drawer was reported, left, came back, and the grid dropped her without a word. ⚠️ **So did the alias note, and that one is not in this table**: `_one_name_per_row`'s "alice and bob both resolve to row …" line lives with the resolver, and `update_grid` subtracts its drops from `missing` — so a second, identical collision was throttled *and* the missing line suppressed, and a participant left the grid with nothing said at all. It needs clearing in **two** places: where the collision goes away, and in `_name_notes`, because a name that LEFT is in no later `resolved` for the first one to visit |
| **an agtermctl that will not ANSWER is not a refusal** | killed at 30 s against a wedged agterm client, which is the one failure where the grid may be **up**. The relay says so, assumes it owns a grid and closes it on the way out — and **backs the retry off**, doubling from one tick to 32. ⚠️ That last part is the fix, not a nicety: the grid update runs *before* the message pass, so a 30-second call every tick starves the pump the grid is an adjunct to. Any call that answers, refusal included, clears the back-off |
| **an agtermctl that will not START is not fatal** | `Ctl.dashboard` answers with a status when agtermctl *ran* and refused, and **raises** when it cannot be started at all — a removed binary, a `$PATH` an agterm pane did not inherit, `/proc/<pid>/exe (deleted)` after an upgrade. It is said and ignored. Before this it killed the relay mid-delivery |

⚠️ **Running `agb-peer relay --dashboard` and `agb-dashboard` at the same time is unsupported**, not
defended against. Whoever opens last wins. ⚠️ **And the latch each keeps records that it opened *a*
grid, never *which* one** — `agtermctl dashboard --close` closes "the open one" and takes no target,
so the flag gates *whether* to close and can never gate *what*. Neither closes anything when it
opened nothing; either will close **whatever is up** if it did, including the other's replacement.
⚠️ The relay's loss is permanent for the run, too: its re-open is gated on the cell set *changing*,
so once its grid has been replaced it puts none back until membership or the row ids move.

Open and close it by hand from a terminal outside agterm if you prefer, which is where `agtermctl`
is being driven from anyway — or use [`agb-dashboard`](#agb-dashboard--watch-several-rows-at-once-by-name),
which does the same by **label** and cleans up after itself:

```sh
agtermctl dashboard <a-id>:left <b-id>:left
agtermctl dashboard --close
```

### The doorbell, and why the screen carries no content

The first design printed the message to the agent's screen and had the relay read it back. **That
does not work**, and all three reasons were measured on a live agent rather than reasoned about:

| measured | consequence |
|---|---|
| `alternate_on=1`, `history_size=0` | Claude Code runs on the alternate screen, so the **terminal has no scrollback at all**. `capture-pane -S -2000` and agterm's `--all` both return exactly the visible screen |
| Claude's own scrollback **collapses multi-line blocks onto one line and truncates with `…`** | a scrolled-away message is *destroyed*, not merely hard to find — and driving it with `PageUp` is visible to whoever is watching |
| **Claude Code does not render tool output onto the pane** — it draws the command | ← the decisive one. A message printed to stdout is never on screen for anything to read, fresh or not |

So the screen cannot carry content. What it *can* carry is a **doorbell**, because tmux draws the
status bar rather than the app, and `session text` includes it:

```
window name:  claude [peer #k3n9x2]     <- always visible, never scrolls
tmux option:  @agbpeer_msg_k3n9x2       <- the message: 3 KB, exact round trip
```

The relay reads the bar on a tick it already makes for mode detection, so **watching costs nothing**.
Only when the id *changes* does it reach for the content — one ssh per message, none while idle.
The screen says *when*; ssh says *what*.

⚠️ **The fetch runs no remote shell.** `ssh <host> tmux show-options -p -t %N` is pure argv — the
first version ran a POSIX `for … in $(…)` loop and died with `Illegal variable name.`, because
`ssh <host> <cmd>` hands the command to the remote **login** shell and a farm login shell is often
tcsh. Quoting a script through tcsh is a losing game; there is no script now. `show-options -p`
renders each option on **one line**, escaping newline, quote and backslash, so the Mac parses it with
no help from the far side.

⚠️ **A fetch takes everything pending, not just the announced id.** The doorbell shows only the
latest, so a tick that missed one would lose it for ever; sweeping every `@agbpeer_msg_*` makes a
missed doorbell harmless. Each is unset as it is read, so nothing is delivered twice.

⚠️ **Order matters, and it bit on the first live run: start the relay BEFORE anyone sends.** The
first tick primes, and priming drains and discards — so a message queued a moment earlier is thrown
away, correctly and by design. The line naming what it dropped is the only warning you get.

⚠️ **Priming fetches and DISCARDS.** Options left in tmux from an earlier session would otherwise be
swept up by the first real message's drain and delivered as if new. The first pass clears them and
says how many.

### A participant on a machine you cannot ssh to

```
agb-peer relay me=<label>@<host> pool=<label>@<reachable host>:nfs \
    --chat-dir /abs/path/to/<statedir>/chat
```

For a job on a compute pool: it mounts the same NFS, and the Mac cannot ssh it. **Delivery is
unchanged** — start `agb-tmux` on a host you *can* reach, submit the interactive pool job from inside
that shell, and start the agent there; `session type` types into the pane and the pane is connected
all the way down. Nothing ever connects *into* the pool, which is why it works.

**Sending falls back to a file**, because the tmux socket cannot travel: `$TMUX`/`$TMUX_PANE` are
inherited through the job submission, `/tmp` is local to each machine, and ⚠️ **putting the socket on
NFS does not help — MEASURED.** A `tmux -S <nfs path>` server accepts connections and holds pane
options locally; from another machine, with that server alive and the socket file plainly visible on
the shared mount, tmux answers `no server running`. A unix socket is a local kernel rendezvous, not
a filesystem object NFS carries.

So the fallback uses the two things that do cross:

| | |
|---|---|
| doorbell | an echoed `[peer #id]` line — `read_doorbell` already scans the whole pane, so this needed no new parsing |
| content | `<statedir>/chat/<id>.msg`, temp+renamed because a torn read on NFS is real |
| fetch | `ssh <reachable host> cat …` — to the **container**, never the pool, and only when the doorbell changes |

⚠️ **The fallback needs a POSITIVE signal, not just a failure.** Three different failures reach the
same error, and only one means "use files" — all three measured: a sandbox answers `Operation not
permitted` with the socket right there; a wedged agterm client answers with a timeout; a pool machine
answers `No such file or directory`. Only the last falls back, and it is checked by looking at the
socket path directly rather than by matching an error string. `$TMUX` unset at all is *not* the pool
case — we cannot tell, so it refuses.

⚠️ **It only works for an agent that renders command output on its screen.** MEASURED: Codex does;
Claude Code does not render tool output at all, which is what killed the original screen-as-content
design. A Claude on such a pool would need something else again.

⚠️ `--chat-dir` must be **absolute**. The statedir is not under `$HOME` here — `$HOME` and the statedir routinely differ — so a `~` would expand to the wrong
place on the far side.

### Surviving `agb-refresh`

A refresh closes and re-mints every row, so **every row id changes** — observed twice in one
afternoon. What that does and does not touch:

| | survives | why |
|---|---|---|
| doorbell + message (tmux) | **yes** | they live on the agent's host; a refresh never touches the farm |
| the relay's resolved ids | **no** | dead the moment the rows are re-minted |
| an open `dashboard` grid, under `agb-peer relay --dashboard` | **effectively yes** | the cells are dead the instant ids are re-minted, but the relay re-resolves every tick and **re-opens** the grid when the cell set changes, so it repairs itself within a tick |
| an open `dashboard` grid, under `agb-dashboard` | **no** | that grid is a one-shot: it holds the ids resolved at open and does **not** follow. `--follow` is deferred, not forgotten |

So **name participants by label, not by row id** — `resolve` tries an exact id, then an id prefix,
then a title substring, and only the last survives a refresh. The relay re-resolves every tick and
re-opens the dashboard when ids move. A relay that cached ids would go permanently deaf, and its
only symptom would be silence.

⚠️ **The re-open trigger is the CELL SET, not the participant set** — `(name, id, pane)` for every
member — and it is computed whether or not membership changed. Two separate bugs lived in the older
"only when there is more than one participant, and only when the roster moved" reading: a drop to one
member left the departed member's cell on the screen, and a refresh that moved ids under unchanged
names changed nothing the relay was looking at. A drop to **zero** closes the grid, because agterm
has no empty one; a drop to **one** re-opens with the single cell, because one cell is valid
(measured).

```
agb-peer relay alice=<label>[:pane][@<ssh target>] bob=<label> [--dashboard]
agb-peer relay --roster <file> [--dashboard]     # the same, re-read as it changes
```

**`--roster <file>`** takes the participants from a file instead of the command line, one per line,
same grammar, `#` comments and blank lines ignored. It is re-read every tick, so agents can be added
and removed **without restarting the relay**. ⚠️ A `#` comment is a *whole line*, never a trailing
one: `<row>` is a row-title substring and may contain `#`.

⚠️ **Write the file atomically — edit a copy and `mv` it into place.** A file rewritten in place can
be read half-written, and a read truncated at a line boundary parses *cleanly* as a shorter roster,
which is indistinguishable from a real removal. An empty read is refused as no-information, but a
line boundary cannot be; the window is about a millisecond against an eight-second tick, and `mv`
closes it entirely.

### `agb-peer-setup` — build that file without typing the grammar

```
agb-peer-setup <roster-file>            # the interactive editor
agb-peer-setup validate <roster-file>   # would `agb-peer relay` START with it?
```

A picker over live agterm rows, plus one writer that gets the atomicity right. Menu:
`[a] add [d] delete [e] edit raw [v] view [w] write & exit [q] quit`.

⚠️ **The `<row>` it writes is the row's LABEL, not the title agterm shows.** The title is
`label · host · cwd · pane · beat` and a roster line is split on **whitespace**, so a title can
never be a spec; the `beat` field is an age that changes on every repaint; and the row **id** is
worse in a way that looks fine, because it works today and breaks at the next `agb-refresh`. So the
tool strips a leading `[?] ` or `[done] `, takes the first ` · ` component, refuses one containing
whitespace, `@` or `:`, and confirms the result resolves to **the row you picked** — not merely to
exactly one row, because an id-prefix match beats a label match and can be a different row entirely.

⚠️ **It assumes the default `row_fields` order**, where the label is first. With
`row_fields = host,label` it would propose the host; the uniqueness check is what catches that.

The transport prompt maps one-to-one onto what gets written, rather than onto "where is this peer",
which has no single answer for a row that agterm, ssh and tmux each locate differently:

| | writes | when it is offered |
|---|---|---|
| `[a]` the row's own host | no `@…` | **only** when `host_<name>` does not remap that host — see below |
| `[l]` tmux on this Mac | `@local:<tmux>` | always |
| `[s]` ssh to a tmux host | `@<target>` | always; offers this instance's `host_<name>` table |
| `[t]` ssh, explicit tmux | `@<target>:<tmux>` | always |

⚠️ **`[a]` is withheld when a `host_<name>` mapping applies, and that is not fussiness.** The relay
hands the row's `--host` to ssh **verbatim** — it never applies the mapping — so on a host that
needs an alias, `[a]` produces a roster that parses, validates, prints a working-looking next
command, and then silently never delivers.

⚠️ **`[a]` is missing for two different reasons, and the tool says which.** Besides the remapped-host
case above, a row that was **not opened through agbridge** carries no `agb pane` command, so there is
no host recorded on it to fall back to at all — a plain agterm shell, or a Mac-side agent nobody
clicked through to. Both drop the same option; the message tells you whether it is "this host is
remapped" or "this row has no host". Only a bridge-opened row whose host resolves to **itself** gets
`[a]` offered.

⚠️ **It rewrites the file as generated output, so `#` comments and blank lines are NOT preserved.**
An entry is the only thing the editor models, so a comment has nowhere to attach — it may belong to
the file, to the entry below it, or to the gap. It says so **when it opens the file**, not when you
save, because by then you would be choosing between losing the comments and losing the work.

**Writing is gated on the file's bytes.** If the roster changed since the session opened it, nothing
is written, your draft is saved to `<roster>.conflict` **before you are asked anything**, and you
get `[v]` view / `[r]` reload / `[q]` quit / `enter` back. Nothing is ever merged: these lines
encode a participant and a transport, not prose. An **unreadable** roster is a conflict too, for
the same reason — refusing to write is the only safe answer when the gate cannot be read.

⚠️ **Fewer than two participants is a warning, not a refusal**, because that is how you *remove*
somebody from a running relay. `validate` is the opposite and applies `minimum=2`, because it
answers "why will my relay not start".

The file is written `0600`, restated on every save, so a roster loosened by hand tightens again.

`agb-peer-setup` is **not installed by `install.sh`** — symlink it onto `$PATH` beside `agb-peer`.

⚠️ **`--roster` and positional participants together are refused** — two sources of truth have no
answer to *who is in this conversation* that does not depend on which one you read.

At **startup** an unreadable, missing, empty or malformed roster, or one naming fewer than two
participants, is a refusal. While **running**, every one of those *holds* the roster already in use
and says so once: at runtime none of them is evidence that anybody left. A drop to one participant is
allowed and announced, because people do leave.

Three consequences that are easy to miss, each with a test:

- ✅ **A message sent during the blackout is delayed, not lost.** While the rows are gone the relay
  can see nothing, but the doorbell and the message are in tmux on the agent's host and are
  untouched. When the row returns, the relay reads an id it has not seen and fetches. The
  screen-as-content design could never have done this — the content would have scrolled away while
  nobody was watching.
- ⚠️ **Every re-minted row comes back DETACHED**, showing `agb pane`'s menu — and a menu is not a
  tmux screen, so the status bar is absent and the doorbell cannot be seen. The relay **arms it**
  with the bare-newline attach rather than complaining and waiting, or every refresh would be a
  manual re-attach of every participant. Retried at most once in ten ticks, so an ssh that cannot
  connect is not hammered.
- ⚠️ **A label that matches no row is reported, not skipped in silence** — after three ticks, then
  every thirty. One missed tick is a refresh in progress; a hundred is a bridge that never came back,
  and a relay that said nothing would leave "gone" looking exactly like "quiet".

⚠️ **`automatic-rename` is `on` in tmux by default — MEASURED, and the doorbell depends on it being
off.** It survives today only because an explicit `rename-window` disables it as a side effect,
which is an undocumented dependency: if anything re-enables it, tmux overwrites the window name and
the relay goes deaf with no error anywhere. `send` therefore pins `automatic-rename off` explicitly
the first time it stores the base name.

`@<target>[:<tmux target>]` says where that agent's tmux lives; `@local` means this machine, and a Mac-side
participant then uses the identical mechanism minus the ssh. Without it the target comes from the
row's own `agb pane --host`, read out of agterm's `foreground` field — the same field that is
useless for mode detection is exactly right for this.

⚠️ **TWO things defeat the delivery check, and fixing one leaves the other.** A message that was
**pasted** is not on screen at all (`[Pasted text #1]`, `[Pasted Content 1461 chars]`); a message that was **wrapped** is on screen,
but agterm broke it across lines so a 40-character tail probe straddles the break and matches
nothing. The check strips all whitespace from both sides, which makes it wrap-immune — the same
reasoning the wire format uses, and which this check did not inherit for an embarrassing number of
hours.

⚠️ **Every pane read is the WHOLE VISIBLE SCREEN — no `--lines`, no `--all` — and reading a tail hid
Codex completely.** MEASURED: **Claude anchors its composer to the bottom of the pane; Codex draws
from the top and leaves the bottom blank.** So `--lines 40` finds Claude every time and missed
Codex's composer entirely, and `classify` answered `unknown` for a perfectly healthy Codex row that
the relay would then never deliver to. The doorbell kept working throughout, because tmux's status
bar is always the last line — two reads, two geometries, and only one of them was ever tested
against Codex.

The tail bought nothing anyway: the alternate screen has no scrollback, so `--all`, `--lines 400`
and the bare default all return the same lines.

✅ **Codex works as a peer, and needed one character.** MEASURED against codex-cli 0.149.1: its
composer glyph is `›` where Claude's is `❯`, and that is the **only** difference — the empty-composer
caret is column 2 for both, Enter submits for both, and a ~900-character injection is rendered in
full by Codex rather than collapsed to a placeholder, which makes it the *easier* case for the
delivery verification. `classify` accepts either glyph; nothing else changed.

⚠️ **`codex queue --thread <uuid> --message …` was measured working and deliberately NOT used.** It
delivers to a live TUI session with no composer at all and **queues** rather than interrupts, which
is genuinely safer — but delivery already rides agterm's own ssh, so that route would *add* an ssh we
do not otherwise need, plus a uuid-discovery problem and a second code path.
`docs/agtermctl.md` records it for the day the interrupt-vs-queue difference actually bites.

⚠️ **Whether a message is typed or pasted depends on the TRANSPORT, not the length alone.** Delivery
to a farm agent goes over `ssh` into tmux and trickles in slowly enough to be typed; delivery to a
local agent is tmux-inside-agterm and arrives fast enough to be pasted. So the same message can be
verifiable one way and invisible the other — a local pane is the harsher case, which is the reverse
of what you would guess. And the collapse is a **hybrid**: the head is typed literally and only the
tail becomes the placeholder, so a check matching the message's *tail* is exactly the one that fails.

⚠️ **Both agents collapse a long, fast injection into a placeholder** — the body is simply not
on the screen to verify against. MEASURED: Claude at 843 characters draws `[Pasted text #1]`, and
**Return submits it fine**: the Return was never the problem, the verification was, and it refused to
press it. So every long message needed a human to hit Enter, in both directions. `deliver` accepts a
paste placeholder as evidence — but only one that **was not there before typing**, or a single
earlier long message would make every later failure look like a success. agterm's own cookbook checks
for the same indicator.

⚠️ **The two agents spell the placeholder DIFFERENTLY, and that cost a real message.** Codex draws
`[Pasted Content 1461 chars]` where Claude draws `[Pasted text #1]`. `docs/agtermctl.md` had recorded
— measured, at ~900 characters — that Codex renders a long injection *in full*, and concluded that a
Claude-shaped mark was a harmless no-op there. At 1461 characters it is not: `deliver` found neither
the body nor a mark, raised exit 4, and **the relay drops exit 4 rather than retrying** (typing again
would leave two copies in the composer). The message sat in Codex's composer waiting for a human.
⚠️ **And the read is retried, because a long body is still arriving a second after it was typed.**
MEASURED: a ~2.8 KB delivery had rendered two paste placeholders and no tail one second in. A single
read cannot tell a half-rendered pane from a swallowed message, and it answers exit 4 — which the
relay **drops rather than retries**, so successive attempts piled up unsubmitted in the composer.
`deliver` now reads up to `VERIFY_READS` (4) times a second apart and stops at the first that
verifies; the prompt case still costs exactly one second, because a relay must not block.

`PASTE_MARKS` holds both spellings, each compared on its own count and **case-insensitively** —
`cat -A` on the pane read `Content` while the operator watching the same row read `content`, and case
distinguishes nothing on a composer. **A per-agent rendering read off one sample is a sample, not a
constant.**

⚠️ **A wedged agterm client wedges tmux, and `agb-peer` times out rather than hanging.** MEASURED:
while a `dashboard` had an unresponsive view-only client attached, `tmux list-clients` answered
instantly and every command that has to *notify* a client — `display-message`, `show-options -t`,
`rename-window` — blocked indefinitely. `send` hung with nothing written and no output. Every
subprocess is now bounded (30 s) and a timeout is reported as an error naming the command.

⚠️ **agterm spawns `bash --noprofile --norc`, so a pane inherits only what `login` gives it — NOT
your PATH.** Measured on macOS, and it bit three times in a row at three different depths: the row's
`--command` could not find `tmux`, then could not find `claude`, and then an agent running happily
*inside* tmux still could not exec `tmux` from a tool call. Use absolute paths in a `session new
--command`, and put `agb-peer` somewhere `login` already exports (`/opt/homebrew/bin`), not
`~/.local/bin`. `agb-peer` resolves tmux itself — `$AGB_TMUX`, then the usual homes, then the bare
name — so only the things it does not control need spelling out.

⚠️ **Every participant needs tmux, including on the Mac — and macOS ships without it.** This is by
construction, not an oversight: the doorbell *is* a tmux window name and the message store *is* a
tmux option. `brew install tmux`, then start the agent inside it:

```sh
agtermctl session new --name macbot --cwd "$HOME" \
  --command "tmux new -A -s macbot $(command -v claude)"
```

⚠️ **agterm closes a session the moment its command exits**, so a failure here makes the row appear
and vanish with nothing to read. `--wait` holds it open and prints the error — CONFIRMED live
2026-08-24, which is how `exec: tmux: not found` was found at all.

⚠️ **A Mac-native participant must name its tmux session**, e.g.
`macside=<label>@local:<tmux session>`. The pane id is otherwise read out of the row's
`agb pane --pane` argv, and an agterm session that is not an agbridge row has no such argv — its
`foreground` is a shell. Without the explicit target, `@local` could never work at all.

### The agent's half: `skills/agb-peer/SKILL.md`

An agent will not use any of this unless it is told to, so the repo ships one skill:

```sh
ln -s "$PWD/skills/agb-peer" ~/.claude/skills/agb-peer   # on every participant's machine
```

✅ **Codex reads the same format from `~/.codex/skills/`** — MEASURED: `~/.codex/skills/<name>/SKILL.md`
with `name`/`description` frontmatter, identical to Claude Code's, and a symlinked `agb-peer` showed
up in a live Codex's own list of skills. So the one file serves both:

```sh
ln -s "$PWD/skills/agb-peer" ~/.claude/skills/agb-peer     # Claude Code
ln -s "$PWD/skills/agb-peer" ~/.codex/skills/agb-peer      # Codex
```

**A symlink, not a copy**, which is how every other skill in this project is installed — a copy goes
stale the next time the repo moves forward and nothing says so. ⚠️ The target must be **absolute**:
a relative link resolves against `~/.claude/skills/`, not against your shell's directory.

⚠️ **Which is why the skill file contains nothing to edit.** An earlier draft had three
fill-in-the-blank lines; through a symlink, filling them in is a modification to the repository.
`agb-peer` is expected on `$PATH` (or `$AGB_PEER`), and the participant names are something the
agent is told or **asks the user for** — never guesses, because a message to an unknown name is
dropped.

⚠️ It lives in `skills/`, deliberately **not** in `.claude/skills/` where this repo's own `agbridge`
skill sits. That directory is for people *working on agbridge*; this one is for an agent *being a
participant*, and putting it there would offer peer-chat to anyone who opened this repository.

**One file, not one per agent** — agterm's cookbook needs two because each agent drives delivery
itself and they differ (Tab vs Return to submit, different command names). Here the relay absorbs
every difference, so the agent side is identical whether it runs on a cluster host or the Mac. That
is the same uniformity that made the screen the transport.

Three lines in it must be edited before first use: the path to `agb-peer`, the agent's own
participant name, and who it may write to. The names are the ones the relay was started with.

The rules in it are not decoration; two are lifted from agterm's cookbook because they are failures
somebody hit:

- ⚠️ **Never poll or wait for a reply.** If both agents wait on each other, nothing moves. Send and
  end the turn; the reply arrives as a prompt. This is the failure the arrangement is most prone to.
- ⚠️ **Never touch the peer's terminal directly** — no `agtermctl session type`, no `tmux send-keys`,
  no reading its pane. Delivery is gated on the peer not being mid-turn and its composer being
  empty; going around that types into whatever is on screen, including a permission dialog.
- **Send through `--stdin` and a quoted heredoc**, never as an argument: a shell mangles quotes,
  backticks and `$`, and a message is prose.
- **Print the message and let the turn end.** The lines *are* the message, and a large print
  immediately afterwards scrolls them out of the buffer before the relay reads them.

⚠️ **`send` refuses `--from`.** There is no sender field on the wire: the relay signs a message with
the participant name of the pane it was found in, because an agent cannot print into another agent's
pane, so the place is the only part of the envelope that cannot be misstated. A `--from` would look
like it changed that and could not, so it is an error rather than something ignored.

A test asserts every flag the skill names is a flag the parser has. The skill is prose an agent
follows literally and is never executed, so a rename on one side is otherwise silent.

### What it inherits from the cookbook, unfixed

The composer check cannot tell an empty composer from one whose caret was moved back over text —
agterm's own `surface cursor --help` says so. Sending to Claude Code **interrupts** rather than
queues. There is no transcript. And a model may simply decline to answer a perfectly delivered
message.

## `agb-dashboard` — watch several rows at once, by name

```
agb-dashboard <selector> [<selector> ...]   grid those rows
agb-dashboard --roster <file>               grid a relay roster's members
agb-dashboard --mru                         grid the rows you last used
agb-dashboard --version
```

agterm can show a **view-only grid** of live sessions, one cell per pane. `agtermctl dashboard`
takes row **ids** and never names, and `agb-refresh` re-mints every id — so driving it by hand means
looking ids up again every time. This resolves them fresh on every run, from the same
label/id-prefix/title-substring rule `agb-peer` uses.

⚠️ **Not installed by `install.sh`, and nothing in `agb`/`agb_mac`/`agb_ops` imports it.** Symlink it
onto your `$PATH` yourself, from the checkout, so `git pull` keeps it current:

```sh
ln -s "$PWD/agb-dashboard" ~/.local/bin/agb-dashboard
```

Same arrangement as `agb-peer` and `agb-peer-setup`, and inherited from them: it loads `agb-peer` by
path from beside itself, so the two travel together and `--version` is per-file. It runs on the
**Mac**, because that is where `agtermctl` is.

⚠️ **"Beside itself" is beside its REAL path, not beside the symlink** — `peer_path` resolves
`__file__` first, for exactly this reason. So the two symlinks need not live in the same directory,
which matters here: `agb-peer` belongs somewhere `login` exports (`/opt/homebrew/bin`) because an
*agent* invokes it inside an agterm pane, while this one you type yourself and `~/.local/bin` is
fine. Both resolve back into the checkout, where they are genuinely side by side.

| flag | |
|---|---|
| `<selector>` | a substring of the row's label, its id, or an id prefix — the tiers `agb-peer` resolves in. Prefer the label: an id dies at the next `agb-refresh` |
| `--roster <file>` | take the members from an `agb-peer relay --roster` file instead, panes and all. Parsed with a minimum of **one**, not the relay's two — a chat needs somebody to talk to, a grid does not |
| `--mru` | let agterm pick the most-recently-used sessions. Names nobody, so nothing is resolved |
| `--detach` | open the grid and exit, printing the literal command that closes it |
| `--version`, `--help` | answered before anything is loaded or resolved |

The three modes cannot be combined — they ask different questions, and there is no defensible way to
merge them, so preferring one silently would be a guess dressed as a feature.

**Exit codes**, measured rather than intended: **0** the grid opened (`--version` and `--help` too);
**2** a shortfall *this command* detected — an unresolved or ambiguous selector, no rows at all, too
many cells, a pane the grid cannot show, agterm *running and refusing* to open the grid, or agterm
opening a grid short of what was asked for; **1**
everything else, which is a usage error (a bad flag, no mode, two modes at once, or no arguments at
all, which prints the usage) **and also anything the shared `agb-peer` layer refuses** — an unreadable
or malformed roster, an agtermctl that will not start. ⚠️ Those arrive as `PeerError` and carry *its*
code, which is 1. This paragraph said 2 for "anything that stopped a grid opening" and no roster
failure has ever exited 2; the codes are now pinned to the number by tests rather than to "non-zero",
which is what let the two drift apart.

### Fail-closed: a shortfall opens nothing

> **The relay's grid is an adjunct to a message pump; this command's grid is the point.**

`agb-peer relay --dashboard` is best-effort by design — a cosmetic grid failure must never stop a
message. Somebody who typed `agb-dashboard alice bob` asked for the grid as the **primary effect**,
so it must fail loudly rather than half-succeed. Everything below follows from that one sentence.

| what happens | what you get |
|---|---|
| a selector matches no row | nothing opens, exit 2, the selector is named — and from a roster, **the participant too**: `carol (oldrow): no row matches it`. Two lines pointing at one dead label would otherwise produce two byte-identical refusals naming neither line to edit |
| a selector matches several | nothing opens, exit 2, and **its matches are listed** — the first five of them, so an over-broad selector against forty rows does not fill the terminal |
| agterm has no rows at all | its own message, rather than N identical "no row matches" lines — with no rows the answer is about agterm, not about what was typed |
| two selectors naming **one cell** | **deduped**, not refused, and the fold is **reported by name** — `(carol names the same cell as alice -- shown once)`. Two ways of naming one cell is not a user error; spending two of the nine on it is the bug, and dropping a name in silence is the other one |
| a `scratch` participant from a roster | nothing opens, exit 2, **naming the participant** — `drawer (scratch)`, not a row-id prefix: you wrote `drawer=…` and a hex prefix sends you to look up which line to edit. ⚠️ **Deliberately stricter than the relay**, which reports the same exclusion and grids the rest |
| more than 9 cells after the dedupe | refused **before** `agtermctl dashboard` is called (the `tree --json` has already run, by design). ⚠️ **Counted after the pane exclusion**, so the number is of cells that could actually go in a grid: ten participants of which two are `:scratch` is an eight-cell roster, and the cap used to refuse it with "got 10" while never mentioning the drawer |
| ⚠️ agterm prints `unresolved: <id>` | 🔴 **the dangerous one.** agterm exits **0**, opens the grid without those cells, and says so on **stdout alone**. This is the one place in the family where the exit status is not trusted: the output is read, the grid is **closed again**, and the run exits 2 |
| ⚠️ agtermctl does not answer at all | 🔴 **the other dangerous one, and it used to read as a refusal.** The call is killed at 30 s — a wedged tmux or agterm client does this — so whether agterm opened the grid is **unknown**. It says so (`the dashboard MAY BE UP`), prints `agtermctl dashboard --close`, closes **nothing**, and exits 2. A close would be a second blocking call into the same wedge, and with no proof this run opened the grid it would reach for one that may be somebody else's |

⚠️ **The close-before-exit is not tidiness.** `unresolved:` is printed *after* the grid is already
up, so refusing and exiting without closing would leave exactly the silently-partial grid this
command exists to remove — the headline feature shipping with its own bug. If the close itself
fails, it says so in capitals **and prints the literal command** — the same thing the foreground
hold does when its own close fails, and for the same reason: that is the moment you most need it
and least want to go and look it up. ⚠️ It did not, for as long as this paragraph said it did.

There is **no `--partial`**. Shipping one unrequested would re-introduce the behaviour being removed,
behind a flag nobody knows to avoid. ⚠️ `--mru` is the one exemption from all of this, and it is not
an oversight: there the user asserted no membership, so there is no set to fall short of, and a line
agterm printed about a row nobody requested would be reported as *our* failure and would close a grid
that is perfectly usable.

### The pane rule, and why the 9-cell cap counts panes

⚠️ **A cell is never a bare id.** agterm's cap is **9 cells and it counts PANES** — a bare id takes
*every* pane of its session, so a row somebody happened to open an `[s]` split on silently costs two
cells. The same rows would fit, or not, depending on state nobody is looking at.

Emitting an explicit pane always is what **turns a cap that counts panes into a cap that counts
agents**, which is the only reason the preflight above can say "9 cells; got 10" honestly instead of
guessing.

| where the pane comes from | cell |
|---|---|
| a bare positional selector | `<id>:left` — it names a row and says nothing about which half |
| `--roster`, participant in `left` | `<id>:left` |
| `--roster`, participant in `right` | `<id>:right` — ⚠️ **preserved, not forced.** Rewriting it would point the cell at the wrong half of somebody's screen |
| ⚠️ `--roster`, participant in `scratch` | **no cell.** agterm rejects `:scratch` at parse time — `invalid session id … use <id>, <id>:left, or <id>:right`, CONFIRMED 2026-08-27 — so here it is a shortfall and nothing opens |

That spelling lives in exactly one function, `dashboard_cells` in `agb-peer`, shared with the relay
and pinned by an AST guard spanning both files: a second caller growing its own copy is how the two
would drift apart.

### Lifecycle — something has to own the grid

agterm has **one** grid and no ownership token; its own `--close` closes *"the open one"*. So the
default is a **foreground hold**: open, print what was opened, wait, close.

```
$ agb-dashboard alice bob
agb-dashboard: 2 cell(s)
  A1B2C3D4 left alice · box01 · /w/api · %7 · 3s
  E5F6A7B8 left bob · box02 · /w/dv · %3 · 1s
  Run this from a terminal OUTSIDE agterm -- that is where the hold was
  measured to stay responsive while a grid is up.
  This grid does NOT follow: an `agb-refresh` re-mints every row id and
  leaves dead cells here. `agb-peer relay --dashboard` is the one that
  re-resolves and re-opens.
press enter to close the grid (Ctrl-C closes it too)
```

Enter, EOF and `Ctrl-C` all close it — the close is in a `finally`, because `Ctrl-C` is the
documented way out of a foreground wait and is not an `Exception`, so an `except` would miss it and
orphan the grid the hold exists to own.

⚠️ **And neither does a lost stdout.** `agb-dashboard alice | head` closes stdout the instant `head`
exits, and the next write raises — so everything printed *after* the grid goes up goes through a
writer that cannot raise, or the exception unwinds past the close and leaves a grid nobody owns.
Writes *before* the grid exists stay fatal on purpose: there is nothing to orphan yet, and a
`--version` that could not be printed has failed.

⚠️ **Run it from a terminal OUTSIDE agterm.** That is the condition the measurement holds under: with
a grid up, an external terminal stayed fully responsive and a blocking read returned on Enter
(CONFIRMED 2026-08-27). A grid **cell** is read-only to the keyboard, which is a different claim, and
whether a shell running *inside* an agterm session stays responsive is **untested** — the tool says
which route was measured rather than implying both work.

⚠️ **A held grid does NOT follow.** The cells carry the ids resolved at open, so an `agb-refresh`
under a held grid leaves dead cells — the documented limitation, not a crash. `agb-peer relay
--dashboard` is the one that re-resolves and re-opens; `--follow` here is **deferred by decision**,
and this is the one place the two commands genuinely differ in capability.

`--detach` is the explicit hand-over: it opens, reports, and exits, printing
`agtermctl dashboard --close` — precisely because after it, nothing else will.

⚠️ **Running this and `agb-peer relay --dashboard` at once is unsupported.** There is one grid, and
whoever opens last wins. Each keeps a latch saying it opened *a* grid — never *which*, because
`agtermctl dashboard --close` closes "the open one" and there is no way to name one. So neither
closes anything when it opened nothing, and each closes **whatever is up** if it did: the relay
replaces a held grid, and this command's hold then closes the relay's. ⚠️ Nor does the relay put its
own back — its re-open is gated on the cell set *changing*, so a replaced relay grid stays gone until
membership or the row ids move.

⚠️ **Not verified against a live agterm.** Every behaviour above is either measured against the
binary and recorded in [`agtermctl.md`](agtermctl.md), or covered by tests against a fake `Ctl`.
Nobody has yet watched this command open a real grid — see the Post-Completion list in the plan.

## `agb version`

```
agb version
```

No flags; any argument is ignored. Prints `agb <VERSION>` on stdout, exit 0. Load-bearing — see
[`design.md`](design.md) §0.
