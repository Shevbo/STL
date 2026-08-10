// Таймфреймы: одна карта на все графики. Раньше их было две — полная в
// ChartFrame и урезанная копия в MiniChart, где 30м и 1ч сваливались в M15, и
// по одной и той же кнопке два экрана показывали РАЗНЫЕ свечи.
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import { TF_BUTTONS, TF_NAMES, tfName } from './chart-time';

describe('таймфреймы графиков', () => {
  it('кнопки ровно те, что просил оператор', () => {
    expect(TF_BUTTONS.map((b) => b.label)).toEqual(['1м', '5м', '15м', '30м', '1ч', '4ч', '1д']);
  });

  it('у каждой кнопки есть кадр, и он не по умолчанию', () => {
    for (const b of TF_BUTTONS) {
      expect(TF_NAMES[b.value], b.label).toBeTruthy();
      expect(tfName(b.value), b.label).toBe(TF_NAMES[b.value]);
    }
  });

  it('соседние кнопки не дают один и тот же кадр — кроме честного 30м', () => {
    // Finam REST не отдаёт M30 (проверено вживую), поэтому 30м резолвится в
    // ближайший младший M15. Это ЕДИНСТВЕННОЕ допустимое совпадение.
    const byName = new Map<string, string[]>();
    for (const b of TF_BUTTONS) {
      const n = tfName(b.value);
      byName.set(n, [...(byName.get(n) ?? []), b.label]);
    }
    const dups = [...byName.entries()].filter(([, ls]) => ls.length > 1);
    expect(dups.map(([n, ls]) => `${n}: ${ls.join(',')}`)).toEqual(['TIME_FRAME_M15: 15м,30м']);
  });

  it('неизвестный код не роняет запрос, а падает на M5', () => {
    expect(tfName(999)).toBe('TIME_FRAME_M5');
  });

  it('мини-график больше не держит свою копию карты', () => {
    const src = readFileSync(resolve(process.cwd(), 'src/components/MiniChart.svelte'), 'utf8');
    expect(src).not.toMatch(/const TF_NAMES/);
    expect(src).toMatch(/tfName\(tf\)/);
  });
});

// ── Ручка «тянуть высоту» обязана быть ВИДНА ────────────────────────────────
// Штатный Splitter прозрачен до наведения (6 px), и оператор её просто не нашёл:
// «как менять мышкой высоту графика?» — при том что функция уже работала.
describe('ручка изменения высоты графиков', () => {
  const src = readFileSync(resolve(process.cwd(), 'src/components/ChartsGrid.svelte'), 'utf8');

  it('высота графиков задаётся переменной, а не константой в CSS', () => {
    expect(src).toMatch(/--mini-h:\s*\{chartH\}px/);
    expect(src).toMatch(/height: var\(--mini-h/);
  });

  it('ручка обёрнута в видимую полосу с подсказкой', () => {
    expect(src).toMatch(/class="gf-resize"/);
    expect(src).toMatch(/title="Потяните/);
    // у полосы есть собственный фон: прозрачной, как сам Splitter, ей быть нельзя
    expect(src).toMatch(/\.gf-resize \{[^}]*background:/);
  });

  it('текущее значение подписано — иначе непонятно, что тянешь', () => {
    expect(src).toMatch(/\{chartH\} px/);
  });
});
