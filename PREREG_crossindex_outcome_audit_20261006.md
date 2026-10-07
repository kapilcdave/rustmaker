# PREREG — CF Benchmarks versus Chainlink 15-minute direction audit

Frozen Tuesday, October 6, 2026 at 08:02:36 PDT, before downloading the broad
Polymarket outcome population.

Population: every settled local `KXBTC15M` market in the September 13–October
5 download. Match its open timestamp to Polymarket slug
`btc-updown-15m-{open_unix}` and require a closed, definitive UP/DOWN outcome.

Compare Kalshi's CF Benchmarks BRTI direction with Polymarket's Chainlink
BTC/USD 60-second-TWAP direction. Report matched count, exact disagreements,
agreement rate and Wilson 95% interval. For each disagreement report the
Kalshi settlement move from strike in basis points.

This is a risk audit for the prospective opposite-side paired-book collector,
not a strategy backtest. A lower agreement bound above 99% permits continued
paper collection but never makes a cross-index pair risk-free. Any mismatch
must be modeled as a possible $1 pair loss rather than spread capture.

## Download integrity correction

The first request burst matched only 631 markets because 1,474 cached entries
were HTTP or connection errors from excessive concurrency. Its outcome-rate
calculation is void as the requested population audit. Re-fetch error entries
with four workers, persistent retry/backoff, and keep every strategy and risk
criterion unchanged. Only the completed retry report is governing.
