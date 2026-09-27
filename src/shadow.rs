//! Shadow market maker: live feed, simulated orders, NO real orders.
//!
//! Each strategy joins the touch on both sides of every mid-band 15M crypto market, one contract
//! per side, with an inventory cap. Simulated orders pay our MEASURED latencies (create 5.44 ms,
//! cancel 4.43 ms from decision to the venue book, 2026-09-23) and join the BACK of the displayed
//! queue. Queue ahead shrinks only when prints at our price trade through it, or when the level
//! shrinks below it (cancels ahead of us are otherwise assumed to be behind us: conservative).
//!
//! Output: a tape (same format as `probe`) plus `F` fill rows, for offline settlement P&L.

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
    pub series: Vec<String>,
    /// Run only the frozen `base` rule, so it gets the whole write budget (PREREG_btc_focus.md).
    pub only_base: bool,
    /// Run the controlled penny4 clip-capacity experiment: touch control plus 1, 3 and 10
    /// contract clips, each with an absolute-position cap equal to its clip.
    pub clip_ladder: bool,
    /// Spot gate lookback: the Coinbase mid's move over this window decides a spot pull.
    pub spot_window_us: i64,
    /// Sports joins the touch at an absolute cent spread, without crypto gates.
    pub sports_min_spread_c: Option<i64>,
    /// Sports: admit only tickers containing `-<tag>` (e.g. a game date like 26SEP26).
    pub sports_tag: Option<String>,
}

impl Default for Params {
    fn default() -> Self {
        Self {
            create_us: 5_440,
            cancel_us: 4_430,
            amend_us: 4_600,
            size_fp: SIZE_SCALE,
            max_pos_fp: SIZE_SCALE, // same as the live engine
            mid_lo_c: 15.0,
            mid_hi_c: 85.0,
            stop_before_close_s: 120,
            mom_pull_c: 0.25,
            thin_pull: 0.9213,
            tokens_per_s: 300.0,
            bucket: 900.0,
            series: Vec::new(),
            only_base: false,
            clip_ladder: false,
            spot_window_us: 1_000_000,
            sports_min_spread_c: None,
            sports_tag: None,
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
    /// Average price of the open (unpaired) inventory, for the break-even pairing clamp.
    entry_fp: i64,
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
    /// How the PAIRING side (the one that flattens inventory) quotes:
    /// 0 = like any side (gated, join); 1 = join, ungated; 2 = one tick inside, ungated, never
    /// through break-even; 3 = one tick inside, ungated, no break-even floor.
    pair: u8,
    /// 0 = join the touch (mid band only). n > 0 = PENNY: quote one tick inside, on any price band,
    /// only when the spread is at least n ticks (PREREG_penny.md).
    penny_room: i64,
    /// Spot gate: pull the side a Coinbase move of more than this many bps (over
    /// `spot_window_us`) runs into, both on the spot tick itself and at every later decision.
    /// 0 = off. The Kalshi mid lags spot (the fast makers react to spot, 2026-09-23 side analysis).
    spot_bps: f64,
    spot_pulls: u64,
    clip_fp: i64,
    max_pos_fp: i64,
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
        writeln!(gz, "kind,recv_us,ticker,a,b,c,d,venue_ms")?;
        for line in tape_rx {
            gz.write_all(line.as_bytes())?;
        }
        gz.finish()?.flush()?;
        Ok(())
    });

    let now0 = unix_us();
    let specs: Vec<(&'static str, bool, bool, bool, u8, i64, f64, i64)> = if p.clip_ladder {
        vec![
            ("touch_c1", true, false, true, 0, 0, 0.0, 1),
            ("p4_c1", true, false, true, 0, 4, 0.0, 1),
            ("p4_c3", true, false, true, 0, 4, 0.0, 3),
            ("p4_c10", true, false, true, 0, 4, 0.0, 10),
        ]
    } else {
        vec![
            ("base", true, false, true, 0, 0, 0.0, 1),
            ("penny3", true, false, true, 0, 3, 0.0, 1),
            // penny4 is the live rule (run 9, 2026-09-26). Its spot-gated and amend variants:
            ("penny4", true, false, true, 0, 4, 0.0, 1),
            ("p4_am", true, true, true, 0, 4, 0.0, 1),
            ("p4_s1", true, false, true, 0, 4, 1.0, 1),
            ("p4_s2", true, false, true, 0, 4, 2.0, 1),
            ("p4_s4", true, false, true, 0, 4, 4.0, 1),
            ("p4_am_s2", true, true, true, 0, 4, 2.0, 1),
        ]
    };
    let mut strategies: Vec<Strategy> = specs
        .into_iter()
        .map(|(name, gate, amend, prorata, pair, penny_room, spot_bps, clip_multiple)| {
            let clip_fp = p.size_fp * clip_multiple;
            Strategy {
                name, gate, amend, prorata, pair, penny_room, spot_bps, spot_pulls: 0,
                clip_fp, max_pos_fp: if p.clip_ladder { clip_fp } else { p.max_pos_fp },
                amends: 0, seats: HashMap::new(), tokens: p.bucket, last_refill_us: now0,
                posts: 0, cancels: 0, throttled: 0, pulls: 0, fills: 0, filled_fp: 0,
            }
        })
        .filter(|s: &Strategy| !p.only_base || s.name == "base")
        .collect();

    if p.sports_min_spread_c.is_some() {
        strategies.retain(|s| s.name == "base");
        for s in &mut strategies {
            s.name = "sports_join";
            s.gate = false;
            // Do not assume cancellations ahead of us. Trades and level clamps only.
            s.prorata = false;
        }
        fs::write(out.join(format!("sports_run_{stamp}.json")), serde_json::to_vec_pretty(&serde_json::json!({
            "mode":"shadow", "strategy":"sports_join", "series":p.series,
            "min_spread_c":p.sports_min_spread_c, "tag":p.sports_tag, "create_us":p.create_us,
            "cancel_us":p.cancel_us, "size_fp":p.size_fp, "max_pos_fp":p.max_pos_fp,
            "stop_before_close_s":p.stop_before_close_s,
            "fees_included":false, "game_state_feed":false,
            "note":"Research only. Cash and fills are simulated and gross of fees. Close time is not game start."
        }))?)?;
    }

    let (disc_tx, mut disc_rx) = mpsc::channel(256);
    let disc = if p.sports_min_spread_c.is_some() {
        tokio::spawn(crate::sports::discover(disc_tx, p.series.clone(), out.clone(), p.sports_tag.clone()))
    } else {
        tokio::spawn(discover(disc_tx, p.series.clone()))
    };
    let (spot_tx, mut spot_rx) = mpsc::channel::<SpotTick>(65_536);
    let spot_feed = if p.sports_min_spread_c.is_none() {
        Some(tokio::spawn(spot_feed(spot_tx, p.series.clone())))
    } else { None };
    // Per series: (our receipt µs, Coinbase mid in dollars), trimmed to the last 10 s.
    let mut spot: HashMap<String, VecDeque<(i64, f64)>> = HashMap::new();
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
                sp = spot_rx.recv(), if spot_feed.is_some() => {
                    let Some(sp) = sp else { continue };
                    let _ = tape_tx.try_send(format!("X,{},{},{:.6},,,,{}\n", sp.recv_us, sp.series, sp.mid, sp.exch_ms));
                    let h = spot.entry(sp.series.clone()).or_default();
                    h.push_back((sp.recv_us, sp.mid));
                    while h.front().is_some_and(|(t, _)| *t < sp.recv_us - 10_000_000) { h.pop_front(); }
                    let Some(r) = spot_ret_bps(h, sp.recv_us, p.spot_window_us) else { continue };
                    // Act on the spot tick itself: cancel the side the move runs into, now, not at
                    // the next Kalshi message (which is what lags).
                    let side = if r > 0.0 { ASK } else { BID };
                    let prefix = format!("{}-", sp.series);
                    for s in &mut strategies {
                        if s.spot_bps <= 0.0 || r.abs() <= s.spot_bps { continue; }
                        s.tokens = (s.tokens + (sp.recv_us - s.last_refill_us) as f64 / 1e6 * p.tokens_per_s).min(p.bucket);
                        s.last_refill_us = sp.recv_us;
                        for (t, seat) in s.seats.iter_mut() {
                            if !t.starts_with(&prefix) { continue; }
                            let Some(o) = seat.orders[side].as_mut() else { continue };
                            if o.cancel_at.is_some() { continue; }
                            if s.tokens < 2.0 { s.throttled += 1; continue; }
                            s.tokens -= 2.0;
                            s.cancels += 1;
                            s.spot_pulls += 1;
                            o.cancel_at = Some(sp.recv_us + p.cancel_us);
                        }
                    }
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
                        eprintln!("[{}s] {:10} clip={:.0} posts={} amends={} cancels={} pulls={} spot_pulls={} throttled={} fills={} filled_ct={:.0} open_abs_pos_ct={:.0}",
                            started.elapsed().as_secs(), s.name, s.clip_fp as f64 / SIZE_SCALE as f64,
                            s.posts, s.amends, s.cancels, s.pulls, s.spot_pulls, s.throttled,
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
                    }
                }
            }

            // 2. Apply the event.
            match kind {
                "orderbook_delta" => {
                    let previous_touch = book.touch();
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
                        }
                        // Size changes and symmetric price changes matter to queue research
                        // even when the midpoint is unchanged. Keep midpoint history sparse,
                        // but record every change to the best prices or displayed sizes.
                        if t != previous_touch {
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
                        // A taker walking through our price can consume at most its observed size.
                        // This distinction is immaterial for most 1-ct quotes but critical at c10.
                        let through = if taker_yes { price > o.price } else { price < o.price };
                        let fill = if through {
                            through_fill(o.remaining, count)
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
                            let before = seat.pos_fp;
                            let signed = if i == BID { fill } else { -fill };
                            if i == BID {
                                seat.pos_fp += fill;
                                seat.cash_c -= px_c * ct;
                            } else {
                                seat.pos_fp -= fill;
                                seat.cash_c += px_c * ct;
                            }
                            // Entry of the open inventory: set on opening, averaged on adding,
                            // unchanged on reducing.
                            if before == 0 || (before > 0) != (seat.pos_fp > 0) {
                                seat.entry_fp = o.price;
                            } else if before.signum() == signed.signum() {
                                seat.entry_fp = (seat.entry_fp * before.abs() + o.price * fill) / seat.pos_fp.abs();
                            }
                            s.fills += 1;
                            s.filled_fp += fill;
                            let _ = tape_tx.send(format!(
                                "F,{now},{ticker},{},{},{},{ct},{vt}\n",
                                s.name, if i == BID { "bid" } else { "ask" }, o.price));
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
            let series = ticker.split('-').next().unwrap_or("");
            let spot_r = spot.get(series).and_then(|h| spot_ret_bps(h, now, p.spot_window_us));
            for s in &mut strategies {
                // Token bucket refill.
                s.tokens = (s.tokens + (now - s.last_refill_us) as f64 / 1e6 * p.tokens_per_s).min(p.bucket);
                s.last_refill_us = now;
                let seat = s.seats.entry(ticker.to_owned()).or_default();
                for i in [BID, ASK] {
                    let pairing = (i == ASK && seat.pos_fp > 0) || (i == BID && seat.pos_fp < 0);
                    let pair_mode = if pairing { s.pair } else { 0 };
                    // Venue tick is tapered: 0.1 c in the wings, 1 c in the middle.
                    let side_px = if i == BID { bid } else { ask };
                    let tick: i64 = if side_px < 1_000 || side_px > 9_000 { 10 } else { 100 };
                    let mut want = if s.penny_room > 0 {
                        open_long && ask - bid >= s.penny_room * tick && mid > 1.0 && mid < 99.0
                    } else {
                        in_band && open_long
                    };
                    if let Some(width) = p.sports_min_spread_c {
                        want = crate::sports::quote_allowed(bid, ask, width, mk.close_unix_ms, now / 1000, p.stop_before_close_s);
                    }
                    if pair_mode > 0 {
                        // Keep trying to flatten until 20 s before close, in or out of band.
                        want = mk.close_unix_ms == 0 || mk.close_unix_ms - now / 1_000 > 20_000;
                    }
                    if i == BID && seat.pos_fp + s.clip_fp > s.max_pos_fp && !pairing { want = false; }
                    if i == ASK && seat.pos_fp - s.clip_fp < -s.max_pos_fp && !pairing { want = false; }
                    if want && s.gate && pair_mode == 0 {
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
                    // Spot up = YES worth more: our ask is the stale side. Spot down: our bid.
                    if want && s.spot_bps > 0.0 && pair_mode == 0 {
                        if let Some(r) = spot_r {
                            if (i == ASK && r > s.spot_bps) || (i == BID && r < -s.spot_bps) { want = false; }
                        }
                    }
                    let mut target = if i == BID { bid } else { ask };
                    if s.penny_room > 0 && pair_mode == 0 {
                        target = if i == BID { bid + tick } else { ask - tick };
                    }
                    if pair_mode >= 2 {
                        const TICK: i64 = 100; // 1c: mid band only
                        target = if i == ASK {
                            if ask - TICK > bid { ask - TICK } else { ask }
                        } else if bid + TICK < ask { bid + TICK } else { bid };
                        if pair_mode == 2 {
                            target = if i == ASK { target.max(seat.entry_fp) } else { target.min(seat.entry_fp) };
                        }
                    }
                    let current = seat.orders[i].as_ref().filter(|o| o.cancel_at.is_none())
                        .map(|o| o.amend.map_or(o.price, |(px, _)| px));
                    let keep = want && current == Some(target);
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
                        if s.tokens >= 10.0 {
                            s.tokens -= 10.0;
                            s.posts += 1;
                            seat.orders[i] = Some(SimOrder {
                                price: target, live_at: now + p.create_us, cancel_at: None,
                                live: false, queue_ahead: 0,
                                // A pairing order only ever closes what is open (fractional fills).
                                remaining: if pairing { s.clip_fp.min(seat.pos_fp.abs()) } else { s.clip_fp },
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
    if let Some(feed) = spot_feed { feed.abort(); }
    drop(tape_tx);
    tape_thread.join().map_err(|_| anyhow::anyhow!("tape thread panicked"))??;
    Ok(())
}

pub struct SpotTick {
    pub series: String,
    pub recv_us: i64,
    pub mid: f64,
    pub exch_ms: i64,
}

/// Coinbase mid move, in bps, from `window_us` ago to now. None when the feed is stale (no tick
/// in the last 3 s) or has no history that far back: a dead feed must never gate or un-gate.
pub fn spot_ret_bps(h: &VecDeque<(i64, f64)>, now: i64, window_us: i64) -> Option<f64> {
    let &(t_last, last) = h.back()?;
    if now - t_last > 3_000_000 { return None; }
    let &(_, then) = h.iter().rev().find(|(t, _)| *t <= now - window_us)?;
    Some((last / then - 1.0) * 1e4)
}

fn through_fill(remaining: i64, observed_count: i64) -> i64 {
    remaining.min(observed_count.max(0))
}

/// Coinbase Exchange public `ticker` feed (no auth; market data only, no trading): one message
/// per match with the best bid/ask after it. KXBTC15M → BTC-USD.
pub async fn spot_feed(tx: mpsc::Sender<SpotTick>, series: Vec<String>) -> Result<()> {
    let product = |s: &str| format!("{}-USD", s.trim_start_matches("KX").trim_end_matches("15M"));
    let by_product: HashMap<String, String> = series.iter().map(|s| (product(s), s.clone())).collect();
    loop {
        let (mut ws, _) = match connect_async("wss://ws-feed.exchange.coinbase.com").await {
            Ok(x) => x,
            Err(e) => { eprintln!("spot ws connect: {e}"); tokio::time::sleep(Duration::from_secs(1)).await; continue; }
        };
        let sub = serde_json::json!({"type": "subscribe", "product_ids": by_product.keys().collect::<Vec<_>>(), "channels": ["ticker"]});
        if ws.send(Message::Text(sub.to_string().into())).await.is_err() { continue; }
        while let Some(msg) = ws.next().await {
            let text = match msg {
                Ok(Message::Text(t)) => t,
                Ok(Message::Ping(x)) => { let _ = ws.send(Message::Pong(x)).await; continue; }
                Ok(_) => continue,
                Err(e) => { eprintln!("spot ws error: {e}"); break; }
            };
            let recv_us = unix_us();
            let Ok(v) = serde_json::from_str::<Value>(&text) else { continue };
            if v["type"] == "error" { eprintln!("spot feed error: {v}"); continue; }
            if v["type"] != "ticker" { continue; }
            let Some(series) = v["product_id"].as_str().and_then(|p| by_product.get(p)) else { continue };
            let px = |k: &str| v[k].as_str().and_then(|x| x.parse::<f64>().ok());
            let (Some(b), Some(a)) = (px("best_bid"), px("best_ask")) else { continue };
            let exch_ms = v["time"].as_str().and_then(rfc3339_us).map_or(0, |u| u / 1_000);
            if tx.try_send(SpotTick { series: series.clone(), recv_us, mid: (a + b) / 2.0, exch_ms }).is_err() {
                // The main loop is behind; a dropped spot tick only delays a pull.
            }
        }
        tokio::time::sleep(Duration::from_millis(500)).await;
    }
}

#[cfg(test)]
mod tests {
    use std::collections::VecDeque;

    #[test]
    fn spot_return_needs_fresh_history() {
        let h: VecDeque<(i64, f64)> = [(0, 100.0), (500_000, 100.01), (1_200_000, 100.03)].into();
        // 1 s back from 1.2 s is the tick at 0: +3 bps.
        let r = super::spot_ret_bps(&h, 1_200_000, 1_000_000).unwrap();
        assert!((r - 3.0).abs() < 1e-9, "{r}");
        // Not enough history for a 2 s window.
        assert!(super::spot_ret_bps(&h, 1_200_000, 2_000_000).is_none());
        // Feed silent for more than 3 s: no gate either way.
        assert!(super::spot_ret_bps(&h, 5_000_000, 1_000_000).is_none());
    }

    #[test]
    fn through_price_fill_is_bounded_by_observed_taker_size() {
        assert_eq!(super::through_fill(1_000, 250), 250);
        assert_eq!(super::through_fill(1_000, 2_000), 1_000);
        assert_eq!(super::through_fill(1_000, -1), 0);
    }
}
