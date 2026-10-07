# 15M crypto idea battery, 2026-10-06 (candle level, offline, no orders placed)

Scripts: `idea_lab/`. In-sample tape 2026-07-02 → 09-14 (6,953 windows × 9 series). OOS window 2026-09-14 → 10-05
(2,106 windows × 9 series, fetched after `PREREG_altspot_tilt_20261006.md` was frozen).

| # | Idea | Verdict |
|---|------|---------|
| 1 | Open is mispriced vs spot-minus-strike | **No general mispricing.** Mid beats a Coinbase-spot fair value on Brier at k=1..11 (0.1011 vs 0.1082 at k=11). Residual slope of (y-mid) on (fair-mid) is 0.13-0.26 pooled but taker trades net -1.2 to -2.2c at a 2-5c gap. No special effect at open (k=1 slope 0.17 = later minutes). |
| 2 | BTC leads altcoin mids | **Closed.** BTC residual coef: ETH/SOL/XRP 0, DOGE/BNB/HYPE/NEAR *negative* (-0.04 to -0.09), wrong sign. |
| 3 | Settlement-index basis vs Coinbase | **Not the explanation.** Strike basis sd 1.3 (BTC) → 5.6 bps (ZEC); mean +0.6 → +4.7 bps (NEAR). Corrected for in the OOS rule. |
| 4 | Tilt/take on spot gap | **Thin-altcoin effect, see below.** Slope by series: ZEC .50, HYPE .39, NEAR .38, DOGE .20, BNB .12, SOL .09, BTC .05, ETH -.05, XRP -.10. |
| 5 | Hours when fast makers are absent | **Nothing.** 5-min spread is 2.4-3.0c at every UTC hour and by weekday. Live ledger (520 mkts, +$6.78) too thin to split by hour (hour 12 +30c is n=16). |
| 6 | Hourly KXBTCD ladder vs 15M strike | **Closed.** Both settle on the same BRTI 60s average. Ladder interpolated at the 15M strike is *worse* than the 15M mid (Brier .1196 vs .1000 at k=11); slope -0.2 to -0.65, n.s. (328 windows). The 15M book prices its own strike better than the hourly ladder. |

## Idea 4 OOS (prereg `PREREG_altspot_tilt_20261006.md`): PASS by its letter
Rule: end of minute k∈{2,3,5,8}, first k with |fair - quote| > 10c, taker, one contract, net of fee, one trade per market.
- Primary NEAR+ZEC+HYPE: **+2.345 c/ct, lo95 +0.334, n=1,280**; NEAR +2.32, ZEC +1.17, HYPE +3.15 (all > 0).
- Controls BTC+ETH+SOL+XRP: -0.678 (≤ 0 as required), but SOL alone +3.66 and ETH -3.41, XRP -2.71.
- Secondary BNB: **+6.76, lo95 +4.26, n=981**; DOGE +0.56.
- `oos_stress.py` re-run (n 1,305): primary +2.14 [0.12]; H1 +2.28 / H2 +2.02; **top 5 of 23 days = 89% of P&L**, 65% positive days.
  BNB: top-5 share 49%, 83% positive days, H1 +7.17 / H2 +6.27.

## Why this is NOT a deployable edge yet
- **It dies in a minute.** Filling at the NEXT bar's quote: primary **-2.09c** (lo95 -3.87), BNB +2.06 [-0.18]. The edge lives inside the
  minute the signal is computed from, i.e. it is a snapshot/latency effect, the same shape as [[tape-flow-is-a-staleness-relabel]]. Spot-close
  and quote-close are treated as one instant here; a tick tape could shrink or erase it.
- Filling at the in-bar worst price (bar-high ask): primary -8.5c, BNB -4.6c (a bound, not an estimate).
- Candle bars carry no depth; entries average 27c, spread 1.8c. 1-ct depth at the quote is unverified, and capacity is ~20 trades/day.
- Series were chosen after the in-sample slope; the OOS pass is real for that choice, but BNB was *not* chosen (mixed in sample at k=8) and is
  the strongest cell, which is a warning about stability, not a confirmation.
- Prereg pass authorises ONLY a shadow with a tick tape: measure the spot→quote repricing lag on NEAR/ZEC/HYPE/BNB and whether the
  quote at spot-time+lag still shows the 10c gap. Needs a live feed; authenticated access to Kalshi was blocked this session.

## Not done
- ed25519 latency test on the Ohio box (authenticated call blocked by the session classifier).
- No orders were placed anywhere. No live arming.

## Tick-level follow-up (same day): `idea_lab/tick_replay.py`, `tick_catchup.py`
Tape: `data/box/shadow_spot/tape.csv.gz`, 12 h (2026-09-26 06:00Z → 18:00Z), 441 markets, Kalshi top-of-book (B) + Coinbase spot ticks (X).
Tape facts: B `venue_ms` is MICROseconds (X is ms); our spot feed lag p50 11.8 ms [9.7, 18.3]; Kalshi book lag p50 5.6 ms [4.2, 7.5].
L = everything after we receive the spot tick (decide + sign + order transit/ingest). **Ed25519 vs RSA-PSS moves L by about 1 ms.**

1. **Hold-to-settlement, first tick with a 5-10c gap, IOC at the arrival-time book (th 0.10):** primary NEAR+ZEC+HYPE +1.2c (L=0), +2.5c (L=10 ms),
   +3.0c (100 ms), +4.6c (250 ms), -2.4c (500 ms); SE ~4.2c on ~120 trades. BNB -4 to -6c (n~46), controls -9 to -11c (n~172, SE 3.3).
   **No monotone latency dependence at all** and nothing the SE can resolve; the primary sign matches the candle OOS, BNB's does not.
2. **Does the book close the gap to spot-fair?** (sum of mid move toward fair / sum of |gap|, 9,184 primary triggers): **0.1% at 100 ms, 1.3% at 1 s,
   4.5% at 60 s; controls -8.5% at 60 s.** Kalshi does not reprice toward my spot fair value within a minute. So the gap is NOT a stale quote waiting to be
   hit: either my fair value is wrong in ways the book knows (sigma, spot lead, basis) or it is a slow calibration under-reaction that only settlement resolves.
   **This contradicts the "staleness" reading in the section above; treat that explanation as unsupported.**
3. **Gross markout, enter at arrival L, exit mid at +60 s:** primary -0.876c (L=0), -0.850 (10 ms), -0.830 (25 ms), -0.891 (250 ms). 1 ms of L is worth
   about **0.002-0.003 c/ct**, i.e. nothing. Negative before the fee and exit spread.

**Verdict:** ed25519's ~1 ms does not change anything for this signal; the tick data refutes it as a speed edge and cannot confirm the settlement-held
version (n too small). Only a multi-week tick tape on NEAR/ZEC/HYPE/BNB with spot (needs authenticated Kalshi WS) could settle the hold-to-settlement
question, and its ceiling is ~2c/trade at ~20 trades/day.

## Ed25519 latency, MEASURED on the Ohio box (2026-10-06 19:31Z)
Code: `src/auth.rs` now signs Ed25519 (ring `Ed25519KeyPair`, raw signature over `{ts}{METHOD}{path}`) when the PEM is an Ed25519 PKCS#8 key,
RSA-PSS otherwise; unit test `ed25519_pem_signs_and_verifies` passes. Static musl binary at `~/trading/kalshi-mm15-ed25519/` on the box.
100 × (post-only 1 ct YES bid @ $0.01 on KXBTC15M-26OCT061545-45, then amend, then cancel); 0 fills; `data/box/ed25519/lat_ed25519.json`.
The new key authenticates (the 401s of 10-06 08:51Z are resolved by the key rotation).

| order→book (venue µs ts − our send), ms | p10 | p50 | p90 | p99 | 09-23 RSA p50 | change |
|---|---|---|---|---|---|---|
| create | 2.73 | **3.77** | 5.37 | 6.79 | 4.87 | **-1.10** |
| cancel | 2.34 | **3.15** | 4.07 | 6.99 | 4.79 | **-1.64** |
| amend  | 2.19 | **3.01** | 4.15 | 5.75 | 4.60 | **-1.59** |

Signed REST rtt p50: create 5.45, cancel 4.65, amend 4.42 ms. Book-change→our feed p50 5.5-5.7 ms (unchanged: that is Kalshi's publish delay).
Local Ed25519 sign on the box: ~57 µs (Python/OpenSSL; ring is faster) vs 650 µs for RSA-PSS via ring, so signing explains ~0.6 ms of the 1.1-1.6 ms.
**Caveat:** the RSA numbers are from 09-23, a different day and market. The old RSA keys are dead, so there is no same-day control; the remaining
~0.5-1 ms (smaller request, cheaper server-side verify, or day-to-day variance) is unattributed.

Reaction time now (feed 5.5 + order→book): post ≈ 9.3 ms, cancel ≈ 8.7 ms, against competitors' add-after-print p50 7.5 ms (second joiner 10.3).
That is still ~1-2 ms short of parity at the median (9-23 numbers: 9-12 ms, beat 37-45% of refreshes), so ed25519 improves the odds but does not by
itself make us the faster side. The book→us feed leg (5.5 ms) is now the bigger lever, and no key change touches it.

## AZ / destination-IP / source-IP latency work (2026-10-06 evening)
Probes: t3.micro AL2023, one per AZ (HTTP-200-only, ~20 req/s; the first per-IP sweep drew HTTP 429 and was discarded).
- Kalshi `external-api*` ELB nodes exist only in **use2-az1 and use2-az2**. TCP connect: same-AZ node 0.22-0.3 ms, az1<->az2 0.8, az1<->az3 1.1, az2<->az3 0.43.
- Keep-alive REST `/exchange/status` median p50 (ms), pinned: az1 box 3.43 (az1 nodes) / 3.41 (az2 nodes); **az2 box 4.02 / 2.88**; az3 box 4.23 / 3.00. Backend sits behind az2.
- A connection's speed persists (r=0.93 over 25 s). Source EIP matters: ABAB on one box, 3.131.61.121 = 3.30 ms twice vs 3.151.50.11 = 2.88/2.91; others 2.80-2.83.
- Signed order path from the **az2 clone** with `src/main.rs` `KALSHI_REST_IP` / `KALSHI_WS_IP` pinning (new): create rtt 4.2-4.5 ms, to_book create 2.7-3.0 / cancel 2.6-2.8 / amend 2.6-2.9 (az1 box: rtt 5.45, to_book 3.77 / 3.15 / 3.01). REST pinned to az1-group nodes from the same box: to_book 3.2-3.4, rtt 5.3-5.5.
- Feed leg (book change -> us) 5.4-6.4 ms (az1 box 5.5-5.7); az2-group WS nodes 5.4-6.3 vs az1-group 6.2-6.4, noisy, ~0.5 ms at best.
- Full reaction (feed + order to_book), p50: az1 box 9.3 (post) / 8.8 (cancel); **az2 + pinned REST 8.2-8.5 / 8.2**; competitor add-after-print p50 7.5 ms, so still ~0.7-1.0 ms short at the median.
- The az2 clone (`i-0f25ca08ce8d9d016`, from snapshot snap-092a10c7951de163e, disarmed offline: arm file moved, segment-mm / kalshi-wing / altcoin collector / watchdog / hermes-gateway unlinked, manifest `~/DISABLED_AT_CLONE_20261006.txt`) boots clean with nothing running.
