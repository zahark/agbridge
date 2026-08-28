# Both delivery gates miss Claude Code's startup trust prompt

**Observed live**, 2026-08-28, by a peer agent starting a throwaway Claude to get a composer for an
unrelated experiment. Not yet reached by a real delivery, and recorded because both mechanisms that
exist to prevent exactly this are individually blind to it.

## The two gates, and why each one misses

🔴 **THIS ENTRY SAYS "BOTH GATES" AND THERE ARE THREE — the third was never evaluated, and it is
the one most likely to catch this.** Corrected 2026-08-28, from the code rather than a measurement:
`wait_ready` runs `peer_busy` (status), then `pane_busy` (screen text), then the **cursor column**.

| gate | what it asks | verdict on the trust prompt |
|---|---|---|
| `classify(text)` | is `COMPOSER_GLYPHS = ("❯", "›")` on the pane? | ❌ misses — the prompt draws `❯` |
| `peer_busy(status)` | is the agent `active`/`blocked`? | ❌ misses — it has never run a turn, so the status is `-` |
| `pane_busy(text)` | is a `BUSY_MARKS` string on the pane? | ❌ misses — the prompt's wording is not one |
| **`ctl.cursor(surface) == EMPTY_COLUMN`** | **is the caret where an empty composer's is (column 2)?** | ⚠️ **UNMEASURED** |

⚠️ **The fourth is the interesting one and nobody looked.** `EMPTY_COLUMN = 2` was measured for an
empty *Claude composer*; the trust prompt is a **select list**, not a text input, so its caret has
little reason to sit in the same place. If it does not, `wait_ready` already refuses and **this
entry's premise is wrong** — the hole would be a documentation defect rather than a delivery one.
If it does report 2, the hole is real and worse than described, because it survives three gates.

**One command settles it**: `agtermctl surface cursor` against a pane sitting at the trust prompt.
Until that is run, treat everything below as *conditional on the caret reading 2*.

⚠️ And the general lesson stands either way, which is why it is not deleted: **an enumeration of
defences in a bug report is itself a claim, and this one was short by one.** The count came from the
two gates whose *docstrings discuss each other*; the cursor check is argued in `wait_ready`'s body
and was invisible to a reader following the argument rather than the code.

`try_deliver` will not type into a peer unless **all** agree it is safe:

- `classify(text)` — reads the pane and looks for `COMPOSER_GLYPHS = ("❯", "›")`.
- `peer_busy(status)` — reads the agent's own hook-reported state and refuses `active` / `blocked`.
- `pane_busy(text)` / the caret — see the table above.

Claude Code's startup trust prompt — *"Is this a project you created or one you trust?"* — **renders
`❯`**. So:

| gate | what it sees | verdict |
|---|---|---|
| `classify` | a `COMPOSER_GLYPH` in the buffer | `MODE_COMPOSER` — **there is somewhere to type** |
| `peer_busy` | the agent has never run a turn, so the bridge has no state for it: `-` | **not busy** |

⚠️ **This is the Dialog Window Vulnerability that `peer_busy`'s docstring cites as the whole reason
the two gates are not redundant — arriving in the one variant where the second gate does not help.**
The permission-dialog case it was written about happens *mid-session*, so the status is `blocked`
and `peer_busy` catches it. The compaction case (recorded in the same docstring) happens mid-turn,
so the status is `active` and `peer_busy` catches that too. This one fires **at startup, before the
agent has ever been active**, which is precisely the window in which the status gate has nothing to
say.

**Non-redundant is not jointly sufficient.** Two gates that each cover what the other misses still
leave whatever neither covers, and nothing in the code or the docstrings said so until now.

## What typing into it would do

Not measured, and it should be before anything is built on a guess. The hazard is that the trust
prompt is a **choice**, and `deliver` types the body and then sends `SUBMIT_KEY` (`\r`) as a second
call — so a message delivered here plausibly **answers the prompt** rather than being ignored, on
whichever option the modal has focused. That would be a message destroying a decision, in the same
family as `PANE_WORDS` (a message that reduces to `q` closing a live row) and worse in kind, because
`PANE_WORDS` is guarded and this is not.

The `rendered()` check does not save it either: the typed text may well appear on screen, so the
verification passes and the Return is sent.

## What would fix it, and why it is not done here

The narrow fix is a `BUSY_MARKS`-style screen test for the trust prompt's own text — cheap, and in
the mechanism that already exists for reading hazards off a pane. Two reasons to measure first:

- **The prompt's exact wording is a moving target** across Claude Code versions, and a mark that
  stops matching fails **open**, which is the direction that types into the dialog.
- ⚠️ **The general form is the interesting one and a wording match does not address it.** A glyph is
  evidence that *something drew a glyph*; a modal that borrows the composer's chrome will keep
  defeating `classify` in a new costume. The question worth answering is whether there is any
  positive signal that a pane is a **live composer** rather than a picture of one — and if there is
  not, whether `peer_busy` should refuse an agent with **no** reported state at all, which today it
  deliberately allows through (see its `idle` reasoning: refusing "no current information" would
  strand every row whose bridge is briefly disconnected).

That second question is a design change with a real cost on the other side, which is why it is
written down rather than guessed at.

## A candidate positive signal: bracketed paste

Proposed 2026-08-28, from the placeholder experiment, and it is the right **shape** even if it turns
out not to work: **a composer answers bracketed paste and a modal does not.** Wrapping a body in
`\033[200~ … \033[201~` makes Claude Code draw `[Pasted text #1]` / `paste again to expand` — a
composer-specific, *visible* response. A select-list dialog has no such affordance.

⚠️ **The property that makes it better than a wording match is that a paste is CONTENT, not a
keystroke** — so the probe cannot answer the prompt it is probing. A text match on the trust
prompt's wording fails *open* when the wording changes; this fails *closed*, because no response
means no evidence of a composer.

❌ **Measured 2026-08-28: the empty probe does nothing.** `\033[200~\033[201~` into a live composer
produces **no visible change** — baseline and post-probe screens byte-identical. So the zero-content
probe does not exist, and any probe of this shape has to **carry a body**.

⚠️ **That is a materially different proposition and must be argued on its own, not inherited from
the idea above.** Typing content into a pane you are unsure about is the thing this entry exists to
prevent; a probe that does it in order to find out whether it was safe has to justify itself against
the case where the answer is *no*. The content-not-a-keystroke property still holds — a paste cannot
select an option — but "cannot answer the prompt" is a weaker guarantee than "changes nothing", and
only the second one was what made the idea attractive.
