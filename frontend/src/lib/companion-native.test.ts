// Статус native в компаньонах (real-trade 17.09.2026): после входа по умной заявке
// STL ставит нативную стоп-заявку QUIK на всю защитную связку. Такая защита
// переживает падение STL, поэтому заявка ЖИВАЯ и подписана иначе, чем armed. Если
// терминал её не принял (native_state=failed), защиту снова ведёт STL — и это
// обязано быть видно тревожно, а не молча. Держим обе страницы.
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
    ordOpen: new Set(['sl']), ordDone: true, ordFull: false,
  };
  return new Function('d', `with (d) { ${groups}${m[0]}; return renderOrders; }`)(deps);
}

const snap = (over: Record<string, unknown>) => ({
  manual: [], counts: { sl: 1 }, counts_active: { sl: 1 },
  smart: [{ so_id: 'p1', kind: 'sl', code: 'RIZ6', side: 'sell', qty: 1, trigger: 87000,
            created_ms: 1, ...over }],
});

describe.each(['companion.html', 'm.html'])('%s — под охраной терминала', (file) => {
  const render = renderOrdersOf(page(file));

  it('подпись говорит, кто держит защиту, и виден номер стоп-заявки QUIK', () => {
    const out = render(snap({ status: 'native', native_state: 'live',
                              native_stop_num: '1900000000000000123' }), {}) as string;
    expect(out).toContain('под охраной терминала');
    expect(out).toContain('стоп-заявка QUIK 1900000000000000123');
  });

  it('терминал не принял — строка тревожная и говорит, что ведёт STL', () => {
    const out = render(snap({ status: 'native', native_state: 'failed' }), {}) as string;
    expect(out).toContain('терминал НЕ принял, защиту ведёт STL');
    expect(out).toMatch(/class="v down"/);
  });

  it('native не выглядит отработавшей: цвет живой, а не погашенный', () => {
    const out = render(snap({ status: 'native', native_state: 'live' }), {}) as string;
    expect(out).not.toMatch(/class="v nil"/);
  });

  it('отработавшая заявка по-прежнему гасится', () => {
    const out = render(snap({ status: 'fired', fired_price: 87010 }), {}) as string;
    expect(out).toMatch(/class="v nil"/);
  });
});
