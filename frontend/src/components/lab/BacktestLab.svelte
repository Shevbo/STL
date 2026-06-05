<script lang="ts">
  import { untrack } from 'svelte';
  import { fetchWithAuth } from '../../lib/fetch-auth';
  import BacktestChart from './BacktestChart.svelte';
  import Optimizer from './Optimizer.svelte';
  import { toFills, replay, commissionBreakdown } from '../../lib/lab-analytics';

  let { preset = null }: { preset?: any } = $props();

  let centerMode = $state<'chart' | 'optimize'>('chart');

  const TYPE_LABEL: Record<string, string> = {
    open: 'Открытие', average: 'Усреднение', close: 'Закрытие', reverse: 'Закр+Реверс',
  };
  const fmtT = (ts: number) => new Date(ts * 1000).toLocaleString('ru-RU', {
    timeZone: 'Europe/Moscow', day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit',
  });
  const fmtP = (v: number) => (v >= 0 ? '+' : '') + Math.round(v);

  // Commission (broker+exchange, taker — manual backtests model taker) for a result
  // row, so the user can verify the fee is computed from the real broker/exchange
  // tariff. Uses the same shared model as the chart and the backend.
  function commOf(r: any) {
    const sym = (typeof r.params === 'object' ? r.params?.symbol : null) || paramValues.symbol || selectedSymbol || '';
    return commissionBreakdown(toFills(r.trades), pointValue, sym, true);
  }

  // trades ledger for the selected result (newest first)
  let ledger = $derived(
    selectedResult ? replay(toFills(selectedResult.trades)).ledger.slice().reverse() : []
  );

  let robots = $state<any[]>([]);
  let selectedRobotId = $state('');
  let dateFrom = $state('2026-03-02');
  let dateTo = $state('2026-05-24');
  let paramValues = $state<Record<string, any>>({});  // structured param form values
  let openInfo = $state<string | null>(null);
  let hoverInfo = $state<string | null>(null);
  let engine = $state<'local' | 'remote'>('local');  // VDS vs powerful host agent
  let runId = $state('');
  let status = $state('');
  let results = $state<any[]>([]);
  let running = $state(false);
  let error = $state('');
  let loadingData = $state(false);
  let dataStatus = $state('');
  let coverage = $state<any[]>([]);
  let selectedResult = $state<any | null>(null);

  let strategies = $state<any[]>([]);
  let instruments = $state<any[]>([]);   // FORTS top by turnover (dropdown)

  let selectedSymbol = $derived(
    paramValues.symbol
      ?? (() => {
        const robot = robots.find(r => r.id === selectedRobotId);
        const pj = robot?.params_json;
        return (typeof pj === 'object' ? pj?.symbol : null) ?? 'RIM6';
      })()
  );

  // Ruble economics for the chart (so backtest stats + TP/SL read in rubles, like
  // the robot window). Fetched per symbol from the cached instrument meta.
  let pointValue = $state(1);
  $effect(() => {
    const sym = selectedSymbol;
    pointValue = 1;
    fetchWithAuth(`/api/v1/instruments/${encodeURIComponent(sym)}/meta`)
      .then(r => r.ok ? r.json() : null)
      .then(m => { if (m?.point_value) pointValue = m.point_value; })
      .catch(() => { /* keep 1 */ });
  });

  // Match the strategy template behind the selected robot (by script_code containing its id)
  let selectedStrategy = $derived(
    (() => {
      const robot = robots.find(r => r.id === selectedRobotId);
      const code = robot?.script_code ?? '';
      return strategies.find(s => code.includes(s.id)) ?? null;
    })()
  );

  async function loadRobots() {
    const res = await fetchWithAuth('/api/v1/robots');
    robots = res.ok ? await res.json() : [];
    if (robots.length && !selectedRobotId) selectedRobotId = robots[0].id;
  }

  async function loadStrategies() {
    const res = await fetchWithAuth('/api/v1/strategies');
    strategies = res.ok ? await res.json() : [];
  }

  async function loadInstruments() {
    try {
      const res = await fetchWithAuth('/api/v1/forts-instruments');
      instruments = res.ok ? await res.json() : [];
    } catch { instruments = []; }
  }

  async function runBacktest() {
    error = '';
    running = true;
    results = [];
    try {
      // Structured form → single-value param grid (each param fixed to its value).
      const grid: Record<string, any> = {};
      for (const [k, v] of Object.entries(paramValues)) grid[k] = v;
      const symbol = paramValues.symbol || selectedSymbol || '';
      const res = await fetchWithAuth('/api/v1/backtest/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          robotId: selectedRobotId,
          symbol,
          dateFrom: new Date(dateFrom).toISOString(),
          dateTo: new Date(dateTo).toISOString(),
          paramsGrid: grid,
          engine,
        }),
      });
      if (!res.ok) throw new Error(await res.text());
      const data = await res.json();
      runId = data.run_id;
      await pollStatus();
    } catch (e) {
      error = String(e);
      running = false;
    }
  }

  // Seed the structured param form from the selected strategy defaults + robot
  // params. Runs when robot/strategy changes (but not while a preset is applying).
  // Plain vars (not $state) — they are control flags, not reactive UI, and writing
  // them inside an effect must not retrigger it.
  let presetApplied = false;
  function seedParams() {
    const robot = robots.find(r => r.id === selectedRobotId);
    const schema = selectedStrategy?.params_schema ?? [];
    const base: Record<string, any> = {};
    for (const p of schema) base[p.key] = p.default;
    const rp = (typeof robot?.params_json === 'object' ? robot.params_json : {}) ?? {};
    paramValues = { ...base, ...rp };
  }

  // Apply a Botstore preset: pick the matching robot, fill params + period.
  function applyPreset(p: any) {
    if (!p || !robots.length) return;
    const robot = robots.find(r => (r.script_code ?? '').includes(p.strategyId))
      ?? robots.find(r => r.id === selectedRobotId);
    if (robot) selectedRobotId = robot.id;
    paramValues = { ...(p.params ?? {}) };
    if (p.symbol) paramValues.symbol = p.symbol;
    if (p.dateFrom) dateFrom = p.dateFrom.slice(0, 10);
    if (p.dateTo) dateTo = p.dateTo.slice(0, 10);
    presetApplied = true;
  }

  async function pollStatus() {
    while (true) {
      await new Promise(r => setTimeout(r, 2000));
      const res = await fetchWithAuth(`/api/v1/backtest/${runId}/status`);
      const data = await res.json();
      status = data.status;
      if (data.status === 'done') {
        await loadResults();
        running = false;
        return;
      } else if (data.status === 'failed') {
        error = data.error_msg || 'Backtest failed';
        running = false;
        return;
      }
    }
  }

  async function loadResults() {
    const res = await fetchWithAuth(`/api/v1/backtest/${runId}/results`);
    results = res.ok ? await res.json() : [];
    // Auto-select the top result so the chart appears immediately (single-param
    // run → one row; grid → best by return, already sorted server-side).
    selectedResult = results.length ? results[0] : null;
    if (selectedResult) centerMode = 'chart';
  }

  async function deployResult(params: any) {
    await fetchWithAuth(`/api/v1/robots/${selectedRobotId}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ paramsJson: params }),
    });
    await fetchWithAuth(`/api/v1/robots/${selectedRobotId}/deploy`, { method: 'POST' });
  }

  async function loadData() {
    const robot = robots.find(r => r.id === selectedRobotId);
    const symbol = robot?.params_json?.symbol || '';
    if (!symbol) { error = 'Robot has no symbol in params_json'; return; }
    loadingData = true;
    dataStatus = 'Requesting ISS download…';
    error = '';
    try {
      const res = await fetchWithAuth('/api/v1/market/update', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          symbols: [symbol],
          dateFrom: new Date(dateFrom).toISOString(),
          dateTo: new Date(dateTo).toISOString(),
        }),
      });
      if (!res.ok) throw new Error(await res.text());
      dataStatus = `Download started for ${symbol}. Refresh coverage in ~30s.`;
      setTimeout(loadCoverage, 5000);
    } catch (e) {
      error = String(e);
    }
    loadingData = false;
  }

  async function loadCoverage() {
    const res = await fetchWithAuth('/api/v1/market/coverage');
    coverage = res.ok ? await res.json() : [];
  }

  $effect(() => { loadRobots(); loadCoverage(); loadStrategies(); loadInstruments(); });

  // Apply an incoming Botstore preset once robots are loaded. Writes happen inside
  // untrack so mutating paramValues/selectedRobotId doesn't retrigger this effect.
  let presetDone = false;
  $effect(() => {
    void preset; void robots.length; void strategies.length;
    if (preset && robots.length && strategies.length && !presetDone) {
      presetDone = true;
      untrack(() => applyPreset(preset));
    }
  });

  // Seed the structured form when the robot/strategy changes (unless a preset set it).
  let lastSeededRobot = '';
  $effect(() => {
    const sid = selectedRobotId;
    const strat = selectedStrategy;
    if (!sid || !strat) return;
    untrack(() => {
      if (presetApplied) { presetApplied = false; lastSeededRobot = sid; return; }
      if (sid !== lastSeededRobot) { seedParams(); lastSeededRobot = sid; }
    });
  });
</script>

<div class="backtest-lab">

  <!-- ── Left: controls ─────────────────────────────────────────── -->
  <div class="controls">
    <h3>Backtest Lab</h3>
    <label>
      Робот
      <select bind:value={selectedRobotId}>
        {#each robots as r}<option value={r.id}>{r.name}</option>{/each}
      </select>
    </label>

    <!-- Strategy "about" + author link -->
    {#if selectedStrategy}
      <div class="about-box">
        <div class="about-name">{selectedStrategy.name}</div>
        {#if selectedStrategy.description}<div class="about-desc">{selectedStrategy.description}</div>{/if}
        {#if selectedStrategy.source}
          <a class="about-link" href={selectedStrategy.source} target="_blank" rel="noopener">Подробное описание робота ↗</a>
        {/if}
      </div>
    {/if}

    <!-- Structured parameters: instrument dropdown + each strategy param with (i) -->
    {#if selectedStrategy}
      <div class="param-form">
        <div class="section-title">Параметры</div>
        {#each (selectedStrategy.params_schema ?? []) as p}
          {@const info = p.desc || p.hint}
          {@const isSymbol = p.key === 'symbol'}
          <div class="pf-row">
            <span class="pf-label">
              {p.label}
              {#if info}
                <span class="pf-i" role="button" tabindex="0" aria-label="Описание"
                  onclick={() => openInfo = openInfo === p.key ? null : p.key}
                  onkeydown={(e) => (e.key === 'Enter' || e.key === ' ') && (openInfo = openInfo === p.key ? null : p.key)}
                  onmouseenter={() => hoverInfo = p.key} onmouseleave={() => hoverInfo = null}
                >ⓘ</span>
              {/if}
              {#if info && (openInfo === p.key || hoverInfo === p.key)}
                <div class="pf-popover">
                  <div class="pp-title">{p.label}</div>
                  <div class="pp-body">{p.desc || p.hint}</div>
                  {#if p.type === 'number' && (p.min != null || p.max != null)}
                    <div class="pp-range">Диапазон: {p.min ?? '—'} … {p.max ?? '—'} · по умолч. {p.default}</div>
                  {/if}
                </div>
              {/if}
            </span>
            {#if isSymbol}
              <select bind:value={paramValues[p.key]}>
                <!-- keep the current value even if not in the top-turnover list -->
                {#if paramValues[p.key] && !instruments.some(i => i.symbol === paramValues[p.key])}
                  <option value={paramValues[p.key]}>{paramValues[p.key]}</option>
                {/if}
                {#each instruments as inst}
                  <option value={inst.symbol}>{inst.symbol} — {inst.name}</option>
                {/each}
              </select>
            {:else if p.type === 'number'}
              <input type="number" min={p.min} max={p.max} bind:value={paramValues[p.key]} placeholder={String(p.default)} />
            {:else}
              <input type="text" bind:value={paramValues[p.key]} placeholder={String(p.default)} />
            {/if}
          </div>
        {/each}
      </div>
    {/if}

    <label>Период бэктеста</label>
    <div class="period-row">
      <input type="date" bind:value={dateFrom} />
      <span class="period-dash">—</span>
      <input type="date" bind:value={dateTo} />
    </div>

    <!-- Market data -->
    <div class="data-section">
      <div class="section-title">Market Data (ISS MOEX)</div>
      {#if coverage.length}
        <div class="coverage">
          {#each coverage as c}
            <span class="cov-item">{c.symbol}: {c.min_date} — {c.max_date} ({c.cnt ?? c.count} bars)</span>
          {/each}
        </div>
      {:else}
        <div class="no-data">No cached data yet</div>
      {/if}
      <button class="load-btn" onclick={loadData} disabled={loadingData}>
        {loadingData ? 'Loading…' : 'Load from ISS'}
      </button>
      {#if dataStatus}<div class="data-status">{dataStatus}</div>{/if}
    </div>

    <div class="divider"></div>

    <label>Движок расчёта</label>
    <div class="engine-row">
      <button class="eng-btn" class:active={engine === 'local'} onclick={() => engine = 'local'}>VDS (сервер)</button>
      <button class="eng-btn" class:active={engine === 'remote'} onclick={() => engine = 'remote'}>Мощный хост</button>
    </div>
    <div class="hint">«Мощный хост» — расчёт на внешнем агенте (i9/128ГБ), не грузит торговый сервер. Требует запущенного агента.</div>

    <button onclick={runBacktest} disabled={running}>
      {running ? `Running… (${status})` : 'Run Backtest'}
    </button>
    {#if error}<div class="error">{error}</div>{/if}

    <!-- Results table -->
    {#if results.length}
      <div class="results-section">
        <div class="section-title">
          Results ({results.length}) — click to view chart
        </div>
        <div class="disclaimer">Доходность рассчитана от первоначальных инвестиций 100 000 ₽, в рублях (реальная стоимость пункта и ГО с MOEX ISS). При усреднении ГО растёт пропорционально. ⚠ Результаты могут отличаться от live (без модели проскальзывания).</div>
        <table>
          <thead>
            <tr>
              <th>Params</th><th>Return</th><th>Комиссия ₽</th><th>Sharpe</th><th>MaxDD</th><th>Win%</th><th>N</th><th></th>
            </tr>
          </thead>
          <tbody>
            {#each results as r}
              {@const isSelected = selectedResult === r}
              <tr
                class:selected={isSelected}
                onclick={() => selectedResult = r}
                role="button"
                tabindex="0"
                onkeydown={(e) => e.key === 'Enter' && (selectedResult = r)}
              >
                <td class="params-cell">
                  {#each Object.entries(typeof r.params === 'object' ? r.params : {}) as [k,v]}
                    {#if k !== 'symbol'}<span class="param-tag">{k}={v}</span>{/if}
                  {/each}
                </td>
                <td class:pos={r.total_return > 0} class:neg={r.total_return < 0}>
                  {r.total_return != null ? (r.total_return * 100).toFixed(2) + '%' : '—'}
                </td>
                {@const cb = commOf(r)}
                <td class="comm-cell neg" title="брокер {Math.round(cb.broker)} + биржа {Math.round(cb.exchange)} ₽ ({cb.fills} филлов, {(cb.rate*100).toFixed(4)}% от номинала)">
                  −{Math.round(cb.total).toLocaleString('ru-RU')}
                </td>
                <td class:pos={(r.sharpe ?? 0) > 0} class:neg={(r.sharpe ?? 0) < 0}>
                  {r.sharpe?.toFixed(2) ?? '—'}
                </td>
                <td>{r.max_drawdown != null ? (r.max_drawdown * 100).toFixed(1) + '%' : '—'}</td>
                <td>{r.win_rate != null ? (r.win_rate * 100).toFixed(0) + '%' : '—'}</td>
                <td>{r.total_trades ?? 0}</td>
                <td>
                  <button class="deploy-btn" onclick={(e) => { e.stopPropagation(); deployResult(r.params); }}>
                    ▶
                  </button>
                </td>
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
    {/if}

    <!-- Virtual trades ledger (bottom of left panel) -->
    {#if ledger.length}
      <div class="trades-section">
        <div class="section-title">Виртуальные сделки ({ledger.length})</div>
        <div class="trades-scroll">
          <table class="trades-table">
            <thead>
              <tr><th>Время</th><th>Напр.</th><th>Кол</th><th>Цена</th><th>Тип</th><th>Рез.</th></tr>
            </thead>
            <tbody>
              {#each ledger as t}
                <tr>
                  <td>{fmtT(t.time)}</td>
                  <td class:buy={t.side === 'buy'} class:sell={t.side === 'sell'}>{t.side === 'buy' ? 'Купить' : 'Продать'}</td>
                  <td>{t.qty}</td>
                  <td>{Math.round(t.price)}</td>
                  <td class="ttype">{TYPE_LABEL[t.type] ?? t.type}</td>
                  <td class:pos={t.pnl > 0} class:neg={t.pnl < 0}>{t.pnl != null ? fmtP(t.pnl) : ''}</td>
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
      </div>
    {/if}
  </div>

  <!-- ── Center: chart / optimizer ──────────────────────────────── -->
  <div class="chart-area">
    <div class="center-tabs">
      <button class:active={centerMode === 'chart'} onclick={() => centerMode = 'chart'}>График</button>
      <button class:active={centerMode === 'optimize'} onclick={() => centerMode = 'optimize'}>Перебор параметров</button>
    </div>

    <div class="center-body">
      {#if centerMode === 'optimize'}
        <Optimizer
          robotId={selectedRobotId}
          strategy={selectedStrategy}
          baseParams={paramValues}
          instruments={instruments}
          dateFrom={new Date(dateFrom).toISOString()}
          dateTo={new Date(dateTo).toISOString()}
          onSelectResult={(r) => { selectedResult = r; centerMode = 'chart'; }}
        />
      {:else if selectedResult}
        <BacktestChart
          result={selectedResult}
          symbol={selectedSymbol}
          strategy={selectedStrategy}
          dateFrom={new Date(dateFrom).toISOString()}
          dateTo={new Date(dateTo).toISOString()}
          pointValue={pointValue}
        />
      {:else}
        <div class="chart-placeholder">
          <div class="ph-icon">📈</div>
          <div class="ph-text">Запустите бэктест и выберите строку, либо откройте «Перебор параметров»</div>
        </div>
      {/if}
    </div>
  </div>

</div>

<style>
  .backtest-lab { display: flex; height: 100%; }
  .controls {
    width: 300px; padding: 16px; border-right: 1px solid #2d2d4a;
    display: flex; flex-direction: column; gap: 10px; flex-shrink: 0; overflow-y: auto;
  }
  h3 { color: #4caf50; margin: 0 0 8px; font-size: 13px; }
  label { display: flex; flex-direction: column; gap: 4px; font-size: 11px; color: #888; }
  select, input, textarea {
    background: #0f0f1e; border: 1px solid #2d2d4a; color: #ccc;
    padding: 4px 6px; font-size: 11px; border-radius: 3px;
  }
  textarea { font-family: monospace; resize: vertical; }
  button {
    padding: 6px 12px; background: #4caf5020; border: 1px solid #4caf5066;
    color: #4caf50; cursor: pointer; border-radius: 3px; font-size: 11px;
  }
  button:disabled { opacity: 0.5; cursor: default; }
  .error { color: #f44336; font-size: 11px; }
  .data-section { background: #0a0a15; border: 1px solid #2d2d4a; border-radius: 4px; padding: 8px; display: flex; flex-direction: column; gap: 6px; }
  .section-title { font-size: 10px; color: #666; text-transform: uppercase; letter-spacing: 0.5px; }

  /* Strategy about box */
  .about-box { background: #0a1a0a; border: 1px solid #1e3a1e; border-radius: 4px; padding: 8px 10px; }
  .about-name { font-size: 12px; color: #4caf50; font-weight: 600; margin-bottom: 4px; }
  .about-desc { font-size: 10px; color: #999; line-height: 1.5; margin-bottom: 6px; }
  .about-link { font-size: 10px; color: #6aa8ff; text-decoration: none; }
  .about-link:hover { text-decoration: underline; }

  /* Structured param form */
  .param-form { display: flex; flex-direction: column; gap: 6px; }
  .pf-row { display: flex; flex-direction: column; gap: 3px; }
  .pf-label { font-size: 11px; color: #888; position: relative; }
  .pf-i {
    display: inline-flex; align-items: center; justify-content: center;
    width: 13px; height: 13px; margin-left: 4px; border-radius: 50%;
    font-size: 9px; color: #6aa8ff; border: 1px solid #6aa8ff66; cursor: help; user-select: none;
  }
  .pf-i:hover { background: #6aa8ff22; }
  .pf-popover {
    position: absolute; left: 0; top: 100%; margin-top: 4px; z-index: 30;
    width: 240px; background: #12121f; border: 1px solid #3d3d5a; border-radius: 4px;
    padding: 8px 10px; box-shadow: 0 4px 16px #000000aa;
  }
  .pp-title { font-size: 11px; color: #fff; font-weight: 600; margin-bottom: 3px; }
  .pp-body { font-size: 11px; color: #bbb; line-height: 1.5; }
  .pp-range { font-size: 10px; color: #777; margin-top: 5px; font-family: monospace; }
  .period-row { display: flex; align-items: center; gap: 6px; }
  .period-row input { flex: 1; }
  .period-dash { color: #555; }
  .coverage { display: flex; flex-direction: column; gap: 2px; }
  .cov-item { font-size: 10px; color: #4caf50; font-family: monospace; }
  .no-data { font-size: 10px; color: #444; font-style: italic; }
  .load-btn { background: #1a1a2e; border-color: #3d3d5a; color: #aaa; padding: 4px 10px; font-size: 10px; }
  .data-status { font-size: 10px; color: #888; }
  .divider { height: 1px; background: #2d2d4a; margin: 4px 0; }
  .hint { font-size: 10px; color: #666; line-height: 1.4; }
  .engine-row { display: flex; gap: 4px; }
  .eng-btn { flex: 1; padding: 5px 8px; background: #0f0f1e; border: 1px solid #2d2d4a; color: #888; font-size: 11px; border-radius: 3px; cursor: pointer; }
  .eng-btn:hover { color: #ccc; }
  .eng-btn.active { background: #4caf5018; border-color: #4caf5066; color: #4caf50; }

  /* Results table */
  .results-section { display: flex; flex-direction: column; gap: 6px; flex-shrink: 0; max-height: 28%; overflow: auto; }

  /* Virtual trades ledger */
  .trades-section { display: flex; flex-direction: column; gap: 4px; flex: 1; min-height: 120px; overflow: hidden; }
  .trades-scroll { overflow: auto; border: 1px solid #1a1a2e; border-radius: 3px; }
  .trades-table { width: 100%; border-collapse: collapse; font-size: 9px; }
  .trades-table th { position: sticky; top: 0; background: #0f0f1e; color: #555; text-align: left; padding: 2px 4px; white-space: nowrap; border-bottom: 1px solid #1a1a2e; }
  .trades-table td { padding: 1px 4px; border-bottom: 1px solid #12121c; color: #aaa; white-space: nowrap; cursor: default; }
  .trades-table td.buy { color: #4caf50; }
  .trades-table td.sell { color: #f44336; }
  .trades-table .ttype { color: #777; }
  .disclaimer { font-size: 10px; color: #666; }
  table { width: 100%; border-collapse: collapse; font-size: 10px; }
  th { text-align: left; padding: 4px 6px; background: #0f0f1e; color: #555; border-bottom: 1px solid #2d2d4a; white-space: nowrap; }
  td { padding: 3px 6px; border-bottom: 1px solid #14141f; color: #ccc; cursor: pointer; }
  tr:hover td { background: #1a1a2e; }
  tr.selected td { background: #0d1a0d; }
  .params-cell { min-width: 80px; }
  .param-tag { display: inline-block; background: #1a1a2e; border-radius: 2px; padding: 1px 4px; margin: 1px; font-size: 9px; font-family: monospace; white-space: nowrap; }
  .pos { color: #4caf50; }
  .neg { color: #f44336; }
  .deploy-btn { padding: 1px 6px; font-size: 10px; background: #4caf5010; border: 1px solid #4caf5033; color: #4caf5099; cursor: pointer; border-radius: 2px; }
  .deploy-btn:hover { background: #4caf5025; color: #4caf50; }

  /* Center chart area */
  .chart-area { flex: 1; min-width: 0; overflow: hidden; border-left: 1px solid #2d2d4a; display: flex; flex-direction: column; }
  .center-tabs { display: flex; gap: 2px; padding: 4px 8px; background: #0f0f1e; border-bottom: 1px solid #1a1a2e; flex-shrink: 0; }
  .center-tabs button { padding: 3px 12px; background: transparent; color: #555; border: 1px solid transparent; font-size: 11px; border-radius: 3px; cursor: pointer; }
  .center-tabs button:hover { color: #aaa; }
  .center-tabs button.active { color: #4caf50; border-color: #4caf5066; }
  .center-body { flex: 1; min-height: 0; overflow: hidden; }
  .chart-placeholder {
    display: flex; flex-direction: column; align-items: center; justify-content: center;
    height: 100%; gap: 12px; color: #333;
  }
  .ph-icon { font-size: 48px; }
  .ph-text { font-size: 13px; color: #444; text-align: center; }
</style>
