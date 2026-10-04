// Витрина кампаний: что видит человек, когда данные есть, когда их нет и когда они
// неполные. Монтируем настоящий компонент с подставным API.
//
// Данных сборщика на момент написания ещё нет (формат — в спеке), поэтому тест
// заодно и единственное место, где страница видит карточки раньше, чем backtests
// выложат первый index.json.
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import CampaignShowcase from './CampaignShowcase.svelte';

// jsdom не знает ResizeObserver, а bind:clientWidth на графике его требует: без
// заглушки страница с кривой молча оставалась на «Загрузка…» (ошибка эффекта
// глотается), а карточки без кривой рисовались — так её и нашли.
(globalThis as any).ResizeObserver ??= class { observe() {} unobserve() {} disconnect() {} };

vi.mock('$lib/fetch-auth', () => ({ fetchWithAuth: (u: string) => (globalThis as any).__fetch(u) }));

const J = (body: unknown, status = 200) =>
  Promise.resolve(new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } }));
// Ответ API приходит через Response.json(), то есть через МАКРОзадачи: одних
// микротасков не хватает, и страница в тесте оставалась бы на «Загрузка…».
const tick = async () => { for (let i = 0; i < 12; i++) { await new Promise((r) => setTimeout(r, 0)); flushSync(); } };

const CARDS = [
  { slug: 'grid-r1', title: 'Радиация r1', idea: 'сетка в боковике', status: 'done', unit: 'rub',
    family: 'grid', rev: 1, kind: 'research',
    headline: { net: -1_200_000, trades: 340, max_dd: 1_500_000, window: '2026-01..2026-09' },
    thumb: [[1_790_000_000, 0], [1_790_100_000, 5000], [1_790_200_000, -8000]] },
  { slug: 'grid-r2', title: 'Радиация r2', idea: 'фильтр тренда', status: 'done', unit: 'rub',
    family: 'grid', rev: 2, kind: 'research',
    headline: { net: 40_000, trades: 120, max_dd: 90_000, window: '2026-01..2026-09' },
    thumb: [[1_790_000_000, 0], [1_790_100_000, 2000]] },
  { slug: 'macd-q', title: 'MACD очередь', status: 'queued', unit: 'rub', thumb: null,
    headline: { net: null, trades: null, max_dd: null, window: null } },
  { slug: 'nocurve', title: 'Без кривой', status: 'no_curve', unit: 'rub', thumb: null,
    no_curve_reason: 'перепрогон не делали', headline: { net: 10, trades: 3, max_dd: 1, window: null } },
];

let host: HTMLElement;
let app: any;

function api(map: Record<string, () => Promise<Response>>) {
  (globalThis as any).__fetch = (u: string) => (map[u] ?? (() => J({ detail: 'нет' }, 404)))();
}
async function open(path: string) {
  window.history.replaceState(null, '', path);
  host = document.createElement('div');
  document.body.appendChild(host);
  app = mount(CampaignShowcase, { target: host });
  await tick();
}

beforeEach(() => { document.body.innerHTML = ''; });
afterEach(() => { if (app) unmount(app); app = null; });

describe('витрина', () => {
  it('сетка карточек: каждая со своей ссылкой, свёрнуто до последней редакции линии', async () => {
    api({ '/api/v1/lab/showcase/campaigns': () => J({ available: true, campaigns: CARDS, built_at_ms: 1_790_000_000_000, reason: '' }) });
    await open('/backtest/campaigns');
    const cards = [...host.querySelectorAll('a.cs-card')] as HTMLAnchorElement[];
    // r1 свёрнута под r2 той же линии: по умолчанию видна только последняя редакция.
    expect(cards.map((a) => a.getAttribute('href'))).toEqual([
      '/backtest/campaigns/grid-r2', '/backtest/campaigns/macd-q', '/backtest/campaigns/nocurve']);
    expect(host.textContent).toContain('ред. 2 из 2');
  });

  it('показать все редакции возвращает свёрнутую', async () => {
    api({ '/api/v1/lab/showcase/campaigns': () => J({ available: true, campaigns: CARDS, built_at_ms: 1, reason: '' }) });
    await open('/backtest/campaigns');
    const chk = host.querySelector('input[type=checkbox]') as HTMLInputElement;
    chk.click(); await tick();
    expect(host.querySelectorAll('a.cs-card')).toHaveLength(4);
  });

  it('нет кривой — причина словами, а не нулевая линия', async () => {
    api({ '/api/v1/lab/showcase/campaigns': () => J({ available: true, campaigns: CARDS, built_at_ms: 1, reason: '' }) });
    await open('/backtest/campaigns');
    expect(host.textContent).toContain('перепрогон не делали');
    expect(host.textContent).toContain('ожидает прогона');
    // у карточек без кривой нет ни одной нарисованной линии
    const queued = [...host.querySelectorAll('a.cs-card')].find((a) => a.textContent?.includes('MACD очередь'))!;
    expect(queued.querySelectorAll('svg path.ln')).toHaveLength(0);
  });

  it('нет итога — прочерк, а не ноль', async () => {
    api({ '/api/v1/lab/showcase/campaigns': () => J({ available: true, campaigns: CARDS, built_at_ms: 1, reason: '' }) });
    await open('/backtest/campaigns');
    const queued = [...host.querySelectorAll('a.cs-card')].find((a) => a.textContent?.includes('MACD очередь'))!;
    const dd = [...queued.querySelectorAll('dd')].map((d) => d.textContent?.trim());
    expect(dd).toEqual(['—', '—', '—']);
  });

  it('витрина не собрана — причина, а не сетка пустых карточек', async () => {
    api({ '/api/v1/lab/showcase/campaigns': () => J({ available: false, campaigns: [], built_at_ms: null,
      reason: 'витрина ещё не собрана: сборщик не оставил index.json' }) });
    await open('/backtest/campaigns');
    expect(host.querySelectorAll('a.cs-card')).toHaveLength(0);
    expect(host.textContent).toContain('витрина ещё не собрана');
  });

  it('API недоступен — это называется, а не выглядит как пустая витрина', async () => {
    api({ '/api/v1/lab/showcase/campaigns': () => J({ detail: 'x' }, 500) });
    await open('/backtest/campaigns');
    expect(host.textContent).toContain('HTTP 500');
  });

  it('поиск сужает список и считает «N из M»', async () => {
    api({ '/api/v1/lab/showcase/campaigns': () => J({ available: true, campaigns: CARDS, built_at_ms: 1, reason: '' }) });
    await open('/backtest/campaigns');
    const q = host.querySelector('input[type=search]') as HTMLInputElement;
    q.value = 'очередь'; q.dispatchEvent(new Event('input', { bubbles: true })); await tick();
    expect(host.querySelectorAll('a.cs-card')).toHaveLength(1);
    expect(host.textContent).toContain('1 из 4');
  });
});

describe('пагинация', () => {
  // 04.10.2026 в настоящей витрине 542 карточки, у 507 кривой нет. Страница
  // обязана показывать порциями и честно говорить, сколько осталось.
  const MANY = Array.from({ length: 120 }, (_, i) =>
    ({ slug: `c-${i}`, title: `Кампания ${i}`, status: 'no_curve', unit: 'points', thumb: null,
       headline: { net: null, trades: null, max_dd: null, window: null } }));

  it('первая страница 48, дальше «показать ещё» с честным остатком', async () => {
    api({ '/api/v1/lab/showcase/campaigns': () => J({ available: true, campaigns: MANY, built_at_ms: 1, reason: '' }) });
    await open('/backtest/campaigns');
    expect(host.querySelectorAll('a.cs-card')).toHaveLength(48);
    expect(host.textContent).toContain('показано 48 из 120');
    const more = [...host.querySelectorAll('button')].find((b) => b.textContent?.includes('показать ещё'))!;
    more.click(); await tick();
    expect(host.querySelectorAll('a.cs-card')).toHaveLength(96);
    (([...host.querySelectorAll('button')].find((b) => b.textContent?.includes('показать ещё'))) as HTMLElement).click();
    await tick();
    expect(host.querySelectorAll('a.cs-card')).toHaveLength(120);
    expect([...host.querySelectorAll('button')].some((b) => b.textContent?.includes('показать ещё'))).toBe(false);
  });

  it('смена поиска возвращает на первую страницу', async () => {
    api({ '/api/v1/lab/showcase/campaigns': () => J({ available: true, campaigns: MANY, built_at_ms: 1, reason: '' }) });
    await open('/backtest/campaigns');
    ([...host.querySelectorAll('button')].find((b) => b.textContent?.includes('показать ещё')) as HTMLElement).click();
    await tick();
    expect(host.querySelectorAll('a.cs-card')).toHaveLength(96);
    const q = host.querySelector('input[type=search]') as HTMLInputElement;
    q.value = 'Кампания 1'; q.dispatchEvent(new Event('input', { bubbles: true })); await tick();
    // «Кампания 1», 10-19 и 100-119 — 31 штука, все влезают на одну страницу
    expect(host.querySelectorAll('a.cs-card')).toHaveLength(31);
  });

  it('мало карточек — кнопки нет', async () => {
    api({ '/api/v1/lab/showcase/campaigns': () => J({ available: true, campaigns: MANY.slice(0, 10), built_at_ms: 1, reason: '' }) });
    await open('/backtest/campaigns');
    expect([...host.querySelectorAll('button')].some((b) => b.textContent?.includes('показать ещё'))).toBe(false);
  });
});

describe('отчёт', () => {
  const REPORT = {
    slug: 'grid-r2', title: 'Радиация r2', idea: 'фильтр тренда', status: 'done', unit: 'rub',
    family: 'grid', rev: 2, verdict: 'плюса нет',
    leaders: [
      { rank: 1, params: { step: 90 }, metrics: { net: 40_000, max_dd: 90_000, win_rate: 0.51, ulcer_idx: 7.5 },
        trades_n: 120, curve: [[1_790_000_000, 0], [1_790_100_000, 5000], [1_790_200_000, -3000]] },
      { rank: 2, params: { step: 50 }, metrics: { net: -2000, max_dd: 50_000, win_rate: 0.4, ulcer_idx: 9 },
        trades_n: 90, curve: null },
    ],
    revisions: [{ slug: 'grid-r1', rev: 1, changes: null }, { slug: 'grid-r2', rev: 2, changes: 'добавили фильтр тренда' }],
    runs: ['camp-1'], data_window: { from: '2026-01-01', to: '2026-09-30', symbols: ['RIZ6'] },
  };
  const LIST = () => J({ available: true, campaigns: CARDS, built_at_ms: 1, reason: '' });

  it('открывается сразу в развёрнутом виде по своему URL', async () => {
    api({ '/api/v1/lab/showcase/campaigns': LIST, '/api/v1/lab/showcase/campaigns/grid-r2': () => J(REPORT) });
    await open('/backtest/campaigns/grid-r2');
    expect(host.textContent).toContain('Радиация r2');
    expect(host.textContent).toContain('плюса нет');
    // большой график: у лидера №1 есть и зелёный, и красный кусок
    expect(host.querySelectorAll('svg path.area.pos').length).toBeGreaterThan(0);
    expect(host.querySelectorAll('svg path.area.neg').length).toBeGreaterThan(0);
  });

  it('колонки метрик берутся из ДАННЫХ: новый ключ не пропадает', async () => {
    api({ '/api/v1/lab/showcase/campaigns': LIST, '/api/v1/lab/showcase/campaigns/grid-r2': () => J(REPORT) });
    await open('/backtest/campaigns/grid-r2');
    const heads = [...host.querySelectorAll('thead th')].map((t) => t.textContent);
    expect(heads).toContain('Net');
    expect(heads).toContain('Просадка');
    expect(heads).toContain('Win rate');       // известный ключ — человеческим именем
    expect(heads).toContain('ulcer_idx');      // незнакомый печатается как есть, а не пропадает
  });

  it('у лидера без кривой — причина, а не пустая рамка', async () => {
    api({ '/api/v1/lab/showcase/campaigns': LIST, '/api/v1/lab/showcase/campaigns/grid-r2': () => J(REPORT) });
    await open('/backtest/campaigns/grid-r2');
    (host.querySelectorAll('tbody tr')[1] as HTMLElement).click(); await tick();
    expect(host.querySelector('.cc-empty')?.textContent).toContain('у этого лидера кривой нет');
  });

  it('цепочка редакций с описанием изменений', async () => {
    api({ '/api/v1/lab/showcase/campaigns': LIST, '/api/v1/lab/showcase/campaigns/grid-r2': () => J(REPORT) });
    await open('/backtest/campaigns/grid-r2');
    expect(host.textContent).toContain('добавили фильтр тренда');
    expect(host.querySelectorAll('.cs-chain li')).toHaveLength(2);
  });

  it('неизвестная кампания — 404 словами, а не пустой отчёт', async () => {
    api({ '/api/v1/lab/showcase/campaigns': LIST });
    await open('/backtest/campaigns/nope');
    expect(host.textContent).toContain('Такой кампании нет');
  });

  it('ID окна несёт полный URL для копирования', async () => {
    api({ '/api/v1/lab/showcase/campaigns': LIST, '/api/v1/lab/showcase/campaigns/grid-r2': () => J(REPORT) });
    await open('/backtest/campaigns/grid-r2');
    expect(host.querySelector('.screen-tag')?.textContent).toContain('CAMPAIGN-grid-r2');
  });
});

describe('рабочее место: купил и держи, честный объём, редакции', () => {
  const LIST = () => J({ available: true, campaigns: CARDS, built_at_ms: 1, reason: '' });
  const base = (extra: Record<string, unknown> = {}, leader: Record<string, unknown> = {}) => ({
    slug: 'grid-r2', title: 'Радиация r2', status: 'done', unit: 'rub', family: 'grid', rev: 2,
    leaders: [{ rank: 1, params: {}, metrics: { net: 40_000 }, trades_n: 1,
      curve: [[1_790_000_000, 0], [1_790_050_000, 10_000], [1_790_100_000, 20_000]], ...leader }],
    revisions: [{ slug: 'grid-r1', rev: 1, changes: null }, { slug: 'grid-r2', rev: 2, changes: 'x' }],
    ...extra,
  });
  const open2 = async (rep: unknown, path = '/backtest/campaigns/grid-r2') => {
    api({ '/api/v1/lab/showcase/campaigns': LIST, '/api/v1/lab/showcase/campaigns/grid-r2': () => J(rep),
          '/api/v1/lab/showcase/campaigns/grid-r1': () => J({ ...base(), slug: 'grid-r1', rev: 1, title: 'Радиация r1' }) });
    await open(path);
  };

  it('нет buyhold_curve — говорим об этом вслух, разности нет', async () => {
    await open2(base());
    expect(host.textContent).toContain('buyhold_curve');
    expect(host.querySelector('path.hold')).toBeNull();
    expect(host.textContent).not.toContain('минус «купил и держи»');
  });

  it('есть buyhold_curve — линия на графике и второй ряд с разностью', async () => {
    // «Купил и держи» ПО-ЛИДЕРСКИ (ответ backtests 04.10.2026): он считается на полный
    // объём лидера, а контрактов у лидеров разное.
    await open2(base({}, { buyhold_curve: [[1_790_000_000, 0], [1_790_100_000, 5_000]] }));
    expect(host.querySelector('path.hold')).not.toBeNull();
    expect(host.textContent).toContain('минус «купил и держи»');
    expect(host.textContent).not.toContain('Линии «купил и держи» нет');
  });

  it('честный объём: нет полей — прочерки и названия, а не нули', async () => {
    await open2(base());
    const vol = host.querySelector('.cs-vol')!;
    const dd = [...vol.querySelectorAll('dd')].map((d) => d.textContent?.trim());
    expect(dd[0]).toBe('—');           // контрактов
    expect(dd[1]).toBe('—');           // полная стоимость
    expect(dd[3]).toBe('—');           // доходность
    expect(host.textContent).toContain('contracts_peak, full_cost_rub, return_pct');
  });

  it('честный объём: поля есть — печатаются как отданы', async () => {
    await open2(base({}, { contracts_peak: 3, full_cost_rub: 750_000, return_pct: 5.3 }));
    const txt = host.querySelector('.cs-vol')!.textContent!;
    expect(txt).toContain('3');
    expect(txt).toMatch(/750\s?000/);
    expect(txt).toContain('5,3');
    expect(host.textContent).not.toContain('Сборщик ещё не отдаёт');
  });

  it('кнопки движка заблокированы и объясняют ПОЧЕМУ', async () => {
    await open2(base());
    const btns = [...host.querySelectorAll('.cs-actions button')] as HTMLButtonElement[];
    expect(btns.map((b) => b.textContent)).toEqual([
      'Нормализовать объём до 1 млн', 'Создать новую редакцию', 'Запустить прогон']);
    for (const b of btns) { expect(b.disabled).toBe(true); expect(b.title.length).toBeGreaterThan(20); }
  });

  it('/rev/N открывает ту редакцию линии, которую просили', async () => {
    await open2(base(), '/backtest/campaigns/grid-r2/rev/1');
    expect(window.location.pathname).toBe('/backtest/campaigns/grid-r1');
    expect(host.textContent).toContain('Радиация r1');
  });

  it('несуществующая редакция — сказано словами, показана текущая', async () => {
    await open2(base(), '/backtest/campaigns/grid-r2/rev/9');
    expect(host.textContent).toContain('Редакции 9 в этой линии нет');
    expect(host.textContent).toContain('Радиация r2');
  });
});

describe('данные 04.10.2026: редиректы, ленивые кривые, перебор за чипом, таблица', () => {
  const LISTX = (cards: unknown[]) => () => J({ available: true, campaigns: cards, built_at_ms: 1, reason: '' });
  const REP = (o: Record<string, unknown> = {}) => ({
    slug: 'new-slug', title: 'Новая', status: 'done', unit: 'rub', rev: 1,
    leaders: [
      { rank: 1, rev: 1, contracts_peak: 2, full_cost_rub: 400000, return_pct: 3.1,
        metrics: { net: 12000, rf: 2.5, l_share: 0.7, score: 21000, lb_net: 15000 },
        curve: [[1_790_000_000, 0], [1_790_100_000, 5000], [1_790_200_000, 12000]], buyhold_curve: null },
      { rank: 2, metrics: { net: 9000, rf: 4, l_share: 0.5, score: 18000 }, curve: null,
        curve_url: 'new-slug.leader-2.json' },
      { rank: 3, metrics: { net: 3000 }, curve: null },
    ],
    ...o,
  });

  it('старый slug: URL переписывается на новый, ссылка жива', async () => {
    api({ '/api/v1/lab/showcase/campaigns': LISTX([]),
          '/api/v1/lab/showcase/campaigns/old-slug': () => J({ ...REP(), redirected_from: 'old-slug' }) });
    await open('/backtest/campaigns/old-slug');
    expect(window.location.pathname).toBe('/backtest/campaigns/new-slug');
    expect(host.textContent).toContain('Новая');
  });

  it('кривая лидера вне топ-10 подгружается по клику', async () => {
    let asked = 0;
    api({ '/api/v1/lab/showcase/campaigns': LISTX([]),
          '/api/v1/lab/showcase/campaigns/new-slug': () => J(REP()),
          '/api/v1/lab/showcase/campaigns/new-slug/leaders/2': () => { asked++; return J({ rank: 2,
            curve: [[1_790_000_000, 0], [1_790_100_000, -4000], [1_790_200_000, 9000]], buyhold_curve: null }); } });
    await open('/backtest/campaigns/new-slug');
    expect(asked).toBe(0);                                   // сразу ничего лишнего не тянем
    ([...host.querySelectorAll('tbody tr')].find((r) => r.textContent?.startsWith('2')) as HTMLElement).click();
    await tick();
    expect(asked).toBe(1);
    expect(host.querySelectorAll('svg path.area.neg').length).toBeGreaterThan(0);   // у кривой №2 есть минус
  });

  it('лидер без кривой и без curve_url — причина, а не запрос в пустоту', async () => {
    let asked = 0;
    api({ '/api/v1/lab/showcase/campaigns': LISTX([]),
          '/api/v1/lab/showcase/campaigns/new-slug': () => J(REP()),
          '/api/v1/lab/showcase/campaigns/new-slug/leaders/3': () => { asked++; return J({}, 404); } });
    await open('/backtest/campaigns/new-slug');
    ([...host.querySelectorAll('tbody tr')].find((r) => r.textContent?.startsWith('3')) as HTMLElement).click();
    await tick();
    expect(asked).toBe(0);
    expect(host.querySelector('.cc-empty')?.textContent).toContain('кривой нет');
  });

  it('колонки топ-100: rf, net, L, score и честный объём', async () => {
    api({ '/api/v1/lab/showcase/campaigns': LISTX([]), '/api/v1/lab/showcase/campaigns/new-slug': () => J(REP()) });
    await open('/backtest/campaigns/new-slug');
    const heads = [...host.querySelectorAll('thead th')].map((t) => t.textContent?.replace(/[ ▲▼]/g, ''));
    for (const h of ['Ред.', 'Контрактов', 'Полнаястоимость', 'Net', 'Доходность', 'RF', 'L', 'RF×net×L']) {
      expect(heads, h).toContain(h);
    }
    expect(host.querySelector('tbody tr')!.textContent).toContain('70');       // L = 0.7 → 70 %
  });

  it('клик по заголовку сортирует, пустые в конце', async () => {
    api({ '/api/v1/lab/showcase/campaigns': LISTX([]), '/api/v1/lab/showcase/campaigns/new-slug': () => J(REP()) });
    await open('/backtest/campaigns/new-slug');
    const th = [...host.querySelectorAll('thead th')].find((t) => t.textContent?.startsWith('RF') && !t.textContent.includes('×')) as HTMLElement;
    th.click(); await tick();
    const first = () => host.querySelector('tbody tr td')!.textContent;
    expect(first()).toBe('2');           // rf 4 — наибольший
    th.click(); await tick();
    expect(host.querySelector('tbody tr:last-child td')!.textContent).toBe('3');   // rf пуст — всегда внизу
  });

  it('перепрогон на текущем движке: звёздочка у net и пояснение', async () => {
    api({ '/api/v1/lab/showcase/campaigns': LISTX([]), '/api/v1/lab/showcase/campaigns/new-slug': () => J(REP()) });
    await open('/backtest/campaigns/new-slug');
    expect(host.querySelector('sup.rerun')).not.toBeNull();
    expect(host.textContent).toContain('перепрогон на текущем движке');
  });

  it('перебор без кривой скрыт чипом, исследование и идущее — нет', async () => {
    const cards = [
      { slug: 'r', title: 'Исследование без кривой', status: 'no_curve', kind: 'research', unit: 'rub', thumb: null },
      { slug: 'o1', title: 'Перебор с кривой', status: 'done', kind: 'optimizer', unit: 'points',
        thumb: [[1_790_000_000, 0], [1_790_100_000, 5]] },
      { slug: 'o2', title: 'Перебор пустой', status: 'no_curve', kind: 'optimizer', unit: 'points', thumb: null },
      { slug: 'o3', title: 'Перебор идёт', status: 'running', kind: 'optimizer', unit: 'points', thumb: null,
        progress: { finished: 1, total: 4 } },
    ];
    api({ '/api/v1/lab/showcase/campaigns': LISTX(cards) });
    await open('/backtest/campaigns');
    const titles = () => [...host.querySelectorAll('a.cs-card h2')].map((h) => h.textContent);
    expect(titles()).toEqual(['Исследование без кривой', 'Перебор с кривой', 'Перебор идёт']);
    expect(host.textContent).toContain('показывать без кривой (1)');
    const chk = [...host.querySelectorAll('input[type=checkbox]')]
      .find((i) => i.parentElement?.textContent?.includes('без кривой')) as HTMLInputElement;
    chk.click(); await tick();
    expect(titles()).toHaveLength(4);
  });
});

describe('разные единицы у лидеров одной кампании', () => {
  // Настоящие отчёты 04.10.2026: у лидера своё поле unit, в десяти кампаниях они
  // разные. Подписать пункты рублями или отсортировать их вместе — это ложь.
  const MIX = {
    slug: 'mix', title: 'Смесь', status: 'done', unit: 'rub',
    leaders: [
      { rank: 1, unit: 'rub', metrics: { net: 5000, rf: 2 }, curve: [[1_790_000_000, 0], [1_790_100_000, 5000]] },
      { rank: 2, unit: 'points', metrics: { net: 90000, rf: 3 },
        curve: [[1_790_000_000, 0], [1_790_100_000, 90000]] },
    ],
  };
  const openMix = async () => {
    api({ '/api/v1/lab/showcase/campaigns': () => J({ available: true, campaigns: [], built_at_ms: 1, reason: '' }),
          '/api/v1/lab/showcase/campaigns/mix': () => J(MIX) });
    await open('/backtest/campaigns/mix');
  };

  it('каждая ячейка net подписана СВОЕЙ единицей', async () => {
    await openMix();
    const nets = [...host.querySelectorAll('tbody tr')].map((r) => r.textContent);
    expect(nets[0]).toContain('₽');
    expect(nets[1]).toContain('п.');
    expect(nets[1]).not.toContain('₽');
  });

  it('предупреждение о разных единицах', async () => {
    await openMix();
    expect(host.textContent).toContain('разные единицы');
  });

  it('денежные колонки не сортируются, неденежные — да', async () => {
    await openMix();
    const th = (name: string) => [...host.querySelectorAll('thead th')].find((t) => t.textContent?.startsWith(name)) as HTMLElement;
    th('Net').click(); await tick();
    expect(host.querySelector('tbody tr td')!.textContent).toBe('1');      // порядок не тронут
    th('RF').click(); await tick();
    expect(host.querySelector('tbody tr td')!.textContent).toBe('2');      // rf 3 > 2, сортируется
  });

  it('единицы одинаковые — предупреждения нет', async () => {
    api({ '/api/v1/lab/showcase/campaigns': () => J({ available: true, campaigns: [], built_at_ms: 1, reason: '' }),
          '/api/v1/lab/showcase/campaigns/mix': () => J({ ...MIX, leaders: MIX.leaders.map((l) => ({ ...l, unit: 'rub' })) }) });
    await open('/backtest/campaigns/mix');
    expect(host.textContent).not.toContain('разные единицы');
  });
});

describe('единица не определена и источник L (данные 04.10.2026)', () => {
  // backtests опровергли эвристику «единица по point_value» (ошибалась в 47%): единица
  // теперь только измеренная, у остальных unit = null.
  const NULLU = {
    slug: 'nu', title: 'Без единицы', status: 'done', unit: null,
    leaders: [
      { rank: 1, unit: null, metrics: { net: 5000, rf: 2 }, curve: [[1_790_000_000, 0], [1_790_100_000, 5000]] },
      { rank: 2, unit: null, metrics: { net: 9000, rf: 3 }, curve: null },
    ],
  };
  const openRep = async (rep: unknown, slug = 'nu') => {
    api({ '/api/v1/lab/showcase/campaigns': () => J({ available: true, campaigns: [], built_at_ms: 1, reason: '' }),
          [`/api/v1/lab/showcase/campaigns/${slug}`]: () => J(rep) });
    await open(`/backtest/campaigns/${slug}`);
  };

  it('единица null: число БЕЗ единицы, а не подписанное единицей карточки', async () => {
    // У карточки unit rub, но у лидера поле есть и равно null: подставлять рубли нельзя.
    await openRep({ ...NULLU, unit: 'rub' });
    const row = host.querySelector('tbody tr')!.textContent!;
    expect(row).not.toContain('₽');
    expect(row).not.toContain('п.');
    expect(host.textContent).toContain('Единица не определена');
  });

  it('единица null: денежные колонки не сортируются, неденежные — да', async () => {
    await openRep(NULLU);
    const th = (n: string) => [...host.querySelectorAll('thead th')].find((t) => t.textContent?.startsWith(n)) as HTMLElement;
    th('Net').click(); await tick();
    expect(host.querySelector('tbody tr td')!.textContent).toBe('1');
    th('RF').click(); await tick();
    expect(host.querySelector('tbody tr td')!.textContent).toBe('2');
  });

  it('старый сборщик (поля unit у лидера нет) — единица карточки, как раньше', async () => {
    await openRep({ ...NULLU, unit: 'rub', leaders: NULLU.leaders.map(({ unit: _u, ...l }) => l) });
    expect(host.querySelector('tbody tr')!.textContent).toContain('₽');
    expect(host.textContent).not.toContain('Единица не определена');
  });

  const LS = {
    slug: 'ls', title: 'Источники L', status: 'done', unit: 'rub',
    leaders: [
      { rank: 1, unit: 'rub', l_share_source: 'curve', metrics: { net: 5000, l_share: 0.8, score: 100 },
        curve: [[1_790_000_000, 0], [1_790_100_000, 5000]] },
      { rank: 2, unit: 'rub', l_share_source: 'leaderboard_windows', metrics: { net: 4000, l_share: 0.9, score: 300 },
        curve: null },
    ],
  };

  it('смесь источников L: пометка ≈ у оценки и предупреждение', async () => {
    await openRep(LS, 'ls');
    const rows = [...host.querySelectorAll('tbody tr')];
    expect(rows[0].querySelector('.est')).toBeNull();           // измерено — без значка
    expect(rows[1].querySelector('.est')).not.toBeNull();       // оценка — со значком
    expect(host.textContent).toContain('получена по-разному');
  });

  it('смесь источников L: сортировка по score отключена', async () => {
    await openRep(LS, 'ls');
    const th = [...host.querySelectorAll('thead th')].find((t) => t.textContent?.startsWith('RF×net×L')) as HTMLElement;
    th.click(); await tick();
    expect(host.querySelector('tbody tr td')!.textContent).toBe('1');        // порядок не тронут (score №2 больше)
  });

  it('один источник — значков и предупреждения нет', async () => {
    await openRep({ ...LS, leaders: LS.leaders.map((l) => ({ ...l, l_share_source: 'curve' })) }, 'ls');
    expect(host.querySelector('.est')).toBeNull();
    expect(host.textContent).not.toContain('получена по-разному');
  });

  it('карточка витрины: net без единицы помечен вопросом', async () => {
    const card = { slug: 'k', title: 'K', status: 'done', kind: 'research', unit: null, thumb: null,
      headline: { net: 1234, trades: 5, max_dd: 10, window: null } };
    api({ '/api/v1/lab/showcase/campaigns': () => J({ available: true, campaigns: [card], built_at_ms: 1, reason: '' }) });
    await open('/backtest/campaigns');
    expect(host.querySelector('a.cs-card sup.unk')).not.toBeNull();
    expect(host.querySelector('a.cs-card')!.textContent).not.toContain('₽');
  });
});

describe('перепрогон не запускался и несравнимый net', () => {
  // backtests 04.10.2026: перепрогоны сделаны только для 20 отобранных карточек,
  // у остальных лидеров объёма и кривой нет, и само они не досчитаются. Это не
  // сбой сборщика — подпись другая.
  const NR = {
    slug: 'nr', title: 'Без перепрогона', status: 'done', unit: null,
    leaders: [
      { rank: 1, unit: null, unit_source: null, metrics: { net: 5000, rf: 2 }, curve: null },
    ],
  };
  const openRep = async (rep: unknown) => {
    api({ '/api/v1/lab/showcase/campaigns': () => J({ available: true, campaigns: [], built_at_ms: 1, reason: '' }),
          '/api/v1/lab/showcase/campaigns/nr': () => J(rep) });
    await open('/backtest/campaigns/nr');
  };

  it('у неперепрогнанного лидера причина — «перепрогон не запускался», а не «сборщик не отдал»', async () => {
    await openRep(NR);
    expect(host.textContent).toContain('перепрогон не запускался: объёма и кривой нет');
    expect(host.textContent).not.toContain('Сборщик ещё не отдаёт');
  });

  it('прочерк в колонке объёма несёт ту же причину в подсказке', async () => {
    // Колонка объёма видна, когда он есть хотя бы у одного лидера (перепрогнанного);
    // у неперепрогнанного в ней прочерк с причиной.
    await openRep({ ...NR, leaders: [
      { rank: 1, unit: 'rub', unit_source: 'measured', contracts_peak: 3, full_cost_rub: 750000,
        metrics: { net: 9000 }, curve: null },
      { ...NR.leaders[0], rank: 2, contracts_peak: null, full_cost_rub: null },
    ] });
    const tds = [...host.querySelectorAll('tbody td')].filter((t) => t.getAttribute('title')?.includes('перепрогон не запускался'));
    expect(tds.length).toBeGreaterThan(0);
    expect(tds[0].textContent).toBe('—');
  });

  it('поля unit_source нет вовсе (старый сборщик) — прежняя подпись «сборщик не отдал»', async () => {
    const { unit_source: _s, ...bare } = NR.leaders[0];
    await openRep({ ...NR, leaders: [bare] });
    expect(host.textContent).toContain('Сборщик ещё не отдаёт');
    expect(host.textContent).not.toContain('перепрогон не запускался: объёма');
  });

  it('карточка с comparable=false: net без цветового выделения, с вопросом', async () => {
    const card = { slug: 'c', title: 'C', status: 'done', kind: 'research', unit: 'rub', thumb: null,
      headline: { net: 9999, trades: 5, max_dd: 10, window: null, comparable: false } };
    api({ '/api/v1/lab/showcase/campaigns': () => J({ available: true, campaigns: [card], built_at_ms: 1, reason: '' }) });
    await open('/backtest/campaigns');
    const dd = host.querySelector('a.cs-card dl dd')!;
    expect(dd.className).not.toContain('pos');       // положительный net не краснеет и не зеленеет
    expect(host.querySelector('a.cs-card sup.unk')).not.toBeNull();
  });

  it('comparable=true: net выделяется как раньше', async () => {
    const card = { slug: 'c', title: 'C', status: 'done', kind: 'research', unit: 'rub', thumb: null,
      headline: { net: 9999, trades: 5, max_dd: 10, window: null, comparable: true } };
    api({ '/api/v1/lab/showcase/campaigns': () => J({ available: true, campaigns: [card], built_at_ms: 1, reason: '' }) });
    await open('/backtest/campaigns');
    expect(host.querySelector('a.cs-card dl dd')!.className).toContain('pos');
  });
});
