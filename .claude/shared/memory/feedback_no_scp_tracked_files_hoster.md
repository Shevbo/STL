---
name: feedback-no-scp-tracked-files-hoster
description: "STRICT: отслеживаемые git-файлы в рабочую копию хостера через scp не носить (только push и pull, временное в /tmp); 01.10.2026 scp двух скриптов заблокировал git pull real-trade, хостер застрял на 11e02bc"
metadata:
  node_type: memory
  type: feedback
  originSessionId: ea265f2b-1cf2-4121-a2e6-fc0a097704c1
  modified: 2026-10-01T02:38:19.802Z
---

01.10.2026 мои субагенты, чтобы быстрее прогнать подготовку данных, скопировали через scp
`scripts/book_full_digest.py` и `scripts/exec_anchors.py` в `~/apps/shectory-trader` на хостере
ДО коммита. Рабочая копия разошлась с HEAD (плюс CRLF от Windows), `git pull --ff-only` у окна
real-trade перестал проходить, хостер остался на 11e02bc и их фикс не доезжал; real-trade
написал письмом. Снято позже через `git stash` → pull → `stash drop`, хвосты в корне
(`lxk22_sets.json`, `macdshort_sets.json`) удалены.

**Why:** рабочая копия хостера общая для трёх окон; незакоммиченная правка отслеживаемого файла
это блокировка чужой выкладки, тот же класс, что «не запушено = не существует».

**How to apply:** в задания субагентам писать явно: на хостер код попадает только через
`git push` + `git pull --ff-only`; если нужно прогнать незакоммиченный скрипт, класть его в
`/tmp/<имя>` и запускать оттуда с `PYTHONPATH=~/apps/shectory-trader`; после работы проверять
`git status --short` на хостере (без `??` своих файлов в корне). `M AGENTS.md` на хостере это
блок fedrag от Клода федерации (21.09), не наш, не трогать. См. [[reference-i9-uncommitted-code]].
