// Книга умных ручных заявок, одна на приложение.
//
// Читают её двое: фрейм «Заявки» и главный график (уровни срабатывания). Пока
// у каждого был свой опрос, они показывали разное состояние одной и той же
// заявки. Один опрос — один ответ у всех.

import { fetchWithAuth } from '$lib/fetch-auth';

export interface SmartOrder {
  so_id: string;
  kind: 'sl' | 'tp' | 'trail_tp' | 'on_fill';
  code: string;
  side: 'buy' | 'sell';
  qty: number;
  trigger_price: number;
  trail_offset: number;
  sl_offset: number;      // защитный стоп после входа, пункты (0 = без стопа)
  parent_id: string;      // у защитного стопа — заявка, которая его породила
  watch_client_id: string;
  child_price: number;
  oco_group: string;
  good_till_ms: number;
  status: string;
  note: string;
  peak: number;
  activated: boolean;
  created_ms: number;
  fired_ms: number;
  fired_client_id: string;
}

let _orders = $state<SmartOrder[]>([]);
let _loaded = $state(false);
let _error = $state('');
let timer: ReturnType<typeof setInterval> | null = null;
let users = 0;

async function load(): Promise<void> {
  try {
    const res = await fetchWithAuth('/api/v1/quik/smart-orders');
    if (!res.ok) { _error = `HTTP ${res.status}`; return; }
    _orders = (await res.json()).orders || [];
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
  get armed(): SmartOrder[] { return _orders.filter((o) => o.status === 'armed'); },
  get loaded(): boolean { return _loaded; },
  get error(): string { return _error; },
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
