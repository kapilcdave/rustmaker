import unittest

from idea_lab.analyze_public_hourly_dominance_orderbook import ask, valid_meta


class PublicHourlyOrderbookTests(unittest.TestCase):
    def test_yes_ask_comes_from_no_bid(self):
        book = {"no_bid": 0.63, "no_bid_size": 7}
        self.assertEqual(ask(book, "yes"), (0.37, 7.0))

    def test_no_ask_comes_from_yes_bid(self):
        book = {"yes_bid": 0.22, "yes_bid_size": 5}
        self.assertEqual(ask(book, "no"), (0.78, 5.0))

    def test_meta_requires_same_index_and_valid_strikes(self):
        meta = {
            "asset": "BTC",
            "k15": 100.0,
            "lower_strike": 99.99,
            "upper_strike": 100.99,
            "m15_close_time": "x",
            "lower_close_time": "x",
            "upper_close_time": "x",
            "m15_rules_primary": "BRTI",
            "lower_rules_primary": "Bitcoin Real-Time Index (BRTI)",
            "upper_rules_primary": "BRTI",
        }
        self.assertTrue(valid_meta(meta))
        meta["upper_rules_primary"] = "ERTI"
        self.assertFalse(valid_meta(meta))


if __name__ == "__main__":
    unittest.main()
