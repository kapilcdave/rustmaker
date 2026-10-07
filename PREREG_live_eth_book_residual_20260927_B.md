# Live ETH book-residual validation B — 2026-09-27

Run A completed normally over two finalized ETH 15-minute markets with 28 fills,
13 paired contracts, +$0.15 paired P&L, +$0.03 net residual P&L, and +$0.18
total settled P&L. One market lost $0.09 and one gained $0.27. This short result
does not establish an income rate or justify larger size.

## Frozen run

- Series: `KXETH15M` only, exchange shard 2.
- Strategy and binary: unchanged `kalshi-mm15-bookres-v4` book-residual rule.
- Duration: 120 minutes.
- Clip: 1 contract; absolute position cap: 1 contract per market.
- Venue order-group limit: 2 matched contracts per rolling 15 seconds.
- Stop opening and pull all quotes 120 seconds before close and before the
  experiment deadline.
- Session marked-loss cap: 100 cents. Cumulative cap from this run directory's
  initial shard equity: 100 cents.
- Output: `data/live-eth-bookres-20260927-B` on the Ohio `us-east-2` VM.
- No penny improvement, spot-adjusted fair value, taker exit, amend, or size
  increase.

## Decision

Stop normally at the deadline or immediately at either loss cap. Verify no
resting orders after exit. Score every finalized market into paired and residual
P&L. Do not increase clip size from this run alone.
