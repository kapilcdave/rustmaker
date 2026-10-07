//! ARMED market maker. Places REAL orders. Same quoting rule as `shadow` (gate: join the touch
//! on both sides of mid-band markets, pull a side when 1 s momentum runs into it or it is nearly
//! empty), clip `--clip` (default 1), |position| <= max_pos per market, flat-or-hold to settlement (never crosses).
//!
//! Purpose of the first runs: measure REAL fills — in particular how much of the queue ahead of
//! us at join time clears by CANCELS rather than trades (the shadow assumed none did).
//!
//! Guardrails (each one a past incident in this repo's memory):
//! - orders keyed by client_order_id, never by ticker;
//! - position from the venue's own fill messages (`post_position_fp`), not from our arithmetic;
//! - session loss cap on the engine's OWN fill ledger marked to mid (a pair is worth exactly 100
//!   at any mid — venue position fields net pairs away), PLUS a cumulative cap anchored to a
//!   venue baseline persisted across restarts in `live_state.json`;
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
use tokio_tungstenite::tungstenite::Message;

use crate::{
    auth::Auth,
    book::{Book, PRICE_SCALE, SIZE_SCALE, parse_value},
    client, rest_base, rfc3339_us, signed_ws_request,
    stats::Samples,
    unix_us,
};

/// Per-interval cap for the stage timers. Reset every housekeeping tick, so this only has to
/// hold one interval's frames, not a whole session.
const TIMING_CAP: usize = 50_000;

pub struct LiveParams {
    pub series: Vec<String>,
    pub minutes: u64,
    pub max_pos_fp: i64,
    /// Contracts per order (SIZE_SCALE units). |position| + clip must stay within max_pos.
    pub clip_fp: i64,
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
    /// Join one 15-minute crypto touch only when it remains favorable versus the receipt-clock
    /// midpoint from one second ago. This is the book-only residual control.
    pub book_residual: bool,
    /// No order that would OPEN or ADD to a position this close to the market's close; orders
    /// that reduce the position keep quoting until `stop_before_close_s`. 450 s cut live
    /// leftovers 126 → 15 ct over 197 markets (`leftover_rules.py`, 2026-09-25).
    pub open_cutoff_s: i64,
    /// Requote an untouched resting order with one amend (same order_id, measured 2026-09-26)
    /// instead of cancel + create.
    pub amend: bool,
    /// Pull the side a Coinbase move of more than this many bps over 1 s runs into, on the spot
    /// tick itself. 0 = off.
    pub spot_bps: f64,
    /// Cap on the ROUND's directional bet: the summed YES position across every market closing
    /// at the same time (the crypto series move together, so it is one bet). A side that would
    /// push |sum| past this is not quoted; sides that shrink it always are. 0 = off. Replaying
    /// all 11 live penny journals (`leftover_portfolio.py`, 2026-09-28) at 2 ct: +19 +/- 7 c/round
    /// vs base, max drawdown $9.96 -> $3.6, robust to 5 s of fill-knowledge lag.
    pub max_round_net_fp: i64,
    /// Opening/adding orders only at a YES price in [open_lo_c, open_hi_c); orders that reduce the
    /// position quote at any price. Wing fills (<10c, >90c) were 60% of 2,960 capped live fills
    /// and settled at ~0 c/ct in both halves (2026-09-30), only adding variance. 0/100 = off.
    pub open_lo_c: f64,
    pub open_hi_c: f64,
    /// Sports mode: many markets per series, game-level pauses off the parent (full-game) books,
    /// per-game and total worst-case contract caps. None = the 15M crypto engine.
    pub sports: Option<SportsLive>,
}

pub struct SportsLive {
    /// Only tickers containing `-<tag>` for one of these (game dates like 26SEP27).
    pub tags: Vec<String>,
    /// Full-game series watched as a score feed and never quoted (e.g. KXNCAAFGAME,KXNCAAFTOTAL).
    pub parents: Vec<String>,
    /// Quote only when the OTHERS' spread is at least this wide (cents), one tick inside it.
    pub min_spread_c: i64,
    /// Pause a game when any of its parent books' mid moves this much (cents) within the window.
    pub parent_move_c: f64,
    pub parent_window_us: i64,
    /// Pause a game when one of its quoted books' mid jumps this much (cents) within 2 s.
    pub jump_c: f64,
    pub pause_us: i64,
    /// Worst-case contracts per game / in total, assuming every resting order of one side fills
    /// (a score sweeps a whole strike ladder the same way).
    pub max_game_fp: i64,
    pub max_total_fp: i64,
    pub max_markets: usize,
    pub refresh_s: u64,
    /// Go one tick inside when the others' spread is at least this (cents); below it, join.
    pub penny_min_c: i64,
    /// Open only on books at most this wide (cents): a fill on a wider book is a directional
    /// entry with no realistic second leg.
    pub max_open_spread_c: i64,
    /// Round trip: after an opening fill, only the flattening side quotes, at entry +/- this
    /// edge (cents) until `scratch_us`, then at the entry price until `bail_us`, then at the
    /// competitive price whatever it costs. Always post-only; never crosses.
    pub exit_edge_c: i64,
    pub scratch_us: i64,
    pub bail_us: i64,
    /// `--series auto`: discover across every fee-free sports series (this set), in the
    /// background, ranked by 24h volume; tags are the rolling UTC dates (yesterday..tomorrow).
    pub auto_series: Option<std::collections::HashSet<String>>,
    pub min_v24: f64,
}

/// --dry-run: no order-group create, no order, amend or cancel is sent; acks are synthesized
/// locally so the whole decision path (discovery, pauses, caps) runs against the live feed.
pub static DRY: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);
fn dry() -> bool { DRY.load(std::sync::atomic::Ordering::Relaxed) }

const BID: usize = 0;
const ASK: usize = 1;

fn book_residual_allows(side: usize, target_fp: i64, anchor_c: f64) -> bool {
    if side == BID { anchor_c >= target_fp as f64 / 100.0 }
    else { target_fp as f64 / 100.0 >= anchor_c }
}

fn opening_deadline_ok(remaining: Duration, cutoff_s: i64) -> bool {
    cutoff_s <= 0 || remaining > Duration::from_secs(cutoff_s as u64)
}

fn open_band_ok(target_fp: i64, lo_c: f64, hi_c: f64) -> bool {
    let c = target_fp as f64 / 100.0;
    c >= lo_c && c < hi_c
}

fn nonnegative_pair_price(position_fp: i64, side: usize, target_fp: i64, entry_fp: i64) -> bool {
    if position_fp > 0 && side == ASK { target_fp >= entry_fp }
    else if position_fp < 0 && side == BID { target_fp <= entry_fp }
    else { true }
}

#[derive(Clone, Copy, PartialEq, Debug)]
enum St {
    PendingNew,
    Resting,
    PendingCancel,
    /// Amend in flight: still resting at `price` until the ack.
    PendingAmend,
}

struct LiveOrder {
    coid: String,
    order_id: Option<String>,
    ticker: String,
    side: usize,
    price: i64,
    st: St,
    /// Unfilled size resting at the venue, from `remaining_count_fp`. A half-filled clip-2 order
    /// holds 1 ct at our price, and only that much is ours to remove when reading the others' book.
    remaining_fp: i64,
}

/// Would a clip on this side push the round's summed YES position past the cap? A side that
/// shrinks |sum| is never blocked, so a round over the cap can always work its way back.
fn round_cap_blocks(round_net_fp: i64, is_bid: bool, clip_fp: i64, cap_fp: i64) -> bool {
    let after = round_net_fp + if is_bid { clip_fp } else { -clip_fp };
    after.abs() > cap_fp && after.abs() > round_net_fp.abs()
}

#[derive(Default)]
struct Mkt {
    book: Option<Book>,
    close_unix_ms: i64,
    mids: VecDeque<(i64, f64)>,
    receipt_mids: VecDeque<(i64, f64)>,
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
    /// The venue's price grid, (start, end, step) in PRICE_SCALE units, from `price_ranges`.
    /// Crypto and GOLD/SILVER/WTI step 0.1c below 10c and above 90c; COPPER/NATGAS step 1c
    /// everywhere, so the wing tick must never be assumed.
    ranges: Vec<(i64, i64, i64)>,
    /// Sports: the game key (ticker's second token) and whether this is a watched parent book.
    game: String,
    parent: bool,
    /// Sports: opening fill price (PRICE_SCALE, YES terms) and time, for the flattening quote.
    entry_px: i64,
    entry_us: i64,
    /// Others' touch in cents, for a mark at the price we could exit at, not the mid.
    last_bid: f64,
    last_ask: f64,
    conservative: bool,
}

impl Mkt {
    fn mtm_c(&self) -> f64 {
        if self.conservative && self.last_bid > 0.0 && self.last_ask > 0.0 {
            // A YES+NO pair is exactly 100; the unpaired rest is marked where it could be sold.
            let pairs = self.yes_ct.min(self.no_ct);
            return self.flow_c + pairs * 100.0 + (self.yes_ct - pairs) * self.last_bid
                + (self.no_ct - pairs) * (100.0 - self.last_ask);
        }
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
    Amended { coid: String, price: i64, res: Result<Value> },
}

/// Gzipped JSONL. A 3 h, 9-series run wrote 431 MB uncompressed (the box has ~3 GB free);
/// gzip -1 made it 55 MB. `flush` is a gzip SYNC flush, so a crash loses at most one
/// housekeeping interval and the file stays readable up to the last flush.
struct Journal(BufWriter<GzEncoder<File>>, Samples);

impl Journal {
    /// Timed, because this is a synchronous serde + gzip deflate running on the decide loop, and
    /// `j.row("new", ..)` is the last statement before the `tokio::spawn` that POSTs an order.
    /// If `journal_us` turns out to be material, move it to a writer thread the way `probe`
    /// ("gzip must never sit in the receive path") and `shadow`'s Recorder already do.
    fn row(&mut self, kind: &str, v: Value) {
        let t = Instant::now();
        let _ = writeln!(self.0, "{}", json!({"k": kind, "t": unix_us(), "v": v}));
        self.1.push(t.elapsed().as_micros() as i64, 1.0);
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

/// Stage timings for one WS frame, reset every housekeeping tick.
///
/// Until 2026-10-06 `live` took **no monotonic clock reading per message at all** — a single
/// `unix_us()` wall-clock stamp and nothing after it — so every stage below was invisible and
/// "where does the decision path spend its time" was unanswerable. The order round trip itself is
/// already known (`rtt`/`latency` subcommands, and reconstructible offline from the journal's `new`
/// -> `ack_new` rows); what was missing is everything *before* the send.
///
/// Pure instrumentation: nothing here changes a quoting decision.
struct Timing {
    /// Venue `ts` -> our receipt. The ONLY series here that compares two clocks, so it inherits
    /// the chrony offset (RMS 8.4 us, root delay 330 us on 2026-10-06) plus the venue's own
    /// unknown clock bias. Treat the shape and config-to-config deltas as real, the absolute
    /// level as offset by an unknown constant.
    feed_age_us: Samples,
    /// `serde_json::from_str` on the frame.
    parse_us: Samples,
    /// The two O(markets) scans: `exposure_elsewhere` and `round_other`.
    scan_us: Samples,
    /// Delta handling: `apply_delta` + both `touch()` recomputes. INCLUDES the `own_delta`/`B`
    /// journal rows written inside that block — subtract `journal_us` to split them.
    book_us: Samples,
    /// Receipt -> reaching the `---- decide ----` marker.
    pre_decide_us: Samples,
    /// Receipt -> the `tokio::spawn` that POSTs a new order. The end of everything we control.
    to_spawn_us: Samples,
}

impl Timing {
    fn new() -> Self {
        Self {
            feed_age_us: Samples::new(TIMING_CAP),
            parse_us: Samples::new(TIMING_CAP),
            scan_us: Samples::new(TIMING_CAP),
            book_us: Samples::new(TIMING_CAP),
            pre_decide_us: Samples::new(TIMING_CAP),
            to_spawn_us: Samples::new(TIMING_CAP),
        }
    }

    fn report(&self, journal_us: &Samples) -> Value {
        json!({
            "feed_age_us": self.feed_age_us.summary(&[]),
            "parse_us": self.parse_us.summary(&[]),
            "scan_us": self.scan_us.summary(&[]),
            "book_us": self.book_us.summary(&[]),
            "pre_decide_us": self.pre_decide_us.summary(&[]),
            "to_spawn_us": self.to_spawn_us.summary(&[]),
            "journal_us": journal_us.summary(&[]),
        })
    }

    /// p50 of a series, for the one-line status print.
    fn p50(s: &Samples) -> i64 {
        s.summary(&[])["quantiles"]["p50"].as_i64().unwrap_or(0)
    }
}

pub async fn run(auth: Auth, p: LiveParams) -> Result<String> {
    if p.clip_fp <= 0 || p.clip_fp > p.max_pos_fp {
        bail!("--clip must be > 0 and <= --max-pos (clip {} ct, max-pos {} ct)", p.clip_fp as f64 / SIZE_SCALE as f64, p.max_pos_fp as f64 / SIZE_SCALE as f64);
    }
    if p.book_residual {
        anyhow::ensure!(p.sports.is_none(), "--book-residual is only available in crypto live mode");
        anyhow::ensure!(p.series.len() == 1 && matches!(p.series[0].as_str(), "KXBTC15M" | "KXETH15M"),
            "--book-residual requires one supported series: KXBTC15M or KXETH15M");
        anyhow::ensure!(p.penny_room == 0, "--book-residual cannot be combined with --penny-room");
        anyhow::ensure!(p.spot_bps == 0.0, "--book-residual is the frozen book-only control; do not combine it with --spot-bps");
    }
    fs::create_dir_all(&p.out)?;
    let auth = Arc::new(auth);
    let http = client()?;
    let stamp = unix_us() / 1_000;
    let mut j = Journal(BufWriter::new(GzEncoder::new(
        OpenOptions::new().create(true).append(true).open(p.out.join(format!("live_{stamp}.jsonl.gz")))?,
        Compression::fast(),
    )), Samples::new(TIMING_CAP));
    let mut timing = Timing::new();

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
    eprintln!("ARMED. equity {:.2}c, cumulative baseline {:.2}c, session cap {:.0}c, cumulative cap {:.0}c, round net cap {}",
        equity0, baseline, p.session_max_loss_c, p.cumulative_max_loss_c,
        if p.max_round_net_fp > 0 { format!("{} ct", p.max_round_net_fp as f64 / SIZE_SCALE as f64) } else { "off".into() });
    j.row("start", json!({"equity_c": equity0, "baseline_c": baseline, "series": p.series,
        "book_residual": p.book_residual, "penny_room": p.penny_room, "spot_bps": p.spot_bps,
        "max_round_net_fp": p.max_round_net_fp, "open_lo_c": p.open_lo_c, "open_hi_c": p.open_hi_c}));

    // ---- venue-side kill switch ----
    let group = if dry() { json!({"order_group_id": "dry-run"}) } else { signed(&http, &auth, "POST", "/portfolio/order_groups/create",
        Some(&json!({"contracts_limit": p.group_contracts_per_15s, "exchange_index": p.exchange_index}))).await? };
    let mut group_id = group["order_group_id"].as_str().context("order group id")?.to_owned();
    eprintln!("order group {group_id}: venue cancels all at {} contracts matched / 15 s", p.group_contracts_per_15s);
    j.row("order_group", group.clone());

    let (done_tx, mut done_rx) = mpsc::unbounded_channel::<Done>();
    let (spot_tx, mut spot_rx) = mpsc::channel::<crate::shadow::SpotTick>(65_536);
    let spot_task = (p.spot_bps > 0.0).then(|| tokio::spawn(crate::shadow::spot_feed(spot_tx, p.series.clone())));
    let mut spot: HashMap<String, VecDeque<(i64, f64)>> = HashMap::new();
    let mut spot_pulls = 0u64;
    let mut amends = 0u64;
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
    let mut game_pause: HashMap<String, i64> = HashMap::new();
    let mut parent_seen: std::collections::HashSet<String> = std::collections::HashSet::new();
    let (mut game_pauses, mut jump_pauses) = (0u64, 0u64);
    let mut last_sports_refresh = Instant::now();
    let (mut round_trips, mut rt_pnl_c) = (0u64, 0.0f64);
    // Auto discovery runs off the loop: a full sweep of open sports events takes tens of seconds.
    let (auto_tx, mut auto_rx) = mpsc::channel::<Vec<Value>>(2);
    let auto_task = p.sports.as_ref().and_then(|c| c.auto_series.clone().map(|set| {
        let (http, tx, xi, refresh, max_sp, min_v) = (http.clone(), auto_tx.clone(), p.exchange_index, c.refresh_s, c.max_open_spread_c, c.min_v24);
        tokio::spawn(async move { auto_discover(http, set, xi, refresh, max_sp, min_v, tx).await })
    }));
    let mut auto_latest: Vec<Value> = Vec::new();
    if auto_task.is_some() {
        eprintln!("auto discovery: first sweep...");
        if let Some(v) = auto_rx.recv().await { auto_latest = v; }
    }

    // nohup'd background jobs ignore SIGINT; SIGTERM must also cancel everything on the way out.
    let mut sigterm = tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())?;
    'outer: while Instant::now() < deadline {
        // Discover the current open market per series (REST), then subscribe.
        match &p.sports {
            Some(cfg) if cfg.auto_series.is_some() => { apply_sports(auto_latest.iter().map(|m| (m.clone(), false)).collect(), cfg, &mut markets, &mut orders, &mut realized_c, &mut j, true); }
            Some(cfg) => { refresh_sports(&http, &p.series, cfg, p.exchange_index, &mut markets, &mut orders, &mut realized_c, &mut j).await; }
            None => refresh_markets(&http, &p.series, &mut markets).await,
        }
        parent_seen.clear();
        sync_positions(&http, &auth, &mut markets).await?;
        let (mut ws, _) = match crate::connect_ws(signed_ws_request(&auth)?).await {
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
                        Done::Amended { coid, price, res } => {
                            match res {
                                Ok(v) => {
                                    j.row("ack_amend", json!({"coid": coid, "price": price, "resp": v}));
                                    if let Some(o) = orders.get_mut(&coid) {
                                        if o.st == St::PendingAmend { o.st = St::Resting; o.price = price; }
                                    }
                                }
                                Err(e) => {
                                    rejects += 1;
                                    let es = format!("{e:#}");
                                    j.row("reject_amend", json!({"coid": coid, "err": es}));
                                    if es.contains("404") || es.contains("not_found") {
                                        release(&mut orders, &mut markets, &coid); // filled or gone
                                    } else if let Some(o) = orders.get_mut(&coid) {
                                        if o.st == St::PendingAmend { o.st = St::Resting; } // still at the old price
                                    }
                                }
                            }
                        }
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
                v = auto_rx.recv(), if auto_task.is_some() => {
                    let Some(v) = v else { continue };
                    if let Some(cfg) = &p.sports {
                        let new = apply_sports(v.into_iter().map(|m| (m, false)).collect(), cfg, &mut markets, &mut orders, &mut realized_c, &mut j, true);
                        if !new.is_empty() {
                            ws.send(Message::Text(json!({"id": next_id, "cmd": "subscribe", "params": {
                                "channels": ["orderbook_delta", "trade"], "market_tickers": new, "use_yes_price": true}}).to_string().into())).await?;
                            next_id += 1;
                        }
                    }
                    continue;
                }
                _ = housekeeping.tick() => {
                    j.flush();
                    // Stage timings for the interval just ended, then reset so the next report
                    // describes the next interval rather than the first frames of the run.
                    let stages = timing.report(&j.1);
                    eprintln!("        stage us p50: feed_age={} parse={} scan={} book={} pre_decide={} to_spawn={} journal={}",
                        Timing::p50(&timing.feed_age_us), Timing::p50(&timing.parse_us),
                        Timing::p50(&timing.scan_us), Timing::p50(&timing.book_us),
                        Timing::p50(&timing.pre_decide_us), Timing::p50(&timing.to_spawn_us),
                        Timing::p50(&j.1));
                    j.row("timing", stages);
                    timing = Timing::new();
                    j.1 = Samples::new(TIMING_CAP);
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
                    if let Some(cfg) = &p.sports {
                        if cfg.auto_series.is_none() && last_sports_refresh.elapsed().as_secs() >= cfg.refresh_s {
                            last_sports_refresh = Instant::now();
                            let new = refresh_sports(&http, &p.series, cfg, p.exchange_index, &mut markets, &mut orders, &mut realized_c, &mut j).await;
                            if !new.is_empty() {
                                ws.send(Message::Text(json!({"id": next_id, "cmd": "subscribe", "params": {
                                    "channels": ["orderbook_delta", "trade"], "market_tickers": new, "use_yes_price": true}}).to_string().into())).await?;
                                next_id += 1;
                            }
                        }
                    } else if markets.len() != before || markets.len() < p.series.len() {
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
                sp = spot_rx.recv(), if p.spot_bps > 0.0 => {
                    let Some(sp) = sp else { continue };
                    j.row("X", json!([sp.series, sp.recv_us, sp.mid, sp.exch_ms]));
                    let h = spot.entry(sp.series.clone()).or_default();
                    h.push_back((sp.recv_us, sp.mid));
                    while h.front().is_some_and(|(t, _)| *t < sp.recv_us - 10_000_000) { h.pop_front(); }
                    let Some(r) = crate::shadow::spot_ret_bps(h, sp.recv_us, 1_000_000) else { continue };
                    if r.abs() <= p.spot_bps { continue; }
                    // Spot up = YES worth more: our ask is stale. Pull it now, not at the next
                    // Kalshi message (the Kalshi book is what lags).
                    let side = if r > 0.0 { ASK } else { BID };
                    let prefix = format!("{}-", sp.series);
                    let victims: Vec<String> = markets.iter()
                        .filter(|(t, _)| t.starts_with(&prefix))
                        .filter_map(|(_, m)| m.slots[side].clone())
                        .filter(|c| orders.get(c).is_some_and(|o| o.st != St::PendingCancel))
                        .collect();
                    for c in victims {
                        if tokens < 2.0 { break; }
                        tokens -= 2.0;
                        spot_pulls += 1;
                        j.row("spot_pull", json!({"coid": c, "r_bps": r}));
                        request_cancel(&http, &auth, &done_tx, &mut orders, &c, p.exchange_index, &mut cancels);
                    }
                    continue;
                }
                _ = equity_tick.tick() => {
                    // Caps run on OUR ledger, marked to mid (a pair is exactly 100): the venue's
                    // position fields net pairs away, which read as a phantom −$6 on 2026-09-25.
                    let session = realized_c + markets.values().map(Mkt::mtm_c).sum::<f64>();
                    let cumulative = equity0 - baseline + session;
                    let venue = equity_c(&http, &auth, p.exchange_index).await.ok();
                    j.row("equity", json!({"session_mtm_c": session, "cumulative_c": cumulative, "venue_cash_plus_net_exposure_c": venue}));
                    eprintln!("[{}s] session {:+.2}c cumulative {:+.2}c (venue cash+net exposure {:.2}c) | posts={} cancels={} amends={} spot_pulls={} rejects={} fills={} group_trips={} undercuts={} resting={}",
                        started.elapsed().as_secs(), session, cumulative, venue.unwrap_or(f64::NAN), posts, cancels, amends, spot_pulls, rejects, fills, group_trips, undercuts, orders.len());
                    if p.sports.is_some() {
                        let open_pos: f64 = markets.values().map(|m| m.pos_fp.abs() as f64).sum::<f64>() / SIZE_SCALE as f64;
                        eprintln!("        sports: markets={} quotable={} games_with_parent_seen={} game_pauses={} jump_pauses={} open_abs_pos_ct={:.0} round_trips={} rt_pnl={:+.1}c",
                            markets.len(), markets.values().filter(|m| !m.parent).count(), parent_seen.len(), game_pauses, jump_pauses, open_pos, round_trips, rt_pnl_c);
                    }
                    if session < -p.session_max_loss_c { stop_reason = format!("session loss cap: {session:.2}c"); break 'outer; }
                    if cumulative < -p.cumulative_max_loss_c { stop_reason = format!("cumulative loss cap: {cumulative:.2}c"); break 'outer; }
                    continue;
                }
                _ = tokio::signal::ctrl_c() => { stop_reason = "ctrl-c".into(); break 'outer; }
                _ = sigterm.recv() => { stop_reason = "sigterm".into(); break 'outer; }
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
            let t_parse = Instant::now();
            let v: Value = match serde_json::from_str(&text) { Ok(v) => v, Err(_) => continue };
            timing.parse_us.push(t_parse.elapsed().as_micros() as i64, 1.0);
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
                            let was = mk.pos_fp;
                            mk.pos_fp = post; // the venue's number, not ours
                            if post == 0 && was != 0 && mk.entry_px > 0 {
                                let exit = (px * 100.0).round() as i64;
                                let pnl = if was > 0 { exit - mk.entry_px } else { mk.entry_px - exit } as f64 / 100.0;
                                round_trips += 1;
                                rt_pnl_c += pnl;
                                j.row("round_trip", json!({"ticker": ticker, "entry": mk.entry_px, "exit": exit, "long": was > 0, "pnl_c": pnl, "held_s": (now - mk.entry_us) / 1_000_000}));
                            }
                            if post == 0 { mk.entry_px = 0; mk.entry_us = 0; }
                            else if was == 0 || was.signum() != post.signum() {
                                mk.entry_px = (px * 100.0).round() as i64;
                                mk.entry_us = now;
                            }
                        }
                    }
                    continue;
                }
                "user_order" => {
                    j.row("user_order", m.clone());
                    let coid = m["client_order_id"].as_str().unwrap_or("").to_owned();
                    let st = m["status"].as_str().unwrap_or("");
                    if let (Some(o), Ok(rem)) = (orders.get_mut(&coid), parse_value(m.get("remaining_count_fp"), SIZE_SCALE)) {
                        o.remaining_fp = rem;
                    }
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
            let t_scan = Instant::now();
            // Sports caps: worst-case contracts in every OTHER quoted market (this game, all games).
            let exposure_elsewhere = p.sports.as_ref().map(|_| {
                let game = ticker.split('-').nth(1).unwrap_or("");
                let (mut g, mut t) = (0i64, 0i64);
                for (tk, om) in markets.iter().filter(|(tk, om)| !om.parent && tk.as_str() != ticker) {
                    let _ = tk;
                    let rest = |k: usize| om.slots[k].as_ref().and_then(|c| orders.get(c)).map_or(0, |o| o.remaining_fp);
                    let pot = (om.pos_fp + rest(BID)).abs().max((om.pos_fp - rest(ASK)).abs());
                    t += pot;
                    if om.game == game { g += pot; }
                }
                (g, t)
            });
            // Round cap: the summed position of every OTHER market closing with this one.
            let round_other = (p.max_round_net_fp > 0).then(|| {
                let close = markets.get(ticker).map_or(-1, |m| m.close_unix_ms);
                markets.iter().filter(|(tk, om)| tk.as_str() != ticker && om.close_unix_ms == close)
                    .map(|(_, om)| om.pos_fp).sum::<i64>()
            });
            timing.scan_us.push(t_scan.elapsed().as_micros() as i64, 1.0);
            let Some(mk) = markets.get_mut(ticker) else { continue };
            let vt = match kind {
                "orderbook_snapshot" => {
                    mk.book = Book::from_snapshot(m).ok();
                    if mk.parent && mk.book.is_some() { parent_seen.insert(mk.game.clone()); }
                    if let Some(t) = mk.book.as_ref().map(Book::touch) {
                        if let (Some(b), Some(a)) = (t.yes_bid_fp, t.yes_ask_fp) {
                            mk.last_mid = (b + a) as f64 / 200.0;
                            if p.book_residual { mk.receipt_mids.push_back((now, mk.last_mid)); }
                        }
                    }
                    continue;
                }
                "orderbook_delta" => m["ts"].as_str().and_then(rfc3339_us),
                "trade" => m["ts_ms"].as_i64().map(|x| x * 1_000),
                _ => None,
            };
            let Some(vt) = vt else { continue };
            timing.feed_age_us.push(now - vt, 1.0);
            let Some(book) = mk.book.as_mut() else { continue };
            let t_book = Instant::now();
            if kind == "orderbook_delta" {
                let previous_touch = book.touch();
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
                    }
                    // Preserve size-only changes for queue and capacity diagnostics.
                    if t != previous_touch {
                        j.row("B", json!([ticker, vt, b, t.yes_bid_size_fp, a, t.yes_ask_size_fp]));
                    }
                    // Parents keep the whole move window; quoted books only need 2 s.
                    let keep = p.sports.as_ref().filter(|_| mk.parent).map_or(2_000_000, |c| c.parent_window_us + 2_000_000);
                    while mk.mids.len() > 2 && mk.mids[1].0 < vt - keep { mk.mids.pop_front(); }
                }
            } else {
                j.row("T", json!([ticker, vt, m["taker_side"], m["yes_price_dollars"], m["count_fp"]]));
            }
            timing.book_us.push(t_book.elapsed().as_micros() as i64, 1.0);

            // ---- sports: a parent book is a score feed, never quoted ----
            if let Some(cfg) = &p.sports {
                if mk.parent {
                    if kind != "orderbook_delta" { continue; }
                    let (Some(m1), Some(m0)) = (mk.mids.back().map(|x| x.1), mk.mid_at(vt - cfg.parent_window_us)) else { continue };
                    // A book pinned in a tail (both ends < 3c or > 97c) is a decided line, not news.
                    if (m1 - m0).abs() < cfg.parent_move_c || m1.max(m0) < 3.0 || m1.min(m0) > 97.0 { continue; }
                    let game = mk.game.clone();
                    if game_pause.get(&game).is_some_and(|u| *u > now) { game_pause.insert(game, now + cfg.pause_us); continue; }
                    game_pauses += 1;
                    j.row("parent_pause", json!({"game": game, "parent": ticker, "from": m0, "to": m1, "vt": vt}));
                    pause_game(&game, now + cfg.pause_us, &mut game_pause, &markets, &mut orders, &http, &auth, &done_tx, p.exchange_index, &mut cancels);
                    continue;
                }
            }
            // ---- decide ----
            timing.pre_decide_us.push(t_recv.elapsed().as_micros() as i64, 1.0);
            if group_tripped || now < mk.hold_until_us { continue; }
            tokens = (tokens + (now - last_refill) as f64 / 1e6 * 300.0).min(900.0);
            last_refill = now;
            // The market as OTHER participants make it: our own resting clip removed, or an
            // improved quote of ours would read as the touch and we would penny ourselves.
            let own = |i: usize| mk.slots[i].as_ref().and_then(|c| orders.get(c));
            let (own_bid, own_ask) = (own(BID).map(|o| o.price), own(ASK).map(|o| o.price));
            let own_sz = |i: usize| own(i).map_or(0, |o| o.remaining_fp);
            let (Some((bid, bsz)), Some((ask, asz))) = (
                book.best_excluding("yes", own_bid, own_sz(BID)),
                book.best_excluding("no", own_ask, own_sz(ASK)),
            ) else { continue };
            let mid = (bid + ask) as f64 / 200.0;
            mk.last_bid = bid as f64 / 100.0;
            mk.last_ask = ask as f64 / 100.0;
            if p.book_residual {
                if mk.receipt_mids.back().is_none_or(|(_, old)| *old != mid) {
                    mk.receipt_mids.push_back((now, mid));
                }
                while mk.receipt_mids.len() > 2 && mk.receipt_mids[1].0 < now - 2_000_000 {
                    mk.receipt_mids.pop_front();
                }
            }
            let anchor = p.book_residual.then(|| mk.receipt_mids.iter().rev()
                .find(|(seen, _)| *seen <= now - 1_000_000).map(|(_, value)| *value)).flatten();
            let mom = if p.book_residual { anchor.map(|m0| mid - m0).unwrap_or(0.0) }
                else { mk.mid_at(vt - 1_000_000).map(|m0| mid - m0).unwrap_or(0.0) };
            let tot = (bsz + asz).max(1) as f64;
            let open_ok = mk.close_unix_ms - now / 1_000 > p.stop_before_close_s * 1_000;
            let postable = mid >= p.mid_lo_c && mid <= p.mid_hi_c && open_ok;
            // Sports: a jump in this book pauses its whole game (a score moves every ladder).
            let mut jump_game: Option<String> = None;
            let mut sports_ok = true;
            let mut sports_open = true;
            if let Some(cfg) = &p.sports {
                if let Some(m0) = mk.mid_at(vt - 2_000_000) {
                    if (mid - m0).abs() >= cfg.jump_c && !game_pause.get(&mk.game).is_some_and(|u| *u > now) {
                        jump_game = Some(mk.game.clone());
                    }
                }
                sports_ok = jump_game.is_none()
                    && (cfg.parents.is_empty() || parent_seen.contains(&mk.game))
                    && !game_pause.get(&mk.game).is_some_and(|u| *u > now);
                sports_open = sports_ok && ask - bid >= cfg.min_spread_c * 100
                    && ask - bid <= cfg.max_open_spread_c * 100
                    && mid >= p.mid_lo_c && mid <= p.mid_hi_c;
            }
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
                // Improving a bid steps UP from its own level; improving an ask steps DOWN, so its
                // step is the grid's just below it. At ask 0.9000 that is 1c, not the 0.1c above it:
                // 0.8990 is not a price (1,649 invalid_price rejects on run 9, 2026-09-26).
                let tick = if i == BID { tick_at(&mk.ranges, side_px) } else { tick_at(&mk.ranges, side_px - 1) };
                // Sports: the flattening side only needs the game to be quiet; opening also needs
                // width and the band.
                let exiting = (i == ASK && mk.pos_fp > 0) || (i == BID && mk.pos_fp < 0);
                let mut want = if p.sports.is_some() {
                    if exiting { sports_ok } else { sports_open }
                } else if p.penny_room > 0 {
                    open_ok && ask - bid >= p.penny_room * tick && mid > 1.0 && mid < 99.0
                } else {
                    postable
                };
                // Count the clip we are about to post: a fractional position (−0.98 after a
                // partial fill) must not admit a clip that ends at −1.98 (happened 2026-09-23).
                if i == BID && mk.pos_fp + p.clip_fp > p.max_pos_fp { want = false; }
                if i == ASK && mk.pos_fp - p.clip_fp < -p.max_pos_fp { want = false; }
                // A bid opens/adds unless we are short; an ask opens/adds unless we are long.
                let opens = if i == BID { mk.pos_fp >= 0 } else { mk.pos_fp <= 0 };
                if opens && mk.close_unix_ms - now / 1_000 <= p.open_cutoff_s * 1_000 { want = false; }
                if round_other.is_some_and(|o| round_cap_blocks(o + mk.pos_fp, i == BID, p.clip_fp, p.max_round_net_fp)) {
                    want = false;
                }
                if opens && !opening_deadline_ok(deadline.saturating_duration_since(Instant::now()), p.open_cutoff_s) { want = false; }
                if let (Some(cfg), true) = (&p.sports, want && opens) {
                    // Worst case with THIS side resting: every other market of the game at its worst
                    // one-sided outcome, this one with side i at a full clip.
                    let rest = |k: usize| mk.slots[k].as_ref().and_then(|c| orders.get(c)).map_or(0, |o| o.remaining_fp);
                    let (b, a) = if i == BID { (p.clip_fp, rest(ASK)) } else { (rest(BID), p.clip_fp) };
                    let this = (mk.pos_fp + b).abs().max((mk.pos_fp - a).abs());
                    let (g_other, t_other) = exposure_elsewhere.unwrap_or((0, 0));
                    if g_other + this > cfg.max_game_fp || t_other + this > cfg.max_total_fp { want = false; }
                }
                let toxic = if i == ASK { mom > p.mom_pull_c || bsz as f64 / tot > p.thin_pull }
                            else { -mom > p.mom_pull_c || asz as f64 / tot > p.thin_pull };
                if toxic { want = false; }
                if want && p.spot_bps > 0.0 {
                    let series = ticker.split('-').next().unwrap_or("");
                    if let Some(r) = spot.get(series).and_then(|h| crate::shadow::spot_ret_bps(h, now, 1_000_000)) {
                        if (i == ASK && r > p.spot_bps) || (i == BID && r < -p.spot_bps) { want = false; }
                    }
                }
                let mut target = match (p.penny_room > 0, i == BID) {
                    (true, true) => bid + tick,
                    (true, false) => ask - tick,
                    (false, true) => bid,
                    (false, false) => ask,
                };
                if let Some(cfg) = &p.sports {
                    // Inside only when there is room for both of our quotes plus a tick; else join.
                    if ask - bid < cfg.penny_min_c * 100 { target = if i == BID { bid } else { ask }; }
                    if exiting && mk.entry_px > 0 {
                        let held = now - mk.entry_us;
                        let edge = if held < cfg.scratch_us { cfg.exit_edge_c * 100 } else if held < cfg.bail_us { 0 } else { i64::MIN / 4 };
                        if edge > i64::MIN / 4 {
                            // Long YES: sell no lower than entry + edge. Short YES: buy no higher than entry - edge.
                            target = if i == ASK { target.max(mk.entry_px + edge) } else { target.min(mk.entry_px - edge) };
                        }
                        // Post-only must not cross the others' opposite touch.
                        target = if i == ASK { target.max(bid + tick) } else { target.min(ask - tick) };
                    }
                }
                if p.book_residual {
                    want &= anchor.is_some_and(|value| book_residual_allows(i, target, value));
                    if exiting && mk.entry_px > 0 {
                        want &= nonnegative_pair_price(mk.pos_fp, i, target, mk.entry_px);
                    }
                }
                // Sports: our OWN price must sit in the band too (a 6c bid under a 30c ask has
                // an in-band mid; the ledger's band was on the quote price).
                if p.sports.is_some() && !exiting && ((target as f64) < p.mid_lo_c * 100.0 || (target as f64) > p.mid_hi_c * 100.0) { want = false; }
                if opens && !open_band_ok(target, p.open_lo_c, p.open_hi_c) { want = false; }
                if target <= 0 || target >= PRICE_SCALE { want = false; }
                let cur = mk.slots[i].as_ref().and_then(|c| orders.get(c));
                match cur {
                    Some(o) if matches!(o.st, St::PendingCancel | St::PendingNew | St::PendingAmend) => continue,
                    Some(o) if want && o.price == target => continue,
                    // Untouched order, new price: one amend (keeps order_id) instead of two legs.
                    // A partly filled order is cancelled instead: amend's `count` semantics on a
                    // partial fill are unmeasured.
                    Some(o) if want && p.amend && o.st == St::Resting && o.remaining_fp == p.clip_fp && o.order_id.is_some() => {
                        if tokens < 10.0 { continue; }
                        tokens -= 10.0;
                        amends += 1;
                        let (c, id) = (o.coid.clone(), o.order_id.clone().unwrap_or_default());
                        let body = json!({"ticker": ticker, "side": if i == BID { "bid" } else { "ask" },
                            "price": format!("{:.4}", target as f64 / PRICE_SCALE as f64),
                            "count": format!("{:.2}", p.clip_fp as f64 / SIZE_SCALE as f64)});
                        j.row("amend", json!({"coid": c, "body": body, "mid": mid}));
                        if let Some(o) = orders.get_mut(&c) { o.st = St::PendingAmend; }
                        let (http2, auth2, tx) = (http.clone(), auth.clone(), done_tx.clone());
                        tokio::spawn(async move {
                            let res = if dry() { Ok(json!({})) } else { signed(&http2, &auth2, "POST", &format!("/portfolio/events/orders/{id}/amend"), Some(&body)).await };
                            let _ = tx.send(Done::Amended { coid: c, price: target, res });
                        });
                    }
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
                            "count": format!("{:.2}", p.clip_fp as f64 / SIZE_SCALE as f64), "price": format!("{:.4}", target as f64 / PRICE_SCALE as f64),
                            "time_in_force": "good_till_canceled", "self_trade_prevention_type": "maker",
                            "post_only": true, "order_group_id": group_id, "exchange_index": p.exchange_index,
                        });
                        j.row("new", json!({"coid": coid, "body": body, "mid": mid, "mom": mom,
                            "anchor_c": anchor, "bsz": bsz, "asz": asz}));
                        orders.insert(coid.clone(), LiveOrder { coid: coid.clone(), order_id: None,
                            ticker: ticker.to_owned(), side: i, price: target, st: St::PendingNew, remaining_fp: p.clip_fp });
                        mk.slots[i] = Some(coid.clone());
                        let (http2, auth2, tx) = (http.clone(), auth.clone(), done_tx.clone());
                        timing.to_spawn_us.push(t_recv.elapsed().as_micros() as i64, 1.0);
                        tokio::spawn(async move {
                            let res = if dry() { Ok(json!({"order_id": body["client_order_id"].clone()})) } else { signed(&http2, &auth2, "POST", "/portfolio/events/orders", Some(&body)).await };
                            let _ = tx.send(Done::Created { coid, res });
                        });
                    }
                    None => {}
                }
            }
            if let (Some(cfg), Some(game)) = (&p.sports, jump_game) {
                jump_pauses += 1;
                j.row("jump_pause", json!({"game": game, "ticker": ticker, "vt": vt}));
                pause_game(&game, now + cfg.pause_us, &mut game_pause, &markets, &mut orders, &http, &auth, &done_tx, p.exchange_index, &mut cancels);
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
    j.row("stop", json!({"reason": stop_reason, "game_pauses": game_pauses, "jump_pauses": jump_pauses, "posts": posts, "cancels": cancels, "rejects": rejects, "fills": fills, "group_trips": group_trips, "undercuts": undercuts, "local_cash_c": cash_c}));
    cancel_everything(&http, &auth, &mut j, p.exchange_index).await;
    if !dry() { let _ = signed(&http, &auth, "DELETE", &format!("/portfolio/order_groups/{group_id}"), None).await; }
    if let Some(t) = spot_task { t.abort(); }
    if let Some(t) = auto_task { t.abort(); }
    j.row("round_trips", json!({"n": round_trips, "pnl_c": rt_pnl_c}));
    let eq = equity_c(&http, &auth, p.exchange_index).await.unwrap_or(f64::NAN);
    let session = realized_c + markets.values().map(Mkt::mtm_c).sum::<f64>();
    j.row("end", json!({"venue_equity_c": eq, "session_mtm_c": session, "cumulative_c": equity0 - baseline + session}));
    eprintln!("session (own ledger, marked) {session:+.2}c, cumulative {:+.2}c", equity0 - baseline + session);
    j.finish();
    eprintln!("end equity {eq:.2}c, session {:+.2}c, cumulative {:+.2}c (open positions ride to settlement)", eq - equity0, eq - baseline);
    Ok(stop_reason)
}

/// Cancel every order of ours in one game's markets and block posting there until `until`.
#[allow(clippy::too_many_arguments)]
fn pause_game(
    game: &str, until: i64, game_pause: &mut HashMap<String, i64>, markets: &HashMap<String, Mkt>,
    orders: &mut HashMap<String, LiveOrder>, http: &reqwest::Client, auth: &Arc<Auth>,
    tx: &mpsc::UnboundedSender<Done>, exchange_index: i64, cancels: &mut u64,
) {
    game_pause.insert(game.to_owned(), until);
    let victims: Vec<String> = orders.values()
        .filter(|o| markets.get(&o.ticker).is_some_and(|m| m.game == game))
        .map(|o| o.coid.clone()).collect();
    for c in victims { request_cancel(http, auth, tx, orders, &c, exchange_index, cancels); }
}

/// Sports discovery: every open market of the quoted series (tag- and shard-filtered, capped),
/// plus the parent books of the games admitted. Markets no longer open are dropped (their
/// ledger value locked in, their order slots released — the venue cancels resting orders on
/// close). Returns tickers that were newly added. A failed page keeps the current set.
#[allow(clippy::too_many_arguments)]
async fn refresh_sports(
    http: &reqwest::Client, series: &[String], cfg: &SportsLive, exchange_index: i64,
    markets: &mut HashMap<String, Mkt>, orders: &mut HashMap<String, LiveOrder>,
    realized_c: &mut f64, j: &mut Journal,
) -> Vec<String> {
    let tagged = |t: &str| cfg.tags.iter().any(|g| t.contains(&format!("-{g}")));
    let mut open: Vec<(Value, bool)> = Vec::new();
    for (list, parent) in [(series, false), (cfg.parents.as_slice(), true)] {
        for s in list {
            let mut cursor = String::new();
            loop {
                let url = format!("{}/markets?series_ticker={s}&status=open&limit=1000&cursor={cursor}", rest_base());
                let body: Value = match http.get(url).send().await {
                    Ok(r) if r.status().is_success() => match r.json().await { Ok(b) => b, Err(_) => return Vec::new() },
                    _ => { eprintln!("sports refresh {s}: failed, keeping the current set"); return Vec::new(); }
                };
                for m in body["markets"].as_array().into_iter().flatten() {
                    let t = m["ticker"].as_str().unwrap_or("");
                    if tagged(t) && m["exchange_index"].as_i64() == Some(exchange_index) { open.push((m.clone(), parent)); }
                }
                cursor = body["cursor"].as_str().unwrap_or("").to_owned();
                if cursor.is_empty() { break; }
            }
            tokio::time::sleep(Duration::from_millis(120)).await;
        }
    }
    apply_sports(open, cfg, markets, orders, realized_c, j, false)
}

/// Reconcile the tracked set with a fresh discovery (already filtered; auto lists come ranked).
/// In `evict` mode a tracked market that fell out of the list is dropped when it holds no
/// position and no order of ours, so a continuous run follows where the volume is.
fn apply_sports(
    open: Vec<(Value, bool)>, cfg: &SportsLive, markets: &mut HashMap<String, Mkt>,
    orders: &mut HashMap<String, LiveOrder>, realized_c: &mut f64, j: &mut Journal, evict: bool,
) -> Vec<String> {
    let open_set: std::collections::HashSet<String> = open.iter().filter_map(|(m, _)| m["ticker"].as_str().map(str::to_owned)).collect();
    let gone: Vec<String> = markets.iter()
        .filter(|(t, m)| !open_set.contains(*t) && (!evict || (m.pos_fp == 0 && m.slots.iter().all(Option::is_none))))
        .map(|(t, _)| t.clone()).collect();
    for t in &gone {
        if let Some(m) = markets.remove(t) {
            *realized_c += m.mtm_c();
            for c in m.slots.iter().flatten() { orders.remove(c); }
        }
    }
    let mut new = Vec::new();
    let mut quotable = markets.values().filter(|m| !m.parent).count();
    for (m, parent) in open.iter().filter(|(_, p)| !*p) .chain(open.iter().filter(|(_, p)| *p)) {
        let t = m["ticker"].as_str().unwrap_or("").to_owned();
        if markets.contains_key(&t) { continue; }
        let game = t.split('-').nth(1).unwrap_or("").to_owned();
        if *parent {
            if !markets.values().any(|x| !x.parent && x.game == game) { continue; }
        } else {
            if quotable >= cfg.max_markets { continue; }
            quotable += 1;
        }
        let close = m["close_time"].as_str().and_then(rfc3339_us).map_or(i64::MAX / 4, |c| c / 1_000);
        markets.insert(t.clone(), Mkt { close_unix_ms: close, ranges: price_ranges(m), game, parent: *parent, conservative: true, ..Default::default() });
        new.push(t);
    }
    j.row("sports_markets", json!({"added": new.len(), "dropped": gone.len(), "total": markets.len(), "quotable": quotable}));
    eprintln!("sports refresh: +{} -{} markets, {} tracked ({} quotable)", new.len(), gone.len(), markets.len(), quotable);
    new
}

/// Rolling date tags as they appear in tickers (`26SEP27` = 2026-09-27): yesterday..tomorrow UTC.
fn utc_tags() -> Vec<String> {
    const MON: [&str; 12] = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"];
    let today = unix_us() / 86_400_000_000;
    (-1..=1).map(|d| {
        // Civil-from-days (H. Hinnant), days since 1970-01-01.
        let z = today + d + 719_468;
        let era = z.div_euclid(146_097);
        let doe = z - era * 146_097;
        let yoe = (doe - doe / 1_460 + doe / 36_524 - doe / 146_096) / 365;
        let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
        let mp = (5 * doy + 2) / 153;
        let day = doy - (153 * mp + 2) / 5 + 1;
        let month = if mp < 10 { mp + 3 } else { mp - 9 };
        let year = yoe + era * 400 + i64::from(month <= 2);
        format!("{:02}{}{:02}", year % 100, MON[(month - 1) as usize], day)
    }).collect()
}

/// Background sweep of every open sports event: fee-free series, our shard, a date tag in
/// the ticker, a tight enough in-band book, enough 24h volume. Ranked by 24h volume.
async fn auto_discover(
    http: reqwest::Client, free: std::collections::HashSet<String>, exchange_index: i64,
    refresh_s: u64, max_spread_c: i64, min_v24: f64, tx: mpsc::Sender<Vec<Value>>,
) {
    let f = |m: &Value, k: &str| m[k].as_str().and_then(|x| x.parse::<f64>().ok());
    loop {
        let tags: Vec<String> = utc_tags().into_iter().map(|t| format!("-{t}")).collect();
        let mut picked: Vec<(f64, Value)> = Vec::new();
        let mut cursor = String::new();
        let mut ok = true;
        for _ in 0..400 {
            let url = format!("{}/events?status=open&with_nested_markets=true&limit=200&cursor={cursor}", rest_base());
            let body: Value = match http.get(&url).send().await {
                Ok(r) if r.status().is_success() => match r.json().await { Ok(b) => b, Err(_) => { ok = false; break; } },
                Ok(r) if r.status().as_u16() == 429 => { tokio::time::sleep(Duration::from_secs(2)).await; continue; }
                _ => { ok = false; break; }
            };
            for ev in body["events"].as_array().into_iter().flatten() {
                if !ev["series_ticker"].as_str().is_some_and(|s| free.contains(s)) { continue; }
                for m in ev["markets"].as_array().into_iter().flatten() {
                    let t = m["ticker"].as_str().unwrap_or("");
                    if m["exchange_index"].as_i64() != Some(exchange_index) || !tags.iter().any(|g| t.contains(g.as_str())) { continue; }
                    if m["status"].as_str().is_some_and(|s| s != "active") { continue; }
                    let (Some(b), Some(a)) = (f(m, "yes_bid_dollars"), f(m, "yes_ask_dollars")) else { continue };
                    let v = f(m, "volume_24h_fp").unwrap_or(0.0);
                    let mid = (a + b) * 50.0;
                    if b <= 0.0 || a >= 1.0 || (a - b) * 100.0 > max_spread_c as f64 + 0.01 || !(5.0..=95.0).contains(&mid) || v < min_v24 { continue; }
                    picked.push((v, m.clone()));
                }
            }
            cursor = body["cursor"].as_str().unwrap_or("").to_owned();
            if cursor.is_empty() { break; }
            tokio::time::sleep(Duration::from_millis(150)).await;
        }
        if ok {
            picked.sort_by(|a, b| b.0.total_cmp(&a.0));
            if tx.send(picked.into_iter().map(|(_, m)| m).collect()).await.is_err() { return; }
        } else {
            eprintln!("auto discovery: sweep failed, keeping the current set");
        }
        tokio::time::sleep(Duration::from_secs(refresh_s)).await;
    }
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
        let res = if dry() { Ok(json!({})) } else { signed(&http, &auth, "DELETE", &path, None).await };
        let _ = tx.send(Done::Cancelled { coid, res });
    });
}

/// Standalone kill: cancel every resting order on the account and verify the book is empty.
/// Places nothing. For cleanup when an engine exited with an order still in flight.
pub async fn cancel_all(auth: Auth, exchange_index: i64) -> Result<()> {
    let http = client()?;
    let tmp = std::env::temp_dir().join(format!("cancel_all_{}.jsonl.gz", unix_us()));
    let mut j = Journal(BufWriter::new(GzEncoder::new(File::create(&tmp)?, Compression::fast())), Samples::new(TIMING_CAP));
    cancel_everything(&http, &auth, &mut j, exchange_index).await;
    j.finish();
    Ok(())
}

async fn cancel_everything(http: &reqwest::Client, auth: &Auth, j: &mut Journal, exchange_index: i64) {
    if dry() { eprintln!("dry run: nothing to cancel"); return; }
    // Five lists, four cancel rounds: the last list only verifies. Back off between attempts —
    // three sweeps 10 ms apart on 2026-09-25 retried inside the same failure and left 2 resting.
    for attempt in 0..5u64 {
        if attempt > 0 { tokio::time::sleep(Duration::from_millis(250 * attempt)).await; }
        let open = match signed(http, auth, "GET", "/portfolio/orders?status=resting", None).await {
            Ok(v) => v,
            Err(e) => { eprintln!("list resting failed: {e:#}"); continue; }
        };
        // Only this shard's orders: a second engine on another shard must survive our shutdown.
        let list: Vec<(String, String)> = open["orders"].as_array().into_iter().flatten()
            .filter(|o| o["exchange_index"].as_i64().is_none_or(|x| x == exchange_index))
            .filter_map(|o| Some((o["order_id"].as_str()?.to_owned(), o["ticker"].as_str()?.to_owned()))).collect();
        j.row("sweep", json!({"attempt": attempt, "resting": list.len()}));
        if list.is_empty() { eprintln!("verified: no resting orders"); return; }
        if attempt == 4 { break; }
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
                j.row("cancel_failed", json!({"order_id": id, "ticker": t, "error": format!("{e:#}")}));
            }
        }
    }
    eprintln!("WARNING: could not verify an empty book after 4 cancel rounds — check the account");
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
        .filter(|m| m["exchange_index"].as_i64() == Some(exchange_index))
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

fn price_ranges(m: &Value) -> Vec<(i64, i64, i64)> {
    m["price_ranges"].as_array().into_iter().flatten().filter_map(|r| {
        let f = |k: &str| parse_value(r.get(k), PRICE_SCALE).ok();
        Some((f("start")?, f("end")?, f("step")?))
    }).collect()
}

/// Price step at `px` on this grid; 1c if the grid is unknown (coarser never invents a price).
fn tick_at(ranges: &[(i64, i64, i64)], px: i64) -> i64 {
    ranges.iter().find(|(a, b, _)| *a <= px && px < *b)
        .or_else(|| ranges.last().filter(|(_, b, _)| px == *b))
        .map_or(100, |(_, _, st)| *st)
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
                markets.insert(t.to_owned(), Mkt { close_unix_ms: c / 1_000, ranges: price_ranges(m), ..Default::default() });
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
    use super::{ASK, BID, Mkt};
    use serde_json::Value;

    #[test]
    fn round_cap_blocks_only_the_side_that_grows_the_bet() {
        let (ct, cap) = (100, 200); // 1 ct clips, cap 2 ct (SIZE_SCALE 100)
        // At +2 across the round: another YES would make +3, blocked; a sale shrinks it.
        assert!(super::round_cap_blocks(200, true, ct, cap));
        assert!(!super::round_cap_blocks(200, false, ct, cap));
        // Up to the cap is allowed; mirror image for a short round.
        assert!(!super::round_cap_blocks(100, true, ct, cap));
        assert!(super::round_cap_blocks(-200, false, ct, cap));
        assert!(!super::round_cap_blocks(-200, true, ct, cap));
        // Already past the cap (fills landed together): only the shrinking side quotes.
        assert!(super::round_cap_blocks(300, true, ct, cap));
        assert!(!super::round_cap_blocks(300, false, ct, cap));
    }

    #[test]
    fn tick_follows_the_venue_grid() {
        let v: Value = serde_json::from_str(r#"{"price_ranges":[{"end":"0.1000","start":"0.0000","step":"0.0010"},{"end":"0.9000","start":"0.1000","step":"0.0100"},{"end":"1.0000","start":"0.9000","step":"0.0010"}]}"#).unwrap();
        let r = super::price_ranges(&v);
        assert_eq!((super::tick_at(&r, 500), super::tick_at(&r, 5_000), super::tick_at(&r, 9_500)), (10, 100, 10));
        let flat: Value = serde_json::from_str(r#"{"price_ranges":[{"end":"1.0000","start":"0.0000","step":"0.0100"}]}"#).unwrap();
        assert_eq!(super::tick_at(&super::price_ranges(&flat), 500), 100); // COPPER wing: 1c
        assert_eq!(super::tick_at(&[], 500), 100); // unknown grid: never finer than 1c
        // Boundaries: an ask at 0.9000 improves by 1c (to 0.8900), a bid at 0.9000 by 0.1c.
        assert_eq!(super::tick_at(&r, 9_000 - 1), 100);
        assert_eq!(super::tick_at(&r, 9_000), 10);
        // An ask at 0.1000 improves by 0.1c (to 0.0990), a bid at 0.0990 by 0.1c (to 0.1000).
        assert_eq!(super::tick_at(&r, 1_000 - 1), 10);
        assert_eq!(super::tick_at(&r, 990), 10);
    }

    #[test]
    fn book_residual_rejects_a_touch_that_crossed_the_old_mid() {
        assert!(super::book_residual_allows(BID, 4_900, 50.0));
        assert!(!super::book_residual_allows(BID, 5_100, 50.0));
        assert!(super::book_residual_allows(ASK, 5_100, 50.0));
        assert!(!super::book_residual_allows(ASK, 4_900, 50.0));
    }

    #[test]
    fn open_band_keeps_the_wings_out() {
        assert!(super::open_band_ok(1_000, 10.0, 90.0)); // 10.00c is in
        assert!(!super::open_band_ok(990, 10.0, 90.0)); // 9.90c wing
        assert!(super::open_band_ok(8_900, 10.0, 90.0));
        assert!(!super::open_band_ok(9_000, 10.0, 90.0)); // 90.00c is out
        assert!(super::open_band_ok(9_950, 0.0, 100.0)); // default: off
        assert!(super::open_band_ok(10, 0.0, 100.0));
    }

    #[test]
    fn opening_stops_before_the_experiment_deadline() {
        assert!(super::opening_deadline_ok(std::time::Duration::from_secs(121), 120));
        assert!(!super::opening_deadline_ok(std::time::Duration::from_secs(120), 120));
        assert!(!super::opening_deadline_ok(std::time::Duration::from_secs(1), 120));
    }

    #[test]
    fn book_residual_never_locks_a_negative_pair() {
        assert!(super::nonnegative_pair_price(100, ASK, 5_100, 5_000));
        assert!(!super::nonnegative_pair_price(100, ASK, 4_900, 5_000));
        assert!(super::nonnegative_pair_price(-100, BID, 4_900, 5_000));
        assert!(!super::nonnegative_pair_price(-100, BID, 5_100, 5_000));
    }

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
