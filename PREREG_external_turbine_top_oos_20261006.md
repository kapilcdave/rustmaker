# Preregistration: top public Turbine KXBTC15M strategy

Frozen before scoring the local post-publication outcomes.

## Source and selection warning

- Repository: `ojo-network/kalshi-bots-collection`
- Frozen commit: `a6b31da83462698fc3a6a6ccbe253de6dad1f5b7`
- Strategy: `btc-martingale-40-80-dc2b2e655aac`
- Public generation time: 2026-05-21

This is the highest reported PnL among 1,276 KXBTC15M backtests in the dump.
All 1,276 are labelled `single_window_in_sample`; selecting the winner creates
severe multiple-testing bias. A post-publication pass can make it a paper
candidate, never REAL-LIVE evidence.

## Frozen one-minute replication

Use the September 14–October 6 local BTC market and Coinbase archives.

At each Kalshi one-minute close with more than three minutes remaining and no
position:

1. Compute causal Coinbase `velocity_1m` as the percent return of the latest
   fully completed one-minute candle.
2. Signal only if velocity is greater than `0.167`, YES midpoint is between
   0.05 and 0.90, and YES spread is below 0.03.
3. Buy 40 YES contracts at the next Kalshi minute's YES ask.

While holding, signal an exit when any source condition is met:

- fewer than three minutes remain;
- YES midpoint is above 0.88 while more than three minutes remain;
- mark-to-bid unrealized PnL exceeds $5 or is below -$3;
- Coinbase velocity falls below -0.05;
- fewer than two minutes remain.

Sell all at the next minute's YES bid. If no sell executes, settle the position
against the venue result. Re-entry after an exit is allowed, matching the DSL's
`position_size == 0` rule.

Every buy and sell is charged the batch taker fee
`ceil(0.07 * contracts * p * (1-p) * 100) / 100`.

## Evidence gate

Report per-contract PnL with the standard clustered/halves/1c-stress battery.
The strategy requires at least 100 completed position cycles and the full
battery-wide promotion gate. It remains paper even if it passes.

