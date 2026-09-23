//! Shadow market maker: live feed, simulated orders, NO real orders.
//!
//! Each strategy joins the touch on both sides of every mid-band 15M crypto market, one contract
//! per side, with an inventory cap. Simulated orders pay our MEASURED latencies (create 5.44 ms,
//! cancel 4.43 ms from decision to the venue book, 2026-09-23) and join the BACK of the displayed
//! queue. Queue ahead shrinks only when prints at our price trade through it, or when the level
//! shrinks below it (cancels ahead of us are otherwise assumed to be behind us: conservative).
//!
//! Arms: `gate`/`gate_pr` reproduce shadow v1-v3. `gate_pr_in` quotes one tick inside when the
//! spread has room (rest alone at a new level: head of the next sweep, not its tail).
//! `gate_pr_front` posts only where the displayed level at our price is small, so a fill never
//! needs a sweep through thousands of contracts ahead of us.
//!
//! Output: a tape (same format as `probe`) plus `F` fill rows carrying the queue we joined behind
//! and the queue still ahead at the fill (`q_join`, `q_fill`, contracts), for offline P&L.

use std::{
    collections::{HashMap, VecDeque},
    fs::{self, File},
    io::{BufWriter, Write},
    path::PathBuf,
    sync::mpsc as std_mpsc,
    time::{Duration, Instant},
};

use anyhow::{Context, Result};
use flate2::{Compression, write::GzEncoder};
use futures_util::{SinkExt, StreamExt};
use serde_json::Value;
use tokio::{sync::mpsc, time::interval};
use tokio_tungstenite::{connect_async, tungstenite::Message};

use crate::{
    auth::Auth,
    book::{Book, PRICE_SCALE, SIZE_SCALE, parse_value},
    discover, rfc3339_us, signed_ws_request, sub_cmd, unix_us,
};

pub struct Params {
    pub create_us: i64,
    pub cancel_us: i64,
    /// Amend: price change in one request, 10 tokens (default cost). Measured 2026-09-23.
    pub amend_us: i64,
    pub size_fp: i64,
    pub max_pos_fp: i64,
    pub mid_lo_c: f64,
    pub mid_hi_c: f64,
    pub stop_before_close_s: i64,
    pub mom_pull_c: f64,
    pub thin_pull: f64,
    /// Advanced tier: 300 write tokens/s, bucket 900; create costs 10, cancel 2.
    pub tokens_per_s: f64,
    pub bucket: f64,
    /// One price tick inside the 15-85c band (whole cent; no sub-cent prices seen there).
    pub tick: i64,
    /// `front` arms post only where the displayed level at our price is at most this.
    pub max_join_fp: i64,
}

impl Default for Params {
    fn default() -> Self {
        Self {
            create_us: 5_440,
            cancel_us: 4_430,
            amend_us: 4_600,
            size_fp: SIZE_SCALE,
            max_pos_fp: 2 * SIZE_SCALE,
            mid_lo_c: 15.0,
            mid_hi_c: 85.0,
            stop_before_close_s: 120,
            mom_pull_c: 0.25,
            thin_pull: 0.9213,
            tokens_per_s: 300.0,
            bucket: 900.0,
            tick: 100,
            max_join_fp: 50 * SIZE_SCALE,
        }
    }
}

const BID: usize = 0; // resting YES bid: filled by a NO taker (taker sells yes)
const ASK: usize = 1; // resting YES ask: filled by a YES taker

#[derive(Clone)]
struct SimOrder {
    price: i64,
    live_at: i64,
    cancel_at: Option<i64>,
    live: bool,
    queue_ahead: i64,
    /// Displayed size at our price when the order went live (the queue we joined behind).
    q_join: i64,
    remaining: i64,
    /// Level shrinks at our price not yet explained by a trade: (venue ms, amount, queue before).
    /// 82% of fill deltas arrive BEFORE their trade message (measured 2026-09-23), so a trade
    /// must undo the clamp its own fill delta already applied, or the queue advances twice.
    shrinks: VecDeque<(i64, i64, i64)>,
    /// (new price, time it reaches the venue). Until then the order still rests at `price`.
    amend: Option<(i64, i64)>,
}

#[derive(Default)]
struct Seat {
    orders: [Option<SimOrder>; 2],
    pos_fp: i64,
    cash_c: f64, // cents
}

struct Strategy {
    name: &'static str,
    gate: bool,
    /// Requote by amending the resting order (one leg, no unquoted gap) instead of
    /// cancel-then-create. A price change still goes to the back of the queue either way.
    amend: bool,
    amends: u64,
    /// Credit cancels at our level pro rata (false = only trades and level clamps advance us).
    prorata: bool,
    /// Quote one tick inside the touch when there is room, so we rest alone at a new level and
    /// are the head of the next sweep instead of its tail.
    improve: bool,
    /// Post only where the displayed level at our price is at most `Params::max_join_fp`.
    front: bool,
    skipped: u64,
    seats: HashMap<String, Seat>,
    tokens: f64,
    last_refill_us: i64,
    posts: u64,
    cancels: u64,
    throttled: u64,
    pulls: u64,
    fills: u64,
    filled_fp: i64,
}

#[derive(Default)]
struct Mkt {
    book: Option<Book>,
    close_unix_ms: i64,
    mids: VecDeque<(i64, f64)>, // (venue µs, mid cents) on touch change
}

impl Mkt {
    fn mid_at(&self, t: i64) -> Option<f64> {
        self.mids.iter().rev().find(|(vt, _)| *vt <= t).map(|(_, m)| *m)
    }
}

fn q(v: &mut [u64], p: f64) -> u64 {
    if v.is_empty() {
        return 0;
    }
    v.sort_unstable();
    v[((v.len() - 1) as f64 * p) as usize]
}

pub async fn run(auth: Auth, out: PathBuf, minutes: u64, p: Params) -> Result<()> {
    fs::create_dir_all(&out)?;
    let stamp = unix_us() / 1_000;
    let tape_path = out.join(format!("shadow_{stamp}.csv.gz"));
    eprintln!("shadow tape {} (NO orders are placed)", tape_path.display());
    let (tape_tx, tape_rx) = std_mpsc::sync_channel::<String>(200_000);
    let tape_thread = std::thread::spawn(move || -> Result<()> {
        let mut gz = GzEncoder::new(BufWriter::new(File::create(&tape_path)?), Compression::fast());
        writeln!(gz, "kind,recv_us,ticker,a,b,c,d,venue_ms,q_join,q_fill")?;
        for line in tape_rx {
            gz.write_all(line.as_bytes())?;
        }
        gz.finish()?.flush()?;
        Ok(())
    });

    let now0 = unix_us();
    // (name, gate, amend, prorata, improve, front). gate/gate_pr reproduce shadow v1-v3.
    let mut strategies: Vec<Strategy> = [
        ("gate", true, false, false, false, false),
        ("gate_pr", true, false, true, false, false),
        ("gate_pr_in", true, false, true, true, false),
        ("gate_pr_front", true, false, true, false, true),
    ]
        .into_iter()
        .map(|(name, gate, amend, prorata, improve, front)| Strategy {
            name, gate, amend, prorata, improve, front, skipped: 0, amends: 0, seats: HashMap::new(),
            tokens: p.bucket, last_refill_us: now0,
            posts: 0, cancels: 0, throttled: 0, pulls: 0, fills: 0, filled_fp: 0,
        })
        .collect();

    let (disc_tx, mut disc_rx) = mpsc::channel(256);
    let disc = tokio::spawn(discover(disc_tx));
    let started = Instant::now();
    let deadline = started + Duration::from_secs(minutes * 60);
    let mut markets: HashMap<String, Mkt> = HashMap::new();
    let mut report = interval(Duration::from_secs(60));
    report.tick().await;
    // Time from frame receipt to the end of the quote decision: parse + book + sim + decide.
    let mut handle_us: Vec<u64> = Vec::with_capacity(200_000);
    let mut tape_dropped: u64 = 0;

    'outer: while Instant::now() < deadline {
        let (mut ws, _) = match connect_async(signed_ws_request(&auth)?).await {
            Ok(x) => x,
            Err(e) => {
                eprintln!("ws connect: {e}");
                tokio::time::sleep(Duration::from_secs(1)).await;
                continue;
            }
        };
        // A disconnect in real life leaves orders resting blind; in the shadow we drop them and
        // count it, so the run is honest about blind time.
        for s in &mut strategies {
            for seat in s.seats.values_mut() {
                seat.orders = [None, None];
            }
        }
        let mut next_id = 1u64;
        let mut seqs: HashMap<u64, u64> = HashMap::new();
        let open: Vec<String> = markets.keys().cloned().collect();
        if !open.is_empty() {
            for m in markets.values_mut() {
                m.book = None;
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
                    markets.insert(d.ticker.clone(), Mkt { close_unix_ms: d.close_unix_ms, ..Default::default() });
                    ws.send(Message::Text(sub_cmd(next_id, std::slice::from_ref(&d.ticker)).into())).await?;
                    next_id += 1;
                    continue;
                }
                _ = close_tick.tick() => {
                    let now_ms = unix_us() / 1_000;
                    let closed: Vec<String> = markets.iter()
                        .filter(|(_, m)| m.close_unix_ms > 0 && now_ms > m.close_unix_ms + 30_000)
                        .map(|(k, _)| k.clone()).collect();
                    for t in closed {
                        markets.remove(&t);
                        for s in &mut strategies {
                            if let Some(seat) = s.seats.remove(&t) {
                                let _ = tape_tx.send(format!(
                                    "S,{},{t},{},{},{:.4},,\n", unix_us(), s.name, seat.pos_fp, seat.cash_c));
                            }
                        }
                    }
                    if Instant::now() >= deadline { break 'outer; }
                    continue;
                }
                _ = report.tick() => {
                    eprintln!("[{}s] handle_us p50={} p99={} max={} n={} | tape rows dropped={}",
                        started.elapsed().as_secs(), q(&mut handle_us, 0.5), q(&mut handle_us, 0.99),
                        handle_us.iter().max().copied().unwrap_or(0), handle_us.len(), tape_dropped);
                    handle_us.clear();
                    for s in &strategies {
                        let pos: i64 = s.seats.values().map(|x| x.pos_fp.abs()).sum();
                        eprintln!("[{}s] {:13} posts={} amends={} cancels={} pulls={} skipped={} throttled={} fills={} filled_ct={:.0} open_abs_pos_ct={:.0}",
                            started.elapsed().as_secs(), s.name, s.posts, s.amends, s.cancels, s.pulls, s.skipped, s.throttled,
                            s.fills, s.filled_fp as f64 / SIZE_SCALE as f64, pos as f64 / SIZE_SCALE as f64);
                    }
                    continue;
                }
                _ = tokio::signal::ctrl_c() => break 'outer,
            };
            let text = match msg {
                Some(Ok(Message::Text(t))) => t,
                Some(Ok(Message::Ping(x))) => { ws.send(Message::Pong(x)).await?; continue; }
                Some(Ok(_)) => continue,
                Some(Err(e)) => { eprintln!("ws error: {e}"); break; }
                None => { eprintln!("ws closed"); break; }
            };
            let now = unix_us();
            let t_recv = Instant::now();
            let v: Value = match serde_json::from_str(&text) { Ok(v) => v, Err(_) => continue };
            if let (Some(sid), Some(seq)) = (v["sid"].as_u64(), v["seq"].as_u64()) {
                if let Some(prev) = seqs.insert(sid, seq) {
                    if seq != prev + 1 {
                        eprintln!("seq gap sid {sid}: {prev} -> {seq}; reconnecting");
                        break;
                    }
                }
            }
            let kind = v["type"].as_str().unwrap_or("");
            let m = &v["msg"];
            let Some(ticker) = m["market_ticker"].as_str() else { continue };
            let Some(mk) = markets.get_mut(ticker) else { continue };

            let vt = match kind {
                "orderbook_snapshot" => {
                    match Book::from_snapshot(m) {
                        Ok(b) => mk.book = Some(b),
                        Err(e) => eprintln!("snapshot {ticker}: {e:#}"),
                    }
                    continue;
                }
                "orderbook_delta" => m["ts"].as_str().and_then(rfc3339_us),
                "trade" => m["ts_ms"].as_i64().map(|x| x * 1_000),
                _ => None,
            };
            let Some(vt) = vt else { continue };
            let Some(book) = mk.book.as_mut() else { continue };

            // 1. Our pending orders/cancels that reached the venue before this event.
            for s in &mut strategies {
                let Some(seat) = s.seats.get_mut(ticker) else { continue };
                for (i, slot) in seat.orders.iter_mut().enumerate() {
                    let Some(o) = slot.as_mut() else { continue };
                    if o.cancel_at.is_some_and(|c| c <= vt) {
                        *slot = None;
                        continue;
                    }
                    if let Some((px, at)) = o.amend.filter(|(_, at)| *at <= vt) {
                        o.price = px;
                        o.live = false;
                        o.live_at = at;
                        o.shrinks.clear();
                        o.amend = None;
                    }
                    if !o.live && o.live_at <= vt {
                        o.live = true;
                        o.queue_ahead = book.size_at(if i == BID { "yes" } else { "no" }, o.price);
                        o.q_join = o.queue_ahead;
                    }
                }
            }

            // 2. Apply the event.
            match kind {
                "orderbook_delta" => {
                    if let Err(e) = book.apply_delta(m) {
                        eprintln!("delta {ticker}: {e:#}; reconnecting");
                        break;
                    }
                    let side = m["side"].as_str().unwrap_or("");
                    let price = parse_value(m.get("price_dollars"), PRICE_SCALE).unwrap_or(-1);
                    let level = book.size_at(side, price);
                    let delta = parse_value(m.get("delta_fp"), SIZE_SCALE).unwrap_or(0);
                    let i = if side == "yes" { BID } else { ASK };
                    for s in &mut strategies {
                        if let Some(Some(o)) = s.seats.get_mut(ticker).map(|x| &mut x.orders[i]) {
                            if o.live && o.price == price {
                                if delta < 0 {
                                    o.shrinks.push_back((vt / 1_000, -delta, o.queue_ahead));
                                    while o.shrinks.front().is_some_and(|x| x.0 < vt / 1_000 - 50) {
                                        o.shrinks.pop_front();
                                    }
                                }
                                if s.prorata && delta < 0 {
                                    // Cancels are spread evenly through the queue: our share of
                                    // the shrink is the fraction of the level ahead of us. Live
                                    // fills showed 41% of the queue ahead clears by cancels.
                                    let before = level - delta;
                                    if before > 0 {
                                        o.queue_ahead = (o.queue_ahead as i128 * level as i128 / before as i128) as i64;
                                    }
                                }
                                o.queue_ahead = o.queue_ahead.min(level);
                            }
                        }
                    }
                    let t = book.touch();
                    if let (Some(b), Some(a)) = (t.yes_bid_fp, t.yes_ask_fp) {
                        let mid = (b + a) as f64 / 200.0;
                        if mk.mids.back().is_none_or(|(_, x)| *x != mid) {
                            mk.mids.push_back((vt, mid));
                            tape_dropped += tape_tx.try_send(format!(
                                "B,{now},{ticker},{b},{},{a},{},{vt}\n",
                                t.yes_bid_size_fp.unwrap_or(0), t.yes_ask_size_fp.unwrap_or(0))).is_err() as u64;
                        }
                        while mk.mids.len() > 2 && mk.mids[1].0 < vt - 2_000_000 {
                            mk.mids.pop_front();
                        }
                    }
                }
                "trade" => {
                    let taker_yes = m["taker_side"].as_str() == Some("yes");
                    let price = parse_value(m.get("yes_price_dollars"), PRICE_SCALE).unwrap_or(-1);
                    let count = parse_value(m.get("count_fp"), SIZE_SCALE).unwrap_or(0);
                    tape_dropped += tape_tx.try_send(format!(
                        "T,{now},{ticker},{},{price},{},,{vt}\n",
                        if taker_yes { "yes" } else { "no" }, count as f64 / SIZE_SCALE as f64)).is_err() as u64;
                    let i = if taker_yes { ASK } else { BID };
                    for s in &mut strategies {
                        let Some(seat) = s.seats.get_mut(ticker) else { continue };
                        let Some(o) = seat.orders[i].as_mut() else { continue };
                        if !o.live {
                            continue;
                        }
                        // A taker walking THROUGH our price must have taken our level entirely.
                        let through = if taker_yes { price > o.price } else { price < o.price };
                        let mut q_fill = o.queue_ahead;
                        let fill = if through {
                            o.remaining
                        } else if price == o.price {
                            // Undo the clamp of this trade's own fill delta(s), if they came first.
                            let ms = vt / 1_000;
                            let mut matched = 0;
                            let mut restored = None;
                            o.shrinks.retain(|(t, amt, q_before)| {
                                if matched < count && (t - ms).abs() <= 2 {
                                    matched += amt;
                                    restored.get_or_insert(*q_before);
                                    false
                                } else {
                                    true
                                }
                            });
                            if let Some(q) = restored {
                                o.queue_ahead = q;
                            }
                            q_fill = o.queue_ahead;
                            let f = (count - o.queue_ahead).clamp(0, o.remaining);
                            o.queue_ahead = (o.queue_ahead - count).max(0);
                            f
                        } else {
                            0
                        };
                        if fill > 0 {
                            o.remaining -= fill;
                            let px_c = o.price as f64 / 100.0;
                            let ct = fill as f64 / SIZE_SCALE as f64;
                            if i == BID {
                                seat.pos_fp += fill;
                                seat.cash_c -= px_c * ct;
                            } else {
                                seat.pos_fp -= fill;
                                seat.cash_c += px_c * ct;
                            }
                            s.fills += 1;
                            s.filled_fp += fill;
                            let _ = tape_tx.send(format!(
                                "F,{now},{ticker},{},{},{},{ct},{vt},{},{}\n",
                                s.name, if i == BID { "bid" } else { "ask" }, o.price,
                                o.q_join as f64 / SIZE_SCALE as f64, q_fill as f64 / SIZE_SCALE as f64));
                            if o.remaining == 0 {
                                seat.orders[i] = None;
                            }
                        }
                    }
                }
                _ => {}
            }

            // 3. Decide quotes at our receipt time `now`, from what we know now.
            let t = book.touch();
            let (Some(bid), Some(ask)) = (t.yes_bid_fp, t.yes_ask_fp) else { continue };
            let (bsz, asz) = (t.yes_bid_size_fp.unwrap_or(0), t.yes_ask_size_fp.unwrap_or(0));
            let mid = (bid + ask) as f64 / 200.0;
            let mom = mk.mid_at(vt - 1_000_000).map(|m0| mid - m0).unwrap_or(0.0);
            let tot = (bsz + asz).max(1) as f64;
            let in_band = mid >= p.mid_lo_c && mid <= p.mid_hi_c;
            let open_long = mk.close_unix_ms == 0
                || (mk.close_unix_ms - now / 1_000) > p.stop_before_close_s * 1_000;
            for s in &mut strategies {
                // Token bucket refill.
                s.tokens = (s.tokens + (now - s.last_refill_us) as f64 / 1e6 * p.tokens_per_s).min(p.bucket);
                s.last_refill_us = now;
                let seat = s.seats.entry(ticker.to_owned()).or_default();
                for i in [BID, ASK] {
                    let mut want = in_band && open_long;
                    if i == BID && seat.pos_fp >= p.max_pos_fp { want = false; }
                    if i == ASK && seat.pos_fp <= -p.max_pos_fp { want = false; }
                    if want && s.gate {
                        // Pull the side a toxic taker would hit: mid running into it, or it is
                        // nearly empty (about to be cleared).
                        let toxic = if i == ASK {
                            mom > p.mom_pull_c || bsz as f64 / tot > p.thin_pull
                        } else {
                            -mom > p.mom_pull_c || asz as f64 / tot > p.thin_pull
                        };
                        if toxic {
                            want = false;
                            if seat.orders[i].as_ref().is_some_and(|o| o.cancel_at.is_none()) {
                                s.pulls += 1;
                            }
                        }
                    }
                    let touch_px = if i == BID { bid } else { ask };
                    let mut target = touch_px;
                    debug_assert!(!(s.front && s.improve), "front sizes the touch level only");
                    if s.improve {
                        // The shadow book never contains our own orders, so `bid`/`ask` are the
                        // other makers' best. Improve both sides from 3 ticks; at 2 ticks improve
                        // only the side that reduces inventory (both would meet at the mid).
                        let room = (ask - bid) / p.tick;
                        let reduces = if i == BID { seat.pos_fp < 0 } else { seat.pos_fp > 0 };
                        if room >= 3 || (room == 2 && reduces) {
                            target = if i == BID { bid + p.tick } else { ask - p.tick };
                        }
                    }
                    let current = seat.orders[i].as_ref().filter(|o| o.cancel_at.is_none())
                        .map(|o| o.amend.map_or(o.price, |(px, _)| px));
                    // Improving: a maker joining OUR price makes it the touch; we are still ahead
                    // of them, so staying beats re-improving over our own queue.
                    let keep = want && (current == Some(target)
                        || (s.improve && target != touch_px && current == Some(touch_px)
                            && seat.orders[i].as_ref().is_some_and(|o| o.live)));
                    if s.amend && want && !keep && current.is_some() {
                        if s.tokens >= 10.0 {
                            s.tokens -= 10.0;
                            s.amends += 1;
                            if let Some(o) = seat.orders[i].as_mut() {
                                o.amend = Some((target, now + p.amend_us));
                            }
                        } else {
                            s.throttled += 1;
                        }
                        continue;
                    }
                    if current.is_some() && !keep {
                        if s.tokens >= 2.0 {
                            s.tokens -= 2.0;
                            s.cancels += 1;
                            if let Some(o) = seat.orders[i].as_mut() {
                                o.cancel_at = Some(now + p.cancel_us);
                            }
                        } else {
                            s.throttled += 1;
                            continue;
                        }
                    }
                    let slot_free = seat.orders[i].as_ref().is_none_or(|o| o.cancel_at.is_some());
                    if want && !keep && slot_free {
                        // One order per side: wait for the old one's cancel to land.
                        if seat.orders[i].is_some() {
                            continue;
                        }
                        // `front` never improves, so its target is the touch and the level is bsz/asz.
                        if s.front && (if i == BID { bsz } else { asz }) > p.max_join_fp {
                            s.skipped += 1;
                            continue;
                        }
                        if s.tokens >= 10.0 {
                            s.tokens -= 10.0;
                            s.posts += 1;
                            seat.orders[i] = Some(SimOrder {
                                price: target, live_at: now + p.create_us, cancel_at: None,
                                live: false, queue_ahead: 0, q_join: 0, remaining: p.size_fp,
                                shrinks: VecDeque::new(), amend: None,
                            });
                        } else {
                            s.throttled += 1;
                        }
                    }
                }
            }
            handle_us.push(t_recv.elapsed().as_micros() as u64);
        }
        tokio::time::sleep(Duration::from_millis(250)).await;
    }
    for s in &strategies {
        for (t, seat) in &s.seats {
            let _ = tape_tx.send(format!("S,{},{t},{},{},{:.4},,\n", unix_us(), s.name, seat.pos_fp, seat.cash_c));
        }
    }
    disc.abort();
    drop(tape_tx);
    tape_thread.join().map_err(|_| anyhow::anyhow!("tape thread panicked"))??;
    Ok(())
}
