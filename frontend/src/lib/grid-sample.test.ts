import { describe, expect, it } from 'vitest';
import { AXIS_MAX, axisValues, comboAt, gridSize, pickCombos } from './grid-sample';

describe('grid-sample', () => {
  it('сетка меньше лимита отдаётся целиком и без дублей', () => {
    const dims = [[1, 2, 3], [10, 20]];
    const got = pickCombos(dims, 5000, false);
    expect(got).toHaveLength(6);
    expect(new Set(got.map(c => c.join(','))).size).toBe(6);
  });

  it('нумерация покрывает всю сетку', () => {
    const dims = [[1, 2], [7, 8, 9]];
    const all = Array.from({ length: gridSize(dims) }, (_, i) => comboAt(dims, i).join(','));
    expect(new Set(all).size).toBe(6);
    expect(all).toContain('2,9');
  });

  it('огромная сетка не материализуется: 1e12 комбинаций → ровно maxC за миллисекунды', () => {
    const dims = [
      Array.from({ length: 1000 }, (_, i) => i),
      Array.from({ length: 1000 }, (_, i) => i),
      Array.from({ length: 1000 }, (_, i) => i),
      Array.from({ length: 1000 }, (_, i) => i),
    ];
    expect(gridSize(dims)).toBe(1e12);
    for (const shuffle of [true, false]) {
      const got = pickCombos(dims, 5000, shuffle);
      expect(got).toHaveLength(5000);
      expect(new Set(got.map(c => c.join(','))).size).toBe(5000);
      expect(got.every(c => c.length === 4)).toBe(true);
    }
  });

  it('ось «до меньше от» даёт одно значение, а не пустоту, обнуляющую сетку', () => {
    const { vals } = axisValues(16, 10, 1, 12);
    expect(vals).toEqual([12]);
    expect(gridSize([vals, [1, 2, 3]])).toBe(3);
  });

  it('нулевой шаг и мусор в полях не роняют ось', () => {
    expect(axisValues(1, 10, 0, 4).vals).toEqual([4]);
    expect(axisValues('', '', '', 4).vals).toEqual([4]);
    expect(axisValues(1, Infinity, 1, 4).vals).toEqual([4]);
  });

  it('слишком мелкий шаг зажимается по AXIS_MAX и сообщает об этом', () => {
    const { vals, clamped } = axisValues(0, 1e9, 1, 0);
    expect(clamped).toBe(true);
    expect(vals).toHaveLength(AXIS_MAX);
  });
});
