// Витрина кампаний бэктеста: чистая логика (маршрут, фильтры, геометрия кривой).
//
// Заказ оператора 04.10.2026, разделение работы с окном backtests: они отдают
// данные (реестр + сборщик, формат в docs/campaign-showcase-spec.md), здесь
// страница. Всё, где легко соврать — смена цвета на нуле, единицы, статусы,
// «ред. N из M», — вынесено сюда и покрыто тестами: компонент остаётся тонким.
//
// Принцип страницы тот же, что у остальных экранов: пустое остаётся пустым.
// Нет кривой — это не нулевая линия, нет итога — это не «0».

export interface Headline { net: number | null; trades: number | null; max_dd: number | null; window: string | null }
export interface Card {
  slug: string; title: string; idea?: string; strategy?: string;
  family?: string; rev?: number; status: string;
  progress?: { finished: number; total: number } | null;
  updated_at?: string | number | null;
  headline?: Partial<Headline> | null;
  thumb?: [number, number][] | null;
  no_curve_reason?: string | null;
  verdict?: string | null; doc?: string | null;
  unit?: string; kind?: string; symbols?: string[];
}

// ── Маршрут ─────────────────────────────────────────────────────────────────
// Путевые URL работают без правки nginx: он отдаёт index.html на любой путь
// (проверено 04.10.2026 на несуществующем). Query-форма держится для ссылок из
// документов — если путь когда-нибудь поменяем, они не умрут.
const SLUG_RE = /^[a-z0-9][a-z0-9-]{0,127}$/;
export const BASE_PATH = '/backtest/campaigns';

export type Route = { kind: 'list' } | { kind: 'campaign'; slug: string; rev?: number };

export function routeOf(pathname: string, search = ''): Route | null {
  const p = pathname.replace(/\/+$/, '');
  if (p === BASE_PATH) return { kind: 'list' };
  if (p.startsWith(BASE_PATH + '/')) {
    // /<slug> или /<slug>/rev/<n> — редакция карточки (рабочее место бэктеста,
    // спека docs/backtest-workbench-spec.md). Номер редакции — целое ≥ 1.
    const m = p.slice(BASE_PATH.length + 1).match(/^([^/]+)(?:\/rev\/(\d+))?$/);
    if (!m) return null;
    let slug = '';
    try { slug = decodeURIComponent(m[1]); } catch { return null; }
    // Битый slug — это не «витрина», а неизвестная страница: возвращаем null, и
    // приложение откроет обычный терминал, а не покажет пустой отчёт.
    if (!SLUG_RE.test(slug)) return null;
    const rev = m[2] === undefined ? undefined : Number(m[2]);
    if (rev !== undefined && rev < 1) return null;
    return rev === undefined ? { kind: 'campaign', slug } : { kind: 'campaign', slug, rev };
  }
  const qs = new URLSearchParams(search);
  if (qs.get('lab') === 'campaigns') {
    const c = qs.get('c');
    if (!c) return { kind: 'list' };
    return SLUG_RE.test(c) ? { kind: 'campaign', slug: c } : null;
  }
  return null;
}

export const campaignPath = (slug: string, rev?: number) =>
  `${BASE_PATH}/${encodeURIComponent(slug)}${rev == null ? '' : `/rev/${rev}`}`;

// ── Время и единицы ─────────────────────────────────────────────────────────
// В спеке ось времени названа «ts» без единицы. Секунды (≈1.8e9) и миллисекунды
// (≈1.8e12) не пересекаются на сотни лет, поэтому различаем по величине. Если
// backtests зафиксируют единицу письмом — эту функцию можно упростить.
export const toMs = (ts: number) => (ts < 1e11 ? ts * 1000 : ts);

const UNIT_LABEL: Record<string, string> = {
  rub: '₽', rubles: '₽', 'руб': '₽', points: 'п.', pts: 'п.', pct: '%', percent: '%',
};
const NBSP = ' ';

export function unitLabel(unit?: string | null): string {
  if (!unit) return '';
  return UNIT_LABEL[unit] ?? unit;      // незнакомую единицу печатаем как есть, не гадаем
}

/** Число с единицей. null → «—», а не «0»: нет итога и нулевой итог — разные вещи. */
export function fmtPnl(v: number | null | undefined, unit?: string | null, signed = true): string {
  if (v == null || !Number.isFinite(v)) return '—';
  const u = unitLabel(unit);
  const dec = u === '%' ? 1 : 0;
  const body = Math.abs(v).toLocaleString('ru-RU', { maximumFractionDigits: dec, minimumFractionDigits: dec });
  const sign = v < 0 ? '−' : (signed && v > 0 ? '+' : '');
  return (sign + body + (u ? NBSP + u : '')).replace(/ /g, NBSP);
}

export const cls = (v: number | null | undefined) =>
  v == null || !Number.isFinite(v) ? '' : v > 0 ? 'pos' : v < 0 ? 'neg' : '';

// ── Статусы ─────────────────────────────────────────────────────────────────
export interface StatusInfo { label: string; tone: 'ok' | 'run' | 'wait' | 'bad' | 'unk' }
export function statusInfo(c: Pick<Card, 'status' | 'progress'>): StatusInfo {
  switch (c.status) {
    case 'done': return { label: 'готово', tone: 'ok' };
    case 'running': {
      const p = c.progress;
      return { label: p && p.total ? `идёт ${p.finished}/${p.total}` : 'идёт', tone: 'run' };
    }
    case 'queued': return { label: 'ожидает прогона', tone: 'wait' };
    case 'no_curve': return { label: 'кривой нет', tone: 'bad' };
    // Незнакомый статус печатаем кодом: новый статус сборщика не должен
    // исчезнуть с экрана молча (так дважды прятало новые виды заявок).
    default: return { label: c.status || '—', tone: 'unk' };
  }
}

// ── Редакции ────────────────────────────────────────────────────────────────
/** Для каждой карточки: её редакция и сколько редакций у линии в этом списке. */
export function revisionOf(cards: Card[]): Map<string, { rev: number; of: number } | null> {
  const maxByFamily = new Map<string, number>();
  const countByFamily = new Map<string, number>();
  for (const c of cards) {
    if (!c.family) continue;
    maxByFamily.set(c.family, Math.max(maxByFamily.get(c.family) ?? 0, c.rev ?? 0));
    countByFamily.set(c.family, (countByFamily.get(c.family) ?? 0) + 1);
  }
  const out = new Map<string, { rev: number; of: number } | null>();
  for (const c of cards) {
    const n = c.family ? countByFamily.get(c.family) ?? 0 : 0;
    // Единственная редакция линии — метки нет: «ред. 1 из 1» ничего не сообщает.
    out.set(c.slug, c.family && n > 1 && c.rev != null
      ? { rev: c.rev, of: maxByFamily.get(c.family) ?? c.rev } : null);
  }
  return out;
}

/** Линия идеи по возрастанию редакции — для цепочки «ред. 1 → 2 → 3». */
export function chainOf(cards: Card[], family?: string): Card[] {
  if (!family) return [];
  return cards.filter((c) => c.family === family).sort((a, b) => (a.rev ?? 0) - (b.rev ?? 0));
}

// ── Фильтры ─────────────────────────────────────────────────────────────────
export interface Filters {
  status: string;        // 'all' | код статуса
  kind: string;          // 'all' | 'research' | 'optimizer'
  q: string;
  allRevisions: boolean;
  symbol: string;        // '' | инструмент (работает, только если сборщик отдал symbols)
}
export const NO_FILTERS: Filters = { status: 'all', kind: 'all', q: '', allRevisions: false, symbol: '' };

export function visibleCards(cards: Card[], f: Filters): Card[] {
  // Свёртка до последней редакции линии — ДО остальных фильтров: иначе фильтр по
  // статусу выбросил бы свежую редакцию и показал на её месте старую.
  let list = cards;
  if (!f.allRevisions) {
    const latest = new Map<string, number>();
    for (const c of cards) if (c.family) latest.set(c.family, Math.max(latest.get(c.family) ?? 0, c.rev ?? 0));
    list = cards.filter((c) => !c.family || (c.rev ?? 0) === latest.get(c.family));
  }
  const q = f.q.trim().toLowerCase();
  return list.filter((c) => {
    if (f.status !== 'all' && c.status !== f.status) return false;
    if (f.kind !== 'all' && (c.kind ?? 'research') !== f.kind) return false;
    if (f.symbol && !(c.symbols ?? []).includes(f.symbol)) return false;
    if (q && !`${c.title} ${c.idea ?? ''} ${c.strategy ?? ''} ${c.slug}`.toLowerCase().includes(q)) return false;
    return true;
  });
}

export const KIND_LABEL: Record<string, string> = { research: 'исследование', optimizer: 'оптимизатор' };

// ── Геометрия кривой ────────────────────────────────────────────────────────
// Рисунок как в TSLab: площадь от нулевой оси, ВЫШЕ нуля зелёная, НИЖЕ красная.
// Смена цвета СТРОГО на y=0, без перехода. Делаем это не градиентом и не
// clipPath, а разрезанием кривой в точках пересечения нуля: каждый кусок живёт
// по одну сторону оси и красится своим цветом, а точка пересечения лежит ровно
// на нуле (линейная интерполяция), поэтому «зелёное под нулём» невозможно.
export interface Seg { sign: 1 | -1 | 0; pts: [number, number][] }

export function splitAtZero(points: [number, number][]): Seg[] {
  const pts = points.filter((p) => Number.isFinite(p[0]) && Number.isFinite(p[1]));
  if (!pts.length) return [];
  const sgn = (v: number): 1 | -1 | 0 => (v > 0 ? 1 : v < 0 ? -1 : 0);
  const first = pts.find((p) => p[1] !== 0);
  if (!first) return [{ sign: 0, pts: pts.slice() }];       // вся кривая на нуле
  let cur: Seg = { sign: sgn(first[1]), pts: [] };
  const out: Seg[] = [];
  let prev: [number, number] | null = null;
  for (const p of pts) {
    const s = sgn(p[1]);
    if (s !== 0 && s !== cur.sign && prev) {
      // Пересечение нуля между prev и p. prev тоже может лежать на нуле — тогда
      // кусок просто меняется на ней, интерполяции не нужно.
      const x0 = prev[1] === 0 ? prev[0]
        : prev[0] + (p[0] - prev[0]) * (0 - prev[1]) / (p[1] - prev[1]);
      const z: [number, number] = [x0, 0];
      cur.pts.push(z);
      out.push(cur);
      cur = { sign: s, pts: [z] };
    }
    cur.pts.push(p);
    prev = p;
  }
  out.push(cur);
  return out;
}

export interface Pad { l: number; r: number; t: number; b: number }
export interface Geometry {
  xmin: number; xmax: number; ymin: number; ymax: number; y0: number;
  x: (ts: number) => number; y: (v: number) => number;
  segs: { sign: 1 | -1 | 0; line: string; area: string }[];
  /** Вторая серия («купил и держи») на ТОЙ ЖЕ шкале, что и основная. */
  extraLine: string | null;
}

/** Кривая → пути SVG. null — рисовать нечего (меньше двух точек): вызывающий
 *  обязан сказать причину словами, а не нарисовать пустую рамку. */
export function curveGeometry(
  points: [number, number][] | null | undefined, w: number, h: number,
  pad: Pad = { l: 0, r: 0, t: 0, b: 0 },
  extra: [number, number][] | null = null,
): Geometry | null {
  if (!points || points.length < 2) return null;
  const pm = points.map((p) => [toMs(p[0]), p[1]] as [number, number])
    .filter((p) => Number.isFinite(p[0]) && Number.isFinite(p[1]))
    .sort((a, b) => a[0] - b[0]);
  if (pm.length < 2) return null;
  const xmin = pm[0][0], xmax = pm[pm.length - 1][0];
  // Нулевая ось входит в диапазон ВСЕГДА: кривая целиком выше нуля без оси
  // выглядела бы «в плюсе от начала», хотя это просто масштаб.
  // Вторая серия входит в ДИАПАЗОН, но не в границы времени основной: сравнивать
  // кривые на разных шкалах — это нарисовать ложную разницу.
  const em = (extra ?? []).map((p) => [toMs(p[0]), p[1]] as [number, number])
    .filter((p) => Number.isFinite(p[0]) && Number.isFinite(p[1]) && p[0] >= xmin && p[0] <= xmax)
    .sort((a, b) => a[0] - b[0]);
  let ymin = Math.min(0, ...pm.map((p) => p[1]), ...em.map((p) => p[1]));
  let ymax = Math.max(0, ...pm.map((p) => p[1]), ...em.map((p) => p[1]));
  if (ymin === ymax) { ymin -= 1; ymax += 1; }
  const iw = Math.max(1, w - pad.l - pad.r), ih = Math.max(1, h - pad.t - pad.b);
  const xs = xmax === xmin ? 1 : (xmax - xmin);
  const x = (ts: number) => pad.l + ((toMs(ts) - xmin) / xs) * iw;
  const y = (v: number) => pad.t + (1 - (v - ymin) / (ymax - ymin)) * ih;
  const y0 = y(0);
  const fx = (n: number) => n.toFixed(1);
  const segs = splitAtZero(pm).map((s) => {
    const xy = s.pts.map(([t, v]) => [x(t), y(v)] as [number, number]);
    const line = xy.map(([a, b], i) => `${i ? 'L' : 'M'}${fx(a)} ${fx(b)}`).join(' ');
    const last = xy[xy.length - 1], head = xy[0];
    const area = `${line} L${fx(last[0])} ${fx(y0)} L${fx(head[0])} ${fx(y0)} Z`;
    return { sign: s.sign, line, area };
  });
  const extraLine = em.length >= 2
    ? em.map(([t, v], i) => `${i ? 'L' : 'M'}${fx(x(t))} ${fx(y(v))}`).join(' ') : null;
  return { xmin, xmax, ymin, ymax, y0, x, y, segs, extraLine };
}

/** «Красивые» метки оси: шаг 1/2/5 × 10^k, включая нуль, когда он в диапазоне. */
export function niceTicks(min: number, max: number, count = 5): number[] {
  if (!(max > min)) return [min];
  const raw = (max - min) / Math.max(1, count);
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const norm = raw / mag;
  const step = (norm >= 5 ? 5 : norm >= 2 ? 2 : 1) * mag;
  const out: number[] = [];
  for (let v = Math.ceil(min / step) * step; v <= max + step * 1e-9; v += step) {
    out.push(Math.abs(v) < step * 1e-9 ? 0 : v);
  }
  return out;
}

// ── «Купил и держи» и разность ──────────────────────────────────────────────
/** Стратегия минус «купил и держи», в точках СТРАТЕГИИ.
 *
 *  Значение второй кривой между её точками берём линейной интерполяцией; ТОЧКИ
 *  СТРАТЕГИИ ВНЕ ДИАПАЗОНА второй кривой выбрасываем, а не экстраполируем:
 *  дорисованный хвост выдал бы за разность то, чего в данных нет. Нет второй
 *  кривой или она короче двух точек — null, и экран говорит об этом словами. */
export function diffCurve(
  strategy: [number, number][] | null | undefined,
  hold: [number, number][] | null | undefined,
): [number, number][] | null {
  if (!strategy || strategy.length < 2 || !hold || hold.length < 2) return null;
  const h = hold.map((p) => [toMs(p[0]), p[1]] as [number, number])
    .filter((p) => Number.isFinite(p[0]) && Number.isFinite(p[1])).sort((a, b) => a[0] - b[0]);
  if (h.length < 2) return null;
  const out: [number, number][] = [];
  let j = 0;
  for (const [ts, v] of strategy) {
    const t = toMs(ts);
    if (!Number.isFinite(t) || !Number.isFinite(v)) continue;
    if (t < h[0][0] || t > h[h.length - 1][0]) continue;       // вне диапазона — не гадаем
    while (j < h.length - 2 && h[j + 1][0] < t) j++;
    const [t0, v0] = h[j], [t1, v1] = h[j + 1];
    const hv = t1 === t0 ? v0 : v0 + (v1 - v0) * (t - t0) / (t1 - t0);
    out.push([ts, v - hv]);
  }
  return out.length >= 2 ? out : null;
}

// ── Честный объём ───────────────────────────────────────────────────────────
// Жёсткое требование оператора (04.10.2026): ВСЕ цифры без плеча. Объём — число
// контрактов, ПОЛНАЯ стоимость — цена × стоимость пункта × контрактов в пике;
// гарантийное обеспечение нигде не используется. Считает движок (backtests), тут
// только чтение: вторая реализация расчёта разошлась бы с первой.
export interface HonestVolume {
  contracts: number | null; fullCost: number | null; net: number | null; returnPct: number | null;
  /** Поля, которых сборщик не отдал: экран называет их, а не рисует нули. */
  missing: string[];
}
export function honestVolume(l: Record<string, any> | null | undefined): HonestVolume {
  const num = (...keys: string[]): number | null => {
    for (const k of keys) {
      const v = l?.[k] ?? l?.metrics?.[k];
      if (typeof v === 'number' && Number.isFinite(v)) return v;
    }
    return null;
  };
  const r: HonestVolume = {
    contracts: num('contracts_peak'), fullCost: num('full_cost_rub'),
    net: num('net', 'net_taker'), returnPct: num('return_pct'), missing: [],
  };
  if (r.contracts == null) r.missing.push('contracts_peak');
  if (r.fullCost == null) r.missing.push('full_cost_rub');
  if (r.returnPct == null) r.missing.push('return_pct');
  return r;
}
