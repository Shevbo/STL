/** Поле, которое приходит то СТРОКОЙ, то уже разобранным объектом.
 *
 * `params_json` и `signal_json` живут в зеркале агента строками, а ручка стенда
 * (`/lab/robot-stand`) с 06.08.2026 нормализует их в словарь — иначе спред строки
 * рассыпал параметры на ключи "0","1","2". Потребитель, который делал голый
 * JSON.parse, после этого начал молча получать пустоту: JSON.parse от объекта
 * бросает, а catch отдавал {} — и на стенде исчезли ВСЕ параметры разом.
 *
 * Поэтому разбор один на всех и терпит оба вида.
 */
export function asObject<T = Record<string, any>>(v: unknown, fallback: T): T {
  if (v == null || v === '') return fallback;
  if (typeof v === 'object') return v as T;
  if (typeof v !== 'string') return fallback;
  try {
    const p = JSON.parse(v);
    return p && typeof p === 'object' ? (p as T) : fallback;
  } catch {
    return fallback;
  }
}
