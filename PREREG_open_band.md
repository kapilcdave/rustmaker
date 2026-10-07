# Pre-registration: penny4 opening price band 10-90c

Frozen 2026-09-30 09:20Z, before any fill from the first band run was scored.

## Change

Live supervisor run from 2026-09-30 09:07:53Z (`kalshi-mm15-live-band`, `--open-min-c 10 --open-max-c 90`,
otherwise the clip-2 config: penny4, amend, spot 2 bps, clip 2, max-pos 2, round-net 4, 450 s cutoffs).
Opening/adding orders only at YES prices in [10c, 90c); reducing orders quote at any price.

## Why

On 2,960 capped live fills (runs 12, 13, sup 1-3; `taker_size_capacity.py`), wing fills (<10c, >90c) were 60%
of fills and settled at -0.04/-0.15 (<10c) and -0.26/+0.10 (>90c) c/ct in the two halves. Dropping them in replay
raised the clip-1 per-market t from 2.09 to 2.28 at the same dollars. Two earlier independent measurements agree
(wings-only shadow -7.9 c/mkt; wing fills mark out ~0).

## Scoring (at 300 settled markets under the band, venue ledger, held to settlement)

- Primary: c/mkt mean, market-clustered se, t; per-contract c/ct. Pass = lower95 > 0 and both time halves > 0.
- Comparison: the same statistics on the clip-2 no-band run (sup_20260930T064425Z) and the clip-1 capped runs,
  reported per CONTRACT (clip differs).
- Also report the minus-best-20-markets total, next to the value a normal with the same mean/sd gives.

## Holdout for DOGE/SOL (selected in-sample; not acted on)

DOGE and SOL were negative in both halves of the same 2,960 fills. Test on fills after 2026-09-30 06:44Z only
(never used in selection): drop them only if both are < 0 c/ct AND the pooled DOGE+SOL c/ct is < 0 at one-sided
p < 0.10, market-clustered.
