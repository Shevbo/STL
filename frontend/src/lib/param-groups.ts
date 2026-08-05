/** Смысловая группировка параметров робота для развёрнутой панели.
 *
 * Порядок групп — порядок вопросов, которые оператор задаёт роботу: по какому
 * сигналу входим, каким объёмом, чем выходим, как часто и что вообще разрешено.
 * Алфавит и порядок схемы для этого бесполезны: в списке из 19 полей `sl_frac`
 * стоял между `signal` и `symbol`, хотя относится к выходам.
 *
 * Ключ, не попавший ни в одну группу, уходит в «Прочее» — панель не имеет права
 * ПОТЕРЯТЬ параметр: невидимое поле продолжает влиять на живого робота.
 */

export type Group = { id: string; title: string; note: string; keys: string[] };

export const GROUPS: Group[] = [
  { id: 'signal', title: 'Сигнал', note: 'по чему робот решает, куда становиться',
    keys: ['fast', 'slow', 'signal', 'period', 'ema_period', 'rsi_period', 'atr_period',
           'mult', 'multiplier', 'oversold', 'overbought', 'lookback', 'impulse_frac',
           'level', 'open_hour', 'open_min', 'range_min', 'rr_x10'] },
  { id: 'size', title: 'Объём и лестница', note: 'сколько контрактов и как добирает',
    keys: ['qty', 'avg_max', 'avg_step_atr', 'avg_atr_n', 'k_avg',
           'bet_step', 'bet_max', 'super_y', 'super_z'] },
  { id: 'exit', title: 'Выходы', note: 'чем робот закрывает позицию',
    keys: ['tp_atr', 'sl_frac', 'sl_pct'] },
  { id: 'rate', title: 'Частота', note: 'что тормозит вход и доборы',
    keys: ['min_gap_pts', 'gap_auto', 'nd_days', 'cooldown_min', 'cooldown_pct'] },
  { id: 'allow', title: 'Разрешения', note: 'какие стороны роботу открыты',
    keys: ['allow_long', 'allow_short', 'exit_only'] },
];

/** Поля 0/1 — им нужен переключатель, а не числовое поле: `true` в текстовом
 *  инпуте оператор читает как значение, которое надо набрать руками. */
export const BINARY = new Set(['allow_long', 'allow_short', 'exit_only', 'gap_auto']);

/** Повышение этих параметров ОГРАНИЧИВАЕТ робота (стоп, паузы, разножка). Метка
 *  нужна не для красоты: в одном столбце соседствуют «добирай больше» и «стой
 *  дольше», и на глаз они неразличимы. */
export const BOUNDING = new Set(['sl_frac', 'sl_pct', 'min_gap_pts',
                                 'cooldown_min', 'cooldown_pct', 'exit_only']);

export type Field = { key: string; label: string; type?: string; min?: number; max?: number };

/** Разложить схему по группам, сохранив ВСЕ поля. `symbol` и всё неопознанное —
 *  в «Прочее», иначе панель молча спрячет действующий параметр. */
export function groupFields(schema: Field[]): { group: Group; fields: Field[] }[] {
  const byKey = new Map(schema.map((f) => [f.key, f]));
  const used = new Set<string>();
  const out: { group: Group; fields: Field[] }[] = [];
  for (const g of GROUPS) {
    const fields = g.keys.map((k) => byKey.get(k)).filter(Boolean) as Field[];
    for (const f of fields) used.add(f.key);
    if (fields.length) out.push({ group: g, fields });
  }
  const rest = schema.filter((f) => !used.has(f.key));
  if (rest.length)
    out.push({ group: { id: 'rest', title: 'Прочее', note: 'инструмент и всё остальное', keys: [] },
               fields: rest });
  return out;
}

/** Шаг стрелки. Целые периоды ходят по 1, доли (×10 / проценты) — по 5, чтобы
 *  дотянуть от 0 до 200 не стоило сорока кликов. */
export function stepFor(f: Field): number {
  const span = (f.max ?? 0) - (f.min ?? 0);
  if (BINARY.has(f.key)) return 1;
  if (span >= 400) return 25;
  if (span >= 120) return 5;
  return 1;
}
