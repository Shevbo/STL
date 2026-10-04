// Витрина кампаний: логика, где легко соврать.
//
// Заказ оператора 04.10.2026. График «как в TSLab»: выше нуля мягкий зелёный,
// ниже мягкий красный, смена СТРОГО на y=0. Проверяется не «что-то нарисовалось»,
// а инвариант: ни один зелёный кусок не лежит под нулём и наоборот.
import { describe, it, expect } from 'vitest';
import {
  BASE_PATH, NO_FILTERS, campaignPath, chainOf, curveGeometry, fmtPnl, niceTicks,
  revisionOf, routeOf, splitAtZero, statusInfo, toMs, visibleCards, type Card,
} from './campaign-showcase';

describe('маршрут', () => {
  it('витрина и отчёт по пути', () => {
    expect(routeOf('/backtest/campaigns')).toEqual({ kind: 'list' });
    expect(routeOf('/backtest/campaigns/')).toEqual({ kind: 'list' });
    expect(routeOf('/backtest/campaigns/grid-r01')).toEqual({ kind: 'campaign', slug: 'grid-r01' });
  });

  it('query-форма жива: ссылки из документов не умирают', () => {
    expect(routeOf('/', '?lab=campaigns')).toEqual({ kind: 'list' });
    expect(routeOf('/', '?lab=campaigns&c=grid-r01')).toEqual({ kind: 'campaign', slug: 'grid-r01' });
  });

  it('чужие пути и битый slug — не витрина', () => {
    // Иначе неизвестная страница открылась бы пустым отчётом, а не терминалом.
    expect(routeOf('/')).toBeNull();
    expect(routeOf('/backtest')).toBeNull();
    expect(routeOf('/backtest/campaigns/..%2f..')).toBeNull();
    expect(routeOf('/backtest/campaigns/A_B')).toBeNull();
    expect(routeOf('/backtest/campaigns/%E0%A4%A')).toBeNull();     // битая кодировка
    expect(routeOf('/', '?lab=campaigns&c=Bad_Slug')).toBeNull();
    expect(routeOf('/', '?lab=backtest')).toBeNull();
  });

  it('путь карточки собирается обратно в тот же маршрут', () => {
    const p = campaignPath('grid-radiation-r02');
    expect(p.startsWith(BASE_PATH + '/')).toBe(true);
    expect(routeOf(p)).toEqual({ kind: 'campaign', slug: 'grid-radiation-r02' });
  });
});

describe('время и единицы', () => {
  it('секунды и миллисекунды различаются по величине', () => {
    expect(toMs(1_790_000_000)).toBe(1_790_000_000_000);
    expect(toMs(1_790_000_000_000)).toBe(1_790_000_000_000);
  });

  it('нет числа — прочерк, а не ноль', () => {
    expect(fmtPnl(null, 'rub')).toBe('—');
    expect(fmtPnl(undefined, 'rub')).toBe('—');
    expect(fmtPnl(NaN, 'rub')).toBe('—');
    expect(fmtPnl(0, 'rub', true)).toContain('0');      // а честный ноль — число
  });

  it('единица подписана, знак явный, пробелы неразрывные', () => {
    expect(fmtPnl(-1_234_567, 'rub')).toBe('−1 234 567 ₽');
    expect(fmtPnl(500, 'points')).toBe('+500 п.');
    expect(fmtPnl(12.34, 'pct')).toBe('+12,3 %');
    expect(fmtPnl(-5, 'rub')).not.toContain(' ');
  });

  it('незнакомая единица печатается как есть, а не угадывается', () => {
    expect(fmtPnl(10, 'usd')).toBe('+10 usd');
  });
});

describe('статусы', () => {
  it('известные статусы человеческими словами', () => {
    expect(statusInfo({ status: 'done' }).label).toBe('готово');
    expect(statusInfo({ status: 'queued' }).label).toBe('ожидает прогона');
    expect(statusInfo({ status: 'no_curve' }).label).toBe('кривой нет');
    expect(statusInfo({ status: 'running', progress: { finished: 120, total: 620 } }).label)
      .toBe('идёт 120/620');
  });

  it('идёт без прогресса — без выдуманных 0/0', () => {
    expect(statusInfo({ status: 'running', progress: null }).label).toBe('идёт');
    expect(statusInfo({ status: 'running', progress: { finished: 0, total: 0 } }).label).toBe('идёт');
  });

  it('НОВЫЙ статус сборщика печатается кодом, а не исчезает', () => {
    // Перечень, который молча отстаёт от источника, на этом проекте уже дважды
    // прятал новые виды заявок. Правило тестируем, а не литерал.
    const s = statusInfo({ status: 'paused_by_operator' });
    expect(s.label).toBe('paused_by_operator');
    expect(s.tone).toBe('unk');
  });
});

const C = (slug: string, o: Partial<Card> = {}): Card =>
  ({ slug, title: slug, status: 'done', ...o });

describe('редакции', () => {
  const cards = [
    C('g-r1', { family: 'g', rev: 1 }), C('g-r2', { family: 'g', rev: 2 }),
    C('g-r3', { family: 'g', rev: 3 }), C('solo', { family: 's', rev: 1 }),
    C('free'),
  ];

  it('«ред. N из M» только у линии с несколькими редакциями', () => {
    const r = revisionOf(cards);
    expect(r.get('g-r2')).toEqual({ rev: 2, of: 3 });
    expect(r.get('solo')).toBeNull();     // «ред. 1 из 1» ничего не сообщает
    expect(r.get('free')).toBeNull();
  });

  it('цепочка линии по возрастанию редакции', () => {
    const shuffled = [cards[2], cards[0], cards[1]];
    expect(chainOf(shuffled, 'g').map((c) => c.rev)).toEqual([1, 2, 3]);
    expect(chainOf(cards, undefined)).toEqual([]);
  });

  it('по умолчанию свёрнуто до последней редакции линии', () => {
    const v = visibleCards(cards, NO_FILTERS).map((c) => c.slug);
    expect(v).toEqual(['g-r3', 'solo', 'free']);
  });

  it('переключатель «показать все редакции»', () => {
    expect(visibleCards(cards, { ...NO_FILTERS, allRevisions: true })).toHaveLength(5);
  });

  it('свёртка идёт ДО фильтра статуса: свежая редакция не подменяется старой', () => {
    // Если сначала отфильтровать по статусу, то при r3=queued фильтр «готово»
    // показал бы r2 — старую редакцию вместо последней. Витрина врала бы о
    // том, на чём линия остановилась.
    const c = [C('a-r1', { family: 'a', rev: 1 }),
               C('a-r2', { family: 'a', rev: 2, status: 'queued' })];
    expect(visibleCards(c, { ...NO_FILTERS, status: 'done' })).toEqual([]);
    expect(visibleCards(c, { ...NO_FILTERS, status: 'queued' }).map((x) => x.slug)).toEqual(['a-r2']);
  });
});

describe('фильтры', () => {
  const cards = [
    C('a', { title: 'Радиация: сигнал тренда', kind: 'research', symbols: ['RIZ6'] }),
    C('b', { title: 'MACD перебор', kind: 'optimizer', symbols: ['GZZ6'], status: 'running' }),
    C('c', { title: 'Прочее', idea: 'сетка вокруг цены' }),
  ];

  it('поиск по названию, идее и slug без учёта регистра', () => {
    expect(visibleCards(cards, { ...NO_FILTERS, q: 'РАДИАЦИЯ' }).map((c) => c.slug)).toEqual(['a']);
    expect(visibleCards(cards, { ...NO_FILTERS, q: 'сетка' }).map((c) => c.slug)).toEqual(['c']);
  });

  it('вид и статус', () => {
    expect(visibleCards(cards, { ...NO_FILTERS, kind: 'optimizer' }).map((c) => c.slug)).toEqual(['b']);
    expect(visibleCards(cards, { ...NO_FILTERS, status: 'running' }).map((c) => c.slug)).toEqual(['b']);
  });

  it('карточка без kind считается исследованием', () => {
    expect(visibleCards(cards, { ...NO_FILTERS, kind: 'research' }).map((c) => c.slug))
      .toEqual(['a', 'c']);
  });

  it('фильтр по инструменту: нет symbols — карточка не проходит', () => {
    expect(visibleCards(cards, { ...NO_FILTERS, symbol: 'RIZ6' }).map((c) => c.slug)).toEqual(['a']);
  });
});

describe('кривая: разрез по нулю', () => {
  it('одна сторона — один кусок', () => {
    const s = splitAtZero([[1, 5], [2, 9], [3, 2]]);
    expect(s).toHaveLength(1);
    expect(s[0].sign).toBe(1);
  });

  it('пересечение нуля режет кусок ровно на оси, по интерполяции', () => {
    // от +10 в t=0 до −10 в t=2: нуль ровно в t=1
    const s = splitAtZero([[0, 10], [2, -10]]);
    expect(s.map((x) => x.sign)).toEqual([1, -1]);
    expect(s[0].pts[s[0].pts.length - 1]).toEqual([1, 0]);
    expect(s[1].pts[0]).toEqual([1, 0]);
  });

  it('ИНВАРИАНТ: зелёный не уходит под нуль, красный не поднимается над ним', () => {
    const wave: [number, number][] = [];
    for (let i = 0; i < 60; i++) wave.push([i, Math.sin(i / 4) * 100 + (i % 7) - 3]);
    for (const seg of splitAtZero(wave)) {
      for (const [, y] of seg.pts) {
        if (seg.sign === 1) expect(y).toBeGreaterThanOrEqual(0);
        if (seg.sign === -1) expect(y).toBeLessThanOrEqual(0);
      }
    }
  });

  it('касание нуля без пересечения не рвёт кривую', () => {
    const s = splitAtZero([[0, 5], [1, 0], [2, 5]]);
    expect(s).toHaveLength(1);
  });

  it('вся кривая на нуле — один серый кусок, а не пустота', () => {
    const s = splitAtZero([[0, 0], [1, 0]]);
    expect(s).toHaveLength(1);
    expect(s[0].sign).toBe(0);
  });

  it('пусто и мусор — пусто, без падения', () => {
    expect(splitAtZero([])).toEqual([]);
    expect(splitAtZero([[NaN, 1], [1, Infinity]])).toEqual([]);
  });
});

describe('геометрия', () => {
  it('меньше двух точек — рисовать нечего: null, а не пустая рамка', () => {
    expect(curveGeometry(null, 100, 50)).toBeNull();
    expect(curveGeometry([], 100, 50)).toBeNull();
    expect(curveGeometry([[1, 5]], 100, 50)).toBeNull();
  });

  it('нулевая ось входит в диапазон даже у кривой целиком выше нуля', () => {
    // Без оси такая кривая выглядела бы «в плюсе от самого начала».
    const g = curveGeometry([[1, 100], [2, 200]], 100, 50)!;
    expect(g.ymin).toBe(0);
    expect(g.y0).toBeCloseTo(50, 1);                 // нуль внизу
    expect(g.y(200)).toBeCloseTo(0, 1);              // максимум вверху
  });

  it('плоская кривая не делит на ноль', () => {
    const g = curveGeometry([[1, 0], [2, 0]], 100, 50)!;
    expect(Number.isFinite(g.y(0))).toBe(true);
  });

  it('отступы учтены: кривая лежит внутри рамки', () => {
    const g = curveGeometry([[0, -50], [10, 50]], 200, 100, { l: 20, r: 10, t: 5, b: 15 })!;
    expect(g.x(0)).toBeCloseTo(20, 1);
    expect(g.x(10)).toBeCloseTo(190, 1);
    expect(g.y(50)).toBeCloseTo(5, 1);
    expect(g.y(-50)).toBeCloseTo(85, 1);
  });

  it('смена цвета на нуле: площадь каждого куска замыкается по линии нуля', () => {
    const g = curveGeometry([[0, 10], [2, -10]], 100, 100)!;
    expect(g.segs.map((s) => s.sign)).toEqual([1, -1]);
    for (const s of g.segs) expect(s.area).toContain(`L`);
    // нулевая линия в пикселях одна и та же у обоих кусков
    const y0 = g.y0.toFixed(1);
    expect(g.segs[0].area).toContain(`${y0} L`);
    expect(g.segs[1].area).toContain(` ${y0} Z`);
  });

  it('точки приходят секундами или миллисекундами — картинка одна', () => {
    const a = curveGeometry([[1_790_000_000, 1], [1_790_000_100, 5]], 100, 50)!;
    const b = curveGeometry([[1_790_000_000_000, 1], [1_790_000_100_000, 5]], 100, 50)!;
    expect(a.segs[0].line).toBe(b.segs[0].line);
  });
});

describe('метки оси', () => {
  it('шаг 1/2/5 и нуль среди меток', () => {
    const t = niceTicks(-120, 480, 5);
    expect(t).toContain(0);
    expect(t.every((v) => Math.abs(v) % 100 < 1e-6 || Math.abs(v) % 50 < 1e-6)).toBe(true);
  });

  it('пустой диапазон не зацикливается', () => {
    expect(niceTicks(5, 5)).toEqual([5]);
  });
});
