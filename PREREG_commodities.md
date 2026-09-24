# Preregistration: 15M COMMODITY two-sided maker shadow (frozen 2026-09-24 ~07:40Z, before any run)

## What is being ported
The 15M crypto pipeline, unchanged except for the series list: `probe` (competitor reaction,
feed age, full touch tape) → `tox.py` (print toxicity at t−LAG, H1/H2) → `shadow` (simulated
two-sided 1-ct maker, measured latencies, back of queue, pro-rata cancel credit) → `shadow_pnl.py`
(settlement P&L, FIFO pairs + naked residual, SE clustered by market).

Series: KXGOLD15M, KXSILVER15M, KXWTI15M, KXCOPPER15M, KXNATGAS15M. One up/down market per
series per quarter hour, ticker time in US Eastern, fee_type quadratic ×1 on all five (checked
2026-09-24; maker fee measured 0.000000 on the commodity runner). The mid band 15–85c is a 1c
lattice on all five (GOLD/SILVER/WTI are deci-cent only in the wings).

## Why this is NOT the closed commodity seat
`commodity-pair-maker-loses-three-cents-irreducibly` closed a DIRECTIONAL-then-paired seat (one
leg on an imbalance signal, then the exit). That note names the simultaneous two-sided quote as
still open. This shadow tests exactly that seat. It places NO orders.

## Arms (one process, one feed, same latencies: create 5.44 ms, cancel 4.43 ms; 1 ct/side; |pos| ≤ 2)
All constants are copied from the crypto shadow and are NOT tuned for commodities.
- `nogate_pr` — join both touches, no gate, pro-rata cancel credit. The baseline.
- `gate` — crypto gate (pull on 1 s mid momentum ≥ 0.25c into the side, or hit side < 7.87% of
  touch size), trades-only queue. Pessimistic queue bound; sensitivity only.
- `gate_pr` — same gate, pro-rata cancel credit. The main gated arm.
- `gate_pr_in` — `gate_pr`, one tick inside when spread ≥ 3 ticks (≥ 2 on the reducing side).
- `gate_pr_front` — `gate_pr`, posting only where the displayed level is ≤ 50 ct.

## Window
One 12 h shadow run on the Ohio box, starting when exchange index 2 reports `trading_active`
after the 2026-09-24 maintenance. A 12 h `probe` runs alongside it for the tape and reaction stats.
H1 / H2 = markets closing before / after the median close time of settled markets.

## Primary outcome
Settlement c/market per arm, all five series pooled, SE clustered by market.

## Decision rules (fixed now)
1. **An arm PASSES** iff (a) its lower 95% bound (mean − 1.96·SE) > 0 over the full window, AND
   (b) its mean is > 0 in BOTH H1 and H2. A gated arm must also (c) beat `nogate_pr` on the same
   markets, paired by market, with t ≥ 2.
2. **Gate value:** `gate_pr − nogate_pr`, paired by market. Reported, no pass/fail on its own.
3. **Queue diagnosis** (the queue prereg's rule 1, applied to `gate_pr`): small queue at fill
   (≤ 50 ct) minus deep (> 250 ct), c/ct, paired by market, SUPPORTED iff > 2 SE.
4. **Per-series results are exploratory.** A series picked on H1 counts only if it is positive
   again on H2, and even then it needs its own fresh prereg before any capital
   (`in-sample-controls-cannot-detect-selection`: +16.6pp became −0.4pp OOS on crypto).
5. **Closure:** if no arm passes rule 1, the commodity two-sided maker is CLOSED on the shadow.
   No re-tuning of the gate constants, band, clip or queue threshold on this tape. A new
   threshold needs a new window.
6. **Toxicity features** (`tox.py` on the probe tape) count only if the sign agrees in H1 and H2.
   They are inputs to a future prereg, not to this decision.
7. **A pass does not arm anything.** It justifies a capped real 1-ct run, which needs the user's
   approval and has to state its expected cost if the rule is void.

## Known limits
- Our shadow orders are not in the book, so nobody reacts to them. `gate_pr_in` is biased UP.
- The write-token bucket is per arm (as if each arm had the account's Advanced budget alone).
- Real fills showed cancels clear 41% of the queue on crypto. Pro-rata is a model, not a
  measurement on commodities. `gate` vs `gate_pr` brackets it.
- If the crypto NEAR shadow is still running on the box, the two share 2 vCPU. The commodity
  shadow logs its handling latency (`handle_us`) every 60 s. p99 > 1 ms flags a contaminated run.
