from __future__ import annotations

import itertools
import math


def payouts(returns: tuple[int, ...]) -> tuple[list[float], list[int]]:
    high = max(returns)
    leaders = [index for index, value in enumerate(returns) if value == high]
    yes_value = math.floor(100 / len(leaders)) / 100
    leader = [yes_value if index in leaders else 0.0 for index in range(len(returns))]
    up = [int(value > 0) for value in returns]
    return leader, up


def test_complete_set_worst_case_bounds_include_fractional_ties():
    for returns in itertools.product(range(-1, 2), repeat=5):
        leader, _ = payouts(returns)
        assert sum(leader) >= 0.99
        assert sum(1 - value for value in leader) >= 4.0
        for omitted in range(5):
            assert sum(1 - leader[index] for index in range(5) if index != omitted) >= 3.0


def test_leader_and_positive_implies_leader_is_positive():
    for returns in itertools.product(range(-1, 2), repeat=5):
        leader, up = payouts(returns)
        for i in range(5):
            for j in range(5):
                if i == j:
                    continue
                # Portfolio NO(L_i) + NO(U_j) + YES(U_i).
                payoff = (1 - leader[i]) + (1 - up[j]) + up[i]
                assert payoff >= 1.0
