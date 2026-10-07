# Preregistration: PM-US/Kalshi exact pair with batch fee rounding

Frozen at Unix time `1791302814.603325` (2026-10-06 09:06:54 PDT).

The one-contract screen pays two separately rounded fees. For displayed depth
of at least ten whole contracts, fee rounding can be amortized across a batch.
This screen tests whether that mechanical effect creates a real exact-pair
margin.

- Same exact BRTI contracts and read-only journal as the other PM-US screens.
- Require open PM state, no errors, `abs(pm_age) <= 10ms`, and a metadata pair
  that passes the separate exact-equivalence audit.
- Quantity is `min(100, floor(displayed_size_on_both_legs))`; require `q >= 10`.
- Kalshi batch fee is conservatively rounded up to the cent:
  `ceil(0.07*q*p*(1-p)*100)/100`.
- PM-US batch fee uses coefficient 0.0695 and is also conservatively rounded
  up to the cent.
- Batch per-contract locked margin is
  `1 - yes_ask - no_ask - (fee_k + fee_pm)/q`.
- Require at least 1c per contract on two consecutive samples no more than
  1.5 seconds apart. One signal per window.
- Report an additional 2c per-contract two-leg stress.

The short screen requires five windows, positive stressed margins in both
halves, at least 80% next-sample survival, and minimum quantity ten. It remains
paper evidence even if passed.

