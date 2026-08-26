# agb-peer `who` — let an agent ask who is in the conversation

> **Plan B of two.** Plan A (the dynamic roster) is **merged to `main` and verified live** —
> [`completed/20260825-agb-peer-dynamic-roster.md`](completed/20260825-agb-peer-dynamic-roster.md).
>
> **Revision 4**, and it is much smaller than revisions 1–3. Three review rounds (7, 5 and 4
> criticals) all found their sharpest defect in the same place, and the fourth answer was to stop
> patching it. **Corrections from revision 3** records what was cut and why.

## Overview

After Plan A, attaching an agent to a running relay changes where messages route and nothing else.
The agent cannot discover who is in the conversation, or even its own participant name —
`skills/agb-peer/SKILL.md` currently tells it to ask the user.

`agb-peer who` asks the **relay**, over the channel that already exists: the agent sends a request
like any other message, and the relay answers by typing the roster back into its pane.

## Corrections from revision 3 — what was cut, and why

Revisions 1–3 had the relay **publish** the roster to each participant, which the agent then read
locally: a tmux pane option where tmux was reachable, and a file under `--chat-dir` where it was
not. That design is gone. It failed three times in the same place, and the failures were not
independent:

| round | the NFS finding |
|---|---|
| 1 | `you` rested on `$AGB_PEER_FROM` — read at exactly one place, in the **legacy** one-shot form; `cmd_send` never reads it and no launcher exports it |
| 2 | the "spec-exemption" yielded `roster.nfs` — one shared file for everybody, because a `:nfs` spec's target is the **sentinel string**, not a pane |
| 3 | the replacement key was not unique either (pane ids are per tmux *server*), and the guard added to cover that **refuses this project's own canonical two-host fixture** |

⚠️ **One root cause: there is no per-agent identity on the file transport.** The *message* path never
needed one — the id is in the filename and the recipient is inside the file. `who` was the first
thing that had to answer *"which one are **you**"*, and each round invented an answer that did not
exist.

**Asking removes the question.** `try_deliver`'s own comment states the primitive:

> *The sender is the pane the marker was FOUND in, never the `from` field. A pane is a place, and an
> agent cannot print into another agent's pane, so the place is the only part of this that cannot be
> misstated.*

The relay already knows who asked, by the one means it trusts. So it needs no key, no filename, no
collision guard.

**What this deletes outright:** the `_spawn` stdin seam (it existed solely for `ssh … tee`), all nine
roster argv builders, `publish_targets`, `publish_roster`/`unpublish_roster`, the last-published
memo, the four-term publish gate, the pane-collision guard, the unconditional startup write and its
60-second negative-dentry window, the `--chat-dir` symmetry work, the `_name_notes` growth, and the
leaver/rebind unpublish problem. **None of it is replaced by anything.**

⚠️ **This is not the "proactive announcement" that was rejected.** That one wakes every agent and
spends a turn none of them asked for. This spends a turn the asking agent chose to spend, and
touches nobody else. The rule kept is *never interrupt an agent with something it did not ask for*.

## The design

Three steps, each on machinery that is already live-verified:

1. **The agent asks.** `agb-peer who` sends a message to a **reserved recipient** through the
   ordinary `send` path — a tmux pane option where tmux is reachable, a file plus a printed doorbell
   where it is not. Both legs already work and are verified live, in both directions.
2. **The relay answers.** Draining a participant's doorbell, it finds a message addressed to the
   reserved name. Instead of routing it, it queues a reply **to the sender** — and the sender is the
   pane the doorbell rang on, which is the identity that cannot be misstated.
3. **The reply arrives as a message.** `[chat from relay] you=alice peer=bob peer=carol`, delivered
   by `try_deliver` like anything else: the same composer gate, the same holding, the same
   throttles.

⚠️ **`who` is therefore asynchronous, and that is the whole cost.** It prints *"asked the relay"* and
finishes; the answer arrives as a prompt on a later turn. `SKILL.md` already forbids agents to poll
or wait, so this is the discipline they are built for — but the skill must say plainly that **no
answer is not an error**, and must not invite a retry loop.

### Membership only

The reply carries names and nothing else — no liveness, no timestamp. A status would be a snapshot
from the moment of the reply, read as current later; `agb-peer` deliberately has no heartbeat, so
there is nothing an age could honestly say. ⚠️ **And the peer list is the roster spec, never what
resolved**: a row is routinely absent for a moment while `agb-refresh` re-mints it, and deriving the
list from resolvable participants would report a transient absence as *left the chat*.

### Two ways to get no answer, and they are indistinguishable

**No relay is running**, or **this pane is not a participant** — in which case nobody reads its
doorbell. `who` must name both and neither must invite a retry.

## Context (from discovery)

Verified on `main` at plan time:

- `try_deliver` takes the sender from the pane the marker was found in (`agb-peer:1650-1653`), which
  is what makes the reply addressable with no new identity.
- Delivery to an unreachable participant already works — the pane belongs to a host the Mac *can*
  reach. `CLAUDE.md` records the pool case verified live **in both directions**.
- `cmd_send`'s file fallback prints a `[peer #<id>]` doorbell the agent must repeat on its own
  screen; `SKILL.md` already carries that rule and it was **observed working live** during Plan A's
  acceptance run.
- ⚠️ `parse_participants` accepts any name in `[A-Za-z0-9._-]`, so the reserved name must be refused
  there or a participant could shadow it.
- `_throttled(say, notes, key)` exists for say-once complaints; `notes` persists across ticks.
- ⚠️ `conftest.functions(*trees)` raises on cross-file duplicates — `statedir` **and** `main` collide
  between `agb` and `agb-peer`. Structural guards use the peer tree alone.
- ⚠️ `__pycache__/agb-peercpython-36.pyc` exists (dot-less). Mutation checks must delete it.
- `main(["who"])` **already raises today** via `--to is required`; there is no unknown-command path.
- Baseline **2254** tests.

## Development Approach

- **testing approach**: **Regular** (code first, then tests), as Plan A
- ⚠️ **No task may contain a test that cannot pass until a later task**, and **no test may assert a
  property already free on the branch it exercises.** All three review rounds found instances; Task 6
  audits for both
- ⚠️ **Never read an expected constant out of the implementation** — assert against a literal *and*
  compare the literal to the implementation
- **CHANGELOG entry in the commit that makes the user-visible change**
- backward compatibility: every existing caller of `send`, `drain`, `try_deliver` and `relay_tick`
  behaves exactly as today

## Implementation Steps

### Task 1: Reserve the recipient name

**Files:** Modify `agb-peer`, `tests/test_agb_peer.py`, `CHANGELOG.md`

- [ ] add `RELAY_NAME = "relay"` — the recipient that means *the relay itself*
- [ ] make `parse_participants` refuse it as a participant name, saying why
- [ ] write a test that `relay=<row>` is refused, with the reason
- [ ] write the companion that an ordinary name is still accepted, so the refusal is not a blanket one
- [ ] add the CHANGELOG entry
- [ ] run tests — must pass before Task 2

### Task 2: Compose the answer

**Files:** Modify `agb-peer`, `tests/test_agb_peer.py`

- [ ] add `roster_answer(you, members)` → `you=alice peer=bob peer=carol`, peers sorted, `you`
      always present. Plain text: it is delivered as a message and read by a human and an agent
- [ ] ⚠️ `peers = members - {you}` where `members` is the **roster spec**, never the resolved set
- [ ] write a round-trip-shaped test: the answer names every participant and marks exactly one `you`
- [ ] write a test for a **one-participant** roster — `you=alice` with no peers, which `RosterReader`
      explicitly permits
- [ ] write a test that a name absent from the spec but present in `resolved` does **not** appear
- [ ] run tests — must pass before Task 3

### Task 3: The relay answers a `who` request

**Files:** Modify `agb-peer`, `tests/test_agb_peer.py`, `CHANGELOG.md`

- [ ] in `relay_tick`'s drain loop, a message whose `to` is `RELAY_NAME` is **not routed**: queue a
      reply to the **sender** — the pane the doorbell rang on — and do not add the request to
      `pending`
- [ ] the reply's text is `roster_answer(sender, members)`; `members` is the spec already passed in
      as `members=`
- [ ] deliver it through the ordinary `pending` path, so it inherits the composer gate, the holding
      and the throttles with no new code
- [ ] ⚠️ sign it so the agent can tell it apart from a peer: it arrives as
      `[chat from relay] you=…`, which `compose` produces if the reply's sender is `RELAY_NAME`
- [ ] write a test that a request is answered and the **request itself is never delivered** to
      anybody — the companion that stops "answered" passing against a relay that also routes it
- [ ] write a test that the answer goes to the **asker**, with a second participant present that
      must not receive it
- [ ] write a test that the `you` in the answer is the asking pane's name, using two askers in one
      tick to prove it is not a constant
- [ ] write a test that an ordinary message is still routed normally
- [ ] add the CHANGELOG entry
- [ ] run tests — must pass before Task 4

### Task 4: `agb-peer who` — the agent side

**Files:** Modify `agb-peer`, `tests/test_agb_peer.py`, `CHANGELOG.md`

- [ ] add a `who` verb dispatching to `cmd_who`, which sends `RELAY_NAME` a request through the
      **same `cmd_send` path** — no new transport, and the file fallback comes free
- [ ] print what happened: that the relay was asked, and that **the answer will arrive as a
      message**, not on this command's output
- [ ] ⚠️ print that **no answer means either no relay is running or this pane is not a participant**,
      and that neither is worth retrying
- [ ] ⚠️ on the file fallback, the `[peer #<id>]` doorbell must reach the agent's **visible screen** —
      the same rule `send` has, for the same measured reason
- [ ] refuse outside tmux with `send`'s existing message; `$TMUX_PANE` is required
- [ ] add `who` to `USAGE`
- [ ] write a test that `who` makes **no agtermctl call** — it runs on a farm host where agterm does
      not exist — with the companion showing the fake would have recorded one
- [ ] write a test that `who` refuses outside tmux
- [ ] write a test that the request is addressed to `RELAY_NAME`
- [ ] ⚠️ write the dispatch guard as a **monkeypatch of `peer.os.environ`**: `main` is
      `main(argv, out=None, ctl=None)` with nowhere to pass `env`/`run`. There is **no
      unknown-command path** — `main(["who"])` already raises today via `--to is required` — so the
      guard is "reaches `cmd_who`", not "does not raise"
- [ ] add the CHANGELOG entry
- [ ] run tests — must pass before Task 5

### Task 5: `skills/agb-peer/SKILL.md` — the trigger

⚠️ Not optional. An agent has no event loop; it acts on instructions, not events. **`who` is dead
weight unless the skill says when to run it.**

**Files:** Modify `skills/agb-peer/SKILL.md`, `tests/test_agb_peer.py`

- [ ] add an `agb-peer who` section — what it does, and that **the answer arrives as a prompt on a
      later turn**, not as command output
- [ ] say **when**: before addressing someone it has not talked to, and when a message arrives from a
      name it does not recognise
- [ ] ⚠️ **say that no answer is not an error and must not be retried in a loop** — this is the one
      place the async design can go wrong, and `SKILL.md`'s existing "never poll, never wait" rule is
      the thing to point at
- [ ] say the answer arrives as `[chat from relay] …` and is not a peer talking
- [ ] ⚠️ `test_the_skill_has_nothing_to_fill_in` asserts the literal `"ask the user"` is present
      (`tests/test_agb_peer.py:1032`); decide deliberately whether that phrase survives — `who` makes
      "do not guess" *cheap*, not obsolete — or amend the test with a stated new invariant
- [ ] extend `test_the_verbs_the_skill_names_are_dispatched` to cover `who`
- [ ] keep it agent-agnostic — both the Claude and Codex ends read this file
- [ ] run tests — must pass before Task 6

### Task 6: Documentation and acceptance

**Files:** Modify `docs/design.md`, `docs/commands.md`, `docs/cookbook.md`

- [ ] extend §6 with the ask-and-be-told model, **and why publishing was abandoned** — three rounds,
      one root cause, no per-agent identity on the file transport. A future reader will otherwise
      reinvent it
- [ ] ⚠️ **delete `agb-peer who` from §6's *Deferred, with reasons*** — a stale "named gap" is worse
      than none
- [ ] ⚠️ **replace** `docs/cookbook.md:770`'s *"An agent cannot yet ask who is in the chat"*
- [ ] give `who` a real entry in `docs/commands.md` beside `send` and `relay`; qualify the section
      heading *"`agb-peer` — Mac, not installed by default"*, since `who` runs on the agent's machine
- [ ] record the limitation: **the answer costs the agent a turn**, and there is no way to know a
      request was lost
- [ ] ⚠️ **audit every task's tests for the two banned shapes** — cannot-pass-yet, and free-on-branch
- [ ] run the full suite — **2254** before this plan
- [ ] bump `VERSION` 0.2.0 → 0.3.0, and add the guard that does not exist today: nothing asserts
      `agb-peer`'s `VERSION` or that `USAGE` lists every verb `main` dispatches
- [ ] correct the test counts in all four places: `CLAUDE.md:8`, `CLAUDE.md:697`, `README.md:317`,
      `README.md:329`
- [ ] move this plan to `docs/plans/completed/`

## Post-Completion — the live acceptance walkthrough

⚠️ **Plan A's live run found five defects and every one was in the documentation.** Check these
before blaming the code: roster labels are **substrings** (session names must not nest); farm
participants need `@<ssh alias>` and it fails **late**, on the first send; `AGB_CLAUDE_CUSTOM` means
`agb-claude` starts **no local agent**; `agb-claude -d` is a **no-op** on an existing session; the
farm shell is **tcsh** (a bash heredoc hangs — pass the message as an argument), the Mac is zsh, and
**agents run bash**.

### Setup

Plan A's cast, plus one pool agent so the file leg is exercised:

```sh
# farm, one tcsh shell
unsetenv AGB_CLAUDE_CUSTOM
agb-claude -d peer-a
agb-claude -d peer-b
env AGB_CLAUDE_CUSTOM='<your qsub line with {env} and {}>' agb-claude -d pooled
```

Roster on the Mac — unique names, none a prefix of another, `@<alias>` on every farm participant:

```
alice=peer-a@<alias>
bob=peer-b@<alias>
pooled=pooled@<alias>:nfs
```

```sh
agb-peer relay --roster ~/peers --interval 2 --chat-dir <statedir>/chat
```

### Checks

| # | Do | Expect | Failure |
|---|---|---|---|
| **0** | **Regression.** Plan A's Check 0 unchanged. | `alice -> bob: delivered` | stop — this branch broke the roster |
| **1** | Prompt `peer-a` to run `agb-peer who`. | it prints *asked the relay*; then a **later turn** brings `[chat from relay] you=alice peer=bob peer=pooled` | no reply — check the relay saw the doorbell |
| **2** | ⭐ Same on the **pool** agent. | ⚠️ it takes the **file** path and prints `[peer #…]`, which the agent must repeat on its screen; then the reply arrives | no reply: the marker never reached the visible screen — the failure `SKILL.md` exists to prevent |
| **3** | Add a participant, then `who` again. | the new name is in the answer | stale — the answer is not built from the live spec |
| **4** | Remove one, then `who` again. | it is gone | stale |
| **5** | Stop the relay; `who`. | *asked the relay*, then **silence** | anything claiming an answer |
| **6** | `who` from a tmux pane that is **not** a participant. | same silence | — |
| **7** | Two agents ask in the same tick. | each gets **its own** `you=` | both get the same name |
| **8** | `who` outside tmux. | refused, naming `$TMUX_PANE` | — |

### Known limitations — not bugs

- **The answer costs a turn.** It arrives as a prompt on a later turn, never as command output.
- **A lost request is silent.** No relay, or not a participant, and nothing distinguishes them — by
  design, since neither is worth retrying.
- **No rooms**, and **no broadcast** (`--to all`): with three or more agents a reply-all is a
  feedback loop with no natural stop, which `SKILL.md`'s anti-deadlock rule does not cover.
