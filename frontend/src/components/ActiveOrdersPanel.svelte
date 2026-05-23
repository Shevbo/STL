<!-- frontend/src/components/ActiveOrdersPanel.svelte -->
<script lang="ts">
  import { ordersStore } from '$lib/stores/orders.svelte';

  let orders = $derived(ordersStore.all);

  function fmtTime(ts: number | undefined): string {
    if (!ts) return '—';
    const d = new Date(ts * 1000);
    return d.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  }

  function fmtPrice(p: number): string {
    return p.toLocaleString('ru-RU', { maximumFractionDigits: 1 });
  }
</script>

<div class="aop">
  <div class="aop-title">Активные заявки</div>
  {#if orders.length === 0}
    <div class="aop-empty">Нет заявок</div>
  {:else}
    <div class="aop-scroll">
      <table class="aop-table">
        <thead>
          <tr>
            <th>Код</th>
            <th>Цена</th>
            <th>Кол</th>
            <th>Опер</th>
            <th>Время</th>
            <th>Ком.</th>
          </tr>
        </thead>
        <tbody>
          {#each orders as o (o.order_id)}
            <tr class:buy={o.side === 'buy'} class:sell={o.side === 'sell'}>
              <td class="sym">{o.symbol.split('@')[0]}</td>
              <td class="num">{fmtPrice(o.price)}</td>
              <td class="num">{o.qty}</td>
              <td class="side-cell" class:buy={o.side === 'buy'} class:sell={o.side === 'sell'}>
                {o.side === 'buy' ? 'Б' : 'П'}
              </td>
              <td class="time">{fmtTime(o.created_at)}</td>
              <td class="cmt">{o.comment ?? ''}</td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  {/if}
</div>

<style>
  .aop {
    display: flex; flex-direction: column;
    border-top: 1px solid #2d2d4a;
    font-size: 10px; font-family: 'JetBrains Mono', 'Consolas', monospace;
    min-height: 80px; max-height: 200px;
  }
  .aop-title {
    padding: 3px 8px; font-size: 11px; color: #666;
    background: #1a1a2e; border-bottom: 1px solid #2d2d4a;
    flex-shrink: 0;
  }
  .aop-empty {
    padding: 10px 8px; color: #444; font-size: 10px; text-align: center;
  }
  .aop-scroll {
    flex: 1; overflow-y: auto;
  }
  .aop-table {
    width: 100%; border-collapse: collapse;
  }
  .aop-table thead tr {
    background: #111120;
    position: sticky; top: 0;
  }
  .aop-table th {
    padding: 2px 4px; text-align: right; color: #555;
    font-weight: 400; border-bottom: 1px solid #1e1e3a;
    white-space: nowrap;
  }
  .aop-table th:first-child, .aop-table td:first-child { text-align: left; }
  .aop-table td {
    padding: 2px 4px; text-align: right; color: #777;
    border-bottom: 1px solid #141424;
  }
  .aop-table td.sym { color: #aaa; font-size: 10px; }
  .aop-table td.num { color: #ccc; }
  .aop-table td.time { color: #555; }
  .aop-table td.cmt { color: #555; max-width: 50px; overflow: hidden; text-overflow: ellipsis; }
  .aop-table td.side-cell.buy { color: #4caf50; }
  .aop-table td.side-cell.sell { color: #f44336; }
</style>
