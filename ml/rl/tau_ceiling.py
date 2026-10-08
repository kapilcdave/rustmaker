"""The ceiling of this whole branch, in one number, measured on VAL only.

The v2 scored run failed at paired −5.498 ± 1.252 against `room4`, and the diagnostics say why:
the critic's Q difference is **correctly ordered** across spread bands (monotone, seed sd ~0.09 c)
but its ZERO is shifted by about −0.5 c, so it only posts at ~11 ticks when the true edge turns
positive at 4. That is a calibration bias, not an information failure — and a constant bias is
fixed by one threshold.

So the question that decides the branch is no longer "can RL beat the width gate" but "how much is
the critic's ranking worth ONCE the threshold is right". This script answers it:

    post iff  (Q(POST) - Q(SKIP)) > tau,   tau swept on val

and scores every tau in the harness, paired per market against `room4`. If the best tau only ties
`room4`, the learned ranking is worth nothing on top of one comparison and no further architecture
helps. If it clears the bar, the branch earns a new pre-registration.

⚠ VAL ONLY, deliberately. The v2 test fold has been read once under
`PREREG_rlmm_v2_20261007.md` and its single scored evaluation is spent. A tau fitted after seeing
that score cannot be scored on the same fold and called a pass — per that prereg's own amendment
rule it can only open a new prereg. What this script produces is a val-fold ESTIMATE of the
ceiling, which is enough to decide whether a new prereg is worth opening at all.

    .venv/bin/python ml/rl/tau_ceiling.py ml/data/mk5s-v4 ml/runs/rlmm-v2
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import torch

ML = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ML))

from policy_eval import per_market_net, summarize  # noqa: E402
from rl.arms import arm_room4  # noqa: E402
from rl.env import POST, episodes, labels_from, rollout, state_at  # noqa: E402
from rl.shaped import ShapedAgent  # noqa: E402
from rl.train import (BATCH, BUFFER, GAMMA_RL, HIDDEN, LR, N_STEP,  # noqa: E402
                      TARGET_SYNC, paired)

TAU_GRID = (-1.2, -1.0, -0.8, -0.6, -0.5, -0.4, -0.3, -0.2, -0.1, 0.0, 0.2)


def load_agents(run: Path, n_feat: int = 40):
    ck = torch.load(run / "c51v2.pt", map_location="cpu", weights_only=False)
    ags = []
    for st in ck["states"]:
        a = ShapedAgent(n_feat, 0, HIDDEN, LR, 1000, GAMMA_RL, N_STEP, BATCH,
                        TARGET_SYNC, 10 ** 9, n_atoms=201)
        a.net.load_state_dict(st)
        a.set_support(ck["vmin"], ck["vmax"])
        a.net.eval()
        ags.append(a)
    return ags, ck["mu"], ck["sd"]


def tau_decider(ags, mu, sd, ep, tau: float):
    """Post iff the ensemble's Q advantage for posting clears tau."""
    cap = ep.capture_c

    def decide(state, _pos, t):
        x = (state - mu) / sd
        q = np.mean([a.q_true(x, float(cap[t]), 1.0)[0] for a in ags], axis=0)
        return bool((q[POST] - q[0]) > tau)
    return decide


def main() -> None:
    root, run = Path(sys.argv[1]), Path(sys.argv[2])
    d = pd.read_parquet(root / "rows.parquet")
    va = d[d.fold == "val"].reset_index(drop=True)
    eps = episodes(va)
    ags, mu, sd = load_agents(run)
    r4 = arm_room4(va)
    r4_net = per_market_net(va, r4)
    print(f"VAL fold: {va.ticker.nunique()} markets, {len(va):,} rows")
    print(f"room4: {r4_net.mean():+.3f} c/market, {int((r4 != 'HOLD').sum()):,} fills\n")
    print(f"{'tau':>6} {'c/market':>10} {'vs room4':>10} {'se':>7} {'t':>7} {'share+':>7} "
          f"{'fills':>8} {'c/ct':>8}")
    print("-" * 70)
    best = None
    for tau in TAU_GRID:
        acts = [rollout(ep, tau_decider(ags, mu, sd, ep, tau))[0] for ep in eps]
        lab = labels_from(va, eps, acts)
        net = per_market_net(va, lab)
        p = paired(net, r4_net)
        n_f = int(sum(a.sum() for a in acts))
        cct = net.sum() / max(n_f, 1)
        print(f"{tau:>6.2f} {net.mean():>+10.3f} {p['diff']:>+10.3f} {p['se']:>7.3f} "
              f"{str(p['t']):>7} {p['share_pos']:>7.3f} {n_f:>8,} {cct:>+8.3f}")
        if best is None or p["diff"] > best[1]["diff"]:
            best = (tau, p, net, lab, n_f)
    tau, p, net, lab, n_f = best
    print(f"\nbest tau on val = {tau:+.2f}: paired vs room4 {p['diff']:+.3f} +- {p['se']:.3f} "
          f"(t = {p['t']}), {n_f:,} fills vs room4's {int((r4 != 'HOLD').sum()):,}")
    print(summarize(va, {"room4": r4, f"c51v2_tau{tau}": lab}).to_string())
    print(f"\nG2 bar restated per contract is +0.112 c/ct over room4 "
          f"(= the v1 +20 c/market bar at 179 fills/market).")
    print(f"This measures the CEILING of the branch: the best a correctly-calibrated version of "
          f"this critic can do on the selection fold.")


if __name__ == "__main__":
    main()
