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

// Generic per-parameter help by KEY — shared indicator params across strategies
// (period, mult, oversold…). Grounded in trader/lab/strategies/library.py signal
// functions. Used as a fallback so every field gets an explanation.
const GENERIC: Record<string, ParamHelp> = {
  qty: { title: 'Объём (контрактов на вход)', short: 'Базовый размер позиции',
    what: 'Сколько контрактов робот покупает/продаёт при открытии и при каждой докупке. 1 = один фьючерс. Масштабирует прибыль и риск линейно.' },
  period: { title: 'Период индикатора (баров M1)', short: 'Сколько минутных баров в расчёте',
    what: 'Длина окна расчёта индикатора в 1-минутных барах. Короче — быстрее реагирует, больше ложных сигналов; длиннее — плавнее, но с задержкой.' },
  mult: { title: 'Множитель ширины (×10)', short: 'Ширина канала/полос в сигмах или ATR',
    what: 'Хранится ×10: 20 = 2.0. Для Боллинджера — число стандартных отклонений; для Кельтнера/ATR — множитель ATR. Больше — полосы шире, сигналов реже, но надёжнее.' },
  fast: { title: 'Быстрая линия', short: 'Короткий период скользящей',
    what: 'Период быстрой скользящей средней (реагирует на цену быстро). Пересечение с медленной задаёт сигнал.' },
  slow: { title: 'Медленная линия', short: 'Длинный период скользящей',
    what: 'Период медленной скользящей — задаёт основной тренд. Пересечение быстрой с медленной = смена направления.' },
  mid: { title: 'Средняя SMA', short: 'Промежуточный период',
    what: 'Средняя из трёх скользящих. Сигнал — когда быстрая > средняя > медленная (лонг) или наоборот (шорт).' },
  signal: { title: 'Сигнальная линия MACD', short: 'EMA от линии MACD',
    what: 'Период EMA, сглаживающей линию MACD. MACD выше сигнальной → лонг, ниже → шорт.' },
  ema1: { title: 'EMA1 (быстрая)', short: 'Быстрая EMA кроссовера',
    what: 'Быстрая экспоненциальная средняя. Выше EMA2 → лонг, ниже → шорт. Всегда в рынке, переворот на пересечении.' },
  ema2: { title: 'EMA2 (медленная)', short: 'Медленная EMA кроссовера',
    what: 'Медленная EMA — долгосрочный тренд. Пересечение EMA1×EMA2 = момент переворота позиции.' },
  ema_period: { title: 'EMA фильтр тренда', short: 'Период трендовой EMA',
    what: 'Период EMA, определяющей тренд. Цена выше EMA — ищем только лонги, ниже — только шорты.' },
  rsi_period: { title: 'RSI период', short: 'Баров для RSI',
    what: 'Период индекса относительной силы. 14 — стандарт. Короче — RSI резче, чаще заходит в зоны перекупленности/перепроданности.' },
  atr_period: { title: 'ATR период (баров M1)', short: 'Окно ATR (волатильность)',
    what: 'Число 1-минутных баров для ATR (среднего размаха). Влияет на ширину канала и порог пробоя в пунктах.', schematic: 'atr',
    live: (_v, c) => c.atr > 0 ? `сейчас ATR ≈ ${Math.round(c.atr).toLocaleString('ru-RU')} п. за бар` : null },
  oversold: { title: 'Уровень «перепродан»', short: 'Порог для входа в лонг',
    what: 'Когда осциллятор опускается ниже этого уровня — актив «перепродан», ждём отскок вверх → лонг.' },
  overbought: { title: 'Уровень «перекуплен»', short: 'Порог для входа в шорт',
    what: 'Когда осциллятор поднимается выше этого уровня — актив «перекуплен», ждём откат вниз → шорт.' },
  threshold: { title: 'Порог срабатывания', short: 'Граница сигнала',
    what: 'Порог индикатора (CCI — пункты индекса; ROC — %×100, 50=0.5%). За порогом — сигнал; в коридоре ±порог сигнал нейтральный (закрытие).' },
  lookback: { title: 'Окно поиска импульса', short: 'Сколько баров назад сканируем',
    what: 'Окно (баров M1) поиска импульсной свечи и предшествующей ей зоны заказов (Order Block). Больше — ловит более старые зоны.' },
  impulse_frac: { title: 'Порог импульса (×10000)', short: 'Мин. тело импульсной свечи',
    what: 'Свеча считается импульсной, если |тело|/цена ≥ порога. Хранится ×10000: 30 = 0.30%. Больше — только мощные движения формируют зону, реже сигналы.',
    live: (v, c) => c.price > 0 ? `тело импульса ≥ ${Math.round(c.price * v / 10000).toLocaleString('ru-RU')} п. (${(v / 100).toFixed(2)}%)` : null },
  level: { title: 'Уровень пивотов', short: '1 = ближние R1/S1, 2 = дальние R2/S2',
    what: '1 = R1/S1 (ближние, срабатывают чаще), 2 = R2/S2 (дальние, реже, но сильнее экстремум). Считаются от вчерашних High/Low/Close.' },
  bet_step: { title: 'Система ставок +N после убытка', short: 'Рост объёма после убыточной сделки',
    what: 'После каждой убыточной закрытой сделки следующий вход увеличивается на N контрактов (1→2→3…), после прибыльной сбрасывается к базовому qty. 0 = выключено.' },
  bet_max: { title: 'Макс. добавка по ставкам', short: 'Потолок роста ставки',
    what: 'Максимум лишних контрактов по системе ставок. Например 10 при qty=1 = не больше 11 за вход. Защита от разгона на длинной серии убытков.' },
};

export function helpFor(strategyId: string, key: string): ParamHelp | null {
  return STRATEGY_HELP[strategyId]?.[key] ?? SPEC_HELP[key] ?? SHARED[key] ?? GENERIC[key] ?? null;
}

// MUST DESCRIPTION: the strategy-level block shown ABOVE the params — the four
// things an operator needs before touching any knob: the analysis timeframe, the
// entry signal, the take-profit rule, and the stop-loss rule.
export interface StrategyOverview {
  timeframe: string;  // период анализа сигналов
  entry: string;      // описание сигнала открытия
  tp: string;         // описание тейк-профита
  sl: string;         // описание стоп-лосса
}

// Timeframe + TP + SL are shared: all strategies run on M1 and use the SAME
// position-management layer (make_on_bar) — take-profit by tp_atr and NO stop-loss
// (averaging instead). Only the ENTRY signal differs per strategy.
const TF = 'М1 — минутные бары (жёстко, tf=1). Стратегия считается один раз по закрытию каждой минутки; бары строятся из ленты сделок QUIK.';
const TP = 'Тейк-профит на «средняя цена входа ± tp_atr×ATR» (если tp_atr>0). При tp_atr=0 тейка нет — выход только по смене сигнала.';
const SL = 'СТОП-ЛОССА НЕТ. Убыток закрывается сменой сигнала (или тейком). При avg_step_atr>0 робот докупает ПРОТИВ движения до avg_max — усреднение вместо стопа; «усиление» на графике — это ярлык, не отдельная логика. Жёсткий потолок «max позиция» лишь блокирует новые доборы, но не закрывает.';

export const STRATEGY_OVERVIEW: Record<string, StrategyOverview> = {
  fvg: { timeframe: TF, tp: TP, sl: SL,
    entry: 'Fair Value Gap (ICT), трёхбарный разрыв: (1) low текущего бара выше high позапрошлого — бычий (зеркально — медвежий), И (2) тело текущей 1-мин свечи ≥ min_frac (фильтр силы импульса). Оба условия — вход в сторону разрыва.' },
  macd_cross: { timeframe: TF, tp: TP, sl: SL,
    entry: 'MACD-кроссовер: линия MACD (EMA fast − EMA slow) выше сигнальной (EMA signal от MACD) → лонг, ниже → шорт. Всегда в рынке, переворот на пересечении.' },
  bollinger_mr: { timeframe: TF, tp: TP, sl: SL,
    entry: 'Контртренд от полос Боллинджера: закрытие ниже нижней полосы → лонг (ждём возврата к средней), выше верхней → шорт. В коридоре между полосами сигнал нейтральный → позиция закрывается в ноль.' },
  bollinger_bo: { timeframe: TF, tp: TP, sl: SL,
    entry: 'Пробой полос Боллинджера ПО тренду: закрытие выше верхней полосы → лонг, ниже нижней → шорт. Внутри полос — удержание позиции (без нового сигнала).' },
  bollinger_bo_m1: { timeframe: TF, tp: TP, sl: SL,
    entry: 'То же, что Bollinger Breakout (пробой полос по тренду), но усреднение ПРИНУДИТЕЛЬНО включено (avg_max≥2, шаг>0) — модификация «прокачки»: держит просадку добором вместо стопа.' },
  shectory_2ema: { timeframe: TF, tp: TP, sl: SL,
    entry: 'Пересечение двух EMA: EMA1 (быстрая) выше EMA2 (медленной) → лонг, ниже → шорт. Всегда в рынке, переворот на пересечении. Есть система ставок (+N контрактов после убытка).' },
  stochastic: { timeframe: TF, tp: TP, sl: SL,
    entry: 'Стохастик %K: ниже уровня «перепродан» → лонг, выше «перекуплен» → шорт. В середине диапазона — нейтрально (позиция закрывается).' },
  cci: { timeframe: TF, tp: TP, sl: SL,
    entry: 'CCI (индекс товарного канала): ниже −порога → лонг (перепродано), выше +порога → шорт. В коридоре ±порог — нейтрально (закрытие).' },
  williams_r: { timeframe: TF, tp: TP, sl: SL,
    entry: 'Williams %R: ниже −oversold → лонг (перепродано), выше −overbought → шорт. В середине — нейтрально.' },
  momentum: { timeframe: TF, tp: TP, sl: SL,
    entry: 'Моментум (цена минус цена N баров назад): >0 → лонг, <0 → шорт. Простой трендовый импульс.' },
  roc: { timeframe: TF, tp: TP, sl: SL,
    entry: 'Rate of Change: скорость изменения цены за N баров. Выше +порога → лонг, ниже −порога → шорт. В коридоре — нейтрально.' },
  triple_sma: { timeframe: TF, tp: TP, sl: SL,
    entry: 'Выравнивание трёх SMA: быстрая > средняя > медленная → лонг; быстрая < средняя < медленная → шорт. Иначе нейтрально (закрытие).' },
  keltner_bo: { timeframe: TF, tp: TP, sl: SL,
    entry: 'Пробой канала Кельтнера (EMA ± mult×ATR): закрытие выше верхней границы → лонг, ниже нижней → шорт. Внутри канала — удержание.' },
  rsi_trend: { timeframe: TF, tp: TP, sl: SL,
    entry: 'RSI-откат В тренде: цена выше EMA-фильтра (тренд вверх) И RSI ниже oversold → лонг; цена ниже EMA И RSI выше overbought → шорт. Вход на откате в направлении тренда.' },
  ema_atr: { timeframe: TF, tp: TP, sl: SL,
    entry: 'Двойная EMA + ATR-фильтр: быстрая EMA > медленной И цена > медленная + mult×ATR → лонг; зеркально → шорт. ATR-фильтр отсекает слабые пробои.' },
  order_block: { timeframe: TF, tp: TP, sl: SL,
    entry: 'Order Block (ICT): в окне lookback ищется импульсная свеча (|тело| ≥ impulse_frac), затем последняя контр-свеча перед ней — зона заказов. Лонг, когда цена возвращается в зону бычьего OB [low, high]; шорт — в зону медвежьего.' },
  pivot_reversal: { timeframe: TF, tp: TP, sl: SL,
    entry: 'Контртренд от вчерашних floor-пивотов: P=(H+L+C)/3 предыдущего дня, R1=2P−L, S1=2P−H (уровень 2 = R2/S2, дальше). Цена ≤ S1 → лонг (перепродано), ≥ R1 → шорт.' },
};

export function overviewFor(strategyId: string): StrategyOverview | null {
  return STRATEGY_OVERVIEW[strategyId] ?? null;
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
