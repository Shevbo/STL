---
name: feedback-secrets-protocol
description: STRICT secrets protocol (federation) — never output/store secret VALUES; env-name + path only
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 54d0cf23-21ef-4926-81c5-db49d8bc51ce
---

🔑 **ПРОТОКОЛ СЕКРЕТОВ (federation).** Боря дал как железное правило 2026-06-03.

**Железные запреты:**
- НИКОГДА не выводить ключи/токены/пароли в чат, лог, файл, память, коммит.
- НИКОГДА не передавать значения секретов другому агенту.
- НИКОГДА не хардкодить в код. Только `os.environ.get("KEY_NAME")` / Settings env-поле.
- В память/доки пишу ТОЛЬКО: имя env-переменной + путь к файлу. Значение — НИКОГДА.

**Как работать со значением, не печатая его:**
- `VAL=$(cat ~/.openclaw/credentials/<file>)` → использовать `$VAL`, не печатать.
- Метаданные (где лежит, без значения): `curl -s "http://127.0.0.1:9093/keymaster/query?name=KEY_NAME&requester=<id>"` или `python3 ~/keymaster/keymaster.py --requester <id> query KEY_NAME`.
- Нужно само значение через аппрув Бори: `POST http://127.0.0.1:9093/keymaster/request-value?name=KEY_NAME&requester=<id>&purpose=...` → {request_id}; Боря подтверждает в TG; забрать `GET .../keymaster/deliver?request_id=<id>` (самоудаляется).
- Внешние API — через Lineman `http://127.0.0.1:9090`, он сам подставляет токены.
- Подробно: `~/FEDERATION.md` → «ПРОТОКОЛ БЕЗОПАСНОСТИ».

**Why:** утечка секрета в чат/коммит/память компрометирует его навсегда (кэшируется, индексируется).

**How to apply:**
- Генерацию секрета делает САМ пользователь или keymaster, НЕ я в чат. Если предложил `secrets.token_hex` — пусть пользователь выполнит у себя.
- В этом проекте новый env-параметр: `OPT_AGENT_TOKEN` (Settings.opt_agent_token), читается из `~/.shectory_trade.env` на VDS и из env на Windows-хосте агента. Значение НЕ хранить здесь.
- ВАЖНО: keymaster (порт 9093) и FEDERATION.md живут на федеративном хосте smain (Linux), НЕ на Windows-машине трейдера (c:\Dev\...). На Windows эти эндпоинты недоступны — секреты туда заводит Боря вручную в env.

**Нарушение 2026-06-03 (запомнить, не повторять):** я сгенерировал OPT_AGENT_TOKEN и НАПЕЧАТАЛ его в чат. Тот токен скомпрометирован — его нельзя использовать. Правильно: попросить Борю сгенерировать/завести токен у себя, я работаю только с именем env-переменной.

Related: [[reference-vds-load]] (agent offload), [[feedback-rtk-usage]].
