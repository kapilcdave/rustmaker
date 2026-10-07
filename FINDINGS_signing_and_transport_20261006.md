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

## What is still unmeasured, and is the only local lever left

`live.rs` takes **no monotonic clock reading per message** — one `unix_us()` at `live.rs:563` and
nothing after. Invisible: parse, `book.apply_delta`, two O(markets) scans (`live.rs:638-655`), the
~135-line / ~20-gate decision block (`live.rs:719-854`), and the gzip journal writes. The prime
suspect is `Journal::row` — a synchronous serde + **gzip deflate** + BufWriter on the decide loop,
at `live.rs:694` (every touch change) and `live.rs:895` (the last statement *before* the
`tokio::spawn` that POSTs the order). `probe` already moves gzip to its own OS thread with the
comment *"gzip must never sit in the receive path"* (`main.rs:602-612`) and `shadow` uses a worker
thread (`residual.rs:56-99`); `live` never got either. `shadow.rs:418-419/622-623`
(`t_recv`/`handle_us`) is the template to port.
