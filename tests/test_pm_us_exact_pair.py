from decimal import Decimal

from idea_lab.analyze_pm_us_exact_pair_live import (
    candidates,
    kalshi_fee,
    pm_fee,
    score,
)


def row(ts=1.0, pm_ask=0.40, pm_bid=0.39, k_yes_bid=0.55, k_no_bid=0.44):
    return {
        "ts": ts,
        "slug": "window",
        "ticker": "ticker",
        "pm_age": 0.001,
        "pm_state": "MARKET_STATE_OPEN",
        "pm_asks": [[pm_ask, 10]],
        "pm_bids": [[pm_bid, 10]],
        "k_yes_bids": [[k_yes_bid, 10]],
        "k_no_bids": [[k_no_bid, 10]],
    }


def test_one_contract_fee_rounding():
    assert kalshi_fee(0.45) == 0.02
    assert pm_fee(0.40) == 0.02
    assert Decimal(str(pm_fee(0.99))) == Decimal("0.00")


def test_candidate_uses_executable_complements():
    found = {item["direction"]: item for item in candidates(row())}
    # PM YES 40c + Kalshi NO 45c + 2c + 2c fees = 89c.
    assert round(found["pm_yes_kalshi_no"]["net"], 8) == 0.11
    # Kalshi YES 56c + PM NO 61c cannot be a complete-set bargain.
    assert found["kalshi_yes_pm_no"]["net"] < 0


def test_persistence_and_one_signal_per_window():
    rows = [row(1.0), row(2.0), row(3.0), row(4.0)]
    report = score(rows)
    assert report["persistent_candidate_windows"] == 1
    assert report["signals"][0]["next_sample_survives"] is True


def test_stale_pm_book_is_rejected():
    stale = row()
    stale["pm_age"] = 0.011
    assert candidates(stale) == []
