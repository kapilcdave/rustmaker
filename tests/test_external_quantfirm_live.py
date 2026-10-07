import pytest

from idea_lab.audit_external_quantfirm_live import summarize


def test_summarize_expands_contract_counts():
    rows = [
        {
            "count": "2",
            "pnl": "0.4",
            "fee": "0.02",
            "settled_at": "2026-09-12T17:00:00Z",
        },
        {
            "count": "1",
            "pnl": "-0.1",
            "fee": "0.01",
            "settled_at": "2026-09-13T17:00:00Z",
        },
    ]
    report = summarize(rows)
    assert report["fills"] == 2
    assert report["contracts"] == 3
    assert report["pnl_dollars"] == pytest.approx(0.3)
