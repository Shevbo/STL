// Выборка комбинаций из сетки перебора БЕЗ её материализации.
//
// Движок берёт за раунд максимум несколько тысяч комбинаций, а полная декартова
// развёртка широких диапазонов — это гигабайты массивов в рендерере. 06.08.2026
// вкладка Лаборатории умирала «Опаньки» ещё ДО отправки задания (прогон bsh1):
// сетка разворачивалась целиком и только потом обрезалась до лимита.

// Больше значений на одной оси, чем движок берёт комбинаций за раунд, смысла не
// имеет: ось всё равно не будет пройдена целиком. Зажим спасает и от одной
// случайной оси «от 0 до 1e9 шагом 1», которая валит вкладку в одиночку.
export const AXIS_MAX = 5000;

export function gridSize(dims: number[][]): number {
  return dims.reduce((n, v) => n * v.length, 1);
}

/** Комбинация по её НОМЕРУ в сетке (смешанная система счисления). */
export function comboAt(dims: number[][], idx: number): number[] {
  const out: number[] = [];
  let rest = idx;
  for (let d = dims.length - 1; d >= 0; d--) {
    const len = dims[d].length;
    out[d] = dims[d][rest % len];
    rest = Math.floor(rest / len);
  }
  return out;
}

/**
 * shuffle=true — случайная точка сетки по каждой оси независимо: это и есть
 * равномерная выборка, и она не зависит от разрядности номера (сетка бывает
 * больше 2^53, где номер уже теряет младшие оси). Иначе берём подряд с начала.
 */
export function pickCombos(dims: number[][], maxC: number, shuffle: boolean): number[][] {
  const total = gridSize(dims);
  if (total === 0 || maxC <= 0) return [];
  if (total <= maxC) return Array.from({ length: total }, (_, i) => comboAt(dims, i));
  if (!shuffle) return Array.from({ length: maxC }, (_, i) => comboAt(dims, i));
  const seen = new Set<string>();
  const out: number[][] = [];
  // ponytail: отбраковка дублей попытками, а не перестановкой всей сетки. При
  // total >> maxC совпадения редки; guard не даёт зациклиться на узкой сетке.
  for (let guard = maxC * 20; out.length < maxC && guard > 0; guard--) {
    const c = dims.map(v => v[Math.floor(Math.random() * v.length)]);
    const k = c.join(',');
    if (seen.has(k)) continue;
    seen.add(k);
    out.push(c);
  }
  return out;
}

/**
 * Значения одной числовой оси. «до» меньше «от» или нулевой шаг — это ОДНО
 * значение (fallback), а НЕ пустая ось: пустая обнуляла всё произведение, и
 * монитор обещал N комбинаций, а на движок уезжал ноль.
 */
export function axisValues(
  from: unknown, to: unknown, step: unknown, fallback: number,
): { vals: number[]; clamped: boolean } {
  const f = Number(from), t = Number(to);
  const s = Math.max(0, Number(step) || 0);
  if (!(s > 0) || !Number.isFinite(f) || !Number.isFinite(t) || !(t > f)) {
    return { vals: [fallback], clamped: false };
  }
  const vals: number[] = [];
  for (let x = f; x <= t && vals.length < AXIS_MAX; x += s) vals.push(x);
  return { vals, clamped: vals.length >= AXIS_MAX };
}
