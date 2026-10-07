# PREREG — Coin Race 15-minute complete-set constructions

Frozen 2026-10-06 at 07:05:48 PDT, before historical candle sums or prospective
direct orderbooks for `KXCRYPTOLEAD15M` were collected or analyzed.

Each event has five mutually exclusive contracts: BTC, ETH, SOL, XRP and HYPE
has the highest 15-minute CF Benchmarks return. The rules split a tie among tied
YES contracts and round each fractional payout down to the nearest cent.

## Frozen constructions

At every complete same-timestamp observation:

1. **All YES:** buy one YES in all five markets. Worst contractual payout is
   $0.99 because a three-way tie can pay $0.33 each.
2. **All NO:** buy one NO in all five markets. Worst contractual payout is
   $4.00.
3. **Four NO:** for each omitted market, buy one NO in the other four. Worst
   contractual payout is $3.00.

Charge the standard taker fee separately on every leg. A signal requires total
ask plus fees to be strictly below the construction's worst payout. Report raw
and additional one-cent-per-leg stress margins.

Historical development uses a seeded day-stratified sample of eight complete
events per UTC day from 2026-09-14 through 2026-10-05. Minute candles sharing an
end timestamp are non-atomic and cannot establish execution.

Prospective evidence starts with the first event opening after Unix second
`1791295548`. Fetch every leg's public direct orderbook sequentially, record
request timestamps and displayed size, and exclude incomplete or rule-invalid
events. A prospective observation is still non-atomic REST paper.

The direct-book construction screen passes only if at least one signal has a
positive margin after one-cent-per-leg stress and all five legs display at
least one contract at the required asks. A pass cannot authorize orders:
multi-leg execution must still be atomic or safely legged, neither of which is
measured here.
