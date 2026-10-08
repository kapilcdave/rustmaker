# FINDINGS — RLMM v3: fixing the instrument flipped the sign, and the branch still closes

Pre-registration `PREREG_rlmm_v3_20261008.md`. Scored run `ml/runs/rlmm-v3-mk5s-scored/report.json`,
checkpoint `ml/runs/rlmm-v3-mk5s/c51v2.pt`. Dataset `ml/data/mk5s-v5` (1,921 markets, 481-market
test fold), built after both fill-model bugs were fixed.
**VERDICT: FAIL, 4 of 7 gates.**

## The scored result

| arm | c/market | SE | t | fills |
| --- | --- | --- | --- | --- |
| always | −1.521 | 2.016 | −0.75 | 48,157 |
| detgate | +4.766 | 1.598 | +2.98 | 32,951 |
| room4 | +6.709 | 0.993 | +6.75 | 12,594 |
| logit | +4.874 | 1.023 | +4.76 | 5,000 |
| glft | +3.303 | 0.841 | +3.93 | 1,357 |
| **c51v3 (τ=−0.20)** | **+7.852** | 1.376 | +5.71 | 22,461 |
| oracle_entry | +96.343 | 4.716 | +20.43 | 23,563 |

| comparison | diff | SE | t | share+ |
| --- | --- | --- | --- | --- |
| **c51v3 − room4** | **+1.142** | 0.867 | **+1.32** | 0.484 |
| **c51v3 − glft** | **+4.549** | 0.963 | **+4.72** | 0.601 |
| **c51v3 − logit** | **+2.978** | 0.928 | **+3.21** | 0.557 |
| logit − room4 | −1.835 | 0.575 | −3.19 | 0.362 |
| always − room4 | −8.230 | 1.712 | −4.81 | 0.374 |

G1 ✗ (t 1.32) · G2 ✗ (+1.142 vs a +9.0 bar) · **G3 ✓** · G4 ✓ · G5 ✗ (48.4% positive) ·
G6 ✓ · G7 ✓

## 1. The headline across four scored runs: the instrument was the story

| run | instrument | paired `c51 − room4` | t |
| --- | --- | --- | --- |
| v1 | v3 — throttled `live` rule, sweeps duplicated | **−9.005 ± 4.343** | −2.07 |
| v2 (mk5s) | v4 — `live` rule fixed | **−5.498 ± 1.252** | −4.39 |
| v2 (mk60s) | v4 | −2.558 ± 4.721 | −0.54 |
| **v3 (mk5s)** | **v5 — `live` rule + sweep dedupe** | **+1.142 ± 0.867** | **+1.32** |

**Fixing two bugs in the fill model moved this comparison by 10.1 c/market and flipped its sign.**
Nothing about the agent changed between v2 and v3 — same architecture, same features, same
hyperparameters, same selection rule. The entire earlier conclusion ("a large policy class loses to
one line of code") was a property of a broken instrument.

And it beats the other two learned/closed-form arms decisively on the corrected data:
**+4.549 (t 4.72) over GLFT** and **+2.978 (t 3.21) over the 30-feature logistic**, both with
breadth above 55%. The C51 critic is now the best gate on the table. It just isn't a good enough
one.

## 2. Why it still fails, and the arithmetic is exact

| fill set | rows | c/ct | mean spread |
| --- | --- | --- | --- |
| both | 12,101 | +0.259 | 6.52 |
| **only c51v3** | **10,360** | **+0.062** | **2.66** |
| only room4 | 493 | +0.182 | 5.67 |

The agent now takes essentially all of `room4`'s set (only 493 rows declined) **plus 10,360 extra
narrow-book fills that are genuinely, if barely, profitable** at +0.062 c/ct. That is the whole
edge: 10,360 × 0.062 = +642 c over 481 markets = **+1.33 c/market**, which is the +1.142 observed.

So the learned policy's contribution is correctly identifying that **narrow books are marginally
positive on a correctly measured instrument** — the opposite of what every width-gated result in
this corpus assumed, and only visible once the sweep over-count stopped loading the narrow-spread
cell with duplicated adverse fills. It is a real finding and it is worth **+1.1 c/market** against
a **+9.0** bar.

**Val → test swing is 4.85 c** (val +5.992 ± 1.455 t 4.12 → test +1.142 ± 0.867 t 1.32). Better
than v1's 19.8 and v2-mk60s's 14.6, and the same mechanism as the latter: the agent posts **46.6%**
of offered fills against `room4`'s 26%, so the turnover mismatch leaves it exposed to fold drift
that pairing against a fixed-turnover benchmark cannot cancel. The τ sweep was at least a broad
plateau this time (τ ∈ [−0.50, 0.00] all read +4.35 to +5.99 on val at t 2.5–4.1), not a spike.

## 3. What the corrected instrument says about everything else

Measured on the **val** fold of v5, with headroom defined as `oracle_entry − always`:

| arm | c/market | share of headroom |
| --- | --- | --- |
| always | +16.373 | — |
| room4 | +19.001 | **+2.0%** |
| logit | +18.318 | +1.5% |
| **c51v3** | **+24.993** | **+6.5%** |
| oracle_entry | +149.359 | 100% |

**Two corpus-level consequences.** First, on a correctly measured instrument the *ungated*
improved-quote maker is **profitable** (+16.373, t 4.53 on val; pooled over all v5 entry rows the
ungated level is +0.1215 c/ct against the 8.04M-real-print ledger's +0.254). On the broken
instrument it read −47.75 — the wrong sign — and the whole "this seat needs a gate" framing came
from that artifact. Second, `room4` — the benchmark two preregistrations were written against —
captures **2.0%** of the available gate value. The learned critic triples that to 6.5% and still
leaves 93% on the table, so the oracle headroom is not an argument that a better model is close.

⚠ **Every `mk5s` level in this corpus is superseded**, including the numbers in
`ml/README.md` (room4 +65.7, logit +69.4, always −33.4) and in the v1/v2 findings. Paired
differences between arms on identical rows survive; levels do not.

## 4. Corrections to my own earlier claims, stated plainly

* **The mk60s feature lead is dead.** v2 §7 reported `logit − room4 = +11.948 ± 6.743` (t 1.77,
  8/9 series, drop-best-5 +6.71) at the 60-second markout and called it the live lead. After the
  sweep dedupe it is **+2.055 ± 5.527 (t 0.37, 6/9 series, drop-best-5 −1.68)** — a zero that fails
  breadth. Powering it to t = 2 would need ~13,900 markets ≈ 16 days of tape. The *negative* mk5s
  side of that reversal survived and strengthened (−1.835, t −3.19), so what remains is "features
  lose to width at 5 s", not a horizon reversal.
* **The capture decomposition is 1.65% of the variance, not an order of magnitude** (capture sd
  0.475 c vs adverse 3.665 c on v5). Its benefit is an exact edge term, not variance reduction.
* **G2's bar moved from +20 to +9.0 c/market**, re-derived for the corrected instrument before this
  run scored, and `train2.py` was checking a hardcoded 20 while the prereg said 9.0 — now fixed and
  named `G2_BAR_C`. The verdict is unaffected: +1.142 fails both.

## 5. The mk60s secondary on v5 — also FAIL, same mechanism

`c51v3 − room4 = −3.691 ± 4.000 (t = −0.92)`, FAIL. The val τ sweep selected **−0.60**, which makes
the agent post **76.4%** of offered fills against `room4`'s 26%, and val +8.425 became test −3.691 —
a 12.1 c swing by the turnover mechanism again. Unlike the mk5s sweep, the mk60s τ curve was a
**spike, not a plateau** (−0.60 → +8.425 but −0.50 → +1.995), so that selection was noise-driven
and should not have been trusted; the mk5s plateau was the credible one.

Also on this fold, `logit − room4` at mk60s reads **+2.055 ± 5.527 (t 0.37)** against val's
+12.089 ± 9.699 — confirming §4's retraction from the other direction.

## Every scored run in one table

| run | instrument | mark | paired `c51 − room4` | t | gates |
| --- | --- | --- | --- | --- | --- |
| v1 | v3 | mk5s | −9.005 ± 4.343 | −2.07 | 3/7 |
| v2 | v4 | mk5s | −5.498 ± 1.252 | −4.39 | 3/7 |
| v2 | v4 | mk60s | −2.558 ± 4.721 | −0.54 | 4/7 |
| **v3** | **v5** | **mk5s** | **+1.142 ± 0.867** | **+1.32** | **4/7** |
| v3 | v5 | mk60s | −3.691 ± 4.000 | −0.92 | 3/7 |

## 6. Where the branch stands

**Closed.** Not on model capacity — the critic is now the best gate measured on this venue, beating
GLFT by +4.5 and a 30-feature logistic by +3.0 with breadth — but on **magnitude**: +1.1 c/market
against the +9.0 that would roughly double a live seat earning
**+$1.26/day at 40.4% realised duty with an all-time t of +0.87**
(`FINDINGS_seat_funding_20261007.md`). Six dimensions are now measured and none binds: architecture,
atom resolution, risk measure, ensemble size, feature set, exploration.

**The one thing genuinely left is the instrument, and it is no longer the biggest term.** After both
fixes the simulated level is +0.1215 c/ct against the real-print +0.254 — right sign, 2.1× low. The
remaining gap is measurable on the real print set (`s·(P − 100y)`: no mid, no queue, no fill model),
and that measurement has already closed this seat on economics once: gross flat from 1c to 10c of
spread, below its own circular-shift null, and the exchange taking 85.4% of what the taker pays.
Nothing in v3 disturbs that.
