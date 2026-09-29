/** Журнал ручных заявок: пересчёт ответов API в то, что показывает экран.
 *
 *  Вынесено из разметки, потому что все три ловушки этого экрана — про ЧЕСТНОСТЬ
 *  числа, а не про вёрстку (real-trade 24.09.2026):
 *    • `partial` — журнал сделок начат 23.09.2026, и «за месяц» пока не месяц;
 *    • `priced=false` — хотя бы у одного инструмента нет ₽ за пункт, и тогда
 *      рублёвый итог НЕПОЛНЫЙ: показываем пункты и говорим об этом;
 *    • `open` — переоценка открытой позиции, она в `net_rub` НЕ входит и меняется
 *      каждую секунду: отдельной строкой и с другой подписью, не складывать.
 */

export type Period = 'day' | 'week' | 'month';

export const PERIOD_RU: Record<Period, string> = {
  day: 'день', week: 'неделя', month: 'месяц',
};

/** Три канала ручной торговли — ровно те, что называет оператор. Имена каналов
 *  даёт сервер (quik|broker|smart), он же их и различает: по реестру роботов, а
 *  не по форме тега (real-trade 24.09.2026). Экран только переводит. */
export const CHANNEL_RU: Record<string, string> = {
  quik: 'терминал QUIK',
  broker: 'приложение брокера (FINAM)',
  smart: 'умные заявки STL',
  recon: 'выравнивание книг робота',
};
export function channelRu(c: string | undefined): string {
  return CHANNEL_RU[String(c ?? '')] ?? String(c ?? '—');
}

/** История ЗАЯВОК полна только у умных: терминал и приложение брокера своих
 *  намерений STL не рассказывают, от них видны только сделки. */
export const CHANNELS_WITH_EVENTS = new Set(['smart']);

export interface PnlReport {
  period?: string; from?: string; to?: string;
  fills?: number; lots?: number;
  gross_rub?: number; commission_rub?: number; net_rub?: number;
  priced?: boolean; partial?: boolean; coverage_from?: string | null;
  orders?: number;
  // Границы окна ЯВНО, в миллисекундах: день считается с 07:00 МСК, и писать
  // «период 26.09…26.09» значило бы подтверждать сутки (real-trade 26.09.2026).
  from_ms?: number; to_ms?: number;
  // Откуда взята открытая позиция и остаток окна, который раньше и был в `open`.
  open_source?: 'account' | 'window';
  window_residual?: Array<{ symbol: string; position?: number }>;
  // Сведение не сошлось с позицией счёта: часть сделок в журнал не попала
  // (агент отдаёт ринг 500 последних). Итог тогда НЕ точный.
  journal_complete?: boolean;
  open_vs_account?: Record<string, number>;
  account_manual?: Record<string, number>;
  by_channel?: Array<{ channel: string; fills?: number; lots?: number; orders?: number;
                       gross_rub?: number; commission_rub?: number; net_rub?: number }>;
  by_day?: Array<{ date: string; fills?: number; lots?: number;
                   gross_rub?: number; commission_rub?: number; net_rub?: number }>;
  by_symbol?: Array<{ symbol: string; point_value?: number; fills?: number; lots?: number;
                      realized_points?: number; gross_rub?: number; commission_rub?: number }>;
  by_source?: Array<{ source: string; fills?: number; lots?: number;
                      gross_rub?: number; commission_rub?: number }>;
  open?: Array<{ symbol: string; position?: number; avg_price?: number; last?: number;
                 point_value?: number; unrealized_rub?: number }>;
}

/** Оговорки к итогу. Пустой список = цифру можно читать как есть. */
export function pnlCaveats(r: PnlReport | null): string[] {
  if (!r) return [];
  const out: string[] = [];
  if (r.partial) {
    out.push(r.coverage_from
      ? `Данные с ${r.coverage_from}: журнал сделок начат позже начала периода, за весь «${PERIOD_RU[(r.period as Period)] ?? r.period}» фактов ещё нет.`
      : 'Журнал покрывает не весь период: часть окна без фактов.');
  }
  // ЭТО НЕ ТРЕВОГА, А АРИФМЕТИКА. Остаток окна считается проигрыванием сделок
  // ВНУТРИ окна, с нуля. Значит остаток − позиция счёта = МИНУС позиция, которая
  // была на начало окна. День считается с 07:00, и любая переночевавшая позиция
  // даёт «расхождение» каждый день. Пугать им нельзя: 28.09.2026 оператор
  // спросил, что ему сделать, чтобы это не появлялось, — а делать нечего, это
  // нормальная работа границы. Печатаем факт: сколько было на начало окна.
  // Тревогу оставляем ровно для случая, когда позиция счёта НЕИЗВЕСТНА и
  // проверить нечем.
  if (r.journal_complete === false && !r.account_manual) {
    out.push('Полноту журнала проверить не с чем: позиция счёта неизвестна'
      + ' (зеркало агента не пришло). Список сделок может быть неполным.');
  }

  if (r.priced === false) {
    const noPv = (r.by_symbol ?? []).filter((s) => !(s.point_value && s.point_value > 0))
      .map((s) => s.symbol);
    out.push('Рублёвый итог НЕПОЛНЫЙ: ₽ за пункт неизвестен'
      + (noPv.length ? ` у ${noPv.join(', ')}` : '')
      + '. Их результат смотрите в пунктах по инструментам ниже.');
  }
  return out;
}

/** Переоценка открытой позиции — ОТДЕЛЬНО от итога, суммировать с ним нельзя. */
export function openTotalRub(r: PnlReport | null): number | null {
  const rows = r?.open ?? [];
  if (!rows.length) return null;
  let sum = 0;
  let known = false;
  for (const o of rows) {
    if (typeof o.unrealized_rub === 'number' && Number.isFinite(o.unrealized_rub)) {
      sum += o.unrealized_rub; known = true;
    }
  }
  return known ? sum : null;
}

/** Итог в пунктах по инструментам, у которых нет ₽ за пункт: иначе они молча
 *  выпадают из рублёвой цифры и оператор их не видит вовсе. */
export function unpricedPoints(r: PnlReport | null): Array<{ symbol: string; points: number }> {
  return (r?.by_symbol ?? [])
    .filter((s) => !(s.point_value && s.point_value > 0) && Number(s.realized_points ?? 0) !== 0)
    .map((s) => ({ symbol: s.symbol, points: Number(s.realized_points ?? 0) }));
}

export interface JournalRow {
  ts_ms?: number; type?: 'event' | 'trade'; event?: string; source?: string;
  so_id?: string; code?: string; side?: string; qty?: number; kind?: string;
  parent_id?: string; detail?: string; price?: number; order_num?: string;
}

/** Событие словами. Коды приходят из движка; незнакомый код показываем КАК ЕСТЬ,
 *  а не прячем за «прочее» — иначе новый вид события исчезнет с экрана молча. */
const EVENT_RU: Record<string, string> = {
  created: 'взведена',
  activated: 'активирована',
  fired: 'сработала',
  cancelled: 'снята',
  rejected: 'отклонена',
  native_sent: 'отправлена в терминал',
  native_live: 'принята терминалом',
  native_rejected: 'терминал отказал',
  native_cancelled: 'снята в терминале',
  native_expired: 'истекла в терминале',
  orphaned: 'исполнение не подтверждено',
  // Доведение заявки до исполнения (real-trade 29.09.2026). Карточка говорит,
  // что БУДЕТ; строка журнала говорит, что БЫЛО — разбирать потом придётся
  // именно второе. Разбор родного стопа на 70 контрактов 29.09 занял час ровно
  // потому, что событий не было, только косвенные следы в таблице.
  escalated: 'доведение до исполнения',
  // Ход многоразовой фигуры: от какой стенки, куда встала позиция, какой это
  // переворот. Без ленты вопрос «почему коридор сейчас в шорте на 20» остаётся
  // без ответа, а он возникает первым.
  corridor: 'ход фигуры',
  'сделка': 'сделка',
};
export function eventRu(row: JournalRow): string {
  const e = String(row.event ?? '');
  return EVENT_RU[e] ?? e;
}

/** Насколько громко показать строку.
 *
 *  `market` — заявка ушла РЫНОЧНОЙ. Это единственное место во всей системе, где
 *  мы отправляем заявку без цены (просьба real-trade 29.09.2026 выделить её
 *  сильнее прочего): значит лимит не налили, рынок в тот момент бежал, и цена
 *  выхода будет хуже расчётной.
 *
 *  `warn` — прочее доведение (фаза преследования) и отказы: событие заметное, но
 *  не чрезвычайное.
 *
 *  Опознаём рыночную фазу по тексту движка, потому что отдельного поля у
 *  события нет. Текст не совпал — строка остаётся `warn`, а не теряет
 *  подсветку совсем: пропустить громкое событие хуже, чем подсветить лишнее. */
export function eventTone(row: JournalRow): '' | 'warn' | 'market' {
  const e = String(row.event ?? '');
  if (e === 'escalated') {
    return /по\s+рынку|рыночной/i.test(String(row.detail ?? '')) ? 'market' : 'warn';
  }
  if (e === 'rejected' || e === 'native_rejected' || e === 'orphaned') return 'warn';
  return '';
}

/** Строки ленты, отфильтрованные тем, что выбрал оператор. */
export function filterRows(rows: JournalRow[], opts: {
  query?: string; kind?: 'all' | 'event' | 'trade'; code?: string;
}): JournalRow[] {
  const q = (opts.query ?? '').trim().toLowerCase();
  const words = q ? q.split(/\s+/).filter(Boolean) : [];
  return (rows ?? []).filter((r) => {
    if (opts.kind && opts.kind !== 'all' && r.type !== opts.kind) return false;
    if (opts.code && r.code !== opts.code) return false;
    if (!words.length) return true;
    const hay = [r.event, eventRu(r), r.source, r.so_id, r.code, r.side, r.detail, r.order_num]
      .map((x) => String(x ?? '').toLowerCase()).join(' ');
    return words.every((w) => hay.includes(w));
  });
}

/** Инструменты, встретившиеся в ленте — для фильтра-выпадашки. */
export function rowCodes(rows: JournalRow[]): string[] {
  return [...new Set((rows ?? []).map((r) => String(r.code ?? '')).filter(Boolean))].sort();
}

/** Позиция, которая была на начало окна, по инструментам.
 *
 *  Остаток окна считается с нуля, поэтому `остаток − счёт = −позиция на старте`.
 *  Это не расхождение и не ошибка: день считается с 07:00 МСК, и всё, набранное
 *  раньше, в окно не входит. Показываем это фактом вместо тревоги (28.09.2026).
 */
export function positionAtWindowStart(r: PnlReport | null): Array<{ symbol: string; qty: number }> {
  if (!r || !r.account_manual) return [];
  const resid: Record<string, number> = {};
  for (const w of r.window_residual ?? [] as any) {
    if (w && w.symbol) resid[w.symbol] = Number(w.position ?? 0);
  }
  const out: Array<{ symbol: string; qty: number }> = [];
  for (const sym of new Set([...Object.keys(resid), ...Object.keys(r.account_manual)])) {
    const qty = Number(r.account_manual[sym] ?? 0) - Number(resid[sym] ?? 0);
    if (qty) out.push({ symbol: sym, qty });
  }
  return out.sort((a, b) => a.symbol.localeCompare(b.symbol));
}

/** «ВМ ручных за день» из разбивки агента — та самая цифра, что стоит в панели.
 *  Складываем НЕ-роботов: всё, что не робот, сделал оператор своими руками. */
export function vmManualFromStatus(status: any): number | null {
  const classes = status?.day?.classes;
  if (!Array.isArray(classes)) return null;
  let sum = 0;
  for (const c of classes) {
    if (String(c?.kind ?? '') === 'robot') continue;
    const v = Number(c?.vm_rub ?? 0);
    if (Number.isFinite(v)) sum += v;
  }
  return sum;
}

export interface Reconcile {
  vm: number;              // ВМ ручных за день (панель)
  realizedNet: number;     // реализовано по журналу, после комиссии
  commission: number;      // комиссия, которую журнал вычел
  unrealized: number;      // переоценка открытой позиции
  residual: number;        // что не объяснилось
  notes: string[];
}

/** СВЕРКА ДВУХ ЦИФР. Оператор требует, чтобы суммы в панели и в журнале
 *  сходились. Сами по себе они и не должны: одна считает рыночную переоценку за
 *  день, вторая — закрытые круги без переоценки и с ОЦЕНОЧНОЙ комиссией.
 *  Поэтому показываем не «равно», а МОСТ: из чего складывается разница и что
 *  осталось необъяснённым. Необъяснённый остаток — единственное, на что стоит
 *  смотреть; всё остальное это разные вопросы к одним и тем же сделкам.
 */
export function reconcile(r: PnlReport | null, vm: number | null): Reconcile | null {
  if (!r || vm == null) return null;
  const realizedNet = Number(r.net_rub ?? 0);
  const commission = Math.abs(Number(r.commission_rub ?? 0));
  const unrealized = openTotalRub(r) ?? 0;
  // ВМ считается ДО комиссии (она списывается отдельно) и включает переоценку.
  const explained = realizedNet + commission + unrealized;
  const notes: string[] = [];
  if (r.period !== 'day') {
    notes.push('Сверка имеет смысл только для ДНЯ: в панели дневная величина.');
  }
  notes.push('Окна разные: панель считает сутки с полуночи МСК, журнал — день с 07:00.'
    + ' Сделки вечерней сессии, которые QUIK датирует сегодняшним днём, попадают'
    + ' в панель и не попадают в журнал.');
  if (openTotalRub(r) == null) {
    notes.push('Переоценка открытой позиции неизвестна — в мосте она принята нулём.');
  }
  return { vm, realizedNet, commission, unrealized, residual: vm - explained, notes };
}
