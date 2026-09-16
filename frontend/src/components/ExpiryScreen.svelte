<!-- ExpiryScreen: кампания перекладки роботов и ручной позиции на следующий контракт
     (docs/design/expiry-roll.md, ТЗ раздела 10). Бэкенд — окно real-trade, маршруты
     раздела 9. Пока их нет, экран честно говорит об этом и НЕ рисует пустых таблиц:
     нарисованная пустота читается как «роботов нет», а это другое утверждение.

     Правило кампании, от которого зависит вся разметка: она НИКОГДА не закрывает
     позицию сама. Всё, что экран может, — показать застрявшего робота и дать
     оператору закрыть его руками, с вводом ID. -->
<script lang="ts">
  import { fetchWithAuth, errText } from '../lib/fetch-auth';
  import { downloadCSV } from '$lib/csv';
  import { canStart, confirmMatches, leftToDeadline, robotsCsv, rollSummary,
           stateLabel, stateTone, type Check } from '$lib/expiry-help';

  let { onClose }: { onClose?: () => void } = $props();

  const API = '/api/v1/quik/expiry';

  let series = $state<any[]>([]);
  let camp = $state<any | null>(null);
  let checks = $state<Check[]>([]);
  let loading = $state(true);
  let error = $state('');
  let backendMissing = $state(false);     // маршрутов ещё нет (404) — это не ошибка оператора
  let notice = $state('');
  let busy = $state(false);
  let openRobot = $state<string | null>(null);
  let preview = $state<any | null>(null);
  let confirmCode = $state('');
  let rollMode = $state<'taker' | 'maker'>('maker');   // решение оператора 16.09: лимитными
  let transferIds = $state<Record<string, boolean>>({});
  let flatId = $state<Record<string, string>>({});

  async function call(path: string, init?: RequestInit): Promise<any | null> {
    try {
      const res = await fetchWithAuth(API + path, init);
      if (res.status === 404) { backendMissing = true; return null; }
      backendMissing = false;
      if (!res.ok) { error = await errText(res); return null; }
      error = '';
      return await res.json();
    } catch (e) {
      error = String(e);
      return null;
    }
  }

  async function loadSeries() {
    const d = await call('/series');
    if (d) series = d.series || d || [];
    loading = false;
  }

  async function loadCampaign(id?: string) {
    const d = await call(id ? `/campaigns/${encodeURIComponent(id)}` : '/campaigns');
    if (!d) return;
    camp = Array.isArray(d.campaigns) ? (d.campaigns[0] ?? null) : (d.campaign ?? d);
    checks = camp?.checkup ?? [];
  }

  async function createCampaign(base: string) {
    busy = true;
    const d = await call('/campaigns', { method: 'POST', body: JSON.stringify({ base }) });
    busy = false;
    if (d) { camp = d.campaign ?? d; checks = camp?.checkup ?? []; }
  }

  async function runCheckup() {
    if (!camp) return;
    busy = true;
    const d = await call(`/campaigns/${camp.id}/checkup`, { method: 'POST' });
    busy = false;
    if (d) checks = d.checks ?? d ?? [];
  }

  async function start() {
    if (!camp || !canStart(checks)) return;
    busy = true;
    const d = await call(`/campaigns/${camp.id}/start`, { method: 'POST' });
    busy = false;
    if (d) await loadCampaign(camp.id);
  }

  async function cancel() {
    if (!camp) return;
    const reason = prompt('Причина отмены кампании:');
    if (!reason) return;
    busy = true;
    await call(`/campaigns/${camp.id}/cancel`, { method: 'POST', body: JSON.stringify({ reason }) });
    busy = false;
    await loadCampaign(camp.id);
  }

  async function toggleRobot(robotId: string, include: boolean) {
    if (!camp) return;
    await call(`/campaigns/${camp.id}/robots`,
               { method: 'POST', body: JSON.stringify({ robot_id: robotId, include }) });
    await loadCampaign(camp.id);
  }

  // ── Кнопки в строке робота зовут СУЩЕСТВУЮЩИЕ маршруты роботов; кампания их не
  //    оборачивает (ТЗ, п.4). Закрытие по рынку — реальные деньги, поэтому оператор
  //    вводит ID робота прямо в строке, как на его карточке.
  async function robotAction(path: string, body: any, okText: string) {
    busy = true;
    try {
      const res = await fetchWithAuth(path, { method: 'POST', body: JSON.stringify(body || {}) });
      if (!res.ok) { error = await errText(res); return; }
      notice = okText;
      error = '';
    } catch (e) { error = String(e); }
    finally { busy = false; }
    if (camp) await loadCampaign(camp.id);
  }

  function flatten(r: any) {
    if (flatId[r.robot_id]?.trim() !== r.robot_id) {
      error = 'Для закрытия по рынку введите точный ID робота в его строке.';
      return;
    }
    robotAction(`/api/v1/quik/robots/${encodeURIComponent(r.robot_id)}/flatten-agent`, {},
                `Закрытие по рынку отправлено: ${r.name}`);
  }

  function pause(r: any) {
    robotAction(`/api/v1/quik/robots/${encodeURIComponent(r.robot_id)}/pause-agent`, {},
                `Пауза: ${r.name}`);
  }

  function resetPaper(r: any) {
    if (r.mode !== 'paper') { error = 'Обнуление позиции — только для бумажного робота.'; return; }
    if (!r.paused) { error = 'Сначала пауза: обнулять книгу торгующего робота нельзя.'; return; }
    robotAction(`/api/v1/quik/robots/${encodeURIComponent(r.robot_id)}/set-position-agent`,
                { position: 0, avg_price: 0, confirm_id: r.robot_id },
                `Книга обнулена: ${r.name}`);
  }

  // ── Ручная позиция (шаг 3) ────────────────────────────────────────────────
  async function loadPreview() {
    if (!camp) return;
    const d = await call(`/campaigns/${camp.id}/manual/preview`);
    if (d) { preview = d; confirmCode = ''; transferIds = {}; }
  }

  async function rollManual() {
    if (!camp || !preview) return;
    if (!confirmMatches(confirmCode, camp.new)) {
      error = `Подтвердите перекладку: введите код нового контракта (${camp.new}).`;
      return;
    }
    busy = true;
    const d = await call(`/campaigns/${camp.id}/manual/roll`, {
      method: 'POST',
      body: JSON.stringify({
        confirm: camp.new, mode: rollMode,
        transfer_so_ids: Object.entries(transferIds).filter(([, v]) => v).map(([k]) => k),
      }),
    });
    busy = false;
    if (d) { preview = null; await loadCampaign(camp.id); }
  }

  async function completeLeg(leg: string) {
    if (!camp) return;
    if (!confirmMatches(prompt(`Довести остаток ноги ${leg}. Введите код нового контракта:`) || '', camp.new)) {
      error = 'Подтверждение не совпало — остаток не доводился.';
      return;
    }
    busy = true;
    await call(`/campaigns/${camp.id}/manual/complete-leg`,
               { method: 'POST', body: JSON.stringify({ leg }) });
    busy = false;
    await loadCampaign(camp.id);
  }

  let summary = $derived(rollSummary(camp?.robots));
  let left = $derived(leftToDeadline(camp?.deadline_ms));
  let startable = $derived(canStart(checks));

  $effect(() => { loadSeries(); loadCampaign(); });

  // Чекап сам обновляется раз в 30 с (ТЗ п.3): оператор смотрит на экран и ждёт,
  // когда позеленеет, а не жмёт «проверить» вручную каждые полминуты.
  $effect(() => {
    const t = setInterval(() => { if (camp && !busy) loadCampaign(camp.id); }, 30_000);
    return () => clearInterval(t);
  });
</script>

<div class="exp">
  <div class="exp-top">
    <span class="exp-id" title="ID кампании и ссылка на этот экран — копируются">
      {camp?.id ?? 'кампании нет'} · {typeof location !== 'undefined' ? location.href : '/?expiry=1'}
    </span>
    <span class="exp-sp"></span>
    {#if onClose}<button class="exp-btn" onclick={onClose}>✕ закрыть</button>{/if}
  </div>

  <h2 class="exp-h">Экспирация: перекладка контрактов</h2>

  {#if backendMissing}
    <div class="exp-warn">
      Бэкенд кампании ещё не поднят (маршруты {API}/… отвечают 404). Экран готов и
      включится сам, как только окно real-trade выложит раздел 9 проекта.
    </div>
  {/if}
  {#if error}<div class="exp-err">{error}</div>{/if}
  {#if notice}<div class="exp-ok">{notice}</div>{/if}

  <!-- 1. Серии: что вообще истекает -->
  <div class="exp-sec">Серии</div>
  {#if loading}
    <div class="exp-dim">загружаю…</div>
  {:else if !series.length}
    <div class="exp-dim">серий с позициями и роботами нет</div>
  {:else}
    <table class="exp-t">
      <thead>
        <tr><th>серия</th><th>старый</th><th>новый</th><th>экспирация</th><th class="num">дней</th>
          <th class="num">роботов</th><th class="num">ручная</th><th></th></tr>
      </thead>
      <tbody>
        {#each series as s (s.base)}
          <tr>
            <td><b>{s.base}</b></td><td>{s.old}</td><td>{s.new ?? '—'}</td>
            <td>{s.last_trade_date ?? '—'}</td>
            <td class="num">{s.days_left ?? '—'}</td>
            <td class="num">{(s.robots?.length ?? 0)}</td>
            <td class="num">{s.manual_net ?? 0}</td>
            <td><button class="exp-btn" disabled={busy || !s.new}
                        onclick={() => createCampaign(s.base)}>Создать кампанию</button></td>
          </tr>
        {/each}
      </tbody>
    </table>
  {/if}

  {#if camp}
    <!-- 2. Чекап готовности -->
    <div class="exp-sec">
      Чекап готовности
      <button class="exp-btn sm" disabled={busy} onclick={runCheckup}>Проверить сейчас</button>
      <span class="exp-sp"></span>
      <button class="exp-btn go" disabled={busy || !startable}
              title={startable ? 'запустить кампанию' : 'старт возможен только когда все обязательные проверки зелёные'}
              onclick={start}>Старт</button>
      <button class="exp-btn bad" disabled={busy} onclick={cancel}>Отменить кампанию</button>
    </div>
    {#if !checks.length}
      <div class="exp-dim">проверок ещё не было — нажмите «Проверить сейчас»</div>
    {:else}
      <ul class="exp-checks">
        {#each checks as c (c.key)}
          <li class={c.ok ? 'ok' : 'bad'}>
            <b>{c.ok ? '✓' : '✗'}</b> {c.text}
            {#if c.blocking === false}<span class="exp-dim"> (не блокирует старт)</span>{/if}
          </li>
        {/each}
      </ul>
    {/if}

    <!-- 3. Роботы -->
    <div class="exp-sec">
      Роботы: переключено {summary.done}/{summary.total}
      {#if summary.stuck}<span class="exp-bad"> · требуют человека: {summary.stuck}</span>{/if}
      {#if left}<span class="exp-dim"> · до дедлайна выхода {left}</span>{/if}
      <span class="exp-sp"></span>
      <button class="exp-btn sm" onclick={() => downloadCSV(robotsCsv(camp.robots || []), 'expiry-' + camp.id)}>CSV</button>
    </div>
    <table class="exp-t">
      <thead>
        <tr><th></th><th>робот</th><th>режим</th><th>контракт</th><th class="num">поз.</th>
          <th class="num">заявки</th><th class="num">бары</th><th>состояние</th><th>в состоянии</th><th></th></tr>
      </thead>
      <tbody>
        {#each (camp.robots || []) as r (r.robot_id)}
          <tr class="exp-row" class:off={r.state === 'skipped'}>
            <td><input type="checkbox" checked={r.state !== 'skipped'} disabled={busy}
                       title="участвует в кампании"
                       onchange={(e) => toggleRobot(r.robot_id, (e.target as HTMLInputElement).checked)} /></td>
            <td>
              <button class="exp-link" onclick={() => openRobot = openRobot === r.robot_id ? null : r.robot_id}>
                {openRobot === r.robot_id ? '▴' : '▾'} {r.name}
              </button>
            </td>
            <td class={r.mode === 'real' ? 'exp-real' : 'exp-dim'}>{r.mode === 'real' ? 'РЕАЛ' : 'бумага'}</td>
            <td>{r.symbol ?? camp.old}</td>
            <td class="num" class:exp-bad={r.position}>{r.position ?? 0}</td>
            <td class="num">{r.working_orders ?? 0}</td>
            <td class="num">{r.bars_count ?? '—'}</td>
            <td><span class="exp-tag {stateTone(r.state)}" title={r.note || ''}>{stateLabel(r.state)}</span></td>
            <td class="exp-dim">{r.state_since ?? ''}</td>
            <td class="exp-acts">
              <a class="exp-btn sm" href={`/?agent_robot=${encodeURIComponent(r.robot_id)}`}>карточка</a>
              <button class="exp-btn sm" disabled={busy} onclick={() => pause(r)}>пауза</button>
              {#if r.mode === 'paper'}
                <button class="exp-btn sm" disabled={busy} onclick={() => resetPaper(r)}>обнулить</button>
              {/if}
            </td>
          </tr>
          {#if openRobot === r.robot_id}
            <tr class="exp-sub">
              <td colspan="10">
                <div class="exp-note">{r.note || 'заметок нет'}</div>
                <div class="exp-flat">
                  Закрыть по рынку (реальные деньги): введите точный ID робота
                  <input class="exp-in" placeholder={r.robot_id}
                         value={flatId[r.robot_id] ?? ''}
                         oninput={(e) => flatId = { ...flatId, [r.robot_id]: (e.target as HTMLInputElement).value }} />
                  <button class="exp-btn bad" disabled={busy} onclick={() => flatten(r)}>закрыть по рынку</button>
                </div>
                <ul class="exp-ev">
                  {#each (camp.events || []).filter((e: any) => e.robot_id === r.robot_id).slice(-20).reverse() as e (e.ts_ms)}
                    <li>{new Date(e.ts_ms).toLocaleTimeString('ru-RU')} — {e.text}</li>
                  {/each}
                </ul>
              </td>
            </tr>
          {/if}
        {/each}
      </tbody>
    </table>

    <!-- 4. Ручная позиция -->
    <div class="exp-sec">Ручная позиция</div>
    <div class="exp-manual">
      <span>{camp.old}: <b>{camp.manual?.old_net ?? 0}</b></span>
      <span>{camp.new}: <b>{camp.manual?.new_net ?? 0}</b></span>
      <button class="exp-btn" disabled={busy || !!camp.manual?.blocked_reason} onclick={loadPreview}
              title={camp.manual?.blocked_reason || 'посчитать перекладку'}>Предпросмотр перекладки</button>
      {#if camp.manual?.blocked_reason}<span class="exp-dim">{camp.manual.blocked_reason}</span>{/if}
    </div>
    {#if camp.manual?.legs?.length}
      {#each camp.manual.legs as leg (leg.name)}
        <div class="exp-leg">
          {leg.name}: {leg.filled ?? 0}/{leg.total ?? 0} по {leg.avg_price ?? '—'} · {leg.state ?? ''}
        </div>
      {/each}
      {#if camp.manual.imbalance}
        <div class="exp-err">
          Дисбаланс ног: {camp.manual.imbalance}. Позиция не переложена целиком.
          <button class="exp-btn bad" disabled={busy}
                  onclick={() => completeLeg(camp.manual.imbalance_leg || 'new')}>довести остаток</button>
        </div>
      {/if}
    {/if}

    <!-- 5. Лента событий и очередь SMS -->
    <div class="exp-sec">События кампании</div>
    <ul class="exp-ev">
      {#each (camp.events || []).slice(-200).reverse() as e (e.ts_ms + (e.robot_id || ''))}
        <li>{new Date(e.ts_ms).toLocaleTimeString('ru-RU')} — {e.robot_id ? e.robot_id + ': ' : ''}{e.text}</li>
      {/each}
    </ul>
    <div class="exp-sec">Очередь SMS</div>
    <ul class="exp-ev">
      {#each (camp.sms_queue || []) as m (m.ts_ms)}
        <li class={m.sent ? '' : 'exp-bad'}>
          {new Date(m.ts_ms).toLocaleTimeString('ru-RU')} — {m.text} {m.sent ? '· отправлено' : '· ждёт отправки'}
        </li>
      {/each}
    </ul>
  {/if}
</div>

{#if preview}
  <div class="exp-modal-bg" role="presentation" onclick={() => preview = null}></div>
  <div class="exp-modal">
    <div class="exp-h">Перекладка ручной позиции {camp?.old} → {camp?.new}</div>
    <div class="exp-grid">
      <span>контрактов</span><b>{preview.qty}</b>
      <span>нога 1</span><b>{preview.leg_old_side} {camp?.old} × {preview.qty}</b>
      <span>нога 2</span><b>{preview.leg_new_side} {camp?.new} × {preview.qty}</b>
      <span>цены</span><b>{preview.old_price ?? '—'} / {preview.new_price ?? '—'}</b>
      <span>базис</span><b>{preview.basis ?? '—'}</b>
      <span>стоимость</span><b>{preview.cost_rub ?? '—'} ₽</b>
      <span>ГО после</span><b>{preview.margin_after_rub ?? '—'} ₽ (свободно {preview.free_after_rub ?? '—'} ₽)</b>
    </div>
    {#if preview.smart_orders?.length}
      <div class="exp-sec">Перенести на новый контракт</div>
      {#each preview.smart_orders as so (so.so_id)}
        <label class="exp-check">
          <input type="checkbox" checked={transferIds[so.so_id] ?? true}
                 onchange={(e) => transferIds = { ...transferIds, [so.so_id]: (e.target as HTMLInputElement).checked }} />
          {so.kind} {so.code} {so.side} {so.qty} · {so.trigger ?? ''}
        </label>
      {/each}
    {/if}
    <div class="exp-modes">
      <label><input type="radio" checked={rollMode === 'maker'} onchange={() => rollMode = 'maker'} /> лимитными с доводкой</label>
      <label><input type="radio" checked={rollMode === 'taker'} onchange={() => rollMode = 'taker'} /> по рынку</label>
    </div>
    <div class="exp-confirm">
      Подтвердите: введите код нового контракта <b>{camp?.new}</b>
      <input class="exp-in" bind:value={confirmCode} placeholder={camp?.new} spellcheck="false" />
      <button class="exp-btn go" disabled={busy || !confirmMatches(confirmCode, camp?.new)}
              onclick={rollManual}>Переложить</button>
      <button class="exp-btn" onclick={() => preview = null}>Отмена</button>
    </div>
  </div>
{/if}

<style>
  .exp { padding: 10px 14px 24px; font-size: 12px; color: #d7dae8; overflow: auto; height: 100%; }
  .exp-top { display: flex; align-items: baseline; gap: 10px; margin-bottom: 4px; }
  .exp-id { font-family: Consolas, monospace; font-size: 10px; color: #8a90a8; user-select: all; }
  .exp-sp { flex: 1; }
  .exp-h { font-size: 15px; margin: 4px 0 10px; color: #e8e8f0; }
  .exp-sec { display: flex; align-items: center; gap: 8px; margin: 14px 0 6px;
    font-size: 11px; letter-spacing: .08em; text-transform: uppercase; color: #8a90a8; }
  .exp-warn { padding: 8px 10px; border-left: 3px solid #e0a53c; color: #e0a53c; background: #14142a; }
  .exp-err { padding: 8px 10px; border-left: 3px solid #ff5252; color: #ff9d90; background: #14142a; margin: 6px 0; }
  .exp-ok { padding: 6px 10px; border-left: 3px solid #00e676; color: #7ef0a6; margin: 6px 0; }
  .exp-dim { color: #8a90a8; }
  .exp-bad { color: #ff9d90; }
  .exp-real { color: #7ef0a6; font-weight: 600; }
  .exp-t { width: 100%; border-collapse: collapse; font-size: 11px; }
  .exp-t th { text-align: left; color: #8a90a8; font-weight: 500; border-bottom: 1px solid #2d2d4a; padding: 3px 6px; }
  .exp-t td { padding: 3px 6px; border-bottom: 1px solid #1d1d33; }
  .exp-t .num { text-align: right; font-variant-numeric: tabular-nums; }
  .exp-row.off { opacity: .5; }
  .exp-sub td { background: #14142a; }
  .exp-tag { padding: 1px 6px; border: 1px solid; border-radius: 2px; font-size: 10px; }
  .exp-tag.wait { color: #e0a53c; border-color: #e0a53c; }
  .exp-tag.ok { color: #7ef0a6; border-color: #7ef0a6; }
  .exp-tag.bad { color: #ff5252; border-color: #ff5252; }
  .exp-tag.off { color: #8a90a8; border-color: #3a3a5a; }
  .exp-btn { background: #14142a; border: 1px solid #2d2d4a; color: #d7dae8; padding: 3px 8px;
    font-size: 11px; cursor: pointer; text-decoration: none; display: inline-block; }
  .exp-btn.sm { font-size: 10px; padding: 2px 6px; }
  .exp-btn.go { border-color: #00e676; color: #7ef0a6; }
  .exp-btn.bad { border-color: #ff5252; color: #ff9d90; }
  .exp-btn:disabled { opacity: .45; cursor: not-allowed; }
  .exp-link { background: none; border: none; color: #d7dae8; cursor: pointer; font-size: 11px; padding: 0; }
  .exp-acts { display: flex; gap: 4px; }
  .exp-checks { list-style: none; padding: 0; margin: 0; font-size: 11px; }
  .exp-checks li.ok { color: #7ef0a6; }
  .exp-checks li.bad { color: #ff9d90; }
  .exp-ev { list-style: none; padding: 0; margin: 0; font-size: 10px; color: #b9bfd4;
    max-height: 220px; overflow: auto; }
  .exp-note { color: #8a90a8; font-size: 11px; margin-bottom: 4px; }
  .exp-flat { display: flex; align-items: center; gap: 6px; font-size: 11px; margin-bottom: 6px; }
  .exp-manual { display: flex; align-items: center; gap: 12px; font-size: 12px; }
  .exp-leg { font-size: 11px; color: #b9bfd4; margin-top: 4px; }
  .exp-in { background: #0e0e1c; border: 1px solid #2d2d4a; color: #e8e8f0; padding: 3px 6px; font-size: 11px; }
  .exp-modal-bg { position: fixed; inset: 0; background: #000a; z-index: 40; }
  .exp-modal { position: fixed; z-index: 41; top: 8%; left: 50%; transform: translateX(-50%);
    width: min(560px, 92vw); max-height: 84vh; overflow: auto; background: #0e0e1c;
    border: 1px solid #2d2d4a; padding: 14px 16px; font-size: 12px; }
  .exp-grid { display: grid; grid-template-columns: max-content 1fr; gap: 3px 12px; }
  .exp-grid span { color: #8a90a8; }
  .exp-check { display: block; font-size: 11px; margin: 2px 0; }
  .exp-modes { display: flex; gap: 14px; margin: 10px 0; font-size: 11px; }
  .exp-confirm { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; font-size: 11px; }
  @media (max-width: 820px) {
    .exp { font-size: 15px; }
    .exp-t { font-size: 13px; }
    .exp-acts { flex-wrap: wrap; }
  }
</style>
