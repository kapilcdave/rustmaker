//! ARMED latency test: places real orders. 1-contract post-only YES bids at $0.01 on one 15M
//! market, each cancelled immediately. Worst case per order is a $0.01 fill.
//!
//! For every create and cancel it records, all in µs:
//!   rtt        local send → HTTP response
//!   to_book    venue book-change `ts` (µs) − our send time   (true one-way into the engine)
//!   book_to_us our receipt of that delta − its venue `ts`     (publish + feed back)

use std::{collections::HashMap, time::Duration};

use anyhow::{Context, Result, bail};
use futures_util::{SinkExt, StreamExt};
use serde_json::{Value, json};
use tokio::sync::{mpsc, watch};
use tokio_tungstenite::{connect_async, tungstenite::Message};

use crate::{
    auth::Auth,
    book::Book,
    client, rest_base, rfc3339_us, signed_ws_request,
    stats::Samples,
    unix_us,
};

const BID_PRICE: &str = "0.0100";
/// Never place unless the best YES ask is at least this far away (fixed-point 1e-4 dollars).
const MIN_ASK_FP: i64 = 500;
const SERIES: &str = "KXBTC15M";

struct OwnDelta {
    client_order_id: String,
    positive: bool,
    venue_us: i64,
    recv_us: i64,
}

pub async fn run(auth: Auth, n: usize) -> Result<()> {
    let http = client()?;
    let ticker = pick_market(&http).await?;
    eprintln!("latency test on {ticker}: {n} × (post-only 1 @ {BID_PRICE}, cancel)");

    let (own_tx, mut own_rx) = mpsc::unbounded_channel::<OwnDelta>();
    let (ask_tx, ask_rx) = watch::channel::<Option<i64>>(None);
    let ws_auth_req = signed_ws_request(&auth)?;
    let ws_ticker = ticker.clone();
    let feed = tokio::spawn(async move { feed(ws_auth_req, ws_ticker, own_tx, ask_tx).await });

    // Warm the keep-alive connection and wait for a book.
    signed(&http, &auth, "GET", "/portfolio/balance", None).await?;
    for _ in 0..50 {
        if ask_rx.borrow().is_some() {
            break;
        }
        tokio::time::sleep(Duration::from_millis(100)).await;
    }

    let names = [
        "create.rtt", "create.to_book", "create.book_to_us",
        "cancel.rtt", "cancel.to_book", "cancel.book_to_us",
    ];
    let mut s: HashMap<&str, Samples> = names.iter().map(|k| (*k, Samples::new(10_000))).collect();
    let mut sent: HashMap<String, (i64, i64)> = HashMap::new(); // coid → (create_send, cancel_send)
    let mut resting: Vec<(String, String)> = Vec::new(); // (order_id, coid) not yet cancelled
    let mut skipped = 0;

    for i in 0..n {
        let ask = *ask_rx.borrow();
        if !ask.is_some_and(|a| a >= MIN_ASK_FP) {
            skipped += 1;
            eprintln!("skip {i}: best ask {ask:?} too close to {BID_PRICE}");
            tokio::time::sleep(Duration::from_millis(500)).await;
            continue;
        }
        let coid = random_id();
        let body = json!({
            "ticker": ticker, "client_order_id": coid, "side": "bid", "count": "1.00",
            "price": BID_PRICE, "time_in_force": "good_till_canceled",
            "self_trade_prevention_type": "maker", "post_only": true,
        });
        let t0 = unix_us();
        let resp = signed(&http, &auth, "POST", "/portfolio/events/orders", Some(&body)).await;
        let t1 = unix_us();
        let resp = match resp {
            Ok(r) => r,
            Err(e) => {
                eprintln!("create {i} failed: {e:#}");
                if i == 0 {
                    break; // a first-order rejection is a setup problem, not noise
                }
                continue;
            }
        };
        s.get_mut("create.rtt").unwrap().push(t1 - t0, 1.0);
        let order_id = resp["order_id"].as_str().context("create: no order_id")?.to_owned();
        if resp["fill_count"].as_str().is_some_and(|f| f != "0.00") {
            eprintln!("FILLED on create {i} ({resp}); stopping");
            resting.push((order_id, coid));
            break;
        }
        resting.push((order_id.clone(), coid.clone()));

        let t2 = unix_us();
        let path = format!("/portfolio/events/orders/{order_id}?market_ticker={ticker}");
        match signed(&http, &auth, "DELETE", &path, None).await {
            Ok(_) => {
                s.get_mut("cancel.rtt").unwrap().push(unix_us() - t2, 1.0);
                resting.retain(|(o, _)| o != &order_id);
            }
            Err(e) => eprintln!("cancel {i} failed: {e:#}"),
        }
        sent.insert(coid, (t0, t2));
        // Basic tier: 100 write tokens/s, create 10 + cancel 2.
        tokio::time::sleep(Duration::from_millis(400)).await;
        while let Ok(d) = own_rx.try_recv() {
            record(&mut s, &sent, d);
        }
    }
    tokio::time::sleep(Duration::from_millis(1_000)).await;
    while let Ok(d) = own_rx.try_recv() {
        record(&mut s, &sent, d);
    }
    feed.abort();

    // Sweep anything still resting (ours or from a failed cancel).
    let open = signed(&http, &auth, "GET", "/portfolio/orders?status=resting", None).await?;
    for o in open["orders"].as_array().into_iter().flatten() {
        if let (Some(id), Some(t)) = (o["order_id"].as_str(), o["ticker"].as_str()) {
            let path = format!("/portfolio/events/orders/{id}?market_ticker={t}");
            match signed(&http, &auth, "DELETE", &path, None).await {
                Ok(_) => eprintln!("swept resting order {id} on {t}"),
                Err(e) => eprintln!("SWEEP FAILED for {id} on {t}: {e:#}"),
            }
        }
    }
    let fills = signed(&http, &auth, "GET", "/portfolio/fills?limit=5", None).await?;

    let report: serde_json::Map<String, Value> =
        names.iter().map(|k| ((*k).into(), s[k].summary(&[]))).collect();
    println!("{}", serde_json::to_string_pretty(&json!({
        "ticker": ticker, "orders_attempted": n, "skipped_for_safety": skipped,
        "latency_us": report, "recent_fills": fills["fills"],
    }))?);
    Ok(())
}

fn record(s: &mut HashMap<&str, Samples>, sent: &HashMap<String, (i64, i64)>, d: OwnDelta) {
    let Some((create_send, cancel_send)) = sent.get(&d.client_order_id) else { return };
    let (leg, send) = if d.positive { ("create", *create_send) } else { ("cancel", *cancel_send) };
    s.get_mut(format!("{leg}.to_book").as_str()).unwrap().push(d.venue_us - send, 1.0);
    s.get_mut(format!("{leg}.book_to_us").as_str()).unwrap().push(d.recv_us - d.venue_us, 1.0);
}

async fn feed(
    req: tokio_tungstenite::tungstenite::handshake::client::Request,
    ticker: String,
    own: mpsc::UnboundedSender<OwnDelta>,
    ask: watch::Sender<Option<i64>>,
) -> Result<()> {
    let (mut ws, _) = connect_async(req).await?;
    ws.send(Message::Text(
        json!({"id": 1, "cmd": "subscribe", "params": {
            "channels": ["orderbook_delta"], "market_tickers": [ticker], "use_yes_price": true}})
        .to_string()
        .into(),
    ))
    .await?;
    let mut book: Option<Book> = None;
    while let Some(msg) = ws.next().await {
        let Message::Text(text) = msg? else { continue };
        let recv_us = unix_us();
        let v: Value = serde_json::from_str(&text)?;
        let m = &v["msg"];
        match v["type"].as_str() {
            Some("orderbook_snapshot") => book = Some(Book::from_snapshot(m)?),
            Some("orderbook_delta") => {
                if let Some(b) = book.as_mut() {
                    b.apply_delta(m)?;
                }
                if let (Some(coid), Some(vt)) =
                    (m["client_order_id"].as_str(), m["ts"].as_str().and_then(rfc3339_us))
                {
                    let positive = !m["delta_fp"].as_str().unwrap_or("").starts_with('-');
                    let _ = own.send(OwnDelta { client_order_id: coid.into(), positive, venue_us: vt, recv_us });
                }
            }
            _ => continue,
        }
        let _ = ask.send(book.as_ref().and_then(|b| b.touch().yes_ask_fp));
    }
    Ok(())
}

async fn pick_market(http: &reqwest::Client) -> Result<String> {
    let url = format!("{}/markets?series_ticker={SERIES}&status=open&limit=5", rest_base());
    let body: Value = http.get(url).send().await?.json().await?;
    let now_ms = unix_us() / 1_000;
    for m in body["markets"].as_array().into_iter().flatten() {
        let close = m["close_time"].as_str().and_then(rfc3339_us).unwrap_or(0) / 1_000;
        if close - now_ms > 4 * 60_000 {
            return Ok(m["ticker"].as_str().context("ticker")?.to_owned());
        }
    }
    bail!("no {SERIES} market with >4 min to close; retry after the next open")
}

async fn signed(
    http: &reqwest::Client,
    auth: &Auth,
    method: &str,
    path: &str,
    body: Option<&Value>,
) -> Result<Value> {
    let h = auth.headers(method, &format!("/trade-api/v2{path}"));
    let url = format!("{}{path}", rest_base());
    let mut req = match method {
        "POST" => http.post(url),
        "DELETE" => http.delete(url),
        _ => http.get(url),
    }
    .header("KALSHI-ACCESS-KEY", h.key_id)
    .header("KALSHI-ACCESS-TIMESTAMP", h.timestamp)
    .header("KALSHI-ACCESS-SIGNATURE", h.signature);
    if let Some(b) = body {
        req = req.json(b);
    }
    let resp = req.send().await?;
    let status = resp.status();
    let text = resp.text().await?;
    if !status.is_success() {
        bail!("{method} {path} → {status}: {text}");
    }
    Ok(if text.is_empty() { Value::Null } else { serde_json::from_str(&text)? })
}

fn random_id() -> String {
    use ring::rand::{SecureRandom, SystemRandom};
    let mut b = [0u8; 16];
    SystemRandom::new().fill(&mut b).expect("rng");
    b.iter().map(|x| format!("{x:02x}")).collect()
}
