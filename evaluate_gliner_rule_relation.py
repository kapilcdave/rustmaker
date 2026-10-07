#!/usr/bin/env python3
"""Evaluate GLiNER2.5-Decide on preregistered contract-rule relations."""
from __future__ import annotations

import argparse
import itertools
import json
import time
from collections import Counter, defaultdict
from pathlib import Path

LABELS = {
    "same_event": "Both contracts resolve on exactly the same settlement predicate",
    "first_superset": "Every YES state of the second contract is also YES for the first contract",
    "first_subset": "Every YES state of the first contract is also YES for the second contract",
    "different_settlement": "The contracts use a different index or a different settlement time",
}

ASSETS = {
    "BTC": ("BRTI", 85000.0, 100.0),
    "ETH": ("ERTI", 3000.0, 5.0),
    "SOL": ("SOLUSD_RTI", 130.0, 0.25),
    "XRP": ("XRPUSD_RTI", 1.80, 0.02),
}


def rule(index: str, hour: int, strike: float, wording: int = 0) -> str:
    if wording:
        return (
            f"YES when the sixty-observation average of CF Benchmarks {index} "
            f"immediately before {hour:02d}:00 UTC is greater than {strike:g}."
        )
    return (
        f"YES if the simple average of the sixty seconds of CF Benchmarks {index} "
        f"before {hour:02d}:00 UTC is above {strike:g} at {hour:02d}:00 UTC."
    )


def cases() -> list[dict]:
    out = []
    for asset, (index, base, step) in ASSETS.items():
        for hour in (4, 12, 20, 23):
            a = rule(index, hour, base, 0)
            out.extend(
                [
                    {
                        "asset": asset,
                        "label": "same_event",
                        "text": f"FIRST: {a}\nSECOND: {rule(index, hour, base, 1)}",
                    },
                    {
                        "asset": asset,
                        "label": "first_superset",
                        "text": f"FIRST: {rule(index, hour, base-step)}\nSECOND: {a}",
                    },
                    {
                        "asset": asset,
                        "label": "first_subset",
                        "text": f"FIRST: {rule(index, hour, base+step)}\nSECOND: {a}",
                    },
                    {
                        "asset": asset,
                        "label": "different_settlement",
                        "text": f"FIRST: {a}\nSECOND: {rule(index, (hour+1)%24, base)}",
                    },
                    {
                        "asset": asset,
                        "label": "different_settlement",
                        "text": f"FIRST: {a}\nSECOND: {rule('OTHERUSD_RTI', hour, base)}",
                    },
                ]
            )
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="fastino/GLiNER2.5-Decide")
    ap.add_argument("--batch-size", type=int, default=8)
    ap.add_argument("--output", type=Path, default=Path("data/cf_exact/gliner_rule_relation.json"))
    args = ap.parse_args()
    from gliner2 import AutoExtractor

    rows = cases()
    started = time.time()
    model = AutoExtractor.from_pretrained(args.model)
    load_seconds = time.time() - started
    started = time.time()
    outputs = model.batch_classify_text(
        [x["text"] for x in rows],
        {"relation": {"labels": LABELS}},
        batch_size=args.batch_size,
        include_confidence=True,
    )
    inference_seconds = time.time() - started

    confusion = defaultdict(Counter)
    predictions = []
    for row, output in zip(rows, outputs):
        value = output.get("relation")
        predicted = value.get("label") if isinstance(value, dict) else value
        predictions.append(predicted)
        confusion[row["label"]][predicted] += 1
    recalls = {
        label: confusion[label][label] / sum(confusion[label].values())
        for label in LABELS
    }
    accuracy = sum(p == r["label"] for p, r in zip(predictions, rows)) / len(rows)
    counts = Counter(predictions)
    report = {
        "preregistration": "PREREG_gliner_rule_relation_20261006.md",
        "model": args.model,
        "cases": len(rows),
        "accuracy": accuracy,
        "recall": recalls,
        "prediction_counts": dict(counts),
        "confusion": {k: dict(v) for k, v in confusion.items()},
        "passes": (
            set(counts) == set(LABELS)
            and accuracy >= 0.80
            and min(recalls.values()) >= 0.70
        ),
        "load_seconds": load_seconds,
        "inference_seconds": inference_seconds,
        "rows": [
            {**row, "prediction": pred, "output": output}
            for row, pred, output in zip(rows, predictions, outputs)
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True))
    print(json.dumps({k: v for k, v in report.items() if k != "rows"}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
