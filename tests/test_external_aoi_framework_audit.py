from idea_lab.audit_external_aoi_framework import taker_fee_cents


def test_one_contract_fee_rounding():
    assert taker_fee_cents(50) == 2
    assert taker_fee_cents(1) == 1
    assert taker_fee_cents(99) == 1
    assert taker_fee_cents(0) == 0
    assert taker_fee_cents(100) == 0

