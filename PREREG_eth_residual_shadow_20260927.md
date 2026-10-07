# ETH residual queue/capacity shadow — 2026-09-27

Run the frozen 14-arm residual ladder from `PREREG_residual_queue_capacity.md`
unchanged on `KXETH15M` for four hours. This is a new-asset transfer test, not a
continuation of the BTC statistical claim.

- Arms: base 1/25, book-only 25, and spot-adjusted 1/10/25/100, each under pro-rata
  and trade-only queue advancement.
- Duration: 240 minutes; all zero-fill windows count.
- Complete-window, feed-gap, queue, position-cap, and audit-reconciliation rules are
  unchanged.
- Output: `capacity-eth-20260927-A` on the Ohio `us-east-2` VM.
- No orders are sent.

Do not increase live ETH size from this run unless settlement P&L is positive in
both chronological halves and larger clips increase total P&L under both queue
assumptions. Compare contracts/hour with BTC; lower competition is not useful if
ETH flow is too small to scale.
