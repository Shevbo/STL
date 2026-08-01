import { describe, it, expect } from 'vitest';
import { fmtEta, progressPct } from './BacktestLab.svelte';

describe('монитор прогона: длительность', () => {
  it('секунды до минуты, дальше минуты', () => {
    expect(fmtEta(0)).toBe('0 с');
    expect(fmtEta(42)).toBe('42 с');
    expect(fmtEta(59)).toBe('59 с');
    expect(fmtEta(60)).toBe('1 мин');
    expect(fmtEta(605)).toBe('10 мин');
  });

  it('нет оценки — прочерк, а не «NaN с»', () => {
    expect(fmtEta(null)).toBe('—');
    expect(fmtEta(undefined)).toBe('—');
    expect(fmtEta(NaN)).toBe('—');
  });
});

describe('монитор прогона: честный процент', () => {
  it('готово — ровно 100%', () => {
    expect(progressPct('done', 5, 999)).toBe(100);
  });

  it('идёт расчёт: доля прошедшего времени против ETA', () => {
    expect(progressPct('running', 30, 30)).toBe(50);
    expect(progressPct('running', 90, 30)).toBe(75);
  });

  it('работающий прогон никогда не показывает 100% до конца', () => {
    expect(progressPct('running', 1000, 0)).toBe(97);
    expect(progressPct('queued', 3600, 1)).toBe(97);
  });

  it('в очереди сразу после старта — почти ноль, но не отрицательный', () => {
    expect(progressPct('queued', 0, 600)).toBe(0);
    expect(progressPct('queued', 1.5, 600)).toBe(0);
  });

  it('ETA нет — тик от времени, а не мёртвый ноль', () => {
    expect(progressPct('running', 0, null)).toBe(0);
    expect(progressPct('running', 60, null)).toBe(20);
    expect(progressPct('running', 9000, undefined)).toBe(90);   // потолок без ETA
  });
});
