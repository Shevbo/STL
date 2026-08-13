---
name: stl-docs-release
description: Документация STL на /docs.html + сквозное датовое версионирование релизов; FEDBACKUP-протокол
metadata: 
  node_type: memory
  type: project
  originSessionId: 7bc0d4ac-8550-437c-aafb-b106efc6e49b
  modified: 2026-07-25T07:15:09.548Z
---

Документация платформы живёт в `frontend/public/docs.html` (интерактивный лонгрид,
5 разделов, каталог 22 стратегий), в UI — бургер-меню «Справка». Деплой = обычный
фронтовый (scp dist, без рестарта).

Версионирование СКВОЗНОЕ и датовое: релиз `STL 2026.07.25` общий для платформы и
доки (`v2026.07.25-1`); сателлиты несут свои build_rev (агент/раннер 1784961467,
компаньон sha256 3463dd77…) и привязаны таблицей версий в шапке доки. При заметном
релизе — обновить блок «Версии» в docs.html.

**How to apply:** любое изменение архитектуры/артефактов СООБЩАТЬ агенту FEDBACKUP
(`POST http://10.66.0.1:9090/api/agent/fed-backup/message?from=klod-stl`, body
`news event=backup-arch-change|backup-gap|backup-new-resource ...`) — «что не
сообщено FEDBACKUP — не бэкапится». Инвентарь артефактов STL:
`smain:~/docs/STL_BACKUP_ARTIFACTS.md` (поддерживать при изменениях). Известные
gaps отправлены 2026-07-25 (id 7,8): Postgres хостера и ~/apps вне стандартного
охвата ~/workspaces.
