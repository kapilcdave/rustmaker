"""Frozen OOS replication of mickey1995's favorite and momentum rules."""
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
OUTPUT = Path("idea_lab/external_mickey_remaining_report.json")
SOURCE_COMMIT = "7a10efdc8d5d9c67219ce9104dbdbee1e0269201"


def midpoint_cents(row: dict) -> int:
    return int(((float(row["b"]) + float(row["a"])) / 2.0) * 100)


def market_bars(market: dict) -> dict[int, dict]:
    return {
        (int(row["ts"]) - int(market["open_ts"])) // 60: row
        for row in market.get("bars") or []
        if not row.get("post_close")
    }


def favorite_signal(market: dict) -> tuple[str, dict, int] | None:
    bars = market_bars(market)
    for minute in range(1, 4):
        row = bars.get(minute)
        if row is None:
            continue
        midpoint = midpoint_cents(row)
        if 60 <= midpoint <= 80:
            return "yes", row, midpoint
    return None


def momentum_signal(market: dict) -> tuple[str, dict, int, int] | None:
    bars = market_bars(market)
    initial_row = next((bars[m] for m in sorted(bars) if m >= 0), None)
    if initial_row is None:
        return None
    initial = midpoint_cents(initial_row)
    for minute in range(5, 8):
        row = bars.get(minute)
        if row is None:
            continue
        current = midpoint_cents(row)
        change = current - initial
        if abs(change) < 10:
            continue
        if change > 0 and 55 <= current <= 75:
            return "yes", row, current, initial
        no_price = 100 - current
        if change < 0 and 55 <= no_price <= 75:
            return "no", row, current, initial
    return None


def score(
    market: dict, found: tuple, strategy: str
) -> tuple[dict, dict | None]:
    side, row, midpoint, *rest = found
    t0 = int(market["open_ts"])
    entry = float(row["a"]) if side == "yes" else 1.0 - float(row["b"])
    payout = 1.0 if market["result"] == side else 0.0
    base = {
        "ticker": market["ticker"],
        "t0": t0,
        "day": t0 // 86400,
        "strategy": strategy,
        "side": side,
        "signal_ts": int(row["ts"]),
        "minute": (int(row["ts"]) - t0) // 60,
        "midpoint_cents": midpoint,
        "initial_midpoint_cents": rest[0] if rest else None,
        "entry": entry,
        "result": market["result"],
        "pnl": payout - entry - fee(entry),
    }
    bars = {
        int(item["ts"]): item
        for item in market.get("bars") or []
        if not item.get("post_close")
    }
    next_row = bars.get(int(row["ts"]) + 60)
    delayed = None
    if next_row is not None:
        next_entry = (
            float(next_row["a"])
            if side == "yes"
            else 1.0 - float(next_row["b"])
        )
        delayed = {
            **base,
            "entry": next_entry,
            "execution_ts": int(next_row["ts"]),
            "pnl": payout - next_entry - fee(next_entry),
        }
    return base, delayed


def main() -> None:
    markets = sorted(
        json.loads(MARKETS.read_text())["markets"],
        key=lambda row: int(row["open_ts"]),
    )
    rows = {
        "favorite": {"same": [], "next": []},
        "momentum": {"same": [], "next": []},
    }
    for market in markets:
        if market.get("result") not in ("yes", "no"):
            continue
        for name, signal_fn in (
            ("favorite", favorite_signal),
            ("momentum", momentum_signal),
        ):
            found = signal_fn(market)
            if found is None:
                continue
            immediate, delayed = score(market, found, name)
            rows[name]["same"].append(immediate)
            if delayed is not None:
                rows[name]["next"].append(delayed)
    report = {
        "preregistration": "PREREG_external_mickey_remaining_oos_20261006.md",
        "source_commit": SOURCE_COMMIT,
        "market_count": len(markets),
        "strategies": {
            name: {
                "same_bar_governing": summarize(values["same"]),
                "next_minute_diagnostic": summarize(values["next"]),
            }
            for name, values in rows.items()
        },
    }
    OUTPUT.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
