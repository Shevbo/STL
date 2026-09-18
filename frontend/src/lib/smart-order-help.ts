// Умные ручные заявки: тексты, алгоритмы и единые визуальные токены.
//
// ОДИН источник правды для трёх мест: фрейма «Заявки», линий на графике и
// легенды под ним. Если цвет или формулировка живут в двух местах, они
// разъезжаются — а здесь речь о том, что оператор поймёт про свои деньги.
//
// Все формулировки сверены с движком (trader/quik/smart_orders.py). Меняется
// движок — правится и текст, иначе интерфейс начнёт обещать не то, что будет.

export type Kind = 'sl' | 'tp' | 'trail_tp' | 'on_fill' | 'trail_sl';
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
 *  выходить нечем и прибыль забрать некому. Движок разрешает их каждому ВХОДЯЩЕМУ
 *  типу (`SmartOrder.validate`); подтягивающая не входит, а выходит, и ей они
 *  запрещены — в её списке полей их нет. */
const AFTER_FILL_FIELDS = [
  { key: 'sl_offset', label: 'Стоп после сделки',
    hint: 'Задаётся ПУНКТАМИ от фактической цены входа (против позиции) ИЛИ ЦЕНОЙ уровня — одно из двух. 0 и пусто — без стопа' },
  { key: 'tp_offset', label: 'Тейк после сделки',
    hint: 'Задаётся ПУНКТАМИ от фактической цены входа (в пользу позиции) ИЛИ ЦЕНОЙ уровня — одно из двух. Со стопом они в одной связке: сработал один, второй снимется' },
  // Третий блок «после сделки» (12.08.2026). Со стопом он НЕСОВМЕСТИМ, и это не
  // вкусовщина: сработает ближний, дальний останется взведён и следующим ходом
  // откроет позицию в обратную сторону. Поэтому в форме это переключатель
  // «стоп / подтягивающая», а не два независимых поля.
  { key: 'trail_after', label: 'Подтягивающая после сделки, пункты',
    hint: 'ПУНКТЫ, не цена. 0 — выключена. Иначе после сделки стоп идёт за ценой на этом расстоянии; уровня активации у него нет, а вместе с обычным стопом он запрещён — движок вернёт 422' },
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
      { key: 'trigger_price', label: 'Уровень',
        hint: 'ЦЕНА инструмента, на которой заявка срабатывает. Не пункты: 83 430 это цена, а не расстояние' },
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
      { key: 'trigger_price', label: 'Цель',
        hint: 'ЦЕНА инструмента, на которой срабатываем. Не пункты: это уровень на графике, а не расстояние от входа' },
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
      { key: 'trigger_price', label: 'Активация',
        hint: 'ЦЕНА инструмента, с которой заявка начинает вести экстремум. 0 — следить сразу' },
      { key: 'trail_offset', label: 'Отступ, пункты',
        hint: 'ПУНКТЫ: насколько цена должна отойти от лучшей достигнутой, чтобы заявка сработала' },
      ...AFTER_FILL_FIELDS,
    ],
    color: '#ffb300',
    lineStyle: 3,
    legend: 'следящая: пик и уровень отката',
  },
  {
    // ТИП, КОТОРЫЙ ЛЕГЧЕ ВСЕГО СПУТАТЬ СО «СЛЕДЯЩЕЙ», и путать его нельзя:
    // следящая ВХОДИТ в позицию и спит до уровня активации, подтягивающая
    // ВЫХОДИТ из уже открытой и стережёт с первого тика — уровня активации у
    // неё нет вовсе (движок вернёт 422, поэтому поля в форме тоже нет).
    id: 'trail_sl',
    name: 'Подтягивающая',
    short: 'ПОДТ',
    essence: 'Стеречь УЖЕ ОТКРЫТУЮ позицию: уровень едет за ценой только в сторону уменьшения убытка.',
    algorithm: [
      'Уровня активации нет: позиция уже в рынке, поэтому сторож работает с первого тика.',
      'Сторож запоминает лучшую достигнутую цену и держит уровень на заданном отступе от неё.',
      'Уровень едет ТОЛЬКО в сторону уменьшения убытка и никогда не откатывается назад.',
      'Цена дошла до уровня — ставится лимитная заявка, пробивающая рынок, и позиция закрывается.',
      'Прежние стопы этой же позиции (тот же инструмент и та же сторона закрытия) снимаются автоматически.',
    ],
    // Блоков «после сделки» у неё НЕТ и быть не может: она ВЫХОДИТ из позиции,
    // а блоки ВХОДЯТ — разрешить их значит открыть после закрытия обратную
    // позицию. Движок отвечает 422 (smart_orders.py:135). Форма их показывала, и
    // остатки полей от прошлого типа уезжали молча (аудит 18.09.2026).
    fields: [
      { key: 'trail_offset', label: 'Отступ, пункты',
        hint: 'ПУНКТЫ: насколько ХУЖЕ лучшей достигнутой цены держать уровень выхода. Уровня активации у неё нет' },
    ],
    color: '#ff8fb1',
    lineStyle: 3,
    legend: 'подтягивающая: уровень выхода',
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
      { key: 'child_price', label: 'Цена',
        hint: 'ЦЕНА дочерней заявки. Пусто — по рынку' },
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
  // Защиту держит САМ ТЕРМИНАЛ: STL поставил нативную стоп-заявку QUIK на всю
  // связку (real-trade 17.09). Смысл статуса в том, что такая защита переживёт
  // падение STL, поэтому она не «взведена», а «под охраной терминала».
  native: 'под охраной терминала',
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
  /** Тот же блок, заданный ЦЕНОЙ уровня вместо пунктов (движок принимает и так). */
  slPrice?: number;
  tpPrice?: number;
  /** Тейк после сделки СЛЕДЯЩИЙ: откат от экстремума в пунктах (0 = фиксированный).
   *  Тогда `tpOffset` — уровень активации от цены входа, без него движок вернёт 422. */
  tpTrail?: number;
  /** Подтягивающая после сделки, пункты. Со `slOffset` несовместима. */
  trailAfter?: number;
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
  } else if (p.kind === 'trail_sl') {
    if (!error && !(p.trailOffset > 0)) error = 'Укажите отступ в пунктах.';
    const best = p.side === 'sell' ? 'максимумом' : 'минимумом';
    const worse = p.side === 'sell' ? 'ниже' : 'выше';
    sentence = `Сторож стережёт открытую позицию с первого тика: держит уровень на ` +
      `${fmtPts(p.trailOffset)} ${worse} лучшей цены, идёт за ${best} и никогда не отступает ` +
      `назад. Цена дошла до уровня — ${what} по рынку, позиция закрыта.`;
    if (p.price > 0 && p.trailOffset > 0) {
      const lvl = p.side === 'sell' ? p.price - p.trailOffset : p.price + p.trailOffset;
      distance = `От текущей ${fmtNum(p.price)} уровень выхода ${fmtNum(lvl)}` +
        (p.pointValue ? ` = ${fmtRub(p.trailOffset * p.pointValue * qty)} по ${qty} ` +
          plural(qty, 'контракту', 'контрактам', 'контрактам') : '') + '.';
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
  const tra = Math.max(0, p.trailAfter || 0);
  const tpTrail = Math.max(0, p.tpTrail || 0);
  // Блок можно задать пунктами ИЛИ ценой уровня — движок принимает оба
  // (smart_orders.py:169). Пока форма знала только пункты, тейк с активацией
  // ЦЕНОЙ считался незаполненным и намертво гасил кнопку взвода (18.09).
  const slLvl = Math.max(0, p.slPrice || 0);
  const tpLvl = Math.max(0, p.tpPrice || 0);
  const slOn = sl || slLvl, tpOn = tp || tpLvl;
  // Следящий тейк без уровня активации движок отвергает (422, real-trade 17.09):
  // непонятно, с какой прибыли начинать следить. Форма не пускает до кнопки.
  if (tpTrail && !tpOn) error = error || 'Следящий тейк: укажите уровень активации — пунктами или ценой.';
  // Стоп и подтягивающая на одной позиции — не двойная защита, а вход в рынок:
  // сработает ближний, дальний останется взведён и откроет обратную сторону.
  // Движок это запрещает (422), поэтому форма не даёт даже дойти до кнопки.
  if (slOn && tra) error = error || 'Стоп и подтягивающая вместе нельзя: оставьте что-то одно.';
  if (slOn || tpOn || tra) {
    // Смешанный случай (один блок пунктами, другой ценой) не может опереться на
    // общий хвост «от её цены»: пунктовая часть подписывает себя сама.
    const mixed = !!(slLvl || tpLvl);
    const pts = (n: number) => fmtPts(n) + (mixed ? ' от входа' : '');
    const parts = [
      sl ? `стоп ${pts(sl)}` : slLvl ? `стоп на ${fmtNum(slLvl)}` : '',
      tra ? `подтягивающая ${pts(tra)}` : '',
      tpOn ? (tpTrail
        ? `следящий тейк: активация ${tp ? 'через ' + pts(tp) : 'с ' + fmtNum(tpLvl)}, откат ${fmtPts(tpTrail)}`
        : tp ? `тейк ${pts(tp)}` : `тейк на ${fmtNum(tpLvl)}`) : '',
    ].filter(Boolean).join(' и ');
    sentence += ` Сразу после сделки встанут ${parts}` + (mixed ? '' : ' от её цены') +
      ((slOn || tra) && tpOn ? ', в одной связке — сработает один, второй снимется.' : '.');
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

/** Ключевая цена заявки для отдельной крупной колонки списка.
 *
 *  15.09.2026 оператор: направление, объём и цена тонули в строке условия мелким
 *  моноширинным шрифтом. Цена у каждого типа своя, и подпись говорит, ЧТО это за
 *  цена, иначе «86 300» у следящей (уровень активации) и у стопа (уровень
 *  срабатывания) читались бы одинаково.
 *  price = null — отдельного числа у заявки нет (следит сразу, по рынку): колонка
 *  показывает подпись без числа, а не придуманный ноль. */
/** ЖИВАЯ заявка — та, что ещё может сработать: сторож STL (armed) или нативная
 *  стоп-заявка терминала (native). Делить список по одному `armed` нельзя: заявки
 *  под охраной терминала уехали бы в историю, хотя именно они и защищают позицию. */
export const LIVE_STATUSES = new Set(['armed', 'native']);
export function isLive(status: string | null | undefined): boolean {
  return LIVE_STATUSES.has(String(status || ''));
}

export function keyPrice(o: any): { label: string; price: number | null } {
  const k: Kind = o.kind;
  if (k === 'trail_tp') {
    if (o.activated && o.peak > 0) {
      return { label: 'выход', price: o.side === 'buy' ? o.peak + o.trail_offset : o.peak - o.trail_offset };
    }
    return o.trigger_price > 0 ? { label: 'активация', price: o.trigger_price } : { label: 'следит сразу', price: null };
  }
  if (k === 'trail_sl') {
    return o.peak > 0
      ? { label: 'выход', price: o.side === 'sell' ? o.peak - o.trail_offset : o.peak + o.trail_offset }
      : { label: 'ждёт позицию', price: null };
  }
  if (k === 'on_fill') {
    return o.child_price > 0 ? { label: 'цена', price: o.child_price } : { label: 'по рынку', price: null };
  }
  const down = (k === 'sl' && o.side === 'sell') || (k === 'tp' && o.side === 'buy');
  return { label: `${k === 'tp' ? 'тейк' : 'стоп'} ${down ? '≤' : '≥'}`, price: o.trigger_price || null };
}

/** Строка условия для списка взведённых заявок. */
export function conditionText(o: any): string {
  const k: Kind = o.kind;
  if (k === 'trail_tp') {
    const act = o.trigger_price > 0 ? `активация ${fmtNum(o.trigger_price)}, ` : 'следит сразу, ';
    const peak = o.activated && o.peak ? `, пик ${fmtNum(o.peak)}` : '';
    return `${act}откат ${fmtPts(o.trail_offset)}${peak}`;
  }
  if (k === 'trail_sl') {
    // Уровня активации нет — писать «следит сразу» нечего: она всегда сразу.
    const lvl = o.peak > 0
      ? `, выход ${fmtNum(o.side === 'sell' ? o.peak - o.trail_offset : o.peak + o.trail_offset)}`
      : '';
    return `стережёт позицию, отступ ${fmtPts(o.trail_offset)}${lvl}`;
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

/** Порядок заявок на экране: СНАЧАЛА ПРОДАЖИ, потом покупки, внутри стороны —
 *  цена по убыванию (17.09.2026, просьба оператора).
 *
 *  Список, отсортированный по времени взведения, читается как журнал: чтобы
 *  понять, что стоит над рынком и что под ним, оператору приходилось складывать
 *  уровни в голове. Отсортированный по стороне и цене — читается лестницей:
 *  сверху то, что сработает при росте, снизу то, что при падении.
 *  Заявки без своей цены (зависимая «по рынку», подтягивающая без позиции)
 *  уходят в конец своей стороны: ставить их между уровнями не за что. */
export function sortBySideAndPrice<T extends { side?: string }>(list: T[] | null | undefined): T[] {
  const rank = (o: T) => (o?.side === 'buy' ? 1 : 0);          // sell выше buy
  return [...(list || [])].sort((a, b) => {
    if (rank(a) !== rank(b)) return rank(a) - rank(b);
    const pa = keyPrice(a).price, pb = keyPrice(b).price;
    if (pa == null && pb == null) return 0;
    if (pa == null) return 1;                                   // без цены — в конец
    if (pb == null) return -1;
    return pb - pa;                                             // цена по убыванию
  });
}

/** Подсказки инструмента для формы: сначала по ЧАСТОТЕ использования в книге
 *  умных заявок (вся книга, включая отработавшие), затем — остальные коды из
 *  фида по алфавиту. Ручной ввод список не отменяет (datalist, не select). */
export function codeSuggestions(orders: Array<{ code?: string }>, feedCodes: string[]): string[] {
  const freq = new Map<string, number>();
  for (const o of orders) {
    if (o.code) freq.set(o.code, (freq.get(o.code) || 0) + 1);
  }
  // Из истории берём только то, что торгуется сейчас: истёкший контракт в списке
  // выглядит как обычный выбор (18.09.2026 оператор так и взвёл заявку на RIU6).
  // ФИД - ЕДИНСТВЕННЫЙ ИСТОЧНИК «контракт существует». Фида нет - подсказок нет:
  // прежде в этом случае показывалась история, и в списке снова всплывал истёкший
  // RIU6 (оператор 18.09.2026). Мёртвый контракт в списке выглядит как обычный
  // выбор, и заявка на него ждёт цену, которой больше нет.
  const live = new Set((feedCodes || []).filter(Boolean));
  if (!live.size) return [];
  const frequent = [...freq.entries()].sort((a, b) => b[1] - a[1]).map(([c]) => c)
    .filter((c) => live.has(c));
  return [...frequent, ...[...live].filter((c) => !frequent.includes(c)).sort()];
}

/** Инструмент, подставляемый в форму при открытии.
 *
 *  Правило оператора: САМЫЙ ИСПОЛЬЗУЕМЫЙ из книги умных заявок, но ТОЛЬКО среди
 *  инструментов, которые агент отдаёт сейчас (18.09.2026: книга помнит прошлый
 *  контракт, и форма подставляла истёкший RIU6). До 18.09.2026
 *  форма ставила символ главного экрана, и оператор получал в поле GDU6 просто
 *  потому, что на графике был газ. Книга приходит асинхронно, поэтому запасные
 *  варианты по убыванию осмысленности: частый из книги -> символ экрана (если он
 *  есть в фиде) -> первый код фида -> пусто.
 */
export function defaultCode(
  orders: Array<{ code?: string }> | null | undefined,
  feedCodes: string[] | null | undefined,
  screenSymbol = '',
): string {
  const feed = (feedCodes || []).filter(Boolean);
  // ТОЛЬКО инструменты, которые агент реально отдаёт сейчас. История заявок живёт
  // дольше контракта: 18.09.2026 самым частым кодом в ней был истёкший RIU6, форма
  // подставила его, и заявка на 10 контрактов ждала цену, которой больше нет.
  // Фида нет - НЕ ПОДСТАВЛЯЕМ НИЧЕГО. Пустое поле честнее истёкшего контракта:
  // 18.09.2026 фид молчал, форма достала из истории RIU6, и оператор увидел на
  // экране мёртвый контракт как ни в чём не бывало.
  if (!feed.length) return '';
  const live = new Set(feed);
  const freq = new Map<string, number>();
  for (const o of orders || []) {
    if (o.code && live.has(o.code)) freq.set(o.code, (freq.get(o.code) || 0) + 1);
  }
  if (freq.size) {
    return [...freq.entries()].sort((a, b) => b[1] - a[1])[0][0];
  }
  const screen = (screenSymbol || '').split('@')[0];
  if (screen && feed.includes(screen)) return screen;
  return feed[0] || '';
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
  const out0 = protectiveLevels(o);
  if (o.kind === 'sl' || o.kind === 'tp') {
    return [{ key: o.so_id, price: o.trigger_price, title: who }, ...out0];
  }
  if (o.kind === 'trail_sl') {
    // Уровня активации нет по устройству типа, поэтому рисуем только рабочий
    // уровень выхода и лучшую достигнутую цену, от которой он отсчитан.
    if (!(o.peak > 0)) return out0;
    const stop = o.side === 'sell' ? o.peak - o.trail_offset : o.peak + o.trail_offset;
    return [
      { key: o.so_id + ':stop', price: stop, color: TRAIL_ACTIVE_COLOR, title: `${who} · выход` },
      { key: o.so_id + ':peak', price: o.peak, title: `${who} · лучшая`, dim: true },
      ...out0,
    ];
  }
  if (o.kind === 'trail_tp') {
    const out: Array<{ key: string; price: number; title: string; dim?: boolean; color?: string }> = [];
    // Объём НА КАЖДОЙ линии, включая вспомогательные. Без него шесть спящих
    // следящих подписаны одинаково («СЛЕД активация») и на графике не отличить,
    // какая из них какая и на сколько контрактов (оператор, 09.08.2026).
    if (!o.activated && o.trigger_price > 0) {
      out.push({ key: o.so_id + ':act', price: o.trigger_price,
                 title: `${who} · акт.`, dim: true });
    }
    if (o.activated && o.peak > 0) {
      const stop = o.side === 'sell' ? o.peak - o.trail_offset : o.peak + o.trail_offset;
      out.push({ key: o.so_id + ':stop', price: stop, color: TRAIL_ACTIVE_COLOR,
                 title: `${who} · откат` });
      out.push({ key: o.so_id + ':peak', price: o.peak, title: `${who} · пик`, dim: true });
    }
    return [...out, ...out0];
  }
  return o.child_price > 0
    ? [{ key: o.so_id, price: o.child_price, title: who }, ...out0] : [];
}

/** Двузначный код заявки для меток на графике и карточек в списке.
 *
 *  so_id — длинный идентификатор, в подпись на линии он не влезает, а без него
 *  семь взведённых заявок на графике неразличимы. Код выводится ИЗ so_id, а не
 *  раздаётся счётчиком: он не хранится нигде, одинаков во всех местах экрана и
 *  переживает перезагрузку страницы.
 *
 *  Защитные дети получают код РОДИТЕЛЯ: «47» на линии входа и «47» на её стопе
 *  говорят, что это одна связка — ровно то, зачем код и заводится.
 *
 *  Столкновения возможны: 90 кодов на всю книгу. Разводим их детерминированно —
 *  по возрастанию so_id, занятый код сдвигается на следующий свободный. Значит
 *  код меняется только у ПОЗЖЕ добавленной заявки и только при совпадении. */
export function shortCode(id: string): number {
  let h = 0;
  for (let i = 0; i < id.length; i++) h = (h * 31 + id.charCodeAt(i)) >>> 0;
  return 10 + (h % 90);
}

export function shortCodes(orders: any[]): Record<string, string> {
  const roots = new Map<string, string>();      // so_id -> корень связки
  for (const o of orders) roots.set(o.so_id, o.parent_id || o.so_id);
  const uniqueRoots = [...new Set(roots.values())].sort();
  const taken = new Set<number>();
  const byRoot = new Map<string, string>();
  for (const r of uniqueRoots) {
    let c = shortCode(r);
    for (let i = 0; i < 90 && taken.has(c); i++) c = c === 99 ? 10 : c + 1;
    taken.add(c);
    byRoot.set(r, String(c));
  }
  const out: Record<string, string> = {};
  for (const [id, root] of roots) out[id] = byRoot.get(root) ?? '';
  return out;
}

/** Цена, от которой движок будет считать защитные заявки, когда родитель
 *  сработает. Это ЕГО ЖЕ оценка: `_protective` в trader/quik/smart_orders.py
 *  берёт цену дочерней заявки в момент срабатывания, а потом пересчитывает
 *  уровни от РЕАЛЬНОЙ средней (`rebase_protective`). Значит нарисованный уровень
 *  верен с точностью до проскальзывания — поэтому он и вспомогательный. */
function entryEstimate(o: any): number {
  if (o.kind === 'on_fill') return o.child_price || 0;
  if (o.kind === 'trail_tp') {
    if (o.activated && o.peak > 0) {
      return o.side === 'sell' ? o.peak - o.trail_offset : o.peak + o.trail_offset;
    }
    return o.trigger_price || 0;
  }
  return o.trigger_price || 0;
}

/** Стоп и тейк, которые появятся ПОСЛЕ срабатывания заявки (блоки «после
 *  сделки»). Оператор их выставил, деньгами рискует по ним же — а на графике их
 *  не было вовсе, хотя уровни считаются однозначно.
 *
 *  Цвет берём у того типа, которым заявка СТАНЕТ (стоп — красный, тейк —
 *  зелёный): нарисованная сейчас проекция и реальная заявка после срабатывания
 *  обязаны выглядеть одинаково, иначе одно и то же читается как два разных.
 *  Линия вспомогательная: цена ещё не факт. */
export function protectiveLevels(
  o: any,
): Array<{ key: string; price: number; title: string; dim?: boolean; color?: string }> {
  const entry = entryEstimate(o);
  if (entry <= 0) return [];
  const out: Array<{ key: string; price: number; title: string; dim?: boolean; color?: string }> = [];
  const dir = o.side === 'buy' ? 1 : -1;      // сторона ВХОДА родителя
  // СТРЕЛКА У ЗАЩИТНОЙ ЛИНИИ — ПРОТИВОПОЛОЖНАЯ. Родитель ВХОДИТ в позицию, а
  // стоп и тейк из неё ВЫХОДЯТ: движок так и создаёт их, `exit_side = "sell" if
  // parent.side == "buy" else "buy"`. Подписать их стрелкой родителя значило бы
  // нарисовать покупку там, где уйдёт продажа. Уровень при этом считается от
  // стороны РОДИТЕЛЯ — это две разные вещи, и путать их нельзя.
  const exitArrow = o.side === 'buy' ? '▼' : '▲';
  const who = `${exitArrow} ${o.qty} к`;
  const add = (kind: 'sl' | 'tp', offset: number, word: string) => {
    if (!(offset > 0)) return;
    // Стоп против входа, тейк в сторону входа — знак ровно как в движке.
    const price = entry + (kind === 'sl' ? -1 : 1) * offset * dir;
    if (price > 0) {
      out.push({ key: `${o.so_id}:${kind}`, price, dim: true,
                 color: KIND_BY_ID[kind].color, title: `${who} · ${word}` });
    }
  };
  // Уровень, заданный ЦЕНОЙ, рисуем как есть: считать его от входа не нужно и
  // нельзя. Без этого заявка с защитой по цене оставалась на графике без линий
  // (оператор 18.09.2026, заявка 4117394fd0).
  const addLevel = (kind: 'sl' | 'tp', price: number, word: string) => {
    if (!(price > 0)) return;
    out.push({ key: `${o.so_id}:${kind}`, price, dim: true,
               color: KIND_BY_ID[kind].color, title: `${who} · ${word}` });
  };
  add('sl', Number(o.sl_offset || 0), 'стоп');
  addLevel('sl', Number(o.sl_price || 0), 'стоп');
  add('tp', Number(o.tp_offset || 0), 'тейк');
  addLevel('tp', Number(o.tp_price || 0), 'тейк');
  return out;
}

/** Легенда: что за линии сейчас на графике. ОДНА на оба графика — копия в
 *  каждом компоненте означала бы две правды об одних и тех же уровнях. */
export function smartLegend(
  orders: any[],
): Array<{ color: string; style: number; text: string }> {
  const out: Array<{ color: string; style: number; text: string }> = [];
  const seen = new Set<string>();
  for (const o of orders) {
    const m = KIND_BY_ID[o.kind];
    if (m && !seen.has(o.kind)) { seen.add(o.kind); out.push({ color: m.color, style: m.lineStyle, text: m.legend }); }
    for (const k of ['sl', 'tp'] as const) {
      const off = Number((k === 'sl' ? o.sl_offset : o.tp_offset) || 0);
      const tag = `after:${k}`;
      if (off > 0 && !seen.has(tag)) {
        seen.add(tag);
        out.push({ color: KIND_BY_ID[k].color, style: 3,
                   text: k === 'sl' ? 'стоп после входа (расчётный)' : 'тейк после входа (расчётный)' });
      }
    }
  }
  return out;
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

export interface OpenPos {
  code: string;
  /** Нетто всего счёта по инструменту (роботы включены). */
  net: number;
  /** Сколько из него держат РЕАЛЬНЫЕ роботы. */
  robots: number;
  /** Остаток — торговля оператора руками. Именно её закрывает умная заявка. */
  manual: number;
  avg: number;
}

/** Открытые позиции для формы умной заявки: что из нетто счёта РУЧНОЕ.
 *
 *  Умная заявка — МАНУАЛЬНЫЙ класс: роботы её не видят и сверка её не трогает.
 *  Поэтому подставлять в форму нетто счёта нельзя: в нём сидят позиции роботов,
 *  и закрытие «всей позиции» увело бы робота в минус контрактов у него за
 *  спиной. Ручное = нетто минус позиции РЕАЛЬНЫХ роботов (бумажные на бирже
 *  ничего не держат).
 */
export function manualPositions(status: any, mirror: any): OpenPos[] {
  const byRobot = new Map<string, number>();
  for (const r of (mirror?.robots || [])) {
    if (r?.paper === true || !r?.symbol) continue;      // бумажный ничего не держит
    const p = Number(r.position ?? 0);
    if (!Number.isFinite(p) || !p) continue;
    byRobot.set(r.symbol, (byRobot.get(r.symbol) || 0) + p);
  }
  const out: OpenPos[] = [];
  for (const p of (status?.health?.positions || [])) {
    const code = String(p?.sec || '');
    const net = Number(p?.net ?? 0);
    if (!code || !Number.isFinite(net) || !net) continue;
    const robots = byRobot.get(code) || 0;
    out.push({ code, net, robots, manual: net - robots, avg: Number(p?.avg ?? 0) || 0 });
  }
  return out.sort((a, b) => Math.abs(b.manual) - Math.abs(a.manual));
}

/** Сторона ЗАКРЫТИЯ позиции: лонг закрывают продажей, шорт — покупкой. */
export function closingSide(pos: number): Side {
  return pos > 0 ? 'sell' : 'buy';
}


/** Что встанет после сделки при ТЕКУЩЕЙ цене — строкой под полями.
 *
 *  Ошибку видно до отправки, а не в карточке взведённой заявки: 18.09.2026
 *  уровень 84700, введённый в поле пунктов, дал активацию 168210, и позиция
 *  осталась без тейка. Пункты считаются от цены входа, которая ещё неизвестна,
 *  поэтому здесь честно сказано «при входе около».
 */
export function afterFillPreview(o: {
  side: Side; price: number;
  // Поля формы: из <input type="number"> прилетает ЧИСЛО, из пустого — '' или null.
  slOffset: string | number | null; slPrice: string | number | null;
  tpOffset: string | number | null; tpPrice: string | number | null;
  tpMode: 'fixed' | 'trail'; afterMode: 'sl' | 'trail';
}): { sl: string; tp: string } {
  // Значения приходят ЧИСЛАМИ из <input type="number"> (bind:value), а не
  // строками: `.trim()` на числе падал с TypeError и рвал реактивность экрана.
  const num = (v: unknown) => parseFloat(String(v ?? '').trim()) || 0;
  const long = o.side === 'buy';
  const at = o.price ? `при входе около ${o.price.toLocaleString('ru-RU')}: ` : '';
  const out = { sl: '', tp: '' };
  if (o.afterMode === 'sl') {
    const lvl = num(o.slPrice);
    const pts = num(o.slOffset);
    if (lvl) out.sl = `стоп ровно на ${lvl.toLocaleString('ru-RU')}`;
    else if (pts && o.price) out.sl = `${at}стоп ${(long ? o.price - pts : o.price + pts).toLocaleString('ru-RU')}`;
  }
  const tlvl = num(o.tpPrice);
  const tpts = num(o.tpOffset);
  const what = o.tpMode === 'trail' ? 'тейк начнёт следить с' : 'тейк на';
  if (tlvl) out.tp = `${what} ${tlvl.toLocaleString('ru-RU')}`;
  else if (tpts && o.price) out.tp = `${at}${what} ${(long ? o.price + tpts : o.price - tpts).toLocaleString('ru-RU')}`;
  return out;
}

/** Какая ПАРА защитников получится после сделки и чем её выразит терминал.
 *
 *  Стоп и тейк — две НЕЗАВИСИМЫЕ ноги: стоп бывает фиксированным или
 *  подтягивающимся, тейк — фиксированным или следящим, и любая комбинация
 *  законна. Оператор этого на форме не видел и решил, что «стоп фиксированный
 *  + тейк следящий» не предусмотрен (18.09.2026), — теперь пара названа вслух.
 *  Факты о терминале сверены с trader/quik/native_protect.py.
 */
export function protectionPair(o: {
  afterMode: 'sl' | 'trail'; tpMode: 'fixed' | 'trail';
  hasStop: boolean; hasTake: boolean;
}): string {
  const stop = !o.hasStop ? ''
    : o.afterMode === 'trail' ? 'подтягивающийся стоп' : 'фиксированный стоп';
  const take = !o.hasTake ? '' : o.tpMode === 'trail' ? 'следящий тейк' : 'фиксированный тейк';
  if (!stop && !take) return 'Защиты после сделки нет: позиция останется без стопа и без тейка.';
  const pair = [stop, take].filter(Boolean).join(' + ');
  // Подтягивающая — единственный вид, которого у QUIK нет: он остаётся сторожу STL
  // и вместе с ним переживёт падение STL только наполовину.
  // С подтягивающей терминал не берёт НИ ОДНОЙ ноги: build_native_protection
  // возвращает None целиком (native_protect.py:54). Обещать тейку охрану
  // терминала здесь значит обещать то, чего не будет.
  if (o.afterMode === 'trail' && stop) {
    return take
      ? `${pair}. Оба остаются сторожу STL: с подтягивающей терминал не берёт и тейк.`
      : `${pair}. QUIK такого вида не умеет — стоп остаётся сторожу STL и умрёт вместе с ним.`;
  }
  if (stop && take) return `${pair}. Одна нативная запись QUIK, две ноги: сработал один — второй снимется.`;
  return `${pair}. Уйдёт под охрану терминала и переживёт падение STL.`;
}

/** Блоки «после сделки» ВЗВЕДЁННОЙ заявки, словами для карточки.
 *
 *  Карточка перечисляла только `sl_offset`/`tp_offset` и молчала про блоки,
 *  заданные ЦЕНОЙ уровня, и про подтягивающую: заявка 4117394fd0 со следящим
 *  тейком по цене выглядела как заявка без тейка (оператор 18.09.2026). Пустая
 *  строка = блоков нет, тогда карточка не рисует строку вовсе.
 */
export function afterFillFacts(o: {
  sl_offset?: number; tp_offset?: number; tp_trail?: number;
  sl_price?: number; tp_price?: number; trail_after?: number;
}): string {
  const n = (v: number | undefined) => (v && v > 0 ? v : 0);
  const sl = n(o.sl_offset), tp = n(o.tp_offset), tra = n(o.trail_after);
  const slLvl = n(o.sl_price), tpLvl = n(o.tp_price), trail = n(o.tp_trail);
  const stop = sl ? `стоп ${fmtPts(sl)} от её цены`
    : slLvl ? `стоп на ${fmtNum(slLvl)}`
    : tra ? `подтягивающийся стоп ${fmtPts(tra)} от лучшей цены` : '';
  const tpWhere = tp ? `${fmtPts(tp)} от её цены` : tpLvl ? `на ${fmtNum(tpLvl)}` : '';
  const take = !tpWhere ? ''
    : trail ? `следящий тейк: активация ${tpWhere}, откат ${fmtPts(trail)}`
    : `тейк ${tpWhere}`;
  if (!stop && !take) return '';
  return [stop, take].filter(Boolean).join(' и ')
    + (stop && take ? ', в одной связке — сработает один, второй снимется' : '');
}

/** Заявки, которыми НАБРАНА открытая позиция: тот же инструмент, сторона входа,
 *  и заявка уже сработала. Свежие сверху.
 *
 *  Нужны кнопке «Закрыть свою позицию»: закрывающая заявка должна попасть в ту
 *  же связку OCO, что и вход, иначе два выхода на одну позицию сработают оба и
 *  второй откроет обратную (оператор 18.09.2026 — связка не подставлялась вовсе).
 */
export function entryOrders<T extends {
  code: string; side: Side; status: string; fired_ms?: number; parent_id?: string;
}>(orders: T[], code: string, manual: number): T[] {
  if (!code || !manual) return [];
  const entrySide: Side = manual > 0 ? 'buy' : 'sell';   // лонг набран покупкой
  return orders
    // Только СВОИ входы: защитные дети (parent_id) позицию не набирали.
    .filter((o) => o.code === code && o.side === entrySide && !o.parent_id
                   && (o.status === 'fired' || (o.fired_ms || 0) > 0))
    .sort((a, b) => (b.fired_ms || 0) - (a.fired_ms || 0));
}

/** Имя связки, в которую встаёт выход по этой заявке: своё имя, если оно у входа
 *  уже есть, иначе его id — он уникален и читается в карточке. */
export function ocoNameOf(o: { so_id: string; oco_group?: string }): string {
  return (o.oco_group || '').trim() || o.so_id;
}

/** Одна строка таблицы `stop_orders` терминала, приведённая к виду для экрана.
 *
 *  Агент отдаёт строку QUIK КАК ЕСТЬ, своими именами полей (shectory_trade.lua:
 *  stop_row копирует весь ряд). Поэтому здесь НЕ ВЫДУМЫВАЕТСЯ смысл: известные
 *  поля берутся по точному имени, отсутствующие остаются null и рисуются
 *  прочерком, а всё прочее уходит в `rest` и показывается сырым в раскрытии.
 *  Трактовать flags/state не берёмся: их значения на нашем терминале не
 *  проверены сериями, а угаданный статус на экране защиты — худшая из лжей.
 *
 *  `byNum` — номера нативных стоп-заявок наших умных заявок: так видно, какие
 *  записи в терминале поставил STL, а какие оператор руками в QUIK.
 */
export function stopOrderRow(raw: Record<string, any>, byNum: Map<string, string> = new Map()) {
  const s = (k: string) => (raw[k] === undefined || raw[k] === null || raw[k] === '' ? null : String(raw[k]));
  const n = (k: string) => {
    const v = parseFloat(String(raw[k] ?? ''));
    return Number.isFinite(v) ? v : null;
  };
  // НОМЕР: терминал вернул его в `order_num`/`ordernum` (execution-module.md, S1),
  // а не в `stop_order_num` — по одному только последнему имени номер на экране
  // всегда был прочерком.
  const num = s('order_num') || s('ordernum') || s('stop_order_num');
  const known = new Set(['stop_order_num', 'order_num', 'ordernum', 'sec_code', 'class_code',
                         'qty', 'price', 'condition_price', 'condition_price2',
                         'stop_order_kind', 'order_date_time_ms', 'flags', 'operation',
                         'brokerref', 'balance', 'filled_qty', 'linkedorder']);
  // СОСТОЯНИЕ — по проверенным фактам, а не по догадке: docs/design/execution-module.md,
  // серии S1 и S2 на живом счёте. flags бит0 «активна», бит1 «снята»; снятая остаётся
  // в таблице до конца сессии (26), исполненная теряет бит0 и не получает бит1 (28).
  // Флагов в строке нет — состояние НЕИЗВЕСТНО, и такую строку мы не прячем.
  const flags = n('flags');
  const state = flags === null ? null
    : flags % 2 === 1 ? 'активна'
    : Math.floor(flags / 2) % 2 === 1 ? 'снята'
    : 'исполнена';
  return {
    num,
    code: s('sec_code'),
    cls: s('class_code'),
    qty: n('qty'),
    price: n('price'),
    cond: n('condition_price'),
    cond2: n('condition_price2'),
    kind: s('stop_order_kind'),
    whenMs: n('order_date_time_ms'),
    // ЧЬЯ ЭТА ЗАПИСЬ. Номер знаем не всегда, зато STL кладёт в транзакцию свою
    // метку `stl-so-<id>`, и в терминале она видна в «Комментарии». В каком
    // именно поле QUIK её отдаёт, на нашем терминале не проверено, поэтому метку
    // ИЩЕМ ПО ВСЕЙ СТРОКЕ, а не гадаем имя поля.
    flags, state,
    // НАПРАВЛЕНИЕ. Явное поле, если терминал его прислал; иначе бит2 flags — тот же
    // разряд, что у обычных заявок QUIK, и он сошёлся на двух наших опытах:
    // S1 покупка flags 25, S2 продажа flags 29 (различие ровно в бите2).
    // Ни того, ни другого нет — направление НЕИЗВЕСТНО, прочерк вместо догадки.
    dir: dirOf(raw, flags),
    balance: n('balance'),
    filled: n('filled_qty'),
    linked: s('linkedorder') === '0' ? null : s('linkedorder'),
    // Прячем только то, что ТОЧНО отработало. Неизвестное состояние — на экран.
    done: state === 'снята' || state === 'исполнена',
    ours: (num ? byNum.get(num) : null) || stlMark(raw),
    rest: Object.keys(raw).filter((k) => !known.has(k)).sort()
      .map((k) => `${k}=${raw[k]}`),
  };
}

/** Направление стоп-заявки: 'buy' | 'sell' | null (неизвестно). */
function dirOf(raw: Record<string, any>, flags: number | null): Side | null {
  const op = String(raw.operation ?? '').trim().toUpperCase();
  if (op === 'B' || op === 'BUY') return 'buy';
  if (op === 'S' || op === 'SELL') return 'sell';
  if (raw.is_sell !== undefined && raw.is_sell !== null && raw.is_sell !== '') {
    return String(raw.is_sell) === '1' || raw.is_sell === true ? 'sell' : 'buy';
  }
  if (flags === null) return null;
  return Math.floor(flags / 4) % 2 === 1 ? 'sell' : 'buy';
}

/** Метка STL в любом поле строки: транзакция уходит с комментарием `stl-so-<id>`. */
function stlMark(raw: Record<string, any>): string | null {
  for (const v of Object.values(raw)) {
    const m = String(v ?? '').match(/stl-so-([0-9a-zA-Z]+)/);
    if (m) return m[1];
  }
  return null;
}

/** Номер нативной стоп-заявки -> id нашей умной заявки, которая её поставила. */
export function nativeStopIndex(orders: Array<{ so_id: string; native_stop_num?: string }>): Map<string, string> {
  const m = new Map<string, string>();
  for (const o of orders) if (o.native_stop_num) m.set(String(o.native_stop_num), o.so_id);
  return m;
}

/** Чего ждёт запись терминала и ПОЧЕМУ у неё такие параметры.
 *
 *  Объяснение строится от НАШЕЙ книги, а не от полей QUIK: вид стоп-заявки
 *  терминал возвращает числом `stop_order_type`, и его расшифровка на нашем
 *  терминале сериями не проверена. Для чужой записи (поставлена руками в QUIK)
 *  говорим только то, что видно, и направление условия не выдумываем.
 *
 *  `price` — текущая цена инструмента (0 = неизвестна), `pointValue` — ₽ за пункт.
 */
export function stopOrderWhy(
  r: { dir: Side | null; cond: number | null; price: number | null; ours: string | null },
  so: { kind: Kind; side: Side; sl_offset?: number; tp_offset?: number; tp_trail?: number;
        sl_price?: number; tp_price?: number } | null,
  price = 0, pointValue = 0,
): { waits: string; why: string } {
  const cond = r.cond ?? 0;
  // Куда должна пойти цена. Знаем это ТОЛЬКО для своих: у стопа условие против
  // позиции, у тейка — в её пользу (trader/quik/native_protect.py).
  const isTake = !!so && (so.tp_offset || so.tp_price ? true : false) && !so.sl_offset && !so.sl_price;
  let waits = cond > 0 ? `условие ${fmtNum(cond)}` : 'условие не указано';
  if (cond > 0 && r.dir && so) {
    // Выход продажей ждёт падения (стоп) или роста (тейк); покупкой — наоборот.
    const down = r.dir === 'sell' ? !isTake : isTake;
    waits = `ждёт цену ${down ? '≤' : '≥'} ${fmtNum(cond)}`;
    if (price > 0) {
      const gap = Math.abs(cond - price);
      const passed = down ? price <= cond : price >= cond;
      waits += passed
        ? ` — цена ${fmtNum(price)}, уровень уже пройден`
        : `, сейчас ${fmtNum(price)}, до срабатывания ${fmtPts(gap)}`
          + (pointValue ? ` = ${fmtRub(gap * pointValue)} на контракт` : '');
    }
  } else if (cond > 0 && price > 0) {
    waits += `, сейчас ${fmtNum(price)} (${fmtPts(Math.abs(cond - price))} между ними)`;
  }

  if (!so) {
    return { waits, why: r.ours
      ? 'запись поставил STL, но её умной заявки в книге уже нет — параметры смотрите в терминале'
      : 'запись поставлена в терминале руками: STL её не ставил и не снимает' };
  }
  // Почему параметры именно такие — по фактам движка, а не по догадке.
  const bits: string[] = [];
  if (so.sl_offset) bits.push(`стоп в ${fmtPts(so.sl_offset)} от цены входа, против позиции`);
  if (so.sl_price) bits.push(`стоп ровно на уровне ${fmtNum(so.sl_price)}, заданном оператором`);
  if (so.tp_trail) bits.push(`тейк следящий: с уровня активации идёт за экстремумом и закрывает на откате ${fmtPts(so.tp_trail)}`);
  else if (so.tp_offset || so.tp_price) bits.push('тейк фиксированный: в терминале это тейк-профит с откатом в ОДИН шаг цены, отката 0 QUIK не принимает');
  bits.push('лимитная цена ребёнка на 2 шага ХУЖЕ уровня: иначе на быстром движении заявка не нальётся и позиция останется незакрытой');
  return { waits, why: bits.join('; ') };
}
