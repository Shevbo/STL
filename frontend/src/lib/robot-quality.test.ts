// Экран качества роботов. Проверяем не вёрстку, а пять мест, где он подтолкнёт
// к неверному решению, если соврать (предупреждения real-trade 25.09.2026).
import { describe, expect, it } from 'vitest';
import { equityPoints, nowState, rfText, statCaveats, winPct } from './robot-quality';

describe('null это не ноль и не бесконечность', () => {
  it('кругов не было — прочерк, а не 0%', () => {
    expect(winPct({ robot_id: 'a', win_rate: null })).toBe('—');
    expect(winPct({ robot_id: 'a', win_rate: 0 })).toBe('0%');
    expect(winPct({ robot_id: 'a', win_rate: 0.93 })).toBe('93%');
  });

  it('просадки не было — RF прочерк, бесконечность не печатаем никогда', () => {
    expect(rfText({ robot_id: 'a', recovery_factor: null })).toBe('—');
    expect(rfText({ robot_id: 'a', recovery_factor: Infinity })).toBe('—');
    expect(rfText({ robot_id: 'a', recovery_factor: 26 })).toBe('26.00');
    expect(rfText({ robot_id: 'a', recovery_factor: -0.6 })).toBe('-0.60');
  });
});

describe('чем робот занят СЕЙЧАС', () => {
  it('лидер качества на бумаге назван бумагой', () => {
    const r = { robot_id: 'l90z0afz', mode: 'real', net_rub: 56604,
                current: { mode: 'paper', running: true, paused: false }, alive: true };
    expect(nowState(r).kind).toBe('paper');
    expect(statCaveats(r).join(' ')).toContain('НЕ в текущем режиме');
  });

  it('снятый робот назван снятым', () => {
    expect(nowState({ robot_id: 'a', alive: false }).text).toBe('снят');
    expect(nowState({ robot_id: 'a', current: null }).text).toBe('снят');
  });

  it('пауза и «не запущен» не выдаются за работу', () => {
    expect(nowState({ robot_id: 'a', current: { mode: 'real', paused: true } }).kind).toBe('off');
    expect(nowState({ robot_id: 'a', current: { mode: 'real', running: false } }).kind).toBe('off');
    expect(nowState({ robot_id: 'a', current: { mode: 'real', running: true } }).kind).toBe('real');
  });
});

describe('оговорки к цифрам робота', () => {
  it('незакрытый круг — у цифр есть невидимая часть', () => {
    const c = statCaveats({ robot_id: 'a', open_tail: 3, trades: 42 }).join(' ');
    expect(c).toContain('Незакрытый круг');
    expect(c).toContain('ЗАКРЫТЫМ');
  });

  it('RF без просадки объяснён словами, а не «бесконечно хорошо»', () => {
    expect(statCaveats({ robot_id: 'a', trades: 10, recovery_factor: null }).join(' '))
      .toContain('мерить нечем');
  });

  it('блокировка агента названа первой и не зависит от статуса робота', () => {
    const c = statCaveats({ robot_id: 'a', current: { mode: 'real', running: true } }, true);
    expect(c[0]).toContain('Агент блокирует торговлю');
  });

  it('работающий реал без хвоста и с просадкой — оговорок нет', () => {
    expect(statCaveats({ robot_id: 'a', trades: 100, open_tail: 0, recovery_factor: 2.4,
                         net_rub: 1000, current: { mode: 'real', running: true }, alive: true }))
      .toEqual([]);
  });
});

describe('кривая качества', () => {
  it('пропуск остаётся пропуском, через пустоту линию не ведём', () => {
    expect(equityPoints({ robot_id: 'a', series: [
      { key: '2026-09-25', equity_rub: 100 },
      { key: '2026-09-26', equity_rub: null },
      { key: '2026-09-27', equity_rub: 300 },
    ] })).toEqual([
      { key: '2026-09-25', v: 100 }, { key: '2026-09-26', v: null }, { key: '2026-09-27', v: 300 },
    ]);
  });
});
