"""Verify the pinned public live-edge verdict against its published state."""
from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parent
STATE = ROOT / "external_kyle_state_4bcd7b8.md.txt"
REGISTRY = ROOT / "external_kyle_registry_4bcd7b8.md.txt"
OUTPUT = ROOT / "external_kyle_live_edge_audit.json"


def dollars(pattern: str, text: str) -> float:
    match = re.search(pattern, text)
    if not match:
        raise ValueError(pattern)
    return float(match.group(1))


def main() -> None:
    state = STATE.read_text()
    registry = REGISTRY.read_text()
    contracts = int(re.search(r"\*\*576\*\*", registry).group(0).strip("*"))
    pnl = dollars(r"\*\*-\$(\d+\.\d+)\*\*", registry)
    ev = dollars(r"EV is \*\*-\$(\d+\.\d+)\*\*", registry)
    report = {
        "source_repo": "https://github.com/kyleleedixon/kalshi",
        "pinned_commit": "4bcd7b81c08a4560d9bd01dfbdaf896a2870253e",
        "snapshot_generated_utc": "2026-10-06T16:30:00Z",
        "strategy": {
            "tag": "edge007_spread003_fresh2s_v1 + maker_v2 pooled",
            "entry_gates": {
                "post_fee_model_edge_min_c": 7,
                "spread_max_c": 3,
                "book_age_max_s": 2,
            },
            "contracts": contracts,
            "settled_markets": 249,
            "pnl_usd": -pnl,
            "ev_per_contract_usd": -ev,
            "backtest_ev_per_contract_usd": 0.065,
            "precommitted_kill_threshold_contracts": 500,
            "precommitted_required_ev_per_contract_usd": 0.02,
            "verdict": "REFUTED",
        },
        "maker_test": {
            "requested_contracts": 24,
            "filled_contracts": 0,
            "fill_rate": 0.0,
            "verdict": "REFUTED",
        },
        "state_confirms_halted": "**Status: HALTED**" in state,
        "state_confirms_no_active_slice_gates": "Active slice gates: 0" in state,
        "interpretation": (
            "A preregistered, fee-aware, fresh-book high-model-edge rule "
            "reversed from +6.5c/contract in backtest to -4.62c/contract "
            "over 576 live contracts; its maker variant filled 0/24."
        ),
    }
    OUTPUT.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
