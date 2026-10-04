<!-- Витрина кампаний бэктеста: ВСЕ проведённые кампании карточками, по клику —
     подробный отчёт лидеров с большим графиком. Заказ оператора 04.10.2026.

     Работа двух окон: backtests ведут реестр и сборщик (data/campaign_showcase/*),
     здесь страница. Формат данных — docs/campaign-showcase-spec.md.

     Маршруты: /backtest/campaigns (витрина) и /backtest/campaigns/<slug>
     (развёрнутый отчёт, ссылка живёт вечно). Страница держит их сама через
     history, без перезагрузки; обычный клик по карточке остаётся ссылкой, поэтому
     Ctrl+клик открывает отчёт в новой вкладке.

     Принцип тот же, что у остальных экранов: ПУСТОЕ ОСТАЁТСЯ ПУСТЫМ. Нет кривой —
     пишем причину, а не рисуем нулевую линию; нет итога — прочерк, а не «0»; нет
     данных вовсе — «витрина не собрана», а не сетка пустых карточек. -->
<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import { fetchWithAuth } from '$lib/fetch-auth';
  import { downloadCSV } from '$lib/csv';
  import { setTitle } from '$lib/page-title';
  import {
    NO_FILTERS, KIND_LABEL, campaignPath, chainOf, cls, fmtPnl, revisionOf, routeOf,
    statusInfo, visibleCards, BASE_PATH, type Card, type Filters, type Route,
  } from '$lib/campaign-showcase';
  import CurveChart from './CurveChart.svelte';
  import ScreenTag from './ScreenTag.svelte';
  import NavMenu from '../NavMenu.svelte';

  let route = $state<Route>(routeOf(window.location.pathname, window.location.search) ?? { kind: 'list' });

  let cards = $state<Card[]>([]);
  let available = $state<boolean | null>(null);   // null — ещё не спросили
  let reason = $state('');
  let builtAt = $state<number | null>(null);
  let listErr = $state('');

  let report = $state<any>(null);
  let reportErr = $state('');
  let reportFor = '';
  let leaderRank = $state(1);

  let filters = $state<Filters>({ ...NO_FILTERS });

  // ── Данные ────────────────────────────────────────────────────────────────
  async function loadList() {
    try {
      const r = await fetchWithAuth('/api/v1/lab/showcase/campaigns');
      if (!r.ok) { listErr = `HTTP ${r.status}`; return; }
      const d = await r.json();
      cards = (d.campaigns ?? []) as Card[];
      available = !!d.available;
      reason = d.reason ?? '';
      builtAt = d.built_at_ms ?? null;
      listErr = '';
    } catch (e: any) { listErr = e?.message || 'нет связи'; }
  }

  async function loadReport(slug: string) {
    reportFor = slug; report = null; reportErr = ''; leaderRank = 1;
    try {
      const r = await fetchWithAuth(`/api/v1/lab/showcase/campaigns/${encodeURIComponent(slug)}`);
      if (reportFor !== slug) return;          // пока грузили, ушли на другую
      if (r.status === 404) { reportErr = 'Такой кампании нет: slug мог измениться или сборщик её ещё не выдал.'; return; }
      if (!r.ok) { const d = await r.json().catch(() => null); reportErr = d?.detail ?? `HTTP ${r.status}`; return; }
      report = await r.json();
      const first = (report.leaders ?? [])[0];
      if (first) leaderRank = first.rank ?? 1;
    } catch (e: any) { if (reportFor === slug) reportErr = e?.message || 'нет связи'; }
  }

  // ── Навигация ─────────────────────────────────────────────────────────────
  function go(path: string, e?: MouseEvent) {
    // Ctrl/Cmd/средняя кнопка — обычная ссылка в новой вкладке, её не перехватываем.
    if (e && (e.ctrlKey || e.metaKey || e.shiftKey || e.button === 1)) return;
    e?.preventDefault();
    history.pushState(null, '', path);
    route = routeOf(path) ?? { kind: 'list' };
    window.scrollTo({ top: 0 });
  }
  const onPop = () => { route = routeOf(window.location.pathname, window.location.search) ?? { kind: 'list' }; };

  onMount(() => { loadList(); window.addEventListener('popstate', onPop); });
  onDestroy(() => window.removeEventListener('popstate', onPop));

  $effect(() => { if (route.kind === 'campaign') loadReport(route.slug); else { report = null; reportFor = ''; } });

  const card = $derived(route.kind === 'campaign' ? cards.find((c) => c.slug === route.slug) ?? null : null);
  $effect(() => {
    if (route.kind === 'campaign') setTitle(`Кампания ${report?.title ?? card?.title ?? route.slug}`);
    else setTitle('Витрина кампаний');
  });

  // ── Производные ───────────────────────────────────────────────────────────
  const revs = $derived(revisionOf(cards));
  const shown = $derived(visibleCards(cards, filters));
  const statusCodes = $derived([...new Set(cards.map((c) => c.status))]);
  const kinds = $derived([...new Set(cards.map((c) => c.kind ?? 'research'))]);
  const symbols = $derived([...new Set(cards.flatMap((c) => c.symbols ?? []))].sort());
  const filtered = $derived(JSON.stringify(filters) !== JSON.stringify(NO_FILTERS));

  const leader = $derived((report?.leaders ?? []).find((l: any) => (l.rank ?? 0) === leaderRank)
    ?? (report?.leaders ?? [])[0] ?? null);
  const unit = $derived(report?.unit ?? card?.unit ?? '');
  const chain = $derived(report?.revisions ?? (route.kind === 'campaign'
    ? chainOf(cards, card?.family).map((c) => ({ slug: c.slug, rev: c.rev, changes: null })) : []));

  const fmtWhen = (ms: number | null) => ms == null ? '—'
    : new Date(ms).toLocaleString('ru-RU', { timeZone: 'Europe/Moscow', day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });

  const params = (p: Record<string, unknown> | null | undefined) =>
    p && Object.keys(p).length ? Object.entries(p).map(([k, v]) => `${k}=${v}`).join(' · ') : '—';

  // Метрики лидера — набор ключей задаёт сборщик, поэтому колонки берём из ДАННЫХ,
  // а не из перечня: перечень, который молча отстаёт от источника, на этом проекте
  // уже дважды прятал новые поля. Известным ключам даём человеческое имя, остальные
  // печатаем как есть.
  const METRIC_RU: Record<string, string> = {
    net: 'Net', pnl: 'P&L', max_dd: 'Просадка', dd: 'Просадка', trades: 'Сделки',
    trades_n: 'Сделки', win_rate: 'Win rate', pf: 'PF', sharpe: 'Sharpe', gross: 'Gross',
    commission: 'Комиссия', calmar: 'Calmar', recovery: 'Recovery',
  };
  const MONEY = /(^|_)(net|pnl|gross|dd|commission|loss|profit)($|_)/;
  const metricKeys = $derived.by(() => {
    const seen: string[] = [];
    for (const l of report?.leaders ?? []) for (const k of Object.keys(l.metrics ?? {})) if (!seen.includes(k)) seen.push(k);
    return seen;
  });
  function fmtMetric(k: string, v: unknown): string {
    if (v == null || (typeof v === 'number' && !Number.isFinite(v))) return '—';
    if (typeof v !== 'number') return String(v);
    if (MONEY.test(k)) return fmtPnl(v, unit, k !== 'dd' && k !== 'max_dd');
    return v.toLocaleString('ru-RU', { maximumFractionDigits: 2 });
  }

  function exportList() {
    downloadCSV(shown.map((c) => ({
      slug: c.slug, название: c.title, идея: c.idea ?? '', линия: c.family ?? '', ред: c.rev ?? '',
      статус: c.status, вид: c.kind ?? '', единица: c.unit ?? '',
      net: c.headline?.net ?? '', сделки: c.headline?.trades ?? '',
      просадка: c.headline?.max_dd ?? '', окно: c.headline?.window ?? '', вердикт: c.verdict ?? '',
    })), 'campaigns.csv');
  }
  function exportLeaders() {
    downloadCSV((report?.leaders ?? []).map((l: any) => ({
      slug: report.slug, rank: l.rank, ...(l.metrics ?? {}), trades_n: l.trades_n ?? '',
      ...Object.fromEntries(Object.entries(l.params ?? {}).map(([k, v]) => [`param.${k}`, v])),
    })), `${report?.slug ?? 'campaign'}-leaders.csv`);
  }

  const absUrl = (path: string) => `${window.location.origin}${path}`;
  const here = $derived(route.kind === 'campaign' ? campaignPath(route.slug) : BASE_PATH);
</script>

<div class="cs">
  <ScreenTag id={route.kind === 'campaign' ? `CAMPAIGN-${route.slug}` : 'CAMPAIGNS'}
             name={route.kind === 'campaign' ? 'отчёт кампании' : 'витрина кампаний'}
             corner="tl" copyText={absUrl(here)} />

  <header class="cs-head">
    <NavMenu />
    <h1>{route.kind === 'campaign' ? 'Отчёт кампании' : 'Витрина кампаний бэктеста'}</h1>
    {#if available}
      <span class="cs-meta">собрано {fmtWhen(builtAt)} МСК</span>
    {/if}
  </header>

  {#if route.kind === 'list'}
    <!-- ───────────────────────── ВИТРИНА ───────────────────────── -->
    {#if listErr}
      <div class="cs-note bad">Витрину не прочитать: {listErr}.</div>
    {:else if available === null}
      <div class="cs-note">Загрузка…</div>
    {:else if !available}
      <div class="cs-note">{reason || 'Витрина ещё не собрана.'}</div>
    {:else}
      <div class="cs-filters">
        <input class="cs-q" type="search" placeholder="поиск по названию и идее" bind:value={filters.q}
               aria-label="поиск по кампаниям" />
        <select bind:value={filters.status} aria-label="статус">
          <option value="all">все статусы</option>
          {#each statusCodes as s (s)}<option value={s}>{statusInfo({ status: s }).label}</option>{/each}
        </select>
        {#if kinds.length > 1}
          <select bind:value={filters.kind} aria-label="вид кампании">
            <option value="all">любой вид</option>
            {#each kinds as k (k)}<option value={k}>{KIND_LABEL[k] ?? k}</option>{/each}
          </select>
        {/if}
        {#if symbols.length}
          <select bind:value={filters.symbol} aria-label="инструмент">
            <option value="">любой инструмент</option>
            {#each symbols as s (s)}<option value={s}>{s}</option>{/each}
          </select>
        {/if}
        <label class="cs-chk"><input type="checkbox" bind:checked={filters.allRevisions} />
          показать все редакции</label>
        {#if filtered}
          <button class="cs-btn" onclick={() => (filters = { ...NO_FILTERS })}>сбросить</button>
        {/if}
        <span class="cs-sp"></span>
        <span class="cs-count">{shown.length} из {cards.length}</span>
        <button class="cs-btn" onclick={exportList} disabled={!shown.length}>CSV</button>
      </div>

      {#if !shown.length}
        <div class="cs-note">{cards.length ? 'Под фильтр ничего не попало.' : 'В витрине нет ни одной кампании.'}</div>
      {/if}

      <div class="cs-grid">
        {#each shown as c (c.slug)}
          {@const st = statusInfo(c)}
          {@const rv = revs.get(c.slug)}
          <a class="cs-card" href={campaignPath(c.slug)} onclick={(e) => go(campaignPath(c.slug), e)}>
            <div class="cs-row">
              <span class={`cs-badge ${st.tone}`}>{st.label}</span>
              {#if c.kind && c.kind !== 'optimizer'}<span class="cs-mark">{KIND_LABEL[c.kind] ?? c.kind}</span>{/if}
              {#if rv}<span class="cs-mark" title="редакция в линии идеи">ред. {rv.rev} из {rv.of}</span>{/if}
            </div>
            <h2>{c.title}</h2>
            {#if c.idea}<p class="cs-idea">{c.idea}</p>{/if}
            <CurveChart points={c.thumb ?? null} unit={c.unit ?? ''}
                        emptyText={c.status === 'queued' ? 'ожидает прогона'
                          : (c.no_curve_reason || 'кривой нет')} />
            <dl class="cs-kv">
              <div><dt>net</dt><dd class={cls(c.headline?.net)}>{fmtPnl(c.headline?.net, c.unit)}</dd></div>
              <div><dt>сделки</dt><dd>{c.headline?.trades == null ? '—' : c.headline.trades.toLocaleString('ru-RU')}</dd></div>
              <div><dt>просадка</dt><dd>{fmtPnl(c.headline?.max_dd, c.unit, false)}</dd></div>
            </dl>
            {#if c.headline?.window}<div class="cs-win">{c.headline.window}</div>{/if}
          </a>
        {/each}
      </div>
    {/if}

  {:else}
    <!-- ───────────────────────── ОТЧЁТ ───────────────────────── -->
    <a class="cs-back" href={BASE_PATH} onclick={(e) => go(BASE_PATH, e)}>← все кампании</a>

    {#if reportErr}
      <div class="cs-note bad">{reportErr}</div>
    {:else if !report}
      <div class="cs-note">Загрузка отчёта…</div>
    {:else}
      {@const st = statusInfo(report)}
      <section class="cs-rep">
        <div class="cs-row">
          <span class={`cs-badge ${st.tone}`}>{st.label}</span>
          {#if report.kind}<span class="cs-mark">{KIND_LABEL[report.kind] ?? report.kind}</span>{/if}
          {#if report.family}<span class="cs-mark">линия {report.family}</span>{/if}
          {#if report.rev != null}<span class="cs-mark">ред. {report.rev}</span>{/if}
        </div>
        <h2 class="cs-title">{report.title}</h2>
        {#if report.idea}<p class="cs-idea big">{report.idea}</p>{/if}
        {#if report.strategy}<p class="cs-sub">стратегия: <b>{report.strategy}</b></p>{/if}

        <CurveChart full points={leader?.curve ?? null} {unit}
                    emptyText={report.no_curve_reason || (leader ? 'у этого лидера кривой нет' : 'кривой нет')} />
        {#if leader}
          <div class="cs-leadline">лидер №{leader.rank}
            {#if leader.trades_n != null}· сделок {leader.trades_n.toLocaleString('ru-RU')}{/if}
            · {params(leader.params)}</div>
        {/if}

        {#if report.verdict}
          <div class="cs-verdict"><b>Вердикт.</b> {report.verdict}</div>
        {/if}

        {#if (report.leaders ?? []).length}
          <div class="cs-sec">
            <h3>Лидеры</h3>
            <button class="cs-btn" onclick={exportLeaders}>CSV</button>
          </div>
          <div class="cs-scroll">
            <table class="cs-tbl">
              <thead><tr>
                <th>№</th>
                {#each metricKeys as k (k)}<th>{METRIC_RU[k] ?? k}</th>{/each}
                <th>Параметры</th>
              </tr></thead>
              <tbody>
                {#each report.leaders as l (l.rank)}
                  <tr class:on={l.rank === leaderRank} onclick={() => (leaderRank = l.rank)}
                      title={l.curve ? 'показать кривую этого лидера' : 'у этого лидера кривой нет'}>
                    <td>{l.rank}</td>
                    {#each metricKeys as k (k)}
                      <td class={MONEY.test(k) && typeof l.metrics?.[k] === 'number' ? cls(l.metrics[k]) : ''}>{fmtMetric(k, l.metrics?.[k])}</td>
                    {/each}
                    <td class="p">{params(l.params)}</td>
                  </tr>
                {/each}
              </tbody>
            </table>
          </div>
        {/if}

        {#if chain.length > 1}
          <div class="cs-sec"><h3>Редакции линии</h3></div>
          <ol class="cs-chain">
            {#each chain as r (r.slug)}
              <li class:cur={r.slug === report.slug}>
                <a href={campaignPath(r.slug)} onclick={(e) => go(campaignPath(r.slug), e)}>ред. {r.rev ?? '?'}</a>
                <span>{r.changes ?? (r.slug === report.slug ? 'эта редакция' : '')}</span>
              </li>
            {/each}
          </ol>
        {/if}

        <div class="cs-sec"><h3>Условия</h3></div>
        <dl class="cs-facts">
          {#if report.data_window}
            <div><dt>окно данных</dt><dd>{report.data_window.from ?? '—'} … {report.data_window.to ?? '—'}</dd></div>
            {#if (report.data_window.symbols ?? []).length}
              <div><dt>инструменты</dt><dd>{report.data_window.symbols.join(', ')}</dd></div>
            {/if}
          {/if}
          {#if (report.runs ?? []).length}
            <div><dt>прогоны</dt><dd class="mono">{report.runs.join(', ')}</dd></div>
          {/if}
          {#if report.unit}<div><dt>единица</dt><dd>{report.unit}</dd></div>{/if}
          {#if report.doc}<div><dt>документ</dt><dd class="mono">{report.doc}</dd></div>{/if}
          {#if report.built_at_ms}<div><dt>собрано</dt><dd>{fmtWhen(report.built_at_ms)} МСК</dd></div>{/if}
        </dl>
        {#if report.notes}<p class="cs-sub">{report.notes}</p>{/if}
      </section>
    {/if}
  {/if}
</div>

<style>
  /* Токены темы: тёмная по умолчанию (вся SPA тёмная), светлая — по системной. */
  .cs {
    --bg: #0f0f1e; --panel: #14142a; --bd: #2d2d4a; --ink: #e2e6f0; --muted: #8a90a8; --faint: #6f7590;
    --pos: #6fa77a; --neg: #c47a7a; --accent: #4dd0e1; --warn: #e0a35c;
    position: relative; min-height: 100vh; background: var(--bg); color: var(--ink);
    font: 13px/1.5 -apple-system, "Segoe UI", Roboto, sans-serif;
  }
  @media (prefers-color-scheme: light) {
    .cs {
      --bg: #f4f2ea; --panel: #fcfbf6; --bd: #d9d5c6; --ink: #222630; --muted: #6a6f80; --faint: #8b8f9c;
      --pos: #4f8a5a; --neg: #b25f5f; --accent: #2b5fb0; --warn: #a5661b;
    }
  }
  :global(body:has(.cs)) { background: #0f0f1e; }
  @media (prefers-color-scheme: light) { :global(body:has(.cs)) { background: #f4f2ea; } }

  /* ID окна — в ЛЕВОМ верхнем углу (правило оператора), шапка сдвинута под него. */
  .cs-head { display: flex; align-items: center; gap: 12px; flex-wrap: wrap;
             padding: 26px 20px 10px; border-bottom: 1px solid var(--bd); }
  .cs-head h1 { margin: 0; font-size: 16px; font-weight: 600; }
  .cs-meta { color: var(--faint); font-size: 11px; margin-left: auto; }

  .cs-note { margin: 24px 20px; padding: 14px 16px; border: 1px dashed var(--bd); border-radius: 6px;
             color: var(--muted); max-width: 70ch; }
  .cs-note.bad { color: var(--neg); border-color: var(--neg); }

  .cs-filters { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; padding: 12px 20px; }
  .cs-filters input[type=search], .cs-filters select {
    background: var(--panel); color: var(--ink); border: 1px solid var(--bd); border-radius: 4px;
    padding: 5px 8px; font-size: 12px; min-height: 30px; }
  .cs-q { min-width: 220px; }
  .cs-chk { display: inline-flex; align-items: center; gap: 5px; color: var(--muted); font-size: 12px; cursor: pointer; }
  .cs-sp { flex: 1; }
  .cs-count { color: var(--faint); font-size: 11px; }
  .cs-btn { background: var(--panel); color: var(--muted); border: 1px solid var(--bd); border-radius: 4px;
            padding: 4px 10px; font-size: 11px; cursor: pointer; min-height: 28px; }
  .cs-btn:hover:not(:disabled) { color: var(--ink); }
  .cs-btn:disabled { opacity: .5; cursor: default; }

  .cs-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
             gap: 12px; padding: 4px 20px 60px; }
  .cs-card { display: flex; flex-direction: column; gap: 8px; padding: 12px 14px; background: var(--panel);
             border: 1px solid var(--bd); border-radius: 8px; color: inherit; text-decoration: none;
             min-width: 0; }
  .cs-card:hover { border-color: var(--accent); }
  .cs-card h2 { margin: 0; font-size: 14px; line-height: 1.3; font-weight: 600; overflow-wrap: anywhere; }
  .cs-idea { margin: 0; color: var(--muted); font-size: 12px; line-height: 1.45;
             display: -webkit-box; -webkit-line-clamp: 2; line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
  .cs-idea.big { display: block; font-size: 14px; max-width: 80ch; -webkit-line-clamp: unset; line-clamp: unset; }

  .cs-row { display: flex; gap: 6px; flex-wrap: wrap; align-items: center; }
  .cs-badge { font-size: 10px; font-weight: 600; padding: 1px 7px; border-radius: 10px; border: 1px solid currentColor; white-space: nowrap; }
  .cs-badge.ok { color: var(--pos); } .cs-badge.run { color: var(--accent); }
  .cs-badge.wait { color: var(--warn); } .cs-badge.bad { color: var(--neg); } .cs-badge.unk { color: var(--faint); }
  .cs-mark { font-size: 10px; color: var(--muted); border: 1px solid var(--bd); border-radius: 3px; padding: 0 5px; white-space: nowrap; }

  .cs-kv { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 6px; margin: 0; }
  .cs-kv dt { font-size: 10px; color: var(--faint); }
  .cs-kv dd { margin: 0; font-size: 12px; font-weight: 600; white-space: nowrap; font-variant-numeric: tabular-nums; }
  .pos { color: var(--pos); } .neg { color: var(--neg); }
  .cs-win { font-size: 10px; color: var(--faint); }

  .cs-back { display: inline-block; margin: 14px 20px 0; color: var(--muted); font-size: 12px; text-decoration: none; }
  .cs-back:hover { color: var(--ink); }
  .cs-rep { padding: 8px 20px 60px; max-width: 1100px; }
  .cs-title { margin: 8px 0 6px; font-size: 20px; line-height: 1.25; }
  .cs-sub { color: var(--muted); font-size: 12px; margin: 4px 0 10px; }
  .cs-leadline { margin-top: 6px; font-size: 11px; color: var(--muted); font-family: ui-monospace, Consolas, monospace;
                 overflow-wrap: anywhere; }
  .cs-verdict { margin: 14px 0; padding: 10px 14px; border-left: 3px solid var(--accent); background: var(--panel);
                border-radius: 0 6px 6px 0; font-size: 13px; }
  .cs-sec { display: flex; align-items: center; gap: 10px; margin: 22px 0 8px; }
  .cs-sec h3 { margin: 0; font-size: 11px; letter-spacing: .14em; text-transform: uppercase; color: var(--muted); }
  .cs-scroll { overflow-x: auto; border: 1px solid var(--bd); border-radius: 6px; }
  .cs-tbl { width: 100%; border-collapse: collapse; font-size: 12px; }
  .cs-tbl th { text-align: left; color: var(--faint); font-weight: 600; font-size: 10px; padding: 6px 10px;
               border-bottom: 1px solid var(--bd); white-space: nowrap; }
  .cs-tbl td { padding: 6px 10px; border-bottom: 1px solid var(--bd); white-space: nowrap; font-variant-numeric: tabular-nums; }
  .cs-tbl td.p { white-space: normal; color: var(--muted); font-family: ui-monospace, Consolas, monospace; font-size: 11px; min-width: 260px; }
  .cs-tbl tbody tr { cursor: pointer; }
  .cs-tbl tbody tr:hover { background: color-mix(in srgb, var(--accent) 8%, transparent); }
  .cs-tbl tbody tr.on { background: color-mix(in srgb, var(--accent) 14%, transparent); }
  .cs-tbl tr:last-child td { border-bottom: none; }
  .cs-chain { list-style: none; margin: 0; padding: 0; display: grid; gap: 4px; }
  .cs-chain li { display: flex; gap: 10px; padding: 6px 10px; border: 1px solid var(--bd); border-radius: 5px; }
  .cs-chain li.cur { border-color: var(--accent); }
  .cs-chain a { color: var(--accent); text-decoration: none; white-space: nowrap; font-weight: 600; }
  .cs-chain span { color: var(--muted); font-size: 12px; }
  .cs-facts { display: grid; gap: 4px; margin: 0; }
  .cs-facts div { display: flex; gap: 12px; }
  .cs-facts dt { width: 120px; flex-shrink: 0; color: var(--faint); font-size: 11px; }
  .cs-facts dd { margin: 0; overflow-wrap: anywhere; }
  .mono { font-family: ui-monospace, Consolas, monospace; font-size: 11px; }

  @media (max-width: 560px) {
    .cs-head, .cs-filters, .cs-grid, .cs-rep { padding-left: 12px; padding-right: 12px; }
    .cs-grid { grid-template-columns: minmax(0, 1fr); }
    .cs-q { min-width: 0; flex: 1 1 100%; }
  }
</style>
