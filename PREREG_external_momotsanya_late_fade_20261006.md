# PREREG — external final-two-minute near-strike late fade

Frozen Tuesday, October 6, 2026 at 09:37 PDT, before querying local outcomes
for this rule.

Source: public `momotsanya/KalshiBot` commit
`8c867ef05c9a1963eba227605e7246927a157cc5`. Its shipped dry-run configuration
uses `late_fade`, minutes 13–15, spot/strike distance at most 0.02%, contract
price 15–50c and a 60-second adverse-momentum tolerance of 0.03%.

The source combines this entry with loss-recovery sizing. Sizing cannot change
per-contract expectancy, so this test scores one contract per market and does
not simulate martingale or recovery.

## Frozen causal minute proxy

BTC only. Evaluate the Kalshi quote at 120 and 60 seconds before settlement,
in that order:

1. use the Coinbase minute close stamped **60 seconds before** the Kalshi
   candle timestamp, so the spot input is complete before the quote;
2. require absolute spot/strike distance in `(0, 0.02%]`;
3. if spot is above strike, buy NO; if below strike, buy YES;
4. require the chosen side's ask in `[0.15, 0.50]`;
5. calculate the most recent fully known 60-second spot return; for a NO order
   reject momentum above `+0.03%`, and for YES reject momentum below `-0.03%`;
6. take only the first qualifying row, charge the standard taker fee and hold
   to settlement.

Score separately on the untouched September 13–October 5 panel and the earlier
July 2–September 8 archive. Promotion requires at least 100 trades in each
period, positive means after an extra 1c execution stress, positive
UTC-day-clustered lower bounds, positive chronological halves, positive means
after removing the best 10% of days, and no day above 20% of gross positive
P&L. Failure of either period closes the rule.
