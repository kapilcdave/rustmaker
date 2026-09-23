"""Score a kalshi-mm15 shadow tape: settlement P&L per strategy, paired vs naked, markouts.

Per market and strategy, fills are paired FIFO bid-vs-ask: a matched pair locks in
(ask - bid) cents at $1 regardless of outcome. The unmatched residual rides to settlement.
All fills in a market share one outcome, so the market is the unit of evidence (clustered SE).

Usage: python3 shadow_pnl.py shadow_XXXX.csv.gz
"""
import sys
from collections import deque

import numpy as np
import pandas as pd

sys.path.insert(0, ".")
from tox import results  # noqa: E402

path = sys.argv[1]
df = pd.read_csv(path, dtype={"a": str, "b": str}, low_memory=False)
b = df[df.kind == "B"].copy()
for c in "abcd":
    b[c] = pd.to_numeric(b[c])
b["mid"] = (b.a + b.c) / 200.0
f = df[df.kind == "F"].rename(columns={"a": "strat", "b": "side", "c": "price", "d": "ct", "venue_ms": "vt"}).copy()
f["price"] = pd.to_numeric(f.price) / 100.0
f["ct"] = pd.to_numeric(f.ct)
res = results(sorted(f.ticker.unique()))
f["y"] = f.ticker.map({k: (1.0 if v == "yes" else 0.0 if v == "no" else np.nan) for k, v in res.items()})
f = f[f.y.notna()]  # unsettled markets (still open) are excluded
s = np.where(f.side == "ask", 1.0, -1.0)  # maker sold yes on ask
for h in [5, 60]:
    mids = []
    for tk, g in f.groupby("ticker"):
        bb = b[b.ticker == tk].sort_values("venue_ms")
        i = np.searchsorted(bb.venue_ms.to_numpy(), g.vt.to_numpy() + h * 1_000_000, side="right") - 1
        mids.append(pd.Series(bb.mid.to_numpy()[np.clip(i, 0, len(bb) - 1)], index=g.index))
    f[f"mk{h}s"] = s * (f.price - pd.concat(mids).reindex(f.index))

rows = []
for (strat, tk), g in f.sort_values("vt").groupby(["strat", "ticker"]):
    bids, asks = deque(), deque()
    paired_c = 0.0
    n_pairs = 0.0
    for _, r in g.iterrows():
        mine, other = (bids, asks) if r.side == "bid" else (asks, bids)
        q = r.ct
        while q > 0 and other:
            px, oq = other[0]
            m = min(q, oq)
            bid_px, ask_px = (r.price, px) if r.side == "bid" else (px, r.price)
            paired_c += m * (ask_px - bid_px)
            n_pairs += m
            q -= m
            if oq > m:
                other[0] = (px, oq - m)
            else:
                other.popleft()
        if q > 0:
            mine.append((r.price, q))
    y = g.y.iloc[0] * 100
    naked_c = sum(q * (y - px) for px, q in bids) + sum(q * (px - y) for px, q in asks)
    rows.append({
        "strat": strat, "ticker": tk, "series": tk.split("-")[0], "ct": g.ct.sum(),
        "pairs": n_pairs, "paired_c": paired_c, "naked_ct": sum(q for _, q in bids) + sum(q for _, q in asks),
        "naked_c": naked_c, "total_c": paired_c + naked_c,
        "mk5s_c": (g.mk5s * g.ct).sum(), "mk60s_c": (g.mk60s * g.ct).sum(),
    })
m = pd.DataFrame(rows)


def summary(x):
    n = len(x)
    tot = x.total_c.sum()
    per_mkt = x.total_c.to_numpy()
    se_mkt = per_mkt.std(ddof=1) / np.sqrt(n) if n > 1 else np.nan
    return pd.Series({
        "markets": n, "contracts": x.ct.sum(), "pair_share": 2 * x.pairs.sum() / x.ct.sum(),
        "paired_c_per_pair": x.paired_c.sum() / max(x.pairs.sum(), 1e-9),
        "naked_c_per_ct": x.naked_c.sum() / max(x.naked_ct.sum(), 1e-9),
        "total_$": tot / 100, "c_per_ct": tot / x.ct.sum(),
        "c_per_market": tot / n, "c_per_market_se": se_mkt,
        "mk5s_c_per_ct": x.mk5s_c.sum() / x.ct.sum(), "mk60s_c_per_ct": x.mk60s_c.sum() / x.ct.sum(),
    })


print(f"settled fills {len(f):,} over {f.ticker.nunique()} markets")

# Queue split (tapes from 2026-09-24 on): settlement P&L of each fill, s*(price - 100y), is
# additive, so a split by queue ahead at the fill sums back to the strategy total.
if "q_fill" in f.columns and f.q_fill.notna().any():
    f["settle_c"] = s * (f.price - 100 * f.y) * f.ct
    f["q_b"] = pd.cut(pd.to_numeric(f.q_fill), [-1, 0, 10, 50, 250, 1000, 1e12],
                      labels=["0", "1-10", "11-50", "51-250", "251-1k", ">1k"])
    g = f.groupby(["strat", "q_b"], observed=True)
    qt = g.agg(ct=("ct", "sum"), settle_c=("settle_c", "sum"), mk5s=("mk5s", "mean"), mkts=("ticker", "nunique"))
    qt["c_per_ct"] = qt.settle_c / qt.ct
    print("\n== by queue ahead at fill (contracts) ==")
    print(qt.round(3).to_string())
    # Prereg rule 1: small-queue (q_fill <= 50) minus deep-queue (> 250) c/ct, per market, on
    # markets that have both, so the SE is clustered by market.
    qf = pd.to_numeric(f.q_fill)
    for st, g in f.groupby("strat"):
        g = g.assign(grp=np.where(qf[g.index] <= 50, "small", np.where(qf[g.index] > 250, "deep", "")))
        per = g[g.grp != ""].groupby(["ticker", "grp"]).apply(lambda x: x.settle_c.sum() / x.ct.sum(), include_groups=False).unstack()
        per = per.dropna()
        if len(per) > 1:
            d = per["small"] - per["deep"]
            se = d.std(ddof=1) / np.sqrt(len(d))
            print(f"rule1 {st:15s} mkts {len(d):4d}  small-deep {d.mean():+7.3f} c/ct  se {se:6.3f}  t {d.mean() / se:+5.2f}")
print("\n== by strategy ==")
print(m.groupby("strat").apply(summary, include_groups=False).round(3).T.to_string())
print("\n== by strategy x series ==")
print(m.groupby(["strat", "series"]).apply(summary, include_groups=False)[["markets", "contracts", "total_$", "c_per_ct", "c_per_market_se", "mk60s_c_per_ct"]].round(3).to_string())

# Prereg rule 2: each arm vs gate_pr on the same markets (paired by market).
if "gate_pr" in m.strat.unique():
    base = m[m.strat == "gate_pr"].set_index("ticker").total_c
    print("\n== paired vs gate_pr (c/market) ==")
    for st in sorted(set(m.strat) - {"gate_pr"}):
        arm = m[m.strat == st].set_index("ticker").total_c
        idx = base.index.union(arm.index)
        d = arm.reindex(idx, fill_value=0) - base.reindex(idx, fill_value=0)
        se = d.std(ddof=1) / np.sqrt(len(d))
        print(f"{st:15s} mkts {len(d):4d}  diff {d.mean():+8.2f} se {se:6.2f}  t {d.mean() / se:+5.2f}")
