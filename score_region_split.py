#!/usr/bin/env python3
"""Is the penny seat a WING rule or a MID-BAND rule? Split every real fill by the tick regime
its own price sits in, and by whether it opened or reduced a position.

Why this split and not any other: `KX*15M` is `tapered_deci_cent`, so `--penny-room N` is two
different rules at once -- N x 0.1c of required spread below 10c and above 90c, N x 1c between.
Measured on 10.7M book states, room 4 fires on 34-39% of wing states and 5-6% of mid-band ones.
So "penny jumping" has never been scored as the two rules it actually is.

Per-fill settlement P&L is EXACTLY additive, no exit model and no pairing assumption:
  cash + pos*y = sum_f (-s*px*ct) + (sum_f s*ct)*y = sum_f s*ct*(y - px)
so a region split of the fills is a decomposition of the whole seat's P&L, not an approximation.

Usage: python3 score_region_split.py
"""
import importlib.util
import json
import math
from collections import defaultdict

import numpy as np

s = importlib.util.module_from_spec(
    importlib.util.spec_from_file_location("s", "score_open_band.py"))
importlib.util.spec_from_file_location("s", "score_open_band.py").loader.exec_module(s)

BANDED = ("band_c1", "band_c2", "band_pr3")


def region(px_c):
    """Tick regime of the fill's own YES price. The taper boundaries are 10c and 90c."""
    if px_c < 10.0:
        return "wing <10c"
    if px_c >= 90.0:
        return "wing >90c"
    return "mid 10-90c"


def cluster_se(vals, keys):
    g = defaultdict(float)
    for v, k in zip(vals, keys):
        g[k] += v
    n, m = len(vals), len(g)
    if m < 2:
        return float("nan")
    return math.sqrt(np.array(list(g.values())).var(ddof=1) * m) / n


def show(rows, label, indent="  "):
    if not rows:
        print(f"{indent}{label:30} -")
        return
    pnl = np.array([r["pnl_c"] for r in rows])          # already contract-weighted
    ct = np.array([r["ct"] for r in rows])
    n = len(rows)
    half = n // 2
    cpc = pnl.sum() / ct.sum()
    # c/ct uncertainty, clustered on the round (nine co-expiring legs are one bet)
    se = cluster_se(pnl, [r["round"] for r in rows]) * n / ct.sum()
    h1 = pnl[:half].sum() / max(ct[:half].sum(), 1)
    h2 = pnl[half:].sum() / max(ct[half:].sum(), 1)
    print(f"{indent}{label:30} fills={n:6d} ct={int(ct.sum()):6d} tot={pnl.sum()/100:+8.2f}$ "
          f"c/ct={cpc:+7.3f} se={se:6.3f} lo95={cpc - 1.96 * se:+7.3f} "
          f"H1={h1:+7.3f} H2={h2:+7.3f}")


def main():
    rows, meta, clip = s.load()
    rows = [r for r in rows if r["series"] in s.CRYPTO]

    # Sequential replay for the opening/reducing label and the additive per-fill P&L.
    pos = defaultdict(float)
    fills = []
    for f in rows:
        k = (f["journal"], f["ticker"])
        new = pos[k] + f["sgn"] * f["ct"]
        opening = abs(new) > abs(pos[k])
        pos[k] = new
        fills.append({**f,
                      "pnl_c": f["sgn"] * f["ct"] * (f["y"] - f["px"]),
                      "region": region(f["px"]),
                      "leg": "opening" if opening else "reducing",
                      "banded": f["cohort"] in BANDED})

    tot = sum(f["pnl_c"] for f in fills) / 100
    print(f"all real crypto 15M maker fills: {len(fills)} fills, "
          f"{int(sum(f['ct'] for f in fills))} contracts, {tot:+.2f}$  "
          f"(additive per-fill settlement P&L; reconciles to the per-market total)")

    print("\n" + "=" * 118)
    print("BY TICK REGIME -- all eras pooled")
    print("=" * 118)
    for r in ("wing <10c", "mid 10-90c", "wing >90c"):
        show([f for f in fills if f["region"] == r], r)
    show([f for f in fills if f["region"] != "mid 10-90c"], "BOTH WINGS pooled")

    print("\n" + "=" * 118)
    print("BY TICK REGIME x ERA -- under the 10-90c band a wing fill can only be a REDUCING order,")
    print("so the band era is not a clean read on the wing rule. The no-band era is.")
    print("=" * 118)
    for era, banded in (("NO BAND", False), ("BAND 10-90c", True)):
        print(f"  --- {era} ---")
        sub = [f for f in fills if f["banded"] == banded]
        for r in ("wing <10c", "mid 10-90c", "wing >90c"):
            show([f for f in sub if f["region"] == r], r, "    ")
        show([f for f in sub if f["region"] != "mid 10-90c"], "BOTH WINGS pooled", "    ")

    print("\n" + "=" * 118)
    print("OPENING vs REDUCING, no-band era only (the band restricted opening orders only)")
    print("=" * 118)
    nb = [f for f in fills if not f["banded"]]
    for r in ("wing <10c", "mid 10-90c", "wing >90c"):
        for leg in ("opening", "reducing"):
            show([f for f in nb if f["region"] == r and f["leg"] == leg], f"{r} / {leg}")

    print("\n" + "=" * 118)
    print("WHAT EACH REGIME IS WORTH PER HOUR (no-band era, the only one with a positive record)")
    print("=" * 118)
    span = defaultdict(lambda: [1 << 62, 0])
    for f in nb:
        a = span[f["journal"]]
        a[0], a[1] = min(a[0], f["ts_us"]), max(a[1], f["ts_us"])
    hours = sum((b - a) for a, b in span.values()) / 3.6e9
    for r in ("wing <10c", "mid 10-90c", "wing >90c"):
        sub = [f for f in nb if f["region"] == r]
        p = sum(f["pnl_c"] for f in sub) / 100
        c = sum(f["ct"] for f in sub)
        print(f"  {r:14} {p:+7.2f}$ over {hours:5.2f}h = {p / hours:+6.3f}$/h   "
              f"({c / hours:6.1f} ct/h, {100 * c / sum(f['ct'] for f in nb):5.1f}% of contracts)")
    p = sum(f["pnl_c"] for f in nb) / 100
    print(f"  {'TOTAL':14} {p:+7.2f}$ over {hours:5.2f}h = {p / hours:+6.3f}$/h "
          f"= {24 * p / hours:+6.2f}$/day at 100% duty")


if __name__ == "__main__":
    main()
