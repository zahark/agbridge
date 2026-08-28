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

**Still open**, and it needs a real composer:

- Whether the placeholder's own `+N lines` / `NNNN chars` count matches the body — because if it
  does, it is a cheap content-length check for exactly the case where there is no content on screen,
  and the fix follows from it.
- Where in the composer the head goes, and from what size.

## Why it is not fixed here

The obvious repair — probe **both** ends — is not obviously safe. A long body that is *not* pasted
occupies more rows than the `--lines` read window, so its head may legitimately have scrolled out,
and requiring it would fail deliveries that worked. The right answer probably lives in the paste
placeholder's own character count, which is measurable and currently unused. Guessing a change into
the one path that decides whether a message was delivered is worse than recording it precisely.

⚠️ **Until it is fixed, `delivered` means "a tail arrived, or something was pasted".** It does not
mean the peer got what you sent.
