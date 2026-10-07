#!/usr/bin/env bash
# ARMED supervisor for the capped penny engine: REAL ORDERS, relaunched unattended.
# Runs the engine back to back (8 h deadline or $2 session trip), and between runs:
#   1. waits for the current 15-min round to settle (next quarter hour + 120 s), so leftovers
#      are cash, not marks;
#   2. reads the crypto shard's CASH balance from the venue (shard.py), and stops for good if it
#      is below the floor;
#   3. sizes the next session cap as min(SESSION_CAP_C, balance - floor), so one run cannot take the
#      account much below the floor (a leftover can still settle against us after the trip).
# The floor is the COLLATERAL the seat needs to display its quotes; the drawdown budget sits on top
# of it and is sized off the seat's own measured round variance, not off the account. The engine's
# cumulative baseline (live_state.json) is re-based at supervisor start, so a deposit or withdrawal
# between launches never leaves a stale absolute number behind. A withdrawal DURING a supervisor's
# life reads as a loss and stops it (the safe direction); relaunch to re-base.
# Stops for good on: balance < floor, headroom < a fifth of the drawdown, an engine that exits within 120 s (it
# refused to start or crashed), or a STOP file next to this script's working dir.
#   Launch:  cd ~/trading/kalshi-mm15 && rm -f STOP && \
#              ENV_FILE=~/.config/kalshi/env CLIP=1 nohup setsid runners/penny_supervisor.sh \
#              > data/live_penny/supervisor.log 2>&1 &
#   Stop:    touch ~/trading/kalshi-mm15/STOP   (the engine running now finishes its run), or
#            kill -INT <engine pid> as well to end it now (clean cancel sweep).
set -uo pipefail
cd "$(dirname "$0")/.."

# Contracts per order. Position cap = CLIP, round net cap = 2 x CLIP (two fills, as replayed at 1 ct),
# venue group limit = 12 x CLIP.
CLIP="${CLIP:-1}"
BIN="${BIN:-../kalshi-mm15-penny4/kalshi-mm15}"
# The credential. Default is deliberately the DEAD key in ./.env: a dead credential is the only
# thing that keeps a forgotten supervisor inert, so pointing this at the live key is the ARMING
# act and has to be typed (ENV_FILE=~/.config/kalshi/env ... runners/penny_supervisor.sh).
ENV_FILE="${ENV_FILE:-.env}"
# ⛔ Opening band OFF by default. PREREG_open_band FAILED on 440 settled markets (-1.241 c/mkt,
# lo95 -6.0, both halves negative) and the mechanism is in FINDINGS_open_band_20261007.md: on a
# tapered_deci_cent ladder the tick is 0.1c in the wings and 1c in the middle, so --penny-room 4
# means 0.4c of room in the wings and 4.0c in the middle. It passes 34-39% of wing states against
# 5-6% of mid-band ones, and 64-76% of all its passes are in the wings -- which a 10-90c band
# forbids opening in. A tick-denominated width gate and a cent-denominated band do not compose.
OPEN_MIN_C="${OPEN_MIN_C:-0}"
OPEN_MAX_C="${OPEN_MAX_C:-100}"
# Coinbase spot guard, bps over 1 s; 0 turns it off (commodities: there is no spot feed for them).
SPOT_BPS="${SPOT_BPS:-2}"
# Quote one tick inside only when the spread is at least PENNY_ROOM ticks.
PENNY_ROOM="${PENNY_ROOM:-4}"
# ⚑ Two INDEPENDENT floors, both measured (FINDINGS_open_band_20261007.md). A percent-of-balance
# drawdown was the wrong shape: this seat's risk is an absolute dollar quantity that scales with
# CLIP, not with the account.
#   1. COLLATERAL: a two-sided 1-ct quote locks ~$1 per market whatever the price, so quoting 9
#      series needs ~100c x series x clip before any inventory. Zero `insufficient` rejects across
#      31 runs / 1,056,028 posts at balances $8.98-$46.65; at $2.86 the venue refused 11,227 of
#      19,806 posts (57%). Below this the engine cannot display its own quotes.
#   2. DRAWDOWN: round-level P&L is mean +16.0c, sd 75.4c, worst -175c, observed max DD $3.97 over
#      123 rounds at clip 1. A $1-2 session cap -- what the 09-29..10-02 runs ran -- is BELOW the
#      seat's own observed drawdown, so it fires on noise, locks the loss, strands the position
#      without its exit leg and blocks re-entry through the settle wait. Every cap-killed run in
#      the history is -4 to -12 c/mkt. Default is the measured max DD, scaled by clip.
COLLATERAL_C="${COLLATERAL_C:-}"            # default: 100c x n_series x clip
MAX_DD_C="${MAX_DD_C:-$(( 400 * CLIP ))}"   # total drawdown budget
SESSION_CAP_C="${SESSION_CAP_C:-$MAX_DD_C}" # per-run cap; never larger than the total budget
SERIES="${SERIES:-KXBTC15M,KXETH15M,KXSOL15M,KXXRP15M,KXDOGE15M,KXHYPE15M,KXBNB15M,KXZEC15M,KXNEAR15M}"
log() { echo "$(date -u +%FT%TZ) $*"; }

shard2_cents() {
    python3 shard.py "$ENV_FILE" | python3 -c 'import sys,ast; l=sys.stdin.read(); print(int(round(ast.literal_eval(l.split("breakdown $: ")[1].strip())[2]*100)))'
}

wait_for_settlement() {
    local now next
    now=$(date -u +%s)
    next=$(( (now / 900 + 1) * 900 + 120 ))
    log "waiting $((next - now)) s for the round to settle"
    sleep $((next - now))
}

# Preflight, before any order can exist. Each line is a go/no-go the old script did not have.
[ -x "$BIN" ] || { log "PREFLIGHT FAIL: $BIN is not an executable"; exit 1; }
[ -f "$ENV_FILE" ] || { log "PREFLIGHT FAIL: credential $ENV_FILE does not exist"; exit 1; }
n_series=$(awk -F, '{print NF}' <<<"$SERIES")
: "${COLLATERAL_C:=$(( 100 * n_series * CLIP ))}"
[ "$SESSION_CAP_C" -le "$MAX_DD_C" ] || { log "PREFLIGHT FAIL: session cap ${SESSION_CAP_C}c exceeds the total drawdown budget ${MAX_DD_C}c"; exit 1; }
# One AUTHENTICATED read, because no unauthenticated endpoint can report on the control channel
# (topics-live-arming-and-plumbing). This is also the balance read, on the TRADING SHARD.
bal0=$(shard2_cents) || { log "PREFLIGHT FAIL: authenticated shard-2 balance read failed on $ENV_FILE"; exit 1; }
FLOOR_C="$COLLATERAL_C"
need=$(( COLLATERAL_C + MAX_DD_C ))
if [ "$bal0" -lt "$need" ]; then
    log "PREFLIGHT FAIL: shard 2 has ${bal0}c; this config needs ${need}c = ${COLLATERAL_C}c collateral"
    log "  (${n_series} series x clip ${CLIP}) + ${MAX_DD_C}c drawdown budget. Fund the shard or lower CLIP/MAX_DD_C."
    exit 1
fi
MIN_HEAD_C=$(( MAX_DD_C / 5 ))
[ -f data/live_penny/live_state.json ] && mv data/live_penny/live_state.json "data/live_penny/live_state_$(date -u +%Y%m%dT%H%M%SZ).json"
log "PREFLIGHT OK: bin $BIN, cred $ENV_FILE, shard 2 cash ${bal0}c >= ${need}c"
log "start: collateral floor ${COLLATERAL_C}c, drawdown budget ${MAX_DD_C}c, per-run cap ${SESSION_CAP_C}c; engine baseline re-based"

n=0
while true; do
    [ -f STOP ] && { log "STOP file present; exiting"; exit 0; }
    bal=$(shard2_cents) || { log "balance read failed; exiting"; exit 1; }
    head=$(( bal - FLOOR_C ))
    log "shard 2 cash ${bal}c, floor ${FLOOR_C}c, headroom ${head}c"
    [ "$bal" -lt "$FLOOR_C" ] && { log "below floor; exiting"; exit 0; }
    [ "$head" -lt "$MIN_HEAD_C" ] && { log "headroom under ${MIN_HEAD_C}c (a fifth of the drawdown); exiting"; exit 0; }
    cap=$(( head < SESSION_CAP_C ? head : SESSION_CAP_C ))
    n=$((n + 1))
    out="data/live_penny/sup_$(date -u +%Y%m%dT%H%M%SZ).log"
    band=$([ "$OPEN_MIN_C" = 0 ] && [ "$OPEN_MAX_C" = 100 ] && echo "off" || echo "${OPEN_MIN_C}-${OPEN_MAX_C}c")
    log "run $n: ${SERIES}, spot ${SPOT_BPS} bps, penny room ${PENNY_ROOM}, clip ${CLIP}, open band ${band}, session cap ${cap}c -> $out"
    start=$(date -u +%s)
    "$BIN" live --armed --env-file "$ENV_FILE" --penny-room "$PENNY_ROOM" --amend --spot-bps "$SPOT_BPS" --max-round-net $((2 * CLIP)) \
        --clip "$CLIP" --max-pos "$CLIP" --group-limit $((12 * CLIP)) \
        --stop-before-close-s 450 --open-cutoff-s 450 --open-min-c "$OPEN_MIN_C" --open-max-c "$OPEN_MAX_C" --series "$SERIES" --minutes 480 \
        --max-loss-c "$cap" --cum-max-loss-c "$MAX_DD_C" --out data/live_penny > "$out" 2>&1 < /dev/null
    rc=$?
    ran=$(( $(date -u +%s) - start ))
    log "run $n exited rc=$rc after ${ran}s: $(grep '^end equity' "$out" | tail -1)"
    [ "$ran" -lt 120 ] && { log "engine exited within 120 s ($(tail -1 "$out")); exiting"; exit 1; }
    wait_for_settlement
done
