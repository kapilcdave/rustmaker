from idea_lab.analyze_external_settlement_basis import fit_probit


def test_probit_fit_recovers_directional_offset():
    rows = [
        {"distance": distance, "outcome": int(distance + 10 >= 0)}
        for distance in range(-100, 101, 5)
    ]
    model = fit_probit(rows)
    assert model["converged"]
    assert model["offset_dollars"] > 0
    assert model["scale_dollars"] >= 1
