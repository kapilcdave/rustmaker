"""Tests for the RL market maker. Runnable without pytest:

    .venv/bin/python ml/rl/test_rl.py            # units only
    .venv/bin/python ml/rl/test_rl.py ml/data/mk5s-v3   # plus the dataset-consistency tests

The dataset tests are the ones that matter most: they check that the environment reconstructs the
position-dependent features the same way `features._derive` built them, and that the label mapping
agrees with `arm_always` fill for fill. If either drifts, the RL arm stops being comparable to the
benchmarks and the whole comparison is void.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ML = Path(__file__).resolve().parent.parent
if str(ML) not in sys.path:
    sys.path.insert(0, str(ML))

import torch  # noqa: E402

from rl.bandit import Exp3, N_CELLS, assign_cells, fit_cells  # noqa: E402
from rl.bocpd import HAZARD, bocpd_path, flow_bits  # noqa: E402
from rl.c51 import N_ATOMS, project, q_from_dist  # noqa: E402
from rl.glft import K_C, required_half_c, spread_term_c  # noqa: E402

DATA = None


# ---- BOCPD ----------------------------------------------------------------------------------
def test_bocpd_is_causal():
    """State k must not depend on observations at or after k."""
    rng = np.random.default_rng(1)
    x = rng.integers(0, 2, 80).astype(np.int8)
    y = x.copy()
    y[40:] = 1 - y[40:]
    a, b = bocpd_path(x), bocpd_path(y)
    for key in a:
        assert np.allclose(a[key][:41], b[key][:41]), key


def test_bocpd_detects_a_regime_flip():
    o = bocpd_path(np.array([1] * 60 + [0] * 60, dtype=np.int8))
    # the flip is at index 60 (0-based), so the posterior that first sees it is at k=61
    assert o["cp_prob"][61] > 10 * o["cp_prob"][59]
    assert o["cp_recent"][63] > 0.5
    assert o["bias_mean"][59] > 0.9 and o["bias_mean"][70] < 0.2


def test_bocpd_cp_prob_is_not_the_hazard_constant():
    """The degeneracy this module exists to avoid: under the textbook recursion P(r=0) == H."""
    o = bocpd_path(np.array([1] * 60 + [0] * 60, dtype=np.int8))
    assert o["cp_prob"].std() > 0.01
    assert abs(o["cp_prob"][61] - HAZARD) > 0.05


def test_bocpd_is_a_distribution():
    o = bocpd_path(np.array([1, 0, 1, 1, 0, 0, 1], dtype=np.int8))
    for k in o:
        assert np.all(np.isfinite(o[k])), k
    assert np.all((o["bias_mean"] >= 0) & (o["bias_mean"] <= 1))
    assert np.all((o["cp_prob"] >= 0) & (o["cp_prob"] <= 1))
    assert np.all(o["cp_recent"] >= o["cp_prob"] - 1e-12)


def test_flow_bits_convention():
    assert list(flow_bits(np.array(["yes", "no", "yes"]))) == [1, 0, 1]


# ---- C51 ------------------------------------------------------------------------------------
def test_projection_conserves_probability():
    torch.manual_seed(0)
    support = torch.linspace(-8.0, 8.0, N_ATOMS)
    logp = torch.log_softmax(torch.randn(16, N_ATOMS), -1)
    for r in (-20.0, -1.0, 0.0, 0.37, 1.0, 20.0):     # includes targets outside the support
        for done in (0.0, 1.0):
            m = project(logp, torch.full((16,), r), torch.full((16,), done),
                        torch.full((16,), 0.97), support)
            assert torch.allclose(m.sum(-1), torch.ones(16), atol=1e-5), (r, done)
            assert (m >= 0).all()


def test_projection_on_exact_atoms_keeps_full_mass():
    """b landing exactly on an atom is the case a naive (hi-b)/(b-lo) split zeroes out."""
    support = torch.linspace(-1.0, 1.0, 3)            # atoms at -1, 0, +1, dz = 1
    logp = torch.log(torch.tensor([[0.0, 1.0, 0.0]]).clamp_min(1e-12))
    m = project(logp, torch.zeros(1), torch.zeros(1), torch.ones(1), support)
    assert torch.allclose(m.sum(), torch.tensor(1.0), atol=1e-5)
    assert m[0, 1] > 0.99                              # 0 + 1*0 = 0 -> the middle atom


def test_n_step_uses_the_RECEIVED_reward_not_the_offered_one():
    """Regression: crediting `net_c` on SKIPPED steps makes the bootstrap target the return of a
    policy that posts everywhere, i.e. `always` -- an arm already measured at -33.4 c/market."""
    from rl.c51 import n_step
    from rl.train import GAMMA_RL, N_STEP, Agent
    acts = np.array([1, 0, 1, 0, 0, 1], dtype=np.int64)
    net_c = np.array([10.0, 100.0, 20.0, 100.0, 100.0, 30.0], dtype=np.float32)
    states = np.zeros((6, 3), dtype=np.float32)
    ag = Agent.__new__(Agent)          # no network needed; only the reward path is under test
    from rl.c51 import Replay
    ag.buf = Replay(64, 3)
    ag.push(states, acts, net_c, np.zeros(3, np.float32), np.ones(3, np.float32))
    r = ag.buf.r[:6]
    g = GAMMA_RL
    # step 0: 10 (post) + g*0 (skip) + g^2*20 (post)
    assert abs(r[0] - (10.0 + g * 0.0 + g ** 2 * 20.0)) < 1e-4, r[0]
    # step 1 was a SKIP: its own reward is 0, and the 100 offered there must never appear
    assert abs(r[1] - (0.0 + g * 20.0 + g ** 2 * 0.0)) < 1e-4, r[1]
    assert not np.any(np.isclose(r, 100.0)), "an offered-but-skipped reward leaked into the target"
    # and the plain n_step helper is linear in the reward stream it is handed
    _, _, _, R, _, _ = n_step(states, acts, net_c * (acts == 1), N_STEP, GAMMA_RL)
    assert np.allclose(R, r, atol=1e-4)


def test_capture_identity_holds_on_the_dataset():
    """net_c == capture_c - adverse_c must be an identity, not an approximation."""
    if not DATA:
        return
    from rl.env import episodes
    d = _load()
    for ep in episodes(d)[:40]:
        assert np.allclose(ep.net_c, ep.capture_c - ep.adverse_c, atol=1e-5)
        # and capture must be KNOWN at decision time: it is a function of state columns only
        assert np.allclose(ep.capture_c, ep.s * (ep.px_c - ep.mid_c), atol=1e-6)


def test_shaping_is_exact():
    """Q~ = Q - c must reproduce the unshaped Bellman target.

    The failure this guards is subtle and silent: shaping the reward by `-c` and forgetting to add
    `c(s',a')` back inside the bootstrap changes the optimal policy rather than erroring. Checked
    numerically on a scalar two-step chain, both branches computed by hand.
    """
    g, r0, cap0, cap1 = 0.99, 2.0, 1.5, 0.75
    # unshaped: Q(s0,POST) = r0 + g * max(Q(s1,.)); take Q(s1,POST)=cap1+z1, Q(s1,SKIP)=z_skip
    z1, z_skip = 0.30, -0.10
    q_true = r0 + g * max(cap1 + z1, z_skip)
    # shaped: Q~(s0,POST) = (r0 - cap0) + g * max(Q~(s1,a) + c(s1,a)); add cap0 back at the end
    q_shaped = (r0 - cap0) + g * max(z1 + cap1, z_skip + 0.0)
    assert abs((q_shaped + cap0) - q_true) < 1e-12, (q_shaped + cap0, q_true)


def test_shaped_agent_pushes_minus_adverse_and_tracks_bootstrap_capture():
    from rl.env import POST
    from rl.shaped import ShapedAgent
    ag = ShapedAgent(n_feat=3, seed=0, hidden=16, lr=1e-3, buffer=64, gamma_rl=1.0,
                     n_step_k=1, batch=8, target_sync=10, warmup=10**9)
    acts = np.array([1, 0, 1], dtype=np.int64)
    net_c = np.array([5.0, 9.0, -2.0], dtype=np.float32)
    capture = np.array([2.0, 3.0, 4.0], dtype=np.float32)
    states = np.zeros((3, 3), dtype=np.float32)
    ag.push(states, acts, net_c, capture, np.zeros(3, np.float32), np.ones(3, np.float32))
    # shaped reward is -(adverse) on POST steps and 0 on the SKIP step
    assert np.allclose(ag.buf.r[:3], [5.0 - 2.0, 0.0, -2.0 - 4.0], atol=1e-5), ag.buf.r[:3]
    # bootstrap capture is the capture at s' = state t+1 (terminal state for the last)
    assert np.allclose(ag.cap_next[:3], [3.0, 4.0, 4.0], atol=1e-5), ag.cap_next[:3]
    assert ag.buf.a[0] == POST and ag.buf.a[1] == 0


def test_q_from_dist_mean_and_cvar():
    support = torch.tensor([-4.0, 0.0, 6.0])
    logp = torch.log(torch.tensor([[[0.25, 0.25, 0.5]]]))
    mean = q_from_dist(logp, support, 1.0)
    assert torch.allclose(mean, torch.tensor([[2.0]]), atol=1e-5)
    cvar = q_from_dist(logp, support, 0.25)            # exactly the worst atom
    assert torch.allclose(cvar, torch.tensor([[-4.0]]), atol=1e-4)
    assert q_from_dist(logp, support, 0.5).item() < mean.item()


# ---- GLFT -----------------------------------------------------------------------------------
def test_glft_spread_term_degenerates_to_the_as_half_spread():
    from features import AS_HALF_C
    assert abs(spread_term_c(1e-6) - AS_HALF_C) < 1e-3
    # and it is decreasing in risk aversion: a more risk-averse maker quotes TIGHTER in GLFT,
    # because the spread term is (1/g) ln(1 + g/k)
    assert spread_term_c(1e-2) < spread_term_c(1e-4) < spread_term_c(1e-6)


def test_glft_skew_signs():
    """A long inventory must make the maker demand MORE to buy again and LESS to sell."""
    kw = dict(sigma_c=1.1, A=5.0, gamma=1e-2, k_c=K_C)
    bid_flat = required_half_c(0.0, -1.0, **kw)
    bid_long = required_half_c(+5.0, -1.0, **kw)
    ask_long = required_half_c(+5.0, +1.0, **kw)
    assert bid_long > bid_flat > ask_long
    # and it is antisymmetric in inventory
    assert abs((bid_long - bid_flat) - (bid_flat - required_half_c(-5.0, -1.0, **kw))) < 1e-9


# ---- scenario bandit ------------------------------------------------------------------------
def test_cells_are_frozen_terciles():
    rng = np.random.default_rng(3)
    stress = rng.random((300, 2))
    b = fit_cells(stress)
    c = assign_cells(stress, b)
    assert c.min() >= 0 and c.max() < N_CELLS
    counts = np.bincount(c, minlength=N_CELLS)
    assert counts.min() > 10                      # roughly balanced on uniform input
    # a point outside the training range still lands in an end cell, not out of bounds
    assert 0 <= assign_cells(np.array([[-9.0, 9.0]]), b)[0] < N_CELLS


def test_exp3_drifts_toward_the_losing_cell():
    """The adversary must upweight the cell where the policy does worst."""
    ex = Exp3(n_cells=3, gamma=0.2)
    avail = np.ones(3)
    rng = np.random.default_rng(0)
    losses = {0: +10.0, 1: 0.0, 2: -10.0}         # cell 2 is where the policy bleeds
    for _ in range(400):
        cell, p = ex.draw(avail, rng)
        ex.update(cell, p, losses[cell], scale=10.0)
    w = ex.w / ex.w.sum()
    assert w[2] > w[1] > w[0], w


# ---- dataset consistency (needs a built dataset) --------------------------------------------
def _load():
    import pandas as pd
    root = Path(DATA)
    d = pd.read_parquet(root / "rows.parquet")
    return d[d.fold == "val"].reset_index(drop=True)


def test_env_reconstructs_the_position_features():
    """`env._fill_position` must agree with `features._derive` on the dataset's own position."""
    from rl.env import IDX, episodes, state_at
    d = _load()
    eps = episodes(d)
    checked = 0
    for ep in eps[:25]:
        g = d.loc[ep.row_index]
        for t in range(0, len(ep), 37):
            pos = float(g.pos_before.iloc[t])
            st = state_at(ep, t, pos)
            for col in ("pos_before", "as_skew_c", "as_resv_c", "as_edge_c", "qxi_lean"):
                want = float(g[col].iloc[t])
                got = float(st[IDX[col]])
                assert abs(got - want) < 2e-3, (ep.ticker, t, col, got, want)
            checked += 1
    assert checked > 50


def test_queue_term_collapses_and_says_so():
    """q_ahead is identically 0 in this instrument, so `qxi` must equal s*(2*imb-1) exactly."""
    d = _load()
    assert float(d.q_ahead_ct.abs().max()) == 0.0
    want = d.s * (2.0 * d.imb - 1.0)
    assert float((d.qxi - want).abs().max()) < 1e-9
    # the queue-adjusted variant differs only by our own clip in the denominator
    assert float(np.corrcoef(d.qxi, d.qxi_q)[0, 1]) > 0.999


def test_always_posting_reproduces_arm_always():
    """If the agent posts at every legal chance, its labels must match `arm_always` except where
    the inventory cap bound -- and that difference must be in the direction of FEWER fills."""
    from policy_eval import arm_always, per_market_net
    from rl.env import episodes, labels_from, rollout
    d = _load()
    eps = episodes(d)
    acts = [rollout(e, lambda *_: True)[0] for e in eps]
    mine = labels_from(d, eps, acts)
    theirs = arm_always(d)
    took_mine = (mine != "HOLD").sum()
    took_theirs = (theirs != "HOLD").sum()
    assert took_mine <= took_theirs
    assert took_mine >= 0.95 * took_theirs, (took_mine, took_theirs)
    # where both post, the side must be identical -- a flipped side would silently invert P&L
    both = (mine != "HOLD") & (theirs != "HOLD")
    assert (mine[both] == theirs[both]).all()
    # and the env's own P&L must match the harness's accounting for the same labels
    env_pnl = sum(float((e.net_c * (a == 1)).sum()) for e, a in zip(eps, acts))
    assert abs(env_pnl - per_market_net(d, mine).sum()) < 1.0


def test_cap_is_enforced():
    from features import POS_CAP
    from rl.env import episodes, rollout
    d = _load()
    for ep in episodes(d)[:40]:
        _, _, pos, _ = rollout(ep, lambda *_: True)
        assert np.abs(pos).max() <= POS_CAP


NEEDS_DATA = [test_env_reconstructs_the_position_features, test_queue_term_collapses_and_says_so,
              test_always_posting_reproduces_arm_always, test_cap_is_enforced,
              test_capture_identity_holds_on_the_dataset]
_ND = {f.__name__ for f in NEEDS_DATA}
UNITS = [v for k, v in sorted(globals().items())
         if k.startswith("test_") and k not in _ND]


if __name__ == "__main__":
    DATA = sys.argv[1] if len(sys.argv) > 1 else None
    tests = list(UNITS) + (NEEDS_DATA if DATA else [])
    bad = 0
    for fn in tests:
        try:
            fn()
            print(f"  ok    {fn.__name__}")
        except AssertionError as e:
            bad += 1
            print(f"  FAIL  {fn.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            bad += 1
            print(f"  ERROR {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - bad}/{len(tests)} passed")
    if not DATA:
        print("dataset tests skipped: pass a dataset dir to run them")
    sys.exit(1 if bad else 0)
