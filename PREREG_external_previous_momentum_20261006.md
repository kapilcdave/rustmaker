# PREREG — public PREVIOUS/MOMENTUM/CONSENSUS bot replication

Frozen Tuesday, October 6, 2026 at 08:14 PDT, before scoring.

Source: `DeweyMarco/simple-kalshi-bot`, commit
`f01baea7cf185186e46ce701904d60357ad779ba`. Its README defines three
`KXBTC15M` paper strategies:

1. `PREVIOUS`: buy the same side as the immediately preceding settled window;
2. `MOMENTUM`: buy the side of BTC's preceding 60-second move;
3. `CONSENSUS`: trade only when those two sides agree.

Use every adjacent settled local BTC window from September 13–October 5, long
after the source commit. The decision uses only the Coinbase candle completed
at the new window's open and the one before it. Enter at the first Kalshi
one-minute candle's same-side ask close, charge the taker fee, and hold to
settlement. This one-minute delay is a conservative proxy for the source bot
waiting for the previous market to report settlement.

Report each strategy's trades, fee-adjusted mean, day-clustered lower bound,
halves, best-10%-day deletion and extra-1c stress. There is no threshold search.
Promotion requires at least 100 trades, positive stress and clustered lower
bound, positive halves and trimmed mean, and no day above 20% of gross positive
P&L. A pass would permit only a prospective direct-book journal.
