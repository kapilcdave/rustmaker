# PREREG — prospective direct-book Coin Race rank calibration

Frozen Tuesday, October 6, 2026 at 07:52:55 PDT. Only journal snapshots with
`wall_ns >= 1791298375016036000` are eligible.

This is a read-only paper test. It places no order and claims no fill.

The model is copied without modification from the development-only fit in
`PREREG_crypto_leader_rankcal_20261006.md`. For decision minutes 5, 8 and 12,
the fixed `[alpha_BTC, alpha_ETH, alpha_SOL, alpha_XRP, alpha_HYPE, beta]`
vectors are:

- minute 5: `[-0.3391638451, -0.2813714652, -0.0622432344, 0.2247149700,
  0.4580635747, 1.4958202938]`;
- minute 8: `[-0.3702546322, -0.4275506268, 0.0352786079, 0.3101045031,
  0.4524221481, 1.7622721386]`;
- minute 12: `[-0.2362998533, -0.3773055647, -0.0479355808, 0.3354599039,
  0.3260810949, 2.3957180714]`.

At each minute, calculate the same clipped, trailing-volatility-standardized
Coinbase return. Use the earliest complete five-leg direct-orderbook snapshot
whose wall time is 2–15 seconds after the minute boundary. Convert each NO bid
to the executable YES ask `1-NO_bid`. If the largest model probability minus
YES ask minus standard taker fee is strictly above 10 cents, paper-buy that
YES contract and ignore all later decisions in the event.

Settle at the published fractional settlement value. Record every eligible
decision, displayed size, request span and signal. The short prospective gate
requires at least 10 settled signals, positive mean after fees and after an
extra one-cent stress, positive chronological halves, and no asset above 60%
of gross positive P&L. Passing would authorize only a longer direct capture,
not trading.
