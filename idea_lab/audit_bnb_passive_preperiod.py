"""Post-selection pre-period robustness audit for the BNB passive fill floor."""
from __future__ import annotations

import argparse
import json
import math
import random
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

from audit_altspot_passive_fillfloor import Http, fetch_one, score
from common import load_cb, load_markets, norm_cdf, vol_min

START = 1_783_555_200  # 2026-07-09T00:00:00Z
END = 1_789_357_500  # 2026-09-14T03:45:00Z exclusive
SEED = 20261006
PER_DAY = 12
BASIS_BPS = 2.44
KS = (2, 3, 5, 8)


def sampled_signals() -> list[dict]:
    cb = load_cb("BNB")
    candidates = []
    for market in load_markets("BNB"):
        t0 = market["t0"]
        if not START <= t0 < END:
            continue
        sigma = vol_min(cb, t0)
        if sigma is None or sigma < 2e-5:
            continue
        adjusted = market["K"] * math.exp(-BASIS_BPS * 1e-4)
        for k in KS:
            bar = market["bars"].get(t0 + 60 * k)
            spot = cb.get(t0 + 60 * (k - 1))
            if (
                not bar
                or not spot
                or bar.get("a") is None
                or bar.get("b") is None
            ):
                continue
            fair = float(
                norm_cdf(
                    math.log(spot[1] / adjusted)
                    / (sigma * math.sqrt(max(15 - k, 0.5)))
                )
            )
            ask, bid = float(bar["a"]), float(bar["b"])
            side = None
            limit = None
            if fair - ask > 0.10 and 0 < bid < 1:
                side, limit = "yes", bid
            elif bid - fair > 0.10 and 0 < ask < 1:
                side, limit = "no", 1.0 - ask
            if side:
                candidates.append(
                    {
                        "asset": "BNB",
                        "ticker": market["tk"],
                        "t0": t0,
                        "close_ts": t0 + 900,
                        "decision_ts": t0 + 60 * k,
                        "day": t0 // 86400,
                        "k": k,
                        "side": side,
                        "limit": limit,
                        "yes_boundary": bid if side == "yes" else ask,
                        "result": "yes" if market["y"] == 1 else "no",
                    }
                )
                break
    by_day: dict[int, list[dict]] = {}
    for row in candidates:
        by_day.setdefault(row["day"], []).append(row)
    rng = random.Random(SEED)
    chosen = []
    for day in sorted(by_day):
        rows = sorted(by_day[day], key=lambda row: row["ticker"])
        chosen.extend(rows if len(rows) <= PER_DAY else rng.sample(rows, PER_DAY))
    return sorted(chosen, key=lambda row: (row["t0"], row["ticker"]))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--cache",
        type=Path,
        default=Path("idea_lab/bnb_passive_preperiod_tapes.jsonl"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("idea_lab/bnb_passive_preperiod_report.json"),
    )
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--qps", type=float, default=3.0)
    args = parser.parse_args()
    signals = sampled_signals()
    cached = {}
    if args.cache.exists():
        for line in args.cache.read_text().splitlines():
            row = json.loads(line)
            cached[row["ticker"]] = row
    todo = [row for row in signals if row["ticker"] not in cached]
    http = Http(args.qps)
    if todo:
        with args.cache.open("a") as handle, ThreadPoolExecutor(args.workers) as pool:
            for index, row in enumerate(pool.map(lambda item: fetch_one(http, item), todo), 1):
                cached[row["ticker"]] = row
                handle.write(json.dumps(row, separators=(",", ":")) + "\n")
                if index % 100 == 0:
                    handle.flush()
                    print(f"fetched {index}/{len(todo)}", flush=True)
    rows = [cached[row["ticker"]] for row in signals if row["ticker"] in cached]
    report = {
        "preregistration": "PREREG_bnb_passive_preperiod_replication_20261006.md",
        "seed": SEED,
        "per_day": PER_DAY,
        "signals": len(signals),
        "fetched": len(rows),
        "truncated": sum(bool(row["truncated"]) for row in rows),
        "http_errors": http.errors,
        "scores": [
            score(rows, ("BNB",), 2, stress, single_asset=True, min_fills=75)
            for stress in (0.0, 0.01)
        ],
    }
    args.output.write_text(json.dumps(report, indent=2))
    compact = json.loads(json.dumps(report))
    for item in compact["scores"]:
        item.pop("filled_rows", None)
        item.pop("submission_rows", None)
    print(json.dumps(compact, indent=2))


if __name__ == "__main__":
    main()
