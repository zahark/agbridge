# `agb` — command and flag reference

Every flag of every command, with the default the code actually uses. Companion to the command table
in [`../README.md`](../README.md) and to [`design.md`](design.md) §0.

## `agb <command> --help` is not implemented

There is no per-command help. `agb doctor --help` answers
`agb: doctor: unknown option: --help` and exits 1, because every parser here is hand-rolled and
rejects anything it does not know. `agb`, `agb -h`, `agb --help` and `agb help` print the list of
command *names* on stderr and exit 2; that list carries no flags.

This is **deferred, not an oversight.** `argparse` is a ~10 ms import on a hot path measured in
milliseconds (constraint #3), so there are nine hand-rolled parsers — `feed`, `bridge`,
`close-done`, `pane`, `doctor`, `prune`, `status-line`, `install-hooks`, `install-config` (`hook`
reads one positional and parses nothing) — spread over three files, and a `--help` arm would have to
be added, and kept correct, in each. `agb` is also within a few hundred bytes of the parse-cost cap
that three consecutive tasks declined to raise. **This file is the reference instead.** If `--help`
is ever added, it is added here first.

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
agb close-done [--config PATH] [--rows PATH] [--dry-run]
```

| Flag | Default | Meaning |
|---|---|---|
| `--config <path>` | `~/.config/agbridge/config` | which instance's map to work on; `--rows` is derived from its directory |
| `--rows <path>` | derived from `--config` | the row map to read and rewrite. An explicit path wins |
| `--dry-run` | off | print what would be closed, close nothing, rewrite nothing |

Only `[done]` entries are touched; a bound row is never closed. A row is forgotten only if
`agtermctl` reports it closed — otherwise it stays in the map and is printed as "close by hand".

**It says which map it is acting on, on every run**, before anything else including the `--dry-run`
exit:

```
close-done: config /Users/you/.config/agbridge/hostb/config; rows /Users/you/.config/agbridge/hostb/rows
```

Unconditional, because the failure it guards is silent: run without `--config` while meaning a
second instance and this reclaims the *default* instance's rows and reports success in exactly the
words you were expecting.

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

Sets the **label** a row is titled from (`label · host · cwd · pane`).

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
                   [--generate-mac-id] [--print-mac-id] [--dry-run]
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
| `--dry-run` | off | print the full report and write nothing |

With no `--mac-id`, no id in the file and no `--generate-mac-id`, the command **fails** and explains
why. The merge preserves comments, layout and keys this tool does not know; the text it is about to
write is re-parsed and refused if it would not read back as the values reported. The previous file is
copied to `config.agb.bak`, and a run that would change nothing writes neither.

## `install.sh mac --instance <name>` — Mac, a second machine

```
sh install.sh mac --instance <name> --statedir <path> --feed-host <target> \
                  --agb-remote-path <farm path> [the usual options]
```

A **machine that shares no disk with the first** needs its own statedir, so it needs its own feed and
its own bridge. `--instance` is that install: an independent launchd job whose rows appear in the
same agterm sidebar. It is **sugar over three flags that already existed**, and passing any of them
explicitly wins over the sugar:

| | default install | `--instance hostb` |
|---|---|---|
| `--config` | `~/.config/agbridge/config` | `~/.config/agbridge/hostb/config` |
| `--label` | `com.agbridge` | `com.agbridge.hostb` |
| `--log-dir` | `~/Library/Logs/agbridge` | `~/Library/Logs/agbridge/hostb` |

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

Three refusals, each because the alternative is silent:

| Refused | Why |
|---|---|
| `--instance` without `--statedir` | it would fall back to the **default** config's statedir: ssh to the right machine, read the wrong directory, then create it and report an empty farm for ever |
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

⚠️ **It is a word you type, and it can never be the default.** Re-running `install.sh mac` with the
original flags is how you pick up new code, so an **absent** `--instance` has to keep meaning the
default instance. If it meant "name it after whatever the feed host calls itself", every upgrade of
an existing install would mint a *new* instance beside it — new config, new launchd job, new rows
map, and every row duplicated in the sidebar.

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

Then repair it with `agb-refresh --instance <name>`, and read
[`design.md`](design.md) §5, *One Mac, several instances* for the seven limitations — the first of
which is that a helper run **without** `--instance` acts on the default one and reports success.

## `agb forget-rows` — Mac

```
agb forget-rows [--key <key>]... [--config <path>] [--rows <path>] [--dry-run]
```

Drops `key → row` bindings so the next snapshot re-creates the rows. The recovery for **agterm
having forgotten its rows** — closed, reset or reinstalled — while the map still names them. Every
`rename`/`status` then fails with `error: no such session`, and nothing else clears a *bound* entry:
`close-done` only touches `[done]` rows, and `prune` works from farm-side state.

| Flag | Default | Meaning |
|---|---|---|
| `--key <key>` | every binding | forget one; repeatable. Use it when only one row was closed — dropping the whole map mints duplicates for rows that are still live |
| `--config <path>` | `~/.config/agbridge/config` | which instance to repair. **Both** `--rows` and `--placements` are derived from its directory |
| `--rows <path>` | derived from `--config` | the map. An explicit path wins |
| `--placements <path>` | derived from `--config` | remembered `key = workspace` file. An explicit path wins |
| `--no-close` | off | leave agterm's sessions open. They become duplicates as soon as the bridge mints fresh rows, so this is for the case where you are about to close them yourself |
| `--dry-run` | off | name the bindings and change nothing |

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

Exits 1 if a named key was not in the map (and says so), 0 otherwise. **Nothing on the farm is
touched** — agents, keys and state are untouched, so rows return with the same identities.

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
| `--key <key>` | every binding | forget one; repeatable. Passed straight through to `forget-rows` |
| `--dry-run` | off | say what would happen; stop and start nothing, change nothing |
| `--no-close` | off | passed through to `forget-rows`: leave agterm's sessions open |
| `--instance <name>` | the default instance | act on the instance `install.sh mac --instance <name>` created: label `com.agbridge.<name>`, and the config **that instance's plist names**. **Sugar for `--label` and `--config`, which must always move together** — a label without its config stops one instance and forgets another's bindings. An explicit `--label`/`--config` still wins. Same name rule as the installer: letters, digits, `-`, `_` |
| `--config <path>` | the config `<label>.plist` was rendered with; failing that `~/.config/agbridge[/<name>]/config` | the config to repair against; `forget-rows` derives the rows map and the placements file from it. Accepted on its own for an install made with `install.sh mac --config <path>` and no `--instance`, which has no instance *name* to pass. **Given alone it also moves the label**: the plists are scanned for the job that holds **the map this path names**, and its label adopted, so the bridge's own `agb-refresh --config …` hint acts on the bridge that printed it. The match is on the **map**, not on the spelling: the **directory** is resolved (`//`, `.`, `..`, a relative path, a symlinked `$HOME`) and **nothing else is matched on**, because `rows` and `placements` are `dirname(config)/rows` and `dirname(config)/placements` — the basename plays no part in either. So `<dir>/config`, `<dir>/`, `<dir>//` and `<dir>/anything` all name the same map and all find the same label; keeping the basename made the match *narrower* than the map it guards, and `--config ~/.config/agbridge/hostb/` (what tab-completion leaves you holding) bounced the **default** job with hostb's map. Among several matches the basename does break the **tie** — the job naming this exact file wins over one that merely shares the directory, rather than the winner falling out of `*.plist`'s collating order. A plist carrying no `--config` counts as naming the **default** config, since that is what its bridge resolves; only if it is readable and its label is in the `com.agbridge` space, so another program's LaunchAgent cannot claim agbridge's map. A path whose directory does not exist has no canonical form and matches nothing. When more than one job claims the map, all of them are named, the chosen one is said, and the run continues — they share a rows file, so there is nothing to choose. If no plist claims it, the default label is used and a `note:` says so — and names the job it is about to bounce |
| `--label <name>` | `com.agbridge` | the launchd job to boot out and back in |
| `--launch-agents <dir>` | `~/Library/LaunchAgents` | where that job's plist lives |
| `--agb <path>` | `~/.local/lib/agbridge/agb` | the installed `agb` to run `forget-rows` through — **and the tree the plist reader asks what each job's arguments mean**: it loads `agb_mac.parse_bridge_args` from this file's directory rather than imitating it, so an argv `agb bridge` would refuse (`--config=` with no value, a stray positional, an unknown option, a `--watchdog` that is not a number) counts as naming no config, which is what a job launchd restarts for ever without ever starting a bridge holds. A tree with no `agb_mac` beside this path is **fatal** and says so: `agb forget-rows` could not run there either |
| `--python <path>` | the first `python3` on `$PATH` | interpreter to run it with. `agb` has no shebang on purpose |
| `--rows <path>` | derived from `--config` | passed through to `forget-rows` |

⚠️ **`--instance <name>` does not mean `~/.config/agbridge/<name>/config`** — it means *whatever
that instance's plist was rendered with*. `install.sh mac --instance hostb --config <elsewhere>` is
supported, and rebuilding the conventional path instead would name a file that does not exist:
`forget-rows` answers `no rows to forget: the map is already empty`, exits 0, and the recovery
command reports success for a map it never touched. The convention is the fall-back for a Mac whose
plist was never rendered.

**It names what it is acting on, first line, every run** — including under `--dry-run`:

```
instance: hostb -- label com.agbridge.hostb, config /Users/you/.config/agbridge/hostb/config
instance: (default) -- label com.agbridge, config /Users/you/.config/agbridge/config
```

⚠️ This is the whole mitigation for the one real hazard of running several instances: without
`--instance` this command stops `com.agbridge`, forgets the **default** instance's bindings and
restarts it — succeeding, and saying so in exactly the words you were expecting for the instance you
meant. Nothing can detect the intent, so it states the answer instead.

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
  there; anything else means the *reader* failed, which is a statement about `$python`. Folding the
  last two into the second made `--python /bin/false` — an ordinary mistake — read every plist as
  silent, fall through to the default label and the conventional config, and report success. Both are
  fatal now, at all three call sites: `plist_read_ok` spells the rule once — **2 is an answer,
  anything else is fatal** — and names `--agb` for 3 and the interpreter for the rest. An
  `import plistlib` probe runs once before any plist is read to catch the case a status cannot see
  (`--python /bin/echo` exits 0 and prints its own arguments).
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

## `agb-claude [name]` — farm, a convenience

```
agb-claude [name] [-- <claude args>...]
```

Not part of `agb` — a separate POSIX-sh script beside it. It starts Claude Code inside a **named
tmux session**, which is what makes the resulting row attachable.

| Argument | Default | Meaning |
|---|---|---|
| `name` | the current directory's name | tmux session name. `.`, `:` and spaces become `-`, because tmux cannot address them as a target |
| `-d`, `--detach` | off | start it in the background and return immediately, with the row already showing |
| `--greet <text>` | `hi` | the opening prompt `-d` gives Claude. Refused without `-d`, where it would be silently ignored |
| `--` | — | everything after it is passed to `claude` untouched. **Required for anything starting with `-`**: `agb-claude --resume <id>` is refused, `agb-claude work -- --resume <id>` is not |

### `-d`, and why it needs a greeting

A row appears on the first **hook**, not at launch, so a session started and left alone writes
nothing and stays invisible. `-d` therefore hands Claude an opening prompt — answering it fires
`UserPromptSubmit`, which mints the key and creates the row. The prompt goes **last**, because
`claude [options] [prompt]`.

```sh
agb-claude -d api-refactor                    # row appears, nothing to detach from
agb-claude -d api-refactor --greet "ready?"
```

⚠️ **It does not work in a directory Claude has not been trusted in yet.** Claude stops on *"Is this
a project you trust?"* and waits, so nothing is submitted and no row appears. That prompt is
deliberately **not** answered for you — it is a security decision belonging to the human, and a
wrapper that clicked through it would be doing what this tool exists to stop. Attach once
(`tmux attach -t <name>`), answer it, and `-d` works there from then on.

Re-running with the same name **attaches** to the existing session rather than starting a second
agent in it, matched exactly (`-t "=name"`), so `agb-claude api` will not attach to `api-refactor`.
Nothing is restarted in that case, so any `--` arguments cannot take effect — they are reported as
ignored rather than dropped silently.
Run from inside tmux it creates a *sibling* session and switches: a nested session cannot be
attached to from outside, and agbridge would record the outer session's name for the inner agent.

It exists because the tmux session name is resolved **once**, at the agent's first hook, and never
refreshed — so naming has to happen before the agent starts, and forgetting is easy.

## `agb version`

```
agb version
```

No flags; any argument is ignored. Prints `agb <VERSION>` on stdout, exit 0. Load-bearing — see
[`design.md`](design.md) §0.
