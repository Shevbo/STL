/** План живого робота как ЦЕНОВЫЕ УРОВНИ для графика.
 *
 *  Что робот собирается делать, видно было только на его собственном экране, а на
 *  общем «Позиции и заявки на графиках» — ничего (оператор 22.09.2026).
 *
 *  ЧТО РИСУЕМ И ЧТО НЕТ. Рисуются ТОЛЬКО настоящие ценовые триггеры:
 *    • working_orders — заявки робота, реально стоящие в стакане;
 *    • signal.planned_orders — заявки, которые он выставит по следующему сигналу;
 *    • signal.exit_levels {tp, sl} — точные уровни выхода стратегии.
 *  НЕ рисуется signal.armed: «взведён» у большинства стратегий означает готовность
 *  к ПАТТЕРНУ на закрытии бара, а не касание уровня. Нарисованные линией, они уже
 *  вводили оператора в заблуждение («цена пересекла линию пять раз, робот стоит»)
 *  — этот запрет повторяет решение экрана робота, не расходясь с ним.
 */
import { asObject } from './json-field';

export interface PlanLine {
  key: string; price: number; title: string; color: string; style: number; dim?: boolean;
}

/** Цвет линий робота. Отличается от всех типов умных заявок намеренно: на одном
 *  графике рядом стоят ручные заявки оператора и планы робота, и путать, кто
 *  поставил уровень, нельзя. */
export const ROBOT_LINE_COLOR = '#4fd1c5';

const arrow = (side: unknown) =>
  String(side ?? '').toUpperCase().includes('SELL') || String(side) === 'sell' ? '▼' : '▲';

const num = (v: unknown) => {
  const n = Number(v);
  return Number.isFinite(n) && n > 0 ? n : 0;
};

/** Уровни плана всех роботов по ОДНОМУ инструменту. `code` — код агента (RIZ6). */
export function robotPlanLines(robots: any[], code: string): PlanLine[] {
  const out: PlanLine[] = [];
  if (!code) return out;
  for (const r of robots || []) {
    if (String(r?.symbol ?? '') !== code) continue;
    // Пауза — не остановка планов, но и не исполнение: показываем тише.
    const paused = !!r?.paused;
    const who = String(r?.display_name || r?.robot_id || 'робот');
    const id = String(r?.robot_id ?? who);
    const sig = asObject(r?.signal_json, null as any);

    for (const [i, w] of (r?.working_orders ?? []).entries()) {
      const price = num(w?.price);
      if (!price) continue;
      out.push({ key: `rb:${id}:w${w?.order_id ?? i}`, price,
                 title: `${arrow(w?.side)} ${who} · в стакане ${num(w?.qty) || ''}`.trim(),
                 color: ROBOT_LINE_COLOR, style: 0, dim: paused });
    }
    for (const [i, p] of (sig?.planned_orders ?? []).entries()) {
      const price = num(p?.price);
      if (!price) continue;
      const why = String(p?.reason ?? '').trim();
      out.push({ key: `rb:${id}:p${i}`, price,
                 title: `${arrow(p?.side)} ${who} · ${why || 'план'}`,
                 color: ROBOT_LINE_COLOR, style: 2, dim: true });
    }
    const el = sig?.exit_levels;
    if (el && num(el.tp) && num(el.sl)) {
      const side = Number(el.dir) > 0 ? 'sell' : 'buy';
      out.push({ key: `rb:${id}:tp`, price: num(el.tp),
                 title: `${arrow(side)} ${who} · тейк`,
                 color: ROBOT_LINE_COLOR, style: 2, dim: paused });
      out.push({ key: `rb:${id}:sl`, price: num(el.sl),
                 title: `${arrow(side)} ${who} · стоп`,
                 color: ROBOT_LINE_COLOR, style: 2, dim: paused });
    }
  }
  return out;
}

/** Коды инструментов, по которым у живых роботов есть позиция или план: такие
 *  инструменты обязаны попасть в сетку графиков, даже когда ручных заявок нет. */
export function robotCodes(robots: any[]): string[] {
  const out = new Set<string>();
  for (const r of robots || []) {
    const code = String(r?.symbol ?? '');
    if (!code) continue;
    if (Number(r?.position ?? 0) !== 0 || (r?.working_orders ?? []).length
        || robotPlanLines([r], code).length) out.add(code);
  }
  return [...out].sort();
}
