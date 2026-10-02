// Раскладка строки мобильной панели: контракт, который нельзя терять.
//
// 02.10.2026 оператор прислал скриншот с iPhone: значения ВМ уезжали ЗА правый
// край экрана, подписи строились лесенкой по одному слову. Причина — одна
// флекс-линия: у флекс-детей min-width равен auto, поэтому неразрывные куски
// («Роботы 0 ср. 9 891», «Ручные −23 ср. с клиринга 85 300», «журнал +3») плюс
// число с nowrap в сумме становились шире экрана.
//
// Разметку нечем проверить вычислением: в jsdom нет раскладки. Поэтому
// проверяем САМ КОНТРАКТ — правила, возврат которых вернёт и баг.
import { describe, it, expect } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';

const M = fs.readFileSync(path.resolve('public/m.html'), 'utf8');
const css = M.slice(M.indexOf('<style>'), M.indexOf('</style>'));
const rule = (sel: string) => {
  const i = css.indexOf(sel + ' {');
  expect(i, `правило ${sel} пропало`).toBeGreaterThan(-1);
  return css.slice(i, css.indexOf('}', i));
};

describe('строка панели', () => {
  it('сетка, а не флекс: текст вправе ужаться до нуля', () => {
    const r = rule('.row');
    expect(r).toContain('display: grid');
    // minmax(0, 1fr) — это и есть право ужаться. Без нуля в минимуме колонка
    // текста не станет уже своего содержимого, и строка снова уедет за край.
    expect(r).toContain('minmax(0, 1fr)');
  });

  it('у каждого ребёнка min-width: 0 — иначе сетка повторит ошибку флекса', () => {
    expect(rule('.row > *')).toContain('min-width: 0');
  });

  it('число не переносится и стоит в своей колонке первой строки', () => {
    const v = rule('.row > .v');
    expect(v).toContain('white-space: nowrap');
    expect(v).toContain('grid-column: 2');
    expect(v).toContain('grid-row: 1');
  });

  it('уточнение занимает ВСЮ ширину: лесенки из узкой колонки больше нет', () => {
    expect(rule('.row > .sub')).toContain('grid-column: 1 / -1');
  });

  it('распорка флекс-времён погашена, а не оставлена третьей колонкой', () => {
    expect(rule('.row > .sp')).toContain('display: none');
  });

  it('тач-цель не меньше 32 px', () => {
    expect(rule('.row')).toContain('min-height: 32px');
  });

  it('скрытого переполнения нигде нет: оно прячет следующий такой же баг', () => {
    expect(css).not.toMatch(/overflow-x:\s*hidden/);
  });
});

describe('деньги', () => {
  it('разделитель тысяч и знак рубля — неразрывными пробелами', () => {
    // Второй замок поверх nowrap: число, разорванное переносом, читается как
    // другое число (оператор 01.10.2026: «−12 784 ₽» распалось на «−12»).
    const src = M.slice(M.indexOf('function rub('), M.indexOf('function px('));
    expect(src).toContain('\u00a0');
    const rub = new Function(`${src}; return rub;`)() as (v: number, o?: unknown) => string;
    expect(rub(-17866)).not.toContain(' ');     // обычных пробелов в числе нет
    expect(rub(-17866)).toContain('\u00a0₽');
  });
});
