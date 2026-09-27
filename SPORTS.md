# Sports experiment

`sports-scan` discovers the current Kalshi Sports catalog using public GETs.
`sports-shadow` runs rustmaker's queue/latency simulator on selected sports series.
Neither command submits orders. No AWS resources are needed.

```sh
cargo run --release -- sports-scan --out data/sports
# Select exact tickers from that catalog:
cargo run --release -- sports-shadow --series KXATPTOTALSETS \
  --min-spread-c 20 --minutes 60 --out data/sports \
  --env-file /path/to/private.env
```

The shadow authenticates only for market data using the existing Kalshi credential
loader. Series membership is checked against the current Sports catalog before
connecting. Discovery follows market pagination and saves raw market records,
including event IDs. At most 20 series and 256 distinct markets are admitted per
run; hitting the market cap is reported explicitly. Selection follows API order,
so a capped run is not representative of the whole venue.

The initial hypothesis is **joining a wide sports book can earn enough spread to
cover fees and adverse selection**. This is an experiment, not a proven edge.
The default threshold is 20 cents in absolute price units, including near the
tails. It is a research parameter, not an optimum. `sports_join` joins both
touches, queues behind displayed size, uses one-contract clips and a one-contract
absolute position cap per market. It disables crypto momentum, imbalance, spot,
and penny improvement rules. Cancellation credit uses the existing trade/level
clamp model, without pro rata cancellation credit.

Outputs under `--out`:

- `sports_catalog_*.json`: full series catalog, fees and lifetime volume.
- `sports_selection_*.json`: selected series metadata.
- `sports_markets_*.jsonl`: market metadata and explicit event linkage.
- `sports_run_*.json`: simulation settings and limitations.
- `shadow_*.csv.gz`: book/trade observations, simulated fills and ending inventory.

Important measurement limits:

- Fees are not deducted; simulator cash is not profit. Series fees can have event
  overrides. Do not use `shadow_pnl.py`'s gross, market-clustered report as a sports
  net-profit claim. Sports analysis must group correlated markets by event.
- The simulator inherits 5.44 ms create / 4.43 ms cancel assumptions from the
  earlier crypto measurement. They are not measurements from this machine/run.
- Matching prints and cancellations is a model; actual queue position is unknown.
  Reconnects clear simulated orders, whereas real orders can remain resting.
  Inspect sequence gaps and dropped tape rows before using a run.
- Close time is not game start. The experiment includes pregame and in-play books
  without a score/injury feed. There is no event-level portfolio limit or live
  sports execution mode. Unknown close times are excluded.
- The older `../kalshi-sport` candle research showed promising cells, but its live
  tick validation was inconclusive. This touch-joining, position-capped experiment
  differs from its inside-spread, hold-to-settlement strategy.

Evaluate fill rate, spreads at simulated fills, adverse markouts, actual fee
overrides, and event-clustered net settlement returns over multiple independent
games before drawing an income conclusion. Catalog volume alone cannot establish
an edge or the absence of competing bots.

API references: [series catalog](https://docs.kalshi.com/api-reference/market/get-series-list),
[paginated markets](https://docs.kalshi.com/api-reference/market/get-markets).

## First smoke test — 2026-09-26

`cargo test --offline`: 15 passed. The public catalog returned 3,920 sports
series. A one-minute authenticated shadow run selected `KXATPTOTALSETS,KXMLBRFI`
at a 20c threshold and discovered 17 markets across 17 events (all admitted
markets were KXMLBRFI). It processed 1,483 timed events, recorded 53 touch changes
and 30 prints, posted 8 simulated quotes and cancelled 8, with **zero fills** and
zero dropped tape rows. Five touch changes met the width threshold, all in
`KXMLBRFI-26SEP262140LAASEA`. Median local handler time was 23 µs; p99 was 141 µs.
These are processing times, not network or exchange execution latency.

Artifacts are local and ignored under `data/sports-smoke/`, including
`shadow_1790473491943.csv.gz`. This verifies plumbing only; no profitability
estimate or live order resulted.

## sports-live — ARMED sports maker (2026-09-27)

`sports-live` places REAL orders (`--armed`) or sends nothing (`--dry-run`). It reuses the
15M live engine (order group, caps on the engine's own fill ledger, verified cancel-all) with
a sports mode:

- Quotes fee-free series only (refuses `quadratic_with_maker_fees` at startup), tickers
  matching `--tag` (game dates, e.g. `26SEP27`), on ONE shard (`--exchange-index`; football,
  soccer, esports are shard 0; MLB/WNBA/tennis are shard 3). Up to `--max-markets` quoted books.
- One tick inside the OTHERS' touch when their spread is >= `--min-spread-c` (10) and both
  their mid and OUR price are inside 15-85c. Clip 1, |pos| <= 1 per market, post-only.
- `--parents` are the same games' full-game books, watched and never quoted. A game is not
  quotable until one of its parent books has been seen (fail closed). A parent mid move of
  >= `--parent-move-c` (3c) within `--parent-window-s` (10 s) pauses the whole game for
  `--pause-s` (30 s) and cancels every order in it; so does a >= `--jump-c` (5c) jump within
  2 s in any of its quoted books. Basis: `the-full-game-book-is-the-segment-books-spot-feed`
  (WNBA, in-sample, exploratory).
- Worst-case caps assume a score sweeps a whole ladder one way: `--max-game-ct` (3),
  `--max-total-ct` (8). Loss caps `--max-loss-c` 300 (session), `--cum-max-loss-c` 500.
- Markets that leave `status=open` are dropped on the `--refresh-s` (60 s) rediscovery.

Known gaps: no period clock, so a period that has ended but whose markets are still listed
is protected only by the band, the width gate and the jump pause; quoting is two-sided (the
basketball ledger says sell-the-over only on TOTALs); `score_sports.py` scores shadow tapes,
not live journals.

## Two-sided round trips and continuous auto mode (2026-09-27, supersedes the defaults above)

The first armed run (10c gate, hold to settlement) took 5 fills and 0 round trips: every
position was a naked directional bet, and the three San Jose–Portland 2H-total fills were all
bids hit because the over decays with the clock. So `sports-live` is now a spread collector:

- Opens only on books `--min-spread-c` (1) to `--max-open-spread-c` (10; the service uses 3)
  wide; joins the touch below `--penny-min-c` (3c), one tick inside at or above it.
- After any fill only the flattening side quotes: entry +/- `--exit-edge-c` (1c) until
  `--scratch-s` (60 s), then at the entry price until `--bail-s` (180 s), then at the
  competitive price. Always post-only; never crosses. `round_trip` journal rows carry each
  round trip's P&L; the status line prints `round_trips=` and `rt_pnl=`.
- The loss caps mark open inventory at the price it could be sold at (bid for longs, ask for
  shorts; a YES+NO pair is exactly 100), not the mid of a wide book.
- `--series auto`: a background sweep of every open sports event (every `--refresh-s`, 180 s)
  picks fee-free markets on `--exchange-index`, dated yesterday..tomorrow UTC in the ticker,
  inside the open width and the 5-95c band, with 24h volume >= `--min-v24` (500), ranked by
  volume, top `--max-markets`. Markets that fall out of the list are dropped once flat. With
  no `--parents`, the parent-book gate is off and the 5c/2 s jump pause is the event guard.
- SIGTERM stops cleanly (cancel all on this shard, verify). A loss-cap stop exits 3.
- Shutdown cancels only this shard's orders, so one engine per shard can run side by side.
- `balance` (read-only) and `shard-transfer --from A --to B --dollars D [--go]` subcommands.

### Running it on the VM

The VM (t3.micro, 945 MB) has no Rust toolchain and is too small to build this. Build on a
laptop and copy the binary, or download the release asset:

```sh
# laptop (Homebrew rustc shadows rustup and has no musl std, so use rustup's):
PATH=$(dirname $(rustup which rustc)):$PATH cargo zigbuild --release --target x86_64-unknown-linux-musl
# or on the VM:
gh release download sports-mm-v1 -R kapilcdave/rustmaker -p kalshi-mm15-x86_64-linux-musl
```

Install as `~/trading/kalshi-mm15/kalshi-mm15-sports`, with the Kalshi key in
`~/trading/kalshi-mm15/.env` (`KALSHI_API_KEY`, `KALSHI_PRIVATE_KEY`), then:

```sh
./kalshi-mm15-sports sports-live --dry-run --series auto --exchange-index 0 \
    --min-spread-c 1 --max-open-spread-c 3 --minutes 3 --out data/sports-auto-dry --env-file .env
sudo cp deploy/sports-mm.service /etc/systemd/system/ && sudo systemctl daemon-reload
sudo systemctl enable --now sports-mm          # REAL orders, shard 0, 24/7
tail -3 data/sports-auto/run.log               # round_trips= / rt_pnl=
sudo systemctl stop sports-mm                  # cancels everything on shard 0
```

Shards: football, soccer, esports and cricket are shard 0; MLB, WNBA and most tennis are
shard 3. Each shard holds its own cash (`balance` shows the breakdown).

### What the first night established

- 2026-09-27 armed runs, all shard 0: about -$1.00 overall, almost all of it wide-book
  positions held to settlement (the design this section replaces). Real round trips: +2c
  (Liga MX BTTS 21->23), +18c, 0c scratch, -10c (bail after 180 s on a moving book).
- A taker hitting stale segment quotes after a full-game move loses -10.5c/ct (2% win,
  53 moves, 4 games): the segment book is ~29c wide and its mid does not follow within 60 s.
- Five overnight shadow runs of the old wide-join design: nothing distinguishable from zero;
  fills in the last 15 minutes before close lose everywhere (-16.2c worst).
- Known gap: complementary markets of one game (e.g. `2H-MINN` long and `2H-WASH` short) are
  the same bet, and the per-game cap counts them as two. A net-direction cap is next.
