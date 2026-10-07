import unittest

from idea_lab.collect_altspot_tilt_live import asks, decision_is_timely
from idea_lab.analyze_altspot_tilt_live import fee


class AltspotTiltLiveTests(unittest.TestCase):
    def test_direct_book_asks(self):
        yes, no, ys, ns = asks(
            {
                "yes_bid": 0.2,
                "yes_bid_size": 3,
                "no_bid": 0.7,
                "no_bid_size": 4,
            }
        )
        self.assertAlmostEqual(yes, 0.3)
        self.assertAlmostEqual(no, 0.8)
        self.assertEqual((ys, ns), (4, 3))

    def test_fee(self):
        self.assertEqual(fee(0.5), 0.02)

    def test_late_decision_is_rejected(self):
        self.assertTrue(decision_is_timely(72, 0, 1))
        self.assertFalse(decision_is_timely(80, 0, 1))


if __name__ == "__main__":
    unittest.main()
