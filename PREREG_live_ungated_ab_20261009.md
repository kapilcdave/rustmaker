# PREREG — live A/B: does removing the width gate pay, on OUR OWN fills?

Frozen 2026-10-09 before the config change. Author: Claude Opus 5. Operator: kapilcdave.
**REAL ORDERS, REAL MONEY.** Bounded by the drawdown budget the operator already authorised.

## 1. Why this is the next action and not a deposit

`kalshi-scalp/FINDINGS_narrow_book_oos_20261009.md` establishes that the narrow book — the ~85% of
fills `--penny-room 4` declines — pays **+0.2312 c/ct [+0.0725, +0.3883], t 2.89** on 5.0 M real
prints across two disjoint windows. `kalshi-mm15/FINDINGS_instrument_residual_20261009.md`
establishes that **our own fill-model instrument scores that same population at −0.3853
[−0.715, −0.060] in the same fortnight.**

**Two instruments, same fills, same window, opposite signs, 0.73 c/ct apart. Nothing in this repo
can size the seat, because the two things that could disagree.** The print tape is a population
average over participants it cannot identify; the sim is our own fill model, which has already been
wrong twice this month (`a-fill-models-live-condition-was-a-collector-staleness-proxy`,
`a-sweep-is-one-fill-opportunity-reported-as-many-prints`).

**The live seat is the only instrument that resolves it, because it fills with our own orders in our
own queue position.** And it is nearly free: at clip 1 the whole question costs the existing cap.

**Why NOT deposit first.** The $211/day figure is a **clip-300** number requiring **~$4,926**:

| clip | capital needed | ct/market | $/day at +0.2312 |
| ---: | ---: | ---: | ---: |
| 1 | $16 | 3 | $0.69 |
| 10 | $163 | 33 | $6.94 |
| 100 | $1,626 | 330 | $69 |
| **300** | **$4,926** | **990** | **$208** |

Account today is **$59.52**, which supports clip ~3.7 ≈ **$2.54/day**. So the headline is a
**deposit decision of ~$5k**, and committing $5k on the strength of a population statistic that our
own backtest contradicts by the wrong sign is the exact mistake
`research-direction-preference` exists to prevent ("price the prize before running the experiment").
**This A/B is what makes that $5k call informed instead of a coin flip.**

## 2. The change, and it is one environment variable

`PENNY_ROOM` 4 → **2**. The gate is `ask - bid >= penny_room * tick` (`live.rs:1456`), a **floor,
not a band**, and the mid-band tick is 1c with an observed minimum spread of 2 ticks — so
`PENNY_ROOM=2` is the **ungated** seat. That is the right arm: the claim is not "narrow books are
better than wide", it is "the gate declines 85% of fills that pay, so remove it".

Everything else held fixed and declared: 9 series, `--amend`, clip 1, max-pos 1, spot 2 bps,
group-limit 12, stop-before-close 450 s, open band 0–100, `MAX_DD_C=3200`, `SESSION_CAP_C=3200`,
`MIN_HEAD_C=250`, binary `../kalshi-mm15-penny4/kalshi-mm15-amend`.

**Risk delta, stated honestly.** Fill rate is expected to rise ~5× (2-tick books are 62% of the
sim's fillable rows vs the 15% at ≥4 ticks). Exposure does **not** rise — max-pos stays 1, so
collateral stays 900c and the absorbing barrier stays the already-authorised 3200c. What rises is
the *rate* at which the cap is approached if the sim is right. **If the sim is right this spends up
to $32 and answers the question; if the tape is right it is the first positive seat in this corpus.**

## 3. Power, declared before arming

The two instruments differ by **0.73 c/ct**. At the live seat's measured ~3.3 ct/market and
4.6 markets/round, that is **≈11 c/round**, against a round sd of **82.57c**. Distinguishing them at
t=2 needs

    n = (2 * 82.57 / 11)^2 ≈ 225 rounds ≈ 2.4 days at the realised 40% duty

**This is tractable, and that is the whole point** — unlike establishing the seat's own edge
(+3.24 c/round, t +0.87), which needs ~2,600 rounds / 67 days
(`the-15m-penny-seat-is-cap-limited-not-edge-limited`). **I am not testing whether the seat is
profitable. I am testing which of two instruments is lying**, which is a 7× bigger effect and
therefore 50× cheaper to measure.

## 4. Decision rule, frozen

Scored on **c/ct over ungated fills**, market-clustered, after **≥225 rounds** or a cap trip,
whichever comes first. The pre-existing room-4 fill history (`ruin.py`, 493 rounds) is the control
arm; the comparison is unpaired and that is declared.

| outcome | verdict |
| --- | --- |
| ungated c/ct **≥ +0.10** | the tape is right, the gate was wrong. **THEN** price the deposit: clip 300 for $208/day, staged, re-scored at each clip |
| ungated c/ct **in (−0.10, +0.10)** | a zero. The gate is harmless and the narrow book is not income at our queue position. No deposit |
| ungated c/ct **≤ −0.10** | the sim is right, the print-tape population average does not transfer to a one-sided touch maker. **Revert to room 4, close the branch, and the $211/day is retired** |
| cap trips before 225 rounds | the sim is right in the worst way; revert and close |

**Pre-committed so it cannot be re-derived later:** a positive result does **not** license the full
$5k. It licenses clip 10 (~$163, ~$7/day), re-scored, then clip 30, then clip 100 — each stage
paying for the next. `kalshi-wing-is-net-zero` measured edge **anti-correlated** with attainable
volume (b = −0.1555, t = −11.47), so **the per-contract edge must be re-measured at every clip** and
is expected to decay. The clip-300 row of the table above assumes it does not, which is why that row
is a ceiling and not a forecast.

## 5. Safety, and what is explicitly NOT changed

- No deposit, no withdrawal, no shard transfer, no credential change.
- `MAX_DD_C` unchanged at the operator-authorised 3200c. **I am not raising a risk budget.**
- Revert is `PENNY_ROOM=4` and a restart — one variable, no code change, no rebuild.
- Pre-arm checks required and recorded: 0 resting orders, 0 open positions, engine+supervisor down,
  watchdog alive, no `STOP`, preflight passes its own collateral+cap floor.
- Stop at any time: `touch /home/admin/trading/kalshi-mm15/STOP`.

## 6. What this licenses

Licenses: the `PENNY_ROOM=2` config on the existing budget, and a staged deposit *decision* if §4
returns the first row. Does NOT license: any deposit, any clip increase, any cap increase, or any
claim that $211/day is attainable — that number needs clip 300 and has never been measured above
clip 1.
