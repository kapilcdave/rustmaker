"""Score a fresh --clip-ladder shadow tape without treating fills as independent samples.

Usage: python3 score_clip_ladder.py data/shadow_clip_ladder/shadow_XXXX.csv.gz
"""

import sys
from collections import deque

import numpy as np
import pandas as pd

from tox import results


ARMS = ["touch_c1", "p4_c1", "p4_c3", "p4_c10"]


def cluster_ratio_se(markets):
    """Market-clustered SE for sum(PnL) / sum(contracts)."""
    n = len(markets)
    if n < 2 or markets.ct.sum() <= 0:
        return np.nan
    mean = markets.pnl_c.sum() / markets.ct.sum()
    residual = markets.pnl_c - mean * markets.ct
    return np.sqrt(n / (n - 1) * np.square(residual).sum()) / markets.ct.sum()


def pair_share(group):
    bids, asks = deque(), deque()
    paired = 0.0
    for row in group.sort_values("vt").itertuples():
        mine, other = (bids, asks) if row.side == "bid" else (asks, bids)
        quantity = row.ct
        while quantity > 1e-12 and other:
            available = other[0]
            matched = min(quantity, available)
            paired += matched
            quantity -= matched
            if available - matched > 1e-12:
                other[0] = available - matched
            else:
                other.popleft()
        if quantity > 1e-12:
            mine.append(quantity)
    return 2 * paired / group.ct.sum()


def summarize(markets, fills, hours, scope, arm, half):
    m = markets[(markets.scope == scope) & (markets.arm == arm)]
    if half != "ALL":
        m = m[m.half == half]
    selected = set(m.ticker)
    f = fills[(fills.scope == scope) & (fills.arm == arm) & fills.ticker.isin(selected)]
    mean = m.pnl_c.sum() / m.ct.sum() if m.ct.sum() else np.nan
    se = cluster_ratio_se(m)
    return {
        "scope": scope,
        "arm": arm,
        "half": half,
        "markets": len(m),
        "contracts": m.ct.sum(),
        "pair_share": pair_share(f) if len(f) else np.nan,
        "c_per_ct": mean,
        "c_per_ct_se": se,
        "lo95_c_per_ct": mean - 1.96 * se,
        "total_$": m.pnl_c.sum() / 100,
        "$_per_hour": m.pnl_c.sum() / 100 / hours if half == "ALL" else np.nan,
    }


def main(path):
    raw = pd.read_csv(path, dtype={"a": str, "b": str}, low_memory=False)
    fills = raw[raw.kind == "F"].rename(
        columns={"a": "arm", "b": "side", "c": "price", "d": "ct", "venue_ms": "vt"}
    ).copy()
    fills.price = pd.to_numeric(fills.price) / 100
    fills.ct = pd.to_numeric(fills.ct)
    missing = set(ARMS) - set(fills.arm.unique())
    if missing:
        raise SystemExit(f"not a clip-ladder tape; missing arms: {sorted(missing)}")

    outcomes = results(sorted(fills.ticker.unique()))
    fills["y"] = fills.ticker.map({
        ticker: 100.0 if outcome == "yes" else 0.0 if outcome == "no" else np.nan
        for ticker, outcome in outcomes.items()
    })
    fills = fills[fills.y.notna()].copy()
    fills["pnl_c"] = np.where(
        fills.side == "bid",
        fills.ct * (fills.y - fills.price),
        fills.ct * (fills.price - fills.y),
    )
    fills["scope"] = np.where(fills.ticker.str.startswith("KXBTC15M-"), "BTC", "POOLED")
    pooled = fills.copy()
    pooled["scope"] = "POOLED"
    fills = pd.concat([fills[fills.scope == "BTC"], pooled], ignore_index=True)

    markets = fills.groupby(["scope", "arm", "ticker"], as_index=False).agg(
        pnl_c=("pnl_c", "sum"), ct=("ct", "sum"), first_vt=("vt", "min")
    )
    for scope in ["BTC", "POOLED"]:
        ticker_times = markets[markets.scope == scope].groupby("ticker").first_vt.min()
        midpoint = ticker_times.median()
        markets.loc[markets.scope == scope, "half"] = np.where(
            markets.loc[markets.scope == scope, "first_vt"] <= midpoint, "H1", "H2"
        )

    recv = pd.to_numeric(raw.recv_us, errors="coerce").dropna()
    hours = max((recv.max() - recv.min()) / 3.6e9, 1e-9)
    rows = [
        summarize(markets, fills, hours, scope, arm, half)
        for scope in ["BTC", "POOLED"]
        for arm in ARMS
        for half in ["H1", "H2", "ALL"]
    ]
    report = pd.DataFrame(rows)
    print(report.round(4).to_string(index=False))

    pooled_all = report[(report.scope == "POOLED") & (report.half == "ALL")].set_index("arm")
    p4_c1_dollars = pooled_all.loc["p4_c1", "total_$"]
    valid = pooled_all.loc["touch_c1", "total_$"] < 0
    print(f"\nvalidity touch_c1 negative: {valid}")
    for arm in ["p4_c3", "p4_c10"]:
        arm_rows = report[(report.scope == "POOLED") & (report.arm == arm)].set_index("half")
        halves_positive = arm_rows.loc["H1", "c_per_ct"] > 0 and arm_rows.loc["H2", "c_per_ct"] > 0
        dollars_increase = arm_rows.loc["ALL", "total_$"] > p4_c1_dollars
        print(f"{arm}: halves_positive={halves_positive} dollars_increase={dollars_increase} pass={valid and halves_positive and dollars_increase}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    main(sys.argv[1])
