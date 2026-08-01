# `install.sh mac` refuses an unnamed instance

> **Follow-up to `20260801-agb-symmetric-instances.md`**, split out of it deliberately rather than
> deferred by accident. That plan removed the *default instance's privilege* from every Mac-side
> command. This one removes the ability to create a nameless instance at all — which is what turns
> symmetry from a convention into a guarantee.

## Overview

After the symmetric-instances change, no Mac-side command privileges the unnamed instance: a bare
`agb-refresh` and a bare `agb close-done` sweep all of them, a bare `agb forget-rows` is refused, and
`agb instances` lists what exists. But `install.sh mac` with no `--instance` still **creates** a
nameless one, so a Mac can carry a default instance beside named ones indefinitely.

That is not a correctness bug — the sweeps handle it, and the default label is just another label —
but it leaves four things true that should not be:

- `agb instances`' listing has a row whose **name column is empty**, and the human reading it has to
  know that means "the unnamed one" rather than "the tool could not tell".
- `agb-refresh` still needs a `(default)` spelling in its banner, and the fall-through that produces
  it is the one that mis-named a custom-`--label` instance (fixed in the parent plan, but the branch
  survives because the case does).
- `agb doctor`, `agb status-line` and `prune --via-ssh` resolve `~/.config/agbridge/config`
  unconditionally (design.md §5, limitation 3). While an unnamed instance exists that file is *some
  instance's* config, which hides the limitation; once every instance is named it is simply absent,
  which is the honest state and the one that motivates giving those three a `--config`.
- The `no --config in the argv ⇒ the default config` implication — which `bind_label_to_config` and
  the in-process sweeps both spell — exists only to describe plists rendered before instances did.

## Why this was NOT in the parent plan

Three concrete costs, each measured rather than estimated, and none of them is about difficulty:

1. **It does not fix the stated problem.** The problem was "a helper acts on the wrong instance and
   reports success". The sweeps fix that. Refusing an unnamed install fixes a *different*, smaller
   thing, and bundling them would have made the breaking change harder to reason about and to revert.
2. **It reaches 24+ test functions through one fixture.** `tests/test_install_pkg.py`'s `mac_args`
   hardcodes `["mac", "--no-load", "--no-probe"]` (`:498`) and is used **140 times** in that file. A
   mandatory `--instance` makes every one of those runs invalid, so the change is mostly a test-suite
   change wearing an installer change's clothes.
3. **It forces a transitive `--statedir` decision that is not obviously right.** `install.sh:446`
   makes `--instance` **require** `--statedir`, for a good reason (an instance inheriting the default
   config's statedir would ssh to the right machine and read the wrong directory, then create it and
   report an empty farm for ever). So mandating `--instance` mandates `--statedir` **on every Mac
   install *and* every upgrade** — and re-running the installer with the original flags is how you
   pick up new code. That is a real ergonomic regression and needs its own answer, not a shrug.

## The questions to answer before writing code

- **What does `dist/com.agbridge.plist` stand for afterwards?** It is the rendered default job, and
  it is the oracle two tests compare the fixture's argv against (the "harness simpler than reality"
  guard). If no install can produce that label, the file is a fossil with live tests pointing at it.
  Options: keep it as the shape-oracle and rename what it documents; render it under a placeholder
  label; or delete it and re-home the oracle. **Not decided.**
- **Does `--instance` stay required-with-`--statedir`?** Three candidate answers: (a) keep the rule
  and accept `--statedir` on every install; (b) require `--statedir` only when the config is *new*,
  so an upgrade of an existing instance can reuse what it already has; (c) let `--instance` default
  its statedir from **its own** config when that config already exists, which is the same
  own-config-first adoption `mac_id` already uses (`docs/commands.md`, `install.sh mac --instance`).
  (c) looks right and needs checking against the failure `:446` exists to prevent.
- **Is `--label`-only still an install?** `install.sh --label <anything>` puts no shape rule on a
  label and `agb instances` deliberately treats such a job as an instance. If `--instance` becomes
  mandatory, does `--label` without it stay legal? Answering "no" narrows `_is_agbridge_instance`'s
  second clause to a case nothing can create; answering "yes" means "unnamed" is still reachable by
  another spelling and this plan buys less than it looks like.
- **What happens to an existing unnamed install on upgrade?** Refusing outright breaks
  `sh install.sh mac …` for every current user. A migration prompt is not available (the installer is
  non-interactive by design). The likely answer is a **deprecation**: warn loudly, name the migration
  steps already written in `CHANGELOG.md`'s *Upgrading from ≤ 0.5.0*, and refuse only in a later
  release.

## Out of scope

- Giving `doctor`/`status-line`/`prune --via-ssh` a `--config`. That is limitation 3 and deserves its
  own plan; it is *motivated* by this one but not blocked on it.
- Anything on the farm side. A farm host's config path is resolved on every `agb hook` invocation
  (invariant 4) and cannot move.

## Development approach

- ⚠️ **DO NOT PUSH.** Local commits only, as in the parent plan.
- **`agb` is at 103,198 of 103,200 characters.** Nothing here should touch it; if something must,
  it needs prose moved into a sibling docstring or a third measured budget raise.
- Python 3.6.8 floor; `sh -n install.sh` must pass.
- The parent plan's bar applies: one mutation-check per guard, `ast` for source structure and
  `plistlib` for rendered artefacts, non-vacuity asserted, a `CHANGELOG.md` entry naming the symptom
  in the same commit.
