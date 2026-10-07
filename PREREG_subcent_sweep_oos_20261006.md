# PREREG — sub-cent sweep-backed maker, fresh 21-day OOS

Frozen 2026-10-06 before downloading any post-2026-09-14 trade tape for this
experiment.  The candle files are being downloaded for a different frozen
spot-model test and have not been scored for this arm.

## Frozen trade

Apply the existing `FINDINGS_subcent_ladder_15m.md` construction without
retuning:

- assets: ETH, SOL, XRP, BNB, ZEC, DOGE, HYPE and NEAR;
- complete UTC days 2026-09-15 through 2026-10-05;
- during candle bars 12 through 14, join the first displayed 0.9-cent offer
  on whichever outcome is then the cheap side;
- size 100 contracts, one join per market;
- hold any fills to settlement;
- maker fee zero.

The historical tape supplies a pessimistic fill floor.  A same-side taker
order that prints strictly beyond 0.9 cents necessarily clears an added
0.9-cent offer first, so credit `min(100, beyond_volume)` contracts.  Ignore
orders that stop at the joined level.  Group prints into taker orders by exact
`(created_time, taker_side)` as in the frozen predecessor.

## Scoring

For each joined market:

`pnl = credited_fills * (0.009 - cheap_side_settlement)`

The primary statistic is mean dollars per complete UTC day after subtracting
an additional 0.1 cent per credited contract for impact/competition.  Cluster
the bootstrap by UTC day so simultaneous cross-asset tail losses remain
together.

Promotion requires:

- all eight assets and all 21 complete days present;
- no capped trade histories;
- at least 1,000 joined markets and at least 10 adverse UTC days;
- positive point P&L in both chronological halves after 0.1-cent stress;
- a positive day-clustered 95% lower bound after stress;
- at least six of eight assets positive after stress;
- no asset contributes over 40% of gross positive unstressed P&L.

Also report zero-stress economics, credited fill rate, adverse windows and the
largest losing day.  Historical counterfactual fills cannot measure impact,
queue changes caused by our order, or future competition.  A pass is research
evidence only and does not authorize an order.
