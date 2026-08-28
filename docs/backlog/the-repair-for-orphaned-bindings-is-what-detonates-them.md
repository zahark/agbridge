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

## 🔴 Inert litter and armed litter look identical in the map

**Measured 2026-08-28**, both sides of the wire, while four more dead bindings accumulated in front
of the two agents watching — the first time this was observed happening rather than found afterwards.

Two independent properties, and **they are on opposite sides of the wire**:

| property | decides | visible in the rows map? | visible in the statedir? |
|---|---|---|---|
| `done` vs `bound` | whether anything **sweeps** it | ✅ yes | no |
| key still **alive** | whether `agb-refresh` **re-mints** it | ❌ **no** | ✅ yes |

- `done` + row gone → `close-done` **tries**, fails, prints `close by hand`. Visible, and
  sweepable-in-principle.
- `bound` + row gone → `close-done` does not even consider it. **Invisible.**

⚠️ **And the danger runs the opposite way to the visibility.** Four `hangout-*` bindings left over
from a single evening's restarts are all `done`, and their keys are **dead** — reaped when the pids
died, confirmed absent from the statedir. `agb-refresh` cannot re-mint what no longer beats, so they
are **inert**. The one entry that matters is `bound`, and its key is **alive and beating** (measured:
pid up, beat 6 s old) — so a refresh *will* re-mint it, as a second row with a duplicate label.
**Armed.**

🔴 **So a cleanup tool reading only the map cannot tell inert litter from armed litter**, and the
map's own `done`/`bound` marking points the wrong way: the noisy, sweepable, visible entries are the
harmless ones, and the quiet invisible one is the hazard. **The discriminator is whether the key is
still in the feed**, which is on the *agent host's* side and is not something `agb-refresh` reads.

⚠️ That is a harder problem than the one `close-done` fails at, and it is the reason the "make
`agb-refresh` refuse" idea below is not simply a matter of counting labels: the refusal must be
conditioned on liveness, and liveness lives somewhere else.

## ⚠️ And ARMED is not DANGEROUS — a third split, measured 2026-08-28

Both sides counted, six orphaned-`bound` bindings on one instance:

| binding | key in the statedir? | consequence of `agb-refresh` |
|---|---|---|
| 3 of them | **absent** — already reaped | **inert**: nothing to re-mint |
| `glc_toucan_integ` | **alive**, beating | **armed → a REPAIR**: a live agent with no row gets one back |
| `glc_nesher_be_branch_2` | **alive**, beating | **armed → a REPAIR** |
| `agbridge-public` | **alive**, beating | 🔴 **armed → an OUTAGE**: it duplicates a live row's label |

🔴 **So the command is not dangerous; it is dangerous for exactly ONE entry and beneficial for two.**
Two live agents are sitting with no row at all right now, and `agb-refresh` is precisely what gives
them one. Framing it as "the repair detonates the litter" was true of the entry that prompted this
file and wrong as a general statement — **the hazard is not re-minting, it is re-minting into a
duplicate label.**

The full test therefore has **three** questions, on two sides of the wire, and only the last is about
danger:

1. is the binding orphaned? *(the rows map, Mac side)*
2. is its key still beating? *(the statedir, agent side)* — decides **inert vs armed**
3. **does its label duplicate a live row's?** *(the tree, Mac side)* — decides **repair vs outage**

⚠️ Nobody was asking question 3 until three armed entries turned up and two of them were harmless.

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
