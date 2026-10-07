#!/usr/bin/env python3
"""Read-only account probe: tier/entitlement, per-shard cash, open positions,
resting orders. GETs only -- no POST/DELETE path is imported or called.

Run from the segment deploy checkout so it reuses that tree's credential
loader and signer (one authenticated call is the only thing that can report on
the control channel -- see topics-live-arming-and-plumbing).
"""
import json
import os
import sys

import requests

from live_broker import API_PREFIX, KalshiAuth, load_credentials

BASE = "https://api.elections.kalshi.com"
SHARDS = {0: "Default", 1: "Combos", 2: "Crypto", 3: "Sports"}


def main():
    # load_credentials populates os.environ and returns provenance only (never
    # key material), so the id and path come back out of the environment.
    info = load_credentials()
    auth = KalshiAuth(os.environ.get("KALSHI_API_KEY_ID"),
                      os.environ.get("KALSHI_PRIVATE_KEY_PATH"))
    print(f"credentials: source={info['source']} "
          f"fp={info['key_id_fingerprint']} alg={auth.algorithm}\n")

    def get(path):
        url = BASE + API_PREFIX + path
        r = requests.get(url, headers=auth.headers("GET", API_PREFIX + path), timeout=20)
        return r.status_code, (r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text)

    code, limits = get("/account/limits")
    print(f"GET /account/limits -> {code}")
    if code == 200:
        print(json.dumps(limits, indent=2)[:900])
    else:
        print(str(limits)[:300])

    code, bal = get("/portfolio/balance")
    print(f"\nGET /portfolio/balance -> {code}")
    if code == 200:
        total = bal.get("balance")
        print(f"  total (cents) {total}")
        for entry in bal.get("balance_breakdown", []) or []:
            idx = entry.get("exchange_index")
            print(f"  shard {idx} ({SHARDS.get(int(idx), '?'):8s}) {entry}")
    else:
        print(str(bal)[:300])

    code, pos = get("/portfolio/positions")
    print(f"\nGET /portfolio/positions -> {code}")
    if code == 200:
        rows = [p for p in (pos.get("market_positions") or [])
                if str(p.get("position_fp", p.get("position", "0"))).strip("0.-") not in ("", )]
        print(f"  market_positions rows total {len(pos.get('market_positions') or [])}, non-zero {len(rows)}")
        for p in rows[:20]:
            print(f"   {p}")
    else:
        print(str(pos)[:300])

    code, orders = get("/portfolio/orders?status=resting")
    print(f"\nGET /portfolio/orders?status=resting -> {code}")
    if code == 200:
        rows = orders.get("orders") or []
        print(f"  resting orders {len(rows)}")
        for o in rows[:20]:
            print(f"   {o.get('ticker')} {o.get('side')} {o.get('yes_price')} rem={o.get('remaining_count')} id={o.get('order_id')}")
    else:
        print(str(orders)[:300])
    return 0


if __name__ == "__main__":
    sys.exit(main())
