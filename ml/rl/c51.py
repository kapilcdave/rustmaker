"""Rainbow-style distributional DQN (C51) for the two-action quoting decision.

Components, and why each is here rather than "because Rainbow has it":

  C51 (categorical distribution over returns)   The point of the whole exercise. A maker's return
      per fill is strongly bimodal -- capture the spread, or get run over -- so the mean is a bad
      summary and a mean-only critic cannot express "this fill is usually fine and occasionally
      terrible". It also gives risk-sensitive action selection for free: the same network can be
      greedy in the mean or greedy in a CVaR of its own predicted distribution, with no retraining.
  Double DQN      The overestimation bias it fixes is worse than usual here: rewards are noisy
      cents and the action set is tiny, so max-over-actions on a noisy critic systematically
      favours POST, which is exactly the failure mode (a gate that never gates).
  Dueling heads   Most of the variance in this state space is "is this market quotable at all",
      which is a state value, not an action advantage.
  Noisy nets      Exploration without an epsilon schedule. An epsilon-greedy quoter perturbs the
      INVENTORY path, which is state, so epsilon noise does not just waste a step here -- it
      corrupts the following states. State-dependent parametric noise is the better fit.
  Prioritised replay   The informative fills (large |reward|) are a small minority of rows.
  n-step returns   The only sequential coupling in this environment is through inventory and the
      position cap, and it is short-ranged, so n is small (3) by design and not a tuned knob.

Shapes: state (B, F) -> logits (B, A, N_ATOMS) -> softmax over the last axis.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

N_ACTIONS = 2
N_ATOMS = 51


class NoisyLinear(nn.Module):
    """Factorised Gaussian noisy layer (Fortunato et al. 2017)."""

    def __init__(self, nin: int, nout: int, sigma0: float = 0.5):
        super().__init__()
        self.nin, self.nout, self.sigma0 = nin, nout, sigma0
        self.w_mu = nn.Parameter(torch.empty(nout, nin))
        self.w_sigma = nn.Parameter(torch.empty(nout, nin))
        self.b_mu = nn.Parameter(torch.empty(nout))
        self.b_sigma = nn.Parameter(torch.empty(nout))
        self.register_buffer("w_eps", torch.zeros(nout, nin))
        self.register_buffer("b_eps", torch.zeros(nout))
        self.reset_parameters()
        self.reset_noise()

    def reset_parameters(self) -> None:
        bound = 1.0 / math.sqrt(self.nin)
        self.w_mu.data.uniform_(-bound, bound)
        self.b_mu.data.uniform_(-bound, bound)
        self.w_sigma.data.fill_(self.sigma0 / math.sqrt(self.nin))
        self.b_sigma.data.fill_(self.sigma0 / math.sqrt(self.nin))

    @staticmethod
    def _f(x: torch.Tensor) -> torch.Tensor:
        return x.sign() * x.abs().sqrt()

    def reset_noise(self) -> None:
        ei = self._f(torch.randn(self.nin, device=self.w_mu.device))
        eo = self._f(torch.randn(self.nout, device=self.w_mu.device))
        self.w_eps.copy_(eo.outer(ei))
        self.b_eps.copy_(eo)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        if self.training:
            w = self.w_mu + self.w_sigma * self.w_eps
            b = self.b_mu + self.b_sigma * self.b_eps
        else:
            w, b = self.w_mu, self.b_mu
        return F.linear(x, w, b)


class C51Net(nn.Module):
    """Dueling categorical head over a shared MLP trunk."""

    def __init__(self, n_feat: int, hidden: int = 128, n_atoms: int = N_ATOMS,
                 n_actions: int = N_ACTIONS, noisy: bool = True):
        super().__init__()
        self.n_atoms, self.n_actions = n_atoms, n_actions
        lin = NoisyLinear if noisy else nn.Linear
        self.trunk = nn.Sequential(
            nn.Linear(n_feat, hidden), nn.ReLU(),
            nn.Linear(hidden, hidden), nn.ReLU(),
        )
        self.val = nn.Sequential(lin(hidden, hidden // 2), nn.ReLU(),
                                 lin(hidden // 2, n_atoms))
        self.adv = nn.Sequential(lin(hidden, hidden // 2), nn.ReLU(),
                                 lin(hidden // 2, n_actions * n_atoms))

    def reset_noise(self) -> None:
        for m in self.modules():
            if isinstance(m, NoisyLinear):
                m.reset_noise()

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """Returns log-probabilities, shape (B, n_actions, n_atoms)."""
        h = self.trunk(x)
        v = self.val(h).view(-1, 1, self.n_atoms)
        a = self.adv(h).view(-1, self.n_actions, self.n_atoms)
        logits = v + a - a.mean(dim=1, keepdim=True)
        return F.log_softmax(logits, dim=-1)


def q_from_dist(logp: torch.Tensor, support: torch.Tensor, risk: float = 1.0) -> torch.Tensor:
    """Scalarise the predicted return distribution.

    `risk` is the CVaR level: 1.0 is the plain mean (risk neutral), 0.1 scores each action by the
    mean of the worst 10% of its own predicted returns. Distributional RL's real payoff in a
    market-making context is that this is a free choice at decision time -- the critic is trained
    once and the risk appetite is a val-fold selection, not a retrain.
    """
    p = logp.exp()
    if risk >= 1.0:
        return (p * support).sum(-1)
    c = p.cumsum(-1)
    # weight atoms up to the risk quantile, pro-rating the atom the quantile falls inside
    w = torch.clamp((risk - (c - p)) / p.clamp_min(1e-12), 0.0, 1.0) * p
    return (w * support).sum(-1) / w.sum(-1).clamp_min(1e-12)


def project(next_logp: torch.Tensor, rewards: torch.Tensor, done: torch.Tensor,
            disc: torch.Tensor, support: torch.Tensor) -> torch.Tensor:
    """The categorical distributional Bellman projection (Bellemare et al. 2017, alg. 1)."""
    B, n_atoms = next_logp.shape
    vmin, vmax = support[0], support[-1]
    dz = (vmax - vmin) / (n_atoms - 1)
    tz = (rewards.unsqueeze(1) + disc.unsqueeze(1) * (1.0 - done.unsqueeze(1))
          * support.unsqueeze(0)).clamp(vmin, vmax)
    b = (tz - vmin) / dz
    lo = b.floor().long().clamp(0, n_atoms - 1)
    hi = b.ceil().long().clamp(0, n_atoms - 1)
    p = next_logp.exp()
    out = torch.zeros(B, n_atoms, device=p.device, dtype=p.dtype)
    # when b lands exactly on an atom, lo == hi and the two weights below are 0 and 0; give that
    # atom the full mass instead of dropping it
    eq = (lo == hi).to(p.dtype)
    out.scatter_add_(1, lo, p * ((hi.to(p.dtype) - b) + eq))
    out.scatter_add_(1, hi, p * (b - lo.to(p.dtype)) * (1.0 - eq))
    return out


@dataclass
class Replay:
    """Proportional prioritised replay over a flat numpy store."""
    capacity: int
    n_feat: int
    alpha: float = 0.6
    beta: float = 0.4
    eps: float = 1e-3
    size: int = field(default=0, init=False)
    pos: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        c, f = self.capacity, self.n_feat
        self.s = np.zeros((c, f), dtype=np.float32)
        self.s2 = np.zeros((c, f), dtype=np.float32)
        self.a = np.zeros(c, dtype=np.int64)
        self.r = np.zeros(c, dtype=np.float32)
        self.d = np.zeros(c, dtype=np.float32)
        self.g = np.zeros(c, dtype=np.float32)
        self.prio = np.zeros(c, dtype=np.float64)

    def add_batch(self, s, a, r, s2, d, g) -> None:
        n = len(a)
        if n == 0:
            return
        idx = (np.arange(n) + self.pos) % self.capacity
        self.s[idx], self.s2[idx] = s, s2
        self.a[idx], self.r[idx], self.d[idx], self.g[idx] = a, r, d, g
        top = self.prio[:self.size].max() if self.size else 1.0
        self.prio[idx] = max(top, 1.0)
        self.pos = int((self.pos + n) % self.capacity)
        self.size = int(min(self.size + n, self.capacity))

    def sample(self, batch: int, rng: np.random.Generator):
        p = self.prio[:self.size] ** self.alpha
        p = p / p.sum()
        idx = rng.choice(self.size, size=min(batch, self.size), replace=True, p=p)
        w = (self.size * p[idx]) ** (-self.beta)
        w = w / w.max()
        return idx, w.astype(np.float32)

    def update(self, idx: np.ndarray, err: np.ndarray) -> None:
        self.prio[idx] = np.abs(err) + self.eps


def n_step(states: np.ndarray, acts: np.ndarray, rewards: np.ndarray, n: int, gamma: float):
    """Fold an episode into n-step transitions.

    The terminal state of a market is absorbing with value 0 (the market settles; there is no
    continuation), so `done` is set on the last n transitions rather than only the last one.
    """
    T = len(acts)
    if T == 0:
        return (np.zeros((0, states.shape[1]), np.float32),) * 2 + (
            np.zeros(0, np.int64), np.zeros(0, np.float32), np.zeros(0, np.float32),
            np.zeros(0, np.float32))
    disc = gamma ** np.arange(n)
    R = np.zeros(T, dtype=np.float32)
    for k in range(n):
        R[: T - k] += (disc[k] * rewards[k:]).astype(np.float32)
    nxt = np.empty_like(states)
    nxt[: max(T - n, 0)] = states[n:]
    done = np.zeros(T, dtype=np.float32)
    if T - n < 0:
        done[:] = 1.0
        nxt[:] = states[-1]
    else:
        done[T - n:] = 1.0
        nxt[T - n:] = states[-1]
    g = np.full(T, gamma ** n, dtype=np.float32)
    return states, nxt, acts.astype(np.int64), R, done, g


__all__ = ["C51Net", "NoisyLinear", "Replay", "project", "q_from_dist", "n_step",
           "N_ACTIONS", "N_ATOMS"]
