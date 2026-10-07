# FINDINGS — PREREG_open_band FAILS, and the last two weeks of losses are caps and collateral, not the seat (2026-10-07)

Scored offline from 45 live journals held locally (2026-09-24 23:08Z → 10-05 02:33Z) plus the
**public** `/markets/{ticker}` settlement endpoint. No orders were placed and no credential was
used. Instrument: `score_open_band.py`; fills `data/leftover/band_fills.gz` (14,370 rows),
settlements `data/leftover/results.json` (2,157 tickers, **all finalized, zero fetch failures**).

`PREREG_open_band.md` was frozen 2026-09-30 09:20Z and asked for 300 settled markets under the
`--open-min-c 10 --open-max-c 90` band. 440 crypto markets have accumulated. **It had never been
scored.**

---

## Verdict

| | n mkts | ct | total | c/mkt | lo95 (mkt / round) | H1 / H2 | c/ct |
|---|---:|---:|---:|---:|---|---|---:|
| **BAND (prereg primary)** | 440 | 1,615 | **−$5.46** | −1.241 | −6.00 / −5.87 | −1.43 / −1.05 | −0.338 |
| **NO BAND, clip 1, round-net 2** | 585 | 2,990 | **+$16.64** | +2.845 | **+0.19 / +0.16** | +3.02 / +2.67 | +0.557 |
| no band, clip 2 (06:44Z) | 60 | 560 | +$0.90 | +1.505 | −10.3 / −11.1 | — | +0.161 |
| band, penny room 3 | 83 | 451 | +$0.80 | +0.964 | −7.6 | — | +0.177 |
| pre-round-net era | 855 | 8,511 | +$5.98 | +0.700 | −1.52 / −1.73 | +1.55 / −0.15 | +0.070 |
| band, commodities | 89 | 225 | +$3.89 | +4.376 | −5.76 | — | +1.725 |
| **all-time seat, crypto 15M** | **2,023** | **14,129** | **+$18.87** | +0.933 | | | +0.134 |

**PREREG_open_band → FAIL.** The pass condition was `lower95 > 0` **and** both chronological
halves `> 0`. The band population delivers `lower95 = −6.00` with **both halves negative**.
Concentration is not the reason: minus-best-k tracks a normal with the same mean and sd to within
$1 at k = 0/5/10/20, so the band population is simply negative, not tail-damaged.

**DOGE/SOL holdout → KEEP BOTH.** The prereg's frozen rule (fills after 2026-09-30 06:44Z only;
drop only if both are < 0 c/ct *and* the pooled pair is < 0 at one-sided p < 0.10) returns
DOGE −0.818 c/ct, SOL **+0.107** c/ct, pooled −0.251 c/ct at **p = 0.436**. The in-sample
"DOGE/SOL are negative in both halves" result does not replicate — the third instance in this
corpus of `in-sample-controls-cannot-detect-selection`.

## ⛔ MECHANISM: a gate measured in TICKS and a band measured in CENTS are not composable on a tapered ladder

`KX*15M` is `tapered_deci_cent` — verified live on the venue payload:
`price_ranges = [0→0.10 step 0.0010, 0.10→0.90 step 0.0100, 0.90→1.00 step 0.0010]`. So the tick is
**0.1c in the wings and 1c in the middle**, and `--penny-room 4` therefore means **0.4c of required
spread in the wings but 4.0c in the mid-band.**

Measured on **10.7 million two-sided book states** from two real no-band runs:

| region | share of states | mean spread | room-4 pass rate | share of all room-4 passes |
|---|---:|---:|---:|---:|
| wing < 10c | 13.0% / 11.4% | 0.50c / 0.56c | **34.0% / 39.0%** | 33.7% / 29.4% |
| mid 10–90c | 75.0% / 68.0% | 1.51c / 1.45c | **6.3% / 5.2%** | 35.9% / 23.7% |
| wing > 90c | 12.0% / 20.7% | 0.49c / 0.45c | **33.3% / 34.2%** | 30.4% / 47.0% |

(run 12, 7.75 h, 8,497,559 states | 09-30 06:44Z, 2.3 h, 2,225,386 states)

**64% and 76% of every room-4 pass is in the wings**, on 25–32% of the states. The 10–90c opening
band forbade opening in exactly the price region where the rule fires, so it removed roughly
**two thirds of the seat's opportunity set**. That is visible directly in the engine counters:
fills per post ran 1.3–2.7% before the band and **0.4–0.7%** after it.

The in-sample case for the band ("wing fills settled ≈0 c/ct on 60% of capped fills") was a
**per-contract** argument, and it was not wrong — it was applied to a rule whose opportunity set is
defined in **ticks** on a ladder whose tick changes by 10× at 10c and 90c. The band deleted the
volume and the surviving mid-band per-contract edge did not improve to compensate
(band clip 1 −0.644 c/ct vs no-band clip 1 +0.557 c/ct).

**How to apply:** never compose a tick-denominated width gate with a cent-denominated price band
without first printing the gate's pass rate per tick regime. The same arithmetic says `--penny-room`
is really two different rules — a 0.4c rule in the wings and a 4c rule in the middle — and the
corpus has never scored them separately.

## The band is not identified as the cause, and that is the point

The 09-30 09:07Z change bundled four things, so the band cohort is not a band measurement:
the band, 9 → **17 series** (commodities + CRYPTOLEAD), two penny-room-3 interludes, and session
caps cut to **$1–2**. The one contrast that holds everything else fixed — clip 2, 9 series,
adjacent in time, 06:44Z vs 09:07Z+17:17Z — reads **+0.161 → +0.100 c/ct**, i.e. **no detectable
band effect in either direction**.

So the band is not the villain. It is a change that bought nothing, failed its own gate, and
should be reverted because the configuration it replaced is the only one with a positive record.

## What the losses after 09-30 actually were

Splitting every cohort by **how its run ended** (the stop row's own reason):

| cohort | completed its window | ctrl-c | killed by a loss cap |
|---|---|---|---|
| no band, clip 1 | n=455 **+$17.65** (+3.88 c/mkt) | n=82 +$1.14 | n=48 −$2.15 (−4.48) |
| band, clip 2 | n=83 **+$4.30** (+5.18 c/mkt) | — | n=30 −$3.63 (−12.10) |
| band, clip 1 | n=67 −$0.07 | n=180 −$2.93 | n=80 −$3.12 (−3.91) |

**Every cap-killed run is −4 to −12 c/mkt; every completed no-band run is strongly positive.** Part
of that is tautological — a cap fires because the run is losing — but the magnitude is not. The
measured round-level distribution of this seat is **mean +16.0c, sd 75.4c, worst −175c, p5 −101.5c,
max drawdown $3.97** over 123 rounds. The 10-02 runs were launched behind a **$1.00 session cap**
and the 09-29/09-30 ones behind $2.00. **A $1 cap on a 75c-sd unit fires on noise**, and when it
fires it locks the loss, strands the position without its exit leg, and blocks re-entry through the
settle wait. Four consecutive 10-02 runs died that way for −$1.00, −$1.07, −$1.05 and a cumulative
trip. This is `price-a-risk-control-at-equal-income-not-equal-clip` and
`a-drawdown-cap-on-cash-measures-exposure-not-loss`, in production, costing money.

**The 10-05 run was a collateral failure, not a trading result.** 19,806 posts, **11,421 rejects,
of which 11,227 are `insufficient`** — 57% of its order attempts were refused for lack of
collateral at a $2.86 balance. Against that: **zero** insufficient rejects across every run from
$8.98 to $46.65 of starting balance, over **1,056,028** posts (31 runs). A two-sided 1-contract quote locks ~$1
per market regardless of price, so 9 series needs **≈ $9 of working collateral** before any
inventory, and the drawdown budget is **≈ $4** on top.

| | measured requirement |
|---|---|
| working collateral, 9 series × clip 1, two-sided | **≈ $9** (0 refusals in 31 runs / 1.06M posts; wall at $2.86) |
| drawdown budget (observed max DD, clip 1) | **≈ $4** |
| session cap that does not fire on noise (≥3 round sd) | **≈ $2.25 minimum, $4 sane** |
| income, runs that completed their window | **+$0.66/h → +$15.83/day at 100% duty** |

## Two instrument defects found, both of which have corrupted published numbers

**1. The `fill` channel is ACCOUNT-WIDE, not order-scoped.** Another rig's fills land in this
engine's journal. A single **30.74-contract taker** fill in `KXMLBTOTAL-26SEP301400PHIATL-8`
carried **−$9.53** inside the 09-30 20:49Z run — 81% of that cohort's loss — and belongs to the
sports runner. Our seat is `post_only`, so `is_taker` is the discriminator (a foreign fill also has
no `client_order_id`); the configured `--series` list is the second. Without both filters a journal
replay silently scores other strategies.

**2. Run 12's "own ledger +$9.86" was itself contaminated, and its seat number is +$10.82.** The
published venue figure is a shard-2 *balance delta*, i.e. account-level, and the commodity runner
shares shard 2 — it included a foreign −$0.96 gold taker fill. Both the venue figure and the old
journal replay agreed *because both contained the contamination*. The reconciliation gate in
`score_open_band.py` now matches the venue at **account level to $0.00 on all three runs with
known ledgers** (run 5 +$5.06, run 9b −$3.05, run 12 +$9.86) and reports the seat-only number
beside it.

## What the candidate number does and does not license

The no-band clip-1 cohort is the strongest live record this seat has ever produced — and its
significance is carried by one run:

| leave out | n | total | c/mkt | lo95 |
|---|---:|---:|---:|---:|
| nothing | 585 | +$16.64 | +2.845 | **+0.193** |
| run 12 (09-28 22:38Z) | 420 | +$5.82 | +1.386 | −1.727 |
| 09-28 (day) | 482 | +$9.22 | +1.912 | −0.999 |
| 09-29 (day) | 161 | +$7.13 | +4.431 | −0.861 |

Minus the best 20 of 585 markets leaves **+$0.38**. Per `the-tail-is-not-skew`, that is a t-stat
statement, not a tail artifact — this seat's per-market P&L is normal-shaped, and the fix is **more
markets at a frozen config**, not reshaping. At ~20 settled markets/hour, resolving the sign needs
roughly **a week of continuous running**, which no run in this history has ever achieved: of 25
configured runs, 7 died on loss caps, 11 were ctrl-c'd, and 1 crashed on a full disk.

**Licensed:** reverting to the frozen pre-band configuration (9 crypto series, clip 1, penny room 4,
`--max-round-net 2`, amend on, `--spot-bps 2`, open band **off**, hold leftovers, 8-hour windows)
with caps sized to the seat's measured round variance rather than to the account, and running it
continuously.

**Not licensed:** clip ≥ 2 (`PREREG_clip_ladder.md` is frozen and still unrun — the one clip-2
cohort without the band is 60 markets), any series selection, dropping DOGE/SOL, and any claim that
the band caused the losses.

## ⚑ Which regime is the seat? Mid-band. The HIGH wing is the only reliable loser.

`score_region_split.py`, every real crypto maker fill (14,046 fills / 14,129 contracts / +$18.87),
split by the tick regime the fill's own YES price sits in. Per-fill settlement P&L is **exactly
additive** — `cash + pos·y = Σ s·ct·(y − px)` — so this is a decomposition of the whole seat, with
no exit model and no pairing assumption. SEs clustered on the round.

| regime | contracts | total | c/ct | lo95 | H1 / H2 |
|---|---:|---:|---:|---:|---|
| wing < 10c | 3,739 | +$3.63 | +0.097 | −0.217 | +0.278 / −0.078 |
| **mid 10–90c** | 5,740 | **+$22.52** | **+0.392** | −0.206 | +0.358 / +0.423 |
| wing > 90c | 4,648 | **−$7.28** | **−0.157** | −0.404 | −0.091 / −0.217 |
| both wings pooled | 8,388 | −$3.65 | −0.044 | −0.238 | +0.078 / −0.158 |

Restricted to the no-band era (the clean read — under the band a wing fill can only be a *reducing*
order): mid 10–90c **+0.668 c/ct, lo95 +0.006, both halves positive** on 3,903 contracts; wing <10c
+0.131 (lo95 −0.155); wing >90c **−0.162, lo95 −0.386, both halves negative** on 4,517 contracts.

**The two wings are not one bucket.** The corpus's standing "wings ≈ 0 on 60% of fills" pooled a
zero (<10c) with a loser (>90c) — `a-pooled-edge-has-no-single-toll` again. And the >90c wing is the
**largest single bucket at 37.4% of all contracts.**

**The leg split names the mechanism.** In the wings the two legs are equal and opposite — <10c
opening **+1.120** / reducing **−0.970**; >90c opening −0.635 / reducing +0.362 — which is what a
round trip with no edge looks like, and per
`fair-value-sits-at-twenty-percent-of-a-wide-ladder-spread` is the signature of a **mid bias rather
than a seat**. The mid-band pays on **both** legs: opening +0.771, reducing +0.530.

**⚠ This decomposition is NOT a counterfactual for a regime-restricted seat.** A position opened at
8c and closed at 12c books its opening P&L in the wing and its exit in the mid-band, so "mid-band
+0.668" includes exits inherited from wing entries. The only actual region-restriction experiment
ever run is the 10–90c band, and it **failed** — for the mechanical reason above: the band keeps the
paying region and throws away the region where a *tick*-denominated gate fires.

**So the next test is one change, not two half-changes:** restrict to the mid-band **and** denominate
the room requirement in **cents**, so the rule still fires there. In the mid-band a ≥2c room passes
**23.8–25.0%** of book states against room-4's 5.2–6.3% — about 4× the opportunity the band allowed.
That needs an engine flag and its own preregistration; it must **not** be justified by flipping
`--open-max-c 90` on the table above, because deleting an in-sample losing bucket is precisely the
reasoning that produced the failed band (`in-sample-controls-cannot-detect-selection`).

## The speed question, answered against this seat's own numbers

The az2 box with REST IP pinning and the Ed25519 signer reacts in **8.2–8.5 ms** (post) / **8.2 ms**
(cancel), against 9.3/8.8 ms on az1 — a 1.1 ms gain, of which our entire decision path is **44 µs**.
On this seat's own tick tape, 1 ms of reaction is worth **0.002–0.003 c/ct**
(`FINDINGS_idea_battery_20261006.md`, §tick follow-up), so the whole 1.1 ms is **≈0.003 c/ct against
a seat that earns +0.557 c/ct — about half a percent.** The cancel-side bound agrees: the
same-price sweep tail is 5.67% of contracts and pulling *all* of it recovers **+0.089 c/ct**
(`adverse-selection-lives-in-the-sweep-tail`).

**The rig's contribution is not milliseconds, it is completion.** The only configuration that
cleared `lower95 > 0` is the one whose runs reached their 8-hour deadline instead of dying on a cap,
a disk, or a credential. Every binding constraint found here is operational: cap sizing, working
collateral, and config churn.

## Reproduce

```bash
python3 -I /tmp/fetch_band_results.py     # public settlements, cached
python3 score_open_band.py                 # gate 0 must say VALIDATED before any number is read
```
