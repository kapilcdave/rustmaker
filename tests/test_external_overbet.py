import pytest

from idea_lab.analyze_external_overbet import find_signal, score_market


def market(midpoints, result="yes"):
    t0 = 1_800_000_000
    bars = []
    for minute, mid in enumerate(midpoints, 1):
        bars.append(
            {
                "ts": t0 + minute * 60,
                "b": mid - 0.01,
                "a": mid + 0.01,
                "post_close": False,
            }
        )
    return {
        "ticker": "KXBTC15M-TEST",
        "open_ts": t0,
        "result": result,
        "bars": bars,
    }


def test_requires_three_strict_extreme_closes():
    candidate = market([0.81, 0.19, 0.81, 0.50, 0.70, 0.70])
    side, row, count = find_signal(candidate)
    assert side == "yes"
    assert row["ts"] == candidate["open_ts"] + 300
    assert count == 3

    assert find_signal(market([0.81, 0.19, 0.80, 0.50, 0.70, 0.70])) is None


def test_entry_side_comes_from_minute_five_and_uses_ask():
    candidate = market([0.81, 0.82, 0.83, 0.40, 0.40, 0.30], result="no")
    immediate, delayed = score_market(candidate)
    assert immediate["side"] == "no"
    assert immediate["entry"] == pytest.approx(0.61)
    assert delayed is not None
    assert delayed["entry"] == pytest.approx(0.71)


def test_missing_observation_minute_is_excluded():
    candidate = market([0.81, 0.82, 0.83, 0.40, 0.40, 0.30])
    del candidate["bars"][2]
    assert find_signal(candidate) is None
