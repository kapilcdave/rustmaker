"""Read-only direct-book collector for paired Kalshi/Polymarket BTC 15m."""
from __future__ import annotations

import argparse
import gzip
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

try:
    from idea_lab.collect_altspot_tilt_live import active_market
    from idea_lab.collect_public_hourly_dominance_orderbook import book
except ModuleNotFoundError:
    from collect_altspot_tilt_live import active_market
    from collect_public_hourly_dominance_orderbook import book


def poly_market(t0: int) -> dict | None:
    response = requests.get(
        "https://gamma-api.polymarket.com/events",
        params={"slug": f"btc-updown-15m-{t0}"},
        timeout=10,
    )
    response.raise_for_status()
    events = response.json()
    if not events:
        return None
    market = (events[0].get("markets") or [{}])[0]
    try:
        tokens = json.loads(market["clobTokenIds"])
    except (KeyError, TypeError, json.JSONDecodeError):
        return None
    return {
        "slug": market.get("slug"),
        "up_token": tokens[0],
        "down_token": tokens[1],
        "resolution_source": market.get("resolutionSource"),
    }


def poly_book(token: str) -> tuple[dict | None, int, int, str | None]:
    started = time.time_ns()
    try:
        response = requests.get(
            "https://clob.polymarket.com/book",
            params={"token_id": token},
            timeout=5,
        )
        response.raise_for_status()
        payload = response.json()
        asks = [
            (float(row["price"]), float(row["size"]))
            for row in payload.get("asks") or []
            if float(row["size"]) > 0
        ]
        bids = [
            (float(row["price"]), float(row["size"]))
            for row in payload.get("bids") or []
            if float(row["size"]) > 0
        ]
        return {
            "best_ask": min(asks)[0] if asks else None,
            "best_ask_size": min(asks)[1] if asks else None,
            "best_bid": max(bids)[0] if bids else None,
            "best_bid_size": max(bids)[1] if bids else None,
            "source_timestamp_ms": payload.get("timestamp"),
        }, started, time.time_ns(), None
    except Exception as exc:
        return None, started, time.time_ns(), f"{type(exc).__name__}: {exc}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration", type=int, default=7_400)
    parser.add_argument("--interval", type=float, default=2.0)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("idea_lab/polymarket_crossvenue_live_20261006.jsonl.gz"),
    )
    args = parser.parse_args()
    deadline = time.monotonic() + args.duration
    current = None
    poly = None
    next_discovery = 0.0
    with gzip.open(args.output, "at", encoding="utf-8", compresslevel=1) as out, \
            ThreadPoolExecutor(max_workers=3) as pool:
        while time.monotonic() < deadline:
            loop = time.monotonic()
            now = int(time.time())
            if loop >= next_discovery:
                found = active_market("BTC", now)
                if found and (
                    current is None or found["ticker"] != current["ticker"]
                ):
                    current = found
                    try:
                        poly = poly_market(int(found["open_ts"]))
                    except Exception:
                        poly = None
                next_discovery = loop + 5
            if not current or not poly or now >= int(current["close_ts"]):
                time.sleep(min(args.interval, 1.0))
                continue
            futures = {
                "kalshi": pool.submit(book, current["ticker"]),
                "poly_up": pool.submit(poly_book, poly["up_token"]),
                "poly_down": pool.submit(poly_book, poly["down_token"]),
            }
            responses = {}
            for name, future in futures.items():
                data, start_ns, finish_ns, error = future.result()
                responses[name] = {
                    "data": data,
                    "request_start_ns": start_ns,
                    "request_finish_ns": finish_ns,
                    "error": error,
                }
            out.write(
                json.dumps(
                    {
                        "kind": "snapshot",
                        "wall_ns": time.time_ns(),
                        "kalshi_market": current,
                        "polymarket": poly,
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
