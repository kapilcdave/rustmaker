from idea_lab.analyze_external_sentient_markov import (
    aggregate,
    build_transition_matrix,
    price_change_to_state,
)


def test_aggregate_requires_complete_bars():
    rows = [
        [60 * i, 9 + i, 11 + i, 10 + i, 10.5 + i, 1]
        for i in range(5)
    ]
    bar = aggregate(rows, 300)[0]
    assert bar == [0, 9.0, 15.0, 10.0, 14.5, 5.0]
    assert aggregate(rows[:-1], 300) == []


def test_state_and_transition_are_deterministic():
    assert price_change_to_state(0.0) == 4
    matrix = build_transition_matrix([4, 4, 5])
    assert matrix[4][4] == 0.5
    assert matrix[4][5] == 0.5
