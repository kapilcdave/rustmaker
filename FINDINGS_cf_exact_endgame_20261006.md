# FINDINGS — exact settlement feed for Kalshi 15-minute crypto

Date: 2026-10-06. Research/read-only only; no orders were placed.
Preregistration: `PREREG_cf_exact_endgame_20261006.md`.

## Verdict

The forecast is easy. The trade is not.

Kalshi's public event live-data endpoint exposes a one-second trailing
settlement variable whose close point reproduces the published expiration
value. On 2,000 recent altcoin markets, B2 predicts the result with 93.6%
accuracy at 60 seconds, 99.1% at 10 seconds, and 99.85% at one second.

That information does **not** beat observed executable prices:

- development B2/2c: +0.57c per observed print, but only +0.07c when close
  windows are equal weighted; median -1.82c; -7.39c after removing the best 10%
  of windows; chronological halves +2.01c / -1.88c;
- untouched adjacent-history replication B2/2c: -1.26c per print and -1.53c
  per equal-weighted close window; both halves negative; 1c-stressed
  equal-window 95% interval [-4.80c, -0.19c].

The replication closes B1/B2 historical real-print execution. Persistence,
model-agreement, empirical calibration, and an overlap-aware trend model do
not rescue it. GLiNER2.5-Decide fails before P&L because it emits
`trade_yes` on all 64 balanced synthetic states.

The live public-REST panel is still collecting through 10:00 PDT. Its interim
read at 03:06 PDT has 456 final-minute model/book observations and **zero**
signals above the preregistered 2c threshold. The largest paper edge is 0.90c
for B1 and 0.60c for B2. These are non-atomic REST snapshots, not fills.

No new exact-endgame strategy is cleared.

## What was newly found

Official Kalshi interfaces now expose:

1. authenticated one-hertz CF values plus the accumulating final-minute
   quarter-hour average;
2. authenticated raw values at up to 5 Hz for ETH, SOL, XRP, DOGE and BTC;
3. a public event live-data timeseries that reproduces the final published
   settlement value.

The authenticated local credentials returned 401, so A1-A4 were not silently
replaced by a weaker instrument. Amendments 1-8 separately preregister the
public-history, live-REST, and counterparty-calibration work.

## Data integrity

Development panel:

- 2,000 markets: 250 each for ETH, SOL, XRP, DOGE, BNB, HYPE, NEAR and ZEC;
- 1,278 independent quarter-hour close windows;
- every file has 3,601 strictly increasing one-hertz points;
- all 2,000 close points exactly reproduce published `expiration_value`;
- all non-displayed-tie outcomes reproduce the venue result.

Adjacent replication:

- 1,995 usable markets and 1,302 close windows;
- five older rows have missing market metadata and are skipped;
- every usable close point reproduces `expiration_value`;
- all non-displayed-tie outcomes reproduce the venue result.

The historical endpoint rounds/replaces the close-boundary point with the
published expiration precision. A live-versus-later audit found two changed
points among 4,780 matches, both exactly at the market close, and zero
non-close revisions. Models decide before close, so this does not create their
headline forecast accuracy.

## Probability forecast

Development panel:

| seconds left | B1 Brier | B2 Brier | Q1 overlap Brier | B2 accuracy |
|---:|---:|---:|---:|---:|
| 60 | 0.04866 | 0.04676 | **0.04257** | 93.55% |
| 30 | 0.02597 | 0.01900 | **0.01879** | 97.70% |
| 10 | 0.00856 | 0.00709 | **0.00697** | 99.05% |
| 1 | **0.00121** | 0.00131 | 0.00130 | 99.85% |

Q1's overlap-decayed trend is a real forecasting improvement at longer
horizons. It still fails economically.

## Real-print economic upper bound

These rows use another trader's actual execution. They are deliberately
optimistic: the tape proves that price traded, not that equal liquidity
remained for us.

### Development, 2c threshold

| arm | trades | windows | per-print mean | equal-window mean | median | equal-window ex-best-10% | equal-window halves |
|---|---:|---:|---:|---:|---:|---:|---:|
| B1 diffusion | 660 | 533 | -2.15c | -1.67c | -2.95c | -9.33c | +0.94 / -4.27 |
| B2 trend | 653 | 534 | +0.57c | +0.07c | -1.82c | -7.39c | +2.01 / -1.88 |
| Q1 overlap trend | 659 | 536 | -0.64c | -0.81c | -2.57c | -8.49c | +2.52 / -4.14 |
| P1 2s persistence | 648 | 532 | +0.24c | -0.30c | -1.93c | -7.75c | +1.88 / -2.48 |
| P2 5s persistence | 646 | 531 | -0.14c | -0.83c | -2.04c | -8.19c | +1.27 / -2.92 |
| P3 B1/B2 agreement | 530 | 442 | -0.01c | +0.03c | -4.27c | -8.29c | +1.99 / -1.92 |
| C1 empirical | 585 | 433 | -0.08c | -0.04c | -0.11c | -0.19c | +0.02 / -0.09 |

C1 is the important calibration result: conditioning on the causal state and
the observed aggressor side removes nearly all of the apparent B2 edge. After
1c stress its equal-window interval is [-1.18c, -0.84c].

C2/C3 produce only 2-7 trades. Their positive point estimates have no sample
authority.

### Untouched adjacent-history replication, 2c threshold

| arm | trades | windows | per-print mean | equal-window mean | equal-window ex-best-10% | equal-window halves |
|---|---:|---:|---:|---:|---:|---:|
| B1 | 625 | 531 | -3.31c | -3.29c | -10.37c | -5.21 / -1.37 |
| B2 | 651 | 555 | -1.26c | -1.53c | -8.74c | -2.23 / -0.84 |
| Q1 | 630 | 538 | -1.23c | -1.35c | -8.95c | -2.46 / -0.25 |
| P1 | 649 | 553 | -1.79c | -2.14c | -9.14c | -3.27 / -1.01 |
| P2 | 642 | 546 | -1.02c | -1.36c | -8.58c | -1.86 / -0.87 |
| P3 | 527 | 459 | -3.50c | -3.93c | -11.09c | -4.08 / -3.78 |
| C1 | 479 | 393 | -0.16c | -0.19c | -0.22c | -0.22 / -0.16 |

No powered arm passes both panels.

## Why sharp forecasting becomes a losing trade

On the replication panel, B2/2c selects prints with:

- mean model probability for the observed side: 41.0%;
- actual settlement win rate for that side: 24.9%;
- conditional calibration gap: -16.1 percentage points;
- mean advertised model edge: +14.9c;
- realised P&L: -1.26c.

The exact-index model is accurate on the market population. The subset where
another trader executes against the book is not the market population. The
print itself is selection information, and it points against the naive model
edge. C1 learns that directly and prices the apparent opportunity down to
approximately zero before latency stress.

## GLiNER2.5-Decide

The requested `fastino/GLiNER2.5-Decide` checkpoint was downloaded and run
zero-shot with the four frozen labels:

- `trade_yes`
- `trade_no`
- `skip_uncertain`
- `skip_expensive`

Balanced grid: 2 sides x 4 horizons x 4 probabilities x 2 price states = 64.
Result: `trade_yes` 64/64. The preregistration requires at least three labels.
The arm is degenerate and closed without retrospective P&L tuning.

This agrees with the model card's scope: it is an operational classifier, not a
reasoning or numerical forecasting system.

## What remains live until 10:00 PDT

`collect_public_cf_endgame.py` is recording ETH/SOL/XRP/DOGE once per second:

- exact public settlement-variable points;
- request start/end timestamps;
- the subsequent market snapshot;
- displayed top-of-book sizes;
- discovery and 429 errors.

Interim REST latency:

| request | p50 | p90 | p99 |
|---|---:|---:|---:|
| live data | 83.9 ms | 232.3 ms | 404.4 ms |
| market book | 72.7 ms | 170.8 ms | 248.8 ms |
| serial gap | 2.3 ms | 2.8 ms | 7.9 ms |

The two requests are sequential and non-atomic. Even before that limitation,
the maximum observed post-fee paper edge is below one cent, versus the frozen
two-cent minimum.

## Existing profitable 15-minute crypto result

This study does not erase the corpus's one validated live result:
the altcoin wing scalp had +$21.83 over 150 venue settlements, with a
day-clustered interval excluding zero. Its measured rate was only about
$2.15/day, with throughput/capital and rare-tail risk binding. That is a
different maker/tail strategy, not exact-endgame taker forecasting.

Nothing in this file licenses increasing its size, adding assets, or changing
its caps.

## Reproduce

```bash
python3 audit_cf_rolling_history.py
python3 analyze_cf_rolling_endgame.py \
  --iterations 10000 \
  --output data/cf_exact/report_2000_final.json \
  --trades-output data/cf_exact/trades_base.jsonl
python3 analyze_cf_empirical_prints.py
python3 analyze_cf_persistence_prints.py
python3 analyze_cf_overlap_trend.py
python3 -m unittest tests.test_cf_rolling_endgame -v
```

Prospective scoring after collection:

```bash
python3 analyze_public_cf_live.py \
  data/cf_exact/live/public_20261006_021838.jsonl.gz \
  --fetch-outcomes \
  --output data/cf_exact/live/final_report.json \
  --signals-output data/cf_exact/live/final_signals.jsonl
python3 audit_public_cf_revisions.py \
  data/cf_exact/live/public_20261006_021838.jsonl.gz
```
