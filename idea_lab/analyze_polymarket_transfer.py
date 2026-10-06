"""Frozen historical Polymarket-to-Kalshi BTC 15-minute transfer screen."""
from __future__ import annotations

import json
import math
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import requests

try:
    from idea_lab.common import day_cluster_se, fee
except ModuleNotFoundError:
    from common import day_cluster_se, fee

KS = (2, 3, 5, 8)
SAMPLE = Path("idea_lab/crypto_leader_history_sample.json")
BTC = Path("/Users/kapil/proj/kalshi-scalp/data/oos_20261006/candles_BTC.json")
CACHE = Path("idea_lab/polymarket_transfer_cache.json")
OUTPUT = Path("idea_lab/polymarket_transfer_report.json")
GAMMA = "https://gamma-api.polymarket.com/events"
HISTORY = "https://clob.polymarket.com/prices-history"


def fetch_one(t0: int) -> tuple[int, dict]:
    session = requests.Session()
    event = session.get(
        GAMMA, params={"slug": f"btc-updown-15m-{t0}"}, timeout=20
    ).json()
    if not event:
        return t0, {"error": "missing_event"}
    market = (event[0].get("markets") or [{}])[0]
    try:
        tokens = json.loads(market["clobTokenIds"])
        outcome_prices = [float(value) for value in json.loads(market["outcomePrices"])]
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return t0, {"error": "bad_market", "market": market}
    response = session.get(
        HISTORY,
        params={
            "market": tokens[0],
            "startTs": t0,
            "endTs": t0 + 900,
            "fidelity": 1,
        },
        timeout=20,
    )
    response.raise_for_status()
    return t0, {
        "slug": market.get("slug"),
        "closed": market.get("closed"),
        "outcome_prices": outcome_prices,
        "history": response.json().get("history") or [],
        "resolution_source": market.get("resolutionSource"),
    }


def load_or_fetch(opens: list[int]) -> dict[str, dict]:
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    missing = [t0 for t0 in opens if str(t0) not in cache]
    with ThreadPoolExecutor(max_workers=12) as pool:
        futures = {pool.submit(fetch_one, t0): t0 for t0 in missing}
        for future in as_completed(futures):
            t0 = futures[future]
            try:
                key, row = future.result()
            except Exception as exc:  # preserve individual download failures
                key, row = t0, {"error": f"{type(exc).__name__}: {exc}"}
            cache[str(key)] = row
            time.sleep(0.01)
    CACHE.write_text(json.dumps(cache, indent=2))
    return cache


def main() -> None:
    sample = json.loads(SAMPLE.read_text()).get("events") or {}
    opens = sorted(
        {int(event["legs"]["BTC"]["open_ts"]) for event in sample.values()}
    )
    btc_rows = json.loads(BTC.read_text())["markets"]
    btc = {int(row["open_ts"]): row for row in btc_rows}
    cache = load_or_fetch(opens)

    agreements = []
    trades = []
    matched = 0
    for t0 in opens:
        market = btc.get(t0)
        poly = cache.get(str(t0)) or {}
        history = poly.get("history") or []
        prices = poly.get("outcome_prices") or []
        if not market or not history or len(prices) != 2:
            continue
        matched += 1
        poly_up = prices[0] > prices[1]
        kalshi_up = market.get("result") == "yes"
        agreements.append(poly_up == kalshi_up)
        bars = {int(row["ts"]): row for row in market.get("bars") or []}
        points = sorted(
            (int(row["t"]), float(row["p"]))
            for row in history
            if row.get("t") is not None and row.get("p") is not None
        )
        for k in KS:
            stamp = t0 + 60 * k
            bar = bars.get(stamp)
            prior = [point for point in points if point[0] <= stamp]
            if not bar or not prior:
                continue
            poly_ts, probability = prior[-1]
            if stamp - poly_ts > 90:
                continue
            yes_ask = float(bar["a"])
            no_ask = 1.0 - float(bar["b"])
            choices = []
            if 0 < yes_ask < 1:
                choices.append(
                    (
                        probability - yes_ask - fee(yes_ask),
                        "yes",
                        yes_ask,
                    )
                )
            if 0 < no_ask < 1:
                choices.append(
                    (
                        (1.0 - probability) - no_ask - fee(no_ask),
                        "no",
                        no_ask,
                    )
                )
            if not choices:
                continue
            edge, side, entry = max(choices)
            if edge <= 0.10:
                continue
            payout = 1.0 if market.get("result") == side else 0.0
            trades.append(
                {
                    "ticker": market["ticker"],
                    "t0": t0,
                    "day": t0 // 86400,
                    "k": k,
                    "poly_ts": poly_ts,
                    "poly_up": probability,
                    "side": side,
                    "entry": entry,
                    "edge": edge,
                    "result": market.get("result"),
                    "pnl": payout - entry - fee(entry),
                }
            )
            break

    pnl = np.asarray([row["pnl"] for row in trades])
    days = np.asarray([row["day"] for row in trades])
    mean = se = None
    if len(pnl):
        mean, se = day_cluster_se(pnl, days)
    split = len(trades) // 2
    day_totals = {
        day: float(pnl[days == day].sum()) for day in sorted(set(days.tolist()))
    }
    remove_n = max(1, math.ceil(0.10 * len(day_totals))) if day_totals else 0
    removed = set(
        sorted(day_totals, key=lambda day: day_totals[day], reverse=True)[:remove_n]
    )
    trimmed = [row["pnl"] for row in trades if row["day"] not in removed]
    gross_by_day = {
        day: sum(max(row["pnl"], 0.0) for row in trades if row["day"] == day)
        for day in day_totals
    }
    gross_total = sum(gross_by_day.values())
    max_day_share = (
        max(gross_by_day.values()) / gross_total if gross_total else None
    )
    report = {
        "preregistration": "PREREG_polymarket_transfer_20261006.md",
        "sample_opens": len(opens),
        "matched_events": matched,
        "outcome_agreement_n": len(agreements),
        "outcome_agreement": float(np.mean(agreements)) if agreements else None,
        "outcome_disagreements": len(agreements) - sum(agreements),
        "trades": len(trades),
        "mean_c": None if mean is None else mean * 100,
        "se_c": None if se is None else se * 100,
        "lo95_c": None if mean is None else (mean - 1.96 * se) * 100,
        "one_cent_stressed_mean_c": None if mean is None else mean * 100 - 1,
        "halves_c": [
            float(np.mean(pnl[:split]) * 100) if split else None,
            float(np.mean(pnl[split:]) * 100) if len(pnl[split:]) else None,
        ],
        "mean_ex_best_10pct_days_c": (
            float(np.mean(trimmed) * 100) if trimmed else None
        ),
        "max_day_gross_positive_share": max_day_share,
        "trade_rows": trades,
    }
    report["passes_paper_screen"] = bool(
        len(trades) >= 40
        and report["outcome_agreement"] is not None
        and report["outcome_agreement"] >= 0.99
        and report["mean_c"] is not None
        and report["mean_c"] > 0
        and report["one_cent_stressed_mean_c"] > 0
        and report["lo95_c"] > 0
        and all(value is not None and value > 0 for value in report["halves_c"])
        and report["mean_ex_best_10pct_days_c"] is not None
        and report["mean_ex_best_10pct_days_c"] > 0
        and max_day_share is not None
        and max_day_share <= 0.20
    )
    OUTPUT.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
