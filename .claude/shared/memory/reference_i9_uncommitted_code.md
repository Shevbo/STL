---
name: i9-uncommitted-code-trap
description: Код только на i9 (без git) = невоспроизводимые числа; __inv-урок + штатный снятие-пауз путь
metadata: 
  node_type: memory
  type: reference
  originSessionId: 7bc0d4ac-8550-437c-aafb-b106efc6e49b
  modified: 2026-07-28T07:18:13.961Z
---

**Самообновление i9 РАБОТАЕТ (проверено 28.07.2026).** Порядок доставки файла из
манифеста (`agent/update_manifest.txt`): коммит+пуш в main -> `INSERT INTO
agent_control(key,value) VALUES('update_token', now()::text) ON CONFLICT ...` ->
агент подхватывает за ~30-60 с. Чтобы доставку было ЧЕМ проверить, бампай
`AGENT_VERSION` в `scripts/opt_agent.py` и следи за полем `i9.version` в
`GET /api/v1/agent/activity`.

Логика инверсии `__inv` жила ТОЛЬКО на i9 как ручная правка без коммита; очередной
ресинк по манифесту её затёр. Итог: контр-лидеры в leaderboard до 2026-07-25
посчитаны утерянной сборкой и НЕ воспроизводимы (пример: keltner_bo__inv таблица
278 598 ₽ vs честный пересчёт 176 665 ₽). Доверять только `camp-20260725-contrredo`
и позже.

**Why:** i9 — рукосинхронная копия репо; всё, что он исполняет, обязано быть в git
и в `agent/update_manifest.txt`. Триггер самообновления: bump `update_token` в
`agent_control` (SQL в докстроке `/api/v1/agent/control`).

**How to apply:**
- Никогда не оставлять стратегическую логику только на i9.
- Снятие авто-пауз реальных роботов — ШТАТНЫМ `hoster:~/stl-morning-resume.sh`
  (гейт линк+раннер+понг, снимает только из `~/.stl-autopaused`); прямой POST
  pause/start-agent режется классификатором.
- Вотчер-probe (`hoster:~/stl-watchdog-probe.sh`) — вне репо; правки session-гейта
  в репозитории до него сами не доедут.
Related: [[stl-docs-release]], [[forts-weekend-trading]].
