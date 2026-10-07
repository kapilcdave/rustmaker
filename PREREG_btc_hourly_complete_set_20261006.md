# PREREG — BTC hourly directional/range complete-set arbitrage

Frozen 2026-10-06 before downloading or scoring any `KXBTC` range-bucket
candles for this experiment.

## Identity

For adjacent `KXBTCD` thresholds `L < U`, select the `KXBTC` bucket `C`
covering the displayed settlement values strictly above `L` through `U`.
All three contracts use the same 60-second BRTI average and close time.

The events:

- lower-threshold NO: `S <= L`;
- bucket YES: `L < S <= U`;
- upper-threshold YES: `S > U`

are mutually exclusive and exhaustive.  Buying all three pays exactly $1.
The complementary portfolio — lower YES, bucket NO, upper NO — pays exactly
$2.

## Frozen screen

Use the same on-the-hour BTC population and final 13 one-minute bar closes as
`PREREG_btc_hourly_dominance_20261006.md`, including its mechanically selected
ten-day pre-gap extension.

For each hour, use exactly the closest `KXBTCD` threshold strictly below the
contemporaneous `KXBTC15M` strike and the closest threshold greater than or
equal to it.  This mechanically fixes one adjacent pair per hour before any
range-bucket candles are downloaded; do not scan the rest of the ladder.

At each common timestamp:

1. price both three-leg portfolios at displayed asks;
2. round the standard taker fee up separately on all three legs;
3. require at least 2 cents guaranteed edge after fees;
4. take only the first qualifying portfolio per hour, choosing the larger
   edge if both qualify simultaneously.

Report 0c, 0.5c-per-leg and 1c-per-leg additional stress.  Candle closes are a
non-atomic upper bound and do not prove a fill.

## Integrity and promotion

Void the hour unless:

- all three rules use the same BRTI settlement window;
- all published expiration values match exactly;
- the bucket boundaries have no displayed gap or overlap with `L` and `U`;
- settled outcomes select exactly one member of the first partition.

Promotion requires at least 25 opportunities, 30 UTC days, positive mean and
day-clustered 95% lower bound after 1c per leg stress, positive chronological
halves, positive mean after removing the best 10% of days, and no day above
20% of gross positive edge.

No result authorizes live orders.  A pass only permits a prospective
depth-aware collector.
