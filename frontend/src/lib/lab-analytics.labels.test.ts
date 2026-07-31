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
import { fromLastFlat, groupByOrder, tradeEvents } from './lab-analytics';

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
    // Обрезаем позицию посередине: первой становится продажа.
    const tail = LEDGER.slice(2);
    const evs = tradeEvents(groupByOrder(tail as any), 60, 1, 'RIU6', true);
    expect(evs[0].kind).toBe('open');
    expect(evs[0].posAfter).toBeLessThan(0);          // «шорт», которого не было
    // И покупка-добор превращается в закрытие с ярлыком стопа.
    expect(evs[1].close?.exit).toBe('SL');
  });

  it('fromLastFlat отрезает историю до первого доказуемого нуля позиции', () => {
    const mid = [{ ts_ms: T * 1000, side: 'sell', qty: 1, price: 88500, pos_after: 5, order_num: 'x' },
                 ...LEDGER];
    // Режет ДО первого нуля включительно: остаётся только то, что после него.
    expect(fromLastFlat(mid as any).length).toBe(2);
    expect(fromLastFlat(LEDGER as any)).toEqual(LEDGER);   // окно и так с нуля
  });
});
