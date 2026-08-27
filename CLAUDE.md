# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```sh
python3 -m pytest tests/ -q                                    # full suite (2622 tests, ~80 s)
python3 -m pytest tests/test_hook.py -q                        # one file
python3 -m pytest tests/test_hook.py::test_beat_refresh_is_throttled -q   # one test
python3 -m pytest tests/ -q -k "prune and not ssh"             # by expression
sh -n install.sh                                               # shell syntax check
```

There is no build step, no linter config, and no dependency install: the tool is stdlib-only and
the suite needs only `pytest`. Tests require no network, no second host and no Mac — a remote
machine is simulated by writing entries whose `host` differs from the local one.

**Python 3.6.8 is the floor.** No f-strings with `=`, no dataclasses, no walrus, no
`subprocess.run(capture_output=)`. This is a hard target, not a preference: the tool runs on
cluster hosts whose interpreter you do not control.

## Architecture

### Three files, and why it is not over-engineering

| File | Loaded by | Contains |
|---|---|---|
| `agb` | **every** hook invocation | hot path, feed, identity, the shared unlink authority, primitives |
| `agb_mac` | `agb bridge`, `agb close-done`, `agb forget-rows`, `agb instances` | the Mac side: transport, watchdog, row bijection, rendering, instance discovery |
| `agb_ops` | `doctor`/`prune`/`pane`/`status-line`/`install-*` | operator and diagnostic commands |

Plus **`agb-claude`**, a standalone POSIX-sh script that is not part of `agb` at all: it starts
Claude Code in a named tmux session. It exists because the session name is resolved once, at an
agent's first hook, so it has to be set before the agent starts.

It also **mints the row before Claude runs** — the session's shell hooks, then `exec`s Claude —
which is worth understanding because the same trick is available anywhere. ⚠️ **`exec` preserves pid
*and* starttime**, so the identity the shell records *is* the agent's a moment later and `bind_key`
**adopts** the key rather than minting a second one. Get any of three things wrong and you get two
rows instead of one: hook outside the new pane (wrong anchor), drop the `exec` (different pid), or
drop `AGB_AGENT_PID=$$` (a pid-less entry, which adopts but is then reapable only by `agb prune`).
The state is `completed` — a session at an empty prompt is waiting for you — and it raises no
banner, because the finished-turn banner measures from a preceding `active`.

And **`agb-ralphex`**, the same idea for a *supervisor* rather than an agent. ralphex runs a fresh
Claude per task, and a key is minted per **agent**, so ten tasks are ten rows with the same label and
a banner apiece. This gives the plan its own row — `agb hook active` before, `agb hook completed`
after — so the finished-turn banner fires once and names the plan. ⚠️ **The marker lives in its own
tmux session, and that is the design, not tidiness.** The anchor is `(host, tmux-server-pid, %PANE)`
and every Claude ralphex spawns inherits `$TMUX_PANE`, so a marker sharing that pane loses its key to
the first task that hooks and its closing hook mints a *new* row instead of closing its own —
measured, not assumed. It also proves `agb hook` needs no Claude: it is a command that writes a state
file, and nothing about the wire cares what produced it.

Two more launchers copy that recipe, and each is worth knowing for what it proves. **`agb-codex`**
does it for Codex — which fires **no** agbridge hooks at all, so the pre-mint is not merely earlier
than the first hook, it is the *only* thing that ever gives that agent a row, and the row never
leaves `completed`. ⚠️ **`AGB_CODEX_CUSTOM` replaces its `codex` command line entirely**, so the
agent can be started through a scheduler or pool launcher whose spelling is site-specific and does
not belong in a public file; it is embedded in the argv tmux is *handed*, never inherited, because a
session created against an already-running tmux server takes its environment from the **server's**.
**`agb-tmux`** does it for a bare shell or any command, which is how a long build gets a row.

**`AGB_CLAUDE_CUSTOM` is the same seam for `agb-claude`, and it is NOT the same feature.** Both take
two placeholders — `{}` for this invocation's agent flags, spliced verbatim, and `{env}` for the
identity a remotely-launched agent must report. ⚠️ **`{env}` exists because Claude hooks and Codex
does not.** A Codex started on another machine can never disturb its row, since it never writes one;
a Claude started there resolves a *different* anchor and mints a second row, on a host the Mac has no
mapping for. `AGB_HOST` alone does not fix it — `bind_key` then finds a pid that does not match and
**replaces** the index, orphaning the first row — so `{env}` sets `AGB_AGENT_PID=none` too, and a
pid-less hook adopts. ⚠️ The price is that the entry can no longer be reaped by proof of death, so
such a row outlives its job until `agb prune`; use `{env}` only when the launcher really is remote.

⚠️ **`agb-peer-setup` is the tool that writes that roster**, and the thing it exists to get right
is not the menu. `<row>` cannot be the row's title (`label · host · cwd · pane · beat`, and a
roster line is split on **whitespace**) and must not be its id (`agb-refresh` re-mints every one),
so it takes the **label** — after stripping `[?] `/`[done] `, without which every stale row is
unpickable — and confirms it resolves to **the row you picked**, since an id-prefix match beats a
label match and can be a different row. It withholds the "use the row's own host" option whenever
`host_<name>` remaps that host, because the relay uses `--host` **verbatim** and would otherwise
produce a roster that parses, validates, prints a working next command and never delivers. And it
writes through a **byte gate** with an **ungated** recovery-draft writer beside it: routing the
recovery through the gated one would raise `RosterConflict` from inside the conflict handler and
destroy the draft it was called to save. `docs/design.md` §6.

And **`agb-peer`** — two agents talking to each other. ⚠️ **It is not on the wire, and that is the
thing to hold on to**: it never reads or writes a session record. It reuses agterm rows as
*terminals* — a Mac-side `relay` reads each participant's screen and types into the other's, while a
farm-side `agb-peer send` stashes the message in a **tmux pane option** and rings a doorbell, the
pane's **window name**, which the relay already sees in the status bar it is reading anyway. It
touches the statedir only when the peer's tmux is unreachable: `<statedir>/chat/<id>.msg` plus a
*printed* doorbell, there being no window to rename. ⚠️ **That printed marker has to reach the
agent's visible screen**, which is why `skills/agb-peer/SKILL.md` tells the sender to repeat it in
its own answer — an agent UI folds command output behind a `ran N commands` summary, and the message
then sits unread with no error at either end. Measured, twice.

⚠️ **An agent can ask who it is talking to** — `agb-peer who`, answered by the relay over the
channel that already exists: the agent sends the single word `who` to the reserved name `relay`, and
the relay types the membership back. ⚠️ **The answer arrives on a LATER TURN**, not as that command's
output, and silence means either no relay or not-a-participant — indistinguishable, and neither
worth retrying. ⚠️ **Only that exact word is answered**, which is a loop guard rather than fussiness:
`SKILL.md` tells an agent to reply to anything arriving as `[chat from …]`, and the answer looks
exactly like that. Publishing a roster for agents to read was tried three times and abandoned —
there is no per-agent identity on the file transport, and `try_deliver` already had the primitive
that removes the question. `docs/design.md` §6.

⚠️ **Its participants can be a FILE, re-read while it runs** (`--roster`), which is what makes
attaching and detaching an agent possible without a restart. Three rules from it are worth carrying,
because each was a bug first. **Membership is a question about the roster, not about what currently
resolves** — a name with no row yet has its mail *held*, and using the resolved map to answer it
silently discarded every message to an agent that had not booted. **`seen` records what was read**,
not what was intended, so a failed fetch retries instead of marking a participant caught-up having
read nothing. And **a joiner is *primed***: its pane's existing content is discarded rather than
delivered. ⚠️ In that last one, *detached* is not *nothing announced* — a menu hides the status bar,
so a doorbell can be **invisible rather than absent**, and every `agb-refresh`-re-minted row comes
back detached, which makes it the ordinary case rather than a corner. `docs/design.md` §6 has the
rest, including why the prime retry is bounded and why giving up **delivers**.

And **`agb-dashboard`** — agterm's view-only **grid** of live rows, opened by *label*. ⚠️ **The
thing it exists to get right is not the grid, it is the refusal.** When only *some* cells resolve,
agterm exits **0**, opens the grid without them, and names the casualties on **stdout alone** — so
the honest-looking grid is the one missing the agent you opened it to watch. This is the one place in
the family where the exit status is deliberately not trusted, and the refusal **closes the grid
first**, because `unresolved:` is printed *after* it is already up. ⚠️ **A cell is never a bare id**:
agterm's cap is nine **panes** and a bare id takes every pane of its session, so an explicit pane
turns a cap that counts panes into one that counts **agents** — which is the only thing that makes
the preflight exact instead of a guess. One function, `dashboard_cells`, spells that for this script
and for the relay, with an AST guard spanning both files.

⚠️ **Its policy is the opposite of `relay --dashboard`'s, and both are right.** *The relay's grid is
an adjunct to a message pump; `agb-dashboard`'s grid is the point.* So the relay names an
unresolvable member and grids the rest, while this opens nothing at all; the relay **follows**,
re-resolving every tick, while this is a one-shot with a foreground hold and says so in its own
output. ⚠️ agterm has **one** grid and no ownership token, so each closes only a grid *it* opened and
running both at once is documented as unsupported rather than defended against.

⚠️ **The name was argued three ways and a future reader will re-open it.** Not `agb-peer watch`:
watching rows is not a peer-chat activity — you might grid an agent and the build it kicked off, with
no relay anywhere — and **the resolver living in `agb-peer` today is an implementation smell that
must not be allowed to choose the user-facing noun.** Not `agb-watch`, the strongest counter-proposal,
which lost on discoverability: agterm calls this a dashboard and so do our own docs, and nobody
hunting for how to see a grid searches for *watch*. Extracting the resolver was deliberately **not**
done here — it looks like two small functions and is not, and a boundary is worth drawing once two
callers have shown where it is. `docs/design.md` §6.

⚠️ **Not verified against a live agterm.** agterm's own `dashboard` behaviour was measured
(`docs/agtermctl.md`); agbridge's use of it — this command, and the `relay --dashboard` fixes beside
it — has tests and no live run. ⚠️ There are **four** of those, not three: the grid left on screen at
exit, the grid that went on **following its membership** wrongly (a departed participant's cell
stayed up, and an `agb-refresh` moving every id changed nothing the relay compared), the unmarked
partial grid, and the `scratch` participant that stopped the whole grid opening. Two enumerations of
them in this repo dropped the membership one — including the live-verification list in `README.md`,
so the hardest defect to *see* was the one nobody was told to look for.

⚠️ **And a fifth thing, found in review rather than in the plan: the grid calls could RAISE.**
`Ctl.dashboard` and `Ctl.dashboard_close` return a status for an agtermctl that ran and refused;
`_spawn` **raises** when it cannot be started at all. The relay died mid-delivery and
`agb-dashboard`'s strict path lost the message naming the grid it had left on screen. The general
lesson is the one to carry: a "this can never stop a message" property must be enforced at **every**
call, because a docstring asserting it is not a mechanism — and the guarded call and the unguarded
one were three lines apart.

`agb hook` runs on **every Claude Code tool call**, over a network filesystem. `agb` has no `.py`
extension and runs as `__main__`, so **CPython caches no bytecode for it** — the whole file is
re-parsed every single invocation. (This is a property of being `__main__`, not of the missing
extension: a `.py` script run directly is equally uncached, while an extension-less file loaded
through `importlib` *is* cached. That is exactly why the two siblings are cheap on reload.)

The siblings are reached by a **one-statement lazy hop** from `agb`'s dispatch and are never opened
by a hook. `tests/test_mac_split.py` proves this directly — it `chmod 000`s the sibling and runs a
hook, rather than asserting a timing.

`conftest.AGB_PARSE_BUDGET` caps `agb`'s source size, **in characters**. **Raising it is almost
always the wrong answer** — pay for new code by moving other code into a sibling. It has been raised
**twice**, each time on a measurement; the comment above the constant records both and the bar. The
second raise (+716, for `agb instances`' dispatch arm) is worth reading before proposing a third: the
route that avoided it would have forced an `agb_mac` → `agb_ops` edge that does not exist and is
asserted absent, so the cap was doing its job by making the architectural cost visible rather than by
being obeyed.

### The data flow

Agents write to a shared statedir; the Mac pulls over one long-lived `ssh`. Read
[`docs/design.md`](docs/design.md) before changing any of this — it is the authority and is
reconciled against the implementation.

- `sessions/<host>/<key>.state` — five bare lines; **its mtime is the beat**. Written *in place*
  because a rename would make the mtime uncontrollable.
- `sessions/<host>/<key>.json` — the record. Written temp+rename because torn reads matter.
- `gen/<host>.marker` — the live key list **as content**, temp+rename.
- `bridge/<mac-id>.beat` — the Mac's reverse heartbeat, touched every poll.

**The owning host is in the path.** That is what makes every marker rebuild a `readdir` with zero
opens, and makes "only sweep your own entries" structural rather than a runtime check.

### Everything the bridge tells agterm

Every one of these is a subprocess call through `_run_command`, and every one is best-effort — a
failure is written to the log and returned, never raised. `docs/agtermctl.md` tags each clause with
its evidence class; nothing here may be added from a guess.

| When | Call |
|---|---|
| a key with no row | `session new --name … --cwd … --command … --no-select [--workspace-name …]` |
| identity or marker changed | `session rename <title> --target <row>` |
| status changed, or every 30 s | `session status <state> --target <row> [--blink]` |
| transition **into** `blocked` | `notify <body> --title … --target <row>` |
| transition **into** `completed`, if the turn ran ≥ `notify_on_completed_after` | `notify <body> --title … --target <row>` |
| `agb close-done` | `session close --target <row>` |

Three of these are gated on a **transition**, not on the level state, and for the same reason: they
are events, not renderings. `--blink` fires only on a real move into `active`; the banners only on a
real move into `blocked` or `completed`. All would otherwise repeat on every snapshot and on the
30 s re-assert.

The banners have config gates — `notify_on_blocked` and `notify_on_new_row`, **on by default**, read
through `agb_mac.config_flag` (which lives there and not in `agb`: the hot path never reads a flag,
and `agb` has no bytes to spare). It spells out what counts as false, because `"0"` is truthy in
Python and a key that silently means its opposite is worse than one that does not exist. Whether the
Dock icon *bounces* is agterm's own setting, not ours — which events are worth announcing is this
tool's business; how loudly the machine interrupts you is the machine's.

**`notify_on_completed_after` is a number, not a flag**, read through `config_seconds` beside it, and
the number *is* the switch — `0`/`off`/`no`/`false`/negative disable it, absent gives the default.
Those two states must stay distinguishable or the default becomes unreachable once anybody writes the
key. It is thresholded because `blocked` is rare while a turn ends **every time an agent answers
you**: ungated it announces the "yes" you typed three seconds ago, and there is no "only when I'm
away" to fall back on, because agterm raises the banner and bounces the Dock even for the row you are
looking at (it suppresses only the unseen badge).

⚠️ **This one's transition memory is a `pop`, and that is why it is safe.** `RowRenderer.working`
maps a key to the feed-clock instant the turn was first seen; the `completed` branch pops it, so a
repeat report finds nothing. Nothing but `_render_upsert` and `_render_remove` ever writes it, so no
*repaint* can move it — which makes it disconnect-immune and burst-immune **structurally** rather
than by a rule someone has to remember. That is the `applied`-as-a-gate trap below, and this is the
worked example of a case where it cannot fire at all. `_render_remove` pops too: a removal ends the
turn, or an agent removed while working and re-asserted as finished would be announced with a
duration spanning the removal.

`agb pane` adds two more calls from `agb_ops` — `session split` / `session scratch` plus
`session type` — which is why there are **three** doors to `agtermctl` rather than one.

### A row's title is composed, not fixed

`row_fields` (Mac side, per instance) names which of `label`/`host`/`cwd`/`pane`/`beat`/`key` render
and in what order, `cwd:base` shortening the directory. Default `label,host,cwd,pane,beat` — today's
title exactly. ⚠️ **Three things the config deliberately cannot reach**, each with its own test:

- **`[?]` and `[done]` are prefixes, not fields.** `idle` renders as *no glyph*, so without the
  marker a dead row is pixel-identical to a live idle one. A cosmetic key must not disable a safety
  property.
- **The title is never empty.** A field list that renders nothing is reachable — `row_fields = beat`
  on a healthy agent, `pane` on an agent not in tmux — and an empty body makes `_title` skip the
  rename entirely, leaving agterm's own name on the row. There are **two** fallback chains and they
  are not duplication: `label` itself is `label or key or "?"` (keeping the default byte-identical on
  a record with no label), and the *join* falls back the same way.
- **A field with no value is dropped, not rendered as an empty segment.** Invisible in any test whose
  record has every field populated, which `wire()`'s does.

⚠️ **The parser is where four review passes found their defects**, never the feature: the strip is
per *component* (`cwd: base` must work), empty items are skipped *after* stripping, and it never
returns an empty list — `row_fields = ,` reduces to nothing and would otherwise render an empty
title. An unknown field refuses the **whole** list, which is the only failure mode a user can
actually notice.

### The status vocabulary is closed

`active | blocked | completed | idle`, and **there is no `unknown`**. Agents report only the first
three (hooks: `UserPromptSubmit`/`PostToolUse` → `active`, `Notification` `permission_prompt` →
`blocked`, `Stop` → `completed`). `idle` is emitted by the *bridge alone*, for two renderings that
must not be confusable with a live agent, which is why each also carries a title prefix:

| Bridge emits `idle` | Title prefix |
|---|---|
| feed went quiet / connection lost | `[?] ` |
| agent removed (finished, reaped, pruned) | `[done] ` |

`idle` renders as **no glyph**, so a row showing nothing is either one of those two cases or a row
that has not been painted yet. "No glyph" is never evidence about the agent.

## Invariants that are easy to break

These are not style preferences. Each one has a test, and most were re-learned the hard way.

1. **Liveness is proven, never inferred.** No age is ever converted into a status. `liveness()` is
   three-valued (`DEAD`/`ALIVE`/`UNKNOWN`) precisely so "not provably alive" cannot collapse into
   "dead". Collapsing it is the delete-everything bug.
2. **Removal requires positive proof.** A short/malformed/empty read is *no information this poll*,
   never "gone". `ENOENT` on a random key name is the one carve-out — it is a positive server
   answer, and random keys mean no cached negative dentry can manufacture it.
3. **There are exactly two session unlink sites**: `reap_entry` (lexically gated by
   `proof_of_death`, own-host only, enforced by a raise not an `assert` — `python -O` strips
   asserts) and `prune_remove` (gated by a human typing a key). A structural test pins this list.
   ⚠️ **"Own-host" is `real_host()`, not `own_host()`, and the difference cost eleven live rows.**
   `own_host()` honours `$AGB_HOST` — an *identity*, asserted, and legitimately so, since a remotely
   launched agent (`agb-claude`'s `{env}`) has to report the row it belongs to. But the guard was
   spelled in terms of it, so the override sat on **both sides of the comparison** and could never
   fire: a `{env}` agent swept the statedir of the host it was impersonating, adjudicated that
   host's pids against its own machine's namespace, got `ESRCH` for every live one, and reaped them
   all — then dropped their idx anchors, including its own, so its next hook minted a duplicate row.
   `real_host()` (`uname`, unoverridable) answers *whose pid namespace is this*, and
   `host_is_observed()` is the gate. `AGB_HOST_LOCAL` is an opt-**in** with no opposite, because a
   process cannot tell "standing in for another host" from "renamed" by looking at itself — so the
   dangerous direction is the one you have to type. `docs/design.md` §2 has the measured trace.
4. **The hot path touches exactly two files** on a no-change invocation and imports no `json`. The
   `json` import lives inside the transition branch. Config is never read on the hot path.
5. **The hook never writes to stdout** — Claude Code injects `UserPromptSubmit` stdout into the
   prompt — and **always exits 0**, leaving a breadcrumb instead.
6. **Freshness-critical reads are `open()` + `os.fstat(fd)`, never `os.stat`.** On NFS, `stat` is
   served from the attribute cache; close-to-open forces a real `GETATTR` on `open()`. Measured:
   5 × `os.stat` → 1 GETATTR RPC, 5 × `open`+`fstat` → 5–6 GETATTRs.
7. **Never `readdir` another host's session directory.** Directory listings can be served stale for
   up to the attribute-cache lifetime. Cross-host discovery goes through the marker's *content*.
   Own-host `readdir` **is** authoritative and is what marker rebuilds use.
8. **All ages are computed in one clock domain.** `beat` is set with `os.utime(path, None)` so the
   *server* stamps it; the feed derives its `now` by `fstat`ing the beat file it just wrote.
9. **A snapshot may only authorise removals when it is complete.** The feed sets `complete` false
   whenever any read failed, and re-emits a full snapshot once reads recover (`FeedState.owed`).
   The bridge treats only `complete is True` as authority — a truthy string is not.
10. **A `[done]` row can be rebound** when the feed positively re-asserts the key. The removal was
    never proof, so refusing to rebind strands a live agent's row forever.
11. **`agb bridge` has no statedir default.** `agb`'s own `~/.agbridge` is right for a process on
    the farm and wrong for the Mac, which would resolve `~` locally and ship a path meaning
    something else. There is no value that side can invent, so it asks. This shipped broken once.
12. **Everything the Mac side owns derives from ONE path: the config.** `rows`, `placements` and the
    `host_<name>` table are `dirname(<config>)/…` or keys inside it, which is what makes a second
    instance (`agb bridge --config <path>`, `install.sh mac --instance <name>`) a directory rather
    than a concept. Anything new that a bridge persists on the Mac belongs there too. ⚠️ The row's
    own command must carry `--config` for a non-default instance, or `agb pane` resolves `--host`
    through the **default** config and click-to-attach reaches the wrong machine with every test
    green; `pane_argv` emits it only when `normpath` says it differs, so default installs are
    byte-identical. ⚠️ **Corollary, and it has been got wrong twice: "the same instance" is a
    comparison of the resolved DIRECTORY, nothing more.** The basename never reaches `rows` or
    `placements`, so an equivalence test that keeps it — or that compares the path as text — is
    narrower than the map it guards, and the failure always has the same shape: the right map under
    the *wrong* label, i.e. bounce instance A while forgetting B's bindings under B's live bridge.
    `agb-refresh`'s `same_map` is the one place that comparison lives, over `config_map_dir`, which
    is the canonicaliser it is spelled in terms of. ⚠️ **But *matching* the
    map and *choosing* between the matches are two questions**, and running them together is how the
    fourth instance of that failure arrived: the basename and "does this plist declare a config or
    merely imply one" break the tie among candidates that have already matched, so the job naming
    this exact file wins over one that only shares the directory instead of the winner falling out of
    `*.plist`'s collating order. Ranking is not matching, and it narrows nothing.
    ⚠️ **And the same comparison is owed to the PROCESS side, where a pattern cannot give it.**
    `agb-refresh`'s liveness poll asks `pgrep -f` about a running bridge, and `pgrep -f` matches a
    regex against whatever spelling that process was *started with* — the far side of a regex match
    cannot be canonicalised, so a bridge over this map spelled `<dir>/./config` matches nothing and
    reads as gone. The fifth instance of the same failure arrived exactly there. The answer is not a
    cleverer pattern: it is `ps -ww -o args=` on the pids `pgrep` returns, and `same_map` on what it
    says. Anything that has to decide *whose* a running bridge is belongs on that route.
    ⚠️ **And WHICH `--config` on that line is `parse_bridge_args`' answer, which keeps the LAST** —
    it reads value flags into a dict with no duplicate check, so a repeat overwrites. Reading the
    first (the sixth instance of the same failure) attributes
    `… bridge --config /old --config <ours>` to `/old`. Both readings are kept anyway, because `ps`
    flattens the arguments and `--config "/a --config /b"` is one path containing that text: every
    occurrence is offered, since an over-match costs a bounded wait and an under-match is
    `forget-rows` under a live bridge.
    ⚠️ **And `--config=<path>` is that same flag** — `parse_bridge_args` partitions on `=` before
    looking the name up — so "every occurrence" spans both spellings, **in position order**. The
    seventh instance arrived here: two `case` arms, one per spelling, pick by *arm* order, so an
    inline occurrence before a space-form one was discarded whole — and it is the one the parser
    keeps under the one-argument reading. The plist reader knew only the two-word form, and answered
    `""`, which downstream means *the default config* — so an instance's job claimed the default map.
    One marker (`--config`), cut at its first occurrence, with the next character saying which
    spelling — or that it is not the flag (`--configs/b` inside a value). That is the `ps` reader's
    answer and still is; the plist side gets both spellings from `parse_bridge_args` itself (below).
    Any new reader of that flag owes both spellings. And when a harness proves such a scan safe,
    check it composes the input the ambiguity is *about*: the permutation check that cleared this
    built its lines only out of separate flags, the one reading in which the loss is harmless.
    ⚠️ **And `--config` is not the only flag that says which map a bridge holds** — `--rows`
    overrides the rows file the config would have chosen (`render_settings`), so a bridge or a plist
    naming this map only through `--rows` holds it too. Adding a flag to what is *offered* can only
    add "ours" answers, so it is safe by construction; the reverse is not, and `--rows` deliberately
    does **not** answer the *untagged* question, since such a bridge still resolves the default
    config for its placements.
    ⚠️ **And the eighth instance is the one worth generalising: ` --config ` BYTES ARE NOT A FLAG.**
    `ps` flattens argv, so `--workspace "farm --config /x"` prints a line identical to one carrying
    the flag; the plist side has no such excuse, since `ProgramArguments` *is* the argv and an
    element consumed as a value is not a flag at any distance. Two rules follow, and they are
    different rules: where the input is genuinely ambiguous, **the undecidable case must resolve to
    "ours"** (a bounded 10 s wait, not a `forget-rows` under a live bridge) — here, a `--config` is
    proof only when no other value-taking flag precedes it; where it is *not* ambiguous, **call the
    parser** rather than imitating it. The `ps` side cannot (argv is already flattened) and so still
    needs the list of flags that consume the next argument, which is why `BRIDGE_VALUE_FLAGS` remains
    a third cross-file agreement (invariant 14); the plist side does not, and stopped.
    ⚠️ **And the twelfth instance is why "simulate the parser exactly" was the wrong instruction:
    A SIMULATION CANNOT SIMULATE A REFUSAL.** `plist_arg` walked the array for five rounds, each
    round adding one of `parse_bridge_args`' properties in a second language, and the class it could
    never reach was an argv `agb bridge` **rejects** — `--config=` with no value, a stray positional,
    an unknown option, a `--watchdog` that is not a number. Such a job starts no bridge and holds no
    map, and the walk answered a real-looking config for all four: a dead `KeepAlive` job as an
    exact, declaring, rank-1 claimant outranking the live one. Adding the rejection rules would have
    needed the *boolean* flag list as a second agreement plus two numeric validations — a **wider**
    agreement and a sixth round. So the post-`bridge` elements go to `agb_mac.parse_bridge_args`
    itself, loaded by path from beside `$agb` (no new dependency: `agb forget-rows`, step 2 of the
    same script, already loads that file). A tree missing it is **exit 3, fatal, naming `--agb`** —
    not "this plist says nothing", which would bounce an unclaimed job and then fail the forget
    anyway. ⚠️ A refused argv answering "no `--config`" is correct (it holds no map) but not free:
    `agb-refresh --instance <name>` against a plist hand-edited into a refusal falls back to the
    conventional config path. Ranking it *below* a working claimant is unavailable — with the argv
    refused there is no parsed value to rank, and recovering one is the simulation again.
    ⚠️ **And "simulate the parser" includes simulating WHERE THE ARGV IS.** A plist is not an argv,
    only one of its keys is; `ProcessType`, the log paths, `WorkingDirectory`, an
    `EnvironmentVariables` value and a `WatchPaths` array all carry strings too. Reading the whole
    file let a hand-edited pair *after* the array overwrite the real value (last-wins, which is
    right *inside* argv) and one *before* it manufacture a config for a job whose argv has none —
    both silent, both the wrong-job bounce. ⚠️ Too *tight* is not the safe side either: a missed
    `ProgramArguments` demotes that plist to "carries no `--config`", so a named instance's label is
    never found and the default job gets bounced instead.
    ⚠️ **And the ninth instance is the one that ended the sequence: A HAND-ROLLED XML TOKENIZER IS
    NOT AN XML PARSER, and four consecutive review rounds found exactly that, each round finding the
    rule the previous round's rule did not have.** Whitespace inside a tag (`<string >`, `</array >`
    — both valid XML, and the second let a `WatchPaths` array overwrite the config), a comment
    splitting a value across lines (`<string>/tmp/a<!--`⏎`-->b/config</string>` *is* `/tmp/ab/config`
    — so "a comment can only ever hide argv" was true only of a comment sitting *between* elements),
    CDATA, PIs, DOCTYPE, character references, minification, nesting. `plist_arg` now parses with
    **`plistlib`** — stdlib on both sides, and `agb-refresh` already requires a python3, so it is no
    new dependency — which makes "only `ProgramArguments`" structural (ask a dict for one key) and
    retires the whole written-down limitation list: binary plists, CDATA-carried values, character
    references in a flag *name*, DOCTYPE, multi-line tags. What remains is a file that is not a
    plist, which exits **2** and is skipped rather than standing for the default config. ⚠️ Two
    traps if you touch it: `plistlib.load` **sniffs** the format and rejects a file starting with
    `<!DOCTYPE` (retry with `fmt=FMT_XML`), and `$python` must be resolved **before**
    `bind_label_to_config` or every plist answers nothing; the inline `-c` program must also be
    **pure ASCII** and its output written as UTF-8 **bytes**, since `-E` does not touch `LC_ALL`.
    The acceptance bar is a **differential corpus** of forty-odd hand-editable plists checked against `plistlib` *and*
    `agb_mac.parse_bridge_args` — authorities that are not `agb-refresh`'s own code.
    ⚠️ **And the thirteenth is where the reader ended up: it is not in `agb-refresh` at all any more.**
    `plist_arg` is two lines of shell calling `agb instances --plist <path> --arg <flag>`; the parse,
    the `plistlib` sniff-retry and the `parse_bridge_args` call live in `agb_mac.run_instances`.
    `bind_label_to_config` is **byte-identical** to before — the loop, `same_map`, the five ranks,
    `nclaim`, the claimant warning, the `${rows:-$config}` input and its twelve named tests all
    survive, because *the ranking in shell was never what broke; the plist parsing was.* Porting the
    ranks too was proposed and rejected: `same_map` would be re-implemented in Python (this
    invariant's whole subject), `config_map_dir` is fail-closed via `cd -P … && pwd -P` while
    `os.path.realpath` never fails (silent widening, the direction that bounces the wrong job), and
    the multi-claimant warning would die. Two things follow that are easy to get wrong. **The 0/2/3
    contract had to be inherited, not re-invented**, because its two consumers read an empty value
    *differently on purpose* — `bind_label_to_config` reads it as `$DEFAULT_CONFIG`, the named-instance
    path reads it as the `<dir>/<name>/config` convention — and one table for both restores this
    invariant under a new name. And **`agb` answers an unknown command with exit 2 and empty stdout**,
    byte-identical to "this plist says nothing", so an old installed `agb` would make every plist
    silent and succeed on the wrong instance: `agb instances --probe` must answer the literal
    `instances-ok`, **stdout-compared**, before any plist is read. A status alone cannot see
    `--python /bin/echo`.
    ⚠️ **And the tenth instance is about that corpus, not about the reader: `ProgramArguments` is not
    the bridge's argv, it is the whole command line.** The real array is `<python> -S -E <agb> bridge
    --config <path>`, so four elements go by before `agb bridge` sees anything, and `agb` reads its
    command from `argv[1]` — a flag in *front* of the command name **is** the command name
    (`unknown command: --config`), a job that starts no bridge and holds no map. Walking the whole
    array made that dead job an exact, declaring, rank-1 claimant that outranked the live one. The
    corpus missed it because it modelled argv as `["bridge", …]`, and so did the fixture every other
    plist test goes through: **forty cases proved on an array four elements shorter than any that
    exists.** Nothing fails when a harness is simpler than reality, which is why both now have a
    guard of their own — the corpus runs every case in *both* shapes, and the fixture's argv is
    compared against `dist/com.agbridge.plist` itself.
    ⚠️ **And the eleventh: "I could not answer" is not "the answer is nothing", and there are four
    statuses here, not two.** `plist_arg` exits 0 (answered), **2** (this file says nothing), **3**
    (the parser could not be loaded from beside `$agb` — a statement about `--agb`), or something
    else (the *reader* failed — a statement about `$python`, not about the plist). Folding the last
    into the second made `--python /bin/false` read every plist as silent and bounce the default job
    under a live instance bridge; it is fatal now, at all three call sites, spelled once in
    `plist_read_ok` and **called from the parent shell** (a wrapper inside `value=$(…)` would `exit`
    only the subshell). A status cannot see `--python /bin/echo`, which exits 0 printing its own
    arguments, so an `import plistlib` probe asks a question with a known answer once, up front. And
    exit 2 itself covers two different questions — **a missing plist and an unreadable one** — where
    the conventional path is a fall-back for the first and a *guess* for the second; the guess
    repaired a map that never existed and reported it empty, so it refuses and asks for `--config`.
    `docs/design.md` §5, *One Mac, several instances*, is the authority.
13. **`agtermctl session split` and `session scratch` are used as `on`, never `toggle`** — either
    key can be pressed twice, and a toggle would close the pane the second time — and the pane must
    exist before anything is typed into it (`--pane right` errors otherwise, and `--select` is
    main-pane only; the same claim for `--pane scratch` is **ASSUMED** and unobservable, since
    `scratch on` always goes first). Recorded in `docs/agtermctl.md` and mutation-tested.
    `session scratch --command` is **deliberately unused**: it respawns an already-open scratch, so
    a second `[d]` would destroy a shell in use.
14. **Eight cross-file agreements have no single source of truth, and all fail silently.** `agb` is
    Python under a character cap; `install.sh` and `agb-refresh` are POSIX sh; none of the three can
    import the others, so each spells the shared value itself.
    - **The default config path is spelled three times** — `agb.config_path()`, `install.sh`'s
      `DEFAULT_CONFIG_DIR`/`DEFAULT_CONFIG`, `agb-refresh`'s copy of the same pair. `pane_argv`
      emits `--config` only when the path *differs* from `agb.config_path()`, so a disagreement
      between the first two makes **every default install re-mint every row**, reporting success.
    - **`instance_ok()` exists in both shell scripts** and must accept exactly the same names. A
      name `agb-refresh` accepts and the installer refuses points at a plist that was never
      rendered; the reverse writes four things where they were not meant to go. The *messages*
      differ on purpose (one writes those four things, the other goes looking for three).
    - **`agb-refresh`'s `BRIDGE_VALUE_FLAGS` is `agb_mac.BRIDGE_VALUE_ARGS`**, because the liveness
      attribution has to know which `agb bridge` flags consume the argument after them: it asks
      whether a `--config` on a `ps` line could be inside an earlier flag's value. A flag missing
      from the shell copy stops it seeing that value as a place a `--config` can hide. ⚠️ **One
      reader, not two, and that is the point.** `plist_arg` needed the same list while it *walked*
      `ProgramArguments`; it calls `parse_bridge_args` now and takes the table out of it, so the
      agreement got **narrower** rather than wider. It cannot go away entirely: by the time `ps` has
      flattened argv there is no argv left to hand a parser.
    - **`~/Library/LaunchAgents` and the `com.agbridge` label space are spelled in all three** —
      `install.sh` renders the plist there under that prefix, `agb-refresh` defaults `$agentsdir` to
      it, and `agb_mac` now does too (`default_agents_dir`, `INSTANCES_LABEL_PREFIX`), because
      `agb instances`, `close-done` and `forget-rows` all have to discover instances without a shell.
      A disagreement is silent in the worst way: the shell sweep and the in-process sweep would visit
      **different sets** of instances, so one command repairs an instance the other cannot see. Note
      the label space is used for *two different questions* here and only one of them is this
      agreement — `bind_label_to_config`'s claimant guard and `_is_agbridge_instance`'s membership
      rule are deliberately different predicates (the second also accepts any plist running
      `<…>/agb bridge`), so a test comparing them for equality would be wrong.
    - **`install.sh`'s `PRINT_STATEDIR_NONE` is `agb_ops.PRINT_STATEDIR_NONE`** — the exit status
      `agb install-config --print-statedir` answers for *this file carries no statedir of its own*,
      as against `1` for *I could not read it*. The installer swallows the first and dies on the
      second, so a disagreement collapses the two back into one: every unreadable config reported as
      "carries none to adopt", and the operator sent after `--statedir`. The only one of the five
      that is a **number**, which is also why it is the easiest to get wrong quietly.

    - **`agb-peer`'s `PANE_WORDS` is the union of `agb_ops`' `PANE_QUIT_WORDS`, `PANE_SPLIT_WORDS`
      and `PANE_DRAWER_WORDS`** — the words `agb pane`'s menu acts on. The relay must refuse to
      *deliver* a message that reduces to one of them, because a detached row shows that menu and
      `q` would **close the row of a live agent**. `agb-peer` is a standalone script that cannot
      import `agb_ops`, so it spells the union itself; a word added there and not here is a message
      that silently destroys a row instead of arriving.

    - **`own_host()`'s resolution is spelled again in `agb-claude` and `agb-codex`** —
      `$AGB_HOST`, else `uname -n`, domain stripped. Their `{env}` placeholder has to name *this*
      host, because an agent started on another machine reports whatever it is told to. A
      disagreement raises nothing anywhere: the agent reports a host with no `host_<name>` mapping,
      and you get a **second row** beside the one the wrapper just minted — which looks like the
      wrapper being broken, not like a hostname being spelled two ways.

    - **The `sys.modules` key `agb_peer` is spelled in THREE places** — `agb-peer-setup`'s
      `PEER_MODULE`, `agb-dashboard`'s, and `tests/conftest.py`'s. Each loads `agb-peer` by path and
      registers it under that name, and each returns an already-registered module rather than
      re-executing it. A disagreement loads a **second module object with its own `PeerError`
      class**, so `except peer.PeerError` around a call into the other script silently does not
      catch and `setup.PeerError is peer.PeerError` fails — which reads like a loader bug rather
      than like a name spelled two ways. It was already cited as invariant 14 by `conftest.py` while
      this list did not contain it; adding `agb-dashboard` as the third speller is what made the
      dangling citation worth fixing rather than deleting.

    The first two are pinned by `tests/test_install_pkg.py` — the path agreement compares the
    resolved strings, the validator agreement compares the `case` **patterns**, not the bodies — the
    third by `tests/test_agb_refresh.py`, the fourth and fifth beside the first two in
    `tests/test_install_pkg.py`, the sixth by `tests/test_agb_peer.py`, which compares against the
    three tuples themselves rather than a copy of their contents, and the seventh by both wrapper
    test files, each comparing the substituted value against `agb.own_host()` itself. The eighth is
    pinned by `tests/test_agb_dashboard.py`, which compares all three constants **and** asserts the
    two scripts end up with the *same class object* — a string comparison alone would pass against
    two loaders that agreed on the name and still built two modules.

## Testing conventions

**Structural guards.** Many tests parse the source with `ast` and assert properties like "`json` is
never imported at module top level" or "only these functions unlink a session". Use the helpers in
`conftest.py` — `all_trees`, `functions`, `calls`, `toplevel_imports`, `reachable_from`.

⚠️ **Never write a structural guard as a substring grep of the source.** It will silently pass by
matching the explanatory *comment* that describes the prohibition. Four guards shipped green this
way before being caught. For the same reason, a guard that searches for a bare name (`_unlink_quiet`)
misses the qualified form (`agb._unlink_quiet`) that a sibling file must use — `reachable_from`
follows `agb.<name>` edges for exactly this reason.

**Assert non-vacuity.** A reachability guard must assert its walk actually ran (`assert "hook_apply"
in reachable`) before asserting what is absent; otherwise renaming the root makes it pass while
covering nothing. Same for loops: assert the collection is non-empty before asserting over it.

**Never fabricate a pid.** A made-up pid is almost certainly a *dead* pid, which silently converts a
liveness test into something else. Use `conftest.live_agent()` / `dead_agent()` — the latter forks,
exits, reaps, then verifies the pid is actually free.

**Mutation-check new guards.** Break the property, confirm a *named* test fails, restore. Every
review round in this repo's history found guards that passed vacuously; running the suite green
proves the tests ran, not that they hold anything up.

⚠️ **Two ways a mutation-check reports a false pass, both found while mutating `agb_ops`:**

- **Delete `__pycache__/agb_ops*.pyc` after writing mutated source.** The siblings are loaded *by
  path* through `importlib`, which unlike `agb`-as-`__main__` **does** cache bytecode — validated on
  (source mtime in **whole seconds**, source size). A mutation that only *moves* text is
  size-identical, and a rewrite inside the same second reuses the stale `.pyc`, so the test runs
  against the **unmutated** implementation and the check reads as a pass. It said so only by flipping
  between runs.
- **A mutation that MOVES a guard needs both edits.** Adding it at the new site without deleting it
  from the old one leaves the original firing, so the "mutation" is a no-op — and a no-op is
  indistinguishable from a guard nothing covers. Assert the anchor is unique and re-read the mutated
  file before running.

Restore from an **in-memory snapshot** and verify by `sha256`, never `git checkout` — a checkout of
uncommitted code silently reports deleted-implementation runs as passes. Commit before mutating.

⚠️ **Three harness facts make a renderer test vacuous, and none is obvious from reading it.** All
three cost a review round on the finished-turn banner:

- **`BridgeModel._upsert` drops a record identical to the last one** before the renderer sees it,
  and `wire()` has a constant `seq`. A "this is announced only once" test that re-sends the same
  record never enters `_render_upsert` at all, and passes with the code under test deleted. **Vary
  `seq` on every repeat upsert.**
- **`Harness.upsert` defaults to `now=NOW`.** A test that leaves the clock implicit puts every event
  at the same instant, which makes "measured from the first sighting" and "measured from the last"
  indistinguishable. **Pin every clock in any test about durations.**
- **`model.now` never returns to `None` once set** (`BridgeModel.apply` assigns only for a numeric
  value). A "no clock" test must therefore target the *first* event of a fresh harness; later ones
  inherit the previous value and prove nothing.

And the general form, which is the one worth carrying: **a test asserting that nothing happened needs
a companion that differs only in the variable under test.** Otherwise it passes against a feature
that can never fire, for any reason at all.

⚠️ **A cache or memo needs a test where the KEY differs, not only one where the hit is counted.**
`install.sh`'s `verify_tree` memo is keyed on `"$vpython $vagb"`, and its only test was a
`--dry-run`, where both callers ask about the same tree — so re-keying it on `"$vpython"` alone left
the whole suite green while a *real* install stopped proving `$dest/agb` at all and printed
`verified: … at <dest>/agb` anyway. The same shape as "assert the collection is non-empty": a test
that exercises one value of the key is a test the key does not appear in.

⚠️ **And reading a constant out of the implementation turns a loop into a tautology.** A table-driven
refusal test whose allowed set is `ops.PRINT_STATEDIR_ALLOWED` asserts that whatever the parser
permits, parses — so *widening* the constant is what the test was for and is exactly what it stops
seeing. Reading the constant is still right for the loop (a hand-kept copy drifts from the parser's
own tables instead); the fix is to assert **both**, with an equality against a literal set beside it.
Two opposite failure modes, and trading one for the other reads as a cleanup.

**Always pass `timeout=` to `communicate()`.** `conftest.communicate()` wraps this. Without it a
regression that wedges a subprocess hangs the suite instead of failing it.

## Hard-won environment facts

Non-obvious things that cost real time to discover. Verify before relying on any of them in a new
environment — several are version- or mount-specific.

- **`python3 -X importtime` is a silent no-op on 3.6.8** (exit 0, no output), so importtime-based
  assertions pass *vacuously*. The working import guard is `python3 -S -E -v` grepped on **stderr**
  for `^import 'json'`. Any such guard needs a negative control, or it passes because the harness
  never reached the branch under test.
- **`os.path.isdir` / `os.path.exists` swallow every `stat` errno**, not just `ENOENT` — `ENOTDIR`,
  `EACCES`, `ESTALE`, `EIO` all return `False`. Using one as an error-handling branch reports a
  broken filesystem as "does not exist yet". This shipped once and was caught in review.
- **`sys.stdout` is `strict`, `sys.stderr` is `backslashreplace`** — a CPython default, and the whole
  reason the two streams get different rules here. A machine-readable **value** must leave as UTF-8
  bytes on `sys.stdout.buffer` (`agb instances --arg`, `install-config --print-statedir`): `-E` does
  not touch `LC_ALL`, so through `sys.stdout.write` a non-ASCII path raises under `LC_ALL=C` — turning
  a query's exit status into the one meaning *I could not read it* — and, worse, *succeeds*
  transcoded under ISO-8859-1. **Prose** may stay text on the injected `out` seam precisely because
  stderr's handler cannot raise and so cannot change an exit status. `--print-mac-id` is the exception
  and does not generalise: `valid_mac_id` refuses anything outside an ASCII alphabet.
- **`os.utime` accepts an `O_RDONLY` fd**, which saves a second `LOOKUP` on the hot path.
- **`/proc/<pid>/exe` returns `…/tmux (deleted)`** after the binary is upgraded under a running
  process. It still passes a naive basename check, then fails to exec. Strip the suffix and require
  `os.access(X_OK)`, with a `$PATH` fallback.
- **tmux `select-pane -t %N` does not switch the session's active *window*.** Two agents in two
  windows of one session both land on whichever window was last active. `select-window -t %N`
  accepts a pane id; `attach-session -t %N` resolves the session owning that pane. (Verified on
  tmux 3.5a.)
- **`$TMUX` is session-level, not pane-level.** Per-pane identity comes from `$TMUX_PANE`.
- **`socket.gethostname()` returns the FQDN** on many clusters. `own_host()` uses `os.uname()[1]`
  and strips the domain — importing `socket` would cost ~4 ms on every hook.
- **NFSv3 `O_APPEND` is not atomic**, which is why breadcrumbs are per-session files bounded by
  truncate-and-restart rather than a shared appended log.
- **In-place `O_TRUNC` opens a zero-length window** that is visible to other hosts, which is why the
  marker — where an empty read would mean "this host has no sessions" — is written temp+rename.

## Conventions

- **`Task N` references** in comments and docs are provenance from the original build plan's
  phases. That plan is not published; the markers are retained because they group related decisions.
  Treat [`docs/design.md`](docs/design.md) as the authority for *why*, not the task numbers.
- **Comments carry the reason, not just the rule.** A withdrawn rule keeps its reasoning on purpose
  — a rule without its reason gets re-litigated. Preserve this when editing.
- Hand-rolled argv parsing is deliberate (`argparse` costs ~10 ms of import on the hot path). The
  thirteen parsers share a `*_FLAGS` / `*_VALUE_ARGS` table convention; `--opt=` with an empty inline
  value is a missing-value error in all of them.
- Function-local imports (`json`, `select`, `subprocess`, `re`, `fcntl`) are deliberate. `agb`'s
  module-level imports are pinned to exactly `{errno, os, sys, time}` by a test.
- **`RowRenderer.applied`/`.titles` are an optimisation, never a source of truth.** They record what
  the bridge last *sent*, which is not what agterm is *showing* — agterm resets a row's status when
  the row's command starts, so attaching clears the glyph with no error anywhere. `_reassert` re-sends
  every status every `REASSERT_INTERVAL` (30 s) so no divergence can be permanent. If you add another
  suppress-if-unchanged path, give it the same escape hatch.
- ⚠️ **`applied` is also the wrong gate for "did the AGENT change".** It is the right gate for
  `--blink`, which is about what was painted — but `_render_stale` writes `idle` into it on *any*
  disconnect, including a routine 10 s quiet spell. Anything that should fire once per real agent
  transition needs its own memory, or a network hiccup replays it. `RowRenderer.blocked` (the set
  behind the `blocked` banner) is the worked example; substituting `applied` there fails five named
  tests. This distinction has now caused two separate bugs — assume it will cause a third.

## Where the project is (2026-08-01)

⚠️ **Newer than this section's date and not yet released (2026-08-24/25): agent-to-agent chat.**
`agb-peer`, with `agb-codex` and `agb-tmux` beside it — described above, in full in
[`docs/commands.md`](docs/commands.md), and for the *agent's* side in `skills/agb-peer/SKILL.md`.
Verified live in every combination tried: Claude↔Claude on the farm, Claude↔Mac, and Claude↔Codex on
a batch-pool node **the Mac cannot ssh at all**, both directions. ⚠️ That last case shaped the
design. Its tmux socket is unreachable — a unix socket is a local kernel rendezvous, not a
filesystem object NFS carries, **measured** — so *sending* falls back to a file plus a printed
doorbell, while *delivery* needed no change whatever, because the pane is connected all the way
down from a host you can reach. `AGB_CODEX_CUSTOM` is what starts an agent there.

⚠️ **Newer still, and unreleased with it: `agb-peer-setup` and `agb-dashboard`** — the roster writer
and the row grid, both described above and in [`docs/commands.md`](docs/commands.md). Neither is
installed by `install.sh`; both load `agb-peer` by path from beside themselves. ⚠️ **`agb-dashboard`
has never been run against a live agterm** (see the honest list below), and the `relay --dashboard`
fixes that shipped with it have not either.

Released **0.5.0** — **instances**: a machine that shares no disk with the first is now an install
(`install.sh mac --instance <name> --statedir …`), one independent bridge per machine, all rendering
into the same sidebar. One flag, `--config`, carries it everywhere: bridge, `close-done`,
`forget-rows`, the row's own `agb pane` command, the launchd plist and `agb-refresh`. Invariant 12
and `docs/design.md` §5 hold the reasoning; the seven limitations are written out there. As shipped,
the first of them — a helper without `--instance` succeeding on the wrong instance — was mitigated
**only** by the banner those commands print on every run; the unreleased change above replaced that
with a default that has nothing to be wrong about. The banner still matters, for a **narrowed** run.

⚠️ **It shipped without ever having been run live**, which is unusual here and is said out loud in
`CHANGELOG.md`'s `### Not verified`: nobody has had two bridges up on one Mac. The check that
matters is clicking a row from *each* instance and landing on the right machine. Until that is done,
treat the feature as tests-only — and this project's own history is that two of the last four
features passed every test and still needed a fix after live use.

Unreleased, and the headline: **the Mac side has no default instance any more.** A bare `agb-refresh`
and a bare `agb close-done` sweep **every** instance; a bare `agb forget-rows` is **refused** and
names `--all` — not because it closes rows (`agb-refresh` closes every row it forgets too) but
because nothing restarts the bridges afterwards, so the sweep that ends in a restart may default to
all and the one that does not, may not. `--key` **sweeps** on both, because a key read out of a log
does not say which instance minted it; naming a **map** (`--instance`/`--label`/`--config`/`--rows`/
`--placements`) is what narrows a run. An instance left without a running bridge is a distinct error
(exit **4**), which is what makes bare-is-all safe. New command **`agb instances`** — a listing,
`--labels` for the sweeps, `--plist … --arg` (now `agb-refresh`'s whole plist reader), and `--probe`,
whose literal `instances-ok` answer is load-bearing because `agb` answers an unknown command with
exit 2 and empty stdout, byte-identical to "this plist says nothing". `docs/design.md` §5 is the
authority; limitation 1 is fixed **by the default** rather than by the banner, and limitation 6 is
mitigated (the maps stay per-instance; only the commands visit all of them).

Also unreleased, and the follow-up that closed the asymmetry above: **`install.sh mac` refuses an
install with no `--instance`.** The refusal sits at the top of `role_mac`, before any filesystem
mutation *and* before `probe_farmhost`, so a refused install writes nothing and makes no ssh call —
which is what makes "no name can be invented" true rather than merely untested. `install.sh farm` is
untouched: a farm host has one identity, and `agb hook` resolves `agb.config_path()` on every
invocation. Its transitive cost is that **`--statedir` is now required on a first Mac install too**,
which is paid for by adopting it on a re-install — `agb install-config --print-statedir`, a pure
query answering the file's **own** value, handled the instant `parse_config` returns and returning
there. ⚠️ **It has three statuses, not two**: `0` with the value, `PRINT_STATEDIR_NONE` (**4**) for
*this file carries none*, and `1` for *I could not read it at all*. The installer swallows only the
middle one. One non-zero for both was the shape shipped first, and it reported an unreadable config
as "carries none to adopt" — sending the operator after `--statedir`, a flag that was not the
problem. Invariant 12's rule about "I could not answer", in a fifth place.

⚠️ **The claim is "no NEW nameless instance is created by default", NOT that symmetry is guaranteed.**
`install.sh mac --instance X --config $DEFAULT_CONFIG` still writes the unnamed file, because
`--instance` only *defaults* `--config`; refusing that would forbid a legitimate shape to prevent a
deliberate act. And **no legacy code path was deleted** — `instance_display_name`'s `(default)`,
`bind_label_to_config`'s no-`--config` branch, `_is_agbridge_instance`'s label-space clause all read
plists that already exist. A plist on disk outlives the installer that wrote it: **creatability**
changed, reachability did not. ⚠️ The operator-visible consequence, which is the line that matters:
**a legacy unnamed install has no in-place upgrade at all** — `--instance` is mandatory, and adopting
the old file via `--config <the default path>` re-demands `--statedir`. Give it a name (`CHANGELOG.md`,
*Upgrading from ≤ 0.5.0*).

⚠️ **`VERSION` was deliberately NOT bumped by that change.** It lives at `agb:24`, the only place, and
the plan's own constraint was that `agb` is not touched (1 character of headroom). A breaking CLI
change does argue 0.7.0 — but the number decides nothing until a release does, so the entry sits under
`## Unreleased` at 0.6.0 and the release that ships it picks the number. Recorded so the omission does
not read as an oversight.

Before it, **a banner when a long-running agent finishes** (`notify_on_completed_after`, on by
default at 300 s). The threshold is the feature — `completed` fires once per *turn*, so ungated it
announces every "yes" you type, and agterm banners and bounces even for the row you are looking at.
It also fixed three things it tripped over: `agb doctor` had been calling two of its own documented
config keys typos since 0.4.0, `config_flag` had never had a test, and `agtermctl notify` had no
contract oracle in the stub.

Before it, **0.4.0** — notifications: a banner when an agent blocks, one when a new agent appears,
and the unseen badge cleared when a block is answered.

⚠️ **`agb`'s budget is measured in CHARACTERS, not bytes** — the guard is
`len(agb_source) < AGB_PARSE_BUDGET` (`tests/test_mac_split.py`), and `agb` is not pure ASCII. It is
**105,269 characters** (105,287 *bytes*) against **105,300**, and the comparison is a **strict `<`**
— so the maximum is 105,299 and the real headroom is **30 characters**. `wc -c` is the wrong number
to compare, and so is the difference from the budget. This is the single hardest constraint on
any change to the hot path. Neither 0.5.0 nor the finished-turn banner added anything to it; the
instances change did — the only part of it in `agb` is `cmd_instances`, a dispatch arm and a `USAGE`
line, measured at +716 and paid for with the second budget raise. Everything else landed in
`agb_mac`. ⚠️ **Anything further in `agb` needs prose moved into a sibling docstring or a third
measured raise** — 63 of the 65 characters that raise left were spent immediately afterwards, when
widening `cmd_instances`' `except` to `Exception` turned out to be load-bearing (`_load_sibling` loads
by path, so a missing `agb_mac` raises `FileNotFoundError`, an `OSError`). 2622 tests.

Verified against a live agterm, in this order of confidence: row creation and the returned id,
`rename`, `status`, `--blink`, `close`, `split`+`type`, click-to-attach reaching the right host and
pane, `[s] shell` opening a split, `notify --target` producing a banner, and **all three
notification paths end to end** — a blocked agent, a new agent, and the badge clearing when the
block was answered.

⚠️ **Two of those three needed a fix *after* the live test, in ways 1400 tests could not catch.**
The badge test was run against the row that was selected, and agterm never badges the session you
are looking at — so a working feature read as broken. The new-row quiet window was armed at
construction rather than at the first op batch, and was long enough to swallow a real agent started
9 s after a reinstall. Neither was reachable from the test suite. **Live-test anything that talks to
agterm, and think about which row you test it on.**

**Still unverified, and the honest list:**

- **`session scratch`'s behaviour** — the `[d]` drawer added in 0.3.0. Its spelling is
  `--help`-verified and its call path is mutation-tested against the `[s]` split it copies, but
  nobody has watched a drawer open, be hidden, and come back with **the same shell still alive**.
  That claim is the entire reason `scratch` was chosen over `overlay`, so it is the one worth
  checking first. `README.md`'s verification table carries unchecked rows for it.
- ~~**A second instance, live.**~~ **Verified 2026-08-01**, on a Mac with two named instances and two
  bridges: `agb instances` listed both, a bare `agb-refresh` swept both, and clicking a row from
  *each* landed on the right machine with the right config on its identity line. The unnamed default
  instance is gone — migrated to a name, which is what `docs/plans/completed/` describes.

  ⚠️ **And the live run found two things 1867 tests did not.** `agb instances` printed a **blank**
  name column for the default instance *and* for any custom-`--label` one — the same bug the banner
  had, surviving in the listing because the fix was applied in one place. And the migration exposed
  a `feed_host` that had been **wrong and dormant in the config for hours** — a misspelling of the
  *other* instance's ssh alias. The bridge reads its config once at startup, so the file was wrong
  and the process was right until a restart reconciled them, and then every row went `[?]`. Both are
  fixed; the second is why the upgrade note tells you to read the installer's `probed:` line rather
  than trust the alias you copied out of the config you are migrating.
- **`agb-dashboard`, and every `relay --dashboard` fix beside it.** agterm's `dashboard` *clauses*
  were measured against the binary (`docs/agtermctl.md`); nobody has watched either command drive a
  real grid. ⚠️ **The one to check first is not the happy path** — it is the refusal: a grid agterm
  opened while printing `unresolved:` on stdout has to be **closed again**, and that close is the
  step the tests can only assert against a fake `Ctl`.
- **Long-running behaviour** — reconnects, the watchdog firing, `prune` against a genuinely dead
  host. This is why the version is 0.x.

⚠️ **agterm closes a session when its command exits** (confirmed live 2026-07-31, in isolation).
`q`/`quit`/`exit` at the `agb pane` prompt therefore **destroys the row** of a live agent — not a
dismissal, an accident — and leaves a bound entry naming a row that no longer exists, which is
almost certainly the source of the `no such session` spam in the bridge log. Full write-up in
[`docs/agtermctl.md`](docs/agtermctl.md) → "agterm closes a session when its command exits". Read it
before reasoning about row lifetime; it is not in agterm's own reference and nothing here assumed it.

⚠️ **Never design against `agterm.com/commands` without running the command on the Mac first.**
It cost two reversed decisions in one day. First it documented `events` and `pick`, which the
installed build did not have (they arrive in agterm **v0.16.0**, along with `session restore`).
After upgrading they existed — and the page was *still* wrong about behaviour: it says `events`
returns a batch and exits, and it **follows**. A full design had been built on that sentence, and an
earlier, correct objection to it had been marked ✅ wrong on the page's authority. Findings in
[`docs/agtermctl.md`](docs/agtermctl.md) → "What `agtermctl events` actually does"; the shelved
design is in `docs/plans/blocked/`. The "capture the real output first" task is what caught both,
and is why such a task belongs in any plan that touches agterm.

**agbridge uses 8 of agterm's ~70 documented subcommands.** The unused surface was surveyed on 2026-07-31 and
the useful part is recorded in [`docs/agtermctl.md`](docs/agtermctl.md) → "What agbridge does not use
yet", with what each would buy. The standout is **`events`**, agterm's control-event ring: the
bridge is write-only today, which is why nothing notices when a row is closed by hand or agterm
forgets its sessions, and why `agb-refresh` exists. Read that section before adding any new call —
it may already say why a thing was passed over.

**Considered and not built:** bringing agterm to the front on `blocked`. `agtermctl window select`
is recorded verbatim in `docs/agtermctl.md` with its trap — given no id it raises whichever window
is *already active*, so targeting the blocked row's window needs the id from `tree --json`, which
`tree_workspaces` already parses. Deferred deliberately: a bouncing Dock says "come here when you
are ready" and a window jumping in front says "stop what you are doing", and the banner may well
turn out to be enough. If it is built, it wants a config gate and an **off** default — focus-stealing
is a thing to choose, not to inherit.

## Changelog and releases

**Every user-visible change gets a `CHANGELOG.md` entry in the same commit as the code.** Not at
release time — by then the reason is gone, and the reason is the whole point of the file.

Entries accumulate under `## Unreleased` until a release renames that heading to
`## <version> — <date>`. Sub-headings are `### Added` / `### Fixed`; 0.3.0 also uses
`### Decisions recorded, because they will look like mistakes later` and `### Not verified`, both
worth copying when they apply.

**The house style is that an entry says *why*.** A rule without its reason gets re-litigated, and in
this project most reasons are a failure somebody actually hit — so entries name the symptom, not
just the fix ("a row's status glyph disappeared on first attach and never came back", not "fixed
status handling"). Where a decision will look wrong to a future reader, say what was rejected and
why: `--command` and the duplicated pane openers are both in there for that reason. And **carry the
caveats forward** rather than summarising them away — that the log cap truncates instead of
rotating, that `session scratch` is unverified.

Releasing, in order:

1. `agb:24` `VERSION` — the **only** place it lives, and load-bearing: both installers probe
   `agb <version>` and refuse to write anything without the right answer back. New key or feature →
   minor; fixes only → patch.
2. `wc -c agb` against `AGB_PARSE_BUDGET` in `tests/conftest.py`. A same-length version string
   leaves it unchanged, which is the expected result for a release that touches no other line of
   `agb` — if the number moves, something else got in.
3. Rename `## Unreleased` to the version and date.
4. `git commit -m "release: <version>"`, then an **annotated** tag `v<version>` whose message is a
   short prose summary (not a commit list — that is what the changelog is for).
5. `git push origin HEAD && git push origin v<version>`.
6. GitHub Release from the tag, title `agbridge <version>`. The body is a **short summary plus a
   link to `CHANGELOG.md`** — never a copy of it. Two places saying the same thing is two places to
   keep in step, and the one nobody can `git log` is the one that goes stale. Link the **tag**, not
   `main`, so it keeps showing the changelog as of that release:
   `https://github.com/zahark/agbridge/blob/v<version>/CHANGELOG.md#<anchor>` — the anchor is the
   heading lowercased with dots and the em dash dropped, e.g. `## 0.4.0 — 2026-07-31` →
   `#040--2026-07-31`.

⚠️ **A tag is only as good as what it points at.** Fixes landing after the tag are not in the
release; move the tag with `git tag -f -a` and `git push --force origin v<version>` *before* cutting
the GitHub Release, or say plainly that they are not included.

⚠️ **A release is not installed by pulling.** The Mac loads `agb_mac`/`agb_ops` from
`~/.local/lib/agbridge/`, not from the checkout, so `sh install.sh mac --instance <name> …` is
required — and existing rows keep the `agb pane` code they were *created* with until `agb-refresh`
re-mints them. Say this in the release notes every time; it is the single most common way a fix
appears not to work.

## Known gaps

- **What is and is not verified against a live agterm** is above, under "Where the project is".
  [`docs/agtermctl.md`](docs/agtermctl.md) tags every individual clause. Three clauses stay
  **ASSUMED** and are fine that way: repeated `rename` (the bridge does it on every update, so a
  failure would be constant rather than subtle), whether `blink` is sticky or one-shot (it is only
  ever sent on a transition, so it is correct under either reading), and the spelling of
  `--auto-reset`, which agbridge never emits.
- **Three doors to `agtermctl`, deliberately.** `agb_mac._run_command` is the renderer's single
  door; `agb_ops.open_split` and `agb_ops.open_drawer` are the other two, because `agb pane` runs
  on the Mac but lives in `agb_ops`, which never loads `agb_mac`. All obey the same rule: a failure
  is written out and returned, never raised.
- **`open_split` and `open_drawer` are duplicated on purpose — merging them is not a tidy-up.**
  They differ in two constants and one noun, which normally argues for a parameter. The reason not
  to: they are expected to **diverge**. `session scratch` takes a `--command` that `session split`
  has no equivalent for, so the drawer may yet become a single call while the split cannot. The
  same reasoning is recorded at the call site and in `tests/test_identity.py`'s enumeration.
- `agb <cmd> --help` is not implemented — it would need a `--help` arm in thirteen hand-rolled parsers
  across three files, against the byte cap. [`docs/commands.md`](docs/commands.md) is the reference.
- The thirteen `parse_*_args` functions share scaffolding that could collapse into one helper. It was
  deliberately not done: the helper would have to live in `agb`, the byte-capped file, so that two
  *lazily loaded* siblings could share it — inverting the constraint the cap exists to enforce.
