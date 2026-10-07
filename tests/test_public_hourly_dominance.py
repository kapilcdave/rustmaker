import importlib.util
import pathlib
import unittest


P = pathlib.Path(__file__).parents[1] / "idea_lab" / "analyze_public_hourly_dominance.py"
spec = importlib.util.spec_from_file_location("public_dominance", P)
mod = importlib.util.module_from_spec(spec)
assert spec.loader
spec.loader.exec_module(mod)


class PublicDominanceTests(unittest.TestCase):
    def test_yes_quote(self):
        self.assertEqual(
            mod.quote({"yes_ask_dollars": "0.2500", "yes_ask_size_fp": "3.00"}, "yes"),
            (0.25, 3.0),
        )

    def test_no_quote_uses_no_ask_not_one_minus_stale_yes_bid(self):
        market = {
            "no_ask_dollars": "0.3100",
            "yes_bid_dollars": "0.6800",
            "yes_bid_size_fp": "4.00",
        }
        self.assertEqual(mod.quote(market, "no"), (0.31, 4.0))

    def test_missing_size_is_not_executable(self):
        self.assertIsNone(mod.quote({"yes_ask_dollars": "0.25"}, "yes"))


if __name__ == "__main__":
    unittest.main()
