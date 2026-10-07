# PREREG — exact-contract cross-venue hedged maker

Frozen Tuesday, October 6, 2026 at 08:58:09 PDT. Only paired-book rows with
`ts >= 1791302289.195273` are prospective.

This tests a structurally different use of the exact Polymarket US/Kalshi BTC
15-minute identity. Instead of trying to cross both venues as a taker, rest one
maker order and, only if it fills, immediately buy the complementary contract
on the other venue. Matching settlement then removes direction risk:

- make PM US YES, hedge Kalshi NO;
- make PM US NO, hedge Kalshi YES;
- make Kalshi YES, hedge PM US NO;
- make Kalshi NO, hedge PM US YES.

At each fresh paired book (`abs(pm_age) <= 10ms`), price the maker one tick
inside the spread when room exists, otherwise join the best bid. The order must
remain strictly below its same-venue ask. Use zero maker fee, charge the other
venue's exact one-contract taker fee, require at least one displayed hedge
contract and at least **2c** locked margin before maker-fill uncertainty.

Call a quote opportunity only when the same direction clears 2c on two
consecutive samples no more than 1.5 seconds apart. Take the first per window.
Report next-sample survival and displayed hedge size.

This is a quote-opportunity screen, not a fill backtest. A passing short screen
requires five independent windows, positive halves, at least 80% next-sample
survival and at least one displayed hedge contract. It would authorize a
read-only trade-tape/queue study only. It does not authorize orders.
