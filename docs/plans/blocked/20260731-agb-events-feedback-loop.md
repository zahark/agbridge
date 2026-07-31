# Close the feedback loop: read agterm's event ring

> ⛔ **BLOCKED — `agtermctl events` does not exist on the installed agterm.**
>
> Verified 2026-07-31 on the Mac, which is what Task 0 exists for: `agtermctl help events` falls
> through to the top-level help, and `agtermctl events --json` fails with `Unknown option '--json'`.
> The command is documented at `agterm.com/commands`, which describes a **newer build**. The same
> check showed `pick` missing and `restore` living at top level rather than under `session`.
>
> **Nothing here is wrong, and nothing here is implementable yet.** The design survives an agterm
> upgrade unchanged; when `events` appears, resume at Task 0 to capture the real JSON shape — which
> is still unknown and still must not be guessed. Add a capability probe and a stated minimum
> version at that point, since a build without `events` must degrade to today's behaviour rather
> than warn on every poll.
>
> **The row-loss problem this was written to solve has a better answer available today**: top-level
> `restore` pins the command a pane re-runs, which stops the row dying when `agb pane` exits —
> attacking the cause instead of reacting to the symptom.

*Revised after a review pass that found four critical defects, including one that made the
headline feature unreachable. Where a fix is non-obvious, the defect it closes is named.*

## Overview

agbridge is **write-only**. It tells agterm things — create this row, rename it, set its status —
and learns nothing back. Every failure that follows has the same shape: the map keeps naming a row
id agterm no longer has, and the bridge discovers it only by failing, once per poll, for ever.

`agtermctl events` reads the app's control-event ring. Polling it turns the one-way pipe into a
loop:

| event | what it repairs |
|---|---|
| `session.closed` | a row destroyed while its agent is alive — the common case, not an edge case |
| a changed `run` | agterm restarted, so every id we hold is dead. An automatic `agb-refresh` |
| `tree.changed` | a row dragged to another workspace, noticed continuously rather than at refresh |

### The discovery that motivates it

Written up in [`docs/agtermctl.md`](../agtermctl.md) → *"agterm closes a session when its command
exits"*, confirmed live and in isolation on 2026-07-31:

**agterm destroys a session when its command exits.** `q`, `quit` and `exit` are all
`PANE_QUIT_WORDS`, so any of them at the `agb pane` prompt ends `agb pane` with status 0 and
**agterm takes the row away** — while the agent carries on running. The test agent `closetest2`
stayed alive (tmux running, key in the marker) after its row vanished.

A row is lost by *leaving a prompt* — an accident, not an intent — and each one leaves a `bound`
entry naming a destroyed row. That is almost certainly the source of the `no such session` spam.

This reframes the central question. "React to `session.closed` by forgetting the binding and letting
the next report re-mint the row" sounds like it makes rows un-closable. It does not: since a
hand-`exit` is not an intent to close, **re-minting repairs a row you lost by accident.**

## Context (from discovery)

- **`agb_mac` only.** `RowRenderer` (`agb_mac:1540`, `__init__` at `:1553`) owns every `agtermctl`
  call and already has a heartbeat, `_render_tick` (`:1997`), with a rate-limited job hanging off
  it, `_reassert` (`:2010`). This is a second such job.
- `_agtermctl` (`:1650`) returns **stdout on success, `None` on failure**. ⚠️ It collapses *every*
  failure to `None` and swallows stderr into a warning — the caller cannot distinguish "cursor
  evicted" from "agtermctl missing". That limitation drives a design decision below.
- The warning dedup is in `_bridge_warn` (`:2276`), which is production wiring — the test `Harness`
  passes a plain list, so tests see every warning.
- `RowMap` (`:965`) stores `entries: key -> [row, bound?, title]`; a reverse lookup is a scan and
  covers bound and `[done]` alike. `forget` is `:1081`.
- Placements: `placements_path` (`:1215`), `tree_workspaces(run)` (`:1219`), `read_placements`
  (`:1266`), `_read_text` (`:1274`), `write_placements` (`:1285`).
- `NOTICE` (`:1379`) is the prefix exempt from dedup. ⚠️ `_notify` (`:2113`) is **not** the way to
  emit one here — see Technical Details.
- Tests: `tests/test_bridge_rows.py`. `_notifies` (`:940`), `_seens` (`:1113`), `_Clock` (`:919`),
  `_reassert_bridge` (`:932`) are the patterns to copy.
- ⚠️ **`agb` is at 102,429 of its 102,500-byte budget.** Nothing here may touch it.

### Test seams that must be extended first

Two are **broken for this feature** and would produce false-green tests. Found by review, verified
against source:

- **`tests/stubs/agtermctl:33`** rejects any argv whose first word is not `session` (`exit 2`). The
  end-to-end stub therefore fails every `events` call. It needs an `events` arm.
- **`Runner.__call__` (`tests/test_bridge_rows.py:75`)** computes `verb = argv[2]`. For
  `["agtermctl", "events", "--json", …]` that is `"--json"`, so **`Runner(fail=["events"])` does
  not fail the call** — it falls through to `(0, "", "")`. Every "a failed poll changes nothing"
  test would be exercising *rc 0 with empty stdout* instead, and its mutation would pass.

### Command shape

From `agterm.com/commands` — **documentation, not observation**:

```
agtermctl events [--json] [--kind KIND ...] [--run UUID --after SEQ] [--limit N]
```

- Returns a **batch and exits** — not a stream. An ordinary subprocess call: no second long-lived
  input, no new liveness story. (`docs/agtermctl.md` previously claimed otherwise; corrected.)
- The response carries `run` and `next`; resume with `--run RUN --after NEXT`, together.
- Kinds: `status`, `notify`, `session.created`, `session.closed`, `tree.changed` (100 ms coalesced).
- `--limit` default 100, range 1–1000. The ring holds the latest **4096** events per app process.

⚠️ **The JSON response shape is unknown and must not be guessed.** Where `run`/`next` sit, which
field of a `session.closed` record carries the row id, and whether a first cursorless poll returns
the newest or the oldest events are all unresolved — and `tree_workspaces` shows agterm nests
(`tree["result"]["tree"]["workspaces"]`, `agb_mac:1235`), so top-level is the *less* likely guess. A
wrong guess is a silent no-op against real agterm that every unit test confirms as working, which is
this project's documented recurring failure. **Task 0 captures the real output before any parser is
written.**

## Development Approach

- **Testing approach**: regular (code first, then tests), per repo practice — with the standing
  addition that **every guard is mutation-tested**: break it, confirm a *named* test fails, restore.
- ⚠️ **Every negative assertion must first assert non-vacuity.** "No binding was forgotten" is true
  of a poll that never ran, and several seams here make "never ran" easy to reach by accident.
- Complete each task fully before the next; tests are separate checklist items.
- **All tests pass before the next task starts.** `python3 -m pytest tests/ -q` (1436 at the start).

## Testing Strategy

- **Unit tests** in `tests/test_bridge_rows.py` with a recording `run` — no agterm, no network.
- **End-to-end** through `tests/stubs/agtermctl`, which needs the new `events` arm (above).
- **Live checks** under Post-Completion. This project's record is that the unit suite is not what
  catches agterm-facing mistakes: two of the last three features passed every test and still needed
  a fix after live use.

## Progress Tracking

- Mark items `[x]` immediately. `➕` for new tasks, `⚠️` for blockers.

## Solution Overview

```
_render_tick ──> _reassert            (existing, 30 s)
             └─> _poll_events         (new, ~5 s, backs off on failure)
                    │
                    ├─ run changed ──────> forget EVERY binding  (NOTICE + re-arm quiet_until)
                    ├─ session.closed ───> forget that one key
                    └─ tree.changed ─────> re-read tree once, merge placements
```

**Key design decisions:**

1. **Poll from the renderer, on the tick.** No thread, no timer, no new op source, no transport
   change. Accepted cost: a dead feed means no ticks and no polling — fine, since every row is `[?]`
   then anyway.
2. **Filter by kind.** `status`, `notify` and `session.created` are our **own echoes**.
3. **`forget`, not `unbind`.** `unbind` is the `[done]` path and keeps the entry, so `rebind` would
   hand back a dead id.
4. **The `run` is persisted on *any* observation, including the first.**
   ⚠️ *Closes review finding 1.* The obvious rule — persist only on a change — makes Task 6
   unreachable: first run recorded in memory only, nothing written, bridge restarts, agterm
   restarted meanwhile, no stored run to compare, **no detection**. That is exactly the scenario the
   persistence exists for.
5. **Placements are merged, never replaced.**
   ⚠️ *Closes review finding 11.* An earlier draft said both "merge" and "ordering matters or the
   file is erased" — contradictory. With merge semantics a fresh agterm's empty tree erases nothing,
   so the ordering hazard does not exist and no ordering guard is needed. Merge is chosen precisely
   because it removes a guard rather than adding one.
6. **Every reaction is idempotent** — forgetting an unbound key is a no-op, re-reading the tree is a
   no-op, a row id is never reused. A wrong cursor costs *work*, never correctness. That is what
   makes "drop the cursor and re-poll" safe.
7. **A stale cursor must be droppable after N consecutive failures.**
   ⚠️ *Closes review finding 3, the worst one.* After an agterm restart the bridge holds
   `--run A --after N` for a run that no longer exists. Every poll passes the dead cursor, agterm
   errors, `_agtermctl` returns `None`, the poll is classified failed and never interprets the run —
   backoff climbs to 300 s and **the restart is never detected.** The guard structure would lock out
   the single highest-value case. Since `_agtermctl` cannot report *why* a call failed, the only
   implementable rule is: after `EVENTS_CURSOR_RETRIES` consecutive failures, drop the cursor and
   poll unfiltered. Safe by decision 6.

➕ **One addition beyond the brainstorm, flagged not smuggled:** a config gate `watch_agterm`, on by
default. Every other notification feature has one, and this is the only feature that can *destroy*
state.

## Technical Details

**Constants** (beside `REASSERT_INTERVAL`, `agb_mac:1375`)

| | value | why |
|---|---|---|
| `EVENTS_INTERVAL` | 5.0 s | slower than the tick, faster than a human notices a stale row |
| `EVENTS_BACKOFF_MAX` | 300.0 s | a failing poll must not spawn a subprocess every 5 s for ever |
| `EVENTS_CURSOR_RETRIES` | 3 | consecutive failures before the cursor is dropped (decision 7) |
| `EVENTS_LIMIT` | 100 | agterm's own default |
| `EVENTS_KINDS` | `("session.closed", "tree.changed")` | everything else is our own echo |

**Renderer slots**: `events_at`, `events_backoff`, `events_run`, `events_next`, `events_fails`.

⚠️ **`events_at` is seeded `self.clock() + EVENTS_INTERVAL`**, following `reasserted`
(`agb_mac:1571`). *Closes review finding 8.* Seeding it `0` breaks two existing tests that assert a
tick emits nothing (`test_bridge_rows.py:594`, `:736`). But the seeded form means any test that
swaps in `_Clock` (which starts at `1000.0`) **without re-seeding `events_at` will never poll** —
comparing 1000.0 against a `time.monotonic()` in the millions. Task 3 therefore adds an
`_events_bridge` helper that swaps the clock *and* re-seeds, exactly as `_reassert_bridge` does.

**The cursor file** — `<config dir>/events-run`, one line, the `run` id. A missing or unreadable
file means *no stored run*, which can never trigger a forget.

**Poll argv**

```
agtermctl events --json --limit 100 --kind session.closed --kind tree.changed \
                 [--run <run> --after <next>]
```

⚠️ Assert this by **comparing the whole argv list**. `_options()` (`test_bridge_rows.py:118`) is
wrong here twice: `--json` takes the next word as its value, and repeated `--kind` collapses to the
last.

**How forget-all announces itself**: `self._warn(NOTICE + …)`, **not** `self._notify(…)`.
⚠️ *Closes review finding 16.* `_notify` (`agb_mac:2113`) also pops an `osascript` banner **and
consumes the `NOTIFY_INTERVAL` rate limiter**, which would suppress a real feed-death notification
for five minutes. This event belongs in the log, not on the screen.

**Failure handling**

| failure | behaviour |
|---|---|
| `events` absent (older agterm) | `_agtermctl` warns, returns `None`; feature does nothing |
| transient failure | interval doubles to `EVENTS_BACKOFF_MAX`; resets on first success |
| `EVENTS_CURSOR_RETRIES` consecutive failures | **drop the cursor**, poll unfiltered (decision 7) |
| rc 0 with empty stdout | a failed poll — this is what the default `Runner` returns |
| malformed line | skip the line, keep the batch |
| response with no usable `run` | a **failed** poll — never a changed run |

⚠️ **The destructive path.** A false run-change forgets every binding and mints duplicates. **Four**
guards — the fourth was in a table but in no checklist, which is how it would have been missed:

1. a **first** observation only records (and persists) the run;
2. a failed or unparseable poll is **never** evidence;
3. **a response whose `run` is absent, empty or not a string is a failed poll** — otherwise
   `None != "A"` destroys every binding;
4. forget-all emits a `NOTICE` and **re-arms `quiet_until`**.

⚠️ **Forget-all is a third catch-up moment.** *Closes review finding 4.* `_notify_new_row`'s
docstring (`agb_mac:1844`) names two — construction and `_render_live`. After a forget-all every key
is re-minted through `_create_row` → `_notify_new_row` with `quiet_until` long expired, giving **one
banner and one Dock bounce per agent**: precisely the failure `NEW_ROW_QUIET` was written to prevent.

## What Goes Where

- **Implementation Steps**: code, tests and docs here.
- **Post-Completion**: live checks needing a Mac.

## Implementation Steps

### Task 0: capture the real `events` output — **before any parser is written**

**Files:**
- Modify: `docs/agtermctl.md`

- [ ] on the Mac, run and record verbatim: `agtermctl help events`
- [ ] `agtermctl events --json --limit 5` with no cursor — record the **exact JSON**
- [ ] close a row by hand, then `agtermctl events --json --kind session.closed --limit 5` — record
      which field carries the row id
- [ ] re-run with `--run <run> --after <next>` to confirm the resume spelling
- [ ] determine whether a cursorless poll returns the **newest** or the **oldest** events; if oldest,
      note that the bridge must fast-forward and `next` handling becomes load-bearing
- [ ] record all of it in `docs/agtermctl.md`, tagging the **spelling** as documented-upstream and
      anything not observed as **ASSUMED**, following the `session scratch` precedent
      (`docs/agtermctl.md:20-24`). ⚠️ *Closes review finding 14* — `CONFIRMED` is defined as
      "observed directly", and a website read from the cluster is not that
- [ ] no code, no tests; Tasks 1–2 may proceed in parallel, **Task 3 may not**

### Task 1: `RowMap.key_for(row)` — the reverse lookup

**Files:**
- Modify: `agb_mac`
- Modify: `tests/test_bridge_rows.py`

- [ ] add `key_for(row)` to `RowMap` (`agb_mac:965`), scanning `entries` for `entry[0] == row`
- [ ] it must find **`[done]` entries too** — otherwise a closed `[done]` row leaves an entry naming
      a session that no longer exists, the bug `close-done` cannot clear today
- [ ] docstring: the map is a bijection, so at most one key matches; `None` for unknown
- [ ] write tests: finds a bound key; finds a `[done]` key; `None` for an unknown row id
- [ ] mutation-test: restrict the scan to bound entries → the `[done]` test must fail
- [ ] run `python3 -m pytest tests/ -q`

### Task 2: the persisted `run` cursor file

**Files:**
- Modify: `agb_mac`
- Modify: `tests/test_bridge_rows.py`

- [ ] add `events_run_path()` beside `placements_path()` → `<config dir>/events-run`
- [ ] `read_events_run(path=None)` — the stored run or `None`; missing, empty or unreadable is
      `None`, never an error (reuse `_read_text`)
- [ ] `write_events_run(run, path=None)` via `agb.atomic_write`, like `write_placements`
- [ ] validate on read; anything implausible is treated as absent. **Failing safe here means "no
      stored run", which can never trigger a forget**
- [ ] write tests: round trip; missing file; empty file; garbage content
- [ ] run tests

### Task 3: the test seams, then the poll

**Files:**
- Modify: `tests/stubs/agtermctl`
- Modify: `tests/test_bridge_rows.py`
- Modify: `agb_mac`

⚠️ The seams come **first**. Both are broken for this feature and both produce false-green tests.

- [ ] `tests/stubs/agtermctl`: add an `events` arm before the `!= "session"` rejection at `:33`,
      emitting a JSON batch, honouring `$AGB_AGTERMCTL_FAIL`
- [ ] `Runner.__call__` (`test_bridge_rows.py:75`): recognise `events` off **`argv[1]`**, not
      `argv[2]`, and return a configurable JSON payload. *Closes review finding 7* — without this
      `Runner(fail=["events"])` silently does not fail
- [ ] add `_events_bridge(mac, rows_file)` beside `_reassert_bridge`, swapping the clock **and**
      re-seeding `events_at`
- [ ] add the five constants and the five renderer slots, `events_at` seeded per Technical Details
- [ ] `_poll_events()`: rate-limit on `self.clock()` as `_reassert` does; build the argv; call
      `_agtermctl`; parse into `(run, next, [events])`
- [ ] on failure or unparseable response: increment `events_fails`, double `events_backoff` to the
      cap, return **without interpreting anything**. On success: reset both
- [ ] after `EVENTS_CURSOR_RETRIES` consecutive failures, drop `events_run`/`events_next` and poll
      unfiltered (decision 7)
- [ ] skip malformed records individually; one bad line must not lose the batch
- [ ] call `_poll_events()` from `_render_tick` after `_reassert()`
- [ ] write tests: full-argv comparison; cursor passed **only** when known; rate-limiting; backoff
      doubling; **the cap is reached and not exceeded**; reset on success; malformed line survives;
      rc 0 with empty stdout is a failed poll; **the cursor is dropped after N failures**
- [ ] mutation-test: remove the backoff → the growth test fails; remove the cursor drop → the
      recovery test fails
- [ ] run tests

### Task 4: react to `session.closed`

**Files:**
- Modify: `agb_mac`
- Modify: `tests/test_bridge_rows.py`

- [ ] resolve each closed row id via `RowMap.key_for` and `rows.forget(key)`
- [ ] an id we do not hold is a **no-op**, not a warning — agterm has sessions that are not ours
- [ ] comment the reasoning: this repairs a row lost by leaving the prompt; `forget` not `unbind`
- [ ] write tests: a bound key is forgotten; an unknown id changes nothing; a `[done]` entry is
      forgotten; the row is re-minted on the next report. **Each negative test asserts an `events`
      call was actually recorded first**
- [ ] mutation-test: `unbind` instead of `forget` → a named test must fail
- [ ] run tests

### Task 5: react to a changed `run` — the destructive one

**Files:**
- Modify: `agb_mac`
- Modify: `tests/test_bridge_rows.py`

- [ ] compare the response's `run` with `self.events_run` on a successful poll
- [ ] **guard 1** — no stored run: record it, **persist it** (decision 4), forget nothing
- [ ] **guard 3** — a `run` that is absent, empty or not a string makes this a *failed* poll
- [ ] on a genuine change: `self._warn(NOTICE + …)` (**not** `_notify`), forget every binding,
      re-arm `self.quiet_until = self.clock() + NEW_ROW_QUIET`, persist the new run
- [ ] update `_notify_new_row`'s docstring (`agb_mac:1844`) to name **three** catch-up moments
- [ ] write tests: first response records **and writes the file** without forgetting; a changed run
      forgets all; **a failed poll forgets nothing** (with the fixed `Runner`); **a response with no
      `run` forgets nothing**; the `NOTICE` is emitted; no desktop notification is emitted;
      **no new-row banner follows the re-mint**
- [ ] mutation-test **all four guards** — treat a first observation as a change; interpret a failed
      poll; accept a `None` run; drop the `quiet_until` re-arm — each must fail a named test
- [ ] run tests

### Task 6: detect an agterm restart that happened while the bridge was down

**Files:**
- Modify: `agb_mac`
- Modify: `tests/test_bridge_rows.py`

- [ ] seed `self.events_run` from `read_events_run()` at construction
- [ ] a first successful poll then compares against the **stored** run, so a mismatch means agterm
      restarted while we were not watching — the case that had `agb-refresh` run by hand
- [ ] ⚠️ write the **round-trip** test, not just seeded-file tests: poll → run persisted → construct
      a *new* renderer → it reads that run back. *Closes review finding 2* — seeded-file tests alone
      pass green while the production path that creates the file does not exist
- [ ] write tests: stored `A`, poll returns `B` → all forgotten, `B` persisted; stored `A`, poll
      returns `A` → nothing forgotten
- [ ] run tests

### Task 7: react to `tree.changed`

**Files:**
- Modify: `agb_mac`
- Modify: `tests/test_bridge_rows.py`

- [ ] if the batch contains **any** `tree.changed`, call `tree_workspaces(self.run)` **once**
- [ ] map `{row: workspace}` to `{key: workspace}` through the row map and **merge** into the
      existing placements (decision 5), then `write_placements`
- [ ] `tree_workspaces` returning `None` means *could not ask* → leave the file alone
- [ ] ⚠️ update the comment at `agb_mac:1558` — "it cannot change under a running bridge" becomes
      false the moment the renderer writes this file
- [ ] note in the task that `render_settings` has **no** `placements` key, so production always
      writes `placements_path()`; tests must pass `settings={"placements": …}`
- [ ] write tests: placements updated; **one** tree read for several `tree.changed` in one batch;
      `None` leaves the file untouched; a run change does not erase placements
- [ ] mutation-test: read the tree per event instead of per batch → the one-read test fails
- [ ] run tests

### Task 8: the `watch_agterm` config gate

**Files:**
- Modify: `agb_mac`
- Modify: `tests/test_bridge_rows.py`

- [ ] add `watch_agterm` to `render_settings` (`agb_mac:2152`) via `config_flag`, default **True**
- [ ] ⚠️ the renderer-side read needs its own default —
      `(self.settings or {}).get("watch_agterm", True)`, following `agb_mac:1820`/`:1857`. The test
      `Harness` constructs with `settings or {}`, so without it **every test in Tasks 3–7 silently
      does nothing**
- [ ] `_poll_events` returns immediately when off — no subprocess, no state change
- [ ] write tests: off → no `events` call and nothing forgotten; **on → the call IS made** (the
      positive control, without which the negative is vacuous)
- [ ] mutation-test: delete the gate → the "off" test fails
- [ ] run tests

### Task 9: verify acceptance criteria

- [ ] `[q]`/`exit` at the pane prompt no longer costs a live agent its row: the row returns
- [ ] a `[done]` row closed by hand **while the bridge is polling** is cleared from the map
      (⚠️ *review finding 13*: the two entries already in the map may pre-date the current agterm
      process and its 4096-event ring, so they are **not** a valid test)
- [ ] an agterm restart is detected and every binding re-minted, without `agb-refresh` —
      **including when the bridge was down at the time**
- [ ] a forget-all produces **no** banner storm
- [ ] a row dragged to another workspace updates `placements` within one poll
- [ ] with `events` unavailable, the bridge behaves exactly as it does today
- [ ] run the full suite: `python3 -m pytest tests/ -q`
- [ ] `wc -c agb` — **must still be 102,429**; this plan touches `agb_mac` only

### Task 10: [Final] Documentation and release

**Files:**
- Modify: `docs/agtermctl.md`, `docs/commands.md`, `README.md`, `CLAUDE.md`, `CHANGELOG.md`
- Modify: `agb` (the `VERSION` line only)

- [ ] `docs/agtermctl.md`: finish the Task 0 recording; **remove `events` from the "What agbridge
      does not use yet" menu** so that list stays a menu rather than a fiction
- [ ] `docs/commands.md`: a section under `agb bridge` with the failure table and the four guards
- [ ] `README.md`: the `watch_agterm` config row and a behaviour bullet; ⚠️ `README.md:152-155`
      ("the bridge only sets a workspace when a row is created") goes stale — fix it
- [ ] `CLAUDE.md`: "Everything the bridge tells agterm" gains a **read** direction; add the seam
      lesson — *the `Runner` fixture keys verbs off `argv[2]`, which is wrong for any top-level
      verb, and `tests/stubs/agtermctl` rejects non-`session` argv*
- [ ] record the accepted cost: **no way to dismiss a live agent's row**, with the "dismissed set"
      alternative named as the door
- [ ] `CHANGELOG.md` under `## Unreleased`, house style — say *why*, name the symptom
- [ ] bump `VERSION` (`agb:24`) — new behaviour, **minor**. Confirm length-neutral
- [ ] move this plan to `docs/plans/completed/`

## Post-Completion

*Needs a Mac with agterm running.*

**Live checks, in order** — the first is the whole point:

- click a live agent's row, type `exit`, confirm the row **comes back** within a couple of seconds
  instead of being lost until `agb-refresh`
- confirm `~/Library/Logs/agbridge/bridge.err.log` stops accumulating `no such session`
- quit agterm and reopen it; confirm rows are re-minted **without** `agb-refresh`, in the right
  workspaces, and with **no banner storm**
- **kill the bridge, restart agterm, then start the bridge** — the cross-restart detection, which is
  the case the persisted cursor exists for and the one a memory-only design silently fails
- drag a row to another workspace, wait a poll, check `~/.config/agbridge/placements`
- ⚠️ **watch for duplicates.** Forget-all is the destructive path; if restart detection misfires the
  symptom is two rows per agent

**Two things that would look like failures and are not**, from earlier features: a banner or badge
shows only on a row that is **not** selected, and anything measured within seconds of a reinstall may
land inside a quiet window.
