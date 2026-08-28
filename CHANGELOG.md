# Changelog

Every entry here says *why*, not just what. A rule without its reason gets re-litigated — and in
this project most of the reasons are a failure somebody actually hit.

Versions are `agb`'s `VERSION`, which both installers probe (`agb <version>`) before writing
anything. The wire protocol has not changed since 0.2.0: any farm host works with any Mac.

## Unreleased

### Fixed

- **🔴 Repairing a participant's transport destroyed the message the repair existed to deliver.**
  MEASURED live: a participant's roster entry was missing its transport hint, so the relay took the
  tmux branch every tick and **never once called `drain_files`** — two files sat uncollected for
  hours with the doorbell plainly on screen. Adding `:nfs` fixed the transport **and, on the same
  tick, discarded the pending message**: the edit registered as a re-join, and priming discards a
  joiner's mail by design.

  ⚠️ **A roster edit was the only way to fix delivery for that participant, and it was guaranteed to
  destroy whatever was pending for them.** Same shape as `agb-refresh` detonating the orphaned
  binding it is the documented fix for — and nastier, because **the discard is correct**: priming
  exists so a joiner is not delivered an hour of stale backlog.

  ⚠️ **Fixed by asking the right question.** A binding is `(row, pane, target, tmux_target)`; only
  the first two say *which pane*, the rest is *how to reach it*. Priming is justified by *"the new
  pane may hold anything, including a conversation this name was never part of"* — which **cannot be
  true when the pane did not move**. So only a name whose **pane** changed is primed. Forgetting
  `seen` is still right for a transport change and is not the harmful half: it makes the doorbell
  read as new, which is exactly how the repaired transport picks the message up.

  ⚠️ **It took three attempts to write a test that caught it, and the first two failures were the
  harness rather than the code**: the doorbell has to be on the **edited participant's own pane**
  (priming discards its *outgoing* mail, not the delivery queue), and the edit must not be the one
  that adds `:nfs` — that moves the drain onto a path the harness does not model, so the scenario
  under test disappears into the fake. A unit test of `apply_leaves` cannot see any of this, because
  `moved` is computed at the call site.

- **🔴 One cause, two error strings, and the cause appears in neither — plus the raw traceback that
  reported the second.** MEASURED: an agent under a sandbox that confines writes to its workspace
  could use **neither** `agb-peer` transport. The tmux socket in `/tmp` answered `Operation not
  permitted`; the statedir answered `Read-only file system`. **Two subsystems, two strings, and
  nothing in either names a sandbox** — so the failures looked unrelated, each was diagnosed locally
  and correctly, and the shared cause was never named.

  ⚠️ **That is why the first fix landed on a door the same cause had already closed.** Reasoning
  *"the socket is blocked, therefore use the file"* was sound and never asked whether the file path
  was reachable. **A fallback needs its precondition checked, not assumed** — and the check is cheap
  here, because reaching the file path at all means the socket has already failed.

  ⚠️ **And the second failure was a raw `OSError` traceback**, which is worth holding beside the
  evening's other reporting faults because it fails in the **opposite direction**. Those were quiet —
  exit 0 for a dropped message, `queued` for one destroyed a tick later, a true error that stopped an
  agent. This one is maximally **loud** and equally useless. **Loud-and-unclassifiable and
  quiet-and-plausible are both failures to say what happened in terms the caller can act on.** It is
  now a `PeerError` that names the shared cause, says a sandbox confining writes blocks *both*, gives
  `$AGB_STATEDIR` plus a matching `--chat-dir` as the way out, and warns that **the doorbell printed
  a moment earlier named a message that was never written**.

- **🔴 The `[hangout]` marker commits the SENDER, and the file only ever explained it to the
  receiver.** MEASURED on the first send that actually went out — the opener was:

  > `[hangout] Ready to help. What's the task?`

  Marker applied correctly on line one, followed by **a request for work**. It copied the syntax and
  dropped the meaning, which is precisely the failure the marker exists to prevent — **committed by
  the sender**, a direction nobody had considered because the file tells the *receiver* what the
  marker means and tells the *sender* only where to put it.

  ⚠️ **The instruction that produced it said "`[hangout]` on the first line" — placement, not
  meaning.** The skill now states that a message carrying the marker **must not ask for a task**, and
  names the phrasings that fail it: *"ready to help"*, *"what do you need"*, *"how can I help"*. **If
  you are asking for a task you are not hanging out**, whatever is on line one — a service opening a
  ticket is not somebody hanging out.

- **🔴 There is a second peer system on the same machine, and an agent used it to conclude a
  reachable peer was unreachable.** Claude Code has a built-in `ListAgents`/`SendMessage` pair that
  lists **Claude sessions on this machine**. `agb-peer` is a different system with **overlapping
  vocabulary and disjoint membership**: a **Codex** peer and a peer on **another host** can never
  appear in the built-in listing, and `agb-peer` membership lives in the **relay's roster**, on the
  far side of the wire.

  MEASURED on the first working hangout attempt: asked to open a chat with a Codex, the agent ran
  `ListAgents`, got a confident five-name list that **could not have contained it**, and **refused to
  send to a peer that was reachable the whole time.** ⚠️ Every step was reasonable — the list was
  accurate about its own mechanism and irrelevant to the one it had been asked to use. **The third
  "true fact, false conclusion" of the day**, after the missing `agtermctl` and the sandboxed socket.

  ⚠️ **And the instruction that was meant to help is what left the gap.** It said *"do not run
  `agb-peer who` first"* — correct, since `who` cannot answer in the same turn — which removed the
  **right** lookup and left the agent reaching for the wrong one. Same shape as a true caveat arming
  a wrongly-ordered step: the removal was justified and nothing replaced what it removed.

  Both skills now carry it: **a name's absence from `ListAgents` is not evidence about `agb-peer`
  reachability**, which is not discoverable from the agent's side at all — if you were told a name,
  use it, and let the relay be the authority, since it now bounces a notice back when a name is
  wrong.

- **🔴 The repainting-composer caret was filed as a hypothesis and is now MEASURED, twice, on a
  healthy live row.** A relay log caught its own peer's composer reading **`column 0`** and then
  **`column 1`**, minutes apart, on an agent working perfectly throughout. ⚠️ **So without the
  two-agreeing-reads guard, `caret_reason`'s below-2 branch would have announced "not a composer at
  all — it needs a HUMAN" about a healthy agent, twice** — in the one case the wording exists for.
  The guard is the **load-bearing** half, not the caution; the wording change alone would have been
  actively wrong more often than right. Filed as *"the shape earns the guard, not a sighting"*, and
  the sighting arrived within the hour, from a log nobody was reading for this.

- **🔴 Recorded: four correct behaviours composing into an agent that waits for ever.** Roster
  reverts → the agent is no longer a participant → its `agb-peer who` cannot be answered → the roster
  is restored → **the priming rule discards the pending `who` as predating the join** → nothing ever
  arrives. ⚠️ **No component is wrong**: each of the four is documented, defended in a comment, and
  three of the four comments explain why the *opposite* behaviour would be a bug. **A composed
  failure has no faulty component, so every review that examines components passes it.**

  ⚠️ **The stall was diagnosed as transient and was permanent** — *"it will probably recover when the
  answer arrives"* was wrong in the reassuring direction, because the answer had already been deleted
  by a correct rule. And it is the strongest argument for the *do not end your turn in silence* rule
  added the same day: that was written against a **transient** wait as a matter of manners, and turns
  out to be the only thing between an agent and an unbounded one.
  `docs/backlog/four-correct-behaviours-composing-into-a-permanent-stall.md` also records why the
  obvious fix collides with the rule that creates the problem.

- **🔴 A message dropped for an unknown recipient now bounces back to the sender.** `send` printed
  `queued for <name> as #<id>` and exited **0**; a tick later the relay destroyed the message because
  the name was not in the roster, and **the only trace was a line in relay output that no agent can
  read.** The membership test lives at the relay — the sender is on the wrong side of the wire and
  cannot check — so the sender was told success for a message that went nowhere.

  ⚠️ **The drop itself is correct and is unchanged**: a name nobody added is a typo, and holding
  those for ever is the opposite mistake. **The defect was the silence.** The relay already types
  into the sender's pane — that is how `agb-peer who` is answered — so the mechanism existed and only
  the decision to use it for a *failure* did not.

  ⚠️ **MEASURED, and the measurement is the argument**: this caught an agent that had spent the same
  evening writing up **two other faults with the identical signature** — a peer goes quiet, visible
  only in relay output. It reported the send as done. Not inattention: it had the failure mode in
  working memory and still read exit 0 as delivery, **because exit 0 is the only thing there is to
  read**. Three causes, one symptom, and the third one caught the author of the first two.

  ⚠️ Two implementation notes worth keeping: the bounce cannot loop (signed `relay`, and the relay
  answers nothing but the literal word `who`), and it is collected **separately** from `pending`
  because the drain does `pending[:] = held` and would otherwise discard it — a guard with its own
  test, since it fails silently and looks like the feature simply not working.

- **A peer's report and a peer's request are the same message, and both readings are correct.**
  MEASURED: two agents read the same diagnosis of a defect, both treated it as a work item, and both
  fixed it — one patch was wasted and would have conflicted. ⚠️ **A task handoff has an implicit
  owner; a peer conversation does not**, so *"here is what I found"* is a report to one side and a
  request to the other, and neither is misreading. `skills/agb-peer/SKILL.md` now says: **if a peer's
  message makes you start work, say "taking this" before you start.** ⚠️ Same shape as the two agents
  that stopped on true errors — nothing was wrong at either end, and the gap was *between* them.

  🔴 **And that rule is NOT a lock — measured on the very next occasion, which it failed.** The
  announcement travels on **the same unordered transport as the work**, so it can land *after* the
  thing it was meant to prevent, and it did: both agents had already acted. **It helps when the work
  is slow and the channel is quiet, and does nothing when the work is one tool call.** The two things
  that do work are making the work idempotent, or announcing and then **waiting a turn** — which
  costs a round trip and is usually not worth it. ⚠️ Writing a rule that assumed an ordering the
  transport does not provide, in the same session that established the transport does not provide
  ordering, is its own instance of the evening's shape: the fact was in hand and the question it
  applied to was the one nobody asked.

- **🔴 `agb-hangout` stalled on the very first real use, and it was an ORDERING defect — the same
  one as the `agb-peer` usage line, in a section that had just been reviewed.** A user said *"hang
  out with codex — no task, just talk"*. The agent loaded the skill, ran **one** command
  (`agb-peer who`), said it would wait for the answer rather than poll, and **ended its turn having
  sent nothing.**

  ⚠️ **It did exactly what the file said.** `## Starting one` opened with *"If you do not already
  know your peer's name, `agb-peer who` asks the relay for it"*, and the opener followed as
  *"**Then** open with something."* — so the send was made **sequentially dependent on an
  asynchronous step**. ⚠️ And the guard was **false**: the user *had* named the peer. It ran `who`
  anyway, because it was first and "Then" implies order. **No line was wrong; the order was**, which
  is precisely the usage-line defect one file over.

  🔴 **And the warning I added hours earlier made it worse.** Emphasising that `who`'s answer
  arrives on a *later turn* was correct and necessary — and it is exactly what told the agent that
  waiting was the right move. **A true caveat attached to a wrongly-ordered step makes the wrong
  behaviour more reliable, not less.** That is not a fix causing a regression; it is a fix making a
  latent ordering defect fire every time.

  ⚠️ **It is also shape E.** The review that added that warning *also* noted, in the paragraph
  immediately below, that a cold agent needs no lookup at all because *the relay signed the
  message*. That insight never reached the paragraph **above** it, which still gated the opener on
  `who` — same file, adjacent sections, one writer, one pass.

  ⚠️ **That middle point is recorded as a class, not just an instance**, in
  `docs/backlog/a-true-caveat-can-arm-a-wrongly-ordered-instruction.md`: **a true statement can
  convert a latent structural defect into a reliable one**, and the clearer the addition the more
  reliably the bad structure is followed, because the reader now has a *good reason* to do the wrong
  thing. It is not a regression, not a wrong fix, and it did not touch the defect. The check it
  leaves: **when you add a caveat to a step, ask what the caveat tells a reader to DO, and whether
  the step should have been there at all** — writing the warning is the moment you have that step's
  full context in front of you, and the last moment anybody will look at it.

  **Fixed by inverting it**: *send the opener* is now the first and only step, `who` is demoted to
  *If you have no name at all* — explicitly a fallback rather than a step — and the file now says
  what to do while one is outstanding, which was unspecified and is the gap the agent fell into:
  **do not end your turn in silence.** That is not "not polling", it is disappearing.

- **A third label-collision variant, found in live agent names: one label NESTED in another — and
  the shorter one is what breaks.** `data-pipeline` and `data-pipeline-2`, both real
  keys on one host. The longer row's title contains the shorter name entirely, so the selector
  `data-pipeline` matches **both**, while `data-pipeline-2` stays unique and keeps
  working. ⚠️ **So the agent that gets broken is the one that was there FIRST**, by the later one
  being named after it — and the person who caused it is the one whose agent still works, which is
  why nothing draws their attention to it.

  ⚠️ **The `_2` suffix manufactures it**, which is what everyone reaches for when they want a second
  of something. ⚠️ **And it can lie dormant**: the measured case is safe only because the shorter
  agent is **dead**. The day it comes back while `_2` has a row, the collision arrives with **neither
  row stale and neither name wrong** — nothing about the state on the day you look tells you it is
  coming. `docs/cookbook.md` already warned that no session name may be a prefix of another; what it
  did not carry, and now does, is **who breaks**, **that the convention causes it**, and **that it
  can be latent**.

- **Corrected: `agb-refresh` on an instance with orphaned bindings is usually a REPAIR, not a
  hazard.** An earlier entry framed the documented cleanup as the thing that detonates the litter.
  Measured across both sides: of six orphaned bindings on one instance, **three are inert** (key
  already reaped), and of the three that are live, **two have no row at all** — for them a refresh
  is exactly what gives them one back. **Only one is dangerous**, because only one duplicates a live
  row's label. ⚠️ **The hazard is not re-minting; it is re-minting into a duplicate label**, and the
  test needs a third question nobody was asking: *does this orphan's label duplicate a live row's?*

- **A sandboxed agent could not use `agb-peer send` at all, and the refusal did not say so.**
  MEASURED on the second live hangout attempt: a **Codex** ran the *correct* command this time and
  got `error connecting to /tmp/tmux-…/default (Operation not permitted)` — its sandbox refuses a
  socket that is right there and belongs to it. `socket_is_missing` correctly answers **False** (the
  socket *is* on this machine), so `cmd_send` refused, and the agent reported the error and stopped.
  **The file transport would have worked the whole time.**

  ⚠️ **The refusal's reasoning was right and its outcome was wrong.** No error string is a sound
  discriminator between *a sandbox blocking the socket* and *the socket being broken* — the docstring
  says so, and it is correct. So the answer is not a cleverer sniff but an operator opt-in:
  **`AGB_PEER_FILE=1`**, on the `AGB_HOST_LOCAL` precedent — **an opt-in with no opposite, because a
  process cannot tell those two apart by looking at itself.** When set, `send` does not reach for the
  socket at all.

  ⚠️ **And the refusal now names it**, which is the same defect as the missing-`agtermctl` message
  fixed hours earlier, in a second place: **a true fact that leads somewhere false is worse than a
  vague one, because it gets believed.** Two agents stopped tonight on errors that were entirely
  accurate. `"0"`/`no`/`false`/`off` read as **off**, because a variable that silently means its
  opposite is worse than one that does not exist.

- **🔴 A missing `agtermctl` sent an agent away from a transport that was working.** Observed on the
  first live hangout: a **Codex on a cluster host** ran the direct form, got
  `agtermctl: [Errno 2] No such file or directory`, told its user that *"agb-peer's required
  agtermctl dependency isn't installed"* — and stopped.

  ⚠️ **Every word of that was defensible and the conclusion was wrong.** `agtermctl` is not a
  dependency of `agb-peer`; it is a dependency of the **direct delivery path**, which types into a
  row and so only exists where agterm does. **`agb-peer send` needs no agtermctl at all** — it
  stashes the body in a tmux pane option and rings a doorbell — and it was available the whole time.
  **An error that names a true fact and leads somewhere false is worse than a vague one, because it
  gets believed.** The message now names `send`, says why that form is different, and mentions
  `$AGB_AGTERMCTL` for the case where agterm *is* present under another path. Only a *missing*
  binary gets the hint — an agterm that ran and refused still reports what it said.

- **The first line of `agb-peer`'s usage was the one an agent cannot run.** It led with the direct
  form — which needs agterm, i.e. the Mac — while `send` sat on the second line. ⚠️ **An agent copies
  the first line**, and every agent that matters here runs on a cluster host. `send` leads now.

- **🔴 Recorded, not fixed: the documented repair for an orphaned row binding is the exact command
  that detonates it.** An entry can be **bound to a row agterm no longer has** — minted, then the row
  closed, the binding surviving. Measured on one live instance: **11 of 18 bindings name a session
  that does not exist** (5 orphaned-bound, 6 `[done]`), and ⚠️ **`close-done` cannot reach either
  half** — it closes *rows*, and these have none.

  ⚠️ **The inversion is the finding.** A **re-resolve is safe**: `resolve_all` reads agterm's *tree*,
  and an orphaned binding is not in it. What is *not* safe is **`agb-refresh`** — it forgets the
  bindings and re-mints, so an orphan whose label duplicates a live row's gets a **new, real row**,
  the selector then matches two, and the relay refuses on the next tick. **`CLAUDE.md` names
  `agb-refresh` as the fix for orphaned bindings and does not carry the precondition that no orphan's
  label may duplicate a live row's.**

  ⚠️ **And it has already happened once, unnoticed.** An unrelated `agb-refresh` earlier the same
  evening changed the orphan's bound id, so a second row with the duplicate label plausibly existed
  for a while — invisible because **a duplicate label costs nothing until somebody resolves that
  label**, and every resolve in that window landed on the live row.

  🔴 **And inert litter looks identical to armed litter in the map.** Measured on both sides while
  four more dead bindings accumulated in front of the two agents watching: the `done`/`bound` marking
  decides whether anything *sweeps* an entry, but whether the key is **still alive in the feed**
  decides whether `agb-refresh` *re-mints* it — and that is on the agent host's side, invisible to
  the Mac-side tool. ⚠️ **The two point opposite ways**: four leftover `hangout-*` bindings are
  `done` with **dead** keys, so they are noisy, sweepable and **inert**; the single dangerous entry
  is `bound` with a **live, beating** key, so it is invisible to `close-done` and **armed**. A
  cleanup reading only the map cannot tell them apart, which is a harder problem than the one
  `close-done` already fails at.

  `docs/backlog/the-repair-for-orphaned-bindings-is-what-detonates-them.md` has the measurements, the
  ordering to follow before refreshing, and why both obvious fixes need a decision rather than a
  patch.

- **`agb-hangout` told agents to keep it short and they sent each other paragraphs.** Found on the
  first live run, which is the only way this could have been found — nothing executes a skill file.
  Three causes, and the length rule was the least of them:

  - **The budget had an escape hatch.** *"Two to six sentences most of the time; longer when you
    have genuinely got going about something"* — and a model always feels it has got going. It is
    **one to three sentences** now, with **no exception clause**: send the short version and let
    them ask.
  - ⚠️ **The example was long, and an example outranks the prose.** The sample opener ran to six
    lines of explanation plus an "opening bid" paragraph, so that was the register agents copied. It
    is four lines now, three of which are the actual message, and it says out loud that the *length*
    is the point — an opener twice that long teaches your peer that twice that long is normal, and
    you do not get it back.
  - 🔴 **The file's own register was the real mechanism, and it is the one nobody would have
    guessed.** Every bullet here is dense, hedged and full of ⚠️ warnings, because a reference is
    read once and has to survive being wrong. Both agents mirrored it and sent each other
    documentation. **An instruction to be brief, written at length, is contradicted by its own
    form** — so the file now says *do not write like this file*, which is the only version of that
    instruction that can work.

  ⚠️ **And the cost is not only tone.** Every message is a full turn on the other machine, so a long
  one buys a long reply which buys a longer one. **Length compounds in a way content does not**, and
  that is the argument the file was missing.

## 0.7.0 — 2026-08-28

> ⚠️ **One breaking change**: the Mac installer refuses an install that does not name its instance.
> Read *Installing after this change* below before upgrading a Mac — and read it **first** if that
> Mac still has an unnamed default instance, because that one has no in-place upgrade at all.

### Changed, and it is a breaking change

- **`relay` is now a reserved participant name in `agb-peer relay`.** A roster line naming a
  participant `relay` is refused, because that is how an agent addresses **the relay itself** —
  `agb-peer who` sends to it, and a participant of that name would shadow it.

  ⚠️ **The reservation is exact and case-sensitive, and that is deliberate rather than lazy.**
  `Relay`, `RELAY` and `relayed` stay ordinary, addressable participants. The refusal and the
  intercept that answers a request have to *agree*: a case-insensitive refusal paired with an exact
  intercept would accept `Relay` into a roster and then never intercept it — a name you can add and
  can never address.

- **`sh install.sh mac --feed-host … --agb-remote-path …` now exits 1 and installs nothing.** The
  command in the README, in the cookbook and in your shell history is refused: `--instance <name>`
  is required, and so is `--statedir` the first time you name an instance. Nothing is copied, no
  config is written, no plist is rendered, and the probe ssh is never made — the refusal is the first
  thing the `mac` role does.

  ```console
  $ sh install.sh mac --feed-host myfarm --agb-remote-path /opt/agbridge/agb
  install.sh: mac: --instance is required. Every Mac-side instance is named, so `agb instances`
  can say what exists and no command has to guess which one you meant. Pass --instance <name>,
  or --instance auto to name it after --feed-host.
  ```

  0.6.0 removed the unnamed instance's *privilege* from every Mac-side command — a bare
  `agb-refresh` and a bare `agb close-done` sweep all instances, a bare `agb forget-rows` is refused,
  `agb instances` lists what exists. But the installer still **created** a nameless one, so symmetry
  was a convention that any first install could break. It is a hard error rather than a warning
  because a warning on a *first* install gets ignored, and the asymmetry it warned about is then
  permanent on that Mac.

  **`install.sh farm` is untouched: it takes no `--instance`, and does not *require* `--statedir`.** A
  farm host has exactly one identity: `agb hook` and `agb status-line` resolve
  `~/.config/agbridge/config` on every invocation, so a named farm config is a file nothing opens.
  Both halves of that asymmetry come from the one fact.

  ⚠️ **"Does not require" is not "does not take" — keep the `--statedir` the Mac prints.** The farm
  role still accepts `--statedir` and forwards it to both `install-config` and `install-hooks`, and
  the `install.sh farm …` line the Mac hands you now carries it *always* (it used to be dropped when
  the Mac had none, which is no longer a state that exists). Paste that line as printed. Dropping the
  flag is not an upgrade shortcut: the farm falls back to its own default and writes hooks against a
  statedir the Mac's bridge never reads, and the feed then reports an empty farm for ever — the
  symptom that made the printed line unconditional in the first place.

  ⚠️ **The `--statedir` refusal has three wordings, because it now serves three different runs.** It
  was written for "a second instance without `--statedir`", and mandating `--instance` turned it into
  the message every *first* install gets — where "a second machine shares no disk with the first"
  describes a first machine that does not exist, and "`<config>` carries none to adopt" is plainly
  wrong when `--config` was typed and that file carries one. So: a **new** instance is told there is
  no config to read one out of, a config that **carries none** is named as such, and a typed
  `--config` is told that *that* is the reason. The reason is what says whether to pass the flag, fix
  the file, or drop `--config`.

- **`--statedir` is adopted on a re-install, so an upgrade is the original command minus that flag.**
  The transitive cost of the above is that every Mac install now needs a statedir, including the
  first — and re-typing a path on every routine upgrade is exactly how a wrong value gets copied
  forward. On this project's own Mac a `feed_host` copied that way was wrong and dormant in a config
  for hours. So a re-install reads the value back out of the config `--instance` derived and says so:

  ```
  instance: hostb -- label com.agbridge.hostb, config /Users/you/.config/agbridge/hostb/config
  statedir: adopted /home/you/.agbridge from /Users/you/.config/agbridge/hostb/config
  ```

  ⚠️ **It adopts only from the config `--instance` DERIVED, never through an explicit `--config`.**
  That flag may name any file at all — the default one, another instance's — and adopting a statedir
  out of it is precisely the failure the requirement exists to prevent: a bridge to the new machine
  reading the *other* cluster's directory, arriving by the one route the guard cannot see. So
  `install.sh mac --instance hostb --config <anywhere>` still demands `--statedir`, exactly as
  before. A small ergonomic loss for a shape nobody uses, and the difference between a guard and a
  hole.

  ⚠️ **Not a mirror of the `mac_id` adoption**, which probes this instance's config and *then* the
  default one. One Mac has one identity, so sharing an id across instances is the truth; sharing a
  statedir is the failure being refused. One candidate here, never a loop.

- **`agb-refresh` no longer tells a legacy nameless job to re-run the installer.** Every operator
  message that used to say *"re-run `install.sh mac`"* now says `--instance <name>` — and for the
  bare `com.agbridge` job that advice would be actively wrong, because a re-run does not repair it:
  it **mints a second instance beside it**, with its own config, label and rows map, and every row
  duplicated in the sidebar. All three places that can name that job — `start_label`'s *"could not
  start"* warning, the sweep's *"no bridge was started again for:"* summary, and the refusal you get
  when that job's plist is **there but unreadable** — now branch on the label: a named instance is
  told to re-run with **its own name**, filled in rather than left as a placeholder, and the nameless
  one is pointed at the migration under *Upgrading from ≤ 0.5.0*. The sweep is where this turns up,
  because it visits every plist in `~/Library/LaunchAgents`, a 0.5.0-era one included.

  ⚠️ The third one was missed on the first pass and is worth naming, because the rule is only ever
  broken this way: two of the three sites got the carve-out and the third got the mechanical
  `--instance <name>` insertion, so the one message that names a file *it cannot re-render* was the
  one still offering to re-render it. That refusal also said "which config uses" — with a hole where
  the instance name goes — on every run that did not type one.

### Fixed

- **The caret diagnosis now has to be stable before it makes the alarming claim.** `caret_reason`'s
  below-2 branch says *"not a composer at all — it needs a human"*, and that split rests on a
  composer sitting at column 2 — true of a **settled** one. ⚠️ The rendered view is measurably
  unstable (a 16 KB body read at 6 s showed *less* than the same body at 2 s), so a composer caught
  mid-repaint could plausibly read 0 or 1 and produce **a confident, alarming, wrong diagnosis about
  a healthy agent**. Two disagreeing reads now say *repainting* instead, and show both numbers.
  ⚠️ **Hypothesised, not sighted** — the shape earns the guard, not a sighting, so it costs exactly
  one extra read, on an already-failing path, on the alarming branch only; above 2 is a draft and a
  draft that appears is a draft. ⚠️ **The decision is unchanged in every case — we refuse** — which
  is what makes it safe to land with no live run: a wrong reading can now mis-word a refusal and can
  never permit a delivery.

- **✅ Measured: the caret gate catches the trust prompt, so a message can never answer that dialog.**
  The report was filed as *"both gates miss it"* with the hazard that a delivered message might
  **select an option**. Measured — trust prompt **column 1**, empty composer **2**, composer with a
  9-character draft **11** — so `wait_ready` refuses with code 3, nothing is typed, and the `\r`
  second call never fires. **That hazard is deleted rather than softened.** ⚠️ Both controls were
  load-bearing: without the empty-composer 2 a `1` could have been the tool failing, and without the
  2 + 9 "not 2" would not have shown the reading tracks content at all.

  ⚠️ **What it does cost is real**: code 3 means *held*, and the trust prompt persists until a
  **human** answers it — so such a peer receives nothing while every sender sees `queued` and exit 0.
  🔴 **Identical presentation to the exit-4 wedge, different cause** — the second entry to end at
  that signature, so both now carry the same diagnostic: *if a peer stops answering, read the relay's
  output first.*

- **`wait_ready` stops calling a startup prompt "somebody's draft".** It reported *any* column that
  was not `EMPTY_COLUMN` as *"the composer is not empty — somebody has a draft in it"*, so an
  operator whose agent sat on a trust question was sent to clear a composer **that does not exist** —
  and that line is the only thing they ever see, repeated until a human intervenes. `caret_reason`
  splits it on the measurement: **above** 2 is a draft; **below** is *not a composer at all*, naming
  the trust prompt as the measured case and saying it needs a human. ⚠️ It reports the column it
  actually read and does **not** claim a 1 *is* the trust prompt — that is one measured instance, not
  an identification.

  ⚠️ **And the catch is luck, not a defence.** Nothing makes a modal put its caret anywhere in
  particular; the next one may read 2. The open question — is there any *positive* signal that a pane
  is a live composer rather than a picture of one — survives its motivating hazard, and is demoted
  rather than closed.

- **✅ Verified live: the label/cwd collision line.** On a real relay, in the shape the bug actually
  takes — bound first so the previous binding existed, *then* collided. One line, both rivals named,
  printed **once across ~21 ticks**, and diagnosable without being told: the rival's *label* is
  nothing like the selector, so a reader can see the match came from the **cwd**. ⚠️ **The clearing
  half was checked in both directions too** — closing the rival made it resolve silently, and a
  *different* rival printed again naming the **new** one rather than replaying a cached string.
  ⚠️ And the **before** was captured as a negative control, which is what makes the after mean
  anything: the same harness on the previous commit stayed silent *through an explicit re-resolve
  that reprinted the whole binding table*, printing the id it would have printed on success —
  byte-identical output whether the resolve succeeded or fell back.

- **The trust-prompt report was corrected: there are three gates, not two, and the third was never
  evaluated.** It named `classify` and `peer_busy`. `wait_ready` also runs `pane_busy` and then the
  **caret** (`column == EMPTY_COLUMN`) — and a trust prompt is a **select list**, not a text input,
  so its caret has little reason to sit where an empty composer's does. ⚠️ **If it does not, the
  entry's premise is wrong** and the hole is a documentation defect rather than a delivery one; if
  it reports 2, the hole is real and *worse* than described, having survived three gates. One
  `surface cursor` reading settles it and nobody has taken it. ⚠️ **The general lesson is why the
  entry keeps its name and its content**: an enumeration of defences in a bug report is itself a
  claim, and this one came from the two gates whose *docstrings discuss each other* — the caret check
  is argued in `wait_ready`'s body and was invisible to a reader following the argument rather than
  the code.

- **A failed delivery no longer wedges the peer, and the verification finally checks the end the
  failure eats.** Two fixes, and **the order is the whole point**:

  1. ⚠️ **`deliver` clears the composer and proves it empty.** A verification that false-negatived
     used to drop the message *and leave the body in the peer's composer* — so the next message hit
     `wait_ready`'s caret gate and was held every tick for ever: the peer went deaf while its sender
     saw `queued` and exit 0. There was no known keystroke to undo it until `\003` was measured
     (a 3500-char draft cleared to zero, **agent alive**). A **verified** clear raises **3** — held,
     retried — because exit 4 existed only for "typing it again would leave two copies", and a
     verified clear removes that reason. An unverified one still raises 4 and now *says* the draft
     may be stranded. 🔴 Never two `\003` in one payload: adjacent bytes kill the agent, one second
     apart does not — so the rule is a property of a single call, which is checkable, rather than of
     history, which is not.
  2. ✅ **The probe is both ends** (`probes_for`). `deliver` exists against a dialog swallowing
     keystrokes — which eats the **head** — and probed `body[-40:]`, the **tail**, so it passed on
     exactly the damage it was written to catch. Now measured safe: the composer renders both ends
     and elides the middle, and the head it keeps is a **constant ~104 characters** at 1 K, 4 K,
     8 K and 16 K. The old objection — that a long body's head may have scrolled out — was simply
     wrong.

  ⚠️ **Fix 2 alone would have made things worse.** Requiring both ends makes a false negative *more*
  likely, because the rendered view is measurably unstable (a 16 KB body read at 6 s showed **less**
  than the same body at 2 s — longer is not better, it is unstable). It was only affordable once
  being wrong cost one more tick instead of a deaf peer.

  ⚠️ **Unfixable by this route, and worth knowing:** nothing on screen can see the **middle**. The
  composer elides it, so a body damaged between its ends passes both probes and no third probe helps.

  ⚠️ **And one test fixture was the reason the old probe looked sufficient**: it faked the pane as
  the tail *alone*, a harness simpler than the thing it modelled. It now carries both ends, plus the
  companion that is the actual defect — a body that lost its head must be refused.

- **🔴 The unbounded reap was in THREE places, and two of them promised in writing that it was not.**
  After fixing `agb-peer._spawn`, searching for the *shape* rather than waiting for the next report
  found the identical `kill()` + untimed `communicate()` in **`agb_mac._run_command`** — whose
  docstring says *"the reason `agtermctl` failing can never wedge the bridge: … a process that never
  returns … comes back as data rather than as an exception or a hang"* — and in
  **`tests/conftest.communicate`**, whose first line is *"`proc.communicate()` that can never
  hang"*. A grandchild inherits the pipes, so both blocked for ever. ⚠️ The `agb_mac` one is the
  worst of the three: it is the **bridge's rendering path**, so one wedged helper stops every row
  updating. All three bounded now, `poll()` rather than `wait()` on the way out. ⚠️ **The tell, each
  time, was a docstring making a promise about behaviour rather than describing code** — worth
  carrying, because the two found by reading were both in files whose comments claimed immunity.

- **`_run_command` stops saying "could not run" about a command that ran.** It answered `None` for
  both *could not be started* and *timed out*, so a wedged agterm was logged as a missing binary —
  sending the reader to check the install, the one thing that was fine. Now `TIMED_OUT` for the
  second, with `_rc_reason` giving three sentences for three facts. Only one caller read `rc is
  None` and it was that log string; `rc == 0` is still the only success test. (The *hard* half of
  `a-timed-out-session-new-can-mint-a-second-row` — what `_new` should do with "unknown" — is
  untouched.)

- **A label colliding with another row's `cwd` is no longer silent.** `resolve_all` kept the
  previously-resolved row without a word when a label stopped resolving. ⚠️ **One fallback, two
  causes, and only one of them wanted quiet**: a row briefly absent while `agb-refresh` re-mints it
  is transient and heals, while an ambiguous label is permanent — and the silence masked it until
  the next restart, which is when it failed loudly, at the worst moment, for a cause introduced
  hours earlier. The reason is now reported once per unchanged message, and since `resolve` names
  the rival rows, the line is what tells you the collision is a **cwd** and not a second label. The
  throttle is cleared on a successful resolve, or a fixed collision would stay "already reported"
  and its recurrence would be silent for ever. `docs/cookbook.md` carries the warning beside the
  existing prefix one.

- **One test was racing its own hang-guard.** `test_what_a_default_install_renders_leaves_the_bridge_where_it_was`
  failed on a host at load average 34, reporting *"subprocess did not exit within 30s"* — which reads
  exactly like a hang in `install.sh`. It is not flaky by accident: a full `install.sh mac` measures
  **25.4 s** against `conftest`'s **30 s** global budget, so the guard was protecting nothing. Given
  its own 180 s budget rather than raising the global, which would have weakened every other test's
  guard to accommodate one slow one.

- **`agb-peer`'s timeout is now actually a timeout.** `_spawn` killed a timed-out child and then
  called `communicate()` with **no timeout**, waiting for EOF on the pipes — and a grandchild
  inherits those pipes, so a command spawning a helper that outlived it blocked that second wait
  **for ever, with no output**: strictly worse than the timeout it was reaching for. Measured live:
  `exec sleep 300` returned at 31 s against a 30 s budget, correctly; the same hang one process
  deeper never returned at all. Now `communicate(timeout=REAP_TIMEOUT)` in its own `try`, with
  `poll()` rather than `wait()` on the way out — `wait()` would reintroduce the block it avoids.
  Worst case is `timeout + 5 s`.

  ⚠️ **Bounded is not reaped, deliberately, and the reason the better fix was declined is worth
  more than the fix.** `start_new_session=True` + `os.killpg` is correct rather than merely bounded —
  but it **detaches every child from the controlling terminal**, so Ctrl-C at an `agb-peer relay`
  prompt would stop reaching the subprocess it is running. That is a real regression in the
  interactive path, paid for a case that is unreachable with today's single-binary `agtermctl`.
  Recorded at the call site and in the backlog entry, so the next reader does not re-derive it.

- **🔴 Exit 4's stated cost — "one lost message and a loud line" — is wrong, and the correction came
  from connecting two comments already in the same function.** `try_deliver` drops a message whose
  text was typed but could not be verified, on the reasoning that one lost message beats two copies
  in a composer. Twelve lines away, its own throttle comment describes *"a peer whose composer has a
  draft in it stays that way until a human looks"*. ⚠️ **Exit 4 is a producer of that draft.** The
  chain: a verify false-negative drops the message with the body left unsubmitted → the next message
  hits `wait_ready`'s caret gate, raises code 3, and is **held every tick for ever** → that peer
  receives **nothing at all**, while the sender sees `queued` and exit 0 with no error on its side.
  So the real price is a lost message, a loud line, **and a deaf peer**.

  ⚠️ **And the relay cannot clean up after itself** — MEASURED: `\025` (Ctrl-U) and Escape through
  `session type` both do **nothing** to a Claude composer; a 500-byte body stayed on screen while a
  900-byte one was typed after it. There is no keystroke that undoes the state this branch creates,
  which is what makes "drop is safer than retry" fail on contact: it would be true if dropping left a
  clean composer.

- **The recurrence signature is not what it looked like.** Direct typing concatenates, but **the
  relay does not** — the caret gate holds the next message rather than typing after the leftover. So
  what to watch for is not a message arriving with an unexpected prefix; it is **a peer that silently
  stops receiving**, with a repeating "the composer is not empty … somebody has a draft in it" line
  in the relay log. ⚠️ That log is the **only** place it is visible, and neither agent can see it.

- **`agtermctl session text` is a RENDERED, ELIDED view — it draws the two ends and drops the
  middle.** Measured: a 3488-character body with distinct head and tail markers read back as **1459
  visible characters, both markers present**. ✅ So a head check is as available as a tail check —
  which **refutes the stated reason** `agb-peer`'s probe was tail-only ("the head may have scrolled
  out"); it does not scroll out. ⚠️ And nothing on screen can see the middle, so any screen-based
  verification is structurally blind to a body damaged between its ends.

  ⚠️ **The view is also still SETTLING when it is read.** The same body, delivered identically, read
  twice: the first found the tail marker **absent**, a slightly later read found it present. So the
  verification can **false-negative on a message that arrived perfectly** — and what that costs is
  not a duplicate but a **drop**: `deliver` raises code 4, `try_deliver` ends `return error.code ==
  4`, and the Return is sent *after* the verification — so the body sits in the peer's composer
  **unsubmitted** while the relay reports it was not sent. (`VERIFY_READS = 4` at 1 s intervals is
  what usually saves it.)

  ⚠️ **The two results pull opposite ways, and that is the decision they leave.** The elision makes a
  both-ends probe **possible**; the settling makes any **stricter** probe more likely to
  false-negative, and a false negative drops the message and strands a draft. The fix became
  available and *less* attractive in the same measurement.

- **The paste theory of the truncated message is dead, and the head has no mechanism again.** The
  argv-vs-`--stdin` discriminator ran: **identical, no collapse on either**, 800–8000 bytes. So the
  843-character figure in `COMPOSER_GLYPHS` cannot be reproduced on the current build — **version-
  stale rather than wrong**, since it was a live symptom, but not to be quoted as current. Nothing on
  our path brackets, so a relay-delivered body arrives as ordinary typed text and **never reaches
  `rendered`'s `pasted` branch**: that hole is real and **unreachable from the relay**, and the
  tail-probe hole is the only live one.

- **❌ The empty bracketed-paste probe does nothing** — `\033[200~\033[201~` into a live composer
  leaves the screen byte-identical. So the zero-content probe for "is this a real composer?" does not
  exist, and any probe of that shape must **carry a body**. ⚠️ That is a different proposition and
  has to be argued on its own: typing content into a pane you are unsure about is the thing the
  trust-prompt entry exists to prevent. "Cannot answer the prompt" is a weaker guarantee than
  "changes nothing", and only the second made the idea attractive.

- **🔴 Two measurements now disagree about what makes Claude Code collapse an injection into
  `[Pasted text #1]`, and the discriminator is written down rather than guessed.** `COMPOSER_GLYPHS`'
  comment has said **843 characters** — a length — since it was written. Re-measured on a fresh
  composer: 800 / 900 / 3500 / **8000** raw bytes showed the body **in full**, head and tail, **no
  placeholder at any size**; the same bodies wrapped in `\033[200~ … \033[201~` collapsed at **900**.
  On that reading the trigger is **bracketed paste**, a framing, and length is irrelevant. The two
  are not obviously reconcilable — the 843 figure was a *live symptom* through this file's own
  delivery path, not a lab result. ⚠️ **The one difference not controlled for**: `ctl.type` passes
  the body as a plain **argv element**, while the re-measurement drove **`--stdin`**. If agtermctl
  brackets one and not the other, both are right about different commands and only the argv form is
  on our path. Neither figure should be quoted as settled until that is run.

  ⚠️ **Consequence if the new reading holds: the paste theory of the lost head dies.** Nothing in
  this repo wraps anything in bracketed paste, so a relay-delivered body would arrive as ordinary
  typed text, land in full, and never reach `rendered`'s `pasted` branch — making that hole real but
  unreachable from the relay, leaving the tail-probe hole fully live, and leaving the lost head with
  no mechanism again.

- **❌ The leading candidate fix for the verify-nothing branch is dead: Claude's placeholder carries
  no count.** A 3500-byte paste renders exactly `❯ [Pasted text #1]` and `paste again to expand` —
  nothing numeric anywhere on the pane. The `NNNN chars` form is **Codex's** alone, and the docstring
  had them looking like two spellings of one thing. So "verify the body against the placeholder's own
  count" is unavailable on the Claude side.

- **A candidate positive signal for the trust-prompt hole, from the same run: bracketed paste.** A
  composer answers it visibly (`[Pasted text #1]` / `paste again to expand`); a select-list modal has
  no such affordance. ⚠️ **The property that makes it better than matching the prompt's wording is
  that a paste is CONTENT, not a keystroke** — so the probe cannot answer the prompt it is probing,
  and it fails *closed* where a wording match fails *open*. Needs its own measurement first: does an
  **empty** `\033[200~\033[201~` produce any visible change?

- **`agtermctl session type` measured: no size limit to 64 KB, byte-exact — and the obvious way to
  test that gives the wrong answer.** This doc carried no length clause at all, which is what let
  the transport stay a suspect for a truncated message. Measured against a raw-mode reader run as an
  agterm session's `--command`: **byte-exact at 216 B through 64 KB, head and tail markers intact at
  every size**, four times past the 16336-byte tmux ceiling that bounds anything `agb-peer` can put
  on the wire. `session type` is eliminated. ⚠️ **A `cat`-based harness would have incriminated it**:
  a pane running `cat` puts the tty in **canonical** mode, where the line discipline caps one line at
  **≈1024 bytes on macOS, 4096 on Linux** — a confound that does not exist on the real path, because
  Claude Code's composer runs raw. The cheap tell is that the confound loses the **tail** while the
  bug loses the **head**: a harness whose failure signature does not match the symptom is measuring
  something else. Both are in `docs/agtermctl.md` now, the trap recorded beside the result.

- **An agent at Claude Code's startup trust prompt holds every message for ever — and the dangerous
  half of that report turned out not to exist.** Observed live: *"Is this a
  project you created or one you trust?"* renders `❯`, a `COMPOSER_GLYPH` — so `classify` calls a
  blocking modal `MODE_COMPOSER`, and because it appears **before the agent has ever run a turn**
  the hook-derived status is `-` rather than `active`, so `peer_busy` passes it too. ⚠️ This is
  exactly the Dialog Window Vulnerability `peer_busy`'s docstring cites as the reason the two gates
  are non-redundant, arriving in the one variant where the second gate has nothing to say. **Two
  gates that each cover what the other misses still leave whatever neither covers**, and nothing
  said so until now. The hazard is not a lost message: `deliver` sends `\r` as a second call, so a
  message here plausibly **answers the prompt**. Recorded rather than patched, because a
  wording-match fails *open* and the general form — a modal wearing the composer's chrome — will
  keep coming back in a new costume:
  `docs/backlog/an-agent-at-the-trust-prompt-holds-every-message-for-ever.md`.

- **The double-delivery item is CONFIRMED, with sender-side proof.**
  `a-timed-out-session-type-is-retried-and-can-double-deliver.md` was found by search and said "Not
  observed live"; a peer's message then landed **twice, verbatim**, in a receiving agent's composer.
  The sender produced its full queue-id log — nine sends, nine distinct ids — and the duplicated text
  has **exactly one**. A re-send mints a new id, so no second send exists. The circumstances match
  the mechanism exactly: the receiver was mid-compaction, which is precisely when a `session type` is
  slow enough to hit the 30 s timeout while still landing.

  ⚠️ **The discriminator exists and is thrown away.** `compose` renders `[chat from <name>] <text>`
  and drops `message["id"]` — so the one field that separates *delivered twice* from *sent twice* is
  discarded exactly where it would be useful, and it took two agents comparing logs to establish
  what one line of output would have made self-evident. Written down rather than changed: it is a
  wire-visible format change, `[chat from relay]` is matched as a **literal** by the relay's own loop
  guard, and `SKILL.md` tells every agent to reply to anything shaped `[chat from <name>]`.

- **`SKILL.md` now says arrival order is not send order, and that a message can arrive twice.** A
  message is **held** while the peer is mid-turn, so arrival order is a property of *the peer's
  availability* — something sent later can land first, and a reply that seems to answer the wrong
  message usually has that explanation. Both caveats sit beside the no-receipt one, because all
  three are the same thing: each end can see only its own half.

- **A message can arrive with its head missing while both ends report success — recorded, not yet
  fixed.** A peer agent's message landed in a composer starting mid-sentence; the sender read
  `queued … as #id`, the relay read `delivered`, and the conversation ran two more exchanges before
  a human noticed the reply did not answer the question. ⚠️ **The transport was measured and
  exonerated**: the tmux pane option this comment called *"the message, 3 KB, exact"* actually holds
  **16336 bytes** on tmux 3.5a, and 16337 is **refused** (`command too long`, rc 1) with the previous
  value retained — so `cmd_send` raises rather than losing anything, and `show-options -p` renders
  the value on one line with both ends intact. The comment is corrected in place; being five times
  under, with nothing said about the failure mode, is what sent the first investigation at the wrong
  half of the wire.

  ⚠️ **What makes it silent is `deliver`, and the sharp part is that its check is blind to the
  failure it exists for.** Its own docstring says the verification is there because *a permission
  dialog can appear between the cursor check and the keystrokes, swallowing them* — swallowing the
  **leading** ones — and the probe is `body[-40:]`, the **tail**, which a lost head leaves intact.
  Beneath that, `rendered`'s `pasted` branch verifies no content at all: a long injection becomes
  `[Pasted text #1 …]`, the body is not on screen, and the branch confirms only that *a* paste of
  *some* length happened — so the messages most likely to be damaged are the ones whose content is
  never checked. Until this is fixed, `delivered` means "a tail arrived, or something was pasted".
  Why the head is lost is still unknown and needs a live agterm;
  `docs/backlog/delivery-is-verified-on-the-one-end-a-lost-head-leaves-intact.md` has the
  measurements, both holes, the experiment that would settle it, and why probing both ends is not
  the obvious safe repair.

- **`peer_busy` now names compaction as the second reason its two gates are not redundant.** A peer
  mid-autocompact reported `active` while the screen read `composer` — the pane genuinely looks
  ready to type into, and the hook-derived status is the only thing between a message and an agent
  that would drop it. The docstring justified two gates with the permission-dialog case alone, and a
  single example reads like *the* reason rather than like *a* case of it. The general rule, now
  written down: the screen can look typeable while the agent cannot receive, and the screen is the
  half that cannot tell.

### Added

- **`agb-hangout` — a skill for two agents talking with no task attached.** `agb-peer` carries
  messages between agents, and usually there is work on the other end of one. This is the file for
  when there is not: an open-ended conversation, symlinked into `~/.claude/skills` and
  `~/.codex/skills` so both ends read identical instructions. Nothing new runs — it is guidance over
  `agb-peer send`, not a command.

  ⚠️ **The `[hangout]` marker on the first message is the whole protocol, and it is a marker rather
  than something the two agents agreed earlier because an agreement does not survive a `/clear`.**
  The skill's own description names the marker, so a peer that has the skill installed loads it
  *from the message* — a cleared context needs no memory of you, which is the case that would
  otherwise have no way in. A peer without the skill still gets a legible invitation, because the
  opener is required to carry who you are, that nothing needs actioning, and how to reply. Without
  the marker there is nothing separating "let's talk" from "here is a task", and a peer reading a
  chat opener as a work request answers it like a ticket.

  ⚠️ **It does not end on its own.** Every message arrives as a prompt that produces a reply that
  arrives as a prompt, so the loop is self-sustaining and spends tokens on both machines until
  somebody stops it. The skill says so where an operator will read it rather than leaving it to be
  discovered, and names three exits: the user's word wins immediately, somebody signs off, or it has
  gone flat. The sign-off gets a ⚠️ of its own — two polite agents will ping-pong farewells for ever,
  and each round is a full turn on both machines.

  The rest is about being worth talking to, and the failure mode it is written against is not
  silence: two models trained to be helpful converge on violent agreement in about four exchanges.
  Hence *disagree when you actually disagree*, *be specific*, and a length budget — this is chat, and
  an essay landing in somebody's composer is a wall rather than a message.

  ⚠️ **`agb-peer who` is asynchronous, and the skill says so where it tells you to use it.** The
  answer arrives on a *later* turn as a `[chat from relay]` message, so a name cannot be looked up
  and used in the same breath, and silence is ambiguous (no relay, or not a participant) — ask the
  user rather than guessing, because a message to an unknown name is dropped with no error. A cold
  agent needs none of this to *reply*: the relay signed the message it woke up to.

  ⚠️ **Not verified: the anti-convergence rules have never been under strain.** They were written
  out of an exchange between two agents that argued — a modality conclusion corrected, a measurement
  that turned out to answer a different question than the one asked, three backlog items unified into
  one over their author's framing. That conversation was never going to converge, so it cannot show
  whether these rules *prevent* convergence or whether the friction was there already. A reader whose
  own chats run to agreement may read that as the rules working.

- **`agb-dashboard` — watch several agent rows at once, by name.** agterm can show a view-only grid
  of up to nine live sessions, and until now driving it meant knowing the **row ids**
  (`agtermctl dashboard A1B2C3D4:left E5F6A7B8:left`). `agb-refresh` re-mints every id, so an id you
  wrote down is dead — the same trap `agb-peer-setup` exists to route around. This takes **row
  selectors**: a label substring, an id, or an id prefix, resolved fresh on every run.

  ```console
  $ agb-dashboard alice bob
  agb-dashboard: 2 cell(s)
    AAAA1111 left alice · box01 · /w/api · %7 · 3s
    BBBB2222 left bob · box02 · /w/dv · %3 · 1s
    Run this from a terminal OUTSIDE agterm -- that is where the hold was
    measured to stay responsive while a grid is up.
    This grid does NOT follow: an `agb-refresh` re-mints every row id and
    leaves dead cells here. `agb-peer relay --dashboard` is the one that
    re-resolves and re-opens.
  press enter to close the grid (Ctrl-C closes it too)
  ```

  ⚠️ **It refuses rather than opening a grid missing the agent you opened it to watch.** That is the
  whole reason it is a wrapper and not an alias. When only *some* cells resolve, agterm exits **0**,
  opens the grid without the rest, and names the casualties as `unresolved: <id>` on **stdout**
  alone — so the honest-looking grid is the incomplete one. An unresolved selector, an ambiguous one
  and a participant in a pane the grid cannot show all open nothing; a shortfall agterm reports
  after the fact **closes the grid it already opened**, because exiting on it would leave exactly
  the partial grid the command exists to remove.

  ⚠️ **Every cell carries an explicit pane, and never a bare id.** A bare id takes *every* pane of
  its session and agterm's nine-cell cap counts **panes**, so a row somebody opened a `[s]` split on
  silently costs two cells — the same rows fit, or do not, depending on state nobody is looking at.
  An explicit pane turns a cap on panes into a cap on agents, which is what makes the "9 cells;
  got 10" refusal exact instead of a guess. A roster's `right` is preserved, not forced to `left`.

  ⚠️ **The grid is held in the foreground, and that is what owns it.** agterm has exactly one grid
  and no ownership token, so a grid nobody closes is a grid in everybody's way. The close is in a
  `finally`, so **`Ctrl-C` and an exhausted stdin close it too** — `sys.stdin.readline()` returns
  `""` at end of input and does not raise, and treating that as an answer is what made
  `agb-peer-setup` spin 305,869 times in six seconds. `--detach` is the explicit hand-over: it
  leaves the grid up and prints the literal `agtermctl dashboard --close` you will need, because
  after it nothing else can.

  `--roster <file>` grids a relay roster's members (a **one**-participant roster is fine; a grid
  needs nobody to talk to). `--mru` lets agterm pick — and is the one mode exempt from the strict
  check, because it asserts no membership and so has no shortfall to detect; it says so in its own
  output rather than leaving a `--mru` grid looking like a checked one.

  ⚠️ **Not installed by `install.sh`.** Symlink it onto `$PATH` beside `agb-peer`, which it loads by
  path — same as `agb-peer-setup`. And it does **not follow**: `agb-peer relay --dashboard` is the
  feature that re-resolves and re-opens as ids move.

- **`agb-peer-setup` — build a relay roster by picking rows instead of typing the grammar.**
  Writing one by hand means knowing three things that are not obvious: that `<row>` is the row's
  **label** and not the title agterm shows you (a title is `label · host · cwd · pane · beat`, and a
  roster line is split on **whitespace**, so a pasted title silently is not a spec), that a `:<tmux>`
  suffix with no `@<target>` reparses as a *pane*, and that editing the file while a relay runs has
  to be atomic or the relay reads a truncated roster as somebody leaving.

  ⚠️ **It withholds the "use the row's own host" option when `host_<name>` remaps that host**,
  because the relay hands `--host` to ssh **verbatim** and never applies the mapping. That
  combination produces a roster that parses, validates, prints a working-looking next command, and
  then silently never delivers — which is indistinguishable from a broken agent.

  ⚠️ **It rewrites the file as generated output, so `#` comments and blank lines are not preserved**,
  and it says so **when it opens the file** rather than when you save — by then you would be
  choosing between losing the comments and losing the work. Found during the first live run: the
  cookbook's own hand-written roster example carries a comment on line 1, so the documented way to
  author one produced a file this tool ate without a word.

  ⚠️ **It exits on an exhausted stdin instead of spinning.** `sys.stdin.readline()` returns `""`
  at end of input — it does not raise `EOFError`, which is what `input()` does — so treating that as
  an ordinary answer made every re-prompting loop run for ever. Measured against the real binary
  with stdin closed: **305,869 menu prints and 1.8 million lines in six seconds.** Any
  non-interactive invocation did it: a pipe that runs out, a closed stdin, a here-doc shorter than
  the prompts. The discriminator is the newline — pressing enter gives `"\n"` and means "take the
  default", EOF gives `""` — so the raw value is tested before stripping, because stripping makes
  the two indistinguishable and the dangerous one becomes the harmless one.

  Found by the first live run. The test fake raised `EOFError`, which real stdin never does, so the
  harness described a world where this could not happen and the EOF test passed against the broken
  code throughout. The fake now returns lines with their newline and `""` when exhausted, which is
  what `readline` does.

  Not installed by `install.sh`; symlink it beside `agb-peer`.

  **Verified against a live agterm on 2026-08-27** — the picker, the `[done]` prefix strip on a real
  stale row, the substring-ambiguity refusal, the write and its `0600` mode, the conflict path with
  its recovery draft, the symlink install, add and remove against a *running* relay, and both
  branches of the `[a]` gate. ⚠️ **Two of the three defects above were found by that run and by
  nothing else** — 2462 tests, three review rounds and fifteen mutation-checked tasks did not
  surface either. **Not verified**: the `[?]` (feed-quiet) prefix, and a second instance's
  `host_<name>` table rendered live — in both cases because no such row existed to pick, and
  manufacturing one meant breaking a working bridge or installing an instance on somebody's
  machine.

- **`agb-peer` can now write a roster file safely, which is what an interactive builder needs.**
  Editing a `--roster` file by hand while a relay runs means knowing that the write has to be
  atomic: a file rewritten in place can be read truncated at a line boundary, and a truncated
  roster parses *cleanly* as a shorter one — indistinguishable from somebody leaving, so the relay
  applies a departure nobody asked for. `docs/commands.md` stated that as a rule for whoever edits
  the file; `write_roster_file` is the code that keeps it.

  It is **gated on the file's bytes**, not on a `(mtime, size, inode)` key — matching the byte gate
  `RosterReader` already uses, and for the reason written there: `os.stat` on a network mount is
  served from the attribute cache, so comparing what you read satisfies invariant 6 by construction
  rather than by a comment. An editor re-saving identical content is therefore *not* a conflict,
  which a stat key would have got wrong.

  ⚠️ **`roster_bytes` has three answers where `read_roster_file` has two**, and the third is a
  raise. Folding "unreadable" into "absent" would make the gate compare `None == None` and rename
  straight over a roster nobody could read — vacuous exactly when it matters. That is invariant
  12's rule ("I could not answer" is not "the answer is nothing") applied to the one file this tool
  writes.

  ⚠️ **`RosterConflict` is its own exception class because its caller must *recover*, not print and
  exit** — an in-memory draft is still good and has to be persisted before anything else happens.
  Everything meaning *do not write*, including the unreadable case, leaves through that one door;
  anything reaching the handler as a bare `PeerError` would lose the draft silently, with every
  test green.

  `write_draft_file` is the **ungated** sibling, for exactly that recovery draft. Routing it
  through the gated writer would raise `RosterConflict` from inside the conflict handler and lose
  the draft it was called to save — so the one path standing between a conflict and lost work
  cannot itself be conditional.

  The temp name is `agb.temp_name` **minus the host component** (`<name>.tmp.<pid>.<rand>`): that
  one carries a host because two machines may write one target over NFS, and a roster is edited by
  a person at the Mac the relay runs on. Taking it would have meant spelling `own_host()` a third
  time. A **fixed** `.tmp` name — the shape `write_chat_file` uses, which is right for a
  per-message-id file — would have two editing sessions share one temp and publish the torn read
  this whole mechanism exists to prevent.

  Mode is `0600`, **chosen rather than inherited**, and restated with `fchmod` on every write
  because `O_CREAT`'s mode argument is umask-filtered. Under the ordinary `022` that restatement
  looks like dead code; measured under `umask 0600` it is the only thing standing between the relay
  and a roster at mode `0000`. A roster loosened by hand therefore tightens again on the next save.

- **`agb-peer` is 0.4.0** — `who` is a new verb (0.3.0), the roster writers are new here, and `--version` is the only way to tell which side
  of an independently-installed pair is old. ⚠️ `install.sh` does not install `agb-peer`, so both
  the relay's copy and the agent's come from a checkout and can drift.

- **`agb-peer who`** — an agent asks who is in the conversation. It sends the request and prints
  that it did; ⚠️ **the answer arrives as a MESSAGE on a later turn, not on this command's output**,
  because there is no channel from the relay back to a command that has already exited.

  It also prints what silence means: **no relay is running, or this pane is not one of its
  participants** — indistinguishable, and neither worth retrying.

  ⚠️ **It checks `$TMUX_PANE` itself rather than letting `send` do it.** Delegating first would
  raise *"send must run inside tmux"*, which reads as nonsense from `who` — the same phrasing leak
  `wait_ready`'s `retries=0` note records.

- **The relay answers `who`.** An agent addresses a message to the reserved name `relay` with the
  single word `who`, and the relay replies with the membership —
  `[chat from relay] you=alice peer=bob peer=carol` — delivered like any other message.

  ⚠️ **The asker is the pane the doorbell rang in**, which is `try_deliver`'s existing rule and the
  reason this needs no identity of its own: an agent cannot print into another agent's pane, so the
  place is the only part of a message that cannot be misstated.

  ⚠️ **Only that exact word is answered, and that is a loop guard rather than input validation.**
  `SKILL.md` tells an agent that anything arriving as `[chat from <name>]` is a peer talking to it
  and to reply — so an answer signed `[chat from relay]` invites a reply, which is another message
  to `relay`, for ever. A polite *"thanks"* is not the token, so it is dropped and named, and the
  loop cannot start.

  **Membership only** — no status, no timestamp. The answer is composed when the request is *drained*
  and delivered whenever the composer is free, so anything time-shaped in it would be a snapshot from
  one moment read as truth at another.


### Fixed

- **A grid call that agterm never answered was reported as one that refused — and on the relay it
  starved the message pump.** `Ctl.dashboard` has three outcomes, not two: it raises when agtermctl
  cannot be started, answers a status when it ran, and is **killed at the 30 s timeout** when it ran
  and never came back. Both callers spelled that third one "the dashboard would not open".

  It is the one failure where the grid may well be **up**: the timeout fires against a *wedged*
  agterm client, and the measured wedge was a view-only client attached to a **dashboard**. So
  `agb-dashboard` ended a run with agterm's one screen full of cells, nothing saying so, and no close
  command printed — the exact failure the command exists to prevent, delivered by its own error
  message. It now says the grid **may be up**, prints `agtermctl dashboard --close`, and deliberately
  does *not* close: with no proof this run opened the grid, a close reaches for one that may be
  somebody else's, through the same wedge.

  On `agb-peer relay --dashboard` it was worse in two ways. The ownership latch stayed `False` — a
  claim of proof that nothing had established — so the exit path walked past a grid the relay may
  own. And the earlier fix for a *failed* open ("do not record the cells as shown, so the next tick
  retries") turned a wedge into a 30-second blocking call **every tick**: the grid update runs before
  the message pass, so at `--interval 2` the pump dropped to roughly one pass in fifteen. That is the
  one outcome the comment three lines above forbids in as many words; the fix for one bug shape
  walked in underneath it. The latch is armed pessimistically now, and the retry **backs off**,
  doubling from one tick to 32 — not "give up for the run", which would cost the grid for a transient
  wedge, and not every tick, which is the starvation. Any call that *answers*, refusal included,
  clears the back-off; a refusal is still retried every tick, because it costs nothing.

  ⚠️ **The general shape, recorded in `CLAUDE.md` as shape D** beside the three already there:
  **an indefinite outcome collapsed into a definite negative.** It is invariant 1 ("liveness is
  proven, never inferred") and invariant 12 ("I could not answer" is not "the answer is nothing")
  arriving in a new subject, which is why neither caught it. The check is at every branch on a
  three-valued result: ask whether the `else` is a *proof* or merely *not the yes*.

> The seven entries below came from **going looking for more of the same three shapes** after the
> named ones were fixed, rather than from a report. Each is the same defect one function over.

- **A grid the relay could not CLOSE was recorded as shown, so the close was never retried.** The
  third incomplete outcome in `update_grid`, beside the failed open and the partial one it already
  handled: when membership falls to nobody the relay closes its grid, and if that close failed the
  cell set was advanced anyway — so the next tick took the `fresh == shown` early return and a grid
  of participants who had left stayed on agterm's one screen for the rest of the run. `shown` is
  held now and the message throttled, exactly as the other two are.

- **`agb-peer relay` could go deaf with no error anywhere, permanently, after one tmux hiccup.**
  The first `send` from a pane pins `automatic-rename off` (tmux's default is *on*, and it would
  otherwise wipe the doorbell) and then memoises the window's base name. The memo is the gate: every
  later send sees a base and skips the whole block. So the two were in the wrong order — a pin that
  failed once, which a 30 s timeout against a wedged agterm client will do, was never attempted
  again, and the consequence is the one written beside it. The memo is the last write now: it
  records *all of this was done*, not *some of it was tried*.

- **A message could be orphaned in the chat directory for ever by a closed stdout.** On the file
  transport — an agent on a batch node whose tmux socket the Mac cannot reach — the *printed*
  doorbell is not a report about the send, it **is** the send: `drain_files` cannot sweep a shared
  directory (a message carries its recipient, not its sender) so it fetches by name, and the only
  names it has are the markers on that screen. The file was written first and the doorbell printed
  after, through three ordinary `stdout` writes that raise on `| head` or a full disk. The doorbell
  goes first now; the remaining order is the harmless one, because a doorbell whose file is missing
  is the documented `FETCH_GONE` path.

- **`write_chat_file` left its `.tmp` behind on any failure**, alone among the four temp+rename
  writers in that file — so an ENOSPC or a `Ctrl-C` mid-write leaves `<id>.msg.tmp` in a *shared*
  chat directory where nothing collects it and nothing names it.

- **The resolver's throttle was the sixth one with no clear**, and the same defect as the alias note
  above it. A roster participant whose label matches no row is reported once and then throttled; the
  note was missing from `_name_notes`, and it can have no in-place clear (only a name that has
  *never* resolved reaches that throttle, and such a name is in no `resolved` dict to visit). So a
  participant with a wrong label who leaves the roster and comes back with the same wrong label was
  reported **nowhere** — and without `--dashboard` that line is the only thing the relay ever says
  about them.

- **A roster refusal from `agb-dashboard` could name neither line to edit.** Two roster lines
  pointing at one dead label produced two byte-identical `oldrow: no row matches it` lines — the
  same "go and look up who vanished" as the row-id spelling, in the function whose own docstring
  argues against it. A refusal carries both tokens now (`carol (oldrow)`): the participant says
  which line, the label says what on it is wrong. A positional selector is its own name and renders
  unchanged.

- **Four comments that had become false**, all of them claims about code somewhere else — the one
  place this project keeps getting caught. `CLOSE_COMMAND` said it was printed at two sites when the
  fix above made it three; `HANDLED_ERRORS` gave two reasons for its `OSError` entry, both wrong (an
  unreadable roster arrives as `PeerError`, and nothing here loads `agb`) while the entry itself is
  right for a *third* reason; `Ctl.dashboard` said its guard belongs at "every call site", which is
  wider than the rule and would have argued for guarding a call that cannot orphan anything; and
  `hold`'s note about writes inside its own `try` did not cover the one write that cannot be.

- **A lost stdout orphaned the grid `agb-dashboard` had just opened.** `agb-dashboard alice | head`
  closes stdout the instant `head` exits, and every line printed after the grid went up — the cell
  report, the hold's own banner — was outside any cleanup guard. The `BrokenPipeError` unwound past
  the close, `__main__` reported it as a handled `OSError`, and the run exited with a grid on the
  only screen agterm has and nothing owning it. Demonstrated with a probe rather than argued:

  ```
  BrokenPipeError closed
  [('tree',), ('dashboard', ['AAAA1111:left'])]      # opened, never closed
  ```

  ⚠️ **Two fixes, not one, and they cover different writes.** `out` is wrapped in a writer that
  cannot raise **at the moment the grid goes up**, so a report added later is covered by
  construction rather than by the next author remembering; and `hold` prints its banner **inside**
  its own `try`, because the function whose entire job is the close may not depend on who called it.
  Writes *before* the grid exists are deliberately still fatal — there is nothing to orphan, and a
  `--version` that cannot be printed has failed. `agb-peer` grew exactly this guard for the relay's
  `say` earlier in the same release; this is the fourth instance of the shape.

- **The refusal named an excluded participant by ROW-ID PREFIX**, which is the one thing this
  command exists to stop people looking up. A roster line is `drawer=<row>:scratch`, and
  `agb-dashboard` answered `BBBB2222 (scratch)` — an id the operator never wrote and that
  `agb-refresh` re-mints anyway. It says `drawer (scratch)` now. The relay carries the argued rule
  verbatim ("by NAME, not by row id — a hex prefix would make them go and look up which participant
  vanished") and it applies *harder* here, because this refuses: the token in the message is the
  only clue which roster line to edit. The name was being thrown away at the point the roster was
  parsed; it is carried on the cell now, in the relay's own `(name, id, pane)` shape.

- **An over-cap roster was diagnosed with a count that was not the problem.** The nine-cell
  preflight ran *before* the pane exclusion, so ten participants of which two are `:scratch` — an
  eight-cell roster, one edit from working — refused with "shows 9 cells; got 10" and never
  mentioned the drawer. Two round trips to fix one roster, the second chasing a number that was
  never wrong. The cap counts gridable cells now, which is the order the relay already used.

- **Two names for one cell were folded in silence.** The dedupe itself is right — two ways of naming
  one cell is not a user error, spending two of the nine on it is — but a roster is the relay's own
  membership grammar, and there the identical situation is reported by name. `carol` was simply not
  on the screen with nothing saying which name went; it now prints
  `(carol names the same cell as alice -- shown once)`.

- **The strict path left a grid up without saying how to close it.** When the `unresolved:` refusal
  cannot close the grid it just caused, it said `AND THE GRID IS STILL UP: <reason>` and stopped
  there — the literal `agtermctl dashboard --close` was printed only by `--detach` and by a failed
  close in the foreground hold. That is the same moment, by the same argument (you most need the
  command and least want to go and look it up), and two docs had been claiming it was printed here
  for a release.

- **A participant could vanish from the relay's grid with NOTHING saying so — defect 3's exact
  symptom, reached through the code that fixed it.** `_one_name_per_row` throttles its "alice and bob
  both resolve to row AAAA1111" line so a roster nobody fixes does not print it every tick, and that
  note was the one throttle in the file that shipped with no clear. The cost is not a missing line:
  `update_grid` subtracts the alias drops from `missing` on the argument that an alias *has already
  been told*, which is true only the first time. Measured — bob aliases alice (said), the roster is
  fixed, bob aliases alice again: the second collision is throttled **and** the missing line is
  suppressed, so the grid quietly shows one agent where two were asked for.

  ⚠️ **It needed clearing in two places, not one, and the second is the one that is easy to miss.**
  `_one_name_per_row` clears the note for every name that resolved *and was not dropped* — but a
  participant who **leaves the roster** appears in no later `resolved` at all, so that loop can never
  reach it. Only `apply_leaves` can, and the note was missing from `_name_notes`, whose own comment
  spells out the rule ("a list, not a pattern, and it has to grow when a new per-name note appears").
  `docs/commands.md` had already promised that every throttle is cleared when its condition goes
  away.

- **A grid agterm opened only PARTIALLY was never retried.** `update_grid` records the cell set as
  shown so the relay does not re-open an unchanged grid every tick — and it recorded it after an open
  in which agterm had said `unresolved: <id>` on stdout and left those cells out. The next tick then
  took the `fresh == shown` early return, so a **transient** partial open (the documented cause is a
  `:right` cell whose split is briefly absent) stayed partial until the row ids or the membership
  moved, which for a stable roster is the rest of the run. It is retried now, with the message
  throttled — the same trade a failed open and a missing member both already made, three lines above
  the comment that argued it.

- **`Ctl.dashboard`'s docstring told the next caller the opposite of what the code does.** It said
  the relay was content to ignore agterm's `unresolved:` output; the relay grew a reader for it in
  this same release, and `unresolved_lines`' own docstring says there are two callers. Both `Ctl`
  grid methods now also say out loud that they **raise** — "best-effort" describes the return, not
  the call, and a caller reading only the first line is the bug two review passes found on both sides
  of this branch.

- **`agb-dashboard`'s own cleanup could destroy the message it exists to print.** Both places it
  closes a grid assumed `Ctl.dashboard_close` returns a two-tuple; it *raises* when agtermctl cannot
  be started at all. On the strict path that meant the error naming the still-up partial grid was
  **never constructed** — you got an errno and no word that a grid is on your screen, which is
  exactly the failure the command exists to prevent. In the hold's `finally` it replaced the exit
  with a traceback out of the cleanup. One `close_grid` helper now answers "no, and here is why",
  which is what the two-tuple already said.

- **The two handler entries written to catch filesystem errors were INERT.** `__main__` matched
  `type(error).__name__`, and no real filesystem error is ever spelled `OSError`: it arrives as
  `FileNotFoundError` or `PermissionError`. `IOError` could never match at all — it is an alias of
  `OSError` in Python 3 and is not a class name — so it is gone rather than kept as decoration. The
  match is over the whole MRO now, which also makes `RosterConflict` fall out of `PeerError` instead
  of needing an entry of its own. ⚠️ The structural test asserted that the implementation's own list
  was present and nothing about whether anything could ever match it — the tautology `CLAUDE.md`
  warns about, in the one place it was load-bearing.

- **A grid that would not open KILLED THE RELAY and lost the message it was carrying.** The one
  outcome the grid's whole error policy forbids. `Ctl.dashboard` returns a status for an agtermctl
  that *ran* and refused — but the relay's open was unguarded, and `_spawn` **raises** when agtermctl
  cannot be started at all: a removed binary, a `$PATH` an agterm pane did not inherit, the
  `/proc/<pid>/exe (deleted)` case after an upgrade, EMFILE. Measured:

  ```
  RELAY DIED: PeerError agtermctl: [Errno 2] No such file or directory
  typed: []      # the queued "[chat from alice] hello" was never delivered
  ```

  ⚠️ **The close three lines below it had been guarded since the day it was written**, with the
  reason in a comment and a test to match; the open was written later and did not carry the lesson.
  The contract test that should have caught it modelled a failure as a returned `(False, "", why)` —
  a fake that describes a world where the real failure cannot happen, which is why it passed for as
  long as the bug existed. It now has a sibling whose fake **raises**.

  The report is guarded too, and for a related reason: `say` writes to `out`, which
  `agb-peer relay | head` closes the moment `head` exits. A raise there unwound *before* the relay
  recorded that it owned a grid, so the `finally` did not know there was one to close — leaving the
  cells on the only grid the Mac has. And `close_grid` no longer lets a failed **report** change its
  answer about whether the grid is **gone**: it did, so a close that worked came back as "still
  owned" and the relay closed a second time on the way out, by then possibly over somebody else's
  grid.

- **The relay could not see the third cause of a partially populated grid.** agterm exits **0**,
  opens the grid without the cells it could not resolve, and names them as `unresolved: <id>` on
  **stdout alone** — so the status the relay was reading says the grid is fine. Two causes had a
  line each (a member with no row; a member in a pane the grid cannot express) and this one had
  none, while `docs/commands.md` promised that a partial grid is always marked. It is reachable with
  a shape agterm documents: a `:right` cell whose split has since closed is *unresolved, not an
  error*.

  ```
  dashboard: open -- but agterm dropped unresolved: BBBB2222
  ```

  Said, and the grid stays up — deliberately unlike `agb-dashboard`, which refuses. The relay's grid
  is an adjunct to a message pump and its failures stay cosmetic. `unresolved_lines` moved from
  `agb-dashboard` into `agb-peer`, beside `dashboard_cells`, because there are now two callers and
  the file that owns the grid vocabulary should own this too.

- **One transient failure meant no grid for the rest of the run.** The cell set was recorded as
  shown whether or not the open worked, so with membership unchanged the trigger never fired again —
  while the docs advertised a grid that "repairs itself within a tick". Measured: a single failure
  then four ticks of identical membership produced **one** attempt. The open is retried until it
  works; the *message* is throttled instead, the same trade the missing-member line makes.

- **A tenth participant cost everybody the grid.** agterm's dashboard takes **nine** cells and the
  relay handed it one per participant, so a roster that grew past nine was defect 4 all over again
  with a different cause: one participant too many, and nobody is shown. What agterm actually does
  with a tenth cell has never been measured either — which is the second reason to decide it here
  rather than find out at run time. Nine are gridded, in the same order the cells are already built
  in, and the rest are **named**:

  ```
  dashboard: p9 not shown -- agterm's grid takes 9 cells
  ```

  ⚠️ **Deliberately the opposite of `agb-dashboard`**, which refuses the whole grid over the cap. The
  same sentence decides both: the relay's grid is an adjunct to a message pump, so nine of ten beats
  none; `agb-dashboard`'s grid *is* the point, so a grid short of what you named is not worth opening.

- **A participant who left the scratch drawer and came back was dropped from the grid in silence.**
  The exclusion note was never cleared, so the throttle that stops it repeating every tick also
  stopped it ever firing again. The missing-member note beside it had had the clear — and the
  comment explaining why — since it was written; the two had the same argument and only one applied
  it.

- **`agb-peer relay --dashboard` left its grid on the screen after it exited.** agterm has exactly
  **one** dashboard grid, so a relay that opened one and then took its documented exit — Ctrl-C —
  left the departed conversation's cells sitting in the only grid the Mac has. The next thing that
  wanted a grid found somebody else's dead agents in it.

  The relay now closes the grid on the way out, and **only a grid it opened this run**: there is no
  ownership token on agterm's side, so an unconditional close would dismiss whatever you had up.
  The close is best-effort to the point of swallowing exceptions — it runs from a `finally` on the
  Ctrl-C path, where a raise would replace your Ctrl-C with a traceback out of the cleanup, and a
  grid that will not close is cosmetic while a relay that dies reporting it is not.

- **`agb-peer relay --dashboard` opened no grid at all when a participant was in the scratch
  drawer.** `agb pane`'s `[d]` puts an agent there, and the relay handed agterm a `<id>:scratch`
  cell — which agterm refuses at parse time, before it looks at any row, so the *whole* command
  failed and the conversation you asked to watch got no grid. All-or-nothing on a cell for the one
  participant you were least likely to be watching.

  A grid cell is now built in exactly one place, which drops a pane agterm's grid cannot show and
  opens the grid with the rest — **and says who it dropped**, by name and pane:

  ```
  dashboard: not shown -- carol (scratch); agterm's grid takes only left/right panes
  ```

  ⚠️ **The line matters as much as the fix.** A dropped cell turns an all-or-nothing failure you
  could see into a participant quietly missing from a grid that otherwise looks right — the same
  class of defect, one layer down. It is said **once**, not once per relay tick: the grid is
  re-opened on every row-id change (an `agb-refresh` re-mints them all), and an unthrottled line
  would repeat for ever and bury everything else the relay says. A change in *who* is excluded still
  gets through, because the throttle is on the message rather than on the clock.

- **`agb-peer relay --dashboard` went on showing a participant who had left, and said nothing about
  one it could not show.** Two defects in one block, both of them a grid that looks right and is
  not.

  The re-open was guarded by `len(resolved) > 1`, so it was skipped in exactly the case that needed
  it: when membership fell to a single participant — or to none — the *previous* grid stayed up,
  cells and all, including the person who had just left the roster. The comment two lines below it
  said the re-open existed because "a grid built on dead ids shows dead cells".

  And a member the relay could not resolve simply was not there. Three participants, two with rows,
  and you got a tidy two-cell grid with nothing anywhere saying carol was missing:

  ```
  dashboard: no row for carol -- the grid shows the other 2
  ```

  ⚠️ **A missing member does not close the grid, and that is a decision rather than an oversight.**
  `resolve_all` treats a label nothing answers to as a *steady state* — it has to, or a typo prints
  a line every tick for the life of the relay — so closing on one would mean a single mistyped
  roster entry costs you the grid for the whole run. The relay's grid is an adjunct to a message
  pump; its failures stay cosmetic and get a line of text.

  ⚠️ **The re-open now follows the `(name, id, pane)` cell set, not the set of names.** An
  `agb-refresh` re-mints every row, so the names are identical while every id has moved — the case
  the old comparison was there for. A name-keyed trigger would have called that "unchanged" and
  reintroduced the dead-cell bug while fixing the other two. And the membership check runs on every
  tick rather than only when the resolution moved, because adding an unresolvable member leaves the
  resolution byte-identical: a report written inside that condition could never have fired for the
  very case it was written for. The *message* is throttled instead; the cell set is what stops the
  repeated `agtermctl` call.

  ⚠️ A name dropped for sharing a row with another participant is **not** reported as missing. It
  resolved perfectly well and has already been told which row it collided with; a second line
  calling it "no row" would be a contradictory diagnosis of one situation.

- **A message delivered to a *working* Codex was typed and never submitted.** Intermittent by
  nature: the same delivery to an idle Codex submits itself, so it depended entirely on whether the
  peer happened to be mid-turn when the reply came back. The relay logged `delivered` either way.

  ⚠️ **The relay could not tell.** `peer_busy` reads the agterm row's status, which comes from the
  agent's own agbridge hooks — and **Codex fires none**: `agb-codex` mints its row `completed` and
  it stays there for the life of the agent. The cursor gate cannot help either, because a working
  Codex has an **empty** composer, caret at column 2, which reads as ready. So the one agent that
  needed the gate was the one agent invisible to it.

  `pane_busy` reads it off the pane instead. MEASURED on live panes, both agents:

  ```
  Codex    • Working (6s • esc to interrupt)
  Claude   ⏵⏵ bypass permissions on (shift+tab to cycle) · esc to interrupt · ← for agents
  ```

  One marker covers both. ⚠️ Claude's is **transient and never reaches the scrollback** — it was
  caught by polling a live pane every half second, which is worth knowing before anyone tries to
  confirm it from history and concludes it is not there.

  A busy peer is now **held**, not typed into: exit 3, retried next tick, nothing lost. `--force`
  still bypasses it, as it always has for the status gate. ⚠️ And an unreadable pane holds too — a
  read failure is no information, and no information may not mean ready.

  ⚠️ **`tab to queue message` is deliberately not the marker.** It is what Codex shows once
  something is already in the composer — so as a gate it arrives one step too late, and Enter is not
  what queues.

- **Nothing delivered to a Codex peer was ever submitted — the Return arrived as a newline.** The
  message was typed, verified, and the Return went out; Codex put a newline in its composer and sat
  there. Every message to a Codex row has needed a human to press Enter since Codex support was
  added, in every length and every direction.

  MEASURED 2026-08-26, on live panes:

  | what arrives at the pane | Claude | Codex |
  |---|---|---|
  | raw `0x0A` (LF) | newline **inserted** | newline **inserted** |
  | raw `0x0D` (CR) | *not run* | **submitted** |
  | agterm `session type "\n"` | **submits** (verified since 0.3.0) | newline **inserted** |

  ⚠️ **The third row was read by counting blank lines** — an empty Codex composer renders one above
  the model line, the loaded one rendered two — which is why this looked for so long like a Return
  that was never sent. It was sent, and it arrived. What agterm puts on the wire for `"\n"` is not
  observable from the farm side and does not need to be: `\r` is what a real Return key sends, and it
  is the only one of the two that both TUIs agree about. `SUBMIT_KEY` is `"\r"`.

  ⚠️ **`agb pane`'s menu still gets a literal `"\n"`, and unifying them would break it silently.**
  That prompt is a shell `read` on a tty in **canonical** mode, where `ICRNL` makes CR and LF
  equivalent; a TUI puts the tty in **raw** mode and decodes keys itself, so it does not. Same
  keystroke, two readers, and only the raw-mode one is picky. A structural test pins the asymmetry,
  and a mutation that merges them fails it by name.

  ✅ **VERIFIED LIVE 2026-08-26, both directions**: a message typed into Codex submitted itself,
  Codex replied unprompted, and Claude received the reply and answered. No human touched either
  composer.

  ⚠️ **`delivered` in the relay's log was never evidence that anything was submitted** — it means the
  Return went out, which had been true all along. Three separate real bugs were found and fixed
  between the symptom and the cause (a lost doorbell, a paste placeholder spelled differently, a pane
  read before it finished rendering), and each one looked like it might be the explanation. The thing
  that finally separated them was **counting blank lines in the composer**, which is the only
  observation that distinguished "the Return never arrived" from "the Return arrived as a newline".

- **A long message was given up on while the pane was still rendering it.** The delivery check typed
  the body, slept **one second**, read the pane **once**, and treated "not there yet" as "swallowed".

  MEASURED 2026-08-26: a ~2.8 KB message to a Codex row was still arriving a second later — the
  screen held two paste placeholders and the tail had not been typed. A half-rendered pane is
  indistinguishable from a swallowed message on a single read, and the wrong answer is expensive:
  the failure is exit 4, which the relay **drops rather than retries** (typing again would leave two
  copies in the composer), so a slow render cost the whole message. Successive attempts then piled
  up unsubmitted in the composer, one on top of the next.

  The pane is now read up to `VERIFY_READS` (4) times, a second apart, and the delivery succeeds the
  moment it verifies. ⚠️ **The cost is paid only on the failing path** — anything that renders
  promptly still costs exactly one second, which a test pins, because a relay must not block. Four
  seconds of one conversation stalling is worth one message not being thrown away; forty would not
  be.

- **A long message delivered to a Codex peer was typed but never submitted.** It sat in the composer
  as `› [Pasted Content 1461 chars]` waiting for a human to press Return, and the relay reported
  `typed, but '…' never appeared in the peer's pane, so it was NOT sent`.

  `deliver` verifies that what it typed actually rendered before pressing Return, and a long, fast
  injection is collapsed by the agent into a placeholder — so the placeholder is what it looks for.
  ⚠️ **The two agents spell it differently**: Codex says `[Pasted Content 1461 chars]`, Claude says
  `[Pasted text #1]`. Only Claude's spelling was known, so the check found neither the body nor a
  mark and refused. Exit 4 is **dropped rather than retried** on purpose — typing again would leave
  two copies in the composer — so the message was lost as far as the relay was concerned.

  🔴 **The reason it shipped that way is worth more than the fix.** `docs/agtermctl.md` recorded,
  *measured on a live pane*, that a ~900-character injection is rendered in full by Codex rather than
  collapsed — and concluded that a Claude-shaped mark was a harmless no-op there, which made Codex
  "the easier case". That was one sample at one length. At 1461 characters Codex collapses like
  Claude. The project's standing rule is *run it, do not read it*; this is the corollary it did not
  have: **a per-agent rendering read off one sample is a sample, not a constant.** Both the constant
  and the doc now say so.

  `PASTE_MARK` is `PASTE_MARKS`, a tuple, and each spelling is compared on **its own** count rather
  than on a total — a total could stay level while one placeholder appeared and another was cleared.

  ⚠️ **Matched case-insensitively, for the same reason one level down.** `cat -A` on the live pane
  read `[Pasted Content 1461 chars]`; the operator watching that same row read `[Pasted content …]`.
  Two readings of one screen, disagreeing on one letter. Case distinguishes nothing on a composer, so
  betting on it buys nothing and can only lose a message; the count still has to go **up**.

- **Two messages sent between one relay tick lost the earlier one, on the file transport.** The
  doorbell shows only the newest id and `drain_files` fetched exactly that one, so the first message
  was orphaned in the chat directory for ever, silently.

  ⚠️ **The tmux transport never had this**, and the difference is why it took a live run to find:
  `drain` sweeps *every* `@agbpeer_msg_*` option off the pane, so a missed doorbell there is
  harmless. `drain_files` cannot sweep — the chat directory is shared by every participant, and a
  message carries its recipient but **not** its sender, so a blind glob would credit one agent's
  message to whichever pane happened to ring.

  So it now fetches **every id still on that screen**, which is the only list of names anchored to
  the pane being read. Ids already delivered are skipped, so a long transcript does not cost an ssh
  per marker per ring.

  ⚠️ **And that list is deduplicated, because the same id is on the screen twice by design.**
  `skills/agb-peer/SKILL.md` tells a file-transport sender to *repeat* the printed doorbell in its
  own answer — an agent UI folds command output behind a `ran N commands` summary, so the repeat is
  the copy that actually reaches the pane. Fetching the second copy can only fail, the first having
  unlinked the file, and it fails saying `nothing to read … normal after a relay restart`, which is
  not what happened. Confirmed on the live capture that found the bug above: its screen carried the
  same marker twice.

  Measured live: a Codex rang twice inside one two-second interval — a `who` and a message — and the
  `who` was orphaned.

- **A file-transport doorbell that outlived its message made the relay re-fetch it every tick, for
  ever.** After a relay restart you saw one line —
  `pool: cannot read <chat-dir>/<id>.msg: No such file or directory` — and then silence, while the
  relay kept issuing an ssh at the relay interval for the life of the process.

  The file transport names its file by the doorbell id, and the relay **deletes** that file once it
  has read it — but it cannot clear the doorbell, because that transport exists precisely because the
  agent's tmux is unreachable. So the doorbell outlives the file, and every later relay primes on a
  stale id. Reported as a fetch *failure*, `seen` never advanced, so the retry re-fired for ever and
  `_throttled` hid it after the first complaint.

  A fetch now has three outcomes, not two: **the command ran and there is nothing to fetch** is
  distinct from **we could not ask**. The first advances `seen` and stops; the second still retries.

  ⚠️ **The discriminator is the exit status, not the error text.** `ssh` reserves **255** for its own
  failures; anything else non-zero means the remote command ran and failed. Measured: `cat` on a
  missing file exits 1, `ssh` to an unresolvable host exits 255. Matching *"No such file or
  directory"* would have been the error-string guess `socket_is_missing` exists to avoid.

  ⚠️ **The cost, stated rather than hidden:** a file that is momentarily unreadable — a permissions
  repair, an NFS hiccup — is now given up on rather than waited out, so one message could be lost.
  That is a real weakening of *"a short read is no information"*, taken because the measured
  alternative was an ssh every two seconds, silent, indefinitely.

- **`agb-peer who` crashed in a plain login shell, after it had already sent the request.** A warning
  glyph in its output raised `UnicodeEncodeError: 'ascii' codec can't encode characters` — so the
  message went out, the relay answered it, and the command reported a traceback.

  `sys.stdout` is `strict` in CPython and `-E` does not touch `LC_ALL`, so any non-ASCII byte written
  through it raises under an ASCII locale, which a tcsh login shell has. ⚠️ **The comments and
  docstrings in that file may hold anything — and do — but what leaves through `out.write` or `say`
  may not.** A structural guard now pins that, with a companion so it cannot be satisfied by
  stripping the file's warnings.

- **A message with no space in it was silently lost.** `agb-peer send --to bob hello` never arrived;
  `agb-peer send --to bob 'hello there'` did. The difference was a space, and there was no error at
  either end.

  `tmux show-options -p` quotes a value **only when it has to**, so `bob\nhello there` comes back
  quoted and `bob\nhello` comes back **bare** — but *both* render the newline as a literal
  backslash-`n`. The parser unescaped only the quoted form, so a bare value still held two characters
  where the separator belonged, `parse_option_value` found no newline, returned `None`, and the
  message was passed over in silence.

  ⚠️ **The docstring claimed both forms were handled.** It had said so since the transport was
  written; it was a claim, not a fact.

  ⚠️ **The whole test suite exercised one of the two shapes**, because the `framed()` fixture always
  emitted the quoted form. Found by live-testing `agb-peer who`, whose request is a single word by
  design and so hit it every time.



### Added

> ⚠️ **The roster work below was verified live** — real agents on a farm host, a relay on a Mac,
> messages crossing both ways — after the tests were green. Two cases were **not** run and are
> flagged in `README.md`: two roster names resolving to the same row, and a joiner whose row is
> detached at the moment it joins. Five defects turned up during that run and **every one was in the
> documentation**, not the code: nested session names, a missing `@<ssh alias>`, `AGB_CLAUDE_CUSTOM`
> meaning `agb-claude` starts no local agent, `agb-claude -d` silently no-opping on an existing
> session, and a bash heredoc hanging in tcsh.

- **Participants can now be added, removed and repointed while the relay runs.** A roster edit is
  applied on the next tick: joiners are primed, leavers are forgotten, and a name whose row changed
  is treated as neither.

  ⚠️ **A joiner is *primed*, never delivered** — its pane may hold an hour-old conversation, and
  priming discards it and says what went. Priming is **state that survives ticks**, not a one-shot:
  a joiner whose row has not appeared, whose pane is detached, or whose fetch failed stays pending
  and is retried, with no second roster edit needed. It is bounded, and giving up **delivers**
  rather than discards — see the reasoning at `PRIME_ATTEMPTS_MAX`.

  ⚠️ **The roster is read *before* the rows are resolved.** Resolving first, on the old roster,
  leaves a joiner unresolved on the tick it joined.

  ⚠️ **A rebind is not a leave.** Both forget the pane-specific state, but a rebound participant
  keeps its queued mail — it moved, it did not go — and it must drop its **resolved binding**, or
  `resolve_all`'s keep-previous silently routes to the row it just left.

- **Two participants resolving to the same row are refused, with a warning naming both.** `resolve`
  refuses an *ambiguous label*, but two different labels can each unambiguously match the same row,
  and nothing cross-checked the map. Two names on one pane drain it twice, and `try_deliver`'s
  self-guard compares **names** — so a message addressed to the alias is typed into its own
  sender's composer. A startup typo could do this before; a roster edit makes it ordinary.

- **An unresolvable participant is reported once, not every tick.** With a fixed participant list a
  mistyped label is a startup error the operator sees and interrupts. With a roster it is a steady
  state, and it would otherwise print for the life of the relay.

- **A running relay re-reads its roster when the file changes**, so participants can be added and
  removed without restarting it. ⚠️ **Every failed read HOLDS the roster already running** —
  unreadable, missing, not UTF-8, malformed, empty. At runtime none of those is evidence that
  anybody left, and a truncated read is exactly what a file being rewritten in place looks like for
  a millisecond. It parses *cleanly* as a shorter roster, so treating a short answer as
  authoritative would apply a leave nobody asked for. `mv`ing the file away does not dissolve the
  conversation.

  Each hold is said **once per unchanged reason**, not every tick.

  ⚠️ **The change gate compares bytes, not a stat key.** The file is a handful of lines, so reading
  it is cheaper than reasoning about whether `(mtime, size, inode)` can miss a rewrite — and
  `os.stat` on a network mount is served from the attribute cache, which is what invariant 6 is
  about. Comparing what was read satisfies that by construction rather than by a comment.

  ⚠️ **Two pieces of state, and they diverge on purpose**: the bytes last *seen* (the gate) and the
  roster last *applied* (the diff base). A read that parses badly advances the first and not the
  second, so the next valid edit is still compared against what is really running. Collapsing them
  loses a join.

- **`agb-peer relay --roster <file>`** — participants from a file instead of the command line, so
  the set can change without restarting the relay. Same grammar as the positional form, because the
  file's words are handed to the same parser: there is one grammar, read from two places.
  `#` comments and blank lines are ignored.

  ⚠️ **A `#` comment is a whole line, never a trailing one.** `<row>` is a row-title substring and
  may contain `#`, so stripping from the first one would silently truncate a legitimate spec.

  ⚠️ **`--roster` and positional participants together are refused.** With two sources of truth
  there is no answer to *who is in this conversation* that does not depend on which one you read.

  Startup **refuses** where a running relay will hold — unreadable, missing, empty, malformed, or
  fewer than two participants — because holding means keeping the roster you already had, and at
  startup there is none. It is also the only place the two-participant minimum can be enforced: a
  relay may *drop* to one when somebody leaves, but it cannot begin with nobody to talk to.

- **`agb-peer` participant names are now letters, digits, dot, underscore and hyphen** — refused at
  `agb-peer relay`, with the reason named. ⚠️ **The rule applies to the *name* only**, the text left
  of the `=`: `<row>` is a row-title *substring* and legitimately contains `/`, because the default
  `row_fields` renders `cwd` unshortened — `bob=/home/you/agbridge` is a spec people have and it
  still parses.

  Two reasons, and the second is why it is drawn now rather than when it is first needed: a name has
  to survive being spliced into a value that crosses `ssh <host> tmux set …`, where ssh flattens
  argv into a string handed to the remote login shell (often tcsh); and **tightening a refusal later
  is the breaking direction**, so the time to draw the line is before anyone owns a name it rejects.

  It is a positive list rather than a list of characters to escape, for the same reason
  `agb-claude`'s `{}` splice is: a denylist grows a hole every time somebody invents a
  metacharacter.

- **A failed fetch lost the messages it could not read, until the agent happened to send again.**
  `relay_tick` recorded `seen[name] = ident` one line *above* the fetch, so an ssh that failed left
  the participant marked caught-up having read nothing. The doorbell guard then short-circuited every
  later tick — the doorbell had not moved, and the relay believed it had already handled it — so
  those messages sat in tmux until the agent sent something new and moved the marker. A transient
  network blip was enough.

  `seen` now records what was actually **read**: it is written after the drain, and only when the
  drain reports that the fetch succeeded. The retry is then automatic and needs no extra state.

  ⚠️ **The cost is that a persistent failure now complains every tick rather than once**, which is
  the same shape this project has fixed twice before. Both the fetch complaint and the pre-existing
  `cannot read <name>` one are now said **once per unchanged reason** and again after a recovery, so
  a real change of state is still visible while a stuck one is not repeated.

- **`agb-peer relay` silently discarded every message addressed to a participant whose agent had
  not started yet.** The symptom: you name two agents, start only one, send it a message from the
  other — `agb-peer send` answers `queued for bob as #…` and exits 0, the relay says
  `dropping a message for 'bob': not a participant`, and nothing ever arrives. The sender is never
  told, because there are no delivery receipts on this channel by design.

  `cmd_relay` passed **`resolved`** — the names whose row is in agterm's tree *right now* — as
  `try_deliver`'s `people`, and `try_deliver` used it to answer two different questions at once:
  *is this a participant?* and *can I reach it?* The second is a good reason to wait; the first is
  the only one that justifies throwing a message away. Conflated, an agent that had not booted yet
  was indistinguishable from a typo.

  It has been wrong since the relay existed, and a fixed participant list mostly hid it: you start
  the relay once the agents are up. It matters now because the roster work makes the opposite
  ordinary — you list the agents you intend to talk to, and start them when you get to them.

  Membership is now a question about the **roster**, and the two answers differ: a name that is not
  in it is still **dropped** and named, while a name that is in it but has no row yet is **held**
  and delivered when the row appears. The hold complains on a ladder rather than every tick, and is
  **bounded** — ⚠️ 225 ticks, about half an hour at the default interval, and that number is a
  judgement rather than a measurement. Holding for ever is defensible (the messages are addressed to
  a real participant and nothing else consumes them) but a name that never resolves would then
  accumulate mail for the life of the relay.

  ⚠️ **The hold ages by ticks, not by messages.** `try_deliver` runs once per queued message, so
  three messages for one absent name would otherwise age the hold three times as fast and make the
  bound depend on how much mail is waiting.

- **An agent launched on another machine with `{env}` reaped every live row on the host it was
  impersonating, then duplicated its own.** The symptom, from the outside: you start one agent
  through `AGB_CLAUDE_CUSTOM` with `{env}`, and every *other* row in the sidebar — agents that are
  running fine, on a machine you never touched — goes `[done]` at once. Seconds later the agent you
  did start appears **twice**. Both halves are one `agb hook`.

  `{env}` sets `AGB_HOST` so a remotely launched agent reports the row it belongs to. `own_host()`
  honours it, `maybe_sweep` sweeps `sessions/<own_host()>/`, and `os.kill(pid, 0)` then answers
  about the machine the agent is *actually* on. Every pid belonging to the impersonated host comes
  back `ESRCH`, which `liveness()` reads as positive proof of death — so the sweep reaps sessions
  it has no standing to judge, and `sweep_idx` unlinks their anchors for the same reason. One of
  those anchors is the agent's own, so its next hook finds none and mints a second key: the
  duplicate row.

  Measured on this project's farm: eleven live sessions reaped and thirteen anchors dropped inside
  40 ms, followed two seconds later by the duplicate. That a reap was *false* rather than unlucky is
  provable from the breadcrumbs — a pid reaped as "gone" at 11:34:02 was recorded again by a mint at
  11:41:36, which only happens for a process `resolve_agent()` finds alive.

  `_require_own_host` is the guard that should have caught this, and could not: it compares against
  `own_host()`, so the override was on both sides of its comparison. The fix splits the question it
  was really asking. `own_host()` stays the **identity** — what name entries are written under,
  overridable, because a remote agent has to be able to assert one. `real_host()` is the new
  **observation** — `uname` only, never `AGB_HOST` — and answers whose pid namespace this process
  may interrogate. `host_is_observed()` is where they meet; `maybe_sweep` returns before any I/O
  when they disagree, and `_require_own_host` refuses with a message naming the machine it is
  actually speaking from.

  ⚠️ **`AGB_HOST_LOCAL=1` is the opt-in, and there is deliberately no opt-out.** A process cannot
  distinguish "I am standing in for another host" from "I have been renamed" by looking at itself,
  so an explicit statement is unavoidable — and the direction that has to be typed is the dangerous
  one. Say nothing and you get the safe answer. Set it only where an overridden `AGB_HOST` genuinely
  names the machine you are on; `{env}` does not set it, and must not.

  ⚠️ **If you have run `{env}` already, the reaped entries are gone** — they were unlinked, not
  hidden, so no `agb-refresh` brings them back. Those agents are still running and each mints a
  **new** key on its next tool call, so the rows return under new identities; `agb close-done`
  clears the `[done]` remains.

  This does not change the other cost of `{env}`, which is unchanged and still worth knowing:
  `AGB_AGENT_PID=none` makes such a row unreapable by proof of death, so it outlives its job until
  `agb prune`. That property is also what limited the blast radius here — the pid-less records were
  the only ones the bogus sweep skipped.

### Added

- **`AGB_CLAUDE_CUSTOM`, and two placeholders so a custom launcher can still take per-run flags.**
  `agb-claude` gets the seam `agb-codex` already had, and both grow `{}` and `{env}`:

  ```sh
  export AGB_CLAUDE_CUSTOM='submit -q big -I "{env} claude {}"'
  agb-claude work -- --model opus
  ```

  `{}` is where **this invocation's** agent flags go. Refusing them outright was right while there
  was no way to say *where* — the agent sits inside somebody else's argument, so appending puts the
  flag on the launcher — but it also meant changing an environment variable to change a model. The
  placeholder is the missing information, and it is the caller's to give.

  ⚠️ **It splices verbatim, so the allowed set is a positive list** — `[A-Za-z0-9._:/=+@,-]` — not a
  list of characters to escape. The wrapper cannot know whether `{}` sits inside quotes
  (`-I "claude {}"` says yes, `docker run img claude {}` says no), so it cannot quote for you; an
  argument that would change how the line parses is refused rather than silently mangled, and
  `--greet` stays refused because prose always needs quoting.

  **`{env}` is the interesting half, and it exists because Claude hooks and Codex does not.** A Codex
  on a pool node can never disturb its row — it writes none. A Claude there resolves a *different*
  anchor, `own_host()` naming that machine, and mints a **second row** on a host the Mac has no
  mapping for and whose pane it cannot reach.

  `AGB_HOST` alone does not fix it: `$TMUX`/`$TMUX_PANE` do survive job submission, so the anchor
  matches again, but `bind_key` adopts only when `idx_matches`, the pid differs, and it **replaces**
  the index — orphaning the row the wrapper just minted. `{env}` therefore expands to
  `AGB_HOST=<this host> AGB_AGENT_PID=none`, and a pid-less hook adopts, because *absence of evidence
  must never re-mint*.

  ⚠️ **The price, and it is why `{env}` is opt-in rather than automatic:** every hook rewrites the
  record's pid, so that entry becomes pid-less and can no longer be reaped by proof of death. When
  the job ends the row sits at its last state until `agb prune`. Liveness is proven, never inferred,
  so nothing tidies it up. Use `{env}` only when the launcher really is remote.

  ⚠️ **A seventh cross-file agreement** (CLAUDE.md invariant 14): `{env}` spells `own_host()`'s
  resolution in POSIX sh. A disagreement raises nothing — the agent reports a host with no mapping
  and you get a second row, which reads as the wrapper being broken. Both wrappers' tests compare the
  substituted value against `agb.own_host()` itself rather than a copy of its rules.

  Twenty new tests, each mutation-checked. One mutation had to be redone: making the refusal fire
  *unconditionally* killed two unrelated tests and said nothing about the guard, where removing it
  killed exactly the named one.

- **`AGB_CODEX_CUSTOM` — `agb-codex` can start the agent through a launcher, so the Codex you get a
  row for need not be the one on this host.** The case that forced it: the Codex worth talking to
  lives on a batch pool, reached through a scheduler submit command, on a machine picked at submit
  time. `agb-codex` hard-coded `exec codex`, so the only way to get that was a private fork of a
  script that ships publicly — and a fork drifts silently from the pre-mint it exists to preserve.

  ```sh
  export AGB_CODEX_CUSTOM='submit -q big -I "codex --yolo"'
  agb-codex -d pool
  ```

  Set, it replaces the `codex` command line entirely; unset — the only shape anyone had before —
  nothing changes. **The launcher itself stays out of the repo**, which is the point: it is
  site-specific, and a public file is the wrong place for it.

  Four decisions that will each look like an over-reaction until they bite:

  - **The value is a shell command line, `eval`ed, not a program name.** That is what lets the agent
    be an *argument* to the launcher (`-I "codex --yolo"`). Word-splitting it hands the launcher
    `"codex` and `--yolo"` instead, and no downstream error message explains that.
  - **`--` passthrough args and `--greet` are refused while it is set, not appended.** With an opaque
    launcher there is no position to append at that is not a guess — the agent is inside somebody
    else's argument, so a trailing word lands on the launcher. The refusal names the variable and
    what it dropped. *(Superseded below: the `{}` placeholder is how you say where they go. Without
    one they are still refused, and the refusal now names `{}`.)*
  - **It is embedded in the command `tmux` is handed, never inherited.** A session created against an
    already-running tmux server takes its environment from the **server's**, plus
    `update-environment`; a variable exported in your shell a moment ago is not there. The version
    that read it from the environment inside the session would have exec'd nothing on any machine
    with a server already up — i.e. everywhere except a first run.
  - **The first word is checked against `$PATH`.** A typo becomes an error here instead of a session
    that opens, execs nothing, and closes again — which, for a wrapper whose whole job is to make a
    row exist, is the failure that looks most like the tool being broken.

  Verified live: two sessions started this way, each minting its own row, and each landing on a
  **different pool node** — which is the point of submitting a job rather than running a program,
  and is also why the row's identity has to be the launcher rather than the agent.

  The pre-mint is untouched and still correct: `exec` keeps the pid and starttime of the pane's own
  process, which is now the launcher, and that is the process whose death should reap the row — even
  though the agent itself ends up on another machine entirely. Seven new tests, each
  mutation-checked; the quoting one runs the generated command for real rather than asserting on the
  string, because `eval` is what decides it and a string comparison cannot see that.

- **`notify_on_new_row` also takes a list of states, so the Dock stops bouncing for every `claude`
  on the cluster.** The symptom: a Mac raising a banner and bouncing the Dock every single time a
  bare `claude` started on a farm host — a session opened in a VNC desktop the user was already
  sitting in, interrupting a machine they were not looking at. Six such keys had accumulated on one
  host in a week.

  `notify_on_new_row = 0` could not express what was wanted, because it is one switch for two
  different arrivals. The key now also accepts states:

  ```
  notify_on_new_row = completed      # `agb-claude` announces; a bare `claude` does not
  ```

  Only a row whose **first-seen** state is in the list is announced. The row is created either way
  — this withholds a banner, never a row.

  **Why states can stand in for the launcher, which is the part that will look like a trick later.**
  `agb-claude` mints the row *before* Claude runs, with `completed` (a session at an empty prompt is
  waiting for you). A bare `claude` mints on its first hook, and the first hook can only ever be
  `UserPromptSubmit` or `PostToolUse` → `active`. So the first state *is* the launcher, and the Mac
  side needs to learn nothing new.

  **The honest alternative was rejected on cost, and it is worth saying which.** Marking the record
  at mint time — `agb-claude` exporting `AGB_ANNOUNCE=1`, `agb hook` writing an `origin` field —
  names the launcher instead of inferring it. It needs an env read and a record field in `agb`,
  which is re-parsed on every hook and had **one character** of headroom against `AGB_PARSE_BUDGET`.
  A cosmetic Mac-side preference is not what a third budget raise is for. `agb` is untouched by this
  change and `wc -c agb` is unmoved. The inference is a cross-file agreement with `agb-claude`'s
  `PREMINT`, and it is pinned where it can be seen:
  `test_the_premint_state_is_completed_not_active` now says out loud that a Mac-side key depends on
  that word.

  Details that were decided rather than fallen into:

  - **`idle` is refused**, though it is in the status vocabulary. The bridge emits it for `[?]` and
    `[done]`, both of which are about a row that already exists, so no *new* row can arrive in it —
    accepting the word would be a value that silently does nothing. The allowed list is
    `agb.AGENT_STATES` itself, so the exclusion cannot drift.
  - **An unknown state refuses the whole list and falls back to *on*, with the reason in the bridge
    log.** `row_fields`' rule for the refusal, but the fallback direction is the opposite of what
    looks natural and is the point: a typo restores exactly today's behaviour and says why, where
    falling back to *off* would silently remove notifications — not being told is the failure this
    key exists to fix. ⚠️ `agb doctor` validates key *names*, not values, and runs on the farm, so
    the bridge log is the only place that warning appears.
  - **The default is unchanged.** Absent, empty, `1`/`yes`/`on`/`true` all still mean every new row.

  ⚠️ **Not verified against a live agterm.** The gate is unit- and mutation-tested, but nobody has
  yet watched a bare `claude` start in silence and an `agb-claude` start raise its banner on the
  same Mac. This project's history is that agterm-facing features pass every test and still need a
  fix after live use, twice in the last four. The check is both launchers in one sitting, on a row
  that is **not** selected.

- **`row_fields` — you choose what a row shows.** Titles were `label · host · cwd · pane · beat`,
  fixed, and on a real four-row sidebar that ran **69–77 characters**. The measurements say why:

  - the **host is identical on every row** on a single-host setup — 25 of ~72 characters, **35% of
    the line carrying no information**;
  - **`cwd` largely repeats the label** — one row read `data_pipeline_v2 · … · /home/me/data_pipeline_v2`,
    the same word twice;
  - ⚠️ **`pane` is last and it is the disambiguator.** Two agents in two panes of one tmux session
    share label, host, cwd *and* tmux, so `%15` is the only thing separating their rows — and being
    last, it is the first thing agterm clips. Arguably a design bug rather than a preference.

  ```ini
  row_fields = label,cwd:base,pane      # agbridge_dev · agbridge-public · %15   (36 chars, was 73)
  ```

  Any of `label`, `host`, `cwd`, `pane`, `beat`, `key`, in the order you write them, with
  `cwd:base` for the directory's basename. `key` is new — the 8 characters `agb rename` takes.
  **Default `label,host,cwd,pane,beat`, byte-identical to before**, so this is invisible until you
  set it.

  **An unknown field refuses the whole list** and logs why, rather than dropping just that field:
  a missing field is exactly what goes unnoticed, while "I edited it, restarted, and nothing
  changed" is unmissable. ⚠️ The bridge log is the only place the reason appears — `agb doctor`
  checks key *names*, not values, and it runs on the cluster while this is a Mac-side key.

  **What it cannot do**, deliberately: switch off the `[?]` and `[done]` prefixes. `idle` renders
  as *no glyph*, so without the marker a dead row is pixel-identical to a live idle one — a
  cosmetic setting must not be able to disable a safety property. And a field list that renders
  nothing (`row_fields = beat` on a healthy agent, or `pane` on an agent not in tmux) falls back to
  the label rather than producing an empty title, which `_title` would turn into no rename at all,
  leaving agterm's own name on the row.

  ⚠️ **Dropping `beat` costs more than it looks.** `docs/design.md` calls the age in the title the
  compensation for the first invariant — agbridge refuses to convert an age into a *status*, and
  the number is what it offers instead. A sidebar without it keeps the refusal and loses the
  answer. Allowed, now said out loud.

  **Rejected, recorded so they are not re-proposed.** *Automatic elision* — drop any field
  identical across all rows, so `host` vanishes with one host — needs no config and does the right
  thing unprompted, but makes a title a function of **global** state: starting an agent on a second
  host would silently rewrite every existing row's title, and it defeats the suppress-if-unchanged
  memory that compares against the last string sent for *that* key. A *format template*
  (`{label} · {cwd|base}`) needs placeholder parsing and has no way to report a broken template
  except a blank sidebar.

  Like every bridge-side key it is read once at startup, so `agb-refresh` after editing.

- **`agb install-config --print-statedir`** — puts that config's **own** `statedir` alone on stdout
  and writes nothing at all. It is what the adoption above reads, for the same reason the mac-id
  adoption uses `--print-mac-id`: a second reader of the `key = value` format in shell is a second
  reader that drifts from the first.

  Three properties are load-bearing and each was measured against a version that lacked it:

  - it prints the file's **own** value, never `agb.statedir()`'s fallback — which ends at the
    *default-path* config, i.e. the exact answer the installer must not get;
  - **"this file carries none" has its own exit status — `4` — and is not the same answer as "I
    could not read it", which is `1`.** The value is handled the instant the config has been parsed
    and returns there, so no later failure can arrive wearing that status. One non-zero for both was
    measured to matter: a config the installer could not read (mode 000, not UTF-8) was reported as
    *carries none to adopt* and the operator sent after `--statedir`, a flag that was not the
    problem and that would have installed the instance against a config nothing there can read.
    `install.sh` swallows `4` and treats anything else as fatal, naming the file. Same shape, and
    the same reason, as `agb-refresh`'s four-status plist reader;
  - it **writes nothing**, with or without `--dry-run`. Bolted onto the tail beside `--print-mac-id`
    instead, a statedir-less config was measured to be *rewritten with the default config's
    statedir* on the way to the error — the failure the flag exists to prevent, caused by the flag.

  It refuses company: only `--config` and `--dry-run` may accompany it. Everything else asks for a
  write it will not perform, and "you asked me to write and I silently did not" is the class of
  failure this project keeps removing. `--print-mac-id` alongside it is refused too — both own
  stdout, and neither answer says which one it is.

- **One skill file serves both agents.** MEASURED: Codex reads
  `~/.codex/skills/<name>/SKILL.md` with `name`/`description` frontmatter — byte-identical in shape
  to Claude Code's — and a symlinked `agb-peer` appeared in a live Codex's own list of skills
  alongside its bundled ones. So the agent-facing half needed no port at all, only a second symlink,
  which is the strongest evidence yet that `send` printing to tmux was the right shape: it names no
  model and neither does the skill, now that its description says "Claude Code or Codex" rather than
  assuming.

- ✅ **VERIFIED LIVE: a two-way conversation with an agent on an unreachable machine.** A Codex on a
  compute-pool node — picked at random, mounting the same NFS, with the Mac unable to ssh it at all —
  received a message and **replied**. Outbound took the path this release adds: a file on NFS, a
  doorbell echoed to its own screen, the relay reading that screen for free, and one `ssh` to the
  **container** to `cat` the file. Nothing touched the pool machine's network or its tmux.

  That is Codex↔Claude, across machines, with one end unreachable — and it is worth noting how much
  of it was already there: delivery needed no change at all, the doorbell needed no new parsing, and
  the only new code was "notice the socket is missing" plus "read a file instead of a tmux option".

- **A peer on a machine you cannot ssh to.** A job on a compute pool mounts the same NFS and the Mac
  cannot reach it. Two of the three legs turned out not to need that ssh at all.

  ✅ **Delivery is unchanged**, and the trick is the user's: start `agb-tmux` on a host you *can*
  reach, submit the interactive pool job from inside that shell, start the agent there.
  `session type` types into the pane and the pane is connected all the way down. **Nothing ever
  connects into the pool** — the same shape as agbridge's founding constraint. Verified live against
  a Codex on a pool node.

  ❌ **Sending could not use tmux**, and no permission fixes it: `$TMUX`/`$TMUX_PANE` *are* inherited
  through the job submission, `/tmp` is local to each machine, and ⚠️ **putting the socket on NFS
  does not help — MEASURED.** A `tmux -S <nfs path>` server accepts connections and holds pane
  options locally; from the pool, with that server still alive and the socket file plainly visible on
  the shared mount, tmux answers `no server running`. A unix socket is a local kernel rendezvous, not
  a filesystem object NFS carries. That is a structural no rather than another permissions fight,
  which is the more useful kind of answer.

  So sending falls back to **a file on NFS plus an echoed doorbell** — the two things that do cross.
  The doorbell needed **no new parsing at all**: `read_doorbell` already scans the whole pane, so an
  echoed `[peer #id]` line matches the same regex as a tmux status bar. Content is
  `<statedir>/chat/<id>.msg`, temp+renamed because a torn read on NFS is real, fetched with one
  `ssh <reachable host> cat` — to the container, never the pool, and only when the doorbell changes.
  Watching stays free, so this is still not polling.

  ⚠️ **The fallback needs a POSITIVE signal, not merely a failure**, and that distinction is the
  whole safety of it. Three failures reach the same error and only one means "use files", all three
  measured this week: a sandbox answers `Operation not permitted` with the socket right there; a
  wedged agterm client answers with a timeout; a pool machine answers `No such file or directory`.
  Falling back on any of them would have moved messages to a transport nobody was reading during a
  transient stall. The socket path is checked directly rather than an error string matched, and
  `$TMUX` unset at all is deliberately *not* the pool case.

  ⚠️ **It only works for an agent that renders command output on its screen** — Codex does, Claude
  Code does not, which is exactly what killed the original screen-as-content design. A Claude on such
  a pool needs something else again.

  ⚠️ And one of the six mutation checks caught a **vacuous guard of mine**: the temp+rename test
  matched `"rename"` in an AST dump, which includes the **docstring** saying "temp+rename". A
  structural test passing against its own explanation — the third of that kind today, and precisely
  what `CLAUDE.md` warns about. It asserts an actual call node now.

- **`agb-tmux` — a row for any command, not just an agent.** The general case of the family:
  `agb-tmux -d shellrow` gives you a farm shell you can click on from the sidebar, and
  `agb-tmux -d build -- make -j8` gives a long build a row of its own. `agb-claude` and `agb-codex`
  remain the two special cases that know their agent's name and its caveats.

  ⚠️ **Its status never changes**, because a shell fires no hooks — the glyph never moves, so a long
  build looks exactly like an idle prompt. ⚠️ **And it is deliberately not an `agb-peer`
  participant**: `classify` reads a shell as `unknown` so the relay will never type into it, which is
  the right answer, because a shell would *execute* what it was sent. Both are said at the top of the
  script and pinned by a test.

  The pre-mint matters most of the three here: Claude would eventually mint a row on its first hook,
  Codex never would, and a shell never would either — with nothing that could later change its mind.

  Three near-copies is a lot, and the test file says so: if a fourth appears they should be
  collapsed. What stops that today is that the two agent wrappers are live and verified, and
  rewriting a working thing for tidiness is the trade this project keeps declining.

- ⚠️ **A sandboxed Codex is RECEIVE-ONLY, and that is measured rather than reasoned.** Codex runs
  model-generated shell commands in a sandbox which refuses the tmux socket
  (`error connecting to /tmp/tmux-100000/default (Operation not permitted)`). Not a file-permission
  problem: under `workspace-write` the sandbox writes to `/tmp` and `ls` sees the socket, and the
  connect still fails. Since the doorbell **is** a tmux window name and the message store **is** a
  tmux option, such a peer can be sent to and can never send.

  ⚠️ **And the escape hatch may not exist.** MEASURED: Codex refuses to start with the bypass —
  `` `approval_policy = "never"` cannot be used because requirements do not allow
  `sandbox_mode = "danger-full-access"` `` — an **org policy** forbidding the mode, not a bad flag.
  Where such a requirement is in force there is no local workaround at all: every permitted mode
  blocks the socket and the one that would not is disallowed. A Codex peer is then **receive-only,
  permanently**.

  The escape hatch, where it does exist, is running Codex with its sandbox bypassed. `agb-codex`
  deliberately will not do that for you — it removes the sandbox for *every* command that agent runs, not just ours, which is
  the human's decision, the same reasoning that stops `agb-claude` answering Claude's trust prompt.

  Found the only way it could be: Codex received a message, found the skill in `~/.codex/skills/`,
  ran the exact command, hit the refusal, **did not retry**, and reported it — the skill's rules,
  written for Claude and tested only on Claude, holding on a different model unmodified.

- **`agb-codex` — the launcher, so a Codex agent gets a row at all.** `agb-claude` for Codex: name
  the tmux session, mint the row, then `exec`. Verified live — `agb-codex -d probe-codex` produced
  `probe-codex  completed  %86` in `agb list` with Codex drawn in the pane.

  ⚠️ **The pre-mint matters more here than for Claude.** Claude would eventually mint a row on its
  first hook, so the wrapper only makes it earlier; **Codex never would**, because it fires no
  agbridge hooks at all. Without this there is no row, not on the first prompt and not ever.

  ⚠️ **And that row's status never changes** — it stays `completed` for its whole life. The glyph
  never moves, and `agb-peer`'s status gate protects a Codex peer not at all. Said loudly at the top
  of the script and pinned by a test, because it is the single most surprising thing about a Codex
  row and the kind of fact that is otherwise discovered at the worst moment.

  It is a near-copy of `agb-claude` and that is deliberate: the two are expected to diverge (no trust
  prompt, `resume`/`queue`, no hooks), which is the same reasoning recorded for
  `open_split`/`open_drawer`. What must not diverge is the pre-mint, and five mutation-checked tests
  pin it — dropping the `exec`, dropping `AGB_AGENT_PID`, minting `active`, making the hook fatal
  with `&&`, and dropping the no-hooks caveat.

- ⚠️ **Reading a tail hid Codex completely, and the fix is to stop reading tails.** MEASURED on the
  first live Codex row: **Claude anchors its composer to the BOTTOM of the pane; Codex draws from the
  TOP and leaves the bottom blank.** `--lines 40` therefore finds Claude every time and missed
  Codex's composer entirely — `classify` said `unknown` for a healthy row, and the relay would never
  have delivered to it. The doorbell kept working the whole time, because tmux's status bar is
  always the last line: two reads, two geometries, and only one of them had ever been tested against
  Codex.

  Every pane read is now the whole **visible screen** — no `--lines`, no `--all`. Which the earlier
  measurements had already made the right answer and nobody joined up: the alternate screen has no
  scrollback, so those flags all returned the same lines, and the only thing the tail ever did was
  hide the top of a pane. It also makes the `--all`/`--lines` conflict that shipped in the first
  place unrecreatable, since neither flag is sent at all.

- **Codex works as a peer, and it cost one character.** MEASURED against codex-cli 0.149.1 on a
  cluster host: its composer glyph is `›` where Claude's is `❯`, and that is the **only** difference.
  The empty-composer caret is column 2 for both, Enter submits for both, and a ~900-character fast
  injection is rendered in full by Codex rather than collapsed into `[Pasted text #1]` — so Codex is
  the *easier* case for the delivery verification, not the harder one. `classify` takes a list of
  glyphs now; `PASTE_MARK` stays Claude-shaped and simply never fires.

  agterm's cookbook says "Tab for Codex". Measured: that is about queueing while **busy**, not
  submitting — Enter submits an idle composer, and interrupts a working one exactly as with Claude.

  ⚠️ **`codex queue --thread <uuid> --message …` was measured working and deliberately not used.** It
  delivers to a live TUI session with no composer, no caret gate, no paste-or-wrap verification and
  no submit key, and it **queues** rather than interrupts — genuinely safer. It was rejected on cost:
  delivery already rides agterm's own ssh, so that route *adds* an ssh we do not need, plus a
  uuid-discovery problem (the thread uuid only exists after the session's first completed turn) and
  a second code path. Recorded in `docs/agtermctl.md`, along with the trap that `queue` succeeds
  against a **dead** thread just as loudly as a live one, so its success is not a delivery receipt.

- ⚠️ **`agb install-hooks --agterm` was built, tested, pushed and then WITHDRAWN.** Recorded because
  the reasoning is the useful part.

  The premise was: a Mac-side agent has no status, so `agb-peer`'s delivery gate — the one that
  refuses to type into an agent mid-turn or on a permission prompt — cannot protect it. That premise
  came from seeing `STATUS -` in `agb-peer --list` and **inventing a cause instead of looking**.

  What is actually there: **agterm ships `agent-status/agterm-agent-status.sh` in its app bundle**,
  and agterm's own hook installer wires it to the same four events. It targets `$AGTERM_SESSION_ID`,
  forwards `--pane` and `--pane-id` for split resolution, honours `$AGTERM_SOCKET`, no-ops outside
  agterm and always exits 0. Every agterm user already has it, and it is **more complete than what
  was written here** — the version in `11b3854` handled none of the pane, socket or outside-agterm
  cases. Shipping it would have been a worse duplicate of a first-party feature, and on a machine
  that already had the real one it would have left eight hooks doing one job.

  It was the peer agent on the Mac that caught this, twice: first by noticing the pre-existing hooks
  in the installer's own dry-run output, then by reading the script when asked to confirm a
  hypothesis that turned out to be wrong. Neither was reachable from here.

  ⚠️ **And the `STATUS -` that started all of this still has no confirmed explanation.** Three
  hypotheses were offered and two were measured false. It is not the hooks being absent (they are
  there and correct). It is **not `--auto-reset`** either: measured on the Mac, a row reads `active`
  while working and `completed` after sixty seconds idle, never blank. What is left is unproven — the
  dash appeared on several rows at once, including a **farm** row with ordinary agbridge hooks, and
  always shortly after rows had been re-minted, which fits `CLAUDE.md`'s note that agterm resets a
  row's status when its command starts and `_reassert` only repaints every 30 s. Fits, but was not
  tested, and this entry has already been wrong twice about it.

  The lesson is the one the whole day kept teaching and this feature is the clearest case of:
  **a symptom was observed, a cause was invented, and a fix was built, tested and pushed before
  anyone looked at what was already installed.** Two of the three explanations offered along the way
  were disproved by one agent running two commands.

- **`agb-peer` — one agent can type into another's composer, from the Mac.** agterm ships a
  [`two-agent-chat`](https://github.com/umputun/agterm/tree/master/cookbook/two-agent-chat) cookbook
  where two *local* agents talk by injecting keystrokes into each other's pane. This is that, pointed
  at agbridge rows — where the agent is in tmux on a cluster host and the row's pane is running
  `agb pane` over `ssh -t`. It works: keystrokes survive agterm → ssh → tmux → the composer, and
  `surface cursor` reports the empty-composer column through the same hops. Both confirmed live
  before the script was written, not after.

  ⚠️ **Not installed.** `install.sh` does not touch it and nothing in `agb`/`agb_mac`/`agb_ops`
  imports it — `agb`'s byte cap is untouched. Copy it onto `$PATH` yourself.
  [`docs/commands.md`](docs/commands.md) has the flags.

  **Three gates, because there are three different ways this goes wrong**, and the middle one is the
  part agterm's own recipe cannot have:

  - *Mode.* A row's pane is either `agb pane`'s menu or the agent's composer, and the same bytes
    mean different things in each — a message sent to the menu is swallowed whole, and one that
    strips to `q` **destroys the row**. `session text` is the only detector; `foreground` looks like
    one and is not, because it reports the argv the session was *launched* with, so an agbridge row
    says `agb pane` whether or not anyone is attached. A menu row is armed with a **bare newline**,
    the one input `agb pane` cannot act on.
  - *Status.* `tree` carries the status the bridge last set, which came from the peer's own hooks —
    a fact about the agent rather than a guess about a screen. `active` and `blocked` refuse.
    `idle` is allowed through on purpose: it is the bridge's word for *no current information*, so
    refusing it would strand every row whose feed blinked.
  - *Composer.* `surface cursor` must report the empty column. **This is the one that earns its
    keep** — the very first row this was ever pointed at had an unsubmitted draft in it
    (`test the install script on another workarea`), which a naive `session type` would have
    appended to and submitted together on the next Return.

  Then it types, re-reads the pane to confirm the text rendered, and only then presses Return as a
  **second** call — because a permission dialog can appear between the cursor check and the
  keystrokes, and pressing Return would answer the dialog rather than the agent.

  ⚠️ **Live testing immediately found one bug and one confirmation.** The bug: `--list` read a
  hardcoded **6 lines** per row where the flow reads 40, so a `blocked` agent — whose permission
  dialog pushes the composer glyph off a short window — was listed as `unknown` while `--dry-run`
  called the same row `composer`. A diagnostic that disagrees with the thing it diagnoses, in the
  direction that reads as safe. Fixed by threading `--lines` through, and pinned by a test.

  The confirmation is the better half: **that same row proves the status gate is not redundant.** A
  `blocked` agent reads as `composer` at any window size, because the dialog and the composer glyph
  are both on screen. There *is* somewhere to type and it is the dialog — the cookbook's "Dialog
  Window Vulnerability" — and neither the mode check nor the cursor check can see it. The gate that
  looked like belt-and-braces is the only one that catches that case.

  **`agb-peer send` / `agb-peer relay` make it a conversation, and one mechanism covers all three
  pairings** — farm↔farm, farm↔Mac, Mac↔Mac. **The transport is the screen**: every agent already
  has a pane on the Mac, which is what agbridge is for, so the Mac is the one place agents in
  different worlds coexist. An agent sends by *printing a line* — `agb-peer send` calls no
  `agtermctl` and runs anywhere — and a Mac-side relay reads panes and types the payload into the
  recipient's composer. A file-based outbox was designed first and rejected: it only works when both
  parties share a disk, so it fails Mac↔Mac entirely and fails farm↔farm across two instances.

  Watch one with `agtermctl dashboard <a>:left <b>:left` — one agent per row, so each keeps its own
  status, glyph and banner.

  ⚠️ **The first design put the message on the agent's own screen and had the relay read it back.
  Live testing killed it, three times over, and every reason was measured rather than reasoned:**

  - `alternate_on=1`, `history_size=0` — Claude Code runs on the **alternate screen**, so the
    terminal has no scrollback at all. `capture-pane -S -2000` and agterm's `--all` both return
    exactly the visible screen.
  - Claude keeps its own transcript and repaints it when you scroll — but it **collapses multi-line
    blocks onto one line and truncates them with an ellipsis**, so a scrolled-away message is
    destroyed, not merely hard to find. (Driving that scroll with `PageUp` does work, and is visible
    to whoever is watching the pane — which rules it out on its own terms.)
  - **Claude Code does not render tool output onto the pane.** It draws the command. A message
    printed to stdout is never on screen for anything to read, fresh or otherwise. That one is fatal
    on its own.

  **So the screen carries a doorbell, not content.** tmux draws the status bar rather than the app,
  and `session text` includes it:

  ```
  window name:  claude [peer #k3n9x2]     <- always visible, never scrolls
  tmux option:  @agbpeer_msg_k3n9x2       <- the message: 3 KB, exact round trip, measured
  ```

  The relay reads the bar on a tick it already makes for mode detection, so **watching is free**, and
  only a *changed* id costs an ssh — which is what makes this acceptable after "I do not want to
  access every second". The screen says *when*; ssh says *what*. A fetch drains **everything**
  pending and unsets each option as it reads it, so a missed doorbell cannot lose a message and
  nothing is delivered twice. Priming drains and **discards**, or options left over from an earlier
  session would be swept up by the first real message and delivered as if new.

  **Surviving `agb-refresh` is a design requirement, not an afterthought.** A refresh re-mints every
  row and every id changes — watched happen twice in one afternoon. The doorbell and the message live
  in tmux on the agent's host and are untouched; the relay's resolved ids and any open `dashboard`
  are dead. So participants are named by **label**, re-resolved every tick, and the dashboard is
  re-opened when ids move. A relay that cached ids would go permanently deaf with silence as its only
  symptom.

  Three things about a refresh that are easy to miss, each pinned by a test. ✅ **A message sent
  during the blackout is delayed, not lost** — the doorbell and the message sit in tmux on the
  agent's host, so when the row returns the relay reads an id it has not seen and fetches it. The
  screen-as-content design could never have done that. ⚠️ **Every re-minted row comes back
  detached**, and a menu is not a tmux screen, so the relay **arms it** with the bare-newline attach
  rather than complaining — otherwise every refresh would be a manual re-attach of every participant.
  ⚠️ **A label matching no row is reported after three ticks**, not skipped in silence: one missed
  tick is a refresh in progress, a hundred is a bridge that never came back.

  ⚠️ **`automatic-rename` is `on` in tmux by default, and the doorbell depends on it being off.**
  Measured. It survives today only because an explicit `rename-window` disables it as a side effect —
  an undocumented dependency, and if anything re-enables it tmux overwrites the window name and the
  relay goes deaf with no error anywhere. `send` pins it off explicitly now.

  ✅ **Both directions, both lengths, unattended.** short→Mac, short→farm, long→Mac, long→farm — all
  four delivered and submitted with no keypress.

  ⚠️ **The asymmetry that made this hard to see is worth carrying: the transport decides whether a
  message is TYPED or PASTED.** Delivery to a farm agent goes through `ssh` into tmux and arrives
  slowly enough that Claude Code types it, so the body is on screen and verification passes.
  Delivery to a Mac agent is tmux-inside-agterm on the same machine, fast enough to trigger paste
  collapse — and then the body is not on screen at all. Same `deliver` code, opposite outcomes,
  which is why long messages appeared to work inbound and fail outbound for an hour. A local pane is
  the *harsher* environment here, which is the reverse of what anyone would guess.

  Also observed: the collapse is a **hybrid** — `❯ [chat from me] Long outbound test. This message
  is deliberately [Pasted text #1]` — the head typed literally and only the tail collapsed. So a
  verification matching on the message's *tail* is the one that fails; matching on the head would
  have masked the bug rather than fixed it.

  ✅ **The paste fix is confirmed live**: with the relay restarted, a ~1,150-character message from
  the Mac agent arrived and **submitted itself** with no keypress. Before it, every message over a
  few hundred characters needed a human to press Enter, in both directions.

  ✅ **And the peer channel did real work**: the Mac agent was asked to run four read-only
  `agtermctl --help` captures and report back, which closed an **ASSUMED** clause `docs/agtermctl.md`
  had carried since it was written — `session status bogus` really is refused (`error: invalid
  status`, exit 1), so the Task 4b stub is not stricter than reality. It also turned up `--sound`,
  `--color` and `--shape` on `session status`, and a **repeatable** `session close --target`, which
  no survey had. Recorded there as CONFIRMED behaviour with PARAPHRASED text, since a relayed
  summary is a weaker provenance than a paste and is labelled rather than blended in.

  ✅ **FARM ↔ MAC VERIFIED, 2026-08-24 — the pairing that motivated the whole design.** A Claude
  running in tmux inside an agterm session on the Mac exchanged messages both ways with a Claude on
  a Linux cluster host. The `@local` path drained the Mac agent's tmux **without ssh**, and the farm
  side went over one; neither agent knew or cared where the other was, which is the property the
  screen-as-address-space choice was made for.

  Getting there cost four more fixes, all found in use and none reachable from the suite: agterm's
  `bash --noprofile --norc` PATH (which ate `tmux`, then `claude`, then `tmux` again from inside a
  tool call), unbounded `communicate()` calls that hung for two minutes against a wedged agterm
  dashboard client, and a failed tmux read being turned into a guess and then **stored**. The last
  is the one worth remembering: the timeout fix had converted a visible hang into a silently wrong
  result.

  Setting up a Mac participant is `brew install tmux` plus
  `agtermctl session new --name <n> --command "<abs tmux> new -A -s <n> <abs claude>"` — absolute
  paths throughout, because that shell reads no profile.

  ✅ **ROUND TRIP VERIFIED, 2026-08-24 — two agents, a conversation, both directions.** A dedicated
  peer was started with `agb-claude -d peer-bot`, and its row came up **detached**, so the relay
  armed it with the bare-newline attach — the refresh-survival path, exercised live for the first
  time. A message went out, was delivered and submitted; the peer then **discovered the `agb-peer`
  skill on its own** (symlinked into `~/.claude/skills/` mid-session — skills are found
  dynamically, not only at startup), rang its own doorbell, and its reply came back through the same
  relay into the sender's composer.

  Three things ran for the first time in that exchange and all held: `agb-peer send` from an agent
  other than the author of this code, delivery **into** a busy conversation's composer, and arming a
  row that had never been attached.

  ✅ **VERIFIED LIVE, 2026-08-24 — the whole path, farm agent to farm agent through the Mac.** A
  message left one agent as a tmux option plus a doorbell on its window name; agterm rendered tmux's
  status bar; the relay read the doorbell on a tick it was making anyway, fetched over one
  argv-only ssh, unset the option, and typed the text into the other agent's composer, which
  submitted it and started working. `--dashboard` showed both panes side by side throughout.

  This project's rule is that agterm-facing features pass every test and still need a fix after live
  use. This one needed **four**, and none was reachable from the suite: a `fetch` seam with no
  production default, `--all` and `--lines` being mutually exclusive, a POSIX script handed to a
  tcsh login shell, and priming discarding a message queued before the relay started. 2078 tests
  were green for all four.

  ⚠️ **Priming names what it discards, because the first successful live run threw a message away.**
  The relay was started *after* the message was queued, so the priming pass drained and discarded it —
  correct behaviour, but `discarded 1 message(s)` reads as housekeeping rather than "this is the
  thing you were waiting for". It now names each one and says to send *after* the relay is up.

  ⚠️ **The second live run died on tcsh.** The fetch ran a POSIX `for … in $(…)` loop over ssh and
  answered `Illegal variable name.` — `ssh <host> <cmd>` hands the command to the remote **login**
  shell, and a farm login shell is often tcsh. Rather than quote a script through it, there is no
  script: `ssh <host> tmux show-options -p -t %N` is pure argv, and `show-options -p` renders each
  option on one line with `\n`/`\"`/`\\` escapes, so the Mac parses it unaided. Unsetting is a
  second argv-only call per message; a **delivered-set** makes a failed unset harmless, since the
  option comes back on the next ring and delivering it twice would repeat a sentence in a composer.

  ⚠️ **The first live relay died at the one seam no test filled in**: `drain`'s `fetch` parameter
  defaulted to `None`, and every test injected a fake, so the production path was the only one never
  exercised — `'NoneType' object is not callable` on the first doorbell. Exactly the shape of the
  `--all`/`--lines` bug earlier the same day: a default that only exists for tests. It falls back to
  the real runner now, pinned by a test that passes no fetcher at all.

  `agtermctl dashboard <a>:left <b>:left` is the read-only side-by-side view; `--dashboard` keeps it
  in step. One agent per row, so each keeps its own status, glyph and banner.

  `@<ssh target>` on a participant says where its tmux lives, `@local` meaning this machine — so a
  Mac-side participant uses the identical mechanism minus the ssh. Without it the target comes from
  the row's own `agb pane --host`, read out of agterm's `foreground` field. That field is useless for
  telling an attached row from a detached one, and exactly right for this.

  **`skills/agb-peer/SKILL.md` is the agent's half** — an agent will not use any of this unless it
  is told to. `ln -s "$PWD/skills/agb-peer" ~/.claude/skills/agb-peer` on every participant's
  machine — a **symlink**, which is how every other skill here is installed, because a copy goes
  stale the next time the repo moves and nothing says so.

  ⚠️ **Which is why the skill contains nothing to edit.** The first draft had three fill-in lines
  (path, own name, peers) — and through a symlink, filling them in is a modification to the
  repository. `agb-peer` is expected on `$PATH` or `$AGB_PEER`, and participant names are something
  the agent is told or asks the user for, never guesses. It also lives in `skills/` rather than
  `.claude/skills/` where this repo's own `agbridge` skill sits: that directory is for people
  working *on* agbridge, this one is for an agent *being* a participant.

  **One skill file, not one per agent.** agterm's cookbook ships two because each agent drives
  delivery itself and they differ — Tab versus Return to submit, different command names. Here the
  relay absorbs every difference, so the agent side is byte-identical whether it runs on a cluster
  host or the Mac. Same uniformity that made the screen the transport.

  Two of its rules are lifted from the cookbook because they are failures somebody hit: **never poll
  or wait for a reply** (two agents each waiting on the other is a deadlock, and it is what this
  arrangement is most prone to), and **never touch the peer's terminal directly** — delivery is
  gated, and going around it types into whatever is on screen, including a permission dialog.

  Two warts fixed on the way: **`send` now refuses `--from`** instead of accepting and ignoring it,
  and `encode` lost the sender parameter it never wrote. There is no sender on the wire by design —
  the relay signs with the pane a message was found in, because an agent cannot print into another
  agent's pane — and a flag that looks like it changes that, and cannot, is worse than no flag.

  A test asserts **every flag the skill names is one the parser has**. The skill is prose an agent
  follows literally and is never executed, so a rename on one side is otherwise silent.

  **Carried forward, unfixed, and they are the cookbook's own**: the cursor check cannot tell an
  empty composer from one whose caret was moved back over text (agterm's `--help` says so); sending
  to Claude Code interrupts rather than queues; there is no transcript; and a model may decline to
  answer a perfectly delivered message.

  ⚠️ **The nine words `agb pane` acts on are refused outright, in either mode**, because mode
  detection is a screen read and a screen read can be wrong. That list is a cross-file agreement
  with `agb_ops` — a standalone Mac script cannot import it — and is pinned by a test that compares
  against the three tuples themselves.

### Changed

- **`agb-claude` mints the row before Claude starts.** A hook is what mints a key, so a row used to
  appear only once somebody typed — and in a directory Claude has not been trusted in, *never*: it
  stops on *"Is this a project you trust?"* and submits nothing, so no hook fires and no row is ever
  created. The script's own comment warned about that and could not do anything about it.

  The session's shell now hooks first and then `exec`s Claude. Three properties make that produce
  **one** row rather than two, and each was measured rather than reasoned about:

  - it runs **inside** the new session, because the anchor is `(host, tmux-server-pid, %PANE)` —
    hooking from the caller's pane would mint a row pointing at the wrong terminal, which looks like
    it works and is worse than no row;
  - **`exec` preserves pid *and* starttime**, so the identity the shell records *is* Claude's a
    moment later: `bind_key` finds a matching record and **adopts** the key instead of minting a
    second one;
  - it records a real pid. A pid-less entry adopts too — *"absence of evidence must never re-mint"* —
    but nothing except `agb prune` could remove it if Claude never started.

  The state is **`completed`**, not `active`: a session at an empty prompt is waiting for you, which
  is what that glyph means, where `active` would claim it is working and blink a transition that
  never happened. It raises no banner, since the finished-turn banner measures from a preceding
  `active` and a fresh key has none — verified against the bridge, not assumed.

  Best-effort by construction (`;` not `&&`, stderr discarded): a missing or broken `agb` costs a
  row, never a Claude.

- ⚠️ **`agb-claude -d` no longer sends `hi` by default.** The greeting existed *only* to make a row
  appear, and the row no longer needs it — so a detached start costs no turn and no API call.
  `--greet <text>` still sends one when you actually want the session warmed up or a first
  instruction delivered. Anyone relying on `-d` producing a first turn will notice; anyone relying
  on it producing a *row* will not.

  This is the one thing here that could surprise: the row for a detached session now shows
  `completed` from the start rather than briefly going `active` while "hi" is answered.

### Fixed

- **A peer whose tmux is unreachable could answer you and never be heard.** MEASURED, live: a Codex
  agent on a batch pool replied, `agb-peer send` correctly took the file transport, wrote the
  message, and it then sat in the spool unread for 25 minutes with no error at either end.

  The relay finds a message by reading `[peer #<id>]` off the peer's **visible screen**. On the tmux
  path that marker is a window name and the status bar renders it for free. On the file path there
  is no window to rename, so `agb-peer` *prints* it — and printed output is exactly what an agent UI
  folds behind a `ran N commands` summary. `drain_files` then bails at `no message id in the
  doorbell`, which is a correct report of a question nobody could answer.

  `skills/agb-peer/SKILL.md` now tells the sending agent to copy the printed `[peer #…]` line into
  its **visible answer** when `send` reports `(via file)` — an agent's own reply text is on the
  screen even when its command output is not. Verified by the same peer, on the same stuck message:
  the marker went on screen, the relay drained the file, and the reply arrived.

  ⚠️ **Whether an already-running agent picks this up turns on when its skills are read, and the
  two ends need not agree.** MEASURED for Claude Code: re-invoking the skill returns the file from
  **disk**, so an edit applies on the next invocation with no restart — only the one-line description
  used to decide relevance is loaded at session start. MEASURED for Codex as well, but only in the
  weaker form: a session started **after** an edit reads the file and follows the rule unprompted —
  its `~/.codex/skills/agb-peer` is a symlink into the checkout, one inode, and three round trips
  succeeded, including one on a completely fresh context with no hint from the other end. Whether a
  **running** Codex notices a mid-session edit is still untested, because in every round so far the
  edit preceded the restart, which makes a session-start read and an on-invoke read
  indistinguishable. Until someone edits the file under a live session, tell a running agent
  directly. And note what is *not* claimed: the transport was never broken. Nothing in
  `agb-peer` changed — this is a rule about what has to be on a screen, which is the one thing the
  relay cannot arrange for itself.

- **`docs/agtermctl.md` promised `session restore` as the structural fix for a row dying when its
  command exits. It is not, and building it would have fixed nothing that hurts.** The symptom is
  the familiar one: type `q` at the `agb pane` prompt and a live agent's row disappears, because
  agterm closes a session when its command exits. The entry claimed a per-session restore pin would
  prevent that. `agtermctl help session restore` on the installed 0.24.0 binary says the opposite —
  *"The override is written now and consumed on the NEXT launch — it never touches the running
  session."* So restore covers only the **restart** half (rows come back alive after agterm is
  relaunched, instead of as dead panes); the typed-`quit` half is untouched and still points at
  `session new --wait`. The two had been bundled into one table row because both were read off
  `agterm.com/commands` rather than run — the third time that page has cost this project a reversed
  decision, and the reason `CLAUDE.md` carries the rule about running the command on the Mac first.

  Corrected in place, and the same pass re-ran `--help` for four other commands the file had only
  ever surveyed. Two say more than was written down: **`session move`** takes a repeatable
  `--target` and `--after`/`--before` anchors (so deterministic row *order*, not just workspace
  restore), and **`session text`** reads a pane *"even when hidden"*, with scrollback and `--json`.
  **`surface cursor`** is new in 0.24.0 and was not in the survey at all — and its own help warns
  that a cursor column *"AT the prompt establishes nothing"*, which is worth having written down
  before someone builds an is-the-composer-empty check on it.

  Three constraints on `session restore` that nobody had recorded, and any future use inherits all
  three: it is **gated on a user setting**, it is **sticky** until cleared, and the pinned command is
  **readable via `tree`**, so it must not carry secrets.

  Also recorded: `agtermctl --help` and `agtermctl session --help` verbatim, closing two
  `_not recorded_` gaps that had been open since the file was written — which is what answers the
  third, `session list --help`, since there is no `session list`. No code changed.

- **The 0.24.0 doc re-survey shipped one wrong claim of its own, caught by running it.** The entry
  above said `session type`'s pane vocabulary had *widened* in 0.24.0 and that `right`/`scratch`
  were now aliases. Measured on a live row, the binary **rejects** `primary`, `top`, `split` and
  `bottom` — the accepted set is exactly the old `left`, `right`, `scratch`, while the 0.24.0
  `--help` documents all seven. So `TYPE_RIGHT`/`TYPE_SCRATCH` in `agb_ops` are correct as written,
  and "modernising" them to the documented spelling would have broken `agb pane`'s `[s]` and `[d]`
  with agterm's own help text as the justification. First case in this file where the bad source was
  the **help text** rather than `agterm.com/commands`.

  The same run retires the section's NOT YET MEASURED mark. ✅ **Keystrokes injected by
  `session type` do reach a remote agent's composer** — agterm → `ssh -t` → tmux → Claude, confirmed
  by typing a string into an attached agbridge row and reading it back out of the composer box
  unsent. ✅ **`surface cursor` works through that ssh** and reports column `2` for an empty Claude
  composer, the same value agterm's own two-agent-chat cookbook checks for a local agent.

  Three findings that were not being looked for:

  - **`foreground` does not change on attach.** It is the argv the session was *launched* with — for
    an agbridge row, always `agb pane` — so it cannot say whether anyone is attached. The cookbook
    validates a peer that way; here only `session text` can.
  - **Surface ids live in `tree`'s per-session `surfaces` list** as `surface:<session id>:<kind>`,
    with `kind` in the same `left`/`right`/`scratch` vocabulary. That object also carries `split`,
    `scratch`, `overlay`, `realized` and `status` — five answers agbridge currently assumes or
    re-sends, all in JSON it already fetches and discards.
  - ⚠️ **A zsh trap that reads as an agterm limitation**: `"surface:$ROW:left"` applies zsh's `:l`
    lowercase modifier, yielding `surface:<lowercased>eft` and an `invalid surface` error. It looked
    exactly like "the cookbook's target syntax is not supported on this build". Brace it.

  ⚠️ **The hazard the pre-check exists for fired on the first row picked**: its composer already held
  an unsubmitted draft, which `session type` would have appended to and submitted together on the
  next Return. Documented rather than designed around, because no fix is proposed yet.

### Installing after this change

**If your Mac's instances are already named** — every `install.sh mac` since 0.5.0 that passed
`--instance` — this is one edit to a command you re-run rarely: keep `--instance`, drop `--statedir`.

⚠️ **If your Mac still has the unnamed default instance, it has NO in-place upgrade.** There is no
flag combination that re-renders that job where it stands: `--instance` is mandatory, and adopting
the old file with `--config ~/.config/agbridge/config` re-demands `--statedir` by the rule above.
Give it a name instead — the ordered steps are under **[*Upgrading from ≤ 0.5.0*](#upgrading-from--050)**
in 0.6.0, and they were written for exactly this migration: boot the job out and *wait*, move
`config`/`rows`/`placements` into `~/.config/agbridge/<name>/`, re-run the installer with every
mandatory flag, read the `probed:` line rather than trusting the alias you copied, delete the old
plist, and re-mint the rows with `agb-refresh --instance <name>`.

Those steps still work as written. One clause in them has softened: step 3 says *"`--instance`
requires `--statedir`"*, and because step 2 has already moved the old config to the derived path, the
re-run in step 3 would now **adopt** the statedir out of it. Passing the flag anyway is correct and
is what the step shows — spelling it out is how you find out that the value in the file is the one
you meant, which is the same reason step 3 tells you to read `probed:` instead of trusting the alias.

**As always, a release is not installed by pulling.** The Mac loads `agb_mac`/`agb_ops` from
`~/.local/lib/agbridge/`, so `sh install.sh mac --instance <name> …` is required, and existing rows
keep the `agb pane` code they were *created* with until `agb-refresh` re-mints them.

### What this deliberately does not do

- **No legacy code path was deleted, and none should be.** Those paths read plists that **already
  exist on disk**, and refusing to create new ones removes none of the old ones — a plist on disk
  outlives the installer that wrote it. So `agb instances` still prints `(default)`,
  `bind_label_to_config` still treats a plist with no `--config` as naming the default config,
  `_is_agbridge_instance` still accepts the bare label, and `doctor`/`status-line`/`prune --via-ssh`
  still resolve `~/.config/agbridge/config` unconditionally. **Creatability changed; reachability did
  not.** Said here so that nobody later "cleans up" one of those branches believing this change
  retired it.
- **Symmetry is still not guaranteed.** `install.sh mac --instance hostb --config
  ~/.config/agbridge/config` writes the unnamed config, because `--instance` only *defaults*
  `--config` rather than owning it. Closing that would mean refusing a `--config` that resolves to
  the default path, which forbids a legitimate shape (adopting an existing file under a name) to
  prevent a deliberate act. Documented rather than closed — and it is exactly why the statedir
  adoption refuses to fire through that route.
- **`VERSION` was not bumped by that change**, deliberately. It lives at `agb:24`, the only place it
  lives, and the change was built under a hard constraint that `agb` is not touched — `agb` had one
  character of headroom against `AGB_PARSE_BUDGET` at the time, and `--print-statedir` landed
  entirely in `agb_ops`. A breaking CLI change does argue 0.7.0; the number decides nothing until a
  release does, so it sat under `## Unreleased` at 0.6.0. ✅ **This release picked it: 0.7.0**, on
  that breaking change. (`agb` is 105,269 characters against a budget of 105,300 and a **strict
  `<`**, so the headroom is now **30**, not one — the budget was raised twice since, each time on a
  measurement.)
- **`dist/com.agbridge.plist` was not renamed.** Only the filename misleads — its `@LABEL@`
  placeholder means it already renders every instance's plist, named ones included. Cosmetic, and
  out of scope.

### Verified live, 2026-08-28

`agb-dashboard` has now been run against a real agterm. All six paths pass: two rows by label; a row
with a **split open still costing one cell** (the reason every cell is `<id>:left`, and invisible to
any test); the fail-closed refusal opening **nothing**; `--detach` closed using only the command it
printed; `--roster`; and `--mru`.

Two failure modes were forced deliberately and behave as documented. `agtermctl` **absent** exits 1
naming the binary and the errno. `agtermctl` **wedged** — a fake passing `tree` through and hanging
on `dashboard` — returned at 31 s against a 30 s budget with *"the dashboard MAY BE UP"*, closed
nothing, and printed the close command. That is the `TIMED_OUT` branch a review pass added, doing
exactly what it claims.

⚠️ Still not verified: two `agb-dashboard` runs against each other, or one against a live
`relay --dashboard` — the single-grid contention the docs describe. And `session scratch`'s own
behaviour, unchanged from before.

### Not verified

⚠️ **This section used to say "nothing in `agb-dashboard` has been run against a live agterm",
directly under a heading saying it had been.** The live run happened and the stale list was not
pruned, so the file asserted both. What follows is what is *actually* still unverified.

- **Two grids contending** — two `agb-dashboard` runs against each other, or one against a live
  `relay --dashboard`. agterm has **one** grid and no ownership token, so each closes only a grid
  *it* opened; running both at once is documented as unsupported rather than defended against.

- **`relay --dashboard` following its membership.** The live run exercised `agb-dashboard`, which is
  a **one-shot with a foreground hold**; the relay's grid is the one that re-resolves every tick, and
  a departed participant's cell staying on screen is invisible unless you look for it. ⚠️ Two
  separate enumerations of these fixes in this repo dropped exactly this one. **Do not read
  `agb-dashboard`'s six passing paths as evidence for it** — they are different commands with
  deliberately opposite error policies.

- **`session scratch`'s own behaviour** — the `[d]` drawer added in 0.3.0. Nobody has watched a
  drawer open, be hidden, and come back with **the same shell still alive**, which is the entire
  reason `scratch` was chosen over `overlay`.

- **The composer-clear path end to end.** `\003` was measured to clear a draft with the agent alive,
  and the *decision* it drives is covered against a fake `Ctl` — but no live delivery has actually
  false-negatived, cleared, and been retried on the next tick.

- ⚠️ **`agb-peer-setup`, `agb-dashboard` and the `agb-hangout` skill are not installed by
  `install.sh`.** They are run from the checkout and load `agb-peer` by path from beside themselves.

## 0.6.0 — 2026-08-01

> **Two breaking changes**, both in the same direction: a command with no instance flag used to act
> on the unnamed instance and now acts on **every** one, and `agb forget-rows` refuses a bare run.
> Read *Upgrading from ≤ 0.5.0* below before installing — nothing has to change, but one habit does.

### Changed, and it is a breaking change

- **`agb-refresh` with no flags now refreshes EVERY Mac-side instance, not the unnamed one.**
  The symptom it fixes: with two bridges on one Mac, a plain `agb-refresh` stopped `com.agbridge`,
  forgot the *default* instance's bindings and started it again — reporting success in exactly the
  words it would have used for the instance you meant. The other sidebar stayed broken and nothing
  said so. That default was an artifact of install order rather than of the domain: every instance
  can be closed by hand, killed, or come back with its rows forgotten, and all of them have the same
  claim on the command that repairs that. (`docs/design.md` §5, limitation 1.)

  **What still narrows it**: `--instance`, `--label`, `--config` — and `--rows`, because naming one
  map *is* naming what to act on; such a run keeps today's semantics exactly.

  ⚠️ **`--key` does NOT narrow it, deliberately.** `agb-refresh --key a3f9c1e0` is typed by someone
  reading a key out of a bridge log, and nothing in that log says which instance minted it — "you
  should not have to know which instance" is the whole point. A key belongs to exactly one map, so
  the sweep finds it wherever it lives and fails only when *no* instance had it. Its one blind spot,
  said out loud: several `--key`s spread across *different* instances make every child report a
  missing key, so the run reports failure although all of them were forgotten. That errs towards
  failing for work that succeeded, and each instance's own `forget <key> -> <row>` lines are on the
  terminal.

  **An instance left without a running bridge fails the sweep.** It is the rule that makes
  bare-is-all safe: forgetting an instance's rows and then not starting its bridge again leaves that
  sidebar dark with nothing to re-mint it. It is a *sweep* rule — `agb-refresh --instance <name>` on
  a Mac whose plist was never rendered still warns and exits 0, which is a documented recipe.

  **A Mac with no instance plists at all is unchanged**: the run still forgets the default map and
  warns that nothing was restarted, which is the commonest recipe this command has.

  **Ctrl-C is safe again, and was not before.** A signal between the `bootout` and the restart used
  to leave that bridge down with its rows already forgotten — this command causing the dark sidebar
  it exists to cure. The trap that repairs it lives in the process that did the `bootout`, which
  under a sweep is the child; the sweep then stops there rather than bouncing the instances you
  interrupted it to protect.

  **Implementation note, because it will look odd**: the sweep re-execs `agb-refresh` once per
  label instead of looping in-process. This is 1,600 lines of `set -eu` with per-run globals and a
  `die` on most error paths — an in-process loop would carry one instance's state into the next, and
  any `die` would end the sweep with jobs already booted out and never started again.

- **`agb close-done` with no flags now reclaims `[done]` rows in EVERY instance**, one banner
  apiece — the same symptom as above, one command along: with two bridges on one Mac it reclaimed
  the *unnamed* instance's rows and reported success, and the other sidebar kept growing a row per
  agent with nothing to say so. It is safe to default to all here by construction: a `[done]` row is
  one whose agent is already gone, so reclaiming it in an instance you did not have in mind costs
  nothing you can lose.

- **`agb forget-rows` with no flags is now REFUSED, and names `--all`.** ⚠️ This is the one command
  that does not default to all, and the reason is *not* that it closes rows — `agb-refresh` closes
  every row it forgets too. The difference is what happens next: `agb-refresh` restarts the bridge,
  so the rows are re-minted within seconds, and `forget-rows` restarts nothing. A sweep nobody meant
  would leave every row of every instance closed until each bridge was bounced by hand. So the sweep
  that ends in a restart may default to all; the one that does not, may not. `--all` is the opt-in.

  **`--key <key>` is the other way in, and it sweeps** — same reading as `agb-refresh --key`: a key
  is read out of a log that does not say which instance minted it, so the run finds it wherever it
  lives, names the instance that had it, and fails only when *no* instance did. Running in-process,
  this side can tell "not in this map" from "in no map at all", which the shell sweep cannot.

  ⚠️ **`--rows`/`--placements`/`--config` still narrow, and `--rows` alone still implies the DEFAULT
  config** — the one place the old default survives, deliberately. Naming a map *is* naming what to
  act on, and `agb forget-rows --rows ~/.config/agbridge/rows` is the documented recovery for an
  install that has no instance name to give. Having just been told the default is gone, expect this
  to look inconsistent: it is the same rule as `agb-refresh --rows`, and a run that names a map has
  already answered the question the sweep exists to stop you having to answer.
  `--all` beside one of them is an error rather than a silent winner — that is invariant 12's shape,
  the right map under the wrong label, reported as success.

  **A Mac with a config and no launchd job is unchanged**: both commands say "no instances found"
  and act on the default map, which is `docs/cookbook.md`'s bare `agb close-done` recipe and the
  commonest shape there is.

  ⚠️ **One instance whose plist cannot be read stops the whole sweep**, rather than being skipped.
  Skipping it acts on the other maps and returns 0, and nothing tells you an instance was missed —
  "I could not answer" collapsing into "the answer is nothing" is the failure this whole change
  exists to remove. A plist that is *not* ours and cannot be read is still ignored, exactly as
  `agb instances --labels` ignores it, so somebody else's broken file cannot stop your sweep.

  Both commands also take `--launch-agents <dir>`, which says where to look for instances rather
  than which one to act on.

### Added

- **`agb instances`** — which instances this Mac has, a question you could not ask before. Four
  modes: a human listing (`name  label  config`), `--labels` (one per line, what the sweeps
  iterate), `--plist <path> --arg <flag>` (one plist's `agb bridge` flag — this **is**
  `agb-refresh`'s plist reader now), and `--probe`. All take `--launch-agents <dir>`.

  **It exists because the two sweeps share no language.** `agb-refresh` is POSIX sh and
  `close-done`/`forget-rows` run in process on the Mac; two implementations of "which plists are
  ours" would be a bug class of their own, and a sweep visiting a strictly smaller set than the other
  leaves an instance nobody repairs.

  ⚠️ **`--probe` prints the literal `instances-ok`, and that is load-bearing rather than decorative.**
  `agb` answers an **unknown command** with exit 2, empty stdout and `USAGE` on stderr — which is
  byte-identical to `--arg` answering "this plist names no config". So a checkout `agb-refresh`
  against an installed 0.5.0 `agb` would read *every* plist as silent, fall through to the default
  label and the conventional config, and report success in the words it uses when it is right. The
  probe runs once before any plist is read and is **stdout-compared, not status-compared** — a status
  alone cannot see `--python /bin/echo`, which exits 0 while printing its own arguments.

  ⚠️ **`--labels` has its own status contract, and the split is the whole point.** A **missing**
  `~/Library/LaunchAgents` is exit 0 with empty output — "there are no instances", the ordinary Mac.
  **Every other errno is fatal.** Collapsing the second into the first is "I could not answer"
  becoming "the answer is nothing": a Mac with a momentarily unreadable directory would sweep
  nothing and report success. Spelled out rather than left to `os.path.isdir`, which swallows every
  `stat` errno — a mistake that has shipped in this project before.

  **What counts as an instance is deliberately wider than the `com.agbridge` guard `agb-refresh` uses
  to pick a label**, and both are right: that one is a *claimant* rule (a third-party LaunchAgent must
  not stand for agbridge's default config), this one is asking who to sweep. A plist is an instance
  iff its label is in the `com.agbridge` space **or** its `ProgramArguments` runs `<…>/agb bridge` —
  **any** tree, not `realpath`-equal to this one, since a plist naming an `agb` elsewhere is
  supported. Over-listing costs a bounded refresh of something that turns out not to be ours;
  under-listing is an instance nobody sweeps.

  **`agb-refresh`'s ranking is untouched** — the map comparison, the five ranks, the multi-claimant
  warning and the `[ -e ]` split are byte-identical, and so is the 0/2/3/other status contract their
  twelve tests are written against. Only the *reader* moved. Porting the ranks to Python was proposed
  and rejected: it would have re-implemented `same_map` (fail-closed in shell, and
  `os.path.realpath` never fails — silent widening, the direction that bounces the wrong job) and
  killed the claimant warning, which is the count whose absence made the wrong-job bounce invisible
  in the first place.

- **A banner when a long-running agent finishes** — `notify_on_completed_after`, **on by default at
  300 seconds**. You start a long job on a detached agent, walk away, and until now the only way to
  learn it finished was to go and look. `blocked` already gets a banner because it means a human is
  required; finishing means the same thing, more quietly.

  **The threshold is the feature, not a refinement.** `blocked` is rare, but a turn ends *every time
  an agent answers you* — so an ungated banner announces the "yes" you typed three seconds ago. And
  there is no falling back on "only when I'm away": agterm raises the banner and bounces the Dock
  **even for the row you are currently looking at** (it suppresses only the unseen badge). At five
  minutes it announces the job you walked away from and nothing else.

  ⚠️ **This is on after an upgrade with no config change.** `notify_on_completed_after = off` opts
  out; any number retunes it. The number is the switch, so there is no second key to keep in step —
  and no way to write a combination that silently does nothing.

  **Rejected: `idle` as the trigger.** It is the obvious reading of "the agent went idle" and it is
  wrong — `idle` is emitted by the *bridge*, for `[?]` (feed quiet) and `[done]` (agent gone), and a
  farm-side stall flips every row to `[?]` roughly every 12–16 s. That would be an anti-feature.
  Also rejected: a boolean plus a separate threshold, which is two keys and one more way to get a
  silent no-op.

  **Carry these forward** — the duration is a heuristic and `docs/commands.md` lists all five ways:
  a turn shorter than one 2 s poll can never announce; a bridge that restarts mid-turn measures from
  the restart; **a turn that both started and finished while the bridge was down is never announced
  at all**, which is the feature being quietest exactly when you were away longest; a long outage
  inflates the reported duration, because it is wall time and not work; and `--from-stdin` replays
  re-announce everything in the recording.

- **`agb-host-line`** — run it on a new cluster host and it prints the `host_<name> = <ssh-target>`
  line the Mac needs, plus a command that appends it **to the right config**. Three things it gets
  right that a hand-written `echo` does not, each of which fails silently:

  **The instance.** With more than one bridge on a Mac, `~/.config/agbridge/config` is the wrong
  file — the line belongs to whichever instance watches *this* host's statedir, and a line in the
  other one does nothing at all. The emitted command resolves it by statedir, which is the
  discriminator (two instances sharing one would be two bridges rendering the same agents twice).

  **The name.** `own_host()` is `uname -n` with the domain stripped, so a key written against the
  FQDN never matches a record.

  **The ssh target, which the farm cannot actually know** — it is how the *Mac* reaches this box,
  over the Mac's network and ssh config. So it is printed as a guess to confirm rather than written
  automatically. And when this host is running a feed for the configured mac-id, the tool says so:
  that feed was started by the Mac over ssh, which makes the Mac's own `feed_host` a target already
  **proven** to work, and it points you at that instead. Not hypothetical — the FQDN guess for the
  host this was written on appears dozens of times in a real bridge log as
  `ssh: Could not resolve hostname …`.

  The emitted snippet is idempotent, and is an `if/elif/else` with no `exit` anywhere: it is pasted
  into an interactive shell, where `exit` closes the window.

- **`agb-ralphex`** — run [ralphex](https://github.com/umputun/ralphex) under **one** agbridge row
  for the whole plan. ralphex starts a fresh Claude per task and agbridge mints a key per *agent*,
  so a ten-task plan is ten rows carrying the same label and a banner apiece. This opens a row
  before ralphex starts and closes it after — one finished-turn banner, naming the plan and its
  outcome (`OK add-auth` / `FAILED add-auth`), with ralphex's own exit code passed through.

  ⚠️ **The marker row lives in its own tmux session, and that is load-bearing.** agbridge's anchor
  is `(host, tmux-server-pid, %PANE)`, and every Claude ralphex spawns inherits `$TMUX_PANE` — so a
  marker sharing that pane loses its key to the first task that hooks, and its closing
  `agb hook completed` mints a *fresh* row rather than closing its own. Measured before it was
  written, not assumed: in a shared pane the wrapper's own row is left stranded.

  It holds on the wrapper's **pid**, not a flag file, so a `Ctrl-C`, a kill or a dropped ssh still
  closes the row — a flag only gets written on the paths somebody remembered. And the wrapper does
  **not** wait for the marker to close: the holder's exit condition is "the wrapper is gone", so
  waiting would be each waiting on the other, every run.

  Two things it deliberately does not do. It does not touch ralphex's config — `notify_custom_script`
  would give richer detail (files, branch, additions) at the cost of coupling, and the exit code plus
  the banner's own duration covers the question actually being asked. And it does **not** suppress
  the per-task rows; those are useful (you can attach to a running task) and are a separate problem.

  Nothing here assumes anything about Claude Code: `agb hook` is a command that writes a state file.

  **Every argument goes to ralphex untouched**, leading dashes included — `agb-ralphex --review
  <plan>` just works, and so does bare `agb-ralphex` for ralphex's own fzf picker. What makes blind
  passthrough safe is that the wrapper's single option is namespaced `--agb-name`, which ralphex has
  no equivalent of, so nothing is ambiguous between the two programs. Requiring `--` before any dash
  would have been a tax on the normal case: ralphex has ~25 flags and its own documentation leads
  with `ralphex --review <plan>`.

  ⚠️ The row's name is taken from the first `*.md` argument, **not** the first argument without a
  dash. ralphex takes values after flags, so `-m 20 --worktree plan.md` would otherwise have named
  the row `20`, and `-b main plan.md` would have named it `main`.

- **`install.sh mac --instance auto`** — name the instance after the machine, instead of thinking of
  one. The installer was already ssh'ing `--feed-host` to read its hostname back, because a record's
  `host` is a *hostname* while `--feed-host` is an ssh *alias* and `host_<name>` is what makes a row
  clickable. `auto` spends that same answer on the name, so the config, the launchd label and the log
  directory all follow what the machine calls itself:

  ```
  instance: auto -> hostb01 (read back from hostb-alias)
  ```

  **One ssh, two readers.** The probe is asked once and the mapping finds it already answered — no
  second round trip, and no way for a box that renamed itself between two calls to end up with a
  config whose `host_<name>` does not match its own directory.

  ⚠️ **It is a word you type, and it can never become the default.** Re-running `install.sh mac` with
  the original flags is how you pick up new code, so an **absent** `--instance` has to keep meaning
  the default instance. Auto-naming by default would mint a *new* instance on every upgrade — new
  config, new launchd job, new rows map, every row duplicated in the sidebar. That is the whole
  reason this is not simply inferred, and it is why the literal name `auto` is now unavailable.

  ⚠️ **Every failure is a refusal that writes nothing** — never a fall-back to the default instance.
  That fall-back is the accident the flag exists to avoid: it would rewrite the first machine's
  `feed_host` and `statedir`, boot out its launchd job and point its bridge at the new box, in the
  same words a correct run uses. So the probe being best-effort for the *mapping* (a note, and you
  pass `--host` yourself) deliberately does not carry over to the *name*: an unreachable machine, a
  missing `--feed-host`, a contradicting `--no-probe`, the farm role, and a hostname that is not a
  usable label name are each refused up front.

  The hostname rule is looser than the instance rule on purpose — a `.` is fine in a `host_<name>`
  key and not in a launchd label component — so `auto` re-asks with the narrow one and names the
  **host** when it fails. You typed `auto`; a complaint about `weird.name` would otherwise read as
  being about something you wrote.

### Fixed

- **`agb-refresh` called a custom-label instance "(default)".** `install.sh mac --label <anything>`
  puts no shape rule on a label, so `weird.label` is a real install — and the banner reads the
  instance *name* back out of the label, with everything outside the `com.agbridge` space falling
  through to `(default)`. A bare run therefore announced two default instances, one of which was
  somebody's named machine, in the one line whose entire job is to say which instance moved. That
  banner is the whole mitigation for acting on the wrong instance, so a false one is worse than none.

  It needed the sweep to become reachable by accident: before it, the only way to land on such a
  label was to type `--label weird.label`, and whoever typed it knew what they had asked for. The
  sweep types it for you, once per plist in the directory. Such an instance has no name but its
  label, so the label is now what is shown; only `com.agbridge` itself is `(default)`.

- **`agb instances` printed a blank name for the default instance — and for a custom-label one.**
  The same bug as the entry above, in the listing rather than the banner, because the fix was
  applied in one place and the other was written from the same rule. Found by running the command on
  a real two-instance Mac, where it printed a named instance and then a row whose first column was
  two spaces:

  ```
  hostb  com.agbridge.hostb  ~/.config/agbridge/hostb/config
    com.agbridge  ~/.config/agbridge/config
  ```

  The name was derived by stripping the `com.agbridge.` prefix and answering `""` when the label did
  not start with it — and the default label never does, because it *is* the prefix. So the one
  instance every Mac has read as nameless, in a listing whose whole job is to say which instances
  exist. `com.agbridge` is `(default)` now and any other label shows the label, which is
  `agb-refresh`'s rule verbatim rather than a second one invented here.

  The two spellings — the shell `case` block and `agb_mac.instance_display_name` — are now pinned
  together by a test that **executes the shell block's own text** and compares it, so neither side
  can be changed alone. A re-spelling of the rule in the test would have been a third copy able to
  drift from both.

  The columns are padded now as well: a name column holding `(default)`, a hostname or a whole
  dotted label has no natural width, and this listing is read for the config paths in its last
  column. Padding stops at the last column that exists, so a job whose argv carries no `--config`
  does not end its line in blanks. `agb instances --labels` — what the sweeps consume — is
  deliberately untouched by all of this; only the human listing changed.

- **Nothing said that a config change needs the bridge restarted.** The bridge reads its config
  once, at startup, so editing `workspace`, `feed_host` or any `notify_on_*` leaves the running
  process on the old value — with no error, no warning, and a file that disagrees with the sidebar
  indefinitely. The config tables in `README.md`, `docs/commands.md` and the skill listed the keys
  and their defaults and never mentioned it.

  Found the slow way, during the first live test of the finished-turn banner: a config edited nine
  minutes after the bridge started made a working feature look broken for the better part of an
  hour, and the diagnosis went through the installed code, the process tree and the launchd job
  first. `host_<name>` and `pane`'s use of `jump_host` are genuinely different — `agb pane` is a
  fresh process per click — which is exactly what made the inconsistency easy to assume away.

  The skill also gains a troubleshooting row keyed on the *symptom* ("a config change seems to be
  ignored"), since that is the form the problem actually arrives in — and a warning to check that
  `agb-refresh` bounced the pid you meant, because with several instances a refresh without
  `--instance` acts on the default and reports success either way.

- **`agb doctor` called two of its own documented config keys typos.** `notify_on_blocked` and
  `notify_on_new_row` have been missing from `agb_ops.CONFIG_KEYS` since 0.4.0, so a Mac with
  notifications configured got *"unknown key -- a typo here is silent everywhere else"* for each,
  and a `WARN` on the whole config probe. Both are now known, along with the new key.

  Nothing caught it because **both tests that check that list iterate the list itself**, so dropping
  a key made them weaker rather than red — confirmed by mutation. The replacement spells the whole
  documented set out by hand. `docs/design.md`'s config table was missing the same two keys *and*
  `workspace`; all three are added there too.

- **`config_flag` had no tests at all**, through two releases — the off-switch tests inject settings
  straight into the renderer and never reach the coercer, so the entire reason the function exists
  (that the string `"0"` is true in Python) was covered by nothing. It is covered now, along with the
  new `config_seconds` beside it.

- **`agtermctl notify` had no contract oracle.** The test stub rejects anything outside
  `session <verb>`, so every `notify` the bridge sent through it came back "unknown command",
  `_agtermctl` turned that into a best-effort warning, and the end-to-end test stayed green while
  proving nothing about the banner path. The stub now has a `notify` arm requiring `--target`, and
  the test asserts both the recorded call and a clean stderr — a swallowed failure being precisely
  what went unnoticed.
- **`--farm` is documented, and the cookbook no longer tells you to write a config line the
  installer already wrote.** Both came out of the first real instance install on a second machine.

  `--farm <ssh-target>` had no entry in `docs/commands.md` at all, and it is one flag away from
  `--feed-host` in every example — so the two read as interchangeable, and they are not.
  `--feed-host` is **stored** and is the bridge's transport for the life of the install; `--farm` is
  used **once**, by the installer, to run the farm side instead of printing it, and is written
  nowhere. For a no-shared-disk instance they name one machine and should be one string typed twice;
  they differ only on a shared-disk cluster, where the farm install runs on every agent host while
  only one is the feed host. Both now say so, in `commands.md`, `cookbook.md` and the skill.

  Getting `--farm` wrong is loud and stops safely — the Mac half is configured, then `ssh: Could not
  resolve hostname …` and `the farm side failed; the Mac is configured, the farm is not` — so the
  recovery is now written down too, because "half installed" reads worse than it is.

  Separately, the cookbook's *Make its rows clickable* step told you to append `host_<name> =
  <alias>` by hand. The installer has always derived that mapping by ssh'ing the feed host, so the
  step was busywork that also implied the automatic one had not happened. It now says when you
  genuinely need it: `--no-probe`, a machine that would not answer, or a rename.

- **`[enter]` failing with `missing or unsuitable terminal` is now diagnosed rather than discovered.**
  Found on the first attach to a second machine, and it reads as a broken row for several minutes:
  the identity is right, the ssh target is right, the config it resolved through is right, the ssh
  connects — and then tmux refuses to start because that host's terminfo database has no entry for
  the terminal you are attaching from. agbridge never sets `TERM`; `ssh -t` carries yours across, and
  modern terminals (Ghostty, Kitty, WezTerm) all ship terminfo an ordinary cluster box has never
  seen.

  The recipe is one line — `infocmp -x "$TERM" | ssh <target> -- tic -x -` — with two conditions that
  are the whole difference between it working and it silently installing the wrong entry:

  ⚠️ **Run it from inside agterm**, and use `"$TERM"` rather than a name you type. agterm is the
  terminal that runs the row, so it is the only place `$TERM` holds the value that reaches the far
  side. On the Mac this was found on, agterm reports `xterm-ghostty` while the same user's login
  shell reports `xterm-kitty` — copying the second would have installed a terminal the row never
  uses, and said `wrote` while doing it. A row's own `[s]`/`[d]` shells are no good either: both ssh
  to the *agent's* host.

  That also settles a tempting feature: **`install.sh` must not ship its own `$TERM`** to the farm
  host. It runs in your login terminal and the row does not, so it would be guessing about a terminal
  that does not exist yet, on every machine. If it is ever automated it belongs in `agb pane`, which
  knows the real value at the moment the attach fails. `docs/agtermctl.md` records agterm's `TERM` as
  a CONFIRMED observation, with that argument, so the idea does not get re-proposed.

  Written up in `docs/cookbook.md` (symptom → why → recipe → fallbacks when the Mac cannot dump the
  entry either) and in the skill's symptom table. The copy fixes **one host** — machine #3 behind a
  jump host, and every agent host on a shared-disk cluster, each need their own.

  ⚠️ The *verification* line that went with it was wrong on its first outing and is worth keeping
  right: **a farm login shell is often tcsh**, which has no `2>&1` and answers
  `Ambiguous output redirect.`, so `ssh host 'cmd >/dev/null 2>&1'` fails on the shell rather than
  on the command. Redirects belong outside the quotes. The copy itself was never affected —
  `tic -x -` has nothing to misparse — but every remote one-liner in these docs now keeps its
  redirects local.

- **The no-shared-disk check is about the disk, not the path.** Two sites using one home-directory
  convention give the *same* `--statedir` string on both machines while being separate filesystems —
  the normal shape of this, and previously easy to read as a mistake. What actually matters is the
  opposite case: a second instance pointed at a statedir the first can see makes both bridges read
  every session, so **every agent gets two rows**, one per instance, with nothing erroring. The
  cookbook now says to run the `touch`/`ls` probe rather than compare paths by eye, and what to do
  instead when it turns out the disk *is* shared.

### Upgrading from ≤ 0.5.0

**Nothing has to change.** The new code sweeps a Mac that still has an unnamed default instance
perfectly well — the default label is just another label — so there is no window in which a bare
`agb-refresh` silently succeeds on nothing. What changes is that a bare `agb-refresh` and a bare
`agb close-done` now visit *every* instance, and a bare `agb forget-rows` stops and asks for `--all`.

**As always, a release is not installed by pulling.** The Mac loads `agb_mac`/`agb_ops` from
`~/.local/lib/agbridge/`, not from the checkout, so `sh install.sh mac …` is required — and existing
rows keep the `agb pane` code they were *created* with until `agb-refresh` re-mints them.

**Optional: give the default instance a name.** With the privilege gone there is no longer a reason
for one instance to be unnamed, and a named one is the one `agb instances` can tell you about. The
order matters, and two steps are easy to get wrong:

1. ⚠️ **Boot out and WAIT before moving anything.** A live bridge holds the rows map in memory and
   merges-then-writes, so a move underneath it is silently lost.
   `launchctl bootout gui/$(id -u)/com.agbridge`, then poll until `pgrep -f 'agb bridge'` loses that
   pid.
2. `mkdir -p ~/.config/agbridge/<name>/` and move `config`, `rows` and `placements` into it. Move the
   logs too if you want them separated.
3. Re-run the installer with **every mandatory flag** — `--instance` requires `--statedir`, and the
   `mac` role requires `--feed-host` and `--agb-remote-path`:
   `sh install.sh mac --instance <name> --statedir <p> --feed-host <t> --agb-remote-path <p>`

   ⚠️ **Read the `probed:` line, do not trust the alias you just re-passed.** The obvious way to
   fill those flags is to copy them out of the config you are moving — which faithfully copies a
   mistake, and a wrong `feed_host` is *invisible while the bridge is running*, because the bridge
   reads its config once at startup. On the Mac this migration was first run on, `feed_host` had
   been a misspelling of the **other** instance's alias for hours; nothing showed it until the
   restart in this very step reconciled the file with the process, and then every row went `[?]`.
   The installer ssh's the feed host itself and prints what answered:
   `probed: <alias> is '<hostname>' -> host_<hostname> = <alias>`. If that hostname is not the
   machine you meant, stop here — you are two steps from migrating a broken config into a new home.
4. `rm ~/Library/LaunchAgents/com.agbridge.plist`, and confirm the new label bootstrapped.
5. ⚠️ **Re-mint the rows**: `agb-refresh --instance <name>`. Rows minted by the default install carry
   **no `--config`** in their command, so until they are re-minted `agb pane` resolves `--host`
   through the *default* config — which, after step 2, is a file that no longer exists.
6. Check both instances' rows are present once each, and that clicking each reaches the right machine.

⚠️ **After that migration, `agb doctor`, `agb status-line` and `prune --via-ssh` describe a file that
is not there.** All three resolve `~/.config/agbridge/config` unconditionally and have no `--config`
(`docs/design.md` §5, limitation 3). That limitation gets *worse* on a Mac where every instance is
named, and giving them a `--config` is deliberately not part of this change.

### Verified live, 2026-08-28

`agb-dashboard` has now been run against a real agterm. All six paths pass: two rows by label; a row
with a **split open still costing one cell** (the reason every cell is `<id>:left`, and invisible to
any test); the fail-closed refusal opening **nothing**; `--detach` closed using only the command it
printed; `--roster`; and `--mru`.

Two failure modes were forced deliberately and behave as documented. `agtermctl` **absent** exits 1
naming the binary and the errno. `agtermctl` **wedged** — a fake passing `tree` through and hanging
on `dashboard` — returned at 31 s against a 30 s budget with *"the dashboard MAY BE UP"*, closed
nothing, and printed the close command. That is the `TIMED_OUT` branch a review pass added, doing
exactly what it claims.

⚠️ Still not verified: two `agb-dashboard` runs against each other, or one against a live
`relay --dashboard` — the single-grid contention the docs describe. And `session scratch`'s own
behaviour, unchanged from before.

- ~~**Two bridges, live, through a sweep.**~~ **Done, and it earned its keep.** Verified on a Mac
  with two named instances: `agb instances` listed both, a bare `agb-refresh` swept both, clicking a
  row from *each* landed on the right machine with the right config on its identity line, and the
  farm side confirmed the beat at one second old. The unnamed default instance was migrated to a
  name in the same session, following the steps under *Upgrading* above.

  ⚠️ **The live run found two things the 1877 tests did not** — both listed under `### Fixed`: the
  blank name column in `agb instances`, and a `feed_host` that had been wrong and dormant in a config
  for hours. That is now **three of the last six** features needing a fix after live use, which is
  the argument for doing this every time rather than the argument for a bigger suite.

- **Two sweep failure modes still stub-only.** **Ctrl-C mid-sweep** — the child's trap is the only
  thing between that and a bridge left down — and a **deliberately broken instance**, where the
  others should still be refreshed and the broken one's job **restarted anyway**. Both are covered by
  named tests against stub `launchctl`/`pgrep`/`ps`, which is not the same as watching a real job
  come back.
- ~~**Symmetry is a convention, not a guarantee.**~~ **Landed** — see *Unreleased*, *Changed, and it
  is a breaking change*. The rest of this bullet is the 0.6.0-era record of why it was deferred, kept
  because the cost it names is what the follow-up then paid: as of 0.6.0, `install.sh mac` could
  still create a *nameless* instance. Mandating `--instance` was split into a follow-up plan rather
  than bundled here: it does not fix the stated problem — the sweeps do — and it reaches 24+ test
  functions through the installer suite's `mac_args` fixture, changes what
  `dist/com.agbridge.plist` stands for, and forces a transitive `--statedir` decision on every Mac
  install *and upgrade*. Until it lands, an unnamed default instance remains creatable; it simply is
  not privileged any more.

## 0.5.0 — 2026-08-01

### Added

- **A machine that shares no disk with the first is now an install, not a workaround.** agbridge
  assumed **one** statedir, one feed, one bridge. That is right for a cluster whose hosts share a
  network home and wrong the moment you add a standalone Linux box: no shared disk means a second
  statedir, which means a second feed, which meant hand-editing the launchd plist with six explicit
  flags and a separate `--rows` file — and even then the two bridges shared **one** `placements`
  file and **one** `host_<name>` table, so the second machine's rows took their workspaces and their
  ssh targets from the first machine's config. Clicking one reached the wrong machine, or nowhere.

  ```sh
  sh install.sh mac --instance hostb \
      --feed-host hostb-alias --agb-remote-path /opt/agbridge/agb --statedir /home/you/.agbridge
  ```

  One independent bridge per machine, rows from all of them in the same agterm sidebar.
  `agb-refresh --instance hostb` repairs that one and leaves the others alone.

  **It is one flag underneath, `--config`,** and that is the whole design rather than an
  implementation note: the rows map, the placements file and the `host_<name>` table all derive from
  the config's *directory*, so an instance is a directory and there is nothing else to keep in sync.
  `--instance <name>` is sugar over the three flags that already existed — `--config`, `--label`,
  `--log-dir`. `--dest` and `--bin-dir` stay **shared**: one code install, N configurations, so an
  upgrade is one `install.sh mac` and not one per machine.

  ⚠️ **The row's own command carries the config too**, which is the part that would have been
  invisible. Clicking a row runs `agb pane <key> --host <hostname> …`, and `agb pane` resolves that
  hostname through **its own** config read — with two instances that read hit the default config, so
  instance B's rows would have resolved their ssh target from instance A's `host_<name>` table:
  click-to-attach reaching the wrong machine, or nowhere, with every test passing. The flag is
  emitted only when the path differs from the default, so a default install's row commands are
  byte-identical to before and existing rows keep the command they were minted with.

  ⚠️ **`--instance` refuses to run without `--statedir`.** Without one it would fall back to reading
  the *default* config's `statedir` — ssh to the right machine and read the wrong directory, then
  create it and report an empty farm for ever. Refusing is the whole mitigation; there is no value
  the installer can invent for a machine it has never seen.

  ⚠️ **A helper run without `--instance` acts on the default instance and reports success in the
  same words** — `agb-refresh` would stop `com.agbridge`, forget the *other* instance's bindings and
  restart it while you were trying to fix this one. Nothing can detect that you meant something
  else, so both `agb-refresh` and the row-map commands now print the instance and config path they
  are acting on **on every run**, not only on failure. That banner is the mitigation. The other six
  limitations are written out in [`docs/design.md`](docs/design.md) §5, *One Mac, several
  instances*, along with the one-bridge-many-feeds shape that was designed and **rejected** — it is
  a data-model change rather than a transport one, and its worst failure is one machine's outage
  blanking every row.

  ⚠️ **The one upgrade that is not transparent: an existing `install.sh mac --config <nondefault>`
  install gets DUPLICATE ROWS.** That flag existed before this change and the plist **ignored** it —
  the bridge read `~/.config/agbridge/config` whatever the config file said. Now the plist carries
  `--config`, so such an install's rows map moves from `~/.config/agbridge/rows` to
  `dirname(<nondefault>)/rows`, the old map is orphaned, and every row is minted again beside the
  ones agterm is still showing. The fix is one line, run **before** reinstalling — carry the map to
  where the bridge will now look for it:

  ```sh
  mv ~/.config/agbridge/rows ~/.config/agbridge/placements /path/to/nondefault-dir/
  ```

  If the duplicates are already there, `agb forget-rows --rows ~/.config/agbridge/rows` clears them:
  it closes each agterm session as it forgets it, so the orphaned copies go and the live ones — held
  in the new map — stay. `agb-refresh --config <path>` takes a bare config path for exactly this
  install, which has no instance *name* to pass. Default installs, which is everyone else, are
  unaffected: same plist but for the two new lines, same map, same placements, same row commands.

  ⚠️ **`install.sh --config` must now be an absolute path**, and is refused otherwise. It is the one
  path that reaches launchd, and the job runs with `WorkingDirectory /tmp`: `--config relcfg/config`
  wrote a real config to `$PWD/relcfg/config`, reported success, and handed the bridge
  `/tmp/relcfg/config` — read as `{}`, dying in a `KeepAlive` restart loop whose error named a file
  that existed where you were standing. The quoted `~/…` form was worse still, because
  `install-config` expands it and the plist does not, so the two halves of one install disagreed
  about which file they meant. Every other path flag already went through the same check.

  **Re-installing an existing instance keeps the `mac_id` it already has.** Adoption fires on every
  `--instance` run without `--mac-id` — i.e. on a routine upgrade — and a given id beats a recorded
  one, so probing only the default config *replaced* the instance's own. Every farm host of that
  cluster kept watching `bridge/<old-id>.beat`, so `agb status-line` read `bridge:DOWN` for ever out
  of an install asked to change nothing. Its own config is probed first now, the default's second.

  **`agb-refresh --instance <name>` reads the config out of that instance's plist** instead of
  rebuilding `~/.config/agbridge/<name>/config` by convention. `install.sh mac --instance hostb
  --config <elsewhere>` is supported, and against such an install the convention named a file that
  does not exist: `forget-rows` answered `the map is already empty` and exited 0 — the recovery
  command reporting success for a map it never opened, while the real one kept its stale bindings.
  The same plist value now feeds the liveness pattern, which also gained a trailing path boundary:
  `pgrep -f` is an unanchored regex, so `…/agbridge/config` matched an instance named `configb`.

  **A config path read out of a plist is XML-decoded, and regex-quoted before it becomes a
  pattern.** `install.sh` escapes what it writes, so `--config '/tmp/a&b/config'` reaches the plist
  as `&amp;` and `plutil -lint` is happy with it; and `pgrep -f` reads an *extended regular
  expression*, so a config at `/tmp/a+b/config` interpolated raw matches `ab` and not the path it
  came from. Both failed the same silent way: the liveness poll matched no bridge, exited with zero
  waits and no warning, and the forget landed under a live bridge — which merges-then-writes and
  re-mints rows against the ids `forget-rows` had just closed. Undecoded, `--config` was also wrong
  for `forget-rows` itself, which then answered `the map is already empty` for a map it never opened.

  **`agb pane` says which config it resolved through, and says so when it cannot read it.** A row
  command hard-codes the absolute config path it was minted with, so moving, renaming or
  reinstalling an instance made every existing row fall back to the bare hostname — silently, with
  no error and no exit code, which for two clusters with overlapping short hostnames is the wrong
  machine again. **Unreadable counts as "cannot read", not just missing:** `read_config` re-raises
  everything except `ENOENT`, and agterm closes a session when its command exits — so a config with
  the wrong mode printed a traceback and *destroyed a live agent's row* instead of warning inside
  it. It now reads as `{}` with the errno named on the row, which is also what makes that branch of
  the warning reachable at all.

  ⚠️ **And the warning fires on whether the READ failed, not on whether `--config` was typed.** The
  flag is emitted only for a non-default instance, so gating the diagnostic on it left the majority
  of rows — every default install's — swallowing an unreadable **default** config in complete
  silence: no `host_<name>` table, no `jump_host`, and a row that looks healthy while it resolves
  the bare hostname. On a two-instance Mac the default instance *is* instance A, so that is exactly
  the wrong-machine failure the warning exists for, arriving with the warning taken away. A merely
  *absent* config still says nothing, which is the normal state of a plain install.

  And the `no such session` hint the bridge logs now carries **this** instance's `--config`: a fixed
  `Run agb-refresh` is instance A's recipe printed in instance B's log, and followed literally it
  succeeds — on the wrong instance.

  ⚠️ **That hint is only correct because `agb-refresh` binds the label to the config it is given.**
  `--config` does not move the launchd label by itself, and while it did not, the new hint was
  *worse* than the fixed one it replaced: it booted out `com.agbridge`, waited for the **default**
  bridge to exit, then ran `forget-rows` against this instance's map while **this** instance's
  bridge was still running — the one condition the wait exists to prevent. The rows came back closed
  in agterm with the map still full of dead bindings. A config path is the only identity a bridge
  has (nothing tells it its instance name or its label), so the two halves are one change:
  `agb-refresh --config <path>` now looks that path up in the plists and adopts the label of the job
  that names it, saying so when nothing does.

  ⚠️ **That lookup compares the path *canonically*, because the two halves of the command normalise
  differently.** The label is matched against the plist's text; the map is derived by
  `forget-rows` with `os.path.dirname`, which normalises. So a spelling that still names the right
  map — `//`, a `.` segment, a relative path typed from the instance's own directory, a symlinked
  `$HOME` — named the right map under the *default* label, which is the bounce this matching exists
  to prevent: stop instance A, wait for A's bridge, then forget B's bindings while B's bridge is
  live and merging them back. Reachable by hand, too: this is the path `agb pane`'s warning tells
  you to type. A path whose directory does not exist has **no** canonical form and matches nothing
  rather than matching anything — and when nothing matches, the `note:` now names the job it is
  about to bounce and the bindings it is about to discard, since it is the one case where the label
  and the map provably come from different places.

  ⚠️ **…and it compares the resolved DIRECTORY, which is all the map is made of.** `rows` and
  `placements` are `dirname(config)/rows` and `dirname(config)/placements`, so the basename plays no
  part; keeping it in the comparison made the match *narrower* than the thing it guards. Every
  spelling with a different final component — `<dir>/`, `<dir>//`, `<dir>/anything` — named the
  right map under the **default** label, and `--config ~/.config/agbridge/hostb/` is what
  tab-completing the instance's directory leaves on the command line. Two instances deliberately
  installed into one directory now both match, which is right: they share the map too, and the
  "more than one launchd job claims this config's map" warning is the report that deserves.

  ⚠️ **Matching the map and choosing between the matches are different questions**, and running them
  together let a job that merely *shares* the directory beat the one whose config was named exactly:
  `*.plist` expands in collating order, so `com.agbridge.aaa` naming `<dir>/config.bak` won a
  `--config <dir>/config` run. Every directory-equal job is still a match and every one is reported;
  the one naming this exact file is the one used, compared canonically so `<dir>//config` still
  counts. **And a plist carrying no `--config` is an answer, not a silence** — the bridge it starts
  resolves the default config itself, so that is the map it holds. Skipping it made the wrong-job
  bounce *silent*: with a second job's config installed into the default directory (one map, two
  jobs), `agb-refresh --config <default config>` saw only the instance's plist, adopted its label,
  bounced it, and had nothing to warn about because it was then the only match — while the default
  bridge kept running over the map that was rewritten under it. The implication is confined to
  **readable** plists in the **`com.agbridge`** label space: `plist_config` answers `""` equally for
  "no `--config`" and "could not read it", and `~/Library/LaunchAgents` is shared with every other
  program on the Mac.

  ⚠️ **A narrow liveness miss now means "not proven gone", not "gone".** The pattern comes from the
  **plist**; the question is about the **running process**, and nothing keeps those in step —
  `install.sh mac --no-load` writes the plist and deliberately leaves the old bridge running, a
  hand-started bridge carries whatever was typed, a re-rendered plist describes a process that has
  not restarted. In each, the plist carries `--config` and the live bridge does not: the pattern
  missed on the *first* poll, the wait was skipped with no output at all, and `stopped:` was printed
  as a claim nobody checked — the forget landing under a live bridge, which merges-then-writes and
  re-mints rows against the ids just closed. Before the pattern was narrowed that bridge **was**
  waited for, so this was a regression rather than a gap. A miss now **reads the command lines**:
  `pgrep -f` for the pids of every `<agb> bridge`, `ps -ww -o args=` for what each one was started
  with, and `same_map` to attribute each to a map. The wait is announced with its reason.

  ⚠️ **The first shape of that follow-up was a subtraction, and it had a hole exactly where the
  label side did.** It asked only "is a bridge up carrying no `--config` at all?", by subtracting
  `pgrep -f "<agb> bridge --config"` from `pgrep -f "<agb> bridge"`, since "does not contain" is not
  a regular expression — and a bridge over **this** map, started with a merely different spelling of
  the same path (`<dir>/./config`, a relative path, a symlinked `$HOME`, or the plist re-rendered by
  `install.sh mac --instance` after the running bridge was started from an older one), was counted on
  both sides, read as "tagged, therefore somebody else's", and not waited for. `forget-rows` then ran
  under a live bridge, silently — the same bug `same_map` was extracted to fix on the label side,
  arriving on the process side. No pattern can close it: `pgrep -f` matches a regex against whatever
  spelling the process was started with, and the far side of a regex match cannot be canonicalised.
  Reading the command lines back and comparing canonically is what closes it. A bridge carrying some
  *other* `--config` is still not waited for: it belongs to whichever instance that path names.
  A bridge `ps` will not name **is** waited for — unattributable is not gone.

  ⚠️ **That probe asks its question only on a run repairing the *default* map.** Its first
  justification — no 0.5.0 plist can start an untagged bridge, so it cannot be another instance's —
  is true only of a bridge some 0.5.0 plist *started*, and the commonest untagged bridge on a Mac
  with instances is the **default job's**, still running from a plist rendered before the flag
  existed because `install.sh mac --instance <name>` renders only the new instance's plist and does
  not restart it. Against that bridge the probe waited the full 10 s on **every**
  `agb-refresh --instance <name>` and then printed `com.agbridge.<name> is still running after 10s` —
  a warning that the forget may have been undone, provably false, on every run of a recovery command:
  the exact every-run warning narrowing the pattern was meant to remove. An untagged bridge *is*
  attributable, just not by its command line: with no `--config` it resolves the default config
  itself, so the default map is the only one it can hold, and that is when the wait is kept. The 10 s
  warning also names **what was still running** — a probe-driven wait used to name `$label`, the job
  that had just been booted out and is precisely what the poll is no longer matching.

  ⚠️ That gate used to be spelled **once**, by emptying the broad pattern for a non-default run,
  which switched off the attribution of every *other* bridge too — and is how the differently-spelled
  case above went unwaited-for. It is asked per bridge now, where it can only ever be about an
  untagged one.

  ⚠️ **Which `--config` a repeated flag means is `agb bridge`'s answer, not the reader's.** The
  bridge's own parser reads its value flags into a dict with no duplicate check, so a repeated flag
  overwrites and the **last** one wins. Both readers here took the **first**: a hand-started
  `… bridge --config /old --config <this instance's>` is a bridge holding **this** map, and it was
  attributed to `/old` — not ours, zero waits, `forget-rows` under a live bridge, which is the one
  answer this probe is not allowed to get wrong. (The blank-prefix walk could not rescue it: it only
  *shortens* the remainder, so it offered `/old --config <this>`, `/old --config`, `/old`.) A plist
  hand-edited the same way was worse still — the job whose bridge holds the map was not recognised as
  a claimant, so the **default** job was booted out, and the liveness pattern was built from a path no
  process was running on. Now every occurrence on a command line is offered to `same_map`, and the
  plist reader keeps the last pair. ⚠️ **Both readings are kept on the process side on purpose**: `ps`
  flattens the arguments with blanks, so `--config "/a --config /b"` is *one* path containing that
  literal text and is indistinguishable from two flags — the first reading is right for it and the
  last for a genuinely repeated flag, and nothing on the line says which. Offering every occurrence is
  a superset of both; the extra candidates can only produce an over-match, which costs a bounded 10 s
  wait, and that is the direction this probe is allowed to be wrong in.

  ⚠️ **And `--config=<path>` is the same flag as `--config <path>`, which neither reader knew.**
  `agb bridge`'s parser partitions every argument on `=` before looking the name up, so both
  spellings start the bridge on the same file — and both readers scanned for the two-word form only,
  or for the two forms as two `case` arms, which is worse than it sounds. `case` picks by **arm
  order, never by position**: a command line whose inline occurrence came *first* cut at the later
  space-form one and threw the inline value away — and under the one-argument reading above (`ps`
  flattens, so `--config=/tmp/a --config b/config` is *one* argument whose value contains a blank)
  that discarded value is precisely the one the bridge is running on. Not ours, zero waits,
  `forget-rows` under a live bridge again, from a direction the previous round's permutation check
  could not see because it only ever composed lines out of *separate* flags. On the plist side an
  inline `<string>--config=/path</string>` read as **nothing at all**, and nothing there is not
  silence — it is read as "the default config", so an instance's own launchd job claimed the
  **default** map, the run fell through to the **default** label, and it forgot that instance's
  bindings with that instance's bridge still up: the wrong-job bounce, arriving through the reader.
  Both now scan one marker (the bare `--config`) in position order and let the character after it say
  which spelling it is — or that it is not the flag at all, so `--configs/b` sitting inside somebody's
  `--statedir` value no longer makes an untagged bridge read as tagged. Last-wins holds *across* the
  two spellings, not within each. (**The plist side no longer scans anything** — a later entry below
  hands its argv to `agb_mac.parse_bridge_args`, which knows both spellings and the last-wins rule
  first-hand; the rule is unchanged, and the `ps` side still spells it.) As a consequence the note
  explaining a wait is now read off the attribution's own answer rather than re-derived from the
  line, which had begun to disagree with it.

  ⚠️ **`--config` is not the only flag that says which map a bridge holds.** `agb bridge --rows`
  overrides the rows file the config would have chosen (`render_settings`: `opts.get("rows") or
  rows_path(config)`), so a bridge started `--config <elsewhere> --rows <this map's rows>` is writing
  the very file `agb-refresh` is about to rewrite — and, attributed on its config alone, read as
  somebody else's: zero waits, `forget-rows` under it. Both flags are now scanned on the command line,
  and a plist that names this map only through `--rows` claims the label too, ranked last (the
  installer renders no `--rows`, so it may replace the "nothing claims this config" fallback but never
  outbid a job that names the config itself). The reverse does **not** hold: a `--rows` is not
  evidence about the *untagged* question, because such a bridge still resolves the default config for
  its placements file.

  ⚠️ **And ` --config ` bytes on a `ps` line are not proof of a `--config` flag.**
  `agb bridge --workspace "farm --config /other/config"` carries no config flag at all — it runs on
  `agb.config_path()` and holds the **default** map — but `ps` flattens argv and prints a line
  byte-for-byte identical to one that does. Read as proof, it skipped the default-map question and
  answered "not ours" on a plain `agb-refresh`: zero waits, the forget landing under a live bridge
  over the map being repaired. The line cannot decide it (both readings are real argvs), so the
  undecidable case now resolves towards the **wait**: a `--config` is proof only when no other
  value-taking flag precedes it on the line. Nothing changes for a bridge launchd started — the plist
  puts `--config` immediately after `bridge` — so another instance's bridge is still not waited for
  and the every-run 10 s warning does not come back; a hand-started
  `agb bridge --feed-host X --config Y` is now waited for on a default-map run, which is the bounded
  direction. On the **plist** side the same false positive is not undecidable and is not treated as
  such: `ProgramArguments` is a real argv array, so the elements are read the way the parser reads
  argv — by handing them to it, in a later entry below — and
  `<string>--workspace</string><string>--config=/other/config</string>` is a workspace
  *name*. Read as a config, it made the banner, the liveness pattern and `forget-rows` all act on a
  map no process runs on — `the map is already empty`, exit 0, and the stale bindings that sent you
  there still in place. The list of flags that consume the next argument is now a cross-file agreement
  with `agb_mac.BRIDGE_VALUE_ARGS` (invariant 14) and is pinned by a test.

- **A `--config` written anywhere else in a plist was read as the bridge's own.** The reader said it
  walked `ProgramArguments` and in fact scanned every `<string>` in the file — and a plist is not an
  argv, only one of its keys is. `ProcessType`, the two log paths, `WorkingDirectory`, an
  `EnvironmentVariables` value and a `WatchPaths` array all carry `<string>`s. On a hand-edited plist
  both directions were reachable and neither said anything: a `--config` pair **after** the array
  *overwrote* the real one, because the reader takes the last occurrence (which is right *inside*
  argv, where a repeated flag really does overwrite) — so the banner, the liveness pattern and
  `forget-rows` all named a map launchd started nothing on, while the map the bridge actually holds
  kept the stale bindings that sent you there; a pair **before** the array *manufactured* a config
  for a job whose argv has none, claiming somebody else's map on behalf of a bridge that resolves the
  default one. The walk is now bounded by the array — `<key>ProgramArguments</key>` arms, the
  `<array>` immediately after it opens the walk, its close ends it, and any other key or `<string>`
  disarms. It reads **tokens rather than lines** on purpose: too *tight* a boundary is not the safe
  side here, since a missed `ProgramArguments` demotes that plist to "carries no `--config`" and a
  named instance's label then goes unfound, bouncing the default job while that instance's bridge is
  live. So a minified plist, a key and its array on one line, and a file truncated mid-array (which
  still stands on the last *complete* value) all read alike, and `dist/com.agbridge.plist` rendered —
  XML comment and all — is one of the tests. `docs/design.md` §5 carried the same wrong claim, and
  listed the stray-`<string>` case as failing towards a loud "no config found" when it could silently
  override a valid one. (**The array boundary and the token walk are both gone** — the entry two
  below replaced the whole token scan with `plistlib`. The *rule* is unchanged and is now structural;
  the truncated-file behaviour changed deliberately, and that entry says why.)

- **A comment in the plist made the whole file read as "no `--config`".** An XML comment can *contain*
  markup, and the reader tokenized it. `<!-- old argv shape: <array><string>bridge</string></array> -->`
  sitting between `<key>ProgramArguments</key>` and the real `<array>` is a perfectly valid plist that
  launchd starts normally — and the commented `<array>` **opened** argv while its `</array>` **closed**
  it, so the real array arrived disarmed and the file read as naming no config at all. For a named
  instance that is the quiet failure: its label is never found, so `agb-refresh --config <that
  instance>` bounces the **default** job and forgets the default map while the instance's bridge is
  still live. This is a shape the array boundary above made *worse* — before it, stray tokens in a
  comment were harmless noise; after it, they consume the boundary — and it is reachable on a
  hand-edited plist, which this branch documents as supported. The project ships a 60-line comment in
  `dist/com.agbridge.plist`, which passed only because it happens to contain no argv markup.

  Two neighbouring regions were then **measured rather than assumed**, and both were reachable too: a
  processing instruction (`<?…?>`) in that position does exactly the same thing, and a
  `<![CDATA[<key>ProgramArguments</key><array>…]]>` under some *other* key **manufactures** a config
  for a plist whose argv has none — the unsafe direction, where the liveness poll is narrowed onto a
  path no process carries, waits zero times, and forgets under a live bridge. All three are now
  removed as opaque regions before the element walk, across records, since any of them may span lines.

  One guarantee is unique to the comment and is the reason the shipped template was ever safe: **XML
  forbids `--` inside a comment and every flag name contains one**, so no valid comment can spell
  `--config`. A comment could only ever *hide* argv, never manufacture it. A PI and a CDATA section
  have no such rule, which is why they are dropped whole rather than read — **at a cost, stated
  rather than solved**: a config delivered *as* CDATA (`<string><![CDATA[/x]]></string>`) is lost and
  reads as an empty argument. That is still better than before, when the element matched no token at
  all and a `--config` waiting for its value silently took the **next** element instead — a
  manufactured path, the unsafe direction again. Nothing writes CDATA into a plist: not `install.sh`,
  not `plutil`, not `PlistBuddy`.

  ⚠️ **The class was narrower, not closed**, and the remainder was written down rather than argued
  away: the **DOCTYPE** declaration was not one of the three regions, so an internal subset spelling
  argv markup would still be walked; so were a tag whose own text spans lines, a flag or key name
  written as a character reference, and a binary plist. **The entry below closed all of them at
  once**, which is what an accurate limitation list is for — it is what said the tokenizer was still
  losing ground.

- **The plist reader was a hand-rolled XML tokenizer, and four consecutive review rounds found the
  rule it did not have.** Whitespace inside a tag, comments spanning a value, CDATA, processing
  instructions, DOCTYPE, character references, minification, nesting: each round added a rule and the
  next round found the next one. Two shapes ended it, both **valid plists** that `plistlib` and
  `plutil` read without complaint, and both failing towards a path *no bridge is running on* — the
  liveness poll then matches nothing, waits zero times, and `forget-rows` lands under a live bridge:

  * **A blank before the `>` of a tag.** XML allows `<string >` and `</array >`. The scan matched
    neither, so `<string >/real/config</string>` vanished and the dangling `--config` was spent on
    the next element (`--workspace`), while `</array >` never closed argv and let a later
    `WatchPaths` array overwrite the config with `/tmp/decoy/config`.
  * **A comment splitting a value across lines.** `<string>/tmp/a<!--`⏎`-->b/config</string>` is the
    string `/tmp/ab/config`; the scan kept its "inside a comment" state across records but not the
    text *before* the opener, so the element was lost and the `--config` spent on the next one. The
    entry above claimed a comment could only ever *hide* argv — true only of a comment sitting
    **between** elements, and this is the counterexample.

  `plist_arg` now **parses** the file with `plistlib`. That costs no new dependency: it is stdlib on
  macOS and on the Linux the suite runs on, and `agb-refresh` already requires a python3 — it dies
  without one and runs `agb` through it. (`plutil` was rejected the other way: macOS-only, so the
  suite could not test what it shipped.) "Only `ProgramArguments`" becomes structural — ask a dict
  for one key — and the whole written-down limitation list is retired: **binary** plists, a config
  delivered **as CDATA**, **character references** even in a flag name (`&#45;-config`), the
  **DOCTYPE** with an internal subset, and tags spanning lines are all read correctly now.

  ⚠️ **One behaviour changed deliberately, and it is not a widening.** A file that is not a plist —
  unreadable, truncated mid-element, or neither XML nor binary — now answers "this file says
  nothing" (exit 2) instead of standing on the last *complete* value the scan had seen. That reads
  like a loss and is not: cut a plist one element later and the scan answered `--workspace`, a flag
  name, as the config path. `bind_label_to_config` skips such a file rather than letting it imply
  the **default** config, which is what the readable-but-not-a-plist case used to do — a file saying
  nothing about any map claiming one under a real job's label. Nothing is lost by skipping it:
  launchd cannot load it either, so no bridge was started from it.

  ⚠️ **Two traps for anyone touching this.** `plistlib.load` **sniffs** the format off the first 32
  bytes and knows only `<?xml`, `<plist` and the binary magic — so a plist opening with its
  `<!DOCTYPE`, valid XML that launchd loads, is refused before parsing; the reader retries with
  `fmt=FMT_XML`, which skips the sniff without loosening anything (a file that is not XML still fails
  in the parser). And `$python` is now resolved **before** the label is bound, not beside the `$agb`
  check below it: left where it was, every read ran `"" -S -E -c …`, so every plist answered nothing
  and the run bounced the default job with a named instance's bridge live. Only one test omits
  `--python`, which is why that ordering has a test at all.

  ⚠️ **And three rules on the reader's own text and output, two of which only bite under a locale
  nobody tests in.** Inline in POSIX sh, it may contain **no apostrophe**. It must be **pure ASCII**
  — Python decodes a `-c` program with the *locale's* filesystem encoding and `-E` does not touch
  `LC_ALL`, so under `LC_ALL=C` a single `⚠️` in a comment is
  `Unable to decode the command from the command line`: the reader never runs and every caller reads
  "this plist names no config". And the value goes out as **UTF-8 bytes**, not `print` — which
  encodes with the locale, so a non-ASCII config path (ordinary on a Mac, where the filesystem is
  UTF-8 by fiat) raises `UnicodeEncodeError` under `LC_ALL=C` and, worse, *succeeds* under an
  ISO-8859-1 locale with a transcoded path that names nothing. The awk passed bytes through
  untouched. All three are tested, the last against three locales.

  The cost is a process per question rather than an awk per question: measured at 0.75 s for a
  pathological 20-plist directory read twice each, against the scan's 0.20 s, and ~36 ms for the one
  or two agbridge plists a real `~/Library/LaunchAgents` holds. This is a recovery command that
  already sleeps in a poll loop waiting for a bridge to exit. The acceptance bar is a **differential
  corpus** of forty-odd hand-editable plists compared against authorities that are not
  `agb-refresh`'s own code — `plistlib` for what the argv *is*, `agb_mac.parse_bridge_args` for what
  `agb bridge` *does* with it — and it keeps every shape the token scan got wrong **and** every shape
  it got right.

- **`docs/commands.md` and the `agbridge` skill said an instance's `mac_id` is adopted from the
  default config.** It is adopted from **this instance's own** config first, and only then from the
  default's. The distinction is the whole point of the ordering: the adoption fires on every
  `--instance` run without `--mac-id`, i.e. on a routine upgrade, so probing only the default would
  *replace* an id this instance had already published — leaving every farm host of that cluster
  watching a `bridge/<old-id>.beat` nobody writes, `agb status-line` reading `bridge:DOWN` for ever
  and `agb doctor` reporting no beat, out of an install that changed nothing anybody asked to change.

- **An empty value is a missing value in both shell scripts, as it always was in `agb`.**
  `--config "$cfg"` with `$cfg` unset is *one empty argument*, so a check that counted arguments saw
  a value where there was none — and every one of these flags has a default waiting a few lines
  later. `install.sh mac --config ""` installed cleanly against `~/.config/agbridge/config`;
  `install.sh mac --instance hostb --statedir ""` read as "no statedir" and would have inherited the
  first machine's farm path (ssh to the right machine, read the wrong directory), which is the one
  failure `--instance` refuses to install without; `agb-refresh --config ""` stopped, forgot and
  restarted the **default** instance while printing the same lines the run you meant would have
  printed. `agb`'s nine Python parsers have refused both `--opt=` and `--opt ""` since they were
  written; the two shell scripts could not import them and each spelled the check itself
  (invariant 14), and both spelled it wrong. Fixed in `need`, so it covers **every** value-taking
  flag rather than the one that was reported, and pinned by three tests: the two bodies must be the
  same text, every arm that reads `$2` must call `need`, and each flag is driven with an empty value.

- **The stale-row hint is quoted, because it is printed to be retyped.** `agb-refresh --config
  /Users/z/My Configs/config` is `--config /Users/z/My` plus an unexpected word once a shell has had
  it — so the one recovery command an operator copies out of a bridge log was invalid for any config
  path containing a blank, and worse than invalid for one containing a backtick. Config paths are
  free to contain both (`install.sh --config` asks only that the path be absolute), and
  `pane_command` had `shlex.quote`d the row's own command for exactly this reason forty lines away in
  the same file. An ordinary path is byte-identical, so nothing about a normal install changes.

  **`agb pane`'s unreadable-config warning names the file even when the exception does not.** A
  config that is a *directory* opens fine and fails in `os.read`, and errors from `os.read` carry no
  filename: the warning read `[Errno 21] Is a directory`, naming nothing — on a default row, where
  no other line of the identity block names a config either.

### Fixed

- **A launchd job whose arguments `agb bridge` REFUSES no longer claims your rows map.** A
  hand-edited plist can carry an argv the bridge exits on — `bridge --config=/real/config
  --config=` (a missing value), `bridge --config /real bridge --config /decoy` (a stray positional),
  `bridge --config /real --bogus` (an unknown option), `bridge --config /real --watchdog soon` (a
  number that is not one). launchd restarts such a job once every `ThrottleInterval` for ever under
  `KeepAlive` and no bridge is ever started, so it holds no map — but `agb-refresh`'s plist reader
  answered `/real/config` for all four and ranked it an **exact, declaring** claimant, top of the
  table. `*.plist` expands in collating order, so `com.agbridge.aaa` beat the live
  `com.agbridge.hostb` naming the same file: the run bounced the job that was not running, waited
  for nothing, and forgot the map while hostb's bridge was still live and merging rows back in.
  Such an argv now reads as "carries no `--config`", which is what a job that starts no bridge
  holds.

  ⚠️ **The fix is that the reader stopped simulating the parser and started calling it.** It had
  walked `ProgramArguments` itself for five review rounds, each round adding one more property of
  `agb_mac.parse_bridge_args` spelled a second time in shell — last-occurrence-wins, both `=` and
  two-element spellings, "an element is a flag only in flag position", only `ProgramArguments`, only
  after the `bridge` command word. A *refusal* is the class that walk could never reach, and adding
  it would have needed the **boolean** flag list as a second cross-file agreement plus two numeric
  validations: a wider agreement and a sixth round. The elements after the command word now go to
  `parse_bridge_args`, loaded by path from beside the `--agb` tree — which is no new dependency,
  since step 2 of the same script (`agb forget-rows`) already loads that file. `BRIDGE_VALUE_FLAGS`
  stays in the script for the `ps`-line scanner alone, which has no argv left to hand a parser.

  ⚠️ **A tree with no `agb_mac` beside `agb` is now fatal, naming `--agb`.** Reading every plist as
  silent there would bounce whichever job the unread plists left unclaimed and then fail the forget
  anyway — after the bootout. Two consequences worth knowing: a `ProgramArguments` containing a
  **non-string** element (a nested `<array>`, an `<integer>`) is a job launchd refuses outright and
  now counts as naming no config wherever the element sits, not only in value position; and
  `agb-refresh --instance <name>` against a plist hand-edited into a refusal falls back to the
  conventional config path, which is the merely-useless direction (`forget-rows` reports an empty
  map) rather than the destructive one, and `--config` settles it.

  ⚠️ **The test corpus had CODIFIED this**, which is worse than missing it: its oracle treated "the
  parser rejected this argv" as *no opinion*, so the declared expectation `/real/config` stood with
  nothing checking it. Parser rejection is now a decided answer — `""` — and six `rejected-*` cases
  assert it, two of which (a bad `--watchdog`, a value on a boolean flag) no argv walk could ever
  have got right.

- **`agb-refresh` no longer takes a launchd job that starts no bridge for the one holding your
  map.** `ProgramArguments` is the whole command line — `<python> -S -E <agb> bridge --config
  <path>` — and only what follows `bridge` is the bridge's argv, because `agb` reads its command
  from the first argument. A hand-edited plist with the flag in *front* of the command word,
  `<agb> --config /real/config bridge`, is `agb: unknown command: --config`: it starts nothing and
  holds no map, and under `KeepAlive` it does that once every `ThrottleInterval` for ever. The
  reader walked the whole array, so it answered `/real/config` for that job and ranked it top of
  the table — an exact, declaring claimant. With it sorting first, `agb-refresh --config
  /real/config` bounced the dead job, waited for nothing, and forgot the map while the real
  instance's bridge was still live and merging rows back in. A plist with no `bridge` in its argv
  at all now reads as "carries no `--config`" — the same answer a plist predating the flag gives,
  which the ranking already puts below any job that names one — rather than vanishing from the
  search.

  ⚠️ **The harness had never seen a real plist.** The differential corpus and the fixture behind
  every other plist test both modelled `ProgramArguments` as `["bridge", …]`, an array four
  elements shorter than any launchd runs, so forty cases were proving the property on inputs the
  property was not about. Nothing *fails* when a harness is simpler than reality, which is why both
  now carry a guard: the corpus runs every case in **both** shapes, and the fixture's rendered argv
  is compared against `dist/com.agbridge.plist` itself.

- **A `--python` that is not a python3 is refused, instead of quietly bouncing the wrong job.**
  The plist reader has four answers, not two: 0 is an answer, **2** means "this file says nothing",
  **3** means the parser could not be loaded from beside `--agb` (the entry above), and anything else
  means the *reader* failed — which is a statement about the interpreter, not about the plist. All
  three call sites read everything nonzero as the second, so `agb-refresh --python
  /bin/false` (an ordinary mistake, not a hand-edit) made **every** plist say nothing: no job
  claimed the config, the run fell through to the default label and the conventional config, and
  printed `stopped: com.agbridge` and exit 0 in exactly the words it uses when it is right — while
  the named instance's bridge kept running over the map it had just forgotten. It is fatal now, and
  an `import plistlib` probe runs once before any plist is read, because a *status* cannot see
  `--python /bin/echo`: that exits 0 and prints its own arguments, so every plist would "answer" a
  config path made of the reader's own source code.

- **`agb-refresh --instance <name>` against an unreadable plist refuses instead of guessing.**
  "There is no plist" and "the plist is there and cannot be read" both answered exit 2, and the
  conventional `~/.config/agbridge/<name>/config` is the right fall-back only for the first. For an
  install made with `--config <elsewhere>`, a truncated or unreadable `com.agbridge.<name>.plist`
  sent the run at a map that never existed — `forget-rows` reported "the map is already empty" and
  exited 0 — while the liveness pattern, built from the same empty answer, waited for a bridge whose
  command line names the real config. Success, twice, on the wrong instance. A plist that is not
  there at all still falls back to the convention, which is what lets a Mac whose plist was never
  rendered be refreshed by name. With `--config` given there is nothing to guess and the run
  proceeds, with a note that says the plist could not be read rather than the old one claiming it
  carried no `--config` — advice for a different file.

- **A row agterm has forgotten is written to once, not for ever.** Closing a row — by hand, or by
  typing `exit` at the `agb pane` prompt, which ends the pane's command and makes agterm destroy the
  session — left a bound entry naming a row that no longer exists. The bridge then renamed and
  statused that dead id on **every poll for the life of the process**, filling
  `~/Library/Logs/agbridge/bridge.err.log` with thousands of identical `no such session` lines. That
  noise hid the line that mattered on two separate days of debugging.

  Now agterm's own answer is believed: `no such session` marks the row dead, one line explains it
  and names `agb-refresh`, and nothing is sent to that row again.

  ⚠️ **The binding is deliberately KEPT, so the row stays gone.** Forgetting it would have the
  bridge mint a replacement within seconds — and closing a row is how you dismiss it. `agb-refresh`
  remains the deliberate way to bring every row back. The alternative (forget and re-mint, which
  would also make an agterm restart self-repair) was considered and rejected for exactly that
  reason: it trades away dismissal.

  ⚠️ **The match is narrow on purpose.** `agtermctl` exits 1 for *every* failure, so this keys on
  agterm's own words. A missing binary, a hung call or a permissions problem keeps being retried —
  otherwise one broken `agtermctl` would silently stop the bridge painting anything at all, which is
  far worse than the noise it replaces.

### Verified live, 2026-08-28

`agb-dashboard` has now been run against a real agterm. All six paths pass: two rows by label; a row
with a **split open still costing one cell** (the reason every cell is `<id>:left`, and invisible to
any test); the fail-closed refusal opening **nothing**; `--detach` closed using only the command it
printed; `--roster`; and `--mru`.

Two failure modes were forced deliberately and behave as documented. `agtermctl` **absent** exits 1
naming the binary and the errno. `agtermctl` **wedged** — a fake passing `tree` through and hanging
on `dashboard` — returned at 31 s against a 30 s budget with *"the dashboard MAY BE UP"*, closed
nothing, and printed the close command. That is the `TIMED_OUT` branch a review pass added, doing
exactly what it claims.

⚠️ Still not verified: two `agb-dashboard` runs against each other, or one against a live
`relay --dashboard` — the single-grid contention the docs describe. And `session scratch`'s own
behaviour, unchanged from before.

- **Instances have never been run live.** All of the above is covered by tests only — nobody has yet
  had two bridges up on one Mac. The check that matters is **clicking a row from each instance and
  landing on the right machine**: that path (`pane_argv` → the row's command → `agb pane`'s own
  config read) is exactly the one every unit test passes through without performing. Two of the last
  four features passed every test and still needed a fix after live use, and a feature about *which
  machine you reach* is squarely in that class. `README.md`'s verification table carries ⬜ rows for
  both checks.
- **The banner has not been read in anger.** Limitation 1's entire mitigation is that these commands
  name the instance they acted on. Worth running a bare `agb-refresh` on purpose while both bridges
  are up, to see whether the line actually tells you which one moved.

## 0.4.0 — 2026-07-31

Notifications. agbridge could show you a row's state; it could not get your attention when that
state changed. Three additions, all verified end to end against a live agterm — and two of the three
needed a fix *after* that testing, in ways the 1400-test suite could not have caught.

### Added

- **A desktop banner when a new agent appears** — label, host and working directory, attributed to
  its row. The counterpart to the one below: that says *you are needed here*, this says *something
  new exists*. The directory is in it because two agents on one host share everything else.

  Its own switch, `notify_on_new_row` (**on by default**), separate from `notify_on_blocked`:
  wanting one without the other is an ordinary preference.

  ⚠️ **Rows are minted in bursts and a burst is silent.** `agb-refresh` forgets every binding and
  the bridge re-mints all of them; so does a first install or a lost rows file. Without this a
  nine-row refresh would be nine banners and nine Dock bounces for agents running since breakfast —
  how a feature gets switched off on its first day. Rows created within `NEW_ROW_QUIET` (3 s) of the
  **first op batch of a connection** are silent, armed by that batch rather than at construction:
  the burst arrives with the snapshot, so a slow ssh would otherwise let the window expire before
  the very thing it exists to cover. It is a heuristic — an agent that genuinely starts inside that
  window is missed, which is the safe direction to fail. A `[done]` row reported again is a return,
  not an arrival, and never banners.

  Both halves of that were wrong on the first cut, and only running it showed
  it: a 10 s window armed at construction swallowed a real agent started 9 s
  after a reinstall.

- **A desktop banner when an agent starts waiting for you.** On a transition into `blocked` the
  bridge sends `agtermctl notify … --target <row>`: a macOS banner attributed to that row, which
  raises the row's unseen badge and jumps to the pane when clicked.

  `blocked` is the only state where *you* are the blocker — a permission prompt sitting unanswered
  — and the premise of a sidebar row is that you are not watching it. A glyph is enough for
  `active`; it is not enough for this.

  **On by default**, disabled with `notify_on_blocked = 0` in the Mac's config. Whether the Dock
  icon also *bounces* is agterm's own setting (Settings ▸ Notifications: off, once, or until you
  focus agterm, off by default). That split is deliberate: which events are worth announcing is
  agbridge's business, how loudly the machine interrupts you is the machine's.

  Verified live end to end (2026-07-31): an agent driven into `blocked` with agterm in the
  background produced the banner and a bouncing Dock icon.

- **The badge is cleared when the agent stops being blocked.** Somebody answered it, so the badge
  is advertising something already dealt with — and answering in a terminal you already had open
  never touches the agterm row, so agterm does not clear it on its own.

  Only for a badge agbridge itself raised, and only while `notify_on_blocked` is on: *unwind what
  you did*, so one switch governs the whole feature rather than half of it. Six limitations are
  written out in `docs/agtermctl.md` under `session seen`; the three that look like bugs are that an
  agent **killed** while blocked keeps its badge (it leaves through a removal, not a transition),
  that a **bridge restart** orphans one (the memory is per-process), and that **agterm never badges
  the row you are currently viewing** — nothing is unseen about a session you are looking at, which
  makes that row the one place this cannot be observed.

  ⚠️ **The transition is tracked separately from the painted status.** Gating on `applied` — what
  `--blink` uses — is the obvious implementation and is wrong: `_render_stale` paints every row
  `idle` on *any* disconnect, including a routine 10-second quiet spell, so an agent that merely
  stayed blocked would be re-announced after every hiccup. The banner's memory changes only when the
  **agent's** state does. Substituting the wrong gate fails five named tests.

## 0.3.0 — 2026-07-30

One feature, one fix.

### Added

- **`agb pane` gains `[d] drawer`** — an ssh shell on the agent's host, in the agent's directory,
  in agterm's **scratch drawer**: *over* the pane rather than beside it. It costs no width, and
  hidden it stays alive, so `[d]` brings the same shell back.

  ```
  [enter] attach   [s] split   [d] drawer   [q] quit >
  ```

  **`[s]` keeps the split**, deliberately rather than out of caution: neither subsumes the other.
  The split shows you the agent and the shell *at once*, which an overlay cannot do by definition;
  the drawer gives the agent its full width back, which a split cannot. `s` to watch a test run
  beside the output that provoked it, `d` for a look-and-leave `git log`.

  `shell` stays a synonym for the **split**. The prompt's label moved from `shell` to `split` —
  both panes hold shells now, so the pane is the distinction — but the word keeps its old meaning,
  or someone typing it out of habit silently gets a different pane than yesterday.

### Fixed

- **`agb-refresh` is on `PATH`.** `install.sh mac` links it into `~/.local/bin` beside `agb`; it
  had been reachable only as `~/.local/lib/agbridge/agb-refresh`, while two of the three docs wrote
  it bare. Wrong shape for a *recovery* command: one you have to remember an absolute path for is
  one you will not reach for when the sidebar has already gone wrong.

### Decisions recorded, because they will look like mistakes later

- **`session scratch --command` is not used**, though it would collapse two calls into one and
  remove the keystroke injection and its shell-quoting entirely. Its own help says it *"respawns
  the scratch if one is already open"* — so a second press of `[d]` would destroy a shell in use.
  Typing into the existing shell nests an ssh instead, which `exit` undoes. A shrug, not an
  incident.
- **`open_split` and `open_drawer` are duplicated, not parametrised.** They differ in two constants
  and one noun. They are kept apart because they are expected to **diverge**: `session scratch` has
  a `--command` that `session split` has no equivalent for, so the drawer may yet become a single
  call. Merging them is not a tidy-up.

### Verified live, 2026-08-28

`agb-dashboard` has now been run against a real agterm. All six paths pass: two rows by label; a row
with a **split open still costing one cell** (the reason every cell is `<id>:left`, and invisible to
any test); the fail-closed refusal opening **nothing**; `--detach` closed using only the command it
printed; `--roster`; and `--mru`.

Two failure modes were forced deliberately and behave as documented. `agtermctl` **absent** exits 1
naming the binary and the errno. `agtermctl` **wedged** — a fake passing `tree` through and hanging
on `dashboard` — returned at 31 s against a 30 s budget with *"the dashboard MAY BE UP"*, closed
nothing, and printed the close command. That is the `TIMED_OUT` branch a review pass added, doing
exactly what it claims.

⚠️ Still not verified: two `agb-dashboard` runs against each other, or one against a live
`relay --dashboard` — the single-grid contention the docs describe. And `session scratch`'s own
behaviour, unchanged from before.

`session scratch`'s **behaviour** has not been exercised against a live agterm. Its spelling is
`--help`-verified and its call path is mutation-tested against the `[s]` split it copies, but nobody
has watched a drawer open, be hidden, and come back with the same shell alive. `README.md`'s
verification table carries two unchecked rows for it.

## 0.2.2 — 2026-07-30

Bug fixes only. Every one was found by running the tool, not by review, and they share a theme:
**failures that were silent.** Nothing errored, nothing was logged, a row just quietly stopped being
right.

### Fixed

- **A row's status glyph disappeared on first attach and never came back.** agterm resets a
  session's status when the session's command starts. The bridge skips any repaint matching what it
  last sent, so it believed the row was already correct — and since a row's state only moves when
  Claude Code fires a hook, an idle agent's row could stay blank for hours.

  The underlying error was a category one: the renderer's memory was being used as *a description
  of what is on screen*. It cannot be, because agterm changes rows for its own reasons. It is now
  an optimisation only, and **every row's status is re-sent every 30 s** regardless of change. Never
  blinks, never runs while the feed is stale, ticks only.
- **The feed ssh had no connect timeout.** `ServerAlive` only starts once a session exists, so a
  laptop losing its VPN sat in the kernel's TCP timeout — minutes with every row `[?]` before
  anything was reported. Now `ConnectTimeout=20`.
- **The feed ssh could block on a prompt for ever.** A LaunchAgent has no tty, so an ssh deciding to
  ask for a passphrase or a host-key confirmation would hang: a live process with a dead bridge,
  indistinguishable from a quiet farm. `BatchMode=yes` makes it a loud, restarting failure instead.
- **Bridge warnings had no timestamps.** launchd's log is appended across every restart while the
  warning dedup is per process, so errors from three restarts ago read exactly like one happening
  now. This cost real diagnosis time. Every line is now stamped in UTC — and the dedup key stays the
  *raw* text, since stamping the key would make every line unique and silently kill the dedup.
- **The log had no bound.** An afternoon of `Could not resolve hostname` — `ssh`'s own stderr,
  inherited, so outside both the dedup and the stamp — buried the last 30 seconds under screenfuls
  of undated history. The bridge now truncates its own log past 4 MiB. ⚠️ **Truncates, not rotates:**
  the history is discarded. What matters is always the newest lines, and one line is written back
  saying the rest is gone, so a short log is never mistaken for a quiet one.
- **`agb-refresh` did not wait for the bridge to exit.** `launchctl bootout` returns when launchd
  *accepts* the request, not when the process is gone. The forget could land while the old bridge
  still held the row map in memory, letting it re-mint rows against ids just closed — reinstating
  the `no such session` spam that sends you to `agb-refresh` in the first place. It now polls until
  the process is gone, bounded at 10 s.

## 0.2.1 — 2026-07-30

### Fixed

- **A freshly minted row came up blank** — no glyph, agterm's own default name — until the agent's
  state next changed. `agb forget-rows` under a running bridge drops the binding from another
  process, so the next update minted a new agterm row while the render memory still described the
  old one.

### Confirmed

- **`--blink`** verified against a live agterm. No `ASSUMED` clause remained that agbridge actually
  depends on — until 0.3.0 added `session scratch`.

## 0.2.0 — 2026-07-30

First public release: at-a-glance Claude Code agent status from remote Linux hosts in
[agterm](https://github.com/umputun/agterm)'s macOS sidebar, over one long-lived `ssh` and a shared
NFS directory. No reverse tunnel, no sockets, no daemon on the Mac beyond the bridge.

Why 0.2.0 and not 1.0.0: reconnects, the watchdog firing and `prune` against a genuinely dead host
had not been exercised. `README.md`'s Status section tracks what has.
