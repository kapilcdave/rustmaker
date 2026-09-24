# Pre-registration: BTC-focused gated maker (frozen 2026-09-24, before any BTC-only data)

## Why BTC, decided without P&L
- Flow: KXBTC15M is 85.6% of contracts traded on the 9-series 15M crypto tape (3 h, 556k prints).
- Depth: its touch queue is 1,000-8,000 ct (live join logs), so cancels ahead are plentiful —
  the mechanism that gave 41% of real queue clearance.
- Budget: at 9 series the Advanced write budget throttled the gated quoter ~34k times in 3 h.
  One series spends the whole 300 tokens/s on the deepest book.
- NOT a reason: any per-series P&L. BTC's gated P&L in v3 (−0.04 c/ct) and BNB's loss were seen
  AFTER the fact and are explicitly excluded from this decision.

## Frozen rule (identical to shadow v4 `base`)
- KXBTC15M only; 1 ct per side; |position| ≤ 1; mid band 15-85 c; no posts < 120 s to close.
- Join the touch; pull a side if 1 s mid momentum ≥ 0.25 c runs into it, or it holds < 7.87% of
  touch size (imb > 0.9213). Closing side quotes like any side (v4 showed ungated pairing loses).
- Latencies: create 5.44 ms, cancel 4.43 ms (measured). Queue: pro-rata cancel credit.

## Window and metric
- Fresh shadow window starting after this file is written; 48 settled BTC markets (12 h).
- Primary: settlement c/market for the frozen rule, SE clustered by market.
- Secondary (diagnostic, no decision weight): throttled count; mk60s; paired vs unpaired.

## Decision
- Lower 95% bound (mean − 1.96·SE) > 0  → candidate for a capped live run (needs user approval).
- Mean > 0 but bound ≤ 0                → inconclusive; extend once by 48 markets, same rule.
- Mean ≤ 0                              → BTC-focus closed; do not re-tune on this window.
