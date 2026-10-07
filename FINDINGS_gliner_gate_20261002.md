# FINDINGS — GLiNER2.5 gate on the 15M entry decision (2026-10-02)

Scored against `PREREG_gliner_gate.md`, primary mark `mk5s`, dataset `ml/data/mk5s-v1`
(558 markets from two non-overlapping crypto probe tapes; 334 train / 84 val / 140 test,
247,575 entry rows). Adapter: `gliner2.5-small-v1` + LoRA r=8, **1 epoch** (see caveat).

## Arm table, 140 held-out markets, cents per market

| arm | c/market | SE | t | entries taken | c per entry |
| --- | --- | --- | --- | --- | --- |
| always | −33.40 | 18.27 | −1.83 | 83,191 | −0.056 |
| detgate | −11.70 | 10.59 | −1.10 | 54,873 | −0.030 |
| logit | **+69.41** | 15.01 | +4.62 | 9,777 | **+0.994** |
| gliner | **+62.09** | 12.97 | +4.79 | 21,107 | +0.412 |
| oracle_entry | +505.83 | 46.04 | +10.99 | 38,485 | +1.841 |

Paired across the same markets, which is the test that matters:

| contrast | diff | SE | t |
| --- | --- | --- | --- |
| gliner − logit | **−7.33** | 7.55 | **−0.97** |
| gliner − always | +95.49 | 11.90 | +8.03 |
| logit − always | +102.82 | 12.43 | +8.27 |

Row accuracy: always .463, detgate .488, logit .575, **gliner .601**.

## Verdict: FAILS the preregistered bar

The bar required the adapter to beat `logit` **at all**. It does not: the paired difference is
−7.33 ± 7.55 (t = −0.97). Statistically that is a tie, not a loss — but a tie is not a win, and the
PREREG's reason for that clause stands: a 74M text encoder fed a rendered numeric snapshot had every
chance to find structure a 14-feature multinomial logistic cannot, and it found none.
**The serialization is not adding information.** Per the stopping rule this closes the branch for
this serialization; a different one is a new PREREG, not a re-score.

What it did clear: both learned arms beat the ungated quoter by ~+100 c/market at t ≈ 8, and the
adapter is the more *accurate* classifier (.601 vs .575) while being the weaker *policy*. That gap is
the finding — accuracy and P&L rank the two arms in opposite orders.

## The one genuinely new thing: capacity vs edge

The two arms reach the same total P&L through opposite routes:

- `logit` takes 9,777 entries at **+0.994 c each**
- `gliner` takes 21,107 entries at **+0.412 c each**

2.2x the volume at 0.41x the per-entry edge, landing within noise of the same number. If the binding
constraint were ever capacity rather than edge — a bigger book, or a seat that must quote — the
adapter would be the better selector at equal total. On this book it is not, because nothing forces
us to take 21,107 fills. Worth keeping in mind as a shape, not as a result.

## Caveats, stated rather than buried

1. **1 epoch, not the 2 specified.** CPU training ran at 8.14 s/step (e2-standard-16, 7.9 samples/s,
   barely better than the Mac's 5.7); the free-tier billing account cannot attach a GPU, and the run
   could not finish inside its cost deadline. The epoch-1 checkpoint was salvaged off the VM and
   scored. Epoch 1: train loss 0.2066, eval loss 0.2685. A second epoch could move this, but it
   would have to move it +7.3 c/market to clear the bar, and the eval loss curve gives no reason to
   expect that.
2. **Both learned arms capture only ~13% of `oracle_entry`** (+62/+69 of +506). The headroom is real
   and neither arm is near it.
3. **The jump from the 117-market table is unreproduced.** At 117 markets `logit` was +51 ± 123; at
   558 it is +69 ± 15. The tapes are three days apart. The collection now running on the box is what
   settles whether this survives on fresh markets.
4. `mk5s` is an adverse-selection metric, not money. Per the PREREG these numbers may not be
   reported as P&L, and the settlement arm needs ~550 h of tape.

## Cost

Four VM launches, three of which died in minutes on environment defects (missing GPU entitlement,
stale image family, no bucket IAM, no pip/conda/torch on the image), plus ~5 h of e2-standard-16.
Under $6 of credits, no GPU hours.
