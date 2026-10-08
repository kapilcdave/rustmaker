"""The v2 agent: a capture-decomposed C51 critic, and an ensemble over seeds.

Three changes from `rl/train.py`'s agent, each aimed at the one thing the scored v1 run showed was
wrong. v1 was not short of architecture -- it was short of signal. The numbers that say so:

  * the edge it had to resolve in the cell it got wrong is **+0.069 c/ct** (the 15,267 `room4`
    fills it declined), against a per-fill reward whose 3-step return spans **[-98, +82] c**;
  * its C51 support therefore had **3.6 c of resolution per atom** -- 52x coarser than the signal;
  * val said `+10.775` and test said `-9.005`, a 19.8 c swing out of a best-of-120 selection.


## 1. Capture decomposition -- supply the known half of the reward analytically

`net_c = capture_c - adverse_c` where `capture_c = s*(px_c - mid_c)` is a DETERMINISTIC function of
columns already in the state, and `adverse_c` is the only random part. v1's critic spent its
capacity regressing a quantity whose dominant variation (capture spans ~0.5 to 20+ c across the
book) it could have been handed for free.

So define the shaped value `Q~(s,a) = Q(s,a) - c(s,a)` with `c(s,POST) = capture_c(s)`,
`c(s,SKIP) = 0`. Substituting into the Bellman equation:

    Q~(s,a) = [r(s,a) - c(s,a)] + gamma * E[ max_a' ( Q~(s',a') + c(s',a') ) ]

which is **exact, not an approximation** -- note the bootstrap adds `c(s',a')` back INSIDE the max,
so the greedy action is still chosen on true Q. What the network now learns is
`r - c = -adverse_c`: mean approximately zero, and with the capture's spread removed its dynamic
range collapses, which is what buys the atom resolution back. Getting this wrong in the obvious way
-- shaping the reward and *not* adding `c` back in the bootstrap -- would silently change the
optimal policy, so `test_rl.py::test_shaping_is_exact` checks the identity numerically.

## 2. Support from the SHAPED return distribution

Once the target is `-adverse` the 0.1%/99.9% range is far tighter than v1's 180 c, so the same 51
atoms resolve roughly an order of magnitude finer. This is also the principled answer to the
heteroscedasticity in this reward (`sigma_c` scores AUC 0.685 for "is this fill in the worst 5%"):
a categorical critic represents the whole conditional distribution natively, so it handles
non-constant variance for free -- PROVIDED the atoms are fine enough to express it. v1's were not.

## 3. Ensemble over seeds, and a smoothed checkpoint selection

v1's policy was the luckiest of 120 val evaluations on 84 markets and gave back 19.8 c out of
sample. Averaging Q across independently seeded critics reduces policy variance directly, and
selecting on a moving average of the val curve stops the single luckiest checkpoint from being the
one that ships.
"""
from __future__ import annotations

import numpy as np
import torch

from .c51 import C51Net, N_ATOMS, Replay, n_step, project, q_from_dist
from .env import POST


class ShapedAgent:
    """C51 critic over the shaped value Q~ = Q - capture."""

    def __init__(self, n_feat: int, seed: int, hidden: int, lr: float, buffer: int,
                 gamma_rl: float, n_step_k: int, batch: int, target_sync: int,
                 warmup: int, n_atoms: int = N_ATOMS, device: str = "cpu"):
        self.dev = torch.device(device)
        torch.manual_seed(seed)
        self.n_atoms = n_atoms
        self.net = C51Net(n_feat, hidden, n_atoms=n_atoms).to(self.dev)
        self.tgt = C51Net(n_feat, hidden, n_atoms=n_atoms).to(self.dev)
        self.tgt.load_state_dict(self.net.state_dict())
        self.opt = torch.optim.Adam(self.net.parameters(), lr=lr, eps=1.5e-4)
        # the buffer carries one extra column v1 did not need: the capture at the BOOTSTRAP state,
        # because the target has to add c(s',a') back inside the max
        self.buf = Replay(buffer, n_feat)
        self.cap_next = np.zeros(buffer, dtype=np.float32)
        self.rng = np.random.default_rng(seed)
        self.gamma_rl, self.n_step_k = gamma_rl, n_step_k
        self.batch, self.target_sync, self.warmup = batch, target_sync, warmup
        self.steps = 0
        self.set_support(-8.0, 8.0)

    def set_support(self, lo: float, hi: float) -> None:
        self.vmin, self.vmax = float(lo), float(hi)
        self.support = torch.linspace(self.vmin, self.vmax, self.n_atoms, device=self.dev)

    # -- acting -------------------------------------------------------------------------------
    def q_shaped(self, x: np.ndarray, risk: float) -> np.ndarray:
        """Q~ for both actions. Shape (B, 2)."""
        with torch.no_grad():
            t = torch.as_tensor(x, dtype=torch.float32, device=self.dev)
            if t.ndim == 1:
                t = t.unsqueeze(0)
            return q_from_dist(self.net(t), self.support, risk).cpu().numpy()

    def q_true(self, x: np.ndarray, capture_c: float, risk: float) -> np.ndarray:
        """True Q, by adding the analytic capture back onto the POST branch."""
        q = self.q_shaped(x, risk)
        q[:, POST] = q[:, POST] + capture_c
        return q

    # -- learning -----------------------------------------------------------------------------
    def push(self, states: np.ndarray, acts: np.ndarray, net_c: np.ndarray,
             capture: np.ndarray, mu, sd) -> None:
        """Store shaped n-step transitions.

        The reward stream handed to `n_step` is `(net_c - capture) * 1[POST]`, i.e. `-adverse` on
        the steps we posted and 0 on the steps we skipped (v1's bug was crediting `net_c` on
        skipped steps; keep the action mask).
        """
        if len(acts) == 0:
            return
        shaped = ((net_c - capture) * (acts == POST)).astype(np.float32)
        s, s2, a, r, d, g = n_step((states - mu) / sd, acts, shaped,
                                   self.n_step_k, self.gamma_rl)
        T = len(a)
        k = self.n_step_k
        # capture at the bootstrap state s' (= state t+k, or the terminal state when done)
        cn = np.empty(T, dtype=np.float32)
        if T - k > 0:
            cn[:T - k] = capture[k:]
            cn[T - k:] = capture[-1]
        else:
            cn[:] = capture[-1]
        pos = self.buf.pos
        self.buf.add_batch(s, a, r, s2, d, g)
        idx = (np.arange(T) + pos) % self.buf.capacity
        self.cap_next[idx] = cn

    def learn(self) -> float | None:
        if self.buf.size < self.warmup:
            return None
        idx, w = self.buf.sample(self.batch, self.rng)
        dev = self.dev
        s = torch.as_tensor(self.buf.s[idx], device=dev)
        s2 = torch.as_tensor(self.buf.s2[idx], device=dev)
        a = torch.as_tensor(self.buf.a[idx], device=dev)
        r = torch.as_tensor(self.buf.r[idx], device=dev)
        d = torch.as_tensor(self.buf.d[idx], device=dev)
        g = torch.as_tensor(self.buf.g[idx], device=dev)
        wt = torch.as_tensor(w, device=dev)
        cn = torch.as_tensor(self.cap_next[idx], device=dev)
        # c(s', a') as a (B, 2) table: capture on the POST branch, zero on SKIP
        c2 = torch.zeros(len(a), 2, device=dev)
        c2[:, POST] = cn

        self.net.reset_noise()
        self.tgt.reset_noise()
        with torch.no_grad():
            # Double DQN on TRUE Q (Q~ + c), which is what the greedy policy actually maximises
            a2 = (q_from_dist(self.net(s2), self.support, 1.0) + c2).argmax(1)
            next_logp = self.tgt(s2)[torch.arange(len(a2), device=dev), a2]
            # the bootstrap value is Q~(s',a*) + c(s',a*); fold the known c into the reward term,
            # which is exactly where the shift belongs in the categorical projection
            r_eff = r + g * (1.0 - d) * c2.gather(1, a2.unsqueeze(1)).squeeze(1)
            m = project(next_logp, r_eff, d, g, self.support)
        logp = self.net(s)[torch.arange(len(a), device=dev), a]
        loss_i = -(m * logp).sum(1)
        loss = (wt * loss_i).mean()
        self.opt.zero_grad(set_to_none=True)
        loss.backward()
        torch.nn.utils.clip_grad_norm_(self.net.parameters(), 10.0)
        self.opt.step()
        self.buf.update(idx, loss_i.detach().cpu().numpy())
        self.steps += 1
        if self.steps % self.target_sync == 0:
            self.tgt.load_state_dict(self.net.state_dict())
        return float(loss.item())

    def state(self):
        return {k: v.detach().cpu().clone() for k, v in self.net.state_dict().items()}


class Ensemble:
    """Mean of several independently seeded shaped critics.

    v1 shipped the luckiest of 120 val evaluations and gave back 19.8 c/market out of sample.
    Averaging Q over seeds attacks that directly: the ensemble's decision boundary is the average
    boundary rather than one draw from its sampling distribution.
    """

    def __init__(self, agents: list[ShapedAgent]):
        self.agents = agents

    def decider(self, mu, sd, risk: float, ep, tau: float = 0.0):
        """`decide(state, pos, t) -> bool`, using the ensemble-mean TRUE Q.

        `tau` is a calibration offset on the POST advantage: post iff Q(POST) - Q(SKIP) > tau.
        tau = 0 is plain greedy. It exists because the v2 scored run established that this
        critic's RANKING across the book is correct and monotone while its ZERO sits about 0.5 c
        too high -- it posted at ~11 ticks when the edge turns positive at 4 -- and a constant
        bias is corrected by one threshold, not by more architecture.

        `capture_c` is materialised once here, not per step: it is a property that rebuilds the
        whole array, and calling it inside the loop would make the rollout quadratic in episode
        length.
        """
        cap = ep.capture_c

        def decide(state, _pos, t):
            x = (state - mu) / sd
            q = np.mean([ag.q_true(x, float(cap[t]), risk)[0] for ag in self.agents], axis=0)
            return bool((q[POST] - q[0]) > tau)
        return decide

    def eval_mode(self, on: bool) -> None:
        for ag in self.agents:
            ag.net.eval() if on else ag.net.train()


__all__ = ["ShapedAgent", "Ensemble"]
