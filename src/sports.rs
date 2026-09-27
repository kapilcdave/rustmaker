//! Public GET-only sports discovery. No order endpoints.
use crate::{Discovered, client, rest_base, rfc3339_ms, unix_us};
use anyhow::{Context, Result, ensure};
use serde_json::{Value, json};
use std::{
    collections::HashSet,
    fs::{self, OpenOptions},
    io::Write,
    path::{Path, PathBuf},
    time::Duration,
};
use tokio::sync::mpsc;

pub fn parse_series(raw: &str) -> Result<Vec<String>> {
    let mut series = Vec::new();
    for s in raw.split(',').map(str::trim) {
        ensure!(
            !s.is_empty()
                && s.bytes()
                    .all(|b| b.is_ascii_uppercase() || b.is_ascii_digit()),
            "invalid series ticker: {s}"
        );
        if !series.iter().any(|x| x == s) {
            series.push(s.to_owned());
        }
    }
    ensure!(
        series.len() <= 20,
        "select at most 20 sports series per experiment"
    );
    Ok(series)
}

fn is_sports(s: &Value) -> bool {
    s["category"].as_str() == Some("Sports")
        || s["categories"]
            .as_array()
            .is_some_and(|xs| xs.iter().any(|x| x.as_str() == Some("Sports")))
}

async fn catalog() -> Result<Vec<Value>> {
    let body: Value = client()?
        .get(format!("{}/series", rest_base()))
        .query(&[("category", "Sports"), ("include_volume", "true")])
        .send()
        .await?
        .error_for_status()?
        .json()
        .await?;
    Ok(body["series"]
        .as_array()
        .context("missing series array")?
        .iter()
        .filter(|s| is_sports(s))
        .cloned()
        .collect())
}

pub async fn scan(out: &Path) -> Result<()> {
    let mut rows = catalog().await?;
    rows.sort_by(|a, b| a["ticker"].as_str().cmp(&b["ticker"].as_str()));
    fs::create_dir_all(out)?;
    let path = out.join(format!("sports_catalog_{}.json", unix_us() / 1000));
    fs::write(
        &path,
        serde_json::to_vec_pretty(&json!({"captured_us":unix_us(), "series":rows}))?,
    )?;
    println!("ticker\tfee_type\tfee_multiplier\tlifetime_volume\ttitle");
    for s in &rows {
        println!(
            "{}\t{}\t{}\t{}\t{}",
            s["ticker"].as_str().unwrap_or(""),
            s["fee_type"].as_str().unwrap_or("unknown"),
            s["fee_multiplier"],
            s["volume_fp"].as_str().unwrap_or("unknown"),
            s["title"].as_str().unwrap_or("")
        );
    }
    eprintln!(
        "{} sports series; catalog {}. Volume is lifetime, not executable liquidity.",
        rows.len(),
        path.display()
    );
    Ok(())
}

pub async fn validate(series: &[String], out: &Path) -> Result<()> {
    let rows = catalog().await?;
    for ticker in series {
        ensure!(
            rows.iter().any(|s| s["ticker"].as_str() == Some(ticker)),
            "{ticker} is not in the current Sports catalog"
        );
    }
    fs::create_dir_all(out)?;
    let selected: Vec<_> = rows
        .into_iter()
        .filter(|s| series.iter().any(|t| s["ticker"].as_str() == Some(t)))
        .collect();
    fs::write(
        out.join(format!("sports_selection_{}.json", unix_us() / 1000)),
        serde_json::to_vec_pretty(&json!({"captured_us":unix_us(), "series":selected}))?,
    )?;
    Ok(())
}

/// Every Sports series that does not bill the maker.
pub async fn free_series() -> Result<HashSet<String>> {
    Ok(catalog().await?.iter()
        .filter(|s| s["fee_type"].as_str() == Some("quadratic"))
        .filter_map(|s| s["ticker"].as_str().map(str::to_owned)).collect())
}

/// fee_type per requested series, from the live catalog.
pub async fn fee_types(series: &[String]) -> Result<Vec<(String, String)>> {
    let rows = catalog().await?;
    Ok(series.iter().map(|t| {
        let fee = rows.iter().find(|s| s["ticker"].as_str() == Some(t))
            .and_then(|s| s["fee_type"].as_str()).unwrap_or("unknown").to_owned();
        (t.clone(), fee)
    }).collect())
}

pub fn quote_allowed(
    bid: i64,
    ask: i64,
    min_spread_c: i64,
    close_ms: i64,
    now_ms: i64,
    stop_s: i64,
) -> bool {
    bid > 0
        && ask < 10_000
        && ask > bid
        && ask - bid >= min_spread_c * 100
        && close_ms > now_ms + stop_s * 1000
}

/// Page every selected series, save raw event linkage/fee/tick metadata, admit up to
/// 256 markets per run. The cap is explicit; this is a bounded experiment, not a census.
pub async fn discover(
    tx: mpsc::Sender<Discovered>,
    series: Vec<String>,
    out: PathBuf,
    tag: Option<String>,
) -> Result<()> {
    let tag = tag.map(|t| format!("-{t}"));
    let http = client()?;
    let mut seen = HashSet::new();
    let mut meta = OpenOptions::new()
        .create_new(true)
        .write(true)
        .open(out.join(format!("sports_markets_{}.jsonl", unix_us())))?;
    loop {
        for series in &series {
            let mut cursor = String::new();
            let mut cursors = HashSet::new();
            loop {
                let response = http
                    .get(format!("{}/markets", rest_base()))
                    .query(&[
                        ("series_ticker", series.as_str()),
                        ("status", "open"),
                        ("limit", "1000"),
                        ("cursor", cursor.as_str()),
                    ])
                    .send()
                    .await;
                let body: Value = match response.and_then(|r| r.error_for_status()) {
                    Ok(r) => match r.json().await {
                        Ok(b) => b,
                        Err(e) => {
                            eprintln!("sports discovery {series}: {e}");
                            break;
                        }
                    },
                    Err(e) => {
                        eprintln!("sports discovery {series}: {e}");
                        break;
                    }
                };
                for m in body["markets"]
                    .as_array()
                    .context("missing markets array")?
                {
                    let Some(ticker) = m["ticker"].as_str() else {
                        continue;
                    };
                    let Some(close) = m["close_time"].as_str().and_then(rfc3339_ms) else {
                        continue;
                    };
                    if tag.as_ref().is_some_and(|t| !ticker.contains(t.as_str())) {
                        continue;
                    }
                    if close <= unix_us() / 1000 + 120_000 || seen.contains(ticker) {
                        continue;
                    }
                    if seen.len() >= 256 {
                        eprintln!(
                            "sports market cap 256 reached; start a separate experiment for more markets"
                        );
                        // Keep the discovery channel open until the run ends.
                        std::future::pending::<()>().await;
                    }
                    writeln!(
                        meta,
                        "{}",
                        json!({"captured_us":unix_us(), "series_ticker":series, "market":m})
                    )?;
                    meta.flush()?;
                    tx.send(Discovered {
                        ticker: ticker.to_owned(),
                        close_unix_ms: close,
                    })
                    .await?;
                    seen.insert(ticker.to_owned());
                }
                cursor = body["cursor"].as_str().unwrap_or("").to_owned();
                if cursor.is_empty() {
                    break;
                }
                ensure!(
                    cursors.insert(cursor.clone()),
                    "repeated sports discovery cursor"
                );
                tokio::time::sleep(Duration::from_millis(100)).await;
            }
            tokio::time::sleep(Duration::from_millis(100)).await;
        }
        tokio::time::sleep(Duration::from_secs(30)).await;
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn sports_category_can_be_secondary() {
        assert!(is_sports(
            &json!({"category":"Other", "categories":["Sports"]})
        ));
        assert!(!is_sports(&json!({"category":"Crypto"})));
    }
    #[test]
    fn selectors_are_bounded_and_deduplicated() {
        assert_eq!(
            parse_series("KXNBA, KXNBA,KXWNBA").unwrap(),
            vec!["KXNBA", "KXWNBA"]
        );
        for bad in ["", "KXNBA,", "KXNBA&status=closed", "kxnba"] {
            assert!(parse_series(bad).is_err());
        }
    }
    #[test]
    fn width_is_cents_and_missing_close_fails_closed() {
        assert!(quote_allowed(4000, 6000, 20, 500_000, 100_000, 120));
        assert!(!quote_allowed(4000, 5990, 20, 500_000, 100_000, 120));
        assert!(!quote_allowed(100, 400, 20, 500_000, 100_000, 120));
        for (b, a, c) in [
            (4000, 6000, 0),
            (4000, 6000, 220_000),
            (6000, 4000, 500_000),
            (0, 6000, 500_000),
            (4000, 10_000, 500_000),
        ] {
            assert!(!quote_allowed(b, a, 20, c, 100_000, 120));
        }
    }
}
