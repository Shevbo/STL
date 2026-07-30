import { describe, it, expect } from 'vitest';
import { buildTitle, titleFromQuery, TITLE_SUFFIX } from './page-title';

const q = (s: string) => new URLSearchParams(s);

describe('заголовок закладки', () => {
  it('конкретика идёт ПЕРВОЙ: в узкой закладке видны только первые символы', () => {
    expect(buildTitle('agent-ob-BRU6-v1 · РЕАЛ')).toBe('agent-ob-BRU6-v1 · РЕАЛ · STL');
    expect(buildTitle('agent-ob-BRU6-v1 · РЕАЛ').startsWith('agent-ob')).toBe(true);
  });

  it('без содержания остаётся только продукт', () => {
    expect(buildTitle('')).toBe(TITLE_SUFFIX);
    expect(buildTitle(null)).toBe(TITLE_SUFFIX);
    expect(buildTitle('   ')).toBe(TITLE_SUFFIX);
  });
});

describe('заголовок по URL', () => {
  it('каждый экран называет себя', () => {
    expect(titleFromQuery(q('agent_robot=agent-ob-BRU6-v1'))).toBe('Робот agent-ob-BRU6-v1');
    expect(titleFromQuery(q('strategy=order_block'))).toBe('Стратегия order_block');
    expect(titleFromQuery(q('campaign=camp-20260713-brq6orderblock')))
      .toBe('Перебор camp-20260713-brq6orderblock');
    expect(titleFromQuery(q('orders=1'))).toBe('Заявки');
    expect(titleFromQuery(q('tables=1'))).toBe('Таблицы QUIK');
    expect(titleFromQuery(q('equity=1'))).toBe('Доходность роботов');
    expect(titleFromQuery(q(''))).toBe('Терминал');
  });

  it('вкладка лаборатории названа по-человечески, неизвестная — как дефолт экрана', () => {
    expect(titleFromQuery(q('lab=backtest'))).toBe('Лаборатория · Backtest Lab');
    expect(titleFromQuery(q('lab=botstore'))).toBe('Лаборатория · Botstore');
    expect(titleFromQuery(q('lab='))).toBe('Лаборатория · Live Robots');
    expect(titleFromQuery(q('lab=чтотоневедомое'))).toBe('Лаборатория · Live Robots');
  });

  it('порядок совпадает с выбором экрана в App: титул про ВИДИМЫЙ экран', () => {
    // ?strategy рисуется поверх всего, ?agent_robot — поверх терминала с фреймами.
    expect(titleFromQuery(q('strategy=fvg&agent_robot=r1&orders=1'))).toBe('Стратегия fvg');
    expect(titleFromQuery(q('agent_robot=r1&orders=1'))).toBe('Робот r1');
  });
});
