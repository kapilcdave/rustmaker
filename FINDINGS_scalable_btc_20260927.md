# BTC maker capacity investigation — 2026-09-27

**Conclusion: a useful short-horizon toxicity signal, but no demonstrated
$10,000/month strategy.** The next research target is retaining profitable
queue positions in the deep BTC touch, rather than increasing penny-jump size.
The evidence below does not yet establish that the proposed gate makes money.

## VM inspection

Connected using `ssh aws` through the existing Session Manager configuration.
`/home/admin/trading/rustmaker` is an older source checkout; recent binaries and
captures are under `/home/admin/trading/kalshi-mm15`. No trading process was
running at inspection. No orders were submitted, no services restarted, and
no cloud resources created. Approximately 2.8 GB of disk was available.

Analysis used the existing local copy of the 12-hour `shadow_spot` capture,
`data/box/shadow_spot/tape.csv.gz`; VM inventory identified its counterpart as
`shadow_1790416495315.csv.gz`. These copies were not hash-compared. The figures
below are explicitly results for the local file.

## Hypothesis and measurement

A fixed one-second spot-return threshold does not express the risk to a binary
contract: the same BTC move has different probability impact at different
volatility, probability, and time to expiry. Test a one-second book anchor
adjusted for the observable spot move:

```
fair = logistic(logit(mid_1s_ago / 100)
                + 1.6 * log(spot_now / spot_1s_ago)
                      / sqrt(variance_per_second * seconds_to_close)) * 100
```

This is a heuristic local probability adjustment, not a calibrated settlement
model. Variance is the trailing mean of 60 squared one-second log returns;
stale samples invalidate it. Features use observations received before the
trade venue timestamp minus 5.44 ms order latency and an extra 1 ms timestamp
buffer. Repeating with 20 ms order latency checks timing sensitivity.

The maker-side edge is `ask - fair` or `fair - bid`, using the last observable
quote, not the later execution price. Screen thresholds 0, 0.5 and 1 cent were
specified before the first calculation. The most permissive, zero-cent gate
is the candidate discussed here. Midpoint 15–85 cents; more than 120 seconds
to close; sufficient fresh spot history required.

Evaluate both public prints and the `base` queue simulator's existing fills.
Split chronologically by whole markets, never by individual fills. This is
exploratory analysis of an existing tape, not a fresh prospective holdout.
The sampled base arm already has its original momentum/thinness restrictions.

## Result on queued BTC shadow fills

Markouts below are cents per contract relative to the future midpoint. They
are **not executable exits, settlement P&L, or net trading profits**.

| Subset | Contracts | 5s markout H1 | 5s markout H2 | Pooled 5s | Pooled 60s |
|---|---:|---:|---:|---:|---:|
| Existing base fills | 5,955.35 | +0.0572 | +0.0591 | +0.0583 | +0.0413 |
| Retain model edge >= 0 | 5,210.46 | +0.1301 | +0.1445 | +0.1383 | +0.0770 |
| Exclude model edge < 0 | 744.89 | -0.5293 | -0.4842 | -0.5011 | -0.2086 |
| Book-only anchor control | 5,163.54 | +0.0962 | +0.1288 | +0.1148 | +0.0787 |

48 BTC markets contribute base fills; the overall screen spans 49 markets.
The candidate retains 87.5% of eligible base volume, approximately 434
contracts/hour. The excluded cohort loses at five seconds in both halves.

Market-clustered standard errors:

- Retained 5s markout: **+0.1383 +/- 0.0312 cents** (one SE).
- Retained 60s markout: **+0.0770 +/- 0.0721 cents** (one SE).
- Paired difference from all base fills at 5s: **+0.07997 +/- 0.01476 cents**.
- Increment over book-only at 5s: **+0.02356 +/- 0.01338 cents**.
- Increment over book-only at 60s: **-0.00171 +/- 0.04889 cents**.

Thus the toxicity separation is stronger than the evidence for the incremental
spot model. Most improvement is already present in the book-only control.
Neither the model's incremental benefit nor longer-horizon economics has a
positive conventional 95% lower bound. Adjacent markets may be correlated;
market clustering alone does not address all time dependence.

With 20 ms order latency, the retained/excluded 5s marks are +0.1382/-0.4958
cents. The result is not confined to the fastest assumed order path. Fixed
1 bp and 2 bp adverse spot gates remove only one contract and zero contracts,
respectively, from this eligible base sample. They do not address this cohort.

The stricter 1-cent model threshold retains only 292 contracts and does not
have a statistically established 5s or 60s advantage. Do not select it because
its point estimate looks larger.

## Capacity arithmetic

$10,000 per 30-day month requires $333.33/day, or $13.89/hour at continuous
operation. Required executed contracts/hour at a *net realized* edge:

| Net cents/contract | Required contracts/hour |
|---:|---:|
| 1.00 | 1,389 |
| 0.50 | 2,778 |
| 0.14 | 9,921 |

At the measured +0.1383-cent **5s mark**, the arithmetic would require roughly
10,042 contracts/hour, about 23 times the retained one-contract shadow flow.
Multiplying current flow by that mark gives about $432/month, but even this
is **not a profit forecast**: a midpoint mark cannot simply be cashed out.

All 5,477 retained shadow fill messages can be linked to their contemporaneous
public trade message. Capping those trades' gross quantities at different
clip sizes gives the following deliberately optimistic sensitivities:

| Hypothetical clip | Gross trade quantity available at original fill events | Multiple of original retained fills |
|---:|---:|---:|
| 1 | 5,394.53 | 1.04 |
| 10 | 52,067.57 | 9.99 |
| 25 | 123,408.92 | 23.68 |
| 100 | 405,595.97 | 77.84 |

This suggests gross taker size is sufficient to warrant a **25-contract
queue experiment**. It does not establish accessible capacity. These totals
do not subtract volume consumed ahead of us; larger inventory changes future
orders; added displayed size changes competitors and takers. Do not multiply
the average one-contract markout by these quantities and call it a backtest.

## Execution and recording limitations

Filtering an existing fill ledger does not replay cancellations, lost queue
priority, inventory, pairing or future reposts. A live gate could remove the
bad fills but also remove the good fills that followed them. This is the main
unresolved issue, and the next test must model it jointly.

Both `shadow.rs` and `live.rs` recorded B rows only when the midpoint changed.
They omitted size-only changes and symmetric price changes that preserve the
midpoint. The online shadow engine still received full deltas; the omission
does **not** by itself invalidate its online queue calculations. It does make
recorded B rows inadequate for an independent queue/depth reconstruction,
and the diagnostic's remembered quote can be stale even when its midpoint
is correct.

Local source now records every change to a valid best bid/ask or displayed
size while preserving sparse midpoint history. This is a recording change,
not a new trading strategy. It is **not deployed**. It does not add full-depth
snapshots/deltas or retroactively repair historical data. Future captures need
bounded disk usage, initial snapshots and gap detection before full replay.

Kalshi documents price-time queue priority and a queue-position endpoint:
https://docs.kalshi.com/api-reference/orders/get-order-queue-position
Use actual queue observations in any later live validation.

The July 7, 2026 fee schedule has default maker multiplier zero and separate
exceptions; a later live evaluation must verify the series and actual fill
fees. Passive entry does not make an aggressive exit free:
https://kalshi.com/docs/kalshi-fee-schedule.pdf

## Reproduce and validation

From this directory:

```sh
.venv/bin/python research_spot_residual.py data/box/shadow_spot/tape.csv.gz --out data/research_spot_residual_20260927.json
.venv/bin/python research_spot_residual.py data/box/shadow_spot/tape.csv.gz --delay-us 20000 --out data/research_spot_residual_20260927_slow.json
cargo test --offline
```

Primary output and controls: `data/research_spot_residual_20260927.txt`.
All 16 Rust tests passed. A synthetic diagnostic check also passed: changing
future spot observations leaves past model edges unchanged, volatility has
the required warmup, end-of-tape markouts remain missing rather than being
clipped, and bid/ask markout signs are correct.

No new live strategy or larger live clip is justified by this screen alone.
