// Форматирование ЦЕНЫ инструмента — ЕДИНСТВЕННОЕ разрешённое место.
// ПРАВИЛО (баг ловлен ДВАЖДЫ, 07.2026): цену НИКОГДА не Math.round и не toFixed(0) —
// у BR шаг цены 0.01, и 92.62 превращался в 93 в таблицах сделок. Дробная часть
// печатается как есть; максимум 6 знаков гасит только float-пыль (92.62000000001).
export const fmtPrice = (v: number | null | undefined): string =>
  v == null || !Number.isFinite(Number(v))
    ? '—'
    : Number(v).toLocaleString('ru-RU', { maximumFractionDigits: 6 });

// То же для CSV: точное значение, запятая как десятичный разделитель (Excel RU),
// БЕЗ разрядных пробелов (они ломают числовой тип колонки).
export const csvPrice = (v: number | null | undefined): string =>
  v == null ? '' : String(v).replace('.', ',');
