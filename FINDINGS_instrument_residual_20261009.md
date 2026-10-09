# FINDINGS — the 15M instrument's residual is REAL, signed DOWN, and localized to the
# narrow book that the width gate exists to decline

Prereg `PREREG_instrument_residual_20261009.md` (committed `9364d1f`, frozen before any outcome
statistic was computed). Scorer `research_instrument_residual.py`, run once, report
`reports/instrument_residual.json`. Dataset `ml/data/mk5s-v5` — 146,080 fills / 1,666 markets / 8
non-BTC 15M crypto series / `px_c ∈ [15,85]` / 2026-09-23 → 10-07. **$0, no collector, no
credential, no capital, no holdout burned** (`ml/data/holdout-01` untouched).

This answers the one open term `FINDINGS_rlmm_holdout_20261008.md` §7 left behind:

> "the remaining term is the instrument's residual understatement against the real print ledger,
> not the policy."

## 0. Both frozen controls passed

| control | result |
| --- | --- |
| **C1** settlement provenance | **PASS** — 0 null `result_yes` in 275,226 rows, mean 0.5096 |
| **C2** the identity the study rests on | **PASS** — `max abs(s·(px_c − 100·result_yes) − settle_c) = 0.00e+00`, exactly, on 100% of rows |

C2 matters: it verifies by computation, not by reading `ml/features.py:285`, that **the sim already
carries the real-print ledger's own gross estimator** `maker_gross_settle = s·(P − 100·y)` with
`MAKER_FEE_C = 0.0`. The two instruments disagree about *which fills happen*; they use identical
arithmetic to value one. **So the entire residual is attributable to the fill population**, and that
is what made this study a groupby instead of a collector.

## 1. The "2.1× understatement" was a horizon error and does not exist as stated

The residual has been carried in this corpus as *"the sim reads ≈0 where the tape reads +0.254, so
the sim understates by 2.1×."* Those two numbers are **different horizons**: the tape's is held to
settlement, the sim's headline is a 5-second markout. On one identical fill set:

| horizon | sim, c/ct | real print tape |
| --- | --- | --- |
| **5 s markout** (`mk5s_c`) | **−0.0007** [−0.0374, +0.0344] | not measurable from prints |
| 60 s markout (`mk60s_c`) | +0.0248 [−0.1149, +0.1624] | +0.176 at the k=1 (90 s) bar |
| **settlement** (`settle_c`) | **+0.4583** [+0.1107, +0.7992] | **+0.254** [+0.118, +0.386] |

**P1 passes as preregistered** — the sim's settlement CI overlaps the population band
[+0.118, +0.386] *and* the touch-maker band [+0.0415, +0.6501]. Taken at face value the instrument
is vindicated and reads, if anything, **high**.

**The pass is an artifact, and §2 is why.** But the negative conclusion survives it: there is no
multiplicative 2.1× understatement to recover. The number that looked like a 2× gap was a 5-second
markout being compared to a 15-minute hold.

**Incidentally measured, and it is the tightest such number in the corpus:** the ungated 15M maker
at 5 s is **−0.0007 ± 0.035 c/ct** on 146,080 fills. That sharpens the holdout study's
"≈0, within ±0.13 c/ct across three windows" by **4×** and confirms its withdrawal of the
"ungated maker is profitable" claim.

## 2. P1's pass is carried by 4.5% of fills, and they are a directional bet

P1 arrived with an internal contradiction the prereg's §7 anticipated: the per-market reducer read
**+0.4583** while the pooled-flat row mean read **+0.0307**, and **all five price bands read
negative**. A mean of per-market means is not additive over row subsets, so that combination is
arithmetically possible — and it localizes the level to low-row-count markets. It does:

| per-market row-count decile | 1–18 | 19–30 | 31–43 | … | 113–139 | 140–183 | 184–542 |
| --- | ---: | ---: | ---: | --- | ---: | ---: | ---: |
| mean `settle_c` | **+1.587** | +2.023 | +0.893 | … | −0.477 | −0.342 | **−0.196** |

Monotone. Splitting instead on how **one-sided** the quoter's fill set was in each market
(`|mean s|`, where 0 is perfectly two-sided):

| | share of markets | share of fills | `settle_c` | `mk60s_c` | `mk5s_c` | contribution to the +0.4583 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| **two-sided** `|s|≤0.20` | 83.4% | **95.5%** | **−0.1137** [−0.323,+0.097] | **−0.1431** [−0.255,−0.031] | +0.0025 [−0.031,+0.036] | **−0.0949** |
| **one-sided** `|s|>0.20` | 16.6% | **4.5%** | **+3.3394** [+1.582,+5.081] | +0.8707 [+0.274,+1.484] | −0.0170 [−0.159,+0.124] | **+0.5532** |

**4.5% of fills carry +0.553 of a +0.458 level; the other 95.5% carry −0.095.** Being one-sided and
having few fills is the same condition here, so the equal-weight-per-market reducer — the ledger's
own reducer — up-weights exactly the markets where the position was most directional.

**A one-sided fill set held to settlement is not maker capture. It is an unhedged bet on a
15-minute binary** (`a-maker-with-a-crossing-exit-is-a-directional-bet`,
`an-offsetting-pair-self-liquidates-at-one-dollar`). The giveaway is the third column: that tail is
worth **+3.34 c/ct at settlement and −0.017 c/ct at 5 s**. None of it is capture; all of it is the
terminal payoff of carrying inventory to expiry.

**C3 confirms the mechanism independently.** The circular-shift null (roll `result_yes` by market
within series, turnover and fill set held exactly fixed) pays **−1.2906** [−1.802, −0.800]. Our
level sits **+1.75 above** its own null — the opposite relationship to the tape, whose +0.254 sits
*below* a +0.674 null. A fill set that loses 1.29 c/ct against a randomized outcome is positioned on
the price path, which is what a directional position is.

## 3. The real finding: the tape and the sim disagree on the NARROW book, by 0.6 c/ct, with
## non-overlapping intervals

Restricting to **two-sided** markets removes the directional tail, and the disagreement with the
venue's print tape becomes sharp and localized. The tape's strongest structural result is that the
real maker earns **the same ~+0.25 c/ct resting in a 1 c book as in a 10 c book** (+0.249 / +0.264 /
+0.252 / +0.240 / +0.244 across 0-1 / 1-2 / 2-3 / 3-5 / 5-10 c).

| `spread_ticks` | share of fills | tape, settlement | **sim two-sided, settlement** | sim two-sided, 5 s |
| --- | ---: | ---: | ---: | ---: |
| **2** | **62%** | ~**+0.25** | **−0.3853** [−0.715, **−0.060**] | −0.2781 [−0.321, −0.237] |
| 3 | 22% | ~+0.25 | −0.2813 [−1.120, +0.544] | +0.1913 [+0.096, +0.285] |
| 4 | 9% | ~+0.25 | +0.0395 [−1.290, +1.380] | +0.7551 [+0.586, +0.926] |
| 5-6 | 4% | ~+0.25 | +1.1063 [−0.736, +2.966] | +1.0412 [+0.791, +1.294] |
| 7+ | 1% | ~+0.25 | +1.3760 [−2.494, +5.199] | +2.2887 [+1.783, +2.791] |

**On the 2-tick book — 62% of all fills — the tape says +0.25 and the sim says −0.39, and the sim's
interval excludes zero.** That is a **0.63 c/ct disagreement, signed down, on the modal fill**, and
it is the only place the two instruments disagree: by 5-6 ticks they agree inside noise.

⚠ **This corrects my own intermediate reading in this session.** The *pooled* 2-tick settlement
number is +0.2179, which agrees with the tape to 0.03 c/ct and briefly looked like clean
vindication. It is the one-sided tail of §2 leaking in. Crossing the two diagnostics flips the sign.
The pooled cell is in the report as `D2`; `D3` is the one to quote.

### Which indicts the gate, in the gate's own units

`room4` = `spread_ticks ≥ 4` exists to decline narrow books. Two-sided markets only:

| `room4`'s decision | share of fills | sim, 5 s markout | sim, settlement | tape says |
| --- | ---: | ---: | ---: | ---: |
| **DECLINES** (<4 ticks) | **84.8%** | −0.1486 [−0.184, −0.114] | **−0.3571** [−0.595, −0.119] | **~+0.25** |
| TAKES (≥4 ticks) | 15.2% | +0.8617 [+0.698, +1.027] | +0.3912 [−0.869, +1.630] | ~+0.25 |

The gate is **internally consistent inside the sim** — it declines the set the sim scores negative
and takes the set it scores positive. The problem is that **the venue's own print tape scores the
declined 85% at +0.25, not −0.36.**

### And the gradient the gate rests on does not survive its own horizon

Same rows, three horizons, frozen bucket scheme:

| `spread_ticks` | 2 | 3 | 4 | 5-6 | 7-9 | 10+ | slope (c/ct per tick) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| **`mk5s_c`** | **−0.256** | +0.165 | +0.730 | +1.080 | +2.213 | +5.007 | **+0.512**, all 6 CIs tight and non-overlapping |
| `mk60s_c` | −0.259 | +0.186 | +0.342 | +0.859 | +0.652 | +7.024 | +0.654, only 2 of 6 exclude 0 |
| `settle_c` | +0.218 | +0.103 | +0.623 | +1.454 | +2.214 | +0.672 | **+0.096, CI [−1.175, +1.345]** |

**P2 passes** — the sim reproduces flat-in-spread at settlement (slope CI includes zero). But read
with the row above it, the pass means something sharper than the prereg anticipated: **the gradient
is a 5-second-markout phenomenon.** It is overwhelming and monotone at 5 s, not established at
60 s, and gone at settlement, where the tape independently reads flat.

**A capture that does not survive its own horizon is a mid artifact.** The mid of a 10 c-wide book
is the midpoint of two prices nobody traded, so "earning half the spread against the mid" in a wide
book measures the mid's own noise, not income — which is
`a-fifteen-minute-binarys-price-resolution-is-coarser-than-its-spread` and
`quoted-spread-prices-its-own-adverse-selection`, now visible as a horizon ladder on one fill set.
The tape said the same thing in one sentence and we did not act on it: *"capture rises with spread
mechanically ⇒ adverse rises in lockstep, so no width gate can manufacture an edge."*

## 4. Secondaries

**Per series: 7 of 8 positive at settlement, matching the ledger's 7 of 8** — but not the same
seven. The sign ranking does not transfer across the disjoint windows (ledger's negative was HYPE,
which is our second-best at +0.943).

| | ETH | SOL | XRP | DOGE | HYPE | ZEC | NEAR | BNB |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `settle_c` | **−0.438** | +0.450 | +0.164 | +0.751 | +0.943 | +0.327 | **+1.177** | +0.490 |

⚑ **The single negative series is ETH**, independently reproduced here from a different instrument
and a different statistic than the two that already flagged it: ETH is **−4.39 c/round (t −1.62)**
in the live seat's own fill history, and `eth-15m-gated-maker-closed-same-as-btc` closed it
outright. **ETH is live in the armed seat right now.** NEAR is the best cell, consistent with
`series-count-costs-rope-linearly-and-buys-sharpe-sublinearly` (a prior-chosen ZEC+NEAR pair held
90% of the seat's all-time P&L on 37% of its markets).

**KXBTC15M**, descriptor only, excluded from every gate: +2.111 [−2.621, +6.771] on 159 markets —
uninformative, as expected at that market count.

## 5. Verdict

**The open term is resolved, and the answer is three-part.**

1. **There is no multiplicative 2.1× understatement.** That comparison was a 5-second markout
   against a 15-minute hold. The claim should be struck from the corpus.
2. **There IS a residual, it is additive, signed DOWN, and worth ~0.6 c/ct — but only in the
   2-tick book**, where the tape reads +0.25 and the sim reads −0.39 with non-overlapping
   intervals. Everywhere wider, the two instruments agree inside noise.
3. **The width gate's edge is a mid artifact.** Its entire gradient lives in the 5-second markout
   and vanishes by settlement, where the venue's print tape independently reads flat. So `room4`,
   `spread_ticks ≥ 4`, the RL critic's ranking and the `+9.0 c/market` bar are all denominated in a
   statistic that disagrees with the venue by 0.6 c/ct on 85% of the fills they decline.

**Does this reopen the branch? No — and the prereg pre-stated the test so this could not be spun.**
§8: "the RL edge is +0.910, so the residual factor would have to exceed 9.9× to reach the bar."
An **additive** level shift is worse than that for reopening: it applies to both arms of a paired
difference and so changes `c51 − room4 = +0.910` by **nothing at all**. It moves the *seat's* level,
not the agent's edge. The RLMM branch stays closed on magnitude, on six measured dimensions, exactly
as `FINDINGS_rlmm_holdout_20261008.md` left it.

**What it does change is which seat is worth measuring.** If the tape is right that narrow books pay
+0.25 c/ct, the gate is declining 85% of the available fills — and the 2-tick book holds 86,274 of
our 146,080 fills, roughly **62 fills/market at clip 1 against the ~6 fills/market the live seat
currently gets**. That product is the first thing in this family whose arithmetic reaches the
`+9.0 c/market` bar, and it reaches it through **turnover at a small positive**, not through an edge
— which is Shape A in `HANDOFF-ASTRA.md` §0, the one shape that passes the durability filter.

**⛔ And it is probably still not a trade, for a reason already on the record.** The tape's +0.254
sits **below its own +0.674 circular-shift null**. So "narrow books pay +0.25" is not an edge claim;
it says a static price-level effect pays +0.67 and resting in the book costs you 0.42 of it. Any
narrow-book seat has to clear **its own null**, not zero. Nobody has ever run that null restricted
to narrow books.

## 6. The one designated next test, and it is free

**Re-run the real-print ledger restricted to 2-tick books, on the mk5s-v5 window, with its own
circular-shift null.** This is pre-authorized by the prereg §6.4 and costs nothing: the public
unauthenticated `/markets/trades` endpoint, `kalshi-scalp/fetch_real_print_tapes.py`, **$0, no
credential, no order client, no capital.**

It settles both open questions at once, because it closes the two confounds this study could not:

- **Window.** The ledger ran 2026-07-15 → 09-14; mk5s-v5 is 09-23 → 10-07, **disjoint**. A level
  comparison across disjoint windows is exactly what tests an instrument
  (`cross-window-level-agreement-tests-an-instrument-a-paired-diff-cannot`), but it cannot separate
  "instrument differs" from "window differs" without this arm.
- **Width.** The ledger never published a narrow-book cell against a narrow-book null, and that
  single cell decides whether the 0.6 c/ct residual is money or a mirage.

Pre-stated decision rule, so the follow-up inherits this study's discipline:

| 2-tick real prints, settlement | verdict |
| --- | --- |
| **above** its own narrow-book null | the residual is money; the gate is wrong; price an ungated narrow-book seat on turnover |
| **at or below** its own null | the residual is a static price-level effect; the 15M maker family is closed on the instrument too, and the corpus stops carrying an open term |

**Population caveat to carry into it, from the ledger's own text:** 88.4% of its contracts sit at
prices no resting touch order held, the touch-maker number is +0.3473 with only **5 of 8** series
positive, and two-sided self-liquidating makers measured at +1.949 c/ct are *inside* its average —
so "a one-sided maker should expect less than +0.254."

## 7. Licenses nothing operational

No arming, no capital, no config change, no `$/day` claim, no reopening of RLMM on policy,
architecture, features, risk measure, atoms or exploration. Per prereg §8.

**One operational note that is not a licence but should not be lost:** ETH is the only negative
series here, it is negative in the live seat's own fill history at −4.39 c/round, it is closed
outright by `eth-15m-gated-maker-closed-same-as-btc`, and **it is one of the nine series armed on
`i-0f25ca08ce8d9d016` right now.** Dropping ETH and XRP costs nothing and was already flagged as
"the first thing to try if this run disappoints." Three independent instruments now say the same
thing about ETH. That is the operator's call, not this study's.
