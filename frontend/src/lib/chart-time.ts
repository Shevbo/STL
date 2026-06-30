// MSK (UTC+3) display formatters for lightweight-charts. Bar timestamps stay UTC
// (unix seconds); only the AXIS + crosshair labels are rendered in Moscow time, so we
// never have to shift the data (which would desync the live last-candle update that
// mixes UTC bar times with quote timestamps). FORTS trades on MSK, so the operator
// expects MSK on the time axis — the default UTC rendering looked 3h off.

const MSK_OFFSET_MS = 3 * 3600 * 1000;
const MONTHS = ['янв', 'фев', 'мар', 'апр', 'май', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек'];

// lightweight-charts may pass a numeric UTCTimestamp (our case) or a BusinessDay.
type LwcTime = number | { year: number; month: number; day: number };

function toMskDate(time: LwcTime): Date {
  if (typeof time === 'number') {
    // shift by +3h, then read the UTC parts → Moscow wall-clock
    return new Date(time * 1000 + MSK_OFFSET_MS);
  }
  return new Date(Date.UTC(time.year, time.month - 1, time.day));
}

const p2 = (n: number) => String(n).padStart(2, '0');

// Axis tick labels. tickMarkType: 0=Year 1=Month 2=DayOfMonth 3=Time 4=TimeWithSeconds.
export function mskTickFormatter(time: LwcTime, tickMarkType: number): string {
  const d = toMskDate(time);
  switch (tickMarkType) {
    case 0: return String(d.getUTCFullYear());
    case 1: return MONTHS[d.getUTCMonth()];
    case 2: return `${p2(d.getUTCDate())} ${MONTHS[d.getUTCMonth()]}`;
    case 4: return `${p2(d.getUTCHours())}:${p2(d.getUTCMinutes())}:${p2(d.getUTCSeconds())}`;
    default: return `${p2(d.getUTCHours())}:${p2(d.getUTCMinutes())}`;
  }
}

// Crosshair / vertical-line label: full date-time in MSK.
export function mskCrosshairFormatter(time: LwcTime): string {
  const d = toMskDate(time);
  return `${p2(d.getUTCDate())}.${p2(d.getUTCMonth() + 1)} ${p2(d.getUTCHours())}:${p2(d.getUTCMinutes())}`;
}
