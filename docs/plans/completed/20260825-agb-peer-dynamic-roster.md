# agb-peer: a dynamic roster — attach and detach a running relay

> **Plan A of two.** `agb-peer who` is [`20260825-agb-peer-who.md`](20260825-agb-peer-who.md).
>
> **Revision 5**, after four review rounds (6 + 6 + 6 + 5 criticals). See **Corrections from
> revision 4** — the design changed again, and a plan that swaps a mechanism silently reads as if
> the old one was never wrong.

## Overview

A relay's participants are fixed at startup: `spec = parse_participants(words)` runs once, before
the loop. Adding or removing an agent means killing the relay and restarting it — which also drops
`pending` and re-primes, discarding anything queued in the meantime.

This adds `--roster <path>`: a declarative file, re-read when it changes, so agents can attach and
detach a **running** relay.

⚠️ **It also fixes a pre-existing message-loss bug that the roster would otherwise make routine** —
see Task 1. That fix stands on its own and is sequenced first.

### What this plan does NOT do

⚠️ **N-way routing already works.** `parse_participants` enforces a *minimum* of two, not a maximum
— verified: four participants parse and route. The `a=`/`b=` in `USAGE` is naming, not a limit.

**Nothing here publishes anything to agents.** After this plan, attaching an agent changes where
messages route and nothing else. That is Plan B, and it is a named gap.

Out of scope: broadcast (`--to all`), rooms in any form, proactive join/leave announcements,
per-participant `--chat-dir`. Reasons in Task 11.

## Corrections from revision 4

1. ⚠️ **`seen[name]` is written BEFORE the drain, and revision 4 never acted on it.** `agb-peer:1332`
   sets `seen[name] = ident`; `drain` runs at `:1337`; the guard at `:1330` short-circuits on
   `seen.get(name) == ident`. So a failed fetch marks the participant caught-up having read nothing,
   and **never retries** — revision 4's headline test ("a joiner whose fetch fails stays pending and
   its backlog is discarded when the fetch later succeeds") was impossible against its own stated
   extraction. Revision 4's Context section *recorded this observation* and produced no checkbox.
   **Fix:** Task 3 moves the write to after a successful fetch — an intentional behaviour change,
   with its consequences (retries, and therefore throttles) in the same task.
2. ⚠️ **The outcome vocabulary was defined one layer too low.** Revision 4 put "no doorbell" among
   `drain`'s outcomes, but `:1330` short-circuits **before** `drain` is called, so `drain` can never
   observe it. Worse, the dominant real path for a joiner is **`MODE_MENU`** — every row
   `agb-refresh` re-mints comes back detached, and a detached row's doorbell is *not visible*, not
   *absent*. Classifying that as "nothing to fetch" clears the joiner unprimed and its backlog is
   delivered later: exactly the bug the mechanism exists to prevent, on the ordinary path.
   **Fix:** the vocabulary belongs to the extracted function (Task 4), enumerates **all five** of its
   exits, and defaults to **keep pending**.
3. ⚠️ **Membership was a question about `resolved`, and it should be about the spec.**
   `cmd_relay` passes `resolved` as `people` (`:1421`); `try_deliver:1239` does
   `if recipient not in people: say("dropping…"); return True` — a **permanent drop**. So a rebind
   whose new label does not resolve has its carefully-kept `pending` dropped in the same tick, and —
   far worse — **every message to a roster entry whose agent has not started yet is silently
   discarded.** For a declarative roster, listing three agents while two are up is the *normal*
   case. This is a pre-existing bug the roster promotes to routine; Plan B already states the
   correct rule, so revisions 3–4 had the two plans contradicting each other. **Fix:** Task 1,
   sequenced first and independently justified.
4. ⚠️ **An unbounded prime retry loses live messages.** While a name is in `needs_prime` its drains
   are discarded, so a participant stuck pending through a flaky-ssh spell has **everything it sends
   in that window thrown away**. Revision 4 retried for ever and justified clearing on "nothing to
   fetch" with *"discarding a real message is worse"* — without noticing the same argument condemns
   the unbounded retry. **Fix:** a bounded number of attempts, after which the name is cleared
   **without discarding**, loudly.
5. **A short read is not a small roster.** A file being rewritten in place can be read truncated at
   a line boundary and parses *cleanly* as a shorter roster — so revision 4 would have applied a
   spurious **leave**, clearing state and dropping messages. This is invariant 2 ("a short read is no
   information this poll, never gone") applied to the one input this plan adds. **Fix:** an empty
   read is a hold; the runtime minimum is **1**; the file is documented as needing an atomic write.

## Context (from discovery)

Verified against source across review rounds 3 and 4:

- `cmd_relay` runs `resolve_all` (`:1402`) **before** `relay_tick` (`:1421`), and passes
  **`resolved`** as `people`.
- `relay_tick` writes `seen[name] = ident` at `:1332` — **before** `drain` at `:1337`.
- ⚠️ The per-participant body has **five exits that never reach `drain`**: `session is None`
  (`:1293`), `ctl.text` error (`:1307`), `MODE_MENU` (`:1310`), `not ident` (`:1330`), and
  `seen.get(name) == ident` (`:1330`).
- `try_deliver:1239` **drops** a message whose recipient is not in `people`; its hold branch
  (`:1247`) only fires for a name that *is* in `people`.
- `resolve_all` (`:1381-1384`) keeps `previous[name]` whenever `name in previous`, and its `else`
  branch `say`s **with no throttle**.
- `resolve` refuses an *ambiguous* label — but two **different** labels can each unambiguously match
  the **same** row, and `resolve_all` has no cross-check.
- `parse_participants` raises on `len(people) < 2` (`:750`). One production call site (`:1393`);
  every test call site is positional single-argument.
- `drain`'s return is asserted in **seven** tests (`:610, 715, 730, 736, 1139, 1511, 1542`); none is
  positional past `say`/`ident`/`chat_dir`, so a trailing keyword is safe.
- `cmd_relay` with `once=True` returns **after the priming tick** (`:1427`), so **no existing test
  ever exercises a delivering tick through `cmd_relay`**. `once` is passed positionally as the 5th
  argument by three tests and by `main:1461` — any seam must be **additive**.
- `relay_tick` has ~20 positional call sites in the tests; its signature must stay compatible.
- `relay_tick:1354`'s discard line says "queued before this relay **started**" — false for a joiner.
- `word.partition("=")` splits on the **first** `=`, so a name containing `=` is unconstructible.
  Relay words come from `.split()`, so whitespace in a name is equally unreachable.
- `bob=/home/you/agbridge` parses today; `<row>` is a row-title substring.
- `notes` is created once (`:1399`) and **persists across ticks**.
- ⚠️ `conftest.functions(*trees)` raises on cross-file duplicates — **`statedir` and `main` both
  collide**. Structural guards must use the peer tree alone.
- ⚠️ `__pycache__/agb-peercpython-36.pyc` **exists** (dot-less spelling). Mutation checks must
  delete it.
- ⚠️ `tests/test_agb_peer.py:1114` passes `set()` as `seen`, surviving only because `if not ident`
  short-circuits before `seen.get(name)`. Any reordering there fails with a bare `AttributeError`.
- Baseline **2169** tests.


## Outcome — what actually happened

All thirteen tasks landed. 2254 tests (from 2169), 48 mutations, every one caught by a named test.
Three deviations from the plan, recorded because a ticked box that quietly meant something else is
worse than an unticked one:

1. **Two guards turned out to be defensive, not load-bearing, and say so in the source.** Skipping
   pending joiners in the scan sits behind `seen`, which a successful prime already writes; and
   `deliver_to` at the loop level converts a delay into an immediate delivery rather than preventing
   a loss. Neither can fail a test on its own. The `deliver_to` *property* is pinned on `relay_tick`,
   where it is observable.
2. **The Task 10 structural guard was dropped rather than written.** The plan already suspected it:
   a reachability edge exists whenever the feature works at all, so its mutation could never be shown
   load-bearing. The tick-budget test (`ticks=2`) is the real assertion about ordering.
3. **One extra fix, not in the plan.** An audit of every `say()` reachable from the relay loop found
   `try_deliver`'s vanished-row hold still speaking every tick — a holding path that predates this
   work and that the new holds made conspicuous.

Two of my own tests were wrong in ways the mutation checks caught, which is the argument for running
them: one asserted on `pending` where a delivered message and a lost one leave the same value, and
one read its expected note keys out of the implementation, so *shrinking* that list shrank the
expectation and the test stayed green.

## Development Approach

- **testing approach**: **Regular** (code first, then tests)
- ⚠️ **No task may contain a test that cannot pass until a later task**, and **no test may assert a
  property already free on the branch it exercises.** All four review rounds found these two shapes.
  Task 12 audits for both.
- **CHANGELOG entries go in the commit that makes the change**
- backward compatibility: a relay started with **positional** participants behaves as today, except
  for the two intentional fixes in Tasks 1 and 3, which are named and tested
- **update this plan when scope changes**

## Testing Strategy

- **unit tests** in `tests/test_agb_peer.py`; no e2e in this project
- **structural guards use `ast`**, never a substring grep, and the **peer tree alone**
- **mutation-check every new guard**; ⚠️ **delete `__pycache__/agb-peercpython-36.pyc` after writing
  mutated source** — the cache is keyed on (mtime in whole seconds, size), so a same-size rewrite
  inside one second runs the unmutated file and reads as a pass. Restore from an in-memory snapshot
  verified by `sha256`.
- **companions** for every "nothing happened" test; **non-vacuity** assertions
- **`timeout=` on every `communicate()`**; **Python 3.6.8**; **`PANE_WORDS` untouched**
- commands: `python3 -m pytest tests/test_agb_peer.py -q`; full `python3 -m pytest tests/ -q` (2169)

## Solution Overview

### `needs_prime`, defined by its meaning

> **A name is in `needs_prime` when there may be content on that pane which predates its join, so
> the next successful drain must be discarded rather than delivered.**

Every exit rule follows from that sentence rather than from a list:

| what happened | clear it? | why |
|---|---|---|
| pane read, doorbell found, drain **succeeded** | **yes** | the stale content is gone |
| pane read, **no doorbell** | **yes** | there is nothing stale to discard |
| pane **not** read — `session is None`, text error, **`MODE_MENU`** | **no** | we do not know |
| doorbell found, drain **failed** | **no** | we do not know |
| the bound is exhausted | **yes, without discarding** | see below |

⚠️ **`MODE_MENU` is the case to get right.** A detached row's doorbell is *not visible*, which is not
the same as *absent* — and every `agb-refresh`-re-minted row is detached. Treating it as "nothing to
fetch" is how a backlog gets delivered on the ordinary path.

⚠️ **The bound is not optional.** While a name is pending its drains are discarded, so a participant
stuck through a flaky-ssh spell loses **everything it sends meanwhile**. After N attempts the name
is cleared **without discarding** — we may then deliver one stale backlog, which is strictly better
than silently destroying live messages. Same principle as the no-doorbell rule.

⚠️ **The skip is scoped to the doorbell scan only, never to delivery.** A pending participant must
still *receive*, or a name stuck pending would be unable to be talked to.

⚠️ **Startup is not a join.** The existing `deliver_new=False` priming pass handles the initial
roster; `needs_prime` is only for later joins. This keeps positional and roster mode symmetric and
avoids priming twice on tick 1.

### Refusals: startup vs runtime

| | startup | runtime |
|---|---|---|
| bad parse | **hard refusal** | **hold**, say once per unchanged reason |
| missing / unreadable file | **hard refusal** | **hold**, say once |
| **empty read** | **hard refusal** | ⚠️ **hold** — a truncated read is no information |
| fewer than two participants | **refuse** (`minimum=2`) | **allow** (`minimum=1`), announced once |

⚠️ **Accepted, recorded risk:** a read truncated at a *line boundary* parses cleanly as a shorter
roster and would apply a spurious leave. The empty-read refusal does not close this. The write
window is ~1 ms against an ~8 s tick, and `docs/commands.md` will say to write the file atomically
(`mv` into place). Recorded as a decision, not omitted.

### Ordering inside a tick

```
1. read the roster (byte compare); diff against the last APPLIED spec
2. apply LEAVES         (leaver still in old `resolved`, so cleanup has its target)
3. resolve_all(NEW spec)  (departed names dropped from `previous`)
4. prime anything in `needs_prime`
5. the doorbell scan — SKIPPING names in `needs_prime` — then delivery for EVERYONE
```

⚠️ The read must precede `resolve_all`. Revision 2 put `resolve_all` first, on the *old* spec, so a
joiner was absent from `resolved` on its join tick and every later step silently skipped it.

### Leave, and what a rebind does differently

| cleared | on leave | on rebind |
|---|---|---|
| `seen[name]` | yes | **yes** — pane-specific |
| `("gone"/"menu"/"said", name)` notes | yes | **yes** |
| that name's `delivered` entries | yes | **yes** — pane-specific |
| `resolved[name]` | yes | ⚠️ **yes** — required, or `resolve_all` keeps the old row under the same name |
| `needs_prime` membership | yes | **re-added** — the new pane may hold stale content |
| `pending` messages **to** the name | ⚠️ **yes, named** | ⚠️ **NO** — it moved, it did not leave |

Messages **from** a leaver are always kept — already taken off the sender's pane, and the sender
leaving does not unsay them.

⚠️ **The explicit drop on leave is load-bearing, not duplicate.** It looks like it duplicates
`try_deliver`'s existing "not a participant" drop — but after Task 1 that branch *holds* for spec
members, so without this a leaver's mail would be held for ever.

## Implementation Steps

### Task 1: Membership is the spec, not `resolved` — a pre-existing message-loss bug

Sequenced first because it stands alone: it needs no roster, and it is wrong today.

**Files:** Modify `agb-peer`, `tests/test_agb_peer.py`, `CHANGELOG.md`

- [x] pass the applied **spec** membership to `try_deliver` alongside `resolved`
- [x] **hold** (return False) when the recipient is in the spec but not resolved — it is a
      participant whose row has not appeared yet; **drop** only when it is not in the spec at all
- [x] complain about a held recipient on a **ladder** (the `relay_tick:1300` `gone == 3 or
      gone % 30` shape), not every tick
- [x] **bound the hold**: after N ticks, drop and say so loudly and by name — an unbounded hold grows
      `pending` for ever for an agent that never starts. State the chosen N and that it is a
      judgement, not a measurement.
- [x] write tests: a message to a spec member with no row is **held**, then delivered when the row
      appears; a message to a non-member is dropped and named; the ladder complains a bounded number
      of times across many ticks; the bound eventually drops with its own message
- [x] add the CHANGELOG entry naming the **symptom** — "messages to a participant whose agent had
      not started yet were silently discarded"
- [x] run tests — must pass before Task 2

### Task 2: `drain` reports whether the fetch succeeded

**Files:** Modify `agb-peer`, `tests/test_agb_peer.py`

- [x] add an optional trailing `outcome=None` out-parameter to `drain`, recording **success** or
      **failure** — two outcomes only. ⚠️ "No doorbell" is *not* one of them: `:1330` short-circuits
      before `drain` is reached, so that case belongs to Task 4's function.
- [x] do the same for `drain_files`
- [x] leave both return shapes unchanged — seven existing tests assert on them
- [x] write tests that success and failure are distinguishable, including the case that makes it
      necessary: `drain` returning `[]` for **both** "fetch failed" and "nothing pending"
- [x] write the companion proving a caller passing no `outcome` sees byte-identical behaviour
- [x] run tests — must pass before Task 3

### Task 3: Move the `seen` write after a successful fetch, and throttle what that unleashes

⚠️ **An intentional behaviour change**, isolated in its own task so it is reviewable and revertible.

**Files:** Modify `agb-peer`, `tests/test_agb_peer.py`, `CHANGELOG.md`

- [x] move `seen[name] = ident` (`:1332`) to **after** a successful drain, so a failed fetch is
      retried rather than silently marking the participant caught-up having read nothing
- [x] ⚠️ throttle the complaint paths this makes repeat: `drain`'s `fetch failed` (`:1212`),
      `drain_files`' four `say`s (`:1173`–`:1191`), and `relay_tick:1308`'s `cannot read %s`. Before
      this change each fired once per doorbell; after it they fire every tick until the fetch
      succeeds — the exact shape this file has already fixed twice.
- [x] write the test that a failed fetch is **retried on the next tick** and the messages arrive,
      with the companion that a successful fetch is not re-drained
- [x] write the throttle tests: a persistently failing fetch complains a bounded number of times
      across many ticks; companion — a healthy participant never complains
- [x] add the CHANGELOG entry naming the symptom (messages lost until the next doorbell)
- [x] run tests — must pass before Task 4

### Task 4: Extract the per-participant block, with its full exit vocabulary

**Files:** Modify `agb-peer`, `tests/test_agb_peer.py`

- [x] extract the per-participant body of `relay_tick` into a function callable for **one**
      participant
- [x] ⚠️ **enumerate all five exits** in its returned outcome, keeping them distinct:
      `session is None`, `ctl.text` error, `MODE_MENU`, `no doorbell`, `already caught up` — plus
      Task 2's drain success/failure. **The default is "we did not read the pane".**
- [x] ⚠️ **pin the signature and return shape here**, including who owns `seen[name]` (Task 3 moved
      it) and how `discarded` is returned. `relay_tick` has ~20 positional call sites; any new
      parameter is additive.
- [x] accept a pre-built `sessions` map so `cmd_relay` can build it once per tick from one
      `ctl.tree()` — priming (step 4) runs outside `relay_tick` and must not fetch a second tree
- [x] keep behaviour otherwise identical; existing relay tests pass unchanged
- [x] ⚠️ note in the task that `tests/test_agb_peer.py:1114` passes `set()` as `seen` and survives
      only because `if not ident` short-circuits first — a reordering fails it with a bare
      `AttributeError`, not a readable assertion
- [x] write a test for **each** of the five exits, asserting they are distinguishable — `MODE_MENU`
      in particular must be distinct from `no doorbell`
- [x] run tests — must pass before Task 5

### Task 5: Participant name charset, and the version bump

**Files:** Modify `agb-peer`, `tests/test_agb_peer.py`, `CHANGELOG.md`

- [x] restrict participant names to `[A-Za-z0-9._-]`, non-empty, **left of `=` only** — Plan B's
      roster encoding must survive `ssh … tmux set`, and tightening a refusal later is the breaking
      direction
- [x] bump `agb-peer`'s `VERSION` — ⚠️ the justification is `--roster`, a new relay flag; it is
      **not** the wire, since the room reader and emitter are both cut and nothing here changes what
      crosses between the two copies
- [x] write charset tests for the reachable refusals — `/`, `,`, `@`, `:`, non-ASCII
- [x] ⚠️ note `=` and whitespace as **unreachable** through either front door (`partition("=")`
      takes the first `=`; relay words come from `.split()`), so a reader does not mistake those
      cases for reachable refusals
- [x] write the positive companion: `bob=/home/you/agbridge` still parses
- [x] add the CHANGELOG entry, in this commit
- [x] run tests — must pass before Task 6

### Task 6: `--roster` — reading the file, parsing it, and the startup refusals

**Files:** Modify `agb-peer`, `tests/test_agb_peer.py`, `CHANGELOG.md`

- [x] add a `minimum=2` parameter to `parse_participants`; one production call site, all test call
      sites positional single-argument
- [x] add `read_roster_file(path)` returning bytes, and `parse_roster_text(text, minimum)` — strip
      `#` comments and blank lines, hand the words to `parse_participants` so the grammar cannot
      drift. ⚠️ Task 7 reuses `read_roster_file`; defining it here is what makes these refusals
      testable.
- [x] add `--roster` to `PEER_VALUE_ARGS` and thread it from `main`'s `relay` branch into
      `cmd_relay`; refuse it together with positional participants, somewhere that sees both; say
      whether the other two argv forms silently accept and ignore it (`parse_args` is shared)
- [x] **startup refuses** on: unreadable file, missing file, **empty file**, bad parse, fewer than
      two participants — five distinguishable messages
- [x] update `USAGE`
- [x] write tests: comments and blanks ignored; a line parses identically to the positional form;
      both forms refused; each of the five startup refusals named
- [x] add the CHANGELOG entry, in this commit
- [x] run tests — must pass before Task 7

### Task 7: The runtime reader — byte gate and the holds

**Files:** Modify `agb-peer`, `tests/test_agb_peer.py`, `CHANGELOG.md`

- [x] compare **bytes** to the previous read, re-parsing only on a difference — simpler and cheaper
      than a stat key for a handful of lines, and invariant-6 clean by construction
- [x] ⚠️ keep **two** pieces of state: the last bytes read (the gate) and the last **successfully
      applied** spec (the diff base). Across a hold they diverge, and diffing against a failed parse
      loses the join.
- [x] decode explicitly and treat `UnicodeDecodeError` as a hold — it is a `ValueError`, not an
      `OSError`, so a naive `except IOError` lets it kill the relay
- [x] hold, saying **once per unchanged reason** via `notes`, on: bad parse; missing or unreadable
      file; ⚠️ **an empty read** — a truncated read is no information, never "everybody left"
- [x] use `minimum=1` at runtime, announcing a drop below two once
- [x] write tests: unchanged bytes do not re-parse (count the parses), companion that changed bytes
      do; bad parse holds and says **once across many ticks**; file removed holds; **empty file
      holds**; recovery when restored; a hold followed by a valid edit still sees the join (the
      two-state property)
- [x] mutation-check the gate both ways, deleting `__pycache__/agb-peercpython-36.pyc` each time
- [x] add the CHANGELOG entry, in this commit
- [x] run tests — must pass before Task 8

### Task 8: Apply leaves

**Files:** Modify `agb-peer`, `tests/test_agb_peer.py`

- [x] add `apply_leaves(left, rebound, …)` clearing, per the table above: `seen`, the three `notes`
      keys, that name's `delivered` entries, ⚠️ `resolved[name]`, and ⚠️ `needs_prime` membership
- [x] drop pending messages **to** a true leaver and **name** them — ⚠️ load-bearing after Task 1,
      which makes `try_deliver` *hold* for spec members rather than drop
- [x] ⚠️ for a **rebind**, keep `pending` to that name, and **re-add** it to `needs_prime`
- [x] write tests: leave clears all six things and drops to-messages (named) while keeping
      from-messages
- [x] write the rebind tests: `resolved[name]` dropped, `needs_prime` re-added, and `pending` to that
      name **survives** — the three that distinguish a rebind from a leave
- [x] run tests — must pass before Task 9

### Task 9: Prime joiners

**Files:** Modify `agb-peer`, `tests/test_agb_peer.py`, `CHANGELOG.md`

- [x] add `needs_prime`, a set that **survives ticks**; ⚠️ **startup is not a join** — the existing
      `deliver_new=False` pass handles the initial roster
- [x] `prime_joiners(...)` iterates a **copy** (it removes from the set it walks), calls Task 4's
      function, discards what it finds and **names** it, and clears a name per the meaning table:
      **drain succeeded** or **pane read with no doorbell** → clear; every not-read exit and a
      **failed drain** → keep
- [x] use `resolved.get(name)`, not `resolved[name]` — a name can be in `needs_prime` and absent
      from `resolved`; that must skip and keep, never `KeyError` the relay dead
- [x] ⚠️ **bound the attempts**: after N, clear the name **without discarding** and say so loudly.
      While pending, a participant's drains are discarded, so an unbounded retry destroys every
      message it sends meanwhile — worse than delivering one stale backlog.
- [x] write tests: a **failed drain** keeps the name pending and its backlog is discarded on the
      later tick when the drain succeeds; **`MODE_MENU`** keeps it pending (the ordinary
      `agb-refresh` path); **no doorbell** clears it immediately; an unresolved row keeps it and is
      primed on a later tick with no second file edit; the bound clears without discarding
- [x] add the CHANGELOG entry, in this commit
- [x] run tests — must pass before Task 10

### Task 10: Wire it into `cmd_relay`

**Files:** Modify `agb-peer`, `tests/test_agb_peer.py`, `CHANGELOG.md`

- [x] add an **additive** tick-limit seam — ⚠️ `once` is passed positionally as the 5th argument by
      three tests and by `main:1461`; it must keep its meaning, and the new limit is a keyword after
      `chat_dir`. Note the roster file is mutated from a `sleep` override (`RelayCtl.sleep` exists).
- [x] build `sessions` once per tick from one `ctl.tree()` and pass it to both priming and the scan
- [x] implement the ordering: read → leaves → `resolve_all(new spec)` → prime → scan **skipping
      `needs_prime`** → ⚠️ **delivery for everyone, including pending names**
- [x] ⚠️ throttle `resolve_all`'s unresolvable-entry `say` (add `notes=` — existing calls are
      positional up to `previous`): today a startup typo the operator Ctrl-Cs, with a roster a
      permanent every-8s line, and the `("gone", name)` ladder cannot help because an unresolved name
      never reaches `resolved`
- [x] ⚠️ detect **two names resolving to the same row id** — `resolve` refuses an ambiguous label but
      nothing cross-checks the map, and a roster edit makes this reachable. Two names on one pane
      both drain it, and `try_deliver`'s self-guard is by **name**, so a message can be typed into
      the sender's own composer. Keep the first binding by sorted order, name the one dropped,
      complain throttled.
- [x] fix two wrong-cause messages: `row ids moved -- re-resolved (an agb-refresh re-mints…)`, which
      now also fires on membership change, and `relay_tick:1354`'s "queued before this relay
      **started**", false for a joiner
- [x] decide and implement what a membership change does to the **dashboard** — `:1403` re-opens it
      on every `fresh != resolved`, which now includes every join and leave, and a drop below two
      leaves a stale grid open behind the `len(resolved) > 1` gate
- [x] write the end-to-end multi-tick tests: a participant added mid-run joins and receives; one
      removed stops receiving; a joiner added on tick N is resolved **and primed on tick N**
- [x] write the test whose other half lives in Task 9: a joiner with **no doorbell** has its **first
      real message delivered, not discarded** — this is the scan-skip property, so it belongs here
- [x] write a test that a name stuck in `needs_prime` can still **receive**
- [x] write a test that the loop still works with **positional** participants and never reads a
      roster file
- [x] if a structural guard is added, make it assert **ordering** (the roster-read node precedes the
      `resolve_all` node in `cmd_relay`'s body) — a plain reachability guard proves an edge that
      exists whenever the feature works, so its mutation could never be shown load-bearing
- [x] add the CHANGELOG entry, in this commit
- [x] run tests — must pass before Task 11

### Task 11: Design documentation and the deferred list

**Files:** Modify `docs/design.md`, `docs/commands.md`

- [x] extend §6 with the roster, the startup/runtime refusal split, the tick ordering, and
      **`needs_prime` stated by its meaning** — including why "no doorbell" clears it, why
      `MODE_MENU` does not, and why the retry is bounded
- [x] record the Task 1 fix as a **behaviour change**: messages to a not-yet-started participant are
      now held, not discarded
- [x] record the **named gap**: attaching an agent changes routing only; discovery is Plan B
- [x] record the deferred items with reasons: broadcast (`--to all`) — a reply-all among 3+ agents is
      a feedback loop with no natural stop, which `SKILL.md`'s "send, then finish your turn" does not
      cover; rooms (and why *both* halves were cut: adding a reader later breaks nothing, and rooms
      get implemented in the relay anyway); proactive join/leave announcements; per-participant
      `--chat-dir`
- [x] record the accepted risks: the line-boundary truncation window, and the alias collision being
      *detected* rather than prevented
- [x] record the lingering-option hole: a value failing `parse_option_value` is neither unset nor
      reported
- [x] record **CONFIRMED / ASSUMED**: reachable + no shared disk (Mac ↔ farm) **CONFIRMED**;
      unreachable + shares a mount with its reachable neighbour **CONFIRMED**; unreachable + on a
      *different* mount from the other unreachable agents **NEVER RUN**
- [x] add `--roster` to `docs/commands.md`, ⚠️ **saying the file must be written atomically** (`mv`
      into place), since an in-place rewrite can be read truncated
- [x] run tests — must pass before Task 12

### Task 12: Verify acceptance criteria

- [x] verify every requirement in the Overview is implemented
- [x] verify a relay with **positional** participants is unchanged apart from the two named fixes
- [x] verify the five startup refusals and the four runtime holds are reachable and distinct
- [x] ⚠️ **audit every task's tests for the two shapes all four review rounds caught**: a test that
      cannot pass until a later task, and a test asserting a property already free on its branch
- [x] verify **no complaint path can fire unthrottled every tick** — Tasks 1, 3, 7, 10 each added or
      changed one
- [x] re-run every mutation check (Tasks 7, 10), deleting `__pycache__/agb-peercpython-36.pyc`
- [x] run the full suite: `python3 -m pytest tests/ -q` — 2169 before this plan

### Task 13: [Final] Update documentation

- [x] correct `CLAUDE.md`'s stale test count (2141 → the new number)
- [x] update `CLAUDE.md` if a new invariant emerged
- [x] move this plan to `docs/plans/completed/`

## Post-Completion — the live acceptance walkthrough

⚠️ **Nothing below has been run.** Two of the last four features in this project passed every test
and still needed a fix after live use. This is the gate.

⚠️ **Three commits changed behaviour for people who never touch a roster.** Messages to an
unresolved participant are now *held* rather than dropped, `seen` means *read* rather than
*intended*, and several complaint paths became say-once. **Check 0 is a regression check and it is
the most important item here.**

### Who runs `agb-peer send`, and why it matters for staging

⚠️ **Inside a Claude session, the AGENT runs it — there is no shell prompt to type at.** `agb-peer
send` writes its doorbell onto **`$TMUX_PANE`**, the pane it runs in, and the relay reads the pane
recorded in that row's `agb pane` argv. A split, another window or a second session is a *different
pane*, and a message sent from one is invisible to the relay. That is what
`skills/agb-peer/SKILL.md` is for in normal use.

That would make this walkthrough depend on an LLM running a command verbatim, so it does not. The
way out is a property worth knowing in its own right:

> **A bare shell can SEND but cannot RECEIVE.** `scan_participant` consults `classify` only for
> `MODE_MENU`, so a shell's doorbell is read exactly like an agent's — but `ensure_composer` refuses
> `MODE_UNKNOWN`, so the relay will never type into one.

So: **shells are the senders, agents are the receivers.** The sending path is identical either way.
Check 0 is the exception and uses two real agents end to end, which is the point of it.

### The cast

⚠️ **THE SESSION NAMES MUST NOT BE PREFIXES OF EACH OTHER.** A roster entry is a **substring of the
row title**, so `tx`, `tx-old` and `tx-new` make `tx=tx` match all three and the relay refuses it:
`'tx' matches 3 rows … -- use a longer prefix or the row id`. It refuses cleanly and carries on with
the rest, which is correct — but that participant is simply absent until you fix it. Nor can you
disambiguate with more of the title: `parse_roster_text` splits each line on whitespace, so a label
containing a space becomes two words, and a pane id like `%111` moves when rows are re-minted, which
is the whole reason labels exist. **Rename the session.** The names below are deliberately unrelated.

| session | start it with | role |
|---|---|---|
| `peer-a` | `agb-claude -d peer-a` | receiver, and check 0's sender (agent-driven) |
| `peer-b` | `agb-claude -d peer-b` | receiver |
| `peer-c` | `agb-claude -d peer-c` — **started late, in check 1** | receiver |
| `sender` | `agb-tmux -d sender` | general deterministic sender |
| `stale` | `agb-tmux -d stale` | check 3 — sends *before* joining |
| `rookie` | `agb-tmux -d rookie` | check 4 — joins first, *then* sends |

⚠️ **IF `AGB_CLAUDE_CUSTOM` IS SET, `agb-claude` DOES NOT START A LOCAL AGENT.** It replaces the
whole `claude` command line, so on a site that submits agents to a batch pool the tmux session is
created **here** while the agent process runs **there** — and `$TMUX`/`$TMUX_PANE` are inherited
through the submission to a socket that does not exist on that machine. `agb-peer send` then takes
the **file** transport and says so:

```
agb-peer: tmux is unreachable (error connecting to /tmp/tmux-…/default (No such file or directory))
agb-peer: wrote <statedir>/chat/<id>.msg
[peer #<id>]
agb-peer: queued for bob as #<id> (via file)
```

That is correct behaviour, not a fault — but it is a **different transport**, and testing the roster
over it means a failure has two possible causes. For this walkthrough, force the agents local
(`custom=${AGB_CLAUDE_CUSTOM:-}`, so an empty value disables it):

```sh
env AGB_CLAUDE_CUSTOM= agb-claude -d peer-a
```

⚠️ Running the whole walkthrough **again** with pool agents afterwards is worth doing — a roster over
the file transport is coverage nobody has. It needs `:nfs` on those participants
(`alice=peer-a@<alias>:nfs`) and a relay-wide `--chat-dir <statedir>/chat`.

⚠️ **`-d` mints the row before the command starts, so the row appears immediately — but a row is not
the same as a ready agent.** A fresh Claude in a directory it has not been trusted in sits on a
trust prompt, which classifies as `MODE_UNKNOWN`, and the relay refuses to type into it. Click each
agent row once in agterm and confirm you can see its composer before relying on delivery.
`agb-claude -d peer-a --greet 'say hello'` pushes it past a trust prompt (only valid with `-d`).

### Terminals

| | where | what |
|---|---|---|
| **T1** | Mac | the relay, foreground. **The instrument** — nearly every check is a line that must or must not appear here |
| **T2** | Mac | spare shell for editing `~/peers` |
| **T3** | farm | login shell for `agb-claude` / `agb-tmux`, and for `tmux attach -t sender` to send |
| **T4** | Mac | agterm, for clicking rows and watching messages land |

---

### Part 0 — get the code onto both machines

**Farm (T3):**

```sh
cd ~/agbridge && git fetch && git checkout agb-peer-dynamic-roster
python3 -m pytest tests/ -q          # 2254 expected, ~80 s
agb-peer --version                    # 0.2.0
```

**Mac (T2):**

```sh
cd ~/agbridge && git fetch && git checkout agb-peer-dynamic-roster
./agb-peer --version                  # 0.2.0
```

⚠️ **`install.sh` does not install `agb-peer`** — both sides run it from a checkout, so there are two
copies and **both must be on this branch**. A farm copy on `main` with a Mac copy on the branch is
the one configuration that will waste an afternoon. Check both `--version` outputs before anything
else.

---

### Part 1 — stage and start

**Farm (T3):**

```sh
agb-claude -d peer-a
agb-claude -d peer-b
agb-tmux   -d sender
agb-tmux   -d stale
agb-tmux   -d rookie
```

**Mac (T4):** click `peer-a` and `peer-b` in agterm; confirm each shows a live Claude composer.

⚠️ **FARM PARTICIPANTS NEED `@<ssh alias>`, AND NOTHING WARNS YOU UNTIL A DOORBELL RINGS.** The relay
takes its ssh target from the row's own `agb pane --host`, which is a **hostname** — and unlike
`agb pane`, `agb-peer` does **not** read agbridge's `host_<name>` mapping. On a Mac that cannot
resolve the bare hostname you get, on the first fetch and not before:

```
agb-peer: alice: fetch failed: ssh: Could not resolve hostname tlv02-… : nodename nor servname provided
```

Find the alias on the Mac and put it after an `@`:

```sh
grep -h '^host_' ~/.config/agbridge/*/config ~/.config/agbridge/config 2>/dev/null
```

⚠️ Note **when** it appears: the relay starts cleanly and binds every row, because resolving a row
and reaching its tmux are different questions. Nothing is wrong until somebody sends.

⚠️ And nothing is **lost** either — `seen` is only written on a successful drain, so the doorbell
stays and the message is delivered on the tick after you fix the alias, with no re-send. If you are
staging check 13, this *is* check 13.

**Mac (T2):**

```sh
cd ~/agbridge && ./agb-peer --list       # note the row titles
cp /dev/null ~/peers
cat > ~/peers <<'EOF'
# who is in this chat -- @<alias> because the row's --host is a HOSTNAME
alice=peer-a@<alias>
bob=peer-b@<alias>
tx=sender@<alias>
EOF
cp ~/peers ~/peers.bak                   # checks 8-11 need a copy
```

Use a **substring of the row title**, not a row id — `agb-refresh` re-mints every row and ids change.

**Mac (T1):**

```sh
cd ~/agbridge && ./agb-peer relay --roster ~/peers --interval 2
```

**Expect:**

```
agb-peer: alice        <id>:left
agb-peer: bob          <id>:left
agb-peer: tx           <id>:left
agb-peer: primed on N doorbell(s); relaying every 2s -- Ctrl-C to stop
```

---

⚠️ **EDIT THE ROSTER UNDER THE RUNNING RELAY — DO NOT RESTART IT.** Restarting is the one action
that throws away queued messages: the new relay's priming pass drains every pane and **discards**
what it finds, by design, because otherwise starting a relay would replay an hour-old conversation
into everybody. Every roster change in this walkthrough is meant to be picked up live, within a
tick. If you do restart, assume anything in flight is gone and re-send it.

⚠️ **A leftover doorbell after a restart is normal.** `send` renames the window and the relay only
compares the id, so `primed on 1 doorbell(s)` with **no** `discarded …` line means the doorbell
survived but its message was already consumed — not that something was lost.

### Check 0 — the regression check ⭐ **run this first; if it fails, stop**

Two real agents, end to end. **In agterm (T4)**, click `peer-a` and give Claude this prompt:

```
Run exactly this command and then tell me what it printed:

agb-peer send --to bob --stdin <<'CHAT'
check zero, hello
CHAT
```

| | |
|---|---|
| **T1 must show** | `agb-peer: alice -> bob: delivered` |
| **peer-b must show** | a prompt beginning `[chat from alice] check zero, hello` |
| **FAIL** | nothing arrives, or `dropping a message for 'bob': not a participant` |

If the agent paraphrases the command instead of running it, that is not a product failure — re-prompt.

---

### Check 1 — held, not dropped ⭐ *the pre-existing bug*

**T2:**

```sh
cp ~/peers ~/peers.new && echo 'carol=peer-c' >> ~/peers.new && mv ~/peers.new ~/peers
```

**T1 within a few ticks:** `roster: +carol`, then `carol: no row matches 'peer-c', 3 ticks running…`

**Farm (T3)** — send from the shell, deterministically:

```sh
tmux attach -t sender
# inside that session:
agb-peer send --to carol --stdin <<'CHAT'
waiting for you
CHAT
# then Ctrl-b d to detach
```

| | |
|---|---|
| **T1 must show** | `carol is in the roster but has no row yet -- holding (1/225)` |
| **T1 must NOT show** | `dropping a message for 'carol': not a participant` ← **the old bug** |

**Now start the agent (T3):** `agb-claude -d peer-c`, then click it in agterm so its composer is live.

| | |
|---|---|
| **T1 must show** | `tx -> carol: delivered` |
| **peer-c must show** | `[chat from tx] waiting for you` |
| **FAIL** | it never arrives — it was dropped at send time |

---

### Check 2 — the hold is throttled

Scroll back in **T1** over what you just watched.

| | |
|---|---|
| **Expect** | a ladder — `holding (1/225)`, `(3/225)`, then every 30th; and `no row matches that label` at tick 3, then every 30th |
| **FAIL** | a line every 2 seconds |

---

### Check 3 — a joiner's backlog is discarded ⭐

`stale` is running and is **not** in the roster. Give it a backlog first.

**Farm (T3):**

```sh
tmux attach -t stale
agb-peer send --to alice --stdin <<'CHAT'
this is old news
CHAT
# Ctrl-b d
```

It prints `queued for alice as #<id>`. **T1 says nothing** — correct: nobody is reading that
doorbell yet.

**T2 — admit it:**

```sh
cp ~/peers ~/peers.new && echo 'dave=stale' >> ~/peers.new && mv ~/peers.new ~/peers
```

| | |
|---|---|
| **T1 must show** | `roster: +dave` **and** `discarded 1 message(s) that predate a join: #<id> (dave -> alice)` |
| **peer-a must NOT show** | `this is old news` |
| **FAIL** | alice receives it — the backlog was delivered as new |

Then send from `stale` again and confirm *that* one **does** arrive.

---

### Check 4 — the inverse failure ⭐ *no test in the suite can reach this*

`rookie` has **never sent anything**, so it has no doorbell at all — exactly the case that breaks if
"nothing announced" fails to clear the prime.

**T2 — admit it first:**

```sh
cp ~/peers ~/peers.new && echo 'erin=rookie' >> ~/peers.new && mv ~/peers.new ~/peers
```

**T1 must show** `roster: +erin` and **no** `discarded` line — there was nothing to discard.

**Farm (T3) — now it sends its first ever message:**

```sh
tmux attach -t rookie
agb-peer send --to alice --stdin <<'CHAT'
my first words
CHAT
# Ctrl-b d
```

| | |
|---|---|
| **T1 must show** | `erin -> alice: delivered` |
| **peer-a must show** | `[chat from erin] my first words` |
| **FAIL** | `discarded 1 message(s) that predate a join` — its **first real message** was thrown away |

---

### Check 6 — removal drops the queued mail, by name

**T2** — point carol at a row that does not exist, so her mail queues again:

```sh
cp ~/peers ~/peers.new && sed 's|^carol=.*|carol=no-such-row-zzz|' ~/peers.new > ~/peers.tmp \
  && mv ~/peers.tmp ~/peers
```

**T3:** from `sender`, send to `carol`. **T1** shows the hold.

**T2 — remove her:**

```sh
grep -v '^carol=' ~/peers > ~/peers.tmp && mv ~/peers.tmp ~/peers
```

| | |
|---|---|
| **T1 must show** | `carol left, so 1 queued message(s) will not be delivered: #<id> (tx -> carol)` and `roster: -carol` |
| **FAIL** | silence — the message is held for ever with nothing to release it |

---

### Check 7 — a rebind must stop routing to the old row ⚠️ *the subtlest*

**T2** — repoint `bob` to a row that does not resolve:

```sh
sed 's|^bob=.*|bob=no-such-row-yyy|' ~/peers > ~/peers.tmp && mv ~/peers.tmp ~/peers
```

**T3:** from `sender`, send to `bob`.

| | |
|---|---|
| **T1 must show** | `roster: ~bob`, then `bob is in the roster but has no row yet -- holding` |
| **peer-b must NOT show** | the message |
| **FAIL** | it lands in `peer-b` — `resolve_all` kept the stale binding and typed into the pane bob just left |

Put it back (`bob=peer-b`) and confirm the held message is then delivered.

---

### Checks 8–11 — resilience

After **each** one, send a message to prove the chat still works.

| | **T2 does** | **T1 must show** |
|---|---|---|
| 8 | `echo 'this is not a spec' >> ~/peers` | `participants are name=… -- keeping the N participant(s) already running`, **said once**; chat unaffected |
| 9 | `mv ~/peers /tmp/ && sleep 10 && mv /tmp/peers ~/` | `cannot read the roster … -- keeping …` once, then changes apply again on restore |
| 10 | `: > ~/peers` then `cp ~/peers.bak ~/peers` | `the roster is empty -- keeping …` ⚠️ **the conversation must not dissolve** |
| 11 | cut the roster to one name | `the roster is down to 1 participant(s): nothing can be delivered until another joins`; relay stays up |

---

### Opportunistic

| | Do | Expect |
|---|---|---|
| 5 | Run `agb-refresh` on the Mac — it re-mints every row **detached** — while a participant has an undrained message | `is detached -- attaching it so its doorbell can be seen`, then the message still arrives once armed. ⚠️ This is what the whole `SCAN_DETACHED` distinction exists for |
| 12 | Point two roster names at the same row | `X and Y both resolve to row Z -- ignoring Y …` |
| 13 | Drop the VPN briefly with a message in flight | `fetch failed` **once**, then it arrives when ssh returns. Hard to stage cleanly — skip rather than fake it |

---

### Known limitations — do not report these as bugs

- A read truncated at a **line boundary** is indistinguishable from a real removal. Write the roster
  with `mv` into place, as every step above does.
- An attached agent has **no way to discover the roster**. That is Plan B (`agb-peer who`).
- Two *unreachable* agents on two *different* mounts cannot both be served — `--chat-dir` is
  relay-wide. Never run.
- **A shell participant never receives.** `tx`, `stale` and `rookie` are senders by design; the
  relay refusing to type into them is correct, not a failure.

---

### If something fails

Capture, in this order: the **full T1 scrollback**, `~/peers` as it was at that moment, and
`./agb-peer --list` from the Mac. The relay's output is the whole diagnosis for nearly every failure
mode here, and it is lost when the terminal closes.

