// Пределы живой торговли на странице вотчера: что уходит в PUT.
//
// Ручка устроена так, что НЕ ПРИСЛАННОЕ ПОЛЕ ОСТАЁТСЯ ПРЕЖНИМ. Значит форму
// нельзя слать целиком: правка соседнего окна, сделанная пока страница открыта,
// была бы переписана обратно молча. На умных заявках это уже стоило живого
// робота — посланный целиком набор параметров стёр ключи, о которых отправитель
// не думал.
import { describe, it, expect, beforeAll } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';

let limitsPatch: (form: any, cur: any) => Record<string, unknown>;

beforeAll(() => {
  const html = fs.readFileSync(path.resolve('public/watchdog-log.html'), 'utf8');
  const src = html.slice(html.indexOf('function limitsPatch'));
  const end = src.indexOf('\nfunction limitsPaint');
  limitsPatch = new Function(`${src.slice(0, end)}; return limitsPatch;`)() as any;
});

const CUR = {
  instrument_whitelist: ['RIZ6', 'GZZ6'],
  max_contracts_per_order: 75,
  max_working_contracts: 250,
  daily_order_cap: 500,
  price_collar_frac: 0.002,
  trading_enabled: true,
};
const SAME = {
  per_order: '75', working: '250', daily: '500', collar: '0.002',
  whitelist: 'RIZ6, GZZ6', enabled: true,
};

describe('что уходит на сервер', () => {
  it('ничего не менял — не шлём ничего', () => {
    expect(limitsPatch(SAME, CUR)).toEqual({});
  });

  it('одно поле — одно поле, остальные не трогаем', () => {
    expect(limitsPatch({ ...SAME, working: '300' }, CUR))
      .toEqual({ max_working_contracts: 300 });
  });

  it('пустое поле это «не трогать», а НЕ ноль', () => {
    // Ноль запрещает торговлю молча — движок за это отвечает 422, но и
    // отправлять его из пустого поля нельзя: оператор ничего не вводил.
    expect(limitsPatch({ ...SAME, daily: '' }, CUR)).toEqual({});
    expect(limitsPatch({ ...SAME, per_order: 'абв' }, CUR)).toEqual({});
  });

  it('ноль, введённый РУКАМИ, уходит на сервер — отказ объяснит движок', () => {
    expect(limitsPatch({ ...SAME, daily: '0' }, CUR)).toEqual({ daily_order_cap: 0 });
  });

  it('белый список разбирается по запятым, пустые куски выбрасываются', () => {
    expect(limitsPatch({ ...SAME, whitelist: ' RIZ6 ,, SiZ6 ,' }, CUR))
      .toEqual({ instrument_whitelist: ['RIZ6', 'SiZ6'] });
  });

  it('очищенный список НЕ шлётся: пустой запрещает всю торговлю молча', () => {
    // Для остановки есть мастер-флаг, и это написано в тексте отказа движка.
    expect(limitsPatch({ ...SAME, whitelist: '  , ' }, CUR)).toEqual({});
  });

  it('тот же список в другом порядке — это правка, а не шум', () => {
    expect(limitsPatch({ ...SAME, whitelist: 'GZZ6, RIZ6' }, CUR))
      .toEqual({ instrument_whitelist: ['GZZ6', 'RIZ6'] });
  });

  it('мастер-флаг уходит только когда изменился', () => {
    expect(limitsPatch({ ...SAME, enabled: false }, CUR)).toEqual({ trading_enabled: false });
    expect(limitsPatch({ ...SAME, enabled: true }, CUR)).toEqual({});
  });

  it('коллар — доля, а не проценты: 0.2 не превращается в 0.002', () => {
    expect(limitsPatch({ ...SAME, collar: '0.2' }, CUR)).toEqual({ price_collar_frac: 0.2 });
  });

  it('пустой текущий конфиг не мешает первой правке', () => {
    expect(limitsPatch(SAME, {})).toEqual({
      max_contracts_per_order: 75, max_working_contracts: 250, daily_order_cap: 500,
      price_collar_frac: 0.002, instrument_whitelist: ['RIZ6', 'GZZ6'],
      trading_enabled: true,
    });
  });
});
