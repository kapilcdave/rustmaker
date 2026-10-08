"""Score the FROZEN v3 agent on fresh tape, with a mandatory reproduction arm.

See `PREREG_rlmm_holdout_20261008.md`. Nothing is selected here: the checkpoint, the risk level,
the calibration offset tau, the GLFT gamma and the normalizer all come from
`ml/runs/rlmm-v3-mk5s`, every one of them fitted on v5's val fold before this tape existed.

Order of operations is the point:

  1. CONTAMINATION GATE -- the holdout must share no tape and no market with the dev set.
  2. REPRODUCTION ARM -- re-score the frozen config on v5's TEST fold. If it does not return
     `c51v3 - room4 = +1.142 +- 0.05`, exit non-zero and score nothing else: a null on the holdout
     would be unattributable between a fold-specific effect and a broken scorer.
  3. Only then read the holdout.

    .venv/bin/python ml/rl/score_holdout_agent.py ml/data/mk5s-v5 ml/data/holdout-01 \
        --load ml/runs/rlmm-v3-mk5s --tau -0.20
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import torch

ML = Path(__file__).resolve().parent.parent
if str(ML) not in sys.path:
    sys.path.insert(0, str(ML))

from policy_eval import arm_always, arm_detgate, arm_logit, per_market_net, summarize  # noqa: E402
from rl import glft  # noqa: E402
from rl.arms import arm_room4  # noqa: E402
from rl.env import N_FEAT, episodes, labels_from  # noqa: E402
from rl.shaped import Ensemble, ShapedAgent  # noqa: E402
from rl.train import (BATCH, BUFFER, GAMMA_RL, HIDDEN, LR, N_STEP,  # noqa: E402
                      TARGET_SYNC, WARMUP, paired)
from rl.train2 import N_ATOMS_V2, evaluate  # noqa: E402

V3_TEST_DIFF = 1.142      # FINDINGS_rlmm_v3_20261008.md, the number this run must reproduce
REPRO_TOL = 0.05
G2_BAR_C = 9.0


def load_frozen(run: Path, seeds: int | None = None):
    ck = torch.load(run / "c51v2.pt", map_location="cpu", weights_only=False)
    n = seeds or len(ck["states"])
    mk = lambda s: ShapedAgent(N_FEAT, s, HIDDEN, LR, BUFFER, GAMMA_RL, N_STEP,  # noqa: E731
                               BATCH, TARGET_SYNC, WARMUP, n_atoms=N_ATOMS_V2)
    ens = Ensemble([mk(s) for s in range(n)])
    for ag, st in zip(ens.agents, ck["states"]):
        ag.net.load_state_dict(st)
        ag.set_support(ck["vmin"], ck["vmax"])
    return ens, ck["mu"], ck["sd"], float(ck["risk"]), ck


def score(df: pd.DataFrame, tr: pd.DataFrame, ens, mu, sd, risk, tau, gamma):
    """The v3 arm table and paired differences, on whatever rows are handed in."""
    d = df.reset_index(drop=True)
    eps = episodes(d)
    acts, _, posts = evaluate(ens, eps, mu, sd, risk, tau)
    g_acts, _, _ = glft.run(eps, gamma)
    arms = {
        "always": arm_always(d),
        "detgate": arm_detgate(d),
        "room4": arm_room4(d),
        "logit": arm_logit(tr, d),
        "glft": labels_from(d, eps, g_acts),
        "c51v3": labels_from(d, eps, acts),
        "oracle_entry": np.where(d.kind == "entry", d.label, "HOLD"),
    }
    nets = {k: per_market_net(d, v) for k, v in arms.items()}
    pairs = {f"c51v3 - {k}": paired(nets["c51v3"], nets[k])
             for k in ("room4", "glft", "logit", "always")}
    pairs["logit - room4"] = paired(nets["logit"], nets["room4"])
    return {"rows": d, "eps": eps, "arms": arms, "nets": nets, "pairs": pairs,
            "table": summarize(d, arms), "posts": posts,
            "offered": int((d.kind == "entry").sum())}


def breadth(d: pd.DataFrame, a: np.ndarray, b: np.ndarray) -> dict:
    na, nb = per_market_net(d, a), per_market_net(d, b)
    out = {"pooled": paired(na, nb)}
    close = d.groupby("ticker").vt.max().sort_values()
    mid = len(close) // 2
    for lab, ts in (("early", close.index[:mid]), ("late", close.index[mid:])):
        out[lab] = paired(na[na.index.isin(ts)], nb[nb.index.isin(ts)])
    per = {}
    for s, g in d.assign(_a=a, _b=b).groupby("series"):
        gg = g.reset_index(drop=True)
        per[s] = paired(per_market_net(gg, gg._a.to_numpy()),
                        per_market_net(gg, gg._b.to_numpy()))
    out["per_series"] = {k: v["diff"] for k, v in per.items()}
    out["per_series_n"] = {k: v["markets"] for k, v in per.items()}
    out["series_positive"] = f"{sum(1 for v in out['per_series'].values() if v > 0)}/{len(per)}"
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("dev")
    ap.add_argument("holdout")
    ap.add_argument("--load", required=True)
    ap.add_argument("--tau", type=float, required=True,
                    help="FROZEN calibration offset. Must match the --load run's "
                         "report.json val_selection.tau; this script refuses to run if it does not.")
    ap.add_argument("--gamma", type=float, default=None,
                    help="FROZEN GLFT gamma; defaults to the --load run's val-selected value")
    a = ap.parse_args()

    dev_root, ho_root, run = Path(a.dev), Path(a.holdout), Path(a.load)
    rep = json.loads((run / "report.json").read_text())
    vs = rep["val_selection"]
    if abs(vs["tau"] - a.tau) > 1e-9:
        sys.exit(f"--tau {a.tau} does not match the frozen val selection {vs['tau']}")
    gamma = a.gamma if a.gamma is not None else float(vs["glft_gamma"])

    dev = pd.read_parquet(dev_root / "rows.parquet")
    ho = pd.read_parquet(ho_root / "rows.parquet")
    dm = json.loads((dev_root / "manifest.json").read_text())
    hm = json.loads((ho_root / "manifest.json").read_text())

    print("=" * 92)
    print("CONTAMINATION GATE")
    print("=" * 92)
    dt = {Path(p).name for p in dm["tapes"]}
    ht = {Path(p).name for p in hm["tapes"]}
    shared_t, shared_m = dt & ht, set(dev.ticker.unique()) & set(ho.ticker.unique())
    print(f"  dev    {len(dt):3d} tapes  {dev.ticker.nunique():5d} markets  {len(dev):7,} rows")
    print(f"  hold   {len(ht):3d} tapes  {ho.ticker.nunique():5d} markets  {len(ho):7,} rows")
    print(f"  shared tapes {len(shared_t)}   shared markets {len(shared_m)}")
    if shared_t or shared_m:
        sys.exit(f"CONTAMINATED: {len(shared_t)} tapes, {len(shared_m)} markets")
    print("  clean\n")

    ens, mu, sd, risk, ck = load_frozen(run)
    print(f"frozen: checkpoint {run}/c51v2.pt  iter {ck.get('iter')}  seeds "
          f"{len(ck['states'])}  risk {risk}  tau {a.tau:+.2f}  glft_gamma {gamma}\n")

    tr = dev[dev.fold == "train"].reset_index(drop=True)
    te = dev[dev.fold == "test"].reset_index(drop=True)

    print("=" * 92)
    print("REPRODUCTION ARM -- frozen config on v5's test fold")
    print("=" * 92)
    r = score(te, tr, ens, mu, sd, risk, a.tau, gamma)
    got = r["pairs"]["c51v3 - room4"]
    print(f"  c51v3 - room4 = {got['diff']:+8.3f} +- {got['se']:.3f}  t = {got['t']}  "
          f"n = {got['markets']}   (v3 reported {V3_TEST_DIFF:+.3f})")
    delta = abs(got["diff"] - V3_TEST_DIFF)
    if delta > REPRO_TOL:
        sys.exit(f"VOID: reproduction arm is {delta:.3f} c/market off v3's {V3_TEST_DIFF:+.3f}; "
                 f"refusing to score the holdout (PREREG_rlmm_holdout_20261008 §reproduction)")
    print(f"  reproduces to {delta:.4f} c/market -- the scorer is the scorer that produced v3\n")

    print("=" * 92)
    print(f"HOLDOUT -- {ho.ticker.nunique()} markets, {len(ho):,} rows, never read")
    print("=" * 92)
    h = score(ho, tr, ens, mu, sd, risk, a.tau, gamma)
    print(h["table"][["c_per_market", "se", "t", "entries_taken"]].to_string())
    print("\npaired per-market differences:")
    for k, v in h["pairs"].items():
        print(f"  {k:>16}  {v['diff']:+9.3f} +- {v['se']:.3f}  t = {str(v['t']):>6}  "
              f"n = {v['markets']}  share+ {v.get('share_pos')}  "
              f"drop-best5 {v.get('drop_best5', float('nan')):+.2f}")

    b = breadth(h["rows"], h["arms"]["c51v3"], h["arms"]["room4"])
    p = b["pooled"]
    print(f"\nbreadth of c51v3 - room4:")
    print(f"  halves: early {b['early']['diff']:+8.3f} (t {b['early']['t']}, n "
          f"{b['early']['markets']})   late {b['late']['diff']:+8.3f} (t {b['late']['t']}, n "
          f"{b['late']['markets']})")
    print(f"  series {b['series_positive']} positive: " + "  ".join(
        f"{s.replace('KX', '').replace('15M', '')}:{v:+.1f}(n{b['per_series_n'][s]})"
        for s, v in sorted(b["per_series"].items(), key=lambda kv: -kv[1])))
    ung = h["rows"][h["rows"].kind == "entry"].net_c.mean()
    print(f"  ungated level on this tape: {ung:+.4f} c/ct   "
          f"posts {h['posts']:,} of {h['offered']:,} offered "
          f"({h['posts'] / max(h['offered'], 1):.1%})")

    print("\n" + "=" * 92)
    print("THE PREREGISTERED DECISION RULE")
    print("=" * 92)
    D = p["diff"]
    verdict = ("REPLICATES (sign and rough magnitude)" if D >= 0.5
               else "ZERO at this resolution -- downgrade the claim" if D > -0.5
               else "DOES NOT SURVIVE -- retract the +1.142 as fold-specific")
    print(f"  D = c51v3 - room4 = {D:+.3f} +- {p['se']:.3f} (t {p['t']}) on {p['markets']} markets")
    print(f"  95% upper bound {D + 1.96 * p['se']:+.3f} vs the G2 bar {G2_BAR_C:+.1f} -- "
          f"{'still below, branch stays closed' if D + 1.96 * p['se'] < G2_BAR_C else 'CLEARS G2'}")
    print(f"  VERDICT: {verdict}")
    for k in ("c51v3 - glft", "c51v3 - logit"):
        v = h["pairs"][k]
        was = {"c51v3 - glft": 4.549, "c51v3 - logit": 2.978}[k]
        print(f"  secondary {k:>14} = {v['diff']:+7.3f} (t {v['t']})  v3 said {was:+.3f}  "
              f"{'SAME SIGN' if v['diff'] * was > 0 else '*** SIGN FLIP ***'}")

    out = ho_root / "holdout_agent_score.json"
    out.write_text(json.dumps({
        "dev": str(dev_root), "holdout": str(ho_root), "run": str(run),
        "frozen": {"risk": risk, "tau": a.tau, "glft_gamma": gamma,
                   "iter": ck.get("iter"), "seeds": len(ck["states"])},
        "reproduction": {"diff": got["diff"], "se": got["se"], "t": got["t"],
                         "v3_reported": V3_TEST_DIFF, "delta": delta},
        "holdout_pairs": {k: v for k, v in h["pairs"].items()},
        "breadth": b, "ungated_c_per_ct": float(ung),
        "posts": h["posts"], "offered": h["offered"],
        "decision": {"D": D, "se": p["se"], "t": p["t"], "verdict": verdict},
        "table": json.loads(h["table"].to_json(orient="index")),
    }, indent=2, default=str))
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
