"""Score the markout-horizon hypothesis on FRESH tape. One evaluation, two signs.

The hypothesis (`FINDINGS_rlmm_v2_20261007.md` §7): a plain 30-feature logistic loses to the
one-line width gate at a 5-second markout and beats it at a 60-second markout, because the
HORIZON -- not the model class and not the feature set -- is what decides whether anything beyond
width pays.

That makes a prediction with TWO signs on the same markets, which is much harder to satisfy by
chance than one:

    mk60s:  logit - room4  >  0
    mk5s:   logit - room4  <  0

A single-sign test on a noisy 480-market fold is the kind of thing this corpus has been fooled by
before. Requiring the effect to REVERSE with the horizon, on identical rows, with the model fit on
identical training data, is a sign test no level artifact and no fold drift can produce: both marks
share the same fills, the same features, the same split and the same fold.

Training set is ALL of the dev dataset (every tape that has ever been in a dataset). The holdout is
tape collected after the hypothesis was formed and never used for anything. No val selection
happens here at all -- `arm_logit` is fit once and scored once, and `room4` is a frozen one-line
rule -- so there is no checkpoint, no risk level, no tau, and no multiplicity.

    .venv/bin/python ml/rl/score_holdout.py ml/data/mk5s-v5 ml/data/holdout-01
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ML = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ML))

from features import NUMERIC  # noqa: E402
from policy_eval import arm_always, arm_detgate, arm_logit, per_market_net, summarize  # noqa: E402
from rl.arms import arm_room4  # noqa: E402
from rl.train import paired  # noqa: E402

MARKS = {"mk5s": "mk5s_c", "mk60s": "mk60s_c"}


def relabel(d: pd.DataFrame, col: str) -> pd.DataFrame:
    d = d.assign(net_c=d[col])
    d["label"] = np.where(d.net_c <= 0, "HOLD", np.where(d.s < 0, "BUY_YES", "BUY_NO"))
    return d


def breadth(te: pd.DataFrame, a: np.ndarray, b: np.ndarray) -> dict:
    """Per-series and calendar-half splits -- this corpus's durability gate."""
    na, nb = per_market_net(te, a), per_market_net(te, b)
    out = {"pooled": paired(na, nb)}
    close = te.groupby("ticker").vt.max().sort_values()
    mid = len(close) // 2
    for lab, ts in (("early", close.index[:mid]), ("late", close.index[mid:])):
        out[lab] = paired(na[na.index.isin(ts)], nb[nb.index.isin(ts)])
    per = {}
    for s, g in te.assign(_a=a, _b=b).groupby("series"):
        gg = g.reset_index(drop=True)
        per[s] = paired(per_market_net(gg, gg._a.to_numpy()),
                        per_market_net(gg, gg._b.to_numpy()))["diff"]
    out["per_series"] = per
    out["series_positive"] = f"{sum(1 for v in per.values() if v > 0)}/{len(per)}"
    return out


def main() -> None:
    dev_root, ho_root = Path(sys.argv[1]), Path(sys.argv[2])
    dev = pd.read_parquet(dev_root / "rows.parquet")
    ho = pd.read_parquet(ho_root / "rows.parquet")
    dm = json.loads((dev_root / "manifest.json").read_text())
    hm = json.loads((ho_root / "manifest.json").read_text())

    dev_tapes = {Path(p).name for p in dm["tapes"]}
    ho_tapes = {Path(p).name for p in hm["tapes"]}
    overlap = dev_tapes & ho_tapes
    shared_mkts = set(dev.ticker.unique()) & set(ho.ticker.unique())
    print("=" * 88)
    print("CONTAMINATION GATE -- the holdout must share no tape and no market with the dev set")
    print("=" * 88)
    print(f"  dev tapes {len(dev_tapes)}   holdout tapes {len(ho_tapes)}   shared tapes "
          f"{len(overlap)}")
    print(f"  dev markets {dev.ticker.nunique()}   holdout markets {ho.ticker.nunique()}   "
          f"shared markets {len(shared_mkts)}")
    if overlap or shared_mkts:
        sys.exit(f"CONTAMINATED: {len(overlap)} shared tapes, {len(shared_mkts)} shared markets")
    print("  clean\n")

    results = {}
    for mark, col in MARKS.items():
        tr, te = relabel(dev, col), relabel(ho, col).reset_index(drop=True)
        lg, r4 = arm_logit(tr, te), arm_room4(te)
        arms = {"always": arm_always(te), "detgate": arm_detgate(te), "room4": r4,
                "logit": lg, "oracle_entry": np.where(te.kind == "entry", te.label, "HOLD")}
        print("=" * 88)
        print(f"{mark.upper()}  --  holdout, {te.ticker.nunique()} markets, {len(te):,} rows")
        print("=" * 88)
        print(summarize(te, arms)[["c_per_market", "se", "t", "entries_taken"]].to_string())
        b = breadth(te, lg, r4)
        p = b["pooled"]
        print(f"\n  logit - room4 = {p['diff']:+8.3f} +- {p['se']:.3f}  t = {p['t']}  "
              f"lo95 {p['lo95']}  share+ {p['share_pos']}  drop-best5 {p['drop_best5']:+.2f}")
        print(f"  halves: early {b['early']['diff']:+8.3f} (t {b['early']['t']})   "
              f"late {b['late']['diff']:+8.3f} (t {b['late']['t']})")
        print(f"  series {b['series_positive']} positive: " + "  ".join(
            f"{s.replace('KX', '').replace('15M', '')}:{v:+.0f}"
            for s, v in sorted(b["per_series"].items(), key=lambda kv: -kv[1])))
        ung = te[te.kind == "entry"].net_c.mean()
        print(f"  ungated level on this fold: {ung:+.4f} c/ct")
        results[mark] = {"paired": p, "breadth": {k: v for k, v in b.items()
                                                  if k != "per_series"},
                         "per_series": b["per_series"], "ungated_c_per_ct": float(ung)}

    d60, d5 = results["mk60s"]["paired"], results["mk5s"]["paired"]
    print("\n" + "=" * 88)
    print("THE PREREGISTERED SIGN TEST")
    print("=" * 88)
    g = {
        "H1_mk60s_positive": bool(d60["diff"] > 0),
        "H2_mk60s_t_at_least_2": bool(d60["t"] is not None and d60["t"] >= 2.0),
        "H3_mk5s_negative": bool(d5["diff"] < 0),
        "H4_reversal_exceeds_noise": bool(
            (d60["diff"] - d5["diff"]) > 2.0 * np.hypot(d60["se"], d5["se"])),
        "H5_mk60s_breadth": bool(d60["share_pos"] >= 0.55 and d60["drop_best5"] > 0),
        "H6_mk60s_both_halves_positive": bool(results["mk60s"]["breadth"]["early"]["diff"] > 0
                                              and results["mk60s"]["breadth"]["late"]["diff"] > 0),
    }
    print(f"  mk60s {d60['diff']:+.3f} +- {d60['se']:.3f} (t {d60['t']})   "
          f"mk5s {d5['diff']:+.3f} +- {d5['se']:.3f} (t {d5['t']})")
    print(f"  reversal = {d60['diff'] - d5['diff']:+.3f} c/market, "
          f"2*se_combined = {2 * np.hypot(d60['se'], d5['se']):.3f}")
    print(json.dumps(g, indent=2))
    print(f"VERDICT: {'CONFIRMED' if all(g.values()) else 'NOT CONFIRMED'} "
          f"({sum(g.values())}/{len(g)})")
    out = ho_root / "holdout_score.json"
    out.write_text(json.dumps({"dev": str(dev_root), "holdout": str(ho_root),
                               "results": results, "gates": g}, indent=2, default=str))
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
