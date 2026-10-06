"""Audit the pinned public Resolution Rider source/config snapshot."""
from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parent
PARAMS = ROOT / "external_reed_rr_params_217355f.json"
OPTIMIZER = ROOT / "external_reed_optimize_rr_217355f.py.txt"
STRATEGY = ROOT / "external_reed_resolution_rider_217355f.py.txt"
OPTIMIZATION_DOC = ROOT / "external_reed_optimization_217355f.md.txt"
OUTPUT = ROOT / "external_reed_resolution_rider_audit.json"


def main() -> None:
    params = json.loads(PARAMS.read_text())
    optimizer = OPTIMIZER.read_text()
    strategy = STRATEGY.read_text()
    optimization_doc = OPTIMIZATION_DOC.read_text()

    cells = {}
    for name, row in sorted(params.items()):
        if not name.endswith("_15m"):
            continue
        cells[name] = {
            key: row.get(key)
            for key in (
                "cv_total_val_trades",
                "cv_val_losses",
                "cv_val_profit",
                "cv_safe_total",
                "cv_safe_ppt",
                "training_trades",
                "training_profit",
                "max_entry_price",
            )
        }

    report = {
        "source_repo": "https://github.com/reedjacobp/kalshi-trading-bot",
        "pinned_commit": "217355f3babe0f6a764a360f9b759dbb7bd38594",
        "commit_date": "2026-04-23T11:24:06-07:00",
        "source_claim_present": (
            "Walk-forward backtest: 18,316 trades" in strategy
            and "12/12 months profitable" in strategy
        ),
        "source_ships_claim_supporting_ledger": False,
        "optimization_doc_tick_period": (
            re.search(r"currently Apr 8-11", optimization_doc) is not None
        ),
        "optimizer_zero_slippage_default": (
            'os.getenv("ZERO_SLIPPAGE", "1") == "1"' in optimizer
        ),
        "optimizer_taker_profit_direct_fee_call": (
            "kalshi_taker_fee" in optimizer[
                optimizer.index("# --- Taker path (legacy) ---"):
                optimizer.index("def evaluate_params")
            ]
        ),
        "all_15m_cells_safe_total_negative": all(
            row["cv_safe_total"] is not None and row["cv_safe_total"] < 0
            for row in cells.values()
        ),
        "positive_15m_cv_val_profit_cells": [
            name for name, row in cells.items() if (row["cv_val_profit"] or 0) > 0
        ],
        "positive_15m_cv_safe_total_cells": [
            name for name, row in cells.items() if (row["cv_safe_total"] or 0) > 0
        ],
        "cells": cells,
        "interpretation": (
            "The pinned public artifact does not substantiate profitable "
            "15-minute deployment: every shipped 15m cell has negative "
            "cv_safe_total, and the optimizer defaults ZERO_SLIPPAGE to 1. "
            "The headline 18,316-trade/12-month claim has no shipped ledger."
        ),
    }
    OUTPUT.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
