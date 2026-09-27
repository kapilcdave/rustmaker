#!/usr/bin/env python3
"""Score sports-shadow fills against venue settlement, clustered by GAME.

    python3 score_sports.py data/sports-live-0927-*/

Per simulated fill: bid (bought YES at p) earns 100y - p, ask (sold YES) earns p - 100y,
in cents per contract. Segment series are fee-free for the maker, so this is net of fees.
Unsettled markets are reported, never scored. Minutes-to-close uses the venue's FINAL
close_time, which is only known after settlement (it is a placeholder while live), so it
is a post-hoc label, not something the quoter could see.
"""
import collections, glob, json, os, random, sys, time, urllib.request, zlib

B = "https://api.elections.kalshi.com/trade-api/v2"
CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "sports_results_cache.json")


def rows(path):
    """Read a gzip tape that may still be being written (no trailer yet)."""
    d = zlib.decompressobj(16 + zlib.MAX_WBITS)
    buf = b""
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            try:
                buf += d.decompress(chunk)
            except zlib.error:
                break
            *lines, buf = buf.split(b"\n")
            for l in lines:
                yield l.decode().split(",")


def market(t, cache):
    if t in cache and cache[t].get("result") in ("yes", "no"):
        return cache[t]
    for _ in range(4):
        try:
            m = json.load(urllib.request.urlopen(f"{B}/markets/{t}", timeout=20))["market"]
            cache[t] = {k: m.get(k) for k in ("result", "status", "close_time", "event_ticker")}
            return cache[t]
        except Exception:
            time.sleep(1.5)
    return {}


def iso_ms(s):
    import datetime as dt
    return int(dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp() * 1000)


def boot(pairs, n=2000):
    g = collections.defaultdict(lambda: [0.0, 0.0])
    for k, v, w in pairs:
        g[k][0] += v * w
        g[k][1] += w
    keys = list(g)
    N = sum(c for _, c in g.values())
    if not N:
        return "n=0"
    S = sum(s for s, _ in g.values())
    mean = S / N
    if len(keys) < 2:
        return f"{mean:+.2f}c ct={N:.0f} games={len(keys)} total=${S/100:+.2f}"
    r = random.Random(7)
    bs = sorted(sum(g[k][0] for k in ks) / sum(g[k][1] for k in ks)
                for ks in ([r.choice(keys) for _ in keys] for _ in range(n)))
    return f"{mean:+.2f}c [{bs[int(.025*n)]:+.2f},{bs[int(.975*n)]:+.2f}] ct={N:.0f} games={len(keys)} total=${S/100:+.2f}"


def main():
    cache = json.load(open(CACHE)) if os.path.exists(CACHE) else {}
    for d in sys.argv[1:]:
        fills = []
        for tape in glob.glob(os.path.join(d, "shadow_*.csv.gz")):
            for r in rows(tape):
                if r[0] == "F" and len(r) >= 8:
                    fills.append((int(r[1]), r[2], r[4], int(r[5]) / 100, float(r[6])))
        unsettled = 0
        by = collections.defaultdict(list)
        for ts, t, side, p, ct in fills:
            m = market(t, cache)
            if m.get("result") not in ("yes", "no"):
                unsettled += 1
                continue
            y = 100 * (m["result"] == "yes")
            v = (y - p) if side == "bid" else (p - y)
            game = t.split("-")[1]
            band = "mid15-85" if 15 <= p <= 85 else "tail"
            mtc = (iso_ms(m["close_time"]) - ts // 1000) / 60000 if m.get("close_time") else None
            tb = "unk" if mtc is None else "<=1m" if mtc <= 1 else "1-15m" if mtc <= 15 else ">15m"
            for key in ("ALL", band, f"{band}|{tb}", f"series:{t.split('-')[0]}", f"{band}|side:{side}"):
                by[key].append((game, v, ct))
        json.dump(cache, open(CACHE, "w"))
        print(f"\n=== {d}  fills={len(fills)} unsettled={unsettled}")
        for k in sorted(by, key=lambda k: (k != "ALL", k.startswith("series:"), k)):
            print(f"  {k:28s} {boot(by[k])}")


if __name__ == "__main__":
    main()
