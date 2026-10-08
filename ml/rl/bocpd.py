"""Bayesian online change-point detection over directional taker flow bias.

Adams & MacKay (2007), run-length posterior with a constant hazard, Beta-Bernoulli observation
model. The stream is one observation per PRINT: x_i = 1 if the taker bought yes (lifted an ask),
0 if the taker sold yes. theta = P(next taker lifts the ask) is the regime's directional bias,
and a change point is a shift in that bias -- the thing a static gate cannot see.

Why per print and not per contract: a 50-lot print is one taker decision, and weighting by size
turns the Bernoulli into a Binomial whose effective sample size is set by the clip distribution
rather than by arrival count. The bias we want is "which way are arrivals leaning", so arrivals
are the sample unit. `tvol60` already carries size.

⚠ One modelling choice matters enough to state, because the textbook recursion makes the headline
feature useless here. If the change-point branch scores x_t under the OLD run's posterior
predictive (as in the paper's eq. 3 and most implementations), then with a constant hazard H the
normalised P(run length = 0) is **exactly H at every t** -- the numerator and denominator share
the same evidence sum, so the feature is a constant with zero variance. Measured here: cp_prob
sat at 0.0200 = H for all 120 steps of a stream that visibly flips regime at t=60. A fresh run is
generatively a fresh theta drawn from the prior, so this module scores the change-point branch
under the PRIOR predictive instead. That version spikes (0.02 -> 0.23 on the flip above) and is
the one with information in it.

Indexing convention: r = number of observations in the current run, INCLUDING the latest one, so
r = 1 means "a change point just happened". `bocpd_path` returns, for every k, the posterior after
the FIRST k observations, so a feature read at index k has seen nothing at or after k.
"""
from __future__ import annotations

import numpy as np

R_MAX = 200          # run-length truncation, in prints. Growth mass at the top bin is folded back
                     # into it, so a long quiet regime does not silently lose probability (which
                     # would renormalise the posterior toward "a change point just happened").
HAZARD = 1.0 / 50.0  # prior change-point rate: one regime shift per ~50 prints. A SCALE CHOICE,
                     # frozen before any score; the val fold selects nothing about it.
ALPHA0 = 1.0         # Beta(1,1) prior on theta -- uniform, no directional prior
BETA0 = 1.0

KEYS = ("cp_prob", "cp_recent", "bias_mean", "bias_sd", "run_len")


def bocpd_path(x: np.ndarray, r_max: int = R_MAX, hazard: float = HAZARD,
               alpha0: float = ALPHA0, beta0: float = BETA0) -> dict[str, np.ndarray]:
    """Run the BOCPD recursion over a binary stream.

    Returns arrays of length ``len(x) + 1``; index k is the posterior having seen ``x[:k]``.

      cp_prob    P(run length == 1): a change point at the latest observation
      cp_recent  P(run length <= 4): a change point within the last few prints
      bias_mean  model-averaged E[theta] = P(the next taker lifts the ask)
      bias_sd    posterior SD of theta under the run-length mixture
      run_len    E[run length], prints, capped at r_max
    """
    x = np.asarray(x)
    n = len(x)
    out = {k: np.empty(n + 1, dtype=np.float64) for k in KEYS}
    # p[r] = P(run length = r | data so far); alpha[r]/beta[r] are that run's Beta parameters.
    # r = 0 is the empty run and is only occupied before the first observation.
    p = np.zeros(r_max + 1)
    p[0] = 1.0
    alpha = np.full(r_max + 1, alpha0)
    beta = np.full(r_max + 1, beta0)
    rs = np.arange(r_max + 1, dtype=np.float64)
    prior_pred = (alpha0, beta0)

    def record(k: int) -> None:
        w = p / max(p.sum(), 1e-300)
        th = alpha / (alpha + beta)
        var = alpha * beta / ((alpha + beta) ** 2 * (alpha + beta + 1.0))
        m = float(w @ th)
        out["cp_prob"][k] = w[1]
        out["cp_recent"][k] = w[1:5].sum()
        out["bias_mean"][k] = m
        # law of total variance over the mixture: within-component + between-component
        out["bias_sd"][k] = float(np.sqrt(max(w @ var + w @ (th - m) ** 2, 0.0)))
        out["run_len"][k] = float(w @ rs)

    record(0)
    for k in range(n):
        xi = int(x[k])
        a0, b0 = prior_pred
        pi_new = (a0 if xi else b0) / (a0 + b0)        # fresh run: the PRIOR predictive
        pi_run = (alpha if xi else beta) / (alpha + beta)   # continuing run r, per r

        grow = (1.0 - hazard) * p * pi_run             # r -> r + 1
        cp = hazard * pi_new * p.sum()                 # any r -> 1, x_k starts a new run

        p_new = np.zeros_like(p)
        p_new[1:] = grow[:-1]
        p_new[r_max] += grow[r_max]                    # fold the truncated tail back in
        p_new[1] += cp

        a_new = np.empty_like(alpha)
        b_new = np.empty_like(beta)
        a_new[0], b_new[0] = a0, b0                    # unreachable after k=0, kept well-defined
        a_new[1:] = alpha[:-1] + xi
        b_new[1:] = beta[:-1] + (1 - xi)
        # r=1 is a mixture of "the change-point branch" and "the empty run grew", and both carry
        # exactly the prior plus x_k, so no merge is needed there.
        # The folded top bin IS a mixture of two different run lengths; merge its statistics by
        # mass rather than letting the shift silently keep only one of them.
        wl, wr = grow[r_max - 1], grow[r_max]
        tot = wl + wr
        if tot > 0:
            a_new[r_max] = (wl * (alpha[r_max - 1] + xi) + wr * (alpha[r_max] + xi)) / tot
            b_new[r_max] = (wl * (beta[r_max - 1] + 1 - xi)
                            + wr * (beta[r_max] + 1 - xi)) / tot
        p, alpha, beta = p_new, a_new, b_new
        s = p.sum()
        if s > 0:
            p /= s
        record(k + 1)
    return out


def flow_bits(side: np.ndarray) -> np.ndarray:
    """Taker-side strings -> the binary stream. A yes print is a taker lifting an ask.

    The convention is the one tested directly against the quoted touch on 8 of 8 series
    (corpus: `kalshi-taker-side-convention-confirmed`), not assumed.
    """
    return (np.asarray(side) == "yes").astype(np.int8)
