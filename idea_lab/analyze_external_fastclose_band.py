"""Frozen external final-minute 90-97c favorite replication."""
from __future__ import annotations

import gzip
import json
import math
from pathlib import Path

import numpy as np

try:
    from idea_lab.common import day_cluster_se, fee
except ModuleNotFoundError:
    from common import day_cluster_se, fee


SOURCE_COMMIT = "9698c3dfe8ce811f1ec16ba6eb76196810e4d871"
PUBLIC_COMMIT = "ed3230f21b0cb2b66f5b870e94695c1fd1e3bdc2"
PUBLIC = Path(
    "/tmp/kalshi_ext_20261006_7/"
    "theruviparambil_kalshi-btc-15m/data/windows.jsonl.gz"
)
LOCAL = Path(
    "/Users/kapil/proj/kalshi-scalp/data/oos_20261006/candles_BTC.json"
)
OUTPUT = Path("idea_lab/external_fastclose_band_report.json")


def summarize(trades: list[dict]) -> dict:
    pnl = np.asarray([row["pnl"] for row in trades])
    days = np.asarray([row["day"] for row in trades])
    mean = se = None
    if len(pnl):
        mean, se = day_cluster_se(pnl, days)
    split = len(trades) // 2
    day_totals = {
        day: float(pnl[days == day].sum()) for day in sorted(set(days.tolist()))
    }
    remove_n = max(1, math.ceil(0.10 * len(day_totals))) if day_totals else 0
    removed = set(
        sorted(day_totals, key=lambda day: day_totals[day], reverse=True)[:remove_n]
    )
    trimmed = [row["pnl"] for row in trades if row["day"] not in removed]
    gross_by_day = {
        day: sum(max(row["pnl"], 0.0) for row in trades if row["day"] == day)
        for day in day_totals
    }
    gross_total = sum(gross_by_day.values())
    row = {
        "trades": len(trades),
        "wins": sum(value["payout"] > 0 for value in trades),
        "losses": sum(value["payout"] == 0 for value in trades),
        "mean_c": None if mean is None else mean * 100,
        "lo95_c": None if mean is None else (mean - 1.96 * se) * 100,
        "extra_1c_stress_c": None if mean is None else mean * 100 - 1,
        "halves_c": [
            float(np.mean(pnl[:split]) * 100) if split else None,
            float(np.mean(pnl[split:]) * 100) if len(pnl[split:]) else None,
        ],
        "mean_ex_best_10pct_days_c": (
            float(np.mean(trimmed) * 100) if trimmed else None
        ),
        "max_day_gross_positive_share": (
            max(gross_by_day.values()) / gross_total if gross_total else None
        ),
        "rows": trades,
    }
    row["passes_gate"] = bool(
        row["trades"] >= 100
        and row["extra_1c_stress_c"] is not None
        and row["extra_1c_stress_c"] > 0
        and row["lo95_c"] is not None
        and row["lo95_c"] > 0
        and all(value is not None and value > 0 for value in row["halves_c"])
        and row["mean_ex_best_10pct_days_c"] is not None
        and row["mean_ex_best_10pct_days_c"] > 0
        and row["max_day_gross_positive_share"] is not None
        and row["max_day_gross_positive_share"] <= 0.20
    )
    return row


def trade_row(
    ticker: str,
    close_ts: int,
    yes_bid: float,
    yes_ask: float,
    outcome_yes: bool,
    sample: str,
) -> dict | None:
    no_ask = 1.0 - yes_bid
    if yes_ask >= no_ask:
        side, entry = "yes", yes_ask
    else:
        side, entry = "no", no_ask
    if not 0.90 <= entry <= 0.97:
        return None
    won = outcome_yes if side == "yes" else not outcome_yes
    payout = 1.0 if won else 0.0
    return {
        "ticker": ticker,
        "sample": sample,
        "close_ts": close_ts,
        "day": close_ts // 86400,
        "seconds_left_proxy": 60,
        "side": side,
        "entry": entry,
        "result": "yes" if outcome_yes else "no",
        "payout": payout,
        "pnl": payout - entry - fee(entry),
    }


def public_rows() -> tuple[int, list[dict]]:
    rows = []
    markets = 0
    with gzip.open(PUBLIC, "rt") as handle:
        for line in handle:
            market = json.loads(line)
            markets += 1
            quote = market.get("q", {}).get("1")
            if not quote:
                continue
            close_ts = int(
                __import__("datetime")
                .datetime.fromisoformat(market["close"].replace("Z", "+00:00"))
                .timestamp()
            )
            found = trade_row(
                market["ticker"],
                close_ts,
                float(quote[0]),
                float(quote[1]),
                bool(market["y"]),
                "public_full_population",
            )
            if found:
                rows.append(found)
    return markets, rows


def local_rows() -> tuple[int, list[dict]]:
    markets = json.loads(LOCAL.read_text())["markets"]
    rows = []
    for market in sorted(markets, key=lambda row: int(row["open_ts"])):
        close_ts = int(market["close_ts"])
        quote = next(
            (
                row
                for row in market.get("bars") or []
                if int(row["ts"]) == close_ts - 60 and not row.get("post_close")
            ),
            None,
        )
        if not quote or market.get("result") not in ("yes", "no"):
            continue
        found = trade_row(
            market["ticker"],
            close_ts,
            float(quote["b"]),
            float(quote["a"]),
            market["result"] == "yes",
            "local_post_source",
        )
        if found:
            rows.append(found)
    return len(markets), rows


def main() -> None:
    public_market_count, first = public_rows()
    local_market_count, second = local_rows()
    report = {
        "preregistration": "PREREG_external_fastclose_band_oos_20261006.md",
        "source_commit": SOURCE_COMMIT,
        "public_data_commit": PUBLIC_COMMIT,
        "timing": "exactly_60s_boundary_proxy_for_source_strictly_under_60s",
        "public_market_count": public_market_count,
        "local_market_count": local_market_count,
        "public_full_population": summarize(first),
        "local_post_source": summarize(second),
    }
    report["passes_both_samples"] = bool(
        report["public_full_population"]["passes_gate"]
        and report["local_post_source"]["passes_gate"]
    )
    report["promotion_eligible"] = False
    OUTPUT.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
