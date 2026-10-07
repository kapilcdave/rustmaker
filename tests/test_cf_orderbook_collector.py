import unittest

import collect_public_cf_orderbook as collector


class OrderbookCollectorTests(unittest.TestCase):
    def test_best_uses_highest_bid(self):
        self.assertEqual(
            collector.best([["0.1000", "2.00"], ["0.3000", "1.00"], ["0.2000", "4.00"]]),
            (0.3, 1.0),
        )

    def test_best_ignores_zero_size(self):
        self.assertEqual(
            collector.best([["0.9000", "0.00"], ["0.4000", "3.00"]]),
            (0.4, 3.0),
        )

    def test_empty_book(self):
        self.assertIsNone(collector.best([]))


if __name__ == "__main__":
    unittest.main()
