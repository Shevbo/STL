<!-- Рабочее место карточки: редакции, воркер, лог, diff, приёмка.

     Заказ оператора 04.10.2026 (docs/backtest-workbench-spec.md). Оператор просит модель
     внести правку; её делает отдельный воркер на smain, этот экран только отправляет
     просьбу и показывает, что вышло. Правила (кто что может) на сервере, экран заранее
     говорит ПОЧЕМУ кнопка не работает.

     ЧУЖОЙ ТЕКСТ. message написал оператор для модели, а log и diff — модель. Всё это
     показывается ТОЛЬКО как текст (никакого {@html}) и ничего из него не исполняется.

     «ПРИНЯТЬ» НИЧЕГО НЕ СЛИВАЕТ И НЕ РЕЛИЗИТ: переводит статус и записывает, кто и какой
     diff принял. Слияние в main делает окно backtests по явной команде, релиз — real-trade. -->
<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import { fetchWithAuth } from '$lib/fetch-auth';
  import {
    MESSAGE_MAX, canAccept, canCreate, errorText, gatesSummary, isOpen, messageError,
    statusLabel, statusTone, workerLine, type Revision, type WorkerState,
  } from '$lib/workbench';

  let { card }: { card: string } = $props();

  const API = '/api/v1/lab/workbench';
  let worker = $state<WorkerState | null>(null);
  let operatorsConfigured = $state<boolean | null>(null);
  let revs = $state<Revision[]>([]);
  let loaded = $state(false);
  let loadErr = $state('');
  let picked = $state<number | null>(null);
  let detail = $state<Revision | null>(null);
  let detailErr = $state('');

  let composing = $state(false);
  let message = $state('');
  let sending = $state(false);
  let actionErr = $state('');
  let reviewed = $state(false);
  let acting = $state(false);
  let note = $state('');

  let timer: ReturnType<typeof setTimeout> | null = null;
  let alive = true;

  async function load() {
    try {
      const [s, l] = await Promise.all([
        fetchWithAuth(`${API}/status`), fetchWithAuth(`${API}/cards/${encodeURIComponent(card)}/revisions`)]);
      if (!s.ok || !l.ok) {
        // Рабочее место недоступно — ГОВОРИМ об этом, а не рисуем пустой список редакций.
        const bad = !s.ok ? s : l;
        loadErr = errorText(bad.status, await bad.json().catch(() => null));
        return;
      }
      const sd = await s.json();
      worker = sd.worker as WorkerState;
      operatorsConfigured = !!sd.operators_configured;
      revs = ((await l.json()).revisions ?? []) as Revision[];
      loadErr = ''; loaded = true;
      if (picked != null) await loadDetail(picked, false);
    } catch (e: any) { loadErr = e?.message || 'нет связи'; }
  }

  async function loadDetail(n: number, reset = true) {
    if (reset) { detail = null; detailErr = ''; reviewed = false; note = ''; }
    try {
      const r = await fetchWithAuth(`${API}/cards/${encodeURIComponent(card)}/revisions/${n}`);
      if (picked !== n) return;
      if (!r.ok) { detailErr = errorText(r.status, await r.json().catch(() => null)); return; }
      detail = await r.json();
    } catch (e: any) { detailErr = e?.message || 'нет связи'; }
  }

  function pick(n: number) { picked = n; loadDetail(n); }

  // Опрос: пока есть редакция в работе — часто, иначе редко. Воркер живёт секундами,
  // а оператор смотрит на экран и ждёт результата.
  function schedule() {
    if (!alive) return;
    const fast = revs.some(isOpen);
    timer = setTimeout(async () => { await load(); schedule(); }, fast ? 4000 : 20000);
  }
  onMount(async () => { await load(); schedule(); });
  onDestroy(() => { alive = false; if (timer) clearTimeout(timer); });

  const can = $derived(canCreate(worker, revs, loaded));
  const msgErr = $derived(composing ? messageError(message) : '');

  async function send() {
    if (sending || messageError(message)) return;
    sending = true; actionErr = '';
    try {
      const r = await fetchWithAuth(`${API}/cards/${encodeURIComponent(card)}/revisions`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        // parent не шлём: сервер строит от последней, а устаревшую страницу ловит сам.
        body: JSON.stringify({ message, parent: revs.length ? Math.max(...revs.map((x) => x.rev)) : null }),
      });
      const d = await r.json().catch(() => null);
      if (!r.ok) { actionErr = errorText(r.status, d); await load(); return; }
      composing = false; message = '';
      await load();
      pick(d.rev);
    } catch (e: any) { actionErr = e?.message || 'нет связи'; }
    finally { sending = false; }
  }

  async function act(kind: 'cancel' | 'accept') {
    if (!detail || acting) return;
    acting = true; actionErr = '';
    try {
      const r = await fetchWithAuth(`${API}/cards/${encodeURIComponent(card)}/revisions/${detail.rev}/${kind}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: kind === 'accept' ? JSON.stringify({ diff_sha: detail.diff_sha }) : undefined,
      });
      const d = await r.json().catch(() => null);
      if (!r.ok) { actionErr = errorText(r.status, d); await load(); return; }
      note = kind === 'accept'
        ? 'Принято. Статус записан; слияние в main и релиз — отдельный шаг: окно backtests по вашей команде, рестарты только real-trade.'
        : 'Отменено.';
      await load();
    } catch (e: any) { actionErr = e?.message || 'нет связи'; }
    finally { acting = false; }
  }

  const when = (ms?: number | null) => ms ? new Date(ms).toLocaleString('ru-RU',
    { timeZone: 'Europe/Moscow', day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }) : '—';
  const acceptState = $derived(canAccept(detail, reviewed));
  const gates = $derived(gatesSummary(detail?.gates));
</script>

<section class="wb" aria-label="Рабочее место: редакции карточки">
  <div class="wb-sec"><h3>Редакции и правка кода</h3></div>

  {#if loadErr}
    <!-- Рабочее место недоступно: так и говорим, а не рисуем пустой список редакций. -->
    <div class="wb-note bad">Рабочее место недоступно: {loadErr}.</div>
  {:else}
    <div class="wb-worker" class:down={worker && !worker.alive}>
      <span class="wb-dot" class:ok={worker?.alive}></span>{workerLine(worker)}
    </div>

    <div class="wb-actions">
      <button class="wb-btn primary" disabled={!can.ok || composing}
              title={can.ok ? 'попросить модель внести правку: появится новая редакция' : can.why}
              onclick={() => { composing = true; actionErr = ''; }}>Создать новую редакцию</button>
      {#if !can.ok && loaded}<span class="wb-why">{can.why}</span>{/if}
    </div>

    {#if composing}
      <div class="wb-compose">
        <label for="wb-msg">Что изменить (условия, триггеры, правки):</label>
        <textarea id="wb-msg" rows="5" bind:value={message} maxlength={MESSAGE_MAX * 2}
                  placeholder="например: добавь фильтр входа по ADX и поставь стоп за последним экстремумом"></textarea>
        <div class="wb-row">
          <span class="wb-count" class:over={message.length > MESSAGE_MAX}>{message.length} / {MESSAGE_MAX}</span>
          {#if msgErr}<span class="wb-err">{msgErr}</span>{/if}
          <span class="wb-sp"></span>
          <button class="wb-btn" onclick={() => { composing = false; message = ''; }}>отмена</button>
          <button class="wb-btn primary" disabled={sending || !!msgErr || !can.ok} onclick={send}>
            {sending ? 'отправляю…' : 'отправить воркеру'}</button>
        </div>
        <div class="wb-hint">Правку делает воркер в отдельной ветке <code>wb/{card}/N</code>, с тестами; в рабочий код
          она не попадает, пока вы её не примете.</div>
      </div>
    {/if}
    {#if actionErr}<div class="wb-note bad">{actionErr}</div>{/if}
    {#if note}<div class="wb-note">{note}</div>{/if}

    {#if !loaded}
      <div class="wb-note">Загрузка…</div>
    {:else if !revs.length}
      <div class="wb-note">Редакций пока нет.</div>
    {:else}
      <ul class="wb-list">
        {#each [...revs].reverse() as r (r.id)}
          <li class:on={r.rev === picked}>
            <button class="wb-item" onclick={() => pick(r.rev)}>
              <b>ред. {r.rev}</b>
              <span class={`wb-badge ${statusTone(r.status)}`}>{statusLabel(r.status)}</span>
              <span class="wb-msg">{r.change_note || r.message}</span>
              <span class="wb-when">{when(r.created_at)}</span>
            </button>
          </li>
        {/each}
      </ul>
    {/if}

    {#if picked != null}
      {#if detailErr}
        <div class="wb-note bad">{detailErr}</div>
      {:else if !detail}
        <div class="wb-note">Загрузка редакции…</div>
      {:else}
        <div class="wb-detail">
          <div class="wb-row">
            <b>Редакция {detail.rev}</b>
            <span class={`wb-badge ${statusTone(detail.status)}`}>{statusLabel(detail.status)}</span>
            {#if detail.code_ref}<span class="wb-ref" title="ветка и коммит с правкой">{detail.code_ref}</span>{/if}
          </div>
          <!-- message, log и diff — ЧУЖОЙ текст: только как текст. -->
          <div class="wb-lbl">Просьба</div>
          <pre class="wb-pre small">{detail.message}</pre>
          {#if gates.total}
            <div class="wb-lbl">Ворота: {gates.green} из {gates.total} зелёных</div>
            <ul class="wb-gates">
              {#each gates.items as g (g.name)}<li class:ok={g.ok}>{g.ok ? '✓' : '✗'} {g.name}</li>{/each}
            </ul>
          {:else if detail.status !== 'queued'}
            <div class="wb-lbl">Ворот пока нет</div>
          {/if}
          {#if detail.diff}
            <div class="wb-lbl">Diff ({detail.diff.length.toLocaleString('ru-RU')} знаков)</div>
            <pre class="wb-pre">{detail.diff}</pre>
          {/if}
          {#if detail.log}
            <div class="wb-lbl">Лог воркера</div>
            <pre class="wb-pre small">{detail.log}</pre>
          {/if}

          {#if detail.status === 'ready'}
            <label class="wb-chk"><input type="checkbox" bind:checked={reviewed} /> я просмотрел diff и ворота</label>
          {/if}
          <div class="wb-actions">
            {#if isOpen(detail)}
              <button class="wb-btn" disabled={acting} onclick={() => act('cancel')}>Отменить</button>
            {/if}
            {#if detail.status === 'ready' || detail.status === 'accepted'}
              <button class="wb-btn primary" disabled={!acceptState.ok || acting}
                      title={acceptState.ok ? 'принять этот diff' : acceptState.why}
                      onclick={() => act('accept')}>Принять</button>
              {#if !acceptState.ok && detail.status !== 'accepted'}<span class="wb-why">{acceptState.why}</span>{/if}
              {#if operatorsConfigured === false}
                <span class="wb-why">список операторов приёмки на сервере не задан: принять пока некому</span>
              {/if}
            {/if}
          </div>
          {#if detail.status === 'accepted'}
            <div class="wb-sub">Принято: {detail.accepted_by} · {when(detail.accepted_at)}. Слияние в main и релиз —
              отдельным шагом (окно backtests по вашей команде).</div>
          {/if}
        </div>
      {/if}
    {/if}
  {/if}
</section>

<style>
  .wb { margin-top: 26px; --pos: #6fa77a; --neg: #c47a7a; --accent: #4dd0e1; --warn: #e0a35c;
        --bd: #2d2d4a; --panel: #14142a; --ink: #e2e6f0; --muted: #8a90a8; --faint: #6f7590; }
  @media (prefers-color-scheme: light) {
    .wb { --pos: #4f8a5a; --neg: #b25f5f; --accent: #2b5fb0; --warn: #a5661b;
          --bd: #d9d5c6; --panel: #fcfbf6; --ink: #222630; --muted: #6a6f80; --faint: #8b8f9c; }
  }
  .wb-sec h3 { margin: 0 0 8px; font-size: 11px; letter-spacing: .14em; text-transform: uppercase; color: var(--muted); }
  .wb-worker { display: flex; align-items: center; gap: 8px; font-size: 12px; color: var(--muted); margin-bottom: 8px; }
  .wb-worker.down { color: var(--warn); }
  .wb-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--faint); display: inline-block; }
  .wb-dot.ok { background: var(--pos); }
  .wb-actions { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; margin: 8px 0; }
  .wb-why { color: var(--warn); font-size: 12px; }
  .wb-btn { background: var(--panel); color: var(--muted); border: 1px solid var(--bd); border-radius: 4px;
            padding: 5px 12px; font-size: 12px; cursor: pointer; min-height: 32px; }
  .wb-btn:hover:not(:disabled) { color: var(--ink); }
  .wb-btn.primary { color: var(--accent); border-color: var(--accent); }
  .wb-btn:disabled { opacity: .5; cursor: default; }
  .wb-compose { display: grid; gap: 6px; margin: 8px 0; padding: 10px; border: 1px solid var(--bd); border-radius: 6px; background: var(--panel); }
  .wb-compose label { font-size: 12px; color: var(--muted); }
  .wb-compose textarea { width: 100%; box-sizing: border-box; background: var(--panel); color: var(--ink);
    border: 1px solid var(--bd); border-radius: 4px; padding: 8px; font: 13px/1.45 inherit; resize: vertical; }
  .wb-row { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
  .wb-sp { flex: 1; }
  .wb-count { font-size: 11px; color: var(--faint); }
  .wb-count.over, .wb-err { color: var(--neg); font-size: 12px; }
  .wb-hint, .wb-sub { font-size: 11px; color: var(--faint); }
  .wb-note { margin: 8px 0; padding: 10px 12px; border: 1px dashed var(--bd); border-radius: 6px; color: var(--muted); font-size: 12px; }
  .wb-note.bad { color: var(--neg); border-color: var(--neg); }
  .wb-list { list-style: none; margin: 8px 0; padding: 0; display: grid; gap: 4px; }
  .wb-list li.on .wb-item { border-color: var(--accent); }
  .wb-item { width: 100%; display: flex; gap: 10px; align-items: baseline; text-align: left; background: var(--panel);
             color: var(--ink); border: 1px solid var(--bd); border-radius: 5px; padding: 6px 10px; cursor: pointer; font-size: 12px; }
  .wb-msg { flex: 1; min-width: 0; color: var(--muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .wb-when { color: var(--faint); font-size: 11px; white-space: nowrap; }
  .wb-badge { font-size: 10px; font-weight: 600; padding: 1px 7px; border-radius: 10px; border: 1px solid currentColor; white-space: nowrap; }
  .wb-badge.ok { color: var(--pos); } .wb-badge.run { color: var(--accent); } .wb-badge.wait { color: var(--warn); }
  .wb-badge.bad { color: var(--neg); } .wb-badge.done { color: var(--pos); } .wb-badge.unk { color: var(--faint); }
  .wb-detail { margin-top: 12px; padding: 12px; border: 1px solid var(--bd); border-radius: 6px; background: var(--panel); }
  .wb-lbl { margin: 12px 0 4px; font-size: 10px; letter-spacing: .12em; text-transform: uppercase; color: var(--muted); }
  .wb-ref { font: 11px ui-monospace, Consolas, monospace; color: var(--muted); }
  .wb-pre { margin: 0; padding: 8px; max-height: 360px; overflow: auto; background: color-mix(in srgb, var(--panel) 70%, black);
            border: 1px solid var(--bd); border-radius: 4px; font: 11px/1.45 ui-monospace, Consolas, monospace;
            white-space: pre-wrap; overflow-wrap: anywhere; color: var(--ink); }
  .wb-pre.small { max-height: 180px; }
  .wb-gates { list-style: none; margin: 0; padding: 0; display: flex; gap: 12px; flex-wrap: wrap; font-size: 12px; color: var(--neg); }
  .wb-gates li.ok { color: var(--pos); }
  .wb-chk { display: flex; align-items: center; gap: 6px; margin-top: 12px; font-size: 12px; color: var(--muted); cursor: pointer; }
  @media (max-width: 560px) { .wb-item { flex-wrap: wrap; } .wb-msg { flex-basis: 100%; white-space: normal; } }
</style>
