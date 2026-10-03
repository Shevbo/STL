// Книга умных ручных заявок, одна на приложение.
//
// Читают её двое: фрейм «Заявки» и главный график (уровни срабатывания). Пока
// у каждого был свой опрос, они показывали разное состояние одной и той же
// заявки. Один опрос — один ответ у всех.

import { fetchWithAuth } from '$lib/fetch-auth';
import type { Kind } from '$lib/smart-order-help';

export interface SmartOrder {
  so_id: string;
  // Вид берём ИЗ ОДНОГО перечисления с остальным экраном (smart-order-help.Kind):
  // здесь он отстал на три вида — коридор, треугольник и радиацию, — и карточка
  // лезла в KIND_BY_ID[o.kind] мимо типа. Второе перечисление видов всегда
  // отстаёт от первого, вопрос только в том, что именно пропадёт с экрана.
  kind: Kind;
  code: string;
  side: 'buy' | 'sell';
  qty: number;
  trigger_price: number;
  trail_offset: number;
  sl_offset: number;      // защитный стоп после входа, пункты (0 = без стопа)
  tp_offset: number;      // тейк после входа, пункты (0 = без тейка)
  tp_trail?: number;      // тейк после входа следящий: откат, пункты (0 = фиксированный)
  // Те же два блока, заданные ЦЕНОЙ УРОВНЯ, и подтягивающая после входа. Их не
  // было в типе, поэтому карточка о них молчала: заявка с тейком по цене
  // выглядела «без тейка» (оператор 18.09.2026, заявка 4117394fd0).
  sl_price?: number;
  tp_price?: number;
  trail_after?: number;
  parent_id: string;      // у защитного стопа — заявка, которая его породила
  watch_client_id: string;
  child_price: number;
  oco_group: string;
  good_till_ms: number;
  status: string;          // armed | native | fired | cancelled | expired | error | orphaned
  native_state?: string;   // sent | live | failed | done — как приняла защиту сторона QUIK
  native_stop_num?: string;// номер нативной стоп-заявки QUIK (строкой: ~1.9e18)
  // Доведение стопа до исполнения тремя фазами (real-trade 29.09.2026): стоим у
  // планки, идём за ценой, дальше по рынку. Необязательные: заявка, созданная до
  // этого релиза, приехала без них, и умолчания движка за неё не подставляем.
  esc_hold_sec?: number;
  esc_chase_sec?: number;
  esc_chase_every_sec?: number;
  esc_market?: boolean;    // фаза 3 уже отработала
  // Профиль доведения (29.09.2026): пустое = штатный, незнакомое имя движок
  // откатывает к штатному, а не отвергает — опечатка не лишает доведения.
  esc_profile?: string;
  // КОРИДОР (kind="corridor", 29.09.2026). Верхняя граница — прямая через две
  // точки; нижняя ПАРАЛЛЕЛЬНА ей и задана ценой в момент первой точки. Читаем
  // обратно позицию, счётчик переворотов и признак конца: статус у коридора в
  // норме «armed», и по нему не видно ни того, ни другого.
  c_t1_ms?: number; c_p1?: number;
  c_t2_ms?: number; c_p2?: number;
  c_low?: number;
  c_low2?: number;         // ТРЕУГОЛЬНИК: нижняя граница в момент c_t2_ms (свой угол)
  c_stop_pts?: number;
  c_flips_max?: number;
  c_flips?: number;
  c_pos?: number;          // + лонг, − шорт, 0 вне рынка
  c_done?: boolean;
  // ТЕКУЩИЕ стенки считает ДВИЖОК и отдаёт готовыми (real-trade 29.09.2026).
  // Панель их не пересчитывает: две реализации одной прямой расходятся, вопрос
  // только когда. width сразу в пунктах.
  c_now?: { low: number; top: number; width: number; ts_ms: number };
  // РАДИАЦИЯ (kind="grid", 30.09.2026): сетка от цены постановки g_base, уровни
  // не двигаются. g_live — что стоит в стакане сейчас, ключи flip:<уровень>
  // означают перевёрнутую сторону (исполнилось — встала встречная).
  g_step?: number;
  g_buys?: number;
  g_sells?: number;
  g_lot?: number;
  g_base?: number;
  g_stop_pts?: number;
  g_live?: Record<string, unknown>;
  g_pos?: number;
  g_done?: boolean;
  // Средняя ОТКРЫТОЙ позиции фигуры (0 = вне рынка) и режим «только на выход»
  // (real-trade 02.10.2026): в этом режиме движок ставит лишь то, что сокращает
  // позицию, и лишь по цене не хуже средней. Имена полей разные у сетки и у
  // фигур, карточка читает их через ownPosition().
  g_avg?: number;
  // ЗАЩИТА СЕТКИ (real-trade 02.10.2026): после g_trig_fills исполненных уровней
  // ИЛИ ухода цены от базы на g_trig_move_pct процентов (считая g_trig_touches
  // касаний С ОДНОЙ стороны) сетка сама переходит в «только на выход» по
  // безубытку; g_trig_ms — когда это случилось, g_rearm_min — через сколько
  // минут она встанет снова, g_rearms — сколько раз уже вставала.
  g_trig_fills?: number;
  g_trig_move_pct?: number;
  g_trig_touches?: number;
  g_trig_ms?: number;
  g_rearm_min?: number;
  g_rearms?: number;
  g_fills_done?: number;
  // ЦЕЛЬ ПРИБЫЛИ в рублях и денежный поток сетки в ПУНКТАХ (продажи плюс,
  // покупки минус). g_cash_on=false — поток ещё не заведён: сетка взведена до
  // этой механики, и счёт идёт не со взвода.
  g_tp_rub?: number;
  g_cash_pts?: number;
  g_cash_on?: boolean;
  // Сколько уровней реально стоит в стакане с каждой стороны (0 = все сразу);
  // снимается стоящее только дальше окна + 2, поэтому их бывает на две больше.
  g_window?: number;
  // ТОЧКА ОТСЧЁТА защиты по уровням (real-trade 03.10.2026): позиция, от которой
  // она считает набор, — её задаёт оператор, сняв режим «только на выход». 0 =
  // считается от нуля. Едет вслед за сокращением позиции, разворот обнуляет.
  g_guard_base?: number;
  c_avg?: number;
  exit_only?: boolean;
  note: string;
  peak: number;
  activated: boolean;
  created_ms: number;
  fired_ms: number;
  fired_client_id: string;
}

let _orders = $state<SmartOrder[]>([]);
let _session = $state<{ open: boolean | null; phase: string }>({ open: null, phase: '' });
let _loaded = $state(false);
let _error = $state('');
let timer: ReturnType<typeof setInterval> | null = null;
let users = 0;

async function load(): Promise<void> {
  try {
    const res = await fetchWithAuth('/api/v1/quik/smart-orders');
    if (!res.ok) { _error = `HTTP ${res.status}`; return; }
    const body = await res.json();
    _orders = body.orders || [];
    _session = body.session || { open: null, phase: '' };
    _loaded = true;
    _error = '';
  } catch (e: any) {
    // Книга не обновилась — держим прошлую и говорим об этом. Пустой список
    // здесь означал бы «заявок нет», а это не то же самое, что «не спросили».
    _error = e?.message || 'нет связи';
  }
}

export const smartOrdersStore = {
  get all(): SmartOrder[] { return _orders; },
  // native тоже живая: защиту держит терминал, и в истории ей не место.
  get armed(): SmartOrder[] { return _orders.filter((o) => o.status === 'armed' || o.status === 'native'); },
  get loaded(): boolean { return _loaded; },
  get error(): string { return _error; },
  /** Вне торгов сторож намеренно не срабатывает — взведённая заявка не сломана. */
  get session() { return _session; },
  get paused(): boolean { return _session.open !== true; },
  forCode(code: string): SmartOrder[] {
    return _orders.filter((o) => o.code === code);
  },
  refresh: load,
  /** Подписка с общим таймером: последний отписавшийся гасит опрос. */
  subscribe(everyMs = 2000): () => void {
    users += 1;
    if (!timer) { load(); timer = setInterval(load, everyMs); }
    return () => {
      users -= 1;
      if (users <= 0 && timer) { clearInterval(timer); timer = null; users = 0; }
    };
  },
};
