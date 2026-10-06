"""Post-publication replication of Quantfirm's selected BTC wait3/75 favorite."""
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
OUTPUT = Path("idea_lab/external_quantfirm_wait3_favorite_report.json")
SOURCE_COMMIT = "364984643cc35a2a69bea44399a0d1fd95d697ec"


def side_asks(row: dict) -> list[tuple[str, float]]:
    return [
        ("yes", float(row["a"])),
        ("no", 1.0 - float(row["b"])),
    ]


def signal(market: dict) -> tuple[str, dict, float] | None:
    t0 = int(market["open_ts"])
    rows = sorted(
        (
            row
            for row in market.get("bars") or []
            if not row.get("post_close")
            and t0 + 180 <= int(row["ts"]) <= t0 + 840
        ),
        key=lambda row: int(row["ts"]),
    )
    for row in rows:
        eligible = [
            (side, price)
            for side, price in side_asks(row)
            if 0.75 <= price <= 0.92
            and fee(price) <= 0.15 * (1.0 - price)
            and (1.0 - price - fee(price)) >= 0.07
        ]
        if eligible:
            side, price = max(eligible, key=lambda item: item[1])
            return side, row, price
    return None


def score_market(market: dict) -> tuple[dict, dict | None] | None:
    found = signal(market)
    if found is None:
        return None
    side, row, entry = found
    t0 = int(market["open_ts"])
    payout = float(market["result"] == side)
    base = {
        "ticker": market["ticker"],
        "t0": t0,
        "day": t0 // 86400,
        "signal_ts": int(row["ts"]),
        "minute": (int(row["ts"]) - t0) // 60,
        "side": side,
        "entry": entry,
        "result": market["result"],
        "pnl": payout - entry - fee(entry),
    }
    next_row = next(
        (
            item
            for item in market.get("bars") or []
            if not item.get("post_close")
            and int(item["ts"]) == int(row["ts"]) + 60
        ),
        None,
    )
    delayed = None
    if next_row is not None:
        delayed_entry = (
            float(next_row["a"])
            if side == "yes"
            else 1.0 - float(next_row["b"])
        )
        delayed = {
            **base,
            "execution_ts": int(next_row["ts"]),
            "entry": delayed_entry,
            "pnl": payout - delayed_entry - fee(delayed_entry),
        }
    return base, delayed


def main() -> None:
    markets = sorted(
        json.loads(MARKETS.read_text())["markets"],
        key=lambda row: int(row["open_ts"]),
    )
    immediate = []
    delayed = []
    for market in markets:
        if market.get("result") not in ("yes", "no"):
            continue
        found = score_market(market)
        if found is None:
            continue
        now, later = found
        immediate.append(now)
        if later is not None:
            delayed.append(later)
    report = {
        "preregistration": "PREREG_external_quantfirm_wait3_favorite_20261006.md",
        "source_commit": SOURCE_COMMIT,
        "source_published_before_local_sample": True,
        "market_count": len(markets),
        "same_bar_governing": summarize(immediate),
        "next_minute_diagnostic": summarize(delayed),
    }
    OUTPUT.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
