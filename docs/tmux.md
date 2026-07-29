# `agb status-line` — the tmux segment

One line in the tmux status bar answering one question: **is anything consuming the feed?**

```
bridge:UP 2s          a Mac wrote bridge/<mac-id>.beat 2 seconds ago
bridge:DOWN 14m       it last wrote 14 minutes ago
bridge:DOWN never     there is no beat file at all under this mac-id
bridge:UP +3s         the beat is dated in the *future* — the two clocks disagree
bridge:ERR EACCES     the segment could not answer, and says so rather than going blank
```

The claim behind `UP` is deliberately narrow. It is **not** "an ssh process exists" and **not** "a
socket is there" — those are the claims [`agr`](../README.md) made and could not back up. It is
"another machine wrote this file, and here is how long ago". `feed` touches the beat on every poll
(2 s by default); anything older than **30 s** reads `DOWN`, the same threshold `agb doctor` uses,
so the bar and the diagnostic can never disagree.

## Configuration

Verified end to end on tmux 3.5a on the farm box: with the line below and `status-interval 2`, tmux
ran the command 5 times in 7 seconds and each run emitted exactly one line.

```tmux
# ~/.tmux.conf
set -g status-interval 5
set -g status-right '#(AGB_STATEDIR=/shared/.agbridge /bin/python3 -S -E /opt/agbridge/agb status-line --mac-id my-mac) | %H:%M'
```

Four details, each load-bearing:

| Detail | Why |
|---|---|
| `/bin/python3`, absolute | tmux runs `#()` through `sh` with a minimal environment; a bare `python3` that fails to resolve prints nothing, and a blank segment is indistinguishable from "tmux is not running it at all" (constraint #14, the same rule the hooks follow) |
| `-S -E` | skips `site.py` (which scans user site-packages over NFS) and ignores `PYTHONPATH`. Measured: 4.2 ms of interpreter floor instead of ~9 ms |
| `AGB_STATEDIR=…` inline | with it, `agb` never opens the config file to find the statedir. The inline assignment works because `#()` is an `sh` command line — verified |
| `--mac-id my-mac` | with it, the config file is not opened **at all**. Without it the segment still works; it just costs one NFS `open()` of `~/.config/agbridge/config` per tick |

`status-interval 5` rather than the tmux default of 15 s: the beat moves every 2 s, so 15 s makes a
freshly-dead bridge look alive for a quarter of a minute longer than it needs to. Do not go below
1 s — tmux itself refuses to redraw the status line more than once a second.

### Which Mac is being reported

Resolution order, first hit wins:

1. `--mac-id <id>` on the command line
2. `mac_id` in `~/.config/agbridge/config` (written by `install.sh`, Task 9b)
3. **fallback**: the newest `bridge/*.beat` in the statedir

A configured `mac_id` is never second-guessed: if its beat file is missing the answer is
`bridge:DOWN never`, *not* "some other Mac's beat is fresh". Falling back there would report a
different machine as this one. The design assumes a **single Mac** (the plan's YAGNI call), which is
exactly why the fallback needs no arbitration and why the segment never names the Mac it found —
`agb doctor` lists them all, with ages.

## ⚠️ Achievable resolution — do not trust this segment below ~60 s

The number in the bar is an age, and it is honest about the file it read. What it cannot be honest
about is **how quickly this host learns that the file changed at all.** Three independent lags stack
up, and only the first is under this tool's control:

| Lag | Size | Why |
|---|---|---|
| the mtime itself | none | the beat is read with `open()` + `fstat` (constraint #6), which forces a real `GETATTR` on every tick — never `os.stat`, which is served from the attribute cache |
| **finding the file the first time** | **up to 60 s** | `/shared` is NFSv3 with no `ac*` options, so the kernel defaults `acdirmin=30 acdirmax=60` apply. A **negative** lookup is cached the same way: if `bridge/<mac-id>.beat` did not exist when this host last looked, its absence can be believed for up to a minute after the Mac starts writing it. The `bridge/*.beat` fallback is worse still — it is a `readdir`, so a *new* mac-id can be invisible for the same window |
| tmux's own refresh | up to one `status-interval` | tmux does not wait for `#()` to finish: it displays **the previous run's output** and starts a new one. The bar is always one tick behind, by design (`man tmux`, FORMATS) |

So: `bridge:DOWN` → `bridge:UP` can lag by up to ~60 s after the Mac connects, and that lag is a
property of the **NFS mount**, not of the segment. The other direction — `UP` → `DOWN` — is prompt,
because the file already exists locally and its attributes are revalidated on every open.

**Read this as a minute-resolution indicator.** For anything finer, `agb doctor` measures it
properly: it reports every beat with its age, the mount options in force, and the measured clock
skew per host.

### Reading it from machine #3

Machine #3 has no feed, so the beat file is written by neither it nor the Mac it is watching — every
byte of that answer crosses NFS from another host. The 60 s window above is therefore the *normal*
case there rather than the corner case, and `bridge:DOWN never` on #3 while box #2 shows
`bridge:UP 2s` is a cache-age artefact until it has persisted for a minute.

### The clock

The age is `this host's clock` minus `the NFS server's mtime` — the one cross-clock comparison in
the command. Writing a probe file to get a server-stamped `now` (which is how `doctor` does it) costs
a write, a rename and an unlink on a path that runs every few seconds forever, which is not a trade
a display may make. It is safe because this comparison can only ever *display*: it never removes
anything, and a clock disagreement surfaces as the `+` in `bridge:UP +3s` rather than being clamped
away. `agb doctor`'s clock-skew probe is where it gets quantified.

## Cost

Medians of 40 subprocess runs on the farm box, 2026-07-28. A "tick" is the whole
`python3 -S -E agb status-line --mac-id …`:

| | tick | no-change hook |
|---|---|---|
| implementation in `agb_ops` (**shipped**) | **15.2 ms** | 10.82 ms |
| implementation spliced into `agb` instead | 12.2 ms | 10.83 ms (`compile()` +0.25 ms) |
| in `agb_ops`, with no `__pycache__` at all | 20.9 ms | — |
| floors: `python3 -S -E -c pass` 4.2 ms, `agb version` 11.8 ms | | |

`status-line` is the only non-rare command in `agb_ops`, so Task 8 measured the placement rather
than inheriting it. It stays there: the ~3 ms it costs a tick is a background repaint nothing waits
on (0.06% of one core at `status-interval 5`, and tmux does not block on it anyway), while the
~0.25 ms it would add to a hook is latency *inside* a Claude tool call on a budget already at the
top of its band — and moving it would mean raising a size cap that three consecutive tasks declined
to move. The full argument, with the numbers, is in the comment above `status_age` in `agb_ops`.

⚠️ **The `__pycache__` row is a real operational caveat.** `agb_ops` is loaded as a module, so
CPython caches its bytecode next to it — something `agb` itself, having no `.py` extension and being
run as a script, never gets. If the install directory is **read-only**, that cache cannot be written
and every tick pays the full ~9 ms compile, forever. If ticks look expensive, check that
`<install-dir>/__pycache__/` exists and is writable.

## Troubleshooting

| The bar says | It means | Next step |
|---|---|---|
| `bridge:DOWN never` | no `bridge/<mac-id>.beat` exists | wrong `mac_id`, wrong statedir, or the Mac has never connected. Give it 60 s first (see above), then `agb doctor` |
| `bridge:DOWN 14m` | the beat exists and stopped moving | the bridge died, the ssh dropped, or the Mac slept. `agb doctor` on the farm; on the Mac, the launchd job `com.agbridge` and its logs (see the README, *Operating the bridge*) |
| `bridge:UP +3s` | the beat is dated in the future | this host's clock is behind the NFS server's. Harmless for the segment; `agb doctor`'s skew probe says how far |
| `bridge:ERR EACCES` | the statedir or the beat file is unreadable | check ownership and mode — the statedir must be `0700` and yours |
| `bridge:ERR ESTALE` | the NFS handle went stale | usually transient; if it persists, `agb doctor` prints the mount |
| `bridge:ERR status-line: unknown option: …` | a typo in `~/.tmux.conf` | shown in the bar on purpose, so it is not diagnosed by staring at an empty segment |
| *nothing at all* | tmux never ran the command, or `sh` could not start the interpreter | run the exact `#()` body by hand in `sh -c`. This is the failure mode the absolute interpreter path exists to prevent |

The command exits **0** when it could answer (`UP` or `DOWN`) and **1** when it could not (`ERR`),
so it is also usable from a script — but it always prints its one line either way. A status segment
that fails silently is the thing this entire project exists to eliminate.
