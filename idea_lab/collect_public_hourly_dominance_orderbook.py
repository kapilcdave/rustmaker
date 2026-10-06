"""Direct-orderbook repair for dominance preregistration Amendment 5."""
from __future__ import annotations

import argparse
import gzip
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

try:
    from idea_lab.collect_public_hourly_dominance import LADDERS, discover, get
except ModuleNotFoundError:
    from collect_public_hourly_dominance import LADDERS, discover, get


def best(levels: list[list[str]]) -> tuple[float, float] | None:
    parsed = [
        (float(price), float(size))
        for price, size in levels
        if float(size) > 0
    ]
    return max(parsed, key=lambda x: x[0]) if parsed else None


def book(ticker: str) -> tuple[dict | None, int, int, str | None]:
    data, start, finish, error = get(f"/markets/{ticker}/orderbook?depth=1")
    if not data:
        return None, start, finish, error
    raw = data.get("orderbook_fp") or data.get("orderbook") or {}
    yes = best(raw.get("yes_dollars") or raw.get("yes") or [])
    no = best(raw.get("no_dollars") or raw.get("no") or [])
    return {
        "ticker": ticker,
        "yes_bid": None if yes is None else yes[0],
        "yes_bid_size": None if yes is None else yes[1],
        "no_bid": None if no is None else no[0],
        "no_bid_size": None if no is None else no[1],
    }, start, finish, error


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--assets", nargs="+", default=list(LADDERS))
    ap.add_argument("--duration", type=int, default=18_000)
    ap.add_argument("--interval", type=float, default=3.0)
    ap.add_argument("--discover-seconds", type=float, default=20.0)
    ap.add_argument(
        "--output",
        default="idea_lab/public_hourly_dominance_orderbook_20261006.jsonl.gz",
    )
    args = ap.parse_args()
    end = time.monotonic() + args.duration
    current: dict[str, dict] = {}
    next_discover = 0.0
    path = Path(args.output)
    with gzip.open(path, "at", encoding="utf-8", compresslevel=1) as out, \
            ThreadPoolExecutor(max_workers=3) as pool:
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
                    name: pool.submit(book, meta[name])
                    for name in ("m15", "lower", "upper")
                }
                for name, future in futures.items():
                    data, start, finish, error = future.result()
                    responses[name] = {
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
            delay = args.interval - (time.monotonic() - loop)
            if delay > 0:
                time.sleep(delay)


if __name__ == "__main__":
    main()
