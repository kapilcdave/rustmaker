# PREREG — BTC hourly upper-tail duplicate contracts

Frozen 2026-10-06 before downloading or scoring paired upper-tail candles.
One current settled market was inspected only to verify the identity and API
schema.

## Identity

Each hourly `KXBTC` range event has one upper-tail contract.  Its ticker,
strike, close time, settlement value and primary rule are also published in
the `KXBTCD` directional ladder.  When the primary rule strings are exactly
equal, the two contracts are the same binary event.

Therefore either cross-series portfolio:

- `KXBTC` YES plus `KXBTCD` NO; or
- `KXBTC` NO plus `KXBTCD` YES

pays exactly $1 at settlement.

The lower tails are excluded.  Their displayed cutoffs differ by one cent
and no assumption about unpublished settlement precision is allowed.

## Frozen historical screen

Use all settled on-the-hour BTC events in the completed
`idea_lab/btc_hourly_dominance_panel.json`, including its mechanically chosen
ten-day pre-gap extension.

For each hour:

1. select the sole `KXBTC` contract having a floor strike and no cap strike;
2. pair it only with the `KXBTCD` contract having the exact same ticker suffix;
3. require exact equality of primary rules, close times, expiration values
   and settled outcomes;
4. at every common candle close from 13 through 1 minutes before settlement,
   price both two-leg portfolios at displayed asks;
5. round the standard taker fee up separately on both legs;
6. require at least 2 cents guaranteed edge after fees;
7. take only the first qualifying portfolio per hour, choosing the larger
   edge if both qualify simultaneously.

Report 0c, 0.5c-per-leg and 1c-per-leg additional stress.  Candle closes are
a non-atomic execution upper bound and do not prove that both quotes
coexisted or had executable size.

## Promotion gate

Promotion requires at least 25 opportunities across at least 30 UTC days,
positive mean and day-clustered 95% lower bound after 1c per leg stress,
positive chronological halves, positive mean after removing the best 10% of
days, no day above 20% of gross positive edge, and zero identity or settlement
violations.

No result authorizes live orders.  A pass only permits a prospective
depth-aware collector.
