# An agent at the startup trust prompt holds every message, for ever

**Found live**, 2026-08-28, by a peer agent starting a throwaway Claude for an unrelated experiment.

> ✅ **THE ORIGINAL PREMISE WAS WRONG, AND THE DANGEROUS HALF DOES NOT EXIST.** This entry was filed
> as *"both gates miss the trust prompt"*, with the hazard that a delivered message might **answer**
> the dialog. Measured 2026-08-28 — the caret gate catches it, so nothing is ever typed:
>
> | pane | `surface cursor` column |
> |---|---|
> | **trust prompt** | **1** |
> | empty composer | **2** (`EMPTY_COLUMN` — the instrument answers) |
> | composer + 9-char draft | **11** (= 2 + 9 — the caret tracks content) |
>
> `1 != 2`, so `wait_ready` refuses with code **3**. The body is never typed and the `\r` second
> call never fires, so **a message cannot answer that dialog**. That hazard is deleted rather than
> softened.
>
> ⚠️ **Both controls were load-bearing.** Without the empty-composer 2, a `1` could have been the
> tool failing rather than the caret moving; without the 2 + 9, "not 2" would not have established
> that the reading tracks content at all.

## What it actually costs, which is not nothing

Code **3** means *held, retried next tick* — and the trust prompt **persists until a human answers
it**. So a peer whose agent came up in an untrusted directory **holds every message for ever**, while
every sender sees `queued for <name>` and exit 0.

🔴 **That is the same presentation as the exit-4 wedge**
([delivery-is-verified-on-the-one-end-a-lost-head-leaves-intact.md](delivery-is-verified-on-the-one-end-a-lost-head-leaves-intact.md)):
a peer that silently stops receiving, visible **only in relay output that neither agent can see**,
and easily attributed to the model declining to answer. Different cause, identical symptom — **the
second entry to end at that signature**, which is why the diagnostic is worth learning once:

> **If a peer stops answering, read the relay's output before anything else.** A repeating hold line
> names the cause; silence at both ends never will.

## ✅ What was fixed here

The refusal line **misattributed it**. `wait_ready` said *"the composer is not empty … somebody has
a draft in it"* for **any** column that was not 2 — so an operator whose agent was sitting on a trust
question was sent to clear a composer that does not exist, and that line is the only thing they ever
see. `caret_reason` now splits the two, on the measurement above:

- **above** `EMPTY_COLUMN` → a draft, as before;
- **below** → *not a composer at all*, naming the trust prompt as the measured case and saying it
  needs a **human**.

⚠️ It reports the column it actually read, and it does **not** claim a 1 *is* the trust prompt — that
is the one measured instance, not an identification.

## What is still open

Nothing dangerous, and no code change is obviously right. The relay cannot answer a trust prompt and
should not; the question is only whether an indefinite hold deserves louder reporting than a
throttled line. See the cross-referenced entry — the answer is probably the same for both.

## Why the report was short a gate, which is the transferable part

⚠️ The entry counted **two** gates. There are **four** checks before a keystroke: `classify`,
`peer_busy`, `pane_busy`, and the caret. The two that got counted are the two whose **docstrings
argue with each other** about being non-redundant; `pane_busy` and the caret are argued in
`wait_ready`'s *body*, and were invisible to a reader following the prose rather than the code.

**An enumeration of defences in a bug report is itself a claim**, and this one was short in the
direction that made the finding sound worse than it was. The same shape as trusting a comment's
threshold over a measurement, one layer up.

⚠️ **And one prior claim in the code reads like an answer to this question and is not.**
`peer_busy`'s docstring says of the *permission dialog* that "the cursor check would be measuring the
dialog's caret". That is a dialog **replacing a composer that already existed**, where the caret is in
the thing that replaced it. At the trust prompt there was never a composer, and the caret is in a
**select list** — which is why it reads 1 rather than 2. Two questions that look like one, and the
docstring's phrasing does not distinguish them.

## The wider question this raised, now demoted

⚠️ **Read the correction above first: the caret gate refuses, so none of what follows is urgent.**
It is kept because the *question* survives its motivating hazard, and because two ideas here were
measured and should not be re-proposed.

The narrow fix once considered was a `BUSY_MARKS`-style screen test for the trust prompt's own text.
Two reasons it was never right, and only the first has changed:

- ~~A mark that stops matching fails **open**, which types into the dialog.~~ ❌ **Withdrawn**: the
  caret gate catches it regardless, so a stale wording match now costs nothing. It is simply
  redundant.
- ⚠️ **The general form is still open.** A glyph is evidence that *something drew a glyph*; a modal
  borrowing the composer's chrome will keep defeating `classify` in a new costume. **Is there any
  positive signal that a pane is a live composer rather than a picture of one?** The trust prompt
  happens to be caught by a caret that lands elsewhere — that is luck, not a defence, and the next
  modal may put its caret at column 2.

⚠️ And the escalation that would follow from *no* — should `peer_busy` refuse an agent with **no**
reported state at all? — remains a real design change with a real cost: it deliberately allows `idle`
through, because refusing "no current information" would strand every row whose bridge is briefly
disconnected.

### ❌ The candidate that died: bracketed paste

Proposed from the placeholder experiment, on the right **shape**: a composer answers bracketed paste
visibly (`[Pasted text #1]` / `paste again to expand`) and a select-list modal has no such
affordance. ⚠️ **What made it attractive was that a paste is CONTENT, not a keystroke** — it cannot
select an option, so the probe cannot answer the prompt it is probing.

❌ **Measured 2026-08-28: the empty probe does nothing.** `\033[200~\033[201~` into a live composer
leaves the screen **byte-identical**. So the zero-content probe does not exist and any probe of this
shape must **carry a body**.

⚠️ **Which is a different proposition, and now a weaker one.** "Cannot answer the prompt" is a weaker
guarantee than "changes nothing", and only the second was what made the idea worth having — and with
the caret gate already refusing, a body-carrying probe would be spending a real risk to buy a defence
against a modal nobody has yet seen. Do not resurrect it without a case where the caret reads 2.
