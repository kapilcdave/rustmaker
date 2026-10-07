from idea_lab.analyze_external_brandon_gbm import model_probability, normal_cdf


def test_normal_cdf_is_symmetric():
    assert abs(normal_cdf(0.0) - 0.5) < 1e-12
    assert abs(normal_cdf(1.0) + normal_cdf(-1.0) - 1.0) < 1e-12


def test_model_uses_only_completed_spot_bar():
    rows = {}
    for minute in range(-20, 3):
        ts = minute * 60
        price = 100.0 + minute * 0.01
        rows[ts] = [ts, price, price, price, price, 1.0]
    # The decision at t=120 must read the close at t=60, not t=120.
    rows[120][4] = 10_000.0
    found = model_probability(rows, 0, 120)
    assert found is not None
    _, diagnostics = found
    assert diagnostics["spot_timestamp"] == 60
    assert diagnostics["current_price"] == rows[60][4]
