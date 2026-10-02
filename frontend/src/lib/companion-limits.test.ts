// Потребление пределов живой торговли в панели.
//
// Предел, которого не видно, замечают в момент отказа: дневной кап 50 однажды
// молча заморозил ВСЕ заявки роботов, включая выходы, и нашли это по логу
// раннера на VDS, а не на экране. Поэтому проверяется не «рисует», а две
// честности: счёт ведётся с подъёма службы, и жёстче всегда агент.
import { describe, it, expect, beforeAll } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';

const PANELS = ['companion.html', 'm.html'];
const fns: Record<string, (l: unknown) => string> = {};

beforeAll(() => {
  for (const f of PANELS) {
    const html = fs.readFileSync(path.resolve('public', f), 'utf8');
    const src = html.slice(html.indexOf('function renderLimits'),
                           html.indexOf('\nfunction renderWatch'));
    fns[f] = new Function(
      'esc', 'val',
      `${src}; return renderLimits;`,
    )((x: string) => String(x),
      (t: string | null, k = '') => (t == null ? '<span class="v nil">—</span>'
                                               : `<span class="v ${k}">${t}</span>`)) as any;
  }
});

const BASE = {
  trading_enabled: true,
  max_contracts_per_order: 75,
  max_working_contracts: 250,
  daily_order_cap: 500,
  price_collar_frac: 0.002,
  whitelist: ['RIZ6', 'GZZ6'],
  placed_today: 12,
  working_contracts: 24,
  counted_since_ms: Date.UTC(2026, 9, 2, 13, 49),   // 16:49 МСК
};

for (const f of PANELS) {
  describe(f, () => {
    it('показывает израсходованное из предела', () => {
      const h = fns[f](BASE);
      expect(h).toContain('12 из 500');
      expect(h).toContain('24 из 250');
    });

    it('СЧЁТ С ПОДЪЁМА СЛУЖБЫ подписан временем', () => {
      // «12 из 500 за день» и «12 из 500 с 16:49» — разные вещи, а в день с
      // шестью рестартами разница решает.
      expect(fns[f](BASE)).toContain('16:49');
    });

    it('расхода не знаем — показываем предел, а не ноль', () => {
      // Ноль читался бы как «не торговали», хотя мы просто не считали.
      const h = fns[f]({ ...BASE, placed_today: null, working_contracts: null });
      expect(h).not.toContain('0 из 500');
      expect(h).toContain('500');
    });

    it('у половины предела жёлтый, у 80% красный', () => {
      expect(fns[f]({ ...BASE, placed_today: 300 })).toContain('v warn');
      expect(fns[f]({ ...BASE, placed_today: 450 })).toContain('v down');
      expect(fns[f]({ ...BASE, placed_today: 12 })).not.toContain('v warn');
    });

    it('агентский предел жёстче — показываем ЕГО, он и отрежет', () => {
      // Пуш из STL умеет только ужесточать агентский бэкстоп, действует меньший
      // из двух. Показав один наш, экран обещал бы объём, который агент не
      // пропустит.
      const h = fns[f]({ ...BASE, agent: { max_contracts_per_order: 50 } });
      expect(h).toContain('агент 50');
    });

    it('агентский шире нашего — молчим: действует всё равно наш', () => {
      const h = fns[f]({ ...BASE, agent: { max_contracts_per_order: 100 } });
      expect(h).not.toContain('агент 100');
    });

    it('мастер-флаг выключен — это первая строка блока', () => {
      const h = fns[f]({ ...BASE, trading_enabled: false });
      expect(h.indexOf('Торговля запрещена')).toBeGreaterThan(-1);
      expect(h.indexOf('Торговля запрещена')).toBeLessThan(h.indexOf('Заявок за день'));
    });

    it('пустой белый список назван запретом, а не нулём', () => {
      expect(fns[f]({ ...BASE, whitelist: [] })).toContain('торговля запрещена молча');
    });

    it('пределов нет — блока нет', () => {
      expect(fns[f](null)).toBe('');
      expect(fns[f]({})).toBe('');
    });
  });
}
