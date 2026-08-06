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

describe('служебные поля вне схемы', () => {
  it('exit_only попадает в «Разрешения», а не теряется в «Прочем»', () => {
    // Именно этого поля не было в редакторе, поэтому правка qty его и снесла.
    const withFlag = [...SCHEMA, { key: 'exit_only', label: 'Только на выход', type: 'bool' }];
    const allow = groupFields(withFlag).find((g) => g.group.id === 'allow');
    expect(allow?.fields.map((f) => f.key)).toContain('exit_only');
  });

  it('symbol не растворяется: он текстовый и должен быть виден', () => {
    const shown = groupFields(SCHEMA).flatMap((g) => g.fields.map((f) => f.key));
    expect(shown).toContain('symbol');
  });
});

describe('счёт комбинаций сетки', () => {
  /** Та же формула, что в BacktestLab.comboCount после зажима. */
  function comboCount(schema: any[], ranges: Record<string, any>): number {
    let n = 1;
    for (const p of schema) {
      if (p.type !== 'number' || p.key === 'symbol') continue;
      const r = ranges[p.key];
      if (!r) continue;
      const from = Number(r.from), to = Number(r.to);
      const step = Math.max(1, Number(r.step) || 1);
      if (!Number.isFinite(from) || !Number.isFinite(to) || to <= from) continue;
      n *= Math.max(1, Math.floor((to - from) / step) + 1);
    }
    return n;
  }
  const S = [{ key: 'fast', type: 'number' }, { key: 'avg_max', type: 'number' }];

  it('перевёрнутая ось (до < от) НЕ даёт отрицательный множитель', () => {
    // Сеятель выставил avg_max «от 16 до 10» — монитор показывал −609 499 054 080 000.
    const n = comboCount(S, { fast: { from: 3, to: 9, step: 3 },
                              avg_max: { from: 16, to: 10, step: 1 } });
    expect(n).toBe(3);
    expect(n).toBeGreaterThan(0);
  });

  it('нулевой шаг не делит на ноль', () => {
    expect(comboCount(S, { fast: { from: 1, to: 5, step: 0 } })).toBe(5);
  });

  it('обычная сетка считается как раньше', () => {
    expect(comboCount(S, { fast: { from: 10, to: 20, step: 5 },
                           avg_max: { from: 1, to: 4, step: 1 } })).toBe(3 * 4);
  });

  it('мусор в диапазоне ось просто выключает', () => {
    expect(comboCount(S, { fast: { from: NaN, to: 9, step: 1 } })).toBe(1);
  });
});
