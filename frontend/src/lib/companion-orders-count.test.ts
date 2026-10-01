// Счётчик группы заявок в компаньонах. 17.09.2026 оператор: панель показывала 2
// следящие заявки, а в книге их было 4. Снапшот режет список до 20 строк, и с
// выключенным фильтром «исполненные и снятые» панель считала ВИДИМЫЕ строки, то
// есть повторяла обрезку. Теперь число живых заявок приходит с сервера
// (counts_active) и показывается как есть. Держим обе страницы.
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

const page = (f: string) => readFileSync(resolve(process.cwd(), 'public', f), 'utf8')
  .replace(/\r\n/g, '\n');

function renderOrdersOf(html: string, ordDone: boolean) {
  const m = html.match(/function renderOrders\(o, d\) \{[\s\S]*?\n\}\n/);
  if (!m) throw new Error('не нашёл renderOrders');
  const groups = html.match(/const ORD_GROUPS = \[[\s\S]*?\n\];\n/)![0];
  const deps = {
    esc: (x: unknown) => String(x ?? ''), px: (x: unknown) => String(x), todayLo: () => 0,
    ORD_DONE_ST: new Set(['fired', 'cancelled', 'expired']),
    ordOpen: new Set<string>(), ordDone, ordFull: false,
  };
  return new Function('d', `with (d) { ${groups}${m[0]}; return renderOrders; }`)(deps);
}

// Две видимые взведённые из четырёх: ещё две сервер обрезал, но посчитал.
const SNAP = {
  manual: [],
  counts: { quik: 0, sl: 0, tp: 0, trail_tp: 40, trail_sl: 1, on_fill: 0 },
  counts_active: { quik: 0, sl: 0, tp: 0, trail_tp: 4, trail_sl: 1, on_fill: 0 },
  smart: [
    { so_id: 'a', kind: 'trail_tp', status: 'armed', code: 'RIZ6', side: 'sell', qty: 1,
      trigger: 90000, trail_offset: 50, created_ms: 2 },
    { so_id: 'b', kind: 'trail_tp', status: 'armed', code: 'RIZ6', side: 'sell', qty: 1,
      trigger: 90100, trail_offset: 50, created_ms: 1 },
    { so_id: 'c', kind: 'trail_sl', status: 'armed', code: 'BRZ6', side: 'sell', qty: 1,
      trail_offset: 300, created_ms: 3 },
  ],
};

describe.each(['companion.html', 'm.html'])('%s — счётчик заявок', (file) => {
  const html = page(file);

  it('с фильтром «только живые» число берётся с сервера, а не из обрезанного списка', () => {
    const out = renderOrdersOf(html, false)(SNAP, {}) as string;
    // Следящих видно две, а взведённых в книге четыре — показываем четыре.
    expect(out).toMatch(/Следящие<\/span><span class="sp"><\/span><span class="ord-n">4</);
  });

  it('с историей число тоже серверное: весь день, а не двадцать строк', () => {
    const out = renderOrdersOf(html, true)(SNAP, {}) as string;
    expect(out).toMatch(/Следящие<\/span><span class="sp"><\/span><span class="ord-n">40</);
  });

  it('у подтягивающих своя группа: они больше не падают в «Условные»', () => {
    const out = renderOrdersOf(html, false)(SNAP, {}) as string;
    expect(out).toContain('Подтягивающие');
    expect(out).not.toMatch(/Условные<\/span><span class="sp"><\/span><span class="ord-n">1</);
  });

  it('старый сервер без counts_active — считаем видимое, но не врём в большую сторону', () => {
    const old = { ...SNAP, counts_active: undefined };
    const out = renderOrdersOf(html, false)(old, {}) as string;
    expect(out).toMatch(/Следящие<\/span><span class="sp"><\/span><span class="ord-n">2</);
  });
});

// Новые виды умных заявок в панели. 29.09–30.09.2026 завели коридор,
// треугольник и радиацию, а список групп в панели остался прежним: их заявки
// падали в «Условные» и выглядели как отсутствующие. Оператор 01.10: «новых
// умных заявок в компаньоне нет».
describe('группы новых видов заявок', () => {
  const PAGES = ['companion.html', 'm.html'];
  const read = (f: string) => readFileSync(resolve('public', f), 'utf8');

  for (const page of PAGES) {
    const src = read(page);

    it(`${page}: у коридора, треугольника и радиации своя группа`, () => {
      for (const id of ['corridor', 'triangle', 'grid']) {
        expect(src, id).toContain(`id: '${id}'`);
      }
    });

    // Незнакомый вид обязан быть ВИДЕН как незнакомый, а не прикинуться
    // условной заявкой: именно так три новых типа и пропали с экрана.
    it(`${page}: неизвестный вид уходит в «Прочие», а не в «Условные»`, () => {
      expect(src).toContain("rowsBy.other");
      expect(src).not.toContain("rowsBy[s.kind] || rowsBy.sl");
    });

    it(`${page}: корзины строятся из списка групп, а не из второго перечня`, () => {
      expect(src).toContain('for (const g of ORD_GROUPS) rowsBy[g.id] = [];');
    });
  }
});
