"""Score a `spotprobe` capture: which BTC feed shows a move first, on the box's receipt clock?

1. Exchange-to-receipt delay per feed (recv - exchange stamp; includes the venue's clock skew).
2. Lead/lag vs the feed the engine uses now (coinbase_ticker): cross-correlation of 25 ms-binned
   log returns at lags -400..+400 ms. A peak at NEGATIVE lag means the feed moves BEFORE Coinbase.
3. Move race: every 1 s Coinbase move >= 2 bps (the engine's pull threshold). For each other feed,
   when did it first show a same-direction move of >= 1 bp from its own price 1 s earlier? Median
   of (feed first - Coinbase first); negative = that feed would have fired the pull earlier.
4. Optional, with a live journal (B rows on the same box clock, Kalshi's own timestamps): same
   cross-correlation of each feed vs the KXBTC15M mid.

Usage: python3 spot_lead.py <spotprobe_*.csv.gz> [live_*.jsonl.gz]
"""
import gzip
import json
import sys
from collections import defaultdict

import numpy as np

BIN_US = 25_000
REF = "coinbase_ticker"


def read_probe(path):
    feeds = defaultdict(list)
    with gzip.open(path, "rt") as fh:
        try:
            next(fh)
            for line in fh:
                if line.startswith("#"):
                    continue
                f, recv, exch, px = line.rstrip("\n").split(",")
                feeds[f].append((int(recv), int(exch), float(px)))
        except (EOFError, ValueError):
            pass  # a capture still being written
    return {f: np.array(v) for f, v in feeds.items()}


def binned_logpx(recv_us, px, t0, n):
    """Last price at or before each bin edge (forward-filled)."""
    idx = np.searchsorted(recv_us, t0 + np.arange(n) * BIN_US, side="right") - 1
    out = np.full(n, np.nan)
    ok = idx >= 0
    out[ok] = np.log(px[idx[ok]])
    return out


def xcorr(a, b, max_lag):
    """corr(a[t], b[t+lag]) for lag in bins; a, b are return series."""
    res = {}
    for lag in range(-max_lag, max_lag + 1):
        if lag >= 0:
            x, y = a[:len(a) - lag], b[lag:]
        else:
            x, y = a[-lag:], b[:len(b) + lag]
        m = np.isfinite(x) & np.isfinite(y) & ((x != 0) | (y != 0))
        res[lag] = np.corrcoef(x[m], y[m])[0, 1] if m.sum() > 100 else np.nan
    return res


def kalshi_mid(path):
    ts, mid = [], []
    with gzip.open(path, "rt") as fh:
        try:
            for line in fh:
                if line.startswith('{"k":"B"'):
                    t, vt, b, _, a, _ = json.loads(line)["v"]
                    if t.startswith("KXBTC15M"):
                        ts.append(vt); mid.append((b + a) / 2)
        except (EOFError, json.JSONDecodeError):
            pass
    return np.array(ts), np.array(mid)


def main():
    feeds = read_probe(sys.argv[1])
    t0 = int(max(v[0, 0] for v in feeds.values())) + 5_000_000
    t1 = int(min(v[-1, 0] for v in feeds.values() if len(v) > 1000))
    n = (t1 - t0) // BIN_US
    print(f"{(t1 - t0) / 60e6:.1f} min common window\n")
    print(f"{'feed':<16}{'trades':>9}{'delay p50':>11}{'p90':>8}  xcorr peak vs {REF}")
    ref = np.diff(binned_logpx(feeds[REF][:, 0], feeds[REF][:, 2], t0, n))
    lagged = {}
    for f, v in sorted(feeds.items()):
        d = v[:, 0] / 1000 - v[:, 1]
        r = np.diff(binned_logpx(v[:, 0], v[:, 2], t0, n))
        xc = xcorr(r, ref, 16)
        best = max((k for k in xc if np.isfinite(xc[k])), key=lambda k: xc[k], default=None)
        lagged[f] = r
        peak = f"{best * BIN_US / 1000:+5.0f} ms (r={xc[best]:.2f})" if best is not None else "n/a"
        print(f"{f:<16}{len(v):>9}{np.median(d):>9.1f}ms{np.percentile(d, 90):>6.0f}ms  {peak}")

    # Move race on Coinbase 1 s moves >= 2 bps.
    lp = {f: binned_logpx(v[:, 0], v[:, 2], t0, n) for f, v in feeds.items()}
    w = 1_000_000 // BIN_US
    ret1 = lp[REF][w:] - lp[REF][:-w]
    first = np.where(np.abs(ret1) >= 2e-4)[0]
    events, last = [], -10 * w
    for i in first:
        if i - last > 2 * w:
            events.append(i + w)
        last = i
    print(f"\nmove race: {len(events)} Coinbase 1 s moves >= 2 bps (median lead of each feed, ms; negative = earlier)")
    for f in sorted(feeds):
        if f == REF:
            continue
        leads = []
        for e in events:
            sgn = np.sign(lp[REF][e] - lp[REF][e - w])
            lo = e - w
            base = lp[f][lo - w] if lo - w >= 0 else np.nan
            seg = lp[f][lo:e + w]
            hit = np.where(sgn * (seg - base) >= 1e-4)[0]
            ref_hit = np.where(sgn * (lp[REF][lo:e + w] - lp[REF][lo - w]) >= 1e-4)[0]
            if len(hit) and len(ref_hit) and np.isfinite(base):
                leads.append((hit[0] - ref_hit[0]) * BIN_US / 1000)
        if leads:
            print(f"  {f:<16} {np.median(leads):+7.0f} ms  (earlier in {np.mean(np.array(leads) < 0):.0%} of {len(leads)})")

    if len(sys.argv) > 2:
        kt, km = kalshi_mid(sys.argv[2])
        sel = (kt >= t0) & (kt < t0 + n * BIN_US)
        if sel.sum() > 1000:
            idx = np.searchsorted(kt[sel], t0 + np.arange(n) * BIN_US, side="right") - 1
            k = np.where(idx >= 0, km[sel][np.maximum(idx, 0)], np.nan)
            kr = np.diff(k)
            print("\nvs Kalshi KXBTC15M mid (venue clock): xcorr peak, negative = feed LEADS Kalshi")
            for f, r in sorted(lagged.items()):
                xc = xcorr(r, kr, 40)
                best = max((q for q in xc if np.isfinite(xc[q])), key=lambda q: xc[q], default=None)
                if best is not None:
                    print(f"  {f:<16} {best * BIN_US / 1000:+6.0f} ms (r={xc[best]:.3f})")


if __name__ == "__main__":
    main()
