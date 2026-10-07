# 0.9-cent sweep-backed maker OOS — positive point estimate, gate failed

The 21-day interval, 0.9-cent level, bars 12–14, 100-contract size and
sweep-backed fill floor were frozen in
`PREREG_subcent_sweep_oos_20261006.md`.

The first high-concurrency fetch was discarded because 77 trade histories
were capped by public-endpoint errors. The governing rerun used two workers
and completed all 692 eligible joins with **zero capped histories**.

## Governing base result

- 692 joined markets, fewer than the required 1,000;
- 66,906.6 credited contracts, 96.69 per join;
- zero-stress point P&L **+$4.86/day**;
- after the frozen 0.1c/contract stress: **+$1.68/day**;
- day-clustered stressed interval **[-$16.86, +$18.04]/day**;
- chronological halves **+$7.24 / -$3.38 per day**;
- only 5 adverse windows on 5 UTC days, below the required 10 days;
- stressed positive assets: BNB, DOGE, HYPE, NEAR and SOL (5/8, not 6/8);
- ZEC alone lost **-$230.13** after stress.

The preregistered OOS gate **FAILS** on sample size, adverse-event count,
second-half sign, clustered uncertainty and breadth.

## Causal no-rise sibling

Requiring the 0.9-cent cheap ask to be non-increasing for two prior bars kept
69.2% of raw joins. It did not solve the tail:

- 479 joins, zero capped histories;
- stressed point P&L **-$1.09/day**;
- both halves negative;
- clustered interval **[-$18.81, +$13.12]/day**.

This sibling is closed.

## BTC tail hedge sibling

The frozen BTC overlay entered 9 cheap hedges. All 9 expired worthless. It
reduced total stressed P&L from $35.25 to $32.25, slightly increased maximum
drawdown and left the worst day unchanged. It is closed.

## Verdict

**NOT PROMOTED.** The original sub-cent mechanism still has a positive
21-day point estimate, but this fresh OOS interval did not reproduce a stable
or broad profit. The rare loss process remains too under-sampled and
concentrated to call profitable.

Canonical reports:

- `idea_lab/oos_subcent_sweep_report.json`
- `idea_lab/oos_subcent_no_rise_report.json`
- `idea_lab/oos_subcent_btc_tail_hedge_report.json`
