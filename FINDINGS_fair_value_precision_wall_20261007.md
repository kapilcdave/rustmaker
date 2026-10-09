# The model-priced 15M maker: sigma CANCELS, and the gate is the PAIR RATE, not speed or width

Date: 2026-10-07. Code: `src/fairvalue.rs` (24 unit tests), wired into `src/live.rs` behind
`--fair`, **unarmed**. Measurement: `score_index_level_precision.py` on the az2 box against the
existing 2026-10-07 tapes (10 min of all nine CF indices, both channels, 17,400 prints; 10 h of
10-venue spot). `score_fair_value_precision.py` is the public-data version, no credentials.

> ## ⚠ RETRACTION of this file's first version, same day
>
> The first version claimed the level term was **~25× the available gross** and that the mid band
> was closed on price resolution. **That magnitude was wrong by about 10×, in the pessimistic
> direction, and the mechanism was misattributed.** It measured our spot against a *minute-bar
> Coinbase close*, which is up to 60 s stale, and read that staleness as our achievable precision.
> With the actual CF index feed the level is known to **0.15-0.42 bp**, not ~1-6 bp, and the true
> multiple at the money is **2.6×**, not 25×. The corrected figures are below; the old ones must
> not support any sizing. What survives unchanged: the width gate is the wrong control, and the
> admissible region is a band in `|z|`.

## Why this was built

The operator's reading: *we are fast enough; price it properly with a real market-making model
instead of waiting for a 4-tick spread.* Measured verdict: **both halves of that are right**, and
the thing that decides the seat turned out to be neither.

**The width gate is the wrong control** (not re-derived; cited). Real-print maker gross is flat at
+0.249 / +0.264 / +0.252 / +0.240 / +0.244 c/ct across 0-1, 1-2, 2-3, 3-5, 5-10 c of spread, on
8,038,551 prints. The gradient it was built on came from a fill model that overstated adverse
selection **5.17×**. `penny_room` of 4 c fires in **5-6% of mid-band states**. And it cost the
exits: on the 2026-10-07 armed session (544 fills, −$5.76) pairs earned **+$0.42** while the whole
**−$6.18** loss sat in settlement-held positions, **95.5%** of which had a profitable exit print
arrive later — behind that same gate. Removed, crypto-only, under `--fair`.

**Local speed is finished as a lever.** Decision path **45 µs**. Of the 38 ms from CF computing an
index to us seeing it, **1.6 ms is ours** (CF→Kalshi 21-34, Kalshi queue 3, Kalshi→us 1.6).

## The structural result: sigma cancels out of the margin

A 15M return-strike digital has one scale, `sigma_eff = sigma*sqrt(tau - 2w/3)` (the 60 s
settlement average removes exactly `2w/3 = 40 s` of variance — first-order, not a refinement).
Two tolls follow, and **neither contains a volatility term**:

```
delta        = 100*phi(z) / sigma_eff                      cents per bp of level
level error  = sigma * sqrt(staleness)                     bp
level toll   = delta * level error
             = 100*phi(z)*sqrt(staleness / (tau - 40))     <-- sigma CANCELLED
latency toll = 100*phi(z)*sqrt(reaction  / (tau - 40))

margin_required(z) = 100*phi(z)*(sqrt(staleness) + sqrt(reaction)) / sqrt(tau - 40)
```

Both the price's sensitivity and the level's error scale with the same `sigma`, so it divides out
exactly. This is `return-strike-markets-are-scale-invariant` applied to the **margin** rather than
to the price, and it is asserted in the scorer against the per-asset empirical path (the assertion
passes, which is why every 200 ms asset below reads an identical number despite index vols
spanning **22.1% to 64.1%** annual).

**Consequences.** The required margin cannot be improved by picking a calmer asset, a calmer hour,
or a better vol model. It depends on exactly two things: **how stale the level is** and **how much
time is left**.

## What is measured

Index staleness is `transport + gap/2`, the average over the publish cycle — print-to-print is the
*worst* moment, not the mean, because at CF-time `s(k+1)` the freshest print we hold is `k`.

| cadence | assets | index vol (annual) | mean staleness | level error |
|---|---|---:|---:|---:|
| 200 ms | BTC ETH SOL XRP DOGE | 22.1 - 64.1% | **138 ms** | 0.15 - 0.42 bp |
| 1 s | BNB HYPE NEAR ZEC | 31.0 - 118.0% | **538 ms** | 0.40 - 1.54 bp |

Required margin at the money, and its multiple of the **+0.25 c/ct settlement-held** gross:

| cadence | `tau`=900 | `tau`=600 | `tau`=300 |
|---|---:|---:|---:|
| 200 ms | **0.65c  2.6×** | 0.81c  3.2× | 1.19c  4.7× |
| 1 s | 1.14c  4.6× | 1.42c  5.7× | 2.08c  8.3× |

**The spot feed adds nothing to the level.** Carrying the index forward by the multi-venue median
spot return beats holding it unchanged by **0%** on all five 200 ms assets, and only 8-16% at the
median (28% at p90) on the 1 s assets. A clock-offset sweep from −200 ms to +200 ms is **flat to
three decimals**, so this is not a mis-alignment artifact: at its own cadence the index is its own
best predictor. Reading the underlying upstream of Kalshi's publisher — the thing that makes "we
are fast enough" true — does **not** convert into level precision.

⚠ The median is the wrong statistic here and nearly cost the finding. Several indices publish on a
lattice coarse relative to their moves: **SOL is 80.7% byte-identical print-to-print** on a 0.846
bp quantum, so its median error is **exactly 0.000** — pure quantization, not precision. RMS
throughout.

## The inversion, and the one thing that is genuinely closed

Solve the inequality for freshness. Budget `= gross*sqrt(tau-40)/(100*phi(0))`, spent on
`sqrt(staleness) + sqrt(reaction)`:

| `tau` | budget | our reaction | left for staleness | max staleness |
|---|---:|---:|---:|---:|
| 900 | 0.1838 | 0.1077 | 0.0761 | **5.8 ms** |
| 600 | 0.1483 | 0.1077 | 0.0406 | **1.6 ms** |
| 300 | 0.1010 | 0.1077 | −0.0067 | **NONE** |

**The measured CF→us transport alone is 38 ms, `sqrt` = 0.1949 — 1.06× the entire `tau`=900
budget.** A perfectly fresh index, read the instant it arrives with zero reaction and zero print
gap, already exceeds it. And of that 38 ms we own **1.6 ms**; the rest is CF's own publish path
and Kalshi's queue.

So **against settlement-held inventory, at the money, the seat is closed by something we do not
own and cannot buy** — not by our speed, not by the width gate, not by model quality. The
reopening condition is being inside CF's publish path.

## What is NOT closed, and it is the operator's thesis

The 0.25 c/ct gross is the **settlement-held** number. A two-sided maker that *pairs* earns the
quoted spread by identity — measured **+1.1068 c/round trip [+1.0186, +1.1983], positive 8/8
series**. Against a 1.00 c round trip the same inequality reads:

| gross | cadence | `tau`=900 | `tau`=600 | `tau`=300 |
|---|---|---|---|---|
| 0.25c (held) | 200 ms | outside 8.3c / 91.7c | outside 6.3c / 93.7c | outside 3.9c / 96.1c |
| **1.00c (paired)** | **200 ms** | **ENTIRE mid band** | **ENTIRE mid band** | outside 28.0c / 72.0c |
| 1.00c (paired) | 1 s | outside 30.2c / 69.8c | outside 20.2c / 79.8c | outside 11.3c / 88.7c |

**At `tau` >= 600 s on the five 200 ms-cadence assets, a model-priced continuous two-sided quote
clears its own derived tolls everywhere in the mid band — if it pairs.** The gate is therefore the
**pair rate**, which is already the corpus's own decision statistic for this seat
(`cost-per-pair-against-one-dollar-is-the-makers-decision-statistic`;
`crossed-share-is-the-only-lever-and-break-even-is-twelve-percent`, break-even crossed share
**<16.0%**).

That is a sharper and more favourable conclusion than the width gate ever permitted, and it is the
operator's reading with one substitution: **not "quote always and collect the spread", but "quote
always and PAIR — carrying is what cannot pay."** It also explains the armed session directly:
pairs made +$0.42, settlement-held lost −$6.18.

## Status

- Width gate **removed** on the crypto seat; model pricing implemented with the averaging
  correction, index-anchored level, inventory skew, and both derived tolls. 24 unit tests.
- A-S's spread term **deliberately unused**: its `k` is unidentified here (hazard ratio inverts
  past 8 s of resting, negative in 3 of 8 age bins, implied optimum brackets what the venue
  quotes) and collapses to `1/k` γ-free on a 1 c lattice. Only the reservation-price skew is used.
- `--fair-gross-c` is the knob that encodes **which income is being claimed**. Default **0.25**
  (settlement-held, conservative); **1.0** asserts the paired round trip and must not be set
  without a pair-rate measurement to back it.
- `--fair` is **crypto-only**, start-up-refuses on sports (width *is* income there, peaking at
  10-20 c, 7/7 positive week clusters), and refuses every way of being silently inert (five guards
  verified firing).
- **NOT ARMED.** The next step is a pair-rate measurement, not a build: the 200 ms assets at
  `tau` >= 600 s are admissible *conditional on pairing*, and nothing here measures the pair rate
  a continuously-quoting model-priced seat would actually achieve.

⚠ **Power.** 10 minutes of index tape, 17,400 prints, one window. The load-bearing results are
arithmetic (the `sigma` cancellation is exact and asserted; the transport figures are from a
separate 45,600-frame capture), so the sample supports them. The **staleness** numbers are one
10-minute window on nine assets and should be re-measured over a longer capture before sizing.

Reproduce: `python3 -I score_index_level_precision.py --index <index.jsonl.gz> --spot <altfeed.csv.gz>`
