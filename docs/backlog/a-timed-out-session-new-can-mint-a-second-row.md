# A timed-out `session new` is read as "no row was created", and can mint a second one

**Found by search**, 2026-08-28, sweeping for more of `CLAUDE.md`'s shape **D** — *an indefinite
outcome collapsed into a definite negative* — after fixing the `agb-peer` / `agb-dashboard` grid
timeout. Not reported by a user; not observed live.

## What happens

`agb_mac._run_command` returns `rc = None` for **two different questions**:

```python
    except OSError as exc:
        return (None, "", "%s" % (exc,))          # the binary never started
    except subprocess.TimeoutExpired:
        proc.kill()
        out, err = proc.communicate()
        return (None, _text(out), "timed out after %gs" % (timeout,))   # it RAN
```

`AGTERMCTL_TIMEOUT` is **10 s**. `_agtermctl` then reports both as `could not run`, which is false of
the second — and, more than cosmetically, `RowRenderer._new` reads `out is None` as *the row does not
exist*:

```python
        out = self._agtermctl(args)
        if out is None:
            return None
```

so `self.rows.bind(key, row, name)` never runs. The key is still unbound on the next poll, so the
bridge issues **another `agtermctl session new`**. If agterm created the row on the first call and
merely failed to answer within 10 s, the agent now has two rows: the second one bound and painted,
the first one orphaned — its id is in no map, so `close-done`, `forget-rows` and `agb-refresh` cannot
reach it, and only `[?]`/nothing distinguishes it on screen.

This is the same defect as the grid one fixed on this branch, one file over: the `else` of a
three-valued outcome read as a proof. `session new` is the call where it costs something, because it
is the only one that *creates* state agterm keeps.

## Why it has not been seen

A 10 s `session new` needs agterm or its control socket to be badly wedged, which is the same
condition that produced the two-minute `agb-peer send` hang recorded in `agb-peer:1531-1537`. It is
rare, and the symptom — a duplicate row — reads like an `agb-refresh` artefact rather than like a
timeout.

## The fix worth considering

Split the two `None`s. `_run_command` already knows which it is; something like an
`rc = TIMED_OUT` sentinel (agb-peer spells it `124`) would let `_agtermctl` say *ran and did not
answer* rather than *could not run*, and let `_new` treat it as **unknown** rather than as *not
created*.

What "unknown" should then do is the part that needs thought, and it is why this is not a two-line
change:

- **Do not retry blind.** Another `session new` is the duplicate.
- `agtermctl session list` / `tree --json` could be asked whether a row with this name now exists —
  but the row's *name* is not unique (two agents can carry the same label), so this needs the same
  care `bind_label_to_config` needed: matching is not choosing.
- Doing nothing for that poll and letting the *next* one re-ask is probably right, but only once the
  re-ask can tell "already created" from "not created".

⚠️ Whatever is chosen wants a live check, because none of this is observable from the test suite:
the stub answers instantly.

## Not scheduled

`agb_mac` is outside the `agb-dashboard` branch's diff, and the honest version of this fix is a plan
of its own. The free half — `_agtermctl` no longer saying `could not run` about a command that ran —
could be taken separately.
