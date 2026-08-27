# `agtermctl` — the contract agbridge codes against

`agtermctl` is a **Mac-only** binary shipped with [agterm](https://github.com/umputun/agterm). It
cannot be run or `--help`'d from a Linux host: it does not exist there. Everything below was either
derived from the design and later confirmed against a live agterm, or captured verbatim on a Mac —
each clause says which.

This file therefore has two layers, and they are kept **visually separate on purpose**:

1. **The assumed contract** — what Tasks 4b, 8 and 9b code against *today*. Every clause is tagged
   with its evidence class.
2. **The verbatim recording** — `--help` output captured on the Mac. `session split` and
   `session type` are recorded inline in layer 1; `agtermctl --help` and `agtermctl session --help`
   are at the bottom of the file; and `surface cursor`, `session text`, `session restore` and
   `session move` are in "Re-surveyed against the installed binary — agterm **0.24.0**", which also
   **corrects** a `session restore` claim this file made from the website and never ran.

**As of 2026-07-30 every clause this tool depends on has been exercised against a live agterm**,
including the spelling of `--blink` (observed blinking a live row) — **with one exception, below.**
What remains `ASSUMED` is that `session rename` may be called repeatedly — which the bridge does on
every update, so a failure would be loud and constant rather than subtle — and the spelling of
`--auto-reset`, which agbridge does not use (see "`--auto-reset`: deliberately dropped").

✅ **The status vocabulary is no longer assumed.** `session status bogus` answers `error: invalid
status` and exits 1 — recorded at the bottom of this file, 2026-08-24.

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

Verbatim from `agtermctl session new --help`, **re-captured 2026-08-06** on the upgraded build
(⚠️ the version was not captured in the same run, so this is dated rather than versioned — the
2026-07-30 capture it replaces was taken before the v0.19.1 upgrade):

```
OVERVIEW: Create a session.

USAGE: agtermctl session new <options>

OPTIONS:
  --cwd <cwd>             Working directory (defaults to $HOME).
  --workspace <workspace> Target workspace by id/prefix/active (defaults to the current one). Mutually exclusive with --workspace-name.
  --workspace-name <workspace-name>
                          Target workspace by name; errors if not found unless --create-workspace. Mutually exclusive with --workspace.
  --create-workspace      With --workspace-name, create the workspace when it does not exist (reuse it otherwise).
  --command <command>     Run this command as the session's process instead of the login shell (no echoed command line; the session closes when it
                          exits).
  --wait                  With --command, hold the session open after the command exits (press any key to close) instead of closing immediately.
  --name <name>           Initial session name (defaults to the auto basename).
  --after <after>         Place the new session right AFTER this anchor session (id/prefix/active); the anchor carries its own workspace, replacing
                          --workspace.
  --before <before>       Place the new session right BEFORE this anchor session (id/prefix/active); mirror of --after.
  --no-select             Create the session in the background without selecting or focusing it (leaves the current selection untouched).
  --socket <socket>       Override the control socket path.
  --json                  Print the raw JSON response.
  --window <window>       Target window id, unique prefix, or 'active' (defaults to the frontmost).
  -h, --help              Show help information.
```

Six flags are new since the 2026-07-30 capture — `--wait`, `--after`, `--before`, `--socket`,
`--json`, `--window` — and one of them is not a minor addition. ⚠️ **`--command`'s own help text now
states the closing rule outright**: *"the session closes when it exits"*. That was learned here the
hard way, live, and written up below under "agterm closes a session when its command exits"; it is
now documented behaviour rather than an observation of ours.

⚠️ **`--wait` looks like a cheap partial fix for exactly that**, and is untested. See its entry in
"What agbridge does not use yet".

**No icon flag, and that is a recorded negative.** Asked on 2026-08-06 because a locally-run Claude
shows an icon on its agterm row and an agbridge row does not: there is nothing here that sets one,
and `agtermctl --help | grep -i icon` was empty. ⚠️ That second one is **weak evidence** — the
top-level help lists subcommands, not their flags, so it does not rule out a separate verb. What
paints the local icon is unknown: process detection on the Mac (structurally unreachable for a
bridge row, whose only local process is `agb pane` while Claude is on the farm) or an escape
sequence from Claude itself (which would travel over ssh, and would already show while attached).
The experiment that distinguishes them is to attach to a live row and watch it; nobody has run it.

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
  later `session rename` receives — by default label, host, cwd, pane and the beat age, joined by
  ` · ` (`row_fields` picks them), with
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
| normal | `<label> · <host> · <cwd> · <pane> · <beat-age>` — the **default** field list; `row_fields` chooses |
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

### `agtermctl session type [<text>] [--pane <pane>] [--target <id>]` — **CONFIRMED**

Recaptured on **0.24.0**, 2026-08-24. The 2026-07-29 capture is kept below it, because the
difference is the point: the help's pane list changed and **the binary's did not** — see the
measured table below, which is the reason both captures are kept.

```
OVERVIEW: Inject text into a session.

USAGE: agtermctl session type [<text>] [--stdin] [--select] [--pane <pane>] [--target <target>] [--socket <socket>] [--json] [--window <window>]

ARGUMENTS:
  <text>                  Text to inject (omit with --stdin).

OPTIONS:
  --stdin                 Read the text from stdin instead of an argument.
  --select                Select the session first if its surface is not ready (main pane only; a split pane must already exist).
  --pane <pane>           Which pane to type into: primary/left/top, split/right/bottom, or scratch (even when hidden). Defaults to primary.
  --target <target>       Target session/workspace id, unique prefix, or 'active'. (default: active)
  --socket <socket>       Override the control socket path.
  --json                  Print the raw JSON response.
  --window <window>       Target window id, unique prefix, or 'active' (defaults to the frontmost).
  -h, --help              Show help information.
```

As captured 2026-07-29, on the build before:

```
OVERVIEW: Inject text into a session.
USAGE: agtermctl session type [<text>] [--stdin] [--select] [--pane <pane>] [--target <target>] ...
  --select   Select (and realize) a never-shown session before injecting
             (main pane only; a split pane must already exist).
  --pane     Which pane to type into: left (main), right (split), or scratch. Defaults to left.
```

`--select`'s constraint is **unchanged** across the two, which is what keeps both consequences below
true.

### ⚠️ The `--pane` vocabulary in 0.24.0's `--help` is WRONG — **MEASURED 2026-08-24**

The help above says `primary/left/top, split/right/bottom, or scratch`. Four of those seven words
are rejected. Measured against a live row, one word per call, reading the error:

| word | result |
|---|---|
| `left` | **accepted** — returned the buffer |
| `right` | **accepted** — `error: session has no split pane`, a pane error, not a vocabulary error |
| `scratch` | **accepted** — `error: session has no scratch terminal` |
| `primary` | `error: invalid pane: primary` |
| `top` | `error: invalid pane: top` |
| `split` | `error: invalid pane: split` |
| `bottom` | `error: invalid pane: bottom` |

So the accepted vocabulary is exactly the **2026-07-29** one: `left`, `right`, `scratch`. The
distinction that makes this measurable rather than a guess is that a *rejected* word answers
`invalid pane: X` while an *accepted* word that names a pane which does not exist answers about the
pane — so `right` and `scratch` are confirmed accepted on a session that has neither.

⚠️ **`TYPE_RIGHT`/`TYPE_SCRATCH` in `agb_ops` are correct and must not be "modernised".** Rewriting
them to the documented `split`/`primary` would break `agb pane`'s `[s]` and `[d]` outright, with the
binary's own `--help` as the justification. This is the first case in this file where the failure
came from agterm's **help text** rather than from `agterm.com/commands`, so the rule generalises:
run it, whatever the source.

The rejection was measured on `session text`. `session type` documents the same list; whether it
accepts the same subset is **untested**, though agbridge has shipped `--pane right` and
`--pane scratch` through `session type` since 0.3.0, which is evidence for `right`/`scratch` and
says nothing about `primary`.

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

## agterm's `TERM`, and what it costs a remote attach — **CONFIRMED live, 2026-08-01**

agterm sets **`TERM=xterm-ghostty`** in the sessions it runs. Observed twice on one Mac, in the same
hour, which is what makes it evidence rather than a guess:

- a row's `[enter]` attach failed with `open terminal failed: missing or unsuitable terminal:
  xterm-ghostty` — so that is the value `ssh -t` carried from the row;
- a local shell in agterm reported the same name, while the *same user's* login shell in Kitty
  reported `xterm-kitty`.

agbridge does not set `TERM` and should not: `pane_remote_command` builds tmux commands only, and
`ssh -t` propagates the client's value by design. But the consequence belongs here, because it looks
exactly like a broken row and is not one:

⚠️ **A farm host with no terminfo entry for agterm's `TERM` cannot be attached to.** tmux refuses to
start against a name it cannot look up, so `[enter]` prints the message above and `agb pane` reports
`ssh exited 1 -- nothing was attached`. Everything else about the row is correct — the identity, the
ssh target, the config it resolved through — which is why the diagnosis reads as an agbridge failure
at first. `docs/cookbook.md` carries the recipe (`infocmp -x "$TERM" | ssh <target> -- tic -x -`,
run **from inside agterm**, since that is the only place `$TERM` holds the value that will travel).

⚠️ **`"$TERM"`, never a hardcoded name — including in anything agbridge might one day automate.**
The obvious implementation is for `install.sh` to ship its own `$TERM` to the farm host, and it is
**wrong**: the installer runs in your login terminal, the row runs in agterm, and on the Mac that
produced this note those were `xterm-kitty` and `xterm-ghostty`. That version would install the
wrong entry on every machine and report success. If this is ever automated it belongs in `agb pane`,
which knows the real value at the moment the attach fails — not in the installer, which is guessing
about a terminal that does not exist yet.

Two limits worth stating with it: the copy fixes **one host**, so machine #3 behind a jump host and
every other agent host on a shared-disk cluster each need their own; and the value above is agterm's
*today* — the recipe is written in terms of `$TERM` precisely so it survives agterm changing it.

## ⚠️ agterm closes a session when its command exits

**CONFIRMED live, 2026-07-31**, in isolation: an agent (`closetest2`) was started detached, its row
clicked, `exit` typed at the `agb pane` prompt without attaching. The row **vanished from the
sidebar** while the agent stayed alive on the farm — tmux session running, key in the marker, state
`completed`.

⚠️ **Update, 2026-08-06: it IS documented now, and agterm ships a flag against it.** The re-captured
`session new --help` above says so in `--command`'s own text — *"the session closes when it exits"* —
and adds **`--wait`**, which holds the row open instead. So the sentence below stands as history
rather than as a current claim about the reference. `--wait` is untested and is **not** simply the
fix: what it leaves behind is a row whose command has ended, which may be worse than a gone row, and
it cannot tell a typed `quit` from `agb pane` dying for a real reason. Its survey entry lists the
three questions.

This was not documented anywhere in agterm's own reference at the time and nothing in agbridge
assumed it. It has three consequences, and the first is a bug we lived with for a day without
recognising it.

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

*Surveyed 2026-07-31 against `agterm.com/commands`, and **five entries re-surveyed 2026-08-24
against the installed 0.24.0 binary** — see the section after this one, which corrects one of them.
agterm exposes roughly **70** subcommands;
agbridge calls **eight**: `session new`/`rename`/`status`/`close`/`split`/`scratch`/`type`, `notify`,
and `tree --json`. This is the menu for later, not a backlog — nothing here is committed, and each
entry says what it would buy so the next reader does not have to re-derive it.*

### Would fix something we have actually hit

| Command | What it would buy |
|---|---|
| ⚠️ **`events`** — *control-event stream* | **Exists from agterm v0.16.0; usable only as a long-lived stream.** Full findings in "What `agtermctl events` actually does" above. Would give live `session.closed` and `tree.changed`; would **not** give restart detection or catch-up, because no run id is obtainable. Design work is done and shelved in `docs/plans/blocked/20260731-agb-events-feedback-loop.md`. |
| ✅ **`session new --wait`** — *hold the row open after its command exits* | **CONFIRMED live 2026-08-24**: a row whose `--command` failed appeared and vanished instantly with nothing to read; `--wait` held it open showing `exec: tmux: not found`, which was the whole diagnosis. Used as a *diagnostic* here, not by the bridge — the three open questions below are still open for `_create_row`. **Found 2026-08-06** in the re-captured `--help`; not present in the 2026-07-30 one. *"With `--command`, hold the session open after the command exits (press any key to close)."* That is the row-destroying bug below, addressed by one flag in `_create_row`'s `args` — far cheaper than `session restore`, and the two are not alternatives: `--wait` stops the row **dying**, `restore` brings its command **back**. ⚠️ Read off help text, untested, and three things need answering before it is a plan. Does "press any key to close" leave a row that looks alive but runs nothing, which is worse than a gone row? What does `agb-refresh` see when it closes such a row? And it fires for *every* exit, not just a typed `quit` — including `agb pane` dying for a real reason, which is a case we currently find out about. |
| ⚠️ **`session restore`** — *pin the command a pane re-runs* | **The claim that used to sit here was wrong, and wrong in the direction that would have got the wrong fix built.** It read: *"this is the **structural fix** for a row dying when its command exits, which is what `q`/`quit`/`exit` at the `agb pane` prompt does today"*. `--help` on 0.24.0 says the opposite — *"The override is written now and consumed on the **NEXT launch** — it never touches the running session."* So it fixes only the **restart** half (rows come back alive after an agterm restart instead of as dead panes, the other half of the reboot story in `docs/cookbook.md`); a live row destroyed by a typed `quit` is gone and restore cannot bring it back. That half still points at `session new --wait`, above. Full capture and the three new constraints — sticky, gated on a setting, world-readable via `tree` — in "Re-surveyed against the installed binary", below. |
| **`session move`** — *relocate a session to another workspace* | `agb-refresh` currently **destroys and recreates** rows and restores their workspace from `placements`. `move` would let it keep the row and put it back instead — fewer moving parts, and row ids would survive a refresh. **CONFIRMED 0.24.0**, and it does more than the survey said: `--target` is **repeatable** (one call for a batch), `--to up\|down\|top\|bottom` reorders within a workspace, and `--after`/`--before` place relative to an anchor session that *carries its own workspace* — relocate and position in one shot. The second half is a capability agbridge has never had: deterministic row **order**, e.g. grouped by host. |

### New capability, no existing pain

| Command | What it would buy |
|---|---|
| `session flag` | Flag on `blocked`, unflag when it clears — agterm's flagged view becomes "agents that need you". Overlaps with the status glyph, so it may be redundant. |
| `session text` / `session search` | Read a row's terminal buffer from outside. Could answer *why* an agent is blocked without attaching to it. **`session text` CONFIRMED 0.24.0** with more reach than assumed: `--pane` reads a pane *"even when hidden"*, `--all` takes scrollback as well as the visible screen, `--lines N` keeps the tail, `--json` for the raw response. It is also the only command here that can tell an agbridge row's **two modes apart** — `agb pane`'s menu prompt versus an attached agent — see the re-survey below. |
| `session background text` | A per-row watermark — the host name behind the pane, so a full-screen agent still shows which box it is on. |
| `window select`, `session go next-attention` | The bring-to-front family. Scoped and deliberately deferred: a bouncing Dock says "come here when you are ready", a window jumping in front says "stop what you are doing". See `window select`'s entry above for the id trap. |

### Deliberately not applicable

`quick`, `dashboard`, `pick`, `theme`, `font`, `keymap`, `config`, `surface zoom`, `session
copy`/`paste`/`select-all`/`reveal`/`duplicate`/`focus`/`resize`, and most of `workspace` — these are
the human's UI, not a bridge's business. `pick` is the near miss: an agterm-native row picker sounds
appealing, but `agb list` already does that on the farm side, which is where you are when you need
a key.

## Re-surveyed against the installed binary — agterm **0.24.0**, CONFIRMED live 2026-08-24

Prompted by a question that had nothing to do with agbridge: whether agterm's own
[`cookbook/two-agent-chat`](https://github.com/umputun/agterm/tree/master/cookbook/two-agent-chat)
recipe — two agents in one split, each typing into the other's composer — could be pointed at
agbridge rows. Answering it meant running `--help` on five commands this file had only ever
*surveyed off the website*. One of the five said the opposite of what was written down, and two
said more than was written down. That ratio is the argument for the rule in `CLAUDE.md`, not a
reason to distrust this particular page.

⚠️ **`agtermctl` has no `--version`** (`Error: Unknown option '--version'`). The build number comes
from the app bundle:

```sh
defaults read /Applications/agterm.app/Contents/Info CFBundleShortVersionString   # -> 0.24.0
```

### `agtermctl surface cursor` — **CONFIRMED, not used**, new in this survey

Not in the 2026-07-31 survey at all; `surface` had only `zoom` on 0.19.1. The recipe above requires
it, which is why that recipe needs agterm ≥ 0.24.0.

```
OVERVIEW: Report a terminal surface's zero-based cursor column.

Prints the column alone, so it drops straight into a command substitution. Row is not reported: the
pinned libghostty exposes no cursor accessor and the vertical metrics it does export cannot recover
a row that survives a custom `adjust-font-baseline`.

A column is a signal, not proof about the line's content. Past the prompt it establishes the line is
not empty; AT the prompt it establishes nothing, since the caret may have been moved back over text
that is still there.

USAGE: agtermctl surface cursor [--target <target>] [--socket <socket>] [--json] [--window <window>]

OPTIONS:
  --target <target>       Target surface id from tree, 'quick' (the quick terminal), or 'active'. (default: active)
  --socket <socket>       Override the control socket path.
  --json                  Print the raw JSON response.
  --window <window>       Target window id, unique prefix, or 'active' (defaults to the frontmost).
  -h, --help              Show help information.
```

Two things worth carrying, both from agterm's own text:

- ⚠️ **A cursor column is not an emptiness proof, and the tool says so** — *"AT the prompt it
  establishes nothing, since the caret may have been moved back over text that is still there."*
  That is exactly the "Composer Safety Risk" the cookbook's README lists as its own worst
  limitation, restated by the binary. Anything built on it inherits the limitation; `session text`,
  which can read the line's content, is the stronger signal and `surface cursor` is the cheap
  pre-filter.
- **`--target` is a *surface* id from `tree`, not a session id.** `tree_workspaces` (`agb_mac:1243`)
  parses `result.tree.workspaces[].sessions[].id` and stops there, so a caller would need to reach
  one level further into the same JSON it already fetches. **RECORDED 2026-08-24**: each session
  object carries a `surfaces` list, and the id is the cookbook's form —

  ```json
  "surfaces": [ { "visible": true, "kind": "left", "id": "surface:B6FB71FB-…-CA3233329B63:left", "active": true } ]
  ```

  — i.e. `surface:<session id>:<kind>`, and `kind` uses the same `left`/`right`/`scratch` vocabulary
  the panes do. It is derivable by string concatenation, but read it from `surfaces` rather than
  building it: `kind` is agterm's word for that pane and the list is what says which panes exist.

  ⚠️ **A shell trap, not an agterm one, and it cost a wrong conclusion here.** `"surface:$ROW:left"`
  in **zsh** applies the `:l` *lowercase modifier* to `$ROW`, producing
  `surface:<lowercased-id>eft` — which agterm rejects as `invalid surface`, and which reads exactly
  like "the cookbook's target syntax does not work on this build". It does work. Brace it:
  `"surface:${ROW}:left"`.

### `agtermctl session text` — **CONFIRMED, not used**

```
OVERVIEW: Print a session's terminal buffer as plain text (does not touch the system clipboard).

USAGE: agtermctl session text [--all] [--lines <lines>] [--pane <pane>] [--target <target>] [--socket <socket>] [--json] [--window <window>]

OPTIONS:
  --all                   Read the full screen + scrollback instead of just the visible screen.
  --lines <lines>         Keep only the last N lines of the full buffer.
  --pane <pane>           Which pane to read: primary/left/top, split/right/bottom, or scratch (even when hidden). Defaults to the on-screen pane.
  --target <target>       Target session/workspace id, unique prefix, or 'active'. (default: active)
  --socket <socket>       Override the control socket path.
  --json                  Print the raw JSON response.
  --window <window>       Target window id, unique prefix, or 'active' (defaults to the frontmost).
  -h, --help              Show help information.
```

⚠️ **`--pane` defaults to *the on-screen pane*, not to `primary`** — unlike `session type`, which
defaults to `primary` outright. A caller that omits it is asking about whichever pane happens to be
showing, which for an agbridge row changes the moment somebody opens the `[s]` split. Pass it.

### `agtermctl session restore` — **CONFIRMED, not used**, and it is NOT what this file said

The entry in "Would fix something we have actually hit" claimed restore was the structural fix for
a row dying when `agb pane` exits. It is not:

```
The override is written now and consumed on the NEXT launch -- it never touches the running session.
It wins over the pane's captured foreground command, is gated on the restore-running-command
setting, and reads back on `tree` as restoreCommand (main pane) or splitRestoreCommand (split pane).
It is STICKY: it fires again on every launch until cleared.

COMMAND is shell code, stored verbatim in the window's state file and readable via `tree`, so it
must not carry secrets.
```

So it addresses the **restart** half of the row-lifetime story and nothing else. A live row
destroyed by a typed `quit` is gone at that instant, and no pin brings it back — that half is
`session new --wait`'s, and the two were bundled into one entry because both were read off a
website rather than run.

Three constraints that follow, none of which were in the survey:

- **It is gated on a user setting** ("restore-running-command"). A feature built on it is off by
  default for anyone who has that setting off, silently.
- **It is sticky** — it fires on *every* launch until `--clear`. Written per row, that is durable
  state agbridge would then own on the agterm side, in addition to `rows` and `placements`.
  Invariant 12 says everything the Mac side persists derives from the config; a pin lives in
  agterm's window state instead, which is the first thing that would not.
- ⚠️ **It is world-readable via `tree`** and is *"stored verbatim in the window's state file"*. An
  `agb pane` line carries a host, a tmux session name, a pane id and possibly a `--config` path —
  none secret today, but this turns "what goes on that command line" from a free choice into a
  constraint.

`--pane-id` also documents **`$AGTERM_PANE_ID`**, a stable per-surface token in the pane's
environment. Together with the cookbook's `AGTERM_SESSION_ID` that means a row's own `agb pane`
process can learn its agterm identity without the bridge telling it — a door agbridge does not
currently use.

### `agtermctl session move` — **CONFIRMED, not used**

```
OVERVIEW: Move a session: to another workspace, reorder with --to, or place relative to an anchor with --after/--before.

USAGE: agtermctl session move [<workspace>] [--to <to>] [--after <after>] [--before <before>] [--target <target> ...] [--socket <socket>] [--json] [--window <window>]
```

`--target` is repeatable, so a whole refresh is one call. `--after`/`--before` take an anchor that
carries its own workspace, relocating and positioning in one shot — which is a capability agbridge
has never had at all: deterministic row **order**, e.g. all of one host's rows together.

### What `tree --json` reports that agbridge throws away — **CONFIRMED 2026-08-24**

`tree_workspaces` (`agb_mac:1243`) walks `result.tree.workspaces[].sessions[]` and reads `id` — and
`name` for the workspace. The session object it already has in hand carries all of this:

| field | type | why it matters here |
|---|---|---|
| `id` | str | the row id; the only one read today |
| `surfaces` | list | `{visible, kind, id, active}` per pane — the surface ids `surface cursor` needs |
| `split`, `scratch`, `overlay` | bool | **whether those panes exist.** Invariant 13 sends `session split on` unconditionally because there was no way to ask; there is |
| `realized` | bool | the never-shown flag `--select` exists for — a read instead of an assumption |
| `status` | str | agterm's own copy of what the bridge last set. `RowRenderer.applied` is documented as *what we sent, not what agterm shows*; this **is** what agterm shows |
| `foreground` | list | the argv the session was launched with — ⚠️ **not** the pane's live foreground process, see below |
| `cwd`, `name`, `flagged`, `active`, `fontSize` | | |

⚠️ **This is a menu, not a plan.** `status` in particular looks like it retires `_reassert`'s 30 s
re-send, and it does not obviously do so: reading it costs a `tree` call per poll where the re-send
costs nothing, and the divergence `_reassert` exists for (agterm resets a row's status when the
row's command starts) would be *detected* rather than *prevented*. Worth measuring before anyone
treats it as a simplification.

### What this says about pushing text into an agbridge row

Recorded here because the question will be asked again, and because the answer is *yes with a
qualifier* rather than the *no* it first looks like.

`session type` works on an agbridge row. What differs from the cookbook's case is that the row's
pane has **two modes**, and the same bytes mean different things in each:

| mode | what receives the text |
|---|---|
| unattached | `agb pane`'s `sys.stdin.readline()` — the `[enter] attach   [s] split   [d] drawer   [q] quit >` prompt |
| attached | the pty running `ssh -t … tmux attach-session`, hence the remote agent's composer |

⚠️ **agbridge cannot tell you which**, and that is deliberate: attaching, detaching and scrolling
change no agent state, which is what makes the four-word status vocabulary trustworthy. The mode is
readable only off the screen — `session text --pane primary --lines 5` — which is the one thing this
survey added that makes the route defensible at all.

Two consequences:

- **Menu mode consumes a message silently.** `pane_attach`'s dispatch falls through to the attach
  for *anything* unrecognised (`agb_ops:2205`), so an unattached row swallows the text and attaches.
  The exceptions are the word sets, which are matched after `.strip().lower()`:
  `PANE_QUIT_WORDS = ("q", "quit", "exit")` **destroys the row** (agterm closes a session when its
  command exits), and `("s","shell","split")` / `("d","drawer","scratch")` open panes.
- **A bare newline is the safe arming primitive.** It strips to `""`, which is in none of the three
  sets, so it falls through to the attach and cannot hit the destructive case. One call turns menu
  mode into composer mode.

✅ **CONFIRMED live, 2026-08-24** — this was the claim the whole route rested on, and it holds:
keystrokes injected by `session type` survive agterm → `ssh -t` → remote tmux → the agent's
composer. Measured on an attached agbridge row whose agent was idle on a farm host:

```sh
ROW=<row id from tree>
agtermctl session text --target "$ROW" --pane left --lines 12   # tmux status bar => attached
agtermctl surface cursor --target "surface:${ROW}:left"         # -> 2, an empty composer
agtermctl session type "hello from agtermctl" --target "$ROW" --pane left   # -> ok
agtermctl session text --target "$ROW" --pane left --lines 12
```

The last read showed `❯ hello from agtermctl` inside the composer box, unsent. Nothing was
submitted — `session type` was given no trailing newline, which is what makes this test safe to
repeat.

Two results fall out of the same run:

- ✅ **`surface cursor` works through the ssh, and an empty Claude composer reports column `2`** —
  the exact value agterm's own cookbook checks for a local agent. So the caret test survives two
  extra hops. It remains a *signal*, not proof, for the reason `surface cursor`'s help gives.
- ⚠️ **`foreground` does NOT change on attach**, so it cannot be used as the mode detector. On this
  attached row it was an 18-element argv beginning with `Python` — the `agb pane` command line, not
  the `ssh` actually running in the pane. agterm reports what the session was *launched* with. The
  cookbook validates a peer by checking `foreground` names the expected agent; for an agbridge row
  it will always say `agb pane`, attached or not. **`session text` is the only mode detector.**

⚠️ **The hazard is not theoretical, and it fired on the first row picked.** That row's composer
already held an unsubmitted draft — `test the install script on another workarea` — which
`session type` would have appended to, submitting both together on the next Return. It was cleared
by hand before the test. `surface cursor` returning `2` afterwards is what made the run safe, and is
the whole argument for taking that reading first.

Also unmeasured, and separate: whether `surface cursor` reports anything useful for an attached row.
tmux draws its own status line, so the agent's composer is not the last line of the buffer — the
cursor column is robust to that where a `session text` tail-match is not, which inverts the
cookbook's preference between the two.

## Codex as a peer — MEASURED 2026-08-24, on a Linux cluster host

Everything below was run against `codex-cli 0.149.1`, not read. It matters for `agb-peer`, whose
delivery half is the only part of that tool that is agent-specific at all.

### The TUI differs in exactly TWO constants

| | Claude Code | Codex |
|---|---|---|
| composer glyph | `❯` | **`›`** |
| empty-composer caret column | 2 | **2** |
| submit key | Enter | **Enter** |
| what `session type` must send to submit | `\n` **or** `\r` | **`\r` only — `\n` inserts a newline** |
| a ~900-char fast injection | collapses to `[Pasted text #1]` | rendered in full, wrapped |
| a ~1500-char fast injection | collapses to `[Pasted text #1]` | **collapses to `[Pasted Content 1461 chars]`** |

So a Codex peer needs no profile worth the name: `classify` accepting either glyph covers it and the
caret gate is unchanged — but it needs **both paste spellings**, `[Pasted text` and
`[Pasted Content`. ⚠️ agterm's own cookbook says "Tab for Codex" — that is about **queueing while
busy**, not submitting; Enter submits an idle composer.

🔴 **The fourth row said "rendered in full" and this page concluded from it that Codex was the
*easier* case, nothing being hidden behind a placeholder. That conclusion was WRONG, and the way it
was wrong is the reusable part.** It was measured once, at one length. MEASURED 2026-08-26 on a live
Codex row: a **1461-character** delivery collapsed to `› [Pasted Content 1461 chars]` — a
placeholder after all, spelled differently. The threshold is somewhere between the two lengths and is
not worth pinning; what matters is that the row was a fact about the message tested, not about Codex.

Downstream, `agb-peer`'s `deliver` found neither the body (collapsed) nor a paste mark (wrong
spelling), raised exit 4 — which the relay **drops rather than retries**, on purpose — and a real
message sat in Codex's composer waiting for a human to press Return. `PASTE_MARK` is `PASTE_MARKS`
now, and matched **case-insensitively**: `cat -A` on the pane read `Content`, the operator watching
the same row read `content`, and case distinguishes nothing here worth losing a message over. **A per-agent rendering read off one sample is a sample, not a constant**; this page's own rule
about running rather than reading does not protect against running it once.

⚠️ **A submit key sent immediately after the text is LOST.** Measured twice: text then Enter with no
gap left the line sitting in the composer; text, one second, Enter submitted it. `agb-peer`'s
`deliver` already sleeps between the two, for an unrelated reason, and is not exposed.

### 🔴 `session type "\n"` submits Claude and does NOT submit Codex

MEASURED 2026-08-26, after a Codex peer spent an evening receiving messages it never acted on:

| what arrives at the pane | Claude | Codex |
|---|---|---|
| raw `0x0A` (LF) | newline **inserted** | newline **inserted** |
| raw `0x0D` (CR) | *not run* | **submitted** |
| agterm `session type "\n"` | **submits** (verified since 0.3.0) | newline **inserted** |
| agterm `session type "\r"` | **submits** | **submits** — ✅ verified live 2026-08-26 |

The third row is the one that cost the messages, and it was read by **counting blank lines**: an
empty Codex composer renders one blank line above the model line, and the loaded one rendered two.
So the Return *was* going out and *was* arriving — as a newline. That is why the symptom read as
"the Return is never sent" for so long: the relay had done its part.

What agterm actually puts on the wire for `"\n"` is **not observable from the farm side**, and it
does not matter: `\r` is what a real Return key sends and it is the only one of the two both TUIs
agree about. `agb-peer`'s `SUBMIT_KEY` is `"\r"`.

### 🔴 And an idle Codex submits where a working one does not

MEASURED 2026-08-26. `session type "\r"` submits an **idle** Codex composer — verified live, twice,
with nothing else touching the pane. Delivered into a **working** Codex the identical call typed the
body and left the Return in the composer as a newline. That is what made the whole thing read as
intermittent.

`agb-peer` does not chase the key semantics for that case; it refuses to type into a working peer at
all, which is the right behaviour under any reading. The signal is on the pane, because the agterm
row status cannot help: it comes from the agent's own agbridge hooks and **Codex fires none**.

| agent | what it renders while working |
|---|---|
| Codex | `• Working (6s • esc to interrupt)` — and `tab to queue message` once the composer has text |
| Claude | `⏵⏵ bypass permissions on (shift+tab to cycle) · esc to interrupt · ← for agents` |

⚠️ Claude's line is **transient and never reaches the scrollback** — grepping history for it returns
nothing on an agent that has worked all evening. It was caught by polling the pane every half second.

⚠️ **`agb pane`'s menu still gets a literal `"\n"`, and merging the two would break it silently.**
That prompt is a shell `read` on a tty in **canonical** mode, where the line discipline's `ICRNL`
makes CR and LF equivalent — and that path is verified as it stands. A TUI puts the tty in **raw**
mode and decodes keys itself, so the equivalence does not hold there. Same keystroke, two readers;
only the raw-mode one is picky. A structural test pins the asymmetry.

### ⚠️ A sandboxed Codex is RECEIVE-ONLY

Codex runs model-generated shell commands in a sandbox, and that sandbox refuses the tmux socket:

```
agb-peer: cannot read tmux options for %90:
error connecting to /tmp/tmux-100000/default (Operation not permitted)
```

Not a file-permission problem, measured three ways: under `workspace-write` the sandbox writes to
`/tmp` happily and `ls` sees the socket, and the connect still fails — which points at unix-socket
connects being blocked outright rather than at the filesystem policy.

Since `agb-peer`'s doorbell **is** a tmux window name and its message store **is** a tmux option,
that makes a sandboxed Codex a peer that can be **sent to** and can never **send**. The escape hatch
is running it with the sandbox bypassed, which is a security decision for the human and which
`agb-codex` deliberately will not do on its own.

⚠️ **And that hatch may not exist.** MEASURED on this host, Codex refuses to start with it:

```
Error loading configuration: `approval_policy = "never"` cannot be used because requirements do
not allow `sandbox_mode = "danger-full-access"`
```

An **org policy** forbidding the mode, not a bad flag.

⚠️ **And the near-miss does not work either.** The refusal is precise: that sandbox is forbidden only
in combination with `approval_policy = "never"`, so `codex -s danger-full-access -a on-request`
**starts cleanly**. It does not help — MEASURED, in that configuration the policy silently downgrades
to read-only (bash cannot even create a heredoc temp file) and the socket is refused exactly as
before. The full table, all measured on one host:

| configuration | result |
|---|---|
| `read-only`, `workspace-write` | socket blocked |
| `danger-full-access` + `on-request` | starts, but downgraded to read-only; socket still blocked |
| `--dangerously-bypass-approvals-and-sandbox` | refused by org policy, will not start |

So where such a requirement is in force there is **no configuration at all** in which a Codex peer
can send. It is receive-only, permanently — send to it, and do not expect it to answer.

⚠️ One portability bug fell out of the attempt, and it was in a file claimed to be model-agnostic:
`skills/agb-peer/SKILL.md` said "always through `--stdin` and a quoted heredoc, **never** as an
argument". A heredoc needs a writable temp file, which a read-only sandbox does not provide, so that
instruction is unfollowable there. The skill now names the fallback.

⚠️ Worth recording separately: `codex sandbox -c sandbox_mode="danger-full-access"` behaved *more*
restrictively than `workspace-write` (it could not write `/tmp` at all), so that `-c` override does
not appear to be honoured by the `sandbox` subcommand. Do not use it to reason about what a real
session's policy permits.

### ✅ `codex queue` is a first-party delivery channel, and it is better than typing

```sh
codex queue --thread <session uuid> --message "<text>"
```

Measured: a message queued to a **live** TUI session was answered 16 seconds later. No keystrokes,
no composer, no caret check, no paste or wrap problem, no submit key — and it **queues**, so it is
safe to send mid-turn, which is the property the keystroke path cannot have.

Constraints, all measured:

- ⚠️ **The thread UUID only exists after the session's FIRST COMPLETED TURN.** A freshly started
  Codex has no rollout file and is not addressable. It appears as
  `~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<uuid>.jsonl`, and the uuid is in the filename.
- ⚠️ **No app-server daemon is needed** — which contradicts what `codex agents` implies. On this host
  the daemon could not start at all (`codex app-server daemon start` requires the *standalone*
  install at `~/.codex/packages/standalone/current/codex`; a shared managed install under
  `/shared/tools` is not it). `queue` worked regardless.
- ⚠️ **`queue` succeeds against a DEAD thread too**, silently. Measured: queueing to a killed
  session's uuid printed `Queued message …` exactly as for a live one. So the command's success is
  **not a delivery receipt**, and anything that needs one must still read the pane afterwards.

## An agent on a machine you cannot ssh to — MEASURED 2026-08-25

A pool of compute machines, jobs land on one at random, they mount the same NFS as everything else,
and **the Mac cannot ssh to them**. All three legs of `agb-peer` looked like they needed that ssh.
Two of them turned out not to.

### ✅ Inbound works, via a chain opened from the reachable side

```
Mac → ssh → container host → tmux (an `agb-tmux` row) → pool session → codex
```

Start `agb-tmux` on a host you *can* reach, and inside that shell submit the interactive pool job and
start the agent there. `agtermctl session type` types into the pane, and the pane is connected all
the way down. **Nothing ever connects INTO the pool**, which is the whole reason it works — the same
shape as agbridge's founding constraint, that the Mac pulls and nothing pushes to the farm.

Verified live: a message reached a Codex on `compute-node-example` and it acted on it.

### ✅ The pool's permissions remove the sandbox blocker

Codex there starts in **`permissions: YOLO mode`** — the bypass that org policy refuses on the
container is allowed on that pool. The failure moved accordingly, from `Operation not permitted`
(the sandbox refusing the socket) to `No such file or directory` (no socket to refuse).

### ❌ But the tmux socket does not travel, and NFS cannot carry one

`$TMUX` and `$TMUX_PANE` **are inherited** through the job submission — the pool agent knows it is
`%99` of `/tmp/tmux-100000/default`. That path is on the container; `/tmp` is local to each machine,
so from the pool it does not exist.

⚠️ **Putting the socket on NFS does not help — MEASURED, not assumed.** `tmux -S /home/user/.agb-sock/test`
creates a working server: it accepts connections and holds pane options, everything the doorbell
needs. From the pool machine, with the server still alive and the socket file plainly present on the
shared mount, `tmux -S … list-sessions` answers **`no server running`**. A unix socket is a local
kernel rendezvous, not a filesystem object NFS knows how to carry.

So a pool agent can be **sent to** and cannot **send** — for a third distinct reason, after Codex's
sandbox and the missing socket.

### What would close it

Outbound needs a surface that crosses NFS, which means ordinary file operations:

- **the doorbell** becomes `agb rename "<base> [peer #id]"` — it writes the record on NFS, rides the
  feed that already exists, and lands in the row's **title**, which the relay reads from `tree` on
  every tick anyway. Watching stays free.
- **the content** becomes a file in the statedir, fetched with one `ssh <feed-host> cat` — ssh to the
  **container**, which is reachable, never to the pool.

Every piece of that is already measured to work. None of it is built.

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

## Verbatim recording *(from the Mac — PARTIALLY RECORDED)*

Run these on the Mac and paste the output verbatim under each heading. Then reconcile the assumed
contract above and the stub, keeping the vocabulary rejection.

**Recorded so far: the two top-level listings, on 0.24.0, 2026-08-24.** The five `session <cmd>
--help` captures below are still open. Note `agtermctl session --help` and `agtermctl help session`
print the same listing; the second is what was run.

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

**CONFIRMED 0.24.0, 2026-08-24.** No `--version` subcommand or flag: `agtermctl --version` is
`Error: Unknown option '--version'`.

```
OVERVIEW: Drive agterm over its control socket.

USAGE: agtermctl <subcommand>

OPTIONS:
  -h, --help              Show help information.

SUBCOMMANDS:
  tree                    Print the workspace/session tree.
  events                  Continuously print control events.
  workspace               Workspace commands.
  session                 Session commands.
  surface                 Terminal surface commands.
  dashboard               Open a view-only grid of live sessions, or --close the open one.
  window                  Window commands.
  quick                   Quick terminal: visibility, type into it, read its text.
  sidebar                 Sidebar visibility and view mode.
  notify                  Post a desktop notification (default: the active session of the frontmost window).
  font                    Font size commands.
  keymap                  Keymap commands.
  config                  Config commands.
  theme                   Theme commands.
  pick                    Open, poll, or cancel a native fuzzy picker.
  restore                 Restore-running-command commands.

  See 'agtermctl help <subcommand>' for detailed help.
```

⚠️ **`restore` at top level is not `session restore`.** Its own help says so explicitly:
*"Not to be confused with `agtermctl restore clear`, which is app-global and clears every session's
CAPTURED foreground command; this one is per-session and clears only the override."* An app-global
command that discards captured state is worth knowing the name of before anyone types it.

### `agtermctl session --help`

**CONFIRMED 0.24.0, 2026-08-24** (captured as `agtermctl help session`).

```
OVERVIEW: Session commands.

USAGE: agtermctl session <subcommand>

OPTIONS:
  -h, --help              Show help information.

SUBCOMMANDS:
  new                     Create a session.
  duplicate               Duplicate a session: a fresh shell in its directory, placed right after it.
  close                   Close a session.
  select                  Select a session.
  go                      Navigate sessions: next|prev|first|last|next-attention|prev-attention.
  rename                  Rename a session.
  reveal                  Reveal a session's focused working directory in Finder.
  move                    Move a session: to another workspace, reorder with --to, or place relative to an anchor with --after/--before.
  type                    Inject text into a session.
  split                   Show or hide a session split (on|off|toggle).
  scratch                 Show or hide a session scratch terminal (on|off|toggle).
  focus                   Focus a split session's pane (left|right|other).
  resize                  Resize a split session's divider (set or nudge the left-pane fraction).
  copy                    Print a session's selected text (does not touch the system clipboard).
  paste                   Paste the system clipboard into a session (like ⌘V).
  select-all              Select a session's entire terminal buffer (like ⌘A).
  text                    Print a session's terminal buffer as plain text (does not touch the system clipboard).
  status                  Set a session's agent status indicator.
  restore                 Pin the command a session's pane re-runs on the next launch.
  flag                    Flag a session for the flagged working-set view (on|off|toggle|clear).
  seen                    Clear a session's unseen-notification badge without changing the selection or focus (idempotent).
  search                  Search a session's terminal output (open the bar, set a needle, or step matches).
  background              Set or clear a session's background (image, rasterized text, or solid color).
  overlay                 Open, resize, or close an ephemeral overlay terminal on a session.

  See 'agtermctl help session <subcommand>' for detailed help.
```

⚠️ **There is no `session list`.** The last open capture below asks for it; this listing answers it.
`tree` is the enumeration command, which is what agbridge already uses.

**All eight subcommands agbridge calls are present and spelled as coded** — `new`, `rename`,
`status`, `close`, `split`, `scratch`, `type`, plus top-level `notify` and `tree`.

### `agtermctl session new --help`

_not recorded_

### `agtermctl session status --help`

⚠️ **Evidence class: CONFIRMED behaviour, PARAPHRASED text.** These three were run on the Mac by
another agent and reported back over `agb-peer` — so the *results* were observed, but the wording
below is its summary and not a verbatim capture. Anything depending on exact spelling still wants a
paste. Recorded 2026-08-24.

- the state argument must be one of `idle` / `active` / `completed` / `blocked`
- flags include `--blink`, **`--sound`**, **`--color`**, **`--shape`**, `--pane`, `--target`

✅ **The vocabulary is ENFORCED — this closes a long-standing ASSUMED clause.**
`agtermctl session status bogus --target X` answers **`error: invalid status`** and exits **1**. The
Task 4b stub has rejected out-of-vocabulary statuses since it was written, on the strength of a
design argument alone; agterm really does refuse them, so the stub is not stricter than reality and
"there is no `unknown`" is a property of the tool and not just of this project.

⚠️ **`--sound`, `--color` and `--shape` were not in any previous survey.** They are per-row
presentation, which is the human's business rather than a bridge's on the same reasoning that keeps
`theme` and `font` out — but `--sound` is worth a second look, since agbridge currently has no way
to distinguish "an agent needs you" from "an agent finished" audibly.

### `agtermctl session rename --help`

**CONFIRMED behaviour, paraphrased** (same provenance as above):
`session rename <name> [--target] [--socket] [--json] [--window]` — sets the session's name.

### `agtermctl session close --help`

**CONFIRMED behaviour, paraphrased** (same provenance as above):
`session close [--target …] [--socket] [--json] [--window]`, and ⚠️ **`--target` is repeatable, so
several rows close in one call.** `agb close-done` closes them one at a time; batching is available
if that ever matters.

### `agtermctl session list --help`

**Answered without running it: there is no `session list`** — see the `session --help` listing above.
`tree --json` is the enumeration route and is what agbridge uses.

### Checks to make while recording

- does `session new` print the row id, and in what format?
- is `blink` sticky or one-shot? (the flag itself is confirmed accepted; this is still open)
- can `rename` be applied repeatedly to a live row?
- does `session close` exist, and does it take `--target`?
- ~~is any status outside the four-word vocabulary accepted (it must not be)?~~ **ANSWERED
  2026-08-24: no — `error: invalid status`, exit 1.**

### What `dashboard` actually does — **CONFIRMED live 2026-08-27**

Captured against the installed binary, because a claim about this command had been asserted in
`docs/commands.md` with **no entry here at all** and turned out to be wrong. That is the failure this
file exists to prevent.

| clause | class |
|---|---|
| cells are `<id>` or `<id>:left` / `<id>:right`; **ids or unique prefixes, never names** | CONFIRMED |
| **`session type` still works while a grid is open** — returns ok, and the text is there on read-back | CONFIRMED |
| ⚠️ **`:scratch` is REJECTED** — `invalid session id … use <id>, <id>:left, or <id>:right` | **CONFIRMED 2026-08-27** |
| ⚠️ …and it is a **parse-time** rejection, not a resolution check: byte-identical error whether the row has a scratch pane open or not | **CONFIRMED 2026-08-27** |
| a grid **cell** cannot be typed into — it is read-only to the keyboard | **CONFIRMED 2026-08-27** |
| ⚠️ a terminal **outside agterm** that launched the grid stays fully responsive — a blocking `read` there returns on Enter and the grid closes | **CONFIRMED 2026-08-27** |
| whether a shell running **inside** an agterm session stays responsive while a grid is up | **ASSUMED** — untested, and not needed: the documented way to drive `agtermctl` is from outside |
| a bare id takes **every pane** of the session, and the 9-cell cap counts **panes**, not sessions | CONFIRMED (help text) |
| `--mru` opens a grid of the window's most-recently-used sessions with **no ids given** | CONFIRMED (help text) |
| one cell alone is valid; there is no minimum of two | CONFIRMED |
| every id unresolvable → `error: no dashboard sessions resolved`, **exit 1**, nothing opens | CONFIRMED |
| ⚠️ **some ids unresolvable → prints `unresolved: <id>`, exit 0, and OPENS with the rest** | CONFIRMED |
| a malformed suffix (`id:notapane`, `a::b`) → invalid-id error, **exit 1**, rejected before opening | CONFIRMED |
| `:right` on a session with no split is *unresolved*, not an error | CONFIRMED |

⚠️ **The partial-success row is the one with teeth for a caller.** A mix of good and bad cells exits
**0** and opens a grid missing a participant, announcing it only on stdout. Anything wrapping this
must read the output for `unresolved:` rather than trusting the status, or a silently absent agent
reads as a working dashboard.


⚠️ **The read-only finding is about CELLS, and collapsing it into "you cannot interact at all" is
wrong.** A grid cell cannot be typed into — measured. The terminal that *launched* the grid is a
different thing entirely, and is **not** blocked: measured from an external terminal, a blocking
`read` waited normally and returned on Enter, closing the grid. An agterm overlay does not capture
the keyboard of a separate terminal application.

So a foreground "open, wait, close" is sound **when run from outside agterm**, which is what
`docs/commands.md` already recommends for other reasons. Running one from *inside* an agterm session
is untested and does not need to be: that is not the documented route.
