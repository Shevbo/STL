// Раскладка строки панели: контракт, который нельзя терять. ОБЕ ПАНЕЛИ.
//
// 02.10.2026 оператор прислал скриншот с iPhone: значения ВМ уезжали ЗА правый
// край экрана, подписи строились лесенкой по одному слову. Причина — одна
// флекс-линия: у флекс-детей min-width равен auto, поэтому неразрывные куски
// («Роботы 0 ср. 9 891», «Ручные −23 ср. с клиринга 85 300», «журнал +3») плюс
// число с nowrap в сумме становились шире экрана.
//
// Я починил m.html и отчитался — а следом тот же оператор прислал Windows-панель,
// где ВМ по RIZ6 не было ВООБЩЕ: её вытолкнуло за правый край вместе с подписью.
// Причина та же, строка та же, панель другая. Поэтому контракт проверяется по
// ОБЕИМ: правило, проверенное на одном файле, не защищает второй, а расходиться
// этим двум запрещено.
//
// Раскладку нечем проверить вычислением: в jsdom нет layout. Поэтому проверяем
// САМ КОНТРАКТ — правила, возврат которых вернёт и баг.
import { describe, it, expect } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';

describe.each(['m.html', 'companion.html'])('%s — строка панели', (file) => {
  const html = fs.readFileSync(path.resolve('public', file), 'utf8');
  const css = html.slice(html.indexOf('<style>'), html.indexOf('</style>'));
  const rule = (sel: string) => {
    const i = css.indexOf(sel + ' {');
    expect(i, `правило ${sel} пропало в ${file}`).toBeGreaterThan(-1);
    return css.slice(i, css.indexOf('}', i));
  };

  it('сетка, а не флекс: текст вправе ужаться до нуля', () => {
    const r = rule('.row');
    expect(r).toContain('display: grid');
    // minmax(0, 1fr) — это и есть право ужаться. Без нуля в минимуме колонка
    // текста не станет уже своего содержимого, и строка снова уедет за край.
    expect(r).toContain('minmax(0, 1fr)');
  });

  it('у каждого ребёнка min-width: 0 — иначе сетка повторит ошибку флекса', () => {
    expect(rule('.row > *')).toContain('min-width: 0');
  });

  it('число не переносится и стоит в своей колонке первой строки', () => {
    const v = rule('.row > .v');
    expect(v).toContain('white-space: nowrap');
    expect(v).toContain('grid-column: 2');
    expect(v).toContain('grid-row: 1');
  });

  it('уточнение занимает ВСЮ ширину: лесенки из узкой колонки больше нет', () => {
    expect(rule('.row > .sub')).toContain('grid-column: 1 / -1');
  });

  it('распорка флекс-времён погашена, а не оставлена третьей колонкой', () => {
    expect(rule('.row > .sp')).toContain('display: none');
  });

  it('подпись под числом есть: «что это за число» не отнимает ширину у текста', () => {
    expect(rule('.row > .v small')).toContain('display: block');
  });

  it('телефон не прячет переполнение: оно прячет следующий такой же баг', () => {
    // В m.html скрывать нельзя: там прокрутка штатная, и уехавшее число должно
    // быть ВИДНО как уехавшее. В companion.html обрезка есть и остаётся —
    // ширину окна в трее задаёт Go, горизонтальная полоса в нём неуместна, — но
    // именно она 02.10.2026 СЪЕЛА ВМ по RIZ6 без следа: число не уехало за
    // край, оно исчезло. Поэтому для той панели единственная защита — контракт
    // строки выше, и он проверяется здесь же.
    if (file === 'm.html') expect(css).not.toMatch(/overflow-x:\s*hidden/);
  });

  it('деньги печатаются неразрывными пробелами', () => {
    // Второй замок поверх nowrap: число, разорванное переносом, читается как
    // другое число (оператор 01.10.2026: «−12 784 ₽» распалось на «−12»).
    const src = html.slice(html.indexOf('function rub('), html.indexOf('function cls('));
    const rub = new Function(`${src}; return rub;`)() as (v: number) => string;
    expect(rub(-17866)).not.toContain(' ');     // обычных пробелов в числе нет
    expect(rub(-17866)).toContain('\u00a0₽');
  });

  it('позиция отдаёт ВМ колонке значения, а не куску общей линии', () => {
    // Именно это и уехало за край: «ВМ сессии» стояла сбоку отдельным .sub и
    // отнимала ширину у разбивки, выталкивая само число наружу.
    const src = html.slice(html.indexOf('function renderPositions('));
    const body = src.slice(0, src.indexOf('\n}'));
    expect(body).toContain('<small>ВМ сессии</small>');
    expect(body).not.toContain('<span class="sub">ВМ сессии</span>');
  });
});
