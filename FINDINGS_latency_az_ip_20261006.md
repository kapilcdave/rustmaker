# FINDINGS — Kalshi latency is set by the destination IP GROUP and the SOURCE IP, not the AZ (2026-10-06)

Measured from 3 × t3.micro AL2023 probes in the default VPC, one per us-east-2 AZ
(`i-0f06f74226b361d5a` az1, `i-0b7dbc2d30aa4b77f` az2, `i-032de919db5bcae8a` az3), plus the live
trading box `i-0a6fef62e75fbe959` (t3.micro, us-east-2a) and an az2 clone of it
(`i-0f25ca08ce8d9d016`). Region is pinned to us-east-2 by project constraint, so this is a
within-region placement question only.

## Verdict: pin REST to an az2-group node IP. It buys ~0.6 ms on order→book and ~1 ms round trip.

The AZ of the box is **not** the lever people assume. Kalshi's `external-api*.kalshi.com` ELB nodes
exist in **use2-az1 and use2-az2 only**, and the REST backend sits behind **az2**. What moves
latency is which node IP the connection lands on, and secondarily which source IP you dial from.

| | TCP connect p50 |
| --- | --- |
| to same-AZ ELB nodes | **0.3 ms** |
| az1 ↔ az2 | 0.8 ms |
| az2 ↔ az3 | 0.43 ms |
| az1 ↔ az3 | 1.1 ms |

Pinned REST `/exchange/status`, keep-alive, **HTTP-200-only**, median p50 (ms):

| box | az1-group IPs | az2-group IPs |
| --- | ---: | ---: |
| az1 | 3.43 | 3.41 |
| **az2** | 4.02 | **2.88** |
| az3 | 4.23 | 3.00 |

**az2 box + az2-group IPs wins.** Note the asymmetry: an az1 box gains *nothing* from IP choice
(3.43 vs 3.41 — the backend is reachable at the same cost either way), so the win requires moving
the box *and* pinning the IP. Either one alone is worth roughly nothing.

## Signed order latency, the number that actually matters

The table above is an unauthenticated status call. Re-measured with real signed Ed25519 orders
(p50), az1 box vs the az2 clone with `KALSHI_REST_IP` pinned to an az2-group node:

| | az1 box | az2 clone, pinned | gain |
| --- | ---: | ---: | ---: |
| signed order round trip | 5.45 ms | **4.2–4.5 ms** | ~1.0 ms |
| create → in book | 3.77 ms | **2.7–3.0 ms** | ~0.6 ms |
| amend → in book | 3.01 ms | **2.6–2.9 ms** | ~0.2 ms |

End-to-end reaction time is **~8.2–8.5 ms**, against competitors measured at **~7.5 ms**
(`kalshi-15m-crypto-competitors-react-twice-as-fast-as-the-ohio-box`). So this closes part of the
gap and does not close it: we remain ~0.7–1.0 ms slow at the median. **The WS pin
(`KALSHI_WS_IP`) showed no benefit distinguishable from noise and is not quantified here** — the
code path exists but the az2-group result above is a REST finding only.

## The source IP is worth ±0.4 ms, and one in five is an outlier

ABAB on a single az2 box, same destination group, varying only the source EIP:

| source EIP | p50 |
| --- | ---: |
| 52.14.207.87 | **2.802** |
| 3.133.191.228 | **2.827** |
| 3.151.50.11 | 2.88 / 2.91 (repeat) |
| 3.131.61.121 | **3.30** twice — outlier, released |

One of five sources was a reproducible +0.4 ms penalty, which is most of what the AZ move buys.
**How to apply:** test the final EIP before committing to it and reject any source with p50 > ~2.95.
A connection's speed also persists once established (corr **0.93** over 25 s), so this is a property
of the path, not per-request jitter.

## ⚠ Two traps that contaminated the first round

1. **Hammering the public API poisons the measurement.** 3 probes × ~30 IPs × 55 req at ~100 req/s
   drew **HTTP 429 on nearly every IP**. Round 1 never checked status and its per-IP numbers are
   discarded. Every number in this document is HTTP-200-only at ~20 req/s on 4 connections.
2. **IPs rotate.** The az2-group membership below was true at measurement time and must be
   re-mapped by **TCP connect time (~0.3 ms = local group)** rather than hardcoded.

az2-group nodes seen: `3.128.23.29 18.220.61.73 3.132.102.31 3.21.201.140 16.58.161.178
77.112.106.163 77.112.237.231 3.139.157.109 77.112.152.164 18.189.161.21 3.151.123.9 52.14.151.60
3.141.81.118 3.23.236.150 18.223.167.92 18.225.202.31`.

## Does this change any verdict? No.

Worth stating plainly, because a latency win invites re-opening closed branches. It does not:
`kalshi-maker-latency-cliff` puts **69% of the cancel-latency loss already incurred by 10 ms**, and
this box moves from ~8.5 ms to ~8.2 ms reaction — both deep inside the band where the loss is
already taken. The 0 ms row of that table is clairvoyance, not the fast-trader limit. **No closed
maker branch is re-opened by this measurement**, and none should be re-tuned on it.

## Status

Clone built and measured; **cutover NOT done.** The live box `i-0a6fef62e75fbe959` still runs hermes
and the armed `segment-mm`. The clone was disarmed offline before first boot — see
`ohio-box-az-probe-and-clone-hazard` for why a snapshot of the trading box must never be booted
as-is (it auto-starts a second armed trader on the same key).
