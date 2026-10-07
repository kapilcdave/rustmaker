# Preregistration — full-population calibration transfer

Frozen: 2026-10-06 before fitting or scoring this rule.

## Hypothesis

The public 6,257-window BTC panel reports small horizon-specific calibration
departures. Test whether a calibration map learned entirely before the local
sample can convert those departures into executable value, rather than merely
better probability scores.

## Train

Source:

- repository `theruviparambil/kalshi-btc-15m`
- commit `ed3230f21b0cb2b66f5b870e94695c1fd1e3bdc2`
- June 27–September 3, 2026

For each horizon in exactly `(10, 5, 3, 2, 1)` minutes:

1. keep one row per market with midpoint strictly between 5c and 95c;
2. fit `logit(P(YES)) = intercept + slope * logit(mid)` by maximum
   likelihood;
3. do not tune, regularize, bucket, side-split or hour-split the map.

## Test

Use only the local September 14–October 6 BTC candle panel. At horizons
10, 5, 3, 2 and 1 minutes, in that chronological order:

1. map the midpoint through the frozen horizon model;
2. compute expected P&L for buying YES and NO at their actual asks after the
   local conservative one-contract taker fee;
3. if the better side has modeled net edge at least 2c, buy it;
4. enter at the first qualifying horizon and at most once per market;
5. hold to settlement.

No spot, momentum, volatility, direction, hour or additional calibration
filter may be added.

## Gate

Require:

- at least 100 local trades;
- positive realized mean after an extra 1c stress;
- positive day-clustered 95% lower bound;
- both chronological halves positive;
- positive mean after deleting the best 10% of days;
- no day above 20% of gross positive P&L.

Failure closes calibration correction as an executable directional strategy.

