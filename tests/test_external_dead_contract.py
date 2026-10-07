import pytest

from idea_lab.analyze_external_dead_contract import find_signal, score_market


def market(result="no"):
    t0 = 1_800_000_000
    bars = []
    for minute in range(1, 16):
        bid, ask = 0.30, 0.32
        if minute == 10:
            bid, ask = 0.08, 0.10
        if minute == 11:
            bid, ask = 0.07, 0.09
        bars.append(
            {
                "ts": t0 + minute * 60,
                "b": bid,
                "a": ask,
                "post_close": minute == 15,
            }
        )
    return {
        "ticker": "KXBTC15M-TEST",
        "open_ts": t0,
        "result": result,
        "bars": bars,
    }


def test_finds_first_eligible_minute_and_floored_midpoint():
    row, midpoint = find_signal(market())
    assert row["ts"] == 1_800_000_000 + 600
    assert midpoint == 9


def test_uses_executable_no_ask_not_midpoint_complement():
    immediate, delayed = score_market(market())
    assert immediate["entry"] == 0.92
    assert immediate["result"] == "no"
    assert delayed is not None
    assert delayed["entry"] == pytest.approx(0.93)


def test_below_eight_cent_midpoint_is_excluded():
    candidate = market()
    candidate["bars"][9]["b"] = 0.06
    candidate["bars"][9]["a"] = 0.08
    row, midpoint = find_signal(candidate)
    assert row["ts"] == 1_800_000_000 + 660
    assert midpoint == 8
