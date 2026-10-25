#!/usr/bin/env python3
"""Score the fills OUR sports-live seat would get, from a --dry-run journal.

A dry run synthesises acks and never fills, so this replays its quotes against the public
trade tape (`T` rows) and top of book (`B` rows) at our queue position and latency:

- an order is live from `t + latency` until its cancel/amend `t + latency` (a pull in flight
  is still exposed -- that is where adverse selection lands);
- joining an existing level queues behind its displayed size; improving the touch queues
  behind nothing;
- a print strictly through our price fills us; a print AT our price eats the queue first;
- `strict` credits queue only for prints, `clamp` also caps the queue at the displayed size
  (cancels ahead of us). The truth lies between; report both.

Per fill: spread captured vs the mid at fill, markouts vs the mid at +5/30/60/300 s, and
(with --settle) vs settlement. AS = markout - capture. Clustered by event (game), bootstrap
CI. The venue line scores every print's MAKER side in the same markets, so `ours - venue`
is the selection gap (never-arm-on-a-venue-average-edge). Stdlib only; streams the journal.

Usage: score_sports_fillset.py JOURNAL.jsonl.gz [...] [--latency-ms 30] [--settle]
"""
import argparse, collections, gzip, json, random, sys, urllib.request

HZ = (5, 30, 60, 300)


def opener(p):
    """Lines of a journal; a .gz still being written ends mid-stream, so stop at its tail."""
    f = gzip.open(p, "rt") if p.endswith(".gz") else open(p)
    try:
        for line in f:
            yield line
    except EOFError:
        print("note: %s is truncated (still being written); read up to its tail" % p, file=sys.stderr)


def event_of(tk):
    return tk.rsplit("-", 1)[0]


def series_of(tk):
    return tk.split("-", 1)[0]


class Pending:
    """Mid at fill_time + h for each horizon, resolved as later B rows stream past."""

    def __init__(self):
        self.by_tk = collections.defaultdict(list)

    def add(self, tk, t, rec):
        for h in HZ:
            self.by_tk[tk].append((t + h * 1_000_000, h, rec))

    def on_book(self, tk, t, prev_mid):
        lst = self.by_tk.get(tk)
        if not lst:
            return
        keep = []
        for due, h, rec in lst:
            if t >= due:
                rec["m%d" % h] = prev_mid
            else:
                keep.append((due, h, rec))
        self.by_tk[tk] = keep

    def flush(self, mids):
        for tk, lst in self.by_tk.items():
            for _, h, rec in lst:
                rec.setdefault("m%d" % h, mids.get(tk))
                rec["censored"] = True


def replay(paths, lat_us):
    book = {}          # tk -> (bid, bsz, ask, asz) in 1/100 c and 1/100 ct
    mids = {}
    orders = {}        # incarnation key -> dict
    cur = {}           # coid -> key of its live incarnation (amends re-key, cancels name the coid)
    fills = []         # our simulated fills
    venue = []         # maker side of every print in quoted markets
    quoted = set()
    pend = Pending()
    rows = 0
    for p in paths:
        for line in opener(p):
            try:
                r = json.loads(line)
            except ValueError:
                continue
            k, t, v = r.get("k"), r.get("t"), r.get("v")
            rows += 1
            if k == "B":
                tk, _, b, bs, a, as_ = v
                pend.on_book(tk, t, mids.get(tk))
                book[tk] = (b, bs, a, as_)
                if b and a and a > b:
                    mids[tk] = (b + a) / 200.0
                for o in orders.values():   # clamp: cancels ahead of us
                    if o["tk"] == tk and o["live"] <= t:
                        lvl = bs if o["side"] == "bid" and o["px"] == b else as_ if o["side"] == "ask" and o["px"] == a else None
                        if lvl is not None:
                            o["q_clamp"] = min(o["q_clamp"], lvl / 100.0)
            elif k == "new":
                body = v["body"]
                tk, side, px = body["ticker"], body["side"], round(float(body["price"]) * 10000)
                quoted.add(tk)
                bk = book.get(tk)
                q = 0.0
                if bk:
                    if side == "bid" and px == bk[0]:
                        q = bk[1] / 100.0
                    if side == "ask" and px == bk[2]:
                        q = bk[3] / 100.0
                cur[body["client_order_id"]] = body["client_order_id"]
                orders[body["client_order_id"]] = dict(tk=tk, side=side, px=px, live=t + lat_us,
                                                      dead=None, q_strict=q, q_clamp=q,
                                                      done_s=False, done_c=False, posted=t)
            elif k == "amend":
                o = orders.get(cur.get(v["coid"], ""))
                if o and o["dead"] is None:   # an amend re-queues at the new price; old price exposed until it lands
                    o["dead"] = t + lat_us
                    body = v["body"]
                    px = round(float(body["price"]) * 10000)
                    bk = book.get(o["tk"])
                    q = 0.0
                    if bk and ((o["side"] == "bid" and px == bk[0]) or (o["side"] == "ask" and px == bk[2])):
                        q = (bk[1] if o["side"] == "bid" else bk[3]) / 100.0
                    cur[v["coid"]] = v["coid"] + "#%d" % t
                    orders[cur[v["coid"]]] = dict(tk=o["tk"], side=o["side"], px=px, live=t + lat_us,
                                                         dead=None, q_strict=q, q_clamp=q,
                                                         done_s=False, done_c=False, posted=t)
            elif k == "ack_cancel":   # dry acks are synthesised at send time
                o = orders.get(cur.get(v.get("coid", ""), ""))
                if o and o["dead"] is None:
                    o["dead"] = t + lat_us
            elif k == "T":
                tk, _, taker, pxs, szs = v
                px, sz = round(float(pxs) * 10000), float(szs)
                m = mids.get(tk)
                if tk in quoted and m is not None:
                    # maker of a YES-taker print sold YES at px; of a NO-taker print bought YES
                    rec = dict(tk=tk, t=t, side="ask" if taker == "yes" else "bid", px=px / 100.0, mid0=m, w=sz)
                    venue.append(rec)
                    pend.add(tk, t, rec)
                for coid, o in list(orders.items()):
                    if o["tk"] != tk or o["live"] > t or (o["dead"] is not None and o["dead"] <= t):
                        continue
                    hit = (taker == "yes" and o["side"] == "ask" and px >= o["px"]) or \
                          (taker == "no" and o["side"] == "bid" and px <= o["px"])
                    if not hit:
                        continue
                    through = px != o["px"]
                    for mode in ("s", "c"):
                        qk, dk = ("q_strict", "done_s") if mode == "s" else ("q_clamp", "done_c")
                        if o[dk]:
                            continue
                        if not through:
                            o[qk] -= sz
                            if o[qk] >= 0:
                                continue
                        o[dk] = True
                        rec = dict(tk=tk, t=t, side=o["side"], px=o["px"] / 100.0, mid0=m, mode=mode,
                                   through=through, age_s=(t - o["posted"]) / 1e6, w=1.0)
                        fills.append(rec)
                        pend.add(tk, t, rec)
            # prune dead orders now and then
            if rows % 200000 == 0:
                orders = {c: o for c, o in orders.items() if o["dead"] is None or o["dead"] > t - 5_000_000}
    pend.flush(mids)
    return fills, venue, rows, len(quoted)


def settle(tickers):
    out = {}
    for tk in tickers:
        try:
            with urllib.request.urlopen("https://api.elections.kalshi.com/trade-api/v2/markets/" + tk, timeout=10) as f:
                m = json.load(f)["market"]
            if m.get("result") in ("yes", "no"):
                out[tk] = 100.0 if m["result"] == "yes" else 0.0
        except Exception:
            pass
    return out


def signed(rec, ref):
    """c/ct earned by the maker if marked at `ref`."""
    if ref is None:
        return None
    return (ref - rec["px"]) if rec["side"] == "bid" else (rec["px"] - ref)


def ci(recs, key, weighted=False, n=2000):
    by = collections.defaultdict(list)
    for r in recs:
        x = r.get(key)
        if x is not None:
            by[event_of(r["tk"])].append((x, r["w"] if weighted else 1.0))
    ev = list(by.values())
    if not ev:
        return None
    def stat(groups):
        s = sum(x * w for g in groups for x, w in g)
        d = sum(w for g in groups for _, w in g)
        return s / d if d else float("nan")
    est = stat(ev)
    rng = random.Random(7)
    bs = sorted(stat([rng.choice(ev) for _ in ev]) for _ in range(n))
    return est, bs[int(0.025 * n)], bs[int(0.975 * n)], len(ev), sum(len(g) for g in ev)


def annotate(recs, st):
    for r in recs:
        r["cap"] = signed(r, r["mid0"])
        for h in HZ:
            r["mk%d" % h] = signed(r, r.get("m%d" % h))
            if r["mk%d" % h] is not None:
                r["as%d" % h] = r["mk%d" % h] - r["cap"]
        if st is not None:
            r["mks"] = signed(r, st.get(r["tk"]))


def line(name, c):
    if c is None:
        return "%-26s  n/a" % name
    return "%-26s %+7.3f c/ct [%+.3f, %+.3f]  events=%d fills=%d" % ((name,) + c)


def report(fills, venue, do_settle):
    keys = ["cap"] + ["mk%d" % h for h in HZ] + ["as60", "as300"] + (["mks"] if do_settle else [])
    for mode, label in (("s", "OURS strict queue"), ("c", "OURS clamp queue")):
        sub = [f for f in fills if f["mode"] == mode]
        print("\n== %s: %d fills, %d through-price" % (label, len(sub), sum(f["through"] for f in sub)))
        for k in keys:
            print(line(k, ci(sub, k)))
    print("\n== VENUE maker side, same markets, contract-weighted: %d prints" % len(venue))
    for k in keys:
        print(line(k, ci(venue, k, weighted=True)))
    print("\n== OURS (clamp) by series, mk60 / mk300 / AS300")
    sub = [f for f in fills if f["mode"] == "c"]
    by = collections.defaultdict(list)
    for f in sub:
        by[series_of(f["tk"])].append(f)
    for s, fs in sorted(by.items(), key=lambda kv: -len(kv[1])):
        a, b, c = ci(fs, "mk60", n=300), ci(fs, "mk300", n=300), ci(fs, "as300", n=300)
        print("  %-24s n=%-5d mk60 %s  mk300 %s  AS300 %s" % (s, len(fs),
              "%+.2f" % a[0] if a else "n/a", "%+.2f" % b[0] if b else "n/a", "%+.2f" % c[0] if c else "n/a"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("journals", nargs="+")
    ap.add_argument("--latency-ms", type=float, default=30.0)
    ap.add_argument("--settle", action="store_true")
    a = ap.parse_args()
    fills, venue, rows, nq = replay(sorted(a.journals), int(a.latency_ms * 1000))
    st = settle({f["tk"] for f in fills}) if a.settle else None
    if st is not None:
        print("settled %d of %d filled markets" % (len(st), len({f['tk'] for f in fills})))
    annotate(fills, st)
    annotate(venue, st)
    print("rows=%d quoted_markets=%d latency=%.0fms censored_fills=%d" % (
        rows, nq, a.latency_ms, sum(1 for f in fills if f.get("censored"))))
    report(fills, venue, a.settle)


if __name__ == "__main__":
    main()
