// План робота на общем графике: рисуем настоящие ценовые триггеры и НЕ рисуем
// «взведён» — тот срабатывает по паттерну на закрытии бара, а не по касанию
// уровня, и линией уже вводил оператора в заблуждение (экран робота, 09.08.2026).
import { describe, expect, it } from 'vitest';
import { robotCodes, robotPlanLines } from './robot-plan';

const robot = (over: any = {}) => ({
  robot_id: 'r1', display_name: 'MACD RIZ6', symbol: 'RIZ6', position: 0,
  working_orders: [], signal_json: '{}', ...over,
});

describe('уровни плана робота', () => {
  it('заявки в стакане рисуются сплошной линией с направлением', () => {
    const [l] = robotPlanLines([robot({
      working_orders: [{ order_id: 'a', side: 'SIDE_SELL', price: 86300, qty: 2 }],
    })], 'RIZ6');
    expect(l.price).toBe(86300);
    expect(l.title).toContain('▼');
    expect(l.title).toContain('MACD RIZ6');
    expect(l.style).toBe(0);
  });

  it('планируемые заявки называют причину и рисуются пунктиром', () => {
    const [l] = robotPlanLines([robot({
      signal_json: JSON.stringify({ planned_orders: [{ side: 'buy', price: 85000, reason: 'усреднение' }] }),
    })], 'RIZ6');
    expect(l.title).toContain('усреднение');
    expect(l.style).toBe(2);
  });

  it('точные уровни выхода стратегии: тейк и стоп', () => {
    const ls = robotPlanLines([robot({
      position: 2, signal_json: JSON.stringify({ exit_levels: { tp: 86500, sl: 85100, dir: 1 } }),
    })], 'RIZ6');
    expect(ls.map((x) => x.price).sort()).toEqual([85100, 86500]);
    expect(ls.every((x) => x.title.includes('▼'))).toBe(true);   // лонг закрывают продажей
  });

  it('«взведён» линией НЕ рисуется: это паттерн, а не уровень', () => {
    expect(robotPlanLines([robot({
      signal_json: JSON.stringify({ armed: [{ side: 'buy', price: 85000, reason: 'FVG' }] }),
    })], 'RIZ6')).toEqual([]);
  });

  it('чужой инструмент и нулевые цены не попадают на график', () => {
    expect(robotPlanLines([robot({ working_orders: [{ price: 86300 }] })], 'GZZ6')).toEqual([]);
    expect(robotPlanLines([robot({ working_orders: [{ price: 0 }] })], 'RIZ6')).toEqual([]);
    expect(robotPlanLines([robot({
      signal_json: JSON.stringify({ exit_levels: { tp: 86500, sl: 0, dir: 1 } }),
    })], 'RIZ6')).toEqual([]);   // половина уровней — не уровни выхода
  });

  it('у робота на паузе уровни показываются тише, но показываются', () => {
    const [l] = robotPlanLines([robot({
      paused: true, working_orders: [{ price: 86300, side: 'buy' }],
    })], 'RIZ6');
    expect(l.dim).toBe(true);
  });

  it('инструменты роботов: с позицией, с заявками и с планом', () => {
    expect(robotCodes([
      robot({ robot_id: 'a', symbol: 'RIZ6', position: 3 }),
      robot({ robot_id: 'b', symbol: 'GZZ6', working_orders: [{ price: 1 }] }),
      robot({ robot_id: 'c', symbol: 'SiZ6',
              signal_json: JSON.stringify({ planned_orders: [{ price: 90000, side: 'buy' }] }) }),
      robot({ robot_id: 'd', symbol: 'BRZ6' }),                       // ничего не ждёт
    ])).toEqual(['GZZ6', 'RIZ6', 'SiZ6']);
  });
});
