// Порядок заявок в компаньонах (просьба оператора 17.09.2026): внутри группы
// сначала ПРОДАЖИ, потом ПОКУПКИ, внутри стороны — цена срабатывания по убыванию.
// Список по времени взведения читался журналом: что стоит над рынком, а что под
// ним, приходилось складывать в голове. Держим обе страницы.
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

const page = (f: string) => readFileSync(resolve(process.cwd(), 'public', f), 'utf8')
  .replace(/\r\n/g, '\n');

function renderOrdersOf(html: string) {
  const m = html.match(/function renderOrders\(o, d\) \{[\s\S]*?\n\}\n/);
  if (!m) throw new Error('не нашёл renderOrders');
  const groups = html.match(/const ORD_GROUPS = \[[\s\S]*?\n\];\n/)![0];
  const deps = {
    esc: (x: unknown) => String(x ?? ''), px: (x: unknown) => String(x), todayLo: () => 0,
    ORD_DONE_ST: new Set(['fired', 'cancelled', 'expired']),
    ordOpen: new Set(['trail_tp', 'sl']), ordDone: true, ordFull: false,
  };
  return new Function('d', `with (d) { ${groups}${m[0]}; return renderOrders; }`)(deps);
}

const so = (id: string, side: string, extra: Record<string, unknown>) =>
  ({ so_id: id, kind: 'trail_tp', status: 'armed', code: 'RIZ6', side, qty: 1,
     trail_offset: 100, created_ms: 1, ...extra });

// Порядок в книге намеренно вперемешку: экран обязан переставить сам.
const SNAP = {
  manual: [],
  counts: { trail_tp: 4 }, counts_active: { trail_tp: 4 },
  smart: [
    so('b1', 'buy', { trigger: 88000, created_ms: 4 }),
    so('s1', 'sell', { trigger: 90500, created_ms: 1 }),
    so('b2', 'buy', { trigger: 89000, created_ms: 3 }),
    so('s2', 'sell', { activated: true, peak: 91000, trigger: 90000, created_ms: 2 }),
  ],
};

describe.each(['companion.html', 'm.html'])('%s — порядок заявок', (file) => {
  const out = renderOrdersOf(page(file))(SNAP, {}) as string;
  const prices = [...out.matchAll(/(?:ждёт пробоя|сделка при [≥≤] )([\d\s   ]+)/g)]
    .map((m) => Number(m[1].replace(/\D/g, '')));

  it('сначала продажи, потом покупки', () => {
    const sides = [...out.matchAll(/class="ord-side( buy)?"/g)].map((m) => (m[1] ? 'buy' : 'sell'));
    expect(sides).toEqual(['sell', 'buy']);
  });

  it('внутри стороны цена по убыванию, у следящей в слежении — уровень выхода', () => {
    // s2 активна: выход 91000-100 = 90900, выше уровня активации 90000 у неё же.
    expect(prices).toEqual([90900, 90500, 89000, 88000]);
  });

  it('подпись стороны стоит один раз на сторону, а не над каждой строкой', () => {
    expect((out.match(/class="ord-side/g) || []).length).toBe(2);
  });
});
