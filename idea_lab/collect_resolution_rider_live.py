"""Prospective direct-book collector for the fixed BTC resolution rider."""
from __future__ import annotations

import argparse
import gzip
import json
import math
import sys
import time
from pathlib import Path

try:
    from idea_lab.collect_altspot_tilt_live import active_market
    from idea_lab.collect_public_hourly_dominance_orderbook import book
except ModuleNotFoundError:
    from collect_altspot_tilt_live import active_market
    from collect_public_hourly_dominance_orderbook import book

sys.path.insert(0, "/Users/kapil/proj/kalshi-scalp")
from fetch_cb_minutes import fetch_chunk  # type: ignore

KS = (12, 13, 14)
MAX_LAG = 15


def spot(asset: str, t0: int, k: int) -> tuple[dict | None, int, int]:
    started = time.time_ns()
    rows = fetch_chunk(f"{asset}-USD", t0 + 60 * (k - 3), t0 + 60 * k)
    finished = time.time_ns()
    closes = {int(row[0]): float(row[4]) for row in rows}
    stamp = t0 + 60 * (k - 1)
    if stamp not in closes or stamp - 60 not in closes:
        return None, started, finished
    return {
        "candle_ts": stamp,
        "spot": closes[stamp],
        "previous_spot": closes[stamp - 60],
        "momentum_pct": (closes[stamp] / closes[stamp - 60] - 1.0) * 100,
    }, started, finished


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration", type=int, default=7_200)
    parser.add_argument("--poll", type=float, default=1.0)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("idea_lab/resolution_rider_live_20261006.jsonl.gz"),
    )
    args = parser.parse_args()
    deadline = time.monotonic() + args.duration
    current = None
    next_discovery = 0.0
    evaluated = set()
    signaled = set()
    with gzip.open(args.output, "at", encoding="utf-8", compresslevel=1) as out:
        while time.monotonic() < deadline:
            loop = time.monotonic()
            now = int(time.time())
            if loop >= next_discovery:
                current = active_market("BTC", now)
                next_discovery = loop + 5
            if not current:
                time.sleep(args.poll)
                continue
            t0 = int(current["open_ts"])
            close = int(current["close_ts"])
            for k in KS:
                key = (current["ticker"], k)
                boundary = t0 + 60 * k
                if key in evaluated or now < boundary + 2:
                    continue
                if now > boundary + MAX_LAG:
                    evaluated.add(key)
                    out.write(
                        json.dumps(
                            {
                                "kind": "decision",
                                "wall_ns": time.time_ns(),
                                "market": current,
                                "k": k,
                                "seconds_left": close - boundary,
                                "complete": False,
                                "reason": "missed_boundary",
                            },
                            separators=(",", ":"),
                        )
                        + "\n"
                    )
                    out.flush()
                    continue
                evaluated.add(key)
                state, spot_start, spot_finish = spot("BTC", t0, k)
                depth, book_start, book_finish, error = book(current["ticker"])
                row = {
                    "kind": "decision",
                    "wall_ns": time.time_ns(),
                    "market": current,
                    "k": k,
                    "seconds_left": close - boundary,
                    "spot_request_start_ns": spot_start,
                    "spot_request_finish_ns": spot_finish,
                    "book_request_start_ns": book_start,
                    "book_request_finish_ns": book_finish,
                    "book_error": error,
                    "spot_state": state,
                    "book": depth,
                    "complete": False,
                    "signal": False,
                }
                if state and depth and depth.get("yes_bid") is not None and depth.get("no_bid") is not None:
                    yes_ask = 1.0 - float(depth["no_bid"])
                    no_ask = 1.0 - float(depth["yes_bid"])
                    if yes_ask >= no_ask:
                        side, entry = "yes", yes_ask
                        displayed = depth.get("no_bid_size")
                        signed_buffer = (
                            (state["spot"] - float(current["strike"]))
                            / float(current["strike"])
                            * 100
                        )
                        momentum_ok = state["momentum_pct"] >= -0.04
                    else:
                        side, entry = "no", no_ask
                        displayed = depth.get("yes_bid_size")
                        signed_buffer = (
                            (float(current["strike"]) - state["spot"])
                            / float(current["strike"])
                            * 100
                        )
                        momentum_ok = state["momentum_pct"] <= 0.04
                    required = 0.10 * math.sqrt(row["seconds_left"] / 60)
                    row.update(
                        {
                            "complete": True,
                            "side": side,
                            "entry": entry,
                            "displayed_size": displayed,
                            "signed_buffer_pct": signed_buffer,
                            "required_buffer_pct": required,
                            "momentum_ok": momentum_ok,
                        }
                    )
                    row["signal"] = bool(
                        current["ticker"] not in signaled
                        and 0.94 <= entry <= 0.97
                        and signed_buffer >= required
                        and momentum_ok
                    )
                    if row["signal"]:
                        signaled.add(current["ticker"])
                out.write(json.dumps(row, separators=(",", ":")) + "\n")
                out.flush()
            time.sleep(max(0.0, args.poll - (time.monotonic() - loop)))


if __name__ == "__main__":
    main()
