# PREREG — prospective direct-book Mystic done-deal rule

Frozen Tuesday, October 6, 2026 at 08:01:15 PDT. Only decisions with
`wall_ns >= 1791298875631679000` are eligible.

The post-August historical test of the external 93–95c rule produced 159
trades, three losses, +1.978c after a charged 1c slip and taker fee, positive
halves, but a clustered lower 95% bound of -0.578c. It therefore failed its
paper gate. This read-only prospective capture is retained to measure direct
books and operational frequency, not to rescue that failed interval.

For each new BTC 15-minute market, evaluate 300, 240, 180 and 120 seconds
before close, 2–15 seconds after the minute boundary. Apply the exact frozen
distance, three-minute momentum, opposing-wick and 93–95c favorite-ask rules
from `PREREG_mystic_done_deal_oos_20261006.md` using completed Coinbase
candles and the direct Kalshi depth-one orderbook. Record the first signal per
market, displayed size, request timing, taker fee and settlement. Do not add
the historical 1c slip to the observed direct ask; report it as a separate
stress.

The short screen requires at least 10 settled signals, 95% complete decisions,
positive fee-adjusted and 1c-stressed mean, positive chronological halves, and
at least one loss or 100 settled signals. It cannot authorize live trading.
