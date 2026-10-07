import pytest

from idea_lab.analyze_external_fastclose_band import trade_row


def test_selects_favorite_and_uses_complement_no_ask():
    row = trade_row(
        "TEST",
        1_800_000_000,
        yes_bid=0.04,
        yes_ask=0.05,
        outcome_yes=False,
        sample="test",
    )
    assert row is not None
    assert row["side"] == "no"
    assert row["entry"] == pytest.approx(0.96)
    assert row["payout"] == 1.0


def test_band_is_inclusive_and_outside_prices_are_rejected():
    assert trade_row("T", 1, 0.10, 0.11, False, "x")["entry"] == pytest.approx(
        0.90
    )
    assert trade_row("T", 1, 0.03, 0.04, False, "x")["entry"] == pytest.approx(
        0.97
    )
    assert trade_row("T", 1, 0.02, 0.03, False, "x") is None

