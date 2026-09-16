// Экспирация: границы, на которых экран может подтолкнуть оператора к ошибке в день
// экспирации на живом счёте (docs/design/expiry-roll.md).
import { describe, expect, it } from 'vitest';

import { canStart, confirmMatches, leftToDeadline, robotsCsv, rollSummary, stateLabel, stateTone } from './expiry-help';

describe('старт кампании', () => {
  it('пустой чекап старт не разрешает: «не проверяли» это не «всё хорошо»', () => {
    expect(canStart([])).toBe(false);
    expect(canStart(null)).toBe(false);
  });

  it('красная блокирующая проверка держит старт, предупреждение — нет', () => {
    expect(canStart([{ key: 'wl', ok: true, text: '' }, { key: 'so', ok: false, text: 'умные заявки' }])).toBe(false);
    expect(canStart([{ key: 'wl', ok: true, text: '' },
                     { key: 'hint', ok: false, text: 'справочно', blocking: false }])).toBe(true);
  });
});

describe('состояния роботов', () => {
  it('застрявшие состояния красные, переключённый зелёный', () => {
    expect(stateTone('needs_operator')).toBe('bad');
    expect(stateTone('refused')).toBe('bad');
    expect(stateTone('reset_needed')).toBe('bad');
    expect(stateTone('switched')).toBe('ok');
    expect(stateLabel('exit_wait')).toBe('ждёт выхода');
  });

  it('неизвестное состояние показываем как есть, а не прячем', () => {
    expect(stateLabel('какое-то_новое')).toBe('какое-то_новое');
    expect(stateTone('какое-то_новое')).toBe('off');
  });

  it('сводка не считает пропущенных и отдельно считает застрявших', () => {
    expect(rollSummary([
      { state: 'switched' }, { state: 'switched' }, { state: 'exit_wait' },
      { state: 'needs_operator' }, { state: 'skipped' },
    ])).toEqual({ total: 4, done: 2, stuck: 1 });
  });
});

describe('дедлайн выхода', () => {
  const now = Date.UTC(2026, 8, 17, 10, 0, 0);
  it('часы и минуты, а после срока — прямая формулировка', () => {
    expect(leftToDeadline(now + 72 * 60_000, now)).toBe('1 ч 12 мин');
    expect(leftToDeadline(now + 40 * 60_000, now)).toBe('40 мин');
    expect(leftToDeadline(now - 1, now)).toBe('срок вышел');
  });
  it('срока нет — не выдумываем', () => {
    expect(leftToDeadline(null, now)).toBeNull();
    expect(leftToDeadline(0, now)).toBeNull();
  });
});

describe('перекладка ручной позиции', () => {
  it('подтверждение только точным кодом нового контракта', () => {
    expect(confirmMatches('riz6', 'RIZ6')).toBe(true);
    expect(confirmMatches(' RIZ6 ', 'RIZ6')).toBe(true);
    expect(confirmMatches('RIU6', 'RIZ6')).toBe(false);   // код СТАРОГО не проходит
    expect(confirmMatches('', 'RIZ6')).toBe(false);
    expect(confirmMatches('RIZ6', '')).toBe(false);
  });
});

describe('CSV роботов', () => {
  it('в выгрузке те же поля и та же подпись состояния, что на экране', () => {
    expect(robotsCsv([{ robot_id: 'r1', name: 'fvg', mode: 'real', state: 'exit_wait',
                        position: -2, working_orders: 1, bars_count: 900, paused: false }])[0])
      .toEqual({ robot_id: 'r1', name: 'fvg', mode: 'real', state: 'exit_wait',
                 state_ru: 'ждёт выхода', position: -2, working_orders: 1, bars_count: 900,
                 paused: 0, note: '' });
  });
});
