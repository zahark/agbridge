# agb-dashboard: watch several agent rows at once

## Overview

agterm can show a **view-only grid** of live sessions — one cell per pane, up to nine. It is the
right way to watch two or more agents work at the same time, and today it is close to unusable
unless you already know what you are doing:

```sh
agtermctl dashboard A1B2C3D4:left E5F6A7B8:left C9D0E1F2:left
agtermctl dashboard --close
```

You have to know the **row ids**, which is the same trap `agb-peer-setup` exists to route around:
`agb-refresh` re-mints every row, so an id you wrote down is dead. You have to know the `:left`
suffix matters. And you have to remember to close it.

This adds **`agb-dashboard`**, a standalone command in the `agb-*` family (`agb-claude`,
`agb-codex`, `agb-tmux`, `agb-peer`, `agb-peer-setup`, `agb-host-line`, `agb-refresh`). It takes
**row selectors** — a label substring, an id, or an id prefix — resolves them fresh, and opens the
grid:

```sh
agb-dashboard alice bob                 # two rows by label
agb-dashboard --roster ~/peers          # everyone in a relay roster
agb-dashboard --mru                     # whatever you were just using
```

It also fixes three defects in the existing `agb-peer relay --dashboard`, which stays — the two are
different features and both are wanted (see *Two features, not one*).

## What agterm actually does — MEASURED 2026-08-27

⚠️ **Captured from the installed binary, not from the published page.** This repo has a hard rule
about that, because the page has twice described behaviour the binary does not have and it cost two
reversed decisions in one day. The evidence table is in `docs/agtermctl.md` → *What `dashboard`
actually does*.

| behaviour | consequence for this plan |
|---|---|
| cells are `<id>`, `<id>:left` or `<id>:right`; **ids or unique prefixes, never names** | a wrapper is genuinely needed; there is no name-taking form to lean on |
| **max 9 cells, and the cap counts PANES** — a bare id takes *every* pane of its session | ⚠️ emit `:left` always; see *Why `:left` is not cosmetic* |
| `--mru` grids the window's most-recently-used sessions with **no ids at all** | worth exposing, as a separate mode |
| one cell alone is valid; there is no minimum of two | no special-casing for a single selector |
| all selectors unresolvable → `error: no dashboard sessions resolved`, **exit 1**, nothing opens | the good failure |
| ⚠️ **some** unresolvable → prints `unresolved: <id>`, **exit 0**, and **opens without them** | 🔴 the dangerous one; see *Fail closed* |
| a malformed suffix (`id:notapane`, `a::b`) → invalid-id error, **exit 1**, before opening | distinct from unresolved, and worth distinguishing in our errors too |
| `:right` on a session with no split is *unresolved*, not an error | do not offer `:right` blindly |
| **`session type` works while a grid is open**, and the text lands | a grid does **not** stop the relay delivering |
| whether the grid takes **physical keyboard** focus in the GUI | **ASSUMED** — untested, nobody has typed at the machine with one open |

Observed live with a three-cell grid of a real three-way conversation:

- three cells **read comfortably** — no pressure to document a practical limit below agterm's nine
- **cell content updates live**, it is not a snapshot

## Two features, not one

`agb-peer relay --dashboard` already exists and **stays**. It is not made redundant, and the reason
is a distinction worth writing down:

> **The relay's grid is an adjunct to a message pump; `agb-dashboard`'s grid is the point.**

That difference decides the error policy, and both policies are correct in their place. A cosmetic
grid failure must **never** stop the relay carrying messages — so `relay --dashboard` is
best-effort by design, and that is not sloppiness to be fixed. But a user who typed
`agb-dashboard alice bob` asked for the grid as the **primary effect**, so it must fail loudly
rather than half-succeed.

They also differ in lifetime. `relay --dashboard` **follows**: it re-resolves every tick and
re-opens whenever ids move, which is what keeps a grid honest across an `agb-refresh`.
`agb-dashboard` is a one-shot open with a foreground hold (see *Lifecycle*).

## The three defects in `relay --dashboard`

Found while designing this, by reading `agb-peer:2560` against the measured behaviour above. All
three are the same class: **a visual surface that looks correct and is not.**

1. 🔴 **It never closes.** There is no `--close` call anywhere in `agb-peer` — measured, zero
   occurrences. When the relay exits, its grid is left open on somebody's screen for ever.
2. 🔴 **`if dashboard and len(resolved) > 1:` skips the repair in the case that needs it most.**
   When membership drops to one resolved participant the whole block is skipped, so the **previous
   grid stays up showing the departed member**. The comment two lines below says the re-open exists
   because "a grid built on dead ids shows dead cells" — and the guard defeats exactly that.
3. ⚠️ **A partially-resolved roster opens a smaller grid with no marker.** Three members, two
   resolvable, and you get a tidy two-cell grid. Nothing on screen says carol is missing.

Fixing these is in scope here because the shared cell-construction lands in the same change.
⚠️ **But the relay must stay best-effort about DELIVERY** — the fix makes its *grid* honest, and
must not introduce any path where a grid problem stops a message.

## Design decisions, and why

### Why `:left` is not cosmetic

⚠️ **A bare id takes every pane of its session, and the 9-cap counts panes** — so a row someone
opened a `[s]` split on silently costs two cells. The same set of rows therefore fits, or does not,
depending on state nobody is looking at. Emitting `:left` unconditionally **converts a cap that
counts panes into a cap that counts agents**, which is what makes the preflight below exact rather
than a guess. Never pass a bare id from this layer.

### Fail closed on explicit membership

If any selector is unresolvable or ambiguous, **open nothing and exit non-zero**, naming each one.

The reason is the measured partial-success row: raw `agtermctl` exits **0** and opens a grid missing
a participant, announcing it only on stdout. Removing that failure is the wrapper's main reason to
exist — a grid you trust that is quietly missing the agent you wanted to watch is worse than no
grid. ⚠️ `--mru` is deliberately **not** held to this: there the user asserted no membership set, so
"whatever resolved" is the request rather than a shortfall.

A `--partial` opt-in is **considered and not built**. It can be added if anyone asks; shipping it
unrequested would re-introduce the exact behaviour being removed, behind a flag nobody knows to
avoid.

### Preflight the 9-cell cap

Count resolved selectors **before** calling agtermctl and refuse with `dashboard supports 9 cells;
got N` if there are too many. Because of `:left`, one selector is exactly one cell, so this
statement is honest rather than approximate.

### Lifecycle

**Foreground hold by default**: open, print what was opened, wait, close on enter or `SIGINT`.
That gives the grid an owner and makes an orphan impossible.

**`--detach`** for fire-and-forget — and it must **print the exact close command**, because a grid
nobody remembers how to dismiss is the failure the hold exists to prevent.

⚠️ **The first cut does NOT follow.** A held grid does not re-resolve, so an `agb-refresh` during a
hold leaves it showing dead cells. That is a real limitation and must be **documented in the tool's
own output**, not just in a doc — `relay --dashboard` is the thing that follows, and the two should
not be confused. `--follow` is the obvious next step and is deliberately out of scope here.

### Why `agb-dashboard`, and not `agb-watch` or `agb-peer watch`

Recorded because it was argued three ways and a future reader will re-open it.

**Not `agb-peer watch`.** Watching rows is not a peer-chat activity. You might grid an agent and the
build it kicked off, or three agents on different hosts, with no relay anywhere. Behind a chat tool
that is undiscoverable, and the name asserts a relationship to messaging that does not exist.
⚠️ The resolver living in `agb-peer` today is an **implementation smell and must not choose the
user-facing noun.**

**Not `agb-watch`**, though it was the strongest counter-proposal and the argument was good: it
names the operator's job rather than agterm's primitive, and it would grow into following without a
rename. It lost on discoverability — agterm calls this a dashboard, `docs/agtermctl.md` calls it a
dashboard, and somebody looking for how to see a grid will not search for *watch*. Naming it after
the thing it opens wins for a command whose whole job is opening that thing.

### The resolver stays in `agb-peer` for now

`sessions_of` and `match_sessions` (`agb-peer:372`, `:393`) are the only label→row resolution in the
tree; `agb_mac.tree_workspaces` returns `{id: workspace}` and no names, so it cannot answer *which
row is called alice*.

⚠️ **Do not extract it as part of this change.** It looks like two small functions and it is not:
the moment this command is useful it also wants roster parsing, pane defaults and `PeerError`-shaped
diagnostics, and moving those touches install paths, loader invariants, module-identity tests and
the byte-sensitive `agb-peer`. `agb-dashboard` loads `agb-peer` by path exactly as `agb-peer-setup`
already does, with an honest comment saying so. Extraction becomes mechanical — and justified —
once two callers have shown where the boundary really is.

## Acceptance criteria

- [ ] `agb-dashboard alice bob` opens a grid from **labels**, with no id typed
- [ ] every cell is explicitly `:left`; no bare id is ever passed to agtermctl
- [ ] an unresolvable or ambiguous selector opens **nothing** and exits non-zero, naming it
- [ ] ten selectors are refused **before** agtermctl is called, naming the cap
- [ ] `--roster <file>` grids a relay roster, using roster names only in diagnostics
- [ ] `--mru` works with no selectors, and is refused **together with** selectors or `--roster`
- [ ] the foreground hold closes the grid on enter; `--detach` prints the close command
- [ ] `relay --dashboard` no longer leaves a stale, orphaned or silently-partial grid
- [ ] nothing in the relay's grid handling can stop a message being delivered

## Implementation Steps

### Task 1: Cell construction and the strict resolve, in `agb-peer`

Shared by both callers, so it lands where the resolver already is.

**Files:**
- Modify: `agb-peer`
- Modify: `tests/test_agb_peer.py`

- [ ] add `dashboard_cells(resolved)` → `["<id>:left", …]` from a name→spec map, **always** with an
      explicit pane and never a bare id; carry the reason in the docstring (a bare id takes every
      pane, the cap counts panes, so a stranger's split changes whether your grid fits)
- [ ] add `DASHBOARD_MAX_CELLS = 9` beside it, with the note that `:left` is what makes one
      selector cost exactly one cell and therefore makes a preflight exact
- [ ] add `resolve_selectors(ctl, selectors)` → `(resolved, problems)` using `sessions_of` and
      `match_sessions`, reporting **unresolved** and **ambiguous** separately, and naming the
      matches for an ambiguous one
- [ ] write tests: `dashboard_cells` emits `:left` for every entry, including one whose spec says
      `left`, and never emits a bare id
- [ ] write tests: an **AST guard** that `dashboard_cells` is the only place a cell string is built,
      modelled on `tests/test_agb_peer.py:1799` — with its non-vacuity assertion
- [ ] write tests: `resolve_selectors` distinguishes unresolved from ambiguous, and an ambiguous
      result names the rows it matched
- [ ] run `python3 -m pytest tests/test_agb_peer.py -q` — must pass before task 2

### Task 2: Fix the three defects in `relay --dashboard`

**Files:**
- Modify: `agb-peer`
- Modify: `tests/test_agb_peer.py`
- Modify: `CHANGELOG.md`

- [ ] add `Ctl.dashboard_close()` calling `dashboard --close`, best-effort like every other call in
      that class — a grid that will not close must not raise into the relay loop
- [ ] close the relay's grid when it exits, so a run cannot orphan one
- [ ] replace `len(resolved) > 1` with a rule that acts on **every** membership change: re-open with
      the full explicit cell set when all members resolve; when any member is unresolved, **close**
      the relay-owned grid rather than leave a complete-looking one up, and say
      `dashboard: waiting for unresolved <name>`
- [ ] ⚠️ keep every one of these best-effort: wrap in the same failure handling the existing call
      uses, so no grid outcome can stop delivery
- [ ] write tests: a membership drop to one resolved participant **closes** the grid (the defect-2
      regression); mutation-check it by restoring the `> 1` guard
- [ ] write tests: a partially-resolved roster does not open a grid, and says which member it is
      waiting for
- [ ] write tests: relay exit closes the grid
- [ ] write tests: a `dashboard` call that fails does not stop a queued message being delivered —
      the invariant that must survive all of the above
- [ ] add the `CHANGELOG.md` entry in this commit
- [ ] run tests — must pass before task 3

### Task 3: `agb-dashboard` skeleton and argument parsing

**Files:**
- Create: `agb-dashboard`
- Create: `tests/test_agb_dashboard.py`
- Modify: `tests/conftest.py`

- [ ] create `agb-dashboard` (`#!/usr/bin/env python3`, executable, own `VERSION`/`--version`)
- [ ] `load_peer()` by path from beside `os.path.realpath(__file__)`, registering in `sys.modules`
      under the shared key, exactly as `agb-peer-setup` does — with the honest comment: *row
      selector resolution lives in `agb-peer` today because message delivery needed it first; this
      imports it rather than cloning a second resolver*
- [ ] hand-rolled parser following the house convention: selectors positional, plus `--roster`,
      `--mru`, `--detach`, `--font-size`, `--version`, `--help`
- [ ] refuse `--mru` together with selectors or `--roster`, naming both — the modes answer different
      questions and silently preferring one would be a guess
- [ ] refuse zero selectors with no `--roster` and no `--mru`
- [ ] `__main__` guard naming `PeerError`, `KeyboardInterrupt`, `OSError`/`IOError` — matched by
      class name, since the sibling may not be loaded when a usage error is raised
- [ ] add a `dashboard` fixture to `tests/conftest.py` beside the existing `peer` one
- [ ] write tests: the module-identity guard (`dash.load_peer() is peer`)
- [ ] write tests: `load_peer` resolves through a **symlink**, the documented install shape
- [ ] write tests: each refusal above, asserting nothing is written and no subprocess runs
- [ ] run `python3 -m pytest tests/test_agb_dashboard.py -q` — must pass before task 4

### Task 4: Open, preflight, and the strict failure

**Files:**
- Modify: `agb-dashboard`
- Modify: `tests/test_agb_dashboard.py`

- [ ] resolve selectors via `resolve_selectors`; on any unresolved or ambiguous selector print each
      with its reason and exit non-zero **without calling agtermctl at all**
- [ ] preflight `DASHBOARD_MAX_CELLS`, refusing with `dashboard supports 9 cells; got N` before any
      call
- [ ] build cells with `dashboard_cells` and open through `Ctl.dashboard`
- [ ] ⚠️ treat a returned `unresolved:` as a **failure** even though agtermctl exits 0 — the whole
      point of the wrapper, and the one place the exit status must not be trusted
- [ ] print the resolved mapping (`name -> id:left`) so what was opened is on the record
- [ ] write tests: one bad selector among three → **no** agtermctl call at all, non-zero exit, the
      bad one named. Mutation-check by making it open the resolvable ones
- [ ] write tests: an ambiguous selector is refused and names the rows it matched
- [ ] write tests: ten selectors → refused before any call, message names the cap
- [ ] write tests: nine selectors → accepted (the boundary, from the other side)
- [ ] write tests: a fake `Ctl` returning exit 0 **with** `unresolved:` in its output is treated as
      a failure — the regression for trusting the status
- [ ] run tests — must pass before task 5

### Task 5: Lifecycle — hold, detach, and `--mru`

**Files:**
- Modify: `agb-dashboard`
- Modify: `tests/test_agb_dashboard.py`
- Modify: `CHANGELOG.md`

- [ ] foreground hold by default: print `press enter to close`, wait, then close
- [ ] ⚠️ close on `KeyboardInterrupt` too, and via `try/finally`, so `Ctrl-C` cannot orphan the grid
      the hold exists to own
- [ ] ⚠️ EOF on stdin closes the grid and exits — `readline` returns `""` at EOF and does not raise,
      and treating that as an ordinary answer is what made `agb-peer-setup` spin 305,869 times in
      six seconds. Same trap, same file family, one line apart
- [ ] `--detach`: open, print the resolved cells **and the literal `agtermctl dashboard --close`
      command**, exit 0 leaving the grid up
- [ ] say, on a held run, that the grid does **not** follow an `agb-refresh` and that
      `agb-peer relay --dashboard` is what follows — in the tool's own output, not only the docs
- [ ] `--mru` mode: call `dashboard --mru`, skipping resolution entirely, and print that membership
      was not asserted
- [ ] write tests: the hold closes on enter, on EOF, and on `KeyboardInterrupt` — three named tests,
      each mutation-checked
- [ ] write tests: an EOF read count stays bounded (the anti-spin guard, as in `test_agb_peer_setup`)
- [ ] write tests: `--detach` leaves the grid open and prints a close command containing
      `dashboard --close`
- [ ] write tests: `--mru` calls agtermctl with `--mru` and resolves nothing
- [ ] add the `CHANGELOG.md` entry in this commit
- [ ] run tests — must pass before task 6

### Task 6: Verify acceptance criteria (automated only)

- [ ] walk the **Acceptance criteria** list, checking every item verifiable without a live agterm
- [ ] confirm `agb` is untouched: `git diff --stat HEAD -- agb` empty
- [ ] confirm no bare id can reach agtermctl: the Task 1 AST guard plus a grep of the new file
- [ ] run the full suite: `python3 -m pytest tests/ -q`

### Task 7: Documentation

**Files:**
- Modify: `docs/commands.md`
- Modify: `docs/cookbook.md`
- Modify: `README.md`
- Modify: `CLAUDE.md`
- Modify: `docs/design.md`

- [ ] `docs/commands.md` — full reference; state the fail-closed rule, the `:left` reason, the
      9-cell cap and that it counts panes, and that a held grid does not follow
- [ ] `docs/cookbook.md` — a "watch two agents talk" recipe next to the relay recipe, and the
      one-line difference between the two features
- [ ] `README.md` — add the row to the `agb-*` family table
- [ ] `CLAUDE.md` — add it to the Architecture enumeration, and record the naming decision, since it
      was argued three ways
- [ ] `docs/design.md` — the *adjunct versus primary effect* distinction, which is the reason two
      commands exist rather than one
- [ ] move this plan to `docs/plans/completed/`

## Post-Completion

**Live verification.** This project's history is that agterm-facing features pass every test and
still need a fix after live use — twice in the last four, and twice again today.

- Grid two agents by **label**, confirm no id was typed and the cells are right.
- ⚠️ Grid a row that has a **split open** (`[s]` from `agb pane`) and confirm it still costs one
  cell — the whole reason for `:left`, and invisible to any test.
- Force ambiguity: two rows whose labels are prefixes of one another (`api` and `api-refactor`), and
  confirm nothing opens.
- Run an `agb-refresh` while a grid is **held** and confirm the documented limitation is what
  actually happens — dead cells, not a crash.
- `--detach`, then close using only the printed command.
- Watch a **Codex** row in a grid while it is visibly working. Its status glyph never leaves
  `completed`, because Codex fires no hooks. ⚠️ **Open question:** whether a frozen glyph beside
  live content misleads. Observed once and nobody was watching for it; if it does mislead, the fix
  is a note in this tool's output, not a change to the row.
