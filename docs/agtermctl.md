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

**As of 2026-07-30 every clause this tool depends on has been exercised against a live agterm**,
including the spelling of `--blink` (observed blinking a live row) — **with one exception, below.**
What remains `ASSUMED` is that `session rename` may be called repeatedly — which the bridge does on
every update, so a failure would be loud and constant rather than subtle — and the spelling of
`--auto-reset`, which agbridge does not use (see "`--auto-reset`: deliberately dropped").

⚠️ **The exception is `session scratch`**, added for `agb pane`'s `[d] drawer`. Its *spelling* is
recorded verbatim from `--help`, but its **behaviour has not yet been exercised**: nobody has
watched a scratch drawer open, been hidden and come back with the same shell alive. Until that
happens it sits one evidence class below everything else here. The check that clears it is in
`docs/plans/completed/20260730-agb-pane-scratch-drawer.md` under Post-Completion.

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

  and the CLI spells them `--blink` / `--auto-reset` (which is how `agr`'s own front-end spells
  them, `agr:213`).
- **CONFIRMED** (2026-07-30, live): `--blink` is accepted and takes effect — a row that had shown no
  status came up blinking on the next transition into `active`. Only `--blink` was exercised;
  `--auto-reset` is not emitted by agbridge at all, so its spelling stays unverified and unused.
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

agbridge uses **`on`**, never the default `toggle`: `[s] split` can be pressed twice and a toggle
would close the pane the second time. The same rule governs `session scratch` below, for `[d]`.

### `agtermctl session scratch [on|off|toggle] [--command <command>] [--target <id>]` — **CONFIRMED**

Verbatim from `agtermctl session scratch --help` (2026-07-30):

```
OVERVIEW: Show or hide a session scratch terminal (on|off|toggle).
USAGE: agtermctl session scratch [<mode>] [--command <command>] [--target <target>] ...
  <mode>     Mode: on (show), off (hide), or toggle (default). The hidden scratch shell stays
             alive. (default: toggle)
  --command  When showing, run this command as the scratch's process instead of a login shell
             (run-once; respawns the scratch if one is already open).
  --target   Target session/workspace id, unique prefix, or 'active'. (default: active)
```

- **`--command` is deliberately not used**, despite being the nicer single call — it would replace
  `scratch on` + `type --pane scratch` with one invocation and remove the keystroke-injection layer
  and its quoting entirely. The blocker is in its own help: *"respawns the scratch if one is already
  open"*. `[d]` is two keystrokes and gets pressed twice, and the second press would then destroy a
  shell somebody was working in. Typing into the shell that is already there nests an ssh instead,
  which `exit` undoes — and which is exactly what `[s]` has always done.
- **ASSUMED**: that `--pane scratch` errors before the scratch exists, the way `--pane right` does
  before the split does. Nothing recorded says so — the help constrains only `--select`, and only
  for a split. It is also **unobservable** from here, because `open_drawer` always sends `scratch
  on` first: no test and no manual check can falsify it. The ordering is kept on the split's
  precedent and costs nothing if the constraint turns out not to exist.

### `agtermctl notify <body> [--title T] [--target T] [--window W]` — **CONFIRMED**

Top-level, not under `session`. `--target` addresses a session by id, which is what makes it usable
from the bridge: the banner is attributed to the right row, clicking it jumps to that pane, and it
raises that row's unseen badge. `--title` defaults to the session name; `--target` to the active
session of the frontmost window, which is never what the bridge wants.

agbridge sends one on a transition into **`blocked`** — the only state that means a human is the
blocker. Whether the Dock icon also bounces is **agterm's** setting, not agbridge's
(Settings ▸ Notifications: *off*, *once*, or *until you focus agterm*, off by default). That split is
deliberate: which events are worth announcing is agbridge's business; how loudly the machine
interrupts you is the machine's.

- **CONFIRMED** (2026-07-31, live): run by hand against a bound row, it produced a banner. The flag
  names and defaults come from `agterm.com/commands`; `--target` was additionally proven by its
  *rejection* of a stale row id — `error: no such session: <uuid>` means the flag parsed and the
  lookup ran.
- **CONFIRMED** (2026-07-31, live): the bridge's own path, end to end, on **both** triggers. An
  agent driven into `blocked` with agterm in the background produced the banner and a bouncing Dock
  icon; so did a genuinely new agent appearing, naming its working directory. So the triggers are
  proven as well as the command.

### `agtermctl session seen [--target T] [--window W]` — **CONFIRMED**

*"Clears the session's unseen-notification badge without changing the selection, focus, or agent
status."* Idempotent, and the badge is a **count**, not a boolean.

**CONFIRMED live** (2026-07-31): with the row *not* selected, the badge appeared on the block and
went away when the agent moved off it.

agbridge sends one when an agent **leaves** `blocked` — somebody answered it, so the badge is
advertising something already dealt with. It is reached only for a key agbridge itself announced,
and only while `notify_on_blocked` is on: *unwind what you did*, so one switch governs the whole
feature rather than half of it.

Five limitations, none escapable. The middle two look enough like bugs to be worth stating:

| | |
|---|---|
| the whole **count** is cleared | agterm has no partial decrement. An agent that raised its own notification (OSC 9) while blocked loses that badge too when the prompt is answered. The alternative is not clearing at all |
| ⚠️ an agent **killed** while blocked keeps its badge | it reaches `[done]` through a *removal*, not a state transition, so it never passes the clearing point. Deliberate — nobody answered it, and the row is about to be reclaimed |
| ⚠️ a **bridge restart** orphans one badge | the "we announced this" memory is per-process, so an agent blocked before a restart and answered after one is not in the set at clear time. Same reason a restart re-announces a still-blocked agent. Persisting the set fixes both and is far more machinery than a stale badge is worth |
| `notify_on_blocked = 0` clears nothing | including badges an earlier run raised while it was on |
| clicking the banner or the row clears it first | agterm's own behaviour; the call is then a no-op, which is only harmless because `seen` is idempotent |
| ⚠️ **a selected row never gets a badge at all** | observed 2026-07-31: nothing is "unseen" about the session you are looking at, so the badge is not raised and there is nothing to clear. The banner and the Dock bounce still fire. This makes the row you are watching **the one row where this feature cannot be tested** — the first attempt was invalid for exactly that reason |

### `agtermctl window select [<id>]` — **CONFIRMED, not yet used**

Verbatim from `agtermctl help window select` (2026-07-31):

```
OVERVIEW: Select (raise or open) a window.
USAGE: agtermctl window select [<id>] [--socket <socket>] [--json]
  <id>       Window id, unique prefix, or 'active'. (default: active)
```

Recorded because it is the primitive for a *bring-agterm-to-the-front* feature, which is being
considered separately. Note the default: with no id it raises whichever window is **already
active** — so raising the window that actually holds a blocked row needs its id, which
`tree --json` reports and `tree_workspaces` already parses.

### `agtermctl session overlay open|close|resize|result` — **CONFIRMED to exist, deliberately unused**

*"Open, resize, or close an ephemeral overlay terminal on a session"*; `open` runs a command and
*"it closes when COMMAND exits"*. It was the other candidate for `[d]` and was rejected: an
interactive shell destroyed when it is dismissed is the opposite of a drawer. `scratch` was chosen
because its help promises *"the hidden scratch shell stays alive"*. Recorded so the next reader does
not re-evaluate it from scratch.

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

## ⚠️ agterm closes a session when its command exits

**CONFIRMED live, 2026-07-31**, in isolation: an agent (`closetest2`) was started detached, its row
clicked, `exit` typed at the `agb pane` prompt without attaching. The row **vanished from the
sidebar** while the agent stayed alive on the farm — tmux session running, key in the marker, state
`completed`.

This is not documented anywhere in agterm's own reference and nothing in agbridge assumed it. It has
three consequences, and the first is a bug we lived with for a day without recognising it.

**1. Leaving the prompt costs you the row.** `q`, `quit` and `exit` are all `PANE_QUIT_WORDS`, and
any of them ends `agb pane` with status 0 — so agterm destroys the row of a perfectly healthy agent.
That is not a *dismissal*, it is an accident: you stopped looking at a row and lost it. Nothing
brings it back except `agb-refresh`.

**2. It is almost certainly the source of the `no such session` spam.** Every `exit` leaves a
`bound` entry naming a row agterm has already destroyed, and the bridge then renames and statuses
that dead id on every poll for the life of the process. The screenfuls of it in
`~/Library/Logs/agbridge/bridge.err.log` that took a morning to diagnose were, in all likelihood,
just somebody leaving prompts.

**3. It reverses the reasoning behind `session.closed` handling.** The obvious objection to
"`session.closed` → forget the binding → let the next report re-mint the row" is that it makes a row
*un-closable*. That objection is wrong **because a hand-`exit` is not an intent to close.** Re-minting
is not the tool fighting you; it is the tool repairing a row you lost by leaving a prompt.

There is a real cost, and it is recorded here rather than solved: **after that change there is no
way to dismiss a live agent's row.** `agb prune` is the only remover and it is destructive on the
farm side. The alternative considered and not taken was a *dismissed* set — a hand-closed key
suppressed until its agent stops reporting — which gives both behaviours at the price of one more
piece of per-process state. If dismissal is ever wanted, that is the door.

⚠️ **`agb close-done` cannot clear an entry whose row is already gone.** It only drops an entry whose
`session close` *succeeded*, so a `[done]` row you closed by hand leaves a permanent entry that no
command touches. Two were sitting in a live rows map when this was found. Reacting to
`session.closed` fixes it as a side effect.

**`session restore` is the structural fix** and is not yet used: it pins the command a pane re-runs,
which would let a row survive its command exiting at all. See the menu below.

## What `agtermctl events` actually does — **CONFIRMED live, 2026-07-31**

Shipped in agterm **v0.16.0** (2026-07-22, #273). Verbatim from `agtermctl help events` on v0.19.1:

```
OVERVIEW: Continuously print control events.

USAGE: agtermctl events [--socket <socket>] [--json] [--kind <kind> ...] [--run <run>]
                        [--after <after>] [--limit <limit>]
  --kind    Event kind; repeat or comma-separate values.
  --run     App-run UUID paired with --after.
  --after   Sequence cursor paired with --run.
  --limit   Events per read (1...1000).
```

An observed event, `--json`, one object per line — **flat, not nested** the way `tree --json` is:

```json
{"workspace":"AE519AEE-…","kind":"status","session":"B2567244-…","window":"D0088CF9-…",
 "payload":{"blink":false,"status":"completed","name":"agbridge_dev · … · %15"},
 "ts":1785494078.740017,"seq":24}
```

**`session` carries the row id**, which is what a `session.closed` reaction would key on.

### Why agbridge does not use it

Four findings, each from a run on the Mac, and together they sink the design that was written for it:

1. **It follows; it does not return.** *"Continuously print"* is literal — cursorless, it printed
   one current event and then sat there until interrupted. There is no batch-and-exit mode reachable
   in practice, so consuming it means a **long-lived streaming subprocess** with its own liveness
   story.
2. **It starts at the tail.** `--kind session.closed` printed nothing across a 15 s window because
   nothing closed *during* it. There is no history replay, so anything that happens while the bridge
   is down is simply missed.
3. **The run id is not obtainable.** Not in the event records, not in `tree --json` (whose top level
   is `{"result":{"tree":{…}},"ok":true}` — no app-run anywhere), and there is no header or envelope
   before the event lines (checked by redirecting to a file, so nothing could scroll past).
   `--run/--after` must be paired, so **the cursor cannot be bootstrapped** — which removes both
   catch-up and restart detection.
4. **Possible buffering when stdout is not a tty.** The same command that printed an event to a
   terminal produced an empty file when redirected. Either nothing happened in that window or
   `agtermctl` block-buffers to a pipe — unresolved, and it matters: a consumer reading over a pipe
   would get events in 4 KB chunks, which for "a row was closed" is close to useless.

One consolation prize, worth recording because it is exactly the signal a restart detector would
want: passing a stale cursor makes it **exit** with `Error: event run changed`. agterm compares the
runs itself and says so. It just will not tell you the current one, so there is no way to store a
cursor for next time.

⚠️ **This is the second time the website reversed a design decision.** The original assessment here
was "a second long-lived input, so a second thing that can wedge — it would need its own liveness
story". `agterm.com/commands` said it returns a batch and exits, so that objection was marked
**wrong** with a ✅. The binary says the original objection was **right**. The rule that follows is
in `CLAUDE.md`: run it on the Mac before designing against it.

## What agbridge does not use yet

⚠️ **Never plan against `agterm.com/commands` without running the command on the Mac.** On
2026-07-31 that page described `events` and `pick`, and the installed agterm had neither — the
Mac was on a build older than **v0.16.0**, which is where `agtermctl events` and
`agtermctl session restore` both shipped (2026-07-22, #273 and #271). Upgrading to v0.19.1 made both
appear. But the page was *also wrong about behaviour* on the build that has them: see the `events`
entry below, where it cost two reversed design decisions.

*Surveyed 2026-07-31 against `agterm.com/commands`. agterm exposes roughly **70** subcommands;
agbridge calls **eight**: `session new`/`rename`/`status`/`close`/`split`/`scratch`/`type`, `notify`,
and `tree --json`. This is the menu for later, not a backlog — nothing here is committed, and each
entry says what it would buy so the next reader does not have to re-derive it.*

### Would fix something we have actually hit

| Command | What it would buy |
|---|---|
| ⚠️ **`events`** — *control-event stream* | **Exists from agterm v0.16.0; usable only as a long-lived stream.** Full findings in "What `agtermctl events` actually does" above. Would give live `session.closed` and `tree.changed`; would **not** give restart detection or catch-up, because no run id is obtainable. Design work is done and shelved in `docs/plans/blocked/20260731-agb-events-feedback-loop.md`. |
| **`session restore`** — *pin the command a pane re-runs* | Promoted after 2026-07-31: this is the **structural fix** for a row dying when its command exits, which is what `q`/`quit`/`exit` at the `agb pane` prompt does today (see the section above). It would also let rows come back alive after an agterm restart rather than as dead panes — the other half of the reboot story in `docs/cookbook.md` — and shrink what `agb-refresh` is needed for. |
| **`session move`** — *relocate a session to another workspace* | `agb-refresh` currently **destroys and recreates** rows and restores their workspace from `placements`. `move` would let it keep the row and put it back instead — fewer moving parts, and row ids would survive a refresh. |

### New capability, no existing pain

| Command | What it would buy |
|---|---|
| `session flag` | Flag on `blocked`, unflag when it clears — agterm's flagged view becomes "agents that need you". Overlaps with the status glyph, so it may be redundant. |
| `session text` / `session search` | Read a row's terminal buffer from outside. Could answer *why* an agent is blocked without attaching to it. |
| `session background text` | A per-row watermark — the host name behind the pane, so a full-screen agent still shows which box it is on. |
| `window select`, `session go next-attention` | The bring-to-front family. Scoped and deliberately deferred: a bouncing Dock says "come here when you are ready", a window jumping in front says "stop what you are doing". See `window select`'s entry above for the id trap. |

### Deliberately not applicable

`quick`, `dashboard`, `pick`, `theme`, `font`, `keymap`, `config`, `surface zoom`, `session
copy`/`paste`/`select-all`/`reveal`/`duplicate`/`focus`/`resize`, and most of `workspace` — these are
the human's UI, not a bridge's business. `pick` is the near miss: an agterm-native row picker sounds
appealing, but `agb list` already does that on the farm side, which is where you are when you need
a key.

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

The flag itself is **CONFIRMED** live (2026-07-30) — a row began blinking on a transition into
`active`. What is still not established is whether `blink` is a **sticky attribute** or a
**one-shot animation trigger**. If it is one-shot, re-sending it on every snapshot would make a row
that has been quietly `active` for an hour flash on every bridge reconnect. Gating on transitions is
correct under both readings, so the ambiguity costs nothing and does not need resolving.

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
- is `blink` sticky or one-shot? (the flag itself is confirmed accepted; this is still open)
- can `rename` be applied repeatedly to a live row?
- does `session close` exist, and does it take `--target`?
- is any status outside the four-word vocabulary accepted (it must not be)?
