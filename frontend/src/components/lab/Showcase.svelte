<!-- Showcase.svelte
     Витрина: все бумажные роботы, их P&L в реальном времени, глобальная лента сделок,
     кнопка остановки с комментарием "на доработку".
-->
<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import { fetchWithAuth } from '../../lib/fetch-auth';
  import { toFills, tradeEvents } from '../../lib/lab-analytics';
  import RobotWindow from './RobotWindow.svelte';

  // Robot whose chart + trades window is open (double-click a row to open).
  let windowRobotId = $state<string | null>(null);

  const INITIAL_EQUITY = 100_000;
  const EXECUTED = new Set(['paper', 'filled', 'submitted', 'executed']);
  const REFRESH_MS = 60_000;

  let robots = $state<any[]>([]);
  let loading = $state(true);
  let lastUpdate = $state<Date | null>(null);

  // Retire modal state
  let retireTarget = $state<any | null>(null);
  let retireComment = $state('');
  let retiring = $state(false);

  function computePnl(robot: any): { net: number; retPct: number; position: number; trades: number; margin: number } {
    const fills = toFills(
      (robot.trades ?? []).filter((t: any) => EXECUTED.has(t.status))
    );
    const pv = robot.point_value ?? 1;
    const sym = robot.symbol ?? '';
    const events = tradeEvents(fills, 60, pv, sym, false);
    let cum = 0;
    for (const e of events) if (e.close) cum += e.close.pnl;
    let pos = 0;
    for (const f of fills) pos += f.side === 'buy' ? f.qty : -f.qty;
    // Engaged ГО (initial margin) = per-contract margin × contracts currently held.
    const margin = (robot.initial_margin ?? 0) * Math.abs(pos);
    return {
      net: cum,
      retPct: (cum / INITIAL_EQUITY) * 100,
      position: pos,
      trades: fills.length,
      margin,
    };
  }

  // Pre-compute P&L for all robots — memoized on robots array identity
  let summaries = $derived(
    robots.map(r => ({ ...computePnl(r), robot: r }))
          .sort((a, b) => b.net - a.net)
  );

  // Totals across LIVE (deployed) robots — summary row at the top.
  let totals = $derived.by(() => {
    const live = summaries.filter(s => s.robot.deployed);
    const net = live.reduce((a, s) => a + s.net, 0);
    const trades = live.reduce((a, s) => a + s.trades, 0);
    const margin = live.reduce((a, s) => a + s.margin, 0);
    const count = live.length;
    return { net, trades, margin, count, retPct: count ? (net / (INITIAL_EQUITY * count)) * 100 : 0 };
  });

  // Global trades feed built from each robot's fills, so every row carries its
  // type (TP/SL/AVG/реверс/вход) and, on TP/SL, the round-trip P&L.
  let enrichedFeed = $derived.by(() => {
    const rows: any[] = [];
    for (const r of robots) {
      const fills = toFills((r.trades ?? []).filter((t: any) => EXECUTED.has(t.status)));
      const evs = tradeEvents(fills, 60, r.point_value ?? 1, r.symbol ?? '', false);
      for (const e of evs) {
        rows.push({
          time: e.rawTime, robot_name: r.name, symbol: r.symbol,
          side: e.side, qty: e.qty, price: e.price, kind: e.kind, close: e.close,
        });
      }
    }
    rows.sort((a, b) => b.time - a.time);
    return rows.slice(0, 100);
  });

  function tradeTypeLabel(row: any): { text: string; cls: string } {
    if (row.close) return { text: row.close.exit, cls: row.close.exit === 'TP' ? 'tt-tp' : 'tt-sl' };
    if (row.kind === 'average') return { text: 'AVG', cls: 'tt-avg' };
    if (row.kind === 'reverse') return { text: 'реверс', cls: 'tt-rev' };
    return { text: 'вход', cls: 'tt-open' };
  }

  async function load() {
    loading = true;
    const res = await fetchWithAuth('/api/v1/robots/showcase');
    if (res.ok) {
      robots = await res.json();
      lastUpdate = new Date();
    }
    loading = false;
  }

  async function openRetire(robot: any) {
    retireTarget = robot;
    retireComment = '';
  }

  async function confirmRetire() {
    if (!retireTarget) return;
    retiring = true;
    await fetchWithAuth(`/api/v1/robots/${retireTarget.id}/undeploy`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ comment: retireComment.trim() || null }),
    });
    retiring = false;
    retireTarget = null;
    await load();
  }

  const fmtMoney = (v: number) =>
    (v >= 0 ? '+' : '') + Math.round(v).toLocaleString('ru-RU') + ' ₽';
  const fmtPct = (v: number) => (v >= 0 ? '+' : '') + v.toFixed(1) + '%';
  const fmtUnix = (sec: number) =>
    new Date(sec * 1000).toLocaleString('ru-RU', {
      timeZone: 'Europe/Moscow', day: '2-digit', month: '2-digit',
      hour: '2-digit', minute: '2-digit',
    });
  const fmtSince = (iso: string | null) => {
    if (!iso) return '—';
    const diff = (Date.now() - new Date(iso).getTime()) / 86400000;
    if (diff < 1) return '< 1 д.';
    return Math.floor(diff) + ' д.';
  };

  let timer: ReturnType<typeof setInterval>;

  onMount(() => {
    load();
    timer = setInterval(load, REFRESH_MS);
  });

  onDestroy(() => clearInterval(timer));
</script>

<!-- Per-robot chart + trades window (double-click a row) -->
{#if windowRobotId}
  <RobotWindow robotId={windowRobotId} onClose={() => windowRobotId = null} />
{/if}

<!-- Retire confirm modal -->
{#if retireTarget}
  <div class="modal-overlay" onclick={() => retireTarget = null} role="dialog" aria-modal="true">
    <div class="modal" onclick={(e) => e.stopPropagation()}>
      <div class="modal-title">Остановить робота</div>
      <div class="modal-robot">{retireTarget.name}</div>
      <div class="modal-label">Комментарий (на доработку стратегии):</div>
      <textarea
        class="modal-textarea"
        bind:value={retireComment}
        placeholder="Например: слишком широкий стоп, не работает на боковике, переход на D1..."
        rows="4"
      ></textarea>
      <div class="modal-actions">
        <button class="btn-cancel" onclick={() => retireTarget = null}>Отмена</button>
        <button class="btn-retire" onclick={confirmRetire} disabled={retiring}>
          {retiring ? 'Останавливаю…' : 'Убрать из LIVE'}
        </button>
      </div>
    </div>
  </div>
{/if}

<div class="sc-wrap">
  <!-- Header -->
  <div class="sc-header">
    <span class="sc-title">Витрина — Бумажная торговля</span>
    <div class="sc-meta">
      {#if lastUpdate}
        <span class="sc-updated">обновлено {lastUpdate.toLocaleTimeString('ru-RU')}</span>
      {/if}
      <button class="sc-refresh" onclick={load}>↺ Обновить</button>
    </div>
  </div>

  <!-- Totals across LIVE robots -->
  <div class="sc-totals">
    <span class="tot-label">Итого LIVE ({totals.count}):</span>
    <span class="tot-item">P&amp;L
      <b class:pos={totals.net > 0} class:neg={totals.net < 0}>{fmtMoney(totals.net)}</b>
      <span class="tot-pct" class:pos={totals.net > 0} class:neg={totals.net < 0}>({fmtPct(totals.retPct)})</span>
    </span>
    <span class="tot-item">Сделок <b>{totals.trades}</b></span>
    <span class="tot-item">ГО <b>{Math.round(totals.margin).toLocaleString('ru-RU')} ₽</b></span>
  </div>

  <!-- Robot summary table -->
  <div class="sc-section">
    <div class="sc-section-title">
      Роботы ({summaries.filter(s => s.robot.deployed).length} активных)
      <span class="sc-hint">· P&amp;L % — доход от стартового капитала {INITIAL_EQUITY.toLocaleString('ru-RU')} ₽ на робота · ГО — задействованное гарантийное обеспечение текущей позиции</span>
    </div>
    {#if loading}
      <div class="sc-loading">Загрузка…</div>
    {:else}
      <div class="sc-table-wrap">
        <table class="sc-table">
          <thead>
            <tr>
              <th>Робот</th>
              <th>Инструмент</th>
              <th class="num">P&amp;L ₽</th>
              <th class="num">P&amp;L %</th>
              <th class="num">ГО ₽</th>
              <th class="num">Позиция</th>
              <th class="num">Сделок</th>
              <th class="num">Дней</th>
              <th class="num">Статус</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {#each summaries as s}
              {@const r = s.robot}
              <tr class="sc-row" class:stopped={!r.deployed}
                  title="Двойной клик — график и сделки робота"
                  ondblclick={() => windowRobotId = r.id}>
                <td class="sc-name">
                  <span class="dot" class:live={r.deployed}></span>
                  {r.name}
                  {#if r.retire_comment}
                    <span class="retire-note" title={r.retire_comment}>📝</span>
                  {/if}
                </td>
                <td class="sc-sym">{r.symbol || '—'}</td>
                <td class="num sc-pnl" class:pos={s.net > 0} class:neg={s.net < 0}>
                  {s.trades > 0 ? fmtMoney(s.net) : '—'}
                </td>
                <td class="num sc-pct" class:pos={s.net > 0} class:neg={s.net < 0}>
                  {s.trades > 0 ? fmtPct(s.retPct) : '—'}
                </td>
                <td class="num sc-go">
                  {s.margin > 0 ? Math.round(s.margin).toLocaleString('ru-RU') : '—'}
                </td>
                <td class="num" class:pos={s.position > 0} class:neg={s.position < 0}>
                  {s.position !== 0 ? (s.position > 0 ? '+' : '') + s.position + ' к' : '—'}
                </td>
                <td class="num">{s.trades}</td>
                <td class="num">{fmtSince(r.deployed_at)}</td>
                <td class="num">
                  {#if r.deployed}
                    <span class="badge-live">LIVE</span>
                  {:else if r.retire_comment}
                    <span class="badge-rework">доработка</span>
                  {:else}
                    <span class="badge-off">off</span>
                  {/if}
                </td>
                <td ondblclick={(e) => e.stopPropagation()}>
                  {#if r.deployed}
                    <button class="btn-stop" onclick={() => openRetire(r)}>Стоп</button>
                  {:else if r.retire_comment}
                    <span class="comment-pill" title={r.retire_comment}>{r.retire_comment.slice(0, 40)}{r.retire_comment.length > 40 ? '…' : ''}</span>
                  {/if}
                </td>
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
    {/if}
  </div>

  <!-- Live trades feed -->
  <div class="sc-section sc-feed-section">
    <div class="sc-section-title">Лента сделок (последние 100)</div>
    {#if loading}
      <div class="sc-loading">Загрузка…</div>
    {:else if enrichedFeed.length === 0}
      <div class="sc-empty">Сделок пока нет</div>
    {:else}
      <div class="sc-feed-wrap">
        <table class="sc-table sc-feed">
          <thead>
            <tr>
              <th>Время (МСК)</th>
              <th>Робот</th>
              <th>Инструмент</th>
              <th>Тип</th>
              <th>Сторона</th>
              <th class="num">Кол-во</th>
              <th class="num">Цена</th>
              <th class="num">Фин. рез</th>
            </tr>
          </thead>
          <tbody>
            {#each enrichedFeed as t}
              {@const tt = tradeTypeLabel(t)}
              <tr>
                <td class="sc-time">{fmtUnix(t.time)}</td>
                <td class="sc-rname">{t.robot_name}</td>
                <td class="sc-sym">{t.symbol}</td>
                <td><span class="tt-badge {tt.cls}">{tt.text}</span></td>
                <td class="sc-side" class:buy={t.side === 'buy'} class:sell={t.side === 'sell'}>
                  {t.side === 'buy' ? 'Покупка' : 'Продажа'}
                </td>
                <td class="num">{t.qty}</td>
                <td class="num sc-price">{t.price.toLocaleString('ru-RU')}</td>
                <td class="num sc-price" class:pos={t.close && t.close.pnl > 0} class:neg={t.close && t.close.pnl < 0}>
                  {t.close ? fmtMoney(t.close.pnl) : '—'}
                </td>
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
    {/if}
  </div>
</div>

<style>
  .sc-wrap { display: flex; flex-direction: column; height: 100%; overflow: hidden; gap: 0; }

  .sc-header {
    display: flex; justify-content: space-between; align-items: center;
    padding: 8px 14px; border-bottom: 1px solid #2d2d4a; flex-shrink: 0;
  }
  .sc-title { font-size: 12px; font-weight: 600; color: #ccc; text-transform: uppercase; letter-spacing: 0.5px; }
  .sc-meta { display: flex; align-items: center; gap: 10px; }
  .sc-updated { font-size: 10px; color: #556; }
  .sc-refresh { background: #1a1a2e; border: 1px solid #3d3d5a; color: #aaa; font-size: 10px; padding: 3px 8px; border-radius: 3px; cursor: pointer; }
  .sc-refresh:hover { color: #fff; }

  .sc-section { display: flex; flex-direction: column; min-height: 0; overflow: hidden; }
  .sc-section:first-of-type { flex: 1 1 0; border-bottom: 1px solid #2d2d4a; }
  .sc-feed-section { flex: 0 0 260px; }
  .sc-section-title { font-size: 10px; color: #4a4a6a; text-transform: uppercase; letter-spacing: 0.4px; padding: 6px 14px 4px; flex-shrink: 0; }
  .sc-loading, .sc-empty { padding: 14px; color: #556; font-size: 12px; }

  .sc-table-wrap, .sc-feed-wrap { overflow-y: auto; flex: 1; min-height: 0; }

  .sc-table { width: 100%; border-collapse: collapse; font-size: 11px; }
  .sc-table thead th {
    position: sticky; top: 0; background: #0d0d1a; z-index: 1;
    color: #556; font-size: 10px; font-weight: 500; text-align: left;
    padding: 5px 10px; border-bottom: 1px solid #1e1e3a;
  }
  .sc-table th.num, .sc-table td.num { text-align: right; }
  .sc-table td { padding: 7px 10px; border-bottom: 1px solid #0f0f1e; color: #aaa; }
  .sc-row { cursor: pointer; }
  .sc-row:hover td { background: #12121f; }
  .sc-row.stopped td { opacity: 0.55; }

  .dot { display: inline-block; width: 6px; height: 6px; border-radius: 50%; background: #333; margin-right: 5px; vertical-align: middle; }
  .dot.live { background: #4caf50; box-shadow: 0 0 4px #4caf5088; }

  .sc-name { color: #ccc; max-width: 180px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .sc-sym { color: #4caf50; font-family: monospace; font-weight: 600; }
  .sc-time { font-family: monospace; color: #667; font-size: 10px; }
  .sc-rname { color: #aaa; max-width: 140px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .sc-price { font-family: monospace; }

  .sc-pnl { font-weight: 700; font-size: 12px; }
  .sc-pct { font-size: 11px; }
  .sc-go { color: #b8b8d0; font-family: monospace; }
  .pos { color: #00e676; text-shadow: 0 0 6px #00e67644; }
  .neg { color: #ff5252; text-shadow: 0 0 6px #ff525244; }

  /* Totals band */
  .sc-totals {
    display: flex; align-items: baseline; gap: 18px; flex-wrap: wrap;
    padding: 7px 14px; background: #0b0b16; border-bottom: 1px solid #2d2d4a; flex-shrink: 0;
  }
  .tot-label { font-size: 10px; color: #4a4a6a; text-transform: uppercase; letter-spacing: 0.4px; }
  .tot-item { font-size: 11px; color: #889; }
  .tot-item b { font-size: 13px; color: #ccc; font-family: monospace; margin-left: 4px; }
  .tot-pct { font-size: 11px; }
  .sc-hint { font-size: 9px; color: #445; text-transform: none; letter-spacing: 0; font-weight: 400; }

  /* Trade-type badges */
  .tt-badge { font-size: 9px; padding: 1px 6px; border-radius: 2px; background: #1a1a2e; color: #888; font-weight: 600; }
  .tt-tp { background: #11271a; color: #4caf50; }
  .tt-sl { background: #2a1414; color: #ff6b6b; }
  .tt-avg { background: #2a2410; color: #ffb300; }
  .tt-rev { background: #1a1430; color: #b388ff; }
  .tt-open { background: #14222a; color: #6aa8ff; }

  .sc-side.buy { color: #4caf50; }
  .sc-side.sell { color: #f44336; }

  .badge-live { font-size: 9px; color: #4caf50; font-weight: 600; }
  .badge-rework { font-size: 9px; color: #ff9800; }
  .badge-off { font-size: 9px; color: #555; }

  .btn-stop {
    padding: 3px 10px; font-size: 10px; background: #1a0a0a;
    border: 1px solid #f4433666; color: #f44336; border-radius: 3px; cursor: pointer;
    white-space: nowrap;
  }
  .btn-stop:hover { background: #2a0a0a; border-color: #f44336; }

  .comment-pill {
    display: inline-block; max-width: 200px; overflow: hidden; text-overflow: ellipsis;
    white-space: nowrap; font-size: 10px; color: #ff9800; background: #1a1000;
    border: 1px solid #ff980033; border-radius: 10px; padding: 2px 8px;
  }
  .retire-note { font-size: 11px; cursor: help; }

  /* Modal */
  .modal-overlay {
    position: fixed; inset: 0; z-index: 3000; background: rgba(0,0,0,0.7);
    display: flex; align-items: center; justify-content: center;
    backdrop-filter: blur(2px);
  }
  .modal {
    background: #0d0d1a; border: 1px solid #3d3d5a; border-radius: 6px;
    padding: 24px; width: 420px; max-width: 95vw; display: flex; flex-direction: column; gap: 12px;
    box-shadow: 0 10px 40px rgba(0,0,0,0.7);
  }
  .modal-title { font-size: 13px; font-weight: 600; color: #f44336; }
  .modal-robot { font-size: 14px; color: #eee; font-weight: 600; }
  .modal-label { font-size: 11px; color: #888; }
  .modal-textarea {
    background: #080810; border: 1px solid #3d3d5a; color: #ccc; font-size: 12px;
    border-radius: 4px; padding: 8px 10px; resize: vertical; font-family: inherit;
    width: 100%; box-sizing: border-box;
  }
  .modal-textarea:focus { outline: none; border-color: #ff9800; }
  .modal-actions { display: flex; justify-content: flex-end; gap: 8px; }
  .btn-cancel {
    padding: 6px 14px; font-size: 11px; background: #1a1a2e; border: 1px solid #3d3d5a;
    color: #aaa; border-radius: 4px; cursor: pointer;
  }
  .btn-cancel:hover { color: #fff; }
  .btn-retire {
    padding: 6px 16px; font-size: 11px; background: #2a0a0a; border: 1px solid #f44336;
    color: #f44336; border-radius: 4px; cursor: pointer; font-weight: 600;
  }
  .btn-retire:hover:not(:disabled) { background: #3a0a0a; }
  .btn-retire:disabled { opacity: 0.5; cursor: not-allowed; }
</style>
