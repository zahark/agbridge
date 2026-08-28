# A timed-out `session type` is read as "nothing was typed", and the relay retries it

**Found by search**, 2026-08-28, sweeping for more of `CLAUDE.md`'s shape **D** — *an indefinite
outcome collapsed into a definite negative* — after fixing the grid timeout. Not observed live.

## What happens

`Ctl.type` flattens every non-zero rc into one exception with the default code:

```python
    def type(self, target, pane, text):
        rc, out, err = self.run(["session", "type", text, "--target", target, "--pane", pane])
        if rc != 0:
            raise PeerError("session type failed: %s" % (err.strip() or rc,))
        return True
```

`rc` here includes `TIMED_OUT` (124): `_spawn` kills agtermctl after `SUBPROCESS_TIMEOUT` = **30 s**,
which is exactly what a wedged agterm client does. `deliver` types the body with it, and
`try_deliver` decides what to do about the failure by the code alone:

```python
    except PeerError as error:
        ...
        return error.code == 4
```

`1 != 4`, so the message is **held and retried on the next tick**. But a timeout is the outcome where
the text may already be in the peer's composer — agterm may have done the work and simply not
answered. Retrying types it again, which is verbatim the outcome the exit-4 rule exists to prevent:

> ⚠️ **Exit 4 is dropped, not retried.** It means the text WAS typed and could not be verified —
> typing it again would leave two copies in the composer, which is a worse outcome than one lost
> message and a loud line.

The same applies to the second `ctl.type(target, pane, SUBMIT_KEY)`: a timeout there may mean Return
*was* pressed, and the retry re-types the whole body.

## ⚠️ Why the obvious fix is wrong

Making `Ctl.type` raise **code 4** whenever `rc == TIMED_OUT` looks like a one-line change and would
be a regression. `Ctl.type` has two other callers, and for them a timeout means the opposite thing:

- `ensure_composer` types a bare newline to **arm** a detached row. A timeout there says nothing
  about the message — which has definitely not been typed yet — so raising code 4 would make
  `try_deliver` **drop a message that was never delivered**.
- `scan_participant` types the same arming newline. That one is not even guarded: a `PeerError` there
  propagates through `relay_tick` and out of `cmd_relay`, so a wedged agterm **kills the relay**
  during a routine re-attach. That is a separate defect (the "no grid outcome may stop a message"
  property, one call further out) and is worth fixing whether or not this one is.

So the decision belongs at the **call site**, not in `Ctl.type`: what an unknown outcome means is a
question about what was being typed. A `Ctl.type(..., unknown=4)` parameter defaulting to 1, with
`deliver`'s two calls passing 4, is the smallest shape that gets both right.

## Not scheduled

It is a change to the delivery path — the most safety-critical part of the tool — for a failure
nobody has hit, and the review pass that found the grid timeout did not raise it. It wants its own
tests: one where the type times out and the message is dropped with a loud line, and the companion
where the *arming* newline times out and the message is still held.
