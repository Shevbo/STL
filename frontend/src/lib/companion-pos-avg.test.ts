// Средняя цена ВСЕЙ позиции инструмента (роботы + руками) в блоке «Позиции».
// Число уже ехало в снапшоте (`avg`), но не рисовалось — оператор считал среднюю
// в уме. Главное здесь не «есть подпись», а ЧТО НУЛЯ НЕТ: `avg: 0` у QUIK означает
// «средней нет», и напечатанное «ср. 0» читается как «вошли по нулю».
import fs from 'node:fs';
import path from 'node:path';
import { describe, it, expect } from 'vitest';

const read = (f: string) => fs.readFileSync(path.resolve('public', f), 'utf8');
const PAGES = { 'companion.html': read('companion.html'), 'm.html': read('m.html') };

/** Достаём renderPositions со страницы и подставляем заглушки её помощников. */
function posFn(src: string): (list: any[]) => string {
  const m = src.match(/function renderPositions\(list\) \{[\s\S]*?\n\}/);
  if (!m) throw new Error('renderPositions не найдена');
  const px = src.match(/function px\(v\) \{[\s\S]*?\n\}/);
  if (!px) throw new Error('px не найдена');
  return new Function(`
    ${px[0]}
    const esc = (s) => String(s);
    const cls = () => '';
    const val = (s) => String(s);
    const rub = (v) => String(v);
    const quoteHtml = () => '';
    ${m[0]};
    return renderPositions;`)() as (list: any[]) => string;
}

describe('средняя цена позиции в панели', () => {
  for (const [page, src] of Object.entries(PAGES)) {
    it(`${page}: средняя напечатана рядом с разбивкой`, () => {
      const html = posFn(src)([
        { sec: 'RIZ6', net: -35, avg: 83007, varmargin: -31163, robot_net: 5, manual_net: -40 },
      ]);
      expect(html).toContain('ср.');
      expect(html).toContain('83 007'.replace(/ /g, ' '));
    });

    it(`${page}: средней нет — строки нет, а не «ср. 0»`, () => {
      for (const avg of [0, null, undefined]) {
        const html = posFn(src)([
          { sec: 'RIZ6', net: -35, avg, varmargin: -31163, robot_net: 5, manual_net: -40 },
        ]);
        expect(html).not.toContain('ср.');
      }
    });
  }
});
