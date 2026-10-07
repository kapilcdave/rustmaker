# Preregistration: external BTC/ETH minute-5 regime rule

Frozen before scoring the local outcomes.

## Source

- Repository: `rodiiipooo/kalshi-crypto-regime-edge`
- Frozen commit: `73708296532ae163e369fe1f494d47ef6a41f099`
- Commit timestamp: 2026-06-27
- Source study period: ten days, entirely in sample.

The executable `STATE_ACTION` map in `make_figures.py` is authoritative. It
contains nine states, including three states omitted from the README's shorter
target-state table. No state will be added, removed, or sign-flipped after
opening the holdout.

## Untouched evaluation sample

- Kalshi: `/Users/kapil/proj/kalshi-scalp/data/oos_20261006/candles_BTC.json`
- Coinbase:
  - `/Users/kapil/proj/kalshi-scalp/data/oos_20261006/cb_BTC-USD.json`
  - `/Users/kapil/proj/kalshi-scalp/data/oos_20261006/cb_ETH-USD.json`

The common aligned sample begins 2026-09-14, after publication.

## Frozen feature construction

At minute 5 of each settled BTC contract:

1. `btc_dir` is `B+` iff the Coinbase BTC close at `t0+5m` exceeds its close
   at `t0`; otherwise `B-`.
2. `eth_dir` is defined identically.
3. Pre-window volatility is the sample standard deviation of the latest 30 BTC
   one-minute log returns with timestamps strictly before `t0`, requiring at
   least ten.
4. Intra-window volatility is the sample standard deviation of BTC log returns
   timestamped from `t0` through `t0+5m`, inclusive.
5. `vol_ratio = intra_vol / pre_vol`; deficit is `<0.6`, excess is `>1.4`,
   otherwise track.
6. The source's rolling 30-return BTC/ETH correlation is recorded but does not
   gate its executable backtest.

## Frozen executable state map

- Buy NO:
  - `B-/E-/deficit`
  - `B-/E-/track`
  - `B-/E-/excess`
  - `B-/E+/deficit`
  - `B-/E+/excess`
- Buy YES:
  - `B+/E+/track`
  - `B+/E+/deficit`
  - `B+/E+/excess`
  - `B+/E-/deficit`

All other states skip.

Governing execution uses the actual local minute-5 executable ask: YES ask for
YES, and `1 - YES bid` for NO. One contract is held to settlement and charged
the local conservative whole-cent taker fee.

## Fixed diagnostics and promotion gate

- Repeat at minute 6 without recomputing the signal.
- Report each state separately, but no state-only variant can be promoted from
  this holdout.
- Use the same battery-wide promotion gate: at least 100 trades, positive after
  an extra 1c stress, positive day-clustered 95% lower bound, both chronological
  halves positive, positive after deleting the best 10% of days, and best-day
  gross-positive share no greater than 20%.

## Post-score timestamp audit

This section was added after the frozen score, without changing any result.
Coinbase's candle timestamp is the bar **open**, while Kalshi
`end_period_ts` is the bar **close**. Therefore a Coinbase close indexed
`t0+5m` is not observable until `t0+6m`, one minute after the Kalshi close
indexed `t0+5m`. The source implementation joins those two rows at the same
index and thus leaks one minute of future spot into its nominal minute-5 entry.

Accordingly, the originally designated minute-5 result is retained as an audit
of the source code but is not promotion-eligible. The preregistered minute-6
execution diagnostic is the causally aligned result for that signal because the
spot close used by the signal is observable then.
