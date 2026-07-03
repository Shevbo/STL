<!-- LatencyPane: historical exchange-latency chart shown UNDER the price chart's time axis
     in the robot window. Self-contained: polls /api/v1/live/latency every 5s and redraws.
     Three series over MSK wall-clock time:
       outbound (blue)  = T2 - T1 - theta   (send -> server, clock-offset corrected)
       inbound  (orange)= T3 - T2 + theta   (server -> receive, corrected)
       RTT      (grey)  = T3 - T1           (round-trip; the objective, offset-free ping)
     theta is the rolling clock-offset estimate from the backend so the split stays >= 0
     without assuming NTP-synced clocks. -->
<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import { fetchLatency, pingColor, fmtMs, type LatencyResponse } from '../../lib/latency';

  let { minutes = 360 }: { minutes?: number } = $props();

  const MSK_OFFSET = 3 * 3600;   // samples are true-UTC epochs; render MSK wall clock
  const OUT_COLOR = '#42a5f5';   // outbound (blue)
  const IN_COLOR = '#ffa726';    // inbound (orange)
  const RTT_COLOR = '#90a4ae';   // round-trip (grey)

  let paneEl: HTMLDivElement;
  let chart: any = null;
  let outSeries: any = null, inSeries: any = null, rttSeries: any = null;
  let ro: ResizeObserver | null = null;
  let lat = $state<LatencyResponse | null>(null);
  let ready = false;

  const lastRtt = $derived(lat?.summary?.last_rtt_ms ?? null);

  function fmtClock(t: number): string {
    const d = new Date(t * 1000);   // t already shifted to MSK; format as UTC
    const p = (n: number) => String(n).padStart(2, '0');
    return `${p(d.getUTCHours())}:${p(d.getUTCMinutes())}`;
  }

  function draw() {
    if (!ready || !lat) return;
    const theta = lat.theta_ms || 0;
    // Dedupe by integer second (lightweight-charts needs strictly increasing unique times).
    const outPts: any[] = [], inPts: any[] = [], rttPts: any[] = [];
    let lastT = -1;
    for (const s of lat.samples) {
      if (!s.ok || s.rtt_ms == null) continue;
      const t = Math.round(s.t + MSK_OFFSET);
      if (t <= lastT) continue;
      lastT = t;
      rttPts.push({ time: t, value: Math.max(0, s.rtt_ms) });
      if (s.out_ms != null && s.in_ms != null) {
        outPts.push({ time: t, value: Math.max(0, s.out_ms - theta) });
        inPts.push({ time: t, value: Math.max(0, s.in_ms + theta) });
      }
    }
    rttSeries.setData(rttPts);
    outSeries.setData(outPts);
    inSeries.setData(inPts);
    chart.timeScale().fitContent();
  }

  async function poll() {
    const r = await fetchLatency(minutes);
    if (r) { lat = r; draw(); }
  }

  let timer: ReturnType<typeof setInterval> | null = null;
  onMount(async () => {
    const { createChart } = await import('lightweight-charts');
    chart = createChart(paneEl, {
      layout: { background: { color: 'transparent' }, textColor: '#8892a0', fontSize: 10 },
      grid: { vertLines: { color: '#161628' }, horzLines: { color: '#161628' } },
      rightPriceScale: { borderColor: '#2d2d4a', scaleMargins: { top: 0.15, bottom: 0.05 } },
      timeScale: { borderColor: '#2d2d4a', timeVisible: true, secondsVisible: false,
                   tickMarkFormatter: (t: number) => fmtClock(t) },
      localization: { timeFormatter: (t: number) => fmtClock(t), priceFormatter: (v: number) => `${Math.round(v)}` },
      crosshair: { horzLine: { visible: true }, vertLine: { visible: true } },
      handleScroll: false, handleScale: false,
      width: paneEl.clientWidth || 600, height: paneEl.clientHeight || 120,
    });
    const common = { lineWidth: 1, lastValueVisible: true, priceLineVisible: false, crosshairMarkerVisible: true };
    rttSeries = chart.addLineSeries({ ...common, color: RTT_COLOR, lineWidth: 2 });
    outSeries = chart.addLineSeries({ ...common, color: OUT_COLOR });
    inSeries = chart.addLineSeries({ ...common, color: IN_COLOR });
    ready = true;
    ro = new ResizeObserver(() => { if (paneEl) chart?.applyOptions({ width: paneEl.clientWidth, height: paneEl.clientHeight }); });
    ro.observe(paneEl);
    await poll();
    timer = setInterval(poll, 5000);
  });
  onDestroy(() => {
    if (timer) clearInterval(timer);
    if (ro) ro.disconnect();
    try { chart?.remove(); } catch { /* gone */ }
  });
</script>

<div class="lat-pane">
  <div class="lat-head">
    <span class="lat-title">Задержка до биржи</span>
    <span class="lat-now" style="color:{pingColor(lastRtt)}">{fmtMs(lastRtt)}</span>
    <span class="legend"><i style="background:{RTT_COLOR}"></i>RTT</span>
    <span class="legend"><i style="background:{OUT_COLOR}"></i>исходящая</span>
    <span class="legend"><i style="background:{IN_COLOR}"></i>входящая</span>
    <span class="lat-stat">p50 {fmtMs(lat?.summary?.rtt_p50_ms)} · p95 {fmtMs(lat?.summary?.rtt_p95_ms)}</span>
  </div>
  <div class="lat-chart" bind:this={paneEl}></div>
</div>

<style>
  .lat-pane { display: flex; flex-direction: column; height: 100%; min-height: 0; }
  .lat-head { display: flex; align-items: center; gap: 12px; padding: 3px 8px; flex-shrink: 0; }
  .lat-title { font-size: 10px; color: #667; text-transform: uppercase; letter-spacing: 0.5px; }
  .lat-now { font-size: 13px; font-weight: 600; font-family: monospace; }
  .legend { display: flex; align-items: center; gap: 4px; font-size: 10px; color: #99a; }
  .legend i { width: 9px; height: 2px; display: inline-block; border-radius: 1px; }
  .lat-stat { margin-left: auto; font-size: 10px; color: #667; font-family: monospace; }
  .lat-chart { flex: 1; min-height: 0; }
</style>
