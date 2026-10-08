"""The replay environment: one market is one episode, one offered fill is one step.

This is a REPLAY, not a simulator. Every step is a fill the dataset's fill model already decided
happened (`features.market_rows`: our improved quote was live and the touch had room, so the next
taker on that side hit us). The agent's only choice is whether to have been there:

    action 0 = SKIP   reward 0
    action 1 = POST   reward = the row's `net_c` under the dataset's mark

So the action space is deliberately smaller than a textbook market maker's. A quote-OFFSET action
space would need a fill model that answers "what if I had rested a tick wider", and that
counterfactual is exactly what this venue's data cannot support -- there is no historical resting
depth (corpus: `kalshi-retains-no-historical-resting-depth`), and a book-only fill model has zero
observations on a fast rule. Widening the action space here would mean inventing the fills, which
is the one error this corpus has already paid for at 5.17x.

What makes it sequential rather than a bandit:
  * the position cap masks POST when the fill would breach it;
  * five features are functions of the live position (`features.POS_DEPENDENT`), so the state the
    agent sees depends on what it did earlier in the market, and they are recomputed here instead
    of being read off the dataset's column -- the dataset's `pos_before` is the path the `always`
    quoter carried, which a selective policy does not.

⚠ One bias is structural and unfixable in replay, and it runs AGAINST the agent: `market_rows`
dropped rows whose fill would have breached the cap on the ALWAYS path, so a selective policy is
offered fewer fills than it really would have been (it is holding less inventory, so the cap binds
less often). Rows that do not exist cannot be handed back. A gate therefore cannot win here by
freeing up cap room it did not actually use.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from features import (AS_HALF_C, CLIP_CT, GAMMA_AS, POS_CAP, RL_NUMERIC)

IDX = {c: i for i, c in enumerate(RL_NUMERIC)}
N_FEAT = len(RL_NUMERIC)
SKIP, POST = 0, 1


@dataclass
class Episode:
    """One market, ready for either a rollout or a replay-buffer sweep."""
    ticker: str
    row_index: np.ndarray      # positions in the parent frame, for writing labels back
    x: np.ndarray              # (T, N_FEAT) features, position columns NOT yet filled
    s: np.ndarray              # (T,) +1 improved ask (we sell yes), -1 improved bid
    net_c: np.ndarray          # (T,) reward for POST, cents, under the dataset's mark
    sigma_c: np.ndarray
    spread_ticks: np.ndarray
    mid_c: np.ndarray
    px_c: np.ndarray
    qxi_q: np.ndarray
    ttc_s: np.ndarray
    tvol60: np.ndarray

    def __len__(self) -> int:
        return len(self.s)

    @property
    def capture_c(self) -> np.ndarray:
        """Our signed half spread, cents: s*(px_c - mid_c).

        This is the HALF of the reward that is known at decision time -- it is a deterministic
        function of columns already in the state. The identity is
            net_c = capture_c - adverse_c,   adverse_c = s*(mid_{t+5s} - mid_c)
        so `capture_c` is exactly the part a critic should never have to learn, and `adverse_c` is
        the only random part. The corpus states the same identity from the measurement side: the
        pre-trade mid cancels out of `capture - adverse` identically
        (`real-print-maker-ledger-15m-venue-normal`).
        """
        return self.s * (self.px_c - self.mid_c)

    @property
    def adverse_c(self) -> np.ndarray:
        """The random half: what the mid did to us over the markout window."""
        return self.capture_c - self.net_c


def episodes(d: pd.DataFrame) -> list[Episode]:
    """Split a fold into per-market episodes in decision order."""
    d = d.reset_index(drop=True)
    missing = [c for c in RL_NUMERIC if c not in d.columns]
    if missing:
        raise KeyError(f"dataset is missing RL features {missing}; rebuild with ml/build_dataset.py")
    if (d.kind != "entry").any():
        raise ValueError("the RL env is entry-only; build the dataset with a markout mark")
    out = []
    for tk, g in d.groupby("ticker", sort=False):
        g = g.sort_values("vt")
        out.append(Episode(
            ticker=str(tk), row_index=g.index.to_numpy(),
            x=np.nan_to_num(g[RL_NUMERIC].to_numpy(dtype=np.float32)),
            s=g.s.to_numpy(dtype=np.float32), net_c=g.net_c.to_numpy(dtype=np.float32),
            sigma_c=g.sigma_c.to_numpy(dtype=np.float32),
            spread_ticks=g.spread_ticks.to_numpy(dtype=np.float32),
            mid_c=g.mid_c.to_numpy(dtype=np.float32), px_c=g.px_c.to_numpy(dtype=np.float32),
            qxi_q=g.qxi_q.to_numpy(dtype=np.float32), ttc_s=g.ttc_s.to_numpy(dtype=np.float32),
            tvol60=g.tvol60.to_numpy(dtype=np.float32),
        ))
    return out


def _fill_position(x: np.ndarray, t: int, pos: float, ep: Episode) -> None:
    """Overwrite the position-dependent features at step t with the LIVE position.

    Mirrors `features._derive` exactly; if that changes, this must change with it. Kept as a
    separate function so `test_env.py` can assert the two agree on the dataset's own rows.
    """
    x[IDX["pos_before"]] = pos
    skew = -pos * GAMMA_AS * ep.sigma_c[t] ** 2 * ep.ttc_s[t]
    x[IDX["as_skew_c"]] = skew
    x[IDX["as_resv_c"]] = ep.mid_c[t] + skew
    x[IDX["as_edge_c"]] = ep.s[t] * (ep.px_c[t] - (ep.mid_c[t] + skew))
    x[IDX["qxi_lean"]] = ep.qxi_q[t] * (1.0 + (pos * -ep.s[t]) / POS_CAP)


def state_at(ep: Episode, t: int, pos: float) -> np.ndarray:
    x = ep.x[t].copy()
    _fill_position(x, t, pos, ep)
    return x


def can_post(pos: float, s: float, cap: float = POS_CAP) -> bool:
    """A POST that would breach the inventory cap is not an available action."""
    return abs(pos + (-s) * CLIP_CT) <= cap


def rollout(ep: Episode, decide, cap: float = POS_CAP):
    """Walk one episode under a decision function.

    `decide(state_vector, pos, t) -> bool` returns True to POST. Returns
    ``(actions, states, pos_path, pnl_c)``; `states` are the states actually shown to `decide`,
    which is what a replay buffer must store (the agent's own inventory path, not the dataset's).
    """
    T = len(ep)
    acts = np.zeros(T, dtype=np.int64)
    states = np.empty((T, N_FEAT), dtype=np.float32)
    pos_path = np.empty(T, dtype=np.float32)
    pos, pnl = 0.0, 0.0
    for t in range(T):
        st = state_at(ep, t, pos)
        states[t] = st
        pos_path[t] = pos
        if can_post(pos, ep.s[t], cap) and decide(st, pos, t):
            acts[t] = POST
            pnl += float(ep.net_c[t])
            pos += -float(ep.s[t]) * CLIP_CT
    return acts, states, pos_path, pnl


def labels_from(d: pd.DataFrame, eps: list[Episode], actions: list[np.ndarray]) -> np.ndarray:
    """Actions -> the label array `ml/policy_eval.py` scores.

    The side is not the agent's to choose: the row IS a fill on side s, so POST means
    BUY_NO when we sold yes (s>0) and BUY_YES when we bought yes (s<0) -- the same mapping
    `arm_always` uses, so the arms differ only in WHICH fills they take.
    """
    out = np.full(len(d), "HOLD", dtype=object)
    for ep, a in zip(eps, actions):
        take = a == POST
        out[ep.row_index[take]] = np.where(ep.s[take] < 0, "BUY_YES", "BUY_NO")
    return out.astype(str)


def room_half_c(ep: Episode, t: int) -> float:
    """Our signed half spread in cents -- the quantity `room4` gates on, in A-S coordinates."""
    return float(ep.s[t] * (ep.px_c[t] - ep.mid_c[t]))


__all__ = ["Episode", "episodes", "rollout", "labels_from", "state_at", "can_post",
           "room_half_c", "IDX", "N_FEAT", "SKIP", "POST", "AS_HALF_C"]
