import unittest

from idea_lab.oos_subcent_no_rise import cheap_quote


class SubcentNoRiseTests(unittest.TestCase):
    def test_yes_cheap_quote(self):
        self.assertEqual(cheap_quote({"b": 0.008, "a": 0.009}), ("yes", 0.009))

    def test_no_cheap_quote(self):
        side, price = cheap_quote({"b": 0.991, "a": 0.992})
        self.assertEqual(side, "no")
        self.assertAlmostEqual(price, 0.009)

    def test_invalid_book(self):
        self.assertIsNone(cheap_quote({"b": None, "a": 0.009}))
        self.assertIsNone(cheap_quote({"b": 0.2, "a": 0.2}))


if __name__ == "__main__":
    unittest.main()
