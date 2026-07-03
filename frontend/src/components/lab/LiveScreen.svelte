<!-- LIVE tab: real-money (deployed && !paper) robots with the exchange ping + a rolling
     latency sparkline. All Finam robots share ONE gateway link, so the ping shown on each
     card is that shared Finam-link round-trip (honestly labelled). Double-click opens the
     robot window. -->
<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import { fetchWithAuth } from '../../lib/fetch-auth';
  import { fetchLatency, pingColor, pingLabel, fmtMs, type LatencyResponse } from '../../lib/latency';

  let { onOpen }: { onOpen: (id: string) => void } = $props();

  let robots = $state<any[]>([]);
  let lat = $state<LatencyResponse | null>(null);
  let loading = $state(true);

  // Real-money live robots only.
  const liveRobots = $derived(robots.filter(r => r.deployed && r.paper === false));

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

  async function loadLatency() {
    const r = await fetchLatency(360);
    if (r) lat = r;
  }

  let timer: ReturnType<typeof setInterval> | null = null;
  onMount(async () => {
    await Promise.all([loadRobots(), loadLatency()]);
    loading = false;
    // Ping refreshes on the 5s probe cadence; robot list every 6 ticks (30s).
    let n = 0;
    timer = setInterval(async () => {
      await loadLatency();
      if (++n % 6 === 0) await loadRobots();
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

  {#if loading}
    <div class="empty">Загрузка…</div>
  {:else if liveRobots.length === 0}
    <div class="empty">Нет LIVE-роботов на реальные деньги.</div>
  {:else}
    <div class="cards">
      {#each liveRobots as r (r.id)}
        <div class="card" role="button" tabindex="0"
             title="Двойной клик — окно робота"
             ondblclick={() => onOpen(r.id)}
             onkeydown={(e) => e.key === 'Enter' && onOpen(r.id)}>
          <div class="card-top">
            <span class="dot"></span>
            <span class="card-name">{r.name}</span>
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
  .stat .k { font-size: 9px; color: #555; text-transform: uppercase; }
  .stat .v { font-size: 12px; color: #bbb; font-family: monospace; }

  .method-note { font-size: 10px; color: #667; line-height: 1.5; }

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
</style>
