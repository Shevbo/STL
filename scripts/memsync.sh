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

# Каталог авто-памяти именуется по ПУТИ репозитория, а на smain путь другой —
# угадывать алгоритм нельзя. Ищем каталог, в котором лежит MEMORY.md; если их
# несколько, берём самый свежий. Переопределяется STL_MEMORY_DIR.
find_memory_dir() {
  if [ -n "${STL_MEMORY_DIR:-}" ]; then echo "$STL_MEMORY_DIR"; return; fi
  local best="" newest=0 f d
  for f in "$CLAUDE_HOME"/projects/*/memory/MEMORY.md; do
    [ -f "$f" ] || continue
    d="$(dirname "$f")"
    local t; t=$(date -r "$f" +%s 2>/dev/null || echo 0)
    [ "$t" -ge "$newest" ] && { newest=$t; best="$d"; }
  done
  echo "$best"
}

MEM="$(find_memory_dir)"

copy_tree() {  # src dst  — только markdown, без логов и временного
  local src="$1" dst="$2"
  [ -d "$src" ] || return 0
  mkdir -p "$dst"
  (cd "$src" && find . -maxdepth 1 -name '*.md' -print0 2>/dev/null) |
    while IFS= read -r -d '' f; do cp -p "$src/$f" "$dst/$f" 2>/dev/null; done
}

case "${1:-}" in
  pull)
    [ -n "$MEM" ] || MEM="$CLAUDE_HOME/projects/stl/memory"
    copy_tree "$SHARED/memory"   "$MEM"
    copy_tree "$SHARED/remember" "$REPO/.remember"
    for g in CLAUDE.md RTK.md; do
      [ -f "$SHARED/global/$g" ] && { mkdir -p "$CLAUDE_HOME"; cp -p "$SHARED/global/$g" "$CLAUDE_HOME/$g"; }
    done
    echo "память подтянута в $MEM"
    ;;
  push)
    [ -n "$MEM" ] || { echo "не нашёл каталог памяти; задай STL_MEMORY_DIR"; exit 1; }
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
