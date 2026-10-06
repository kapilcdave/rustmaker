"""Prospective direct-book collector for the fixed external Mystic rule."""
from __future__ import annotations

import argparse
import gzip
import json
import sys
import time
from pathlib import Path

try:
    from idea_lab.collect_altspot_tilt_live import active_market
    from idea_lab.collect_public_hourly_dominance_orderbook import book
    from idea_lab.analyze_mystic_done_deal_oos import aligned_and_no_wick
except ModuleNotFoundError:
    from collect_altspot_tilt_live import active_market
    from collect_public_hourly_dominance_orderbook import book
    from analyze_mystic_done_deal_oos import aligned_and_no_wick

sys.path.insert(0, "/Users/kapil/proj/kalshi-scalp")
from fetch_cb_minutes import fetch_chunk  # type: ignore

KS = (10, 11, 12, 13)


def spot_state(t0: int, k: int) -> tuple[dict | None, int, int]:
    started = time.time_ns()
    rows = fetch_chunk("BTC-USD", t0 - 60, t0 + 60 * k)
    finished = time.time_ns()
    spot = {
        int(row[0]): (float(row[3]), float(row[4]), float(row[1]), float(row[2]))
        for row in rows
    }
    stamp = t0 + 60 * (k - 1)
    if t0 not in spot or stamp not in spot:
        return None, started, finished
    return {
        "stamp": stamp,
        "spot": spot[stamp][1],
        "window_open": spot[t0][0],
        "candles": {
            str(ts): values
            for ts, values in spot.items()
            if stamp - 180 <= ts <= stamp
        },
        "aligned_up": aligned_and_no_wick(spot, stamp, "up", spot[stamp][1]),
        "aligned_down": aligned_and_no_wick(
            spot, stamp, "down", spot[stamp][1]
        ),
    }, started, finished


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration", type=int, default=7_000)
    parser.add_argument("--poll", type=float, default=1.0)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("idea_lab/mystic_done_deal_live_20261006.jsonl.gz"),
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
                if now > boundary + 15:
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
                state, spot_start, spot_finish = spot_state(t0, k)
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
                        side, ask = "yes", yes_ask
                        distance = (
                            state["spot"] / state["window_open"] - 1.0
                        ) * 100
                        aligned = state["aligned_up"]
                        displayed = depth.get("no_bid_size")
                    else:
                        side, ask = "no", no_ask
                        distance = (
                            state["window_open"] / state["spot"] - 1.0
                        ) * 100
                        aligned = state["aligned_down"]
                        displayed = depth.get("yes_bid_size")
                    required = 0.10 if row["seconds_left"] > 210 else 0.06
                    row.update(
                        {
                            "complete": True,
                            "side": side,
                            "entry": ask,
                            "distance_pct": distance,
                            "required_distance_pct": required,
                            "aligned_and_no_wick": aligned,
                            "displayed_size": displayed,
                        }
                    )
                    row["signal"] = bool(
                        current["ticker"] not in signaled
                        and 0.93 <= ask <= 0.95
                        and distance >= required
                        and aligned
                    )
                    if row["signal"]:
                        signaled.add(current["ticker"])
                out.write(json.dumps(row, separators=(",", ":")) + "\n")
                out.flush()
            time.sleep(max(0.0, args.poll - (time.monotonic() - loop)))


if __name__ == "__main__":
    main()
