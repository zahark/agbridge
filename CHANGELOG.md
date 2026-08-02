# Changelog

Every entry here says *why*, not just what. A rule without its reason gets re-litigated — and in
this project most of the reasons are a failure somebody actually hit.

Versions are `agb`'s `VERSION`, which both installers probe (`agb <version>`) before writing
anything. The wire protocol has not changed since 0.2.0: any farm host works with any Mac.

## Unreleased

> ⚠️ **One breaking change**: the Mac installer refuses an install that does not name its instance.
> Read *Installing after this change* below before upgrading a Mac — and read it **first** if that
> Mac still has an unnamed default instance, because that one has no in-place upgrade at all.

### Changed, and it is a breaking change

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

  **`install.sh farm` is untouched and still takes no `--instance`** — and no `--statedir` either. A
  farm host has exactly one identity: `agb hook` and `agb status-line` resolve
  `~/.config/agbridge/config` on every invocation, so a named farm config is a file nothing opens.
  Both halves of that asymmetry come from the one fact.

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
  duplicated in the sidebar. Both places that can name that job — `start_label`'s *"could not start"*
  warning and the sweep's *"no bridge was started again for:"* summary — now branch on the label:
  a named instance is told to re-run with **its own name**, filled in rather than left as a
  placeholder, and the nameless one is pointed at the migration under *Upgrading from ≤ 0.5.0*. The
  sweep is where this turns up, because it visits every plist in `~/Library/LaunchAgents`, a
  0.5.0-era one included.

### Added

- **`row_fields` — you choose what a row shows.** Titles were `label · host · cwd · pane · beat`,
  fixed, and on a real four-row sidebar that ran **69–77 characters**. The measurements say why:

  - the **host is identical on every row** on a single-host setup — 25 of ~72 characters, **35% of
    the line carrying no information**;
  - **`cwd` largely repeats the label** — one row read `data_pipeline_v2 · … · /home/zk/data_pipeline_v2`,
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
- **`VERSION` is not bumped.** It lives at `agb:24`, the only place it lives, and this change was
  built under a hard constraint that `agb` is not touched — it has **one character** of headroom
  against `AGB_PARSE_BUDGET`, and `--print-statedir` landed entirely in `agb_ops`. A breaking CLI
  change does argue 0.7.0; the number decides nothing until a release does, so this sits under
  `## Unreleased` at 0.6.0 and the release that ships it picks the number. Recorded so the omission
  is not read as an oversight.
- **`dist/com.agbridge.plist` was not renamed.** Only the filename misleads — its `@LABEL@`
  placeholder means it already renders every instance's plist, named ones included. Cosmetic, and
  out of scope.

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

### Not verified

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

### Not verified

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

### Not verified

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
