//! `fastspot`: the fast external view of the 15M crypto underlying, per asset.
//!
//! Kalshi's own market data reaches us **5.7 ms** after the venue stamps it, and that wait is
//! 68% of our 8.5 ms reaction time (`FINDINGS_signing_and_transport_20261006.md`). Our own
//! decision path is 45 µs, so there is nothing left to win locally. The one view that is not
//! behind Kalshi's publisher is the underlying itself: every asset in the 15M ladder trades on
//! public spot venues we can read directly, and the 15M market settles on a CF Benchmarks index
//! computed from a subset of them.
//!
//! The engine already reads **one** feed — Coinbase Exchange `ticker`, one socket, BTC-shaped
//! (`shadow::spot_feed_impl`). That channel only fires on a Coinbase *match*, which on a thin
//! altcoin is a poor clock: NEAR or ZEC can hold a stale Coinbase print for minutes while the
//! quote moves everywhere else. This module replaces it with a per-asset, multi-venue
//! **top-of-book** feed, and ships the race probe that has to decide which venues are worth a
//! socket before any of it is believed.
//!
//! Market data only: no credentials, no orders, no writes to the venue.
//!
//! * [`feed`] — one task per venue; emits every top-of-book change on our receipt clock.
//! * [`probe`] — capture every venue to one gzip CSV for `altfeed_score.py`.
//! * [`ping`] — TCP handshake RTT per venue host, the cheap pre-flight for "is this venue even
//!   near Ohio".
//! * [`Consolidated`] — the live aggregate the engine consumes: a per-venue return, then the
//!   median across fresh venues, which is immune to the USDT/perp basis.

use std::{
    collections::{BTreeMap, HashMap},
    path::PathBuf,
    time::Duration,
};

use anyhow::{Context, Result};
use futures_util::{SinkExt, StreamExt};
use serde_json::{Value, json};
use tokio::{sync::mpsc, time::interval};
use tokio_tungstenite::tungstenite::{Message, client::IntoClientRequest};

use crate::{rfc3339_us, unix_us};

/// The nine assets with a Kalshi 15M series.
pub const ASSETS: [&str; 9] = ["BTC", "ETH", "SOL", "XRP", "DOGE", "HYPE", "BNB", "ZEC", "NEAR"];

/// `KXETH15M` -> `ETH`. Returns `None` for anything that is not a crypto 15M series, so a mixed
/// run (commodities, CRYPTOLEAD) cannot subscribe `GOLD-USD` and friends.
pub fn asset_of_series(series: &str) -> Option<&'static str> {
    let core = series.trim_start_matches("KX").trim_end_matches("15M");
    ASSETS.iter().copied().find(|a| *a == core)
}

#[derive(Clone, Copy, PartialEq, Eq, Hash, Debug)]
pub enum Venue {
    /// Coinbase Exchange `ticker`: fires on a match only, carries the touch with it.
    CbEx,
    /// Coinbase Advanced Trade `ticker`: same trigger, different publisher and envelope clock.
    CbAdv,
    /// Kraken v2 `ticker` with `event_trigger: bbo` — a true quote feed.
    Kraken,
    /// Binance.US `@bookTicker` — a true quote feed, smallest frame of the set, but **unstamped**.
    BinanceUs,
    /// OKX `bbo-tbt` — tick-by-tick quote feed, USDT quote.
    Okx,
    /// Gate.io `spot.book_ticker` — true quote feed, USDT quote.
    Gate,
    /// Bitstamp `order_book_*` — full 100-level snapshot, so the touch is element 0.
    Bitstamp,
    /// Crypto.com `book.*.10` — 10-level snapshot.
    CryptoCom,
    /// Gemini `l2` — incremental; we keep a book to recover the touch.
    Gemini,
    /// Hyperliquid `bbo` — the perp, and for HYPE the primary market.
    Hyperliquid,
}

use Venue::*;

/// Every venue this module can read. `probe` defaults to all of them; the engine should run the
/// subset the probe says leads, because each one is a socket and a reconnect to supervise.
pub const ALL: [Venue; 10] =
    [CbEx, CbAdv, Kraken, BinanceUs, Okx, Gate, Bitstamp, CryptoCom, Gemini, Hyperliquid];

impl Venue {
    pub fn name(self) -> &'static str {
        match self {
            CbEx => "cb_ex",
            CbAdv => "cb_adv",
            Kraken => "kraken",
            BinanceUs => "binance_us",
            Okx => "okx",
            Gate => "gate",
            Bitstamp => "bitstamp",
            CryptoCom => "crypto_com",
            Gemini => "gemini",
            Hyperliquid => "hyperliquid",
        }
    }

    pub fn parse_name(s: &str) -> Option<Venue> {
        ALL.into_iter().find(|v| v.name() == s)
    }

    /// What the venue's prices are denominated in. A USDT or perp venue carries a basis against
    /// the USD index the contract settles on, so its *level* is not comparable and only its
    /// *return* is. [`Consolidated`] relies on this.
    pub fn quote(self) -> &'static str {
        match self {
            CbEx | CbAdv | Kraken | BinanceUs | Bitstamp | CryptoCom | Gemini => "USD",
            Okx | Gate => "USDT",
            Hyperliquid => "PERP",
        }
    }

    /// `true` when the venue stamps its own frames, i.e. exchange-to-receipt delay is measurable.
    /// Binance.US `@bookTicker` carries only an update id, so its row has `exch_us = 0` and it can
    /// only ever be scored on the receipt clock.
    pub fn stamped(self) -> bool {
        !matches!(self, BinanceUs | Gemini)
    }

    fn host_port(self) -> (&'static str, u16) {
        match self {
            CbEx => ("ws-feed.exchange.coinbase.com", 443),
            CbAdv => ("advanced-trade-ws.coinbase.com", 443),
            Kraken => ("ws.kraken.com", 443),
            BinanceUs => ("stream.binance.us", 9443),
            Okx => ("ws.okx.com", 8443),
            Gate => ("api.gateio.ws", 443),
            Bitstamp => ("ws.bitstamp.net", 443),
            CryptoCom => ("stream.crypto.com", 443),
            Gemini => ("api.gemini.com", 443),
            Hyperliquid => ("api.hyperliquid.xyz", 443),
        }
    }

    /// The venue's symbol for `asset`, or `None` when it is not listed there.
    pub fn symbol(self, asset: &str) -> Option<String> {
        let lower = asset.to_lowercase();
        Some(match self {
            CbEx | CbAdv => format!("{asset}-USD"),
            // Kraken's REST `wsname` is the **v1** vocabulary (XBT/USD, XDG/USD) and v2 rejects
            // it: "Currency pair not supported XBT/USD". v2 is plain BTC/USD and DOGE/USD.
            // Measured 2026-10-07; the first build subscribed the REST names and silently had no
            // BTC or DOGE rows for the whole capture.
            Kraken => format!("{asset}/USD"),
            BinanceUs => format!("{lower}usd"),
            Okx => format!("{asset}-USDT"),
            Gate => format!("{asset}_USDT"),
            Bitstamp => format!("{lower}usd"),
            // Crypto.com lists neither BNB nor ZEC against USD.
            CryptoCom if asset == "BNB" || asset == "ZEC" => return None,
            CryptoCom => format!("{asset}_USD"),
            // Gemini has no NEAR book.
            Gemini if asset == "NEAR" => return None,
            Gemini => format!("{}USD", asset.to_uppercase()),
            Hyperliquid => asset.to_owned(),
        })
    }

    /// WebSocket URL. Binance is the only venue that puts the subscription in the path.
    fn url(self, symbols: &[String]) -> String {
        let (h, p) = self.host_port();
        match self {
            BinanceUs => {
                let streams =
                    symbols.iter().map(|s| format!("{s}@bookTicker")).collect::<Vec<_>>().join("/");
                format!("wss://{h}:{p}/stream?streams={streams}")
            }
            Kraken => format!("wss://{h}:{p}/v2"),
            Okx => format!("wss://{h}:{p}/ws/v5/public"),
            Gate => format!("wss://{h}:{p}/ws/v4/"),
            CryptoCom => format!("wss://{h}:{p}/exchange/v1/market"),
            Gemini => format!("wss://{h}:{p}/v2/marketdata"),
            Hyperliquid => format!("wss://{h}:{p}/ws"),
            _ => format!("wss://{h}:{p}"),
        }
    }

    /// Subscribe frames to send after the handshake.
    fn subs(self, symbols: &[String]) -> Vec<Value> {
        match self {
            CbEx => vec![json!({"type": "subscribe", "product_ids": symbols, "channels": ["ticker"]})],
            CbAdv => vec![json!({"type": "subscribe", "product_ids": symbols, "channel": "ticker"})],
            Kraken => vec![json!({"method": "subscribe", "params": {
                "channel": "ticker", "symbol": symbols, "event_trigger": "bbo"}})],
            BinanceUs => vec![],
            Okx => vec![json!({"op": "subscribe", "args": symbols.iter()
                .map(|s| json!({"channel": "bbo-tbt", "instId": s})).collect::<Vec<_>>()})],
            Gate => vec![json!({"time": unix_us() / 1_000_000, "channel": "spot.book_ticker",
                "event": "subscribe", "payload": symbols})],
            // Bitstamp takes one channel per command.
            Bitstamp => symbols.iter()
                .map(|s| json!({"event": "bts:subscribe", "data": {"channel": format!("order_book_{s}")}}))
                .collect(),
            CryptoCom => vec![json!({"id": 1, "method": "subscribe", "nonce": unix_us() / 1_000,
                "params": {"channels": symbols.iter().map(|s| format!("book.{s}.10")).collect::<Vec<_>>()}})],
            Gemini => vec![json!({"type": "subscribe",
                "subscriptions": [{"name": "l2", "symbols": symbols}]})],
            // Hyperliquid takes one subscription per command.
            Hyperliquid => symbols.iter()
                .map(|s| json!({"method": "subscribe", "subscription": {"type": "bbo", "coin": s}}))
                .collect(),
        }
    }

    /// Application-level keepalive the venue requires: `(every_seconds, frame)`. Transport pings
    /// are answered separately; these are the venues that idle-disconnect regardless.
    fn keepalive(self) -> Option<(u64, String)> {
        match self {
            Okx => Some((20, "ping".into())),
            Gate => Some((20, json!({"time": 0, "channel": "spot.ping"}).to_string())),
            Hyperliquid => Some((45, json!({"method": "ping"}).to_string())),
            _ => None,
        }
    }
}

/// One top-of-book observation, on our clock.
#[derive(Clone, Debug, PartialEq)]
pub struct Quote {
    pub venue: Venue,
    pub asset: &'static str,
    /// Our receipt of the frame, µs since epoch. The only clock the engine can act on.
    pub recv_us: i64,
    /// The venue's own stamp, µs since epoch, or 0 when the venue does not send one.
    pub exch_us: i64,
    pub bid: f64,
    pub ask: f64,
}

impl Quote {
    pub fn mid(&self) -> f64 {
        (self.bid + self.ask) / 2.0
    }
}

fn f64_of(v: &Value) -> Option<f64> {
    v.as_f64().or_else(|| v.as_str().and_then(|s| s.parse().ok()))
}

fn i64_of(v: &Value) -> Option<i64> {
    v.as_i64().or_else(|| v.as_str().and_then(|s| s.parse().ok()))
}

/// Per-connection state a venue needs to turn frames into quotes. Only Gemini uses the book.
#[derive(Default)]
struct VenueState {
    /// Gemini l2 is incremental: symbol -> (bids, asks) keyed by price in 1e-8 units.
    books: HashMap<String, [BTreeMap<i64, f64>; 2]>,
    /// Last quote emitted per asset, for the touch-change filter below.
    last: HashMap<&'static str, (f64, f64, i64)>,
}

/// A venue that publishes depth (Gemini l2) or repeated snapshots (Bitstamp, crypto.com) reports
/// a change at every level, most of which leave the touch alone: Gemini alone sent **48k frames a
/// minute** across nine assets in the first smoke run. Keep only the frames that moved the touch
/// — an unchanged quote is not an observation of a move and would just weight the race by a
/// venue's chattiness.
///
/// The exception is the once-a-second repeat. Without it an unchanged quote is indistinguishable
/// from a dead socket, and "stale" would come to mean "has not moved" on exactly the thin
/// altcoins where a quote legitimately sits still for minutes.
const REPEAT_US: i64 = 1_000_000;

impl VenueState {
    fn is_new(&mut self, q: &Quote) -> bool {
        match self.last.get(&q.asset) {
            Some(&(b, a, t))
                if b == q.bid && a == q.ask && q.recv_us - t < REPEAT_US => false,
            _ => {
                self.last.insert(q.asset, (q.bid, q.ask, q.recv_us));
                true
            }
        }
    }
}

const PX_SCALE: f64 = 1e8;

/// What a frame asks of the connection beyond yielding quotes.
#[derive(Debug, PartialEq)]
enum Action {
    Nothing,
    /// Send this frame back (crypto.com's heartbeat).
    Reply(String),
    /// The venue refused something. Report it loudly; a rejected subscribe is otherwise
    /// indistinguishable from a quiet market, which is how the first build of this module ran a
    /// whole capture with no Kraken BTC rows at all.
    Warn(String),
}

/// A subscribe rejection, in each venue's own shape. Checked before the quote branches because
/// a rejection frame carries no prices and would otherwise fall through to `Nothing`.
fn rejection(venue: Venue, v: &Value) -> Option<String> {
    let s = |x: &Value| x.as_str().map(str::to_owned);
    match venue {
        CbEx | CbAdv => (v["type"] == "error").then(|| s(&v["message"]).unwrap_or_else(|| v.to_string())),
        Kraken => (v["success"] == false).then(|| v.to_string()),
        BinanceUs => v["error"].is_object().then(|| v["error"].to_string()),
        Okx => (v["event"] == "error").then(|| v.to_string()),
        Gate => (v["error"].is_object() || (v["event"] == "subscribe" && v["result"]["status"] != "success"))
            .then(|| v.to_string()),
        Bitstamp => (v["event"] == "bts:error").then(|| v.to_string()),
        CryptoCom => (v["method"] == "subscribe" && v["code"].as_i64().unwrap_or(0) != 0)
            .then(|| v.to_string()),
        Gemini => (v["result"] == "error").then(|| v.to_string()),
        Hyperliquid => (v["channel"] == "error").then(|| v.to_string()),
    }
}

/// Decode one frame into zero or more quotes. `out` is cleared by the caller, never by us, so a
/// venue that packs several symbols per frame appends.
///
/// `Err` is returned only for a protocol-level reason to drop the connection (Bitstamp's
/// reconnect request).
fn decode(
    venue: Venue,
    v: &Value,
    recv_us: i64,
    by_symbol: &HashMap<String, &'static str>,
    st: &mut VenueState,
    out: &mut Vec<Quote>,
) -> Result<Action> {
    if let Some(why) = rejection(venue, v) {
        return Ok(Action::Warn(why));
    }
    let mut push = |symbol: &str, exch_us: i64, bid: Option<f64>, ask: Option<f64>| {
        let (Some(asset), Some(bid), Some(ask)) = (by_symbol.get(symbol), bid, ask) else { return };
        if !(bid.is_finite() && ask.is_finite() && bid > 0.0 && ask > 0.0) { return }
        out.push(Quote { venue, asset, recv_us, exch_us, bid, ask });
    };
    match venue {
        CbEx => {
            if v["type"] == "ticker" {
                let t = v["time"].as_str().and_then(rfc3339_us).unwrap_or(0);
                push(v["product_id"].as_str().unwrap_or(""), t,
                     f64_of(&v["best_bid"]), f64_of(&v["best_ask"]));
            }
        }
        CbAdv => {
            if v["channel"] == "ticker" {
                let t = v["timestamp"].as_str().and_then(rfc3339_us).unwrap_or(0);
                for e in v["events"].as_array().into_iter().flatten() {
                    for tk in e["tickers"].as_array().into_iter().flatten() {
                        push(tk["product_id"].as_str().unwrap_or(""), t,
                             f64_of(&tk["best_bid"]), f64_of(&tk["best_ask"]));
                    }
                }
            }
        }
        Kraken => {
            if v["channel"] == "ticker" {
                for d in v["data"].as_array().into_iter().flatten() {
                    let t = d["timestamp"].as_str().and_then(rfc3339_us).unwrap_or(0);
                    push(d["symbol"].as_str().unwrap_or(""), t, f64_of(&d["bid"]), f64_of(&d["ask"]));
                }
            }
        }
        BinanceUs => {
            // Combined stream: {"stream":"ethusd@bookTicker","data":{...}}; raw if one symbol.
            let d = if v["data"].is_object() { &v["data"] } else { v };
            if let Some(s) = d["s"].as_str() {
                push(&s.to_lowercase(), 0, f64_of(&d["b"]), f64_of(&d["a"]));
            }
        }
        Okx => {
            if v["arg"]["channel"] == "bbo-tbt" {
                let sym = v["arg"]["instId"].as_str().unwrap_or("").to_owned();
                for d in v["data"].as_array().into_iter().flatten() {
                    let t = i64_of(&d["ts"]).unwrap_or(0) * 1_000;
                    push(&sym, t, f64_of(&d["bids"][0][0]), f64_of(&d["asks"][0][0]));
                }
            }
        }
        Gate => {
            if v["channel"] == "spot.book_ticker" && v["event"] == "update" {
                let r = &v["result"];
                let t = i64_of(&r["t"]).unwrap_or(0) * 1_000;
                push(r["s"].as_str().unwrap_or(""), t, f64_of(&r["b"]), f64_of(&r["a"]));
            }
        }
        Bitstamp => {
            // The venue asks us to reconnect rather than closing; honour it or the socket goes
            // quiet with no error.
            anyhow::ensure!(v["event"] != "bts:request_reconnect", "bitstamp requested reconnect");
            if v["event"] == "data" {
                let sym = v["channel"].as_str().unwrap_or("").trim_start_matches("order_book_");
                let d = &v["data"];
                let t = i64_of(&d["microtimestamp"]).unwrap_or(0);
                push(sym, t, f64_of(&d["bids"][0][0]), f64_of(&d["asks"][0][0]));
            }
        }
        CryptoCom => {
            if v["method"] == "public/heartbeat" {
                return Ok(Action::Reply(json!({"id": v["id"], "method": "public/respond_heartbeat"}).to_string()));
            }
            let r = &v["result"];
            if r["channel"] == "book" {
                let sym = r["instrument_name"].as_str().unwrap_or("").to_owned();
                for d in r["data"].as_array().into_iter().flatten() {
                    // `t` is the book's publish time, `tt` the venue's transaction time.
                    let t = i64_of(&d["t"]).or_else(|| i64_of(&d["tt"])).unwrap_or(0) * 1_000;
                    push(&sym, t, f64_of(&d["bids"][0][0]), f64_of(&d["asks"][0][0]));
                }
            }
        }
        Gemini => {
            // l2: one unstamped snapshot of every level, then incremental changes. Qty 0 removes.
            let Some(sym) = v["symbol"].as_str() else { return Ok(Action::Nothing) };
            if !by_symbol.contains_key(sym) { return Ok(Action::Nothing) }
            let Some(changes) = v["changes"].as_array() else { return Ok(Action::Nothing) };
            let book = st.books.entry(sym.to_owned()).or_default();
            for c in changes {
                let (Some(side), Some(px), Some(qty)) =
                    (c[0].as_str(), f64_of(&c[1]), f64_of(&c[2])) else { continue };
                let k = (px * PX_SCALE).round() as i64;
                let m = &mut book[usize::from(side == "sell")];
                if qty == 0.0 { m.remove(&k); } else { m.insert(k, qty); }
            }
            let bid = book[0].keys().next_back().map(|k| *k as f64 / PX_SCALE);
            let ask = book[1].keys().next().map(|k| *k as f64 / PX_SCALE);
            push(sym, 0, bid, ask);
        }
        Hyperliquid => {
            if v["channel"] == "bbo" {
                let d = &v["data"];
                let t = i64_of(&d["time"]).unwrap_or(0) * 1_000;
                push(d["coin"].as_str().unwrap_or(""), t,
                     f64_of(&d["bbo"][0]["px"]), f64_of(&d["bbo"][1]["px"]));
            }
        }
    }
    Ok(Action::Nothing)
}

/// TLS WebSocket connect with `TCP_NODELAY`, which `connect_async` does not set. Our outbound
/// traffic is only subscribes and pings, so this matters less than on the order path — but a
/// 40 ms Nagle delay on a subscribe is 40 ms of a capture that is measuring milliseconds.
async fn connect(url: &str, host: &str, port: u16) -> Result<
    tokio_tungstenite::WebSocketStream<tokio_tungstenite::MaybeTlsStream<tokio::net::TcpStream>>,
> {
    let tcp = tokio::net::TcpStream::connect((host, port)).await?;
    tcp.set_nodelay(true)?;
    let (ws, _) = tokio_tungstenite::client_async_tls_with_config(
        url.into_client_request()?,
        tcp,
        None,
        None,
    )
    .await?;
    Ok(ws)
}

/// What the venue task reports upstream: a quote, or a connection-lifecycle event worth taping.
#[derive(Debug)]
pub enum Event {
    Q(Quote),
    /// `(venue, what, detail)` — `connected`, `disconnected`, `connect_error`, `ws_error`,
    /// `venue_error`. Taped with a `#` prefix so a scorer can see blind time.
    Note(Venue, &'static str, String),
}

/// One venue, forever: connect, subscribe, decode, reconnect with backoff. The backoff matters —
/// a bare retry loop became a 58-retry hammer and drew HTTP 429 once already
/// (`a-restart-supervisor-needs-backoff-or-it-becomes-a-hammer`).
async fn run_venue(
    venue: Venue,
    assets: Vec<&'static str>,
    tx: mpsc::Sender<Event>,
    dropped: std::sync::Arc<std::sync::atomic::AtomicU64>,
) {
    let by_symbol: HashMap<String, &'static str> =
        assets.iter().filter_map(|a| venue.symbol(a).map(|s| (s, *a))).collect();
    if by_symbol.is_empty() {
        let _ = tx.send(Event::Note(venue, "venue_error", "no listed symbol".into())).await;
        return;
    }
    let mut symbols: Vec<String> = by_symbol.keys().cloned().collect();
    symbols.sort();
    let url = venue.url(&symbols);
    let (host, port) = venue.host_port();
    let mut backoff_s = 1u64;
    let mut out: Vec<Quote> = Vec::with_capacity(16);
    loop {
        let mut ws = match connect(&url, host, port).await {
            Ok(ws) => ws,
            Err(e) => {
                let _ = tx.send(Event::Note(venue, "connect_error", e.to_string())).await;
                tokio::time::sleep(Duration::from_secs(backoff_s)).await;
                backoff_s = (backoff_s * 2).min(60);
                continue;
            }
        };
        let _ = tx.send(Event::Note(venue, "connected", String::new())).await;
        backoff_s = 1;
        let mut st = VenueState::default();
        let mut failed = false;
        for s in venue.subs(&symbols) {
            if ws.send(Message::Text(s.to_string().into())).await.is_err() {
                failed = true;
                break;
            }
        }
        let mut keep = interval(Duration::from_secs(venue.keepalive().map_or(3_600, |k| k.0)));
        keep.tick().await;
        while !failed {
            let msg = tokio::select! {
                _ = keep.tick(), if venue.keepalive().is_some() => {
                    let frame = venue.keepalive().unwrap().1;
                    if ws.send(Message::Text(frame.into())).await.is_err() { break }
                    continue;
                }
                m = ws.next() => m,
            };
            let text = match msg {
                Some(Ok(Message::Text(t))) => t,
                Some(Ok(Message::Ping(p))) => {
                    if ws.send(Message::Pong(p)).await.is_err() { break }
                    continue;
                }
                Some(Ok(_)) => continue,
                Some(Err(e)) => {
                    let _ = tx.send(Event::Note(venue, "ws_error", e.to_string())).await;
                    break;
                }
                None => break,
            };
            // Stamp before parsing: the parse is ours to pay, not the venue's to be blamed for.
            let recv_us = unix_us();
            let Ok(v) = serde_json::from_str::<Value>(&text) else { continue };
            out.clear();
            match decode(venue, &v, recv_us, &by_symbol, &mut st, &mut out) {
                Ok(Action::Reply(reply)) => {
                    if ws.send(Message::Text(reply.into())).await.is_err() { break }
                }
                Ok(Action::Warn(why)) => {
                    let _ = tx.send(Event::Note(venue, "rejected", why)).await;
                }
                Ok(Action::Nothing) => {}
                Err(e) => {
                    let _ = tx.send(Event::Note(venue, "venue_error", e.to_string())).await;
                    break;
                }
            }
            for q in out.drain(..) {
                if !st.is_new(&q) { continue }
                // A full channel means the consumer is behind. Dropping a quote only delays a
                // decision, so never block the socket for it — but COUNT it, because a quote the
                // engine never saw is indistinguishable from a quiet venue in the signal and
                // would otherwise show up as the feed getting mysteriously worse under load.
                if tx.try_send(Event::Q(q)).is_err() {
                    dropped.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
                    break;
                }
            }
        }
        let _ = tx.send(Event::Note(venue, "disconnected", String::new())).await;
        tokio::time::sleep(Duration::from_millis(500)).await;
    }
}

/// Spawn one task per venue for `assets`. The shared counter is incremented whenever a quote is
/// dropped because the consumer's channel was full; report it, because a silently dropped quote
/// looks exactly like a venue that went quiet.
pub fn feed(
    venues: &[Venue],
    assets: &[&'static str],
    tx: mpsc::Sender<Event>,
) -> (Vec<tokio::task::JoinHandle<()>>, std::sync::Arc<std::sync::atomic::AtomicU64>) {
    let dropped = std::sync::Arc::new(std::sync::atomic::AtomicU64::new(0));
    let tasks = venues
        .iter()
        .map(|v| tokio::spawn(run_venue(*v, assets.to_vec(), tx.clone(), dropped.clone())))
        .collect();
    (tasks, dropped)
}

/// The live aggregate the engine consumes.
///
/// Levels across venues are **not** comparable: OKX and Gate quote USDT and Hyperliquid quotes a
/// perp, each carrying its own basis against the USD index the contract settles on. Returns are.
/// So the aggregate is: one return per venue over the same window, then the median across venues
/// that are fresh. One stuck socket cannot then drag the signal, and a single venue's outlier
/// print cannot fire a pull on its own.
#[derive(Default)]
pub struct Consolidated {
    /// (asset, venue) -> recent (recv_us, mid), oldest first.
    hist: HashMap<(&'static str, Venue), std::collections::VecDeque<(i64, f64)>>,
    pub notes: u64,
}

impl Consolidated {
    /// Keep this much history per venue; must exceed the longest return window asked for.
    const KEEP_US: i64 = 30_000_000;

    pub fn apply(&mut self, e: &Event) {
        match e {
            Event::Q(q) => {
                let h = self.hist.entry((q.asset, q.venue)).or_default();
                h.push_back((q.recv_us, q.mid()));
                while h.front().is_some_and(|(t, _)| *t < q.recv_us - Self::KEEP_US) {
                    h.pop_front();
                }
            }
            Event::Note(..) => self.notes += 1,
        }
    }

    /// Per-venue return in bps over `window_us` ending at `now`, for venues whose last quote is
    /// no older than `max_age_us`. A venue needs an observation at or before the window's start
    /// or it contributes nothing — otherwise a venue that just connected reports the move it
    /// never saw.
    pub fn venue_rets_bps(
        &self,
        asset: &str,
        now: i64,
        window_us: i64,
        max_age_us: i64,
    ) -> Vec<(Venue, f64)> {
        let mut out = Vec::with_capacity(ALL.len());
        for ((a, v), h) in &self.hist {
            if *a != asset {
                continue;
            }
            let Some(&(last_t, last_px)) = h.back() else { continue };
            if now - last_t > max_age_us {
                continue;
            }
            let Some(&(_, base_px)) =
                h.iter().rev().find(|(t, _)| *t <= now - window_us) else { continue };
            if base_px > 0.0 {
                out.push((*v, (last_px / base_px - 1.0) * 10_000.0));
            }
        }
        out
    }

    /// Median of the per-venue returns, or `None` when fewer than `min_venues` are fresh. The
    /// minimum is the whole point: a two-venue median is one venue plus a tiebreak.
    pub fn ret_bps(
        &self,
        asset: &str,
        now: i64,
        window_us: i64,
        max_age_us: i64,
        min_venues: usize,
    ) -> Option<f64> {
        let mut r: Vec<f64> =
            self.venue_rets_bps(asset, now, window_us, max_age_us).into_iter().map(|(_, x)| x).collect();
        if r.len() < min_venues.max(1) {
            return None;
        }
        r.sort_by(f64::total_cmp);
        let n = r.len();
        Some(if n % 2 == 1 { r[n / 2] } else { (r[n / 2 - 1] + r[n / 2]) / 2.0 })
    }

    /// Median mid across fresh **USD-quoted** venues. A level is only meaningful on those, and
    /// only this can be compared with the settlement index.
    pub fn usd_mid(&self, asset: &str, now: i64, max_age_us: i64) -> Option<f64> {
        let mut m: Vec<f64> = self
            .hist
            .iter()
            .filter(|((a, v), _)| *a == asset && v.quote() == "USD")
            .filter_map(|(_, h)| h.back().copied())
            .filter(|(t, _)| now - *t <= max_age_us)
            .map(|(_, px)| px)
            .collect();
        if m.is_empty() {
            return None;
        }
        m.sort_by(f64::total_cmp);
        let n = m.len();
        Some(if n % 2 == 1 { m[n / 2] } else { (m[n / 2 - 1] + m[n / 2]) / 2.0 })
    }

    /// Venues currently fresh for `asset`, for a health line. A feed silently losing venues is
    /// how a gate becomes a no-op.
    pub fn fresh_venues(&self, asset: &str, now: i64, max_age_us: i64) -> usize {
        self.hist
            .iter()
            .filter(|((a, _), h)| {
                *a == asset && h.back().is_some_and(|(t, _)| now - *t <= max_age_us)
            })
            .count()
    }
}

/// The venue table, as CSV: what each socket is, what it quotes in, whether its frames are
/// stamped, and which assets it lists. `altfeed_score.py` needs the stamped/quote columns to know
/// which venues it may put in a level median and which it can only score on the receipt clock,
/// and an operator needs it to pick `--spot-venues`.
pub fn venues_table(assets: &[&'static str]) -> Result<()> {
    println!("venue,host,port,quote,stamped,listed_assets,missing");
    for v in ALL {
        let (h, p) = v.host_port();
        let listed: Vec<&str> = assets.iter().copied().filter(|a| v.symbol(a).is_some()).collect();
        let missing: Vec<&str> = assets.iter().copied().filter(|a| v.symbol(a).is_none()).collect();
        println!(
            "{},{h},{p},{},{},{},{}",
            v.name(),
            v.quote(),
            v.stamped(),
            listed.join(" "),
            missing.join(" ")
        );
    }
    Ok(())
}

/// TCP handshake RTT to each venue, `n` samples each. The cheap pre-flight: a venue 80 ms away
/// cannot win a race measured in milliseconds, and this costs one connect to find out.
pub async fn ping(venues: &[Venue], n: usize) -> Result<()> {
    println!("venue,host,port,n_ok,p50_us,min_us,max_us");
    for v in venues {
        let (host, port) = v.host_port();
        let mut us: Vec<i64> = Vec::with_capacity(n);
        for _ in 0..n {
            let t0 = unix_us();
            match tokio::time::timeout(
                Duration::from_secs(5),
                tokio::net::TcpStream::connect((host, port)),
            )
            .await
            {
                Ok(Ok(s)) => {
                    us.push(unix_us() - t0);
                    drop(s);
                }
                _ => {}
            }
            tokio::time::sleep(Duration::from_millis(50)).await;
        }
        us.sort_unstable();
        let q = |f: f64| us.get(((us.len() as f64 - 1.0) * f).round() as usize).copied().unwrap_or(-1);
        println!(
            "{},{host},{port},{},{},{},{}",
            v.name(),
            us.len(),
            q(0.5),
            us.first().copied().unwrap_or(-1),
            us.last().copied().unwrap_or(-1)
        );
    }
    Ok(())
}

/// Capture every requested venue to one gzip CSV on one receipt clock, for `altfeed_score.py`.
///
/// The row is `src,asset,recv_us,exch_us,bid,ask`; lifecycle events are `#`-prefixed so a scorer
/// can account for blind time instead of reading a gap as a quiet market.
pub async fn probe(
    out: PathBuf,
    minutes: u64,
    venues: Vec<Venue>,
    assets: Vec<&'static str>,
) -> Result<()> {
    use std::io::Write;
    std::fs::create_dir_all(&out)?;
    let path = out.join(format!("altfeed_{}.csv.gz", unix_us() / 1_000));
    let mut gz = flate2::write::GzEncoder::new(
        std::io::BufWriter::new(std::fs::File::create(&path)?),
        flate2::Compression::fast(),
    );
    writeln!(gz, "src,asset,recv_us,exch_us,bid,ask")?;
    eprintln!(
        "altfeed probe -> {} for {minutes} min | {} venues x {} assets",
        path.display(),
        venues.len(),
        assets.len()
    );

    // A generous queue: the writer is the only consumer and gzip is ~4 µs, but a 100-level
    // Bitstamp burst across 9 assets should never make a socket drop a frame.
    let (tx, mut rx) = mpsc::channel::<Event>(262_144);
    let (tasks, dropped) = feed(&venues, &assets, tx);
    let deadline = tokio::time::Instant::now() + Duration::from_secs(minutes * 60);
    let mut report = interval(Duration::from_secs(60));
    report.tick().await;
    // Every (venue, asset) cell the run CLAIMS to capture, seeded at zero. A cell that stays at
    // zero is the failure this probe is most likely to hide: a rejected symbol looks exactly like
    // a quiet market in the output, and a missing cell silently shrinks the race.
    let mut n: HashMap<(&str, &str), u64> = venues
        .iter()
        .flat_map(|v| assets.iter().filter(|a| v.symbol(a).is_some()).map(move |a| ((v.name(), *a), 0)))
        .collect();
    let mut rows = 0u64;
    loop {
        tokio::select! {
            _ = tokio::time::sleep_until(deadline) => break,
            _ = tokio::signal::ctrl_c() => { eprintln!("ctrl-c"); break }
            _ = report.tick() => {
                let mut by: Vec<_> = n.iter().map(|((v, a), c)| (*v, *a, *c)).collect();
                by.sort();
                let per_venue: BTreeMap<&str, u64> = by.iter().fold(BTreeMap::new(), |mut m, (v, _, c)| {
                    *m.entry(*v).or_default() += c; m
                });
                let elapsed = (minutes * 60) as i64
                    - deadline.duration_since(tokio::time::Instant::now()).as_secs() as i64;
                let lost = dropped.load(std::sync::atomic::Ordering::Relaxed);
                eprintln!("[{elapsed}s] rows {rows} dropped {lost} | {per_venue:?}");
                let silent: Vec<String> =
                    by.iter().filter(|(_, _, c)| *c == 0).map(|(v, a, _)| format!("{v}/{a}")).collect();
                if !silent.is_empty() {
                    eprintln!("  SILENT CELLS ({}): {}", silent.len(), silent.join(" "));
                }
                gz.flush()?;
            }
            ev = rx.recv() => {
                let Some(ev) = ev else { break };
                match ev {
                    Event::Q(q) => {
                        *n.entry((q.venue.name(), q.asset)).or_default() += 1;
                        rows += 1;
                        writeln!(gz, "{},{},{},{},{},{}",
                            q.venue.name(), q.asset, q.recv_us, q.exch_us, q.bid, q.ask)?;
                    }
                    Event::Note(v, what, detail) => {
                        let line = format!("#{},{what},{},{}", v.name(), unix_us(), detail.replace(',', ";"));
                        eprintln!("{line}");
                        writeln!(gz, "{line}")?;
                    }
                }
            }
        }
    }
    for t in tasks {
        t.abort();
    }
    gz.finish()?.flush()?;
    let mut by: Vec<_> = n.iter().map(|((v, a), c)| (*v, *a, *c)).collect();
    by.sort();
    let lost = dropped.load(std::sync::atomic::Ordering::Relaxed);
    eprintln!("done: {rows} rows ({lost} dropped) -> {}", path.display());
    for (v, a, c) in &by {
        eprintln!("  {v:12} {a:5} {c}{}", if *c == 0 { "   <-- SILENT" } else { "" });
    }
    let silent = by.iter().filter(|(_, _, c)| *c == 0).count();
    anyhow::ensure!(
        silent == 0,
        "{silent} of {} (venue, asset) cells produced no quote; the capture is not the race it \
         claims to be. Fix the symbol or drop the venue from --venues before scoring.",
        by.len()
    );
    Ok(())
}

/// Parse `--venues cb_ex,kraken` / `--assets ETH,ZEC`, rejecting an unknown name rather than
/// silently capturing fewer feeds than the run claims.
pub fn parse_venues(s: &str) -> Result<Vec<Venue>> {
    s.split(',')
        .map(str::trim)
        .filter(|x| !x.is_empty())
        .map(|x| Venue::parse_name(x).with_context(|| format!("unknown venue {x:?}")))
        .collect()
}

pub fn parse_assets(s: &str) -> Result<Vec<&'static str>> {
    s.split(',')
        .map(str::trim)
        .filter(|x| !x.is_empty())
        .map(|x| {
            let up = x.to_uppercase();
            ASSETS
                .iter()
                .copied()
                .find(|a| *a == up)
                .with_context(|| format!("unknown asset {x:?}"))
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn dec(venue: Venue, raw: &str, symbols: &[(&str, &'static str)]) -> Vec<Quote> {
        let by: HashMap<String, &'static str> =
            symbols.iter().map(|(s, a)| ((*s).to_owned(), *a)).collect();
        let mut st = VenueState::default();
        let mut out = vec![];
        decode(venue, &serde_json::from_str(raw).unwrap(), 7, &by, &mut st, &mut out).unwrap();
        out
    }

    /// Every shape below is a verbatim frame captured from the venue on 2026-10-07 by
    /// `idea_lab/ws_shape_probe.py`, not a shape copied out of a doc.
    #[test]
    fn decoders_read_verbatim_frames() {
        let q = |venue, exch_us, bid, ask| Quote { venue, asset: "ETH", recv_us: 7, exch_us, bid, ask };

        assert_eq!(
            dec(CbEx, r#"{"type":"ticker","product_id":"ETH-USD","best_bid":"2616.26","best_ask":"2616.27","time":"2026-10-07T07:26:35.225762Z"}"#, &[("ETH-USD", "ETH")]),
            vec![q(CbEx, 1791357995225762, 2616.26, 2616.27)]
        );
        assert_eq!(
            dec(CbAdv, r#"{"channel":"ticker","timestamp":"2026-10-07T07:26:39.064486276Z","events":[{"type":"update","tickers":[{"product_id":"ETH-USD","best_bid":"2616.26","best_ask":"2616.27"}]}]}"#, &[("ETH-USD", "ETH")]),
            vec![q(CbAdv, 1791357999064486, 2616.26, 2616.27)]
        );
        assert_eq!(
            dec(Kraken, r#"{"channel":"ticker","type":"snapshot","data":[{"symbol":"ETH/USD","bid":2615.91,"ask":2615.92,"timestamp":"2026-10-07T07:26:38.154167Z"}]}"#, &[("ETH/USD", "ETH")]),
            vec![q(Kraken, 1791357998154167, 2615.91, 2615.92)]
        );
        assert_eq!(
            dec(BinanceUs, r#"{"stream":"ethusd@bookTicker","data":{"u":3793150009,"s":"ETHUSD","b":"2616.48000000","B":"0.0391","a":"2616.56000000","A":"0.1484"}}"#, &[("ethusd", "ETH")]),
            vec![q(BinanceUs, 0, 2616.48, 2616.56)]
        );
        assert_eq!(
            dec(Okx, r#"{"arg":{"channel":"bbo-tbt","instId":"ETH-USDT"},"data":[{"asks":[["2617.22","4.2908","0","8"]],"bids":[["2617.21","4.984","0","3"]],"ts":"1791357997507"}]}"#, &[("ETH-USDT", "ETH")]),
            vec![q(Okx, 1791357997507000, 2617.21, 2617.22)]
        );
        assert_eq!(
            dec(Gate, r#"{"channel":"spot.book_ticker","event":"update","result":{"t":1791358083012,"s":"ETH_USDT","b":"1323.93","B":"0.081","a":"1324.15","A":"0.073"}}"#, &[("ETH_USDT", "ETH")]),
            vec![q(Gate, 1791358083012000, 1323.93, 1324.15)]
        );
        assert_eq!(
            dec(Bitstamp, r#"{"event":"data","channel":"order_book_ethusd","data":{"microtimestamp":"1791357997711238","bids":[["2616.35","5.169015"],["2616.33","11.466956"]],"asks":[["2616.40","1.0"]]}}"#, &[("ethusd", "ETH")]),
            vec![q(Bitstamp, 1791357997711238, 2616.35, 2616.40)]
        );
        assert_eq!(
            dec(CryptoCom, r#"{"id":-1,"method":"subscribe","code":0,"result":{"instrument_name":"ETH_USD","subscription":"book.ETH_USD.10","channel":"book","depth":10,"t":1791358083012,"data":[{"t":1791358083012,"asks":[["2616.72","3.2521","9"]],"bids":[["2616.71","0.9655","2"]]}]}}"#, &[("ETH_USD", "ETH")]),
            vec![q(CryptoCom, 1791358083012000, 2616.71, 2616.72)]
        );
        assert_eq!(
            dec(Hyperliquid, r#"{"channel":"bbo","data":{"coin":"ETH","time":1791358082778,"bbo":[{"px":"90.95","sz":"3.0","n":1},{"px":"90.951","sz":"562.22","n":5}]}}"#, &[("ETH", "ETH")]),
            vec![q(Hyperliquid, 1791358082778000, 90.95, 90.951)]
        );
    }

    /// Gemini ships a snapshot then deltas, and a `0.0` quantity is a removal. If the removal
    /// branch is wrong the touch sticks at a price that no longer exists — the one shape here
    /// that cannot be checked by eye against a single frame.
    #[test]
    fn gemini_l2_tracks_the_touch_through_a_removal() {
        let by: HashMap<String, &'static str> = [("ETHUSD".to_owned(), "ETH")].into();
        let mut st = VenueState::default();
        let mut out = vec![];
        let feed = |raw: &str, st: &mut VenueState, out: &mut Vec<Quote>| {
            out.clear();
            decode(Gemini, &serde_json::from_str(raw).unwrap(), 7, &by, st, out).unwrap();
        };
        feed(r#"{"type":"l2_updates","symbol":"ETHUSD","changes":[["buy","2616.35","1.5"],["buy","2616.30","2.0"],["sell","2616.40","1.0"],["sell","2616.50","2.0"]]}"#, &mut st, &mut out);
        assert_eq!((out[0].bid, out[0].ask), (2616.35, 2616.40));
        // Best bid pulled: the touch must step DOWN to the next level, not stay.
        feed(r#"{"type":"l2_updates","symbol":"ETHUSD","changes":[["buy","2616.35","0.0"]]}"#, &mut st, &mut out);
        assert_eq!((out[0].bid, out[0].ask), (2616.30, 2616.40));
        // A better ask arrives.
        feed(r#"{"type":"l2_updates","symbol":"ETHUSD","changes":[["sell","2616.36","0.4"]]}"#, &mut st, &mut out);
        assert_eq!((out[0].bid, out[0].ask), (2616.30, 2616.36));
        // Another symbol must not leak into this book.
        feed(r#"{"type":"l2_updates","symbol":"BTCUSD","changes":[["buy","60000","1.0"]]}"#, &mut st, &mut out);
        assert!(out.is_empty());
    }

    #[test]
    fn bitstamp_reconnect_request_drops_the_connection() {
        let by: HashMap<String, &'static str> = [("ethusd".to_owned(), "ETH")].into();
        let mut st = VenueState::default();
        let mut out = vec![];
        let v: Value = serde_json::from_str(r#"{"event":"bts:request_reconnect","channel":"","data":{}}"#).unwrap();
        assert!(decode(Bitstamp, &v, 7, &by, &mut st, &mut out).is_err());
    }

    #[test]
    fn crypto_com_heartbeat_is_answered() {
        let by: HashMap<String, &'static str> = [("ETH_USD".to_owned(), "ETH")].into();
        let mut st = VenueState::default();
        let mut out = vec![];
        let v: Value = serde_json::from_str(r#"{"id":1587523073344,"method":"public/heartbeat","code":0}"#).unwrap();
        let Action::Reply(reply) = decode(CryptoCom, &v, 7, &by, &mut st, &mut out).unwrap() else {
            panic!("heartbeat must be answered or crypto.com drops the socket")
        };
        assert_eq!(
            serde_json::from_str::<Value>(&reply).unwrap(),
            json!({"id": 1587523073344i64, "method": "public/respond_heartbeat"})
        );
    }

    #[test]
    fn symbols_cover_the_ladder_and_name_kraken_its_own_codes() {
        assert_eq!(Kraken.symbol("BTC").unwrap(), "BTC/USD");
        assert_eq!(Kraken.symbol("DOGE").unwrap(), "DOGE/USD");
        assert_eq!(BinanceUs.symbol("NEAR").unwrap(), "nearusd");
        assert!(Gemini.symbol("NEAR").is_none());
        assert!(CryptoCom.symbol("ZEC").is_none());
        // Everything else must be listed, or the probe quietly measures 8 assets of 9.
        for a in ASSETS {
            for v in [CbEx, CbAdv, Kraken, BinanceUs, Okx, Gate, Bitstamp, Hyperliquid] {
                assert!(v.symbol(a).is_some(), "{} missing {a}", v.name());
            }
        }
        assert_eq!(asset_of_series("KXETH15M"), Some("ETH"));
        assert_eq!(asset_of_series("KXGOLD"), None);
    }

    #[test]
    fn binance_stream_url_lists_every_symbol() {
        let u = BinanceUs.url(&["ethusd".into(), "zecusd".into()]);
        assert_eq!(u, "wss://stream.binance.us:9443/stream?streams=ethusd@bookTicker/zecusd@bookTicker");
    }

    /// The aggregate's contract: a return per venue, median across venues, and a venue that has
    /// no observation old enough to span the window contributes nothing.
    #[test]
    fn consolidated_is_a_median_of_per_venue_returns() {
        let mut c = Consolidated::default();
        let q = |venue, t, px| Event::Q(Quote { venue, asset: "ETH", recv_us: t, exch_us: 0, bid: px, ask: px });
        // Five venues at five different bases — three USD, one USDT, one perp — each up 10 bps
        // over the second. A level-averaging aggregate would report the basis; this one reports
        // the move.
        for (v, base) in
            [(CbEx, 2000.0), (Kraken, 2004.0), (Bitstamp, 1998.0), (Okx, 2010.0), (Hyperliquid, 1990.0)]
        {
            c.apply(&q(v, 1_000_000, base));
            c.apply(&q(v, 2_000_000, base * 1.001));
        }
        // A sixth venue up 500 bps but only connected inside the window: no base, no vote.
        c.apply(&q(Gate, 1_900_000, 100.0));
        c.apply(&q(Gate, 2_000_000, 105.0));
        let r = c.ret_bps("ETH", 2_000_000, 1_000_000, 5_000_000, 3).unwrap();
        assert!((r - 10.0).abs() < 1e-6, "{r}");
        assert_eq!(c.venue_rets_bps("ETH", 2_000_000, 1_000_000, 5_000_000).len(), 5);
        // Six fresh venues, five of them voting.
        assert_eq!(c.fresh_venues("ETH", 2_000_000, 5_000_000), 6);
        // Stale: nothing fresh, so no signal rather than a stale one.
        assert!(c.ret_bps("ETH", 20_000_000, 1_000_000, 5_000_000, 3).is_none());
        // The minimum-venue floor is enforced.
        assert!(c.ret_bps("ETH", 2_000_000, 1_000_000, 5_000_000, 6).is_none());
        // Levels only average the USD venues, never the USDT or perp basis.
        let mid = c.usd_mid("ETH", 2_000_000, 5_000_000).unwrap();
        assert!((mid - 2002.0).abs() < 1e-6, "{mid}");
    }

    /// The defect this guard exists for: Kraken answered a bad symbol with `success: false` on a
    /// frame carrying no prices, so the first build read it as a quiet market and captured zero
    /// BTC and DOGE rows for a whole run.
    #[test]
    fn a_rejected_subscription_is_reported_not_swallowed() {
        let by: HashMap<String, &'static str> = [("BTC/USD".to_owned(), "BTC")].into();
        let mut st = VenueState::default();
        let mut out = vec![];
        let raw = r#"{"error":"Currency pair not supported XBT/USD","method":"subscribe","success":false,"symbol":"XBT/USD"}"#;
        let v: Value = serde_json::from_str(raw).unwrap();
        let Action::Warn(why) = decode(Kraken, &v, 7, &by, &mut st, &mut out).unwrap() else {
            panic!("a subscribe rejection must surface")
        };
        assert!(why.contains("not supported"), "{why}");
        assert!(out.is_empty());
        // A successful subscribe ack is not a rejection.
        let ok: Value = serde_json::from_str(
            r#"{"method":"subscribe","result":{"channel":"ticker","symbol":"BTC/USD"},"success":true}"#,
        )
        .unwrap();
        assert_eq!(decode(Kraken, &ok, 7, &by, &mut st, &mut out).unwrap(), Action::Nothing);
        // Every venue has a rejection shape, and none of them fires on an ordinary data frame.
        for (v, data) in [
            (CbEx, r#"{"type":"ticker","product_id":"BTC-USD"}"#),
            (CbAdv, r#"{"channel":"ticker","events":[]}"#),
            (BinanceUs, r#"{"stream":"btcusd@bookTicker","data":{"s":"BTCUSD"}}"#),
            (Okx, r#"{"arg":{"channel":"bbo-tbt"},"data":[]}"#),
            (Gate, r#"{"channel":"spot.book_ticker","event":"update","result":{}}"#),
            (Bitstamp, r#"{"event":"data","channel":"order_book_btcusd","data":{}}"#),
            (CryptoCom, r#"{"id":-1,"method":"subscribe","code":0,"result":{"channel":"book"}}"#),
            (Gemini, r#"{"type":"l2_updates","symbol":"BTCUSD","changes":[]}"#),
            (Hyperliquid, r#"{"channel":"bbo","data":{}}"#),
            (Kraken, r#"{"channel":"ticker","type":"update","data":[]}"#),
        ] {
            assert!(
                rejection(v, &serde_json::from_str(data).unwrap()).is_none(),
                "{} false-flags its own data frame",
                v.name()
            );
        }
        for (v, bad) in [
            (CbEx, r#"{"type":"error","message":"nope"}"#),
            (Okx, r#"{"event":"error","msg":"nope"}"#),
            (Gate, r#"{"event":"subscribe","result":{"status":"fail"}}"#),
            (Bitstamp, r#"{"event":"bts:error","data":{}}"#),
            (CryptoCom, r#"{"method":"subscribe","code":40004}"#),
            (Gemini, r#"{"result":"error","reason":"nope"}"#),
            (Hyperliquid, r#"{"channel":"error","data":"nope"}"#),
            (BinanceUs, r#"{"error":{"code":-1121,"msg":"Invalid symbol."}}"#),
        ] {
            assert!(
                rejection(v, &serde_json::from_str(bad).unwrap()).is_some(),
                "{} swallows its own rejection",
                v.name()
            );
        }
    }

    #[test]
    fn an_unchanged_touch_is_dropped_but_repeats_once_a_second() {
        let mut st = VenueState::default();
        let q = |t, bid, ask| Quote { venue: Gemini, asset: "ETH", recv_us: t, exch_us: 0, bid, ask };
        assert!(st.is_new(&q(0, 10.0, 11.0)));
        assert!(!st.is_new(&q(1, 10.0, 11.0)), "an unchanged touch must not be an observation");
        assert!(st.is_new(&q(2, 10.0, 11.5)), "a moved ask is a new observation");
        assert!(!st.is_new(&q(3, 10.0, 11.5)));
        // Still unchanged, but a second has passed: one repeat, so silence means a dead socket.
        assert!(st.is_new(&q(2 + REPEAT_US, 10.0, 11.5)));
        // Another asset on the same connection keeps its own last quote.
        let mut other = q(4, 10.0, 11.5);
        other.asset = "ZEC";
        assert!(st.is_new(&other));
    }

    #[test]
    fn parsers_reject_an_unknown_name() {
        assert_eq!(parse_venues("kraken,okx").unwrap(), vec![Kraken, Okx]);
        assert!(parse_venues("kraken,nope").is_err());
        assert_eq!(parse_assets("eth,ZEC").unwrap(), vec!["ETH", "ZEC"]);
        assert!(parse_assets("ETH,LUNA").is_err());
    }
}
