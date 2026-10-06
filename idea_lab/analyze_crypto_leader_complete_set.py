"""Analyze direct KXCRYPTOLEAD15M complete-set snapshots."""
from __future__ import annotations

import argparse
import gzip
import json
import math
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


def ask_prices(legs: dict) -> tuple[dict[str, float], dict[str, float], dict[str, float]]:
    yes, no, sizes = {}, {}, {}
    for asset in ASSETS:
        depth = legs[asset]["book"]
        yes[asset] = 1.0 - float(depth["no_bid"])
        no[asset] = 1.0 - float(depth["yes_bid"])
        sizes[f"yes:{asset}"] = float(depth.get("no_bid_size") or 0)
        sizes[f"no:{asset}"] = float(depth.get("yes_bid_size") or 0)
    return yes, no, sizes


def construction(name: str, prices: list[float], payout: float, sizes: list[float]) -> dict:
    cost = sum(price + fee(price) for price in prices)
    return {
        "name": name,
        "legs": len(prices),
        "cost": cost,
        "worst_payout": payout,
        "margin": payout - cost,
        "margin_after_1c_per_leg": payout - cost - 0.01 * len(prices),
        "displayed_size": min(sizes) if sizes else 0.0,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "capture",
        type=Path,
        nargs="?",
        default=Path("idea_lab/crypto_leader_complete_set_live_20261006.jsonl.gz"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("idea_lab/crypto_leader_complete_set_live_report.json"),
    )
    args = parser.parse_args()
    counts = {"rows": 0, "complete": 0}
    best: dict[str, dict] = {}
    signals = []
    events = set()
    for line in open_rows(args.capture):
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        counts["rows"] += 1
        if not row.get("complete"):
            continue
        counts["complete"] += 1
        events.add(row["event"]["event_ticker"])
        yes, no, sizes = ask_prices(row["legs"])
        builds = [
            construction(
                "all_yes",
                [yes[a] for a in ASSETS],
                0.99,
                [sizes[f"yes:{a}"] for a in ASSETS],
            ),
            construction(
                "all_no",
                [no[a] for a in ASSETS],
                4.00,
                [sizes[f"no:{a}"] for a in ASSETS],
            ),
        ]
        for omitted in ASSETS:
            chosen = [asset for asset in ASSETS if asset != omitted]
            builds.append(
                construction(
                    f"four_no_omit_{omitted}",
                    [no[a] for a in chosen],
                    3.00,
                    [sizes[f"no:{a}"] for a in chosen],
                )
            )
        for build in builds:
            name = build["name"]
            if name not in best or build["margin"] > best[name]["margin"]:
                best[name] = {**build, "event": row["event"]["event_ticker"]}
            if build["margin"] > 0:
                signals.append(
                    {
                        **build,
                        "event": row["event"]["event_ticker"],
                        "wall_ns": row["wall_ns"],
                    }
                )
    report = {
        "preregistration": "PREREG_crypto_leader_complete_set_20261006.md",
        "evidence": "prospective sequential direct-orderbook paper",
        **counts,
        "events": len(events),
        "best": best,
        "signals": signals,
        "passes_direct_book_screen": any(
            row["margin_after_1c_per_leg"] > 0 and row["displayed_size"] >= 1
            for row in signals
        ),
    }
    args.output.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
