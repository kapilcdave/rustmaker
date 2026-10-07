import unittest

from idea_lab.analyze_public_tail_duplicate_orderbook import fee


class PublicTailDuplicateOrderbookTests(unittest.TestCase):
    def test_fee_rounding(self):
        self.assertEqual(fee(0.5), 0.02)
        self.assertEqual(fee(0.01), 0.01)


if __name__ == "__main__":
    unittest.main()
