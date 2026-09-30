---
name: reference-i9-broken-pool
description: "i9: задание висит running при heartbeat idle = BrokenProcessPool, все следующие задания падают мгновенно; лечится переключением i9_workers в agent_control (пересборка пула), не рестартом; очередь однопоточная, снятие в БД счёт не останавливает"
metadata:
  node_type: memory
  type: reference
  originSessionId: ea265f2b-1cf2-4121-a2e6-fc0a097704c1
  modified: 2026-09-30T05:52:28.759Z
---

30.09.2026 (партия fp3): `fp3-A4-oos` 48 мин в `running`, heartbeat показывал `activity=idle`;
воркер-пул i9 сломался (`BrokenProcessPool`), 8 следующих заданий упали за секунды с той же
ошибкой. Починка без рестарта сервиса: сменить `i9_workers` в `agent_control` (14→13→14),
`opt_agent.py` пересобирает пул штатно; после этого перепоставить упавшие задания.

Свойства очереди, которые стоили ночи: строго последовательная (одно задание за раз, воркеры
только внутри задания); `status=failed` в БД НЕ останавливает счёт на i9 (старый процесс
досчитает и перепишет статус на done); чистый Python без numpy: C1 на 390k снимков с
draws=200 шёл 148 мин, D1 (DFT) 114 мин. Ставить стакан-модули с draws=50, max_rows=100k,
по одной единице на задание; бамп update_token только на пустой очереди.

**How to apply:** «running» дольше ожидаемого + heartbeat idle = сломанный пул, не ждать; при
постановке тяжёлых заданий сначала дымовая единица; оператору предложить numpy на i9.
См. [[reference-i9-empty-result]], [[project-algo-footprints]].
