// Профили доведения умной заявки до исполнения — и ВХОДА, и ВЫХОДА.
//
// 29.09.2026 родной стоп оператора на 70 RIZ6 сработал с лимитом в 30 пунктов от
// уровня, рынок за минуту прошёл 690 пунктов, заявка умерла с нулём исполнения,
// позиция осталась открытой. Отсюда трёхфазное доведение: постоять у планки,
// догнать цену, добить по рынку.
//
// Секунды фаз не универсальны, и это назвал сам оператор: на тихом рынке и малой
// позиции спешить незачем, можно ждать по три минуты и выйти по хорошей цене; на
// быстром рынке и крупной позиции ждать нельзя вовсе. Поэтому профиль выбором, а
// не три поля на форме.

export interface ExecProfile {
  hold_sec: number;
  chase_sec: number;
  chase_every_sec: number;
  /** Добивать ли остаток РЫНОЧНОЙ заявкой. false у «нормального». */
  market: boolean;
  title: string;
}

export type ExecProfiles = Record<string, ExecProfile>;

export const DEFAULT_PROFILE = 'aggressive';
/** Штатные три. Движок их не удаляет никогда: заявка, сославшаяся на исчезнувший
 *  профиль, осталась бы без доведения молча. Экран обязан знать те же имена,
 *  чтобы не давать их стереть кнопкой. */
export const BUILTIN = ['aggressive', 'active', 'normal'];

/** Сколько секунд заявка может простоять, прежде чем её добьют по рынку.
 *  null — добивать не будут вовсе («нормальный»): числа там нет и быть не может. */
export function guaranteedSec(p: ExecProfile | null | undefined): number | null {
  if (!p) return null;
  if (!p.market) return null;
  return Math.max(0, p.hold_sec || 0) + Math.max(0, p.chase_sec || 0);
}

function human(sec: number): string {
  if (sec < 60) return `${sec} с`;
  const m = Math.floor(sec / 60), s = sec % 60;
  return s ? `${m} мин ${s} с` : `${m} мин`;
}

/** Итог одной строкой — оператор ставит профиль, глядя именно на него.
 *
 *  «Нормальный» НЕ называем нулём секунд. Ноль в фазах означает «сразу по
 *  рынку», а нормальный — «по рынку никогда», и разница в том, кто отвечает за
 *  неисполнение: там это человек, который так выбрал. Написать их одинаково
 *  значит спрятать от него этот выбор. */
export function guaranteeText(p: ExecProfile | null | undefined): string {
  if (!p) return '';
  if (!p.market) {
    return 'по рынку НИКОГДА: заявка стоит лимитом. Не нальют — не исполнится, '
      + 'и за это отвечает тот, кто выбрал этот профиль';
  }
  const n = guaranteedSec(p) ?? 0;
  if (!n) return 'по рынку СРАЗУ: ждать не будем вовсе';
  return `выход гарантирован за ${human(n)}: ${human(p.hold_sec)} у цены заявки`
    + (p.chase_sec ? `, затем ${human(p.chase_sec)} догоняем цену` : '')
    + ', дальше добиваем по рынку';
}

/** Предупреждение о длинных фазах: это выбор оператора, но следствие он обязан
 *  видеть. Порог — минута: при штатных 10 + 10 с строки быть не должно, иначе её
 *  перестанут читать. */
export function longWaitWarn(p: ExecProfile | null | undefined): string {
  const n = guaranteedSec(p);
  if (n == null || n < 60) return '';
  return `заявка может простоять в рынке до ${human(n)}, прежде чем добьёт остаток по рынку`;
}

/** Профиль заявки по её полю `esc_profile`.
 *
 *  Пустое — штатный по умолчанию. НЕЗНАКОМОЕ ИМЯ не ошибка экрана: движок в этом
 *  случае откатывается к штатному, и панель обязана показывать то же самое, а не
 *  пустое место. Опечатка не лишает заявку доведения. */
export function profileOf(profiles: ExecProfiles, name: string | undefined | null): ExecProfile | null {
  const key = String(name || '').trim();
  return profiles[key] ?? profiles[DEFAULT_PROFILE] ?? null;
}

/** Порядок в селекте: штатные в своём порядке, дальше пользовательские по
 *  алфавиту. Отдавать «как пришло» значит менять порядок пунктов между
 *  загрузками — оператор выбирает мышкой, и прыгающий список опасен. */
export function profileNames(profiles: ExecProfiles): string[] {
  const own = Object.keys(profiles).filter((n) => !BUILTIN.includes(n)).sort();
  return [...BUILTIN.filter((n) => n in profiles), ...own];
}

/** Что не так с профилем, которого оператор правит. Пусто — сохранять можно. */
export function profileError(p: Partial<ExecProfile>): string {
  const nums: Array<[string, number | undefined]> = [
    ['ожидание у цены', p.hold_sec], ['погоня за ценой', p.chase_sec],
    ['шаг перестановки', p.chase_every_sec],
  ];
  for (const [what, v] of nums) {
    if (v == null || !Number.isFinite(v)) return `${what}: нужно число секунд`;
    if (v < 0) return `${what}: секунды не бывают отрицательными`;
    if (!Number.isInteger(v)) return `${what}: секунды целые`;
  }
  // Погоня без шага перестановки — это фаза, которая ничего не делает: заявка
  // просто стоит, а оператор думает, что она идёт за ценой.
  if ((p.chase_sec ?? 0) > 0 && !(p.chase_every_sec ?? 0)) {
    return 'погоня задана, а шаг перестановки нулевой: заявка будет просто стоять';
  }
  if (!String(p.title || '').trim()) return 'подпись обязательна: по ней профиль выбирают на форме';
  return '';
}
