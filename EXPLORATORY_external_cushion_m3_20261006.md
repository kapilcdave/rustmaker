# External BTC minute-3 cushion gate transfer

Status: **CLOSED / OVERLAPPING-HISTORY DIAGNOSTIC**

`darup67/kalshi-btc-agent` commit
`a3f1ebaad385dec9a0595ad558649fa199a20256` reports an unregistered positive
minute-3 cell for a 1.04-sigma BTC spot cushion. I transferred that fixed
threshold to the local September 13–October 5 panel, using the actual
minute-3 Kalshi ask and the last fully completed Coinbase bar. This avoids the
one-minute future-candle error.

## Result

- 183 calls across 22 days;
- **+3.338c/contract** after the exact taker fee;
- day-clustered lower 95% bound **-0.262c**;
- halves **+4.877c / +1.815c**;
- YES +4.571c/93 and NO +2.063c/90;
- best-10%-day deletion +1.229c;
- one-minute delayed execution +3.292c.

The result fails promotion standards:

- the source selected minute 3 after inspecting its own table;
- the local panel substantially overlaps the source's September–October
  history and therefore is not independent replication;
- the unstressed clustered interval already crosses zero, and adding 1c
  adverse execution stress moves its lower bound to **-1.262c**;
- the strictly post-source-report slice has only 14 calls over two days,
  **-1.643c** mean, lower bound -16.238c, and halves
  -15.743c/+12.457c.

The full-period point estimate is attractive but fails uncertainty, stress,
independence, and fresh-period checks. The rule is closed on this evidence. Do
not tune its minute, z-threshold, side, or volatility window on this panel.
