# FINDINGS — the fast feed sees the move 368 ms early, and taking it still loses 1.65 c/ct (2026-10-07)

Scores `PREREG_altfeed_venue_race_20261007.md` on the 10h09m capture: **14,627,339 quotes**, 10
venues × 9 assets, 0 dropped, 0 silent cells, from the az2 box. Kalshi book from the concurrent
hourly `gate_probe.sh` tapes; settlement index from a 2 h `probe --index` run. crypto.com excluded
per amendment A1 (449 disconnects).

## Verdict in one line

**The feed is real and is a large improvement as an input: it sees the underlying move a median
368 ms before the Kalshi touch reacts, against the 8.6 ms our order needs. Latency is not the
binding constraint and never was. Taking the stale quote still loses, because the quotes that
survive are the ones that correctly did not need to move.** The surviving use is the maker pull,
which this run does not measure.

## D1/D2 — which venues, per asset. PASSES; no asset dropped.

Keep a venue on an asset iff it won ≥10% of scored race events, ≥30 scored events. Every asset
cleared ≥3 venues, so D2 drops nothing.

| asset | scored events | venues kept, best first |
| --- | ---: | --- |
| NEAR | 3,783 | binance_us 39%, gate 23%, cb_ex 13% |
| ZEC | 938 | cb_ex 24%, bitstamp 19%, gate 18%, binance_us 13% |
| HYPE | 274 | cb_ex 32%, gate 26%, binance_us 16% |
| XRP | 155 | cb_ex 21%, okx 19%, gate 19%, binance_us 12% |
| DOGE | 165 | binance_us 40%, gate 21%, cb_ex 19% |
| ETH | 99 | cb_ex 27%, binance_us 22%, okx 13% |
| SOL | 99 | gate 32%, cb_ex 26%, okx 20% |
| BTC | 32 | cb_ex 47%, okx 16%, gate 13%, binance_us 13% |
| BNB | 30 | gate 20%, okx 17%, cb_adv 13%, gemini 10%, cb_ex 10%, bitstamp 10% |

Per venue: **cb_ex 9/9, gate 8/9, binance_us 7/9, okx 5/9**, bitstamp 2/9, gemini 1/9, cb_adv 1/9,
**kraken 0/9, hyperliquid 0/9**.

### ⚑ Update rate is ANTI-correlated with winning the race

This is the result that could not have been obtained from a feed census, and it reverses the
reading of the update-rate table in `FINDINGS_altfeed_index_path_20261007.md` §3:

| venue | updates/s on its best asset | race events won |
| --- | ---: | ---: |
| **gemini** | **54.0/s** (HYPE), 55.2/s (ZEC) | 8.8%, 5.3% — kept on **1 of 9** |
| **kraken** | **21.8/s** (DOGE), 22.5/s (ZEC) | 2.4%, 5.7% — kept on **0 of 9** |
| cb_ex | **0.1–2.6/s**, the sparsest of all | **12.8–46.9%** — kept on **9 of 9** |

The two chattiest feeds are the two worst at being first, and the sparsest feed is the best. Their
churn is quote flicker at the touch, not price discovery. Coinbase Exchange `ticker` fires only on
a *match*, so it is sparse by construction — and a match is information, which is why it wins.

**This corrects the premise this work started from.** I argued that cb_ex at 0.1 updates/s on
BNB and HYPE meant the engine was "blind", and inferred that the busy venues were the valuable
ones. The first half is right — one feed is not enough, and the leader differs by asset. The
inference was wrong, and only the race could show it. The default `--spot-venues` is now
`cb_ex,gate,binance_us,okx` (kraken removed).

## D3 — the lead over the Kalshi book. PASSES, enormously, and in the right units.

**⛔ First the failure, because its number was briefly reported as a result.** The
cross-correlation route gave the spot reference leading the Kalshi mid by −170..+710 ms at
correlations of **0.002–0.014**, and leading the *settlement index* at **0.012–0.045**. The second
figure is the tell: the CF index is computed from those very venues, so a near-zero correlation
there cannot be a market property. It was the 10 ms grid — a forward-filled bin with no new quote
has a return of exactly zero, so on ~1-update/s feeds almost every surviving pair is
(moved, did-not-move):

| bin | zero-return bins | spot vs index corr |
| ---: | ---: | ---: |
| 10 ms | 87–96% | **0.05–0.09** |
| 250 ms | 20–61% | 0.29–0.48 |
| 5 s | 0.5–10% | **0.84–0.96** |

`xcorr_peak` now **refuses** to return a correlation when >60% of bins have no price change, with
the real numbers above in the comment and a test that reproduces the artifact on a
known-perfect relationship. `altfeed_lead.py` sweeps bin sizes instead of assuming one. Swept
properly, spot vs index is corr 0.36–0.83 and spot vs the Kalshi mid is corr 0.14–0.19 at
500–1000 ms, with BNB/NEAR/ZEC **void** at every bin (0.47–3.1 Kalshi touch rows/s). But
coarsening costs the resolution: all it establishes is |lead| < 500 ms.

**The event study answers it properly.** Time from a consolidated spot move until the threatened
Kalshi touch first changes, on our receipt clock:

| spot move | p10 | p25 | **p50** | p75 | p90 | still up at our 8.6 ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| ≥2 bps | 23 ms | 95 ms | **368 ms** | 1,209 ms | 3,172 ms | **95%** |
| ≥5 bps | 11 | 50 | **199** | 727 | 2,209 | 91% |
| ≥10 bps | 10 | 36 | **126** | 294 | 1,039 | 90% |
| ≥20 bps | 13 | 21 | **23** | 158 | 440 | 86% |

**The lead is 40× our reaction time at the median, and we win 86–95% of races.** Survival falls
monotonically with move size (368 → 23 ms), exactly as it should: informative moves get repriced
fast. D3's 5 ms bar is cleared by two orders of magnitude on every asset.

## D4 — the taker scalp. CLOSED. The September result reproduces at 10× the sample.

Same replay, same fee `ceil(7p(1-p))`, same race rule as `latency_arb.py`; only the signal changed
from one Coinbase socket to the median of ≥3 of nine venues. Net per contract at +60 s, won races:

| threshold | events | won | this run (multi-venue) | 2026-09-27 (single venue) |
| --- | ---: | ---: | ---: | ---: |
| 2 bps | 9,563 | 9,104 | **−1.65 ± 0.14 c** | −1.47 ± 0.12 c |
| 5 bps | 623 | 570 | **−1.34 ± 0.51 c** | −1.38 ± 0.25 c |
| 10 bps | 67 | 60 | −1.31 ± 1.86 c | −1.02 ± 0.60 c |
| 20 bps | 7 | 6 | +1.07 ± 4.96 c (n=6, void) | −3.05 ± 1.02 c |

At 2 bps the estimate is **12 standard errors below zero** and **all 8 assets with a sample are
negative** (BTC −1.19, DOGE −1.32, ETH −1.88, HYPE −1.80, NEAR −1.02, SOL −1.98, XRP −1.68,
ZEC −0.96). Mean fee 1.60 c; depth at the touch 30–81 ct, so size is not the binding term. D4's
promotion bar (positive, SE excluding zero, ≥2 thresholds, ≥200 won events, both halves) fails on
the sign.

**The better signal did not help, and now we know why it cannot.** We win 95% of the races, so
this was never a speed problem — `taker-latency-arb-off-coinbase-does-not-pay` named the mechanism
and it survives a 9-venue upgrade: **winning the race selects the quotes that correctly did not
need to reprice.** The gross before fee is ≈0 and the fee is the entire loss (Wall 1). A faster
feed finds more of the same selected population.

## D5 — amend-only. NOT MEASURED by this run.

Independent of the above, and still only a plumbing claim backed by the October latency bench
(amend→book 2.6–2.9 ms vs cancel 4.79 + create 2.7–3.0). It needs a shadow run with our own
resting orders and a markout; nothing here licenses it. The `how` field on `spot_pull` journal
rows distinguishes amend from the cancel fallback — a run with only one of them has not exercised
both paths.

## What this leaves open

The feed's value is now bounded on one side and not the other. It is **not** a taker edge: that is
closed twice, at 10× sample, with the mechanism named. It is a **pull signal worth hundreds of
milliseconds of warning**, and a maker that pulls 368 ms before the touch moves is a different
object from the one `kalshi-maker-latency-cliff` priced — that table's parameter is *cancel*
latency, and its 0 ms row is clairvoyance about a print that has not happened. Here the spot move
has already happened and is not yet in the Kalshi book. Whether that converts into money is the
next measurement, and it must be a shadow with real resting orders, not a replay.

## Artifacts

`data/altfeed/race_10h.{txt,json}` · `lead_10h.{txt,json}` · `taker_10h.{txt,json}` ·
`altfeed_score.py` · `altfeed_lead.py` · `altfeed_taker.py` · `src/fastspot.rs` ·
`tests/test_altfeed_scorers.py` (17 tests). No order was placed: the `altfeed` verb has no
credential and no order path.
