# PREREG — BTC tail hedge for the 0.9-cent altcoin maker basket

Frozen 2026-10-06 at 05:07 PDT before reading post-2026-09-14 trade-tape
results or BTC candle outcomes for this arm.

## Hypothesis

The 0.9-cent altcoin maker loses on rare, same-slot factor jumps. A cheap BTC
contract in the same loss direction may insure several correlated altcoin
legs more cheaply than hedging every leg separately.

Use the exact joins and sweep-backed credited fills from
`PREREG_subcent_sweep_oos_20261006.md`. At the close of BTC candle bar 14 in
each 15-minute slot:

1. retain only credited altcoin fills already established by the frozen
   sweep rule;
2. require at least three distinct filled assets;
3. sum filled contracts by their loss side (`yes` or `no`);
4. require the dominant side to hold at least 75% of filled contracts;
5. inspect the matching `KXBTC15M` contract and buy the dominant outcome only
   when its executable ask is at most 2.0 cents;
6. buy `floor(dominant_alt_fills / 3)` BTC contracts, capped at 200.

The hedge decision uses only known fills and the bar-14 BTC book. It does not
use settlement, bar 15 or a later price.

## Costs and scoring

- Charge the published standard taker fee to the BTC batch:
  `ceil(0.07 * contracts * price * (1-price) * 100) / 100`.
- Stress the altcoin fills by 0.1 cent per contract, as in the base OOS arm.
- Stress the BTC hedge by an additional 0.2 cent per contract.
- Score all 21 complete UTC days, including days with no hedge.

This is a risk overlay. Promotion requires:

- complete eight-altcoin and BTC coverage, and no capped base histories;
- at least ten hedge entries and at least three winning hedge entries;
- combined stressed P&L positive in both chronological halves;
- positive day-clustered 95% lower bound;
- largest losing day at least 20% less severe than the stressed unhedged
  sibling;
- maximum drawdown lower than the stressed unhedged sibling;
- total stressed P&L remains at least 50% of the unhedged total.

The arm shares its OOS interval with the base strategy and is not an
independent replication. Historical candle closes are non-atomic executable
price proxies. A pass does not authorize an order.
