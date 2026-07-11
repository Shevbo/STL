<script lang="ts">
  import ParamSchematic from './ParamSchematic.svelte';
  import type { ParamHelp, LiveCtx } from '$lib/strategy-help';

  // value = current raw param value; ctx = live ATR/price for the points line.
  let { help, value, ctx }:
    { help: ParamHelp; value: number; ctx: LiveCtx } = $props();

  const liveLine = $derived(help.live ? help.live(Number(value), ctx) : null);
  // Schematic annotations derived from the raw value (×10 / ×10000 conventions).
  const tpMult = $derived(help.schematic === 'ladder' ? Number(value) / 10 : 0);
  const stepMult = $derived(help.schematic === 'ladder' ? Number(value) / 10 : 0);
  const fracPct = $derived(help.schematic === 'fvg' ? Number(value) / 100 : 0);
</script>

<div class="ph">
  <div class="ph-title">{help.title}</div>
  <div class="ph-what">{help.what}</div>
  {#if help.how}<div class="ph-how">{help.how}</div>{/if}
  {#if liveLine}<div class="ph-live">▸ {liveLine}</div>{/if}
  {#if help.schematic}
    <ParamSchematic kind={help.schematic} atr={ctx.atr} price={ctx.price}
                    {tpMult} {stepMult} {fracPct} />
  {/if}
  {#if help.note}<div class="ph-note">⚠ {help.note}</div>{/if}
</div>

<style>
  .ph { border-left: 3px solid #2d4a7a; background: #0c0f1a; border-radius: 0 6px 6px 0;
    padding: 10px 14px; margin: 4px 0 8px; }
  .ph-title { font-size: 13px; font-weight: 700; color: #dde; margin-bottom: 4px; }
  .ph-what { font-size: 13px; line-height: 1.55; color: #b9c0d0; }
  .ph-how { font-size: 12px; line-height: 1.5; color: #8b93a7; margin-top: 6px; }
  .ph-live { font-size: 13px; color: #7ab8ff; margin-top: 6px;
    font-family: Consolas, "SF Mono", monospace; }
  .ph-note { font-size: 12px; line-height: 1.5; color: #ffb86b; margin-top: 8px;
    background: #241a08; border-radius: 4px; padding: 6px 8px; }
  @media (prefers-color-scheme: light) {
    .ph { background: #eef1f7; border-left-color: #2563c4; }
    .ph-title { color: #1a1e28; } .ph-what { color: #333a48; }
    .ph-how { color: #5a6172; } .ph-note { background: #fff3e0; color: #8a5a00; }
  }
</style>
