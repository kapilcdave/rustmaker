# Preregistration — causal settlement-basis probit transfer

Frozen: 2026-10-06 before fitting or scoring.

## Hypothesis

The closing CF Benchmarks index can differ from a causal, one-minute-old
Coinbase proxy. Fit that combined remaining-move/index-basis distribution from
an earlier BTC period, then test whether it prices the final minute better than
the later Kalshi ask after fees.

## Train

Use only the earlier local BTC panel (`data/kxbtc15m_candles.json`) and matching
Coinbase one-minute candles.

For every settled market:

1. define the decision quote timestamp as exactly 60 seconds before close;
2. use Coinbase's candle close timestamped one additional minute earlier, so
   the spot value is complete and observable at the quote timestamp;
3. set `distance = causal_spot - strike`;
4. fit one probit
   `P(YES) = Phi((distance + offset) / scale)` by maximum likelihood;
5. bound offset to -$500…+$500 and scale to $1…$2,000;
6. do not condition on hour, direction, volatility, market price or outcome
   subsets.

## Test

Apply the frozen offset and scale to the later September 14–October 6 BTC panel.
At exactly 60 seconds before close:

1. use the actual YES ask and complementary NO ask from that Kalshi candle;
2. use only the completed Coinbase candle from one minute earlier;
3. calculate modeled YES and NO probability;
4. buy the better side only when probability minus actual ask minus the local
   conservative one-contract taker fee is at least 2c;
5. hold to settlement and trade at most once per market.

## Gate

Require all of:

- at least 100 trades;
- positive mean after an extra 1c stress;
- positive day-clustered 95% lower bound;
- both chronological halves positive;
- positive mean after deleting the best 10% of days;
- no day above 20% of gross positive P&L.

Failure closes this basis-only transfer. No threshold, horizon, side or period
may be retuned from the test result.
