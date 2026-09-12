// Прокрутка списка ручных заявок в companion.html. Страница отдаётся компаньону
// как есть, без сборщика, поэтому проверяем её файлом — как и мини-график рядом.
//
// 12.09.2026 в проде: попытка удержать прокрутку таймером была дописана ПОСЛЕ
// закрывающего </script> и на TypeScript. Панель печатала этот код оператору
// текстом внизу окна, а сама прокрутка продолжала сбрасываться. Тест держит оба
// края: в странице нет кода вне скрипта, и paint() возвращает прокрутку сам.
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

const html = readFileSync(resolve(process.cwd(), 'public/companion.html'), 'utf8')
  .replace(/\r\n/g, '\n');

describe('companion.html — заявки, прокрутка', () => {
  it('после </script> в странице нет ничего, кроме закрывающих тегов', () => {
    const tail = html.slice(html.lastIndexOf('</script>') + '</script>'.length);
    expect(tail.replace(/<\/(body|html)>/g, '').trim()).toBe('');
  });

  it('в странице нет синтаксиса TypeScript — её никто не компилирует', () => {
    const src = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map((m) => m[1]).join('\n');
    expect(src).not.toMatch(/\bas unknown as\b|\blet \w+: \w+/);
  });

  it('paint() возвращает прокрутку заявок после полной перерисовки', () => {
    // jsdom не считает раскладку и держит scrollTop нулём: подменяем на обычное
    // поле, нам нужна проводка «сохранил → вернул», а не настоящий скролл.
    Object.defineProperty(window.Element.prototype, 'scrollTop', {
      get() { return (this as any).__st || 0; },
      set(v) { (this as any).__st = v; },
      configurable: true,
    });
    document.body.innerHTML = '<div id="brand"></div><div id="body"></div>';

    const m = html.match(/function paint\(d\) \{[\s\S]*?\n\}\n/);
    expect(m).not.toBeNull();
    const stub = () => '';
    const deps: Record<string, unknown> = {
      lastData: null, STL: '', DENSITY: [],
      $: (id: string) => document.getElementById(id),
      density: () => 60, fitWindow: stub,
      renderBrand: stub, renderMarket: stub, renderAccount: stub,
      renderPositions: stub, renderRobots: stub, robotsTotal: stub,
      manualTotal: stub, renderWatch: stub, renderAlerts: stub, alertsToggle: stub,
      renderOrders: () => '<div class="ord-box">много строк</div>',
    };
    const paint = new Function('d', `with (d) { ${m![0]}; return paint; }`)(deps) as (x: unknown) => void;

    paint({});
    const box = () => document.querySelector('.ord-box') as HTMLElement;
    box().scrollTop = 42;         // оператор отлистал список вниз
    paint({});                    // пришёл снапшот, #body переписан целиком
    expect(box().scrollTop).toBe(42);
  });
});
