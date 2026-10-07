import pytest

from idea_lab.analyze_external_turbine_top import (
    batch_fee,
    completed_velocity,
    entry_signal,
    exit_signal,
)


def test_completed_velocity_uses_only_completed_coinbase_bar():
    ts = 1_800_000_000
    spot = {ts - 120: 100.0, ts - 60: 100.2, ts: 200.0}
    assert completed_velocity(spot, ts) == pytest.approx(0.2)


def test_entry_rule_is_strict():
    row = {"b": 0.50, "a": 0.52}
    assert entry_signal(row, 0.168, 181)
    assert not entry_signal(row, 0.167, 181)
    assert not entry_signal(row, 0.168, 180)


def test_exit_rules_and_batch_fee():
    row = {"b": 0.40, "a": 0.42}
    assert exit_signal(row, 0.0, 179, 0.40) == "closeout"
    assert exit_signal(row, -0.051, 300, 0.40) == "velocity_collapse"
    assert batch_fee(0.5) == 0.70
