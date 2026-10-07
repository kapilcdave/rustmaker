# Prospective direct-book preregistration: causal BTC/ETH regime rule

Created after identifying the source backtest's one-minute timestamp leak and
before collecting any direct-book decisions for this rule.

For each new KXBTC15M window through 10:00 PDT:

1. At `t0+6m+2s`, fetch completed Coinbase candles through the candle opening
   at `t0+5m`. Its close became observable at `t0+6m`.
2. Recompute the frozen source minute-5 BTC direction, ETH direction,
   pre-window volatility, and volatility state without changing its thresholds
   or nine-state action map.
3. Fetch the public Kalshi depth-one book after both spot requests finish.
4. If the frozen map signals, record the executable side ask and sizes. Do not
   submit an order.
5. Capture the same book again at +5s and +15s from the causal decision boundary
   for execution-latency diagnostics.
6. After settlement, score one contract at each recorded ask with the local
   conservative whole-cent taker fee.

This tiny forward journal is descriptive only and cannot promote a strategy.

