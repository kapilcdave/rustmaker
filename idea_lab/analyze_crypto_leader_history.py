"""Score the preregistered historical Coin Race complete-set sample."""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import numpy as np

from common import fee

ASSETS = ("BTC", "ETH", "SOL", "XRP", "HYPE")
SOURCE = Path("idea_lab/crypto_leader_history_sample.json")
OUTPUT = Path("idea_lab/crypto_leader_history_report.json")


def evaluate(prices: list[float], payout: float) -> tuple[float, float]:
    cost = sum(price + fee(price) for price in prices)
    return payout - cost, payout - cost - 0.01 * len(prices)


def main() -> None:
    data = json.loads(SOURCE.read_text())
    opportunities = []
    best: dict[str, dict] = {}
    events = data.get("events") or {}
    invalid = 0
    observations = 0
    for event, event_row in events.items():
        legs = event_row["legs"]
        if set(legs) != set(ASSETS):
            invalid += 1
            continue
        by_asset = {
            asset: {int(row["ts"]): row for row in legs[asset]["bars"]}
            for asset in ASSETS
        }
        common = set.intersection(*(set(rows) for rows in by_asset.values()))
        if not common:
            invalid += 1
            continue
        for stamp in sorted(common):
            rows = {asset: by_asset[asset][stamp] for asset in ASSETS}
            if any(
                row.get("bid") is None or row.get("ask") is None
                for row in rows.values()
            ):
                continue
            observations += 1
            yes = {asset: float(rows[asset]["ask"]) for asset in ASSETS}
            no = {asset: 1.0 - float(rows[asset]["bid"]) for asset in ASSETS}
            builds = {
                "all_yes": evaluate([yes[a] for a in ASSETS], 0.99),
                "all_no": evaluate([no[a] for a in ASSETS], 4.00),
            }
            for omitted in ASSETS:
                builds[f"four_no_omit_{omitted}"] = evaluate(
                    [no[a] for a in ASSETS if a != omitted], 3.00
                )
            for name, (margin, stressed) in builds.items():
                row = {
                    "event": event,
                    "ts": stamp,
                    "name": name,
                    "margin": margin,
                    "margin_after_1c_per_leg": stressed,
                }
                if name not in best or margin > best[name]["margin"]:
                    best[name] = row
                if margin > 0:
                    opportunities.append(row)
    report = {
        "preregistration": "PREREG_crypto_leader_complete_set_20261006.md",
        "evidence": "historical same-minute candle closes; non-atomic",
        "events": len(events),
        "invalid_events": invalid,
        "observations": observations,
        "best": best,
        "opportunities": len(opportunities),
        "stressed_opportunities": sum(
            row["margin_after_1c_per_leg"] > 0 for row in opportunities
        ),
        "opportunity_events": len({row["event"] for row in opportunities}),
        "margin_c_quantiles": (
            {
                str(q): float(np.quantile([row["margin"] for row in opportunities], q)) * 100
                for q in (0.0, 0.25, 0.5, 0.75, 1.0)
            }
            if opportunities
            else {}
        ),
    }
    OUTPUT.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
