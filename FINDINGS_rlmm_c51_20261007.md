# FINDINGS — distributional-RL market maker on 15M crypto: **FAIL, 3 of 7 gates**

Pre-registration `PREREG_rlmm_c51_20261007.md` (3 amendments, all before any test row was read).
Instrument `ml/rl/`, dataset `ml/data/mk5s-v3`, scorer `ml/policy_eval.py` unchanged. Scored run
`ml/runs/rlmm-v1-scored/report.json`; the val-selected checkpoint is `ml/runs/rlmm-v1-seed0/c51.pt`
(risk = 1.0, iteration 700, GLFT γ = 0.01, all selected on the 84 val markets before test was read).

## Verdict

A Rainbow-style C51 quoter with a BOCPD regime filter, a queue-adjusted exposure term, CVaR action
selection and an EXP3 adversarial scenario curriculum is **significantly worse than one line of
code** on the 140 held-out markets, and **ties** the closed-form GLFT controller the corpus already
closed.

| arm | c/market | SE | t | fills posted |
| --- | --- | --- | --- | --- |
| always | −33.401 | 18.274 | −1.83 | 83,191 |
| detgate | −11.696 | 10.591 | −1.10 | 54,873 |
| **room4** (`spread_ticks >= 4`, one line) | **+65.708** | 15.283 | **+4.30** | 25,125 |
| logit (30 features) | +69.276 | 14.834 | +4.67 | 10,608 |
| glft (closed form, γ val-selected) | +58.495 | 14.672 | +3.99 | 7,137 |
| **c51 (this work)** | **+56.703** | 14.649 | +3.87 | 11,587 |
| oracle_entry | +505.834 | 46.042 | +10.99 | 38,485 |

Paired per market, the only comparison the instrument supports:

| comparison | diff | SE | t | lo95 | share positive | drop best 5 |
| --- | --- | --- | --- | --- | --- | --- |
| **c51 − room4** | **−9.005** | 4.343 | **−2.07** | −17.52 | 0.407 | −13.58 |
| c51 − glft | −1.792 | 4.597 | −0.39 | | | |
| c51 − logit | −12.574 | 5.783 | −2.17 | | | |
| glft − room4 | −7.213 | 5.106 | −1.41 | | | |
| logit − room4 | +3.569 | 5.983 | +0.60 | | | |

| gate | bar | result | |
| --- | --- | --- | --- |
| G1 significance | paired > 0 at t ≥ 2 | −9.005, t = **−2.07** | ✗ |
| G2 magnitude | ≥ +20 c/market | −9.005 | ✗ |
| G3 attribution | beats GLFT | −1.792, t = −0.39 | ✗ |
| G4 not a width gate | Jaccard < 0.9, non-empty disagreement | 0.367 | ✓ |
| G5 breadth | ≥ 55% markets +, survives drop-best-5 | 40.7%, −13.58 | ✗ |
| G6 capacity | ≥ 2% of offered fills | 13.9% | ✓ |
| G7 null | above a turnover-matched shift null's hi95 | +56.70 vs +14.74 | ✓ |

## Seed stability (declared in the prereg; seed 0 is the scored run)

All three seeds are negative against one line of code, and none passes G1 or G2. Seed 0 is the
scored run per §3 of the prereg; 1 and 2 are the declared stability check and are reported in full
rather than used to pick a winner.

| seed | c51 c/market | c51 − room4 | t | c51 − glft | posts | gates |
| --- | --- | --- | --- | --- | --- | --- |
| **0 (scored)** | +56.703 | **−9.005 ± 4.343** | −2.07 | −1.792 | 11,587 | 3/7 |
| 1 | — | −2.881 ± 4.170 | −0.69 | +4.331 | 11,683 | 4/7 |
| 2 | — | −7.454 ± 4.325 | −1.72 | −0.242 | 11,948 | 3/7 |
| mean | | **−6.45** | | **+0.77** | 11,739 | |

Reading: the margin against `room4` is negative in 3 of 3 (mean −6.45 c/market) but only seed 0
reaches t = 2, so the defensible claim is **"loses to one line, significantly in the scored run and
negatively in all three"**, not "loses by 9". The margin against GLFT is a **zero** and is stable:
−1.79 / +4.33 / −0.24, mean +0.77. The RL machinery does not improve on the closed-form controller
it was built to beat. Post counts are stable to 3% (11,587–11,948 of 83,191 offered), so the
seed spread is in *which* fills, not how many.

## The four things worth keeping

**1. The agent re-derived the width gate, then traded good fills for worthless ones.** This is the
decomposition that explains the whole result — disjoint, on the test fold, across all three seeds:

| fill set | rows (s0 / s1 / s2) | c/contract | mean spread (ticks) |
| --- | --- | --- | --- |
| both c51 and room4 | 9,858 / 10,157 / 9,948 | **+0.826 / +0.858 / +0.808** | 11.1 / 11.0 / 11.3 |
| **only c51** | 1,729 / 1,526 / 2,000 | **−0.117 / +0.054 / +0.061** | **2.61 / 2.66 / 2.62** |
| only room4 | 15,267 / 14,968 / 15,177 | **+0.069 / +0.032 / +0.077** | 6.16 / 6.11 / 6.01 |

The fills the agent and the one-line rule agree on are its entire edge, and they are the widest
books on the tape (~11 ticks, +0.81 to +0.86 c/ct, stable to 6% across seeds). What it does with
its remaining capacity is the loss: it **declines ~15,000 mid-width fills worth +0.03 to +0.08 c/ct
each** and replaces them with **~1,700 narrow-book fills (2.6 ticks) worth a sign-unstable zero**
(−0.117 / +0.054 / +0.061). The exchange is not a disaster per fill — it is a bad trade at volume.
Jaccard 0.367–0.381 passes G4 on the letter, since the policy is not literally `room4`; the
disjoint decomposition is what shows the disagreement is where the money goes.

**2. The BOCPD regime filter is at chance, measured directly rather than inferred from the policy
result.** `ml/rl/regime_lift.py`, train fold, 116,049 rows, label "did this fill make money"
(base rate 0.454). AUC, best of the feature and its negation:

| | `bias_adv` | `bias_sd` | `cp_recent` | `cp_prob` | `run_len` | `bias_mean` | `spread_ticks` | `as_room_c` |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| AUC | **0.5001** | 0.5002 | 0.5023 | 0.5037 | 0.5043 | 0.5080 | 0.5899 | 0.5877 |

`bias_adv` — the signed change-point-filtered flow bias, the single feature the proposal rests on —
scores **0.5001**, which is chance to four decimal places. Paired on the val fold, a logistic on all
40 features beats the incumbent 30 by **+0.05 ± 5.04 c/market (t = +0.01)**, and the 10 new
features *alone* produce a model that takes **zero** entries. That is the third independent
replication of `richer-features-add-nothing-to-the-15m-maker-gate` (v2 measured −0.14 ± 3.33).

**3. The tail IS predictable — by volatility, not by regime.** This is the one place the
distributional framing had a real claim, so it got its own measurement. AUC for "is this fill in
the worst q% of fills", train fold:

| worst q | threshold | `sigma_c` | `spread_ticks` | `cp_prob` | `qxi_lean` | `bias_adv` |
| --- | --- | --- | --- | --- | --- | --- |
| 5% | −5.50 c | **0.6846** | 0.5967 | 0.5280 | 0.5153 | 0.5023 |
| 10% | −3.05 c | **0.6813** | 0.6007 | 0.5350 | 0.5278 | 0.5076 |
| 25% | −1.00 c | **0.6476** | 0.5955 | 0.5252 | 0.5423 | 0.5053 |

So a CVaR-greedy quoter has something real to be greedy about, and it is `sigma_c` — an incumbent
feature, already in the gate through the spread. Change-point detection adds 0.03 of AUC over
chance on the tail and nothing on the mean. **Magnitude is predictable here, in the mean and in the
tail; direction is not, in either.** And the risk parameter did not convert: the val sweep selected
`risk = 1.0` (plain mean) over 0.5 / 0.25 / 0.1, which post less and earn less at every checkpoint.

**4. The val-to-test swing is 19.8 c/market, in the direction selection predicts.** Val said
c51 − room4 = **+10.775 ± 13.273 (t = 0.81)**; test said **−9.005 ± 4.343 (t = −2.07)**. The val
figure was the best of 120 selections (30 checkpoints × 4 risk levels) on 84 markets, and it did
not survive. Nothing in-sample flagged it — the same shape as this corpus's
`in-sample-controls-cannot-detect-selection` (+16.6 pp → −0.4 pp OOS).

## Two implementation facts that cost a run each, both found before test was read

**The textbook BOCPD recursion makes its own headline feature a constant.** Scoring the
change-point branch under the *old run's* posterior predictive (Adams & MacKay eq. 3, and most
implementations) makes the normalised `P(run length = 0)` **exactly the hazard at every t** —
numerator and denominator share the same evidence sum. Measured before the fix: `cp_prob` sat at
0.0200 = H for all 120 steps of a stream that visibly flips regime at t = 60. Scoring a fresh run
under the *prior* predictive is the generatively coherent choice and gives a feature with variance
(0.0104 → 0.285 on the flip). Guarded by
`test_rl.py::test_bocpd_cp_prob_is_not_the_hazard_constant`.

**A two-action quoter collapses to abstention and silently stops training its POST head.** Under
the frozen config, val posts fell 669 → 1,117 → 151 → 10 → 0 → 0 → 8 → 76 → 127 → 23 → 20 out of
48,335 offered fills over iterations 100–1,100. Once the greedy policy abstains, the replay buffer
holds no POST transitions and `Q(POST)` is never updated again — the critic looks converged and is
simply blind. Fixed by rolling 3 episodes per iteration under `always` purely for coverage
(excluded from the bandit's reward); `buf_post_share` then held at 0.41–0.47 and val went from
~0 to +54.75. **A DQN with a do-nothing action needs its action coverage logged next to its loss**,
for the same reason a fill model needs its fill count logged next to its P&L.

## What this closes, and what it does not

**Closed:** the reinforcement-learning extension of the controller family on 15M crypto, at this
instrument. Do not reopen on: a bigger network, more features of the same kind, a longer BOCPD
hazard, a different risk measure, or a different exploration scheme — the binding fact is that the
discriminating information in these inputs is one monotone book statistic, the agent found it, and
its own additions to it are negative. `mm-controller-family-closed-both-15m-classes` stands, and
now stands against a strictly larger policy class.

**Not closed, and not touched here:** the *instrument*. Every number above is `mk5s` cents under
the improved-quote fill model, which overstates adverse selection **5.17×** against 8,038,551 real
Kalshi prints. `room4`'s +65.7 is roughly 11× the live penny-jump arm's +5.9 c/market at settlement
on real fills. The paired differences are the defensible quantity because both arms inherit the
same error; the levels are not. A genuine reopening needs a different *measurement*, not a
different model — the real-print ledger `s·(P − 100y)`, which needs no mid, no queue and no fill
model, is what moved this seat +6.32 c/ct and refuted every simulated number before it.

**The honest summary of the prize, priced before the run and unchanged by it:** G2's +20 c/market
bar deflated to ≈ +$15/day of real income. The branch failed at −9.
