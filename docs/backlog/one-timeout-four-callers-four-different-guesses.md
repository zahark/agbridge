# One timeout, four callers, four different guesses

**The unification**, 2026-08-28, proposed by the peer agent who found the first of these while
stress-testing `agb-dashboard`. Three backlog items filed separately turned out to be one shape:

> **`_spawn`'s timeout tells you a call did not ANSWER. It never tells you whether it TOOK EFFECT.**
> So every caller has to decide what an indefinite outcome means — and they decided differently.

That is why these read as unrelated bugs: the *decision* is local, so each wrong one corrupts
something different. The **root** is that the indefiniteness originates in one place and fans out.

## The four decisions

| caller | what it does with "did not answer" | what that costs |
|---|---|---|
| `Ctl.dashboard` (`agb-peer`) | **now** treats it as *unknown* — says "the grid MAY BE UP", closes nothing, prints the close command | fixed 2026-08-28; was "would not open" |
| `agb_mac._run_command` → `RowRenderer._new` | reads it as *the row does not exist* | ⚠️ re-issues `session new`; if agterm made the row and only failed to answer, the agent gets a **second row** and the first is orphaned in **no map at all** — unreachable by `close-done`, `forget-rows` or `agb-refresh`. See [a-timed-out-session-new-can-mint-a-second-row.md](a-timed-out-session-new-can-mint-a-second-row.md) |
| `Ctl.type` → `try_deliver` | flattens it to a default-code `PeerError`, decided by code alone | ⚠️ **retries** a delivery whose text may already be in the composer — verbatim what the exit-4 rule exists to prevent. See [a-timed-out-session-type-is-retried-and-can-double-deliver.md](a-timed-out-session-type-is-retried-and-can-double-deliver.md) |
| `_spawn` itself | claims the timeout is always a timeout | ⚠️ true only while the command has no surviving children; a grandchild holding the pipes makes the post-kill `communicate()` wait for an EOF that never comes. See [a-timeout-is-only-a-timeout-without-grandchildren.md](a-timeout-is-only-a-timeout-without-grandchildren.md) |

Three of the four are still open. Only the grid one has been fixed.

## Why one write-up rather than three fixes

⚠️ **There is no single correct answer to substitute.** `Ctl.type` is the proof: coding a timeout as
*may already have been delivered* is right for a message and **wrong for the arming newline**
`ensure_composer` and `scan_participant` send, where the same code would drop a message that was
never delivered at all. The decision genuinely belongs at the call site.

So the shared fix is not a policy — it is **making the third outcome visible**. Today `_spawn`
returns `TIMED_OUT` and most callers narrow it to a boolean before anyone can act on it. A caller
that cannot see "unknown" cannot choose, and will guess. The grid fix is the worked example:
`Ctl.dashboard`'s `ok` became three-valued (`True`/`False`/`None`) rather than gaining a fourth
return value, so `if not ok` still caught it and no other caller changed.

## The check

At every `if not ok`, every `else`, and every `except` around a subprocess: **ask whether this is a
proof or merely not-the-yes** — and, the half this unification adds, **ask whether the caller could
even tell the difference if it wanted to.** If the value it was handed cannot express "unknown",
the guess was made upstream and the call site is only where it surfaces.

`CLAUDE.md` → *Bug shapes that keep coming back* → **shape D** is the general form.
