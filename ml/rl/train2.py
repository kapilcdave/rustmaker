"""v2 trainer: capture-decomposed C51, seed ensemble, smoothed checkpoint selection.

    # train + val-select. No test rows read.
    .venv/bin/python ml/rl/train2.py ml/data/mk5s-v4 --out ml/runs/rlmm-v2 --seeds 3

    # the single scored evaluation
    .venv/bin/python ml/rl/train2.py ml/data/mk5s-v4 --out ml/runs/rlmm-v2 --seeds 3 --score-test

What is different from `rl/train.py`, and why (see `rl/shaped.py` for the derivations):
  1. the critic learns `-adverse_c` and the known `capture_c` is added back analytically;
  2. the C51 support is fitted to the SHAPED return, which is ~an order of magnitude tighter, so
     the atoms can actually resolve the +0.069 c/ct cell v1 got wrong;
  3. Q is the mean over `--seeds` independently seeded critics, and the checkpoint is picked off a
     3-point moving average of the val curve rather than its single luckiest point -- v1 shipped
     the best of 120 val evaluations and gave back 19.8 c/market out of sample.

Everything else is deliberately identical to v1: same env, same scorer, same benchmark arms, same
`room4`, same gates. The comparison is the point.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import torch

ML = Path(__file__).resolve().parent.parent
if str(ML) not in sys.path:
    sys.path.insert(0, str(ML))

from policy_eval import arm_always, arm_detgate, arm_logit, per_market_net, summarize  # noqa: E402
from rl import bandit as bd  # noqa: E402
from rl import glft  # noqa: E402
from rl.arms import arm_room4  # noqa: E402
from rl.c51 import N_ATOMS, n_step  # noqa: E402
from rl.tau_ceiling import TAU_GRID  # noqa: E402
from rl.env import N_FEAT, POST, Episode, episodes, labels_from, rollout  # noqa: E402
from rl.shaped import Ensemble, ShapedAgent  # noqa: E402
from rl.train import (COVER_PER_ITER, GAMMA_RL, GRAD_STEPS_PER_ITER, LR,  # noqa: E402
                      MARKETS_PER_ITER, N_STEP, RISK_GRID, TARGET_SYNC, WARMUP,
                      BATCH, BUFFER, HIDDEN, normalizer, paired, shift_null, width_overlap)

SMOOTH = 3          # points in the val moving average used for checkpoint selection
G2_BAR_C = 9.0      # magnitude bar, re-derived for the corrected instrument (PREREG_rlmm_v3 §3)
# ⚑ MEASURED on the train fold before any val or test number existed (see FINDINGS_rlmm_v2):
# the shaped 3-step return is heavy-tailed -- q0.001 is -71.19c but q0.01 is only -31.19c -- so
# v1's q[0.001,0.999] support spent its 51 atoms covering a range set by 0.1% of the samples and
# left 3.695 c/atom of resolution, against an edge of +0.069 c/ct in the cell it got wrong.
# q[0.01,0.99] with 201 atoms gives 0.394 c/atom, a 9.4x improvement, and clips 0.68% of targets.
# C51 clips Bellman targets into the support, so this trade is explicit: a little tail censoring
# bought an order of magnitude of resolution where the decision actually happens.
N_ATOMS_V2 = 201
SUPPORT_Q = (0.01, 0.99)


def evaluate(ens: Ensemble, eps: list[Episode], mu, sd, risk: float, tau: float = 0.0):
    ens.eval_mode(True)
    acts, total, posts = [], 0.0, 0
    for ep in eps:
        a, _, _, pnl = rollout(ep, ens.decider(mu, sd, risk, ep, tau))
        acts.append(a)
        total += pnl
        posts += int(a.sum())
    ens.eval_mode(False)
    return acts, total / max(len(eps), 1), posts


def train(ens: Ensemble, eps, cells, mu, sd, iters, val_eps, eval_every, log, va, room4_val):
    ex = bd.Exp3()
    avail = np.array([(cells == c).sum() > 0 for c in range(bd.N_CELLS)], dtype=float)
    by_cell = {c: np.flatnonzero(cells == c) for c in range(bd.N_CELLS)}
    scale = float(np.std([ep.net_c.sum() for ep in eps]) or 1.0)
    rng = ens.agents[0].rng
    hist, curves = [], {r: [] for r in RISK_GRID}
    states = {r: [] for r in RISK_GRID}
    for it in range(iters):
        cell, p = ex.draw(avail, rng)
        pick = rng.choice(by_cell[cell], size=min(MARKETS_PER_ITER, len(by_cell[cell])),
                          replace=False)
        pnls = []
        for i in pick:
            ep = eps[i]
            for ag in ens.agents:
                ag.net.reset_noise()
            dec = ens.decider(mu, sd, 1.0, ep)
            acts, st, _, pnl = rollout(ep, dec)
            for ag in ens.agents:
                ag.push(st, acts, ep.net_c, ep.capture_c, mu, sd)
            pnls.append(pnl)
        ex.update(cell, p, float(np.mean(pnls)) if pnls else 0.0, scale)
        # action coverage, excluded from the bandit's reward (v1 amendment 3)
        for i in rng.choice(len(eps), size=min(COVER_PER_ITER, len(eps)), replace=False):
            ep = eps[i]
            acts, st, _, _ = rollout(ep, lambda *_: True)
            for ag in ens.agents:
                ag.push(st, acts, ep.net_c, ep.capture_c, mu, sd)
        for _ in range(GRAD_STEPS_PER_ITER):
            for ag in ens.agents:
                ag.learn()
        if eval_every and (it + 1) % eval_every == 0:
            row = {"iter": it + 1, "buffer": ens.agents[0].buf.size,
                   "grad_steps": ens.agents[0].steps,
                   "buf_post_share": round(float(
                       ens.agents[0].buf.a[:ens.agents[0].buf.size].mean()), 4)}
            for risk in RISK_GRID:
                acts_v, cpm, posts = evaluate(ens, val_eps, mu, sd, risk)
                # ⚑ SELECT ON THE PAIRED DIFF, not on absolute val c/market. The chronological
                # split left the val fold with the OPPOSITE SIGN to train and test on the ungated
                # baseline (val +0.0770 c/ct vs train -0.0735 and test -0.0546), so absolute val
                # cents are contaminated by fold-level drift that no policy controls. The paired
                # difference against room4 is computed on identical rows, so the drift cancels --
                # and it is the statistic the gates are written on, which is what selection should
                # always track.
                dv = paired(per_market_net(va, labels_from(va, val_eps, acts_v)), room4_val)
                curves[risk].append(dv["diff"])
                states[risk].append([ag.state() for ag in ens.agents])
                row[f"val_c_risk{risk}"] = round(cpm, 2)
                row[f"val_vs_room4_risk{risk}"] = dv["diff"]
                row[f"val_posts_risk{risk}"] = posts
            hist.append(row)
            log(json.dumps(row))
    # smoothed selection: the best SMOOTH-point moving average, and the checkpoint at its centre
    best = {"val_c": -np.inf, "risk": RISK_GRID[0], "states": None, "iter": -1, "smoothed": None}
    n_evals = len(next(iter(curves.values()))) if curves else 0
    k = SMOOTH if n_evals >= SMOOTH else 1
    if k != SMOOTH:
        # A silent fall-through here would ship the LAST iterate while the report claimed a
        # val-selected checkpoint -- the exact kind of unattributable policy v1's 19.8c swing came
        # from. Degrade loudly to raw argmax instead.
        log(f"WARNING: only {n_evals} val evaluations, fewer than SMOOTH={SMOOTH}; selecting on "
            f"the RAW val argmax. Do not score a run in this state -- raise --iters.")
    for risk, c in curves.items():
        if len(c) < k:
            continue
        ma = np.convolve(c, np.ones(k) / k, mode="valid")
        j = int(np.argmax(ma))
        centre = j + k // 2
        if ma[j] > best["val_c"]:
            best = {"val_c": float(ma[j]), "risk": risk, "states": states[risk][centre],
                    "iter": hist[centre]["iter"], "smoothed": k,
                    "raw_at_centre": float(c[centre])}
    return best, hist, ex.report(), {str(k): [round(x, 2) for x in v] for k, v in curves.items()}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("data")
    ap.add_argument("--out", default="ml/runs/rlmm-v2")
    ap.add_argument("--iters", type=int, default=3000)
    ap.add_argument("--eval-every", type=int, default=150)
    ap.add_argument("--seeds", type=int, default=3)
    ap.add_argument("--load", default=None)
    ap.add_argument("--mark", default=None, choices=["mk5s", "mk60s", "settle"],
                    help="re-derive net_c and the label off a different markout than the one the "
                         "dataset was built with. `market_rows` stores all three columns on every "
                         "row, so this is a relabelling, not a rebuild. mk60s needs ~2,104 markets "
                         "for a t=2 test of a 10 c/market effect and mk5s needs 151, which is why "
                         "every earlier run in this corpus was stuck at mk5s on 558 markets.")
    ap.add_argument("--tau-select", action="store_true",
                    help="after the checkpoint is chosen, sweep the calibration offset tau on VAL "
                         "and use the winner for the test score. Declared in the prereg before "
                         "this run; the tau VALUE is fitted on val only.")
    ap.add_argument("--score-test", action="store_true")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    lf = (out / "train.log").open("w")

    def log(m):
        print(m)
        lf.write(str(m) + "\n")
        lf.flush()

    root = Path(a.data)
    man = json.loads((root / "manifest.json").read_text())
    d = pd.read_parquet(root / "rows.parquet")
    mark = a.mark or man["mark"]
    if a.mark and a.mark != man["mark"]:
        col = {"mk5s": "mk5s_c", "mk60s": "mk60s_c", "settle": "settle_c"}[a.mark]
        # The markout columns are produced by _at(), which CLIPS its index, so a row whose window
        # runs past the end of its tape silently marks against the last book row it has instead of
        # against t+h. At h=60s that is a real exposure on hourly tapes. Detect it the only way the
        # parquet allows -- a row where the 5 s and 60 s marks are bit-identical almost certainly
        # had both clipped to the same final mid -- and refuse to relabel if it is material.
        same = float((d.mk5s_c == d.mk60s_c).mean())
        d = d.assign(net_c=d[col])
        d["label"] = np.where(d.net_c <= 0, "HOLD",
                              np.where(d.s < 0, "BUY_YES", "BUY_NO"))
        log(f"RELABELLED {man['mark']} -> {a.mark} (column {col}); "
            f"{same:.2%} of rows have mk5s == mk60s exactly (suspected window clip)")
        if same > 0.10:
            raise SystemExit(f"{same:.1%} of rows look window-clipped; the {a.mark} mark is not "
                             f"measurable on this tape set")
    tr = d[d.fold == "train"].reset_index(drop=True)
    va = d[d.fold == "val"].reset_index(drop=True)
    tr_eps, va_eps = episodes(tr), episodes(va)
    mu, sd = normalizer(tr_eps)
    log(f"dataset {root}  mark={mark}  rows={len(d):,}  markets={d.ticker.nunique()}")
    log(f"train {tr.ticker.nunique()} / val {va.ticker.nunique()} markets, {N_FEAT} features, "
        f"{a.seeds} seeds")

    mk = lambda s: ShapedAgent(N_FEAT, s, HIDDEN, LR, BUFFER, GAMMA_RL, N_STEP,  # noqa: E731
                               BATCH, TARGET_SYNC, WARMUP, n_atoms=N_ATOMS_V2)
    ens = Ensemble([mk(s) for s in range(a.seeds)])

    # support from the SHAPED return, which is the whole point of the decomposition
    shaped_rets = np.concatenate([
        n_step(e.x, np.ones(len(e), np.int64), (e.net_c - e.capture_c).astype(np.float32),
               N_STEP, GAMMA_RL)[3] for e in tr_eps])
    lo, hi = np.quantile(shaped_rets, list(SUPPORT_Q))
    pad = 0.25 * (hi - lo)
    for ag in ens.agents:
        ag.set_support(lo - pad, hi + pad)
    unshaped = np.concatenate([
        n_step(e.x, np.ones(len(e), np.int64), e.net_c, N_STEP, GAMMA_RL)[3] for e in tr_eps])
    ulo, uhi = np.quantile(unshaped, [0.001, 0.999])
    uw, sw = (uhi - ulo) * 1.5, (hi - lo) * 1.5
    clipped = float(((shaped_rets < lo - pad) | (shaped_rets > hi + pad)).mean())
    log(f"C51 support SHAPED q{SUPPORT_Q} [{lo - pad:.2f}, {hi + pad:.2f}] c over "
        f"{N_ATOMS_V2} atoms = {sw / (N_ATOMS_V2 - 1):.3f} c/atom, {clipped:.2%} clipped")
    log(f"  (v1: q[0.001,0.999] over {N_ATOMS} atoms = {uw / (N_ATOMS - 1):.3f} c/atom "
        f"-- this is {(uw / (N_ATOMS - 1)) / max(sw / (N_ATOMS_V2 - 1), 1e-9):.1f}x finer)")
    _cap = np.concatenate([e.capture_c for e in tr_eps])
    _adv = np.concatenate([e.adverse_c for e in tr_eps])
    _share = _cap.var() / (_cap.var() + _adv.var())
    log(f"  reward decomposition: capture sd {_cap.std():.3f}c vs adverse sd {_adv.std():.3f}c "
        f"-- capture is {_share:.2%} of the variance, so the decomposition is NOT variance "
        f"reduction; what it buys is an EXACT edge term rather than an approximated one")

    stress = bd.market_stress(tr_eps)
    bounds = bd.fit_cells(stress)
    cells = bd.assign_cells(stress, bounds)

    if a.load:
        ck = torch.load(Path(a.load) / "c51v2.pt", map_location="cpu", weights_only=False)
        for ag, st in zip(ens.agents, ck["states"]):
            ag.net.load_state_dict(st)
            ag.set_support(ck["vmin"], ck["vmax"])
        mu, sd = ck["mu"], ck["sd"]
        best = {"val_c": ck.get("val_c"), "risk": ck["risk"], "iter": ck.get("iter")}
        hist, ex_report, curves = ck.get("val_history", []), {}, {}
        log(f"loaded {a.load}: risk={best['risk']} iter={best['iter']} val={best['val_c']}")
    else:
        t0 = time.time()
        room4_val = per_market_net(va, arm_room4(va))
        best, hist, ex_report, curves = train(ens, tr_eps, cells, mu, sd, a.iters, va_eps,
                                              a.eval_every, log, va, room4_val)
        log(f"trained {ens.agents[0].steps} steps/seed in {time.time() - t0:.0f}s; "
            f"best SMOOTHED val-vs-room4 {best['val_c']:+.2f} c/market at risk={best['risk']} "
            f"(iter {best['iter']}, raw {best.get('raw_at_centre', float('nan')):+.2f})")
        if not best["states"]:
            raise SystemExit("no checkpoint was selected on val -- refusing to save or score a "
                             "policy that would silently be the last iterate")
        for ag, st in zip(ens.agents, best["states"]):
            ag.net.load_state_dict(st)
        torch.save({"states": [ag.net.state_dict() for ag in ens.agents], "mu": mu, "sd": sd,
                    "risk": best["risk"], "vmin": ens.agents[0].vmin,
                    "vmax": ens.agents[0].vmax, "val_c": best["val_c"], "iter": best["iter"],
                    "val_history": hist, "seeds": a.seeds}, out / "c51v2.pt")

    tau = 0.0
    if a.tau_select:
        r4v = per_market_net(va, arm_room4(va))
        log("\nval tau sweep (calibration offset on the POST advantage):")
        rows_t = []
        for tv in TAU_GRID:
            av, _, pv = evaluate(ens, va_eps, mu, sd, best["risk"], tv)
            pd_ = paired(per_market_net(va, labels_from(va, va_eps, av)), r4v)
            rows_t.append((tv, pd_, pv))
            log(f"   tau {tv:+.2f}  vs room4 {pd_['diff']:+8.3f} +- {pd_['se']:.3f}  "
                f"t = {pd_['t']}  fills {pv:,}")
        tau = max(rows_t, key=lambda r: r[1]["diff"])[0]
        log(f"  tau selected on val: {tau:+.2f}")
        report_tau = [{"tau": r[0], **r[1], "fills": r[2]} for r in rows_t]
    else:
        report_tau = []

    glft_val = {g: glft.run(va_eps, g)[1] / max(len(va_eps), 1) for g in glft.GAMMA_GRID}
    best_g = max(glft_val, key=glft_val.get)
    _, v_cpm, v_posts = evaluate(ens, va_eps, mu, sd, best["risk"], tau)
    val_arms = {"always": arm_always(va), "detgate": arm_detgate(va), "room4": arm_room4(va),
                "logit": arm_logit(tr, va),
                "glft": labels_from(va, va_eps, glft.run(va_eps, best_g)[0]),
                "c51v2": labels_from(va, va_eps, evaluate(ens, va_eps, mu, sd, best["risk"], tau)[0]),
                "oracle_entry": np.where(va.kind == "entry", va.label, "HOLD")}
    vt = summarize(va, val_arms)
    log("\nVAL arm table (selection context, no test rows):\n" + vt.to_string())
    vn = {k: per_market_net(va, v) for k, v in val_arms.items()}
    for k in ("c51v2", "glft", "logit"):
        p = paired(vn[k], vn["room4"])
        log(f"  val {k:>8} - room4  {p['diff']:+9.3f} +- {p['se']:.3f}  t = {p['t']}")

    report = {"dataset": str(root), "mark": mark, "seeds": a.seeds, "iters": a.iters,
              "support": [ens.agents[0].vmin, ens.agents[0].vmax],
              "val_selection": {"risk": best["risk"], "smoothed_val_c": best["val_c"],
                                "iter": best["iter"], "glft_gamma": best_g, "tau": tau},
              "val_tau_sweep": report_tau,
              "val_curves": curves, "val_history": hist, "scenario_bandit": ex_report,
              "val_table": json.loads(vt.to_json(orient="index")),
              "val_c51v2_posts": v_posts, "test_scored": bool(a.score_test)}

    if a.score_test:
        te = d[d.fold == "test"].reset_index(drop=True)
        te_eps = episodes(te)
        log(f"\nTEST fold: {len(te):,} rows / {te.ticker.nunique()} markets")
        acts, cpm, posts = evaluate(ens, te_eps, mu, sd, best["risk"], tau)
        g_acts, g_tot, g_posts = glft.run(te_eps, best_g)
        arms = {"always": arm_always(te), "detgate": arm_detgate(te), "room4": arm_room4(te),
                "logit": arm_logit(tr, te), "glft": labels_from(te, te_eps, g_acts),
                "c51v2": labels_from(te, te_eps, acts),
                "oracle_entry": np.where(te.kind == "entry", te.label, "HOLD")}
        table = summarize(te, arms)
        log("\n" + table.to_string())
        nets = {k: per_market_net(te, v) for k, v in arms.items()}
        pairs = {f"{k} - room4": paired(nets[k], nets["room4"])
                 for k in ("c51v2", "glft", "logit", "detgate", "always")}
        pairs["c51v2 - glft"] = paired(nets["c51v2"], nets["glft"])
        pairs["c51v2 - logit"] = paired(nets["c51v2"], nets["logit"])
        log("\npaired per-market differences (the headline):")
        for k, v in pairs.items():
            log(f"  {k:>20}  {v['diff']:+9.3f} +- {v['se']:.3f}  t = {v['t']}  "
                f"n = {v['markets']}  share+ {v.get('share_pos')}")
        offered = int((te.kind == "entry").sum())
        null = shift_null(te, te_eps, acts, draws=20, seed=0)
        ov = width_overlap(te, arms["c51v2"], arms["room4"])
        log(f"\nposts: c51v2 {posts:,} ({100 * posts / max(offered, 1):.1f}% of {offered:,}), "
            f"glft {g_posts:,}, room4 {int(table.loc['room4', 'entries_taken']):,}")
        log(f"shift null: {null['c_per_market_mean']:+.2f} +- {null['c_per_market_sd']:.2f} "
            f"(hi95 {null['hi95']:+.2f})")
        log("fill-set decomposition vs room4: " + json.dumps(ov))
        pr = pairs["c51v2 - room4"]
        gates = {"G1_beats_room4_t2": bool(pr["t"] and pr["t"] >= 2.0 and pr["diff"] > 0),
                 # re-derived per instrument in PREREG_rlmm_v3: +9.0 c/market is what roughly
                 # DOUBLES the live seat's all-time +0.134 c/ct, given v5's 135 fills/market and
                 # the instrument's residual 2.1x understatement vs the real-print ledger. The
                 # v2 bar of +20 was computed on an instrument since shown to be broken.
                 "G2_magnitude": bool(pr["diff"] >= G2_BAR_C),
                 "G3_beats_glft": bool(pairs["c51v2 - glft"]["diff"] > 0),
                 "G4_not_room4_in_disguise": bool(ov["only_learned"]["rows"] > 0
                                                  and ov["jaccard"] < 0.9),
                 "G5_breadth": bool(pr["share_pos"] >= 0.55 and pr["drop_best5"] > 0),
                 "G6_posts_at_least_2pct": bool(posts >= 0.02 * offered),
                 "G7_above_turnover_null": bool(cpm > null["hi95"])}
        log("\ngates: " + json.dumps(gates, indent=2))
        log(f"VERDICT: {'PASS' if all(gates.values()) else 'FAIL'} "
            f"({sum(gates.values())}/{len(gates)})")
        report["test"] = {"table": json.loads(table.to_json(orient="index")), "paired": pairs,
                          "posts": {"c51v2": posts, "glft": g_posts, "offered": offered},
                          "shift_null": null, "width_overlap": ov,
                          "c51v2_c_per_market": round(cpm, 3)}
        report["gates"] = gates

    (out / "report.json").write_text(json.dumps(report, indent=2, default=str))
    log(f"\nwrote {out / 'report.json'}")
    lf.close()


if __name__ == "__main__":
    main()
