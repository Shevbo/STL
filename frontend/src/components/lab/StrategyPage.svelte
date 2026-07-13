<!--
  Strategy explainer — the canonical /?strategy=<id> page. Editorial longread: a hero with
  the strategy as an "instrument", the trade lifecycle as four numbered stages, a concrete
  behavioral example, and each parameter with its mechanics/schematic (ParamHelp).
-->
<script lang="ts">
  import { onMount } from 'svelte';
  import { fetchWithAuth } from '$lib/fetch-auth';
  import { helpFor, overviewFor, behaviorFor, nameFor } from '$lib/strategy-help';
  import ParamHelp from './ParamHelp.svelte';

  let { strategyId }: { strategyId: string } = $props();
  let strat = $state<any>(null);
  let error = $state('');
  let loading = $state(true);
  const ctx = { atr: 0, price: 0 };  // static page: formula + schematic, no live numbers

  const ov = $derived(overviewFor(strategyId));
  const name = $derived(nameFor(strategyId));
  // Representative behavior from the schema defaults (no live params on the doc page).
  const defaults = $derived.by(() => {
    const o: Record<string, any> = {};
    for (const p of (strat?.params_schema ?? [])) o[p.key] = p.default;
    return o;
  });
  const behavior = $derived(strat ? behaviorFor(strategyId, defaults, defaults.symbol) : null);
  const STAGES = [
    { key: 'timeframe', label: 'Период анализа', kicker: 'Когда смотрит' },
    { key: 'entry', label: 'Сигнал входа', kicker: 'Когда заходит' },
    { key: 'tp', label: 'Тейк-профит', kicker: 'Когда фиксирует' },
    { key: 'sl', label: 'Стоп-лосс', kicker: 'Когда режет риск' },
  ];

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
  {#if loading}
    <div class="sp-msg">Загрузка…</div>
  {:else if error}
    <div class="sp-err">{error}</div>
  {:else if strat}
    <header class="hero">
      <div class="hero-eyebrow">Стратегия робота · FORTS</div>
      <h1 class="hero-name">{name}</h1>
      <div class="hero-meta">
        <span class="hero-chip">{strategyId}</span>
        <span class="hero-tf">минутные бары · M1</span>
        {#if strat.source_url || strat.source}
          <a class="hero-src" href={strat.source_url || strat.source} target="_blank" rel="noopener">источник ↗</a>
        {/if}
      </div>
      {#if ov}<p class="hero-thesis">{ov.entry}</p>{/if}
    </header>

    <div class="rule"></div>

    {#if behavior}
      <section class="behavior">
        <div class="behavior-kicker">Как ведёт себя робот</div>
        <p class="behavior-text">{behavior}</p>
        <div class="behavior-foot">на параметрах по умолчанию — свои значения задаются при запуске</div>
      </section>
    {/if}

    {#if ov}
      <section class="lifecycle">
        <h2 class="sec-title">Жизненный цикл сделки</h2>
        <ol class="stages">
          {#each STAGES as s, i}
            <li class="stage" class:risk={s.key === 'sl'}>
              <div class="stage-num">{String(i + 1).padStart(2, '0')}</div>
              <div class="stage-body">
                <div class="stage-kicker">{s.kicker}</div>
                <h3 class="stage-label">{s.label}</h3>
                <p class="stage-text">{ov[s.key]}</p>
              </div>
            </li>
          {/each}
        </ol>
      </section>
    {/if}

    <section class="params">
      <h2 class="sec-title">Параметры и механика</h2>
      <div class="param-list">
        {#each (strat.params_schema ?? []) as p}
          {@const help = helpFor(strategyId, p.key)}
          <article class="param">
            <div class="param-head">
              <code class="param-key">{p.key}</code>
              <span class="param-title">{help?.title ?? p.label ?? p.key}</span>
              {#if p.default !== undefined}<span class="param-def">по умолч. {p.default}</span>{/if}
            </div>
            {#if help}
              <ParamHelp {help} value={Number(p.default) || 0} {ctx} />
            {:else if p.desc || p.hint}
              <p class="param-desc">{p.desc || p.hint}</p>
            {/if}
          </article>
        {/each}
      </div>
    </section>
  {/if}
</div>

<style>
  .sp {
    --bg: #0a0d15; --surface: #12151f; --surface2: #161b28;
    --ink: #eef2fa; --ink2: #aab4c8; --muted: #6b7688; --rule: #1e2536;
    --accent: #5aa0ff; --entry: #3ddc84; --amber: #f0a860; --amber-rail: #6e4a24;
    --ghost: rgba(90,160,255,0.07);
    max-width: 900px; margin: 0 auto; padding: 46px 24px 90px;
    font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
    color: var(--ink); animation: rise .5s ease both;
  }
  :global(body:has(.sp)) { background: #0a0d15; }

  .hero-eyebrow { font-size: 12px; letter-spacing: 2.2px; text-transform: uppercase; color: var(--muted); font-weight: 600; }
  .hero-name { margin: 10px 0 0; font-size: clamp(30px, 5.4vw, 48px); line-height: 1.03; font-weight: 800;
    letter-spacing: -0.022em; color: var(--ink); text-wrap: balance; }
  .hero-meta { display: flex; flex-wrap: wrap; align-items: center; gap: 10px 14px; margin-top: 16px; }
  .hero-chip { font-family: ui-monospace, "SF Mono", "Cascadia Code", monospace; font-size: 12.5px;
    color: var(--accent); background: rgba(90,160,255,.09); border: 1px solid rgba(90,160,255,.22);
    border-radius: 6px; padding: 4px 9px; letter-spacing: .3px; }
  .hero-tf { font-size: 12.5px; color: var(--ink2); }
  .hero-src { font-size: 12.5px; color: var(--accent); text-decoration: none; }
  .hero-src:hover { text-decoration: underline; }
  .hero-thesis { margin: 22px 0 0; font-size: clamp(16px, 2.2vw, 19px); line-height: 1.6; color: var(--ink2);
    max-width: 68ch; font-weight: 400; }

  .rule { height: 1px; margin: 34px 0; background: linear-gradient(90deg, var(--accent), transparent 62%); opacity: .5; }
  .sec-title { font-size: 13px; letter-spacing: 1.6px; text-transform: uppercase; color: var(--muted);
    font-weight: 700; margin: 0 0 20px; }

  .behavior { background: var(--surface); border: 1px solid var(--rule); border-left: 3px solid var(--accent);
    border-radius: 4px 12px 12px 4px; padding: 20px 24px; margin-bottom: 40px; }
  .behavior-kicker { font-size: 11px; letter-spacing: 1px; text-transform: uppercase; color: var(--accent); font-weight: 700; margin-bottom: 9px; }
  .behavior-text { margin: 0; font-size: 16px; line-height: 1.68; color: var(--ink); max-width: 68ch; }
  .behavior-foot { margin-top: 12px; font-size: 12px; color: var(--muted); }

  .lifecycle { margin-bottom: 48px; }
  .stages { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 4px; }
  .stage { position: relative; display: grid; grid-template-columns: 96px 1fr; gap: 8px 12px; align-items: start;
    padding: 22px 8px; border-top: 1px solid var(--rule); }
  .stage:first-child { border-top: none; }
  .stage-num { font-family: ui-monospace, "SF Mono", monospace; font-size: clamp(36px, 6vw, 58px); font-weight: 800;
    line-height: .9; color: var(--ghost); letter-spacing: -.03em; -webkit-text-stroke: 1px rgba(90,160,255,.22); }
  .stage-kicker { font-size: 11px; letter-spacing: 1px; text-transform: uppercase; color: var(--accent); font-weight: 700; }
  .stage-label { margin: 3px 0 8px; font-size: 20px; font-weight: 700; color: var(--ink); letter-spacing: -.01em; }
  .stage-text { margin: 0; font-size: 14.5px; line-height: 1.62; color: var(--ink2); max-width: 66ch; }
  .stage.risk .stage-num { color: rgba(240,168,96,.09); -webkit-text-stroke-color: rgba(240,168,96,.28); }
  .stage.risk .stage-kicker { color: var(--amber); }

  .params { margin-top: 8px; }
  .param-list { display: flex; flex-direction: column; gap: 22px; }
  .param { border-top: 1px solid var(--rule); padding-top: 18px; }
  .param:first-child { border-top: none; padding-top: 0; }
  .param-head { display: flex; flex-wrap: wrap; align-items: baseline; gap: 8px 12px; margin-bottom: 10px; }
  .param-key { font-family: ui-monospace, "SF Mono", monospace; font-size: 13px; color: var(--accent);
    background: rgba(90,160,255,.08); border-radius: 5px; padding: 2px 7px; }
  .param-title { font-size: 15.5px; font-weight: 700; color: var(--ink); }
  .param-def { font-size: 11.5px; color: var(--muted); font-family: ui-monospace, monospace; margin-left: auto; }
  .param-desc { margin: 0; font-size: 14px; line-height: 1.6; color: var(--ink2); max-width: 66ch; }

  .sp-msg { color: var(--muted); padding: 40px 0; }
  .sp-err { color: #ff8a80; padding: 40px 0; }

  @keyframes rise { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: none; } }
  @media (prefers-reduced-motion: reduce) { .sp { animation: none; } }

  /* Light theme — via the app toggle (data-theme) and the OS preference. */
  :root[data-theme='light'] .sp, .sp:where([data-theme='light'] *) {
    --bg: #f6f8fc; --surface: #ffffff; --surface2: #eef2fa;
    --ink: #131824; --ink2: #46516a; --muted: #8b95a9; --rule: #dde3ef;
    --accent: #2563c4; --entry: #17a05a; --amber: #b5711f; --ghost: rgba(37,99,196,.08);
  }
  :root[data-theme='light'] :global(body:has(.sp)) { background: #f6f8fc; }
  @media (prefers-color-scheme: light) {
    :root:not([data-theme='dark']) .sp {
      --bg: #f6f8fc; --surface: #ffffff; --surface2: #eef2fa;
      --ink: #131824; --ink2: #46516a; --muted: #8b95a9; --rule: #dde3ef;
      --accent: #2563c4; --entry: #17a05a; --amber: #b5711f; --ghost: rgba(37,99,196,.08);
    }
    :root:not([data-theme='dark']) :global(body:has(.sp)) { background: #f6f8fc; }
  }
</style>
