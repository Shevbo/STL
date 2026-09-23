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
