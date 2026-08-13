#!/usr/bin/env bash
# ПЕРЕДАЧА РАБОТЫ МЕЖДУ ОКНАМИ. Ничего не пропадает, никто не узнаёт последним.
#
#   bash scripts/handover.sh whatsnew    что сделали БЕЗ МЕНЯ (в начале работы)
#   bash scripts/handover.sh report      я закончил кусок: запушить + разослать
#
# ЗАЧЕМ. Окна на Windows-VM гаснут вместе со светом, а подхват на smain в это
# время работает. Пока он работает, остальные не знают об этом ничего. 13.08.2026
# его коммит сутки жил только на smain и заблокировал выкладку чужого фикса —
# ровно тот случай, ради которого этот файл и написан.
#
# ДВА ПРАВИЛА, ОБА ЖЁСТКИЕ:
#   не запушено = не существует. Коммит в своей папке для других окон невидим;
#   не разослано = не передано. Владелец зоны обязан узнать, что в ней трогали.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"
WIN="${STL_WINDOW:-$( [ -f CLAUDE.local.md ] && sed -n 's/^# ТЫ ОКНО «\([^»]*\)».*/\1/p' CLAUDE.local.md | head -1 )}"
MARK="${STL_HANDOVER_MARK:-$HOME/.stl-handover-$WIN}"
SSH_HOST="${STL_DEVMAIL_SSH:-hoster}"
DEVMSG="bash ~/apps/shectory-trader/scripts/devmsg.sh"

[ -n "$WIN" ] || { echo "Не понял, какое я окно: нет ни STL_WINDOW, ни CLAUDE.local.md"; exit 2; }

# Кто владеет файлом. Порядок важен: первое совпадение выигрывает.
owner_of() {
  case "$1" in
    proto/*|quik_agent/*|robot_runner/*)        echo real-trade ;;
    trader/lab/*|scripts/queue_campaign.py)     echo backtests ;;
    frontend/*|companion/*|trader/api/*)        echo ui-ux ;;
    *)                                          echo "" ;;
  esac
}

case "${1:-}" in
  whatsnew)
    git fetch -q origin 2>/dev/null
    since="$(cat "$MARK" 2>/dev/null || true)"
    [ -n "$since" ] || since="origin/main~15"
    n="$(git rev-list --count "$since"..origin/main 2>/dev/null || echo 0)"
    if [ "$n" = "0" ]; then
      echo "С прошлого раза ничего нового."
    else
      echo "=== СДЕЛАНО БЕЗ ТЕБЯ: $n коммит(ов) ==="
      git log --oneline --no-decorate "$since"..origin/main 2>/dev/null | head -40
      echo
      echo "Твоей зоны касаются:"
      git diff --name-only "$since"..origin/main 2>/dev/null | while read -r f; do
        [ "$(owner_of "$f")" = "$WIN" ] && echo "  $f"
      done | sort -u | head -30
      echo
      echo "Подтянуть: git pull --ff-only"
    fi
    git rev-parse origin/main > "$MARK" 2>/dev/null || true
    ;;

  report)
    # 1) Запушить. Незапушенный коммит для остальных не существует.
    git push -q origin HEAD 2>&1 | tail -2
    since="$(git rev-parse origin/main@{1} 2>/dev/null || echo 'HEAD~10')"
    commits="$(git log --oneline --no-decorate "$since"..HEAD 2>/dev/null | head -20)"
    [ -n "$commits" ] || { echo "нечего передавать: новых коммитов нет"; exit 0; }
    # 2) Разослать ВЛАДЕЛЬЦАМ затронутых зон, каждому про его файлы.
    for w in real-trade backtests ui-ux; do
      [ "$w" = "$WIN" ] && continue
      files="$(git diff --name-only "$since"..HEAD 2>/dev/null | while read -r f; do
                 [ "$(owner_of "$f")" = "$w" ] && echo "  $f"; done | sort -u | head -25)"
      [ -n "$files" ] || continue
      body="Пока твоё окно было недоступно, я работал в его зоне. Всё запушено в origin/main, забирай через git pull --ff-only.

Коммиты:
$commits

Файлы ТВОЕЙ зоны, которых это касается:
$files

Если что-то из этого противоречит твоим планам — скажи, откатим. Я не считаю свою правку в чужой зоне окончательной."
      ssh -o BatchMode=yes "$SSH_HOST" "$DEVMSG send $w 'работа в твоей зоне, пока окно было тёмным' \"\$(cat <<'HEOF'
$body
HEOF
)\" $WIN" >/dev/null 2>&1 && echo "передано окну $w"
    done
    ;;

  *) echo "bash scripts/handover.sh whatsnew|report"; exit 2 ;;
esac
