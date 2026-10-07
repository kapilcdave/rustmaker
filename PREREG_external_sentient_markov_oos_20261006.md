# Preregistration — external Sentient Markov stack OOS

Frozen: 2026-10-06 08:24 PDT, before running this rule on local September–
October outcomes.

Source: `Julian-dev28/sentient-market-reader`, public head
`bfbf470f49cc7585f59fde02f4501da0ae480e37`, dated 2026-04-29. The local
market period begins in September, so it is post-publication.

## Frozen translation

Port the deterministic gates and model constants from
`python-service/run_backtest.py` and `trade_daemon.py`:

- aggregate completed Coinbase one-minute bars to 5m and 15m bars;
- 9 Markov return states with the source bounds, returns and variances;
- 480-state rolling history and at least 20 transitions;
- absolute `p_yes - 0.5 >= 0.11`;
- current-state persistence at least 0.82;
- 15m Garman–Klass volatility no more than 0.0025;
- source variance-ratio Hurst at least 0.50;
- price at least 0.02% from the strike;
- velocity toward the strike no more than 40% of crossing velocity;
- block UTC hours 8, 11, 16, 18 and 21;
- YES ask cap 72c and NO ask cap 65c.

Evaluate once at each available minute-close from minute 3 through minute 12.
Use the source daemon's timing rule: 3–12 minutes left only when the live YES
ask is 65–73c; otherwise 6–9 minutes left. Select the first passing signal per
market.

The source uses the latest completed 5m bar. The local implementation will
apply that same one-full-5m-bar lag and will not use a partial Coinbase bar.
Entry is the actual same-minute Kalshi side ask close. Score one contract to
settlement with the standard taker fee, not the source backtest's fabricated
empirical price table or maker-fee assumption.

## Acceptance gate

At least 100 trades, positive fee-adjusted mean, positive one-cent stress,
positive day-clustered 95% lower bound, both halves positive, positive
best-10%-day-deleted mean, and no day above 20% of gross positive P&L.

Also report a next-minute execution diagnostic. No threshold will be changed
after seeing the result.
