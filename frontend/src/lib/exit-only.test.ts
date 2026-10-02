// Режим «только на выход»: кнопка, фраза состояния и причина тишины.
//
// Проверяем ПРАВИЛО, а не перечень видов: перечень фигур на этом экране уже
// дважды отставал от движка и прятал новые заявки молча.
import { describe, it, expect } from 'vitest';
import { canExitOnly, exitOnlyFact, exitOnlyHeld, ownPosition } from './smart-order-help';

describe('кому положена кнопка', () => {
  it('фигурам со своей позицией — да', () => {
    for (const k of ['grid', 'corridor', 'triangle']) expect(canExitOnly(k)).toBe(true);
  });

  it('защитным — нет: они и так только закрывают, движок отвечает им 422', () => {
    for (const k of ['sl', 'tp', 'trail_tp', 'trail_sl', 'on_fill']) {
      expect(canExitOnly(k)).toBe(false);
    }
  });

  it('НЕЗНАКОМЫЙ вид получает кнопку, а не исчезает', () => {
    // Новая фигура появится в движке раньше, чем в этом файле. Лишняя кнопка
    // объяснится ответом 422; спрятанная не объяснится ничем.
    expect(canExitOnly('spiral')).toBe(true);
    expect(canExitOnly('')).toBe(false);
    expect(canExitOnly(undefined)).toBe(false);
  });
});

describe('позиция и средняя: имена поля разные, смысл один', () => {
  it('сетка читается из g_*, фигура из c_*', () => {
    expect(ownPosition({ g_pos: -3, g_avg: 83510 })).toEqual({ pos: -3, avg: 83510 });
    expect(ownPosition({ c_pos: 2, c_avg: 410.5 })).toEqual({ pos: 2, avg: 410.5 });
    expect(ownPosition({})).toEqual({ pos: 0, avg: 0 });
  });
});

describe('фраза состояния', () => {
  it('выключенный режим не говорит ничего', () => {
    expect(exitOnlyFact({ exit_only: false, g_pos: 5, g_avg: 100 })).toBe('');
  });

  it('лонг закрывается продажей не ниже средней', () => {
    const t = exitOnlyFact({ exit_only: true, kind: 'grid', g_pos: 4, g_avg: 83510 });
    expect(t).toContain('лонг 4');
    expect(t).toContain('продажей');
    expect(t).toContain('не ниже');
  });

  it('шорт закрывается покупкой не выше средней', () => {
    const t = exitOnlyFact({ exit_only: true, kind: 'corridor', c_pos: -2, c_avg: 410 });
    expect(t).toContain('шорт 2');
    expect(t).toContain('покупкой');
    expect(t).toContain('не выше');
  });

  it('КОМИССИЯ названа: равенство средней это ноль ДО сборов', () => {
    expect(exitOnlyFact({ exit_only: true, g_pos: 1, g_avg: 100 })).toMatch(/комисси/i);
  });

  it('позиции нет — говорим это, а не молчим', () => {
    const t = exitOnlyFact({ exit_only: true, kind: 'grid', g_pos: 0, g_avg: 0 });
    expect(t).toContain('позиции нет');
    expect(t).not.toMatch(/комисси/i);   // сравнивать не с чем, оговорка лишняя
  });

  it('средней нет — цену не выдумываем', () => {
    const t = exitOnlyFact({ exit_only: true, g_pos: 3, g_avg: 0 });
    expect(t).toContain('лонг 3');
    expect(t).not.toMatch(/средней\s+[\d.,]/);   // числа, которого не знаем, в тексте нет
  });
});

describe('причина тишины из журнала', () => {
  const rows = [
    // новые сверху, как отдаёт журнал
    { ts_ms: 300, event: 'held', so_id: 'A', detail: 'только на выход: уровень +2 не выставлен — цена 83600 хуже средней 83510: закрытие здесь дало бы убыток' },
    { ts_ms: 290, event: 'held', so_id: 'A', detail: 'только на выход: уровень -1 не выставлен — позиции нет, открывать нечего' },
    { ts_ms: 280, event: 'held', so_id: 'B', detail: 'уровень +1 не выставлен: биржа не торгует (расписание: закрыто)' },
    { ts_ms: 270, event: 'corridor', so_id: 'A', detail: 'ход фигуры' },
    { ts_ms: 260, event: 'held', so_id: 'C', detail: 'только на выход: позиции нет, открывать нечего' },
  ];

  it('берёт САМУЮ СВЕЖУЮ причину каждой заявки', () => {
    const got = exitOnlyHeld(rows);
    expect(got.A.ts_ms).toBe(300);
    expect(got.A.detail).toContain('хуже средней');
    expect(got.C.ts_ms).toBe(260);
  });

  it('чужие удержания в режим не записывает', () => {
    // «биржа не торгует» — это не про режим, и приписать это режиму значит
    // соврать о причине. У B причины режима нет.
    expect(exitOnlyHeld(rows).B).toBeUndefined();
  });

  it('пустая лента — пустой ответ, без выдуманных причин', () => {
    expect(exitOnlyHeld([])).toEqual({});
    expect(exitOnlyHeld(undefined as any)).toEqual({});
  });
});
