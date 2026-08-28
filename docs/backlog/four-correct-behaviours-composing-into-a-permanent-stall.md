# Four correct behaviours composing into an agent that waits for ever

**Observed live**, 2026-08-28, and reconstructed from the relay's own output afterwards. ⚠️ **No
component is wrong.** Every step is documented, defended in a docstring, and doing what it should.
The defect exists only in the composition, which is why nothing in the suite could see it and why
each half was independently diagnosed as fine.

## The chain

1. The roster is **reverted** (by something never identified), removing a participant.
2. That agent's `agb-peer who` therefore **cannot be answered** — `answer_who` is reached only for a
   participant, and it is not one any more.
3. The roster is **restored**, and the agent is a member again.
4. ⚠️ The relay's **priming rule discards the pending `who` as predating the join**:
   `discarded 1 message(s) that predate a join`.
5. Nothing ever arrives. **The agent waits for ever.**

Each step defends itself, correctly:

| step | why it is right |
|---|---|
| membership from the **roster**, not the resolved map | otherwise mail for an agent that has not booted is silently discarded |
| a non-member's `who` is not answered | it is not a participant; answering would leak the room to a stranger |
| a rejoin is **primed** | the pane's existing content is discarded rather than delivered, or the first real message drains an hour-old conversation |
| priming **discards rather than delivers** | said out loud in `relay_tick`'s docstring: *"the honest move is to clear them and say how many"* |

## Why it matters more than the incident

⚠️ **The stall was diagnosed as transient and it was permanent.** The first reading — *"it will
probably recover when the relay's answer arrives"* — was wrong, and wrong in the reassuring
direction: the answer had already been thrown away by step 4 before anybody looked. **A stall whose
recovery depends on a message that a correct rule has already deleted does not recover.**

⚠️ **And it is the strongest possible argument for the rule added the same day** — *do not end your
turn in silence waiting for `who`*. That rule was written against a **transient** wait, on the
reasoning that disappearing is bad manners. It turns out to be the only thing standing between an
agent and an unbounded one. An agent that says what it is waiting for gets rescued by its user; an
agent that waits silently is simply gone.

## What would fix it

Nothing obvious, and that is the honest answer.

- ⚠️ **Do not make priming deliver.** The rule it breaks is worse: a rejoining participant would
  drain an hour of stale conversation into its composer as if it were new.
- ⚠️ **Do not answer a non-member's `who`.** That is the leak the membership check exists to stop.
- A **bounce on an unanswerable `who`** would help, and is the same shape as the drop-bounce added
  the same day — the relay knows the asker's pane and can type into it. But an unanswerable `who`
  comes from a **non-participant**, and typing into a non-participant's pane is precisely what the
  membership check refuses to do. **The fix collides with the rule that creates the problem**, which
  is the thing to notice before proposing it a second time.

## The general form

**A composed failure has no faulty component, so every review that examines components passes it.**
Each of these four is defended in a comment, and three of the four comments explain why the
*opposite* behaviour would be a bug. The only way this was found was watching an agent do nothing
and then reading a log written for a different purpose.
