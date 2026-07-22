<!-- SmartOrdersPanel — операторские умные заявки (SL / TP / Trail TP / по исполнению).
     Тонкий UI над /api/v1/quik/smart-orders: список книги + форма создания + отмена.
     Дочерние заявки уходят обычным человеческим путём (лимиты/коллар/kill-switch),
     для агента это MANUAL-класс — роботы и сверка их не видят. -->
<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import { fetchWithAuth } from '../lib/fetch-auth';
  import { downloadCSV } from '../lib/csv';
  import { ordersStore } from '$lib/stores/orders.svelte';

  let { symbol = '' }: { symbol?: string } = $props();

  const KINDS = [
    ['sl', 'SL (стоп-лосс)'],
    ['tp', 'TP (тейк-профит)'],
    ['trail_tp', 'Trail TP (скользящий)'],
    ['on_fill', 'По исполнению'],
  ] as const;
  const KIND_RU: Record<string, string> = {
    sl: 'SL', tp: 'TP', trail_tp: 'Trail', on_fill: 'По исп.',
  };
  const STATUS_RU: Record<string, string> = {
    armed: 'взведена', fired: 'исполнена', cancelled: 'отменена',
    expired: 'истекла', error: 'ошибка',
  };

  let orders = $state<any[]>([]);
  let open = $state(false);
  let msg = $state('');
  let pollTimer: ReturnType<typeof setInterval> | null = null;

  // форма
  let kind = $state<'sl' | 'tp' | 'trail_tp' | 'on_fill'>('sl');
  let side = $state<'buy' | 'sell'>('sell');
  let qty = $state(1);
  let trigger = $state('');
  let trailOffset = $state('');
  let watchId = $state('');
  let ocoGroup = $state('');

  let activeOrders = $derived(ordersStore.all);
  let armedCount = $derived(orders.filter((o) => o.status === 'armed').length);
  let code = $derived((symbol || '').split('@')[0]);

  async function load() {
    try {
      const res = await fetchWithAuth('/api/v1/quik/smart-orders');
      if (res.ok) orders = (await res.json()).orders || [];
    } catch { /* следующий тик перезагрузит */ }
  }

  async function create() {
    msg = '';
    const body: any = {
      kind, code, side, qty,
      trigger_price: parseFloat(trigger) || 0,
      trail_offset: parseFloat(trailOffset) || 0,
      watch_client_id: watchId.trim(),
      oco_group: ocoGroup.trim(),
    };
    try {
      const res = await fetchWithAuth('/api/v1/quik/smart-orders', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) { msg = data?.detail || `HTTP ${res.status}`; return; }
      msg = 'взведена';
      trigger = ''; trailOffset = ''; watchId = '';
      await load();
    } catch (e: any) { msg = e?.message || 'ошибка'; }
  }

  async function cancel(soId: string) {
    try {
      const res = await fetchWithAuth(`/api/v1/quik/smart-orders/${soId}`, { method: 'DELETE' });
      if (!res.ok) msg = (await res.json().catch(() => ({})))?.detail || `HTTP ${res.status}`;
      await load();
    } catch { /* список обновится тиком */ }
  }

  function fmtTrigger(o: any): string {
    if (o.kind === 'trail_tp') return `${o.trigger_price} ±${o.trail_offset}`;
    if (o.kind === 'on_fill') return `после ${o.watch_client_id?.slice(0, 12) || '—'}`;
    return String(o.trigger_price);
  }
  function fmtTime(ms: number): string {
    return ms ? new Date(ms).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' }) : '—';
  }

  onMount(() => { load(); pollTimer = setInterval(load, 5000); });
  onDestroy(() => { if (pollTimer) clearInterval(pollTimer); });
</script>

<details class="sop" bind:open>
  <summary>
    Умные заявки{#if armedCount} <span class="cnt">({armedCount})</span>{/if}
    <button class="csv" onclick={(e) => { e.preventDefault(); downloadCSV(orders, 'smart_orders.csv'); }}
            title="выгрузить книгу умных заявок в CSV">CSV</button>
  </summary>

  <div class="form">
    <select bind:value={kind} title="тип умной заявки">
      {#each KINDS as [k, label]}<option value={k}>{label}</option>{/each}
    </select>
    <div class="row">
      <button class="sbtn buy" class:on={side === 'buy'} onclick={() => side = 'buy'}>Купить</button>
      <button class="sbtn sell" class:on={side === 'sell'} onclick={() => side = 'sell'}>Продать</button>
      <input class="in qty" type="number" min="1" step="1" bind:value={qty} title="кол-во контрактов" />
    </div>
    {#if kind !== 'on_fill'}
      <input class="in" type="number" step="any" bind:value={trigger}
             placeholder={kind === 'sl' ? 'стоп-цена' : kind === 'tp' ? 'цель' : 'цена активации'} />
    {/if}
    {#if kind === 'trail_tp'}
      <input class="in" type="number" step="any" bind:value={trailOffset} placeholder="отступ от пика (пункты)" />
    {/if}
    {#if kind === 'on_fill'}
      <input class="in" list="sop-watch" bind:value={watchId} placeholder="client_id наблюдаемой заявки" />
      <datalist id="sop-watch">
        {#each activeOrders as o}<option value={o.order_id}>{o.symbol} {o.side} {o.qty}@{o.price}</option>{/each}
      </datalist>
      <input class="in" type="number" step="any" bind:value={trigger} placeholder="цена дочерней (0 = по рынку)" />
    {/if}
    <input class="in" bind:value={ocoGroup} placeholder="OCO-группа (необязательно)"
           title="заявки одной группы отменяют друг друга при срабатывании любой" />
    <button class="go" onclick={create} disabled={!code || qty <= 0}>
      Взвести {KIND_RU[kind]} {code || '—'}
    </button>
    {#if msg}<div class="msg">{msg}</div>{/if}
  </div>

  {#if orders.length}
    <table>
      <thead><tr><th>Тип</th><th>Код</th><th>Опер</th><th>Кол</th><th>Триггер</th><th>Статус</th><th></th></tr></thead>
      <tbody>
        {#each [...orders].reverse() as o (o.so_id)}
          <tr class:dim={o.status !== 'armed'} title={o.note || ''}>
            <td>{KIND_RU[o.kind] || o.kind}</td>
            <td class="mono">{o.code}</td>
            <td class:buy={o.side === 'buy'} class:sell={o.side === 'sell'}>{o.side === 'buy' ? 'Б' : 'П'}</td>
            <td class="num">{o.qty}</td>
            <td class="num">{fmtTrigger(o)}</td>
            <td>{STATUS_RU[o.status] || o.status} {o.status === 'fired' ? fmtTime(o.fired_ms) : ''}</td>
            <td>{#if o.status === 'armed'}
              <button class="x" onclick={() => cancel(o.so_id)} title="отменить">✕</button>
            {/if}</td>
          </tr>
        {/each}
      </tbody>
    </table>
  {/if}
</details>

<style>
  .sop { border-top: 1px solid #2d2d4a; padding: 4px 10px 8px; font-size: 11px; color: #ccc;
    overflow: auto; }
  .sop summary { cursor: pointer; font-weight: 600; color: #ddd; font-size: 12px; padding: 4px 0;
    display: flex; align-items: center; gap: 8px; }
  .cnt { color: #e0a53c; }
  .csv { margin-left: auto; font-size: 10px; padding: 1px 6px; background: #1a1a2e; color: #9aa0b4;
    border: 1px solid #2d2d4a; border-radius: 3px; cursor: pointer; }
  .form { display: flex; flex-direction: column; gap: 5px; margin: 4px 0 8px; }
  .row { display: flex; gap: 4px; }
  select, .in { background: #0f0f1e; border: 1px solid #2d2d4a; color: #ccc; padding: 3px 6px;
    border-radius: 3px; font-size: 11px; width: 100%; box-sizing: border-box; }
  .in.qty { width: 64px; flex-shrink: 0; }
  .sbtn { flex: 1; padding: 3px 0; border: 1px solid #2d2d4a; background: #1a1a2e; color: #555;
    border-radius: 3px; cursor: pointer; font-size: 10px; }
  .sbtn.buy.on { background: #1b3a1b; color: #4caf50; border-color: #4caf50; }
  .sbtn.sell.on { background: #3a1b1b; color: #f44336; border-color: #f44336; }
  .go { padding: 5px 0; border: none; border-radius: 3px; cursor: pointer; font-size: 11px;
    font-weight: 600; background: #2d2d4a; color: #e8e8f0; }
  .go:disabled { opacity: 0.4; cursor: default; }
  .msg { font-size: 10px; color: #e0a53c; }
  table { width: 100%; border-collapse: collapse; font-size: 10px; }
  th { text-align: left; color: #555; font-weight: 400; padding: 2px 4px;
    border-bottom: 1px solid #2d2d4a; }
  td { padding: 2px 4px; border-bottom: 1px solid #1e1e3a; white-space: nowrap; }
  td.num { text-align: right; font-variant-numeric: tabular-nums; }
  td.mono { font-family: monospace; }
  td.buy { color: #4caf50; }
  td.sell { color: #f44336; }
  tr.dim { opacity: 0.5; }
  .x { background: none; border: none; color: #f44336; cursor: pointer; font-size: 11px; padding: 0 2px; }
</style>
