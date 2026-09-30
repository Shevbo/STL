// Две средние в блоке «Позиции»: отдельно у роботной половины, отдельно у ручной.
// Одна средняя на всю позицию (число QUIK по нетто) не годится: половины ходят в
// РАЗНЫЕ стороны — сегодня роботы +5, руками −40 — и нетто-безубыток не является
// ценой входа ни одной из них (оператор 29.09.2026).
//
// Второе, что сторожит этот тест: НУЛЬ НЕ ПЕЧАТАЕТСЯ. У ручной половины средней
// может не быть честно (журнал окна её не видел), и «ср. 0» читалось бы как
// «вошли по нулю» — ровно тот сорт выдуманного числа, на котором уже горели.
import fs from 'node:fs';
import path from 'node:path';
import { describe, it, expect } from 'vitest';

const read = (f: string) => fs.readFileSync(path.resolve('public', f), 'utf8');
const PAGES = { 'companion.html': read('companion.html'), 'm.html': read('m.html') };

/** Достаём renderPositions со страницы и подставляем заглушки её помощников. */
function posFn(src: string): (list: any[]) => string {
  const pick = (re: RegExp, what: string) => {
    const m = src.match(re);
    if (!m) throw new Error(`${what} не найдена`);
    return m[0];
  };
  return new Function(`
    ${pick(/function px\(v\) \{[\s\S]*?\n\}/, 'px')}
    ${pick(/function half\(avg\) \{[\s\S]*?\n\}/, 'half')}
    const esc = (s) => String(s);
    const cls = () => '';
    const val = (s) => String(s);
    const rub = (v) => String(v);
    const quoteHtml = () => '';
    ${pick(/function renderPositions\(list\) \{[\s\S]*?\n\}/, 'renderPositions')};
    return renderPositions;`)() as (list: any[]) => string;
}

const nb = (s: string) => s.replace(/ /g, ' ');   // toLocaleString('ru-RU')

describe('средние цены половин позиции в панели', () => {
  for (const [page, src] of Object.entries(PAGES)) {
    it(`${page}: у роботов и у рук своя средняя`, () => {
      const html = posFn(src)([
        { sec: 'RIZ6', net: -35, varmargin: -31163,
          robot_net: 5, robot_avg: 82826, manual_net: -40, manual_avg: 82984 },
      ]);
      expect(html).toContain(nb('82 826'));
      expect(html).toContain(nb('82 984'));
      // Порядок важен: средняя стоит при СВОЕЙ половине, а не свалена в конец.
      expect(html.indexOf(nb('82 826'))).toBeLessThan(html.indexOf('Ручные'));
      expect(html.indexOf(nb('82 984'))).toBeGreaterThan(html.indexOf('Ручные'));
    });

    // Нуля не печатаем НИКОГДА: «ср. 0» читается как «вошли по нулю».
    it(`${page}: нуля вместо средней не бывает`, () => {
      for (const avg of [0, null, undefined]) {
        const html = posFn(src)([
          { sec: 'RIZ6', net: -35, varmargin: -31163,
            robot_net: 5, robot_avg: avg, manual_net: -40, manual_avg: avg },
        ]);
        expect(html).not.toContain('ср. 0');
        expect(html).not.toContain('ср. —');
      }
    });

    // У РУЧНОЙ половины неизвестная средняя это факт, а не пустота: журнал не
    // видел набора позиции, а средняя QUIK при живых роботах относится ко ВСЕЙ
    // позиции. Пустое место оператор читал как поломку экрана (30.09.2026).
    it(`${page}: неизвестная ручная средняя названа «?», а не спрятана`, () => {
      const html = posFn(src)([
        { sec: 'RIZ6', net: -35, varmargin: -31163,
          robot_net: 5, robot_avg: 82826, manual_net: -40, manual_avg: null,
          manual_avg_why: 'позиция набрана до начала журнала' },
      ]);
      expect(html).toContain(nb('82 826'));       // роботная известна
      expect(html).toContain('ср. ?');            // ручная — честное «не знаю»
      expect(html).toContain('до начала журнала');// с причиной в подсказке
    });

    // Средняя СЧЁТА пересчитывается к клиринговой цене (real-trade доказал
    // наблюдением 30.09.2026: позиция не менялась, а средняя за ночь уехала на
    // 1550 пунктов). Показать её можно, назвать ценой входа — нельзя: это то же
    // смешение баз, на котором горели с ВМ «за сессию» против «от входа».
    it(`${page}: средняя счёта подписана клирингом, а не ценой входа`, () => {
      const html = posFn(src)([
        { sec: 'RIZ6', net: -43, varmargin: 100,
          robot_net: 0, robot_avg: null, manual_net: -43,
          manual_avg: 84920, manual_avg_src: 'quik' },
      ]);
      expect(html).toContain('ср. с клиринга');
      expect(html).toContain(nb('84 920'));
    });

    it(`${page}: журнальная средняя — обычная «ср.», она и есть цена входа`, () => {
      const html = posFn(src)([
        { sec: 'RIZ6', net: -43, varmargin: 100,
          robot_net: 0, robot_avg: null, manual_net: -43,
          manual_avg: 84805, manual_avg_src: 'journal' },
      ]);
      expect(html).toContain('ср.');
      expect(html).not.toContain('с клиринга');
    });

    // Роботной средней может не быть тоже, и вот ей вопросительный знак не
    // ставим: она либо посчитана по входам раннеров, либо её нет.
    it(`${page}: флэт руками не просит средней вовсе`, () => {
      const html = posFn(src)([
        { sec: 'RIZ6', net: 5, varmargin: 100,
          robot_net: 5, robot_avg: 82826, manual_net: 0, manual_avg: null },
      ]);
      expect(html).not.toContain('ср. ?');
    });
  }
});
