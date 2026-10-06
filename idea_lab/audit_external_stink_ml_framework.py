#!/usr/bin/env python3
"""Audit two external BTC 15-minute strategy artifacts without trading.

The first artifact is a historical "stink bid" simulator.  The second is an
XGBoost probability report.  This audit is intentionally limited to shipped
code and artifacts: it does not call authenticated APIs or place orders.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any


STINK_COMMIT = "8796d10d5e9d8bf99f16de769a758911a0f74759"
ML_COMMIT = "00f6c52b74aa17dcb69971b4c9480db7d173bfcc"


def _float(row: dict[str, str], key: str) -> float:
    value = row.get(key)
    return float(value) if value not in (None, "") else 0.0


def audit_stink_bid(repo: Path) -> dict[str, Any]:
    output = repo / "btc15m_backtest" / "outputs" / "analysis"
    with (output / "stink_bid_summary_KXBTC15M.csv").open(newline="") as handle:
        summary = next(csv.DictReader(handle))
    with (output / "stink_bid_trades_KXBTC15M.csv").open(newline="") as handle:
        rows = list(csv.DictReader(handle))

    fills = [row for row in rows if row["action"] == "fill"]
    exits = [
        row for row in rows if row["action"] in ("time_exit", "settlement")
    ]
    fill_groups = Counter((row["market"], row["ts_fill"]) for row in fills)
    fill_fees = sum(_float(row, "fees") for row in fills)
    exit_fees_including_entry = sum(_float(row, "fees") for row in exits)
    gross = sum(_float(row, "pnl_gross") for row in exits)
    exit_net = sum(_float(row, "pnl_net") for row in exits)
    all_row_net = sum(_float(row, "pnl_net") for row in rows)
    initial_bankroll = _float(rows[0], "bankroll") if rows else 0.0
    final_bankroll = _float(rows[-1], "bankroll") if rows else 0.0

    strategy_source = (
        repo / "btc15m_backtest" / "stink_bid_strategy.py"
    ).read_text()
    engine_source = (repo / "btc15m_backtest" / "stink_bid_engine.py").read_text()

    return {
        "source_commit": STINK_COMMIT,
        "markets_total": int(summary["markets_total"]),
        "orders_submitted": int(summary["total_orders_submitted"]),
        "simulated_fill_rows": len(fills),
        "independent_filled_markets": len({row["market"] for row in fills}),
        "fill_rate": float(summary["overall_fill_rate"]),
        "fill_timestamp_groups": len(fill_groups),
        "groups_reusing_one_print_for_multiple_levels": sum(
            count > 1 for count in fill_groups.values()
        ),
        "max_levels_filled_by_one_print": max(fill_groups.values(), default=0),
        "reported_gross_pnl_dollars": float(summary["pnl_gross_sum"]),
        "reported_fee_sum_dollars": float(summary["fees_sum"]),
        "reported_net_pnl_dollars": float(summary["pnl_net_sum"]),
        "bankroll_change_dollars": final_bankroll - initial_bankroll,
        "fill_row_fees_dollars": fill_fees,
        "exit_row_fees_including_entry_dollars": exit_fees_including_entry,
        "exit_row_net_pnl_dollars": exit_net,
        "all_trade_log_rows_net_pnl_dollars": all_row_net,
        "summary_double_counts_entry_fees": abs(
            (exit_net - fill_fees) - all_row_net
        ) < 1e-9,
        "source_ignores_taker_side_for_fill": (
            "def _order_fillable" in strategy_source
            and "taker_side" not in strategy_source
        ),
        "source_does_not_decrement_print_quantity_across_orders": (
            "for order in orders:" in strategy_source
            and "trade_qty" in strategy_source
            and "per_trade_cap" in strategy_source
        ),
        "zero_submit_latency": float(summary["avg_submit_latency_ms"]) == 0.0,
        "no_queue_model_disclosed": "No queue position model" in summary["limitations"],
        "minute_bar_exit_approximation_disclosed": (
            "sub-minute exits approximated" in summary["limitations"]
        ),
        "adverse_selection_columns_populated": any(
            row.get("adverse_selection_1m") not in (None, "") for row in fills
        ),
        "engine_uses_trade_through_not_book_queue": (
            "_get_trades" in engine_source and "_simulate_fill_pass" in strategy_source
        ),
        "methodology_flags": [
            "only 14 independent filled markets underlie 41 simulated fill rows",
            "one historical print can fill several resting ladder levels because print quantity is not shared across orders",
            "the fill test ignores taker_side, so it does not prove a print hit the simulated resting bid",
            "fractional or tiny prints are promoted to at least one contract by max(1, int(volume * fill_pct))",
            "orders are modeled as submitted at exactly market open with zero latency and no queue position",
            "the summary net PnL double-counts entry fees relative to bankroll accounting",
            "positive one- and five-minute markouts are conditional on the same optimistic simulated-fill assignment",
            "the entire result is retrospective simulation, not an authenticated live fill ledger",
        ],
    }


def audit_ml_framework(repo: Path) -> dict[str, Any]:
    report_path = repo / "backend" / "btcmarket" / "artifacts" / "training_report.json"
    report = json.loads(report_path.read_text())
    training_source = (repo / "backend" / "core" / "btc_model_training.py").read_text()
    candles_source = (repo / "backend" / "btcmarket" / "candles.py").read_text()

    folds = report["fold_reports"]
    total_test_rows = sum(int(fold["test_rows"]) for fold in folds)
    total_test_windows = sum(int(fold["test_windows"]) for fold in folds)
    rows_per_window = [
        fold["test_rows"] / fold["test_windows"] for fold in folds
    ]
    committed_ledgers = [
        str(path.relative_to(repo))
        for path in repo.rglob("*")
        if path.is_file()
        and (
            path.suffix.lower() in {".db", ".sqlite", ".sqlite3"}
            or "paper_trades" in path.name.lower()
        )
    ]

    candle_open_timestamped = (
        '"open_time": datetime.fromtimestamp(r[0]' in candles_source
        and '"close": float(r[4])' in candles_source
    )
    backward_open_time_join = (
        'left_on="timestamp", right_on="candle_1m_open_time", direction="backward"'
        in training_source
    )
    backward_hour_open_time_join = (
        'left_on="timestamp", right_on="candle_1h_open_time", direction="backward"'
        in training_source
    )

    return {
        "source_commit": ML_COMMIT,
        "reported_total_windows": int(report["total_windows"]),
        "folds": len(folds),
        "aggregated_test_rows": total_test_rows,
        "aggregated_test_window_occurrences": total_test_windows,
        "mean_rows_per_test_window": total_test_rows / total_test_windows,
        "fold_rows_per_window": rows_per_window,
        "latest_to_earliest_fold_row_density_ratio": (
            rows_per_window[-1] / rows_per_window[0]
        ),
        "reported_xgb_brier": report["metrics"]["xgb"]["brier"],
        "reported_market_brier": report["metrics"]["market"]["brier"],
        "fixed_horizon_or_one_row_per_window_score": False,
        "executable_ask_pnl_reported": False,
        "fees_reported": False,
        "committed_live_or_paper_ledgers": committed_ledgers,
        "coinbase_candles_keyed_by_open_time_with_final_close": candle_open_timestamped,
        "one_minute_features_joined_on_candle_open_time": backward_open_time_join,
        "one_hour_features_joined_on_candle_open_time": backward_hour_open_time_join,
        "one_minute_within_bar_lookahead_risk": (
            candle_open_timestamped and backward_open_time_join
        ),
        "one_hour_within_bar_lookahead_risk": (
            candle_open_timestamped and backward_hour_open_time_join
        ),
        "model_uses_market_mid_as_feature": "yes_mid" in report["feature_importance"][2]["feature"]
        or any(
            item["feature"] == "yes_mid" for item in report["feature_importance"]
        ),
        "methodology_flags": [
            "57,379 snapshot rows reuse only 2,695 fold test-window outcomes",
            "reported Brier is row-weighted rather than one decision per independent window",
            "the latest fold has materially denser snapshots and therefore more weight in the aggregate score",
            "one-minute finalized candle closes are joined at candle open timestamps, leaking up to one minute of future data",
            "one-hour finalized candle return/range values are joined at hour open timestamps, leaking up to one hour of future data",
            "forecast accuracy is not executable PnL: the report has no asks, fees, fill model or trading return",
            "no underlying snapshot database or authenticated paper/live trade ledger is committed",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--stink-repo",
        type=Path,
        default=Path(
            "/tmp/kalshi_ext_20261006_8/artyomderkach-bit_btc-15m-vol-backtest"
        ),
    )
    parser.add_argument(
        "--ml-repo",
        type=Path,
        default=Path("/tmp/kalshi_ext_20261006_8/SiddhaBasu_kalshi-bot"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("idea_lab/external_stink_ml_framework_audit.json"),
    )
    args = parser.parse_args()
    result = {
        "stink_bid_simulator": audit_stink_bid(args.stink_repo),
        "xgboost_framework": audit_ml_framework(args.ml_repo),
        "evidence_class": "external_retrospective_methodology_audit",
        "promotion_eligible": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
