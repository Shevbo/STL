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

/** Иконка кассеты — тоже общий для двух панелей кусок, тоже сверяем. */
function loadCassette(page = 'public/companion.html') {
  const file = resolve(process.cwd(), page);
  const src = readFileSync(file, 'utf8').replace(/\r\n/g, '\n');
  const m = src.match(/const cassette = \(playing\)[\s\S]*?;\n/);
  if (!m) throw new Error('не нашёл cassette в ' + page);
  const ctx: any = { console };
  vm.createContext(ctx);
  vm.runInContext(m[0] + ';this.cassette=cassette', ctx);
  return ctx.cassette as (p: boolean) => string;
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

  it('замерший график подписан, живой — нет', () => {
    const nowS = Math.floor(Date.now() / 1000);
    // свежая минута: подписи «замер» быть не должно
    const live = miniChart({ bars: bars(30, 87000, nowS - 29 * 60), signal_known: true, want: 1 });
    expect(live).not.toContain('frozen');
    // хвост вчерашней сессии: замерший график неотличим от живого без подписи
    const old = miniChart({ bars: bars(30, 87000, nowS - 8 * 3600), signal_known: true, want: 1 });
    expect(old).toContain('class="frozen"');
    expect(old).toMatch(/замер \d{2}:\d{2}/);
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

describe('иконка кассеты', () => {
  const cassette = loadCassette();

  it('это штриховой эскиз: ни одной заливки, фон панели виден насквозь', () => {
    const svg = cassette(true);
    // Прежняя иконка была «фотографией» — чёрный корпус fill:#17181a на светлой
    // полупрозрачной панели читался наклейкой. Заливки в разметке быть не должно.
    expect(svg).not.toMatch(/fill="(?!none)/);
    expect(svg).not.toMatch(/#[0-9a-fA-F]{3,6}/);
  });

  it('играет: катушки крутятся, ноты есть, zzz молчит', () => {
    const svg = cassette(true);
    expect(svg).not.toContain('stopped');
    expect(svg).toContain('class="hub"');
    expect(svg).toMatch(/♪|♫/);
    expect(svg).toContain('робот работает');
  });

  it('пауза: тот же рисунок, но с классом stopped — спят по CSS, не по разметке', () => {
    const svg = cassette(false);
    expect(svg).toContain('cass stopped');
    expect(svg).toContain('class="zzz z1"');
    expect(svg).toContain('на паузе');
    // разметка одна и та же: разное состояние — это класс, а не второй рисунок
    expect(svg.replace(' stopped', '')).toBe(cassette(true).replace(
      'робот работает: считает бары и выставляет заявки',
      'на паузе: новые заявки не выставляются, позиция остаётся'));
  });

  it('SVG разбирается парсером без ошибок', () => {
    const doc = new DOMParser().parseFromString(cassette(true), 'text/html');
    expect(doc.querySelector('parsererror')).toBeNull();
    expect(doc.querySelectorAll('.hub').length).toBe(2);
    expect(doc.querySelectorAll('.note').length).toBe(2);
    expect(doc.querySelectorAll('.zzz').length).toBe(3);
  });

  it('обе панели рисуют одну и ту же кассету', () => {
    const mobile = loadCassette('public/m.html');
    expect(mobile(true)).toBe(cassette(true));
    expect(mobile(false)).toBe(cassette(false));
  });
});

// ── Масштаб панели: пять шагов, как Ctrl +/− в редакторе ─────────────────────
// Растёт не только ширина: вёрстка остаётся 350-пиксельной, а увеличивает её
// zoom. Поэтому шаги обязаны начинаться ровно с исходных 350 и доходить до
// 1000, а множитель — выводиться из них, а не жить отдельной константой.
describe('шаги масштаба компаньона', () => {
  const src = readFileSync(resolve(process.cwd(), 'public/companion.html'), 'utf8');
  const sizes = JSON.parse(src.match(/const SIZES = (\[[^\]]*\])/)![1]);

  it('пять шагов от текущей ширины до 1000', () => {
    expect(sizes).toHaveLength(5);
    expect(sizes[0]).toBe(350);
    expect(sizes[4]).toBe(1000);
  });

  it('шаги строго растут и ложатся ровно', () => {
    for (let i = 1; i < sizes.length; i++) expect(sizes[i]).toBeGreaterThan(sizes[i - 1]);
    const gaps = sizes.slice(1).map((w: number, i: number) => w - sizes[i]);
    // ровный шаг: разброс между интервалами не больше 10 px
    expect(Math.max(...gaps) - Math.min(...gaps)).toBeLessThanOrEqual(10);
  });

  it('масштабируется ВСЯ панель через zoom, а не через transform', () => {
    // transform не меняет layout-бокс: высота, которую страница сообщает Go,
    // осталась бы от немасштабированной вёрстки и окно разъехалось бы.
    expect(src).toMatch(/document\.body\.style\.zoom/);
    expect(src).not.toMatch(/style\.transform\s*=\s*.scale/);
  });

  it('множитель считается от первого шага, а не задан отдельно', () => {
    expect(src).toMatch(/zoom\s*=\s*String\(w \/ SIZES\[0\]\)/);
  });

  it('ширина уходит в Go на КАЖДОЙ смене шага, а не однажды при старте', () => {
    expect(src).not.toMatch(/widthSet/);
    // ширина уходит из applySize при каждой смене шага (в физических пикселях)
    expect(src).toMatch(/lastW = w; shell\('\/width\?w='/);
  });
});

// ── Высота панели: ничего не обрезается ──────────────────────────────────────
// На большом масштабе содержимое перестаёт влезать в рабочую область экрана, Go
// зажимает высоту окна по ней, и всё ниже обрезалось наглухо (жалоба оператора
// 10.08.2026). Плюс мерить body в этот момент нельзя: он становится прокруткой
// и отдаёт ВИДИМУЮ часть, из-за чего панель замирает на обрезанной высоте.
describe('высота компаньона', () => {
  const src = readFileSync(resolve(process.cwd(), 'public/companion.html'), 'utf8');

  it('высота меряется по обёртке содержимого, а не по body', () => {
    expect(src).toMatch(/const el = \$\('panel'\)/);
    expect(src).not.toMatch(/document\.body\.getBoundingClientRect\(\)\.height/);
  });

  it('обёртка есть в разметке и несёт отступы панели', () => {
    expect(src).toMatch(/<div id="panel">/);
    expect(src).toMatch(/#panel \{ padding:/);
  });

  it('не влезло — прокрутка, а не обрезка', () => {
    expect(src).toMatch(/body \{ overflow-x: hidden; overflow-y: auto; \}/);
    // горизонтальной прокрутки быть не должно: ширину задаёт сама панель
    expect(src).not.toMatch(/body[^{]*\{[^}]*overflow-x: auto/);
  });
});

// ── Размеры окна уходят в ФИЗИЧЕСКИХ пикселях ───────────────────────────────
// Окно Go ставит в физических, а страница считает в CSS. На экране со 100%
// они совпадают, и ошибка пряталась; на 4К-портрете с системным масштабом окно
// выходило во столько же раз ниже, во сколько включено масштабирование Windows,
// и панель обрезалась. Перетаскивание уже умножало дельты на devicePixelRatio —
// оно и было единственным местом, где DPI учитывался.
describe('масштаб экрана в размерах окна', () => {
  const src = readFileSync(resolve(process.cwd(), 'public/companion.html'), 'utf8');

  it('и высота, и ширина проходят через пересчёт в физические', () => {
    expect(src).toMatch(/shell\('\/height\?h=' \+ phys\(h\)\)/);
    expect(src).toMatch(/shell\('\/width\?w=' \+ phys\(w\)\)/);
  });

  it('пересчёт берёт devicePixelRatio и не падает без него', () => {
    const fn = src.match(/const phys = [^\n]*\n/)![0];
    expect(fn).toContain('devicePixelRatio');
    expect(fn).toContain('|| 1');
  });
});
