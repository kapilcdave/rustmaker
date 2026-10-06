"""Collect the preregistered BTC minute-3 cushion decision from public data."""
from __future__ import annotations

import argparse
import gzip
import json
import math
import time
from pathlib import Path

import numpy as np

try:
    from idea_lab.collect_altspot_tilt_live import active_market, asks
    from idea_lab.collect_public_hourly_dominance_orderbook import book
except ModuleNotFoundError:
    from collect_altspot_tilt_live import active_market, asks
    from collect_public_hourly_dominance_orderbook import book

import sys
sys.path.insert(0, "/Users/kapil/proj/kalshi-scalp")
from fetch_cb_minutes import fetch_chunk  # type: ignore

MIN_Z = 1.04
PROXY_RMSE = 8.32
REMAINING_EFFECTIVE = 12.0 - 2.0 / 3.0


def spot_state_m3(open_ts: int):
    start_ns = time.time_ns()
    rows = fetch_chunk("BTC-USD", open_ts - 61 * 60, open_ts + 180)
    finish_ns = time.time_ns()
    closes = {int(row[0]): float(row[4]) for row in rows if len(row) >= 5}
    history = [closes.get(ts) for ts in range(open_ts - 3600, open_ts, 60)]
    spot = closes.get(open_ts + 120)
    if spot is None or any(value is None for value in history):
        return None, start_ns, finish_ns
    sigma_dollar = float(np.std(np.diff(history), ddof=1))
    return {
        "spot_close": spot,
        "sigma_dollar_1m": sigma_dollar,
        "history_points": len(history),
        "candle_ts": open_ts + 120,
    }, start_ns, finish_ns


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration", type=int, default=3300)
    parser.add_argument(
        "--output", type=Path,
        default=Path("idea_lab/btc_cushion_m3_live_20261006.jsonl.gz"),
    )
    args = parser.parse_args()
    deadline = time.monotonic() + args.duration
    seen = set()
    with gzip.open(args.output, "at", encoding="utf-8", compresslevel=1) as out:
        while time.monotonic() < deadline:
            now = int(time.time())
            market = active_market("BTC", now)
            if not market or market["ticker"] in seen:
                time.sleep(1)
                continue
            decision_ts = market["open_ts"] + 180
            if now < decision_ts + 2:
                time.sleep(min(1, decision_ts + 2 - now))
                continue
            if now > decision_ts + 15:
                seen.add(market["ticker"])
                out.write(json.dumps({
                    "kind": "missed", "wall_ns": time.time_ns(),
                    "market": market, "decision_lag_s": now - decision_ts,
                }, separators=(",", ":")) + "\n")
                out.flush()
                continue
            spot, spot_start, spot_finish = spot_state_m3(market["open_ts"])
            depth, book_start, book_finish, error = book(market["ticker"])
            record = {
                "kind": "decision", "wall_ns": time.time_ns(), "market": market,
                "decision_lag_s": now - decision_ts,
                "spot_request_start_ns": spot_start,
                "spot_request_finish_ns": spot_finish,
                "book_request_start_ns": book_start,
                "book_request_finish_ns": book_finish,
                "book_error": error, "spot": spot, "book": depth, "signal": None,
            }
            if spot and depth and depth.get("yes_bid") is not None and depth.get("no_bid") is not None:
                denom = math.sqrt(
                    spot["sigma_dollar_1m"] ** 2 * REMAINING_EFFECTIVE
                    + PROXY_RMSE * PROXY_RMSE
                )
                z = (spot["spot_close"] - market["strike"]) / denom
                yes_ask, no_ask, yes_size, no_size = asks(depth)
                record["z"] = z
                record["yes_ask"] = yes_ask
                record["no_ask"] = no_ask
                if z >= MIN_Z and yes_ask is not None:
                    record["signal"] = {"side": "yes", "price": yes_ask, "size": yes_size}
                elif z <= -MIN_Z and no_ask is not None:
                    record["signal"] = {"side": "no", "price": no_ask, "size": no_size}
            out.write(json.dumps(record, separators=(",", ":")) + "\n")
            out.flush()
            seen.add(market["ticker"])
            time.sleep(1)


if __name__ == "__main__":
    main()
