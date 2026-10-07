from idea_lab.analyze_pm_us_crossvenue_hedged_maker import (
    improve,
    kalshi_tick,
    opportunities,
    score,
)


def row(ts=1.0):
    return {
        "ts": ts,
        "slug": "window",
        "ticker": "ticker",
        "pm_age": 0.001,
        "pm_state": "MARKET_STATE_OPEN",
        "pm_bids": [[0.40, 10]],
        "pm_asks": [[0.45, 10]],
        "k_yes_bids": [[0.52, 10]],
        "k_no_bids": [[0.43, 10]],
    }


def test_tick_and_improvement():
    assert kalshi_tick(0.05) == 0.001
    assert kalshi_tick(0.50) == 0.01
    assert round(improve(0.40, 0.45, 0.01), 8) == 0.41
    assert improve(0.40, 0.41, 0.01) == 0.40


def test_pm_maker_hedge_margin_uses_taker_fee():
    found = {item["direction"]: item for item in opportunities(row())}
    item = found["make_pm_yes_hedge_k_no"]
    # 41c maker + 48c Kalshi NO hedge + 2c taker fee = 91c.
    assert round(item["margin"], 8) == 0.09


def test_persistent_signal_only_once_per_window():
    report = score([row(1.0), row(2.0), row(3.0)])
    assert report["persistent_candidate_windows"] == 1
    assert report["signals"][0]["next_sample_survives"] is True
