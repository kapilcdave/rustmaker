import unittest

from idea_lab.oos_subcent_btc_tail_hedge import batch_taker_fee, max_drawdown


class SubcentBtcTailHedgeTests(unittest.TestCase):
    def test_batch_fee_rounds_once(self):
        self.assertEqual(batch_taker_fee(0.01, 100), 0.07)

    def test_drawdown(self):
        self.assertAlmostEqual(max_drawdown([2.0, -3.0, 1.0, -2.0]), 4.0)

    def test_empty_drawdown(self):
        self.assertEqual(max_drawdown([]), 0.0)


if __name__ == "__main__":
    unittest.main()
