# PREREG — GLiNER2.5 gate on the 15M quoter (written 2026-10-02, before any adapter exists)

## Claim under test

A LoRA-fine-tuned GLiNER2.5 classifier, reading a serialized book snapshot, gates the penny-jump
quoter's entries and exits better than the deterministic toxicity rule already in `penny.py`.

## Unit and metric

Cents per **market**, lot-wise, on the chronologically last fold of markets (`ml/policy_eval.py`).
Entries are charged the maker fee (0 for 15M crypto), exits the Kalshi taker fee
`ceil(0.07·p·(1−p)·100)`. SE is across markets; a market is one unit of evidence because every fill
in it shares one settlement.

## Arms, all reported together

1. `always` — post the improved quote at every chance, never exit.
2. `detgate` — `penny.py`'s rule: pull on 1 s momentum > 0.25 c against us, or touch imbalance
   > 0.9213 on the heavy side.
3. `bracket_2_3` — a hand-written +2 c / −3 c unrealized bracket, the thing a trader writes first.
4. `logit` — multinomial logistic on the identical numeric rows (the reproduction arm: without it an
   OOS null is unattributable between model, features and labels).
5. `oracle_entry` / `oracle_exit` / `oracle_both` — the true labels, as ceilings.
6. `gliner` — the adapter.

## Amendment, 2026-10-02 (before any adapter was scored)

The exit half of the action set is **descoped to a control**, on measurement, not opinion:
`bracket_2_3` scores −399.7 ± 61.1 c/market (t = −6.5) and `oracle_both` equals `oracle_entry` to
the cent, because 0 of the 3,196 lots a perfect entry gate accepts ever has a row where crossing out
beats carrying. On a binary that settles at 100, carrying dominates any intermediate exit minus the
fee, so a profitable exit only exists on a lot that was going to lose — which entry selection
already removes. `TAKE_PROFIT` / `STOP_LOSS` stay in the label set and in `serve.py` (as hard
deterministic brackets, which exist for drawdown control, not for mean P&L), but **the scored claim
is the entry gate**. An exit claim needs a variance objective and its own PREREG.

## Bar

`gliner` passes only if **all** hold on the test fold:

- `c_per_market` positive with t > 2 against zero, measured on ENTRY decisions;
- it beats `detgate` by more than the SE of the difference;
- it beats `logit` at all — if a 194M text encoder cannot beat a linear model on the same numbers,
  the serialization is not adding information and the branch is closed, not retuned;
- it beats `bracket_2_3`, which is a low bar but a published one;
- both halves of the test fold (split by market close) have the same sign.

## Amendment 2, 2026-10-02: the mark

The scored P&L is **named before the run and shared with the training label** (`--mark`, recorded in
`manifest.json`). Measured paired gate-minus-always SD per market: 61 c at the 5 s markout, 229 c at
60 s, 732 c at settlement — a 140x swing in required tape, because hold-to-settlement adds
directional variance the quoting decision does not control.

Primary: **`mark = mk5s`**, which the 117 markets in hand can resolve (the gate-minus-always paired
difference is already +29.5 ± 5.7). Secondary, unscored until the tape exists: `mark = settle`.
An `mk5s` win is a claim about adverse selection, NOT about money, and may not be reported as P&L.

## Sample precondition

The 3-hour tape in this tree gives 117 markets and a test-fold SE of ±52 c/market with
per-market P&L of order ±30 c — the controls cannot separate at that n (measured, `ml/README.md`).
**No GPU run is scored until the dataset has enough markets that `detgate` separates from `always`
with |t| > 2.** This is now **met at `mark = mk5s`** (paired +29.5 ± 5.7, t = 5.2, 117 markets) and
**not met at `mark = settle`** (+103.8 ± 67.7, t = 1.5; ~550 h of tape needed). A GPU run is
therefore authorized at `mk5s` and barred at `settle`.

## Amendment 3, 2026-10-03, written BEFORE the adapter was scored: the pilot is not the scored run

A LoRA run is training now and it is a **PILOT**, not this PREREG's one scored run. Declared in
advance so it cannot be read as the scored run afterwards, whichever way it comes out:

- `fastino/gliner2.5-small-v1` (74 M), not `-base-v1` (194 M), because the GCP free-trial billing
  account refuses non-TPU accelerators and this runs on the Mac's MPS;
- train set **subsampled 116,049 -> 20,000 lines** (every 6th line, so it spans the same window
  rather than the first hour), val 48,335 -> 4,000;
- 2 epochs, LoRA r=8, 663,552 trainable params (0.89%);
- scoring is on the **full** 140-market test fold from the unsubsampled parquet, so the arm table
  is comparable even though the fit is not.

**What the pilot can and cannot do.** It cannot pass the bar: a win on a sixth of the training
data would need confirming at full scale, and the stopping rule's "one scored run per dataset
version" is reserved for that. It CAN fail informatively — if a 74 M encoder on 20 k rows lands far
below `room4` (+65.7 ± 15.3 c/market), that is evidence about the serialization, which is what
§"What is not being tested" already says the GPU would buy.

**The bar it is measured against has changed since this PREREG was written, and the change is
adverse.** Clause 3 said "it beats `logit` at all". Measured 2026-10-02, `logit` IS the one-line
rule `spread_ticks >= 4` (paired `logit − room4` = +3.7 ± 4.1 c/market, t = 0.91), so the real
comparison is against a deterministic rule that already runs in `penny.py`'s 11 ms path — and
adding 16 features (VWAP, moving averages, realized vol, taker flow, A-S terms) moved it by
**−0.14 ± 3.33**. `room4` is therefore added to the trainer's arm table with paired differences,
and a GLiNER arm that does not beat `room4` closes this serialization under the stopping rule
below, exactly as clause 3 intended.

## Stopping rule

One scored run per dataset version. A failed bar closes the branch for that serialization; a new
serialization is a new PREREG, not a re-score of the same rows.

## What is not being tested

Queue position (the improved quote creates an empty level, so there is none) and anything live: the
sidecar ships with `GATE_SHADOW_ONLY=1`.

Latency is a **measured cost, not an open question**: `gliner2.5-small-v1` is 55.9 ms p50 on the
Mac and `-base-v1` is 80.1 ms, against our 11 ms decision→book path. The model is therefore barred
from the cancel path by construction; the deterministic rule keeps pulling quotes. Any result here is
a result about a decision that tolerates ~60-100 ms, and a live arming that puts the sidecar in the
cancel path is a different, unpreregistered experiment.
