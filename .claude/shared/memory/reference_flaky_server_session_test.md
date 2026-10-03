---
name: reference-flaky-server-session-test
description: "tests/quik/test_server.py::test_session_pushes_set_limits_on_register зависает примерно 1 раз из 60 на Windows; не регрессия, проверено на старом сервере; полный прогон надо перезапускать"
metadata:
  type: reference
---

03.10.2026 полный прогон `tests/quik` иногда обрывался строкой «+++ Timeout +++» (pytest-timeout 30 с в
pytest.ini) без падения конкретного теста. Серия из 25 прогонов `test_server.py` поймала зависание на
втором тесте, `test_session_pushes_set_limits_on_register`: асинхронный gRPC-тест, стек стоит в
`asyncio` `GetQueuedCompletionStatus` (Windows, proactor). Проверка на коде сервера ДО моей правки
очереди (коммит 4e011af~1): 1 зависание из 60, то есть дефект самого теста или gRPC на Windows,
правкой очереди не вызван. Полный набор в среднем идёт 10 секунд; зависший прогон повторять, а не
искать регрессию. Как отличить: имя зависшего теста в `-v --timeout=15`, плюс повтор на чистом HEAD.
Не лечил: тест не мой, а причина (гонка закрытия клиентского генератора и чтения сервера) не найдена.
