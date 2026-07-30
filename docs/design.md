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
| **the Mac** | your workstation, running agterm and the `agb bridge` launchd job. It never reads the shared directory — it only ever sees the feed's stdout. |
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
| `agb bridge` | Mac | `agb` → `agb_mac` | one long-lived launchd job |
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

Beat age is therefore **surfaced in the row title and never converted into a state** (§3). The cost
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

`<label> · <host> · <cwd> · <pane> [· <beat age>]`, with a prefix of `[?]` or `[done]` when either
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
agb close-done [--rows PATH] [--dry-run]
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
(`[enter] attach   [s] shell   [q] quit > `), and attaches only if you ask it to:

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
| `~/.config/agbridge/rows` (Mac) | **temp + `rename()`** | its **content** is the whole key ↔ row bijection; content atomicity dominates |

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

`~/.config/agbridge/config`, `key = value`, read lazily and **never on the hot path**:

| Key | Used by | Meaning | Default if unset |
|---|---|---|---|
| `statedir` | all | overridden by `$AGB_STATEDIR` | `~/.agbridge` |
| `mac_id` | `bridge`, `status-line`, `doctor` | names `bridge/<mac-id>.beat`; generated at install. `feed` takes it as an argv, not from config — it is spawned by the bridge | none; `bridge` refuses to start, `status-line` falls back to the newest `bridge/*.beat` |
| `feed_host` | `bridge` | ssh target for the feed | none; `bridge` refuses to start |
| `agb_remote_path` | `bridge`, `prune --via-ssh` | path to `agb` on the farm, **absolute** | `bridge`: `/opt/agbridge/agb`. `prune --via-ssh`: `agb.sibling_path("agb")` — the running tree's own `agb` |
| `remote_python` | `bridge`, `prune --via-ssh` | farm-side interpreter, **absolute** | `bridge`: `/bin/python3`. `prune --via-ssh`: `sys.executable` |
| `jump_host` | `pane`, `bridge`, `prune --via-ssh` | for machine #3. All three consumers drop it when the hop would go through the target or through the host they are already on (`agb_mac.jump_for`, `pane_settings`, `prune_jump_host`) — `install.sh` copies the Mac's `--jump-host` into the farm's config, where that is the normal case | none |
| `host_<name> = <ssh-target>` | `pane`, `prune --via-ssh` | a record's `host` is a hostname, not an ssh alias | the hostname, used as the ssh target |

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

## Resolved design questions

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

  ⚠️ **Do not point it at a scratch volume without checking the purge policy.** Scratch and
  `/tmp`-like volumes commonly reap files by age, which would silently delete the `.state` of a
  long-idle session — manufacturing exactly the removals §2 refuses to infer. The tool cannot detect
  this; it looks identical to a session that ended.

Deliberately still open, because they need a second machine or a live agterm to answer:
the verbatim `agtermctl` contract (see [`agtermctl.md`](agtermctl.md) — the invocation shape is
*assumed*, with fallbacks recorded), and real cross-host propagation latency, which the
marker-carries-the-key-list design should make immediate for a new key and bounded by the
directory-attribute cache for a new *host*.
