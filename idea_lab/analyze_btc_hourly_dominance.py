"""Score PREREG_btc_hourly_dominance_20261006.md."""
from __future__ import annotations

import argparse
import json
import math
import re
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path

import numpy as np


def fee(p: float) -> float:
    return math.ceil(0.07 * p * (1.0 - p) * 100.0 - 1e-9) / 100.0


def result_yes(x: dict) -> bool:
    return x.get("result") == "yes"


def day_ci(values: list[float], days: list[int], draws: int = 20_000) -> tuple[float, float]:
    by_day: dict[int, list[float]] = defaultdict(list)
    for v, d in zip(values, days):
        by_day[d].append(v)
    groups = list(by_day.values())
    if not groups:
        return float("nan"), float("nan")
    rng = np.random.default_rng(20261006)
    sims = []
    for _ in range(draws):
        chosen = rng.integers(0, len(groups), len(groups))
        flat = [v for j in chosen for v in groups[j]]
        sims.append(float(np.mean(flat)))
    return tuple(float(x) for x in np.quantile(sims, [0.025, 0.975]))


def common_bars(a: list[dict], b: list[dict]) -> list[tuple[int, dict, dict]]:
    aa = {int(x["ts"]): x for x in a if x.get("ts") is not None}
    bb = {int(x["ts"]): x for x in b if x.get("ts") is not None}
    return [(t, aa[t], bb[t]) for t in sorted(aa.keys() & bb.keys())]


def same_expiration_value(a: object, b: object) -> bool:
    try:
        return Decimal(str(a).replace(",", "")) == Decimal(str(b).replace(",", ""))
    except (InvalidOperation, ValueError):
        return False


def index_name(rules: object) -> str | None:
    text = str(rules or "").upper()
    match = re.search(r"\b(BRTI|ERTI|[A-Z]+USD_RTI)\b", text)
    return match.group(1) if match else None


def summarize_opportunities(opportunities: list[dict]) -> dict:
    edges = [x["guaranteed_edge"] for x in opportunities]
    days = [x["day"] for x in opportunities]
    realized = [x["realized_pnl"] for x in opportunities]
    order = np.argsort([x["close_ts"] for x in opportunities])
    half = len(order) // 2
    by_day: dict[int, float] = defaultdict(float)
    for x in opportunities:
        by_day[x["day"]] += max(0.0, x["guaranteed_edge"])
    day_totals = sorted(by_day.values(), reverse=True)
    trim_n = max(1, math.ceil(0.10 * len(day_totals))) if len(day_totals) > 1 else 0
    trimmed_days = set(
        day for day, _ in sorted(by_day.items(), key=lambda kv: kv[1], reverse=True)[:trim_n]
    )
    return {
        "mean_guaranteed_edge_c": 100 * float(np.mean(edges)),
        "median_guaranteed_edge_c": 100 * float(np.median(edges)),
        "mean_realized_pnl_c": 100 * float(np.mean(realized)),
        "stress_per_leg_c": {
            str(s): 100 * (float(np.mean(edges)) - 2 * s / 100.0)
            for s in (0.0, 0.5, 1.0)
        },
        "day_cluster_ci_after_1c_per_leg_stress_c": [
            100 * (x - 0.02) for x in day_ci(edges, days)
        ],
        "chronological_halves_after_1c_per_leg_stress_c": [
            100 * (float(np.mean([edges[i] for i in order[:half]])) - 0.02) if half else None,
            100 * (float(np.mean([edges[i] for i in order[half:]])) - 0.02),
        ],
        "mean_after_removing_best_10pct_days_and_1c_per_leg_stress_c": (
            100
            * (
                float(
                    np.mean(
                        [
                            x["guaranteed_edge"]
                            for x in opportunities
                            if x["day"] not in trimmed_days
                        ]
                    )
                )
                - 0.02
            )
            if len(trimmed_days) < len(by_day)
            else None
        ),
        "largest_day_share_of_gross_positive_edge": (
            max(day_totals) / sum(day_totals) if sum(day_totals) else None
        ),
    }


def passes_gate(integrity: dict, opportunities: list[dict], summary: dict) -> bool:
    return bool(
        integrity["hours_with_both_brackets"] >= 100
        and integrity["utc_days"] >= 30
        and integrity["matching_expiration_values"] == integrity["hours_with_both_brackets"]
        and integrity["matching_index_rules"] == integrity["hours_with_both_brackets"]
        and not integrity["settlement_dominance_violations"]
        and len(opportunities) >= 25
        and summary["stress_per_leg_c"]["1.0"] > 0
        and summary["day_cluster_ci_after_1c_per_leg_stress_c"][0] > 0
        and all(
            x is not None and x > 0
            for x in summary["chronological_halves_after_1c_per_leg_stress_c"]
        )
        and summary["mean_after_removing_best_10pct_days_and_1c_per_leg_stress_c"] is not None
        and summary["mean_after_removing_best_10pct_days_and_1c_per_leg_stress_c"] > 0
        and summary["largest_day_share_of_gross_positive_edge"] is not None
        and summary["largest_day_share_of_gross_positive_edge"] <= 0.20
    )


def descriptive_edges(rows: list[dict]) -> dict:
    if not rows:
        return {}
    values = np.asarray([x["edge"] for x in rows], float)
    return {
        "hours": len(rows),
        "mean_c": 100 * float(np.mean(values)),
        "max_c": 100 * float(np.max(values)),
        "p50_c": 100 * float(np.quantile(values, 0.50)),
        "p90_c": 100 * float(np.quantile(values, 0.90)),
        "p99_c": 100 * float(np.quantile(values, 0.99)),
        "positive_hours": int(np.sum(values > 0)),
        "over_1c_hours": int(np.sum(values >= 0.01 - 1e-12)),
        "over_2c_hours": int(np.sum(values >= 0.02 - 1e-12)),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("panel", nargs="?", default="idea_lab/btc_hourly_dominance_panel.json")
    ap.add_argument("--output", default="idea_lab/btc_hourly_dominance_report.json")
    args = ap.parse_args()
    panel = json.loads(Path(args.panel).read_text())

    integrity = {
        "hours": 0,
        "matching_expiration_values": 0,
        "matching_index_rules": 0,
        "settlement_dominance_checks": 0,
        "settlement_dominance_violations": [],
        "hours_with_both_brackets": 0,
        "utc_days": 0,
    }
    opportunities = []
    best_edges = []
    hourly_opportunities = []
    hourly_best_edges = []
    all_days = set()
    for key, row in sorted(panel.items(), key=lambda kv: int(kv[0])):
        if not row.get("complete"):
            continue
        close = int(row["close_ts"])
        day = close // 86400
        all_days.add(day)
        m15 = row["m15"]
        ladder = row.get("hourly") or []
        lower = [m for m in ladder if m["strike"] < m15["strike"]]
        upper = [m for m in ladder if m["strike"] >= m15["strike"]]
        integrity["hours"] += 1
        if ladder and all(
            same_expiration_value(m.get("expiration_value"), m15.get("expiration_value"))
            for m in ladder
        ):
            integrity["matching_expiration_values"] += 1
        if ladder and index_name(m15.get("rules_primary")) is not None and all(
            index_name(m.get("rules_primary")) == index_name(m15.get("rules_primary"))
            for m in ladder
        ):
            integrity["matching_index_rules"] += 1
        for m in ladder:
            if m["strike"] < m15["strike"]:
                ok = not result_yes(m15) or result_yes(m)
            else:
                ok = not result_yes(m) or result_yes(m15)
            integrity["settlement_dominance_checks"] += 1
            if not ok:
                integrity["settlement_dominance_violations"].append(
                    {"close_ts": close, "m15": m15["ticker"], "hourly": m["ticker"]}
                )
        if not lower or not upper:
            continue
        integrity["hours_with_both_brackets"] += 1
        candidates = []
        lo = max(lower, key=lambda m: m["strike"])
        hi = min(upper, key=lambda m: m["strike"])
        # lower YES + 15m NO
        for t, b15, bh in common_bars(m15["bars"], lo["bars"]):
            if not close - 13 * 60 <= t <= close - 60:
                continue
            if bh.get("a") is None or b15.get("b") is None:
                continue
            p1, p2 = float(bh["a"]), 1.0 - float(b15["b"])
            edge = 1.0 - p1 - p2 - fee(p1) - fee(p2)
            candidates.append((t, edge, "lower_yes_plus_15m_no", lo, p1, p2))
        # 15m YES + upper NO
        for t, b15, bh in common_bars(m15["bars"], hi["bars"]):
            if not close - 13 * 60 <= t <= close - 60:
                continue
            if b15.get("a") is None or bh.get("b") is None:
                continue
            p1, p2 = float(b15["a"]), 1.0 - float(bh["b"])
            edge = 1.0 - p1 - p2 - fee(p1) - fee(p2)
            candidates.append((t, edge, "15m_yes_plus_upper_no", hi, p1, p2))
        hourly_candidates = []
        for t, blo, bhi in common_bars(lo["bars"], hi["bars"]):
            if not close - 13 * 60 <= t <= close - 60:
                continue
            if blo.get("a") is None or bhi.get("b") is None:
                continue
            p1, p2 = float(blo["a"]), 1.0 - float(bhi["b"])
            edge = 1.0 - p1 - p2 - fee(p1) - fee(p2)
            hourly_candidates.append((t, edge, p1, p2))
        if hourly_candidates:
            hourly_best_edges.append(
                {"close_ts": close, "day": day, "edge": max(x[1] for x in hourly_candidates)}
            )
        hourly_eligible = [x for x in hourly_candidates if x[1] >= 0.02 - 1e-12]
        if hourly_eligible:
            first_t = min(x[0] for x in hourly_eligible)
            t, edge, p1, p2 = max(
                (x for x in hourly_eligible if x[0] == first_t),
                key=lambda x: x[1],
            )
            payout = (1.0 if result_yes(lo) else 0.0) + (0.0 if result_yes(hi) else 1.0)
            hourly_opportunities.append(
                {
                    "close_ts": close,
                    "day": day,
                    "decision_ts": t,
                    "side": "lower_yes_plus_upper_no",
                    "lower": lo["ticker"],
                    "upper": hi["ticker"],
                    "strike_gap": float(hi["strike"]) - float(lo["strike"]),
                    "leg_prices": [p1, p2],
                    "guaranteed_edge": edge,
                    "realized_pnl": payout - p1 - p2 - fee(p1) - fee(p2),
                }
            )
        eligible = [x for x in candidates if x[1] >= 0.02 - 1e-12]
        if candidates:
            best_edges.append(
                {
                    "close_ts": close,
                    "day": day,
                    "edge": max(x[1] for x in candidates),
                }
            )
        if eligible:
            first_t = min(x[0] for x in eligible)
            best = max((x for x in eligible if x[0] == first_t), key=lambda x: x[1])
            t, edge, side, mh, p1, p2 = best
            payout = (
                (1.0 if result_yes(mh) else 0.0) + (0.0 if result_yes(m15) else 1.0)
                if side.startswith("lower")
                else (1.0 if result_yes(m15) else 0.0) + (0.0 if result_yes(mh) else 1.0)
            )
            opportunities.append(
                {
                    "close_ts": close,
                    "day": day,
                    "decision_ts": t,
                    "side": side,
                    "m15": m15["ticker"],
                    "hourly": mh["ticker"],
                    "strike_gap": abs(float(mh["strike"]) - float(m15["strike"])),
                    "leg_prices": [p1, p2],
                    "guaranteed_edge": edge,
                    "realized_pnl": payout - p1 - p2 - fee(p1) - fee(p2),
                }
            )
    integrity["utc_days"] = len(all_days)

    report = {
        "preregistration": "PREREG_btc_hourly_dominance_20261006.md",
        "evidence": "historical non-atomic one-minute candle-close upper bound",
        "integrity": integrity,
        "opportunities": len(opportunities),
        "hourly_internal_opportunities": len(hourly_opportunities),
        "descriptive_best_edge_per_hour": descriptive_edges(best_edges),
        "hourly_internal_descriptive_best_edge_per_hour": descriptive_edges(hourly_best_edges),
    }
    if opportunities:
        report["summary"] = summarize_opportunities(opportunities)
        report["passes_historical_promotion_gate"] = passes_gate(
            integrity, opportunities, report["summary"]
        )
    else:
        report["passes_historical_promotion_gate"] = False
    if hourly_opportunities:
        report["hourly_internal_summary"] = summarize_opportunities(hourly_opportunities)
        report["hourly_internal_passes_historical_promotion_gate"] = passes_gate(
            integrity, hourly_opportunities, report["hourly_internal_summary"]
        )
    else:
        report["hourly_internal_passes_historical_promotion_gate"] = False
    Path(args.output).write_text(json.dumps(report, indent=2, sort_keys=True))
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
