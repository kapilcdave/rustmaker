"""Score a decision policy in cents per MARKET, the unit of evidence.

A policy is a label per row (BUY_YES / BUY_NO / HOLD / TAKE_PROFIT / STOP_LOSS). Accounting is
lot-wise: every accepted entry is its own 1-contract lot, carried to settlement unless the policy
calls TAKE_PROFIT or STOP_LOSS on one of THAT LOT's own exit rows, in which case the lot is crossed
out at that row's exitable touch and charged the taker fee. Matching exits to their lot by lot_id is
what makes the two exit actions separable; the position cap in the dataset keeps the whole thing
close to what one book could actually carry.

Arms reported together, because an OOS number with no reproduction arm is unattributable:
  always    post the improved quote at every chance, never exit  (the ungated quoter)
  detgate   the deterministic toxicity rule already in penny.py  (mom > 0.25 c or imb > 0.9213)
  logit     multinomial logistic on the same numeric features, fit on train  (reproduction arm)
  <policy>  whatever labels were handed in
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from features import EXIT_ACTIONS, NUMERIC, taker_fee_c


def per_market_net(d: pd.DataFrame, pred: np.ndarray) -> pd.Series:
    """Cents per market for one policy. d must be one fold, with the row order of pred."""
    d = d.reset_index(drop=True)
    pred = np.asarray(pred)
    out = {}
    for tk, g in d.groupby("ticker", sort=False):
        p = pred[g.index.to_numpy()]
        ent = g[(g.kind == "entry") & np.isin(p, ["BUY_YES", "BUY_NO"])]
        # an entry is only taken when the policy's side matches the side the fill actually was
        side_ok = np.where(ent.s < 0, "BUY_YES", "BUY_NO") == pred[ent.index.to_numpy()]
        ent = ent[side_ok]
        ex = g[(g.kind == "exit") & np.isin(p, EXIT_ACTIONS)]
        by_lot = {k: v for k, v in ex.groupby("lot_id", sort=False)}
        total = 0.0
        for r in ent.itertuples():
            rows = by_lot.get(r.lot_id)
            if rows is not None and len(rows):
                x = rows.sort_values("vt").iloc[0]      # the first exit the policy calls on this lot
                lot = -r.s                              # +1 long yes, -1 short yes
                total += lot * (x.px_c - r.px_c) - taker_fee_c(x.px_c)
            else:
                total += r.net_c                        # settlement, net of the maker fee
        out[tk] = total
    return pd.Series(out, name="net_c")


def summarize(d: pd.DataFrame, arms: dict[str, np.ndarray]) -> pd.DataFrame:
    rows = []
    for name, pred in arms.items():
        x = per_market_net(d, pred)
        n = len(x)
        se = x.std(ddof=1) / np.sqrt(n) if n > 1 else np.nan
        taken = int(((d.kind == "entry") & np.isin(pred, ["BUY_YES", "BUY_NO"])).sum())
        rows.append({"arm": name, "markets": n, "c_per_market": x.mean(), "se": se,
                     "t": x.mean() / se if se else np.nan, "entries_taken": taken,
                     "tp": int(((d.kind == "exit") & (pred == "TAKE_PROFIT")).sum()),
                     "sl": int(((d.kind == "exit") & (pred == "STOP_LOSS")).sum())})
    return pd.DataFrame(rows).set_index("arm").round(3)


def arm_always(d: pd.DataFrame) -> np.ndarray:
    return np.where(d.kind == "entry", np.where(d.s < 0, "BUY_YES", "BUY_NO"), "HOLD")


def arm_detgate(d: pd.DataFrame) -> np.ndarray:
    """penny.py's gate: pull the quote on 1 s momentum against us or a one-sided touch."""
    adverse = np.where(d.s > 0, d.mom1s_c, -d.mom1s_c)       # s>0 = our ask fills (we sold yes)
    heavy = np.where(d.s > 0, d.imb, 1 - d.imb)
    toxic = (adverse > 0.25) | (heavy > 0.9213)
    return np.where((d.kind == "entry") & ~toxic, np.where(d.s < 0, "BUY_YES", "BUY_NO"), "HOLD")


def arm_bracket(d: pd.DataFrame, tp_c: float = 2.0, sl_c: float = 3.0) -> np.ndarray:
    """A fixed bracket, the thing a trader would write by hand: flatten at +tp_c or -sl_c
    unrealized. The model has to beat THIS, not just beat never exiting."""
    u = d.unreal_c.to_numpy()
    act = np.where(u >= tp_c, "TAKE_PROFIT", np.where(u <= -sl_c, "STOP_LOSS", "HOLD"))
    entry = np.where(d.s < 0, "BUY_YES", "BUY_NO")
    return np.where(d.kind == "entry", entry, act)


def arm_oracle(d: pd.DataFrame) -> np.ndarray:
    """The true labels. Not a policy -- it reads the settlement -- but it is the ceiling: if exit
    timing is worth little even with perfect foresight, no model of it can pay."""
    return d.label.to_numpy()


def arm_logit(train: pd.DataFrame, test: pd.DataFrame) -> np.ndarray:
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    from sklearn.pipeline import make_pipeline
    m = make_pipeline(StandardScaler(), LogisticRegression(max_iter=2000))
    m.fit(np.nan_to_num(train[NUMERIC].to_numpy()), train.label.to_numpy())
    return m.predict(np.nan_to_num(test[NUMERIC].to_numpy()))
