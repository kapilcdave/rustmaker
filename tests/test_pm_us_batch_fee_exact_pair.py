from idea_lab.analyze_pm_us_batch_fee_exact_pair import batch_fee, opportunities, score


def row(ts=10.0, slug="w"):
    return {
        "ts": ts, "slug": slug, "ticker": "T",
        "pm_state": "MARKET_STATE_OPEN", "pm_age": 0.001,
        "pm_bids": [[0.48, 30]], "pm_asks": [[0.49, 30]],
        "k_yes_bids": [[0.50, 30]], "k_no_bids": [[0.50, 30]],
    }


def test_batch_fee_is_rounded_once_for_quantity():
    assert batch_fee(0.07, 0.50, 10) == 0.18


def test_batch_opportunity_uses_displayed_integer_depth():
    found = opportunities(row())
    assert found[0]["quantity"] == 30
    assert found[0]["direction"] == "pm_yes_kalshi_no"


def test_persistence_gate():
    report = score([row(10), row(11)])
    assert report["persistent_candidate_windows"] == 0  # margin is below frozen 1c
