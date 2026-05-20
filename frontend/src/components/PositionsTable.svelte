<!-- frontend/src/components/PositionsTable.svelte -->
<script lang="ts">
  import type { Position } from '$lib/types';

  let { positions }: { positions: Position[] } = $props();

  const SIDE_COLOR: Record<Position['side'], string> = {
    long: '#4caf50',
    short: '#f44336',
    flat: '#888',
  };

  const SIDE_LABEL: Record<Position['side'], string> = {
    long: 'Long',
    short: 'Short',
    flat: 'Flat',
  };

  function fmt(n: number, decimals = 2): string {
    return n.toLocaleString('ru-RU', { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
  }
</script>

<section class="positions-panel">
  <div class="panel-title">Позиции</div>
  {#if positions.length === 0}
    <div class="empty">Нет открытых позиций</div>
  {:else}
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Инструмент</th>
            <th>Сторона</th>
            <th class="num">Кол-во</th>
            <th class="num">Средняя</th>
            <th class="num">Текущая</th>
            <th class="num">Вар. маржа</th>
          </tr>
        </thead>
        <tbody>
          {#each positions as pos (pos.symbol + pos.account_id)}
            <tr>
              <td class="symbol">{pos.symbol}</td>
              <td style="color: {SIDE_COLOR[pos.side]}">{SIDE_LABEL[pos.side]}</td>
              <td class="num">{pos.quantity}</td>
              <td class="num">{fmt(pos.avg_price)}</td>
              <td class="num">{fmt(pos.current_price)}</td>
              <td class="num" style="color: {pos.var_margin >= 0 ? '#4caf50' : '#f44336'}">
                {pos.var_margin >= 0 ? '+' : ''}{fmt(pos.var_margin)}
              </td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  {/if}
</section>

<style>
  .positions-panel {
    background: #14142a; border-top: 1px solid #2d2d4a;
    padding: 8px 12px; font-size: 12px; flex-shrink: 0;
  }
  .panel-title {
    font-size: 11px; font-weight: 600; color: #888;
    text-transform: uppercase; letter-spacing: 0.06em;
    margin-bottom: 6px;
  }
  .empty { color: #444; font-size: 11px; padding: 4px 0; }
  .table-wrap { overflow-x: auto; }
  table { width: 100%; border-collapse: collapse; }
  th {
    color: #555; font-size: 10px; text-transform: uppercase;
    letter-spacing: 0.04em; padding: 3px 8px 3px 0;
    text-align: left; font-weight: 500;
    border-bottom: 1px solid #2d2d4a;
  }
  td {
    color: #ccc; padding: 4px 8px 4px 0;
    border-bottom: 1px solid #1a1a2e;
  }
  .symbol { font-weight: 500; color: #ddd; }
  .num { text-align: right; font-variant-numeric: tabular-nums; }
  th.num { text-align: right; }
</style>
