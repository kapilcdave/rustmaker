#!/usr/bin/env python3
"""Public-REST fallback capture for exact rolling-average endgame research.

Each asset poll records a request interval, the newest one-second settlement
variable, and a contemporaneous top-of-book GET.  Because the two GETs are not
atomic, the row carries both intervals and must not be used for sub-request
latency claims.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import gzip
import json
import signal
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import requests


REST = "https://external-api.kalshi.com/trade-api/v2"
SERIES = {
    "ETH": "KXETH15M",
    "SOL": "KXSOL15M",
    "XRP": "KXXRP15M",
    "DOGE": "KXDOGE15M",
    "BNB": "KXBNB15M",
    "HYPE": "KXHYPE15M",
    "NEAR": "KXNEAR15M",
    "ZEC": "KXZEC15M",
}
BOOK_FIELDS = (
    "ticker",
    "event_ticker",
    "open_time",
    "close_time",
    "floor_strike",
    "result",
    "status",
    "yes_bid_dollars",
    "yes_ask_dollars",
    "yes_bid_size_fp",
    "yes_ask_size_fp",
    "last_price_dollars",
    "volume_fp",
    "open_interest_fp",
    "price_level_structure",
)
_local = threading.local()


def session() -> requests.Session:
    if not hasattr(_local, "session"):
        _local.session = requests.Session()
    return _local.session


def now_us() -> int:
    return time.time_ns() // 1000


def get(url: str, params: Optional[Dict[str, Any]] = None) -> tuple[Dict[str, Any], int, int]:
    start = now_us()
    response = session().get(url, params=params, timeout=12)
    end = now_us()
    response.raise_for_status()
    return response.json(), start, end


def discover(asset: str) -> Optional[Dict[str, Any]]:
    body, start, end = get(
        f"{REST}/markets",
        {"series_ticker": SERIES[asset], "status": "open", "limit": 4},
    )
    markets = body.get("markets") or []
    if not markets:
        return None
    now_ms = int(time.time() * 1000)
    active = []
    for market in markets:
        try:
            close_ms = int(
                datetime.fromisoformat(market["close_time"].replace("Z", "+00:00")).timestamp()
                * 1000
            )
        except Exception:
            continue
        if close_ms >= now_ms - 5000:
            active.append((close_ms, market))
    if not active:
        return None
    market = min(active, key=lambda x: x[0])[1]
    return {
        "asset": asset,
        "market": {k: market.get(k) for k in BOOK_FIELDS},
        "discover_start_us": start,
        "discover_end_us": end,
    }


def poll(asset: str, market: Dict[str, Any], last_ts: int) -> Dict[str, Any]:
    event = market["event_ticker"]
    ticker = market["ticker"]
    live, live_start, live_end = get(f"{REST}/live_data/events/{event}")
    book, book_start, book_end = get(f"{REST}/markets/{ticker}")
    points = live.get("live_data", {}).get("details", {}).get("timeseries", [])
    fresh = [x for x in points if int(x.get("t", 0)) > last_ts]
    market_body = book.get("market", book)
    return {
        "asset": asset,
        "ticker": ticker,
        "event_ticker": event,
        "live_start_us": live_start,
        "live_end_us": live_end,
        "book_start_us": book_start,
        "book_end_us": book_end,
        "points": fresh[-5:],
        "book": {k: market_body.get(k) for k in BOOK_FIELDS},
    }


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--assets", nargs="+", default=["ETH", "SOL", "XRP", "DOGE"])
    p.add_argument("--seconds", type=float, default=3600)
    p.add_argument("--interval", type=float, default=1.0)
    p.add_argument("--discover-seconds", type=float, default=10)
    p.add_argument(
        "--output",
        type=Path,
        default=Path("data/cf_exact/live") / f"public_{int(time.time()*1000)}.jsonl.gz",
    )
    args = p.parse_args()
    assets = [a.upper() for a in args.assets]
    unknown = sorted(set(assets) - SERIES.keys())
    if unknown:
        raise SystemExit(f"unknown assets: {unknown}")

    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    markets: Dict[str, Dict[str, Any]] = {}
    last_ts: Dict[str, int] = {a: 0 for a in assets}
    deadline = time.monotonic() + args.seconds
    next_discovery = 0.0
    rows = 0
    with gzip.open(args.output, "at", encoding="utf-8", compresslevel=1) as handle:
        handle.write(
            json.dumps(
                {
                    "kind": "start",
                    "local_us": now_us(),
                    "assets": assets,
                    "interval": args.interval,
                    "note": "public REST; live-data and book GETs are non-atomic",
                },
                separators=(",", ":"),
            )
            + "\n"
        )
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(4, len(assets))) as pool:
            while not stop.is_set() and time.monotonic() < deadline:
                cycle = time.monotonic()
                if cycle >= next_discovery:
                    futures = {pool.submit(discover, a): a for a in assets}
                    for future, asset in [(f, futures[f]) for f in futures]:
                        try:
                            row = future.result()
                            if row:
                                markets[asset] = row["market"]
                                handle.write(
                                    json.dumps({"kind": "discover", "local_us": now_us(), **row},
                                               separators=(",", ":"))
                                    + "\n"
                                )
                        except Exception as exc:
                            handle.write(
                                json.dumps(
                                    {"kind": "error", "local_us": now_us(), "asset": asset,
                                     "where": "discover", "error": f"{type(exc).__name__}:{exc}"},
                                    separators=(",", ":"),
                                )
                                + "\n"
                            )
                    next_discovery = cycle + args.discover_seconds

                futures = {
                    pool.submit(poll, asset, market, last_ts[asset]): asset
                    for asset, market in markets.items()
                }
                for future, asset in [(f, futures[f]) for f in futures]:
                    try:
                        row = future.result()
                        if row["points"]:
                            last_ts[asset] = max(int(x["t"]) for x in row["points"])
                        handle.write(
                            json.dumps({"kind": "poll", "local_us": now_us(), **row},
                                       separators=(",", ":"))
                            + "\n"
                        )
                        rows += 1
                    except Exception as exc:
                        handle.write(
                            json.dumps(
                                {"kind": "error", "local_us": now_us(), "asset": asset,
                                 "where": "poll", "error": f"{type(exc).__name__}:{exc}"},
                                separators=(",", ":"),
                            )
                            + "\n"
                        )
                if rows % 40 == 0:
                    handle.flush()
                    print(
                        f"rows={rows} assets={len(markets)} "
                        f"remaining_min={(deadline-time.monotonic())/60:.1f}",
                        flush=True,
                    )
                delay = args.interval - (time.monotonic() - cycle)
                if delay > 0:
                    stop.wait(delay)
        handle.write(json.dumps({"kind": "stop", "local_us": now_us(), "rows": rows}) + "\n")
    print(f"wrote {args.output} rows={rows}")


if __name__ == "__main__":
    main()
