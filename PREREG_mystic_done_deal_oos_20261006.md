# PREREG — external Mystic 93–95c done-deal OOS replication

Frozen Tuesday, October 6, 2026 at 08:00:43 PDT, before scoring local data.

The external corrected report was dated August 18, 2026 and selected:

- BTC `KXBTC15M`;
- 1.75–5.0 minutes remaining;
- favorite ask 93–95c;
- BTC distance from window open at least 0.10% above 3.5 minutes remaining,
  otherwise 0.06%;
- aligned three-completed-minute momentum;
- no large opposing wick in the latest two completed minutes;
- 1c adverse entry slip plus Kalshi taker fee.

Its published May 17–August 15 data are in-sample and the report explicitly
acknowledges selection and optimistic fills. This test uses only the local
September 13–October 5 BTC download, entirely after the report date.

## Frozen minute-data replication

At 300, 240, 180 and 120 seconds before close, in that order:

1. choose the favorite side and require its historical Kalshi ask in
   `[0.93,0.95]`;
2. use the same-timestamp completed Coinbase minute as a Binance proxy;
3. measure signed move from the Coinbase minute open at the start of the
   15-minute window and apply the report's 0.10%/0.06% distance rule;
4. require the net move across the latest three completed Coinbase candles to
   align with the favorite;
5. reject an opposing wick in either latest two candles when it exceeds 2.5
   times the candle body and 0.015% of current spot;
6. enter once per market at `ask+1c`, charge the taker fee and hold to
   settlement.

The external strategy's intraminute stop cannot be reconstructed from
one-minute Kalshi candles, so this is a hold-to-settlement replication. The
external report had only one stop in its selected 783-trade row; nevertheless,
this mismatch is reported and bars direct performance equivalence.

Same-timestamp Coinbase and Kalshi candles are non-atomic, and Coinbase is not
Binance or CF Benchmarks. Report trade count, losses, fee-adjusted mean,
UTC-day-clustered interval, chronological halves, best-10%-day deletion and an
extra one-cent stress beyond the already charged one-cent slip.

The local OOS gate requires at least 100 trades, positive stressed mean and
clustered lower bound, positive halves and best-day deletion, and no day above
20% of gross positive P&L. A pass can authorize only a direct-book prospective
collector, not live trading.
