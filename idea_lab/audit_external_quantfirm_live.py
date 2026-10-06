"""Audit the public Quantfirm confirmed-live Kalshi settlement ledger."""
from __future__ import annotations

import argparse
import csv
import json
import math
from collections import defaultdict
from pathlib import Path

import numpy as np

try:
    from idea_lab.common import day_cluster_se
except ModuleNotFoundError:
    from common import day_cluster_se


INPUT = Path("idea_lab/external_quantfirm_kalshi_paper_trades.csv")
OUTPUT = Path("idea_lab/external_quantfirm_live_audit.json")
SOURCE_COMMIT = "dc3257dbd58c93fd04caec324f84d29510ea1aa8"
RULE_COMMIT = "364984643cc35a2a69bea44399a0d1fd95d697ec"
POST_RULE_SETTLEMENT = "2026-09-12T16:15:00Z"


def summarize(rows: list[dict]) -> dict:
    contracts = []
    days = []
    for row in rows:
        count = int(round(float(row["count"])))
        per_contract = float(row["pnl"]) / count
        contracts.extend([per_contract] * count)
        days.extend([row["settled_at"][:10]] * count)
    pnl = np.asarray(contracts, dtype=float)
    day_arr = np.asarray(days)
    if not len(pnl):
        return {"fills": 0, "contracts": 0, "passes": False}
    mean, se = day_cluster_se(pnl, day_arr)
    split = len(pnl) // 2
    totals = {
        str(day): float(pnl[day_arr == day].sum())
        for day in np.unique(day_arr)
    }
    remove_n = max(1, math.ceil(0.10 * len(totals)))
    removed = set(
        sorted(totals, key=lambda day: totals[day], reverse=True)[:remove_n]
    )
    trimmed = pnl[~np.isin(day_arr, list(removed))]
    gross = defaultdict(float)
    for row in rows:
        gross[row["settled_at"][:10]] += max(float(row["pnl"]), 0.0)
    gross_total = sum(gross.values())
    out = {
        "fills": len(rows),
        "contracts": len(pnl),
        "pnl_dollars": sum(float(row["pnl"]) for row in rows),
        "fees_dollars": sum(float(row["fee"]) for row in rows),
        "mean_c_per_contract": mean * 100.0,
        "lo95_c_per_contract": (mean - 1.96 * se) * 100.0,
        "halves_c_per_contract": [
            float(np.mean(pnl[:split]) * 100.0),
            float(np.mean(pnl[split:]) * 100.0),
        ],
        "extra_1c_stress_c_per_contract": mean * 100.0 - 1.0,
        "mean_ex_best_10pct_days_c_per_contract": (
            float(np.mean(trimmed) * 100.0) if len(trimmed) else None
        ),
        "max_day_gross_positive_share": (
            max(gross.values()) / gross_total if gross_total else None
        ),
        "daily_pnl_dollars": totals,
        "first_settlement": rows[0]["settled_at"],
        "last_settlement": rows[-1]["settled_at"],
    }
    out["passes"] = bool(
        out["fills"] >= 100
        and out["lo95_c_per_contract"] > 0
        and all(value > 0 for value in out["halves_c_per_contract"])
        and out["extra_1c_stress_c_per_contract"] > 0
        and out["mean_ex_best_10pct_days_c_per_contract"] > 0
        and out["max_day_gross_positive_share"] <= 0.20
    )
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=INPUT)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    rows = list(csv.DictReader(args.input.open()))
    live_crypto = [
        row
        for row in rows
        if row["adapter"] == "live"
        and row["metal"] in ("btc", "eth")
        and row["tag"] == "crypto_fav"
    ]
    post = [
        row
        for row in live_crypto
        if row["settled_at"] >= POST_RULE_SETTLEMENT
        and 0.75 <= float(row["fill_price"]) <= 0.92
    ]
    report = {
        "source_repository": "vinilpolepalli/quantfirm",
        "source_commit": SOURCE_COMMIT,
        "rule_commit": RULE_COMMIT,
        "input_sha256": "0173cdfa5d509f9f124d6fc339624d56563b7328a737537ee2d32b43153bf226",
        "provenance": (
            "external public ledger; code records adapter=live only after "
            "a production IOC response reports a qualifying fill"
        ),
        "post_rule_settlement_cutoff": POST_RULE_SETTLEMENT,
        "all_public_live_crypto": {
            asset: summarize([row for row in live_crypto if row["metal"] == asset])
            for asset in ("btc", "eth")
        },
        "post_rule_75_92": {
            asset: summarize([row for row in post if row["metal"] == asset])
            for asset in ("btc", "eth")
        },
        "post_rule_75_92_combined": summarize(post),
    }
    args.output.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
