// «Изменить» обязано вернуть в форму ВСЕ поля своего типа.
//
// 01.10.2026 оператор нажал «изменить» на живом коридоре 106fbdeca7 и получил
// пустую форму: rearm знал только старый набор полей, а c_* и g_* появились
// позже. Фигуры он лишился (экран снимал заявку первой), а отредактировать не
// смог. Поля сервер отдаёт в GET целиком — их просто никто не читал.
//
// Проверяем по ИСХОДНИКУ компонента: rearm живёт внутри Svelte-компонента, и
// поднимать ради одной функции весь экран с его опросами дороже, чем сверить
// текст. Тест ловит ровно то, что случилось: новый вид заявки добавили в KINDS,
// а в rearm дописать забыли.
import fs from 'node:fs';
import path from 'node:path';
import { describe, it, expect } from 'vitest';
import { KINDS } from './smart-order-help';

const SRC = fs.readFileSync(
  path.resolve('src/components/orders/SmartOrders.svelte'), 'utf8');

function fn(name: string): string {
  const start = SRC.indexOf(`function ${name}(`);
  expect(start, `${name} не найдена`).toBeGreaterThan(-1);
  // Тело до закрывающей скобки верхнего уровня — достаточно, чтобы увидеть,
  // какие поля функция трогает.
  const rest = SRC.slice(start);
  const end = rest.indexOf('\n  }\n');
  return rest.slice(0, end > 0 ? end : 4000);
}

// Поле формы -> переменная состояния, которую обязан восстановить rearm.
const VAR_BY_FIELD: Record<string, string> = {
  trigger_price: 'trigger', trail_offset: 'trailOffset',
  sl_offset: 'slOffset', tp_offset: 'tpOffset', trail_after: 'trailAfter',
  watch_client_id: 'watchId', child_price: 'childPrice',
  c_t1_ms: 'cT1', c_p1: 'cP1', c_t2_ms: 'cT2', c_p2: 'cP2',
  c_low: 'cLow', c_low2: 'cLow2', c_stop_pts: 'cStopPts', c_flips_max: 'cFlipsMax',
  g_step: 'gStep', g_buys: 'gBuys', g_sells: 'gSells', g_lot: 'gLot',
  g_stop_pts: 'gStopPts',
};

describe('подстановка параметров в форму', () => {
  const body = fn('rearm');

  for (const k of KINDS) {
    it(`${k.id}: все поля типа возвращаются в форму`, () => {
      for (const f of k.fields) {
        const v = VAR_BY_FIELD[f.key];
        expect(v, `поле ${f.key} у «${k.name}» не описано в тесте — допишите соответствие`)
          .toBeTruthy();
        expect(body, `rearm не восстанавливает ${f.key} (${v}) для «${k.name}»`)
          .toContain(`${v} =`);
      }
    });
  }

  // База сетки — ЕДИНСТВЕННОЕ поле, которое подставлять НЕЛЬЗЯ: сервер берёт её
  // с рынка в момент приёма и присланное значение уважит, то есть сетка встала
  // бы вокруг устаревшей точки (предупреждение real-trade 01.10.2026).
  it('база сетки в форму НЕ возвращается', () => {
    // Ищем ЧТЕНИЕ поля из заявки, а не слово: про g_base в rearm стоит
    // объяснение, почему его там нет, и оно должно остаться.
    expect(body).not.toContain('any).g_base');
  });

  // У коридора qty мутируется под текущую заявку и на перевороте равен
  // удвоенному: подставив его, оператор взвёл бы фигуру вдвое крупнее.
  it('объём фигуры берётся базовый, а не текущий', () => {
    expect(body).toContain('c_qty');
  });
});

describe('порядок замены заявки', () => {
  const body = fn('edit');

  // Главное следствие аварии: снятие ПЕРЕД созданием оставляло оператора без
  // фигуры на живом рынке при любой ошибке в середине.
  it('«изменить» само по себе ничего не снимает', () => {
    expect(body).not.toContain('cancel(');
  });

  it('«изменить» помечает заявку как заменяемую', () => {
    expect(body).toContain('replacing =');
  });
});
