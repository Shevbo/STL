// Ярлык сделки (OPEN/AVG/TP/SL) и деньги обязаны считаться по ОДНОЙ позиции.
//
// Баг, который это ловит (живой MACD·RIU6, 31.07.2026): роль сделки считалась
// пересчётом хвоста ЗЕРКАЛА — а хвост обрезан 200 филлами и начинается ПОСЕРЕДИНЕ
// позиции. Первым в нём стояла продажа, пересчёт решил, что робот в ШОРТЕ, и
// разметил всё наоборот: покупки-доборы получили ярлык «SL ч.», продажи-закрытия —
// «AVG». Деньги при этом берутся из журнала и садились на противоположные строки,
// поэтому оператор видел строку со стопом и нулём рублей рядом.
//
// Журнал (algo_trades) ведёт позицию непрерывно: pos_after 3 -> 2 -> 1 -> 0 у лонга.
// Ниже — реальные сделки робота за 18:17-19:02 МСК 31.07.2026.
import { describe, expect, it } from 'vitest';
import { fromLastFlat, groupByOrder, sortLedger, tradeEvents } from './lab-analytics';

// Как журнал приходит из API: новыми вперёд, и у QUIK время с точностью до СЕКУНДЫ,
// поэтому три исполнения одной заявки делят один ts_ms. Реальные строки робота за
// 21:25:06 МСК 31.07.2026 (продажа 4 контрактов тремя сделками, лонг 4 -> 0).
// Окно журнала ОБРЕЗАНО лимитом (limit=1000), поэтому первая строка — середина
// позиции: доказуемый ноль ищется поиском, и ложный ноль внутри миллисекунды
// уводит точку отсчёта. Порядок строк — ровно как отдаёт API (новые вперёд).
const API_ORDER = [
  { ts_ms: 1785511506000, seq: 10702253, side: 'sell', qty: 1, price: 88530, pos_after: 0, order_num: '157449' },
  { ts_ms: 1785511506000, seq: 10702252, side: 'sell', qty: 3, price: 88530, pos_after: 1, order_num: '157449' },
  { ts_ms: 1785511506000, seq: 10702251, side: 'sell', qty: 2, price: 88530, pos_after: 4, order_num: '157449' },
  { ts_ms: 1785511200000, seq: 10702100, side: 'buy', qty: 2, price: 88300, pos_after: 6, order_num: '131265' },
];
// Дальше робот заново открывает ЛОНГ и закрывает его — то, что должно попасть в окно.
const AFTER = [
  { ts_ms: 1785511800000, seq: 10702300, side: 'buy', qty: 4, price: 88620, pos_after: 4, order_num: '168845' },
  { ts_ms: 1785512400000, seq: 10702400, side: 'sell', qty: 4, price: 88660, pos_after: 0, order_num: '177413' },
];

describe('хронология журнала внутри одной метки времени', () => {
  it('сортировка только по времени оставляет исполнения заявки в обратном порядке', () => {
    const byTimeOnly = [...API_ORDER, ...AFTER].sort((a, b) => Number(a.ts_ms) - Number(b.ts_ms));
    expect(byTimeOnly.map((r) => r.pos_after)).toEqual([6, 0, 1, 4, 4, 0]);  // ложный ноль вторым
    // История режется по нему и начинается с ПРОДАЖИ. Позиция при этом остаётся
    // верной только потому, что tradeEvents доверяет pos_after журнала (второй
    // рубеж); без него ровно здесь и рождался «шорт» — см. блок про дыры.
    const cut = fromLastFlat(byTimeOnly as any) as any[];
    expect(cut[0].side).toBe('sell');
    const blind = cut.map(({ pos_after, ...r }: any) => r);
    expect(tradeEvents(groupByOrder(blind), 60, 1, 'RIU6', true)[0].posAfter).toBeLessThan(0);
  });

  it('sortLedger восстанавливает 6 -> 4 -> 1 -> 0 и разбор начинается с покупки', () => {
    const ok = sortLedger([...API_ORDER, ...AFTER]);
    expect(ok.map((r) => r.pos_after)).toEqual([6, 4, 1, 0, 4, 0]);
    const evs = tradeEvents(groupByOrder(fromLastFlat(ok as any) as any), 60, 1, 'RIU6', true);
    expect(evs.map((e) => e.kind)).toEqual(['open', 'full']);
    expect(evs[0].side).toBe('buy');           // лонг, а не «шорт»
    expect(evs[0].posAfter).toBe(4);
  });
});

const T = 1785500000;
// Журнал: строка на КАЖДУЮ сделку QUIK (ордер 157449 исполнился 2+1+1).
const LEDGER = [
  { ts_ms: (T + 0) * 1000, side: 'buy', qty: 2, price: 88300, pos_after: 2, order_num: '131265' },
  { ts_ms: (T + 60) * 1000, side: 'buy', qty: 1, price: 88380, pos_after: 3, order_num: '131266' },
  { ts_ms: (T + 480) * 1000, side: 'sell', qty: 2, price: 88530, pos_after: 1, order_num: '157449' },
  { ts_ms: (T + 480) * 1000, side: 'sell', qty: 1, price: 88530, pos_after: 0, order_num: '157449' },
  { ts_ms: (T + 780) * 1000, side: 'buy', qty: 2, price: 88620, pos_after: 2, order_num: '168845' },
  { ts_ms: (T + 1020) * 1000, side: 'sell', qty: 2, price: 88660, pos_after: 0, order_num: '177413' },
];

describe('позиция журнала важнее пересчёта', () => {
  // Дыра в выборке: между строками пропали сделки, набравшие 6 контрактов.
  const GAP = [
    { ts_ms: 1000, seq: 1, side: 'buy', qty: 2, price: 88000, pos_after: 2, order_num: 'a' },
    { ts_ms: 2000, seq: 2, side: 'sell', qty: 2, price: 88100, pos_after: 0, order_num: 'b' },
    // ...здесь журнал теряет набор до 6...
    { ts_ms: 9000, seq: 9, side: 'sell', qty: 2, price: 88500, pos_after: 4, order_num: 'c' },
    { ts_ms: 9600, seq: 10, side: 'sell', qty: 4, price: 88600, pos_after: 0, order_num: 'd' },
  ];

  it('после дыры позиция берётся из журнала, а не уезжает в шорт', () => {
    const evs = tradeEvents(groupByOrder(GAP as any), 60, 1, 'RIU6', true);
    expect(evs.map((e) => e.posAfter)).toEqual([2, 0, 4, 0]);
    expect(evs.map((e) => e.kind)).toEqual(['open', 'full', 'partial', 'full']);
    expect(evs.every((e) => e.posAfter >= 0)).toBe(true);   // лонг остаётся лонгом
  });

  it('без pos_after тот же поток уезжает в шорт — что и было на живом роботе', () => {
    const blind = GAP.map(({ pos_after, ...r }) => r);
    const evs = tradeEvents(groupByOrder(blind as any), 60, 1, 'RIU6', true);
    expect(evs[evs.length - 1].posAfter).toBeLessThan(0);
  });
});

describe('роль сделки берётся из журнала, а не из обрезанного хвоста зеркала', () => {
  it('у лонга покупки открывают/добирают, продажи закрывают и несут деньги', () => {
    const evs = tradeEvents(groupByOrder(fromLastFlat(LEDGER as any) as any), 60, 1, 'RIU6', true);
    expect(evs.map((e) => e.kind))
      .toEqual(['open', 'enforce', 'full', 'open', 'full']);
    // 'enforce' — добор ВЫШЕ средней (цена пошла в нашу сторону), не 'average'.
    // Деньги — только на закрытиях, и они в плюсе: продавали выше средней.
    for (const e of evs) {
      if (e.side === 'buy') expect(e.close).toBeUndefined();
      else expect(e.close!.pnl).toBeGreaterThan(0);
    }
    expect(evs.every((e) => e.posAfter >= 0)).toBe(true);
  });

  it('тот же поток без первой сделки читается как ШОРТ — почему хвост зеркала врал', () => {
    // Хвост ЗЕРКАЛА — это филлы без pos_after: позицию восстановить нечем. Обрезаем
    // посередине, первой становится продажа — и разбор уходит в несуществующий шорт.
    const tail = LEDGER.slice(2).map(({ pos_after, ...r }: any) => r);
    const evs = tradeEvents(groupByOrder(tail as any), 60, 1, 'RIU6', true);
    expect(evs[0].kind).toBe('open');
    expect(evs[0].posAfter).toBeLessThan(0);          // «шорт», которого не было
    // И покупка-добор превращается в закрытие с ярлыком стопа.
    expect(evs[1].close?.exit).toBe('CLOSE');
  });

  it('fromLastFlat отрезает историю до первого доказуемого нуля позиции', () => {
    const mid = [{ ts_ms: T * 1000, side: 'sell', qty: 1, price: 88500, pos_after: 5, order_num: 'x' },
                 ...LEDGER];
    // Режет ДО первого нуля включительно: остаётся только то, что после него.
    expect(fromLastFlat(mid as any).length).toBe(2);
    expect(fromLastFlat(LEDGER as any)).toEqual(LEDGER);   // окно и так с нуля
  });
});
