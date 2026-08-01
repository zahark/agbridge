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

- **Repo**: `/home/zk/agbridge-public`, `main`, HEAD `5571337` (⚠️ R6 — two commits since R5 added `agb-ralphex` and `agb-host-line`; `agb`, `agb_mac`, `agb-refresh` and the tests are untouched, so no cited line moved). No worktree. 1798 tests, ~52 s.
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

- [ ] raise `AGB_PARSE_BUDGET` on a measurement of the **final** delta (arm + `USAGE`, ≥5
      `compile()` runs, in the form the previous raise used), and record why the `OPS_COMMANDS`
      route was rejected
- [ ] fix `tests/conftest.py:44`'s "in bytes" → "in characters"
- [ ] ⚠️ **`--arg` writes `sys.stdout.buffer` UTF-8 bytes** — see contract 1; nothing else in the
      three files does this, so it will not happen by accident
- [ ] ⚠️ **R6 — the exit-3 catch belongs in `agb`'s dispatch arm, not in `run_instances`.** R5's
      bullet was impossible once `run_instances` lives *in* `agb_mac`: the sibling load happens in
      `agb`'s `_load_mac()` (`agb:2632-2633`), so a `cmd_instances` arm has to catch it. Not
      optional — `tests/test_agb_refresh.py:3363` asserts `(3, "")` and `_read_with`'s
      `assert err == b""` (`:3321`) forbids the traceback an uncaught raise produces
- [ ] ⚠️ **R6 — expect several hundred characters, not ~123.** That estimate assumed a bare inline
      arm; a `cmd_instances` with a try/except and this repo's mandatory reason-comment is
      realistically 250–400. The "measure the final delta" instruction below saves it, but the
      number will surprise
- [ ] implement `--plist <path> --arg <flag>` with `plist_arg`'s existing 0/2/3/other contract
- [ ] implement `--labels` with **its own status contract** (contract 2) and **its own membership
      rule** (contract 3), both stated in the source beside the code
- [ ] implement the default human listing: **one row per instance, `name  label  config`, reusing
      `--labels`' membership rule and error policy** — R5 left it as four words, so its columns, its
      behaviour on an unreadable plist and its exit codes were all invented at the keyboard. It is
      the only mode the stated problem does not require; if the budget bites, this is what defers
- [ ] accept `--launch-agents <dir>`, per the `*_FLAGS` / `*_VALUE_ARGS` convention
- [ ] ⚠️ pin `~/Library/LaunchAgents` and the `com.agbridge` label space as a **cross-file
      agreement** (`install.sh`, `agb-refresh:35`, now Python) beside the existing two in
      `tests/test_install_pkg.py` — patterns compared, never substring greps
- [ ] tests for each mode; each status of **both** contracts; `--launch-agents`; a path with a space;
      a non-agbridge plist absent from `--labels`; a **custom-`--label`** install present in it
- [ ] ⚠️ a **three-locale** test for `--arg`'s byte output (`C`, `POSIX`, `en_US.ISO-8859-1`),
      modelled on `tests/test_agb_refresh.py:3428-3450` — this is the guard for contract 1
- [ ] rendered-plist guard using **`plistlib`**, modelled on `tests/test_agb_refresh.py:2328-2353`,
      non-vacuity asserted
- [ ] **mutation-check each guard separately**
- [ ] run tests

### Task 2: `agb-refresh` swaps its reader

**Files:** `agb-refresh`, `tests/test_agb_refresh.py`

- [ ] ⚠️ **first**, reshape the `refresh` fixture's fake interpreter (`:166-172`, routes only `-c`):
      discriminate on `$4 = instances`, leave `forget-rows` stubbed, and say whether the routed call
      is still logged — `refresh.calls()` ordering is asserted at `:337`, `:353-357`
- [ ] add a **negative control** shaped like `_lib_with(edit=…)` (`:3344-3346`)
- [ ] ⚠️ **R5 — ONE probe, not two.** Replace the `plistlib-ok` probe (`:934-940`) with a
      known-answer probe of `agb instances` itself: after the swap this script runs no other `-c`
      program (`plistlib` is imported by `agb`, not by the shell), and a stdout-compared probe of the
      real command subsumes it — it still catches `--python /bin/echo`
      (`tests/test_agb_refresh.py:2107`) and `/bin/false` (`:2084`) **and** proves the tree, which is
      what makes exit 2 unambiguous afterwards. Fail with a message naming `install.sh mac`
- [ ] ⚠️ **R6 — `test_the_embedded_reader_is_ascii_and_apostrophe_free` (`:3398`) is DELETED, not
      repointed.** R5 said both "one probe, not two" and "repoint `:3398` at the `plistlib-ok`
      probe" — contradictory, since the ONE-probe decision removes that program. After the swap
      `agb-refresh` embeds no `-c` program at all, so the guard has no subject: its non-vacuity
      assertions (`"plistlib" in program`, `"parse_bridge_args" in program`, `len(program) > 500`)
      cannot hold for anything that remains. Delete it **with its reasoning moved into the
      `run_instances` docstring**, since the ASCII/bytes rule it enforced is contract 1 and still
      applies — just to Python now, not to an embedded string
- [ ] ⚠️ update `plist_read_ok`'s message (`:571-575`, *"cannot read a plist: $python exited $1 …
      pass `--python`"*): after the swap exit **1** is what `agb` returns for an `AgbError` and for
      any uncaught exception in `run_instances` (`agb:2733-2736` catches only `AgbError`) — neither
      is a statement about `$python`. Either make `run_instances` catch as broadly as `:453-464`
      does today so 1 is unreachable, or name the right file
- [ ] add a **negative control for the probe itself**, not only for the reader
- [ ] replace `plist_arg`'s body with an `agb instances --arg` call — **all three call sites**
      (`:747` config, `:803` rows, `:992` the named instance's own plist) keep their current callers
      and their current readings of 0/2/3
- [ ] ⚠️ **`bind_label_to_config` is otherwise untouched**: loop, `same_map`, ranks, `nclaim`,
      claimant warning, `${rows:-$config}`
- [ ] ⚠️ pass `--launch-agents "$agentsdir"` on every call — without it the flag silently stops
      working while the suite stays green, because the fixture sets `HOME=tmp_path` (`:297`) so the
      default happens to land in the right place
- [ ] ⚠️ **repoint** `test_the_embedded_reader_is_ascii_and_apostrophe_free` (`:3398`) at the
      `plistlib-ok` probe rather than deleting it; update `_extract_sh`/`plist_arg` fixture
      (`:2742-2804`), `_read_with` (`:3310-3322`), `:3325`, `:3349`
- [ ] ⚠️ **move the differential corpus** and update its docstring: after the move the oracle is
      `parse_bridge_args`, so the argv half approaches `f(x)==f(x)`; what survives is the plumbing
      (where argv starts, which key, the status mapping, the encoding) plus `plistlib` as a genuine
      external authority
- [ ] ⚠️ **R5 — this task inverts NOTHING behavioural.** The R4 list (`:428`, `:708`, `:893`, `:946`)
      was stale from R3 and contradicted this task's own scope: `:708`, `:893` and `:946` are
      **narrowed** runs exercising `bind_label_to_config`, which this task does not touch — and
      `:893`/`:946` are the guards on the flagless-plist → `$DEFAULT_CONFIG` implication that
      contract 3 says must stay separate from the convention. "Inverting" them would delete the
      coverage for the bug R3 was rejected for. `:428` is a bare run and belongs to **Task 3**
- [ ] ⚠️ `tests/test_agb_refresh.py:2802` asserts `err == b""` ("anything on stderr here is a bug in
      the reader"). `agb` writes `USAGE` and `AgbError` text to stderr, so this becomes a much
      stronger claim about `run_instances` — keep it, and say so
- [ ] ⚠️ **R6 — no `drop_ops` arm.** R5 asked for one, which is dead under R5's own module decision:
      with the reader in `agb_mac`, "`agb_mac` present with the reader's module gone" is a
      contradiction — `agb_ops` is on no path `agb instances` takes. `_lib_with(drop_mac=True)`
      (`:3299`) already covers the only real direction
- [ ] **mutation-check each behaviour separately**
- [ ] `sh -n agb-refresh`; run tests

### Task 3: `agb-refresh` sweeps every instance, and fails safe

**Files:** `agb-refresh`, `tests/test_agb_refresh.py`

- [ ] bare `agb-refresh` re-execs `"$0"` once per label from `--labels`, aggregating exit codes
- [ ] ⚠️ **forward every flag explicitly** — `--dry-run` above all (a sweep that drops it performs a
      real refresh for a command that promised to change nothing), plus `--no-close`, `--agb`,
      `--python`, `--launch-agents` (the fixture always passes the first two, `:298-300`)
- [ ] ⚠️ **R6 — `--key` SWEEPS; "not in this map" is only an error if NO instance had it.** R5 left
      three incompatible readings across Tasks 3 and 4 ("requires one instance", "narrowing flag,
      today's semantics", "refused without an instance"). Decided in favour of the plan's own
      thesis — *you should not have to know which instance* — and it is also what keeps the
      documented recipe working unchanged: `agb-refresh --key a3f9c1e0` (`agb-refresh:13-16`, `:58`,
      `docs/commands.md:723`) finds the key wherever it lives. A key belongs to exactly one map, so
      the sweep reports which instance had it and fails only when none did
- [ ] `--rows` narrows to one run (`agb-refresh` has **no** `--placements` — that is `forget-rows`',
      Task 4)
- [ ] a child's failure is recorded, the sweep continues, exit non-zero with a summary naming it
- [ ] ⚠️ an instance left **without a running bridge is an ERROR** — it is what justifies bare-is-all
- [ ] ⚠️ **do not break the existing no-plist recipe.** Today a bare `agb-refresh` on a Mac with no
      plist still forgets and warns (`:1608`) — the commonest recipe there is (`SKILL.md:248`). Decide
      what "no instances found" means without regressing it
- [ ] ⚠️ **R5 — this task's real breaks, named.** `tests/test_agb_refresh.py:517`
      (`test_the_convention_is_still_the_fallback_when_no_plist_answers`) and `:2281` both assert
      `rc == 0` on a run where no plist exists for the label, so `bootstrap` is skipped and `:1607`
      warns. "An instance left without a running bridge is an ERROR" makes both non-zero — and note
      it fires on the **single-instance** path, not only the sweep. ⚠️ **R6 — narrow the rule to the
      sweep; do NOT invert them.** Inverting silently repeals a documented promise: `agb-refresh:966-968`
      and `docs/design.md:1911-1914` both state that a Mac whose plist was never rendered can still be
      refreshed by name, and inverting `:517` makes `agb-refresh --instance hostb` fail there. "No
      running bridge is an error" is a **sweep** rule, which is where it earns its keep
- [ ] ⚠️ **re-reason the bare-run block**, which now executes through a re-exec: `:329`, `:341`,
      `:360`, `:370`, `:428`, `:569`, `:1282`, `:1303`, `:1332`, `:1373`, `:1827`, `:3349`, `:3465`,
      `:3575`, `:3640`, `:3662`, `:3695`, `:3752`, `:3821`. Most keep passing with one label present
      — which is the pass-vacuously shape this repo keeps finding. `:569` ("a plain refresh ignores a
      named instance's bridge") and `:3752` assert properties of a command that no longer has them
- [ ] ⚠️ **state how the script names itself for the re-exec.** `refresh.run` invokes
      `["sh", SCRIPT, …]` with SCRIPT absolute (`:296-299`), so `$0` always has a slash **in the
      suite** — installed on `$PATH` it works, but `sh agb-refresh` from its own directory gives a
      `$0` with no slash and no `$PATH` entry, and the sweep dies. Same class as the
      ProgramArguments-is-four-elements-longer finding: a harness simpler than reality. Record the
      rule and test one invocation whose `$0` is not the fixture's
- [ ] ⚠️ move Task 2's `--launch-agents "$agentsdir"` forwarding here — under `--plist <path>` it
      does nothing at the `--arg` sites; it is `--labels` that needs it
- [ ] ⚠️ **the `trap` lives in the CHILD** — the process dying between its `bootout` (`:1539`) and
      `bootstrap` (`:1601`). Name the signals; re-raise for the right exit status
- [ ] tests: two instances in order with their banners; A fails → B still refreshed, A **restarted
      anyway**, exit non-zero, summary names A; a failure in **each** phase (stop, forget, restart);
      nothing left stopped; one test per forwarded flag, `--dry-run` first
- [ ] ⚠️ test that **B's live bridge does not hold A's wait** — `cmdline_is_ours`'
      `ambiguous`/`unknown` branches (`:1287-1298`, `:1477-1490`) treat an unattributable bridge as
      ours, so a sweep could wait 10 s per instance and warn on the commonest invocation there is
- [ ] **mutation-check separately**: child failure fatal; trap dropped; a flag not forwarded
- [ ] `sh -n agb-refresh`; run tests

### Task 4: `close-done` goes plural; `forget-rows` gains `--all`

**Files:** `agb_mac`, `tests/test_bridge_rows.py`

- [ ] discovery module already decided in Task 1; give both commands `--launch-agents` (or a
      parameter on the shared helper) — they are driven **in-process** by `tests/test_bridge_rows.py`,
      so without it the sweep is only testable by monkeypatching `$HOME`
- [ ] ⚠️ decide the **no-launchd case**: plist discovery makes both fail on a Mac with a config but
      no job, where `docs/cookbook.md:161-163`'s bare `close-done` works today
- [ ] bare `close-done` acts on every instance, **through `instance_paths`**
- [ ] bare `forget-rows` is **refused**, naming `--all`; `--all` opts in; `--config` narrows
- [ ] ⚠️ **R5 — `--rows` / `--placements` / `--key` are themselves NARROWING flags; they do not
      require a separate instance.** R4 said both "require one instance" and "bare
      `forget-rows --rows <path>` is a documented recipe", which contradict. Decided: naming a map
      *is* naming what to act on, so such a run keeps **today's** semantics exactly — one map, config
      defaulting to `agb.config_path()` unless `--config` is given. Only a run naming **no** map
      sweeps. That preserves the documented recipe (`docs/cookbook.md:503`, `SKILL.md:250`) and the
      ~23 tests at `tests/test_bridge_rows.py:1607`–`:2832` that pass `--rows` as a test seam, and it
      introduces no new hazard: the mixed-pair case is already documented at `agb_mac:2688-2694`
- [ ] ⚠️ say in the CHANGELOG that `--rows` alone still implies the default config — it is the one
      place the old default survives, deliberately, and a reader who has just been told the default
      is gone will otherwise be surprised
- [ ] banner once per instance; same failure polarity as Task 3
- [ ] define and test the **aggregate exit code** — `run_close_done` returns 0 unconditionally
      (`agb_mac:2958`), `run_forget_rows` returns 1 only for missing keys (`:2892`)
- [ ] `ast` reachability guard that both resolve through `instance_paths`, **non-vacuity first**
      (`assert "run_close_done" in reachable`)
- [ ] invert `tests/test_bridge_rows.py:2583` (`test_forget_rows_uses_the_default_map_when_none_is_named`,
      a live-use regression guard) and `:3037`
      (`test_the_banner_names_the_default_config_when_no_flag_is_passed`, parametrised over **both**
      commands) **in this task**, reasoning updated, not deleted
- [ ] ⚠️ **R6 — the in-process sweep needs the same two policies the shell side got.** State them in
      the same words as contracts 2 and 3: a plist carrying **no `--config`** reads as
      `agb.config_path()` (the `bind_label_to_config:766-769` reading), and **one unreadable plist
      mid-sweep is fatal, not skipped**. Left unstated this is invariant 12's eleventh instance
      arriving in-process, where it closes `[done]` rows in the wrong instance's map and reports
      success
- [ ] ⚠️ decide whether `--all` beside a map flag (`forget-rows --all --rows X`) is an error or the
      map wins — one line either way
- [ ] tests: bare `close-done` reclaims `[done]` rows in both maps, others untouched;
      `forget-rows --all` writes **each instance's own** placements; bare `forget-rows` refused;
      `--rows`/`--placements` keep today's single-map semantics; `--key` sweeps and names the
      instance that had it, failing only when none did
- [ ] **mutation-check each guard separately**, including a `--key` sweep that reports success when
      no map had the key
- [ ] run tests

### Task 5: Verify acceptance criteria

- [ ] bare `agb-refresh` refreshes every instance; each narrowing flag works
- [ ] a failing instance leaves **nothing** stopped; an instance without a running bridge is an error
- [ ] `agb instances` answers all three modes; a non-agbridge plist is not in `--labels`
- [ ] a **custom `--label`** and a **`--config`-only** install are both discovered and actable
      (suite-level — a `--config`-only install is uncreatable once the follow-up plan lands)
- [ ] bare `close-done` acts on both maps; bare `forget-rows` refused; `--all` sweeps
- [ ] the multi-claimant warning still prints on `agb-refresh --config <path>`
- [ ] `len(agb)` in **characters** under the budget; any raise matches its recorded measurement
- [ ] the corpus passes against `plistlib` and `parse_bridge_args`
- [ ] full suite; `sh -n install.sh && sh -n agb-refresh`

### Task 6: Migrate the live Mac (human-run, no code)

Runs **after** the code, deliberately: the new code sweeps a Mac that still has a default instance
perfectly well — the default label is just another label — so there is no window in which a bare
`agb-refresh` silently succeeds on nothing.

⚠️ **Boot out and WAIT before moving anything** — a live bridge holds the rows map in memory and
merges-then-writes, so a move underneath it is silently lost. ⚠️ **Rows minted by the default install
carry no `--config`** (`agb_mac:1384-1406`), so they must be re-minted.

- [ ] pick the name; `launchctl bootout gui/$(id -u)/com.agbridge`; poll until `pgrep -f 'agb bridge'`
      loses its pid
- [ ] `mkdir -p ~/.config/agbridge/<name>/`; move `config`, `rows`, `placements`; move the logs
- [ ] re-run with **every mandatory flag** — `--instance` needs `--statedir` (`install.sh:446`), the
      mac role needs `--feed-host` and `--agb-remote-path` (`:482-483`):
      `sh install.sh mac --instance <name> --statedir <p> --feed-host <t> --agb-remote-path <p>`
- [ ] `rm ~/Library/LaunchAgents/com.agbridge.plist`; confirm the new label bootstrapped
- [ ] **re-mint**: `agb-refresh --instance <name>`
- [ ] verify both instances' rows present once each; clicking each reaches the right machine
- [ ] record the ordered steps in `CHANGELOG.md` as an upgrade note for ≤ 0.5.0

### Task 7: [Final] Documentation

- [ ] ⚠️ `docs/design.md:1450-1920` — ~450 lines specifying `plist_arg`: its statuses, the `plistlib`
      sniff, the label-space guard, the ranks, the claimant counting, the `[ -e ]` split. design.md is
      "the authority and is reconciled against the implementation", so this is the **largest** doc
      site, not §5's cells
- [ ] `docs/design.md` §5: limitation 1 mitigated by the default; limitation 6 **mitigated** (not
      resolved); the rejected discovery designs; ⚠️ limitation 3 gains a line — after Task 6 there is
      **no default config on the Mac at all**, so `doctor`, `status-line` and `prune --via-ssh`
      describe a file that does not exist
- [ ] ⚠️ `docs/commands.md:786-861` (the `--config` scan), plus `:221`, `:668` (`--config` default
      cells), `:727` (`--instance` default cell), `:728`
- [ ] `docs/cookbook.md`: ⚠️ `:161-163`, `:364-366` (the "act on the default instance — successfully,
      which is the trap" table **and** the line after it), `:503`
- [ ] `README.md`: ⚠️ `:112` ("run the helpers **without** `--instance` and they act on the default
      instance"), `:329`; test count
- [ ] `.claude/skills/agbridge/SKILL.md`: instance recipe, symptom table, ⚠️ `:250`, `:254`
- [ ] ⚠️ in-code prose stating the old default: `agb_mac:2814-2820`, `:2921-2923`
- [ ] ⚠️ `docs/design.md:1937-1943`'s limitation-1 table has a row `agb-refresh (the mistake)` →
      `instance: (default)`, annotated "the one thing this line cannot be allowed to be wrong about".
      That cell becomes false
- [ ] ⚠️ `agb-host-line:99` prints `grep feed_host ~/.config/agbridge/config   # on the Mac`, naming
      a file that will not exist after Task 6. (Its emitted Mac-side snippet at `:116-118` already
      globs `~/.config/agbridge/*/config` and is fine — only the prose is wrong.) The script is
      otherwise not in this plan
- [ ] record the measured cost, so it is not rediscovered as a regression: today's reader is
      ~21.5 ms/call; `agb` as `__main__` plus one sibling load is ~24.7–26.1 ms (no `.pyc`, because
      `__main__`), and `bind_label_to_config` calls it up to twice per plist — so 30 LaunchAgents
      goes ~1.3 s → ~1.6 s. Not a reason to change anything
- [ ] ⚠️ `CLAUDE.md`: invariant 14 is a **counted** list currently reading "Three cross-file
      agreements" — Task 1 adds a fourth (the LaunchAgents dir + label space), so the count moves
- [ ] ⚠️ `CLAUDE.md`: invariant 12's corollary; the bytes-vs-characters correction; any budget
      decision; **and if Task 1 put `run_instances` in `agb_ops`, the Known-gaps claim that it
      "never loads `agb_mac`"** — also asserted in prose at `tests/test_identity.py:1000`
- [ ] ⚠️ `CHANGELOG.md`: add to the **existing** `## Unreleased` (`:9`) — do not create a second
      heading. Name the symptom; carry Task 6's upgrade note; say that symmetry is a **convention
      until the follow-up plan lands**
- [ ] decide `VERSION` (`agb:24`): a new command plus two breaking CLI changes argues **0.6.0**
- [ ] ⚠️ **bump `VERSION` only — do NOT cut the release.** No `git push`, no `git tag`, no GitHub
      Release. `CHANGELOG.md` stays under `## Unreleased`. The owner does all of that by hand
- [ ] write the follow-up plan for `install.sh mac --instance` (the `mac_args` fixture, the
      `dist/com.agbridge.plist` question, the transitive `--statedir` decision)
- [ ] move this plan to `docs/plans/completed/`

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
