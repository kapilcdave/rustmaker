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
