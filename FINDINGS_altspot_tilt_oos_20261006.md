# Thin-altcoin spot-gap OOS — preregistered pass, execution still unproved

Rule and population were frozen in `PREREG_altspot_tilt_20261006.md` before
the post-2026-09-14 data was fetched. The corrected scorer explicitly enforces
the preregistered exclusive end at 2026-10-06 00:00 UTC.

## Governing result

Primary pool, NEAR + ZEC + HYPE:

- **1,280** first-trigger trades;
- net **+2.345 cents/contract** after the standard taker fee;
- day-clustered lower 95% bound **+0.334 cents**;
- NEAR **+2.32c**, ZEC **+1.17c**, HYPE **+3.15c**;
- both chronological halves positive: **+1.52c / +3.17c**;
- deleting the best 10% of UTC days leaves **+2.01c**;
- largest day is 9.1% of gross positive P&L.

The frozen control pool, BTC + ETH + SOL + XRP, was **-0.678c**. It therefore
did not generically pass. The preregistered primary gate **PASSES**.

BNB was a pre-declared secondary with no decision authority. It read
**+6.757c**, lower clustered bound **+4.264c**, and has been moved only to a
fresh prospective direct-book replication. DOGE was +0.56c with a negative
lower bound.

## Robustness and the binding limitation

This is same-close candle evidence: the Coinbase minute close and Kalshi
candle-close quote are contemporaneous but not an atomic executable snapshot.
The result is therefore sensitive to execution:

- raw day-bootstrap interval: **[+0.32c, +4.38c]**;
- after an extra 1c/contract stress: mean +1.35c but interval
  **[-0.68c, +3.38c]**;
- after 2c stress: mean +0.35c and interval **[-1.68c, +2.38c]**.

The prospective direct-orderbook journal uses the same frozen formula and
2–15 second decision timing. Its final paper settlements are appended at the
10:00 PDT stop.

## Verdict

**PROMOTED TO DIRECT-BOOK SHADOW, NOT TO LIVE TRADING.** This is the strongest
new directional candidate in the overnight work: it cleared its true OOS gate
with the intended negative control. It is not yet a validated profitable bot
because one tick of slippage destroys the lower confidence bound and the
historical quotes are non-atomic.

Canonical reports:

- `oos_score.json`
- `idea_lab/altspot_oos_robustness_report.json`
- `idea_lab/altspot_tilt_live_report.json` (prospective, final at stop)
