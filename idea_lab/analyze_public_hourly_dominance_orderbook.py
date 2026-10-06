"""Analyze direct-orderbook dominance capture from Amendment 5."""
from __future__ import annotations

import argparse
import gzip
import json
import math
import re
from pathlib import Path


def fee(p: float) -> float:
    return math.ceil(0.07 * p * (1.0 - p) * 100.0 - 1e-9) / 100.0


def ask(book: dict, side: str) -> tuple[float, float] | None:
    if side == "yes":
        bid, size = book.get("no_bid"), book.get("no_bid_size")
    else:
        bid, size = book.get("yes_bid"), book.get("yes_bid_size")
    if bid is None or size is None:
        return None
    return 1.0 - float(bid), float(size)


def index_name(rules: object) -> str | None:
    text = str(rules or "").upper().replace("_", "")
    if "BRTI" in text:
        return "BTCUSDRTI"
    if "ERTI" in text:
        return "ETHUSDRTI"
    match = re.search(r"\b([A-Z]+USDRTI)\b", text)
    return match.group(1) if match else None


def valid_meta(meta: dict) -> bool:
    names = [
        index_name(meta.get(f"{leg}_rules_primary"))
        for leg in ("m15", "lower", "upper")
    ]
    closes = [
        meta.get(f"{leg}_close_time") for leg in ("m15", "lower", "upper")
    ]
    expected = f"{meta.get('asset')}USDRTI"
    return bool(
        names[0] is not None
        and all(name == expected for name in names)
        and len(set(closes)) == 1
        and float(meta["lower_strike"]) < float(meta["k15"])
        and float(meta["upper_strike"]) >= float(meta["k15"])
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "journal",
        nargs="?",
        default="idea_lab/public_hourly_dominance_orderbook_20261006.jsonl.gz",
    )
    ap.add_argument(
        "--output", default="idea_lab/public_hourly_dominance_orderbook_report.json"
    )
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
            pass

    first = {}
    max_edges = {}
    hourly_first = {}
    hourly_max = {}
    valid = 0
    invalid_integrity = 0
    for row in rows:
        meta, rr = row["meta"], row["responses"]
        if not valid_meta(meta):
            invalid_integrity += 1
            continue
        if any(rr[k].get("book") is None for k in ("m15", "lower", "upper")):
            continue
        starts = [rr[k]["request_start_ns"] for k in ("m15", "lower", "upper")]
        finishes = [rr[k]["request_finish_ns"] for k in ("m15", "lower", "upper")]
        span_ms = (max(finishes) - min(starts)) / 1e6
        if span_ms > 750 or max(finishes) >= meta["close_ts"] * 1_000_000_000:
            continue
        m15, lo, hi = (rr[k]["book"] for k in ("m15", "lower", "upper"))
        candidates = []
        pairs = [
            ("lower_yes_plus_15m_no", ask(lo, "yes"), ask(m15, "no")),
            ("15m_yes_plus_upper_no", ask(m15, "yes"), ask(hi, "no")),
        ]
        for side, a, b in pairs:
            if not a or not b:
                continue
            edge = 1.0 - a[0] - b[0] - fee(a[0]) - fee(b[0])
            candidates.append((side, edge, min(a[1], b[1])))
        if not candidates:
            continue
        valid += 1
        key = f"{meta['asset']}:{meta['close_ts']}"
        best = max(candidates, key=lambda x: x[1])
        max_edges[key] = max(max_edges.get(key, -10.0), best[1])
        if best[1] >= 0.02 - 1e-12 and best[2] >= 1 and key not in first:
            first[key] = {
                "asset": meta["asset"],
                "close_ts": meta["close_ts"],
                "side": best[0],
                "post_fee_edge_c": 100 * best[1],
                "after_1c_per_leg_stress_c": 100 * (best[1] - 0.02),
                "paired_size": best[2],
                "request_span_ms": span_ms,
            }
        a, b = ask(lo, "yes"), ask(hi, "no")
        if a and b:
            edge = 1.0 - a[0] - b[0] - fee(a[0]) - fee(b[0])
            size = min(a[1], b[1])
            hourly_max[key] = max(hourly_max.get(key, -10.0), edge)
            if edge >= 0.02 - 1e-12 and size >= 1 and key not in hourly_first:
                hourly_first[key] = {
                    "asset": meta["asset"],
                    "close_ts": meta["close_ts"],
                    "side": "lower_yes_plus_upper_no",
                    "post_fee_edge_c": 100 * edge,
                    "after_1c_per_leg_stress_c": 100 * (edge - 0.02),
                    "paired_size": size,
                    "request_span_ms": span_ms,
                }
    report = {
        "preregistration": "PREREG_btc_hourly_dominance_20261006.md Amendment 5",
        "snapshots": len(rows),
        "valid_snapshots": valid,
        "invalid_integrity_snapshots": invalid_integrity,
        "asset_hours": len(max_edges),
        "signals": list(first.values()),
        "max_edge_by_asset_hour_c": {
            k: 100 * v for k, v in sorted(max_edges.items())
        },
        "hourly_internal_signals": list(hourly_first.values()),
        "hourly_internal_max_edge_by_asset_hour_c": {
            k: 100 * v for k, v in sorted(hourly_max.items())
        },
        "evidence": "prospective direct-depth size-aware non-atomic REST paper",
    }
    Path(args.output).write_text(json.dumps(report, indent=2, sort_keys=True))
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
