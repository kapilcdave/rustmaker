#!/usr/bin/env python3
"""Map external-api.kalshi.com ELB nodes by TCP connect time, then gently
confirm the fastest few with HTTP-200 app RTT.

Read-only. TCP connects do not draw 429s; the HTTP phase is rate limited to
~4 req/s per IP and asserts status 200 before trusting any number
(see FINDINGS_latency_az_ip_20261006.md: unverified RTTs were contaminated).
"""
import socket
import ssl
import statistics
import sys
import time

HOST = "external-api.kalshi.com"
PORT = 443
TCP_N = 25
HTTP_N = 12


def resolve(rounds=12):
    ips = set()
    for _ in range(rounds):
        try:
            for fam, _, _, _, sa in socket.getaddrinfo(HOST, PORT, socket.AF_INET, socket.SOCK_STREAM):
                ips.add(sa[0])
        except socket.gaierror:
            pass
        time.sleep(0.25)
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
        time.sleep(0.02)
    return (statistics.median(xs), len(xs)) if xs else (float("nan"), 0)


def http_p50(ip, n=HTTP_N):
    """Keep-alive app RTT on GET /trade-api/v2/exchange/status; 200 only."""
    ctx = ssl.create_default_context()
    xs, bad = [], 0
    raw = socket.create_connection((ip, PORT), timeout=5)
    raw.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    sock = ctx.wrap_socket(raw, server_hostname=HOST)
    req = (
        f"GET /trade-api/v2/exchange/status HTTP/1.1\r\nHost: {HOST}\r\n"
        "Connection: keep-alive\r\nAccept: application/json\r\n\r\n"
    ).encode()
    try:
        for _ in range(n):
            t0 = time.perf_counter()
            sock.sendall(req)
            buf = b""
            while b"\r\n\r\n" not in buf:
                chunk = sock.recv(8192)
                if not chunk:
                    raise OSError("closed")
                buf += chunk
            head, _, rest = buf.partition(b"\r\n\r\n")
            status = int(head.split(b" ")[1])
            clen = 0
            for line in head.split(b"\r\n")[1:]:
                if line.lower().startswith(b"content-length:"):
                    clen = int(line.split(b":")[1])
            while len(rest) < clen:
                rest += sock.recv(8192)
            dt = (time.perf_counter() - t0) * 1e3
            if status == 200 and b"exchange_active" in rest:
                xs.append(dt)
            else:
                bad += 1
            time.sleep(0.25)
    finally:
        sock.close()
    return (statistics.median(xs) if xs else float("nan"), len(xs), bad)


def main():
    ips = resolve()
    print(f"{HOST}: {len(ips)} distinct A records\n")
    rows = []
    for ip in ips:
        p50, ok = tcp_p50(ip)
        rows.append((p50, ip, ok))
        print(f"  tcp {p50:7.3f} ms  ok {ok:2d}/{TCP_N}  {ip}")
    rows.sort()
    local = [r for r in rows if r[0] < 0.5]
    print(f"\nlocal-group (tcp < 0.5 ms): {len(local)} of {len(rows)}")

    cands = rows[: min(4, len(rows))]
    print(f"\nHTTP-200 app RTT, {HTTP_N} keep-alive reqs each at ~4/s:")
    best = None
    for p50tcp, ip, _ in cands:
        try:
            h, ok, bad = http_p50(ip)
        except OSError as exc:
            print(f"  {ip:16s} http FAILED {exc}")
            continue
        print(f"  {ip:16s} tcp {p50tcp:6.3f}  http p50 {h:7.3f} ms  ok {ok} bad {bad}")
        if ok >= HTTP_N // 2 and (best is None or h < best[1]):
            best = (ip, h)
    if best:
        print(f"\nFASTEST VERIFIED: {best[0]}  app p50 {best[1]:.3f} ms")
    else:
        print("\nno IP produced enough verified 200s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
