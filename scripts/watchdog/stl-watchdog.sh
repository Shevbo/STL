#!/usr/bin/env bash
# STL continuous trading watchdog. Runs on smain (cron every 10 min in FORTS
# session 07:00-23:50 MSK, weekends included). SMSes ONLY on problems, one SMS
# per problem key per COOLDOWN, plus a single «восстановилось» when all clear.
# WATCHDOG_TEST=1 forces a test problem end-to-end (proves SMS delivery).
#
# Cutover 2026-08-05: SMS через send-sms.sh (LAN direct via shevbo-pi →
# 192.168.1.128:8080). Legacy WG-probe (wg show wg0 dump ...) удалён — телефон
# больше не в WG, sms_gateway|молчит алерт был мёртв (host != WG peer IP).

set -uo pipefail
CRED="$HOME/.keymaster/credentials"
STATE="$HOME/.stl-watchdog"; mkdir -p "$STATE"
COOLDOWN=3600   # s, per problem key

# Категории, которые НЕ будят SMS: уходят ТОЛЬКО в Телеграм. Затянувшийся
# бэктест не торгует и ничего не теряет, пока считается, так что ТГ хватает,
# а смс-кой оператора будить незачем (09.08.2026, просьба оператора). Гасить
# такую категорию через escalate в вотчдоге НЕЛЬЗЯ: probe тогда не печатает
# строку вовсе, и она пропадает из ОБОИХ каналов сразу.
TG_ONLY_KEYS="backtest_stuck"
tg_only() {
  local k="$1"
  for pat in $TG_ONLY_KEYS; do
    case "$k" in $pat*) return 0;; esac
  done
  return 1
}
TS=$(date '+%Y-%m-%d %H:%M:%S %Z')
NOW=$(date +%s)

# Trading window gate 06:55-23:55 MSK.
HM=$((10#$(date +%H) * 60 + 10#$(date +%M)))
if [ "${WATCHDOG_TEST:-0}" != "1" ] && { [ "$HM" -lt 415 ] || [ "$HM" -gt 1435 ]; }; then
  exit 0
fi

PHONE=$(cat "$CRED/stl_sms_phone" 2>/dev/null || true)
[ -z "$PHONE" ] && { echo "$TS ERROR: stl_sms_phone missing"; exit 1; }

if [ "${WATCHDOG_TEST:-0}" = "1" ]; then
  PROBLEMS="selftest|ТЕСТ вотчдога: канал SMS работает."
else
  # Своя загрузка едет вместе с вызовом: пробник кладёт её в запись прогона, и
  # компаньон показывает LA обеих машин, а не только хостера (22.09.2026).
  SMAIN_LOAD=$(cut -d' ' -f1 /proc/loadavg 2>/dev/null)
  PROBLEMS=$(ssh -o BatchMode=yes -o ConnectTimeout=15 hoster \
    "SMAIN_LOAD='${SMAIN_LOAD}' bash ~/stl-watchdog-probe.sh" 2>/dev/null)
  RC=$?
  if [ $RC -ne 0 ]; then
    PROBLEMS="hoster_down|hoster недоступен по ssh: состояние торговли неизвестно."
  fi
fi

# ── ТГ (второй канал алерта, не подмена SMS)
[ -f "$HOME/.stl_tg.env" ] && . "$HOME/.stl_tg.env"
send_tg() {
  local text="$1"
  [ -z "${STL_TG_RELAY_URL:-}" ] && { echo "$TS tg skipped: нет STL_TG_RELAY_URL"; return 1; }
  local body; body=$(ACC="${STL_TG_ACCOUNT:-klod}" CHAT="${STL_TG_CHAT_ID:-}" TEXT="$text" \
    python3 -c 'import os,json;print(json.dumps({"account":os.environ["ACC"],"chat_id":os.environ["CHAT"],"text":os.environ["TEXT"]},ensure_ascii=False))')
  local http; http=$(curl -s -m 15 -o /dev/null -w '%{http_code}' -X POST "$STL_TG_RELAY_URL" \
    -H "Content-Type: application/json" --data-binary "$body")
  echo "$TS tg http=$http"
  [ "$http" = "200" ]
}

send_sms() {
  local text="STL-вотчдог $(date +%H:%M): $1"
  POLL_TIMEOUT=90 /home/shectory/bin/send-sms.sh "$PHONE" "$text"
  local rc=$?
  echo "$TS sms rc=$rc text=[$text]"
  [ $rc -ne 0 ] && { send_tg "SMS не доставлена (rc=$rc). $text" || true; }
  # Escalation mark (best-effort; joined to probe runs by timestamp on hoster).
  TEXT="$text" RC="$rc" python3 -c 'import os,json,time;print(json.dumps({"ts_ms":int(time.time()*1000),"text":os.environ["TEXT"],"rc":int(os.environ["RC"])},ensure_ascii=False))' \
    | ssh -o BatchMode=yes -o ConnectTimeout=10 hoster 'cat >> ~/stl-watchdog-escalations.jsonl' 2>/dev/null || true
}

HAD_PROBLEMS=0; [ -s "$STATE/active" ] && HAD_PROBLEMS=1

if [ -z "$PROBLEMS" ]; then
  if [ "$HAD_PROBLEMS" = "1" ]; then
    send_sms "восстановилось: все проверки снова в норме."
    rm -f "$STATE/active"
  fi
  echo "$TS ok"
  exit 0
fi

: > "$STATE/active.new"
SENT=0
while IFS='|' read -r key text; do
  [ -z "$key" ] && continue
  echo "$key" >> "$STATE/active.new"
  last=$(cat "$STATE/sent_$key" 2>/dev/null || echo 0)
  if [ $((NOW - last)) -ge $COOLDOWN ]; then
    if tg_only "$key"; then
      send_tg "STL-вотчдог $(date +%H:%M): $text" || true
      echo "$TS tg-only $key: $text"
    else
      send_sms "$text"
      SENT=$((SENT+1))
    fi
    echo "$NOW" > "$STATE/sent_$key"
  else
    echo "$TS suppressed(cooldown) $key: $text"
  fi
done <<< "$PROBLEMS"
mv -f "$STATE/active.new" "$STATE/active"
echo "$TS problems=$(wc -l < "$STATE/active") sms_sent=$SENT"
