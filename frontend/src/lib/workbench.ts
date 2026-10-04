// Рабочее место бэктеста: логика экрана редакций (кнопки, статусы, причины отказа).
//
// Заказ оператора 04.10.2026, контракт — docs/backtest-workbench-spec.md. Правила (кто
// что может, когда создание отклоняется) живут на СЕРВЕРЕ (trader/api/lab_workbench):
// экран их повторяет только затем, чтобы заранее сказать человеку ПОЧЕМУ кнопка не
// работает, а не нажимать её вслепую. Сервер всё равно проверит заново.
//
// Принцип страницы прежний: молчащая кнопка хуже честного «нельзя, потому что».

export interface WorkerState {
  alive: boolean; age_s: number | null; busy_with: number | null;
  version: string | null; worker_id: string | null;
}
export interface Revision {
  id: number; card: string; rev: number; parent: number | null; status: string;
  message?: string; change_note?: string | null; code_ref?: string | null;
  created_by?: string; created_at?: number; updated_at?: number;
  accepted_by?: string | null; accepted_at?: number | null; diff_sha?: string | null;
  log?: string; diff?: string | null; gates?: Record<string, any> | null; params?: Record<string, any> | null;
}

export const MESSAGE_MAX = 4000;
/** Редакция «в работе»: пока она есть, новую поверх не создать (одна рабочая на карточку). */
export const OPEN_STATUSES = ['queued', 'working', 'gates'];
export const isOpen = (r: Pick<Revision, 'status'>) => OPEN_STATUSES.includes(r.status);

/** Воркер одной фразой. `age_s: null` — не появлялся НИ РАЗУ: это не «упал». */
export function workerLine(w: WorkerState | null | undefined): string {
  if (!w) return 'состояние воркера неизвестно';
  if (w.age_s == null) return 'воркер ещё ни разу не выходил на связь';
  const ago = w.age_s < 90 ? `${Math.round(w.age_s)} с назад` : `${Math.floor(w.age_s / 60)} мин назад`;
  if (w.alive) return `воркер жив${w.version ? ` (${w.version})` : ''}, сигнал ${ago}`;
  return `воркер не отвечает: последний сигнал ${ago}`;
}

export interface Can { ok: boolean; why: string }

/** Можно ли создать редакцию. Причина отказа — человеческая: кнопка без объяснения —
 *  то же, что сломанная (так было с «Обычным режимом» сетки). */
export function canCreate(
  w: WorkerState | null | undefined, revs: Revision[], loaded: boolean,
): Can {
  if (!loaded) return { ok: false, why: 'состояние рабочего места ещё не загружено' };
  if (!w || !w.alive) return { ok: false, why: workerLine(w) };
  const open = revs.find(isOpen);
  if (open) return { ok: false, why: `в карточке уже есть редакция ${open.rev} в работе (${statusLabel(open.status)})` };
  return { ok: true, why: '' };
}

const LABEL: Record<string, string> = {
  draft: 'черновик', queued: 'в очереди', working: 'воркер правит', gates: 'идут ворота',
  ready: 'готова к приёмке', failed: 'не вышло', accepted: 'принята',
};
/** Незнакомый статус печатаем кодом: новый статус сервера не должен исчезнуть молча. */
export const statusLabel = (s: string) => LABEL[s] ?? s;

export type Tone = 'wait' | 'run' | 'ok' | 'bad' | 'done' | 'unk';
export function statusTone(s: string): Tone {
  switch (s) {
    case 'queued': return 'wait';
    case 'working': case 'gates': return 'run';
    case 'ready': return 'ok';
    case 'failed': return 'bad';
    case 'accepted': return 'done';
    default: return 'unk';
  }
}

/** Ворота: сколько зелёных из скольких. «Зелёное» — строго `ok === true`, как на сервере:
 *  строка "true" не зелёная. Нет ворот — не «0 из 0», а честное «ворот нет». */
export function gatesSummary(g: Record<string, any> | null | undefined):
    { total: number; green: number; items: { name: string; ok: boolean }[] } {
  const items = Object.entries(g ?? {}).map(([name, v]) => ({ name, ok: !!v && typeof v === 'object' && v.ok === true }));
  return { total: items.length, green: items.filter((i) => i.ok).length, items };
}

/** Что показывать у приёмки. Принять можно только готовую редакцию, и только после того,
 *  как оператор отметил, что смотрел diff: хеш diff уйдёт на сервер вместе с приёмкой. */
export function canAccept(r: Revision | null | undefined, reviewed: boolean): Can {
  if (!r) return { ok: false, why: 'редакция не выбрана' };
  if (r.status === 'accepted') return { ok: false, why: 'уже принята' };
  if (r.status !== 'ready') return { ok: false, why: `принять можно только готовую редакцию, сейчас: ${statusLabel(r.status)}` };
  if (!r.diff || !r.diff_sha) return { ok: false, why: 'нет diff: принимать нечего' };
  const g = gatesSummary(r.gates);
  if (!g.total || g.green !== g.total) return { ok: false, why: 'ворота не все зелёные' };
  if (!reviewed) return { ok: false, why: 'отметьте, что просмотрели diff' };
  return { ok: true, why: '' };
}

/** Текст ошибки сервера: он объясняет причину лучше любой нашей формулировки. */
export function errorText(status: number, body: any): string {
  const d = body?.detail;
  if (d && typeof d === 'object' && d.text) return String(d.text);
  if (typeof d === 'string' && d) return d;
  return `HTTP ${status}`;
}

export const messageError = (m: string): string =>
  !m.trim() ? 'Опишите, что изменить.' : m.length > MESSAGE_MAX ? `Длиннее ${MESSAGE_MAX} знаков (${m.length}).` : '';
