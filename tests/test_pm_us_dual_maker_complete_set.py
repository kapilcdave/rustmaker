import pytest

from idea_lab.analyze_pm_us_dual_maker_complete_set import quotes, score


def row(ts, slug="s", pm_bid=0.40, pm_ask=0.42, ky=0.39, kn=0.58):
    return {
        "ts": ts,
        "slug": slug,
        "ticker": "K",
        "pm_state": "MARKET_STATE_OPEN",
        "pm_age": 0.0,
        "pm_bids": [[pm_bid, 10]],
        "pm_asks": [[pm_ask, 10]],
        "k_yes_bids": [[ky, 10]],
        "k_no_bids": [[kn, 10]],
    }


def test_quote_builds_complementary_pair():
    found = quotes(row(1.0))
    by_direction = {item["direction"]: item for item in found}
    candidate = by_direction["pm_yes_k_no"]
    assert candidate["pm_price"] == pytest.approx(0.41)
    assert candidate["kalshi_price"] == pytest.approx(0.59)
    assert round(candidate["margin"], 8) == 0.0


def test_strict_through_requires_both_legs():
    rows = [
        row(1.0, pm_bid=0.30, pm_ask=0.35, ky=0.34, kn=0.60),
        row(2.0, pm_bid=0.30, pm_ask=0.35, ky=0.34, kn=0.60),
        # PM YES ask goes below the 31c quote; K NO ask = 1-ky goes
        # below its 61c quote. Both are strict-through witnesses.
        row(3.0, pm_bid=0.29, pm_ask=0.30, ky=0.40, kn=0.55),
        row(4.0, slug="next"),
    ]
    report = score(rows)
    assert report["candidate_windows"] == 1
    assert report["paired_strict_through_windows"] == 1
    assert report["orphan_windows"] == 0


def test_touch_only_is_not_a_fill():
    rows = [
        row(1.0, pm_bid=0.30, pm_ask=0.35, ky=0.34, kn=0.60),
        row(2.0, pm_bid=0.30, pm_ask=0.35, ky=0.34, kn=0.60),
        # Asks merely touch the resting limits.
        row(3.0, pm_bid=0.30, pm_ask=0.31, ky=0.39, kn=0.55),
        row(4.0, slug="next"),
    ]
    report = score(rows)
    assert report["paired_strict_through_windows"] == 0
    assert report["orphan_windows"] == 0
