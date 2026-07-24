// Комиссия входа на ЧАСТИЧНЫХ выходах: она принадлежит контрактам, а не позиции.
//
// Баг, который это ловит: carriedFee списывался ЦЕЛИКОМ на каждом частичном
// закрытии и не уменьшался, поэтому позиция, набранная усреднением и распущенная
// кусками, платила комиссию входа заново столько раз, сколько было выходов.
// На живом MACD·RIU6 (18 контрактов, 155 частичных выходов) это превращало
// +129 тыс ₽ в −103 тыс ₽ и рисовало стену несуществующих SL.
import { describe, expect, it } from 'vitest';
import { tradeEvents } from './lab-analytics';

// Тейкерская модель: комиссия заметная (биржевой сбор от объёма), на ней баг видно.
const ev = (fills: any[]) => tradeEvents(fills, 60, 1, 'RIU6', true);

describe('комиссия входа на частичных выходах', () => {
  it('вход 4 контракта и 4 выхода по одному стоят столько же, сколько один выход всеми', () => {
    const open = { time: 1, side: 'buy', qty: 4, price: 100_000 };
    const byOne = ev([open,
      { time: 2, side: 'sell', qty: 1, price: 100_000 },
      { time: 3, side: 'sell', qty: 1, price: 100_000 },
      { time: 4, side: 'sell', qty: 1, price: 100_000 },
      { time: 5, side: 'sell', qty: 1, price: 100_000 },
    ]);
    const atOnce = ev([open, { time: 2, side: 'sell', qty: 4, price: 100_000 }]);

    const sum = (list: any[]) => list.filter((e) => e.close)
      .reduce((a, e) => a + e.close.pnl, 0);
    // Цена входа = цене выхода, поэтому весь результат — это уплаченная комиссия.
    // Дробить выход на четыре части не должно стоить дороже.
    expect(sum(byOne)).toBeCloseTo(sum(atOnce), 6);
    expect(sum(byOne)).toBeLessThan(0);
  });

  it('частичный выход половиной позиции забирает половину комиссии входа', () => {
    const [, half] = ev([
      { time: 1, side: 'buy', qty: 2, price: 100_000 },
      { time: 2, side: 'sell', qty: 1, price: 100_000 },
    ]);
    const [, full] = ev([
      { time: 1, side: 'buy', qty: 2, price: 100_000 },
      { time: 2, side: 'sell', qty: 2, price: 100_000 },
    ]);
    // Половина закрытия несёт половину комиссии входа + свою комиссию выхода,
    // поэтому по модулю она дешевле полного выхода, но не вдвое.
    expect(Math.abs(half.close!.pnl)).toBeLessThan(Math.abs(full.close!.pnl));
    expect(half.kind).toBe('partial');
  });

  it('выход в ноль по цене входа не должен считаться убытком из-за двойной комиссии', () => {
    // 3 контракта усреднением, выход тремя кусками ровно по средней цене.
    const events = ev([
      { time: 1, side: 'buy', qty: 1, price: 100_000 },
      { time: 2, side: 'buy', qty: 1, price: 99_000 },
      { time: 3, side: 'buy', qty: 1, price: 98_000 },
      { time: 4, side: 'sell', qty: 1, price: 99_000 },
      { time: 5, side: 'sell', qty: 1, price: 99_000 },
      { time: 6, side: 'sell', qty: 1, price: 99_000 },
    ]);
    const closes = events.filter((e) => e.close);
    const net = closes.reduce((a, e) => a + e.close!.pnl, 0);
    // Чистый результат — только комиссия шести филлов, а не её кратное повторение.
    const oneFillFee = Math.abs(ev([
      { time: 1, side: 'buy', qty: 1, price: 99_000 },
      { time: 2, side: 'sell', qty: 1, price: 99_000 },
    ])[1].close!.pnl) / 2;
    expect(net).toBeGreaterThan(-oneFillFee * 6 * 1.05);
    expect(closes.length).toBe(3);
  });
});
