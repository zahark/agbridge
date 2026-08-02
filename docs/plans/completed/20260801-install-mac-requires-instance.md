# `install.sh mac` refuses an unnamed instance

> **Follow-up to `20260801-agb-symmetric-instances.md`**, split out of it deliberately rather than
> deferred by accident. That plan removed the *default instance's privilege* from every Mac-side
> command. This one removes the ability to create a nameless instance at all.
>
> **Planned 2026-08-02. Revision 6.** Revision 1's statedir mechanism did not exist; revision 2's
> replacement had two defects; revision 4's task split could not satisfy its own gate. Each was found
> by a review that *implemented* the plan in a scratch tree and measured. Everything marked
> ⚠️ **measured** below was executed against the code, not inferred, and the revision-3 numbers were
> re-measured independently and reproduced exactly.
>
> ⚠️ **Line citations were verified at `b393d8e`.** `install.sh`, `agb_ops` and
> `tests/test_install_pkg.py` are untouched by the `row_fields` work that landed in between, so their
> numbers hold; `docs/design.md` moved **+5**, `README.md` **+1** and `CHANGELOG.md` **+49**.
> Re-anchor by content, not by line, if the tree has moved again.

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

- **Repo**: this checkout, `main`, at 0.6.0 (released 2026-08-01). ⚠️ **measured at `b393d8e`: 1900
  tests** — it was 1884 when this plan was first written, and the `row_fields` feature landed in
  between. **Re-measure rather than quoting either number**; `README.md:290`/`:302` still say 1877 and
  are two releases stale.
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
- **`install.sh:396-418`** is the comment block behind `--instance auto`. ⚠️ Its `:401-402` — *"an
  ABSENT `--instance` has to keep meaning the default instance"* — **is falsified by this plan** and
  has to be marked withdrawn rather than deleted. ⚠️ `:400` (*"OPT-IN, and it can never become the
  default"*) **survives**: a bare install is refused, never auto-named. So does the second ⚠️
  paragraph at `:408-414`. Narrow the edit to the one sentence.
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
- ⚠️ **The `mac_args` rewrite is the risk, not the installer change.** ⚠️ **measured**: with the whole
  plan implemented and the fixture given shape (A), the failure set is **12 tests, all in
  `tests/test_install_pkg.py`** — identical to the set the fixture change alone produces. The
  installer change breaks nothing beyond the fixture. ⚠️ **But the refusal alone breaks 45**, which is
  why the fixture and the refusal are one task: see Task 2's table.
- ⚠️ **Harness blindness has been found four times in this codebase.** Most relevant here: a fixture
  that wrote a plist into *both* candidate directories, so the flag under test could not matter —
  inside the test written to catch that.

## Progress Tracking

- `[x]` immediately; ➕ for discoveries; ⚠️ for blockers.

## Solution Overview

Four changes: a read-back channel in `agb_ops`, three rules in `install.sh`, a fixture, and the docs.

**1. The `mac` role requires `--instance`** (Task 2). A hard error, not a warning — a warning on a
first install gets ignored and the asymmetry becomes permanent on that Mac. The message names both
`--instance <name>` and `--instance auto`, so the fix is one word. The `farm` role is untouched: a
farm host has exactly one identity and its config path is resolved on every `agb hook` (invariant 4).

**2. `--statedir` is adopted from the instance's *derived* config when that config carries one**
(Tasks 1 + 3). Narrower than revision 2 claimed — see Decision 1. It closes the re-typing hazard that
put a `feed_host` typo into one of the owner's instances, without reopening `:446`'s.

**3. The printed farm hint always carries `--statedir`** (Task 2, Decision 4). Its conditional becomes
dead the moment the flag stops being optional for the mac role.

**4. The `mac_args` fixture gains an instance** (Task 2 — *not* its own task; see Task 2's table). The fixture *is* the statement of what a
default test install looks like, and after this change that is a named one.

**The statedir work is separable, and the tasks are split along that seam.** Task 2 (the refusal, and
the test suite that states it) is the point of the plan and ⚠️ **measured** to leave a green tree on
its own; Task 1 + Task 3 is the ergonomic follow-on. If it grows, delete those two tasks and ship
Task 2 — recorded here, not done quietly. ⚠️ The seam is *not* between the refusal and the fixture:
revision 4 split it there and neither half could pass its own gate. **What leaves with Tasks 1 and 3**:
Task 4's *"an upgrade of an existing instance succeeds without `--statedir`"* and *"`--instance X
--config <the default config>` is still refused"*, and Task 5's `--print-statedir` documentation and
every mention of the adoption rule.

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

**The refusals go at the top of `role_mac`** (`:482-483`). **The adoption block goes between the
`instance:` banner (`:501-503`) and the dry/real branch (`:505`)** — after the header, still before
the first mutation at `:510`. Both constraints are satisfiable without splitting the `say`:

```sh
# --- at the top of role_mac, beside the two existing requirement checks ---
[ -n "$instance" ]   || die …   # Decision 3's message -- the refusal
[ -n "$feedhost" ]   || die …   # existing :482
[ -n "$remotepath" ] || die …   # existing :483

# --- after the `instance:` banner (:503), before the dry/real branch (:505) ---
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
- **Output ordering, decided here**: **below the `instance:` banner**, which is what the placement
  above buys — an operator reads "instance: hostb … / statedir: adopted …" as one statement about one
  instance, and a bare `statedir:` line before the header names an instance nothing has printed yet.
  ⚠️ **measured**: a `--dry-run` install with adoption prints **two** `verified:` lines (the adoption's
  against `$SELF/agb`, then `:507`'s against the same file) — and the first of them lands *between*
  the `instance:` banner and `statedir: adopted …`, so the adjacency this placement was chosen to buy
  is not actually achieved. Suppress the adoption's `verify_tree` output, or move its `say` after the
  block, or accept the interleave and say in the comment why there are two.

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
printed `install.sh farm …` hint.

⚠️ **It dies with the REFUSAL (Task 2), not with the adoption (Task 3)** — revision 4 attributed this
to the wrong task, and the mistake broke the plan's own fallback. `:446`'s
`[ -n "$statedir" ] || die` already fires for every instance *today*; the moment `--instance` is
mandatory, every mac install has an instance, so every one that reaches `:725` has a non-empty
`$statedir`. The farm role never builds that hint. ⚠️ **measured**:
`test_the_printed_farm_command_omits_the_statedir_when_none_was_given` fails under the refusal plus
the fixture alone, with **no** Task 1 and **no** Task 3.

**Make it unconditional and delete that test, in Task 2.** `tests/test_install_pkg.py:644` asserts a
property that no longer exists; keeping it alive by reaching `:725` some other way would be asserting
a shape the code cannot produce.

⚠️ **Its successor lands in Task 3, so the replacement spans two tasks** — that is not the plan
breaking its own "invert, do not delete" rule, it is the rule applied honestly to a test whose subject
one task removes and another task replaces. State it in both tasks so a reader of either does not see
a bare deletion.

⚠️ **And the successor is not "the hint always carries `--statedir`" — that is already `:626`'s
subject** (`test_the_printed_farm_command_carries_the_statedir_the_mac_recorded`, eighteen lines
above, asserting exactly `hint and "--statedir %s" % (sd,) in hint[0]` with the flag passed
explicitly). ⚠️ **measured**: `:644`'s own argv (`mac_args(**{"--statedir": None})`) is *refused*
after Task 2, so it cannot be reused either. **The successor's subject is the one Decision 1
created**: `_instance_args(mac_args, **{"--statedir": None})` — which drops `--config`, so
`config_given=no` and adoption is the only route to a non-empty `$statedir` — against a config seeded
at the derived path by the `instance_config` fixture (`tests/conftest.py:502`) carrying a statedir.
*The hint carries the **adopted** statedir*, a value that never appeared on the argv, which `:626`
cannot distinguish from a forwarded one. Non-vacuous by construction: without adoption the install is
refused and no `install.sh farm` line is printed at all.

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

- [x] add `"--print-statedir": "print_statedir"` to `CONFIG_FLAGS` (`agb_ops:3396`), to
      `parse_config_args`' defaults (`:3630`), and to its docstring usage line (`:3617-3620`)
- [x] in `run_install_config` (`:3667`), handle it **immediately after
      `existing, _malformed = agb.parse_config(text)`** (`:3686`) and **return** — before
      `install_config_values`, `merge_config_text` and `write_settings`. Write
      `existing.get("statedir")` alone to stdout, or raise `agb.AgbError` naming the file
- [x] comment the two reasons the placement is load-bearing, both measured: run after the write and a
      statedir-less config is *rewritten with the default config's statedir*; run after
      `install_config_values` and a config with no `mac_id` raises `MAC_ID_MISSING_NOTE` instead of
      answering, so non-zero would stop meaning "no own statedir"
- [x] comment that it prints the file's **own** value and never `agb.statedir()`'s fallback, naming
      the failure that would follow
- [x] refuse `--print-mac-id` and `--print-statedir` together: both own stdout, and a caller reading
      one line would silently get the other's
- [x] ⚠️ **refuse** a mutating option alongside it — decided here, not in the implementation.
      **measured**: today `install-config --config C --statedir /new --feed-host zzz --print-statedir`
      prints the *old* value, exits 0 and writes nothing. Correct for a pure query, but "you asked me
      to write and I silently did not" is invariant 12's family. **Allowed set: `--config` and
      `--dry-run`, nothing else** — `--config` is exempt because it names *which* file to read and is
      exactly what `install.sh` passes (Decision 2's snippet is `install-config --config "$config"
      --print-statedir`); `--dry-run` is a no-op for a query and refusing it would trap the obvious
      first guess
- [x] write tests for that refusal **parametrized over the parser's own tables**, not an enumerated
      list — `CONFIG_VALUE_ARGS` (`agb_ops:3386-3394`) has seven entries and `CONFIG_HOST_ARG` and
      `--generate-mac-id` add two more, so a hand-written list silently misses `--agb-remote-path`,
      `--remote-python` and `--jump-host`. Assert `--config` and `--dry-run` accepted, every other
      table entry refused
- [x] write tests: prints the own value; **exits non-zero with nothing on stdout** for a config with
      no statedir key, and for a config that does not exist
- [x] write a test with **both** files present holding **different** statedirs, asserting the
      instance's own is printed — so it cannot pass by coincidence
- [x] write a test that a config with a statedir and **no `mac_id`** still answers (important: the
      measured regression)
- [x] write a test that the config file is **byte-identical** after `--print-statedir` **without**
      `--dry-run`, in both the answering and the raising case
- [x] write tests: the two `--print-*` flags together are refused
- [x] **mutation-check**: move the read below `install_config_values`; confirm a **named** test fails;
      restore. Then: make the raise a `return ""`; confirm a **named** test fails; restore
      — ➕ all three done (a third moved the read to the tail beside `--print-mac-id`, failing
      `…leaves_the_config_byte_identical`). ⚠️ **The first attempt was VACUOUS and said so only by
      flipping between runs**: `agb_ops` is loaded by path through importlib, which caches bytecode
      and validates it on (source mtime in *whole seconds*, source size) — two of the three mutations
      only *move* text, so the size is identical and a same-second rewrite reuses the stale `.pyc`.
      **Any mutation-check in this repo must delete `__pycache__/agb_ops*.pyc` after writing.** Worth
      a `CLAUDE.md` "Testing conventions" bullet in Task 5
- [x] run tests — must pass before Task 2 — ⚠️ **re-measured: 1900 baseline → 1910** with the 10 new

### Task 2: `install.sh mac` requires `--instance`, and the suite says so

⚠️ **One task on purpose, against the plan's own "~5 checkboxes" bar.** Revision 4 split this into a
refusal task and a fixture task, and a review **measured** that the split cannot work: the refusal
alone leaves **45 failing tests**, and the only thing that repairs them is the `mac_args` change —
so no intermediate state is green and neither half can satisfy "run tests, must pass before the
next". The dependency runs *fixture → refusal*, not the other way, and the two are not independently
meaningful anyway: "what a default test install now is" is **defined by** the refusal.

⚠️ **measured**, so the implementer knows what green looks like at each step:

| tree | result |
|---|---|
| baseline | 1900 passed |
| refusal only | **45 failed** |
| fixture only (Decision 5 shape A) | 12 failed |
| refusal + fixture | **12 failed** — a strict subset of the 45, and the 12 named below |
| refusal + fixture + their repairs | green |

**This task stands alone.** If Tasks 1 and 3 are dropped (the plan's stated fallback), Task 2 ships
by itself and the suite is green — which was **not** true of revision 4's split.

**Files:**
- Modify: `install.sh`
- Modify: `tests/test_install_pkg.py`

*The installer:*

- [x] add the refusal at the top of `role_mac`, beside `--feed-host`/`--agb-remote-path` (`:482-483`)
      and **before any filesystem mutation** — Decision 3's message, single-quoted
- [x] leave the `farm` role untouched, with a comment saying why it is different
- [x] make `install.sh:725` unconditional (Decision 4) — it dies with the **refusal**, because `:446`
      already requires `--statedir` for every instance. Comment why `:618`, dead by the same argument,
      is deliberately left alone (nothing tests it; `:725` moves only because `:644` asserts its
      conditionality). ⚠️ `:757`/`:767` are `role_farm`'s and correctly stay conditional
- [x] ⚠️ mark the withdrawn rule at `install.sh:401-402` — *"an ABSENT `--instance` has to keep meaning
      the default instance"* is now false. **Keep the paragraph and mark that sentence withdrawn with
      its reason**, per the house rule the plan restates: a withdrawn reason that is deleted gets
      re-proposed. ⚠️ `:400`'s *"OPT-IN, and it can never become the default"* **survives** — a bare
      install is refused, never auto-named — but the same line starts the sentence *"Re-running
      `install.sh mac` with the original flags is the documented upgrade path"*, which is now false
      for a legacy nameless install (Task 5 says so outright). So: keep the OPT-IN clause, withdraw
      the upgrade-path clause with it. The block runs `:396-418`; the second ⚠️ paragraph
      (`:408-414`) stays true
- [x] update `usage()` (`install.sh:66`): the `mac` synopsis gains `--instance <name>`, and the
      `--instance` entry (`:77`) states that it is **required**. ⚠️ Task 3 edits the *same entry* for
      the adoption rule — this task owns "required", Task 3 owns "and its statedir may come from its
      own config". ⚠️ Nothing tests `usage()`'s prose, so this is the item most likely to be
      forgotten

*The fixture, and the 12 it moves (Decision 5 shape A):*

- [x] `"--instance": HOST` in `mac_args`' args dict, `--config`/`--log-dir` still pinned; rewrite the
      docstring to say what a default test install now **is** and that the derived label is the only
      thing that moves. ⚠️ The sentence about the pinned `--config` keeping Task 3's adoption branch
      from firing belongs in **Task 3** — written here it documents a feature that never lands if the
      fallback is taken
- [x] verify `_instance_args` (`:1151`), `_probing_args` (`:1852`) and `_auto_args` (`:1947`) still
      compose — all three override `--instance` or filter argv, none touches `--label`
- [x] **re-measure the failure set** and reconcile against the 12 below before changing any of them —
      a 13th means the installer change went further than intended
      — ➕ **re-measured at `fd08a39`** (post Task 1): baseline **1910 passed**; refusal only **45
      failed** (the plan's number, reproduced exactly); refusal + fixture **12 failed** — *the same
      twelve*, no thirteenth; refusal + fixture + repairs **1913 passed** (−1 deleted, +4 new)
- [x] **mechanical (5)** — `:926`, `:943`, `:976`, `:1073`, `:1112` assert `agents/com.agbridge.plist`,
      now `com.agbridge.box2.plist`. ⚠️ `:926` also asserts `parsed["Label"] == "com.agbridge"` at
      `:938` — one assertion more than "the path"
- [x] ⚠️ **audit the tests that keep PASSING and stop asserting** — the failure set cannot see these,
      and this is the plan's own named failure class (Testing Strategy). Five **negative** assertions
      name `agents/com.agbridge.plist` — `:1011`, `:1039`, `:1205`, `:1438`, `:1984` — and after shape
      (A) no successful install can produce that filename, so each goes quiet. Grep the file for
      `com.agbridge.plist` and re-point every one at the name the fixture now renders
- [x] ⚠️ two of those five are the sharp ones, and both are **labelled "Non-vacuity"** in the source:
      `:1205` and `:1984` assert *"the DEFAULT instance was not written"* as the isolation claim of an
      instance install. There is no default install to be isolated from any more, so re-anchor the
      claim (assert the **other** instance's plist is absent) rather than re-pointing the path. ⚠️
      `:1011` and `:1039` are refusal tests running a success-shaped `mac_args()`; their
      `glob("*.tmp.*") == []` companion keeps them green while the plist assertion says nothing
- [x] ⚠️ `:644` `…omits_the_statedir_when_none_was_given` — **delete** it (Decision 4); its successor
      is Task 3's, so the replacement spans two tasks. Say that here, or the deletion reads as a
      violation of "invert, do not delete"
- [x] ⚠️ `:606` `…defaults_the_config_to_the_users_own_dotfile` — the "own dotfile" is now
      `~/.config/agbridge/<name>/config`. The property (the *default* path is exercised, not only the
      `--config` seam) survives; the path it names does not
- [x] ⚠️ `:1238` `test_an_instance_adopts_the_macs_existing_mac_id` — seeds the default config with
      `run_sh(mac_args(**{"--config": None}))`, i.e. **through the installer**, which no longer
      creates one. Reseed by calling `agb install-config` directly (`tests/test_install_pkg.py:429`
      already does this, and it is the stronger reseed here — this test's subject is adoption from a
      *real* installer-written config); the `instance_config` **fixture** (`tests/conftest.py:502`,
      request it, do not call it as a module function — `name=None` writes the default path) is the
      lighter alternative. Say in the docstring why the installer can no longer be the seeder
- [x] ⚠️ `:1442` `test_reinstalling_an_instance_keeps_the_mac_id_it_already_has` — same reseed
      (`:1457`), and it is the load-bearing pin of the "own config first" rule Task 3's adoption
      imitates. Its docstring should say how the two rules differ (Task 3's comment)
- [x] ⚠️ `:1493` `test_a_default_install_is_the_plist_it_always_was_plus_the_config_flag` — asserts
      `"instance:" not in out` (`:1519`), which *was* the definition of a default install. Invert with
      the reasoning rewritten: there is no default install, so the banner is always printed. ⚠️ its
      plist filename (`:1505`), `ProgramArguments` config (`:1506-1508`) and `Label` (`:1509`) all
      move too — four changes, not one
- [x] ⚠️ `:1522` `test_what_a_default_install_renders_leaves_the_bridge_where_it_was` — the only
      **end-to-end** exercise of invariant 14's first cross-file agreement (`install.sh`'s
      `DEFAULT_CONFIG` vs `agb.config_path()`). ⚠️ Revision 5 said *"there is no route to a default
      install at all"* — **that is false**, and it would have given up coverage that is recoverable:
      `install.sh mac --instance X --config <the default path>` is still legal, and this plan
      documents it twice. **Repair it that way** (`mac_args(**{"--config": <default path>})`), which
      ⚠️ **measured** keeps it green with its non-vacuity block (`:1560-1569`) intact
- [x] ⚠️ **and record the coverage that is genuinely lost**: ⚠️ **measured**, after this task replacing
      `install.sh:452` (`[ -n "$config" ] || config="$DEFAULT_CONFIG"`) with a `die` causes **zero**
      failures in the whole suite. Every mac test derives its config from `--instance`; every farm test
      pins `--config` (`_farm`, `:2116`). That line survives only as the *string* compared at `:2203`.
      Either drop `--config` from one farm test to keep it exercised, or say plainly in `:2203`'s
      comment that the runtime use is now test-dead and the string guard is all there is
- [x] ⚠️ `:2084` `test_an_absent_instance_is_the_default_one_even_when_the_probe_answers` — the worst
      one: its whole subject (the ⚠️ pin behind `install.sh:401-402`) is **deleted** by this plan.
      Invert into its successor — ⚠️ but **not** *"refused even when the probe answers"*: **measured**,
      the refusal at the top of `role_mac` fires before `probe_farmhost` (`install.sh:546`), so
      `stub_bin.calls("ssh") == []` and the probe never answers. The assertable — and stronger —
      statement is *"the probe is never consulted, so no name can be invented"*. As worded otherwise
      the implementer either keeps `:2084`'s `assert "hostb01" in out` non-vacuity line and it fails,
      or drops it silently. A reasoning rewrite, not a fixture fix, moving in step with the
      withdrawn-rule comment above

*New tests, and the gate:*

- [x] write tests: a nameless `mac` install is refused, **writing nothing** — no config, no `$dest`,
      no `$agentsdir`, no `launchctl` call — asserted as absence, not exit code
- [x] write tests: `--instance auto` and an explicit `--instance <name>` both still work
- [x] write tests: `install.sh farm` still installs with no `--instance`
- [x] keep `PLIST_TEMPLATE` (`:55`) and the two shape oracles in `tests/test_agb_refresh.py`
      (`:2443`, `:2526`) pointing at `dist/com.agbridge.plist` — the template is label-agnostic
- [x] **mutation-check**: downgrade the refusal to a warning; let the farm role inherit the
      requirement; move the refusal below `mkdir -p "$dest"`; drop `--instance` from the fixture dict
      — each confirming a **named** test fails, not merely "many tests"
      — ➕ all four done, in-memory snapshot restored and sha256-verified, never `git checkout`:
      (a) → `test_a_nameless_mac_install_is_refused_and_writes_nothing`,
      (b) → `test_install_sh_farm_still_installs_with_no_instance`,
      (c) → `test_a_nameless_mac_install_is_refused_and_writes_nothing`,
      (d) → `test_the_rendered_plist_names_the_installed_agb`.
      ⚠️ (c) needs **two** edits — deleting the refusal from the top of `role_mac` as well as adding
      it after `mkdir -p "$dest"`, or the original still fires and the mutation is a no-op that reads
      as a pass
- [x] `sh -n install.sh`; run tests — must pass before Task 3

### Task 3: `install.sh mac` adopts its statedir from the derived config

Needs Task 1. Droppable with it (see *Solution Overview*).

**Files:**
- Modify: `install.sh`
- Modify: `tests/test_install_pkg.py`

- [x] record `config_given` at `:447` (initialised at `:318`, because `set -u`)
- [x] move the statedir `die` out of `:446` into `role_mac` per Decision 2; **leave `:438`'s
      `[ "$role" = mac ]` check exactly where it is**, and comment that the move is only safe because
      of it
- [x] adopt via `--print-statedir` against `$SELF/agb`, **only when `config_given = no`** (Decision 1),
      re-running `shell_safe` and `absolute` on the adopted value like `:603` does for the mac-id;
      place the block between the `instance:` banner (`:503`) and the dry/real branch (`:505`)
- [x] comment that this is **not** a symmetric mirror of the mac-id adoption: that one deliberately
      falls back to `$DEFAULT_CONFIG` because sharing an id is correct, and sharing a statedir is the
      exact failure `:446` exists to prevent
      — ➕ **OUTPUT ORDERING, the open choice Decision 2 handed the implementer: resolution (1),
      suppress the adoption's `verify_tree` output** (`verify_tree "$python" "$SELF/agb" >/dev/null`).
      Chosen over "accept the interleave" because accepting it forfeits the exact adjacency the
      placement was picked to buy, and over "move the `say`" because the `say` is already as close to
      the banner as it can get — the interleaving line was the *verify*, not the report. It costs
      nothing an operator needs: the surviving `verified:` line names the tree being **installed**,
      which is the claim that line makes, and a failure is still loud because `verify_tree` dies on
      stderr rather than reporting. ⚠️ The redirection is a *reporting* suppression only, so a future
      `say` added inside `verify_tree` would be swallowed here — said in the comment. ⚠️ **measured**
      on a `--dry-run` install with adoption: one `verified:` line, and `statedir: adopted …` sits
      directly under `instance: …`
- [x] update `usage()`'s `--statedir` entry and the second half of the `--instance` entry (`:77`)
      with the adoption rule — Task 2 owns the "required" half
- [x] ⚠️ fix `_instance_args`' docstring (`tests/test_install_pkg.py:1156-1158`), which says
      `--statedir` *"is **required** by `--instance`"* — false once this task lands
      — ➕ and the sentence Task 2 deferred here landed too: `mac_args`' docstring now says its pinned
      `--config` keeps the adoption from firing, so a test written against it directly is silently on
      the non-adopting branch
- [x] write tests: statedir adopted from the derived config; still **required** for a new instance;
      still **required** when the existing config has no statedir key; the adopted value is announced
      — ➕ `test_an_existing_instance_adopts_the_statedir_from_its_own_config` (non-vacuous by
      construction: without the adoption that argv is *refused*, so there is no exit-0 run to assert
      anything about), `test_an_instance_whose_own_config_carries_no_statedir_is_still_refused`, and
      the existing `…without_a_statedir_is_refused_and_installs_nothing` re-stated as the NEW-instance
      case with the adoption's companion named in its docstring
- [x] ⚠️ write the Decision 1 test: `--instance hostb --config <the DEFAULT config>` with no
      `--statedir` is **still refused**, with a default config present that carries one — the measured
      hole. ⚠️ **measured: the existing suite does not cover this** — dropping the `config_given`
      condition leaves the failure set unchanged, so without this test *and* its mutation-check the
      guard ships vacuous
      — ➕ `test_an_instance_pointed_at_another_config_still_needs_a_statedir`, whose second half puts
      the **same content** at the DERIVED path and asserts it *is* adopted. That is what makes the
      first half a statement about `--config` having been typed rather than about the file; the
      plan's warning is confirmed exactly — mutation (c) below fails this test and **only** this test
- [x] write a test that an adopted statedir is the instance's own, never the default's — both files
      present, different values
      — ➕ and a second one the mutation-check turned out to need:
      `test_a_new_instance_never_inherits_the_default_configs_statedir`. "Both files present" cannot
      see the symmetric-mirror mistake at all (the loop finds the instance's own first and stops), so
      the assertable case is a **new** instance with only the default config present. Its non-vacuity
      is that the *same run* reads that *same file* for the mac-id
- [x] write a test that a **refused** install runs no `agb` against `$dest` — the adoption reads
      `$SELF/agb`, and a test that let it read a copied tree would assert the wrong file
      — ➕ spelled as an ORDER assertion (`test_the_statedir_is_adopted_from_the_installers_own_tree`):
      `$dest/agb` does not exist until the `copied:` line, so `statedir: adopted` appearing *before*
      it is positive evidence about which file was read, where "`$dest` is absent after a refusal" is
      only evidence that nothing ran at all. Both are asserted, the second in the refusal tests
- [x] write `:644`'s successor per Decision 4 — subject: **the hint carries the adopted statedir**,
      via `_instance_args(mac_args, **{"--statedir": None})` against a config seeded at the derived
      path by the `instance_config` fixture. Not "the hint always carries one" — that is `:626`'s
      — ➕ `test_the_printed_farm_command_carries_the_adopted_statedir`; the deletion note left in
      Task 2 now names it, so neither half of the two-task replacement reads as a bare deletion
- [x] **mutation-check each guard separately**: let a new instance inherit a statedir; let the
      missing-key case pass; **drop the `config_given` condition**
      — ➕ all three done, in-memory snapshot restored and sha256-verified, never `git checkout`.
      Each anchor asserted unique and each mutated file re-read before running, so a no-op mutation
      cannot read as a pass:
      (a) fall back to `$DEFAULT_CONFIG` (the symmetric-mirror mistake) → **1 failure**,
      `test_a_new_instance_never_inherits_the_default_configs_statedir`;
      (b) delete the `[ -n "$statedir" ] || die` → 4 failures, including
      `test_an_instance_whose_own_config_carries_no_statedir_is_still_refused`;
      (c) drop `[ "$config_given" = no ]` → **1 failure**,
      `test_an_instance_pointed_at_another_config_still_needs_a_statedir`.
      (a) and (c) each hang on a single named test, which is the plan's measured warning reproduced:
      neither was covered before this task. ⚠️ The `__pycache__` hazard from Task 1 does **not** apply
      to these — `install.sh` is re-read by `/bin/sh` every run — but the driver drops the cache
      anyway, since each run shells out through importlib-loaded siblings
- [x] `sh -n install.sh`; run tests — must pass before Task 4 — ➕ **re-measured: 1913 baseline →
      1920** with the 7 new tests; `sh -n install.sh` clean; `git diff -- agb` empty


### Task 4: Verify acceptance criteria

⚠️ **All nine were EXERCISED end to end**, in throwaway `$HOME`s under a scratch directory, with
`launchctl`/`ssh`/`pgrep` as recording stubs on `$PATH` so nothing could reach a real launchd or the
network. The real `$HOME` was confirmed untouched afterwards (`~/Library/LaunchAgents` and
`~/.local/lib/agbridge` still absent; `~/.config/agbridge` and `~/.local/bin/agb` still at their
pre-session mtimes). Running on Linux cost nothing: `--no-load` and the `ssh` stub are the same
machinery `tests/test_install_pkg.py`'s `mac_args`/`_ssh_answering` use, and no criterion needed a
Mac. **Nothing below was skipped.**

- [x] a nameless `install.sh mac` is refused and writes nothing, in a throwaway `$HOME`
      — ➕ exit 1, the message naming both `--instance <name>` and `--instance auto`, and the
      throwaway `$HOME` still containing **exactly one path — itself** (`find | wc -l` == 1). Asserted
      as absence, not as an exit code
- [x] `install.sh mac --instance auto` still names an instance after its feed host
      — ➕ against a stub `ssh` answering `farmbox01`: one `ssh … 'hostname -s'` call, then
      `instance: auto -> farmbox01`, `~/.config/agbridge/farmbox01/config`,
      `~/Library/LaunchAgents/com.agbridge.farmbox01.plist` and
      `~/Library/Logs/agbridge/farmbox01/`. The rendered plist's `ProgramArguments` end
      `bridge --config <that instance's config>`
- [x] an upgrade of an existing instance succeeds **without** `--statedir` and keeps the old value
      — ➕ re-running against the instance criterion 2 created printed
      `statedir: adopted /shared/agb from …/farmbox01/config` directly under the `instance:` banner
      (the adjacency Task 3's `>/dev/null` was chosen to buy, confirmed in a **real** install, not
      only a `--dry-run`), exit 0, and the config still carries `statedir = /shared/agb`
- [x] a new instance without `--statedir` is still refused
      — ➕ `--instance brandnew` with no `--statedir`: exit 1, and the `$HOME` path count is
      **unchanged** (24 → 24) — no `~/.config/agbridge/brandnew`, no
      `com.agbridge.brandnew.plist`, no `~/Library/Logs/agbridge/brandnew`
- [x] ⚠️ `--instance X --config <the default config>` without `--statedir` is still refused
      — ➕ the measured hole, exercised as the plan describes it: a default config **present and
      carrying `statedir = /shared/DEFAULT`**, `--instance hostb --config <it>` and no `--statedir`
      → exit 1, and that config **byte-identical** afterwards (sha256 compared). Non-vacuity in the
      same run: the identical argv **with** `--statedir /shared/hostb` installs
- [x] `install.sh farm` is unaffected
      — ➕ a farm install into its own throwaway `$HOME` with no `--instance`: exit 0, config with
      `statedir`+`mac_id`, and all four hook events merged into `~/.claude/settings.json`. And the
      other half of the asymmetry re-confirmed: `install.sh farm --instance x` is still refused
- [x] the legacy paths still work: a hand-placed 0.5.0-era `com.agbridge.plist` is still listed by
      `agb instances` as `(default)`, still claimed by `bind_label_to_config`, still swept — the
      *"what this does NOT do"* section, asserted rather than assumed
      — ➕ **the artefact was not hand-written.** `git archive v0.5.0` was extracted to a scratch
      tree and **0.5.0's own `install.sh mac`** run with no `--instance`, which is the shape today's
      installer then refused in the same `$HOME`. Against that file, today's code: `agb instances`
      prints `(default)  com.agbridge  <default config>` and `--labels` prints `com.agbridge`;
      `agb-refresh --config <default config>` binds `label com.agbridge` **without** the
      *"no plist … names this config"* note — and the **negative control** (same command, plist moved
      away) prints that note, so the match was real and not the fallback; a **bare** `agb-refresh`
      swept it and actually forgot a binding (`bound aaaa1111 → ROW-1` in the map before,
      `forgot 1 of 1 binding` and an empty map after); `agb close-done` discovered it too.
      ➕ Also exercised the *pre*-0.5.0 branch the same section names: the same plist with the
      `--config` pair removed is still `(default)` and still claimed, which is
      `bind_label_to_config`'s *"a plist with no `--config` implies the default config"*.
      ➕ **Pinned permanently**, since the conjunction had no test: new
      `test_the_installed_tree_still_reads_a_plist_it_can_no_longer_write`
      (`tests/test_install_pkg.py`) renders the nameless plist from `dist/com.agbridge.plist` — the
      shipped template, so it cannot drift from what a 0.5.0 Mac has — and reads it back with the
      `agb` the installer **just installed**, asserting the name **column** is `(default)` and that
      `--labels` lists it. **Mutation-checked**: `instance_display_name`'s `or "(default)"` removed
      (the original live bug) fails that named test; in-memory snapshot restored and sha256-verified,
      `__pycache__` dropped either side per Task 1's hazard.
      The three sub-claims each also keep their existing separate tests
      (`test_instances_listing_names_the_default_instance`,
      `test_a_plist_from_before_the_flag_still_claims_the_default_map`,
      `test_a_bare_run_sweeps_every_instance_in_order`)
- [x] `agb` unchanged: `git diff -- agb` empty; character count re-measured against
      `tests/conftest.AGB_PARSE_BUDGET` (characters, not `wc -c`)
      — ➕ `git diff -- agb`, `git diff 598d184..HEAD -- agb` and `git diff v0.6.0..HEAD -- agb` all
      empty. Re-measured: **103,198 characters** (103,212 `wc -c` bytes — the wrong number) against
      `AGB_PARSE_BUDGET = 103200` with a strict `<`, so **1 character** of headroom, unmoved
- [x] full suite; `sh -n install.sh && sh -n agb-refresh`
      — ➕ **1921 passed** (1920 before this task's one new test), 72.9 s; both `sh -n` clean

### Task 5: [Final] Documentation

**Files:** `docs/commands.md`, `docs/cookbook.md`, `README.md`,
`.claude/skills/agbridge/SKILL.md`, `docs/design.md`, `agb-refresh`, `CLAUDE.md`, `CHANGELOG.md`

- [x] `docs/commands.md`: the `install.sh mac --instance` section — the refusal, and the adoption rule
      with its "derived config only" reasoning; document `agb install-config --print-statedir` beside
      `--print-mac-id`, including that it is a **read-only query**
- [x] `docs/cookbook.md`: the first-install recipe (`:51`) and the no-shared-disk recipe both gain
      `--instance`; grep for every `install.sh mac` without it
- [x] ⚠️ `docs/design.md` §5: **amend the first of the "Three guards"** — intro `:1407`, bullet `:1409`
      at `b393d8e` (this file drifted +5; anchor on the text *"Three guards exist"*), which states
      flatly *"`--instance` requires `--statedir`"* — now conditional. Check the §5 table just above
      it — all three rows (config, launchd label, **log dir**) — whose "default" column is no longer
      creatable. Claim only that **no new nameless instance is
      created by default**, not that symmetry is guaranteed (`--config $DEFAULT_CONFIG` still reaches
      it). Say plainly that the legacy readers stay, with why: a plist on disk outlives the installer
      that wrote it
      — ➕ the §5 table's header is now `without --instance` / `--instance hostb`, with a ⚠️ under it
      saying the left column is what a **pre-0.6.0** Mac has on disk rather than something a run can
      still produce, and naming the four legacy readers that stay. The first guard now reads
      *"requires `--statedir`, unless that instance's own config already carries one"*, with the
      `config_given` condition and the not-a-mirror-of-`mac_id` note as its two sub-paragraphs
- [x] ⚠️ `agb-refresh`: **operator-facing** messages tell users to re-run `install.sh mac` with no
      instance — `:505`, `:912`, `:1085`, `:1189`, `:1510`, `:1735`, `:1789`, `:1792`. That advice is
      now a refusal. Leave the *comment* mentions alone (`:610`, `:781`, `:836`, `:1007`, `:1143`,
      `:1212`, `:1273`, `:1549`, `:1572`, `:1589`) — they describe legacy installs and stay accurate.
      ⚠️ No test pins that prose (`tests/test_agb_refresh.py:3678` only asserts the substring
      `"install.sh mac"`, which survives any rewrite), so this is a read-every-one item
      — ➕ **7 of the 8 rewritten, all re-anchored by content** (the file had moved +5). `:505`,
      `:912`, `:1085`, `:1189`, `:1735`, `:1789` gained `--instance <name>`; `:1792` — the plist
      with **no `--config`**, i.e. a legacy nameless install — got the real rewrite, because a
      re-run there does not repair that job, it **mints a second instance beside it**. Its comment
      says so. ⚠️ **`:1510` was read and deliberately LEFT**: it is not advice, it is a causal list
      (`install.sh mac --no-load` ... *all leave one* untagged bridge) and it stays accurate for
      exactly the legacy installs it describes -- adding `--instance` there would narrow a true
      claim into a false one. All ten comment mentions untouched. ⚠️ **Four assertions pin this
      prose, not the one the plan named**: `install.sh mac` (`:3678`), `no --config in` (`:1439`,
      `:1963`), `could not be read, so the wait below matches ANY` (`:2425`) and
      `could not start com.agbridge.hostb` / `no bridge was started again for: ...` (`:4442`) --
      every one preserved verbatim; `sh -n agb-refresh` clean
- [x] `README.md`: install examples; test count in **both** places (`:290` and `:302` at `b393d8e`,
      both stale at 1877 — **re-measure**, do not copy a number from this plan)
      — ➕ ⚠️ **re-measured 1921**, not the plan's 1900 nor the file's 1877. Both README places and
      both CLAUDE.md places updated. The install example gained `--instance` **and `--statedir`**:
      the transitive cost, measured live rather than inferred -- a FIRST Mac install now needs a
      statedir too, since every install is an instance install
- [x] `SKILL.md`: the install recipes and the refusals list
- [x] `CLAUDE.md`: correct the test count in **both** places (`:8` and `:549` at `b393d8e`, both now
      1900 — **re-measure**); invariant 12 or 14 only if something structural changed
- [x] ⚠️ `CHANGELOG.md`: **`## Unreleased` already exists** (`:9`, carrying the `agb-claude` entry from
      `ba17783`) — add to it, do not create a second heading. Name the breaking change, point at the
      existing *Upgrading from ≤ 0.5.0* steps (`:489` at `b393d8e`; this file drifted +49) for anyone with an unnamed instance, and say
      what this does **not** do
- [x] ⚠️ and name the consequence the plan currently only implies: **a legacy unnamed install has no
      in-place upgrade at all.** `--instance` is mandatory, and adopting the old file via
      `--config <the default path>` re-demands `--statedir` (Decision 1). That is the symptom line an
      operator needs, not a footnote under *Still open*
- [x] ⚠️ **`VERSION` is NOT bumped by this plan.** It lives at `agb:24` — the only place it lives —
      and this plan's own constraint is that `agb` is not touched. A breaking CLI change does argue
      0.7.0, but "bump only, no tag, no release" already means the number decides nothing until a
      release does. So: the change lands under `## Unreleased` at 0.6.0, and the *release* that ships
      it picks the number. Record that here so nobody reads the omission as an oversight
- [x] ⚠️ run the identifier sweep before committing:
      `git ls-files -z | xargs -0 grep -nEi 'nvidia|<your hosts>|<your user>|/home/<you>/'` — it
      caught three leaks during the parent plan, two of them written *while documenting the first*
      — ➕ run, and **nothing this task wrote** matches (the same pattern over `git diff`: clean).
      ⚠️ It did surface **pre-existing** leaks from the `row_fields` commits (`b2f4bf4`, `75ab873`),
      confirmed by `git log -S`: a real internal hostname `dev01-container-xterm-032`
      (`docs/cookbook.md` and a completed plan), `/home/user/...` paths, and the internal project
      names `data_pipeline_v2` / `api_gateway_svc` / `api_tests` -- across `CHANGELOG.md`,
      `docs/commands.md`, `docs/cookbook.md`, `docs/agtermctl.md`, `agb_mac`,
      `tests/test_bridge_rows.py` and `docs/plans/completed/20260802-agb-row-fields.md`.
      **Reported, not fixed**: they predate this plan, span files it does not own, and the cookbook
      ones sit inside character-count worked examples a rename would falsify. Wants its own commit
- [x] ⚠️ **gate this task too**: `sh -n install.sh && sh -n agb-refresh`; full suite. This is the task
      that rewrites eight `agb-refresh` messages in a POSIX-sh file, and it is the only one with no
      gate after it. `tests/test_agb_refresh.py:3678` asserts the substring `"install.sh mac"` — a
      rewrite phrasing it as "re-run the mac installer" **fails** that test, with nothing else to
      catch it
      — ➕ `sh -n install.sh` and `sh -n agb-refresh` both clean; **1921 passed**, unchanged from the
      Task 4 baseline, which is the expected result for a documentation-only task; `git diff -- agb`
      empty
- [x] move this plan to `docs/plans/completed/`
      — ➕ `git mv`, so history follows. ⚠️ The progress log was copied to
      `docs/plans/completed/20260801-install-mac-requires-instance-progress.txt` as asked -- but
      note this **establishes** that convention rather than following it: no other plan in
      `docs/plans/completed/` has a progress file beside it

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
