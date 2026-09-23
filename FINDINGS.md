# Findings — Kalshi 15M crypto market-making

## Probe setup

`kalshi-mm15` reads the Kalshi 15M crypto books, records the full tape
(books + prints + round-trip latencies), then scores every print as a maker
fill. Features are read at `t - LAG` where `LAG = feed_one_way (6.2 ms median)
+ order into book (4.4-5.4 ms median)` — a feature we could not act on in time
is worthless to a quoter. All markouts are contract-weighted; SEs are clustered
by market (all fills in a market share one settlement).

- Render the toxic-flow table: `python3 tox.py <tape>.csv.gz`
- Go/no-go on speed: `python3 verdict.py <stats>.jsonl [<rtt>.json]`
- Pull-the-quote rules on the mid-band ledger: `python3 gate_eval.py <prints>.parquet`
- Shadow-strategy P&L: `python3 shadow_pnl.py <shadow>.csv.gz`

## Getting hit is roughly breakeven, and the hurt is concentrated

On the 3 h box tape (`tape_1790136632445`, ~330k prints, ~63 markets, ~two
halves H1/H2):

- **All fills:** holding to settlement is near breakeven on the first half and
  positive on the second (settle ~ +0.2 c/ct on the tight-spread majority).
- **Momentum is the tell.** Where the mid was already moving in the taker's
  direction in the prior 1 s, the maker's fill loses at both 5 s and settlement:

  | 1s momentum (seen at t-LAG) | mk5s (c/ct) | settle (c/ct) |
  |---|---|---|
  | against | +0.5 / +0.4 | +2.9 / +3.8 |
  | flat | +0.1 / +0.2 | -0.0 / +3.7 |
  | with | -1.1 / -2.9 | -4.1 / -3.6 |

  So the edge is on the *fade* side: fills taken while the mid is moving the
  other way mark out well; fills taken into an in-flight move are the toxic
  flow. A quoter who does nothing looks like the "flat" row.
- **Hit-side thinness.** Making on the side that is thin (top-quintile size
  imbalance against you, `imbq = q5`) loses at 5 s on both halves and at
  settlement on H1 (-1.9 c/ct). A thick hit side is fine (+0.2 / +3.2).
- **Spread.** Fills on 1-2 c spreads mark out fine; the small slice taken on
  2-3 c spreads loses at every horizon on H1.
- **Late closure.** Fills inside the last minute mark out badly on H2 at
  settlement (-3.5), though evidence is thin (tens of markets).

## Speed verdict

`verdict.py` says reaction lands at the venue at `t_print + feed one-way +
order one-way` (feed ~6 ms p50, order ~4.4-5.4 ms p50) — one-sided — so an
"avoid the toxic print" strategy builds in ~11 ms of unavoidable lag. Pulling a
quote flagged by momentum or hit-side-thinness at that lag is evaluated by
`gate_eval.py`: it simulates removing the flagged fills and measures the
markout of the fills that *remain*, so an honestly-stated bar for "the gate
works" is positive remaining-markout.

## Shadow-strategy P&L

`shadow_pnl.py` scores a shadow tape by pairing fills FIFO bid-vs-ask
(locked-in spread livery) and riding the residual to settlement, per strategy
and series, with clustered SE. Run it on each shadow tape under `data/shadow*`.