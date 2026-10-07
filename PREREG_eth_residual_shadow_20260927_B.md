# ETH residual-capacity shadow B — 2026-09-27

This prospective run follows the frozen `capacity-eth-20260927-A` shadow. That
run exposed an implementation defect before any residual arm posted: Coinbase
ETH ticker messages can be more than two seconds apart, while the one-second
variance grid required every carried observation to be at most two seconds old.
Consequently all 61 grid points were almost never simultaneously available.

## Frozen correction

- Carry the last received Coinbase midpoint forward for at most 20 seconds when
  sampling the one-second variance grid. A gap longer than 20 seconds still
  fails closed and removes residual-arm quotes.
- Keep the existing 61-second warmup, causal receipt-clock sampling, formula,
  quote-value threshold, queue model, latency, close cutoff, and all fourteen
  arms unchanged.
- Poll initialized markets as well as open markets and pre-subscribe only when
  their scheduled open is within 60 seconds. This corrects A's late-discovery
  exclusions while retaining the existing snapshot-within-20-seconds validity
  rule; it does not create simulated quotes before a valid two-sided book exists.
- Do not use this correction in the live market maker. This is shadow research
  only and submits no orders.

## Run

- Series: `KXETH15M` only on the existing Ohio VM.
- Duration: 180 minutes.
- Output: `/home/admin/trading/kalshi-mm15/capacity-eth-20260927-B`.
- Archive only closed, hash-verified audit chunks. Preserve partial files on the
  VM until the recorder closes them.
- Stop on venue gaps, spot disconnect/overflow, audit failure, the 1 GiB spool
  limit, or the 512 MiB free-space guard.

## Decision

Score settlement P&L, paired P&L, residual P&L, contracts, inventory, and both
queue assumptions on complete market panels. Report zero-fill arms and excluded
windows. Do not deploy the spot-adjusted rule or increase live size based on this
short run; it is an implementation validation and capacity screen.
