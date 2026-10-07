from idea_lab.analyze_external_quantfirm_wait3_favorite import signal


def test_waits_three_minutes_and_takes_first_rich_favorite():
    market = {
        "open_ts": 1000,
        "bars": [
            {"ts": 1120, "a": 0.80, "b": 0.79},
            {"ts": 1180, "a": 0.76, "b": 0.75},
            {"ts": 1240, "a": 0.31, "b": 0.30},
        ],
    }
    side, row, price = signal(market)
    assert side == "yes"
    assert row["ts"] == 1180
    assert price == 0.76


def test_uses_no_side_ask():
    market = {
        "open_ts": 1000,
        "bars": [{"ts": 1180, "a": 0.21, "b": 0.20}],
    }
    side, _, price = signal(market)
    assert side == "no"
    assert price == 0.80
