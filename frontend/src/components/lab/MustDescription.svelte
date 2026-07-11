<script lang="ts">
  import { overviewFor } from '$lib/strategy-help';
  let { strategyId }: { strategyId: string } = $props();
  const ov = $derived(overviewFor(strategyId));
</script>

{#if ov}
  <div class="must">
    <div class="must-title">MUST DESCRIPTION</div>
    <div class="must-row"><span class="must-k">1 · Период анализа</span><span class="must-v">{ov.timeframe}</span></div>
    <div class="must-row"><span class="must-k">2 · Сигнал открытия</span><span class="must-v">{ov.entry}</span></div>
    <div class="must-row"><span class="must-k">3 · Тейк-профит (TP)</span><span class="must-v">{ov.tp}</span></div>
    <div class="must-row sl"><span class="must-k">4 · Стоп-лосс (SL)</span><span class="must-v">{ov.sl}</span></div>
  </div>
{/if}

<style>
  .must { border: 1px solid #2d3a5a; border-radius: 8px; background: #0c1120;
    padding: 12px 14px; margin-bottom: 12px; }
  .must-title { font-size: 12px; font-weight: 700; letter-spacing: 1px; color: #7ab8ff;
    margin-bottom: 8px; }
  .must-row { display: grid; grid-template-columns: 150px 1fr; gap: 12px; padding: 5px 0;
    border-top: 1px solid #16203a; }
  .must-row:first-of-type { border-top: none; }
  .must-k { font-size: 12px; font-weight: 600; color: #9ab; }
  .must-v { font-size: 13px; line-height: 1.5; color: #c5ccdb; }
  .must-row.sl .must-k { color: #ff8a80; }
  .must-row.sl .must-v { color: #ffc9c4; }
  @media (max-width: 560px) { .must-row { grid-template-columns: 1fr; gap: 2px; } }
  @media (prefers-color-scheme: light) {
    .must { background: #eef2fa; border-color: #cdd6ea; }
    .must-title { color: #2563c4; }
    .must-k { color: #55607a; } .must-v { color: #2a3040; }
    .must-row.sl .must-k { color: #c7346a; } .must-row.sl .must-v { color: #7a2444; }
  }
</style>
