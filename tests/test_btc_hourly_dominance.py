import importlib.util
import pathlib
import unittest


P = pathlib.Path(__file__).parents[1] / "idea_lab" / "analyze_btc_hourly_dominance.py"
spec = importlib.util.spec_from_file_location("dominance", P)
dominance = importlib.util.module_from_spec(spec)
assert spec.loader
spec.loader.exec_module(dominance)


class DominanceTests(unittest.TestCase):
    def test_lower_strike_portfolio_floor(self):
        # Hourly YES event strictly contains 15m YES when L < K15.
        for hourly_yes, m15_yes in [(False, False), (True, False), (True, True)]:
            payout = int(hourly_yes) + int(not m15_yes)
            self.assertGreaterEqual(payout, 1)

    def test_upper_strike_portfolio_floor(self):
        # 15m YES strictly contains hourly YES when U >= K15.
        for hourly_yes, m15_yes in [(False, False), (False, True), (True, True)]:
            payout = int(m15_yes) + int(not hourly_yes)
            self.assertGreaterEqual(payout, 1)

    def test_fee_is_rounded_up_per_leg(self):
        self.assertEqual(dominance.fee(0.5), 0.02)
        self.assertEqual(dominance.fee(0.01), 0.01)
        self.assertEqual(dominance.fee(0.99), 0.01)

    def test_common_bars_never_forward_fills(self):
        a = [{"ts": 1, "a": 0.5}, {"ts": 2, "a": 0.6}]
        b = [{"ts": 2, "b": 0.4}, {"ts": 3, "b": 0.3}]
        out = dominance.common_bars(a, b)
        self.assertEqual([x[0] for x in out], [2])

    def test_expiration_comparison_preserves_small_altcoin_precision(self):
        self.assertTrue(dominance.same_expiration_value("0.1004097", 0.1004097))
        self.assertFalse(dominance.same_expiration_value("0.1004097", "0.1004098"))

    def test_index_name(self):
        self.assertEqual(dominance.index_name("CF Benchmarks' BRTI before close"), "BRTI")
        self.assertEqual(dominance.index_name("the SOLUSD_RTI before close"), "SOLUSD_RTI")


if __name__ == "__main__":
    unittest.main()
