"""Analyze Amendment 2's prospective paired REST snapshots."""
from __future__ import annotations

import argparse
import gzip
import json
import math
from pathlib import Path


def fee(p: float) -> float:
    return math.ceil(0.07 * p * (1.0 - p) * 100.0 - 1e-9) / 100.0


def number(x: object) -> float | None:
    if x in (None, "", "None"):
        return None
    return float(str(x).replace(",", ""))


def quote(m: dict, side: str) -> tuple[float, float] | None:
    if side == "yes":
        p, size = number(m.get("yes_ask_dollars")), number(m.get("yes_ask_size_fp"))
    else:
        # A NO ask executes against the displayed YES bid.  The REST market
        # object exposes `no_ask_dollars` but not `no_ask_size_fp`; its size is
        # therefore the corresponding `yes_bid_size_fp`.
        p, size = number(m.get("no_ask_dollars")), number(m.get("yes_bid_size_fp"))
    if p is None or size is None:
        return None
    return p, size


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("journal", nargs="?", default="idea_lab/public_hourly_dominance_20261006.jsonl.gz")
    ap.add_argument("--output", default="idea_lab/public_hourly_dominance_report.json")
    args = ap.parse_args()

    opener = gzip.open if args.journal.endswith(".gz") else open
    rows = []
    with opener(args.journal, "rt", encoding="utf-8") as fh:
        try:
            for line in fh:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        except EOFError:
            # A collector that is still running has not written the current
            # gzip member's footer yet.  Complete JSON lines yielded before the
            # partial footer remain valid for an interim report.
            pass
    first_by_hour = {}
    max_edges = {}
    hourly_first_by_hour = {}
    hourly_max_edges = {}
    valid = 0
    for row in rows:
        meta, rr = row["meta"], row["responses"]
        if any(rr[k]["market"] is None for k in ("m15", "lower", "upper")):
            continue
        starts = [rr[k]["request_start_ns"] for k in ("m15", "lower", "upper")]
        finishes = [rr[k]["request_finish_ns"] for k in ("m15", "lower", "upper")]
        span_ms = (max(finishes) - min(starts)) / 1e6
        if span_ms > 750 or max(finishes) >= meta["close_ts"] * 1_000_000_000:
            continue
        m15 = rr["m15"]["market"]
        lo = rr["lower"]["market"]
        hi = rr["upper"]["market"]
        q_lo_yes, q_15_no = quote(lo, "yes"), quote(m15, "no")
        q_15_yes, q_hi_no = quote(m15, "yes"), quote(hi, "no")
        candidates = []
        if q_lo_yes and q_15_no:
            edge = 1 - q_lo_yes[0] - q_15_no[0] - fee(q_lo_yes[0]) - fee(q_15_no[0])
            candidates.append(("lower_yes_plus_15m_no", edge, min(q_lo_yes[1], q_15_no[1])))
        if q_15_yes and q_hi_no:
            edge = 1 - q_15_yes[0] - q_hi_no[0] - fee(q_15_yes[0]) - fee(q_hi_no[0])
            candidates.append(("15m_yes_plus_upper_no", edge, min(q_15_yes[1], q_hi_no[1])))
        if not candidates:
            continue
        valid += 1
        best = max(candidates, key=lambda x: x[1])
        key = f"{meta['asset']}:{meta['close_ts']}"
        max_edges[key] = max(max_edges.get(key, -10.0), best[1])
        if best[1] >= 0.02 - 1e-12 and best[2] >= 1 and key not in first_by_hour:
            first_by_hour[key] = {
                "asset": meta["asset"],
                "close_ts": meta["close_ts"],
                "side": best[0],
                "post_fee_edge_c": 100 * best[1],
                "after_1c_per_leg_stress_c": 100 * (best[1] - 0.02),
                "paired_size": best[2],
                "request_span_ms": span_ms,
            }
        q_lo_yes, q_hi_no = quote(lo, "yes"), quote(hi, "no")
        if q_lo_yes and q_hi_no:
            edge = 1 - q_lo_yes[0] - q_hi_no[0] - fee(q_lo_yes[0]) - fee(q_hi_no[0])
            size = min(q_lo_yes[1], q_hi_no[1])
            hourly_max_edges[key] = max(hourly_max_edges.get(key, -10.0), edge)
            if edge >= 0.02 - 1e-12 and size >= 1 and key not in hourly_first_by_hour:
                hourly_first_by_hour[key] = {
                    "asset": meta["asset"],
                    "close_ts": meta["close_ts"],
                    "side": "lower_yes_plus_upper_no",
                    "post_fee_edge_c": 100 * edge,
                    "after_1c_per_leg_stress_c": 100 * (edge - 0.02),
                    "paired_size": size,
                    "request_span_ms": span_ms,
                }
    report = {
        "preregistration": "PREREG_btc_hourly_dominance_20261006.md Amendment 2",
        "snapshots": len(rows),
        "valid_snapshots": valid,
        "asset_hours": len(max_edges),
        "signals": list(first_by_hour.values()),
        "max_edge_by_asset_hour_c": {k: 100 * v for k, v in sorted(max_edges.items())},
        "hourly_internal_signals": list(hourly_first_by_hour.values()),
        "hourly_internal_max_edge_by_asset_hour_c": {
            k: 100 * v for k, v in sorted(hourly_max_edges.items())
        },
        "evidence": "prospective size-aware but non-atomic sequential REST paper",
    }
    Path(args.output).write_text(json.dumps(report, indent=2, sort_keys=True))
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
