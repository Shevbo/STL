<!-- Optimizer.svelte — TSLab-style parameter sweep.
     For each numeric strategy param: from / to / step → expands to a value list.
     Runs the full grid (backend itertools.product), shows a sortable results
     table + a 2-param heatmap. Clicking a row opens that combo's chart.
-->
<script lang="ts">
  import { fetchWithAuth } from '../../lib/fetch-auth';

  let {
    robotId, strategy = null, baseParams = {}, dateFrom, dateTo, onSelectResult,
  }: {
    robotId: string;
    strategy?: any;
    baseParams?: Record<string, any>;
    dateFrom: string;
    dateTo: string;
    onSelectResult?: (r: any) => void;
  } = $props();

  // numeric params from the strategy schema
  let numericParams = $derived(
    (strategy?.params_schema ?? []).filter((p: any) => p.type === 'number')
  );

  // grid spec per param: { enabled, from, to, step }
  let grid = $state<Record<string, any>>({});
  let seededFor = '';   // strategy id the grid was seeded for

  // Seed grid when the strategy changes. Depend ONLY on strategy id (not on
  // `grid`) — reading grid here would create an update-depth-exceeded loop.
  $effect(() => {
    const sid = strategy?.id ?? '';
    const ps = (strategy?.params_schema ?? []).filter((p: any) => p.type === 'number');
    if (sid === seededFor) return;
    const g: Record<string, any> = {};
    for (const p of ps) {
      const def = Number(baseParams[p.key] ?? p.default ?? 0);
      g[p.key] = {
        enabled: true,
        from: p.min ?? def,
        to: p.max ?? def,
        step: Math.max(1, Math.round((((p.max ?? def) - (p.min ?? def)) / 4)) || 1),
      };
    }
    grid = g;
    seededFor = sid;
  });

  let running = $state(false);
  let status = $state('');
  let error = $state('');
  let results = $state<any[]>([]);
  let progress = $state('');
  let sortKey = $state('total_return');
  let sortDir = $state<-1 | 1>(-1);

  // which two enabled params drive the heatmap axes
  let sweptKeys = $derived(
    Object.entries(grid).filter(([, g]: any) => g.enabled && expand(g).length > 1).map(([k]) => k)
  );

  function expand(g: any): number[] {
    const from = Number(g.from), to = Number(g.to), step = Number(g.step);
    if (!step || step <= 0 || to < from) return [from];
    const out: number[] = [];
    for (let v = from; v <= to + 1e-9; v += step) out.push(Math.round(v * 1e6) / 1e6);
    return out;
  }

  let comboCount = $derived(
    Object.entries(grid)
      .filter(([, g]: any) => g.enabled)
      .reduce((acc, [, g]: any) => acc * Math.max(1, expand(g).length), 1)
  );

  function buildParamsGrid(): Record<string, number[]> {
    const out: Record<string, number[]> = {};
    for (const [k, g] of Object.entries(grid)) {
      if ((g as any).enabled) out[k] = expand(g);
    }
    return out;
  }

  async function runOptimization() {
    error = ''; results = []; running = true; progress = '';
    try {
      const pg = buildParamsGrid();
      const res = await fetchWithAuth('/api/v1/backtest/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          robotId,
          symbol: baseParams.symbol,
          dateFrom, dateTo,
          paramsGrid: pg,
        }),
      });
      if (!res.ok) throw new Error(await res.text());
      const { run_id } = await res.json();
      await poll(run_id);
    } catch (e) {
      error = String(e);
    }
    running = false;
  }

  async function poll(runId: string) {
    const total = comboCount;
    while (true) {
      await new Promise(r => setTimeout(r, 1500));
      const st = await (await fetchWithAuth(`/api/v1/backtest/${runId}/status`)).json();
      status = st.status;
      // partial results to show progress
      const rs = await (await fetchWithAuth(`/api/v1/backtest/${runId}/results`)).json();
      results = Array.isArray(rs) ? rs : [];
      progress = `${results.length} / ${total}`;
      if (st.status === 'done') { return; }
      if (st.status === 'failed') { error = st.error_msg || 'failed'; return; }
    }
  }

  function paramVal(r: any, key: string) {
    const p = typeof r.params === 'object' ? r.params : JSON.parse(r.params || '{}');
    return p[key];
  }

  let sorted = $derived(
    [...results].sort((a, b) => {
      const av = a[sortKey] ?? -Infinity, bv = b[sortKey] ?? -Infinity;
      return (av - bv) * sortDir;
    })
  );

  function setSort(k: string) {
    if (sortKey === k) sortDir = (sortDir === 1 ? -1 : 1);
    else { sortKey = k; sortDir = -1; }
  }

  // heatmap: 2 swept params → grid of cells colored by total_return
  let heatmap = $derived.by(() => {
    if (sweptKeys.length !== 2 || !results.length) return null;
    const [kx, ky] = sweptKeys;
    const xs = expand(grid[kx]);
    const ys = expand(grid[ky]);
    const cell = new Map<string, any>();
    for (const r of results) {
      cell.set(`${paramVal(r, kx)}|${paramVal(r, ky)}`, r);
    }
    const vals = results.map(r => r.total_return ?? 0);
    const lo = Math.min(...vals), hi = Math.max(...vals);
    return { kx, ky, xs, ys, cell, lo, hi };
  });

  function heatColor(v: number, lo: number, hi: number): string {
    if (v == null) return '#1a1a2e';
    if (v >= 0) {
      const t = hi > 0 ? v / hi : 0;
      return `rgba(76,175,80,${0.15 + 0.65 * t})`;
    } else {
      const t = lo < 0 ? v / lo : 0;
      return `rgba(244,67,54,${0.15 + 0.65 * t})`;
    }
  }

  const COLS = [
    { k: 'total_return', label: 'Доход%', fmt: (v: any) => v != null ? (v * 100).toFixed(2) + '%' : '—' },
    { k: 'sharpe',       label: 'Sharpe', fmt: (v: any) => v != null ? v.toFixed(2) : '—' },
    { k: 'max_drawdown', label: 'Просад%', fmt: (v: any) => v != null ? (v * 100).toFixed(1) + '%' : '—' },
    { k: 'win_rate',     label: 'Win%',   fmt: (v: any) => v != null ? (v * 100).toFixed(0) + '%' : '—' },
    { k: 'total_trades', label: 'Сделок', fmt: (v: any) => v ?? 0 },
  ];
</script>

<div class="opt-root">
  <!-- grid builder -->
  <div class="opt-builder">
    <div class="ob-title">Перебор параметров</div>
    {#if numericParams.length === 0}
      <div class="ob-empty">У стратегии нет числовых параметров</div>
    {:else}
      <table class="ob-grid">
        <thead>
          <tr><th>Параметр</th><th>Вкл</th><th>От</th><th>До</th><th>Шаг</th><th>Знач.</th></tr>
        </thead>
        <tbody>
          {#each numericParams as p}
            {#if grid[p.key]}
              <tr>
                <td class="ob-name" title={p.hint}>{p.label}</td>
                <td><input type="checkbox" bind:checked={grid[p.key].enabled} /></td>
                <td><input type="number" bind:value={grid[p.key].from} /></td>
                <td><input type="number" bind:value={grid[p.key].to} /></td>
                <td><input type="number" bind:value={grid[p.key].step} /></td>
                <td class="ob-count">{grid[p.key].enabled ? expand(grid[p.key]).length : 1}</td>
              </tr>
            {/if}
          {/each}
        </tbody>
      </table>
      <div class="ob-run-row">
        <button class="ob-run" onclick={runOptimization} disabled={running || comboCount < 1}>
          {running ? `Перебор… ${progress}` : `Запустить (${comboCount} комбинаций)`}
        </button>
      </div>
      {#if error}<div class="ob-error">{error}</div>{/if}
    {/if}
  </div>

  <!-- results -->
  {#if results.length}
    <div class="opt-results">
      <div class="or-head">
        <span class="or-title">Результаты ({results.length}{running ? ` / ${comboCount}` : ''})</span>
        <span class="or-hint">клик по строке — открыть график; клик по шапке — сортировка</span>
      </div>
      <div class="or-scroll">
        <table class="or-table">
          <thead>
            <tr>
              {#each sweptKeys as k}<th>{k}</th>{/each}
              {#each COLS as c}
                <th class="sortable" class:active={sortKey === c.k} onclick={() => setSort(c.k)}>
                  {c.label}{sortKey === c.k ? (sortDir === -1 ? ' ▼' : ' ▲') : ''}
                </th>
              {/each}
            </tr>
          </thead>
          <tbody>
            {#each sorted as r, i}
              <tr class:best={i === 0} onclick={() => onSelectResult?.(r)}
                  role="button" tabindex="0"
                  onkeydown={(e) => e.key === 'Enter' && onSelectResult?.(r)}>
                {#each sweptKeys as k}<td class="pv">{paramVal(r, k)}</td>{/each}
                {#each COLS as c}
                  <td class:pos={c.k !== 'max_drawdown' && (r[c.k] ?? 0) > 0}
                      class:neg={c.k !== 'max_drawdown' && (r[c.k] ?? 0) < 0}>
                    {c.fmt(r[c.k])}
                  </td>
                {/each}
              </tr>
            {/each}
          </tbody>
        </table>
      </div>

      <!-- heatmap for exactly 2 swept params -->
      {#if heatmap}
        <div class="heat-wrap">
          <div class="heat-title">Тепловая карта доходности: {heatmap.ky} (строки) × {heatmap.kx} (столбцы)</div>
          <table class="heat">
            <thead>
              <tr><th></th>{#each heatmap.xs as x}<th>{x}</th>{/each}</tr>
            </thead>
            <tbody>
              {#each heatmap.ys as y}
                <tr>
                  <th>{y}</th>
                  {#each heatmap.xs as x}
                    {@const r = heatmap.cell.get(`${x}|${y}`)}
                    <td
                      style="background:{r ? heatColor(r.total_return ?? 0, heatmap.lo, heatmap.hi) : '#0a0a15'}"
                      title={r ? `${heatmap.kx}=${x}, ${heatmap.ky}=${y}\nДоход ${((r.total_return ?? 0)*100).toFixed(2)}% Sharpe ${(r.sharpe ?? 0).toFixed(2)}` : ''}
                      onclick={() => r && onSelectResult?.(r)}
                      role="button" tabindex="0"
                      onkeydown={(e) => e.key === 'Enter' && r && onSelectResult?.(r)}
                    >{r ? ((r.total_return ?? 0) * 100).toFixed(1) : ''}</td>
                  {/each}
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
      {/if}
    </div>
  {/if}
</div>

<style>
  .opt-root { display: flex; flex-direction: column; height: 100%; overflow: auto; background: #0a0a15; padding: 12px; gap: 14px; }

  .opt-builder { background: #0f0f1e; border: 1px solid #2d2d4a; border-radius: 5px; padding: 10px; }
  .ob-title { font-size: 12px; color: #4caf50; font-weight: 600; margin-bottom: 8px; }
  .ob-empty { font-size: 11px; color: #666; }
  .ob-grid { width: 100%; border-collapse: collapse; font-size: 11px; }
  .ob-grid th { text-align: left; color: #666; padding: 3px 6px; font-weight: 400; }
  .ob-grid td { padding: 3px 6px; }
  .ob-name { color: #ccc; }
  .ob-count { color: #6aa8ff; text-align: center; }
  .ob-grid input[type=number] { width: 64px; background: #0a0a15; border: 1px solid #2d2d4a; color: #ccc; padding: 2px 4px; font-size: 11px; border-radius: 3px; }
  .ob-run-row { margin-top: 10px; }
  .ob-run { padding: 7px 16px; background: #4caf5020; border: 1px solid #4caf5066; color: #4caf50; cursor: pointer; border-radius: 4px; font-size: 12px; }
  .ob-run:disabled { opacity: 0.5; cursor: default; }
  .ob-error { color: #f44336; font-size: 11px; margin-top: 6px; }

  .opt-results { display: flex; flex-direction: column; gap: 10px; }
  .or-head { display: flex; align-items: baseline; gap: 10px; }
  .or-title { font-size: 12px; color: #4caf50; font-weight: 600; }
  .or-hint { font-size: 10px; color: #555; }
  .or-scroll { max-height: 340px; overflow: auto; border: 1px solid #1a1a2e; border-radius: 4px; }
  .or-table { width: 100%; border-collapse: collapse; font-size: 11px; }
  .or-table th { position: sticky; top: 0; background: #0f0f1e; color: #666; text-align: left; padding: 5px 8px; white-space: nowrap; border-bottom: 1px solid #2d2d4a; }
  .or-table th.sortable { cursor: pointer; user-select: none; }
  .or-table th.sortable:hover { color: #aaa; }
  .or-table th.active { color: #4caf50; }
  .or-table td { padding: 4px 8px; border-bottom: 1px solid #12121c; color: #ccc; cursor: pointer; white-space: nowrap; }
  .or-table tr:hover td { background: #1a1a2e; }
  .or-table tr.best td { background: #0d1f0d; }
  .or-table .pv { color: #6aa8ff; font-family: monospace; }
  .pos { color: #4caf50; } .neg { color: #f44336; }

  .heat-wrap { display: flex; flex-direction: column; gap: 6px; }
  .heat-title { font-size: 11px; color: #888; }
  .heat { border-collapse: collapse; font-size: 10px; }
  .heat th { color: #666; padding: 2px 6px; font-weight: 400; }
  .heat td { width: 48px; height: 30px; text-align: center; color: #fff; cursor: pointer; border: 1px solid #0a0a15; font-size: 10px; }
  .heat td:hover { outline: 1px solid #fff6; }
</style>
