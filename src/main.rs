//! Read-only competitiveness probe for Kalshi 15M crypto. Places NO orders.
//!
//!   kalshi-mm15 probe --out data --minutes 60 [--dump 20]
//!   kalshi-mm15 rtt --n 300
//!
//! All reaction-window statistics are gaps between two messages on OUR receive clock, so
//! they need no clock agreement with the venue. Only `feed_age_ms` compares clocks.

mod auth;
mod book;
mod latency;
mod live;
mod shadow;
mod stats;

use std::{
    collections::{HashMap, HashSet},
    env,
    fs::{self, File},
    io::{BufWriter, Write},
    path::PathBuf,
    sync::mpsc as std_mpsc,
    time::{Duration, Instant},
};

use anyhow::{Context, Result, bail};
use flate2::{Compression, write::GzEncoder};
use futures_util::{SinkExt, StreamExt};
use serde_json::{Value, json};
use tokio::{sync::mpsc, time::interval};
use tokio_tungstenite::{
    connect_async,
    tungstenite::{
        Message,
        client::IntoClientRequest,
        http::{HeaderName, HeaderValue},
    },
};

use crate::{
    auth::{Auth, unix_ms},
    book::{Book, PRICE_SCALE, SIZE_SCALE, Touch, parse_value},
    stats::Samples,
};

/// Direct us-east-2 origin. `api.elections.kalshi.com` is a CloudFront edge in front of it.
const REST_DEFAULT: &str = "https://external-api.kalshi.com/trade-api/v2";
const WS_DEFAULT: &str = "wss://external-api-ws.kalshi.com/trade-api/ws/v2";

fn rest_base() -> String {
    env::var("KALSHI_REST").unwrap_or_else(|_| REST_DEFAULT.into())
}

fn ws_url() -> String {
    env::var("KALSHI_WS").unwrap_or_else(|_| WS_DEFAULT.into())
}
const SERIES: [&str; 9] = [
    "KXBTC15M", "KXETH15M", "KXSOL15M", "KXXRP15M", "KXDOGE15M", "KXHYPE15M", "KXBNB15M",
    "KXZEC15M", "KXNEAR15M",
];
/// Same-side prints this close together are one taker sweep.
const BURST_US: i64 = 50_000;
/// Thresholds (µs) at which to report "share of gaps longer than": a reaction that takes
/// this long from our receipt of the first print still lands before the next one.
const THRESH_US: [i64; 8] = [1_000, 2_000, 3_000, 5_000, 7_500, 10_000, 15_000, 25_000];
const CAP: usize = 400_000;

#[tokio::main(flavor = "current_thread")]
async fn main() -> Result<()> {
    let args: Vec<String> = env::args().collect();
    let flag = |name: &str| {
        args.iter()
            .position(|a| a == name)
            .and_then(|i| args.get(i + 1))
            .cloned()
    };
    match args.get(1).map(String::as_str) {
        Some("probe") => {
            let out = PathBuf::from(flag("--out").unwrap_or_else(|| "data".into()));
            let minutes: u64 = flag("--minutes").map_or(Ok(60), |m| m.parse())?;
            let dump: usize = flag("--dump").map_or(Ok(0), |m| m.parse())?;
            probe(out, minutes, dump).await
        }
        Some("get") => {
            // Signed read-only GET of one path, e.g. `get /account/limits`.
            let path = args.get(2).context("usage: get /path")?;
            let auth = load_auth()?;
            let h = auth.headers("GET", &format!("/trade-api/v2{path}"));
            let body = client()?
                .get(format!("{}{path}", rest_base()))
                .header("KALSHI-ACCESS-KEY", h.key_id)
                .header("KALSHI-ACCESS-TIMESTAMP", h.timestamp)
                .header("KALSHI-ACCESS-SIGNATURE", h.signature)
                .send()
                .await?
                .text()
                .await?;
            println!("{body}");
            Ok(())
        }
        Some("shadow") => {
            let out = PathBuf::from(flag("--out").unwrap_or_else(|| "data/shadow".into()));
            let minutes: u64 = flag("--minutes").map_or(Ok(60), |m| m.parse())?;
            let p = shadow::Params {
                series: flag("--series").map_or_else(
                    || SERIES.iter().map(|s| s.to_string()).collect(),
                    |v| v.split(',').map(str::to_owned).collect(),
                ),
                only_base: args.iter().any(|a| a == "--only-base"),
                ..shadow::Params::default()
            };
            shadow::run(load_auth()?, out, minutes, p).await
        }
        Some("live") => {
            // ARMED. Requires the explicit flag so it can never start by accident.
            if !args.iter().any(|a| a == "--armed") {
                bail!("live places REAL orders; pass --armed to confirm");
            }
            let num = |name: &str, default: f64| flag(name).map_or(Ok(default), |v| v.parse::<f64>());
            let params = live::LiveParams {
                series: flag("--series")
                    .unwrap_or_else(|| "KXBTC15M,KXETH15M,KXXRP15M".into())
                    .split(',')
                    .map(str::to_owned)
                    .collect(),
                minutes: num("--minutes", 120.0)? as u64,
                max_pos_fp: (num("--max-pos", 1.0)? * book::SIZE_SCALE as f64) as i64,
                session_max_loss_c: num("--max-loss-c", 200.0)?,
                cumulative_max_loss_c: num("--cum-max-loss-c", 300.0)?,
                // Clip 1 and |pos| <= 1 already bound what a sweep can take; the group catches a
                // runaway loop. 4 tripped on ordinary flow (5 fills / 6 s across 3 markets).
                group_contracts_per_15s: num("--group-limit", 12.0)? as i64,
                stop_before_close_s: 120,
                mid_lo_c: 15.0,
                mid_hi_c: 85.0,
                mom_pull_c: 0.25,
                thin_pull: 0.9213,
                exchange_index: num("--exchange-index", 2.0)? as i64,
                out: PathBuf::from(flag("--out").unwrap_or_else(|| "data/live".into())),
                penny_room: num("--penny-room", 0.0)? as i64,
            };
            live::run(load_auth()?, params).await
        }
        Some("latency") => {
            let n: usize = flag("--n").map_or(Ok(50), |m| m.parse())?;
            latency::run(load_auth()?, n).await
        }
        Some("upgrade") => {
            // Self-serve Basic → Advanced API usage level (needs one API-created order).
            let auth = load_auth()?;
            let path = "/account/api_usage_level/upgrade";
            let h = auth.headers("POST", &format!("/trade-api/v2{path}"));
            let resp = client()?
                .post(format!("{}{path}", rest_base()))
                .header("KALSHI-ACCESS-KEY", h.key_id)
                .header("KALSHI-ACCESS-TIMESTAMP", h.timestamp)
                .header("KALSHI-ACCESS-SIGNATURE", h.signature)
                .json(&json!({})) // the endpoint 400s `invalid_content_type` without a JSON body
                .send()
                .await?;
            println!("{} {}", resp.status(), resp.text().await?);
            Ok(())
        }
        Some("rtt") => rtt(flag("--n").map_or(Ok(300), |m| m.parse())?).await,
        _ => bail!("usage: kalshi-mm15 probe --out DIR --minutes N [--dump N] | rtt --n N"),
    }
}

// ---------------------------------------------------------------------------------------
// rtt: our own application round trip on a keep-alive connection. Read-only endpoints.
// ---------------------------------------------------------------------------------------

async fn rtt(n: usize) -> Result<()> {
    let auth = load_auth()?;
    let http = client()?;
    let mut report = serde_json::Map::new();
    for (name, path, signed) in [
        ("exchange_status", "/exchange/status", false),
        ("portfolio_balance_signed", "/portfolio/balance", true),
        ("portfolio_orders_signed", "/portfolio/orders?limit=1", true),
    ] {
        let mut s = Samples::new(n);
        for i in 0..n + 5 {
            let mut req = http.get(format!("{}{path}", rest_base()));
            if signed {
                let h = auth.headers("GET", &format!("/trade-api/v2{path}"));
                req = req
                    .header("KALSHI-ACCESS-KEY", h.key_id)
                    .header("KALSHI-ACCESS-TIMESTAMP", h.timestamp)
                    .header("KALSHI-ACCESS-SIGNATURE", h.signature);
            }
            let t = Instant::now();
            let resp = req.send().await?;
            let status = resp.status();
            resp.bytes().await?;
            let us = t.elapsed().as_micros() as i64;
            if !status.is_success() {
                bail!("{path} returned {status}");
            }
            if i >= 5 {
                s.push(us, 1.0); // first 5 pay the handshake
            }
            tokio::time::sleep(Duration::from_millis(100)).await; // stay under the read rate limit (429 at ~38/s)
        }
        report.insert(name.into(), s.summary(&THRESH_US));
    }
    let mut s = Samples::new(n);
    for _ in 0..n {
        let t = Instant::now();
        std::hint::black_box(auth.headers("DELETE", "/trade-api/v2/portfolio/orders/00000000"));
        s.push(t.elapsed().as_micros() as i64, 1.0);
    }
    report.insert("rsa_pss_sign_local".into(), s.summary(&[]));
    let mut s = Samples::new(n);
    for _ in 0..(n / 10).max(10) {
        let t = Instant::now();
        let (mut ws, _) = connect_async(signed_ws_request(&auth)?).await?;
        s.push(t.elapsed().as_micros() as i64, 1.0);
        let _ = ws.close(None).await;
    }
    report.insert("ws_connect_incl_tls".into(), s.summary(&[]));
    println!("{}", serde_json::to_string_pretty(&Value::Object(report))?);
    Ok(())
}

fn load_auth() -> Result<Auth> {
    let args: Vec<String> = env::args().collect();
    match args.iter().position(|a| a == "--env-file").and_then(|i| args.get(i + 1)) {
        Some(path) => Auth::from_env_file(path),
        None => Auth::from_env(),
    }
}

fn client() -> Result<reqwest::Client> {
    reqwest::Client::builder()
        .tcp_nodelay(true)
        .pool_idle_timeout(Duration::from_secs(90))
        .timeout(Duration::from_secs(5))
        .build()
        .context("http client")
}

fn signed_ws_request(
    auth: &Auth,
) -> Result<tokio_tungstenite::tungstenite::handshake::client::Request> {
    let h = auth.headers("GET", "/trade-api/ws/v2");
    let mut request = ws_url().into_client_request()?;
    for (name, value) in [
        ("KALSHI-ACCESS-KEY", h.key_id),
        ("KALSHI-ACCESS-TIMESTAMP", h.timestamp),
        ("KALSHI-ACCESS-SIGNATURE", h.signature),
    ] {
        request
            .headers_mut()
            .insert(HeaderName::from_bytes(name.as_bytes())?, HeaderValue::from_str(&value)?);
    }
    Ok(request)
}

// ---------------------------------------------------------------------------------------
// probe
// ---------------------------------------------------------------------------------------

struct Discovered {
    ticker: String,
    close_unix_ms: i64,
}

async fn discover(tx: mpsc::Sender<Discovered>, series: Vec<String>) -> Result<()> {
    let http = client()?;
    let mut seen = HashSet::new();
    let mut tick = interval(Duration::from_secs(15));
    loop {
        tick.tick().await;
        for series in &series {
            let url = format!("{}/markets?series_ticker={series}&status=open&limit=20", rest_base());
            let body: Value = match http.get(&url).send().await {
                Ok(r) => match r.json().await {
                    Ok(v) => v,
                    Err(e) => {
                        eprintln!("discover {series}: {e}");
                        continue;
                    }
                },
                Err(e) => {
                    eprintln!("discover {series}: {e}");
                    continue;
                }
            };
            for m in body["markets"].as_array().into_iter().flatten() {
                let Some(ticker) = m["ticker"].as_str() else { continue };
                let close = m["close_time"].as_str().and_then(rfc3339_ms).unwrap_or(0);
                if seen.insert(ticker.to_owned()) {
                    tx.send(Discovered { ticker: ticker.to_owned(), close_unix_ms: close })
                        .await?;
                }
            }
        }
    }
}

fn rfc3339_ms(s: &str) -> Option<i64> {
    rfc3339_us(s).map(|us| us.div_euclid(1_000))
}

/// "2026-09-23T04:01:24.753459Z" (fraction optional) → unix µs, exact to the µs.
fn rfc3339_us(s: &str) -> Option<i64> {
    let (date, time) = s.trim_end_matches('Z').split_once('T')?;
    let mut d = date.split('-').map(|x| x.parse::<i64>());
    let (y, m, dd) = (d.next()?.ok()?, d.next()?.ok()?, d.next()?.ok()?);
    let mut t = time.split(':');
    let (hh, mi) = (t.next()?.parse::<i64>().ok()?, t.next()?.parse::<i64>().ok()?);
    let sec_str = t.next()?;
    let (secs, frac) = sec_str.split_once('.').unwrap_or((sec_str, ""));
    let ss: i64 = secs.parse().ok()?;
    let frac_us: i64 = if frac.is_empty() { 0 } else { format!("{:0<6}", &frac[..frac.len().min(6)]).parse().ok()? };
    // days from civil (Howard Hinnant)
    let y2 = if m <= 2 { y - 1 } else { y };
    let era = y2.div_euclid(400);
    let yoe = y2 - era * 400;
    let mp = (m + 9) % 12;
    let doy = (153 * mp + 2) / 5 + dd - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    let days = era * 146_097 + doe - 719_468;
    Some((days * 86_400 + hh * 3_600 + mi * 60 + ss) * 1_000_000 + frac_us)
}

#[derive(Default)]
struct Mkt {
    series: String,
    book: Option<Book>,
    touch: Option<Touch>,
    touch_since_us: Option<i64>,
    close_unix_ms: i64,
    last_trade: Option<LastTrade>,
    /// Armed by a print; cleared by the first add at/inside the touch afterwards.
    add_watch_us: Option<i64>,
    /// Armed by a touch change; cleared by the first add AT the new touch price.
    join_watch: Option<(i64, i64, &'static str)>,
}

#[derive(Clone, Copy)]
struct LastTrade {
    t_us: i64,
    taker_yes: bool,
    burst_head_us: i64,
    burst_head_price: i64,
}

struct Stats {
    by_name: HashMap<String, Samples>,
    counts: HashMap<String, u64>,
}

impl Stats {
    fn push(&mut self, series: &str, name: &str, v: i64, w: f64) {
        for key in [format!("ALL.{name}"), format!("{series}.{name}")] {
            self.by_name.entry(key).or_insert_with(|| Samples::new(CAP)).push(v, w);
        }
    }
    fn count(&mut self, series: &str, name: &str) {
        for key in [format!("ALL.{name}"), format!("{series}.{name}")] {
            *self.counts.entry(key).or_default() += 1;
        }
    }
    fn report(&self, started: Instant) -> Value {
        let mut names: Vec<_> = self.by_name.keys().collect();
        names.sort();
        let dists: serde_json::Map<String, Value> = names
            .into_iter()
            .map(|k| {
                let th: &[i64] = if k.ends_with("feed_age_us") { &[] } else { &THRESH_US };
                (k.clone(), self.by_name[k].summary(th))
            })
            .collect();
        let mut counts: Vec<_> = self.counts.iter().collect();
        counts.sort();
        json!({
            "unix_ms": unix_ms() as u64,
            "elapsed_s": started.elapsed().as_secs(),
            "counts": counts.into_iter().map(|(k, v)| (k.clone(), json!(v))).collect::<serde_json::Map<_,_>>(),
            "dists_us": dists,
        })
    }
}

fn unix_us() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_micros() as i64)
        .unwrap_or_default()
}

fn series_of(ticker: &str) -> String {
    ticker.split('-').next().unwrap_or(ticker).to_owned()
}

async fn probe(out: PathBuf, minutes: u64, mut dump: usize) -> Result<()> {
    fs::create_dir_all(&out)?;
    let auth = load_auth()?;
    let stamp = unix_ms();
    let tape_path = out.join(format!("tape_{stamp}.csv.gz"));
    let stats_path = out.join(format!("stats_{stamp}.jsonl"));
    let dump_path = out.join(format!("raw_{stamp}.jsonl"));
    eprintln!("tape {} | stats {}", tape_path.display(), stats_path.display());

    // Tape writer on its own OS thread: gzip must never sit in the receive path.
    let (tape_tx, tape_rx) = std_mpsc::sync_channel::<String>(200_000);
    let tape_thread = std::thread::spawn(move || -> Result<()> {
        let mut gz = GzEncoder::new(BufWriter::new(File::create(&tape_path)?), Compression::fast());
        writeln!(gz, "kind,recv_us,ticker,a,b,c,d,venue_ms")?;
        for line in tape_rx {
            gz.write_all(line.as_bytes())?;
        }
        gz.finish()?.flush()?;
        Ok(())
    });
    let mut stats_file = BufWriter::new(File::create(&stats_path)?);
    let mut dump_file = BufWriter::new(File::create(&dump_path)?);
    let mut dumped: HashMap<String, usize> = HashMap::new();

    let (disc_tx, mut disc_rx) = mpsc::channel(256);
    let disc = tokio::spawn(discover(disc_tx, SERIES.iter().map(|s| s.to_string()).collect()));

    let started = Instant::now();
    let deadline = started + Duration::from_secs(minutes * 60);

    let mut stats = Stats { by_name: HashMap::new(), counts: HashMap::new() };
    let mut markets: HashMap<String, Mkt> = HashMap::new();
    let mut report_tick = interval(Duration::from_secs(60));
    report_tick.tick().await;

    'outer: while Instant::now() < deadline {
        let (mut ws, _) = match connect_async(signed_ws_request(&auth)?).await {
            Ok(x) => x,
            Err(e) => {
                eprintln!("ws connect: {e}");
                tokio::time::sleep(Duration::from_secs(1)).await;
                continue;
            }
        };
        stats.count("ALL", "ws_connects");
        let mut next_id = 1u64;
        let mut seqs: HashMap<u64, u64> = HashMap::new();
        // Kalshi MERGES later subscribes into the first command's sids, so a closed market is
        // simply left to go quiet: unsubscribing its sid would unsubscribe every market.
        let open: Vec<String> = markets.keys().cloned().collect();
        if !open.is_empty() {
            for m in markets.values_mut() {
                m.book = None;
                m.touch = None;
            }
            ws.send(Message::Text(sub_cmd(next_id, &open).into())).await?;
            next_id += 1;
        }
        let mut close_tick = interval(Duration::from_secs(10));

        loop {
            let msg = tokio::select! {
                m = ws.next() => m,
                d = disc_rx.recv() => {
                    let d = d.context("discovery stopped")?;
                    markets.insert(d.ticker.clone(), Mkt {
                        series: series_of(&d.ticker),
                        close_unix_ms: d.close_unix_ms,
                        ..Default::default()
                    });
                    ws.send(Message::Text(sub_cmd(next_id, std::slice::from_ref(&d.ticker)).into())).await?;
                    next_id += 1;
                    continue;
                }
                _ = close_tick.tick() => {
                    let now = unix_ms() as i64;
                    markets.retain(|_, m| m.close_unix_ms == 0 || now <= m.close_unix_ms + 30_000);
                    if Instant::now() >= deadline { break 'outer; }
                    continue;
                }
                _ = report_tick.tick() => {
                    let r = stats.report(started);
                    writeln!(stats_file, "{r}")?;
                    stats_file.flush()?;
                    print_brief(&r);
                    continue;
                }
                _ = tokio::signal::ctrl_c() => break 'outer,
            };
            let text = match msg {
                Some(Ok(Message::Text(t))) => t,
                Some(Ok(Message::Ping(p))) => { ws.send(Message::Pong(p)).await?; continue; }
                Some(Ok(_)) => continue,
                Some(Err(e)) => { eprintln!("ws error: {e}"); break; }
                None => { eprintln!("ws closed"); break; }
            };
            let recv_unix_us = unix_us();
            let v: Value = match serde_json::from_str(&text) {
                Ok(v) => v,
                Err(e) => { eprintln!("bad json: {e}"); continue; }
            };
            let kind = v["type"].as_str().unwrap_or("?").to_owned();
            if dump > 0 {
                let n = dumped.entry(kind.clone()).or_default();
                if *n < dump {
                    *n += 1;
                    writeln!(dump_file, "{recv_unix_us}\t{text}")?;
                    dump_file.flush()?;
                }
                if dumped.len() >= 4 && dumped.values().all(|n| *n >= dump) { dump = 0; }
            }
            if kind == "error" {
                eprintln!("venue error: {text}");
                stats.count("ALL", "venue_errors");
                continue;
            }
            if let (Some(sid), Some(seq)) = (v["sid"].as_u64(), v["seq"].as_u64()) {
                if let Some(prev) = seqs.insert(sid, seq) {
                    if seq != prev + 1 {
                        eprintln!("seq gap sid {sid}: {prev} -> {seq}; reconnecting");
                        stats.count("ALL", "seq_gaps");
                        break;
                    }
                }
            }
            let m = &v["msg"];
            let Some(ticker) = m["market_ticker"].as_str() else { continue };
            let Some(mk) = markets.get_mut(ticker) else { continue };
            let series = mk.series.clone();
            stats.count(&series, &format!("msg.{kind}"));

            match kind.as_str() {
                "orderbook_snapshot" => {
                    match Book::from_snapshot(m) {
                        Ok(b) => { mk.touch = Some(b.touch()); mk.book = Some(b); mk.touch_since_us = None; }
                        Err(e) => eprintln!("snapshot {ticker}: {e:#}"),
                    }
                }
                "orderbook_delta" => {
                    // Deltas carry the venue's µs clock; every gap below is on it.
                    let Some(vt) = m["ts"].as_str().and_then(rfc3339_us) else {
                        stats.count(&series, "delta_without_ts");
                        continue;
                    };
                    // Every 16th delta is plenty for a latency distribution on a 1 GB box.
                    if seqs.values().sum::<u64>() % 16 == 0 {
                        stats.push(&series, "delta.feed_age_us", recv_unix_us - vt, 1.0);
                    }
                    let Some(book) = mk.book.as_mut() else { continue };
                    if let Err(e) = book.apply_delta(m) {
                        eprintln!("delta {ticker}: {e:#}; reconnecting");
                        stats.count(&series, "bad_delta");
                        break;
                    }
                    let side = m["side"].as_str().unwrap_or("");
                    let price = parse_value(m.get("price_dollars"), PRICE_SCALE).unwrap_or(-1);
                    let delta = parse_value(m.get("delta_fp"), SIZE_SCALE).unwrap_or(0);
                    let new = book.touch();
                    let old = mk.touch.clone();
                    let at_or_inside = match side {
                        "yes" => new.yes_bid_fp.is_some_and(|b| price >= b),
                        _ => new.yes_ask_fp.is_some_and(|a| price <= a),
                    };
                    if delta > 0 && at_or_inside {
                        stats.count(&series, "touch_adds");
                        // Trade ts is ms-truncated, so this gap is biased up by [0, 1) ms.
                        if let Some(t0) = mk.add_watch_us.take() {
                            stats.push(&series, "add_after_print", vt - t0, 1.0);
                        }
                        if let Some((t0, p, s)) = mk.join_watch {
                            if s == side && p == price {
                                stats.push(&series, "join_after_new_touch", vt - t0, 1.0);
                                mk.join_watch = None;
                            }
                        }
                    } else if delta < 0 && at_or_inside {
                        stats.count(&series, "touch_removes");
                    }
                    if old.as_ref() != Some(&new) {
                        if let Some(o) = &old {
                            let bid_moved = o.yes_bid_fp != new.yes_bid_fp;
                            let ask_moved = o.yes_ask_fp != new.yes_ask_fp;
                            if bid_moved || ask_moved {
                                if let Some(t0) = mk.touch_since_us {
                                    stats.push(&series, "touch_price_lifetime", vt - t0, 1.0);
                                }
                                mk.touch_since_us = Some(vt);
                                // A new, better price was just posted: how fast does a second
                                // participant join it? That is the queue race we would enter.
                                let improved_bid = bid_moved && side == "yes" && delta > 0;
                                let improved_ask = ask_moved && side == "no" && delta > 0;
                                if improved_bid {
                                    mk.join_watch = new.yes_bid_fp.map(|p| (vt, p, "yes"));
                                } else if improved_ask {
                                    mk.join_watch = new.yes_ask_fp.map(|p| (vt, p, "no"));
                                }
                            }
                        }
                        let _ = tape_tx.try_send(format!(
                            "B,{recv_unix_us},{ticker},{},{},{},{},{vt}\n",
                            new.yes_bid_fp.unwrap_or(-1), new.yes_bid_size_fp.unwrap_or(0),
                            new.yes_ask_fp.unwrap_or(-1), new.yes_ask_size_fp.unwrap_or(0),
                        ));
                        mk.touch = Some(new);
                    }
                }
                "trade" => {
                    let Some(vt_ms) = m["ts_ms"].as_i64() else {
                        stats.count(&series, "trade_without_ts_ms");
                        continue;
                    };
                    let vt = vt_ms * 1_000;
                    stats.push(&series, "trade.feed_age_us", recv_unix_us - vt, 1.0);
                    let taker_yes = m["taker_side"].as_str() == Some("yes");
                    let price = parse_value(m.get("yes_price_dollars"), PRICE_SCALE).unwrap_or(-1);
                    let count = parse_value(m.get("count_fp"), SIZE_SCALE).unwrap_or(0) as f64
                        / SIZE_SCALE as f64;
                    let _ = tape_tx.try_send(format!(
                        "T,{recv_unix_us},{ticker},{},{price},{count},,{vt}\n",
                        if taker_yes { "yes" } else { "no" },
                    ));
                    stats.count(&series, "prints");
                    if let Some(t) = &mk.touch {
                        let (tp, tsz) = if taker_yes { (t.yes_ask_fp, t.yes_ask_size_fp) } else { (t.yes_bid_fp, t.yes_bid_size_fp) };
                        if tp == Some(price) {
                            stats.push(&series, "touch_queue_ct_at_print", tsz.unwrap_or(0) / SIZE_SCALE, 1.0);
                        }
                    }
                    let mut head_us = vt;
                    let mut head_price = price;
                    if let Some(lt) = mk.last_trade {
                        let gap = vt - lt.t_us;
                        if lt.taker_yes == taker_yes && gap == 0 {
                            // Same venue millisecond, same side: one taker order filling
                            // against several resting orders. Not a separate arrival.
                            stats.count(&series, "prints_same_ms_fragment");
                            head_us = lt.burst_head_us;
                            head_price = lt.burst_head_price;
                        } else {
                            stats.push(&series, "print_gap_any", gap, 1.0);
                            if lt.taker_yes == taker_yes {
                                stats.push(&series, "print_gap_same_side", gap, count);
                                if gap <= BURST_US {
                                    head_us = lt.burst_head_us;
                                    head_price = lt.burst_head_price;
                                    // A later arrival continuing the sweep. Only the part at
                                    // the head's own price can hit a resting order there.
                                    let name = if price == head_price { "sweep_cont_same_price" } else { "sweep_cont_deeper" };
                                    stats.push(&series, &format!("{name}.gap_from_prev"), gap, count);
                                    stats.push(&series, &format!("{name}.gap_from_head"), vt - head_us, count);
                                }
                            }
                        }
                    }
                    if head_us == vt { stats.count(&series, "sweep_heads"); }
                    mk.last_trade = Some(LastTrade { t_us: vt, taker_yes, burst_head_us: head_us, burst_head_price: head_price });
                    mk.add_watch_us = Some(vt);
                }
                _ => {}
            }
        }
        tokio::time::sleep(Duration::from_millis(250)).await;
    }

    let r = stats.report(started);
    writeln!(stats_file, "{r}")?;
    stats_file.flush()?;
    print_brief(&r);
    disc.abort();
    drop(tape_tx);
    tape_thread.join().map_err(|_| anyhow::anyhow!("tape thread panicked"))??;
    Ok(())
}

fn sub_cmd(id: u64, tickers: &[String]) -> String {
    json!({
        "id": id,
        "cmd": "subscribe",
        "params": {"channels": ["orderbook_delta", "trade"], "market_tickers": tickers, "use_yes_price": true}
    })
    .to_string()
}

fn print_brief(r: &Value) {
    let d = &r["dists_us"];
    let p50 = |k: &str| d[k]["quantiles"]["p50"].clone();
    eprintln!(
        "[{}s] prints={} gap_same_side_p50={}us sweep_same_price_p50={}us add_after_print_p50={}us join_p50={}us feed_age_p50={}us",
        r["elapsed_s"],
        r["counts"]["ALL.prints"],
        p50("ALL.print_gap_same_side"),
        p50("ALL.sweep_cont_same_price.gap_from_prev"),
        p50("ALL.add_after_print"),
        p50("ALL.join_after_new_touch"),
        p50("ALL.delta.feed_age_us"),
    );
}

#[cfg(test)]
mod tests {
    use super::rfc3339_ms;

    #[test]
    fn parses_close_time() {
        assert_eq!(rfc3339_ms("1970-01-01T00:00:01Z"), Some(1_000));
        assert_eq!(rfc3339_ms("2026-09-23T04:00:00Z"), Some(1_790_136_000_000));
        assert_eq!(super::rfc3339_us("2026-09-23T04:01:24.753459Z"), Some(1_790_136_084_753_459));
        assert_eq!(super::rfc3339_us("2026-09-23T04:01:24.75Z"), Some(1_790_136_084_750_000));
    }
}
