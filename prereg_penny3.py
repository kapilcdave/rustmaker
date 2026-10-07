"""PREREG_penny3.md decision: penny3 c/market with lo95, H1/H2 split at the median close, and
penny3 - penny5 paired on the same markets (a market an arm never filled in counts as 0 for it).
Usage: python3 prereg_penny3.py tape.csv.gz
"""
import runpy, sys
import numpy as np
import pandas as pd

g = runpy.run_path("shadow_pnl.py", run_name="shadow_pnl")
m = g["m"]
# The event segment of the ticker (e.g. 26SEP251015) sorts by close time within a day's tape.
m["close_key"] = m.ticker.str.split("-").str[1]
wide = m.pivot_table(index=["ticker", "close_key"], columns="strat", values="total_c", aggfunc="sum").fillna(0.0).reset_index()
med = wide.close_key.sort_values().iloc[len(wide) // 2]
wide["half"] = np.where(wide.close_key < med, "H1", "H2")


def stat(x):
    x = np.asarray(x, float)
    mu, se = x.mean(), x.std(ddof=1) / np.sqrt(len(x))
    return f"{mu:+6.2f} ± {se:4.2f}  lo95 {mu - 1.96 * se:+6.2f}  (n={len(x)})"


print("\n== PREREG_penny3 decision (c/market, zeros where an arm had no fills) ==")
for arm in ["base", "penny2", "penny3", "penny4", "penny5"]:
    if arm in wide:
        print(f"{arm:7s} ALL {stat(wide[arm])} | H1 {wide[wide.half=='H1'][arm].mean():+6.2f}  H2 {wide[wide.half=='H2'][arm].mean():+6.2f}")
d = wide["penny3"] - wide["penny5"]
print(f"penny3-penny5 paired: {stat(d)} | H1 {d[wide.half=='H1'].mean():+.2f}  H2 {d[wide.half=='H2'].mean():+.2f}")
d4 = wide["penny4"] - wide["penny5"]
print(f"penny4-penny5 paired: {stat(d4)}")
p3 = wide["penny3"]
lo = p3.mean() - 1.96 * p3.std(ddof=1) / np.sqrt(len(p3))
ok = lo > 0 and p3[wide.half == "H1"].mean() > 0 and p3[wide.half == "H2"].mean() > 0 and d.mean() > 0 and wide["base"].mean() < 0
print("VERDICT:", "PASS" if ok else "NOT PASS", "| control base negative:", wide["base"].mean() < 0)
