"""EXP3 scenario bandit: an adversarial curriculum over regime cells.

The failure mode a plain DQN market maker has in the literature is a drawdown under persistent
directional imbalance -- it trains on whatever mix of regimes the tape happened to contain, so the
rare persistent-direction stretches are a small fraction of the gradient. The fix used here is to
put an adversary in charge of the training distribution: EXP3 over a frozen partition of training
markets into regime cells, rewarded for finding cells where the CURRENT policy does badly. The
sampler therefore drifts toward low-return regimes and the policy is pushed to a
distributionally-robust rather than an average-case optimum.

The two stress axes are the ones that actually break an inventory controller, and both are read
off the BOCPD posterior that is already in the state:

  persistence   mean |2 E[theta] - 1| over the market: how one-sided taker arrivals were. High =
                "correlated direction" stress, the regime a symmetric quoter bleeds in.
  switching     mean P(change point) over the market: how often the bias regime flipped. High =
                "random persistence" stress, where a trend follower and a mean reverter both lose.

⚠ Two disciplines this requires, both of which are easy to get wrong and would invalidate the
result:
  1. The cell boundaries are terciles of the TRAIN fold only, computed once and written to the
     report. Val and test markets are assigned by those frozen boundaries.
  2. Selection and scoring happen on the UNWEIGHTED fold. The bandit is a training-distribution
     device; reporting a bandit-weighted score would be reporting a number from a distribution
     the venue does not serve.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

AXES = ("persistence", "switching")
N_TERCILE = 3
N_CELLS = N_TERCILE ** len(AXES)


def market_stress(eps) -> np.ndarray:
    """(n_markets, 2): the two stress coordinates per episode, from the BOCPD state."""
    from .env import IDX
    out = np.empty((len(eps), 2), dtype=np.float64)
    for i, ep in enumerate(eps):
        bias = ep.x[:, IDX["bias_mean"]]
        cp = ep.x[:, IDX["cp_prob"]]
        out[i, 0] = float(np.mean(np.abs(2.0 * bias - 1.0))) if len(bias) else 0.0
        out[i, 1] = float(np.mean(cp)) if len(cp) else 0.0
    return out


def fit_cells(stress: np.ndarray) -> dict:
    """Frozen tercile boundaries from the train fold."""
    return {ax: np.quantile(stress[:, j], [1 / 3, 2 / 3]).tolist()
            for j, ax in enumerate(AXES)}


def assign_cells(stress: np.ndarray, bounds: dict) -> np.ndarray:
    """Cell id in [0, N_CELLS) for each market, under frozen boundaries."""
    out = np.zeros(len(stress), dtype=np.int64)
    for j, ax in enumerate(AXES):
        out = out * N_TERCILE + np.searchsorted(np.asarray(bounds[ax]), stress[:, j])
    return out


@dataclass
class Exp3:
    """EXP3 over scenario cells, rewarded for LOW policy return (an adversary)."""
    n_cells: int = N_CELLS
    gamma: float = 0.15          # exploration floor; a frozen scale choice, not tuned on val
    w: np.ndarray = field(init=False)
    pulls: np.ndarray = field(init=False)
    last_reward: np.ndarray = field(init=False)

    def __post_init__(self) -> None:
        self.w = np.ones(self.n_cells, dtype=np.float64)
        self.pulls = np.zeros(self.n_cells, dtype=np.int64)
        self.last_reward = np.full(self.n_cells, np.nan)

    def probs(self, available: np.ndarray) -> np.ndarray:
        """Mixture of the exponential weights and the uniform floor, over non-empty cells only."""
        p = np.zeros(self.n_cells)
        w = self.w * available
        tot = w.sum()
        k = max(int(available.sum()), 1)
        p[available > 0] = self.gamma / k
        if tot > 0:
            p += (1.0 - self.gamma) * w / tot
        return p / p.sum()

    def draw(self, available: np.ndarray, rng: np.random.Generator) -> tuple[int, np.ndarray]:
        p = self.probs(available)
        return int(rng.choice(self.n_cells, p=p)), p

    def update(self, cell: int, p: np.ndarray, policy_return: float, scale: float) -> None:
        """`policy_return` is cents/market in that cell. The bandit's reward is its NEGATIVE,
        squashed into [0, 1] by a fixed scale, so the sampler climbs toward the cells the policy
        is currently worst in."""
        r = float(np.clip(0.5 - policy_return / (2.0 * max(scale, 1e-9)), 0.0, 1.0))
        self.last_reward[cell] = r
        self.pulls[cell] += 1
        est = r / max(p[cell], 1e-12)
        self.w[cell] *= np.exp(self.gamma * est / self.n_cells)
        m = self.w.max()
        if m > 1e100:                      # EXP3 weights grow without bound; rescale, no-op on p
            self.w /= m

    def report(self) -> dict:
        return {"pulls": self.pulls.tolist(),
                "weights": (self.w / self.w.sum()).round(4).tolist(),
                "last_adversary_reward": np.round(self.last_reward, 3).tolist()}


__all__ = ["Exp3", "market_stress", "fit_cells", "assign_cells", "AXES", "N_CELLS"]
