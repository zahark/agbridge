# agb-dashboard: watch several agent rows at once

> **Revision 4**, after a third review (5 critical + 7 important).
>
> ⚠️ **Revision 3 made the same mistake twice in one document**: correction 3 diagnosed a return
> shape changed without converting its caller, fixed it in Task 1, and then Task 2c did the
> identical thing to `_one_name_per_row`. That is recorded rather than quietly fixed, because it
> is the argument for implementing rather than reviewing a fourth time — see *Where this stands*.
>
> Revision 3, after a second review (7 critical + 9 important). Revision 2 repeated the failure
> it was written to fix — three corrections stated a property at the top that the named call could
> not deliver — and introduced two contradictions of its own. Most seriously it would have shipped
> the headline feature **with the bug it exists to remove**. See *Corrections from revision 2*.
>
> **Base commit for the `agb`-untouched check: `98210e5`.**

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

It also fixes **four defects** in the existing `agb-peer relay --dashboard`, the fourth confirmed
by measurement in Task 0. `relay --dashboard` stays. The two
features are different and both are wanted — see *Solution Overview*.

**Benefits**: a grid you can open by name rather than by id; one that refuses to lie about its
membership; and a relay whose grid stops going stale, partial, orphaned — or, for one class of
roster, stops failing to open at all.

## Corrections from revision 2

1. 🔴 **The strict failure would have left the partial grid on screen — the exact thing the command
   exists to remove.** `unresolved:` is printed **after** agterm has already opened the grid with
   the rest (`docs/agtermctl.md`, CONFIRMED). Revision 2's "treat it as a failure" therefore exits
   non-zero with the silently-partial grid still up, and nothing closes it because a strict failure
   has no hold. **Fixed**: Task 4 closes the grid this invocation opened, then exits non-zero.
2. 🔴 **The fourth defect rests on evidence never captured.** The measured table claims
   "`:scratch` is an invalid id" and cites `docs/agtermctl.md`, which has **no `:scratch` row** —
   only `id:notapane`. Two things point the same way (the help says "`:left`/`:right` pane suffix",
   and the error enumerates "use `<id>`, `<id>:left`, or `<id>:right`") but neither is a measurement
   of `:scratch`, and agterm demonstrably *has* a scratch pane (`agb_ops.open_drawer`). If it is in
   fact accepted, revision 2 removes a legitimate cell from every drawer-hosted agent. **Fixed**:
   measured in Task 0 and recorded **before** Task 2 builds on it.
3. 🔴 **Revision 2's correction 1 never reached a checkbox.** Task 1 changes `Ctl.dashboard` to
   `(ok, out, err)`; its only caller is `agb-peer:2565`, `ok, why = ctl.dashboard(...)`, which then
   raises `ValueError: too many values to unpack`. The conversion was listed in **Task 2**, so
   Task 1's own gate could not go green. **Fixed**: converted in Task 1.
4. 🔴 **The alias-versus-unresolved distinction is unimplementable as specified.**
   `_one_name_per_row` is called at the **end** of `resolve_all` (`agb-peer:2466`) and returns only
   the collapsed dict (`:2489`) — it exposes no drop set, so after `resolve_all` an alias-dropped
   name and an unresolved one are *both simply absent*. **Fixed**: a checkbox makes it report what
   it dropped.
5. 🔴 **The new trigger reintroduces the defect the old code existed for.** "Act when the set of
   gridded participants changes" reads as a set of **names** — but an `agb-refresh` changes every
   **id** while the names are identical, which is precisely what `fresh != resolved` (a comparison
   over tuples) was catching, and what the comment at `:2560` describes. **Fixed**: the tracked set
   is the `(name, id, pane)` cell set, with a regression test for id-churn under a stable roster.
6. 🔴 **The `scratch` fix and the close rule fought each other.** A `scratch` participant is never
   gridded, so "close the grid when any member is not gridded" keeps it permanently closed — which
   contradicts Task 2's own test that a `scratch` participant no longer prevents the grid opening.
   **Fixed**: `scratch` counts as **accounted for**, not missing.
7. ⚠️ **"Without calling agtermctl at all" is false**: `resolve_selectors` is fed by one
   `agtermctl tree --json` by this plan's own design, so the refusal path has already called it.
   **Fixed**: it says `agtermctl dashboard`, and the test is written against that call.
8. ⚠️ **Duplicate selectors were recorded as fixed without being decided** — Technical Details said
   dedupe, Task 6's acceptance criterion said refuse. **Fixed**: **dedupe**, once, everywhere. Two
   ways of naming one row is not a user error; spending two of nine cells on it is the bug.
9. ⚠️ **`dashboard_cells` returning only cells cannot report what it excluded**, which two
   checkboxes require. **Fixed**: it returns `(cells, excluded)`.
10. ⚠️ **The relay's exit is `Ctrl-C`** (`agb-peer:2585` prints so) and `cmd_relay` returns from
    three points inside the loop, so "close on exit" needs a `try/finally` no checkbox stated.
    **Fixed**, with a `KeyboardInterrupt` test.
11. ⚠️ **A bare positional selector carries no pane**, so the pane criteria were only reachable
    through `--roster`. **Fixed**: a bare selector defaults to `left`, said once.
12. ⚠️ Also fixed: `<base>` is now a real sha in the header; the bare-id AST guard covers **both**
    files rather than leaving the new one to a human read; Task 2 is split into three; Task 0 is
    scoped as gating **Tasks 2 and 5** rather than everything; the install note (`agb-dashboard` is
    **not** installed by `install.sh`) is in Task 7; `_throttled` dedupes on message text rather
    than time, so the "set changed" rule is what guards the repeated call; drifted line numbers
    corrected (`_throttled` is `:2318`, `resolve_all` `:2439`, `_one_name_per_row` `:2469`,
    `grid_cells` `:2563`, the call `:2565`); and invariant 14's count in `CLAUDE.md` goes from seven
    to eight, fixing a dangling citation `tests/conftest.py:143` already makes.

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
| the help says a cell "may carry a `:left`/`:right` pane suffix", and an invalid suffix errors with "use `<id>`, `<id>:left`, or `<id>:right`" | ⚠️ **suggestive, NOT measured for `:scratch`** — Task 0 measures it, because participant panes are a wider vocabulary and agterm does have a scratch pane |
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

**`dashboard_cells(pairs)` → `(cells, excluded)`, from `(id, pane)` pairs**

⚠️ It returns **two** values, not a list: two checkboxes require it to report what it dropped, and a
list of strings cannot say what is not in it.

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
| ⚠️ `scratch` | **Task 0 decides.** If agterm accepts `:scratch`, it passes through like any other pane and defect 4 does not exist. If it rejects it, the cell is **excluded with a one-shot note** |

⚠️ **The `scratch` branch is conditional on a measurement that has not been made.** Revision 2 stated
it as fact citing an evidence table that has no such row — generalising from `id:notapane`, which is
a different input, while agterm demonstrably *has* a scratch pane (`agb_ops.open_drawer` opens one).
If `:scratch` is in fact accepted, excluding it would remove a legitimate cell from every
drawer-hosted agent's grid. Measure first.

⚠️ **If excluded, a `scratch` participant counts as ACCOUNTED FOR, not as missing.** Otherwise the
close rule below — "close when any member is not gridded" — would keep the grid permanently shut for
any roster containing one, which is the opposite of the fix.

**Grid ownership — one dashboard, two commands**

⚠️ agterm has **one** grid: its own help says `--close` closes *"the open one"*. There is no
ownership token, so a naive implementation has the relay closing the grid `agb-dashboard` is holding
every time it re-resolves.

⚠️ **And a strict failure must close what it opened.** `unresolved:` is printed **after** the grid
is already up, so refusing on it and exiting leaves exactly the partially-populated grid this command
exists to remove — the headline feature shipping with its own bug. The refusal path closes first,
then exits non-zero.

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
⚠️ It **dedupes by `(id, pane)`**, keeping first-seen order — decided once, here, and the **key is
not the id alone**. `X:left` and `X:right` are two legitimate, distinct cells, and a roster may
legitimately hold `alice=<label>` beside `split=<same label>:right` (`agb-peer:1383-1385` supports
exactly that). Deduping by id would silently drop one — the same class of missing cell this command
exists to remove. Two selectors naming one row **and one pane** is not a user error; spending two of
the nine cells on it is the bug. It is **not** a refusal, and no acceptance criterion may say
otherwise.

⚠️ **A bare positional selector carries no pane**, so it defaults to `left`. Panes reach this command
only through `--roster`, where a participant spelled `:right` or `:scratch` already has one.

⚠️ **`resolved` therefore carries `(id, pane)`, not bare ids** — `dashboard_cells` takes pairs, and
revision 3 left nothing bridging a flat list of selector strings to them. `resolve_selectors` returns
the pane alongside each id: `left` for a bare selector, the participant's own for a roster entry.
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

## Where this stands

⚠️ **Three review rounds: 6, then 7, then 5 criticals.** Not converging quickly, and revision 3 made
the *same* mistake twice in one document — correction 3 diagnosed a return shape changed without its
caller, fixed it in Task 1, and Task 2c then did it to `_one_name_per_row`.

That pattern is the argument for **implementing rather than reviewing a fourth time**. The plan is
now ~660 lines describing perhaps 450 of implementation, and the last two rounds' findings are
increasingly about the document's internal consistency rather than about the world. On the previous
feature the same signal appeared at the same point, the call was to implement, and it was right: the
first hour of coding found a missing import no review had, and the live run found two defects that
2462 tests and three rounds had not.

⚠️ **The remaining uncertainty is a measurement, not a document.** Task 0's two questions —
does agterm accept `:scratch`, and can a held grid be typed at — gate Tasks 2b-ii and 5 and are
unanswered. Nothing in Tasks 1, 2a, 2b-i, 3 or 4 depends on them.

## Implementation Steps

### Task 0: Measure the two things later tasks are built on

⚠️ **A gate, not a formality**, and it now gates **Task 2 and Task 5** — not Tasks 1, 3 or 4, which
do not depend on it. Both questions are currently **ASSUMED**, and both decide code rather than prose.

- [x] **Does agterm accept a `:scratch` cell suffix?** Revision 2 asserted it does not, citing an
      evidence table that has no such row — it generalised from `id:notapane`, a different input,
      while agterm demonstrably has a scratch pane (`agb_ops.open_drawer`). If accepted, **defect 4
      does not exist** and Task 2's exclusion must not be built
- [x] **Can a human type at the terminal that opened a held grid?** A different path from
      `session type`, which is already CONFIRMED. If not, Task 5's default becomes `--detach` with
      `--hold` opt-in
- [x] record both in `docs/agtermctl.md`, promoting each from ASSUMED to CONFIRMED with its date
- [x] ⚠️ update the pane rule in *Technical Details* and Task 2's checkboxes to whichever branch the
      first answer selects, **before** starting Task 2

**ANSWERS — measured 2026-08-27, by a human at the machine:**

1. **`:scratch` is REJECTED** — `invalid session id … use <id>, <id>:left, or <id>:right`. ⚠️ And
   **at parse time, not as a resolution check**: byte-identical error whether the row has a scratch
   pane open or not, so the suffix vocabulary is fixed before agterm looks at row state. Stronger
   than the question asked for. **Defect 4 is real; Task 2b-ii is built.** `DASHBOARD_PANES =
   ("left", "right")`, already written in Task 1, is confirmed correct and needs no change — which
   is what isolating the unknown to one constant bought.
2. **A terminal outside agterm stays responsive** while a grid is up: a blocking `read` waited
   normally and returned on Enter, closing the grid. **Foreground hold stays the default**;
   `--detach` remains an option.

   ⚠️ **What was NOT measured, because a first attempt conflated them.** A grid *cell* is read-only
   to the keyboard — true, and irrelevant: nothing types into a cell, the hold is a `read` in the
   launching shell. Whether a shell *inside* an agterm session stays responsive is untested and
   unneeded; outside is the documented route.

### Task 1: A stdout-carrying dashboard call, and cell construction

**Files:**
- Modify: `agb-peer`
- Modify: `tests/test_agb_peer.py`

- [x] ⚠️ change `Ctl.dashboard` to surface **stdout** — `(ok, out, err)` — because `unresolved:` is
      announced only there and the current `rc, _out, err` discards it
- [x] ⚠️ convert its only caller, `agb-peer:2565` (`ok, why = ctl.dashboard(...)`), to the new shape
      **in this task**. Without it that line raises `ValueError: too many values to unpack` and
      Task 1's own gate cannot go green. No behaviour change here — unpacking only
- [x] update `RelayCtl` (`tests/test_agb_peer.py:559`) to the new return shape and add
      `dashboard_close`, so Task 2 does not break the 12 `cmd_relay(` call sites
- [x] add `DASHBOARD_MAX_CELLS = 9` and `dashboard_cells(pairs)` → **`(cells, excluded)`**, taking
      `(id, pane)` pairs, emitting an explicit pane always and a bare id never, preserving `right`,
      and handling `scratch` per whichever branch Task 0 selected
- [x] write tests: `left` and `right` are preserved; a bare id is never emitted; order is preserved;
      `excluded` reports what was dropped
- [x] write tests: `Ctl.dashboard` returns stdout, driven by a fake `run` producing `unresolved: X`
      on stdout with exit 0 — the shape Task 4 depends on
- [x] run `python3 -m pytest tests/test_agb_peer.py -q` — **349 passed**; full suite **2482**
- [x] ➕ mutation-checked four guards (bare id, forced `:left`, swallowed `excluded`, dropped
      stdout): each failed its named test
- [x] ➕ **started without Task 0's answer, safely**: the `scratch` question is now one constant,
      `DASHBOARD_PANES`, and the test asserts only that it is NARROWER than `PANE_KINDS` — not
      which way the measurement went. Task 2b-ii changes that line if `:scratch` turns out to be
      accepted, and nothing else moves

### Task 2a: Close the grid the relay opened (defect 1)

**Files:**
- Modify: `agb-peer`
- Modify: `tests/test_agb_peer.py`

- [x] add `Ctl.dashboard_close()` calling `dashboard --close`, best-effort like every call in that class
      — ➕ already landed in Task 1; unchanged here
- [x] track whether **this run** opened a grid; close only that one, and never a grid this run did
      not open — per the ownership policy
      — ➕ the flag is **latched, never cleared**: a re-open that *fails* does not mean the earlier
      grid went away, so clearing it would strand cells this relay put there
- [x] ⚠️ wrap the relay loop in `try/finally` so the close happens on `Ctrl-C`, which is the relay's
      documented exit (`agb-peer:2596` prints "Ctrl-C to stop") and reaches neither of the **two**
      in-loop `return`s (`:2600`, `:2604`)
- [x] ⚠️ check the 12 existing `cmd_relay(` tests that assert on `ctl.said`: a `finally` now runs on
      every exit, so anything the close says appears in output those tests pin
      — ➕ none needed changing. `cmd_relay`'s `say` writes to `out`, not `ctl.said`, and the only
      test passing `dashboard=True` discards `out`. The close is silent for every other test
      because `opened_grid` is false
- [x] write tests: a relay that opened a grid closes it on `KeyboardInterrupt`; one that never
      opened one closes nothing
      — ➕ plus the `return 0` path (`once=True`) and an open that *failed* (closes nothing)
- [x] write tests: a failing close does not raise into the loop
      — ➕ two shapes, not one: a close that **reports** failure and a close that **raises**. The
      second is what the `finally` needs — an exception there would replace the KeyboardInterrupt
      with a traceback out of the cleanup — plus a `say` that raises, since `out` can be closed by
      then. `close_grid` is a module-level function so that last case is directly testable
- [x] run tests — must pass before task 2b
      — ➕ 356 in `test_agb_peer.py`, 2489 in the full suite. Three mutations checked, each
      naming a distinct failing test: `finally` removed, close made unconditional, close's
      `except` removed
- [x] ➕ **CHANGELOG entry added here, not in 2b-i.** 2b-i's bullet says it carries the entry "for
      the user-visible half of Task 2a and this task", but 2c's bullet says the opposite — "Tasks
      2a, 2b-i and 2b-ii carry their own, per the repo rule that an entry lands in the same commit
      as its code" — and CLAUDE.md's rule is binding, so the entry ships with its code. 2b-i now
      only owes an entry for itself

### Task 2b-i: Route the relay's cells through `dashboard_cells` — UNCONDITIONAL

**Files:**
- Modify: `agb-peer`
- Modify: `tests/test_agb_peer.py`

⚠️ **This half runs whatever Task 0 answered.** Revision 3 made the whole of 2b skippable while
Task 6 named its AST guard as evidence — so on the skip branch an acceptance criterion cited a guard
nobody wrote, and the no-bare-id property reverted to a human read.

- [x] convert `agb-peer:2563`'s cell construction to `dashboard_cells`
- [x] write the AST guard that cell strings are built only in `dashboard_cells`, over `agb-peer`
      **and** `agb-dashboard`, with its non-vacuity assertion
      — ➕ **two** non-vacuity assertions, not one, because there are two ways to cover nothing:
      `checked` (no tree was parsed) and `found` (the pattern matches no literal any more). An
      absent `agb-dashboard` is skipped, so without the second the guard would go green on a file
      whose only cell format had been renamed
      — ➕ the pattern is *placeholders joined by colons* (`%s:%s`, `{}:{}`), NOT "a string with a
      colon in it": `render_roster` legitimately builds `":%s"` for the roster grammar, and a
      broader pattern would have had to allow-list it — an allow-list being the thing that makes a
      guard stop guarding
- [x] ⚠️ **two walks, one tree each — not `functions(peer_tree, dash_tree)`.**
      `tests/conftest.py`'s `functions()` raises on any non-dunder name defined in two trees, and
      both files define `main`. Revision 3's parenthetical "the `conftest` helpers take any tree" was
      false and would have sent an implementer into an assertion about duplicate definitions
      — ➕ a local `_owned_strings` attributes each literal to its **innermost** `def`, so the answer
      is "which function spells this" rather than "does the file contain it"
- [x] add `DASH_PATH` to `tests/conftest.py` beside `PEER_PATH`/`SETUP_PATH`, which the guard needs
      to parse the new file — ➕ commented as *may not exist yet*, with the skip-plus-non-vacuity
      contract a reader of it owes
- [x] write tests: the relay's cells still match what it produced before, for a roster of `left` and
      `right` participants — the no-behaviour-change proof for the routing itself
      — ➕ plus a complement, `test_the_relay_asks_dashboard_cells_for_its_cells`: the absence of a
      stray format proves nothing on its own, since a caller could pass a **bare id** and never
      spell a colon at all
- [x] ➕ **CHANGELOG entry: this task's half only.** 2a already wrote its own (see its last bullet),
      so this bullet's "for the user-visible half of Task 2a and this task" is stale. The entry
      written here is for the one user-visible consequence this commit really has: routing through
      `dashboard_cells` drops a `scratch` participant, so a relay that previously opened **no grid
      at all** (agterm refuses `:scratch` at parse time — Task 0) now opens one without that cell.
      ⚠️ **Task 2b-ii should EXTEND that entry, not add a second** — it is the same symptom, and the
      entry already flags the silent-drop gap 2b-ii closes
- [x] run tests — must pass before task 2b-ii
      — ➕ 359 in `test_agb_peer.py`, 2492 in the full suite
- [x] ➕ **mutation-checked three ways, each naming a distinct failing test.** The routing reverted
      to the inline comprehension (`test_cell_strings_are_spelled_only_in_dashboard_cells` **and**
      `test_the_relay_asks_dashboard_cells_for_its_cells`); the pane forced to `left`
      (`test_the_relays_cells_are_unchanged_by_the_routing`); and the cell built by `+` instead of
      `%` (`…spelled_only_in_dashboard_cells` again, this time on its **non-vacuity** assertion).
      ⚠️ That third one is worth carrying: the pattern matches the `%`/`.format` idiom and NOT a
      concatenation, so a rewrite of the idiom does not silently widen the guard — it fails loudly
      on `found` and asks to be re-taught

### Task 2b-ii: Exclude `scratch` from the grid (defect 4) — CONDITIONAL

**Files:**
- Modify: `agb-peer`
- Modify: `tests/test_agb_peer.py`
- Modify: `CHANGELOG.md`

✅ **Task 0 measured `:scratch` as REJECTED, so this task IS built.** The conditional is resolved:
four defects, and every downstream "three, or four if…" now reads four.

- [x] exclude a `scratch`-paned participant from the cell set, via `dashboard_cells`' `excluded`
      — ➕ **already true when this task started**: 2b-i's routing dropped the cell, so the headline
      symptom (agterm refuses `:scratch` at parse time, the whole call fails, **no grid at all**)
      was already gone. What was left was that the drop was *silent* — `cmd_relay` captured
      `_excluded` and never read it — which is the same "looks correct and is not" class one layer
      down. This task is the report, not the exclusion
- [x] report an excluded participant **once**, not per tick
      — ➕ through the existing `_throttled(say, notes, ("dash-excluded",))`. It dedupes on the
      **message**, so a change in *who* is excluded still gets through while a steady state is
      silent — the clock never enters it
      — ➕ ⚠️ **by NAME, not by row id.** `dashboard_cells` speaks in `(row, pane)`, so `resolved` is
      inverted at the call site; that inversion is only sound because `_one_name_per_row` already
      guarantees the map is 1:1, which is worth knowing before anyone moves this code
- [x] write tests: a `scratch` participant no longer prevents the grid opening
      — ➕ four, not one: the grid opens with the other two cells; the excluded participant is named
      **and** its pane said; a **companion** asserting nothing is said when everyone fits (a
      "nothing happened" test alone passes against a report that can never fire); and once-ness
      across a real re-open, driven by a roster edit through `TickingCtl`, with
      `len(ctl.dashboards) > 1` asserted first so the throttle is provably exercised
- [x] mutation-check by passing the participant pane through unchecked
      — ➕ three mutations, each naming a distinct failing test: `DASHBOARD_PANES` widened to
      `PANE_KINDS` (`…does_not_stop_the_grid_opening`, plus two Task 1 tests); the `if excluded:`
      report deleted (`…excluded_participant_is_named`); and the throttle replaced by a bare `say`
      (`…reported_once_not_per_reopen`)
      — ⚠️ **the second mutation caught a VACUOUS test**, which is the whole reason for running it:
      `…excluded_participant_is_named` first asserted `"carol" in out.getvalue()`, and the relay
      already prints `carol  CCCC3333:scratch` in the participant list it emits on every
      re-resolve — so it was green with the report deleted. It now selects the line containing
      `not shown` and asserts on that. A test about a NEW message must not be satisfiable by an old
      one that happens to contain the same word
- [x] add the `CHANGELOG.md` entry naming this as **a bug users may have hit**
      — ➕ **extended 2b-i's existing entry rather than adding a second**: same symptom, and that
      entry already carried the silent-drop gap as its open caveat. It now shows the line verbatim
      and says why once-ness is part of the fix
- [x] ➕ **stale comment above `DASHBOARD_PANES` rewritten.** It said the scratch question was
      "pending the measurement in the agb-dashboard plan's Task 0"; Task 0 has been measured, so it
      now states the measured fact and its date (2026-08-27) — including that the refusal is at
      **parse time**, which is why exclusion is the only available answer
- [x] run tests — must pass before task 2c
      — ➕ 363 in `test_agb_peer.py`, 2496 in the full suite

### Task 2c: The membership trigger (defects 2 and 3)

**Files:**
- Modify: `agb-peer`
- Modify: `tests/test_agb_peer.py`
- Modify: `CHANGELOG.md`

- [x] ⚠️ have `_one_name_per_row` (`agb-peer:2469`) record its alias drops **in the `notes` dict it
      already receives** — NOT by changing its return shape. `agb-peer:2466` is
      `return _one_name_per_row(...)`, so it **is** `resolve_all`'s return value: changing it would
      change `resolve_all`'s shape across **8 call sites**, four of which index the result as a
      dict. That is the identical error correction 3 diagnosed for `Ctl.dashboard` and this plan
      then repeated one task later
- [x] the `notes` route is nearly free — `_throttled(say, notes, ("alias", name))` at `agb-peer:2477`
      already writes `notes[("said", ("alias", name))]`. ⚠️ **But it has a staleness trap**: the note
      persists after the alias goes away, so the drop set must be rebuilt per tick rather than read
      as an accumulator
- [x] ⚠️ track the **`(name, id, pane)` cell set**, not the set of names. An `agb-refresh` changes
      every id while the names are identical — that is exactly what `fresh != resolved` was
      catching, and a name-set trigger would silently reintroduce the dead-cell defect the code
      already handled
- [x] replace `len(resolved) > 1`: re-open with the cell set on every change, and ⚠️ **when a member
      is missing, OPEN WITH THE REST AND SAY SO** — do not close. Defect 3's symptom is a partial
      grid *"with nothing saying carol is missing"*, so the minimal fix is the marker, not the
      closure. ⚠️ Closing would be a worse regression: `resolve_all` throttles an unresolvable name
      **for ever** (`agb-peer:2458-2465` calls a mistyped label a steady state), so one typo would
      mean **no grid at all for the life of the relay** — and it contradicts this plan's own framing
      of the relay's grid as an adjunct whose failures stay cosmetic
- [x] ⚠️ compute it **outside** `if fresh != resolved:` — a roster edit adding an unresolvable member
      leaves `fresh == resolved`, so a fix inside it never fires — and throttle the message through
      `_throttled` (`agb-peer:2318`). ⚠️ `_throttled` dedupes on message **text**, not time, so it
      guards the message; the "cell set changed" rule is what guards the repeated `--close` call
- [x] ⚠️ keep every grid outcome best-effort, so none of this can stop delivery
- [x] write tests: a drop to one resolved participant, **and to zero**. ⚠️ **[deviation]** only the
      drop to **zero** closes; a drop to **one** re-opens with the single remaining cell. agterm's
      measured table says "one cell alone is valid; no minimum of two", and a `len(cells) > 1`
      special case *is* the `len(resolved) > 1` guard this task exists to remove — with a close
      bolted on. Defect 2's symptom is the departed member's cell still being on screen, and
      re-opening with one cell removes it. The zero case closes because agterm has no empty grid
- [x] write tests: **ids change while names do not → the grid re-opens** (the `agb-refresh` defence,
      the regression this trigger could silently remove)
- [x] write tests: a roster edit adding an unresolvable member **is named, and the grid is left
      alone** (⚠️ **[deviation]** — "closes the grid" here contradicts the checkbox four above it,
      which forbids closing on a missing member and gives the reason; the no-close reading wins.
      The test asserts the whole ordered grid log, so a close before the exit fails it)
- [x] write tests: a name dropped by `_one_name_per_row` is **not** reported as unresolved
- [x] write tests: the "waiting" message is throttled, not per-tick
- [x] write tests: a failing `dashboard` call does not stop a queued message being delivered
- [x] mutation-check the defect-2 fix by restoring the `> 1` guard — ➕ **eight** mutations were
      run, not one, and the sixth (*close the grid when a member is missing*) **survived**: the
      test's `grid_log == [open, close]` could not tell a mid-run close followed by no exit close
      from no mid-run close followed by the exit one. `TickingCtl.sleep` now stamps `("tick", n)`
      into the same log, so the assertion is ordered and the mutation dies. The same shape as this
      repo's "a test asserting that nothing happened needs a companion differing in the variable
      under test" — here the missing variable was *time*
- [x] add the `CHANGELOG.md` entry for **this task's** defects (2 and 3). Tasks 2a, 2b-i and 2b-ii
      carry their own, per the repo rule that an entry lands in the same commit as its code
- [x] run tests — must pass before task 3

### Task 3: `agb-dashboard` skeleton, loading and argument parsing

**Files:**
- Create: `agb-dashboard`
- Create: `tests/test_agb_dashboard.py`
- Modify: `tests/conftest.py`

- [x] create `agb-dashboard` (executable, own `VERSION`/`--version`), loading `agb-peer` by path from
      beside `os.path.realpath(__file__)` and registering it under the shared `sys.modules` key
      — ➕ Tasks 4 and 5 are stubs refusing with **exit 70** (`run_grid`, `run_mru`), the shape
      `agb-peer-setup` used at the same stage. A test asserts an *accepted* argv reaches one of
      them: without it every refusal test in the file would pass against a `main` that refused
      everything
- [x] ⚠️ that key is now a **three-way** cross-file agreement — `agb-peer-setup`, `tests/conftest.py`
      and this file. Name all three in the comment, and add it to CLAUDE.md invariant 14 in Task 7
      — ➕ the test compares against **both** other spellings (`conftest.PEER_MODULE` and
      `setup.PEER_MODULE`), not just one, since a two-way check passes while the third drifts
- [x] hand-rolled parser: positional selectors plus `--roster`, `--mru`, `--detach`
      (⚠️ **no `--font-size`** — revision 1 invented it; there is no evidence agterm's dashboard
      takes one, and inventing a flag is the exact thing this plan's own rule forbids)
      — ➕ its absence is **pinned by a test** (`…does_not_offer_a_font_size`) rather than left to a
      reader, because "we cut it" is not a property anything checks
      — ➕ `--` ends the flags, so a label beginning with a dash is nameable rather than an unknown
      option; `--version`/`--help` are answered **before** the mode rules, so an argv naming no mode
      is still a legal way to ask for the version
- [x] refuse `--mru` together with selectors or `--roster`, naming both; refuse zero selectors with
      neither `--roster` nor `--mru`
      — ➕ **[decision]** `--roster` beside positional selectors is refused too, though no checkbox
      demanded it. A union is defensible — both assert membership and `resolve_selectors` dedupes —
      but nothing specifies how a bare selector's default `left` composes with a roster entry's own
      pane, and refusing is reversible while a silent union is not. Task 4 may relax it
      — ➕ split into `parse_args` (syntax) and `select_mode` (coherence), because folding the mode
      rules into the parser would refuse `--version`
- [x] `__main__` guard naming `PeerError`, `KeyboardInterrupt`, `OSError`/`IOError`, matched by class
      name since the sibling may not be loaded when a usage error is raised
      — ➕ `RosterConflict` is named too: it is a `PeerError` **subclass**, and the match is on the
      exact `type(error).__name__`, so leaving it out lets it escape as a traceback while its parent
      is handled. `AgbError` is named although nothing here loads `agb_ops` **today**, for the same
      cost-nothing reason the family's other script does
      — ➕ `KeyboardInterrupt` gets a clause of its **own** and a second, separate test: it is not an
      `Exception`, so the class-name match cannot see it at all
- [x] add a `dashboard` fixture to `tests/conftest.py` beside the existing `peer` one
      — ➕ `DASH_PATH` was already there from Task 2b-i; its "may not exist yet" comment was rewritten
      rather than deleted, because the skip-plus-non-vacuity contract it states is not about the
      file's existence
- [x] write tests: module identity (`dash.load_peer() is peer`), and resolution through a **symlink**
- [x] write tests: each refusal, asserting nothing is written and no subprocess runs
      — ➕ "no subprocess runs" is an **assertion**, not an inference: the injected `NoCtl` raises on
      any attribute access and `no_read_line` raises on any read, so a refusal that opened the grid
      first and complained afterwards — the exact failure this command exists to remove — fails
      loudly instead of passing on an empty stdout
- [x] run `python3 -m pytest tests/test_agb_dashboard.py -q` — must pass before task 4
      — ➕ **35** in `test_agb_dashboard.py`, **2544** in the full suite
- [x] ➕ mutation-checked **seven** guards, each dying against a named test: `realpath` → `abspath`
      (the symlink test); `AgbError` dropped from the tuple; `except KeyboardInterrupt` → `ValueError`;
      the two-mode refusal disabled; the no-mode refusal disabled; the `sys.modules` key changed; and
      `--roster=` accepted as an empty string

### Task 4: Resolution, preflight, and the strict failure

**Files:**
- Modify: `agb-dashboard`
- Modify: `tests/test_agb_dashboard.py`

- [x] implement `resolve_selectors(sessions, selectors)` **here, not in `agb-peer`** — its only
      caller is this command, and `agb-peer` is re-parsed by every agent on every `send`. It takes a
      single already-fetched tree, classifies on `len(match_sessions(...))`, separates unresolved
      from ambiguous, names an ambiguous selector's matches, and **dedupes by row id**
      — ⚠️ **[deviation]** it dedupes by **`(id, pane)`**, not "by row id". This checkbox's wording
      is stale against the plan's own *Technical Details*, which decided the wider key and says why:
      `X:left` and `X:right` are two legitimate cells and a roster may hold `alice=<label>` beside
      `split=<same label>:right`, so an id-only key silently drops one — the same missing-cell class
      the command exists to remove. Pinned by `…the_dedupe_key_is_id_AND_pane_not_the_id_alone`
      — ➕ it takes an optional third `peer=None` argument (defaulting to `load_peer()`), because the
      two-argument signature the plan names has nowhere to get `match_sessions` from. The signature
      test asserts the first two parameters and that **`ctl` is not among them**, which is the
      property the plan was protecting
      — ➕ a bare **string** item is accepted as `(selector, DEFAULT_PANE)`, so the defaulting rule
      lives with the resolution rather than being re-spelled at each call site
- [x] on any unresolved or ambiguous selector: print each with its reason and exit non-zero
      **without calling `agtermctl dashboard`**. ⚠️ Not "without calling agtermctl at all" — one
      `tree --json` has already run by this design, so the test asserts on the *dashboard* call
      — ➕ **[decision]** *no rows at all* gets its own refusal (exit 2) instead of N identical
      "no row matches" lines: with an empty tree the answer is about agterm, not about what was typed
- [x] preflight `DASHBOARD_MAX_CELLS` after deduping, refusing with a message naming the cap
- [x] build cells with `dashboard_cells`, open through the new `Ctl.dashboard`, print the resolved
      mapping so what was opened is on the record
      — ➕ printed **after** a clean open, not before it: a mapping printed first would describe a
      grid the strict check is about to close
      — ➕ the id/pane line is deliberately not spelled `"%s:%s"`; Task 2b's AST guard covers this
      file too, and `dashboard_cells` stays the only place a cell string is built
- [x] ⚠️ treat a non-empty `excluded` as a **shortfall here**, unlike in the relay: this command's
      whole contract is fail-closed on asserted membership, so a `--roster` naming a `scratch`
      participant must refuse rather than open a grid quietly missing it
- [x] ⚠️ treat `unresolved:` **in stdout** as a failure despite exit 0 — the one place the exit
      status is deliberately not trusted, and the reason Task 1 changed the return shape
- [x] 🔴 **and CLOSE the grid before exiting on it.** `unresolved:` is printed *after* agterm has
      already opened the grid with the rest, so refusing and exiting leaves exactly the
      partially-populated grid this command exists to remove. Close what this invocation opened,
      then exit non-zero
      — ➕ a close that **fails** says so in the message (`AND THE GRID IS STILL UP: …`), with a test:
      the grid is up, nothing else will close it, and a tidy-sounding refusal would hide that
      — ➕ a `dashboard` call that returns **not ok** does *not* close: agterm refuses an invalid id
      and a wholly unresolvable set before opening anything, so closing there would dismiss whatever
      somebody else had up
- [x] `--roster` parses with **`minimum=1`**, not the default 2: this plan's own measured table says
      one cell is valid, and a one-participant roster is a legal thing to want to watch
- [x] write tests: one bad selector among three → **no `agtermctl dashboard` call**, non-zero exit,
      the bad one named; mutation-check by making it open the rest. ⚠️ Not "no agtermctl call" — one
      `tree --json` has already run by this design, and a test asserting otherwise would push an
      implementer back into the per-selector-subprocess shape revision 1 rejected
      — ➕ the test asserts the `tree` call **is** there, so the design is pinned rather than merely
      tolerated
- [x] write tests: an ambiguous selector is refused and names its matches
- [x] write tests: two selectors resolving to the **same row** are deduped, not double-billed
- [x] write tests: ten selectors refused before any call; nine accepted (the boundary, both sides)
- [x] write tests: exit 0 **with** `unresolved:` on stdout is treated as a failure, **and the grid
      is closed** before the non-zero exit — the regression for shipping the bug the command exists
      to fix
      — ➕ plus the companion a "nothing happened" test needs: a clean open with ordinary stdout is
      **not** read as unresolved, or the check could pass while unable to fire at all
- [x] write tests: a one-participant `--roster` is accepted
      — ➕ and one that a roster's `:right` pane is **preserved**, which is the property the pane is
      carried through the resolver for
- [x] run tests — must pass before task 5
      — ➕ **52** in `test_agb_dashboard.py`, **2561** in the full suite
      — ➕ Task 3's `…reaches_a_stub_rather_than_a_refusal` was narrowed to `--mru`: the selector and
      roster argvs now reach a real implementation, and leaving them asserting exit 70 would have
      failed. Its non-vacuity job passes to the `run_grid` tests, which assert the argv handed to
      `agtermctl`
      — ➕ mutation-checked **six** guards, each dying against a named test: the close-before-exit
      dropped; `unresolved_lines` returning `[]`; the dedupe key narrowed to the id; the cap
      preflight removed; the `excluded` shortfall downgraded; and the unresolved-selector refusal
      removed

### Task 5: Lifecycle — hold, detach and `--mru`

✅ **Task 0 measured that a terminal outside agterm stays responsive, so foreground hold IS the
default** and `--detach` stays an option. ⚠️ The tool should say it wants running from outside
agterm — that is the condition the measurement holds under, and the docs already recommend it.

**Files:**
- Modify: `agb-dashboard`
- Modify: `tests/test_agb_dashboard.py`
- Modify: `CHANGELOG.md`

- [x] foreground hold: print `press enter to close`, wait, close via `try/finally`
- [x] ⚠️ close on `KeyboardInterrupt` **and** on EOF — `readline` returns `""` at EOF and does not
      raise, and treating that as an answer is what made `agb-peer-setup` spin 305,869 times in six
      seconds. Same trap, same family of file
      — ➕ **[decision] the hold reads ONCE, with no loop at all**, so the spin class is structurally
      unreachable rather than guarded against: any line closes the grid, so there is nothing to
      re-prompt for. The raw `""` is still tested before stripping, because it means something
      different from a keypress and the hold says so (`stdin ended -- closing the grid`)
      — ➕ `EOFError` is caught too, for an `input()`-shaped injected reader. The real `readline`
      cannot raise it, but a traceback out of the wait would orphan the grid just as a missing
      `finally` would
      — ➕ `KeyboardInterrupt` is **left to propagate** after the close: `__main__` already has a
      clause reporting it as an exit rather than a crash, and catching it here would have made the
      documented way out exit 0 while `Ctrl-C` anywhere else in the file exits 1
- [x] `--detach`: open, print the resolved cells **and the literal close command**, exit 0
- [x] say on a held run that the grid does **not** follow an `agb-refresh`, and that
      `agb-peer relay --dashboard` is what does — in the tool's own output, not only the docs
      — ➕ plus Task 0's condition: the hold says to run it from a terminal **outside** agterm, which
      is the only configuration the responsiveness was measured in
- [x] `--mru`: call `dashboard --mru`, resolve nothing, print that membership was not asserted, and
      ⚠️ do **not** apply the strict `unresolved:` rule — with no membership asserted there is no
      shortfall to detect
      — ➕ it also makes **no `tree --json` call**: with no selector there is no question to ask about
      the row set, and a test asserts the absence
- [x] write tests: the hold closes on enter, on EOF and on `KeyboardInterrupt` — three named tests
      — ➕ four: an `EOFError`-raising reader is the fourth
- [x] write tests: an EOF read count stays bounded (the anti-spin guard)
      — ➕ the `Reader` fake caps itself at 20 reads and raises, so a spin fails **loudly** instead of
      hanging the suite; a test of the fake itself pins that it returns lines WITH their newline and
      `""` when exhausted, the simplification `agb-peer-setup` shipped a spin on
- [x] write tests: `--detach` leaves the grid open and prints a command containing `dashboard --close`
- [x] write tests: `--mru` calls agtermctl with `--mru`, resolves nothing, and does not apply the
      strict check
      — ➕ with the companion that keeps the exemption honest: the same `unresolved:` stdout **does**
      refuse when selectors were named. A test that nothing happened needs one differing only in the
      variable under test
- [x] add the `CHANGELOG.md` entry in this commit
- [x] run tests — must pass before task 6
      — ➕ 73 in `test_agb_dashboard.py`, **2582** in the full suite
      — ➕ **[deviation] Task 4's `grid()` helper now passes `--detach`.** The hold became the
      default, so every Task 4 success case would otherwise block on `read_line`, and its
      "nothing was closed" assertions would have become claims about the hold's own tidy-up rather
      than about the strict `unresolved:` check they were written for
- [x] ➕ mutation-checked four guards, each failing a **named** test: the `finally` removed
      (`test_the_hold_closes_the_grid_on_KeyboardInterrupt`), the raw value stripped before
      the EOF test (`test_the_hold_closes_the_grid_on_enter`), the strict check applied to
      `--mru` (`test_mru_does_NOT_apply_the_strict_unresolved_check`), and `--detach` no
      longer printing the close command

### Task 6: Verify acceptance criteria

- [x] `agb-dashboard alice bob` opens a grid from **labels**, with no id typed
      — `test_a_grid_is_opened_with_explicit_panes_and_reported`: the argv is `["alice", "bob"]`
      and the argv handed to agtermctl is `["AAAA1111:left", "BBBB2222:left"]`
- [x] no bare id can reach agtermctl — the Task 2b AST guard, which covers **both** `agb-peer` and
      `agb-dashboard` (the `conftest` helpers take any tree), not a human read of the new file
      — `test_cell_strings_are_spelled_only_in_dashboard_cells` now parses **two** trees (Task 3
      created the second, so its skip arm no longer fires), plus
      `test_the_relay_asks_dashboard_cells_for_its_cells` for the complement.
      ➕ **The manual half, stated AS manual**: the AST guard proves no *second* place spells a
      cell, and cannot prove a caller does not hand agterm a bare id having spelled no colon at
      all. Read by hand: `agb-dashboard` has exactly two `ctl.dashboard(...)` call sites — `:366`
      with `cells` straight out of `peer.dashboard_cells`, and `:406` with the literal `["--mru"]`,
      which is a **flag**, not an id. `agb-peer` has one, `:2782`, with `grid_cells` from the same
      builder. Nothing else reaches `Ctl.dashboard`.
- [x] `right` is preserved; `scratch` behaves per Task 0's measured answer
      — `test_a_roster_pane_is_PRESERVED_not_forced_to_left` (`alice=alice` + `split=alice:right`
      → `["AAAA1111:left", "AAAA1111:right"]`) and, for the measured `:scratch` rejection,
      `test_a_scratch_participant_is_a_SHORTFALL_here` on this side and
      `test_a_scratch_participant_does_not_stop_the_grid_opening` +
      `test_an_excluded_participant_is_named` on the relay's.
- [x] an unresolvable or ambiguous selector opens nothing and exits non-zero; a **duplicate** is
      **deduped**, not refused — decided once, in *Technical Details*
      — `test_one_bad_selector_among_three_opens_NOTHING`,
      `test_an_ambiguous_selector_is_refused_and_NAMES_ITS_MATCHES`,
      `test_two_selectors_naming_one_cell_are_DEDUPED_not_refused` and
      `test_the_dedupe_key_is_id_AND_pane_not_the_id_alone`.
- [x] a strict failure on `unresolved:` **closes** the grid agterm already opened
      — `test_the_strict_failure_CLOSES_the_grid_before_exiting`, with
      `test_a_close_that_FAILS_is_said_out_loud` for the other half.
- [x] ten selectors refused before **`agtermctl dashboard`** is called (the tree fetch precedes it)
      — `test_ten_selectors_are_refused_before_the_dashboard_call` asserts `ctl.opened() == []`
      while the `tree` call is present; `test_nine_selectors_are_accepted` is its companion, so the
      refusal is not vacuous.
- [x] `--roster` grids a one-participant roster; `--mru` works alone and is refused alongside
      selectors — `test_a_ONE_participant_roster_is_accepted`,
      `test_mru_hands_agterm_the_flag_and_resolves_NOTHING`,
      `test_two_modes_at_once_are_refused_NAMING_BOTH`.
- [x] the hold closes on enter, EOF and `Ctrl-C`; `--detach` prints the close command
      — `test_the_hold_closes_the_grid_on_enter` / `_on_EOF` / `_on_KeyboardInterrupt`, and
      `test_detach_leaves_the_grid_open_and_prints_the_close_command`.
- [x] `relay --dashboard` leaves no stale or orphaned grid, marks a partial one instead of hiding
      it, and no grid outcome stops a message
      — `test_cmd_relay_closes_the_grid_it_opened_on_ctrl_c` / `_on_return`,
      `test_cmd_relay_closes_no_grid_it_did_not_open`, `test_an_unresolvable_participant_is_named_and_the_rest_are_gridded`, `test_a_membership_drop_to_nobody_closes_the_grid`, and
      `test_a_failing_grid_does_not_stop_a_message`. Task 0 found `:scratch` **is** rejected, so
      the conditional clause applies and is covered by
      `test_a_scratch_participant_does_not_stop_the_grid_opening`.
- [x] ⚠️ confirm `agb` is untouched **against the plan's base commit**:
      `git diff --stat 98210e5..HEAD -- agb` → **empty**. `agb`'s character budget is therefore
      untouched by this whole feature; no `AGB_PARSE_BUDGET` change was needed.
- [x] run the full suite: `python3 -m pytest tests/ -q` → **2582 passed**.

**No acceptance criterion failed, and nothing was fixed under this task.** ➕ One criterion was
**narrowed rather than met as written**: "no bare id can reach agtermctl" cannot be settled by an
AST guard alone, because a caller can pass a bare id without spelling a colon anywhere. The guard's
own complement test knows this for `cmd_relay`; for `agb-dashboard` the remaining half is the manual
read recorded above. Said out loud rather than quietly counted as automated.

### Task 7: Update documentation

**Files:**
- Modify: `docs/commands.md`
- Modify: `docs/cookbook.md`
- Modify: `docs/agtermctl.md`
- Modify: `docs/design.md`
- Modify: `README.md`
- Modify: `CLAUDE.md`
- Modify: `tests/conftest.py` (➕ the dangling invariant-14 citation, below)

- [x] `docs/commands.md` — the new command's reference: the fail-closed rule, the pane rule, the
      9-cell cap and that it counts panes, and that a held grid does not follow
      — a new `## agb-dashboard — watch several rows at once, by name` section with four
      subsections, the flag table, the exit codes, the fail-closed table (including the
      `unresolved:`-beside-exit-0 row and why the close comes before the exit), the pane table, and
      the lifecycle. It closes with an explicit **not verified against a live agterm** note.
- [x] ⚠️ `docs/commands.md` — **also update the EXISTING `relay --dashboard` prose** and the
      `agb-refresh` survival table, whose behaviour Task 2 changes
      — the relay section gained a *what it does, tick by tick* table (closes what it opened and
      **only** that; a partial grid is marked, not hidden; a `scratch` participant is excluded and
      said; an excluded participant is **accounted for, not missing**; both messages throttled) and
      the both-at-once-is-unsupported warning. The survival table's single `dashboard` row became
      **two** — the relay's grid now effectively survives a refresh because it re-opens, while
      `agb-dashboard`'s does not — with a paragraph on the trigger being the **cell set**, and on
      the drop-to-zero/drop-to-one asymmetry.
      ➕ **Deviation:** deleted a near-duplicate `agtermctl dashboard <rowA>:left <rowB>:left`
      code block that sat two paragraphs below an identical one. It was already redundant; leaving
      it beside the new pointer to `agb-dashboard` would have made three ways to say one thing.
- [x] `docs/agtermctl.md` — confirmed. Task 0's table (*What `dashboard` actually does — CONFIRMED
      live 2026-08-27*) says exactly what was measured: `:scratch` **rejected**, and rejected at
      **parse time**; a grid **cell** read-only; the **launching terminal outside agterm** stays
      responsive; a shell *inside* agterm marked **ASSUMED — untested**. Nothing added, nothing
      upgraded from a guess.
- [x] `docs/cookbook.md` — a `### Watch them talk` recipe beside the relay recipe, opening with the
      one-line difference in a two-row table (*the grid is an adjunct* vs *the grid is the point*),
      then both invocations, the hold, and the four warnings — refuses rather than half-succeeds,
      does not follow, do not run both, and not yet run live.
- [x] `docs/design.md` — a `### Watching rows, and who owns agterm's one grid` subsection in §6:
      the adjunct-versus-primary-effect distinction as a four-row comparison, why *accounted for,
      not missing* is load-bearing, the one-global-grid ownership policy and why running both is
      documented rather than defended against, the one place the exit status is second-guessed, the
      never-a-bare-id rule as a statement about the **cap**, and where the resolver lives.
- [x] `README.md` — a row in the `agb-*` family table, plus a note under it covering `agb-peer`,
      `agb-peer-setup` and `agb-dashboard` together: **not installed by `install.sh`**, symlink them
      onto `$PATH` from the checkout (a symlink, so `git pull` updates them), `<tool> --version` is
      how you tell the two ends apart. ➕ Also three rows in the verification table — the measured
      `agtermctl dashboard` clauses ✅, and `agb-dashboard` and the `relay --dashboard` fixes ⬜ **not
      yet run** — and the summary sentence above it narrowed so "measured clauses" is not read as
      "we ran our own command".
- [x] `CLAUDE.md` — three paragraphs in the Architecture enumeration (what the tool is and that the
      refusal is the point; the opposite-but-both-right policy against `relay --dashboard`; the
      naming decision, including that **the resolver living in `agb-peer` must not choose the
      user-facing noun**), each ending in a pointer, plus a *not verified live* note. Invariant 14
      is now **Eight** cross-file agreements, with `PEER_MODULE` written out as a three-way one and
      a line added to the closing paragraph naming `tests/test_agb_dashboard.py` as its pin.
      ➕ `tests/conftest.py:143`'s comment said "a CROSS-FILE AGREEMENT with `agb-peer-setup`" —
      the dangling citation the checkbox predicted. Rewritten to name all three spellers.
- [ ] move this plan to `docs/plans/completed/` — left to the orchestrator, by instruction.

## Post-Completion

*No checkboxes — these need a live agterm or a human eye.*

**Manual verification.** This project's history is that agterm-facing features pass every test and
still need a fix after live use — twice in the last four, and twice again this week.

- Grid two agents **by label**, confirming no id was typed and the cells are right.
- ⚠️ Grid a row that has a **split open** and confirm it still costs one cell — the whole reason for
  `:left`, and invisible to any test.
- Force ambiguity with two rows whose labels are prefixes of one another, and confirm nothing opens.
- ⚠️ **Only if Task 0 found `:scratch` is rejected**: run `relay --dashboard` with a `scratch`
  participant and confirm the grid now opens without it, naming the exclusion — defect 4, and the
  case most likely to have bitten somebody already without being recognised. If Task 0 found it is
  accepted, confirm instead that such a participant simply gets a cell.
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
