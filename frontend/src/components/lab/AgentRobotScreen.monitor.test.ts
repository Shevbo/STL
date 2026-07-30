import { describe, it, expect } from 'vitest';
import {
  parseLog, appendLog, stickToBottom, stateTransitions, type LogLine, type RobotSnap,
} from './AgentRobotScreen.svelte';

const L = (t: number, text = 'x'): LogLine => ({ t, kind: 'ok', text });

describe('монитор: лента из localStorage', () => {
  it('битый JSON и мусор дают пустую ленту, а не падение', () => {
    expect(parseLog(null)).toEqual([]);
    expect(parseLog('')).toEqual([]);
    expect(parseLog('{не json')).toEqual([]);
    expect(parseLog('{"a":1}')).toEqual([]);          // объект вместо массива
    expect(parseLog('[1,2,"строка"]')).toEqual([]);   // элементы не похожи на строки лога
  });

  it('хвост обрезается по лимиту, порядок сохраняется', () => {
    const many = Array.from({ length: 1200 }, (_, i) => L(i));
    const got = parseLog(JSON.stringify(many), 1000);
    expect(got).toHaveLength(1000);
    expect(got[0].t).toBe(200);
    expect(got[999].t).toBe(1199);
  });

  it('лимит по умолчанию — 1000 строк (заказ оператора)', () => {
    const many = Array.from({ length: 1500 }, (_, i) => L(i));
    expect(parseLog(JSON.stringify(many))).toHaveLength(1000);
    expect(appendLog(Array.from({ length: 1000 }, (_, i) => L(i)), L(9999))).toHaveLength(1000);
  });

  it('appendLog не даёт ленте расти бесконечно', () => {
    let lines: LogLine[] = Array.from({ length: 1000 }, (_, i) => L(i));
    lines = appendLog(lines, L(9999, 'новая'), 1000);
    expect(lines).toHaveLength(1000);
    expect(lines[999].text).toBe('новая');
    expect(lines[0].t).toBe(1);                        // самая старая ушла
  });
});

describe('монитор: прилипание к низу', () => {
  it('у самого низа прилипает', () => {
    expect(stickToBottom(1000, 900, 100)).toBe(true);  // ровно в низу
    expect(stickToBottom(1000, 890, 100)).toBe(true);  // 10px запаса
  });
  it('отскроллил вверх — не дёргаем', () => {
    expect(stickToBottom(1000, 500, 100)).toBe(false);
    expect(stickToBottom(1000, 860, 100)).toBe(false); // 40px выше низа
  });
});

describe('монитор: переходы состояния', () => {
  const snap = (o: Partial<RobotSnap> = {}): RobotSnap =>
    ({ mode: 'РЕАЛ', run: 'РАБОТАЕТ', eo: false, ...o });

  it('первый снимок молчит: это не переход', () => {
    expect(stateTransitions(null, snap())).toEqual([]);
  });

  it('без изменений — ни строки', () => {
    expect(stateTransitions(snap(), snap())).toEqual([]);
  });

  it('смена режима называет ОБА состояния (иначе не читается)', () => {
    expect(stateTransitions(snap(), snap({ mode: 'PAPER' })))
      .toEqual(['Режим робота: РЕАЛ → PAPER.']);
  });

  it('пауза и «только на выход» — отдельные строки, обе видны сразу', () => {
    const got = stateTransitions(snap(), snap({ run: 'ПАУЗА', eo: true }));
    expect(got).toEqual([
      'Состояние: РАБОТАЕТ → ПАУЗА.',
      'Робот перешёл в «только на выход».',
    ]);
  });

  it('снятие режима «только на выход» пишется явно', () => {
    expect(stateTransitions(snap({ eo: true }), snap({ eo: false })))
      .toEqual(['Робот вернулся в обычный режим.']);
  });
});
