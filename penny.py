"""Penny-jump scorer on a probe tape: quote ONE TICK INSIDE the touch whenever there is room.

Our improved quote creates a new, empty price level, so there is no queue to model: the next taker
on that side, arriving after our quote is live, hits us first. The quote is posted at the touch
seen at t_decision and live LAG later; it is withdrawn (cancel LAG) when the touch it was improving
moves. Fill price is our price; P&L is marked to mid at +5 s / +60 s and to settlement.

Tick is venue-tapered: 0.1 c below 10 c and above 90 c (sub-cent ladder), else 1 c.
Usage: python3 penny.py tape.csv.gz
"""
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, ".")
from tox import cluster_se, close_utc, load, results  # noqa: E402

LAG_US = 11_000  # our measured decision → book time (feed 6.2 + create 4.9)


# Series on a flat 1c grid at every price (venue `price_ranges`, checked 2026-09-24).
FLAT_1C = ("KXCOPPER15M", "KXNATGAS15M")


def tick_fp(price_fp, flat=False):
    # 1e-4 dollar units: 10 = 0.1 c, 100 = 1 c
    if flat:
        return np.full(np.shape(price_fp), 100)
    return np.where((price_fp < 1000) | (price_fp > 9000), 10, 100)


def score_market(bb, tt):
    """Yield one row per simulated fill for one market."""
    bb = bb.sort_values("vt")
    bb = bb[(bb.bid > 0) & (bb.ask > 0)]
    if len(bb) < 20 or len(tt) == 0:
        return []
    bvt, bid, ask = bb.vt.to_numpy(), bb.bid.to_numpy(), bb.ask.to_numpy()
    mid = (bid + ask) / 200.0
    tt = tt.sort_values("vt")
    out = []
    # For each taker print, find the touch as it stood LAG before (our quote would be live by now
    # if the touch had not changed since), and decide whether our improved quote was resting.
    for side, g in tt.groupby("side"):
        v = g.vt.to_numpy()
        i = np.searchsorted(bvt, v - LAG_US, side="right") - 1  # touch our quote was built on
        j = np.searchsorted(bvt, v, side="right") - 1           # touch just before the print
        ok = (i >= 0) & (i == j)  # touch unchanged from our decision to the print: quote live
        i = np.clip(i, 0, len(bvt) - 1)
        b, a = bid[i], ask[i]
        # Toxicity gate inputs as of our decision (t - LAG): 1 s mid momentum and touch sizes.
        i1 = np.clip(np.searchsorted(bvt, v - LAG_US - 1_000_000, side="right") - 1, 0, len(bvt) - 1)
        mom = mid[i] - mid[i1]
        bs, as_ = bb.bidsz.to_numpy()[i], bb.asksz.to_numpy()[i]
        tot = np.maximum(bs + as_, 1)
        tk = tick_fp(np.where(side == "yes", a, b))
        room = (a - b) >= 2 * tk
        if side == "yes":   # yes-taker lifts the ask: our improved ASK at a - tick fills
            px = a - tk
            s = 1.0         # we sold yes
            toxic = (mom > 0.25) | (bs / tot > 0.9213)
        else:               # no-taker hits the bid: our improved BID at b + tick fills
            px = b + tk
            s = -1.0        # we bought yes
            toxic = (-mom > 0.25) | (as_ / tot > 0.9213)
        m = ok & room
        if not m.any():
            continue
        vv = v[m]
        k5 = np.clip(np.searchsorted(bvt, vv + 5_000_000, side="right") - 1, 0, len(bvt) - 1)
        k60 = np.clip(np.searchsorted(bvt, vv + 60_000_000, side="right") - 1, 0, len(bvt) - 1)
        P = px[m] / 100.0  # cents
        out.append(pd.DataFrame({
            "vt": vv, "s": s, "P": P, "spread_ticks": ((a - b) / tk)[m],
            "mid": mid[i[m]],
            "mk5s": s * (P - mid[k5]), "mk60s": s * (P - mid[k60]),
            "count": 1.0, "toxic": toxic[m],  # one contract per fill: we are the whole new level
        }))
    return out


def main():
    b, t = load(sys.argv[1])
    rows = []
    for tk, tt in t.groupby("ticker"):
        for d in score_market(b[b.ticker == tk], tt):
            d["ticker"] = tk
            d["series"] = tk.split("-")[0]
            rows.append(d)
    d = pd.concat(rows, ignore_index=True)
    d = d[(d.vt < pd.Series([close_utc(x) for x in d.ticker]).to_numpy() - 120_000_000)]
    res = results(sorted(d.ticker.unique()))
    y = d.ticker.map({k: 1.0 if v == "yes" else 0.0 if v == "no" else np.nan for k, v in res.items()})
    d["settle"] = d.s * (d.P - 100 * y)
    d = d[d.settle.notna()]
    d["half"] = np.where(d.vt < d.vt.median(), "H1", "H2")
    d["band"] = pd.cut(d.mid, [0, 10, 15, 85, 90, 100], labels=["0-10 wing", "10-15", "15-85 mid", "85-90", "90-100 wing"])
    d["room"] = pd.cut(d.spread_ticks, [1.5, 2.5, 4.5, 1e9], labels=["2", "3-4", "5+"])
    print(f"simulated 1-ct improved fills: {len(d):,} over {d.ticker.nunique()} markets")

    def tab(by, label):
        g = d.groupby(by, observed=True)
        o = pd.DataFrame({"fills": g.size(), "mkts": g.ticker.nunique(),
                          "mk5s": g.mk5s.mean(), "mk60s": g.mk60s.mean(), "settle": g.settle.mean()})
        o["settle_se"] = g.apply(lambda x: cluster_se(x, "settle"), include_groups=False)
        o["mk60s_se"] = g.apply(lambda x: cluster_se(x, "mk60s"), include_groups=False)
        print(f"\n== {label} (c per contract, maker view) ==")
        print(o.round(3).to_string())

    d["gate"] = np.where(d.toxic, "pulled", "kept")
    tab(["gate", "half"], "gate split (kept = what a gated penny quoter would fill)")
    k = d[~d.toxic]
    per_mkt = k.groupby(["room", "ticker"], observed=True).settle.sum().groupby(level=0, observed=True)
    print("\n== gated, SETTLEMENT c per MARKET by room (the decision unit) ==")
    for room, x in per_mkt:
        x = x.to_numpy()
        print(f"  room {room}: {x.mean():+.1f} ± {x.std(ddof=1)/np.sqrt(len(x)):.1f} c/market over {len(x)} markets")
    for h in ["H1", "H2"]:
        x = k[(k.half == h) & (k.room == "5+")].groupby("ticker").settle.sum().to_numpy()
        if len(x) > 1:
            print(f"  room 5+ {h}: {x.mean():+.1f} ± {x.std(ddof=1)/np.sqrt(len(x)):.1f} c/market over {len(x)} markets")
    d = k
    tab(["room", "half"], "GATED by spread in ticks")
    tab(["band", "half"], "GATED by price band")
    tab(["half"], "all")
    tab(["band", "half"], "by price band")
    tab(["room", "half"], "by spread in ticks before improving")
    tab(["series"], "by series")


if __name__ == "__main__":
    main()
