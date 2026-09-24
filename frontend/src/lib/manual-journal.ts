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
  // ИТОГ НЕТОЧЕН — говорим это так же прямо, как про неполный период.
  if (r.journal_complete === false) {
    const d = Object.entries(r.open_vs_account ?? {})
      .map(([sym, diff]) => `${sym} ${diff > 0 ? '+' : ''}${diff}`).join(', ');
    out.push('Итог НЕТОЧЕН: сведение journal не сошлось с позицией счёта'
      + (d ? ` (${d})` : '')
      + '. Часть сделок в журнал не попала — агент отдаёт кольцо последних 500,'
      + ' и простой STL длиннее его оборота теряет сделки.');
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

/** Позиция счёта по инструментам: {RIZ6: -4}. Берётся из зеркала агента —
 *  это ФАКТ счёта, в отличие от остатка, который получается проигрыванием
 *  журнала сделок за окно. */
export function accountNet(status: any): Record<string, number> {
  const out: Record<string, number> = {};
  for (const p of (status?.health?.positions ?? [])) {
    const code = String(p?.sec ?? '');
    const net = Number(p?.net ?? 0);
    if (code) out[code] = net;
  }
  return out;
}

/** Расхождение «открытой позиции» отчёта с позицией счёта.
 *
 *  `open` в отчёте — это ОСТАТОК проигрывания журнала сделок ЗА ОКНО, а не
 *  позиция счёта: позиция, набранная до начала окна, в журнал окна не входит, и
 *  остаток получается любым. 24.09.2026 экран написал «RIZ6 −4, переоценка −976
 *  ₽», когда счёт был ПУСТ. Печатать такое числом нельзя — это выдуманная
 *  позиция; печатаем расхождение словами.
 *
 *  `null` в `net` = позиции счёта мы не знаем (зеркало не приехало): тогда и
 *  расхождения не утверждаем.
 */
export function openMismatch(r: PnlReport | null, net: Record<string, number> | null):
    Array<{ symbol: string; journal: number; account: number }> {
  if (!r || !net) return [];
  const out: Array<{ symbol: string; journal: number; account: number }> = [];
  for (const o of r.open ?? []) {
    const j = Number(o.position ?? 0);
    const a = Number(net[o.symbol] ?? 0);
    if (j !== a) out.push({ symbol: o.symbol, journal: j, account: a });
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
  'сделка': 'сделка',
};
export function eventRu(row: JournalRow): string {
  const e = String(row.event ?? '');
  return EVENT_RU[e] ?? e;
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
