//! ARMED Kap maker on Kalshi 15M crypto. Places REAL orders. The seat the historical replay
//! scored (`~/kap/RESULT_kap_hist_replay_20261023.md`, kap_fast, k = 4, cap 5), run as fast as
//! this box can run it.
//!
//! Brain, per market (`~/kap/replay/engine.py` is normative):
//!   belief  p_i = Kap P(settle YES) for grid step i (state at t_i = open + 5(i+1)), computed by
//!           the python sidecar (`~/kap/live/belief_sidecar.py`) and sent here over UDP; usable
//!           from t_i + 1 s, exactly as trained and scored. Never on the order path.
//!   fast    p(tau) = Phi(Phi^-1(p_i) + dS_bps / (sigma_i sqrt(close - tau))), recomputed on EVERY
//!           Coinbase BTC-USD match. dS from the latest match vs the last match at or before
//!           t_i - 1 s; sigma_i the 300 s 1-s realised vol before t_i - 1 s, floored at 0.05.
//!   target  ask joins the others' touch if it is above 100p + k, else the first whole cent
//!           above; bid mirrored; never improves the touch, never crosses.
//! An order keeps its place while its target is unchanged; a changed target is ONE amend
//! (2.6-2.9 ms to the book vs 4.8 ms for a cancel), so the requote never double-rests a side.
//! |position| <= --cap per market, counting every order that could still fill on the side that
//! grows it. Take-profit (`--take-profit-c`, on by default): the moment closing the WHOLE market
//! position at the others' touch would leave this market's P&L >= tp cents per contract AFTER the
//! taker fee, send one immediate-or-cancel order that closes it. Otherwise holds to settlement.
//!
//! Guardrails, each a past incident in this repo's memory:
//! - risk budget is REQUIRED on the command line (`--max-dd-pct`), never a default, and is a
//!   percent of the baseline equity persisted in `kapmm_state.json` (never silently re-based:
//!   delete the file to re-base, deliberately);
//! - position from the venue's `post_position_fp`, orders keyed by client_order_id;
//! - every order in an order group whose `contracts_limit` makes the VENUE cancel us on a sweep;
//! - nothing posts unless every feed is fresh: Kalshi book, Coinbase, and a belief < 12 s old;
//! - any feed break cancels everything; on exit cancel all and verify nothing rests.
//! Queue position is the question this run exists to answer, so every own-order book delta is
//! journalled with the size resting ahead of us at join (`own_delta.level_before`), and every
//! print (`T`) and touch change (`B`) is kept for the at-price vs through-print fill split.

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
use tokio::{net::UdpSocket, sync::mpsc, time::interval};
use tokio_tungstenite::tungstenite::Message;

use crate::{
    auth::Auth,
    book::{Book, PRICE_SCALE, SIZE_SCALE, parse_value},
    client, rest_base, rfc3339_us, signed_ws_request, unix_us,
    live::{equity_c, signed},
};

const BID: usize = 0;
const ASK: usize = 1;
const US: i64 = 1_000_000;
/// One whole cent in PRICE_SCALE units.
const CENT: i64 = PRICE_SCALE / 100;
const SIG_FLOOR: f64 = 0.05;
/// Belief i is usable from t_i + LAG (training, maker gate and replay all used 1 s).
const LAG_US: i64 = US;
/// Dust never sets the touch (paper_quoter MIN_SIZE).
const MIN_SIZE_FP: i64 = SIZE_SCALE;

pub static DRY: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);
fn dry() -> bool { DRY.load(std::sync::atomic::Ordering::Relaxed) }

pub struct Params {
    pub series: String,
    pub minutes: u64,
    pub max_dd_pct: f64,
    pub cap_fp: i64,
    pub k_c: f64,
    pub exchange_index: i64,
    pub group_limit: i64,
    pub out: PathBuf,
    pub belief_listen: String,
    pub sidecar_addr: String,
    pub stop_before_close_ms: i64,
    pub max_belief_age_us: i64,
    /// Close the position once market P&L after the taker fee is >= this many cents per contract.
    pub take_profit_c: Option<f64>,
    pub max_cb_age_us: i64,
    pub preflight_only: bool,
    /// Own-fill GATE mode (`~/kap/PREREG_kap_ownfill_20261025.md`, `~/kap/live/gate_sidecar.py`): the
    /// sidecar sends, per market and event, an absolute price per side or none; Kap beliefs unused.
    pub gate: bool,
    pub max_gate_age_us: i64,
}

// ------------------------------------------------------------------------------- brain
/// Gate mode: the sidecar's prices, never improving the others' REAL touch (the model scored an
/// estimated touch; resting deeper is the conservative direction) and never crossing it.
fn gate_targets(bid: Option<i64>, ask: Option<i64>, b: Option<i64>, a: Option<i64>) -> (Option<i64>, Option<i64>) {
    let bid = bid.map(|x| b.map_or(x, |b| x.min(b))).filter(|&x| x >= CENT && a.is_none_or(|a| x < a));
    let ask = ask.map(|x| a.map_or(x, |a| x.max(a))).filter(|&x| x <= 99 * CENT && b.is_none_or(|b| x > b));
    (ask, bid)
}

/// Standard normal CDF.
fn ndtr(x: f64) -> f64 { 0.5 * libm::erfc(-x / std::f64::consts::SQRT_2) }

/// Inverse standard normal CDF: Acklam's rational approximation + one Halley step (|err| < 1e-12).
fn ndtri(p: f64) -> f64 {
    const A: [f64; 6] = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
        1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00];
    const B: [f64; 5] = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
        6.680131188771972e+01, -1.328068155288572e+01];
    const C: [f64; 6] = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
        -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00];
    const D: [f64; 4] = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
        3.754408661907416e+00];
    let pl = 0.02425;
    let x = if p < pl {
        let q = (-2.0 * p.ln()).sqrt();
        (((((C[0] * q + C[1]) * q + C[2]) * q + C[3]) * q + C[4]) * q + C[5])
            / ((((D[0] * q + D[1]) * q + D[2]) * q + D[3]) * q + 1.0)
    } else if p <= 1.0 - pl {
        let q = p - 0.5;
        let r = q * q;
        (((((A[0] * r + A[1]) * r + A[2]) * r + A[3]) * r + A[4]) * r + A[5]) * q
            / (((((B[0] * r + B[1]) * r + B[2]) * r + B[3]) * r + B[4]) * r + 1.0)
    } else {
        let q = (-2.0 * (1.0 - p).ln()).sqrt();
        -(((((C[0] * q + C[1]) * q + C[2]) * q + C[3]) * q + C[4]) * q + C[5])
            / ((((D[0] * q + D[1]) * q + D[2]) * q + D[3]) * q + 1.0)
    };
    let e = ndtr(x) - p;
    let u = e * (2.0 * std::f64::consts::PI).sqrt() * (x * x / 2.0).exp();
    x - u / (1.0 + x * u / 2.0)
}

/// engine.decisions, fast branch.
fn fast_p(p_i: f64, s: f64, s_ref: f64, sig: f64, rem_s: f64) -> f64 {
    let z = ndtri(p_i.clamp(0.005, 0.995)) + 1e4 * (s / s_ref).ln() / (sig * rem_s.max(1.0).sqrt());
    if z.is_finite() { ndtr(z) } else { p_i }
}

/// paper_quoter.targets in PRICE_SCALE units. b, a = the OTHERS' touch.
fn targets(p: f64, b: Option<i64>, a: Option<i64>, k_c: f64) -> (Option<i64>, Option<i64>) {
    let g = 100.0 * p;
    let ask = match a { Some(a) if a as f64 > CENT as f64 * (g + k_c) => a,
        _ => CENT * ((g + k_c).floor() as i64 + 1) };
    let bid = match b { Some(b) if (b as f64) < CENT as f64 * (g - k_c) => b,
        _ => CENT * ((g - k_c).ceil() as i64 - 1) };
    let ok = |x: i64| (CENT..=99 * CENT).contains(&x);
    let ask = (ok(ask) && b.is_none_or(|b| ask > b)).then_some(ask);
    let bid = (ok(bid) && a.is_none_or(|a| bid < a)).then_some(bid);
    (ask, bid)
}

/// Coinbase last-trade series on the exchange clock.
#[derive(Default)]
struct Cb {
    ts: VecDeque<i64>,
    px: VecDeque<f64>,
    last_recv_us: i64,
}

impl Cb {
    fn push(&mut self, exch_us: i64, px: f64, recv_us: i64) {
        self.last_recv_us = recv_us;
        if self.ts.back().is_some_and(|t| exch_us < *t) { return; }
        self.ts.push_back(exch_us);
        self.px.push_back(px);
        while self.ts.front().is_some_and(|t| *t < exch_us - 420 * US) {
            self.ts.pop_front();
            self.px.pop_front();
        }
    }
    fn last(&self) -> Option<f64> { self.px.back().copied() }
    /// Last price at or before t (exch clock).
    fn at(&self, t: i64) -> Option<f64> {
        let k = self.ts.partition_point(|x| *x <= t);
        (k > 0).then(|| self.px[k - 1])
    }
    /// engine.spot_ref: (S_ref, sigma) for a belief whose state time is t_i.
    fn reference(&self, t_i_us: i64) -> Option<(f64, f64)> {
        let ref_t = t_i_us - US;
        if self.ts.front().is_none_or(|t| *t > ref_t - 301 * US) { return None; }
        let s_ref = self.at(ref_t)?;
        let mut ss = 0.0;
        let mut prev = self.at(ref_t - 300 * US);
        for k in (0..300).rev() {
            let cur = self.at(ref_t - k * US);
            if let (Some(a), Some(b)) = (prev, cur) { ss += (1e4 * (b / a).ln()).powi(2); }
            prev = cur;
        }
        Some((s_ref, (ss / 300.0).sqrt().max(SIG_FLOOR)))
    }
}

// ------------------------------------------------------------------------------- state
#[derive(PartialEq)]
enum St { PendingNew, Resting, PendingCancel, PendingAmend }

struct Ord_ { ticker: String, side: usize, price: i64, st: St, order_id: Option<String> }

struct Belief { i: i64, t_i_us: i64, p: f64, s_ref: f64, sig: f64 }

#[derive(Default)]
struct Mkt {
    book: Option<Book>,
    open_ms: i64,
    close_ms: i64,
    pos_fp: i64,
    slots: [Option<String>; 2],
    pending: Vec<(i64, Belief)>,      // (activate_at_us, belief)
    belief: Option<Belief>,
    yes_ct: f64,
    no_ct: f64,
    flow_c: f64,
    last_mid_c: f64,
    last_target: (Option<i64>, Option<i64>),
    /// `user_order` can free a filled order's slot before the `fill` message (the position
    /// authority) lands; posting in between quotes off a stale position. Hold while announced >
    /// seen, with a timer only as the backstop (live.rs, NEAR +2 on 2026-09-25).
    fills_announced: u64,
    fills_seen: u64,
    hold_until_us: i64,
    /// a take-profit IOC is in flight: no quoting and no second exit until it is answered
    exit_pending: bool,
    /// this market's P&L the last time the position was flat; take-profit judges only the
    /// position opened since, so profit banked by an earlier exit never excuses a new loser
    banked_c: f64,
    /// gate mode: (bid, ask) in PRICE_SCALE units, None = do not quote that side; and when it arrived
    gate: Option<(Option<i64>, Option<i64>, i64)>,
}

impl Mkt {
    fn mtm_c(&self) -> f64 { self.flow_c + self.yes_ct * self.last_mid_c + self.no_ct * (100.0 - self.last_mid_c) }
    /// This market's P&L if the open position is closed at YES price `x_c` (cents), fee included.
    fn close_pnl_c(&self, x_c: f64, n: f64) -> f64 {
        self.flow_c + self.yes_ct * x_c + self.no_ct * (100.0 - x_c) - taker_fee_c(x_c, n)
    }
    /// P&L of the position opened since the market was last flat, if closed at `x_c`.
    fn open_pnl_c(&self, x_c: f64, n: f64) -> f64 { self.close_pnl_c(x_c, n) - self.banked_c }
}

/// Kalshi taker fee for one order: 0.07 * C * P * (1 - P) dollars, rounded UP to the cent.
fn taker_fee_c(x_c: f64, n: f64) -> f64 {
    let x = x_c / 100.0;
    (7.0 * n * x * (1.0 - x) - 1e-9).ceil().max(0.0)
}

enum Done {
    Created { coid: String, res: Result<Value> },
    Cancelled { coid: String, res: Result<Value> },
    Amended { coid: String, price: i64, res: Result<Value> },
    Exited { ticker: String, coid: String, res: Result<Value> },
}

struct Journal(BufWriter<GzEncoder<File>>);
impl Journal {
    fn row(&mut self, kind: &str, v: Value) {
        let _ = writeln!(self.0, "{}", json!({"k": kind, "t": unix_us(), "v": v}));
    }
}

/// Best OTHERS' price on one ladder: our own resting size removed, dust ignored.
fn others_best(book: &Book, side: &str, own_px: Option<i64>, own_fp: i64) -> Option<i64> {
    book.levels(side).find_map(|(p, sz)| {
        let other = if Some(p) == own_px { sz - own_fp } else { sz };
        (other >= MIN_SIZE_FP).then_some(p)
    })
}

// ------------------------------------------------------------------------------- feeds
/// Coinbase Exchange `matches` for one product -> (exchange us, price, receipt us).
fn coinbase(product: &'static str, tx: mpsc::Sender<(i64, f64, i64)>) -> tokio::task::JoinHandle<()> {
    tokio::spawn(async move {
        loop {
            match crate::connect_ws("wss://ws-feed.exchange.coinbase.com").await {
                Ok((mut ws, _)) => {
                    let sub = json!({"type": "subscribe", "product_ids": [product], "channels": ["matches"]});
                    if ws.send(Message::Text(sub.to_string().into())).await.is_err() { continue; }
                    while let Some(Ok(m)) = ws.next().await {
                        let Message::Text(t) = m else { continue };
                        let recv = unix_us();
                        let Ok(v) = serde_json::from_str::<Value>(&t) else { continue };
                        if !matches!(v["type"].as_str(), Some("match" | "last_match")) { continue; }
                        let (Some(ts), Some(px)) = (v["time"].as_str().and_then(rfc3339_us),
                            v["price"].as_str().and_then(|s| s.parse::<f64>().ok())) else { continue };
                        if tx.send((ts, px, recv)).await.is_err() { return; }
                    }
                    eprintln!("coinbase ws closed; reconnecting");
                }
                Err(e) => eprintln!("coinbase connect: {e:#}"),
            }
            tokio::time::sleep(Duration::from_secs(1)).await;
        }
    })
}

async fn discover(http: &reqwest::Client, series: &str, markets: &mut HashMap<String, Mkt>) -> Vec<String> {
    let now_ms = unix_us() / 1_000;
    let url = format!("{}/markets?series_ticker={series}&min_close_ts={}&max_close_ts={}&limit=10",
        rest_base(), now_ms / 1_000, now_ms / 1_000 + 1_200);
    let mut new = Vec::new();
    let Ok(r) = http.get(url).send().await else { return new };
    let Ok(body) = r.json::<Value>().await else { return new };
    for m in body["markets"].as_array().into_iter().flatten() {
        let (Some(t), Some(o), Some(c)) = (m["ticker"].as_str(),
            m["open_time"].as_str().and_then(rfc3339_us), m["close_time"].as_str().and_then(rfc3339_us)) else { continue };
        // subscribe at most ~2 min ahead of the open, like the forward collector
        if c / 1_000 > now_ms && o / 1_000 < now_ms + 120_000 && !markets.contains_key(t) {
            markets.insert(t.to_owned(), Mkt { open_ms: o / 1_000, close_ms: c / 1_000, last_mid_c: 50.0, ..Default::default() });
            new.push(t.to_owned());
        }
    }
    new
}

async fn sweep(http: &reqwest::Client, auth: &Auth, j: &mut Journal, xi: i64) -> bool {
    if dry() { return true; }
    for attempt in 0..5u64 {
        if attempt > 0 { tokio::time::sleep(Duration::from_millis(250 * attempt)).await; }
        let Ok(open) = signed(http, auth, "GET", "/portfolio/orders?status=resting", None).await else { continue };
        let list: Vec<(String, String)> = open["orders"].as_array().into_iter().flatten()
            .filter(|o| o["exchange_index"].as_i64().is_none_or(|x| x == xi))
            .filter_map(|o| Some((o["order_id"].as_str()?.to_owned(), o["ticker"].as_str()?.to_owned()))).collect();
        j.row("sweep", json!({"attempt": attempt, "resting": list.len()}));
        if list.is_empty() { return true; }
        if attempt == 4 { break; }
        for (id, t) in &list {
            let path = format!("/portfolio/events/orders/{id}?market_ticker={t}&exchange_index={xi}");
            if let Err(e) = signed(http, auth, "DELETE", &path, None).await {
                j.row("cancel_failed", json!({"order_id": id, "ticker": t, "error": format!("{e:#}")}));
            }
        }
    }
    eprintln!("WARNING: could not verify an empty book — check the account");
    false
}

// ------------------------------------------------------------------------------- run
pub async fn run(auth: Auth, p: Params) -> Result<String> {
    anyhow::ensure!(p.max_dd_pct > 0.0 && p.max_dd_pct <= 50.0, "--max-dd-pct must be in (0, 50]");
    anyhow::ensure!(p.cap_fp >= SIZE_SCALE, "--cap must be >= 1");
    fs::create_dir_all(&p.out)?;
    let auth = Arc::new(auth);
    let http = client()?;
    let stamp = unix_us() / 1_000;
    let mut j = Journal(BufWriter::new(GzEncoder::new(
        OpenOptions::new().create(true).append(true).open(p.out.join(format!("kapmm_{stamp}.jsonl.gz")))?,
        Compression::fast())));

    // ---- preflight: an authenticated read, an empty book and no inventory in the series ----
    let equity0 = equity_c(&http, &auth, p.exchange_index).await.context("authenticated equity read")?;
    let resting = signed(&http, &auth, "GET", "/portfolio/orders?status=resting", None).await?;
    let n_rest = resting["orders"].as_array().map_or(0, Vec::len);
    let pos = signed(&http, &auth, "GET", "/portfolio/positions?count_filter=position&limit=200", None).await?;
    let held: Vec<String> = pos["market_positions"].as_array().into_iter().flatten()
        .filter(|m| m["ticker"].as_str().is_some_and(|t| t.starts_with(&format!("{}-", p.series))))
        .filter(|m| m["position_fp"].as_str().is_some_and(|x| x.parse::<f64>().unwrap_or(0.0) != 0.0))
        .filter_map(|m| m["ticker"].as_str().map(str::to_owned)).collect();
    let state_path = p.out.join("kapmm_state.json");
    let baseline = match fs::read_to_string(&state_path) {
        Ok(s) => serde_json::from_str::<Value>(&s)?["baseline_equity_c"].as_f64().context("state")?,
        Err(_) => {
            if !p.preflight_only && !dry() {
                fs::write(&state_path, json!({"baseline_equity_c": equity0, "created_ms": stamp}).to_string())?;
            }
            equity0
        }
    };
    let dd_c = baseline * p.max_dd_pct / 100.0;
    let pre = json!({"equity_c": equity0, "baseline_c": baseline, "dd_cap_c": dd_c, "resting": n_rest,
        "series_positions": held, "cap_ct": p.cap_fp / SIZE_SCALE, "k_c": p.k_c, "dry": dry()});
    eprintln!("preflight {pre}");
    j.row("preflight", pre);
    if n_rest > 0 && !dry() { bail!("{n_rest} resting order(s) on the account; refusing to start"); }
    if !held.is_empty() && !dry() { bail!("open positions in {}: {held:?}; refusing to start (flat start required)", p.series); }
    if equity0 < baseline - dd_c { bail!("drawdown cap already hit: equity {equity0:.2}c vs baseline {baseline:.2}c"); }

    // ---- venue-side kill switch ----
    let group_id = if dry() || p.preflight_only { "dry-run".to_owned() } else {
        let g = signed(&http, &auth, "POST", "/portfolio/order_groups/create",
            Some(&json!({"contracts_limit": p.group_limit, "exchange_index": p.exchange_index}))).await?;
        j.row("order_group", g.clone());
        g["order_group_id"].as_str().context("order group id")?.to_owned()
    };

    let udp = UdpSocket::bind(&p.belief_listen).await.context("belief socket")?;
    let mut ubuf = vec![0u8; 4096];
    let (cb_tx, mut cb_rx) = mpsc::channel::<(i64, f64, i64)>(65_536);
    let cb_task = coinbase(match p.series.as_str() { "KXBTC15M" => "BTC-USD", "KXETH15M" => "ETH-USD",
        "KXSOL15M" => "SOL-USD", _ => bail!("unsupported series {}", p.series) }, cb_tx);
    let mut cb = Cb::default();
    let (done_tx, mut done_rx) = mpsc::unbounded_channel::<Done>();
    let mut orders: HashMap<String, Ord_> = HashMap::new();
    let mut markets: HashMap<String, Mkt> = HashMap::new();
    let mut tokens = 900.0f64;
    let mut last_refill = unix_us();
    let (mut posts, mut amends, mut cancels, mut rejects, mut fills, mut beliefs, mut late_beliefs) = (0u64, 0u64, 0u64, 0u64, 0u64, 0u64, 0u64);
    let (mut own_joins, mut own_ahead_zero, mut own_ahead_sum) = (0u64, 0u64, 0f64);
    let mut realized_c = 0.0f64;
    let (mut exits, mut exit_rejects) = (0u64, 0u64);
    let mut group_tripped_until = 0i64;
    let started = Instant::now();
    let deadline = started + Duration::from_secs(if p.preflight_only { 75 } else { p.minutes * 60 });
    let mut stop_reason = String::from("deadline");
    let mut sigterm = tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())?;
    let mut ever_healthy = false;

    'outer: while Instant::now() < deadline {
        discover(&http, &p.series, &mut markets).await;
        let (mut ws, _) = match crate::connect_ws(signed_ws_request(&auth)?).await {
            Ok(x) => x,
            Err(e) => { eprintln!("ws connect: {e:#}"); tokio::time::sleep(Duration::from_secs(1)).await; continue; }
        };
        let tickers: Vec<String> = markets.keys().cloned().collect();
        for t in &tickers {
            let m = &markets[t];
            let _ = udp.send_to(json!({"k": "mkt", "tk": t, "open_ms": m.open_ms, "close_ms": m.close_ms}).to_string().as_bytes(), &p.sidecar_addr).await;
        }
        ws.send(Message::Text(json!({"id": 1, "cmd": "subscribe", "params": {
            "channels": ["orderbook_delta", "trade"], "market_tickers": tickers, "use_yes_price": true}}).to_string().into())).await?;
        ws.send(Message::Text(json!({"id": 2, "cmd": "subscribe", "params": {
            "channels": ["fill", "user_orders"]}}).to_string().into())).await?;
        let mut next_id = 3u64;
        let mut seqs: HashMap<u64, u64> = HashMap::new();
        for m in markets.values_mut() { m.book = None; }
        let mut housekeeping = interval(Duration::from_secs(2));
        let mut equity_tick = interval(Duration::from_secs(20));
        let mut activate = interval(Duration::from_millis(20));
        equity_tick.tick().await;

        loop {
            // `dirty`: markets whose inputs changed this iteration and must be re-decided.
            let mut dirty: Vec<String> = Vec::new();
            tokio::select! {
                biased;
                _ = tokio::signal::ctrl_c() => { stop_reason = "ctrl-c".into(); break 'outer; }
                _ = sigterm.recv() => { stop_reason = "sigterm".into(); break 'outer; }
                d = done_rx.recv() => {
                    let Some(d) = d else { continue };
                    match d {
                        Done::Created { coid, res } => match res {
                            Ok(v) => {
                                j.row("ack_new", json!({"coid": coid, "resp": v}));
                                if let Some(o) = orders.get_mut(&coid) {
                                    o.order_id = v["order"]["order_id"].as_str().or(v["order_id"].as_str()).map(str::to_owned);
                                    if o.st == St::PendingNew { o.st = St::Resting; }
                                    if o.st == St::PendingCancel { if let Some(id) = o.order_id.clone() {
                                        spawn_cancel(&http, &auth, &done_tx, coid.clone(), id, o.ticker.clone(), p.exchange_index);
                                    }}
                                    dirty.push(o.ticker.clone());
                                }
                            }
                            Err(e) => {
                                rejects += 1;
                                let es = format!("{e:#}");
                                j.row("reject_new", json!({"coid": coid, "err": es}));
                                if es.contains("order_group") { group_tripped_until = unix_us() + 20 * US; }
                                if let Some(t) = release(&mut orders, &mut markets, &coid) { dirty.push(t); }
                            }
                        },
                        Done::Amended { coid, price, res } => match res {
                            Ok(v) => {
                                j.row("ack_amend", json!({"coid": coid, "price": price, "resp": v}));
                                if let Some(o) = orders.get_mut(&coid) {
                                    if o.st == St::PendingAmend { o.st = St::Resting; o.price = price; }
                                    // an amend may hand back a new order id
                                    if let Some(id) = v["order"]["order_id"].as_str() { o.order_id = Some(id.to_owned()); }
                                    dirty.push(o.ticker.clone());
                                }
                            }
                            Err(e) => {
                                rejects += 1;
                                let es = format!("{e:#}");
                                j.row("reject_amend", json!({"coid": coid, "err": es}));
                                if es.contains("404") || es.contains("not_found") {
                                    if let Some(t) = release(&mut orders, &mut markets, &coid) { dirty.push(t); }
                                } else if let Some(o) = orders.get_mut(&coid) {
                                    // still resting at its old price: cancel it rather than leave a stale quote
                                    o.st = St::Resting;
                                    let c = coid.clone();
                                    let t = o.ticker.clone();
                                    request_cancel(&http, &auth, &done_tx, &mut orders, &c, p.exchange_index, &mut cancels);
                                    dirty.push(t);
                                }
                            }
                        },
                        Done::Exited { ticker, coid, res } => {
                            match &res {
                                Ok(v) => j.row("ack_exit", json!({"coid": coid, "tk": ticker, "resp": v})),
                                Err(e) => { exit_rejects += 1; j.row("reject_exit", json!({"coid": coid, "tk": ticker, "err": format!("{e:#}")})); }
                            }
                            if let Some(mk) = markets.get_mut(&ticker) { mk.exit_pending = false; }
                            dirty.push(ticker);
                        }
                        Done::Cancelled { coid, res } => {
                            match &res {
                                Ok(v) => j.row("ack_cancel", json!({"coid": coid, "resp": v})),
                                Err(e) => j.row("reject_cancel", json!({"coid": coid, "err": format!("{e:#}")})),
                            }
                            if let Some(t) = release(&mut orders, &mut markets, &coid) { dirty.push(t); }
                        }
                    }
                }
                c = cb_rx.recv() => {
                    let Some((ts, px, recv)) = c else { continue };
                    cb.push(ts, px, recv);
                    dirty.extend(markets.keys().cloned());
                }
                r = udp.recv_from(&mut ubuf) => {
                    let Ok((n, _)) = r else { continue };
                    let Ok(v) = serde_json::from_slice::<Value>(&ubuf[..n]) else { continue };
                    if v["k"].as_str() == Some("gate") {
                        let Some(tk) = v["tk"].as_str() else { continue };
                        let Some(mk) = markets.get_mut(tk) else { continue };
                        let px = |x: &Value| x.as_f64().filter(|d| *d > 0.0 && *d < 1.0).map(|d| (d * PRICE_SCALE as f64).round() as i64);
                        let g = (px(&v["bid"]), px(&v["ask"]), unix_us());
                        beliefs += 1;
                        if mk.gate.is_none_or(|o| (o.0, o.1) != (g.0, g.1)) {
                            j.row("gate", json!({"tk": tk, "bid": g.0, "ask": g.1, "pb": v["pb"], "pa": v["pa"], "tau_us": v["tau_us"]}));
                        }
                        mk.gate = Some(g);
                        dirty.push(tk.to_owned());
                        continue;
                    }
                    let (Some(tk), Some(i), Some(pr)) = (v["tk"].as_str(), v["i"].as_i64(), v["p"].as_f64()) else { continue };
                    let Some(mk) = markets.get_mut(tk) else { continue };
                    let t_i_us = (mk.open_ms / 1_000 + 5 * (i + 1)) * US;
                    let now = unix_us();
                    let Some((s_ref, sig)) = cb.reference(t_i_us) else {
                        j.row("belief_no_ref", json!({"tk": tk, "i": i}));
                        continue;
                    };
                    beliefs += 1;
                    if now > t_i_us + LAG_US { late_beliefs += 1; }
                    j.row("belief", json!({"tk": tk, "i": i, "p": pr, "s_ref": s_ref, "sig": sig,
                        "recv_after_ti_ms": (now - t_i_us) / 1_000, "fair": v["fair"]}));
                    mk.pending.push((t_i_us + LAG_US, Belief { i, t_i_us, p: pr, s_ref, sig }));
                }
                _ = activate.tick() => {
                    let now = unix_us();
                    for (t, mk) in markets.iter_mut() {
                        let mut ready: Vec<Belief> = Vec::new();
                        mk.pending.retain_mut(|(at, b)| {
                            if *at <= now { ready.push(Belief { i: b.i, t_i_us: b.t_i_us, p: b.p, s_ref: b.s_ref, sig: b.sig }); false } else { true }
                        });
                        if let Some(b) = ready.into_iter().max_by_key(|b| b.i) {
                            if mk.belief.as_ref().is_none_or(|x| x.i < b.i) { mk.belief = Some(b); dirty.push(t.clone()); }
                        }
                    }
                }
                _ = housekeeping.tick() => {
                    let _ = j.0.flush();
                    let now_ms = unix_us() / 1_000;
                    for m in markets.values().filter(|m| now_ms >= m.close_ms + 5_000) { realized_c += m.mtm_c(); }
                    markets.retain(|_, m| now_ms < m.close_ms + 5_000);
                    let new = discover(&http, &p.series, &mut markets).await;
                    if !new.is_empty() {
                        for t in &new {
                            let m = &markets[t];
                            let _ = udp.send_to(json!({"k": "mkt", "tk": t, "open_ms": m.open_ms, "close_ms": m.close_ms}).to_string().as_bytes(), &p.sidecar_addr).await;
                        }
                        ws.send(Message::Text(json!({"id": next_id, "cmd": "subscribe", "params": {
                            "channels": ["orderbook_delta", "trade"], "market_tickers": new, "use_yes_price": true}}).to_string().into())).await?;
                        next_id += 1;
                        j.row("markets", json!(markets.keys().collect::<Vec<_>>()));
                    }
                    if Instant::now() >= deadline { break 'outer; }
                }
                _ = equity_tick.tick() => {
                    let session = realized_c + markets.values().map(Mkt::mtm_c).sum::<f64>();
                    let cumulative = equity0 - baseline + session;
                    let venue = equity_c(&http, &auth, p.exchange_index).await.ok();
                    let now = unix_us();
                    let healthy = (now - cb.last_recv_us) < p.max_cb_age_us
                        && markets.values().any(|m| m.book.is_some())
                        && markets.values().any(|m| if p.gate { m.gate.is_some_and(|g| now - g.2 < p.max_gate_age_us) }
                            else { m.belief.as_ref().is_some_and(|b| now - b.t_i_us < p.max_belief_age_us) });
                    ever_healthy |= healthy;
                    let joins = own_joins.max(1) as f64;
                    j.row("equity", json!({"session_mtm_c": session, "cumulative_c": cumulative, "venue_c": venue, "healthy": healthy}));
                    eprintln!("[{}s] session {:+.2}c cum {:+.2}c (cap -{:.0}c) venue {:.2}c | posts={posts} amends={amends} cancels={cancels} rejects={rejects} fills={fills} exits={exits}/{exit_rejects} beliefs={beliefs} late={late_beliefs} | joins={own_joins} at_front={:.0}% mean_ahead={:.0}ct | healthy={healthy} pos={:?}",
                        started.elapsed().as_secs(), session, cumulative, dd_c, venue.unwrap_or(f64::NAN),
                        100.0 * own_ahead_zero as f64 / joins, own_ahead_sum / joins,
                        markets.iter().map(|(t, m)| (t.rsplit('-').next().unwrap_or("").to_owned(), m.pos_fp / SIZE_SCALE)).collect::<Vec<_>>());
                    if cumulative < -dd_c { stop_reason = format!("drawdown cap: {cumulative:.2}c < -{dd_c:.2}c"); break 'outer; }
                    if let Some(m) = markets.values().find(|m| m.pos_fp.abs() > p.cap_fp + SIZE_SCALE) {
                        stop_reason = format!("venue position {} beyond cap", m.pos_fp / SIZE_SCALE); break 'outer;
                    }
                    // the reflex needs 301 s of Coinbase history before its first reference
                    if !ever_healthy && started.elapsed() > Duration::from_secs(420) {
                        stop_reason = "feeds never all healthy within 420 s".into(); break 'outer;
                    }
                }
                msg = ws.next() => {
                    let text = match msg {
                        Some(Ok(Message::Text(t))) => t,
                        Some(Ok(Message::Ping(x))) => { ws.send(Message::Pong(x)).await?; continue; }
                        Some(Ok(_)) => continue,
                        Some(Err(e)) => { eprintln!("ws error: {e}"); break; }
                        None => { eprintln!("ws closed"); break; }
                    };
                    let now = unix_us();
                    let Ok(v) = serde_json::from_str::<Value>(&text) else { continue };
                    if let (Some(sid), Some(seq)) = (v["sid"].as_u64(), v["seq"].as_u64()) {
                        if let Some(prev) = seqs.insert(sid, seq) { if seq != prev + 1 { eprintln!("seq gap sid {sid}"); break; } }
                    }
                    let kind = v["type"].as_str().unwrap_or("");
                    let m = &v["msg"];
                    match kind {
                        "fill" => {
                            fills += 1;
                            j.row("fill", json!({"recv": now, "m": m}));
                            let ticker = m["market_ticker"].as_str().unwrap_or("").to_owned();
                            let px = parse_value(m.get("yes_price_dollars"), PRICE_SCALE).unwrap_or(0) as f64 / CENT as f64;
                            let ct = parse_value(m.get("count_fp"), SIZE_SCALE).unwrap_or(0) as f64 / SIZE_SCALE as f64;
                            if let Some(mk) = markets.get_mut(&ticker) {
                                if m["book_side"].as_str() == Some("bid") { mk.yes_ct += ct; mk.flow_c -= px * ct; }
                                else { mk.no_ct += ct; mk.flow_c -= (100.0 - px) * ct; }
                                // taker exits pay a fee; maker fills report 0
                                mk.flow_c -= m["fee_cost"].as_str().and_then(|f| f.parse::<f64>().ok()).unwrap_or(0.0) * 100.0;
                                match parse_value(m.get("post_position_fp"), SIZE_SCALE) {
                                    Ok(post) => {
                                        mk.pos_fp = post;
                                        // flat (YES and NO pair off at $1): bank the market's P&L so far
                                        if post == 0 { mk.banked_c = mk.close_pnl_c(50.0, 0.0); }
                                        mk.fills_seen += 1;
                                        if mk.fills_seen >= mk.fills_announced { mk.hold_until_us = 0; }
                                    }
                                    // fail closed: a fill we cannot read is a position we do not know
                                    Err(_) => { stop_reason = "unparseable fill position".into(); break 'outer; }
                                }
                                dirty.push(ticker);
                            }
                        }
                        "user_order" => {
                            j.row("user_order", m.clone());
                            let coid = m["client_order_id"].as_str().unwrap_or("").to_owned();
                            let st = m["status"].as_str().unwrap_or("");
                            if m["fill_count_fp"].as_str().is_some_and(|f| f != "0.00") {
                                let t = orders.get(&coid).map(|o| o.ticker.clone()).or_else(|| m["ticker"].as_str().map(str::to_owned));
                                if let Some(mk) = t.and_then(|t| markets.get_mut(&t)) {
                                    mk.fills_announced += 1;
                                    if mk.fills_announced > mk.fills_seen { mk.hold_until_us = now + 1_500_000; }
                                }
                            }
                            if st == "canceled" || st == "executed" {
                                if st == "canceled" && orders.get(&coid).is_some_and(|o| o.st != St::PendingCancel) {
                                    j.row("venue_cancel", json!({"coid": coid}));
                                    group_tripped_until = now + 20 * US;
                                }
                                if let Some(t) = release(&mut orders, &mut markets, &coid) { dirty.push(t); }
                            }
                        }
                        "orderbook_snapshot" | "orderbook_delta" | "trade" => {
                            let Some(ticker) = m["market_ticker"].as_str() else { continue };
                            let Some(mk) = markets.get_mut(ticker) else { continue };
                            if kind == "orderbook_snapshot" { mk.book = Book::from_snapshot(m).ok(); dirty.push(ticker.to_owned()); continue; }
                            if kind == "trade" {
                                let vt = m["ts_ms"].as_i64().map(|x| x * 1_000).or_else(|| m["ts"].as_i64().map(|x| x * US));
                                j.row("T", json!([ticker, vt, now, m["taker_side"], m["yes_price_dollars"], m["count_fp"], m["trade_id"]]));
                                let px = parse_value(m.get("yes_price_dollars"), 1_000).unwrap_or(0);
                                let qty = parse_value(m.get("count_fp"), SIZE_SCALE).unwrap_or(0) as f64 / SIZE_SCALE as f64;
                                let side = if m["taker_side"].as_str() == Some("yes") { 1 } else { -1 };
                                let _ = udp.send_to(json!({"k": "T", "tk": ticker, "ts_ms": now / 1_000, "vt_us": vt, "px": px, "q": qty,
                                    "s": side, "id": m["trade_id"]}).to_string().as_bytes(), &p.sidecar_addr).await;
                                continue;
                            }
                            let Some(book) = mk.book.as_mut() else { continue };
                            let side = m["side"].as_str().unwrap_or("");
                            let price = parse_value(m.get("price_dollars"), PRICE_SCALE).unwrap_or(-1);
                            let before = book.size_at(side, price);
                            if let Err(e) = book.apply_delta(m) { eprintln!("delta {ticker}: {e:#}"); break; }
                            if let Some(coid) = m["client_order_id"].as_str() {
                                let delta = parse_value(m.get("delta_fp"), SIZE_SCALE).unwrap_or(0);
                                if delta > 0 {
                                    own_joins += 1;
                                    if before == 0 { own_ahead_zero += 1; }
                                    own_ahead_sum += before as f64 / SIZE_SCALE as f64;
                                }
                                j.row("own_delta", json!({"coid": coid, "recv": now, "delta": m["delta_fp"],
                                    "price": m["price_dollars"], "side": side, "level_before": before, "ts": m["ts"]}));
                            }
                            let t = book.touch();
                            if let (Some(b), Some(a)) = (t.yes_bid_fp, t.yes_ask_fp) {
                                mk.last_mid_c = (b + a) as f64 / (2.0 * CENT as f64);
                                j.row("B", json!([ticker, now, b, t.yes_bid_size_fp, a, t.yes_ask_size_fp]));
                            }
                            dirty.push(ticker.to_owned());
                        }
                        _ => {}
                    }
                }
            }
            if p.preflight_only { continue; }
            // ---------------------------------------------------------------- decide
            dirty.sort_unstable();
            dirty.dedup();
            let now = unix_us();
            tokens = (tokens + (now - last_refill) as f64 / 1e6 * 300.0).min(900.0);
            last_refill = now;
            for tk in dirty {
                let Some(mk) = markets.get_mut(&tk) else { continue };
                let own = |i: usize| mk.slots[i].as_ref().and_then(|c| orders.get(c));
                if mk.exit_pending { continue; }
                // ---- take-profit: close the whole position once it is in profit after the fee ----
                if let (Some(tp), Some(book)) = (p.take_profit_c, mk.book.as_ref()) {
                    let n_fp = mk.pos_fp.abs();
                    let known = ((mk.yes_ct - mk.no_ct) * SIZE_SCALE as f64).round() as i64 == mk.pos_fp;
                    if n_fp > 0 && known && now >= mk.hold_until_us && mk.close_ms - now / 1_000 > 1_000 && tokens >= 10.0 {
                        let n = n_fp as f64 / SIZE_SCALE as f64;
                        // long YES sells into the others' bid; short YES buys the others' ask
                        let (side, x) = if mk.pos_fp > 0 {
                            ("ask", others_best(book, "yes", own(BID).map(|o| o.price), own(BID).map_or(0, |_| SIZE_SCALE)))
                        } else {
                            ("bid", others_best(book, "no", own(ASK).map(|o| o.price), own(ASK).map_or(0, |_| SIZE_SCALE)))
                        };
                        if let Some(x) = x {
                            let x_c = x as f64 / CENT as f64;
                            let pnl = mk.open_pnl_c(x_c, n);
                            if pnl >= tp * n {
                                tokens -= 10.0;
                                exits += 1;
                                mk.exit_pending = true;
                                let coid = crate::latency::random_id();
                                let body = json!({"ticker": tk, "client_order_id": coid, "side": side,
                                    "count": format!("{n:.2}"), "price": format!("{:.4}", x as f64 / PRICE_SCALE as f64),
                                    "time_in_force": "immediate_or_cancel", "self_trade_prevention_type": "maker",
                                    "exchange_index": p.exchange_index});
                                j.row("exit", json!({"coid": coid, "tk": tk, "side": side, "px": x, "n": n, "pnl_c": pnl,
                                    "flow_c": mk.flow_c, "banked_c": mk.banked_c, "yes_ct": mk.yes_ct, "no_ct": mk.no_ct, "pos": mk.pos_fp}));
                                let (http2, auth2, tx, t2) = (http.clone(), auth.clone(), done_tx.clone(), tk.clone());
                                tokio::spawn(async move {
                                    let res = if dry() { Ok(json!({"dry": true})) }
                                        else { signed(&http2, &auth2, "POST", "/portfolio/events/orders", Some(&body)).await };
                                    let _ = tx.send(Done::Exited { ticker: t2, coid, res });
                                });
                                continue;
                            }
                        }
                    }
                }
                // Inputs fresh? Anything stale -> no quote on either side.
                let fresh = mk.book.is_some()
                    && now - cb.last_recv_us < p.max_cb_age_us
                    && (if p.gate { mk.gate.is_some_and(|g| now - g.2 < p.max_gate_age_us) }
                        else { mk.belief.as_ref().is_some_and(|b| now - b.t_i_us < p.max_belief_age_us) })
                    && mk.close_ms - now / 1_000 > p.stop_before_close_ms
                    && now >= group_tripped_until
                    && now >= mk.hold_until_us;
                let (mut want_ask, mut want_bid) = (None, None);
                let mut p_now = f64::NAN;
                if fresh {
                    let book = mk.book.as_ref().unwrap();
                    let b = others_best(book, "yes", own(BID).map(|o| o.price), own(BID).map_or(0, |_| SIZE_SCALE));
                    let a = others_best(book, "no", own(ASK).map(|o| o.price), own(ASK).map_or(0, |_| SIZE_SCALE));
                    if p.gate {
                        let g = mk.gate.unwrap();
                        (want_ask, want_bid) = gate_targets(g.0, g.1, b, a);
                    } else {
                        let bl = mk.belief.as_ref().unwrap();
                        p_now = match cb.last() { Some(s) => fast_p(bl.p, s, bl.s_ref, bl.sig, (mk.close_ms - now / 1_000) as f64 / 1_000.0), None => bl.p };
                        (want_ask, want_bid) = targets(p_now, b, a, p.k_c);
                    }
                    // cap: count every order on the growing side that could still fill
                    let live = |i: usize| orders.values().filter(|o| o.ticker == tk && o.side == i).count() as i64 * SIZE_SCALE;
                    let cur_counts = |i: usize| own(i).map_or(0, |_| SIZE_SCALE);
                    if mk.pos_fp + live(BID) - cur_counts(BID) + SIZE_SCALE > p.cap_fp { want_bid = None; }
                    if mk.pos_fp - live(ASK) + cur_counts(ASK) - SIZE_SCALE < -p.cap_fp { want_ask = None; }
                }
                if (want_ask, want_bid) != mk.last_target {
                    j.row("target", json!({"tk": tk, "p": p_now, "i": mk.belief.as_ref().map(|b| b.i),
                        "ask": want_ask, "bid": want_bid, "pos": mk.pos_fp, "cb": cb.last()}));
                    mk.last_target = (want_ask, want_bid);
                }
                for (i, want) in [(BID, want_bid), (ASK, want_ask)] {
                    let cur = mk.slots[i].clone();
                    match (cur, want) {
                        (Some(c), w) => {
                            let Some(o) = orders.get(&c) else { mk.slots[i] = None; continue };
                            if o.st != St::Resting { continue; }       // in flight: wait for the ack
                            match w {
                                Some(px) if px == o.price => {}
                                Some(px) if o.order_id.is_some() => {
                                    if tokens < 10.0 { continue; }
                                    tokens -= 10.0;
                                    amends += 1;
                                    let o = orders.get_mut(&c).unwrap();
                                    o.st = St::PendingAmend;
                                    j.row("amend", json!({"coid": c, "from": o.price, "to": px, "p": p_now}));
                                    let body = json!({"ticker": tk, "side": if i == BID { "bid" } else { "ask" },
                                        "price": format!("{:.4}", px as f64 / PRICE_SCALE as f64), "count": "1.00"});
                                    let (http2, auth2, tx, id, c2) = (http.clone(), auth.clone(), done_tx.clone(), o.order_id.clone().unwrap(), c.clone());
                                    tokio::spawn(async move {
                                        let res = if dry() { Ok(json!({})) } else {
                                            signed(&http2, &auth2, "POST", &format!("/portfolio/events/orders/{id}/amend"), Some(&body)).await };
                                        let _ = tx.send(Done::Amended { coid: c2, price: px, res });
                                    });
                                }
                                _ => {
                                    if tokens < 2.0 { continue; }
                                    tokens -= 2.0;
                                    j.row("cancel", json!({"coid": c, "p": p_now}));
                                    request_cancel(&http, &auth, &done_tx, &mut orders, &c, p.exchange_index, &mut cancels);
                                }
                            }
                        }
                        (None, Some(px)) => {
                            if tokens < 10.0 { continue; }
                            tokens -= 10.0;
                            posts += 1;
                            let coid = crate::latency::random_id();
                            let body = json!({"ticker": tk, "client_order_id": coid, "side": if i == BID { "bid" } else { "ask" },
                                "count": "1.00", "price": format!("{:.4}", px as f64 / PRICE_SCALE as f64),
                                "time_in_force": "good_till_canceled", "self_trade_prevention_type": "maker",
                                "post_only": true, "order_group_id": group_id, "exchange_index": p.exchange_index});
                            j.row("new", json!({"coid": coid, "px": px, "side": i, "p": p_now, "pos": mk.pos_fp}));
                            orders.insert(coid.clone(), Ord_ { ticker: tk.clone(), side: i, price: px, st: St::PendingNew, order_id: None });
                            mk.slots[i] = Some(coid.clone());
                            let (http2, auth2, tx) = (http.clone(), auth.clone(), done_tx.clone());
                            tokio::spawn(async move {
                                let res = if dry() { Ok(json!({"order_id": body["client_order_id"].clone()})) }
                                    else { signed(&http2, &auth2, "POST", "/portfolio/events/orders", Some(&body)).await };
                                let _ = tx.send(Done::Created { coid, res });
                            });
                        }
                        (None, None) => {}
                    }
                }
            }
        }
        eprintln!("feed break: cancelling all resting orders");
        tokio::time::sleep(Duration::from_millis(500)).await;
        sweep(&http, &auth, &mut j, p.exchange_index).await;
        orders.clear();
        for m in markets.values_mut() { m.slots = [None, None]; m.belief = None; m.gate = None; m.pending.clear(); }
    }

    eprintln!("STOP: {stop_reason}. Cancelling everything.");
    tokio::time::sleep(Duration::from_millis(500)).await;
    let clean = sweep(&http, &auth, &mut j, p.exchange_index).await;
    if !dry() && !p.preflight_only { let _ = signed(&http, &auth, "DELETE", &format!("/portfolio/order_groups/{group_id}"), None).await; }
    cb_task.abort();
    let eq = equity_c(&http, &auth, p.exchange_index).await.unwrap_or(f64::NAN);
    let session = realized_c + markets.values().map(Mkt::mtm_c).sum::<f64>();
    j.row("end", json!({"reason": stop_reason, "venue_equity_c": eq, "session_mtm_c": session, "clean": clean,
        "posts": posts, "amends": amends, "cancels": cancels, "rejects": rejects, "fills": fills,
        "exits": exits, "exit_rejects": exit_rejects,
        "beliefs": beliefs, "late_beliefs": late_beliefs, "own_joins": own_joins, "own_at_front": own_ahead_zero,
        "ever_healthy": ever_healthy}));
    let _ = j.0.into_inner().map(|g| g.finish());
    eprintln!("end equity {eq:.2}c (start {equity0:.2}c), session marked {session:+.2}c; open positions ride to settlement");
    Ok(stop_reason)
}

fn release(orders: &mut HashMap<String, Ord_>, markets: &mut HashMap<String, Mkt>, coid: &str) -> Option<String> {
    let o = orders.remove(coid)?;
    if let Some(m) = markets.get_mut(&o.ticker) {
        if m.slots[o.side].as_deref() == Some(coid) { m.slots[o.side] = None; }
    }
    Some(o.ticker)
}

fn request_cancel(http: &reqwest::Client, auth: &Arc<Auth>, tx: &mpsc::UnboundedSender<Done>,
    orders: &mut HashMap<String, Ord_>, coid: &str, xi: i64, cancels: &mut u64) {
    let Some(o) = orders.get_mut(coid) else { return };
    if o.st == St::PendingCancel { return; }
    o.st = St::PendingCancel;
    *cancels += 1;
    if let Some(id) = o.order_id.clone() { spawn_cancel(http, auth, tx, coid.to_owned(), id, o.ticker.clone(), xi); }
}

fn spawn_cancel(http: &reqwest::Client, auth: &Arc<Auth>, tx: &mpsc::UnboundedSender<Done>,
    coid: String, id: String, ticker: String, xi: i64) {
    let (http, auth, tx) = (http.clone(), auth.clone(), tx.clone());
    tokio::spawn(async move {
        let path = format!("/portfolio/events/orders/{id}?market_ticker={ticker}&exchange_index={xi}");
        let res = if dry() { Ok(json!({})) } else { signed(&http, &auth, "DELETE", &path, None).await };
        let _ = tx.send(Done::Cancelled { coid, res });
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn gate_never_improves_or_crosses_the_real_touch() {
        let c = CENT;
        // estimate above the real bid -> rests at the real bid; below -> keeps its own deeper price
        assert_eq!(gate_targets(Some(45 * c), Some(52 * c), Some(44 * c), Some(55 * c)), (Some(55 * c), Some(44 * c)));
        assert_eq!(gate_targets(Some(40 * c), Some(60 * c), Some(44 * c), Some(55 * c)), (Some(60 * c), Some(40 * c)));
        // a side the gate declines stays empty
        assert_eq!(gate_targets(None, Some(60 * c), Some(44 * c), Some(55 * c)), (Some(60 * c), None));
        // no real book on a side: the gate's own price, still never crossing the other side
        assert_eq!(gate_targets(Some(56 * c), None, None, Some(55 * c)), (None, None));
    }

    #[test]
    fn inverse_normal_round_trips() {
        for p in [0.005, 0.01, 0.1, 0.3, 0.5, 0.77, 0.95, 0.995] {
            assert!((ndtr(ndtri(p)) - p).abs() < 1e-12, "{p}");
        }
        assert!((ndtr(1.959963984540054) - 0.975).abs() < 1e-12);
    }

    #[test]
    fn targets_match_the_paper_quoter() {
        // p = 0.50, k = 4: ask first whole cent above 54 = 55c, bid first below 46 = 45c
        assert_eq!(targets(0.50, None, None, 4.0), (Some(55 * CENT), Some(45 * CENT)));
        // join a touch that is already outside the gate
        assert_eq!(targets(0.50, Some(40 * CENT), Some(60 * CENT), 4.0), (Some(60 * CENT), Some(40 * CENT)));
        // touch inside the gate: rest at the gate, not the touch
        assert_eq!(targets(0.50, Some(49 * CENT), Some(51 * CENT), 4.0), (Some(55 * CENT), Some(45 * CENT)));
        // never cross the others' touch: their bid already sits above our ask gate
        assert_eq!(targets(0.50, Some(56 * CENT), None, 4.0).0, None);
        // wings: no quote outside 1..99c
        assert_eq!(targets(0.97, None, None, 4.0).0, None);
    }

    #[test]
    fn taker_fee_rounds_up_per_order() {
        assert_eq!(taker_fee_c(50.0, 1.0), 2.0);   // 1.75c -> 2c
        assert_eq!(taker_fee_c(50.0, 5.0), 9.0);   // 8.75c -> 9c
        assert_eq!(taker_fee_c(99.0, 1.0), 1.0);
        assert_eq!(taker_fee_c(100.0, 3.0), 0.0);
    }

    #[test]
    fn close_pnl_counts_entry_exit_and_fee() {
        // sold 5 YES at 54 (= bought 5 NO at 46): flow -230, no_ct 5
        let m = Mkt { no_ct: 5.0, flow_c: -230.0, ..Default::default() };
        // buy back 5 YES at 40: +5*(54-40) = +70, fee ceil(7*5*.4*.6)=ceil(8.4)=9
        assert!((m.close_pnl_c(40.0, 5.0) - 61.0).abs() < 1e-9);
        // at 54 it is the fee only
        assert!((m.close_pnl_c(54.0, 5.0) + 9.0).abs() < 1e-9);
    }

    #[test]
    fn banked_profit_does_not_excuse_a_new_loser() {
        // round trip: sold 5 at 54, bought 5 back at 40 with a 9c fee -> flat, banked +61
        let mut m = Mkt { yes_ct: 5.0, no_ct: 5.0, flow_c: -230.0 - 200.0 - 9.0, ..Default::default() };
        m.banked_c = m.close_pnl_c(50.0, 0.0);
        assert!((m.banked_c - 61.0).abs() < 1e-9);
        // re-enter: sell 1 more at 40; market moves to 45 against us
        m.no_ct += 1.0; m.flow_c -= 60.0;
        assert!(m.close_pnl_c(45.0, 1.0) > 1.0);          // the whole window still looks green...
        assert!(m.open_pnl_c(45.0, 1.0) < 0.0);           // ...but the new position is a loser: no exit
        assert!((m.open_pnl_c(35.0, 1.0) - 3.0).abs() < 1e-9); // 40 -> 35 = +5, fee 2 -> +3: exit
    }

    #[test]
    fn fast_reflex_moves_with_spot() {
        let up = fast_p(0.5, 100_010.0, 100_000.0, 1.0, 100.0);
        let dn = fast_p(0.5, 99_990.0, 100_000.0, 1.0, 100.0);
        assert!(up > 0.5 && dn < 0.5 && (up - 0.5 - (0.5 - dn)).abs() < 1e-5);
        assert_eq!(fast_p(0.5, 100_000.0, 100_000.0, 1.0, 100.0), 0.5);
    }

    #[test]
    fn coinbase_reference_needs_300_seconds_and_floors_sigma() {
        let mut cb = Cb::default();
        let t0 = 1_000_000 * US;
        for s in 0..400 { cb.push(t0 + s * US, 100.0, 0); }
        let t_i = t0 + 350 * US;
        let (s_ref, sig) = cb.reference(t_i).unwrap();
        assert_eq!(s_ref, 100.0);
        assert_eq!(sig, SIG_FLOOR);
        assert!(cb.reference(t0 + 200 * US).is_none());
    }
}
