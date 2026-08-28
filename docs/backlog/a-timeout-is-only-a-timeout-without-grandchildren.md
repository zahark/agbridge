# `_spawn`'s timeout kills the child, not the process group

**Found live**, 2026-08-28, by a peer agent stress-testing `agb-dashboard` against a deliberately
wedged `agtermctl`. **Not reachable today** — recorded because the failure is silent and the day it
becomes reachable is a day nobody would think to look here.

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
