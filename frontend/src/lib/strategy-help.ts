// Rich per-parameter help for the robot parameter editor: plain-language copy,
// mechanics, a schematic id, and a LIVE points conversion. Ground truth mirrors
// trader/lab/strategies/library.py (sig_fvg, on_bar) and indicators.atr — keep
// them in sync. All strategy bars are M1 (tf=1 hardwired), so every "свеча" here
// means one 1-minute bar.

export type SchematicId = 'fvg' | 'atr' | 'ladder' | null;

export interface LiveCtx {
  atr: number;    // current ATR in price points (0 if unknown)
  price: number;  // current/last price
}

export interface ParamHelp {
  title: string;                 // human name of the parameter
  short: string;                 // one-liner (collapsed hint / title attr)
  what: string;                  // plain explanation
  how?: string;                  // formula / mechanics
  schematic?: SchematicId;       // which diagram to draw
  note?: string;                 // caveat worth calling out
  // Live conversion: given the raw param value + current ATR/price, produce a
  // human "= X пунктов" line, or null when there's nothing to convert.
  live?: (v: number, ctx: LiveCtx) => string | null;
}

const pts = (n: number) => Math.round(n).toLocaleString('ru-RU') + ' п.';

// Shared position-management params (injected into every strategy via AVG_PARAMS).
const SHARED: Record<string, ParamHelp> = {
  avg_max: {
    title: 'Усреднение: макс. контрактов',
    short: 'Потолок контрактов в позиции при усреднении',
    what: 'Сколько контрактов робот готов НАБРАТЬ в одну позицию, докупая против движения. 1 = усреднение выключено (держит ровно qty). 5 = докупает до 5 контрактов, снижая среднюю цену входа.',
    how: 'Докупка идёт шагами по qty, пока |позиция| < avg_max. Больше avg_max = глубже усреднение, но больше ГО и просадка внутри эпизода.',
    schematic: 'ladder',
  },
  avg_step_atr: {
    title: 'Усреднение: шаг против входа',
    short: 'Как далеко против позиции цена уйдёт до докупки',
    what: 'Насколько цена должна уйти ПРОТИВ позиции, чтобы робот докупил ещё qty контрактов. Измеряется в долях ATR (авто-подстройка под волатильность), хранится ×10: 24 = 2.4×ATR. 0 = усреднение выключено.',
    how: 'Для лонга: докупка когда цена ≤ средняя − (avg_step_atr/10)×ATR. Для шорта — зеркально. ATR берётся по периоду avg_atr_n.',
    schematic: 'ladder',
    note: '«Усиление» (докупка в сторону прибыли) на графике — это ЯРЛЫК аналитики, когда цена добора оказалась на выгодной стороне средней. Стратегия докупает только ПРОТИВ движения (усреднение вниз).',
    live: (v, c) => (v > 0 && c.atr > 0) ? `${(v / 10).toFixed(1)}×ATR = ${pts((v / 10) * c.atr)} против входа` : null,
  },
  tp_atr: {
    title: 'Тейк-профит',
    short: 'Цель прибыли от средней цены входа',
    what: 'На каком расстоянии от средней цены входа робот закрывает позицию в плюс. В долях ATR, хранится ×10: 60 = 6.0×ATR. 0 = тейка нет, выход только по обратному сигналу.',
    how: 'Лонг закрывается когда цена ≥ средняя + (tp_atr/10)×ATR. Шорт — когда цена ≤ средняя − (tp_atr/10)×ATR.',
    schematic: 'ladder',
    live: (v, c) => (v > 0 && c.atr > 0) ? `${(v / 10).toFixed(1)}×ATR = ${pts((v / 10) * c.atr)} от средней` : null,
  },
  avg_atr_n: {
    title: 'Период ATR',
    short: 'По скольким минутным барам считается ATR',
    what: 'ATR (Average True Range) — средний размах цены за бар, в пунктах. Период задаётся в БАРАХ М1: 21 = ATR по 21 одноминутному бару (НЕ «21-минутная свеча»).',
    how: 'True Range бара = max(High−Low, |High−предыд.Close|, |Low−предыд.Close|). ATR = сглаженное среднее TR (метод Уайлдера) по avg_atr_n баров. Короче период = чувствительнее к последним движениям.',
    schematic: 'atr',
    live: (v, c) => (c.atr > 0) ? `сейчас ATR ≈ ${pts(c.atr)} за бар` : null,
  },
};

// Per-strategy help. fvg fully fleshed; others fall back to the schema hint.
export const STRATEGY_HELP: Record<string, Record<string, ParamHelp>> = {
  fvg: {
    symbol: {
      title: 'Инструмент (контракт)',
      short: 'FORTS-тикер, например RIU6',
      what: 'Фьючерсный контракт FORTS, которым торгует робот. От него зависят стоимость пункта и ГО. Меняется только редеплоем спеки.',
    },
    qty: {
      title: 'Объём шага (контрактов на вход)',
      short: 'Базовый размер каждой новой позиции',
      what: 'Сколько контрактов робот покупает/продаёт при открытии позиции и при каждой докупке-усреднении. 1 = один фьючерс за раз.',
      how: 'Влияет на прибыль и риск линейно: qty=2 удваивает и P&L, и требуемое ГО.',
    },
    min_frac: {
      title: 'Мин. тело свечи-подтверждения (сила импульса)',
      short: 'Размер тела текущего 1-мин бара, ×10000 от цены',
      what: 'Фильтр СИЛЫ импульса. Вход по FVG требует двух вещей: (1) трёхбарный разрыв — low текущего бара выше high позапрошлого (бычий) или зеркально (медвежий); (2) тело ТЕКУЩЕЙ 1-минутной свечи ≥ этого порога. Это НЕ расстояние между барами, а размер тела свечи-подтверждения.',
      how: 'Порог хранится ×10000 от цены: 15 = 0.15%. Тело = |close − open| текущего бара. Больше min_frac = сильнее нужен импульс = меньше входов, но чище.',
      schematic: 'fvg',
      live: (v, c) => (c.price > 0) ? `тело ≥ ${pts(c.price * v / 10000)} при цене ${Math.round(c.price).toLocaleString('ru-RU')} (${(v / 100).toFixed(2)}%)` : null,
    },
    ...SHARED,
  },
};

// Hard cap and schedule live outside params_json (spec-level), but the editor
// shows help for them too.
export const SPEC_HELP: Record<string, ParamHelp> = {
  max_position: {
    title: 'Макс. позиция (жёсткий потолок)',
    short: 'Заявка сверх этого не отправляется вовсе',
    what: 'Абсолютный потолок контрактов. Заявка, которая увела бы позицию выше него, не отправляется в QUIK — страховка перед лимитами агента (по умолчанию 10 на заявку / 20 в работе).',
    note: 'Разворот = закрыть N + открыть N = 2N контрактов в полёте одновременно. Потолок должен это учитывать.',
  },
  schedule: {
    title: 'Окно торговли',
    short: 'Часы, когда робот активен',
    what: 'Интервал МСК, в который робот принимает сигналы и торгует. Вне окна — не входит. Формат «09:00-23:55».',
  },
};

export function helpFor(strategyId: string, key: string): ParamHelp | null {
  return STRATEGY_HELP[strategyId]?.[key] ?? SPEC_HELP[key] ?? SHARED[key] ?? null;
}

// Client-side ATR (Wilder), mirroring trader/lab/indicators.py atr(). Fed by the
// M1 bars the chart already loads, so the editor shows live points even without
// a live robot (backtest context). Returns 0 when there aren't enough bars.
export function atrFromBars(
  bars: { high: number; low: number; close: number }[], period: number,
): number {
  const n = bars.length;
  if (n < period + 1) return 0;
  const trs: number[] = [];
  for (let i = 1; i < n; i++) {
    const h = bars[i].high, l = bars[i].low, pc = bars[i - 1].close;
    trs.push(Math.max(h - l, Math.abs(h - pc), Math.abs(l - pc)));
  }
  let avg = trs.slice(0, period).reduce((a, b) => a + b, 0) / period;
  for (let i = period; i < trs.length; i++) avg = (avg * (period - 1) + trs[i]) / period;
  return avg;
}
