/** Качество роботов: win rate и recovery factor с историей.
 *
 *  Экран заводится ради одного вопроса оператора: какие роботы ХОРОШИ, а не
 *  какие принесли больше денег. Перспективный робот на маленьком объёме в
 *  хит-параде по прибыли не виден, а при увеличении объёма может дать много.
 *
 *  Пять мест, где этот экран подтолкнёт к неверному решению, если соврать
 *  (предупреждения real-trade 25.09.2026, каждое из живого случая):
 *    1. Лидер по качеству мог набрать своё в ИЮЛЕ в реале, а сейчас стоять на
 *       бумаге. Строка без «сейчас: бумага» зовёт увеличить объём выключенному.
 *    2. win_rate=null это «кругов не было», recovery_factor=null это «просадки
 *       не было, RF не определён». Ноль рисовать нельзя, бесконечность тем более.
 *    3. Отрицательный RF («потерял столько-то своих просадок») читается мягче,
 *       чем есть, если рядом нет рублёвого итога.
 *    4. open_tail — филлы незакрытого круга на конце окна. Просадка считается по
 *       ЗАКРЫТЫМ кругам, и стратегия, досиживающая убыток, показывает мираж:
 *       win rate 0.93 при худшем итоге. Это у нас уже было.
 *    5. Робот может быть заблокирован агентом при живом статусе — на карточке
 *       это важнее числа сделок.
 */

export type Period = 'day' | 'week' | 'month' | 'all';
export type Bucket = 'day' | 'week' | 'month';

export const PERIOD_RU: Record<Period, string> = {
  day: 'день', week: 'неделя', month: 'месяц', all: 'вся история',
};

export interface RobotStat {
  robot_id: string;
  mode?: string;
  symbol?: string;
  trades?: number;
  fills?: number;
  open_tail?: number;
  wins?: number;
  losses?: number;
  win_rate?: number | null;
  net_rub?: number | null;
  max_drawdown_rub?: number | null;
  recovery_factor?: number | null;
  profit_factor?: number | null;
  avg_win_rub?: number | null;
  avg_loss_rub?: number | null;
  best_rub?: number | null;
  worst_rub?: number | null;
  first_ms?: number;
  last_ms?: number;
  series?: Array<{ key: string; trades?: number; wins?: number;
                   win_rate?: number | null; net_rub?: number | null; equity_rub?: number | null }>;
  current?: { mode?: string; running?: boolean; paused?: boolean;
              position?: number; symbol?: string } | null;
  alive?: boolean;
}

/** Доля выигранных кругов в процентах. null — кругов НЕ БЫЛО, и это не ноль. */
export function winPct(r: RobotStat): string {
  return r.win_rate == null ? '—' : (r.win_rate * 100).toFixed(0) + '%';
}

/** Recovery factor. null — просадки не было, RF не определён; бесконечность
 *  не печатаем никогда, иначе экран обещает вечный двигатель. */
export function rfText(r: RobotStat): string {
  const v = r.recovery_factor;
  if (v == null || !Number.isFinite(Number(v))) return '—';
  return Number(v).toFixed(2);
}

/** Чем робот занят ПРЯМО СЕЙЧАС, словами и коротко. Показывается рядом с
 *  именем, а не в подсказке: качество набрано в прошлом, решение принимается
 *  про настоящее. */
export function nowState(r: RobotStat): { text: string; kind: 'real' | 'paper' | 'off' } {
  const c = r.current;
  if (!c || r.alive === false) return { text: 'снят', kind: 'off' };
  const mode = String(c.mode ?? r.mode ?? '');
  if (c.paused) return { text: mode === 'real' ? 'реал, пауза' : 'бумага, пауза', kind: 'off' };
  if (!c.running) return { text: mode === 'real' ? 'реал, не запущен' : 'бумага, не запущен', kind: 'off' };
  return mode === 'real'
    ? { text: 'реал', kind: 'real' }
    : { text: 'бумага', kind: 'paper' };
}

/** Оговорки к строке робота. Пустой список = цифры можно читать как есть. */
export function statCaveats(r: RobotStat, blocked = false): string[] {
  const out: string[] = [];
  if (blocked) {
    out.push('Агент блокирует торговлю: заявки этого робота сейчас отклоняются,'
      + ' что бы ни показывал его статус.');
  }
  if ((r.open_tail ?? 0) > 0) {
    out.push(`Незакрытый круг на конце окна (${r.open_tail} филлов): просадка и win rate`
      + ' считаются по ЗАКРЫТЫМ кругам, поэтому у этих цифр есть невидимая часть.'
      + ' Стратегия, досиживающая убыток, так и выглядит хорошей.');
  }
  if (r.recovery_factor == null && (r.trades ?? 0) > 0) {
    out.push('Recovery factor не определён: просадки по закрытым кругам не было.'
      + ' Это не «бесконечно хорошо», это «мерить нечем».');
  }
  const cur = nowState(r);
  if (cur.kind !== 'real' && (r.net_rub ?? 0) > 0) {
    out.push(`Результат набран НЕ в текущем режиме: сейчас ${cur.text}.`
      + ' Увеличивать объём по этим цифрам нельзя, пока робот не вернётся в реал.');
  }
  return out;
}

/** Точки кривой качества для графика: накопленный результат по корзинам.
 *  Пропуски (null) НЕ соединяем — разрыв честнее прямой через пустоту. */
export function equityPoints(r: RobotStat): Array<{ key: string; v: number | null }> {
  return (r.series ?? []).map((s) => ({
    key: s.key,
    v: s.equity_rub == null || !Number.isFinite(Number(s.equity_rub)) ? null : Number(s.equity_rub),
  }));
}
