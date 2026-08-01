# A banner when a long-running agent finishes

*Revised three times after review: 6 findings, then 9, then 10 — each pass finding defects in the
previous pass's fixes. Pass 3 simulated the sketch rather than reading it, and caught two tests that
could not fail under the mutations they were named for. Every finding was re-verified against
source; see "What review changed" at the end. **Pass 3 is the last: its remaining findings were
folded in, and further passes are not planned.***

## Overview

agbridge raises two `agtermctl notify` banners today: a transition into `blocked`
(`notify_on_blocked`) and a new row appearing (`notify_on_new_row`). Neither covers the case that
actually costs time on a cluster: you start a long job on a detached agent, walk away, and can only
learn it finished by going back and looking.

This adds a third banner for **an agent finishing its turn** — the `Stop` hook, state `completed` —
gated on **how long the turn took**. The filter is not a refinement, it is the feature: `completed`
fires *once per turn*, so an unfiltered banner would bounce the Dock three seconds after you type
"yes", on the row you are staring at. A threshold turns "the agent stopped" into "the thing you
walked away from is done".

Integration is narrow. This is entirely Mac-side rendering: one new dict on `RowRenderer`, one new
method beside `_notify_blocked`, one config key. Nothing about the wire protocol, the hot path, the
farm side, or `agb` itself is involved.

## Context (from discovery)

- **Files involved**: `agb_mac` — `RowRenderer.__slots__` (`:1679`), `__init__` (`:1707`),
  `_forget_unmapped` (`:1753`), `_render_upsert` (`:1937`), `_notify_blocked` (`:1939-1983`),
  `_render_remove` (`:2066`), `render_settings` (`:2300`, notify keys at `:2342-2343`),
  `CONFIG_FALSE` (`:2352`) and `config_flag` (`:2355`). Plus `agb_ops.CONFIG_KEYS` (`:228-229`),
  `tests/test_bridge_rows.py` and `tests/test_core.py`.
- **Patterns found**: every banner is a small `_notify_*` method called unconditionally from
  `_render_upsert`, gating itself on a **transition** held in its own memory, reaching `agtermctl`
  only through `self._agtermctl`, and treating failure as a warning rather than an exception.
  ⚠️ **All three existing gates pass a default to `.get()`** — `.get("notify_blocked", True)` at
  `:1977` and `:2062`, `.get("notify_new_row", True)` at `:2014`. The new gate does the same.
- **Dependencies identified**: none new. No new subprocess, no new import, no new file.
- ⚠️ **`config_flag`, `CONFIG_FALSE` and the notification half of `render_settings` have zero
  tests today.** The off-switch tests inject `settings={"notify_blocked": False}` directly and never
  go through `config_flag`, so the *"`0` is truthy in Python"* reasoning at `agb_mac:2348-2352` is
  unexercised. Task 1 adds the first coverage.
- ⚠️ **Three config keys are already missing from two places.** `notify_on_blocked` and
  `notify_on_new_row` are absent from `agb_ops.CONFIG_KEYS`, so `agb doctor` reports two documented
  keys as *"unknown key -- a typo here is silent everywhere else"*. And the config **table** in
  `docs/design.md` (`:1294-1302`) lists seven keys, including neither of them nor `workspace`.
  Adding a fourth key to lists already missing three is how drift compounds, so both are fixed here
  (Tasks 3 and 5).

### Verified against the current tree (2026-08-01, HEAD `adc3284`, 1777 tests green)

⚠️ **Re-anchor before starting.** This plan was first written against `bd95749` and the tree moved
three commits (`ee8e976`, `8417ad9`, `adc3284` — `install.sh mac --instance auto`). The anchors
below were re-measured at `adc3284`, but they will drift again; treat every line number as a hint
and grep for the symbol.

- **The notification path is still untouched.** Zero diff hunks between `_render_upsert` and
  `_status` across everything since 0.4.0.
- **`render_settings` keeps its `(opts, config=None)` signature** but threads
  `path = opts.get("config")`, so a new key read off the passed-in dict is **instance-aware for
  free** — `agb bridge --config` and `install.sh mac --instance` need nothing.
- ⚠️ **The bridge reads its config exactly once** (`agb_mac:2482`), pinned by
  `test_the_bridge_reads_its_config_exactly_once` (`tests/test_bridge_transport.py:1118`). The new
  key **must** come off the dict `render_settings` was handed, never a fresh `agb.read_config()`.
- ⚠️ **`render_settings` runs on *every* path that builds a renderer**, including `--from-stdin` —
  `bridge_sink` calls it at `agb_mac:2376`, and the only escape is `opts["no_agterm"]`, which
  builds no renderer at all. That same test's docstring says so in as many words
  (`tests/test_bridge_transport.py:1123-1125`). An earlier draft of this plan claimed the opposite;
  it was wrong.
- **`BRIDGE_VALUE_FLAGS`, `same_map`, `config_map_dir` and `parse_bridge_args` are the `--config`
  *CLI flag's* plumbing, not the file's contents.** A config key touches none of them — proof:
  `notify_on_blocked` appears nowhere in `agb-refresh`. Invariant 14 does not apply, and
  `agb-refresh`, `install.sh`, `dist/com.agbridge.plist` and `agb_ops.CONFIG_VALUE_ARGS` /
  `CONFIG_WRITE_ORDER` / `install_config_values` are all untouched.
- **`agb` is not touched.** Its guard is `len(agb_source) < AGB_PARSE_BUDGET`
  (`tests/test_mac_split.py:382`) — **characters, not bytes**. `agb` is 102 429 B but **102 419
  chars** against 102 500, so the real headroom is **80 characters**, not the 71 the docs quote.
- **`Harness.send(kind, now=NOW, **fields)` and `.upsert(session, now=NOW)` already accept the feed
  clock** (`tests/test_bridge_rows.py:147-154`), so driving duration needs **no harness change**.
  `.stale()` (`:165`) bypasses `apply()`, so a disconnect does not advance `model.now` at all.
- ⚠️ **`BridgeModel._upsert` drops an identical record before the renderer sees it** —
  `if self.sessions.get(key) == session: return []` (`agb_mac:336-337`) — and `wire()` has constant
  `beat`/`updated`/`seq` (`tests/test_bridge_rows.py:42-52`). **Every repeat upsert in a test must
  vary `seq`**, or the renderer is never entered and the test proves nothing. Precedent:
  `test_a_block_that_persists_is_announced_once` (`:1146-1152`) loops `seq in range(1,6)`.
- ⚠️ **`model.now` never returns to `None` once set.** `BridgeModel.apply` assigns only for a
  numeric value and otherwise leaves the previous one (`agb_mac:276-278`). This has a consequence
  for the sketch below: given `started is not None`, `now is None` is **unreachable**. Keep the
  clause as defensive code; do not try to test it on the `completed` side.
- **`BEAT_LATE = agb.BEAT_INTERVAL * 2 = 30.0`** (`agb:974`, `agb_mac:1454`), so `beat_age_text`
  returns `""` below it (`agb_mac:1514-1516`). Unit-testable, and Task 2 tests it.
- **`CHANGELOG.md` already has `## Unreleased`** at `:9` with `### Added` at `:11`, added by
  `ee8e976`. **Append to it — do not create a second heading.**
- **Both new names are free.** `config_seconds` and `_notify_completed` appear nowhere in the tree.

## Development Approach

- **Testing approach**: **regular** (code first, then tests), matching this repo's practice — with
  the project's standing addition: **every new guard is mutation-tested.** Break it, confirm a
  *named* test fails, restore it. A guard whose removal keeps the suite green is not a guard.
- ⚠️ **Every silent-direction test needs an announcing companion** that differs only in the variable
  under test. Five of this plan's tests assert "no banner"; without a companion under the *same*
  settings, they pass against a feature that can never fire. This repo has shipped four vacuous
  guards, and that is the shape they took.
- ⚠️ **Every mutation must name a victim.** Two mutations in the previous draft could not fail any
  listed test. Before writing a mutation into this plan, name the test it breaks.
- Complete each task fully before moving to the next.
- **Every task includes its tests**, listed as separate checklist items.
- **All tests pass before the next task starts.** `python3 -m pytest tests/ -q` (1777 passing at
  `adc3284`, ~52 s).
- Python 3.6.8 is the floor: no f-strings with `=`, no dataclasses, no walrus.
- Update this plan when scope changes; `➕` for discovered tasks, `⚠️` for blockers.

## Testing Strategy

- **Unit tests**: `tests/test_bridge_rows.py`, driven by the recording `Runner` (`:57`) and
  `Harness` (`:132`) — no agterm, no ssh, no network. What is asserted is the argv.
- ⚠️ **No test may assert an exact count over `_notifies`.** `_notifies` (`:1118`) returns *every*
  notify call, including `_notify_new_row`'s. A count of 1 in a fresh `bridge()` holds only because
  `quiet_until` happens to suppress the new-row banner (`agb_mac:1720-1724`, gate at `:2016`) — a
  reason unrelated to this feature, which will silently stop being true. Add a `_finished(b)` helper
  filtering `_notifies` on the body, and count *that*. A **non-emptiness** assertion over
  `_notifies` is fine and is sometimes required, as a non-vacuity guard.
- **No e2e tests in this project**: there is no UI harness. The substitute is the live-agterm check
  under Post-Completion, because `agtermctl`'s behaviour is the one thing this repo does not own —
  and `CLAUDE.md` records that **two of the three existing notification paths needed a fix *after*
  the live test, in ways 1400 tests could not catch.**
- **Mutation testing is the acceptance bar for guards**, per `CLAUDE.md`. Substring greps over
  source do not count as structural tests — they pass by matching explanatory comments.

## Progress Tracking

- Mark completed items `[x]` immediately.
- `➕` prefix for newly discovered tasks, `⚠️` for issues or blockers.
- Keep the plan in sync with the work actually done.

## Solution Overview

One module constant, one new dict on `RowRenderer`, one new method beside `_notify_blocked`, and
one line in `_render_remove`:

```python
COMPLETED_AFTER = 300.0     # module level, so the default lives in exactly one place

def _notify_completed(self, key, row, session, state):
    now = self.model.now                      # the feed's clock -- never self.clock()
    if state == "active":
        if now is not None and key not in self.working:
            self.working[key] = now           # first sighting wins
        return
    started = self.working.pop(key, None)     # popped BEFORE the config gate, on purpose
    if state != "completed" or started is None or now is None:
        return
    after = (self.settings or {}).get("notify_completed_after", COMPLETED_AFTER)
    if not after or (now - started) < after:
        return
    ...  # self._agtermctl(["notify", body, "--title", ..., "--target", row])
```

**Key design decisions:**

1. **The trigger is `completed`, not `idle`.** `idle` is bridge-only — `[?]` feed-quiet and
   `[done]` removal — and `docs/design.md:785` records a farm stall flipping every row to `[?]`
   roughly every 12–16 s. A banner on that is an anti-feature.
2. **The Dock bounce is not a lever this tool has.** `agtermctl notify` takes only
   `<body> [--title] [--target] [--window]` (`docs/agtermctl.md:185`); the bounce is agterm's
   *global* setting and already fires on `blocked`. So the only buildable version of "add a bounce"
   is "send a banner on one more event" — 1:1, in both directions.
3. **The `pop` *is* the transition memory.** A re-upsert of the same `completed` record finds
   nothing and stays silent. No second set to maintain, unlike `RowRenderer.blocked`.
4. **Disconnect-immune by construction.** `_render_stale` never routes through `_render_upsert`,
   and `key not in self.working` keeps the **original** start time across a reconnect. This is the
   `applied`-is-the-wrong-gate trap `CLAUDE.md` warns *"will cause a third"* bug — it structurally
   cannot fire here, because this memory is never written by a paint.
5. **Burst-immune by construction.** A fresh renderer's first snapshot shows finished agents as
   `completed` with no `working` entry, so it is silent. It needs no `quiet_until` and gets none.
   ⚠️ This holds for a *reconnect snapshot*, not for a **`--from-stdin` replay**, where the `active`
   upserts are in the stream and long turns will banner. Noted in Known limits.
6. **`blocked` resets the clock**, so "you answered a prompt and it finished three seconds later"
   is silent. Correct: you already got a banner for that block.
7. ⚠️ **A removal ends the turn: `_render_remove` pops `working` too.** This is the third mutator
   of row state and the only one the immunity argument does not cover. Without it, an `active`
   agent that is removed (a complete snapshot dropping it, or `agb prune`) and then re-asserted as
   `completed` would banner a duration **spanning the removal** — a turn nobody observed.
   `_forget_unmapped` does not save us **on this path**: a `[done]` entry is deliberately still
   `rows.known()` (`agb_mac:1748-1750`), so the reclaimer skips it by design. Put the pop **before**
   the `row is None` early return, but ⚠️ **do not write a load-bearing reason into the comment** —
   two drafts of this plan tried and both were wrong. It is simply defensive and free. The
   `unbind`-returns-`None` case is *already* covered by `_forget_unmapped`: that branch is only
   reachable after `_merge_disk` dropped the key (`agb_mac:1209-1211`), and the merge happens inside
   `rows.save()` (`:1735`) immediately before `_forget_unmapped()` (`:1739`) **in the same
   `__call__`** — so `working[key]` is gone before any later batch could deliver that `remove`.
8. **`started` is popped before the config gate**, exactly as `self.blocked.add()` precedes it at
   `agb_mac:1976` — turning the key on mid-flight must not produce a backlog of stale durations.
9. **One numeric config key, not a boolean plus a threshold.** The number *is* the switch, so there
   is one thing to explain and no way to write a combination that silently does nothing.
10. **The renderer carries its own default, `COMPLETED_AFTER`.** ⚠️ *Not* because `render_settings`
    might not run — it runs on every renderer-building path including `--from-stdin`
    (`agb_mac:2376`), and an earlier draft claiming otherwise was wrong. The real reasons: the
    three existing gates all do it (`:1977`, `:2014`, `:2062`), a module constant single-sources
    the number, and a `RowRenderer` built with `{}` — which is how every test and any future direct
    caller builds it — behaving unlike production is a trap for the next reader. The
    threshold-vs-boolean asymmetry costs nothing, because `config_seconds` maps *off* to `0.0` and
    *absent* to the default, so "off" and "not configured" stay distinguishable through
    `if not after`.

**Known limits, documented rather than fixed** — all the same measurement being a heuristic, and
all belong in `docs/commands.md`:

- **Sub-poll floor.** The bridge polls every 2 s, so a turn shorter than one poll is never observed
  as `active` and can never announce. Right answer for a short turn, wrong reason.
- **Restart floor.** A fresh renderer first sees an already-running agent as `active` and measures
  from *there*, so a three-hour turn interrupted by a restart announces as whatever elapsed since.
- **Outage inflation.** Keeping the original start across a 600 s watchdog outage (decision 4)
  means the announced duration can be mostly outage. Deliberate — the alternative is losing the
  turn — but it is wall time, not work.
- **Replay banners.** `agb bridge --from-stdin` against a captured feed re-emits banners for every
  long turn in the recording (decision 5).
- ⚠️ **A turn that finished while the bridge process was down is never announced at all.** This is
  decision 5 stated from the user's side, and it is the limit they will hit hardest: the feature
  goes quiet exactly when you were away longest. Distinguish it in the docs from a *connection*
  loss, where the process survives, `working` survives with it, and the banner **does** fire — with
  a duration inflated by the outage.

## Technical Details

**The config contract.** `notify_on_completed_after`, read through a new `config_seconds` helper
beside `config_flag`, sharing `CONFIG_FALSE` so there is one falsy vocabulary rather than two:

| spelling | result | why |
|---|---|---|
| absent | `COMPLETED_AFTER` | an existing config gets the feature without being edited |
| `` (empty) | `COMPLETED_AFTER` | same rule as `config_flag` |
| `0`, `off`, `no`, `false` | `0.0` → disabled | reuses `CONFIG_FALSE` |
| `-5` | `0.0` → disabled | a negative threshold cannot mean anything else |
| `120` | `120.0` | |
| `5 minutes` | `COMPLETED_AFTER` | never raises. ⚠️ `agb doctor` validates key *names*, not values — say so in the docstring, because a silent fallback is exactly the surprise this project dislikes and the alternative (raising from a render path) is worse |

**The banner**, mirroring `_notify_new_row`'s `"<label> started"` + optional `" in <cwd>"`:

```
body  = "<label> finished"  [+ " after <duration>"]
--title  agbridge: <host>
--target <row>
```

⚠️ **The two existing banners disagree on the missing-label fallback** — `_notify_blocked` uses
`label or key` (`agb_mac:1980`), `_notify_new_row` uses `label or "an agent"` (`:2022`). Use
**`label or key`**: this banner is about a specific row you can click, and `"an agent finished"` on
a keyless record tells you nothing.

`<duration>` is `beat_age_text(now - started)`. ⚠️ **Guard the empty return** — `""` below
`BEAT_LATE` (30 s) — and drop the whole `" after …"` clause rather than emitting
`"finished after "`. This is the one output-shaping rule in the change, it is reachable from a unit
test, and Task 2 tests it rather than leaving it to the live check.

**Where the pieces go** (`agb_mac` at `adc3284`):

| # | site | change |
|---|---|---|
| 1 | `__slots__` `:1679-1681` | add `"working"` |
| 2 | `__init__` `:1707` | `self.working = {}` beside `self.blocked`, with the "not derived from `applied`" reason |
| 3 | `_forget_unmapped` `:1753` | `working` joins the dict tuple `(self.seen, self.applied, self.titles)` |
| 4 | `_render_upsert` `:1937` | `self._notify_completed(key, row, session, state)` after `_notify_blocked` |
| 5 | after `:1983` | the new method |
| 6 | `_render_remove` `:2074` | `self.working.pop(key, None)` **before** the `row is None` return |
| 7 | near `NEW_ROW_QUIET` | `COMPLETED_AFTER = 300.0` |
| 8 | `:2355` | `config_seconds` beside `config_flag` |
| 9 | `render_settings` `:2343` | `"notify_completed_after": config_seconds(config, "notify_on_completed_after", COMPLETED_AFTER)` |

## What Goes Where

- **Implementation Steps**: code, tests and docs in this repo.
- **Post-Completion**: the live-agterm check, which needs a Mac with agterm running.

## Implementation Steps

### Task 1: Add `config_seconds`, and the first tests for `config_flag`

**Files:**
- Modify: `agb_mac`
- Modify: `tests/test_bridge_rows.py`

- [x] add `config_seconds(config, key, default)` immediately after `config_flag` (`agb_mac:2363`),
      implementing the table under Technical Details: absent/empty → `default`, `CONFIG_FALSE` → 0,
      unparseable → `default`, `<= 0` → 0, otherwise `float(value)`
- [x] docstring records **why it never raises** (it runs on the render path, where an exception
      would wedge a paint) and that `agb doctor` therefore cannot catch a mistyped *value*
- [x] write a table-driven test covering every row of that table, negative and garbage included
- [x] ⚠️ write the **first ever** test for `config_flag` in the same place — all four `CONFIG_FALSE`
      spellings, absent, empty, and a truthy value. The `"0"`-is-truthy reasoning at
      `agb_mac:2348-2352` is the entire reason the function exists and is currently unexercised
- [x] mutation-test: return the default for `0` instead of disabling → the table test must fail
- [x] run `python3 -m pytest tests/ -q` — must pass before Task 2

### Task 2: Add the `working` memory and `_notify_completed`

**Files:**
- Modify: `agb_mac`
- Modify: `tests/test_bridge_rows.py`

**Code:**

- [ ] add `COMPLETED_AFTER = 300.0` at module level near `NEW_ROW_QUIET`, with decision 10's reason
- [ ] add `"working"` to `__slots__` (`:1679-1681`) and `self.working = {}` in `__init__` beside
      `self.blocked` (`:1707`), commented with what it holds and why it is **not** derived from
      `applied`
- [ ] add `self.working` to the dict tuple in `_forget_unmapped` (`:1753`)
- [ ] add `_notify_completed` after `_notify_blocked` (`:1983`), exactly as in Solution Overview —
      including the `COMPLETED_AFTER` default on the `.get()` and the `beat_age_text` empty guard
- [ ] add `self.working.pop(key, None)` to `_render_remove` (`:2074`) **before** the `row is None`
      return, with decision 7's reason in a comment
- [ ] call `_notify_completed` from `_render_upsert` (`:1937`) right after `_notify_blocked`
- [ ] ⚠️ **the docstring must not name all four status words.**
      `test_the_status_vocabulary_has_exactly_one_source` (`tests/test_bridge_rows.py:2167`) walks
      the *whole* `agb_mac` AST including docstrings. Naming `active`, `blocked` and `completed`
      without `idle` is safe
- [ ] ⚠️ record in a comment that the `now is None` clause on the `completed` side is
      **unreachable twice over** — `model.now` never returns to `None` once set
      (`agb_mac:276-278`), and `working` is only written when `now is not None`, so
      `started is not None` implies `now is not None`. Kept as defence. The *reachable* `None`
      guard is the one in the `active` branch

**Harness:**

- [ ] add a `_finished(b)` helper beside `_notifies` (`:1118`) filtering on the body — no test may
      count total banners (see Testing Strategy)
- [ ] add `"_notify_completed"` to the tuple in
      `test_the_renderer_never_consults_the_macs_own_clock` (`:2159-2161`). ⚠️ **This guards
      `time.time`/`time.monotonic` only** — `conftest.calls` yields `("self", "clock")` for
      `self.clock()` (`tests/conftest.py:271-283`), so it does **not** pin "uses `model.now`". That
      is caught behaviourally by the disconnect test

**Tests.** ⚠️ Except where stated, **every test passes `settings={"notify_completed_after": <n>}`
explicitly**, and **every repeat upsert varies `seq`** (`agb_mac:336-337`).

- [ ] `test_a_long_turn_is_announced_when_it_finishes` — `active` at `NOW`, `completed` at
      `NOW + 400` with `seq=2`; one finished banner, right `--target`, label in the body
- [ ] `test_a_short_turn_finishes_silently` — the same pair 10 s apart; zero. Companion to the above
- [ ] ⚠️ `test_the_default_threshold_applies_with_no_settings` — build with **`settings={}`** and
      drive a 400 s turn; one banner. **This is the only victim mutation (i) has**, and no existing
      test can stand in: every existing `active`/`completed` upsert in the file runs at the harness
      default `now=NOW`, so their durations are 0
- [ ] `test_a_finished_turn_is_announced_once_not_per_snapshot` — the second `completed` carries
      `seq=3` so it genuinely reaches the renderer; still one banner
- [ ] ⚠️ `test_a_disconnect_mid_turn_does_not_reset_the_timer` — **pin every clock, or mutations (a)
      and (b) both pass.** With `settings={"notify_completed_after": 300}`: `active` at `NOW` →
      `stale("eof")` → `active` at **`NOW + 350`** (`seq=2`) → `completed` at `NOW + 400` (`seq=3`);
      assert one banner. `Harness.upsert` defaults to `now=NOW`
      (`tests/test_bridge_rows.py:153`), so the free reading puts the second `active` at the *same
      instant* as the first — under which correct code and both mutations all announce, and even
      asserting the duration text does not discriminate. At `+350`, correct code measures 400 s and
      announces; either mutation measures 50 s and goes silent. ⚠️ Also the test that catches
      `self.clock()` substituted for `model.now`
- [ ] `test_a_reconnect_full_of_finished_agents_is_silent` — call `_past_quiet(b)` (`:1190`) first
      so new-row banners **do** fire, then assert the total is non-zero and the finished count is
      zero. That non-emptiness assertion is the non-vacuity guard
- [ ] `test_a_block_mid_turn_restarts_the_clock` — `active` → `blocked` → `active` → `completed`
      shortly after; silent. Companion: the same sequence without the block announces
- [ ] `test_a_removal_then_a_rebind_does_not_announce` — `active`, `remove`, then a `completed`
      upsert that rebinds; silent. Companion: without the removal it announces. Covers decision 7
- [ ] ⚠️ `test_a_first_event_with_no_feed_clock_does_not_poison_the_turn` — target the **`active`
      branch**, and **three events, not two**, or it proves nothing:

      ```
      send("upsert", now=None, session=wire(k, "active"))        # no feed clock yet
      assert b.renderer.working == {}                            # the guard, directly
      upsert(wire(k, "active",    seq=2), now=NOW)               # clock arrives
      upsert(wire(k, "completed", seq=3), now=NOW + 400)         # announces
      ```

      There is **no `TypeError`** to catch — an earlier draft claimed one, and it is wrong:
      `working.pop(key, None)` returns `None` for a *stored* `None` exactly as for an absent key, so
      `started is None` short-circuits. The real damage is subtler and is what the third event
      exposes: without the guard, `working[key] = None` is stored, which makes the later
      `key not in self.working` **false**, so the real clock is never recorded and the turn is
      silently lost. With the guard the banner fires; without it, nothing does
- [ ] ⚠️ `test_a_sub_thirty_second_turn_names_no_duration` — `settings={"notify_completed_after":
      5}` with a 10 s turn; the body is exactly `"<label> finished"`, no trailing `" after"`.
      Companion at 400 s asserting the duration text **is** present. This is `beat_age_text`
      returning `""` below `BEAT_LATE`, and it is the one output rule the live check would
      otherwise be first to find
- [ ] ⚠️ `test_the_finished_banner_can_be_turned_off` — `settings={"notify_completed_after": 0}`.
      **Two assertions, both required.** `key in b.renderer.working` **between** the `active` and
      `completed` upserts — not at the end, where the pop has already run — and then
      `assert b.renderer.working == {}` **after** the `completed` one. The second is not decoration:
      it is the *only* discriminator for mutation (d), since a gate that returns before the pop
      leaves the entry in place while both `_finished(b) == []` and the mid-test membership
      assertion still hold. (The `:1181` template asserts membership at the end because
      `self.blocked` is a set that persists; `working` is popped by decision 8, so a literal copy
      fails — and the tempting "fix" *is* mutation (d))
- [ ] ➕ `test_two_long_turns_in_a_row_are_both_announced` — `active`/`completed`, then
      `active`/`completed` again, all long, `seq` climbing; **two** banners. The `pop` is the entire
      re-arming mechanism (decision 3) and nothing else pins it: an implementation that announced
      once per key — a `set` of announced keys, added by someone "stopping repeats" — would pass
      every other test in this list
- [ ] ⚠️ `test_working_memory_is_reclaimed_when_the_map_forgets_a_key` — spell it out:
      `upsert(active)` → `b.rows.forget(key)` → `tick()` → `assert b.renderer.working == {}`.
      **No `remove()` step.** The template at `:2344` has one (`:2356`), and decision 7's pop would
      empty `working` there, making the assertion — and mutation (f) — vacuous. The path is real:
      an external `agb close-done` merged by `rows.save()` drops a key that is still `active`
- [ ] mutation-test, each naming its victim. ⚠️ **Three of these are only real if the tests above
      are written as specified** — (a) and (b) need the disconnect test's pinned clocks, (d) needs
      the off-switch test's *final* emptiness assertion. A mutation whose victim still passes is a
      false guarantee, and this plan has shipped three of them across two review passes:

      | | mutation | victim |
      |---|---|---|
      | a | gate on `applied` instead of `working` | disconnect test — **needs `+350`** |
      | b | assign unconditionally instead of `key not in working` | disconnect test — **needs `+350`** |
      | c | drop the `pop` on non-`active` states | block test (its companion forces the span) |
      | d | move the `pop` after the config gate | off-switch test — **needs the final `== {}`** |
      | e | remove the `< after` comparison | short-turn test |
      | f | drop `working` from `_forget_unmapped` | reclamation test — **needs no `remove()`** |
      | g | `self.clock()` instead of `model.now` | disconnect test |
      | h | drop the `_render_remove` pop | removal test |
      | i | drop the `COMPLETED_AFTER` default | the no-settings default test |
      | j | drop the `beat_age_text` empty guard | the sub-thirty-second test |
      | k | announce once per key instead of popping | the two-consecutive-turns test |
      | l | drop the `now is not None` guard in the `active` branch | the no-feed-clock test |
- [ ] run `python3 -m pytest tests/ -q` — must pass before Task 3

### Task 3: Wire the setting through, and teach `agb doctor` all three notify keys

**Files:**
- Modify: `agb_mac`
- Modify: `agb_ops`
- Modify: `tests/test_core.py`

- [ ] add `"notify_completed_after": config_seconds(config, "notify_on_completed_after",
      COMPLETED_AFTER)` to the `render_settings` return dict beside the other two (`agb_mac:2343`)
- [ ] ⚠️ read it off the **`config` dict the function was handed**, never a fresh
      `agb.read_config()` — `test_the_bridge_reads_its_config_exactly_once`
      (`tests/test_bridge_transport.py:1118`) pins the single read at `agb_mac:2482`
- [ ] add `notify_on_completed_after`, `notify_on_blocked` **and** `notify_on_new_row` to
      `agb_ops.CONFIG_KEYS` (`:228-229`) — the latter two are drift, documented in four places but
      reported by `agb doctor` as typos since 0.4.0
- [ ] add one line per new key to the hand-written config blob in
      `test_parse_config_reads_the_documented_keys` (`tests/test_core.py:196-206`)
- [ ] ⚠️ write a test hardcoding **the whole documented key list**, not just the three new ones.
      Both existing tests iterate `CONFIG_KEYS` itself (`tests/test_core.py:209-210`, `:232-233`),
      so removing a key makes them weaker rather than failing — which is exactly why this drift
      survived since 0.4.0. Hardcoding only the new three leaves the other seven pinned by nothing
- [ ] write a test that `render_settings` surfaces `COMPLETED_AFTER` with an empty config and 120
      with `notify_on_completed_after = 120`
- [ ] mutation-test: drop any key from `CONFIG_KEYS` → the hardcoded test must fail
- [ ] run `python3 -m pytest tests/ -q` — must pass before Task 4

### Task 4: ➕ Give the `agtermctl` stub a `notify` arm

*Optional and smaller than it looks — see the first item.*

**Files:**
- Modify: `tests/stubs/agtermctl`
- Modify: `tests/test_bridge_rows.py`

- [ ] ⚠️ **the stub already records `notify`** — `:31` appends every argv *before* the
      `$1 != "session"` rejection at `:33-36` — so the recording assertion can be written today
      without touching the stub. What the arm actually buys is **exit 0** (removing a warning
      `test_the_bridge_drives_agtermctl_end_to_end` never checks) and a `--target` requirement
- [ ] add a `notify` arm before the `session` check: require `--target`, exit 4 without
- [ ] strengthen `test_the_bridge_drives_agtermctl_end_to_end` (`:1823`) to assert the recorded
      `notify` call **and** a clean warning channel. ⚠️ Do not assert the arm exists by grepping
      the stub's source — `CLAUDE.md` forbids substring guards because they match the comment that
      describes the rule. The recording is the oracle
- [ ] ⚠️ **`seen` is deliberately left out.** Its arm would never execute: `_seen` needs a
      `blocked` → non-`blocked` transition (`agb_mac:2030`, `:2047-2050`) and that test's event
      list is `active → blocked → remove`, which never reaches it. The prerequisite, if it is ever
      wanted, is one more `active` upsert in that test — recorded here rather than done
- [ ] run `python3 -m pytest tests/ -q` — must pass before Task 5

### Task 5: Update the user-facing docs and the changelog

**Files:**
- Modify: `README.md`
- Modify: `docs/commands.md`
- Modify: `docs/design.md`
- Modify: `.claude/skills/agbridge/SKILL.md`
- Modify: `CHANGELOG.md`

- [ ] `README.md` config table (the `notify_on_blocked` row, `:165` at `adc3284`) — one row stating
      the default and that the number is the switch
- [ ] `docs/commands.md` beside the `blocked` banner (`:116`, `:133-134`) — the threshold, the
      transition memory, and ⚠️ **all four known limits**: sub-poll floor, restart floor, outage
      inflation, replay banners
- [ ] ⚠️ `docs/design.md:1294-1302` — the config **table** is missing `workspace`,
      `notify_on_blocked` and `notify_on_new_row` as well. Add all four rows; adding one to a table
      missing three leaves the design authority wrong about its own config surface. There is no
      doc-consistency test anywhere in `tests/`, so this fails silently and for ever
- [ ] **verify only** — `docs/design.md:1354`, the §5 per-instance row, already reads *"`statedir`,
      `feed_host`, `mac_id`, the notification switches"*. Generic and plural, so a third switch
      needs no edit. Confirm and move on; an earlier draft listed this as work
- [ ] `.claude/skills/agbridge/SKILL.md` — the Notifications table (`:309-313` at `adc3284`) **and**
      the Config-keys table (`:331-342`, notify row at `:341`). ⚠️ These moved; the `:314-323`
      anchor in the first draft now points at the Workspaces section
- [ ] ⚠️ `CHANGELOG.md` — **append to the existing `## Unreleased`** (`:9`, `### Added` at `:11`,
      added by `ee8e976`). Do **not** create a second heading, as the first draft of this plan said
- [ ] the entry says **why**, per house style: `completed` fires once per turn, so the threshold is
      the feature and not a refinement. Name the rejected alternatives (`idle` as trigger, a
      bool-plus-threshold config), and carry the caveats forward — the banner and bounce fire even
      for the row you are looking at, and the duration is wall time
- [ ] ⚠️ state the **upgrade effect**: the default is 300, so this is **on** for every existing
      install after `sh install.sh mac`, with `notify_on_completed_after = off` as the opt-out
- [ ] ⚠️ if the code lands in its own commit, the changelog entry belongs **in that commit** —
      `CLAUDE.md`'s rule is same-commit, because by release time the reason is gone
- [ ] no tests (documentation only); run the suite anyway

### Task 6: Verify acceptance criteria

- [ ] a turn longer than the threshold produces exactly one banner naming the label and the row
- [ ] a turn shorter than the threshold produces none, and one under 30 s names no duration
- [ ] a disconnect mid-turn changes nothing — not the timer, not the count
- [ ] a removal mid-turn ends it: a later rebind announces nothing
- [ ] a bridge restart or reconnect announces nothing for agents that were already finished
- [ ] `notify_on_completed_after = off` is silent, and re-enabling produces no backlog
- [ ] the existing `blocked` and new-row banners are unchanged in count, wording and gating
- [ ] `agb doctor` no longer warns about any `notify_on_*` key
- [ ] run the full suite: `python3 -m pytest tests/ -q`
- [ ] confirm `agb` is untouched: `python3 -c 'print(len(open("agb").read()))'` must still print
      **102419** against the 102 500 ceiling (`tests/conftest.py:63`). ⚠️ The guard counts
      **characters**; `wc -c` reads 102 429 and is the wrong number to compare

### Task 7: [Final] Update `CLAUDE.md` and close out

**Files:**
- Modify: `CLAUDE.md`
- Modify: `docs/plans/completed/` (move this file)

- [ ] `CLAUDE.md` — add the new call to the "Everything the bridge tells agterm" table, and extend
      the notifications paragraph to cover three switches rather than one
- [ ] `CLAUDE.md` — record the property worth keeping: this banner's transition memory is a `pop`,
      which makes it disconnect- and burst-immune *structurally*. The file already warns that the
      `applied`-as-a-gate confusion *"has now caused two separate bugs — assume it will cause a
      third"*; naming a case immune by construction is how the third is avoided
- [ ] ➕ `CLAUDE.md` — record the three test-harness traps this plan hit, because all are general
      and none is written down: `BridgeModel._upsert` drops an identical record before the renderer
      sees it (a repeat-upsert test must vary `seq`); `_notifies` returns every banner (a count
      assertion silently depends on `quiet_until`); and `model.now` never returns to `None` once
      set, so a "no clock" test must target the first event, not a later one
- [ ] `CLAUDE.md` — re-measure and write the test count the suite actually reports. ⚠️ Do not copy
      the 1777 from this plan; these tasks add roughly twenty more
- [ ] ➕ `CLAUDE.md`'s "Where the project is" is **already stale before this change** — it says
      *"Released 0.4.0"* and *"1436 tests"* while `agb:24` reads `0.5.0` and the suite reports 1777.
      Fix it in the same edit rather than adding a third wrong number to it
- [ ] leave `agb:24` `VERSION` alone — the release is not part of this change
- [ ] move this plan to `docs/plans/completed/`

## Post-Completion

*No checkboxes — these need a Mac with agterm running and cannot be done from the farm.*

**Install first.** `git pull` is not enough: the bridge loads `agb_mac` from
`~/.local/lib/agbridge/`, not from the checkout. Run `sh install.sh mac …` with the original flags,
then `agb-refresh` — and `agb-refresh --instance <name>` for every other instance, since a running
bridge holds the code it started with. Rows do **not** need re-minting; `agb pane` is unaffected.

**Manual verification on the Mac** — the real acceptance test. `CLAUDE.md` records that two of the
three existing notification paths needed a fix *after* their live test:

- ⚠️ **check agterm's Settings ▸ Notifications ▸ Dock-icon bounce before anything else.** It
  defaults to *off*, and with it off a working feature reads as broken.
- set `notify_on_completed_after = 20`, restart the bridge, start a detached agent with
  `agb-claude -d longjob`, give it a task running well over 20 s, and confirm one banner naming the
  label — plus the bounce — when it stops.
- give the same agent a trivial one-word prompt that finishes in seconds and confirm **no** banner.
  **This is the check that matters**; it is the entire reason the threshold exists.
- ⚠️ run both on a row that is **not selected**, with agterm in the background, so the unseen badge
  is observable too. Per `docs/agtermctl.md:229` the banner and bounce fire either way, but the
  badge is never raised on the row you are looking at — testing on the selected row invalidated a
  live test once already.
- confirm the body at 20 s reads `"longjob finished"` with no dangling `after` (the sub-`BEAT_LATE`
  case now also covered by a unit test).
- set the key to `off`, restart, confirm silence.
- run `agb doctor` on a cluster host; no `notify_on_*` key should be reported as unknown.
- tail `~/Library/Logs/agbridge/bridge.err.log` throughout for `agtermctl` failures.

**Release, deliberately not part of this plan.** A new config key is a **minor** bump: `agb:24`
`VERSION` `0.5.0` → `0.6.0`, length-neutral and so unable to move the parse budget.

## What review changed

Two passes. Recorded so the next reader does not re-derive it, and because most findings were
mechanical facts about the harness rather than judgement calls. **Every claim below was
re-verified against the source**; one row in the first version of this table was wrong and is
corrected here.

**Pass 1 — six findings:**

| Finding | Evidence | Change |
|---|---|---|
| The gate had no default, so Task 2's tests would fail or pass vacuously | the three existing gates pass one (`agb_mac:1977`, `:2014`, `:2062`) | `COMPLETED_AFTER` + `.get(..., COMPLETED_AFTER)`; every test injects settings; silent tests get companions |
| An identical re-upsert never reaches the renderer | `agb_mac:336-337`; `wire()` has constant `seq` | every repeat upsert varies `seq`; count assertions replaced with a body filter |
| `_render_remove` leaks `working`, and `[done]` is never reclaimed | `agb_mac:2074-2078`, `:1748-1750` | decision 7, plus its own test and mutation (h) |
| The clock guard does not catch `self.clock()` | `conftest.calls` yields `("self","clock")` (`tests/conftest.py:271-283`) | rationale corrected; mutation (g) re-attributed to the disconnect test |
| The stub's `seen` arm would never execute | `agb_mac:2030`, `:2047-2050` | `seen` dropped from Task 4, reason and prerequisite recorded |
| `docs/design.md`'s config **table** is missing three keys | the table at `:1294-1302` lists seven, without `workspace` or either notify key. ⚠️ The first version of this row cited `grep -c workspace docs/design.md → 0`; it returns **7** — `workspace` appears elsewhere, just not in the table | Task 5 adds all four rows; evidence scoped to the table |

**Pass 2 — nine findings, two of them in pass 1's own fixes:**

| Finding | Evidence | Change |
|---|---|---|
| Mutation (i) had no possible victim — every test injected settings, so dropping the default changed nothing | no existing test has a non-zero duration: all `completed` upserts run at `now=NOW` | added `test_the_default_threshold_applies_with_no_settings`, built with `settings={}` |
| The "no feed clock" test was structurally vacuous — `now is None` is unreachable given `started` | `agb_mac:276-278` leaves the previous value; `working` is only set when `now is not None` | retargeted at the **`active`** branch. ⚠️ **The replacement was still vacuous and its reason was wrong — see pass 3.** The diagnosis held; the fix did not |
| The reclamation test would be vacuous if it copied its template | the template does `remove()` at `tests/test_bridge_rows.py:2356`, and decision 7's pop empties `working` there | sequence spelled out with **no `remove()` step** |
| The `--from-stdin` justification for the renderer default was **false** | `bridge_sink` calls `render_settings` at `agb_mac:2376` on every renderer path; `tests/test_bridge_transport.py:1123-1125` says so | decision 10 rewritten to the true reasons; replay added to Known limits |
| The off-switch test would fail if it mirrored `:1181` | `self.blocked` persists, `working` is popped by decision 8 | membership asserted between the two upserts, not at the end |
| The `beat_age_text` empty guard had no test | `BEAT_LATE = agb.BEAT_INTERVAL * 2 = 30.0` (`agb_mac:1454`); reachable with a 5 s threshold | `test_a_sub_thirty_second_turn_names_no_duration` + companion + mutation (j) |
| Anchors stale by three commits | HEAD moved `bd95749` → `adc3284`, 1777 tests | re-anchored; ⚠️ `CHANGELOG.md` **already has `## Unreleased`** (append, do not create) and `SKILL.md`'s tables moved to `:309-313` / `:331-342` |
| The stub already records `notify` before rejecting it | `tests/stubs/agtermctl:31` precedes `:33-36` | Task 4 scoped down to what the arm actually buys |
| Hardcoding only three keys leaves the other seven unpinned | `tests/test_core.py:209-210`, `:232-233` are self-referential | hardcode the whole documented list |

**Pass 3 — ten findings, four of them gating and three of those in pass 2's own fixes.** This pass
*simulated* the sketch instead of reading it, which is how it found two tests that could not fail
under the mutations they were named for:

| Finding | Evidence | Change |
|---|---|---|
| The no-feed-clock test pass 2 wrote was **still vacuous**, and its stated mechanism was false | `working.pop(k, None)` returns `None` for a *stored* `None` exactly as for an absent key, so `started is None` short-circuits — no `TypeError`. Simulated: identical banners with and without the guard | rewritten with **three** events; the real damage is `working[key] = None` blocking the later `key not in working` write, so the turn is silently lost. Mutation (l) added |
| The disconnect test had **unpinned clocks**, so mutations (a) and (b) both passed | `Harness.upsert` defaults `now=NOW` (`tests/test_bridge_rows.py:153`); simulated at `+0` all three variants announce `[400.0]`, at `+350` only correct code does | second `active` pinned at `NOW + 350`, threshold 300 |
| The off-switch test could not fail mutation (d) | a gate returning before the pop leaves the entry in place while both `_finished(b) == []` and the mid-test membership assertion still hold | final `assert working == {}` made an explicit requirement, not prose |
| Decision 7's reason for the pop placement was **unsupported** — the second wrong reason for the same line | `rows.save()` (`agb_mac:1735`) runs `_merge_disk` immediately before `_forget_unmapped()` (`:1739`) in the same `__call__`, so the `forget-rows` case is already covered | placement kept, reason reduced to "defensive and free", with a warning not to invent a third |
| The label fallback was unspecified, and the two existing banners **disagree** | `label or key` (`agb_mac:1980`) vs `label or "an agent"` (`:2022`) | `label or key` named, with the reason |
| Nothing pinned that a **second consecutive turn** announces | the `pop` is the whole re-arming mechanism; an "announce once per key" set would pass all 14 tests | new test + mutation (k) |
| The "bridge was down" limit was missing | decision 5 stated as a virtue; from the user's side the feature goes quiet when they were away longest | added to Known limits, distinguished from a connection loss |
| Testing Strategy forbade the assertion it then required | *"no test may count total banners"* vs *"assert the total is non-zero"* | rule reworded — exact counts banned, non-emptiness allowed |
| `docs/design.md:1354` needed no edit at all | the row already reads "the notification switches", generic and plural | demoted to a verify-only item |
| Six anchors had drifted 1–2 lines despite pass 2 claiming a re-anchor | `_past_quiet` `:1190`, `wire()` `:42-52`, `test_core.py` `:209-210`/`:232-233`, the template's `remove()` `:2356`, the 12–16 s sentence `:785`, `BEAT_LATE` in `agb_mac:1454` not `agb:974` | all corrected, here and above |

**What three passes did not find**, recorded because absence of a finding is itself information:
the design premise, the task ordering, the scope, and roughly seventy other file:line anchors were
re-verified and held. Every defect across all three passes was in **test specification** — never in
the feature. That is worth knowing before the next plan in this repo is written.
