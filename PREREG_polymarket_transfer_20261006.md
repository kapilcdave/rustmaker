# PREREG — Polymarket-to-Kalshi BTC 15-minute transfer

Frozen Tuesday, October 6, 2026 at 07:55:42 PDT, before downloading or joining
historical Polymarket price histories.

The outside hypothesis comes from a public open-source bot survey: use the
parallel Polymarket BTC 15-minute market as an independent price-discovery
signal for `KXBTC15M`. The contracts are **not duplicates**. Kalshi settles
against CF Benchmarks BRTI; Polymarket settles against a Chainlink BTC/USD
60-second TWAP. This screen is directional transfer, not risk-free arbitrage.

## Frozen historical sample

Use exactly the 176 quarter-hour opens already selected by the seeded,
day-stratified `KXCRYPTOLEAD15M` sample. Match each open to the local
`KXBTC15M` historical candle panel and to Polymarket slug
`btc-updown-15m-{open_unix}`. This sample selection predates this hypothesis.

For each matched event:

1. Verify and report whether the two venues' settled directions agree.
2. At decision minutes `k={2,3,5,8}`, take the latest Polymarket UP
   price-history point at or before the Kalshi candle timestamp, rejecting it
   if more than 90 seconds old.
3. Use the Kalshi historical YES ask and NO ask (`1-YES_bid`) from that
   same-minute candle.
4. Buy YES if `poly_up - yes_ask - Kalshi_fee > 0.10`; buy NO if
   `(1-poly_up) - no_ask - Kalshi_fee > 0.10`. At the first qualifying minute,
   take the side with larger edge. One trade per event.
5. Settle against the Kalshi result only.

Polymarket history is a one-minute reference price, not a historical
executable bid/ask. The two venue observations are non-atomic. Report an
additional one cent execution stress, but classify all results as historical
signal paper.

The screen requires at least 40 trades, positive mean and one-cent-stressed
mean, positive UTC-day-clustered lower 95% bound, positive chronological
halves, positive best-10%-day deletion and no day above 20% of gross positive
P&L. Outcome agreement must be at least 99%; otherwise cross-venue hedging is
explicitly rejected even if the directional transfer happens to pass.

No parameter may be changed after the report is viewed. A pass can authorize
only a fresh direct-book prospective collector.
