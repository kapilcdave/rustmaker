from idea_lab.analyze_external_reclaim import (
    btc_15m_closes,
    ema,
    score_trade,
    side_rows,
)


def test_ema_and_bucketed_closes_are_causal():
    spot = {
        ts: (100.0, float(ts), 99.0, 101.0)
        for ts in range(0, 3600, 60)
    }
    closes = btc_15m_closes(spot, 3600)
    assert closes == [840.0, 1740.0, 2640.0, 3540.0]
    assert ema([1.0, 2.0], 3) == 1.5


def test_no_side_ohlc_is_complemented():
    market = {
        "close_ts": 60,
        "bars": [
            {
                "ts": 60,
                "b": 0.30,
                "a": 0.32,
                "bh": 0.40,
                "al": 0.20,
                "post_close": False,
            }
        ],
    }
    row = side_rows(market, "no")[0]
    assert abs(row["ask_close"] - 0.70) < 1e-12
    assert abs(row["ask_low"] - 0.60) < 1e-12
    assert abs(row["bid_high"] - 0.80) < 1e-12


def test_target_is_only_credited_after_entry():
    rows = [
        {"minute": 1, "ask_close": 0.30, "bid_high": 0.30},
        {"minute": 2, "ask_close": 0.30, "bid_high": 0.30},
        {"minute": 3, "ask_close": 0.30, "bid_high": 0.30},
        {"minute": 4, "ask_close": 0.30, "bid_high": 0.30},
        {"minute": 5, "ask_close": 0.30, "bid_high": 0.30},
        {"minute": 6, "ask_close": 0.50, "bid_high": 0.95},
        {"minute": 7, "ask_close": 0.51, "bid_high": 0.60},
        {"minute": 8, "ask_close": 0.52, "bid_high": 0.91},
    ]
    signal = {
        "minute": 6,
        "ask_close": 0.50,
        "early_low": 0.20,
        "decision_close": 0.30,
        "signed_btc_ret": 0.0,
        "signed_ema_dist": -100.0,
        "all_rows": rows,
    }
    market = {"ticker": "T", "open_ts": 0, "result": "no"}
    lane = {
        "strategy": "S",
        "side": "yes",
        "entry": (0.35, 0.65),
        "target": 0.90,
    }
    close_trade = score_trade(market, lane, signal, "signal_close")
    delayed_trade = score_trade(market, lane, signal, "next_minute_1c")
    assert close_trade["target_hit"] is True
    assert delayed_trade["target_hit"] is True
    assert delayed_trade["entry"] == 0.52
