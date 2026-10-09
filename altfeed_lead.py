#!/usr/bin/env python3
"""Does the consolidated spot feed lead the Kalshi 15M book, and the settlement index, and by how
much? Swept across bin sizes, with the sparsity guard enforced at every one.

This is D3 of `PREREG_altfeed_venue_race_20261007.md`, split out of `altfeed_score.py` because
the first attempt at it was **void** and the reason is worth carrying in one place.

`altfeed_score.py` reported, on the 10h09m capture, that the spot reference led the Kalshi mid by
-170..+710 ms at correlations of **0.002-0.014**, and led the settlement index at **0.012-0.045**.
The second number is the tell: the CF index is computed FROM those very venues, so a near-zero
correlation there cannot be a property of the market. It was the 10 ms grid. A forward-filled bin
with no new quote has a return of exactly zero, so on feeds updating ~1/s:

    bin      zero-return bins     spot vs index corr
    10 ms         87-96%               0.05-0.09
    250 ms        20-61%               0.29-0.48
    5 s           0.5-10%              0.84-0.96

So the correlation is real and large, and the estimator destroyed it. The cost of coarsening is
that the LEAD becomes sub-bin and collapses toward zero, which is the usual bind for asynchronous
sparse series. This script therefore reports the whole sweep instead of one number, and marks the
finest bin that clears the guard — the honest reading is "a lead of order X at the finest bin that
is measurable at all", never a single millisecond figure.

The Kalshi mid has a second, independent problem the index does not: it lives on a 1c (0.1c wing)
lattice, so a 5 bps spot move does not move it at all and its return series is both sparse AND
quantised. On the thin alts it barely moves at any resolution — BNB 2,996 and NEAR 2,235 touch
rows in TEN HOURS. Where that happens the sweep says so rather than producing a number, and the
race in `altfeed_score.py` (an event study, immune to all of this) is the only valid read.

Usage:
  python3 altfeed_lead.py --altfeed data/.../altfeed_*.csv.gz \
      --kalshi data/.../tape_*.csv.gz --index data/.../index_*.jsonl.gz \
      --exclude-venues crypto_com --json out.json
"""
from __future__ import annotations

import argparse
import glob
import json
import sys

import numpy as np

import altfeed_score as sc

BINS_US = (50_000, 100_000, 250_000, 500_000, 1_000_000, 2_000_000, 5_000_000)
MAX_LAG_US = 3_000_000
MIN_GRID = 500


def build(level_srcs, t0, t1, bin_us):
    """Median level across sources on a shared grid -> log returns. Sources are (recv, px)."""
    n = int((t1 - t0) // bin_us) + 1
    if n < MIN_GRID:
        return None, n
    rows = [sc.grid(r, p, t0, n, bin_us) for r, p in level_srcs]
    return sc.logret(np.nanmedian(np.vstack(rows), axis=0)), n


def sweep(a_srcs, b_srcs, t0, t1):
    """Lead of a over b at every bin size, finest-first, with the guard applied at each."""
    tried, best = [], None
    for bin_us in BINS_US:
        a, n = build(a_srcs, t0, t1, bin_us)
        if a is None:
            continue
        b, _ = build(b_srcs, t0, t1, bin_us)
        out = sc.xcorr_peak(
            a, b,
            max_lag=max(2, int(MAX_LAG_US // bin_us)),
            min_bins=100,
            bin_us=bin_us,
        )
        out["bin_ms"] = bin_us / 1000.0
        out["grid_bins"] = n
        tried.append(out)
        if best is None and np.isfinite(out["corr"]):
            best = out
    return {"finest_valid": best, "sweep": tried}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--altfeed", nargs="+", required=True)
    ap.add_argument("--kalshi", nargs="*", default=[])
    ap.add_argument("--index", nargs="*", default=[])
    ap.add_argument("--exclude-venues", nargs="*", default=[])
    ap.add_argument("--json")
    args = ap.parse_args()

    expand = lambda pats: [p for pat in pats for p in (glob.glob(pat) or [pat])]
    feeds, _ = sc.read_altfeed(expand(args.altfeed))
    if args.exclude_venues:
        drop = set(args.exclude_venues)
        feeds = {k: v for k, v in feeds.items() if k[1] not in drop}
        print(f"excluded {sorted(drop)}", file=sys.stderr)
    kalshi = sc.read_kalshi(expand(args.kalshi)) if args.kalshi else {}
    index, _, _, _ = sc.read_index(expand(args.index)) if args.index else ({}, [], {}, [])

    out = {"bins_ms": [b / 1000 for b in BINS_US], "max_zero_share": sc.MAX_ZERO_SHARE,
           "assets": {}}
    for asset in sorted({a for a, _ in feeds}):
        usd = [feeds[(a, v)] for (a, v) in feeds if a == asset and v in sc.USD_VENUES]
        if not usd:
            continue
        spot = [(x[:, 0], x[:, 2]) for x in usd]
        lo = min(x[0, 0] for x in usd)
        hi = max(x[-1, 0] for x in usd)
        rec = {"n_usd_venues": len(usd)}

        series = f"KX{asset}15M"
        if series in kalshi and len(kalshi[series]) > MIN_GRID:
            # `read_kalshi` returns an Nx2 array of (recv_us, mid_cents) and does NOT sort, and
            # this run concatenates 11 hourly tapes, so sort before any searchsorted.
            k = kalshi[series]
            k = k[np.argsort(k[:, 0], kind="stable")]
            # Compare only over the overlap, or the grid is mostly one-sided.
            t0, t1 = max(lo, k[0, 0]), min(hi, k[-1, 0])
            rec["vs_kalshi_mid"] = sweep(spot, [(k[:, 0], k[:, 1])], t0, t1)
            rec["vs_kalshi_mid"]["touch_rows"] = int(len(k))
            rec["vs_kalshi_mid"]["touch_per_s"] = float(len(k) / max((t1 - t0) / 1e6, 1))
            rec["vs_kalshi_mid"]["overlap_h"] = (t1 - t0) / 3.6e9

        idx_id = "BRTI" if asset == "BTC" else f"{asset}USD_RTI"
        if idx_id in index and len(index[idx_id]) > MIN_GRID:
            ix = index[idx_id]
            t0, t1 = max(lo, ix[0, 0]), min(hi, ix[-1, 0])
            rec["vs_index"] = sweep(spot, [(ix[:, 0], ix[:, 1])], t0, t1)
            rec["vs_index"]["index_id"] = idx_id
            rec["vs_index"]["overlap_h"] = (t1 - t0) / 3.6e9
        out["assets"][asset] = rec

    for comp, title in (("vs_kalshi_mid", "SPOT vs the KALSHI 15M MID"),
                        ("vs_index", "SPOT vs the SETTLEMENT INDEX")):
        print(f"\n=== {title} — finest bin clearing the <{sc.MAX_ZERO_SHARE:.0%}-zero guard")
        print(f"{'asset':6} {'overlap_h':>9} {'bin_ms':>7} {'corr':>7} {'lead_ms':>8} "
              f"{'zero%':>6} {'extra':>22}")
        for asset, rec in out["assets"].items():
            c = rec.get(comp)
            if not c:
                print(f"{asset:6} {'-':>9} {'no data':>7}")
                continue
            extra = (f"{c['touch_per_s']:.2f} touch/s" if comp == "vs_kalshi_mid"
                     else c.get("index_id", ""))
            b = c["finest_valid"]
            if b is None:
                worst = c["sweep"][-1] if c["sweep"] else {}
                print(f"{asset:6} {c['overlap_h']:9.2f} {'VOID':>7} {'-':>7} {'-':>8} "
                      f"{100*worst.get('zero_share', float('nan')):5.0f}% {extra:>22}")
                continue
            print(f"{asset:6} {c['overlap_h']:9.2f} {b['bin_ms']:7.0f} {b['corr']:7.3f} "
                  f"{b['lead_ms']:8.0f} {100*b['zero_share']:5.0f}% {extra:>22}")

    if args.json:
        with open(args.json, "w") as fh:
            json.dump(out, fh, indent=1, default=float)
        print(f"\nfull sweep -> {args.json}")


if __name__ == "__main__":
    main()
