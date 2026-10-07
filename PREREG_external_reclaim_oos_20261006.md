# PREREG — external BTC reclaim strategy OOS replication

Frozen Tuesday, October 6, 2026 at 08:09:08 PDT, before scoring the local
post-publication data.

Source: public repository `J0shusmc/Kalshi-BTC`, commit
`593029336b9639ce6489592a12345fa40ebe4a69`, published September 17, 2026.
That commit froze three BTC 15-minute reclaim lanes on June 15–August 20 data.
No source thresholds will be changed after seeing this test.

## Holdout

Use local `KXBTC15M` and Coinbase BTC/USD one-minute candles from September 18
through October 5, 2026. This begins after the public commit. Drop post-close
Kalshi candles and incomplete markets.

## Frozen lanes

All lanes make a decision after five completed minutes and take the first
qualifying minute from 6 through 10:

1. `RECLAIM_70`: YES early ask low `(15c,30c]`, signal ask close `[35c,55c)`,
   first-five BTC move `[-$20,+$20]`, and BTC at minute five between $50 and
   $150 below its completed 15-minute EMA21. Target 70c.
2. `BTC_FADE_90`: YES early ask low `(25c,30c]`, signal ask close `[45c,65c)`,
   and first-five BTC move `[-$75,-$25]`. Target 90c.
3. `NO_RECLAIM_80`: NO early ask low `(35c,40c]`, signal ask close `[50c,70c)`,
   and signed first-five move against NO `[-$75,-$25]` (equivalently raw BTC
   move `[+$25,+$75]`). Target 80c.

Each signal must also close above its minute-five ask close and have a positive
one-minute ask body. Because the local Kalshi file omits candle opens, use the
previous minute's ask close as the frozen open proxy.

## Execution rows

Report both:

- `signal_close`: buy at the signal candle ask close; and
- governing `next_minute_1c`: delay until the next candle, buy at its ask close
  plus 1c, and require it still lies inside the lane's entry band.

For either row, a target exit is credited only if a later same-side bid high
touches the target. Charge the Kalshi taker fee on entry and target exit. If no
target is touched, hold to settlement and charge only the entry fee. Permit at
most one trade per market, taking the earliest signal and then source lane
order.

## Reporting and gate

Report trades, targets, settlement wins/losses, mean cents per contract,
UTC-day-clustered 95% lower bound, chronological halves, best-10%-day deletion,
lane breakdown and an extra 1c stress.

Promotion requires on the governing row:

- at least 100 trades;
- positive fee-adjusted mean and extra-1c-stressed mean;
- positive day-clustered lower bound;
- both chronological halves positive;
- positive best-10%-day-deleted mean; and
- no UTC day above 20% of gross positive P&L.

A pass would authorize only a prospective direct-book journal, never live
orders.
