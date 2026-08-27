# agb-dashboard: watch several agent rows at once

> **Revision 2**, after a plan review (6 critical + 9 important). Every claim below was
> re-verified against the source before being accepted. The review found a **fourth** defect in
> `relay --dashboard` that revision 1 missed, and showed that revision 1's headline safety property
> was **unimplementable** through the call it named. See *Corrections from revision 1*.

## Overview

agterm can show a **view-only grid** of live sessions — one cell per pane, up to nine. It is the
right way to watch two or more agents work at the same time, and today it is close to unusable
unless you already know the internals:

```sh
agtermctl dashboard A1B2C3D4:left E5F6A7B8:left C9D0E1F2:left
agtermctl dashboard --close
```

Three things stand in the way. You must know the **row ids** — the same trap `agb-peer-setup` was
built to route around, because `agb-refresh` re-mints every row and an id you wrote down is dead.
You must know that the `:left` suffix matters. And you must remember to close it.

This plan adds **`agb-dashboard`**, a standalone command in the `agb-*` family, taking **row
selectors** (a label substring, an id, or an id prefix), resolving them fresh and opening the grid:

```sh
agb-dashboard alice bob                 # two rows, by label
agb-dashboard --roster ~/peers          # everyone in a relay roster
agb-dashboard --mru                     # whatever you were just using
```

It also fixes **four defects** in the existing `agb-peer relay --dashboard`, which stays. The two
features are different and both are wanted — see *Solution Overview*.

**Benefits**: a grid you can open by name rather than by id; one that refuses to lie about its
membership; and a relay whose grid stops going stale, partial, orphaned — or, for one class of
roster, stops failing to open at all.

## Corrections from revision 1

1. 🔴 **The headline safety property was unimplementable through the call it named.** Revision 1
   said "open through `Ctl.dashboard`" and, next line, "treat `unresolved:` in the output as a
   failure despite exit 0". `Ctl.dashboard` (`agb-peer:1524-1528`) is
   `rc, _out, err = self.run(...)` — it **discards stdout**, and `unresolved:` is announced *only*
   on stdout. The reason this whole command exists could not have been built as written, and no
   checkbox changed the return shape. **Fixed**: Task 1 changes it and updates the test fake.
2. 🔴 **A fourth defect, worse than the three found.** `PANE_KINDS = ("left", "right", "scratch")`
   (`agb-peer:66`) but agterm's dashboard accepts only `:left`/`:right` and rejects anything else
   with **exit 1 before opening**. The relay builds each cell from that participant's own pane
   (`agb-peer:2562`), so **one `scratch` participant means the grid never opens for the entire
   run** — reported only into the relay's log. **Fixed**: the pane rule is now explicit.
3. 🔴 **"Always `:left`" would have silently re-pointed a `:right` participant's cell**, a
   user-visible relay change arriving as a side effect of a helper's docstring. The property that
   actually matters is **never a BARE id**; the pane itself must be preserved where the dashboard
   can express it. **Fixed**: `dashboard_cells` takes `(id, pane)` pairs.
4. 🔴 **One global grid, two commands closing it.** agterm's help is singular — *"`--close` **the
   open one**"* — and there is no ownership token. Revision 1 gave both the relay and
   `agb-dashboard` unconditional authority to close, so a relay re-resolving would close the grid
   `agb-dashboard` is holding. **Fixed**: an explicit policy in *Technical Details*.
5. 🔴 **Task 2 would have broken 12 existing tests.** `RelayCtl`
   (`tests/test_agb_peer.py:559`) has `dashboard` and no `dashboard_close`, and there are 12
   `cmd_relay(` call sites. **Fixed**: a checkbox for the fake, and the close is gated.
6. 🔴 **Task 1's AST guard could not pass at the end of Task 1.** The only place cell strings are
   built is the relay at `agb-peer:2562`, which Task 2 converts — so the guard would either fail or
   be written loosely enough to be vacuous. **Fixed**: moved to Task 2.
7. 🔴 **The default mode rested on the one clause still marked ASSUMED.** If a grid takes physical
   keyboard focus, "press enter to close" cannot be typed and foreground-hold is unusable on
   arrival. **Fixed**: measuring it is a gate *before* Task 5, not a Post-Completion note.
8. ⚠️ **`--font-size` was invented.** It appeared in one checkbox with no evidence anywhere that
   `agtermctl dashboard` accepts it — in a plan whose own rule is never to design against the page
   without running it. **Fixed**: cut.
9. ⚠️ **`resolve_selectors` was placed in the wrong file and given the wrong signature.** Taking
   `ctl` implies one `tree` call per selector (nine subprocesses, and the row set can move between
   them); and its only caller is `agb-dashboard`, so putting presentation logic in the file every
   agent re-parses on every `send` contradicts this plan's own rationale. **Fixed**: it takes
   `sessions`, and it lives in `agb-dashboard`.
10. ⚠️ **Defect 2 was understated and Task 2's trigger was ambiguous.** The drop-to-**zero** case is
    the same bug. And "every membership change" does not say whether it means the roster (`spec`)
    or the resolution (`resolved`) — a roster edit adding an unresolvable member leaves
    `fresh == resolved`, so a fix inside that `if` never fires, while hoisting it out prints every
    tick. **Fixed**: the trigger and the throttle are both named.
11. ⚠️ **"Any member that does not resolve" is the wrong set.** `_one_name_per_row`
    (`agb-peer:2468`) also drops a name that collides with another on the same row, so the message
    would say "waiting for unresolved bob" about a name that resolved fine. **Fixed**.
12. ⚠️ Also fixed: `--roster` must pass `minimum=1` (the plan's own table says one cell is valid);
    duplicate selectors resolving to one row must be deduped or refused; the `agb`-untouched check
    must compare against the plan's **base commit**, not `HEAD`, or a `CHANGELOG` commit hides it;
    Task 7 must update the *existing* `relay --dashboard` docs, whose behaviour Task 2 changes.

## Context (from discovery)

**Files/components involved:**

- `agb-peer` — owns the only label→row resolution in the tree: `sessions_of` (`:372`),
  `match_sessions` (`:393`, tier order: exact id, id prefix, then name substring, first non-empty
  tier wins), `Ctl.dashboard` (`:1524`), and the relay's grid block (`:2560`).
- `agb-peer-setup` — the precedent for a standalone command that loads `agb-peer` by path.
- `agb_mac` — has `tree_workspaces` (`:1243`), which returns `{row id: workspace}` and **no names**,
  so it cannot answer *which row is called alice*.
- `tests/conftest.py` — holds the shared `peer` fixture and its `sys.modules` key.

**Patterns found:**

- Standalone `agb-*` scripts load siblings by path (`SourceFileLoader`, `realpath`, registered in
  `sys.modules` under a shared key) — established by `agb-peer-setup` this week.
- Hand-rolled argv parsing with `*_FLAGS` / `*_VALUE_ARGS` tables; no `argparse`.
- Every `agtermctl` call in `agb-peer`'s `Ctl` is best-effort: a failure is returned, never raised.

**Dependencies identified:** `agb-dashboard` → `agb-peer` (resolver + shared helpers), by path.
Nothing new is added to `agb`, so its character budget is untouched.

### What agterm actually does — MEASURED 2026-08-27

⚠️ **Captured from the installed binary, not the published page.** This repo has a hard rule about
that: the page has twice described behaviour the binary does not have, and it cost two reversed
decisions in one day. Full evidence table: `docs/agtermctl.md` → *What `dashboard` actually does*.

| behaviour | consequence for this plan |
|---|---|
| cells are `<id>`, `<id>:left` or `<id>:right`; **ids or unique prefixes, never names** | a wrapper is genuinely needed; there is no name-taking form to lean on |
| **max 9 cells, and the cap counts PANES** — a bare id takes *every* pane of its session | ⚠️ emit `:left` always (see *Technical Details*) |
| `--mru` grids the most-recently-used sessions with **no ids at all** | worth exposing, as its own mode |
| one cell alone is valid; no minimum of two | no special case for a single selector |
| all selectors unresolvable → `error: no dashboard sessions resolved`, **exit 1**, nothing opens | the good failure |
| ⚠️ **some** unresolvable → prints `unresolved: <id>`, **exit 0**, and **opens without them** | 🔴 the dangerous one; the reason the wrapper exists |
| malformed suffix (`id:notapane`, `a::b`) → invalid-id error, **exit 1**, before opening | distinct from unresolved; our errors should distinguish them too |
| `:right` on a session with no split is *unresolved*, not an error | do not offer `:right` blindly |
| ⚠️ the only legal suffixes are `:left` and `:right` — **`:scratch` is an invalid id, exit 1** | participant panes are a WIDER vocabulary; see defect 4 |
| **`session type` works while a grid is open**, and the text lands | a grid does **not** stop the relay delivering |
| whether the grid takes **physical keyboard** focus in the GUI | **ASSUMED** — untested |

Observed live with a three-cell grid of a real three-way conversation: three cells **read
comfortably** (no practical limit below agterm's nine), and **cell content updates live** rather
than being a snapshot.

## Development Approach

- **Testing approach**: Regular (code first, then tests) — matches this repo and `agb-peer-setup`.
- Complete each task fully, with passing tests, before the next.
- **CRITICAL: every task MUST include new/updated tests** for the code it adds or changes.
- **CRITICAL: all tests must pass before starting the next task.**
- **CRITICAL: update this plan file if scope changes during implementation.**
- Hand-rolled argv parsing; dependency-injected `run`/`read_line` so every flow is testable without
  a terminal or a real `agtermctl`.
- Python 3.6.8 compatible (no f-strings with `=`, no dataclasses, no walrus).
- ⚠️ **This plan does not touch `agb`.** The check is `git diff --stat HEAD -- agb` being empty.
- Backward compatible: `relay --dashboard` keeps its flag and its best-effort contract.

## Testing Strategy

- **Unit tests** via dependency injection (fake `Ctl`, fake `read_line`); no real subprocess, no
  real agterm, no timing races.
- **Integration-style tests** drive the whole command against a fake `Ctl`, asserting the exact argv
  handed to `agtermctl` and the exit code.
- **No e2e/UI framework** in this project — "e2e" here means the above. Anything needing a real
  agterm is Post-Completion.
- **Non-vacuity**: every AST/structural guard asserts its target was found before asserting what is
  absent; every loop asserts its collection is non-empty first.
- **Structural guards are AST-based, never substring greps** — `tests/test_agb_peer.py:1799` is the
  worked example, including its comment about the first version passing against a docstring.
- **Mutation-check** every new guard, with this repo's mechanics: commit first; restore from an
  in-memory snapshot verified by `sha256`; delete `<repo-root>/__pycache__/agb-peer*.pyc` and
  `agb-dashboard*.pyc` (**repo root, hyphen** — the wrong path silently disarms the check); confirm
  the mutated file's mtime moved; assert the anchor is unique; confirm a **named** test fails.
- ⚠️ **A fake must model reality.** `agb-peer-setup` shipped a spin bug because its fake raised
  `EOFError` where `readline` returns `""`. Any `read_line` fake here returns lines **with** their
  newline and `""` when exhausted.

## Progress Tracking

- Mark completed items `[x]` immediately when done.
- Add newly discovered tasks with ➕.
- Document blockers with ⚠️.
- Keep the plan in sync with the work actually done.

## Solution Overview

### Two features, not one

`agb-peer relay --dashboard` **stays**, and is not made redundant. The distinction is worth writing
down because it decides the error policy:

> **The relay's grid is an adjunct to a message pump; `agb-dashboard`'s grid is the point.**

Both policies are correct in place. A cosmetic grid failure must **never** stop the relay carrying
messages — so `relay --dashboard` is best-effort by design, and that is not sloppiness. But a user
who typed `agb-dashboard alice bob` asked for the grid as the **primary effect**, so it must fail
loudly rather than half-succeed.

They also differ in lifetime. `relay --dashboard` **follows**: it re-resolves every tick and re-opens
when ids move. `agb-dashboard` is a one-shot open with a foreground hold; `--follow` is deliberately
out of scope (see *What Goes Where*).

### The four defects in `relay --dashboard`

Found by reading `agb-peer:2560` against the measured behaviour. The first three are the same class:
**a visual surface that looks correct and is not.** The fourth is worse — a surface that never
appears at all.

1. 🔴 **It never closes.** No `--close` call exists anywhere in `agb-peer` — measured, zero
   occurrences. When the relay exits, its grid is left open for ever.
2. 🔴 **`if dashboard and len(resolved) > 1:` skips the repair in the case that needs it most.**
   When membership drops to one resolved participant — **or to zero**, which is the same bug — the
   block is skipped entirely, so the **previous grid stays up showing the departed member**, while
   the comment two lines below says the re-open exists because "a grid built on dead ids shows dead
   cells".
3. ⚠️ **A partially-resolved roster opens a smaller grid with no marker.** Three members, two
   resolvable, and you get a tidy two-cell grid with nothing saying carol is missing.
4. 🔴 **One `scratch` participant kills the whole grid, for the whole run.** Participant panes are
   `left|right|scratch` (`agb-peer:66`, validated `:1385`) and the relay builds each cell from the
   participant's own pane (`:2562`) — but agterm accepts only `:left`/`:right` and rejects anything
   else as an **invalid id, exit 1, before opening**. So `bob=<label>:scratch` in a roster means
   `--dashboard` silently never produces a grid, with the reason going only into the relay's log.
   ⚠️ This is not hypothetical: `scratch` is a pane `agb pane`'s `[d]` drawer puts an agent in.

### Why `agb-dashboard`, and not `agb-watch` or `agb-peer watch`

Recorded because it was argued three ways and a future reader will re-open it.

**Not `agb-peer watch`** — watching rows is not a peer-chat activity. You might grid an agent and the
build it kicked off, with no relay anywhere. ⚠️ The resolver living in `agb-peer` today is an
**implementation smell and must not choose the user-facing noun.**

**Not `agb-watch`**, though it was the strongest counter-proposal: it names the operator's job rather
than agterm's primitive and would grow into following without a rename. It lost on discoverability —
agterm calls this a dashboard, our own docs call it a dashboard, and nobody hunting for how to see a
grid searches for *watch*.

### The resolver stays in `agb-peer` for now

⚠️ **Do not extract it as part of this change.** It looks like two small functions and is not: the
moment this command is useful it also wants roster parsing, pane defaults and `PeerError`-shaped
diagnostics, and moving those touches install paths, loader invariants, module-identity tests and
the byte-sensitive `agb-peer`. `agb-dashboard` imports by path with an honest comment. Extraction
becomes mechanical — and justified — once two callers show where the boundary really is.

## Technical Details

**`dashboard_cells(pairs)` → `["<id>:<pane>", …]`, from `(id, pane)` pairs**

⚠️ **The property is NEVER A BARE ID — not "always `:left`".** A bare id takes every pane of its
session and the 9-cap counts panes, so a row somebody opened a `[s]` split on silently costs two
cells: the same rows fit, or do not, depending on state nobody is looking at. Always emitting an
explicit pane **converts a cap that counts panes into a cap that counts agents**, which is what makes
the preflight exact rather than a guess.

⚠️ **But the pane must be PRESERVED, not forced.** Revision 1 said "`:left` for every entry", which
would have silently re-pointed a `:right` participant's cell — a user-visible relay change arriving
as a side effect of a helper's docstring.

**The pane rule**, stated once here because two callers depend on it:

| participant pane | cell |
|---|---|
| `left` | `<id>:left` |
| `right` | `<id>:right` |
| ⚠️ `scratch` | **no cell exists** — agterm rejects `:scratch` as an invalid id, exit 1, before opening |

`scratch` is the fourth defect. It is **excluded from the grid with a printed note** rather than
mapped to `left` (which would show the wrong pane) or passed through (which kills the grid). For
`agb-dashboard` a `scratch` selector is a refusal like any other unusable one; for the relay it is a
participant that is simply not gridded, said once.

**Grid ownership — one dashboard, two commands**

⚠️ agterm has **one** grid: its own help says `--close` closes *"the open one"*. There is no
ownership token, so a naive implementation has the relay closing the grid `agb-dashboard` is holding
every time it re-resolves.

**Policy**: the relay closes **only** a grid it opened *this run* — it tracks that it opened one and
clears the flag on close — and `agb-dashboard` closes only what its own hold opened. Neither reaches
for a grid it did not open. This does not make them safe to run simultaneously; it makes each one
honest about its own. ⚠️ **Running both at once is documented as unsupported** rather than defended
against: whoever opens last wins, because agterm gives us nothing finer to work with.

**`resolve_selectors(sessions, selectors)` → `(resolved, problems)`** — in `agb-dashboard`

⚠️ It takes **`sessions`, not `ctl`**: one `agtermctl tree --json` fetched once by the caller. Taking
`ctl` would mean one subprocess per selector, and the row set can move between them.

⚠️ It classifies on `len(match_sessions(...))` rather than calling `resolve()`, which raises
`PeerError` code 2 for **unresolved, ambiguous and no-sessions-at-all alike** — telling them apart
through it would mean string-matching an error message.

Reports **unresolved** and **ambiguous** separately, naming the matches for an ambiguous one.
⚠️ It also **dedupes by row id**: two selectors can resolve to the same row (`alice` and an id
prefix of it), which would spend two cells of the nine on one agent.
Ambiguity is real but narrow: labels collide when two hosts run `agb-tmux <same name>`, or when one
label is a prefix of another (`api` vs `api-refactor`, which the docs already warn about).

**Failure policy.** Any unresolved or ambiguous selector → open **nothing**, exit non-zero, name each
one. ⚠️ `--mru` is deliberately exempt: there the user asserted no membership set, so "whatever
resolved" is the request rather than a shortfall. A `--partial` opt-in is **considered and not
built** — shipping it unrequested re-introduces the exact behaviour being removed, behind a flag
nobody knows to avoid.

**Preflight.** Count resolved selectors before calling agtermctl; refuse with `dashboard supports 9
cells; got N`.

**Exit status is not trusted.** agtermctl exits **0** while printing `unresolved:`, so the wrapper
treats that output as a failure. This is the one place the status must be second-guessed.

**Lifecycle.** Foreground hold by default (open, print, wait, close) via `try/finally` so `Ctrl-C`
cannot orphan the grid the hold exists to own. `--detach` prints the literal close command.

## What Goes Where

- **Implementation Steps** — all code, tests and docs below.
- **Post-Completion** — anything needing a live agterm, plus `--follow`, which is **deferred by
  decision**: v1 holds but does not follow, and says so in its own output.

## Implementation Steps

### Task 0: Measure whether a held grid can be typed at

⚠️ **A gate, not a formality.** Task 5 makes foreground-hold the default, and whether a grid takes
physical keyboard focus is the one clause our docs still mark **ASSUMED**. If it does, "press enter
to close" cannot be typed and the default is unusable on arrival — and choosing a default on an
untested assumption is the mistake this project keeps writing post-mortems about.

- [ ] with a grid open, confirm a human can still type at the terminal that opened it (this is a
      different path from `session type`, which is already CONFIRMED)
- [ ] record the answer in `docs/agtermctl.md`, promoting the clause from ASSUMED to CONFIRMED
- [ ] ⚠️ if typing does NOT reach the terminal: change Task 5 so `--detach` is the default and
      `--hold` is opt-in, and say so here before writing any of it

### Task 1: Cell construction and a stdout-carrying dashboard call, in `agb-peer`

**Files:**
- Modify: `agb-peer`
- Modify: `tests/test_agb_peer.py`

- [ ] ⚠️ change `Ctl.dashboard` to surface **stdout** — `(ok, out, err)` — because `unresolved:` is
      announced only there, and the current `rc, _out, err` discards it. Without this the strict
      check in Task 4 cannot be implemented at all
- [ ] update `RelayCtl` (`tests/test_agb_peer.py:559`) to the new return shape, and add
      `dashboard_close` so Task 2 does not break the 12 `cmd_relay(` call sites
- [ ] add `DASHBOARD_MAX_CELLS = 9` and `dashboard_cells(pairs)` taking `(id, pane)` pairs, emitting
      an explicit pane always and a bare id never, **preserving `right`** and **excluding `scratch`**
      per the pane rule in *Technical Details*
- [ ] write tests: `left` and `right` are preserved; `scratch` is excluded and reported; a bare id is
      never emitted; order is preserved
- [ ] write tests: `Ctl.dashboard` returns stdout, using a fake `run` that produces `unresolved: X`
      on stdout with exit 0 — the shape Task 4 depends on
- [ ] run `python3 -m pytest tests/test_agb_peer.py -q` — must pass before task 2

### Task 2: Fix the four defects in `relay --dashboard`

**Files:**
- Modify: `agb-peer`
- Modify: `tests/test_agb_peer.py`
- Modify: `CHANGELOG.md`

- [ ] add `Ctl.dashboard_close()` calling `dashboard --close`, best-effort like every call in that class
- [ ] convert the relay's cell construction at `agb-peer:2562` to `dashboard_cells`, which is what
      fixes defect 4 — a `scratch` participant is now excluded with a note instead of killing the grid
- [ ] ⚠️ track whether **this run** opened a grid, and close only that one, on exit and when the rule
      below says to — per the ownership policy; never close a grid this run did not open
- [ ] ⚠️ replace `len(resolved) > 1` with a rule whose trigger is named: act when the set of
      **gridded participants** changes, computed after `resolve_all` **and** after
      `_one_name_per_row`, so a name dropped as a same-row alias is not reported as unresolved.
      Re-open with the full cell set when every roster member is gridded; **close** the run's own
      grid and say `dashboard: waiting for <name>` when any is not. ⚠️ Compute it **outside** the
      `if fresh != resolved:` block, because a roster edit adding an unresolvable member leaves
      `fresh == resolved` and a fix inside it never fires — and throttle the message through
      `_throttled` (`agb-peer:2456`), or it prints every tick for the life of the relay
- [ ] ⚠️ keep every grid outcome best-effort, so none of the above can stop delivery
- [ ] write tests: a drop to one resolved participant, **and a drop to zero**, both close the grid
- [ ] write tests: a `scratch` participant no longer prevents the grid opening (defect 4 regression)
- [ ] write tests: a roster edit adding an unresolvable member closes the grid and names it — the
      case that stays hidden if the rule lives inside `fresh != resolved`
- [ ] write tests: the "waiting" message is throttled, not per-tick
- [ ] write tests: a name dropped by `_one_name_per_row` is **not** reported as unresolved
- [ ] write tests: relay exit closes the grid; a relay that never opened one closes nothing
- [ ] write tests: a failing `dashboard` call does not stop a queued message being delivered
- [ ] write the AST guard that cell strings are built only in `dashboard_cells` — **here**, now that
      the relay call site is converted; with its non-vacuity assertion
- [ ] mutation-check the defect-2 fix by restoring the `> 1` guard, and the defect-4 fix by passing
      the participant pane through unchecked
- [ ] add the `CHANGELOG.md` entry in this commit, naming the `scratch` fix as a **bug users may
      have hit** rather than an internal tidy
- [ ] run tests — must pass before task 3

### Task 3: `agb-dashboard` skeleton, loading and argument parsing

**Files:**
- Create: `agb-dashboard`
- Create: `tests/test_agb_dashboard.py`
- Modify: `tests/conftest.py`

- [ ] create `agb-dashboard` (executable, own `VERSION`/`--version`), loading `agb-peer` by path from
      beside `os.path.realpath(__file__)` and registering it under the shared `sys.modules` key
- [ ] ⚠️ that key is now a **three-way** cross-file agreement — `agb-peer-setup`, `tests/conftest.py`
      and this file. Name all three in the comment, and add it to CLAUDE.md invariant 14 in Task 7
- [ ] hand-rolled parser: positional selectors plus `--roster`, `--mru`, `--detach`
      (⚠️ **no `--font-size`** — revision 1 invented it; there is no evidence agterm's dashboard
      takes one, and inventing a flag is the exact thing this plan's own rule forbids)
- [ ] refuse `--mru` together with selectors or `--roster`, naming both; refuse zero selectors with
      neither `--roster` nor `--mru`
- [ ] `__main__` guard naming `PeerError`, `KeyboardInterrupt`, `OSError`/`IOError`, matched by class
      name since the sibling may not be loaded when a usage error is raised
- [ ] add a `dashboard` fixture to `tests/conftest.py` beside the existing `peer` one
- [ ] write tests: module identity (`dash.load_peer() is peer`), and resolution through a **symlink**
- [ ] write tests: each refusal, asserting nothing is written and no subprocess runs
- [ ] run `python3 -m pytest tests/test_agb_dashboard.py -q` — must pass before task 4

### Task 4: Resolution, preflight, and the strict failure

**Files:**
- Modify: `agb-dashboard`
- Modify: `tests/test_agb_dashboard.py`

- [ ] implement `resolve_selectors(sessions, selectors)` **here, not in `agb-peer`** — its only
      caller is this command, and `agb-peer` is re-parsed by every agent on every `send`. It takes a
      single already-fetched tree, classifies on `len(match_sessions(...))`, separates unresolved
      from ambiguous, names an ambiguous selector's matches, and **dedupes by row id**
- [ ] on any unresolved, ambiguous or `scratch`-paned selector: print each with its reason and exit
      non-zero **without calling agtermctl at all**
- [ ] preflight `DASHBOARD_MAX_CELLS` after deduping, refusing with a message naming the cap
- [ ] build cells with `dashboard_cells`, open through the new `Ctl.dashboard`, print the resolved
      mapping so what was opened is on the record
- [ ] ⚠️ treat `unresolved:` **in stdout** as a failure despite exit 0 — the one place the exit
      status is deliberately not trusted, and the reason Task 1 changed the return shape
- [ ] `--roster` parses with **`minimum=1`**, not the default 2: this plan's own measured table says
      one cell is valid, and a one-participant roster is a legal thing to want to watch
- [ ] write tests: one bad selector among three → **no** agtermctl call, non-zero exit, the bad one
      named; mutation-check by making it open the rest
- [ ] write tests: an ambiguous selector is refused and names its matches
- [ ] write tests: two selectors resolving to the **same row** are deduped, not double-billed
- [ ] write tests: ten selectors refused before any call; nine accepted (the boundary, both sides)
- [ ] write tests: exit 0 **with** `unresolved:` on stdout is treated as a failure — the regression
      for trusting the status
- [ ] write tests: a one-participant `--roster` is accepted
- [ ] run tests — must pass before task 5

### Task 5: Lifecycle — hold, detach and `--mru`

⚠️ **Task 0 decides this task's default.** If a held grid cannot be typed at, `--detach` is the
default and `--hold` is opt-in; update the checkboxes before starting.

**Files:**
- Modify: `agb-dashboard`
- Modify: `tests/test_agb_dashboard.py`
- Modify: `CHANGELOG.md`

- [ ] foreground hold: print `press enter to close`, wait, close via `try/finally`
- [ ] ⚠️ close on `KeyboardInterrupt` **and** on EOF — `readline` returns `""` at EOF and does not
      raise, and treating that as an answer is what made `agb-peer-setup` spin 305,869 times in six
      seconds. Same trap, same family of file
- [ ] `--detach`: open, print the resolved cells **and the literal close command**, exit 0
- [ ] say on a held run that the grid does **not** follow an `agb-refresh`, and that
      `agb-peer relay --dashboard` is what does — in the tool's own output, not only the docs
- [ ] `--mru`: call `dashboard --mru`, resolve nothing, print that membership was not asserted, and
      ⚠️ do **not** apply the strict `unresolved:` rule — with no membership asserted there is no
      shortfall to detect
- [ ] write tests: the hold closes on enter, on EOF and on `KeyboardInterrupt` — three named tests
- [ ] write tests: an EOF read count stays bounded (the anti-spin guard)
- [ ] write tests: `--detach` leaves the grid open and prints a command containing `dashboard --close`
- [ ] write tests: `--mru` calls agtermctl with `--mru`, resolves nothing, and does not apply the
      strict check
- [ ] add the `CHANGELOG.md` entry in this commit
- [ ] run tests — must pass before task 6

### Task 6: Verify acceptance criteria

- [ ] `agb-dashboard alice bob` opens a grid from **labels**, with no id typed
- [ ] no bare id can reach agtermctl — the Task 2 AST guard, plus a **manual** read of the new file
      (a grep, stated as manual because the Testing Strategy forbids grep-shaped guards)
- [ ] `right` is preserved, `scratch` is refused with its reason
- [ ] an unresolvable, ambiguous or duplicate selector opens nothing and exits non-zero
- [ ] ten selectors refused before agtermctl is called
- [ ] `--roster` grids a one-participant roster; `--mru` works alone and is refused alongside selectors
- [ ] the hold closes on enter, EOF and `Ctrl-C`; `--detach` prints the close command
- [ ] `relay --dashboard` leaves no stale, orphaned or silently-partial grid, opens for a `scratch`
      roster, and no grid outcome stops a message
- [ ] ⚠️ confirm `agb` is untouched **against the plan's base commit**:
      `git diff --stat <base>..HEAD -- agb` — not `HEAD`, which a later `CHANGELOG` commit would hide
- [ ] run the full suite: `python3 -m pytest tests/ -q`

### Task 7: Update documentation

**Files:**
- Modify: `docs/commands.md`
- Modify: `docs/cookbook.md`
- Modify: `docs/agtermctl.md`
- Modify: `docs/design.md`
- Modify: `README.md`
- Modify: `CLAUDE.md`

- [ ] `docs/commands.md` — the new command's reference: the fail-closed rule, the pane rule, the
      9-cell cap and that it counts panes, and that a held grid does not follow
- [ ] ⚠️ `docs/commands.md` — **also update the EXISTING `relay --dashboard` prose** (`:1553-1580`)
      and the `agb-refresh` survival table (`:1673`), whose behaviour Task 2 changes: it now closes
      on exit and on an incomplete roster
- [ ] `docs/agtermctl.md` — the pane-suffix row (`:scratch` is an invalid id), and Task 0's answer
- [ ] `docs/cookbook.md` — a "watch two agents talk" recipe beside the relay recipe, with the
      one-line difference between the two features
- [ ] `docs/design.md` — the *adjunct versus primary effect* distinction, and the one-global-grid
      ownership policy
- [ ] `README.md` — add the row to the `agb-*` family table
- [ ] `CLAUDE.md` — the Architecture enumeration, the naming decision, and the `sys.modules` key as
      a **three-way** agreement under invariant 14
- [ ] move this plan to `docs/plans/completed/`

## Post-Completion

*No checkboxes — these need a live agterm or a human eye.*

**Manual verification.** This project's history is that agterm-facing features pass every test and
still need a fix after live use — twice in the last four, and twice again this week.

- Grid two agents **by label**, confirming no id was typed and the cells are right.
- ⚠️ Grid a row that has a **split open** and confirm it still costs one cell — the whole reason for
  `:left`, and invisible to any test.
- Force ambiguity with two rows whose labels are prefixes of one another, and confirm nothing opens.
- ⚠️ Run `relay --dashboard` with a **`scratch`** participant in the roster and confirm the grid now
  opens without it, naming the exclusion — defect 4, and the case most likely to have bitten
  somebody already without being recognised.
- Run an `agb-refresh` while a grid is **held**, and confirm the documented limitation is what
  actually happens — dead cells, not a crash.
- `--detach`, then close using only the printed command.
- Watch a **Codex** row in a grid while it is visibly working. Its status glyph never leaves
  `completed`, because Codex fires no hooks. ⚠️ **Open question**: whether a frozen glyph beside live
  content misleads. Observed once with nobody watching for it; if it does, the fix is a note in this
  tool's output, not a change to the row.

**Deferred by decision, not forgotten:**

- **`--follow`** — a held grid that re-resolves and re-opens when ids move. `relay --dashboard`
  already proves the mechanism; v1 documents the gap instead of closing it. ⚠️ Note this is the one
  place the two commands genuinely differ in capability, so the docs must not imply otherwise.
- **`--partial`** — an opt-in to the behaviour this tool exists to remove. Add only if asked for.
- **Extracting the resolver** out of `agb-peer` into something both callers share honestly, once
  these two callers have shown where the boundary is.
