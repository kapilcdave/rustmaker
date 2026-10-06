"""Post-publication OOS test of the Sentient Markov/Hurst trading stack."""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path

try:
    from idea_lab.analyze_external_dead_contract import summarize
    from idea_lab.common import fee
except ModuleNotFoundError:
    from analyze_external_dead_contract import summarize
    from common import fee


MARKETS = Path(
    "/Users/kapil/proj/kalshi-scalp/data/oos_20261006/candles_BTC.json"
)
SPOT = Path(
    "/Users/kapil/proj/kalshi-scalp/data/oos_20261006/cb_BTC-USD.json"
)
OUTPUT = Path("idea_lab/external_sentient_markov_report.json")

NUM_STATES = 9
STATE_BOUNDS = [-3.35, -2.24, -1.12, -0.45, 0.45, 1.12, 2.24, 3.35]
STATE_RETURNS = [-2.0, -1.25, -0.75, -0.35, 0.0, 0.35, 0.75, 1.25, 2.0]
STATE_VOL = [1.0, 0.35, 0.25, 0.15, 0.10, 0.15, 0.25, 0.35, 1.0]
BLOCKED_HOURS = {8, 11, 16, 18, 21}


def aggregate(raw: list[list], seconds: int) -> list[list]:
    groups: dict[int, list[list]] = {}
    for row in raw:
        ts = int(row[0])
        groups.setdefault(ts // seconds * seconds, []).append(row)
    expected = seconds // 60
    out = []
    for bucket, rows in sorted(groups.items()):
        rows.sort(key=lambda row: int(row[0]))
        timestamps = [int(row[0]) for row in rows]
        if len(rows) != expected:
            continue
        if timestamps != list(range(bucket, bucket + seconds, 60)):
            continue
        out.append(
            [
                bucket,
                min(float(row[1]) for row in rows),
                max(float(row[2]) for row in rows),
                float(rows[0][3]),
                float(rows[-1][4]),
                sum(float(row[5]) for row in rows),
            ]
        )
    return out


def norm_cdf(z: float) -> float:
    sign = 1 if z >= 0 else -1
    x = abs(z)
    t = 1.0 / (1.0 + 0.2316419 * x)
    poly = t * (
        0.319381530
        + t
        * (
            -0.356563782
            + t * (1.781477937 + t * (-1.821255978 + t * 1.330274429))
        )
    )
    pdf = math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)
    return 0.5 + sign * (0.5 - pdf * poly)


def compute_hurst(candles_newest: list[list]) -> float | None:
    closes = [c[4] for c in reversed(candles_newest)]
    if len(closes) < 12:
        return None
    lr = [
        math.log(closes[i] / closes[i - 1])
        for i in range(1, len(closes))
        if closes[i - 1] > 0
    ]
    if len(lr) < 6:
        return None
    var1 = sum(r * r for r in lr) / len(lr)
    pairs = [lr[i] + lr[i + 1] for i in range(0, len(lr) - 1, 2)]
    var2 = sum(r * r for r in pairs) / max(len(pairs), 1) if pairs else 0.0
    if var1 <= 0:
        return None
    return max(
        0.0,
        min(
            1.0,
            0.5
            + math.log(max(var2 / (2 * var1), 1e-12))
            / (2 * math.log(2)),
        ),
    )


def gk_vol(candles_newest: list[list]) -> float | None:
    k = 2 * math.log(2) - 1
    terms = []
    for c in candles_newest:
        lo, hi, op, cl = c[1], c[2], c[3], c[4]
        if op <= 0 or lo <= 0 or hi <= 0:
            continue
        terms.append(
            0.5 * math.log(hi / lo) ** 2 - k * math.log(cl / op) ** 2
        )
    if len(terms) < 2:
        return None
    return math.sqrt(max(0.0, sum(terms) / len(terms)))


def price_change_to_state(pct: float) -> int:
    for index, bound in enumerate(STATE_BOUNDS):
        if pct < bound:
            return index
    return NUM_STATES - 1


def build_transition_matrix(history: list[int]) -> list[list[float]]:
    counts = [[0.0] * NUM_STATES for _ in range(NUM_STATES)]
    for source, target in zip(history, history[1:]):
        counts[source][target] += 1.0
    matrix = []
    for row in counts:
        total = sum(row)
        matrix.append(
            [value / total for value in row]
            if total > 0
            else [1.0 / NUM_STATES] * NUM_STATES
        )
    return matrix


def predict(
    matrix: list[list[float]],
    current_state: int,
    minutes_left: float,
    dist_pct: float,
) -> dict:
    steps = max(1, round(minutes_left / 5))
    required_drift = -dist_pct
    distribution = [0.0] * NUM_STATES
    distribution[current_state] = 1.0
    expected_drift = 0.0
    variance = 0.0
    for _ in range(steps):
        mean = sum(
            distribution[i] * STATE_RETURNS[i] for i in range(NUM_STATES)
        )
        second = sum(
            distribution[i]
            * (STATE_VOL[i] ** 2 + STATE_RETURNS[i] ** 2)
            for i in range(NUM_STATES)
        )
        expected_drift += mean
        variance += max(0.0, second - mean**2)
        nxt = [0.0] * NUM_STATES
        for i in range(NUM_STATES):
            for j in range(NUM_STATES):
                nxt[j] += distribution[i] * matrix[i][j]
        distribution = nxt
    sigma = math.sqrt(max(variance, 0.01))
    p_yes = norm_cdf((expected_drift - required_drift) / sigma)
    return {
        "p_yes": p_yes,
        "persist": matrix[current_state][current_state],
    }


def build_history(candles_5m: list[list], decision_ts: int) -> list[int]:
    relevant = [c for c in candles_5m if c[0] + 300 <= decision_ts]
    states = [
        price_change_to_state(
            (relevant[i][4] - relevant[i - 1][4])
            / relevant[i - 1][4]
            * 100
        )
        for i in range(1, len(relevant))
        if relevant[i - 1][4] > 0
    ]
    return states[-480:]


def find_signal(
    market: dict, candles_5m: list[list], candles_15m: list[list]
) -> dict | None:
    t0 = int(market["open_ts"])
    strike = float(market["floor_strike"])
    market_rows = {
        (int(row["ts"]) - t0) // 60: row
        for row in market.get("bars") or []
        if not row.get("post_close")
    }
    c5 = {int(row[0]): row for row in candles_5m}
    for minute in range(3, 13):
        row = market_rows.get(minute)
        if row is None:
            continue
        decision_ts = t0 + minute * 60
        minutes_left = 15 - minute
        yes_ask_c = float(row["a"]) * 100
        no_ask_c = (1.0 - float(row["b"])) * 100
        golden = 65 <= yes_ask_c <= 73
        if not ((3 <= minutes_left <= 12) if golden else (6 <= minutes_left <= 9)):
            continue
        if datetime.fromtimestamp(decision_ts, timezone.utc).hour in BLOCKED_HOURS:
            continue
        context = [bar for bar in candles_15m if bar[0] + 900 <= decision_ts]
        if len(context) < 12:
            continue
        newest = list(reversed(context[-32:]))
        volatility = gk_vol(newest[:16])
        if volatility is None or volatility <= 0 or volatility > 0.0025:
            continue
        hurst = compute_hurst(newest[:24])
        if hurst is not None and hurst < 0.50:
            continue
        c5_ts = decision_ts // 300 * 300 - 300
        current_bar = c5.get(c5_ts) or c5.get(c5_ts - 300)
        if current_bar is None:
            continue
        spot = float(current_bar[4])
        dist_pct = (spot - strike) / strike * 100
        if abs(dist_pct) < 0.02:
            continue
        prior_15m = c5.get(c5_ts - 900)
        if prior_15m is not None and prior_15m[4] > 0:
            velocity = (spot - prior_15m[4]) / 15
            crossing_velocity = abs(spot - strike) / minutes_left
            toward = (spot >= strike and velocity < 0) or (
                spot < strike and velocity > 0
            )
            if (
                toward
                and crossing_velocity > 0
                and abs(velocity) > 0.40 * crossing_velocity
            ):
                continue
        history = build_history(candles_5m, decision_ts)
        if len(history) < 20:
            continue
        prior_bar = c5.get(c5_ts - 300)
        current_state = (
            price_change_to_state(
                (current_bar[4] - prior_bar[4]) / prior_bar[4] * 100
            )
            if prior_bar is not None and prior_bar[4] > 0
            else 4
        )
        forecast = predict(
            build_transition_matrix(history + [current_state]),
            current_state,
            minutes_left,
            dist_pct,
        )
        if abs(forecast["p_yes"] - 0.5) < 0.11:
            continue
        if forecast["persist"] < 0.82:
            continue
        side = "yes" if forecast["p_yes"] > 0.5 else "no"
        entry_c = yes_ask_c if side == "yes" else no_ask_c
        cap = 72 if side == "yes" else 65
        if round(entry_c) > cap:
            continue
        return {
            "side": side,
            "row": row,
            "minute": minute,
            "entry": entry_c / 100,
            "p_yes": forecast["p_yes"],
            "persist": forecast["persist"],
            "spot": spot,
            "dist_pct": dist_pct,
            "gk_vol": volatility,
            "hurst": hurst,
        }
    return None


def main() -> None:
    raw = json.loads(SPOT.read_text())["bars"]
    candles_5m = aggregate(raw, 300)
    candles_15m = aggregate(raw, 900)
    markets = sorted(
        json.loads(MARKETS.read_text())["markets"],
        key=lambda row: int(row["open_ts"]),
    )
    same, delayed = [], []
    for market in markets:
        if market.get("result") not in ("yes", "no"):
            continue
        signal = find_signal(market, candles_5m, candles_15m)
        if signal is None:
            continue
        payout = 1.0 if market["result"] == signal["side"] else 0.0
        t0 = int(market["open_ts"])
        base = {
            key: value for key, value in signal.items() if key != "row"
        }
        base.update(
            {
                "ticker": market["ticker"],
                "t0": t0,
                "day": t0 // 86400,
                "result": market["result"],
                "pnl": payout - signal["entry"] - fee(signal["entry"]),
            }
        )
        same.append(base)
        next_row = next(
            (
                row
                for row in market.get("bars") or []
                if not row.get("post_close")
                and int(row["ts"]) == int(signal["row"]["ts"]) + 60
            ),
            None,
        )
        if next_row is not None:
            next_entry = (
                float(next_row["a"])
                if signal["side"] == "yes"
                else 1.0 - float(next_row["b"])
            )
            delayed.append(
                {
                    **base,
                    "entry": next_entry,
                    "pnl": payout - next_entry - fee(next_entry),
                }
            )
    report = {
        "preregistration": "PREREG_external_sentient_markov_oos_20261006.md",
        "source_commit": "bfbf470f49cc7585f59fde02f4501da0ae480e37",
        "markets": len(markets),
        "spot_5m_bars": len(candles_5m),
        "spot_15m_bars": len(candles_15m),
        "same_bar_governing": summarize(same),
        "next_minute_diagnostic": summarize(delayed),
    }
    OUTPUT.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
