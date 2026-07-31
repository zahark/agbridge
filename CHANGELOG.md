# Changelog

Every entry here says *why*, not just what. A rule without its reason gets re-litigated — and in
this project most of the reasons are a failure somebody actually hit.

Versions are `agb`'s `VERSION`, which both installers probe (`agb <version>`) before writing
anything. The wire protocol has not changed since 0.2.0: any farm host works with any Mac.

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
