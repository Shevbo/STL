<!-- Качество роботов: доля выигранных кругов и recovery factor с историей.
     Заказ оператора: перспективный робот на малом объёме в хит-параде по
     прибыли не виден, а при увеличении объёма может дать много. Поэтому список
     отсортирован ПО КАЧЕСТВУ (сортировку делает сервер), а не по деньгам.
     Данные и API — окно real-trade, экран наш. Всё, что здесь легко соврать,
     разобрано в $lib/robot-quality и покрыто тестами. -->
<script lang="ts">
  import { onDestroy, onMount } from 'svelte';
  import { fetchWithAuth } from '$lib/fetch-auth';
  import { downloadCSV } from '$lib/csv';
  import ScreenTag from './lab/ScreenTag.svelte';
  import {
    PERIOD_RU, equityPoints, nowState, rfText, statCaveats, winPct,
    type Bucket, type Period, type RobotStat,
  } from '$lib/robot-quality';

  let { onClose }: { onClose?: () => void } = $props();

  let period = $state<Period>('all');
  let bucket = $state<Bucket>('day');
  let mode = $state<'real' | 'paper' | 'all'>('real');
  let rows = $state<RobotStat[]>([]);
  let blocked = $state(false);
  let loading = $state(true);
  let err = $state('');
  let openId = $state<string | null>(null);
  let fullscreen = $state(false);
  function onKeydown(e: KeyboardEvent) { if (e.key === 'Escape' && fullscreen) fullscreen = false; }

  const rub = (v: any) => (v == null || !Number.isFinite(Number(v)))
    ? '—' : (Number(v) > 0 ? '+' : '') + Math.round(Number(v)).toLocaleString('ru-RU') + ' ₽';
  const num = (v: any) => (v == null ? '—' : Number(v).toLocaleString('ru-RU'));
  const day = (ms: any) => !ms ? '—'
    : new Date(Number(ms)).toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit', year: '2-digit' });

  async function load() {
    loading = true; err = '';
    try {
      const q = `period=${period}&bucket=${bucket}&mode=${mode}`;
      const [r, sn] = await Promise.all([
        fetchWithAuth(`/api/v1/quik/robot-stats?${q}`),
        // Блокировка агента: она важнее любой цифры на карточке, и её надо
        // показать, даже когда сам робот числится работающим.
        fetchWithAuth('/api/v1/quik/status'),
      ]);
      if (!r.ok) { err = `статистика не пришла: HTTP ${r.status}`; rows = []; }
      else rows = (await r.json())?.robots ?? [];
      if (sn.ok) {
        const d = await sn.json();
        blocked = (d?.trading_block?.trading_blocked ?? d?.trading_blocked) === true;
      }
    } catch (e) {
      err = 'не удалось получить данные: ' + String(e).slice(0, 140);
    } finally {
      loading = false;
    }
  }

  let timer: ReturnType<typeof setInterval> | null = null;
  onMount(() => { load(); timer = setInterval(load, 60_000); });
  onDestroy(() => { if (timer) clearInterval(timer); });
  function set<T>(setter: (v: T) => void, v: T) { setter(v); load(); }

  /** SVG-путь накопленной кривой. Пропуск РАЗРЫВАЕТ линию: вести её через
   *  корзину без кругов значило бы обещать сделки, которых не было. */
  function eqPath(pts: Array<{ v: number | null }>, w: number, h: number,
                  mn: number, mx: number): string {
    const y = (v: number) => h - ((v - mn) / Math.max(1, mx - mn)) * (h - 6) - 3;
    const step = w / Math.max(1, pts.length - 1);
    let d = '', pen = false;
    for (const [i, p] of pts.entries()) {
      if (p.v == null) { pen = false; continue; }
      d += (pen ? ' L' : ' M') + (i * step).toFixed(1) + ' ' + y(p.v).toFixed(1);
      pen = true;
    }
    return d;
  }
</script>

<svelte:window onkeydown={onKeydown} />

<div class="rq" class:fullscreen>
  <ScreenTag id="ROBOT-QUALITY" name="качество роботов" corner="tl"
             copyText={typeof location !== 'undefined' ? location.href : '/?quality=1'} />
  <header class="rq-head">
    <h2>Качество роботов</h2>
    <div class="rq-seg" role="group" aria-label="Период">
      {#each (['day', 'week', 'month', 'all'] as Period[]) as p}
        <button type="button" class:on={period === p} onclick={() => set((v) => period = v, p)}>{PERIOD_RU[p]}</button>
      {/each}
    </div>
    <div class="rq-seg" role="group" aria-label="Режим">
      {#each (['real', 'paper', 'all'] as const) as m}
        <button type="button" class:on={mode === m} onclick={() => set((v) => mode = v, m)}>
          {m === 'real' ? 'реал' : m === 'paper' ? 'бумага' : 'все'}</button>
      {/each}
    </div>
    <div class="rq-seg sm" role="group" aria-label="Шаг графика">
      {#each (['day', 'week', 'month'] as Bucket[]) as b}
        <button type="button" class:on={bucket === b} onclick={() => set((v) => bucket = v, b)}>
          по {b === 'day' ? 'дням' : b === 'week' ? 'неделям' : 'месяцам'}</button>
      {/each}
    </div>
    {#if loading}<span class="rq-load">обновляю…</span>{/if}
    {#if rows.length}
      <button class="rq-csv" onclick={() => downloadCSV(rows, 'robot_quality')}>CSV</button>
    {/if}
    <button class="rq-full" onclick={() => fullscreen = !fullscreen}>
      {fullscreen ? '⊟ Свернуть' : '⛶ Во весь экран'}</button>
    {#if onClose}<button class="rq-close" onclick={onClose}>✕</button>{/if}
  </header>

  {#if err}<div class="rq-err">{err}</div>{/if}
  {#if blocked}
    <div class="rq-blocked">Агент блокирует торговлю: заявки отклоняются у ВСЕХ роботов,
      что бы ни показывал их статус. Цифры ниже — про прошлое.</div>
  {/if}

  <p class="rq-note">
    Список отсортирован ПО КАЧЕСТВУ (recovery factor, затем доля выигранных), а не по деньгам:
    перспективный робот на малом объёме в хит-параде по прибыли не виден. Сделка здесь — КРУГ
    от нулевой позиции до нулевой, а не отдельный филл; win rate — доля кругов с прибылью после
    комиссии; RF — результат, делённый на максимальную просадку по закрытым кругам. Методика та
    же, что в бэктесте, чтобы экраны не спорили.
  </p>

  {#if !rows.length && !loading}
    <p class="rq-empty">За «{PERIOD_RU[period]}» кругов у роботов нет.</p>
  {/if}

  {#each rows as r (r.robot_id)}
    {@const cur = nowState(r)}
    {@const cav = statCaveats(r, blocked)}
    {@const pts = equityPoints(r)}
    <article class="rq-card" class:off={cur.kind === 'off'}>
      <div class="rq-c-head">
        <b class="rq-id">{r.robot_id}</b>
        <!-- РЕЖИМ СЕЙЧАС рядом с именем, а не в подсказке: качество набрано в
             прошлом, а решение принимается про настоящее. -->
        <span class="rq-now {cur.kind}">сейчас: {cur.text}</span>
        {#if r.symbol}<span class="rq-sym">{r.symbol}</span>{/if}
        <span class="rq-sp"></span>
        <span class="rq-metric" title="доля кругов с прибылью после комиссии">
          <small>win</small>{winPct(r)}</span>
        <span class="rq-metric" title="результат / максимальная просадка по закрытым кругам">
          <small>RF</small>{rfText(r)}</span>
        <!-- Рублёвый итог стоит РЯДОМ с RF всегда: −0.60 у робота с минусом
             36 тысяч читается мягче, чем он есть. -->
        <span class="rq-net" class:pos={(r.net_rub ?? 0) > 0} class:neg={(r.net_rub ?? 0) < 0}>
          {rub(r.net_rub)}</span>
        <button class="rq-more" onclick={() => openId = openId === r.robot_id ? null : r.robot_id}>
          {openId === r.robot_id ? 'свернуть' : 'подробно'}</button>
      </div>
      <div class="rq-c-sub">
        кругов {num(r.trades)} · из них в плюс {num(r.wins)} · просадка {rub(r.max_drawdown_rub)}
        {#if r.first_ms}· с {day(r.first_ms)}{/if}{#if r.last_ms} по {day(r.last_ms)}{/if}
      </div>
      {#each cav as c}<div class="rq-warn">{c}</div>{/each}

      {#if openId === r.robot_id}
        <div class="rq-detail">
          <div class="rq-nums">
            <span>средний плюс {rub(r.avg_win_rub)}</span>
            <span>средний минус {rub(r.avg_loss_rub)}</span>
            <span>лучший {rub(r.best_rub)}</span>
            <span>худший {rub(r.worst_rub)}</span>
            <span>profit factor {r.profit_factor == null ? '—' : Number(r.profit_factor).toFixed(2)}</span>
            <span>филлов {num(r.fills)}</span>
          </div>
          {#if pts.length > 1}
            {@const vals = pts.map((p) => p.v).filter((v): v is number => v != null)}
            {@const mx = Math.max(...vals, 0)}
            {@const mn = Math.min(...vals, 0)}
            {@const H = 70}
            {@const W = Math.max(240, pts.length * 9)}
            {@const y = (v: number) => H - ((v - mn) / Math.max(1, mx - mn)) * (H - 6) - 3}
            <!-- Кривая накопленного результата по корзинам. Пропуск — РАЗРЫВ:
                 линия через пустоту обещала бы сделки, которых не было. -->
            <svg class="rq-chart" viewBox="0 0 {W} {H}" preserveAspectRatio="none" role="img"
                 aria-label="накопленный результат по {bucket === 'day' ? 'дням' : bucket === 'week' ? 'неделям' : 'месяцам'}">
              {#if mn < 0 && mx > 0}<line class="zero" x1="0" y1={y(0)} x2={W} y2={y(0)} />{/if}
              <path class="eq" d={eqPath(pts, W, H, mn, mx)} fill="none" />
            </svg>
            <div class="rq-chart-x">{pts[0].key} … {pts[pts.length - 1].key}
              · накопленный результат, ₽ · пропуск = кругов в корзине не было</div>
          {:else}
            <div class="rq-nochart">Истории для графика нет: кругов меньше двух корзин.</div>
          {/if}
        </div>
      {/if}
    </article>
  {/each}
</div>

<style>
  .rq { position: relative; height: 100%; overflow: auto; padding: 28px 16px 20px;
        color: #d7dbe8; font-size: 12px; max-width: 1180px; }
  .rq.fullscreen { position: fixed; inset: 0; z-index: 1000; width: 100vw; height: 100vh;
    max-width: none; background: #0f0f1e; }
  .rq-head { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 10px; }
  .rq-head h2 { margin: 0; font-size: 17px; color: #e8e8f0; font-weight: 600; }
  .rq-load { font-size: 10px; color: #8a90a8; }
  .rq-csv, .rq-full, .rq-close { background: none; border: 1px solid #2d2d4a; border-radius: 5px;
    color: #8a90a8; cursor: pointer; font-size: 11px; padding: 4px 10px; }
  .rq-csv:hover, .rq-full:hover, .rq-close:hover { color: #e8e8f0; border-color: #4a4a7a; }
  .rq-full { margin-left: auto; }

  .rq-seg { display: flex; border: 1px solid #2d2d4a; border-radius: 6px; overflow: hidden; }
  .rq-seg button { background: #0e0e1e; border: 0; color: #8a90a8; cursor: pointer;
    font: 600 11px/1 system-ui, sans-serif; padding: 7px 11px; }
  .rq-seg button + button { border-left: 1px solid #2d2d4a; }
  .rq-seg button.on { background: #1d3557; color: #dfe6ff; box-shadow: inset 0 -2px 0 #4f8bd6; }
  .rq-seg.sm button { padding: 5px 8px; font-size: 10px; }

  .rq-err { margin-bottom: 10px; padding: 8px 10px; border-radius: 6px;
    background: #3a1616; border: 1px solid #ff6b5a; color: #ff9d90; }
  .rq-blocked { margin-bottom: 10px; padding: 9px 11px; border-radius: 6px;
    background: #7a1414; color: #fff; font-weight: 700; }
  .rq-note { margin: 0 0 12px; padding: 9px 11px; border-radius: 6px; line-height: 1.5;
    background: #16203a; border: 1px solid #2a3c5e; color: #9aa8c4; font-size: 11px; }
  .rq-empty { color: #6f7590; }

  .rq-card { background: #12122480; border: 1px solid #23233f; border-radius: 8px;
    padding: 9px 12px; margin-bottom: 8px; }
  .rq-card.off { opacity: .8; }
  .rq-c-head { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; }
  .rq-id { font: 600 13px/1 Consolas, monospace; color: #e8e8f0; }
  .rq-now { font-size: 10px; border: 1px solid #2d2d4a; border-radius: 3px; padding: 1px 6px; }
  .rq-now.real { color: #ff9d90; border-color: #6b2a2a; }
  .rq-now.paper { color: #8a90a8; }
  .rq-now.off { color: #e0a53c; border-color: #6b5a2a; }
  .rq-sym { font: 11px/1 Consolas, monospace; color: #9aa0b4; }
  .rq-sp { flex: 1; }
  .rq-metric { font: 14px/1 Consolas, monospace; color: #dfe6ff; }
  .rq-metric small { font-size: 9px; color: #6f7590; margin-right: 4px; text-transform: uppercase; }
  .rq-net { font: 14px/1 Consolas, monospace; min-width: 96px; text-align: right; }
  .rq-net.pos { color: #7ef0a6; }
  .rq-net.neg { color: #ff9d90; }
  .rq-more { background: none; border: 1px solid #2d2d4a; border-radius: 4px; color: #8a90a8;
    cursor: pointer; font-size: 10px; padding: 2px 8px; }
  .rq-c-sub { margin-top: 3px; font-size: 11px; color: #8a90a8; }
  .rq-warn { margin-top: 6px; padding: 6px 8px; border-radius: 5px; line-height: 1.45;
    background: #2a2416; border: 1px solid #6b5a2a; color: #e0c98a; font-size: 11px; }

  .rq-detail { margin-top: 8px; padding-top: 8px; border-top: 1px solid #23233f; }
  .rq-nums { display: flex; gap: 14px; flex-wrap: wrap; font-size: 11px; color: #b9bfd4; }
  .rq-chart { display: block; width: 100%; height: 70px; margin-top: 8px; }
  .rq-chart .eq { stroke: #4fd1c5; stroke-width: 1.5; vector-effect: non-scaling-stroke; }
  .rq-chart .zero { stroke: #3a3a5a; stroke-width: 1; vector-effect: non-scaling-stroke; }
  .rq-chart-x { font-size: 10px; color: #6f7590; margin-top: 2px; }
  .rq-nochart { margin-top: 8px; font-size: 11px; color: #6f7590; }
</style>
