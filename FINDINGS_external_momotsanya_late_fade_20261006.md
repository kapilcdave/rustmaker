# Findings — external near-strike late fade

The frozen causal proxy in
`PREREG_external_momotsanya_late_fade_20261006.md` failed cleanly in both
independent periods.

| period | trades | mean/contract | 95% clustered low | 1c stress | halves |
|---|---:|---:|---:|---:|---:|
| Sep 13–Oct 5 | 281 | -1.843c | -7.020c | -2.843c | -3.007c / -0.688c |
| Jul 2–Sep 8 | 683 | -2.171c | -5.824c | -3.171c | -2.478c / -1.865c |

Deleting the best 10% of days worsened the means to -4.639c and -5.367c.
Concentration was not the problem; the directional rule itself was negative.

The external source's recovery/martingale sizing was deliberately excluded.
Increasing size after losses cannot turn negative per-contract expectancy
positive and would only increase drawdown and tail exposure.

**Decision: closed.** Do not deploy the late-fade entry or its recovery sizing.
