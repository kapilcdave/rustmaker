# Preregistration — prospective Sentient Markov direct-book journal

Frozen: 2026-10-06 08:31 PDT, before starting the collector.

This is a prospective read-only check of the exact deterministic stack frozen
in `PREREG_external_sentient_markov_oos_20261006.md`.

- Instrument: open `KXBTC15M` markets.
- Decisions: minutes 3 through 12, no more than 20 seconds after each exact
  minute boundary.
- Information order: fetch completed Coinbase one-minute history first, then
  fetch the direct public Kalshi order book.
- Coinbase aggregation and all Markov/Hurst/volatility/velocity/distance,
  hour, timing and price-cap gates are unchanged.
- Signal price: direct YES or NO ask derived from the opposite best bid.
- At most one signal per market.
- No order is submitted.

The final report will settle any signals through the public market endpoint
and score the captured ask plus taker fee. No threshold will be changed. This
short journal is descriptive unless it independently reaches the same
100-trade acceptance gate as the historical preregistration.
