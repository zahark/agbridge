# `agb pane`: a `[d] drawer` key beside `[s] split`

## Overview

`agb pane` is the command an agterm row runs when you click it. It prints the agent's identity and
offers `[enter] attach   [s] shell   [q] quit`. `[s]` opens agterm's **split pane** beside the
agent's and starts an ssh shell on the agent's host, in the agent's directory.

agterm has a third pane — the **scratch drawer** — which overlays the terminal rather than taking
horizontal space from it. This plan adds `[d]`, which puts the same ssh shell there.

The problem it solves: the split halves the width of the pane you are reading. For a quick
`git log` or `ls` you want the shell *over* the agent, not *beside* it. The two are genuinely
different tools — the split's advantage is simultaneity (read Claude's output while you type), the
drawer's is that it costs nothing until you open it — so both keys stay.

Integration is narrow by construction: `agb pane` runs on the Mac, reads no statedir, touches no
agent state, and speaks only to `agtermctl`. Nothing about the wire protocol, the hot path or the
farm side is involved.

## Context (from discovery)

- **Files involved**: `agb_ops` (constants at `:1752-1793`, `open_split` at `:2076`, and the key
  dispatch loop — which is in **`pane_attach`** (`:1998`), *not* `run_pane` (`:2098`): the loop is
  `:2015-2039`, the split branch `:2017-2027`). `split_shell_line` (`:2050`) and `_have` (`:2041`)
  are reused unchanged. Plus `tests/test_identity.py` — see the structural test below.
- ⚠️ **`tests/test_identity.py:1017-1018` pins the exact set of functions containing
  `import subprocess`**, and `open_drawer` will copy `open_split`'s function-local import. That
  test fails until the new name is added to the set. It is an existing test, not a new one, and its
  failure will read like an unrelated regression if it is not expected.
- **Patterns found**: every `agtermctl` interaction is a tuple constant plus a small function that
  runs the calls in a fixed order and treats failure as a message, never an exception. Hand-rolled
  argv parsing, function-local imports, comments that carry the *reason* and not just the rule.
- **Tests**: `tests/test_pane.py:770-804` has four tests for the split — ordering, `on`-not-toggle,
  the failure path, and the prompt string. They are the template.
- **Dependencies identified**: none new. `agtermctl` is already a soft dependency guarded by
  `_have`; its absence is a printed message.

### Verified against a live agterm (2026-07-30)

```
agtermctl session scratch [<mode>] [--command <command>] [--target <target>]
  <mode>      on (show), off (hide), or toggle (default).
              The hidden scratch shell stays alive. (default: toggle)
  --command   When showing, run this command as the scratch's process instead of a
              login shell (run-once; respawns the scratch if one is already open).
  --target    Target session/workspace id, unique prefix, or 'active'. (default: active)
```

`agtermctl session --help` also revealed **`session overlay open|close|resize|result`** —
*"Open, resize, or close an ephemeral overlay terminal on a session"*, where `open` runs a command
and *"it closes when COMMAND exits"*. Rejected for this feature: an interactive shell that is
destroyed when hidden is the opposite of a drawer. Recorded in `docs/agtermctl.md` so the next
reader does not re-evaluate it from scratch.

## Development Approach

- **Testing approach**: regular (code first, then tests), matching this repo's practice — but with
  the project's standing addition: **every new guard is mutation-tested.** Break it, confirm a
  *named* test fails, restore it. A guard whose removal keeps the suite green is not a guard.
- Complete each task fully before moving to the next.
- **Every task includes its tests.** Listed as separate checklist items, not bundled into the
  implementation line.
- **All tests pass before the next task starts.** `python3 -m pytest tests/ -q` (1412 passing at
  the time of writing).
- Update this plan when scope changes; `➕` for discovered tasks, `⚠️` for blockers.

## Testing Strategy

- **Unit tests**: `tests/test_pane.py`, driven by a recording `run` callable — no agterm, no ssh,
  no network. What is asserted is the argv and its order.
- **No e2e tests in this project**: there is no UI harness. The substitute is a live-agterm check
  listed under Post-Completion, because `agtermctl`'s behaviour is the one thing this repo does not
  own.
- **Mutation testing is the acceptance bar for guards**, per `CLAUDE.md`. Substring greps over
  source do not count as structural tests — they pass by matching explanatory comments.

## Progress Tracking

- Mark completed items `[x]` immediately.
- `➕` prefix for newly discovered tasks, `⚠️` for issues or blockers.
- Keep the plan in sync with the work actually done.

## Solution Overview

Two new `agtermctl` call constants and one new function, deliberately duplicated from the split's:

```python
SCRATCH_ON   = ("session", "scratch", "on", "--target", "active")
TYPE_SCRATCH = ("session", "type", "--target", "active", "--pane", "scratch")
```

`open_drawer(line, run, out)` runs them in that order and bails out if the first fails — the same
contract as `open_split`, for the same reason: `--pane scratch` is an error before the scratch
exists, exactly as `--pane right` is before the split does.

**Key design decisions:**

1. **Two keys, not one.** `[s]` keeps the split. The drawer is not an upgrade — it cannot show you
   the agent and the shell at once — so replacing the split would be a loss.
2. **Duplication over abstraction**, chosen explicitly. The two functions differ in two constants
   and one noun, which is exactly the case an abstraction is usually right for. The reason not to:
   they are likely to **diverge**. `scratch` has `--command`, which `split` has no equivalent for,
   so a future drawer may collapse to a single call. A shared parametrised function would make that
   change awkward; two functions make it local.
3. **`on`, never the default `toggle`.** This key can be pressed twice and a toggle would *close*
   the drawer on the second press — the same rule that already governs `[s]`.
4. **`scratch on --command <line>` rejected**, despite being the nicer single call with no
   keystroke injection and no shell-quoting layer. Its help says it *"respawns the scratch if one
   is already open"*, so a second press destroys a shell you were working in. The chosen path nests
   an ssh inside the existing one instead: recoverable with `exit`, and identical to what `[s]`
   already does today. **This goes in a code comment** — `--command` looks strictly better to
   anyone reading the help later, and the reason it was not used is not visible from the call site.
5. **`"shell"` stays an alias for `[s]`.** The label changes from `shell` to `split` because both
   panes now hold shells and the pane is the distinction — but the *word* keeps its old meaning, so
   habitual typists do not silently get a different pane than they got yesterday.

## Technical Details

**Prompt.** `"[enter] attach   [s] split   [d] drawer   [q] quit > "`

**Word sets** (must stay disjoint — see Task 3):

| constant | words |
|---|---|
| `PANE_SPLIT_WORDS` | `s`, `shell`, `split` (unchanged) |
| `PANE_DRAWER_WORDS` | `d`, `drawer`, `scratch` |

**Dispatch** gains one branch in `run_pane`'s loop, identical in shape to the split's: the same
three guards in the same order, then `continue` so the prompt returns.

| # | condition | behaviour |
|---|---|---|
| 1 | `split_line is None` | `"no shell target recorded for this row"` |
| 2 | `not _have(AGTERMCTL)` | `PANE_NO_CTL` — see below |
| 3 | otherwise | print `PANE_DRAWER_HINT`, print the exact line, then `open_drawer`, then `out.flush()` |

⚠️ **`PANE_SPLIT_NO_CTL` must be reworded, not reused as-is.** Its text (`agb_ops:1780-1782`) says
*"agtermctl is not on PATH, so **the split** cannot be opened from here"* — so sharing it would
print a message about the split when `[d]` is pressed. The message really is about agtermctl's
absence rather than either pane, so the fix is to rename it `PANE_NO_CTL` and drop the noun
("…so agterm's panes cannot be opened from here"). This is a shared-text edit, and it is the one
deliberate exception to Task 6's "`[s]` is byte-identical" criterion.

The branch ends `out.flush()` then `continue`, matching the split's (`agb_ops:2026-2027`) — without
the flush the drawer's output sits buffered where the split's does not.

**Hint text.** `PANE_DRAWER_HINT` is its own constant: the shell opens *over* this pane, and hiding
it keeps it alive so `[d]` brings it back. That second sentence is the drawer's actual selling point
and is not obvious from the UI.

**The typed line** is `split_shell_line(...)` unchanged — `ssh -t [-J jump] <target> 'cd <cwd> &&
exec $SHELL -l'`, every word `shlex.quote`d — plus a trailing `"\n"`, or the shell never runs it.

**Inherited wart, kept deliberately**: the failure message prints `" ".join(args[:3])`, which for
the `type` call reads `session type --target`. It is odd, it is what `open_split` does, and making
the two differ costs more than the tidiness is worth.

## What Goes Where

- **Implementation Steps**: code, tests and docs in this repo.
- **Post-Completion**: the live-agterm check, which needs a Mac with agterm running and cannot be
  done from the farm.

## Implementation Steps

### Task 1: Add the scratch call constants and `open_drawer`

**Files:**
- Modify: `agb_ops`
- Modify: `tests/test_pane.py`
- Modify: `tests/test_identity.py`

- [ ] add `SCRATCH_ON` and `TYPE_SCRATCH` beside `SPLIT_ON`/`TYPE_RIGHT` (`agb_ops:1777`), under
      the existing verbatim-`--help` comment block; extend that block with `session scratch`'s
      recorded usage
- [ ] add `PANE_DRAWER_WORDS = ("d", "drawer", "scratch")` beside `PANE_SPLIT_WORDS`
- [ ] add `PANE_DRAWER_HINT` — opens over this pane; hidden, it stays alive; `[d]` brings it back
- [ ] add `open_drawer(line, run=None, out=None)` after `open_split`, same two-call ordering, same
      bail-out on the first failure, `"the drawer was not opened"` in the message
- [ ] comment in `open_drawer` recording why `scratch on --command <line>` was **not** used
      (respawns an open scratch → destroys a shell in use; nesting is recoverable)
- [ ] add `"open_drawer"` to the `import subprocess` holder set at `tests/test_identity.py:1017-1018`
      — an **existing** test that fails until this is done — and extend that test's docstring
      (`:995-1002`), which currently names `open_split` as the second door: it is now a third
- [ ] write tests: the scratch is shown before anything is typed into it (call order)
- [ ] write tests: `on` not `toggle`, `--pane scratch`, and the trailing newline on the line
- [ ] write tests: a failed first call makes exactly **one** call and says so
- [ ] mutation-test each: reverse the order, change `on`→`toggle`, drop the bail-out — each must
      fail a *named* test
- [ ] run `python3 -m pytest tests/ -q` — must pass before Task 2

### Task 2: Wire `[d]` into the prompt and the dispatch loop

**Files:**
- Modify: `agb_ops`

- [ ] change `PANE_PROMPT` to `"[enter] attach   [s] split   [d] drawer   [q] quit > "`
- [ ] rename `PANE_SPLIT_NO_CTL` to `PANE_NO_CTL` and drop "the split" from its text — see the
      warning under Technical Details
- [ ] add the `PANE_DRAWER_WORDS` branch to **`pane_attach`**'s loop (`agb_ops:2017`, the function
      at `:1998` — *not* `run_pane`), mirroring the split branch's three guards, ending in
      `out.flush()` then `continue`
- [ ] update the existing `test_the_prompt_offers_the_shell` to assert both `[s] split` and
      `[d] drawer`
- [ ] write a dispatch test — **and get its mechanism right, or it proves nothing.** Call
      `pane_attach` **directly** with `ctl=<recorder>` (`run_pane` has no `ctl` parameter, so the
      recorder cannot be injected through it), and monkeypatch `ops._have` to return True (`_have`
      scans `$PATH`, and there is no `agtermctl` on the farm, so guard #2 fires and **zero calls
      are recorded**). Answer `"d"` then `"q"`. **Assert the call list is non-empty first**, then
      assert it says `scratch`. Without the non-emptiness assertion, "no call mentions split"
      passes against an empty list — the vacuous-pass failure mode `CLAUDE.md` warns about
- [ ] write a test: `"shell"` still opens the **split** (the compatibility promise, pinned so a
      later tidy-up cannot quietly drop it). Same mechanism and same non-vacuity assertion
- [ ] write a test: the `agtermctl`-missing guard fires for `[d]` — the branch most likely to be
      forgotten in a copy. This one wants `_have` **not** patched, and asserts the message
- [ ] mutation-test: point the `[d]` branch at `open_split` and confirm the dispatch test fails.
      **If it still passes, the test is vacuous — fix the test before continuing**
- [ ] run `python3 -m pytest tests/ -q` — must pass before Task 3

### Task 3: Guard the two word sets against overlap

**Files:**
- Modify: `tests/test_pane.py`

- [ ] write a test asserting all **three** word tuples are pairwise disjoint —
      `PANE_SPLIT_WORDS`, `PANE_DRAWER_WORDS` and `PANE_QUIT_WORDS` (`agb_ops:1754`). Quit is
      matched in the same loop and is checked *after* both others, so it has the identical
      silent-shadowing property
- [ ] document in the test *why*: both new sets contain s-words (`split`, `scratch`, `shell`), the
      dispatch is keyed on string membership, and whichever branch is checked first wins — an
      overlap makes the other silently unreachable, with no error anywhere
- [ ] mutation-test: add `"scratch"` to `PANE_SPLIT_WORDS` and confirm this test fails
- [ ] run `python3 -m pytest tests/ -q` — must pass before Task 4

### Task 4: Record the agtermctl contract

**Files:**
- Modify: `docs/agtermctl.md`

- [ ] add `session scratch`'s **verbatim** `--help` beside the existing `split` and `type`
      recordings (`docs/agtermctl.md:139-175`), tagged **CONFIRMED** with the date
- [ ] record `session overlay` as **known to exist and deliberately not used**, with the reason:
      it is ephemeral and closes when its command exits, so a hidden drawer would be destroyed
- [ ] record the `--command` decision — the one-call spelling that was rejected, and why
- [ ] note that `--pane scratch` requires the scratch to exist first, the same constraint already
      documented for `--pane right`
- [ ] `docs/agtermctl.md:150` says "`[s] shell` can be pressed twice" — relabel to `[s] split` and
      extend the sentence to cover `[d]`, which is governed by the same rule
- [ ] no tests (documentation only); run the suite anyway to confirm nothing regressed

### Task 5: Update the user-facing docs

**Files:**
- Modify: `docs/design.md`
- Modify: `docs/commands.md`
- Modify: `docs/cookbook.md`
- Modify: `README.md`

The prompt string is quoted **verbatim in four places**; after the `shell`→`split` relabel every
one of them is wrong. Grep `\[enter\] attach` and `\[s\] shell` to confirm none are missed.

- [ ] `docs/design.md:623` (the verbatim prompt) and `:630-651` (the `s`/`shell`/`split` section
      and its three load-bearing properties). **This file first**: `CLAUDE.md:54-56` names it the
      authority, reconciled against the implementation — the others follow it
- [ ] `docs/commands.md:146-162` — update the prompt block and the key table with `[d]`, and
      describe the two-call sequence as it is already described for `[s]`
- [ ] `docs/commands.md:143` — `--cwd`'s description says "used by `[s] shell` below"; it is now
      used by both keys
- [ ] `docs/cookbook.md:128` (prompt block) and `:133-134` (the `s` bullet)
- [ ] `README.md:186-189` — "One row, two panes" becomes three; state the split-vs-drawer
      trade-off in one sentence (side by side, versus over the top and free of width)
- [ ] `README.md:242` — the verified-against-live-agterm table row reads `[s] shell → split pane`;
      relabel it, and add a `[d]` row marked **unverified** until the Post-Completion check is done
- [ ] no tests (documentation only); run the suite anyway

### Task 6: Verify acceptance criteria

- [ ] `[s]` behaviour is unchanged: same two calls, same order, same words accepted. The **one**
      deliberate exception is `PANE_SPLIT_NO_CTL` → `PANE_NO_CTL`, whose text no longer names the
      split (Task 2)
- [ ] `[d]` opens the scratch and types the same line `[s]` would
- [ ] both keys return to the prompt, so either can be pressed after the other
- [ ] a row with no host still says "no shell target recorded" for both keys
- [ ] a missing `agtermctl` is a message, not a traceback, for both keys
- [ ] run the full suite: `python3 -m pytest tests/ -q`
- [ ] confirm `agb`'s parse budget is untouched: `wc -c agb` (102,429 against a 102,500 ceiling —
      `tests/conftest.py:63`). This plan edits `agb_ops` only, so the number must not move here;
      Task 7's version bump is length-neutral (`0.2.2` → `0.3.0`) and must not move it either

### Task 7: [Final] Update documentation and close out

- [ ] `CLAUDE.md:99-101`, invariant #12 — it says `agtermctl session split` is used as `on`, never
      `toggle`. The same rule now governs `session scratch`; widen the invariant
- [ ] `CLAUDE.md:187-190`, "**Two doors to `agtermctl`, deliberately**" — `open_drawer` makes three.
      Correct the count and the sentence
- [ ] `CLAUDE.md` — record the duplication decision: the two pane openers are kept separate
      *because they are expected to diverge* (`scratch` has `--command`, `split` has no
      equivalent), not by oversight. Without this a future reader will "fix" it by merging them
- [ ] promote the version in `agb` (`VERSION`, `agb:24`) — a new key is a minor bump: `0.3.0`
- [ ] move this plan to `docs/plans/completed/`

## Post-Completion

*No checkboxes — these need a Mac with agterm running and cannot be done from the farm.*

**Manual verification on the Mac** (this is the real acceptance test — `agtermctl`'s behaviour is
the one thing this repo does not own, and every previous surprise in this project came from
assuming it rather than watching it):

- click a row, press `d`, and confirm a shell opens **over** the pane on the agent's host, in the
  agent's directory
- hide the drawer and bring it back with `d`; confirm the shell is the *same* one, still logged in
  — this is the `"stays alive"` claim in the help, and it is the whole reason `scratch` was chosen
  over `overlay`
- press `d` twice without hiding, and confirm the second press nests an ssh (recoverable with
  `exit`) rather than destroying the first — the behaviour that ruled out `--command`
- press `s` and `d` in both orders on one row; confirm the split and the drawer coexist
- confirm `[enter]` still attaches and detaching still returns to the prompt

**Install after merging**: the fix is in `agb_ops`, which the Mac loads from
`~/.local/lib/agbridge/`, not from the checkout — `sh install.sh mac …` is required. Farm hosts
need nothing: `agb_ops` never runs there.
