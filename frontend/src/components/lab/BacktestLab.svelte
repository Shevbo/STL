<script lang="ts">
  import { fetchWithAuth } from '../../lib/fetch-auth';

  let robots = $state<any[]>([]);
  let selectedRobotId = $state('');
  let dateFrom = $state('2026-01-01');
  let dateTo = $state('2026-05-01');
  let paramsGrid = $state('{"fast_period": [5, 9, 14], "slow_period": [20, 30]}');
  let runId = $state('');
  let status = $state('');
  let results = $state<any[]>([]);
  let running = $state(false);
  let error = $state('');

  async function loadRobots() {
    const res = await fetchWithAuth('/api/v1/robots');
    robots = res.ok ? await res.json() : [];
    if (robots.length && !selectedRobotId) selectedRobotId = robots[0].id;
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

  $effect(() => { loadRobots(); });
</script>

<div class="backtest-lab">
  <div class="controls">
    <h3>Backtest Lab</h3>
    <label>
      Robot
      <select bind:value={selectedRobotId}>
        {#each robots as r}<option value={r.id}>{r.name}</option>{/each}
      </select>
    </label>
    <label>From <input type="date" bind:value={dateFrom} /></label>
    <label>To <input type="date" bind:value={dateTo} /></label>
    <label>
      Params grid (JSON)
      <textarea bind:value={paramsGrid} rows="4"></textarea>
    </label>
    <button onclick={runBacktest} disabled={running}>
      {running ? `Running… (${status})` : 'Run Backtest'}
    </button>
    {#if error}<div class="error">{error}</div>{/if}
  </div>

  {#if results.length}
    <div class="results">
      <p class="disclaimer">Results may differ from live due to slippage and latency.</p>
      <table>
        <thead>
          <tr>
            <th>Params</th><th>Sharpe</th><th>Max DD</th>
            <th>Win Rate</th><th>Return</th><th>Trades</th><th>Deploy</th>
          </tr>
        </thead>
        <tbody>
          {#each results as r}
            <tr>
              <td class="params-cell">{JSON.stringify(r.params)}</td>
              <td>{r.sharpe?.toFixed(2) ?? '—'}</td>
              <td>{r.max_drawdown != null ? (r.max_drawdown * 100).toFixed(1) + '%' : '—'}</td>
              <td>{r.win_rate != null ? (r.win_rate * 100).toFixed(0) + '%' : '—'}</td>
              <td>{r.total_return != null ? (r.total_return * 100).toFixed(2) + '%' : '—'}</td>
              <td>{r.total_trades ?? 0}</td>
              <td><button onclick={() => deployResult(r.params)}>Deploy</button></td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  {/if}
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
  .results { flex: 1; overflow: auto; padding: 16px; }
  .disclaimer { font-size: 10px; color: #666; margin-bottom: 8px; }
  table { width: 100%; border-collapse: collapse; font-size: 11px; }
  th { text-align: left; padding: 6px 8px; background: #1a1a2e; color: #888; border-bottom: 1px solid #2d2d4a; }
  td { padding: 4px 8px; border-bottom: 1px solid #1e1e3a; color: #ccc; }
  td.params-cell { font-family: monospace; font-size: 10px; max-width: 200px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
</style>
