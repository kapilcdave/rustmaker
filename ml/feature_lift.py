"""Does the v2 tape context (VWAP, moving averages, sigma, taker flow, A-S terms) add cents?

The question is NOT whether a richer feature set has higher accuracy. It is whether it beats the
two things already on the table, PAIRED per market:

    room4        spread_ticks >= 4 and nothing else -- the one-line rule the 14-feature logit
                 turned out to be (paired +3.7 +- 4.1 against it on v1)
    logit-base   the same model class on the ORIGINAL 14 features

A feature set earns its place only by beating both. Thresholds and feature sets are fixed before
this runs; the test fold is scored once.

    python3 ml/feature_lift.py ml/data/mk5s-v2
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from features import BASE_NUMERIC, CONTEXT_NUMERIC, NUMERIC  # noqa: E402
from policy_eval import arm_always, arm_detgate, per_market_net, summarize  # noqa: E402

root = Path(sys.argv[1])
man = json.loads((root / "manifest.json").read_text())
d = pd.read_parquet(root / "rows.parquet")
tr, va = d[d.fold == "train"], d[d.fold == "val"].reset_index(drop=True)
te = d[d.fold == "test"].reset_index(drop=True)
print(f"mark = {man['mark']}   {man['markets']} markets   {len(NUMERIC)} features")


def fit(cols, train, test):
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    m = make_pipeline(StandardScaler(), LogisticRegression(max_iter=3000))
    m.fit(np.nan_to_num(train[cols].to_numpy()), train.label.to_numpy())
    return m.predict(np.nan_to_num(test[cols].to_numpy()))


side = lambda f: np.where(f.s < 0, "BUY_YES", "BUY_NO")                      # noqa: E731
gate = lambda f, m: np.where((f.kind == "entry") & m, side(f), "HOLD")       # noqa: E731

for name, fold in (("VAL", va), ("TEST", te)):
    arms = {
        "always": arm_always(fold),
        "detgate": arm_detgate(fold),
        "room4": gate(fold, fold.spread_ticks >= 4),
        "logit_base14": fit(BASE_NUMERIC, tr, fold),
        "logit_ctx16": fit(CONTEXT_NUMERIC, tr, fold),
        "logit_all30": fit(NUMERIC, tr, fold),
    }
    print(f"\n--- {name} fold, {fold.ticker.nunique()} markets")
    print(summarize(fold, arms)[["c_per_market", "se", "t", "entries_taken"]].to_string())
    if name == "TEST":
        net = {k: per_market_net(fold, v) for k, v in arms.items()}
        print("\npaired differences per market (the only fair comparison):")
        for a, b in (("logit_all30", "room4"), ("logit_all30", "logit_base14"),
                     ("logit_ctx16", "room4"), ("logit_base14", "room4")):
            x = (net[a] - net[b]).dropna()
            se = x.std(ddof=1) / np.sqrt(len(x))
            print(f"  {a:>12} - {b:<12} {x.mean():+8.2f} +- {se:5.2f}  t={x.mean()/se:+5.2f}")
