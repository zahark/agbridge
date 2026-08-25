# agb-peer: `who` — letting an agent discover the chat

> **Plan B of two, and it depends on Plan A**
> ([`20260825-agb-peer-dynamic-roster.md`](20260825-agb-peer-dynamic-roster.md)), which owns the
> roster itself. Nothing here can be built until a relay has a roster to publish.
>
> ⚠️ **This plan has NOT been through a review pass.** Its parent was reviewed twice, and both
> rounds found six critical defects — most of them in exactly the publishing machinery this file
> describes. The design decisions below are settled and hard-won; the **task breakdown is not**.
> Run a review on this file before implementing a line of it, and expect the tasks to change.

## Overview

After Plan A, attaching an agent to a running relay changes where messages route and nothing else.
The agent cannot discover who is in the chat, or even **its own participant name** — today
`skills/agb-peer/SKILL.md` tells it to *ask the user*.

This adds `agb-peer who`: the relay publishes the membership to each participant, and the agent
reads it locally, instantly, with no network.

## Why this is a pull, not a push

An agent has **no event loop**. It is not a daemon and cannot watch a file. There are exactly two
ways it ever learns anything:

1. **it runs a command** — synchronous, agent-initiated. This is `who`.
2. **something types into its composer** — asynchronous, relay-initiated, and it **costs the agent a
   turn**.

So `who` reads a published value on demand, and ⚠️ **the trigger is a line in `SKILL.md`** — for an
LLM agent, behaviour is driven by instructions, not events. Without that edit the command is dead
weight. This is the same shape as the existing rule telling a sender to repeat its `[peer #…]` line.

Freshness comes free: the relay republishes on **every** membership change, so whenever the agent
looks it is current as of the last change. The agent only needs the answer at the moment it is about
to address someone.

## Settled design decisions

Carried forward from the brainstorm and two review rounds. These are the parts not to re-litigate.

### Publish where that participant already speaks

The same branch `drain` already makes (`if pane == NFS_TARGET: drain_files(...)`):

| participant | roster goes to |
|---|---|
| reachable tmux — farm **or** Mac | a pane option, via `ssh_argv`, which is already local/remote polymorphic |
| NFS participant (`:nfs`) | a file under `--chat-dir`, via `ssh … tee`, temp+rename |

This is one rule with two implementations, not two mechanisms. It is what lets a **Mac-side**
participant and a **farm** participant share a code path — `ssh_argv` drops the ssh for `local`.

### Membership only — no liveness, no timestamp

The roster is published **on change**, so any state baked into it (`here`/`busy`/`detached`) would be
a snapshot from an unrelated moment, read as current: the age-becomes-status trap this project
writes invariants against. A timestamp is refused for the same family of reasons — a heartbeat was
deliberately rejected, so nothing an age could say would be honest, and a number that cannot support
an inference invites the inference.

⚠️ **And the published peer list comes from the roster SPEC, never from what resolved.** A row is
routinely absent for a moment while `agb-refresh` re-mints it — `resolve_all`'s own comment records
this happening twice in one afternoon. Deriving the list from resolvable participants would make a
transient absence read as *"left the chat"* on every other agent's `who`. That is liveness leaking
into a membership-only roster, and it is the delete-everything shape in miniature.

### The value must survive `ssh … tmux set`

⚠️ **`ssh <host> <words…>` flattens argv into a string and hands it to the remote login shell**,
often tcsh — the lesson `list_argv`'s docstring already records. So the published value must be a
single token with no whitespace and no shell metacharacter. Plan A's participant-name charset
(`[A-Za-z0-9._-]`) exists partly to make that safe **by construction**:

```
room=default,you=alice,peer=bob,peer=carol
```

An earlier design published a multi-line, space-separated value and would have produced
`tmux set -p -t %7 @agbpeer_roster room default you alice …` on the far side. Its own
"no metacharacters" test could not have passed.

### `you` is per-pane where it can be, self-reported where it cannot

The relay writes per pane, so it knows which name that pane is. For an **NFS** participant one
shared file cannot say which reader is which, so `you` is omitted and `who` falls back to
`$AGB_PEER_FROM` (which already means "how my messages are signed"), printing
`you <name> (self-reported)` — **labelled**, never confused with something the relay confirmed.

### The startup publish is an NFS correctness fix

If an agent runs `who` before the roster file has ever existed, that `ENOENT` can be cached as a
**negative dentry for up to 60 s** (`acdirmin`/`acdirmax`; `agb doctor` prints exactly these on this
mount). Writing unconditionally at relay start makes every later update an *overwrite*, where
close-to-open consistency holds. Write **temp + rename**, like every other record here.

⚠️ And the publish path needs **`mkdir -p`**: `write_chat_file` calls `os.makedirs` before writing,
so at relay startup — precisely when this write matters — no sender may ever have created the chat
directory, and `tee` would fail with `No such file or directory`.

### An NFS leaver is a named limitation, not a bug to fix

Leave **rewrites** the shared file without the leaver — it must not **delete** it, because the
remaining NFS participants read that same file. The consequence: a removed NFS agent still reads a
valid roster and has no reliable way to know it is not in the list. Comparing `$AGB_PEER_FROM`
against the peers would rest on an env var the operator set, so **this is documented, not solved.**

## Findings from the review of the combined plan

Every one of these was a real defect in the version of this work that was reviewed. They are
recorded so the next author does not rediscover them.

1. ⚠️ **`who` must resolve tmux through `tmux_binary()`.** It runs on the agent's machine and does
   *not* go through `ssh_argv`, so nothing rewrites `argv[0]`. `tmux_binary`'s docstring records the
   measured failure: on a cluster host `/usr/bin/tmux` is **2.7**, has no `show-options -p`, answers
   `unknown option -- p` and then **blocks trying to start a server of its own** — *"which read as
   'tmux is wedged' for three rounds of diagnosis."* `cmd_send` guards against this; a naive `who`
   would hit it, on the hosts most agents live on.
2. ⚠️ **A failed publish must be reported.** `_spawn` raises `PeerError` only on `OSError` from
   `Popen` (a missing binary). A remote `tmux set` that fails, a `tee` into a missing directory, an
   `mv` that loses a race — all return a **non-zero rc with no exception**. A `try/except PeerError`
   catches none of them, and the roster then goes stale with no diagnosis while `who` confidently
   prints old membership.
3. ⚠️ **There are five states, not two.** `socket_is_missing`'s docstring records **three** measured
   tmux failure modes, and only one may fall back to a file: Codex's sandbox (`Operation not
   permitted`), a wedged agterm client (a timeout), and the pool case (`No such file or directory`).
   Add `$TMUX_PANE` unset and `$TMUX` unset and the honest enumeration is: published; nothing
   published (*no relay running, **or** this pane is not a participant* — both, never one);
   `$TMUX_PANE` unset; `$TMUX` unset; **tmux reachable but would not answer, with the reason**.
   Folding the last into "nothing published" renders the two most diagnosable failures as the one
   state the agent is told not to retry.
4. ⚠️ **`--chat-dir` is used in the reverse direction for the first time, and a mismatch is silent.**
   Today the agent *writes* `statedir()/chat/<id>.msg` and the relay *reads* `--chat-dir/<id>.msg`,
   so a mismatch fails loudly on the relay. For the roster the relay *writes* at `--chat-dir` and the
   agent *reads* at its own `statedir()/chat` — a mismatch produces **no error anywhere**, only
   `who` reporting nothing. They must resolve to the same directory, and **`who` should print the
   path it looked at** so the mismatch is diagnosable.
5. ⚠️ **The targets map is not where it looks like it is.** The ssh host and tmux pane are derived
   per participant from the session's `foreground` argv — but in `relay_tick` that derivation sits
   behind four `continue`s, the last being *"the doorbell did not change"*. Lifting it from there
   yields a map that is empty on almost every tick. It must be built in a **separate loop over
   `sorted(people)`**, before the scan.
6. ⚠️ **Publishing must not be gated on membership change alone if any target was held.** A
   participant whose row is briefly unresolvable is skipped; if publishing only fires on membership
   change, it is skipped **forever**. Either retry while any target is owed (the `FeedState.owed`
   shape in `agb`) or re-resolve before publishing. Note that a participant given explicitly as
   `bob=<row>@host:%9` or `pool=<row>@container:nfs` needs **no** resolved row at all — both the
   host and the target come from the spec — so holding those is over-broad, and it is the NFS
   participant that it holds unnecessarily.
7. ⚠️ **Room namespacing on the roster key is NOT worth taking now.** Putting the room in
   `@agbpeer_roster_<room>` prevents two relays sharing a pane from overwriting each other — but the
   agent is told nothing about rooms and has no `--room` flag, so `who` would hardcode `default`
   anyway. That is namespacing with no reader tolerance: the *opposite* of how the message wire is
   treated, from a mechanism introduced specifically to make rooms work. A `room=` field inside the
   value is then redundant with the key as well. **Defer both.** Note Plan A's tolerant message
   parser was cut for the same class of reason — adding a *reader* later breaks nothing in either
   direction, and rooms get implemented in the relay, so a room-aware sender can never face a
   room-unaware relay. When rooms are built, reader and emitter land together.
8. ⚠️ **A metacharacter test needs two rules, not one.** An allowlist for the encoded **value** and
   the option **key**; a **denylist** (no whitespace, no ``$();|&<>*?'"` ``) for **path** arguments,
   which necessarily contain `/`. The existing `test_the_file_drain_names_exactly_one_file` uses the
   denylist form for exactly this reason. A single allowlist rejects the file argvs and the task's
   headline test fails on first write.
9. ⚠️ **`who` needs an injectable runner**, as `cmd_send(recipient, text, run, out, …)` has —
   otherwise every test must monkeypatch `run_local`, and the "makes no agtermctl call" companion is
   awkward to write.
10. ⚠️ **A `stdin` seam is required in `_spawn`** for `ssh … tee` — `Popen` currently passes no
    stdin, and the helper was deliberately hardened in `44f436a`. Widening it also changes the
    **`fetch` seam**, whose contract is one positional argument, which means updating **every** fake
    in `tests/test_agb_peer.py` — the `Fetcher` class, the ad-hoc lambdas, **and the `run_local`
    monkeypatches**. That is a test-file-wide change and belongs in its own task.
11. ⚠️ **`publish_roster` needs `fetch = fetch or run_local`.** `drain`'s own docstring records that
    omitting this default killed the first live relay: every test injected a fake, so the production
    path was the one thing nothing exercised. Write the test that exercises the default.
12. ⚠️ **Extend `test_the_verbs_the_skill_names_are_dispatched`** to cover `who`. Five SKILL.md
    guards already exist; without extending that one, a `who` renamed in `main` and left in the skill
    sends every agent to `unknown command` with nothing noticing — which is the exact failure that
    guard's docstring describes. The task's **Files** must include `tests/test_agb_peer.py`.
13. **Open question, worth one line of documentation rather than a live discovery:** a `@local`
    Mac-side participant's tmux target is documented as a **session name** ("tmux resolves a session
    name to its active pane"), while `who` reads `$TMUX_PANE`. If the agent is not in the session's
    active pane, the relay sets the option on a different pane than `who` reads. The same assumption
    already underpins message drain, so it may be acceptable — but say so deliberately.

## Sketch of the work

⚠️ **Deliberately coarse.** Turning this into task-level checkboxes is what the review pass is for.

- **The `_spawn` stdin seam and the `fetch` contract**, including every test fake. Its own task, done
  first — it is the one change to hardened code and it touches the whole test file. (Finding 10.)
- **Roster encoding and argv builders** — the space-free token, the pane-option set/unset, the
  `mkdir -p` + `tee` + `mv` triple. Two metacharacter rules. (Findings 8.)
- **`publish_roster` / `unpublish_roster`** — the `drain`-shaped branch, targets built in their own
  loop, the peer list from the spec, non-zero rc reported, `fetch or run_local`, NFS leave rewriting
  rather than deleting. (Findings 2, 5, 6, 11.)
- **Wiring into the relay** — unconditional publish at startup, republish on membership change *and*
  while any target is owed. The relay-survival guard belongs **here**, not in the task that defines
  `publish_roster`, because nothing calls it until this point.
- **`agb-peer who`** — `tmux_binary`, injectable runner, `socket_is_missing` for the fallback, five
  states, print the path it looked at, `$AGB_PEER_FROM` labelled self-reported. (Findings 1, 3, 4,
  9.)
- **`skills/agb-peer/SKILL.md`** — the trigger, plus extending the verb-dispatch guard. Not optional:
  without it the command is dead weight. (Finding 12.)
- **Docs** — `docs/design.md` §6, `docs/commands.md`, CHANGELOG entries in their own commits, and the
  named limitations: the NFS leaver, the `@local` session-vs-pane assumption, and per-participant
  `--chat-dir`.

## Post-Completion

Live verification, none of it reachable from the suite:

- run `agb-peer who` on a farm agent, a **Mac-side** agent, and a **pool/NFS** agent — the third is
  the one the `tee` path exists for, and the only one where `you` is self-reported
- confirm the roster file is on the pool node's mount *before* the agent first asks — i.e. that the
  startup publish actually defeats the negative-dentry window
- confirm `who` on a farm host does not hit the tmux 2.7 decoy
- add and remove participants mid-conversation and confirm `who` tracks it on every transport
