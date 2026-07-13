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

<div class="sp-page">
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
</div>

<style>
  /* Committed light warm-paper theme (single look — no OS media query, no toggle, so the
     nested schematics can never desync). Warm cream + strong sans + a deep signal-blue and
     mono "instrument" motif — deliberately NOT the cream/serif/terracotta editorial cliché. */
  .sp-page {
    --bg: #f4f2ea; --paper: #fcfbf6; --tint: #eef3fb; --tint-line: #cfdcf1;
    --ink: #222630; --ink2: #545a6a; --muted: #96907f; --rule: #e4e0d4;
    --accent: #2b5fb0; --entry: #147c45; --amber: #a5661b; --amber-bg: #f7f0e2;
    --ghost: rgba(43,95,176,0.10);
    background: var(--bg); min-height: 100vh; color: var(--ink);
  }
  .sp {
    max-width: 880px; margin: 0 auto; padding: 52px 26px 96px;
    font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
    animation: rise .5s ease both;
  }
  :global(body:has(.sp-page)) { background: #f4f2ea; }

  /* ── ONE type scale — each size maps to ONE role, applied everywhere ──────────
     display 46 · title(H3) 17 · lead 16 · body 15 · meta 13 · eyebrow 12 · kicker 11 · mono 12.5 */
  .hero-eyebrow { font-size: 12px; letter-spacing: 2px; text-transform: uppercase; color: var(--muted); font-weight: 600; }
  .hero-name { margin: 14px 0 0; font-size: clamp(38px, 6.2vw, 56px); line-height: 1.0; font-weight: 800;
    letter-spacing: -0.025em; color: var(--ink); text-wrap: balance; }
  .hero-meta { display: flex; flex-wrap: wrap; align-items: center; gap: 10px 14px; margin-top: 16px; }
  .hero-chip { font-family: ui-monospace, "SF Mono", monospace; font-size: 12.5px; color: var(--accent);
    background: rgba(43,95,176,.08); border: 1px solid rgba(43,95,176,.24); border-radius: 6px; padding: 4px 9px; letter-spacing: .3px; }
  .hero-tf { font-size: 13px; color: var(--ink2); }
  .hero-src { font-size: 13px; color: var(--accent); text-decoration: none; }
  .hero-src:hover { text-decoration: underline; }
  .hero-thesis { margin: 24px 0 0; font-size: 18px; line-height: 1.6; color: var(--ink2); max-width: 62ch; font-weight: 400; }

  .rule { height: 1px; margin: 44px 0; background: linear-gradient(90deg, var(--accent), transparent 62%); opacity: .45; }
  .sec-title { font-size: 12px; letter-spacing: 1.8px; text-transform: uppercase; color: var(--muted); font-weight: 700; margin: 0 0 26px; }

  .behavior { background: var(--tint); border: 1px solid var(--tint-line); border-left: 3px solid var(--accent);
    border-radius: 4px 12px 12px 4px; padding: 24px 28px; margin-bottom: 56px; }
  .behavior-kicker { font-size: 11px; letter-spacing: 1px; text-transform: uppercase; color: var(--accent); font-weight: 700; margin-bottom: 11px; }
  .behavior-text { margin: 0; font-size: 17px; line-height: 1.72; color: var(--ink); max-width: 64ch; }
  .behavior-foot { margin-top: 14px; font-size: 13px; color: var(--muted); }

  .lifecycle { margin-bottom: 64px; }
  .stages { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; }
  .stage { position: relative; display: grid; grid-template-columns: 92px 1fr; gap: 6px 18px; align-items: start;
    padding: 30px 8px; border-top: 1px solid var(--rule); }
  .stage:first-child { border-top: none; padding-top: 8px; }
  .stage-num { font-family: ui-monospace, "SF Mono", monospace; font-size: 60px; font-weight: 800;
    line-height: .82; color: var(--ghost); letter-spacing: -.03em; -webkit-text-stroke: 1px rgba(43,95,176,.24); }
  .stage-kicker { font-size: 11px; letter-spacing: 1px; text-transform: uppercase; color: var(--accent); font-weight: 700; }
  .stage-label { margin: 5px 0 10px; font-size: 21px; font-weight: 700; color: var(--ink); letter-spacing: -.015em; }
  .stage-text { margin: 0; font-size: 16px; line-height: 1.7; color: var(--ink2); max-width: 62ch; }
  .stage.risk .stage-num { color: rgba(165,102,27,.12); -webkit-text-stroke-color: rgba(165,102,27,.34); }
  .stage.risk .stage-kicker { color: var(--amber); }

  .params { margin-top: 8px; }
  .param-list { display: flex; flex-direction: column; }
  .param { border-top: 1px solid var(--rule); padding: 30px 0; }
  .param:first-child { border-top: none; padding-top: 4px; }
  .param-head { display: flex; flex-wrap: wrap; align-items: baseline; gap: 9px 13px; margin-bottom: 14px; }
  .param-key { font-family: ui-monospace, "SF Mono", monospace; font-size: 12.5px; color: var(--accent);
    background: rgba(43,95,176,.08); border-radius: 5px; padding: 3px 8px; }
  .param-title { font-size: 21px; font-weight: 700; color: var(--ink); letter-spacing: -.015em; }
  .param-def { font-size: 12.5px; color: var(--muted); font-family: ui-monospace, monospace; margin-left: auto; }
  .param-desc { margin: 0; font-size: 16px; line-height: 1.7; color: var(--ink2); max-width: 62ch; }

  .sp-msg { color: var(--muted); padding: 40px 0; font-size: 15px; }
  .sp-err { color: #b5443a; padding: 40px 0; font-size: 15px; }

  @keyframes rise { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: none; } }
  @media (prefers-reduced-motion: reduce) { .sp { animation: none; } }

  /* Nested ParamHelp + schematic at the SAME body/meta sizes as the page (no jumps). The
     ph-title is hidden — the param head already shows the title (kills the duplicate heading). */
  .params :global(.ph) { background: var(--paper); border-left: 3px solid var(--accent);
    border-radius: 0 9px 9px 0; padding: 15px 18px; margin: 2px 0 6px; }
  .params :global(.ph-title) { display: none; }
  .params :global(.ph-what) { font-size: 16px; line-height: 1.7; color: var(--ink2); }
  .params :global(.ph-how) { font-size: 13px; line-height: 1.55; color: var(--muted); }
  .params :global(.ph-live) { font-size: 13px; color: var(--accent); }
  .params :global(.ph-note) { font-size: 13px; line-height: 1.55; color: var(--amber); background: var(--amber-bg); }
  .params :global(.schem) { --sch-bg: #fbfaf5; --sch-line: #e4e0d4; --sch-ink: #222630; --sch-cap: #545a6a;
    border-radius: 9px; padding: 14px 16px; margin-top: 8px; }
  .params :global(.schem svg) { max-width: 384px; }
  .params :global(.cap) { font-size: 13px; line-height: 1.56; }
</style>
