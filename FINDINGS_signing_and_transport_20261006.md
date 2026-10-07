# FINDINGS — the order-latency budget is venue-side; signing is 16 µs and FIX is tier-blocked (2026-10-06)

Measured on the az2 box `i-0f25ca08ce8d9d016` (t3.micro, us-east-2b, Ohio), chrony-synced
(RMS offset 8.4 µs, root delay 330 µs — good enough for sub-ms claims, unlike the September
`systemd-timesyncd` state). Binary `kalshi-mm15` @ `47bffe5`, sha256 `4a60d081…`.

## Verdict: migrate to Ed25519 (worth ~600 µs, done). Everything else local is worth ~tens of µs.

| measurement | p50 | p99 |
| --- | ---: | ---: |
| **Ed25519 sign, local, incl. header build** | **16 µs** | 31 µs |
| RSA-2048 PSS sign (openssl, same CPU) | ~668 µs | — |
| TCP connect, nearest REST ELB node | 266 µs | — |
| `exchange_status` — unsigned GET | 2,721 µs | 3,768 µs |
| `portfolio_orders_signed` | 5,155 µs | 8,456 µs |
| `portfolio_balance_signed` | 8,047 µs | 9,465 µs |

`ring`'s Ed25519 is **16 µs**, three times faster than openssl's 50 µs on the same Xeon 8259CL,
because ring ships hand-written assembly. Against a 2.7–8.0 ms round trip that is **0.2–0.6% of a
request**.

## The headline: production never had the fast signer

Every deployed binary was a **Sep-30 RSA-PSS-only build**. The Ed25519 signer existed only as an
uncommitted working-tree change until `47bffe5`. Because `Auth::new` picks its branch by sniffing
the PEM for the id-Ed25519 OID, a Sep-30 binary handed the **currently configured** key
(`~/.config/kalshi/key.pem`, a 119-byte Ed25519 PKCS#8) fails at `Auth::new` outright — the RSA
parse rejects it. So this was a **correctness** bug before it was a latency one, and today's
"4.2–4.5 ms signed order" benchmark was taken with a *side-directory* Ed25519 build
(`~/trading/kalshi-mm15-ed25519/`, 19:26Z) that production never used.

**So production was ~618 µs slower than every number we have been quoting.**

## ⛔ The budget is not ours to win: signed endpoints cost 2.4–5.3 ms MORE than unsigned

An unsigned `exchange_status` is 2.72 ms while TCP connect to the same ELB node is 0.27 ms — so
**~2.45 ms is already behind Kalshi's load balancer** before any authentication. Signed portfolio
endpoints then cost a further 2.4–5.3 ms. That delta is **not our crypto** (16 µs); it is
server-side auth plus portfolio lookup. No box, thread, core-pinning or IP choice touches it.

**How to apply:** stop treating local compute as the lever. After the Ed25519 deploy, the
remaining reaction budget is roughly: venue-side processing (~2.4 ms+, untouchable), transport
framing (FIX would remove it, see below), and our own decision path (**still unmeasured** — see
`live.rs` gaps below). Local crypto is finished as an optimisation target.

## ⛔ FIX order entry is real, in-region, and TIER-BLOCKED

Kalshi runs a FIX order-entry gateway — `mm.fix.elections.kalshi.com`, ports 8228-8233,
FIXT.1.1/FIX50SP2, TLS 1.2+ mandatory, Ed25519 the *recommended* logon algorithm, logon
`SendingTime` tolerance 30 s, one active connection per API key. It supports `35=D` new,
`35=F` cancel, `35=G` cancel/replace and **`35=q` mass-cancel (1/sec)** — the last being a far
better risk primitive than N individual cancels.

A session authenticates **once at logon**, so FIX removes per-request HTTP framing *and* the
per-request server-side auth that the table above prices at 2.4–5.3 ms. It is the only identified
lever large enough to reach the ~7.5 ms competitor figure.

**It is not available to us.** Logon with the correct pairing (port **8228**, TargetCompID
**`KalshiNR`**) is rejected with:

```
8=FIXT.1.1|9=129|35=5|34=1|49=KalshiNR|...|58=API usage level is not allowed for FIX
```

Our tier is **`advanced`** (`GET /account/limits`: `usage_tier: advanced`, read/write refill 300/s,
bucket 900, granted `manual`). Port 8229 answers `58=Invalid TargetCompID` for both `KalshiRT` and
`KalshiNR`; 8230 times out. The same gate guards the bigger prize: *"Specific tier eligibility
required for private connectivity (PrivateLink, VPC peering)."*

**How to apply:** FIX and PrivateLink are a **commercial** conversation with Kalshi, not an
engineering task. Do not build a FIX engine before the entitlement exists — the logon reject above
is the cheap pre-flight, and it costs one TLS connection to re-run.

## ⚠ If FIX ever opens, the AZ answer may INVERT

TCP connect from the az2 box: nearest FIX order-entry IP `18.223.244.34` = **0.882 ms**, the other
`18.119.210.162` = 1.45 ms, market-data `18.220.63.230` = 0.95 ms — against **0.266 ms** for the
nearest REST ELB node. **Neither FIX IP is in our AZ.** The az2 placement was optimised for the
REST backend ([[FINDINGS_latency_az_ip_20261006.md]]); FIX appears to favour a different AZ. Re-run
the per-AZ probe against the FIX IPs before moving on a FIX migration — and note the probes used
for the REST matrix have been terminated.

## Capacity ceiling, while we are here

`advanced` = 300 tokens/s read and write, bucket 900. At the documented default of 10 tokens per
request that is **~30 orders/sec sustained, ~90 burst**. Batch cancel costs 2 tokens per order, so
**~150 cancels/sec**. REST and FIX drain the *same* buckets — FIX is a latency play, never a
throughput one.

## Pre-signing is viable but no longer interesting

Measured venue tolerance on `GET /portfolio/balance`, each request uniquely signed and used once:
`now`, ±2 s and ±10 s all return **200**; ±60 s returns **401 `header_timestamp_expired`**. So the
window is symmetric and ≥±10 s, and **future timestamps are accepted** — which makes pre-signed
cancels (`DELETE /portfolio/orders/{id}`, path known once an order rests) perfectly feasible with no
reliance on replay tolerance.

**But it is now a 16 µs optimisation.** Worth building only if a stage-timing pass shows signing
has somehow become material. It has not.

## Ed25519 migration completed 2026-10-07 — and a correction

Full key census, one read-only `GET /portfolio/balance` per credential:

| env | key id | alg | status |
| --- | --- | --- | --- |
| `trading/kalshi-mm15/.env`, `kalshi-mm15-cpen-live/.env` | `68c396c3` | RSA-PSS | **DEAD 401** |
| `trading/secrets/.env` | `78ed31d4` | RSA-PSS | **DEAD 401** |
| `trading/xvenue/.env` | `6ea3fca8` | RSA-PSS | **DEAD 401** |
| `.config/kalshi/env.trade` | `641a25b1` | RSA-PSS | **DEAD 401** |
| **`.config/kalshi/env`** | **`2e88fe77`** | **Ed25519** | **LIVE** |

**There is exactly one live credential and it was already Ed25519**, so the venue-side migration was
complete before we started. What remained was that five configs still pointed at dead RSA keys.

### ⚠ Correction: gate_probe's 401 storm was a DEAD KEY, not the algorithm

Earlier in this document the Sep-30 RSA-only binary is blamed for `gate_probe`'s
`ws connect: HTTP error: 401 Unauthorized` and 0-byte tapes. **That was wrong.** `gate_probe.sh`
did not use the configured Ed25519 key at all — it set its own
`KALSHI_PRIVATE_KEY_PATH=$HOME/.kalshi/key_mm15.pem`, an **RSA-2048** key the old binary parsed
perfectly well. Its *key id* `68c396c3` was dead at the venue. The algorithm claim is still true of
anything using `.config/kalshi/env` (an RSA-only binary rejects that 119-byte Ed25519 PKCS#8 at
`Auth::new`), but it was not this job's failure. **A 401 names an unusable credential; it does not
tell you which part is unusable — check the key id and the algorithm separately.**

### What was changed

- `kalshi-mm15-penny4/kalshi-mm15` swapped to the Ed25519-capable build
  (sha256 `3802193a…`, from commit `94d902e`); previous RSA-only binary kept as
  `kalshi-mm15.rsa-sep30.bak` (`fc72e0a3…`). `PROVENANCE.txt` written alongside. Deliberately
  **not** applied to `rustmaker/rustmaker` or `xvenue/*`: despite the shared remote, that binary
  exposes a `rustmaker` subcommand this build does not, so it is a different tool.
- `gate_probe.sh` now sources the single live credential instead of the dead RSA one. Collection
  resumed immediately: **tape 335 KB and stats 24 KB within 90 s, zero 401s**, against 0-byte files
  for the preceding ~7.5 h. Script backed up as `gate_probe.sh.bak-20261007`.
- Dead RSA material retired to `~/.kalshi/dead-20261007/` and
  `~/.config/kalshi/dead-20261007/` (a move, so reversible) with a README, so no job can silently
  pick up a dead key again.

### ⛔ The four remaining dead configs were deliberately NOT repaired

`secrets/.env`, `xvenue/.env`, `kalshi-mm15/.env` and `cpen-live/.env` still carry dead ids, and
several drive order-placing rigs. **A dead credential is the only thing keeping a forgotten
executor inert** — exactly the `live_sports_loop.sh` case from 10-06, which was respawning
`rustmaker --exec --max-contracts 5` and had logged 31,118 401s while unable to trade. Repointing
those at the live key would be an **arming action**, not a fix. Each needs a freshly issued, scoped
key applied deliberately, and the collector ought to get its own read-only key rather than keep
sharing the trading credential.

### Feed age replicates independently

The restarted probe immediately reproduced the headline below on a **different series via a
different code path**: `KXXRP15M.trade.feed_age_us` **p50 5,905 µs / p1 3,544 µs** (`probe` mode,
XRP) against **5,749 / 3,635** measured in `live` on ETH. Two code paths, two series, same ~5.7 ms
floor — the number is not an artifact of the `live` instrumentation.

## ⚑ MEASURED: the decision path is 45 µs. The MARKET DATA FEED is 5.7 ms.

Stage timers added to `live.rs` and run `--dry-run` on `KXETH15M` for 3 min on the az2 box
(19 reporting intervals, ~2,653 frames per 10 s = **265 msg/s**). Medians of per-interval quantiles:

| stage | p1 | p50 | p99 |
| --- | ---: | ---: | ---: |
| **feed_age — venue `ts` → our receipt** | **3,635 µs** | **5,749 µs** | **10,058 µs** |
| parse (`serde_json::from_str`) | 1 | 2 | 18 |
| book apply + touch (incl. its journal rows) | 0 | 2 | 16 |
| receipt → `decide` marker | 2 | 8 | 48 |
| **receipt → order spawn (ALL our compute)** | **25** | **44** | **89** |
| journal gzip write | 0 | 4 | 47 |

**The budget closes exactly:** 5.75 ms feed + 0.044 ms our compute + 2.7–3.0 ms order-to-book
≈ **8.5 ms**, against the ~8.2–8.5 ms reaction measured independently. Nothing is unaccounted for.

### Everything we were about to optimise is a rounding error

- **Our entire decision path is 44 µs — 0.5% of reaction.** Driving it to zero cannot move the
  headline number.
- **The journal gzip is 4 µs**, not the hundreds of µs suspected above. It is on the send path and
  that is still ugly, but it is **not worth a writer thread for latency reasons**. The suspicion in
  the section below was wrong, and the measurement is what settled it.
- The two O(markets) scans are **0 µs** at this market count.
- A bigger instance with an isolated core would compress our p99 (48 µs → maybe 20 µs). That is
  **0.03 ms of an 8.5 ms budget.** The t3.micro was never the problem: unlimited credit mode,
  0.2–2.4% CPU, and at 265 msg/s × 8 µs we use **0.2% of one core**.

### ⚠ This is not the drain-time artifact

[[a-receipt-timestamp-measures-drain-time-not-arrival]] is the right objection: a slow select loop
inflates `feed_age` with our own queueing. It is ruled out here. Our drain can only **add**, so the
low quantile bounds true transit, and **p1 is 3,635 µs with the lowest p1 seen at 3,470 µs** — the
*fastest* frame still arrives 3.5 ms after the venue stamped it. Combined with 0.2% core
utilisation, the floor is venue publish + network, not us. Residual caveat: `feed_age` compares our
chrony clock (RMS 8.4 µs) to the venue's clock, so an unknown constant venue bias is possible —
but no plausible NTP error is 3.5 ms, and the level was stable across all 19 intervals
(5,466–5,941 µs p50).

### So the path to ~7.5 ms runs through the FEED, and that is tier-gated too

68% of reaction is waiting for market data. The only identified faster feed is **FIX market data**
(`marketdata.fix.elections.kalshi.com`), behind the same entitlement wall as FIX order entry, and
PrivateLink sits behind it as well. Competitors at ~7.5 ms are most likely buying a better feed
path, not writing tighter code — our code is already within 45 µs of its floor.

**How to apply:** stop spending on local latency. The remaining engineering levers are worth
~0.05 ms combined; the venue-side 8.4 ms is worth 100× that and is bought commercially, not
written. `to_spawn_us` p50 44 µs is the number to defend against regression, not to improve.

(Sampling note: `to_spawn_us` shows `n~29` per interval against 2,653 frames, and reads 0 in
intervals after posting stopped — it only samples frames that actually spawn an order. A p50 of 0
there means "no posts", never "instant".)

## What was still unmeasured before the above — now answered

`live.rs` takes **no monotonic clock reading per message** — one `unix_us()` at `live.rs:563` and
nothing after. Invisible: parse, `book.apply_delta`, two O(markets) scans (`live.rs:638-655`), the
~135-line / ~20-gate decision block (`live.rs:719-854`), and the gzip journal writes. The prime
suspect is `Journal::row` — a synchronous serde + **gzip deflate** + BufWriter on the decide loop,
at `live.rs:694` (every touch change) and `live.rs:895` (the last statement *before* the
`tokio::spawn` that POSTs the order). `probe` already moves gzip to its own OS thread with the
comment *"gzip must never sit in the receive path"* (`main.rs:602-612`) and `shadow` uses a worker
thread (`residual.rs:56-99`); `live` never got either. `shadow.rs:418-419/622-623`
(`t_recv`/`handle_us`) is the template to port.
