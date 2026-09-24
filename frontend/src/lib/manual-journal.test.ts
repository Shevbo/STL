// Экран журнала ручных заявок. Проверяем не вёрстку, а три места, где он может
// соврать про деньги (предупреждение real-trade 24.09.2026).
import { describe, expect, it } from 'vitest';
import {
  eventRu, filterRows, openTotalRub, pnlCaveats, rowCodes, unpricedPoints,
} from './manual-journal';

describe('оговорки к итогу', () => {
  it('неполный период назван неполным и с датой начала данных', () => {
    const c = pnlCaveats({ period: 'month', partial: true, coverage_from: '2026-09-23' });
    expect(c.join(' ')).toContain('2026-09-23');
    expect(c.join(' ')).toContain('месяц');
  });

  it('без ₽ за пункт итог назван НЕПОЛНЫМ и перечислены инструменты', () => {
    const c = pnlCaveats({ priced: false, by_symbol: [
      { symbol: 'RIZ6', point_value: 1.5 }, { symbol: 'spRIZ6d0921' },
    ] });
    expect(c.join(' ')).toContain('НЕПОЛНЫЙ');
    expect(c.join(' ')).toContain('spRIZ6d0921');
    expect(c.join(' ')).not.toContain('RIZ6,');   // у кого коэффициент есть — не жалуемся
  });

  it('всё в порядке — оговорок нет, цифру можно читать как есть', () => {
    expect(pnlCaveats({ partial: false, priced: true })).toEqual([]);
    expect(pnlCaveats(null)).toEqual([]);
  });
});

describe('открытая позиция считается ОТДЕЛЬНО', () => {
  it('переоценка суммируется сама по себе, а не с итогом', () => {
    expect(openTotalRub({ net_rub: 1000, open: [
      { symbol: 'RIZ6', unrealized_rub: -500 }, { symbol: 'GZZ6', unrealized_rub: 200 },
    ] })).toBe(-300);
  });

  it('позиции нет или переоценка неизвестна — не выдумываем ноль', () => {
    expect(openTotalRub({ open: [] })).toBeNull();
    expect(openTotalRub({ open: [{ symbol: 'RIZ6' }] })).toBeNull();
    expect(openTotalRub(null)).toBeNull();
  });
});

describe('инструменты без ₽ за пункт видны в пунктах', () => {
  it('выпавшие из рублёвого итога перечислены с их пунктами', () => {
    expect(unpricedPoints({ by_symbol: [
      { symbol: 'RIZ6', point_value: 1.5, realized_points: 100 },
      { symbol: 'spRIZ6d0921', realized_points: -42 },
      { symbol: 'spGZZ6', realized_points: 0 },      // нечего показывать
    ] })).toEqual([{ symbol: 'spRIZ6d0921', points: -42 }]);
  });
});

describe('лента', () => {
  const rows = [
    { ts_ms: 3, type: 'event' as const, event: 'created', source: 'оператор', code: 'RIZ6', so_id: 'a1' },
    { ts_ms: 2, type: 'trade' as const, event: 'сделка', source: 'умная заявка a1', code: 'RIZ6', side: 'buy', qty: 2 },
    { ts_ms: 1, type: 'event' as const, event: 'native_live', source: 'терминал QUIK', code: 'GZZ6' },
  ];

  it('незнакомый код события показывается КАК ЕСТЬ, а не прячется', () => {
    expect(eventRu({ event: 'created' })).toBe('взведена');
    expect(eventRu({ event: 'совершенно_новое' })).toBe('совершенно_новое');
  });

  it('фильтр по виду строки и по инструменту', () => {
    expect(filterRows(rows, { kind: 'trade' }).length).toBe(1);
    expect(filterRows(rows, { code: 'GZZ6' }).length).toBe(1);
    expect(filterRows(rows, { kind: 'all' }).length).toBe(3);
  });

  it('поиск идёт и по русскому названию события, и по источнику', () => {
    expect(filterRows(rows, { query: 'взведена' }).length).toBe(1);
    expect(filterRows(rows, { query: 'терминал' }).length).toBe(1);
    expect(filterRows(rows, { query: 'a1' }).length).toBe(2);   // событие и его сделка
  });

  it('инструменты для выпадашки — без пустых и без дублей', () => {
    expect(rowCodes(rows)).toEqual(['GZZ6', 'RIZ6']);
  });
});
