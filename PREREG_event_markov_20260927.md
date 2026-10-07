# Preregistration: event-level queue-reactive maker screen

Frozen 2026-09-27 before computing any model score on
`data/box/shadow_spot/tape.csv.gz`. This is an offline, shadow-fill screen. It
places no orders and cannot authorize live trading.

## Question

Does the Ohio engine's full receipt-time event stream contain a causal
microstructure state that improves the existing one-contract `base` maker at
realistic cancel latency? The candidate is a queue-reactive finite-state Markov
reward model, not another candle-level fair-value model.

## Data and causality

- Use only `base` shadow fills.
- Infer settlement only where the final recorded market midpoint is at most 5c
  or at least 95c. Exclude the final unfinished close window.
- A fill at venue time `t` may use only rows whose local `recv_us` is at or
  before `t - cancel_latency`.
- Test cancel latencies 5.44, 10, 20 and 50 ms.
- Split by 15-minute close window, never by fill: first 50% train, next 25%
  validation, final 25% test. Assets in the same close window stay together.
- Fit pooled across assets with asset indicators; publish ETH and every other
  asset separately on the untouched test windows.

## Models

1. `all`: retain every eligible `base` fill.
2. `book_edge_ge_0`: the one-second Kalshi-book anchor control.
3. `gbm_edge_ge_0`: the frozen 60-second realized-volatility/logistic-GBM fair
   value used by `research_spot_residual.py`.
4. `ridge`: a regularized continuous event-state reward model.
5. `markov`: a regularized finite-state reward model using spread, hit-side
   depth imbalance, recent directional midpoint transitions, same-side trade
   flow, touch depletion, Coinbase movement and time to close.

Both learned models predict maker five-second markout. Validation chooses one
retention fraction from 50%, 75% and 100%, maximizing validation five-second
markout; ties choose the larger retained fraction. Test settlement is not used
for fitting or threshold selection.

## Primary gate

A learned arm qualifies for a prospective Rust shadow arm only if, on pooled
test windows at both 5.44 and 10 ms:

1. it retains at least 50% of eligible fills and at least 50 markets;
2. five-second maker markout is positive with a two-standard-error lower bound
   above zero; and
3. settlement P&L per close-window is positive with a two-standard-error lower
   bound above zero.

Per-asset rows, 20/50 ms rows, GBM and book controls are diagnostics. Filtering
an existing fill ledger does not replay cancellations, queue loss, reposts or
inventory. Even a primary pass requires a new isolated shadow run before any
live order.

## Amendment A: asset-only diagnostic

Frozen after the primary run revealed that its final close-window block had no
eligible ETH fills, but before computing any ETH-only model score. For each
asset with at least eight eligible close windows, repeat the same 50/25/25
chronological split, models and validation retention rule inside that asset.
These rows answer the requested ETH question but cannot satisfy the primary
gate or authorize a prospective arm.

## Amendment B: model-independent eligibility correction

Frozen after Amendment A produced no non-BTC asset rows, before recomputing the
corrected report. The first implementation incorrectly required every fill to
have a valid 60-second GBM volatility estimate and fresh spot observation. That
made the GBM control define the sample for the book and event models and removed
nearly all non-BTC fills. Correct behavior is:

- common eligibility depends only on causal book history, the frozen mid-band
  and close-time rules, a settled outcome and a genuine later five-second mark;
- stale or unavailable spot/GBM features are missing states for learned models;
- `gbm_edge_ge_0` alone requires a finite GBM estimate.

No threshold, split, feature bin or pass rule changes.
