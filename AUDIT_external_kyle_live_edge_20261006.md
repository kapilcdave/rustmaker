# Audit — external preregistered live high-edge rule

Pinned public source: `kyleleedixon/kalshi` commit
`4bcd7b81c08a4560d9bd01dfbdaf896a2870253e`, generated 2026-10-06
16:30 UTC.

This is unusually useful negative evidence because the repository publishes a
precommitted kill rule, strategy tags, live contract counts, settlements and
P&L rather than only a backtest. The published verdict table does not break the
clean sample down by instrument type, so treat it as mixed crypto counterevidence
that includes the engine's 15-minute universe, not as a pure KXBTC15M estimate.

## Rule and verdict

The live rule required:

- post-fee model edge at least 7c;
- spread at most 3c;
- a book re-fetched within two seconds;
- the same directional taker rule under both pooled tags.

Its backtest expected **+6.5c/contract**. The preregistered live kill rule
required at least +2c/contract after 500 contracts.

Published live result:

| evidence | contracts | settled markets | P&L | EV/contract |
|---|---:|---:|---:|---:|
| initial tag | 109 | 36 | +$2.05 | +1.88c |
| later tag | 467 | 215 | -$28.65 | -6.13c |
| **pooled at kill** | **576** | **249** | **-$26.60** | **-4.62c** |

The source marked the hypothesis **REFUTED** and halted. Its current state
remains halted with zero active slice gates.

The separate maker-first mechanism also failed its preregistered fill-rate
guard: **0 of 24 requested maker contracts filled**, versus a reported 64%
taker fill rate. It was disabled before the P&L threshold.

## Decision

**Closed as external mixed-crypto real-live counterevidence.** This is stronger
than another local candle backtest: a model-value rule that looked monotone,
survived jackknife and chronological checks, and started positive still
reversed by roughly 11c/contract when exposed to live selection and execution.
It cannot by itself estimate a 15-minute-only effect because that composition
is not published.

Reusable lesson: even a large post-fee model edge and fresh-book gate cannot be
treated as a fill-conditioned edge until a preregistered live sample survives.

Reproduction:

```bash
python3 idea_lab/audit_external_kyle_live_edge.py
```
