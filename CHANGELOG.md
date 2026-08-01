# Changelog

Every entry here says *why*, not just what. A rule without its reason gets re-litigated — and in
this project most of the reasons are a failure somebody actually hit.

Versions are `agb`'s `VERSION`, which both installers probe (`agb <version>`) before writing
anything. The wire protocol has not changed since 0.2.0: any farm host works with any Mac.

## Unreleased

### Added

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
