from idea_lab.analyze_external_sardine_model import CHECKPOINTS


def test_checkpoints_are_chronological():
    assert CHECKPOINTS == (720, 480, 300, 120)
