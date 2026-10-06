"""Audit the public Turbine strategy dump for selection and evidence quality."""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path


ROOT = Path("/tmp/kalshi_ext_20261006_5/botscollection_0828")
OUTPUT = Path("idea_lab/external_turbine_collection_audit.json")
SOURCE_COMMIT = "a6b31da83462698fc3a6a6ccbe253de6dad1f5b7"


def strategy_name(text: str) -> str | None:
    match = re.search(r'(?m)^strategy_name:\s*["\']?(.+?)["\']?\s*$', text)
    return None if match is None else match.group(1)


def main() -> None:
    rows = []
    hashes = Counter()
    names = Counter()
    validation = Counter()
    periods = Counter()
    for dsl_path in sorted((ROOT / "dsl").glob("*.yaml")):
        text = dsl_path.read_text(errors="replace")
        if not re.search(r'series_ticker:\s*["\']?KXBTC15M', text):
            continue
        stem = dsl_path.stem
        backtest_path = ROOT / "backtests" / f"{stem}.json"
        if not backtest_path.exists():
            continue
        payload = json.loads(backtest_path.read_text())
        summary = payload.get("summary") or {}
        meta = payload.get("meta") or {}
        digest = hashlib.sha256(text.encode()).hexdigest()
        hashes[digest] += 1
        name = strategy_name(text) or stem
        names[name] += 1
        validation[str(meta.get("validation_scheme"))] += 1
        periods[str(meta.get("period_days"))] += 1
        rows.append({
            "stem": stem,
            "name": name,
            "dsl_sha256": digest,
            "net_pnl": summary.get("net_pnl"),
            "roi_pct": summary.get("roi_pct"),
            "sharpe_ratio": summary.get("sharpe_ratio"),
            "total_trades": summary.get("total_trades"),
            "markets_simulated": summary.get("markets_simulated"),
            "positions_settled_by_outcome": meta.get("positions_settled_by_outcome"),
            "validation_scheme": meta.get("validation_scheme"),
            "start_time": summary.get("start_time"),
            "end_time": summary.get("end_time"),
        })
    ranked = sorted(
        rows,
        key=lambda row: float(row["net_pnl"] or float("-inf")),
        reverse=True,
    )
    positive = [row for row in rows if float(row["net_pnl"] or 0) > 0]
    report = {
        "source_commit": SOURCE_COMMIT,
        "source_generated_at": "2026-05-21T17:47:41Z",
        "all_strategy_files": len(list((ROOT / "dsl").glob("*.yaml"))),
        "kxbtc15m_backtests": len(rows),
        "positive_net_pnl": len(positive),
        "positive_share": len(positive) / len(rows) if rows else None,
        "roi_over_100pct": sum(float(row["roi_pct"] or 0) > 100 for row in rows),
        "exact_unique_dsls": len(hashes),
        "exact_duplicate_dsl_groups": sum(count > 1 for count in hashes.values()),
        "largest_exact_duplicate_group": max(hashes.values(), default=0),
        "unique_strategy_names": len(names),
        "largest_name_family": max(names.values(), default=0),
        "largest_name_families": names.most_common(15),
        "validation_schemes": dict(validation),
        "period_days": dict(periods),
        "top_20_by_reported_net_pnl": ranked[:20],
        "top_strategy_audit": {
            "stem": ranked[0]["stem"] if ranked else None,
            "reported_trades": ranked[0]["total_trades"] if ranked else None,
            "reported_markets": ranked[0]["markets_simulated"] if ranked else None,
            "reported_positions_settled_by_outcome": (
                ranked[0]["positions_settled_by_outcome"] if ranked else None
            ),
            "reported_validation_scheme": (
                ranked[0]["validation_scheme"] if ranked else None
            ),
        },
        "evidence_class": "massively_multiple-tested_single-window_in_sample",
        "promotion_eligible": False,
    }
    OUTPUT.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
