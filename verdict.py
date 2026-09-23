"""Go/no-go on SPEED for a 15M crypto maker, from the probe's last stats row.

Our reaction lands at the venue at  t_print + feed_one_way + order_one_way.
    feed_one_way  = trade.feed_age_us p50 (box clock is chrony/AWS Time Sync; Kalshi's is assumed good)
    order_one_way = signed-REST RTT p50 / 2  (lower bound: auth is server-side, so the cancel's
                    one-way may be most of the RTT; we also report the full-RTT case)

Usage: python3 verdict.py stats_XXXX.jsonl [rtt.json]
"""
import json
import sys


def last(path):
    with open(path) as f:
        rows = [json.loads(line) for line in f if line.strip()]
    return rows[-1]


def interp_share_above(dist, x_us):
    """Weighted share of samples > x, linearly interpolated between reported thresholds."""
    sa = dist.get("share_above", {})
    pts = sorted((int(k[1:]), v) for k, v in sa.items())
    if not pts:
        return None
    if x_us <= pts[0][0]:
        return pts[0][1]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if x0 <= x_us <= x1:
            return y0 + (y1 - y0) * (x_us - x0) / (x1 - x0)
    return pts[-1][1]


def main():
    r = last(sys.argv[1])
    rtt = json.load(open(sys.argv[2])) if len(sys.argv) > 2 else None
    d, c = r["dists_us"], r["counts"]
    feed = d["ALL.trade.feed_age_us"]["quantiles"]["p50"]
    order_rtt = rtt["portfolio_orders_signed"]["quantiles"]["p50"] if rtt else 13_620
    cases = {
        "optimistic (feed + RTT/2)": feed + order_rtt / 2,
        "realistic (feed + full signed RTT)": feed + order_rtt,
    }
    print(f"elapsed {r['elapsed_s']}s  prints {c.get('ALL.prints')}  "
          f"same-ms fragments {c.get('ALL.prints_same_ms_fragment')}  sweep heads {c.get('ALL.sweep_heads')}")
    print(f"feed one-way p50 {feed/1000:.2f} ms   signed order RTT p50 {order_rtt/1000:.2f} ms\n")

    rows = [
        ("sweep continuation at head's price, gap from prev (ct-weighted)",
         "ALL.sweep_cont_same_price.gap_from_prev", "share we'd cancel BEFORE it (good)"),
        ("competitor add at/inside touch after a print",
         "ALL.add_after_print", "share of refreshes we'd BEAT"),
        ("second participant joining a newly improved touch",
         "ALL.join_after_new_touch", "share of new levels where we'd be 2nd, not 3rd+"),
        ("touch price lifetime",
         "ALL.touch_price_lifetime", "share of touches that outlive our post"),
    ]
    for label, key, meaning in rows:
        dist = d.get(key, {"n": 0})
        if not dist.get("n"):
            print(f"{label}: no samples")
            continue
        q = {k: round(v / 1000, 1) for k, v in dist["quantiles"].items()}
        print(f"{label}  n={dist['n']}\n   ms {q}")
        for name, lat in cases.items():
            s = interp_share_above(dist, lat)
            print(f"   {meaning} @ {name} {lat/1000:.1f} ms: {s:.1%}" if s is not None else "")
        print()
    q = d.get("ALL.touch_queue_ct_at_print", {}).get("quantiles")
    if q:
        print(f"resting contracts at the touch a print hits: {q}")


if __name__ == "__main__":
    main()
