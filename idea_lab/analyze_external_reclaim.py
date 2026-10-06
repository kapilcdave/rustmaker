"""Frozen OOS replication and public-fill audit for J0shusmc/Kalshi-BTC."""
from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Iterable

import numpy as np

try:
    from idea_lab.common import day_cluster_se, fee
except ModuleNotFoundError:
    from common import day_cluster_se, fee


MARKETS = Path(
    "/Users/kapil/proj/kalshi-scalp/data/oos_20261006/candles_BTC.json"
)
SPOT = Path(
    "/Users/kapil/proj/kalshi-scalp/data/oos_20261006/cb_BTC-USD.json"
)
SOURCE_LOG = Path(
    "/tmp/kalshi_ext_20261006_2/Kalshi-BTC/reports/btc15_trade_log.json"
)
OOS_OUTPUT = Path("idea_lab/external_reclaim_oos_report.json")
FILL_OUTPUT = Path("idea_lab/external_reclaim_fill_audit_report.json")
HOLDOUT_START = 1790208000  # 2026-09-18 00:00:00 UTC

LANES = (
    {
        "strategy": "RECLAIM_70",
        "side": "yes",
        "early": (0.15, 0.30),
        "entry": (0.35, 0.55),
        "btc_ret": (-20.0, 20.0),
        "ema": (-150.0, -50.0),
        "target": 0.70,
    },
    {
        "strategy": "BTC_FADE_90",
        "side": "yes",
        "early": (0.25, 0.30),
        "entry": (0.45, 0.65),
        "btc_ret": (-75.0, -25.0),
        "ema": None,
        "target": 0.90,
    },
    {
        "strategy": "NO_RECLAIM_80",
        "side": "no",
        "early": (0.35, 0.40),
        "entry": (0.50, 0.70),
        "btc_ret": (-75.0, -25.0),
        "ema": None,
        "target": 0.80,
    },
)


def ema(values: Iterable[float], span: int) -> float | None:
    values = list(values)
    if not values:
        return None
    alpha = 2.0 / (span + 1.0)
    current = float(values[0])
    for value in values[1:]:
        current = alpha * float(value) + (1.0 - alpha) * current
    return current


def btc_15m_closes(
    spot: dict[int, tuple[float, float, float, float]], decision_ts: int
) -> list[float]:
    """Port the source bot's completed-15m close bucketing."""
    closes: dict[int, float] = {}
    for ts in sorted(spot):
        if ts < decision_ts - 8 * 3600 or ts >= decision_ts:
            continue
        close_ts = ts + 60
        bucket_end = ((close_ts + 899) // 900) * 900
        if bucket_end <= decision_ts:
            closes[bucket_end] = spot[ts][1]
    return [closes[key] for key in sorted(closes)]


def btc_context(
    spot: dict[int, tuple[float, float, float, float]], t0: int
) -> dict | None:
    needed = [spot.get(t0 + 60 * minute) for minute in range(5)]
    if any(row is None for row in needed):
        return None
    rows = [row for row in needed if row is not None]
    decision_ts = t0 + 300
    closes = btc_15m_closes(spot, decision_ts)
    avg = ema(closes, 21)
    if avg is None:
        return None
    first_open = rows[0][0]
    decision_close = rows[-1][1]
    return {
        "open": first_open,
        "decision_close": decision_close,
        "ret": decision_close - first_open,
        "ema21": avg,
        "ema_dist": decision_close - avg,
    }


def side_rows(market: dict, side: str) -> list[dict]:
    rows = []
    prior_ask = None
    for index, raw in enumerate(
        sorted(
            (
                row
                for row in market.get("bars") or []
                if not row.get("post_close")
                and int(row["ts"]) <= int(market["close_ts"])
            ),
            key=lambda row: int(row["ts"]),
        ),
        start=1,
    ):
        if side == "yes":
            ask_close = float(raw["a"])
            ask_low = float(raw.get("al", ask_close))
            bid_high = float(raw.get("bh", raw["b"]))
        else:
            ask_close = 1.0 - float(raw["b"])
            ask_low = 1.0 - float(raw.get("bh", raw["b"]))
            bid_high = 1.0 - float(raw.get("al", raw["a"]))
        rows.append(
            {
                "minute": index,
                "ts": int(raw["ts"]),
                "ask_open_proxy": prior_ask,
                "ask_close": ask_close,
                "ask_low": ask_low,
                "bid_high": bid_high,
            }
        )
        prior_ask = ask_close
    return rows


def find_signal(market: dict, lane: dict, context: dict) -> dict | None:
    rows = side_rows(market, lane["side"])
    if len(rows) < 7:
        return None
    early = [row["ask_low"] for row in rows[:5]]
    early_low = min(early)
    early_min, early_max = lane["early"]
    if not early_min < early_low <= early_max:
        return None
    decision_close = rows[4]["ask_close"]
    sign = 1.0 if lane["side"] == "yes" else -1.0
    signed_ret = sign * context["ret"]
    ret_min, ret_max = lane["btc_ret"]
    if not ret_min <= signed_ret <= ret_max:
        return None
    if lane["ema"] is not None:
        signed_ema = sign * context["ema_dist"]
        ema_min, ema_max = lane["ema"]
        if not ema_min <= signed_ema < ema_max:
            return None
    entry_min, entry_max = lane["entry"]
    for row in rows[5:10]:
        opened = row["ask_open_proxy"]
        closed = row["ask_close"]
        if (
            opened is not None
            and entry_min <= closed < entry_max
            and closed > decision_close
            and closed > opened
        ):
            return {
                **row,
                "early_low": early_low,
                "decision_close": decision_close,
                "signed_btc_ret": signed_ret,
                "signed_ema_dist": sign * context["ema_dist"],
                "all_rows": rows,
            }
    return None


def score_trade(
    market: dict, lane: dict, signal: dict, mode: str
) -> dict | None:
    rows = signal["all_rows"]
    signal_index = int(signal["minute"]) - 1
    if mode == "signal_close":
        entry_index = signal_index
        entry = signal["ask_close"]
    elif mode == "next_minute_1c":
        entry_index = signal_index + 1
        if entry_index >= len(rows):
            return None
        entry = rows[entry_index]["ask_close"] + 0.01
        entry_min, entry_max = lane["entry"]
        if not entry_min <= entry <= entry_max:
            return None
    else:
        raise ValueError(mode)
    target = lane["target"]
    target_hit = any(
        row["bid_high"] >= target for row in rows[entry_index + 1 :]
    )
    if target_hit:
        exit_value = target
        pnl = target - entry - fee(entry) - fee(target)
    else:
        exit_value = 1.0 if market["result"] == lane["side"] else 0.0
        pnl = exit_value - entry - fee(entry)
    return {
        "ticker": market["ticker"],
        "t0": int(market["open_ts"]),
        "day": int(market["open_ts"]) // 86400,
        "strategy": lane["strategy"],
        "side": lane["side"],
        "signal_minute": signal["minute"],
        "entry_minute": entry_index + 1,
        "early_low": signal["early_low"],
        "decision_close": signal["decision_close"],
        "signal_close": signal["ask_close"],
        "entry": entry,
        "target": target,
        "target_hit": target_hit,
        "settlement_result": market["result"],
        "exit_value": exit_value,
        "signed_btc_ret": signal["signed_btc_ret"],
        "signed_ema_dist": signal["signed_ema_dist"],
        "pnl": pnl,
    }


def summary(trades: list[dict]) -> dict:
    if not trades:
        return {
            "trades": 0,
            "targets": 0,
            "mean_c": None,
            "lo95_c": None,
            "halves_c": [None, None],
            "extra_1c_stress_c": None,
            "mean_ex_best_10pct_days_c": None,
            "max_day_gross_positive_share": None,
            "by_strategy": {},
            "trade_rows": [],
        }
    pnl = np.asarray([row["pnl"] for row in trades], dtype=float)
    days = np.asarray([row["day"] for row in trades])
    mean, se = day_cluster_se(pnl, days)
    split = len(trades) // 2
    totals = {int(day): float(pnl[days == day].sum()) for day in np.unique(days)}
    remove_n = max(1, math.ceil(0.10 * len(totals)))
    removed = set(
        sorted(totals, key=lambda day: totals[day], reverse=True)[:remove_n]
    )
    trimmed = [row["pnl"] for row in trades if row["day"] not in removed]
    gross = {
        day: sum(max(row["pnl"], 0.0) for row in trades if row["day"] == day)
        for day in totals
    }
    gross_total = sum(gross.values())
    by_strategy = {}
    for lane in LANES:
        lane_rows = [row for row in trades if row["strategy"] == lane["strategy"]]
        lane_pnl = [row["pnl"] for row in lane_rows]
        by_strategy[lane["strategy"]] = {
            "trades": len(lane_rows),
            "targets": sum(row["target_hit"] for row in lane_rows),
            "mean_c": float(np.mean(lane_pnl) * 100) if lane_pnl else None,
        }
    return {
        "trades": len(trades),
        "targets": sum(row["target_hit"] for row in trades),
        "settlement_losses": sum(
            not row["target_hit"] and row["exit_value"] == 0 for row in trades
        ),
        "mean_c": mean * 100,
        "se_c": se * 100,
        "lo95_c": (mean - 1.96 * se) * 100,
        "halves_c": [
            float(np.mean(pnl[:split]) * 100) if split else None,
            float(np.mean(pnl[split:]) * 100) if len(pnl[split:]) else None,
        ],
        "extra_1c_stress_c": mean * 100 - 1.0,
        "mean_ex_best_10pct_days_c": (
            float(np.mean(trimmed) * 100) if trimmed else None
        ),
        "removed_best_days": sorted(removed),
        "max_day_gross_positive_share": (
            max(gross.values()) / gross_total if gross_total else None
        ),
        "by_strategy": by_strategy,
        "trade_rows": trades,
    }


def analyze_oos(markets_path: Path = MARKETS, spot_path: Path = SPOT) -> dict:
    markets = json.loads(markets_path.read_text())["markets"]
    spot_payload = json.loads(spot_path.read_text())
    spot = {
        int(row[0]): (float(row[3]), float(row[4]), float(row[1]), float(row[2]))
        for row in spot_payload["bars"]
    }
    modes = {"signal_close": [], "next_minute_1c": []}
    eligible = 0
    for market in sorted(markets, key=lambda row: int(row["open_ts"])):
        if (
            int(market["open_ts"]) < HOLDOUT_START
            or market.get("result") not in ("yes", "no")
        ):
            continue
        context = btc_context(spot, int(market["open_ts"]))
        if context is None:
            continue
        eligible += 1
        candidates = []
        for lane_index, lane in enumerate(LANES):
            signal = find_signal(market, lane, context)
            if signal is not None:
                candidates.append((signal["minute"], lane_index, lane, signal))
        if not candidates:
            continue
        _, _, lane, signal = min(candidates, key=lambda row: (row[0], row[1]))
        for mode in modes:
            trade = score_trade(market, lane, signal, mode)
            if trade is not None:
                modes[mode].append(trade)
    report = {
        "preregistration": "PREREG_external_reclaim_oos_20261006.md",
        "source_commit": "593029336b9639ce6489592a12345fa40ebe4a69",
        "holdout_start_utc": HOLDOUT_START,
        "eligible_markets": eligible,
        "rows": {mode: summary(trades) for mode, trades in modes.items()},
    }
    governing = report["rows"]["next_minute_1c"]
    report["passes_governing_gate"] = bool(
        governing["trades"] >= 100
        and governing["mean_c"] is not None
        and governing["mean_c"] > 0
        and governing["extra_1c_stress_c"] > 0
        and governing["lo95_c"] > 0
        and all(value is not None and value > 0 for value in governing["halves_c"])
        and governing["mean_ex_best_10pct_days_c"] is not None
        and governing["mean_ex_best_10pct_days_c"] > 0
        and governing["max_day_gross_positive_share"] is not None
        and governing["max_day_gross_positive_share"] <= 0.20
    )
    return report


def analyze_fills(source_log: Path = SOURCE_LOG) -> dict:
    payload = json.loads(source_log.read_text())
    signals = {
        row["ticker"]: row
        for row in payload.get("entered_signals", {}).values()
        if row.get("ticker")
    }
    rows = []
    for settlement in sorted(
        payload.get("kalshi_settlements", []),
        key=lambda row: row.get("settled_time", ""),
    ):
        ticker = settlement["ticker"]
        signal = signals.get(ticker, {})
        yes_count = float(settlement.get("yes_count_fp") or 0.0)
        no_count = float(settlement.get("no_count_fp") or 0.0)
        yes_cost = float(settlement.get("yes_total_cost_dollars") or 0.0)
        no_cost = float(settlement.get("no_total_cost_dollars") or 0.0)
        fees = float(settlement.get("fee_cost") or 0.0)
        result = settlement.get("market_result")
        payout = yes_count if result == "yes" else no_count
        pnl = payout - yes_cost - no_cost - fees
        entry_count = float(signal.get("count") or max(yes_count, no_count) or 1.0)
        settled_time = settlement.get("settled_time", "")
        day_text = settled_time[:10]
        rows.append(
            {
                "settled_time": settled_time,
                "day": day_text,
                "ticker": ticker,
                "strategy": signal.get("strategy", "UNKNOWN"),
                "entry_count": entry_count,
                "yes_count": yes_count,
                "no_count": no_count,
                "yes_cost": yes_cost,
                "no_cost": no_cost,
                "reported_fee": fees,
                "result": result,
                "payout": payout,
                "pnl_dollars": pnl,
                "pnl_per_entry_contract": pnl / entry_count,
            }
        )
    values = np.asarray([row["pnl_per_entry_contract"] for row in rows])
    day_ids = np.asarray(
        [
            int(row["day"].replace("-", ""))
            for row in rows
        ]
    )
    mean, se = day_cluster_se(values, day_ids) if rows else (None, None)
    split = len(rows) // 2
    cumulative = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for row in rows:
        cumulative += row["pnl_dollars"]
        peak = max(peak, cumulative)
        max_drawdown = min(max_drawdown, cumulative - peak)
    by_strategy = {}
    for strategy in sorted({row["strategy"] for row in rows}):
        subset = [row for row in rows if row["strategy"] == strategy]
        by_strategy[strategy] = {
            "settlements": len(subset),
            "positive": sum(row["pnl_dollars"] > 0 for row in subset),
            "negative": sum(row["pnl_dollars"] <= 0 for row in subset),
            "pnl_dollars": sum(row["pnl_dollars"] for row in subset),
            "mean_per_entry_contract_c": float(
                np.mean([row["pnl_per_entry_contract"] for row in subset]) * 100
            ),
        }
    gross_by_day = defaultdict(float)
    for row in rows:
        gross_by_day[row["day"]] += max(row["pnl_dollars"], 0.0)
    gross_total = sum(gross_by_day.values())
    current_lanes = {
        "RECLAIM_70",
        "BTC_FADE_90",
        "NO_RECLAIM_80",
    }
    lane_rows = [row for row in rows if row["strategy"] in current_lanes]
    lane_values = np.asarray(
        [row["pnl_per_entry_contract"] for row in lane_rows], dtype=float
    )
    lane_days = np.asarray(
        [int(row["day"].replace("-", "")) for row in lane_rows]
    )
    lane_mean, lane_se = (
        day_cluster_se(lane_values, lane_days)
        if lane_rows
        else (None, None)
    )
    lane_split = len(lane_rows) // 2
    report = {
        "preregistration": "PREREG_external_reclaim_fill_audit_20261006.md",
        "evidence": "external self-reported authenticated fills and settlements",
        "source_sha256": (
            "d0d3f6176bcb1a23d93616fa0a53965634129d1613dc69281d8b894bcbea8df6"
        ),
        "settlements": len(rows),
        "fills": len(payload.get("kalshi_fills", [])),
        "total_pnl_dollars": sum(row["pnl_dollars"] for row in rows),
        "positive_settlements": sum(row["pnl_dollars"] > 0 for row in rows),
        "negative_settlements": sum(row["pnl_dollars"] <= 0 for row in rows),
        "mean_per_entry_contract_c": None if mean is None else mean * 100,
        "lo95_per_entry_contract_c": (
            None if mean is None else (mean - 1.96 * se) * 100
        ),
        "halves_per_entry_contract_c": [
            float(np.mean(values[:split]) * 100) if split else None,
            float(np.mean(values[split:]) * 100) if len(values[split:]) else None,
        ],
        "max_closed_trade_drawdown_dollars": max_drawdown,
        "max_day_gross_positive_share": (
            max(gross_by_day.values()) / gross_total if gross_total else None
        ),
        "by_strategy": by_strategy,
        "published_three_lane_subset": {
            "settlements": len(lane_rows),
            "pnl_dollars": sum(row["pnl_dollars"] for row in lane_rows),
            "mean_per_entry_contract_c": (
                None if lane_mean is None else lane_mean * 100
            ),
            "lo95_per_entry_contract_c": (
                None
                if lane_mean is None
                else (lane_mean - 1.96 * lane_se) * 100
            ),
            "halves_per_entry_contract_c": [
                (
                    float(np.mean(lane_values[:lane_split]) * 100)
                    if lane_split
                    else None
                ),
                (
                    float(np.mean(lane_values[lane_split:]) * 100)
                    if len(lane_values[lane_split:])
                    else None
                ),
            ],
            "positive": sum(row["pnl_dollars"] > 0 for row in lane_rows),
            "negative": sum(row["pnl_dollars"] <= 0 for row in lane_rows),
        },
        "trade_rows": rows,
    }
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fills-only", action="store_true")
    parser.add_argument("--oos-only", action="store_true")
    args = parser.parse_args()
    if not args.fills_only:
        report = analyze_oos()
        OOS_OUTPUT.write_text(json.dumps(report, indent=2))
        print(json.dumps(report, indent=2))
    if not args.oos_only:
        report = analyze_fills()
        FILL_OUTPUT.write_text(json.dumps(report, indent=2))
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
