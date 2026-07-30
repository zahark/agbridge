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
agb bridge [--from-stdin] [--no-agterm] [--feed-host H] [--mac-id M] [--statedir P]
           [--remote-path P] [--remote-python P] [--watchdog S] [--connections N] [--rows P]
```

| Flag | Default | Meaning |
|---|---|---|
| `--from-stdin` | off | read the NDJSON wire from stdin instead of spawning ssh. No feed host or mac-id is needed or looked up; one "connection", then exit. A test seam first, a debugging tool second |
| `--no-agterm` | off | consume and log the wire, touch no rows. This is what makes a transport problem diagnosable separately from a rendering one |
| `--feed-host <target>` | config `feed_host` | ssh target of the farm box. **Required**: with neither, the bridge refuses to start rather than starting and never connecting |
| `--mac-id <id>` | config `mac_id` | names `bridge/<mac-id>.beat`. **Required**, same reason |
| `--statedir <path>` | `$AGB_STATEDIR` → config `statedir`, **no default** | the **farm-side** statedir, sent across in `env AGB_STATEDIR=…`. Never expanded against the Mac's `$HOME`: `~` would resolve locally and then be shipped to a machine where it means something else. **Required** for exactly that reason — `agb`'s own `~/.agbridge` default is right for a process running *on* the farm and wrong for this one, so the bridge asks rather than inventing a path that is correct on the wrong machine |
| `--remote-path <path>` | config `agb_remote_path` → `/opt/agbridge/agb` | absolute path of `agb` on the farm |
| `--remote-python <path>` | config `remote_python` → `/bin/python3` | absolute farm-side interpreter (`ssh host cmd` sources no profile) |
| `--watchdog <seconds>` | `10.0` (five poll intervals) | no line at all — **including a tick** — for this long means the feed is dead: mark every row stale and reconnect. Must be > 0 |
| `--connections <n>` | unset — reconnect for ever | stop after `n` connections. Must be ≥ 1 |
| `--rows <path>` | `~/.config/agbridge/rows` | the persisted `key → agterm row` map, on the Mac |

## `agb close-done` — Mac

```
agb close-done [--rows PATH] [--dry-run]
```

| Flag | Default | Meaning |
|---|---|---|
| `--rows <path>` | `~/.config/agbridge/rows` | the row map to read and rewrite |
| `--dry-run` | off | print what would be closed, close nothing, rewrite nothing |

Only `[done]` entries are touched; a bound row is never closed. A row is forgotten only if
`agtermctl` reports it closed — otherwise it stays in the map and is printed as "close by hand".

## `agb pane <key>` — Mac

```
agb pane <key> --host <host> [--tmux <session>] [--pane %N] [--cwd <path>] [--jump <host>]
```

This is the row's own command, built by the bridge; the flags mirror `agb_mac.pane_argv` word for
word, and the two are tested against each other.

| Flag | Default | Meaning |
|---|---|---|
| `<key>` (positional) | — | **required**, and validated as a minted key |
| `--host <hostname>` | — | **required**. The Mac cannot read the shared statedir, so the identity has to arrive on the command line. It is a *hostname*; `host_<name>` in the config maps it to an ssh target, and without such a key the hostname is used as the target |
| `--tmux <session>` | none | the tmux session to attach to. **Without it there is no attach at all**: `pane` prints the identity, says so, and exits 0 without prompting |
| `--pane %N` | none | tmux pane id, `%` plus digits. Accepted without `--tmux` (that record exists), and then rendered in the identity like any other field |
| `--cwd <path>` | none | the agent's working directory, used by `[s] shell` below so the split pane opens there. Optional: rows created before it existed carry none, and the shell then lands wherever ssh does |
| `--jump <host>` | config `jump_host` | ssh jump host for machine #3. The bridge's hint wins over the config, and either is dropped when it names the resolved target or the `--host` value itself — hopping through the box you are already going to |

### The prompt

```
[enter] attach   [s] shell   [q] quit >
```

| Key | Effect |
|---|---|
| **enter** | `ssh -t <target> 'tmux select-window -t %N ; tmux select-pane -t %N ; exec tmux attach-session -t <session>'`. Runs in a **loop**, not `exec`: detaching returns to this prompt instead of closing the row's terminal. A non-zero exit is reported and prompted again rather than being fatal |
| **s** / `shell` / `split` | opens agterm's split pane beside this one and starts `ssh -t [-J <jump>] <target> 'cd <cwd> && exec $SHELL -l'` in it. Both panes belong to the same row |
| **q** / `quit` / `exit`, or EOF | leaves without attaching. Changes nothing about the agent |

`[s]` is two `agtermctl` calls in a fixed order — `session split on --target active`, then
`session type --target active --pane right` — because `--pane right` is an error when there is no
split yet. `on` rather than `toggle`, since pressing `s` twice would otherwise close the pane.
`--target active` needs no row id: this command is running *inside* the row's own session.
A missing or failing `agtermctl` costs the row its split and nothing else.

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

## `agb forget-rows` — Mac

```
agb forget-rows [--key <key>]... [--rows <path>] [--dry-run]
```

Drops `key → row` bindings so the next snapshot re-creates the rows. The recovery for **agterm
having forgotten its rows** — closed, reset or reinstalled — while the map still names them. Every
`rename`/`status` then fails with `error: no such session`, and nothing else clears a *bound* entry:
`close-done` only touches `[done]` rows, and `prune` works from farm-side state.

| Flag | Default | Meaning |
|---|---|---|
| `--key <key>` | every binding | forget one; repeatable. Use it when only one row was closed — dropping the whole map mints duplicates for rows that are still live |
| `--rows <path>` | `~/.config/agbridge/rows` | the map |
| `--dry-run` | off | name the bindings and change nothing |

Exits 1 if a named key was not in the map (and says so), 0 otherwise. **Nothing on the farm is
touched** — agents, keys and state are untouched, so rows return with the same identities.

⚠️ Not a file deletion, and not a `sed`. The map ends in an `#end <count>` sentinel; a hand-edited
line leaves the count wrong, the whole map then reads as corrupt, and the bindings you meant to keep
go with it. Stop the bridge first — it holds the map in memory and merges-then-writes on every save.
`agb-refresh` does the whole sequence.

## `agb-refresh` — Mac, a convenience

```
agb-refresh [--key <key>]... [--dry-run] [--label <name>] [--agb <path>] [--rows <path>]
```

Stop the bridge → `agb forget-rows` → start it again. A separate POSIX-sh script, installed beside
`agb` on the Mac. It restarts the bridge **whatever the middle step reported**: leaving it down
because one key was not in the map would turn a small surprise into a dark sidebar. A bridge that
was already stopped is a fine starting state, not an error.

## `agb-claude [name]` — farm, a convenience

```
agb-claude [name] [-- <claude args>...]
```

Not part of `agb` — a separate POSIX-sh script beside it. It starts Claude Code inside a **named
tmux session**, which is what makes the resulting row attachable.

| Argument | Default | Meaning |
|---|---|---|
| `name` | the current directory's name | tmux session name. `.`, `:` and spaces become `-`, because tmux cannot address them as a target |
| `--` | — | everything after it is passed to `claude` untouched |

Re-running with the same name **attaches** to the existing session rather than starting a second
agent in it, matched exactly (`-t "=name"`), so `agb-claude api` will not attach to `api-refactor`.
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
