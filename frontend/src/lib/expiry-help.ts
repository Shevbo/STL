// Экспирация: перекладка роботов и ручной позиции на следующий контракт.
// Чистая часть экрана (docs/design/expiry-roll.md, раздел 10) — здесь, а не в
// разметке: состояния роботов и правило старта решают, что оператор нажмёт на
// живом счёте в день экспирации, и проверяться они обязаны тестом, а не глазами.

export type RobotState =
  | 'selected' | 'exit_wait' | 'ready' | 'switching' | 'switched'
  | 'refused' | 'reset_needed' | 'needs_operator' | 'skipped';

/** Подпись, цвет и смысл состояния робота в кампании. */
export const ROBOT_STATE: Record<RobotState, { label: string; tone: 'wait' | 'ok' | 'bad' | 'off'; what: string }> = {
  selected:      { label: 'выбран',        tone: 'off',  what: 'команд ещё не было' },
  exit_wait:     { label: 'ждёт выхода',   tone: 'wait', what: 'стоит «только на выход»: позиция или заявки ещё есть' },
  ready:         { label: 'готов',         tone: 'ok',   what: 'позиция 0, рабочих заявок нет' },
  switching:     { label: 'переключается', tone: 'wait', what: 'деплой на новый контракт отправлен, ждём зеркало' },
  switched:      { label: 'переключён',    tone: 'ok',   what: 'зеркало показывает новый контракт' },
  refused:       { label: 'отклонён',      tone: 'bad',  what: 'раннер отказал или зеркало не подтвердило: нужен оператор' },
  reset_needed:  { label: 'сброс позиции', tone: 'bad',  what: 'бумажный робот на истёкшем контракте: закрывать нечем' },
  needs_operator:{ label: 'нужен оператор', tone: 'bad', what: 'таймаут выхода или позиция на истёкшем контракте' },
  skipped:       { label: 'пропущен',      tone: 'off',  what: 'исключён оператором' },
};

export function stateLabel(s: string): string {
  return ROBOT_STATE[s as RobotState]?.label ?? s;
}

export function stateTone(s: string): string {
  return ROBOT_STATE[s as RobotState]?.tone ?? 'off';
}

export interface Check { key: string; ok: boolean; text: string; blocking?: boolean }

/** Можно ли жать «Старт».
 *
 *  Правило ТЗ: только когда ВСЕ блокирующие проверки зелёные. Проверка без пометки
 *  (Р) — предупреждение, она старт не держит. Пустой чекап стартовать НЕ даёт: это
 *  «не проверяли», а не «всё хорошо». */
export function canStart(checks: Check[] | null | undefined): boolean {
  if (!checks || !checks.length) return false;
  return checks.every((c) => c.ok || c.blocking === false);
}

/** Сколько осталось до дедлайна выхода (T-60). null = срок не задан. */
export function leftToDeadline(deadlineMs: number | null | undefined, now = Date.now()): string | null {
  if (!deadlineMs) return null;
  const min = Math.round((deadlineMs - now) / 60000);
  if (min <= 0) return 'срок вышел';
  const h = Math.floor(min / 60), m = min % 60;
  return h ? `${h} ч ${m} мин` : `${m} мин`;
}

/** Сколько робот сидит в текущем состоянии. state_since приходит в unix ms
 *  (уточнение real-trade 16.09), и печатать его сырым числом на экране нельзя:
 *  «1789567582955» оператору не говорит ничего, а «12 мин» говорит всё. */
export function timeInState(sinceMs: number | null | undefined, now = Date.now()): string {
  if (!sinceMs) return '';
  const min = Math.floor((now - sinceMs) / 60000);
  if (min < 1) return 'только что';
  if (min < 60) return `${min} мин`;
  const h = Math.floor(min / 60);
  return h < 24 ? `${h} ч ${min % 60} мин` : `${Math.floor(h / 24)} сут`;
}

/** Имя робота для экрана: бэкенд кампании зовёт его display_name, зеркало агента —
 *  name. Показываем то, что пришло, и НИКОГДА не пустую ячейку: без имени оператор
 *  не поймёт, какой робот застрял. */
export function robotName(r: { display_name?: string; name?: string; robot_id?: string }): string {
  return r?.display_name || r?.name || r?.robot_id || '—';
}

/** Какую кампанию показать.
 *
 *  Активных бывает НЕСКОЛЬКО сразу: 17.09 у RI, Si и GZ экспирация в один день, и
 *  это три разные кампании по своим сериям (real-trade 16.09). Правила выбора:
 *  выбранная оператором, пока она в списке; иначе первая из active_ids (она же
 *  самая свежая); иначе первая в списке — чтобы после завершения всех кампаний
 *  экран показывал историю, а не пустоту.
 */
export function pickCampaign<T extends { id: string; active?: boolean }>(
  list: T[] | null | undefined, activeIds: string[] | null | undefined, selectedId?: string | null,
): T | null {
  const all = list || [];
  if (!all.length) return null;
  if (selectedId) {
    const sel = all.find((c) => c.id === selectedId);
    if (sel) return sel;
  }
  for (const id of activeIds || []) {
    const hit = all.find((c) => c.id === id);
    if (hit) return hit;
  }
  return all.find((c) => c.active) ?? all[0];
}

/** Активные кампании отдельно от завершённых: первые — карточками, вторые — историей.
 *  Признак берём из active_ids, если он есть, иначе из поля active самой кампании:
 *  экран не должен зависеть от одного источника правды больше, чем нужно. */
export function splitCampaigns<T extends { id: string; active?: boolean }>(
  list: T[] | null | undefined, activeIds?: string[] | null,
): { active: T[]; history: T[] } {
  const ids = new Set(activeIds || []);
  const isActive = (c: T) => (activeIds?.length ? ids.has(c.id) : !!c.active);
  const all = list || [];
  return { active: all.filter(isActive), history: all.filter((c) => !isActive(c)) };
}

/** Сводка кампании одной строкой: сколько переключено и сколько ждёт человека. */
export function rollSummary(robots: Array<{ state: string }> | null | undefined) {
  const list = (robots || []).filter((r) => r.state !== 'skipped');
  const done = list.filter((r) => r.state === 'switched').length;
  const stuck = list.filter((r) => stateTone(r.state) === 'bad').length;
  return { total: list.length, done, stuck };
}

/** Строки CSV таблицы роботов: ровно то, что видно на экране. */
export function robotsCsv(robots: Array<Record<string, any>>): Array<Record<string, any>> {
  return (robots || []).map((r) => ({
    robot_id: r.robot_id, name: robotName(r), mode: r.mode, state: r.state,
    state_ru: stateLabel(r.state), position: r.position, working_orders: r.working_orders,
    bars_count: r.bars_count, paused: r.paused ? 1 : 0, note: r.note || '',
  }));
}

/** Подтверждение перекладки ручной позиции: оператор вводит код НОВОГО контракта.
 *  Регистр не важен, пробелы по краям тоже; пустой ввод никогда не проходит. */
export function confirmMatches(input: string, newCode: string): boolean {
  const a = (input || '').trim().toUpperCase();
  return !!a && a === (newCode || '').trim().toUpperCase();
}
