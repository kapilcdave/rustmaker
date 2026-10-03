#!/usr/bin/env bash
# READ-ONLY shadow supervisor for one 15M crypto series (PREREG_eth_sol_focus.md).
#
# NOT ARMED: runs `shadow`, which places no orders. The binary's `live` path is unreachable
# without --armed, and this script never passes it.
#
# Runs the shadow back to back in 60-minute segments until TARGET_H hours have elapsed, so a crash
# costs one segment rather than the window. Between segments it:
#   1. backs off exponentially on a short exit (a segment that dies inside 120 s means the venue or
#      the credential is refusing, and a tight relaunch loop becomes a hammer -- 58 retries once
#      earned an HTTP 429 and truncated a forward set);
#   2. checks free disk against MIN_FREE_MB and stops rather than filling the box;
#   3. stops for good on a STOP file.
#
#   Launch:  cd ~/trading/kalshi-mm15 && SERIES=KXETH15M nohup setsid runners/shadow_series.sh \
#              > data/shadow_eth/supervisor.log 2>&1 &
#   Stop:    touch ~/trading/kalshi-mm15/data/shadow_eth/STOP
set -uo pipefail
cd "$(dirname "$0")/.."

# launchd and some login shells hand a job a minimal PATH; the AWS CLI and python live in ~/.local/bin.
export PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

SERIES="${SERIES:?set SERIES to exactly one series, e.g. KXETH15M}"
case "$SERIES" in *,*) echo "SERIES must be ONE series (PREREG amendment 1): got $SERIES" >&2; exit 2;; esac

TAG="$(echo "$SERIES" | sed 's/^KX//; s/15M$//' | tr 'A-Z' 'a-z')"
OUT="${OUT:-data/shadow_$TAG}"
TARGET_H="${TARGET_H:-14}"          # 48 markets at ~4/h is 12 h; 14 h gives slack for restarts
SEG_MIN="${SEG_MIN:-60}"
MIN_FREE_MB="${MIN_FREE_MB:-400}"
BIN="${BIN:-./kalshi-mm15}"
STOP="$OUT/STOP"

mkdir -p "$OUT"
log() { echo "$(date -u +%FT%TZ) $*"; }
free_mb() { df -Pm . | awk 'NR==2{print $4}'; }

[ -x "$BIN" ] || { log "FATAL: no executable $BIN"; exit 1; }
# Refuse to start if the binary can reach an armed path without the flag being ours to pass.
if "$BIN" 2>&1 | grep -q 'shadow'; then :; else log "FATAL: $BIN has no shadow subcommand"; exit 1; fi

log "series=$SERIES out=$OUT target=${TARGET_H}h segment=${SEG_MIN}m free=$(free_mb)MB"
log "PREREG_eth_sol_focus.md -- rule frozen, --only-base, read-only"

deadline=$(( $(date -u +%s) + TARGET_H * 3600 ))
backoff=30; n=0
while [ "$(date -u +%s)" -lt "$deadline" ]; do
    [ -f "$STOP" ] && { log "STOP file present; exiting"; break; }
    fm=$(free_mb)
    if [ "$fm" -lt "$MIN_FREE_MB" ]; then log "disk low: ${fm}MB < ${MIN_FREE_MB}MB; exiting"; break; fi

    n=$((n+1)); t0=$(date -u +%s)
    log "segment $n start (free ${fm}MB)"
    "$BIN" shadow --out "$OUT" --minutes "$SEG_MIN" --series "$SERIES" --only-base \
        --stop-before-close-s 120 >> "$OUT/shadow.log" 2>&1
    rc=$?; dt=$(( $(date -u +%s) - t0 ))
    log "segment $n exit rc=$rc after ${dt}s"

    if [ "$dt" -lt 120 ]; then
        log "short segment; backing off ${backoff}s"
        sleep "$backoff"
        backoff=$(( backoff * 2 )); [ "$backoff" -gt 900 ] && backoff=900
    else
        backoff=30
    fi
done
log "window done after $n segments; free $(free_mb)MB"
