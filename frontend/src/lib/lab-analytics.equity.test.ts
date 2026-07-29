import { describe, it, expect } from 'vitest';
import { equityPaths } from './lab-analytics';

const mk = (vals: number[]) => vals.map((v, i) => ({ time: i * 60, equity: v }));

describe('equityPaths', () => {
  it('обе кривые в одной шкале: общий min/max', () => {
    const r = equityPaths(mk([0, 10]), mk([0, 100]), 100, 50)!;
    expect(r.lo).toBe(0);
    expect(r.hi).toBe(100);
    // худшая кривая НЕ дотягивает до верха бокса (y=0), иначе выглядела бы как лучшая
    const lastA = r.pa.split(' ').pop()!.split(',').map(Number);
    const lastB = r.pb.split(' ').pop()!.split(',').map(Number);
    expect(lastA[1]).toBeGreaterThan(lastB[1]);   // ниже по значению = больше y в SVG
    expect(lastB[1]).toBe(0);
  });
  it('прореживание сохраняет последнюю точку', () => {
    const long = mk(Array.from({ length: 5000 }, (_, i) => i));
    const r = equityPaths(long, long, 100, 50, 100)!;
    const pts = r.pa.split(' ');
    expect(pts.length).toBeLessThanOrEqual(102);
    expect(pts[pts.length - 1]).toBe('100.0,0.0');   // конец кривой = итог, не срезан
  });
  it('плоская кривая не делит на ноль', () => {
    const r = equityPaths(mk([5, 5, 5]), mk([5, 5, 5]), 100, 50)!;
    expect(r.pa).not.toContain('NaN');
  });
  it('пустые данные — null, а не пустой график', () => {
    expect(equityPaths([], mk([1, 2]), 10, 10)).toBeNull();
    expect(equityPaths(mk([1]), mk([1, 2]), 10, 10)).toBeNull();
  });
});
