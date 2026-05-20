<!-- frontend/src/components/InstrumentPanel.svelte -->
<script lang="ts">
  interface InstrumentInfo {
    symbol: string;
    priceMin: number;
    priceMax: number;
    margin: number;
    expiration: string;
  }

  let { info = null }: { info?: InstrumentInfo | null } = $props();
</script>

<aside class="instrument-panel">
  {#if info}
    <div class="title">{info.symbol}</div>
    <div class="section">
      <div class="label">Коридор цен</div>
      <div class="value">{info.priceMin.toLocaleString('ru-RU')} – {info.priceMax.toLocaleString('ru-RU')}</div>
    </div>
    <div class="section">
      <div class="label">ГО / маржа</div>
      <div class="value">{info.margin.toLocaleString('ru-RU', { maximumFractionDigits: 0 })} ₽</div>
    </div>
    <div class="section">
      <div class="label">Экспирация</div>
      <div class="value">{info.expiration}</div>
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
