"""Collect direct books for the Coin Race/directional implication portfolios."""
from __future__ import annotations

import argparse
import gzip
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

try:
    from idea_lab.collect_altspot_tilt_live import active_market
    from idea_lab.collect_crypto_leader_complete_set import ASSETS, active_event
    from idea_lab.collect_public_hourly_dominance_orderbook import book
except ModuleNotFoundError:
    from collect_altspot_tilt_live import active_market
    from collect_crypto_leader_complete_set import ASSETS, active_event
    from collect_public_hourly_dominance_orderbook import book


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration", type=int, default=10_400)
    parser.add_argument("--interval", type=float, default=2.0)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("idea_lab/crypto_leader_directional_live_20261006.jsonl.gz"),
    )
    args = parser.parse_args()
    deadline = time.monotonic() + args.duration
    next_discovery = 0.0
    current = None
    with gzip.open(args.output, "at", encoding="utf-8", compresslevel=1) as handle, \
            ThreadPoolExecutor(max_workers=5) as pool:
        while time.monotonic() < deadline:
            loop = time.monotonic()
            now = int(time.time())
            if loop >= next_discovery:
                leader = active_event(now)
                directions = {asset: active_market(asset, now) for asset in ASSETS}
                if (
                    leader
                    and all(directions.values())
                    and all(
                        row["open_ts"] == leader["open_ts"]
                        and row["close_ts"] == leader["close_ts"]
                        for row in directions.values()
                    )
                ):
                    current = {"leader": leader, "directions": directions}
                else:
                    current = None
                next_discovery = loop + 10
            if current is not None:
                tickers = {
                    **{
                        f"leader:{asset}": current["leader"]["tickers"][asset]
                        for asset in ASSETS
                    },
                    **{
                        f"direction:{asset}": current["directions"][asset]["ticker"]
                        for asset in ASSETS
                    },
                }
                futures = {name: pool.submit(book, ticker) for name, ticker in tickers.items()}
                responses = {}
                complete = True
                for name, future in futures.items():
                    depth, start_ns, finish_ns, error = future.result()
                    responses[name] = {
                        "book": depth,
                        "request_start_ns": start_ns,
                        "request_finish_ns": finish_ns,
                        "error": error,
                    }
                    if (
                        not depth
                        or depth.get("yes_bid") is None
                        or depth.get("no_bid") is None
                    ):
                        complete = False
                handle.write(
                    json.dumps(
                        {
                            "kind": "snapshot",
                            "wall_ns": time.time_ns(),
                            "meta": current,
                            "responses": responses,
                            "complete": complete,
                        },
                        separators=(",", ":"),
                    )
                    + "\n"
                )
                handle.flush()
            delay = args.interval - (time.monotonic() - loop)
            if delay > 0:
                time.sleep(delay)


if __name__ == "__main__":
    main()
