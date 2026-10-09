# PREREG — Does the 15M sim instrument understate the maker, or is the real-print ledger a
# different population?

Frozen 2026-10-09, before any outcome statistic was computed on `settle_c`. Author: Claude Opus 5.
Operator: kapilcdave. Scorer to be written AFTER this file is committed.

## 1. Why this, and why now

`FINDINGS_rlmm_holdout_20261008.md` §7 closed the RLMM branch and named exactly one surviving term:

> "the remaining term is the instrument's residual understatement against the real print ledger
> (`real-print-maker-ledger-15m-venue-normal`), not the policy."

That residual is the last thing in the 15M maker family that has never been measured. It is worth
measuring because it is the **common input to every level in the family**, including the live seat's
`$1.26/day`, the `+9.0 c/market` arming bar, and the RL agent's `+0.910 c/market`. If the sim
understates maker P&L by ~2×, every one of those scales. If it does not, the family is closed with
no hidden upside and the corpus stops carrying an open term it cannot resolve.

**The residual, stated as it currently stands:**

| instrument | maker gross, hold to settlement | source |
| --- | --- | --- |
| real prints, population resting side | **+0.254 c/ct** [+0.118, +0.386] | `kalshi-scalp/FINDINGS_real_print_ledger.md` |
| real prints, **touch** resting maker | **+0.3473 c/ct** [+0.0415, +0.6501] | `touch-resting-maker-closed-15m` |
| our sim, ungated improved-quote maker, **5 s markout** | **≈0, within ±0.13 c/ct on three windows** | `FINDINGS_rlmm_v3`, `FINDINGS_rlmm_holdout` |

The gap has been called "a 2.1× understatement". **It has never been measured on a matched
statistic**, and it cannot be, as stated, because the two rows are different horizons: one is held
to settlement, the other is a 5-second markout.

## 2. The thing that makes this cheap, and testable today

`ml/features.py:285` is

```python
e["settle_c"] = e.s * (e.px_c - 100.0 * result_yes) - MAKER_FEE_C      # MAKER_FEE_C = 0.0
```

which is **character-for-character the real-print ledger's estimator**
`maker_gross_settle = s · (P − 100·y)`, with the same sign convention (`s=+1` ⇒ the maker sold yes;
confirmed by `label = BUY_YES where s < 0` on line 290, and against
`kalshi-taker-side-convention-confirmed`). `MAKER_FEE_C = 0.0`, so `settle_c` is **gross**, directly
comparable to a published gross number with no fee adjustment.

So `ml/data/mk5s-v5/rows.parquet` **already carries, on disk, the sim's own value of the real-print
ledger's headline statistic**, on the sim's own fill set, for 275,226 fills across 1,921 markets.
Nothing needs to be collected, fetched, or rebuilt to score the primary. This study is a groupby.

**This is the matched comparison the "2.1×" claim has been asserting without ever running.** The two
instruments disagree about *which fills happen* (the sim has a fill model; the print tape does not
need one). They do **not** disagree about how to value a fill once it has happened — that is one
line of arithmetic and both use it. Therefore any residual is attributable to the **fill
population**, not the valuation, and that is a decomposable claim.

## 3. Universe, frozen

- Dataset `ml/data/mk5s-v5` (`rows.parquet`, 275,226 rows, 1,921 markets, 2026-09-23T04:10Z →
  2026-10-07T22:12Z). **A used dataset is fine here**: this study scores no model, selects no
  hyperparameter and fits nothing. There is no holdout to burn. `ml/data/holdout-01` is NOT touched.
- **Series: the 8 non-BTC crypto series**, matching the real-print ledger's universe exactly —
  KXETH15M, KXSOL15M, KXXRP15M, KXDOGE15M, KXHYPE15M, KXZEC15M, KXNEAR15M, KXBNB15M. **KXBTC15M is
  excluded** (22,904 rows) because the ledger excluded it. BTC reported separately as a descriptor
  only, never in a gate.
- **Mid band `px_c ∈ [15, 85]`**, matching the ledger's primary. 55.2% of rows pre-filter.
- All folds pooled (train+val+test). Fold structure is a model-selection device and is irrelevant to
  an instrument question; pooling is declared here so it cannot be chosen later.
- Rows are used as they sit in the parquet, i.e. **after** the build's per-market subsample
  (mean `samp_rate` 0.929) and **after** its inventory walk. Both are declared confounds, §7.

## 4. Estimator, frozen

Per-row statistic `settle_c`. Reducer: **mean of per-market means** (equal weight per market), CI by
**market-clustered bootstrap**, 10,000 resamples, seed 20261009, percentile method.

Market clustering is mandatory, not a preference: every row in a 15-minute market shares one price
path, so a row-level CI is a fiction (`gliner-directional-on-15m-is-closed-by-the-target`).

**One reducer, used for every number in the output.** The parent study published a decomposition on
a different estimator than its headline and the residual came out 2.2× the study's own effect
(`FINDINGS_real_print_ledger.md` §4b, `a-decomposition-must-use-its-headlines-reducer`). Every cell
in every table below is the same `cluster_boot` call. No `_mean()` over legs anywhere.

I will **also** report the flat print-weighted mean beside the primary, labelled as such, because
the ledger's headline is print-weighted and the two differ on real prints (+0.254 print-weighted vs
+0.580 contract-weighted). Where they disagree the **per-market** reducer governs the gates.

## 5. The two primaries

### P1 — the level, on a matched statistic and a matched horizon

`settle_c` over the universe in §3.

| outcome | meaning | verdict |
| --- | --- | --- |
| 95% CI **overlaps [+0.118, +0.386]** | the sim agrees with the real tape on the only statistic both can compute | **INSTRUMENT VINDICATED** — the residual is closed at zero; the 15M maker family has no hidden 2× and every published level stands |
| 95% CI entirely **below +0.118** | the sim values a matched fill set lower than the tape does | **RESIDUAL REAL AND SIGNED DOWN** — size it, then rescale the family's levels by the measured factor |
| 95% CI entirely **above +0.386** | the sim's fill set is *better* than the population | **SELECTION, NOT UNDERSTATEMENT** — the fill model is optimistic and the levels are too high |

### P2 — flat-in-spread, which is the sharper test and the one that can indict the gate

The ledger's strongest structural finding is that the real maker earns **the same** resting in a 1 c
book as in a 10 c book: +0.249 / +0.264 / +0.252 / +0.240 / +0.244 c/ct across tape spreads of
0-1 / 1-2 / 2-3 / 3-5 / 5-10 c. "Capture rises with spread mechanically ⇒ adverse rises in
lockstep", so **no width gate can manufacture an edge** on real prints.

Our entire gate framing — `room4`, `spread_ticks >= 4`, the RL critic, the `+9.0 c/market` bar — is
built on the sim showing a **strong** `settle_c` gradient in `spread_ticks`. Both cannot be right.

Score `settle_c` by `spread_ticks` bucket {2, 3, 4, 5-6, 7-9, 10+}, same reducer.

| outcome | verdict |
| --- | --- |
| slope's 95% CI **includes 0** | the sim reproduces flat-in-spread; the width gate is selecting *turnover*, not edge, and the gate literature in this corpus is measuring a different thing than it claims |
| slope's 95% CI **excludes 0 and is positive** | the sim manufactures a spread gradient the tape does not have ⇒ **this is the instrument bug**, localized, and it is the direct cause of the width-gate result |
| slope positive **on the tape too** (re-check §6) | the ledger's flat-in-spread is window-specific and the gate is real |

Slope = OLS of per-bucket per-market mean on bucket midpoint, CI from the same market-clustered
bootstrap (slope recomputed inside each resample).

## 6. Secondary, pre-declared, NOT gates

1. **The horizon ladder on one identical fill set** — `mk5s_c`, `mk60s_c`, `settle_c` on the same
   rows. The ledger's ladder is 90 s +0.176 → settlement +0.254 (Δ 0.078, "fully arrived by 90 s").
   If our ladder is ≈0 → ? → ?, this says whether the residual is a **horizon** artifact or a
   **level** artifact. This decides what the "2.1×" ever referred to.
2. **Per-series**, all 8. The ledger had 7 of 8 positive, HYPE −0.045. A sign agreement count is a
   cheap replication check.
3. **Per-price-band** inside the mid band, and the sub-1-contract question is **not** askable here
   (the sim has no fractional clips), so `count_fp` is declared out of scope.
4. **Window disagreement is the one confound I can actually close.** The ledger ran 2026-07-15 →
   09-14; mk5s-v5 is 09-23 → 10-07, **disjoint**. If P1 fails, the fetch of real prints for the
   mk5s-v5 window via the **free, unauthenticated** `/markets/trades` endpoint
   (`kalshi-scalp/fetch_real_print_tapes.py`, $0, no credential, no capital) is the designated
   follow-up and is pre-authorized here. Per
   `cross-window-level-agreement-tests-an-instrument-a-paired-diff-cannot`, a level comparison
   across disjoint windows is exactly what tests an instrument — but it cannot separate
   "instrument differs" from "window differs" without this arm.

## 7. Declared confounds, before seeing the answer

- **Disjoint windows** (§6.4). The largest one. The ledger's own first-half lower bound was +0.009,
  so its level is not strongly established within-window either.
- **Population ≠ population.** The ledger averages over all resting sides including two-sided
  self-liquidating makers (measured at +1.949 c/ct, ~8× the mean) and **88.4% of its contracts sit
  at prices no resting touch order held**. Our sim is a one-sided improved-quote maker. The
  ledger's own note says "a one-sided maker should expect less than +0.254" — so **a sim reading
  below +0.254 is not automatically a bug**, and P1's FAIL-LOW branch must be read against the
  touch-maker band [+0.0415, +0.6501] as well as the population band. Both are reported.
- **The subsample and the inventory walk** (§3) are selections on the sim's side that the tape does
  not have. I will report the dropped fraction and the `samp_rate` distribution so the size of this
  is visible, but I cannot undo it without a rebuild, and a rebuild is out of scope.
- **`result_yes` provenance.** `settle_c` depends on a settlement cache (`data/settle_cache.json`).
  If any market's `result_yes` is null or mis-joined, `settle_c` is silently wrong. **C1: assert 0
  nulls in `result_yes` and that its mean sits in [0.3, 0.7]**, and report the count. A failed C1
  voids the study rather than being worked around.
- **C2: the identity check.** Recompute `s*(px_c - 100*result_yes)` from raw columns and assert it
  equals the stored `settle_c` to < 1e-9 on 100% of rows. This verifies the claim in §2 rather than
  trusting my reading of `features.py`. A failed C2 voids the study.
- **C3: a null.** The ledger's +0.254 sits **below its own circular-shift null** of +0.674, i.e.
  most of the level is a static price-level effect, not conditional skill. I will run the same
  circular outcome shift (roll `result_yes` by market within series, never redraw — holds turnover
  fixed, `a-null-must-hold-turnover-fixed-or-it-is-a-cost-handicap`) at k=96 and report our level
  against our null. **A level above zero but below its own null is not an edge**, and if that is
  what we find, P1 "vindicated" must be reported with that caveat attached.

## 8. What this licenses, and what it does not

**Licenses:** a rescaling factor for the published levels in the 15M maker family, and an answer to
the one open term in `FINDINGS_rlmm_holdout_20261008.md` §7.

**Does NOT license:** any change to the live seat, any arming, any capital, any re-opening of the
RLMM branch on policy, architecture or features (closed on six measured dimensions), and no `$/day`
claim. Per `research-direction-preference`, this study exists to **close an open term durably and
cheaply**, not to resurrect a branch. A P1 vindication *closes* the family; only a FAIL-LOW with a
factor large enough to clear the `+9.0 c/market` bar would reopen anything, and the arithmetic for
that is pre-stated: the RL edge is +0.910, so **the residual factor would have to exceed 9.9× to
reach the bar**. A 2.1× factor does not reopen the branch. I am stating that now so the result
cannot be spun later.

## 9. Execution

One scorer, `research_instrument_residual.py`, written after this file is committed, run **once**,
all tables emitted in a single pass to `reports/instrument_residual.json`. No interactive
exploration of `settle_c` before the run. Findings to
`FINDINGS_instrument_residual_20261009.md`.
