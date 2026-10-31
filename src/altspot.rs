//! Thin-altcoin spot-gap taker (PREREG_altspot_tilt_20261006.md, OOS pass +2.345c/ct on 1,280 trades).
//!
//! Fair = Phi(ln(S_k / K') / (sigma_1m * sqrt(15 - k))), S_k = Coinbase close of minute k,
//! K' = floor_strike * exp(-basis), sigma_1m = std of 1-min log returns over the prior 120 min.
//! At the end of minute k in {2,3,5,8} (first trigger only, one trade per market):
//!   buy YES at the ask if fair - yes_ask > edge; buy NO (sell YES at the bid) if yes_bid - fair > edge.
//! The edge is the Kalshi mid under-reacting to spot on thin books; it is a 1-minute signal, so it is
//! not a latency race against the ~7 ms makers that pick off 15M quotes.
//!
//! Risk: one IOC clip per market; the session stops once premium at risk reaches --max-risk-c.
//! Every contract's worst case is its premium, so that cap bounds the session loss outright.

use std::{
    collections::{HashMap, HashSet},
    fs::OpenOptions,
    io::Write,
    time::{Duration, SystemTime, UNIX_EPOCH},
};

use anyhow::{Context, Result};
use serde_json::{Value, json};

use crate::{auth::Auth, live::signed};

pub struct Params {
    pub assets: Vec<String>,
    pub minutes: u64,
    pub dry: bool,
    pub edge: f64,
    pub count: f64,
    pub max_risk_c: f64,
    pub max_lag_s: i64,
    pub exchange_index: i64,
    pub out: String,
    /// Stop (exit 0, no new trades) once total account cash falls below this many cents.
    /// Cash excludes open positions, so this trips conservatively early.
    pub min_balance_c: f64,
}

const KS: [i64; 4] = [2, 3, 5, 8];

fn basis_bps(asset: &str) -> f64 {
    match asset {
        "NEAR" => 4.67,
        "ZEC" => 1.87,
        "HYPE" => 1.77,
        "BNB" => 2.44,
        _ => 0.0,
    }
}

fn now_s() -> i64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs() as i64).unwrap_or(0)
}

fn now_ms() -> i64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_millis() as i64).unwrap_or(0)
}

/// Days since 1970-01-01 for a proleptic Gregorian date (Howard Hinnant's algorithm).
fn days_from_civil(y: i64, m: i64, d: i64) -> i64 {
    let y = if m <= 2 { y - 1 } else { y };
    let era = y.div_euclid(400);
    let yoe = y - era * 400;
    let mp = (m + 9) % 12;
    let doy = (153 * mp + 2) / 5 + d - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    era * 146_097 + doe - 719_468
}

/// "2026-10-31T20:45:00Z" (Kalshi close_time) → unix seconds.
fn parse_rfc3339(s: &str) -> Option<i64> {
    let b = s.as_bytes();
    if b.len() < 19 {
        return None;
    }
    let n = |r: std::ops::Range<usize>| s.get(r)?.parse::<i64>().ok();
    let days = days_from_civil(n(0..4)?, n(5..7)?, n(8..10)?);
    Some(days * 86_400 + n(11..13)? * 3_600 + n(14..16)? * 60 + n(17..19)?)
}

/// Standard normal CDF via erfc (W. J. Cody-style rational approx, |err| < 1.2e-7).
fn norm_cdf(x: f64) -> f64 {
    let z = (x / std::f64::consts::SQRT_2).abs();
    let t = 1.0 / (1.0 + 0.5 * z);
    let erfc = t * (-z * z - 1.265_512_23
        + t * (1.000_023_68
            + t * (0.374_091_96
                + t * (0.096_784_18
                    + t * (-0.186_288_06
                        + t * (0.278_868_07
                            + t * (-1.135_203_98
                                + t * (1.488_515_87 + t * (-0.822_152_23 + t * 0.170_872_77)))))))))
        .exp();
    if x >= 0.0 { 1.0 - 0.5 * erfc } else { 0.5 * erfc }
}

/// Population std of 1-minute log returns (numpy's default ddof=0, matching the prereg scorer).
fn sigma_1m(closes: &[f64]) -> Option<f64> {
    if closes.len() < 40 {
        return None;
    }
    let r: Vec<f64> = closes.windows(2).map(|w| (w[1] / w[0]).ln()).collect();
    let mean = r.iter().sum::<f64>() / r.len() as f64;
    let var = r.iter().map(|x| (x - mean).powi(2)).sum::<f64>() / r.len() as f64;
    let s = var.sqrt();
    (s >= 2e-5).then_some(s)
}

pub fn fair(spot: f64, strike: f64, basis_bps: f64, sigma: f64, k: i64) -> f64 {
    let adj = strike * (-basis_bps * 1e-4).exp();
    norm_cdf((spot / adj).ln() / (sigma * ((15 - k) as f64).max(0.5).sqrt()))
}

#[derive(Debug, PartialEq)]
pub enum Signal {
    BuyYes { price: f64 },
    BuyNo { yes_bid: f64 },
}

/// The frozen rule. YES is checked first, as in the collector.
pub fn decide(fair: f64, yes_bid: f64, yes_ask: f64, edge: f64) -> Option<Signal> {
    if fair - yes_ask > edge {
        Some(Signal::BuyYes { price: yes_ask })
    } else if yes_bid - fair > edge {
        Some(Signal::BuyNo { yes_bid })
    } else {
        None
    }
}

#[derive(Clone)]
struct Market {
    ticker: String,
    open_ts: i64,
    close_ts: i64,
    strike: f64,
}

struct Spot {
    /// candle start (unix s) → close
    closes: HashMap<i64, f64>,
}

async fn coinbase_closes(cb: &reqwest::Client, asset: &str, start: i64, end: i64) -> Result<Spot> {
    let url = format!(
        "https://api.exchange.coinbase.com/products/{asset}-USD/candles?granularity=60&start={start}&end={end}"
    );
    let v: Value = cb.get(url).send().await?.error_for_status()?.json().await?;
    let mut closes = HashMap::new();
    for row in v.as_array().into_iter().flatten() {
        if let (Some(t), Some(c)) = (row.get(0).and_then(Value::as_i64), row.get(4).and_then(Value::as_f64)) {
            closes.insert(t, c);
        }
    }
    Ok(Spot { closes })
}

async fn active_market(http: &reqwest::Client, auth: &Auth, asset: &str, now: i64, shard: i64) -> Result<Option<Market>> {
    let v = signed(http, auth, "GET", &format!("/markets?series_ticker=KX{asset}15M&status=open&limit=8"), None).await?;
    let mut best: Option<Market> = None;
    for m in v["markets"].as_array().into_iter().flatten() {
        if m["exchange_index"].as_i64().is_some_and(|i| i != shard) {
            continue;
        }
        let (Some(close), Some(strike)) = (
            m["close_time"].as_str().and_then(parse_rfc3339),
            m["floor_strike"].as_f64().or_else(|| m["floor_strike"].as_str().and_then(|s| s.replace(',', "").parse().ok())),
        ) else { continue };
        let open = close - 900;
        if open <= now && now < close && best.as_ref().is_none_or(|b| close < b.close_ts) {
            best = Some(Market { ticker: m["ticker"].as_str().unwrap_or_default().to_owned(), open_ts: open, close_ts: close, strike });
        }
    }
    Ok(best)
}

/// Best YES bid / YES ask (dollars) from the REST book: yes_ask = 1 - best NO bid.
async fn touch(http: &reqwest::Client, auth: &Auth, ticker: &str) -> Result<Option<(f64, f64, f64, f64)>> {
    let v = signed(http, auth, "GET", &format!("/markets/{ticker}/orderbook"), None).await?;
    let best = |side: &str| -> Option<(f64, f64)> {
        v["orderbook_fp"][side].as_array()?.iter().filter_map(|lvl| {
            let p: f64 = lvl.get(0)?.as_str()?.parse().ok()?;
            let q: f64 = lvl.get(1)?.as_str()?.parse().ok()?;
            Some((p, q))
        }).max_by(|a, b| a.0.total_cmp(&b.0))
    };
    let (Some((yb, ybs)), Some((nb, nbs))) = (best("yes_dollars"), best("no_dollars")) else { return Ok(None) };
    Ok(Some((yb, 1.0 - nb, ybs, nbs)))
}

pub async fn run(auth: Auth, http: reqwest::Client, p: Params) -> Result<()> {
    let cb = reqwest::Client::builder()
        .user_agent("rustmaker-altspot/0.1")
        .timeout(Duration::from_secs(5))
        .build()?;
    std::fs::create_dir_all(&p.out)?;
    let path = format!("{}/altspot_{}.jsonl", p.out, now_ms());
    let mut j = OpenOptions::new().create(true).append(true).open(&path).with_context(|| path.clone())?;
    let mut row = |kind: &str, v: Value| {
        let _ = writeln!(j, "{}", json!({"k": kind, "t_ms": now_ms(), "v": v}));
        let _ = j.flush();
    };
    eprintln!(
        "{} altspot {:?} edge {:.2} count {} max-risk {:.0}c, journal {path}",
        if p.dry { "DRY RUN (nothing sent):" } else { "ARMED:" }, p.assets, p.edge, p.count, p.max_risk_c
    );
    row("start", json!({"dry": p.dry, "assets": p.assets, "edge": p.edge, "count": p.count, "max_risk_c": p.max_risk_c}));

    let deadline = now_s() + p.minutes as i64 * 60;
    let mut markets: HashMap<String, Market> = HashMap::new();
    let mut sigma: HashMap<String, (String, f64)> = HashMap::new(); // asset → (ticker, sigma)
    let mut evaluated: HashSet<(String, i64)> = HashSet::new();
    let mut traded: HashSet<String> = HashSet::new();
    let mut risk_c = 0.0;
    let (mut decisions, mut signals, mut orders) = (0u64, 0u64, 0u64);
    let mut next_discover = 0i64;
    let mut next_balance = 0i64;

    while now_s() < deadline {
        let now = now_s();
        if now >= next_discover {
            if p.min_balance_c > 0.0 && now >= next_balance {
                next_balance = now + 60;
                match signed(&http, &auth, "GET", "/portfolio/balance", None).await {
                    Ok(b) => {
                        let cash_c = b["balance"].as_f64().unwrap_or(f64::NAN);
                        row("balance", json!({"cash_c": cash_c}));
                        if cash_c < p.min_balance_c {
                            eprintln!("STOP: cash {cash_c:.0}c below floor {:.0}c", p.min_balance_c);
                            row("floor_stop", json!({"cash_c": cash_c, "floor_c": p.min_balance_c}));
                            // Distinct exit code so a supervisor (systemd RestartPreventExitStatus=3)
                            // does NOT restart into a drawdown. IOC orders leave nothing resting.
                            std::process::exit(3);
                        }
                    }
                    Err(e) => eprintln!("balance check: {e:#}"),
                }
            }
            for a in &p.assets {
                match active_market(&http, &auth, a, now, p.exchange_index).await {
                    Ok(Some(m)) => {
                        if markets.get(a).is_none_or(|old| old.ticker != m.ticker) {
                            eprintln!("[{a}] {} strike {} closes {}", m.ticker, m.strike, m.close_ts);
                            row("market", json!({"asset": a, "ticker": m.ticker, "open": m.open_ts, "close": m.close_ts, "strike": m.strike}));
                        }
                        markets.insert(a.clone(), m);
                    }
                    Ok(None) => { markets.remove(a); }
                    Err(e) => eprintln!("[{a}] discover: {e:#}"),
                }
                tokio::time::sleep(Duration::from_millis(150)).await;
            }
            next_discover = now + 20;
        }
        for (a, m) in markets.clone() {
            if traded.contains(&m.ticker) {
                continue;
            }
            for k in KS {
                let key = (m.ticker.clone(), k);
                if evaluated.contains(&key) {
                    continue;
                }
                let decision_ts = m.open_ts + 60 * k;
                if now < decision_ts + 1 {
                    break; // later k cannot be due before this one
                }
                if now > decision_ts + p.max_lag_s {
                    row("missed", json!({"ticker": m.ticker, "k": k, "lag_s": now - decision_ts}));
                    evaluated.insert(key);
                    continue;
                }
                // sigma: once per market, from the 120 min before open.
                if sigma.get(&a).is_none_or(|(t, _)| *t != m.ticker) {
                    match coinbase_closes(&cb, &a, m.open_ts - 121 * 60, m.open_ts - 60).await {
                        Ok(s) => {
                            let hist: Vec<f64> = (1..=120).rev().filter_map(|i| s.closes.get(&(m.open_ts - 60 * i)).copied()).collect();
                            match sigma_1m(&hist) {
                                Some(sg) => { sigma.insert(a.clone(), (m.ticker.clone(), sg)); }
                                None => { row("no_sigma", json!({"ticker": m.ticker, "points": hist.len()})); evaluated.insert(key); continue; }
                            }
                        }
                        Err(e) => { eprintln!("[{a}] coinbase history: {e:#}"); break; }
                    }
                }
                let sg = sigma[&a].1;
                // S_k: the close of the minute that ENDS at open + 60k (candle start open + 60(k-1)).
                let candle = m.open_ts + 60 * (k - 1);
                let t_req = now_ms();
                let spot = match coinbase_closes(&cb, &a, candle, candle + 60).await {
                    Ok(s) => s.closes.get(&candle).copied(),
                    Err(e) => { eprintln!("[{a}] coinbase: {e:#}"); None }
                };
                let Some(spot) = spot else { break }; // candle not published yet; retry next tick
                let t_spot = now_ms();
                let Some((yes_bid, yes_ask, ybs, nbs)) = touch(&http, &auth, &m.ticker).await.unwrap_or(None) else {
                    row("one_sided", json!({"ticker": m.ticker, "k": k}));
                    break;
                };
                let t_book = now_ms();
                let fv = fair(spot, m.strike, basis_bps(&a), sg, k);
                let sig = decide(fv, yes_bid, yes_ask, p.edge);
                decisions += 1;
                evaluated.insert(key);
                row("decision", json!({"asset": a, "ticker": m.ticker, "k": k, "spot": spot, "strike": m.strike,
                    "sigma": sg, "fair": fv, "yes_bid": yes_bid, "yes_ask": yes_ask, "yes_bid_sz": ybs, "no_bid_sz": nbs,
                    "lag_ms": t_book - decision_ts * 1000, "spot_ms": t_spot - t_req, "book_ms": t_book - t_spot,
                    "signal": format!("{sig:?}")}));
                eprintln!("[{a}] k={k} spot {spot} fair {fv:.3} bid {yes_bid:.3} ask {yes_ask:.3} lag {}ms → {sig:?}",
                    t_book - decision_ts * 1000);
                let Some(sig) = sig else { continue };
                signals += 1;
                traded.insert(m.ticker.clone());
                // Premium at risk per contract: YES costs the ask; NO costs 1 - yes_bid.
                let (side, price, premium_c) = match sig {
                    Signal::BuyYes { price } => ("bid", price, price * 100.0 * p.count),
                    Signal::BuyNo { yes_bid } => ("ask", yes_bid, (1.0 - yes_bid) * 100.0 * p.count),
                };
                if risk_c + premium_c > p.max_risk_c {
                    eprintln!("risk cap: {risk_c:.0}c + {premium_c:.0}c > {:.0}c, skipping", p.max_risk_c);
                    row("risk_skip", json!({"ticker": m.ticker, "risk_c": risk_c, "premium_c": premium_c}));
                    break;
                }
                let body = json!({
                    "ticker": m.ticker, "client_order_id": crate::latency::random_id(), "side": side,
                    "count": format!("{:.2}", p.count), "price": format!("{price:.4}"),
                    "time_in_force": "immediate_or_cancel", "self_trade_prevention_type": "maker",
                    "exchange_index": p.exchange_index,
                });
                let res = if p.dry { Ok(json!({"dry_run": true})) } else { signed(&http, &auth, "POST", "/portfolio/events/orders", Some(&body)).await };
                match &res {
                    Ok(v) => {
                        orders += 1;
                        risk_c += premium_c;
                        eprintln!("ORDER {side} {} @ {price:.4} ({premium_c:.0}c at risk, session {risk_c:.0}c) → {v}", m.ticker);
                    }
                    Err(e) => eprintln!("ORDER REJECTED {}: {e:#}", m.ticker),
                }
                row("order", json!({"body": body, "res": res.as_ref().map(|v| v.clone()).map_err(|e| format!("{e:#}")), "premium_c": premium_c}));
                break;
            }
        }
        if risk_c >= p.max_risk_c {
            eprintln!("STOP: premium at risk {risk_c:.0}c reached cap {:.0}c", p.max_risk_c);
            break;
        }
        tokio::time::sleep(Duration::from_millis(500)).await;
    }
    eprintln!("done: decisions={decisions} signals={signals} orders={orders} premium_at_risk={risk_c:.0}c (positions ride to settlement)");
    row("end", json!({"decisions": decisions, "signals": signals, "orders": orders, "risk_c": risk_c}));
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cdf_matches_known_values() {
        assert!((norm_cdf(0.0) - 0.5).abs() < 1e-7);
        assert!((norm_cdf(1.0) - 0.841_344_75).abs() < 1e-6);
        assert!((norm_cdf(-1.96) - 0.024_997_9).abs() < 1e-6);
    }

    #[test]
    fn parses_kalshi_close_time() {
        assert_eq!(parse_rfc3339("2026-10-31T20:45:00Z"), Some(1_793_479_500));
        assert_eq!(parse_rfc3339("1970-01-01T00:00:00Z"), Some(0));
    }

    #[test]
    fn rule_matches_prereg() {
        assert_eq!(decide(0.70, 0.55, 0.58, 0.10), Some(Signal::BuyYes { price: 0.58 }));
        assert_eq!(decide(0.30, 0.45, 0.47, 0.10), Some(Signal::BuyNo { yes_bid: 0.45 }));
        assert_eq!(decide(0.50, 0.45, 0.47, 0.10), None);
        // just under 10c of edge does not trigger
        assert_eq!(decide(0.679, 0.55, 0.58, 0.10), None);
    }

    #[test]
    fn fair_is_half_at_the_money_after_basis() {
        let f = fair(100.0 * (-4.67e-4f64).exp(), 100.0, 4.67, 0.001, 5);
        assert!((f - 0.5).abs() < 1e-6);
    }
}
