# Prospective residual-gated BTC queue test

Written 2026-09-27 AFTER the exploratory results in
`FINDINGS_scalable_btc_20260927.md`. This specifies a future experiment;
The arms were subsequently implemented on 2026-09-27 under
`shadow --residual-ladder --series KXBTC15M`.

## Question

Can a quote-value toxicity filter improve realized maker economics while
preserving queue position and accepting larger clips on the deep BTC touch?

## Fixed arms

BTC15M only, 15–85 cent mid, no new quotes in final 120 seconds, same existing
momentum/thinness gates. Model formula and causal history exactly as in
`research_spot_residual.py`, without tuning on the next sample.

- Existing base, 1-contract clip and absolute-position cap 1.
- Existing base, 25-contract clip and absolute-position cap 25.
- Book-only fair-edge >= 0, 25-contract clip and cap 25.
- Spot-adjusted fair-edge >= 0, clips/caps 1, 10, 25 and 100.

These are shadow arms, not permission to place live trades. Each arm has an
independent budget equal to the real bot's limit; arms do not share fills or
liquidity. Missing/stale model inputs stop new risk and trigger cancellation.
Retain actual create/cancel exposure and existing inventory during any pause.

Evaluate the model on spot updates as well as venue updates. Preserve an
existing quote if it remains eligible; do not cancel/repost merely because
fair value changed. Simulate both cancellation-credit assumptions: trades-only
and the existing pro-rata approximation, reporting both without picking the
better result. Account for consumed quantity across trade fragments.

## Data and scoring

Fresh seven-day capture, at least 672 complete BTC windows; do not count
partial start/end markets toward the requirement. Preserve initial full-book
snapshots, sequenced deltas, public trades, spot receipt times, hypothetical
order lifecycle and queue-ahead estimates. Rotate/compress files and cap disk
usage on the VM. Fail a window on unrecovered feed gaps.

Primary: total settlement P&L, including paired fills, residual inventory and
fees, per wall-clock hour INCLUDING zero-fill windows. Secondary: contracts
per hour, 1/5/60-second markouts, average/max inventory, drawdown, queue age,
cancel latency, pairing fraction, and incremental P&L by clip size. Separate
settlement exposure from paired spread capture.

Use paired comparisons on the same markets; report hourly-block uncertainty
and daily outcomes to expose serial dependence. Report chronological halves,
all scheduled arms, missing data and dropped rows. No parameter retuning.

## Decision

A candidate needs positive settlement economics in both halves, a positive
95% lower bound on hourly P&L, and increased total P&L at larger size. The
spot-adjusted arm must beat the simpler book-only control to justify its extra
model. A $10k/month claim additionally requires conservative net economics
supporting $13.89/hour; a positive five-second midpoint mark does not qualify.

A shadow pass still does not establish deployable capacity because our orders
are invisible to competitors. Any later live test requires explicit live-risk
authorization and a specified capital/loss limit. No live change is part of
this investigation.

## Implementation freeze, before the prospective run

Fourteen arms: each of the seven strategies above under `pr` (the existing
pro-rata/clamp queue approximation) and `tc` (strictly trade-driven queue
advancement, no credit for cancellations or level shrinkage). Keep both in
the report. Pro-rata classification remains approximate; the trade-only arm
is a conservative execution sensitivity, not a mathematically guaranteed P&L
lower bound, since changing fills also changes inventory.

All arms are evaluated on the same venue, spot and 100 ms timer events.
Receipt-clock midpoint history is used consistently. Pending cancels remain
fillable until their venue-arrival timestamp. A hypothetical post-only quote
crossing the opposite touch when it arrives is rejected. Trade fragments
consume remaining order quantity and cannot fill the same order twice beyond
its outstanding size. Position caps include both directions; new quoting does
not assume an immediate fill at the touch.

The spot model uses the first spot receipt as its one-second sampling origin,
matching the diagnostic. A stale input cancels model-arm quotes; a stopped or
overflowed spot feed stops the process. A supervisor may restart after an
identified feed stop; partial windows around a restart are excluded and
reported. Venue reconnects invalidate affected markets. Missing windows are
reported explicitly and cannot support a full-run income claim.

A complete operational window requires the collector to have started before
the window opens, discovery/snapshot within 20 seconds of opening, and no
recorded venue gap. The allowed discovery delay is unquoted time, included in
the 15-minute denominator. This makes the existing 15-second discovery cycle
explicit; it is not evidence that the opening seconds were recorded.

The initial schedule is seven days plus sixteen minutes (10,096 minutes),
allowing for the partial first window. At least 672 complete usable windows
are still required; if gaps leave fewer, the result is incomplete, not a pass.

Audit JSONL is gzipped in five-minute chunks and contains snapshots, sequenced
venue events, spot receipts, simulated create/join/cancel/fill events and final
cash/inventory ledgers, including zero-fill arm windows. The small legacy CSV
contains fills and terminal ledgers only in this mode. A local archiver removes
only completed remote audit chunks after SHA-256 verification of the local
copy. Remote collection stops at 1 GiB spooled data or 512 MiB free disk.
The local archiver requires this computer to remain awake and AWS access to
remain valid; the disk guard handles an unavailable archiver.

No hypothesis decision will be made from smoke-test or interim P&L. The scorer
reports maker-fee-zero results for BTC15M and must flag any fee-schedule change
before final interpretation. It checks cash/inventory reconciliation and clip
caps, separates FIFO paired P&L from residual settlement, and preserves
zero-fill windows. Final inference needs the scheduled-window coverage report
as well as hourly-block and daily uncertainty diagnostics.
