"""Broad outcome agreement audit for Kalshi BRTI vs Polymarket Chainlink."""
from __future__ import annotations

import json
import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

MARKETS = Path(
    "/Users/kapil/proj/kalshi-scalp/data/oos_20261006/candles_BTC.json"
)
CACHE = Path("idea_lab/crossindex_outcome_cache.json")
OUTPUT = Path("idea_lab/crossindex_outcome_audit_report.json")


def fetch(t0: int) -> tuple[int, dict]:
    last_error = None
    for attempt in range(5):
        try:
            response = requests.get(
                "https://gamma-api.polymarket.com/events",
                params={"slug": f"btc-updown-15m-{t0}"},
                timeout=20,
            )
            response.raise_for_status()
            events = response.json()
            break
        except Exception as exc:
            last_error = exc
            time.sleep(0.25 * (attempt + 1))
    else:
        raise last_error or RuntimeError("download failed")
    if not events:
        return t0, {"error": "missing"}
    market = (events[0].get("markets") or [{}])[0]
    try:
        prices = [float(value) for value in json.loads(market["outcomePrices"])]
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return t0, {"error": "bad_prices"}
    return t0, {
        "closed": bool(market.get("closed")),
        "prices": prices,
        "slug": market.get("slug"),
        "resolution_source": market.get("resolutionSource"),
    }


def wilson(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return math.nan, math.nan
    p = successes / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = (
        z
        * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
        / denom
    )
    return center - half, center + half


def main() -> None:
    markets = [
        row
        for row in json.loads(MARKETS.read_text())["markets"]
        if row.get("result") in {"yes", "no"}
    ]
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    missing = [
        int(row["open_ts"])
        for row in markets
        if str(int(row["open_ts"])) not in cache
        or "error" in cache[str(int(row["open_ts"]))]
    ]
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(fetch, t0): t0 for t0 in missing}
        for future in as_completed(futures):
            t0 = futures[future]
            try:
                key, value = future.result()
            except Exception as exc:
                key, value = t0, {"error": f"{type(exc).__name__}: {exc}"}
            cache[str(key)] = value
    CACHE.write_text(json.dumps(cache, indent=2))

    matched = []
    mismatches = []
    for market in markets:
        t0 = int(market["open_ts"])
        poly = cache.get(str(t0)) or {}
        prices = poly.get("prices") or []
        if not poly.get("closed") or len(prices) != 2 or prices[0] == prices[1]:
            continue
        kalshi = "up" if market["result"] == "yes" else "down"
        polymarket = "up" if prices[0] > prices[1] else "down"
        row = {
            "ticker": market["ticker"],
            "t0": t0,
            "kalshi": kalshi,
            "polymarket": polymarket,
            "kalshi_move_bps": (
                (
                    float(market["expiration_value"])
                    / float(market["floor_strike"])
                    - 1.0
                )
                * 10_000
                if market.get("expiration_value") not in {None, ""}
                and market.get("floor_strike") not in {None, ""}
                else None
            ),
        }
        matched.append(row)
        if kalshi != polymarket:
            mismatches.append(row)
    successes = len(matched) - len(mismatches)
    lo, hi = wilson(successes, len(matched))
    report = {
        "preregistration": "PREREG_crossindex_outcome_audit_20261006.md",
        "population_markets": len(markets),
        "matched": len(matched),
        "agreements": successes,
        "disagreements": len(mismatches),
        "agreement_rate": successes / len(matched) if matched else None,
        "agreement_wilson95": [lo, hi] if matched else [None, None],
        "continues_paper_risk_gate": bool(matched and lo > 0.99),
        "mismatch_rows": mismatches,
    }
    OUTPUT.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
