# BTC range/directional duplicate upper tails — exact aliases, no arbitrage

Frozen in `PREREG_btc_hourly_tail_duplicate_20261006.md`.

Instrument identity was exact:

- 757 paired hours over 34 UTC days;
- identical ticker suffix, strike, rule and settlement value;
- zero identity or settlement-result violations.

Where both historical candle series exposed a common executable bar, the
post-fee two-leg edge was **-2.0 cents in every one of 56 hours**. There were
zero positive hours and zero opportunities at the frozen 2-cent threshold.

The prospective multi-asset direct-book sidecar independently showed the two
aliases carrying identical top-of-book states in most paired snapshots. The
books did not expose both opposite asks needed to buy a complete set.

## Verdict

**CLOSED.** These contracts are aliases in economics and, empirically, in
their displayed liquidity. Duplicate labels do not create independent books
to arbitrage.

Canonical report: `idea_lab/btc_hourly_tail_duplicate_report.json`.
