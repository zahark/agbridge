# `install.sh mac` refuses an unnamed instance

> **Follow-up to `20260801-agb-symmetric-instances.md`**, split out of it deliberately rather than
> deferred by accident. That plan removed the *default instance's privilege* from every Mac-side
> command. This one removes the ability to create a nameless instance at all.
>
> **Planned 2026-08-02. Revision 4.** Revision 1's statedir mechanism did not exist; revision 2's
> replacement had two defects that a review found by *implementing* it in a scratch tree and
> measuring. Everything marked ⚠️ **measured** below was executed against the code, not inferred —
> and a third review re-implemented revision 3 end to end and **reproduced every one of them
> exactly**, so the numbers here are twice-measured rather than inherited.

## Overview

After the symmetric-instances change (0.6.0), no Mac-side command privileges the unnamed instance: a
bare `agb-refresh` and a bare `agb close-done` sweep all of them, a bare `agb forget-rows` is
refused, and `agb instances` lists what exists. But `install.sh mac` with no `--instance` still
**creates** a nameless one, so symmetry is a convention rather than a guarantee.

This plan makes `--instance` mandatory for the `mac` role.

| | before | after |
|---|---|---|
| `install.sh mac --feed-host X --agb-remote-path Y` | creates the unnamed instance | **refused**, naming `--instance` and `--instance auto` |
| `install.sh mac --instance hostb …` (new) | requires `--statedir` | unchanged — still requires it |
| `install.sh mac --instance hostb …` (existing, no `--config`) | requires `--statedir` again | **statedir read from that instance's own config** |
| `install.sh mac --instance hostb --config <path> …` | requires `--statedir` | **unchanged — still requires it** (see Decision 1) |
| `install.sh farm …` | nameless | **unchanged** — a farm host has one identity |

## ⚠️ What this does NOT do, and the sketch assumed otherwise

**No legacy code path can be deleted.** The sketch listed four things that "should not be true" and
implied this plan retires them. It cannot: those paths read plists that **already exist on disk**,
and refusing to create new ones does not remove old ones. A 0.5.0-era `com.agbridge.plist` stays
readable for ever.

So these all stay, and their tests with them:

- `agb_mac.instance_display_name`'s `(default)` spelling (`agb_mac:3232`)
- `bind_label_to_config`'s *"a plist with no `--config` implies the default config"* branch
  (`agb-refresh:712`)
- `_is_agbridge_instance`'s label-space clause
- `agb doctor` / `status-line` / `prune --via-ssh` resolving `~/.config/agbridge/config`
  unconditionally (design.md §5, limitation 3 — its own plan, still)

What changes is **creatability**, not reachability. That is a smaller claim than the sketch made, and
worth stating in the CHANGELOG so nobody later "cleans up" a branch believing this plan retired it.

**And creatability is not airtight either.** `install.sh mac --instance hostb --config
~/.config/agbridge/config` still writes the unnamed config — `--instance` only *defaults* `$config`
(`install.sh:447`, `[ -n "$config" ] || config=…`), it does not own it. Closing that would mean
refusing a `--config` that resolves to `$DEFAULT_CONFIG`, which forbids a legitimate shape (adopting
an existing file under a name) to prevent a deliberate act. **Out of scope** — but it is exactly why
Decision 1 below refuses to adopt a statedir through that route. design.md must say "no *new*
nameless instance is created by default", not "symmetry is a guarantee".

## Two questions that dissolved

- **`dist/com.agbridge.plist` is not a fossil.** ⚠️ **measured**: its placeholders are `@AGB@`,
  `@CONFIG@`, `@LABEL@`, `@LOGDIR@`, `@PATH@`, `@PYTHON@` — the label is one of them. The template
  already renders every instance's plist. The two shape-oracle tests that pin a fixture's argv
  against it are `tests/test_agb_refresh.py:2443`
  (`test_the_fixture_renders_the_argv_shape_the_installer_does`) and `:2526`
  (`test_the_plist_the_installer_actually_renders_is_read`); `tests/test_install_pkg.py:55` is only
  the `PLIST_TEMPLATE` constant they and `install.sh:47`'s `TEMPLATE=` share. ⚠️ **measured**: all
  keep passing under this plan's changes. Only the **filename** misleads. A rename is cosmetic and
  **out of scope**.
- **`--label`-only answers itself.** Today `--label <anything>` with no `--instance` falls through to
  `config="$DEFAULT_CONFIG"` (`install.sh:452`), so it *is* a second spelling of "unnamed". Once
  `--instance` is mandatory that path is unreachable — the config comes from the instance. `--label`
  **alongside** `--instance` stays legal and is the custom-label case `agb instances` already handles.
  No separate rule needed.

## Context (from discovery)

- **Repo**: this checkout, `main`, at 0.6.0 (released 2026-08-01). ⚠️ **measured: 1884 tests**.
  `CLAUDE.md` says 1877 in **two** places (`:8` and `:526`) and is already stale — `ba17783` added
  tests.
- **`install.sh` runs under `set -eu`** (`:43`). This is what makes Decision 2's placement a hard
  error rather than a style choice.
- **`install.sh:384-386`** proves all three distribution files exist at `$SELF` before role dispatch,
  and dies naming the missing one. That — not "the installer is run from the checkout" — is what
  makes `$SELF/agb` safe to execute in Decision 2.
- **`install.sh:438`** refuses `--instance` outside the `mac` role; **`:446`** requires `--statedir`;
  **`:447-449`** derive `$config`, `$logdir`, `$label`. Three separate lines — the plan must not
  conflate them.
- **`install.sh:482-483`** (inside `role_mac`) require `--feed-host` and `--agb-remote-path`. ⚠️ This
  is **before any filesystem mutation** — the first is `mkdir -p "$dest"` at `:510` — which is why the
  refusal belongs there and gets "installs nothing" for free.
- **`install.sh:725`** omits `--statedir` from the printed `install.sh farm …` hint when none was
  given, and **`:618`** does the same for the `install-config` argv. ⚠️ Both branches **die with this
  change** (Decision 4).
- **`install.sh:399-418`** is the comment block behind `--instance auto`, and `:400-406` states
  ⚠️ *"OPT-IN, and it can never become the default … an ABSENT `--instance` has to keep meaning the
  default instance."* ⚠️ **This plan falsifies that**, and the rule has to be marked withdrawn rather
  than deleted.
- **The mac-id adoption** (`install.sh:597-609`) probes this instance's own config first, then the
  default's, reading the value back through `agb install-config --dry-run --print-mac-id` and never by
  parsing `key = value` in shell. ⚠️ It is a **partial** model only: sharing a mac-id across instances
  is correct, sharing a statedir is precisely the failure `:446` exists to prevent. Decision 1 is not
  a symmetric mirror of it, and the comment must say so.
- **`tests/test_install_pkg.py`'s `mac_args` fixture** (`:480`) hardcodes
  `argv = ["mac", "--no-load", "--no-probe"]` at `:498`. ⚠️ **measured by `ast`: 65 functions take it
  — 62 `test_*` plus 3 helpers** (`_instance_args:1151`, `_probing_args:1852`, `_auto_args:1947`), not
  24.
- **`agb` is at 103,198 of 103,200 characters** — 1 spare. Nothing here touches it: the new flag lives
  in `agb_ops`, and `agb:2708`'s `USAGE` names `install-config` without any of its flags, so it needs
  no edit either. ⚠️ The budget guard is `len(agb_source) < BUDGET` on a **string**: characters, not
  `wc -c` bytes.

## Development Approach

- ⚠️ **DO NOT PUSH.** Local commits only — no `git push`, no tag, no release. The owner pushes by hand.
- ⚠️ **DO NOT TOUCH `agb`** — 1 character of headroom.
- **Regular** (code then tests), matching the repo.
- Complete each task fully; all tests pass before the next.
- **Each task inverts the tests IT breaks**, named in that task — not deferred to a sweep-up.
- One deliberate breaking change, with a `CHANGELOG.md` entry naming the symptom.

### The project bar

- **One mutation-check per guard**, not per task: break it, confirm a **named** test fails, restore.
  ⚠️ Verify the mutation actually applies and actually executes — the parent plan's implementation hit
  three separate vacuous-guard incidents, including a harness that `git checkout`ed *uncommitted* code
  and reported deleted-implementation runs as passes. **Commit before mutating, and restore from an
  in-memory snapshot.**
- **`ast` for source structure, `plistlib` for rendered artefacts.** Never a substring grep of source.
- **Assert non-vacuity** before asserting absence.
- **A refusal test must prove nothing was written**, not merely that the exit code was non-zero.
- **Never fabricate a pid**; **always pass `timeout=`** to `communicate()`.
- **Comments carry the reason, not just the rule.**
- Python **3.6.8** floor; `sh -n install.sh` must pass.

## Testing Strategy

- **unit tests**: required in every task.
- **e2e**: none. The equivalent is Task 4's end-to-end install into a throwaway `$HOME`.
- ⚠️ **The `mac_args` rewrite is the risk, not the installer change.** ⚠️ **measured**: with Tasks 1, 2a and 2b
  implemented and the fixture given shape (A), the failure set is **12 tests, all in
  `tests/test_install_pkg.py`** — identical to the set the fixture change alone produces. The
  installer change breaks nothing beyond the fixture. Making those 12 pass by mechanically adding
  `--instance` to each is how a fixture stops representing anything; Task 3 names all 12.
- ⚠️ **Harness blindness has been found four times in this codebase.** Most relevant here: a fixture
  that wrote a plist into *both* candidate directories, so the flag under test could not matter —
  inside the test written to catch that.

## Progress Tracking

- `[x]` immediately; ➕ for discoveries; ⚠️ for blockers.

## Solution Overview

Four changes: a read-back channel in `agb_ops`, three rules in `install.sh`, a fixture, and the docs.

**1. The `mac` role requires `--instance`** (Task 2a). A hard error, not a warning — a warning on a
first install gets ignored and the asymmetry becomes permanent on that Mac. The message names both
`--instance <name>` and `--instance auto`, so the fix is one word. The `farm` role is untouched: a
farm host has exactly one identity and its config path is resolved on every `agb hook` (invariant 4).

**2. `--statedir` is adopted from the instance's *derived* config when that config carries one**
(Tasks 1 + 2b). Narrower than revision 2 claimed — see Decision 1. It closes the re-typing hazard that
put a `feed_host` typo into one of the owner's instances, without reopening `:446`'s.

**3. The printed farm hint always carries `--statedir`** (Task 2b, Decision 4). Its conditional becomes
dead the moment the flag stops being optional for the mac role.

**4. The `mac_args` fixture gains an instance** (Task 3). The fixture *is* the statement of what a
default test install looks like, and after this change that is a named one.

**The statedir work is separable, and the tasks are split along that seam.** Task 2a (the refusal) is
the point of the plan and stands alone; Task 1 + Task 2b is the ergonomic follow-on. If it grows,
delete those two tasks and ship 2a — recorded here, not done quietly.

## Technical Details

### ⚠️ Decision 1 — the statedir read-back channel, and where it may read from

Revision 1 said to read it "through `agb install-config --dry-run`, the same way the mac-id adoption
does". **That mirror does not exist**, and the difference is the whole problem:

| | mac-id | statedir |
|---|---|---|
| resolver | `resolve_mac_id` (`agb_ops:3462`) | `install_config_values` (`agb_ops:3507`, the line at `:3521`) |
| with no own value | **raises** `MAC_ID_MISSING_NOTE` | falls back to `agb.statedir()` |
| what that fallback reads | — | ⚠️ **the default-path config** |

So `values["statedir"] = (opts["statedir"] or existing.get("statedir") or agb.statedir())` reports
`set: statedir = <the default config's>` and exits **0** for an instance config with no statedir key
— the exact value `:446` exists to refuse. A dry run cannot tell the two apart.

**(a) A new `--print-statedir`, chosen.** Stdout carries `existing.get("statedir")` **and nothing
else** — the file's own value, never the fallback — and it raises `AgbError` when there is none, so
`install.sh` reads it with the same `|| statedir=""` idiom the mac-id loop uses and non-zero means "no
own value". Rejected alternatives: an `$AGB_STATEDIR` sentinel (works, but depends on a precedence
order stated in a docstring in another file, and no later reader can follow it), and shell-side
`key = value` parsing (refused by the file's own rule at `install.sh:588`: *"a second reader of the
config format is the sort that drifts from the first"*).

⚠️ **It must be a pure query, not a print bolted onto the write path.** `run_install_config` writes at
`agb_ops:3710-3719` (`write_settings` at `:3716`), *before* the tail where `--print-mac-id` emits
(`:3726-3728`). `--print-mac-id` is safe only because its raise is early — `resolve_mac_id`'s raise at
`:3490`, reached through the `install_config_values` call at `:3686`. A raise placed "after the
report" has no such protection, and a review **measured** the result: on an instance config with a
`mac_id` and no `statedir`, the command exits non-zero *and leaves the file rewritten with the
default config's statedir* — the failure the flag exists to prevent, caused by the flag. So:

- the read and the raise go **immediately after `existing, _malformed = agb.parse_config(text)`**
  (`agb_ops:3685`) and **return** — before the `install_config_values` call at `:3686`, before
  `merge_config_text`, before `write_settings`;
- which also fixes a second measured defect: run *after* `install_config_values`, a config carrying a
  statedir but **no `mac_id`** raises `MAC_ID_MISSING_NOTE` instead of answering, and `install.sh`'s
  `|| statedir=""` would then die "needs `--statedir`" for a file that carries one. Non-zero must mean
  *no own statedir* and nothing else (CLAUDE.md invariant 12: *"I could not answer" is not "the answer
  is nothing"*).

⚠️ **And the installer may adopt only from the config `--instance` DERIVED.** Revision 2 claimed the
adoption "does not reopen `:446`'s hazard, because a new instance has no own config to read". A review
**measured** the counter-example — the shape this plan itself documents as still legal:

```
install.sh mac --instance hostb --config ~/.config/agbridge/config \
               --feed-host boxb --agb-remote-path /opt/agbridge/agb      # no --statedir
  today:  refused -- "--instance hostb needs --statedir"
  naive:  statedir: adopted /shared/DEFAULT  ->  exit 0
```

A bridge to `boxb` reading the *other* cluster's directory: precisely the named failure, arriving
through the one route the guard cannot see. `$config` is "this instance's own" by convention only
(`install.sh:447`), never by construction — and `--instance X --config <instance Y's config>` is the
same class.

**The rule: adopt only when `--config` was not given**, i.e. only when `$config` is
`$DEFAULT_CONFIG_DIR/$instance/config` as `:447` derived it. Record that at `:447` with a
`config_given` flag (initialised beside the other variables at `:318`, because `set -u`). An explicit
`--config` keeps today's behaviour exactly: `--statedir` required. That is a small ergonomic loss for
a shape nobody in this project uses, and it is the difference between a guard and a hole.

### ⚠️ Decision 2 — where the read runs, and why `:446` cannot host it

`$installed` is assigned at `install.sh:508` (`--dry-run`) and `:531` (real), both inside `role_mac`.
Under `set -eu`, referencing it at `:446` is an **unbound-variable abort**, not a fallback. And the
obvious repair — move the rule past `installed="$dest/agb"` — lands it *after* `mkdir -p "$dest"` at
`:510`, breaking `test_an_instance_without_a_statedir_is_refused_and_installs_nothing`
(`tests/test_install_pkg.py:1277`), which asserts `not (tmp_path / "dest").exists()`.

**The resolution: run it near the top of `role_mac`, against `$SELF/agb`, before any mutation.** Not a
new idea — the `--dry-run` branch already does exactly this (`verify_tree "$python" "$SELF/agb";
installed="$SELF/agb"`, `:507-508`), and `:384-386` has already proved the file is there. ⚠️ **measured**:
a review implemented this and `tests/test_install_pkg.py:1277` passes unchanged.

Resulting layout inside `role_mac`, all of it before `say "agb install (mac)"`:

```sh
[ -n "$instance" ]   || die …   # Decision 3's message -- the refusal
[ -n "$feedhost" ]   || die …   # existing :482
[ -n "$remotepath" ] || die …   # existing :483
if [ -z "$statedir" ] && [ "$config_given" = no ]; then
    verify_tree "$python" "$SELF/agb"
    statedir=$(run_agb "$python" "$SELF/agb" install-config \
                       --config "$config" --print-statedir 2>/dev/null) || statedir=""
    if [ -n "$statedir" ]; then
        shell_safe "the adopted statedir" "$statedir"
        absolute   "the adopted statedir" "$statedir"
        say "statedir: adopted $statedir from $config"
    fi
fi
[ -n "$statedir" ] || die …     # :446's message, moved
```

Five things to honour:

- **`:438`'s `[ "$role" = mac ]` check stays exactly where it is.** It is a *different line* from
  `:446`, and it is what makes moving the statedir rule into `role_mac` safe at all.
- ⚠️ **An adopted statedir bypasses the top-level `shell_safe`/`absolute` checks** (`:467-468`), which
  ran before it existed — hence the two lines above, mirroring `shell_safe "the adopted mac-id"` at
  `:603`. The value came from a config `check_config_value` already vetted, so this is defence in
  depth, and defence in depth is why that mac-id line exists.
- **`if … then … fi`, never `[ -n "$statedir" ] && say …`.** ⚠️ **measured** safe under bash, *not*
  verified under `dash` or macOS `sh`; a trailing AND-list that fails is one `set -e` reading away
  from a silent `exit 1` with no message, which `:1277`'s `assert "--statedir" in err` would catch
  only by luck.
- **No `--dry-run`** — the flag is read-only by construction (Decision 1). A test must pin that: the
  config is byte-identical after a `--print-statedir` run *without* `--dry-run`.
- **Output ordering, decided here**: the adoption's `say` lands *above* `say "agb install (mac) …"`
  and above the `instance:` banner. **Move it below the `instance:` banner** — an operator reads
  "instance: hostb … / statedir: adopted …" as one statement about one instance, and a bare
  `statedir:` line before the header names an instance nothing has printed yet. ⚠️ **measured**: a
  `--dry-run` install with adoption also prints **two** `verified:` lines (the adoption's against
  `$SELF/agb`, then `:507`'s against the same file). Harmless, and an operator will ask — either
  suppress the second or say in the comment why there are two.

`$python` is resolved at `:388`, before role dispatch, so `run_agb` is usable here.

### ⚠️ Decision 3 — the refusal message, with the backticks fixed

Revision 1's snippet had live backticks inside a double-quoted shell string — `` `agb instances` ``
is command substitution, and `die` would have run it. Single-quote:

```sh
    # The mac role and NOT the farm role: a farm host has exactly one identity,
    # and `agb hook` resolves `agb.config_path()` -- the default path -- on
    # every invocation. A named farm config is a file nothing opens (see :438).
    [ -n "$instance" ] || die 'mac: --instance is required. Every Mac-side instance is named, so `agb instances` can say what exists and no command has to guess which one you meant. Pass --instance <name>, or --instance auto to name it after --feed-host.'
```

### ⚠️ Decision 4 — the farm hint's `--statedir` conditional is now dead code

`install.sh:725` (`if [ -n "$statedir" ]; then set -- "$@" --statedir "$statedir"; fi`) builds the
printed `install.sh farm …` hint. After Decision 2 every mac install that reaches `:725` has a
non-empty `$statedir` — given, adopted, or the run died at the moved `die` — and the farm role never
builds that hint. The branch is unreachable.

**Make it unconditional and invert its test.** `test_the_printed_farm_command_omits_the_statedir_when_none_was_given`
(`tests/test_install_pkg.py:644`) asserts a property that no longer exists; keeping it alive by
reaching `:725` some other way would be asserting a shape the code cannot produce. This is the plan's
own "invert, do not delete" rule applied to a test whose subject the change removes.

⚠️ **But "the hint always carries `--statedir`" is already `:626`'s subject**
(`test_the_printed_farm_command_carries_the_statedir_the_mac_recorded`, eighteen lines above,
asserting exactly `hint and "--statedir %s" % (sd,) in hint[0]`), so a successor phrased that way is a
duplicate — and ⚠️ **measured**, `:644`'s own argv (`mac_args(**{"--statedir": None})`) is now
*refused*, so it cannot even be reused. **The successor's subject is the one Decision 1 created**:
`_instance_args(mac_args, **{"--statedir": None})` against an existing config that carries a statedir
— *the hint carries the **adopted** statedir*, which is now the only route to `:725` without the flag.
Write that, not a copy of `:626`.

⚠️ `install.sh:618` (`if [ -n "$statedir" ]; then set -- "$@" --statedir "$statedir"; fi`, building
the `install-config` argv inside `role_mac`) is dead by **exactly the same argument**. It is left
conditional deliberately: nothing tests it, and `:725` is made unconditional only because its
conditionality is what `:644` asserts. Say so in the comment, so the inconsistency is a decision
rather than an oversight. (`:757`/`:767` are `role_farm`'s and correctly stay conditional.)

### ⚠️ Decision 5 — the `mac_args` shape, decided here and not in the implementation

Three defensible shapes, and they break **different** tests:

| shape | what it does | cost |
|---|---|---|
| **(A) add `"--instance": HOST` to the args dict, keep `--config`/`--log-dir` pinned** ✅ | the fixture becomes "one named instance whose paths are still pinned into `tmp_path`" | ⚠️ **measured: 12 tests**, all in `tests/test_install_pkg.py` |
| (B) drop `--config`/`--log-dir` too, like `_instance_args` | maximal fidelity to a real named install | every pinned path escapes into `fake_home`; ~all 62 change what they assert about paths |
| (C) leave `mac_args`, add `--instance` at 62 call sites | no fixture change | the fixture stops stating anything — the failure mode Testing Strategy names |

**(A).** `--instance` only *defaults* `$config`, `$logdir` and `$label` (`install.sh:447-449`, each
`[ -n … ] ||`), so the fixture's pinned `--config`/`--log-dir` still win and test isolation is
unchanged; the one derived value that moves is the **label**, to `com.agbridge.box2`. `HOST` is
`tests/test_install_pkg.py:51`, the fixture's own `--feed-host` — so the fixture reads as "what
`--instance auto` would have named it", a true statement rather than an arbitrary token.

Two consequences:

- ⚠️ `_instance_args` overrides `--config`, `--log-dir` and `--instance` (`:1160`), so it composes
  untouched — **but it does not override `--label`**. Tests passing an explicit `--label` keep
  working; tests asserting the *derived* label are among the 12.
- ⚠️ Under (A) the fixture's `--config` is pinned and therefore **never the derived path**, so
  Decision 1's adoption does not fire for it. That is correct and convenient — but it means the
  adoption tests must go through `_instance_args` (which drops `--config`), and a test that used
  `mac_args` directly would be silently exercising the non-adopting branch. Say so in the fixture
  docstring.

## What Goes Where

- **Implementation Steps** (`[ ]`): everything in this codebase.
- **Post-Completion**: the live re-install, which only the owner can run.

## Implementation Steps

### Task 1: `agb install-config --print-statedir`, as a pure query

**Files:**
- Modify: `agb_ops`
- Modify: `tests/test_install_pkg.py`

- [ ] add `"--print-statedir": "print_statedir"` to `CONFIG_FLAGS` (`agb_ops:3396`), to
      `parse_config_args`' defaults (`:3630`), and to its docstring usage line (`:3617-3620`)
- [ ] in `run_install_config` (`:3667`), handle it **immediately after
      `existing, _malformed = agb.parse_config(text)`** (`:3686`) and **return** — before
      `install_config_values`, `merge_config_text` and `write_settings`. Write
      `existing.get("statedir")` alone to stdout, or raise `agb.AgbError` naming the file
- [ ] comment the two reasons the placement is load-bearing, both measured: run after the write and a
      statedir-less config is *rewritten with the default config's statedir*; run after
      `install_config_values` and a config with no `mac_id` raises `MAC_ID_MISSING_NOTE` instead of
      answering, so non-zero would stop meaning "no own statedir"
- [ ] comment that it prints the file's **own** value and never `agb.statedir()`'s fallback, naming
      the failure that would follow
- [ ] refuse `--print-mac-id` and `--print-statedir` together: both own stdout, and a caller reading
      one line would silently get the other's
- [ ] ⚠️ decide what a **mutating** option alongside it means. **measured**: today
      `install-config --config C --statedir /new --feed-host zzz --print-statedir` prints the *old*
      value, exits 0 and writes nothing — correct for a pure query, but "you asked me to write and I
      silently did not" is invariant 12's family. Refuse them (the parser already has the table), or
      state the rule in the docstring beside the two-`--print-*` refusal. Refusing is preferred
- [ ] write tests: prints the own value; **exits non-zero with nothing on stdout** for a config with
      no statedir key, and for a config that does not exist
- [ ] write a test with **both** files present holding **different** statedirs, asserting the
      instance's own is printed — so it cannot pass by coincidence
- [ ] write a test that a config with a statedir and **no `mac_id`** still answers (important: the
      measured regression)
- [ ] write a test that the config file is **byte-identical** after `--print-statedir` **without**
      `--dry-run`, in both the answering and the raising case
- [ ] write tests: the two `--print-*` flags together are refused
- [ ] **mutation-check**: move the read below `install_config_values`; confirm a **named** test fails;
      restore. Then: make the raise a `return ""`; confirm a **named** test fails; restore
- [ ] run tests — must pass before Task 2a

### Task 2a: `install.sh mac` requires `--instance`

Split from 2b so the plan's stated fallback — *"if the statedir work grows, drop it and ship the
refusal"* — is a deletion rather than a rewrite. 2a is the point of the plan and stands alone; 2b
needs Task 1.

**Files:**
- Modify: `install.sh`
- Modify: `tests/test_install_pkg.py`

- [ ] add the refusal at the top of `role_mac`, beside `--feed-host`/`--agb-remote-path` (`:482-483`)
      and **before any filesystem mutation** — Decision 3's message, single-quoted
- [ ] leave the `farm` role untouched, with a comment saying why it is different
- [ ] ⚠️ mark the `--instance auto` comment's withdrawn rule at `install.sh:400-406` — *"OPT-IN, and
      it can never become the default … an ABSENT `--instance` has to keep meaning the default
      instance"* is now false. **Keep the paragraph and mark it withdrawn with its reason**, per the
      house rule the plan restates: a withdrawn reason that is deleted gets re-proposed
- [ ] update `usage()` (`install.sh:66`): the `mac` synopsis gains `--instance <name>`, and the
      `--instance` entry states the new rule. ⚠️ Nothing tests `usage()`'s prose, so this is the item
      most likely to be forgotten
- [ ] write tests: a nameless `mac` install is refused, **writing nothing** — no config, no `$dest`,
      no `$agentsdir`, no `launchctl` call — asserted as absence, not exit code
- [ ] write tests: `--instance auto` and an explicit `--instance <name>` both still work
- [ ] write tests: `install.sh farm` still installs with no `--instance`
- [ ] **mutation-check**: downgrade the refusal to a warning; let the farm role inherit the
      requirement; move the refusal below `mkdir -p "$dest"` — each confirming a **named** test fails
- [ ] `sh -n install.sh`; run tests — must pass before Task 2b

### Task 2b: `install.sh mac` adopts its statedir from the derived config

**Files:**
- Modify: `install.sh`
- Modify: `tests/test_install_pkg.py`

- [ ] record `config_given` at `:447` (initialised at `:318`, because `set -u`)
- [ ] move the statedir `die` out of `:446` into `role_mac` per Decision 2; **leave `:438`'s
      `[ "$role" = mac ]` check exactly where it is**, and comment that the move is only safe because
      of it
- [ ] adopt via `--print-statedir` against `$SELF/agb`, **only when `config_given = no`** (Decision 1),
      re-running `shell_safe` and `absolute` on the adopted value like `:603` does for the mac-id;
      place the `say` below the `instance:` banner (Decision 2)
- [ ] comment that this is **not** a symmetric mirror of the mac-id adoption: that one deliberately
      falls back to `$DEFAULT_CONFIG` because sharing an id is correct, and sharing a statedir is the
      exact failure `:446` exists to prevent
- [ ] make `install.sh:725` unconditional (Decision 4), with a comment saying the flag is no longer
      optional for the mac role — **and saying why `:618`, dead by the same argument, is left alone**
- [ ] update `usage()`'s `--statedir`/`--instance` entries with the adoption rule
- [ ] write tests: statedir adopted from the derived config; still **required** for a new instance;
      still **required** when the existing config has no statedir key; the adopted value is announced
- [ ] ⚠️ write the Decision 1 test: `--instance hostb --config <the DEFAULT config>` with no
      `--statedir` is **still refused**, with a default config present that carries one — the measured
      hole. ⚠️ **measured: the existing suite does not cover this** — dropping the `config_given`
      condition leaves the failure set at exactly the same 12, so without this test *and* its
      mutation-check the guard ships vacuous
- [ ] write a test that an adopted statedir is the instance's own, never the default's — both files
      present, different values
- [ ] write a test that a **refused** install runs no `agb` against `$dest` — the adoption reads
      `$SELF/agb`, and a test that let it read a copied tree would assert the wrong file
- [ ] replace `test_the_printed_farm_command_omits_the_statedir_when_none_was_given` (`:644`) per
      Decision 4 — subject: **the hint carries the adopted statedir**, not "the hint always carries
      one" (that is `:626`'s, already)
- [ ] **mutation-check each guard separately**: let a new instance inherit a statedir; let the
      missing-key case pass; **drop the `config_given` condition**
- [ ] `sh -n install.sh`; run tests — must pass before Task 3

### Task 3: `mac_args` states what a default test install now is

⚠️ **measured**: shape (A) alone gives `12 failed, 149 passed` in `tests/test_install_pkg.py` and
`12 failed, 1872 passed` for the full suite; with Tasks 1, 2a and 2b also in, the failure set is **identical**.
All 12 are named below. Re-measure and reconcile before changing any of them — a 13th means something
in Tasks 1–2 went further than intended.

**Files:**
- Modify: `tests/test_install_pkg.py`

- [ ] apply Decision 5 shape **(A)**: `"--instance": HOST` in the args dict, `--config`/`--log-dir`
      still pinned; rewrite the docstring to say what a default test install now **is**, that the
      derived label is the only thing that moves, and that the pinned `--config` means the adoption
      branch does not fire here
- [ ] verify `_instance_args` (`:1151`), `_probing_args` (`:1852`) and `_auto_args` (`:1947`) still
      compose — all three override `--instance` or filter argv, none touches `--label`
- [ ] **mechanical (5)** — `:926`, `:943`, `:976`, `:1073`, `:1112` all assert
      `agents/com.agbridge.plist`, now `com.agbridge.box2.plist`. Update the path, no reasoning
      change. ⚠️ `:926` also asserts `parsed["Label"] == "com.agbridge"` at `:938` — one assertion
      more than "the path"
- [ ] ⚠️ `:644` `…omits_the_statedir_when_none_was_given` — handled in Task 2b (Decision 4)
- [ ] ⚠️ `:606` `…defaults_the_config_to_the_users_own_dotfile` — the "own dotfile" is now
      `~/.config/agbridge/<name>/config`. The property (the *default* path is exercised, not only the
      `--config` seam) survives; the path it names does not
- [ ] ⚠️ `:1238` `test_an_instance_adopts_the_macs_existing_mac_id` — seeds the default config with
      `run_sh(mac_args(**{"--config": None}))`, i.e. **through the installer**, which no longer
      creates one. Reseed by calling `agb install-config` directly (`tests/test_install_pkg.py:429`
      already does this, and it is the stronger reseed here — this test's subject is adoption from a
      *real* installer-written config); the `instance_config` **fixture** (`tests/conftest.py:502`,
      request it, do not call it as a module function — `name=None` writes the default path) is the
      lighter alternative. Say in the docstring why the installer can no longer be the seeder
- [ ] ⚠️ `:1442` `test_reinstalling_an_instance_keeps_the_mac_id_it_already_has` — same reseed
      (`:1457`), and it is the load-bearing pin of the "own config first" rule the new statedir
      adoption imitates. Its docstring should now say how the two rules differ (Task 2b's comment)
- [ ] ⚠️ `:1493` `test_a_default_install_is_the_plist_it_always_was_plus_the_config_flag` — asserts
      `"instance:" not in out` (`:1519`), which *was* the definition of a default install. Invert with
      the reasoning rewritten: there is no default install, so the banner is always printed. ⚠️ its
      plist filename (`:1505`), `ProgramArguments` config (`:1506-1508`) and `Label` (`:1509`) all
      move too — four changes, not one
- [ ] ⚠️ `:1522` `test_what_a_default_install_renders_leaves_the_bridge_where_it_was` — the only
      **end-to-end** exercise of invariant 14's first cross-file agreement (`install.sh`'s
      `DEFAULT_CONFIG` vs `agb.config_path()`); after the refusal there is no route to a default
      install at all. The string-level guard survives at `:2203`, so the invariant stays pinned —
      say in the plan and in the test which half is being given up
- [ ] ⚠️ `:2084` `test_an_absent_instance_is_the_default_one_even_when_the_probe_answers` — the worst
      one: its whole subject (the ⚠️ pin behind `install.sh:400-406`'s *"OPT-IN, and it can never
      become the default"*) is **deleted** by this plan. Invert into its successor — *an absent
      `--instance` is refused even when the probe answers* — which is a reasoning rewrite, not a
      fixture fix, and it must move in step with Task 2a's withdrawn-rule comment
- [ ] keep `PLIST_TEMPLATE` (`:55`) and the two shape oracles in `tests/test_agb_refresh.py`
      (`:2443`, `:2526`) pointing at `dist/com.agbridge.plist` — the template is label-agnostic
- [ ] **mutation-check** that the fixture's instance reaches the installer: drop `--instance` from the
      dict and confirm a **named** test fails, not merely "many tests"
- [ ] run tests — must pass before Task 4

### Task 4: Verify acceptance criteria

- [ ] a nameless `install.sh mac` is refused and writes nothing, in a throwaway `$HOME`
- [ ] `install.sh mac --instance auto` still names an instance after its feed host
- [ ] an upgrade of an existing instance succeeds **without** `--statedir` and keeps the old value
- [ ] a new instance without `--statedir` is still refused
- [ ] ⚠️ `--instance X --config <the default config>` without `--statedir` is still refused
- [ ] `install.sh farm` is unaffected
- [ ] the legacy paths still work: a hand-placed 0.5.0-era `com.agbridge.plist` is still listed by
      `agb instances` as `(default)`, still claimed by `bind_label_to_config`, still swept — the
      *"what this does NOT do"* section, asserted rather than assumed
- [ ] `agb` unchanged: `git diff -- agb` empty; character count re-measured against
      `tests/conftest.AGB_PARSE_BUDGET` (characters, not `wc -c`)
- [ ] full suite; `sh -n install.sh && sh -n agb-refresh`

### Task 5: [Final] Documentation

**Files:** `docs/commands.md`, `docs/cookbook.md`, `README.md`,
`.claude/skills/agbridge/SKILL.md`, `docs/design.md`, `agb-refresh`, `CLAUDE.md`, `CHANGELOG.md`

- [ ] `docs/commands.md`: the `install.sh mac --instance` section — the refusal, and the adoption rule
      with its "derived config only" reasoning; document `agb install-config --print-statedir` beside
      `--print-mac-id`, including that it is a **read-only query**
- [ ] `docs/cookbook.md`: the first-install recipe (`:51`) and the no-shared-disk recipe both gain
      `--instance`; grep for every `install.sh mac` without it
- [ ] ⚠️ `docs/design.md` §5: **amend the first of the "Three guards"** (`:1402` introduces them, the
      bullet is `:1404`), which states flatly *"`--instance` requires `--statedir`"* — now conditional.
      Check the §5 table at `:1370-1374` — all three rows (config, launchd label, **log dir**) — whose
      "default" column is no longer creatable. Claim only that **no new nameless instance is
      created by default**, not that symmetry is guaranteed (`--config $DEFAULT_CONFIG` still reaches
      it). Say plainly that the legacy readers stay, with why: a plist on disk outlives the installer
      that wrote it
- [ ] ⚠️ `agb-refresh`: **operator-facing** messages tell users to re-run `install.sh mac` with no
      instance — `:505`, `:912`, `:1085`, `:1189`, `:1510`, `:1735`, `:1789`, `:1792`. That advice is
      now a refusal. Leave the *comment* mentions alone (`:610`, `:781`, `:836`, `:1007`, `:1143`,
      `:1212`, `:1273`, `:1549`, `:1572`, `:1589`) — they describe legacy installs and stay accurate.
      ⚠️ No test pins that prose (`tests/test_agb_refresh.py:3678` only asserts the substring
      `"install.sh mac"`, which survives any rewrite), so this is a read-every-one item
- [ ] `README.md`: install examples; test count in **both** places (`:289` and `:301`, both already
      stale at 1877)
- [ ] `SKILL.md`: the install recipes and the refusals list
- [ ] `CLAUDE.md`: correct the test count in **both** places (`:8` and `:526` — already stale at 1877
      before this plan); invariant 12 or 14 only if something structural changed
- [ ] ⚠️ `CHANGELOG.md`: **`## Unreleased` already exists** (`:9`, carrying the `agb-claude` entry from
      `ba17783`) — add to it, do not create a second heading. Name the breaking change, point at the
      existing *Upgrading from ≤ 0.5.0* steps (`:440`) for anyone with an unnamed instance, and say
      what this does **not** do
- [ ] ⚠️ and name the consequence the plan currently only implies: **a legacy unnamed install has no
      in-place upgrade at all.** `--instance` is mandatory, and adopting the old file via
      `--config <the default path>` re-demands `--statedir` (Decision 1). That is the symptom line an
      operator needs, not a footnote under *Still open*
- [ ] ⚠️ **`VERSION` is NOT bumped by this plan.** It lives at `agb:24` — the only place it lives —
      and this plan's own constraint is that `agb` is not touched. A breaking CLI change does argue
      0.7.0, but "bump only, no tag, no release" already means the number decides nothing until a
      release does. So: the change lands under `## Unreleased` at 0.6.0, and the *release* that ships
      it picks the number. Record that here so nobody reads the omission as an oversight
- [ ] ⚠️ run the identifier sweep before committing:
      `git ls-files -z | xargs -0 grep -nEi 'nvidia|<your hosts>|<your user>|/home/<you>/'` — it
      caught three leaks during the parent plan, two of them written *while documenting the first*
- [ ] ⚠️ **gate this task too**: `sh -n install.sh && sh -n agb-refresh`; full suite. This is the task
      that rewrites eight `agb-refresh` messages in a POSIX-sh file, and it is the only one with no
      gate after it. `tests/test_agb_refresh.py:3678` asserts the substring `"install.sh mac"` — a
      rewrite phrasing it as "re-run the mac installer" **fails** that test, with nothing else to
      catch it
- [ ] move this plan to `docs/plans/completed/`

## Post-Completion

*Manual, and only the owner can do it*

- **Re-run `install.sh mac --instance <name> …` on the real Mac** for both instances, and confirm the
  upgrade path works without `--statedir` now that both configs carry one. ⚠️ Both were installed
  without `--config`, so both take the derived path and both adopt — but check the banner says
  `statedir: adopted …` rather than assuming it. This is the ergonomic change the plan exists to
  make, and the one thing no test observes.
- **Confirm nothing regressed for the already-migrated Mac**: `agb instances` still lists both, a bare
  `agb-refresh` still sweeps both.

**Still open after this, deliberately:**

- `agb doctor`, `agb status-line` and `prune --via-ssh` have no `--config` and resolve the default
  path unconditionally (design.md §5, limitation 3). With no default config on a fully-migrated Mac
  they describe a file that does not exist. Its own plan.
- `install.sh mac --instance X --config <the default path>` still writes the unnamed config, and now
  also still demands `--statedir`. Refusing it outright would forbid a legitimate shape; documented
  rather than closed.
- Renaming `dist/com.agbridge.plist` to something label-agnostic. Cosmetic; touches `install.sh:47`'s
  `TEMPLATE=` and `tests/test_install_pkg.py:55`.
