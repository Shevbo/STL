// Заявки мышкой ПРЯМО НА ГРАФИКЕ: что означает клик и что из него получится.
//
// Вся арифметика жеста живёт здесь, а не в компоненте: клики по канве тестом не
// проверить, а СТОРОНУ заявки и её цену проверить обязательно — это реальные
// деньги. Компонент отвечает только за пиксели и подтверждение.
//
// Правила стороны выведены из движка (trader/quik/smart_orders.py,
// `_trigger_hit`), а не придуманы:
//   sl  — ПРОДАЖА срабатывает при цене <= уровня, ПОКУПКА при >=;
//   tp  — ПРОДАЖА при >= цели, ПОКУПКА при <=;
//   trail_tp — активация: ПРОДАЖА при >= уровня, ПОКУПКА при <=.
// Значит клик НИЖЕ рынка это защита лонга (продажа) для условной и вход
// покупкой для лимитной, а клик ВЫШЕ — наоборот.

import type { Kind, Side } from './smart-order-help';

/** Модификатор -> тип заявки. Правый клик открывает меню и сюда не приходит. */
export function kindFromEvent(e: { shiftKey: boolean; ctrlKey: boolean; altKey: boolean; metaKey?: boolean }): Kind | null {
  // Порядок проверок фиксирован: с двумя зажатыми клавишами выигрывает более
  // «безопасная» условная — она чаще всего защитная, и ошибиться в её пользу
  // дешевле, чем выставить лишнюю следящую.
  if (e.shiftKey) return 'sl';
  if (e.ctrlKey || e.metaKey) return 'tp';
  if (e.altKey) return 'trail_tp';
  return null;
}

/**
 * Сторона заявки по тому, где кликнули относительно рынка.
 * `null` — сторону вывести нельзя (зависимая заявка не про уровень), оператор
 * выбирает её сам. Молча подставлять «продажу» тут нельзя.
 */
export function sideFor(kind: Kind, price: number, last: number): Side | null {
  if (!(price > 0) || !(last > 0)) return null;
  if (kind === 'on_fill') return null;
  if (kind === 'sl') return price < last ? 'sell' : 'buy';
  return price > last ? 'sell' : 'buy';   // tp, trail_tp
}

/** Цена на сетку инструмента. Шаг неизвестен — не выдумываем, отдаём как есть. */
export function quantize(price: number, step: number): number {
  if (!(step > 0) || !(price > 0)) return price;
  const q = Math.round(price / step) * step;
  // Плавающая арифметика: 0.1 * 3 = 0.30000000000000004. Округляем до числа
  // знаков самого шага, иначе цена приезжает с хвостом из пятнадцати нулей.
  const dec = (String(step).split('.')[1] || '').length;
  return Number(q.toFixed(dec));
}

export interface Draft {
  kind: Kind;
  code: string;
  side: Side;
  qty: number;
  /** Уровень срабатывания (для зависимой — цена дочерней заявки). */
  price: number;
}

/** Тело запроса для POST /api/v1/quik/smart-orders. Поля, которых у типа нет,
 *  отправляем нулями — движок их и ждёт нулями (см. SmartOrder.validate). */
export function draftBody(d: Draft): Record<string, unknown> {
  return {
    kind: d.kind,
    code: d.code,
    side: d.side,
    qty: Math.max(1, Math.floor(d.qty)),
    trigger_price: d.kind === 'on_fill' ? 0 : d.price,
    child_price: d.kind === 'on_fill' ? d.price : 0,
    trail_offset: 0,
    sl_offset: 0,
    tp_offset: 0,
    watch_client_id: '',
    oco_group: '',
    good_till_ms: 0,
  };
}

/** Насколько далеко от уровня можно «взяться» за него мышкой, в пикселях. */
export const GRAB_PX = 4;

/**
 * Уровень под курсором: ближайший в пределах GRAB_PX.
 * Возвращает индекс или -1. Сравниваем в ПИКСЕЛЯХ, а не в цене: на растянутом
 * по вертикали графике одинаковый зазор в рублях это совсем разное расстояние
 * для глаза и для мыши.
 */
export function levelAt(y: number, levels: Array<{ y: number }>): number {
  let best = -1, bestD = GRAB_PX + 1;
  for (let i = 0; i < levels.length; i++) {
    const d = Math.abs(levels[i].y - y);
    if (d <= GRAB_PX && d < bestD) { best = i; bestD = d; }
  }
  return best;
}

// ── Шаг цены инструмента ────────────────────────────────────────────────────
// Один запрос на всю страницу: /api/v1/quik/params отдаёт таблицу целиком, и
// тянуть её отдельно каждым графиком незачем. Шаг нужен, чтобы цена из клика
// легла на сетку биржи: цену, которой не может быть, нельзя ни рисовать, ни
// отправлять (правило платформы).
let stepsPromise: Promise<Record<string, number>> | null = null;

export function priceSteps(
  fetcher: (u: string) => Promise<Response>,
): Promise<Record<string, number>> {
  if (!stepsPromise) {
    stepsPromise = fetcher('/api/v1/quik/params')
      .then((r) => (r.ok ? r.json() : { rows: [] }))
      .then((d) => {
        const out: Record<string, number> = {};
        for (const row of d?.rows ?? []) {
          const step = Number(row?.price_step || 0);
          if (row?.code && step > 0) out[String(row.code)] = step;
        }
        return out;
      })
      .catch(() => ({}));
  }
  return stepsPromise;
}

/** Только для тестов: сбросить кэш шагов. */
export function _resetSteps(): void { stepsPromise = null; }
