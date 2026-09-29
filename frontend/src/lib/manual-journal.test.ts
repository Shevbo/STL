// Экран журнала ручных заявок. Проверяем не вёрстку, а три места, где он может
// соврать про деньги (предупреждение real-trade 24.09.2026).
import { describe, expect, it } from 'vitest';
import {
  CHANNELS_WITH_EVENTS, channelRu, eventRu, filterRows, openTotalRub, pnlCaveats,
  eventTone, positionAtWindowStart, reconcile, rowCodes, unpricedPoints,
  vmManualFromStatus,
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

// 24.09.2026: экран написал «RIZ6 −4, переоценка −976 ₽», когда счёт был ПУСТ.
// `open` в отчёте — это остаток проигрывания журнала ЗА ОКНО, а не позиция счёта:
// набранное ДО начала окна в журнал окна не попадает, и остаток может быть любым.
describe('три канала', () => {
  it('имена каналов переводятся, незнакомый показывается как есть', () => {
    expect(channelRu('quik')).toBe('терминал QUIK');
    expect(channelRu('broker')).toBe('приложение брокера (FINAM)');
    expect(channelRu('smart')).toBe('умные заявки STL');
    expect(channelRu('новый_канал')).toBe('новый_канал');
  });

  it('история заявок полна только у умных заявок', () => {
    expect(CHANNELS_WITH_EVENTS.has('smart')).toBe(true);
    expect(CHANNELS_WITH_EVENTS.has('quik')).toBe(false);
    expect(CHANNELS_WITH_EVENTS.has('broker')).toBe(false);
  });
});

// Оператор: «Сумма P&L ручных сделок в компаньоне и в журнале должна совпадать».
// Сами по себе они не сойдутся — это разные величины за разные окна. Поэтому
// показываем МОСТ, и смотреть надо на необъяснённый остаток (28.09.2026).
describe('сверка панели и журнала', () => {
  it('ВМ ручных берётся из разбивки агента, роботы не считаются', () => {
    expect(vmManualFromStatus({ day: { classes: [
      { kind: 'terminal', vm_rub: 1000 },
      { kind: 'smart', vm_rub: 500 },
      { kind: 'robot', vm_rub: 99999 },
    ] } })).toBe(1500);
  });

  it('разбивки нет — не выдумываем ноль', () => {
    expect(vmManualFromStatus({})).toBeNull();
    expect(vmManualFromStatus(null)).toBeNull();
  });

  it('мост сходится: ВМ = реализовано + комиссия + переоценка', () => {
    const r = reconcile({ period: 'day', net_rub: 1000, commission_rub: -100,
                          open: [{ symbol: 'RIZ6', unrealized_rub: 200 }] }, 1300);
    expect(r!.residual).toBe(0);
  });

  it('необъяснённый остаток виден, когда он есть', () => {
    const r = reconcile({ period: 'day', net_rub: 1000, commission_rub: 0, open: [] }, 1500);
    expect(r!.residual).toBe(500);
  });

  it('разные окна названы прямо: полночь против 07:00', () => {
    const r = reconcile({ period: 'day', net_rub: 0 }, 0);
    expect(r!.notes.join(' ')).toContain('07:00');
    expect(r!.notes.join(' ')).toContain('полуночи');
  });

  it('нет одной из величин — сверки нет, а не нули', () => {
    expect(reconcile(null, 100)).toBeNull();
    expect(reconcile({ period: 'day' }, null)).toBeNull();
  });
});

// Доведение заявки до исполнения и ходы фигуры — события, появившиеся
// 29.09.2026. Разбор родного стопа на 70 контрактов занял у real-trade час
// ровно потому, что событий не было: карточка говорит, что БУДЕТ, а лента —
// что БЫЛО, и разбирать потом приходится второе.
describe('громкость событий в ленте', () => {
  it('новые коды названы словами, а не показаны кодом', () => {
    expect(eventRu({ event: 'escalated' })).toBe('доведение до исполнения');
    expect(eventRu({ event: 'corridor' })).toBe('ход фигуры');
  });

  // Единственное место во всей системе, где заявка уходит БЕЗ ЦЕНЫ.
  it('рыночная фаза выделяется сильнее прочего', () => {
    expect(eventTone({ event: 'escalated', detail: 'по рынку: лимит не налился за 20 с, остаток 40 выводим рыночной заявкой' }))
      .toBe('market');
  });

  it('фаза преследования заметна, но не чрезвычайна', () => {
    expect(eventTone({ event: 'escalated', detail: 'преследование: заявка не налилась за 10 с, переставлена на 82840' }))
      .toBe('warn');
  });

  // Текст движка может измениться. Пропустить громкое событие хуже, чем
  // подсветить лишнее, поэтому незнакомый текст остаётся заметным.
  it('незнакомый текст доведения не теряет подсветку совсем', () => {
    expect(eventTone({ event: 'escalated', detail: 'что-то новое' })).toBe('warn');
  });

  it('обычный ход фигуры не кричит', () => {
    expect(eventTone({ event: 'corridor', detail: 'продажа от верхней стенки: sell 2 по 83600' })).toBe('');
    expect(eventTone({ event: 'fired' })).toBe('');
  });

  it('незнакомый код события показывается КАК ЕСТЬ, а не прячется', () => {
    expect(eventRu({ event: 'совсем_новое' })).toBe('совсем_новое');
  });
});
