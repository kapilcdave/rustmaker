# PREREG — prospective direct-book BTC resolution rider

Frozen Tuesday, October 6, 2026 at 07:58:30 PDT. Only decisions with
`wall_ns >= 1791298710663503000` are eligible.

This copies the fixed rule in
`PREREG_resolution_rider_replication_20261006.md` after its historical score:
117 earlier trades and 60 later trades, zero losses in both periods and
positive stressed economics, but the later period missed the preregistered
100-trade minimum. The prospective test is read-only and places no order.

For each new `KXBTC15M` market, evaluate only 180, 120 and 60 seconds before
close, accepting a decision 2–15 seconds after its minute boundary:

- use the just-completed Coinbase minute close and prior-minute return;
- require the 94–97c favorite direct-orderbook ask;
- require signed distance from strike of at least
  `0.10% * sqrt(seconds_left/60)`;
- reject YES if one-minute momentum is below `-0.04%`, or NO if it is above
  `+0.04%`;
- record the first qualifying signal per market, displayed size, request
  timestamps, fee and eventual settlement.

The Coinbase and Kalshi REST requests are sequential and non-atomic. The short
paper gate requires at least 10 settled signals, positive mean after fees and
one-cent stress, positive chronological halves, at least one losing signal or
100 total settled signals (to prevent a tiny zero-loss run from looking
conclusive), and 95% complete scheduled decisions. A pass authorizes only a
longer capture.
