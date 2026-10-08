# FINDINGS — RLMM v2: the critic's ranking is REAL, its calibration was the bug, and the prize is 10× too small

Pre-registration `PREREG_rlmm_v2_20261007.md` (3 amendments, all before the test fold was read).
Scored run `ml/runs/rlmm-v2-scored/report.json`; checkpoint `ml/runs/rlmm-v2/c51v2.pt`.
**VERDICT: FAIL, 3 of 7 gates** — but for a different and more useful reason than v1.

## The scored result

Test fold: **480 markets**, 68,127 rows (v1 had 140 markets).

| arm | c/market | SE | t | fills |
| --- | --- | --- | --- | --- |
| always | −7.751 | 3.372 | −2.30 | 68,127 |
| detgate | +3.033 | 2.810 | +1.08 | 47,306 |
| **room4** | **+9.156** | 1.530 | **+5.99** | 17,787 |
| logit | +6.474 | 1.315 | +4.92 | 4,833 |
| glft | +4.255 | 1.008 | +4.22 | 1,512 |
| **c51v2** | +3.658 | 0.710 | +5.16 | 1,858 |
| oracle_entry | +126.979 | 5.611 | +22.63 | 32,648 |

| comparison | diff | SE | t | share+ |
| --- | --- | --- | --- | --- |
| **c51v2 − room4** | **−5.498** | 1.252 | **−4.39** | 0.306 |
| c51v2 − glft | −0.597 | 0.657 | −0.91 | 0.335 |
| c51v2 − logit | −2.816 | 0.948 | −2.97 | 0.260 |
| logit − room4 | −2.682 | 1.147 | −2.34 | 0.394 |

G1 ✗ · G2 ✗ · G3 ✗ · G4 ✓ · G5 ✗ · G6 ✓ · G7 ✓

## 1. The methodological fix worked, and it is the most transferable result here

v1 selected its checkpoint on **absolute** val c/market and gave back **19.8 c/market** out of
sample (val +10.775 → test −9.005). v2 selected on the **paired difference against `room4`** —
level-robust, because both arms are scored on identical rows — and the swing collapsed to
**1.1 c/market** (val −4.374 → test −5.498).

This mattered because the chronological split left the val fold with the **opposite sign to both
train and test** on the ungated baseline (val +0.0770 c/ct, train −0.0735, test −0.0546). Absolute
val cents were measuring fold drift no policy controls. **Select on the statistic your gate is
written on.**

## 2. The diagnosis: the ranking is right, the zero is wrong

This is the finding that supersedes v1's "RL loses to one line". Ensemble Q difference by spread
band on the test fold, against the band's true edge:

| spread (ticks) | n | TRUE edge c/ct | mean Q(POST)−Q(SKIP) | seed sd |
| --- | --- | --- | --- | --- |
| 2–3 | 6,150 | **−0.2449** | −0.8771 | 0.104 |
| 3–4 | 1,973 | +0.0409 | −0.5272 | 0.103 |
| 4–6 | 1,616 | **+0.2178** | −0.3027 | 0.092 |
| 6–8 | 692 | **+0.1588** | −0.2096 | 0.091 |
| 8–11 | 461 | **+0.3226** | −0.1368 | 0.084 |
| 11+ | 234 | **+0.7622** | **+0.2191** | 0.087 |

The ordering is **perfectly monotone and correct**, and the three seeds agree to ~0.09 c. So this is
**not** a noise or resolution failure. The Q difference is shifted down by roughly **−0.5 c**, so it
crosses zero at ~11 ticks when the true edge turns positive at **4**. That is why the agent posted
only 1,858 fills against `room4`'s 17,787, and the arithmetic of the gap is exact: the 15,963 fills
it declined that `room4` took are worth **+0.165 c/ct**, i.e. **+5.5 c/market** — the whole deficit.

| fill set | rows | c/ct | mean spread |
| --- | --- | --- | --- |
| both | 1,824 | **+0.964** | 11.08 |
| only c51v2 | 34 | −0.057 | 2.58 |
| only room4 | **15,963** | **+0.165** | 5.91 |

**A likely mechanism, and it indicts a component I added on purpose.** `SKIP`'s immediate reward is
*exactly* 0 while `POST`'s is noisy (shaped reward sd 3.6 c). Double DQN removes optimism from the
action whose value is estimated from noisy returns and has nothing to remove from the one that is
known — so the correction is asymmetric and tilts systematically toward abstention. I included
Double DQN specifically to stop the critic over-posting; on an action set containing an exact-zero
abstain option it over-corrects.

## 3. The critic carries real information beyond the width gate

Measured on the **val** fold, restricted to `room4`'s own accepted set (spread ≥ 4, 8,007 rows,
mean +0.4270 c/ct):

* **AUC(Q difference → fill was profitable) = 0.5873**, against **AUC(spread) = 0.5627** on the
  identical rows. The learned score beats the width gate *within* the width gate's own population.
* Quartiles by Q difference: **−0.024 / +0.114 / +0.340 / +1.277 c/ct** — monotone across a 1.3 c
  range.

This is the first time in this corpus that a learned model has shown information the one-line rule
does not have. It is a real result and it is why the branch does not close the way v1 did.

## 4. The ceiling, and why the branch still closes

A constant bias is fixed by one threshold, so `ml/rl/tau_ceiling.py` sweeps
`post iff Q(POST) − Q(SKIP) > τ` on val — the best a correctly calibrated version of this critic
can do on the fold it was tuned on:

| τ | c/market | vs room4 | SE | t | fills | c/ct |
| --- | --- | --- | --- | --- | --- | --- |
| −1.00 | +22.212 | −0.071 | 2.464 | −0.03 | 41,302 | +0.155 |
| −0.80 | +24.365 | +2.082 | 1.980 | +1.05 | 35,016 | +0.200 |
| **−0.50** | **+24.433** | **+2.150** | 1.320 | **+1.63** | 23,078 | +0.305 |
| −0.40 | +23.311 | +1.027 | 1.104 | +0.93 | 16,726 | +0.401 |
| 0.00 (shipped) | +17.910 | −4.374 | 1.518 | −2.88 | 4,782 | +1.079 |

**The ceiling is +2.150 ± 1.320 c/market (t = 1.63), in-sample on the selection fold, chosen as the
best of 11 τ values on 288 markets.** The curve is non-monotone (−0.80 → +2.08, −0.60 → +0.55,
−0.50 → +2.15), so a good part of even that is τ-selection noise, and out of sample it would be
lower still.

**Against a G2 bar of +20 c/market, the ceiling is ~10%.** The branch therefore closes on
**magnitude**, not on whether the model works — the model does work, it just is not worth enough.
Per the prereg's amendment rule, a τ fitted after reading the v2 test score cannot be scored on the
same fold and called a pass, and the val ceiling makes a third look pointless: no plausible test
result clears +20 from a base of +2.

## 5. What the data changes bought, including the one that failed

* **The `live`-rule fix is the big one** and it has its own write-up: the fill model's "my quote
  survived" test was `i == j` on the book index, which measures feed coalescing, not survival
  (70.30% pass on the throttled tape vs **1.64%** on the full-fidelity probe tape). Fixing it to
  touch-price equality took the dataset from 558 to **1,920 markets** with a **480-market** test
  fold, and moved the instrument's ungated per-contract level from **−0.1321 → −0.0480 c/ct**
  against the 8.04M-real-print ledger's **+0.254** — 2.75× closer, **still the wrong sign**.
* **`room4`'s marginal value collapsed with it**, from +91.7 c/market over `always` on v3 to
  **+8.4** on v4. Much of the width gate's apparent power was the throttled instrument.
* **Atom resolution: 3.358 → 0.360 c/atom (9.3× finer)** by sizing the support off train-fold
  q[0.01, 0.99] with 201 atoms, clipping 0.69% of targets. This is what made the CVaR branch
  representable at all.
* ⚠ **The capture decomposition did far less than I claimed when proposing it.** `capture_c` sd is
  **0.458 c** against `adverse_c`'s **3.609 c**, so it removes **1.5% of the variance**, not the
  order of magnitude I expected; the shaped support came out **1.0×** the unshaped one. Its real
  and narrower benefit is that the term carrying the entire edge is supplied analytically instead
  of approximated. The instrumentation printed the ratio and caught the error before it reached a
  conclusion, which is the only reason it is in this section rather than in the headline.
* **CVaR never converted.** The val sweep picked `risk = 1.0` (plain mean) at every checkpoint;
  every tightening lost money (risk 0.5 → −19.8 vs room4, risk 0.1 → −22.2). Consistent with the
  v1 finding that the tail's one real predictor is `sigma_c`, an incumbent feature.

## 6. The mk60s secondary — `INFORMATIVE, NOT A PASS` (prereg Amendment 4)

Same 1,920 markets relabelled off the `mk60s_c` column (1.80% window-clipped, under the 10%
guard), τ swept on val. **FAIL 4/7.** Test fold, 480 markets:

| arm | c/market | SE | t | fills |
| --- | --- | --- | --- | --- |
| always | −10.115 | 10.467 | −0.97 | 68,127 |
| detgate | +16.911 | 8.545 | +1.98 | 47,306 |
| room4 | +6.269 | 4.027 | +1.56 | 17,787 |
| **logit** | **+18.217** | 6.499 | **+2.80** | 16,152 |
| glft | +2.049 | 2.574 | +0.80 | 1,559 |
| c51v2 (τ=−1.00) | +3.711 | 5.004 | +0.74 | 44,957 |
| oracle_entry | +508.252 | 21.972 | +23.13 | 33,390 |

**c51v2 − room4 = −2.558 ± 4.721 (t = −0.54): a zero, not a loss** — better than mk5s's −5.498,
and it beats GLFT (+1.662) so G3 passes. But **val said +12.068 ± 6.361 and test said −2.558**, a
14.6 c swing, so the τ did not transfer.

**Why the τ failed to transfer, and it is a methodology lesson I got half-right.** Amendment 2
moved selection to the paired difference against `room4` to cancel fold drift, and it worked for
mk5s (1.1 c swing). It did **not** work here, because τ = −1.00 makes the agent post **66% of
offered fills** — close to `always` — while `room4` posts 26%. **Pairing against a fixed-turnover
benchmark removes the benchmark's level but not the interaction between turnover and fold drift.**
The val fold's ungated baseline is **positive** (+0.0664 c/ct) and the test fold's is **negative**
(−0.0713), so a near-`always` policy looked excellent on val for a reason that reverses on test.
The correct selector for a policy whose turnover is not the benchmark's is its **own
turnover-matched null**, which this run already computes for G7: on test the agent is **+3.711
against a shift null of −6.47**, i.e. **+10.2 above a random policy at its own turnover**. So the
critic does add information — it was aimed at the wrong operating point.

The fill-set decomposition confirms it: τ = −1.00 opened the floodgates to narrow books.

| fill set | rows | c/ct | mean spread |
| --- | --- | --- | --- |
| both | 16,336 | +0.227 | 6.49 |
| **only c51v2** | **28,621** | **−0.068** | **2.32** |
| only room4 | 1,451 | −0.485 | 5.87 |

## 7. ⚑ The real result of the secondary is about the LABEL, not the model

**At the 60-second markout a plain 30-feature logistic beats the width gate; at the 5-second
markout it loses to it.** Same features, same markets, same train/test split, no val selection on
either (`logit` is fit on train and scored once):

| | logit − room4 | t | share+ | drop-best5 | series positive | early half | late half |
| --- | --- | --- | --- | --- | --- | --- | --- |
| **mk5s** | **−2.682 ± 1.147** | −2.34 | 0.394 | −3.64 | **3/9** | −1.34 | −4.03 |
| **mk60s** | **+11.948 ± 6.743** | +1.77 | 0.602 | +6.71 | **8/9** | +2.26 | +21.64 |

So the horizon — not the model class and not the feature set — was the binding constraint on
whether anything beyond width pays. That is exactly the corpus's standing instruction when a model
collapses to one feature ("the question is the LABEL or the instrument, not the inputs"), and it
could not be tested before: mk60s needs ~2,104 markets and this corpus had 558 until the `live`-rule
fix produced 1,920.

**It is suggestive, not established.** Breadth is good — 8 of 9 series positive, 60.2% of markets,
survives dropping its best 5 — but **the time halves are +2.26 ± 8.75 and +21.64 ± 10.24**, a ~10×
magnitude disagreement, and the pooled t is 1.77. By this corpus's own durability standard that is
an `IN-SAMPLE ONLY`-shaped flag. It needs fresh tape, not another look at these 480 markets.

## 8. Where this leaves the branch

**Closed on magnitude.** Do not reopen on architecture, atoms, risk measure, ensemble size, feature
set, or exploration — all six have now been measured and the binding fact is that a correctly
calibrated critic's advantage over one line of code is ~+2 c/market against a +20 bar.

**The live lead is NOT the RL agent — it is the mk60s label** (§7): a plain logistic beats the
width gate by +11.948 ± 6.743 with 8/9 series positive, where at mk5s it loses by −2.682 with 3/9.
That needs fresh tape to settle its time-instability, and it needs its own pre-registration with
mk60s primary. It is a feature/horizon result that owes nothing to C51.

**Two further things are open and both are about the INSTRUMENT, not the model:**
1. The `live`-rule fix moved the simulated instrument 2.75× toward the real-print ledger and it is
   still the wrong sign. Whatever remains of the 5.17× adverse-selection overstatement is now the
   largest single error in the measurement chain, and it is measurable on the real print set
   (`s·(P − 100y)` needs no mid, no queue, no fill model).
2. `mk60s` is now scored (§6, §7) and the agent ties `room4` there while a logistic beats it. The
   mark that remains unspent is `settle`, and it is the one only a real-print ledger should settle.

**Standing constraint on all of it:** the seat this would feed is **+$1.26/day at realised duty
with an all-time t of +0.87** (`FINDINGS_seat_funding_20261007.md`), which is why the +20 c/market
bar was set where it was.
