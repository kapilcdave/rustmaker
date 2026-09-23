"""Evaluate pull-the-quote rules on the mid-band print ledger (features at t - 11 ms).
A rule removes the fills it flags; what matters is the markout of the fills that REMAIN."""
import sys
import numpy as np
import pandas as pd
sys.path.insert(0, ".")
from tox import cluster_se

d = pd.read_parquet(sys.argv[1])
d = d[(d.mid_lag > 15) & (d.mid_lag < 85)].copy()
t_mid = d.vt.median()
d["half"] = np.where(d.vt < t_mid, "H1", "H2")
q = d.imb_taker.quantile([0.6, 0.8]).to_numpy()
rules = {
    "none (quote always)": np.zeros(len(d), bool),
    "mom1s with taker": d.mom1s_taker > 0.25,
    "hit side thin (imb top 20%)": d.imb_taker > q[1],
    "hit side thin (imb top 40%)": d.imb_taker > q[0],
    "mom OR thin20": (d.mom1s_taker > 0.25) | (d.imb_taker > q[1]),
    "mom OR thin40": (d.mom1s_taker > 0.25) | (d.imb_taker > q[0]),
    "mom5s with taker": d.mom5s_taker > 0.25,
    "mom1s OR mom5s OR thin20": (d.mom1s_taker > 0.25) | (d.mom5s_taker > 0.25) | (d.imb_taker > q[1]),
}
rows = []
for name, pull in rules.items():
    for h in ["H1", "H2"]:
        keep = d[(~pull) & (d.half == h)]
        tot = d[d.half == h]["count"].sum()
        r = {"rule": name, "half": h, "kept_ct_share": keep["count"].sum() / tot}
        for c in ["mk5s", "mk60s", "settle"]:
            r[c] = np.average(keep[c], weights=keep["count"])
        r["settle_se"] = cluster_se(keep, "settle")
        r["mk60s_se"] = cluster_se(keep, "mk60s")
        rows.append(r)
print(pd.DataFrame(rows).round(3).to_string(index=False))
