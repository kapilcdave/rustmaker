"""Do the BOCPD regime features and the exposure terms carry information at all?

The cheapest decisive measurement, and the one the corpus has run twice before: ask whether the new
features beat the incumbents PAIRED per market, and whether anything directional in them is above
chance. If they are at chance -- the way `flow_adv60` (AUC 0.500) and `flow60` (0.507) were -- then
no policy class reading them can invent the information, and a C51 result either side of zero is
about the optimiser, not about the venue.

    .venv/bin/python ml/rl/regime_lift.py ml/data/mk5s-v3

⚠ This scores TRAIN and VAL only. The test fold is the single scored evaluation of
`PREREG_rlmm_c51_20261007.md` and is not spent on a diagnostic.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ML = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ML))

from features import NUMERIC, QUEUE_NUMERIC, REGIME_NUMERIC, RL_NUMERIC  # noqa: E402
from policy_eval import arm_always, arm_detgate, per_market_net, summarize  # noqa: E402
from rl.arms import arm_room4  # noqa: E402

NEW = REGIME_NUMERIC + QUEUE_NUMERIC


def auc(score: np.ndarray, y: np.ndarray) -> float:
    """Rank AUC, ties averaged. No sklearn dependency so this runs anywhere the tape does."""
    s = np.asarray(score, dtype=float)
    y = np.asarray(y, dtype=bool)
    ok = np.isfinite(s)
    s, y = s[ok], y[ok]
    n1, n0 = int(y.sum()), int((~y).sum())
    if n1 == 0 or n0 == 0:
        return float("nan")
    r = pd.Series(s).rank().to_numpy()
    return float((r[y].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def fit(cols, train, test):
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    m = make_pipeline(StandardScaler(), LogisticRegression(max_iter=3000))
    m.fit(np.nan_to_num(train[cols].to_numpy()), train.label.to_numpy())
    return m.predict(np.nan_to_num(test[cols].to_numpy()))


def main() -> None:
    root = Path(sys.argv[1])
    man = json.loads((root / "manifest.json").read_text())
    d = pd.read_parquet(root / "rows.parquet")
    tr = d[d.fold == "train"].reset_index(drop=True)
    va = d[d.fold == "val"].reset_index(drop=True)
    print(f"mark = {man['mark']}   {man['markets']} markets   {len(RL_NUMERIC)} features "
          f"({len(NUMERIC)} incumbent + {len(NEW)} new)")

    # --- is anything in the new block above chance? ------------------------------------------
    # label: did this fill make money. The gate's own question, on the train fold only.
    y = (tr.net_c > 0).to_numpy()
    print(f"\nAUC for 'was this fill profitable', train fold, {len(tr):,} rows "
          f"(base rate {y.mean():.3f}):")
    ref = ["spread_ticks", "as_room_c", "sigma_c", "tvol60", "imb", "flow_adv60", "mom1s_c"]
    rows = []
    for c in NEW + ref:
        rows.append({"feature": c, "block": "new" if c in NEW else "incumbent",
                     "auc": round(auc(tr[c].to_numpy(), y), 4),
                     "auc_signed": round(auc(-tr[c].to_numpy(), y), 4)})
    t = pd.DataFrame(rows).set_index("feature")
    t["best"] = t[["auc", "auc_signed"]].max(axis=1).round(4)
    print(t.sort_values("best", ascending=False).to_string())

    # --- the TAIL, which is the claim a distributional critic actually makes ------------------
    # A feature at chance for E[profitable] can still predict the left tail, and that is exactly
    # what a CVaR-greedy quoter trades on ("drawdowns under persistent directional imbalance").
    # Refuting the mean does not refute the tail, so the tail gets its own measurement.
    print("\nAUC for 'is this fill in the worst q% of fills' -- the CVaR claim, train fold:")
    rows = []
    for q in (0.05, 0.10, 0.25):
        thr = np.quantile(tr.net_c.to_numpy(), q)
        bad = (tr.net_c.to_numpy() <= thr)
        r = {"worst_q": q, "threshold_c": round(float(thr), 3)}
        for c in NEW + ["spread_ticks", "sigma_c", "imb"]:
            v = tr[c].to_numpy()
            r[c] = round(max(auc(v, bad), auc(-v, bad)), 4) if np.std(v) > 0 else np.nan
        rows.append(r)
    print(pd.DataFrame(rows).set_index("worst_q").T.to_string())

    # correlation of the exposure terms with the incumbent they may just be restating
    print("\nthe exposure terms against the quantity already in detgate "
          "(heavy = s-signed depth imbalance):")
    heavy = (tr.s * (2.0 * tr.imb - 1.0)).to_numpy()
    for c in QUEUE_NUMERIC:
        v = tr[c].to_numpy()
        r = np.corrcoef(v, heavy)[0, 1] if np.std(v) > 0 else np.nan
        print(f"  corr({c:>11}, heavy) = {r: .6f}   sd = {np.std(v):.6f}")

    # --- does it convert to cents, paired per market? ----------------------------------------
    print(f"\n--- VAL fold, {va.ticker.nunique()} markets (test fold reserved for the prereg)")
    arms = {
        "always": arm_always(va),
        "detgate": arm_detgate(va),
        "room4": arm_room4(va),
        "logit_incumbent30": fit(NUMERIC, tr, va),
        "logit_new10": fit(NEW, tr, va),
        "logit_all40": fit(RL_NUMERIC, tr, va),
    }
    print(summarize(va, arms)[["c_per_market", "se", "t", "entries_taken"]].to_string())
    net = {k: per_market_net(va, v) for k, v in arms.items()}
    print("\npaired differences per market (the only fair comparison):")
    for a, b in (("logit_all40", "logit_incumbent30"), ("logit_all40", "room4"),
                 ("logit_new10", "room4"), ("logit_incumbent30", "room4")):
        x = (net[a] - net[b]).dropna()
        se = x.std(ddof=1) / np.sqrt(len(x))
        print(f"  {a:>18} - {b:<18} {x.mean():+8.2f} +- {se:5.2f}  t={x.mean() / se:+5.2f}")


if __name__ == "__main__":
    main()
