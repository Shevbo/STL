<!--
  Standalone strategy explanation page. Deep-link: /?strategy=<strategy_id>.
  Reuses the SAME beautiful help the param-run page shows (MustDescription +
  per-param ParamHelp with fvg/atr/ladder schematics), read-only, so the robot
  stand's «стратегия» link opens one canonical "how it works" page. Static
  context (atr/price=0): formulas + illustrations show; live points lines hide.
-->
<script lang="ts">
  import { onMount } from 'svelte';
  import { fetchWithAuth } from '$lib/fetch-auth';
  import { helpFor } from '$lib/strategy-help';
  import MustDescription from './MustDescription.svelte';
  import ParamHelp from './ParamHelp.svelte';

  let { strategyId }: { strategyId: string } = $props();
  let strat = $state<any>(null);
  let error = $state('');
  let loading = $state(true);
  const ctx = { atr: 0, price: 0 };  // static page: formula + schematic, no live numbers

  onMount(async () => {
    try {
      const res = await fetchWithAuth('/api/v1/strategies');
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      const list = Array.isArray(data) ? data : (data.strategies ?? data.items ?? []);
      strat = list.find((s: any) => s.id === strategyId || s.strategy_id === strategyId) ?? null;
      if (!strat) error = `Стратегия «${strategyId}» не найдена`;
    } catch (e) {
      error = `Не удалось загрузить стратегию: ${e}`;
    } finally {
      loading = false;
    }
  });
</script>

<div class="sp">
  <div class="sp-head">
    <h1>{strat?.name ?? strategyId}</h1>
    <div class="sp-sub">Как работает робот · стратегия <code>{strategyId}</code></div>
  </div>

  {#if loading}
    <div class="sp-msg">Загрузка…</div>
  {:else if error}
    <div class="sp-err">{error}</div>
  {:else if strat}
    <MustDescription {strategyId} />
    {#if strat.source_url || strat.source}
      <a class="sp-src" href={strat.source_url || strat.source} target="_blank" rel="noopener">источник стратегии ↗</a>
    {/if}
    <div class="sp-params-title">Параметры и механика</div>
    {#each (strat.params_schema ?? []) as p}
      {@const help = helpFor(strategyId, p.key)}
      <div class="sp-pf">
        <div class="sp-pf-key"><code>{p.key}</code>{#if help?.title} · {help.title}{:else if p.label} · {p.label}{/if}</div>
        {#if help}
          <ParamHelp {help} value={Number(p.default) || 0} {ctx} />
        {:else if p.desc || p.hint}
          <div class="sp-pf-desc">{p.desc || p.hint}</div>
        {/if}
      </div>
    {/each}
  {/if}
</div>

<style>
  .sp { max-width: 820px; margin: 0 auto; padding: 26px 20px 70px;
    font-family: system-ui, -apple-system, "Segoe UI", sans-serif; }
  .sp-head h1 { font-size: 24px; margin: 0 0 4px; color: #e6ecf5; font-weight: 700; }
  .sp-sub { font-size: 13px; color: #8b93a7; margin-bottom: 20px; }
  .sp-sub code { color: #7ab8ff; }
  .sp-src { display: inline-block; font-size: 12px; color: #7ab8ff; margin: 2px 0 4px;
    text-decoration: none; }
  .sp-src:hover { text-decoration: underline; }
  .sp-params-title { font-size: 15px; font-weight: 700; color: #9aa7bd;
    margin: 22px 0 12px; border-top: 1px solid #1d2740; padding-top: 16px; }
  .sp-pf { margin-bottom: 14px; }
  .sp-pf-key { font-size: 13px; font-weight: 700; color: #cdd4e0; margin-bottom: 4px; }
  .sp-pf-key code { color: #7ab8ff; }
  .sp-pf-desc { font-size: 13px; color: #b9c0d0; line-height: 1.55; }
  .sp-msg { color: #8b93a7; padding: 20px 0; }
  .sp-err { color: #ff8a80; padding: 20px 0; }
  :global(body:has(.sp)) { background: #070a12; }
  @media (prefers-color-scheme: light) {
    :global(body:has(.sp)) { background: #f4f6fb; }
    .sp-head h1 { color: #171b24; }
    .sp-params-title { color: #444d61; border-top-color: #d9e0ee; }
    .sp-pf-key { color: #2a3040; }
    .sp-pf-desc { color: #333a48; }
  }
</style>
