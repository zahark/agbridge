# A label can collide with another row's cwd, and the roster only notices later

**Found live**, 2026-08-27, by a peer agent, while adding a third participant to a running relay.

## What happens

`match_sessions`' third tier is a substring search over the row's **whole title**, and a title is
`row_fields` joined by `·` — by default `label · host · cwd · pane · beat`. So a selector matches a
row whose **cwd basename** happens to equal another row's **label**.

Measured, with `row_fields = label,cwd:base,pane` and two agents started in the same directory:

```
agbridge-public · agbridge-public · %141      <- label is agbridge-public
codexpeer · agbridge-public · %143            <- label is codexpeer, cwd basename is not

match_sessions(rows, "agbridge-public") -> BOTH
```

The second row's *label* is `codexpeer` and shares nothing with the selector — it collides purely
through its **cwd**.

## Why it surfaced late, and badly

⚠️ **A roster entry can be valid when written and become ambiguous later.** Ambiguity is a property
of the *set of live rows*, not of the entry, so `agb-peer-setup`'s uniqueness check — which does
refuse this correctly at write time — is a point-in-time check that nothing re-runs. The entry here
predated the row that collided with it.

⚠️ **And `resolve_all` masked it.** It falls back to the `previous` binding on a failed resolve
(the `agb-refresh` defence), so the relay kept working right up until it was restarted with no
history — at which point it failed loudly, at the worst moment, for a reason introduced hours
earlier.

## What is NOT true

The reporter concluded there is *no* single word that disambiguates, because `<row>` may not contain
whitespace or `·`. That is nearly right but not quite: **the pane id does** — `%141` matched exactly
one row above. It is a poor answer (fragile, and only present when `row_fields` includes `pane`) but
the design is not airtight-stuck.

## The fix worth considering

Add a tier to `match_sessions` between id-prefix and whole-title: **match the label component
alone** — the title's first `·` segment, which is what `agb-peer-setup`'s `row_value` already
derives. Then `agbridge-public` matches only the row actually labelled that, and this entire class
disappears without touching the roster grammar.

⚠️ It is a behaviour change to a function the relay resolves through on every tick, so it wants its
own plan, its own tests, and thought about what it breaks for anyone deliberately matching on cwd.

Two smaller things worth doing either way — ✅ **both done, 2026-08-28:**

- ✅ **The relay says why.** `resolve_all`'s fallback kept the previous binding **silently**; it now
  reports the reason through `_throttled`, and `resolve` already names the rival rows, so the line
  is what tells an operator the collision is a **cwd** rather than a second label. ⚠️ The silence was
  right for the case it was written for — a row briefly absent while `agb-refresh` re-mints it is
  transient and heals — and wrong for this one, which is permanent. One fallback, two causes, and
  only one of them wanted quiet. The throttle is **cleared on a successful resolve**, or a collision
  that was fixed would stay "already reported" and its recurrence would be silent for ever.
- ✅ **`docs/cookbook.md`** carries it beside the prefix warning, with the measured two-row example
  and the note that an entry valid when written can become ambiguous later.

## Not scheduled

Recorded rather than fixed: it surfaced mid-way through the `agb-dashboard` plan and is a separate
concern. The immediate workaround is to give colliding agents different working directories.
