# Pre-registration: penny room 3 (and 4), 15M crypto AND commodities (frozen 2026-09-25, before launch)

## Why
Offline `penny_seq.py` swept the minimum spread room on the two existing probe tapes (2026-09-25):

| room (ticks) | crypto 3 h tape, c/market | commodity 12 h tape, c/market |
|---|---|---|
| 2 | +8.4 ± 3.6 (117) | +4.8 ± 2.8 (245) |
| **3** | **+14.4 ± 3.7 (115)** | **+7.5 ± 2.8 (219)** |
| 4 | +11.8 ± 3.5 (107) | +6.9 ± 2.9 (147) |
| 5 | +9.0 ± 3.2 (100) | +3.1 ± 3.4 (114) |

Room 3 was picked as the best of 4 widths on the same tapes, and the gate thresholds were fitted on the
crypto tape. The offline score is therefore selected in-sample. The crypto penny5 chain shrank
offline +9.0 → shadow +4.8 → live +0.8. Expect the same shrinkage here.

## Runs (both shadows, NO orders, started together after this file is written)
- Crypto: `kalshi-mm15` shadow, all 9 series, `data/shadow_penny3`.
- Commodities: `kalshi-mm15-cpenny` shadow, GOLD/SILVER/WTI/COPPER/NATGAS, `data/shadow_penny3`.
- Arms, all 1 ct, |pos| ≤ 1, bands 1-99 c, gate on, no posts < 120 s to close: `base` (control), `penny5`,
  `penny2`, **`penny3` (PRIMARY)**, `penny4` (secondary). The commodity run also keeps `penny5_ng`/`penny2_ng`.
- Latencies 5.44 ms create / 4.43 ms cancel. Queue: pro-rata cancels. Tick from each market's price_ranges.
- 12 h each.

## Decision (per asset class, independently)
- Primary: `penny3` settlement c/market, SE clustered by market, `python3 shadow_pnl.py <tape>`;
  H1/H2 = split at the median close.
- **PASS**: lower 95% bound > 0 AND mean > 0 in both halves AND penny3 − penny5 > 0 on the same markets.
  → candidate for a capped live 1-ct run (user approval).
- Mean > 0 but bound ≤ 0 → inconclusive, one fresh 12 h window allowed, same rule, no pooling.
- Mean ≤ 0 → room 3 closed on that asset class; the live engine stays at room 5.
- `penny4` passes only by the same test and then needs its own fresh window (it was co-selected).
- Control: `base` must be negative, or the run decides nothing.

## Known limits (they bias every penny arm UP)
- No competitor re-pennies a shadow quote. Live run 1 saw our crypto quote undercut on 9.1% of posts.
- Room 3 quotes more often than room 5, so the throttle binds harder live (one shared budget, not
  one per arm as in the shadow).
