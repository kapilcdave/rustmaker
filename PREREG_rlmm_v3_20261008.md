# PREREG — RLMM v3: the same agent on a CORRECTED instrument

Frozen 2026-10-08, before any val or test number on `mk5s-v5` exists. Supersedes
`PREREG_rlmm_v2_20261007.md`, whose evaluations are spent. Instrument `ml/rl/{shaped,train2}.py`
(unchanged from v2), dataset `ml/data/mk5s-v5`, scorer `ml/policy_eval.py` (unchanged).

## 0. Why this is a new pre-registration and not an amendment

Two independent bugs in the **fill model** were found and fixed after v2 was scored. Neither is a
modelling choice; both changed which fills exist:

1. **The liveness rule was a collector-staleness proxy** — `live = (i == j)` on the book index
   required *no book update at all* in the 11 ms window, which passes 70.30% of prints on a
   throttled feed and **1.64%** on a full-fidelity one. Fixed to touch-price equality.
2. **A sweep is one fill opportunity reported as many prints** — the venue stamps every level of a
   sweep with one microsecond (measured: **332 prints at a single µs** walking 39c → 15c), and the
   model counted each as a separate fill. That produced **50.6%** exact-duplicate rows in v3 and
   **35.9%** in v4, concentrated in the most adverse events on the tape. Fixed by deduping to one
   opportunity per `(vt, side)`.

Together they move the simulated instrument's ungated per-contract level, against the
8.04M-real-print ledger's **+0.254 c/ct**:

| dataset | ungated c/ct | |
| --- | --- | --- |
| v3 | −0.1321 | wrong sign |
| v4 (fix 1) | −0.0480 | wrong sign |
| **v5 (fixes 1+2)** | **+0.1215** | **right sign, 2.1× low** |

**So every level in every earlier RLMM finding is superseded**, including `room4`'s. Paired
differences between arms on identical rows survive, but the arms themselves now select different
fills, so v1's −9.005 and v2's −5.498 are not comparable to what this run produces.

## 1. The reason to run it at all: the gate opportunity is almost entirely unexploited

Measured on the **val** fold of `mk5s-v5` (selection fold, no test rows), defining headroom as
`oracle_entry − always`:

| arm | c/market | t | fills | share of headroom |
| --- | --- | --- | --- | --- |
| always | +16.373 | 4.53 | 39,013 | — |
| detgate | +15.526 | 5.52 | 27,172 | **−0.6%** |
| room4 | +19.001 | 9.50 | 11,816 | **+2.0%** |
| logit (30 feat) | +18.318 | 8.98 | 7,400 | +1.5% |
| **oracle_entry** | **+149.359** | 19.47 | 20,134 | 100% |

**Two facts worth stating before the run.** First, on a correctly measured instrument the *ungated*
improved-quote maker is solidly profitable (+16.373, t = 4.53) — on v3's broken instrument it read
−47.75, which is the wrong sign, and the entire "a gate is what this seat needs" framing came from
that artifact. Second, `room4` — the rule this corpus has treated as the benchmark to beat for two
preregistrations — captures **2.0% of the available gate value**. 98% of a +133 c/market headroom is
untouched. That is the largest unexploited gap this branch has ever had a clean measurement of, and
it is the only reason to spend another run.

## 2. Frozen configuration

Identical to v2 and unchanged: `GAMMA_RL=0.99`, `N_STEP=3`, `LR=3e-4`, `BATCH=512`, `HIDDEN=128`,
`N_ATOMS_V2=201`, `SUPPORT_Q=(0.01,0.99)`, `TARGET_SYNC=250`, `WARMUP=4000`,
`GRAD_STEPS_PER_ITER=8`, `MARKETS_PER_ITER=6`, `COVER_PER_ITER=3`, `BUFFER=400_000`, PER
`alpha=0.6 beta=0.4`, noisy `sigma0=0.5`, BOCPD `HAZARD=1/50 R_MAX=200`, EXP3 `gamma=0.15`,
`POS_CAP=10`, `CLIP_CT=1`, `LAG_US=11_000`, `MAX_ENTRIES_PER_MARKET=400`, `SMOOTH=3`, `--seeds 3`,
`--iters 2000 --eval-every 250`.

Selected on **val only**: CVaR level `risk ∈ {1.0, 0.5, 0.25, 0.1}`, the smoothed checkpoint, the
calibration offset `τ` (grid in `rl/tau_ceiling.py`), and GLFT's `gamma`. Selection is on the
**paired difference against `room4`** (v2 Amendment 2).

⚠ **Carried-over selection hazard, disclosed.** `τ` as a mechanism was learned from the v2 **test**
fold, and v5's test fold is the same calendar period relabelled. The τ *value* is fitted on val
here. This makes the τ arm weaker than a first look; the τ = 0 arm is reported alongside it so the
reader can take the clean number.

## 3. Decision rule

**Primary: mk5s.** Seven gates, unchanged in form from v2:

G1 paired `c51v3 − room4` > 0 at **t ≥ 2** · G3 beats GLFT · G4 Jaccard < 0.9 with non-empty
disagreement · G5 ≥ 55% of markets positive and survives drop-best-5 · G6 posts ≥ 2% of offered
fills · G7 above a turnover-matched circular-shift null's hi95.

**G2 is re-derived for this instrument, because the v2 bar was computed on a broken one.** The
economic anchor is unchanged: the live seat earns **+$1.26/day at realised duty with an all-time t
of +0.87** (`FINDINGS_seat_funding_20261007.md`), and a gate is worth building only if it roughly
doubles that. On v5 the ungated arm takes 135 fills/market, and the instrument understates the real
per-contract level by ~2.1×, so a paired gain of **X c/market** is worth about
`X / 135 × 2.1` c/ct of real edge against the seat's all-time **+0.134 c/ct**. Doubling it needs
**X ≈ +8.6 c/market**. Rounded and frozen:

> **G2: paired `c51v3 − room4` ≥ +9.0 c/market**, which is **6.8% of the +133 c/market headroom**.

This is a *lower* bar than v2's +20 and it is lower for a stated reason — the instrument changed and
the old bar was derived from its inflated levels, not because a run failed. Recorded here before any
v5 score exists.

**Secondary: mk60s**, declared and reported with its own gate table, `INFORMATIVE, NOT A PASS`
(same rule as v2 Amendment 1). ⚠ Note the mk60s *feature* lead from v2 §7 did **not** survive the
dedupe: `logit − room4` went from +11.948 ± 6.743 (t 1.77, 8/9 series, drop-best-5 +6.71) on v4 to
**+2.055 ± 5.527 (t 0.37, 6/9 series, drop-best-5 −1.68)** on v5. Powering that to t = 2 would need
~13,900 markets ≈ 16 days of tape. **It is treated as dead**, and the mk5s side of the reversal is
what survived (−1.835, t −3.19).

## 4. What a pass licenses

Scoring the survivor on the **real print set** (`s·(P − 100y)`: no mid, no queue, no fill model).
No live arming. The instrument is 2.1× low against the real-print ledger even after both fixes, and
that residual is now the largest known error in the chain.

## 5. Amendments

Numbered here with date and whether made before or after the test fold was read.
