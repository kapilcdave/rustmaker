#!/usr/bin/env python3
"""Two oracle bounds on the two execution ideas, measured on a real armed run's own tape.

1. THE 1.5 s POST-FILL FREEZE (live.rs:829 sets hold_until_us = now + 1_500_000; live.rs:940 skips
   the whole per-market decision loop until then). The ~7.5 ms makers appear to be refilling their
   own quote off their private fill notice -- 79% of sub-1ms touch adds are hit-side refill -- while
   we go silent. Bound: contracts that printed AT our just-filled price, on the side we had been
   resting, inside the freeze. A maker still resting there would have been hit again.

2. AMEND vs CANCEL+CREATE. The engine already amends when the slot holds a fully-unfilled resting
   order with a known id, otherwise it cancels and re-creates, leaving a dead window with no order.
   Bound: contracts that printed at the NEW price inside that dead window -- fills an atomic amend
   would have been present for.

Both are UPPER bounds and generous ones: they credit us the whole print, ignore queue position
ahead of us, and ignore that our own presence changes what the taker does.

Side convention (confirmed, kalshi-taker-side-convention-confirmed): a yes-print lands on the ASK,
a no-print lands on the BID. So a fill on our bid is followed by more no-prints; on our ask, yes.

Usage: python3 score_refill_and_amend.py [journal.jsonl.gz ...]
"""
import bisect
import gzip
import json
import sys
from collections import Counter, defaultdict

FREEZE_US = 1_500_000
REQUOTE_MAX_US = 500_000     # a `new` later than this is a fresh decision, not a requote


def px(s):
    """Dollar string -> integer tenths of a cent, so prices compare exactly."""
    return int(round(float(s) * 1000))


def pass1(path):
    """Order lifecycle + fills. Returns the windows to test and some counters."""
    coid = {}                                   # coid -> (ticker, side, price)
    last_cancel = {}                            # (ticker, side) -> (t, price)
    windows = defaultdict(list)                 # ticker -> [(t0, t1, price, want_yes, tag)]
    c = Counter()
    gap_us = []
    with gzip.open(path, "rt") as f:
        for line in f:
            if not line.startswith('{"k":"'):
                continue
            k = line[6:line.index('"', 6)]
            if k not in ("new", "amend", "ack_cancel", "fill", "ack_new", "ack_amend"):
                continue
            v = json.loads(line)
            t, d = v["t"], v["v"]
            if k in ("new", "amend"):
                b = d["body"]
                coid[d["coid"] if k == "amend" else b["client_order_id"]] = (
                    b["ticker"], b["side"], px(b["price"]))
                c[k] += 1
                if k == "new":
                    key = (b["ticker"], b["side"])
                    if key in last_cancel:
                        t0, _ = last_cancel.pop(key)
                        if 0 < t - t0 <= REQUOTE_MAX_US:
                            c["requote_cancel_create"] += 1
                            gap_us.append(t - t0)
                            windows[b["ticker"]].append(
                                (t0, t, px(b["price"]), b["side"] == "ask", "amend"))
            elif k == "ack_cancel":
                o = coid.get(d["coid"])
                if o:
                    last_cancel[(o[0], o[1])] = (t, o[2])
                    c["cancel"] += 1
            elif k == "fill":
                if d.get("is_taker"):
                    continue
                c["fill"] += 1
                windows[d["market_ticker"]].append(
                    (t, t + FREEZE_US, px(d["yes_price_dollars"]),
                     d["book_side"] == "ask", "freeze"))
    for w in windows.values():
        w.sort()
    return windows, c, gap_us


def pass2(path, windows):
    """Stream the trade tape against the windows."""
    hit = Counter()
    starts = {tk: [w[0] for w in ws] for tk, ws in windows.items()}
    with gzip.open(path, "rt") as f:
        for line in f:
            if not line.startswith('{"k":"T"'):
                continue
            v = json.loads(line)
            tk, _, side, price, cnt = v["v"]
            ws = windows.get(tk)
            if not ws:
                continue
            t = v["t"]
            p, yes = px(price), side == "yes"
            # every window whose start <= t; scan back while t could still be inside one
            i = bisect.bisect_right(starts[tk], t)
            for j in range(i - 1, max(-1, i - 400), -1):
                t0, t1, wp, want_yes, tag = ws[j]
                if t > t1:
                    continue
                if p == wp and yes == want_yes:
                    hit[tag + "_ct"] += float(cnt)
                    hit[tag + "_prints"] += 1
    return hit


def main(paths):
    tot = Counter()
    gaps = []
    for p in paths:
        w, c, g = pass1(p)
        h = pass2(p, w)
        tot.update(c)
        tot.update(h)
        gaps += g
        print(f"  {p.split('/')[-1]}: fills={c['fill']} new={c['new']} amend={c['amend']} "
              f"cancel={c['cancel']} cancel+create requotes={c['requote_cancel_create']}")
    gaps.sort()
    n = len(gaps)
    print("\n" + "=" * 100)
    print("REQUOTE PATH")
    print("=" * 100)
    reqs = tot["amend"] + tot["requote_cancel_create"]
    print(f"  amend                 {tot['amend']:7d}  ({100*tot['amend']/max(reqs,1):5.1f}% of requotes)")
    print(f"  cancel + create       {tot['requote_cancel_create']:7d}  ({100*tot['requote_cancel_create']/max(reqs,1):5.1f}% of requotes)")
    print(f"  cancels NOT requoted  {tot['cancel'] - tot['requote_cancel_create']:7d}  (genuine pulls: want went false)")
    if n:
        print(f"  dead window, cancel->create: p50 {gaps[n//2]/1000:.2f} ms  p90 {gaps[int(n*.9)]/1000:.2f} ms  "
              f"p99 {gaps[int(n*.99)]/1000:.2f} ms  total {sum(gaps)/1e6:.1f} s")
    print(f"\n  ORACLE BOUND on making every one of those an amend:")
    print(f"    prints at the NEW price inside the dead window: {tot['amend_prints']} prints, "
          f"{tot['amend_ct']:.2f} contracts")
    print("\n" + "=" * 100)
    print("THE 1.5 s POST-FILL FREEZE")
    print("=" * 100)
    print(f"  our maker fills: {tot['fill']}  ->  {tot['fill']*1.5:.0f} s of frozen market time")
    print(f"  ORACLE BOUND on refilling at the same price instead of freezing:")
    print(f"    prints at our just-filled price, our side, inside the freeze: "
          f"{tot['freeze_prints']} prints, {tot['freeze_ct']:.2f} contracts")
    if tot["fill"]:
        print(f"    = {tot['freeze_ct']/tot['fill']:.3f} extra contracts per fill "
              f"({100*tot['freeze_ct']/max(tot['fill'],1):.1f}% more volume)")


if __name__ == "__main__":
    main(sys.argv[1:] or ["data/box/live_penny_capped/live_1790635109218.jsonl.gz"])
