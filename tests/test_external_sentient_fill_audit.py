import pytest

from idea_lab.audit_external_sentient_fills import ticker_ledger


def fill(ts, action, side, count, yes, no, fee=0):
    return {
        "ts": ts,
        "fill_id": f"{ts}-{action}-{side}",
        "ticker": "KXBTC15M-TEST",
        "action": action,
        "side": side,
        "count_fp": str(count),
        "yes_price_dollars": str(yes),
        "no_price_dollars": str(no),
        "fee_cost": str(fee),
    }


def test_round_trip_and_residual_settlement():
    rows = [
        fill(1, "buy", "yes", 3, 0.6, 0.4, 0.03),
        fill(2, "sell", "yes", 1, 0.7, 0.3, 0.01),
    ]
    ledger = ticker_ledger(rows, "yes")
    assert ledger["ending_yes"] == 2
    assert ledger["pnl"] == pytest.approx(0.86)
    assert ledger["self_contained"] is True


def test_negative_inventory_marks_incomplete_history():
    ledger = ticker_ledger(
        [fill(1, "sell", "no", 1, 0.2, 0.8)], "yes"
    )
    assert ledger["self_contained"] is False
