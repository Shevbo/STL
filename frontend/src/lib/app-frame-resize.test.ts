// Высота нижних фреймов (ORDERS, таблицы QUIK, кривая) тянется мышкой за КРАЙ
// ФРЕЙМА. Раньше ручка существовала только у панели LAB и только сверху: у
// фреймов QUIK её не было вовсе, а высоту они делят с LAB — то есть менять её
// было нечем. Оператор: «встаёшь на линию под легендой, тянешь вниз, и высота
// графика растёт» (10.08.2026).
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

const src = readFileSync(resolve(process.cwd(), 'src/App.svelte'), 'utf8');

describe('высота нижних фреймов', () => {
  it('у каждого фрейма QUIK есть своя ручка под ним', () => {
    const handles = src.match(/onPointerDown\('labBottom'/g) ?? [];
    expect(handles.length, 'ORDERS, таблицы и кривая').toBe(3);
  });

  it('тянешь ВНИЗ — растёт: знак дельты противоположен верхней ручке', () => {
    expect(src).toMatch(/case 'labBottom': labH = clamp\(dragStart\.val \+ dy/);
    expect(src).toMatch(/case 'lab': labH = clamp\(dragStart\.val - dy/);
  });

  it('потолок общий и не 700: на большом экране в него упирались сразу', () => {
    expect(src).toMatch(/const LAB_MAX = \d{4}/);
    const max = Number(src.match(/const LAB_MAX = (\d+)/)![1]);
    expect(max).toBeGreaterThanOrEqual(1200);
    // оба обработчика берут ОДИН потолок: разные привели бы к скачку размера
    expect(src.match(/clamp\(dragStart\.val [-+] dy, 120, LAB_MAX\)/g)).toHaveLength(2);
  });

  it('размер сохраняется — иначе фрейм схлопнется при следующем открытии', () => {
    expect(src).toMatch(/saveSizes\(\{[^}]*labH/);
  });
});
