import pytest

from idea_lab.analyze_external_mickey_remaining import (
    favorite_signal,
    momentum_signal,
    score,
)


def sample(result="yes"):
    t0 = 1_800_000_000
    mids = {
        1: (0.49, 0.51),
        2: (0.59, 0.61),
        3: (0.70, 0.72),
        4: (0.52, 0.54),
        5: (0.60, 0.62),
        6: (0.64, 0.66),
        7: (0.68, 0.70),
        8: (0.69, 0.71),
    }
    return {
        "ticker": "KXBTC15M-TEST",
        "open_ts": t0,
        "result": result,
        "bars": [
            {"ts": t0 + minute * 60, "b": bid, "a": ask}
            for minute, (bid, ask) in mids.items()
        ],
    }


def test_favorite_uses_first_eligible_bar():
    side, row, midpoint = favorite_signal(sample())
    assert side == "yes"
    assert row["ts"] == 1_800_000_000 + 120
    assert midpoint == 60


def test_momentum_compares_with_first_observation():
    side, row, current, initial = momentum_signal(sample())
    assert side == "yes"
    assert row["ts"] == 1_800_000_000 + 300
    assert (current, initial) == (61, 50)


def test_score_uses_side_ask():
    candidate = sample()
    immediate, delayed = score(
        candidate, momentum_signal(candidate), "momentum"
    )
    assert immediate["entry"] == pytest.approx(0.62)
    assert delayed["entry"] == pytest.approx(0.66)
