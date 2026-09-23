// Разбивка «Итог ручных» подписывала три РАЗНЫЕ строки одинаково — «приложение
// брокера» трижды подряд с несовместимыми числами (оператор 23.09.2026).
// У этого вида ключ строки — тег канала брокера, и строк бывает несколько:
// без инструмента и тега подпись не отличает их друг от друга.
import fs from 'node:fs';
import path from 'node:path';
import { describe, it, expect } from 'vitest';

const read = (f: string) => fs.readFileSync(path.resolve('public', f), 'utf8');
const PAGES = { 'companion.html': read('companion.html'), 'm.html': read('m.html') };

/** Достаём manualRowName из страницы и выполняем её — проверяем ПОВЕДЕНИЕ, а не текст. */
function nameFn(src: string): (r: any) => string {
  const m = src.match(/function manualRowName\(r\) \{[\s\S]*?\n\}/);
  if (!m) throw new Error('manualRowName не найдена');
  return new Function(`${m[0]}; return manualRowName;`)() as (r: any) => string;
}

describe('подпись строки разбивки ручной торговли', () => {
  for (const [page, src] of Object.entries(PAGES)) {
    it(`${page}: инструмент назван всегда`, () => {
      const f = nameFn(src);
      expect(f({ kind: 'terminal', name: 'терминал QUIK', sec: 'RIZ6' })).toBe('терминал QUIK RIZ6');
      expect(f({ kind: 'smart', name: 'умные заявки', sec: 'GZZ6' })).toBe('умные заявки GZZ6');
    });

    it(`${page}: у приложения брокера назван и канал`, () => {
      const f = nameFn(src);
      const a = f({ kind: 'external', name: 'приложение брокера', key: 'mob1', sec: 'RIZ6' });
      const b = f({ kind: 'external', name: 'приложение брокера', key: 'mob2', sec: 'RIZ6' });
      expect(a).not.toBe(b);            // две строки одного вида различимы
      expect(a).toContain('mob1');
      expect(a).toContain('RIZ6');
    });

    it(`${page}: нет инструмента — подпись не выдумывается`, () => {
      const f = nameFn(src);
      expect(f({ kind: 'recon', name: 'выравнивание' })).toBe('выравнивание');
      expect(f({})).toBe('—');
    });
  }
});

// «Расшифруй логику каждой цифры» (оператор 23.09.2026). Число в строке — не
// прибыль по сделкам, а рыночная переоценка участника, и подсказка обязана
// назвать ВСЕ её слагаемые, иначе итог по-прежнему берётся ниоткуда.
describe('подсказка объясняет, из чего сложено число', () => {
  // Функции достаём срезом по имени, без регулярок: нужна их РАБОТА, а не текст.
  const cut = (src: string, name: string) => {
    const i = src.indexOf('function ' + name + '(r) {');
    const j = src.indexOf(String.fromCharCode(10) + '}', i);
    if (i < 0 || j < 0) throw new Error(name + ' не найдена');
    return src.slice(i, j + 2);
  };
  const why = (src: string) => {
    const rubSrc = "function rub(v,o){return (v>0&&o&&o.signed?'+':'')+String(v)+' Р'}";
    return new Function(
      rubSrc + ';' + cut(src, 'manualRowName') + cut(src, 'manualRowWhy') + 'return manualRowWhy;',
    )() as (r: any) => string;
  };
  const row = { kind: 'external', name: 'приложение брокера', key: 'mob1', sec: 'RIZ6',
                vm_rub: 124882, fills: 12, lots: 40, cash_pts: -1500,
                net_start: 0, net_end: 10, last: 86000, coef: 1.5681,
                base: 85500, base_src: 'quik_vm' };

  for (const [page, src] of Object.entries(PAGES)) {
    it(`${page}: названы сделки, деньги, позиция, цена и коэффициент`, () => {
      const s2 = why(src)(row);
      expect(s2).toContain('сделок 12');
      expect(s2).toContain('продажи − покупки');
      expect(s2).toContain('на утро');
      expect(s2).toContain('₽ за пункт');
      expect(s2).toContain('решена из ВМ');
    });

    it(`${page}: неизвестная цена клиринга названа неизвестной, а не подставлена молча`, () => {
      expect(why(src)({ ...row, base_src: 'unresolved' })).toContain('неизвестна');
    });

    it(`${page}: доля участника названа оценкой, а не фактом`, () => {
      expect(why(src)(row)).toContain('пропорционально');
    });
  }
});
