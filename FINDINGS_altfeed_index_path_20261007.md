# FINDINGS — the settlement index is 26–73 ms late, and a TCP handshake does not predict a feed (2026-10-07)

Two results, both from the az2 box (`i-0f25ca08ce8d9d016`, chrony RMS 2.6 µs off NTP). The
12-hour venue race preregistered in `PREREG_altfeed_venue_race_20261007.md` is still running;
these are the parts that are already complete, and one of them changes how the race should be
read.

## 1. The `cfbenchmarks_value` channel captured successfully, and its schema decomposes the delay

First successful capture of this channel here. The 2026-10-06 attempt 401'd on a dead key and
wrote nothing (`OVERNIGHT_20261006_STATUS.md`), so the schema below was unknown until now. Ten
minutes, all nine indices, both channels, 45,600 frames.

The two channels carry **different shapes**, and a reader that knows only one silently drops the
other:

```
cfbenchmarks_value_5hz  msg: {index_id, value_usd:"2609.65000000", source_ts_ms, received_at,
                              data:"<CF payload verbatim>"}
cfbenchmarks_value      msg: {index_id, received_at, data:"<CF payload>",
                              avg_60s_data:{value, window_start_ts_ms,
                                            window_end_ts_exclusive, window_size}}
frame: {type, sid, seq, sending_ts_ms}
```

- The value is **`value_usd`**, a dollar STRING, on the 5 Hz channel, and is nested inside the
  `data` JSON **string** on the 1 Hz one. There is no `value` field at the top level of either.
- The 1 Hz channel has **no `source_ts_ms`**; CF's own stamp is `data.time`.
- **`avg_60s_data` is the settlement variable served progressively** — the 60-second average the
  contract compares — and it appears only on the 1 Hz channel. (`window_size` reads 0, which is
  not explained here and was not pursued; the exact-endgame branch is already closed.)

Because every frame carries Kalshi's **receive** and **send** stamps, the index's lateness splits
into three legs belonging to three different parties. Medians over 10 minutes:

| index | cadence | CF→Kalshi | Kalshi queue | Kalshi→us | **CF→us** |
| --- | ---: | ---: | ---: | ---: | ---: |
| BRTI | 200 ms | 34.0 | 3.0 | 1.6 | **38.0** |
| DOGEUSD_RTI | 200 ms | 21.0 | 3.0 | 1.6 | **26.0** |
| SOLUSD_RTI | 200 ms | 24.0 | 3.0 | 1.6 | **29.8** |
| XRPUSD_RTI | 200 ms | 24.0 | 3.0 | 1.6 | **29.6** |
| ETHUSD_RTI | 200 ms | 32.0 | 3.0 | 1.6 | **36.5** |
| BNBUSD_RTI | 1 s | 64.0 | 3.0 | 1.6 | **69.1** |
| HYPEUSD_RTI | 1 s | 64.0 | 3.0 | 1.6 | **68.9** |
| NEARUSD_RTI | 1 s | 64.0 | 3.0 | 1.6 | **69.4** |
| ZECUSD_RTI | 1 s | 63.0 | 3.0 | 1.6 | **68.0** |

### Three things in that table are decision-relevant

**BTC is on the 5 Hz path.** `collect_cf_exact_endgame.py` hardcodes
`FIVE_HZ = {ETH, SOL, XRP, DOGE}` and omits BTC. Measured, **BRTI runs at 200 ms** like the other
four. Five of nine are 5 Hz; BNB, HYPE, NEAR and ZEC are 1 Hz **on both channels** — subscribing
`cfbenchmarks_value_5hz` does not make them faster.

**Kalshi→us is 1.6 ms, not 5.7 ms.** The orderbook delta's venue `ts` → our receipt is
**5.7–6.2 ms** (re-measured at 6.19 ms in this same run). The index frame's `sending_ts_ms` → our
receipt is **1.6 ms**, on the same socket, between the same two clocks. So of the ~6 ms orderbook
feed age, roughly **1.6 ms is network and ~4.5 ms is Kalshi-internal** (stamp → send). That
sharpens a guess in `kalshi-15m-crypto-competitors-react-twice-as-fast-as-the-ohio-box` ("~5ms of
the feed is Kalshi's own publish delay") into a measurement, and it prices the two levers: a
private network path can buy at most ~1.6 ms, while FIX market data would have to attack the
~4.5 ms that is inside the venue. *Caveat:* the two numbers may stamp different events (`ts` on a
delta vs `sending_ts_ms` on an index frame), so treat ~4.5 ms as the internal leg's order of
magnitude, not a certified constant.

**The widest books have the slowest index.** HYPE, NEAR, ZEC and BNB — the thin alts, and
`zec-near-are-the-widest-15m-books` — reach us **68–69 ms** after CF stamps them, against 26–38 ms
for the liquid five, and at a fifth of the cadence. Whatever a direct spot feed is worth, it is
worth most exactly there.

## 2. ⚠ A TCP handshake does not predict a feed's age. It is off by 250×.

`altfeed-ping` (30 keep-alive TCP connects per venue host) against the actual exchange-stamp →
our-receipt delay from the same box, 24.6 min of capture, 440,179 quotes:

| venue | TCP handshake p50 | **data delay p50** | ratio |
| --- | ---: | ---: | ---: |
| hyperliquid | **1.25 ms** | **~305 ms** | **244×** |
| gemini | 12.0 ms | *unstamped* | — |
| cb_adv | 16.6 ms | **12.2 ms** | 0.7× |
| crypto_com | 16.8 ms | 87.5 ms | 5× |
| okx | 16.9 ms | 89.5 ms | 5× |
| cb_ex | 17.1 ms | 13.2–17.6 ms | 1× |
| kraken | 17.1 ms | 47.9–59.7 ms | 3× |
| bitstamp | 96.2 ms | 56.9–68.2 ms | 0.6× |
| binance_us | 137.3 ms | *unstamped* | — |
| gate | 137.7 ms | ~70 ms | 0.5× |

**Hyperliquid is the nearest host on the network and the stalest feed by 4×.** Its handshake
terminates at a CDN edge ~1 ms from Ohio; the data comes from the origin. OKX and crypto.com are
the same shape at 5×. This is the identical trap already recorded for Kalshi itself —
`api.elections.kalshi.com` is a CloudFront edge whose 1.2 ms handshake is not the 6.3 ms
application round trip (`kalshi-latency-from-ohio-box-measured`) — and it reproduces on seven more
hosts at once.

**So `altfeed-ping` is demoted.** It is only useful for ruling a venue *in* on distance, never for
ranking. The delay column above is the real pre-flight, and the race is the real answer.

## 3. The premise this work started from is confirmed: the engine's single feed is blind on alts

Touch updates per second, same 24.6 min. The engine read **only `cb_ex`** until today:

| venue | BNB | BTC | DOGE | ETH | HYPE | NEAR | SOL | XRP | ZEC |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| **cb_ex** (what the engine had) | **0.1** | 1.6 | 0.3 | 1.0 | **0.1** | 0.8 | 0.5 | 0.6 | 0.6 |
| gemini | 2.5 | 5.6 | 2.8 | 2.4 | **55.0** | – | 3.8 | 7.3 | **33.1** |
| kraken | 2.9 | 1.9 | **25.2** | 1.6 | 1.3 | **16.8** | 1.4 | 3.3 | **20.5** |
| okx | 0.9 | 1.1 | 1.0 | 1.1 | **12.6** | 2.2 | 0.9 | 1.1 | 8.1 |
| gate | 0.8 | 0.9 | 1.4 | 1.0 | 1.3 | 2.8 | 0.9 | 0.8 | **9.8** |

**0.1 updates per second on BNB and HYPE** — one observation every ten seconds, against Gemini's
55/s on HYPE. The Coinbase Exchange `ticker` channel fires only on a Coinbase match, so on a thin
altcoin the engine's `--spot-bps` gate was reading an almost-static number. Whether the busier
venues also *lead* is the race's job, and the leader is clearly not the same venue for every
asset: Gemini on HYPE and ZEC, Kraken on DOGE and NEAR, OKX on HYPE, Gate on ZEC.

## Status

- 12 h `altfeed` capture running on the box since 08:55Z (ETA 20:55Z), 10 venues × 9 assets,
  0 dropped quotes, restart-supervised.
- 2 h `probe --index` running since 09:18Z for the spot-vs-index leg.
- The Kalshi book side comes from the pre-existing `gate_probe.sh` tapes, which cover the same
  wall clock — no second book collector was started, and nothing already running was touched.
- Scoring: `altfeed_score.py` (5.2 s per 12 min of capture after the FFT rewrite, so ~5 min for
  the full window) and `altfeed_taker.py`.

Nothing here promotes a strategy, and no order was placed: the `altfeed` verb has no credential
and no order path at all.
