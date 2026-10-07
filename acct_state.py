#!/usr/bin/env python3
"""Read-only account state, Ed25519-signed. GETs only -- no POST/PUT/DELETE path exists here.

Signing follows src/auth.rs exactly: raw Ed25519 over `{ts_ms}{METHOD}{path}` where path is
`/trade-api/v2{...}` with the query string STRIPPED.

Units trap (kalshi-venue-mechanics): top-level `balance` is an integer of cents, while
`balance_breakdown[].balance` is a DOLLAR STRING. And a balance floor must be read on the
TRADING SHARD, never account-wide (a-balance-floor-must-be-read-on-the-trading-shard).

Usage: KALSHI_ENV_FILE=~/.config/kalshi/env python3 -I acct_state.py
"""
import base64
import json
import os
import sys
import time
import urllib.request

from cryptography.hazmat.primitives import serialization

BASE = "https://api.elections.kalshi.com"
PREFIX = "/trade-api/v2"
SHARDS = {0: "Default", 1: "Combos", 2: "Crypto/Commodities", 3: "Sports"}


def load(env_path):
    kid = pem_path = None
    for line in open(os.path.expanduser(env_path)):
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip().removeprefix("export ").strip()
        v = v.strip().strip("\"'")
        if k in ("KALSHI_API_KEY_ID", "KALSHI_API_KEY"):
            kid = v
        elif k == "KALSHI_PRIVATE_KEY_PATH":
            pem_path = v
    key = serialization.load_pem_private_key(
        open(os.path.expanduser(pem_path), "rb").read(), password=None)
    return kid, key, pem_path


def get(kid, key, path):
    ts = str(int(time.time() * 1000))
    msg = f"{ts}GET{PREFIX}{path.split('?')[0]}".encode()
    sig = base64.b64encode(key.sign(msg)).decode()
    req = urllib.request.Request(BASE + PREFIX + path, headers={
        "KALSHI-ACCESS-KEY": kid, "KALSHI-ACCESS-TIMESTAMP": ts,
        "KALSHI-ACCESS-SIGNATURE": sig, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.load(r)


def main():
    env = os.environ.get("KALSHI_ENV_FILE", "~/.config/kalshi/env")
    kid, key, pem_path = load(env)
    print(f"credential: id={kid[:8]} alg={type(key).__name__} env={env} pem={pem_path}\n")

    lim = get(kid, key, "/account/limits")
    print("TIER:", json.dumps(lim, separators=(",", ":"))[:400], "\n")

    bal = get(kid, key, "/portfolio/balance")
    print(f"BALANCE account-wide: {bal.get('balance')} (integer cents)")
    for b in bal.get("balance_breakdown") or []:
        xi = b.get("exchange_index")
        print(f"  shard {xi} {SHARDS.get(xi, '?'):20} cash ${float(b['balance']):8.4f}")

    pos = get(kid, key, "/portfolio/positions?count_filter=position&limit=200")
    mp = [m for m in (pos.get("market_positions") or []) if float(m.get("position_fp", 0) or 0)]
    print(f"\nOPEN POSITIONS: {len(mp)}")
    for m in mp:
        print(f"  shard {m.get('exchange_index')} {m['ticker']:38} pos {m.get('position_fp')!s:>8} "
              f"exposure ${m.get('market_exposure_dollars')}")

    orders = get(kid, key, "/portfolio/orders?status=resting&limit=200")
    ro = orders.get("orders") or []
    print(f"\nRESTING ORDERS: {len(ro)}")
    for o in ro[:30]:
        print(f"  {o.get('ticker','?'):38} {o.get('side','?'):4} {o.get('yes_price','?')!s:>6} "
              f"rem {o.get('remaining_count_fp')!s:>7} id {str(o.get('order_id'))[:8]}")

    with urllib.request.urlopen(BASE + PREFIX + "/exchange/status", timeout=20) as r:
        print("\nEXCHANGE:", json.load(r))


if __name__ == "__main__":
    sys.exit(main())
