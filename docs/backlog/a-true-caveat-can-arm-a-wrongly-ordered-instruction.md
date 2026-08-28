# A correct addition can arm a latent ordering defect without touching it

**Found live**, 2026-08-28, on the first real use of `agb-hangout`, and diagnosed by the peer agent
from the failing turn. Recorded because it is **not** any of the shapes in `CLAUDE.md` and was nearly
filed as one of them.

## What happened

`## Starting one` read, in order:

1. *"If you do not already know your peer's name, `agb-peer who` asks the relay for it."*
2. *"**Then** open with something."*

So the send was made sequentially dependent on an **asynchronous** step. A review that day added a
warning to step 1 — **true, necessary, and asked for**:

> ⚠️ The answer does not come back from that command — it arrives on a *later* turn.

An agent whose user had **already named the peer** then ran `who` anyway (it was first, and "Then"
implies order), read the warning, correctly declined to poll, and **ended its turn having sent
nothing**.

## Why this is its own shape

The ordering defect is the **structure**: a blocking step in front of the action, with a guard that
does not stop a reader who is going in order. That existed before and is the same defect as
`agb-peer`'s usage line putting the agterm-only form first.

⚠️ **What is new is the effect of the fix.** Before the warning, an agent might well have blundered
into opening anyway — running `who`, getting nothing useful, and proceeding. **The warning is what
made waiting the correct reading of a wrongly-ordered instruction.** So:

- it is **not a regression** — the added text is true, and needed;
- it is **not a wrong fix** — the thing it warned about is real and had already cost a defect;
- it **did not touch the defect** — the ordering was not changed, examined, or mentioned.

**A true statement can convert a latent structural defect into a reliable one.** The clearer and more
correct the addition, the more reliably the bad structure is followed — because the reader now has a
*good reason* to do the wrong thing.

## The check

⚠️ **When you add a caveat to a step, ask what the caveat tells a reader to DO, and whether the step
should have been there at all.** A warning answers *"what should I know about this step"*; it never
asks *"should this step be first"*. Adding one is the moment you have the step's full context in
front of you and the last moment anybody will look at it.

⚠️ **And re-read the adjacent sections.** The same review that added this warning had *already*
written, one paragraph below, that a cold agent needs no lookup at all — *the relay signs the
message, so the name is already in front of you*. The two sections agreed with each other everywhere
except **the one place an agent reads first**. That half is `CLAUDE.md`'s shape E; this entry is
about the other half, which E does not cover.

## Status

The instruction is fixed — opening is now first and unconditional, and the lookup is a labelled
fallback. **This entry is about the class, not the instance**, and the class has no guard: nothing in
the suite reads a skill file for ordering, and nothing could — the defect is that a correct file was
correct in the wrong sequence.
