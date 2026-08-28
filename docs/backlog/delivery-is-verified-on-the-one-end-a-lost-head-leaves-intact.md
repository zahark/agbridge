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

**Why the head was lost is not established.** Everything up to `ctl.type` is measured clean; the
remaining suspects are `agtermctl session type` itself and Claude Code's composer under a large
paste. `docs/agtermctl.md` records **no** size clause for `session type` at all — that is the gap.

The experiment needs a live agterm and a real agent row, so it cannot be run from a cluster host:

- `session type` a body of known length with a distinctive **head** *and* tail marker, at 1 KB,
  2 KB, 3 KB, 4 KB, 8 KB, then `session text` and check **which end** survives and from what size.
- Whether the placeholder's own `+N lines` / `NNNN chars` count matches the body — because if it
  does, it is a cheap content-length check for the case where there is no content on screen.

## Why it is not fixed here

The obvious repair — probe **both** ends — is not obviously safe. A long body that is *not* pasted
occupies more rows than the `--lines` read window, so its head may legitimately have scrolled out,
and requiring it would fail deliveries that worked. The right answer probably lives in the paste
placeholder's own character count, which is measurable and currently unused. Guessing a change into
the one path that decides whether a message was delivered is worse than recording it precisely.

⚠️ **Until it is fixed, `delivered` means "a tail arrived, or something was pasted".** It does not
mean the peer got what you sent.
