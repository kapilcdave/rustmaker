//! `spotprobe`: which public BTC feed is fastest FROM THIS BOX? Market data only, no auth, no
//! orders. One websocket per venue, every trade print written as
//! `feed,recv_us,exch_ms,price` to `<out>/spotprobe_<start_ms>.csv.gz`. Score with
//! `spot_lead.py`: exchange-to-receipt delay per feed, and which feed shows a BTC move first
//! (receipt clock, the only clock the engine can act on).
use std::{path::PathBuf, time::Duration};

use anyhow::Result;
use futures_util::{SinkExt, StreamExt};
use serde_json::{Value, json};
use tokio::{sync::mpsc, time::interval};
use tokio_tungstenite::{connect_async, tungstenite::Message};

use crate::{rfc3339_us, unix_us};

type Parse = fn(&Value) -> Vec<(i64, f64)>;

struct Feed {
    name: &'static str,
    url: &'static str,
    sub: Option<Value>,
    parse: Parse,
    /// Application-level keepalive some venues require: (every, text).
    ping: Option<(u64, &'static str)>,
}

fn f64_of(v: &Value) -> Option<f64> {
    v.as_f64().or_else(|| v.as_str().and_then(|s| s.parse().ok()))
}
fn i64_of(v: &Value) -> Option<i64> {
    v.as_i64().or_else(|| v.as_str().and_then(|s| s.parse().ok()))
}
fn iso_ms(v: &Value) -> Option<i64> {
    v.as_str().and_then(rfc3339_us).map(|u| u / 1_000)
}

fn coinbase_ticker(v: &Value) -> Vec<(i64, f64)> {
    if v["type"] != "ticker" { return vec![]; }
    match (iso_ms(&v["time"]), f64_of(&v["price"])) { (Some(t), Some(p)) => vec![(t, p)], _ => vec![] }
}
fn coinbase_adv(v: &Value) -> Vec<(i64, f64)> {
    if v["channel"] != "market_trades" { return vec![]; }
    let mut out = vec![];
    for e in v["events"].as_array().into_iter().flatten().filter(|e| e["type"] == "update") {
        for t in e["trades"].as_array().into_iter().flatten() {
            if let (Some(ts), Some(p)) = (iso_ms(&t["time"]), f64_of(&t["price"])) { out.push((ts, p)); }
        }
    }
    out
}
fn kraken(v: &Value) -> Vec<(i64, f64)> {
    if v["channel"] != "trade" || v["type"] != "update" { return vec![]; }
    v["data"].as_array().into_iter().flatten()
        .filter_map(|t| Some((iso_ms(&t["timestamp"])?, f64_of(&t["price"])?))).collect()
}
fn bitstamp(v: &Value) -> Vec<(i64, f64)> {
    if v["event"] != "trade" { return vec![]; }
    let d = &v["data"];
    match (i64_of(&d["microtimestamp"]), f64_of(&d["price"])) { (Some(t), Some(p)) => vec![(t / 1_000, p)], _ => vec![] }
}
fn gemini(v: &Value) -> Vec<(i64, f64)> {
    if v["type"] != "trade" { return vec![]; }
    match (i64_of(&v["timestamp"]), f64_of(&v["price"])) { (Some(t), Some(p)) => vec![(t, p)], _ => vec![] }
}
fn binance(v: &Value) -> Vec<(i64, f64)> {
    if v["e"] != "trade" && v["e"] != "aggTrade" { return vec![]; }
    match (i64_of(&v["T"]), f64_of(&v["p"])) { (Some(t), Some(p)) => vec![(t, p)], _ => vec![] }
}
fn okx(v: &Value) -> Vec<(i64, f64)> {
    if v["arg"]["channel"] != "trades" { return vec![]; }
    v["data"].as_array().into_iter().flatten()
        .filter_map(|t| Some((i64_of(&t["ts"])?, f64_of(&t["px"])?))).collect()
}
fn bybit(v: &Value) -> Vec<(i64, f64)> {
    if !v["topic"].as_str().is_some_and(|t| t.starts_with("publicTrade")) { return vec![]; }
    v["data"].as_array().into_iter().flatten()
        .filter_map(|t| Some((i64_of(&t["T"])?, f64_of(&t["p"])?))).collect()
}

fn feeds() -> Vec<Feed> {
    vec![
        Feed { name: "coinbase_ticker", url: "wss://ws-feed.exchange.coinbase.com",
               sub: Some(json!({"type": "subscribe", "product_ids": ["BTC-USD"], "channels": ["ticker"]})), parse: coinbase_ticker, ping: None },
        Feed { name: "coinbase_adv", url: "wss://advanced-trade-ws.coinbase.com",
               sub: Some(json!({"type": "subscribe", "product_ids": ["BTC-USD"], "channel": "market_trades"})), parse: coinbase_adv, ping: None },
        Feed { name: "kraken", url: "wss://ws.kraken.com/v2",
               sub: Some(json!({"method": "subscribe", "params": {"channel": "trade", "symbol": ["BTC/USD"]}})), parse: kraken, ping: None },
        Feed { name: "bitstamp", url: "wss://ws.bitstamp.net",
               sub: Some(json!({"event": "bts:subscribe", "data": {"channel": "live_trades_btcusd"}})), parse: bitstamp, ping: None },
        Feed { name: "gemini", url: "wss://api.gemini.com/v2/marketdata",
               sub: Some(json!({"type": "subscribe", "subscriptions": [{"name": "l2", "symbols": ["BTCUSD"]}]})), parse: gemini, ping: None },
        Feed { name: "binance_us", url: "wss://stream.binance.us:9443/ws/btcusdt@trade", sub: None, parse: binance, ping: None },
        Feed { name: "binance_com", url: "wss://stream.binance.com:9443/ws/btcusdt@trade", sub: None, parse: binance, ping: None },
        Feed { name: "binance_fut", url: "wss://fstream.binance.com/ws/btcusdt@aggTrade", sub: None, parse: binance, ping: None },
        Feed { name: "okx", url: "wss://ws.okx.com:8443/ws/v5/public",
               sub: Some(json!({"op": "subscribe", "args": [{"channel": "trades", "instId": "BTC-USDT"}]})), parse: okx, ping: Some((20, "ping")) },
        Feed { name: "bybit", url: "wss://stream.bybit.com/v5/public/spot",
               sub: Some(json!({"op": "subscribe", "args": ["publicTrade.BTCUSDT"]})), parse: bybit, ping: Some((20, r#"{"op":"ping"}"#)) },
    ]
}

async fn run_feed(f: Feed, tx: mpsc::UnboundedSender<String>) {
    loop {
        let (mut ws, _) = match connect_async(f.url).await {
            Ok(x) => x,
            Err(e) => {
                let _ = tx.send(format!("#{},connect_error,{},{}", f.name, unix_us(), e.to_string().replace(',', ";")));
                tokio::time::sleep(Duration::from_secs(30)).await;
                continue;
            }
        };
        let _ = tx.send(format!("#{},connected,{},", f.name, unix_us()));
        if let Some(s) = &f.sub {
            if ws.send(Message::Text(s.to_string().into())).await.is_err() { continue; }
        }
        let mut keep = interval(Duration::from_secs(f.ping.map_or(3600, |p| p.0)));
        keep.tick().await;
        loop {
            tokio::select! {
                _ = keep.tick(), if f.ping.is_some() => {
                    if ws.send(Message::Text(f.ping.unwrap().1.into())).await.is_err() { break; }
                }
                msg = ws.next() => {
                    let Some(msg) = msg else { break };
                    let text = match msg {
                        Ok(Message::Text(t)) => t,
                        Ok(Message::Ping(x)) => { let _ = ws.send(Message::Pong(x)).await; continue; }
                        Ok(Message::Close(_)) => break,
                        Ok(_) => continue,
                        Err(e) => { let _ = tx.send(format!("#{},ws_error,{},{}", f.name, unix_us(), e.to_string().replace(',', ";"))); break; }
                    };
                    let recv = unix_us();
                    let Ok(v) = serde_json::from_str::<Value>(&text) else { continue };
                    for (t, p) in (f.parse)(&v) {
                        let _ = tx.send(format!("{},{recv},{t},{p}", f.name));
                    }
                }
            }
        }
        let _ = tx.send(format!("#{},disconnected,{},", f.name, unix_us()));
        tokio::time::sleep(Duration::from_secs(1)).await;
    }
}

pub async fn run(out: PathBuf, minutes: u64) -> Result<()> {
    use std::io::Write;
    std::fs::create_dir_all(&out)?;
    let path = out.join(format!("spotprobe_{}.csv.gz", unix_us() / 1_000));
    let file = std::fs::File::create(&path)?;
    let mut gz = flate2::write::GzEncoder::new(std::io::BufWriter::new(file), flate2::Compression::fast());
    writeln!(gz, "feed,recv_us,exch_ms,price")?;
    eprintln!("spotprobe -> {} for {minutes} min", path.display());
    let (tx, mut rx) = mpsc::unbounded_channel::<String>();
    for f in feeds() { tokio::spawn(run_feed(f, tx.clone())); }
    let deadline = tokio::time::Instant::now() + Duration::from_secs(minutes * 60);
    let mut n = 0u64;
    let mut report = interval(Duration::from_secs(60));
    loop {
        tokio::select! {
            _ = tokio::time::sleep_until(deadline) => break,
            _ = report.tick() => { eprintln!("[{}] rows {n}", unix_us() / 1_000_000); gz.flush()?; }
            line = rx.recv() => {
                let Some(line) = line else { break };
                if line.starts_with('#') { eprintln!("{line}"); }
                writeln!(gz, "{line}")?;
                n += 1;
            }
        }
    }
    gz.finish()?;
    eprintln!("done: {n} rows -> {}", path.display());
    Ok(())
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    #[test]
    fn parsers_read_verbatim_shapes() {
        assert_eq!(super::bitstamp(&json!({"event":"trade","data":{"price":65000.5,"microtimestamp":"1790700000123456"}})), vec![(1790700000123, 65000.5)]);
        assert_eq!(super::binance(&json!({"e":"trade","T":1790700000123i64,"p":"65000.10"})), vec![(1790700000123, 65000.10)]);
        assert_eq!(super::okx(&json!({"arg":{"channel":"trades"},"data":[{"px":"65000.1","ts":"1790700000123"}]})), vec![(1790700000123, 65000.1)]);
        assert_eq!(super::bybit(&json!({"topic":"publicTrade.BTCUSDT","data":[{"T":1790700000123i64,"p":"65000.1"}]})), vec![(1790700000123, 65000.1)]);
        assert!(super::coinbase_ticker(&json!({"type":"subscriptions"})).is_empty());
    }
}
