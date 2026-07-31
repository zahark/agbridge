# One Mac, several machines that share no disk

*Revised after a review pass that found four paths where an instance silently gets the wrong
config, and a mutation test aimed at the wrong end. Where a fix is non-obvious, the defect it
closes is named.*

## Overview

agbridge assumes **one** statedir, **one** feed, **one** bridge. That is right for a cluster whose
hosts share a network home, and wrong the moment you add a standalone Linux box: no shared disk
means a second statedir, which means a second feed, which today means hand-editing a launchd plist
with six explicit flags and a separate `--rows` file. A workaround, not a deployment story.

This makes a second machine a **supported install**:

```sh
sh install.sh mac --instance hostb \
    --feed-host hostb-alias --agb-remote-path /opt/agbridge/agb --statedir /home/you/.agbridge
```

One independent bridge per machine. Rows from all of them appear in the same agterm sidebar.

### Why not one bridge with several feeds

That shape was designed and **rejected**, and the reason belongs here so it is not re-proposed:
**it is a data-model change, not a transport change.** `BridgeModel._upsert` (`agb_mac:330`) keys
purely on `key`, and nothing in the wire, the model or the row map records which feed a session came
from. Adding several feeds means giving every session a *source*, and then:

- `_render_stale` must stale only the dead source's rows — today it marks **every** bound row `[?]`
- the watchdog, the quiet deadline, the reconnect and the backoff all become per-source
- `RowMap` and `agb pane` need the source to resolve `host_<name>` correctly

That is ~300–400 lines and a large share of the hardest existing tests, which all assume one stream.
Its worst failure — one machine's outage blanking every row — is the same shape as three real bugs
fixed this week. This plan's worst failure is *operating on the wrong instance*, which is
recoverable. Revisit the other shape past roughly four machines.

## Context (from discovery)

**The insight that makes this small: everything derives from one path.**

- `rows_path()` (`agb_mac:802`) → `dirname(agb.config_path())/rows`
- `placements_path()` (`agb_mac:1215`) → `dirname(agb.config_path())/placements`
- `agb.read_config(path)` (`agb:118`) already takes a path

So `--config <path>` gives an instance its own rows, placements **and** `host_<name>` table with no
new concepts and no shared mutable state.

- `bridge_settings` (`agb_mac:652`) and `render_settings` (`agb_mac:2168`) both already accept
  `config=`, and today each calls `agb.read_config()` separately (`:673`, `:2176`).
- `install.sh` already has `--config`, `--log-dir` and `--label`, with defaults applied at `:302`
  and `:326-328`. `--instance` is sugar over those three.
- ⚠️ **`agb` is at 102,429 of its 102,500-byte budget.** Nothing here may touch it.

## Development Approach

- **Testing approach**: regular (code first, then tests), per repo practice — with the standing
  addition that **every guard is mutation-tested**: break it, confirm a *named* test fails, restore.
- ⚠️ **Assert non-vacuity on every negative test.** "It did not use the wrong config" is trivially
  true of a code path that never ran.
- Complete each task fully before the next; tests are separate checklist items.
- **All tests pass before the next task starts.** `python3 -m pytest tests/ -q` (1439 at the start).

## Testing Strategy

- **Unit tests** in `tests/`, driven by the existing recording-`run` and `tmp_path` fixtures.
- **`install.sh` is shell**, so its coverage is a real install into a throwaway `$HOME` plus
  `plutil` validation of the rendered plist — the pattern `tests/test_install_pkg.py` already uses.
- **Live checks** under Post-Completion. Two of the last four features passed every test and still
  needed a fix after live use; a feature about *which machine you reach* is squarely in that class.

## Progress Tracking

- Mark items `[x]` immediately. `➕` for new tasks, `⚠️` for blockers.

## Solution Overview

```
~/.config/agbridge/config              default instance  -> com.agbridge
~/.config/agbridge/hostb/config        instance "hostb"  -> com.agbridge.hostb
                    ├── rows                (derived from the config's directory)
                    ├── placements          (same)
                    └── host_<name> = …     (inside that config)
```

**Key design decisions:**

1. **One flag, `--config`.** Rows, placements and the host table follow it. Nothing else needs to be
   passed, and nothing can drift out of sync because there is only one thing to get right.
2. **The row command carries it too.** See Technical Details — this is the difference between real
   isolation and decorative isolation.
3. **`--dest` and `--bin-dir` stay shared.** The three files are identical per instance, so there is
   one code install and N configurations. An upgrade is one `install.sh mac`, not one per machine.
4. **`mac_id` is reused, not minted.** It identifies *this Mac*, not this connection, and the beat
   lives inside each cluster's own statedir — so the same id in both is the truth, not a collision.
5. **The plist's new lines are unconditional; the row command's flag is not.** `--config @CONFIG@`
   renders for every instance including the default, so the installer has one path. But `pane_argv`
   emits the flag only when the path differs from `agb.config_path()`, so a default install's row
   commands do not change. Two different rules for two different audiences: the installer wants
   uniformity, existing rows want to be left alone.
6. **`--instance` requires `--statedir`.** ⚠️ *Review finding 4.* Without it,
   `install_config_values` (`agb_ops:3360`) falls back to `agb.statedir()`, which reads the
   **default** config — so a new instance would silently inherit instance A's farm path, ssh to the
   right machine and point at the wrong directory. `cmd_feed` would then *create* it and report an
   empty farm for ever, which is precisely what `bridge_settings`' required-statedir rule exists to
   prevent, arriving by a route that rule cannot see.

## Technical Details

⚠️ **The non-obvious site, and a silent bug if missed.** `pane_argv` (`agb_mac:1465`) builds the
row's command:

```
<python> -S -E <agb> pane <key> --host <hostname> [--tmux …] [--cwd …] [--jump …]
```

Clicking a row runs that, and `agb pane` then resolves `--host` through **its own** config read —
`ssh_target_for` (`agb_ops:1384`), reached from `pane_settings` (`agb_ops:1889`, read at `:1902`).
⚠️ *Review finding 3: an earlier draft also cited `agb_ops:1489`. That line is inside
`prune_via_ssh` (`agb_ops:1451`), not on any pane path — editing it would change
`prune --via-ssh`, which is out of scope.* With two instances that read hits the **default** config, so instance B's rows
would resolve their ssh target from instance A's `host_<name>` table: **click-to-attach goes to the
wrong machine, or nowhere.** Every unit test would pass.

So `pane_argv` must emit `--config <path>`, and `agb pane` must accept it — one entry in
`PANE_VALUE_ARGS` (`agb_ops:1769`).

⚠️ **How `pane_argv` learns the path is the part an implementer will get wrong.** *Review finding
2.* `pane_argv` (`agb_mac:1465`) has no access to it, and the renderer's only call site is
`pane_command(session, settings.get("agb_path"), settings.get("python"), jump_for(…))`
(`agb_mac:1714`) — while `render_settings` (`:2168`) returns no config key at all. Three routes
look plausible and two are wrong:

| route | verdict |
|---|---|
| `render_settings` publishes `"config"`, threaded through `pane_command` | ✅ correct |
| `pane_argv` calls `agb.config_path()` itself | ❌ always the default — **the exact silent bug this task exists to prevent**, with every test green |
| derive it from `dirname(settings["rows"])` | ❌ breaks the moment `--rows` is passed explicitly, which this plan *requires* to keep working: `--rows /tmp/rows` would emit `--config /tmp/config`, a file that does not exist, and `ssh_target_for` would fall back to the bare hostname |

So `render_settings` gains a `"config"` key sourced from `opts["config"]` **independently of
`--rows`**, and `pane_command`/`pane_argv` gain a `config` parameter fed from it.

**The predicate is `config and config != agb.config_path()`.** The flag is emitted only for a
non-default instance, which keeps a default install's row commands byte-identical — a stated goal,
and what keeps `tests/test_bridge_rows.py:466-471` (an exact `pane_argv` list) passing.

⚠️ **The `config and` half is not decoration.** `None` is the parameter's default and what
`settings.get("config")` returns for every hand-built settings dict in the tests, and
`None != agb.config_path()` is True — so the bare predicate emits a literal `--config None`.
Compare with `os.path.normpath` on both sides too: `install.sh`'s `DEFAULT_CONFIG` and
`agb.config_path()` agree for a normal `$HOME` and not for one with a trailing slash, and a
mismatch there makes **every default install** start re-minting row commands.

Three consequences: existing rows keep the command they were minted with, so no current install
changes; the path goes through the same value-safety checks as every other; and rows created before
this simply omit the flag and fall back to the default config, which is correct for them.

**What `--instance <name>` sets** — all three already have flags, so this is sugar:

| | default | `--instance hostb` |
|---|---|---|
| config | `~/.config/agbridge/config` | `~/.config/agbridge/hostb/config` |
| label | `com.agbridge` | `com.agbridge.hostb` |
| log dir | `~/Library/Logs/agbridge` | `~/Library/Logs/agbridge/hostb` |

## Limitations — documented, not solved

1. ⚠️ **The sharpest, and it is silent: a helper without `--instance` acts on the wrong instance and
   reports success.** `agb-refresh` would stop `com.agbridge`, forget instance A's bindings and
   restart it while you were trying to fix B. **Mitigation belongs in the output, not the docs**:
   `agb-refresh` and `close-done` print the instance and config path they are acting on, every run.
2. **An upgrade needs each job restarted.** The code is shared, so `install.sh mac` updates all
   instances at once — but a running bridge holds the old `agb_mac` until its job is rebooted.
3. **No aggregate view**, and it cuts both ways. `agb doctor` on each cluster sees only its own
   statedir, by construction. ⚠️ And on the **Mac**, `agb doctor` (`agb_ops:257`) and
   `agb status-line` (`agb_ops:2413`) read the **default** config unconditionally and have no
   `--config` — so `doctor`, the first thing anyone runs when an instance misbehaves, always
   describes instance A. Either give them the flag or say this plainly; do not leave it to be
   discovered.
   ⚠️ `prune --via-ssh` likewise resolves `host_<name>` from the default config
   (`agb_ops:1489`, inside `prune_via_ssh`), so a non-default instance's hosts may not resolve.
4. **Nothing marks which cluster a row belongs to.** `workspace = <cluster>` per instance is the
   recommended idiom — a convention, not a mechanism.
5. **N launchd jobs, N ssh connections, N logs.** Fine at ~4. Past that is where the rejected shape
   starts earning its complexity.
6. **Rows are per-instance**, so `agb-refresh` on one leaves the other's alone. Correct, and
   surprising.
7. ⚠️ **An existing `install.sh --config <nondefault>` install changes behaviour.** *Review finding
   12.* That flag exists today, and today the plist **ignores** it — the bridge reads
   `~/.config/agbridge/config` regardless. Once the plist carries `--config`, such an install's rows
   map moves to `dirname(<nondefault>)/rows`, the old map is orphaned, and every row is re-minted
   beside the ones agterm still shows: **duplicate rows.** This is the one upgrade that is not
   transparent — it needs a `CHANGELOG.md` entry naming the symptom and a one-line migration note
   (move the old `rows` file, or run `agb-refresh`).

## What Goes Where

- **Implementation Steps**: code, tests and docs here.
- **Post-Completion**: live checks needing a Mac and a second machine.

## Implementation Steps

### Task 1: `agb bridge --config <path>`, and one config read

**Files:**
- Modify: `agb_mac`
- Modify: `tests/test_bridge_transport.py`

- [x] add `--config` to `BRIDGE_VALUE_ARGS` (`agb_mac:577`)
- [x] `run_bridge` reads the config **once** from `opts["config"]` (default `agb.config_path()`) and
      passes that dict to both `bridge_settings` and `bridge_sink`/`render_settings`, which already
      accept `config=` and today each read it themselves (`:673`, `:2176`)
- [x] `render_settings` derives `rows` and `placements` from `dirname(<config path>)` when the
      caller passed no explicit `--rows`. **This derivation *is* the isolation** — assert the paths.
      ⚠️ Note `BRIDGE_VALUE_ARGS` has **no** `--placements`, so there is no override to honour on
      this path — and `RowRenderer` reads `settings.get("placements")` (`agb_mac:1571`), which
      **nothing populates today**, so an instance's bridge currently picks row workspaces from the
      *default* placements file. Deriving it is a fix, not just plumbing
- [x] `render_settings` also publishes a **`"config"`** key, taken from `opts["config"]` and
      **independent of `--rows`** — Task 2 needs it, and deriving it from the rows path is wrong
      (see Technical Details)
- [x] write the one-line assertion for it here rather than leaving it to Task 2:
      `mac.render_settings({"config": X})["config"] == X`, and unchanged when `--rows` is passed
- [x] ⚠️ `bridge_settings`' missing-value message (`agb_mac:692`) interpolates `agb.config_path()`
      and would name the **wrong file** for an instance: *"set it in ~/.config/agbridge/config"*
      while reading `hostb/config`. Pass the resolved path in and use it
- [x] write tests: `--config` puts rows and placements beside that config; no `--config` is
      byte-identical to today; an explicit `--rows` still wins; the error message names the
      resolved path
- [x] ⚠️ the read-once test needs a shape, or it is vacuous: `run_bridge` reaches
      `render_settings` first and `bridge_settings` **only on the ssh path**, so a `--from-stdin`
      test reads the config once before *and* after the change. Drive `mac.run_bridge([...])`
      in-process with `mac.bridge_supervise` monkeypatched to a no-op and `agb.read_config`
      counted, and assert the pre-change count would have been 2
- [x] run `python3 -m pytest tests/ -q`

### Task 2: the row command carries the config — ⚠️ the one that matters

**Files:**
- Modify: `agb_mac`
- Modify: `agb_ops`
- Modify: `tests/test_pane.py`
- Modify: `tests/test_bridge_rows.py`

- [x] `pane_command`/`pane_argv` (`agb_mac:1465`) gain a `config` parameter, fed from
      `settings.get("config")` at the call site (`agb_mac:1714`), and emit `--config <path>` only
      when it differs from `agb.config_path()`
- [x] add `--config` to `PANE_VALUE_ARGS` (`agb_ops:1769`) and thread it to the config read behind
      `ssh_target_for` — **`pane_settings` (`agb_ops:1902`) only**. ⚠️ Not `agb_ops:1489`: that is
      inside `prune_via_ssh` and out of scope (see Technical Details)
- [x] ⚠️ **do not add `"config"` to `parse_pane_args`' initializer** (`agb_ops:1836`);
      `tests/test_pane.py:242` asserts the opts dict **exactly**. Read `opts.get("config")`, which
      is how `cwd` is already handled — the alternative is a `KeyError` at attach time on every row
      minted before this change
- [x] a row minted **without** the flag keeps working against the default config
- [x] comment the reasoning at both ends: without this, instance B's rows resolve their ssh target
      from instance A's `host_<name>` table and click-to-attach reaches the wrong machine — with
      every unit test passing
- [x] write tests: `pane_argv` carries the flag; `agb pane --config` resolves `host_<name>` from
      **that** file and not the default; omitting it falls back
- [x] write a test at the **renderer** level, and drive it through **`mac.bridge_sink(model,
      {"config": X, …}, …)`** — ⚠️ *not* the `bridge` fixture, which constructs `RowRenderer` with a
      settings dict directly (`tests/test_bridge_rows.py:132-145`) and so never calls
      `render_settings`. Going through the fixture proves only `RowRenderer → pane_command`, which
      the mutation already covers, and leaves Task 1's `"config"` key **completely untested** —
      which is the very link this task exists to protect. Assert the minted row's `--command`
      contains `--config X`; note `tests/test_bridge_rows.py:520` uses `.startswith(...)`, so it
      catches neither an addition nor an omission
- [x] ⚠️ **mutation-test the renderer call site, not the pure function**: drop
      `settings.get("config")` from the `pane_command(...)` call at `agb_mac:1714`. Mutating
      `pane_argv` alone only proves a pure function emits what it was handed — the silent bug lives
      in whether the renderer hands it anything at all
      — done: exactly `test_the_minted_row_command_carries_the_instances_config` failed. Two more
      mutations for the same reason: the bare `config != agb.config_path()` predicate (three tests,
      including `test_a_default_install_mints_the_command_it_always_did` on the `--config None`
      case) and `pane_settings` reading `agb.read_config()` (both `--config`-resolution tests)
- [x] run tests

### Task 3: `--config` on the row-map commands

**Files:**
- Modify: `agb_mac`
- Modify: `tests/test_bridge_rows.py`

- [ ] `agb close-done --config <path>` and `agb forget-rows --config <path>`; both take `--rows`
      today, and `--config` derives it
- [ ] ⚠️ **`forget-rows` also takes `--placements`** (`FORGET_VALUE_ARGS`, `agb_mac:2419`), and
      `--config` must derive **that too**. *Review finding 1, the worst one:* deriving only `rows`
      means `agb-refresh --instance hostb` forgets B's rows and then writes B's `key = workspace`
      entries into **A's** placements file (`agb_mac:2492`, `:2537`) — the normal recovery command
      corrupting the other instance. `agb-refresh` passes no `--placements` today, so there is no
      existing escape hatch
- [ ] an explicit `--rows` still wins, so nothing existing changes
- [ ] both print the config they are acting on (limitation 1's mitigation, half of it)
- [ ] write tests: `--config` selects the right map **and the right placements file**; explicit
      `--rows`/`--placements` override; the printed line names the path
- [ ] run tests

### Task 4: the plist template and `install.sh --instance`

**Files:**
- Modify: `dist/com.agbridge.plist`
- Modify: `install.sh`
- Modify: `tests/test_install_pkg.py`

- [ ] template: two **fixed** lines, `<string>--config</string>` and `<string>@CONFIG@</string>`,
      **after** `<string>bridge</string>` — before it renders `agb --config X bridge`, which the
      dispatch rejects as an unknown command and launchd then restarts for ever
- [ ] `install.sh:412-416` needs a sixth `-e "s|@CONFIG@|$(rep "$config")|g"`; `rep()` already
      handles the XML and sed escaping, and the post-render placeholder guard at `:425` makes an
      omission loud
- [ ] ⚠️ three template-adjacent things break or go vacuous (*review finding 5*):
      `dist/com.agbridge.plist:7` says "the **five** at-sign placeholders" — now six;
      `tests/test_install_pkg.py:885` and `:909` each assert `ProgramArguments ==` an **exact
      list**; and `tests/test_install_pkg.py:1026` enumerates the placeholders for the guard that
      the template comment must not name them — omit `@CONFIG@` there and **the guard passes
      vacuously for the new placeholder**, which is the class its own docstring was written for
- [ ] `install.sh`: `--instance <name>` sets the config (`:302`), log-dir (`:328`) and label
      (`:330`) defaults. An explicit `--config`/`--label`/`--log-dir` still wins.
      ⚠️ *Lines `:326-327` are `dest` and `agentsdir` — the two that must stay **shared**; an
      earlier draft cited them by mistake*
- [ ] ⚠️ **`--instance` refuses to run without `--statedir`** (decision 6). Silently inheriting the
      default instance's farm path is the worst failure in this plan
- [ ] ⚠️ **`--instance` is refused outside the `mac` role.** `install.sh`'s option loop (`:262-292`)
      is role-agnostic and `$config` is used by the farm role too, so
      `install.sh farm --instance x` would write the farm's config to `~/.config/agbridge/x/config`
      — which nothing on the farm reads (`agb hook` uses `agb.config_path()` only) — and report
      success. `--label` and `--log-dir` are mac-only already, so this is mac-only sugar by
      construction
- [ ] ⚠️ **validate `<name>`**: it becomes a launchd label component, a plist *filename*, a log
      directory and a config *directory*, and `shell_safe` (`install.sh:119`) permits `.` and `/`
      — so `--instance ../../evil` passes it today. Alphanumerics, `-` and `_`; no `/`, no leading
      `.`
- [ ] `--dest` and `--bin-dir` stay shared: one code install, N configurations
- [ ] `mac_id` is **adopted** from the default config rather than minted (decision 4), via
      `agb install-config --config <default> --dry-run --print-mac-id` — **not** by parsing
      `key = value` in shell, which `install.sh:390-392` explicitly refuses as "the sort of second
      reader that drifts". ⚠️ With an empty default config `resolve_mac_id` (`agb_ops:3301`)
      *raises*, so the fall-back-to-minting branch must catch a **non-zero exit**, not an empty
      string
- [ ] **confirm** the `next:` hint already prints the farm command for this instance's statedir —
      `install.sh:502` builds one argv carrying `--statedir "$statedir"` for both the printed and
      the `--farm` forms, so this is a check, not a change
- [ ] write tests: a real install into a throwaway `$HOME` renders a plist whose label, config and
      log paths agree; `plutil` validates it; the default instance's plist is unchanged apart from
      the two new lines
- [ ] write tests for **all three new guards**, which the first draft asserted only as acceptance
      criteria: `--instance` without `--statedir` exits non-zero and installs **nothing**;
      `--instance ../../evil` is refused; `install.sh farm --instance x` is refused.
      ⚠️ **The fixture shape differs per test**: `mac_args` pins `--statedir`
      (`tests/test_install_pkg.py:487`), so the no-statedir test needs `"--statedir": None` too; and
      it hardcodes `argv = ["mac", …]` (`:497`), so the **farm-role test cannot use it at all**.
      `mac_args(**{"--config": None, "--log-dir": None, "--instance": "hostb"})` is the shape for
      the *happy-path* install test only
- [ ] mutation-test each guard — Tasks 1, 3 and 4 introduce guards and only 2 and 5 had a mutation
      checkbox
- [ ] run tests

### Task 5: `agb-refresh --instance`, and saying which instance

**Files:**
- Modify: `agb-refresh`
- Modify: `tests/test_agb_refresh.py`

- [ ] `--instance <name>` sets `--label com.agbridge.<name>` **and** the config path — those two
      must always move together, which is the whole reason for the sugar
- [ ] also accept a plain `--config <path>`: limitation 7's install (`install.sh --config
      <nondefault>`, no instance name) has no instance name to pass, so `--instance` alone leaves it
      unable to refresh against its own map
- [ ] ⚠️ **give `$config` a real default — `DEFAULT_CONFIG="$HOME/.config/agbridge/config"`,
      mirroring `install.sh:53`.** `agb-refresh`'s idiom for an unset path is the empty string
      (`rows=""`, `:53`), and empty is the one value that breaks all three uses below. Since the
      plist's `--config` is **unconditional** (decision 5), every instance's cmdline carries it — so
      a pattern of `"$agb bridge --config "` matches **every** bridge. A plain `agb-refresh` while
      instance B is up would then poll a live process for the full 10 s and print
      `WARNING: … still running after 10s` on the *most common* invocation: the mirror image of the
      symptom the previous checkbox removes. It would also print an empty path in the banner and
      make `--config ""` a missing-value error in `forget-rows`
- [ ] ⚠️ **print the instance and config path on every run**, not only on failure. Limitation 1 is
      silent otherwise: refreshing the wrong instance succeeds and says so
- [ ] pass `--config` through to `agb forget-rows` (which now derives placements from it too)
- [ ] ⚠️ **the liveness poll matches every instance.** `agb-refresh:111` is
      `pgrep -f "$agb bridge"`, and `--dest` is shared, so instance A's bridge satisfies the pattern
      while B is being refreshed: B boots out, the poll never clears, and it prints
      `WARNING: … still running after 10s; forgetting anyway` on **every** run — a warning that
      tells the operator the forget may be undone, in a recovery command they reached while already
      annoyed. The plist now carries `--config`, so match on `"$agb bridge --config $config"`
- [ ] ⚠️ **but derive the pattern from the plist, never assume it.** A narrow pattern is silently
      *vacuous* against a plist rendered before this change — and that is the normal state right
      after adding an instance: `install.sh mac --instance hostb` renders only hostb's plist and
      does not restart the default job, yet it **does** install the new `agb-refresh` (shared
      `--dest`, decision 3). So the default bridge is running with no `--config` in its cmdline,
      `bridge_alive()` returns false on the first call, the poll exits with **zero waits and no
      warning**, and the forget lands while that bridge is still alive — re-minting rows against ids
      it just closed. That is the `no such session` spam this script exists to cure, restored by a
      fix aimed at a cosmetic warning. Grep `$plist` for `<string>--config</string>`: narrow when
      present, broad plus a printed note when not
- [ ] write a test for the **stale-plist** case — reachable from the existing fixture, which
      pre-creates `com.agbridge.plist` as literal `<plist/>`
- [ ] ⚠️ the `pgrep` stub (`tests/test_agb_refresh.py:45-50`) **ignores its argv entirely**, so no
      test can currently observe this or a regression. Teach it to honour the pattern. The fixture
      also pre-creates only `com.agbridge.plist` (`:57`), so `--instance` tests need a second plist
      or they take the "no plist" branch
- [ ] write tests: `--instance` produces the right label and config; the banner names them; the
      default run is unchanged
- [ ] mutation-test: drop the banner → the naming test fails
- [ ] run tests

### Task 6: verify acceptance criteria

- [ ] two instances run side by side; rows from both appear, each updating from its own machine
- [ ] clicking a row on instance B reaches **B's** host, resolved from B's `host_<name>`
- [ ] `agb-refresh --instance hostb` leaves instance A's rows untouched
- [ ] a default-only install behaves exactly as before — same plist but for the two new lines, same
      config path, same rows file, and **the same row commands** (no `--config` emitted)
- [ ] `agb-refresh --instance hostb` does not print the "still running" warning while instance A is
      up, **and plain `agb-refresh` does not print it while instance B is up** — the pattern has to
      be right in both directions, and only the first was checked
- [ ] `--instance` without `--statedir` is **refused**, not defaulted
- [ ] run the full suite: `python3 -m pytest tests/ -q`
- [ ] `sh -n install.sh`
- [ ] `wc -c agb` — **must still be 102,429**; this plan touches `agb_mac`/`agb_ops` only

### Task 7: [Final] Documentation and release

**Files:**
- Modify: `README.md`, `docs/commands.md`, `docs/cookbook.md`, `docs/design.md`, `CLAUDE.md`,
  `CHANGELOG.md`
- Modify: `.claude/skills/agbridge/SKILL.md`
- Modify: `agb` (the `VERSION` line only)

- [ ] `docs/cookbook.md`: a **"a machine with no shared disk"** recipe, end to end
- [ ] `docs/commands.md`: `--config` on bridge/close-done/forget-rows/pane, `--instance` on
      `install.sh` and `agb-refresh`
- [ ] `docs/design.md`: CLAUDE.md calls it "the authority… reconciled against the implementation",
      and this changes where rows, placements and `host_<name>` live on the Mac
- [ ] `README.md`: the requirement currently reads "a shared directory that every agent host and the
      feed resolve to the same files" — it now needs the *per instance* qualifier
- [ ] `.claude/skills/agbridge/SKILL.md`: a recipe. **The skill has no answer for this today**, which
      is how the question arose
- [ ] all **seven limitations** written down, with limitation 1 marked as the one that bites
- [ ] record the rejected one-bridge-many-feeds shape and why, so it is not re-proposed
- [ ] `CHANGELOG.md` under `## Unreleased`, house style — say *why*, name the symptom, **and carry
      the one non-transparent upgrade** (limitation 7: an existing `--config <nondefault>` install
      gets duplicate rows unless its map is moved)
- [ ] bump `VERSION` (`agb:24`) — new capability, **minor**. Confirm length-neutral
- [ ] move this plan to `docs/plans/completed/`

## Post-Completion

*Needs a Mac and a second Linux machine.*

- install the farm side on the new machine with its **own** statedir, then
  `install.sh mac --instance <name>` and confirm both bridges beat (`agb doctor` on each machine)
- **click a row from each instance** and confirm you land on the right machine. This is the check
  for the failure Task 2 exists to prevent, and it is invisible to the test suite
- `agb-refresh --instance <name>` and confirm the other instance's rows do not move
- set `workspace = <cluster>` per instance and confirm the sidebar groups by machine
- ⚠️ **try the wrong command on purpose**: run plain `agb-refresh` while both are up, and check the
  banner tells you which instance it acted on. That banner is the only thing standing between you
  and a confusing five minutes
