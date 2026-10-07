# Preregistration: exact-pair cross-venue lead/lag taker

Frozen at Unix time `1791302550.933293` (2026-10-06 09:02:30 PDT), before
examining any later rows.

## Hypothesis

Polymarket US and Kalshi list the same BTC 15-minute BRTI event. A fresh,
executable bid on one venue may occasionally move far enough beyond the other
venue's executable ask to identify a lagging ask. Instead of taking both legs,
buy one contract on the lagging venue and hold it to the common settlement.

## Inputs and causality

- Source: the already-running read-only
  `../polymarket-us-mm/data/xvenue_btc15_20261006.jsonl`.
- Use rows with `ts >= 1791302550.933293`.
- Require the Polymarket US state to be open, no collector error, and
  `abs(pm_age) <= 10ms`.
- Require at least one displayed contract at every price used.
- A signal is actionable only after two consecutive qualifying observations
  no more than 1.5 seconds apart.
- At most one signal per 15-minute window.

## Frozen opportunities

Let executable side bids on venue A be conservative lower bounds for the
common outcome value on venue B:

1. buy Kalshi YES when PM-US YES bid minus Kalshi YES ask and exact
   one-contract Kalshi taker fee is at least 3c;
2. buy Kalshi NO when PM-US NO bid minus Kalshi NO ask and Kalshi fee is at
   least 3c;
3. buy PM-US YES when Kalshi YES bid minus PM-US YES ask and exact
   one-contract PM-US fee is at least 3c;
4. buy PM-US NO when Kalshi NO bid minus PM-US NO ask and PM-US fee is at
   least 3c.

Polymarket's one-contract fee uses coefficient 0.0695 and cent rounding to
nearest-even. Kalshi uses `ceil(0.07*p*(1-p)*100)/100`.

## Score and gate

P&L is common binary payout minus paid ask minus entry fee. Report raw and
extra-1c-stressed means, chronological halves, venue/side concentration,
displayed size, and next-sample edge survival.

This short prospective screen passes only with at least five settled windows,
positive raw and stressed means, both halves positive, at least 80% next-sample
survival, and no venue/side arm contributing more than 60% of gross positive
P&L. Passing is still paper evidence, not live-trading authorization.

