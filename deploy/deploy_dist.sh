#!/usr/bin/env bash
# SAFE frontend deploy: scp the LOCALLY built dist to the hoster and VERIFY.
# Born 2026-07-24 after the same miss twice in two days: index.html + JS were
# scp'd but the new hashed CSS was forgotten — the site opened unstyled and
# read as "down". This script parses index.html's OWN asset references, ships
# exactly those (plus index.html and the public *.html pages), then re-reads
# the references THROUGH nginx and fails loudly on any 404.
# Usage: bash deploy/deploy_dist.sh   (run from the repo root, after vite build)
set -euo pipefail
cd "$(dirname "$0")/.."
DIST=frontend/dist
HOST=hoster
RDIR="~/apps/shectory-trader/frontend/dist"

# ── Замок 2 (регламент трёх окон, 08.08.2026): на прод — только то, что в git ──
# vite build запекает в dist ЛЮБОЕ состояние frontend/, включая чужие
# незакоммиченные правки (08.08 деплой увёз чужой m.html). Грязно — не деплоим.
DIRTY="$(git status --porcelain -- frontend/ || true)"
if [ -n "$DIRTY" ]; then
  echo "СТОП: под frontend/ незакоммиченные правки — деплой увёз бы на прод то, чего нет в git:" >&2
  echo "$DIRTY" | sed 's/^/  /' >&2
  echo "Закоммить (своё) или передать владельцу зоны (чужое), пересобрать и повторить." >&2
  exit 1
fi

[ -f "$DIST/index.html" ] || { echo "нет $DIST/index.html — сначала vite build"; exit 1; }

ASSETS=$(grep -o 'assets/[^"]*' "$DIST/index.html" | sort -u)
echo "ассеты из index.html:"; echo "$ASSETS" | sed 's/^/  /'

scp "$DIST"/*.html "$HOST:$RDIR/"
for a in $ASSETS; do
  scp "$DIST/$a" "$HOST:$RDIR/assets/"
done

echo "--- проверка через nginx ---"
FAIL=0
for a in $ASSETS; do
  CODE=$(ssh "$HOST" "curl -sk -o /dev/null -w '%{http_code}' -H 'Host: stl.shectory.ru' https://localhost/$a")
  echo "  $CODE $a"
  [ "$CODE" = "200" ] || FAIL=1
done
[ "$FAIL" = "0" ] && echo "OK: деплой целостный" || { echo "FAIL: битые ассеты!"; exit 1; }
