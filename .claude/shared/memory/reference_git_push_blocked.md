---
name: reference-git-push-blocked
description: "Это окно не может push'ить в origin: GITHUB_TOKEN_STL зарегистрирован в keymaster, но по указанному адресу его нет"
metadata: 
  node_type: memory
  type: reference
  originSessionId: 7a0b0f32-4822-4626-a7a5-cf6913d892b4
  modified: 2026-08-13T17:03:05.091Z
---

Push в `origin` (https://github.com/Shevbo/STL.git) из этого окна НЕ РАБОТАЕТ.
Проверено 13.08.2026: `fatal: could not read Username for 'https://github.com'`.

- Локально нет `credential.helper`, нет `~/.git-credentials`, `gh` не залогинен ни здесь,
  ни на `hoster` (там `gh` вообще не установлен), ни на `smain`.
- Keymaster знает секрет `GITHUB_TOKEN_STL` и указывает location
  `~/.shectory_trade.env → GITHUB_TOKEN_STL` на хостере. **Переменной там НЕТ**
  (`grep -c GITHUB ~/.shectory_trade.env` = 0). Запись в keymaster устарела.
- `git fetch origin` при этом работает анонимно — «в синхроне» в git status
  ничего не доказывает про право на push.

Следствие: коммит остаётся локальным, а `origin/main` двигают другие окна.
Публикацию агента это НЕ блокирует — релиз собирается из `~/quik_build` (scp-дерево,
не git). Но правило «прод несёт ровно то, что несёт git» при этом временно нарушено,
поэтому о заблокированном push надо СКАЗАТЬ оператору, а не молчать.

Чинится только оператором: положить действующий PAT в `~/.shectory_trade.env` на
хостере (и поправить location в keymaster) либо выдать креды окну напрямую.
См. [[reference-federation-access]], [[feedback-secrets-protocol]].
