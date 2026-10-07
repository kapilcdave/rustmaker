# Live ETH book-residual smoke test — 2026-09-27

This is an explicitly authorized, bounded live test of the unchanged book-only
residual rule on `KXETH15M`. BTC results do not establish that the signal transfers
to ETH. The purpose is to measure real fills, queue clearing, paired spread capture,
residual losses, and executable capacity in the less-crowded ETH book.

## Frozen run

- Series: `KXETH15M` only, exchange shard 2.
- Strategy: join the existing touch; retain an unchanged quote only while its price
  remains favorable versus the receipt-clock midpoint observed at least one second
  earlier. Keep the existing momentum and thin-touch toxicity pulls. After an entry,
  quote the opposite side only when the resulting YES+NO pair costs at most $1.00;
  otherwise retain the residual rather than lock a guaranteed spread loss.
- No penny improvement, spot gate, or amend.
- Duration: 30 minutes.
- Clip: 1 contract; absolute position cap: 1 contract per market.
- Venue order-group limit: 2 matched contracts per rolling 15 seconds.
- Stop opening and pull all quotes 120 seconds before close.
- Session marked-loss cap: 100 cents. Cumulative cap from this run directory's
  initial shard equity: 100 cents.
- Output: `data/live-eth-bookres-20260927-A` on the Ohio `us-east-2` VM.

## Decision

Report total and per-pair P&L, residual settlement P&L, contracts per hour, queue
ahead at joins, group trips, undercuts, and ending inventory. Do not increase clip
size unless the one-contract run is positive after settlement and capacity is not
already too low. A short positive run is not an income claim.
