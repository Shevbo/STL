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

function termFn(src: string, ordDone = true, open = true, canCancel = true) {
  const pick = (re: RegExp, what: string) => {
    const m = src.match(re);
    if (!m) throw new Error(`${what} не найдена`);
    return m[0];
  };
  const deps = {
    esc: (x: unknown) => String(x ?? ''),
    px: (x: unknown) => String(x),
    pnlBox: () => '',
    feeNote: () => '',
    ordOpen: new Set(open ? ['terminal'] : []),
    ordDone,
    // В трее панель ходит через локальный шелл с токеном компаньона, а тот
    // открывает РОВНО ОДИН эндпоинт — снятия оттуда быть не может.
    CAN_CANCEL: canCancel,
    cancelAsk: (r: any) => `снять ${r.num}?`,
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

// Снятие заявки прямо из панели. Решение real-trade 01.10.2026: кнопка на ВСЕХ
// строках, а защита — в подтверждении, а не в её отсутствии. Ручка написана
// ради ОСИРОТЕВШЕЙ заявки, чей владелец её уже не помнит; дать кнопку только
// ручным значило бы оставить ровно тот тупик, из которого оператор выходил
// руками в терминале.
describe('снятие заявки из панели', () => {
  for (const [page, src] of Object.entries(PAGES)) {
    const ask = (() => {
      const m = src.match(/function cancelAsk\(r\) \{[\s\S]*?\n\}\n/);
      const o = src.match(/function ownerOf\(r\) \{[\s\S]*?\n\}\n/);
      return new Function(`${o![0]}${m![0]}; return cancelAsk;`)() as (r: any) => string;
    })();

    it(`${page}: у чужой заявки подтверждение называет владельца и последствие`, () => {
      const t = ask({ num: '77', origin: 'robot', tag: 'lxk22tsffsx' });
      expect(t).toContain('77');
      expect(t).toContain('lxk22tsffsx');
      expect(t).toContain('НЕ УЗНАЕТ');
      expect(t).toContain('осиротела');
    });

    it(`${page}: у умной заявки подтверждение называет её so_id`, () => {
      expect(ask({ num: '9', origin: 'smart', so_id: '41bf3af0dd' })).toContain('41bf3af0dd');
    });

    // Своя заявка не требует лекции: лишний текст в подтверждении, которое
    // читают каждый раз, перестают читать вовсе.
    it(`${page}: у своей ручной заявки подтверждение короткое`, () => {
      const t = ask({ num: '5', origin: 'manual' });
      expect(t).not.toContain('НЕ УЗНАЕТ');
    });

    it(`${page}: кнопка есть у активной и отсутствует у снятой`, () => {
      const html = termFn(src)({
        terminal: [ROW({ num: '1' })],
        terminal_done: [ROW({ num: '2', active: false, state: 'снята' })],
        terminal_total: 2, terminal_active: 1, terminal_stale: false,
      });
      expect(html.match(/data-cancel="1"/g)).toHaveLength(1);
      expect(html).not.toContain('data-cancel="2"');
    });

    // Токен компаньона открывает РОВНО ОДИН эндпоинт — так и задумано: утёкший
    // токен утекает ЧТЕНИЕ. В трее кнопка молча не работала бы.
    it(`${page}: где снятие недоступно, об этом сказано, а не кнопка-обманка`, () => {
      const html = termFn(src, true, true, false)({
        terminal: [ROW()], terminal_done: [], terminal_total: 1,
        terminal_active: 1, terminal_stale: false,
      });
      expect(html).not.toContain('data-cancel');
      expect(html).toContain('снятие в вебе');
    });
  }
});

// У стоп-заявки ДВЕ цены, и их разрыв — причина, по которой 29.09.2026 стоп
// сработал и не исполнился. Показать одну значит спрятать половину риска.
describe('строки стоп-заявок', () => {
  for (const [page, src] of Object.entries(PAGES)) {
    it(`${page}: показываются и уровень срабатывания, и цена заявки`, () => {
      const html = termFn(src)({
        terminal: [ROW({ kind: 'stop', price: 84_000, limit_price: 83_970, offset: 30 })],
        terminal_done: [], terminal_total: 1, terminal_active: 1, terminal_stale: false,
      });
      expect(html).toContain('срабатывание');
      expect(html).toContain('84000');
      expect(html).toContain('83970');
      expect(html).toContain('стоп');
    });

    // Механика нативных стопов выверена не полностью: пустую сторону печатаем
    // пустой, а не превращаем в «продажу» (предупреждение real-trade).
    it(`${page}: пустая сторона остаётся пустой, а не становится продажей`, () => {
      const html = termFn(src)({
        terminal: [ROW({ kind: 'stop', side: '' })],
        terminal_done: [], terminal_total: 1, terminal_active: 1, terminal_stale: false,
      });
      expect(html).not.toContain('продажа');
      expect(html).not.toContain('покупка');
    });
  }
});

// Вёрстка строки заявки. 02.10.2026 оператор прислал снимок: описание сетки
// («шаг 90 п. · вниз 5 · вверх 5 · по 5 · стоп за краем 100 п.») сжималось в
// узкую колонку и ехало лесенкой, потому что строка была ОДНОЙ флекс-линией, а
// справа на той же линии стояли id и статус. Заявка — объект с заголовком и
// подробностями, а не пара «подпись-значение».
describe('строка заявки читается, а не ломается', () => {
  for (const [page, src] of Object.entries(PAGES)) {
    it(`${page}: заголовок и подробности — разные этажи`, () => {
      const html = termFn(src)({
        terminal: [ROW({ num: '7', qty: 5 })],
        terminal_done: [], terminal_total: 1, terminal_active: 1, terminal_stale: false,
      });
      expect(html).toContain('class="orow');
      expect(html).toContain('class="ohead"');
      expect(html).toContain('class="odet"');
      // Подробности идут ПОСЛЕ заголовка: иначе этажи поменяются местами.
      expect(html.indexOf('class="ohead"')).toBeLessThan(html.indexOf('class="odet"'));
    });

    it(`${page}: правило двух этажей объявлено в стилях`, () => {
      expect(src).toMatch(/\.orow \{[\s\S]*?flex-direction: column/);
      expect(src).toMatch(/\.odet \{[\s\S]*?font-size: 10px/);
    });

    // Сторона красит рейку строки: лонг и шорт различаются до чтения текста.
    it(`${page}: сторона видна рейкой`, () => {
      const buy = termFn(src)({ terminal: [ROW({ side: 'buy' })], terminal_done: [],
                                terminal_total: 1, terminal_active: 1, terminal_stale: false });
      expect(buy).toMatch(/class="orow[^"]*\bbuy\b/);
      const sell = termFn(src)({ terminal: [ROW({ side: 'sell' })], terminal_done: [],
                                 terminal_total: 1, terminal_active: 1, terminal_stale: false });
      expect(sell).toMatch(/class="orow[^"]*\bsell\b/);
    });
  }
});
