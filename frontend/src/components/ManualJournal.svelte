<!-- Журнал ручных заявок: что происходило с заявками оператора и ПО ЧЬЕЙ ВОЛЕ,
     плюс итог его собственной торговли за день/неделю/месяц.

     Данные и API — окно real-trade (trader/api/quik_manual.py), экран наш.
     Три вещи, которые здесь нельзя проглотить, иначе экран соврёт:
       partial      — журнал начат 23.09.2026, «за месяц» пока не месяц;
       priced=false — у инструмента нет ₽ за пункт, рублёвый итог НЕПОЛНЫЙ;
       open         — переоценка открытой позиции, в итог НЕ входит.
     Их разбор живёт в $lib/manual-journal под тестами, а не в разметке. -->
<script lang="ts">
  import { onDestroy, onMount } from 'svelte';
  import { fetchWithAuth } from '$lib/fetch-auth';
  import { downloadCSV } from '$lib/csv';
  import { fmtPrice } from '$lib/format';
  import ScreenTag from './lab/ScreenTag.svelte';
  import {
    CHANNELS_WITH_EVENTS, PERIOD_RU, accountNet, channelRu, eventRu, filterRows,
    openMismatch, openTotalRub, pnlCaveats, rowCodes, unpricedPoints,
    type JournalRow, type Period, type PnlReport,
  } from '$lib/manual-journal';

  let { onClose }: { onClose?: () => void } = $props();

  let period = $state<Period>('day');
  let pnl = $state<PnlReport | null>(null);
  let rows = $state<JournalRow[]>([]);
  let loading = $state(true);
  let err = $state('');
  let query = $state('');
  let kindFilter = $state<'all' | 'event' | 'trade'>('all');
  let codeFilter = $state('');
  let openRow = $state<string | null>(null);
  // Позиция СЧЁТА: нужна, чтобы не выдать остаток журнала за открытую позицию.
  let net = $state<Record<string, number> | null>(null);
  let fullscreen = $state(false);
  function onKeydown(e: KeyboardEvent) { if (e.key === 'Escape' && fullscreen) fullscreen = false; }
  // Лента по ОДНОЙ заявке: ?journal=1&so=<id> — так на неё ссылается карточка.
  const soId = typeof location !== 'undefined'
    ? (new URLSearchParams(location.search).get('so') ?? '') : '';

  const caveats = $derived(pnlCaveats(pnl));
  const openRub = $derived(openTotalRub(pnl));
  // Сверку остатка с позицией счёта теперь делает СЕРВЕР (open_vs_account). Своя
  // остаётся запасной: она работает и на старом бэкенде, до рестарта.
  const mismatch = $derived(
    pnl?.open_vs_account && Object.keys(pnl.open_vs_account).length
      ? Object.entries(pnl.open_vs_account).map(([symbol, diff]) => ({
          symbol, journal: Number(diff) + Number(pnl?.account_manual?.[symbol] ?? 0),
          account: Number(pnl?.account_manual?.[symbol] ?? 0) }))
      : openMismatch(pnl, net));
  const unpriced = $derived(unpricedPoints(pnl));
  const shown = $derived(filterRows(rows, { query, kind: kindFilter, code: codeFilter }));
  const codes = $derived(rowCodes(rows));

  const rub = (v: any) => (v == null || !Number.isFinite(Number(v)))
    ? '—' : (Number(v) > 0 ? '+' : '') + Math.round(Number(v)).toLocaleString('ru-RU') + ' ₽';
  const num = (v: any) => (v == null ? '—' : Number(v).toLocaleString('ru-RU'));
  const pts = (v: any) => (v == null ? '—'
    : (Number(v) > 0 ? '+' : '') + Number(v).toLocaleString('ru-RU', { maximumFractionDigits: 1 }) + ' п.');
  const when = (ms: any) => !ms ? '—'
    : new Date(Number(ms)).toLocaleString('ru-RU', { day: '2-digit', month: '2-digit',
        hour: '2-digit', minute: '2-digit', second: '2-digit' });
  const sideRu = (s: any) => s === 'buy' ? 'покупка' : s === 'sell' ? 'продажа' : '';

  async function load() {
    loading = true; err = '';
    try {
      const q = `period=${period}` + (soId ? `&so_id=${encodeURIComponent(soId)}` : '');
      const [p, j, st] = await Promise.all([
        fetchWithAuth(`/api/v1/quik/manual/pnl?period=${period}`),
        fetchWithAuth(`/api/v1/quik/manual/journal?${q}&limit=1000`),
        // Позиция счёта — для сверки: «открытая позиция» отчёта это остаток
        // проигрывания журнала ЗА ОКНО, а не факт счёта.
        fetchWithAuth('/api/v1/quik/agent-local-status'),
      ]);
      net = st.ok ? accountNet(await st.json()) : null;
      // Итог и лента независимы: упала одна — вторая всё равно показывается.
      pnl = p.ok ? await p.json() : null;
      if (j.ok) { const d = await j.json(); rows = d?.rows ?? []; }
      else { rows = []; err = `лента не пришла: HTTP ${j.status}`; }
      if (!p.ok && !err) err = `итог не пришёл: HTTP ${p.status}`;
    } catch (e) {
      err = 'не удалось получить данные: ' + String(e).slice(0, 140);
    } finally {
      loading = false;
    }
  }

  let timer: ReturnType<typeof setInterval> | null = null;
  onMount(() => { load(); timer = setInterval(load, 30_000); });
  onDestroy(() => { if (timer) clearInterval(timer); });
  function setPeriod(p: Period) { period = p; load(); }
</script>

<svelte:window onkeydown={onKeydown} />

<div class="mj" class:fullscreen>
  <ScreenTag id="MANUAL-JOURNAL" name="журнал ручных заявок" corner="tl"
             copyText={typeof location !== 'undefined' ? location.href : '/?journal=1'} />
  <header class="mj-head">
    <h2>Журнал ручных заявок{#if soId}<span class="mj-one">заявка {soId}</span>{/if}</h2>
    <div class="mj-seg" role="group" aria-label="Период">
      {#each (['day', 'week', 'month'] as Period[]) as p}
        <button type="button" class:on={period === p} onclick={() => setPeriod(p)}>{PERIOD_RU[p]}</button>
      {/each}
    </div>
    {#if loading}<span class="mj-load">обновляю…</span>{/if}
    <button class="mj-full" title={fullscreen ? 'Свернуть (Esc)' : 'Развернуть на весь экран'}
            onclick={() => fullscreen = !fullscreen}>
      {fullscreen ? '⊟ Свернуть' : '⛶ Во весь экран'}
    </button>
    {#if onClose}<button class="mj-close" onclick={onClose}>✕</button>{/if}
  </header>

  {#if err}<div class="mj-err">{err}</div>{/if}

  <!-- ИТОГ. Рубли крупно, но рядом ровно то, что мешает читать их буквально. -->
  <section class="mj-total">
    <div class="mj-t-main">
      <span class="mj-t-k">Реализовано ручными за {PERIOD_RU[period]}</span>
      <span class="mj-t-v" class:pos={(pnl?.net_rub ?? 0) > 0} class:neg={(pnl?.net_rub ?? 0) < 0}>
        {pnl ? rub(pnl.net_rub) : '—'}
      </span>
    </div>
    <div class="mj-t-sub">
      {#if pnl}
        валовый {rub(pnl.gross_rub)} · комиссия {rub(-Math.abs(Number(pnl.commission_rub ?? 0)))}
        · сделок {num(pnl.fills)} · контрактов {num(pnl.lots)}
        {#if pnl.from}· период {pnl.from}…{pnl.to}{/if}
      {:else if !loading}
        итог не пришёл
      {/if}
    </div>
    <!-- ДВЕ РАЗНЫЕ ВЕЛИЧИНЫ С ПОХОЖИМИ НАЗВАНИЯМИ. В компаньоне «Итог ручных» —
         рыночная переоценка за день из разбивки агента: она включает переоценку
         перенесённой позиции и по построению складывается в ВМ счёта. Здесь —
         РЕАЛИЗОВАННЫЙ результат закрытых кругов по журналу сделок, без
         переоценки и с ОЦЕНОЧНОЙ комиссией. Цифры законно расходятся, и молчать
         об этом нельзя: оператор сверял их и не сошёлся (24.09.2026). -->
    <div class="mj-method">
      Это РЕАЛИЗОВАННЫЙ результат закрытых кругов по журналу сделок: переоценка
      открытой позиции сюда не входит, комиссия — оценка по модели FORTS
      (QUIK её в таблице сделок не отдаёт). В компаньоне строка «Итог ручных»
      считает другое — рыночную переоценку за день, которая складывается в ВМ
      счёта. Две разные величины, сходиться они не обязаны.
    </div>
    {#each caveats as c}<div class="mj-warn">{c}</div>{/each}
    {#if unpriced.length}
      <div class="mj-warn">
        Без ₽ за пункт: {unpriced.map((u) => `${u.symbol} ${pts(u.points)}`).join(' · ')}
        — эти результаты в рублёвый итог НЕ вошли.
      </div>
    {/if}
    {#if mismatch.length}
      <!-- ОСТАТОК ЖУРНАЛА != ПОЗИЦИЯ СЧЁТА. «Открытая позиция» отчёта получается
           проигрыванием сделок ЗА ОКНО: позиция, набранная до начала окна, в него
           не входит, и остаток может быть любым. 24.09.2026 экран написал «RIZ6
           −4, переоценка −976 ₽», когда счёт был ПУСТ. Числом такое не печатаем. -->
      <div class="mj-open bad">
        <span class="mj-o-k">Остаток журнала не сходится с позицией счёта</span>
        <div class="mj-o-rows">
          {#each mismatch as m}
            <span>{m.symbol}: по журналу за период {num(m.journal)},
              на счёте {num(m.account)}</span>
          {/each}
        </div>
        <em>Переоценку не показываем: она считалась бы от позиции, которой на счёте
          нет. Причина обычно в том, что позиция набрана ДО начала выбранного
          периода — переключите период или смотрите позицию в терминале.</em>
      </div>
    {:else if openRub != null}
      <!-- ОТДЕЛЬНО И ДРУГИМИ СЛОВАМИ: это не заработано, это текущая переоценка. -->
      <div class="mj-open">
        <span class="mj-o-k">Открытая позиция, переоценка сейчас</span>
        <span class="mj-o-v" class:pos={openRub > 0} class:neg={openRub < 0}>{rub(openRub)}</span>
        <em>в итог выше НЕ входит и меняется каждую секунду{#if net === null}; позиция счёта
          не пришла, сверить не с чем{/if}</em>
        <div class="mj-o-rows">
          {#each pnl?.open ?? [] as o}
            <span>{o.symbol} {num(o.position)} по {fmtPrice(o.avg_price ?? 0)}
              · сейчас {fmtPrice(o.last ?? 0)} · {rub(o.unrealized_rub)}</span>
          {/each}
        </div>
      </div>
    {/if}
  </section>

  {#if (pnl?.by_symbol ?? []).length || (pnl?.by_source ?? []).length}
    <div class="mj-breaks">
      {#if (pnl?.by_symbol ?? []).length}
        <section class="mj-break">
          <div class="mj-b-h">По инструментам
            <button class="mj-csv" onclick={() => downloadCSV(pnl?.by_symbol ?? [], 'manual_by_symbol')}>CSV</button>
          </div>
          <table class="mj-t">
            <thead><tr><th>инстр.</th><th>сделок</th><th>контр.</th><th>пункты</th><th>валовый</th><th>комиссия</th></tr></thead>
            <tbody>
              {#each pnl?.by_symbol ?? [] as s}
                <tr>
                  <td class="mono">{s.symbol}{#if !(s.point_value && s.point_value > 0)}<span class="mj-nopv" title="₽ за пункт неизвестен: рубли по этому инструменту не считаем">пункты</span>{/if}</td>
                  <td>{num(s.fills)}</td><td>{num(s.lots)}</td>
                  <td>{pts(s.realized_points)}</td>
                  <td>{s.point_value ? rub(s.gross_rub) : '—'}</td>
                  <td>{s.point_value ? rub(-Math.abs(Number(s.commission_rub ?? 0))) : '—'}</td>
                </tr>
              {/each}
            </tbody>
          </table>
        </section>
      {/if}
      {#if (pnl?.by_channel ?? pnl?.by_source ?? []).length}
        <!-- ТРИ КАНАЛА, как их называет оператор. Различает их сервер по реестру
             роботов, а не по форме тега; экран только переводит имена. -->
        <section class="mj-break">
          <div class="mj-b-h">По каналам
            <button class="mj-csv" onclick={() => downloadCSV(pnl?.by_channel ?? [], 'manual_by_channel')}>CSV</button>
          </div>
          <table class="mj-t">
            <thead><tr><th>канал</th><th title="заявки С ИСПОЛНЕНИЕМ: считаются по уникальным номерам заявок в сделках, снятые и неисполненные сюда не попадают">заявок*</th><th>сделок</th><th>контр.</th><th>нетто</th></tr></thead>
            <tbody>
              {#each (pnl?.by_channel ?? []) as c}
                <tr>
                  <td>{channelRu(c.channel)}{#if !CHANNELS_WITH_EVENTS.has(c.channel)}<span class="mj-nopv" title="терминал и приложение брокера своих намерений STL не рассказывают: от них видны только сделки">только сделки</span>{/if}</td>
                  <td>{num(c.orders)}</td>
                  <td>{num(c.fills)}</td><td>{num(c.lots)}</td>
                  <td class:pos={(c.net_rub ?? 0) > 0} class:neg={(c.net_rub ?? 0) < 0}>{rub(c.net_rub)}</td>
                </tr>
              {/each}
            </tbody>
          </table>
          <em class="mj-note">* заявки с исполнением. История заявок целиком есть
            только у умных заявок STL.</em>
        </section>
      {/if}
    </div>
  {/if}

  {#if (pnl?.by_day ?? []).length > 1}
    <!-- Неделя и месяц одним числом не читаются: оператор смотрит их по дням. -->
    {@const days = pnl?.by_day ?? []}
    {@const mx = Math.max(1, ...days.map((d) => Math.abs(Number(d.net_rub ?? 0))))}
    <section class="mj-break mj-days">
      <div class="mj-b-h">По дням
        <button class="mj-csv" onclick={() => downloadCSV(days, 'manual_by_day')}>CSV</button>
      </div>
      <div class="mj-bars">
        {#each days as d}
          {@const v = Number(d.net_rub ?? 0)}
          <div class="mj-bar" title="{d.date}: {rub(v)} · сделок {num(d.fills)}">
            <div class="mj-bar-fill" class:neg={v < 0}
                 style="height:{Math.round(Math.abs(v) / mx * 46) + 2}px"></div>
            <span class="mj-bar-d">{String(d.date).slice(8, 10)}</span>
          </div>
        {/each}
      </div>
    </section>
  {/if}

  <!-- ЛЕНТА. Источник — отдельной колонкой: ради него журнал и заведён. -->
  <section class="mj-feed">
    <div class="mj-f-h">
      События и сделки ({shown.length}{#if shown.length !== rows.length} из {rows.length}{/if})
      <input class="mj-search" placeholder="фильтр: событие, источник, id, инструмент…" bind:value={query} />
      <div class="mj-seg sm" role="group" aria-label="Что показывать">
        <button type="button" class:on={kindFilter === 'all'} onclick={() => kindFilter = 'all'}>всё</button>
        <button type="button" class:on={kindFilter === 'event'} onclick={() => kindFilter = 'event'}>события</button>
        <button type="button" class:on={kindFilter === 'trade'} onclick={() => kindFilter = 'trade'}>сделки</button>
      </div>
      {#if codes.length > 1}
        <select class="mj-sel" bind:value={codeFilter}>
          <option value="">все инструменты</option>
          {#each codes as c}<option value={c}>{c}</option>{/each}
        </select>
      {/if}
      <button class="mj-csv" onclick={() => downloadCSV(shown, 'manual_journal')}>CSV</button>
    </div>
    {#if !rows.length && !loading}
      <p class="mj-empty">За {PERIOD_RU[period]} событий и сделок нет.
        {#if pnl?.coverage_from}Журнал ведётся с {pnl.coverage_from}.{/if}</p>
    {/if}
    {#each shown as r, i (String(r.ts_ms) + ':' + (r.so_id ?? '') + ':' + i)}
      {@const key = String(r.ts_ms) + ':' + i}
      <article class="mj-row" class:trade={r.type === 'trade'}>
        <span class="mj-ts mono">{when(r.ts_ms)}</span>
        <span class="mj-ev">{eventRu(r)}</span>
        <span class="mj-src" title="кто это сделал">{r.source ?? '—'}</span>
        <span class="mj-code mono">{r.code ?? ''}</span>
        <span class="mj-side" class:buy={r.side === 'buy'}>{sideRu(r.side)}{r.qty ? ' ' + r.qty : ''}</span>
        <span class="mj-px mono">{r.price ? fmtPrice(r.price) : ''}</span>
        <button class="mj-more" onclick={() => openRow = openRow === key ? null : key}>
          {openRow === key ? 'свернуть' : 'подробно'}
        </button>
      </article>
      {#if openRow === key}
        <div class="mj-detail">
          {#if r.detail}<div>{r.detail}</div>{/if}
          <div class="mono dim">
            {#if r.so_id}заявка {r.so_id}{/if}
            {#if r.parent_id} · родитель {r.parent_id}{/if}
            {#if r.kind} · вид {r.kind}{/if}
            {#if r.order_num} · заявка QUIK {r.order_num}{/if}
            {#if r.event} · код события {r.event}{/if}
          </div>
        </div>
      {/if}
    {/each}
  </section>
</div>

<style>
  .mj { position: relative; height: 100%; overflow: auto; padding: 28px 16px 20px;
        color: #d7dbe8; font-size: 12px; max-width: 1180px; }
  .mj-head { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin-bottom: 12px; }
  .mj-head h2 { margin: 0; font-size: 17px; color: #e8e8f0; font-weight: 600; }
  .mj-one { margin-left: 8px; font: 11px/1 Consolas, monospace; color: #7ec8f0; }
  .mj-load { font-size: 10px; color: #8a90a8; }
  .mj.fullscreen { position: fixed; inset: 0; z-index: 1000; width: 100vw; height: 100vh;
    max-width: none; background: #0f0f1e; }
  .mj-full { margin-left: auto; background: none; border: 1px solid #2d2d4a; border-radius: 5px;
    color: #8a90a8; cursor: pointer; font-size: 11px; padding: 4px 10px; }
  .mj-full:hover { color: #e8e8f0; border-color: #4a4a7a; }
  .mj-close { margin-left: 0; background: none; border: 1px solid #2d2d4a; border-radius: 5px;
    color: #8a90a8; cursor: pointer; padding: 4px 9px; }
  .mj-err { margin-bottom: 10px; padding: 8px 10px; border-radius: 6px;
    background: #3a1616; border: 1px solid #ff6b5a; color: #ff9d90; }

  .mj-seg { display: flex; border: 1px solid #2d2d4a; border-radius: 6px; overflow: hidden; }
  .mj-seg button { background: #0e0e1e; border: 0; color: #8a90a8; cursor: pointer;
    font: 600 12px/1 system-ui, sans-serif; padding: 8px 14px; }
  .mj-seg button + button { border-left: 1px solid #2d2d4a; }
  .mj-seg button.on { background: #1d3557; color: #dfe6ff; box-shadow: inset 0 -2px 0 #4f8bd6; }
  .mj-seg.sm button { padding: 5px 9px; font-size: 10px; }

  .mj-total { background: #12122480; border: 1px solid #23233f; border-radius: 8px;
    padding: 12px 14px; margin-bottom: 12px; }
  .mj-t-main { display: flex; align-items: baseline; gap: 12px; }
  .mj-t-k { font-size: 11px; letter-spacing: .1em; text-transform: uppercase; color: #8a90a8; }
  .mj-t-v { margin-left: auto; font: 22px/1 Consolas, monospace; color: #e8e8f0; }
  .mj-t-v.pos, .mj-o-v.pos { color: #7ef0a6; }
  .mj-t-v.neg, .mj-o-v.neg { color: #ff9d90; }
  .mj-t-sub { margin-top: 6px; color: #8a90a8; }
  .mj-method { margin-top: 8px; padding: 7px 9px; border-radius: 6px; line-height: 1.5;
    background: #16203a; border: 1px solid #2a3c5e; color: #9aa8c4; font-size: 11px; }
  .mj-warn { margin-top: 8px; padding: 7px 9px; border-radius: 6px; line-height: 1.5;
    background: #2a2416; border: 1px solid #6b5a2a; color: #e0c98a; }
  .mj-open.bad { border-top-color: #6b2a2a; }
  .mj-open.bad .mj-o-k { color: #ff9d90; }
  .mj-open { margin-top: 10px; padding-top: 9px; border-top: 1px dashed #2d2d4a;
    display: grid; gap: 3px; }
  .mj-o-k { font-size: 11px; letter-spacing: .08em; text-transform: uppercase; color: #8a90a8; }
  .mj-o-v { font: 16px/1 Consolas, monospace; }
  .mj-open em { font-style: normal; font-size: 10px; color: #8a90a8; }
  .mj-o-rows { display: grid; gap: 2px; font: 11px/1.5 Consolas, monospace; color: #9aa0b4; }

  .mj-breaks { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 12px;
    margin-bottom: 12px; }
  .mj-break { background: #12122480; border: 1px solid #23233f; border-radius: 8px; padding: 10px 12px; }
  .mj-b-h { display: flex; align-items: center; gap: 8px; font-size: 10px; letter-spacing: .12em;
    text-transform: uppercase; color: #8a90a8; margin-bottom: 8px; }
  .mj-t { width: 100%; border-collapse: collapse; }
  .mj-t th { text-align: left; font-size: 10px; font-weight: 500; color: #6f7590;
    padding: 3px 6px; border-bottom: 1px solid #23233f; }
  .mj-t td { padding: 4px 6px; border-bottom: 1px solid #1a1a2e; }
  .mj-nopv { margin-left: 5px; font: 9px/1 system-ui, sans-serif; color: #e0a53c;
    border: 1px solid #6b5a2a; border-radius: 3px; padding: 1px 4px; }

  .mj-feed { background: #12122480; border: 1px solid #23233f; border-radius: 8px; padding: 10px 12px; }
  .mj-f-h { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 8px;
    font-size: 10px; letter-spacing: .12em; text-transform: uppercase; color: #8a90a8; }
  .mj-search { flex: 1 1 200px; min-width: 160px; background: #0e0e1e; border: 1px solid #2d2d4a;
    border-radius: 5px; color: #d7dae8; font-size: 11px; padding: 5px 8px; text-transform: none; }
  .mj-sel { background: #0e0e1e; border: 1px solid #2d2d4a; border-radius: 5px; color: #d7dae8;
    font: 11px/1 Consolas, monospace; padding: 4px 6px; text-transform: none; }
  .mj-csv { background: none; border: 1px solid #2d2d4a; border-radius: 4px; color: #8a90a8;
    cursor: pointer; font-size: 10px; padding: 3px 8px; }
  .mj-csv:hover { color: #e8e8f0; border-color: #4a4a7a; }
  .mj-empty { color: #6f7590; }
  .mj-note { display: block; margin-top: 6px; font-style: normal; font-size: 10px; color: #6f7590; }
  .mj-days { grid-column: 1 / -1; }
  .mj-bars { display: flex; align-items: flex-end; gap: 4px; height: 60px; }
  .mj-bar { display: flex; flex-direction: column; align-items: center; justify-content: flex-end;
    gap: 2px; min-width: 14px; }
  .mj-bar-fill { width: 12px; background: #2ecc71; border-radius: 2px 2px 0 0; }
  .mj-bar-fill.neg { background: #ff6b5a; }
  .mj-bar-d { font: 9px/1 Consolas, monospace; color: #6f7590; }

  .mj-row { display: flex; align-items: baseline; gap: 10px; padding: 4px 6px;
    border-bottom: 1px solid #1a1a2e; }
  .mj-row.trade { background: #14142a; }
  .mj-ts { font-size: 11px; color: #8a90a8; flex: none; }
  .mj-ev { min-width: 150px; color: #e8e8f0; }
  .mj-src { min-width: 150px; color: #7ec8f0; }
  .mj-code { min-width: 60px; color: #d7dae8; }
  .mj-side { min-width: 90px; color: #ff9d90; }
  .mj-side.buy { color: #7ef0a6; }
  .mj-px { margin-left: auto; color: #d7dae8; }
  .mj-more { background: none; border: 1px solid #2d2d4a; border-radius: 4px; color: #8a90a8;
    cursor: pointer; font-size: 10px; padding: 2px 7px; flex: none; }
  .mj-detail { padding: 5px 12px 9px; line-height: 1.5; color: #b9bfd4;
    border-bottom: 1px solid #1a1a2e; }
  .mono { font-family: Consolas, 'Cascadia Mono', monospace; }
  .dim { color: #6f7590; font-size: 10px; margin-top: 3px; }
</style>
