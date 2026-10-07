#!/usr/bin/env python3
"""Taker scalp on an `altfeed` capture: does crossing the stale Kalshi quote pay, on the
CONSOLIDATED multi-venue signal, after the fee?

This is `latency_arb.py` with one thing changed and everything else deliberately identical, so
the two numbers are comparable. `latency_arb.py` measured the same trade off ONE venue's feed
(Coinbase Exchange `ticker`) over a 12 h tape on 2026-09-27 and found **-1.0 to -3.0 c/ct after
fee at every threshold from 2 to 20 bps**, with the diagnosis that winning the race SELECTS the
quotes that correctly did not need to reprice. The open question that result leaves is whether a
single venue's trade-triggered channel was the weak part: on a thin altcoin it fires only on that
venue's own matches.

So the signal here is the engine's: the MEDIAN of the per-venue returns over `--window-ms`, across
venues whose last quote is no older than `--max-age-ms`, requiring at least `--min-venues` of
them. Identical to `fastspot::Consolidated::ret_bps`, including the rule that a venue with no
observation at or before the window start does not vote.

Everything else is held fixed, and that is the point:

* our receipt clock throughout, so no venue clock has to agree with another;
* we win the race iff the first change we SEE on the threatened side is later than
  `--create-us + --feed-us` after the signal;
* value is the Kalshi mid at +10 s and +60 s minus the price paid minus the taker fee
  `ceil(7*p*(1-p))` cents;
* one event per `--debounce-ms`.

This is a PAPER replay against the displayed touch: it is an upper bound on a real fill, never a
fill claim. Read it to reject, not to promote.

Usage:
  python3 altfeed_taker.py --altfeed data/altfeed/altfeed_*.csv.gz --kalshi data/tape_*.csv.gz
  python3 altfeed_taker.py ... --bps 2 5 10 20 --json out.json
"""
from __future__ import annotations

import argparse
import glob
import gzip
import json
import math
import sys
from collections import defaultdict

import numpy as np

USD_VENUES = {"cb_ex", "cb_adv", "kraken", "binance_us", "bitstamp", "crypto_com", "gemini"}


def fee_c(p: float) -> int:
    """Kalshi taker fee on one contract, cents, rounded UP — 1.75c at p=0.50, 0.15c at p=0.98."""
    return math.ceil(0.07 * p * (1 - p) * 100 - 1e-9)


def read_altfeed(paths):
    rows = defaultdict(list)
    for path in paths:
        with gzip.open(path, "rt") as fh:
            header = next(fh, "")
            if not header.startswith("src,"):
                sys.exit(f"{path}: not an altfeed capture")
            # A capture still being written has no end-of-stream marker; read up to the last
            # flush rather than refusing, and say so instead of silently shortening the window.
            try:
                for line in fh:
                    if line.startswith("#"):
                        continue
                    try:
                        src, asset, recv, _exch, bid, ask = line.rstrip("\n").split(",")
                        rows[(asset, src)].append((int(recv), (float(bid) + float(ask)) / 2))
                    except ValueError:
                        continue
            except EOFError:
                print(f"NOTE: {path} is still being written; scored to its last flush",
                      file=sys.stderr)
    out = {}
    for key, vals in rows.items():
        a = np.array(vals, dtype=float)
        out[key] = a[np.argsort(a[:, 0], kind="stable")]
    return out


def read_kalshi(paths):
    """-> {series: dict of parallel arrays over the series' touch rows, sorted by recv_us}."""
    rows = defaultdict(list)
    for path in paths:
        with gzip.open(path, "rt") as fh:
            next(fh, "")
            for line in fh:
                p = line.rstrip("\n").split(",")
                if len(p) < 8 or p[0] != "B":
                    continue
                try:
                    recv, ticker = int(p[1]), p[2]
                    bid, bsz, ask, asz = int(p[3]), int(p[4]), int(p[5]), int(p[6])
                except ValueError:
                    continue
                if bid <= 0 or ask <= 0:
                    continue
                rows[ticker.split("-")[0]].append((recv, ticker, bid, bsz, ask, asz))
    out = {}
    for series, vals in rows.items():
        vals.sort(key=lambda r: r[0])
        out[series] = {
            "t": np.array([v[0] for v in vals], dtype=np.int64),
            "tk": np.array([v[1] for v in vals], dtype=object),
            "bid": np.array([v[2] for v in vals], dtype=float),
            "bsz": np.array([v[3] for v in vals], dtype=float),
            "ask": np.array([v[4] for v in vals], dtype=float),
            "asz": np.array([v[5] for v in vals], dtype=float),
        }
    return out


CHUNK = 1_000_000


def consolidated_signal(venues, window_us, max_age_us, min_venues):
    """The engine's signal, evaluated at every quote arrival across all venues.

    Returns (t, med_bps, n_voting) sorted by t. At each arrival only venues with a fresh last
    quote AND an observation at or before t - window vote, and the result is their median.

    Chunked over time: a 12 h, 10-venue capture is ~13M arrival instants, and the full
    venue-by-instant return matrix would be ~1 GB held at once for no reason.
    """
    names = sorted(venues)
    if not names:
        return np.empty(0), np.empty(0), np.empty(0)
    ts = np.unique(np.concatenate([venues[v][:, 0] for v in names]))
    med = np.full(len(ts), np.nan)
    nvote = np.zeros(len(ts), dtype=int)
    for lo in range(0, len(ts), CHUNK):
        chunk = ts[lo : lo + CHUNK]
        ret = np.full((len(names), len(chunk)), np.nan)
        for i, v in enumerate(names):
            recv, px = venues[v][:, 0], venues[v][:, 1]
            last = np.searchsorted(recv, chunk, side="right") - 1
            base = np.searchsorted(recv, chunk - window_us, side="right") - 1
            ok = (last >= 0) & (base >= 0)
            ok[ok] &= (chunk[ok] - recv[last[ok]]) <= max_age_us
            ok[ok] &= px[base[ok]] > 0
            ret[i, ok] = (px[last[ok]] / px[base[ok]] - 1.0) * 10_000.0
        n = np.sum(np.isfinite(ret), axis=0)
        enough = n >= max(min_venues, 1)
        nvote[lo : lo + len(chunk)] = n
        if enough.any():
            med[lo : lo + len(chunk)][enough] = np.nanmedian(ret[:, enough], axis=0)
    return ts, med, nvote


def replay(asset, venues, k, threshold_bps, args):
    ts, med, nvote = consolidated_signal(
        venues, args.window_ms * 1000, args.max_age_ms * 1000, args.min_venues
    )
    if len(ts) == 0 or k is None:
        return []
    t, tk, bid, bsz, ask, asz = k["t"], k["tk"], k["bid"], k["bsz"], k["ask"], k["asz"]
    land_us = args.create_us + args.feed_us
    hit = np.flatnonzero(np.isfinite(med) & (np.abs(med) >= threshold_bps))
    out = []
    last_ev = -(10**18)
    for idx in hit:
        now = int(ts[idx])
        if now - last_ev < args.debounce_ms * 1000:
            continue
        last_ev = now
        # The market quoting most recently at the event: the series has two open at a time and the
        # tape carries no close time, so "most recently updated" is the proxy (same as
        # latency_arb.py, held fixed for comparability).
        i = int(np.searchsorted(t, now, side="right")) - 1
        if i < 0:
            continue
        this = tk[i]
        up = med[idx] > 0
        px0 = ask[i] if up else bid[i]
        sz0 = asz[i] if up else bsz[i]
        # First change on the side we would take, in the SAME market.
        j = i + 1
        while j < len(t) and (tk[j] != this or (ask[j] if up else bid[j]) == px0):
            j += 1
        if j >= len(t):
            continue  # the capture ends before the quote moved: no label, so no row
        lead_us = int(t[j]) - now

        def mid_at(tt):
            q = int(np.searchsorted(t, tt, side="right")) - 1
            while q >= 0 and tk[q] != this:
                q -= 1
            return (bid[q] + ask[q]) / 200.0 if q >= 0 else float("nan")

        p = px0 / 10_000.0
        pay_c = p * 100.0
        f = fee_c(p)
        v10, v60 = mid_at(now + 10_000_000), mid_at(now + 60_000_000)
        g10 = (v10 - pay_c if up else pay_c - v10) - f
        g60 = (v60 - pay_c if up else pay_c - v60) - f
        out.append({
            "asset": asset, "ticker": this, "t": now, "r_bps": float(med[idx]),
            "n_venues": int(nvote[idx]), "lead_ms": lead_us / 1000.0, "px_c": pay_c,
            "size_ct": sz0 / 100.0, "fee_c": f, "g10": g10, "g60": g60,
            "won": lead_us > land_us,
        })
    return out


def summarize(rows, threshold_bps, land_ms):
    def agg(sel):
        g10 = np.array([r["g10"] for r in sel], dtype=float)
        g60 = np.array([r["g60"] for r in sel], dtype=float)
        g10, g60 = g10[np.isfinite(g10)], g60[np.isfinite(g60)]
        se = lambda a: float(a.std(ddof=1) / np.sqrt(len(a))) if len(a) > 1 else float("nan")
        return {
            "n": len(sel),
            "g10_c": float(g10.mean()) if len(g10) else float("nan"), "g10_se": se(g10),
            "g60_c": float(g60.mean()) if len(g60) else float("nan"), "g60_se": se(g60),
            "fee_c": float(np.mean([r["fee_c"] for r in sel])) if sel else float("nan"),
            "size_ct_p50": float(np.median([r["size_ct"] for r in sel])) if sel else float("nan"),
        }

    won = [r for r in rows if r["won"]]
    leads = np.array([r["lead_ms"] for r in rows], dtype=float)
    return {
        "threshold_bps": threshold_bps,
        "events": len(rows),
        "assets": sorted({r["asset"] for r in rows}),
        "lead_ms": {f"p{q}": float(np.percentile(leads, q)) for q in (10, 25, 50, 75, 90)}
        if len(leads) else {},
        "won_race": len(won),
        "won_share": len(won) / len(rows) if rows else float("nan"),
        "land_ms": land_ms,
        "all": agg(rows),
        "won": agg(won),
        "won_by_asset": {
            a: agg([r for r in won if r["asset"] == a]) for a in sorted({r["asset"] for r in won})
        },
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--altfeed", nargs="+", required=True)
    ap.add_argument("--kalshi", nargs="+", required=True)
    ap.add_argument("--bps", nargs="+", type=float, default=[2.0, 5.0, 10.0, 20.0])
    ap.add_argument("--window-ms", type=int, default=1000, help="return window of the signal")
    ap.add_argument("--max-age-ms", type=int, default=2000, help="a staler venue does not vote")
    ap.add_argument("--min-venues", type=int, default=3)
    ap.add_argument("--debounce-ms", type=int, default=2000, help="one event per excursion")
    # Measured on the az2 box 2026-10-06 (FINDINGS_latency_az_ip / signing_and_transport):
    # create -> in book 2.7-3.0 ms, market-data feed one-way 5.7 ms. The September numbers were
    # 5.44 and 6.0; pass them explicitly to reproduce the older run.
    ap.add_argument("--create-us", type=int, default=2_900)
    ap.add_argument("--feed-us", type=int, default=5_700)
    ap.add_argument("--json")
    args = ap.parse_args()

    expand = lambda pats: [p for pat in pats for p in (glob.glob(pat) or [pat])]
    feeds = read_altfeed(expand(args.altfeed))
    kalshi = read_kalshi(expand(args.kalshi))
    if not feeds:
        sys.exit("no quotes in the altfeed capture")
    if not kalshi:
        sys.exit("no Kalshi touch rows: the tape must come from `kalshi-mm15 probe`")

    assets = sorted({a for a, _ in feeds})
    # Two captures that do not overlap in time produce "no events", which reads exactly like "no
    # signal". Refuse instead: a mis-paired tape is the cheapest way to get a false negative.
    f_lo = min(a[0, 0] for a in feeds.values())
    f_hi = max(a[-1, 0] for a in feeds.values())
    k_lo = min(k["t"][0] for k in kalshi.values())
    k_hi = max(k["t"][-1] for k in kalshi.values())
    overlap = (min(f_hi, k_hi) - max(f_lo, k_lo)) / 1e6
    if overlap <= 0:
        sys.exit(
            f"the altfeed capture and the Kalshi tape do not overlap: spot "
            f"{f_lo/1e6:.0f}..{f_hi/1e6:.0f}, Kalshi {k_lo/1e6:.0f}..{k_hi/1e6:.0f} (unix s). "
            "They have to be the same wall-clock window on the same box."
        )
    print(f"overlap: {overlap/60:.1f} min of common wall clock")
    land_ms = (args.create_us + args.feed_us) / 1000.0
    print(f"consolidated signal: median of >= {args.min_venues} venues, {args.window_ms} ms window, "
          f"{args.max_age_ms} ms freshness")
    print(f"we win the race iff the threatened quote survives {land_ms:.1f} ms "
          f"(create {args.create_us/1000:.1f} + feed {args.feed_us/1000:.1f})")
    print("PAPER against the displayed touch: an upper bound on a fill, not a fill.\n")

    report = {"args": vars(args), "thresholds": {}}
    for th in args.bps:
        rows = []
        for asset in assets:
            venues = {v: feeds[(a, v)] for (a, v) in feeds if a == asset}
            rows += replay(asset, venues, kalshi.get(f"KX{asset}15M"), th, args)
        if not rows:
            print(f">= {th:>5.1f} bps: no events")
            continue
        s = summarize(rows, th, land_ms)
        report["thresholds"][str(th)] = s
        print(f">= {th:>5.1f} bps: {s['events']:5d} events, quote still up {100*s['won_share']:4.0f}% "
              f"| won n={s['won']['n']:5d}  +10s {s['won']['g10_c']:+6.2f} ± {s['won']['g10_se']:.2f} c"
              f"  +60s {s['won']['g60_c']:+6.2f} ± {s['won']['g60_se']:.2f} c"
              f"  fee {s['won']['fee_c']:.2f} c  depth {s['won']['size_ct_p50']:.0f} ct")
        for a, g in s["won_by_asset"].items():
            if g["n"] >= 20:
                print(f"            {a:5} n={g['n']:5d}  +60s {g['g60_c']:+6.2f} ± {g['g60_se']:.2f} c")

    if args.json:
        with open(args.json, "w") as fh:
            json.dump(report, fh, indent=1, default=float)
        print(f"\nfull report -> {args.json}")


if __name__ == "__main__":
    main()
