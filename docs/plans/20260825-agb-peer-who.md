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

### `you` is per-pane, including on the file transport

The relay writes **per pane**, so it knows which name that pane is. That is straightforward for a
tmux participant. For an NFS one it needs a key both sides know, and there is exactly one:

> **`$TMUX_PANE`.** `$TMUX` and `$TMUX_PANE` *are* inherited through job submission — this file's own
> transport comment records it: *"the agent believes it is `%99` of `/tmp/tmux-100000/default`"*. The
> **socket** is missing on the pool node; the **pane id is real**, and it is the pane on the
> reachable host. The relay computes the same value from `pane_argv_field(session["foreground"],
> "--pane")`.

So the NFS roster is **`<chat-dir>/roster.<pane>`**, one file per participant, and `you=` is present
on every transport. ⚠️ **Nothing is ever self-reported.**

⚠️ **An earlier draft of this plan got this wrong and it is worth recording.** It wrote ONE shared
file for all NFS participants — which cannot say which reader is which — and fell back to
`$AGB_PEER_FROM` for `you`. That fallback was dead: `AGB_PEER_FROM` is read at exactly one place
(`agb-peer:2075`), inside the **legacy** one-shot form; `cmd_send` never reads it and no launcher
exports it. Worse, `main` **refuses** `send --from` on the grounds that *"a message is signed with
the pane it was sent from, which is the only part of it that cannot be misstated"* — so a
self-asserted `you` was precisely what `send` had been hardened against. The per-pane file removes
the dependency instead of documenting around it.

⚠️ **The assumption this rests on, stated rather than discovered:** the row's `agb pane --pane` must
equal the agent's inherited `$TMUX_PANE`. It should, since the row was minted for that pane — but
say so in the docs, alongside finding 13.

### The startup publish is an NFS correctness fix

If an agent runs `who` before the roster file has ever existed, that `ENOENT` can be cached as a
**negative dentry for up to 60 s** (`acdirmin`/`acdirmax`; `agb doctor` prints exactly these on this
mount). Writing unconditionally at relay start makes every later update an *overwrite*, where
close-to-open consistency holds. Write **temp + rename**, like every other record here.

⚠️ And the publish path needs **`mkdir -p`**: `write_chat_file` calls `os.makedirs` before writing,
so at relay startup — precisely when this write matters — no sender may ever have created the chat
directory, and `tee` would fail with `No such file or directory`.

### An NFS leaver, now that the file is per-pane

Per-pane files make this simpler than the shared-file design allowed: a leaver's **own** file is
deleted outright, so its `who` goes blank like any other departed participant, and no other NFS
participant is touched. The shared-file limitation — *"a removed NFS agent still reads a valid
roster"* — no longer applies and has been removed rather than carried forward.

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

## What Plan A already built that this uses

Verified present on `main` before writing this:

| | |
|---|---|
| `valid_participant_name` / `NAME_ALPHABET` | `[A-Za-z0-9._-]` — what makes the encoded value ssh-safe **by construction** |
| `RosterReader`, `apply_leaves`, `prime_joiners`, `needs_prime` | the roster machinery this publishes from |
| `scan_participant` + the 7-outcome vocabulary | ⚠️ where the host/pane derivation now lives — see Task 3 |
| `drain(…, outcome=)` → `FETCH_OK` / `FETCH_FAILED` | the out-parameter pattern `publish` copies |
| `_throttled(say, notes, key)` | say-once-per-unchanged-reason, keyed on a **tuple** |
| `_name_notes(name)` | ⚠️ **must grow** if this adds a per-name note, and a test compares it to a literal |
| `try_deliver(…, members=)` | membership is the **spec**, never what resolved |
| `cmd_relay`'s tick ordering | read roster → leaves → `resolve_all` → prime → scan → deliver |

## Context (from discovery)

Re-verified against `main` at plan time, not taken from the review:

- ⚠️ **Finding 5 is worse than the review said.** `pane_argv_field` is now called at `agb-peer:1744`
  and `:1746`, **inside `scan_participant`, behind five returns** — `SCAN_NO_ROW`,
  `SCAN_UNREADABLE`, `SCAN_DETACHED`, `SCAN_NO_DOORBELL` and `SCAN_CAUGHT_UP`. The last of those
  fires on nearly **every steady-state tick**, so a targets map lifted from there is empty almost
  always. It must be built in its own loop.
- `_spawn(argv, timeout)` — **no stdin**, as the review said. Hardened in `44f436a`.
- The `fetch` seam is one positional argument, with **five** fakes (`tests/test_agb_peer.py:482,
  659, 1274, 1296, 1752`) plus **two** `run_local` monkeypatches (`:1131, :1138`).
- `test_the_verbs_the_skill_names_are_dispatched` proves a verb reaches the parser by asserting it
  **raises** — `peer.main(["send", "hi"], …)` fails for having no `--to`. The `who` case needs a
  different shape, since `who` takes no required flag.
- `test_the_file_drain_names_exactly_one_file` already uses the **denylist** form
  (`"$();|&<>*?"`) on path argv — the precedent finding 8 names.
- ⚠️ `conftest.functions(*trees)` raises on cross-file duplicates: **`statedir` and `main` both
  collide** between `agb` and `agb-peer`. Structural guards use the peer tree alone.
- ⚠️ `__pycache__/agb-peercpython-36.pyc` exists (dot-less). Mutation checks must delete it.
- Baseline **2254** tests.

## Development Approach

- **testing approach**: **Regular** (code first, then tests), matching Plan A and this file's history
- ⚠️ **No task may contain a test that cannot pass until a later task**, and **no test may assert a
  property already free on the branch it exercises.** All four of Plan A's review rounds were
  rejected partly for these two shapes; Task 11 audits for both.
- ⚠️ **Never read an expected constant out of the implementation.** Plan A hit this for real: a test
  read `_name_notes()` itself, so *shrinking* that list shrank the expectation and stayed green.
  Assert against a **literal**, and compare the literal to the implementation in a second test.
- **CHANGELOG entry in the commit that makes the change** — house rule
- backward compatibility: a relay started **without** `--chat-dir`, and every existing caller of
  `drain`/`_spawn`/`fetch`, must behave exactly as today

## Testing Strategy

- unit tests in `tests/test_agb_peer.py`; no e2e in this project
- structural guards via `ast`, **peer tree alone**
- mutation-check every new guard; delete `__pycache__/agb-peercpython-36.pyc` after writing mutated
  source; restore from an in-memory snapshot verified by `sha256`
- companions for every "nothing happened" test; non-vacuity assertions
- `timeout=` on every `communicate()`; Python 3.6.8 floor; `PANE_WORDS` untouched
- `python3 -m pytest tests/test_agb_peer.py -q`; full `python3 -m pytest tests/ -q` (**2254** before)

## Implementation Steps

### Task 1: A stdin seam in `_spawn`

⚠️ First, because it is the one change to already-hardened code (`44f436a`). Its only consumer is the
NFS publish's `ssh … tee` in Task 4 — so this task carries the seam and its own tests, and **does
not** touch the `fetch` fakes, which Task 4 does when it has a caller for them.

**Files:** Modify `agb-peer`, `tests/test_agb_peer.py`, `CHANGELOG.md`

- [ ] add `stdin_data=None` to `_spawn`: `stdin=subprocess.PIPE` **only** when it is not `None`, and
      `communicate(input=stdin_data.encode("utf-8"), timeout=timeout)`
- [ ] thread it through `run_local`; leave `run_ctl` alone — no caller needs it
- [ ] write a test proving stdin reaches the child (`cat` echoing it back)
- [ ] write a test proving the **kill-on-timeout** path still returns `TIMED_OUT` *with*
      `stdin_data` — 3.6's `_communication_started` bookkeeping is the easiest thing to break
- [ ] ⚠️ write the companion by **monkeypatching `subprocess.Popen` and capturing its kwargs**:
      `stdin` must be absent/`None` when `stdin_data is None`. From outside a process you cannot
      observe `Popen(stdin=…)`, so an assertion phrased any other way lands as something fragile
- [ ] add the CHANGELOG entry
- [ ] run tests — must pass before Task 2

### Task 2: Encode the roster, and the argv builders

**Files:** Modify `agb-peer`, `tests/test_agb_peer.py`

- [ ] add `ROSTER_OPTION = "@agbpeer_roster"` and `roster_file_name(pane)` → `roster.<pane>` — ⚠️ **no
      room in either** (finding 7): namespacing the key while `who` hardcodes `default` is
      namespacing with no reader tolerance
- [ ] add `roster_value(you, peers)` → `you=alice,peer=bob,peer=carol`, peers sorted, `you=` always
      present now that every transport has it. ⚠️ Single token, no whitespace, no shell metacharacter
- [ ] add `parse_roster_value(text)` → `(you, [peers])`, tolerant of an unknown key
- [ ] add `roster_set_argv(pane, value)` / `roster_unset_argv(pane)` for `ssh_argv`
- [ ] add `roster_mkdir_argv(chat_dir)`, `roster_tee_argv(chat_dir, pane)`,
      `roster_rename_argv(chat_dir, pane)`, `roster_rm_argv(chat_dir, pane)` — ⚠️ **`mkdir -p` is
      required**: `write_chat_file` calls `os.makedirs`, so at relay startup the chat directory may
      never have been created, and that startup write exists to beat a 60 s cached `ENOENT`
- [ ] ⚠️ decide and record how a pane id is spelled in a filename — `%` is not a shell metacharacter
      in `sh`, and in `tcsh` starts a job spec only as a word's first character, so `roster.%99` is
      safe; strip it to `roster.99` if you prefer not to rely on that. Say which, once
- [ ] write round-trip tests for `roster_value` ↔ `parse_roster_value`
- [ ] ⚠️ write **two** metacharacter tests (finding 8): an **allowlist** for the encoded value and
      the option key; a **denylist** (`"$();|&<>*?"`, matching `test_the_file_drain_names_exactly_one_file`)
      for the **path** argvs, which necessarily contain `/`. One allowlist rejects the file argvs
- [ ] mutation-check the allowlist by putting a space in the encoding
- [ ] run tests — must pass before Task 3

### Task 3: Derive the publish targets in their own loop

**Files:** Modify `agb-peer`, `tests/test_agb_peer.py`

- [ ] add `publish_targets(sessions, spec, resolved)` → `{name: (host, tmux_pane, is_nfs)}`.
      ⚠️ **Both inputs, and the rule is explicit** (finding 3): membership and any *explicit*
      `@host` / `:pane` come from **`spec`**; the row→session lookup goes through **`resolved`**,
      because `spec[name][0]` is a row-title *label*, not an id
- [ ] ⚠️ for an NFS participant, compute the pane with `pane_argv_field(session["foreground"],
      "--pane")` **even though the drain uses `NFS_TARGET`** — that pane id is the file key
- [ ] comment why this is a separate loop: the same derivation inside `scan_participant` sits behind
      five returns, the last being `SCAN_CAUGHT_UP`, which fires on nearly every steady-state tick
- [ ] a name absent from `resolved` yields **no target** and is reported as *owed*, unless its host
      and pane both came from `spec`
- [ ] write a test that a spec-explicit participant yields a target with **no session in the tree**
- [ ] write the companion: a derived participant with no session is **absent** and marked owed
- [ ] add a structural guard — **peer tree alone** — that `publish_targets` is **not** reachable from
      `scan_participant`; mutation-check it. ⚠️ This replaces an earlier "steady-state tick yields a
      full map" checkbox, which was **vacuous here**: `publish_targets` is a pure function and
      "everyone caught up" is a property of `seen`, not of its inputs. The behavioural form of that
      test lives in Task 5, where a tick exists
- [ ] run tests — must pass before Task 4

### Task 4: `publish_roster` and `unpublish_roster`

**Files:** Modify `agb-peer`, `tests/test_agb_peer.py`, `CHANGELOG.md`

- [ ] `publish_roster(fetch, targets, members, say, notes=None, chat_dir=None)` — ⚠️ **`notes` is
      required for throttling** (finding 8 of the review): `_throttled` needs it, and this loops over
      several names so it needs a **per-name** key. `notes=None` degrades to unthrottled, as
      `_throttled` already does
- [ ] ⚠️ `members` is the **roster spec**, never the resolved set: a row absent for a moment while
      `agb-refresh` re-mints it must not read as *left the chat* on everyone else's `who`
- [ ] ⚠️ `fetch = fetch or run_local` (finding 11) — `drain`'s docstring records that omitting this
      killed the first live relay
- [ ] branch as `drain` does: NFS → `mkdir -p` + `tee` + `mv` to `roster.<pane>`; otherwise the pane
      option through `ssh_argv`
- [ ] ⚠️ **report a non-zero rc from every leg**, throttled: `_spawn` raises only on a missing binary,
      so a failed `tmux set`, a `tee` into a missing directory or a lost `mv` race all return rc≠0
      **with no exception** — and the roster then goes stale while `who` prints old membership
- [ ] `unpublish_roster(fetch, target, say, notes=None, chat_dir=None)` — ⚠️ **takes ONE
      pre-resolved target**, not a name (finding 4): `apply_leaves` does `resolved.pop(name)` and its
      test asserts it, so by the time the leave path runs there is no binding left to look up. The
      caller captures the target **before** `apply_leaves` mutates anything
- [ ] unset the pane option; for NFS **delete `roster.<pane>`** — per-pane files make this clean,
      and no other participant is touched
- [ ] update the `Fetcher` and `FlakyFetcher` fakes for `stdin_data` — ⚠️ **only those two**
      (finding 9): `LocalRun`, `Failing` and `EmptyThenFailing` are `run` fakes for `cmd_send`, which
      keeps its one-positional contract
- [ ] write a test that a `@local` participant produces a **local** tmux call with no `ssh`, and a
      farm one produces an `ssh` call
- [ ] write a test that an NFS participant produces `mkdir` + `tee` + `mv` naming `roster.<pane>`,
      and **no** tmux call
- [ ] write a test that two NFS participants produce **two different files** — the per-pane key
- [ ] write a test that the peer list comes from `members`, with a resolved-set that **differs**
- [ ] write a test that a failing leg is reported **once across many calls**, with a companion that
      success is quiet
- [ ] write the test that exercises the `fetch or run_local` default
- [ ] add the CHANGELOG entry
- [ ] run tests — must pass before Task 5

### Task 5: Wire publishing into `cmd_relay`

**Files:** Modify `agb-peer`, `tests/test_agb_peer.py`, `CHANGELOG.md`

- [ ] ⚠️ **build `sessions` once at loop top** and pass it to priming, publishing and `relay_tick`
      (finding 10): `cmd_relay` has no such map today and calls `ctl.tree()` twice already. A third
      call per tick is both waste and a new failure mode — `Ctl.tree()` **raises `PeerError`**, which
      is unhandled at loop level and would kill the relay
- [ ] publish on the **first tick** unconditionally — ⚠️ *not* "before the loop": `resolve_all` runs
      inside it, so there is no `resolved` until then. Comment the NFS negative-dentry reason
- [ ] ⚠️ the publish gate is **`joined or left or rebound or owed`** (finding 5). A **rebind** leaves
      `set(spec)` unchanged, so it is *not* a membership change — but it points the name at a
      different pane, which carries no roster option. Plan A's own walkthrough check 7 is a rebind,
      so this is routine, not a corner
- [ ] ⚠️ store *owed* in a **plain set in `cmd_relay`**, not in `notes` (finding 14): `_name_notes`
      is purged by `apply_leaves` for `left` **and `rebound`** — and for a rebind you want owed
      **set**, not cleared. The two cases pull opposite ways and `_name_notes` cannot express it. Do
      **not** add a per-name note here
- [ ] capture departing targets **before** `apply_leaves`, then call `unpublish_roster` for each
- [ ] write a test that the first tick publishes even with an unchanged roster
- [ ] write a test that a **rebind** publishes to the new pane and unsets the old one
- [ ] write a test that a participant unresolvable at join is published to on a **later** tick with
      no second roster edit (the *owed* retry)
- [ ] ⚠️ write the companion at **`ticks=2`**, counting publishes on the **second** tick only
      (finding 15): a tick with no change and nothing owed publishes nothing. At `ticks=1` the
      unconditional startup publish makes this unwritable, and `once=True` — which every existing
      `cmd_relay` test uses — is exactly the form that fails
- [ ] ⚠️ give the new tests **per-row foregrounds** (finding 20): `RelayCtl.tree()` gives every row
      the same `FOREGROUND` (`--host buildbox01 --pane %7`), so a publish test over `PEOPLE` would
      address two participants at one pane and the second `set` would overwrite the first
- [ ] add the structural guard that `publish_roster` is reachable from `cmd_relay`; mutation-check
      it, deleting `__pycache__/agb-peercpython-36.pyc`
- [ ] add the CHANGELOG entry
- [ ] run tests — must pass before Task 6

### Task 6: `agb-peer who` — the agent-side read

**Files:** Modify `agb-peer`, `tests/test_agb_peer.py`, `CHANGELOG.md`

- [ ] add a `who` verb in `main`, beside `send` — needs **no agtermctl**
- [ ] ⚠️ **`who` PRINTS its state and exits 0 in all five cases; it does not raise** (finding 6).
      This is what "name five states" implies, and it is also what makes the dispatch guard in Task 8
      writable at all
- [ ] ⚠️ **resolve tmux through `tmux_binary(env)`** (finding 1 of the inherited list): `who` does not
      go through `ssh_argv`, so nothing rewrites `argv[0]`, and on a cluster host `/usr/bin/tmux` is
      2.7 — no `show-options -p`, answers `unknown option -- p`, then **blocks**
- [ ] ⚠️ give it injectable `run=` **and `env=`** seams as `cmd_send` has (finding 9), so a test is
      hermetic rather than reading the real environment — which differs depending on whether pytest
      was launched inside tmux
- [ ] read the option; on failure use `socket_is_missing()` — the existing **positive**
      discriminator — to decide the file fallback, never an error-string match
- [ ] on the file path, read `<statedir>/chat/roster.$TMUX_PANE` with `open()`, never `os.stat`
- [ ] ⚠️ **name five states**: published; nothing published (*no relay running* **or** *this pane is
      not a participant* — both, never one); `$TMUX_PANE` unset; `$TMUX` unset; **tmux reachable but
      would not answer, with the reason**
- [ ] ⚠️ **print the path or option it looked at, and the tmux binary it resolved** — the relay writes
      at `--chat-dir` and the agent reads at its own `statedir()/chat`, and a mismatch produces **no
      error anywhere**. Printing the tmux path also makes walkthrough check 4 a comparison rather
      than a stopwatch (finding 21)
- [ ] add `who` to `USAGE`
- [ ] write a test for **each** of the five states
- [ ] write a test that `who` makes **no agtermctl call**, with the companion showing the fake would
      have recorded one had it been made
- [ ] write a test that a stripped `$PATH` plus `$AGB_TMUX` is honoured
- [ ] add the CHANGELOG entry
- [ ] run tests — must pass before Task 7

### Task 7: Say where the relay publishes

⚠️ Reduced deliberately. An earlier draft **refused** at startup when an NFS participant existed with
no `--chat-dir`; that is a breaking change to behaviour that today merely complains per fetch
(`drain_files`), and the printed line below buys the same diagnosability for nothing (review's
over-engineering note).

**Files:** Modify `agb-peer`, `tests/test_agb_peer.py`

- [ ] say once at startup the **absolute chat dir** the relay will publish to, so it can be compared
      against what `who` prints on the far side
- [ ] warn once at startup — not refuse — if an NFS participant exists and `--chat-dir` is absent,
      naming the participant
- [ ] write a test that the startup line names the resolved absolute path
- [ ] write a test for the warning, with a companion that a roster with no NFS participant is quiet
- [ ] run tests — must pass before Task 8

### Task 8: `skills/agb-peer/SKILL.md` — the trigger

⚠️ Not optional. An agent has no event loop. **`who` is dead weight unless the skill says when to
run it.**

**Files:** Modify `skills/agb-peer/SKILL.md`, `tests/test_agb_peer.py`

- [ ] add a short `agb-peer who` section — what it prints, that it is local and instant
- [ ] say **when**: before addressing someone it has not talked to, and when a message arrives from
      a name it does not recognise
- [ ] ⚠️ **`test_the_skill_has_nothing_to_fill_in` asserts the literal `"ask the user"` is present**
      (`tests/test_agb_peer.py:1032`), so a revision that removes that phrase fails it and the
      failure reads as an unrelated regression. Decide deliberately: keep the phrase (`who` makes
      "do not guess" *cheap*, not obsolete) or amend the test and state the new invariant
- [ ] say that a blank answer means *no relay is running* **or** *this pane is not a participant*
- [ ] keep it agent-agnostic — both the Claude and Codex ends read this file
- [ ] ⚠️ extend `test_the_verbs_the_skill_names_are_dispatched` (finding 12). Note **there is no
      unknown-command path**: `main` falls through to `parse_args`, a bare word becomes
      `opts["message"]`, and it dies at `--to is required` — so `main(["who"])` **already raises
      today**. The guard must assert `who` reaches `cmd_who` (exit 0 with a known state under
      injected `env=`/`run=`), not that it fails to raise
- [ ] run tests — must pass before Task 9

### Task 9: Documentation

**Files:** Modify `docs/design.md`, `docs/commands.md`, `docs/cookbook.md`

- [ ] extend §6 with the pull model, publish-where-they-speak, **membership-only**, and the per-pane
      file key
- [ ] ⚠️ **delete `agb-peer who` from §6's *Deferred, with reasons* list** (finding 13) — it is no
      longer deferred, and a stale "named gap" is worse than none
- [ ] ⚠️ **replace** `docs/cookbook.md`'s *"An agent cannot yet ask who is in the chat"* (≈`:770`) —
      do not add a paragraph beside it
- [ ] ⚠️ `docs/commands.md`'s section heads **"`agb-peer` — Mac, not installed by default"**; `who` is
      the first subcommand that runs on the **agent's** machine over no ssh. Qualify the heading
- [ ] record the **deferred** list with reasons: rooms in any form including roster-key namespacing;
      broadcast (`--to all`); proactive join/leave announcements (they wake every agent and cost each
      a turn); per-participant `--chat-dir`
- [ ] ⚠️ record the **stopped-relay staleness** (finding 7): a pane option outlives the process that
      set it, and the design refused a timestamp on purpose — so there is no way for `who` to say a
      roster came from a dead relay. Stale membership reads as current, indefinitely
- [ ] ⚠️ record two stated assumptions: a `@local` participant's tmux target is a **session name**
      while `who` reads `$TMUX_PANE` (finding 13); and the row's `--pane` must equal an NFS agent's
      inherited `$TMUX_PANE`, which is what makes the per-pane file key work
- [ ] run tests — must pass before Task 10

### Task 10: Verify acceptance criteria

- [ ] verify every requirement in the Overview is implemented
- [ ] verify a relay with **no** `--chat-dir` and no NFS participant is unchanged
- [ ] verify all five `who` states are reachable and distinct
- [ ] ⚠️ **audit every task's tests for the two banned shapes**: a test that cannot pass until a later
      task, and a test asserting a property already free on its branch
- [ ] verify no publish complaint can fire unthrottled every tick
- [ ] re-run every mutation check, deleting `__pycache__/agb-peercpython-36.pyc` each time
- [ ] run the full suite — **2254** before this plan

### Task 11: [Final] Version, counts, and filing

**Files:** Modify `agb-peer`, `CLAUDE.md`, `README.md`, `tests/test_agb_peer.py`

- [ ] bump `agb-peer`'s `VERSION` 0.2.0 → **0.3.0** — `who` is a new verb
- [ ] ⚠️ add the guard that does not exist: nothing in `tests/` asserts `agb-peer`'s `VERSION` or that
      `USAGE` lists every verb `main` dispatches (finding 19). Add one, so Task 6's `USAGE` line and
      this bump are both held up
- [ ] correct the test counts in **all four** places (finding 22): `CLAUDE.md:8`, `CLAUDE.md:697`,
      `README.md:317`, `README.md:329`
- [ ] update `CLAUDE.md`'s agb-peer paragraph and `README.md`'s verification table
- [ ] run the full suite
- [ ] move this plan to `docs/plans/completed/`

## Post-Completion — the live acceptance walkthrough

⚠️ **Plan A's live run found FIVE defects and every one was in the documentation.** Check these
before blaming the code:

| trap | what it looks like |
|---|---|
| roster labels are **substrings** | `tx=tx` matches `tx`, `tx-old`, `tx-new` — session names must not nest |
| farm participants need `@<ssh alias>` | relay starts clean, then `fetch failed: ssh: Could not resolve hostname …` on the **first send** |
| `AGB_CLAUDE_CUSTOM` is set | `agb-claude` starts **no local agent** — it submits to a pool |
| `agb-claude -d` on an existing session | a **no-op** printing `attach with …`; the old agent keeps running |
| the farm shell is **tcsh** | a bash `<<'CHAT'` heredoc hangs at `?`. Pass the message as an argument. The Mac is zsh (no GNU `sed`); **agents run bash** |

### Setup — the cast is NOT Plan A's

⚠️ Plan A's cast has **no `:nfs` participant and no Mac-side one**, so three of the starred checks
below would have nothing to run against. Staging both is a prerequisite, not an aside.

```sh
# farm, one tcsh shell -- kill any same-named session first, -d is a no-op otherwise
unsetenv AGB_CLAUDE_CUSTOM
agb-claude -d peer-a
agb-claude -d peer-b
agb-tmux   -d sender

# a POOL agent: the launcher is what puts it there, so set it for THIS one only
env AGB_CLAUDE_CUSTOM='<your qsub line with {env} and {}>' agb-claude -d pooled
```

```sh
# Mac -- a local participant, addressed by SESSION NAME after the colon
tmux new -d -s macside
```

```sh
# Mac -- ~/peers ; every name is unique and none is a prefix of another
alice=peer-a@<alias>
bob=peer-b@<alias>
sender=sender@<alias>
pooled=pooled@<alias>:nfs
macside=<row label>@local:macside
```

```sh
agb-peer relay --roster ~/peers --interval 2 --chat-dir <statedir>/chat
```

Note the relay's startup line naming the **absolute chat dir** — checks 5 and 6 compare against it.

### Checks

| # | Do | Expect | Failure |
|---|---|---|---|
| **0** | **Regression.** Plan A's Check 0, unchanged. | `alice -> bob: delivered` | anything else — stop, this branch broke the roster |
| **1** | Prompt an agent to run `agb-peer who`. | its own name, and every other participant | its name missing, or a peer missing |
| **2** | Add a participant; re-run `who` in **another** agent. | the new name appears | stale — the on-change publish did not fire |
| **3** | Remove one; re-run `who` elsewhere. | the name is gone | stale |
| **4** | ⭐ **Repoint** a participant at a different row; run `who` in its **new** pane. | full membership | blank — a rebind is not a membership change, so the gate must include `rebound` |
| **5** | ⭐ `who` on a **farm** host. | instant, and it **prints the tmux path it resolved** | a ~30 s stall, or a path under `/usr/bin` — it found tmux 2.7 |
| **6** | ⭐ `who` on the **pool/NFS** agent. | membership **and its own `you` name** | no `you`, or nothing published — compare its printed path against the relay's startup line |
| **7** | ⭐ Restart the relay with a `--chat-dir` whose directory does **not** exist, then `who` on the pool agent. | it works — `mkdir -p` made it | nothing published: the startup write failed silently, and that write exists to beat a 60 s cached `ENOENT` |
| **8** | `who` in a tmux pane that is **not a participant**. | *no relay is running, **or** this pane is not a participant* — **both** | only one named |
| **9** | `who` outside tmux entirely. | `$TMUX_PANE` unset, named as its own state | folded into "nothing published" |
| **10** | `who` on the **Mac-side** `@local` participant. | membership | ⚠️ if blank, suspect the session-name/`$TMUX_PANE` assumption below |
| **11** | Remove the **pool** agent from the roster; run `who` there. | blank — its own file was deleted | still shows the old roster |

⚠️ **There is deliberately no "stop the relay, expect blank" check.** A tmux pane option **outlives
the process that set it**, and nothing here installs a shutdown hook — so after a relay that
published, `who` prints the last roster. A check expecting blank would fail against *correct* code
and send you hunting a bug that is not there.

### Known limitations — not bugs

- ⚠️ **A stopped relay leaves a stale roster on every pane, and `who` cannot tell.** The design
  refused a timestamp on purpose, so there is nothing for it to compare against. Stale membership
  reads as current, indefinitely.
- **No rooms**, in any form. Two relays sharing a pane overwrite each other's roster.
- **`--chat-dir` is relay-wide**, so two unreachable agents on two different mounts cannot both be
  served.
- **Two stated assumptions**, either of which shows up as a blank `who`: a `@local` participant's
  tmux target is a **session name** while `who` reads `$TMUX_PANE`, so an agent outside the
  session's active pane reads a different pane than the relay wrote; and an NFS agent's inherited
  `$TMUX_PANE` must equal its row's `agb pane --pane`, which is what makes the per-pane file work.
