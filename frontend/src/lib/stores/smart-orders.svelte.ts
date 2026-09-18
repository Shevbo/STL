// Книга умных ручных заявок, одна на приложение.
//
// Читают её двое: фрейм «Заявки» и главный график (уровни срабатывания). Пока
// у каждого был свой опрос, они показывали разное состояние одной и той же
// заявки. Один опрос — один ответ у всех.

import { fetchWithAuth } from '$lib/fetch-auth';

export interface SmartOrder {
  so_id: string;
  kind: 'sl' | 'tp' | 'trail_tp' | 'on_fill' | 'trail_sl';
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
