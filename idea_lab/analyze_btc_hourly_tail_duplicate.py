"""Score PREREG_btc_hourly_tail_duplicate_20261006.md."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from analyze_btc_hourly_dominance import (
    common_bars,
    descriptive_edges,
    fee,
    passes_gate,
    summarize_opportunities,
)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("panel", nargs="?", default="idea_lab/btc_hourly_tail_duplicate_panel.json")
    ap.add_argument("--output", default="idea_lab/btc_hourly_tail_duplicate_report.json")
    args = ap.parse_args()
    panel = json.loads(Path(args.panel).read_text())

    integrity = {
        "hours": 0,
        "hours_with_pairs": 0,
        "hours_with_both_brackets": 0,
        "matching_expiration_values": 0,
        "matching_index_rules": 0,
        "utc_days": 0,
        "identity_violations": [],
        "settlement_dominance_violations": [],
    }
    all_days = set()
    opportunities = []
    best_edges = []
    for key, row in sorted(panel.items(), key=lambda kv: int(kv[0])):
        if not row.get("complete"):
            continue
        close = int(row["close_ts"])
        day = close // 86400
        all_days.add(day)
        integrity["hours"] += 1
        pair = row.get("pair")
        if not pair:
            continue
        integrity["hours_with_pairs"] += 1
        integrity["hours_with_both_brackets"] += 1
        left, right = pair["range"], pair["directional"]
        exact_identity = bool(
            pair.get("identity_ok")
            and left.get("ticker", "").split("-", 1)[1]
            == right.get("ticker", "").split("-", 1)[1]
            and left.get("rules_primary") == right.get("rules_primary")
            and left.get("close_time") == right.get("close_time")
            and str(left.get("expiration_value")) == str(right.get("expiration_value"))
            and str(left.get("strike")) == str(right.get("strike"))
        )
        if exact_identity:
            integrity["matching_expiration_values"] += 1
            integrity["matching_index_rules"] += 1
        else:
            integrity["identity_violations"].append(
                {"close_ts": close, "range": left["ticker"], "directional": right["ticker"]}
            )
            continue
        if left.get("result") != right.get("result"):
            integrity["settlement_dominance_violations"].append(
                {"close_ts": close, "range": left["ticker"], "directional": right["ticker"]}
            )
            continue

        candidates = []
        for t, bl, br in common_bars(left["bars"], right["bars"]):
            if not close - 13 * 60 <= t <= close - 60:
                continue
            if bl.get("a") is not None and br.get("b") is not None:
                prices = [float(bl["a"]), 1.0 - float(br["b"])]
                edge = 1.0 - sum(prices) - sum(fee(p) for p in prices)
                candidates.append((t, edge, "range_yes_plus_directional_no", prices))
            if bl.get("b") is not None and br.get("a") is not None:
                prices = [1.0 - float(bl["b"]), float(br["a"])]
                edge = 1.0 - sum(prices) - sum(fee(p) for p in prices)
                candidates.append((t, edge, "range_no_plus_directional_yes", prices))
        if candidates:
            best_edges.append(
                {"close_ts": close, "day": day, "edge": max(x[1] for x in candidates)}
            )
        eligible = [x for x in candidates if x[1] >= 0.02 - 1e-12]
        if eligible:
            first_t = min(x[0] for x in eligible)
            t, edge, side, prices = max(
                (x for x in eligible if x[0] == first_t), key=lambda x: x[1]
            )
            opportunities.append(
                {
                    "close_ts": close,
                    "day": day,
                    "decision_ts": t,
                    "side": side,
                    "range": left["ticker"],
                    "directional": right["ticker"],
                    "leg_prices": prices,
                    "guaranteed_edge": edge,
                    "realized_pnl": edge,
                }
            )
    integrity["utc_days"] = len(all_days)
    gate_integrity = dict(integrity)
    if integrity["identity_violations"]:
        gate_integrity["settlement_dominance_violations"] = (
            integrity["settlement_dominance_violations"] + integrity["identity_violations"]
        )
    report = {
        "preregistration": "PREREG_btc_hourly_tail_duplicate_20261006.md",
        "evidence": "historical non-atomic one-minute candle-close upper bound",
        "integrity": integrity,
        "opportunities": len(opportunities),
        "descriptive_best_edge_per_hour": descriptive_edges(best_edges),
        "passes_historical_promotion_gate": False,
    }
    if opportunities:
        report["summary"] = summarize_opportunities(opportunities)
        report["passes_historical_promotion_gate"] = passes_gate(
            gate_integrity, opportunities, report["summary"]
        )
    Path(args.output).write_text(json.dumps(report, indent=2, sort_keys=True))
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
