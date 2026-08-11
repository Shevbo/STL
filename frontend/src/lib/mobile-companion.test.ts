// Мобильный компаньон (public/m.html) — два правила, которые молча ломались:
//   1. он показывает ТО ЖЕ, что панель на Windows (public/companion.html);
//   2. все его ссылки ведут на СВОИ экранные формы (#/...), потому что телефон
//      с токеном компаньона в SPA STL всё равно не пустят.
import { describe, it, expect, beforeAll } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';

const read = (f: string) => fs.readFileSync(path.resolve('public', f), 'utf8');
const M = read('m.html');
const WIN = read('companion.html');
const body = (h: string) => h.slice(h.indexOf('<script>') + 8, h.lastIndexOf('</script>'));

const SNAP = {
  ts_ms: Date.UTC(2026, 7, 5, 16, 40),
  ping_ms: 12,
  market: { open: true, phase: 'trading', last_trade_ms: Date.UTC(2026, 7, 5, 16, 39) },
  account: { has_data: true, limit: 1e6, used: 5e5, planned: 5e5, varmargin: -1200, ts_comission: 340 },
  positions: [{ sec: 'RIU6', net: 2, varmargin: -1500, last: 83540.5, bid: 83530, ask: 83550,
                quote_age_ms: 800, robot_net: 2, manual_net: 0 }],
  robots: [{
    id: 'agent-macd-RIU6-v1', name: 'MACD Trend A', symbol: 'RIU6', state: 'реал', mode: 'real',
    paused: false, exit_only: false, real_net: 187000, real_total: 185500, real_today: 2400,
    vm_today: -900, today_total: 1500, chg_pct: 0.8, real_trades_today: 7, position: 2,
    max_position: 4, varmargin: -1500, max_go: 214688,
    started_ms: Date.UTC(2026, 6, 20), ann_pct: 118.4, last_trade_ms: Date.UTC(2026, 7, 5, 16, 20),
  }],
  orders: {
    manual: [{ sec: 'RIU6', side: 'buy', price: 83500, qty: 2, balance: 1, state: 'активна', active: true }],
    smart: [{ so_id: 'so1', kind: 'tp', code: 'RIU6', side: 'sell', qty: 1, trigger: 83900,
              sl_offset: 40, tp_offset: 120, status: 'armed' }],
  },
  watch: { runner: { ok: true, issues: [] }, backtests: { ok: true, issues: [], running: 1, queued: 2 },
           platform: { ok: true, issues: [], hoster: { load: 1.2 }, i9: { cpu_pct: 40 } } },
  alerts: { items: [{ ts_ms: Date.UTC(2026, 7, 5, 6, 3), text: 'SMS-шлюз молчит', ok: false }],
            unresolved: ['SMS-шлюз молчит'], sms_today: ['SMS-шлюз молчит'], sms_count: 1,
            last_run_ms: Date.UTC(2026, 7, 5, 16, 30) },
};

let paint: (d: unknown) => void;

const screen = (hash: string) => {
  window.location.hash = hash;
  paint(SNAP);
  return document.getElementById('app')!.innerHTML;
};

beforeAll(() => {
  document.body.innerHTML = '<div id="app"></div>';
  // Страница на старте сама дёргает снапшот; отвечаем 401 — рисовать будем вручную.
  (globalThis as { fetch?: unknown }).fetch = () =>
    Promise.resolve({ ok: false, status: 401, json: async () => ({}) });
  paint = new Function(body(M) + '\nreturn paint;')() as typeof paint;
});

describe('мобильный компаньон: экранные формы', () => {
  it('все ссылки ведут внутрь страницы, а не в десктопный SPA', () => {
    for (const hash of ['#/', '#/orders', '#/watch', '#/robot/agent-macd-RIU6-v1']) {
      const hrefs = [...screen(hash).matchAll(/href="([^"]*)"/g)].map((m) => m[1]);
      expect(hrefs.length).toBeGreaterThan(0);
      for (const h of hrefs) expect(h.startsWith('#')).toBe(true);
    }
  });

  it('карточка робота открывается по ссылке из панели', () => {
    expect(screen('#/')).toContain('href="#/robot/agent-macd-RIU6-v1"');
    const card = screen('#/robot/agent-macd-RIU6-v1');
    expect(card).toContain('MACD Trend A');
    expect(card).toContain('фикс по закрытым + ВМ открытой позиции');
    expect(card).toContain('agent-macd-RIU6-v1');       // копируемый id экрана
  });

  it('неизвестный робот не роняет экран', () => {
    expect(screen('#/robot/нет-такого')).toContain('не найден');
  });
});

describe('мобильный компаньон: паритет с панелью Windows', () => {
  const panel = () => screen('#/');

  it('позиция несёт живую котировку и подпись «ВМ сессии»', () => {
    const h = panel();
    // innerHTML отдаёт неразрывный пробел как &nbsp;
    expect(h).toMatch(/83(&nbsp;|\s)540,5/);   // цену не округляем до целого
    expect(h).toContain('ВМ сессии');
  });

  it('«сегодня» показано с разбивкой фикс + ВМ, годовая — с горизонтом', () => {
    const h = panel();
    expect(h).toMatch(/= фикс .*ВМ откр\. поз\./);
    expect(h).toMatch(/за \d+ дн/);
    expect(h).toContain('сделок сегодня');
  });

  it('заявки сгруппированы по типам, счётчик виден без раскрытия', () => {
    const h = screen('#/orders');
    expect(h).toContain('QUIK · терминал');      // простая заявка из фикстуры
    expect(h).toContain('Лимитные');             // умная заявка из фикстуры — kind tp
    expect(h).toContain('data-ord="tp"');
    // свёрнутая группа показывает количество, но не строки
    expect(h).not.toContain('стоп 40 п.');
  });

  it('раскрытая группа показывает и стоп, и тейк', () => {
    localStorage.setItem('stl.ordOpen', JSON.stringify(['trail_tp', 'sl', 'tp', 'on_fill', 'quik']));
    // ordOpen читается один раз при загрузке страницы — перечитываем её целиком
    const p2 = new Function(body(M) + '\nreturn paint;')() as typeof paint;
    window.location.hash = '#/orders';
    p2(SNAP);
    const h = document.getElementById('app')!.innerHTML;
    expect(h).toContain('стоп 40 п.');
    expect(h).toContain('тейк 120 п.');
    localStorage.removeItem('stl.ordOpen');
  });

  // Фильтр истории: снятая простая и сработавшая умная уходят из кадра, живые
  // остаются, а счётчик группы перестаёт считать скрытое.
  it('фильтр прячет исполненные и снятые, живые остаются', () => {
    const SNAP2 = { ...SNAP, orders: {
      manual: [SNAP.orders.manual[0],
               { sec: 'RIU6', side: 'sell', price: 84000, qty: 1, balance: 1, state: 'снята', active: false }],
      smart: [SNAP.orders.smart[0],
              { so_id: 'so2', kind: 'tp', code: 'BRU6', side: 'buy', qty: 3, trigger: 6800, status: 'fired' }],
      counts: { quik: 2, tp: 2 },
    } };
    const open = JSON.stringify(['quik', 'tp']);
    localStorage.setItem('stl.ordOpen', open);
    const all = new Function(body(M) + '\nreturn paint;')() as typeof paint;
    window.location.hash = '#/orders';
    all(SNAP2);
    expect(document.getElementById('app')!.innerHTML).toContain('BRU6');

    localStorage.setItem('stl.ordDone', '0');   // фильтр читается при загрузке
    const only = new Function(body(M) + '\nreturn paint;')() as typeof paint;
    only(SNAP2);
    const h = document.getElementById('app')!.innerHTML;
    expect(h).not.toContain('BRU6');            // сработавшая умная скрыта
    expect(h).not.toContain('84&nbsp;000');     // снятая простая скрыта
    expect(h).toContain('83&nbsp;500');         // активная на месте
    expect(h).toMatch(/QUIK · терминал[\s\S]*?ord-n">1</);   // счётчик по видимому
    localStorage.removeItem('stl.ordDone');
    localStorage.removeItem('stl.ordOpen');
  });

  // Дрейф ловим по полям снапшота: если панель Windows начала показывать поле,
  // а мобильная — нет (или наоборот), тест падает раньше оператора.
  it('оба файла читают одни и те же поля снапшота', () => {
    const FIELDS = ['ping_ms', 'quote_age_ms', 'manual_net', 'vm_today', 'today_total',
      'chg_pct', 'ann_pct', 'started_ms', 'max_go', 'max_position', 'real_trades_today',
      'exit_only', 'fired_price', 'tp_offset', 'sl_offset', 'next_session', 'last_trade_ms'];
    for (const f of FIELDS) {
      expect(M.includes(f), `m.html не использует ${f}`).toBe(true);
      expect(WIN.includes(f), `companion.html не использует ${f}`).toBe(true);
    }
  });
});
