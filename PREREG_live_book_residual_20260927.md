# Live BTC book-residual smoke test — 2026-09-27

This is an explicitly authorized, bounded live test of the book-only control from
`PREREG_residual_queue_capacity.md`. It is not a test of the unproven spot-adjusted
model and cannot establish scalable income.

## Frozen run

- Series: `KXBTC15M` only, exchange shard 2.
- Strategy: join the existing touch; retain an unchanged quote only while its price
  remains favorable versus the receipt-clock midpoint observed at least one second
  earlier. Keep the existing momentum and thin-touch toxicity pulls.
- No penny improvement, spot gate, or amend.
- Duration: 30 minutes.
- Clip: 1 contract; absolute position cap: 1 contract per market.
- Venue order-group limit: 2 matched contracts per rolling 15 seconds.
- Stop opening and pull all quotes 120 seconds before close.
- Session marked-loss cap: 100 cents. Cumulative cap from this run directory's
  initial shard equity: 100 cents.
- Output: `data/live-bookres-20260927-A` on the Ohio `us-east-2` VM.

## Evaluation

Use venue fills, resting-order history, and settled positions. Report fills,
contracts, pairing, residual settlement P&L, order-group trips, loss-cap status,
and whether real queue clearing resembles the shadow assumptions. A short positive
run is operational evidence only; it is not a profitability or scale verdict.
