#!/bin/bash
# LAN-side i9 reachability probe (runs on the pi via cron, every minute).
# Pings the i9 and reports up/down to the hoster so the VDS task-fallback knows
# whether the i9 is truly down (take over) vs just its agent hiccupped (leave it).
I9="${I9_HOST:-192.168.1.70}"
API="${STL_API:-https://stl.shectory.ru}"
TOK="$(cat "$HOME/.stl_token" 2>/dev/null)"
if ping -c 1 -W 2 "$I9" >/dev/null 2>&1; then P=true; else P=false; fi
curl -s -m 10 -X POST "$API/api/v1/agent/i9-status" \
  -H "X-Agent-Token: $TOK" -H "Content-Type: application/json" \
  -d "{\"pingable\": $P, \"src\": \"pi\"}" >/dev/null 2>&1
