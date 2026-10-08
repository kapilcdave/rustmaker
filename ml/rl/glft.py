"""The Guéant-Lehalle-Fernandez-Tapia controller, as the arm the RL agent has to beat.

GLFT (2013) solves the market maker's inventory problem in the exponential-intensity model
lambda(delta) = A exp(-k delta) and gives a closed-form reservation quote. In the asymptotic
(long-horizon) regime the required half spread on each side is

    delta_b(q) = (1/g) ln(1 + g/k) + ((2q + 1)/2) * sqrt( s^2 g / (2 k A) * (1 + g/k)^(1 + k/g) )
    delta_a(q) = (1/g) ln(1 + g/k) + ((1 - 2q)/2) * sqrt( same )

with g the risk aversion, k the arrival decay, A the arrival scale, s the volatility and q the
inventory. The first term is inventory-free (the "spread" term), the second is the skew: long
inventory widens the bid and tightens the ask.

In this instrument the quoter does not choose a price -- it is offered a fill one tick inside the
touch and decides whether to be there. So GLFT becomes a GATE: post iff the offered half spread
clears delta(q) on the side being offered.

⚠ Two of the three parameters are not identified on this venue, and the corpus says so in writing.
`k` is MEASURED (108.62 per dollar, age-standardised on 1.66M Kalshi 15M orders) but NOT
identified -- the per-age-bin estimate ranges +121 to -219, because order age is set by our own
requote cadence, so the hazard is downstream of a policy choice (corpus:
`as-arrival-decay-k-is-not-identified`). `A` is calibrated here per row from the observed arrival
rate rather than invented: inverting lambda(delta) = A exp(-k delta) at the observed touch gives
A = lambda_obs * exp(k * delta_obs). `g` is swept on the VAL fold, which is the same courtesy
`room4` got (its threshold k=4 was optimised on train and val before one test score).

In the small-g limit this lattice admits, the closed form degenerates in a way worth knowing:
g/k ~ 1.4e-4, so (1/g) ln(1 + g/k) -> 1/k = 0.921 c (exactly `features.AS_HALF_C`) and
(1 + g/k)^(1 + k/g) -> e. The spread term is therefore g-free and the whole g dependence lives in
the skew, which is O(sqrt(g)). That is why a g sweep moves this arm much less than it looks like
it should.
"""
from __future__ import annotations

import numpy as np

from features import AS_HALF_C, CLIP_CT, K_AS

from .env import Episode, rollout

K_C = K_AS / 100.0           # arrival decay per CENT (K_AS is per dollar); 1/K_C = AS_HALF_C
GAMMA_GRID = (3e-5, 1e-4, 3e-4, 1e-3, 3e-3, 1e-2)   # swept on val, frozen before the test score
A_FLOOR = 1e-3               # arrivals/s; a market with no prints in the window gets the floor


def arrival_scale(tvol60: np.ndarray, spread_c: np.ndarray, k_c: float = K_C) -> np.ndarray:
    """Calibrate A from the observed arrival rate at the observed distance.

    lambda_obs is taker contracts per second over the trailing 60 s; delta_obs is the half spread
    a maker at the touch was standing at. Inverting the intensity model gives A, so the skew term
    is scaled by a rate this market actually showed instead of a literature constant.
    """
    lam = np.maximum(tvol60 / 60.0, A_FLOOR)
    return lam * np.exp(k_c * np.maximum(spread_c / 2.0, 0.0))


def required_half_c(pos: float, s: float, sigma_c: float, A: float,
                    gamma: float, k_c: float = K_C) -> float:
    """delta(q) in cents for the side being offered.

    The fill direction is d = -s (an improved ask makes us short yes), and the two GLFT branches
    collapse to one expression in d: the skew multiplier is (1 + 2 q d)/2, which is (2q+1)/2 for
    the bid (d=+1) and (1-2q)/2 for the ask (d=-1).
    """
    spread_term = np.log1p(gamma / k_c) / gamma
    inner = (sigma_c ** 2) * gamma / (2.0 * k_c * max(A, A_FLOOR))
    inner *= (1.0 + gamma / k_c) ** (1.0 + k_c / gamma)
    skew = np.sqrt(max(inner, 0.0))
    d = -s
    return float(spread_term + ((1.0 + 2.0 * pos * d) / 2.0) * skew * CLIP_CT)


def decider(ep: Episode, gamma: float, k_c: float = K_C):
    """A `decide` callable for `env.rollout`: post iff the offered edge clears delta(q)."""
    A = arrival_scale(ep.tvol60, _spread_c(ep), k_c)
    offered = ep.s * (ep.px_c - ep.mid_c)        # our signed half spread, cents

    def decide(_state, pos, t):
        return offered[t] >= required_half_c(pos, float(ep.s[t]), float(ep.sigma_c[t]),
                                             float(A[t]), gamma, k_c)
    return decide


def _spread_c(ep: Episode) -> np.ndarray:
    """Quoted spread in cents, reconstructed from the half spread the row records.

    `px_c` is one tick inside the touch, so s*(px_c - mid_c) is (spread/2 - tick); the dataset
    carries the spread in TICKS and the tick size differs between the 1 c and 0.1 c lattices, so
    the robust reconstruction is through the offered half spread rather than through the tick.
    """
    return 2.0 * np.abs(ep.s * (ep.px_c - ep.mid_c))


def run(eps: list[Episode], gamma: float, k_c: float = K_C):
    """Roll GLFT over a fold. Returns (actions per episode, total cents, posts)."""
    acts, total, posts = [], 0.0, 0
    for ep in eps:
        a, _, _, pnl = rollout(ep, decider(ep, gamma, k_c))
        acts.append(a)
        total += pnl
        posts += int(a.sum())
    return acts, total, posts


def spread_term_c(gamma: float, k_c: float = K_C) -> float:
    """The inventory-free half spread GLFT demands, in cents. Equals AS_HALF_C as gamma -> 0."""
    return float(np.log1p(gamma / k_c) / gamma)


__all__ = ["GAMMA_GRID", "K_C", "arrival_scale", "required_half_c", "decider", "run",
           "spread_term_c", "AS_HALF_C"]
