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
    await open2(base({ buyhold_curve: [[1_790_000_000, 0], [1_790_100_000, 5_000]] }));
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
