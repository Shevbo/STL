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

// Графики ТЯНУТСЯ вместе с фреймом. Раньше высота была фиксированной: фрейм
// растягивали за нижний край, а график оставался маркой в углу, и под ним зияла
// пустота («как мне растянуть график по вертикали??», 10.08.2026).
describe('графики заполняют фрейм', () => {
  const src = readFileSync(resolve(process.cwd(), 'src/components/ChartsGrid.svelte'), 'utf8');

  it('строки сетки растягиваются, а не фиксированы', () => {
    expect(src).toMatch(/grid-auto-rows: minmax\(160px, 1fr\)/);
    expect(src).toMatch(/\.gf-grid :global\(\.mini\) \{ height: 100%/);
  });

  it('фиксированной высоты и своего сплиттера внутри больше нет', () => {
    expect(src).not.toMatch(/--mini-h/);
    expect(src).not.toMatch(/Splitter/);
  });
});
