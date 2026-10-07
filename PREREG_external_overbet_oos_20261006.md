# Preregistration: external 5-minute favorite-overbet rule

Frozen before opening the local post-publication outcomes.

## Source

- Repository: `mlysophia/kxbtc15m-overbet-study`
- Frozen commit: `a904abe18bba500c9ac5d04fd43dfd61b18c6485`
- Commit timestamp: 2026-08-30
- Source headline: threshold 0.80, five-minute observation window.

## Untouched evaluation sample

`/Users/kapil/proj/kalshi-scalp/data/oos_20261006/candles_BTC.json`,
2026-09-14 onward. The entire local sample starts after the source commit.

The source raw trade archive is unavailable. The local archive contains
one-minute Kalshi bid/ask OHLC snapshots, so this is a frozen candle-close
replication rather than a claim of byte-for-byte reproduction.

## Frozen rule

For each settled KXBTC15M market:

1. Use the five non-post-close rows at minutes 1 through 5.
2. Compute each row's YES midpoint as `(bid + ask) / 2`.
3. A row is extreme when `max(YES midpoint, 1 - YES midpoint) > 0.80`.
4. Trigger only when at least three of the five rows are extreme. This is the
   one-minute-close proxy for strictly more than half of the five-minute
   observation window.
5. At minute 5, select the side with the greater midpoint. A 50/50 tie selects
   YES, matching the source convention.
6. Governing execution uses the executable close snapshot at minute 5: YES ask
   for YES and `1 - YES bid` for NO.
7. Hold one contract to settlement and subtract the existing conservative
   whole-cent taker-fee function used by the local battery.

Markets missing any of minutes 1 through 5 are excluded.

## Diagnostics fixed in advance

- Repeat execution at minute 6, without changing the minute-5 signal or side.
- Apply an additional 1c adverse execution stress.
- Report day-clustered 95% lower bound, chronological halves, result after
  deleting the best 10% of days, and concentration in the best day.
- Report YES and NO sides separately for diagnosis only. No side may be mined
  or promoted from this holdout.

## Promotion gate

The governing rule passes only with at least 100 trades, positive mean after an
extra 1c stress, positive day-clustered 95% lower bound, positive chronological
halves, positive mean after deleting the best 10% of days, and no day supplying
more than 20% of gross positive PnL.

