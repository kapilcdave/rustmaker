# PREREG — Oribar market-vs-spot divergence

Frozen before scoring local data.

`oribarlevco-cell/kalshi-btc-15m-bot` introduced the signal in commit
`59581f9ec5fbf7885af93946e674b9baaabb65bb` and added the governing volume
filter in commit `7f00b0a8dd065bf7c6de4d2860d5ba3c08b2820f`, both before September 5.
The exact rule flags the first window snapshot where:

- Kalshi YES bid is above 65c or below 35c;
- completed Coinbase spot is on the opposite side of the opening strike;
- displayed interval volume is at least 10.

The source only scores whether the market's direction eventually wins. This
replication tests economics: follow that market direction at the local actual
side ask, pay the exact one-contract fee, and hold to settlement. Coinbase's
completed minute ending at the Kalshi candle timestamp is used, never the
still-forming bar. One trade per window.

Pass requires at least 100 trades, positive day-clustered 95% lower bound,
positive chronological halves, positive after an extra 1c execution stress,
positive after deleting the best 10% of days, and no day above 20% of gross
positive P&L. This is paper evidence only.
