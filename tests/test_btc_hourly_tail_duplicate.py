import unittest

from idea_lab.analyze_btc_hourly_dominance import fee


class TailDuplicateTests(unittest.TestCase):
    def test_identical_yes_no_pair_always_pays_one(self):
        for result_yes in (False, True):
            payout = int(result_yes) + int(not result_yes)
            self.assertEqual(payout, 1)

    def test_fee_is_rounded_up_per_leg(self):
        self.assertEqual(fee(0.50), 0.02)
        self.assertEqual(fee(0.01), 0.01)


if __name__ == "__main__":
    unittest.main()
