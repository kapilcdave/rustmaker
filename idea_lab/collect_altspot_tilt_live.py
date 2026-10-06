"""Prospective direct-book collector for PREREG_altspot_tilt_live_20261006.md."""
from __future__ import annotations

import argparse
import gzip
import json
import math
import time
import urllib.parse
from pathlib import Path

import numpy as np

try:
    from idea_lab.common import norm_cdf
except ModuleNotFoundError:
    from common import norm_cdf

try:
    from idea_lab.collect_public_hourly_dominance import get, parse_ts
    from idea_lab.collect_public_hourly_dominance_orderbook import book
except ModuleNotFoundError:
    from collect_public_hourly_dominance import get, parse_ts
    from collect_public_hourly_dominance_orderbook import book

import sys
sys.path.insert(0, "/Users/kapil/proj/kalshi-scalp")
from fetch_cb_minutes import fetch_chunk  # type: ignore

ASSETS = ("NEAR", "ZEC", "HYPE")
BASIS_BPS = {"NEAR": 4.67, "ZEC": 1.87, "HYPE": 1.77, "BNB": 2.44}
KS = (2, 3, 5, 8)
MAX_DECISION_LAG_S = 15


def active_market(asset: str, now: int) -> dict | None:
    data, *_ = get(f"/markets?series_ticker=KX{asset}15M&status=open&limit=8")
    if not data:
        return None
    candidates = []
    for market in data.get("markets") or []:
        try:
            close = parse_ts(market["close_time"])
            strike = float(str(market["floor_strike"]).replace(",", ""))
        except (KeyError, TypeError, ValueError):
            continue
        window_open = close - 900
        if window_open <= now < close:
            candidates.append((close, market, strike))
    if not candidates:
        return None
    close, market, strike = min(candidates, key=lambda item: item[0])
    return {
        "asset": asset,
        "ticker": market["ticker"],
        "event_ticker": market.get("event_ticker"),
        "open_ts": close - 900,
        "close_ts": close,
        "strike": strike,
    }


def spot_state(asset: str, open_ts: int, k: int) -> tuple[dict | None, int, int]:
    start_ns = time.time_ns()
    rows = fetch_chunk(f"{asset}-USD", open_ts - 130 * 60, open_ts + k * 60)
    finish_ns = time.time_ns()
    closes = {int(row[0]): float(row[4]) for row in rows if len(row) >= 5}
    history = [
        closes[open_ts - 60 * i]
        for i in range(120, 0, -1)
        if open_ts - 60 * i in closes
    ]
    decision_key = open_ts + 60 * (k - 1)
    if len(history) < 40 or decision_key not in closes:
        return None, start_ns, finish_ns
    sigma = float(np.std(np.diff(np.log(history))))
    if sigma < 2e-5:
        return None, start_ns, finish_ns
    return {
        "spot_close": closes[decision_key],
        "sigma_1m": sigma,
        "history_points": len(history),
        "candle_ts": decision_key,
    }, start_ns, finish_ns


def asks(depth: dict) -> tuple[float | None, float | None, float | None, float | None]:
    yes_ask = None if depth.get("no_bid") is None else 1.0 - float(depth["no_bid"])
    no_ask = None if depth.get("yes_bid") is None else 1.0 - float(depth["yes_bid"])
    return yes_ask, no_ask, depth.get("no_bid_size"), depth.get("yes_bid_size")


def decision_is_timely(now: int, open_ts: int, k: int) -> bool:
    decision_ts = open_ts + 60 * k
    return decision_ts + 2 <= now <= decision_ts + MAX_DECISION_LAG_S


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--assets", nargs="+", default=list(ASSETS))
    ap.add_argument("--duration", type=int, default=16_500)
    ap.add_argument("--poll", type=float, default=1.0)
    ap.add_argument(
        "--output",
        type=Path,
        default=Path("idea_lab/altspot_tilt_live_20261006.jsonl.gz"),
    )
    args = ap.parse_args()
    deadline = time.monotonic() + args.duration
    current = {}
    evaluated: set[tuple[str, str, int]] = set()
    traded: set[str] = set()
    next_discover = 0.0
    with gzip.open(args.output, "at", encoding="utf-8", compresslevel=1) as out:
        while time.monotonic() < deadline:
            now = int(time.time())
            if time.monotonic() >= next_discover:
                for asset in args.assets:
                    found = active_market(asset, now)
                    if found:
                        current[asset] = found
                    elif asset in current and now >= current[asset]["close_ts"]:
                        current.pop(asset, None)
                    time.sleep(0.1)
                next_discover = time.monotonic() + 10.0
            for asset, market in list(current.items()):
                for k in KS:
                    key = (asset, market["ticker"], k)
                    if key in evaluated or market["ticker"] in traded:
                        continue
                    decision_ts = market["open_ts"] + 60 * k
                    if now < decision_ts + 2:
                        continue
                    if now > decision_ts + MAX_DECISION_LAG_S:
                        out.write(
                            json.dumps(
                                {
                                    "kind": "missed",
                                    "wall_ns": time.time_ns(),
                                    "market": market,
                                    "k": k,
                                    "decision_lag_s": now - decision_ts,
                                },
                                separators=(",", ":"),
                            )
                            + "\n"
                        )
                        out.flush()
                        evaluated.add(key)
                        continue
                    spot, spot_start, spot_finish = spot_state(asset, market["open_ts"], k)
                    depth, book_start, book_finish, error = book(market["ticker"])
                    two_sided = bool(
                        depth
                        and depth.get("yes_bid") is not None
                        and depth.get("no_bid") is not None
                    )
                    if spot is None or not two_sided:
                        out.write(
                            json.dumps(
                                {
                                    "kind": "attempt",
                                    "wall_ns": time.time_ns(),
                                    "market": market,
                                    "k": k,
                                    "decision_lag_s": now - decision_ts,
                                    "spot_request_start_ns": spot_start,
                                    "spot_request_finish_ns": spot_finish,
                                    "book_request_start_ns": book_start,
                                    "book_request_finish_ns": book_finish,
                                    "book_error": error,
                                    "spot_ok": spot is not None,
                                    "two_sided_book": two_sided,
                                },
                                separators=(",", ":"),
                            )
                            + "\n"
                        )
                        out.flush()
                        break
                    record = {
                        "kind": "decision",
                        "wall_ns": time.time_ns(),
                        "market": market,
                        "k": k,
                        "decision_lag_s": now - decision_ts,
                        "spot_request_start_ns": spot_start,
                        "spot_request_finish_ns": spot_finish,
                        "book_request_start_ns": book_start,
                        "book_request_finish_ns": book_finish,
                        "book_error": error,
                        "spot": spot,
                        "book": depth,
                        "signal": None,
                    }
                    adjusted_strike = market["strike"] * math.exp(
                        -BASIS_BPS[asset] * 1e-4
                    )
                    fair = float(
                        norm_cdf(
                            math.log(spot["spot_close"] / adjusted_strike)
                            / (spot["sigma_1m"] * math.sqrt(max(15 - k, 0.5)))
                        )
                    )
                    yes_ask, no_ask, yes_size, no_size = asks(depth)
                    record["fair"] = fair
                    record["yes_ask"] = yes_ask
                    record["no_ask"] = no_ask
                    if yes_ask is not None and fair - yes_ask > 0.10:
                        record["signal"] = {
                            "side": "yes", "price": yes_ask, "size": yes_size
                        }
                    elif no_ask is not None and (1.0 - no_ask) - fair > 0.10:
                        record["signal"] = {
                            "side": "no", "price": no_ask, "size": no_size
                        }
                    out.write(json.dumps(record, separators=(",", ":")) + "\n")
                    out.flush()
                    evaluated.add(key)
                    if record["signal"] is not None:
                        traded.add(market["ticker"])
                    break
            time.sleep(args.poll)


if __name__ == "__main__":
    main()
