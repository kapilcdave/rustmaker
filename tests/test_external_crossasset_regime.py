import math

import pytest

from idea_lab.analyze_external_crossasset_regime import (
    STATE_ACTION,
    feature,
    score_causal_minute5,
    score_market,
)


def series(t0, changes):
    price = 100.0
    out = {}
    for minute in range(-31, 7):
        price *= math.exp(changes.get(minute, 0.001))
        out[t0 + minute * 60] = price
    return out


def market(t0, bid=0.30, ask=0.32, result="no"):
    return {
        "ticker": "KXBTC15M-TEST",
        "open_ts": t0,
        "result": result,
        "bars": [
            {
                "ts": t0 + minute * 60,
                "b": bid,
                "a": ask,
                "post_close": False,
            }
            for minute in range(1, 7)
        ],
    }


def test_state_map_matches_frozen_source_code():
    assert len(STATE_ACTION) == 9
    assert STATE_ACTION["B-/E-/deficit"] == "no"
    assert STATE_ACTION["B+/E+/track"] == "yes"


def test_feature_uses_minute_five_direction_and_fixed_vol_thresholds():
    t0 = 1_800_000_000
    pre = {minute: 0.01 if minute % 2 else -0.01 for minute in range(-31, 0)}
    btc = series(t0, {**pre, **{minute: -0.001 for minute in range(0, 6)}})
    eth = series(t0, {**pre, **{minute: -0.001 for minute in range(0, 6)}})
    found = feature(market(t0), btc, eth)
    assert found is not None
    assert found["state"] == "B-/E-/deficit"
    assert found["vol_ratio"] < 0.6


def test_no_side_uses_executable_complement_ask():
    t0 = 1_800_000_000
    pre = {minute: 0.01 if minute % 2 else -0.01 for minute in range(-31, 0)}
    btc = series(t0, {**pre, **{minute: -0.001 for minute in range(0, 6)}})
    eth = series(t0, {**pre, **{minute: -0.001 for minute in range(0, 6)}})
    immediate, delayed = score_market(market(t0), btc, eth)
    assert immediate["side"] == "no"
    assert immediate["entry"] == pytest.approx(0.70)
    assert delayed is not None
    assert delayed["entry"] == pytest.approx(0.70)


def test_causal_minute_five_uses_spot_through_minute_four():
    t0 = 1_800_000_000
    pre = {minute: 0.01 if minute % 2 else -0.01 for minute in range(-31, 0)}
    btc_changes = {**pre, **{minute: -0.001 for minute in range(0, 5)}, 5: 0.2}
    eth_changes = {**pre, **{minute: -0.001 for minute in range(0, 5)}, 5: 0.2}
    scored = score_causal_minute5(
        market(t0), series(t0, btc_changes), series(t0, eth_changes)
    )
    assert scored is not None
    assert scored["spot_minute"] == 4
    assert scored["side"] == "no"
