from idea_lab.analyze_pm_us_leadlag_taker import opportunities, score


def row(ts=10.0, slug="w", pm_bid=0.60, pm_ask=0.61,
        k_yes_bid=0.50, k_no_bid=0.50):
    return {
        "ts": ts,
        "slug": slug,
        "ticker": "T",
        "pm_state": "MARKET_STATE_OPEN",
        "pm_age": 0.001,
        "pm_bids": [[pm_bid, 3]],
        "pm_asks": [[pm_ask, 4]],
        "k_yes_bids": [[k_yes_bid, 5]],
        "k_no_bids": [[k_no_bid, 6]],
    }


def test_kalshi_yes_leadlag_edge_uses_ask_and_fee():
    found = {x["direction"]: x for x in opportunities(row())}
    assert round(found["kalshi_yes"]["entry"], 6) == 0.50
    assert round(found["kalshi_yes"]["fee"], 6) == 0.02
    assert round(found["kalshi_yes"]["edge"], 6) == 0.08


def test_stale_pm_book_is_rejected():
    value = row()
    value["pm_age"] = 0.011
    assert opportunities(value) == []


def test_requires_two_consecutive_samples_and_one_signal_per_window():
    rows = [row(10.0), row(11.0), row(12.0)]
    report = score(rows, {"T": "yes"})
    assert report["persistent_signal_windows"] == 1
    assert report["settled"] == 1
    assert round(report["mean_c"], 6) == 48.0


def test_gap_breaks_persistence():
    report = score([row(10.0), row(12.0)], {"T": "yes"})
    assert report["persistent_signal_windows"] == 0
