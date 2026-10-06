"""Read-only direct-book journal for the causally aligned cross-asset rule."""
from __future__ import annotations

import argparse
import gzip
import json
import time
from pathlib import Path

try:
    from idea_lab.analyze_external_crossasset_regime import STATE_ACTION, feature
    from idea_lab.collect_public_hourly_dominance import get, parse_ts
    from idea_lab.collect_public_hourly_dominance_orderbook import book
except ModuleNotFoundError:
    from analyze_external_crossasset_regime import STATE_ACTION, feature
    from collect_public_hourly_dominance import get, parse_ts
    from collect_public_hourly_dominance_orderbook import book

import sys

sys.path.insert(0, "/Users/kapil/proj/kalshi-scalp")
from fetch_cb_minutes import fetch_chunk  # type: ignore


def active_market(now: int) -> dict | None:
    data, *_ = get("/markets?series_ticker=KXBTC15M&status=open&limit=8")
    candidates = []
    for market in (data or {}).get("markets") or []:
        try:
            close_ts = parse_ts(market["close_time"])
        except (KeyError, TypeError, ValueError):
            continue
        open_ts = close_ts - 900
        if open_ts <= now < close_ts:
            candidates.append((close_ts, market))
    if not candidates:
        return None
    close_ts, market = min(candidates, key=lambda item: item[0])
    return {
        "ticker": market["ticker"],
        "event_ticker": market.get("event_ticker"),
        "open_ts": close_ts - 900,
        "close_ts": close_ts,
    }


def spot_series(product: str, start: int, end: int) -> tuple[dict[int, float], int, int]:
    request_start = time.time_ns()
    rows = fetch_chunk(product, start, end)
    request_finish = time.time_ns()
    values = {
        int(row[0]): float(row[4])
        for row in rows
        if isinstance(row, list) and len(row) >= 5 and int(row[0]) + 60 <= end
    }
    return values, request_start, request_finish


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration", type=int, default=6000)
    parser.add_argument("--poll", type=float, default=0.5)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("idea_lab/external_crossasset_regime_live_20261006.jsonl.gz"),
    )
    args = parser.parse_args()
    deadline = time.monotonic() + args.duration
    current = None
    evaluated: set[str] = set()
    pending: list[dict] = []
    next_discover = 0.0
    with gzip.open(args.output, "at", encoding="utf-8", compresslevel=1) as out:
        out.write(json.dumps({
            "kind": "start",
            "wall_ns": time.time_ns(),
            "preregistration": "PREREG_external_crossasset_regime_live_20261006.md",
        }) + "\n")
        out.flush()
        while time.monotonic() < deadline:
            now = time.time()
            if time.monotonic() >= next_discover:
                current = active_market(int(now))
                next_discover = time.monotonic() + 8.0
            if current and current["ticker"] not in evaluated:
                decision_ts = current["open_ts"] + 6 * 60
                if decision_ts + 2 <= now <= decision_ts + 18:
                    btc, bs, bf = spot_series(
                        "BTC-USD", current["open_ts"] - 32 * 60, decision_ts
                    )
                    eth, es, ef = spot_series(
                        "ETH-USD", current["open_ts"] - 32 * 60, decision_ts
                    )
                    found = feature(current, btc, eth)
                    depth, ks, kf, error = book(current["ticker"])
                    state = None if found is None else found["state"]
                    side = None if state is None else STATE_ACTION.get(state)
                    record = {
                        "kind": "decision",
                        "wall_ns": time.time_ns(),
                        "market": current,
                        "decision_ts": decision_ts,
                        "lag_s": now - decision_ts,
                        "btc_request_ns": [bs, bf],
                        "eth_request_ns": [es, ef],
                        "book_request_ns": [ks, kf],
                        "book_error": error,
                        "feature": found,
                        "side": side,
                        "book": depth,
                    }
                    out.write(json.dumps(record, separators=(",", ":")) + "\n")
                    out.flush()
                    evaluated.add(current["ticker"])
                    if side is not None:
                        for offset in (5, 15):
                            pending.append({
                                "due": decision_ts + offset,
                                "offset_s": offset,
                                "market": current,
                                "side": side,
                                "state": state,
                            })
                elif now > decision_ts + 18:
                    evaluated.add(current["ticker"])
                    out.write(json.dumps({
                        "kind": "missed",
                        "wall_ns": time.time_ns(),
                        "market": current,
                        "lag_s": now - decision_ts,
                    }, separators=(",", ":")) + "\n")
                    out.flush()
            for task in list(pending):
                if now < task["due"]:
                    continue
                depth, ks, kf, error = book(task["market"]["ticker"])
                out.write(json.dumps({
                    "kind": "markout",
                    "wall_ns": time.time_ns(),
                    "market": task["market"],
                    "state": task["state"],
                    "side": task["side"],
                    "offset_s": task["offset_s"],
                    "book_request_ns": [ks, kf],
                    "book_error": error,
                    "book": depth,
                }, separators=(",", ":")) + "\n")
                out.flush()
                pending.remove(task)
            time.sleep(args.poll)
        out.write(json.dumps({
            "kind": "stop",
            "wall_ns": time.time_ns(),
            "evaluated": len(evaluated),
        }) + "\n")


if __name__ == "__main__":
    main()

