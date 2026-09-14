// Прокрутка списка ручных заявок в компаньонах. Обе страницы отдаются как есть,
// без сборщика, поэтому проверяем их файлом — как и мини-график рядом.
//
// 12.09.2026 в проде: попытка удержать прокрутку таймером была дописана ПОСЛЕ
// закрывающего </script> и на TypeScript. Панель печатала этот код оператору
// текстом, а прокрутка продолжала сбрасываться. 14.09 то же нашлось в m.html:
// починили только Windows-панель, а телефон так и прыгал в начало каждые 5 с.
// Поэтому тест держит ОБЕ страницы: нет кода вне скрипта, нет TypeScript, и
// перерисовка возвращает прокрутку сама.
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

const page = (f: string) => readFileSync(resolve(process.cwd(), 'public', f), 'utf8')
  .replace(/\r\n/g, '\n');

// jsdom не считает раскладку и держит scrollTop нулём: подменяем на обычное
// поле, нам нужна проводка «сохранил → вернул», а не настоящий скролл.
function fakeScroll() {
  Object.defineProperty(window.Element.prototype, 'scrollTop', {
    get() { return (this as any).__st || 0; },
    set(v) { (this as any).__st = v; },
    configurable: true,
  });
}

function pick(html: string) {
  const m = html.match(/function paint\(d\) \{[\s\S]*?\n\}\n/);
  if (!m) throw new Error('не нашёл paint');
  return m[0];
}

const build = (src: string, deps: Record<string, unknown>) =>
  new Function('d', `with (d) { ${src}; return paint; }`)(deps) as (x: unknown) => void;

function scrollSurvives(paint: (x: unknown) => void) {
  paint({});
  const box = () => document.querySelector('.ord-box') as HTMLElement;
  box().scrollTop = 42;         // оператор отлистал список вниз
  paint({});                    // пришёл снапшот, страница переписана целиком
  return box().scrollTop;
}

describe.each(['companion.html', 'm.html'])('%s — заявки, прокрутка', (file) => {
  const html = page(file);

  it('после </script> в странице нет ничего, кроме закрывающих тегов', () => {
    const tail = html.slice(html.lastIndexOf('</script>') + '</script>'.length);
    expect(tail.replace(/<\/(body|html)>/g, '').trim()).toBe('');
  });

  it('в странице нет синтаксиса TypeScript — её никто не компилирует', () => {
    const src = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map((m) => m[1]).join('\n');
    expect(src).not.toMatch(/\bas unknown as\b|\blet \w+: \w+/);
  });
});

describe('перерисовка возвращает прокрутку заявок', () => {
  it('companion.html: paint() переписывает #body', () => {
    fakeScroll();
    document.body.innerHTML = '<div id="brand"></div><div id="body"></div>';
    const stub = () => '';
    const paint = build(pick(page('companion.html')), {
      lastData: null, STL: '', DENSITY: [],
      $: (id: string) => document.getElementById(id),
      density: () => 60, fitWindow: stub,
      renderBrand: stub, renderMarket: stub, renderAccount: stub,
      renderPositions: stub, renderRobots: stub, robotsTotal: stub,
      manualTotal: stub, renderWatch: stub, renderAlerts: stub, alertsToggle: stub,
      renderOrders: () => '<div class="ord-box">много строк</div>',
    });
    expect(scrollSurvives(paint)).toBe(42);
  });

  it('m.html: paint() переписывает #app на любом экране', () => {
    fakeScroll();
    document.body.innerHTML = '<div id="app"></div>';
    const draw = () => {
      document.getElementById('app')!.innerHTML = '<div class="ord-box">много строк</div>';
    };
    const paint = build(pick(page('m.html')), {
      location: { hash: '#/' },
      paintPanel: draw, paintOrders: draw, paintRobot: draw, paintWatch: draw,
    });
    expect(scrollSurvives(paint)).toBe(42);
  });
});
