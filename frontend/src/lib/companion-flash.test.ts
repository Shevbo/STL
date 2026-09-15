// Флэш исполнительного модуля в обоих компаньонах (docs/design/execution-module.md,
// разделы 12 и 15). Пока в снапшоте есть неснятые тревоги alerts.flash, шапка мигает
// с периодом 1 с и первой строкой идёт текст тревоги; нет тревог — ни класса, ни
// строки. Правка одной страницы без другой уже стоила оператору повторной жалобы,
// поэтому держим обе.
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

const page = (f: string) => readFileSync(resolve(process.cwd(), 'public', f), 'utf8')
  .replace(/\r\n/g, '\n');

function renderFlashOf(html: string) {
  const m = html.match(/function renderFlash\(a\) \{[\s\S]*?\n\}\n/);
  if (!m) throw new Error('не нашёл renderFlash');
  const esc = (x: unknown) => String(x ?? '');
  return new Function('esc', `${m[0]}; return renderFlash;`)(esc) as (a: unknown) => string;
}

const FLASH = { flash: [
  { code: 'EXIT_NOT_FILLED', text: 'выход RIU6 не налился за 10 с', ts_ms: 2 },
  { code: 'EXEC_STALL', text: 'доводка встала', ts_ms: 1 },
] };

describe.each(['companion.html', 'm.html'])('%s — флэш тревог', (file) => {
  const html = page(file);
  const renderFlash = renderFlashOf(html);

  it('есть тревоги: строка с первой и счётчиком остальных', () => {
    const out = renderFlash(FLASH);
    expect(out).toContain('class="flash-line"');
    expect(out).toContain('выход RIU6 не налился за 10 с');
    expect(out).toContain('и ещё 1');
  });

  it('тревог нет или старый сервер без поля — пусто', () => {
    expect(renderFlash({ flash: [] })).toBe('');
    expect(renderFlash({})).toBe('');
    expect(renderFlash(undefined)).toBe('');
  });

  it('мигание шапки с периодом 1 с', () => {
    expect(html).toMatch(/@keyframes stl-flash/);
    expect(html).toMatch(/\.top\.flash \{ animation: stl-flash 1s/);
  });
});
