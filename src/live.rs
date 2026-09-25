//! ARMED market maker. Places REAL orders. Same quoting rule as `shadow` (gate: join the touch
//! on both sides of mid-band markets, pull a side when 1 s momentum runs into it or it is nearly
//! empty), clip 1, |position| <= max_pos per market, flat-or-hold to settlement (never crosses).
//!
//! Purpose of the first runs: measure REAL fills — in particular how much of the queue ahead of
//! us at join time clears by CANCELS rather than trades (the shadow assumed none did).
//!
//! Guardrails (each one a past incident in this repo's memory):
//! - orders keyed by client_order_id, never by ticker;
//! - position from the venue's own fill messages (`post_position_fp`), not from our arithmetic;
//! - loss cap on venue equity (balance + position exposure), read the same way at start and
//!   during the run, PLUS a cumulative cap persisted across restarts in `live_state.json`;
//! - every order in an order group whose contracts_limit makes the VENUE cancel us on a sweep;
//! - posts gated on time-to-close at post time, resting orders pulled before close;
//! - venue rejections are counted, never fatal; any feed break cancels everything first;
//! - on exit (deadline, cap, ctrl-c, error) cancel all, then verify nothing rests.

use std::{
    collections::{HashMap, VecDeque},
    fs::{self, File, OpenOptions},
    io::{BufWriter, Write},
    path::PathBuf,
    sync::Arc,
    time::{Duration, Instant},
};

use anyhow::{Context, Result, bail};
use flate2::{Compression, write::GzEncoder};
use futures_util::{SinkExt, StreamExt};
use serde_json::{Value, json};
use tokio::{sync::mpsc, time::interval};
use tokio_tungstenite::{connect_async, tungstenite::Message};

use crate::{
    auth::Auth,
    book::{Book, PRICE_SCALE, SIZE_SCALE, parse_value},
    client, rest_base, rfc3339_us, signed_ws_request, unix_us,
};

pub struct LiveParams {
    pub series: Vec<String>,
    pub minutes: u64,
    pub max_pos_fp: i64,
    pub session_max_loss_c: f64,
    pub cumulative_max_loss_c: f64,
    pub group_contracts_per_15s: i64,
    pub stop_before_close_s: i64,
    pub mid_lo_c: f64,
    pub mid_hi_c: f64,
    pub mom_pull_c: f64,
    pub thin_pull: f64,
    pub exchange_index: i64,
    pub out: PathBuf,
    /// 0 = join the touch (mid band). n > 0 = PENNY (PREREG_penny.md): one tick inside the
    /// OTHERS' touch, any band 1-99 c, only when their spread is at least n ticks.
    pub penny_room: i64,
    /// No order that would OPEN or ADD to a position this close to the market's close; orders
    /// that reduce the position keep quoting until `stop_before_close_s`. 450 s cut live
    /// leftovers 126 → 15 ct over 197 markets (`leftover_rules.py`, 2026-09-25).
    pub open_cutoff_s: i64,
}

const BID: usize = 0;
const ASK: usize = 1;

#[derive(Clone, Copy, PartialEq, Debug)]
enum St {
    PendingNew,
    Resting,
    PendingCancel,
}

struct LiveOrder {
    coid: String,
    order_id: Option<String>,
    ticker: String,
    side: usize,
    price: i64,
    st: St,
}

#[derive(Default)]
struct Mkt {
    book: Option<Book>,
    close_unix_ms: i64,
    mids: VecDeque<(i64, f64)>,
    pos_fp: i64,
    /// Our own fill ledger for this market, this run: contracts of YES and NO bought and the net
    /// cash flow (cents). Marked at `last_mid`, a YES+NO pair is worth 100 at any mid.
    yes_ct: f64,
    no_ct: f64,
    flow_c: f64,
    last_mid: f64,
    slots: [Option<String>; 2], // coid per side
    /// No posting in this market until then: after any fill, until the venue's `fill` message
    /// (the position authority) has landed. The `user_order` "executed" message can arrive first,
    /// free the slot, and let a new order post on a stale position (NEAR reached +2, 2026-09-25).
    hold_until_us: i64,
}

impl Mkt {
    fn mtm_c(&self) -> f64 {
        self.flow_c + self.yes_ct * self.last_mid + self.no_ct * (100.0 - self.last_mid)
    }

    fn mid_at(&self, t: i64) -> Option<f64> {
        self.mids.iter().rev().find(|(vt, _)| *vt <= t).map(|(_, m)| *m)
    }
}

/// Results of REST calls, delivered back to the single-threaded loop.
enum Done {
    Created { coid: String, res: Result<Value> },
    Cancelled { coid: String, res: Result<Value> },
}

/// Gzipped JSONL. A 3 h, 9-series run wrote 431 MB uncompressed (the box has ~3 GB free);
/// gzip -1 made it 55 MB. `flush` is a gzip SYNC flush, so a crash loses at most one
/// housekeeping interval and the file stays readable up to the last flush.
struct Journal(BufWriter<GzEncoder<File>>);

impl Journal {
    fn row(&mut self, kind: &str, v: Value) {
        let _ = writeln!(self.0, "{}", json!({"k": kind, "t": unix_us(), "v": v}));
    }
    fn flush(&mut self) {
        let _ = self.0.flush();
    }
    fn finish(self) {
        if let Ok(gz) = self.0.into_inner() {
            let _ = gz.finish();
        }
    }
}

pub async fn run(auth: Auth, p: LiveParams) -> Result<()> {
    fs::create_dir_all(&p.out)?;
    let auth = Arc::new(auth);
    let http = client()?;
    let stamp = unix_us() / 1_000;
    let mut j = Journal(BufWriter::new(GzEncoder::new(
        OpenOptions::new().create(true).append(true).open(p.out.join(format!("live_{stamp}.jsonl.gz")))?,
        Compression::fast(),
    )));

    // ---- equity baseline and the cumulative cap across restarts ----
    let equity0 = equity_c(&http, &auth, p.exchange_index).await?;
    let state_path = p.out.join("live_state.json");
    let baseline = match fs::read_to_string(&state_path) {
        Ok(s) => serde_json::from_str::<Value>(&s)?["baseline_equity_c"].as_f64().context("state")?,
        Err(_) => {
            fs::write(&state_path, json!({"baseline_equity_c": equity0, "created_ms": stamp}).to_string())?;
            equity0
        }
    };
    if equity0 < baseline - p.cumulative_max_loss_c {
        bail!("cumulative loss cap hit: equity {equity0:.2}c vs baseline {baseline:.2}c (cap {:.0}c); refusing to start", p.cumulative_max_loss_c);
    }
    eprintln!("ARMED. equity {:.2}c, cumulative baseline {:.2}c, session cap {:.0}c, cumulative cap {:.0}c",
        equity0, baseline, p.session_max_loss_c, p.cumulative_max_loss_c);
    j.row("start", json!({"equity_c": equity0, "baseline_c": baseline, "series": p.series}));

    // ---- venue-side kill switch ----
    let group = signed(&http, &auth, "POST", "/portfolio/order_groups/create",
        Some(&json!({"contracts_limit": p.group_contracts_per_15s, "exchange_index": p.exchange_index}))).await?;
    let mut group_id = group["order_group_id"].as_str().context("order group id")?.to_owned();
    eprintln!("order group {group_id}: venue cancels all at {} contracts matched / 15 s", p.group_contracts_per_15s);
    j.row("order_group", group.clone());

    let (done_tx, mut done_rx) = mpsc::unbounded_channel::<Done>();
    let mut orders: HashMap<String, LiveOrder> = HashMap::new();
    let mut markets: HashMap<String, Mkt> = HashMap::new();
    let mut tokens = 900.0f64;
    let mut last_refill = unix_us();
    let (mut posts, mut cancels, mut rejects, mut fills, mut group_trips) = (0u64, 0u64, 0u64, 0u64, 0u64);
    // Orders of ours that another maker improved past while they rested (counted once each).
    let mut undercuts = 0u64;
    let mut undercut_seen: std::collections::HashSet<String> = std::collections::HashSet::new();
    let mut cash_c = 0.0f64; // from our own fills, for the fast local mark-to-market cap
    // Session P&L the caps use: closed markets' locked-in ledger value + open markets marked.
    let mut realized_c = 0.0f64;
    // Order group tripped: no posting at all until a reset SUCCEEDS (not merely until a timer
    // expires — posting into a tripped group is rejected and must not re-arm the pause).
    let mut group_tripped = false;
    let mut paused_until_us = 0i64;
    let started = Instant::now();
    let deadline = started + Duration::from_secs(p.minutes * 60);
    let mut stop_reason = String::from("deadline");

    'outer: while Instant::now() < deadline {
        // Discover the current open market per series (REST), then subscribe.
        refresh_markets(&http, &p.series, &mut markets).await;
        sync_positions(&http, &auth, &mut markets).await?;
        let (mut ws, _) = match connect_async(signed_ws_request(&auth)?).await {
            Ok(x) => x,
            Err(e) => { eprintln!("ws connect: {e}"); tokio::time::sleep(Duration::from_secs(1)).await; continue; }
        };
        let tickers: Vec<String> = markets.keys().cloned().collect();
        ws.send(Message::Text(json!({"id": 1, "cmd": "subscribe", "params": {
            "channels": ["orderbook_delta", "trade"], "market_tickers": tickers, "use_yes_price": true}}).to_string().into())).await?;
        ws.send(Message::Text(json!({"id": 2, "cmd": "subscribe", "params": {
            "channels": ["fill", "user_orders"]}}).to_string().into())).await?;
        let mut next_id = 3u64;
        let mut seqs: HashMap<u64, u64> = HashMap::new();
        for m in markets.values_mut() { m.book = None; }
        let mut housekeeping = interval(Duration::from_secs(10));
        let mut equity_tick = interval(Duration::from_secs(20));
        equity_tick.tick().await;

        loop {
            let msg = tokio::select! {
                m = ws.next() => m,
                d = done_rx.recv() => {
                    let Some(d) = d else { continue };
                    match d {
                        Done::Created { coid, res } => match res {
                            Ok(v) => {
                                j.row("ack_new", json!({"coid": coid, "resp": v}));
                                if let Some(o) = orders.get_mut(&coid) {
                                    o.order_id = v["order_id"].as_str().map(str::to_owned);
                                    if o.st == St::PendingNew { o.st = St::Resting; }
                                    // A cancel requested before the ack could not name the order.
                                    if o.st == St::PendingCancel { if let Some(id) = o.order_id.clone() {
                                        spawn_cancel(&http, &auth, &done_tx, coid.clone(), id, o.ticker.clone(), p.exchange_index);
                                    }}
                                }
                            }
                            Err(e) => {
                                rejects += 1;
                                let es = format!("{e:#}");
                                j.row("reject_new", json!({"coid": coid, "err": es}));
                                if es.contains("order_group") && !group_tripped {
                                    group_trips += 1;
                                    group_tripped = true;
                                    paused_until_us = unix_us() + 20_000_000;
                                }
                                release(&mut orders, &mut markets, &coid);
                            }
                        },
                        Done::Cancelled { coid, res } => {
                            match &res {
                                Ok(v) => j.row("ack_cancel", json!({"coid": coid, "resp": v})),
                                Err(e) => { j.row("reject_cancel", json!({"coid": coid, "err": format!("{e:#}")})); }
                            }
                            // Either way the order is gone or already terminal (filled/cancelled);
                            // user_orders is the authority and will also release it.
                            release(&mut orders, &mut markets, &coid);
                        }
                    }
                    continue;
                }
                _ = housekeeping.tick() => {
                    j.flush();
                    let now_ms = unix_us() / 1_000;
                    // Pull resting orders in markets inside the close buffer; drop closed markets.
                    let late: Vec<String> = orders.values()
                        .filter(|o| markets.get(&o.ticker).is_some_and(|m| m.close_unix_ms - now_ms < p.stop_before_close_s * 1_000))
                        .map(|o| o.coid.clone()).collect();
                    for c in late { request_cancel(&http, &auth, &done_tx, &mut orders, &c, p.exchange_index, &mut cancels); }
                    let before = markets.len();
                    // Lock in the ledger value of markets we are about to forget.
                    for m in markets.values().filter(|m| now_ms >= m.close_unix_ms + 5_000) {
                        realized_c += m.mtm_c();
                    }
                    markets.retain(|_, m| now_ms < m.close_unix_ms + 5_000);
                    if markets.len() != before || markets.len() < p.series.len() {
                        let old: Vec<String> = markets.keys().cloned().collect();
                        refresh_markets(&http, &p.series, &mut markets).await;
                        if let Err(e) = sync_positions(&http, &auth, &mut markets).await { eprintln!("positions: {e:#}"); }
                        let new: Vec<String> = markets.keys().filter(|k| !old.contains(k)).cloned().collect();
                        if !new.is_empty() {
                            ws.send(Message::Text(json!({"id": next_id, "cmd": "subscribe", "params": {
                                "channels": ["orderbook_delta", "trade"], "market_tickers": new, "use_yes_price": true}}).to_string().into())).await?;
                            next_id += 1;
                            j.row("markets", json!(markets.keys().collect::<Vec<_>>()));
                        }
                    }
                    if group_tripped && unix_us() > paused_until_us {
                        let path = format!("/portfolio/order_groups/{group_id}/reset");
                        match signed(&http, &auth, "PUT", &path, Some(&json!({}))).await {
                            Ok(_) => { j.row("group_reset", json!({})); group_tripped = false; paused_until_us = 0; }
                            // A tripped group is DELETED by the venue (reset → 404
                            // order_group_not_found, 2026-09-23), so replace it instead.
                            Err(e) if format!("{e:#}").contains("order_group_not_found") => {
                                let body = json!({"contracts_limit": p.group_contracts_per_15s, "exchange_index": p.exchange_index});
                                match signed(&http, &auth, "POST", "/portfolio/order_groups/create", Some(&body)).await {
                                    Ok(g) => {
                                        if let Some(id) = g["order_group_id"].as_str() {
                                            group_id = id.to_owned();
                                            group_tripped = false;
                                            paused_until_us = 0;
                                            j.row("group_replaced", g.clone());
                                            eprintln!("order group replaced: {group_id}");
                                        }
                                    }
                                    Err(e) => eprintln!("order group re-create failed: {e:#}"),
                                }
                            }
                            Err(e) => eprintln!("group reset failed: {e:#}"),
                        }
                    }
                    if Instant::now() >= deadline { break 'outer; }
                    continue;
                }
                _ = equity_tick.tick() => {
                    // Caps run on OUR ledger, marked to mid (a pair is exactly 100): the venue's
                    // position fields net pairs away, which read as a phantom −$6 on 2026-09-25.
                    let session = realized_c + markets.values().map(Mkt::mtm_c).sum::<f64>();
                    let cumulative = equity0 - baseline + session;
                    let venue = equity_c(&http, &auth, p.exchange_index).await.ok();
                    j.row("equity", json!({"session_mtm_c": session, "cumulative_c": cumulative, "venue_cash_plus_net_exposure_c": venue}));
                    eprintln!("[{}s] session {:+.2}c cumulative {:+.2}c (venue cash+net exposure {:.2}c) | posts={} cancels={} rejects={} fills={} group_trips={} undercuts={} resting={}",
                        started.elapsed().as_secs(), session, cumulative, venue.unwrap_or(f64::NAN), posts, cancels, rejects, fills, group_trips, undercuts, orders.len());
                    if session < -p.session_max_loss_c { stop_reason = format!("session loss cap: {session:.2}c"); break 'outer; }
                    if cumulative < -p.cumulative_max_loss_c { stop_reason = format!("cumulative loss cap: {cumulative:.2}c"); break 'outer; }
                    continue;
                }
                _ = tokio::signal::ctrl_c() => { stop_reason = "ctrl-c".into(); break 'outer; }
            };
            let text = match msg {
                Some(Ok(Message::Text(t))) => t,
                Some(Ok(Message::Ping(x))) => { ws.send(Message::Pong(x)).await?; continue; }
                Some(Ok(_)) => continue,
                Some(Err(e)) => { eprintln!("ws error: {e}"); break; }
                None => { eprintln!("ws closed"); break; }
            };
            let now = unix_us();
            let v: Value = match serde_json::from_str(&text) { Ok(v) => v, Err(_) => continue };
            if let (Some(sid), Some(seq)) = (v["sid"].as_u64(), v["seq"].as_u64()) {
                if let Some(prev) = seqs.insert(sid, seq) {
                    if seq != prev + 1 { eprintln!("seq gap sid {sid}"); break; }
                }
            }
            let kind = v["type"].as_str().unwrap_or("");
            let m = &v["msg"];
            match kind {
                "fill" => {
                    fills += 1;
                    j.row("fill", m.clone());
                    let ticker = m["market_ticker"].as_str().unwrap_or("").to_owned();
                    let px = parse_value(m.get("yes_price_dollars"), PRICE_SCALE).unwrap_or(0) as f64 / 100.0;
                    let ct = parse_value(m.get("count_fp"), SIZE_SCALE).unwrap_or(0) as f64 / SIZE_SCALE as f64;
                    let buy_yes = m["book_side"].as_str() == Some("bid");
                    cash_c += if buy_yes { -px * ct } else { px * ct };
                    if let Some(mk) = markets.get_mut(&ticker) {
                        // Bid fill = bought YES at px; ask fill = sold YES at px = bought NO at 100 - px.
                        if buy_yes { mk.yes_ct += ct; mk.flow_c -= px * ct; }
                        else { mk.no_ct += ct; mk.flow_c -= (100.0 - px) * ct; }
                        if let Ok(post) = parse_value(m.get("post_position_fp"), SIZE_SCALE) {
                            mk.pos_fp = post; // the venue's number, not ours
                        }
                    }
                    continue;
                }
                "user_order" => {
                    j.row("user_order", m.clone());
                    let coid = m["client_order_id"].as_str().unwrap_or("").to_owned();
                    let st = m["status"].as_str().unwrap_or("");
                    if m["fill_count_fp"].as_str().is_some_and(|f| f != "0.00") {
                        let ticker = orders.get(&coid).map(|o| o.ticker.clone())
                            .or_else(|| m["ticker"].as_str().map(str::to_owned));
                        if let Some(mk) = ticker.and_then(|t| markets.get_mut(&t)) {
                            mk.hold_until_us = now + 1_500_000;
                        }
                    }
                    if st == "canceled" || st == "executed" {
                        if st == "canceled" && orders.contains_key(&coid) && m["remaining_count_fp"].as_str() == Some("0.00") {
                            // Possibly the order group firing: venue cancelled us, not we.
                            if orders.get(&coid).is_some_and(|o| o.st != St::PendingCancel) {
                                j.row("venue_cancel", json!({"coid": coid}));
                                if !group_tripped {
                                    group_trips += 1;
                                    group_tripped = true;
                                    paused_until_us = now + 20_000_000;
                                }
                            }
                        }
                        release(&mut orders, &mut markets, &coid);
                    }
                    continue;
                }
                _ => {}
            }
            let Some(ticker) = m["market_ticker"].as_str() else { continue };
            let Some(mk) = markets.get_mut(ticker) else { continue };
            let vt = match kind {
                "orderbook_snapshot" => {
                    mk.book = Book::from_snapshot(m).ok();
                    if let Some(t) = mk.book.as_ref().map(Book::touch) {
                        if let (Some(b), Some(a)) = (t.yes_bid_fp, t.yes_ask_fp) {
                            mk.last_mid = (b + a) as f64 / 200.0;
                        }
                    }
                    continue;
                }
                "orderbook_delta" => m["ts"].as_str().and_then(rfc3339_us),
                "trade" => m["ts_ms"].as_i64().map(|x| x * 1_000),
                _ => None,
            };
            let Some(vt) = vt else { continue };
            let Some(book) = mk.book.as_mut() else { continue };
            if kind == "orderbook_delta" {
                let size_before = book.size_at(m["side"].as_str().unwrap_or(""),
                    parse_value(m.get("price_dollars"), PRICE_SCALE).unwrap_or(-1));
                if let Err(e) = book.apply_delta(m) { eprintln!("delta {ticker}: {e:#}"); break; }
                // Our own order joining the book: log exact join time and queue ahead of us.
                if let Some(coid) = m["client_order_id"].as_str() {
                    j.row("own_delta", json!({"coid": coid, "vt": vt, "recv": now, "delta": m["delta_fp"],
                        "price": m["price_dollars"], "side": m["side"], "level_before": size_before}));
                }
                let t = book.touch();
                if let (Some(b), Some(a)) = (t.yes_bid_fp, t.yes_ask_fp) {
                    let mid = (b + a) as f64 / 200.0;
                    if mk.mids.back().is_none_or(|(_, x)| *x != mid) {
                        mk.mids.push_back((vt, mid));
                        mk.last_mid = mid;
                        j.row("B", json!([ticker, vt, b, t.yes_bid_size_fp, a, t.yes_ask_size_fp]));
                    }
                    while mk.mids.len() > 2 && mk.mids[1].0 < vt - 2_000_000 { mk.mids.pop_front(); }
                }
            } else {
                j.row("T", json!([ticker, vt, m["taker_side"], m["yes_price_dollars"], m["count_fp"]]));
            }

            // ---- decide ----
            if group_tripped || now < mk.hold_until_us { continue; }
            tokens = (tokens + (now - last_refill) as f64 / 1e6 * 300.0).min(900.0);
            last_refill = now;
            // The market as OTHER participants make it: our own resting clip removed, or an
            // improved quote of ours would read as the touch and we would penny ourselves.
            let own_px = |i: usize| mk.slots[i].as_ref().and_then(|c| orders.get(c)).map(|o| o.price);
            let (own_bid, own_ask) = (own_px(BID), own_px(ASK));
            let (Some((bid, bsz)), Some((ask, asz))) = (
                book.best_excluding("yes", own_bid, SIZE_SCALE),
                book.best_excluding("no", own_ask, SIZE_SCALE),
            ) else { continue };
            let mid = (bid + ask) as f64 / 200.0;
            let mom = mk.mid_at(vt - 1_000_000).map(|m0| mid - m0).unwrap_or(0.0);
            let tot = (bsz + asz).max(1) as f64;
            let open_ok = mk.close_unix_ms - now / 1_000 > p.stop_before_close_s * 1_000;
            let postable = mid >= p.mid_lo_c && mid <= p.mid_hi_c && open_ok;
            // Diagnostic the shadow could not see: another maker improving past our live quote.
            for (i, own) in [(BID, own_bid), (ASK, own_ask)] {
                let Some(px) = own else { continue };
                let passed = if i == BID { bid > px } else { ask < px };
                if passed {
                    let coid = mk.slots[i].clone().unwrap_or_default();
                    if undercut_seen.insert(coid.clone()) {
                        undercuts += 1;
                        j.row("undercut", json!({"coid": coid, "our_px": px, "their_px": if i == BID { bid } else { ask }, "vt": vt}));
                    }
                }
            }
            for i in [BID, ASK] {
                let side_px = if i == BID { bid } else { ask };
                let tick: i64 = if side_px < 1_000 || side_px > 9_000 { 10 } else { 100 };
                let mut want = if p.penny_room > 0 {
                    open_ok && ask - bid >= p.penny_room * tick && mid > 1.0 && mid < 99.0
                } else {
                    postable
                };
                // Count the clip we are about to post: a fractional position (−0.98 after a
                // partial fill) must not admit a clip that ends at −1.98 (happened 2026-09-23).
                if i == BID && mk.pos_fp + SIZE_SCALE > p.max_pos_fp { want = false; }
                if i == ASK && mk.pos_fp - SIZE_SCALE < -p.max_pos_fp { want = false; }
                // A bid opens/adds unless we are short; an ask opens/adds unless we are long.
                let opens = if i == BID { mk.pos_fp >= 0 } else { mk.pos_fp <= 0 };
                if opens && mk.close_unix_ms - now / 1_000 <= p.open_cutoff_s * 1_000 { want = false; }
                let toxic = if i == ASK { mom > p.mom_pull_c || bsz as f64 / tot > p.thin_pull }
                            else { -mom > p.mom_pull_c || asz as f64 / tot > p.thin_pull };
                if toxic { want = false; }
                let target = match (p.penny_room > 0, i == BID) {
                    (true, true) => bid + tick,
                    (true, false) => ask - tick,
                    (false, true) => bid,
                    (false, false) => ask,
                };
                let cur = mk.slots[i].as_ref().and_then(|c| orders.get(c));
                match cur {
                    Some(o) if o.st == St::PendingCancel || o.st == St::PendingNew => continue,
                    Some(o) if want && o.price == target => continue,
                    Some(o) => {
                        if tokens < 2.0 { continue; }
                        tokens -= 2.0;
                        let c = o.coid.clone();
                        request_cancel(&http, &auth, &done_tx, &mut orders, &c, p.exchange_index, &mut cancels);
                    }
                    None if want => {
                        if tokens < 10.0 { continue; }
                        tokens -= 10.0;
                        posts += 1;
                        let coid = crate::latency::random_id();
                        let body = json!({
                            "ticker": ticker, "client_order_id": coid, "side": if i == BID { "bid" } else { "ask" },
                            "count": "1.00", "price": format!("{:.4}", target as f64 / PRICE_SCALE as f64),
                            "time_in_force": "good_till_canceled", "self_trade_prevention_type": "maker",
                            "post_only": true, "order_group_id": group_id, "exchange_index": p.exchange_index,
                        });
                        j.row("new", json!({"coid": coid, "body": body, "mid": mid, "mom": mom, "bsz": bsz, "asz": asz}));
                        orders.insert(coid.clone(), LiveOrder { coid: coid.clone(), order_id: None,
                            ticker: ticker.to_owned(), side: i, price: target, st: St::PendingNew });
                        mk.slots[i] = Some(coid.clone());
                        let (http2, auth2, tx) = (http.clone(), auth.clone(), done_tx.clone());
                        tokio::spawn(async move {
                            let res = signed(&http2, &auth2, "POST", "/portfolio/events/orders", Some(&body)).await;
                            let _ = tx.send(Done::Created { coid, res });
                        });
                    }
                    None => {}
                }
            }
        }
        // Feed broke: we are blind. Cancel everything before reconnecting.
        eprintln!("feed break: cancelling all resting orders");
        tokio::time::sleep(Duration::from_secs(2)).await; // in-flight creates land first
        cancel_everything(&http, &auth, &mut j, p.exchange_index).await;
        orders.clear();
        for m in markets.values_mut() { m.slots = [None, None]; }
        tokio::time::sleep(Duration::from_millis(500)).await;
    }

    eprintln!("STOP: {stop_reason}. Cancelling everything.");
    // Let creates already in flight land first, or they rest AFTER the sweep (an XRP bid did,
    // 2026-09-25).
    tokio::time::sleep(Duration::from_secs(2)).await;
    j.row("stop", json!({"reason": stop_reason, "posts": posts, "cancels": cancels, "rejects": rejects, "fills": fills, "group_trips": group_trips, "undercuts": undercuts, "local_cash_c": cash_c}));
    cancel_everything(&http, &auth, &mut j, p.exchange_index).await;
    let _ = signed(&http, &auth, "DELETE", &format!("/portfolio/order_groups/{group_id}"), None).await;
    let eq = equity_c(&http, &auth, p.exchange_index).await.unwrap_or(f64::NAN);
    let session = realized_c + markets.values().map(Mkt::mtm_c).sum::<f64>();
    j.row("end", json!({"venue_equity_c": eq, "session_mtm_c": session, "cumulative_c": equity0 - baseline + session}));
    eprintln!("session (own ledger, marked) {session:+.2}c, cumulative {:+.2}c", equity0 - baseline + session);
    j.finish();
    eprintln!("end equity {eq:.2}c, session {:+.2}c, cumulative {:+.2}c (open positions ride to settlement)", eq - equity0, eq - baseline);
    Ok(())
}

fn release(orders: &mut HashMap<String, LiveOrder>, markets: &mut HashMap<String, Mkt>, coid: &str) {
    if let Some(o) = orders.remove(coid) {
        if let Some(m) = markets.get_mut(&o.ticker) {
            if m.slots[o.side].as_deref() == Some(coid) { m.slots[o.side] = None; }
        }
    }
}

fn request_cancel(
    http: &reqwest::Client, auth: &Arc<Auth>, tx: &mpsc::UnboundedSender<Done>,
    orders: &mut HashMap<String, LiveOrder>, coid: &str, exchange_index: i64, cancels: &mut u64,
) {
    let Some(o) = orders.get_mut(coid) else { return };
    if o.st == St::PendingCancel { return; }
    o.st = St::PendingCancel;
    *cancels += 1;
    // If the create has not been acked yet we cannot name the order; the ack handler cancels it.
    if let Some(id) = o.order_id.clone() {
        spawn_cancel(http, auth, tx, coid.to_owned(), id, o.ticker.clone(), exchange_index);
    }
}

fn spawn_cancel(http: &reqwest::Client, auth: &Arc<Auth>, tx: &mpsc::UnboundedSender<Done>,
    coid: String, id: String, ticker: String, exchange_index: i64) {
    let (http, auth, tx) = (http.clone(), auth.clone(), tx.clone());
    tokio::spawn(async move {
        let path = format!("/portfolio/events/orders/{id}?market_ticker={ticker}&exchange_index={exchange_index}");
        let res = signed(&http, &auth, "DELETE", &path, None).await;
        let _ = tx.send(Done::Cancelled { coid, res });
    });
}

/// Standalone kill: cancel every resting order on the account and verify the book is empty.
/// Places nothing. For cleanup when an engine exited with an order still in flight.
pub async fn cancel_all(auth: Auth, exchange_index: i64) -> Result<()> {
    let http = client()?;
    let tmp = std::env::temp_dir().join(format!("cancel_all_{}.jsonl.gz", unix_us()));
    let mut j = Journal(BufWriter::new(GzEncoder::new(File::create(&tmp)?, Compression::fast())));
    cancel_everything(&http, &auth, &mut j, exchange_index).await;
    j.finish();
    Ok(())
}

async fn cancel_everything(http: &reqwest::Client, auth: &Auth, j: &mut Journal, exchange_index: i64) {
    for attempt in 0..3 {
        let open = match signed(http, auth, "GET", "/portfolio/orders?status=resting", None).await {
            Ok(v) => v,
            Err(e) => { eprintln!("list resting failed: {e:#}"); continue; }
        };
        let list: Vec<(String, String)> = open["orders"].as_array().into_iter().flatten()
            .filter_map(|o| Some((o["order_id"].as_str()?.to_owned(), o["ticker"].as_str()?.to_owned()))).collect();
        j.row("sweep", json!({"attempt": attempt, "resting": list.len()}));
        if list.is_empty() { eprintln!("verified: no resting orders"); return; }
        let body = json!({"orders": list.iter().map(|(id, t)| json!({"order_id": id, "market_ticker": t, "exchange_index": exchange_index})).collect::<Vec<_>>()});
        // The batch call can return 200 while cancelling nothing (per-order errors ride inside the
        // body — seen 2026-09-25), so log its body and ALWAYS follow with single cancels.
        match signed(http, auth, "DELETE", "/portfolio/events/orders/batched", Some(&body)).await {
            Ok(v) => j.row("batch_cancel", v),
            Err(e) => eprintln!("batch cancel failed: {e:#}"),
        }
        for (id, t) in &list {
            let path = format!("/portfolio/events/orders/{id}?market_ticker={t}&exchange_index={exchange_index}");
            if let Err(e) = signed(http, auth, "DELETE", &path, None).await {
                eprintln!("cancel {id} on {t} failed: {e:#}");
            }
        }
    }
    eprintln!("WARNING: could not verify an empty book after 3 sweeps — check the account");
}

/// Venue equity in cents: cash on the trading shard + NET exposure of open positions. It
/// UNDERSTATES while pairs are open (a YES+NO pair nets to exposure 0 until settlement), so it is
/// only a conservative startup / cumulative reference; the running caps use the engine's own
/// ledger. (Valuing holdings at `total_traded_dollars` instead OVERSTATED them 3x — it counts
/// turnover — and phantom-tripped the session cap on 2026-09-25.)
async fn equity_c(http: &reqwest::Client, auth: &Auth, exchange_index: i64) -> Result<f64> {
    let bal = signed(http, auth, "GET", "/portfolio/balance", None).await?;
    let shard = bal["balance_breakdown"].as_array().into_iter().flatten()
        .find(|b| b["exchange_index"].as_i64() == Some(exchange_index))
        .and_then(|b| b["balance"].as_str()).context("no balance on trading shard")?;
    let cash: f64 = shard.parse::<f64>()? * 100.0;
    let pos = signed(http, auth, "GET", "/portfolio/positions?count_filter=position&limit=200", None).await?;
    let exposure: f64 = pos["market_positions"].as_array().into_iter().flatten()
        .filter_map(|m| m["market_exposure_dollars"].as_str()?.parse::<f64>().ok()).sum::<f64>() * 100.0;
    Ok(cash + exposure)
}

/// Seed positions from the venue, so a restart never forgets inventory it is still holding.
async fn sync_positions(http: &reqwest::Client, auth: &Auth, markets: &mut HashMap<String, Mkt>) -> Result<()> {
    let pos = signed(http, auth, "GET", "/portfolio/positions?count_filter=position&limit=200", None).await?;
    for m in pos["market_positions"].as_array().into_iter().flatten() {
        let (Some(t), Some(p)) = (m["ticker"].as_str(), m["position_fp"].as_str()) else { continue };
        if let (Some(mk), Ok(p)) = (markets.get_mut(t), parse_value(Some(&json!(p)), SIZE_SCALE)) {
            mk.pos_fp = p;
        }
    }
    Ok(())
}

async fn refresh_markets(http: &reqwest::Client, series: &[String], markets: &mut HashMap<String, Mkt>) {
    let now_ms = unix_us() / 1_000;
    for s in series {
        let url = format!("{}/markets?series_ticker={s}&status=open&limit=5", rest_base());
        let Ok(r) = http.get(url).send().await else { continue };
        let Ok(body) = r.json::<Value>().await else { continue };
        for m in body["markets"].as_array().into_iter().flatten() {
            let (Some(t), Some(c)) = (m["ticker"].as_str(), m["close_time"].as_str().and_then(rfc3339_us)) else { continue };
            if c / 1_000 > now_ms && !markets.contains_key(t) {
                markets.insert(t.to_owned(), Mkt { close_unix_ms: c / 1_000, ..Default::default() });
            }
        }
    }
}

pub async fn signed(http: &reqwest::Client, auth: &Auth, method: &str, path: &str, body: Option<&Value>) -> Result<Value> {
    let h = auth.headers(method, &format!("/trade-api/v2{path}"));
    let url = format!("{}{path}", rest_base());
    let mut req = match method {
        "POST" => http.post(url),
        "PUT" => http.put(url),
        "DELETE" => http.delete(url),
        _ => http.get(url),
    }
    .header("KALSHI-ACCESS-KEY", h.key_id)
    .header("KALSHI-ACCESS-TIMESTAMP", h.timestamp)
    .header("KALSHI-ACCESS-SIGNATURE", h.signature);
    if let Some(b) = body { req = req.json(b); }
    let resp = req.send().await?;
    let status = resp.status();
    let text = resp.text().await?;
    if !status.is_success() { bail!("{method} {path} → {status}: {text}"); }
    Ok(if text.is_empty() { Value::Null } else { serde_json::from_str(&text)? })
}

#[cfg(test)]
mod tests {
    use super::Mkt;

    #[test]
    fn a_pair_is_worth_one_dollar_at_any_mid() {
        // Bought YES at 60 and NO at 45: a pair that cost 105 and pays 100 → −5 at every mid.
        let mut m = Mkt { yes_ct: 1.0, no_ct: 1.0, flow_c: -105.0, ..Default::default() };
        for mid in [1.0, 37.5, 50.0, 99.0] {
            m.last_mid = mid;
            assert!((m.mtm_c() + 5.0).abs() < 1e-9, "mid {mid}: {}", m.mtm_c());
        }
        // One unpaired YES bought at 20, marked at the mid.
        let mut n = Mkt { yes_ct: 1.0, flow_c: -20.0, last_mid: 35.0, ..Default::default() };
        assert!((n.mtm_c() - 15.0).abs() < 1e-9);
        n.last_mid = 0.0;
        assert!((n.mtm_c() + 20.0).abs() < 1e-9);
    }
}
