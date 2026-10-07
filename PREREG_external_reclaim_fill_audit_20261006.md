# PREREG — external BTC bot authenticated-fill audit

Frozen Tuesday, October 6, 2026 at 08:09:08 PDT, before calculating results.

Audit every fill and settlement in the public
`J0shusmc/Kalshi-BTC/reports/btc15_trade_log.json` file as cloned at commit
`084225ed5afd77ee6b4036a36a9e5e4556e2be85`. The file SHA-256 is
`d0d3f6176bcb1a23d93616fa0a53965634129d1613dc69281d8b894bcbea8df6`.
Do not omit strategies, dates, losses or partial positions.

For each settled ticker, calculate cash P&L from the settlement's YES and NO
contract counts, reported total costs and reported aggregate fee:

`winning-side count - yes total cost - no total cost - fee`.

Map strategy labels only from the file's `entered_signals` records. Report
total dollars, equal-trade mean, per-contract mean, strategy breakdown,
chronological halves, UTC-day clustered uncertainty, drawdown and
concentration.

This is **external self-reported live evidence**, not locally authenticated
venue evidence. It cannot promote a strategy to the corpus's REAL-LIVE tier
because the repository author, credentials and file provenance are not
independently verifiable.
