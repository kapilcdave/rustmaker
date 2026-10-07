#!/usr/bin/env python3
"""Read Kalshi per-shard balances; optionally move DOLLARS from shard A to B.
usage: shard.py ENV_FILE                 (read only)
       shard.py ENV_FILE SRC DST DOLLARS (sends the transfer)

Signs RSA-PSS or Ed25519 depending on the key. Transfer amount is in CENTICENTS on the wire
(dollars x 10,000) and the move is NOT atomic -- show() is polled until the destination lands."""
import base64, json, sys, time, requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, padding

HOST = "https://api.elections.kalshi.com"
BASE = "/trade-api/v2"

def load_env(path):
    vals, key, cur = {}, None, None
    for line in open(path):
        s = line.rstrip("\n")
        if cur is not None:
            vals[cur] += "\n" + s
            if "-----END" in s:
                cur = None
            continue
        if "=" in s and not s.lstrip().startswith("#"):
            k, v = s.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            vals[k] = v
            if "-----BEGIN" in v and "-----END" not in v:
                cur = k
    return vals

env = load_env(sys.argv[1])
kid = env.get("KALSHI_API_KEY_ID") or env["KALSHI_API_KEY"]
pem = env.get("KALSHI_PRIVATE_KEY")
if not pem:
    pem = open(env["KALSHI_PRIVATE_KEY_PATH"]).read()
pem = pem.replace("\\n", "\n")
priv = serialization.load_pem_private_key(pem.encode(), password=None)
# Kalshi verifies either key type and the ONE live credential is Ed25519 (2e88fe77). This script was
# RSA-PSS only, so it raised on the live key -- and penny_supervisor.sh reads the trading shard's
# balance THROUGH here, so an armed launch would have died at its own balance gate. Same two-line
# branch as src/auth.rs: Ed25519 signs the identical {ts}{METHOD}{path} bytes raw (64-byte sig).
IS_ED25519 = isinstance(priv, ed25519.Ed25519PrivateKey)

def sign(msg):
    if IS_ED25519:
        return priv.sign(msg)
    return priv.sign(msg, padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                     salt_length=padding.PSS.DIGEST_LENGTH), hashes.SHA256())

def req(method, path, body=None):
    ts = str(int(time.time() * 1000))
    msg = (ts + method + BASE + path.split("?")[0]).encode()
    sig = sign(msg)
    h = {"KALSHI-ACCESS-KEY": kid, "KALSHI-ACCESS-TIMESTAMP": ts,
         "KALSHI-ACCESS-SIGNATURE": base64.b64encode(sig).decode(),
         "Content-Type": "application/json"}
    r = requests.request(method, HOST + BASE + path, headers=h,
                         data=json.dumps(body) if body else None, timeout=15)
    if r.status_code >= 300:
        raise SystemExit(f"{method} {path} -> {r.status_code} {r.text}")
    return r.json()

def show():
    p = req("GET", "/portfolio/balance")
    rows = {e.get("exchange_index"): float(e.get("balance") or 0)
            for e in p.get("balance_breakdown") or []}
    print(f"total cents {p.get('balance')}  breakdown $: {rows}")
    return rows

rows = show()
if len(sys.argv) == 5:
    src, dst, dollars = int(sys.argv[2]), int(sys.argv[3]), float(sys.argv[4])
    assert src != dst and 0 < dollars <= rows.get(src, 0) + 1e-9, "insufficient on source"
    body = {"source": "event_contract", "destination": "event_contract",
            "amount": int(round(dollars * 10_000)),
            "source_exchange_shard": src, "destination_exchange_shard": dst,
            "source_subaccount": 0, "destination_subaccount": 0}
    print("POST", json.dumps(body))
    print(req("POST", "/portfolio/intra_exchange_instance_transfer", body))
    for _ in range(12):
        time.sleep(2)
        r = show()
        if r.get(dst, 0) >= dollars - 1e-6:
            break
