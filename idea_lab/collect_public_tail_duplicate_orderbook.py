"""Prospective public orderbook capture for exact hourly tail duplicates."""
from __future__ import annotations

import argparse
import gzip
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

try:
    from idea_lab.collect_public_hourly_dominance import get
    from idea_lab.collect_public_hourly_dominance_orderbook import book
except ModuleNotFoundError:
    from collect_public_hourly_dominance import get
    from collect_public_hourly_dominance_orderbook import book

SERIES = {
    "BTC": ("KXBTC", "KXBTCD"),
    "ETH": ("KXETH", "KXETHD"),
    "XRP": ("KXXRP", "KXXRPD"),
    "BNB": ("KXBNB", "KXBNBD"),
    "HYPE": ("KXHYPE", "KXHYPED"),
}


def parse_ts(value: str) -> int:
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())


def discover(asset: str, now: int) -> dict | None:
    range_series, directional_series = SERIES[asset]
    data, *_ = get(
        f"/markets?series_ticker={range_series}&status=open&min_close_ts={now}"
        f"&max_close_ts={now + 7200}&limit=1000"
    )
    if not data:
        return None
    tails = [
        market
        for market in data.get("markets") or []
        if market.get("floor_strike") not in (None, "None", "")
        and market.get("cap_strike") in (None, "None", "")
        and parse_ts(market["close_time"]) > now
    ]
    if not tails:
        return None
    left = min(tails, key=lambda market: parse_ts(market["close_time"]))
    suffix = left["ticker"].split("-", 1)[1]
    right_ticker = f"{directional_series}-{suffix}"
    detail, *_ = get(f"/markets/{right_ticker}")
    if not detail or not detail.get("market"):
        return None
    right = detail["market"]
    identity_ok = bool(
        left.get("rules_primary")
        and left.get("rules_primary") == right.get("rules_primary")
        and left.get("close_time") == right.get("close_time")
        and str(left.get("floor_strike")).replace(",", "")
        == str(right.get("floor_strike")).replace(",", "")
    )
    if not identity_ok:
        return None
    return {
        "asset": asset,
        "close_ts": parse_ts(left["close_time"]),
        "left": left["ticker"],
        "right": right_ticker,
        "strike": left.get("floor_strike"),
        "rules_primary": left.get("rules_primary"),
        "close_time": left.get("close_time"),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--duration", type=int, default=17_400)
    ap.add_argument("--interval", type=float, default=5.0)
    ap.add_argument("--discover-seconds", type=float, default=30.0)
    ap.add_argument(
        "--output",
        type=Path,
        default=Path("idea_lab/public_tail_duplicate_orderbook_20261006.jsonl.gz"),
    )
    args = ap.parse_args()
    deadline = time.monotonic() + args.duration
    current = {}
    next_discover = 0.0
    with gzip.open(args.output, "at", encoding="utf-8", compresslevel=1) as out, \
            ThreadPoolExecutor(max_workers=4) as pool:
        while time.monotonic() < deadline:
            cycle = time.monotonic()
            now = int(time.time())
            if cycle >= next_discover:
                for asset in SERIES:
                    found = discover(asset, now)
                    if found:
                        current[asset] = found
                    elif asset in current and now >= current[asset]["close_ts"]:
                        current.pop(asset, None)
                    time.sleep(0.1)
                next_discover = cycle + args.discover_seconds
            for asset, meta in list(current.items()):
                if now >= meta["close_ts"]:
                    current.pop(asset, None)
                    continue
                futures = {leg: pool.submit(book, meta[leg]) for leg in ("left", "right")}
                responses = {}
                for leg, future in futures.items():
                    data, start, finish, error = future.result()
                    responses[leg] = {
                        "request_start_ns": start,
                        "request_finish_ns": finish,
                        "error": error,
                        "book": data,
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
            delay = args.interval - (time.monotonic() - cycle)
            if delay > 0:
                time.sleep(delay)


if __name__ == "__main__":
    main()
