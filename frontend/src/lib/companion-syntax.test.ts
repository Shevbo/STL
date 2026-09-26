// СТРАНИЦА КОМПАНЬОНА НИКЕМ НЕ КОМПИЛИРУЕТСЯ. Её скрипт уезжает на прод как есть,
// и одна разорванная строка делает панель пустой: 23.09.2026 правка подсказки
// разорвала строковый литерал в companion.html, и это поймал только соседний тест.
// Здесь скрипт каждой страницы РАЗБИРАЕТСЯ как код (компиляция без выполнения).
import fs from 'node:fs';
import path from 'node:path';
import { describe, it, expect } from 'vitest';

const PAGES = ['companion.html', 'm.html', 'devchat.html'];

/** Все инлайновые <script> без src: именно они и уезжают на прод. */
function scripts(html: string): string[] {
  const out: string[] = [];
  const re = /<script\b([^>]*)>([\s\S]*?)<\/script>/gi;
  let m: RegExpExecArray | null;
  while ((m = re.exec(html)) !== null) {
    if (/\bsrc\s*=/i.test(m[1])) continue;
    if (/type\s*=\s*["'](?!text\/javascript|module)/i.test(m[1])) continue;
    if (m[2].trim()) out.push(m[2]);
  }
  return out;
}

describe('страницы компаньона разбираются как код', () => {
  for (const page of PAGES) {
    const file = path.resolve('public', page);
    if (!fs.existsSync(file)) continue;
    it(`${page}: скрипт без синтаксических ошибок`, () => {
      const parts = scripts(fs.readFileSync(file, 'utf8'));
      expect(parts.length, 'инлайновых скриптов не нашлось').toBeGreaterThan(0);
      for (const [i, src] of parts.entries()) {
        // new Function КОМПИЛИРУЕТ тело, но не выполняет его: синтаксис проверен,
        // побочных действий нет. Модульный синтаксис в этих страницах не нужен.
        expect(() => new Function(src), `${page}: скрипт #${i + 1} не разбирается`).not.toThrow();
      }
    });
  }
});

// Панель молча оставалась на старой вёрстке: nginx отдаёт её без Cache-Control,
// WebView держит страницу сколько хочет, и оператор смотрел вчерашний экран
// (24.09.2026, тот же случай, что с SPA 06.08.2026). Сторож версии обязан быть
// на обеих страницах и НЕ опираться на зашитый в файл маркер — его забудут.
describe('сторож версии панели', () => {
  for (const page of ['companion.html', 'm.html']) {
    const src = fs.readFileSync(path.resolve('public', page), 'utf8');

    it(`${page}: сторож есть и сравнивает заголовки страницы, а не зашитый номер`, () => {
      expect(src).toContain('versionWatch');
      expect(src).toMatch(/etag/i);
      expect(src).toMatch(/last-modified/i);
      expect(src).toContain("cache: 'no-store'");
    });

    it(`${page}: полоса обновления и кнопка перезагрузки с обходом кэша`, () => {
      expect(src).toContain('Вышла новая версия панели');
      expect(src).toMatch(/location\.pathname \+ '\?r=' \+ Date\.now\(\)/);
      expect(src).toContain('.newver');
    });
  }
});

// 26.09.2026: kill-switch агента час отклонял заявки реальных роботов, а STL
// считал торговлю разрешённой — на экранах это не было видно вовсе. Полоса
// обязана быть на обеих страницах и обязана различать ТРИ состояния, потому что
// null («агент старее поля») это не «разрешено».
describe('блокировка торговли агентом видна', () => {
  const cut = (src: string, name: string) => {
    const i = src.indexOf('function ' + name + '(tb) {');
    const j = src.indexOf(String.fromCharCode(10) + '}', i);
    if (i < 0 || j < 0) throw new Error(name + ' не найдена');
    return src.slice(i, j + 2);
  };
  for (const page of ['companion.html', 'm.html']) {
    const src = fs.readFileSync(path.resolve('public', page), 'utf8');
    const f = new Function(
      'function esc(x){return String(x==null?"":x);}' + cut(src, 'tradingBlockHtml')
      + 'return tradingBlockHtml;',
    )() as (tb: any) => string;

    it(`${page}: блокировка названа и сказано, что снимается перезапуском`, () => {
      const h = f({ trading_blocked: true, reason: 'kill-switch агента', reversible: false,
                    since_ms: Date.parse('2026-09-26T10:00:00Z'), echo_age_ms: 37000 });
      expect(h).toContain('ЗАБЛОКИРОВАНА');
      expect(h).toContain('kill-switch');
      expect(h).toContain('перезапуском агента');
    });

    it(`${page}: null — это НЕ «разрешено», а «не знаем»`, () => {
      const h = f({ trading_blocked: null, echo_age_ms: 5000 });
      expect(h).toContain('не сообщает');
      expect(h).not.toContain('ЗАБЛОКИРОВАНА');
    });

    it(`${page}: торговля разрешена — полосы нет вовсе`, () => {
      expect(f({ trading_blocked: false })).toBe('');
      expect(f(null)).toBe('');
    });
  }
});
