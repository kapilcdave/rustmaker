"""Prospective read-only REST collector for Amendment 2.

No authenticated endpoint and no order route are used.
"""
from __future__ import annotations

import argparse
import gzip
import json
import math
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

BASE = "https://api.elections.kalshi.com/trade-api/v2"
LADDERS = {
    "BTC": "KXBTCD",
    "ETH": "KXETHD",
    "SOL": "KXSOLD",
    "XRP": "KXXRPD",
    "DOGE": "KXDOGED",
    "BNB": "KXBNBD",
    "HYPE": "KXHYPED",
}


def parse_ts(value: str) -> int:
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())


def get(route: str) -> tuple[dict | None, int, int, str | None]:
    start = time.time_ns()
    try:
        req = urllib.request.Request(BASE + route, headers={"User-Agent": "kalshi-mm15-readonly/1"})
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.load(response)
        return data, start, time.time_ns(), None
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return None, start, time.time_ns(), f"{type(exc).__name__}: {exc}"


def strike(market: dict) -> float:
    raw = market.get("floor_strike")
    if raw not in (None, "None", ""):
        return float(str(raw).replace(",", ""))
    match = re.search(r"-T(-?[0-9]+(?:\.[0-9]+)?)$", market["ticker"])
    if not match:
        raise ValueError(market["ticker"])
    return float(match.group(1))


def discover(asset: str, now: int) -> dict | None:
    series_15 = f"KX{asset}15M"
    data, *_ = get(f"/markets?series_ticker={series_15}&status=open&limit=20")
    if not data:
        return None
    active = []
    for m in data.get("markets") or []:
        try:
            op, close = parse_ts(m["open_time"]), parse_ts(m["close_time"])
        except (KeyError, ValueError):
            continue
        if op <= now < close and close % 3600 == 0:
            active.append((close, m))
    if not active:
        return None
    close, m15 = min(active)
    qs = urllib.parse.urlencode(
        {
            "series_ticker": LADDERS[asset],
            "status": "open",
            "min_close_ts": close,
            "max_close_ts": close,
            "limit": 1000,
        }
    )
    hourly, *_ = get("/markets?" + qs)
    if not hourly:
        return None
    exact_close = [
        m for m in hourly.get("markets") or []
        if parse_ts(m["close_time"]) == close and "-T" in m.get("ticker", "")
    ]
    k15 = strike(m15)
    lower = [m for m in exact_close if strike(m) < k15]
    upper = [m for m in exact_close if strike(m) >= k15]
    if not lower or not upper:
        return None
    lo = max(lower, key=strike)
    hi = min(upper, key=strike)
    return {
        "asset": asset,
        "close_ts": close,
        "m15": m15["ticker"],
        "k15": k15,
        "m15_close_time": m15.get("close_time"),
        "m15_rules_primary": m15.get("rules_primary"),
        "lower": lo["ticker"],
        "lower_strike": strike(lo),
        "lower_close_time": lo.get("close_time"),
        "lower_rules_primary": lo.get("rules_primary"),
        "upper": hi["ticker"],
        "upper_strike": strike(hi),
        "upper_close_time": hi.get("close_time"),
        "upper_rules_primary": hi.get("rules_primary"),
    }


def slim(market: dict) -> dict:
    return {
        k: market.get(k)
        for k in (
            "ticker",
            "close_time",
            "floor_strike",
            "result",
            "expiration_value",
            "rules_primary",
            "yes_bid_dollars",
            "yes_ask_dollars",
            "yes_bid_size_fp",
            "yes_ask_size_fp",
            "no_bid_dollars",
            "no_ask_dollars",
            "no_bid_size_fp",
            "no_ask_size_fp",
        )
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--assets", nargs="+", default=list(LADDERS))
    ap.add_argument("--duration", type=int, default=24_000)
    ap.add_argument("--interval", type=float, default=3.0)
    ap.add_argument("--discover-seconds", type=float, default=20.0)
    ap.add_argument("--output", default="idea_lab/public_hourly_dominance_20261006.jsonl.gz")
    args = ap.parse_args()
    unknown = set(args.assets) - set(LADDERS)
    if unknown:
        raise SystemExit(f"unknown assets: {sorted(unknown)}")

    end = time.monotonic() + args.duration
    current: dict[str, dict] = {}
    next_discover = 0.0
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "at", encoding="utf-8") as out, ThreadPoolExecutor(max_workers=3) as pool:
        while time.monotonic() < end:
            loop = time.monotonic()
            now = int(time.time())
            if loop >= next_discover:
                for asset in args.assets:
                    found = discover(asset, now)
                    if found:
                        current[asset] = found
                    elif asset in current and now >= current[asset]["close_ts"]:
                        current.pop(asset, None)
                    time.sleep(0.12)
                next_discover = loop + args.discover_seconds
            for asset, meta in list(current.items()):
                if now >= meta["close_ts"]:
                    current.pop(asset, None)
                    continue
                responses = {}
                futures = {
                    name: pool.submit(get, f"/markets/{meta[name]}")
                    for name in ("m15", "lower", "upper")
                }
                for name, future in futures.items():
                    data, start, finish, error = future.result()
                    responses[name] = {
                        "request_start_ns": start,
                        "request_finish_ns": finish,
                        "error": error,
                        "market": slim(data["market"]) if data and data.get("market") else None,
                    }
                out.write(
                    json.dumps(
                        {
                            "kind": "snapshot",
                            "wall_ns": time.time_ns(),
                            "meta": meta,
                            "responses": responses,
                        },
                        separators=(",", ":"),
                    )
                    + "\n"
                )
                out.flush()
            delay = args.interval - (time.monotonic() - loop)
            if delay > 0:
                time.sleep(delay)


if __name__ == "__main__":
    main()
