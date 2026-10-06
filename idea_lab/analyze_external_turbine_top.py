"""Post-publication replication of the top reported Turbine BTC15m strategy."""
from __future__ import annotations

import json
import math
from pathlib import Path

try:
    from idea_lab.analyze_external_dead_contract import summarize
except ModuleNotFoundError:
    from analyze_external_dead_contract import summarize


MARKETS = Path(
    "/Users/kapil/proj/kalshi-scalp/data/oos_20261006/candles_BTC.json"
)
SPOT = Path("/Users/kapil/proj/kalshi-scalp/data/oos_20261006/cb_BTC-USD.json")
OUTPUT = Path("idea_lab/external_turbine_top_report.json")
SOURCE_COMMIT = "a6b31da83462698fc3a6a6ccbe253de6dad1f5b7"
CONTRACTS = 40


def batch_fee(price: float, contracts: int = CONTRACTS) -> float:
    return math.ceil(0.07 * contracts * price * (1.0 - price) * 100 - 1e-9) / 100


def completed_velocity(spot: dict[int, float], kalshi_close_ts: int) -> float | None:
    """Percent change of the latest Coinbase candle completed by this close."""
    latest_open = kalshi_close_ts - 60
    latest = spot.get(latest_open)
    prior = spot.get(latest_open - 60)
    if latest is None or prior is None or prior <= 0:
        return None
    return (latest / prior - 1.0) * 100.0


def midpoint(row: dict) -> float:
    return (float(row["b"]) + float(row["a"])) / 2.0


def entry_signal(row: dict, velocity: float | None, tte_s: int) -> bool:
    return bool(
        velocity is not None
        and velocity > 0.167
        and 0.05 < midpoint(row) < 0.90
        and float(row["a"]) - float(row["b"]) < 0.03
        and tte_s > 180
    )


def exit_signal(
    row: dict, velocity: float | None, tte_s: int, entry: float
) -> str | None:
    mark_pnl = (float(row["b"]) - entry) * CONTRACTS
    if tte_s < 180:
        return "closeout"
    if midpoint(row) > 0.88 and tte_s > 180:
        return "profit_price"
    if mark_pnl > 5.0:
        return "profit_dollars"
    if mark_pnl < -3.0:
        return "stop_dollars"
    if velocity is not None and velocity < -0.05:
        return "velocity_collapse"
    if tte_s < 120:
        return "time_stop"
    return None


def score_market(market: dict, spot: dict[int, float]) -> list[dict]:
    t0 = int(market["open_ts"])
    close_ts = int(market["close_ts"])
    bars = {
        int(row["ts"]): row
        for row in market.get("bars") or []
        if not row.get("post_close")
    }
    cycles: list[dict] = []
    entry = None
    entry_ts = None
    buy_fee = None
    pending_entry = False
    pending_exit = None
    for minute in range(1, 16):
        ts = t0 + minute * 60
        row = bars.get(ts)
        if row is None:
            continue
        if pending_entry and entry is None:
            entry = float(row["a"])
            entry_ts = ts
            buy_fee = batch_fee(entry)
            pending_entry = False
        if pending_exit is not None and entry is not None:
            exit_price = float(row["b"])
            total = (
                (exit_price - entry) * CONTRACTS
                - float(buy_fee)
                - batch_fee(exit_price)
            )
            cycles.append({
                "ticker": market["ticker"],
                "t0": t0,
                "day": t0 // 86400,
                "entry_ts": entry_ts,
                "exit_ts": ts,
                "exit_reason": pending_exit,
                "entry": entry,
                "exit": exit_price,
                "result": market["result"],
                "pnl": total / CONTRACTS,
            })
            entry = entry_ts = buy_fee = None
            pending_exit = None
        velocity = completed_velocity(spot, ts)
        tte_s = close_ts - ts
        if entry is None:
            if minute < 15 and entry_signal(row, velocity, tte_s):
                pending_entry = True
        elif minute < 15:
            reason = exit_signal(row, velocity, tte_s, float(entry))
            if reason is not None:
                pending_exit = reason
    if entry is not None:
        payout = 1.0 if market["result"] == "yes" else 0.0
        total = (payout - entry) * CONTRACTS - float(buy_fee)
        cycles.append({
            "ticker": market["ticker"],
            "t0": t0,
            "day": t0 // 86400,
            "entry_ts": entry_ts,
            "exit_ts": close_ts,
            "exit_reason": "settlement",
            "entry": entry,
            "exit": payout,
            "result": market["result"],
            "pnl": total / CONTRACTS,
        })
    return cycles


def main() -> None:
    markets = sorted(
        json.loads(MARKETS.read_text())["markets"],
        key=lambda row: int(row["open_ts"]),
    )
    spot = {
        int(row[0]): float(row[4])
        for row in json.loads(SPOT.read_text())["bars"]
    }
    rows = [
        cycle
        for market in markets
        if market.get("result") in ("yes", "no")
        for cycle in score_market(market, spot)
    ]
    report = {
        "preregistration": "PREREG_external_turbine_top_oos_20261006.md",
        "source_commit": SOURCE_COMMIT,
        "selected_from_kxbtc15m_backtests": 1276,
        "source_validation_scheme": "single_window_in_sample",
        "market_count": len(markets),
        "cycles": summarize(rows),
        "exit_reasons": {
            reason: summarize([row for row in rows if row["exit_reason"] == reason])
            for reason in sorted({row["exit_reason"] for row in rows})
        },
    }
    OUTPUT.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()

