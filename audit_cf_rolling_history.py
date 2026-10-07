#!/usr/bin/env python3
"""Audit cached Kalshi rolling-average histories against market settlement metadata."""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


ASSETS = ["ETH", "SOL", "XRP", "DOGE", "BNB", "HYPE", "NEAR", "ZEC"]


def number(value: Any) -> float:
    return float(str(value).replace(",", ""))


def epoch_ms(value: str) -> int:
    return int(dt.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--assets", nargs="+", default=ASSETS)
    p.add_argument("--history-dir", type=Path, default=Path("data/cf_exact/history"))
    p.add_argument("--metadata-dir", type=Path, default=Path("../kalshi-scalp/data"))
    p.add_argument("--output", type=Path, default=Path("data/cf_exact/history_audit.json"))
    args = p.parse_args()

    metadata = {}
    for asset in args.assets:
        path = args.metadata_dir / f"mkts_KX{asset.upper()}15M.json"
        metadata.update({row["ticker"]: row for row in json.loads(path.read_text())})

    by_asset: dict[str, Counter] = defaultdict(Counter)
    open_errors: dict[str, list[float]] = defaultdict(list)
    failures = []
    for path in sorted(args.history_dir.glob("*.json.gz")):
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            cached = json.load(handle)
        ticker = cached["ticker"]
        meta = metadata.get(ticker)
        if meta is None:
            continue
        if meta.get("floor_strike") is None or meta.get("expiration_value") in (None, ""):
            asset = ticker[2:].split("15M", 1)[0]
            by_asset[asset]["invalid_metadata"] += 1
            continue
        asset = ticker[2:].split("15M", 1)[0]
        points = cached["live_data"]["details"]["timeseries"]
        times = [int(row["t"]) for row in points]
        values = [float(row["v"]) for row in points]
        lookup = dict(zip(times, values))
        close_ms = epoch_ms(meta["close_time"])
        open_ms = epoch_ms(meta["open_time"])
        terminal = lookup.get(close_ms)
        start = lookup.get(open_ms)
        expected_terminal = number(meta["expiration_value"])
        strike = number(meta["floor_strike"])
        result_yes = str(meta["result"]).lower() == "yes"

        stats = by_asset[asset]
        stats["markets"] += 1
        stats["strictly_increasing"] += int(all(b > a for a, b in zip(times, times[1:])))
        stats["strict_one_hz"] += int(all(b - a == 1_000 for a, b in zip(times, times[1:])))
        stats["has_open_boundary"] += int(start is not None)
        stats["has_close_boundary"] += int(terminal is not None)
        stats["terminal_exact"] += int(
            terminal is not None and abs(terminal - expected_terminal) < 1e-12
        )
        displayed_tie = terminal is not None and abs(terminal - strike) < 1e-12
        stats["displayed_ties"] += int(displayed_tie)
        stats["non_tie_markets"] += int(terminal is not None and not displayed_tie)
        stats["label_matches_non_tie_terminal"] += int(
            terminal is not None
            and not displayed_tie
            and ((terminal > strike) == result_yes)
        )
        if start is not None:
            open_errors[asset].append(abs(start - strike))
        if terminal is None or abs(terminal - expected_terminal) >= 1e-12:
            failures.append(
                {
                    "ticker": ticker,
                    "terminal": terminal,
                    "expiration_value": expected_terminal,
                }
            )

    report = {
        "source": "Kalshi public GET /live_data/events/{event_ticker}",
        "assets": {},
        "terminal_failures": failures,
    }
    for asset in sorted(by_asset):
        errors = sorted(open_errors[asset])
        stats = dict(by_asset[asset])
        stats["open_boundary_abs_error"] = {
            "mean": sum(errors) / len(errors) if errors else None,
            "median": errors[len(errors) // 2] if errors else None,
            "max": max(errors) if errors else None,
            "within_0.005": sum(x <= 0.00500001 for x in errors),
        }
        report["assets"][asset] = stats

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True))
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
