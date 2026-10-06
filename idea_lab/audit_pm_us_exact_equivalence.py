"""Fail-closed metadata audit for PM-US/Kalshi BTC 15-minute pairings."""
from __future__ import annotations

import argparse
import json
import urllib.request
from pathlib import Path

PM = "https://gateway.polymarket.us/v1/market/slug/{slug}"
K = "https://api.elections.kalshi.com/trade-api/v2/markets/{ticker}"


def get(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=10) as response:
        return json.load(response)


def check(pm: dict, kalshi: dict) -> dict:
    terms = pm.get("assetPriceTerms") or {}
    price = terms.get("priceToBeat") or {}
    reasons = []
    if terms.get("indexSymbol") != "BRTI":
        reasons.append("pm_index")
    if terms.get("horizon") != "15m":
        reasons.append("pm_horizon")
    if terms.get("windowStart") != kalshi.get("open_time"):
        reasons.append("window_start")
    if terms.get("windowEnd") != kalshi.get("close_time"):
        reasons.append("window_end")
    try:
        pm_strike = float(price["value"])
        k_strike = float(kalshi["floor_strike"])
        strike_delta = pm_strike - k_strike
        if abs(strike_delta) > 0.005:
            reasons.append("strike")
    except (KeyError, TypeError, ValueError):
        pm_strike = k_strike = strike_delta = None
        reasons.append("missing_strike")
    description = str(pm.get("description") or "")
    if "60 BRTI prices" not in description or "simple average" not in description:
        reasons.append("pm_settlement_text")
    kalshi_rules = str(kalshi.get("rules_primary") or "")
    if "Bitcoin Real Time Index" not in kalshi_rules and "BRTI" not in kalshi_rules:
        reasons.append("kalshi_settlement_text")
    return {
        "equivalent": not reasons,
        "reasons": reasons,
        "pm_window_start": terms.get("windowStart"),
        "pm_window_end": terms.get("windowEnd"),
        "kalshi_open": kalshi.get("open_time"),
        "kalshi_close": kalshi.get("close_time"),
        "pm_strike": pm_strike,
        "kalshi_strike": k_strike,
        "strike_delta": strike_delta,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--input", type=Path,
        default=Path("../polymarket-us-mm/data/xvenue_btc15_20261006.jsonl"),
    )
    parser.add_argument(
        "--output", type=Path,
        default=Path("idea_lab/pm_us_exact_equivalence_report.json"),
    )
    args = parser.parse_args()
    pairs = {}
    for line in args.input.read_text().splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("slug") and row.get("ticker"):
            pairs[(str(row["slug"]), str(row["ticker"]))] = None
    rows = []
    for slug, ticker in sorted(pairs):
        try:
            pm = get(PM.format(slug=slug)).get("market") or {}
            kalshi = get(K.format(ticker=ticker)).get("market") or {}
            rows.append({"slug": slug, "ticker": ticker, **check(pm, kalshi)})
        except Exception as error:
            rows.append({
                "slug": slug, "ticker": ticker, "equivalent": False,
                "reasons": ["fetch_error"], "error": repr(error),
            })
    report = {
        "pairs": len(rows),
        "equivalent_pairs": sum(row["equivalent"] for row in rows),
        "all_equivalent": bool(rows) and all(row["equivalent"] for row in rows),
        "rows": rows,
    }
    args.output.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()

