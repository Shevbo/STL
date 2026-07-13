<script lang="ts">
  import { overviewFor, behaviorFor, nameFor, docBase } from '$lib/strategy-help';
  let { strategyId, params = null, symbol = '', compact = false, showName = true }:
    { strategyId: string; params?: Record<string, any> | null; symbol?: string; compact?: boolean; showName?: boolean } = $props();
  const ov = $derived(overviewFor(strategyId));
  const behavior = $derived(behaviorFor(strategyId, params, symbol));
  const name = $derived(nameFor(strategyId));
</script>

{#if ov}
  <article class="sd" class:compact>
    {#if showName}
      <header class="sd-head">
        <div class="sd-eyebrow">Робот-стратегия</div>
        <h2 class="sd-name">{name}</h2>
        <a class="sd-doc" href={'/?strategy=' + encodeURIComponent(docBase(strategyId))} target="_blank" rel="noopener">Полное описание ↗</a>
      </header>
    {/if}

    {#if behavior}
      <div class="sd-behavior">
        <div class="sd-b-label">Как ведёт себя робот с текущими параметрами</div>
        <p class="sd-b-text">{behavior}</p>
      </div>
    {/if}

    <div class="sd-pillars">
      <section class="sd-pillar">
        <div class="sd-p-eyebrow">Период анализа</div>
        <p class="sd-p-text">{ov.timeframe}</p>
      </section>
      <section class="sd-pillar">
        <div class="sd-p-eyebrow">Сигнал входа</div>
        <p class="sd-p-text">{ov.entry}</p>
      </section>
      <section class="sd-pillar">
        <div class="sd-p-eyebrow">Тейк-профит</div>
        <p class="sd-p-text">{ov.tp}</p>
      </section>
      <section class="sd-pillar sd-sl">
        <div class="sd-p-eyebrow">Стоп-лосс</div>
        <p class="sd-p-text">{ov.sl}</p>
      </section>
    </div>
  </article>
{/if}

<style>
  /* Self-contained reading card — its own considered palette so it reads well on any app
     surface. Theme-aware via BOTH the OS media query and the app's data-theme toggle. */
  .sd {
    --sd-bg: #141824; --sd-edge: #263150; --sd-ink: #e9edf6; --sd-ink2: #aeb8cc;
    --sd-eye: #6f7c99; --sd-accent: #6aa8ff; --sd-rail: #2f5c9e; --sd-callout: #172136;
    --sd-red: #ff8f85; --sd-red-rail: #7a3b3b;
    background: var(--sd-bg); border: 1px solid var(--sd-edge); border-radius: 12px;
    padding: 20px 22px; margin-bottom: 14px; max-width: 760px;
    font-family: -apple-system, "Segoe UI", Roboto, sans-serif;
  }
  .sd.compact { padding: 14px 16px; }
  .sd-head { display: flex; flex-wrap: wrap; align-items: baseline; gap: 6px 12px; margin-bottom: 14px; }
  .sd-eyebrow { flex-basis: 100%; font-size: 11px; letter-spacing: 1.4px; text-transform: uppercase;
    color: var(--sd-eye); font-weight: 600; }
  .sd-name { margin: 0; font-size: 22px; line-height: 1.15; font-weight: 700; color: var(--sd-ink);
    text-wrap: balance; }
  .sd.compact .sd-name { font-size: 18px; }
  .sd-doc { margin-left: auto; font-size: 12px; color: var(--sd-accent); text-decoration: none;
    white-space: nowrap; align-self: center; }
  .sd-doc:hover { text-decoration: underline; }

  .sd-behavior { background: var(--sd-callout); border-left: 3px solid var(--sd-rail);
    border-radius: 0 8px 8px 0; padding: 12px 16px; margin-bottom: 16px; }
  .sd-b-label { font-size: 11px; letter-spacing: .6px; text-transform: uppercase; color: var(--sd-accent);
    font-weight: 600; margin-bottom: 6px; }
  .sd-b-text { margin: 0; font-size: 14.5px; line-height: 1.62; color: var(--sd-ink); }

  .sd-pillars { display: flex; flex-direction: column; gap: 14px; }
  .sd-pillar { padding-left: 14px; border-left: 2px solid var(--sd-rail); }
  .sd-p-eyebrow { font-size: 11px; letter-spacing: 1px; text-transform: uppercase; color: var(--sd-eye);
    font-weight: 700; margin-bottom: 4px; }
  .sd-p-text { margin: 0; font-size: 13.5px; line-height: 1.6; color: var(--sd-ink2); max-width: 64ch; }
  .sd-sl { border-left-color: var(--sd-red-rail); }
  .sd-sl .sd-p-eyebrow { color: var(--sd-red); }

  @media (prefers-color-scheme: light) {
    .sd { --sd-bg: #ffffff; --sd-edge: #dbe1ee; --sd-ink: #1a2233; --sd-ink2: #465066;
      --sd-eye: #8592ab; --sd-accent: #2563c4; --sd-rail: #9db8e6; --sd-callout: #f1f5fd;
      --sd-red: #c23a54; --sd-red-rail: #e4a9b4; box-shadow: 0 1px 3px rgba(20,40,80,.06); }
  }
  :root[data-theme='dark'] .sd {
    --sd-bg: #141824; --sd-edge: #263150; --sd-ink: #e9edf6; --sd-ink2: #aeb8cc;
    --sd-eye: #6f7c99; --sd-accent: #6aa8ff; --sd-rail: #2f5c9e; --sd-callout: #172136;
    --sd-red: #ff8f85; --sd-red-rail: #7a3b3b; box-shadow: none;
  }
  :root[data-theme='light'] .sd {
    --sd-bg: #ffffff; --sd-edge: #dbe1ee; --sd-ink: #1a2233; --sd-ink2: #465066;
    --sd-eye: #8592ab; --sd-accent: #2563c4; --sd-rail: #9db8e6; --sd-callout: #f1f5fd;
    --sd-red: #c23a54; --sd-red-rail: #e4a9b4; box-shadow: 0 1px 3px rgba(20,40,80,.06);
  }
</style>
