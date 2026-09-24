# Pre-registration: gated penny-jump maker, 15M crypto (frozen 2026-09-24, before any penny shadow data)

## Why (evidence at freeze time)
- Every gated JOIN-the-touch run lost (−4 to −19 c/market, ~730 market-runs): back-of-queue fills.
- Memory `penny-jump-room-exists-but-is-not-a-race` (2026-09-09, 68 days of candles): improve one
  tick, break-even spread S > 4.60 ticks; `improve_one_tick` never scored on Kalshi 15M.
- Offline sequential sim on the 3 h probe tape (`penny_seq.py`, cap 1, repost lag, order dedupe):
  room ≥5 ticks +9.0 ± 3.2 c/market (H1 +8.5, H2 +9.1), room ≥2 +8.4 ± 3.6.
- Known weaknesses: gate thresholds were fitted on that same tape; competitor reaction to OUR
  improved quote is absent from any tape. This window exists to test both.

## Frozen rule (shadow strategies, all 9 series, 1 ct, |pos| ≤ 1, all price bands 1-99 c)
- `penny5`: if spread ≥ 5 ticks, quote one tick inside on each side; else no quote on that side.
- `penny2`: same with spread ≥ 2 ticks (secondary).
- Tick: 0.1 c below 10 c and above 90 c, else 1 c.
- Gate: pull a side if 1 s mid momentum ≥ 0.25 c runs into it or it holds < 7.87% of touch size.
- No posts < 120 s to close. Latencies: create 5.44 ms, cancel 4.43 ms. Queue: pro-rata cancels.
- Control in the same run: `base` (gated join, mid band) — must reproduce its known negative sign.

## Window, metric, decision
- Fresh window starting after this file; stop at 100 settled markets per strategy.
- Primary: `penny5` settlement c/market, SE clustered by market.
- Lower 95% bound > 0 → candidate for a capped live run (user approval required).
- Mean > 0, bound ≤ 0 → extend once by 100 markets. Mean ≤ 0 → closed, no re-tuning on this window.
- Diagnostic: how often the touch changes within 50 ms after our improved quote goes live
  (competitor re-penny rate) — the reflexivity the offline tape could not see.

## Outcome of the frozen window (scored 2026-09-24 ~18:15Z)
- First 100: +0.08 ± 2.79 c/mkt (bound −5.40) → extend once. First 200: +2.93 ± 2.06 (bound −1.11)
  → INCONCLUSIVE under this rule (mean > 0, bound ≤ 0 after the one allowed extension).
- Controls over the run: base −7.41 ± 2.34 (sign reproduced), penny2 −5.70 ± 2.38.
- Post-window data (201+) is NOT decision data for this prereg.

# Confirmation window (frozen 2026-09-24T22:05Z, identical rule, no changes)
- Data: `penny5` markets that CLOSE after 2026-09-24T22:05Z, from the same running shadow (v6, unchanged).
- Stop at 200 settled markets. Pass: lower 95% bound > 0. Else: mean ≤ 0 → closed; mean > 0 → report,
  no further extension of this rule.

## Final scoring (shadow stopped 2026-09-24 22:10Z by the user, ~14 h, ~470 markets per strategy)
- The confirmation window above received NO data (frozen 22:05Z, run stopped 22:10Z).
- penny5: prereg first 200 +2.93 ± 2.06 (bound −1.11, inconclusive); markets 201+ under the unchanged
  frozen rule +6.30 ± 2.11 (bound +2.16, n=237); whole run +4.76 ± 1.48 (bound +1.85), +$20.78 at 1 ct.
- Controls: base −6.54 ± 1.97, penny2 −2.86 ± 2.03 over the whole run.
- penny5 − base by quarter (same markets, same time): +19.4, +9.2, +6.5, +10.1 c/mkt.
- Pairs +0.50 c/pair, mk60s +0.40 c/ct. Series: NEAR +14.6, DOGE +12.1 strongest; BTC −2.0, SOL −2.6.
- Status: strongest evidence on this seat, not a clean prereg pass. The untested risk is competitor
  re-pennying of a REAL quote; only a capped live run measures it.
