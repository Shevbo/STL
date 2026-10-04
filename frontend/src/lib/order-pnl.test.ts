// P&L отдельной заявки в квадратных скобках (заказ оператора 01.10.2026).
// Проверяем ПОВЕДЕНИЕ помощника, вытащив его прямо со страницы: деньги считает
// сервер, панель только печатает, и важно, ЧТО именно она печатает — и чего не
// печатает, когда данных нет.
import fs from 'node:fs';
import path from 'node:path';
import { describe, it, expect } from 'vitest';

const read = (f: string) => fs.readFileSync(path.resolve('public', f), 'utf8');
const PAGES = { 'companion.html': read('companion.html'), 'm.html': read('m.html') };

function boxFn(src: string): (p: any) => string {
  const pick = (re: RegExp, what: string) => {
    const m = src.match(re);
    if (!m) throw new Error(`${what} не найдена`);
    return m[0];
  };
  return new Function(`
    ${pick(/function px\(v\) \{[\s\S]*?\n\}/, 'px')}
    ${pick(/function rub\(v[\s\S]*?\n\}/, 'rub')}
    ${pick(/function pnlBox\(p\) \{[\s\S]*?\n\}/, 'pnlBox')}
    return pnlBox;`)() as (p: any) => string;
}

const nb = (s: string) => s.replace(/ /g, '\u00a0');

describe('коробка p&l заявки', () => {
  for (const [page, src] of Object.entries(PAGES)) {
    const box = boxFn(src);

    it(`${page}: фикс и ВМ в квадратных скобках`, () => {
      const h = box({ fix_rub: 1234, vm_rub: -567, pos: 2, priced: true, fills: 4, lots: 6 });
      expect(h).toContain('[');
      expect(h).toContain('фикс');
      expect(h).toContain('ВМ');
    });

    // У закрытой позиции переоценивать нечего, и ноль читался бы как факт
    // «переоценка нулевая».
    it(`${page}: у закрытой позиции ВМ не печатается`, () => {
      const h = box({ fix_rub: 900, vm_rub: null, pos: 0, priced: true, fills: 2, lots: 2 });
      expect(h).toContain('фикс');
      expect(h).not.toContain('ВМ');
    });

    // Пункт не рубль. Без ₽/пункт печатаем пункты и говорим это словом.
    it(`${page}: без ₽/пункт показываем ПУНКТЫ, а не рубли`, () => {
      const h = box({ fix_rub: null, fix_pts: 420, vm_rub: null, vm_pts: null,
                      pos: 0, priced: false, fills: 1, lots: 1 });
      expect(h).toContain('п.');
      expect(h).not.toContain('₽');
    });

    it(`${page}: нет данных — нет и скобок`, () => {
      expect(box(null)).toBe('');
      expect(box({ fix_rub: null, fix_pts: null, vm_rub: null, vm_pts: null, pos: 0 })).toBe('');
    });

    it(`${page}: снятая заявка свой результат сохраняет`, () => {
      // Снятость — свойство заявки, а не её сделок: они были и посчитаны.
      const h = box({ fix_rub: -2500, vm_rub: null, pos: 0, priced: true, fills: 3, lots: 3 });
      // Разделитель разрядов у toLocaleString бывает разным неразрывным
      // пробелом в разных сборках ICU — сравниваем по цифрам, а не по виду.
      expect(h.replace(/\s| | /g, '')).toContain('-2500');
      expect(h).toContain('down');        // минус подкрашен как минус
    });
  }
});

describe('ID заявки на экране', () => {
  for (const [page, src] of Object.entries(PAGES)) {
    // Оператор называет ID в разборе — значит он должен быть на экране, а не
    // только в ленте журнала (просьба 01.10.2026).
    it(`${page}: у умной заявки печатается so_id, у терминальной — номер`, () => {
      expect(src).toContain('идентификатор умной заявки STL');
      expect(src).toContain('номер заявки в QUIK');
    });
  }
});

// ЧИСТЫЙ P&L И СДЕЛКИ/КОМИССИЯ ЗАЯВКИ (заказ оператора 04.10.2026): «показывай кол-во
// сделок, размер комиссии и считай P&L за вычетом комиссии». Деньги считает сервер
// (trader/api/order_pnl), панель печатает — и важно, ЧТО именно она называет чистым.
function feeFn(src: string): (p: any) => string {
  const pick = (re: RegExp, what: string) => {
    const m = src.match(re);
    if (!m) throw new Error(`${what} не найдена`);
    return m[0];
  };
  return new Function(`
    ${pick(/function px\(v\) \{[\s\S]*?\n\}/, 'px')}
    ${pick(/function rub\(v[\s\S]*?\n\}/, 'rub')}
    ${pick(/function feeNote\(p\) \{[\s\S]*?\n\}/, 'feeNote')}
    return feeNote;`)() as (p: any) => string;
}

describe('P&L за вычетом комиссии', () => {
  for (const [page, src] of Object.entries(PAGES)) {
    const box = boxFn(src);
    const fee = feeFn(src);

    it(`${page}: основное число — ЧИСТЫЙ фикс (фикс минус комиссия)`, () => {
      const h = box({ fix_rub: 4500, commission_rub: 380, net_rub: 4120, total_rub: 4120,
                      pos: 0, priced: true, fills: 12, lots: 30 });
      expect(h).toContain('чист.');
      expect(h.replace(/\s| | /g, '')).toContain('+4120');
      expect(h).not.toContain('фикс +4');          // грязный фикс в скобках не светится
      expect(h).toContain('up');
    });

    it(`${page}: комиссия и грязный фикс — в подсказке`, () => {
      const h = box({ fix_rub: 4500, commission_rub: 380, net_rub: 4120, total_rub: 4120,
                      pos: 0, priced: true, fills: 12, lots: 30 });
      expect(h).toContain('комиссия');
      expect(h).toContain('оценка сверху');
    });

    it(`${page}: комиссия больше фикса — результат красный, а не «в плюсе по грязному»`, () => {
      // Грязный фикс +300 — плюс; чистый −80 — минус. Цвет по ЧИСТОМУ.
      const h = box({ fix_rub: 300, commission_rub: 380, net_rub: -80, total_rub: -80,
                      pos: 0, priced: true, fills: 4, lots: 4 });
      expect(h).toContain('down');
      expect(h).not.toContain('up');
    });

    it(`${page}: комиссии нет (комиссию не посчитать) — печатаем грязный «фикс» и не называем его чистым`, () => {
      // Заниженная комиссия делает результат лучше, чем он есть.
      const h = box({ fix_rub: 4500, commission_rub: null, net_rub: null, total_rub: null,
                      pos: 0, priced: true, fills: 3, lots: 3 });
      expect(h).toContain('фикс');
      expect(h).not.toContain('чист.');
    });

    it(`${page}: цвет по итогу сервера с живой переоценкой`, () => {
      // Чистый фикс +100, ВМ −400 → итог −300: коробка красная.
      const h = box({ fix_rub: 150, commission_rub: 50, net_rub: 100, vm_rub: -400, total_rub: -300,
                      pos: 1, priced: true, fills: 2, lots: 2 });
      expect(h).toContain('down');
    });

    it(`${page}: строка сделок и комиссии`, () => {
      const t = fee({ fills: 12, commission_rub: 380, priced: true });
      expect(t).toContain('сделок 12');
      expect(t).toContain('комиссия −');
      expect(t).toContain('(расч.)');                 // не «удержано»: это оценка по модели
      expect(t.replace(/\s| | /g, '')).toContain('380');
    });

    it(`${page}: нет сделок — нет строки; нет стоимости пункта — слово, а не ноль`, () => {
      expect(fee({ fills: 0, commission_rub: 0, priced: true })).toBe('');
      expect(fee(null)).toBe('');
      const t = fee({ fills: 3, commission_rub: null, priced: false });
      expect(t).toContain('сделок 3');
      expect(t).toContain('нет стоимости пункта');
      expect(t).not.toMatch(/−0|0 ₽/);
    });
  }
});
