# PREREG — spending the fresh tape: does the corrected instrument's +1.142 survive OOS?

Written **before `ml/data/holdout-01` is scored by anything**. The dataset was built first (so `n`
is a design fact, declared below), but no arm, no P&L and no label distribution has been read.

## Why this is worth fresh tape

`FINDINGS_rlmm_v3_20261008.md` reports `c51v3 − room4 = +1.142 ± 0.867` on the 481-market test
fold of `mk5s-v5`. That fold is held out from *training*, but **not** from *instrument
development*: two fill-model bugs were found and fixed against that same tape pool
([[a-fill-models-live-condition-was-a-collector-staleness-proxy]],
[[a-sweep-is-one-fill-opportunity-reported-as-many-prints]]), and fixing them moved the headline
by 10.1 c/market. An instrument repaired while looking at a pool, then scored on a fold of that
pool, is exactly the shape this corpus has been fooled by before
(`in-sample-controls-cannot-detect-selection`).

So the question the fresh tape buys is **not** "is the branch open" — the branch is closed on
magnitude and §Power below shows this run is arithmetically incapable of reopening it. The question
is narrower and it decides a corpus-level claim: **is the +1.142 real at all, or is it fold-specific
like the mk60s lead that was retracted the same night?** If it does not survive, the claim "the C51
critic is the best gate on the table" has to come out of `ml/README.md` too.

## The holdout

`ml/data/holdout-01`: **422 markets, 47,510 entry rows, 19 tapes, 0 skipped**. Collected
2026-10-07 22:15Z → 2026-10-08 17:15Z, strictly after `mk5s-v5`'s newest tape (10-07 21:14Z), by
the same `probe_gate` collector that produced 60 of v5's 62 tapes. Never read by any run.

**Declared defect in the window.** Two of the 19 tapes are degenerate: `tape_1791443703553`
(10-08 07:15Z) yielded **0 markets** and `tape_1791447311794` (08:15Z) yielded **8**. The window is
therefore not a contiguous 19 hours — there is a ~2 h collector gap in it. Per
`a-truncated-universe-is-time-biased-not-smaller` this is reported as a gap in the universe, not
absorbed into `n`.

## Frozen before the holdout is read

Nothing is selected on the holdout. Every number below comes from `ml/runs/rlmm-v3-mk5s/report.json`
(`val_selection`), fitted on **v5's val fold**:

| knob | frozen value | fitted on |
| --- | --- | --- |
| checkpoint | `ml/runs/rlmm-v3-mk5s/c51v2.pt` (iter 500, 3 seeds) | v5 val |
| `risk` | **1.0** | v5 val |
| `tau` | **−0.20** | v5 val |
| `glft_gamma` | **0.0003** | v5 val |
| normalizer `mu`, `sd` | from the checkpoint | v5 train |
| C51 support | `vmin`/`vmax` from the checkpoint | v5 train |
| `room4` | `spread_ticks >= 4`, one line | nothing, frozen rule |
| `logit` | 30 features, fit on **v5's train fold** | v5 train |

`logit` is fit on v5's train fold and not on all of v5, because v3's `+2.978` comparison used
exactly that fit; widening it would change the benchmark and break the reproduction arm.

## The reproduction arm is mandatory

Per `an-oos-test-needs-a-reproduction-arm`: the identical frozen config is re-scored on **v5's test
fold** in the same invocation, before the holdout is touched.

> **If the reproduction arm does not return `c51v3 − room4 = +1.142` to within ±0.05 c/market, the
> run is VOID** and the holdout number is not interpretable — any null would be unattributable
> between "the effect is fold-specific" and "the scorer I just wrote is not the scorer that produced
> v3".

## Power, declared before reading

v3: `+1.142 ± 0.867` on 481 markets. Scaling to 422 markets, `SE ≈ 0.867 × √(481/422) = 0.926`.

- If the true effect is exactly +1.142, the **expected `t` is 1.23**. G1 (`t ≥ 2`) is therefore
  **unreachable** unless the point estimate lands ≥ +1.85, i.e. 60% above v3's.
- The 95% upper bound on a +1.142 estimate is **+2.96 c/market**, against G2's **+9.0** bar. **No
  outcome of this run can clear G2.** The branch cannot reopen here; it can only lose a claim.

This is preregistered as a **sign-and-bound** test, not a confirmation test. Calling it "confirmed"
on a `t` of 1.2 would be the error `apparent-edge-monotone-in-sample-size-is-noise` warns about.

## Decision rule, three ways, declared now

Let `D = c51v3 − room4` on the holdout, with the reproduction arm clean.

| outcome | reading | corpus action |
| --- | --- | --- |
| `D ≥ +0.5` | the sign and rough magnitude replicate on never-seen tape | keep "the critic is the best gate on the table"; branch stays closed on magnitude |
| `−0.5 < D < +0.5` | a zero at this resolution | downgrade the claim to "indistinguishable from `room4`"; the +1.142 is not a usable edge |
| `D ≤ −0.5` | does not survive OOS | **retract the +1.142** as fold-specific, exactly as the mk60s lead was retracted |

Secondary, reported but not gating: `c51v3 − glft` (v3: +4.549) and `c51v3 − logit` (v3: +2.978).
A sign flip in *those* would be a stronger retraction than a flip in `D`, because they are the
claims that made the critic "best on the table".

Breadth is reported on the same splits v3 used: calendar halves, per-series, share of markets
positive, drop-best-5. Breadth is descriptive here — at 422 markets split 9 ways no per-series cell
is powered, and `a-pooled-statistic-can-move-against-every-component` applies.

## The other prereg this tape closes

`ml/rl/score_holdout.py` holds a two-sided sign test written before the dedupe fix: `mk60s`
`logit − room4 > 0` **and** `mk5s` `logit − room4 < 0` on identical rows. Its hypothesis was
**retracted** on 2026-10-08 — deduped, the mk60s effect is `+2.055 ± 5.527` (t 0.37) and needs
~13,900 markets to power. At 422 markets it is short by a factor of 33.

I am running it anyway, once, to close the prereg on the record rather than abandon it, and its
expected verdict is declared here as **NOT CONFIRMED**. If it comes back confirmed at `t ≥ 2` on
422 markets, that is evidence of a *bug*, not of a horizon effect, and will be treated as such.

## Commands

```
.venv/bin/python ml/rl/score_holdout_agent.py ml/data/mk5s-v5 ml/data/holdout-01 \
    --load ml/runs/rlmm-v3-mk5s --tau -0.20
.venv/bin/python ml/rl/score_holdout.py ml/data/mk5s-v5 ml/data/holdout-01
```

Both gate on contamination first: the holdout must share **no tape and no market** with the dev set,
or they exit non-zero.
