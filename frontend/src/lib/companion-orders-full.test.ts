// Коробка ручных заявок в обоих компаньонах: свёрнута в окошко с прокруткой
// (прежний вид) или развёрнута кнопкой во весь список без прокрутки (15.09.2026,
// длинные «Следящие» неудобно листать в окошке). Страницы без сборщика, поэтому
// функцию достаём из файла, как в соседних тестах.
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

const page = (f: string) => readFileSync(resolve(process.cwd(), 'public', f), 'utf8')
  .replace(/\r\n/g, '\n');

function renderOrdersOf(html: string, ordFull: boolean) {
  const m = html.match(/function renderOrders\(o, d\) \{[\s\S]*?\n\}\n/);
  if (!m) throw new Error('не нашёл renderOrders');
  const deps = {
    esc: (x: unknown) => String(x ?? ''), px: (x: unknown) => String(x), todayLo: () => 0,
    ORD_GROUPS: [{ id: 'trail_tp', name: 'Следящие' }],
    ORD_DONE_ST: new Set(['fired', 'cancelled', 'expired']),
    ordOpen: new Set(['trail_tp']), ordDone: true, ordFull,
  };
  const fn = new Function('d', `with (d) { ${m[0]}; return renderOrders; }`)(deps);
  const smart = Array.from({ length: 12 }, (_, i) => ({
    kind: 'trail_tp', status: 'armed', code: 'RIU6', side: 'sell', qty: 1,
    trigger: 83000 + i, created_ms: i,
  }));
  return fn({ smart, manual: [] }, {}) as string;
}

describe.each(['companion.html', 'm.html'])('%s — развернуть заявки', (file) => {
  const html = page(file);

  it('свёрнуто по умолчанию: окошко с прокруткой и кнопка «развернуть»', () => {
    const out = renderOrdersOf(html, false);
    expect(out).toContain('<div class="ord-box">');
    expect(out).toContain('data-ordfull');
    expect(out).toContain('развернуть');
  });

  it('развёрнуто: коробка без ограничения высоты и кнопка «свернуть»', () => {
    const out = renderOrdersOf(html, true);
    expect(out).toContain('<div class="ord-box full">');
    expect(out).toContain('свернуть');
    expect(out.match(/class="row"/g)?.length).toBe(12);   // весь список, не окошко
  });

  it('стиль развёрнутой коробки снимает высоту и прокрутку', () => {
    expect(html).toMatch(/\.ord-box\.full \{ max-height: none; overflow-y: visible; \}/);
  });

  it('клик по кнопке обрабатывается и переключает режим', () => {
    expect(html).toContain("closest('[data-ordfull]')) { toggleOrdFull(); return; }");
    expect(html).toMatch(/function toggleOrdFull\(\) \{/);
  });
});
