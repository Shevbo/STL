// Мини-график робота живёт внутри public/companion.html: страницу отдаёт прокси
// компаньона как есть, без сборщика, поэтому вынести функцию в модуль нельзя.
// Тест достаёт её из файла и проверяет геометрию — рисование по 320x64 руками
// слишком легко ломается молча.
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import vm from 'node:vm';
import { describe, expect, it } from 'vitest';

function loadMiniChart(page = 'public/companion.html') {
  const file = resolve(process.cwd(), page);
  const html = readFileSync(file, 'utf8').replace(/\r\n/g, '\n');
  const src = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)][0][1];
  const pick = (n: string) => {
    const m = src.match(new RegExp('function ' + n + '\\([\\s\\S]*?\\n\\}\\n'));
    if (!m) throw new Error('не нашёл ' + n);
    return m[0];
  };
  const consts = src.match(/const DENSITY = [^\n]*\n/)![0]
    + src.match(/const CH = \{[^\n]*\n/)![0]
    + pick('density')
    + src.match(/const r1 = [^\n]*\n/)![0]
    + pick('px')
    + 'const esc = (s) => String(s == null ? "" : s);\n';
  // density() читает localStorage; в vm его нет, подсовываем управляемую заглушку.
  const store: Record<string, string> = {};
  const ctx: any = { console, localStorage: {
    getItem: (k: string) => (k in store ? store[k] : null),
    setItem: (k: string, v: string) => { store[k] = v; },
  } };
  vm.createContext(ctx);
  vm.runInContext(consts + pick('chartStatus') + pick('miniChart')
    + ';this.miniChart=miniChart;this.CH=CH;this.setN=(n)=>localStorage.setItem("stl.chartN",String(n))', ctx);
  return ctx;
}

/** Шаг бара считается от ФАКТИЧЕСКОГО их числа, а не от выбранной плотности. */
const pitchFor = (n: number) => CH_W / n;
const CH_W = 320;

const { miniChart, CH } = loadMiniChart();

/** Ровные бары по минуте, close = open + step. */
function bars(n: number, base = 87000, t0 = 1_786_000_000) {
  return Array.from({ length: n }, (_, i) => ({
    t: t0 + i * 60, o: base + i, h: base + i + 5, l: base + i - 5, c: base + i + 1,
  }));
}
const rects = (svg: string) => [...svg.matchAll(/<rect class="cndl[^"]*" x="([-\d.]+)"/g)].map((m) => +m[1]);

describe('мини-график робота', () => {
  it('без баров рисует не пустой график, а причину', () => {
    const out = miniChart({ bars: [] });
    expect(out).not.toContain('<svg');
    expect(out).toContain('баров нет');
  });

  it('30 баров занимают всю ширину, свежий у правого края', () => {
    const svg = miniChart({ bars: bars(30) });
    const xs = rects(svg);
    expect(xs).toHaveLength(30);
    // центр последней свечи на половине шага от правого края
    expect(xs[xs.length - 1] + CH.body / 2).toBeCloseTo(CH_W - pitchFor(30) / 2, 1);
    expect(xs[0]).toBeGreaterThanOrEqual(-1);
  });

  it('неполный хвост тоже прижат вправо, а не растянут', () => {
    const xs = rects(miniChart({ bars: bars(7) }));
    expect(xs).toHaveLength(7);
    // семь баров растягиваются на всю ширину: шаг считается от их числа
    expect(xs[xs.length - 1] + CH.body / 2).toBeCloseTo(CH_W - pitchFor(7) / 2, 1);
  });

  it('плоская минута не даёт NaN', () => {
    const flat = [{ t: 1_786_000_000, o: 100, h: 100, l: 100, c: 100 }];
    const svg = miniChart({ bars: flat });
    expect(svg).toContain('<svg');
    expect(svg).not.toMatch(/NaN|Infinity/);
  });

  it('сделка садится на свою минуту', () => {
    const bs = bars(30);
    const svg = miniChart({
      bars: bs,
      fills: [{ ts: (bs[29].t) * 1000 + 5_000, side: 'buy', price: bs[29].c, qty: 2 }],
    });
    const poly = svg.match(/<polygon class="fill up" points="([-\d., ]+)"/);
    expect(poly, 'маркер покупки нарисован').toBeTruthy();
    const cx = +poly![1].split(',')[0];
    expect(cx).toBeCloseTo(CH_W - pitchFor(30) / 2, 1);
  });

  it('сделка вне окна графика не рисуется', () => {
    const bs = bars(30);
    const svg = miniChart({
      bars: bs, fills: [{ ts: (bs[0].t - 3600) * 1000, side: 'sell', price: bs[0].c, qty: 1 }],
    });
    expect(svg).not.toContain('<polygon');
  });

  it('заявка вне диапазона свечей прижимается к краю и показывает свою цену', () => {
    const bs = bars(30);                     // ~87000
    const svg = miniChart({ bars: bs, orders: [{ side: 'buy', price: 90_590, qty: 5 }] });
    const line = svg.match(/<line class="ord up" x1="0" y1="([-\d.]+)"/);
    expect(line).toBeTruthy();
    const y = +line![1];
    expect(y).toBeGreaterThanOrEqual(0);
    expect(y).toBeLessThanOrEqual(CH.h);
    expect(svg).toMatch(/▲|▼/);              // стрелка «заявка за краем»
    // цена через px(): разряды разделены УЗКИМ неразрывным пробелом, не обычным
    expect(svg.replace(/[\s  ]/g, ' ')).toContain('90 590');
  });

  it('заявка внутри диапазона рисуется без стрелки', () => {
    const bs = bars(30);
    const svg = miniChart({ bars: bs, orders: [{ side: 'sell', price: bs[15].c, qty: 3 }] });
    expect(svg).toContain('<line class="ord down"');
    expect(svg).not.toMatch(/▲|▼/);
  });

  it('всё, что нарисовано, лежит внутри холста и без мусора', () => {
    const bs = bars(30);
    const svg = miniChart({
      bars: bs, avg: bs[10].c,
      orders: [{ side: 'buy', price: 1, qty: 1 }, { side: 'sell', price: 999_999, qty: 1 }],
      fills: [{ ts: bs[5].t * 1000, side: 'sell', price: bs[5].c, qty: 1 }],
    });
    expect(svg).not.toMatch(/NaN|Infinity|undefined/);
    for (const m of svg.matchAll(/y1?="([-\d.]+)"/g)) {
      expect(+m[1]).toBeGreaterThanOrEqual(-5);
      expect(+m[1]).toBeLessThanOrEqual(CH.h + 5);
    }
  });

  it('плотность режет хвост и всегда занимает всю ширину', () => {
    const ctx = loadMiniChart();
    for (const n of [30, 60, 120, 200]) {
      ctx.setN(n);
      const xs = rects(ctx.miniChart({ bars: bars(200) }));
      expect(xs, `плотность ${n}`).toHaveLength(n);
      expect(xs[xs.length - 1]).toBeLessThan(CH_W);
      expect(xs[0]).toBeGreaterThanOrEqual(-1);
    }
  });

  it('на плотной сетке свеча вырождается в штрих, а не в отрицательную ширину', () => {
    const ctx = loadMiniChart();
    ctx.setN(200);
    const svg = ctx.miniChart({ bars: bars(200) });
    const widths = [...svg.matchAll(/<rect class="cndl[^"]*"[^>]*width="([-\d.]+)"/g)].map((m) => +m[1]);
    expect(Math.min(...widths)).toBeGreaterThanOrEqual(1);
    expect(Math.max(...widths)).toBeLessThanOrEqual(CH_W / 200);
  });

  it('статусная строка называет сигнал, и она ВНЕ холста', () => {
    const bs = bars(30);
    const t = (ch: any) => (miniChart({ bars: bs, ...ch }).match(/class="s [^"]*">([^<]*)</) || [])[1];
    expect(t({ signal_known: true, want: null })).toBe('сигнала нет');
    expect(t({ signal_known: true, want: 1 })).toBe('лонг');
    expect(t({ signal_known: true, want: -1 })).toBe('шорт');
    expect(t({ signal_known: true, want: 0 })).toBe('выйти в кэш');
    // сигнал не посчитан (напр. стратегия-модуль вне реестра) — не врём «нет»
    expect(t({ signal_known: false })).toBe('сигнал не рассчитан');
    // строка живёт ПОСЛЕ </svg>: свечи на неё налезать не могут
    const svg = miniChart({ bars: bs, signal_known: true, want: 1 });
    expect(svg.indexOf('mstat')).toBeGreaterThan(svg.indexOf('</svg>'));
  });

  it('статус показывается ВСЕГДА, даже когда на графике есть заявки и сделки', () => {
    const bs = bars(30);
    const svg = miniChart({ bars: bs, signal_known: true, want: -1,
      orders: [{ side: 'buy', price: bs[5].c, qty: 1 }],
      fills: [{ ts: bs[5].t * 1000, side: 'buy', price: bs[5].c, qty: 1 }] });
    expect(svg).toContain('mstat');
    expect(svg).toContain('шорт');
  });

  it('фильтр, держащий вход, назван коротко, а полная фраза — в подсказке', () => {
    const bs = bars(30);
    const full = 'долина смерти: коридор закрытий 34 пт за 20 баров (< 50)';
    const svg = miniChart({ bars: bs, signal_known: true, want: 1,
      blocked: full, blocked_tag: 'долина смерти' });
    expect(svg).toContain('>долина смерти<');
    expect(svg).toContain('title="' + full + '"');
    // без фильтра лишнего значка нет
    expect(miniChart({ bars: bs, signal_known: true, want: 1 })).not.toContain('class="blk"');
  });

  // Две панели считают одни и те же деньги, и мобильная УЖЕ молча отставала от
  // Windows-панели. Сверяем не глазами, а побайтово на одних данных.
  it('мобильная панель рисует то же самое, что и Windows-панель', () => {
    const mobile = loadMiniChart('public/m.html').miniChart;
    const bs = bars(30);
    const data = {
      bars: bs, avg: bs[9].c,
      orders: [{ side: 'buy', price: bs[3].l, qty: 5 }, { side: 'sell', price: 99_000, qty: 2 }],
      fills: [{ ts: bs[20].t * 1000, side: 'buy', price: bs[20].c, qty: 3 }],
    };
    expect(mobile(data)).toBe(miniChart(data));
    expect(mobile({ bars: [] })).toBe(miniChart({ bars: [] }));
  });
});
