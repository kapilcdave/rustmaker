"""Train and score the C51 market maker. One scored evaluation, behind an explicit flag.

    # 1. controls and the GLFT sweep on val -- no test fold is touched
    .venv/bin/python ml/rl/train.py ml/data/mk5s-v3 --out ml/runs/rlmm-v1

    # 2. the single scored run, after the val selection is written down
    .venv/bin/python ml/rl/train.py ml/data/mk5s-v3 --out ml/runs/rlmm-v1 --score-test

Discipline, from `PREREG_rlmm_c51_20261007.md`:
  * folds are the dataset's own chronological split; nothing is reshuffled;
  * every hyperparameter either is frozen in source or is selected on VAL, never on test;
  * the test fold is read only under `--score-test`, and the report records that it was;
  * the headline is the PAIRED difference against `room4`, plus the count of fills posted --
    a policy that earns its cents by posting almost nothing is a capacity statement, not an edge.
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
from rl.c51 import C51Net, N_ACTIONS, N_ATOMS, Replay, n_step, project, q_from_dist  # noqa: E402
from rl.env import N_FEAT, POST, Episode, episodes, labels_from, rollout  # noqa: E402

# ---- frozen hyperparameters. None of these is selected on any fold. --------------------------
GAMMA_RL = 0.99        # within-market discount. The coupling is inventory, which is short-ranged.
N_STEP = 3
LR = 3e-4
BATCH = 512
HIDDEN = 128
BUFFER = 400_000
GRAD_STEPS_PER_ITER = 8
MARKETS_PER_ITER = 6
COVER_PER_ITER = 3     # markets per iteration rolled out under `always` purely for ACTION
                       # COVERAGE. Not a tuning knob: Q(POST) can only be updated from
                       # transitions in which POST was taken, and the frozen configuration
                       # without this collapsed to 20 posts out of 48,335 offered val fills by
                       # iteration 1,100 -- after which the buffer held essentially no POST
                       # actions and the POST head stopped being trained at all. DQN is
                       # off-policy, so feeding it the `always` inventory path (the very path
                       # `features.market_rows` generated the rows under) is valid data, not a
                       # hint. These episodes are excluded from the scenario bandit's reward.
TARGET_SYNC = 250      # gradient steps
WARMUP = 4_000         # transitions before the first gradient step
# ---- selected on VAL, from these frozen grids ------------------------------------------------
RISK_GRID = (1.0, 0.5, 0.25, 0.1)     # CVaR level for decision-time scalarisation
# GLFT's risk aversion grid lives in rl/glft.GAMMA_GRID


def normalizer(eps: list[Episode]) -> tuple[np.ndarray, np.ndarray]:
    x = np.concatenate([e.x for e in eps]) if eps else np.zeros((1, N_FEAT), np.float32)
    mu = x.mean(0)
    sd = x.std(0)
    sd[sd < 1e-6] = 1.0           # a constant column (q_ahead_ct is one) must not become inf
    return mu.astype(np.float32), sd.astype(np.float32)


class Agent:
    def __init__(self, n_feat: int, seed: int, device: str = "cpu"):
        self.dev = torch.device(device)
        torch.manual_seed(seed)
        self.net = C51Net(n_feat, HIDDEN).to(self.dev)
        self.tgt = C51Net(n_feat, HIDDEN).to(self.dev)
        self.tgt.load_state_dict(self.net.state_dict())
        self.opt = torch.optim.Adam(self.net.parameters(), lr=LR, eps=1.5e-4)
        self.buf = Replay(BUFFER, n_feat)
        self.rng = np.random.default_rng(seed)
        self.steps = 0
        self.vmin, self.vmax = -1.0, 1.0
        self.support = torch.linspace(-1.0, 1.0, N_ATOMS, device=self.dev)

    def set_support(self, lo: float, hi: float) -> None:
        """Atom range from the TRAIN fold's n-step return quantiles, widened a little.

        C51 clips the Bellman target into [vmin, vmax], so a range that is too narrow silently
        censors exactly the tail events a distributional critic exists to represent.
        """
        self.vmin, self.vmax = float(lo), float(hi)
        self.support = torch.linspace(self.vmin, self.vmax, N_ATOMS, device=self.dev)

    # -- acting -------------------------------------------------------------------------------
    def _q(self, x: np.ndarray, risk: float) -> np.ndarray:
        with torch.no_grad():
            t = torch.as_tensor(x, dtype=torch.float32, device=self.dev)
            if t.ndim == 1:
                t = t.unsqueeze(0)
            return q_from_dist(self.net(t), self.support, risk).cpu().numpy()

    def decider(self, mu, sd, risk: float):
        def decide(state, _pos, _t):
            q = self._q((state - mu) / sd, risk)[0]
            return bool(q[POST] > q[0])
        return decide

    # -- learning -----------------------------------------------------------------------------
    def push(self, states: np.ndarray, acts: np.ndarray, net_c: np.ndarray, mu, sd) -> None:
        """Store one episode's n-step transitions.

        `net_c` is the reward a POST would earn at each step; the reward actually RECEIVED is zero
        wherever the policy skipped, and the n-step return has to be built from the received
        stream. Crediting `net_c` on skipped steps makes the bootstrap target the return of a
        policy that posts everywhere, which is `always` -- an arm already known to lose.
        """
        realized = (net_c * (acts == POST)).astype(np.float32)
        s, s2, a, r, d, g = n_step((states - mu) / sd, acts, realized, N_STEP, GAMMA_RL)
        self.buf.add_batch(s, a, r, s2, d, g)

    def learn(self) -> float | None:
        if self.buf.size < WARMUP:
            return None
        idx, w = self.buf.sample(BATCH, self.rng)
        dev = self.dev
        s = torch.as_tensor(self.buf.s[idx], device=dev)
        s2 = torch.as_tensor(self.buf.s2[idx], device=dev)
        a = torch.as_tensor(self.buf.a[idx], device=dev)
        r = torch.as_tensor(self.buf.r[idx], device=dev)
        d = torch.as_tensor(self.buf.d[idx], device=dev)
        g = torch.as_tensor(self.buf.g[idx], device=dev)
        wt = torch.as_tensor(w, device=dev)

        self.net.reset_noise()
        self.tgt.reset_noise()
        with torch.no_grad():
            # Double DQN: the ONLINE net picks the next action, the TARGET net values it
            a2 = q_from_dist(self.net(s2), self.support, 1.0).argmax(1)
            next_logp = self.tgt(s2)[torch.arange(len(a2), device=dev), a2]
            m = project(next_logp, r, d, g, self.support)
        logp = self.net(s)[torch.arange(len(a), device=dev), a]
        loss_i = -(m * logp).sum(1)                 # cross-entropy, per sample
        loss = (wt * loss_i).mean()
        self.opt.zero_grad(set_to_none=True)
        loss.backward()
        torch.nn.utils.clip_grad_norm_(self.net.parameters(), 10.0)
        self.opt.step()
        self.buf.update(idx, loss_i.detach().cpu().numpy())
        self.steps += 1
        if self.steps % TARGET_SYNC == 0:
            self.tgt.load_state_dict(self.net.state_dict())
        return float(loss.item())


def evaluate(agent: Agent, eps: list[Episode], mu, sd, risk: float):
    """Unweighted roll-out over a whole fold, noise off."""
    agent.net.eval()
    acts, total, posts = [], 0.0, 0
    decide = agent.decider(mu, sd, risk)
    for ep in eps:
        a, _, _, pnl = rollout(ep, decide)
        acts.append(a)
        total += pnl
        posts += int(a.sum())
    agent.net.train()
    return acts, total / max(len(eps), 1), posts


def train(agent: Agent, eps: list[Episode], cells: np.ndarray, mu, sd, iters: int,
          val_eps: list[Episode], eval_every: int, log=print):
    ex = bd.Exp3()
    avail = np.array([(cells == c).sum() > 0 for c in range(bd.N_CELLS)], dtype=float)
    by_cell = {c: np.flatnonzero(cells == c) for c in range(bd.N_CELLS)}
    scale = float(np.std([ep.net_c.sum() for ep in eps]) or 1.0)
    best = {"val_c": -np.inf, "risk": RISK_GRID[0], "state": None, "iter": -1}
    hist = []
    decide = agent.decider(mu, sd, 1.0)
    for it in range(iters):
        cell, p = ex.draw(avail, agent.rng)
        pick = agent.rng.choice(by_cell[cell], size=min(MARKETS_PER_ITER, len(by_cell[cell])),
                                replace=False)
        pnls, n_post, n_steps_seen = [], 0, 0
        for i in pick:
            ep = eps[i]
            agent.net.reset_noise()            # parametric exploration, per market
            acts, states, _, pnl = rollout(ep, decide)
            agent.push(states, acts, ep.net_c, mu, sd)
            pnls.append(pnl)
            n_post += int(acts.sum())
            n_steps_seen += len(acts)
        ex.update(cell, p, float(np.mean(pnls)) if pnls else 0.0, scale)
        # action coverage: `always` episodes, excluded from the bandit's reward above
        for i in agent.rng.choice(len(eps), size=min(COVER_PER_ITER, len(eps)), replace=False):
            ep = eps[i]
            acts, states, _, _ = rollout(ep, lambda *_: True)
            agent.push(states, acts, ep.net_c, mu, sd)
        for _ in range(GRAD_STEPS_PER_ITER):
            agent.learn()
        if eval_every and (it + 1) % eval_every == 0:
            row = {"iter": it + 1, "buffer": agent.buf.size, "grad_steps": agent.steps,
                   "buf_post_share": round(float(agent.buf.a[:agent.buf.size].mean()), 4),
                   "onpolicy_post_share": round(n_post / max(n_steps_seen, 1), 4)}
            for risk in RISK_GRID:
                _, cpm, posts = evaluate(agent, val_eps, mu, sd, risk)
                row[f"val_c_risk{risk}"] = round(cpm, 2)
                row[f"val_posts_risk{risk}"] = posts
                if cpm > best["val_c"]:
                    best = {"val_c": cpm, "risk": risk, "iter": it + 1,
                            "state": {k: v.detach().cpu().clone()
                                      for k, v in agent.net.state_dict().items()}}
            hist.append(row)
            log(json.dumps(row))
    return best, hist, ex.report()


def paired(a: pd.Series, b: pd.Series) -> dict:
    """Per-market paired difference, with the breadth checks the corpus requires.

    `share_pos` and `drop_best5` are there because this corpus has been burned by both: a +$16.64
    seat became +$0.38 once its best 20 of 585 markets were removed, and significance carried by
    one run is a t-stat statement, not an edge.
    """
    j = pd.concat([a.rename("a"), b.rename("b")], axis=1).dropna()
    d = j.a - j.b
    n = len(d)
    se = d.std(ddof=1) / np.sqrt(n) if n > 1 else np.nan
    trimmed = np.sort(d.to_numpy())[:-5] if n > 5 else d.to_numpy()
    return {"markets": int(n), "diff": round(float(d.mean()), 3),
            "se": round(float(se), 3),
            "t": round(float(d.mean() / se), 2) if se else None,
            "lo95": round(float(d.mean() - 1.96 * se), 3) if se else None,
            "share_pos": round(float((d > 0).mean()), 3),
            "drop_best5": round(float(trimmed.mean()), 3)}


def shift_null(te: pd.DataFrame, eps, acts, draws: int = 20, seed: int = 0):
    """Turnover-matched circular-shift null.

    The decision sequence is rolled within each market instead of redrawn, so the number of posts,
    their autocorrelation and their block length are all preserved and only the alignment to the
    state is destroyed. Redrawing a Bernoulli at the same rate would be a different and much
    weaker null -- a null bar scales with the mask's block length (corpus: 12.9x on one cell).
    Posts that would breach the inventory cap after the roll are dropped, and the post count is
    reported so any turnover gap against the real arm is visible rather than assumed away.
    """
    from features import CLIP_CT, POS_CAP
    rng = np.random.default_rng(seed)
    means, posts = [], []
    for _ in range(draws):
        rolled, kept = [], 0
        for ep, a in zip(eps, acts):
            T = len(a)
            r = np.roll(a, int(rng.integers(1, max(T, 2)))) if T > 1 else a.copy()
            pos, out = 0.0, np.zeros(T, dtype=np.int64)
            for t in range(T):
                if r[t] == POST and abs(pos + -float(ep.s[t]) * CLIP_CT) <= POS_CAP:
                    out[t] = POST
                    pos += -float(ep.s[t]) * CLIP_CT
            rolled.append(out)
            kept += int(out.sum())
        means.append(per_market_net(te, labels_from(te, eps, rolled)).mean())
        posts.append(kept)
    return {"draws": draws, "c_per_market_mean": round(float(np.mean(means)), 3),
            "c_per_market_sd": round(float(np.std(means, ddof=1)), 3),
            "hi95": round(float(np.mean(means) + 1.96 * np.std(means, ddof=1)), 3),
            "posts_mean": int(np.mean(posts))}


def width_overlap(te: pd.DataFrame, a: np.ndarray, b: np.ndarray) -> dict:
    """Is the learned arm just `room4` in disguise?

    Every selector measured on this dataset has collapsed to one width comparison, so the test is
    not "does it correlate" but "is there anything in the disagreement". The decomposition is
    disjoint: rows only A takes, rows only B takes, rows both take.
    """
    ent = (te.kind == "entry").to_numpy()
    ta, tb = (a != "HOLD") & ent, (b != "HOLD") & ent
    net, spr = te.net_c.to_numpy(), te.spread_ticks.to_numpy()

    def cell(m):
        n = int(m.sum())
        return {"rows": n,
                "c_per_ct": round(float(net[m].mean()), 4) if n else None,
                "mean_spread_ticks": round(float(spr[m].mean()), 2) if n else None}

    return {"both": cell(ta & tb), "only_learned": cell(ta & ~tb), "only_room4": cell(tb & ~ta),
            "jaccard": round(float((ta & tb).sum() / max((ta | tb).sum(), 1)), 3)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("data", help="dataset dir built by ml/build_dataset.py with the RL features")
    ap.add_argument("--out", default="ml/runs/rlmm-v1")
    ap.add_argument("--iters", type=int, default=1500)
    ap.add_argument("--eval-every", type=int, default=100)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--load", default=None,
                    help="score a checkpoint written by an earlier run (its c51.pt) instead of "
                         "training again. The val selection the checkpoint carries -- the risk "
                         "level and the chosen iteration -- is used as-is, so the scored run is "
                         "exactly the policy the val fold selected and cannot drift.")
    ap.add_argument("--score-test", action="store_true",
                    help="read the TEST fold and write the scored arm table. Without this flag "
                         "nothing in the run touches test, so the val selection can be iterated "
                         "on honestly.")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    log_path = out / "train.log"
    lf = log_path.open("w")

    def log(msg):
        print(msg)
        lf.write(str(msg) + "\n")
        lf.flush()

    root = Path(a.data)
    man = json.loads((root / "manifest.json").read_text())
    d = pd.read_parquet(root / "rows.parquet")
    log(f"dataset {root}  mark={man['mark']}  rows={len(d):,}  markets={d.ticker.nunique()}")
    tr = d[d.fold == "train"].reset_index(drop=True)
    va = d[d.fold == "val"].reset_index(drop=True)
    tr_eps, va_eps = episodes(tr), episodes(va)
    mu, sd = normalizer(tr_eps)

    # atom range from the train fold's own n-step returns
    rets = np.concatenate([n_step(e.x, np.ones(len(e), np.int64), e.net_c, N_STEP, GAMMA_RL)[3]
                           for e in tr_eps])
    lo, hi = np.quantile(rets, [0.001, 0.999])
    pad = 0.25 * (hi - lo)
    agent = Agent(N_FEAT, a.seed)
    agent.set_support(lo - pad, hi + pad)
    log(f"C51 support [{agent.vmin:.2f}, {agent.vmax:.2f}] c over {N_ATOMS} atoms, "
        f"{N_ACTIONS} actions, {N_FEAT} features")

    stress = bd.market_stress(tr_eps)
    bounds = bd.fit_cells(stress)
    cells = bd.assign_cells(stress, bounds)
    log("scenario cells (train terciles): " + json.dumps(
        {k: [round(x, 4) for x in v] for k, v in bounds.items()}))
    log("cell counts: " + json.dumps(np.bincount(cells, minlength=bd.N_CELLS).tolist()))

    if a.load:
        ck = torch.load(Path(a.load) / "c51.pt", map_location="cpu", weights_only=False)
        agent.net.load_state_dict(ck["net"])
        agent.set_support(ck["vmin"], ck["vmax"])
        mu, sd = ck["mu"], ck["sd"]
        best = {"val_c": ck.get("val_c"), "risk": ck["risk"], "iter": ck.get("iter"),
                "state": None}
        hist, ex_report = ck.get("val_history", []), ck.get("scenario_bandit", {})
        log(f"loaded {Path(a.load) / 'c51.pt'}: val-selected risk={best['risk']} "
            f"from iter {best['iter']} (val {best['val_c']} c/market). No training this run.")
    else:
        t0 = time.time()
        best, hist, ex_report = train(agent, tr_eps, cells, mu, sd, a.iters, va_eps,
                                      a.eval_every, log)
        log(f"trained {agent.steps} gradient steps in {time.time() - t0:.0f}s; "
            f"best val {best['val_c']:.2f} c/market at risk={best['risk']} "
            f"(iter {best['iter']})")
        log("scenario bandit: " + json.dumps(ex_report))
        if best["state"] is not None:
            agent.net.load_state_dict(best["state"])
        torch.save({"net": agent.net.state_dict(), "mu": mu, "sd": sd, "risk": best["risk"],
                    "vmin": agent.vmin, "vmax": agent.vmax, "features": N_FEAT,
                    "val_c": best["val_c"], "iter": best["iter"], "val_history": hist,
                    "scenario_bandit": ex_report}, out / "c51.pt")

    # GLFT's risk aversion, selected on val by the same rule
    glft_val = {}
    for g in glft.GAMMA_GRID:
        _, total, posts = glft.run(va_eps, g)
        glft_val[g] = {"c_per_market": round(total / max(len(va_eps), 1), 2), "posts": posts,
                       "spread_term_c": round(glft.spread_term_c(g), 4)}
    best_g = max(glft.GAMMA_GRID, key=lambda g: glft_val[g]["c_per_market"])
    log("GLFT val sweep: " + json.dumps({str(k): v for k, v in glft_val.items()}))
    log(f"GLFT gamma selected on val: {best_g}")

    # val arm table, so the val selection is read against the benchmarks rather than in a vacuum.
    # This touches no test row.
    c51_va_acts, c51_va_cpm, c51_va_posts = evaluate(agent, va_eps, mu, sd, best["risk"])
    glft_va_acts, _, _ = glft.run(va_eps, best_g)
    val_arms = {"always": arm_always(va), "detgate": arm_detgate(va), "room4": arm_room4(va),
                "logit": arm_logit(tr, va),
                "glft": labels_from(va, va_eps, glft_va_acts),
                "c51": labels_from(va, va_eps, c51_va_acts),
                "oracle_entry": np.where(va.kind == "entry", va.label, "HOLD")}
    val_table = summarize(va, val_arms)
    log("\nVAL arm table (selection context, no test rows):\n" + val_table.to_string())
    val_nets = {k: per_market_net(va, v) for k, v in val_arms.items()}
    val_pairs = {f"{k} - room4": paired(val_nets[k], val_nets["room4"])
                 for k in ("c51", "glft", "logit")}
    val_pairs["c51 - glft"] = paired(val_nets["c51"], val_nets["glft"])
    for k, v in val_pairs.items():
        log(f"  val {k:>14}  {v['diff']:+9.3f} +- {v['se']:.3f}  t = {v['t']}")

    report = {"dataset": str(root), "mark": man["mark"], "iters": a.iters, "seed": a.seed,
              "frozen": {"GAMMA_RL": GAMMA_RL, "N_STEP": N_STEP, "LR": LR, "BATCH": BATCH,
                         "HIDDEN": HIDDEN, "N_ATOMS": N_ATOMS, "RISK_GRID": list(RISK_GRID),
                         "support": [agent.vmin, agent.vmax]},
              "val_selection": {"risk": best["risk"], "val_c_per_market": best["val_c"],
                                "iter": best["iter"], "glft_gamma": best_g},
              "scenario_bandit": ex_report, "cell_bounds": bounds,
              "val_history": hist, "glft_val_sweep": {str(k): v for k, v in glft_val.items()},
              "val_table": json.loads(val_table.to_json(orient="index")),
              "val_paired": val_pairs, "val_c51_posts": c51_va_posts,
              "test_scored": bool(a.score_test)}

    if a.score_test:
        te = d[d.fold == "test"].reset_index(drop=True)
        te_eps = episodes(te)
        log(f"\nTEST fold: {len(te):,} rows / {te.ticker.nunique()} markets")
        c51_acts, c51_cpm, c51_posts = evaluate(agent, te_eps, mu, sd, best["risk"])
        glft_acts, glft_total, glft_posts = glft.run(te_eps, best_g)
        arms = {
            "always": arm_always(te),
            "detgate": arm_detgate(te),
            "room4": arm_room4(te),
            "logit": arm_logit(tr, te),
            "glft": labels_from(te, te_eps, glft_acts),
            "c51": labels_from(te, te_eps, c51_acts),
            "oracle_entry": np.where(te.kind == "entry", te.label, "HOLD"),
        }
        table = summarize(te, arms)
        log("\n" + table.to_string())
        nets = {k: per_market_net(te, v) for k, v in arms.items()}
        pairs = {f"{k} - room4": paired(nets[k], nets["room4"])
                 for k in ("c51", "glft", "logit", "detgate", "always")}
        pairs["c51 - glft"] = paired(nets["c51"], nets["glft"])
        pairs["c51 - logit"] = paired(nets["c51"], nets["logit"])
        log("\npaired per-market differences (the headline):")
        for k, v in pairs.items():
            log(f"  {k:>18}  {v['diff']:+9.3f} +- {v['se']:.3f}  t = {v['t']}  "
                f"n = {v['markets']}")
        offered = int((te.kind == "entry").sum())
        log(f"\nfills posted on test: c51 {c51_posts:,}  glft {glft_posts:,}  "
            f"room4 {int(table.loc['room4', 'entries_taken']):,}  "
            f"always {int(table.loc['always', 'entries_taken']):,}  "
            f"(of {offered:,} offered; c51 takes {100 * c51_posts / max(offered, 1):.1f}%)")

        null = shift_null(te, te_eps, c51_acts, draws=20, seed=a.seed)
        log("\nturnover-matched circular-shift null for c51: "
            f"{null['c_per_market_mean']:+.2f} +- {null['c_per_market_sd']:.2f} c/market "
            f"(hi95 {null['hi95']:+.2f}), {null['posts_mean']:,} posts vs {c51_posts:,} real")
        overlap = width_overlap(te, arms["c51"], arms["room4"])
        log("c51 vs room4 fill-set decomposition: " + json.dumps(overlap))

        report["test"] = {"table": json.loads(table.to_json(orient="index")), "paired": pairs,
                          "posts": {"c51": c51_posts, "glft": glft_posts, "offered": offered},
                          "shift_null_c51": null, "width_overlap_c51_vs_room4": overlap,
                          "c51_c_per_market": round(c51_cpm, 3),
                          "glft_c_per_market": round(glft_total / max(len(te_eps), 1), 3)}
        g = report["gates"] = {
            "G1_beats_room4_t2": bool(pairs["c51 - room4"]["t"] is not None
                                      and pairs["c51 - room4"]["t"] >= 2.0
                                      and pairs["c51 - room4"]["diff"] > 0),
            "G2_magnitude_20c": bool(pairs["c51 - room4"]["diff"] >= 20.0),
            "G3_beats_glft": bool(pairs["c51 - glft"]["diff"] > 0),
            "G4_not_room4_in_disguise": bool(overlap["only_learned"]["rows"] > 0
                                             and overlap["jaccard"] < 0.9),
            "G5_breadth": bool(pairs["c51 - room4"]["share_pos"] >= 0.55
                               and pairs["c51 - room4"]["drop_best5"] > 0),
            "G6_posts_at_least_2pct": bool(c51_posts >= 0.02 * offered),
            "G7_above_turnover_null": bool(c51_cpm > null["hi95"]),
        }
        log("\ngates: " + json.dumps(g, indent=2))
        log(f"VERDICT: {'PASS' if all(g.values()) else 'FAIL'} "
            f"({sum(g.values())}/{len(g)} gates)")
        log("\n⚠ Instrument: these are `mk5s` cents under the improved-quote fill model, which "
            "overstates adverse selection 5.17x against 8,038,551 real Kalshi prints. The LEVEL "
            "of every arm here is inflated by roughly that factor; only the PAIRED differences "
            "between arms on identical rows are the quantity this run can speak to.")

    (out / "report.json").write_text(json.dumps(report, indent=2, default=str))
    log(f"\nwrote {out / 'report.json'}")
    lf.close()


if __name__ == "__main__":
    main()
