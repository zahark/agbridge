# agb-peer-setup: an interactive roster builder

> **Revision 4**, after a third automated plan review. Revision 3 closed the recurring gap —
> review 3 confirmed **all 14 corrections reach the checkboxes** — and exposed a narrower one:
> checkboxes whose *specified call does not do what the checkbox says*. Six were broken that way.
> See **Corrections from revision 3**; every finding below was re-verified, and the ones marked
> **measured** were run.

## Overview

Today the `agb-peer relay --roster <file>` participants file is hand-written: a human must know the
exact row-label text agterm shows, the `[:pane][@ssh-target[:tmux-target]]` suffix grammar, and the
atomic-write discipline (temp file + `mv`) required to edit it safely while a relay is running.

This plan adds **`agb-peer-setup`**, a new standalone script in the `agb-*` family
(alongside `agb-claude`, `agb-codex`, `agb-tmux`, `agb-peer`, `agb-ralphex`, `agb-host-line`). It is
an interactive, lettered-menu tool (no curses — matches `agb pane`'s
`[enter] attach [s] split [d] drawer [q] quit` house style) that:

- discovers live agterm rows (Mac-side, via `agtermctl tree --json`), lets the user **pick** one,
  and **derives a roster-legal `<row>` value from it** (see below — this is the feature, and
  revision 2 never specified it)
- prompts explicitly for transport rather than guessing from the row's launch argv
- writes the roster file atomically, with a byte gate and an automatic recovery draft if the file
  changed underneath the session — never silent data loss
- prints the exact `agb-peer relay --roster <path>` command to run next

### 🔴 The `<row>` derivation is the feature, and it is not the row's title

⚠️ **A picked row's displayed name cannot be used as `<row>`.** Three independent reasons, all
measured or cited:

- `parse_roster_text` splits each line on **whitespace** (`agb-peer:1058-1061`), and the default row
  title is `label · host · cwd · pane · beat` joined by `TITLE_SEP = " · "` (`agb_mac:1345`,
  `:1602`). Measured: `parse_roster_text("alice=my row · box", minimum=1)` →
  `PeerError: participants are name=<row> …, not 'row'`.
- The `beat` field is an **age** (`3s`/`14m`) that changes on every repaint, so any title-derived
  value goes stale within seconds.
- `:` and `@` are grammar (`agb-peer:1165-1168`), so a title containing either is unrepresentable.

And the row **id** is equally wrong: `docs/commands.md:1665-1667` says to name a **label
substring**, never an id, because `agb-refresh` re-mints every row and every id changes.

**The rule this plan adopts**: **strip a leading `TITLE_STALE` (`"[?] "`) or `TITLE_DONE`
(`"[done] "`) prefix** (`agb_mac:1346-1347`, applied at `:2237`/`:2267`/`:2394`), then take the
title's **first `TITLE_SEP` component**, refuse and re-prompt if it contains whitespace, `@` or `:`,
and confirm via `match_sessions` (`agb-peer:380`) that the single match **is the picked row**.

⚠️ **Three traps in that one sentence, all found by review 3 and all reachable:**

- **The prefix is not optional.** `row_title` is `prefix + TITLE_SEP.join(...)` (`agb_mac:1602`),
  so for any `[?]` or `[done]` row the first component is `"[?] label"` — which this very rule then
  refuses **for containing whitespace**. Every stale or finished row would be unpickable, with a
  baffling reason. A test fixture whose title has every field populated and no prefix hides this
  exactly (CLAUDE.md's own hazard), so Task 3 pins a `[?] `-prefixed title.
- **`row_fields` is user-configurable** (`ROW_FIELDS_DEFAULT`, `agb_mac:1358`), so `row_fields =
  host,label` makes the first component the **host**. This plan assumes the default ordering and
  says so; the failure mode is a wrong-but-plausible `<row>`, which the uniqueness check below
  catches only sometimes.
- **`len(matches) == 1` is not "the right row".** `match_sessions` returns the **first non-empty
  tier** and the *id-prefix* tier fires before the name tier (`agb-peer:380-397`), so a label that
  happens to prefix a different row's id yields a unique match on the **wrong** row. The check must
  assert `matches[0]["id"] == picked["id"]`.

Tasks 4 and 7 own this.

### Why a separate script and not a fourth `agb-peer` verb

⚠️ **Recorded because the cheaper route is real.** `agb-peer` already dispatches on leading verbs
(`send`/`who`/`relay`, `agb-peer:2397-2429`) and has **no** byte budget — that constraint is `agb`'s
alone. `agb-peer roster <file>` would delete Task 2 entirely and remove the module-identity trap.

The reason not to: **`agb-peer send` runs on cluster hosts, inside agents, on every message.** A
menu loop, a picker and config-resolution code in that file is parsed on every send by every agent.
Keeping the farm-invoked path small is the same argument that produced `agb`/`agb_mac`/`agb_ops`,
one tier down. ⚠️ This is also why **`import tempfile` is refused** below (correction 8): measured
at **12.5 ms** on this box's 3.6.8, because it pulls `shutil` — the same order as the `argparse`
import CLAUDE.md already rejects.

**The cost is paid explicitly**: shared code stays in `agb-peer`, and Task 2 pins module identity so
the two scripts cannot end up with two different `PeerError` classes.

### Code sharing

The roster grammar/validation/atomic-write code is **not** extracted into a new module. `agb-peer`
already owns `parse_roster_text`, `read_roster_file`, `parse_participants`,
`valid_participant_name`, `pane_argv_field`, `sessions_of`, `match_sessions` and `Ctl` —
`agb-peer-setup` loads `agb-peer` by path (`SourceFileLoader`) and calls them. New primitives (byte
gate, rendering, atomic roster write) are added **to `agb-peer`**, so it stays the single owner of
the roster file format.

## Corrections from revision 3

⚠️ **Review 3's verdict on the recurring problem: fixed.** All fourteen of revision 2's
corrections reach the checkboxes. What follows are the six whose *specified call did not do what
the checkbox claimed*, plus the six lesser ones — a narrower class, and the one this revision closes.

**R1.** 🔴 **Task 4 discarded the `unreadable` list it was supposed to thread.** The checkbox read
`row_target(hint, unreadable)` — a caller's list — while the body it specified was
`pane_settings(opts, unreadable=[])`, a **fresh literal**. The caller's list is never touched, so
the next checkbox ("when `unreadable` comes back non-empty, report and offer no list") could never
fire. Worse, `host_choices` had no `unreadable` parameter at all and called `agb.read_config`
directly — which **re-raises everything except `ENOENT`/`ENOTDIR`** (`agb:152-170`, verified), so an
`EACCES`/`EISDIR`/`ESTALE` config raises `OSError`, a class the `__main__` guard did not catch. That is
exactly the traceback correction 12 fixed for `AgbError`, one exception class over — and
`pane_settings`' docstring exists because that traceback once destroyed a live row. **Fixed**: one
list threaded through both, and `OSError`/`IOError` added to the guard.

**R2.** 🔴 **The `<row>` rule was wrong in two reachable cases and unchecked in a third** —
`TITLE_STALE`/`TITLE_DONE` prefixes, a configurable `row_fields`, and `match_sessions`' tier order
meaning a unique match can be the **wrong row**. See the Overview section, which now carries all
three. **Fixed** in Tasks 4 and 7, with a `[?] `-prefixed test fixture.

**R3.** 🔴 **`write_roster_file` would raise the wrong class on an unreadable file, silently
disarming the recovery flow correction 5 exists for.** Task 1 required a `RosterConflict` for
present→unreadable, but `roster_bytes` **raises `PeerError`** for that case rather than returning a
comparable value — so the comparison never happens and a bare `PeerError` propagates past Task 11's
`except RosterConflict`. **No recovery draft is written and the draft is lost**, with both the guard
and its test green (the test said only "raises rather than renaming over it", which passes on the
wrong class). **Fixed**: the conversion is explicit and the test asserts the class.

**R4.** 🔴 **Correction 8's own prescribed fix reproduced the trap it named.** It said `mkstemp`
creating at **0600** would silently make the roster owner-only, and prescribed copying
`agb.atomic_write` — whose default is `mode=FILE_MODE` and **`FILE_MODE = 0o600` (`agb:41`,
verified)**. The plan never named the intended mode; Task 1 said "not `0600`" and Post-Completion
said "what the operator expects", neither a number. Separately, `agb.temp_name` is
`"%s.tmp.%s.%d.%s" % (base, own_host(), getpid(), urandom(4).hex())` and **`agb-peer` has none of
those three** (measured: zero occurrences), so "copy `temp_name`'s shape" silently demanded either a
new `own_host` spelling — an unregistered instance of CLAUDE.md invariant 14's `own_host` agreement,
which already names `agb-claude` and `agb-codex` — or an unrecorded divergence. **Fixed**: the mode
is a literal and the temp-name components are enumerated.

**R5.** 🔴 **"Move the fixture" would fail the very identity test it was added for.** The existing
`peer` fixture (`tests/test_agb_peer.py:21-28`) **never touches `sys.modules`** — unlike conftest's
`agb` fixture, which assigns before `exec_module` (`tests/conftest.py:138`). A verbatim move leaves
`_load_peer`'s `sys.modules.get(key)` a miss, a second module object is built, and
`setup.PeerError is peer.PeerError` **fails** — in the way the plan itself predicted would "look
like a loader bug". **Fixed**: move **and register**.

**R6.** 🔴 **The raw hatch was still a trap — measured.** Task 8 validated with
`parse_participants`, but that function receives words **already split** and never rejects
whitespace inside `<row>`: `parse_participants(['alice=my row'], minimum=1)` →
`{'alice': ('my row', 'left', None, None)}`, **accepted**. Only `parse_roster_text` rejects it, by
splitting (`agb-peer:1058-1061`). So a spaced line entered the draft and failed only at `w` — the
exact defect correction 4 measured, surviving in the one place whose stated goal is "otherwise the
hatch is a trap". **Fixed**: validate with `parse_roster_text`.

**R7.** ⚠️ **`agb._load_ops()` already exists** (`agb:2682`) and does exactly what Task 4's
`_load_ops` described; `tests/conftest.py:155-162` routes through it **deliberately**, so that every
test exercises the real door rather than a parallel import that would keep passing if the door
broke. Reimplementing it contradicts this plan's own correction-3 rule. **Fixed**: call it. The
by-path loader is owed only for `agb-peer`, which genuinely is not an `agb` sibling.

**R8.** ⚠️ **`_load_peer`'s default path needs `realpath`.** `agb.sibling_path` (`agb:2626-2632`)
uses it with the reason written down — "so an `agb` symlinked into a `bin/` directory still finds
the siblings next to the *real* file" — and this plan's **only** install instruction is to
**symlink** `agb-peer-setup` beside `agb-peer`. Without it the tool fails under its own documented
installation, with every test green because tests run from the checkout.

**R9.** ⚠️ **The `[a]`-gate test was specified in a task where the prompt does not yet exist.**
Task 4 tested prompt behaviour ("withhold, say why, pre-select `[s]`") but the transport prompt is
not built until Task 6, so Task 4's "must pass before task 5" gate was unmeetable — and a deferred
test is how a correction stops being enforced. **Fixed**: a pure predicate in Task 4, the wording
and its test in Task 6.

**R10.** ⚠️ **Task 5's sentinel-inequality test was unimplementable.** After correction 6 there is
no unreadable *answer* to compare against — `roster_bytes` **raises**. Left as written, an
implementer would either invent a third value (reintroducing the vacuous gate) or drop the check.
**Fixed**: restated as a `pytest.raises` assertion.

**R11.** ⚠️ **Task 1 was four independent primitives in one task** (18 checkboxes), against the
~5-checkbox guideline, and `render_roster_lines` does not depend on `roster_bytes` at all.
**Fixed**: split into 1a and 1b.

**R12.** ⚠️ **CLAUDE.md has four stale numbers, not one.** **Measured today**: `agb` is **105,269
characters / 105,287 bytes**, headroom **30** against `AGB_PARSE_BUDGET = 105300`. CLAUDE.md says
103,198 / 103,212 / 103,200 / "one character". **Fixed**: Task 14 corrects all four.

---

## Corrections from revision 2

*Retained because a superseded correction whose reasoning is deleted gets re-litigated. Review 3
confirmed every one of these is factually right about the source and reaches a checkbox.*

Revision 2's thirteen corrections were all factually right about the source. Five did not reach the
checkboxes, and the rewrite introduced five new defects. Both sets are below; each was verified, and
the ones marked **measured** were run.

1. 🔴 **The mutation-check `__pycache__` step was a no-op, which silently disarms every
   mutation-check in the plan.** **Measured**: a by-path load of `agb-peer` writes
   `<repo-root>/__pycache__/agb-peercpython-36.pyc`. Revision 2 said to delete
   `tests/__pycache__` and `__pycache__/agb_peer*.pyc` — wrong **directory** (repo root, not
   `tests/`) *and* wrong **glob** (`agb_peer` with an underscore does not match `agb-peer` with a
   hyphen). The wording was copied from CLAUDE.md, where `agb_ops*.pyc` matches because that name
   has no hyphen. **Fixed**: the exact path, plus an `ls` confirmation step.
2. 🔴 **`parse_participants` cannot detect a duplicate across calls, so Task 6's duplicate test
   could not pass.** **Measured**: two consecutive `parse_participants(['alice=r1'], minimum=1)` /
   `(['alice=r2'], minimum=1)` calls both succeed — `people = {}` is fresh per call
   (`agb-peer:1149`) and "named twice" only fires within one call (`:1176`). Revision 2's
   correction 3 landed for `relay` and the alphabet and **over-claimed** on duplicates. **Fixed**:
   validate the **whole draft plus the new word** in one call, which restores all three guarantees.
3. 🔴 **Task 4's `pane_settings` call could not run and could not answer the question.**
   (a) `pane_settings` reads `opts["host"]` and `opts["jump"]` by **subscript** (`agb_ops:1953-1954`),
   so an `opts` carrying only `config` raises `KeyError`. (b) It returns `(target, jump)` for **one**
   host and deliberately never exposes the `host_<name>` table, so it cannot produce a pick-list.
   **Fixed**: the row's `foreground` **is** an `agb pane` argv, so call the real parser —
   `agb_ops.parse_pane_args` (`agb_ops:1830`) — for a complete `opts`, then `pane_settings` for the
   row's own target and `agb.read_config` filtered on `agb_ops.CONFIG_KEY_PREFIXES`
   (`agb_ops:238`) for the enumeration. This is CLAUDE.md invariant 12's own rule: where it is not
   ambiguous, **call the parser** rather than imitating it.
4. 🔴 **How a picked row becomes a `<row>` value was never specified, and every obvious answer is
   invalid.** See the Overview section above; **measured**. Revision 2's headline value and its
   acceptance criterion both rested on a derivation no task defined. **Fixed**: the label rule, with
   validation and a uniqueness check (now Tasks 4 and 7 after revision 4's split).
5. 🔴 **The recovery write self-conflicts.** **Measured**: `mkstemp` **creates** the file (0 bytes,
   mode `0600`). Revision 2 said to persist the recovery draft "to a `mkstemp` recovery file" *and*
   (checkbox 6) that "the recovery write goes through `write_roster_file`" — but that function
   re-reads the target and compares, so on a just-created path the read is `b""`, any plausible
   `expect` mismatches, and the recovery raises `RosterConflict` **from inside the conflict
   handler**. The one path standing between a conflict and lost work could not execute. **Fixed**:
   the recovery write is **ungated** and writes through the raw fd; the AST guard moves to asserting
   *that* shape.
6. ⚠️ **`roster_bytes` collapsed "absent" and "unreadable" into `None`.** `read_roster_file` raises
   `PeerError` for `EACCES`/`EISDIR`/`ESTALE` (`agb-peer:1035-1036`). If both answer `None`, then
   `write_roster_file(path, lines, None)` compares `None == None` and renames **straight over an
   unreadable existing roster** — the gate is vacuous exactly when it matters. This is the same rule
   the plan quotes approvingly in Task 4 (CLAUDE.md invariant 12, and `agb_ops.pane_settings`'
   docstring at `agb_ops:1926-1946`). **Fixed**: three answers, not two.
7. ⚠️ **The load order of `Draft.entries` was undefined.** Correction 1 rejected a dict because
   "dict ordering is a CPython 3.6 implementation detail", then built the ordered list *from that
   dict*. Task 1's round-trip is order-**insensitive**, so nothing pinned order, while Tasks 7 and 8
   depend on it. **Fixed**: order is the **file's line order**, re-derived from the lines, with a
   test.
8. ⚠️ **`mkstemp` ignored two in-repo precedents and changed the file mode.** `agb.temp_name`
   (`agb:280-289`) already solves same-directory unique temps with the collision reasoning written
   down, and `agb.atomic_write` (`agb:315-334`) is the full pattern — `O_CREAT|O_EXCL`, `os.fchmod`
   to restate the mode past umask, unlink-on-failure. `mkstemp` creates at **0600** and `os.rename`
   preserves it, so the first write through this tool would have silently made the roster
   owner-only. Plus the 12.5 ms `import tempfile` cost above. **Fixed**: copy `temp_name`'s shape;
   no `tempfile`.
9. ⚠️ **`render_roster_lines`' return type contradicted itself** — Task 1 tested `b"".join(render(…))`
   (bytes, self-terminated lines) while Tasks 6 and 8 print and pre-fill "the canonical line" (str).
   **Fixed**: `str` lines without trailing newlines; `write_roster_file` encodes and joins.
10. ⚠️ **Transport `[a]` (agbridge-row default) does not get the `host_<name>` mapping.**
    `scan_participant` does `host = target or pane_argv_field(…, "--host")` (`agb-peer:2081`) and
    hands it **verbatim** to `ssh_argv` — never through `ssh_target_for`. So `[a]` means "ssh to the
    bare hostname", precisely the case `host_<name>` exists to fix: a roster that parses, validates,
    prints a working-looking next command, and silently fails to deliver. **Fixed**: when
    `ssh_target_for(host, config) != host`, withhold `[a]` and pre-select `[s]` with the mapped
    target.
11. ⚠️ **Two names resolving to one row was never cross-checked.** `_one_name_per_row`
    (`agb-peer:2253-2262`) exists because "two DIFFERENT labels can each unambiguously match the
    same row" — the relay then delivers twice and can type a message into its own sender. A picker
    makes this *easy*, and `parse_participants` cannot see it (names differ, row strings differ).
    **Fixed**: a resolve-and-warn check in the write flow.
12. ⚠️ **`_load_ops` had no search path**, and on the Mac `agb`/`agb_ops` live in
    `~/.local/lib/agbridge/`, not beside a checked-out script. `agb-refresh` already sets the
    convention (`DEFAULT_AGB="$HOME/.local/lib/agbridge/agb"`, `agb-refresh:39`, with `--agb` and a
    hard existence check). **Fixed**: same default plus an injectable override. Also `agb_ops`'
    parsers raise **`agb.AgbError`** (`agb_ops:1859-1888`), which revision 2's `__main__` guard did not
    catch — an unparseable `foreground` would traceback out of a menu.
13. ⚠️ **The conftest fixture move was under-specified and missed a file.** Confirmed:
    `tests/conftest.py` has **no** `peer` fixture; the only one is `tests/test_agb_peer.py:21-28`.
    So "add to conftest **or import the existing one**" has one real option, and moving it requires
    **deleting** the local definition — otherwise the module-local fixture shadows conftest's and
    the two files get two module objects again, which is the exact hazard correction 11 was about.
    `tests/test_agb_peer.py` was not in the Files list. **Fixed**, and the `sys.modules` key is
    pinned as a named cross-file agreement.
14. ⚠️ **`wc -c agb` is the wrong measurement, by name.** CLAUDE.md: "`agb`'s budget is measured in
    CHARACTERS, not bytes … `wc -c` is the wrong number to compare." Also **the real budget is
    `AGB_PARSE_BUDGET = 105300`** (`tests/conftest.py:88`) — CLAUDE.md's 103200 is stale. **Fixed**:
    since this plan touches no `agb` bytes, the check is "unchanged from HEAD", which is cheaper and
    stronger.

## Context (from discovery)

Verified against HEAD (`agb-peer` is **2492 lines**); citations re-checked this revision.

- `parse_participants(words, minimum=2)` → **`{name: (row, pane, target, tmux_target)}`**,
  `agb-peer:1129`, returning at `:1190`. `people = {}` is **per call** (`:1149`), so the duplicate
  check at `:1176` is per call only (**measured**). Raises for: malformed word, bad alphabet, the
  reserved `relay` (`:1158`), a pane outside `PANE_KINDS` (`:1170`), empty row, `@` with no target,
  duplicate-within-call, and `< minimum` (`:1182`).
- `parse_roster_text(data, minimum=2)` splits lines and then **whitespace** (`:1039`, `:1058-1061`).
- `PANE_KINDS = ("left", "right", "scratch")` — `agb-peer:66`. `RELAY_NAME = "relay"` — `:1101`.
- `valid_participant_name` — **alphabet only**, `:1125`.
- `match_sessions(sessions, want)` — `:380`; `resolve`'s tier order (exact id, id prefix, title
  substring) — `:1326-1339`.
- `pane_argv_field(foreground, flag)` — literal field extraction, `:1193`.
- `RosterReader` — `:958`, `minimum=1` at `:984`, **byte gate** at `:1002`, docstring rejecting a
  stat key at `:968-973`.
- `write_chat_file` — temp+rename with a **fixed** `.tmp` name, `:666`. Structural guard:
  `tests/test_agb_peer.py:1799`.
- `_one_name_per_row(resolved, say, notes=None)` — `:2253`. `scan_participant`'s verbatim host —
  `:2081`.
- `VERSION = "0.3.0"` — `:56`, served at `:2431`.
- `cmd_list` reports MODE per row and threads `lines` for it — `:1570-1578`.
- **`agb` precedents**: `temp_name(path)` (`agb:280-289`), `atomic_write(path, data, mode)`
  (`agb:315-334`), `_load_sibling` (`agb:2643-2675`, which `del sys.modules[name]` on a failed
  `exec_module`, with the reason written down).
- **`agb_ops`**: `parse_pane_args(argv)` (`:1830`), `pane_settings(opts, config=None, unreadable=None)`
  (`:1903`, reading `opts["host"]`/`opts["jump"]` by **subscript** at `:1953-1954`),
  `ssh_target_for(host, config)` (`:1392`), `CONFIG_KEY_PREFIXES = ("host_",)` (`:238`),
  `pane_config_warning` (`:2079`). Its parsers raise `agb.AgbError` (`:1859-1888`).
- **`agb_mac`**: module-scope `import agb` (`:33`); `TITLE_SEP = " · "` (`:1345`); `row_title` joins
  with it (`:1602`).
- `tests/conftest.py` — **no `peer` fixture**; `AGB_PARSE_BUDGET = 105300` (`:88`).
  `tests/test_agb_peer.py:21-28` holds the only loader fixture.
- `tests/test_core.py:1052-1064` already walks every `tests/test_*.py` for unbounded
  `communicate()`/`.read()`, so a new test file inherits that guard.
- `docs/commands.md:1665-1667` — name a **label substring**, never an id. `:1679-1683` — the
  atomic-write requirement. `:1688-1691` — startup refuses / runtime holds.
- `docs/agtermctl.md:699-719` — `tree --json` exposes `id`, `name`, `cwd`, `status`, `surfaces`,
  `realized`, `foreground` (**launch argv**). **No** hostname/ssh field: transport is asked, never
  inferred.
- `README.md:173-179` lists the `agb-*` family.

## Development Approach

- **Testing approach**: Regular (code first, then tests) — matches the repo.
- Complete each task fully, with passing tests, before the next.
- **CRITICAL: every task MUST include new/updated tests.**
- **CRITICAL: all tests must pass before starting the next task.**
- **CRITICAL: update this plan file if scope changes during implementation.**
- Hand-rolled argv parsing (no `argparse`); dependency-injected `run`/`read_line` so menu and
  discovery logic are testable without a terminal or `agtermctl`.
- Python 3.6.8 compatible (no f-strings with `=`, no dataclasses, no walrus).
- ⚠️ **Function-local imports for anything costly**, and `tempfile` is refused outright
  (correction 8) — 12.5 ms, on a file `agb-peer send` parses on every message from every agent.
- ⚠️ **This plan does not touch `agb`.** The check is "`agb` is unchanged from HEAD"
  (`git diff --stat HEAD -- agb` empty), which is stronger than re-measuring; if it ever must be
  measured, it is **characters** (`len(io.open('agb', encoding='utf-8').read())`) against
  `conftest.AGB_PARSE_BUDGET` (**105300**), never `wc -c` (correction 14).

## Testing Strategy

- **Unit tests** via dependency injection (fake `run`, fake `read_line`); no real subprocess, tmux,
  or filesystem race needed to exercise the conflict path.
- **Integration-style tests** drive the whole loop against a temp roster and a fake `Ctl`, asserting
  final bytes and exit code.
- **Non-vacuity**: every AST/structural guard asserts its target was found before asserting what is
  absent; every loop asserts its collection is non-empty first.
- **Structural guards are AST-based, never substring greps** — `tests/test_agb_peer.py:1799` is the
  worked example, including its comment about the first version passing against a docstring.
- **Mutation-check mechanics**, spelled out because revision 2 got this wrong and thereby disarmed
  every mutation-check it specified (correction 1):
  - commit first; restore from an **in-memory snapshot verified by `sha256`**, never `git checkout`
  - after writing mutated source, delete the by-path bytecode at its **real** location:
    `rm -f <repo-root>/__pycache__/agb-peer*.pyc <repo-root>/__pycache__/agb-peer-setup*.pyc`
    — repo root, **hyphen**, not `tests/` and not `agb_peer*` (**measured**:
    `__pycache__/agb-peercpython-36.pyc`). `ls` the directory afterwards to confirm
  - the `.pyc` is validated on (source mtime in **whole seconds**, source size), so a same-size
    rewrite inside one second reuses stale bytecode — confirm the mutated file's mtime moved
  - assert the mutation anchor is **unique**; re-read the mutated file before running
  - confirm a **named** test fails, then restore

## Solution Overview

**Data structure**: `Draft.entries` is an ordered `list` of `(name, (row, pane, target,
tmux_target))`, ordered by the **file's line order** (correction 7), not by dict iteration.

**New functions in `agb-peer`:**

- `roster_bytes(path)` → **three** answers (correction 6): the bytes, a distinct *absent* sentinel
  for `ENOENT`/`ENOTDIR`, or a raised `PeerError` for anything else. Reads the file; does not
  `os.stat` (invariant 6, and `RosterReader`'s docstring at `:968`).
- `render_roster_lines(entries)` → **`str`** lines, no trailing newlines (correction 9). Emits
  `:<pane>` only when `pane != "left"`, `@<target>` only when set, `:<tmux_target>` only when set,
  and **refuses** a `tmux_target` with no `target` — the grammar cannot express it and it would
  reparse as a pane (`agb-peer:1165-1166`).
- `write_roster_file(path, lines, expect)` → `temp_name`-style same-directory temp,
  `O_CREAT|O_EXCL`, `os.fchmod` to restate the mode past umask, unlink-on-failure — the
  `agb.atomic_write` shape (`agb:315-334`), **not** `tempfile` (correction 8). Re-reads
  `roster_bytes(path)` and compares to `expect` immediately before `os.rename`; raises
  `RosterConflict` on mismatch, **including** absent→present, present→absent, and
  present→unreadable.
- `write_draft_file(path, lines)` → the **ungated** sibling used for recovery drafts (correction 5):
  same temp+rename shape, no comparison, because a freshly-minted unique path has nothing to
  conflict with.
- `RosterConflict(PeerError)` — distinct because the caller must *recover*, not print-and-exit.

**Menu:**

```
Roster: <path> [*]
  1) alice = my-claude-row
  2) bob   = codex-batch@poolnode07

[a] add   [d] delete   [e] edit raw   [v] view   [w] write & exit   [q] quit >
```

**Add flow (`a`)**: fresh `Ctl.tree()` **every time** → numbered picker → **derive `<row>` from the
title's first `TITLE_SEP` component**, refuse whitespace/`@`/`:`, and confirm via `match_sessions`
that it matches exactly one row (correction 4) → name prompt validated by
`parse_participants(<whole draft> + [new], minimum=1)` (correction 2) → pane-kind prompt
(`left`|`right`|`scratch`, default `left`) → transport prompt:

- `[a]` agbridge-row default — no `@…`; offered **only** when `agbridge_hint` found a `--host`
  **and** `ssh_target_for(host, config) == host`, because the relay uses that host verbatim
  (correction 10)
- `[l]` local Mac tmux — `@local:<tmux target>`
- `[s]` ssh — `@<ssh target>`, with a pick-list from the correct instance's `host_<name>` table
- `[t]` ssh with an explicit tmux target — `@<ssh target>:<tmux target>`

**Write flow (`w`)**:
1. Validate via `parse_roster_text("\n".join(render(entries)).encode(), minimum=1)`. Invalid → show
   the error, stay in the menu, no disk touch.
2. If `< 2` participants, **warn and continue**, naming the startup-vs-runtime distinction.
3. Resolve every entry against a fresh tree and **warn if two names land on one row** (correction 11).
4. `write_roster_file(path, lines, draft.loaded)`. Success → print `wrote <path>` and the literal
   next command; clear dirty; refresh `loaded`.
5. `RosterConflict` → refuse the real path, **immediately** `write_draft_file` an ungated recovery
   draft and print its path, then: `[v]` view draft, `[r]` reload (confirmed; the only destructive
   action), `[q]` quit leaving the draft, `[enter]` back to the menu.

No auto-merge anywhere.

## Acceptance Criteria

- [ ] `agb-peer-setup <file>` opens a menu; a row is added by **picking**, and the stored `<row>` is
      a roster-legal label that `match_sessions` resolves to exactly one row
- [ ] the four transport shapes and all three pane kinds are reachable without the raw hatch
- [ ] `relay` is refused as a participant name, at the prompt
- [ ] a duplicate name is refused at the prompt, against the **whole draft**
- [ ] a one-participant roster loads and writes (with a warning), so a participant can be removed
- [ ] a file changed underneath the session never loses the draft and never publishes a torn read
- [ ] an **unreadable** roster is never silently overwritten
- [ ] the printed next-command starts a working relay verbatim
- [ ] `agb-peer-setup validate <file>` reports the same errors `agb-peer relay` would

## Implementation Steps

### Task 1: `RosterConflict`, the byte gate, and rendering

**Files:**
- Modify: `agb-peer`
- Modify: `tests/test_agb_peer.py`

- [x] add `RosterConflict(PeerError)` beside `PeerError`
- [x] add `roster_bytes(path)` with **three** outcomes: the bytes; `None` for *absent*
      (`ENOENT`/`ENOTDIR`); a raised `PeerError` for any other errno. Read the file — do **not**
      `os.stat` — with a comment pointing at `RosterReader`'s docstring (`agb-peer:968`)
- [x] comment the `None`-is-absent choice honestly (R10): now that unreadable **raises**, `None`
      cannot be confused with it, so the vacuous-gate bug correction 6 fixed cannot recur; `None` is
      still distinct from `b""`, which is an empty-but-present file
- [x] add `render_roster_lines(entries)` over the ordered list → **`str`** lines, no trailing
      newlines; omit `:left`, omit an unset `@target`, omit an unset tmux target, and **raise** on a
      `tmux_target` with no `target` (ungrammatical — `agb-peer:1165`)
- [x] write tests: `roster_bytes` returns bytes / `None` / **raises** for present / absent /
      unreadable (chmod `000`), and an identical-content rewrite is **not** a change
- [x] write tests: **semantic** round-trip `parse_roster_text("\n".join(render(entries)).encode(),
      minimum=1) == dict(entries)` over a fixture covering all four transport shapes and all three
      pane kinds
- [x] write tests: canonical spelling pinned against **literal** strings (`alice=myrow` — no
      `:left`, no `@`, no tmux target); `render_roster_lines` returns `str`, not bytes; raises on
      tmux-without-target; and a 3-entry list renders in **list order**
- [x] run `python3 -m pytest tests/test_agb_peer.py -q` — **325 passed**
- [x] ➕ mutation-checked three guards (unreadable→absent, dropped tmux refusal, `:left`
      emitted): each failed its **named** test, restore sha256-verified, `__pycache__/agb-peer*.pyc`
      deleted at the repo root and mtime confirmed moved

### Task 2: The two atomic writers

**Files:**
- Modify: `agb-peer`
- Modify: `tests/test_agb_peer.py`
- Modify: `CHANGELOG.md`

- [x] add `write_roster_file(path, lines, expect)` in the **`agb.atomic_write` shape**
      (`agb:315-334`) — same-directory temp, `O_CREAT|O_EXCL`, `os.fchmod` to restate the mode past
      umask, unlink-on-failure — and **no `tempfile` import** (12.5–14 ms, measured; correction 8)
- [x] ⚠️ **name the mode as a literal, and do not inherit the precedent's default** (R4):
      `agb.FILE_MODE` is **`0o600`** (`agb:41`) — the very trap correction 8 named. A roster is read
      by a relay the same user runs, so `0o600` is defensible, but it must be **chosen and written
      down**, not inherited. Decide: `0o600`, and state that an **existing** file's mode is not
      preserved (the rename replaces it) so a hand-loosened roster silently tightens on first write
- [x] ⚠️ **enumerate which `temp_name` components are copied** (R4): `agb.temp_name` is
      `<name>.tmp.<host>.<pid>.<rand>` (`agb:280-289`) and **`agb-peer` has no `own_host`, `getpid`
      or `urandom`** (measured: zero occurrences). The host component exists because *two hosts* may
      write one target over NFS; this tool is Mac-local and single-user, so copy
      **`<name>.tmp.<pid>.<rand>`** and **record the omission and its reason** — adding an
      `own_host` spelling here would be an unregistered instance of CLAUDE.md invariant 14's
      `own_host` agreement (which already names `agb-claude` and `agb-codex`)
- [x] ⚠️ **convert `roster_bytes`' `PeerError` into `RosterConflict`** (R3): compare
      `roster_bytes(path)` to `expect` immediately before `os.rename`, raising `RosterConflict` on
      mismatch **and** on the unreadable raise. Without the conversion, Task 12's
      `except RosterConflict` never fires for that case, **no recovery draft is written, and the
      draft is lost** — with the guard and its test both green
- [x] add `write_draft_file(path, lines)` — the **ungated** sibling for recovery drafts; same
      temp+rename shape, no comparison, because a freshly minted unique path has nothing to conflict
      with
- [x] document the residual TOCTOU window in `write_roster_file`'s docstring — a writer landing
      between the compare and the rename is still lost — and why that is acceptable here
- [x] bump `agb-peer`'s `VERSION` (`agb-peer:56`)
- [x] write tests: `write_roster_file` succeeds on a matching gate; on a mismatched gate raises
      `RosterConflict`, leaves the target **byte-identical**, and leaves **no** temp behind
- [x] write tests: against an **unreadable** existing file it raises **`RosterConflict`
      specifically** — `pytest.raises(peer.RosterConflict)`, not `PeerError` (R3: the loose
      assertion passes on the wrong class and hides the lost-draft bug). Mutation-check this one
- [x] write tests: the written file's mode is the **chosen literal**, asserted as a number
- [x] write **AST guards** modelled on `tests/test_agb_peer.py:1799`: `write_roster_file` contains a
      real `rename` `Call`; `write_draft_file` contains a `rename` and **no** `roster_bytes`
      comparison; **no** `import tempfile` anywhere in `agb-peer`. Each with its non-vacuity assertion
- [x] add the `CHANGELOG.md` entry in **this** commit (repo rule: same commit as the code)
- [x] run tests — **339 passed**
- [x] ➕ mutation-checked five guards: dropped conversion, skipped gate, gated draft writer,
      fixed temp name, dropped `fchmod`. ⚠️ **The fifth was VACUOUS at first** — `O_CREAT` mode
      `0600` under the ordinary umask `022` needs no restatement, so deleting `fchmod` left every
      mode assertion green. Measured under `umask 0600`: `O_CREAT` yields **0000**. Added
      `test_the_mode_survives_a_umask_that_strips_owner_bits`; the mutation now fails. The guard
      was right, the test was not.

### Task 3: `agb-peer-setup` skeleton, sibling loading, and module identity

**Files:**
- Create: `agb-peer-setup`
- Create: `tests/test_agb_peer_setup.py`
- Modify: `tests/conftest.py`
- Modify: `tests/test_agb_peer.py`

- [x] create `agb-peer-setup` (`#!/usr/bin/env python3`, executable, own `VERSION`/`--version`)
- [x] implement `_load_peer(path=None)` with **`SourceFileLoader`** + `spec_from_file_location`,
      defaulting to `agb-peer` beside **`os.path.realpath(__file__)`** (R8) — citing
      `agb.sibling_path` (`agb:2626-2632`), whose docstring is exactly this case, because this
      plan's only install instruction is to **symlink** the tool
- [x] early-return `sys.modules.get(key)`, and `del sys.modules[key]` on a failed `exec_module` —
      both from `agb._load_sibling` (`agb:2643-2675`); the second because a half-initialised module
      would otherwise be returned by the next call as if it had loaded
- [x] ⚠️ **move the `peer` fixture from `tests/test_agb_peer.py:21-28` into `tests/conftest.py`,
      delete the local definition, AND make it register the module in `sys.modules` under the
      pinned key before `exec_module`** (R5). The existing fixture does **not** do this; conftest's
      `agb` fixture does (`tests/conftest.py:138`). A verbatim move leaves `_load_peer`'s lookup a
      miss, builds a second module object, and **fails** the identity test below
- [x] pin the `sys.modules` key as a named cross-file agreement (CLAUDE.md invariant 14) — the
      fixture's key and `_load_peer`'s key are one string. Note `_load_sibling` uses `setdefault`
      where conftest uses assignment; pick one and say which
- [x] define `USAGE`; implement `main(argv)` dispatching `validate <file>` (body in Task 13) and
      bare `<file>` (body in Task 10) — **the dispatch table is written once, here**
- [x] implement the `__main__` guard: `PeerError`, `KeyboardInterrupt`, **`agb.AgbError`**
      (`agb_ops:1859-1888`) **and `OSError`/`IOError`** (R1) — `agb.read_config` re-raises every
      errno except `ENOENT`/`ENOTDIR` (`agb:152-170`), and an unreadable config must not traceback
      out of a menu. `AgbError` lazily, since `agb` may not be loaded
- [x] write tests: `setup.PeerError is peer.PeerError` — the named identity guard
- [x] write tests: `_load_peer()` resolves `parse_roster_text`/`parse_participants`/`Ctl`/
      `match_sessions`, asserting the walk found them before asserting content
- [x] write tests: a failed `exec_module` leaves **nothing** in `sys.modules` under that key
- [x] write tests: `_load_peer` resolves through a **symlinked** entry point (R8) — the install
      shape, which a checkout-relative test cannot see
- [x] write tests: no path argument → usage, exit 1, **no filesystem write**
- [x] run both files — **339 + 10 passed**; full suite **2357 passed**
- [x] ➕ the `__main__` guard needed a **fourth** class shape: the four names cannot be spelled in
      an `except` clause without importing the sibling first, which a usage error must not
      require — so they are matched by `type(error).__name__`, with a structural test
- [x] ➕ that structural test's **first version asserted over an empty set** — `type(e).__name__`
      is an `Attribute` over a `Call`, not a `Call`, so the walk matched nothing. Caught by its
      own non-vacuity assertion, which is what that convention is for

### Task 4: Discovery, and deriving a roster-legal `<row>` from a picked row

**Files:**
- Modify: `agb-peer-setup`
- Modify: `tests/test_agb_peer_setup.py`

- [ ] implement `row_value(session)` (**the feature**): **strip a leading `"[?] "` / `"[done] "`**
      (`agb_mac:1346-1347`) — R2, without which every stale or finished row is unpickable — then
      take the first ` · ` component; return it plus a reason when unusable (whitespace, `@`, `:`,
      or empty). Never fall back to the row id (`docs/commands.md:1665`)
- [ ] comment the **`row_fields` assumption** (R2): the default ordering puts the label first
      (`ROW_FIELDS_DEFAULT`, `agb_mac:1358`), and `row_fields = host,label` would put the host
      there instead. State the assumption and its failure mode rather than detecting it
- [ ] implement `row_is_unique(sessions, candidate, picked)` — `match_sessions` (`agb-peer:380`)
      returns the **first non-empty tier** and the id-prefix tier fires before the name tier, so
      assert `len(matches) == 1` **and `matches[0]["id"] == picked["id"]`** (R2); a label that
      prefixes another row's id is otherwise a unique match on the **wrong** row
- [ ] implement `format_candidates(sessions)` — pure, `(display_line, session)` pairs; the caller
      numbers them
- [ ] display `name`, short `id`, `cwd`, `status`. **Decide MODE explicitly**: `cmd_list` reports it
      (`agb-peer:1570-1578`) because "a listing that disagrees with the tool it is a listing FOR is
      worse than no listing". Recommendation: **omit** — one `session text` per row makes the picker
      O(n) agterm calls interactively, and this listing feeds an entry resolved later anyway. Record
      the omission **and this reason** in a comment
- [ ] implement `discover_rows(ctl, say)` — fresh `ctl.tree()` each call; on `PeerError` report via
      `say` and return `[]`
- [ ] write tests: `row_value` on a default title (`label · host · cwd · %7 · 3s`) → `label`; **on a
      `"[?] label · host · …"` title → `label`** (R2's regression — mutation-check it, since the
      unprefixed fixture alone hides the bug); on a label containing a space/`@`/`:` → the reason
- [ ] write tests: `row_value` never returns the row id, for any input
- [ ] write tests: `row_is_unique` is False when the sole match is a **different** row reached by
      the id-prefix tier (R2), False when two rows match, True for the picked row
- [ ] write tests: `format_candidates` over a fixed list, asserting non-empty first
- [ ] write tests: `discover_rows` with a `PeerError`-raising `Ctl` returns `[]`, calls `say`, does
      not raise; two calls issue **two** `tree` calls
- [ ] run tests — must pass before task 5

### Task 5: Transport hint and instance-correct host resolution

**Files:**
- Modify: `agb-peer-setup`
- Modify: `tests/test_agb_peer_setup.py`

- [ ] implement `_load_ops(agb_path=None)`: load **`agb`** by path (default
      `~/.local/lib/agbridge/agb` per `agb-refresh:39`, injectable override, hard existence check),
      then call **`agb._load_ops()`** (`agb:2682`) — do **not** reimplement it (R7).
      `tests/conftest.py:155-162` routes through that door deliberately so a broken door fails
      tests rather than being bypassed. Lazy: only on the `[s]` branch
- [ ] a failed `_load_ops` is a **reported** fallback to manual entry, never a crash
- [ ] implement `agbridge_hint(session)`: locate `pane` in `foreground`, call
      **`agb_ops.parse_pane_args(foreground[i+1:])`** (`agb_ops:1830`) for a complete `opts`; return
      `None` when the argv is not an `agb pane` command or the parser refuses it
- [ ] ⚠️ **thread ONE `unreadable` list through both readers** (R1): `row_target(hint, unreadable)`
      passes **that list** to `agb_ops.pane_settings(opts, unreadable=unreadable)` — not a fresh
      `[]`, which is what made the next check dead — and `host_choices(hint, unreadable, say)` takes
      it too, either reusing the `config` dict `pane_settings` already resolved or wrapping its own
      `agb.read_config` in `try/except OSError` and appending
- [ ] enumerate the pick-list via `agb.read_config(...)` filtered on
      `agb_ops.CONFIG_KEY_PREFIXES` (`agb_ops:238`) — `pane_settings` returns one target and cannot
      do this
- [ ] when `unreadable` is non-empty, **report and offer no list** — distinct from "no hint, no
      list" (CLAUDE.md invariant 12; `agb_ops.pane_config_warning`, `agb_ops:2079`, is the house wording)
- [ ] implement `offer_agbridge_default(hint, config)` as a **pure predicate** (R9): True only when
      a `--host` was found **and** `ssh_target_for(host, config) == host`, because
      `scan_participant` uses that host **verbatim** (`agb-peer:2081`). The prompt wording that acts
      on it belongs to Task 7
- [ ] write tests: `agbridge_hint` on a bare shell → `None`; on a real `agb pane --config X --host Y
      --pane Z` argv → a complete `opts`; on an argv `parse_pane_args` **refuses** → `None`, not an
      `AgbError` escaping the menu
- [ ] write tests: reuse `tests/conftest.py:514`'s `instance_config` fixture rather than inventing a
      parallel one — a hint carrying `--config B` enumerates B's `host_<name>` table, **not** A's
- [ ] write tests: a hint with **no** `--config` resolves against the default config — **every**
      default install, not a legacy case (`agb_mac.pane_argv` withholds the flag)
- [ ] write tests: an unreadable config → the `unreadable` list the **caller** passed is non-empty
      (R1's regression: a fresh-literal implementation leaves it empty and passes a weaker test),
      no pick-list, and **no** `OSError` escapes
- [ ] write tests: `offer_agbridge_default` is False when `host_<name>` maps the host, True when it
      does not
- [ ] run tests — must pass before task 6

### Task 6: Draft state and file loading (including repair and unreadable paths)

**Files:**
- Modify: `agb-peer-setup`
- Modify: `tests/test_agb_peer_setup.py`

- [ ] implement `Draft`: `entries` (ordered list), `dirty`, `loaded` (the `roster_bytes` answer),
      `path`
- [ ] implement `Draft.load(path, say)` with **`minimum=1`**, building `entries` in the **file's
      line order** — re-derive from the lines, not from `parse_roster_text`'s dict
- [ ] repair paths: **empty / malformed / non-UTF-8 / one-participant** each report and yield a
      usable draft, with `loaded` preserved so a later `w` still gates against it
- [ ] ⚠️ **unreadable is not a repair path**: `roster_bytes` raises, so refuse to open, say so, and
      exit — opening it would mean a later write gates against nothing and renames over a file
      nobody could read
- [ ] comment the decision: a malformed file's surviving lines are **not** salvaged line-by-line in
      v1 (the parser is all-or-nothing); the draft starts empty with `loaded` still set
- [ ] write tests: a valid **one-participant** roster loads and yields one entry
- [ ] write tests: line **order** survives a load (3 entries)
- [ ] write tests: empty / malformed / non-UTF-8 each report and yield a usable draft with `loaded`
      preserved
- [ ] write tests: an **unreadable** file → `pytest.raises` at load, non-zero exit, nothing written
      (R10 — revision 3 asked for a sentinel-inequality assertion that cannot exist, since there is
      no unreadable *value* to compare)
- [ ] write tests: a missing path yields an empty, non-dirty draft with `loaded is None`, and `None`
      is distinguishable from `b""` (empty-but-present)
- [ ] run tests — must pass before task 7

### Task 7: Add flow (`a`)

**Files:**
- Modify: `agb-peer-setup`
- Modify: `tests/test_agb_peer_setup.py`

- [ ] implement `cmd_add(draft, ctl, read_line, say)`: discover → picker → **`row_value` +
      `row_is_unique`** (Task 4), re-prompting with the reason → name prompt validated by
      **`parse_participants([render(e) for e in draft.entries] + [new_word], minimum=1)`** (the
      whole draft, so alphabet, reserved `relay` **and** duplicates are all the real parser's
      answer) → pane-kind prompt (`PANE_KINDS`, default `left`) → transport prompt `[a]/[l]/[s]/[t]`
- [ ] the `[a]` arm consumes `offer_agbridge_default` (Task 5): when False, **withhold `[a]`, say
      why, and pre-select `[s]`** with the mapped target (R9 — the wording lives here, where the
      prompt exists, not in Task 5 where it could not be tested)
- [ ] print the generated canonical line via `render_roster_lines`
- [ ] write tests: one full flow per transport shape, asserting the **exact** canonical line
- [ ] write tests: a `relay` name is refused and re-prompted, draft unchanged
- [ ] write tests: a **duplicate** name is refused and re-prompted — mutation-check it by reverting
      to the single-word call, following the Testing Strategy's `__pycache__` step exactly
- [ ] write tests: `[a]` is withheld with a reason and `[s]` pre-selected when `host_<name>` maps the
      host (R9's regression, now testable because the prompt exists)
- [ ] write tests: a picked row whose label has a space, and one matching two rows, are each refused
      with the reason; manual entry still works
- [ ] write tests: each pane kind renders correctly, and `left` renders **without** `:left`
- [ ] write tests: discovery failure mid-flow falls back to manual entry without aborting
- [ ] run tests — must pass before task 8

### Task 8: Delete flow (`d`)

**Files:**
- Modify: `agb-peer-setup`
- Modify: `tests/test_agb_peer_setup.py`

- [ ] implement `cmd_delete(draft, read_line, say)`: numbered list over the ordered `entries`, pick
      or cancel, remove, set dirty
- [ ] write tests: delete by index on a **3-entry** draft removes the right entry and preserves the
      order of the rest
- [ ] write tests: cancel and out-of-range leave the draft byte-identical
- [ ] write tests: deleting down to **one** entry is allowed — it is the removal workflow
- [ ] run tests — must pass before task 9

### Task 9: Raw-edit escape hatch (`e`)

**Files:**
- Modify: `agb-peer-setup`
- Modify: `tests/test_agb_peer_setup.py`

- [ ] implement `cmd_edit_raw(draft, read_line, say)`: pick an entry (or "new"), pre-fill with its
      canonical **`str`** line
- [ ] ⚠️ **validate with `parse_roster_text` on the joined draft, NOT `parse_participants`** (R6).
      **Measured**: `parse_participants(['alice=my row'], minimum=1)` → `{'alice': ('my row',
      'left', None, None)}` — **accepted**, because it receives words already split and never
      rejects whitespace inside `<row>`. Only `parse_roster_text` rejects it, by splitting
      (`agb-peer:1058-1061`). Use the same call Task 11 uses, `minimum=1`
- [ ] on invalid input show the **exact** `PeerError` text and the prior canonical line, leaving the
      draft untouched — otherwise the hatch is a trap, which is this checkbox's whole point
- [ ] write tests: **a raw line containing a space is refused** (R6's regression — it passed before)
- [ ] write tests: a valid raw edit replaces the entry **in place** and sets dirty
- [ ] write tests: an invalid raw edit leaves `entries` equal to its pre-attempt value, with the
      parser's own wording in the message
- [ ] write tests: a raw line naming `relay`, or duplicating another entry's name, is refused
- [ ] run tests — must pass before task 10

### Task 10: View (`v`), dirty-aware quit (`q`), and the menu loop

**Files:**
- Modify: `agb-peer-setup`
- Modify: `tests/test_agb_peer_setup.py`

- [ ] implement `cmd_view(draft, say)` — renders the draft, no disk touch
- [ ] implement `cmd_quit(draft, read_line, say)` — no prompt when clean; confirm when dirty
- [ ] implement `render_menu(draft)` and `main_loop(draft, ctl, read_line, say)`, wiring Tasks 7–9's
      handlers and filling Task 3's interactive dispatch arm
- [ ] unrecognised input re-shows the menu and changes nothing — the safe default here, unlike
      `agb pane`'s fall-through-to-attach
- [ ] write tests: quitting clean exits with no prompt; quitting dirty prompts, and "no" returns to
      the menu with the draft intact
- [ ] write tests: an unrecognised key leaves the draft byte-identical
- [ ] run tests — must pass before task 11

### Task 11: Write flow (`w`) — happy path

**Files:**
- Modify: `agb-peer-setup`
- Modify: `tests/test_agb_peer_setup.py`
- Modify: `CHANGELOG.md`

- [ ] implement `cmd_write(draft, ctl, say)`: validate via
      `parse_roster_text("\n".join(render(entries)).encode(), minimum=1)`; invalid → message, stay
      in the menu, **no** disk touch
- [ ] decide and state whether the file ends with a trailing newline — `"\n".join` produces none,
      which `parse_roster_text` tolerates (`splitlines`) but is user-visible in an editor.
      Recommendation: append one
- [ ] if `len(entries) < 2`, **warn and continue**, naming the distinction: a running relay accepts
      one participant, a starting one refuses it
- [ ] resolve every entry against a **fresh** tree and warn when two names land on one row — the
      failure `_one_name_per_row` (`agb-peer:2253`) exists for, which a picker makes easy and
      `parse_participants` cannot see
- [ ] call `write_roster_file(path, lines, draft.loaded)`; on success print `wrote <path>` and the
      literal `agb-peer relay --roster <path>` command; clear dirty; refresh `loaded`
- [ ] add the user-visible `CHANGELOG.md` entry in this commit — name the symptom (hand-writing the
      grammar and the atomic-write discipline), not just "added a builder"
- [ ] write tests: an empty draft is refused with the parser's message, target untouched
- [ ] write tests: a **one-entry** draft **is written**, with a warning — mutation-check it (flip to
      a refusal, confirm this named test fails), following the Testing Strategy's `__pycache__` step
- [ ] write tests: two entries resolving to one row produce a warning naming both
- [ ] write tests: happy path produces the expected bytes and next-command string
- [ ] write tests: after a successful write, `dirty` is False and `loaded` matches, so an immediate
      second `w` does not conflict
- [ ] run tests — must pass before task 12

### Task 12: Write flow (`w`) — conflict detection and recovery

**Files:**
- Modify: `agb-peer-setup`
- Modify: `tests/test_agb_peer_setup.py`

- [ ] extend `cmd_write` to catch `RosterConflict` and **immediately** persist the draft with
      **`write_draft_file`** — the **ungated** writer — to a unique path beside the roster, printing
      it before asking anything
- [ ] implement the submenu: `[v]` view draft, `[r]` reload (confirmed; the only destructive action;
      resets `entries`, `loaded`, clears dirty), `[q]` quit leaving the draft, `[enter]` back to the
      editor. **Deferred deliberately**: a `difflib` display and `[c] view current`
- [ ] a second `w` after `[enter]` re-reads the gate and may conflict again — same code path
- [ ] write tests: mutating the file between `Draft.load` and `cmd_write` leaves the roster
      **byte-identical**, creates exactly **one** recovery file containing the draft, and lands in
      the submenu
- [ ] write tests: the recovery write **succeeds** — a gated writer would raise `RosterConflict`
      from inside the conflict handler
- [ ] write tests: **an unreadable roster at write time also produces a recovery draft** (R3's
      regression) — this is the path that silently lost the draft when the conversion was missing
- [ ] write tests: two conflicts in one session produce two **distinct** recovery files
- [ ] write tests: `[r]` reload resets dirty and `loaded` such that an immediate `w` succeeds
- [ ] write **AST guard**: the recovery path calls `write_draft_file`, which contains **no**
      `roster_bytes` comparison — so a crash mid-recovery leaves the file absent, never half-written
- [ ] mutation-check the conflict gate: make `write_roster_file` skip the comparison, confirm the
      named byte-identical test fails, restore from the `sha256`-verified snapshot — deleting
      `<repo-root>/__pycache__/agb-peer*.pyc` first and confirming the mtime moved
- [ ] run tests — must pass before task 13

### Task 13: Non-interactive `validate` handler

**Files:**
- Modify: `agb-peer-setup`
- Modify: `tests/test_agb_peer_setup.py`
- Modify: `CHANGELOG.md`

- [ ] implement `cmd_validate(path, out)` — `read_roster_file` + `parse_roster_text`; print
      `ok: N participant(s)` or the exact `PeerError` message with a non-zero exit
- [ ] state and comment the `minimum`: **2**, because this answers "would `agb-peer relay --roster`
      **start**?" — deliberately stricter than the editor's `minimum=1`, and the asymmetry is the point
- [ ] the dispatch arm already exists from Task 3 — only the body lands here
- [ ] add its own `CHANGELOG.md` entry — `validate` is a separate user-visible subcommand and the
      repo rule is same-commit-as-the-code
- [ ] write tests: a valid two-participant file → exit 0 and the count
- [ ] write tests: a one-participant file → non-zero, message naming the startup minimum
- [ ] write tests: malformed and non-UTF-8 → non-zero, message **identical** to
      `parse_roster_text`'s own wording (proving no duplicated validation)
- [ ] run tests — must pass before task 14

### Task 14: Verify acceptance criteria (automated only)

*Everything here runs on this machine. Live-agterm checks are Post-Completion and are not duplicated.*

- [ ] walk the **Acceptance Criteria** list, checking off every item verifiable without a live agterm
- [ ] confirm the tool prints the next `agb-peer relay --roster <path>` command — the condition the
      `agb-peer-setup` name rests on; if dropped, revisit the name (`agb-peer-roster`)
- [ ] confirm `agb` is untouched: `git diff --stat HEAD -- agb` is empty (**not** `wc -c`, which
      measures bytes where the budget is characters)
- [ ] run the full suite: `python3 -m pytest tests/ -q`
- [ ] note that `tests/test_core.py:1052-1064` already walks every `tests/test_*.py` for unbounded
      `communicate()`/`.read()`, so the new file inherits that guard

### Task 15: Documentation

**Files:**
- Modify: `docs/commands.md`
- Modify: `docs/design.md`
- Modify: `docs/cookbook.md`
- Modify: `README.md`
- Modify: `CLAUDE.md`
- Modify: `skills/agb-peer/SKILL.md`

- [ ] `docs/design.md` **§6** — the roster write contract, the byte gate, and the conflict semantics
      belong in the authority document CLAUDE.md says is "reconciled against the implementation"
- [ ] `docs/commands.md` — document `agb-peer-setup` beside the `--roster` section, and **state the
      `<row>` derivation rule**: the label, prefix-stripped, not the title (it is user-visible)
- [ ] `docs/cookbook.md` — a "build a roster interactively" recipe
- [ ] `README.md` — add the row to the `agb-*` family table (it runs to `:181`)
- [ ] `CLAUDE.md` — add the tool to the Architecture section's family enumeration; record the
      **why-a-separate-script** reasoning; register the `sys.modules`-key agreement (Task 3) under
      invariant 14; and record the `temp_name`-without-`own_host` divergence (Task 2)
- [ ] ⚠️ `CLAUDE.md` — correct **four** stale numbers, not one (R12). **Measured**: `agb` is
      **105,269 characters / 105,287 bytes**, headroom **30**, against
      `tests/conftest.py:88`'s `AGB_PARSE_BUDGET = 105300`. The file says 103,198 / 103,212 /
      103,200 / "one character". Re-measure before writing — this plan does not touch `agb`, so the
      numbers should still hold at completion
- [ ] `skills/agb-peer/SKILL.md` — point the "make two agents talk" recipe at `agb-peer-setup`
- [ ] confirm the Tasks 2, 11 and 13 `CHANGELOG.md` entries read as one coherent feature
- [ ] move this plan to `docs/plans/completed/`

## Post-Completion

**Manual verification.** This project's history is that menu/discovery features passed every test and
still needed a fix after live use (`CLAUDE.md`, "Where the project is").

- Run against a real agterm with at least two live rows — one agbridge row with a resolvable
  `--host`/`--pane`/`--config`, one plain local tmux session. Confirm the picker shows both, the
  derived `<row>` is the **label** and resolves uniquely, `[a]` is offered only when no
  `host_<name>` mapping applies, and the roster is accepted verbatim by `agb-peer relay --roster`.
- ⚠️ **Include a `[?]` or `[done]` row in that test** — the prefix case (R2) is invisible to a
  fixture whose title has every field populated and no prefix.
- Force a real conflict: two terminals, edit the file from the second before pressing `w` in the
  first. Confirm the roster is untouched, the recovery draft exists with the draft's content, and
  the message is unambiguous.
- Verify the `host_<name>` pick-list on a Mac with **two** configured instances — it must resolve the
  instance tied to the picked row and never silently fall back to the default.
- Remove a participant from a **running** two-person relay and confirm the relay announces the drop
  rather than dying.
- Confirm the written roster's **mode** matches the literal chosen in Task 2, and note that an
  existing file's looser mode is **not** preserved.
- Verify the tool works when invoked through a **symlink** on `$PATH` (R8) — the documented install.
