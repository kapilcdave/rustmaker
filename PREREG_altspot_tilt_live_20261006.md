# PREREG — direct-book prospective check of the thin-altcoin spot gap

Frozen 2026-10-06 at 05:25 PDT after the historical OOS primary population
was scored, but before this prospective journal was started. The trade rule is
copied without modification from `PREREG_altspot_tilt_20261006.md`.

- Assets: NEAR, ZEC and HYPE.
- Fair value:
  `Phi(log(S/K') / (sigma_1m * sqrt(15-k)))`.
- `K' = strike * exp(-basis_bps/10000)`, with frozen bps
  NEAR 4.67, ZEC 1.87, HYPE 1.77.
- `sigma_1m`: standard deviation of the prior 120 Coinbase one-minute close
  returns.
- Decision minutes, in order: `k = 2, 3, 5, 8`.
- One first trigger per market: buy YES when `fair - ask > 0.10`; buy NO when
  `bid - fair > 0.10`.
- Use the public direct depth-one orderbook, not market-summary prices.
- Charge the standard one-contract taker fee.

Coinbase's completed minute candle and Kalshi's orderbook are separate public
REST requests, so this is non-atomic prospective paper evidence. Record request
times, displayed size, all no-trade decisions and the first signal. Score
settled signals at the requested stop.

This check passes operationally if at least 95% of scheduled decisions have a
complete spot history and a direct two-sided book. It passes the paper-economics
screen only with at least 20 settled signals, positive mean after fees, positive
means in both chronological halves and no asset above 60% of gross positive
P&L. It cannot authorize an order because it does not measure slippage or fills.

## Implementation correction before the clean journal

The first three smoke-test rows were started mid-window and paired historical
minute-2 spot candles with a much later book. They are preserved in
`idea_lab/altspot_tilt_live_invalid_late_start_20261006.jsonl.gz` and are void.
Before starting the clean journal, the collector was changed to accept a
scheduled decision only 2–15 seconds after its minute boundary and to record
older decisions as missed. This is an integrity correction, not a strategy
change.
