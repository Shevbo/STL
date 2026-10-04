// Блок «сделки и комиссия за день» в заказе ручных заявок (заказ оператора 04.10.2026).
// Всё считает сервер (тот же журнал и та же функция комиссии, что у каждой заявки);
// здесь проверяется, ЧТО панель печатает и чего не обещает: комиссия — оценка по
// модели, а не «удержанная» по выписке, и недобор называется вслух.
import fs from 'node:fs';
import path from 'node:path';
import { describe, it, expect } from 'vitest';

const read = (f: string) => fs.readFileSync(path.resolve('public', f), 'utf8');
const PAGES: Record<string, string> = { 'companion.html': read('companion.html'), 'm.html': read('m.html') };

function costsFn(src: string): (c: any) => string {
  const pick = (re: RegExp, what: string) => {
    const m = src.match(re);
    if (!m) throw new Error(`${what} не найдена`);
    return m[0];
  };
  return new Function(`
    ${pick(/const esc = \(s\) => [\s\S]*?\]\)\);/, 'esc')}
    ${pick(/function rub\(v[\s\S]*?\n\}/, 'rub')}
    ${pick(/function cls\(v\)[^\n]*/, 'cls')}
    ${pick(/function val\(txt, k\)[\s\S]*?\n[^\n]*\n/, 'val')}
    ${pick(/function manualCosts\(c\) \{[\s\S]*?\n\}/, 'manualCosts')}
    return manualCosts;`)() as (c: any) => string;
}

const ok = {
  fills: 12, lots: 30, orders: 5, commission_rub: 380, commission_floor: false,
  by_channel: [{ channel: 'smart', fills: 10 }, { channel: 'quik', fills: 2 }],
  vm_after_fees_rub: -20_300, model: 'taker',
};
const flat = (h: string) => h.replace(/<[^>]+>/g, ' ').replace(/\s| | /g, '');

for (const [page, src] of Object.entries(PAGES)) {
  describe(`${page}: сделки и комиссия за день`, () => {
    const fn = costsFn(src);

    it('сделки, комиссия и ВМ после комиссии', () => {
      const h = fn(ok);
      expect(h).toContain('Сделок за день');
      expect(flat(h)).toContain('12');
      expect(flat(h)).toContain('−380');
      expect(h).toContain('ВМ ручных после комиссии');
      expect(flat(h)).toContain('-20300');
    });

    it('комиссия названа расчётной, а не удержанной', () => {
      // QUIK комиссию в таблице сделок не отдаёт: «удержано» было бы утверждением
      // про выписку брокера, которой у нас нет.
      const h = fn(ok);
      expect(h).toContain('расч.');
      expect(h).toContain('не выписка брокера');
      expect(h).not.toMatch(/удержан/i);
    });

    it('недобор называется вслух: «не меньше»', () => {
      expect(fn({ ...ok, commission_floor: true })).toContain('не меньше');
      expect(fn(ok)).not.toContain('не меньше');
    });

    it('нет блока ручной торговли — нет и строк, а не «0 ₽»', () => {
      expect(fn(null)).toBe('');
      expect(fn({ ...ok, commission_rub: null })).toBe('');
    });

    it('нет ВМ — нет и «после комиссии»: нулём не подменяем', () => {
      const h = fn({ ...ok, vm_after_fees_rub: null });
      expect(h).not.toContain('после комиссии');
      expect(h).toContain('Комиссия (расч.)');
    });

    it('разбивка по каналам в строке сделок', () => {
      expect(fn(ok)).toContain('smart 10');
    });
  });
}
