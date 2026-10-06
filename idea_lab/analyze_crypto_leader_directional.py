"""Analyze direct leader/directional implication books."""
from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path

from common import fee

ASSETS = ("BTC", "ETH", "SOL", "XRP", "HYPE")


def open_rows(path: Path):
    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            for line in handle:
                yield line
    except (EOFError, gzip.BadGzipFile):
        return


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "capture",
        type=Path,
        nargs="?",
        default=Path("idea_lab/crypto_leader_directional_live_20261006.jsonl.gz"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("idea_lab/crypto_leader_directional_live_report.json"),
    )
    args = parser.parse_args()
    rows = complete = 0
    events = set()
    best = {}
    signals = []
    for line in open_rows(args.capture):
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        rows += 1
        if row.get("complete"):
            complete += 1
        event = row["meta"]["leader"]["event_ticker"]
        events.add(event)
        responses = row["responses"]
        for leader_asset in ASSETS:
            leader = responses[f"leader:{leader_asset}"]["book"]
            ui = responses[f"direction:{leader_asset}"]["book"]
            if (
                not leader
                or leader.get("yes_bid") is None
                or not ui
                or ui.get("no_bid") is None
            ):
                continue
            for positive_asset in ASSETS:
                if positive_asset == leader_asset:
                    continue
                uj = responses[f"direction:{positive_asset}"]["book"]
                if not uj or uj.get("yes_bid") is None:
                    continue
                prices = [
                    1.0 - float(leader["yes_bid"]),
                    1.0 - float(uj["yes_bid"]),
                    1.0 - float(ui["no_bid"]),
                ]
                sizes = [
                    float(leader.get("yes_bid_size") or 0),
                    float(uj.get("yes_bid_size") or 0),
                    float(ui.get("no_bid_size") or 0),
                ]
                cost = sum(price + fee(price) for price in prices)
                item = {
                    "relation": f"L_{leader_asset}+U_{positive_asset}=>U_{leader_asset}",
                    "event": event,
                    "cost": cost,
                    "margin": 1.0 - cost,
                    "margin_after_1c_per_leg": 1.0 - cost - 0.03,
                    "displayed_size": min(sizes),
                    "wall_ns": row["wall_ns"],
                }
                name = item["relation"]
                if name not in best or item["margin"] > best[name]["margin"]:
                    best[name] = item
                if item["margin"] > 0:
                    signals.append(item)
    report = {
        "preregistration": "PREREG_crypto_leader_directional_implication_20261006.md",
        "evidence": "prospective sequential direct-orderbook paper",
        "rows": rows,
        "complete_rows": complete,
        "events": len(events),
        "best": best,
        "signals": signals,
        "passes_direct_book_screen": any(
            item["margin_after_1c_per_leg"] > 0 and item["displayed_size"] >= 1
            for item in signals
        ),
    }
    args.output.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
