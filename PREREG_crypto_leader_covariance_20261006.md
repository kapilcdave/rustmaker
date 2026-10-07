# PREREG — covariance Monte Carlo for Coin Race 15-minute markets

Frozen Tuesday, October 6, 2026 at 07:48:02 PDT, before Coinbase data was
joined to the sampled Coin Race candle panel or any strategy P&L was computed.

This is a preregistered retrospective screen, not a clean temporal holdout.

For each sampled BTC/ETH/SOL/XRP/HYPE leader event:

1. Proxy each starting 60-second CF average with the Coinbase minute close at
   `open-60`.
2. At decision minutes 5, 8 and 12, compute each asset's log return from that
   start proxy to the completed Coinbase minute.
3. Estimate the five-asset one-minute log-return covariance matrix from the
   prior 120 completed minutes, requiring at least 80 aligned returns.
4. With deterministic seed `20261006 + open_ts + k`, draw 20,000 multivariate
   normal remaining-return vectors with covariance multiplied by `15-k`.
5. Fair probability for each asset is its share of simulated terminal returns
   that are the strict maximum. Simulation ties are split equally.
6. At the first decision where any asset's fair minus its same-minute YES ask
   minus the standard taker fee exceeds 10 cents, buy the asset with the
   largest edge. One trade per event.
7. Settle at the published fractional settlement value, not a coerced binary
   label.

Report fee-adjusted mean, UTC-day-clustered interval, chronological halves,
best-10%-day deletion, per-asset P&L and an additional one-cent execution
stress. A paper screen requires at least 40 trades, a positive clustered lower
95% bound, positive halves and no asset above 60% of gross positive P&L.

Same-minute Coinbase and Kalshi candles are non-atomic, and Coinbase is only a
proxy for the CF Benchmarks averages. A pass can authorize only a direct-book
prospective collector.

## Numerical implementation correction

The first execution of the scorer emitted NumPy matrix-multiplication warnings
inside `multivariate_normal` for several near-singular empirical covariance
matrices. That report is void. The sampler was replaced with an explicit
symmetric eigendecomposition, clipping only negative numerical eigenvalues to
zero. The host's Accelerate-backed small-matrix `matmul` then emitted the same
warnings even though every operand was finite and approximately `1e-3`, so
the standard-normal/covariance-root product is evaluated as an explicit
elementwise contraction. This is the identical frozen Gaussian linear
transform and changes no strategy parameter.
