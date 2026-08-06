import { describe, expect, it } from 'vitest';
import { asObject } from './json-field';

/** Регрессия 06.08.2026: ручка стенда стала отдавать params_json СЛОВАРЁМ (иначе
 *  Лаборатория рассыпала строку на символы), а стенд робота делал голый JSON.parse.
 *  JSON.parse от объекта бросает — catch отдавал {}, и со стенда исчезли ВСЕ
 *  параметры разом: список пустой, лист параметров рисовал одну строку `symbol`. */
describe('asObject терпит оба вида поля', () => {
  it('строка разбирается', () => {
    expect(asObject('{"qty":2,"fast":57}', {})).toEqual({ qty: 2, fast: 57 });
  });
  it('объект проходит как есть — ЭТО и сломалось', () => {
    const o = { qty: 1, exit_only: true };
    expect(asObject(o, {})).toBe(o);
  });
  it('пусто и мусор дают запасное значение, а не исключение', () => {
    expect(asObject(null, {})).toEqual({});
    expect(asObject('', {})).toEqual({});
    expect(asObject('{не json', {})).toEqual({});
    expect(asObject('42', {})).toEqual({});      // валидный JSON, но не объект
    expect(asObject(undefined, null)).toBe(null);
  });
});
