"""Download the preregistered KXBTC15M/KXBTCD same-settlement panel.

Public read-only API only.  The output is resumable by hour and is written
atomically after every completed hour.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, "/Users/kapil/proj/kalshi-scalp")
import fetch_15m_candles as F  # type: ignore

LADDER_SERIES = {
    "BTC": "KXBTCD",
    "ETH": "KXETHD",
    "SOL": "KXSOLD",
    "XRP": "KXXRPD",
    "DOGE": "KXDOGED",
    "BNB": "KXBNBD",
    "HYPE": "KXHYPED",
    "NEAR": "KXNEARD",
    "ZEC": "KXZECD",
}


def atomic_dump(obj: object, path: Path) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, sort_keys=True))
    os.replace(tmp, path)


def candles(series: str, ticker: str, start: int, end: int) -> list[dict]:
    route = (
        f"/series/{series}/markets/{ticker}/candlesticks"
        f"?start_ts={start}&end_ts={end}&period_interval=1"
    )
    data = F.get(route)
    if not data or data is F.MISSING:
        return []
    out = []
    for c in data.get("candlesticks") or []:
        yb = c.get("yes_bid") or {}
        ya = c.get("yes_ask") or {}
        out.append(
            {
                "ts": c.get("end_period_ts"),
                "b": F.to_f(yb.get("close_dollars")),
                "a": F.to_f(ya.get("close_dollars")),
                "b_low": F.to_f(yb.get("low_dollars")),
                "a_high": F.to_f(ya.get("high_dollars")),
                "volume": F.to_f((c.get("volume") or c).get("volume_fp")),
            }
        )
    return out


def market_list(series: str, lo: int, hi: int) -> list[dict]:
    rows: list[dict] = []
    cursor = ""
    while True:
        route = (
            f"/markets?series_ticker={series}&status=settled"
            f"&min_close_ts={lo}&max_close_ts={hi}&limit=1000"
        )
        if cursor:
            route += f"&cursor={cursor}"
        data = F.get(route)
        if not data or data is F.MISSING:
            break
        rows.extend(data.get("markets") or [])
        cursor = data.get("cursor") or ""
        if not cursor:
            break
        time.sleep(0.15)
    return rows


def market_detail(ticker: str) -> dict:
    data = F.get(f"/markets/{ticker}")
    if not data or data is F.MISSING or not data.get("market"):
        raise RuntimeError(f"missing market detail: {ticker}")
    return data["market"]


def strike(market: dict) -> float:
    raw = market.get("floor_strike")
    if raw not in (None, "None", ""):
        return float(str(raw).replace(",", ""))
    match = re.search(r"-T(-?[0-9]+(?:\.[0-9]+)?)$", market["ticker"])
    if not match:
        raise ValueError(f"cannot parse strike: {market['ticker']}")
    return float(match.group(1))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--asset", default="BTC", choices=sorted(LADDER_SERIES))
    ap.add_argument("--ladder-series")
    ap.add_argument("--strikes", default="/Users/kapil/proj/kalshi-scalp/data/oos_20261006/strikes.json")
    ap.add_argument("--legacy-15m")
    ap.add_argument("--legacy-days", type=int, default=10)
    ap.add_argument("--output")
    ap.add_argument("--sleep", type=float, default=0.12)
    args = ap.parse_args()

    asset = args.asset.upper()
    series_15 = f"KX{asset}15M"
    ladder_series = args.ladder_series or LADDER_SERIES[asset]
    out_path = Path(args.output or f"idea_lab/{asset.lower()}_hourly_dominance_panel.json")
    panel = json.loads(out_path.read_text()) if out_path.exists() else {}
    all_15 = json.loads(Path(args.strikes).read_text())[series_15]
    if args.legacy_15m:
        legacy = json.loads(Path(args.legacy_15m).read_text()).get("markets") or []
        eligible = [
            m for m in legacy
            if int(m.get("close_ts", -1)) % 3600 == 0
            and m.get("result") in ("yes", "no")
            and m.get("floor_strike") not in (None, "None", "")
        ]
        if eligible:
            last = max(int(m["close_ts"]) for m in eligible)
            first = last - args.legacy_days * 86400
            for m in eligible:
                close = int(m["close_ts"])
                if first < close <= last:
                    all_15.append(
                        {
                            "ticker": m["ticker"],
                            "open_time": datetime.fromtimestamp(
                                int(m["open_ts"]), timezone.utc
                            ).isoformat().replace("+00:00", "Z"),
                            "close_time": datetime.fromtimestamp(
                                close, timezone.utc
                            ).isoformat().replace("+00:00", "Z"),
                            "floor_strike": m["floor_strike"],
                            "result": m["result"],
                        }
                    )
    rows_15 = []
    for m in all_15:
        close = F.parse_iso(m["close_time"])
        if close % 3600 or m.get("result") not in ("yes", "no"):
            continue
        if m.get("floor_strike") in (None, "None", ""):
            continue
        rows_15.append((close, m))

    if not rows_15:
        raise SystemExit("no on-the-hour KXBTC15M rows")
    lo, hi = min(x[0] for x in rows_15), max(x[0] for x in rows_15)
    hourly = market_list(ladder_series, lo, hi)
    by_close: dict[int, list[dict]] = {}
    for m in hourly:
        close = F.parse_iso(m["close_time"])
        by_close.setdefault(close, []).append(m)
    print(f"15m hours={len(rows_15)} hourly markets={len(hourly)}", flush=True)

    for i, (close, m15) in enumerate(sorted(rows_15), 1):
        key = str(close)
        if (
            key in panel
            and panel[key].get("complete")
            and panel[key].get("m15", {}).get("rules_primary")
        ):
            continue
        m15_full = market_detail(m15["ticker"])
        time.sleep(args.sleep)
        k15 = strike(m15_full)
        ladder = [
            m for m in by_close.get(close, [])
            if "-T" in m.get("ticker", "")
        ]
        ladder.sort(key=strike)
        below = [m for m in ladder if strike(m) < k15][-1:]
        above = [m for m in ladder if strike(m) >= k15][:1]
        selected = below + above
        item = {
            "complete": False,
            "close_ts": close,
            "m15": {
                "ticker": m15["ticker"],
                "strike": k15,
                "result": m15_full.get("result"),
                "expiration_value": m15_full.get("expiration_value"),
                "rules_primary": m15_full.get("rules_primary"),
                "bars": candles(series_15, m15["ticker"], close - 960, close + 60),
            },
            "hourly": [],
        }
        time.sleep(args.sleep)
        for mh in selected:
            item["hourly"].append(
                {
                    "ticker": mh["ticker"],
                    "strike": strike(mh),
                    "result": mh.get("result"),
                    "expiration_value": mh.get("expiration_value"),
                    "rules_primary": mh.get("rules_primary"),
                    "bars": candles(ladder_series, mh["ticker"], close - 960, close + 60),
                }
            )
            time.sleep(args.sleep)
        item["complete"] = True
        panel[key] = item
        atomic_dump(panel, out_path)
        if i % 25 == 0:
            print(f"{i}/{len(rows_15)} hours, saved={len(panel)}", flush=True)
    print(f"done hours={len(panel)}", flush=True)


if __name__ == "__main__":
    main()
