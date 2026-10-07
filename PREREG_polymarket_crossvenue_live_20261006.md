# PREREG — prospective Kalshi/Polymarket BTC 15-minute paired books

Frozen Tuesday, October 6, 2026 at 07:56:37 PDT. Only snapshots with
`wall_ns >= 1791298597036698000` are eligible.

This is read-only prospective paper. No order will be placed on either venue.

For matching BTC 15-minute windows, poll the direct top books for
`KXBTC15M` and the Polymarket `btc-updown-15m-{open_unix}` UP and DOWN tokens.
The instruments use identical quarter-hour windows and 60-second TWAP
direction logic but different source indexes: CF Benchmarks BRTI on Kalshi
and Chainlink BTC/USD on Polymarket. Therefore the pair is not risk-free even
though a fixed historical sample had 175/175 matching directions.

At each snapshot calculate:

- Kalshi YES ask + Polymarket DOWN ask;
- Kalshi NO ask + Polymarket UP ask.

Derive Kalshi asks from the opposite best bid. Use the minimum displayed
Polymarket ask. Charge the standard Kalshi one-contract taker fee and a
conservative fixed 1c Polymarket fee allowance. Require the three requests to
finish within 1,000ms, at least one Kalshi contract and at least five
Polymarket shares displayed.

Record a signal only when total cost after those fees is at most 97c. Take
the first qualifying direction per window, choosing lower cost on a tie.
Report raw locked spread assuming matched outcomes and an additional 1c per
leg execution stress. Fetch both venue outcomes after settlement and report
any source-index mismatch explicitly.

The short screen requires at least five settled paired signals, no outcome
mismatch, positive mean after the extra 1c/leg stress and positive
chronological halves. Passing authorizes only a longer atomicity and
cross-index study, not trading.
