from idea_lab.analyze_external_oribar_divergence import MIN_VOLUME, THRESHOLD


def test_frozen_thresholds():
    assert THRESHOLD == 0.65
    assert MIN_VOLUME == 10.0
