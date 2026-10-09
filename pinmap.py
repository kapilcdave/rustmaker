#!/usr/bin/env python3
"""Pick the Kalshi ELB node to pin this launch to, and print it as shell exports.

Why: `external-api-ws` and `external-api` each serve ~8-14 A records split cleanly into a
local group (TCP p50 ~0.26-0.31 ms) and a far group (~0.83-0.88 ms), and DNS round-robin
takes no view. Measured 2026-10-09 on the az2 box, the LIVE engine had drawn
`3.128.58.102` for its feed -- the slowest of the 8 WS records -- costing ~0.3 ms one-way
on the leg that is 68% of reaction (FINDINGS_signing_and_transport_20261006.md). Pinning
the fastest verified node is worth ~0.3 ms of a ~6.5 ms feed_age and ~0.2 ms of each order
round trip.

The IPs ROTATE -- 8 records on 10-07, 14 on 10-09, barely overlapping -- so this must run
at every launch and nothing may be hardcoded (ohio-box-az-probe-and-clone-hazard).

**Fails open.** stdout is empty unless a node was verified reachable, so a supervisor that
does `eval "$(pinmap.py)"` keeps plain DNS behaviour whenever the map is inconclusive.
Diagnostics go to stderr.

  WS   verified by a full TLS handshake (hostname-validated); ranked on TCP p50, because no
       unauthenticated app RTT exists on the WS path.
  REST verified by HTTP 200 on /exchange/status at ~4 req/s, and ranked on app RTT -- TCP
       rank does NOT predict app rank there (10-07: fastest connect was the third fastest
       request), and an unverified RTT is how the 10-06 numbers got 429-contaminated.

Usage:  eval "$(python3 -I pinmap.py)"      # exports KALSHI_WS_IP / KALSHI_REST_IP
        python3 -I pinmap.py --ws-only      # REST left on DNS (see below)

`--ws-only` is the ARMED default and the reason is the failure mode, not the prize: a stale
WS pin makes the engine blind, and `live.rs:1607` cancels everything before reconnecting, so
the seat goes flat and dark. A stale REST pin would break that cancel sweep itself. Pin REST
only once `connect_ws`/`client()` fall back to DNS on a dead address.
"""
import socket
import ssl
import statistics
import sys
import time

WS_HOST = "external-api-ws.kalshi.com"
REST_HOST = "external-api.kalshi.com"
PORT = 443
DNS_ROUNDS = 8
TCP_N = 15
HTTP_N = 10
HTTP_RATE_S = 0.25
# Local vs far is a ~3x split, so key off the observed minimum rather than an absolute
# threshold: the absolute level moves with the box's placement, the ratio has not.
LOCAL_RATIO = 2.0
LOCAL_FLOOR_MS = 0.20
# Candidates to verify at the application layer. 3 covers the local group's spread (0.2 ms)
# without turning launch into a probe run.
VERIFY_N = 3


def log(msg):
    print(msg, file=sys.stderr)


def resolve(host, rounds=DNS_ROUNDS):
    ips = set()
    for _ in range(rounds):
        try:
            for *_, sa in socket.getaddrinfo(host, PORT, socket.AF_INET, socket.SOCK_STREAM):
                ips.add(sa[0])
        except socket.gaierror:
            pass
        time.sleep(0.15)
    return sorted(ips)


def tcp_p50(ip, n=TCP_N):
    xs = []
    for _ in range(n):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(3.0)
        t0 = time.perf_counter()
        try:
            s.connect((ip, PORT))
            xs.append((time.perf_counter() - t0) * 1e3)
        except OSError:
            pass
        finally:
            s.close()
        time.sleep(0.015)
    return statistics.median(xs) if len(xs) >= n // 2 else None


def local_group(host):
    """[(tcp_p50_ms, ip)] for the near-AZ nodes, fastest first. Empty if nothing resolved."""
    ips = resolve(host)
    rows = [(p, ip) for ip, p in ((ip, tcp_p50(ip)) for ip in ips) if p is not None]
    if not rows:
        log(f"{host}: nothing resolved or every node refused -- leaving DNS alone")
        return []
    rows.sort()
    cut = max(rows[0][0] * LOCAL_RATIO, rows[0][0] + LOCAL_FLOOR_MS)
    near = [r for r in rows if r[0] <= cut]
    log(f"{host}: {len(rows)} records, {len(near)} local (tcp <= {cut:.3f} ms)"
        f" | best {near[0][1]} {near[0][0]:.3f} ms, worst {rows[-1][1]} {rows[-1][0]:.3f} ms")
    return near


def tls_ok(ip, host):
    """A hostname-validated TLS handshake: proves the node is live and fronting `host`."""
    try:
        ctx = ssl.create_default_context()
        raw = socket.create_connection((ip, PORT), timeout=5)
        with ctx.wrap_socket(raw, server_hostname=host):
            return True
    except (OSError, ssl.SSLError):
        return False


def http_p50(ip, host, n=HTTP_N):
    """Keep-alive app RTT on an unsigned GET. Returns None unless EVERY reply was a 200."""
    try:
        ctx = ssl.create_default_context()
        raw = socket.create_connection((ip, PORT), timeout=5)
        raw.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock = ctx.wrap_socket(raw, server_hostname=host)
    except (OSError, ssl.SSLError):
        return None
    req = (f"GET /trade-api/v2/exchange/status HTTP/1.1\r\nHost: {host}\r\n"
           "Connection: keep-alive\r\nAccept: application/json\r\n\r\n").encode()
    xs = []
    try:
        for _ in range(n):
            t0 = time.perf_counter()
            sock.sendall(req)
            head = b""
            while b"\r\n\r\n" not in head:
                chunk = sock.recv(65536)
                if not chunk:
                    return None
                head += chunk
            dt = (time.perf_counter() - t0) * 1e3
            if b" 200 " not in head.split(b"\r\n", 1)[0]:
                return None
            xs.append(dt)
            time.sleep(HTTP_RATE_S)
    except (OSError, ssl.SSLError):
        return None
    finally:
        try:
            sock.close()
        except OSError:
            pass
    return statistics.median(xs) if xs else None


def pick_ws():
    for tcp, ip in local_group(WS_HOST)[:VERIFY_N]:
        if tls_ok(ip, WS_HOST):
            log(f"  WS   -> {ip}  tcp {tcp:.3f} ms, TLS ok")
            return ip
        log(f"  WS   skip {ip}: TLS handshake failed")
    return None


def pick_rest():
    scored = []
    for tcp, ip in local_group(REST_HOST)[:VERIFY_N]:
        app = http_p50(ip, REST_HOST)
        log(f"  REST {ip}  tcp {tcp:.3f}  app {'--' if app is None else f'{app:.3f}'} ms")
        if app is not None:
            scored.append((app, ip))
    if not scored:
        return None
    app, ip = min(scored)
    log(f"  REST -> {ip}  app p50 {app:.3f} ms (HTTP 200 x{HTTP_N})")
    return ip


def main():
    ws_only = "--ws-only" in sys.argv
    out = []
    try:
        ws = pick_ws()
        if ws:
            out.append(f"export KALSHI_WS_IP={ws}")
        rest = None if ws_only else pick_rest()
        if rest:
            out.append(f"export KALSHI_REST_IP={rest}")
        if ws_only:
            log("  REST left on DNS (--ws-only): a stale REST pin would break the cancel sweep")
    except Exception as e:  # fail open: a probe must never be able to stop a launch
        log(f"pinmap failed ({type(e).__name__}: {e}); leaving DNS alone")
        out = []
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
