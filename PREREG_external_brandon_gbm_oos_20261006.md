# Preregistration — BrandonOnChain GBM directional bot replication

Frozen before scoring on 2026-10-06.

## Source

- Public repository: `brandononchain/kalshibot`
- Source commit audited: `a01af900f96a6038db081de18ebbb1e98cb3cbb2`
- Commit timestamp: 2026-09-24 23:26:51 -0500
- Strategy: BTC spot displacement from the contract-open price divided by
  trailing realized volatility and remaining-time square root, compared with
  the executable Kalshi ask.

The source's real-capture simulator freezes:

- first four minutes;
- contract ask in 35–65c;
- model edge greater than 15c after fee;
- one cent adverse entry slippage;
- one trade per market, held to settlement.

The public runtime `.env.example` instead uses an 8c minimum divergence and a
ten-minute window. Both named source configurations are reported, but the
real-capture simulator configuration is governing because it is the source's
more conservative research specification.

## Frozen local replication

- Population: BTC 15-minute markets opening strictly after 2026-09-25 00:00
  UTC from the already downloaded 2026-10-06 OOS panel.
- Price reference: Coinbase one-minute bar open at the market open.
- Decision spot: last completed Coinbase one-minute close.
- Volatility: standard deviation of up to 15 completed one-minute log returns,
  scaled to a 15-minute horizon. At least 10 closes are required.
- Kalshi entry: candle-close YES ask or implied NO ask at the matching decision
  minute, plus the source simulator's one-cent slippage and the venue taker
  fee.
- Select the highest-edge side and take only the first qualifying minute.
- No parameter fitting on this post-publication period.

## Interpretation limits

This is a causal minute-resolution replication, not an exact replay of the
source's Binance tick feed or an actual-fill claim. A positive result could
only justify a direct-book prospective journal. A negative result closes the
public claim at the slower, more conservative execution resolution because
displayed tick-level execution cannot repair a large negative settlement
expectation without independent evidence.

## Gates

Promotion requires all of:

1. at least 100 completed markets;
2. positive fee-and-slippage-adjusted mean;
3. positive chronological halves;
4. positive day-clustered 95% lower bound;
5. positive one-additional-cent stress;
6. no single day contributes more than 60% of gross positive P&L.

