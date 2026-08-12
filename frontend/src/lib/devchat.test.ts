// Экран «Чат разработчиков» (public/devchat.html) — страница отдаётся как есть,
// без сборщика, поэтому тест поднимает её скрипт в jsdom на фикстуре ленты.
// Пинним ровно то, что заказано письмом и легко ломается молча:
//   просрочка видна с одного взгляда, тело свёрнуто, ни одного вызова модели.
import { describe, it, expect, beforeAll, vi } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';

const HTML = fs.readFileSync(path.resolve('public/devchat.html'), 'utf8');
const body = (h: string) => h.slice(h.indexOf('<script>') + 8, h.lastIndexOf('</script>'));
const H = (ms: number) => Date.now() - ms * 3600_000;

const FEED = {
  broadcast: 'all',
  stale_after_h: 4.0,
  agents: {
    'real-trade': 'proto/, quik_agent/', backtests: 'trader/lab/, scripts/',
    'ui-ux': 'frontend/, trader/api/', operator: 'человек (Boris)',
  },
  messages: [
    { id: '1:aaa', from: 'real-trade', to: 'backtests', topic: 'Архив включён',
      body: 'Тело первого письма — длинное.', created_ms: H(14), read_ms: 0,
      age_h: 14.0, read: false, stale: true },
    { id: '2:bbb', from: 'ui-ux', to: 'all', topic: 'devmail на русской Windows',
      body: 'Тело второго письма.', created_ms: H(0.2), read_ms: 0,
      age_h: 0.2, read: false, stale: false },
    { id: '3:ccc', from: 'backtests', to: 'ui-ux', topic: 're: лампа',
      body: 'Тело третьего.', created_ms: H(1), read_ms: H(0.5),
      age_h: 1.0, read: true, stale: false },
  ],
};

async function boot(feed: unknown = FEED) {
  document.body.innerHTML = `<span id="hcount"></span><div id="board"></div>
    <form id="compose" class="hidden"><select id="cTo"></select><input id="cTopic">
    <textarea id="cBody"></textarea><button id="cSend"></button><span id="cSent"></span></form>
    <div id="filters"></div><div id="feed"></div>
    <button id="btnCompose"></button><button id="btnCsv"></button>
    <button id="btnAuto"></button><button id="btnReload"></button>`;
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, status: 200, json: async () => feed })));
  new Function(body(HTML))();
  await new Promise((r) => setTimeout(r, 0));
}

describe('чат разработчиков', () => {
  beforeAll(() => vi.useRealTimers());

  it('просрочка видна и в доске окон, и в самом письме', async () => {
    await boot();
    const board = document.getElementById('board')!.innerHTML;
    // окно, которое не забирает почту, краснеет целиком, а не прячет число в строке
    expect(board).toContain('class="win late"');
    expect(board).toContain('14 ч');
    expect(document.getElementById('hcount')!.textContent).toContain('ПРОСРОЧКА');
    expect(document.getElementById('feed')!.innerHTML).toContain('ПРОСРОЧЕНО');
  });

  it('непрочитанное считается по адресату, а не по всей ленте', async () => {
    await boot();
    const board = document.getElementById('board')!.innerHTML;
    // backtests: письмо от real-trade + общее «всем» = 2; своё письмо себе не в счёт
    const win = board.split('class="win').find((s) => s.includes('backtests'))!;
    expect(win).toContain('<b>2</b>');
    // ui-ux: единственное адресованное ему письмо подтверждено, своё «всем» не в счёт
    const own = board.split('class="win').find((s) => s.includes('ui-ux'))!;
    expect(own).toContain('всё прочитано');
  });

  it('тело письма свёрнуто, разворачивается кликом по шапке', async () => {
    await boot();
    const msg = document.querySelector('.msg')!;
    expect(msg.classList.contains('open')).toBe(false);
    (msg.querySelector('.head') as HTMLElement).click();
    expect(document.querySelector('.msg')!.classList.contains('open')).toBe(true);
  });

  it('фильтр «только просроченные» оставляет одно письмо', async () => {
    await boot();
    (document.querySelector('[data-o="stale"]') as HTMLElement).click();
    expect(document.querySelectorAll('.msg')).toHaveLength(1);
    expect(document.getElementById('feed')!.innerHTML).toContain('Архив включён');
  });

  it('лента идёт новыми сверху, как бы её ни отдал сервер', async () => {
    await boot();
    const ids = [...document.querySelectorAll('.msg')].map((e) => (e as HTMLElement).dataset.id);
    expect(ids).toEqual(['2:bbb', '3:ccc', '1:aaa']);
  });

  it('401 говорит, что нужно войти в STL, и не рисует пустую ленту', async () => {
    document.body.innerHTML = '<div id="feed"></div><div id="board"></div><span id="hcount"></span>'
      + '<div id="filters"></div><form id="compose"><select id="cTo"></select><input id="cTopic">'
      + '<textarea id="cBody"></textarea><button id="cSend"></button><span id="cSent"></span></form>'
      + '<button id="btnCompose"></button><button id="btnCsv"></button>'
      + '<button id="btnAuto"></button><button id="btnReload"></button>';
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, status: 401, json: async () => ({}) })));
    new Function(body(HTML))();
    await new Promise((r) => setTimeout(r, 0));
    expect(document.getElementById('feed')!.innerHTML).toContain('войдите в STL');
  });

  // Оператор оговорил отдельно: экран читает API и ничего не отправляет в модель.
  it('ни одного обращения к модели: только своя лента и отправка письма', () => {
    const urls = [...HTML.matchAll(/fetch\(\s*([A-Z_]+|'[^']+')/g)].map((m) => m[1]);
    expect(urls.sort()).toEqual(["'/api/v1/dev/msg'", 'FEED']);
    expect(HTML).not.toMatch(/lineman|klod\/ask|openai|anthropic|\/chat\b/i);
  });
});
