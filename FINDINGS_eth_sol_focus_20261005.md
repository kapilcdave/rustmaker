# FINDINGS — ETH gated 15M maker: CLOSED (2026-10-05)

Scored against `PREREG_eth_sol_focus.md`, frozen 2026-10-03 before any ETH-only datum existed.
Window: `KXETH15M` solo, read-only shadow on the Ohio box, 2026-10-05 05:11:53Z → 19:11:54Z,
14 segments, **all 14 exited rc=0** with 0 short segments, 0 disk events, `throttled=0` throughout.
Tapes `data/shadow_eth/shadow_*.csv.gz` (2,124,194 rows), pooled and scored with `shadow_pnl.py`.

## Verdict: CLOSED

| quantity | value |
| --- | --- |
| settled markets | **57** (prereg required 48) |
| contracts | 4,087 |
| **c per market** | **−19.967 ± 5.136** |
| t | **−3.89** |
| lower 95% bound | **−30.03** |
| total | **−$11.38** |

The decision rule, fixed in advance, was: lower bound > 0 → candidate; mean > 0 with bound ≤ 0 →
extend once; **mean ≤ 0 → closed, do not re-tune on this window.** The mean is −19.97 at t = −3.89
on 57 markets, which is the third branch. **No extension is authorised and none will be run.**

## ETH is indistinguishable from BTC — the series was never the variable

| series | c/market | SE | markets |
| --- | --- | --- | --- |
| KXBTC15M (`PREREG_btc_focus.md`, 2026-09-24) | −18.9 | 8.6 | 26 |
| **KXETH15M (this window)** | **−20.0** | **5.1** | **57** |

Same level, and this window is better powered (SE 5.1 vs 8.6) because it ran its full 14 hours
instead of being cut short by a venue-wide `trading_active=false`. The operator's hypothesis — that
the closure was a BTC artifact — is answered: it is not. It reproduces on a different series, on a
fresh window, at a tighter standard error.

## The loss is in the PAIRS, not the naked residual — and that corrects the BTC reading

| component | value |
| --- | --- |
| pair share | **0.989** |
| paired, c per pair | **−0.3793** |
| paired total | **−$7.67** (67% of the loss) |
| naked, c per ct | −8.627 |
| naked contracts | 43 ct (**1.1%**) |
| naked total | −$3.71 |

The BTC verdict led with `naked −7.0 c/ct`, which reads as a residual-risk problem — leave fewer
unpaired legs and the seat improves. **That is not what is happening here.** 98.9% of contracts
complete a round trip, and the completed round trips themselves lose **0.379 c per pair** on a 1 c
lattice with a **zero maker fee**. By the pair identity
(`gross = matched × (1 − entry_premium − exit_premium)`,
[[an-offsetting-pair-self-liquidates-at-one-dollar]]) the two premiums sum to **1.0038** on average:
the seat is systematically filled on the wrong side of both legs. No exit policy, pairing rule or
residual control can fix a negative *paired* round trip — the loss is embedded at the fills.

## Breadth: it is not a few bad markets

- 19 of 57 markets positive, median **−18.26** c/market.
- Removing the five worst markets leaves **−26.22** c/market, i.e. the trimmed mean is *worse* than
  the raw mean, so the distribution is broadly negative with a few winners, not a few catastrophes.
- Early half −26.90 ± 8.70 (n=28), late half −13.27 ± 5.47 (n=29) — **same sign in both halves**,
  which is what the PREREG asked of any result it would act on.
- Markouts agree with settlement: mk5s −0.283 c/ct, mk60s −0.254 c/ct. This is not a
  hold-to-settlement variance story; the seat is behind five seconds after the fill.

## Deviations and their size, stated rather than buried

1. **Hourly segment restarts.** The supervisor runs 60-minute segments, so simulated inventory
   resets every hour and a market straddling a boundary is quoted by two processes. Pooling is by
   ticker, so its fills are paired across the seam. Expected markets at 4/h × 14 h = 56; **57 were
   scored**, so the restarts cost no measurable sample.
2. **Two counts of the market total.** `shadow_pnl.py` reported 56 markets and the addendum 57; the
   difference is one market that settled between the two `results()` fetches. It moves c/market by
   0.6 c and nothing else.
3. **`gate_probe` was paused for this window** and penny4 was already stopped, so this ran with the
   whole write budget — the condition the solo-window amendment required. `throttled=0` confirms it.

## What this closes

`KXETH15M` is closed for this rule. Combined with the BTC verdict, the 15M crypto gated maker has
now failed a preregistered test on the deepest series and on a second independent series, across
~790 shadow market-runs with no positive. **`KXSOL15M` and `KXNEAR15M` were not run and should not
be** — the prereg declared the three-series multiplicity in advance precisely so that a hunt for the
one series that clears could not be mistaken for a result, and the two remaining candidates are the
ones the 09-24 OOS check already showed regressing. NEAR additionally lost **13.3 c/market live** on
the penny rule on 2026-10-05.

The corpus instruction stands and is now better supported: do not reopen this seat on a speed, queue
model or series-selection argument. Only Premier-tier market data changes it.
