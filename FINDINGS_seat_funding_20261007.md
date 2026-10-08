# FINDINGS — the 15M penny seat is not edge-limited, it is **cap-limited and under-priced**

Instrument `ruin.py` (new). Inputs: every live penny fill this tree holds — `data/leftover/band_fills.gz`
(45 journals, 2026-09-24 → 10-05) plus `data/tonight/tonight_fills.txt.gz` (the 10-07 armed run) —
settled against the **public** `/markets/{ticker}` result cache. No credential, no order.
Unit of analysis is the **round** (one 15-minute expiry, all series), because co-expiring markets
share one price path.

## The seat's own distribution, pooled over everything it has ever traded

**493 rounds / 2,249 markets / 14,929 contracts / +$15.97 total.**

| | |
| --- | --- |
| per round | mean **+3.24 c**, sd **82.57 c**, se 3.72 c, **t = +0.87** |
| quantiles | p5 −120.8 · p25 −44.2 · p50 +2.0 · p75 +50.0 · p95 +134.9 c |
| extremes | worst round **−353.9 c**, best +384.0 c |
| markets/round | 4.6 |
| realised duty | **40.4%** (493 rounds quoted of 1,221 possible over a 305 h span) |

**The all-time seat is t = +0.87. It is positive and it is not established.** That is the honest
headline, and it supersedes the per-configuration numbers this corpus has been quoting.

## Three corrections to numbers that were load-bearing

**1. The income figure was 12× too high.** `kalshi-15m-maker-losses-were-caps-and-collateral`
reports **+$0.66/h = +$15.83/day at 100% duty**, computed on the runs that *completed their
window* — a subset selected on not having been stopped. Pooled over all 493 rounds:

> **+$0.13/h = +$3.11/day at 100% duty, and +$1.26/day at the realised 40% duty.**

**2. The drawdown inputs the supervisor sizes its cap off came from a 123-round subset and were
optimistic on both tails.** At 4× the sample:

| | old basis (123 rounds) | measured (493 rounds) |
| --- | --- | --- |
| mean/round | +16.0 c | **+3.24 c** |
| sd/round | 75.4 c | **82.57 c** |
| worst round | −175 c | **−353.9 c** |

**3. Today's loss was not the leftover leg, and the leftover leg is not the lever.** The 10-07
post-mortem splits −$5.76 into paired **+$0.42 (+0.354 c/ct)** and leftover **−$6.18 (−1.511
c/ct)**, which invites cutting the leftovers. It must not be: `exit_leftovers_paired.py` already
scored that over 1,184 settled markets and the cut turns **+$4.15 into −$7.25** while deepening max
drawdown 68%, because the leftovers carry most of the profit. Today is a one-day draw on the leg
that earns. `--max-round-net 2` was already on.

## The actual binding constraint: the cap, not the market and not the bankroll

First-passage simulation against the engine's own stopping rule (drawdown from the session
baseline, plus the collateral floor as a second absorbing barrier), 10,000 sims on the empirical
round distribution:

| bankroll | cap | P(1 day) | P(1 week) | E[markets accumulated] |
| --- | --- | --- | --- | --- |
| $16.28 | $4.00 | 55.4% | **38.1%** | 1,425 |
| $25.00 | $4.00 | 54.5% | 38.0% | 1,424 |
| $25.00 | $8.00 | 81.4% | 59.7% | 2,132 |
| $25.00 | $16.00 | 98.2% | **84.6%** | 2,782 |
| $50.00 | $16.00 | 98.1% | 83.8% | 2,769 |
| $50.00 | $32.00 | 100.0% | **98.0%** | 3,044 |
| $100.00 | $32.00 | 100.0% | 97.8% | 3,043 |

Two things read straight off this:

**The cap has been destroying the sample.** $4.00 is **4.84 round-sd**. A run at that cap reaches
the end of a single day 55% of the time, and a week 38%. That is the mechanical explanation for
"no run in 25 ever completed a week, 7 died on loss caps" — it is not bad luck, it is the designed
behaviour of a 4.8-sigma barrier on a 93-round-a-day process. The 10-07 run tripped it exactly as
predicted: equity $16.28 → $10.52, run 4 refused at zero seconds.

**Bankroll above `cap + floor` does nothing.** $25, $50 and $100 give identical survival at the
same cap. Funding only matters because it *permits* a bigger cap; the cap is the barrier.

## Price the prize — and this is where it fails

At the measured mean and sd, **t = 2 needs 2,600 rounds = 11,862 markets = 650 h** of quoting.
At the realised 40% duty that is **67 calendar days of continuous operation**, and the risk budget
required to survive it is a **$32 cap** (so ≥ $41 funded).

> So the proposition is: risk $32 and two months of uptime to find out whether an edge worth
> **$1.26/day** is real.

That fails this corpus's own magnitude rule — "a statistically perfect $2/day is not worth an
execution stack" — before any significance question is asked. **The seat is not broken; it is too
small to be worth financing at this clip.** The honest options are (a) stop, (b) fund it to $41 and
run a frozen 67-day measurement knowing the expected payoff, or (c) change the clip, which changes
the collateral and the whole table and needs `PREREG_clip_ladder.md` run first.

## What changed in the code

`runners/penny_supervisor.sh`:

* The drawdown comment block now cites the 493-round measurement and the survival table, instead of
  the 123-round subset it had.
* **`MIN_HEAD_C` 80 c → 250 c (3 round-sd).** A fifth of the drawdown budget is **0.97 round-sd**: a
  run launched with that much rope stops on its first ordinary round, locks the loss and strands
  the leftover without its exit leg, which is strictly worse than never starting. At the current
  $10.52 balance the supervisor now **refuses to start** (headroom 152 c = 1.8 round-sd) rather than
  launching a coin flip, which is what it would have done.
* `MAX_DD_C` default **left at 400 c on purpose.** The survival table says 1,600–3,200 c is what a
  week needs, but raising a real-money risk budget against a t = +0.87 edge is an operator
  decision, not a script's.

## Operational state as of now

* **Flat and safe.** The orphan watchdog's authenticated `cancel-all` at 16:32:46Z returned
  "verified: no resting orders"; nothing is resting and nothing is running.
* **Credentials work.** The 10-06 `401 NOT_FOUND` outage is over — that watchdog call authenticated.
  `~/.config/kalshi/` on az2 holds a live `env` plus `dead-20261006/` and `dead-20261007/`.
* **az1 (`i-0a6fef62e75fbe959`) is `TargetNotConnected`** — the box that held the 6.6-day orphaned
  `--exec` loop is unreachable, so that loop's state is unverified. Check it before it comes back up.
* **I did not restart the trader.** At $10.52 against a $9 floor the cap would be $1.52 = 1.8
  round-sd, which bleeds. The supervisor change now enforces that refusal.
