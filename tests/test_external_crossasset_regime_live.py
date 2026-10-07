from idea_lab.collect_external_crossasset_regime_live import spot_series


def test_spot_series_symbol_is_importable():
    assert callable(spot_series)
