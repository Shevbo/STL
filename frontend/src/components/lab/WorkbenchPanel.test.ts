// Панель рабочего места: что видит оператор, когда воркер жив, молчит, и когда редакция готова.
//
// Монтируем настоящий компонент с подставным API. Правила на сервере (tests/api/
// test_lab_workbench.py); здесь проверяется, что экран ГОВОРИТ ПРИЧИНУ, когда кнопка
// не работает, показывает чужой текст только как текст и не даёт принять то, что
// принимать нельзя.
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import WorkbenchPanel from './WorkbenchPanel.svelte';

vi.mock('$lib/fetch-auth', () => ({ fetchWithAuth: (u: string, o?: any) => (globalThis as any).__fetch(u, o) }));

const J = (body: unknown, status = 200) =>
  Promise.resolve(new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } }));
const tick = async () => { for (let i = 0; i < 12; i++) { await new Promise((r) => setTimeout(r, 0)); flushSync(); } };

const B = '/api/v1/lab/workbench';
let host: HTMLElement;
let app: any;
let calls: { url: string; opts?: any }[] = [];

function api(map: Record<string, (o?: any) => Promise<Response>>) {
  calls = [];
  (globalThis as any).__fetch = (u: string, o?: any) => {
    calls.push({ url: u, opts: o });
    return (map[u] ?? (() => J({ detail: 'нет' }, 404)))(o);
  };
}
async function open(card = 'c1') {
  host = document.createElement('div');
  document.body.appendChild(host);
  app = mount(WorkbenchPanel, { target: host, props: { card } });
  await tick();
}
const W = (o: any = {}) => ({ alive: true, age_s: 10, busy_with: null, version: 'v1', worker_id: 'w1', ...o });
const status = (w: any, ops = true) => () => J({ worker: w, operators_configured: ops });
const list = (revisions: any[]) => () => J({ revisions });
const btn = (text: string) => [...host.querySelectorAll('button')].find((b) => b.textContent?.includes(text)) as HTMLButtonElement;

beforeEach(() => { document.body.innerHTML = ''; });
afterEach(() => { if (app) unmount(app); app = null; });

describe('создание редакции', () => {
  it('воркер жив — кнопка активна', async () => {
    api({ [`${B}/status`]: status(W()), [`${B}/cards/c1/revisions`]: list([]) });
    await open();
    expect(host.textContent).toContain('воркер жив');
    expect(btn('Создать новую редакцию').disabled).toBe(false);
    expect(host.textContent).toContain('Редакций пока нет');
  });

  it('воркер молчит — кнопка заблокирована и ПРИЧИНА написана рядом', async () => {
    // Без воркера редакция встала бы в очередь и выглядела «в работе».
    api({ [`${B}/status`]: status(W({ alive: false, age_s: 420 })), [`${B}/cards/c1/revisions`]: list([]) });
    await open();
    expect(btn('Создать новую редакцию').disabled).toBe(true);
    expect(host.querySelector('.wb-why')!.textContent).toContain('не отвечает');
  });

  it('воркер ни разу не появлялся — об этом и сказано', async () => {
    api({ [`${B}/status`]: status(W({ alive: false, age_s: null })), [`${B}/cards/c1/revisions`]: list([]) });
    await open();
    expect(host.textContent).toContain('ещё ни разу не выходил на связь');
  });

  it('в карточке уже есть редакция в работе — создать нельзя', async () => {
    api({ [`${B}/status`]: status(W()), [`${B}/cards/c1/revisions`]: list([
      { id: 1, card: 'c1', rev: 2, parent: 1, status: 'working', message: 'правка', created_at: 1 }]) });
    await open();
    expect(btn('Создать новую редакцию').disabled).toBe(true);
    expect(host.querySelector('.wb-why')!.textContent).toContain('редакция 2');
  });

  it('отправка: POST с сообщением и номером последней редакции, форма закрывается', async () => {
    let sent: any = null;
    api({ [`${B}/status`]: status(W()),
          [`${B}/cards/c1/revisions`]: (o) => (o?.method === 'POST'
            ? (sent = JSON.parse(o.body), J({ id: 5, rev: 3, status: 'queued' }))
            : J({ revisions: [{ id: 4, card: 'c1', rev: 2, parent: 1, status: 'accepted', message: 'старая' }] })),
          [`${B}/cards/c1/revisions/3`]: () => J({ id: 5, card: 'c1', rev: 3, status: 'queued', message: 'добавь фильтр', log: '' }) });
    await open();
    btn('Создать новую редакцию').click(); await tick();
    const ta = host.querySelector('textarea') as HTMLTextAreaElement;
    ta.value = 'добавь фильтр'; ta.dispatchEvent(new Event('input', { bubbles: true })); await tick();
    btn('отправить воркеру').click(); await tick();
    expect(sent).toEqual({ message: 'добавь фильтр', parent: 2 });
    expect(host.querySelector('textarea')).toBeNull();
  });

  it('пустое сообщение отправить нельзя', async () => {
    api({ [`${B}/status`]: status(W()), [`${B}/cards/c1/revisions`]: list([]) });
    await open();
    btn('Создать новую редакцию').click(); await tick();
    expect(btn('отправить воркеру').disabled).toBe(true);
    expect(host.textContent).toContain('Опишите');
  });

  it('отказ сервера показан ЕГО текстом', async () => {
    api({ [`${B}/status`]: status(W()),
          [`${B}/cards/c1/revisions`]: (o) => (o?.method === 'POST'
            ? J({ detail: { code: 'busy', text: 'В карточке уже есть редакция 1 в работе (working).' } }, 409)
            : J({ revisions: [] })) });
    await open();
    btn('Создать новую редакцию').click(); await tick();
    const ta = host.querySelector('textarea') as HTMLTextAreaElement;
    ta.value = 'правка'; ta.dispatchEvent(new Event('input', { bubbles: true })); await tick();
    btn('отправить воркеру').click(); await tick();
    expect(host.textContent).toContain('В карточке уже есть редакция 1 в работе');
  });
});

describe('недоступное рабочее место', () => {
  it('сервер не ответил — так и сказано, а не пустой список редакций', async () => {
    api({ [`${B}/status`]: () => J({ detail: 'x' }, 503), [`${B}/cards/c1/revisions`]: () => J({ detail: 'x' }, 503) });
    await open();
    expect(host.textContent).toContain('Рабочее место недоступно');
    expect(host.textContent).not.toContain('Редакций пока нет');
    expect(btn('Создать новую редакцию')).toBeUndefined();
  });
});

describe('редакция: лог, diff, приёмка', () => {
  const READY = { id: 9, card: 'c1', rev: 1, parent: null, status: 'ready', message: 'добавь фильтр',
    code_ref: 'wb/c1/1@abc123', diff: 'diff --git a/x b/x\n+фильтр', diff_sha: 'sha1', log: 'прогнал тесты\n',
    gates: { pytest: { ok: true }, ruff: { ok: true } }, created_at: 1 };
  const open1 = async (rev: any = READY, extra: Record<string, any> = {}) => {
    api({ [`${B}/status`]: status(W()), [`${B}/cards/c1/revisions`]: list([rev]),
          [`${B}/cards/c1/revisions/1`]: () => J(rev), ...extra });
    await open();
    (host.querySelector('.wb-item') as HTMLElement).click(); await tick();
  };

  it('diff, лог и ворота показаны', async () => {
    await open1();
    expect(host.textContent).toContain('+фильтр');
    expect(host.textContent).toContain('прогнал тесты');
    expect(host.textContent).toContain('Ворота: 2 из 2 зелёных');
    expect(host.textContent).toContain('wb/c1/1@abc123');
  });

  it('чужой текст — ТОЛЬКО текстом: разметка из лога не становится разметкой', async () => {
    // log и diff пишет модель, message — оператор для модели: ничего не исполняется.
    await open1({ ...READY, log: '<img src=x onerror="alert(1)"><script>boom()</script>',
                  message: '<b>жирный</b>' });
    expect(host.querySelector('.wb-pre img')).toBeNull();
    expect(host.querySelector('.wb-pre script')).toBeNull();
    expect(host.querySelector('.wb-pre b')).toBeNull();
    expect(host.textContent).toContain('<img src=x onerror="alert(1)">');
  });

  it('принять нельзя, пока не отмечено «просмотрел diff»', async () => {
    await open1();
    const accept = btn('Принять');
    expect(accept.disabled).toBe(true);
    expect(host.textContent).toContain('просмотрели diff');
    (host.querySelector('.wb-chk input') as HTMLInputElement).click(); await tick();
    expect(btn('Принять').disabled).toBe(false);
  });

  it('приёмка шлёт ХЕШ diff, который оператор видел, и пишет, что дальше', async () => {
    let body: any = null;
    await open1(READY, { [`${B}/cards/c1/revisions/1/accept`]: (o: any) => (body = JSON.parse(o.body), J({ status: 'accepted' })) });
    (host.querySelector('.wb-chk input') as HTMLInputElement).click(); await tick();
    btn('Принять').click(); await tick();
    expect(body).toEqual({ diff_sha: 'sha1' });
    expect(host.textContent).toContain('слияние в main и релиз — отдельный шаг');
  });

  it('красные ворота — принять нельзя', async () => {
    await open1({ ...READY, gates: { pytest: { ok: true }, ruff: { ok: false } } });
    (host.querySelector('.wb-chk input') as HTMLInputElement).click(); await tick();
    expect(btn('Принять').disabled).toBe(true);
    expect(host.textContent).toContain('ворота не все зелёные');
  });

  it('строка "true" в воротах зелёной не считается', async () => {
    await open1({ ...READY, gates: { pytest: { ok: 'true' } } });
    expect(host.textContent).toContain('Ворота: 0 из 1 зелёных');
  });

  it('403 от сервера — его слова, а не молчание', async () => {
    await open1(READY, { [`${B}/cards/c1/revisions/1/accept`]: () =>
      J({ detail: 'Принимать редакции может только оператор.' }, 403) });
    (host.querySelector('.wb-chk input') as HTMLInputElement).click(); await tick();
    btn('Принять').click(); await tick();
    expect(host.textContent).toContain('Принимать редакции может только оператор.');
  });

  it('список операторов на сервере не задан — об этом сказано', async () => {
    api({ [`${B}/status`]: status(W(), false), [`${B}/cards/c1/revisions`]: list([READY]),
          [`${B}/cards/c1/revisions/1`]: () => J(READY) });
    await open();
    (host.querySelector('.wb-item') as HTMLElement).click(); await tick();
    expect(host.textContent).toContain('принять пока некому');
  });

  it('редакция в работе — можно отменить, принять нельзя', async () => {
    await open1({ ...READY, status: 'working', gates: null, diff: null, diff_sha: null });
    expect(btn('Отменить')).toBeDefined();
    expect(btn('Принять')).toBeUndefined();
  });

  it('принятая редакция: кто принял и что дальше', async () => {
    await open1({ ...READY, status: 'accepted', accepted_by: 'boss@x', accepted_at: 1_790_000_000_000 });
    expect(host.textContent).toContain('Принято: boss@x');
    expect(btn('Принять').disabled).toBe(true);
  });
});

describe('карточка исследования и прогон', () => {
  it('карточка исследования: создать нельзя, причина — несколько инструментов', async () => {
    api({ [`${B}/status`]: status(W()), [`${B}/cards/c1/revisions`]: list([]) });
    host = document.createElement('div'); document.body.appendChild(host);
    app = mount(WorkbenchPanel, { target: host, props: { card: 'c1', kind: 'research', baseRev: 4 } });
    await tick();
    expect(btn('Создать новую редакцию').disabled).toBe(true);
    expect(host.querySelector('.wb-why')!.textContent).toContain('несколько инструментов');
  });

  it('первая редакция шлёт parent = rev сборщика (одна нумерация с витриной)', async () => {
    let sent: any = null;
    api({ [`${B}/status`]: status(W()),
          [`${B}/cards/c1/revisions`]: (o) => (o?.method === 'POST'
            ? (sent = JSON.parse(o.body), J({ id: 1, rev: 4, status: 'queued' }))
            : J({ revisions: [] })),
          [`${B}/cards/c1/revisions/4`]: () => J({ id: 1, card: 'c1', rev: 4, status: 'queued', message: 'x', log: '' }) });
    host = document.createElement('div'); document.body.appendChild(host);
    app = mount(WorkbenchPanel, { target: host, props: { card: 'c1', kind: 'optimizer', baseRev: 3 } });
    await tick();
    btn('Создать новую редакцию').click(); await tick();
    const ta = host.querySelector('textarea') as HTMLTextAreaElement;
    ta.value = 'правь'; ta.dispatchEvent(new Event('input', { bubbles: true })); await tick();
    btn('отправить воркеру').click(); await tick();
    expect(sent.parent).toBe(3);
  });

  const RUN_REV = { id: 9, card: 'c1', rev: 2, parent: 1, status: 'ready', message: 'x', log: '',
    diff: 'd', diff_sha: 's', gates: { pytest: { ok: true } }, script_bytes: 2048,
    params: { symbol: 'RIZ6', date_from: '2026-07-01', date_to: '2026-09-30' }, runs: [] as any[] };

  it('готовая редакция: прогон ставится, id и статус показаны', async () => {
    let posted = 0;
    const rev = { ...RUN_REV };
    api({ [`${B}/status`]: status(W()), [`${B}/cards/c1/revisions`]: list([rev]),
          [`${B}/cards/c1/revisions/2`]: () => J(rev),
          [`${B}/cards/c1/revisions/2/run`]: () => { posted++; rev.runs = [{ run_id: 'run-7', by: 'boss@x', at: 1 }];
            return J({ run_id: 'run-7', engine: 'remote', runs: rev.runs }); },
          '/api/v1/backtest/run-7/status': () => J({ status: 'queued', runner: 'очередь на i9 (№2)' }) });
    await open();
    (host.querySelector('.wb-item') as HTMLElement).click(); await tick();
    expect(host.textContent).toContain('RIZ6 · 2026-07-01 … 2026-09-30');
    btn('Запустить прогон').click(); await tick();
    expect(posted).toBe(1);
    expect(host.textContent).toContain('run-7');
    expect(host.textContent).toContain('очередь на i9 (№2)');
  });

  it('без исходника — прогон заблокирован с причиной', async () => {
    const rev = { ...RUN_REV, script_bytes: null };
    api({ [`${B}/status`]: status(W()), [`${B}/cards/c1/revisions`]: list([rev]),
          [`${B}/cards/c1/revisions/2`]: () => J(rev) });
    await open();
    (host.querySelector('.wb-item') as HTMLElement).click(); await tick();
    expect(btn('Запустить прогон').disabled).toBe(true);
    expect(host.textContent).toContain('не оставил исходник');
  });

  it('отказ ручки прогона — его текст', async () => {
    api({ [`${B}/status`]: status(W()), [`${B}/cards/c1/revisions`]: list([RUN_REV]),
          [`${B}/cards/c1/revisions/2`]: () => J(RUN_REV),
          [`${B}/cards/c1/revisions/2/run`]: () => J({ detail: { code: 'too_many_combos', text: 'Комбинаций 2050, предел 2000.' } }, 422) });
    await open();
    (host.querySelector('.wb-item') as HTMLElement).click(); await tick();
    btn('Запустить прогон').click(); await tick();
    expect(host.textContent).toContain('Комбинаций 2050, предел 2000.');
  });
});
