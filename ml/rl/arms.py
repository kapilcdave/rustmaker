"""The benchmark arms the RL quoter is measured against, in the harness's label format.

`room4` is the one that matters. It is a single comparison -- `spread_ticks >= 4` -- it is already
deployed inside `penny.py`'s 11 ms path, and on this dataset it scores +65.7 c/market against the
30-feature logistic's +69.3 (paired difference +3.6 +- 6.0, t = 0.60). The corpus lesson that
produced it is that the incumbent model collapsed to one line, so "beat the logistic arm" is not
the bar; beating one line is.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

ROOM4_TICKS = 4          # threshold optimised on train and val BEFORE the single test score


def arm_room4(d: pd.DataFrame, ticks: int = ROOM4_TICKS) -> np.ndarray:
    """Post whenever the quoted spread leaves at least `ticks` of room. Nothing else."""
    take = (d.kind == "entry") & (d.spread_ticks >= ticks)
    return np.where(take, np.where(d.s < 0, "BUY_YES", "BUY_NO"), "HOLD")


def arm_as_room(d: pd.DataFrame, thresh: float = 0.0) -> np.ndarray:
    """The same gate in Avellaneda-Stoikov coordinates: our half spread minus the A-S optimum.

    Scores AUC 0.675 against `spread_ticks`'s 0.643 for "which side of a big move", because it
    does not conflate the 1 c and 0.1 c lattices -- and still does not convert to more cents. Kept
    as an arm so the RL agent cannot claim credit for rediscovering a better coordinate.
    """
    take = (d.kind == "entry") & (d.as_room_c >= thresh)
    return np.where(take, np.where(d.s < 0, "BUY_YES", "BUY_NO"), "HOLD")


__all__ = ["arm_room4", "arm_as_room", "ROOM4_TICKS"]
