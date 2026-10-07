from pathlib import Path

from idea_lab.audit_external_stink_ml_framework import (
    audit_ml_framework,
    audit_stink_bid,
)


STINK_REPO = Path(
    "/tmp/kalshi_ext_20261006_8/artyomderkach-bit_btc-15m-vol-backtest"
)
ML_REPO = Path("/tmp/kalshi_ext_20261006_8/SiddhaBasu_kalshi-bot")


def test_external_stink_bid_artifact_exposes_fill_model_failures():
    if not STINK_REPO.exists():
        return
    report = audit_stink_bid(STINK_REPO)
    assert report["simulated_fill_rows"] == 41
    assert report["independent_filled_markets"] == 14
    assert report["max_levels_filled_by_one_print"] == 5
    assert report["source_ignores_taker_side_for_fill"]
    assert report["summary_double_counts_entry_fees"]
    assert report["adverse_selection_columns_populated"]


def test_external_ml_artifact_has_within_candle_lookahead_risk():
    if not ML_REPO.exists():
        return
    report = audit_ml_framework(ML_REPO)
    assert report["aggregated_test_rows"] == 57379
    assert report["one_minute_within_bar_lookahead_risk"]
    assert report["one_hour_within_bar_lookahead_risk"]
    assert not report["executable_ask_pnl_reported"]
