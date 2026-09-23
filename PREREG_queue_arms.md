# Preregistration: queue-position arms for the 15M crypto shadow (frozen 2026-09-23, before any run)

## Why
Shadow v1-v3 lose on every gate variant (gate −14.4 ± 5.3, −12.8 ± 5.7, −20.9 ± 7.9 c/market;
gate_pr −11.3 ± 9.5 on the v3 partial). No fill feature (spread, thin side, lagged momentum)
replicates across tapes as a further filter. Hypothesis: the loss is fill *selection*. We join
the back of the queue, so we are filled mostly when a sweep eats through the size ahead of us, and
those fills are the tail of the sweep, which is the toxic part. On commodities the head of a sweep
paid +0.70 c/ct and the tail −1.7 to −6.4 c/ct.

## Arms (same feed, same process, same latencies, 1 ct/side, cap 2, cancels-ahead pro rata)
- `gate`: v1/v2 reproduction arm. Must land within 2 SE of −13 c/mkt or the run is suspect.
- `gate_pr`: v3 reproduction arm, and the control for both tests.
- `gate_pr_in`: one tick inside the touch when the spread is ≥3 ticks (both sides), or 2 ticks (only the side that reduces inventory).
- `gate_pr_front`: posts only where the displayed level at our price is ≤ 50 ct.

## Primary outcome
Settlement P&L per market (paired + naked), SE clustered by market, one fresh 6h run on the Ohio box
with no other process on it.

## Decision rules (fixed now)
1. **Queue diagnosis** (`gate_pr` fills, by `q_fill`): the hypothesis is SUPPORTED if fills with
   `q_fill` ≤ 50 ct beat fills with `q_fill` > 250 ct by more than 2 SE of the difference
   (clustered by market). Otherwise queue position is not the explanation.
2. **An arm PASSES** only if its c/market > 0 with t ≥ 2 **and** it beats `gate_pr` on the
   same markets (paired by market) with t ≥ 2.
3. **Branch closes** if neither `gate_pr_in` nor `gate_pr_front` passes rule 2. No re-tuning of the
   50 ct threshold or the tick rule on this same tape. A new threshold needs a new run.

## Known limits of the simulation
- Our shadow orders are not in the book, so no other maker reacts to them. A real inside quote gets
  pennied by competitors who react ~7.7 ms vs our ~11 ms. This biases `gate_pr_in` UP, so a pass
  has to be re-checked with real 1-ct orders before any size.
- `gate_pr_front` skips BTC most of the time (BTC median touch ~1,600 ct); expect few BTC fills.
