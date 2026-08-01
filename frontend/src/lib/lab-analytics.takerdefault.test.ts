// Модель комиссии по умолчанию = ТЕЙКЕР. Пин, а не украшение.
//
// Баг, который это ловит: карточка бумажного робота (RobotWindow) считала комиссию
// по МЕЙКЕРСКОЙ модели — только брокерские 0.45 ₽ с филла, без биржевого сбора.
// На Williams %R (GZU6, 2 535 филлов) это дало 1 141 ₽ вместо ~5 957 ₽, и робот
// показывал +2 715 ₽ там, где по-честному −2 101 ₽. Оператор на этом основании
// счёл его перспективным (02.08.2026). Робот, отправленный на агент, кроссит
// спред — значит платит биржевую часть; так считают бэктест, раннер (taker_points)
// и журнал algo_trades. Разъезд моделей = стенд, противоречащий сам себе.
import { describe, expect, it } from 'vitest';
import { commissionBreakdown, commissionFor, rolledPnl } from './lab-analytics';

const GZ = 'GZU6';          // группа «акции»: биржевой сбор 0.0198% от объёма
const PRICE = 9600;         // цена газа в пунктах, ₽/пункт = 1

describe('умолчание модели комиссии', () => {
  it('rolledPnl без явного флага считает ТЕЙКЕРА', () => {
    const fills = [
      { time: 1, symbol: GZ, side: 'buy', qty: 1, price: PRICE },
      { time: 2, symbol: GZ, side: 'sell', qty: 1, price: PRICE },
    ];
    const def = rolledPnl(fills, 1);                  // без флага
    const taker = rolledPnl(fills, 1, true);
    const maker = rolledPnl(fills, 1, false);
    expect(def.net).toBeCloseTo(taker.net, 6);
    expect(def.net).not.toBeCloseTo(maker.net, 6);
  });

  it('вход-выход в ноль стоит биржевой сбор, а не только брокерский', () => {
    // Цена входа = цене выхода, значит весь результат — уплаченная комиссия.
    const fills = [
      { time: 1, symbol: GZ, side: 'buy', qty: 1, price: PRICE },
      { time: 2, symbol: GZ, side: 'sell', qty: 1, price: PRICE },
    ];
    const paid = -rolledPnl(fills, 1).net;
    const brokerOnly = 2 * 0.45;
    expect(paid).toBeGreaterThan(brokerOnly * 3);     // мейкер занижал в ~5 раз
    expect(paid).toBeCloseTo(2 * commissionFor(GZ, PRICE, 1, 1, true), 6);
  });

  it('разница моделей на газе — примерно пятикратная', () => {
    const t = commissionFor(GZ, PRICE, 1, 1, true);
    const m = commissionFor(GZ, PRICE, 1, 1, false);
    expect(m).toBeCloseTo(0.45, 6);                   // только брокер
    expect(t / m).toBeGreaterThan(4);
    expect(t / m).toBeLessThan(7);
  });

  it('разбивка по умолчанию содержит биржевую часть', () => {
    const fills = [
      { time: 1, symbol: GZ, side: 'buy', qty: 1, price: PRICE },
      { time: 2, symbol: GZ, side: 'sell', qty: 1, price: PRICE },
    ];
    const c = commissionBreakdown(fills, 1, GZ);      // без явного флага
    expect(c.exchange).toBeGreaterThan(0);
    expect(c.broker).toBeCloseTo(2 * 0.45, 6);
  });
});
