# PREREG — BNB passive fill-floor execution audit

Frozen 2026-10-06 at 07:08 PDT, before any post-2026-09-14 BNB signal-market
trade tapes were fetched or joined.

Apply the exact BNB secondary signal from
`PREREG_altspot_tilt_20261006.md`: frozen 2.44 bps basis, prior 120-minute
Coinbase volatility, decision minutes 2/3/5/8, first 10-cent fair/book trigger.
The historical BNB outcome result is discovery-only and is not retuned.

At the first signal, join the displayed bid of the signaled side from the
decision candle, two seconds after the boundary, and leave one contract resting
until close. Credit only a later public non-block aggressor print strictly
through that limit. Do not credit prints at the limit because queue ahead is
unknown. Exclude truncated histories. Hold a credited fill to settlement and
charge the standard zero KX*15M maker fee.

Report 2/5/10-second buffers and an additional one-cent adverse-price stress.
The single-asset paper screen requires at least 75 strict-through fills, a
positive UTC-day-clustered lower 95% bound per submitted order, and positive
chronological halves. This is an execution audit on an already discovered
historical population, not an independent return holdout. A pass can authorize
only a prospective queue-aware shadow collector, never orders.
