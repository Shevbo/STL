<!-- LIVE tab: real-money (deployed && !paper) robots with the exchange ping + a rolling
     latency sparkline. All Finam robots share ONE gateway link, so the ping shown on each
     card is that shared Finam-link round-trip (honestly labelled). Double-click opens the
     robot window. -->
<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import { fetchWithAuth } from '../../lib/fetch-auth';
  import RobotIdentity from './RobotIdentity.svelte';
  import { fetchLatency, pingColor, pingLabel, fmtMs, type LatencyResponse } from '../../lib/latency';
  import { fetchAgentRobots, openAgentRobot, type AgentRobotRow } from '../../lib/agent-robots';

  let { onOpen, onShowRobots }: { onOpen: (id: string) => void; onShowRobots?: () => void } = $props();

  let robots = $state<any[]>([]);
  let agentRobots = $state<AgentRobotRow[]>([]);
  let lat = $state<LatencyResponse | null>(null);
  let loading = $state(true);

  // Real-money live robots only.
  const liveRobots = $derived(robots.filter(r => r.deployed && r.paper === false));
  // Real-money robots hosted ON the QUIK agent (separate world; open their full stand).
  const agentLive = $derived(agentRobots.filter(r => !r.paper));
  // Развёрнутые БУМАЖНЫЕ роботы: они не тут (LIVE = только реал), а во вкладке «Роботы».
  // Кнопка «Запустить в торговлю» создаёт именно paper — оператор искал их здесь и не
  // находил. Показываем счётчик + переход, чтобы не блуждать.
  const paperCount = $derived(
    robots.filter(r => r.deployed && r.paper === true).length
    + agentRobots.filter(r => r.paper).length);
  const fmtRub = (v: number) => (v >= 0 ? '+' : '') + Math.round(v).toLocaleString('ru-RU') + ' ₽';

  const lastRtt = $derived(lat?.summary?.last_rtt_ms ?? null);
  // Last ~60 ok RTT samples (5 min) for the header sparkline.
  const spark = $derived((lat?.samples ?? []).filter(s => s.ok && s.rtt_ms != null).slice(-60));

  function sparkPath(samples: { rtt_ms: number | null }[], w = 220, h = 34): string {
    const pts = samples.map(s => s.rtt_ms as number);
    if (pts.length < 2) return '';
    const lo = Math.min(...pts), hi = Math.max(...pts);
    const span = hi - lo || 1;
    const dx = w / (pts.length - 1);
    return pts.map((v, i) => `${i === 0 ? 'M' : 'L'}${(i * dx).toFixed(1)},${(h - ((v - lo) / span) * (h - 4) - 2).toFixed(1)}`).join(' ');
  }

  async function loadRobots() {
    try {
      const res = await fetchWithAuth('/api/v1/robots/showcase');
      robots = res.ok ? await res.json() : [];
    } catch { robots = []; }
  }

  async function loadAgentRobots() {
    // fetchAgentRobots never throws (returns [] on any failure) so an agent
    // outage degrades to "no agent robots", never blanks the Finam list.
    agentRobots = await fetchAgentRobots();
  }

  async function loadLatency() {
    const r = await fetchLatency(360);
    if (r) lat = r;
  }

  let timer: ReturnType<typeof setInterval> | null = null;
  onMount(async () => {
    await Promise.all([loadRobots(), loadLatency()]);
    loading = false;
    // Agent robots fill in independently — a slow/down agent must not hold the
    // Finam cards on "Загрузка…" (their section is gated on its own .length).
    void loadAgentRobots();
    // Ping refreshes on the 5s probe cadence; robot lists every 6 ticks (30s).
    let n = 0;
    timer = setInterval(async () => {
      await loadLatency();
      if (++n % 6 === 0) await Promise.all([loadRobots(), loadAgentRobots()]);
    }, 5000);
  });
  onDestroy(() => { if (timer) clearInterval(timer); });
</script>

<div class="live-screen">
  <!-- Link ping header -->
  <div class="link-bar">
    <div class="link-left">
      <span class="link-name">Биржа Finam</span>
      <span class="ping-big" style="color:{pingColor(lastRtt)}">{fmtMs(lastRtt)}</span>
      <span class="ping-cap" style="color:{pingColor(lastRtt)}">{pingLabel(lastRtt)}</span>
    </div>
    <div class="link-mid">
      <svg class="spark" viewBox="0 0 220 34" preserveAspectRatio="none">
        <path d={sparkPath(spark)} fill="none" stroke={pingColor(lastRtt)} stroke-width="1.5" />
      </svg>
    </div>
    <div class="link-right">
      <div class="stat"><span class="k">min</span><span class="v">{fmtMs(lat?.summary?.rtt_min_ms)}</span></div>
      <div class="stat"><span class="k">p50</span><span class="v">{fmtMs(lat?.summary?.rtt_p50_ms)}</span></div>
      <div class="stat"><span class="k">p95</span><span class="v">{fmtMs(lat?.summary?.rtt_p95_ms)}</span></div>
    </div>
  </div>
  <div class="method-note">
    Пинг = круговое время (RTT) лёгкого запроса к шлюзу Finam по тому же HTTP/2-каналу, что и
    заявки. Замер каждые {lat?.interval_s ?? 5}с, лог хранится. Разбивка исходящая/входящая — в окне робота.
  </div>

  {#if paperCount > 0}
    <div class="paper-hint">
      Здесь только роботы на <b>реальные деньги</b>. Ваши бумажные (paper) роботы —
      их <b>{paperCount}</b> — торгуют в разделе
      <button class="paper-link" onclick={() => onShowRobots?.()}>«Роботы» →</button>
      («Запустить в торговлю» создаёт именно бумажного робота).
    </div>
  {/if}

  {#if loading}
    <div class="empty">Загрузка…</div>
  {:else if liveRobots.length === 0 && agentLive.length === 0}
    <div class="empty">Нет LIVE-роботов на реальные деньги.{#if paperCount > 0} Бумажные — в разделе «Роботы» (см. выше).{/if}</div>
  {:else}
    {#if agentLive.length}
      <div class="group-band">Роботы на бирже QUIK · агент · реальные деньги</div>
      <div class="cards">
        {#each agentLive as r (r.id)}
          <div class="card agent-card" role="button" tabindex="0"
               title="Двойной клик — полный стенд робота"
               ondblclick={() => openAgentRobot(r.id)}
               onkeydown={(e) => e.key === 'Enter' && openAgentRobot(r.id)}>
            <div class="card-top">
              <span class="dot" class:off={!r.deployed}
                    class:stale={r.deployed && r.heartbeatAgeSec != null && r.heartbeatAgeSec > 120}></span>
              <RobotIdentity name={r.name} id={r.id} size="row" />
              <span class="card-sym">{r.symbol}</span>
              {#if !r.deployed}<span class="stop-badge">СТОП</span>{/if}
              <span class="mode-badge" class:real={r.mode === 'real'}>{r.mode === 'real' ? 'РЕАЛ' : 'бумага'}</span>
            </div>
            <div class="agent-metrics">
              <div class="am">
                <span class="amk">P&amp;L</span>
                <span class="amv" class:pos={(r.netRub ?? 0) > 0} class:neg={(r.netRub ?? 0) < 0}>
                  {r.netRub != null ? fmtRub(r.netRub) : `${r.pnlPoints} п`}
                </span>
              </div>
              <div class="am">
                <span class="amk">Позиция</span>
                <span class="amv" class:pos={r.position > 0} class:neg={r.position < 0}>
                  {r.position > 0 ? '+' : ''}{r.position} к
                </span>
              </div>
              <div class="am">
                <span class="amk">Заявок</span>
                <span class="amv" class:warn={r.working > 6}>{r.working}</span>
              </div>
              <div class="am">
                <span class="amk">Heartbeat</span>
                <span class="amv" class:warn={r.heartbeatAgeSec != null && r.heartbeatAgeSec > 120}>
                  {r.heartbeatAgeSec != null ? `${r.heartbeatAgeSec} с` : '—'}
                </span>
              </div>
            </div>
          </div>
        {/each}
      </div>
    {/if}

    {#if liveRobots.length}
      <div class="group-band">Биржа Finam</div>
      <div class="cards">
        {#each liveRobots as r (r.id)}
          <div class="card" role="button" tabindex="0"
               title="Двойной клик — окно робота"
               ondblclick={() => onOpen(r.id)}
               onkeydown={(e) => e.key === 'Enter' && onOpen(r.id)}>
            <div class="card-top">
              <span class="dot"></span>
              <RobotIdentity name={r.name} id={r.id} size="row" />
              <span class="card-sym">{r.symbol}</span>
            </div>
            <div class="card-ping">
              <span class="cp-val" style="color:{pingColor(lastRtt)}">{fmtMs(lastRtt)}</span>
              <span class="cp-cap">пинг Finam</span>
            </div>
            <svg class="card-spark" viewBox="0 0 220 34" preserveAspectRatio="none">
              <path d={sparkPath(spark)} fill="none" stroke={pingColor(lastRtt)} stroke-width="1.5" />
            </svg>
          </div>
        {/each}
      </div>
    {/if}
  {/if}
</div>

<style>
  .live-screen { flex: 1; min-height: 0; overflow-y: auto; padding: 14px 16px; display: flex; flex-direction: column; gap: 12px; }

  .link-bar { display: flex; align-items: center; gap: 18px; background: #0a0a15; border: 1px solid #1e1e3a; border-radius: 6px; padding: 12px 16px; }
  .link-left { display: flex; align-items: baseline; gap: 10px; min-width: 240px; }
  .link-name { font-size: 12px; color: #888; text-transform: uppercase; letter-spacing: 0.5px; }
  .ping-big { font-size: 26px; font-weight: 600; font-family: monospace; }
  .ping-cap { font-size: 11px; }
  .link-mid { flex: 1; min-width: 0; }
  .spark { width: 100%; height: 34px; }
  .link-right { display: flex; gap: 14px; }
  .stat { display: flex; flex-direction: column; align-items: flex-end; }
  .stat .k { font-size: 10px; color: #555; text-transform: uppercase; }
  .stat .v { font-size: 12px; color: #bbb; font-family: monospace; }

  .method-note { font-size: 10px; color: #667; line-height: 1.5; }
  .paper-hint { font-size: 11px; color: #9ab; background: #0c1322; border: 1px solid #24406a;
    border-radius: 5px; padding: 7px 10px; line-height: 1.5; }
  .paper-hint b { color: #cde; }
  .paper-link { background: #12203a; color: #6aa8ff; border: 1px solid #24406a; border-radius: 3px;
    font-size: 11px; padding: 1px 7px; cursor: pointer; }
  .paper-link:hover { color: #cfe; border-color: #6aa8ff66; }

  .cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 12px; }
  .card { background: #0a0a15; border: 1px solid #1e1e3a; border-radius: 6px; padding: 12px; cursor: pointer; transition: border-color 0.12s, background 0.12s; }
  .card:hover { border-color: #4caf5066; background: #0d1a0d; }
  .card-top { display: flex; align-items: center; gap: 7px; margin-bottom: 8px; }
  .dot { width: 7px; height: 7px; border-radius: 50%; background: #4caf50; box-shadow: 0 0 5px #4caf5088; flex-shrink: 0; }
  .card-name { flex: 1; font-size: 13px; color: #ddd; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .card-sym { font-size: 11px; color: #4caf50; font-family: monospace; }
  .card-ping { display: flex; align-items: baseline; gap: 8px; }
  .cp-val { font-size: 22px; font-weight: 600; font-family: monospace; }
  .cp-cap { font-size: 10px; color: #667; }
  .card-spark { width: 100%; height: 34px; margin-top: 6px; }

  .empty { color: #555; font-size: 13px; padding: 24px; text-align: center; }

  /* group headers + agent-hosted robot cards */
  .group-band { font-size: 10px; color: #4a4a6a; text-transform: uppercase; letter-spacing: 0.5px; margin: 4px 2px -4px; }
  .agent-card:hover { border-color: #ffb30066; background: #1a1608; }
  .dot.stale { background: #ff9800; box-shadow: 0 0 5px #ff980088; }
  .dot.off { background: #555; box-shadow: none; }
  .stop-badge { font-size: 10px; font-weight: 700; letter-spacing: 0.5px; color: #ffb300; background: #1a1200; border: 1px solid #ff980044; border-radius: 3px; padding: 1px 6px; }
  .mode-badge { margin-left: auto; font-size: 10px; font-weight: 700; letter-spacing: 0.5px; color: #889; background: #14142a; border: 1px solid #2a2a44; border-radius: 3px; padding: 1px 6px; }
  .mode-badge.real { color: #ff5252; border-color: #ff525255; background: #1a0a0a; }
  .agent-metrics { display: grid; grid-template-columns: 1fr 1fr; gap: 6px 12px; margin-top: 10px; }
  .am { display: flex; flex-direction: column; gap: 1px; }
  .amk { font-size: 10px; color: #556; text-transform: uppercase; letter-spacing: 0.3px; }
  .amv { font-size: 15px; font-weight: 600; font-family: monospace; color: #cfd; }
  .amv.pos { color: #00e676; }
  .amv.neg { color: #ff5252; }
  .amv.warn { color: #ffb300; }
</style>
