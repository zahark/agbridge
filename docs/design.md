# agbridge — detailed design

Companion to the architecture overview in [`../README.md`](../README.md). This file covers the
parts below the transport: what gets written, how a session becomes a row, and what happens when
things break.

> **This document describes what was built, not what was planned.** It was written before the code
> and was wrong on several points; it has been reconciled against the implementation and is the
> authority. Where a rule was withdrawn, the reason is kept rather than the conclusion alone — a
> rule without its reason gets re-litigated.
>
> Four design-level rules were superseded during implementation, in one line each:
>
> 1. **Liveness is proven, never inferred.** §2's Mac-side aging table is withdrawn for *all*
>    states and for silent *hosts*.
> 2. **There is no `unknown` status.** §4 Rule 1's "distinct unknown treatment" is `idle` + a `[?]`
>    title prefix, and **feed death is its only trigger**.
> 3. **`beat` is `.state`'s mtime**, not a JSON field, and `.state` carries five bare lines, not one
>    word.
> 4. **Reaping a quiet host is an operator action** (`agb doctor` → `agb prune`), not an automatic
>    one.
>
> And two implementation-level supersessions with their own sections below: the tool is **three**
> files, not one (§5), and reclamation is **`agb close-done`**, a separate short-lived command (§3).

## The topology this document assumes

Three roles, referred to throughout by these names:

| Name | What it is |
|---|---|
| **the Mac** | your workstation, running agterm and the `agb bridge` launchd job. It never reads the shared directory — it only ever sees the feed's stdout. One job **per instance**: a machine that shares no disk with the first is a second bridge into the same sidebar (§5, *One Mac, several instances*). |
| **box #2** | a cluster host you can `ssh` to directly. The Mac's single long-lived `ssh` runs `agb feed` here, so this host's agents get sub-poll-interval liveness. |
| **machine #3** | any *other* cluster host that shares the same statedir but has **no** `feed` running — typically reachable only by hopping through box #2. Its agents are visible because they write to the same shared directory, but nothing there proves their liveness between hook invocations. |

"machine #3" is shorthand for that third case throughout: **a host nothing can currently speak
for**. It is the hardest case in the design and most of the liveness rules exist because of it.
A fourth machine costs nothing — it needs the shared mount and the hooks, and that is the point.

## 0. Command surface

| Command | Runs on | File | Frequency |
|---|---|---|---|
| `agb hook <state>` | farm | `agb` | **hundreds per session** — the hot path |
| `agb feed <mac-id>` | farm (box #2) | `agb` | one long-lived process per bridge connection |
| `agb bridge` | Mac | `agb` → `agb_mac` | one long-lived launchd job **per instance** (§5) |
| `agb close-done` | Mac | `agb` → `agb_mac` | operator, on demand |
| `agb pane <key> …` | Mac | `agb` → `agb_ops` | one per row click |
| `agb doctor` | farm | `agb` → `agb_ops` | operator, on demand |
| `agb prune` | farm | `agb` → `agb_ops` | operator, rare — the only destructive command |
| `agb status-line` | farm | `agb` → `agb_ops` | every tmux `status-interval` |
| `agb install-hooks` | farm | `agb` → `agb_ops` | once per host |
| `agb install-config` | both | `agb` → `agb_ops` | once per host |
| `agb version` | both | `agb` | once per install, by machine |

`version` is in the table because it is **load-bearing, not cosmetic**: it prints `agb <VERSION>` on
stdout and two installers depend on that exact answer. `install.sh`'s `verify_tree` refuses to
configure anything against a tree whose `version` does not answer `agb <something>`, and
`install-hooks` probes the interpreter it is about to bake into a hook command by running
`<python> -S -E <agb> version` and requiring `agb <VERSION>` back — a hook command is written only
once it has been *run*, because a broken one fails before `agb` starts and leaves no breadcrumb at
all. Changing what `version` prints breaks both.

Per-command flags and their defaults are in [`commands.md`](commands.md); `agb <command> --help` is
not implemented.

## 1. State model

### statedir layout

The statedir lives on a **cluster-shared** volume (`~/.agbridge` by default, overridable by
`$AGB_STATEDIR` or the `statedir` config key). It is created mode **0700** and its ownership and
mode are verified if it already exists, in case its parent is group-writable.

```
<statedir>/
  sessions/<host>/<key>.json     record; temp + rename()
  sessions/<host>/<key>.state    five bare lines; written IN PLACE; MTIME IS THE BEAT
  idx/<host>-<spid>-<tag>        minted key + agent pid + starttime
  gen/<host>.marker              newline-separated LIVE KEY LIST + `#end <count>`; temp + rename()
  bridge/<mac-id>.beat           touched by feed each poll; written in place
  sweep/<host>.marker            mtime gates the 60 s hook-sweep throttle
  err/<host>.<key>.log           per-session breadcrumbs, bounded ~64 KB
```

### The owning host is in the *path*

`sessions/` was flat in the first draft of this document, with `host` readable only *inside* the
record. That made every marker rebuild — which runs on every transition, every proving sweep, and
in `prune` — need a `readdir` **plus an `open()` of every file across every host** just to filter
to its own. Worse, it was a correctness hole: a peer hook inside its in-place `O_TRUNC` window
leaves `.state` momentarily zero-length, so a concurrent rebuilder cannot attribute that key to any
host, silently omits it, and the feed then emits `remove` for a live agent.

With `sessions/<host>/`, a rebuild is `readdir(sessions/<own_host>/)` — **zero opens, no ambiguity,
no dependence on file contents** — and "a host may only sweep its own entries" becomes structural
rather than a runtime check. (The runtime check is kept anyway; see §2.)

### Two files per session

**`sessions/<host>/<key>.state`** — the hot-path file. Five bare lines, no JSON:

```
active                          <- state
worker01       <- host (redundant with the path; kept for readers holding a path)
48213                           <- agent pid, or `-`
9182736                         <- agent starttime (/proc/<pid>/stat field 22), or `-`
47                              <- seq
```

One `open()` yields state + host + pid + starttime + seq and — via `fstat` on the same fd — the
**beat**. This is what keeps the hot path, the sweep and the feed's poll loop free of `import json`.

**`sessions/<host>/<key>.json`** — the record, read only when `seq` moves:

```json
{
  "v": 1,
  "key":   "a3f9c1e0",
  "label": "build",
  "host":  "worker01",
  "pid":   48213,
  "starttime": 9182736,
  "tmux":  "build",
  "pane":  "%24",
  "cwd":   "/shared/work/project",
  "state": "blocked",
  "seq":   47,
  "updated": 1753716123.4
}
```

`state` is one of `active` | `blocked` | `completed` | `idle`.

Three things about this schema are corrections to the first draft:

- **`beat` is not a field.** It is `.state`'s mtime, stamped by the NFS server, and the feed
  synthesizes it into the wire record. A stored `beat` would have to be written by somebody's
  wall clock and compared on another machine; an mtime written with `os.utime(path, None)` is
  stamped by the server, which eliminates skew between writer hosts for free.
- **`agent` is dropped** — one value, no consumer.
- **`pane` is carried**, and `starttime` with it. Two agents in two panes of one tmux session share
  `label`, `host`, `cwd` **and** `tmux`; without `pane` their rows are visually identical and
  `agb pane` cannot select between them. `starttime` is the pid-reuse guard (see §2).

**Agent-produced `idle` was dropped.** The hooks map to `active|blocked|completed` only
(`AGENT_STATES`); a `SessionStart`→`idle` producer would paint a fresh session with agterm's *no
glyph* rendering — pixel-identical to a `[done]` row and to a stale `[?]` row — for the cost of a
fifth hook on every session start. The full vocabulary (`STATUS_VOCABULARY`) still has four words,
because the **bridge** emits `idle` for the stale and `[done]` renderings; it is simply not
something an agent reports.

### `key` is minted, never derived

`agr` keyed on the tmux session name. That is precisely what let two sessions collide onto one row
(see README). A random key minted once at session start is never reused, survives tmux renames, and
collides only within the bound below. `label` carries the human meaning and is free to change at any
time.

**The key is a GLOBAL identity, and nothing anywhere qualifies it by host.** `feed_poll` keys both
its probe set and `FeedState.entries` on the bare key across every host it discovers, the wire's
`remove` carries a bare key, and the Mac's row map is a bare-key → row bijection. So one collision
is not a local nuisance: two live agents on two hosts that shared a key would produce **one** upsert
and one row, and the second agent would be invisible to the bridge entirely — no row, no
notification, nothing to notice it by. On one host it is worse: `bind_key` checks `EEXIST` on the
**anchor**, never on `sessions/<host>/<key>.state`, so two agents would share one `.state` and one
`.json`, and the sweep would adjudicate one of them using the other's pid.

**Hence `KEY_BYTES = 8` — 64 bits, not 32.** The arithmetic, for a statedir holding `n` live keys
and `m` mints over its lifetime, is `m·n / 2^b`. At 32 bits and a deliberately pessimistic farm
(`n` = 500 concurrent, `m` = 10⁵ mints) that is `5·10⁷/4.3·10⁹` ≈ **1.2 %** — small, but a
1-in-86 chance of a silently invisible agent is not a bound worth carrying for a system whose entire
purpose is to remove silent failure. At 64 bits the same numbers give `2.7·10⁻¹²`. The width was
raised rather than the collision detected at mint time because it costs **nothing**: `valid_key`
already accepts up to 64 hex characters, so keys of the old width stay valid, no file layout
changes, and a mint-time `stat` would defend a 10⁻¹⁹ event on the one path that has to stay cheap.

The key is owned by an **identity anchor**, `idx/<host>-<spid>-<tag>`, resolved in three tiers by
how much each can prove:

1. **tmux** — `(host, tmux server pid, %PANE)`. `$TMUX` is *session*-level
   (`<socket>,<server pid>,<session id>`), so the pane has to come from `$TMUX_PANE` or two agents
   in two panes of one session would be one anchor, one key and one row. Only this tier is
   attachable.
2. **plain ssh (machine #3)** — `(host, agent pid, p<starttime>)`. Distinct per agent process, and
   marked as having **no attach target** (`tmux` and `pane` both null in the record) rather than
   discovering that at attach time. The tmux *session name* is resolved once, at mint time, by
   shelling out to tmux; when that fails the record keeps its `pane` and the reason is
   breadcrumbed — the row then attaches on the pane id alone (§3), rather than degrading silently
   and for ever because the value is only ever computed on the mint. The binary is taken from
   `/proc/<tmux server pid>/exe` rather than a bare `tmux`, because a hook's `$PATH` may not resolve
   one (constraint #14) — with two corrections that a real upgrade forced: the kernel appends
   `" (deleted)"` to that link once the running server's binary is replaced, a basename that still
   passes the name check and a path that cannot be exec'd, so the suffix is stripped and the result
   must actually be executable; and when it is not, the weaker `$PATH` answer is taken rather than
   none at all. One that usually works beats a path that provably cannot.
3. **neither** — the session leader, `(host, getsid, s)`. Nothing here can be attached to, and it
   stores no pid, so no sweep may touch it: `agb prune` is its terminal path.

**The agent pid is resolved by walking the `PPid` chain**, not by reading `PPid` itself. Claude runs
hooks through a transient `sh -c` that dies immediately; storing *that* pid would make every entry
provably dead microseconds after it was written — the delete-everything bug, one level up. The walk
climbs `/proc/<pid>/status` until `comm` or `argv[0]`'s basename is `claude` or `node`, bounded by a
step limit and a seen-set so a looping `/proc` cannot hang the hot path. If nothing is identified,
**no pid is stored** and every sweep skips the entry.

**Minting is `os.link(temp, idxpath)`**, not `O_CREAT|O_EXCL`: the latter is atomic *creation*, not
creation-with-content, so a loser could read an empty file. With `link()` the content is already in
the temp, so `EEXIST` resolves by simply reading the file.

**On `EEXIST`, the recorded agent pid + `starttime` are validated and the key is re-minted if they
do not match.** tmux pane ids are never reused within a server, so the anchor is stable across
*successive* agents in one pane — and without this check, agent 2 in a pane whose agent 1 died would
inherit agent 1's key, contradicting "a minted key is never reused" and rebinding a row the bridge
had already marked `[done]`.

### `updated` and `beat` are separate

- `updated` — the last state **transition**, in the *writer's* clock. Written only when `state`
  changes. It stays in the writer's clock deliberately: `doctor` compares it against the
  server-stamped mtime to measure per-host skew.
- `beat` — proof of life, in the **server's** clock. Refreshed at most every 15 s by whoever can
  prove the agent is alive.

Without the split you must choose between hammering NFS on every `PostToolUse` (Claude fires that
constantly) and being unable to distinguish *"blocked 3 seconds ago"* from *"blocked, then the
machine died"*. With it, the bridge can put **"4m"** in the row title — the number that actually
matters when triaging five agents. Note *title*, not *state*: see §2.

### Writes are transition-gated

The hook reads its own previous state and skips the write when unchanged. `agr` relayed on every
`PostToolUse`; here the common case is two `open()`s and no write. The comparison covers `pid` and
`starttime` as well as `state`, because a first hook that could not resolve the agent records `-`
and a later one that can must repair it, or the entry is permanently unsweepable. `host` is
deliberately not compared — it is in the path.

### A short read is "no information"; `ENOENT` is proof

The rule that makes the whole thing survivable is in §5 (*Torn reads are never data*), but one half
of it belongs here because it is the only thing that ever removes a session from the wire:

- A `.state` that does not parse as exactly five lines, or a marker whose `#end <count>` sentinel is
  missing or mismatched, is **discarded and the previous value retained**. Never "empty", never
  "gone".
- `ENOENT` from `open()` on a `.state` is different: it is a **positive server answer** that the key
  is gone. Keys are random, so no cached negative dentry can manufacture a false `ENOENT`. Without
  this carve-out, a key listed in a marker whose `.state` has been unlinked — a normal transient of
  non-atomic unlink-then-rebuild — would be retained forever and *no tool could clear it*, since
  `prune` works from entries and this key has none.
- The **marker's** `ENOENT` is *not* proof, and the asymmetry is the point: a marker's name is not
  random, so the argument above does not cover it — and it does not need to, because removal is
  proven per key, by name. Reading a vanished marker as "this host has no sessions" would emit
  `remove` for every row on the host at once.
- "No information" is a rule about **what the tool decides by itself**, and it can be a *permanent*
  condition: `write_in_place` opens `.state` with `O_TRUNC`, so a hook killed inside that window
  (SIGKILL, OOM, ENOSPC, a host reset) leaves a **zero-length** `.state`, and if its agent is gone
  nothing can ever parse it again. The sweep skips it, the feed retains it and withholds every
  snapshot's removal authority for as long as it exists (§5), and it used to be refused by `prune`
  as well — leaving a file **no command in the tool could clear**. `prune --key <host>/<key>` is now
  the one path that may remove it (§2); no derived list ever offers one.

## 2. Liveness — proven, never inferred

**Decision: no daemons on any farm machine, and no aging anywhere.**

### The withdrawn backstop

The first draft answered "what if no agent ever runs on host `H` again?" with Mac-side aging on
`beat`: dim at 60 s, clear and unbind at 10 minutes. **That table is withdrawn in full.** Aging
cannot distinguish "dead" from "busy for twenty minutes" or "waiting on you", and every attempt to
find a state where it *is* safe failed in turn — first `blocked`, then `active`, then a
"host silent > 10 min ⇒ `idle`+`[?]`" variant that was the same inference in a third location. On
any host without a running `feed` (machine #3 always; box #2 whenever the bridge is down) the only
beat source is hooks, so a `blocked` agent waiting on you emits nothing and would be repainted at
the 10-minute mark while it is perfectly alive.

Beat age is therefore **surfaced in the row title and never converted into a state** (§3). ⚠️ That
is what `row_fields` is dropping if it drops `beat`: not a decoration but this invariant's
compensation, so a sidebar configured without it keeps the refusal to guess and loses the number
offered in its place. The cost
is that entries on a quiet host have no automatic terminal state; that debt is paid by `prune`,
below.

### The single proof

One three-valued predicate, in one place:

```
liveness(pid, starttime) -> DEAD | ALIVE | UNKNOWN
```

Three-valued because "provably dead" and "not provably alive" are different answers, and collapsing
them into a boolean is exactly the mistake that turns a sweep into a delete-everything bug.

- `kill(pid, 0)` → `ESRCH` — nothing holds the pid. **DEAD.**
- `starttime` mismatch — something holds the pid but started at a different moment, so the pid was
  reused and our agent is gone. **DEAD.**
- `EPERM` — the pid exists but belongs to someone else. Suggestive of reuse, not proof of it:
  **UNKNOWN**, unless the recorded `starttime` positively disagrees.
- `pid < 2` — `kill(0, 0)` addresses the caller's whole process group and a negative pid addresses a
  group by id. Neither is a question about this entry. **UNKNOWN.**
- no pid recorded, or `starttime` unavailable — **UNKNOWN.**

`proof_of_death` and `proof_of_life` are the named predicates over it. **Only `DEAD` may unlink
anything**, and every unlink in the tool traces to `proof_of_death` → `reap_entry`. A structural
test enumerates the functions allowed to unlink a session, so a second copy of this logic cannot
grow silently.

### Who runs the proof

Two callers, one implementation (`sweep_entry`):

1. **The feed**, every poll, for entries where `host == own_host()`. This is what makes box-#2
   liveness independent of hooks firing — the feed is the only agb process that runs *continuously*,
   so a blocked agent or a twenty-minute build stays provable. Adjudication happens inside the poll,
   right after the `.state` read, so a dead entry never reaches a **snapshot**.
2. **The hook**, on the transition path only, throttled to once per 60 s per host by
   `sweep/<host>.marker`'s mtime. This is the only agb code that ever runs on machine #3, which has
   no feed: without it every row #3 created would be permanent and `agr` failure mode #3 would be
   reproduced exactly.

On proof of death the entry's `.json` is unlinked **before** its `.state` (a crash between the two
leaves a record-less `.state`, which the feed already degrades gracefully; the other order leaves an
orphan `.json` nothing ever reaps), then `gen/<own_host>.marker` is rebuilt from `readdir` — from
the *directory*, never from an in-memory list, because two unlocked writers would otherwise each
write the list they knew about and the second would drop the first's key.

On proof of life the beat is refreshed (throttled to `BEAT_INTERVAL`, 15 s, against the mtime the
caller already read — no extra round trip).

**Everything else is skipped, never unlinked.** An unresolvable pid, a `.state` that fails
validation, an `EPERM` without a contradicting starttime: absence of evidence fails safe.

The sweep's **own-host precondition is a `raise`, not an `assert`** — `assert` is compiled out by
`python -O`, and the one guard standing between this tool and another host's live sessions must not
be an interpreter flag away from vanishing.

### The host you *are* and the host you *say* — `own_host()` is not adjudicable

That precondition compares the entry's host against `own_host()`, and `own_host()` honours
`$AGB_HOST`. So for its whole life it was written in terms of a value the caller supplies, and the
override it most needed to notice sat on **both sides of the comparison**. It could not fail.

That is not theoretical. Measured 2026-08-25, on this project's own farm:

```
12:30:01.895  a remotely launched agent's first hook writes its state       (adopts its row: correct)
12:30:01.908  reaped: agent pid 3326337 ... is gone      ┐
   ...        nine more                                  │  eleven live agents on a machine
12:30:01.941  reaped: agent pid 425636 ... is gone       ┘  this process had never been on
12:30:01.943  sweep: dropped idx <host>-406994-%0  (key is gone)
   ...        twelve more, including its own anchor
12:30:03.900  minted key ...-%106                        ← the duplicate row
```

One `agb hook`. `agb-claude`'s `{env}` had set `AGB_HOST=<container>` so the agent — running on a
batch node, via `qsub -Is` — would report the row it belonged to. `own_host()` duly returned the
container, `maybe_sweep` swept `sessions/<container>/`, and `os.kill(pid, 0)` answered about the
batch node: `ESRCH` for every one of that container's live agents, which `liveness()` reads as
positive proof of death. `sweep_idx` then asked whether tmux server pid `406994` was alive, got the
same answer, and unlinked every anchor — including the impersonating agent's own, which is why the
hook two seconds later found none and minted a second row.

Proof the reaps were false rather than merely unlucky: pid `1409148` was reaped at 11:34:02, and at
11:41:36 a **new key was minted recording the same pid**, which only happens when `resolve_agent()`
walks the ppid chain and finds that process alive.

The survivors say the same thing from the other side. Exactly the pid-less records lived — the ones
`{env}` had written with `AGB_AGENT_PID=none` — because `liveness(None, …)` is `UNKNOWN`, and
`UNKNOWN` is skipped. Every record carrying a real pid was destroyed. The three-valued verdict held
perfectly; it was answering about the wrong machine.

**The fix is to stop conflating two questions that had shared one function:**

| | question | overridable |
|---|---|---|
| `own_host()` | what name do my entries get written under? | **yes** — an *identity*, and a remote agent must be able to assert one |
| `real_host()` | whose pid namespace can I interrogate? | **no** — an *observation*, `uname` only |

`host_is_observed()` is where they meet, and both destructive paths consult it: `maybe_sweep`
returns before any I/O, and `_require_own_host` — which guards `reap_entry`, the single unlink
authority — refuses with a message naming the machine it is actually speaking from.

⚠️ **The opt-in points the way it does on purpose.** `AGB_HOST_LOCAL=1` re-enables adjudication for
an override that really does name this machine; there is no flag for the reverse. A process cannot
tell "I am standing in for another host" from "I have been renamed" by looking at itself — the test
suite's simulated hosts and a `{env}` agent are byte-identical from the inside — so an explicit
statement is unavoidable, and it must be the *dangerous* direction that has to be typed. Saying
nothing gets you the safe answer. The suite's `set_host` fixture sets it; `{env}` deliberately does
not.

⚠️ **Constraint #11 is therefore narrower than it read.** "Only the owning host may sweep its own
entries" was enforced by a name comparison, and a name is not evidence. What it always meant, and
now says, is: *only a host whose pid namespace backs those entries may adjudicate them.*

### The rest of the sweep

The same 60 s pass also reaps, all own-host only and all after the session pass (so "does this key
still exist?" includes everything just proven dead):

- **`idx/`** anchors whose key no longer exists or whose liveness anchor is dead — `server_pid` for
  tmux anchors, agent pid + `starttime` for the plain-ssh ones. Two fail-safes that are not
  cosmetic: an anchor whose recorded agent is **provably alive** is kept whatever else is true of
  it, and an anchor younger than 60 s is kept unconditionally. `link_idx` creates the idx file
  microseconds before the first `.state`, so inside that window the key genuinely does not exist —
  and dropping it there hands a live agent a second key on its next hook, i.e. two rows for one
  agent. The grace is a **race guard, never evidence**; proof still authorises the unlink.
- **`err/<host>.*.log`** whose session is gone, after 24 h — because the last line written to one is
  usually the reap itself, and "why did this row disappear?" has to stay answerable after it did.
  `err/<host>.-.log`, the breadcrumbs of invocations that never had a key, is never reaped.
- **orphan `*.tmp.*`** older than an hour, in the three directories that get temps. The own-host
  filter here is on the *name* (`<name>.tmp.<host>.<pid>.<rand>`), because `gen/` holds every host's
  marker and this is the one place a sweep could otherwise reach across hosts.

### The gap, and the operator path that replaces aging

If a host goes quiet with live entries — `kill -9` on machine #3, or powering it off mid-session —
**nothing reaps them automatically**. That is the acknowledged cost of withdrawing aging, and the
answer is an operator action rather than a heuristic running unattended:

- **`agb doctor`** lists them as **unadjudicable** entries: entries on a host this machine cannot
  speak for, whose `sweep/<host>.marker` mtime is old. Quietness is defined as *that marker's*
  mtime — the closest thing to a per-host liveness signal in the layout; `.state` mtimes and the
  `gen/` marker each give a different answer. A host with no sweep marker at all counts as quiet: it
  is the least adjudicable case there is.

  "Old" is **`DOCTOR_QUIET_AFTER` = 900.0 s (15 minutes)**, and both commands take
  `--quiet-after <seconds>` to move it (`doctor --quiet-after`, `prune --quiet-after`; the same
  default in both, from the same constant, because a `prune` that offered entries `doctor` had not
  listed would be a second derivation of the heuristic). It is deliberately generous: the marker is
  refreshed by *any* hook transition on that host, so 15 quiet minutes means nobody there has
  changed state in 15 minutes — which is a question, never an answer. `doctor --tail <n>` is the
  unrelated knob next to it: how many lines of each `err/` breadcrumb log to print, default
  `DOCTOR_TAIL` = 3.
- **`agb prune`** removes them, under **per-entry** confirmation, displaying `state`, beat age,
  `host`, `cwd` and `pane`, with an explicit warning when `state == blocked` that this may be a live
  agent waiting for input — printed *before* the prompt, not after a decision has been made. EOF is
  a **no**: the absence of an answer is not consent.

**The word is "unadjudicable", never "orphaned" and never "provable".** This is an age heuristic on
a host nothing can currently speak for, not proof of death, and naming it as proof is precisely what
would invite a destructive `--force` and re-create the bug amendment 1 removed. `--force` is not
merely absent — it is **rejected with the reason**, because the next person who wants one will type
it before they read the file. `--yes` exists and is refused unless at least one explicit
`--key <host>/<key>` was given: consent has to be about something specific. The rule is enforced
down to the string literals — a test bans "orphaned" and "provable" from every literal in `agb_ops`.

Two further safety rules `prune` needs and the shared sweep helper cannot give it:

- **Never remove an entry whose pid is alive by proof on the pruning host.** The asymmetry is the
  point: `proof_of_life` *refuses* a removal, `proof_of_death` would *authorise* one, and for a
  foreign entry the positive question can only be answered by coincidence. `proof_of_death` is not
  reachable from `prune` at all.
- **For a foreign host, derive the new marker by subtracting the pruned keys from that marker's own
  last-read content — never from `readdir`.** Own-host authority does not extend to foreign markers:
  a `readdir` of `sessions/#3/` taken on box #2 can be `acdirmax=60` stale, silently dropping any #3
  key created in the last minute ⇒ `remove` on a live agent ⇒ and then #3's next transition restores
  it as an unbound new key, minting a duplicate row. A marker that fails validation is left exactly
  as it stands. Foreign `idx/` anchors are not touched either, for the mint-race reason above.
- **`prune --via-ssh <host>`** re-issues the confirmed entries on the owning host, where
  `kill(pid, 0)` is meaningful. That is the only way to turn this heuristic into a proof.

**`--key` may remove an entry whose `.state` cannot be read; the derived list may not.** This was
decided against once and the decision was wrong. The argument for refusing — "a peer mid-`O_TRUNC`
looks exactly like a corrupt file" — is a defence against **automatic** removal, and it keeps that
job: `unadjudicable_entries`, the one derivation the tool has, still skips an unparseable `.state`,
so no list *this* machine derives can put one in front of you (`--via-ssh` re-reads on the far side,
which is the one place a key you did not type can land there — first bullet below). But a
zero-length `.state` whose agent is
dead (§1) is permanent, and refusing it by name too meant *nothing in the tool could clear it* while
the feed went on withholding every snapshot's removal authority because of it — a row adopted from
the persisted map after a launchd restart could then never be reclaimed, which is `agr` failure mode
#3 rebuilt inside the tool that exists to remove it. `--key <host>/<key>` is precisely the named,
human-gated path this design built for entries nothing can adjudicate, so it is the one that carries
this. Three conditions, all of them tested:

- It is reachable **only** through `--key`. Locally that means a name typed on the command line.
  `--via-ssh` is the one exception and it is not a loophole: it re-issues entries a human confirmed
  one by one as `--key … --yes` on the owning host, that host re-reads, and a `.state` readable here
  can be unreadable there — so the block is printed *after* the decision on that hop (streamed back
  over ssh) rather than before it. What decides it there is the pid check below, in the pid namespace
  the hop exists to reach.
- **The proof-of-life rule still runs**, on the pid the `.json` still carries. Both files are written
  in one transition from one identity, but `.state` is rewritten in place with `O_TRUNC` while
  `.json` goes through temp-and-rename — so an unreadable `.state` says nothing about the `.json`
  beside it, and that file names the same pid. This is the branch that needs the rule *most*: a torn
  `.state` is what an agent writing one looks like. A `.json` that is missing or pid-less is "no
  proof", never an authorisation, and only then is the operator's answer unopposed. The block says
  which of the two happened, and shows the `.json`'s state and pid — the same fields the tool
  decided on.
- The **prompt itself** carries the risk, not just the block: `--yes` skips the *prompt*, never the
  block, which is printed for every entry either way — but a `--yes` typed against a name is still
  a decision about that name, and a prompt reading like any other would leave the risk only in the
  scrollback.

`label`/`cwd`/`pane` come from that same `.json` read — they are what an operator recognises the
entry by, and they matter most here, where everything `.state` would have said is missing.

### Clock domains

Every age in the system is a subtraction, and the two operands must come from the same clock.

- `beat` is set with `os.utime(path, None)` — **never** explicit times — so the NFS **server**
  stamps it. Skew between writer hosts is eliminated rather than measured.
- The feed's `now` comes from `fstat` of the `bridge/<mac-id>.beat` fd it just wrote, so both sides
  of every age comparison on the wire are server-stamped. Free: the write already returns that stat.
- `doctor` plays the same trick with its atomicity probe's read-back, and says so in a `clock` line
  if the probe failed and the ages that follow are the local host's.
- The Mac's own clock is used for exactly one thing: the bridge's watchdog, which is a local timeout
  on `time.monotonic` and is unrelated to anything on the wire.
- The **one** deliberate cross-clock comparison is the hook's beat throttle (writer's `time.time()`
  against the server-stamped mtime). It is safe because it is a rate limiter and can never remove
  anything, and a future-dated mtime counts as due, so a badly skewed pair cannot freeze a beat
  forever.

⚠️ Skew is still worth reporting even though no threshold now depends on it: `agb doctor` measures
it per host by comparing a record's NFS **mtime** (server clock) against its in-file `updated`
(writer's clock) — which catches skew even when nothing is future-dated.

## 3. Row binding

**The Mac owns the mapping.** The bridge holds `key → agterm row id` in a local file on the Mac
(`~/.config/agbridge/rows`, beside the config and never under the statedir the Mac cannot read) and
enforces one invariant:

> The map is a **bijection**. A row is bound to at most one key, ever.

"Beside the config" is the literal rule rather than a description of one path: the map lives in the
directory of **whichever config the bridge was started with**, which is what makes a second instance
a second bijection with no second concept — see §5, *One Mac, several instances*. The same is true of
the `placements` file below.

`agr` kept this mapping on the *remote*, where nothing could invalidate it — so a row reused for a
new project left a dangling file pointing at it. Here, binding a key to an already-bound row is not
a conflict to be resolved; it is not expressible: `bind()` **refuses** rather than moves, and both a
known key and an already-owned row id are errors.

Labels may collide freely (two agents both called `build`); they get separate rows, disambiguated
by `host`, `cwd` and `pane` in the title.

**The map is persisted, and that has two consequences worth naming.** The bridge is a launchd job
that restarts; an in-memory map would mint a second row for every live agent each time. So:

- **`BridgeModel.adopt()`** seeds the model from the map at startup. Without it, a row whose agent
  ended while the bridge was down is never reclaimed — the first snapshot's resync can only remove
  sessions the model already knows about, and a fresh process knows none. That is `agr` failure
  mode #3 rebuilt out of the persisted map.
- **`save()` merges the on-disk copy first**, because `close-done` is a separate process by design
  and a bridge that wrote its own memory over the top would resurrect every entry `close-done` had
  just closed. The merge and the write are **one critical section**, held under an `flock` on a
  `rows.lock` sibling: merging without holding the lock to the write only narrows the window. The
  interleave *bridge merges → close-done renames → bridge renames* is a plain lost update, and it
  does **not** heal — `_merge_disk` drops an entry only when disk no longer has it, and the bridge
  has just put it back, so it is re-adopted from then on. What survives is a `[done]` entry naming a
  row agterm already closed: `close-done` can only keep failing on it, and no command clears it.
  `flock` rather than an `O_EXCL` lock file because the kernel releases it when the holder dies; the
  lock file itself is created once and never unlinked, since unlinking it is what re-opens the race.
  No lock available (no directory yet, a filesystem that refuses one) degrades to the unlocked
  write — a map that is not written at all is worse than the old window.
- **The merge prefers the DISK copy for a key this process never edited** (`RowMap.touched`, the
  sibling of `dropped`, cleared by a successful `save`). The lock makes one process's
  merge-and-write atomic; it does nothing about the time *between* reads, and `close-done` reads the
  map once and then spends an `agtermctl session close` — seconds — per row. Holding a copy is not
  the same as having an opinion: every `[done]` the bridge records in that window is an entry the
  reclaimer holds as `bound` and never touched, and writing it back means the row renders `[done]`
  on screen while `close-done` reports "no [done] rows to close (1 still bound)". That one *does*
  heal on the bridge's next map write, but the next write on an idle farm — exactly when this
  command gets run — can be never. Ours still wins for a key we did change: an unsaved
  `bind`/`unbind`/`rebind`/`set_title` is newer than anything on disk.

The map is written **temp + `rename()`**, like every other file whose *content* is the data. It used
to be written in place so that "the Mac side has no unlink and no rename authority at all" stayed an
unqualified structural rule — but that rule is about the **shared statedir**, where the Mac has no
removal authority (constraint #11), and this is a Mac-local file under `~/.config` whose only temp
it creates itself. Weighed against that, a torn in-place write loses the whole key ↔ row bijection:
orphan rows the bridge no longer knows about, keys whose row is gone, and an `agb close-done` that
silently closes nothing. The structural guard is narrowed accordingly — `atomic_write` is exempted
for exactly one function, `RowMap.save`, **by name**, and the test asserts it is exactly one — and
the companion guard forbidding every statedir helper is untouched. A torn map is still discarded on
read (the `#end` sentinel is the canary), which costs a duplicate row and never a wrong close.

Its lines are `kind<TAB>key<TAB>row<TAB>title`, under an `agbridge-rows 2` header and the same
`#end <count>` sentinel the marker uses. The **fourth field is the last title painted** for that
row, and it is there because `adopt()` above is only half the restart story: `RowRenderer.seen` is
per-process, so a fresh bridge knows a row's id and nothing about its identity — and the `[done]`
rename that follows the first snapshot would replace `build · box2 · /shared/x · %24` with the
raw hex key. Reading is **backward-tolerant**: an `agbridge-rows 1` header and a three-field line
parse with an empty title rather than being discarded, and the next `save()` rewrites the file as
version 2.

When there is neither a record nor a remembered title — a version-1 file, or a row bound by a bridge
that died before its first rename — the rule splits on **whether a marker is being applied**:

- **no prefix** (a tick, or the feed coming back): the rename is refused. It would be cosmetic, and
  replacing `build · box2 · /shared/x · %24` with a hex string is a pure loss.
- **`[?]` or `[done]`**: the row is renamed to its **key**. The marker is the whole point here, and
  the `idle` that accompanies it lands either way — it needs no identity — so a refused rename would
  leave the row *pixel-identical to a live idle agent*, which is the exact failure the two markers
  exist to prevent. A hex key is a poor label; a row that lies is worse, and this one self-heals on
  the next upsert (whereas the lie does not self-heal at all: nothing is dirty, so the map is not
  rewritten, and a tick skips rows it has no record for).

The stand-in is never *remembered* as the row's identity — writing the key into the map would make it
permanent. Reclamation is unaffected either way, since it is `done_entries()` that `close-done`
works from.

### Rows are created by the bridge, not by you

You never run an adopt command, and agents need not be started from inside agterm. Start Claude in
a VNC terminal on #2, or over ssh on #3 — a row *appears* on the Mac. This directly fixes README
finding #3: under `agr`, agents launched inside the VNC desktop got no binding at all, because
`agr open` requires `AGTERM_SESSION_ID`.

### The title carries what the vocabulary cannot

`<label> · <host> · <cwd> · <pane> [· <beat age>]` **by default** — `row_fields` chooses which of
those render and in what order — with a prefix of `[?]` or `[done]` when either
applies.

The beat age appears **only once the beat is late** (≥ 2 × 15 s), bucketed. A beat is refreshed
every 15 s by whoever can prove the agent is alive, so an always-on age would repaint every row on
every tick to say nothing.

For that to work the model has to emit a heartbeat: **`BRIDGE_OP_TICK`**. Without it the renderer
repaints only when a session changes, so a `blocked` agent on machine #3 — which beats nothing,
because there is no feed there — would display the age it had at its last transition forever. That
is the dashboard-that-lies failure in miniature. Only rows whose rendered title actually changed are
repainted, so the tick stays cheap.

### Unbinding leaves the terminal; reclamation is a separate command

On `remove`, the bridge unbinds the key, sets the row to `idle`, and marks the title **`[done]`**.
It does **not** close the row.

It does, however, **bind the row back** if the feed positively re-asserts that key. `[done]` was
never proof an agent had finished — it is what a `remove` renders, and a `remove` also comes from a
snapshot that could not read the key (see the wire protocol below) and from `agb prune`, which the
tool documents as *expected* to hit live agents. A positive upsert outranks that earlier absence:
this is constraint #8's rule — removal needs proof, absence is not proof — applied on the Mac side.
Refusing it left a live `active` agent rendering idle + `[done]` for ever, recoverable only by `agb
close-done`. The row **id never moves** (`RowMap.rebind` returns the entry to the row it already
had), so the bijection is untouched and `bind()` still refuses a key it has seen.

Rows are therefore never *auto*-closed, which means the sidebar would grow monotonically for every
agent ever run. Reclamation is **`agb close-done`**:

```sh
agb close-done [--rows PATH] [--config PATH] [--dry-run]
```

a **separate short-lived command**, not `agb bridge --close-done`. Both the row map and `agtermctl`
are local to the Mac, so it needs no IPC with the running bridge — and a `bridge` subcommand would
start a **second long-lived launchd-owned bridge** just to close some rows. A row is forgotten only
if `agtermctl` says it was closed; otherwise it stays in the map and is printed as "close by hand",
which is also the recorded degradation if `session close` turns out not to exist.

**"Bound rows are never touched" is checked twice** — once against the map this process loaded, and
once against the map **on disk immediately before each close**. `close-done` reads the map once and
then spends a subprocess per row inside `agtermctl`, and the bridge is a second writer that can
`rebind` a `[done]` row to a live agent at any moment (see above). Closing that row would take a
live agent's pane away, and `session close` does not undo. A rebind found on disk is followed in
memory as well, or `save()` would write this process's stale `done` back over the bridge's `bound`
and orphan the row. An unreadable map answers "not rebound", like every other no-information case
here: the entry already read stands.

Note that the first draft's "keep the pane, you want the scrollback" rationale **does not apply
here**: the Mac-side row runs `agb pane`, which only prints identity and waits. There is no agent
scrollback in it to preserve — the scrollback is on the farm, in tmux, untouched.

### Rows are status-only; attach is on demand

**Decision: no idle connections.** A row is a local placeholder process, not a held ssh.

Note the constraint that forces this shape: **the Mac cannot read the shared NFS dir.** It only
ever sees the feed. So a row cannot render live remote state by itself. (Structural tests assert
that nothing reachable from `bridge`, `close-done` or `pane` touches the statedir — such code
compiles and passes every farm-side test, then hangs or lies on the only machine that runs it.)

The row's command, set at `session new` time, is the one line connecting the two halves:

```sh
<python> -S -E <agb> pane <key> --host <host> [--tmux <session>] [--pane %N] [--jump <jumphost>]
```

The interpreter is spelled out because `agb` has no shebang and is not executable (see §5) — a bare
`agb pane` row command would simply never run.

`agb pane` prints the agent's identity, then prompts
(`[enter] attach   [s] split   [d] drawer   [q] quit > `), and attaches only if you ask it to:

```sh
ssh -t [-J jump] <target> \
    'tmux select-window -t %N ; tmux select-pane -t %N ; exec tmux attach-session -t <session>'
```

**`s`, `shell` or `split` opens agterm's split pane** beside this one and starts a plain login
shell on the same host, in the agent's own directory:

```sh
agtermctl session split on --target active
agtermctl session type  --target active --pane right "ssh -t [-J jump] <target> 'cd <cwd> && exec \$SHELL -l'\n"
```

Both panes belong to **one session and one sidebar row** — agterm's own model, not something this
tool invents. Three properties of that pair are load-bearing, and all three come from
`--help` output recorded in [`agtermctl.md`](agtermctl.md) rather than from assumption:

- **`on`, not the default `toggle`.** `[s]` can be pressed twice, and a toggle would close the pane
  the second time.
- **The split must exist before anything is typed into it.** `--pane right` is an error otherwise,
  and `--select` — the flag that realizes a never-shown session — is documented as *main pane only*.
  Hence two calls in a fixed order, the second skipped if the first fails.
- **`--target active` needs no row id.** This command is running *inside* the row's own session:
  the human clicked it to get here, so the session wanting the split is by definition the active
  one. No rows-map lookup, and therefore no dependency on `agb_mac` from `agb_ops`.

That last point is also why the split is **offered rather than opened automatically**. A row nobody
has clicked is a never-shown session whose split pane cannot be realized — and splitting every row
at creation would open one idle ssh per agent.

**`d`, `drawer` or `scratch` puts the same shell in agterm's scratch drawer**, which overlays the
pane instead of taking width from it:

```sh
agtermctl session scratch on --target active
agtermctl session type    --target active --pane scratch "ssh -t [-J jump] <target> 'cd <cwd> && exec \$SHELL -l'\n"
```

Same shape, same fixed order, same non-fatal failure. **Both keys stay** because neither subsumes
the other: the split can show you the agent and the shell at once, which is exactly what an overlay
cannot do; the drawer costs no width, which is exactly what a split cannot avoid.

Three things about the pair are worth stating, because each looks like an oversight:

- **The two openers are duplicated, not parametrised.** They differ in two constants and one noun.
  The reason not to merge them is that they are expected to **diverge**: `session scratch` takes a
  `--command` that `session split` has no equivalent for, so the drawer may yet collapse to a single
  call. A shared function would make that change awkward; two make it local.
- **`--command` is not used**, though it would remove the keystroke injection and the shell-quoting
  under it. Its help says it *"respawns the scratch if one is already open"* — so a second press of
  `[d]` would destroy a shell in use. Typing into the existing shell nests an ssh instead, which
  `exit` undoes, and which is what `[s]` has always done. A shrug rather than an incident.
- **`--pane scratch` requiring the scratch to exist first is ASSUMED**, not observed, and is
  unobservable here — `scratch on` always goes first, so nothing can falsify it. It is kept on the
  split's precedent and costs nothing if the constraint does not exist.

`shell` remains a synonym for the **split**, not the drawer. The prompt's label moved from `shell`
to `split` when the second pane arrived — both hold shells now, so the pane is the distinction — but
the word keeps its old meaning, or someone typing it out of habit silently gets a different pane
than they got yesterday. A test pins it, along with the rule that the three key-word sets stay
pairwise disjoint: the dispatch matches on membership in order, so an overlap makes a branch
unreachable with no error anywhere, and `shell`/`split`/`scratch` all begin with `s`.

**`q`, `quit`, `exit` and EOF leave without attaching**, exit 0, and print that nothing about the
agent's state was changed — a row command whose stdin is not a terminal therefore ends instead of
spinning on a prompt nobody can answer. Anything else attaches. A **non-zero ssh exit re-prompts**
rather than ending the row: ssh failing because the farm is briefly unreachable is not a reason to
close a row, and the human is standing right there.

Five details, each measured rather than assumed:

- **The pane id alone is a usable attach target**, so `<session>` above is really `tmux or pane`.
  Measured on tmux 3.5a on this box: `attach-session -t %2` attached a client to the session that
  owns `%2` (confirmed through `list-clients`), and `-t %99` failed with `can't find pane: %99`
  rather than silently landing elsewhere. This matters because `resolve_tmux_session()` runs **once,
  at mint time**, shells out to tmux, and returns None whenever that fails — so a hook whose
  environment could not run tmux produced a record with a live `pane` and a null `tmux`, and the row
  was permanently status-only for an agent that *was* in tmux. The session name is still preferred
  when there is one: it survives the pane's death, and a pane id does not. Only a record with
  **neither** is status-only, which is exactly identity tiers 2 and 3.
- **`select-window` as well as `select-pane`**, superseding the first draft. On tmux 3.5a,
  `select-pane -t %N` makes the pane active *inside its own window* but leaves the session's active
  **window** where it was — so two agents in two windows of one session would both attach to
  whichever window was last active, the exact failure `--pane` exists to prevent. `select-window`
  accepts a pane id.
- **Select before attaching, not after.** After `attach-session` the client is attached and the
  command has not returned, so nothing following it runs until the human detaches.
- **`;` rather than tmux's own `\;` sequence.** The string is re-split by the remote login shell, so
  a backslash-semicolon would have to survive two quoting layers — and with `;` a select that fails
  because the pane is gone still falls through to the attach.
- **`subprocess.call` in a loop, not `exec`**, superseding the first draft's `exec ssh -t`.
  `os.exec*` would replace the row's process with ssh, so `C-b d` — the ordinary way to leave a tmux
  session — would end the row's command and take the terminal with it. In a loop, a detach returns
  to the prompt and re-attaching is one keypress. `os.exec*` appears nowhere in the tool.

When there is **no tmux target** (a tier-2 or tier-3 anchor, i.e. a plain-ssh agent on machine #3),
`pane` prints identity and stops — no prompt either, because a row that cannot be attached to must
not offer to attach. `--pane` **without** `--tmux` is accepted rather than refused: an earlier
refusal was wrong, because that is a record this tool really does write (`resolve_tmux_session()`
returns None whenever `$TMUX` is unset or `tmux display-message` cannot answer, while the pane id
survives from `$TMUX_PANE`), and refusing the pair made the auto-created row's command exit 1 — a
row that says nothing at all. It takes the no-tmux path above, pane id included in the identity.

### Machine #3 routing

The Mac cannot reach machine #3 directly. The record already carries `host`, so the bridge puts a
jump-host hint in the row command and `pane` maps the hostname to an ssh target:

- `host_<name> = <ssh-target>` config keys, because a record's `host` is a hostname, not an ssh
  alias.
- `--jump` (the bridge's hint) wins over the config's `jump_host`, which is the fallback for a
  hand-typed invocation and is dropped when it names the target itself. The bridge withholds the
  hint for sessions on the host the feed is itself on — attaching to box #2 *through* box #2 is an
  extra hop for nothing.
- Every word that reaches an ssh command line is validated against a whitelist, **including a
  leading-`-` rejection**: every character of `-oProxyCommand=…` is already in the whitelist, and
  `pane` makes that word reachable from a config key and from a row command, where ssh would read it
  as an option rather than as a host.

## 4. Failure visibility

This is the section that exists because of the original complaint, so it is the most opinionated.

### Rule 1 — never render stale status as live

When the feed dies, the bridge immediately repaints **every** managed row and fires one
notification. `agr` kept displaying the last-known glyph forever, which is worse than showing
nothing: a dashboard that lies is only discovered after you have trusted it for an hour.

**The treatment is `idle` + a `[?]` title prefix.** There is no `unknown` status — the vocabulary is
`active|blocked|completed|idle` and nothing else. `idle` renders as *no glyph*, which is precisely
"we are not asserting anything", and the `[?]` prefix is what keeps it from being pixel-identical to
a live idle agent. Same reasoning for `[done]`: title markers, not statuses, because the vocabulary
cannot express either.

**Feed silence is the only trigger**, and it is the only inference-free staleness signal in the
system because the bridge *owns the ssh* and can observe it. Every way a connection can end — stdin
EOF, the application-level watchdog, or a spawn that failed — is the same fact and gets the same
treatment. There is **no host-silence trigger** (amendment 1). `mark_stale` is idempotent, so a
reconnect storm fires one notification, not ten; the first line of any kind lifts the treatment and
re-asserts the level state.

The watchdog is application-level on purpose: `ServerAlive` alone does not catch a half-open
connection. It has **two thresholds**, because "no data for a while" and "this connection is dead"
are different claims and only one of them justifies killing an ssh:

| | value | what it does |
|---|---|---|
| `BRIDGE_QUIET` | 5 poll intervals (10 s) | rows go `[?]` + `idle`; the loop keeps reading |
| `BRIDGE_WATCHDOG` | 600 s | the ssh is torn down and respawned |

The long one is anchored to the **mount**, not to the poll interval: `/shared` is a hard mount with
`timeo=600,retrans=10` and no `intr`, and `feed_poll` does one `open()` per marker and per `.state`
on it, so a single uninterruptible RPC can block for that whole budget. When both thresholds were
10 s, an ordinary server hiccup tore down the ssh, fired a desktop notification and flipped every
row — then repeated about every 12–16 s, for a stall respawning ssh cannot influence at all because
it is farm-side. The `[?]` rendering was right; the teardown was not. A line arriving during a quiet
spell lifts the `[?]` in place, with no reconnect.

**The two deadlines say different things.** A 10 s quiet spell announced "the feed is gone", in the
same words as a 600 s teardown, which trains the reader to ignore the notification that matters. The
quiet message names the deadline and says the connection is still open and the rows repaint as soon
as it speaks; only the watchdog, a real EOF and a failed spawn say the feed is *gone*.

Two refinements the tests forced: a **malformed** line does not refresh either deadline (bytes
arriving is not a feed that still speaks the protocol, and counting garbage as liveness is how a
wedged feed keeps a dashboard looking healthy), and a line arriving after the deadline does not
un-fire it.

The notification has two channels: a stderr `NOTICE` line, which always fires and is what launchd's
log carries, and `osascript`, which is what the user actually sees. The second is best-effort and
its own failure is reported rather than swallowed — and it is **rate limited to one per 5 minutes,
across reconnect cycles**. `mark_stale`'s idempotence only covers one connection, since the first
line of the next one clears the flag, so a stall that ends the connection, gets respawned and stalls
again used to produce one banner per backoff cycle. The stderr line is deliberately not limited: it
is the log, and it is what makes a missing banner diagnosable rather than a second silence.

That last sentence was false for one release, in the direction that costs the record. Every other
bridge warning goes through `_warn_once`, which dedups **by exact text for the life of the process**
— right for "agtermctl is broken", which would otherwise be a line per poll, and wrong for the one
line that says the feed died: five outages over two hours reached the launchd log once and the
desktop five times, the exact reverse of the split above. The bridge's warn channel therefore
exempts `NOTICE: ` lines from the dedup and nothing else, so the rate limiting lives only where it
was designed to live.

### The renderer's memory is not a description of the screen

`_title` and `_status` each remember what they last emitted per key and skip a repaint that matches
it. That is a real saving — the bridge repaints from every snapshot, and without it a quiet farm
would still produce two `agtermctl` calls per row per poll — but for one release it was also being
trusted as an account of *what agterm is currently showing*. It is not one, and it cannot be:
**agterm changes rows for its own reasons.**

The case that proved it: attaching to a row for the first time made its glyph disappear. agterm
resets a session's status when the session's command starts. The remembered status still matched the
real one, so the bridge never repainted, and the row stayed blank **until the agent's state next
changed** — which, since state only moves when Claude Code fires a hook, can be hours for an idle
agent. Nothing was logged, because nothing failed.

So every row's status is re-sent **every `REASSERT_INTERVAL` (30 s)** whether it changed or not. The
memory keeps its job as an optimisation; it can no longer make a divergence permanent. An attach, an
agterm restart or a display bug self-heals within one interval.

Four constraints, each with a test that fails if it is removed:

| | why |
|---|---|
| on **ticks only** | a tick is emitted when the farm has nothing to say, and a row nobody is updating is exactly the row that stays wrong |
| never while **stale** | `[?]` + `idle` is the correct rendering then; restoring the last known status would assert something no longer observed |
| never **blinks** | a re-assert is not a transition — otherwise every row flashes twice a minute, for ever |
| **rate limited** to the interval | a tick arrives every 2 s; one `agtermctl` per row per tick, for a display that is almost always already right |

The interval trades how long a wrong row may stay wrong against one `agtermctl` per row per 30 s.
The counter is seeded at construction rather than left unset, so the first re-assert lands a full
interval in — the initial paint has just happened, and repeating it immediately is pure duplicate
traffic.

### Rule 2 — liveness flows back through the same channel

The `feed` process runs on machine #2 and is alive exactly as long as the Mac's ssh is alive. On
each poll it touches:

```
<statedir>/bridge/<mac-id>.beat
```

That file is on NFS, so **any terminal on any farm machine** — including #3 — can read bridge
health with no extra transport, no port, and no daemon. `agb status-line` is the tmux segment. It
prints **one bounded line and nothing else** — the bridge's health, not the agents':

```
bridge:UP 2s        <- healthy
bridge:DOWN 14m     <- would have caught the real outage immediately
```

⚠️ **Corrected against the implementation.** The first draft sketched this as
`● build ◐ deploy │ bridge:UP 2s`, i.e. with a per-agent glyph list in front of the bridge field.
Nothing renders that and nothing should: the glyphs live in agterm's sidebar on the **Mac**, which is
the only machine with the bijection, and rendering them here would mean reading every host's marker
and `.state` on **every `status-interval` tick** — against the one-`open()`-per-tick budget Task 8
measured and pins. The segment is one field you compose into your own `status-right`
(`#(… agb status-line --mac-id …) | %H:%M`); see [`tmux.md`](tmux.md).

Five renderings rather than two, each because it calls for a different next step: `UP <age>`,
`DOWN <age>`, `DOWN never` (nothing has ever beaten under this mac-id — a typo, not an outage),
`UP +<age>` for a future-dated beat (marked rather than clamped to `0s`, because clamping would hide
a clock disagreement), and `bridge:ERR <reason>`. It **may never go blank**: a bad option, an
unreadable `bridge/` and a missing statedir all render one bounded line on **stdout**, never stderr
(which tmux does not read) and never a traceback. It is the only command in the tool that catches
its own errors and still exits with output.

A configured `mac_id` is never second-guessed — if its beat is missing the answer is `DOWN never`,
not "some other Mac's beat is fresh", which would report a different machine as this one. Without
one, it falls back to the newest `bridge/*.beat`. Single-Mac topology is assumed.

⚠️ **The segment must not be trusted below ~60 s in the `DOWN → UP` direction.** The mtime itself is
not the problem — `open()`+`fstat` forces a real `GETATTR` — but *finding* the file is: a cached
negative dentry can hide a brand-new beat for up to `acdirmax=60`, and the `bridge/*.beat` fallback
is a `readdir` with the same window. tmux adds up to one `status-interval` on top, since `#()`
displays the previous run's output. `UP → DOWN` is prompt. See [`tmux.md`](tmux.md).

The exact same threshold (`DOCTOR_BEAT_STALE`) drives `doctor`'s stale-beat verdict, aliased rather
than re-typed: a bar reading `UP` beside a `doctor` reporting a stale beat is a dashboard arguing
with itself.

### Rule 3 — hooks stay non-blocking but stop being silent

Exit 0 always; `agr`'s goal there was correct. But record **why** into a breadcrumb, so a failure
is recoverable after the fact instead of invisible during it.

Breadcrumbs are `err/<host>.<key>.log`, **one file per session**: NFSv3 `O_APPEND` is not atomic, so
a shared breadcrumb file is corruptible, and a corruptible breadcrumb undermines the entire
non-silence thesis. Bound by truncate-and-restart above ~64 KB (a read-modify-write would conflict
with the single-`os.write` discipline; the file is per-session, so there is no concurrent appender
to race with). `breadcrumb()` itself can never raise — it is the one call that must not turn a
degraded run into a failed one. A mint is breadcrumbed too: "why did this row appear?" is otherwise
unanswerable after the fact.

Two related hook rules, both structural:

- **Nothing is ever written to stdout.** Claude Code injects `UserPromptSubmit` stdout into the
  prompt context.
- **The hook never blocks reading its stdin JSON.** Exiting without reading gives Claude an `EPIPE`,
  which is expected and harmless.

### Rule 4 — `agb doctor` probes, never checks existence

The bug that started this project was `[ -S "$sock" ]` returning true for a dead socket. So doctor:

- writes a temp file and `rename()`s it, then reads it back byte for byte — proves atomicity
  actually works on this mount, and cleans up in a `finally` so a failed verify leaves nothing
  behind. (The read-back's `fstat` mtime becomes doctor's `now`.) A statedir that **does not exist
  yet** is not a failure of this probe but a `warn` deferring to the statedir probe above: on a fresh
  farm host no agent has run, and `install.sh farm` prints `agb doctor` as its documented next step,
  so a hard `[fail]` + exit 1 was the first thing a correct install showed you — contradicting the
  `warn` two lines above it. That degrade is gated on **`os.stat(statedir)` answering `ENOENT` or
  `ENOTDIR`** — the same two errnos the statedir probe calls absent — and on nothing else. Written
  as `os.path.isdir(gen)` it swallowed *every* stat failure there is (`ENOTDIR`, `EACCES`, `ESTALE`,
  `EIO`, `ELOOP`) and reported a `gen/` that exists and cannot be written as "does not exist yet": a
  false sentence, at `warn`, exiting 0, from the probe whose whole job is to catch exactly that —
  and `prune`, which reads the same probe, then refused to run while telling the operator a `gen`
  that plainly exists does not. Every other failure falls through to the write, which reports the
  real errno at `[fail]`. `doctor` does **not** create the tree either: it reports, and creating
  it would paper over the very misconfiguration the statedir probe exists to surface (and
  `doctor --statedir /typo` would leave a `/typo` behind on every run). `prune` reads the same probe
  more strictly — it deletes, so it stops on anything short of `ok`, needing both the writability
  proof and the server-stamped `now` that every age it prints is measured against.
- reports its own entry's **age**, not merely its presence — and reports a `.state` that fails
  validation as "no information this pass", never as gone.
- reports `bridge/<mac-id>.beat` age, answering *"is the Mac actually consuming?"*, with three
  distinct answers because they have three different causes: fresh, `STALE` at ≥ 30 s, and "no beat
  file at all" for a configured mac-id — the last is how a mac-id typo becomes visible instead of
  looking like a quiet Mac.
- **measures clock skew** per host, mtime vs in-file `updated`, sampled from the `.json` (`.state`'s
  mtime is the beat and is refreshed long after `updated` is written, so that pair would measure the
  beat interval). Keys come from the host's marker content, never a foreign `readdir`.
- prints the mount's `ac*`, `hard`/`soft`, `timeo` and `retrans` from `/proc/mounts`, with both
  interpretations spelled out rather than left to the reader: no `ac*` option means the kernel
  defaults apply, which is *why* discovery reads `gen/<host>.marker` by name; and a `hard` mount
  without `intr` blocks uninterruptibly, which is the first thing to look at when "Claude froze".
- reports statedir ownership and mode, through the same `verify_statedir` the hook uses, so the two
  cannot disagree about what an acceptable statedir is.
- prints breadcrumb tails from **every** host's `err/` logs, not just this one's: machine #3's log is
  precisely the one nobody can otherwise reach. `--tail <n>` sets how many lines of each, default 3,
  over at most 20 logs.
- lists **unadjudicable** entries (§2) as data, which is what `prune` consumes — a second derivation
  of "which entries look old" would be a second place for the heuristic to drift into a claim. Quiet
  means `sweep/<host>.marker` older than `--quiet-after`, default 900 s (15 min), or absent
  altogether; `prune` takes the same flag with the same default.

A failing probe is contained and reported, so the run continues and later probes still appear.
Warnings alone exit **0** — an exit status that cries wolf is one nobody reads — while a failed
probe exits 1.

### Acknowledged, un-engineerable risk

`/shared` is a **hard** mount with `timeo=600,retrans=10` and no `intr`/`soft`. A server hiccup
blocks `open()`/`stat()` uninterruptibly for minutes; `signal.alarm()` cannot interrupt
uninterruptible NFS I/O and the exit-0 discipline never gets reached. Every Claude tool call
traverses this path. Nothing in this design fixes that; `doctor` printing the mount options is what
turns "Claude froze" into a diagnosis in seconds rather than a bug report against agb.

## 5. Implementation

**Decision: stdlib-only Python, written to the 3.6.8 floor, in three files that are one unit.**

### It is three files, not one

The first draft said "a single stdlib-only Python file … one artifact … no distribution problem to
solve". **That is superseded**, twice over — once by the Mac-side split and once by the operator
split — and the reason is specific to how this tool is invoked:

> `agb` has **no `.py` extension** and is run as a script, so CPython writes no `__pycache__` entry
> and **re-compiles the whole file on every hook**. Mac-side and operator-side code sitting in it was
> being parsed hundreds of times per session by processes that can never run it.

| File | Holds | Loaded by |
|---|---|---|
| `agb` | the hot path (`hook`), `feed`, the shared primitives, dispatch | always |
| `agb_mac` | `bridge`, `close-done` | lazily, from `cmd_bridge` / `cmd_close_done` only |
| `agb_ops` | `doctor`, `prune`, `pane`, `status-line`, `install-hooks`, `install-config` | lazily, through one shared door (`cmd_ops`) |

Measured on the farm box: `compile()` of `agb` alone is 3.8 ms; of `agb` + `agb_ops` it is 5.3 ms.
**Keeping `doctor` out of `agb` alone is worth 1.6 ms on every hook.** The Mac-side split bought
back 1.5 ms when the hot path had drifted to 12.1 ms.

Consequences that are part of the design, not incidental:

- The siblings are loaded through one `_load_sibling`, which registers the running module as
  `sys.modules["agb"]` before exec, so `agb_mac`'s own `import agb` binds the **same** module
  object. Two copies would mean two config caches and two versions of every rule.
- Shared primitives (errors, config, `_json`, `_select_readable`, validators) live in `agb` and are
  consumed qualified; a name defined in two files is a test failure, because the structural guards
  merge all three files into one call graph by function name and a duplicate would silently shadow.
- **None of the three has a `.py` extension**, so the import machinery cannot pick any of them up by
  name — and no file named after a stdlib module may sit next to them, because `-S -E` does not
  strip `sys.path[0]` (`-P` arrives in 3.11).
- **Distribution is three files that must land in the same directory.** `install.sh` copies all
  three; a Mac with only `agb` fails at the first `agb bridge`, and a farm box missing `agb_ops`
  fails at the first `agb doctor` — the worst possible moment for a missing file. `$HOME` being NFS
  means one copy is visible farm-wide, but that covers only the *binary*: each farm host still needs
  `agb install-hooks` and a `~/.config/agbridge/config` of its own, because `agb status-line` runs
  under tmux's `status-interval` where neither `$AGB_STATEDIR` nor the ssh `env` is present.

`agb status-line` is the one command where this split is a cost rather than a win — tmux re-runs it
forever, so it is a second hot path. It was measured both ways: in `agb_ops` a tick costs
15.2–15.5 ms; spliced into `agb` it costs 12.2–12.5 ms but adds 0.25 ms of parse to **every hook**.
`agb_ops` won, because a tick is a background repaint nothing waits on (tmux displays the *previous*
run's output) while the hook cost is latency inside a Claude tool call. ⚠️ One caveat, documented
rather than buried: `agb_ops` is loaded as a *module*, so CPython caches its bytecode — on a
**read-only install directory** that cache cannot be written and every tick pays the full compile
(~21 ms).

### Hot-path cost is a design constraint, not an afterthought

`PostToolUse` fires on every tool call, so hook latency is multiplied by hundreds per session.

⚠️ **The first draft's "startup dominated by ~80×" claim is withdrawn — no measurement supported
it.** Re-measured as medians of 40 runs timed in-process (the original numbers were inflated by
shell-fork overhead and a cold NFS cache):

| Operation | Cost |
|---|---|
| `python3 -c pass` | 13.9 ms |
| `python3 -S -c pass` | 8.5 ms |
| `python3 -S -E -c pass` | 8.8 ms |
| `python3 -S -E -c "import json,os,time,sys"` | 17.6 ms |
| NFS atomic write + `rename()` | 0.89 ms |
| NFS `readdir` | 3.7 ms |

**`-S -E` saves ~5.1 ms; `import json` costs ~8.8 ms on top of it.** Both are real and both justify
their constraints, but they are single-digit milliseconds, not two orders of magnitude.

⚠️ **These figures drift**, and by more than the differences they are used to justify: the same
`-S -E -c pass` floor measured 4.0–4.6 ms in later sessions on the same box, and `$HOME` free space
moved from 81 MB to 3.2 GB within one working session. Treat every table here as a *dated
observation* and re-measure whenever a decision hinges on one.

Two conclusions survive re-measurement:

1. **NFS is not the bottleneck.** The real work is ~1 ms. This validates the shared-filesystem
   transport.
2. **Interpreter startup dominates, and part of it is avoidable.** `site.py` scans user
   site-packages over NFS; `-S` skips it, `-E` ignores `PYTHONPATH`/`PYTHONHOME`. `-E` is also a
   robustness win: a stray `PYTHONPATH` cannot break the hook.

**Hooks are therefore invoked as `<abs-python> -S -E <path>/agb hook <state>`** — never via a
shebang (`env` cannot portably pass interpreter flags) and never as bare `python3`. The interpreter
path is absolute because hooks run in a minimal non-interactive environment, and a bare `python3`
that fails to resolve kills the hook *before* `agb` runs, so no breadcrumb is written — exactly the
silent-failure class this project exists to kill. The predicate is "resolves to a working
interpreter at the same absolute path on every host that runs hooks", **not** "lives on a shared
mount"; requiring the latter would push toward an NFS interpreter and undo the whole budget.
`install-hooks` **probes** rather than checks it: it runs `<python> -S -E <agb> version` and requires
`agb <VERSION>` back, one round trip validating interpreter, flags and path together. Every one of
`agr`'s silent no-ops would have passed an `os.access` check.

### Sidecar state file keeps `json` off the hot path

The residual cost is `import json`. So alongside `<key>.json`, `<key>.state` carries the five bare
lines of §1. The hot path — *"has my state changed?"* — reads that file, compares, and exits,
never importing `json`. **`import json` lives in exactly one function**, called only from the
transition path, which makes the rule checkable on the AST rather than by grepping a file whose
comments discuss json throughout. The runtime guard is
`<python> -S -E -v agb hook <state>` on a genuine no-change invocation printing no `import 'json'`
line, with a negative control on the transition path so the assertion cannot pass vacuously.

`argparse` is absent for the same reason — a ~10 ms import — so every argv parser in the tool is
hand-rolled.

| Path | Frequency | Cost |
|---|---|---|
| hot (no change) | every tool call | **~11 ms measured**, against a ~4 ms interpreter floor |
| transition (write both files, rebuild marker) | rare | ~24 ms |
| sweep (throttled, once/60 s/host) | rare | + a few `readdir`s |

### The hot-path NFS budget

Every NFS round trip on a hard mount is an independent stall point, so the no-change path is
budgeted rather than merely "fast": it touches **exactly two files** — `idx/<anchor>` and
`sessions/<host>/<key>.state` (read, plus a conditional `os.utime(fd, None)` on the **same fd**;
verified that `os.utime` accepts an `O_RDONLY` fd here, saving a second `LOOKUP`). Config is never
read on the hot path, and the sweep-throttle marker check lives on the **transition** path only —
putting it on the no-change path would make the budget three files. A syscall-counting test pins it.

### NFS attribute caching, and why the marker carries the key list

Two facts about this mount shape the whole discovery mechanism:

- ⚠️ **`inotify` does not see NFS writes from other hosts.** It will *appear* to work, because it
  catches local writes, then silently miss everything from machine #3. The feed polls.
- ⚠️ **Attribute caching defeats naive polling too — polling does not solve the first problem.** The
  mount carries no `ac*` options, so the kernel defaults apply (`acdirmin=30 acdirmax=60
  acregmin=3 acregmax=60`) and a `readdir` can serve a **cached listing for up to 60 s** after
  another host creates a file. Opening a file inside a directory does **not** refresh that
  directory's listing — dentry and attribute caching are keyed on the *directory* inode.

What does work: `open()` on a **known name** issues a real `LOOKUP`, and since keys are random there
is no cached negative dentry to short-circuit it. Therefore:

> **`gen/<host>.marker` carries the live key list as its content.** Cross-host discovery reads
> markers by name and then each `.state` by name. It never `readdir`s a foreign host's session
> directory.

A brand-new **host** still costs up to 60 s to discover, because `readdir(gen/)` is how hosts are
enumerated. That is accepted, once per host boot, and documented rather than hidden.

**Scope note: the prohibition is cross-host only.** Local writes invalidate the local dcache, so
`readdir(sessions/<own_host>/)` **is** authoritative — which is exactly what the marker rebuild and
the sweep need. Stated globally, the rule would leave no way to build the marker at all.

*(Precision: an NFSv3 `LOOKUP` reply does carry `post_op_attr dir_attributes`, which the Linux
client folds into the parent, so a first lookup can invalidate the dir cache. But a repeat `open()`
of an already-cached name issues only a file `GETATTR` — no `LOOKUP`, no dir attrs — so the
conclusion stands.)*

### Freshness-critical reads are `open()` + `fstat`, never `stat`

`os.stat`/`os.scandir` attributes are served from the attribute cache, so a cross-host reader
holding a cached dentry can be handed the **old inode's attributes silently**. Close-to-open
consistency (verified: no `nocto` on this mount) forces a real `GETATTR` on every `open()`, and on a
renamed-over file that returns `ESTALE`, which self-heals into a fresh lookup. Measured here via
`/proc/self/mountstats`: 5 × `os.stat` → 1 GETATTR RPC; 5 × `open`+`fstat` → 5–6 GETATTRs.

**`ESTALE` triggers a re-lookup and retry, never a skip** — skipping turns a transient NFS condition
into a flapping or vanishing row.

### Write discipline by file class

Chosen per file by which property matters more:

| File | Method | Why |
|---|---|---|
| `sessions/*.state` | **in place** (`O_WRONLY\|O_CREAT\|O_TRUNC`, single `os.write`) | its **mtime is the data** (`beat`); `rename()` would make the mtime uncontrollable |
| `bridge/*.beat` | **in place** | same |
| `sweep/*.marker` | **in place** | same — its mtime is the throttle |
| `gen/*.marker` | **temp + `rename()`** | its **content** is the sole key list; content atomicity dominates |
| `sessions/*.json` | **temp + `rename()`** | torn reads matter |
| `dirname(<config>)/rows` (Mac) — `~/.config/agbridge/rows` for the default instance | **temp + `rename()`** | its **content** is the whole key ↔ row bijection; content atomicity dominates |

Temp names are `<name>.tmp.<host>.<pid>.<rand>`, opened `O_EXCL` so a name collision fails loudly
rather than letting two writers share a temp.

⚠️ **In-place `O_TRUNC` opens a zero-length window.** `open(O_TRUNC)` issues a synchronous
`SETATTR(size=0)` while the data write is page-cached and flushed at `close()`, so the empty interval
spans open→close and is RPC-visible to other hosts. This is exactly why the marker — where an empty
read would mean "this host has no sessions" — is **not** written in place. One transient read of an
in-place marker would emit `remove` for every key on that host.

### Torn reads are never data

The rule that makes the truncate window survivable:

> **A malformed, short, or empty read means "no information this poll" — never "empty" and never
> "gone". Removal requires positive proof, never absence of data.**

`.state` must parse as exactly five lines with a state in the vocabulary; the marker carries a
trailing `#end <count>` sentinel that must match. A read failing validation is discarded and the
previous value retained — expressed as a data structure in the feed, which never drops a remembered
entry on a failed read. The single carve-out is `ENOENT` on a `.state`, which is positive proof
(§1).

*(For the marker the sentinel is defence-in-depth rather than strictly required — `rename()` swaps a
directory entry atomically, so a reader opening by name gets old-or-new, never torn. It is kept as a
cheap corruption canary.)*

### The wire format

NDJSON on the feed's stdout, line-buffered and flushed; every line carries the feed's `now`:

```json
{"t":"snapshot","now":…,"complete":true,"sessions":[ … ]}
{"t":"upsert","now":…,"session":{ … }}
{"t":"remove","now":…,"key":"a3f9c1e0"}
{"t":"tick","now":…}
```

- `snapshot` is emitted first on every connection, and the bridge applies it as a **replacement**,
  not a merge. This is the fix for `agr` failure mode #4/#5: while the bridge was disconnected,
  agents started and finished, and a push transport loses those edges permanently.

#### When may a snapshot claim to be the whole truth?

A snapshot is the feed's **claim to removal authority**, and the claim needs two separate things to
be sound. Both were got wrong once, in opposite directions, and each mistake cost a live row.

**1. The claim must be earned — `complete`.** Retention (§1) makes an unreadable file harmless from
poll 2 on: the previous value stands. A snapshot is poll **1**, and it has nothing to retain — a
marker or `.state` it could not read is simply *absent*, indistinguishable from a key that is
genuinely gone. The feed therefore reports whether that poll read everything it went looking for,
and the bridge **skips the removal half** of the resync when it did not. Upserts still apply: they
are positive data. A **missing** `complete` key means complete, so an older feed still resyncs
exactly as it did; anything *present* that is not the boolean `true` — `false`, `null`, `0`, and
just as importantly the **string** `"false"` — defers removals rather than authorising them on a
guess. The test is `complete is not True`, not `not complete`: a non-empty string is truthy in
Python, so a truth test read `"false"` (what a re-encoding proxy or a hand-edited replay produces)
as authority to mark every absent row `[done]`. Removal authority is granted by the boolean, never
merely left un-withheld.

`complete` is false when any probed `.state` gave a short read, any marker was unreadable, the poll
raised — *and* when the feed had **no marker source at all**. That last one is the direction that
looks safe and is not: `gen/` missing or holding no marker is an absence of *data*, not a farm with
no sessions, and `agb feed` calls `ensure_statedir`, which creates the tree — so a statedir the Mac
and the farm disagree about, a recreated one, or an ssh that lands before the automount is up would
otherwise arrive as an **authoritative** "the farm is empty" and mark every live row `[done]`.
Nothing cross-checks the two statedirs; only `install.sh` happens to keep them in step. A marker is
never unlinked once written (`rebuild_marker` writes an empty one), so "no marker anywhere" means
"no agent has ever run here" — which has nothing to remove in the first place. One agent, on any
host, restores authority on the next poll.

**2. The claim must arrive — `owed`.** Deferring the removals is only sound if a snapshot that *can*
carry them eventually turns up. The feed used to emit exactly one snapshot per connection, so an
incomplete poll 1 withdrew removal authority **for the whole ssh** — days, under launchd — and one
permanently unparseable `.state` (which the sweep skips by design, §2) pinned it false for ever. The
stranded row was then reachable by nothing at all: `close-done` only touches unbound entries, `prune`
works from statedir entries and there were none, and the feed never probed a key that was in no
marker. So `FeedState.owed` holds the obligation open, and the **first subsequent complete poll
re-emits the snapshot**, carrying every entry the feed holds — a snapshot is a replacement, not a
delta, so a re-emission built from that poll's upserts alone would remove everything that had not
changed since poll 1. Between the two the connection runs normally: per-key `remove`s are still
proven and still emitted. `owed` is **re-armed, not merely cleared**: any later incomplete poll owes
the bridge a fresh snapshot too, or the standing claim would rest on a pass that has since been
contradicted.

**3. The claim must be audible when it is withheld.** Both denials — an unreadable marker, an
unparseable `.state` — were silent, which is tolerable only while the condition is transient, and it
need not be: a zero-length `.state` left by a killed hook (§1) is permanent, and then `complete`
never comes back, `owed` is never discharged, and every poll of every feed process reports
`complete: false` with **not one byte of diagnostic**, indefinitely. Both now go to the feed's
stderr, in one sentence — *"<path> unreadable: no snapshot may remove anything it names"* — through
the same `_warn_once` the rest of the loop uses. It dedups by exact text and the text names the
file, so the cost is bounded at **one** launchd-log line per distinct path per process: a permanent
denial is one line however many polls it survives, and a transient one is one line too — not zero,
but never a flood, and never once per poll. The operator's repair
for the permanent case is `prune --key <host>/<key>` on the owning host (§2).
- `upsert` is emitted when the **wire record changes** — `seq` movement **or** a moved `beat`, not
  `seq` alone. The bridge shows beat age in the title, so withholding a beat refresh would let a
  live agent's title age while it is beating. The `.json` is still read only when `seq` moves (and
  at snapshot time, since a restarted feed has no cached record), so the extra emission costs no NFS
  round trip.
- `remove` is emitted on `ENOENT` for a known key, and in the *same* poll as a feed-side reap rather
  than one poll later — a dead agent must never be carried into a snapshot.
- `tick` is emitted on any poll that produced nothing else, which gives the bridge's watchdog a
  crisp silence signal and drives `BRIDGE_OP_TICK` (§3).

The feed **exits on stdin EOF** (`select` inside the loop; a `read()` returning `b""` is what proves
it) and on any write failure. An orphaned feed would keep touching `bridge/<mac-id>.beat`, making
`bridge:UP` a lie. It also silences stdout on the way out: CPython flushes `sys.stdout` during
interpreter shutdown, and with the pipe gone that raises `BrokenPipeError` and exits **120**, so a
clean disconnect would reach the supervisor as a crash.

The ssh the bridge spawns is
`ssh -o ServerAliveInterval=15 -o ServerAliveCountMax=3 -o ConnectTimeout=20 -o BatchMode=yes
<feed_host> env AGB_STATEDIR=<path> <remote_python> -S -E <agb_remote_path> feed <mac-id>`.

The last two options cover what `ServerAlive` cannot, both observed in a real launchd log.
`ServerAlive` starts only once a session exists, so it does not bound the **connect**: a laptop that
loses its VPN mid-session sat in the kernel's TCP timeout and reported `Operation timed out` minutes
later, with every row `[?]` throughout. `BatchMode` is the more serious one — a LaunchAgent has no
tty, so an ssh that decides to ask for a passphrase or a host-key confirmation blocks on a prompt
nobody can answer, for ever. That is a *hung* bridge with a live process, which from the outside is
indistinguishable from a quiet farm: exactly the failure class this design exists to remove.

It is built by a pure function that
**refuses rather than interpolates**: ssh joins its command words with spaces and the remote login
shell re-splits them, so a statedir containing a space — or a `~`, which would expand against the
*Mac's* home — is an error, not a connection that fails invisibly on the far side. Reconnect backoff
is 1→2→4→…→60 s, and a connection that delivered events restarts the exponent at 1 s rather than
zeroing it: a feed that dies right after its snapshot would otherwise be respawned in a tight loop.

### Sweep throttling

At ~3.7 ms per `readdir`, sweeping on every hook is affordable but pointless. It is gated on
`sweep/<host>.marker`'s mtime: at most once per 60 s per host, checked on the **transition** path
only. The window is claimed by touching the marker **before** the sweep runs, so a sweep that dies
partway cannot spin once per transition; a sweep that raises is breadcrumbed and swallowed, because
a hook that recorded its state successfully must not then fail because of a maintenance pass.

### Configuration

`~/.config/agbridge/config`, `key = value`, read lazily and **never on the hot path**. On the Mac
that path is a *default*, not a constant — `agb bridge --config <path>` reads another file, and
everything else the Mac side owns moves with it (see *One Mac, several instances* below):

| Key | Used by | Meaning | Default if unset |
|---|---|---|---|
| `statedir` | all | overridden by `$AGB_STATEDIR` | `~/.agbridge` |
| `mac_id` | `bridge`, `status-line`, `doctor` | names `bridge/<mac-id>.beat`; generated at install. `feed` takes it as an argv, not from config — it is spawned by the bridge | none; `bridge` refuses to start, `status-line` falls back to the newest `bridge/*.beat` |
| `feed_host` | `bridge` | ssh target for the feed | none; `bridge` refuses to start |
| `agb_remote_path` | `bridge`, `prune --via-ssh` | path to `agb` on the farm, **absolute** | `bridge`: `/opt/agbridge/agb`. `prune --via-ssh`: `agb.sibling_path("agb")` — the running tree's own `agb` |
| `remote_python` | `bridge`, `prune --via-ssh` | farm-side interpreter, **absolute** | `bridge`: `/bin/python3`. `prune --via-ssh`: `sys.executable` |
| `jump_host` | `pane`, `bridge`, `prune --via-ssh` | for machine #3. All three consumers drop it when the hop would go through the target or through the host they are already on (`agb_mac.jump_for`, `pane_settings`, `prune_jump_host`) — `install.sh` copies the Mac's `--jump-host` into the farm's config, where that is the normal case | none |
| `workspace` | `bridge` | agterm workspace for new rows, by **name**; created if absent. A remembered placement for a key beats it, so a refresh puts a moved row back rather than herding everything into one place | none; agterm's current workspace |
| `notify_on_blocked` | `bridge` | desktop banner on a transition into `blocked`, and the `session seen` that clears the badge when it ends. One key governs both halves — *unwind what you did* | on |
| `notify_on_new_row` | `bridge` | desktop banner when a key arrives with no row. Silent for `NEW_ROW_QUIET` after a connection's first batch, or a refresh banners every row at once. Also takes a **list of states**, and then only a row whose *first-seen* state is one of them is announced — `completed` is what `agb-claude` premints and `active` is where a bare `claude` arrives, so `notify_on_new_row = completed` means *the sessions I started deliberately*. An unknown state refuses the whole list and falls back to **on** | on |
| `notify_on_completed_after` | `bridge` | desktop banner when a turn **that ran at least this many seconds** finishes. The number is the switch; `0`/`off`/`no`/`false`/negative disables it. A threshold rather than a flag because `completed` fires once per *turn*, so ungated it announces every reply | `300` (5 min) |
| `row_fields` | `bridge` | which fields a row title shows and in what order; `cwd:base` shortens the directory. An unknown name refuses the whole list | `label,host,cwd,pane,beat` |
| `host_<name> = <ssh-target>` | `pane`, `prune --via-ssh` | a record's `host` is a hostname, not an ssh alias | the hostname, used as the ssh target |

⚠️ The four rows above `host_<name>` were **missing from this table** until 0.6.0 — `workspace` since
it was added, the two notification switches since 0.4.0. They were documented in `README.md` and
`docs/commands.md` the whole time, and `agb_ops.CONFIG_KEYS` was missing the notification pair too,
so `agb doctor` reported them as typos. Nothing caught it: there is no doc-consistency test, and both
tests that check `CONFIG_KEYS` iterate the list itself, so an omission made them weaker rather than
red. A key added here belongs in all four places at once.

The two consumers of `agb_remote_path`/`remote_python` default differently on purpose. The bridge
runs on the **Mac**, which has no farm paths to introspect, so it needs written-down ones;
`prune --via-ssh` is already running *on the farm*, where "the interpreter and the `agb` I am
running from" is the better answer — box #2 and machine #3 see the same NFS `agb`, and constraint
#14 already requires that interpreter to exist at the same absolute path on every host that runs
hooks. Either key overrides either consumer.

Malformed lines are skipped, never fatal, and retained so `doctor` can print them rather than
swallow them.

`agb install-config` writes this file — a command rather than shell, because the Mac config, the
farm config and the mac-id all need the same merge, the same validation and the same
`valid_mac_id`, and a second implementation of `key = value` in `sh` is a second reader that drifts.
It **merges**: comments, unknown keys and hand formatting survive verbatim. A `mac_id` already in
the file is **kept, never regenerated** — both halves would stay healthy while the Mac wrote one
`bridge/<mac-id>.beat` and the farm watched another, and the segment would read `bridge:DOWN`
forever. The farm side therefore refuses to invent one; `--generate-mac-id` is the Mac's flag.

`agb install-hooks` merges the four hook entries into `~/.claude/settings.json`
(`UserPromptSubmit`→`active`, `PostToolUse`→`active`, `Notification` **with the `permission_prompt`
matcher**→`blocked`, `Stop`→`completed`) idempotently, and **removes pre-existing `agr` entries** —
they are not unrelated third-party hooks to preserve, they are the tool being replaced, and left in
place both would fire on every tool call. The predicate is structural, never a substring: the
command is tokenised the way a shell would, leading assignments and a leading `env` are skipped, and
it is agr's only if that program word's basename is exactly `agr`. Anything ambiguous is **kept and
named in the report** for a human to look at — reporting an entry costs nothing and deleting the
wrong one is unrecoverable.

`install.sh` (POSIX `sh`; macOS ships bash 3.2 and the farm's login shell is tcsh) has two roles,
`mac` and `farm`, because the two sides install different things and only one owns the mac-id. After
copying, it **runs the installed tree** through all three files and refuses to configure anything
against a tree that cannot answer. `dist/com.agbridge.plist` is the launchd job for `agb bridge`,
with `KeepAlive` unconditionally true — a cleanly exited bridge is exactly the condition the `[?]`
rendering exists for, and the useful response is to reconnect.

### One Mac, several instances

Everything above assumes **one** statedir, one feed, one bridge. That holds for a cluster whose
hosts share a network home and fails the moment a second machine shares no disk with the first: no
shared directory means a second statedir, and §1's whole model is *inside* a statedir. A second
machine is therefore a second **instance** — its own bridge, its own feed, its own row bijection —
rendering into the same sidebar.

**One flag does it, because everything the Mac side owns derives from one path:**

| | derived from |
|---|---|
| the row bijection | `dirname(<config>)/rows` (`agb_mac.rows_path`) |
| remembered workspaces | `dirname(<config>)/placements` (`agb_mac.placements_path`) |
| `host_<name>` → ssh target | keys *inside* that config |
| `statedir`, `feed_host`, `mac_id`, the notification switches, `row_fields` | the same |

So `agb bridge --config <path>` is the whole of the isolation, and there is no second thing to get
right. `install.sh mac --instance <name>` is sugar over three flags that already existed:

| | without `--instance` | `--instance hostb` |
|---|---|---|
| config | `~/.config/agbridge/config` | `~/.config/agbridge/hostb/config` |
| launchd label | `com.agbridge` | `com.agbridge.hostb` |
| log dir | `~/Library/Logs/agbridge` | `~/Library/Logs/agbridge/hostb` |

⚠️ **The left column is no longer creatable.** `install.sh mac` refuses an install with no
`--instance`, so all three of those defaults are what a **pre-0.6.0** Mac has on disk rather than
something a run can still produce. The claim is exactly that: **no *new* nameless instance is created
by default**. It is **not** that symmetry is guaranteed — `install.sh mac --instance hostb --config
~/.config/agbridge/config` still writes the unnamed config, because `--instance` only *defaults*
`--config` rather than owning it, and refusing that would forbid a legitimate shape (adopting an
existing file under a name) to prevent a deliberate act.

And every reader of the left column **stays**, deliberately: `instance_display_name`'s `(default)`
spelling, `bind_label_to_config`'s *"a plist with no `--config` implies the default config"* branch,
`_is_agbridge_instance`'s label-space clause, and `doctor`/`status-line`/`prune --via-ssh` resolving
`~/.config/agbridge/config` unconditionally (limitation 3 below). **A plist on disk outlives the
installer that wrote it** — refusing to create new ones removes none of the old ones — so what
changed is **creatability**, not reachability. Anything later that "cleans up" one of those branches
on the strength of this change is deleting a reader whose input still exists.

**`--dest` and `--bin-dir` stay shared.** The three files are identical per instance, so there is one
code install and N configurations, and an upgrade is one `install.sh mac` rather than one per
machine. **`mac_id` is adopted, not minted**: it identifies *this Mac*, not this connection, and each
cluster's `bridge/<mac-id>.beat` lives in its own statedir — the same id in both places is the truth,
not a collision. The adoption reads it back through `agb install-config --print-mac-id` rather than
parsing `key = value` in shell, for the reason `install.sh` gives at its own parse site: a second
reader of this format is a second reader that drifts.

⚠️ **The row's command has to carry the config, and this is the part that fails invisibly.** A row
runs `agb pane <key> --host <hostname> …`, and `agb pane` resolves that hostname through **its own**
config read (`pane_settings` → `ssh_target_for`). With two instances that read hits the *default*
config, so instance B's rows would resolve their ssh target from instance A's `host_<name>` table:
click-to-attach lands on the wrong machine, or nowhere, while every unit test passes. `pane_argv`
therefore emits `--config <path>`, and `pane` accepts it.

**Two different rules for two audiences, deliberately.** The plist's `--config` is
**unconditional** — rendered for every install including the default one, so the installer has a
single path to get right instead of a conditional exercised only on the second machine. The row
command's flag is **conditional**, emitted only when the path differs from `agb.config_path()`, so a
default install's rows are minted with byte-identical commands and rows created before this change
keep working (they omit the flag and fall back to the default config, which is correct for them).
The predicate is `config and normpath(config) != normpath(agb.config_path())`: without the `config
and` half, `None` — the parameter's default — compares unequal and emits a literal `--config None`;
without `normpath` on both sides, a `$HOME` spelled with a trailing slash makes **every default
install** start re-minting every row.

Four guards exist because their absence is silent rather than loud:

- **`install.sh mac` refuses an install with no `--instance`.** It is a **hard error and not a
  warning**, because a warning on a *first* install is exactly the one that gets ignored — and what it
  warned about does not stay warned about: the nameless config, label and log directory outlive the
  run that printed it, so the asymmetry becomes permanent on that Mac and only the migration under
  `CHANGELOG.md`'s *Upgrading from ≤ 0.5.0* removes it.

  The refusal sits at the **top of `role_mac`**, beside the other two required flags, and both halves
  of where it sits are load-bearing: **before any filesystem mutation** (the first is
  `mkdir -p "$dest"`), so a refused install copies no code, writes no config and renders no plist;
  and **before `probe_farmhost`**, so it makes no ssh call either. The probe is what `--instance auto`
  reads a name out of, so refusing ahead of it is what makes *"no name can be invented"* true rather
  than merely untested — a refusal cannot have asked a machine what it is called.
- **`--instance` requires `--statedir`, unless that instance's own config already carries one.**
  Falling back to `agb.statedir()` reads the *default* config, so a new instance would inherit the
  first machine's farm path: ssh to the right machine, read the wrong directory, and `agb feed` would
  then *create* it and report an empty farm for ever. That is what `agb bridge`'s refusal to *have* a
  statedir default exists to prevent — there is no value the Mac side can invent for a path on
  another machine — arriving by a route that rule cannot see.

  The guard used to be flat, and re-typing the flag on every routine upgrade is how a wrong value
  gets copied forward — a `feed_host` typo reached one of this project's own instances exactly that
  way. So a **re-install** reads the statedir back out of the config `--instance` derived, through
  `agb install-config --print-statedir` (a pure query: it prints that file's **own** value, never the
  fallback, and writes nothing), and announces it as `statedir: adopted <value> from <config>`.

  ⚠️ **The adoption fires only when `--config` was NOT given**, and that condition is the whole
  guard rather than a detail. `$config` is "this instance's own" by convention, never by
  construction: `--instance hostb --config ~/.config/agbridge/config` is legal and still writes the
  unnamed file, so adopting through it means a bridge to `hostb` reading the *other* cluster's
  directory — the precise named failure, arriving by the one route the flat guard could not see. An
  explicit `--config` therefore keeps the old behaviour exactly, `--statedir` and all.

  ⚠️ **Not a mirror of the `mac_id` adoption above**, which probes this instance's config *and then
  the default one*. That asymmetry is deliberate: one Mac has one identity, so sharing an id across
  instances is the truth, while sharing a statedir is the failure being refused. One candidate here,
  never a loop.
- **`--instance` is refused outside the `mac` role.** `install.sh`'s option loop is role-agnostic, so
  `install.sh farm --instance x` would write the farm's config to `~/.config/agbridge/x/config` —
  which nothing on the farm reads, since `agb hook` and `agb status-line` resolve
  `agb.config_path()` and nothing else — and report success.
- **The name is validated as alphanumerics, `-` and `_`.** It becomes a launchd label component, a
  plist *filename*, a log directory and a config *directory*; `install.sh`'s general `shell_safe`
  permits `.` and `/`, so `--instance ../../evil` would pass it. Validated in the option loop rather
  than after it, so `--instance ""` is refused instead of reading as "not given".

**`agb-refresh` moves the label and the config together**, which is the entire reason `--instance` is
sugar there too: a label without its config stops instance B and forgets instance A's bindings. Its
liveness poll — the one that waits for the old bridge to actually exit — matches
`<agb> bridge --config <config>([[:space:]]|$)`, because `--dest` is shared and a bare
`<agb> bridge` matches every instance's process.

⚠️ **A bare `--config` therefore moves the label too**, by scanning `~/Library/LaunchAgents/*.plist`
for the job that holds **the map this path names**. It is not a convenience: it is what the bridge's
stale-row hint depends on. That hint is written into one instance's log and can only name what a
bridge knows about itself, which is a config path — nothing tells a bridge its instance name or its
launchd label. Unbound, `agb-refresh --config <B>` booted out `com.agbridge`, waited for **A's**
bridge to exit, and then ran `forget-rows` against B's map while **B's** bridge was still running:
the exact condition the wait exists to prevent, since a live bridge merges-then-writes and re-mints
against the ids just closed. The rows end up closed in agterm *and* still bound — strictly worse
than the fixed `Run agb-refresh` it replaced.

**"Holds the map" is not "was spelled the same way", and that distinction has now been got wrong in
four separate ways.** The map is `dirname(config)/rows` and `dirname(config)/placements`, so the scan
compares the **resolved directory and nothing else** (invariant 12) — `<dir>/config`, `<dir>/`,
`<dir>//` and `<dir>/anything` all name one map and all find one label. Three things follow, each of
which shipped as its opposite first:

- **Matching and choosing are different questions.** `*.plist` expands in collating order, so
  `com.agbridge.aaa` naming `<dir>/config.bak` beat `com.agbridge.hostb` naming `<dir>/config` on a
  `--config <dir>/config` run. Every directory-equal plist is a match and every one is reported; the
  one that names **this file** is the one used, compared canonically so `<dir>//config` still counts
  as naming it. A directory-only match still wins when it is the only one, so this narrows nothing.
- **A plist carrying no `--config` is an answer, not a silence: it is the *default* config.** The
  bridge it starts resolves `agb.config_path()` itself. Skipping it made the wrong-job bounce
  **silent** — install a second job's config into the default *directory*
  (`--instance hostb --config ~/.config/agbridge/hostb-config`; one map, two jobs) and
  `agb-refresh --config <default config>` saw only hostb's plist, adopted its label, and had nothing
  to warn about because it was then the only match. Two guards on the implication, because both
  directions of it are wrong: the plist must be **readable and parseable**, and its name must be in
  the **`com.agbridge` label space** (`~/Library/LaunchAgents` is shared with every other program on
  the Mac, and none of *their* plists carries a `--config` either). An agbridge install under a
  `--label` of its own is therefore not implied — the pre-existing answer, below.

  The first guard is the reader's **exit status**, not a `-r` test. `plist_arg` prints nothing for
  three different things about the file — "no `--config` in it", "could not read it at all", and
  "this is not a plist" — and only the first is an answer. It exits **2** for the other two, and
  `bind_label_to_config` spells the read as an `if` so that the status survives. A `-r` test caught
  only the middle case: a plist truncated by a full disk, or some other file that ended up under a
  `com.agbridge.*` name, is perfectly readable and went on to *stand for the default config*, so a
  file saying nothing about any map claimed one under a real job's label. Nothing is lost by skipping
  it — launchd cannot load it either, so no bridge was ever started from it.

  ⚠️ **There are FOUR statuses, not two, and only the first two are about the plist — the rest were
  being folded into "this file says nothing" at every call site.** Exit 0 is an answer and exit 2 is
  the intended *unreadable / not-a-plist* one. Exit **3** says `agb_mac` could not be loaded from
  beside `--agb`, so the parser this reader hands the argv to is not there at all (below); and a
  reader that exits **1, 126 or 127** has not answered about the plist either — it has said something
  about the *reader*. An `--python` naming a shell script, a missing file or an interpreter without
  `plistlib` is an ordinary operator mistake, and it made *every* plist say nothing: no job claimed
  the config, the run fell through to the **default label** and the **conventional config**, and
  `stopped: com.agbridge` / exit 0 came out in exactly the words it uses when it is right, while the
  named instance's bridge kept running over the map that had just been forgotten. So the rule is
  spelled once, in `plist_read_ok`, and asked at all three call sites: **2 is an answer, anything
  else is fatal** — with 3 naming `--agb`, because that is a different file from the interpreter. It
  must be called from the parent shell — a wrapper returning the
  value would run inside `value=$(…)`, where `exit 1` ends the substitution and the script carries on
  with an empty value, which is the silent fallback it exists to stop.

  ⚠️ **The "anything else" message names TWO files now, and that is a consequence of the reader
  moving into `agb instances`.** It used to be a statement about `$python` alone, because the reader
  was an inline `-c` program and nothing else could fail. Exit **1** is now also what `agb` returns
  for an `AgbError` and for any uncaught exception in `run_instances`, neither of which the
  interpreter did — so a message naming only `--python` would send an operator to replace a working
  python3. It names both, and says the probe below has already proved that both can run
  `agb instances --probe`, so whatever this is, it is later than that.

  ⚠️ **And a status cannot see an interpreter that succeeds while meaning nothing.** `--python
  /bin/echo` is executable, exits 0 and prints its own arguments, so every plist would "answer" a
  config path made of the reader's own arguments. That one is caught by *asking a question with a
  known answer*, once, before any plist is read — which is also where the good error message lives.
  ⚠️ **The question is `agb instances --probe`, whose known answer is the literal `instances-ok`,
  and it has to be that rather than the `import plistlib` probe it replaces.** Two reasons, and the
  second is the load-bearing one. There is no longer any other `-c` program in the script to probe —
  `plistlib` is imported by `agb`, not by the shell. And `agb` answers an **unknown command** with
  exit **2**, `USAGE` on stderr and *empty stdout*, which is byte-identical to a new `agb` answering
  "this plist says nothing": an `agb` predating this command (0.5.0 and earlier) would make every
  plist silent, and the run would fall through to the default label and the conventional config and
  report success in the words it uses when it is right. The probe is what makes exit 2 unambiguous
  afterwards, so it must be **stdout-compared and not status-compared** — a status alone cannot see
  `--python /bin/echo` either. No other mode has a fixed answer to compare against: `--labels`
  depends on the LaunchAgents directory and the human listing has no spec.
- **The ambiguity warning counts claimants, not runners-up of one kind.** One exact match plus one
  directory-only match is two jobs over one rows file; accounting that keeps two lists finds one
  entry in each and says nothing. It stays a warning rather than a refusal because there is nothing
  to choose — whichever job is bounced, the other keeps running over the map `forget-rows` is about
  to rewrite — and this is a recovery command.
- **A job whose `--config` is elsewhere but whose `--rows` is in this map still holds it**, for the
  reason given under the liveness probe above: `render_settings` spends `--rows` before the config.
  That is a claim too, and it is ranked **last** — `install.sh` renders no `--rows`, so it is a
  hand-edited plist, and it may only ever replace the "nothing claims this config" fallback, never
  outbid a job that names the config itself. It is counted as a claimant like any other, so two jobs
  over one rows file are still named.

When no plist claims the path — an `install.sh mac --config <nondefault>` install made before the
plist carried the flag, which really is a `com.agbridge` job — the default label is kept and a
`note:` says which reading was taken, which job it is about to bounce and whose bindings it is about
to discard.

⚠️ **The config path out of a plist is XML-decoded, and regex-quoted before it becomes a pattern.**
`install.sh` XML-escapes every value it substitutes, so `--config '/tmp/a&b/config'` is written
`&amp;` and lints clean; and `pgrep -f` reads an ERE, so `/tmp/a+b/config` interpolated raw matches
`ab` rather than the path it came from. Both land in the same silent failure as the two below: the
poll matches nothing, exits with zero waits and no warning, and the forget runs under a live bridge.

⚠️ **Everything in that pattern is read out of the plist, and each half of it closes a failure that
is silent rather than loud.**

- **Whether to narrow at all.** A narrow pattern is *vacuously false* against a plist rendered before
  this change, and that is the normal state right after adding an instance: `install.sh mac
  --instance hostb` renders hostb's plist, does not restart the default job, and *does* install the
  new `agb-refresh`. Assuming the narrow pattern would make the poll return immediately with no
  warning and let the forget land while that bridge is still alive — re-minting rows against ids it
  just closed, which is the `no such session` spam `agb-refresh` exists to cure, restored by a fix
  aimed at a cosmetic warning. So the plist is grepped for the flag, and the pattern widens (out
  loud) when it is absent.
- **Which path to narrow on.** The **value** comes from the plist too, not from the config this run
  resolved. They are different questions: the plist says what launchd started the process with,
  `--config` on the command line says which map to repair, and `install.sh mac --instance hostb
  --config <elsewhere>` makes them different strings. Building the pattern from the command line's
  answer is the same vacuous-false failure reached from the other end.
- **Where the path ends.** `pgrep -f` matches an unanchored regex against the whole command line, so
  the default instance's `…/agbridge/config` is a *prefix* of an instance named `configb`'s
  `…/agbridge/configb/config`. The trailing boundary is what keeps a plain `agb-refresh` from
  polling that live process for its full 10 s and warning — on the most common invocation there is.

⚠️ **And a narrow MISS is "not proven gone", never "gone".** The pattern is derived from the
**plist**; what it is asked about is the **running process**, and nothing keeps those in step:
`install.sh mac --no-load` writes the plist and deliberately leaves the old bridge running, a bridge
started by hand carries whatever was typed, and a re-rendered plist describes a process that has not
restarted. In each of those the plist carries `--config` and the live bridge does not, so the narrow
pattern misses on the *first* poll, the loop ends with zero waits and no output, and `stopped:` is
printed as a claim nobody checked — the forget then lands under a live bridge, which is the whole
failure the wait exists to prevent. Before the pattern was narrowed at all, that bridge **was**
waited for, so treating a miss as proof is a regression rather than a gap.

So a miss is not the end of the question — but the further question cannot be another **pattern**.
`pgrep -f` matches a regex against whatever spelling the process was *started with*, and there is no
way to canonicalise the far side of a regex match. So what is running is asked **directly**:
`pgrep -f "<agb> bridge"` hands back the pids, `ps -ww -o args=` turns those pids back into the
command lines they were started with, and each one is attributed to a map by `same_map` — the same
canonical comparison `bind_label_to_config` uses to pick the label (invariant 12).

| the running bridge's command line | this run's? |
|---|---|
| `--config X` (or several) | iff `X` names a file this run rewrites, for **any** occurrence on the line |
| `--rows R` (or several) | iff `R` does, whatever its `--config` says — `render_settings` spends `--rows` **before** the config (`opts.get("rows") or rows_path(config)`) |
| no **provable** `--config` | iff this run repairs the **default** map — the only one such a bridge can hold, since it resolves `agb.config_path()` itself |
| `ps` will not say | yes; unattributable is not gone |

"A file this run rewrites" is `ci_hits`, and it is two files from two flags on this side too:
`dirname(<config>)/placements` always, plus either `dirname(<config>)/rows` or the `--rows` this run
was given (`instance_paths`). `same_map` compares directories, so one call per path covers both.

⚠️ **`--config` is not the only flag that decides which map a bridge holds, and asking only about it
was an under-match.** `agb bridge --config <elsewhere> --rows <this map's rows>` is writing the very
file `forget-rows` is about to rewrite while its `--config` names somewhere else entirely — read as
somebody else's, that is zero waits and the forget landing under a live bridge that
merges-then-writes. Both flags are scanned and a `--rows` hit is enough on its own. But **only
`--config` answers the untagged question**: a bridge with a `--rows` and no `--config` still resolves
`agb.config_path()` for its *placements*, so letting `--rows` count as "this line carries a flag"
would skip that question and under-match again. The same asymmetry holds on the plist side, where a
`--rows` claim exists but is ranked **last** (below).

⚠️ **" --config " BYTES ARE NOT A `--config` FLAG, and reading them as one was the unsafe polarity.**
`bridge --workspace "farm --config /other/config"` leaves `config` unset — that bridge runs on
`agb.config_path()` and holds the **default** map — but `ps` flattens argv and prints a line
byte-for-byte identical to one carrying the flag. Taking those bytes as proof skipped the
default-map question entirely and answered "not ours" on a *default* refresh: zero waits,
`forget-rows` under a live bridge over the map being repaired. It is undecidable from the line alone
(both readings are real argvs), so it is resolved towards the wait: **a `--config` is proof only when
no other value-taking flag appears before it**, because there is then no value it could be part of.
`$BRIDGE_VALUE_FLAGS` in `agb-refresh` is what makes "value-taking" answerable — this script's copy
of `agb_mac.BRIDGE_VALUE_ARGS`, a cross-file agreement with no single source of truth (invariant 14),
pinned by `tests/test_agb_refresh.py`. ⚠️ **This is the only reader that still needs it**, and it is
the one that cannot ask the parser: by the time `ps` has flattened argv there is no argv left to hand
anybody. `plist_arg` had a copy of the same problem and solved it the other way (below). The scan
starts **after** `<agb> bridge`, because argv does: a
` --config ` inside the *agb path* is not an argument, and taking it as one made a line "proof" with
nothing before it to disprove it.

This costs nothing in the ordinary case and it is worth saying why: `ProgramArguments` puts
`--config` immediately after `bridge` (`dist/com.agbridge.plist`), so every launchd-started bridge is
proof and another instance's is still not waited for — the every-run 10 s warning that narrowing the
pattern removed does not come back. What changes is a hand-started
`agb bridge --feed-host X --config Y`, now waited for on a default-map run, which the "does nothing
for" list below already claimed from the other direction.

⚠️ **"Any occurrence", because which one the bridge is actually running on is `parse_bridge_args`'
answer and it keeps the LAST.** That parser reads its value flags into a dict with no duplicate check
(`opts[name] = inline`), so a repeated flag overwrites. Reading only the first — which both the
command-line reader and `plist_arg` did — attributes
`… bridge --config /old --config <this instance's>` to `/old`: not ours, zero waits, `forget-rows`
under a live bridge. The blank walk below cannot rescue it, since it only *shortens* the remainder.
But "the last one" cannot simply replace "the first one" either: `ps` flattens the arguments, so
`--config "/a --config /b"` is **one** path containing that literal text and is indistinguishable
from two flags — the first reading is right for that line and the last for a genuinely repeated flag,
and nothing on the line says which. Every occurrence is therefore offered, each with its own walk: a
strict superset of both readings, whose extra candidates can only ever over-match, which costs a
bounded wait. `plist_arg` has no such ambiguity — a plist's `ProgramArguments` **is** the argv — so
it simply keeps the last one, which is what launchd starts the bridge with.

⚠️ **And "every occurrence" is over BOTH spellings, in POSITION order.** `agb bridge` accepts
`--config=<path>` as well as `--config <path>` — `parse_bridge_args` partitions every argument on `=`
before looking the name up — so both readers have to know both. Neither did, and each failed
differently:

- The command-line reader scanned with two `case` arms, one per spelling, and `case` picks by **arm
  order, not by position**. A line carrying an inline occurrence *before* a space-form one cut at the
  space form and threw the inline value away — and under the one-flag-with-blanks reading that walk
  exists for, the discarded value is exactly the one the parser keeps (`--config=/tmp/a --config
  b/config` is **one** argument). Not ours, no wait, `forget-rows` under a live bridge. The earlier
  round's permutation check missed this by only ever composing lines out of *separate* flags, the
  reading in which the loss is a harmless over-match. The fix is one marker — the bare `--config` —
  cut at its first occurrence, with the character after it saying which spelling it is (and saying
  it is not the flag at all when it is neither `=` nor a blank, so `--configs/b` inside somebody's
  `--statedir` value does not make an untagged bridge read as tagged).
- `plist_arg` recognised only the two-element form, so `<string>--config=/path</string>` read as
  `""` — and `""` is not "no answer" downstream: `bind_label_to_config` reads it as the *default*
  config (below), so an instance's own job claimed the default map, the run fell through to the
  default label, and it forgot that instance's bindings while that instance's bridge was still up.
  Last-wins also has to hold **across** the spellings rather than within each, since
  `--config /decoy --config=<real>` leaves the bridge on `<real>`. Both of those are free now that
  this side calls the parser instead of imitating it; the `ps` side cannot, and still spells them.

The value line a two-element pair consumes is never re-read as a flag, because the parser takes the
argument after `--config` **verbatim** — `--config --config=/x` puts the bridge on a file literally
named `--config=/x`.

⚠️ **And on the plist side that rule generalises: a `<string>` is a flag only when it is in FLAG
POSITION.** `<string>--workspace</string>` followed by `<string>--config=/other/config</string>` is a
*workspace name* that reads like a flag, and `agb bridge` never sees a config there — but a reader
that searches for the text answers `/other/config`, and then every path in the script acts on a map
no process is running on: the banner named it, the liveness pattern was built from it, and
`forget-rows` "repaired" it, reporting `the map is already empty` and exiting 0 while the real map
kept the stale bindings that sent you there. Unlike the `ps` side there is no ambiguity to resolve
and none is invented: `ProgramArguments` is a real argv array, so a blank inside a value is inside
one `<string>`, the answer is exact, and this side may answer "no config" confidently where a
flattened line may not.

### The plist reader does not simulate the parser, it calls it

⚠️ **`plist_arg` walked the array itself for five review rounds, and that sequence has the same shape
as the awk one below it: each round added a rule and the next round found the rule it did not have.**
Last occurrence and not first; both spellings; flag position; only `ProgramArguments`; only after the
command word. Every one of those is a property of `agb_mac.parse_bridge_args` **restated in a second
language**, and each was a wrong-job bounce before it was a rule.

The fifth round found the class a restatement cannot reach: **an argv the parser REJECTS**.
`bridge --config=/real/config --config=` is a missing-value error; `bridge --config /real bridge
--config /decoy` a stray positional; `bridge --config /real --bogus` an unknown option;
`bridge --config /real --watchdog soon` a number that is not one. `agb bridge` exits on every one, so
the job runs no bridge and holds no map — and the walk answered a real-looking config for all four,
which made a dead `KeepAlive` job an **exact, declaring, rank-1** claimant that outranked the live
one. Simulating the rejection rules too would have needed the **boolean** flag list as a second
cross-file agreement, plus the two numeric validations, plus the positional rule: a *wider* agreement
and a sixth round.

So the elements after the command word are handed to `parse_bridge_args` itself, loaded by path from
the directory `$agb` lives in (`agb` registered in `sys.modules` first, so `agb_mac`'s own
`import agb` binds it — the same hop `agb._load_sibling` makes). **This is not a new dependency**:
`install.sh` copies the three files together and refuses an install where they are not, and step 2 of
`agb-refresh` — `agb forget-rows` — already goes through `agb._load_mac()`. A tree where the load
fails cannot do the forget either, which is why the failure is **exit 3, fatal, naming `--agb`**
rather than "this plist says nothing": that answer would bounce whichever job the unread plists left
unclaimed and then fail the forget anyway, after the bootout. `agb_mac` is kept off the *hook's* hot
path, and this is a recovery command that already sleeps in a poll loop.

⚠️ **And the exit-3 catch lives in `agb`'s dispatch arm, around the LOAD ONLY** — not inside
`run_instances`, which cannot catch its own module failing to load. Both narrower spellings shipped
once and were wrong, in opposite directions:

- `except (ImportError, AttributeError)` **misses it**. `agb._load_sibling` loads `agb_mac` **by
  path** — neither sibling has a `.py` extension, so the import machinery cannot find them — and a
  tree with no `agb_mac` beside `agb` therefore raises `FileNotFoundError`, an **`OSError`**. It
  escaped as a traceback and exit 1, which `plist_read_ok` reads as "the reader itself failed": a
  statement about `--python`, sending an operator to replace a working interpreter instead of to
  `--agb`. Latent while the reader was an inline `-c` program that answered 3 for itself; load-bearing
  the moment `plist_arg` started calling `agb`.
- Wrapping the **call** as well as the load is the opposite mistake. A bug in `run_instances` is not
  a statement about the tree, and turning it into 3 would say "cannot load `agb_mac`, pass `--agb`"
  about a file that is sitting right there and loaded fine. It must stay loud.

⚠️ **An argv the parser refuses is "carries no `--config`", and the fail direction was checked rather
than assumed.** The job holds no map, so it is not a claimant — that is simply correct. What it costs
is at the one call site asking a different question: `agb-refresh --instance hostb`, whose plist has
been hand-edited into something the parser refuses, falls back to the conventional path instead of
the config that broken argv names. That is the same answer the shape already got when the `--config`
sat in *front* of the command word, and it is the merely-useless direction rather than the
destructive one (`forget-rows` reports an empty map and exits 0, against bouncing a live instance's
job); `--config` settles it. Ranking such a plist *below* a working claimant instead of dropping it
would be better still and is **not available**: with the argv refused there is no parsed value to
rank, and recovering one means the simulation again.

The `--rows` claim above is the same call asked for a different key, taken from
`BRIDGE_VALUE_ARGS` rather than derived from the flag name — `--feed-host` is `feed_host` there, and
a rule spelled in the shell would be one more thing to keep in step.

A **non-string** element is the one thing answered without the parser, which would raise on it rather
than answer: launchd refuses such a job outright, so the whole array is "carries no `--config`". That
is also a behaviour change — a nested `<array>` in *argument* position used to be stepped over, and
`agb bridge` would have refused it as a stray positional.

⚠️ **And only `ProgramArguments`, which three places claimed and none did.** A plist is not an argv —
only one of its keys is — and every other key carries strings too: `ProcessType`, the two log paths,
`WorkingDirectory`, an `EnvironmentVariables` value, a `WatchPaths` array. Reading the whole file
took them all as arguments, and **both** directions of that were reachable on a hand-edited plist
while nothing said a word:

| where the stray pair sits | what came out |
|---|---|
| **after** `ProgramArguments` | it *overwrote* the real value, because last-wins is right inside argv — the banner, the liveness pattern and `forget-rows` all named a map launchd started nothing on, while the argv one kept its stale bindings |
| **before** `ProgramArguments` | it *manufactured* a config for a job whose argv has none, so a plist standing for the default map claimed some other one |

That is now **structural** rather than a boundary the reader has to hold: `plistlib` hands back a
dict and `plist_arg` asks it for one key, so a nested `ProgramArguments` under `EnvironmentVariables`
is under `EnvironmentVariables` and a `WatchPaths` array is a `WatchPaths` array. It got there the
long way round, and the next section is why.

⚠️ **And only the part of `ProgramArguments` AFTER the `bridge` command word, because that array is
the whole command line and not the bridge's argv.** What `install.sh` renders is

```
<python> -S -E <agb> bridge --config <path>
```

so four elements go by before anything the bridge ever sees, and `agb` reads its command from
`argv[1]` and nothing else. A flag in **front** of the command name therefore *is* the command name:
`<agb> --config /real/config bridge` is `agb: unknown command: --config`, refused, and restarted
once every `ThrottleInterval` for ever under `KeepAlive`. That job runs no bridge and holds no map —
and reading `/real/config` off it made it an **exact, declaring claimant, rank 1**, the top of the
table. With it sorting first (`com.agbridge.aaa` before `com.agbridge.hostb`) a `--config
/real/config` run bounced the job that was not running, waited for nothing, and forgot the map while
the real bridge was still live and merging.

**No `bridge` in the array at all is "carries no `--config`", not "this file says nothing"**, and the
direction was chosen rather than fallen into. It is the same answer a plist predating the flag gives,
so the caller's existing implication (`no --config` ⇒ `$DEFAULT_CONFIG`, ranked *below* every job
that names one) already puts such a job where it belongs. Exit 2 would make it invisible instead —
and would swallow `<plist/>` and every plist with no `ProgramArguments` with it, which is the shape a
Mac installed before instances existed is still in.

⚠️ **The harness had never seen the real shape.** The differential corpus modelled `ProgramArguments`
as `["bridge", …]` and the fixture that renders plists for every other test did the same, so forty
cases were checked against an array four elements shorter than any that exists — a property proved on
inputs the property was not about. Nothing *fails* when a harness is simpler than reality; that is
what makes it worth a guard of its own, and there are now two: the corpus runs every case in **both**
shapes (`_as_installed`), and the fixture's rendered argv is compared against `dist/com.agbridge.plist`
itself rather than against a constant in the test file.

### The plist is parsed, not tokenized

⚠️ **`plist_arg` was an awk token scan for four review rounds, and every round produced a finding of
exactly one kind: a hand-rolled XML tokenizer is not an XML parser.** Whitespace inside a tag,
comments spanning a value, CDATA, processing instructions, DOCTYPE, character references,
minification, nesting — each round added a rule and the next round found the rule it did not have.
The last two are worth writing down because both were **valid plists** that `plistlib` and `plutil`
read without complaint, and both failed towards a path *no bridge is running on*, which is the unsafe
direction (the liveness poll matches nothing and `forget-rows` lands under a live bridge):

- **Whitespace inside a tag.** XML says a start tag is `<` Name (S Attribute)\* S? `>` and an end tag
  is `</` Name S? `>`, so `<string >` and `</array >` are both well-formed. The scan matched neither
  literal. `<string >/real/config</string>` therefore **vanished**, and the dangling `--config` was
  spent on the next element — `--workspace`. `</array >` never **closed** argv, so a later
  `WatchPaths` array overwrote the real config with its own strings.
- **A comment splitting a value across lines.** `<string>/tmp/a<!--` ⏎ `-->b/config</string>` is the
  string `/tmp/ab/config` — XML says character data either side of a comment is one value. The scan
  carried its "inside a comment" state from record to record but not the text *before* the opener, so
  the halves were never joined, the element vanished, and the `--config` was spent on the next one.
  The comment that shipped with that code claimed comments were one-directional and could only ever
  *hide* argv; that is true only of a comment sitting **between** elements.

The replacement is not a fifth rule. The reader parses the file with **`plistlib`**, which costs no
new dependency: it is stdlib on macOS and on the Linux the suite runs on, it imports clean under
`-S -E`, and `agb-refresh` already *requires* a python3 — it dies without one and runs `agb` through
it. (That is also why `$python` is resolved **before** the label is bound: this reader is the first
thing in the script that needs it. Left where it was, every read inside `bind_label_to_config` ran
`"" -S -E …`, so every plist answered nothing and the run fell through to the default label with a
named instance's bridge live.) `plutil` was rejected for the opposite reason — macOS-only, so the
suite could not test what it shipped.

⚠️ **And the reader is no longer in `agb-refresh` at all: `plist_arg` is two lines of shell that
call `agb instances --plist <path> --arg <flag>`.** The parse, the `plistlib` sniff-retry, the
command-word boundary and the `parse_bridge_args` call all live in `agb_mac.run_instances`. The
statuses are unchanged, deliberately — 0/2/3/other is the contract `plist_read_ok` and all three call
sites were already written against, and it had twelve named tests behind it. What moved is *where*
the answer is computed, and the reason is the next section.

It also reads what the scan could not, and these were all written down as permanent limitations:
**binary** plists (`plutil -convert binary1`, which is what Xcode, `PlistBuddy` and `defaults write`
leave behind), a config delivered **as CDATA**, entities and **character references** anywhere
including in a flag *name* (`&#45;-config`), a **DOCTYPE** with an internal subset, and a tag whose
text **spans lines**. They are gone, not narrowed.

⚠️ **One `plistlib` quirk needed handling and is not obvious.** `plistlib.load` **sniffs** the format
off the first 32 bytes and recognises only `<?xml`, `<plist` and the binary magic — so a plist that
opens with its **DOCTYPE**, which is valid XML and which launchd loads, is refused before the parser
sees it. Naming the format (`fmt=plistlib.FMT_XML`) skips the sniff, so the reader tries that on any
failure. It is a widening, not a loosening: a file that is genuinely not XML still fails, one line
lower, in the parser.

The cost is a **process per question** rather than an awk per question, and it went up again when the
reader moved into `agb`. Measured on the 3.6.8 floor: the inline `-c` reader was ~21.5 ms per call;
`agb` as `__main__` plus one `agb_mac` load is **~24.7–26.1 ms**, because `agb` runs as `__main__`
and CPython caches no bytecode for it — the same property the hot path is built around, arriving here
as a cost. `bind_label_to_config` asks up to twice per plist, so a pathological directory of 30
LaunchAgents goes roughly **1.3 s → 1.6 s**; a real `~/Library/LaunchAgents` holds one or two
agbridge plists. **Recorded so it is not rediscovered as a regression, and it is not a reason to
change anything**: this is a recovery command that already sleeps in a poll loop waiting for a bridge
to exit and starts a second python for `forget-rows`.

There is no `2>/dev/null`. The awk had one — so that an unreadable plist stayed quiet — and it
swallowed the interpreter's own diagnostics too, so a parse error *in the program itself* read as
"this plist names no config" at every call site. The reader is silent by construction instead: it
catches its own errors and answers with a status, which leaves stderr free to carry a real bug. The
probe deliberately neither captures stderr nor discards it, for the two halves of the same reason:
`2>&1` would fold startup noise into the answer and refuse a working interpreter, and `2>/dev/null`
would swallow the traceback that explains why.

⚠️ **Three rules used to govern the reader's own text and output. One is retired, one is now
structural, and the dangerous one survives the move unchanged.** The program was inline in POSIX sh,
so it could **contain no apostrophe** — one would have closed the quoting around it. That rule is
**gone**: there is no embedded program left, and the guard that pinned it was deleted rather than
repointed, since its non-vacuity assertions (`"plistlib" in program`, `len(program) > 500`) have no
subject any more. The **pure ASCII** rule is now structural: Python decoded a `-c` program with the
*locale's* filesystem encoding (and `-E` does not touch `LC_ALL`), so under `LC_ALL=C` one non-ASCII
byte in a comment was `Unable to decode the command from the command line` — the reader never ran,
and every caller read "this plist names no config". A file loaded from **disk** is decoded as UTF-8
whatever the locale says, so that cannot happen to `agb_mac`.

⚠️ **The output rule is NOT structural and did not move — it is the dangerous one.** The value is
written as **UTF-8 bytes to `sys.stdout.buffer`**, never `print`: `print` encodes with the locale
too, so a non-ASCII config path — ordinary on a Mac, where the filesystem is UTF-8 by fiat — raises
`UnicodeEncodeError` under `LC_ALL=C` (loud, and read as "no config") and, worse, *succeeds* under an
ISO-8859-1 locale, handing back a transcoded path that names nothing, carried on into the banner, the
`pgrep` pattern and `forget-rows` with no error anywhere. The awk passed bytes through untouched and
`run_instances` has to as well. The rule is stated in `run_instances`' own docstring, where the next
person to touch it will read it, and three locales (`C`, `POSIX`, `en_US.ISO-8859-1`) pin it on both
sides of the shell boundary.

⚠️ **It generalises, and this file used to say it did not.** *"No other code in
`agb`/`agb_mac`/`agb_ops` writes to `stdout.buffer`, so this will not happen by accident"* was true
when written and is **withdrawn**: it read as a property of the codebase when it was only a count of
one, and the second such value arrived without anyone rereading this paragraph.
`agb_ops.run_install_config`'s **`--print-statedir`** shipped through `sys.stdout.write` and had both
halves of the same bug — under `LC_ALL=C` a non-ASCII statedir exited **1**, the status reserved for
*I could not read the file at all*, for a file read perfectly; under ISO-8859-1 it exited **0** with
the path transcoded. It writes `stdout.buffer` now, with its own three-locale guard. The rule to
carry forward is the one the count was standing in for: **a value a caller parses leaves as UTF-8
bytes; prose for a human stays text on the injected `out` seam** — where `sys.stderr`'s default
`backslashreplace` handler means it can neither raise nor change an exit status, which stdout's
`strict` most certainly can. `--print-mac-id` is the deliberate exception and is safe for a reason
that does not generalise either: `valid_mac_id` refuses anything outside an ASCII alphabet, so that
value cannot be non-ASCII at all.

⚠️ **Too TIGHT is not the safe side here, which is why the reader is a real parser and not a boundary
drawn to what `install.sh` happens to write.** Missing a real `ProgramArguments` demotes that plist
to "carries no `--config`" — which for a *named* instance means its label is never found and the run
bounces the **default** job while that instance's bridge is live, the same accident from the other
end. One test still feeds it `dist/com.agbridge.plist` rendered, XML comment and all, and a
**differential corpus** of forty-odd hand-editable plists compares the reader against authorities
that are not `agb-refresh`'s own code: `plistlib` says what the argv *is*, and
`agb_mac.parse_bridge_args` says what `agb bridge` *does* with it. The corpus keeps every shape the
token scan got wrong **and** every shape it got right — a corpus holding only the cases the current
reader passes proves nothing about the next one.

A wait for any of those is announced once, saying which of the five it is, and the 10 s warning
names **what was actually still running** rather than `$label` — `$label` is the job that was booted
out, so it is the one process provably *not* still matching.

⚠️ **The first shape of this was a SUBTRACTION, and it had a hole in exactly the place the label side
did.** It asked only "is a bridge up that carries no `--config` at all?", by counting
`pgrep -f "<agb> bridge"` and subtracting `pgrep -f "<agb> bridge --config"`, because "does not
contain" is not an extended regular expression. A bridge over **this** map started with a merely
different spelling of the same path — `<dir>/./config`, a relative path, a symlinked `$HOME`, or the
plist re-rendered by `install.sh mac --instance` after the running bridge was started from an older
one — is counted on both sides, so it reads as "tagged, therefore somebody else's" and is not waited
for: `forget-rows` under a live bridge, silently. That is the same bug `same_map` was extracted to
fix on the label side, arriving on the process side, and it is why the answer is now a canonical
comparison and not a regex.

⚠️ **The untagged rule is gated on the map, and that gate is now asked per bridge.** The
justification first written here — the flag is rendered unconditionally, so no 0.5.0 plist can start
an untagged bridge, so it cannot be another instance's — is true only of a bridge some 0.5.0 plist
**started**, and contradicted twenty lines above by the case that motivated the whole narrowing:
`install.sh mac --instance hostb` renders hostb's plist, installs the newer `agb-refresh`, and does
**not** restart the default job, whose bridge therefore keeps running untagged from a plist rendered
before the flag existed. Ungated, that probe waited the full 10 s on **every**
`agb-refresh --instance hostb` and then printed `com.agbridge.hostb is still running after 10s` — a
warning that the forget may have been undone, provably false (hostb's bridge exited before the first
poll), on every run of a recovery command. The gate used to be spelled **once**, by emptying the
broad pattern on a non-default run — which switched off the attribution of every *other* bridge too,
and is how the differently-spelled case above went unwaited-for. It is asked per bridge now, where it
can only ever be about an untagged one.

What this deliberately does **nothing** for: a bridge started from a **different** `agb` path (the
broad pattern names this one, and always has); a Mac with no `pgrep`, where there is no poll at all;
a plist with no `--config`, where the pattern is already the broad one, matches any bridge, and the
note above already says so; a config path containing a **newline**, which `ps` cannot put back
together; and a bridge whose `--config` names a directory that no longer exists, where `same_map`
fails closed and it reads as another map's — which it is, in the only sense that matters here, since
there is no map there to protect; and a bridge that repeats `--config` where the value the *parser*
keeps is another instance's while an earlier one is this map's, which is attributed as ours and
waited for although it is somebody else's — an over-match, and the price of not having to decide
which reading of a flattened line is the right one. A config path containing a **blank** *is*
handled: `ps` flattens the arguments, so the value cannot be delimited from the flag that follows it,
and every blank-terminated prefix is offered to `same_map` instead. So is a **repeated** `--config`,
by offering every occurrence, and either **spelling** of it, by scanning one marker in position
order; so is a `--rows` that moves the map out from under the config; and so is a `--config` that
might be **part of another flag's value**, which is resolved towards the wait. Those, `ps` saying
nothing, and a hand-started `agb bridge --feed-host X --config Y` all err towards a bounded wait
rather than towards forgetting under a live bridge.

On the plist side, what `plist_arg` does **nothing** for is now a short list. Most of it is files no
bridge was started from — but **not all of it**, and the exception is the fourth bullet, so the list
is not a safe thing to summarise:

- **A file that is not a plist** — unreadable, truncated mid-element, or neither XML nor binary.
  `plistlib` raises and the reader exits 2, where the token scan used to keep the last *complete*
  value it had seen. That reads like a loss and is not: the scan's partial answer was not safe
  either — truncate a plist one element later and it answered `--workspace`, a flag name, as the
  config path. Exit 2 says "this file says nothing", and `bind_label_to_config` skips it rather than
  letting it stand for the default config (above).
- **A `ProgramArguments` element that is not a string.** launchd refuses such a job outright, so
  nothing is running from it. It consumes a pending flag — the walk stays in step — but is never
  reported as a value.
- **A path containing a newline**, which the command substitution would strip and which `ps` cannot
  put back together on the other side either.
- ⚠️ **An argv that reaches `agb bridge` indirectly — the one entry that IS a running bridge.**
  `/bin/sh -c "exec python3 <agb> bridge --config /x"` is *one* element, not seven, so no element
  equals `bridge` and the answer is "no `--config`" — for a job that is running a bridge, on `/x`,
  and is read as the **default** instance's. That is a genuine wrong-instance hazard on a hand-edited
  plist, not a file that holds no map: `bind_label_to_config` ranks it as a default-config claimant,
  so a default refresh can bounce it while the instance it really serves keeps its bridge, and a
  refresh of `/x` will not find it at all. Unchanged by the command-word boundary (the whole-array
  walk found no `--config` element either): the boundary is drawn to argv shaped the way `install.sh`
  writes it, not to every command line that ends up running a bridge. Handling it would mean parsing
  a shell word-splitting out of an arbitrary `-c` string, which is a second language to get wrong;
  the mitigation is that `install.sh` never writes one.
- **`bridge` appearing before the command word as some other argument's value** — `<agb> hook
  bridge`, or an interpreter option that takes `bridge` as its argument. The walk starts one element
  early and reads the rest as bridge flags. Neither is a command line that starts a bridge, and both
  fail towards "no `--config`" for every shape a human writes, since there are no flags after the
  command word in either.

All of them fail towards "no config found", which is the **loud** direction *for the default instance*
— the default label, and the note that says so — and the quiet one for a named instance. Five things
that used to be on this list are not any more, and it is worth saying which, because each was
written down as permanent: a **minified** plist; an **XML comment**, a **processing instruction** or
a **CDATA section**, in either direction; a **character reference** in a flag name or a key; the
**DOCTYPE** declaration, internal subset and all; and a **binary** plist. An element **outside**
`ProgramArguments` that reads like a flag left the list earlier, and for a sharper reason: it was
listed as a "no config found" case and was not one — it could silently *overwrite* a perfectly valid
argv value.

⚠️ **`--instance <name>` therefore does not *mean* `~/.config/agbridge/<name>/config`** anywhere in
`agb-refresh`: it means the config that instance's plist names, with the conventional path as the
fall-back for a Mac whose plist was never rendered. Rebuilding the convention unconditionally names
a file that may not exist, and `forget-rows` answers `the map is already empty` and exits 0 — the
recovery command reporting success for a map it never opened.

⚠️ **And "there is no plist" and "the plist is there and unreadable" are not the same question**,
though `plist_arg` answers exit 2 to both. The convention is the right fall-back for the first and a
*guess* for the second — a guess about a file that is sitting there saying otherwise. `agb-refresh
--instance hostb` against an install made with `--config <elsewhere>` and a corrupted
`com.agbridge.hostb.plist` repaired `~/.config/agbridge/hostb/config`, a map that never existed, so
`forget-rows` reported "already empty" and exited 0 — while the liveness pattern, built from the same
empty answer, waited for a bridge whose command line names the real config. Success, twice, on the
wrong instance. So the unreadable case **refuses**, and names the flag that settles it (`--config`).
`[ -e ]` is deliberately the weaker test: a dangling symlink or a plist under a directory this user
cannot search answers false and takes the convention exactly as before, so this can only turn the
noisier direction on, never a working `agb-refresh --instance` off. With `--config` given there is
nothing to guess and the run proceeds — with its own note, because the old one said "no `--config`
in <plist>" and gave the advice for a plist predating the flag, about a file that had not been read.

### `agb instances`, and why the reader moved into it

Everything above describes reading **one** plist an operator already named. The command that removed
the default instance needs the other question — *which instances exist* — and it needs it from two
places that share no language: `agb-refresh`, which is POSIX sh, and `close-done`/`forget-rows`,
which run in-process on the Mac. `agb instances` is the one answer both ask.

| mode | prints | status |
|---|---|---|
| `agb instances` | one row per instance, `name  label  config` — the **name** is the same one `agb-refresh`'s banner prints for that label (§5 limitation 1's table below: `(default)` only for `com.agbridge` itself, the label for anything outside that space), because two commands naming one instance differently is the same falsehood in two voices. One rule, spelled in shell and in `agb_mac.instance_display_name`, pinned by a test that runs the shell block's own text | 0 |
| `agb instances --labels` | one label per line — what the sweep iterates | contract 2 below |
| `agb instances --plist <path> --arg <flag>` | that flag's value out of that plist's bridge argv | the 0/2/3/other contract above |
| `agb instances --probe` | the literal `instances-ok` | 0 |

All four take `--launch-agents <dir>`. It lives in **`agb_mac`**, not `agb_ops`, for two reasons that
point the same way: it belongs beside `close-done` and `forget-rows`, which are the commands that
consume it, and putting it in `agb_ops` would need an `agb_mac` → `agb_ops` edge that exists nowhere
today. That cost a **budget raise** rather than the free `OPS_COMMANDS` route — recorded below, and
the first raise since the constant was introduced.

⚠️ **`--labels` needs its own status contract, and it is invariant 12 in a new place.** `--arg`
inherits 0/2/3/other; `--labels` had nothing, so "there are no instances" and "I could not list them"
would be the same answer — and a Mac with a momentarily unreadable LaunchAgents directory would sweep
nothing, fall back to the default job, and report success. So:

> **Contract 2.** A **missing** LaunchAgents directory is **ENOENT → status 0, empty output, "no
> instances"** — the ordinary Mac, and a recipe that has worked since 0.2.0 does not get to start
> failing because a discovery mechanism found nothing to discover. **Every other errno is "could not
> list", non-zero, and fatal at the caller.**

That split is written out rather than left to `os.path.isdir`, and the reason is one of this
project's own hard-won facts: `isdir`/`exists` swallow *every* `stat` errno, so using one as the
error branch reports a broken filesystem as "does not exist yet". That has shipped here before.

⚠️ **Membership is a different question from the label-space guard above, and both are right.**
`bind_label_to_config`'s `com.agbridge` guard is a **claimant** rule — it decides whether a plist may
stand for the default config, where a third-party LaunchAgent must not — and is correctly narrower.
`--labels` is asking who to sweep, and `install.sh --label <name>` puts no shape rule on a label, so
`weird.label.plist` is a real install:

> **Contract 3.** A plist is an agbridge instance for `--labels` iff **its label is in the
> `com.agbridge` space**, **or** its `ProgramArguments` contains the command word `bridge`
> immediately after an element whose **basename is `agb`**.

⚠️ **"basename is `agb`" means ANY tree, not `realpath`-equal to this one.** A plist naming an `agb`
in a different tree is deliberately supported, so requiring identity with the running `agb` would
drop such an instance out of every sweep silently. The looser half is the safe direction here:
over-listing costs a bounded refresh of something that turns out not to be ours, under-listing is an
instance nobody sweeps.

### The sweep, and the one command that does not do it

A bare `agb-refresh`, and a bare `agb close-done`, now act on **every** instance. That removes
limitation 1 below by changing the default rather than by warning about it.

⚠️ **`agb forget-rows` is the exception and is REFUSED without `--all`, and the rule is not "it
closes rows" — `agb-refresh` closes every row it forgets too** (`--no-close` is passed only when
asked). The difference is what happens next: `agb-refresh` **restarts the bridge**, so the rows it
forgot are re-minted within seconds, and `forget-rows` restarts nothing — a sweep nobody meant would
leave every row of every instance closed until each bridge was bounced by hand. **So the sweep that
ends in a restart may default to all; the one that does not, may not.** The consequence is what makes
that distinction true rather than stylistic: an instance the sweep leaves **without a running bridge
is an error**, spelled as a distinct child status (**4**), not folded into the ordinary failure code.

⚠️ **`--key` sweeps; it does not narrow.** A key is read out of a bridge log, and nothing in that log
says which instance minted it — *you should not have to know which instance* is the whole point. A
key belongs to exactly one map, so the sweep finds it wherever it lives, names the instance that had
it, and fails only when **none** did. What narrows a run is naming a **map**: `--instance`,
`--label`, `--config`, and `--rows`/`--placements` where the command has them. Such a run keeps
today's semantics exactly, which is what preserves `agb forget-rows --rows ~/.config/agbridge/rows`
as a documented recovery for an install with no instance name to give — and is the one place the old
default survives, deliberately.

⚠️ **The shell sweep re-execs `"$0" --label <L>` once per label rather than looping in process.**
`agb-refresh` is 1,600 lines of `set -eu` with per-run globals and a `die` on most error paths: an
in-process loop would carry one instance's state into the next, and any `die` would end the sweep
with jobs already booted out and never started again. A fresh process resets every global for free.
The child **cannot re-sweep** structurally rather than by a rule it is told to obey — it is handed
`--label`, which is one of the flags that narrow. Labels and not names, because `--instance`
*computes* `com.agbridge.<name>`: the default label, a custom `--label` install and a `--config`-only
install have no name to pass.

Three consequences worth stating, because each was a decision:

- **How the script names itself.** `$0` is whatever the caller typed and is not always a path —
  `agb-refresh` found on `$PATH`, or `sh agb-refresh` typed in its own directory, both leave a `$0`
  with no slash, and the suite only ever invokes an absolute path. `sweep_self` resolves it as: a
  path as given, else the **current directory**, then `$PATH`. ⚠️ **That order is not a preference**
  — it is the order `sh <name>` itself uses, so matching it is what guarantees the children are the
  same file as the parent. `$PATH` first would sweep with a *different* copy of the script and
  nothing would say so.
- **The `trap` lives in the CHILD**, on `INT`/`TERM`/`HUP`, armed one statement before the `bootout`
  and cleared after the restart — the sweeping parent reaches neither, so it can never arm it. The
  child re-raises after `trap -`, which is what gives the parent a 128+signo it can tell from a
  failure; the parent then **stops** the sweep rather than bouncing the instances the operator
  interrupted it to protect.
- **A child's failure is recorded and the sweep continues**, everything stopped is started again, and
  the run exits non-zero with a summary naming what failed. ⚠️ **One blind spot, stated rather than
  hidden**: several `--key`s spread across *different* instances make every child answer 1 (each
  forgets its own and reports the others missing), so the run reports failure although all of them
  were forgotten. Telling that from a real failure needs the children's **output**, not their status.
  It errs towards failing for work that succeeded, which is the safe direction. The in-process sweep
  does not inherit it — it can see which keys each map held.

⚠️ **The in-process sweep owes the same two policies, in the same words, or it is invariant 12
arriving where it closes `[done]` rows in the wrong instance's map and reports success.** A plist
whose bridge argv carries **no `--config`** reads as `agb.config_path()` — the
`bind_label_to_config` reading, *not* the `<dir>/<name>/config` convention, which belongs to a
*named* run and would repair a map that never existed. And **one unreadable plist mid-sweep is fatal,
not skipped** — with one clause the shell side did not need: fatal **iff its label says it is ours**.
A stricter rule would let any third-party junk `.plist` stop every sweep on the machine; a looser one
would let one of ours fall back to the default config.

**No instances found is a note, then the single default run** — on both sides, deliberately
identically. A Mac carrying a config and no launchd job is the commonest shape this tool is run on,
and `docs/cookbook.md`'s bare `agb close-done` recipe has worked since 0.2.0.

**Rejected discovery designs, recorded so they are not re-proposed.** **Config-directory globbing**
(`~/.config/agbridge/*/config`) misses an `install.sh mac --config /elsewhere` install entirely and
counts leftover directories from instances that no longer exist — it discovers *directories*, and the
question is which launchd jobs exist. **A registry file** the installer writes and the helpers read is
a fourth cross-file agreement with no single source of truth, and invariant 14 documents three that
each caused a bug; it would also go stale against a plist deleted by hand, which is exactly the state
these commands are run in. The plists **are** the registry: launchd already keeps them, and a job
that is not there is not an instance.

#### Limitations — documented, not solved

1. ⚠️ **MITIGATED BY THE DEFAULT, not only by the banner.** This used to read: *a helper run without
   `--instance` acts on the default instance and reports success in the same words* — `agb-refresh`
   stopped `com.agbridge`, forgot that instance's bindings and restarted it while you were trying to
   repair the other one, and nothing could detect the intent, so the whole mitigation was in the
   **output**. That is no longer the shape of it. A bare `agb-refresh` and a bare `agb close-done`
   now sweep **every** instance, and a bare `agb forget-rows` is refused and names `--all` (see *The
   sweep, and the one command that does not do it*, above). There is nothing left to mean by
   accident: the run that used to be the mistake is now the run that visits the instance you meant.

   **What survives, and it is not decoration.** The banner still prints on every run,
   unconditionally, before the dry-run exit — once per instance under a sweep, which is also what
   tells a sweep apart from a fall-through:

   | run | prints |
   |---|---|
   | `agb-refresh --instance hostb` | `instance: hostb -- label com.agbridge.hostb, config …/hostb/config` |
   | `agb-refresh` (the sweep) | `sweep: every agbridge instance in …`, then one `instance:` line per instance, then `swept: N instances` |
   | `agb forget-rows --config …` | `forget-rows: config …; rows …; placements …` |
   | `agb close-done --config …` | `close-done: config …; rows …` |

   ⚠️ The **second** row is the whole point of the table, and it is the row that changed. It used to
   read `agb-refresh (the mistake)` → `instance: (default) …`, annotated as the one thing this line
   could not be allowed to be wrong about. That cell is now false: a bare run names no single
   instance, because it acts on all of them.

   ⚠️ **Narrowing flags still exist and still need the banner**, which is why it did not go away with
   the default. `agb-refresh --instance hostb` and `agb forget-rows --rows <path>` are one-instance
   runs, and a mistyped one is exactly as silent as it always was.

   Only `agb-refresh` has an instance *name* to print — it is the only one of the three that takes
   `--instance`. The other two know a config path and the files derived from it, which is the same
   answer in the spelling they have. When the name was never typed but the label was resolved from a
   config (above), `agb-refresh` reads the name back out of the label rather than printing
   `(default)`, so `agb-refresh --config …/hostb/config` prints `hostb` too. ⚠️ **And a label outside
   the `com.agbridge` space is not the default one either** — `install.sh mac --label <anything>` puts
   no shape rule on a label, so `weird.label` is a real install, and it fell through to `(default)`.
   The sweep is what made that reachable by accident: it types `--label` for each plist it finds,
   custom labels included, so a bare run announced **two** default instances, one of which was
   somebody's named machine. Such an instance has no name but its label, so the label is what is
   shown; only `com.agbridge` itself is `(default)`.
2. **An upgrade needs each job restarted.** The code is shared, so `install.sh mac` updates every
   instance at once — but a running bridge holds the `agb_mac` it started with until its own job is
   booted out and back in.
3. **No aggregate view, and it cuts both ways.** `agb doctor` on a cluster host sees only that
   cluster's statedir, by construction. ⚠️ And on the **Mac**, `agb doctor` and `agb status-line`
   read the default config unconditionally and have **no `--config`** — so `doctor`, the first thing
   anyone runs when an instance misbehaves, always describes the default instance. `prune --via-ssh`
   likewise resolves `host_<name>` from the default config, so a named instance's hosts may not
   resolve there.

   ⚠️ **And this gets WORSE, not better, once every instance is named.** The point of naming them all
   is that there is then no unnamed one — on such a Mac `~/.config/agbridge/config` **does not
   exist**, so those three commands do not merely describe the wrong instance, they describe a file
   that is not there. `agb instances` is the aggregate view for *discovery* and answers none of this:
   it says which instances exist, not whether any of them is healthy. Giving `doctor` and
   `status-line` a `--config` is the obvious fix and is deliberately **not** in this change, which
   was about the row-map commands; until it lands, diagnose an instance from *its* machine
   (`agb doctor` there) and from its own bridge log.
4. **Nothing marks which cluster a row belongs to.** `workspace = <cluster>` in each instance's
   config is the recommended idiom — a convention, not a mechanism.
5. **N launchd jobs, N ssh connections, N logs.** Fine at around four. Past that is where the
   rejected shape below starts earning its complexity.
6. **Rows are per-instance**, so `agb-refresh` on one leaves the other's rows alone. Correct, and
   surprising the first time. ⚠️ **MITIGATED, not resolved.** The maps are still per-instance and
   nothing merges them — what changed is that the *commands* now visit all of them, so a bare
   `agb-refresh` or `agb close-done` repairs every map without your having to name each. A **narrowed**
   run is still one map, which is the whole point of narrowing it.
7. ⚠️ **An existing `install.sh mac --config <nondefault>` install changes behaviour.** That flag
   predates this change, and the plist used to **ignore** it: the bridge read
   `~/.config/agbridge/config` regardless. Now that the plist carries `--config`, such an install's
   rows map moves to `dirname(<nondefault>)/rows`, the old map is orphaned, and every row is minted
   again beside the ones agterm is still showing — **duplicate rows**. Moving `rows` and `placements`
   next to that config before reinstalling avoids it; `agb forget-rows --rows <old map>` clears the
   duplicates afterwards, since it closes each session as it forgets it. `CHANGELOG.md` carries this
   as the one upgrade that is not transparent.

#### The rejected shape: one bridge, several feeds

Designed and rejected, and the reason is recorded so it is not re-proposed: **it is a data-model
change, not a transport change.** `BridgeModel._upsert` keys purely on `key`, and nothing in the
wire, the model or the row map records which feed a session arrived from. Adding several feeds means
giving every session a *source*, and then:

- `_render_stale` must stale only the dead source's rows — today it marks **every** bound row `[?]`,
  so one machine's outage would blank the whole sidebar;
- the watchdog, the quiet deadline, the reconnect and the backoff all become per-source;
- `RowMap` and `agb pane` need the source anyway, to resolve `host_<name>` correctly — which is the
  same problem `--config` solves outright.

That is a few hundred lines and a large share of the hardest existing tests, all of which assume one
stream, and its worst failure is the shape of three bugs this project has already fixed. This shape's
worst failure is operating on the wrong instance, which is recoverable and now announced. Revisit
past roughly four machines.

## Resolved design questions

## 6. Agent-to-agent chat, and why it is **not** on this wire

`agb-peer` lets two agents send each other messages. Everything above describes a wire that carries
**state about** agents; this carries **text between** them, and the two are deliberately separate.

Nothing in `agb-peer` reads or writes a session record. A message is not a state transition, it has
no liveness meaning, and putting it on the wire would give every hook a second thing to be correct
about — on the hot path, in the file whose size is capped. The channel instead reuses what the
bridge has already built: a row is a **terminal**, and a terminal can be read and typed into.

- **Delivery** is the Mac-side `relay` calling `agtermctl session text` to read a participant's
  screen and `session type` to write to the other's. It is gated: `classify` refuses a peer that is
  mid-turn, that is showing `agb pane`'s menu, or whose composer is not empty. ⚠️ It also refuses a
  message that reduces to one of `agb pane`'s own menu words — `q` on a detached row **closes the
  row of a live agent**. That list is a cross-file agreement with `agb_ops` (CLAUDE.md invariant 14).
- **Announcement** is a doorbell: the sending pane's tmux **window name** becomes
  `<base> [peer #<id>]`, and the relay sees it in the status bar it is already capturing. No polling
  is added — the screen read was happening anyway.
- **Content** is a tmux **pane option**, not screen text. Screen text was the original design and it
  failed: Claude Code renders no command output onto its pane, and a long message wraps and
  paste-collapses. An option is exact, 3 KB, and survives scrolling.

### The unreachable peer

A peer may be somewhere neither the Mac nor the relay can reach — a batch-pool node picked at submit
time. Two of the three legs need no change at all. **Delivery already works**, because the pane
belongs to a host you *can* reach and is connected all the way down; nothing ever connects *into* the
pool, which is agbridge's founding constraint restated.

**Sending** cannot use tmux, and no permission fixes it: `$TMUX`/`$TMUX_PANE` *are* inherited through
job submission, but `/tmp` is per-machine, and putting the socket on the shared mount does not help —
MEASURED, a `tmux -S <shared path>` server accepts connections locally and answers `no server
running` from the pool. A unix socket is a local kernel rendezvous, not a filesystem object NFS
carries.

So that one leg falls back to the statedir after all: `<statedir>/chat/<id>.msg`, temp+renamed like
every other record here because a torn read on NFS is real, fetched by one `ssh <reachable host> cat`
and then removed. ⚠️ **The fallback needs a positive signal, not merely a failure** — the socket path
is checked directly rather than an error string matched, because three different failures produce the
same error and only one means "use files".

⚠️ **And the doorbell has to be *printed*, since there is no reachable window to rename** — which
makes it the sending agent's problem rather than the tool's. The relay reads a **screen**; an agent
UI folds command output behind a `ran N commands` summary; a correct message then sits unread with no
error at either end. `skills/agb-peer/SKILL.md` is the fix — it tells the sender to repeat that
`[peer #…]` line in its own answer, which *is* on the screen. This is the one part of the channel
that cannot be made structural, and it cost a 25-minute silent failure to find.

### A roster that changes while the relay runs

Participants started as a command line and are now optionally a **file**, re-read each tick. The
mechanism is small; the reasoning is where the cost is.

**Membership is a question about the roster, not about what resolved.** `try_deliver` used the
*resolved* map to answer both *is this a participant?* and *can I reach it?*, so a message to an
agent that had not booted yet was indistinguishable from a typo and was discarded. The second
question is a reason to wait; only the first justifies throwing a message away. A name in the roster
with no row is **held** — on a ladder, and bounded, because an unbounded hold accumulates mail for
the life of the relay.

**`seen` records what was READ.** It used to be written one line above the fetch, so an ssh that
failed marked the participant caught-up having read nothing and the doorbell guard suppressed every
retry. Recording the truth rather than the intention makes the retry fall out; the cost is that a
persistent failure repeats, which is what the say-once throttles are for.

**Priming is state that survives ticks, and it is defined by its meaning:** *there may be content on
this pane that predates the join, so the next successful drain must be discarded.* Every rule
follows from that sentence rather than a list —

| | |
|---|---|
| the pane was read (drained, or nothing announced) | **clear it** |
| the pane was not read (no row, unreadable, **detached**, drain failed) | **keep it**, retry |

⚠️ **`detached` is the one that looks like the first and is the second.** A detached row shows
`agb pane`'s menu, which hides the status bar, so its doorbell is not *visible* — not *absent*. Every
row `agb-refresh` re-mints comes back detached, so folding it into "nothing announced" delivers a
backlog on the **ordinary** path, not in a corner.

⚠️ **And "nothing announced" must clear it**, which is the inverse failure and the harder one to see.
A fresh joiner has never sent anything, so it has no doorbell at all; treating that as *not yet
primed* leaves it pending until the bound and throws away its **first real message** as a backlog.
Discarding a live message is worse than delivering a stale one — that asymmetry is the whole of the
rule, and it is also why the retry is **bounded**: while a name is pending the ordinary scan skips
it, so persisting longer destroys more real mail. Giving up clears the name *without* draining, so
the backlog is delivered instead. Visible, and the lesser failure.

**Reads are held, never believed.** Unreadable, missing, not UTF-8, malformed, empty: at runtime none
is evidence that anybody left. The dangerous one is not obvious garbage — it is a **truncated** read,
which is what a file being rewritten in place looks like for a millisecond and which parses *cleanly*
as a shorter roster. Treating a short answer as authoritative applies a leave nobody asked for. This
is invariant 2 in its own words. ⚠️ It does not fully close: a read truncated at a *line boundary* is
indistinguishable from a real removal, so `docs/commands.md` says to write the file atomically. The
window is ~1 ms against an ~8 s tick, and that is a judgement recorded rather than a gap overlooked.

**Startup refuses where runtime holds**, because holding means keeping the roster you already had and
at startup there is none. It is also the only place the two-participant minimum can live: a relay may
*drop* to one when somebody leaves, but it cannot begin with nobody to talk to.

**A rebind is not a leave.** Both forget the pane-specific state; only a leave drops the queued mail,
because a rebound participant moved rather than went and those messages exist nowhere else. ⚠️ A
rebind must also drop `resolved[name]`, or `resolve_all`'s keep-previous routes to the row it just
left — and only a name that *was* resolved exhibits it.

### Deferred, with reasons

- **Broadcast (`--to all`).** Not free: with three or more agents a reply-all is a feedback loop with
  no natural stop, and `SKILL.md`'s anti-deadlock rule — *send, then finish your turn* — does not
  cover it, because every participant answering once is already the storm.
- **Rooms.** Both halves were cut, in two steps. The *emitter* first: a new sender writing
  `default/bob` into an older relay is dropped while the sender sees `queued` and exit 0. The
  *reader* second, on re-examination: adding a reader later breaks nothing in either direction, and
  the only case it would help is a room-aware **sender** against a room-unaware **relay**, which
  cannot arise because rooms get implemented *in the relay*. When rooms are built, reader and emitter
  land together. ⚠️ And they will need an age-based complaint: once a relay claims only its own
  room's options, a message for a room whose relay is not running sits on the pane for ever, re-read
  every ring and never reported — today's `not a participant` line is what catches that.
- **Proactive join/leave announcements.** They work, but each one wakes every agent and costs it a
  turn. Behind a flag, off by default, if ever.
- **Per-participant `--chat-dir`.** It is relay-wide, so two *unreachable* agents on two *different*
  mounts cannot both be served.

### Asking who is here

### Writing the roster file, and why it needs its own contract

The roster is the one file this project **writes on behalf of a human** while another process is
reading it on a tick. `agb-peer-setup` is that writer; `write_roster_file` is the contract.

**The gate is the file's bytes**, not a `(mtime, size, inode)` key. `RosterReader` already made
that choice on the read side and wrote down why: the file is a handful of lines, so reading it is
cheaper than reasoning about whether a stat key can miss a rewrite — and `os.stat` on a network
mount is served from the attribute cache, which is what invariant 6 is about. Comparing what you
read satisfies it by construction. It also means an editor re-saving *identical* content is
correctly not a conflict, which a stat key would have got wrong.

**`roster_bytes` has three answers where `read_roster_file` has two**, and the third is a raise.
The relay folds every errno into one "hold", which is right for it — at runtime nothing is evidence
that anybody left. A **writer** cannot do that: folding "unreadable" into "absent" makes the gate
compare `None == None` and rename over a roster nobody could read, going vacuous exactly when it
matters. Invariant 12, applied to the one file this project writes.

**`RosterConflict` is a distinct class because its caller must recover**, not print and exit. An
in-memory draft is still good and has to reach disk before anything else happens. Everything
meaning *do not write* — changed, absent-now, present-now, unreadable — leaves through that one
door; anything arriving as a bare `PeerError` sails past the handler and the draft is lost
silently, with every test green.

**`write_draft_file` is ungated, and that is load-bearing.** Its only caller is the conflict
handler, and the path it is given was just minted and belongs to nobody — so there is nothing to
compare against and any gate would raise `RosterConflict` *from inside the conflict handler*. The
one path standing between a conflict and lost work cannot itself be conditional. It still goes
temp+rename, so a crash leaves the recovery file absent rather than half-written.

**Nothing is ever merged.** A roster line encodes a participant and a transport, not prose, so a
line-wise merge produces a file that looks plausible and routes messages somewhere nobody chose.

⚠️ **The gate is not a lock.** A writer landing between the comparison and the `rename` is still
lost. The window is microseconds against a human deciding what to type, and closing it needs a
lockfile whose failure modes are worse than the one it removes. Recorded so the trade is a decision
rather than an oversight.

`agb-peer who` is request/response over the channel that already exists: the agent sends the single
word `who` to the reserved recipient `relay`, the relay intercepts it while draining and queues a
reply to the asker, and the ordinary `pending` path delivers it with the same composer gate, holding
and throttles as any other message.

⚠️ **The asker is the pane the doorbell rang in** — `try_deliver`'s existing rule — which is why this
needs no identity of its own. An agent cannot print into another agent's pane, so the place is the
only part of a message that cannot be misstated.

⚠️ **Publishing was tried three times and abandoned, and this is why.** The relay would have written
the roster to each participant — a tmux pane option where tmux was reachable, a file under
`--chat-dir` where it was not — and each attempt failed on the same missing thing: **there is no
per-agent identity on the file transport.** The message path never needed one, because the id is in
the filename and the recipient is inside the file; `who` was the first thing that had to answer
*"which one are **you**"*. The three attempts were `$AGB_PEER_FROM` (read in one place, in the legacy
one-shot form, and exported by nothing), a per-participant file keyed on the spec's target (which for
`:nfs` is a sentinel string, not a pane), and a per-pane filename (pane ids are unique per tmux
*server*, and `--chat-dir` is relay-wide). Asking makes the question go away.

⚠️ **The answer must not invite a reply.** `SKILL.md` tells an agent that anything arriving as
`[chat from <name>]` is a peer talking to it and to reply — so an answer signed `[chat from relay]`
would be replied to, and the reply is another message to `relay`, for ever. The relay therefore
answers **only** the exact token and drops anything else with a line. This is the two-party case of
the feedback loop this document already refuses broadcast over.

**Membership only, and no timestamp.** The answer is composed when the request is *drained* and
delivered when the composer is free, so anything time-shaped would be a snapshot from one moment read
as truth at another — and `agb-peer` has no heartbeat by design. ⚠️ The peer list is the roster
**spec**, never what resolved: a row is routinely absent for a moment while `agb-refresh` re-mints
it, and deriving the list from resolvable participants would report that as *left the chat*.

**The costs, stated:** the answer takes a turn; a lost request is silent, and *no relay running* is
indistinguishable from *this pane is not a participant*; and an answer held behind a busy composer
can arrive slightly stale.

### Watching rows, and who owns agterm's one grid

agterm can show a view-only **grid** of live sessions. Two things in this project drive it, and they
are not the same feature:

> **The relay's grid is an adjunct to a message pump; `agb-dashboard`'s grid is the point.**

That single distinction decides both error policies, and both are correct *in place*:

| | `agb-peer relay --dashboard` | `agb-dashboard` |
|---|---|---|
| what the user asked for | a conversation; the grid is a convenience | the grid, as the primary effect |
| a member that will not resolve | **named, and the rest are gridded** — best-effort, because a cosmetic grid failure must never stop a message | **nothing opens**, exit 2, every problem named |
| a cell **agterm** drops (`unresolved:` on stdout, exit 0) | **named, and the rest stay gridded** — the same policy, and the relay was blind to this one until it had a reader of its own. ⚠️ **And not recorded as shown**, so the next tick retries: a partial open is an *incomplete* outcome, and its documented cause (a `:right` cell whose split is briefly absent) is transient | **nothing opens**: the grid agterm just put up is **closed again**, then exit 2 |
| a grid call that **raises** rather than failing | said and ignored; the message pump is what matters | reported, and any grid already up is closed on the way out |
| a member in the `scratch` pane | excluded and said; it counts as *accounted for*, not missing | a shortfall — nothing opens. ⚠️ **Named by PARTICIPANT NAME on both sides**: the operator wrote `drawer=…`, and a row-id prefix sends them to look up which line to edit — which is what `agb-refresh` re-minting every id makes impossible |
| more members than agterm's nine cells | the first nine are gridded and the rest **named** | **nothing opens**, exit 2, the cap and the count both given |
| lifetime | **follows**: re-resolves every tick and re-opens when the cell set moves | one-shot with a foreground hold; `--follow` deferred |

⚠️ **"Accounted for, not missing" is a rule about the REPORT, not about the grid.** An excluded
participant resolved perfectly well — its pane is merely not something a grid can express, and it has
just been named on its own line — so calling it "no row for carol" as well would be a second,
contradictory diagnosis of one situation, and the wrong one.

⚠️ **It was written here as "counting it as missing would keep the grid permanently shut", and that
is FALSE of the code.** `missing` drives one throttled `say` and gates nothing: it does not reach the
`dashboard` call, and a member with no row does not close the grid either. The counterfactual is real
but belongs to a *close rule* the plan considered and did not adopt — "close when any member is not
gridded". Escalating a cosmetic argument into a behavioural claim is the failure mode this file is
supposed to catch, not commit; it is recorded rather than quietly deleted because the rejected close
rule is exactly what a future reader will propose.

**Ownership.** agterm has **one** grid and no ownership token — its own help says `--close` closes
*"the open one"*. So the policy is: **neither closes a grid unless it opened one this run**, tracked
by a latched flag and cleared only on a close that worked.

⚠️ **That gates WHETHER, and it cannot gate WHICH — the distinction is the whole of what is true
here.** The flag records "this run opened a grid at some point"; `--close` takes no target. So a run
that opened a grid and had it replaced closes the *replacement*. This paragraph used to add "neither
reaches for a grid it did not open", and three operator-facing copies repeated it without the hedge
that followed — and it is false in exactly the configuration those copies were about: relay opens R,
`agb-dashboard` replaces it with D, relay exits and closes D. The code cannot do better, so the
sentence had to: **running both at once is documented as unsupported**, whoever opens last wins, and
the loser's grid does not come back — the relay's re-open is gated on its cell set *changing*, so
once replaced it stays gone until membership or the row ids move. Defending against it would mean
inventing an ownership token on top of a single global resource, in two processes that cannot see
each other.

⚠️ **"Best-effort" is a claim about a call that RAISES, not only about one that fails**, and both
sides had it wrong in the same way. `Ctl.dashboard` and `Ctl.dashboard_close` return a status when
agtermctl *ran* and refused — but `_spawn` raises `PeerError` when it cannot be started at all, and a
caller that only reads the status is a caller that dies. In the relay that killed the message pump
the grid is an adjunct to; in `agb-dashboard` it destroyed the error naming the still-up partial grid
and replaced the hold's exit with a traceback out of the cleanup. ⚠️ The general shape is worth more
than the fix: **the guarded call and the unguarded one sat three lines apart**, the guard had its
reason in a comment, and the newer code did not carry it. A "cannot stop the message" property has to
be enforced at every call, because a docstring stating it is not a mechanism — and a test fake that
can only *return* a failure describes a world where the raise cannot happen.

⚠️ **The exit status is second-guessed in exactly one place, and it is not laziness.** agterm exits
**0** while printing `unresolved: <id>` on stdout and opening the grid without those cells (measured
— [`agtermctl.md`](agtermctl.md)). A wrapper that trusted the status would present a silently
incomplete grid as a success, which is §4 Rule 1 in a new costume: a surface that looks correct and
is not. So `agb-dashboard` reads the output, **closes the grid it just caused**, and exits non-zero.
Closing first is not tidiness — the `unresolved:` line arrives *after* the grid is up, so refusing
without closing would leave exactly the partial grid the command exists to remove.

⚠️ **Never a bare cell id**, for a reason that is about the cap rather than about style. agterm's cap
is nine **panes**, and a bare id takes every pane of its session — so the same rows fit or do not
depending on whether a stranger opened a split on one of them. Emitting an explicit pane always
converts a cap that counts panes into a cap that counts **agents**, which is the only thing that
makes a preflight (`9 cells; got 10`) exact rather than a guess. One function, `dashboard_cells`,
spells it for both callers, and an AST guard spanning both files stops a second copy appearing.

**Where the resolver lives.** `agb-dashboard` loads `agb-peer` by path and uses its `match_sessions`,
its roster grammar and its `Ctl`. ⚠️ **That is an implementation smell and was deliberately not
allowed to choose the user-facing noun** — watching rows is not a peer-chat activity, so this is not
`agb-peer watch`. Extraction into something both callers share honestly is deferred until two callers
have shown where the boundary really is; today it is one caller and a comment.

### Still true, and still unfixed

⚠️ **An option that fails `parse_option_value` is neither unset nor reported.** `parse_show_options`
drops it before `drain` sees it, so it lingers silently on the pane for ever.

Verification status, in this project's vocabulary:

| | |
|---|---|
| reachable + no shared disk (Mac ↔ farm) | **CONFIRMED**, verified live |
| unreachable + shares a mount with its reachable neighbour (the pool) | **CONFIRMED**, verified live |
| unreachable + on a *different* mount from the other unreachable agents | **NEVER RUN** |

The three questions this document left open are now answered by the implementation:

- **Install/ops** — `install.sh` (`mac` / `farm` roles), `agb install-hooks`, `agb install-config`,
  `dist/com.agbridge.plist` for launchd. See §5, *Configuration*.
- **tmux status-line integration** — `agb status-line`, reading `bridge/<mac-id>.beat`. See §4
  Rule 2 and [`tmux.md`](tmux.md).
- **Where `statedir` lives** — `~/.agbridge` by default. The requirement is a **single directory
  that every agent host and the feed all resolve to the same underlying files**, writable, with room
  on it. A network home directory satisfies this on most clusters, which is why it is the default;
  it is *not* universal, and where `$HOME` is host-local the two halves silently watch different
  directories. Set `$AGB_STATEDIR` or the `statedir` config key when that is the case, and use
  `agb doctor` to confirm — it prints the resolved path, its ownership and mode, and the mount
  options behind it.

  ⚠️ **That requirement is per *instance*, not per Mac.** A machine that shares no disk with the
  first is not a broken statedir — it is a **second instance**, with its own statedir, feed and
  bridge, rendering into the same sidebar (`install.sh mac --instance <name> --statedir …`). See §5,
  *One Mac, several instances*. Read absolutely, the paragraph above says a standalone box cannot be
  covered at all, which stopped being true in 0.5.0.

  ⚠️ **Do not point it at a scratch volume without checking the purge policy.** Scratch and
  `/tmp`-like volumes commonly reap files by age, which would silently delete the `.state` of a
  long-idle session — manufacturing exactly the removals §2 refuses to infer. The tool cannot detect
  this; it looks identical to a session that ended.

Deliberately still open, because they need a second machine or a live agterm to answer:
the verbatim `agtermctl` contract (see [`agtermctl.md`](agtermctl.md) — the invocation shape is
*assumed*, with fallbacks recorded), and real cross-host propagation latency, which the
marker-carries-the-key-list design should make immediate for a new key and bounded by the
directory-attribute cache for a new *host*.
