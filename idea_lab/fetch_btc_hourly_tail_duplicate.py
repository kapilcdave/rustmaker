"""Fetch the preregistered KXBTC/KXBTCD identical upper-tail contracts."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, "/Users/kapil/proj/kalshi-scalp")
import fetch_15m_candles as F  # type: ignore

from fetch_btc_hourly_complete_set import range_markets
from fetch_btc_hourly_dominance import candles


def atomic_dump(obj: object, path: Path) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, sort_keys=True))
    os.replace(tmp, path)


def dec(value: object) -> Decimal:
    return Decimal(str(value).replace(",", ""))


def same_value(a: object, b: object) -> bool:
    try:
        return dec(a) == dec(b)
    except Exception:
        return False


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dominance-panel", default="idea_lab/btc_hourly_dominance_panel.json")
    ap.add_argument("--output", default="idea_lab/btc_hourly_tail_duplicate_panel.json")
    ap.add_argument(
        "--range-cache", default="idea_lab/btc_hourly_range_markets.json.gz"
    )
    ap.add_argument("--sleep", type=float, default=0.12)
    args = ap.parse_args()

    dominance = json.loads(Path(args.dominance_panel).read_text())
    closes = sorted(int(k) for k, row in dominance.items() if row.get("complete"))
    if not closes:
        raise SystemExit("empty dominance panel")
    output = Path(args.output)
    saved = json.loads(output.read_text()) if output.exists() else {}

    ranges = range_markets(closes, Path(args.range_cache))
    by_close: dict[int, list[dict]] = {}
    for market in ranges:
        by_close.setdefault(F.parse_iso(market["close_time"]), []).append(market)
    print(f"hours={len(closes)} range_markets={len(ranges)}", flush=True)

    for i, close in enumerate(closes, 1):
        key = str(close)
        if key in saved and saved[key].get("complete"):
            continue
        tails = [
            m
            for m in by_close.get(close, [])
            if m.get("floor_strike") not in (None, "None", "")
            and m.get("cap_strike") in (None, "None", "")
        ]
        if len(tails) != 1:
            saved[key] = {
                "complete": True,
                "close_ts": close,
                "pair": None,
                "upper_tail_count": len(tails),
            }
            atomic_dump(saved, output)
            continue

        range_market = tails[0]
        suffix = range_market["ticker"].split("-", 1)[1]
        directional_ticker = f"KXBTCD-{suffix}"
        try:
            directional_market = F.get(f"/markets/{directional_ticker}")["market"]
        except Exception as exc:
            saved[key] = {
                "complete": True,
                "close_ts": close,
                "pair": None,
                "error": f"{type(exc).__name__}: {exc}",
            }
            atomic_dump(saved, output)
            continue

        identity_ok = bool(
            range_market.get("rules_primary")
            and range_market.get("rules_primary") == directional_market.get("rules_primary")
            and range_market.get("close_time") == directional_market.get("close_time")
            and same_value(
                range_market.get("expiration_value"),
                directional_market.get("expiration_value"),
            )
            and same_value(
                range_market.get("floor_strike"),
                directional_market.get("floor_strike"),
            )
        )
        pair = {
            "identity_ok": identity_ok,
            "range": {
                "ticker": range_market["ticker"],
                "strike": range_market.get("floor_strike"),
                "close_time": range_market.get("close_time"),
                "expiration_value": range_market.get("expiration_value"),
                "result": range_market.get("result"),
                "rules_primary": range_market.get("rules_primary"),
                "bars": candles("KXBTC", range_market["ticker"], close - 960, close + 60),
            },
            "directional": {
                "ticker": directional_ticker,
                "strike": directional_market.get("floor_strike"),
                "close_time": directional_market.get("close_time"),
                "expiration_value": directional_market.get("expiration_value"),
                "result": directional_market.get("result"),
                "rules_primary": directional_market.get("rules_primary"),
                "bars": candles(
                    "KXBTCD", directional_ticker, close - 960, close + 60
                ),
            },
        }
        saved[key] = {"complete": True, "close_ts": close, "pair": pair}
        atomic_dump(saved, output)
        time.sleep(args.sleep)
        if i % 25 == 0:
            print(f"{i}/{len(closes)} saved={len(saved)}", flush=True)
    print(f"done hours={len(saved)}", flush=True)


if __name__ == "__main__":
    main()
