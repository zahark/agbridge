# `agtermctl` — the contract agbridge codes against

`agtermctl` is a **Mac-only** binary shipped with [agterm](https://github.com/umputun/agterm). It
cannot be run or `--help`'d from a Linux host: it does not exist there. Everything below was either
derived from the design and later confirmed against a live agterm, or captured verbatim on a Mac —
each clause says which.

This file therefore has two layers, and they are kept **visually separate on purpose**:

1. **The assumed contract** — what Tasks 4b, 8 and 9b code against *today*. Every clause is tagged
   with its evidence class.
2. **The verbatim recording** — `--help` output captured on the Mac. `session split` and
   `session type` are recorded below.

**As of 2026-07-30 every clause this tool depends on has been exercised against a live agterm.**
What remains `ASSUMED` is narrow and marked: the CLI *spelling* of `--blink`/`--auto-reset` (the
underlying arguments are confirmed from `agr`'s wire messages), and that `session rename` may be
called repeatedly — which the bridge does on every update, so a failure would be loud and constant
rather than subtle.

When the recording lands, reconcile layer 1 against it and update `tests/stubs/agtermctl`
(created by Task 4b). Nothing in the plan blocks on that: the assumed contract is complete enough
to implement and to stub.

## Evidence classes

| Tag | Meaning |
|---|---|
| **CONFIRMED** | Observed directly — in a live config, in the `agr` source, or measured on this box |
| **ASSUMED** | Not observed; chosen because it is the smallest contract that satisfies the design. Verify on the Mac |

## The assumed contract

### `agtermctl session new [--cwd <path>] [--name <name>] [--command <cmd>]`

- **CONFIRMED**: the subcommand exists and accepts `--cwd`, `--name`, `--command`.
- **CONFIRMED** (2026-07-29, first live run): it prints the **new row id on stdout** and the bridge
  round-trips it through `--target`. Observed ids `E9E28BA8-C5A6-4664-8E70-D4F06199BA5C` and
  `46178B23-60AC-4773-B136-EE28F20BFDAD` — uppercase UUIDs, matching the shape `agr`'s target files
  suggested. This was the single largest assumption in this file; it held.
- **CONFIRMED**: exit 0 on success, non-zero on failure — a `rename`/`status` naming an id agterm
  has forgotten exits **1** with `error: no such session: <id>` on stderr.

The bridge nevertheless treats the id as an **opaque string**: never parsed, never generated, only
stored and echoed back via `--target`. A UUID today is an observation, not a guarantee.

Verbatim from `agtermctl session new --help` (2026-07-30), the flags agbridge uses:

```
  --cwd <cwd>              Working directory (defaults to $HOME).
  --workspace <workspace>  Target workspace by id/prefix/active (defaults to the current one).
                           Mutually exclusive with --workspace-name.
  --workspace-name <name>  Target workspace by name; errors if not found unless --create-workspace.
  --create-workspace       With --workspace-name, create the workspace when it does not exist.
  --command <command>      Run this command as the session's process instead of the login shell.
  --name <name>            Initial session name.
  --no-select              Create the session in the background without selecting or focusing it
                           (leaves the current selection untouched).
```

agbridge uses it as:

```
agtermctl session new --name '<label> · <host> · <cwd> · <pane> · <beat-age>' --cwd <cwd> \
  --command '<python> -S -E <agb> pane <key> --host <host> [--tmux <session>] [--pane %N] [--jump <j>]' \
  --no-select [--workspace-name <name> --create-workspace]
```

Two flags there are not decoration:

- **`--no-select`** — without it every row creation takes the screen. An agent starting on the farm
  is something to notice in the sidebar, not an interruption, and a refresh recreating several rows
  would otherwise yank the selection once per row.
- **`--workspace-name` + `--create-workspace`**, by *name* rather than id: an id is not something a
  human puts in a config file, and creating-if-absent makes the setting idempotent. Without it
  agterm uses whichever workspace is current, so rows recreated by a refresh land wherever the
  operator happened to be looking. `--workspace` and `--workspace-name` are mutually exclusive.

Two things about that invocation are easy to get wrong:

- **`--name` receives the full row title, not the label.** It is the same `row_title()` string a
  later `session rename` receives — label, host, cwd, pane and the beat age, joined by ` · `, with
  the `[?]`/`[done]` prefix when one applies. A row is titled correctly from the moment it exists,
  so a bridge that dies before its first rename still leaves a legible sidebar.
- **The command begins with an absolute interpreter and `-S -E`**, always. `agb` has no shebang and
  is not executable (design.md §5, and the code says so at `pane_argv`), so a bare `agb pane …` row
  command would simply never run — and `./agb` could not pass the interpreter flags anyway. The
  string is built with `shlex.quote` per word, because agterm hands `--command` to a shell.

Note what this costs Fallback tier 2 below: `--name` is **already carrying** the title, so
"create with `--name <key>`" is a **behaviour change**, not a spare slot. Taking it means the row's
name becomes the minted key and every rendering that rides on the title — beat age, `[?]`, `[done]`
— has to move somewhere else or be dropped.

### `agtermctl session rename <title> --target <id>`

- **CONFIRMED** — exact form, from `agr:60`:
  `agtermctl session rename "$name" --target "$AGTERM_SESSION_ID"`.
- **ASSUMED**: it may be called **repeatedly** on an already-created row, and the new title takes
  effect immediately.

This is the single mechanism behind three separate design requirements, which is why its fallback
matters more than any other (see *Fallbacks*):

| Rendering | Title form |
|---|---|
| normal | `<label> · <host> · <cwd> · <pane> · <beat-age>` |
| feed dead (amendment 2) | `[?] ` prefix |
| `remove` received | `[done] ` prefix |

### `agtermctl session status <state> --target <id> [--blink] [--auto-reset]`

- **CONFIRMED** — the status vocabulary is exactly **`active` | `blocked` | `completed` | `idle`**.
  ⚠️ **There is no `unknown`.** Anything outside this set is a bug, and the Task 4b stub must exit
  non-zero when it sees one.
- **CONFIRMED** — `blink` and `autoReset` exist as status arguments. `agr` reaches agterm through
  the control socket rather than the CLI, and its wire message (`agr:240`) is:

  ```json
  {"cmd":"session.status","target":"<id>","args":{"status":"active","blink":true,"autoReset":true}}
  ```

  so the CLI flags are **ASSUMED** to be spelled `--blink` / `--auto-reset` (which is how `agr`'s
  own front-end spells them, `agr:213`).
- **ASSUMED**: `--target` is required, and setting a status is **level, not edge** — re-applying the
  same status is a harmless no-op. The bridge relies on this: it repaints from every snapshot.

### `agtermctl session close --target <id>`

- **CONFIRMED** (2026-07-30, live): `agb close-done` closed a `[done]` row and agterm removed it
  from the sidebar. Also reached from `forget-rows`, which closes each session as it forgets it.
- The degradation is kept anyway, because it costs nothing and covers a future agterm that drops the
  subcommand: on a non-zero exit the entry stays in the map and is printed for the operator to close
  by hand. It must never be emitted on the `remove` path.

### `agtermctl session split [on|off|toggle] [--target <id>]`  — **CONFIRMED**

Verbatim from `agtermctl session split --help` (2026-07-29):

```
OVERVIEW: Show or hide a session split (on|off|toggle).
USAGE: agtermctl session split [<mode>] [--target <target>] [--socket <socket>] [--json] [--window <window>]
  <mode>     Mode: on (show), off (hide), or toggle (default). Hidden panes stay alive. (default: toggle)
  --target   Target session/workspace id, unique prefix, or 'active'. (default: active)
```

agbridge uses **`on`**, never the default `toggle`: `[s] shell` can be pressed twice and a toggle
would close the pane the second time.

### `agtermctl session type [<text>] [--pane left|right|scratch] [--target <id>]` — **CONFIRMED**

```
OVERVIEW: Inject text into a session.
USAGE: agtermctl session type [<text>] [--stdin] [--select] [--pane <pane>] [--target <target>] ...
  --select   Select (and realize) a never-shown session before injecting
             (main pane only; a split pane must already exist).
  --pane     Which pane to type into: left (main), right (split), or scratch. Defaults to left.
```

Two consequences, both load-bearing:

- **The split must exist before anything is typed into it.** `--pane right` is an error otherwise,
  and `--select` — the flag that realizes a never-shown session — is documented as *main pane only*.
  That is why `open_split` is two calls in a fixed order, and why it stops if the first fails.
- **It injects keystrokes into a shell, not an argv.** What arrives has to be a valid shell *line*,
  ending in a newline or nothing runs. `split_shell_line` quotes every word for that reason.

It also rules out doing this automatically at row creation: a row the human has never clicked is a
never-shown session, and `--select` cannot realize its split pane. So the split is offered by
`agb pane`'s prompt — which runs *inside* the row's own session, making it the active one, so
`--target active` needs no row-id lookup at all.

## Decisions this file settles

### `--blink`: adopted for `active`, **transitions only**

The live config on this box (`~/.claude/settings.json`, **CONFIRMED**) is:

| Event | `agr` command |
|---|---|
| `UserPromptSubmit` | `agr status active --blink` |
| `PostToolUse` | `agr status active --blink` |
| `Notification` (`permission_prompt`) | `agr status blocked` |
| `Stop` | `agr status completed --auto-reset` |

agbridge keeps `--blink` on `active` so the sidebar behaves the way the user's eyes are already
trained, **but only on an actual transition into `active`** — never on a snapshot repaint or a
reconnect resync.

The reason is a genuine unknown: it is not established whether `blink` is a **sticky attribute** or
a **one-shot animation trigger**. If it is one-shot, re-sending it on every snapshot would make a
row that has been quietly `active` for an hour flash on every bridge reconnect. Gating on
transitions is correct under both readings, so the ambiguity costs nothing and need not be resolved
before Task 4b. Record the answer here when the Mac is available.

### `--auto-reset`: **deliberately dropped**

`agr` passes it on `completed`. agbridge does not, and this is a design decision rather than an
oversight:

`auto-reset` hands agterm the authority to change a row's displayed status on **its own timer**,
with no notification back to the bridge. The bridge is a level-state machine that believes the row
still reads `completed`; agterm has silently repainted it to no-glyph. That is precisely the
divergence the `[done]` marker exists to prevent — an auto-reset row is pixel-identical to a live
idle agent, and the bridge cannot tell that it happened.

It also buys nothing here. agr needed it because a pushed edge is all agterm ever gets; agbridge
re-asserts the full level state on every snapshot and clears the glyph explicitly on `remove`.

Consequence: `completed` persists until the next transition or until `remove` repaints the row to
`idle` + `[done]`.

### The bridge never generates or parses a row id

It is stored opaquely in the persisted `key → row` map. This keeps the bijection invariant
independent of agterm's id format.

### An `agtermctl` failure must never wedge the bridge

Every invocation is best-effort: non-zero exit and stderr are recorded, the bridge continues, and
the next snapshot repaint re-applies the intended state. This is Task 4b's "an `agtermctl`
invocation failing must not wedge the bridge" checkbox, stated as a contract rather than as an
implementation detail.

## Fallbacks

Recorded now so that a surprise on the Mac is a localized edit rather than a redesign.

### If `session new` does not print the row id

Tier 1 — **enumerate and diff**: snapshot the row set before and after creation and take the
single new element. Requires a listing command (assumed `agtermctl session list`, printing one row
per line with the id as the first field). Serialize row creation so the diff is unambiguous — the
bridge is single-threaded, so this is free.

Tier 2 — **address by name**: create with `--name <key>` (the minted key is unique by construction)
and target by name if `--target` accepts one. ⚠️ Not a no-op: `--name` currently carries the row
title (above), so this tier trades the title away and needs the `[?]`/`[done]`/beat-age rendering
re-homed — combine it with the degraded behaviour listed under *If there is no updatable title
mechanism*.

Tier 3 — **operator binding**: `agb bridge` prints the key and asks for the row id once per row,
persisting it. Ugly, but never wrong, and it keeps the tool usable while the contract is fixed.

### If there is no updatable title mechanism

This is the expensive one: `[?]`, `[done]` and the beat-age display all ride on `session rename`.
Without it, `idle` + `[?]` collapses to bare `idle`, which is indistinguishable from both a live
idle agent and a removed row — the dashboard-that-lies failure in a new costume.

Degraded behaviour, in order of preference:

1. Set the title **once at creation** via `session new --name`, embedding `label`/`host`/`pane`.
   Static parts survive; `[?]`, `[done]` and beat age do not.
2. On `remove`, call `session close` for that row **immediately** instead of leaving an
   indistinguishable `idle` row. This is sound here for the reason Task 4b already gives for
   `close-done`: the Mac-side pane runs `agb pane`, which only prints identity and waits, so
   there is no agent scrollback to preserve. `design.md` §3's "keep the pane" rationale does not
   apply to it.
3. On feed death, keep the notification and repaint to `idle`, and have `agb doctor` state plainly
   that the title mechanism is unavailable — so the operator knows the sidebar has lost its ability
   to distinguish stale from idle, instead of discovering it after trusting it for an hour.

## What the Task 4b stub must enforce

`tests/stubs/agtermctl` is a recording stub, and its job is to fail loudly on anything this
contract forbids:

- exit **non-zero** on any `session status` outside `active|blocked|completed|idle` — the stub must
  not validate a fiction such as `unknown`
- print a unique, opaque id on `session new`
- append every invocation's argv to a recording file for assertions
- accept `--target` on `rename`, `status` and `close`, and fail if it is missing

## Verbatim recording *(from the Mac — NOT YET RECORDED)*

Run these on the Mac and paste the output verbatim under each heading. Then reconcile the assumed
contract above and the stub, keeping the vocabulary rejection.

```sh
agtermctl --help
agtermctl session --help
agtermctl session new --help
agtermctl session status --help
agtermctl session rename --help
agtermctl session close --help
agtermctl session list --help    # may not exist; record the error if so
```

### `agtermctl --help`

_not recorded_

### `agtermctl session --help`

_not recorded_

### `agtermctl session new --help`

_not recorded_

### `agtermctl session status --help`

_not recorded_

### `agtermctl session rename --help`

_not recorded_

### `agtermctl session close --help`

_not recorded_

### `agtermctl session list --help`

_not recorded_

### Checks to make while recording

- does `session new` print the row id, and in what format?
- is `blink` sticky or one-shot?
- can `rename` be applied repeatedly to a live row?
- does `session close` exist, and does it take `--target`?
- is any status outside the four-word vocabulary accepted (it must not be)?
