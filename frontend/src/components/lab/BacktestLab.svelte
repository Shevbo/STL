<script lang="ts">
  import { fetchWithAuth } from '../../lib/fetch-auth';
  import BacktestChart from './BacktestChart.svelte';
  import { toFills, replay } from '../../lib/lab-analytics';

  const TYPE_LABEL: Record<string, string> = {
    open: 'Открытие', average: 'Усреднение', close: 'Закрытие', reverse: 'Закр+Реверс',
  };
  const fmtT = (ts: number) => new Date(ts * 1000).toLocaleString('ru-RU', {
    timeZone: 'Europe/Moscow', day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit',
  });
  const fmtP = (v: number) => (v >= 0 ? '+' : '') + Math.round(v);

  // trades ledger for the selected result (newest first)
  let ledger = $derived(
    selectedResult ? replay(toFills(selectedResult.trades)).ledger.slice().reverse() : []
  );

  let robots = $state<any[]>([]);
  let selectedRobotId = $state('');
  let dateFrom = $state('2026-01-01');
  let dateTo = $state('2026-05-01');
  let paramsGrid = $state('{}');
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

  let selectedSymbol = $derived(
    (() => {
      const robot = robots.find(r => r.id === selectedRobotId);
      const pj = robot?.params_json;
      return (typeof pj === 'object' ? pj?.symbol : null) ?? 'RIM6';
    })()
  );

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

  async function runBacktest() {
    error = '';
    running = true;
    results = [];
    try {
      const robot = robots.find(r => r.id === selectedRobotId);
      const symbol = robot?.params_json?.symbol || '';
      const res = await fetchWithAuth('/api/v1/backtest/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          robotId: selectedRobotId,
          symbol,
          dateFrom: new Date(dateFrom).toISOString(),
          dateTo: new Date(dateTo).toISOString(),
          paramsGrid: JSON.parse(paramsGrid),
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

  $effect(() => { loadRobots(); loadCoverage(); loadStrategies(); });
</script>

<div class="backtest-lab">

  <!-- ── Left: controls ─────────────────────────────────────────── -->
  <div class="controls">
    <h3>Backtest Lab</h3>
    <label>
      Robot
      <select bind:value={selectedRobotId}>
        {#each robots as r}<option value={r.id}>{r.name}</option>{/each}
      </select>
    </label>
    <label>From <input type="date" bind:value={dateFrom} /></label>
    <label>To   <input type="date" bind:value={dateTo} /></label>
    <label>
      Params grid (JSON)
      <textarea bind:value={paramsGrid} rows="4" placeholder="{`{\"entry_period\":[20,40]}`}"></textarea>
    </label>

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
        <div class="disclaimer">⚠ Results may differ from live (no slippage model)</div>
        <table>
          <thead>
            <tr>
              <th>Params</th><th>Return</th><th>Sharpe</th><th>MaxDD</th><th>Win%</th><th>N</th><th></th>
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

  <!-- ── Center: chart ──────────────────────────────────────────── -->
  <div class="chart-area">
    {#if selectedResult}
      <BacktestChart
        result={selectedResult}
        symbol={selectedSymbol}
        strategy={selectedStrategy}
        dateFrom={new Date(dateFrom).toISOString()}
        dateTo={new Date(dateTo).toISOString()}
      />
    {:else}
      <div class="chart-placeholder">
        <div class="ph-icon">📈</div>
        <div class="ph-text">Запустите бэктест и выберите строку результатов</div>
      </div>
    {/if}
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
  .coverage { display: flex; flex-direction: column; gap: 2px; }
  .cov-item { font-size: 10px; color: #4caf50; font-family: monospace; }
  .no-data { font-size: 10px; color: #444; font-style: italic; }
  .load-btn { background: #1a1a2e; border-color: #3d3d5a; color: #aaa; padding: 4px 10px; font-size: 10px; }
  .data-status { font-size: 10px; color: #888; }
  .divider { height: 1px; background: #2d2d4a; margin: 4px 0; }

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
  .chart-area { flex: 1; min-width: 0; overflow: hidden; border-left: 1px solid #2d2d4a; }
  .chart-placeholder {
    display: flex; flex-direction: column; align-items: center; justify-content: center;
    height: 100%; gap: 12px; color: #333;
  }
  .ph-icon { font-size: 48px; }
  .ph-text { font-size: 13px; color: #444; text-align: center; }
</style>
