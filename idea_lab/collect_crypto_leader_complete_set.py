"""Prospective direct-book collector for KXCRYPTOLEAD15M complete sets."""
from __future__ import annotations

import argparse
import gzip
import json
import time
from pathlib import Path

try:
    from idea_lab.collect_public_hourly_dominance import get, parse_ts
    from idea_lab.collect_public_hourly_dominance_orderbook import book
except ModuleNotFoundError:
    from collect_public_hourly_dominance import get, parse_ts
    from collect_public_hourly_dominance_orderbook import book

ASSETS = ("BTC", "ETH", "SOL", "XRP", "HYPE")
SERIES = "KXCRYPTOLEAD15M"
START_AFTER = 1_791_295_548


def active_event(now: int) -> dict | None:
    payload, *_ = get(f"/markets?series_ticker={SERIES}&status=open&limit=20")
    if not payload:
        return None
    groups: dict[str, list[dict]] = {}
    for market in payload.get("markets") or []:
        event = market.get("event_ticker")
        if event:
            groups.setdefault(event, []).append(market)
    candidates = []
    for event, markets in groups.items():
        labels = {str(m.get("yes_sub_title")): m for m in markets}
        if set(labels) != set(ASSETS):
            continue
        try:
            opens = {parse_ts(m["open_time"]) for m in markets}
            closes = {parse_ts(m["close_time"]) for m in markets}
        except (KeyError, TypeError, ValueError):
            continue
        if len(opens) != 1 or len(closes) != 1:
            continue
        opened, close = opens.pop(), closes.pop()
        if opened <= START_AFTER or not opened <= now < close:
            continue
        candidates.append(
            {
                "event_ticker": event,
                "open_ts": opened,
                "close_ts": close,
                "tickers": {asset: labels[asset]["ticker"] for asset in ASSETS},
            }
        )
    return min(candidates, key=lambda row: row["close_ts"]) if candidates else None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration", type=int, default=10_600)
    parser.add_argument("--poll", type=float, default=1.0)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("idea_lab/crypto_leader_complete_set_live_20261006.jsonl.gz"),
    )
    args = parser.parse_args()
    deadline = time.monotonic() + args.duration
    current = None
    next_discovery = 0.0
    with gzip.open(args.output, "at", encoding="utf-8", compresslevel=1) as handle:
        while time.monotonic() < deadline:
            now = int(time.time())
            if time.monotonic() >= next_discovery:
                current = active_event(now)
                next_discovery = time.monotonic() + 5
            if current is None:
                time.sleep(args.poll)
                continue
            row = {
                "kind": "snapshot",
                "wall_ns": time.time_ns(),
                "event": current,
                "legs": {},
            }
            complete = True
            for asset in ASSETS:
                depth, start_ns, finish_ns, error = book(current["tickers"][asset])
                row["legs"][asset] = {
                    "book": depth,
                    "request_start_ns": start_ns,
                    "request_finish_ns": finish_ns,
                    "error": error,
                }
                if (
                    not depth
                    or depth.get("yes_bid") is None
                    or depth.get("no_bid") is None
                ):
                    complete = False
            row["complete"] = complete
            handle.write(json.dumps(row, separators=(",", ":")) + "\n")
            handle.flush()
            time.sleep(args.poll)


if __name__ == "__main__":
    main()
