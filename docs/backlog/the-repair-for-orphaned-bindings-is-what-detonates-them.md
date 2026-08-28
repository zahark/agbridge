# The documented repair for an orphaned binding is the exact thing that detonates it

**Found live**, 2026-08-28, by a peer agent counting row titles on a Mac while the other half of the
conversation ran over the row in question. ⚠️ **Not urgent in the ordinary case** — and that is
precisely what makes it dangerous, because the state is stable until somebody tries to tidy it up.

## The state

An entry can be **bound to a row agterm does not have**. Not "no row was minted" — minted, then the
row went away (closed by hand, an agterm restart), and the binding survived. `_agtermctl` already
documents this as unrecoverable from inside the bridge and names `agb-refresh` as the fix.

Measured on one live instance:

| bindings in the map | naming a session that exists |
|---|---|
| 12 **bound** | 7 — the other **5** are orphaned |
| 6 **`[done]`** | 0 — `close-done` answered `closed 0 of 6` |
| **18 total** | **7** |

⚠️ **`close-done` cannot help with either half**, and for the same structural reason: it *closes
rows*, and these have no rows to close. A cleanup that exists but cannot reach the mess is close to
not existing.

## 🔴 The inversion

The obvious fear is that a re-resolve trips over it. **It does not, and getting that backwards leads
you to the wrong urgency in both directions.**

- **A re-resolve is SAFE.** `resolve_all` reads agterm's **tree**. An orphaned binding is not in the
  tree, so it cannot be matched. One match now, one match for ever.
- 🔴 **`agb-refresh` is what detonates it.** It forgets the bindings and re-mints — so an orphaned
  entry whose label duplicates a live row's gets a **new, real row**, the selector then matches
  **two**, and the relay's next resolve refuses. The channel goes quiet.

**So the documented repair for this litter is the exact command that turns it into an outage.** That
is a worse property than the litter itself, and nothing warns you: `CLAUDE.md` names `agb-refresh`
for orphaned bindings without the precondition that no orphan's label may duplicate a live row's.

## It has already happened once, unnoticed

⚠️ The peer agent ran `agb-refresh --instance <name>` earlier the same evening for an unrelated fix.
The orphaned key's bound id changed across that run, which is very likely when it was rebound — so a
**second row with the duplicate label plausibly existed for a while and nothing noticed**. It could
not be determined afterwards whether it rendered and was closed or never rendered.

⚠️ **The reason nothing noticed is the same reason this is hard to see: a duplicate label costs
nothing until somebody resolves that label**, and every resolve in that window happened to land on
the live row.

## What to do about it, in order

1. **Before running `agb-refresh` on an instance, check that no orphaned binding's label duplicates a
   live row's.** That is the missing precondition, and it is a name-substring count over the tree —
   the same operation `match_sessions` performs, not a proxy for it.
2. **Deal with the duplicate first**, which usually means ending the second agent. A label cannot be
   changed on a running agent: it is the tmux session name, resolved once at the first hook.
3. Only then refresh.

## What would fix it properly

Not attempted, and it needs a decision rather than a patch:

- ⚠️ **`agb-refresh` could refuse** when re-minting would produce two rows with the same label,
  naming both. That makes the dangerous command safe by default — but it also makes the *only*
  cleanup refuse in exactly the state you most want cleaned, so the refusal has to come with the
  remedy printed beside it or it is a dead end.
- A binding whose row is absent from the tree could be **dropped on sight** rather than kept until a
  refresh. ⚠️ But "absent from the tree" is one poll's answer about another process, and this project
  does not convert a single negative observation into a removal — see invariant 2. It would need the
  same positive-proof treatment session unlinks get.
