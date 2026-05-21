<!-- frontend/src/components/InstrumentPanel.svelte -->
<script lang="ts">
  import { instrumentStore } from '$lib/stores/instrument.svelte';

  let { symbol = '' }: { symbol?: string } = $props();

  let params = $derived(instrumentStore.params);

  function flt(v: unknown): number {
    if (typeof v === 'object' && v !== null && 'value' in v) {
      return parseFloat(String((v as Record<string, unknown>).value) || '0') || 0;
    }
    return parseFloat(String(v || 0)) || 0;
  }

  function fmtNum(v: unknown): string {
    const n = flt(v);
    if (!n) return '—';
    return n.toLocaleString('ru-RU', { maximumFractionDigits: 2 });
  }

  function fmtDate(v: unknown): string {
    if (!v || typeof v !== 'string') return '—';
    try {
      const d = new Date(v);
      return d.toLocaleDateString('ru-RU');
    } catch {
      return String(v);
    }
  }

  // Try common field names from Finam API
  let minPrice = $derived(fmtNum(params?.min_price ?? params?.price_min ?? null));
  let maxPrice = $derived(fmtNum(params?.max_price ?? params?.price_max ?? null));
  let margin = $derived(fmtNum(params?.initial_margin ?? params?.margin ?? params?.go ?? null));
  let tickSize = $derived(fmtNum(params?.price_increment ?? params?.tick_size ?? params?.min_price_step ?? null));
  let tickCost = $derived(fmtNum(params?.price_step_cost ?? params?.tick_cost ?? null));
  let lotSize = $derived(fmtNum(params?.lot_size ?? params?.board_lot_size ?? null));
  let expiration = $derived(fmtDate(params?.expiration_date ?? params?.maturity_date ?? null));
</script>

<aside class="instrument-panel">
  {#if symbol}
    <div class="title">{symbol}</div>
    <div class="section">
      <div class="label">Коридор цен</div>
      <div class="value">{minPrice} – {maxPrice}</div>
    </div>
    <div class="section">
      <div class="label">ГО / маржа</div>
      <div class="value">{margin} ₽</div>
    </div>
    <div class="section">
      <div class="label">Шаг цены</div>
      <div class="value">{tickSize}</div>
    </div>
    <div class="section">
      <div class="label">Шаг / стоим.</div>
      <div class="value">{tickCost} ₽</div>
    </div>
    <div class="section">
      <div class="label">Лот</div>
      <div class="value">{lotSize}</div>
    </div>
    <div class="section">
      <div class="label">Экспирация</div>
      <div class="value">{expiration}</div>
    </div>
  {:else}
    <div class="empty">Выберите инструмент</div>
  {/if}
</aside>

<style>
  .instrument-panel {
    padding: 12px 10px; font-size: 12px;
  }
  .title { font-weight: 600; color: #ddd; margin-bottom: 12px; font-size: 13px; }
  .section { margin-bottom: 10px; }
  .label { color: #555; margin-bottom: 2px; font-size: 11px; text-transform: uppercase; letter-spacing: 0.04em; }
  .value { color: #ccc; }
  .empty { color: #555; padding-top: 8px; }
</style>
