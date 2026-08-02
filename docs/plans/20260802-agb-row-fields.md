# `row_fields`: let the user decide what a row shows

*Anchors measured at HEAD `ba17783`, 1884 tests green. They drift — grep for the symbol, not the
line number.*

## Overview

A row's title is `<label> · <host> · <cwd> · <pane> [· <beat>]`, built in one place
(`agb_mac.row_title`) and never configurable. On a real four-row sidebar that runs **69–77
characters**:

```
73  agbridge_dev · dev01-container-xterm-032 · /home/user/agbridge-public · %15
69  api_tests · dev01-container-xterm-032 · /home/user/api_gateway_svc · %0
77  data_pipeline_v2 · dev01-container-xterm-032 · /home/user/data_pipeline_v2 · %7
71  agb-public · dev01-container-xterm-032 · /home/user/agbridge-public · %23
```

Three findings, and the third is arguably a design bug rather than a preference:

1. **`host` is identical on every row** — 25 of ~72 characters, **35% of the line carrying zero
   information** on a single-host setup.
2. **`cwd` largely repeats `label`.** Row 3 is `data_pipeline_v2 · … · /home/user/data_pipeline_v2`
   — the same word twice.
3. ⚠️ **`pane` is last, and it is the disambiguator.** Two agents in two panes of one tmux session
   share label, host, cwd **and** tmux — `%15` is the only thing separating their rows, and it is
   the first thing agterm clips.

This adds `row_fields`, a Mac-side per-instance config key naming which fields render and in what
order:

```ini
row_fields = label,cwd:base,pane      # agbridge_dev · agbridge-public · %15   (36 chars, was 73)
```

The default is byte-identical to today, so this is invisible to anyone who does not set it.

## Context (from discovery)

- **`agb_mac.row_title` (`:1540`) is the only place a title is built**, called from exactly two
  sites: `_create_row` (`:1870`, the `session new --name`) and `_title` (`:2336`, every rename).
  Both are `RowRenderer` methods, so `self.settings` is in scope at both.
- **Related constants**: `TITLE_SEP` (`:1345`), `TITLE_STALE`/`TITLE_DONE` (`:1346-1347`),
  `BEAT_LATE = agb.BEAT_INTERVAL * 2 = 30.0` (`:1454`), `beat_age_text` (`:1528`).
- **The config pattern to copy** is `config_seconds` (`agb_mac:2474`) beside `config_flag`: never
  raises, absent and configured-to-nothing stay distinguishable, one line in `render_settings`
  (`:2401`).
- **The agterm `--name` is uncapped** — the full string goes over and agterm clips it visually.
  That stays true; a width cap is out of scope. (`clean_row_title` at `agb_mac:837` caps the
  *persisted* title at `ROW_TITLE_MAX = 512`, which no real title approaches.)
- **The warn seam is `bridge_sink` (`:2506`), confirmed rather than assumed.** `render_settings` is
  a module function with no warn channel; `bridge_sink` takes `warn=`, calls `render_settings` at
  `:2516`, and builds the renderer two lines later. It is `render_settings`' **only** production
  caller, and its own only caller is `run_bridge` (`:2624`) — so *"warn once"* is structural, not a
  rule someone has to keep. The `no_agterm` early return (`:2514-2515`) is the single skip and it
  builds no renderer. `RowRenderer.__init__` is not needed.
- ⚠️ **A rendered title is persisted, so changing `row_fields` has a visible cross-restart effect.**
  `_title` stores the *body* via `rows.set_title` (`:2350`), and on a fresh process `_render_stale`
  paints `[?] ` + `rows.title_for(key)` while `self.seen` is still empty (`:2337`). So the first
  `[?]` after a bridge restart shows titles built with the **previous** field list. It self-heals on
  the next upsert and `set_title` is dirty-checked so there is no churn — but it is exactly the
  shape of *"I changed the key and half my rows look wrong"*, so it belongs in the docs as a known
  limit rather than in a bug report.
- **Both new names are free**: `parse_row_fields` and `ROW_FIELDS` appear nowhere in `agb`,
  `agb_mac`, `agb_ops`, `agb-refresh`, `tests/` or `docs/`.
- `tests/test_bridge_rows.py` is the only test file that touches `row_title`.

## Development Approach

- **Testing approach**: **regular** (code first, then tests), matching this repo — with the
  standing addition that **every new guard is mutation-tested**: break it, confirm a *named* test
  fails, restore. A guard whose removal keeps the suite green is not a guard.
- ⚠️ **Every mutation must name a victim before it is written into this plan.** Three mutations
  across the last two plans could not fail any listed test, and two of those were only caught by a
  later review pass.
- Complete each task fully before the next. **All tests pass before the next task starts**
  (`python3 -m pytest tests/ -q`, 1884 at the time of writing, ~63 s).
- Python 3.6.8 is the floor: no f-strings with `=`, no dataclasses, no walrus.
- `➕` for discovered tasks, `⚠️` for blockers.

## Testing Strategy

- **Unit tests** in `tests/test_bridge_rows.py`, driven by the existing `Harness`/`Runner` seams —
  no agterm, no network. What is asserted is the rendered string and the recorded argv.
- **No e2e tests in this project.** The substitute is the live-agterm check under Post-Completion —
  but note this feature is *unusually* safe on that front: it changes a string agbridge composes,
  not a call it makes, so there is no new `agtermctl` contract to be wrong about.
- **Mutation testing is the acceptance bar for guards.** Substring greps over source do not count;
  they pass by matching the comment that describes the rule.

## Progress Tracking

- Mark completed items `[x]` immediately.
- `➕` for newly discovered tasks, `⚠️` for issues.
- Keep the plan in sync with the work actually done.

## Solution Overview

A parser and a widened `row_title`:

```python
ROW_FIELDS = ("label", "host", "cwd", "pane", "beat", "key")
ROW_FIELDS_DEFAULT = (("label", ""), ("host", ""), ("cwd", ""),
                      ("pane", ""), ("beat", ""))

def parse_row_fields(text):
    """(fields, error). `error` is None, or a message naming the offender."""
    ...        # -> ROW_FIELDS_DEFAULT on absent/empty/invalid

def row_title(session, now=None, prefix="", fields=None):
    ...
```

**Key design decisions:**

1. **A field list, not a template.** `{label} · {cwd|base}` was considered and rejected: it needs
   placeholder parsing, unknown-field handling, and has no good way to report a broken template
   except a blank sidebar. A comma list is a config value people can write from memory.
2. ⚠️ **Automatic elision was rejected, and it is the idea worth not re-deriving.** The bridge
   *could* drop any field identical across all current rows — host vanishes with one host,
   reappears with two — which needs no config and does the right thing unprompted. It is rejected
   because a title would become a function of **global** state: starting an agent on a second host
   would silently rewrite every existing row's title, a rename storm, and it defeats the `titles`
   suppress-if-unchanged memory, which compares against the last string sent for *that* key.
3. **`:base` is defined only on `cwd`, and refused elsewhere.** On `label` or `pane` it would be a
   no-op, and a modifier that silently does nothing is a typo nobody ever finds.
4. **An unknown field rejects the WHOLE list**, falling back to the default with one warning.
   Blunt on purpose: dropping just the bad field leaves you with most of what you asked for, and a
   *missing* field is exactly what goes unnoticed. Rejecting the lot means you edit, restart, and
   **nothing changes at all** — an unmissable signal, with the reason in the log.
5. **`key` is new and off by default** — the 8-character prefix `agb rename <key>` accepts
   (`docs/commands.md:428`). Useful when renaming, noise otherwise. ⚠️ `agb prune --key` is **not** a
   consumer: it reads `sessions/<host>/<key>.state` **by exact name** (`agb_ops.entry_for:1203-1204`,
   *"read by name -- never listed"*), so a truncated key answers `STATE_GONE`. An earlier draft
   claimed both, which would have propagated into the docs.
6. **`beat` is droppable but pointless to drop**: `beat_age_text` returns `""` below `BEAT_LATE`
   (30 s), so it costs no width on a healthy agent and dropping it only loses a warning. Say so in
   the docs rather than special-casing it.

## Technical Details

**The field vocabulary:**

| field | renders | note |
|---|---|---|
| `label` | `agbridge_dev` | ⚠️ **`label or key or "?"`**, the full chain `row_title` uses today (`agb_mac:1550`). Not plain `session["label"]` — see below |
| `host` | `dev01-container-xterm-032` | already domain-stripped by `own_host()` |
| `cwd` | `/home/user/agbridge-public` | `cwd:base` → `agbridge-public`. ⚠️ `os.path.basename(v.rstrip("/")) or v` — bare `basename` returns `""` for `/home/user/` and for `/`, silently vanishing the field |
| `pane` | `%15` | the only thing separating two agents in one tmux session |
| `beat` | `12m` | **empty unless late** — see decision 6 |
| `key` | `a9c35465` | first 8 of a **16**-character key; **not** in the default |

⚠️ **`label` keeps all three fallbacks, and getting this wrong breaks two things at once.** Today's
line is `parts = [str(session.get("label") or session.get("key") or "?")]`. Rendering only
`session.get("label")` would make the *default* list drop the leading field on a label-less record —
`host · cwd · pane` where today it is `<key> · host · cwd · pane` — a silent change to the default
that a byte-identity test on a *full* record cannot see. And `row_title` coerces a non-dict to `{}`
(`agb_mac:1548-1549`), where the chain's terminal `"?"` is the only thing standing between the
caller and a title that is nothing but the prefix.

**The parse contract**, mirroring `config_seconds`:

| value | result |
|---|---|
| absent | default |
| `` / whitespace | default (absent and configured-to-nothing must stay distinguishable) |
| `label,cwd:base,pane` | those three, in that order |
| ⚠️ `label, cwd:base, pane` | **the same** — each item is stripped |
| `LABEL,Cwd:Base` | the same — names and modifiers are case-folded |
| `label,workspace` | **default** + error naming `workspace` and listing the valid names |
| `label:base` | **default** + error: `:base` is only defined on `cwd` |
| `cwd:basename`, `cwd:`, `cwd:base:base` | **default** + error: unknown modifier |
| duplicate (`label,label`) | accepted — harmless, and refusing it is a rule with no failure behind it |

⚠️ **Per-item whitespace is the most likely first-use failure, and it is silent.**
`agb.parse_config` strips the whole *value*, not the items — verified: `row_fields = label, cwd:base,
pane` arrives as `'label, cwd:base, pane'`. Without a per-item strip, `" cwd:base"` is an unknown
field, decision 4 rejects the **whole list**, and the user's first attempt renders the default with
only a line in a log they are not watching. Writing a comma list with spaces is what everybody does.

⚠️ **It must never raise.** This runs on the render path, where an exception wedges a paint.

**Two invariants the config must not be able to break:**

- **`[?]` and `[done]` are prefixes, not fields.** Prepended regardless of `row_fields`, because
  `idle` renders as *no glyph* — without the marker a dead row is pixel-identical to a live idle
  one. A cosmetic setting must not switch off a safety property.
- ⚠️ **The title can never be empty**, and the reachable way in is not the obvious one. It is not a
  missing label — the `label` field's own chain ends in `"?"`. It is **`row_fields = beat` on a
  healthy agent**: a valid, parseable, single-field list whose field renders `""` below `BEAT_LATE`.
  Two distinct failures follow, and neither is loud:
  - `_title` with no prefix returns `False` (`agb_mac:2338-2341`), so **the rename never happens** —
    agterm keeps its own default name on that row, permanently and silently;
  - `_create_row` sends `session new --name ""`.

  So the join needs its own fallback — label, then key — *on top of* the `label` field's chain,
  and the two must not be confused: only the join-level one is reachable by config.

**Where the pieces go** (`agb_mac` at `ba17783`):

| # | site | change |
|---|---|---|
| 1 | near `TITLE_SEP` `:1345` | `ROW_FIELDS`, `ROW_FIELDS_DEFAULT` |
| 2 | beside `config_seconds` `:2474` | `parse_row_fields` |
| 3 | `row_title` `:1540` | a `fields=None` parameter, defaulting to `ROW_FIELDS_DEFAULT` |
| 4 | `_create_row` `:1870`, `_title` `:2336` | pass `self.settings.get("row_fields")` |
| 5 | `render_settings` `:2401` | `"row_fields"` + `"row_fields_error"` |
| 6 | `bridge_sink` `:2506` | warn once if `row_fields_error` is set |
| 7 | `agb_ops:228` `CONFIG_KEYS` | add `row_fields` |

## What Goes Where

- **Implementation Steps**: code, tests and docs in this repo.
- **Post-Completion**: seeing it in a real sidebar, which needs a Mac.

## Implementation Steps

### Task 1: `parse_row_fields` and the field vocabulary

**Files:**
- Modify: `agb_mac`
- Modify: `tests/test_bridge_rows.py`

- [ ] add `ROW_FIELDS` and `ROW_FIELDS_DEFAULT` near `TITLE_SEP` (`agb_mac:1345`), with the reason
      the default exists in one place: `render_settings` and `row_title`'s own fallback must agree
- [ ] add `parse_row_fields(text)` beside `config_seconds` (`agb_mac:2474`), returning
      `(fields, error)` per the parse-contract table
- [ ] docstring records **why an unknown field rejects the whole list** (decision 4) and **why it
      never raises** (render path)
- [ ] ⚠️ check the new tuples do not trip `test_the_status_vocabulary_has_exactly_one_source` — it
      walks the **whole** `agb_mac` AST including docstrings and fails on any single string
      containing all four status words. The field names are unrelated, but the docstring is where
      an example could accidentally list them
- [ ] write a table-driven test for `parse_row_fields` covering **every row** of the contract table:
      absent, empty, whitespace, valid list, order preserved, `cwd:base`, ⚠️ **per-item whitespace**
      (`label, cwd:base, pane`), case-folding, `:base` elsewhere refused, unknown *modifier*
      refused (`cwd:basename`, `cwd:`, `cwd:base:base`), unknown field refused, duplicates accepted
- [ ] write a test that the error message **names the offending field** and lists the valid ones —
      a warning that says only "bad row_fields" makes the user diff their config by hand
- [ ] mutation-test: accept an unknown field instead of rejecting → the contract table's
      `label,workspace` case must fail
- [ ] mutation-test: drop the per-item strip → the `label, cwd:base, pane` case must fail
- [ ] run `python3 -m pytest tests/ -q` — must pass before Task 2

### Task 2: `row_title` renders the chosen fields

**Files:**
- Modify: `agb_mac`
- Modify: `tests/test_bridge_rows.py`

- [ ] add `fields=None` to `row_title` (`agb_mac:1540`), defaulting to `ROW_FIELDS_DEFAULT`
- [ ] render each field per the vocabulary table; `cwd:base` via `os.path.basename`
- [ ] ⚠️ keep the empty-title fallback: label, then key. Never return just the prefix
- [ ] ⚠️ **no time calls may enter `row_title`** — `test_the_renderer_never_consults_the_macs_own_clock`
      names it explicitly, and every age here is `feed now - beat`, both server-stamped
- [ ] pass `self.settings.get("row_fields")` from `_create_row` (`:1870`) and `_title` (`:2336`)
- [ ] ⚠️ write the test that matters most first: **the default renders byte-identically to today**.
      Assert the exact string, and do it for **two** records — a full one, *and* one with no label,
      which is the only way to see the `label`-field chain being shortened
- [ ] write tests: each field renders what it claims; order is respected (assert a *reordered* list
      produces a different, specified string — not just that all fields are present)
- [ ] write tests: `cwd:base` shortens; ⚠️ and does not vanish on a trailing slash (`/home/user/`) or
      on `/` — bare `os.path.basename` returns `""` for both
- [ ] ⚠️ write a test that `key` renders the **first 8 of a 16-character key**. The `wire()` fixture
      uses 8-char keys (`"aaaa1111"`) while real keys are 16 (`agb:406` `KEY_BYTES = 8` → hex), so a
      test built on the fixture default proves nothing about truncation
- [ ] write tests: `beat` renders only past `BEAT_LATE` and is absent below it — ⚠️ under a
      **non-default** list, since the two existing beat tests both run the default
- [ ] ⚠️ write the empty-title tests, and target the case config can actually reach:
      `row_fields = beat` on a **healthy** record. With a prefix it must still render
      `[done] <fallback>`; without one it must render a non-empty body, **not** return `False`
- [ ] ⚠️ write a test that **`[done]` and `[?]` survive an EMPTY-BODY field list**, not merely a
      short one. `row_fields = key` always renders something, so it proves only that a prefix
      survives a short list — the invariant is about the body being empty
- [ ] ⚠️ write the plumbing test: drive an upsert through the `bridge` fixture with
      `settings={"row_fields": …}` and assert **both** the recorded `session new --name` and the
      `session rename` argv carry the configured title. Every other test here calls `row_title`
      directly, so dropping `fields=` at `_create_row` (`:1870`) would have **no victim** — and it
      is invisible in the sidebar, because `_render_upsert` calls `_title` immediately after
      `_create_row` (`agb_mac:1945-1950`) and renames the row one call later
- [ ] mutation-test, each naming its victim: drop the prefix → the safety test; ignore the field
      order → the order test; let the join-level fallback go → the `beat`-only test; ignore `:base`
      → the basename test; drop `fields=` at `_create_row` → the plumbing test's `--name`
      assertion; drop it at `_title` → the plumbing test's `rename` assertion
- [ ] run `python3 -m pytest tests/ -q` — must pass before Task 3

### Task 3: Wire the config through, and warn on a bad value

**Files:**
- Modify: `agb_mac`
- Modify: `agb_ops`
- Modify: `tests/test_bridge_transport.py`
- Modify: `tests/test_core.py`

- [ ] add `"row_fields"` and `"row_fields_error"` to the `render_settings` return dict (`:2401`),
      read off the **config dict it was handed** — never a fresh `agb.read_config()`, which
      `test_the_bridge_reads_its_config_exactly_once` pins
- [ ] warn once from `bridge_sink` (`:2506`), which is confirmed as the right seam: it takes `warn`
      and calls `render_settings` at `:2516`, two lines before building the renderer, and it is
      `render_settings`' only production caller. ⚠️ **Guard for `warn=None`** — several existing
      call sites pass nothing (`tests/test_bridge_rows.py:579`, `:619`, `:676`), as
      `load_rows(settings["rows"], warn)` already tolerates
- [ ] add `row_fields` to `agb_ops.CONFIG_KEYS` (`:228`)
- [ ] add a line to the hand-written config blob in `test_parse_config_reads_the_documented_keys`
      (`tests/test_core.py:211-212`) — it iterates `CONFIG_KEYS` asserting `key in values`, so
      **adding** the key without the blob line does go red. It is the *removal* mutation that only
      `test_the_documented_key_list_is_pinned_by_name` catches, so add the key to that hardcoded
      set too
- [ ] write a test that `render_settings` surfaces the parsed fields, and the default when absent
- [ ] write a test that a bad value produces a warning through the real channel — not just a
      non-None error in the dict. ⚠️ Assert the warning **reaches the warn callable**, or this
      proves the parser and not the plumbing
- [ ] mutation-test: drop the key from `CONFIG_KEYS` → the hardcoded test must fail; swallow the
      warning → the plumbing test must fail
- [ ] run `python3 -m pytest tests/ -q` — must pass before Task 4

### Task 4: Docs

**Files:**
- Modify: `README.md`
- Modify: `docs/commands.md`
- Modify: `docs/design.md`
- Modify: `docs/cookbook.md`
- Modify: `docs/agtermctl.md`
- Modify: `agb_mac` (one docstring)
- Modify: `.claude/skills/agbridge/SKILL.md`
- Modify: `CHANGELOG.md`

- [ ] `README.md` config table — one row, naming the default and the six field names
- [ ] `docs/commands.md` — a section beside the row-title description: the vocabulary table, the
      `:base` modifier, the whole-list rejection and why, and ⚠️ that `beat` costs no width below
      30 s so dropping it only loses a warning
- [ ] `docs/design.md` config table — one row
- [ ] ⚠️ `docs/design.md:1365`, the §5 per-instance row (*"`statedir`, `feed_host`, `mac_id`, the
      notification switches"*) — this one **does** need an edit, unlike the last plan's identical-
      looking item. That wording covered `notify_on_completed_after` because it *is* a notification
      switch; `row_fields` is not, so the list no longer describes what moves with an instance
- [ ] ⚠️ **six places state the title format as fixed**, and none is a config table — so a
      config-table-only pass leaves the docs contradicting the feature, silently, because there is
      no doc-consistency test: `docs/design.md:556` (the design authority's own statement),
      `docs/cookbook.md:145`, `docs/commands.md:432`, `docs/agtermctl.md:70` and `:113`, and
      `agb_mac:853` (`format_rows`' docstring, *"what a row **is**"*). Reword each to name the
      default rather than the only possibility
- [ ] ⚠️ document the **cross-restart** effect: the first `[?]` paint after a bridge restart shows
      titles built with the *previous* `row_fields`, because `_title` persists the rendered body and
      `_render_stale` uses it before any record has arrived. Self-heals on the next upsert; worth a
      sentence so it is not reported as a bug
- [ ] `.claude/skills/agbridge/SKILL.md` config table
- [ ] ⚠️ `CHANGELOG.md` — **append to the existing `## Unreleased`** (`:9`). Do not create a second
      heading. ⚠️ It currently carries `### Changed` (`:11`) and no `### Added`; a new key is an
      addition, so that sub-heading has to be created inside the existing section. The entry says
      *why*: the measured 35% of a line spent on a constant host, and that
      `pane` — the disambiguator — is what agterm clips first
- [ ] record the rejected alternatives in the entry (automatic elision, format templates), so they
      are not re-proposed
- [ ] ⚠️ state that, like every bridge-side key, it needs `agb-refresh` to take effect
- [ ] no tests (documentation only); run the suite anyway to confirm nothing regressed

### Task 5: Verify acceptance criteria

- [ ] the default field list renders byte-identically to today
- [ ] `row_fields = label,cwd:base,pane` renders `agbridge_dev · agbridge-public · %15`
- [ ] a reordered list renders in the order written
- [ ] an unknown field leaves the sidebar exactly as it was **and** logs a warning naming it
- [ ] `[done]` and `[?]` appear with any field list, including a single-field one
- [ ] no title is ever empty
- [ ] `agb doctor` does not report `row_fields` as an unknown key
- [ ] `row_fields = beat` on a healthy agent still produces a titled row, not a blank one
- [ ] run the full suite: `python3 -m pytest tests/ -q`
- [ ] confirm `agb` is untouched: `python3 -c 'print(len(open("agb").read()))'` must print
      **103198**. ⚠️ The guard counts **characters**, not bytes — `wc -c` is the wrong number

### Task 6: [Final] `CLAUDE.md` and close out

**Files:**
- Modify: `CLAUDE.md`
- Modify: `docs/plans/completed/` (move this file)

- [ ] `CLAUDE.md` — note that the row title is now composed from a configurable field list, and
      that the `[?]`/`[done]` prefixes are deliberately **outside** it
- [ ] re-measure and write the test count the suite actually reports. ⚠️ Do not copy 1884 from this
      plan; these tasks add roughly twenty
- [ ] leave `agb:24` `VERSION` alone — the release is not part of this change
- [ ] move this plan to `docs/plans/completed/`

## Post-Completion

*Needs a Mac with agterm running.*

**Install first.** `git pull` is not enough — the bridge loads `agb_mac` from
`~/.local/lib/agbridge/`. Run `sh install.sh mac …`, then `agb-refresh` (and
`agb-refresh --instance <name>` per instance). ⚠️ **This key is read once at bridge startup**, so
editing it without a refresh does nothing — silently, which cost the better part of an hour on the
finished-turn banner.

**What to look at**, in order:

- set `row_fields = label,cwd:base,pane`, refresh, and confirm the rows shorten and stay readable.
  This is the whole point: does 36 characters read better than 73 at your sidebar width?
- ⚠️ **check a `[done]` row still shows its marker** — the safety property, and the one thing here
  that could matter rather than merely look wrong.
- try `row_fields = label,workspace` (a deliberate typo), refresh, and confirm the sidebar is
  **unchanged** and `~/Library/Logs/agbridge/bridge.err.log` names the bad field. The failure being
  *visible* is the design; if the log is silent, that is the bug.
- with two agents in one tmux session, confirm `pane` still tells them apart wherever you placed it.

**Worth deciding after living with it:** whether the shipped default should change. This plan
deliberately does not touch it — the measurements say `label,host,cwd,pane,beat` is a poor default
for a single-host setup, but that is a separate change with a different blast radius, and it should
be made on experience rather than on one sidebar's numbers.

## What review changed

One pass, thirteen findings, three of them gating. Every claim below was re-verified against source
before being applied — recorded so the next reader does not re-derive it, and because the pattern is
now consistent across four plans: **the defects are never in the feature, always in the test
specifications.**

| Finding | Evidence | Change |
|---|---|---|
| The no-label test could not fail "let the title go empty" | `agb_mac:1550` — the `label` field is *already* `label or key or "?"`, so the field's own chain satisfies the test and the join-level fallback can be deleted with everything still green | vocabulary table states the full chain; byte-identity test gains a **label-less** record; the empty-title mutation re-targeted at `row_fields = beat` on a healthy agent, the only case config can reach |
| Nothing pinned the settings → call-site plumbing | every Task-2 test called `row_title` directly; `_render_upsert` renames one call after `_create_row` (`:1945-1950`), so a dropped `fields=` there is invisible in the sidebar and visible only in argv | added a plumbing test asserting **both** the `session new --name` and the `session rename` argv |
| The safety test used `row_fields = key`, which always renders | the invariant is about an **empty body**; `key` cannot produce one | re-specified against an empty-body list, with and without a prefix |
| Per-item whitespace was unspecified | `agb.parse_config` strips the whole value only — verified: `label, cwd:base, pane` arrives with the spaces, so unstripped items reject the whole list on the way anyone first writes it | contract row + test case + its own mutation |
| Bad modifiers were unspecified | `cwd:basename`, `cwd:`, `cwd:base:base` — the same typo shape decision 3 exists to catch | three contract rows |
| The `key` test would be vacuous | `wire()` uses 8-char keys, real keys are 16 (`agb:406`), so truncation is untested against the fixture default | test specifies a 16-character key |
| Decision 5 was factually wrong about `agb prune --key` | `agb_ops.entry_for:1203-1204` reads *"by name -- never listed"*, so a prefix answers `STATE_GONE` | claim dropped before Task 4 could propagate it into the docs |
| Task 4 listed only config tables | **six** places state the title format as fixed, none of them a config table, and there is no doc-consistency test | all six added, including `agb_mac:853`'s docstring |
| `docs/design.md:1365` was marked verify-only | the previous plan's identical-looking item genuinely needed none, because that row says *"the notification switches"* and the key **was** one. `row_fields` is not | promoted to a real edit |
| Task 1's mutation named no victim | in a plan whose own rules require it | named |
| The `test_core.py` note was backwards | the iterating test **does** go red when adding a key without the blob line; it is the *removal* case the hardcoded test exists for | corrected |
| "No truncation exists anywhere" | `clean_row_title` caps the persisted title at 512 (`agb_mac:837`) | claim scoped to the agterm `--name` |
| `## Unreleased` carries `### Changed`, not `### Added` | `CHANGELOG.md:11` | Task 4 says to create the sub-heading inside the existing section |

**Also folded in from the review's uncovered list:** `cwd:base` on a trailing slash and on `/` (bare
`os.path.basename` returns `""` for both, silently vanishing the field); `beat` asserted under a
non-default list, since both existing beat tests run the default; a `warn=None` guard, since three
existing call sites pass nothing; and the cross-restart persisted-title effect, which is now a
documented limit.

**What review did not find**, recorded because absence is information: every anchor was correct, the
warn seam was real, the task ordering and scope were sound, and the "ignore the field order"
mutation *was* killed as specified.
