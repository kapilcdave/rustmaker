# BTC 15-minute versus hourly dominance — structurally valid, economically closed

Frozen in `PREREG_btc_hourly_dominance_20261006.md`. Public/read-only
collection only; no order route was called.

## Historical result

The integrity premise passed:

- 765 on-the-hour BTC 15-minute windows over 34 UTC days;
- 757 had both adjacent hourly thresholds;
- all 757 matched the expiration value and BRTI rule;
- 1,514 settlement dominance checks, zero violations.

The trade did not pass. Using one-minute candle-close asks/bids, venue taker
fees and the frozen 2-cent entry threshold:

- only **3** cross-instrument opportunities in 757 eligible hours;
- best edge 4.0 cents, mean selected edge 3.67 cents;
- largest day supplied 72.7% of gross positive edge;
- historical promotion gate: **FAIL** (25 opportunities required).

The hourly ladder's internal monotonicity pair was even cleaner to reject:
zero positive hours, zero 2-cent opportunities and a best observed edge of
**-2.0 cents**.

## Prospective direct-book check

The contemporaneous market-summary fields were invalidated because they
lagged the direct `/orderbook` endpoint by several ticks. Amendment 5 therefore
made the direct endpoint governing. The final prospective journal is scored
separately at the requested 10:00 PDT stop; interim direct snapshots contained
no 2-cent signal.

## Verdict

**CLOSED.** The logical dominance relation is real, but the books price it.
Three historical candle coincidences are too sparse, non-atomic and
concentrated to support a bot. The internal hourly pair never crossed zero.

Canonical report: `idea_lab/btc_hourly_dominance_report.json`.
