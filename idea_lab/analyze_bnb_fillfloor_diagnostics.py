"""Post-hoc mechanism diagnostics for the frozen BNB fill-floor result."""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import numpy as np

SOURCE = Path("idea_lab/bnb_passive_fillfloor_report.json")
OUTPUT = Path("idea_lab/bnb_passive_fillfloor_diagnostics.json")


def summarize(submissions: list[dict], fills: list[dict]) -> dict:
    fill_by_ticker = {row["ticker"]: row for row in fills}
    pnl = np.asarray([float(row["pnl"]) for row in submissions])
    filled = [fill_by_ticker[row["ticker"]] for row in submissions if row["ticker"] in fill_by_ticker]
    return {
        "submissions": len(submissions),
        "fills": len(filled),
        "fill_rate": len(filled) / len(submissions) if submissions else None,
        "mean_per_submission_c": float(pnl.mean() * 100) if len(pnl) else None,
        "one_cent_stressed_per_submission_c": (
            float((pnl.sum() - 0.01 * len(filled)) / len(pnl) * 100)
            if len(pnl)
            else None
        ),
        "mean_per_fill_c": (
            float(np.mean([row["pnl"] for row in filled]) * 100) if filled else None
        ),
        "seconds_to_fill": (
            {
                "p10": float(np.quantile([row["seconds_to_fill"] for row in filled], 0.10)),
                "p50": float(np.quantile([row["seconds_to_fill"] for row in filled], 0.50)),
                "p90": float(np.quantile([row["seconds_to_fill"] for row in filled], 0.90)),
            }
            if filled
            else {}
        ),
    }


def main() -> None:
    report = json.loads(SOURCE.read_text())
    governing = next(
        row for row in report["scores"] if row["latency_s"] == 2 and row["stress"] == 0.0
    )
    submissions = governing["submission_rows"]
    fills = governing["filled_rows"]
    fill_by_ticker = {row["ticker"]: row for row in fills}

    # k and side live in cached fetched rows, while the compact submission rows
    # intentionally retain only accounting fields. Recover them by ticker.
    cached = {}
    for line in Path("idea_lab/bnb_passive_fillfloor_tapes.jsonl").read_text().splitlines():
        item = json.loads(line)
        cached[item["ticker"]] = item
    for row in submissions:
        source = cached[row["ticker"]]
        row["k"] = source["k"]
        row["side"] = source["side"]
        row["limit"] = source["limit"]

    groups: dict[str, dict[str, list[dict]]] = {
        "k": defaultdict(list),
        "side": defaultdict(list),
        "limit_bin": defaultdict(list),
    }
    edges = (0.0, 0.10, 0.25, 0.50, 0.75, 0.90, 1.01)
    for row in submissions:
        groups["k"][str(row["k"])].append(row)
        groups["side"][row["side"]].append(row)
        for lo, hi in zip(edges[:-1], edges[1:]):
            if lo <= row["limit"] < hi:
                groups["limit_bin"][f"{lo:.2f}-{hi:.2f}"].append(row)
                break
    output = {
        "status": "post-hoc diagnostics only; no rule selection authority",
        "overall": summarize(submissions, fills),
        "groups": {
            kind: {
                name: summarize(rows, [fill_by_ticker[r["ticker"]] for r in rows if r["ticker"] in fill_by_ticker])
                for name, rows in sorted(values.items())
            }
            for kind, values in groups.items()
        },
    }
    OUTPUT.write_text(json.dumps(output, indent=2))
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
