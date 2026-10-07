# PREREG — Coin Race leader/directional logical implication

Frozen 2026-10-06 at 07:08:03 PDT, before any historical or prospective price
sum for this construction was computed.

For the same 15-minute window, let `L_i` be asset `i` having the highest return
among BTC/ETH/SOL/XRP/HYPE, and let `U_i` be asset `i` ending above its own
starting value in the corresponding KX*15M directional market.

For every ordered pair `i != j`:

`L_i AND U_j => U_i`.

If asset `j` has a positive return and `i` has the highest return, then `i` also
has a positive return. Fractional leader payouts on ties do not break the
payoff inequality. Therefore the portfolio

`NO(L_i) + NO(U_j) + YES(U_i)`

has contractual payout of at least $1 in every state.

## Frozen test

- Assets: BTC, ETH, SOL, XRP and HYPE.
- Require identical open and close timestamps across the leader event and all
  five directional markets.
- For each ordered pair, buy the three legs at displayed asks and charge the
  standard taker fee on each leg.
- Signal only when total cost plus fees is strictly below $1.
- Report one-cent-per-leg stress and minimum displayed size.

Historical development joins the already preregistered eight-event-per-day
Coin Race sample to the untouched common-timestamp directional candles for
2026-09-14 through 2026-10-05. Candle closes are non-atomic.

Prospective direct-book evidence begins with the first event opening after Unix
second `1791295683`, records all ten direct orderbooks with request timestamps,
and is also non-atomic.

The direct-book screen passes only if a signal remains positive after one cent
per leg and every leg displays at least one contract. A pass permits only an
atomic-execution engineering study, never live orders.
