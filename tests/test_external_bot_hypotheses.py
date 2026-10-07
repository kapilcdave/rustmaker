from __future__ import annotations

import itertools

from idea_lab.analyze_mystic_done_deal_oos import aligned_and_no_wick


def test_mystic_momentum_and_wick_gate_accepts_clean_trend():
    # Tuple layout is (open, close, low, high).
    spot = {
        120: (100.0, 101.0, 99.9, 101.1),
        180: (101.0, 102.0, 100.9, 102.1),
        240: (102.0, 103.0, 101.9, 103.1),
    }
    assert aligned_and_no_wick(spot, 300, "up", 103.0)
    assert not aligned_and_no_wick(spot, 300, "down", 103.0)


def test_mystic_gate_rejects_large_opposing_wick():
    spot = {
        120: (100.0, 101.0, 99.9, 101.1),
        180: (101.0, 102.0, 98.0, 102.1),
        240: (102.0, 103.0, 99.0, 103.1),
    }
    assert not aligned_and_no_wick(spot, 300, "up", 103.0)


def test_crossvenue_opposite_side_pair_is_not_risk_free_when_indexes_differ():
    # Pair Kalshi YES with Polymarket DOWN.
    for kalshi_up, poly_up in itertools.product((False, True), repeat=2):
        payout = int(kalshi_up) + int(not poly_up)
        if kalshi_up == poly_up:
            assert payout == 1
        else:
            assert payout in (0, 2)
