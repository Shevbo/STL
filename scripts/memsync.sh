#!/usr/bin/env bash
# ЕДИНАЯ ПАМЯТЬ И ПРАВИЛА ДЛЯ ВСЕХ ОКОН, включая подхват на smain.
#
#   bash scripts/memsync.sh pull    репозиторий -> живые места (в начале работы)
#   bash scripts/memsync.sh push    живые места -> репозиторий (+ коммит)
#
# ЗАЧЕМ. CLAUDE.md, .claude/ и docs/ едут с git и общие сами собой. А три вещи
# жили ТОЛЬКО на машине окон и пропали бы вместе со светом:
#   ~/.claude/projects/<ключ>/memory/   авто-память (79 файлов, чем закончились
#                                        живые инциденты — этого нет больше нигде)
#   ~/.claude/CLAUDE.md, RTK.md          глобальные правила пользователя
#   .remember/*.md                       история сессий (в git не была вовсе)
#
# ПОЧЕМУ КОПИРОВАНИЕ, А НЕ СИМЛИНКИ. Симлинк на Windows требует прав или режима
# разработчика, junction ведёт себя иначе — установка молча разъехалась бы между
# машинами. Копирование работает везде одинаково, а конфликтов не бывает по
# устройству: окна на Windows-VM и подхват на smain НИКОГДА не работают
# одновременно — подхват для того и заведён, что там нет света.
#
# ЛОГИ И tmp НЕ ПЕРЕНОСИМ: 1.9 МБ сырых логов на каждую синхронизацию, а пользы
# от них на другой машине нет. Переносим только markdown-историю.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SHARED="$REPO/.claude/shared"
CLAUDE_HOME="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"

# Каталог авто-памяти именуется по ПУТИ репозитория: Claude Code заменяет любой
# не буквенно-цифровой символ на дефис. «c:\Dev\Shectory Trade & Lab» даёт
# «c--Dev-Shectory-Trade---Lab», «/home/shectory/stl» даёт «-home-shectory-stl».
#
# ВЫЧИСЛЯЕМ, А НЕ ИЩЕМ. Первая версия искала любой каталог с MEMORY.md и брала
# самый свежий — на smain это оказалась память ЧУЖОГО агента (lineman, Клод), и
# синхронизация свалила туда 79 файлов STL поверх его индекса. Чужая память —
# не наше место: каталог только вычисляем из своего пути, и если его нет, сами
# же и создаём. Переопределяется STL_MEMORY_DIR для нестандартных установок.
memory_dir() {
  if [ -n "${STL_MEMORY_DIR:-}" ]; then echo "$STL_MEMORY_DIR"; return; fi
  local path="$REPO" key
  # Git Bash отдаёт путь как /c/Dev/..., а Claude на Windows считает ключ от
  # РОДНОГО пути c:\Dev\... — ключи расходятся, и скрипт честно не находил
  # каталог (поймало окно ui-ux). Возвращаем windows-форму, если она доступна.
  if command -v cygpath >/dev/null 2>&1; then
    path="$(cygpath -w "$REPO" 2>/dev/null || echo "$REPO")"
    # Claude пишет букву диска в НИЖНЕМ регистре: «c:\Dev\...» -> «c--Dev-...»
    path="$(printf '%s' "$path" | awk '{print tolower(substr($0,1,1)) substr($0,2)}')"
  fi
  key="$(printf '%s' "$path" | sed 's/[^A-Za-z0-9]/-/g')"
  echo "$CLAUDE_HOME/projects/$key/memory"
}

MEM="$(memory_dir)"

copy_tree() {  # src dst  — только markdown, без логов и временного
  local src="$1" dst="$2"
  [ -d "$src" ] || return 0
  mkdir -p "$dst"
  (cd "$src" && find . -maxdepth 1 -name '*.md' -print0 2>/dev/null) |
    while IFS= read -r -d '' f; do cp -p "$src/$f" "$dst/$f" 2>/dev/null; done
}

case "${1:-}" in
  pull)
    mkdir -p "$MEM"
    copy_tree "$SHARED/memory"   "$MEM"
    copy_tree "$SHARED/remember" "$REPO/.remember"
    for g in CLAUDE.md RTK.md; do
      [ -f "$SHARED/global/$g" ] && { mkdir -p "$CLAUDE_HOME"; cp -p "$SHARED/global/$g" "$CLAUDE_HOME/$g"; }
    done
    echo "память подтянута в $MEM"
    ;;
  push)
    [ -d "$MEM" ] || { echo "нет каталога памяти $MEM — нечего выгружать"; exit 1; }
    copy_tree "$MEM"             "$SHARED/memory"
    copy_tree "$REPO/.remember"  "$SHARED/remember"
    mkdir -p "$SHARED/global"
    for g in CLAUDE.md RTK.md; do
      [ -f "$CLAUDE_HOME/$g" ] && cp -p "$CLAUDE_HOME/$g" "$SHARED/global/$g"
    done
    cd "$REPO"
    git add -A .claude/shared >/dev/null 2>&1
    if git diff --cached --quiet -- .claude/shared; then
      echo "память не менялась"
    else
      git commit -q -m "chore(memory): синхронизация памяти и правил между окнами" \
        -m "Единая память для машины окон и подхвата на smain: авто-память, глобальные правила, история .remember." \
        && echo "память закоммичена (запушить: git push)"
    fi
    ;;
  *)
    echo "bash scripts/memsync.sh pull|push"; exit 2 ;;
esac
