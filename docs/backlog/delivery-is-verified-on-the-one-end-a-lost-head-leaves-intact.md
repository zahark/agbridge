# `deliver` verifies the tail, and the failure it exists for eats the head

**Found live**, 2026-08-28, from a message that arrived in an agent's composer starting
**mid-sentence**. The peer agent that sent it read `queued … as #id`; the relay read `delivered`.
Neither end saw anything wrong, and the conversation carried on for two more exchanges before a
human noticed the reply did not answer the question.

## Not the mechanism that was first proposed

The first hypothesis was the transport: `send` stashes the body in a tmux pane option, `agb-peer`'s
own comment describes that option as *"the message, 3 KB, exact"*, and there is no size guard in
the code — so an oversized body was assumed to be silently cut.

**Measured on tmux 3.5a, and it is not that.** Binary search on a single-line pane-option value:

| value length | `set-option -p` | `show-options -p` read-back |
|---|---|---|
| 1024 – 16336 bytes | ok, rc 0 | exact, one line, head and tail intact |
| **16337 bytes** | **refused**, `command too long` / `failed to send command`, **rc 1** | previous value retained |

So the ceiling is **16336 bytes, five times the documented 3 KB**, the message in question was
~3.5 KB and nowhere near it, and — the part that matters — **an oversized write fails loudly.**
`cmd_send`'s `tmux()` helper raises `PeerError` on a non-zero rc, so that path cannot lose a message
quietly. Stages 1–3 (write, `show-options -p` render, `parse_show_options` + `unescape_option`) are
clean to 16 KB. The comment has been corrected in place.

## What actually makes it silent

`deliver` types the body, verifies, then presses Return:

```python
ctl.type(target, pane, body)
probe = body[-40:] if len(body) > 40 else body      # ⚠️ the TAIL
...
if rendered(before, text, probe):
    break
```

and `rendered` is:

```python
return "".join(probe.split()) in flat_text or pasted
```

Two independent holes, and a head-truncated message goes through **both**:

1. ⚠️ **The probe is the last 40 characters.** A message that lost its head still has its tail, so
   the check passes on exactly the damage it is looking at. This hole has no length threshold.
2. ⚠️ **The `pasted` branch verifies no content at all.** A long, fast injection is collapsed by the
   receiving agent into `[Pasted text #1 …]`, so — as `rendered`'s own docstring says — *"the body is
   not on screen at all"*. When that branch fires, the function is not a verification: it is a check
   that **a** paste of **some** length happened. A 3.5 KB message is precisely the size that becomes
   a placeholder, so for the messages most likely to be damaged the content check is skipped
   entirely.

⚠️ **The sharp part is that the probe is blind to the failure the docstring gives as its reason for
existing.** `deliver`'s docstring:

> its value is not that typing might fail — it is that a permission dialog can appear between the
> cursor check and the keystrokes, **swallowing them**.

A dialog swallowing the *leading* keystrokes is a lost head. The verification was written against
that case and then probed at the one end that case leaves intact.

## What is still unknown, and who can measure it

✅ **`session type` is ELIMINATED — measured 2026-08-28** against a raw-mode reader run as an
agterm session's `--command`: byte-exact at 216 B through **64 KB**, head and tail markers intact at
every size, no ceiling found. That is four times past the 16336-byte tmux limit that bounds anything
`agb-peer` can send, so the whole wire is clean well beyond what can reach it. The measured clause,
and the `cat`-based harness that would have incriminated it, are now in `docs/agtermctl.md`.

**So the damage is in the receiving application** — where the analysis above already had it — and
since ~3.5 KB is squarely in collapse territory, **the paste placeholder is the prime suspect rather
than a dialog.** That also means hole 2 above is not merely the wider hole, it is very likely *the*
hole: the message became a placeholder, and the branch that fires for placeholders checks nothing.

❌ **The leading candidate fix is DEAD — measured 2026-08-28.** Claude's placeholder carries **no
count at all**: a 3500-byte paste renders exactly two lines, `❯ [Pasted text #1]` and `paste again
to expand`, with nothing numeric anywhere on the pane. The `NNNN chars` form is **Codex's** alone.
So "verify the body against the placeholder's own count" is unavailable on the Claude side, and
hole 2 needs a different answer.

🔴 **And the same run put the whole paste theory in doubt.** On a fresh composer, 800 / 900 / 3500 /
**8000** raw bytes showed the body **in full**, head and tail, **no placeholder at any size**; only
bodies wrapped in `\033[200~ … \033[201~` collapsed, and those at 900. On that reading the trigger
is **bracketed paste, not length** — and since nothing in this repo wraps anything in bracketed
paste (grepped: no `200~` anywhere in `agb-peer`, `agb-dashboard` or `agb-peer-setup`), a
relay-delivered body would arrive as ordinary typed text, land in full, and **never reach the
`pasted` branch at all.** Hole 2 would then be real but unreachable from the relay, hole 1 (the tail
probe) would remain fully live, and **the lost head would have no mechanism again.**

⚠️ **But that contradicts a measurement already in the tree, and the contradiction is not resolved.**
`COMPOSER_GLYPHS`' comment records Claude collapsing at **843 characters** — observed through this
file's own delivery path, as a live symptom (the verification refused to press Return), not as a lab
result. Both cannot be right as stated.

**The one difference not controlled for**, and it is the next experiment: `ctl.type` passes the body
as a plain **argv element** — `agtermctl session type <text> --target … --pane …` — while the
re-measurement drove **`--stdin`**. If agtermctl brackets one form and not the other, both
measurements are right about different commands, and **only the argv form is on our path**. Until
that is run, neither the 843 figure nor "no placeholder to 8 KB" should be quoted as settled.

✅ **The discriminator ran: argv and `--stdin` are IDENTICAL — no collapse on either**, 800 through
8000 bytes, fresh composer each time. The variable was worth controlling and turned out not to be
the difference. So the 843-character figure **cannot be reproduced on either form** against the
current Claude Code build; treat it as **version-stale rather than wrong** (it was a live symptom),
and do not quote it as current.

**Which settles the consequence**: nothing on our path brackets, so a relay-delivered body arrives
as ordinary typed text, lands in full, and **never reaches the `pasted` branch**. Hole 2 is real and
**unreachable from the relay**. Hole 1 — the tail probe — is fully live and is now the only one.
And **the lost head has no mechanism**; the paste theory is out.

## The composer renders both ENDS and elides the MIDDLE

**Measured 2026-08-28**, and it changes this entry's own conclusion. `session text` does not show the
body — it shows a **rendered, elided** view. A 3488-character filler body with distinct head and tail
markers came back as **1459 visible characters, with both markers present.**

⚠️ **So the stated reason for not probing both ends was WRONG.** This entry said a head probe might
fail because "an unpasted long body's head may legitimately have scrolled out of the `--lines`
window". It does not scroll out: the head is one of the two ends the composer keeps. **A both-ends
probe is available.** (It still cannot see middle loss — the middle is elided, so nothing on screen
can.)

## …and the second measurement makes it less attractive, not more

⚠️ **The rendered view is still SETTLING when it is read.** Same body, two runs, nothing about the
delivery different — only *when* the pane was read: the first found the tail marker **absent**, the
second, slightly later, found it present. So `rendered` can return a **false negative** on a message
that arrived perfectly.

**What that costs is not a duplicate — it is a DROP, and a draft left in someone's composer.**
`deliver` raises code **4** when the probe never appears, and `try_deliver` ends
`return error.code == 4`, with the reasoning stated in its own docstring: *"Exit 4 is dropped, not
retried — typing it again would leave two copies in the composer."* And the Return is sent **after**
the verification, so on that path the body **is in the peer's composer, unsubmitted**, while the
relay says it was not sent and drops it. The next message typed to that peer lands after the
leftover.

⚠️ **Mitigating, and it bounds how often this can happen**: the read is retried `VERIFY_READS = 4`
times with `ctl.sleep(1)` before each, so the repaint has roughly four seconds. A false negative
needs settling slower than that, not merely slower than one read.

⚠️ **So the two findings pull in opposite directions, and that is the decision.** The elision makes a
both-ends probe **possible**; the settling makes any **stricter** probe more likely to false-negative,
and a false negative is a **dropped message plus a stranded draft**. The fix became available and
*less* attractive in the same measurement. Anyone implementing it owes an answer to: what does the
extra condition cost in drops, and should a verify failure still drop rather than hold?

**Still open:**

- Where the lost head actually came from. Every stage is now measured clean and the paste theory is
  dead, so this is back to no mechanism at all.
- Whether a both-ends probe is worth its false-negative cost, per the trade above.

## Why it is not fixed here

The obvious repair — probe **both** ends — is not obviously safe. A long body that is *not* pasted
occupies more rows than the `--lines` read window, so its head may legitimately have scrolled out,
and requiring it would fail deliveries that worked. The right answer probably lives in the paste
placeholder's own character count, which is measurable and currently unused. Guessing a change into
the one path that decides whether a message was delivered is worse than recording it precisely.

⚠️ **Until it is fixed, `delivered` means "a tail arrived, or something was pasted".** It does not
mean the peer got what you sent.
