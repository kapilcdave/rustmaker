"""Score the prospective direct-book BNB strict-through passive shadow."""
from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path

import numpy as np

from audit_altspot_passive_fillfloor import Http, fetch_one

CUTOFF_NS = 1_791_296_761_000_000_000


def rows_from_open_gzip(path: Path):
    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            for line in handle:
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue
    except (EOFError, gzip.BadGzipFile):
        return


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "journal",
        type=Path,
        nargs="?",
        default=Path("idea_lab/bnb_altspot_live_20261006.jsonl.gz"),
    )
    parser.add_argument(
        "--cache",
        type=Path,
        default=Path("idea_lab/bnb_passive_live_tapes.jsonl"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("idea_lab/bnb_passive_live_report.json"),
    )
    args = parser.parse_args()
    signals = []
    for row in rows_from_open_gzip(args.journal):
        if row.get("kind") != "decision" or not row.get("signal"):
            continue
        if int(row.get("wall_ns", 0)) < CUTOFF_NS:
            continue
        side = row["signal"]["side"]
        depth = row["book"]
        if side == "yes":
            limit = depth.get("yes_bid")
            yes_boundary = limit
            displayed = depth.get("yes_bid_size")
        else:
            limit = depth.get("no_bid")
            yes_boundary = None if limit is None else 1.0 - float(limit)
            displayed = depth.get("no_bid_size")
        if limit is None or yes_boundary is None:
            continue
        signals.append(
            {
                "asset": "BNB",
                "ticker": row["market"]["ticker"],
                "t0": row["market"]["open_ts"],
                "close_ts": row["market"]["close_ts"],
                "decision_ts": int(row["book_request_finish_ns"] // 1_000_000_000),
                "ready_us": int(row["book_request_finish_ns"] // 1_000 + 2_000_000),
                "day": row["market"]["open_ts"] // 86400,
                "k": row["k"],
                "side": side,
                "limit": float(limit),
                "yes_boundary": float(yes_boundary),
                "result": "",
                "displayed_size": displayed,
            }
        )

    cached = {}
    if args.cache.exists():
        for line in args.cache.read_text().splitlines():
            try:
                row = json.loads(line)
                cached[row["ticker"]] = row
            except (json.JSONDecodeError, KeyError):
                continue
    http = Http(3)
    with args.cache.open("a") as handle:
        for signal in signals:
            existing = cached.get(signal["ticker"])
            if existing and existing.get("result") in ("yes", "no"):
                continue
            row = fetch_one(http, signal)
            # fetch_one receives the result from its input. Fetch market result.
            payload = http.get(f"/markets/{signal['ticker']}")
            market = (payload or {}).get("market") or {}
            result = str(market.get("result") or "").lower()
            row["result"] = result if result in ("yes", "no") else ""
            cached[row["ticker"]] = row
            handle.write(json.dumps(row, separators=(",", ":")) + "\n")
            handle.flush()

    settled = []
    for signal in signals:
        row = cached.get(signal["ticker"])
        if not row or row.get("result") not in ("yes", "no") or row.get("truncated"):
            continue
        earliest = signal["ready_us"]
        fill = None
        for print_row in row["prints"]:
            stamp, yes_price, size, taker = print_row
            if stamp < earliest or stamp >= signal["close_ts"] * 1_000_000:
                continue
            if signal["side"] == "yes":
                through = taker == "no" and yes_price < signal["yes_boundary"] - 1e-9
            else:
                through = taker == "yes" and yes_price > signal["yes_boundary"] + 1e-9
            if through:
                fill = print_row
                break
        pnl = None
        if fill is not None:
            pnl = (1.0 if row["result"] == signal["side"] else 0.0) - signal["limit"]
        settled.append({**signal, "result": row["result"], "fill": fill, "pnl": pnl})
    filled = [row for row in settled if row["pnl"] is not None]
    report = {
        "preregistration": "PREREG_bnb_passive_live_20261006.md",
        "cutoff_ns": CUTOFF_NS,
        "signals": len(signals),
        "settled_signals": len(settled),
        "strict_through_fills": len(filled),
        "fill_rate": len(filled) / len(settled) if settled else None,
        "pnl_per_signal_c": (
            sum(float(row["pnl"] or 0) for row in settled) / len(settled) * 100
            if settled
            else None
        ),
        "pnl_per_fill_c": (
            float(np.mean([row["pnl"] for row in filled])) * 100 if filled else None
        ),
        "one_cent_stressed_per_signal_c": (
            (
                sum(float(row["pnl"] or 0) for row in settled) - 0.01 * len(filled)
            )
            / len(settled)
            * 100
            if settled
            else None
        ),
        "rows": settled,
    }
    args.output.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
