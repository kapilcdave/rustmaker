"""Frozen OOS replication of the external BTC/ETH minute-five regime stack."""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

try:
    from idea_lab.analyze_external_dead_contract import summarize
    from idea_lab.common import fee
except ModuleNotFoundError:
    from analyze_external_dead_contract import summarize
    from common import fee


MARKETS = Path(
    "/Users/kapil/proj/kalshi-scalp/data/oos_20261006/candles_BTC.json"
)
BTC = Path("/Users/kapil/proj/kalshi-scalp/data/oos_20261006/cb_BTC-USD.json")
ETH = Path("/Users/kapil/proj/kalshi-scalp/data/oos_20261006/cb_ETH-USD.json")
OUTPUT = Path("idea_lab/external_crossasset_regime_report.json")
SOURCE_COMMIT = "73708296532ae163e369fe1f494d47ef6a41f099"

STATE_ACTION = {
    "B-/E-/deficit": "no",
    "B-/E-/track": "no",
    "B-/E-/excess": "no",
    "B-/E+/deficit": "no",
    "B-/E+/excess": "no",
    "B+/E+/track": "yes",
    "B+/E+/deficit": "yes",
    "B+/E+/excess": "yes",
    "B+/E-/deficit": "yes",
}


def closes(path: Path) -> dict[int, float]:
    return {
        int(row[0]): float(row[4])
        for row in json.loads(path.read_text())["bars"]
    }


def log_return(series: dict[int, float], ts: int) -> float | None:
    now = series.get(ts)
    prior = series.get(ts - 60)
    if now is None or prior is None or now <= 0 or prior <= 0:
        return None
    return math.log(now / prior)


def feature(
    market: dict,
    btc: dict[int, float],
    eth: dict[int, float],
    spot_minute: int = 5,
) -> dict | None:
    t0 = int(market["open_ts"])
    decision_spot_ts = t0 + spot_minute * 60
    required = (
        btc.get(t0),
        btc.get(decision_spot_ts),
        eth.get(t0),
        eth.get(decision_spot_ts),
    )
    if any(value is None for value in required):
        return None
    pre_ts = [t0 - minute * 60 for minute in range(30, 0, -1)]
    pre_btc = [log_return(btc, ts) for ts in pre_ts]
    pre_eth = [log_return(eth, ts) for ts in pre_ts]
    paired = [
        (b, e) for b, e in zip(pre_btc, pre_eth) if b is not None and e is not None
    ]
    clean_pre = [value for value in pre_btc if value is not None]
    if len(clean_pre) < 10:
        return None
    pre_vol = float(np.std(clean_pre, ddof=1))
    intra = [
        value
        for ts in range(t0, decision_spot_ts + 1, 60)
        if (value := log_return(btc, ts)) is not None
    ]
    if len(intra) < 2 or pre_vol <= 0:
        return None
    intra_vol = float(np.std(intra, ddof=1))
    vol_ratio = intra_vol / pre_vol
    vol_state = (
        "deficit" if vol_ratio < 0.6 else "excess" if vol_ratio > 1.4 else "track"
    )
    btc_dir = "B+" if btc[decision_spot_ts] > btc[t0] else "B-"
    eth_dir = "E+" if eth[decision_spot_ts] > eth[t0] else "E-"
    corr = None
    if len(paired) >= 15:
        corr = float(np.corrcoef(
            [row[0] for row in paired], [row[1] for row in paired]
        )[0, 1])
    return {
        "state": f"{btc_dir}/{eth_dir}/{vol_state}",
        "btc_dir": btc_dir,
        "eth_dir": eth_dir,
        "vol_state": vol_state,
        "vol_ratio": vol_ratio,
        "corr_pre": corr,
        "spot_minute": spot_minute,
    }


def score_market(
    market: dict, btc: dict[int, float], eth: dict[int, float]
) -> tuple[dict, dict | None] | None:
    found = feature(market, btc, eth)
    if found is None:
        return None
    side = STATE_ACTION.get(found["state"])
    if side is None:
        return None
    t0 = int(market["open_ts"])
    bars = {
        int(row["ts"]): row
        for row in market.get("bars") or []
        if not row.get("post_close")
    }
    row = bars.get(t0 + 5 * 60)
    if row is None:
        return None
    entry = float(row["a"]) if side == "yes" else 1.0 - float(row["b"])
    payout = 1.0 if market["result"] == side else 0.0
    base = {
        "ticker": market["ticker"],
        "t0": t0,
        "day": t0 // 86400,
        **found,
        "side": side,
        "entry": entry,
        "result": market["result"],
        "pnl": payout - entry - fee(entry),
    }
    delayed = None
    next_row = bars.get(t0 + 6 * 60)
    if next_row is not None:
        next_entry = (
            float(next_row["a"])
            if side == "yes"
            else 1.0 - float(next_row["b"])
        )
        delayed = {
            **base,
            "execution_ts": t0 + 6 * 60,
            "entry": next_entry,
            "pnl": payout - next_entry - fee(next_entry),
        }
    return base, delayed


def score_causal_minute5(
    market: dict, btc: dict[int, float], eth: dict[int, float]
) -> dict | None:
    """Use only Coinbase candles completed by the Kalshi minute-five close."""
    found = feature(market, btc, eth, spot_minute=4)
    if found is None:
        return None
    side = STATE_ACTION.get(found["state"])
    if side is None:
        return None
    t0 = int(market["open_ts"])
    row = next(
        (
            row
            for row in market.get("bars") or []
            if not row.get("post_close") and int(row["ts"]) == t0 + 5 * 60
        ),
        None,
    )
    if row is None:
        return None
    entry = float(row["a"]) if side == "yes" else 1.0 - float(row["b"])
    payout = 1.0 if market["result"] == side else 0.0
    return {
        "ticker": market["ticker"],
        "t0": t0,
        "day": t0 // 86400,
        **found,
        "side": side,
        "entry": entry,
        "result": market["result"],
        "pnl": payout - entry - fee(entry),
    }


def main() -> None:
    markets = sorted(
        json.loads(MARKETS.read_text())["markets"],
        key=lambda row: int(row["open_ts"]),
    )
    btc = closes(BTC)
    eth = closes(ETH)
    same: list[dict] = []
    delayed: list[dict] = []
    causal_minute5: list[dict] = []
    for market in markets:
        if market.get("result") not in ("yes", "no"):
            continue
        scored = score_market(market, btc, eth)
        if scored is None:
            continue
        immediate, later = scored
        same.append(immediate)
        if later is not None:
            delayed.append(later)
        causal = score_causal_minute5(market, btc, eth)
        if causal is not None:
            causal_minute5.append(causal)
    correlations = [row["corr_pre"] for row in same if row["corr_pre"] is not None]
    report = {
        "preregistration": "PREREG_external_crossasset_regime_oos_20261006.md",
        "source_commit": SOURCE_COMMIT,
        "source_published_before_local_sample": True,
        "timestamp_audit": {
            "coinbase_timestamp_semantics": "bar_open",
            "kalshi_timestamp_semantics": "bar_close",
            "source_same_index_join_has_future_spot_minutes": 1,
            "same_bar_result_promotion_eligible": False,
            "causally_aligned_result": "next_minute_diagnostic",
        },
        "market_count": len(markets),
        "same_bar_governing": summarize(same),
        "next_minute_diagnostic": summarize(delayed),
        "causal_minute5_exploratory": summarize(causal_minute5),
        "state_diagnostics": {
            state: summarize([row for row in same if row["state"] == state])
            for state in STATE_ACTION
        },
        "correlation_diagnostic": {
            "observations": len(correlations),
            "mean": float(np.mean(correlations)) if correlations else None,
            "share_ge_0_4": (
                float(np.mean(np.asarray(correlations) >= 0.4))
                if correlations else None
            ),
        },
    }
    OUTPUT.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
