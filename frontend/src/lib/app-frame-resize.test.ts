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

  it('потолок считается ОТ ОКНА: фрейм не должен перерастать экран', () => {
    // Сначала было 700 (упиралось сразу), потом 1600 — и фрейм перерос окно,
    // его нижний край с ручкой уехал за границу, высоту стало нельзя менять
    // ничем. Число в потолке — это та же ошибка на новом значении.
    expect(src).not.toMatch(/const LAB_MAX = \d+/);
    expect(src).toMatch(/window\.innerHeight - LAB_RESERVE/);
    // оба обработчика берут ОДИН потолок: разные дали бы скачок размера
    expect(src.match(/clamp\(dragStart\.val [-+] dy, 120, labMax\(\)\)/g)).toHaveLength(2);
  });

  it('уменьшили окно — фрейм ужимается сам', () => {
    expect(src).toMatch(/window\.addEventListener\('resize', fit\)/);
    expect(src).toMatch(/labH = Math\.min\(labH, labMax\(\)\)/);
  });

  it('размер сохраняется — иначе фрейм схлопнется при следующем открытии', () => {
    expect(src).toMatch(/saveSizes\(\{[^}]*labH/);
  });
});
