# A distributional-RL market maker for 15M crypto

A Rainbow-style C51 quoter, a BOCPD regime filter, a queue-adjusted exposure term and an EXP3
scenario bandit — scored in the **existing** harness (`ml/policy_eval.py`), on the **existing**
markets, against the benchmark that is already deployed. Pre-registration:
`PREREG_rlmm_c51_20261007.md`.

```bash
# 0. dataset: the same 558 markets as mk5s-v2, 30 -> 40 features
.venv/bin/python ml/build_dataset.py \
  data/box/tape_1790136632445.csv.gz data/box/shadow_spot/tape.csv.gz \
  --out ml/data/mk5s-v3 --mark mk5s

# 1. tests. The dataset ones check the env reproduces features.py's own columns
.venv/bin/python ml/rl/test_rl.py ml/data/mk5s-v3

# 2. train + val selection. Touches NO test rows.
.venv/bin/python ml/rl/train.py ml/data/mk5s-v3 --out ml/runs/rlmm-v1

# 3. the single scored evaluation
.venv/bin/python ml/rl/train.py ml/data/mk5s-v3 --out ml/runs/rlmm-v1 --score-test
```

## Read this before reading any number this produces

**The level is not a claim about money, and the design reflects that.** `mk5s` rows come from the
improved-quote fill model, which overstates adverse selection **5.17×** against 8,038,551 real
Kalshi prints. `room4` reads +65.7 c/market here against the live penny-jump arm's +5.9 c/market
at settlement on real fills — the same shape, roughly 11× the level. So every gate in the prereg
is written on a **paired difference between arms on identical rows**, where the fill-model error is
common and cancels. A single-arm c/market figure from this harness is context, not evidence.

**The branch it reopens is closed.** `mm-controller-family-closed-both-15m-classes` killed the
A–S / GLFT / microprice family on 15M crypto at a *perfect-inventory oracle*, and
`richer-features-add-nothing-to-the-15m-maker-gate` measured 16 extra features at
−0.14 ± 3.33 c/market. Prior probability here is low; §0 of the prereg states precisely which four
things are new and which load-bearing one is not.

## Files

| file | what it does |
| --- | --- |
| `bocpd.py` | Bayesian online change-point detection over the directional taker-flow bias. Beta-Bernoulli, run-length posterior, constant hazard. Returns `cp_prob`, `cp_recent`, `bias_mean`, `bias_sd`, `run_len` |
| `env.py` | the replay environment. One market = one episode, one offered fill = one step, actions {SKIP, POST}. Recomputes the five position-dependent features from the agent's **own** inventory path |
| `c51.py` | `C51Net` (dueling categorical head, noisy linear layers), the distributional Bellman `project`, prioritised `Replay`, `n_step`, and `q_from_dist` for risk-sensitive scalarisation |
| `glft.py` | the Guéant–Lehalle–Fernandez-Tapia controller as a gate — the arm RL has to beat |
| `bandit.py` | EXP3 over regime cells, rewarded for finding cells the current policy loses in |
| `arms.py` | `room4` (the real benchmark) and `as_room` in the harness's label format |
| `train.py` | train / val-select / score once, with the seven prereg gates and the null |
| `test_rl.py` | 12 unit tests + 4 dataset-consistency tests, runnable without pytest |

## Three design decisions that are not the textbook ones

**1. The change-point branch is scored under the prior predictive, not the old run's.**
Adams & MacKay's eq. 3 scores `x_t` under the *current run's* posterior predictive in both
branches. With a constant hazard that makes the normalised `P(run length = 0)` **exactly equal to
the hazard at every t** — numerator and denominator share the same evidence sum. Measured here
before the fix: `cp_prob` sat at 0.0200 = H for all 120 steps of a stream that visibly flips regime
at t = 60, i.e. the headline feature was a constant. A fresh run is generatively a fresh θ from the
prior, so `bocpd.py` scores the change-point branch under the prior predictive. That version spikes
0.0104 → **0.285** on the flip and `cp_recent` reaches 0.897 within three observations.
`test_bocpd_cp_prob_is_not_the_hazard_constant` guards the regression.

**2. The action space is {SKIP, POST}, not a quote offset.** A textbook RLMM chooses δ⁺/δ⁻. Doing
that here requires answering "what would have filled if I had rested a tick wider", and this venue
retains **no historical resting depth**; a book-only fill model returns *zero* fills on a rule whose
true-runs have a 6 ms median. So a wider action space would be filled in by invention, which is the
error this corpus has already paid for at 5.17×. The sequential content that remains is real but
narrow: the inventory cap masks POST, and five features are functions of the live position.

**3. "Queue-adjusted" has no queue to adjust for, and the code proves it rather than saying it.**
The improved quote is a new level inside the touch, so `q_ahead_ct` ≡ 0 and the exposure imbalance
collapses algebraically to `s·(2·imb − 1)` — which is already `detgate`'s `heavy` term. The column
is kept at zero variance so the collapse shows up in the feature table, and the identity is
asserted on the built dataset in `test_queue_term_collapses_and_says_so`. Only `qxi_lean`, which
amplifies the imbalance when the fill *adds* to inventory, is new information.

## What the risk parameter buys

C51 predicts a distribution over returns per action, so the decision-time scalarisation is a free
choice: `risk=1.0` is greedy in the mean, `risk=0.1` is greedy in the mean of each action's own
worst 10%. One trained critic, four policies, selected on val. That is the one place where
distributional RL is doing something a mean-only critic cannot, and it is also the honest answer to
"what would make a maker robust to persistent directional flow" — a CVaR-greedy quoter declines
fills whose *left tail* is bad even when their mean is fine.

## Known limitations, stated so they are not rediscovered

* **The cap bias runs against the agent.** `features.market_rows` dropped rows whose fill would
  have breached the position cap on the `always` path. A selective policy holds less inventory, so
  the cap would really have bound less often and it would have been offered *more* fills — rows
  that do not exist and cannot be handed back. A gate cannot win here by freeing cap room.
* **The scenario bandit distorts training on purpose.** Selection and scoring are on the
  unweighted fold. A bandit-weighted score would be a number from a distribution the venue does
  not serve.
* **`k` is measured but not identified.** GLFT's δ and the A–S terms all hinge on the arrival decay
  k = 108.62/dollar, whose per-age-bin estimate runs +121 to −219 because order age is downstream
  of our own requote cadence. `A` is at least calibrated per row from the observed arrival rate
  rather than taken from the literature.
* **This is `mk5s`, so it is a statement about adverse selection, not about money.** The
  `settle`-mark confirmation needs ~550 h of tape; collection notes are in `ml/README.md`.
