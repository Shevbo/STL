import { describe, it, expect } from 'vitest';
import { behaviorFor, copyByFor, nameFor, overviewFor } from './strategy-help';

// rich_fool — standalone-модуль, а не реестровая стратегия: у него НЕТ слоя
// make_on_bar, поэтому общий шаблон M1-по-закрытию про него врёт (усреднение
// против движения, отсутствие тейка/стопа). Эти проверки держат текст правдивым.
//
// Инцидент 11-12.09.2026. Лонгрид описывал ВЫБРОШЕННУЮ механику
// (dist_pct/step_gap_pct/spacing/span_pct, фиксированный тейк rr×R, стоп в долях
// амплитуды) и ПУТАЛ СТОРОНУ — утверждал «верхняя ступень это лонг». Стратегия
// ФЕЙДИТ импульс: уровни выше вчерашнего закрытия это заявки на ПРОДАЖУ. Текст и
// код обязаны совпадать, иначе оператор читает одно, а перебор считает другое.

const defaults = {
  place_lead_min: 10, open_hour: 10, step_count: 3, n_days: 5,
  hold_min: 30, qty: 1, sl_price_pct: 100, trail_tp_pct: 50,
  vol_mult: 10, max_contracts: 100, invert: 0,
};

describe('rich_fool: описание на портале', () => {
  it('у страницы есть имя и hero-описание (не пустой hero)', () => {
    expect(nameFor('rich_fool')).toContain('Rich Fool');
    expect(overviewFor('rich_fool')?.entry).toBeTruthy();
    expect(overviewFor('rich_fool')?.tp).toBeTruthy();
  });

  it('текст «как ведёт себя» говорит про лестницу, а не про M1-по-закрытию', () => {
    const t = behaviorFor('rich_fool', defaults)!;
    expect(t).toContain('до открытия');
    expect(t).toContain('уровн');
    expect(t).toContain('овернайт');
    expect(t).not.toContain('без усреднения');
    expect(t).not.toContain('жёсткого тейка нет');
    expect(t).not.toContain('стоп-лосса нет');
  });

  it('СТОРОНА: цена ВВЕРХ = ШОРТ (фейд), цена ВНИЗ = ЛОНГ', () => {
    const t = behaviorFor('rich_fool', defaults)!;
    expect(t).toMatch(/ВВЕРХ[^.]*ШОРТ/);
    expect(t).toMatch(/ВНИЗ[^.]*ЛОНГ/);
    // ровно те формулировки, которые были неверными
    expect(t).not.toContain('верхняя — покупка');
    expect(t).not.toContain('верхняя ступень это лонг');
    const hero = overviewFor('rich_fool')!.entry;
    expect(hero).toContain('ПРОДАЖУ');
    expect(hero).toContain('ФЕЙДИТ');
    expect(hero).not.toContain('верхняя ступень это лонг');
  });

  it('у контр-варианта __inv СВОЙ hero с обратными сторонами', () => {
    const base = overviewFor('rich_fool')!.entry;
    const inv = overviewFor('rich_fool__inv')!.entry;
    expect(inv).not.toBe(base);
    expect(inv).toContain('КОНТРОЛЬНЫЙ');
    expect(inv).toMatch(/ВВЕРХ[^.]*ЛОНГ/);
    const t = behaviorFor('rich_fool', { ...defaults, invert: 1 })!;
    expect(t).toContain('КОНТРОЛЬНЫЙ');
    expect(t).toMatch(/ВВЕРХ[^.]*ЛОНГ/);
  });

  it('уровни считаются убывающим шагом 1/(n+1), а не линейно', () => {
    const t = behaviorFor('rich_fool', defaults)!;
    expect(t).toContain('0.50, 0.83, 1.08');
    expect(t).toContain('УБЫВАЮЩЕМ');
    expect(t).not.toContain('dist_pct');
    expect(t).not.toContain('step_gap_pct');
    expect(t).not.toContain('spacing');
  });

  it('выход: стоп в % ОТ ЦЕНЫ и ТРЕЙЛИНГ-тейк, без фиксированного R:R', () => {
    const t = behaviorFor('rich_fool', { ...defaults, sl_price_pct: 250, trail_tp_pct: 80 })!;
    expect(t).toContain('2.50% ОТ ЦЕНЫ');
    expect(t).toContain('ТРЕЙЛИНГ');
    expect(t).toContain('0.80%');
    expect(t).not.toContain('к 1 от стопа');
    expect(t).not.toContain('амплитуды от средней');
    const hero = overviewFor('rich_fool')!;
    expect(hero.tp).toContain('ТРЕЙЛИНГ');
    expect(hero.tp).toContain('ТОЛЬКО в прибыли');
    expect(hero.sl).toContain('ОТ ЦЕНЫ');
  });

  it('реальные числа параметров попадают в текст', () => {
    const t = behaviorFor('rich_fool', { ...defaults, step_count: 7, n_days: 12, hold_min: 120 })!;
    expect(t).toContain('7 уровн');
    expect(t).toContain('12 дн');
    expect(t).toContain('120 мин');
  });

  it('время открытия — часы И минуты, а не только часы', () => {
    expect(behaviorFor('rich_fool', defaults)).toContain('10:00 МСК');
    expect(behaviorFor('rich_fool', { ...defaults, open_hour: 10, open_min: 5 })).toContain('10:05 МСК');
  });

  it('автор текста описания — не автор стратегии', () => {
    expect(copyByFor('rich_fool').author).toBeTruthy();
    expect(copyByFor('rich_fool').date).toBeTruthy();
    expect(copyByFor('macd_cross').author).toBe('claude opus');
    expect(copyByFor('macd_cross__inv')).toEqual(copyByFor('macd_cross'));
  });
});
