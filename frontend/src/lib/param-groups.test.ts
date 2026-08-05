import { describe, expect, it } from 'vitest';
import { BINARY, GROUPS, groupFields, stepFor } from './param-groups';

// Схема живого MACD trend A после перевода на macd_shectory1.
const SCHEMA = ['symbol', 'fast', 'slow', 'signal', 'qty', 'avg_max', 'avg_step_atr',
  'tp_atr', 'sl_frac', 'sl_pct', 'avg_atr_n', 'min_gap_pts', 'cooldown_min',
  'cooldown_pct', 'allow_long', 'allow_short', 'nd_days', 'gap_auto', 'k_avg']
  .map((key) => ({ key, label: key, type: key === 'symbol' ? 'text' : 'number' }));

describe('группировка параметров', () => {
  it('НЕ ТЕРЯЕТ ни одного поля: невидимый параметр продолжает влиять на живого робота', () => {
    const shown = groupFields(SCHEMA).flatMap((g) => g.fields.map((f) => f.key));
    expect(shown.sort()).toEqual(SCHEMA.map((f) => f.key).sort());
  });

  it('не показывает поле дважды', () => {
    const shown = groupFields(SCHEMA).flatMap((g) => g.fields.map((f) => f.key));
    expect(new Set(shown).size).toBe(shown.length);
  });

  it('неопознанный ключ уходит в «Прочее», а не исчезает', () => {
    const out = groupFields([...SCHEMA, { key: 'wat_42', label: 'wat', type: 'number' }]);
    const rest = out.find((g) => g.group.id === 'rest');
    expect(rest?.fields.map((f) => f.key)).toContain('wat_42');
  });

  it('группы не пересекаются между собой', () => {
    const all = GROUPS.flatMap((g) => g.keys);
    expect(new Set(all).size).toBe(all.length);
  });

  it('exit_only — переключатель: `true` в текстовом поле читается как значение для набора', () => {
    expect(BINARY.has('exit_only')).toBe(true);
    expect(BINARY.has('allow_short')).toBe(true);
  });

  it('шаг стрелки растёт с диапазоном — иначе 0→200 это сорок кликов', () => {
    expect(stepFor({ key: 'fast', label: '', min: 3, max: 60 })).toBe(1);
    expect(stepFor({ key: 'sl_frac', label: '', min: 0, max: 200 })).toBe(5);
    expect(stepFor({ key: 'min_gap_pts', label: '', min: 0, max: 1000 })).toBe(25);
  });
});
