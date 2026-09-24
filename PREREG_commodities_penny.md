# Preregistration: penny-jump maker, 15M COMMODITIES (frozen 2026-09-24 ~22:40Z, before any run)

## Why (evidence at freeze time)
- The commodity JOIN maker is closed (`FINDINGS_commodities.md` on branch `commodities`): 5/5 arms
  lose in both halves (gate_pr −17.7 ± 5.7 c/market). Our back-of-queue fills mark out
  −0.25 to −1.0 c/ct, while the population is breakeven.
- On the commodity probe tape the FIRST taker order of a burst (the head) pays the maker
  +0.19 / +0.37 c/ct at 5 s (H1/H2), and deeper sweep continuation costs −1.20 / −1.62.
  A quote alone at a price nobody else holds is at the front by construction, so it is filled by
  the head. That is the mechanism a penny jump buys.
- The crypto penny shadow (PREREG_penny.md, same engine) scored +4.76 ± 1.48 c/market over its
  whole run, inconclusive on its prereg window. It does not transfer by assumption.
- Against: `gate_pr_in` (one tick inside at spread ≥ 3 ticks, mid band only) lost −13.2 ± 4.9 on
  commodities. The mid-band spread is ≤ 1c on ~80% of prints, so room is rare there.
  The wings (0-10c, 90-100c, deci-cent on GOLD/SILVER/WTI) are where the room is. That is not
  tested by any arm so far.

## Frozen rules (1 ct, |pos| ≤ 1, all bands 1-99c, no posts < 120 s to close)
Same code as the crypto penny shadow except that the tick comes from each market's own
`price_ranges` (COPPER/NATGAS are 1c everywhere; GOLD/SILVER/WTI are 0.1c below 10c and above 90c).
- `penny5` (PRIMARY): spread ≥ 5 ticks → one tick inside on each side, crypto gate on.
- `penny2`: same with spread ≥ 2 ticks.
- `penny5_ng`, `penny2_ng`: same without the gate. The crypto gate's features did not replicate
  on the commodity tape.
- `base`: gated join in the mid band. CONTROL: it must come out negative (commodity join ≈ −13 to −18).
Latencies: create 5.44 ms, cancel 4.43 ms. Queue: pro-rata cancels. Series: all five 15M commodities.

## Window, metric, decision
- One fresh 12 h shadow on the Ohio box starting after this file is committed.
- Primary: `penny5` settlement c/market, SE clustered by market. H1/H2 = split at the median close.
- **penny5 PASSES** iff lower 95% bound > 0 AND mean > 0 in both halves → candidate for a capped
  real 1-ct run (user approval, stated expected cost if void).
- Mean > 0 with bound ≤ 0 → extend once by a fresh 12 h window, same rule. Mean ≤ 0 → closed.
- Secondary arms pass only by the same test, and a secondary pass needs its own fresh window
  before it means anything (four arms tested).
- Control check: if `base` is not negative, the run is suspect and decides nothing.
- No re-tuning of room, gate or tick on this window.

## Known limits (they bias the penny arms UP)
- Our quote is not in the book, so no competitor re-pennies us and no taker reacts to our better
  price. A real inside quote gets jumped. The ~7.7 ms competitors will re-penny us on crypto.
- Fills at our improved price are credited whenever a taker trades at or through it.
