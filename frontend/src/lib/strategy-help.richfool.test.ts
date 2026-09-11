import { describe, it, expect } from 'vitest';
import { behaviorFor, copyByFor, nameFor, overviewFor } from './strategy-help';

// rich_fool — standalone-модуль, а не реестровая стратегия: у него НЕТ слоя
// make_on_bar, поэтому общий шаблон M1-по-закрытию про него врёт (усреднение
// против движения, отсутствие тейка/стопа). Эти проверки держат текст правдивым.

const defaults = {
  place_lead_min: 10, open_hour: 7, step_count: 3, n_days: 5,
  dist_pct: 50, step_gap_pct: 25, hold_min: 30, qty: 1,
  sl_pct: 30, rr_x10: 20, vol_mult: 10, max_contracts: 100, invert: 0,
};

describe('rich_fool: описание на портале', () => {
  it('у страницы есть имя и hero-описание (не пустой hero)', () => {
    expect(nameFor('rich_fool')).toContain('Rich Fool');
    expect(overviewFor('rich_fool')?.entry).toBeTruthy();
    expect(overviewFor('rich_fool')?.tp).toBeTruthy();
    // __inv — тот же лонгрид, суффикс не должен ломать поиск
    expect(overviewFor('rich_fool__inv')?.entry).toBe(overviewFor('rich_fool')?.entry);
  });

  it('текст «как ведёт себя» говорит про лестницу, а не про M1-по-закрытию', () => {
    const t = behaviorFor('rich_fool', defaults)!;
    expect(t).toContain('до открытия');
    expect(t).toContain('ступеней');
    expect(t).toContain('овернайт');
    // общий шаблон: усреднение против движения и отсутствие выхода
    expect(t).not.toContain('без усреднения');
    expect(t).not.toContain('жёсткого тейка нет');
    expect(t).not.toContain('стоп-лосса нет');
  });

  it('реальные числа параметров попадают в текст', () => {
    const t = behaviorFor('rich_fool', { ...defaults, step_count: 7, sl_pct: 40, rr_x10: 30 })!;
    expect(t).toContain('7 ступеней');
    expect(t).toContain('0.40');
    expect(t).toContain('3.0 к 1');
  });

  it('invert=1 честно сообщает о фейде пробоя', () => {
    const t = behaviorFor('rich_fool', { ...defaults, invert: 1 })!;
    expect(t).toContain('ПЕРЕВЁРНУТЫ');
    expect(t).toContain('фейдит');
  });

  it('автор текста описания — не автор стратегии', () => {
    expect(copyByFor('rich_fool').author).toBeTruthy();
    expect(copyByFor('rich_fool').date).toBeTruthy();
    // у прочих лонгридов дефолт
    expect(copyByFor('macd_cross').author).toBe('claude opus');
    expect(copyByFor('macd_cross__inv')).toEqual(copyByFor('macd_cross'));
  });
});
