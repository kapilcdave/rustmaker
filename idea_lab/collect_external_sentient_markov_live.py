"""Read-only prospective direct-book collector for the Sentient Markov rule."""
from __future__ import annotations

import argparse
import gzip
import json
import time
from pathlib import Path

try:
    from idea_lab.analyze_external_sentient_markov import aggregate, find_signal
    from idea_lab.collect_public_hourly_dominance import get, parse_ts
    from idea_lab.collect_public_hourly_dominance_orderbook import book
except ModuleNotFoundError:
    from analyze_external_sentient_markov import aggregate, find_signal
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
            strike = float(str(market["floor_strike"]).replace(",", ""))
        except (KeyError, TypeError, ValueError):
            continue
        open_ts = close_ts - 900
        if open_ts <= now < close_ts:
            candidates.append((close_ts, market, strike))
    if not candidates:
        return None
    close_ts, market, strike = min(candidates, key=lambda item: item[0])
    return {
        "ticker": market["ticker"],
        "event_ticker": market.get("event_ticker"),
        "open_ts": close_ts - 900,
        "close_ts": close_ts,
        "floor_strike": strike,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration", type=int, default=6000)
    parser.add_argument("--poll", type=float, default=1.0)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("idea_lab/external_sentient_markov_live_20261006.jsonl.gz"),
    )
    args = parser.parse_args()
    deadline = time.monotonic() + args.duration
    current = None
    evaluated: set[tuple[str, int]] = set()
    signaled: set[str] = set()
    next_discover = 0.0
    with gzip.open(args.output, "at", encoding="utf-8", compresslevel=1) as out:
        out.write(
            json.dumps(
                {
                    "kind": "start",
                    "wall_ns": time.time_ns(),
                    "preregistration": (
                        "PREREG_external_sentient_markov_live_20261006.md"
                    ),
                }
            )
            + "\n"
        )
        while time.monotonic() < deadline:
            now = int(time.time())
            if time.monotonic() >= next_discover:
                current = active_market(now)
                next_discover = time.monotonic() + 10
            if current and current["ticker"] not in signaled:
                for minute in range(3, 13):
                    key = (current["ticker"], minute)
                    if key in evaluated:
                        continue
                    decision_ts = current["open_ts"] + minute * 60
                    if now < decision_ts + 2:
                        continue
                    if now > decision_ts + 20:
                        evaluated.add(key)
                        out.write(
                            json.dumps(
                                {
                                    "kind": "missed",
                                    "wall_ns": time.time_ns(),
                                    "market": current,
                                    "minute": minute,
                                    "lag_s": now - decision_ts,
                                }
                            )
                            + "\n"
                        )
                        out.flush()
                        continue
                    spot_start = time.time_ns()
                    raw = fetch_chunk(
                        "BTC-USD", decision_ts - 3 * 86400, decision_ts
                    )
                    spot_finish = time.time_ns()
                    usable = [
                        row for row in raw if int(row[0]) + 60 <= decision_ts
                    ]
                    candles_5m = aggregate(usable, 300)
                    candles_15m = aggregate(usable, 900)
                    depth, book_start, book_finish, error = book(
                        current["ticker"]
                    )
                    evaluated.add(key)
                    yes_bid = None if depth is None else depth.get("yes_bid")
                    no_bid = None if depth is None else depth.get("no_bid")
                    if yes_bid is None or no_bid is None:
                        signal = None
                        reason = "two_sided_book_missing"
                    else:
                        synthetic = {
                            **current,
                            "result": None,
                            "bars": [
                                {
                                    "ts": decision_ts,
                                    "b": float(yes_bid),
                                    "a": 1.0 - float(no_bid),
                                    "post_close": False,
                                }
                            ],
                        }
                        signal = find_signal(
                            synthetic, candles_5m, candles_15m
                        )
                        reason = None if signal else "gates_failed"
                    record = {
                        "kind": "decision",
                        "wall_ns": time.time_ns(),
                        "market": current,
                        "minute": minute,
                        "lag_s": now - decision_ts,
                        "spot_request_start_ns": spot_start,
                        "spot_request_finish_ns": spot_finish,
                        "book_request_start_ns": book_start,
                        "book_request_finish_ns": book_finish,
                        "book_error": error,
                        "book": depth,
                        "spot_1m_rows": len(usable),
                        "spot_5m_bars": len(candles_5m),
                        "spot_15m_bars": len(candles_15m),
                        "signal": (
                            None
                            if signal is None
                            else {
                                key: value
                                for key, value in signal.items()
                                if key != "row"
                            }
                        ),
                        "no_signal_reason": reason,
                    }
                    out.write(json.dumps(record, separators=(",", ":")) + "\n")
                    out.flush()
                    if signal is not None:
                        signaled.add(current["ticker"])
                    break
            time.sleep(args.poll)
        out.write(
            json.dumps(
                {
                    "kind": "stop",
                    "wall_ns": time.time_ns(),
                    "evaluated": len(evaluated),
                    "signaled": len(signaled),
                }
            )
            + "\n"
        )


if __name__ == "__main__":
    main()
