# `_spawn`'s timeout kills the child, not the process group

**Found live**, 2026-08-28, by a peer agent stress-testing `agb-dashboard` against a deliberately
wedged `agtermctl`. **Not reachable today** — recorded because the failure is silent and the day it
becomes reachable is a day nobody would think to look here.

> ✅ **FIXED 2026-08-28 — fix 1 of the two below.** `_spawn` now reaps with
> `communicate(timeout=REAP_TIMEOUT)` (5 s) inside its own `try`, and `poll()` rather than `wait()`
> on the way out — `wait()` would reintroduce the block it avoids. Worst case is `timeout + 5 s` and
> a `TIMED_OUT` answer instead of no answer ever. **Fix 2 was declined, and the reason below is
> incomplete**: see *Why fix 2 was not taken* at the end.
>
> The rest of this entry is kept because the measurement and the choice between the two fixes are
> still the live guidance.
>
> 🔴 **AND IT WAS THREE COPIES, NOT ONE.** Searching for the shape rather than waiting for the next
> report found the identical `kill()` + untimed `communicate()` in **`agb_mac._run_command`** and
> **`tests/conftest.communicate`** — each with a docstring asserting the property it did not have
> (*"can never wedge the bridge … a process that never returns"*, *"`proc.communicate()` that can
> never hang"*). The `agb_mac` one is the worst: it is the **bridge's rendering path**, so a
> grandchild holding the pipes stops every row updating. All three are bounded now.
>
> ⚠️ **That is the transferable part.** One instance was found live; the other two were found by
> *reading for the shape*, one file over, in code whose comments claimed immunity. `CLAUDE.md`'s
> shape C — a rule argued on one side and not carried to the other — and the tell each time was a
> docstring making a promise about behaviour rather than describing code.

## What is guaranteed, and what is not

`agb-peer:_spawn` on `TimeoutExpired` does:

```python
proc.kill()          # the DIRECT CHILD only
proc.communicate()   # ⚠️ no timeout — waits for EOF on the pipes
```

A grandchild **inherits those pipes**. So if the command spawns a helper that outlives it, the
second `communicate()` waits for an EOF that never comes.

**Measured, both shapes**, with a fake `agtermctl` that passes `tree` through and hangs on
`dashboard`:

| the hang is… | result |
|---|---|
| the direct child (`exec sleep 300`) | returns at **31 s** against a 30 s budget — correct |
| a grandchild (`sleep 300` as an ordinary child) | **never returns**; killed by hand at two minutes, intermediate shell already gone, grandchild still holding the pipe |

⚠️ **The bad shape is worse than the case it was reaching for.** A timeout is reported and
recoverable — `grid_unknown` says "the grid MAY BE UP" and prints the close command. An unbounded
wait produces **no output at all**.

## Why it is not fixed

`agtermctl` is a single binary today, so no grandchild exists. And `_spawn` is the shared path for
*every* subprocess in `agb-peer` — the relay's ticks, every `agtermctl` call, the ssh fetches — so
changing its kill semantics on a branch about dashboards is the wrong place to do it.

## The two fixes, when it matters

1. **Bound the second wait**: `proc.communicate(timeout=...)` inside its own `try`. Smallest change,
   turns an unbounded hang into a bounded one. Does not reap the grandchild.
2. **Kill the process group**: `Popen(..., start_new_session=True)` plus `os.killpg` on timeout.
   Correct rather than merely bounded, and 3.6-compatible — but it changes signal semantics for
   every caller, so it wants its own tests.

Prefer 2 if `agtermctl` ever grows a helper; 1 is the safe interim.

## The general lesson

This is shape D from `CLAUDE.md` — an indefinite outcome collapsed into a definite one — arriving
in the *mechanism that implements the timeout*, rather than in a caller reading its result. The
check that finds this class is: **a bounded wait is only bounded if everything holding the pipe is
what you killed.**


## Why fix 2 was not taken — a cost this entry did not name

The entry above recommends fix 2 (`start_new_session=True` + `os.killpg`) as *"correct rather than
merely bounded"* and notes only that it "changes signal semantics for every caller". That
understates what changes.

⚠️ **`start_new_session=True` detaches the child from the controlling terminal.** A child in its own
session no longer receives the terminal's SIGINT — so **Ctrl-C at an `agb-peer relay` prompt would
stop reaching the subprocess it is currently running.** That is a user-visible regression in the
interactive path, paid to fix a case that is **unreachable** with today's single-binary `agtermctl`.

So the trade is not "correct versus bounded", it is "correct for a hypothetical grandchild, at the
cost of interrupt handling that works today". Fix 1 was taken on those grounds. ⚠️ **Revisit if
`agtermctl` ever grows a helper process** — at which point the case stops being hypothetical and the
trade genuinely reverses. The check that would notice: `_spawn`'s worst case is
`timeout + REAP_TIMEOUT`, and a grandchild that survives it is still running with the pipe open.
