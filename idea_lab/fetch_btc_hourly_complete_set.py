"""Fetch KXBTC range buckets matching the directional brackets already saved."""
from __future__ import annotations

import argparse
import gzip
import json
import os
import sys
import time
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, "/Users/kapil/proj/kalshi-scalp")
import fetch_15m_candles as F  # type: ignore

from fetch_btc_hourly_dominance import candles, market_list


def atomic_dump(obj: object, path: Path) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, sort_keys=True))
    os.replace(tmp, path)


def dec(value: object) -> Decimal:
    return Decimal(str(value).replace(",", ""))


def range_markets(closes: list[int], cache: Path) -> list[dict]:
    if cache.exists():
        with gzip.open(cache, "rt") as fh:
            rows = json.load(fh)
        cached_closes = {
            F.parse_iso(m["close_time"]) for m in rows if m.get("close_time")
        }
        # The query cache represents a continuous requested close-time span.
        # Some hours legitimately have no KXBTC range markets, so requiring
        # every target close to appear in the returned rows needlessly refetches
        # the entire 160k-market span.
        if (
            cached_closes
            and min(closes) >= min(cached_closes)
            and max(closes) <= max(cached_closes)
        ):
            return rows
    rows = market_list("KXBTC", min(closes), max(closes))
    keep = [
        {
            k: m.get(k)
            for k in (
                "ticker",
                "close_time",
                "floor_strike",
                "cap_strike",
                "result",
                "expiration_value",
                "rules_primary",
            )
        }
        for m in rows
    ]
    tmp = cache.with_suffix(cache.suffix + ".tmp")
    with gzip.open(tmp, "wt") as fh:
        json.dump(keep, fh, separators=(",", ":"), sort_keys=True)
    os.replace(tmp, cache)
    return keep


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dominance-panel", default="idea_lab/btc_hourly_dominance_panel.json")
    ap.add_argument("--output", default="idea_lab/btc_hourly_complete_set_panel.json")
    ap.add_argument(
        "--range-cache", default="idea_lab/btc_hourly_range_markets.json.gz"
    )
    ap.add_argument("--sleep", type=float, default=0.15)
    args = ap.parse_args()
    dominance = json.loads(Path(args.dominance_panel).read_text())
    output = Path(args.output)
    saved = json.loads(output.read_text()) if output.exists() else {}
    closes = sorted(int(k) for k, row in dominance.items() if row.get("complete"))
    if not closes:
        raise SystemExit("empty dominance panel")
    ranges = range_markets(closes, Path(args.range_cache))
    by_close: dict[int, list[dict]] = {}
    for market in ranges:
        by_close.setdefault(F.parse_iso(market["close_time"]), []).append(market)
    print(f"hours={len(closes)} range_markets={len(ranges)}", flush=True)

    for i, close in enumerate(closes, 1):
        key = str(close)
        if key in saved and saved[key].get("complete"):
            continue
        base = dominance[key]
        ladder = base.get("hourly") or []
        lower = [m for m in ladder if m["strike"] < base["m15"]["strike"]]
        upper = [m for m in ladder if m["strike"] >= base["m15"]["strike"]]
        if not lower or not upper:
            saved[key] = {"complete": True, "close_ts": close, "bucket": None}
            atomic_dump(saved, output)
            continue
        lo = max(lower, key=lambda m: m["strike"])
        hi = min(upper, key=lambda m: m["strike"])
        candidates = []
        for market in by_close.get(close, []):
            floor, cap = market.get("floor_strike"), market.get("cap_strike")
            if floor in (None, "None", "") or cap in (None, "None", ""):
                continue
            if dec(floor) > dec(lo["strike"]) and dec(cap) == dec(hi["strike"]):
                candidates.append(market)
        if len(candidates) != 1:
            saved[key] = {
                "complete": True,
                "close_ts": close,
                "bucket": None,
                "candidate_count": len(candidates),
            }
            atomic_dump(saved, output)
            continue
        market = candidates[0]
        saved[key] = {
            "complete": True,
            "close_ts": close,
            "bucket": {
                "ticker": market["ticker"],
                "floor_strike": market.get("floor_strike"),
                "cap_strike": market.get("cap_strike"),
                "result": market.get("result"),
                "expiration_value": market.get("expiration_value"),
                "rules_primary": market.get("rules_primary"),
                "bars": candles("KXBTC", market["ticker"], close - 960, close + 60),
            },
        }
        atomic_dump(saved, output)
        time.sleep(args.sleep)
        if i % 25 == 0:
            print(f"{i}/{len(closes)} saved={len(saved)}", flush=True)
    print(f"done hours={len(saved)}", flush=True)


if __name__ == "__main__":
    main()
