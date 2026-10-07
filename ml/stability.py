"""Is the reproduction arm's +69.4 c/market real, or a sample-size artifact?

The README flags it itself: the logistic arm went from +51 +-123 on 30 test markets to
+69 +-15 on 140, and an apparent edge that grows with n is the classic noise signature.
GLiNER has to BEAT this number, so an unstable benchmark voids the GPU run before it is paid
for. Four cheap checks, all on the built parquet, no GPU:

  1. sign split    -- PREREG bar: both halves of the test fold, split by market close.
  2. per series    -- a pooled statistic can move against every component.
  3. cross tape    -- fit on the EARLIER, disjoint tape alone; does the fit transfer?
  4. edge vs n     -- cumulative c/market over the test markets in close order.

    python3 ml/stability.py ml/data/mk5s-v1
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from policy_eval import (arm_always, arm_detgate, arm_logit,  # noqa: E402
                         per_market_net, summarize)

root = Path(sys.argv[1])
man = json.loads((root / "manifest.json").read_text())
d = pd.read_parquet(root / "rows.parquet")
tr = d[d.fold == "train"]
te = d[d.fold == "test"].reset_index(drop=True)
print(f"mark = {man['mark']}   train {tr.ticker.nunique()} markets   test {te.ticker.nunique()} markets")

# one fit, used by every subset below: the model never sees a test row
pred = arm_logit(tr, te)
always, detgate = arm_always(te), arm_detgate(te)


def table(name: str, sub: pd.DataFrame) -> pd.DataFrame:
    idx = sub.index.to_numpy()
    s = sub.reset_index(drop=True)
    t = summarize(s, {"always": always[idx], "detgate": detgate[idx], "logit": pred[idx]})
    t.insert(0, "subset", name)
    return t


close = te.groupby("ticker").vt.max().sort_values()
order = {tk: i for i, tk in enumerate(close.index)}

print("\n1. sign split, test fold by market close (PREREG: both halves same sign)")
half = len(close) // 2
early, late = set(close.index[:half]), set(close.index[half:])
print(pd.concat([table("early", te[te.ticker.isin(early)]),
                 table("late", te[te.ticker.isin(late)])]).to_string())

print("\n2. per series (pooled can move against every component)")
print(pd.concat([table(sv, g) for sv, g in te.groupby("series")]).to_string())

print("\n3. cross tape: fit on the earlier tape alone, score the same 140 test markets")
tapes = sorted(tr.tape.unique())
for tp in tapes:
    sub = tr[tr.tape == tp]
    p = arm_logit(sub, te)
    t = summarize(te, {"logit": p})
    t.insert(0, "fit_on", f"{tp} ({sub.ticker.nunique()} mkts)")
    print(t.to_string())

print("\n4. edge vs n: cumulative c/market, test markets in close order")
net = {k: per_market_net(te, v) for k, v in
       {"always": always, "detgate": detgate, "logit": pred}.items()}
rows = []
for k, s in net.items():
    s = s.reindex(close.index)
    for n in (10, 20, 30, 50, 70, 100, 140):
        x = s.iloc[:n]
        se = x.std(ddof=1) / np.sqrt(n)
        rows.append({"arm": k, "n": n, "c_per_market": round(x.mean(), 2),
                     "se": round(se, 2), "t": round(x.mean() / se, 2)})
print(pd.DataFrame(rows).pivot(index="n", columns="arm").to_string())

print("\nlogit, per-market net distribution on the test fold:")
x = net["logit"]
print(f"  markets {len(x)}  mean {x.mean():.2f}  median {x.median():.2f}  "
      f"zero {(x == 0).sum()}  >0 {(x > 0).sum()}  <0 {(x < 0).sum()}")
print(f"  top 5 markets contribute {x.nlargest(5).sum():.0f} c of {x.sum():.0f} c "
      f"({100 * x.nlargest(5).sum() / x.sum():.0f}%)")
print(f"  minus the best 5: {x.drop(x.nlargest(5).index).mean():.2f} c/market")
