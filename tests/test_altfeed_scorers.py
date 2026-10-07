"""Unit tests for the altcoin fast-feed scorers.

The two scripts under test have no venue to check them against, so the contract they have to keep
is checked here on synthetic arrays:

* `altfeed_taker.consolidated_signal` must reproduce `fastspot::Consolidated::ret_bps` exactly —
  median of per-venue returns, a minimum venue count, a freshness cutoff, and the rule that a
  venue with no observation at or before the window start does not vote. If the Python replay and
  the Rust engine disagree about the signal, the replay is scoring a strategy nobody can run.
* `altfeed_score.race` must name the venue whose own observations crossed first, and must not
  count a venue that never saw the move as having lost.
"""
from __future__ import annotations

import unittest

import numpy as np

import altfeed_score as sc
import altfeed_taker as tk


def series(points):
    """[(recv_us, px)] -> the (recv, px) array the taker replay consumes."""
    return np.array(points, dtype=float)


def feed_rows(points):
    """[(recv_us, px)] -> the (recv, exch, mid) array the race consumes."""
    return np.array([(t, 0, p) for t, p in points], dtype=float)


class Args:
    """The replay's knobs, as `argparse` would hand them over."""

    def __init__(self, **kw):
        self.window_ms = 1000
        self.max_age_ms = 2000
        self.min_venues = 3
        self.debounce_ms = 2000
        self.create_us = 2_900
        self.feed_us = 5_700
        self.__dict__.update(kw)


S = 1_000_000  # one second in µs


class ConsolidatedSignal(unittest.TestCase):
    def test_median_of_per_venue_returns_ignores_the_basis(self):
        # Three venues at three very different levels, each up exactly 10 bps over the second.
        venues = {
            "cb_ex": series([(0, 2000.0), (S, 2002.0)]),
            "okx": series([(0, 2010.0), (S, 2012.01)]),
            "hyperliquid": series([(0, 1990.0), (S, 1991.99)]),
        }
        t, med, n = tk.consolidated_signal(venues, S, 2 * S, 3)
        at = med[t == S][0]
        self.assertAlmostEqual(at, 10.0, places=3)
        self.assertEqual(n[t == S][0], 3)

    def test_an_outlier_venue_cannot_carry_the_median(self):
        venues = {
            "a": series([(0, 100.0), (S, 100.01)]),   # +1 bp
            "b": series([(0, 100.0), (S, 100.02)]),   # +2 bps
            "c": series([(0, 100.0), (S, 130.0)]),    # +3000 bps, a bad print
        }
        _, med, _ = tk.consolidated_signal(venues, S, 2 * S, 3)
        self.assertAlmostEqual(np.nanmax(med), 2.0, places=3)

    def test_a_venue_without_a_base_in_the_window_does_not_vote(self):
        venues = {
            "a": series([(0, 100.0), (S, 100.1)]),
            "b": series([(0, 100.0), (S, 100.1)]),
            # Connected halfway through: its 500 bps "move" is one it never saw the start of.
            "late": series([(int(0.9 * S), 50.0), (S, 52.5)]),
        }
        t, med, n = tk.consolidated_signal(venues, S, 2 * S, 3)
        self.assertEqual(n[t == S][0], 2, "the late venue must not vote")
        self.assertTrue(np.isnan(med[t == S][0]), "two voters is below the minimum of three")
        # With the minimum lowered to two, the median is the two real venues, not the outlier.
        _, med2, _ = tk.consolidated_signal(venues, S, 2 * S, 2)
        self.assertAlmostEqual(med2[t == S][0], 10.0, places=3)

    def test_chunking_over_time_is_boundary_exact(self):
        """The chunking exists only to bound memory on a 12 h capture; it must not move a number."""
        rng = np.random.default_rng(1)
        venues = {}
        for v in ("a", "b", "c", "d"):
            t = np.sort(rng.choice(np.arange(0, 20 * S, 1000), 900, replace=False)).astype(float)
            px = 100 * np.exp(np.cumsum(rng.standard_normal(len(t)) * 1e-4))
            venues[v] = np.column_stack([t, px])
        whole = tk.consolidated_signal(venues, S, 2 * S, 3)
        original = tk.CHUNK
        try:
            tk.CHUNK = 37  # many boundaries, including inside runs of events
            chunked = tk.consolidated_signal(venues, S, 2 * S, 3)
        finally:
            tk.CHUNK = original
        for a, b, name in zip(whole, chunked, ("t", "med", "n")):
            np.testing.assert_array_equal(
                np.nan_to_num(a, nan=-9e9), np.nan_to_num(b, nan=-9e9), err_msg=name
            )
        self.assertGreater(int(np.isfinite(whole[1]).sum()), 1000, "the fixture must exercise it")

    def test_a_stale_venue_stops_voting(self):
        venues = {
            "a": series([(0, 100.0), (10 * S, 100.1)]),
            "b": series([(0, 100.0), (10 * S, 100.1)]),
            # Last heard from at t=1s; at t=10s it is 9 s stale against a 2 s cutoff.
            "stale": series([(0, 100.0), (S, 100.1)]),
        }
        t, _, n = tk.consolidated_signal(venues, S, 2 * S, 1)
        self.assertEqual(n[t == 10 * S][0], 2)


class Fee(unittest.TestCase):
    def test_the_fee_is_maximal_where_the_mispricing_is_expressible(self):
        # Wall 1: 1.75c at the middle, 0.15c in the tail. Rounded up, in whole cents.
        self.assertEqual(tk.fee_c(0.50), 2)
        self.assertEqual(tk.fee_c(0.98), 1)
        self.assertEqual(tk.fee_c(0.99), 1)


class Replay(unittest.TestCase):
    """A rising spot makes OUR ask stale, so the trade is to lift the Kalshi ASK."""

    def kalshi(self, rows):
        rows = sorted(rows, key=lambda r: r[0])
        return {
            "t": np.array([r[0] for r in rows], dtype=np.int64),
            "tk": np.array(["KXETH15M-A" for _ in rows], dtype=object),
            "bid": np.array([r[1] for r in rows], dtype=float),
            "bsz": np.array([100.0] * len(rows), dtype=float),
            "ask": np.array([r[2] for r in rows], dtype=float),
            "asz": np.array([500.0] * len(rows), dtype=float),
        }

    def up_move(self):
        """Three venues flat for a second, then all up 20 bps at t = 1 s."""
        return {
            v: series([(0, 100.0), (int(0.5 * S), 100.0), (S, 100.2)])
            for v in ("cb_ex", "kraken", "okx")
        }

    def test_a_rising_signal_lifts_the_ask_and_pays_the_fee_at_that_price(self):
        # Kalshi ask 4000 (40c) at the event, and the quote does not move until +1 s, so we win
        # the race. The mid then settles at 50c, worth +10c against a 40c purchase.
        k = self.kalshi([(0, 3900, 4000), (2 * S, 4900, 5100), (70 * S, 4900, 5100)])
        rows = tk.replay("ETH", self.up_move(), k, 10.0, Args())
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertTrue(r["won"], "a quote that lasted 1 s survives our 8.6 ms")
        self.assertAlmostEqual(r["px_c"], 40.0)
        self.assertEqual(r["fee_c"], tk.fee_c(0.40))
        self.assertAlmostEqual(r["g60"], 50.0 - 40.0 - tk.fee_c(0.40))
        self.assertEqual(r["n_venues"], 3)

    def test_losing_the_race_is_labelled_by_the_landing_time(self):
        # The ask moves 1 ms after the signal: inside our 8.6 ms, so we never get that price.
        k = self.kalshi([(0, 3900, 4000), (S + 1_000, 4900, 5100), (70 * S, 4900, 5100)])
        rows = tk.replay("ETH", self.up_move(), k, 10.0, Args())
        self.assertEqual(len(rows), 1)
        self.assertFalse(rows[0]["won"])
        self.assertAlmostEqual(rows[0]["lead_ms"], 1.0)

    def test_a_falling_signal_hits_the_bid(self):
        down = {
            v: series([(0, 100.0), (int(0.5 * S), 100.0), (S, 99.8)])
            for v in ("cb_ex", "kraken", "okx")
        }
        k = self.kalshi([(0, 4000, 4100), (2 * S, 3000, 3100), (70 * S, 3000, 3100)])
        rows = tk.replay("ETH", down, k, 10.0, Args())
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertAlmostEqual(r["px_c"], 40.0, msg="a short sells the bid")
        # Sold at 40c, mid falls to 30.5c: +9.5c gross.
        self.assertAlmostEqual(r["g60"], 40.0 - 30.5 - tk.fee_c(0.40))

    def test_debounce_keeps_one_event_per_excursion(self):
        # A move that stays above threshold for several ticks is one trade, not five.
        pts = [(0, 100.0), (int(0.5 * S), 100.0)]
        pts += [(S + i * 100_000, 100.2 + i * 0.01) for i in range(6)]
        venues = {v: series(pts) for v in ("cb_ex", "kraken", "okx")}
        k = self.kalshi([(0, 3900, 4000), (10 * S, 4900, 5100), (70 * S, 4900, 5100)])
        rows = tk.replay("ETH", venues, k, 10.0, Args(debounce_ms=2000))
        self.assertEqual(len(rows), 1)

    def test_an_event_with_no_later_quote_change_is_dropped_not_scored(self):
        # The capture ends while the quote is still up: there is no label, so there is no row.
        k = self.kalshi([(0, 3900, 4000)])
        self.assertEqual(tk.replay("ETH", self.up_move(), k, 10.0, Args()), [])


class XcorrPeak(unittest.TestCase):
    """The FFT form must equal the obvious loop form. It replaced the loop only for speed."""

    @staticmethod
    def loop_peak(a, b, max_lag, min_bins):
        best = (float("nan"), float("nan"), 0)
        for lag in range(-max_lag, max_lag + 1):
            if lag >= 0:
                x, y = a[: len(a) - lag], b[lag:]
            else:
                x, y = a[-lag:], b[: len(b) + lag]
            m = np.isfinite(x) & np.isfinite(y) & ((x != 0) | (y != 0))
            if int(m.sum()) < min_bins:
                continue
            sx, sy = x[m], y[m]
            if sx.std() == 0 or sy.std() == 0:
                continue
            c = float(np.corrcoef(sx, sy)[0, 1])
            if not np.isfinite(best[0]) or c > best[0]:
                best = (c, lag, int(m.sum()))
        return best

    def test_fft_form_matches_the_loop_on_a_sparse_lagged_series(self):
        rng = np.random.default_rng(7)
        n, lag_true = 4000, -13
        a = rng.standard_normal(n)
        a[rng.random(n) < 0.8] = 0.0  # sparse, like a thin altcoin's bins
        b = np.zeros(n)
        b[: n + lag_true] = a[-lag_true:]  # b[t] = a[t - lag_true]
        b += 0.05 * rng.standard_normal(n)
        for min_bins in (10, 200):
            got = sc.xcorr_peak(a, b, max_lag=60, min_bins=min_bins)
            want = self.loop_peak(a, b, 60, min_bins)
            self.assertAlmostEqual(got["corr"], want[0], places=6, msg=f"min_bins={min_bins}")
            self.assertEqual(got["lead_ms"], want[1] * sc.BIN_US / 1000.0)
            self.assertEqual(got["n_bins"], want[2])
        # The construction above makes `a` the delayed one, so `b` leads: a negative lead for a.
        self.assertLess(sc.xcorr_peak(a, b, max_lag=60, min_bins=10)["lead_ms"], 0)

    def test_a_leading_series_reports_a_positive_lead(self):
        # b is a delayed copy of a by 5 bins, so a LEADS b by 5 bins = 50 ms.
        rng = np.random.default_rng(3)
        a = rng.standard_normal(2000)
        b = np.concatenate([np.zeros(5), a[:-5]])
        out = sc.xcorr_peak(a, b, max_lag=40, min_bins=10)
        self.assertAlmostEqual(out["lead_ms"], 5 * sc.BIN_US / 1000.0)
        self.assertGreater(out["corr"], 0.99)

    def test_nothing_measurable_is_nan_not_zero(self):
        out = sc.xcorr_peak(np.zeros(500), np.zeros(500), max_lag=10)
        self.assertTrue(np.isnan(out["corr"]))
        self.assertEqual(out["n_bins"], 0)


class Race(unittest.TestCase):
    def test_the_first_venue_to_cross_half_the_move_wins(self):
        # Reference rises 20 bps over the second ending at t = 1 s.
        ref_recv = np.array([0, int(0.5 * S), S], dtype=float)
        ref_px = np.array([100.0, 100.0, 100.2], dtype=float)
        venues = {
            # Crosses +10 bps (half of 20) at 0.60 s — the winner.
            "fast": feed_rows([(0, 100.0), (int(0.60 * S), 100.15), (S, 100.2)]),
            # Crosses at 0.70 s, 100 ms behind.
            "slow": feed_rows([(0, 100.0), (int(0.70 * S), 100.15), (S, 100.2)]),
            # Never moves: it saw the event and did not cross.
            "flat": feed_rows([(0, 100.0), (S, 100.0)]),
            # Only connected after the window opened: it cannot have missed what it never saw.
            "late": feed_rows([(int(0.99 * S), 100.0), (S, 100.2)]),
        }
        out = sc.race(ref_recv, ref_px, venues, threshold_bps=10.0)
        self.assertEqual(out["_events"], 1)
        self.assertEqual(out["_scored"], 1)
        self.assertEqual(out["fast"]["first"], 1)
        self.assertEqual(out["slow"]["first"], 0)
        self.assertAlmostEqual(out["slow"]["behind_winner_ms_p50"], 100.0, places=0)
        self.assertEqual(out["flat"]["crossed"], 0)
        self.assertEqual(out["flat"]["saw_event"], 1)
        self.assertEqual(out["late"]["saw_event"], 0, "a venue with no base must not be scored")

    def test_too_few_events_is_reported_as_underpowered(self):
        ref_recv = np.array([0, S], dtype=float)
        ref_px = np.array([100.0, 100.2], dtype=float)
        venues = {"a": feed_rows([(0, 100.0), (S, 100.2)]),
                  "b": feed_rows([(0, 100.0), (S, 100.2)])}
        out = sc.race(ref_recv, ref_px, venues, threshold_bps=10.0)
        self.assertFalse(out["_enough"], "one event cannot rank venues")


if __name__ == "__main__":
    unittest.main()
