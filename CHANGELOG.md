# Changelog

Every entry here says *why*, not just what. A rule without its reason gets re-litigated — and in
this project most of the reasons are a failure somebody actually hit.

Versions are `agb`'s `VERSION`, which both installers probe (`agb <version>`) before writing
anything. The wire protocol has not changed since 0.2.0: any farm host works with any Mac.

## Unreleased

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
  waited for, so this was a regression rather than a gap. A miss now asks one further question — is
  a bridge up carrying no `--config` at all? — by subtracting `pgrep -f "<agb> bridge --config"` from
  `pgrep -f "<agb> bridge"`, since "does not contain" is not a regular expression. The wait is
  announced with its reason. A bridge carrying some *other* `--config` is still not waited for: it
  belongs to whichever instance that path names.

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

  **`agb pane`'s unreadable-config warning names the file even when the exception does not.** A
  config that is a *directory* opens fine and fails in `os.read`, and errors from `os.read` carry no
  filename: the warning read `[Errno 21] Is a directory`, naming nothing — on a default row, where
  no other line of the identity block names a config either.

### Fixed

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
