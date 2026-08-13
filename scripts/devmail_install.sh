#!/usr/bin/env bash
# Установка почты окна НА МАШИНЕ ОКОН. Одна команда на окно.
#
#   STL_WINDOW=real-trade bash scripts/devmail_install.sh
#
# Запускать В САМОМ ОКНЕ Claude: машина окон — Windows-VM рядом с vibe, снаружи
# в неё никто не ходит, поэтому ставит службу тот, кто там и живёт.
#
# ПОЧЕМУ РАСПИСАНИЕ, А НЕ ДЕМОН. Первая версия поднимала фоновый цикл через
# nohup и стерегла его pid-файлом. На Windows это ломается (nohup, pgrep и
# сигналы там не те), а на Linux демон всё равно нужно было воскрешать после
# перезагрузки. Планировщик делает и то и другое сам: раз в минуту дёргаем
# --once, живучесть и автозапуск получаем даром, стеречь нечего.
#
# Идемпотентна: повторный запуск переписывает задание, а не плодит второе.
set -euo pipefail

WIN="${STL_WINDOW:-}"
[ -n "$WIN" ] || { echo "Задай окно: STL_WINDOW=real-trade bash $0"; exit 2; }
case "$WIN" in real-trade|backtests|ui-ux|stl-dev-smain) ;; *) echo "Неизвестное окно: $WIN"; exit 2;; esac

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIR="${STL_DEVMAIL_ROOT:-$HOME/.stl-devmail}"
DRY="${DEVMAIL_DRY_RUN:-0}"
mkdir -p "$DIR"

# Интерпретатор ищем, а не угадываем: на Windows python3 бывает шимом, бывает
# только python, а молчаливая установка на несуществующий бинарь дала бы
# «служба стоит» без единой ошибки.
PY=""
for c in python3 python py; do
  command -v "$c" >/dev/null 2>&1 && "$c" -c "import sys" >/dev/null 2>&1 && { PY="$c"; break; }
done
[ -n "$PY" ] || { echo "Не нашёл python (пробовал python3, python, py). Поставь его и повтори."; exit 1; }
command -v ssh >/dev/null 2>&1 || { echo "Не нашёл ssh — фон не сможет забрать почту."; exit 1; }

case "$(uname -s 2>/dev/null || echo unknown)" in
  MINGW*|MSYS*|CYGWIN*|Windows_NT) OS=windows ;;
  *) OS=unix ;;
esac
echo "машина: $OS · python: $PY · окно: $WIN"

run() { if [ "$DRY" = 1 ]; then echo "[dry-run] $*"; else "$@"; fi; }

if [ "$OS" = windows ]; then
  # Пути планировщику нужны абсолютные и в windows-форме. Интерпретатор тоже:
  # у задания свой PATH, и «python3» там может не разрешиться вовсе — задание
  # молча падало бы каждую минуту, а окно считало бы, что почта настроена.
  # pythonw.exe, а НЕ python.exe: консольный интерпретатор поднимает видимое
  # окно на КАЖДЫЙ прогон, то есть раз в минуту весь день. Оператор это заметил
  # в первый же час. pythonw делает то же самое без окна; если его нет рядом с
  # python.exe — откатываемся на консольный, лучше мигающее окно, чем нерабочая
  # почта.
  PYEXE="$("$PY" -c "import sys; print(sys.executable)")"
  PYW="${PYEXE%python.exe}pythonw.exe"
  [ -x "$PYW" ] && PYEXE="$PYW"
  WPY="$(cygpath -w "$PYEXE" 2>/dev/null || echo "$PYEXE")"
  WPATH="$(cygpath -w "$REPO/scripts/devmail_sync.py" 2>/dev/null || echo "$REPO/scripts/devmail_sync.py")"
  TASK="STL-DevMail-$WIN"
  run schtasks //Create //TN "$TASK" //F //SC MINUTE //MO 1 \
      //TR "\"$WPY\" \"$WPATH\" --window $WIN --once"
  run schtasks //Run //TN "$TASK" >/dev/null 2>&1 || true
  echo "задание планировщика: $TASK (раз в минуту)"
  echo "снять: schtasks //Delete //TN $TASK //F"
else
  LINE="* * * * * cd $REPO && $PY scripts/devmail_sync.py --window $WIN --once >> $DIR/sync-$WIN.log 2>&1"
  if crontab -l 2>/dev/null | grep -Fq "devmail_sync.py --window $WIN"; then
    echo "крон уже прописан"
  else
    # crontab -l на машине без кронтаба выходит с кодом 1, и под set -e это
    # убивало подоболочку до echo — строка терялась молча. Отсюда || true.
    run bash -c "{ crontab -l 2>/dev/null || true; echo \"$LINE\"; } | crontab -"
    echo "крон прописан: раз в минуту"
  fi
fi

if [ "$DRY" = 1 ]; then echo "[dry-run] выход без проверки слепка"; exit 0; fi

# Первый слепок берём сами и ждём его: установка не имеет права отрапортовать
# успех, если почту забрать не удалось. Иначе окно на первом же вводе получит
# «почта не синхронизируется» и пойдёт чинить то, что «установлено».
"$PY" "$REPO/scripts/devmail_sync.py" --window "$WIN" --once || true
n=0
while [ ! -s "$DIR/$WIN.json" ] && [ $n -lt 15 ]; do sleep 1; n=$((n+1)); done
if [ ! -s "$DIR/$WIN.json" ]; then
  echo "СЛЕПОК НЕ ПОЯВИЛСЯ. Почту забрать не вышло. Проверь руками:"
  echo "  ssh ${STL_DEVMAIL_SSH:-hoster} 'bash ~/apps/shectory-trader/scripts/devmsg.sh inbox-json $WIN'"
  tail -5 "$DIR/sync-$WIN.log" 2>/dev/null || true
  exit 1
fi

echo "--- что окно увидит сейчас:"
STL_WINDOW="$WIN" "$PY" "$REPO/scripts/devmail_hook.py" <<< '{"hook_event_name":"SessionStart"}' || true
echo
echo "Готово. Окно «$WIN» видит почту на каждом твоём вводе. Запускайся с STL_WINDOW=$WIN."
