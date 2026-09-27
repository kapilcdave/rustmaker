#!/usr/bin/env bash
# ARMED: penny-room 2. REAL ORDERS. Extra flags pass through to `live`.
exec "$(dirname "$0")/penny.sh" 2 "$@"
