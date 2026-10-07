#!/usr/bin/env python3
"""Run Amendment 2's frozen GLiNER2.5-Decide degeneracy gate."""
from __future__ import annotations

import argparse
import itertools
import json
import time
from collections import Counter
from pathlib import Path


LABELS = {
    "trade_yes": (
        "Buy the observed YES execution only when the forecast supports YES "
        "after price and fee"
    ),
    "trade_no": (
        "Buy the observed NO execution only when the forecast supports NO "
        "after price and fee"
    ),
    "skip_uncertain": "Abstain because outcome confidence or directional support is weak",
    "skip_expensive": "Abstain because price plus fee consumes the forecast edge",
}


def synthetic_texts() -> list[str]:
    texts = []
    for side, seconds, p_yes, cheap in itertools.product(
        ("YES", "NO"),
        (60, 30, 10, 2),
        (0.52, 0.65, 0.85, 0.98),
        (True, False),
    ):
        p_side = p_yes if side == "YES" else 1 - p_yes
        if cheap:
            entry = max(0.02, p_side - 0.15)
        else:
            entry = min(0.98, p_side + 0.12)
        fee = 0.02
        edge = p_side - entry - fee
        texts.append(
            f"Asset ETH. {seconds} seconds remain. "
            f"Observed executable side {side}. "
            f"B2 probability for YES {p_yes:.3f}; "
            f"probability for this side {p_side:.3f}. "
            f"Entry {entry:.3f}; fee {fee:.3f}; post-fee edge {edge:+.3f}. "
            f"Standardized target distance {(p_yes - 0.5) * 6:+.2f}; "
            f"trend shift {(p_yes - 0.5) * 2:+.3f}; "
            "source age 1200 ms; size 3."
        )
    return texts


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--model", default="fastino/GLiNER2.5-Decide")
    p.add_argument("--batch-size", type=int, default=8)
    p.add_argument("--output", type=Path, default=Path("data/cf_exact/gliner_synthetic.json"))
    args = p.parse_args()

    from gliner2 import AutoExtractor

    texts = synthetic_texts()
    started = time.time()
    model = AutoExtractor.from_pretrained(args.model)
    load_seconds = time.time() - started
    started = time.time()
    outputs = model.batch_classify_text(
        texts,
        {"action": {"labels": LABELS}},
        batch_size=args.batch_size,
        include_confidence=True,
    )
    inference_seconds = time.time() - started

    labels = []
    for output in outputs:
        value = output.get("action")
        labels.append(value.get("label") if isinstance(value, dict) else value)
    counts = Counter(labels)
    report = {
        "preregistration": "PREREG_cf_exact_endgame_20261006.md Amendment 2",
        "model": args.model,
        "synthetic_states": len(texts),
        "distinct_labels": len(counts),
        "passes_degeneracy_gate": len(counts) >= 3,
        "counts": dict(counts),
        "load_seconds": load_seconds,
        "inference_seconds": inference_seconds,
        "labels": labels,
        "outputs": outputs,
        "texts": texts,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True))
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
