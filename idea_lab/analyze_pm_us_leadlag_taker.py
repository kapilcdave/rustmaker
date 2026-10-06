"""Score a preregistered directional lead/lag signal on the exact PM-US/Kalshi pair."""
from __future__ import annotations

import argparse
import json
import urllib.request
from collections import defaultdict
from pathlib import Path

try:
    from idea_lab.analyze_pm_us_exact_pair_live import (
        _best_ask,
        _best_bid,
        kalshi_fee,
        load,
        pm_fee,
    )
except ModuleNotFoundError:
    from analyze_pm_us_exact_pair_live import (
        _best_ask,
        _best_bid,
        kalshi_fee,
        load,
        pm_fee,
    )

CUTOFF_TS = 1_791_302_550.933293
MIN_EDGE = 0.03
MAX_PM_AGE_S = 0.010
MAX_GAP_S = 1.5


def opportunities(row: dict) -> list[dict]:
    if row.get("err") or row.get("pm_err"):
        return []
    if row.get("pm_state") != "MARKET_STATE_OPEN":
        return []
    if abs(float(row.get("pm_age", 99.0))) > MAX_PM_AGE_S:
        return []
    pb = _best_bid(row.get("pm_bids") or [])
    pa = _best_ask(row.get("pm_asks") or [])
    ky = _best_bid(row.get("k_yes_bids") or [])
    kn = _best_bid(row.get("k_no_bids") or [])
    if not all((pb, pa, ky, kn)):
        return []
    p_yes_bid, p_yes_bid_size = pb
    p_yes_ask, p_yes_ask_size = pa
    k_yes_bid, k_yes_bid_size = ky
    k_no_bid, k_no_bid_size = kn
    k_yes_ask, k_no_ask = 1.0 - k_no_bid, 1.0 - k_yes_bid
    p_no_bid, p_no_ask = 1.0 - p_yes_ask, 1.0 - p_yes_bid
    raw = [
        ("kalshi_yes", "kalshi", "yes", k_yes_ask, kalshi_fee(k_yes_ask),
         p_yes_bid, min(k_no_bid_size, p_yes_bid_size)),
        ("kalshi_no", "kalshi", "no", k_no_ask, kalshi_fee(k_no_ask),
         p_no_bid, min(k_yes_bid_size, p_yes_ask_size)),
        ("pm_yes", "pm_us", "yes", p_yes_ask, pm_fee(p_yes_ask),
         k_yes_bid, min(p_yes_ask_size, k_yes_bid_size)),
        ("pm_no", "pm_us", "no", p_no_ask, pm_fee(p_no_ask),
         k_no_bid, min(p_yes_bid_size, k_no_bid_size)),
    ]
    return [
        {
            "direction": direction,
            "venue": venue,
            "side": side,
            "entry": entry,
            "fee": fee,
            "reference_bid": reference,
            "displayed_size": size,
            "edge": reference - entry - fee,
        }
        for direction, venue, side, entry, fee, reference, size in raw
        if size >= 1.0
    ]


def fetch_results(tickers: set[str]) -> dict[str, str]:
    out = {}
    base = "https://api.elections.kalshi.com/trade-api/v2/markets/"
    for ticker in sorted(tickers):
        try:
            with urllib.request.urlopen(base + ticker, timeout=5) as response:
                market = json.load(response).get("market") or {}
            result = str(market.get("result") or "").lower()
            if result in ("yes", "no"):
                out[ticker] = result
        except Exception:
            continue
    return out


def score(rows: list[dict], results: dict[str, str] | None = None) -> dict:
    eligible = []
    for row in rows:
        found = opportunities(row)
        if found:
            eligible.append(
                {
                    "ts": float(row["ts"]),
                    "slug": str(row["slug"]),
                    "ticker": str(row["ticker"]),
                    "pm_age_ms": float(row["pm_age"]) * 1000.0,
                    "opportunities": found,
                }
            )
    prior: dict[tuple[str, str], tuple[float, float]] = {}
    seen: set[str] = set()
    signals = []
    for index, row in enumerate(eligible):
        for item in row["opportunities"]:
            key = (row["slug"], item["direction"])
            previous = prior.get(key)
            prior[key] = (row["ts"], item["edge"])
            if row["slug"] in seen or item["edge"] < MIN_EDGE or previous is None:
                continue
            if previous[1] < MIN_EDGE or row["ts"] - previous[0] > MAX_GAP_S:
                continue
            next_edge = None
            for later in eligible[index + 1:]:
                if later["slug"] != row["slug"] or later["ts"] - row["ts"] > MAX_GAP_S:
                    break
                match = next(
                    (x for x in later["opportunities"]
                     if x["direction"] == item["direction"]),
                    None,
                )
                if match is not None:
                    next_edge = float(match["edge"])
                    break
            signals.append(
                {
                    **item,
                    "slug": row["slug"],
                    "ticker": row["ticker"],
                    "ts": row["ts"],
                    "pm_age_ms": row["pm_age_ms"],
                    "edge_c": item["edge"] * 100.0,
                    "previous_edge_c": previous[1] * 100.0,
                    "next_sample_edge_c": (
                        None if next_edge is None else next_edge * 100.0
                    ),
                    "next_sample_survives": (
                        next_edge is not None and next_edge >= 0.0
                    ),
                }
            )
            seen.add(row["slug"])
            break

    results = results or {}
    settled = []
    for item in signals:
        result = results.get(item["ticker"])
        if result not in ("yes", "no"):
            continue
        pnl = float(result == item["side"]) - item["entry"] - item["fee"]
        settled.append({**item, "result": result, "pnl": pnl})
    pnls = [item["pnl"] for item in settled]
    split = len(pnls) // 2
    gross = defaultdict(float)
    for item in settled:
        gross[item["direction"]] += max(0.0, item["pnl"])
    gross_total = sum(gross.values())
    next_known = [x for x in signals if x["next_sample_edge_c"] is not None]
    survival = (
        sum(x["next_sample_survives"] for x in next_known) / len(next_known)
        if next_known else None
    )
    report = {
        "preregistration": "PREREG_pm_us_leadlag_taker_20261006.md",
        "cutoff_ts": CUTOFF_TS,
        "raw_rows": len(rows),
        "fresh_paired_rows": len(eligible),
        "persistent_signal_windows": len(signals),
        "settled": len(settled),
        "mean_c": sum(pnls) / len(pnls) * 100.0 if pnls else None,
        "extra_1c_stress_c": (
            sum(pnls) / len(pnls) * 100.0 - 1.0 if pnls else None
        ),
        "halves_c": [
            sum(pnls[:split]) / split * 100.0 if split else None,
            (sum(pnls[split:]) / len(pnls[split:]) * 100.0
             if pnls[split:] else None),
        ],
        "next_sample_known": len(next_known),
        "next_sample_survival": survival,
        "minimum_displayed_size": (
            min(x["displayed_size"] for x in signals) if signals else None
        ),
        "gross_positive_shares": {
            key: value / gross_total for key, value in gross.items()
        } if gross_total else {},
        "signals": signals,
        "settled_rows": settled,
    }
    report["passes_short_screen"] = bool(
        len(settled) >= 5
        and report["mean_c"] is not None and report["mean_c"] > 0
        and report["extra_1c_stress_c"] > 0
        and all(x is not None and x > 0 for x in report["halves_c"])
        and survival is not None and survival >= 0.80
        and all(x <= 0.60 for x in report["gross_positive_shares"].values())
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--input", type=Path,
        default=Path("../polymarket-us-mm/data/xvenue_btc15_20261006.jsonl"),
    )
    parser.add_argument(
        "--output", type=Path,
        default=Path("idea_lab/pm_us_leadlag_taker_report.json"),
    )
    parser.add_argument("--cutoff", type=float, default=CUTOFF_TS)
    parser.add_argument("--fetch-outcomes", action="store_true")
    args = parser.parse_args()
    rows = load(args.input, args.cutoff)
    tickers = {str(row.get("ticker")) for row in rows if row.get("ticker")}
    results = fetch_results(tickers) if args.fetch_outcomes else {}
    report = score(rows, results)
    args.output.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()

