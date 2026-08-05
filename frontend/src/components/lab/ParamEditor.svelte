<script lang="ts">
  // Schema-driven parameter editor: one row per param (label + value input +
  // «?» toggle that expands ParamHelp with copy, schematic, live points). Used
  // by the robot card; BacktestLab reuses ParamHelp inline in its range rows.
  // The parent owns Save/Cancel; this component renders fields + help + CSV.
  import ParamHelp from './ParamHelp.svelte';
  import ParamPanel from './ParamPanel.svelte';
  import MustDescription from './MustDescription.svelte';
  import { helpFor, type LiveCtx } from '$lib/strategy-help';
  import { downloadCSV } from '$lib/csv';

  let { strategyId, schema, values = $bindable({}), ctx, disabledKeys = [] }:
    { strategyId: string;
      schema: { key: string; label: string; type?: string; min?: number; max?: number }[];
      values: Record<string, any>;
      ctx: LiveCtx;
      disabledKeys?: string[] } = $props();

  let open = $state<Record<string, boolean>>({});
  const toggle = (k: string) => (open[k] = !open[k]);

  // Вид параметров: плотный СПИСОК (влезает целиком) или развёрнутая ПАНЕЛЬ
  // (крупные значения, группы по смыслу, переключатели, перевод в пункты).
  // Выбор запоминается: оператор работает в одном и том же и не переключает
  // его на каждом открытии стенда.
  const VIEW_KEY = 'pe_view';
  let view = $state<'list' | 'panel'>(
    (() => { try { return localStorage.getItem(VIEW_KEY) === 'panel' ? 'panel' : 'list'; }
             catch { return 'list'; } })());
  function setView(v: 'list' | 'panel') {
    view = v;
    try { localStorage.setItem(VIEW_KEY, v); } catch { /* приватный режим */ }
  }
</script>

<div class="pe2">
  <MustDescription {strategyId} />
  <div class="pe2-head">
    <div class="pe2-view" role="group" aria-label="Вид параметров">
      <button class:on={view === 'list'} onclick={() => setView('list')}
              title="плотный список: все поля видно сразу">Список</button>
      <button class:on={view === 'panel'} onclick={() => setView('panel')}
              title="крупно, по группам, с переводом значения в пункты">Панель</button>
    </div>
    <span class="pe2-hint">{view === 'panel'
      ? 'Наведи на название параметра — всплывёт пояснение. Стрелку можно держать.'
      : 'Нажми «?» у параметра — раскроется пояснение со схемой и примером в пунктах.'}</span>
    <button class="pe2-csv" onclick={() => downloadCSV([values], 'params-' + strategyId)}>Выгрузить в CSV</button>
  </div>

  {#if view === 'panel'}
    <ParamPanel {strategyId} {schema} bind:values {ctx} {disabledKeys} />
  {:else}
  {#each schema as f (f.key)}
    {@const help = helpFor(strategyId, f.key)}
    <div class="pe2-row">
      <button class="pe2-q" class:on={open[f.key]} disabled={!help}
              title={help ? 'пояснение' : 'пояснения нет'} onclick={() => toggle(f.key)}>?</button>
      <span class="pe2-label"><code class="pe2-key">{f.key}</code>{#if help?.title} · {help.title}{/if}</span>
      {#if f.type === 'text' || disabledKeys.includes(f.key)}
        <input class="pe2-in mono" value={values[f.key] ?? ''} disabled />
      {:else}
        <input class="pe2-in mono" type="number" min={f.min} max={f.max}
               bind:value={values[f.key]} />
      {/if}
      {#if f.min != null && f.max != null && f.type !== 'text'}
        <span class="pe2-range mono">{f.min}…{f.max}</span>
      {/if}
    </div>
    {#if open[f.key] && help}
      <ParamHelp {help} value={Number(values[f.key]) || 0} {ctx} />
    {/if}
  {/each}
  {/if}
</div>

<style>
  .pe2 { display: flex; flex-direction: column; gap: 2px; }
  .pe2-head { display: flex; align-items: center; gap: 10px; margin-bottom: 8px; flex-wrap: wrap; }
  /* Переключатель вида: две вкладки, активная подсвечена рамкой, а не заливкой —
     заливка спорила бы с зелёными кнопками действий над фреймом. */
  .pe2-view { display: inline-flex; flex: none; border: 1px solid #24406a; border-radius: 4px;
    overflow: hidden; }
  .pe2-view button { background: #0d1526; border: 0; color: #7c8cab; cursor: pointer;
    font: 600 11px/1 system-ui, sans-serif; padding: 6px 12px; }
  .pe2-view button + button { border-left: 1px solid #24406a; }
  .pe2-view button:hover { color: #cfe2ff; }
  .pe2-view button.on { background: #16243c; color: #dfe8f7; }

  .pe2-hint { font-size: 12px; color: #8b93a7; }
  .pe2-csv { margin-left: auto; background: #16162c; border: 1px solid #2d2d4a; color: #cde;
    padding: 4px 12px; border-radius: 4px; cursor: pointer; font-size: 12px; }
  .pe2-csv:hover { border-color: #4caf50; }
  .pe2-row { display: flex; align-items: center; gap: 10px; padding: 3px 0; }
  .pe2-q { width: 22px; height: 22px; flex-shrink: 0; border-radius: 50%;
    background: #16204a; border: 1px solid #2d4a7a; color: #7ab8ff; cursor: pointer;
    font-size: 13px; font-weight: 700; line-height: 1; padding: 0; }
  .pe2-q:hover:not(:disabled) { background: #1d2a5a; }
  .pe2-q.on { background: #2d4a7a; color: #fff; }
  .pe2-q:disabled { opacity: .3; cursor: default; }
  .pe2-label { flex: 1; font-size: 13px; color: #cdd; }
  .pe2-key { font-family: Consolas, "SF Mono", monospace; font-size: 12px; color: #ffca7a;
    background: #2a1e0a; border: 1px solid #b8860b55; border-radius: 3px; padding: 1px 5px; }
  .pe2-in { width: 96px; background: #0a0a12; border: 1px solid #2d2d4a; color: #eee;
    border-radius: 4px; padding: 5px 8px; font-size: 13px; text-align: right; }
  .pe2-in:disabled { opacity: .5; }
  .pe2-range { width: 70px; font-size: 11px; color: #667; text-align: left; }
  .mono { font-family: Consolas, "SF Mono", monospace; font-variant-numeric: tabular-nums; }
  @media (prefers-color-scheme: light) {
    .pe2-hint { color: #5a6172; } .pe2-label { color: #1a1e28; }
    .pe2-in { background: #fff; border-color: #d5d9e6; color: #1a1e28; }
    .pe2-range { color: #8b93a7; }
  }
</style>
