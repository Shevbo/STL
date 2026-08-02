// Фильтр истории прогонов ищет по СЛОВАМ, а не по строке целиком.
//
// Зачем пин: ссылка «Прогоны робота» с карточки графика приходит как
// «<стратегия> <тикер>» — ни одно поле строки истории не содержит обе части
// сразу, и поиск по подстроке целиком не находил НИЧЕГО. Оператор попадал в
// пустую историю на своих же прогонах (02.08.2026).
import { describe, expect, it } from 'vitest';

/** Та же логика, что в BacktestLab.histRows. */
function match(row: any, filter: string): boolean {
  const words = filter.trim().toLowerCase().split(/\s+/).filter(Boolean);
  if (!words.length) return true;
  const hay = [row.campaign, row.strategy, (row.symbols || []).join(' '), row.leader_symbol]
    .map((x: any) => String(x ?? '').toLowerCase()).join(' ');
  return words.every((w) => hay.includes(w));
}

const row = {
  campaign: 'camp-20260802-wrgdsweep',
  strategy: 'shectory_2ema',
  symbols: ['MXU6', 'RIU6'],
  leader_symbol: 'MXU6',
};

describe('фильтр истории прогонов', () => {
  it('находит по стратегии И тикеру одновременно', () => {
    expect(match(row, 'shectory_2ema MXU6')).toBe(true);
  });

  it('порядок слов не важен', () => {
    expect(match(row, 'mxu6 shectory_2ema')).toBe(true);
  });

  it('лишнее слово отсекает строку', () => {
    expect(match(row, 'shectory_2ema SiU6')).toBe(false);
  });

  it('по одному слову работает как раньше', () => {
    expect(match(row, 'wrgdsweep')).toBe(true);
    expect(match(row, 'riu6')).toBe(true);
    expect(match(row, 'нетакого')).toBe(false);
  });

  it('пустой фильтр пропускает всё', () => {
    expect(match(row, '')).toBe(true);
    expect(match(row, '   ')).toBe(true);
  });
});
