<!-- RobotWindow.svelte
     Modal opened on double-click of a robot. One screen with:
       • параметры        — robot params + paper/real badge
       • график           — instrument candles (BacktestChart)
       • заявки на графике — order markers (executed plot; rejected/skipped in table)
       • сделки на графике — fill markers + open→close connectors (BacktestChart)
       • текущий результат — ruble PnL / position / win-rate summary + chart overlay
       • история сделок    — full live_trades table (all orders incl. rejected)
-->
<script lang="ts">
  import { onMount } from 'svelte';
  import { fetchWithAuth, errText } from '../../lib/fetch-auth';
  import RobotIdentity from './RobotIdentity.svelte';
  import ScreenTag from './ScreenTag.svelte';
  import { fmtPrice, csvPrice } from '../../lib/format';
  import { toFills, rolledPnl, annualizedPct } from '../../lib/lab-analytics';
  import BacktestChart from './BacktestChart.svelte';
  import LatencyPane from './LatencyPane.svelte';
  import Frame from './Frame.svelte';
  import RobotConsole from './RobotConsole.svelte';
  import EquityChart from './EquityChart.svelte';
  import { parseLog, appendLog, type LogKind, type LogLine } from '../../lib/robot-console';

  let { robotId, onClose }: { robotId: string; onClose: () => void } = $props();
  // ID окна для дебага (правило: у каждого окна в левом верхнем углу копируемый
  // путь). Копирует deep-link, открывающий ИМЕННО этот стенд.
  let winUrl = $derived(`${location.origin}/?lab=live&robot_win=${encodeURIComponent(robotId)}`);
  // ВМ открытой позиции — приходит из графика (у него свежая цена); правило
  // оператора 26.07: фин.рез везде = фикс + варьмаржа.
  let vmOpen = $state(0);
  // Один формат со стендом робота на агенте: те же фреймы, тот же порядок,
  // то же разворачивание на весь экран. Раньше это были два разных экрана —
  // у агентского фреймы, у бумажного свои панели с ручными сплиттерами.
  let maxId = $state<string | null>(null);

  let loading = $state(true);
  let error = $state('');
  let live = $state<any>(null);

  const EXECUTED = new Set(['paper', 'filled', 'submitted', 'executed']);
  // Candle epochs carry Moscow wall-clock stamped as UTC (ISS convention), but
  // live_trades.timestamp is real UTC. Shift fill times +3h onto the candle's
  // axis so markers/equity land on the right candle. History table keeps the
  // real timestamp (formatted to MSK separately) — this offset is chart-only.
  const MSK_OFFSET = 3 * 3600;

  // Resizable frames: chart vs bottom (vertical), left panel vs trades (horizontal).
  // Высота графика в пикселях: Frame принимает basis именно так. Тянуть
  // границы больше не нужно — рамки складываются, как на агентском стенде.
  let chartPx = $state(480);
  // ПОРТФЕЛЬНЫЙ робот (team-46/AI46): позиция это ДОЛЯ ПОРТФЕЛЯ в разных инструментах,
  // а не контракты. Бэкенд отдаёт готовую сводку в процентах (trader/lab/ai46/
  // portfolio.py) — рублёвая машинерия стенда (ГО, ₽/пункт, TP/SL, усреднения) для
  // него выключена целиком: складывать 1 контракт RI и 1 контракт ED нельзя, а
  // усреднений у стратегии нет по конструкции.
  let pf = $derived(live?.portfolio ?? null);
  // МУЛЬТИ-ИНСТРУМЕНТНЫЙ робот (team-46: 20 тикеров сразу). Стенд одно-инструментный:
  // без этого флага 12 тыс. сделок 20 инструментов валились в одну таблицу, роллы
  // выдумывались между ЖИВЫМИ инструментами, а чужие цены рисовались на чужом графике.
  let allSymbols = $derived(
    [...new Set(((live?.trades ?? []) as any[]).map((t) => t.symbol).filter(Boolean))] as string[]);
  let multi = $derived(allSymbols.length > 2);
  let histSym = $state('');                 // фильтр таблицы по инструменту ('' = все)
  const HIST_CAP = 500;                     // ponytail: рендер-потолок; всё целиком — в CSV
  let histAll = $derived(live
    ? [...(live.trades ?? [])].reverse().filter((t: any) => !histSym || t.symbol === histSym)
    : []);

  // Fills that actually changed the position (exclude rejected/skipped).
  let chartFills = $derived(
    live
      ? toFills((live.trades ?? []).filter((t: any) => EXECUTED.has(t.status)))
          .map((f: any) => ({ ...f, time: f.time + MSK_OFFSET }))
      : []
  );
  // Continuous chart across the roll: one bar-window per contract, concatenated on the
  // time axis at REAL prices (a visible step at the roll). fromTs/toTs are in chart-axis
  // time (fill epoch + MSK_OFFSET) so each contract's bars are clipped to its own span.
  // Returns null for a single-contract robot → BacktestChart does its normal one fetch.
  let segments = $derived.by(() => {
    if (multi) return null;   // сегменты = роллы; у мульти-робота их нет
    const ex = (live?.trades ?? []).filter((t: any) => EXECUTED.has(t.status) && t.symbol);
    if (!ex.length) return null;
    const order: string[] = []; const firstT: Record<string, number> = {};
    for (const t of [...ex].sort((a: any, b: any) => a.time - b.time))
      if (!(t.symbol in firstT)) { firstT[t.symbol] = t.time; order.push(t.symbol); }
    if (order.length < 2) return null;
    const isoDay = (sec: number) => new Date(sec * 1000).toISOString().slice(0, 10);
    return order.map((sym, i) => {
      const endSec = i + 1 < order.length ? firstT[order[i + 1]] : null;
      return {
        symbol: sym,
        dateFrom: isoDay(firstT[sym] - 86400),
        dateTo: endSec ? isoDay(endSec + 86400) : live.date_to,
        fromTs: i === 0 ? 0 : firstT[sym] + MSK_OFFSET,
        toTs: endSec ? endSec + MSK_OFFSET : Number.MAX_SAFE_INTEGER,
      };
    });
  });
  let pv = $derived(live?.point_value ?? 1);
  // Per-contract point values for a rolled robot; fall back to the single value.
  let pvMap = $derived(
    live?.point_values && Object.keys(live.point_values).length
      ? live.point_values : (live?.point_value ?? 1)
  );

  // Param key → schema entry (label, hint, desc) for the (i) popovers.
  let schemaByKey = $derived.by(() => {
    const m: Record<string, any> = {};
    for (const p of (live?.strategy?.params_schema ?? [])) m[p.key] = p;
    return m;
  });
  let openInfo = $state<string | null>(null);
  let hoverInfo = $state<string | null>(null);
  // Roll-aware analytics: P&L summed PER CONTRACT (RIM6 + RIU6 separately), never
  // cross-paired (that invents phantom profit). Single source of truth for money,
  // lifecycle, equity, and the summary.
  // Комиссия — ТЕЙКЕР, как в бэктесте, у раннера (taker_points) и в журнале
  // algo_trades. Мейкерская модель здесь осталась от человеческого лимитного
  // движка и врала в 5 раз: у бумажного Williams %R (GZU6) она показала
  // 1 141 ₽ вместо 5 957 ₽ на 2 535 филлах, и робот выглядел как +2 715 ₽ там,
  // где на самом деле −2 101 ₽ (02.08.2026, оператор так и решил, что он
  // перспективный). Робот, отправленный на агент, кроссит спред — значит платит
  // биржевую часть, и показывать надо её.
  // multi: settleCarried=false — «не текущие» инструменты у мульти-робота ЖИВЫЕ,
  // принудительный ролл-досчёт выдумывал их закрытия по последней цене.
  let rolled = $derived(rolledPnl(chartFills, pvMap, true,
    { bucketSecs: 60, settleCarried: !multi }));
  let events = $derived(rolled.events);
  let closes = $derived(events.filter(e => e.close).map(e => e.close!));

  // Map each executed fill back to its lifecycle event by its UNIQUE order_id. A value
  // key (time+side+qty+price) collides when one signal both closes and reopens at the
  // same instant/price (a reverse recorded as two identical fills) — the close (SL/TP)
  // would be dropped and the row mislabelled "вход" with no P&L. order_id is unique.
  let eventById = $derived.by(() => {
    const m = new Map<string, (typeof events)[number]>();
    for (const e of events) if (e.id != null) m.set(String(e.id), e);
    return m;
  });
  function tradeEvent(t: any) {
    if (t.status === 'rejected' || t.status === 'skipped') return null;
    return (t.order_id != null ? eventById.get(String(t.order_id)) : null) ?? null;
  }
  // Type badge: TP/SL (closing fill, profit/loss), AVG (averaging), вход (open),
  // реверс (flip-through-zero open leg without its own close), — for rejected.
  function tradeTypeLabel(ev: any): { text: string; cls: string } {
    if (!ev) return { text: '—', cls: 'tt-none' };
    if (ev.close) return { text: ev.close.exit, cls: ev.close.exit === 'TP' ? 'tt-tp' : 'tt-sl' };
    if (ev.kind === 'average') return { text: 'AVG', cls: 'tt-avg' };
    if (ev.kind === 'enforce') return { text: 'ENF', cls: 'tt-enf' };
    if (ev.kind === 'reverse') return { text: 'реверс', cls: 'tt-rev' };
    return { text: 'вход', cls: 'tt-open' };
  }
  // Портфельный робот: роль филла приходит с бэкенда (она известна ТОЧНО, стратегия
  // сама её пишет), а не угадывается пересчётом позиции. Выход у неё только по
  // времени удержания — никаких TP/SL, их у стратегии нет.
  const PF_KIND: Record<string, { text: string; cls: string }> = {
    open: { text: 'вход', cls: 'tt-open' },
    close_soft: { text: 'выход · время', cls: 'tt-enf' },
    close_hard: { text: 'выход · принуд.', cls: 'tt-rev' },
    close_take: { text: 'тейк', cls: 'tt-tp' },
    close_stop: { text: 'стоп', cls: 'tt-sl' },
    close: { text: 'выход', cls: 'tt-enf' },
  };
  const pfTypeLabel = (t: any) => PF_KIND[t?.kind] ?? { text: t?.kind ?? '—', cls: 'tt-none' };
  const fmtPct = (v: number | null | undefined, d = 2) =>
    v == null ? '—' : (v >= 0 ? '+' : '') + Number(v).toFixed(d) + '%';

  // Cumulative realized P&L curve in rubles (NET of commission), starting at 0 —
  // there is no start capital; the curve is running profit/loss, not account equity.
  let equityCurve = $derived.by(() => {
    if (!chartFills.length) return [];
    const pts: any[] = [{ time: chartFills[0].time, equity: 0 }];
    let cum = 0;
    for (const e of events) {
      if (e.close) { cum += e.close.pnl; pts.push({ time: e.rawTime, equity: cum }); }
    }
    return pts;
  });

  // ── Системный монитор ───────────────────────────────────────────────────────
  // Та же лента, что на агентском стенде: что оператор отправил роботу и что у
  // робота менялось. Логика ленты общая (lib/robot-console), разметка общая
  // (RobotConsole) — иначе стенды снова разъедутся, как до 05.08.2026.
  const logKey = () => `rw_mon_${robotId}`;
  const LOG_MAX = 1000;
  let logLines = $state<LogLine[]>(
    (() => { try { return parseLog(localStorage.getItem(`rw_mon_${robotId}`), 1000); }
             catch { return []; } })());
  function pushLog(text: string, kind: LogKind = 'ok') {
    logLines = appendLog(logLines, { t: Date.now(), kind, text }, LOG_MAX);
    try { localStorage.setItem(logKey(), JSON.stringify(logLines)); } catch { /* приватный режим */ }
  }
  function clearLog() {
    logLines = [];
    try { localStorage.removeItem(logKey()); } catch { /* приватный режим */ }
  }
  async function copyLog() {
    const t = logLines.map((l) => `${new Date(l.t).toLocaleTimeString('ru-RU')}  ${l.text}`).join('\n');
    try { await navigator.clipboard.writeText(t); pushLog('Лог скопирован в буфер.', 'sys'); }
    catch { pushLog('Буфер обмена недоступен (нужен https или разрешение).', 'err'); }
  }

  // Ссылка «История прогонов»: тот же дип-линк, что на агентском стенде.
  let histHref = $derived(
    `/?lab=backtest&hist=${encodeURIComponent(
      [live?.strategy?.id ?? '', live?.symbol ?? ''].filter(Boolean).join(' '))}`);

  // Отчёт для кривой доходности. Собираем ИЗ СДЕЛОК РОБОТА: журнал algo_trades
  // знает только агентских роботов, у бумажного там нет ни строки.
  let eqReport = $derived.by(() => {
    const pts: { ts_ms: number; net: number; cum_net: number }[] = [];
    let cum = 0;
    for (const e of events) {
      if (!e.close) continue;
      cum += e.close.pnl;
      pts.push({ ts_ms: e.rawTime * 1000, net: e.close.pnl, cum_net: Math.round(cum * 100) / 100 });
    }
    if (!pts.length) return null;
    const byDay = new Map<string, number>();
    for (const p of pts) {
      const d = new Date(p.ts_ms).toISOString().slice(0, 10);
      byDay.set(d, (byDay.get(d) || 0) + p.net);
    }
    return {
      days: [...byDay.entries()].sort().map(([date, net]) => ({ date, net })),
      series_fills: { [robotId]: pts },
      robots: [{ robot_id: robotId, name: live?.robot?.name ?? robotId }],
    };
  });

  // Synthetic "result" so BacktestChart renders candles + markers + connectors + equity.
  // trades = ALL fills (every contract); the chart shows the whole history across the
  // roll, and BacktestChart computes the same roll-aware per-contract analytics.
  let chartResult = $derived(
    live ? {
      // multi: на графике ТОЛЬКО сделки инструмента графика — маркер по цене нефти
      // на графике Si висит в космосе и ломает шкалу.
      trades: multi ? chartFills.filter((f: any) => f.symbol === (live.chart_symbol ?? live.symbol)) : chartFills,
      // У портфельного робота рублёвой кривой не существует: доход считается в % от
      // портфеля, а филлы одного тикера — лишь его кусок. Рисовать «₽» по ним нельзя.
      equity_curve: pf ? [] : equityCurve, params: live.robot?.params_json ?? {},
    } : null
  );

  // Current result summary (rubles, net of commission) — all roll-aware.
  let summary = $derived.by(() => {
    const wins = closes.filter(c => c.pnl > 0).length;
    // A closing fill that SELLS closed a long; one that BUYS closed a short.
    const longRT = events.filter(e => e.close && e.side === 'sell').length;
    const shortRT = events.filter(e => e.close && e.side === 'buy').length;
    // ГО (margin at risk) = current contract's per-contract margin × peak contracts
    // held on a single contract (never the doubled 9+9). Return is on THIS.
    const go = (live?.initial_margins?.[rolled.currentSymbol] ?? live?.initial_margin ?? 0) * rolled.peakContracts;
    return {
      net: rolled.net,
      go,
      retPct: go > 0 ? (rolled.net / go) * 100 : 0,
      position: rolled.position,
      roundTrips: rolled.closes,
      longRT,
      shortRT,
      winRate: closes.length ? (wins / closes.length) * 100 : 0,
      orders: (live?.trades ?? []).length,
    };
  });

  // % от ГО тоже по правилу фикс+ВМ, не по голому фиксу.
  let totalPct = $derived(summary.go > 0 ? ((summary.net + vmOpen) / summary.go) * 100 : 0);
  // Дата начала торговли = первый филл; «доходность в год» — от максимального
  // задействованного ГО, линейно приведённая к году.
  let startedMs = $derived(chartFills.length ? (chartFills[0].time - MSK_OFFSET) * 1000 : 0);
  let annPct = $derived(annualizedPct(summary.net + vmOpen, summary.go, startedMs));

  const fmtMoney = (v: number) =>
    (v >= 0 ? '+' : '') + v.toLocaleString('ru-RU', { maximumFractionDigits: 0 });
  const fmtTime = (iso: string) =>
    new Date(iso).toLocaleString('ru-RU', {
      timeZone: 'Europe/Moscow', day: '2-digit', month: '2-digit',
      hour: '2-digit', minute: '2-digit', second: '2-digit',
    });

  // Export the full history (chronological) to CSV so the P&L can be audited offline.
  // ';'-separated + UTF-8 BOM = opens cleanly in Excel (RU locale). The Фин. рез column
  // sums to the robot's net realized P&L (only TP/SL/реверс rows carry a value).
  function downloadCsv() {
    const head = pf
      ? ['Время (МСК)', 'Тип', 'Сторона', 'Вес портфеля (%)', 'Цена', 'Инструмент',
         'Доходность сделки (%)', 'Вклад в портфель (%)', 'Статус', 'order_id']
      : ['Время (МСК)', 'Тип', 'Сторона', 'Кол-во', 'Цена', 'Контракт', 'Фин. рез (₽)', 'Статус', 'order_id'];
    const q = (v: any) => `"${String(v ?? '').replace(/"/g, '""')}"`;
    const lines = [head.map(q).join(';')];
    for (const t of (live?.trades ?? [])) {            // API order = chronological ascending
      const ev = pf ? null : tradeEvent(t);
      lines.push((pf
        ? [fmtTime(t.iso), pfTypeLabel(t).text, t.side,
           t.size_pct != null ? (t.size_pct * 100).toFixed(3) : '', csvPrice(t.price), t.symbol ?? '',
           t.ret_pct != null ? t.ret_pct.toFixed(4) : '', t.port_pct != null ? t.port_pct.toFixed(5) : '',
           t.status, t.order_id ?? '']
        : [fmtTime(t.iso), tradeTypeLabel(ev).text, t.side, t.qty, csvPrice(t.price),
           t.symbol ?? '', ev?.close ? Math.round(ev.close.pnl) : '', t.status, t.order_id ?? '']
      ).map(q).join(';'));
    }
    const blob = new Blob(['﻿' + lines.join('\r\n')], { type: 'text/csv;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${(live?.robot?.name ?? 'robot').replace(/[^\w\-]+/g, '_')}_trades.csv`;
    a.click();
    URL.revokeObjectURL(url);
  }

  async function load(silent = false) {
    if (!silent) loading = true;
    if (!silent) error = '';
    try {
      const res = await fetchWithAuth(`/api/v1/robots/${robotId}/live`);
      if (!res.ok) throw new Error(await errText(res));
      live = await res.json();          // reassign → chart + tables + stats re-derive
      error = '';
    } catch (e) { if (!silent || !live) error = String(e); }
    if (!silent) loading = false;
  }

  // Deploy this robot's strategy+params ONTO THE AGENT as PAPER (top "QUIK/АГЕНТ"
  // table) — real-ready: it can later be armed to real from the VDS console. This is
  // NOT the STL-only sim (bottom table); the agent runner executes it against QUIK.
  // strategy_id comes from the matched template (live.strategy.id); deploy-agent needs
  // no script_code. New robot_id must be colon-free (order attribution parses on ':').
  let cloneBusy = $state(false);
  let cloneMsg = $state('');
  async function cloneToPaper() {
    const sid = live?.strategy?.id;
    if (!sid) { cloneMsg = 'Не удалось определить strategy_id — на агент разворачиваются только библиотечные/standalone стратегии.'; return; }
    const p = live?.robot?.params_json ?? {};
    const maxPos = Math.max(1, Number(p.avg_max ?? p.qty ?? live?.robot?.max_position ?? 1) || 1);
    if (!window.confirm(`Развернуть «${live.robot?.name}» НА АГЕНТ в PAPER (верхняя таблица)?\n` +
      `Стратегия: ${sid} · ${live.symbol}. Робот появится в «РОБОТЫ НА БИРЖЕ QUIK».\n` +
      `Реал включается ТОЛЬКО с консоли VDS (FLAT + ввод ID) — из GUI не армится.`)) return;
    const newId = `${sid}-${live.symbol}-p${Date.now().toString(36)}`.replace(/[^A-Za-z0-9_-]/g, '');
    cloneBusy = true; cloneMsg = '';
    try {
      const d = await fetchWithAuth(`/api/v1/quik/robots/${encodeURIComponent(newId)}/deploy-agent`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ strategy_id: sid, params: { ...p, symbol: live.symbol }, symbol: live.symbol,
          schedule: live.robot?.schedule ?? '09:00-23:55', max_position: maxPos, paper: true }),
      });
      if (!d.ok) throw new Error(await errText(d));
      cloneMsg = `Развёрнут на агенте в PAPER: «${newId}» (верхняя таблица). Реал — с консоли VDS.`;
    } catch (e) { cloneMsg = `Ошибка деплоя на агент: ${String(e).slice(0, 90)}`; }
    finally { cloneBusy = false; }
  }

  function onKey(e: KeyboardEvent) { if (e.key === 'Escape') onClose(); }
  // Live refresh: while the robot is deployed, silently re-pull its state so the screen
  // "moves" (new candles / fills / position / equity) without a manual reload.
  let pollTimer: ReturnType<typeof setInterval> | null = null;
  onMount(() => {
    load();
    // !live = первая загрузка не удалась (бэкенд перезапускался): опрос должен
    // ПРОДОЛЖАТЬСЯ, иначе 30-секундный рестарт навсегда оставлял открытый стенд
    // с ошибкой, пока оператор не перезагрузит страницу вручную.
    pollTimer = setInterval(() => { if (!live || live.robot?.deployed) load(true); }, 15000);
    return () => { if (pollTimer) clearInterval(pollTimer); };
  });
</script>

<svelte:window onkeydown={onKey} />

<div class="overlay" role="presentation" onclick={onClose} onkeydown={(e) => e.key === 'Escape' && onClose()}>
  <!-- stop propagation so clicks inside the window don't close it -->
  <div class="window" role="dialog" aria-modal="true" tabindex="-1"
       onclick={(e) => e.stopPropagation()} onkeydown={(e) => e.stopPropagation()}>

    <div class="win-header">
      <ScreenTag inline id="ROBOT-WIN" name="стенд робота" copyText={winUrl} />
      <span class="win-icon">🤖</span>
      <RobotIdentity name={live?.robot?.name ?? robotId} id={robotId} size="title" />
      {#if live}
        <!-- Шапка ОДНОГО вида со стендом робота на агенте: опознание и режим здесь,
             состояние и цифры — во фрейме «Информация и статус» ниже. Раньше тут
             стояла своя россыпь бейджей, и два стенда выглядели разными экранами. -->
        <span class="hd-mode" class:real={!live.paper}>{live.paper ? 'PAPER' : 'РЕАЛ'}</span>
        <!-- У портфельного робота одного инструмента нет: показывать live.symbol
             (первый тикер из params) значило врать про 30+ торгуемых. -->
        <span class="hd-sym" title={pf ? allSymbols.join(' ') : ''}>
          {pf ? `портфель · ${pf.instruments} инстр.` : live.symbol}</span>
      {/if}
      <button class="close" onclick={onClose} title="Закрыть (Esc)">✕</button>
    </div>

    {#if loading}
      <div class="state">Загрузка…</div>
    {:else if error}
      <div class="state err">{error}</div>
    {:else if live}
      <div class="win-body">
        <!-- Порядок панелей ОДИН В ОДИН со стендом робота на агенте: полоса
             «статус + действия», график, задержка, внизу логика и сделки. -->
        <div class="ars-strip">
          <Frame fid="rw-state" title="Информация и статус" bind:maxId>
            <div class="fpad">
            {#if pf}
              <!-- Портфельная стратегия: считаем ДОХОДНОСТЬ, а не рубли. Капитала у
                   бумажного портфеля не задано, любая рублёвая цифра была бы вымыслом. -->
              <div class="panel-title res-title">Текущий результат (портфель)</div>
              <div class="result-grid">
                <div class="r-row"><span>Сумма доходностей сделок</span>
                  <b class:pos={pf.ret_sum_pct > 0} class:neg={pf.ret_sum_pct < 0}
                     title="равный вес в каждой сделке — не зависит от размера позиции">{fmtPct(pf.ret_sum_pct)}</b>
                  <span class="sub">ср. {fmtPct(pf.ret_avg_pct, 3)}</span></div>
                <div class="r-row"><span>С учётом веса позиции</span>
                  <b class:pos={(pf.port_pct ?? 0) > 0} class:neg={(pf.port_pct ?? 0) < 0}
                     title="вклад в портфель: доходность × доля портфеля в позиции">{fmtPct(pf.port_pct)}</b>
                  <span class="sub">{pf.weighted_closes} из {pf.closes} сд.</span></div>
                <div class="r-row"><span>Доходность в год</span>
                  <b class:pos={(pf.ann_pct ?? 0) > 0} class:neg={(pf.ann_pct ?? 0) < 0}>{fmtPct(pf.ann_pct, 1)}</b>
                  <span class="sub">{pf.days >= 1 ? pf.days.toFixed(0) + ' дн.' : '—'}</span></div>
                <div class="r-row"><span>Сделок (круг)</span><b>{pf.closes}</b>
                  <span class="sub">win {pf.win_rate.toFixed(0)}%</span></div>
                <div class="r-row"><span>Инструментов</span><b>{pf.instruments}</b>
                  <span class="sub">{pf.fills} филлов</span></div>
                <div class="r-row"><span>Открыто сейчас</span><b>{pf.open_now.length}</b>
                  <span class="sub">{pf.open_now.map((o: any) => o.symbol).join(' ') || '—'}</span></div>
                {#if pf.orphans || pf.lost}
                  <div class="r-row"><span>Позиций потеряно</span><b class="neg">{pf.lost + pf.orphans}</b>
                    <span class="sub">рестарты сервиса</span></div>
                {/if}
              </div>
              {#if pf.weighted_closes < pf.closes}
                <div class="basis">
                  Вес позиции записывается с 31.07.2026. По более ранним {pf.closes - pf.weighted_closes} сделкам
                  известна только доходность инструмента, вклад в портфель не восстановим.
                </div>
              {/if}
              {#if pf.by_symbol.length}
                <div class="panel-title res-title">По инструментам</div>
                <div class="pf-syms">
                  {#each pf.by_symbol as s}
                    <div class="pf-sym">
                      <span class="k">{s.symbol}</span>
                      <span class="sub">{s.closes} сд · win {s.closes ? Math.round(s.wins / s.closes * 100) : 0}%</span>
                      <b class:pos={s.ret_sum_pct > 0} class:neg={s.ret_sum_pct < 0}>{fmtPct(s.ret_sum_pct)}</b>
                    </div>
                  {/each}
                </div>
              {/if}
            {:else}
            <div class="panel-title res-title">Текущий результат</div>
            <div class="result-grid">
              <div class="r-row"><span>Доход (фикс + ВМ)</span>
                <b class:pos={summary.net + vmOpen > 0} class:neg={summary.net + vmOpen < 0}>{fmtMoney(summary.net + vmOpen)} ₽</b>
                <span class="sub">({totalPct >= 0 ? '+' : ''}{totalPct.toFixed(2)}% от ГО)</span></div>
              {#if vmOpen !== 0}
                <div class="r-row"><span>из них: фикс / ВМ откр. поз.</span>
                  <b class:pos={summary.net > 0} class:neg={summary.net < 0}>{fmtMoney(summary.net)}</b>
                  <span class="sub"><b class:pos={vmOpen > 0} class:neg={vmOpen < 0}>{fmtMoney(vmOpen)}</b> ₽</span></div>
              {/if}
              <div class="r-row"><span>Доходность в год</span>
                <b class:pos={(annPct ?? 0) > 0} class:neg={(annPct ?? 0) < 0}
                   title="(фикс + ВМ) к максимальному задействованному ГО, линейно приведено к году">
                  {annPct == null ? '—' : (annPct > 0 ? '+' : '') + annPct.toFixed(1) + '%'}</b>
                {#if annPct == null}<span class="sub">нужно ≥3 дней торговли</span>
                {:else}<span class="sub">с {new Date(startedMs).toLocaleDateString('ru-RU')}</span>{/if}</div>
              <div class="r-row"><span>ГО (макс. задейств.)</span>
                <b>{summary.go > 0 ? Math.round(summary.go).toLocaleString('ru-RU') + ' ₽' : '—'}</b></div>
              <div class="r-row"><span>Позиция сейчас</span>
                <b class:pos={summary.position > 0} class:neg={summary.position < 0}>
                  {summary.position > 0 ? '+' : ''}{summary.position} конт.</b></div>
              <div class="r-row"><span>Сделок (круг)</span><b>{summary.roundTrips}</b>
                <span class="sub">L {summary.longRT} / S {summary.shortRT}</span></div>
              <div class="r-row"><span>Win rate</span><b>{summary.winRate.toFixed(0)}%</b></div>
              <div class="r-row"><span>Всего заявок</span><b>{summary.orders}</b></div>
            </div>
            <div class="basis">P&L % — доход относительно макс. задействованного ГО (ГО/контракт × пик контрактов)</div>
            {/if}
            </div>
          </Frame>
          <Frame fid="rw-acts" title="Доступные действия" bind:maxId>
            <div class="fpad acts">
        <!-- Портфельную стратегию на агент не развернуть: раннер исполняет контрактные
             on_bar-стратегии, а team-46 это доли портфеля в десятках инструментов. -->
        {#if !pf}
          <button class="rw-launch" disabled={cloneBusy} onclick={cloneToPaper}
                  title="развернуть НА АГЕНТ в PAPER (верхняя таблица) — real-ready, армится с консоли VDS">
            {cloneBusy ? '…' : '▶ На агент в торговлю (paper)'}</button>
          {#if cloneMsg}<span class="rw-launch-msg">{cloneMsg}</span>{/if}
        {/if}
        <a class="rw-hist" href={histHref} target="_blank" rel="noopener"
           title="все сохранённые прогоны перебора параметров этой стратегии">История прогонов</a>
            </div>
          </Frame>

          <Frame fid="rw-mon" title="Системный монитор" bind:maxId>
            {#snippet head()}
              <button class="mon-hb" onclick={copyLog} title="скопировать лог в буфер">копировать</button>
              <button class="mon-hb" onclick={clearLog} title="очистить лог этого робота">очистить</button>
            {/snippet}
            <!-- Та же консоль, что на агентском стенде (RobotConsole). Строки ввода нет:
                 LLM-напарник знает только агентских роботов, рисовать неработающий
                 промпт нельзя. -->
            <RobotConsole lines={logLines}
                          headline="SHECTORY TRADE & LAB · PAPER ROBOT CONSOLE"
                          subline={`ROBOT ${String(robotId).toUpperCase()} · PAPER · READY`} />
          </Frame>
        </div>

        <Frame fid="rw-chart" title="График + доходность" bind:maxId basis={chartPx}>
          <div class="rw-chart-body">
          <BacktestChart
            result={chartResult}
            symbol={live.chart_symbol ?? live.symbol}
            dateFrom={live.date_from}
            dateTo={live.date_to}
            pointValue={pv}
            pointValues={live.point_values ?? null}
            segments={segments}
            defaultInterval={5}
            taker={true}
            openOrders={live.open_orders ?? []}
            plannedOrders={live.planned_orders ?? []}
            hideStats={!!pf}
            onVm={(v) => (vmOpen = pf ? 0 : v)}
          />
          </div>
        </Frame>

        <Frame fid="rw-ping" title="Задержка до биржи" bind:maxId basis={140}>
          <!-- История задержки до биржи, под осью времени графика. -->
          <div class="lat-wrap">
            <LatencyPane minutes={360} />
          </div>
        </Frame>

        <div class="bottom-row">
          <Frame fid="rw-logic" title="Логика стратегии" bind:maxId>
            <div class="fpad">
            {#if live.strategy}
              <div class="panel-title">О стратегии</div>
              <div class="about-box">
                <div class="about-name">{live.strategy.name}</div>
                {#if live.strategy.description}<div class="about-desc">{live.strategy.description}</div>{/if}
                {#if live.strategy.source}
                  <a class="about-link" href={live.strategy.source} target="_blank" rel="noopener">Подробное описание робота ↗</a>
                {/if}
              </div>
            {/if}

            <div class="panel-title">Параметры</div>
            <div class="kv-grid">
              {#each Object.entries(live.robot?.params_json ?? {}) as [k, v]}
                {@const sp = schemaByKey[k]}
                <div class="kv">
                  <span class="k">
                    {sp?.label ?? k}
                    {#if sp?.desc || sp?.hint}
                      <span class="kv-i" role="button" tabindex="0" aria-label="Описание"
                        onclick={() => openInfo = openInfo === k ? null : k}
                        onkeydown={(e) => (e.key === 'Enter' || e.key === ' ') && (openInfo = openInfo === k ? null : k)}
                        onmouseenter={() => hoverInfo = k} onmouseleave={() => hoverInfo = null}
                      >ⓘ</span>
                    {/if}
                    {#if (sp?.desc || sp?.hint) && (openInfo === k || hoverInfo === k)}
                      <div class="kv-popover">
                        <div class="pp-title">{sp.label ?? k}</div>
                        <div class="pp-body">{sp.desc || sp.hint}</div>
                      </div>
                    {/if}
                  </span>
                  <span class="v">{v}</span>
                </div>
              {/each}
              {#if !pf}
                <div class="kv"><span class="k">ГО / контракт</span>
                  <span class="v">{live.initial_margin != null ? Math.round(live.initial_margin).toLocaleString('ru-RU') + ' ₽' : '—'}</span></div>
                <div class="kv"><span class="k">пункт = ₽</span><span class="v">{pv}</span></div>
                <div class="kv"><span class="k">комиссия</span><span class="v">4 ₽ / заявка (тейкер)</span></div>
              {/if}
            </div>
            </div>
          </Frame>
          <Frame fid="rw-equity" title="Доходность (журнал)" bind:maxId>
            <!-- Кривая строится из СДЕЛОК САМОГО РОБОТА, а не из журнала algo_trades:
                 в журнал попадают только агентские роботы, у бумажного там нет ни
                 строки, и график был бы пустым. -->
            {#if eqReport}<EquityChart externalReport={eqReport} compact />
            {:else}<div class="fpad empty">Закрытых сделок ещё нет — кривую строить не из чего.</div>{/if}
          </Frame>
          <Frame fid="rw-trades" title={`Сделки робота (${histAll.length})`} bind:maxId>
            <div class="panel-title hist-head">
              <span>История сделок ({histAll.length}{histSym ? ` · ${histSym}` : ''})</span>
              {#if multi}
                <select class="hist-sym" bind:value={histSym} title="Фильтр по инструменту (робот торгует несколько)">
                  <option value="">все инстр. ({allSymbols.length})</option>
                  {#each allSymbols as s}<option value={s}>{s}</option>{/each}
                </select>
              {/if}
              {#if (live.trades ?? []).length}
                <button class="csv-btn" onclick={downloadCsv} title="Выгрузить таблицу сделок в CSV (для сверки P&L)">⭳ CSV</button>
              {/if}
            </div>
            <div class="history-scroll">
              {#if (live.trades ?? []).length === 0}
                <div class="empty">Сделок пока нет. Робот ждёт сигнала.</div>
              {:else}
                <table>
                  <thead>
                    <tr><th>Время (МСК)</th><th>Тип</th><th>Сторона</th><th>{pf ? 'Вес' : 'Кол-во'}</th><th>Цена</th>{#if multi}<th>Инстр.</th>{/if}<th class="num">{pf ? 'Доходн.' : 'Фин. рез'}</th><th>Статус</th><th>ID</th></tr>
                  </thead>
                  <tbody>
                    {#each histAll.slice(0, HIST_CAP) as t}
                      <!-- pf: контрактный пересчёт (rolledPnl по 14 тыс. филлов) не
                           трогаем вовсе — он и не нужен, и стоит CPU на каждом опросе -->
                      {@const ev = pf ? null : tradeEvent(t)}
                      {@const tt = pf ? pfTypeLabel(t) : tradeTypeLabel(ev)}
                      <tr class:rejected={t.status === 'rejected' || t.status === 'skipped'}>
                        <td class="mono">{fmtTime(t.iso)}</td>
                        <td><span class="tt-badge {tt.cls}">{tt.text}</span></td>
                        <td class:buy={t.side === 'buy'} class:sell={t.side === 'sell'}>
                          {t.side === 'buy' ? '▲ buy' : '▼ sell'}</td>
                        <td class="mono">{pf ? (t.size_pct != null ? (t.size_pct * 100).toFixed(2) + '%' : '—') : t.qty}</td>
                        <td class="mono">{fmtPrice(t.price)}</td>
                        {#if multi}<td class="mono">{t.symbol ?? '—'}</td>{/if}
                        {#if pf}
                          <td class="num mono" class:pos={t.ret_pct > 0} class:neg={t.ret_pct < 0}
                              title={t.port_pct != null ? `вклад в портфель ${fmtPct(t.port_pct, 3)}` : 'вес позиции не записан'}>
                            {t.ret_pct == null ? '—' : fmtPct(t.ret_pct)}</td>
                        {:else}
                        <td class="num mono" class:pos={ev?.close && ev.close.pnl > 0} class:neg={ev?.close && ev.close.pnl < 0}>
                          {ev?.close ? fmtMoney(ev.close.pnl) + ' ₽' : '—'}</td>
                        {/if}
                        <td><span class="st-badge st-{t.status}">{t.status}</span></td>
                        <td class="mono id-cell" title={t.order_id ?? ''}>{t.order_id ?? '—'}</td>
                      </tr>
                    {/each}
                  </tbody>
                </table>
                {#if histAll.length > HIST_CAP}
                  <div class="empty">Показаны последние {HIST_CAP} из {histAll.length} — полная история в CSV.</div>
                {/if}
              {/if}
            </div>
          </Frame>
        </div>
      </div>
    {/if}
  </div>
</div>

<style>
  .overlay {
    position: fixed; inset: 0; z-index: 2000;
    background: #000000cc; display: flex; align-items: center; justify-content: center;
    backdrop-filter: blur(2px);
  }
  .window {
    width: 92vw; height: 90vh; background: #0a0a15;
    border: 1px solid #2d2d4a; border-radius: 8px;
    display: flex; flex-direction: column; overflow: hidden;
    box-shadow: 0 12px 48px #000000aa;
  }

  .win-header {
    display: flex; align-items: center; gap: 8px;
    padding: 8px 14px; background: #12121f; border-bottom: 1px solid #2d2d4a; flex-shrink: 0;
  }
  .win-icon { font-size: 15px; }
  .win-name { font-size: 14px; color: #4caf50; font-weight: 600; }
  .badge {
    font-size: 10px; padding: 2px 7px; border-radius: 3px;
    background: #1a3a1a; color: #4caf50; border: 1px solid #4caf5066;
  }
  .badge.real { background: #3a1010; color: #ff6b6b; border-color: #ff6b6b66; }
  .badge.sym { background: #1a1a2e; color: #6aa8ff; border-color: #6aa8ff44; }
  .badge.dim { background: #14141f; color: #777; border-color: #2d2d4a; }
  .rw-launch { margin-left: 8px; padding: 3px 10px; background: linear-gradient(180deg,#1c6b3a,#14522c);
    border: 1px solid #2c8a4e; color: #d6ffe4; border-radius: 4px; font-size: 11px; font-weight: 700;
    cursor: pointer; white-space: nowrap; }
  .rw-launch:hover { background: linear-gradient(180deg,#237e46,#186334); }
  .rw-launch:disabled { opacity: .6; cursor: default; }
  /* Кнопки шапки монитора и ссылка истории — те же, что на агентском стенде. */
  .mon-hb { background: none; border: 1px solid #1c4a2a; color: #2f9c50; cursor: pointer;
    font-size: 9px; text-transform: uppercase; letter-spacing: .06em; padding: 0 5px;
    border-radius: 2px; }
  .mon-hb:hover { color: #7fdba0; border-color: #2f9c50; }
  .rw-hist { display: inline-block; margin-left: 8px; font-size: 11px; color: #7aa2d0;
    border: 1px solid #24406a; border-radius: 3px; padding: 3px 9px; text-decoration: none; }
  .rw-hist:hover { color: #cfe2ff; border-color: #3a6aa8; }
  .rw-launch-msg { font-size: 11px; color: #9fd8b0; }
  .close {
    margin-left: auto; background: none; border: none; color: #888;
    font-size: 16px; cursor: pointer; padding: 0 4px;
  }
  .close:hover { color: #f44336; }

  .state { flex: 1; display: flex; align-items: center; justify-content: center; color: #666; font-size: 13px; }
  .state.err { color: #f4433699; padding: 20px; text-align: center; }

  .win-body { flex: 1; display: flex; flex-direction: column; min-height: 0; }
  .chart-wrap { flex: 0 0 62%; min-height: 0; }
  .lat-wrap { flex: 0 0 134px; min-height: 0; border-top: 1px solid #1a1a2e; }

  .win-bottom { flex: 1 1 0; display: flex; min-height: 0; }

  /* Resize handles */
  .panel { display: flex; flex-direction: column; min-height: 0; overflow: hidden; }
  .panel.left { flex: 0 0 320px; border-right: 1px solid #1a1a2e; padding: 10px 12px; overflow-y: auto; }
  .panel.right { flex: 1; min-width: 0; }

  .panel-title {
    font-size: 10px; color: #555; text-transform: uppercase; letter-spacing: 0.5px;
    margin-bottom: 6px;
  }
  .hist-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; padding-right: 10px; }
  .csv-btn {
    font-size: 10px; color: #6aa8ff; background: #12203a; border: 1px solid #24406a;
    border-radius: 3px; padding: 2px 8px; cursor: pointer; letter-spacing: 0; text-transform: none;
  }
  .hist-sym {
    font-size: 10px; background: #12121f; color: #cde; border: 1px solid #2d2d4a;
    border-radius: 3px; padding: 1px 4px; text-transform: none; letter-spacing: 0;
  }
  .csv-btn:hover { border-color: #6aa8ff66; color: #cfe; }
  .res-title { margin-top: 14px; }

  .kv-grid { display: flex; flex-direction: column; gap: 3px; }
  .kv { display: flex; justify-content: space-between; padding: 3px 7px; background: #0f0f1e; border-radius: 3px; }
  .kv .k { font-size: 11px; color: #888; position: relative; }
  .kv .v { font-size: 11px; color: #4caf50; font-family: monospace; }

  .about-box { background: #0a1a0a; border: 1px solid #1e3a1e; border-radius: 4px; padding: 8px 10px; margin-bottom: 12px; }
  .about-name { font-size: 12px; color: #4caf50; font-weight: 600; margin-bottom: 4px; }
  .about-desc { font-size: 10px; color: #999; line-height: 1.5; margin-bottom: 6px; }
  .about-link { font-size: 10px; color: #6aa8ff; text-decoration: none; }
  .about-link:hover { text-decoration: underline; }

  .kv-i {
    display: inline-flex; align-items: center; justify-content: center;
    width: 13px; height: 13px; margin-left: 4px; border-radius: 50%;
    font-size: 10px; color: #6aa8ff; border: 1px solid #6aa8ff66; cursor: help; user-select: none;
  }
  .kv-i:hover { background: #6aa8ff22; }
  .kv-popover {
    position: absolute; left: 0; top: 100%; margin-top: 4px; z-index: 30;
    width: 230px; background: #12121f; border: 1px solid #3d3d5a; border-radius: 4px;
    padding: 7px 9px; box-shadow: 0 4px 16px #000000aa;
  }
  .pp-title { font-size: 11px; color: #fff; font-weight: 600; margin-bottom: 3px; }
  .pp-body { font-size: 10px; color: #bbb; line-height: 1.5; }

  .result-grid { display: flex; flex-direction: column; gap: 4px; }
  .r-row { display: flex; align-items: baseline; gap: 6px; font-size: 11px; color: #999; }
  .r-row span:first-child { flex: 1; }
  .r-row b { color: #ccc; font-size: 12px; font-family: monospace; }
  .r-row .sub { color: #666; font-size: 10px; flex: none; }
  .basis { margin-top: 8px; font-size: 10px; color: #555; font-style: italic; line-height: 1.5; }
  .pf-syms { display: flex; flex-direction: column; gap: 2px; }
  .pf-sym { display: flex; align-items: baseline; gap: 6px; padding: 2px 7px; background: #0f0f1e; border-radius: 3px; }
  .pf-sym .k { font-size: 11px; color: #6aa8ff; font-family: monospace; flex: none; width: 52px; }
  .pf-sym .sub { flex: 1; font-size: 10px; color: #666; }
  .pf-sym b { font-size: 11px; font-family: monospace; color: #ccc; }
  .pos { color: #4caf50 !important; } .neg { color: #f44336 !important; }

  .history-scroll { flex: 1; overflow-y: auto; min-height: 0; }
  table { width: 100%; border-collapse: collapse; }
  thead th {
    position: sticky; top: 0; background: #12121f; z-index: 1;
    font-size: 10px; color: #666; text-align: left; padding: 5px 10px; font-weight: 500;
    border-bottom: 1px solid #2d2d4a;
  }
  tbody td { font-size: 11px; color: #bbb; padding: 4px 10px; border-bottom: 1px solid #14141f; }
  tbody tr:hover { background: #12121f; }
  tbody tr.rejected td { opacity: 0.5; }
  .mono { font-family: monospace; }
  td.buy { color: #4caf50; } td.sell { color: #f44336; }
  thead th.num, tbody td.num { text-align: right; }
  .tt-badge { font-size: 10px; padding: 1px 6px; border-radius: 2px; background: #1a1a2e; color: #888; font-weight: 600; }
  .tt-tp { background: #11271a; color: #4caf50; }
  .tt-sl { background: #2a1414; color: #ff6b6b; }
  .tt-avg { background: #2a2410; color: #ffb300; }
  .tt-enf { background: #0e2a18; color: #36d57a; }
  .tt-rev { background: #1a1430; color: #b388ff; }
  .tt-open { background: #14222a; color: #6aa8ff; }
  .tt-none { background: transparent; color: #555; }
  .st-badge { font-size: 10px; padding: 1px 6px; border-radius: 2px; background: #1a1a2e; color: #888; }
  .st-paper { background: #1a2a1a; color: #4caf50; }
  .st-rejected, .st-skipped { background: #2a1414; color: #ff6b6b; }
  .st-submitted, .st-filled, .st-executed { background: #14222a; color: #6aa8ff; }
  .empty { padding: 24px; text-align: center; color: #555; font-size: 12px; }

  /* Стенд одного формата с агентским: полоса статуса, фреймы, нижний ряд. */
  .ars-strip { display: grid; grid-template-columns: 1.4fr 1fr; gap: 6px; padding: 6px 6px 0; flex: 0 0 auto; }
  .ars-strip > :global(.frame) { max-height: 210px; }
  .ars-strip > :global(.frame.max) { max-height: none; }
  .win-body > :global(.frame) { margin: 6px; }
  .bottom-row { display: flex; gap: 6px; padding: 0 6px 6px; flex: 1 1 auto; min-height: 260px; }
  .bottom-row > :global(.frame) { flex: 1 1 0; min-width: 0; }
  .fpad { padding: 8px 10px; }
  .rw-chart-body { height: 100%; display: flex; flex-direction: column; }

  /* Телефон: лента сверху вниз, как на агентском стенде. */
  @media (max-width: 820px) {
    .overlay { padding: 0; }
    .window { width: 100vw; height: 100dvh; border-radius: 0; border: none; }
    .win-body { overflow-y: auto; font-size: 14px; }
    .ars-strip { grid-template-columns: 1fr; }
    .ars-strip > :global(.frame) { max-height: none; }
    .bottom-row { flex-direction: column; min-height: 0; }
    .rw-chart-body { height: 62vh; min-height: 300px; }
  }
</style>
