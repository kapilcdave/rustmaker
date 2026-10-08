# FINDINGS — the fresh tape: +1.142 replicates, and two of last night's claims do not

**VERDICT: the agent result REPLICATES out of sample. `c51v3 − room4 = +0.910 ± 0.887` (t 1.03) on
422 never-read markets, against `+1.142 ± 0.867` (t 1.32) on v5's test fold.** Per
`PREREG_rlmm_holdout_20261008.md`'s decision rule (`D ≥ +0.5`), the sign and rough magnitude hold,
the claim "the C51 critic is the best gate on the table" survives, and the branch stays closed on
magnitude — the 95% upper bound is **+2.65** against a **+9.0** bar.

Two *other* claims from `FINDINGS_rlmm_v3_20261008.md` do not survive the fresh tape, and both were
in the "corpus-level consequences" section. They are corrected in §4.

Instrument: `ml/rl/score_holdout_agent.py`, dataset `ml/data/holdout-01` (19 `probe_gate` tapes,
2026-10-07 22:15Z → 10-08 17:15Z, 422 markets, 47,510 entry rows, 0 skipped), frozen checkpoint
`ml/runs/rlmm-v3-mk5s/c51v2.pt` at `risk 1.0`, `tau −0.20`, `glft_gamma 0.0003`.

## 1. The reproduction arm, which is why any of this is readable

The frozen config re-scored on v5's test fold returned `c51v3 − room4 = +1.142 ± 0.867`, **0.0000
c/market** off the published number. The scorer is the scorer that produced v3, so a null on the
holdout would have been attributable (`an-oos-test-needs-a-reproduction-arm`). The contamination
gate passed first: **0 shared tapes, 0 shared markets** out of 62/19 and 1,921/422.

## 2. Every number, both windows

| arm | v5 test (481 mkts) | holdout (422 mkts) | Δ |
| --- | --- | --- | --- |
| always (ungated) | −1.521 | **−3.727** | −2.21 |
| detgate | +4.766 | +0.557 | −4.21 |
| room4 | +6.709 | +5.490 | −1.22 |
| logit | +4.874 | +3.845 | −1.03 |
| glft | +3.303 | +1.582 | −1.72 |
| **c51v3** | **+7.852** | **+6.400** | −1.45 |
| oracle_entry | +96.343 | +114.083 | +17.74 |

| comparison | v5 test | holdout | reads |
| --- | --- | --- | --- |
| **c51v3 − room4** | **+1.142** ± 0.867 (t 1.32) | **+0.910** ± 0.887 (t **1.03**) | **replicates** |
| **c51v3 − glft** | +4.549 ± 0.963 (t 4.72) | **+4.818** ± 1.003 (t **4.80**) | **replicates, strong** |
| **c51v3 − logit** | +2.978 ± 0.928 (t 3.21) | **+2.555** ± 1.050 (t **2.43**) | **replicates** |
| logit − room4 | −1.835 ± 0.575 (t −3.19) | **−1.645** ± 0.703 (t −2.34) | **replicates** |
| always − room4 | −8.230 ± 1.712 (t −4.81) | −9.217 | replicates |

**⚑ The corrected instrument's LEVELS now transfer across disjoint tape windows.** `room4` reads
+6.709 and +5.490 on two non-overlapping windows; `c51v3` +7.852 and +6.400. Under the broken
instrument `room4` read **+65.7** on the same kind of tape. Cross-window level agreement to ~1
c/market is independent evidence the two fill-model fixes were real, and it is a stronger check than
the paired difference, which cancels shared error by construction. Note that
`a-conditioned-cost-cheaper-than-unconditional-is-a-broken-statistic`'s failure mode is absent here:
every gated arm sits above `always` on both windows.

Power was declared before reading: at 422 markets `SE ≈ 0.93`, so a true +1.142 gives an expected
`t` of 1.23 and **G1's `t ≥ 2` was unreachable**. It came in at 1.03. Nothing here is a
confirmation at significance, and the prereg forbade calling it one.

## 3. The replication includes replicating the weakness

`c51v3 − room4` on the holdout: **share+ 0.502**, **drop-best-5 −0.12**, early half **−0.083**
(t −0.06) against late **+1.903** (t 1.80), **4 of 9 series positive**. v5's test fold failed the
same gate (G5, 48.4% positive). So the +0.9 is not a durable per-market edge — it is a thin pooled
excess carried by a minority of markets, and it dies under an absolute trim. The two largest
per-series numbers are on n=4 (ZEC +9.5) and n=8 (BNB −2.6) and should be ignored
(`a-pooled-statistic-can-move-against-every-component`).

Turnover is unchanged in character: the agent posts **21,024 of 47,510** offered fills (44.3%)
against `room4`'s 12,041 (25.3%), so `pairing-against-a-fixed-turnover-benchmark-does-not-remove-fold-drift`
still applies to the comparison.

## 4. Two corrections to last night's findings

**(a) "On a correctly measured instrument the UNGATED improved-quote maker is profitable" — NOT
REPLICATED. Downgrade it.** v3 rested that on val (+16.373, t 4.53) and a pooled `+0.1215 c/ct`.
On fresh tape the ungated level is **−0.0331 c/ct** and `always` is **−3.727 c/market**; v5's own
test fold already read `always = −1.521`. The defensible claim is narrower and still worth the
night it cost: **the ungated level is approximately zero — within ±0.13 c/ct of it on three
windows — not the −47.75 c/market / −0.1321 c/ct the broken instrument reported.** The magnitude of
the instrument error stands; the *sign* claim about the ungated seat does not. The "this seat needs
a gate" framing is not restored either — a seat whose ungated level straddles zero is not a seat
with a gate-sized hole in it.

**(b) "`room4` captures 2.0% of the oracle headroom and the critic triples it to 6.5%" — a VAL-FOLD
artifact.** Recomputed as `(arm − always) / (oracle − always)` on each window:

| window | room4 | c51v3 | ratio |
| --- | --- | --- | --- |
| v5 val (as published) | 2.0% | 6.5% | **3.25×** |
| v5 test | 8.41% | 9.58% | 1.14× |
| **holdout** | **7.82%** | **8.60%** | **1.10×** |

Out of sample the critic adds ~10% *relative* to the one-line rule's headroom capture, not 200%.
The surviving part of the claim is the part that matters: **both arms leave >90% of the oracle
headroom on the table, so headroom is not evidence that a better model is close** — which is the
point `oracle-headroom-can-be-a-feature-problem-not-a-capacity-problem` already makes.

## 5. The horizon prereg is closed on fresh tape: NOT CONFIRMED, 1 of 6

`ml/rl/score_holdout.py`, the two-sided sign test written before the dedupe fix, scored once:

| mark | `logit − room4` | t | prereg expected |
| --- | --- | --- | --- |
| mk60s | **−6.101 ± 9.140** | −0.67 | > 0 |
| mk5s | **−1.309 ± 0.626** | −2.09 | < 0 ✓ |

reversal −4.792 against `2·se_combined` = 18.323. Only H3 passes. **The mk60s point estimate is
NEGATIVE on fresh tape** — the original lead was `+11.948 ± 6.743` (8/9 series) before dedupe and
`+2.055 ± 5.527` after, and it is now `−6.101` on tape that never touched the hypothesis. The
retraction is confirmed in sign, not merely in significance, and the `mk60s` SE of **9.14 on 422
markets** is consistent with the ~13,900-market requirement that killed it. What remains is the
mk5s side: **features lose to width at a 5-second markout**, −1.645 ± 0.703 here, −1.835 ± 0.575 on
v5. Three windows, same sign.

(The two runs report slightly different `logit` levels — +3.845 vs +4.180 — because
`score_holdout_agent.py` fits the logit on v5's **train fold**, to match v3's benchmark exactly,
while `score_holdout.py` fits it on **all of dev** as its own prereg specified. Both are declared;
neither is a bug.)

## 6. Declared defect in the window

Two of the 19 tapes are degenerate: `tape_1791443703553` (10-08 07:15Z) yielded **0 markets** and
`tape_1791447311794` (08:15Z) yielded **8**. The holdout is 19 hours of wall-clock with a ~2 h
collector gap in it, not a contiguous 19 hours. Declared in the prereg before scoring
(`a-truncated-universe-is-time-biased-not-smaller`).

## 7. Where this leaves the branch

**Closed, unchanged, and now closed on out-of-sample evidence rather than on one fold.** The agent
beats GLFT and a 30-feature logistic on never-seen tape at t 4.80 and t 2.43, and beats the one-line
width gate by +0.910 ± 0.887 — real, small, not durable per-market, and an order of magnitude under
the +9.0 bar that would make it worth arming. Do not reopen on architecture, atoms, risk measure,
ensemble size, features, exploration or more tape of this kind: the remaining term is the
instrument's residual understatement against the real print ledger
(`real-print-maker-ledger-15m-venue-normal`), not the policy.

The fresh tape is spent. `ml/data/holdout-01` is now a used holdout and must not be scored again.
