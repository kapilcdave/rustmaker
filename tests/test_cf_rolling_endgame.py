from __future__ import annotations

import math
import unittest

import numpy as np

import analyze_cf_rolling_endgame as cf
from analyze_public_cf_live import choose_side


def synthetic_market(
    *,
    ticker: str = "TEST",
    close_ms: int = 100_000,
    label: int = 1,
    prints: list | None = None,
    probability: float = 0.90,
):
    market = object.__new__(cf.Market)
    market.asset = "ETH"
    market.ticker = ticker
    market.close_ms = close_ms
    market.target = 100.0
    market.label = label
    market.times = np.arange(0, close_ms + 1, 1_000, dtype=np.int64)
    market.values = np.arange(market.times.size, dtype=float)
    market.prints = sorted(prints or [], key=lambda x: x[0])
    market.probabilities = lambda source_index, seconds_left: {
        "B1": probability,
        "B2": probability,
        "sd": 1.0,
        "value": float(market.values[source_index]),
        "trend_shift": 0.0,
    }
    return market


class MarketCausalityTests(unittest.TestCase):
    def test_at_or_before_never_selects_future_sample(self):
        market = synthetic_market()
        self.assertEqual(market.at_or_before(10_999), 10)
        self.assertEqual(int(market.times[market.at_or_before(10_999)]), 10_000)

    def test_horizon_sd_does_not_change_when_future_values_change(self):
        market = synthetic_market(close_ms=2_500_000)
        index = market.at_or_before(2_000_000)
        before = market.horizon_sd(index, 10)
        market.values[index + 1 :] = 1e12
        after = market.horizon_sd(index, 10)
        self.assertEqual(before, after)


class PrintExecutionTests(unittest.TestCase):
    def test_requires_full_second_between_source_and_print(self):
        # At 41.999s the eligible source is 40s, not 41s.
        market = synthetic_market(
            prints=[(41_999_000, 50, 1, "yes")],
            probability=0.90,
        )
        trades = cf.print_upper_bound([market])
        self.assertTrue(trades)
        self.assertTrue(all(t["source_to_print_ms"] >= 1_000 for t in trades))
        self.assertTrue(all(t["source_to_print_ms"] == 1_999 for t in trades))

    def test_yes_and_no_entry_and_payout_conversion(self):
        yes_market = synthetic_market(
            ticker="YES",
            label=1,
            prints=[(50_000_000, 40, 2, "yes")],
            probability=0.95,
        )
        no_market = synthetic_market(
            ticker="NO",
            label=0,
            prints=[(50_000_000, 70, 2, "no")],
            probability=0.05,
        )
        trades = cf.print_upper_bound([yes_market, no_market])
        yes = next(t for t in trades if t["ticker"] == "YES")
        no = next(t for t in trades if t["ticker"] == "NO")
        self.assertAlmostEqual(yes["entry"], 0.40)
        self.assertAlmostEqual(no["entry"], 0.30)
        self.assertAlmostEqual(yes["pnl"], 1.0 - 0.40 - cf.taker_fee(0.40))
        self.assertAlmostEqual(no["pnl"], 1.0 - 0.30 - cf.taker_fee(0.30))

    def test_one_trade_per_market_arm_threshold(self):
        market = synthetic_market(
            prints=[
                (45_000_000, 20, 1, "yes"),
                (46_000_000, 10, 1, "yes"),
                (47_000_000, 5, 1, "yes"),
            ],
            probability=0.99,
        )
        trades = cf.print_upper_bound([market])
        keys = [(t["ticker"], t["arm"], t["threshold"]) for t in trades]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertEqual(len(keys), 2 * len(cf.THRESHOLDS))
        self.assertTrue(all(math.isclose(t["entry"], 0.20) for t in trades))


class ClusterBootstrapTests(unittest.TestCase):
    def test_resampling_unit_is_close_window(self):
        trades = [
            {"close_ms": 1, "pnl": 1.0},
            {"close_ms": 1, "pnl": 1.0},
            {"close_ms": 2, "pnl": -1.0},
        ]
        interval = cf.bootstrap_window_mean(trades, stress=0.0, iterations=2_000)
        # Window resampling can draw only the negative singleton window,
        # producing -1. Row resampling of three rows cannot do so this often
        # and has a different deterministic 2.5% endpoint.
        self.assertLessEqual(interval[0], -0.99)
        self.assertGreaterEqual(interval[1], 0.99)

    def test_equal_window_summary_does_not_overweight_multi_asset_window(self):
        trades = [
            {
                "arm": "X",
                "threshold": 0.02,
                "close_ms": 1,
                "pnl": 1.0,
                "asset": "ETH",
                "seconds_left": 1,
                "source_to_print_ms": 1_000,
                "print_count": 1,
            },
            {
                "arm": "X",
                "threshold": 0.02,
                "close_ms": 1,
                "pnl": 1.0,
                "asset": "SOL",
                "seconds_left": 1,
                "source_to_print_ms": 1_000,
                "print_count": 1,
            },
            {
                "arm": "X",
                "threshold": 0.02,
                "close_ms": 2,
                "pnl": -1.0,
                "asset": "ETH",
                "seconds_left": 1,
                "source_to_print_ms": 1_000,
                "print_count": 1,
            },
        ]
        report = cf.summarize_trades(trades, iterations=100, arms=("X",))["X"]["0.02"]
        self.assertAlmostEqual(report["mean_c"], 100 / 3)
        self.assertAlmostEqual(report["equal_window_mean_c"], 0.0)


class EmpiricalWalkForwardTests(unittest.TestCase):
    def test_current_and_same_market_rows_do_not_enter_training_count(self):
        rows = []
        for i in range(30):
            rows.append(
                {
                    "asset": "ETH",
                    "ticker": f"OLD{i:02d}",
                    "close_ms": i,
                    "seconds_left": 10,
                    "distance": 0.25,
                    "trend_shift": 1.0,
                    "label": 1,
                }
            )
        # Both rows belong to the same later market. The first prediction must
        # see exactly the 30 old markets, and the contradictory second row must
        # not update the cell until the whole market is finished.
        rows.extend(
            [
                {
                    "asset": "ETH",
                    "ticker": "CURRENT",
                    "close_ms": 100,
                    "seconds_left": 10,
                    "distance": 0.25,
                    "trend_shift": 1.0,
                    "label": 0,
                },
                {
                    "asset": "ETH",
                    "ticker": "CURRENT",
                    "close_ms": 100,
                    "seconds_left": 9,
                    "distance": 0.25,
                    "trend_shift": 1.0,
                    "label": 0,
                },
            ]
        )
        out = [r for r in cf.empirical_walkforward(rows) if r["ticker"] == "CURRENT"]
        self.assertEqual(len(out), 2)
        self.assertTrue(all(r["B3_train_n"] == 30 for r in out))
        self.assertTrue(all(math.isclose(r["B3"], 31 / 32) for r in out))

        later = {
            "asset": "ETH",
            "ticker": "LATER",
            "close_ms": 101,
            "seconds_left": 10,
            "distance": 0.25,
            "trend_shift": 1.0,
            "label": 1,
        }
        later_out = [
            r for r in cf.empirical_walkforward([*rows, later]) if r["ticker"] == "LATER"
        ]
        self.assertEqual(later_out[0]["B3_train_n"], 31)


class LiveBookTests(unittest.TestCase):
    def test_one_sided_book_is_still_executable(self):
        yes = choose_side(0.10, bid=0.0, ask=0.001)
        self.assertEqual(yes["side"], "yes")
        self.assertAlmostEqual(yes["entry"], 0.001)
        no = choose_side(0.10, bid=0.999, ask=1.0)
        self.assertEqual(no["side"], "no")
        self.assertAlmostEqual(no["entry"], 0.001)


if __name__ == "__main__":
    unittest.main()
