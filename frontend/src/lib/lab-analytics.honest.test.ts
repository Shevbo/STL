// Подпись «бумага с издержками»: в одном списке теперь соседствуют числа ДВУХ
// методик — старая бумага наливалась по цене бара без спреда, новая считает
// полспреда и комиссию тейкера (backtests 01.10.2026, метка ;honest_v1; в
// order_id). Без подписи читатель сравнивает несравнимое.
import { describe, it, expect } from 'vitest';
import { honestPaperNote, isHonestPaperFill } from './lab-analytics';

const DAY = 24 * 3600;
const OCT1 = Math.floor(Date.UTC(2026, 9, 1, 7, 0, 0) / 1000);   // 10:00 МСК

describe('метка честной бумажной наливки', () => {
  it('узнаёт помеченный филл по order_id', () => {
    expect(isHonestPaperFill({ order_id: 'paper:7;honest_v1;c=12.5' })).toBe(true);
    expect(isHonestPaperFill({ id: 'paper:7;honest_v1;c=12.5' })).toBe(true);
    expect(isHonestPaperFill({ order_id: 'paper:7' })).toBe(false);
    expect(isHonestPaperFill(null)).toBe(false);
  });

  // Дата из САМОГО РАННЕГО помеченного филла: написать «с 01.10» у робота,
  // который начал считаться честно позже, значит объявить честными числа,
  // которые ими не были.
  it('дата берётся из первого помеченного филла, а не из календаря', () => {
    const note = honestPaperNote([
      { order_id: 'paper:1', time: OCT1 - 5 * DAY },
      { order_id: 'paper:9;honest_v1;', time: OCT1 + 2 * DAY },
      { order_id: 'paper:8;honest_v1;', time: OCT1 },
    ]);
    expect(note).toBe('бумага с издержками с 01.10');
  });

  it('помеченных филлов нет — подписи нет, а не «с 01.01»', () => {
    expect(honestPaperNote([{ order_id: 'paper:1', time: OCT1 }])).toBe('');
    expect(honestPaperNote([])).toBe('');
    expect(honestPaperNote(null)).toBe('');
  });

  // Филл без времени не должен превращаться в 1970 год.
  it('метка без времени подписи не даёт', () => {
    expect(honestPaperNote([{ order_id: 'x;honest_v1;' }])).toBe('');
  });
});
