"""Per-series gated P&L across disjoint shadow windows; OOS check of 'which series were positive'."""
import subprocess, sys, io, re
import numpy as np, pandas as pd
sys.path.insert(0, ".")
runs = [("R1 (09-23 07:43)", "data/box/shadow/shadow_1790150212908.csv.gz", "gate"),
        ("R2 (09-23 08:20)", "data/box/shadow_v2/shadow_1790152448386.csv.gz", "gate"),
        ("R3 (09-23 19:59)", "data/box/shadow_v3/shadow_1790193594915.csv.gz", "gate_pr"),
        ("R4 (09-23 23:38)", "data/box/shadow_v4/partial.csv", "base")]
# reuse shadow_pnl's per-market ledger by importing it as a module per file
import importlib.util
rows = []
for label, path, strat in runs:
    spec = importlib.util.spec_from_file_location("sp", "shadow_pnl.py")
    sp = importlib.util.module_from_spec(spec)
    sys.argv = ["x", path]
    out = io.StringIO(); old = sys.stdout; sys.stdout = out
    try:
        spec.loader.exec_module(sp)
    finally:
        sys.stdout = old
    m = sp.m[sp.m.strat == strat]
    for ser, g in m.groupby("series"):
        rows.append({"run": label, "series": ser.replace("KX", "").replace("15M", ""),
                     "mkts": len(g), "c_per_mkt": g.total_c.mean(),
                     "se": g.total_c.std(ddof=1) / np.sqrt(len(g))})
d = pd.DataFrame(rows)
piv = d.pivot(index="series", columns="run", values="c_per_mkt").round(1)
piv["sign+ runs"] = (d.pivot(index="series", columns="run", values="c_per_mkt") > 0).sum(axis=1)
print("gated c/market by series and disjoint window (SE per cell ~10-25c):")
print(piv.to_string())
# OOS: pick series positive in R1, score them in R2-R4 pooled (equal-weight per market)
r1 = d[d.run.str.startswith("R1")]
picked = r1[r1.c_per_mkt > 0].series.tolist()
later = d[d.run.str.startswith(("R3", "R4"))]  # R2 overlaps R1 in time: not out of sample
def pooled(sel):
    x = later[later.series.isin(sel)]
    n = x.mkts.sum(); mean = (x.c_per_mkt * x.mkts).sum() / n
    se = np.sqrt(((x.se ** 2) * x.mkts ** 2).sum()) / n
    return mean, se, n
mp, sp_, n = pooled(picked)
mo, so, no = pooled([s for s in d.series.unique() if s not in picked])
print(f"\npicked in R1 (positive): {picked}")
print(f"  their OOS result R3-R4: {mp:+.1f} ± {sp_:.1f} c/mkt over {n} markets")
print(f"  everything else R3-R4: {mo:+.1f} ± {so:.1f} c/mkt over {no} markets")

for ser in ["NEAR", "XRP", "SOL"]:
    m_, s_, n_ = pooled([ser])
    print(f"  {ser} alone R3-R4: {m_:+.1f} ± {s_:.1f} c/mkt over {n_} markets")
