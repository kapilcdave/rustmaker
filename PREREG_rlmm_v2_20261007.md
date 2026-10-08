# PREREG — RLMM v2: capture-decomposed C51 on 4.4× the data

Frozen 2026-10-07, before any val or test number on `mk5s-v4` exists. Supersedes
`PREREG_rlmm_c51_20261007.md`, whose single test evaluation is spent (v1 FAILED 3/7 gates,
`FINDINGS_rlmm_c51_20261007.md`). Instrument `ml/rl/{shaped,train2}.py`, scorer
`ml/policy_eval.py` unchanged.

## 0. What v1 established, and what therefore has to change

v1 was not short of architecture. It was short of **signal**, and the scored run says so precisely:

| v1 fact | implication |
| --- | --- |
| the cell it got wrong is the 15,267 `room4` fills it declined, worth **+0.069 c/ct** | the quantity to resolve is ~0.07 c |
| its C51 support was q[0.001, 0.999] = **3.613 c/atom** | the grid was 52× coarser than the signal |
| val **+10.775** → test **−9.005**, a 19.8 c swing out of best-of-120 | the selection, not the policy, set the shipped number |
| 558 markets, from 3 h of tape | the sample was tiny |
| `bias_adv` AUC **0.5001**, all 10 new features add +0.05 ± 5.04 c/market | more features of that kind are foreclosed |

So v2 changes the **data, the learning signal and the selection** — and deliberately changes
nothing about the feature set, the env, the action space, the scorer or the benchmark.

## 1. The four changes, each with its measured justification

**(a) 4.4× the data, and a genuinely fresh test fold.** `mk5s-v4` is built from the two original
tapes **plus the 68 hourly gate-probe tapes** (860 MB, 2026-10-02 → 10-07, all 9 crypto 15M series)
that `runners/gate_probe.sh` has been collecting and that have never been used for anything.
Chronological split at a market boundary therefore puts the entire new period in val/test: the test
fold is tape that no model in this corpus has ever been selected against.

**(b) Capture decomposition — the edge term becomes exact instead of approximated.**
`net_c = capture_c − adverse_c` where `capture_c = s·(px_c − mid_c)` is a *deterministic* function
of state columns. Define `Q̃ = Q − c` with `c(s,POST) = capture_c(s)`, `c(s,SKIP) = 0`; the Bellman
recursion becomes

    Q̃(s,a) = [r(s,a) − c(s,a)] + γ·E[ max_a' ( Q̃(s',a') + c(s',a') ) ]

which is **exact** — the bootstrap adds `c(s',a')` back inside the max, so the greedy action is
still chosen on true Q. Checked numerically in `test_rl.py::test_shaping_is_exact`, because the
obvious error (shaping and forgetting to add `c` back) changes the optimal policy *silently*.

⚠ **Measured on the train fold before freezing, and it corrects my own stated rationale:**
`capture_c` sd is **0.440 c** against `adverse_c`'s **3.591 c**, so the decomposition removes
**1.5% of the variance, not an order of magnitude.** What it actually buys is that the term carrying
the entire edge is supplied analytically and cannot be corrupted by function-approximation error.
That is a narrower claim than "variance reduction" and it is the one being tested.

**(c) Atom grid sized off the measured target distribution — a 9.2× resolution gain.** The shaped
3-step return is heavy-tailed: q0.001 = −71.19 c but q0.01 = −31.19 c, so v1's support spent all 51
atoms covering a range set by 0.1% of samples. v2 uses **q[0.01, 0.99] with 201 atoms = 0.394
c/atom**, clipping **0.68%** of targets. This is an explicit trade — a little tail censoring for an
order of magnitude of resolution where the decision happens — and it is the change that makes the
CVaR branch meaningful at all, since a 3.6 c grid cannot represent a tail.

**(d) Seed ensemble + smoothed checkpoint selection, against the 19.8 c swing.** Q is the mean over
3 independently seeded critics, and the checkpoint is the centre of the best 3-point moving average
of the val curve rather than its single luckiest point. If fewer than 3 evaluations exist the run
**refuses to save or score** rather than silently shipping the last iterate.

## 2. Frozen configuration

Unchanged from v1 and not selected on any fold: `GAMMA_RL=0.99`, `N_STEP=3`, `LR=3e-4`,
`BATCH=512`, `HIDDEN=128`, `TARGET_SYNC=250`, `WARMUP=4000`, `GRAD_STEPS_PER_ITER=8`,
`MARKETS_PER_ITER=6`, `COVER_PER_ITER=3`, `BUFFER=400_000`, PER `alpha=0.6 beta=0.4`, noisy
`sigma0=0.5`, BOCPD `HAZARD=1/50 R_MAX=200`, EXP3 `gamma=0.15`, `POS_CAP=10`, `CLIP_CT=1`,
`LAG_US=11_000`. New and frozen here: `N_ATOMS_V2=201`, `SUPPORT_Q=(0.01,0.99)`, `SMOOTH=3`,
`--seeds 3`.

Selected on **val only**, from grids frozen here: CVaR level `risk ∈ {1.0, 0.5, 0.25, 0.1}`, the
smoothed checkpoint, and GLFT's `gamma ∈ {3e-5 … 1e-2}`. The test fold is read only under
`--score-test`.

## 3. Decision rule — the same seven gates, unchanged from v1

G1 paired `c51v2 − room4` > 0 at **t ≥ 2** · G2 diff **≥ +20 c/market** · G3 beats GLFT ·
G4 Jaccard < 0.9 with a non-empty disagreement · G5 ≥ 55% of markets positive and survives
drop-best-5 · G6 posts ≥ 2% of offered fills · G7 above a turnover-matched circular-shift null's
hi95. **All seven must pass.** Gates are deliberately *not* relaxed to match v1's result; a bar
moved after a failure is not a bar.

The magnitude bar keeps its v1 arithmetic: +20 c/market at `mk5s` deflates by the instrument's
~11× to ≈ +1.8 c/market real. ⚠ Note the standing correction from
`FINDINGS_seat_funding_20261007.md`: the live seat it would feed is **+$1.26/day at realised duty
with an all-time t of +0.87**, so a pass here is a claim about adverse selection on this
instrument, not a claim about money.

## 4. What a pass licenses

Exactly one thing: scoring the survivor on the **real print set** (`s·(P − 100y)`, which needs no
mid, no queue and no fill model). It licenses no live arming. The `mk5s` instrument overstates
adverse selection **5.17×** against 8,038,551 real prints and that is unchanged by anything in v2.

## 5. Amendments

Numbered here with date and whether made before or after the test fold was read. An amendment made
after a test score cannot convert a FAIL into a PASS.

**Amendment 4 — 2026-10-07, AFTER the mk5s test score, BEFORE the mk60s run. The secondary mark is
scored with a calibration offset, and the leakage this carries is disclosed.**

The mk5s scored run (FAIL, 3/7) established that this critic's **ranking is correct and monotone**
across spread bands — seed sd 0.09 c, perfectly ordered — while its **zero sits ~0.5 c too high**,
so it posted at ~11 ticks when the edge turns positive at 4. A constant bias is corrected by one
threshold. The mk60s secondary (Amendment 1) is therefore scored with the decision rule

    post iff  Q(POST) − Q(SKIP) > τ,   τ swept on VAL over the grid in `rl/tau_ceiling.py`

**Disclosed leakage, stated plainly.** The τ *value* is fitted on val only. But the *decision to
use a τ at all* was learned from the mk5s **test** fold, and the mk60s test fold is the **same 480
markets relabelled**. So this is not a clean independent test: a structural fact about the critic
(a calibration bias exists) was read off the test markets before this run. I judge the mechanism
structural rather than market-specific — it is a property of an abstain-vs-noisy-action value
comparison, not of these 480 markets — but the claim is weaker than a first look and is reported as
such.

Consequently **the mk60s result cannot pass this pre-registration on its own** under Amendment 1's
rule, and with Amendment 4's leakage it cannot pass at all. It is scored to answer one question —
is the 60-second markout a materially different problem from the 5-second one — and its verdict
is reported as `INFORMATIVE, NOT A PASS` regardless of sign. A positive result opens a new prereg
on fresh tape; it does not reopen this one.

**Amendment 2 — 2026-10-07, BEFORE any val or test score of a learned policy exists. Selection
moves to the paired difference, and a disclosure.**

While validating the new `live` rule (see Amendment 3) I measured the **ungated** `always` arm on
all three folds of `mk5s-v4`:

| fold | markets | ungated c/ct | ungated c/market | t |
| --- | --- | --- | --- | --- |
| train | 1,152 | −0.0735 | −15.15 ± 3.00 | −5.05 |
| **val** | 288 | **+0.0770** | **+13.89 ± 5.15** | **+2.70** |
| test | 480 | −0.0546 | −7.75 ± 3.37 | −2.30 |

**Disclosed:** that includes the test fold. It is a fixed property of the instrument, not a tunable
arm, and **no selection was made on it** — but I looked at it, so it is recorded here rather than
omitted.

**The val fold has the opposite sign to both train and test.** The chronological split happened to
place a profitable stretch in val, so *absolute* val cents are contaminated by fold-level drift
that no policy controls — and selecting a checkpoint on them is a good way to reproduce v1's
19.8 c/market val→test collapse. Selection therefore moves to the **paired difference against
`room4` on val**, which is computed on identical rows so the drift cancels, and which is the same
statistic every gate is written on. Frozen before any learned policy was scored on either fold.

**Amendment 3 — 2026-10-07, BEFORE any val or test score. The fill model's liveness rule was a
collector artifact, and it is corrected.** `features.market_rows` decided "our quote was still
resting when the print landed" as `i == j` on the **book index** — no update of any kind in the
11 ms lag window. That measures how aggressively the feed coalesces, not whether the order
survived: it passes **70.30%** of prints on the throttled box tape (11.7 updates/s) and **1.64%**
on the full-fidelity gate-probe tape (p90 100.1 updates/s), same venue, same series, same
instrument. Adding 68 new tapes yielded **4 extra markets**, with sixty `45 markets (0 with rows)`
lines and no error. `LIVE_RULE = "touch"` now tests whether the **touch price** our quote was built
on is unchanged, which is what the venue actually cancels on (pass rate 83.77% / 60.73%).

Consequences, all stated before scoring:
* the dataset goes from 558 to **1,920 markets** with a **480-market** test fold;
* the instrument moves materially toward the real-print ledger — the ungated per-contract level
  goes **−0.1321 → −0.0480 c/ct** against 8.04M real prints' **+0.254**, i.e. 2.75× closer but
  **still the wrong sign**, so the known adverse-selection overstatement is reduced, not removed;
* `room4`'s marginal value over `always` collapses from **+91.7** to **+8.4 c/market** on val, so
  v2 has far less headroom to win in than v1 appeared to;
* **absolute levels are no longer comparable to v1's.** Paired differences remain the unit, and
  G2's +20 c/market bar is therefore restated per contract: `room4` took 179 fills/market in v1, so
  the bar is **+0.112 c/ct**, which is scale-free and survives both the live-rule change and the
  per-market entry cap.

**Amendment 1 — 2026-10-07, BEFORE any val or test number on `mk5s-v4` exists. A declared
secondary mark.** The corpus's standing instruction when a model collapses to one feature is that
"the question is the LABEL or the instrument, not the inputs" — and v1 collapsed to a width gate.
The 60-second markout is the label change that was previously unaffordable: `ml/README.md` priced
it at ~2,104 markets against mk5s's 151, and every run in this corpus has been stuck at mk5s on
558. **`mk5s-v4` has ~3,000 markets, so mk60s is powered for the first time.** Measured per-market
sd on the existing columns: mk5s **178.5 c**, mk60s **319.8 c**, settle 365.0 c — so mk60s needs
3.2× the markets of mk5s and v4 supplies 5.4×. Window-clipping (a markout whose horizon runs past
the end of its hourly tape, which `_at()` silently absorbs by clipping the index) affects **1.60%**
of rows, under the 10% guard `train2.py` now enforces.

Therefore:
* **Primary mark stays `mk5s`.** The seven gates and the `room4` benchmark are defined on it, and
  keeping it primary is what makes v2 a controlled comparison against v1's −9.005 rather than a
  new experiment with a new yardstick.
* **`mk60s` is a declared secondary**, scored once, with its own full arm table and gate set.
* **Multiplicity is 2 and is stated in the result.** A pass on the secondary alone does **not**
  pass this pre-registration — it is reported as `REQUIRES REPLICATION` and needs its own prereg
  with mk60s primary. A pass on both is a pass.
* `settle` is **not** scored. It remains underpowered and is the mark that only a real-print ledger
  should settle.
