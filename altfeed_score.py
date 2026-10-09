#!/usr/bin/env python3
"""Score an `altfeed` capture: per asset, which spot venue sees the move first, from this box?

The engine's whole reason to read spot is that Kalshi's own feed reaches us 5.7 ms after the
venue stamps it, which is 68% of our reaction time. A spot venue is worth a socket only if it
shows the move *earlier than Kalshi's book does*, by enough to act on. That is three separate
questions and this script answers them separately, because they have different answers:

1. **Delay** — exchange stamp to our receipt, per venue. Includes the venue's own clock skew,
   so read the shape, not the last microsecond. Unstamped venues (Binance.US `@bookTicker`,
   Gemini `l2`) are scored on the receipt clock only, and that is marked in the output.
2. **Lead** — cross-correlation of each venue's returns against a reference, at lags of ±2 s.
   A POSITIVE `lead_ms` means the venue moves BEFORE the reference.
3. **Race** — the only question an engine can act on: on each real move, which venue's receipt
   clock crossed the threshold first, and by how much. Correlation says a venue is informative;
   the race says whether we would have heard it in time.

With a Kalshi tape (`probe`, `kind=B` rows) the reference becomes the 15M book mid, and the
answer is the lead over the venue we actually trade. With an index sidecar (`probe --index`) it
becomes the CF Benchmarks index the contract settles on.

Usage:
  python3 altfeed_score.py data/altfeed/altfeed_*.csv.gz
  python3 altfeed_score.py altfeed_*.csv.gz --kalshi data/tape_*.csv.gz --index data/index_*.jsonl.gz
  python3 altfeed_score.py ... --json out.json
"""
from __future__ import annotations

import argparse
import glob
import gzip
import json
import sys
from collections import defaultdict

import numpy as np

BIN_US = 10_000           # 10 ms bins: finer than the 5.7 ms feed and the ~4 ms order path
MAX_LAG_BINS = 200        # +/- 2 s
MOVE_BPS = 5.0            # a "move" in the reference over MOVE_WINDOW_US
MOVE_WINDOW_US = 1_000_000
RACE_FRAC = 0.5           # a venue has "seen" the move at this fraction of it
MIN_RACE_EVENTS = 30      # below this a per-venue share is noise, not a ranking
# Venues that do not stamp their frames: delay is unmeasurable, lead/race still are.
UNSTAMPED = {"binance_us", "gemini"}
USD_VENUES = {"cb_ex", "cb_adv", "kraken", "binance_us", "bitstamp", "crypto_com", "gemini"}


# --------------------------------------------------------------------------- loading


def read_altfeed(paths):
    """-> {(asset, venue): (recv_us, exch_us, mid)} sorted by recv_us, plus lifecycle notes.

    A capture still being written has no gzip end-of-stream marker and a half-flushed last line.
    Both are tolerated on purpose — scoring a run in flight is the point — but the truncation is
    REPORTED, because a reader that silently stops early turns a long capture into a short one,
    and then every "underpowered" verdict downstream is about the reader, not the market.
    """
    rows = defaultdict(list)
    notes = []
    for path in paths:
        truncated = False
        with gzip.open(path, "rt") as fh:
            header = next(fh, "")
            if not header.startswith("src,"):
                sys.exit(f"{path}: not an altfeed capture (header {header!r})")
            try:
                for line in fh:
                    if line.startswith("#"):
                        notes.append(line.rstrip("\n"))
                        continue
                    try:
                        src, asset, recv, exch, bid, ask = line.rstrip("\n").split(",")
                        rows[(asset, src)].append(
                            (int(recv), int(exch), (float(bid) + float(ask)) / 2)
                        )
                    except ValueError:
                        continue  # a half-written final line
            except EOFError:
                truncated = True
        if truncated:
            print(
                f"NOTE: {path} has no end-of-stream marker, so it is still being written; "
                "scored up to its last flush.",
                file=sys.stderr,
            )
    out = {}
    for key, vals in rows.items():
        a = np.array(vals, dtype=float)
        a = a[np.argsort(a[:, 0], kind="stable")]
        out[key] = a
    return out, notes


def read_kalshi(paths):
    """Kalshi `probe` tape -> {series: (recv_us, mid_cents)} from the touch rows.

    `B` rows are written only when the touch changed, and carry yes bid/ask in 1e-4 dollars with
    -1 for an empty side. A one-sided book has no mid and is dropped rather than guessed.
    """
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
                    bid, ask = int(p[3]), int(p[5])
                except ValueError:
                    continue
                if bid < 0 or ask < 0:
                    continue
                rows[ticker.split("-")[0]].append((recv, (bid + ask) / 2))
    return {k: np.array(v, dtype=float) for k, v in rows.items()}


def _scan_json(text):
    """Yield every JSON object in `text`, ignoring whatever whitespace separates them.

    Not `for line in fh`: the venue's own frames end with a newline, so a record written as
    `{"recv_us":N,"frame":<frame>}` lands its closing brace on the next line. The writer now
    trims, but the 2026-10-07 10-minute schema capture does not, and a reader that cannot read
    its own earlier output is a reader that quietly reports "no index values".
    """
    dec = json.JSONDecoder()
    i, n = 0, len(text)
    while i < n:
        while i < n and text[i] in " \t\r\n":
            i += 1
        if i >= n:
            return
        try:
            obj, end = dec.raw_decode(text, i)
        except json.JSONDecodeError:
            nxt = text.find("\n", i)
            if nxt < 0:
                return
            i = nxt + 1
            continue
        yield obj
        i = end


def read_index(paths):
    """Index sidecar -> {index_id: (recv_us, value_usd)}, plus the delay decomposition.

    Schema confirmed by a successful capture on 2026-10-07. The two channels carry DIFFERENT
    shapes, and a reader that knows only one silently discards the other:

        cfbenchmarks_value_5hz  msg: {index_id, value_usd: "2609.65000000",
                                      source_ts_ms, received_at, data: "<CF payload>"}
        cfbenchmarks_value      msg: {index_id, received_at, data: "<CF payload>",
                                      avg_60s_data: {value, window_start_ts_ms,
                                                     window_end_ts_exclusive, window_size}}

    So the value is `value_usd` (a dollar STRING, the convention of every price on this venue) on
    the 5 Hz channel and lives inside the embedded `data` JSON STRING on the 1 Hz one. The 1 Hz
    channel has **no `source_ts_ms`**; CF's own stamp is `data.time`. `avg_60s_data` is the
    60-second average the contract settles on, served progressively, and appears only there.

    Each frame also carries Kalshi's receive AND send stamps, so the index's lateness splits into
    three legs that belong to three different parties: CF->Kalshi, Kalshi's queue, Kalshi->us.
    """
    rows = defaultdict(list)
    legs = defaultdict(list)
    cadence = defaultdict(list)
    seen_keys, kinds = set(), set()
    for path in paths:
        with gzip.open(path, "rt") as fh:
            for rec in _scan_json(fh.read()):
                frame = rec.get("frame", {})
                msg = frame.get("msg", frame)
                if not isinstance(msg, dict):
                    continue
                seen_keys.update(msg.keys())
                kind = frame.get("type")
                kinds.add(kind)
                idx = msg.get("index_id")
                if idx is None:
                    continue
                inner = {}
                if isinstance(msg.get("data"), str):
                    try:
                        inner = json.loads(msg["data"])
                    except json.JSONDecodeError:
                        inner = {}
                try:
                    val = float(msg.get("value_usd", inner.get("value")))
                except (TypeError, ValueError):
                    continue
                recv_us = int(rec["recv_us"])
                rows[str(idx)].append((recv_us, val))
                cadence[(str(idx), kind)].append(recv_us)
                src = msg.get("source_ts_ms") or inner.get("time")
                got, sent = msg.get("received_at"), frame.get("sending_ts_ms")
                if src and got and sent:
                    legs[(str(idx), kind)].append((got - src, sent - got, recv_us / 1000.0 - sent))
    out = {}
    for k, v in rows.items():
        a = np.array(v, dtype=float)
        # The two channels both carry every index, so the merged series has duplicates at the
        # same value; keep one observation per receipt instant.
        a = a[np.argsort(a[:, 0], kind="stable")]
        out[k] = a[np.concatenate(([True], np.diff(a[:, 0]) > 0))]
    delay = {}
    for (k, kind), v in legs.items():
        a = np.array(v, dtype=float)
        delay[f"{k}/{kind}"] = {
            "n": len(a),
            "cadence_ms_p50": float(np.median(np.diff(sorted(cadence[(k, kind)])))) / 1000.0
            if len(cadence[(k, kind)]) > 1 else float("nan"),
            "cf_to_kalshi_ms_p50": float(np.median(a[:, 0])),
            "kalshi_queue_ms_p50": float(np.median(a[:, 1])),
            "kalshi_to_us_ms_p50": float(np.median(a[:, 2])),
            "source_to_us_ms_p50": float(np.median(a.sum(axis=1))),
        }
    return out, sorted(x for x in seen_keys if x), delay, sorted(x for x in kinds if x)


# --------------------------------------------------------------------------- series maths


def q(a, *fracs):
    if len(a) == 0:
        return [float("nan")] * len(fracs)
    return [float(x) for x in np.quantile(a, fracs)]


def grid(recv_us, px, t0, n, bin_us=None):
    """Last price at or before each bin edge, forward-filled; NaN before the first observation."""
    idx = np.searchsorted(recv_us, t0 + np.arange(n) * (bin_us or BIN_US), side="right") - 1
    out = np.full(n, np.nan)
    ok = idx >= 0
    out[ok] = px[idx[ok]]
    return out


def logret(level):
    r = np.full(len(level), np.nan)
    with np.errstate(divide="ignore", invalid="ignore"):
        r[1:] = np.log(level[1:] / level[:-1])
    r[~np.isfinite(r)] = np.nan
    return r


def _ccf(u, v, max_lag):
    """Sum_t u[t]*v[t+lag] for lag in [-max_lag, max_lag], zero-padded so nothing wraps."""
    n = len(u)
    size = 1 << int(np.ceil(np.log2(2 * n + 1)))
    full = np.fft.irfft(np.conj(np.fft.rfft(u, size)) * np.fft.rfft(v, size), size)
    return full[np.arange(-max_lag, max_lag + 1) % size]


MAX_ZERO_SHARE = 0.60


def xcorr_peak(a, b, max_lag=MAX_LAG_BINS, min_bins=200, bin_us=None,
               max_zero_share=MAX_ZERO_SHARE):
    """How far `a` leads `b`, in ms, at the lag where corr(a[t], b[t+lag]) peaks.

    `lead_ms > 0` means `a` moves FIRST and `b` follows it: if `b` is a copy of `a` delayed by
    d bins, then `corr(a[t], b[t+lag])` peaks at `lag = +d`, so `lead_ms = +d` bins of time.
    The sign is the whole output of this function, so it has its own test.

    A bin with no update carries a zero return (forward fill), and a pair of zeros is excluded:
    on a thin altcoin almost every bin is idle, and counting those pairs would drive every
    correlation toward one. So the correlation at each lag is taken over the set where at least
    one side moved.

    Computed by FFT rather than a loop over lags. Every term the correlation needs is itself a
    cross-correlation, and a zero contributes nothing to a sum of x, x^2 or xy — so only the
    PAIR COUNT needs the mask, and that is three cross-correlations of indicators:
        N = #{x!=0} + #{y!=0} - #{both!=0}
        corr = (N*Sxy - Sx*Sy) / sqrt((N*Sxx - Sx^2) * (N*Syy - Sy^2))
    A 12 h capture is 4.3M bins; the loop form took ~401 passes over it per venue pair, which is
    hours. This is seconds, and `test_altfeed_scorers` pins it against the loop.
    """
    x = np.nan_to_num(np.asarray(a, dtype=float))
    y = np.nan_to_num(np.asarray(b, dtype=float))
    # ⛔ The guard this function exists to carry. A forward-filled bin with no new quote has a
    # return of exactly zero, so on a feed that updates ~1/s a 10 ms grid is ~96% zeros, almost
    # every surviving pair is (moved, did-not-move), and the correlation collapses toward zero
    # MECHANICALLY. Measured 2026-10-07 on DOGE/ETH/ZEC spot against the CF index — the same
    # series, the index being computed from those very venues:
    #     10 ms bins: 87-96% zero -> corr 0.05-0.09
    #      250 ms   : 20-61% zero -> corr 0.29-0.48
    #        5 s    : 0.5-10% zero -> corr 0.84-0.96
    # The first row is not a weak relationship, it is a broken estimator, and it was reported as
    # a result once. Refuse to return a correlation computed on a grid that sparse.
    zero_share = float(max(np.mean(x == 0), np.mean(y == 0)))
    if zero_share > max_zero_share:
        return {"corr": float("nan"), "lead_ms": float("nan"), "n_bins": 0,
                "zero_share": zero_share,
                "void": f"{zero_share:.0%} of bins have no price change at "
                        f"{(bin_us or BIN_US)/1000:.0f} ms; coarsen the bin"}
    ones = np.ones(len(x))
    ix, iy = (x != 0).astype(float), (y != 0).astype(float)
    sxy = _ccf(x, y, max_lag)
    sx, sxx = _ccf(x, ones, max_lag), _ccf(x * x, ones, max_lag)
    sy, syy = _ccf(ones, y, max_lag), _ccf(ones, y * y, max_lag)
    n = np.rint(_ccf(ix, ones, max_lag) + _ccf(ones, iy, max_lag) - _ccf(ix, iy, max_lag))
    vx, vy = n * sxx - sx * sx, n * syy - sy * sy
    with np.errstate(invalid="ignore", divide="ignore"):
        corr = (n * sxy - sx * sy) / np.sqrt(vx * vy)
    corr[(n < min_bins) | (vx <= 0) | (vy <= 0)] = np.nan
    if not np.isfinite(corr).any():
        return {"corr": float("nan"), "lead_ms": float("nan"), "n_bins": 0,
                "zero_share": zero_share, "void": "no lag had enough paired bins"}
    k = int(np.nanargmax(corr))
    lag = k - max_lag
    return {"corr": float(corr[k]), "lead_ms": lag * (bin_us or BIN_US) / 1000.0,
            "n_bins": int(n[k]), "zero_share": zero_share,
            "bin_ms": (bin_us or BIN_US) / 1000.0}


# --------------------------------------------------------------------------- the race


def race(ref_recv, ref_px, venues, threshold_bps=MOVE_BPS):
    """On every reference move, which venue's RECEIPT clock crossed RACE_FRAC of it first?

    The reference is used only to *find* the events and their direction. Each venue is then
    judged on its own observations, so a venue that is simply a constituent of the reference
    gets no credit for being inside it.

    A venue with no observation at or before the event start is skipped for that event, not
    counted as last: a venue that just reconnected never saw the move to miss it.
    """
    if len(ref_recv) < 2:
        return {}
    # Reference move over MOVE_WINDOW_US, evaluated at each reference update.
    base_idx = np.searchsorted(ref_recv, ref_recv - MOVE_WINDOW_US, side="right") - 1
    ok = base_idx >= 0
    bps = np.full(len(ref_recv), 0.0)
    bps[ok] = (ref_px[ok] / ref_px[base_idx[ok]] - 1.0) * 10_000.0
    hit = np.abs(bps) >= threshold_bps
    # One event per excursion: take the first crossing, then wait out the window.
    events = []
    last_t = -np.inf
    for i in np.flatnonzero(hit):
        if ref_recv[i] - last_t < MOVE_WINDOW_US:
            continue
        events.append((ref_recv[base_idx[i]], ref_recv[i], np.sign(bps[i]), abs(bps[i])))
        last_t = ref_recv[i]

    first = defaultdict(int)
    deltas = defaultdict(list)
    seen = defaultdict(int)
    n_scored = 0
    for t_start, t_end, sign, size in events:
        need = size * RACE_FRAC
        cross = {}
        for name, arr in venues.items():
            recv, px = arr[:, 0], arr[:, 2]
            j0 = np.searchsorted(recv, t_start, side="right") - 1
            if j0 < 0:
                continue
            seen[name] += 1
            base = px[j0]
            # Look out to one window past the reference's own crossing.
            j1 = np.searchsorted(recv, t_end + MOVE_WINDOW_US, side="right")
            seg_r, seg_p = recv[j0 + 1 : j1], px[j0 + 1 : j1]
            if len(seg_r) == 0:
                continue
            move = (seg_p / base - 1.0) * 10_000.0 * sign
            k = np.flatnonzero(move >= need)
            if len(k):
                cross[name] = seg_r[k[0]]
        if len(cross) < 2:
            continue
        n_scored += 1
        win_t = min(cross.values())
        for name, t in cross.items():
            if t == win_t:
                first[name] += 1
            deltas[name].append((t - win_t) / 1000.0)

    out = {
        "_events": len(events),
        "_scored": n_scored,
        "_threshold_bps": threshold_bps,
        "_enough": n_scored >= MIN_RACE_EVENTS,
    }
    for name in venues:
        d = np.array(deltas.get(name, []))
        out[name] = {
            "saw_event": seen.get(name, 0),
            "crossed": len(d),
            "first": first.get(name, 0),
            "first_share": first.get(name, 0) / n_scored if n_scored else float("nan"),
            "behind_winner_ms_p50": q(d, 0.5)[0],
            "behind_winner_ms_p90": q(d, 0.9)[0],
        }
    return out


# --------------------------------------------------------------------------- report


def score(feeds, kalshi, index, notes, threshold_bps):
    assets = sorted({a for a, _ in feeds})
    report = {
        "bin_us": BIN_US,
        "move_bps": threshold_bps,
        "move_window_us": MOVE_WINDOW_US,
        "race_frac": RACE_FRAC,
        "notes": notes,
        "assets": {},
    }
    for asset in assets:
        venues = {v: feeds[(a, v)] for (a, v) in feeds if a == asset}
        t_lo = min(arr[0, 0] for arr in venues.values())
        t_hi = max(arr[-1, 0] for arr in venues.values())
        n_bins = int((t_hi - t_lo) // BIN_US) + 1
        if n_bins < 2 * MAX_LAG_BINS:
            report["assets"][asset] = {"error": f"capture too short: {n_bins} bins"}
            continue

        per_venue = {}
        rets = {}
        for v, arr in venues.items():
            recv, exch, px = arr[:, 0], arr[:, 1], arr[:, 2]
            gaps = np.diff(recv)
            stamped = v not in UNSTAMPED and np.any(exch > 0)
            delay = (recv - exch)[exch > 0] if stamped else np.array([])
            per_venue[v] = {
                "n": int(len(arr)),
                "updates_per_s": float(len(arr) / max((t_hi - t_lo) / 1e6, 1e-9)),
                "gap_ms_p50": q(gaps, 0.5)[0] / 1000.0,
                "gap_ms_p90": q(gaps, 0.9)[0] / 1000.0,
                "stamped": bool(stamped),
                "delay_ms": dict(zip(("p1", "p50", "p99"), [x / 1000.0 for x in q(delay, 0.01, 0.5, 0.99)])),
                "quote": "USD" if v in USD_VENUES else "USDT/PERP",
            }
            rets[v] = logret(grid(recv, px, t_lo, n_bins))

        # Reference for lead and for finding move events: the median mid across USD venues,
        # rebuilt on the bin grid. A USDT or perp venue carries a basis, so it cannot be in a
        # level-median; it is still raced and still correlated.
        usd = [v for v in venues if v in USD_VENUES]
        ref_src = usd or list(venues)
        level = np.nanmedian(
            np.vstack([grid(venues[v][:, 0], venues[v][:, 2], t_lo, n_bins) for v in ref_src]), axis=0
        )
        ref_ret = logret(level)
        fin = np.isfinite(level)
        ref_recv = (t_lo + np.arange(n_bins) * BIN_US)[fin]
        ref_px = level[fin]

        lead = {v: xcorr_peak(rets[v], ref_ret) for v in venues}

        asset_out = {
            "window_s": (t_hi - t_lo) / 1e6,
            "reference": f"median mid of {len(ref_src)} USD venues" if usd else "median of all venues",
            "venues": per_venue,
            "lead_vs_reference": lead,
            "race": race(ref_recv, ref_px, venues, threshold_bps),
        }

        # Against the venue we actually trade.
        series = f"KX{asset}15M"
        if series in kalshi and len(kalshi[series]) > 10:
            k = kalshi[series]
            k_ret = logret(grid(k[:, 0], k[:, 1], t_lo, n_bins))
            asset_out["kalshi_mid"] = {
                "n": int(len(k)),
                "lead_of_spot_over_kalshi": {v: xcorr_peak(rets[v], k_ret) for v in venues},
                "reference_lead_over_kalshi": xcorr_peak(ref_ret, k_ret),
            }
        # Against the number the contract settles on.
        idx_id = "BRTI" if asset == "BTC" else f"{asset}USD_RTI"
        if idx_id in index and len(index[idx_id]) > 10:
            ix = index[idx_id]
            i_ret = logret(grid(ix[:, 0], ix[:, 1], t_lo, n_bins))
            asset_out["settlement_index"] = {
                "index_id": idx_id,
                "n": int(len(ix)),
                "publish_gap_ms_p50": q(np.diff(ix[:, 0]), 0.5)[0] / 1000.0,
                "lead_of_spot_over_index": {v: xcorr_peak(rets[v], i_ret) for v in venues},
                "reference_lead_over_index": xcorr_peak(ref_ret, i_ret),
            }
        report["assets"][asset] = asset_out
    return report


def brief(report):
    print(f"bins {report['bin_us']/1000:.0f}ms | move >= {report['move_bps']}bps/1s | "
          f"winner at {report['race_frac']:.0%} of the move")
    for asset, a in report["assets"].items():
        if "error" in a:
            print(f"\n== {asset}: {a['error']}")
            continue
        print(f"\n== {asset}  ({a['window_s']/60:.0f} min, ref = {a['reference']})")
        r = a["race"]
        tag = "" if r.get("_enough") else "  [UNDERPOWERED]"
        print(f"   race: {r['_scored']} scored events of {r['_events']} found{tag}")
        hdr = f"   {'venue':12} {'upd/s':>7} {'gap_p50':>8} {'delay_p50':>10} "
        hdr += f"{'lead_ref':>9} {'first%':>7} {'behind_p50':>11}"
        print(hdr)
        rows = []
        for v, pv in a["venues"].items():
            rv = r.get(v, {})
            rows.append((
                rv.get("first_share", 0) if np.isfinite(rv.get("first_share", 0)) else 0,
                v, pv, rv, a["lead_vs_reference"][v],
            ))
        for _, v, pv, rv, ld in sorted(rows, reverse=True):
            d = pv["delay_ms"]["p50"]
            print(f"   {v:12} {pv['updates_per_s']:7.1f} {pv['gap_ms_p50']:8.1f} "
                  f"{(f'{d:10.2f}' if pv['stamped'] else '         -')} "
                  f"{ld['lead_ms']:+9.0f} {100*rv.get('first_share', float('nan')):6.1f}% "
                  f"{rv.get('behind_winner_ms_p50', float('nan')):11.1f}")
        if "kalshi_mid" in a:
            km = a["kalshi_mid"]["reference_lead_over_kalshi"]
            print(f"   spot reference leads the Kalshi 15M mid by {km['lead_ms']:+.0f} ms "
                  f"(corr {km['corr']:.3f}, {km['n_bins']} bins, {a['kalshi_mid']['n']} touch rows)")
        if "settlement_index" in a:
            si = a["settlement_index"]
            print(f"   spot reference leads {si['index_id']} by "
                  f"{si['reference_lead_over_index']['lead_ms']:+.0f} ms "
                  f"(index publishes every {si['publish_gap_ms_p50']:.0f} ms, {si['n']} values)")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("altfeed", nargs="+", help="altfeed_*.csv.gz from `kalshi-mm15 altfeed`")
    ap.add_argument("--kalshi", nargs="*", default=[], help="tape_*.csv.gz from `kalshi-mm15 probe`")
    ap.add_argument("--index", nargs="*", default=[], help="index_*.jsonl.gz from `probe --index`")
    ap.add_argument("--move-bps", type=float, default=MOVE_BPS)
    ap.add_argument(
        "--exclude-venues",
        nargs="*",
        default=[],
        help="drop these venues at load, so they leave the reference median, the race and the "
        "lead together. A socket that reconnects every ~80 s is in USD_VENUES and would "
        "otherwise corrupt the reference it is being scored against "
        "(PREREG_altfeed_venue_race_20261007.md A1).",
    )
    ap.add_argument("--json", help="write the full report here")
    args = ap.parse_args()

    expand = lambda pats: [p for pat in pats for p in (glob.glob(pat) or [pat])]
    feeds, notes = read_altfeed(expand(args.altfeed))
    if not feeds:
        sys.exit("no quotes in the capture")
    if args.exclude_venues:
        drop = set(args.exclude_venues)
        seen = {v for (_a, v) in feeds}
        missing = drop - seen
        if missing:
            sys.exit(f"--exclude-venues names venues not in the capture: {sorted(missing)}")
        kept = {k: v for k, v in feeds.items() if k[1] not in drop}
        n_drop = sum(len(feeds[k]) for k in feeds if k[1] in drop)
        n_all = sum(len(a) for a in feeds.values())
        print(
            f"EXCLUDED {sorted(drop)}: {n_drop:,} of {n_all:,} rows "
            f"({100.0 * n_drop / n_all:.2f}%) dropped before scoring.",
            file=sys.stderr,
        )
        feeds = kept
        if not feeds:
            sys.exit("every venue was excluded")
    kalshi = read_kalshi(expand(args.kalshi)) if args.kalshi else {}
    index, keys, idx_delay, channels = (
        read_index(expand(args.index)) if args.index else ({}, [], {}, [])
    )
    if args.index and not index:
        print(f"WARNING: no index values parsed; fields seen in the frames: {keys}", file=sys.stderr)
    if idx_delay:
        print(f"settlement index: channels {channels}")
        print(f"  {'index/channel':34} {'n':>7} {'cadence':>8} {'CF->Kalshi':>11} "
              f"{'queue':>7} {'Kalshi->us':>11} {'CF->us':>8}")
        for k in sorted(idx_delay):
            d = idx_delay[k]
            print(f"  {k:34} {d['n']:7d} {d['cadence_ms_p50']:8.0f} {d['cf_to_kalshi_ms_p50']:11.1f} "
                  f"{d['kalshi_queue_ms_p50']:7.1f} {d['kalshi_to_us_ms_p50']:11.1f} "
                  f"{d['source_to_us_ms_p50']:8.1f}")
        print()

    report = score(feeds, kalshi, index, notes, args.move_bps)
    report["index_delay_ms"] = idx_delay
    report["index_channels"] = channels
    brief(report)
    if args.json:
        with open(args.json, "w") as fh:
            json.dump(report, fh, indent=1, default=float)
        print(f"\nfull report -> {args.json}")


if __name__ == "__main__":
    main()
