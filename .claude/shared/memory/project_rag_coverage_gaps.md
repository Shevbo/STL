---
name: project-rag-coverage-gaps
description: "04.10.2026 аудит RAG: окна и канон STL в индексе, но не lua, proto, deploy, frontend/public; SPARE на smain не видит RAG и не пишет память; заявки ушли rag-shectory и Клоду; проверить после реиндекса"
metadata:
  type: project
---

04.10.2026 оператор спросил, переложен ли в RAG весь канон STL с опытом четырёх окон, включая SPARE на smain.
Аудит на сервере RAG (sdev, `~/ragkit`): память Windows-окон синхронизируется живьём (real-trade 233 файла,
backtests 152, ui-ux 112, плюс папка backtest-localLLM 219), репозиторий подтягивается каждые 15 минут,
общая память окон `.claude/shared` (350 файлов), канон docs (65), исходники py/go/ts/svelte, тесты, scripts
(159). Пробелы: (1) профиль `stl` не берёт расширения `lua` и `proto` и пути `proto`, `deploy`,
`frontend/public`, то есть не индексируются QLua-скрипт терминала (ловушка 32-битного `%d`), wire-контракты
агента, скрипты публикации и страницы companion.html, m.html, docs.html; (2) SPARE (`-home-shectory-stl` на
smain, 87 файлов памяти) последний раз писал память 01.10 05:45, а сессия у него была 04.10 23:11; на smain у
него нет ни MCP `rag`, ни Stop-хука записи в память, а стенограммы сессий в корпус не идут по конфигу
(`session-corpus` исключён); (3) ссылка в CLAUDE.md на `.onboarding/CANONICAL.md` была устаревшей: скилл
`/onboarding` канон больше не копирует, а индексирует, старая копия в корне проекта отставала на 60 суток.

Сделано: `/onboarding` прогнан в корне `C:\Dev\Shectory Trade & Lab` (карточка klod-stl обновлена, старый
CANONICAL.md удалён, маркер в CLAUDE.md репозитория переписан, коммит); bootstrap ПЕРЕТЁР вручную
написанные разделы карточки (точки входа, документы), я их восстановил по памяти разговора, бэкапа не было.
Заявка на правку `~/ragkit/configs/stl.toml` (include_ext += lua, proto, sh, cmd, ps1, html; paths +=
proto, deploy, frontend/public; exclude += trader/proto/**, **/dist/**) ушла агенту `rag-shectory` через
Lineman и Клоду (klod-access id 55146) с условием «нет ответа 2 часа, применяет Клод». Подключить SPARE к
RAG и хуку записи памяти поручено Клоду. **Проверка после реиндекса** (мой пункт): rag_search по
'QLua string.format %d усекает epoch-ms', 'Session AgentMessage OrchestratorMessage proto',
'publish_quik_agent.sh --runner-sha', 'companion.html' должен давать stl:quik_agent/lua, stl:proto,
stl:deploy, stl:frontend/public. Урок: перед скриптом, который переписывает файл, сначала снять копию.
Связано: [[reference-federation-access]].
