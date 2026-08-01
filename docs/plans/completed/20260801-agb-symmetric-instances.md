# Symmetric instances: no default, and no privileged config

> **Revision 6, 2026-08-01.** Five review rounds. R2 killed a record format that corrupted on empty
> fields; R3 replaced it with four query modes; **R4 shrank it again** — keep `bind_label_to_config`
> exactly as it is and replace **only the reader**; R5 wrote down the contracts that simplification
> left implicit; **R6 made the three decisions the plan kept saying must be made** — the probe's
> known answer, `--labels`' membership predicate, and what `--key` means — and cleaned up three
> bullets that had gone stale against R5's own decisions inside the same revision.
>
> Round 5 verified the architecture and could not break it: the reader swap, the four contracts, the
> `agb_mac` placement and the `--rows` narrowing all survived independent checking, and its
> 19-test bare-run list was confirmed line for line. What it blocked on was under-specification, not
> design — which is what R6 fixes.
>
> Marked **⚠️ R4/R5/R6** where the previous revision was wrong. The installer change is **split into a
> follow-up plan**. The plan is smaller than revision 1; each round's findings are in this file's git
> history.

## Overview

Remove the "default instance" privilege from agbridge's Mac side. Today `agb-refresh` /
`agb close-done` / `agb forget-rows` with no flag act on the **unnamed** instance and report success
in the same words they would use for the one you meant. There is no way to act on all of them.

That default is an artifact of install order, not of the domain. In the user's words:

> All remote-hosts have same priority. Anyone can die, closed manually, killed reopened etc.

| invocation | acts on |
|---|---|
| `agb instances` | lists what exists — a question you cannot ask today |
| `agb-refresh` | **every** instance |
| `agb close-done` | **every** instance |
| `agb forget-rows` | **refused** — names `--all` |
| `agb forget-rows --all` | every instance |
| any of them, `--instance` / `--config` / `--label` | that one |

**Why `forget-rows` differs.** Bare `agb-refresh` already closes every row it forgets (`--no-close`
is only passed when asked, `agb-refresh:1045`). The real difference is what happens next: refresh
**restarts the bridge**, which re-mints those rows within seconds; `forget-rows` alone leaves them
closed. So the sweep that ends in a restart may default to all; the one that does not, may not.
**Consequence**: an instance left without a running bridge is an **error**, not a warning — that is
what makes the distinction true.

Fixes `docs/design.md` §5 limitation 1 and mitigates limitation 6 (maps stay per-instance; only the
commands visit all of them).

### Split out into a follow-up plan

`install.sh mac` refusing without `--instance` is **not in this plan**. It does not fix the stated
problem — the sweeps do — and it reaches 24+ test functions through `tests/test_install_pkg.py`'s
`mac_args` fixture (which hardcodes `["mac", "--no-load", "--no-probe"]` at `:498`), changes what
`dist/com.agbridge.plist` stands for, and forces an unresolved transitive `--statedir` decision
(`--instance` requires it at `install.sh:446`, so mandating `--instance` mandates `--statedir` on
every Mac install *and upgrade*). Until it lands, symmetry is a convention rather than a guarantee —
worth saying in the CHANGELOG.

## Context (from discovery)

- **Repo**: this checkout, `main`, HEAD `5571337` (⚠️ R6 — two commits since R5 added `agb-ralphex` and `agb-host-line`; `agb`, `agb_mac`, `agb-refresh` and the tests are untouched, so no cited line moved). No worktree. 1798 tests, ~52 s.
  VERSION 0.5.0.
- **The budget is CHARACTERS.** `tests/test_mac_split.py:382` asserts
  `len(agb_source) < AGB_PARSE_BUDGET`, so 102,499 is the largest passing size. `agb` is **102,419**
  — headroom **80**.
- **Scope is the Mac side only.** A farm host's config cannot move: `agb hook` resolves it on every
  tool call (invariant 4). `agb bridge` and `agb pane` untouched.
- Python **3.6.8** floor; POSIX sh, `sh -n` must pass.

## Development Approach

- ⚠️ **DO NOT PUSH. Local commits only.** Nothing in this plan may run `git push`, create a tag, cut
  a release, or touch GitHub in any way — not in a task, not in finalize, not in the documentation
  task. The owner reviews locally and pushes by hand, so every commit must stay revertable with a
  plain `git reset`. If a task seems to need a push to be verifiable, it does not: say so and stop.
- **Regular** (code then tests). Complete each task fully; all tests pass before the next.
- **⚠️ R4 — each task inverts the tests IT breaks.** R3 deferred them to a sweep-up task, which made
  every "run tests — must pass" gate unsatisfiable. Named inversions now live in the task that
  causes them.
- Two deliberate breaking changes, each with a CHANGELOG note: the meaning of a bare row-map command,
  and `forget-rows` requiring `--all`.

### The project bar

- **One mutation-check per guard**, not per task: break it, confirm a **named** test fails, restore.
- **`ast` for source structure; `plistlib` for rendered artefacts** — the existing correct model is
  `tests/test_agb_refresh.py:2328-2353`.
- **Assert non-vacuity** on every structural guard, including cross-file-agreement pins (which must
  not be substring greps on either side).
- **Never fabricate a pid**; **always pass `timeout=`**.
- **CHANGELOG in the same commit**, naming the symptom.

## Solution Overview

### ⚠️ R4 — replace the reader, keep everything built on it

R3 moved all five ranks into Python behind `--for-config`. That was doing far more than the problem
required: **the ranking in shell was never what broke — the plist parsing was.** Worse, it
reintroduced the very bug this plan exists to remove, three ways:

- **`same_map` would be re-implemented in Python** — the comparison invariant 12 documents *five*
  separate failures of. And the port is not mechanical: `config_map_dir:609-622` is fail-closed via
  `cd -P … && pwd -P`, so a directory that does not exist matches **nothing**
  (`docs/commands.md:727`, `docs/design.md:1527`); `os.path.realpath` never fails and would match it.
  Silent widening — the direction that bounces the wrong job.
- **Rank 5 compares against `${rows:-$config}`** (`agb-refresh:812`), where `$rows` is
  `agb-refresh`'s own flag. A one-input `--for-config` matches it against the wrong file under
  `agb-refresh --config X --rows Y`.
- **The multi-claimant warning dies** (`agb-refresh:873-882`, pinned at
  `tests/test_agb_refresh.py:1194`). `docs/design.md:1487-1496` calls that count the thing whose
  absence "made the wrong-job bounce invisible".

So: **`bind_label_to_config` is untouched** — its loop, `same_map`, the rank table, `nclaim`, the
claimant warning, the `${rows:-$config}` input, its twelve named tests. Only `plist_arg` changes.

| mode | prints | replaces |
|---|---|---|
| `agb instances` | human-readable listing | *new* |
| `agb instances --labels` | one label per line | *new* — feeds the sweep |
| `agb instances --plist <path> --arg <flag>` | that flag's value | `plist_arg`, all three call sites |
| `agb instances --probe` | the literal `instances-ok` | *new* — see contract 4 |

⚠️ **R5 — `--arg` takes a plist PATH, not "a path or a label".** All three call sites hold paths
(`$candidate` at `:747`/`:803`, `$plist = "$agentsdir/$label.plist"` at `:992`), so the label form had
no caller and was the sole source of a path-vs-label ambiguity. Two single-value flags also keep the
`*_VALUE_ARGS` convention that all eleven existing tables follow; a two-value flag has no meaning for
`--opt=` inline handling.

### ⚠️ R5/R6 — four contracts the simplification left unwritten

**1. `--arg` writes UTF-8 BYTES to `sys.stdout.buffer`, never `sys.stdout.write`.** Today's reader
does this deliberately (`agb-refresh:508-516`), with the reason in the source: `-E` does not touch
`LC_ALL`, so under ISO-8859-1 a non-ASCII path "comes out MANGLED — a path that exists nowhere, which
is the unsafe direction, silently", carried into the banner, the `pgrep` pattern and `forget-rows`.
**No file in `agb`/`agb_mac`/`agb_ops` uses `stdout.buffer` today**, so `run_instances` is the first
and it will not happen by accident. `tests/test_agb_refresh.py:3428-3450` is parametrised over three
locales and must keep its coverage after the fixture move.

**2. `--labels` needs its own status contract.** `--arg` inherits 0/2/3/other; `--labels` had
nothing, so "there are no instances" and "I could not list them" (an unreadable LaunchAgents
directory, an `agb` that returned 3, a broken reader) were the same answer. That is invariant 12
verbatim — "I could not answer" collapsing into "the answer is nothing" — and it would make a Mac
with a momentarily unreadable directory sweep nothing, fall back to the default job and report
success. **The not-answered case is fatal at the caller**, through `plist_read_ok` or a sibling.

⚠️ **R6 — the errno split, spelled out.** A **missing** LaunchAgents directory is the ordinary Mac
that Task 3 insists must keep working: **`ENOENT` → status 0, empty output, "no instances"**. **Every
other errno → "could not list", fatal.** This is not optional detail — `CLAUDE.md`'s own hard-won
fact is that `os.path.isdir`/`exists` swallow *every* stat errno, and reporting a broken filesystem
as "does not exist yet" has already shipped here once.

**3. ⚠️ R6 — `--labels`' membership predicate, decided.** It is a DIFFERENT question from
`bind_label_to_config`'s guard: `install.sh:369` accepts `--label <name>` with no shape rule, so
`weird.label.plist` is a real install (`tests/test_agb_refresh.py:458` builds one), while the
`com.agbridge` label-space guard at `agb-refresh:765` is a *claimant* rule and correctly narrower.
R5 required this "stated in both places" and never stated it, which left an implementer to invent
one — and every ad-hoc predicate in this area has cost a review round.

> A plist is an agbridge instance for `--labels` iff **its label is in the `com.agbridge` space**,
> **or** its `ProgramArguments` contains the command word `bridge` immediately after an element whose
> **basename is `agb`**.

⚠️ **"basename is `agb`" — ANY tree, not realpath-equal to this one.** A plist naming an `agb` in a
different tree is deliberately supported (`agb-refresh:380-386`), so requiring identity with the
running `agb` would silently drop such an instance from every sweep. The looser half is the safe
direction here: over-listing costs a bounded refresh of something that turns out not to be ours,
under-listing is an instance nobody sweeps.

**4. ⚠️ R6 — the probe has a literal known answer: `agb instances --probe` prints `instances-ok`.**
R5 said "a known-answer probe of `agb instances` itself" without saying what question has a known
answer, and **none of the obvious spellings works**. The probe's whole job is to tell an **old
installed `agb`** from a new one: `agb:2726-2728` answers `unknown command` with exit **2**, `USAGE`
on **stderr** and empty stdout — which is byte-identical to what
`agb instances --plist /nonexistent --arg --config` would give from a *new* `agb`. And status alone
cannot see `--python /bin/echo` (`tests/test_agb_refresh.py:2107`), which is why it must be
stdout-compared; but no other mode has fixed output — `--labels` depends on the LaunchAgents
directory and the human listing has no spec.

So a dedicated mode with a literal answer, the direct analogue of the `plistlib-ok` probe it
replaces. Accepted only on **exit 0 AND stdout exactly `instances-ok`**. Everything about the exit-2
disambiguation rests on this: get it wrong and a checkout `agb-refresh` against an installed 0.5.0
`agb` reads every plist as "says nothing" → convention fall-back → `docs/design.md:1919`'s "success,
twice, on the wrong instance".

⚠️ Correcting an R3 claim: the ranks do **not** gain "real tests instead of shell mutation checks".
They already have named end-to-end tests (`tests/test_agb_refresh.py:788`, `:1104`, `:1138`,
`:1194`). Keeping them where they are keeps that coverage.

### ⚠️ R4 — the status contract is inherited, not invented

`--arg` keeps `plist_arg`'s existing, already-tested contract: **0** answered (a value, possibly
empty), **2** this file says nothing, **3** the tree cannot answer, anything else the reader failed.
Both consumers keep their own reading of an empty value, **which differ on purpose** and which R3
would have collapsed:

| consumer | flagless plist means |
|---|---|
| `bind_label_to_config:766-769` | `$DEFAULT_CONFIG` — its bridge resolves `agb.config_path()` |
| the named-instance path `:1002-1008` | the **convention**, `$DEFAULT_CONFIG_DIR/$instance/config` |

R3 handed one table to both. That would make `agb-refresh --instance hostb` repair
`~/.config/agbridge/config` — limitation 1 restored under a new name.

⚠️ **The exit-code collision, and its fix.** `agb:2733-2735` already returns **2** for
`unknown command`. A checkout `agb-refresh` against an installed 0.5.0 `agb` would get 2 and read it
as "that plist says nothing" → convention fall-back → `docs/design.md:1916`'s "success, twice, on
the wrong instance". Fix: **probe once, up front**, that `agb instances` exists — beside the
`plistlib-ok` known-answer probe at `agb-refresh:934-940` — and fail with a message naming
`install.sh mac`. After that probe, 2 unambiguously means the plist.

### The sweep re-execs on `--label`

`agb-refresh` is 1,614 lines of `set -eu` with global per-run state and `die` error paths; an
in-process loop would leak state and any `die` would exit mid-sweep with jobs booted out. So it
**re-execs `"$0"` once per label**, aggregating exit codes. Labels, not names: `instance_ok` refuses
`.` and `--instance` *computes* `label="$DEFAULT_LABEL.$instance"` (`:945`), so the default label, a
custom `--label` install and a `--config`-only install have no name to pass.

Rejected discovery designs, recorded: **config-directory globbing** misses `--config /elsewhere` and
counts leftover directories; **a registry file** is another cross-file agreement, and invariant 14
documents three that each caused a bug.

## Implementation Steps

### Task 1: `agb instances`

**Files:** `agb` (dispatch + `USAGE`), `agb_ops` or `agb_mac`, `tests/conftest.py` (only if raising),
`tests/test_bridge_rows.py`, `tests/test_install_pkg.py`

⚠️ **R5 — the module is DECIDED, not deferred: `agb_mac`, with a dedicated dispatch arm and a budget
raise.** "Try the cheap route first" was not a decision, because the cheap route is the one that
breaks an architecture claim. `OPS_COMMANDS` (`agb:2660`) fits the 80-character headroom only by
avoiding a dispatch arm — and then Task 4 needs `agb_mac` → `agb_ops`, an edge that exists nowhere
today (`agb_mac` never names `agb_ops`; `agb_ops` never loads `agb_mac`, asserted in prose at
`tests/test_identity.py:1000`). `run_instances` belongs beside `close-done`/`forget-rows`, which is
`agb_mac`; that costs ~123 characters against 80, so the budget is raised — which is the decision
already taken, now for the right reason.

- [x] raise `AGB_PARSE_BUDGET` on a measurement of the **final** delta (arm + `USAGE`, ≥5
      `compile()` runs, in the form the previous raise used), and record why the `OPS_COMMANDS`
      route was rejected
- [x] fix `tests/conftest.py:44`'s "in bytes" → "in characters"
- [x] ⚠️ **`--arg` writes `sys.stdout.buffer` UTF-8 bytes** — see contract 1; nothing else in the
      three files does this, so it will not happen by accident
- [x] ⚠️ **R6 — the exit-3 catch belongs in `agb`'s dispatch arm, not in `run_instances`.** R5's
      bullet was impossible once `run_instances` lives *in* `agb_mac`: the sibling load happens in
      `agb`'s `_load_mac()` (`agb:2632-2633`), so a `cmd_instances` arm has to catch it. Not
      optional — `tests/test_agb_refresh.py:3363` asserts `(3, "")` and `_read_with`'s
      `assert err == b""` (`:3321`) forbids the traceback an uncaught raise produces
- [x] ⚠️ **R6 — expect several hundred characters, not ~123.** That estimate assumed a bare inline
      arm; a `cmd_instances` with a try/except and this repo's mandatory reason-comment is
      realistically 250–400. The "measure the final delta" instruction below saves it, but the
      number will surprise
- [x] implement `--plist <path> --arg <flag>` with `plist_arg`'s existing 0/2/3/other contract
- [x] implement `--labels` with **its own status contract** (contract 2) and **its own membership
      rule** (contract 3), both stated in the source beside the code
- [x] implement the default human listing: **one row per instance, `name  label  config`, reusing
      `--labels`' membership rule and error policy** — R5 left it as four words, so its columns, its
      behaviour on an unreadable plist and its exit codes were all invented at the keyboard. It is
      the only mode the stated problem does not require; if the budget bites, this is what defers
- [x] accept `--launch-agents <dir>`, per the `*_FLAGS` / `*_VALUE_ARGS` convention
- [x] ⚠️ pin `~/Library/LaunchAgents` and the `com.agbridge` label space as a **cross-file
      agreement** (`install.sh`, `agb-refresh:35`, now Python) beside the existing two in
      `tests/test_install_pkg.py` — patterns compared, never substring greps
- [x] tests for each mode; each status of **both** contracts; `--launch-agents`; a path with a space;
      a non-agbridge plist absent from `--labels`; a **custom-`--label`** install present in it
- [x] ⚠️ a **three-locale** test for `--arg`'s byte output (`C`, `POSIX`, `en_US.ISO-8859-1`),
      modelled on `tests/test_agb_refresh.py:3428-3450` — this is the guard for contract 1
- [x] rendered-plist guard using **`plistlib`**, modelled on `tests/test_agb_refresh.py:2328-2353`,
      non-vacuity asserted
- [x] **mutation-check each guard separately**
- [x] run tests

### Task 2: `agb-refresh` swaps its reader

**Files:** `agb-refresh`, `tests/test_agb_refresh.py`

- [x] ⚠️ **first**, reshape the `refresh` fixture's fake interpreter (`:166-172`, routes only `-c`):
      discriminate on `$4 = instances`, leave `forget-rows` stubbed, and say whether the routed call
      is still logged — `refresh.calls()` ordering is asserted at `:337`, `:353-357`
      → **not logged**, same as the `-c` reader was, and the reason is written into the fixture: a
      plist read is not one of the three steps, and logging it turns `assert refresh.calls() == []`
      into an assertion about how many plists were in the directory. Mutation-checked (logging it
      before the `exec` fails 7 named tests; after the `exec` is unreachable and proves nothing)
- [x] add a **negative control** shaped like `_lib_with(edit=…)` (`:3344-3346`)
      → `test_the_fake_interpreter_really_runs_the_tree_the_flag_names`, end to end through the
      script: the blinded `agb_mac` falls to the default label, the unedited copy finds `hostb`
- [x] ⚠️ **R5 — ONE probe, not two.** Replace the `plistlib-ok` probe (`:934-940`) with a
      known-answer probe of `agb instances` itself: after the swap this script runs no other `-c`
      program (`plistlib` is imported by `agb`, not by the shell), and a stdout-compared probe of the
      real command subsumes it — it still catches `--python /bin/echo`
      (`tests/test_agb_refresh.py:2107`) and `/bin/false` (`:2084`) **and** proves the tree, which is
      what makes exit 2 unambiguous afterwards. Fail with a message naming `install.sh mac`
- [x] ⚠️ **R6 — `test_the_embedded_reader_is_ascii_and_apostrophe_free` (`:3398`) is DELETED, not
      repointed.** R5 said both "one probe, not two" and "repoint `:3398` at the `plistlib-ok`
      probe" — contradictory, since the ONE-probe decision removes that program. After the swap
      `agb-refresh` embeds no `-c` program at all, so the guard has no subject: its non-vacuity
      assertions (`"plistlib" in program`, `"parse_bridge_args" in program`, `len(program) > 500`)
      cannot hold for anything that remains. Delete it **with its reasoning moved into the
      `run_instances` docstring**, since the ASCII/bytes rule it enforced is contract 1 and still
      applies — just to Python now, not to an embedded string
- [x] ⚠️ update `plist_read_ok`'s message (`:571-575`, *"cannot read a plist: $python exited $1 …
      pass `--python`"*): after the swap exit **1** is what `agb` returns for an `AgbError` and for
      any uncaught exception in `run_instances` (`agb:2733-2736` catches only `AgbError`) — neither
      is a statement about `$python`. Either make `run_instances` catch as broadly as `:453-464`
      does today so 1 is unreachable, or name the right file
- [x] add a **negative control for the probe itself**, not only for the reader
      → two: `test_an_agb_too_old_to_have_instances_is_refused_at_the_probe` (the dispatch arm cut
      out of a copy of `agb`, asserting exit 2 + empty stdout from `agb` itself first) and
      `test_the_probe_is_answer_compared_and_not_status_compared` (a tree that exits 0 saying
      something else, with an unedited copy as the paired control)
- [x] replace `plist_arg`'s body with an `agb instances --arg` call — **all three call sites**
      (`:747` config, `:803` rows, `:992` the named instance's own plist) keep their current callers
      and their current readings of 0/2/3
- [x] ⚠️ **`bind_label_to_config` is otherwise untouched**: loop, `same_map`, ranks, `nclaim`,
      claimant warning, `${rows:-$config}`
- [x] ⚠️ pass `--launch-agents "$agentsdir"` on every call — without it the flag silently stops
      working while the suite stays green, because the fixture sets `HOME=tmp_path` (`:297`) so the
      default happens to land in the right place
      → done, and guarded **behaviourally** despite being inert under `--plist`:
      `test_plist_arg_really_forwards_the_launch_agents_directory` runs the extracted function with
      an empty `$agentsdir`, which `agb`'s parser refuses as a missing value. Drop the forwarding
      and the call answers `(0, "/a/config")` instead — mutation-confirmed
- [x] ⚠️ ~~**repoint** `test_the_embedded_reader_is_ascii_and_apostrophe_free` (`:3398`) at the
      `plistlib-ok` probe rather than deleting it~~ — **superseded by the R6 bullet above**, which
      says the same test is deleted; the ONE-probe decision removes the program it would point at.
      The rest of the bullet is done: `_extract_sh`/`plist_arg` fixture (`:2742-2804`) and
      `_read_with` (`:3310-3322`) both gained `agentsdir=`, and both docstrings now say `$agb`
      selects the whole reader rather than just the parser beside it
- [x] ⚠️ **move the differential corpus** and update its docstring: after the move the oracle is
      `parse_bridge_args`, so the argv half approaches `f(x)==f(x)`; what survives is the plumbing
      (where argv starts, which key, the status mapping, the encoding) plus `plistlib` as a genuine
      external authority
      → ⚠️ **deviation, stated rather than hidden: the corpus is re-homed IN PLACE, not relocated.**
      Physically moving it to `tests/test_bridge_rows.py` would mean driving `run_instances`
      in-process, which skips exactly the four things the bullet lists as what survives — the whole
      shell→`agb`→status→bytes hop is the part that changed. So all 40 cases × 2 shapes still run
      the shipped `plist_arg`, and both docstrings now spell out that the `parse_bridge_args` half
      of the oracle has gone self-referential while `plistlib` and `dist/com.agbridge.plist` have
      not
- [x] ⚠️ **R5 — this task inverts NOTHING behavioural.** The R4 list (`:428`, `:708`, `:893`, `:946`)
      was stale from R3 and contradicted this task's own scope: `:708`, `:893` and `:946` are
      **narrowed** runs exercising `bind_label_to_config`, which this task does not touch — and
      `:893`/`:946` are the guards on the flagless-plist → `$DEFAULT_CONFIG` implication that
      contract 3 says must stay separate from the convention. "Inverting" them would delete the
      coverage for the bug R3 was rejected for. `:428` is a bare run and belongs to **Task 3**
- [x] ⚠️ `tests/test_agb_refresh.py:2802` asserts `err == b""` ("anything on stderr here is a bug in
      the reader"). `agb` writes `USAGE` and `AgbError` text to stderr, so this becomes a much
      stronger claim about `run_instances` — keep it, and say so
- [x] ⚠️ **R6 — no `drop_ops` arm.** R5 asked for one, which is dead under R5's own module decision:
      with the reader in `agb_mac`, "`agb_mac` present with the reader's module gone" is a
      contradiction — `agb_ops` is on no path `agb instances` takes. `_lib_with(drop_mac=True)`
      (`:3299`) already covers the only real direction
- [x] **mutation-check each behaviour separately** — eight, each producing a *named* failure:
      (1) narrow `except` → `test_the_missing_sibling_is_an_oserror_and_not_an_importerror` +
      `test_a_tree_with_no_agb_mac_beside_agb_is_fatal_not_silent`; (2) `try` wrapping the call →
      `test_a_failure_inside_the_reader_is_not_disguised_as_a_missing_tree`; (3) drop
      `--launch-agents` → `test_plist_arg_really_forwards_the_launch_agents_directory`;
      (4) probe status-compared → `test_the_probe_is_answer_compared_and_not_status_compared` +
      `test_an_interpreter_that_exits_zero_without_reading_anything_is_refused`; (5) drop the exit-3
      forward → `test_a_tree_with_no_agb_mac_beside_agb_is_fatal_not_silent`; (6) no probe at all →
      three named tests; (7) log the routed call → seven named tests; (8) `plist_arg` ignoring
      `--agb` → `test_the_reader_really_asks_agb_mac_and_not_a_walk_of_its_own` +
      `test_the_fake_interpreter_really_runs_the_tree_the_flag_names`
- [x] `sh -n agb-refresh`; run tests — 1824 pass (was 1798; +26), `sh -n` clean on both scripts

**⚠️ Found and fixed here, not in Task 1: `agb.cmd_instances` caught the wrong exception class.**
It shipped as `except (ImportError, AttributeError)`, but `agb._load_sibling` loads `agb_mac` **by
path**, so a tree without it raises `FileNotFoundError` — an `OSError`. That escaped as a traceback
and exit 1, which `plist_read_ok` reads as "the reader itself failed", i.e. a statement about
`--python` for a tree that is missing a file. Latent under Task 1 (the old `-c` reader answered 3
itself); load-bearing the moment `plist_arg` started calling `agb`. Now `except Exception` around
the **load only** — wrapping the call as well is the opposite mistake and has its own guard.

**⚠️ Budget: not raised, and there are 2 characters left.** The code delta is ~10 characters; the
reason-comment moved into `agb_mac.run_instances`' docstring, which is uncapped. `agb` is 103,198
of 103,200 — recorded in `tests/conftest.py` above the constant, because "headroom 65" from Task 1
is now false.

### Task 3: `agb-refresh` sweeps every instance, and fails safe

**Files:** `agb-refresh`, `tests/test_agb_refresh.py`

- [x] bare `agb-refresh` re-execs `"$0"` once per label from `--labels`, aggregating exit codes
- [x] ⚠️ **forward every flag explicitly** — `--dry-run` above all (a sweep that drops it performs a
      real refresh for a command that promised to change nothing), plus `--no-close`, `--agb`,
      `--python`, `--launch-agents` (the fixture always passes the first two, `:298-300`)
- [x] ⚠️ **R6 — `--key` SWEEPS; "not in this map" is only an error if NO instance had it.** R5 left
      three incompatible readings across Tasks 3 and 4 ("requires one instance", "narrowing flag,
      today's semantics", "refused without an instance"). Decided in favour of the plan's own
      thesis — *you should not have to know which instance* — and it is also what keeps the
      documented recipe working unchanged: `agb-refresh --key a3f9c1e0` (`agb-refresh:13-16`, `:58`,
      `docs/commands.md:723`) finds the key wherever it lives. A key belongs to exactly one map, so
      the sweep reports which instance had it and fails only when none did
      → ⚠️ **one blind spot, stated in the source and in `CHANGELOG.md` rather than hidden**: several
      `--key`s spread across DIFFERENT instances make every child answer 1 (each forgets its own and
      reports the others missing), so the run reports failure although all of them were forgotten.
      Telling that from a real failure needs the children's OUTPUT, not their status; it errs towards
      failing for work that succeeded, which is the safe direction
- [x] `--rows` narrows to one run (`agb-refresh` has **no** `--placements` — that is `forget-rows`',
      Task 4)
- [x] a child's failure is recorded, the sweep continues, exit non-zero with a summary naming it
- [x] ⚠️ an instance left **without a running bridge is an ERROR** — it is what justifies bare-is-all
      → spelled as a distinct child exit status (**4**, from `start_label`) rather than folded into
      `$code`: with `--key` a child's 1 is the ORDINARY answer, so a bridge left down had to be
      distinguishable from the answer the sweep expects most
- [x] ⚠️ **do not break the existing no-plist recipe.** Today a bare `agb-refresh` on a Mac with no
      plist still forgets and warns (`:1608`) — the commonest recipe there is (`SKILL.md:248`). Decide
      what "no instances found" means without regressing it
      → **no instances = a note, then FALL THROUGH** to the single default run, not an exit.
      `test_no_instances_at_all_still_refreshes_the_default_map`
- [x] ⚠️ **R5 — this task's real breaks, named.** `tests/test_agb_refresh.py:517`
      (`test_the_convention_is_still_the_fallback_when_no_plist_answers`) and `:2281` both assert
      `rc == 0` on a run where no plist exists for the label, so `bootstrap` is skipped and `:1607`
      warns. "An instance left without a running bridge is an ERROR" makes both non-zero — and note
      it fires on the **single-instance** path, not only the sweep. ⚠️ **R6 — narrow the rule to the
      sweep; do NOT invert them.** Inverting silently repeals a documented promise: `agb-refresh:966-968`
      and `docs/design.md:1911-1914` both state that a Mac whose plist was never rendered can still be
      refreshed by name, and inverting `:517` makes `agb-refresh --instance hostb` fail there. "No
      running bridge is an error" is a **sweep** rule, which is where it earns its keep
      → **neither was inverted and neither needed a code change**: the two branches are different
      facts, and only one of them is exit 4. `start_label` answers 0 for "no plist at this label"
      (the documented recipe) and 1 for "bootstrap AND `load -w` both refused" (a Mac left down).
      A label the sweep visits came out of a plist, so the first branch is unreachable there — the
      one race that could reach it (a human `rm` mid-run) is written down at `start_label`. Both
      tests gained a ⚠️ paragraph saying they are the guards on that scoping
- [x] ⚠️ **re-reason the bare-run block**, which now executes through a re-exec: `:329`, `:341`,
      `:360`, `:370`, `:428`, `:569`, `:1282`, `:1303`, `:1332`, `:1373`, `:1827`, `:3349`, `:3465`,
      `:3575`, `:3640`, `:3662`, `:3695`, `:3752`, `:3821`. Most keep passing with one label present
      — which is the pass-vacuously shape this repo keeps finding. `:569` ("a plain refresh ignores a
      named instance's bridge") and `:3752` assert properties of a command that no longer has them
      → done, and the conclusion is written into the new section's header: the fixture installs ONE
      instance, so a bare run is a sweep of one, and every property those tests assert is a property
      of the CHILD, which did not move. What a sweep of one cannot show is that they survive a sweep
      of several — which is the new section's job, not theirs.
      `:569` and `:3752` each gained a ⚠️ paragraph (the first is no longer a property of the
      COMMAND, only of each child, and names its multi-instance twin; the second's `rc == 0` now also
      says a 10 s warning is not a sweep failure). `:428` gained one too — "(default)" is now what
      the child says about the one installed instance, not what a flagless run means. `:3349`
      (`--agb` broken) and `:3821` (`--flag ""`) refuse BEFORE the sweep; `:3575` passes `--rows`,
      so it narrows
- [x] ⚠️ **state how the script names itself for the re-exec.** `refresh.run` invokes
      `["sh", SCRIPT, …]` with SCRIPT absolute (`:296-299`), so `$0` always has a slash **in the
      suite** — installed on `$PATH` it works, but `sh agb-refresh` from its own directory gives a
      `$0` with no slash and no `$PATH` entry, and the sweep dies. Same class as the
      ProgramArguments-is-four-elements-longer finding: a harness simpler than reality. Record the
      rule and test one invocation whose `$0` is not the fixture's
      → `sweep_self()`, extracted and unit-tested through `_extract_sh` (four cases) as well as two
      end-to-end runs, one per no-slash spelling. ⚠️ **The order is the CWD FIRST, then `$PATH`**,
      and the first draft had it the other way round on a safety argument that turned out to be
      backwards: `sh <name>` itself tries the cwd before `$PATH` (measured on bash, which is
      `/bin/sh` on macOS), so `$PATH`-first would sweep with a DIFFERENT copy of the script than the
      parent was read from. Also measured: `sh "$0"` happens to work for both spellings on bash, so
      the block is not what makes the suite pass — it is there because POSIX does not require that
      `$PATH` search (dash does not do it) and because an unresolvable name has to be one `die`
      before the first bootout rather than `sh: can't open` once per instance
- [x] ⚠️ move Task 2's `--launch-agents "$agentsdir"` forwarding here — under `--plist <path>` it
      does nothing at the `--arg` sites; it is `--labels` that needs it
      → ⚠️ **deviation: ADDED at the `--labels` site, not MOVED.** Task 2 kept it at the `--arg`
      sites deliberately, with the reason at the call site ("a flag forwarded from one site out of
      two is a flag that silently stops working") and a behavioural guard,
      `test_plist_arg_really_forwards_the_launch_agents_directory`. Removing it now would delete that
      guard's subject to satisfy a bullet written before the decision
- [x] ⚠️ **the `trap` lives in the CHILD** — the process dying between its `bootout` (`:1539`) and
      `bootstrap` (`:1601`). Name the signals; re-raise for the right exit status
      → `INT`/`TERM`/`HUP`, armed one statement before the bootout and cleared after the restart, so
      the sweeping parent (which reaches neither) can never arm it. `kill -"$1" $$` after `trap -`,
      which is what gives the parent a 128+signo it can tell from a failure — and the parent then
      STOPS the sweep rather than bouncing the instances the operator interrupted it to protect
- [x] tests: two instances in order with their banners; A fails → B still refreshed, A **restarted
      anyway**, exit non-zero, summary names A; a failure in **each** phase (stop, forget, restart);
      nothing left stopped; one test per forwarded flag, `--dry-run` first
      → plus one the plan did not ask for and the loop needs:
      `test_a_child_that_reads_stdin_does_not_eat_the_remaining_instances`. The label loop's stdin IS
      the here-document holding the labels, so a child that read one byte of it would silently
      swallow the instances not yet swept
- [x] ⚠️ test that **B's live bridge does not hold A's wait** — `cmdline_is_ours`'
      `ambiguous`/`unknown` branches (`:1287-1298`, `:1477-1490`) treat an unattributable bridge as
      ours, so a sweep could wait 10 s per instance and warn on the commonest invocation there is
      → `test_one_instances_live_bridge_does_not_hold_another_instances_wait`: hostb's bridge is up
      and never dies, and `out.count("still running") == 1` says exactly one instance waited and
      which
- [x] **mutation-check separately**: child failure fatal; trap dropped; a flag not forwarded
      → **25 mutations, each producing a NAMED failure.** Two shipped green on the first pass and are
      the reason the count is 25: `--launch-agents` not forwarded (the test wrote the plist in BOTH
      directories, so a child ignoring the flag found it anyway — the "the default happens to land in
      the right place" trap the bullet warns about, arriving inside the test written for it), and the
      `$0` block (raw `$0` works on bash, so only the extracted `sweep_self` unit tests can kill it)
- [x] `sh -n agb-refresh`; run tests — 1847 pass (was 1824; +23), `sh -n` clean on both scripts.
      `agb` is untouched by this task: `git diff -- agb` is empty, so the 1 character of headroom
      Task 2 left is intact

### Task 4: `close-done` goes plural; `forget-rows` gains `--all`

**Files:** `agb_mac`, `tests/test_bridge_rows.py`

- [x] discovery module already decided in Task 1; give both commands `--launch-agents` (or a
      parameter on the shared helper) — they are driven **in-process** by `tests/test_bridge_rows.py`,
      so without it the sweep is only testable by monkeypatching `$HOME`
      → **the flag, on both**, and forwarded through the one shared helper (`sweep_targets`). The
      guard is behavioural and had to be built against the trap the plan warns about twice: under
      `fake_home` the default `~/Library/LaunchAgents` does not exist, so a command ignoring the
      flag falls through to the **default map** and still reclaims the default instance's row. Only
      a *second* instance can tell the two apart, so `_agents_dir()` asserts the default directory is
      absent (non-vacuity) and the test asserts on **hostb's** map
- [x] ⚠️ decide the **no-launchd case**: plist discovery makes both fail on a Mac with a config but
      no job, where `docs/cookbook.md:161-163`'s bare `close-done` works today
      → **a note, then FALL THROUGH to the single default map** — Task 3's answer, verbatim, so the
      shell sweep and the in-process one degrade the same way. ENOENT on the LaunchAgents directory
      is that same answer; every *other* errno is fatal, which is where
      `os.path.isdir`/`exists` swallowing every errno would have reported a broken filesystem as
      "no instances yet"
- [x] bare `close-done` acts on every instance, **through `instance_paths`**
- [x] bare `forget-rows` is **refused**, naming `--all`; `--all` opts in; `--config` narrows
- [x] ⚠️ **R5 — `--rows` / `--placements` / `--key` are themselves NARROWING flags; they do not
      require a separate instance.** R4 said both "require one instance" and "bare
      `forget-rows --rows <path>` is a documented recipe", which contradict. Decided: naming a map
      *is* naming what to act on, so such a run keeps **today's** semantics exactly — one map, config
      defaulting to `agb.config_path()` unless `--config` is given. Only a run naming **no** map
      sweeps. That preserves the documented recipe (`docs/cookbook.md:503`, `SKILL.md:250`) and the
      ~23 tests at `tests/test_bridge_rows.py:1607`–`:2832` that pass `--rows` as a test seam, and it
      introduces no new hazard: the mixed-pair case is already documented at `agb_mac:2688-2694`
      → ⚠️ **deviation, and it resolves a contradiction inside this task rather than introducing
      one: `--key` SWEEPS; only `--rows`/`--placements`/`--config` narrow.** This bullet is R5 text
      that R6 corrected for Task 3 and not here, and this task's own test bullet (below) asks for
      "`--key` sweeps and names the instance that had it, failing only when none did" — the two
      cannot both hold. Decided the same way R6 decided it for `agb-refresh`, and for the same
      reason: `--key` names WHAT to forget, not WHERE, and a key read out of a bridge log does not
      say which instance minted it. `MAP_FLAGS` is the list, `names_a_map()` is the predicate, and
      the reason is at both
- [x] ⚠️ say in the CHANGELOG that `--rows` alone still implies the default config — it is the one
      place the old default survives, deliberately, and a reader who has just been told the default
      is gone will otherwise be surprised
- [x] banner once per instance; same failure polarity as Task 3
      → asserted by *count* (`out.text.count("close-done: config") == 2`), which is also what pins
      the de-duplication: two plists naming one config are one instance and print one banner
- [x] define and test the **aggregate exit code** — `run_close_done` returns 0 unconditionally
      (`agb_mac:2958`), `run_forget_rows` returns 1 only for missing keys (`:2892`)
      → **the FIRST non-zero wins** (`sweep_status`), not the last and not the largest: these
      statuses carry per-command meanings (`agb-refresh`'s 4 is "a bridge was left down"), so
      folding them into "whatever the last instance said" is how a failure mid-sweep disappears.
      A `--key` sweep is 0 when *any* instance held the key and 1 when none did — the per-instance
      "not in this map" is the ordinary answer and is neither printed nor counted
- [x] `ast` reachability guard that both resolve through `instance_paths`, **non-vacuity first**
      (`assert "run_close_done" in reachable`)
      → ⚠️ **the suggested spelling is itself vacuous and was not used**: `reachable_from` seeds its
      result with the root, so `root in reachable` passes even when the function has been renamed
      out from under the walk. The guard asserts `root in funcs` instead, then that
      `instance_paths`, `instance_configs` and `sweep_targets` are all reachable. Mutation-checked by
      renaming `instance_paths`, which the behavioural tests cannot see
- [x] invert `tests/test_bridge_rows.py:2583` (`test_forget_rows_uses_the_default_map_when_none_is_named`,
      a live-use regression guard) and `:3037`
      (`test_the_banner_names_the_default_config_when_no_flag_is_passed`, parametrised over **both**
      commands) **in this task**, reasoning updated, not deleted
      → both inverted in their **argv only**, with a ⚠️ paragraph saying so. The first keeps its
      whole regression (`read_rows_file(None)` answering `[]`) by running `--all` on a Mac with no
      instances, which is the same resolution the bug was in. The second is now parametrised over
      `(name, argv)` and its claim got *stronger*: the banner is what tells a fall-through apart from
      a sweep
- [x] ⚠️ **R6 — the in-process sweep needs the same two policies the shell side got.** State them in
      the same words as contracts 2 and 3: a plist carrying **no `--config`** reads as
      `agb.config_path()` (the `bind_label_to_config:766-769` reading), and **one unreadable plist
      mid-sweep is fatal, not skipped**. Left unstated this is invariant 12's eleventh instance
      arriving in-process, where it closes `[done]` rows in the wrong instance's map and reports
      success
      → both written above `MAP_FLAGS` as a numbered pair, each with a named test and a mutation.
      ⚠️ **The second needed one clause the plan did not spell**: fatal *iff the label says the plist
      is ours*. `_is_agbridge_instance` cannot attribute a file it could not parse, so a stricter
      rule would let any third-party junk `.plist` stop every sweep on the machine, and a looser one
      would let one of ours fall back to the default config. Both directions have a test
- [x] ⚠️ decide whether `--all` beside a map flag (`forget-rows --all --rows X`) is an error or the
      map wins — one line either way
      → **an error.** Letting either win silently is invariant 12's shape — the right map under the
      wrong label, reported as success — and the operator has said two contradictory things
- [x] tests: bare `close-done` reclaims `[done]` rows in both maps, others untouched;
      `forget-rows --all` writes **each instance's own** placements; bare `forget-rows` refused;
      `--rows`/`--placements` keep today's single-map semantics; `--key` sweeps and names the
      instance that had it, failing only when none did
- [x] **mutation-check each guard separately**, including a `--key` sweep that reports success when
      no map had the key
      → **21 mutations, each killed by a NAMED test**, each verified to apply exactly once before
      running (a mutation whose text does not match is reported as a vacuous setup, not as a pass).
      ⚠️ **The harness itself had the bug this bar exists to catch, in a new shape**: its first
      version restored the file with `git checkout -- agb_mac`, which for an *uncommitted* change
      restores HEAD — it silently deleted the whole task's implementation after the first mutation
      and reported the next fourteen as "old text found 0 times". Restore from an in-memory snapshot,
      never from git, when the code under test is not yet committed
- [x] run tests — 1866 pass (was 1847; +19), `sh -n` clean on both scripts, `git diff -- agb` empty

### Task 5: Verify acceptance criteria

⚠️ **Driven end to end against a throwaway `$HOME`, not from the suite** — two instances' plists, a
stub `launchctl`/`pgrep`/`ps`/`agtermctl` on `$PATH` and a fake interpreter that routes
`agb instances` to a real python and records every other `agb` call. Every line below was read out of
those recorded calls.

- [x] bare `agb-refresh` refreshes every instance; each narrowing flag works
      → bare: `swept:    2 instances`, one `bootout`/`forget-rows --config <its own>`/`bootstrap`
      triple each. Narrowing checked one flag at a time: `--instance hostb`, `--label
      com.agbridge.hostb`, `--config <path>`, `--rows <path>` each produce ONE child and no `sweep:`
      line; bare `--dry-run` sweeps with `--dry-run` forwarded to both children and **zero**
      `launchctl` calls; bare `--key` sweeps and reports `keys: forgotten by: …`. Also driven with a
      `$0` carrying no slash (`sh agb-refresh` from its own directory), which is the spelling
      `sweep_self` exists for
- [x] a failing instance leaves **nothing** stopped; an instance without a running bridge is an error
      → first instance's `forget-rows` exits 1: the second is still swept, the failing one is
      **still bootstrapped**, exit 1, `WARNING:  failed: com.agbridge.hostb(exit 1)`. First
      instance's `launchctl` refuses both `bootstrap` and `load -w`: exit 1 with
      `WARNING:  no bridge was started again for: com.agbridge.hostb`, and the second instance is
      swept anyway
- [x] `agb instances` answers all three modes; a non-agbridge plist is not in `--labels`
      → four modes: `--probe` → `instances-ok`; `--labels` → two labels; the default listing → two
      rows; `--plist … --arg --config` → the value (exit 0), empty (exit 0) for a plist of ours
      carrying no `--config`, **exit 2** for a missing file and for a file that is not a plist, and
      the value again for a plist path containing a space. `com.example.other.plist` appears in
      none of them. Contract 2 both ways, live: a missing directory is exit 0 + empty, a `chmod 000`
      one is exit 1 + `instances: cannot list …: errno 13` and the sweep **dies** on it. Contract 1
      live under `LC_ALL=C`, `POSIX` and `en_US.ISO-8859-1`: a config path with `é`/`ü` comes back
      as byte-identical UTF-8 in all three
- [x] a **custom `--label`** and a **`--config`-only** install are both discovered and actable
      (suite-level — a `--config`-only install is uncreatable once the follow-up plan lands)
      → one `$HOME` holding both (`weird.label` + `com.agbridge` naming `$H/elsewhere/config`):
      `--labels` lists both, and the bare sweep bounces both, each `forget-rows` carrying **its
      own** config. This is where the one real defect turned up — see below
- [x] bare `close-done` acts on both maps; bare `forget-rows` refused; `--all` sweeps
      → `close-done` closed `ROW-A-DONE` and `ROW-B-DONE` and left both `bound` rows alone, one
      banner per instance; bare `forget-rows` exits 1 naming `--all` with **no** `agtermctl` call at
      all; `--all` wrote each instance's own `placements` (`bbbb2222 = wsA` in the default's,
      `dddd4444 = wsB` in hostb's — no cross-writing). `--all --rows X` is refused; a Mac with a
      config and no launchd job prints the note and falls through to the default map
- [x] the multi-claimant warning still prints on `agb-refresh --config <path>`
      → two jobs over one map: `WARNING:  more than one launchd job claims this config's map:` /
      `com.agbridge.aaa com.agbridge.hostb.` / `Using com.agbridge.hostb …`, i.e. the rank still
      beats collating order. `bind_label_to_config` is byte-identical to before the plan
- [x] `len(agb)` in **characters** under the budget; any raise matches its recorded measurement
      → 103,198 characters (103,212 bytes — `wc -c` is the wrong number) against 103,200. The
      recorded history reconciles exactly: 102,419 before the plan → 103,135 at Task 1 (**+716**,
      the measured delta the raise is written against, leaving the recorded headroom of 65) →
      103,198 at Task 2, unchanged by Tasks 3, 4 and 5
- [x] the corpus passes against `plistlib` and `parse_bridge_args`
      → 54 cases × 2 argv shapes = 108 through the shipped `plist_arg`, plus the two non-vacuity
      guards on the corpus itself
- [x] full suite; `sh -n install.sh && sh -n agb-refresh`
      → **1867 pass** in 69 s (1866 + the one test below); `sh -n` clean on both scripts;
      `git diff -- agb` empty

**⚠️ Found and fixed here: `agb-refresh` called a custom-label instance "(default)".** The banner
reads the instance *name* back out of the label and everything outside the `com.agbridge` space fell
through to `(default)`, so the bare sweep announced **two** default instances — one of them
somebody's named machine — in the line the file's own comment calls "the whole mitigation" for
acting on the wrong instance. Task 3 is what made it reachable by accident: before the sweep, the
only way to land on such a label was to type `--label weird.label`. Fixed in `agb-refresh` (the
label is shown when it is the only name there is; only `$DEFAULT_LABEL` stays `(default)`), with
`test_a_custom_label_instance_is_not_reported_as_the_default_one` and a `CHANGELOG.md` entry.

**Vacuity sweep: 16 mutations, each killed by a NAMED test, none vacuous.** Ten in `agb_mac`
(`--arg` via `print` instead of `stdout.buffer` → the two three-locale tests; every errno read as
`ENOENT`; membership without the `agb bridge` clause; `--key` added to `MAP_FLAGS`; the `--all` gate
removed; `sweep_targets` ignoring `--launch-agents`; `instance_paths` deriving placements from the
default config; `sweep_status` keeping the last status; an unreadable plist of ours skipped; a
flagless plist not reading as the default config) and six in `agb-refresh` (the sweep dropping
`--dry-run`; a child's failure ending the sweep; the probe removed; `--labels` called without
`--launch-agents`; exit 4 folded into an ordinary failure; the fix above reverted). Restored from an
in-memory snapshot, never `git checkout`, and each mutation asserted to apply exactly once before
running.

### Task 6: Migrate the live Mac (human-run, no code)

Runs **after** the code, deliberately: the new code sweeps a Mac that still has a default instance
perfectly well — the default label is just another label — so there is no window in which a bare
`agb-refresh` silently succeeds on nothing.

⚠️ **Boot out and WAIT before moving anything** — a live bridge holds the rows map in memory and
merges-then-writes, so a move underneath it is silently lost. ⚠️ **Rows minted by the default install
carry no `--config`** (`agb_mac:1384-1406`), so they must be re-minted.

- [x] pick the name; `launchctl bootout gui/$(id -u)/com.agbridge`; poll until `pgrep -f 'agb bridge'`
      loses its pid
- [x] `mkdir -p ~/.config/agbridge/<name>/`; move `config`, `rows`, `placements`; move the logs
- [x] re-run with **every mandatory flag** — `--instance` needs `--statedir` (`install.sh:446`), the
      mac role needs `--feed-host` and `--agb-remote-path` (`:482-483`):
      `sh install.sh mac --instance <name> --statedir <p> --feed-host <t> --agb-remote-path <p>`
- [x] `rm ~/Library/LaunchAgents/com.agbridge.plist`; confirm the new label bootstrapped
- [x] **re-mint**: `agb-refresh --instance <name>`
- [x] verify both instances' rows present once each; clicking each reaches the right machine
- [x] record the ordered steps in `CHANGELOG.md` as an upgrade note for ≤ 0.5.0

### Task 7: [Final] Documentation

- [x] ⚠️ `docs/design.md:1450-1920` — ~450 lines specifying `plist_arg`: its statuses, the `plistlib`
      sniff, the label-space guard, the ranks, the claimant counting, the `[ -e ]` split. design.md is
      "the authority and is reconciled against the implementation", so this is the **largest** doc
      site, not §5's cells
      → six edits in place rather than a rewrite, because most of it stayed true: the four statuses
      (exit 1 now names **two** files, since the reader is two files), the probe (`import plistlib` →
      `agb instances --probe` **and why it must be stdout-compared**), the `parse_bridge_args` hop
      (plus the exit-3 catch's shape and both wrong spellings of it), the `plistlib` paragraph (the
      reader is not in `agb-refresh` any more), the cost, and the three ASCII/bytes rules — **one
      retired, one now structural, one unchanged and still the dangerous one**. `bind_label_to_config`
      is byte-identical, so the ranks/claimant/`[ -e ]` prose needed no change
- [x] `docs/design.md` §5: limitation 1 mitigated by the default; limitation 6 **mitigated** (not
      resolved); the rejected discovery designs; ⚠️ limitation 3 gains a line — after Task 6 there is
      **no default config on the Mac at all**, so `doctor`, `status-line` and `prune --via-ssh`
      describe a file that does not exist
      → plus two new `###` sections carrying what §5's cells cannot: *`agb instances`, and why the
      reader moved into it* (the four modes, contracts 2 and 3, the `agb_mac`-not-`agb_ops` decision
      and its budget raise) and *The sweep, and the one command that does not do it* (why
      `forget-rows` differs, `--key`, the re-exec, `sweep_self`'s cwd-before-`$PATH` order, the trap
      in the child, the `--key` blind spot, the in-process sweep's two policies, the fall-through,
      and both **rejected discovery designs** — config-directory globbing and a registry file)
- [x] ⚠️ `docs/commands.md:786-861` (the `--config` scan), plus `:221`, `:668` (`--config` default
      cells), `:727` (`--instance` default cell), `:728`
      → and a new `## agb instances — Mac` section; `close-done` and `forget-rows` gained their real
      signatures (`--all`, `--launch-agents`, `--placements`), the sweep/refusal rules and the
      "**every instance**" default cells; `agb-refresh` gained the sweep block, exit 4 and the
      Ctrl-C rule. ⚠️ **Two counted claims were already stale and are now measured**: "nine
      hand-rolled parsers" is **thirteen** (the enumeration was missing `forget-rows`, `list`,
      `rename` before `instances` was added), and "within a few hundred bytes of the cap" is 2
      characters
- [x] `docs/cookbook.md`: ⚠️ `:161-163`, `:364-366` (the "act on the default instance — successfully,
      which is the trap" table **and** the line after it), `:503`
      → the bare `close-done` recipe keeps working and now says what it does with several instances;
      the "Living with more than one" table lost its trap row and gained the sweep, the `forget-rows`
      refusal and `agb instances`; the `--rows` recipe says why naming a map is what makes it a
      one-map run
- [x] `README.md`: ⚠️ `:112` ("run the helpers **without** `--instance` and they act on the default
      instance"), `:329`; test count
      → 1777 → **1867** in both places, `agb instances` in the command table, and the "not exercised"
      list gained the sweeps with the three things worth watching live (both sidebars, Ctrl-C
      mid-sweep, a broken instance still being restarted)
- [x] `.claude/skills/agbridge/SKILL.md`: instance recipe, symptom table, ⚠️ `:250`, `:254`
      → recipe rewritten around the sweep; three new symptom rows (which instances exist / exit 4 /
      the `--all` refusal); the `--rows` recovery row says it narrows, which is why it needs no
      `--all`
- [x] ⚠️ in-code prose stating the old default: `agb_mac:2814-2820`, `:2921-2923`
      → ⚠️ **both line numbers were stale** (Tasks 4/5 moved them); found by content. The banner
      comments now say the hazard is a property of a **narrowed** run, and keep the original
      reasoning rather than deleting it
- [x] ⚠️ `docs/design.md:1937-1943`'s limitation-1 table has a row `agb-refresh (the mistake)` →
      `instance: (default)`, annotated "the one thing this line cannot be allowed to be wrong about".
      That cell becomes false
      → the row is now `agb-refresh (the sweep)` → the `sweep:`/`instance:`×N/`swept:` shape, with a
      ⚠️ saying explicitly which cell changed and why the annotation no longer applies to it
- [x] ⚠️ `agb-host-line:99` prints `grep feed_host ~/.config/agbridge/config   # on the Mac`, naming
      a file that will not exist after Task 6. (Its emitted Mac-side snippet at `:116-118` already
      globs `~/.config/agbridge/*/config` and is fine — only the prose is wrong.) The script is
      otherwise not in this plan
      → now globs both shapes, matching the snippet below it. `sh -n` clean; no test asserts on the
      string
- [x] record the measured cost, so it is not rediscovered as a regression: today's reader is
      ~21.5 ms/call; `agb` as `__main__` plus one sibling load is ~24.7–26.1 ms (no `.pyc`, because
      `__main__`), and `bind_label_to_config` calls it up to twice per plist — so 30 LaunchAgents
      goes ~1.3 s → ~1.6 s. Not a reason to change anything
      → in `docs/design.md`, replacing the old 19.0 → 21.6 ms figure, and stated as "not a reason to
      change anything" in those words
- [x] ⚠️ `CLAUDE.md`: invariant 14 is a **counted** list currently reading "Three cross-file
      agreements" — Task 1 adds a fourth (the LaunchAgents dir + label space), so the count moves
      → "Four", with the new bullet naming `default_agents_dir`/`INSTANCES_LABEL_PREFIX`, the
      failure it hides (the two sweeps visiting **different sets**), the pin
      (`test_install_pkg.py:2306`) — and a warning that the label space answers **two different
      questions** here, so a test equating the claimant guard with the membership rule would be wrong
- [x] ⚠️ `CLAUDE.md`: invariant 12's corollary; the bytes-vs-characters correction; any budget
      decision; **and if Task 1 put `run_instances` in `agb_ops`, the Known-gaps claim that it
      "never loads `agb_mac`"** — also asserted in prose at `tests/test_identity.py:1000`
      → invariant 12 gained a **thirteenth** instance (the reader moved; `bind_label_to_config` is
      byte-identical; why porting the ranks was rejected; why the 0/2/3 contract had to be inherited;
      the exit-2 collision). Budget: 102,419/102,500 → **103,198/103,200**, raised **twice** not
      once, with the "63 of the 65 are already spent" warning. Parser count 9 → 13 in three places.
      ⚠️ **The `agb_ops` clause needed NO edit and that was checked, not assumed**: Task 1 put
      `run_instances` in `agb_mac`, and `grep` confirms `agb_ops` still contains no `_load_mac`,
      no `_load_sibling` and no `import agb_mac`
- [x] ⚠️ `CHANGELOG.md`: add to the **existing** `## Unreleased` (`:9`) — do not create a second
      heading. Name the symptom; carry Task 6's upgrade note; say that symmetry is a **convention
      until the follow-up plan lands**
      → one `### Added` entry for `agb instances` (both contracts, the probe's literal answer and the
      exit-2 collision, and why the ranks were *not* ported), an `### Upgrading from ≤ 0.5.0` section
      carrying Task 6's six ordered steps **as an upgrade note, not as a performed migration**, and a
      `### Not verified` naming both the live gap and the convention-not-guarantee. Heading count
      unchanged: one `## Unreleased`
- [x] decide `VERSION` (`agb:24`): a new command plus two breaking CLI changes argues **0.6.0**
      → **0.6.0**. A same-length string, so `agb` is still **103,198** characters and the headroom is
      still 2 — the expected result, and the check that says nothing else got in
- [x] ⚠️ **bump `VERSION` only — do NOT cut the release.** No `git push`, no `git tag`, no GitHub
      Release. `CHANGELOG.md` stays under `## Unreleased`. The owner does all of that by hand
      → bumped only. No push, no tag, no release; the `## Unreleased` heading is untouched and a note
      under it records the version choice so the owner does not have to re-derive it
- [x] write the follow-up plan for `install.sh mac --instance` (the `mac_args` fixture, the
      `dist/com.agbridge.plist` question, the transitive `--statedir` decision)
      → `docs/plans/20260801-install-mac-requires-instance.md`. Measured rather than estimated where
      it could be: `mac_args` is used **140 times** in `tests/test_install_pkg.py`. Four open
      questions left explicitly undecided, including whether `--label`-only stays an install — which
      determines whether this plan buys what it looks like
- [x] move this plan to `docs/plans/completed/` — held back until Task 6 was actually run, so the
      migration checklist stayed visible to the person who had to run it. Done 2026-08-01, after both
      instances were verified live

## Post-Completion

**Live verification** (unreachable from the suite; two of the last four features passed every test
and still needed a fix after live use):

- Two bridges up, bare `agb-refresh`, both sidebars back with their identities.
- A deliberately broken instance mid-sweep: the other still refreshed, the broken one's job
  **restarted anyway**, summary names it, exit non-zero.
- Ctrl-C mid-sweep — the child's trap is the only thing between that and a bridge left down.
- Click a row from each instance; land on the right machine.
- `agb instances` with a non-agbridge plist present, and with a custom-`--label` install.

**Deferred, stated rather than hidden:**

- `agb doctor`, `agb status-line` and `prune --via-ssh` on the Mac resolve the default config
  unconditionally with no `--config`. After Task 6 that file does not exist, so this gets **worse**.
- `install.sh mac` can still create a nameless instance until the follow-up plan lands.
- `README.md`'s verification table still carries the instance rows.
