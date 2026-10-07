from idea_lab.audit_pm_us_exact_equivalence import check


def fixtures():
    pm = {
        "description": "60 BRTI prices are collected; the simple average is used.",
        "assetPriceTerms": {
            "indexSymbol": "BRTI",
            "horizon": "15m",
            "windowStart": "2026-10-06T16:00:00Z",
            "windowEnd": "2026-10-06T16:15:00Z",
            "priceToBeat": {"value": "85735.80"},
        },
    }
    kalshi = {
        "open_time": "2026-10-06T16:00:00Z",
        "close_time": "2026-10-06T16:15:00Z",
        "floor_strike": 85735.8,
        "rules_primary": "Uses the CF Bitcoin Real Time Index (BRTI).",
    }
    return pm, kalshi


def test_exact_metadata_passes():
    pm, kalshi = fixtures()
    assert check(pm, kalshi)["equivalent"]


def test_strike_or_window_mismatch_fails_closed():
    pm, kalshi = fixtures()
    pm["assetPriceTerms"]["priceToBeat"]["value"] = "85735.81"
    pm["assetPriceTerms"]["windowEnd"] = "2026-10-06T16:30:00Z"
    result = check(pm, kalshi)
    assert not result["equivalent"]
    assert "strike" in result["reasons"]
    assert "window_end" in result["reasons"]
