# PREREG — sub-cent maker with a causal no-rise filter, fresh 21-day OOS

Frozen 2026-10-06 at 05:02 PDT before reading any post-2026-09-14 trade
tape or settlement score for this arm. Candle files were already downloading
for two earlier preregistered studies; only file counts and downloader progress
had been inspected.

## Hypothesis

The existing 0.9-cent maker edge loses on rare jumps toward the strike. A
strictly causal venue-price filter may reject joins already moving in that
direction without using the next bar or the eventual fill:

- assets: ETH, SOL, XRP, BNB, ZEC, DOGE, HYPE and NEAR;
- complete UTC days 2026-09-15 through 2026-10-05;
- inspect candle bars 12 through 14;
- fold the market onto its currently cheap side;
- at a displayed 0.9-cent cheap-side ask, require the same side to have been
  cheap in each of the prior two bars and require its ask path to be
  non-increasing over those bars:
  `ask[m-2] >= ask[m-1] >= ask[m]`;
- join the first qualifying bar, 100 contracts, one join per market;
- hold credited fills to settlement; maker fee is zero.

This is an entry rule, not a retrospective walk filter. It uses only bars
closed before or at the join decision and never reads bar `m+1`.

## Fill floor and score

Use the same pessimistic historical fill floor as
`PREREG_subcent_sweep_oos_20261006.md`: a same-side taker order that prints
strictly beyond 0.9 cents necessarily clears our added offer, so credit
`min(100, beyond_volume)`. Ignore orders stopping at our level.

Per joined market:

`pnl = credited_fills * (0.009 - cheap_side_settlement)`

The primary statistic is mean dollars per complete UTC day after a further
0.1 cent per credited contract stress. Cluster the bootstrap by UTC day.

Promotion requires:

- all eight assets and all 21 complete UTC days present;
- no capped trade histories;
- at least 500 joined markets and at least eight adverse UTC days;
- positive stressed P&L in both chronological halves;
- positive day-clustered 95% lower bound after stress;
- at least six of eight assets positive after stress;
- no asset above 40% of gross positive unstressed P&L.

Also report retention versus the unfiltered sibling's eligible joins, credited
fills and P&L. This sibling shares the same OOS interval and therefore is not
an independent replication of the unfiltered rule. A pass is research
evidence only and does not authorize an order.
