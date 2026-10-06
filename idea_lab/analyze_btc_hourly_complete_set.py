"""Score PREREG_btc_hourly_complete_set_20261006.md."""
from __future__ import annotations

import argparse
import json
from decimal import Decimal
from pathlib import Path

from analyze_btc_hourly_dominance import (
    common_bars,
    descriptive_edges,
    fee,
    index_name,
    passes_gate,
    result_yes,
    same_expiration_value,
    summarize_opportunities,
)


def dec(value: object) -> Decimal:
    return Decimal(str(value).replace(",", ""))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dominance-panel", default="idea_lab/btc_hourly_dominance_panel.json")
    ap.add_argument("--bucket-panel", default="idea_lab/btc_hourly_complete_set_panel.json")
    ap.add_argument("--output", default="idea_lab/btc_hourly_complete_set_report.json")
    args = ap.parse_args()
    dominance = json.loads(Path(args.dominance_panel).read_text())
    buckets = json.loads(Path(args.bucket_panel).read_text())
    integrity = {
        "hours": 0,
        "valid_partition_hours": 0,
        "matching_expiration_values": 0,
        "matching_index_rules": 0,
        "settlement_partition_violations": [],
        "hours_with_both_brackets": 0,
        "utc_days": 0,
    }
    all_days = set()
    opportunities = []
    best_edges = []
    for key, base in sorted(dominance.items(), key=lambda kv: int(kv[0])):
        extra = buckets.get(key) or {}
        bucket = extra.get("bucket")
        if not base.get("complete") or not bucket:
            continue
        close = int(key)
        day = close // 86400
        all_days.add(day)
        integrity["hours"] += 1
        ladder = base.get("hourly") or []
        lower = [m for m in ladder if m["strike"] < base["m15"]["strike"]]
        upper = [m for m in ladder if m["strike"] >= base["m15"]["strike"]]
        if not lower or not upper:
            continue
        integrity["hours_with_both_brackets"] += 1
        lo = max(lower, key=lambda m: m["strike"])
        hi = min(upper, key=lambda m: m["strike"])
        boundaries_ok = dec(bucket["floor_strike"]) > dec(lo["strike"]) and dec(
            bucket["cap_strike"]
        ) == dec(hi["strike"])
        if boundaries_ok:
            integrity["valid_partition_hours"] += 1
        if all(
            same_expiration_value(x.get("expiration_value"), lo.get("expiration_value"))
            for x in (hi, bucket)
        ):
            integrity["matching_expiration_values"] += 1
        if index_name(lo.get("rules_primary")) is not None and all(
            index_name(x.get("rules_primary")) == index_name(lo.get("rules_primary"))
            for x in (hi, bucket)
        ):
            integrity["matching_index_rules"] += 1
        partition_winners = int(not result_yes(lo)) + int(result_yes(bucket)) + int(
            result_yes(hi)
        )
        if partition_winners != 1:
            integrity["settlement_partition_violations"].append(
                {"close_ts": close, "lower": lo["ticker"], "bucket": bucket["ticker"], "upper": hi["ticker"]}
            )
        lo_bars = {t: b for t, b, _ in common_bars(lo["bars"], bucket["bars"])}
        bucket_bars = {t: b for t, b, _ in common_bars(bucket["bars"], hi["bars"])}
        hi_bars = {int(x["ts"]): x for x in hi["bars"] if x.get("ts") is not None}
        candidates = []
        for t in sorted(lo_bars.keys() & bucket_bars.keys() & hi_bars.keys()):
            if not close - 13 * 60 <= t <= close - 60:
                continue
            blo, bc, bhi = lo_bars[t], bucket_bars[t], hi_bars[t]
            if any(x is None for x in (blo.get("b"), bc.get("a"), bhi.get("a"))):
                pass
            else:
                prices = [1 - float(blo["b"]), float(bc["a"]), float(bhi["a"])]
                edge = 1 - sum(prices) - sum(fee(p) for p in prices)
                candidates.append((t, edge, "partition", prices))
            if any(x is None for x in (blo.get("a"), bc.get("b"), bhi.get("b"))):
                continue
            prices = [float(blo["a"]), 1 - float(bc["b"]), 1 - float(bhi["b"])]
            edge = 2 - sum(prices) - sum(fee(p) for p in prices)
            candidates.append((t, edge, "complement", prices))
        if candidates:
            best_edges.append({"close_ts": close, "day": day, "edge": max(x[1] for x in candidates)})
        eligible = [x for x in candidates if x[1] >= 0.02 - 1e-12]
        if eligible:
            first = min(x[0] for x in eligible)
            t, edge, side, prices = max((x for x in eligible if x[0] == first), key=lambda x: x[1])
            payout = 1.0 if side == "partition" else 2.0
            opportunities.append(
                {
                    "close_ts": close,
                    "day": day,
                    "decision_ts": t,
                    "side": side,
                    "lower": lo["ticker"],
                    "bucket": bucket["ticker"],
                    "upper": hi["ticker"],
                    "leg_prices": prices,
                    "guaranteed_edge": edge,
                    "realized_pnl": payout - sum(prices) - sum(fee(p) for p in prices),
                }
            )
    integrity["utc_days"] = len(all_days)
    gate_integrity = {
        "hours_with_both_brackets": integrity["hours_with_both_brackets"],
        "utc_days": integrity["utc_days"],
        "matching_expiration_values": integrity["matching_expiration_values"],
        "matching_index_rules": integrity["matching_index_rules"],
        "settlement_dominance_violations": integrity["settlement_partition_violations"],
    }
    report = {
        "preregistration": "PREREG_btc_hourly_complete_set_20261006.md",
        "evidence": "historical non-atomic one-minute candle-close upper bound",
        "integrity": integrity,
        "opportunities": len(opportunities),
        "descriptive_best_edge_per_hour": descriptive_edges(best_edges),
        "passes_historical_promotion_gate": False,
    }
    if opportunities:
        report["summary"] = summarize_opportunities(opportunities)
        # Three legs receive the preregistered one-cent-per-leg stress, one
        # cent more than the shared two-leg helper.
        report["summary"]["stress_per_leg_c"]["0.5"] -= 0.5
        report["summary"]["stress_per_leg_c"]["1.0"] -= 1.0
        report["summary"]["day_cluster_ci_after_1c_per_leg_stress_c"] = [
            x - 1.0 for x in report["summary"]["day_cluster_ci_after_1c_per_leg_stress_c"]
        ]
        report["summary"]["chronological_halves_after_1c_per_leg_stress_c"] = [
            None if x is None else x - 1.0
            for x in report["summary"]["chronological_halves_after_1c_per_leg_stress_c"]
        ]
        trimmed = report["summary"]["mean_after_removing_best_10pct_days_and_1c_per_leg_stress_c"]
        if trimmed is not None:
            report["summary"]["mean_after_removing_best_10pct_days_and_1c_per_leg_stress_c"] = trimmed - 1.0
        report["passes_historical_promotion_gate"] = (
            integrity["valid_partition_hours"] == integrity["hours_with_both_brackets"]
            and passes_gate(gate_integrity, opportunities, report["summary"])
        )
    Path(args.output).write_text(json.dumps(report, indent=2, sort_keys=True))
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
