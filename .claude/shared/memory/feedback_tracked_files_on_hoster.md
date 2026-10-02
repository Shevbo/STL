---
name: feedback-tracked-files-on-hoster
description: STRICT — на хостере НЕ убирать в резерв файлы, мешающие git pull, без git ls-files; отслеживаемый файл трогать нельзя
metadata:
  type: feedback
---

Если `git pull` на хостере блокируют чужие файлы — СНАЧАЛА `git ls-files <путь>`. Отслеживаемый файл убирать в резерв нельзя вовсе: pull разрулит сам.

**Why:** 22.09.2026 я убрал 15 файлов окна backtests в резерв, «сверив sha256 с git». Совпадение ничего не доказывало — это и были версии из git, приехавшие их же pull'ом. Дерево осталось с 15 удалёнными файлами, и кампания backtests на i9 упала с `No such file or directory`. Восстанавливали они.

**How to apply:** `git ls-files` по списку → отслеживаемые не трогать → только по-настоящему неотслеживаемые (и то: `docs/sweep-timings.md` дописывается скриптом на машине прогона, по нему слать diff владельцу, а не удалять). После любой такой уборки проверять `git status --porcelain | grep "^ D"` — должно быть пусто.

Связано: [[reference_ssh_hoster]], [[project_dev_msg_middleware]].
