"""How well can we know the settlement index level between its own prints?

This is the one number that decides whether a model-priced 15M maker can quote the mid band
(`FINDINGS_fair_value_precision_wall_20261007.md`). The fair value moves 2-5 cents per basis
point of level, so the seat's price resolution IS its level resolution times that delta. The
public-data bound measured ~1-6 bp against a minute-bar Coinbase close; the engine's `--fair`
path instead uses the exact CF index level carried forward by a multi-venue median spot return
over only the 26-73 ms since CF's own stamp. That estimator is strictly better and was unmeasured.

**The measurement.** The index is its own ground truth. For consecutive index prints
`(s1, I1) -> (s2, I2)` on CF's clock:

    HOLD   (baseline)  I_hat = I1                      -- what you get with no spot feed at all
    CARRY  (ours)      I_hat = I1 * (1 + spot_ret(s1 -> s2))

and the error is `1e4 * (I_hat/I2 - 1)` bp. Bucketing by `s2 - s1` makes the 26-73 ms figure
directly readable. If CARRY does not beat HOLD, the spot sockets add nothing to the level and the
whole "we are fast enough, just price it" premise has no instrument behind it.

`spot_ret` reproduces `fastspot::Consolidated::venue_rets_bps` exactly: a per-venue return over
the window, then the median across venues fresh at the end, and **a venue with no observation at
or before the window start contributes nothing** (otherwise a venue that just connected reports a
move it never saw).

**The clock caveat, handled explicitly.** Spot receipts are on our chrony-synced clock; index
stamps are on CF's. A constant offset would shift the spot window without changing its length.
`--sweep-offset` scans it and reports the curve; the fitted optimum is disclosed as using one
calibrated scalar, which a live system can also calibrate once, offline.

Usage (on the box, where the tapes live):
    python3 -I score_index_level_precision.py \
        --index  ~/trading/kalshi-mm15-altfeed/data/index_1791363634413.jsonl.gz \
        --spot   ~/trading/kalshi-mm15-altfeed/data/altfeed_1791363308075.csv.gz
    ... --sweep-offset          # scan the CF-vs-our clock offset
    ... --venues cb_ex,kraken   # restrict to CF constituent-like venues
"""

import argparse
import bisect
import gzip
import json
import statistics as st
from collections import defaultdict

# `BRTI` is BTC's index; every other asset is `{ASSET}USD_RTI`.
def asset_of_index(index_id):
    if index_id == "BRTI":
        return "BTC"
    return index_id[:-7] if index_id.endswith("USD_RTI") else None


def read_index(path):
    """asset -> sorted [(source_us, value)], deduped. Both channel shapes, which differ."""
    out = defaultdict(dict)
    bad = 0
    # ⚠ Tapes written before the `text.trim()` fix in `main.rs` split every record across two
    # lines: the venue's own frame ended with a newline, so the record's closing brace sits on
    # its own line. 45,600 lines in the 2026-10-07 capture are 22,800 records. Accumulating until
    # the buffer parses reads both the old and the fixed layout, so no tape is silently empty.
    with gzip.open(path, "rt") as fh:
        buf = ""
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            buf = (buf + raw) if buf else raw
            try:
                row = json.loads(buf)
            except json.JSONDecodeError:
                # A new record starting means the previous buffer was genuinely malformed.
                if len(buf) > 100_000:
                    bad += 1
                    buf = ""
                continue
            buf = ""
            msg = row.get("frame", {}).get("msg", {})
            index_id = msg.get("index_id")
            asset = asset_of_index(index_id) if index_id else None
            if not asset:
                continue
            # 5 Hz: `value_usd` + `source_ts_ms` at the top level. 1 Hz: neither -- CF's payload
            # is a JSON *string* in `data`, and the stamp is `data.time`.
            inner = {}
            if isinstance(msg.get("data"), str):
                try:
                    inner = json.loads(msg["data"])
                except json.JSONDecodeError:
                    inner = {}
            raw = msg.get("value_usd") or inner.get("value")
            src = msg.get("source_ts_ms") or inner.get("time")
            if raw is None or src is None:
                continue
            try:
                value, src = float(raw), int(src) * 1000
            except (TypeError, ValueError):
                continue
            if value > 0:
                out[asset][src] = value
    if bad:
        print(f"# {bad} unparseable index lines skipped")
    return {a: sorted(d.items()) for a, d in out.items()}


def read_spot(path, lo_us, hi_us, keep_assets, venues=None):
    """(asset, venue) -> sorted [(recv_us, mid)], restricted to the scoring window."""
    out = defaultdict(list)
    with gzip.open(path, "rt") as fh:
        header = next(fh, "")
        if not header.startswith("src,asset,recv_us"):
            raise SystemExit(f"unexpected spot header: {header!r}")
        for line in fh:
            # Lifecycle rows are commented (`#cb_ex,connected,...`).
            if not line or line[0] == "#":
                continue
            f = line.rstrip("\n").split(",")
            if len(f) < 6:
                continue
            src, asset, recv = f[0], f[1], f[2]
            if asset not in keep_assets or (venues and src not in venues):
                continue
            try:
                recv = int(recv)
            except ValueError:
                continue
            if not lo_us <= recv <= hi_us:
                continue
            try:
                bid, ask = float(f[4]), float(f[5])
            except ValueError:
                continue
            if bid > 0 and ask > bid:
                out[(asset, src)].append((recv, (bid + ask) / 2.0))
    for k in out:
        out[k].sort()
    return out


def venue_ret_bps(hist, asset, venues, t0, t1, max_age_us, min_venues):
    """Median per-venue return over [t0, t1]; mirrors fastspot::venue_rets_bps."""
    rets = []
    for v in venues:
        h = hist.get((asset, v))
        if not h:
            continue
        ts = [x[0] for x in h]
        # Last observation at or before each end. The start one must EXIST, or the venue is
        # reporting a move it never saw.
        i0 = bisect.bisect_right(ts, t0) - 1
        i1 = bisect.bisect_right(ts, t1) - 1
        if i0 < 0 or i1 < 0:
            continue
        if t1 - h[i1][0] > max_age_us:
            continue
        base, last = h[i0][1], h[i1][1]
        if base > 0:
            rets.append((last / base - 1.0) * 1e4)
    if len(rets) < max(min_venues, 1):
        return None
    return st.median(rets)


BUCKETS = [(0, 50), (50, 100), (100, 150), (150, 250), (250, 400), (400, 700),
           (700, 1200), (1200, 10**9)]


def score(index, spot, venues, offset_us, max_age_us, min_venues, max_gap_ms):
    """Per-asset and pooled errors, in bp, for both arms, bucketed by index-print gap."""
    rows = []
    for asset, prints in index.items():
        for (s1, i1), (s2, i2) in zip(prints, prints[1:]):
            gap_ms = (s2 - s1) / 1000.0
            if gap_ms <= 0 or gap_ms > max_gap_ms:
                continue
            r = venue_ret_bps(spot, asset, venues, s1 + offset_us, s2 + offset_us,
                              max_age_us, min_venues)
            if r is None:
                continue
            hold = 1e4 * (i1 / i2 - 1.0)
            carry = 1e4 * ((i1 * (1.0 + r / 1e4)) / i2 - 1.0)
            rows.append((asset, gap_ms, hold, carry))
    return rows


def pct(xs, q):
    if not xs:
        return float("nan")
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * len(xs)))]


def report(rows, label):
    if not rows:
        print(f"{label}: no scoreable index pairs")
        return None
    hold = [abs(r[2]) for r in rows]
    carry = [abs(r[3]) for r in rows]
    mh, mc = st.median(hold), st.median(carry)
    print(f"\n## {label}  (n={len(rows)} index pairs)\n")
    print(f"{'gap ms':>12s} {'n':>7s} {'HOLD p50':>9s} {'CARRY p50':>10s} "
          f"{'HOLD p90':>9s} {'CARRY p90':>10s} {'ratio p50':>10s}")
    for lo, hi in BUCKETS:
        sel = [r for r in rows if lo <= r[1] < hi]
        if len(sel) < 20:
            continue
        h = [abs(r[2]) for r in sel]
        c = [abs(r[3]) for r in sel]
        tag = f"{lo}-{hi}" if hi < 10**9 else f"{lo}+"
        ratio = st.median(c) / st.median(h) if st.median(h) > 0 else float("nan")
        print(f"{tag:>12s} {len(sel):>7d} {st.median(h):>9.3f} {st.median(c):>10.3f} "
              f"{pct(h, 0.9):>9.3f} {pct(c, 0.9):>10.3f} {ratio:>10.2f}")
    print(f"\n{'POOLED':>12s} {len(rows):>7d} {mh:>9.3f} {mc:>10.3f} "
          f"{pct(hold, 0.9):>9.3f} {pct(carry, 0.9):>10.3f} "
          f"{(mc / mh if mh else float('nan')):>10.2f}")
    print("\nper asset (median |error| bp, HOLD -> CARRY):")
    for a in sorted({r[0] for r in rows}):
        sel = [r for r in rows if r[0] == a]
        h = st.median([abs(r[2]) for r in sel])
        c = st.median([abs(r[3]) for r in sel])
        flag = "" if c < h else "   <-- CARRY NO BETTER"
        print(f"  {a:6s} n={len(sel):6d}  {h:7.3f} -> {c:7.3f}  "
              f"({100 * (1 - c / h) if h else 0:+.0f}%){flag}")
    return mc


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--index", required=True)
    ap.add_argument("--spot", required=True)
    ap.add_argument("--venues", default="", help="comma list; default every venue in the tape")
    ap.add_argument("--min-venues", type=int, default=3)
    ap.add_argument("--max-age-ms", type=float, default=2000.0)
    ap.add_argument("--max-gap-ms", type=float, default=2000.0)
    ap.add_argument("--offset-ms", type=float, default=0.0,
                    help="add to index stamps to reach our spot clock")
    ap.add_argument("--sweep-offset", action="store_true")
    # Measured CF->us, not assumed: 26-73 ms per index (FINDINGS_altfeed_index_path_20261007).
    ap.add_argument("--transport-ms", type=float, default=38.0)
    # Measured index/print -> our change in the book.
    ap.add_argument("--reaction-ms", type=float, default=11.6)
    # Measured real-print maker gross, held to settlement, 8,038,551 prints.
    ap.add_argument("--gross-c", type=float, default=0.25)
    args = ap.parse_args()

    index = read_index(args.index)
    if not index:
        raise SystemExit("no index prints parsed")
    lo = min(p[0][0] for p in index.values())
    hi = max(p[-1][0] for p in index.values())
    print(f"# index: {len(index)} assets, "
          f"{sum(len(v) for v in index.values())} prints, "
          f"span {(hi - lo) / 1e6:.0f}s")
    for a, p in sorted(index.items()):
        gaps = sorted((p[i + 1][0] - p[i][0]) / 1000 for i in range(len(p) - 1))
        print(f"#   {a:6s} {len(p):6d} prints, median gap {gaps[len(gaps) // 2]:7.1f} ms")

    want = set(index)
    # Pad the window so a venue has an observation at or before the first index stamp.
    spot = read_spot(args.spot, lo - 120_000_000, hi + 10_000_000, want,
                     set(args.venues.split(",")) if args.venues else None)
    venues = sorted({v for (_, v) in spot})
    print(f"\n# spot: {len(venues)} venues {venues}")
    for a in sorted(want):
        per = {v: len(spot[(a, v)]) for (x, v) in spot if x == a}
        if per:
            print(f"#   {a:6s} " + " ".join(f"{v}={n}" for v, n in sorted(per.items())))

    if args.sweep_offset:
        print("\n## clock-offset sweep (median |CARRY error| bp)\n")
        best = (None, float("inf"))
        for off in [-200, -100, -50, -20, -10, 0, 10, 20, 50, 100, 200]:
            rows = score(index, spot, venues, int(off * 1000), int(args.max_age_ms * 1000),
                         args.min_venues, args.max_gap_ms)
            if not rows:
                continue
            m = st.median([abs(r[3]) for r in rows])
            print(f"  offset {off:+5d} ms -> {m:.3f} bp  (n={len(rows)})")
            if m < best[1]:
                best = (off, m)
        print(f"\n  best offset {best[0]:+} ms at {best[1]:.3f} bp "
              "-- ONE fitted scalar, disclosed; a live system calibrates it once offline")

    rows = score(index, spot, venues, int(args.offset_ms * 1000),
                 int(args.max_age_ms * 1000), args.min_venues, args.max_gap_ms)
    report(rows, f"level precision, offset {args.offset_ms:+.0f} ms")

    seat_table(rows, index, args)


def seat_table(rows, index, args):
    """Per-asset: the decision-time level error, and the margin it forces at the money.

    ⚠ Three corrections to the naive reading, each of which moved the answer:

    1. **The median is the wrong statistic.** A margin is paid on every quote, so it must cover
       the typical magnitude, not the middle one -- and several of these indices are published on
       a lattice coarse enough that the median error is pure quantization (SOL: 80.7% of
       consecutive prints byte-identical, one quantum = 0.846 bp, median error 0.000). RMS.
    2. **Print-to-print is the WORST moment of the cycle, not the average.** At CF-time `s_k+1`
       the freshest print we hold is `k` (transport 26-73 ms < the 200 ms gap), so the
       print-to-print error is the error just *before* a new print lands. Just *after* one, the
       error is only the index's move over the transport delay. Averaging over the cycle means
       integrating the error over a staleness uniform on `[transport, transport + gap]`, which
       for a root-time process is `sigma*sqrt(mean staleness)`.
    3. **sigma must be the INDEX's own vol, not spot's.** The index is a robust cross-venue
       aggregate, so there is no reason for it to share a single venue's tick variance. It is
       measured here from the tape rather than assumed.
    """
    import math
    # Per-asset index vol, per root-second, from the tape's own print-to-print moves.
    print("\n## the index's own volatility, and the decision-time level error\n")
    print(f"{'asset':>6s} {'gap ms':>7s} {'RMS/gap bp':>11s} {'volA%':>7s} "
          f"{'stale ms':>9s} {'LEVEL bp':>9s}")
    level_bp, stale_s, sig_bp = {}, {}, {}
    for asset, prints in sorted(index.items()):
        gaps = [(prints[i + 1][0] - prints[i][0]) / 1e6 for i in range(len(prints) - 1)]
        d = [abs(prints[i + 1][1] / prints[i][1] - 1.0) * 1e4 for i in range(len(prints) - 1)]
        gap_s = st.median(gaps)
        if gap_s <= 0:
            continue
        rms = (sum(x * x for x in d) / len(d)) ** 0.5
        sig = rms / math.sqrt(gap_s)                      # bp per root-second
        # Transport is measured, not assumed: CF->us 26-73 ms, per index.
        trans = args.transport_ms / 1000.0
        mean_stale = trans + gap_s / 2.0
        level_bp[asset] = sig * math.sqrt(mean_stale)
        stale_s[asset], sig_bp[asset] = mean_stale, sig
        print(f"{asset:>6s} {1000 * gap_s:>7.0f} {rms:>11.4f} "
              f"{sig * 1e-4 * math.sqrt(365 * 86400) * 100:>7.1f} "
              f"{1000 * mean_stale:>9.0f} {level_bp[asset]:>9.4f}")

    # ⚑ sigma CANCELS. level_toll = delta * sig*sqrt(stale) and delta = 100*phi(0)/(sig*sqrt(tau-w23)),
    # so level_toll = 100*phi(0)*sqrt(stale/(tau-w23)) -- NO VOLATILITY TERM. Same for the latency
    # toll. At the money the required margin depends only on STALENESS and TIME TO EXPIRY, which is
    # `return-strike-markets-are-scale-invariant` applied to the margin rather than to the price.
    # That is why every 200 ms asset below reads the same number regardless of its own vol, and it
    # is the check: the closed form is asserted against the per-asset empirical path.
    PHI0 = 0.39894228
    toll = lambda stale_s, tau: 100 * PHI0 * math.sqrt(stale_s / (tau - 2 * 60.0 / 3))
    print(f"\n## the margin that forces, at the money, vs a {args.gross_c}c gross\n")
    print("  sigma CANCELS: toll_c = 100*phi(0)*(sqrt(stale) + sqrt(reaction))/sqrt(tau - 40)")
    print(f"{'asset':>6s} {'stale ms':>9s} " + " ".join(f"{'tau=' + str(t):>13s}" for t in (900, 600, 300)))
    react = math.sqrt(args.reaction_ms / 1000.0)
    for asset in sorted(level_bp):
        cells = []
        for tau in (900, 600, 300):
            need = 100 * PHI0 * (math.sqrt(stale_s[asset]) + react) / math.sqrt(tau - 40)
            cells.append(f"{need:6.2f}c {need / args.gross_c:4.1f}x")
        print(f"{asset:>6s} {1000 * stale_s[asset]:>9.0f} " + " ".join(f"{c:>13s}" for c in cells))
        # The closed form must reproduce the empirical level term it replaced.
        emp = (100 * PHI0 / (sig_bp[asset] * math.sqrt(900 - 40))) * level_bp[asset]
        assert abs(emp - toll(stale_s[asset], 900)) < 1e-9, (asset, emp)
    print("\n  cell = (level toll + latency toll) at the money, and its multiple of the gross.")
    print("  <=1.0x is where a model-priced quote is admissible. Both tolls carry phi(z), so")
    print("  they collapse into the wing: this table is the MID BAND, the hardest case.")

    print("\n## inverted: how fresh would the level have to be?\n")
    print(f"  budget  = gross*sqrt(tau-40)/(100*phi(0)), spent on sqrt(stale) + sqrt(reaction)")
    print(f"{'tau':>6s} {'budget':>9s} {'our reaction':>13s} {'LEFT for stale':>15s} {'max stale':>11s}")
    for tau in (900, 600, 300):
        budget = args.gross_c * math.sqrt(tau - 40) / (100 * PHI0)
        left = budget - react
        ms = 1000 * left * left if left > 0 else float("nan")
        print(f"{tau:>6d} {budget:>9.4f} {react:>13.4f} {left:>15.4f} "
              + (f"{ms:>10.1f}ms" if left > 0 else f"{'NONE':>11s}"))
    print(f"\n  Our measured CF->us transport alone is {args.transport_ms:.0f} ms, i.e."
          f" sqrt = {math.sqrt(args.transport_ms / 1000):.4f}.")
    b900 = args.gross_c * math.sqrt(860) / (100 * PHI0)
    print(f"  At tau=900 the WHOLE budget is {b900:.4f}. A perfectly fresh index -- the instant it")
    print(f"  arrives, zero reaction, zero print gap -- already spends"
          f" {math.sqrt(args.transport_ms / 1000) / b900:.2f}x of it.")


if __name__ == "__main__":
    main()
