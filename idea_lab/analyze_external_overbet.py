"""Frozen post-publication test of the five-minute favorite-overbet rule."""
from __future__ import annotations

import json
from pathlib import Path

try:
    from idea_lab.analyze_external_dead_contract import summarize
    from idea_lab.common import fee
except ModuleNotFoundError:
    from analyze_external_dead_contract import summarize
    from common import fee


MARKETS = Path(
    "/Users/kapil/proj/kalshi-scalp/data/oos_20261006/candles_BTC.json"
)
OUTPUT = Path("idea_lab/external_overbet_report.json")
SOURCE_COMMIT = "a904abe18bba500c9ac5d04fd43dfd61b18c6485"
THRESHOLD = 0.80


def midpoint(row: dict) -> float:
    return (float(row["b"]) + float(row["a"])) / 2.0


def find_signal(market: dict) -> tuple[str, dict, int] | None:
    """Return the minute-five favorite after a strict close-majority extreme."""
    t0 = int(market["open_ts"])
    bars = {
        int(row["ts"]): row
        for row in market.get("bars") or []
        if not row.get("post_close")
    }
    window = [bars.get(t0 + minute * 60) for minute in range(1, 6)]
    if any(row is None for row in window):
        return None
    assert all(row is not None for row in window)
    extreme_count = sum(
        max(midpoint(row), 1.0 - midpoint(row)) > THRESHOLD
        for row in window
    )
    if extreme_count < 3:
        return None
    entry_row = window[-1]
    side = "yes" if midpoint(entry_row) >= 0.5 else "no"
    return side, entry_row, extreme_count


def score_market(market: dict) -> tuple[dict, dict | None] | None:
    found = find_signal(market)
    if found is None:
        return None
    side, row, extreme_count = found
    t0 = int(market["open_ts"])
    entry = float(row["a"]) if side == "yes" else 1.0 - float(row["b"])
    payout = 1.0 if market["result"] == side else 0.0
    base = {
        "ticker": market["ticker"],
        "t0": t0,
        "day": t0 // 86400,
        "side": side,
        "signal_ts": int(row["ts"]),
        "minute": 5,
        "extreme_close_count": extreme_count,
        "entry_midpoint": midpoint(row),
        "entry": entry,
        "result": market["result"],
        "pnl": payout - entry - fee(entry),
    }
    bars = {
        int(item["ts"]): item
        for item in market.get("bars") or []
        if not item.get("post_close")
    }
    next_row = bars.get(t0 + 6 * 60)
    delayed = None
    if next_row is not None:
        next_entry = (
            float(next_row["a"])
            if side == "yes"
            else 1.0 - float(next_row["b"])
        )
        delayed = {
            **base,
            "execution_ts": int(next_row["ts"]),
            "entry": next_entry,
            "pnl": payout - next_entry - fee(next_entry),
        }
    return base, delayed


def main() -> None:
    markets = sorted(
        json.loads(MARKETS.read_text())["markets"],
        key=lambda row: int(row["open_ts"]),
    )
    same_bar: list[dict] = []
    next_minute: list[dict] = []
    for market in markets:
        if market.get("result") not in ("yes", "no"):
            continue
        scored = score_market(market)
        if scored is None:
            continue
        immediate, delayed = scored
        same_bar.append(immediate)
        if delayed is not None:
            next_minute.append(delayed)
    report = {
        "preregistration": "PREREG_external_overbet_oos_20261006.md",
        "source_commit": SOURCE_COMMIT,
        "source_published_before_local_sample": True,
        "replication_kind": "one_minute_close_proxy_with_actual_executable_ask",
        "market_count": len(markets),
        "same_bar_governing": summarize(same_bar),
        "next_minute_diagnostic": summarize(next_minute),
        "side_diagnostics": {
            side: summarize([row for row in same_bar if row["side"] == side])
            for side in ("yes", "no")
        },
    }
    OUTPUT.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()

