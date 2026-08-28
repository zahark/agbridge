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
| `agb_mac._run_command` → `RowRenderer._new` | **half done** — the value can now SAY "timed out" (`TIMED_OUT`, as against `None` for *could not start*), but `_new` still reads any non-zero as *the row does not exist* | ⚠️ unchanged in effect: re-issues `session new`; if agterm made the row and only failed to answer, the agent gets a **second row** and the first is orphaned in **no map at all** — unreachable by `close-done`, `forget-rows` or `agb-refresh`. See [a-timed-out-session-new-can-mint-a-second-row.md](a-timed-out-session-new-can-mint-a-second-row.md) |
| `Ctl.type` → `try_deliver` | flattens it to a default-code `PeerError`, decided by code alone | ⚠️ **retries** a delivery whose text may already be in the composer — verbatim what the exit-4 rule exists to prevent. See [a-timed-out-session-type-is-retried-and-can-double-deliver.md](a-timed-out-session-type-is-retried-and-can-double-deliver.md) |
| `_spawn` itself | ✅ **fixed 2026-08-28** — the post-kill reap is bounded (`REAP_TIMEOUT`), worst case `timeout + 5 s` | was: an unbounded hang with no output whenever a grandchild held the pipes. ⚠️ And it turned out to be **three copies** — `agb_mac._run_command` and `tests/conftest.communicate` had it too, both bounded in the same pass. See [a-timeout-is-only-a-timeout-without-grandchildren.md](a-timeout-is-only-a-timeout-without-grandchildren.md) |

**One fixed outright** (`_spawn`), **one fixed earlier** (the grid), **one half done**, **one open.**

🔴 **And the half-done one is this entry's own warning happening, so it is flagged rather than
counted as progress.** Making `_run_command` able to *express* the third outcome is the shared fix
this entry argues for — but `_new` does not yet *read* it, so the shape has landed without the
decision, which is the outcome the section below says is worse than leaving it alone. It is recorded
here as **half** deliberately: the ledger must not let "the value can now say it" read as "somebody
acted on it". ⚠️ **If you are in `agb_mac` for any reason, that is the file, and `_new` is the
function.**

## Why one write-up rather than three fixes

⚠️ **There is no single correct answer to substitute.** `Ctl.type` is the proof: coding a timeout as
*may already have been delivered* is right for a message and **wrong for the arming newline**
`ensure_composer` and `scan_participant` send, where the same code would drop a message that was
never delivered at all. The decision genuinely belongs at the call site.

So the shared fix is not a policy — it is **making the third outcome visible**. Today `_spawn`
returns `TIMED_OUT` and most callers narrow it to a boolean before anyone can act on it. A caller
that cannot see "unknown" cannot choose, and will guess. The grid fix did this:
`Ctl.dashboard`'s `ok` became three-valued (`True`/`False`/`None`) rather than gaining a fourth
return value, so `if not ok` still caught it and no other caller changed.

⚠️ **But do NOT copy that as a pattern, and the reason is worth understanding before touching the
other three.** `None` is falsy, which is exactly why the change landed for free — and it is exactly
why an unconverted caller keeps its old guess **silently, and looking correct**. The migration
convenience and the hiding place are the same mechanism. Three-valued-via-falsy does not *make*
anyone reckon with the unknown; it only *permits* it. That was fine here because the reckoning
happened in the same commit, for the only two callers. Applied to the remaining three it would land
the shape without the decision, and the result would **look finished**, which is worse than leaving
them open.

If they are ever to be forced rather than invited, the third outcome has to be something a boolean
context **cannot swallow** — a distinct exception type, or a value that raises on `__bool__` — so an
unconverted call site fails loudly instead of quietly keeping its guess. That costs a migration and
buys the guarantee that no caller is still guessing by accident. It is the honest option; it is also
why "fix each at its own call site, when somebody is actually in that file" remains the plan.

## The check

At every `if not ok`, every `else`, and every `except` around a subprocess: **ask whether this is a
proof or merely not-the-yes** — and, the half this unification adds, **ask whether the caller could
even tell the difference if it wanted to.** If the value it was handed cannot express "unknown",
the guess was made upstream and the call site is only where it surfaces.

`CLAUDE.md` → *Bug shapes that keep coming back* → **shape D** is the general form.
