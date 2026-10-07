# PREREG — temporally held-out Coin Race rank calibration

Frozen Tuesday, October 6, 2026 at 07:51:45 PDT, before fitting or scoring this
model.

This is a model-specific chronological holdout inside the already sampled
historical Coin Race panel. It is not a fresh market-data capture. The panel
and its outcomes were used by other, different Coin Race screens, so any pass
can authorize only a prospective direct-book test.

## Frozen split

- development: event opens before 2026-09-25 00:00:00 UTC;
- test: event opens on or after that instant;
- no test event may influence model fitting, scaling or parameter choice.

## Frozen model

For each decision minute `k` in `{5, 8, 12}`:

1. Proxy the start with the Coinbase close at `open-60` and use the completed
   Coinbase candle at `open+60*(k-1)`.
2. For each asset in BTC, ETH, SOL, XRP and HYPE, calculate cumulative log
   return divided by its trailing 120-completed-minute one-minute standard
   deviation times `sqrt(k)`, requiring at least 80 aligned returns. Clip this
   standardized return to `[-5, 5]`.
3. Fit on development events only the conditional softmax
   `score_i = alpha_i + beta*z_i`. Minimize cross-entropy plus an L2 penalty
   `0.5 * sum(parameters^2)` with deterministic L-BFGS. Fractional tied
   settlements are normalized to soft labels summing to one.
4. At each test event, compare the fitted probability with each same-minute
   historical YES ask and standard taker fee. At the first `k` where the
   largest edge is strictly above 10 cents, buy that asset. Take at most one
   trade per event.
5. Settle at the published fractional settlement value.

No feature, penalty, threshold, decision minute or split may be changed after
viewing the result.

## Gate

Report trade count, fee-adjusted mean, UTC-day-clustered interval,
chronological halves, best-10%-day deletion, per-asset contribution and an
extra one-cent execution stress. A historical paper pass requires at least 20
test trades, positive raw and one-cent-stressed means, a positive clustered
lower 95% bound, both chronological halves positive, positive best-day-trimmed
mean and no asset above 60% of gross positive P&L.

Same-minute Coinbase and Kalshi candles are non-atomic and Coinbase is only a
proxy for CF Benchmarks. Even a pass is not executable evidence.
