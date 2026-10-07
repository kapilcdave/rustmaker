#!/usr/bin/env bash
# Follow the running live engine's order flow, one line per event, from the newest journal.
# The journal is gzip, so it is decompressed from the start and rows older than the lookback are
# dropped (a 200 MB journal takes ~10 s to catch up). Usage on the box:
#   runners/watch_orders.sh            # posts, amends, fills, rejects, spot pulls, undercuts
#   runners/watch_orders.sh fills      # fills only
#   runners/watch_orders.sh all        # also cancel acks
#   LOOKBACK_S=600 runners/watch_orders.sh   # start 10 min back (default 60 s)
set -uo pipefail
cd "$(dirname "$0")/../data/live_penny"
f=$(ls -t live_*.jsonl.gz | head -1)
since=$(( ($(date +%s) - ${LOOKBACK_S:-60}) * 1000000 ))
case "${1:-}" in
    fills) kinds='fill' ;;
    all)   kinds='new|amend|fill|reject_new|reject_amend|reject_cancel|spot_pull|undercut|ack_cancel|order_group_trip' ;;
    *)     kinds='new|amend|fill|reject_new|reject_amend|reject_cancel|spot_pull|undercut|order_group_trip' ;;
esac
echo "following $f (lookback ${LOOKBACK_S:-60}s)" >&2
tail -c +1 -f "$f" | zcat 2>/dev/null \
  | grep --line-buffered -E "^\{\"k\":\"($kinds)\"" \
  | jq --unbuffered -r --argjson since "$since" '
      select(.t >= $since)
      | ((.t/1e6|floor|strftime("%H:%M:%S")) + "." + ((.t/1000|floor)%1000|tostring|("00"+.)[-3:])) as $ts
      | (.v.coid // .v.client_order_id // "" | .[0:6]) as $id
      | if .k == "new" then
          "\($ts) NEW    \(.v.body.ticker)  \(.v.body.side) \(.v.body.price) x\(.v.body.count)  mid \(.v.mid)  \($id)"
        elif .k == "amend" then
          "\($ts) AMEND  \(.v.body.ticker)  \(.v.body.side) \(.v.body.price) x\(.v.body.count)  mid \(.v.mid)  \($id)"
        elif .k == "fill" then
          "\($ts) FILL   \(.v.market_ticker)  \(if .v.book_side == "ask" then "SELL" else "BUY " end) yes@\(.v.yes_price_dollars) x\(.v.count_fp)  pos \(.v.post_position_fp)  \(if .v.is_taker then "TAKER" else "maker" end)  \($id)"
        elif .k == "spot_pull" then
          "\($ts) PULL   spot move \(.v.r_bps*100|round/100) bps  \($id)"
        elif .k == "undercut" then
          "\($ts) UNDER  ours \(.v.our_px/100)c theirs \(.v.their_px/100)c  \($id)"
        elif .k == "ack_cancel" then
          "\($ts) CXL    \($id)"
        elif (.k|startswith("reject")) then
          "\($ts) \(.k|ascii_upcase)  \(.v.err | capture("\"details\":\"(?<d>[^\"]*)\"").d? // .[0:90])  \($id)"
        else "\($ts) \(.k)  \(.v|tostring|.[0:120])" end'
