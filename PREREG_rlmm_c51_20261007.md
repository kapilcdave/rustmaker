# PREREG — a distributional-RL market maker on 15M crypto (C51 + BOCPD + scenario bandit)

Frozen 2026-10-07, before any number on the `mk5s-v3` val or test folds exists.
Instrument `ml/rl/`, dataset `ml/data/mk5s-v3`, scorer `ml/policy_eval.py` (unchanged).

## 0. This reopens a closed branch, and here is exactly what is new

`mm-controller-family-closed-both-15m-classes` closed the **continuous-controller family —
Avellaneda–Stoikov, GLFT, microprice — on 15M crypto and 15M commodities**, on the finding that a
*perfect-inventory oracle* loses −4.85 c/ct (crypto) and −2.93 c/ct (commodities), 8 of 8 series.
A reinforcement-learning quoter is a strict superset of that policy class, so by default it is
closed too and this document has to say what escapes the closure.

Four things are new, and one load-bearing thing is **not**:

| | new? | why it could matter |
| --- | --- | --- |
| learned policy instead of closed form | yes | the closure tested controllers whose functional form was fixed; a C51 critic can express a gate the closed forms cannot |
| BOCPD regime state over directional flow bias | yes | nothing in the 30-feature set is a *regime* variable. `flow60`/`flow_adv60` are window means and score AUC 0.500/0.507 — at chance. A change-point posterior is a different statistic, not a longer window |
| queue-adjusted quote-exposure imbalance | **partly** | see §4: `q_ahead` is identically 0 in this instrument, so the term provably collapses to `s·(2·imb−1)`, which is already in `penny.py`'s gate. Only the position-lean variant is new |
| adversarial scenario reweighting (EXP3) + CVaR action selection | yes | both target the drawdown-under-persistent-imbalance failure, which is the specific thing the controller closure measured |
| **the fill model** | **NO** | the binding constraint, and it is unchanged |

The last row is the honest reading of this whole pre-registration. `mk5s` entry rows come from the
improved-quote fill model, and that model **overstates adverse selection 5.17×** against 8,038,551
real Kalshi prints (7.343 simulated vs 1.421 real; capture agrees to 0.005 c). The `room4`
benchmark's +65.7 c/market is therefore roughly 6–11× the live penny-jump arm's +5.9 c/market at
settlement on real fills. **So no absolute number produced by this run is a claim about money.**
What the instrument *can* support is a **paired difference between two policies on identical
rows**, because both arms inherit the same fill-model error. Every gate below is therefore written
on a paired difference, and the single-arm levels are reported only as context.

## 1. Hypothesis

H1. On the 140 held-out markets of `mk5s-v3`, a C51 quoter with regime state beats `room4`
(`spread_ticks >= 4`, one line, already deployed in `penny.py`'s 11 ms path) by a paired per-market
margin that is both statistically and economically material.

H0. It does not — i.e. the result replicates
`richer-features-add-nothing-to-the-15m-maker-gate` (30 features − 14 features = **−0.14 ± 3.33
c/market, t = −0.04**) with a larger policy class, and the 15M crypto maker gate is a one-line
width comparison for the third time.

## 2. Instrument and folds

* Dataset: `ml/data/mk5s-v3`, built by `ml/build_dataset.py --mark mk5s` from the **same two
  tapes** as `mk5s-v2` (`data/box/tape_1790136632445.csv.gz`,
  `data/box/shadow_spot/tape.csv.gz`), so this is the same 558 markets and the same rows v2 was
  measured on, with 10 features added. 40 features total (`features.RL_NUMERIC`).
* Split: the dataset's own **chronological** split at a market boundary — 334 train / 84 val /
  140 test. Nothing is reshuffled; no fold sees a market closing after a later fold's first.
* Mark: `mk5s` (5-second markout). Frozen, recorded in the manifest, read by both the label and
  the scorer. Not `settle`: a t = 2 test of a 10 c/market effect needs 151 markets at `mk5s`
  against 21,425 at settlement.
* Environment: `ml/rl/env.py`. One market is one episode, one offered fill is one step, two
  actions (SKIP / POST). The action space is deliberately *not* a quote offset — that
  counterfactual needs a fill model for "what if I had rested a tick wider", and this venue
  retains no historical resting depth. Widening the action space here would mean inventing fills.

## 3. Frozen configuration

In source, not selected on any fold: `GAMMA_RL=0.99`, `N_STEP=3`, `LR=3e-4`, `BATCH=512`,
`HIDDEN=128`, `N_ATOMS=51`, `TARGET_SYNC=250`, `WARMUP=4000`, `GRAD_STEPS_PER_ITER=8`,
`MARKETS_PER_ITER=6`, `BUFFER=400_000`, PER `alpha=0.6 beta=0.4`, noisy-net `sigma0=0.5`,
BOCPD `HAZARD=1/50 R_MAX=200 Beta(1,1)`, EXP3 `gamma=0.15`, scenario cells = **train-fold**
terciles of (persistence, switching), `POS_CAP=10`, `CLIP_CT=1`, `LAG_US=11_000`.
C51 atom range is set from the **train** fold's n-step return quantiles (0.1%/99.9%, +25% pad).

Selected on **val only**, from grids frozen here: the CVaR level for decision-time scalarisation
`risk ∈ {1.0, 0.5, 0.25, 0.1}`, the training checkpoint (by val c/market), and GLFT's risk
aversion `gamma ∈ {3e-5, 1e-4, 3e-4, 1e-3, 3e-3, 1e-2}`. Seed 0 is the scored run; seeds 1 and 2
are reported as a stability check and **cannot** be substituted for it.

The test fold is read only under `--score-test`, and `report.json` records that it was.

## 4. One result is already determined, and it is recorded here before the run

`q_ahead_ct` is **identically zero** in this instrument: the improved quote creates a *new* level
one tick inside the touch, so nothing rests in front of it. Therefore the "queue-adjusted
quote-exposure imbalance" reduces algebraically to `qxi = s·(2·imb − 1)` — the signed depth
imbalance that `penny.py`'s `detgate` already uses as `heavy`. This is asserted by
`ml/rl/test_rl.py::test_queue_term_collapses_and_says_so` on the built dataset rather than claimed
in prose. Only `qxi_lean` (the same quantity amplified when the fill *adds* to inventory) carries
new information, and `q_ahead_ct` is kept as a zero-variance column so the collapse is visible in
the feature table instead of being quietly dropped.

**Consequence for the claim:** if the agent wins, the queue term is not why.

## 5. Decision rule — seven gates, all on the single test score

| gate | bar | why this bar |
| --- | --- | --- |
| **G1** significance | paired `c51 − room4` > 0 with **t ≥ 2** on 140 markets | `room4` is the benchmark because the incumbent 14-feature logistic collapsed to it (paired +3.7 ± 4.1). Beating `always` or `detgate` proves nothing |
| **G2** magnitude | paired diff **≥ +20 c/market** | priced before the run. Deflating by the instrument's ~11× (room4 +65.7 `mk5s` against the live penny arm's +5.9 at settlement) puts +20 c/market at ≈ +1.8 c/market real; at 9 series × 96 markets/day that is ≈ **+$15/day**, i.e. roughly doubling the only positive live record in this corpus (+$15.83/day at 100% duty). A statistically perfect +2 c/market is not worth an execution stack |
| **G3** attribution | paired `c51 − glft` > 0 | GLFT is the closed-form controller the closure already killed. If RL cannot beat it, the machinery adds nothing and the branch stays closed on its original finding |
| **G4** not a width gate | the `only_learned` fill set is non-empty **and** Jaccard(c51, room4) < 0.9, with the disjoint decomposition reported | every selector measured on this dataset has collapsed to one width comparison. A win that is `room4` with extra steps belongs in `penny.py`, not in a network |
| **G5** breadth | ≥ 55% of test markets positive **and** the diff survives dropping its best 5 markets | a +$16.64 seat in this corpus became +$0.38 once its best 20 of 585 markets were removed |
| **G6** capacity | c51 posts ≥ 2% of offered fills | a policy that earns cents by declining 99.9% of the flow is a capacity statement. Wall 8 — where there is edge there is no size — is what kills nearly everything here |
| **G7** null | c51 c/market > the **hi95** of a turnover-matched **circular-shift** null (20 draws) | the decision sequence is *rolled* within each market, never redrawn, so posts, autocorrelation and block length are preserved and only the alignment to state is destroyed. A redrawn null is a cost handicap, and a null bar scales with the mask's block length |

**All seven must pass.** Any failure closes the branch, and the closure is recorded with the
transferable lesson. Partial passes are reported in full and are not grounds for an amendment that
reruns the test fold.

## 6. What a pass would and would not license

A pass licenses exactly one thing: collecting the `settle`-mark tape needed to confirm the
survivor on real fills, since `mk5s` is a claim about adverse selection and not about money. It
does **not** license arming anything. Before any live arm:

* the gate must be re-measured on the **real print set** (`s·(P − 100y)` needs no mid, no queue
  and no fill model), because that is the measurement that moved this seat +6.32 c/ct and refuted
  the simulated numbers;
* the C51 forward pass must be shown to be out of the **cancel** path — 70%/10 ms on this venue is
  a cancel-latency fact, and the deterministic rule keeps owning the pull;
* funding and caps must be sized off the seat's own round sd (≈75 c), not off the account balance:
  the post-09-30 losses in this corpus were a $1 session cap and a $2.86 balance refusing 57% of
  its own orders, not the seat.

## 7. Amendments

Any change after this file is committed is numbered here, with the date and whether it was made
before or after the test fold was read. An amendment made after a test score cannot convert a FAIL
into a PASS; it can only open a *new* pre-registration.

**Amendment 1 — 2026-10-07, BEFORE any test row was read.** Implementation bug in the n-step
reward, found while the first training run was still on the val fold. `Agent.push` was handed
`ep.net_c` (the reward a POST *would* earn at each step) and `c51.n_step` summed it directly, so
the n-step return credited rewards on steps the policy had **skipped**. The bootstrap target was
therefore the return of a policy that posts everywhere — `always`, an arm already measured at
−33.4 c/market. Fixed by masking the reward stream with the action
(`realized = net_c * (acts == POST)`); guarded by
`test_rl.py::test_n_step_uses_the_RECEIVED_reward_not_the_offered_one`. No frozen hyperparameter
and no gate changed, the training run was discarded and restarted from scratch, and nothing about
this fix was informed by a val score (the bug was found by reading the reward path, not by chasing
a number).

**Amendment 3 — 2026-10-07, BEFORE any test row was read.** Action coverage. Under the frozen
configuration the greedy policy collapsed to near-total abstention — val posts fell
669 → 1,117 → 151 → 10 → 0 → 0 → 8 → 76 → 127 → 23 → 20 out of 48,335 offered fills over
iterations 100–1,100 — after which the replay buffer contained essentially no POST transitions and
`Q(POST)` stopped being updated at all. A critic cannot evaluate an action it has no data on, so
this is a defect in the data pipeline rather than a hyperparameter: scoring a critic whose POST
head was last trained at iteration 300 would measure the optimiser, not the venue. Fixed by adding
`COVER_PER_ITER = 3` episodes per iteration rolled out under the `always` policy, pushed to the
buffer for coverage only and **excluded from the scenario bandit's reward**. DQN is off-policy and
the `always` inventory path is the exact path `features.market_rows` generated the rows under, so
this is valid data. The decision was made from the *buffer composition* and the post counts above,
not from any val cents figure, and the logs now report `buf_post_share` and
`onpolicy_post_share` at every evaluation so the coverage is auditable. Run discarded and
restarted from scratch.

**Amendment 2 — 2026-10-07, BEFORE any test row was read.** `--iters` is not a frozen
hyperparameter (the val-selected checkpoint makes training length a non-selection parameter: more
iterations only add candidate checkpoints). The scored run uses `--iters 3000 --eval-every 100`,
i.e. 30 evaluation points × 4 risk levels = **120 val selections on 84 markets**. That
multiplicity is real and is reported with the result; it biases *against* a pass, because a
checkpoint chosen as the luckiest of 120 on val is the one least likely to transfer to test.
