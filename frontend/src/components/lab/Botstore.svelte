<!-- Botstore.svelte — catalog of portable robots + their best backtest results
     found during background optimization campaigns. -->
<script lang="ts">
  import { fetchWithAuth } from '../../lib/fetch-auth';

  let { onUseInBacktest }: { onUseInBacktest?: (preset: any) => void } = $props();

  let loading = $state(true);
  let error = $state('');
  let initialEquity = $state(100000);
  let catalog = $state<any[]>([]);
  let robotsCount = $state(0);
  let expanded = $state<Record<string, boolean>>({});

  // Build a Backtest Lab preset from a catalog row + chosen result, and switch tab.
  function useResult(robot: any, r: any, ev?: Event) {
    ev?.stopPropagation();
    if (!r) return;
    onUseInBacktest?.({
      strategyId: robot.id,
      name: robot.name,
      symbol: r.symbol,
      params: r.params,
      dateFrom: r.date_from,
      dateTo: r.date_to,
    });
  }

  async function load() {
    loading = true; error = '';
    try {
      const res = await fetchWithAuth('/api/v1/botstore');
      if (!res.ok) throw new Error(await res.text());
      const d = await res.json();
      initialEquity = d.initial_equity ?? 100000;
      robotsCount = d.robots_count ?? 0;
      catalog = d.catalog ?? [];
    } catch (e) {
      error = String(e);
    }
    loading = false;
  }

  // best result per robot (top row across symbols) for the summary line
  function best(robot: any) {
    const rs = robot.results ?? [];
    return rs.length ? rs[0] : null;
  }

  const fmtPct = (v: any) => v != null ? (v * 100).toFixed(2) + '%' : '—';
  const fmtMoney = (v: any) => v != null ? Math.round(v).toLocaleString('ru-RU') + ' ₽' : '—';
  const fmtNum = (v: any, d = 2) => v != null ? Number(v).toFixed(d) : '—';
  const fmtDate = (v: any) => v ? new Date(v).toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', year: '2-digit', hour: '2-digit', minute: '2-digit' }) : '—';
  const fmtPeriod = (a: any, b: any) => (a && b) ? `${a} — ${b}` : '—';

  let totalVariants = $derived(catalog.reduce((s, r) => s + (r.variants_tested || 0), 0));

  $effect(() => { load(); });
</script>

<div class="bs-root">
  <!-- preamble -->
  <div class="bs-preamble">
    <div class="bs-title">Botstore — каталог роботов</div>
    <div class="bs-note">
      Портировано роботов: <b>{robotsCount}</b> · всего протестировано вариантов параметров: <b>{totalVariants.toLocaleString('ru-RU')}</b>.
      Доходность и просадка рассчитаны исходя из первоначальных инвестиций
      <b>{initialEquity.toLocaleString('ru-RU')} ₽</b>. Расчёт в рублях по реальной стоимости пункта и ГО с MOEX ISS.
      При усреднении позиции ГО возрастает пропорционально числу контрактов.
      Результаты — in-sample на историческом окне, без walk-forward: это шортлист для форвард-теста, не для слепого запуска.
    </div>
  </div>

  {#if loading}
    <div class="bs-msg">Загрузка каталога…</div>
  {:else if error}
    <div class="bs-msg err">{error}</div>
  {:else if catalog.length === 0}
    <div class="bs-msg">Нет данных. Фоновые прогоны оптимизации ещё не накопили результатов.</div>
  {:else}
    <div class="bs-table-wrap">
      <table class="bs-table">
        <thead>
          <tr>
            <th></th>
            <th>Робот</th>
            <th>Вариантов</th>
            <th>Послед. прогон</th>
            <th>Лучший инстр.</th>
            <th>Период</th>
            <th>Лучшие параметры</th>
            <th>Доходность</th>
            <th>Чистыми ₽</th>
            <th>Макс. просадка</th>
            <th>Фактор восст.</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {#each catalog as robot}
            {@const b = best(robot)}
            <tr class="bs-row" class:has-detail={(robot.results?.length ?? 0) > 1}
                onclick={() => expanded[robot.id] = !expanded[robot.id]}
                role="button" tabindex="0"
                onkeydown={(e) => e.key === 'Enter' && (expanded[robot.id] = !expanded[robot.id])}>
              <td class="bs-exp">{(robot.results?.length ?? 0) > 1 ? (expanded[robot.id] ? '▾' : '▸') : ''}</td>
              <td class="bs-name">{robot.name}</td>
              <td class="bs-num">{(robot.variants_tested || 0).toLocaleString('ru-RU')}</td>
              <td class="bs-dt">{fmtDate(robot.last_run)}</td>
              <td>{b?.symbol ?? '—'}</td>
              <td class="bs-period">{b ? fmtPeriod(b.date_from, b.date_to) : '—'}</td>
              <td class="bs-params">{b ? JSON.stringify(b.params) : '—'}</td>
              <td class:pos={b?.total_return > 0} class:neg={b?.total_return < 0}>{b ? fmtPct(b.total_return) : '—'}</td>
              <td class:pos={b?.net_profit > 0} class:neg={b?.net_profit < 0}>{b ? fmtMoney(b.net_profit) : '—'}</td>
              <td>{b ? fmtPct(b.max_drawdown) : '—'}</td>
              <td class:pos={b?.recovery_factor > 1}>{b ? fmtNum(b.recovery_factor) : '—'}</td>
              <td>
                {#if b}
                  <button class="bs-use" title="Установить параметры в Backtest Lab"
                    onclick={(e) => useResult(robot, b, e)}>→ в Backtest Lab</button>
                {/if}
              </td>
            </tr>
            {#if expanded[robot.id]}
              {#each (robot.results ?? []) as r}
                <tr class="bs-detail">
                  <td></td><td></td><td></td><td></td>
                  <td>{r.symbol}</td>
                  <td class="bs-period">{fmtPeriod(r.date_from, r.date_to)}</td>
                  <td class="bs-params">{JSON.stringify(r.params)}</td>
                  <td class:pos={r.total_return > 0} class:neg={r.total_return < 0}>{fmtPct(r.total_return)}</td>
                  <td class:pos={r.net_profit > 0} class:neg={r.net_profit < 0}>{fmtMoney(r.net_profit)}</td>
                  <td>{fmtPct(r.max_drawdown)}</td>
                  <td class:pos={r.recovery_factor > 1}>{fmtNum(r.recovery_factor)}</td>
                  <td>
                    <button class="bs-use" title="Установить параметры в Backtest Lab"
                      onclick={(e) => useResult(robot, r, e)}>→ в Backtest Lab</button>
                  </td>
                </tr>
              {/each}
            {/if}
          {/each}
        </tbody>
      </table>
    </div>
    <button class="bs-refresh" onclick={load}>Обновить</button>
  {/if}
</div>

<style>
  .bs-root { display: flex; flex-direction: column; height: 100%; overflow: auto; background: #0a0a15; padding: 12px; gap: 12px; }
  .bs-preamble { background: #0f0f1e; border: 1px solid #2d2d4a; border-radius: 5px; padding: 10px 12px; }
  .bs-title { font-size: 13px; color: #4caf50; font-weight: 600; margin-bottom: 6px; }
  .bs-note { font-size: 11px; color: #888; line-height: 1.5; }
  .bs-note b { color: #ccc; }

  .bs-msg { font-size: 12px; color: #666; padding: 20px; text-align: center; }
  .bs-msg.err { color: #f44336; }

  .bs-table-wrap { overflow: auto; border: 1px solid #1a1a2e; border-radius: 4px; }
  .bs-table { width: 100%; border-collapse: collapse; font-size: 11px; }
  .bs-table th { position: sticky; top: 0; background: #0f0f1e; color: #666; text-align: left; padding: 6px 8px; white-space: nowrap; border-bottom: 1px solid #2d2d4a; }
  .bs-table td { padding: 5px 8px; border-bottom: 1px solid #12121c; color: #ccc; white-space: nowrap; }
  .bs-row { cursor: pointer; }
  .bs-row:hover td { background: #1a1a2e; }
  .bs-exp { color: #4caf50; width: 18px; text-align: center; }
  .bs-name { color: #ccc; font-weight: 600; }
  .bs-num { text-align: right; color: #6aa8ff; }
  .bs-dt { color: #888; font-size: 10px; }
  .bs-period { color: #888; font-size: 10px; }
  .bs-params { font-family: monospace; font-size: 10px; color: #999; max-width: 240px; overflow: hidden; text-overflow: ellipsis; }
  .bs-detail td { background: #08080f; color: #aaa; font-size: 10px; border-bottom: 1px solid #0f0f18; }
  .pos { color: #4caf50; }
  .neg { color: #f44336; }
  .bs-refresh { align-self: flex-start; padding: 5px 14px; background: #1a1a2e; border: 1px solid #2d2d4a; color: #aaa; border-radius: 3px; font-size: 11px; cursor: pointer; }
  .bs-refresh:hover { color: #4caf50; border-color: #4caf5066; }
  .bs-use {
    padding: 2px 8px; background: #4caf5018; border: 1px solid #4caf5066; color: #4caf50;
    border-radius: 3px; font-size: 10px; cursor: pointer; white-space: nowrap;
  }
  .bs-use:hover { background: #4caf5030; }
</style>
