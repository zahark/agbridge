# `row_fields`: let the user decide what a row shows

*Anchors measured at HEAD `ba17783`, 1884 tests green. They drift — grep for the symbol, not the
line number.*

## Overview

A row's title is `<label> · <host> · <cwd> · <pane> [· <beat>]`, built in one place
(`agb_mac.row_title`) and never configurable. On a real four-row sidebar that runs **69–77
characters**:

```
73  agbridge_dev · dev01-container-xterm-032 · /home/zk/agbridge-public · %15
69  api_tests · dev01-container-xterm-032 · /home/zk/api_gateway_svc · %0
77  data_pipeline_v2 · dev01-container-xterm-032 · /home/zk/data_pipeline_v2 · %7
71  agb-public · dev01-container-xterm-032 · /home/zk/agbridge-public · %23
```

Three findings, and the third is arguably a design bug rather than a preference:

1. **`host` is identical on every row** — 25 of ~72 characters, **35% of the line carrying zero
   information** on a single-host setup.
2. **`cwd` largely repeats `label`.** Row 3 is `data_pipeline_v2 · … · /home/zk/data_pipeline_v2`
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
  raises, one line in `render_settings` (`:2401`). ⚠️ **Take its `(config, key)` signature too**, not
  a bare `text`: both siblings do their own `(config or {}).get(key)`, so "absent" is handled inside
  rather than becoming a `None` the caller must remember to guard — and `text.strip()` on `None`
  raises `AttributeError` on the render path, which this plan forbids and no test would have named.
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

def parse_row_fields(config, key):
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
   (30 s), so it costs no width on a healthy agent. ⚠️ **But "only loses a warning" understates it,
   and the design says why.** `docs/design.md:278` — *"Beat age is therefore surfaced in the row
   title and never converted into a state"* — makes the title the design's **compensation for
   invariant 1**: agbridge refuses to turn an age into a status, and the age in the title is what it
   offers instead. `row_fields = label,pane` removes that surface entirely, so the sidebar keeps its
   refusal to guess without the number that made the refusal tolerable. Still allowed — it is the
   user's sidebar — but the docs must say what is being given up, not call it a warning.

## Technical Details

**The field vocabulary:**

| field | renders | note |
|---|---|---|
| `label` | `agbridge_dev` | ⚠️ **`label or key or "?"`**, the full chain `row_title` uses today (`agb_mac:1551`). Not plain `session["label"]` — see below |
| `host` | `dev01-container-xterm-032` | already domain-stripped by `own_host()` |
| `cwd` | `/home/zk/agbridge-public` | `cwd:base` → `agbridge-public`. ⚠️ `os.path.basename(v.rstrip("/")) or v` — bare `basename` returns `""` for `/home/zk/` and for `/`, silently vanishing the field |
| `pane` | `%15` | the only thing separating two agents in one tmux session |
| `beat` | `12m` | **empty unless late** — see decision 6 |
| `key` | `a9c35465` | first 8 of a **16**-character key; **not** in the default |

⚠️ **A field whose value is missing or empty is DROPPED from the join, not rendered as an empty
segment.** Today: `host`/`cwd`/`pane` are appended only `if value` (`agb_mac:1552-1555`) and the beat
only `if age` (`:1556-1558`); `label` alone is unconditional. This rule is invisible in any test
whose record has every field populated — which `wire()` does — so an implementation that joins
unconditionally renders `build · box2 · /shared/work/project · ` with a trailing separator and
passes anyway. It needs its own record and its own mutation.

⚠️ **`label` keeps all three fallbacks, and getting this wrong breaks two things at once.** Today's
line is `parts = [str(session.get("label") or session.get("key") or "?")]`. Rendering only
`session.get("label")` would make the *default* list drop the leading field on a label-less record —
`host · cwd · pane` where today it is `<key> · host · cwd · pane` — a silent change to the default
that a byte-identity test on a *full* record cannot see. And `row_title` coerces a non-dict to `{}`
(`agb_mac:1549-1550`), where the chain's terminal `"?"` is the only thing standing between the
caller and a title that is nothing but the prefix.

**The parse contract**, mirroring `config_seconds`:

| value | result |
|---|---|
| absent | default |
| `` / whitespace | default. ⚠️ **Not** an empty field list — that would be an empty title. (`config_seconds` distinguishes absent from configured-to-nothing because `off` means `0.0` there; here both mean the default, so there is nothing to distinguish and no licence to invent one) |
| `label,cwd:base,pane` | those three, in that order |
| ⚠️ `label, cwd:base, pane` | **the same** — each item is stripped |
| `LABEL,Cwd:Base` | the same — names and modifiers are case-folded |
| ⚠️ `cwd: base` / `cwd : base` | **the same** — the strip is per **component**, not per item |
| ⚠️ `label, ,pane` | `label,pane` — an item is stripped **first**, then skipped if empty |
| ⚠️ `:base` / `:` | **default** + error: the field name is empty. Not an unknown field named `''`, which would print a message with no offender in it |
| `label,workspace` | **default** + error naming `workspace` and listing the valid names |
| `label:base` | **default** + error: `:base` is only defined on `cwd` |
| `cwd:basename`, `cwd:`, `cwd:base:base` | **default** + error: unknown modifier |
| ⚠️ `label,cwd:base,pane,` / `label,,pane` | empty items are **skipped**, not rejected. A trailing comma is the typo everyone makes, and decision 4's bluntness is aimed at a *misspelled* field, not at punctuation that names nothing |
| ⚠️ `,` (every item empty) | **default** + error naming the value. See below — this is not the same as the trailing-comma row |
| duplicate (`label,label`) | accepted — harmless, and refusing it is a rule with no failure behind it |

⚠️ **`parse_row_fields` must never return an EMPTY list**, and that is a real fork the first two
review passes left open. `agb.parse_config` strips the whole value, so `row_fields =` arrives as
`""` (row 2: default) while `row_fields = ,` arrives as `","` → `["", ""]` → both skipped → `[]`.
Two adjacent typos, and as written the table gave them opposite answers. Worse, the difference is
**invisible to every specified test**: `row_title(fields=[])` under `if fields is None:` renders
bare-label rows for every agent, and under `fields = fields or ROW_FIELDS_DEFAULT` renders the full
default — both satisfy the plan, neither warns, and a lone comma silently degrades the whole
sidebar. So: an item list that comes out empty **falls back to the default with an error**, exactly
like `label,workspace`. A value that names no field gives the user nothing they asked for, which is
decision 4's case rather than the trailing-comma one. This also makes the `is None`-versus-`or`
question moot, because `[]` never reaches `row_title`.

⚠️ **Whitespace is the most likely first-use failure, it is silent, and the strip must be
per-COMPONENT rather than per-item.** `agb.parse_config` strips the whole *value* only — verified:
`row_fields = label, cwd:base, pane` arrives as `'label, cwd:base, pane'`. Two granularities are
possible and they disagree on real input:

| written | per-item strip | per-component strip |
|---|---|---|
| `label, cwd:base, pane` | works | works |
| ⚠️ `cwd: base` | modifier is `" base"` → unknown → **whole list rejected** | works |
| ⚠️ `cwd : base` | name is `"cwd "` → unknown → **whole list rejected** | works |

Both are the same typo one character apart, and **no test distinguishes them unless the contract
says which** — `label, cwd:base, pane` passes under either. So: split the item on `,`, split each
item on `:`, and strip **both halves**. Then apply the empty-item skip, in that order: strip first,
*then* skip, or `label, ,pane` rejects the whole list on a stray space.

⚠️ **It must never raise.** This runs on the render path, where an exception wedges a paint.

**Two invariants the config must not be able to break:**

- **`[?]` and `[done]` are prefixes, not fields.** Prepended regardless of `row_fields`, because
  `idle` renders as *no glyph* — without the marker a dead row is pixel-identical to a live idle
  one. A cosmetic setting must not switch off a safety property.
- ⚠️ **The title can never be empty**, and the reachable ways in are not the obvious one. It is not
  a missing label — the `label` field's own chain ends in `"?"`. It is any single-field list whose
  field is dropped by the omit-empty rule above, and there are **three** routes, not one:
  - **`row_fields = beat` on a healthy agent** — `beat_age_text` renders `""` below `BEAT_LATE`;
  - **`row_fields = pane` on a non-tmux agent** — a plain-ssh or session-leader anchor carries no
    pane (`agb:549` defaults it to `None`, `agb:613-620` construct both without one), and `pane` is
    exactly the field this plan's Overview argues people will keep;
  - `row_fields = cwd` on a record without one — `agb.current_dir()` returns `""` when `getcwd()`
    fails and `$PWD` is unset (`agb:859-863`), carried through by `build_record` (`agb:1776`). Same
    mechanism as the other two and deliberately **not** separately tested; listed for completeness,
    not as a third test case.

  ⚠️ **`_title` has a third fallback of its own** (`agb_mac:2338-2342`): with a prefix and an empty
  body it renames to `[done] <key>` rather than failing. So the *with-prefix* case is not a failure
  route at all — which makes it a **vacuous half-test** unless the assertion is an exact string that
  distinguishes the label fallback (`[done] build`) from `_title`'s key fallback (`[done] aaaa1111`).
  The without-prefix case is the one that genuinely fails.

  Two distinct failures follow, and neither is loud:
  - `_title` with no prefix returns `False` (`agb_mac:2338-2341`), so **the rename never happens** —
    agterm keeps its own default name on that row, permanently and silently;
  - `_create_row` sends `session new --name ""`.

  So the join needs its own fallback — ⚠️ **`label or key or "?"`, the same terminal as the field
  chain** — *on top of* the `label` field's own. Ending it at `key` contradicts this section three
  paragraphs up: with a non-dict session coerced to `{}` (`agb_mac:1549-1550`) there is no label and
  no key either, so `label or key` is `""` and the title is the bare prefix. The two chains are not
  duplication — the field-level one keeps the default byte-identical on a label-less record, the
  join-level one catches an empty *list*; only the second is reachable by config.

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

- [x] add `ROW_FIELDS` and `ROW_FIELDS_DEFAULT` near `TITLE_SEP` (`agb_mac:1345`), with the reason
      the default exists in one place: `render_settings` and `row_title`'s own fallback must agree
- [x] add `parse_row_fields(text)` beside `config_seconds` (`agb_mac:2474`), returning
      `(fields, error)` per the parse-contract table
- [x] docstring records **why an unknown field rejects the whole list** (decision 4) and **why it
      never raises** (render path)
- [x] ⚠️ check the new tuples do not trip `test_the_status_vocabulary_has_exactly_one_source` — it
      walks the **whole** `agb_mac` AST including docstrings and fails on any single string
      containing all four status words. The field names are unrelated, but the docstring is where
      an example could accidentally list them
- [x] write a table-driven test for `parse_row_fields` covering **every row** of the contract table:
      absent, empty, whitespace, valid list, order preserved, `cwd:base`, ⚠️ **per-item whitespace**
      (`label, cwd:base, pane`), ⚠️ **whitespace around the `:`** (`cwd: base`, `cwd : base` — the
      cases that divide per-item from per-component stripping), ⚠️ **`label, ,pane`** (strip before
      the empty check), case-folding, `:base` elsewhere refused, unknown *modifier* refused
      (`cwd:basename`, `cwd:`, `cwd:base:base`), ⚠️ **empty field name** (`:base`, `:`), unknown
      field refused, duplicates accepted — ⚠️ and a duplicate with *different* modifiers
      (`cwd,cwd:base`) renders both, in position, since the pair is the identity
- [x] write a test that the error message **names the offending field** and lists the valid ones —
      a warning that says only "bad row_fields" makes the user diff their config by hand
- [x] mutation-test: accept an unknown field instead of rejecting → the contract table's
      `label,workspace` case must fail
- [x] mutation-test: drop the per-item strip → the `label, cwd:base, pane` case must fail
- [x] ⚠️ mutation-test: strip the **item** but not its components → `cwd: base` must fail. Without
      this case the two granularities are indistinguishable and the choice is accidental
- [x] mutation-test: check-empty before stripping → `label, ,pane` must fail
- [x] run `python3 -m pytest tests/ -q` — must pass before Task 2

### Task 2: `row_title` renders the chosen fields

**Files:**
- Modify: `agb_mac`
- Modify: `tests/test_bridge_rows.py`

- [x] add `fields=None` to `row_title` (`agb_mac:1540`), defaulting to `ROW_FIELDS_DEFAULT`
- [x] ⚠️ rewrite `row_title`'s **own docstring** (`agb_mac:1541`), which states the fixed format
      `<label> · <host> · <cwd> · <pane> [· <beat age>]`. It is the docstring of the function this
      change exists to generalise, and it is the one place a stale claim is most likely to be
      believed. Say the default, not the only possibility — and ⚠️ do not name all four status
      words while rewriting it (`test_the_status_vocabulary_has_exactly_one_source` walks docstrings)
- [x] render each field per the vocabulary table; `cwd:base` via `os.path.basename`
- [x] ⚠️ keep the empty-title fallback: label, then key. Never return just the prefix
- [x] ⚠️ **no time calls may enter `row_title`** — `test_the_renderer_never_consults_the_macs_own_clock`
      names it explicitly, and every age here is `feed now - beat`, both server-stamped
- [x] pass `self.settings.get("row_fields")` from `_create_row` (`:1870`) and `_title` (`:2336`)
- [x] ⚠️ write the test that matters most first: **the default renders byte-identically to today**.
      Assert the exact string, and do it for **four** records, because each catches a different
      silent change and `wire()`'s default catches only the first:
      a full one; one with **no label** (the only way to see the `label`-field chain shortened);
      one with **`pane=None`** — ⚠️ *not* the only witness to the omit-empty rule (`wire()`'s default
      `beat` renders `""` too, so record 1 already catches a naive join-everything), but the only one
      that catches an implementation which keeps `beat`'s `if age` special case while joining the
      rest unconditionally; and
      one with a **late beat** (the only way a change to `beat`'s place in the default is visible,
      since `wire()`'s default beat renders `""`)
- [x] write tests: each field renders what it claims; order is respected (assert a *reordered* list
      produces a different, specified string — not just that all fields are present)
- [x] write tests: `cwd:base` shortens; ⚠️ and does not vanish on a trailing slash (`/home/zk/`) or
      on `/` — bare `os.path.basename` returns `""` for both
- [x] ⚠️ write a test that `key` renders the **first 8 of a 16-character key**. The `wire()` fixture
      uses 8-char keys (`"aaaa1111"`) while real keys are 16 (`agb:406` `KEY_BYTES = 8` → hex), so a
      test built on the fixture default proves nothing about truncation
- [x] write tests: `beat` renders only past `BEAT_LATE` and is absent below it — ⚠️ under a
      **non-default** list, since the two existing beat tests both run the default
- [x] ⚠️ write the empty-title tests against **both** reachable routes, not one:
      `row_fields = beat` on a **healthy** record, and `row_fields = pane` on a record with
      `pane=None` (a plain-ssh or session-leader agent, which has no pane at all). With a prefix
      each must render the **exact** string `[done] build` — not merely something
      starting `[done] `, since `_title`'s own `body = key` fallback (`agb_mac:2338-2342`) also
      produces a non-empty prefixed title and would make that half vacuous. Without a prefix each
      must render a non-empty body, **not** return `False` — that is the half that genuinely fails
- [x] ⚠️ write a test that **`[done]` and `[?]` survive an EMPTY-BODY field list**, not merely a
      short one. `row_fields = key` always renders something, so it proves only that a prefix
      survives a short list — the invariant is about the body being empty
- [x] ⚠️ write the plumbing test: drive an upsert through the `bridge` fixture with
      `settings={"row_fields": mac.parse_row_fields("label,pane")[0]}` — ⚠️ **the parsed
      `(name, modifier)` list, not the raw string.** Guessing the string makes this test pass while
      `row_title` iterates characters. Assert **both** the recorded `session new --name` and the
      `session rename` argv carry the configured title. Every other test here calls `row_title`
      directly, so dropping `fields=` at `_create_row` (`:1870`) would have **no victim** — and it
      is invisible in the sidebar, because `_render_upsert` calls `_title` immediately after
      `_create_row` (`agb_mac:1945-1950`) and renames the row one call later
- [x] mutation-test, each naming its victim: drop the prefix → the safety test; ignore the field
      order → the order test; let the join-level fallback go → the `beat`-only test; ignore `:base`
      → the basename test; drop `fields=` at `_create_row` → the plumbing test's `--name`
      assertion; drop it at `_title` → the plumbing test's `rename` assertion; ⚠️ **render an empty
      field as an empty segment instead of dropping it** → the full and `pane=None` byte-identity
      records (the latter is what catches a `beat`-only special case surviving)
- [x] run `python3 -m pytest tests/ -q` — must pass before Task 3

### Task 3: Wire the config through, and warn on a bad value

**Files:**
- Modify: `agb_mac`
- Modify: `agb_ops`
- Modify: `tests/test_bridge_transport.py`
- Modify: `tests/test_core.py`

- [x] add `"row_fields"` (the **parsed** list, not the raw string) and `"row_fields_error"` to
      the `render_settings` return dict (`:2401`),
      read off the **config dict it was handed** — never a fresh `agb.read_config()`, which
      `test_the_bridge_reads_its_config_exactly_once` pins
- [x] warn once from `bridge_sink` (`:2506`), which is confirmed as the right seam: it takes `warn`
      and calls `render_settings` at `:2516`, two lines before building the renderer, and it is
      `render_settings`' only production caller. ⚠️ **Guard for `warn=None`** — several existing
      call sites pass nothing (`tests/test_bridge_rows.py:579`, `:619`, `:676`), as
      `load_rows(settings["rows"], warn)` already tolerates
- [x] add `row_fields` to `agb_ops.CONFIG_KEYS` (`:228`)
- [x] add a line to the hand-written config blob in `test_parse_config_reads_the_documented_keys`
      (`tests/test_core.py:212-213`) — it iterates `CONFIG_KEYS` asserting `key in values`, so
      **adding** the key without the blob line does go red. It is the *removal* mutation that only
      `test_the_documented_key_list_is_pinned_by_name` catches, so add the key to that hardcoded
      set too
- [x] write a test that `render_settings` surfaces the parsed fields, and the default when absent
- [x] write a test that a bad value produces a warning through the real channel — not just a
      non-None error in the dict. ⚠️ Assert the warning **reaches the warn callable**, or this
      proves the parser and not the plumbing. ⚠️ And pass a **tmp config**:
      `bridge_sink(model, {"config": <tmp>}, warn=collector, config={"row_fields": "label,workspace"})`
      — with `{}` the settings resolve `rows_path(None)` and `load_rows` opens the developer's real
      `~/.agbridge/rows`, which `tests/test_bridge_rows.py:12` names as one of the file's three
      load-bearing seams. All **four** existing `bridge_sink` call sites obey it
      (`tests/test_bridge_rows.py:579`, `:619`, `:676`, `tests/test_bridge_transport.py:1116`).
      ⚠️ Pass `run=Runner()` too if the test ever drives an op — without it `RowRenderer` gets the
      real `_run_command`
- [x] mutation-test: drop the key from `CONFIG_KEYS` → the hardcoded test must fail; swallow the
      warning → the plumbing test must fail
- [x] run `python3 -m pytest tests/ -q` — must pass before Task 4

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

- [x] `README.md` config table — one row, naming the default and the six field names
- [x] `docs/commands.md` — ⚠️ under **`## agb bridge`**, where every other bridge key lives
      (`:110`, `:142`, `:176`) and where `:126`'s "takes effect only after `agb-refresh`" statement
      already sits. The only row-title description (`:432`) is under `## agb rename`, a *farm*
      command, so documenting a bridge key there would put it on the wrong side of the wire. The
      section carries: the vocabulary table, the
      `:base` modifier, the whole-list rejection and why, ⚠️ that an **inline `#` is not a comment**
      (`agb:105` honours `#` only as the first character of a line, so `row_fields = label,pane #
      short` makes `pane # short` an unknown field and rejects the whole list — `row_fields` is the
      first key whose value anyone will want to annotate), and what dropping `beat` actually costs
      (decision 6)
- [x] `docs/design.md` config table — one row. ⚠️ It has a **`Default if unset`** column README's
      lacks, so that row is four columns. `docs/design.md:1313` already states the rule this task is
      obeying — *"A key added here belongs in all four places at once"*, added in 0.6.0 after exactly
      this failure — so cite it rather than carrying a private list
- [x] ⚠️ `docs/design.md:1365`, the §5 per-instance row (*"`statedir`, `feed_host`, `mac_id`, the
      notification switches"*) — this one **does** need an edit, unlike the last plan's identical-
      looking item. That wording covered `notify_on_completed_after` because it *is* a notification
      switch; `row_fields` is not, so the list no longer describes what moves with an instance
- [x] ⚠️ **nine places state the title format as fixed**, and none is a config table — so a
      config-table-only pass leaves the docs contradicting the feature, silently, because there is
      no doc-consistency test: `docs/design.md:556` (the design authority's own statement),
      `docs/cookbook.md:145`, `docs/commands.md:432`,
      `docs/agtermctl.md:70` and `:113`, `agb_mac:853` (`format_rows`' docstring, *"what a row
      **is**"*), `agb_mac:1541` (`row_title`'s own — done in Task 2), `agb_ops:3848` (the
      comment above `TITLE_SEP`/`valid_label`), and ⚠️ **`docs/agtermctl.md:87-89`** — prose
      *between* the two sites already listed from that file, enumerating "label, host, cwd, pane and
      the beat age". `docs/agtermctl.md` is what `CLAUDE.md` names as the agterm contract record, so
      a stale enumeration there is the one most likely to be believed. Only the enumeration needs
      softening; its actual claim (`--name` receives the title, not the label) stays true.
- [x] ⚠️ **two further design-authority sites claim the beat age *is* in the title**, and they are a
      different kind of statement from the format enumerations above: `docs/design.md:278` (*"surfaced
      in the row title and never converted into a state"*) and `:227-228` (*"the bridge can put 4m in
      the row title … Note *title*, not *state*"*), plus the comment at `agb_mac:112-118`. These are
      invariant 1's stated compensation, not cosmetics — soften to "by default" and reconcile with
      decision 6. ⚠️ `agb_mac:2259` (*"a healthy row's title carries no age at all"*) stays **true**
      and needs no edit Reword each to name the default rather than the
      only possibility. ⚠️ The label's ban on ` · ` is unaffected and must stay — it is about the
      separator, not the field list. Three further sites print `build · box2 · /shared/x · %24` as
      an **example** rather than a format claim and need no edit: `docs/design.md:526`, `:535` and
      `agb_mac:2323`. ⚠️ A fourth **must not** be edited: `docs/agtermctl.md:369` prints a title
      inside *captured* agterm event JSON, which is evidence rather than documentation. The fixture
      titles in `tests/test_bridge_rows.py` (`:1513`, `:1520`, `:1577`, `:2507-2508`) are the same
      class — noted so the next reader does not re-derive any of them
- [x] ⚠️ document the **cross-restart** effect: the first `[?]` paint after a bridge restart shows
      titles built with the *previous* `row_fields`, because `_title` persists the rendered body and
      `_render_stale` uses it before any record has arrived. Self-heals on the next upsert; worth a
      sentence so it is not reported as a bug
- [x] `.claude/skills/agbridge/SKILL.md` config table
- [x] ⚠️ `CHANGELOG.md` — **append to the existing `## Unreleased`** (`:9`). Do not create a second
      heading. ⚠️ It currently carries `### Changed` (`:11`) and no `### Added`; a new key is an
      addition, so that sub-heading has to be created inside the existing section. The entry says
      *why*: the measured 35% of a line spent on a constant host, and that
      `pane` — the disambiguator — is what agterm clips first
- [x] record the rejected alternatives in the entry (automatic elision, format templates), so they
      are not re-proposed
- [x] ⚠️ state that, like every bridge-side key, it needs `agb-refresh` to take effect
- [x] no tests (documentation only); run the suite anyway to confirm nothing regressed

### Task 5: Verify acceptance criteria

- [ ] the default field list renders byte-identically to today
- [ ] `row_fields = label,cwd:base,pane` renders `agbridge_dev · agbridge-public · %15`
- [ ] a reordered list renders in the order written
- [ ] an unknown field leaves the sidebar exactly as it was **and** logs a warning naming it
- [ ] `[done]` and `[?]` appear with any field list, including a single-field one
- [ ] no title is ever empty
- [ ] `agb doctor` does not report `row_fields` as an unknown key
- [ ] `row_fields = beat` on a healthy agent, and `row_fields = pane` on a non-tmux agent, both
      still produce a titled row rather than a blank one
- [ ] a record missing a field renders no empty segment and no trailing separator
- [ ] `row_fields = ,` renders the default and logs a warning — it never yields an empty list
- [ ] `row_fields = cwd: base` works rather than silently falling back to the default
- [ ] run the full suite: `python3 -m pytest tests/ -q`
- [ ] confirm `agb` is untouched: `python3 -c 'print(len(open("agb").read()))'` must print
      **103198**. ⚠️ The guard counts **characters**, not bytes — `wc -c` is the wrong number.
      ⚠️ And it is `< AGB_PARSE_BUDGET` (103200) with a **strict** `<`, so the true headroom is
      **1 character**. (`CLAUDE.md` said 2 and was corrected in `714fa1a`.) This change must not
      touch `agb` at all

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

### Pass 2 — eight findings, three gating

All re-verified against source. Pass 1's fixes held: every anchor it introduced was right except two
off-by-ones, every mutation it listed has a live victim, and its thirteen review-table rows contained
**one** wrong fact (below). The remaining defects are the same shape as always — a rendering rule the
plan never stated, which made its own flagship test unable to fail the mutation it exists for.

| Finding | Evidence | Change |
|---|---|---|
| ⚠️ **The omit-empty rule was never stated**, so the byte-identity test could not fail "render an empty field as an empty segment" | `agb_mac:1552-1555` appends `host`/`cwd`/`pane` only `if value`; `wire()` populates all of them, so a trailing ` · ` passes every specified test | rule added to the vocabulary table; byte-identity goes from two records to **four** (full, label-less, `pane=None`, late beat), each catching a different silent change; the mutation named |
| "The empty body is reachable **only** via `row_fields = beat`" was **wrong** | a plain-ssh or session-leader anchor carries no pane (`agb:549`, `:613-620`), so `row_fields = pane` is an equally reachable route — and `pane` is the field the Overview argues people keep | claim corrected; the empty-body test now covers both routes |
| The join-level fallback stopped at `key`, contradicting the plan's own reasoning three paragraphs above | a non-dict session coerces to `{}` (`agb_mac:1549-1550`), so `label or key` is `""` — the bare prefix the section forbids | spelled `label or key or "?"` in both places, with why the two chains are not duplication |
| The empty **item** was unspecified | a trailing comma is the typo everyone makes, and decision 4's bluntness argues one way while `if not item: continue` gives the other | contract row: empty items are skipped, not rejected — the bluntness is aimed at a misspelled field, not at punctuation naming nothing |
| Six "fixed format" doc sites were **eight** | `agb_mac:1541` — `row_title`'s **own** docstring, the function this change generalises — and `agb_ops:3848` | both added; the docstring promoted to a Task 2 item |
| The warn-plumbing test would open the developer's real rows map | `bridge_sink(model, {}, …)` resolves `rows_path(None)`; `tests/test_bridge_rows.py:12` names that seam as load-bearing and all three existing call sites obey it | the test spec now passes a tmp config |
| The absent-vs-empty note was copied from `config_seconds`, where it is load-bearing, into a table where both map to the same result | nothing to distinguish here — and read as licence it invents an empty field list, i.e. an empty title | reworded |
| Two anchors off by one | the label chain is `:1551`; the `CONFIG_KEYS` iteration is `:212-213` | corrected |

**Found in passing, and it is live rather than about this plan:** `CLAUDE.md:517` says `agb` has
**2** characters of headroom. The guard is `len(agb_source) < AGB_PARSE_BUDGET` with a **strict** `<`
(`tests/test_mac_split.py:382`), so against 103200 the maximum is 103199 and the true headroom is
**1**. The review said 2 as well; both are off by one. Fixed separately from this plan.

**What pass 2 did not find**, recorded because absence is information: the feature design, the task
ordering, the scope, the `bridge` fixture's `settings=` seam, the two-argv observability of one
upsert, `os.path.basename(v.rstrip("/")) or v` on `/` and on trailing slashes, case-folding safety,
and every one of the ten listed mutations having a real victim. No existing test asserts a full title
string, so none breaks.

### Pass 3 — eight findings, one gating, and it was inside pass 2's own fix

The pattern held for the third time. Pass 2's fixes were correct bar one, every anchor it introduced
verified, and the feature design survived a third pass untouched — but the empty-item rule *it added*
created a config value the plan elsewhere forbade.

| Finding | Evidence | Change |
|---|---|---|
| ⚠️ **`row_fields = ,` yields an empty list, which the same table forbids two rows above.** And the behaviour was implementation-dependent with no test able to tell: `row_title(fields=[])` renders bare-label rows under `if fields is None:` and the full default under `fields or DEFAULT` | measured: `row_fields =` → `""` → default, but `row_fields = ,` → `","` → `["", ""]` → `[]`. Two adjacent typos, opposite specified answers, and a lone comma silently degrading the whole sidebar | `parse_row_fields` **never returns an empty list** — an all-empty item list falls back to the default *with an error*, like `label,workspace`. Own contract row, own test, own acceptance criterion. Also retires the `is None`/`or` fork |
| The empty-body test's with-prefix half was **vacuous** | `_title` has a third fallback of its own — `body = key` (`agb_mac:2338-2342`) — so an empty body *with* a prefix renames to `[done] <key>` rather than failing. Correct code gives `[done] build`, mutated gives `[done] aaaa1111`: both non-empty, both prefixed | stated in Technical Details; the assertion is now an **exact** string. The without-prefix half is the one that genuinely fails |
| Eight "fixed format" doc sites were **nine** | `docs/agtermctl.md:87-89` — prose *between* the two sites already listed from that file. `CLAUDE.md` names that file as the agterm contract record, so a stale enumeration there is the likeliest to be believed | added, with the note that its actual claim stays true |
| "`pane=None` is the only test that can see the omit-empty rule" over-claimed | `wire()`'s default `beat` renders `""` too (`FRESH = NOW - 5.0` vs `BEAT_LATE = 30`), so record 1 already catches a naive join-everything | reason corrected; the record is kept, because it is the only one catching a `beat`-only special case surviving |
| The three empty-body routes vs "**both** reachable routes" contradicted each other on a count | the `cwd` route is real (`agb:859-863` → `""`) | `cwd` marked as the same mechanism, deliberately not separately tested |
| "All three existing `bridge_sink` call sites" | there are **four** — `tests/test_bridge_transport.py:1116` is the fourth, and it obeys the rule too | count corrected, plus a `run=Runner()` note |
| Task 5 called `CLAUDE.md:517` off by one | it was, and `714fa1a` already fixed it | reworded to past tense |
| `settings["row_fields"]` was never said to hold the **parsed** list | the plumbing test spec used an ellipsis; guessing "the raw string" makes that test pass while `row_title` iterates characters | spelled out in Task 3 and in the fixture call |

**On whether to stop.** Pass 3's own verdict: it would not re-review, *but* noted that the one class
this plan has never been wrong about is the parse contract — which is exactly where its gating
finding sat. Three passes have now found 13, 8 and 8; the gating counts were 3, 3 and 1, and the
feature design was never once at fault. If a fourth pass is ever run, point it at the parser's edge
cases and Task 4's doc sites, not at the design.

**Deliberately not cut, though flagged as redundant:** the omit-empty rule is stated four times and
the empty-title argument three. That is prose duplication to maintain rather than a guard — but in a
plan whose whole history is tests that could not fail, restating the rule where each test needs it
is the cheaper error.

### Pass 4 — narrow, and the narrowing was right

Scoped deliberately to the two places pass 3 predicted the remaining risk: **the parse contract** and
**Task 4's doc sites**. Eleven findings, one gating — and it sat exactly where pass 3 said it would.

⚠️ **The gating one: strip granularity was undecided, and the two readings disagree on real input.**

| written | per-**item** strip | per-**component** strip |
|---|---|---|
| `label, cwd:base, pane` | works | works |
| `cwd: base` | modifier `" base"` → **whole list rejected** | works |
| `cwd : base` | name `"cwd "` → **whole list rejected** | works |

The plan said only *"each item is stripped"*, and its one whitespace test case passes under **both**
— so the choice would have been made by accident in the implementation, and a user typing a space
after the colon would get a silently-default sidebar. Now specified as per-component, with the two
cases and their own mutation. Same for ordering: strip **then** skip, or `label, ,pane` rejects
everything over a stray space.

| Finding | Change |
|---|---|
| ⚠️ strip granularity undecided (gating) | per-component, both cases tested, own mutation |
| strip-vs-empty-check ordering undecided | strip first, then skip; `label, ,pane` tested |
| `:base` / `:` (empty field name) unspecified | contract row, with the error naming the empty name rather than an unknown field `''` |
| `parse_row_fields(text)` does not mirror `config_seconds` | signature is `(config, key)` like both siblings, so "absent" is handled inside instead of becoming an `AttributeError` on the render path |
| duplicates with different modifiers (`cwd,cwd:base`) unspecified | both render, in position — the `(name, modifier)` pair is the identity |
| an inline `#` is not a comment | documented: `row_fields` is the first key whose value anyone will want to annotate, and annotating it rejects the whole list |
| ⚠️ **two design-authority sites say the beat age *is* in the title** | `docs/design.md:278`/`:227-228` call it invariant 1's **compensation** — agbridge refuses to turn an age into a status, and the title is what it offers instead. Decision 6's *"only loses a warning"* understated it; both now say what dropping `beat` gives up |
| a fourth "do not edit" example | `docs/agtermctl.md:369` is *captured* agterm JSON — evidence, not documentation. Same class as the test fixtures |
| `docs/cookbook.md:123-125` was classified both ways | it is an example; classified once |
| the commands.md section was aimed at `## agb rename`, a **farm** command | moved to `## agb bridge`, where every other bridge key and the `agb-refresh` caveat already live |
| the four-places rule was being re-derived | `docs/design.md:1313` already states it; cite it |

**Part B came back clean**: all nine doc sites exist and say what the plan claims, the three
"examples" were correctly classified, and an independent sweep of every `.md`, script and test found
no *format-claim* site the plan had missed — only the two beat-age claims above, which are a
different kind of statement.

**Four passes: 13, 8, 8, 11 findings; gating 3, 3, 1, 1.** The feature design has never once been at
fault. Every defect has been in a test specification, a parse contract, or a doc enumeration — and
each pass found its predecessor's work rather than the original. The narrow fourth pass had the best
hit rate per token of any of them, which is the argument for scoping the next one too, if there is a
next one.
