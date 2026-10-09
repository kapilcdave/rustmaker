#!/usr/bin/env bash
# Sweep orders the live engine could not sweep for itself.
#
# The engine cancels everything on SIGINT/SIGTERM and verifies the book is empty. It cannot do that
# on SIGKILL -- an OOM kill on a 945 MB box, or a reboot -- and an orphaned resting order can fill
# and ride to settlement: one naked contract at ~92c settling the wrong way is -$0.92, and nine
# co-expiring markets is -$8, more than the whole drawdown budget the caps enforce.
#
# Fires only when BOTH the engine and its supervisor are gone, so the supervisor's normal
# between-run settlement wait (up to ~15 min with no engine running) is not mistaken for a death.
# Cancels once, verifies, logs, and keeps watching.
#
#   Launch: cd ~/trading/kalshi-mm15 && ENV_FILE=~/.config/kalshi/env \
#             nohup setsid runners/orphan_watchdog.sh > data/live_penny/watchdog.log 2>&1 &
#   Stop:   touch ~/trading/kalshi-mm15/WATCHDOG_STOP
set -uo pipefail
cd "$(dirname "$0")/.."

ENV_FILE="${ENV_FILE:-$HOME/.config/kalshi/env}"
BIN="${BIN:-../kalshi-mm15-penny4/kalshi-mm15-amend}"
EVERY_S="${EVERY_S:-60}"
log() { echo "$(date -u +%FT%TZ) $*"; }

# Match on the full command line, never a bare name: `pgrep -f` inside a shell matches the shell
# that is running the pattern (that has cost a wrong kill here before).
engine_up() { pgrep -f "kalshi-mm15.* live --armed" | grep -qv "^$$\$"; }
supervisor_up() { pgrep -f "^bash runners/penny_supervisor.sh" >/dev/null; }

log "watchdog start pid=$$ (checks every ${EVERY_S}s; cancels only when engine AND supervisor are both gone)"
swept=0
while [ ! -f WATCHDOG_STOP ]; do
    sleep "$EVERY_S"
    if engine_up || supervisor_up; then
        swept=0
        continue
    fi
    # Both gone. One sweep per death, not one per tick.
    if [ "$swept" = 1 ]; then continue; fi
    log "engine AND supervisor both absent -- sweeping any resting order"
    if "$BIN" cancel-all --env-file "$ENV_FILE" 2>&1 | sed 's/^/    /'; then
        log "sweep returned 0"
    else
        log "sweep FAILED -- check the account by hand"
    fi
    swept=1
done
log "watchdog stop: WATCHDOG_STOP present"
