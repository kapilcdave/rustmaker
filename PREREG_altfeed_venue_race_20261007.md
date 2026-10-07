# PREREG — which spot venue leads the altcoin 15M book, per asset (2026-10-07)

Frozen before the capture. The venue set the engine reads is currently a **guess**, and this
document exists so it stops being one. Nothing here authorises an order.

## Why this measurement and not another

`FINDINGS_signing_and_transport_20261006.md` closed the local-latency question: our decision path
is **45 µs**, order→book is **2.7–3.0 ms**, and the market-data feed is **5.7 ms** — 68% of an
8.5 ms reaction spent waiting for Kalshi to publish. FIX market data would shorten it and is
tier-blocked. So the only remaining way to see the underlying sooner is to stop reading Kalshi's
view of it and read the spot venues directly.

The engine has in fact read spot since September, but through **one socket**: Coinbase Exchange
`ticker`, which fires only on a Coinbase *match*. On BTC that is a dense clock. On NEAR, ZEC or
HYPE it is not: the channel can hold a stale print for minutes while the quote moves everywhere
else. `kalshi-15m-crypto-competitors-react-twice-as-fast-as-the-ohio-box` already reached this
conclusion from the other side — "the fast makers don't react to the Kalshi print, they react to
the same EXTERNAL spot move", with the explicit next step *"tape Coinbase spot on the box and
measure how far the 15M mid lags it"*. This is that measurement, widened to ten venues and all
nine assets, because the leader is unlikely to be the same venue for BTC and for ZEC.

## What settles the contract

Checked against live `rules_primary` on all nine series, 2026-10-07:

> If the simple average of the sixty seconds of CF Benchmarks' **ETHUSDRTI** before 3:45 AM EDT is
> at least the simple average of the sixty seconds of **ETHUSDRTI** before 3:30 AM EDT, then Yes.

BTC's index is `BRTI`; the other eight are `{ASSET}USDRTI`. So the thing to predict is a CF
Benchmarks real-time index, built from a subset of these same venues, and the contract is a
comparison of two 60-second averages of it. Kalshi republishes the index on its own WebSocket
(`cfbenchmarks_value`, `cfbenchmarks_value_5hz`) — that is the *reference*, not a fast feed: it is
1 Hz, 5 Hz for ETH/SOL/XRP/DOGE, and it arrives over the same 5.7 ms publisher as everything else.
CF's own API (`www.cfbenchmarks.com/api/v1`) returns 401 without a licence, so a direct index feed
is a commercial question like FIX, not an engineering one.

## Instruments built for this

| what | where |
| --- | --- |
| 10-venue, 9-asset top-of-book feed and capture | `src/fastspot.rs`, `kalshi-mm15 altfeed` |
| venue table (quote currency, stamped, listed assets) | `kalshi-mm15 altfeed-venues` |
| TCP handshake RTT per venue, from the box | `kalshi-mm15 altfeed-ping` |
| settlement index taped on the same receipt clock | `kalshi-mm15 probe --index` |
| the race scorer | `altfeed_score.py` |
| the taker replay, fee-aware | `altfeed_taker.py` |

Venues: Coinbase Exchange + Advanced Trade `ticker`, Kraken v2 `ticker/bbo`, Binance.US
`bookTicker`, OKX `bbo-tbt`, Gate `spot.book_ticker`, Bitstamp `order_book`, Crypto.com `book.10`,
Gemini `l2`, Hyperliquid `bbo`. Every frame shape is a verbatim capture pinned in a unit test, not
a shape copied out of a doc — the first build subscribed Kraken's REST `wsname` (`XBT/USD`,
`XDG/USD`), which v2 rejects, and captured **zero BTC and DOGE rows** while looking healthy. The
probe now fails if any (venue, asset) cell produces no quote.

## The run

All of it on the **az2 box** (`i-0f25ca08ce8d9d016`), chrony-synced, with `KALSHI_REST_IP` pinned
to an az2-group node. Numbers taken anywhere else measure that machine's network, not ours.

```sh
kalshi-mm15 altfeed-ping --n 30 > data/altfeed/ping_$(date +%s).csv   # pre-flight
kalshi-mm15 altfeed --minutes 720 --out data/altfeed &                # spot, no auth
kalshi-mm15 probe   --minutes 720 --index --dump 3 --out data/altfeed & # book + index
wait
python3 altfeed_score.py data/altfeed/altfeed_*.csv.gz \
    --kalshi data/altfeed/tape_*.csv.gz --index data/altfeed/index_*.jsonl.gz \
    --json data/altfeed/race.json
```

12 hours, so every asset sees both a US and an Asia session. `--dump 3` on the first run is not
optional: the `cfbenchmarks_value` frame schema has never been captured successfully here (the
previous attempt 401'd on a dead key and wrote nothing), so the first three frames of each kind
must be recorded verbatim before anything parses them.

## Pre-declared decisions, with the numbers that make them

**D1 — which venues the engine subscribes (`--spot-venues`), per asset.** Keep a venue iff, on
that asset, it wins at least **10%** of scored race events at ≥5 bps, over at least **30** scored
events. Below 30 events the cell is UNDERPOWERED and the default list stands unchanged. This is
deliberately a *race* criterion and not a correlation one: a venue can be perfectly correlated and
still arrive too late to act on.

**D2 — the minimum venue count (`--spot-min-venues`).** Keep 3 unless fewer than 3 venues clear
D1 for an asset, in which case that asset is dropped from the spot gate rather than run on a
one-venue median. A two-venue median is one venue plus a tiebreak.

**D3 — is the consolidated feed actually ahead of Kalshi?** The headline is
`reference_lead_over_kalshi`. A positive lead in milliseconds is the budget any spot-driven
decision has. **If it is not at least 5 ms on an asset, that asset's spot gate is not worth its
sockets** — our own order needs 2.7–3.0 ms to reach the book.

**D4 — the taker scalp.** `altfeed_taker.py` with `--bps 2 5 10 20`, which is the same replay,
the same fee `ceil(7p(1-p))`, and the same race rule as `latency_arb.py` — only the signal
changed, from one venue to the median of several. The prior to beat is explicit and negative:
**−1.0 to −3.0 c/ct after fee at every threshold** over 12 h and 9 series on 2026-09-27, with the
diagnosis that *winning the race selects the quotes that correctly did not need to reprice*. The
taker arm is promoted to a prospective shadow only if the won-race mean at +60 s is **positive
with a standard error excluding zero at two or more thresholds**, on ≥200 won events, **and**
positive in both chronological halves. Anything less reproduces the September closure and is
recorded as such. There is no live taker path in the engine and this measurement does not create
one.

**D5 — amend-only.** Independent of D1–D4, because it is an execution change and not a signal.
Measured gain: amend→book **2.6–2.9 ms** against cancel **4.79 ms** plus a later create **2.7–3.0
ms**, so a pull by amend is one round trip at the fastest verb where cancel-then-repost is two.
`--amend-only` makes every requote and every spot pull an amend, parking the quote
`--pull-amend-ticks` (3) clear of the others' touch instead of removing it. It falls back to
cancel on a partial fill, an unacked order, a price off the grid, or token pressure, and it never
applies when a gate has refused to quote at all — amend expresses "move this quote", only cancel
expresses "there is no quote". Validate on the box with `live --dry-run` first and read the
`spot_pull` rows' `how` field: a run with no `how:"cancel"` rows has not exercised the fallback,
and a run with no `how:"amend"` rows has not exercised the feature.

## What this cannot answer

* **It does not reopen any closed maker branch.** `kalshi-maker-latency-cliff` puts 69% of the
  cancel-latency loss inside 10 ms and the 0 ms row is clairvoyance, not the fast-trader limit. A
  faster feed moves us within that band; it does not leave it.
* Exchange-to-receipt delay includes each venue's own clock skew. Coinbase Advanced Trade already
  reads **negative** from a laptop. Read the shape and the ordering, never the last microsecond,
  and never compare one venue's absolute delay to another's as if the clocks agreed.
* Venues that batch frames inside one TCP segment get the second and later frames stamped at our
  *drain* time, not their arrival (`a-receipt-timestamp-measures-drain-time-not-arrival`). The
  race only uses the first crossing per event, which is the quantile this does not corrupt.
* `altfeed_taker.py` is a paper replay against the **displayed** touch. It is an upper bound on a
  fill and never a fill claim.
* The Kalshi tape carries no close time, so the replay attributes an event to the market that
  updated most recently — held identical to `latency_arb.py` so the two numbers compare.
