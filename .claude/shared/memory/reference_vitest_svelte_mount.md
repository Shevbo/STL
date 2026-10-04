---
name: reference-vitest-svelte-mount
description: "монтировать Svelte-компонент в vitest: нужен resolve.conditions browser и заглушка ResizeObserver; иначе страница молча висит на «Загрузка…»"
metadata:
  node_type: memory
  type: reference
---

04.10.2026, тест страницы витрины кампаний. До этого в `frontend/src` не было ни одного теста, который МОНТИРУЕТ компонент (все проверяли чистые функции или исходный файл regexp-ом), и два подводных камня нашлись подряд.

**Первый: `lifecycle_function_unavailable`.** Vitest резолвит `svelte` в СЕРВЕРНУЮ сборку, где `onMount` и `$effect` недоступны. Лечение — в `vite.config.ts` `resolve.conditions: mode === 'test' ? ['browser'] : undefined`. Условие только для теста, боевая сборка не меняется; полный прогон 831 теста после правки остался зелёным.

**Второй, коварнее: jsdom не знает `ResizeObserver`.** Компонент с `bind:clientWidth` на графике падает внутри эффекта, а Svelte эту ошибку ГЛОТАЕТ: ни `window.onerror`, ни `flushSync` ничего не бросают. Страница в тесте просто остаётся на «Загрузка…», тогда как карточки без графика рисуются нормально — по этой асимметрии («с кривой не рисуется, без кривой рисуется») и нашлась причина. Лечение — заглушка `globalThis.ResizeObserver ??= class { observe() {} unobserve() {} disconnect() {} }` в самом тесте.

**Третье: ответ подставного API нужно ждать макрозадачами.** `Response.json()` разрешается не микротасками, поэтому `await Promise.resolve()` в цикле не хватает; рабочий вариант — цикл `await new Promise(r => setTimeout(r, 0)); flushSync()` на 12 итераций. Подставной `fetchWithAuth` задаётся через `vi.mock('$lib/fetch-auth', …)`, алиас `$lib` в `vi.mock` работает.

**Как диагностировать «страница застряла в тесте»:** временный `zz_debug.test.ts`, который монтирует компонент с минимальными данными и печатает `host.textContent`; сужать, меняя ОДНО поле карточки (у меня это был `thumb: null` против массива). Файл потом удалить.

Образец — `frontend/src/components/lab/CampaignShowcase.test.ts`. Связано: [[project_campaign_showcase]].
