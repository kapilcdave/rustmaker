import math

import pytest

from idea_lab.analyze_external_calibration_transfer import (
    fit_logistic,
    predicted_yes,
)


def test_logistic_fit_recovers_planted_calibration_map():
    mids = [0.08 + index * 0.008 for index in range(105)]
    intercept = -0.15
    slope = 1.2
    # Deterministic fractional replication approximates each planted
    # probability without introducing a random test seed.
    expanded_mids = []
    outcomes = []
    for mid in mids:
        p = 1.0 / (
            1.0
            + math.exp(
                -(
                    intercept
                    + slope * math.log(mid / (1.0 - mid))
                )
            )
        )
        wins = round(p * 100)
        expanded_mids.extend([mid] * 100)
        outcomes.extend([1] * wins + [0] * (100 - wins))
    model = fit_logistic(expanded_mids, outcomes)
    assert model["converged"]
    assert model["intercept"] == pytest.approx(intercept, abs=0.02)
    assert model["slope"] == pytest.approx(slope, abs=0.02)


def test_predicted_yes_applies_intercept_and_logit_slope():
    model = {"intercept": 0.0, "slope": 1.0}
    assert predicted_yes(model, 0.37) == pytest.approx(0.37)

