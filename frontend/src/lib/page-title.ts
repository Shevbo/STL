// Заголовок закладки браузера. У всех экранов SPA один URL и был один титул
// «Shectory Trader»: с десятком открытых закладок понять, где что, невозможно
// (заказ оператора 30.07.2026). Титул собираем из того, ЧТО открыто.
//
// Формат: «<что открыто> · STL». Сначала конкретика, потому что в узкой закладке
// видны только первые символы: «agent-ob-BRU6-v1 · STL» читается, а
// «STL · agent-ob-…» — нет.

export const TITLE_SUFFIX = 'STL';

/** Чистая сборка титула — то, что проверяется тестом. */
export function buildTitle(what?: string | null): string {
  const s = (what ?? '').trim();
  return s ? `${s} · ${TITLE_SUFFIX}` : TITLE_SUFFIX;
}

/** Записать титул документа (в не-браузерной среде — молча ничего). */
export function setTitle(what?: string | null): void {
  if (typeof document === 'undefined') return;
  document.title = buildTitle(what);
}

/** Человеческие имена вкладок Лаборатории для титула (ключ = ?lab=<tab>). */
export const LAB_TAB_TITLES: Record<string, string> = {
  live: 'Live Robots',
  market: 'Market Browser',
  backtest: 'Backtest Lab',
  botstore: 'Botstore',
};

/**
 * Титул по параметрам URL. Порядок совпадает с порядком выбора экрана в
 * App.svelte: страница стратегии, стенд робота, затем оверлеи-фреймы поверх
 * терминала. Иначе титул рассказывал бы не про тот экран, который видно.
 */
export function titleFromQuery(qs: URLSearchParams): string {
  if (qs.get('strategy')) return `Стратегия ${qs.get('strategy')}`;
  if (qs.get('agent_robot')) return `Робот ${qs.get('agent_robot')}`;
  if (qs.get('campaign')) return `Перебор ${qs.get('campaign')}`;
  if (qs.has('lab')) return `Лаборатория · ${LAB_TAB_TITLES[qs.get('lab') || ''] || 'Live Robots'}`;
  if (qs.has('orders')) return 'Заявки';
  if (qs.has('tables')) return 'Таблицы QUIK';
  if (qs.has('equity')) return 'Доходность роботов';
  return 'Терминал';
}
