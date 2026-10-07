# Preregistration — external BTC dead-contract fade

Frozen: 2026-10-06 08:18 PDT, before inspecting any local result for this
rule.

External source:

- repository: `mickey1995/kalshi-btc15m-trading-bot`
- public commit: `7a10efdc8d5d9c67219ce9104dbdbee1e0269201`
- commit date: 2026-02-14
- source rule: after minute 10, if the displayed YES price is below 15c,
  buy NO; the configured maximum derived NO price is 92c.

## Frozen local translation

Use every settled `KXBTC15M` market in
`../kalshi-scalp/data/oos_20261006/candles_BTC.json`. This entire September
14–October 6 sample postdates publication of the external rule.

For each market:

1. Sort non-post-close one-minute bars by timestamp.
2. Starting with the bar timestamped at market open + 600 seconds (minute
   10), select the first bar through open + 840 seconds whose YES bid/ask
   close midpoint is at least 8c and strictly below 15c. The source evaluates
   this midpoint, casts it to integer cents, and applies its threshold to that
   value; therefore the local midpoint will likewise be floored to integer
   cents before applying `8 <= price < 15`. The 8c floor reproduces the
   source's configured `100 - yes_price <= 92` check.
3. Buy NO at the same bar's executable close proxy, `1 - YES bid close`.
   Preserve the source's implementation detail that its 92c maximum is checked
   against `100 - midpoint`, not against the actual NO ask.
4. Permit at most one trade per market.
5. Score settlement P&L as NO payout minus entry ask minus the repository's
   standard Kalshi taker-fee function.

The selected same-bar close is an optimistic historical timing proxy, not a
fill claim. Report two additional diagnostics without using them to change
the governing conclusion:

- one-cent adverse execution stress;
- next-minute NO ask close, where available.

## Frozen acceptance gate

The governing same-bar row passes only if all are true:

- at least 100 trades;
- fee-adjusted mean is positive;
- mean remains positive after one-cent stress;
- day-clustered 95% lower bound is positive;
- both chronological halves are positive;
- mean after removing the best 10% of days is positive;
- no day contributes more than 20% of gross positive P&L.

No threshold, minute, side, or timing variant will be substituted after
seeing the result. A failure closes this external hypothesis for this corpus.
