import unittest

from idea_lab.oos_subcent_sweep import DC, PRICE, STRESS


class OosSubcentSweepTests(unittest.TestCase):
    def test_price_matches_decicent_level(self):
        self.assertEqual(DC, 9)
        self.assertAlmostEqual(PRICE, 0.009)

    def test_stressed_win_and_loss(self):
        self.assertAlmostEqual(PRICE - 0.0 - STRESS, 0.008)
        self.assertAlmostEqual(PRICE - 1.0 - STRESS, -0.992)


if __name__ == "__main__":
    unittest.main()
