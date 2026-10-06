"""Fetch the preregistered day-stratified Coin Race candle sample."""
from __future__ import annotations

import json
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, "/Users/kapil/proj/kalshi-scalp")
import fetch_15m_candles as fetch  # type: ignore

SERIES = "KXCRYPTOLEAD15M"
ASSETS = ("BTC", "ETH", "SOL", "XRP", "HYPE")
START = 1_789_344_000  # 2026-09-14T00:00:00Z
END = 1_791_244_800  # 2026-10-06T00:00:00Z exclusive
SEED = 20261006
PER_DAY = 8
OUTPUT = Path("idea_lab/crypto_leader_history_sample.json")


def main() -> None:
    markets = []
    cursor = ""
    while True:
        path = (
            f"/markets?series_ticker={SERIES}&status=settled"
            f"&min_close_ts={START}&max_close_ts={END - 1}&limit=1000"
        )
        if cursor:
            path += f"&cursor={cursor}"
        payload = fetch.get(path)
        if not payload or payload is fetch.MISSING:
            raise SystemExit("market enumeration failed")
        markets.extend(payload.get("markets") or [])
        cursor = payload.get("cursor") or ""
        print(f"enumerated {len(markets)}", flush=True)
        if not cursor:
            break
        time.sleep(0.5)

    grouped: dict[str, list[dict]] = {}
    for market in markets:
        event = market.get("event_ticker")
        if event:
            grouped.setdefault(event, []).append(market)
    complete = {}
    for event, rows in grouped.items():
        labels = {str(row.get("yes_sub_title")): row for row in rows}
        if set(labels) == set(ASSETS):
            complete[event] = labels
    by_day: dict[str, list[str]] = {}
    for event, labels in complete.items():
        day = labels["BTC"]["close_time"][:10]
        by_day.setdefault(day, []).append(event)
    rng = random.Random(SEED)
    selected = []
    for day in sorted(by_day):
        pool = sorted(by_day[day])
        selected.extend(pool if len(pool) <= PER_DAY else rng.sample(pool, PER_DAY))
    selected = sorted(selected)
    print(f"complete events={len(complete)} selected={len(selected)}", flush=True)

    prior = {}
    if OUTPUT.exists():
        try:
            prior = json.loads(OUTPUT.read_text()).get("events") or {}
        except json.JSONDecodeError:
            prior = {}
    output = dict(prior)
    for index, event in enumerate(selected, 1):
        if event in output:
            continue
        legs = {}
        failed = False
        for asset in ASSETS:
            market = complete[event][asset]
            open_ts = fetch.parse_iso(market["open_time"])
            close_ts = fetch.parse_iso(market["close_time"])
            payload = fetch.get(
                f"/series/{SERIES}/markets/{market['ticker']}/candlesticks"
                f"?start_ts={open_ts - 120}&end_ts={close_ts + 60}&period_interval=1"
            )
            if not payload or payload is fetch.MISSING:
                failed = True
                break
            bars = []
            for candle in payload.get("candlesticks") or []:
                stamp = candle.get("end_period_ts")
                if not isinstance(stamp, int) or stamp > close_ts:
                    continue
                yes_bid = candle.get("yes_bid") or {}
                yes_ask = candle.get("yes_ask") or {}
                bars.append(
                    {
                        "ts": stamp,
                        "bid": fetch.to_f(yes_bid.get("close_dollars")),
                        "ask": fetch.to_f(yes_ask.get("close_dollars")),
                    }
                )
            legs[asset] = {
                "ticker": market["ticker"],
                "open_ts": open_ts,
                "close_ts": close_ts,
                "result": market.get("result"),
                "settlement_value_dollars": market.get("settlement_value_dollars"),
                "rules_secondary": market.get("rules_secondary"),
                "bars": sorted(bars, key=lambda row: row["ts"]),
            }
            time.sleep(0.38)
        if failed:
            print(f"failed {event}", flush=True)
            continue
        output[event] = {"legs": legs}
        OUTPUT.write_text(
            json.dumps(
                {
                    "series": SERIES,
                    "seed": SEED,
                    "per_day": PER_DAY,
                    "events": output,
                }
            )
        )
        if index % 20 == 0:
            print(f"fetched {index}/{len(selected)}", flush=True)
    OUTPUT.write_text(
        json.dumps(
            {
                "series": SERIES,
                "seed": SEED,
                "per_day": PER_DAY,
                "events": output,
            }
        )
    )
    print(f"wrote {len(output)} events -> {OUTPUT}")


if __name__ == "__main__":
    main()
