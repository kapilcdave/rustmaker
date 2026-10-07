# Preregistration — remaining mickey1995 BTC rules

Frozen: 2026-10-06 08:25 PDT, before inspecting local results for these two
rules.

Source: `mickey1995/kalshi-btc15m-trading-bot`, public commit
`7a10efdc8d5d9c67219ce9104dbdbee1e0269201` dated 2026-02-14. The separately
preregistered dead-contract rule is not part of this test.

Use all settled markets in
`../kalshi-scalp/data/oos_20261006/candles_BTC.json`, which postdate the public
commit. Convert every candle-close YES bid/ask midpoint to integer cents using
the source's `int()` behavior. Score one contract at the executable side ask
close and subtract the standard Kalshi taker fee.

## Rule A — ride the favorite

- Examine minute-close bars 1, 2 and 3 in chronological order.
- Enter at the first bar with integer YES midpoint from 60c through 80c,
  inclusive.
- Buy YES at its ask close.
- At most one entry per market.

## Rule B — mid-window momentum

- Treat the first available minute-close midpoint as the initial YES price,
  matching the first observable state available in this historical dataset.
- Examine minute-close bars 5, 6 and 7 in chronological order.
- Require an absolute integer-midpoint change of at least 10c.
- If the change is positive, buy YES only when its midpoint is 55–75c.
- If the change is negative, buy NO only when `100 - YES midpoint` is 55–75c.
- Enter at that side's ask close, at most once per market.

For each rule, also report one-cent adverse stress and next-minute execution
as diagnostics.

## Acceptance gate

Each governing same-bar rule passes only with at least 100 trades, positive
fee-adjusted mean, positive mean after one-cent stress, positive
day-clustered 95% lower bound, both halves positive, positive mean after
deleting the best 10% of days, and no day above 20% of gross positive P&L.

No parameter or timing variant will replace a failed frozen row.
