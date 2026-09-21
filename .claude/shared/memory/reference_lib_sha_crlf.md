---
name: reference_lib_sha_crlf
description: lib_sha в i9_heartbeat считается от файла с LF; локальная Windows-копия с CRLF даёт ложное расхождение «код не доехал»
metadata:
  type: reference
---

21.09.2026. Проверяя, доехала ли новая ось до i9, сравнил `lib_sha` из `i9_heartbeat` (`agent_control`) с sha256 своего `trader/lab/strategies/library.py` — не сошлось, и это выглядело как «агент считает старым движком».

Причина не в коде: в рабочей копии на Windows файл лежит с CRLF (1880 переводов строки), на GitHub — с LF, а `_file_sha` в `scripts/opt_agent.py` берёт sha256 БАЙТОВ и первые 12 знаков. Отпечаток LF-версии совпал с i9 точно.

**Как сверять:** `hashlib.sha256(open(path,'rb').read().replace(b'\r\n', b'\n')).hexdigest()[:12]`. Само по себе совпадение отпечатка не доказывает, что ось работает — для этого есть [[reference_i9_axis_alive_probe]]: прогон с осью и без обязан разойтись по числу сделок или итогу.

Связано: [[feedback_backtests_only_i9]], [[reference_i9_uncommitted_code]].
