#!/usr/bin/env python3
"""Post-mortem on the 2026-10-07 armed run: -$5.76 over 544 real fills in 4 runs.

Reconciliation gate first: the per-fill settlement replay must land on the venue's -576c, or no
breakdown below it means anything (the-venues-own-ledger-beats-a-journal-reconstruction).

Then the three splits that can distinguish the candidate causes:
  - tick regime (wing <10c / mid 10-90c / wing >90c): was the band-off config the mistake?
  - opening vs reducing leg: is this adverse selection on entry, or a bad exit?
  - paired vs leftover: did the loss come from round trips or from naked contracts at settlement?
Per-fill settlement P&L is exactly additive, so each split is a decomposition, not an estimate.
"""
import gzip
import json
import math
from collections import Counter, defaultdict

import numpy as np

FILLS = "data/tonight/tonight_fills.txt.gz"
RESULTS = "data/leftover/results.json"
VENUE_C = -576.35          # 1628.13c -> 1051.78c


def region(p):
    return "wing <10c" if p < 10 else ("wing >90c" if p >= 90 else "mid 10-90c")


def cluster_se(vals, keys):
    g = defaultdict(float)
    for v, k in zip(vals, keys):
        g[k] += v
    m = len(g)
    if m < 2:
        return float("nan")
    return math.sqrt(np.array(list(g.values())).var(ddof=1) * m) / len(vals)


def show(rows, label, ind="  "):
    if not rows:
        print(f"{ind}{label:28} -")
        return
    p = np.array([r["pnl"] for r in rows])
    c = np.array([r["ct"] for r in rows])
    se = cluster_se(p, [r["round"] for r in rows]) * len(p) / c.sum()
    print(f"{ind}{label:28} fills={len(rows):4d} ct={c.sum():6.0f} tot={p.sum()/100:+7.2f}$ "
          f"c/ct={p.sum()/c.sum():+8.3f} se={se:6.3f}")


def main():
    res = json.load(open(RESULTS))
    raw = []
    for line in gzip.open(FILLS, "rt"):
        j, rest = line.split(" ", 1)
        v = json.loads(rest)["v"]
        if v.get("is_taker"):
            continue
        raw.append((v["ts_ms"], j, v))
    raw.sort()

    pos = defaultdict(float)
    rows = []
    unsettled = 0
    for ts, j, v in raw:
        tk = v["market_ticker"]
        r = res.get(tk)
        if not r or r.get("result") not in ("yes", "no"):
            unsettled += 1
            continue
        sgn = 1.0 if v["book_side"] == "bid" else -1.0
        ct = float(v["count_fp"])
        px = float(v["yes_price_dollars"]) * 100.0
        y = 100.0 if r["result"] == "yes" else 0.0
        key = (j, tk)
        new = pos[key] + sgn * ct
        opening = abs(new) > abs(pos[key])
        pos[key] = new
        rows.append({"run": j, "ticker": tk, "series": tk.split("-")[0],
                     "round": tk.split("-")[1], "pnl": sgn * ct * (y - px), "ct": ct,
                     "px": px, "region": region(px),
                     "leg": "opening" if opening else "reducing"})

    tot = sum(r["pnl"] for r in rows)
    print("=" * 104)
    print("GATE 0 - replay vs the venue")
    print("=" * 104)
    print(f"  seat-only replay {tot:+8.2f}c   venue cash delta {VENUE_C:+8.2f}c   "
          f"diff {tot - VENUE_C:+7.2f}c   {'OK' if abs(tot - VENUE_C) < 25 else 'MISMATCH'}")
    print(f"  ({len(rows)} settled fills, {unsettled} unsettled dropped)")

    print("\n" + "=" * 104)
    print("BY RUN")
    print("=" * 104)
    for j in sorted({r["run"] for r in rows}):
        show([r for r in rows if r["run"] == j], j[5:18])

    print("\n" + "=" * 104)
    print("BY TICK REGIME  -- was running with the band OFF the mistake?")
    print("=" * 104)
    for rg in ("wing <10c", "mid 10-90c", "wing >90c"):
        show([r for r in rows if r["region"] == rg], rg)
    show([r for r in rows if r["region"] != "mid 10-90c"], "both wings pooled")

    print("\n" + "=" * 104)
    print("BY LEG  -- adverse selection on entry, or a bad exit?")
    print("=" * 104)
    for leg in ("opening", "reducing"):
        show([r for r in rows if r["leg"] == leg], leg)
    for rg in ("wing <10c", "mid 10-90c", "wing >90c"):
        for leg in ("opening", "reducing"):
            show([r for r in rows if r["region"] == rg and r["leg"] == leg], f"{rg} / {leg}", "    ")

    print("\n" + "=" * 104)
    print("PAIRED vs LEFTOVER  -- round trips, or naked contracts held to settlement?")
    print("=" * 104)
    end = defaultdict(float)
    for r in rows:
        end[(r["run"], r["ticker"])] += 0
    flat = {k for k, v in pos.items() if abs(v) < 1e-9}
    show([r for r in rows if (r["run"], r["ticker"]) in flat], "flat at close (paired)")
    show([r for r in rows if (r["run"], r["ticker"]) not in flat], "left with a position")

    print("\n" + "=" * 104)
    print("BY SERIES")
    print("=" * 104)
    for s in sorted({r["series"] for r in rows},
                    key=lambda s: sum(r["pnl"] for r in rows if r["series"] == s)):
        show([r for r in rows if r["series"] == s], s)

    print("\n" + "=" * 104)
    print("WORST SINGLE MARKETS")
    print("=" * 104)
    per = defaultdict(float)
    nfill = Counter()
    for r in rows:
        per[(r["run"], r["ticker"])] += r["pnl"]
        nfill[(r["run"], r["ticker"])] += 1
    worst = sorted(per.items(), key=lambda kv: kv[1])[:10]
    for (j, tk), v in worst:
        print(f"  {v/100:+7.2f}$  {tk:34} fills={nfill[(j,tk)]:3d} endpos={pos[(j,tk)]:+5.1f}")
    print(f"  worst 10 markets = {sum(v for _, v in worst)/100:+.2f}$ of {tot/100:+.2f}$ "
          f"({100*sum(v for _, v in worst)/tot:.0f}%)")
    print(f"  markets: {len(per)} total, {sum(1 for v in per.values() if v < 0)} losing, "
          f"{sum(1 for v in per.values() if v > 0)} winning")


if __name__ == "__main__":
    main()
