# agb-peer `who` — let an agent ask who is in the conversation

> **Plan B of two.** Plan A (the dynamic roster) is **merged to `main` and verified live** —
> [`completed/20260825-agb-peer-dynamic-roster.md`](completed/20260825-agb-peer-dynamic-roster.md).
>
> **Revision 6**, and much smaller than revisions 1–3. Five review rounds: 7, 5 and 4 criticals all
> condemned a design that no longer exists, then 5 and 2 against this one — the last of which called
> it *"right"* and *"implementable"*. **Corrections from revision 3** records what was cut and why.

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

### ⚠️ The answer must not invite a reply

`SKILL.md` tells an agent that anything arriving as `[chat from <name>] …` is *"a peer talking to
you"* and to **"Reply by sending, exactly as above."** So an answer signed `[chat from relay]` would
be replied to, and the reply is another request, and so on — a two-party feedback loop with no
natural stop. ⚠️ **This plan's own *Known limitations* rejects broadcast for exactly this failure
class and did not notice it had built the two-party case.**

Stopped **structurally, in the relay**, not by asking the agent nicely:

- the relay answers only a message whose text is the **recognised request token**; anything else
  addressed to `relay` is dropped with a line saying so. A polite *"thanks!"* is therefore not a
  request, and the loop cannot start.
- the skill says separately that a `[chat from relay]` answer **is not a peer and needs no reply**.
  That is the mitigation, not the mechanism — a rule an agent may ignore, backed by a rule it cannot.

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

- `try_deliver` takes the sender from the pane the marker was found in (`agb-peer:1632-1635`), which
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
  property already free on the branch it exercises.** Every review round found instances, so **each
  task audits its own tests, inside that task** — an audit at the end catches nothing it can act on
- ⚠️ **Never read an expected constant out of the implementation** — assert against a literal *and*
  compare the literal to the implementation
- **CHANGELOG entry in the commit that makes the user-visible change**
- backward compatibility: every existing caller of `send`, `drain`, `try_deliver` and `relay_tick`
  behaves exactly as today. ⚠️ One deliberate exception: `who` becomes a **verb**, so
  `agb-peer who <args>` is no longer available as the direct one-shot form — exactly as `send` and
  `relay` already are

## Implementation Steps

### Task 1: Reserve the recipient name and the request token

**Files:** Modify `agb-peer`, `tests/test_agb_peer.py`, `CHANGELOG.md`

- [x] add `RELAY_NAME = "relay"` (the recipient meaning *the relay itself*) and `WHO_REQUEST = "who"`
      (the only text it answers)
- [x] make `parse_participants` refuse `RELAY_NAME` as a participant name, saying why
- [x] ⚠️ **The decision, stated rather than reasoned about: both comparisons are exact and
      case-sensitive.** So `Relay=<row>` is a legal, addressable participant and `WHO_REQUEST` is
      matched as the literal `"who"`. The hazard to avoid is the *mismatched* pairing — a
      case-insensitive refusal with an exact intercept would accept `Relay` and then never
      intercept it, leaving a participant that cannot be addressed
- [x] write a test that `relay=<row>` is refused
- [x] ⚠️ write the companion as a **near miss**: `relayed=<row>` and `Relay=<row>` must both be
      **accepted**, per the decision above. An "ordinary name still works" companion adds nothing —
      a blanket refusal would already fail ~15 existing roster tests, so it is not at risk
- [x] ⚠️ this is a **breaking** roster change (a name that parsed yesterday is refused today), so the
      CHANGELOG entry goes under `### Changed`, as Plan A's name-charset change did. Verified: no
      test in the repo uses `relay` as a participant name
- [x] run tests — must pass before Task 2

### Task 2: Compose the answer

**Files:** Modify `agb-peer`, `tests/test_agb_peer.py`

- [x] add `roster_answer(you, members)` → `you=alice peer=bob peer=carol`, peers sorted, `you` always
      present. Plain text: it is delivered as a message and read by an agent and a human
- [x] ⚠️ `peers = sorted(set(members) - {you})` — **`set(...)` is load-bearing.** `cmd_relay` passes
      `members=set(spec)`, but `relay_tick`'s `members=None` fallback substitutes `people`, which is
      a **dict**: `{'a': 1} - {'a'}` raises `TypeError`. `try_deliver` survives the same fallback
      only because it does `in` tests, which work on both
- [x] write a test that the answer names every member and marks exactly one `you`
- [x] write a test for a **one-participant** roster — `you=alice`, no peers, which `RosterReader`
      explicitly permits
- [x] write a test that a member not in the set does not appear, and that ordering is stable
- [x] ⚠️ **audit this task's tests now**, before the gate below: can each pass here, and is any already free
      on this branch? *(An earlier draft had a "present in `resolved` but not in `spec`" test here —
      unwritable, because `roster_answer` has no `resolved` parameter. It is a property of the
      caller and now lives in Task 3.)*
- [x] run tests — must pass before Task 3

### Task 3: The relay answers a `who` request

The whole mechanism. Every behaviour here has a placement that is easy to get wrong.

**Files:** Modify `agb-peer`, `tests/test_agb_peer.py`, `CHANGELOG.md`

- [x] ⚠️ **spell the `members is None` fallback that `try_deliver` already has** —
      `members = people if members is None else members`. `relay_tick`'s signature is `members=None`
      and ~40 test call sites omit it, so `roster_answer(sender, None)` → `None - {you}` →
      **`TypeError`**, killing the tick
- [x] ⚠️ **put the intercept INSIDE the `if deliver_new:` branch** of the drain loop. Above it, the
      relay answers a request that predates it *and delivers on the priming tick*, breaking the
      contract that a startup or joiner backlog is discarded. Inside, a stale request is discarded
      and named, which is correct
- [x] answer only when the text is `WHO_REQUEST`; anything else addressed to `RELAY_NAME` is
      **dropped with a line naming it** — this is what stops the reply loop
- [x] ⚠️ **queue it as `(RELAY_NAME, {"to": <asker>, "text": …, "id": message_id()})` — both fields
      matter.** `<asker>` is the pane the doorbell rang on. The tuple's *sender* must be
      `RELAY_NAME`, not the asker: `try_deliver:1616` drops a message whose `recipient == sender`,
      so signing it with the asker's name loses the answer permanently, with a line blaming the
      asker. The sender is also what `compose` turns into the literal `[chat from relay] ` prefix,
      which Task 5's loop mitigation and walkthrough checks 1 and 2 all depend on
- [x] give it a real `id` ⚠️ — `apply_leaves` prints dropped mail as `#%s (%s -> %s)`, so a `None`
      id logs `#None`
- [x] deliver through the ordinary `pending` path, inheriting the composer gate, the holding and the
      throttles with no new code
- [x] ⚠️ **`say()` when a request is answered.** `docs/cookbook.md` is a *"the relay's output is the
      diagnosis"* table, and every other decision this relay makes says so once; an answered request
      would otherwise produce no line at all and be indistinguishable from one never seen
- [x] write a test that a request is answered **and never routed to anybody** — the companion that
      stops "answered" passing against a relay that does both
- [x] write a test that the answer reaches the **asker**, with a second participant present that must
      not receive it
- [x] write a test that the delivered body starts with the literal **`[chat from relay] `** — the
      prefix three other sections depend on and that no task previously produced
- [x] write a test with **two askers in one tick**, proving `you` is per-pane and not a constant
- [x] write a test that a non-token message to `relay` is dropped and named — **the loop guard**
- [x] write a test that the request is **discarded, not answered, on the priming pass**
      (`deliver_new=False`)
- [x] write a test that `members=None` does not crash
- [x] write a test that the peer list comes from `members` with a `people` that **differs** — the
      property Task 2 could not express
- [x] ⚠️ note that a re-fetched request is answered once, via `notes["delivered"]`, and pin it
- [x] add the CHANGELOG entry
- [x] ⚠️ **audit this task's tests now.** *"An ordinary message is still routed"* is free — ~15
      existing tests cover routing — so keep it only as a labelled regression companion
- [x] run tests — must pass before Task 4

### Task 4: `agb-peer who` — the agent side

**Files:** Modify `agb-peer`, `tests/test_agb_peer.py`, `CHANGELOG.md`

- [x] add `cmd_who(run, out, env=None)` ⚠️ **with the same seams `cmd_send` has** — all twelve
      existing `cmd_send` tests inject `run` and `out`, and without them a test shells out to real
      tmux and, on the file fallback, writes into the developer's real `~/.agbridge`
- [x] send `WHO_REQUEST` to `RELAY_NAME` through the **`cmd_send` path** — no new transport, and the
      file fallback comes free
- [x] print that the relay was asked and that **the answer arrives as a message**, not here
- [x] print that **no answer means no relay is running, or this pane is not a participant**, and that
      neither is worth retrying
- [x] ⚠️ **ordering, or the refusal is unreachable:** resolve `env = os.environ if env is None else
      env`, check `TMUX_PANE` **itself**, refuse in `who`'s own words, and only then call
      `cmd_send(RELAY_NAME, WHO_REQUEST, run, out, env=env)`. Delegate first and `cmd_send:1341`
      raises *"send must run inside tmux"* — the phrasing leak this project already fixed once in
      `wait_ready`'s `retries=0` note, and what walkthrough check 9 marks as a failure
- [x] add `who` to `USAGE`
- [x] add the guard that does not exist today: **`USAGE` lists every verb `main` dispatches**, via
      `ast` over the peer tree alone. ⚠️ Not `assert VERSION == "0.3.0"`, which is the
      read-the-constant-back tautology this plan's own approach bans
- [x] write a test that `who` makes **no agtermctl call**, with the companion showing the fake would
      have recorded one
- [x] write a test that the request is addressed to `RELAY_NAME` with body `WHO_REQUEST`
- [x] write a test that `who` refuses outside tmux, asserting **its own** message
- [x] ⚠️ write the dispatch guard: patch `peer.os.environ` to a dict **without** `TMUX_PANE`, patch
      `peer.run_local` as a tripwire that must record nothing, call `main(["who"], io.StringIO(),
      None)` and assert **`who`'s own message** — which is what distinguishes a dispatched `who`
      from the undispatched `--to is required`. ⚠️ `main` has no `env`/`run` seam, so patching the
      module globals is the only route. *(The twelve `cmd_send` tests inject `env=` directly; that
      is not available here, and nothing in the repo patches `peer.os.environ` today.)*
- [x] add the CHANGELOG entry
- [x] ⚠️ **audit this task's tests now.** The `USAGE`-lists-every-verb guard **passes before this
      change too** (`send` and `relay` are already listed); its value is prospective and it is
      deliberately kept — do not delete it as free
- [x] run tests — must pass before Task 5

### Task 5: `skills/agb-peer/SKILL.md` — the trigger

⚠️ Not optional. An agent has no event loop; it acts on instructions. **`who` is dead weight unless
the skill says when to run it** — and the loop mitigation lives here too.

**Files:** Modify `skills/agb-peer/SKILL.md`, `tests/test_agb_peer.py`

- [ ] add an `agb-peer who` section — what it does, and that **the answer arrives as a prompt on a
      later turn**, not as command output
- [ ] say **when**: before addressing someone it has not talked to, and when a message arrives from a
      name it does not recognise
- [ ] ⚠️ **say a `[chat from relay]` answer is NOT a peer and needs no reply** — `## Receiving`
      currently says to reply to anything in that shape, which is what would start the loop. The
      relay's token match is the mechanism; this is the mitigation
- [ ] ⚠️ **say that no answer is not an error and must not be retried in a loop**
- [ ] ⚠️ extend the `(via file)` rule at `SKILL.md:44-69` to cover `who`: it sits under `## Sending`,
      is written about `send`, and shows `send`'s output — but walkthrough check 2 depends on the
      agent repeating the `[peer #…]` marker for a **`who`**. Same for `:101`, which names
      `agb-peer send` when it says "if it refuses, stop and say so"
- [ ] ⚠️ `test_the_skill_has_nothing_to_fill_in` asserts the literal `"ask the user"`
      (`tests/test_agb_peer.py:1033`); decide deliberately whether that phrase survives — `who` makes
      "do not guess" *cheap*, not obsolete
- [ ] ⚠️ extend `test_the_verbs_the_skill_names_are_dispatched` — but **not mechanically**. It is
      `pytest.raises(PeerError)`, and for `who` **both** the undispatched path (`--to is required`)
      and the dispatched one (no `$TMUX_PANE`) raise `PeerError`: extended as-is it passes today and
      passes with `cmd_who` deleted. Assert the **message**, or reuse Task 4's patched guard
- [ ] keep it agent-agnostic — both the Claude and Codex ends read this file
- [ ] run tests — must pass before Task 6

### Task 6: Documentation

**Files:** Modify `docs/design.md`, `docs/commands.md`, `docs/cookbook.md`, `CHANGELOG.md`

- [ ] extend §6 with the ask-and-be-told model, the token match, and **why publishing was abandoned**
      — three rounds, one root cause, no per-agent identity on the file transport. A future reader
      will otherwise reinvent it
- [ ] ⚠️ **delete `agb-peer who` from §6's *Deferred, with reasons*** (`docs/design.md:2452`)
- [ ] ⚠️ **replace** `docs/cookbook.md:770`'s *"An agent cannot yet ask who is in the chat"*, and
      **add two rows to the diagnosis table** at `:757`: the answered-request line, **and the
      drop line for a non-token message to `relay`** — the loop guard's only operator-visible
      evidence, and what walkthrough check 5 is read off
- [ ] give `who` a real entry in `docs/commands.md` beside `send` and `relay`; qualify the heading
      *"`agb-peer` — Mac, not installed by default"*, since `who` runs on the agent's machine
- [ ] record the limitations: the answer **costs a turn**; a lost request is **silent**; and the
      answer is composed at drain time, so it can be **held behind a busy composer** and arrive
      slightly stale — the same argument that refused a status snapshot
- [ ] run tests — must pass before Task 7

### Task 7: [Final] Version, counts, and filing

**Files:** Modify `agb-peer`, `CLAUDE.md`, `README.md`, `CHANGELOG.md`, `docs/plans/`

- [ ] bump `VERSION` 0.2.0 → 0.3.0, with a CHANGELOG entry saying why, as Plan A did
- [ ] correct the test counts in all four places: `CLAUDE.md:8`, `CLAUDE.md:697`, `README.md:317`,
      `README.md:329`
- [ ] update `CLAUDE.md`'s agb-peer paragraph and `README.md`'s verification table
- [ ] run the full suite — **2254** before this plan
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
| **0** | **Regression.** Plan A's Check 0, **plus an ordinary `pooled -> alice` send**. | both `delivered` | stop — this branch broke the roster. ⚠️ The pool send is here so a check-2 failure is attributable: a roster over the file transport is coverage nobody had before Plan A |
| **1** | Prompt `peer-a` to run `agb-peer who`. | it prints *asked the relay*; then a **later turn** brings `[chat from relay] you=alice peer=bob peer=pooled` | no reply — check the relay saw the doorbell |
| **2** | ⭐ Same on the **pool** agent. | ⚠️ it takes the **file** path and prints `[peer #…]`, which the agent must repeat on its screen; then the reply arrives | no reply: the marker never reached the visible screen — the failure `SKILL.md` exists to prevent |
| **3** | Add a participant, then have **`peer-a`** (not the new one) run `who`. | the new name is in the answer | stale — the answer is not built from the live spec |
| **4** | Remove one, then have **`peer-a`** run `who`. | it is gone | stale |
| **5** | ⭐ Have `peer-a` run the literal `agb-peer send --to relay 'thanks'`. ⚠️ Give it the command, not the intent — a compliant agent will otherwise refuse, and the check passes for the wrong reason. | the relay **drops it and says so**; **no second answer** | a second answer: the loop is live, and it will not stop |
| **6** | Two agents ask close together (raise `--interval` if you want them in one tick; the property holds either way). | each gets **its own** `you=` | both get the same name |
| **7** | `who` from a tmux pane that is **not** a participant. | *asked the relay*, then **silence** | anything claiming an answer |
| **8** | **Now** stop the relay; `who` again. ⚠️ Last, because nothing restarts it. | same silence | — |
| **9** | `who` outside tmux. | refused in **`who`'s own words**, naming `$TMUX_PANE` | it says *"send must run inside tmux"* — the wrong command's phrasing |

### Known limitations — not bugs

- **The answer costs a turn.** It arrives as a prompt on a later turn, never as command output.
- **A lost request is silent.** No relay, or not a participant, and nothing distinguishes them — by
  design, since neither is worth retrying.
- **The answer can arrive slightly stale.** It is composed when the request is drained and delivered
  when the composer is free, so a roster edit in between is not reflected — the same argument that
  refused a status snapshot, applied to membership.
- **No rooms**, and **no broadcast** (`--to all`): with three or more agents a reply-all is a
  feedback loop with no natural stop, which `SKILL.md`'s anti-deadlock rule does not cover.
