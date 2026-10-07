# BTC hourly three-leg complete set — valid identity, insufficient edge

Frozen in `PREREG_btc_hourly_complete_set_20261006.md`. Public historical
data only; no order route was called.

The portfolio identity passed in all 757 usable hours:

- lower-threshold NO + matching range-bucket YES + upper-threshold YES pays
  exactly $1;
- the complementary portfolio pays exactly $2;
- all legs matched BRTI, expiration value and boundaries;
- zero settlement partition violations over 34 UTC days.

The books nevertheless left only **5** candle-close opportunities at the
frozen 2-cent threshold:

- maximum post-fee edge 8.0 cents;
- mean selected edge 4.2 cents;
- mean after 1 cent per leg stress 1.2 cents;
- day-clustered stressed interval **[-0.8c, +3.4c]**;
- historical promotion gate: **FAIL** (25 opportunities required and lower
  interval bound must exceed zero).

## Verdict

**CLOSED.** The three-contract partition is mechanically sound, but the five
non-atomic candle coincidences are too rare and their stressed interval crosses
zero. They are not a trading strategy.

Canonical report: `idea_lab/btc_hourly_complete_set_report.json`.
