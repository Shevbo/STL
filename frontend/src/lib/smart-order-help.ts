// Умные ручные заявки: тексты, алгоритмы и единые визуальные токены.
//
// ОДИН источник правды для трёх мест: фрейма «Заявки», линий на графике и
// легенды под ним. Если цвет или формулировка живут в двух местах, они
// разъезжаются — а здесь речь о том, что оператор поймёт про свои деньги.
//
// Все формулировки сверены с движком (trader/quik/smart_orders.py). Меняется
// движок — правится и текст, иначе интерфейс начнёт обещать не то, что будет.

export type Kind = 'sl' | 'tp' | 'trail_tp' | 'on_fill';
export type Side = 'buy' | 'sell';

export interface KindMeta {
  id: Kind;
  name: string;
  short: string;
  /** Одной строкой: зачем эта заявка нужна. */
  essence: string;
  /** Что сторож делает по шагам. Порядок = порядок исполнения в движке. */
  algorithm: string[];
  /** Поля, которые заполняет оператор. */
  fields: Array<{ key: string; label: string; hint: string }>;
  /** Цвет линии на графике и чипа в легенде. */
  color: string;
  /** lightweight-charts LineStyle: 0 сплошная, 2 пунктир, 3 точки. */
  lineStyle: number;
  legend: string;
}

/** Блоки «после сделки», общие для ВСЕХ типов (09.08.2026). Любая умная заявка
 *  только ВХОДИТ и после срабатывания забывает про позицию: без этой пары
 *  выходить нечем и прибыль забрать некому. Движок разрешает их каждому типу
 *  (`SmartOrder.validate`), поэтому и в форме они стоят везде. */
const AFTER_FILL_FIELDS = [
  { key: 'sl_offset', label: 'Стоп после сделки, пункты',
    hint: '0 — без стопа. Иначе сразу после сделки встанет защитный стоп на этом расстоянии ПРОТИВ цены входа' },
  { key: 'tp_offset', label: 'Тейк после сделки, пункты',
    hint: '0 — без тейка. Иначе после сделки встанет тейк на этом расстоянии В ПОЛЬЗУ входа; со стопом они в одной связке — сработал один, второй снимется' },
];

export const KINDS: KindMeta[] = [
  {
    id: 'sl',
    name: 'Условная',
    short: 'УСЛ',
    essence: 'Сработать по достижении уровня: цена дошла — заявка ушла на биржу.',
    algorithm: [
      'Сторож раз в секунду смотрит цену последней сделки.',
      'Для ПРОДАЖИ срабатывает, когда цена опустилась ДО уровня или ниже (защита лонга).',
      'Для ПОКУПКИ срабатывает, когда цена поднялась ДО уровня или выше (защита шорта).',
      'В момент срабатывания ставится лимитная заявка, пробивающая рынок, и заявка помечается исполненной.',
    ],
    fields: [
      { key: 'trigger_price', label: 'Уровень', hint: 'цена, на которой заявка срабатывает' },
      ...AFTER_FILL_FIELDS,
    ],
    color: '#ff6b5a',
    lineStyle: 2,
    legend: 'условная: уровень срабатывания',
  },
  {
    id: 'tp',
    name: 'Лимитная',
    short: 'ЛИМ',
    essence: 'Сработать по достижении назначенной цены в свою пользу.',
    algorithm: [
      'Сторож раз в секунду смотрит цену последней сделки.',
      'Для ПРОДАЖИ срабатывает, когда цена поднялась ДО цели или выше.',
      'Для ПОКУПКИ срабатывает, когда цена опустилась ДО цели или ниже.',
      'В момент срабатывания ставится лимитная заявка, пробивающая рынок.',
    ],
    fields: [
      { key: 'trigger_price', label: 'Цель', hint: 'цена, на которой срабатываем' },
      ...AFTER_FILL_FIELDS,
    ],
    color: '#2ecc71',
    lineStyle: 2,
    legend: 'лимитная: цель',
  },
  {
    id: 'trail_tp',
    name: 'Следящая',
    short: 'СЛЕД',
    essence: 'Идти за ценой и сработать, когда движение развернулось.',
    algorithm: [
      'Пока цена не дошла до уровня активации, заявка спит. Уровень 0 — включается сразу.',
      'После активации сторож запоминает лучшую достигнутую цену: пик для продажи, дно для покупки.',
      'Пик подтягивается за ценой и никогда не откатывается назад.',
      'Срабатывает, когда цена отошла от пика на заданный отступ в пунктах.',
      'В момент срабатывания ставится лимитная заявка, пробивающая рынок.',
    ],
    fields: [
      { key: 'trigger_price', label: 'Активация', hint: '0 — следить сразу' },
      { key: 'trail_offset', label: 'Отступ, пункты', hint: 'откат от пика, на котором срабатываем' },
      ...AFTER_FILL_FIELDS,
    ],
    color: '#ffb300',
    lineStyle: 3,
    legend: 'следящая: пик и уровень отката',
  },
  {
    id: 'on_fill',
    name: 'Зависимая',
    short: 'ЗАВИС',
    essence: 'Сработать в тот момент, когда исполнится другая заявка.',
    algorithm: [
      'Сторож следит за конкретной заявкой по её client_id.',
      'Как только та отчиталась об исполнении, ставится дочерняя заявка.',
      'Цена дочерней: указанная, либо пробивающая рынок, если не указана.',
      'Цена не зависит от уровня — здесь нет уровня срабатывания, есть событие.',
    ],
    fields: [
      { key: 'watch_client_id', label: 'Ждём исполнения', hint: 'client_id заявки, за которой следим' },
      { key: 'child_price', label: 'Цена', hint: 'пусто — по рынку' },
      ...AFTER_FILL_FIELDS,
    ],
    color: '#7aa2f7',
    lineStyle: 3,
    legend: 'зависимая: цена дочерней заявки',
  },
];

export const KIND_BY_ID: Record<Kind, KindMeta> =
  Object.fromEntries(KINDS.map((k) => [k.id, k])) as Record<Kind, KindMeta>;

/** Цвет сработавшей заявки: тот же тон, но линия сплошная и приглушённая. */
export const FIRED_COLOR = '#9aa0b4';

/** Следящая в АКТИВНОЙ фазе: рабочий уровень выхода ведут отдельным тоном,
 *  чтобы он не сливался с дремлющим уровнем активации того же цвета. */
export const TRAIL_ACTIVE_COLOR = '#b98cff';

export const STATUS_RU: Record<string, string> = {
  armed: 'взведена',
  fired: 'сработала',
  cancelled: 'отменена',
  expired: 'истёк срок',
  error: 'ошибка',
  orphaned: 'дочерняя заявка не дожила',
};

/** Условия, одинаковые для всех типов. Взяты из движка, не выдуманы. */
export const COMMON_FACTS: Array<{ label: string; text: string; warn?: boolean }> = [
  {
    label: 'Где живёт',
    text: 'Книга умных заявок лежит на сервере STL в data/smart_orders.json и переживает его перезапуск.',
  },
  {
    label: 'Кто сторожит',
    text: 'Сторож работает ВНУТРИ процесса STL и обходит книгу раз в секунду. Пока STL недоступен, ни одна умная заявка не сработает.',
    warn: true,
  },
  {
    label: 'По какой цене считается',
    text: 'По цене последней сделки. Если лента молчит — по середине стакана.',
  },
  {
    label: 'Защита от мёртвых данных',
    text: 'По котировке старше 30 секунд не срабатывает никогда.',
  },
  {
    label: 'Kill-switch',
    text: 'При включённом kill-switch заявка остаётся взведённой и не стреляет.',
  },
  {
    label: 'Чем отвечает',
    text: 'Лимитной заявкой, пробивающей рынок: отступ 0,05% от цены, но не меньше 3 шагов и не больше 0,15%. Цена всегда кратна шагу инструмента.',
  },
  {
    label: 'Проверки перед выставлением',
    text: 'Дочерняя заявка идёт тем же путём, что и ручная: мастер-флаг, ценовой коллар, лимиты на объём и число заявок за день.',
  },
  {
    label: 'Кто её видит',
    text: 'Для агента это РУЧНАЯ заявка. Роботы и сверка её не видят и никогда не трогают.',
  },
];

export function ocoFact(group: string): string {
  return group
    ? `Связка OCO «${group}»: как только сработает одна заявка группы, остальные снимаются автоматически.`
    : 'Связка OCO не задана: заявка живёт сама по себе.';
}

export function tillFact(goodTillMs: number): string {
  if (!goodTillMs) return 'Срок не ограничен: заявка ждёт, пока не сработает или пока её не снимут.';
  return `Действует до ${fmtWhen(goodTillMs)}. После этого сама пометится «истёк срок» и стрелять не будет.`;
}

export function fmtWhen(ms: number): string {
  if (!ms) return '—';
  return new Date(ms).toLocaleString('ru-RU', {
    timeZone: 'Europe/Moscow', day: '2-digit', month: '2-digit',
    hour: '2-digit', minute: '2-digit',
  });
}

export function fmtPts(n: number): string {
  return Math.round(n).toLocaleString('ru-RU') + ' п.';
}

/** Рубли БЕЗ знака: здесь это расстояние до уровня, а не прибыль. Знак «+»
 *  читался бы как «заработаете», хотя цена может и не дойти. */
export function fmtRub(n: number): string {
  return Math.abs(Math.round(n)).toLocaleString('ru-RU') + ' ₽';
}

export interface PreviewInput {
  kind: Kind;
  side: Side;
  qty: number;
  code: string;
  trigger: number;
  trailOffset: number;
  watchId: string;
  childPrice: number;
  /** Защитная пара после сделки, пункты (0 — блок выключен). */
  slOffset?: number;
  tpOffset?: number;
  /** Текущая цена инструмента, 0 если неизвестна. */
  price: number;
  /** ₽ за пункт цены, 0/undefined — считать в пунктах. */
  pointValue?: number;
}

export interface Preview {
  /** Главное предложение: что именно произойдёт. */
  sentence: string;
  /** Пояснение расстояния до срабатывания, пусто если неприменимо. */
  distance: string;
  /** Причина, по которой взводить нельзя. Пусто — можно. */
  error: string;
}

const SIDE_RU: Record<Side, string> = { buy: 'ПОКУПКУ', sell: 'ПРОДАЖУ' };

/**
 * Фраза «что произойдёт» — человеческим языком, до нажатия кнопки.
 *
 * Направление сравнения повторяет движок: у SL продажа ждёт цену НИЖЕ уровня,
 * покупка — ВЫШЕ; у TP наоборот. Перепутать здесь стороны значит пообещать
 * оператору не то, что случится с его деньгами.
 */
export function preview(p: PreviewInput): Preview {
  const qty = Math.max(0, Math.floor(p.qty || 0));
  const what = `${SIDE_RU[p.side]} ${qty} ${plural(qty, 'контракт', 'контракта', 'контрактов')}`;
  const code = p.code || 'инструмент';
  let sentence = '';
  let distance = '';
  let error = '';

  if (!p.code) error = 'Не выбран инструмент.';
  else if (qty <= 0) error = 'Количество должно быть больше нуля.';

  if (p.kind === 'sl' || p.kind === 'tp') {
    if (!error && !(p.trigger > 0)) error = 'Укажите уровень срабатывания.';
    const down = (p.kind === 'sl' && p.side === 'sell') || (p.kind === 'tp' && p.side === 'buy');
    const verb = down ? 'опустится до' : 'поднимется до';
    sentence = `Если ${code} ${verb} ${fmtNum(p.trigger)}, сторож поставит ${what} по рынку.`;
    if (p.price > 0 && p.trigger > 0) {
      const gap = Math.abs(p.trigger - p.price);
      const wrongWay = down ? p.trigger > p.price : p.trigger < p.price;
      distance = wrongWay
        ? `Внимание: цена уже ${fmtNum(p.price)}, уровень пройден — сторож выставит заявку сразу же.`
        : `Сейчас ${fmtNum(p.price)}, до срабатывания ${fmtPts(gap)}` +
          (p.pointValue ? ` = ${fmtRub(gap * p.pointValue * qty)} хода по ${qty} ` +
            plural(qty, 'контракту', 'контрактам', 'контрактам') : '') + '.';
    }
  } else if (p.kind === 'trail_tp') {
    if (!error && !(p.trailOffset > 0)) error = 'Укажите отступ от пика в пунктах.';
    const peak = p.side === 'sell' ? 'пиком' : 'дном';
    const back = p.side === 'sell' ? 'откатится вниз' : 'отойдёт вверх';
    const act = p.trigger > 0
      ? `Сторож начнёт следить, когда ${code} дойдёт до ${fmtNum(p.trigger)}.`
      : 'Сторож начнёт следить сразу.';
    sentence = `${act} Дальше он идёт за ${peak} и поставит ${what} по рынку, ` +
      `как только цена ${back} на ${fmtPts(p.trailOffset)} от лучшей достигнутой.`;
    if (p.pointValue && p.trailOffset > 0) {
      distance = `Отступ ${fmtPts(p.trailOffset)} это ${fmtRub(p.trailOffset * p.pointValue * qty)} ` +
        `по ${qty} ${plural(qty, 'контракту', 'контрактам', 'контрактам')}.`;
    }
  } else {
    if (!error && !p.watchId) error = 'Укажите заявку, за исполнением которой следим.';
    const px = p.childPrice > 0 ? `по цене ${fmtNum(p.childPrice)}` : 'по рынку';
    sentence = `Как только исполнится заявка ${p.watchId || '—'}, сторож поставит ${what} ${px}.`;
    distance = 'Уровня цены здесь нет: заявка ждёт события, а не котировки.';
  }

  // Фраза обязана назвать ВСЕ заявки, которые уедут на биржу: после срабатывания
  // защитная пара — это ещё два ордера, и умолчать о них здесь значит обещать
  // не то, что произойдёт.
  const sl = Math.max(0, p.slOffset || 0);
  const tp = Math.max(0, p.tpOffset || 0);
  if (sl || tp) {
    const parts = [sl ? `стоп ${fmtPts(sl)}` : '', tp ? `тейк ${fmtPts(tp)}` : '']
      .filter(Boolean).join(' и ');
    sentence += ` Сразу после сделки встанут ${parts} от её цены` +
      (sl && tp ? ', в одной связке — сработает один, второй снимется.' : '.');
  }

  return { sentence, distance, error };
}

function fmtNum(n: number): string {
  return Number(n).toLocaleString('ru-RU', { maximumFractionDigits: 6 });
}

function plural(n: number, one: string, few: string, many: string): string {
  const a = Math.abs(n) % 100;
  const b = a % 10;
  if (a > 10 && a < 20) return many;
  if (b > 1 && b < 5) return few;
  if (b === 1) return one;
  return many;
}

/** Строка условия для списка взведённых заявок. */
export function conditionText(o: any): string {
  const k: Kind = o.kind;
  if (k === 'trail_tp') {
    const act = o.trigger_price > 0 ? `активация ${fmtNum(o.trigger_price)}, ` : 'следит сразу, ';
    const peak = o.activated && o.peak ? `, пик ${fmtNum(o.peak)}` : '';
    return `${act}откат ${fmtPts(o.trail_offset)}${peak}`;
  }
  if (k === 'on_fill') return `после исполнения ${String(o.watch_client_id || '—').slice(0, 18)}`;
  if (o.parent_id) {
    const what = o.kind === 'tp' ? 'тейк после входа' : 'защитный стоп';
    const down = (o.kind === 'sl' && o.side === 'sell') || (o.kind === 'tp' && o.side === 'buy');
    return `${what}: ${down ? 'цена ≤' : 'цена ≥'} ${fmtNum(o.trigger_price)}`;
  }
  const down = (k === 'sl' && o.side === 'sell') || (k === 'tp' && o.side === 'buy');
  return `${down ? 'цена ≤' : 'цена ≥'} ${fmtNum(o.trigger_price)}`;
}

/** Подсказки инструмента для формы: сначала по ЧАСТОТЕ использования в книге
 *  умных заявок (вся книга, включая отработавшие), затем — остальные коды из
 *  фида по алфавиту. Ручной ввод список не отменяет (datalist, не select). */
export function codeSuggestions(orders: Array<{ code?: string }>, feedCodes: string[]): string[] {
  const freq = new Map<string, number>();
  for (const o of orders) {
    if (o.code) freq.set(o.code, (freq.get(o.code) || 0) + 1);
  }
  const frequent = [...freq.entries()].sort((a, b) => b[1] - a[1]).map(([c]) => c);
  return [...frequent, ...feedCodes.filter((c) => c && !freq.has(c)).sort()];
}

/** Ценовые линии умной заявки на графике: что рисуем и как подписываем.
 *
 *  Жила внутри ChartFrame, но линии нужны И большому графику, И мини-графикам
 *  фрейма «Позиции и заявки» (09.08.2026: там умных заявок не было видно вовсе).
 *  Копия означала бы две разные правды об одних и тех же деньгах, поэтому
 *  функция переехала сюда — туда же, где цвета, стили и легенда.
 *
 *  Цвет по умолчанию берётся из KIND_BY_ID[kind].color; `dim` — вспомогательная
 *  линия (пик, уровень активации), она тоньше и не спорит с рабочей. */
export function smartLevels(
  o: any,
): Array<{ key: string; price: number; title: string; dim?: boolean; color?: string }> {
  // `title` НА ХОЛСТЕ БОЛЬШЕ НЕ РИСУЕТСЯ (09.08.2026). lightweight-charts
  // выводит его плашкой цвета линии поверх свечей у правого края, и семь
  // взведённых заявок закрывали собой четверть графика; ни фон, ни положение
  // плашки библиотека настраивать не даёт. Поле осталось как ЧЕЛОВЕЧЕСКОЕ ИМЯ
  // уровня — для легенды, подсказок и тестов. «5 к» = пять КОНТРАКТОВ: голое
  // число рядом с ценой читается как цена.
  const who = `${o.side === 'buy' ? '▲' : '▼'} ${o.qty} к`;
  if (o.kind === 'sl' || o.kind === 'tp') {
    return [{ key: o.so_id, price: o.trigger_price, title: who }];
  }
  if (o.kind === 'trail_tp') {
    const out: Array<{ key: string; price: number; title: string; dim?: boolean; color?: string }> = [];
    // Объём НА КАЖДОЙ линии, включая вспомогательные. Без него шесть спящих
    // следящих подписаны одинаково («СЛЕД активация») и на графике не отличить,
    // какая из них какая и на сколько контрактов (оператор, 09.08.2026).
    if (!o.activated && o.trigger_price > 0) {
      out.push({ key: o.so_id + ':act', price: o.trigger_price,
                 title: `${who} · активация`, dim: true });
    }
    if (o.activated && o.peak > 0) {
      const stop = o.side === 'sell' ? o.peak - o.trail_offset : o.peak + o.trail_offset;
      out.push({ key: o.so_id + ':stop', price: stop, color: TRAIL_ACTIVE_COLOR,
                 title: `${who} · откат ${fmtPts(o.trail_offset)}` });
      out.push({ key: o.so_id + ':peak', price: o.peak, title: `${who} · пик`, dim: true });
    }
    return out;
  }
  return o.child_price > 0
    ? [{ key: o.so_id, price: o.child_price, title: who }] : [];
}

/** Приглушённый тон для отметок ЗАЯВКИ НА ГРАФИКЕ: цвет типа подмешивается к
 *  фону графика (`mix` — доля фона) и получает прозрачность.
 *
 *  Одной прозрачности мало. lightweight-charts сам подбирает цвет текста в
 *  плашке по её яркости: на насыщенной оранжевой он ставит ТЁМНЫЙ — получается
 *  светофор поверх свечей. Притушенный к фону тон остаётся тёмным, текст на нём
 *  остаётся светлым, и вместо резких плашек выходит спокойный намёк на цвет.
 *
 *  Легенда и чипы берут ЧИСТЫЙ цвет: там перекрывать нечего, а бледная легенда
 *  просто плохо читается. */
export function softColor(hex: string, mix = 0.55, alpha = 0.8,
                          bg = '#0f0f1e'): string {
  const parse = (v: string) => {
    const m = /^#?([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(v.trim());
    return m ? m.slice(1).map((h) => parseInt(h, 16)) : null;
  };
  const c = parse(hex);
  const b = parse(bg);
  if (!c || !b) return hex;
  const k = Math.min(1, Math.max(0, mix));
  const [r, g, bl] = c.map((v, i) => Math.round(v * (1 - k) + b[i] * k));
  return `rgba(${r}, ${g}, ${bl}, ${alpha})`;
}

/** Цвет текста в плашках заявок: всегда мягкий светлый, независимо от типа.
 *  Автоподбор библиотеки на светлом фоне даёт чёрный — резкий контраст. */
export const LABEL_TEXT_COLOR = 'rgba(214, 219, 232, 0.9)';
