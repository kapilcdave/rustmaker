# Coin Race 15-minute structural strategies

Date: Tuesday, 2026-10-06. Public market data only; no orders were placed.

Preregistrations:

- `PREREG_crypto_leader_complete_set_20261006.md`
- `PREREG_crypto_leader_directional_implication_20261006.md`
- `PREREG_crypto_leader_covariance_20261006.md`

## Instrument inventory

`KXCRYPTOLEAD15M` has five mutually exclusive markets per quarter hour:
BTC, ETH, SOL, XRP and HYPE having the highest CF Benchmarks return. Starting
and ending values are separate 60-second time-weighted averages. Tied leaders
split the dollar and each fractional payout is rounded down to cents.

The series was a gap in the prior 15-minute corpus. The other currently listed
standard 15-minute series ADA, BCH and TON had no markets returned by the
public market endpoint during this study.

## Complete-set identities

Contractual lower bounds:

- buying all five YES contracts pays at least $0.99;
- buying all five NO contracts pays at least $4.00;
- buying four NO contracts pays at least $3.00 regardless of which leg is
  omitted.

On a preregistered seeded sample of 176 events, 2,034 common minute closes:

- all NO: zero positive fee-adjusted observations, best -2c;
- every four-NO construction: zero positive observations, best -2c;
- all YES: one apparent opportunity, +15c after fees and +10c after an
  additional one cent per leg.

The lone all-YES row was event `KXCRYPTOLEAD15M-26SEP161415` at 18:02 UTC on
2026-09-16. Its candle asks were BTC 8c, ETH 24c, SOL 16c, XRP 13c and HYPE
17c. It is **not executable evidence**:

- the five candle closes are last quotes within a minute, not atomic books;
- nearby public prints were spread from 18:01:12 through 18:02:44;
- the nearby BTC, SOL and HYPE prints were only 0.01 contracts;
- candle history has no displayed depth.

The prospective direct-book collector remains the governing test. Its early
read over two events had no signal; the best all-YES margin was -14c after
fees.

## Cross-series logical implication

For leader `L_i` and positive directional return `U_i`:

`L_i AND U_j => U_i`.

Therefore `NO(L_i) + NO(U_j) + YES(U_i)` pays at least $1 for every ordered
pair `i != j`.

Across 175 joined sampled events and 2,022 common minute closes, all 20
relations had zero positive observations. Every relation's historical best
was -2.2c after fees. The early direct-book journal also had no signal.

## Covariance Monte Carlo

A preregistered Gaussian remaining-return model used each event's prior 120
aligned Coinbase minutes to estimate the BTC/ETH/SOL/XRP/HYPE covariance
matrix, then simulated the Coin Race winner at minutes 5, 8 and 12. The
numerically corrected frozen run produced 25 trades over 165 eligible events:

- mean: **-15.60c** after taker fees;
- clustered lower 95% bound: **-27.11c**;
- chronological halves: **-7.33c / -23.23c**;
- one-cent stress: **-16.60c**;
- after deleting the best 10% of UTC days: **-22.62c**.

It missed the 40-trade minimum and every profitability criterion. This model
is closed. The result also warns against treating a short-horizon
variance/covariance estimate as a calibrated leader probability without
modeling jumps, asset-specific tails and CF-average microstructure.

## Temporally held-out rank calibration

A lower-dimensional alternative fit only six softmax parameters per decision
minute on events before September 25:

`score_i = asset_intercept_i + beta * standardized_current_return_i`.

It then traded only the September 25–October 5 test events. The frozen result:

- 31 trades from 80 eligible test events;
- mean **-1.065c** after taker fees;
- clustered lower 95% bound **-9.885c**;
- chronological halves **-0.467c / -1.625c**;
- one-cent stress **-2.065c**;
- best-10%-day deletion **-5.962c**.

This missed the 20-trade economics gate despite having enough trades. A
prospective direct-book journal using the already fitted parameters began at
07:52:55 PDT, but it cannot rehabilitate the failed historical arm with only a
few remaining events; it is retained as a model-behavior check.

## Interim verdict

- Coin Race all-NO, four-NO and leader/directional implication constructions:
  **closed by both historical screen and early direct books**.
- Coin Race all-YES: **one non-atomic historical anomaly only**; no direct
  prospective confirmation so far.
- Coin Race covariance Monte Carlo: **closed**, with strongly negative
  historical P&L after a numerically corrected frozen run.
- Coin Race held-out rank calibration: **closed historically**; prospective
  direct-book observations are descriptive unless the frozen short gate is
  genuinely met.

Collectors continue through 10:00 PDT, after which direct results supersede
this interim paragraph.

Canonical reports:

- `idea_lab/crypto_leader_history_report.json`
- `idea_lab/crypto_leader_directional_history_report.json`
- `idea_lab/crypto_leader_covariance_report.json`
- `idea_lab/crypto_leader_rankcal_report.json`
- `idea_lab/crypto_leader_rankcal_live_report.json`
- `idea_lab/crypto_leader_complete_set_live_report.json`
- `idea_lab/crypto_leader_directional_live_report.json`
