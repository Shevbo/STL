// Таблица заявок терминала QUIK целиком. Жалоба оператора 01.10.2026 дословно:
// «не вижу активные заявки в квике вне зависимости от их природы, не могу их
// снять». Корень был в том, что панель выбрасывала роботные, recon и stl-so*
// строки ДО показа — живых заявок QUIK у неё не бывало в принципе.
//
// Природа заявки — ЯРЛЫК, а не фильтр: заявка стоит в рынке и торгует деньгами,
// значит обязана быть на экране, кто бы её ни поставил.
import fs from 'node:fs';
import path from 'node:path';
import { describe, it, expect } from 'vitest';

const read = (f: string) => fs.readFileSync(path.resolve('public', f), 'utf8').replace(/\r\n/g, '\n');
const PAGES = { 'companion.html': read('companion.html'), 'm.html': read('m.html') };

function termFn(src: string, ordDone = true, open = true) {
  const pick = (re: RegExp, what: string) => {
    const m = src.match(re);
    if (!m) throw new Error(`${what} не найдена`);
    return m[0];
  };
  const deps = {
    esc: (x: unknown) => String(x ?? ''),
    px: (x: unknown) => String(x),
    pnlBox: () => '',
    ordOpen: new Set(open ? ['terminal'] : []),
    ordDone,
  };
  const src2 = pick(/const ORIGIN_RU = \{[\s\S]*?\n\};\n/, 'ORIGIN_RU')
    + pick(/function originTag\(r\) \{[\s\S]*?\n\}\n/, 'originTag')
    + pick(/function renderTerminal\(o\) \{[\s\S]*?\n\}\n/, 'renderTerminal');
  return new Function('d', `with (d) { ${src2}; return renderTerminal; }`)(deps) as (o: any) => string;
}

const ROW = (over: Record<string, unknown> = {}) => ({
  num: '19250402', sec: 'RIZ6', side: 'sell', price: 85_000, qty: 5, balance: 5,
  filled: 0, active: true, state: 'активна', tag: '', origin: 'manual', so_id: '',
  ts_ms: 1, ...over,
});

describe('таблица заявок терминала', () => {
  for (const [page, src] of Object.entries(PAGES)) {
    it(`${page}: показываются ВСЕ природы, а не только ручные`, () => {
      const html = termFn(src)({
        terminal: [ROW({ origin: 'robot', num: '1' }), ROW({ origin: 'smart', num: '2', so_id: 'abc' }),
                   ROW({ origin: 'recon', num: '3' }), ROW({ origin: 'external', num: '4' }),
                   ROW({ origin: 'manual', num: '5' })],
        terminal_done: [], terminal_total: 5, terminal_active: 5, terminal_stale: false,
      });
      for (const n of ['1', '2', '3', '4', '5']) expect(html, n).toContain(n);
      for (const w of ['робот', 'умная STL', 'выравнивание', 'приложение брокера', 'руками']) {
        expect(html, w).toContain(w);
      }
    });

    // Пустая таблица при недоступном зеркале это НЕ «заявок нет».
    it(`${page}: недоступная таблица не выдаётся за пустую`, () => {
      const html = termFn(src)({ terminal: [], terminal_done: [], terminal_total: 0,
                                 terminal_active: 0, terminal_stale: true });
      expect(html).toContain('недоступна');
      expect(html).not.toContain('заявок нет');
    });

    it(`${page}: доступная и пустая — так и говорим`, () => {
      const html = termFn(src)({ terminal: [], terminal_done: [], terminal_total: 0,
                                 terminal_active: 0, terminal_stale: false });
      expect(html).toContain('заявок нет');
    });

    // Счётчик берётся с сервера: он считает ДО обрезки списка.
    it(`${page}: счётчик серверный, а не длина показанного`, () => {
      const html = termFn(src, false)({ terminal: [ROW()], terminal_done: [],
                                        terminal_total: 57, terminal_active: 42,
                                        terminal_stale: false });
      expect(html).toContain('42');
      const all = termFn(src, true)({ terminal: [ROW()], terminal_done: [],
                                      terminal_total: 57, terminal_active: 42,
                                      terminal_stale: false });
      expect(all).toContain('57');
    });

    // Старый сервер (до рестарта) полей не присылает — блока нет вовсе, а не
    // пустая коробка со словом «нет».
    it(`${page}: старый сервер без полей блок не рисует`, () => {
      expect(termFn(src)({ manual: [] })).toBe('');
    });

    it(`${page}: незнакомую природу показываем как есть, а не прячем`, () => {
      const html = termFn(src)({ terminal: [ROW({ origin: 'что_то_новое' })],
                                 terminal_done: [], terminal_total: 1,
                                 terminal_active: 1, terminal_stale: false });
      expect(html).toContain('что_то_новое');
    });
  }
});
