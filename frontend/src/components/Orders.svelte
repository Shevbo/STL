<!-- frontend/src/components/Orders.svelte
  "Заявки" — HUMAN-INITIATED QUIK order ticket (sprint02 Phase 2).

  A manual limit-order ticket (instrument from the whitelist, side, price, qty)
  with a CONFIRM dialog showing instrument/side/price/qty/notional + the
  maker-commission estimate. A working-orders + executions table. A prominent
  KILL-SWITCH button. The whole ticket is DISABLED when quik_trading_enabled is
  off (with the reason shown). Every action is operator-initiated + confirmed
  (Guard 3): nothing here auto-places. -->
<script lang="ts">
  import { commissionFor } from '$lib/lab-analytics';

  type AgentLimits = {
    trading_enabled: boolean;
    instrument_whitelist: string[];
    max_contracts_per_order: number;
    max_working_contracts: number;
    price_collar_frac: number;
    daily_order_cap: number;
    last_push_unix_ms: number;
  };
  type Cfg = {
    trading_enabled: boolean;
    max_contracts_per_order: number;
    max_working_contracts: number;
    price_collar_frac: number;
    instrument_whitelist: string[];
    daily_order_cap: number;
    agent_wired: boolean;
    agent_limits?: AgentLimits | null;
  };
  type OrderRow = {
    client_id: string; code: string; side: string; price: number;
    quantity: number; filled: number; remaining: number; state: string;
    order_id: string; ts_unix_ms: number; agent_id?: string; text?: string;
  };
  type ExecRow = {
    client_id: string; code: string; target: number; filled: number;
    avg_price: number; state: string; text: string; ts_unix_ms: number;
  };

  let cfg = $state<Cfg | null>(null);
  let orders = $state<OrderRow[]>([]);
  let execs = $state<ExecRow[]>([]);
  let msg = $state<string>('');

  // ticket fields
  let code = $state<string>('');
  let side = $state<'buy' | 'sell'>('buy');
  let price = $state<number>(0);
  let qty = $state<number>(1);

  // confirm dialog
  let confirming = $state(false);

  // 1b maker execution
  let execSide = $state<'buy' | 'sell'>('sell');
  let execQty = $state<number>(1);
  let execWorst = $state<number>(0);

  let tradingOn = $derived(!!cfg?.trading_enabled && !!cfg?.agent_wired);

  // Whitelist sync: STL is the source of truth and pushes its whitelist to the agent
  // on connect. Compare STL's whitelist to the agent's echoed effective whitelist so a
  // divergence (an order would be rejected "instrument not whitelisted") is visible
  // here instead of only on a rejected order.
  const norm = (a: string[]) => [...(a ?? [])].map((s) => s.trim().toLowerCase()).sort();
  let agentWl = $derived(cfg?.agent_limits?.instrument_whitelist ?? null);
  let whitelistSynced = $derived(
    !!agentWl && JSON.stringify(norm(cfg?.instrument_whitelist ?? [])) === JSON.stringify(norm(agentWl)),
  );

  // Live (maker) estimate: limit order resting in the book → broker fee only.
  let notional = $derived(price * qty);
  let makerFee = $derived(code ? commissionFor(code, price, qty, 1, false) : 0);

  async function loadConfig() {
    try {
      const r = await fetch('/api/v1/quik/orders/config', { credentials: 'include' });
      if (!r.ok) return;
      cfg = await r.json();
      if (!code && cfg && cfg.instrument_whitelist.length) {
        code = cfg.instrument_whitelist[0];
      }
    } catch (_) { /* keep previous */ }
  }

  async function loadTables() {
    try {
      const [ro, re] = await Promise.all([
        fetch('/api/v1/quik/orders/working', { credentials: 'include' }),
        fetch('/api/v1/quik/orders/executions', { credentials: 'include' }),
      ]);
      if (ro.ok) orders = (await ro.json()).orders ?? [];
      if (re.ok) execs = (await re.json()).executions ?? [];
    } catch (_) { /* keep previous */ }
  }

  function newClientId(): string {
    return 'cli-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 7);
  }

  function openConfirm() {
    msg = '';
    if (!tradingOn) return;
    if (!code) { msg = 'Укажите инструмент.'; return; }
    if (qty <= 0) { msg = 'Количество должно быть > 0.'; return; }
    if (price <= 0) { msg = 'Укажите цену.'; return; }
    confirming = true;
  }

  async function confirmPlace() {
    confirming = false;
    const cid = newClientId();
    try {
      const r = await fetch('/api/v1/quik/orders/place', {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          client_id: cid, code, side, price, quantity: qty,
        }),
      });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) { msg = 'Заявка отклонена STL: ' + (j.detail ?? '(без причины)'); return; }
      msg = 'Заявка отправлена агенту, ждём ответ QUIK…';
      await loadTables();
      // The agent/QUIK reply (accept or reject) arrives asynchronously. Poll the
      // working table briefly and surface the REJECT REASON prominently so a rejected
      // order is never silent ("nothing happened"). The agent rejects e.g. with
      // "instrument not whitelisted" when its own whitelist lacks the code.
      watchPlacement(cid);
    } catch (e) {
      msg = 'Ошибка отправки: ' + e;
    }
  }

  // Poll a few times for the just-placed order's terminal reply and reflect it in msg.
  async function watchPlacement(clientId: string): Promise<void> {
    for (let i = 0; i < 6; i++) {
      await new Promise((res) => setTimeout(res, 600));
      await loadTables();
      const o = orders.find((x) => x.client_id === clientId);
      if (!o) continue;
      if (o.state === 'rejected') {
        msg = 'Заявка ОТКЛОНЕНА: ' + (o.text || '(QUIK не указал причину)');
        return;
      }
      if (o.state === 'active' || o.state === 'partial') {
        msg = 'Заявка принята QUIK (в работе).';
        return;
      }
      if (o.state === 'filled') { msg = 'Заявка исполнена.'; return; }
    }
    // Still pending after the watch window: QUIK never acked. A healthy order replies in
    // ~1s; a stuck pending means the agent sent it but QUIK gave no OnTransReply/OnOrder
    // (terminal/DDE/broker-link issue). Surface it LOUDLY — the order may NOT have
    // reached the exchange, so the operator must verify in the QUIK terminal.
    const last = orders.find((x) => x.client_id === clientId);
    if (last && last.state === 'pending') {
      msg = '⚠ ЗАВИСЛА в pending — QUIK не ответил. Проверь терминал QUIK (DDE/связь с брокером): заявка могла НЕ дойти до биржи.';
    }
  }

  async function cancelOrder(o: OrderRow) {
    try {
      const r = await fetch('/api/v1/quik/orders/cancel', {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ client_id: o.client_id, order_id: o.order_id }),
      });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) { msg = j.detail ?? 'Отмена отклонена.'; return; }
      await loadTables();
    } catch (e) { msg = 'Ошибка отмены: ' + e; }
  }

  async function startExecution() {
    msg = '';
    if (!tradingOn) return;
    if (!code) { msg = 'Укажите инструмент.'; return; }
    if (execQty <= 0) { msg = 'Кол-во должно быть > 0.'; return; }
    if (execWorst <= 0) { msg = 'Укажите худшую цену (коллар).'; return; }
    const human = execSide === 'buy' ? 'Покупка' : 'Продажа';
    if (!confirm('1b мейкер-исполнение: ' + human + ' ' + execQty + ' ' + code +
                 ', не хуже ' + execWorst + '. Агент встаёт у спрэда, не пересекает. Запустить?')) return;
    try {
      const r = await fetch('/api/v1/quik/orders/start-execution', {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          client_id: newClientId(), code, side: execSide,
          target_quantity: execQty, worst_price: execWorst, allow_cross: false,
        }),
      });
      const j = await r.json().catch(() => ({}));
      msg = r.ok ? '1b запущено: ' + (j.client_id ?? '') : (j.detail ?? '1b отклонено.');
      await loadTables();
    } catch (e) { msg = 'Ошибка 1b: ' + e; }
  }

  async function stopExecution(clientId: string) {
    try {
      const r = await fetch('/api/v1/quik/orders/stop-execution', {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ client_id: clientId }),
      });
      const j = await r.json().catch(() => ({}));
      msg = r.ok ? '1b остановлено.' : (j.detail ?? 'Стоп не выполнен.');
      await loadTables();
    } catch (e) { msg = 'Ошибка стоп: ' + e; }
  }

  async function killSwitch() {
    if (!confirm('KILL-SWITCH: отменить ВСЕ заявки и заблокировать новые. Продолжить?')) return;
    try {
      const r = await fetch('/api/v1/quik/orders/kill-switch', {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ reason: 'operator UI kill-switch' }),
      });
      const j = await r.json().catch(() => ({}));
      msg = r.ok ? 'KILL-SWITCH активирован. Новые заявки заблокированы.'
                 : (j.detail ?? 'Kill-switch не выполнен.');
      await loadTables();
    } catch (e) { msg = 'Ошибка kill-switch: ' + e; }
  }

  function fmtTs(ms: number): string {
    if (!ms) return '';
    try { return new Date(ms).toLocaleTimeString('ru-RU'); } catch { return ''; }
  }

  // A pending order older than this with no QUIK ack is "stuck" (QUIK gave no reply).
  const STUCK_MS = 8000;
  let nowMs = $state(Date.now());
  function isStuck(o: OrderRow): boolean {
    return o.state === 'pending' && !!o.ts_unix_ms && nowMs - o.ts_unix_ms > STUCK_MS;
  }

  $effect(() => {
    loadConfig();
    loadTables();
    const t = setInterval(async () => { nowMs = Date.now(); await loadConfig(); await loadTables(); }, 4000);
    return () => clearInterval(t);
  });
</script>

<div class="orders">
  <div class="head">
    <span class="lbl">Заявки (QUIK)</span>
    <button class="kill" onclick={killSwitch} title="Отменить все заявки и заблокировать новые">
      KILL-SWITCH
    </button>
  </div>

  {#if !tradingOn}
    <div class="disabled-banner">
      Торговля QUIK отключена
      {#if cfg && !cfg.agent_wired}(агент не запущен){:else}(quik_trading_enabled = false){/if}.
      Тикет недоступен.
    </div>
  {/if}

  <!-- ticket -->
  <div class="ticket" class:off={!tradingOn}>
    <label>
      Инструмент
      <select bind:value={code} disabled={!tradingOn}>
        {#each (cfg?.instrument_whitelist ?? []) as w}
          <option value={w}>{w}</option>
        {/each}
      </select>
    </label>
    <label>
      Сторона
      <select bind:value={side} disabled={!tradingOn}>
        <option value="buy">Покупка</option>
        <option value="sell">Продажа</option>
      </select>
    </label>
    <label>
      Цена
      <input type="number" step="any" bind:value={price} disabled={!tradingOn} />
    </label>
    <label>
      Кол-во
      <input type="number" min="1" max={cfg?.max_contracts_per_order ?? 2}
             bind:value={qty} disabled={!tradingOn} />
    </label>
    <button class="place" onclick={openConfirm} disabled={!tradingOn}>Выставить</button>
  </div>

  <!-- 1b maker execution: agent joins the touch, never crosses, holds the collar -->
  <div class="ticket" class:off={!tradingOn}>
    <span class="exec-lbl">Мейкер 1b:</span>
    <label>
      Сторона
      <select bind:value={execSide} disabled={!tradingOn}>
        <option value="buy">Покупка</option>
        <option value="sell">Продажа</option>
      </select>
    </label>
    <label>
      Кол-во
      <input type="number" min="1" max={cfg?.max_working_contracts ?? 2}
             bind:value={execQty} disabled={!tradingOn} />
    </label>
    <label>
      Худшая цена
      <input type="number" step="any" bind:value={execWorst} disabled={!tradingOn} />
    </label>
    <button class="place" onclick={startExecution} disabled={!tradingOn}>Старт 1b</button>
  </div>

  {#if cfg}
    <div class="limits">
      макс/заявка: {cfg.max_contracts_per_order} ·
      макс в работе: {cfg.max_working_contracts} ·
      коллар: {(cfg.price_collar_frac * 100).toFixed(2)}% ·
      дневной лимит: {cfg.daily_order_cap}
    </div>
    <div class="sync">
      {#if !cfg.agent_limits}
        <span class="sync-dot unknown">●</span> лимиты агента: нет данных (агент не на сборке с синхронизацией)
      {:else if whitelistSynced}
        <span class="sync-dot ok">●</span> whitelist синхронизирован с агентом: {agentWl?.join(', ')}
      {:else}
        <span class="sync-dot warn">●</span> РАСХОЖДЕНИЕ whitelist — STL: [{cfg.instrument_whitelist.join(', ')}]
        · агент: [{agentWl?.join(', ') ?? '—'}] (заявки вне списка агента отклонятся)
      {/if}
    </div>
  {/if}

  {#if msg}<div class="msg">{msg}</div>{/if}

  <!-- working orders -->
  <div class="section-title">В работе</div>
  <div class="grid-wrap">
    <table>
      <thead>
        <tr><th>Время</th><th>Инстр.</th><th>Сторона</th><th>Цена</th>
          <th>Кол-во</th><th>Исполн.</th><th>Статус</th><th>Текст</th><th>ID</th><th></th></tr>
      </thead>
      <tbody>
        {#each orders as o}
          <tr>
            <td>{fmtTs(o.ts_unix_ms)}</td>
            <td>{o.code}</td>
            <td class:buy={o.side === 'buy'} class:sell={o.side === 'sell'}>
              {o.side === 'buy' ? 'Покупка' : o.side === 'sell' ? 'Продажа' : o.side}
            </td>
            <td>{o.price}</td>
            <td>{o.quantity}</td>
            <td>{o.filled}</td>
            <td class:rej={o.state === 'rejected'} class:stuck={isStuck(o)}>
              {isStuck(o) ? 'pending ⚠' : o.state}
            </td>
            <td class="txt" class:rej={o.state === 'rejected'} class:stuck={isStuck(o)}
                title={isStuck(o) ? 'Нет ответа QUIK — проверь терминал' : (o.text ?? '')}>
              {isStuck(o) ? 'завис: нет ответа QUIK — проверь терминал' : (o.text ?? '')}
            </td>
            <td>{o.order_id}</td>
            <td>
              {#if o.state === 'active' || o.state === 'partial' || o.state === 'pending'}
                <button class="x" onclick={() => cancelOrder(o)} disabled={!tradingOn}>×</button>
              {/if}
            </td>
          </tr>
        {/each}
        {#if !orders.length}<tr><td colspan="10" class="empty">нет заявок</td></tr>{/if}
      </tbody>
    </table>
  </div>

  <!-- executions -->
  <div class="section-title">Исполнение (мейкер)</div>
  <div class="grid-wrap">
    <table>
      <thead>
        <tr><th>Время</th><th>Инстр.</th><th>Цель</th><th>Исполн.</th>
          <th>Ср. цена</th><th>Статус</th><th>Текст</th><th></th></tr>
      </thead>
      <tbody>
        {#each execs as e}
          <tr>
            <td>{fmtTs(e.ts_unix_ms)}</td>
            <td>{e.code}</td>
            <td>{e.target}</td>
            <td>{e.filled}</td>
            <td>{e.avg_price}</td>
            <td>{e.state}</td>
            <td>{e.text}</td>
            <td>
              {#if e.state === 'working'}
                <button class="x" onclick={() => stopExecution(e.client_id)} disabled={!tradingOn}>стоп</button>
              {/if}
            </td>
          </tr>
        {/each}
        {#if !execs.length}<tr><td colspan="8" class="empty">нет исполнений</td></tr>{/if}
      </tbody>
    </table>
  </div>
</div>

{#if confirming}
  <div class="overlay" role="dialog" aria-modal="true">
    <div class="dialog">
      <h3>Подтверждение заявки</h3>
      <div class="row"><span>Инструмент</span><b>{code}</b></div>
      <div class="row"><span>Сторона</span>
        <b class:buy={side === 'buy'} class:sell={side === 'sell'}>
          {side === 'buy' ? 'Покупка' : 'Продажа'}
        </b>
      </div>
      <div class="row"><span>Цена</span><b>{price}</b></div>
      <div class="row"><span>Кол-во</span><b>{qty}</b></div>
      <div class="row"><span>Номинал</span><b>{notional.toLocaleString('ru-RU')}</b></div>
      <div class="row"><span>Комиссия (мейкер)</span><b>{makerFee.toFixed(2)} ₽</b></div>
      <div class="actions">
        <button class="cancel" onclick={() => (confirming = false)}>Отмена</button>
        <button class="ok" onclick={confirmPlace}>Подтвердить</button>
      </div>
    </div>
  </div>
{/if}

<style>
  .orders {
    display: flex; flex-direction: column; height: 100%;
    background: #14142a; color: #ccc; font-size: 12px; overflow: auto;
  }
  .head {
    display: flex; align-items: center; gap: 8px;
    padding: 4px 8px; border-bottom: 1px solid #2d2d4a; flex-shrink: 0;
  }
  .lbl { opacity: 0.85; font-weight: 600; }
  .kill {
    margin-left: auto; background: #6b1414; color: #fff; border: 1px solid #f44336;
    border-radius: 4px; padding: 3px 12px; font-weight: 700; cursor: pointer;
    letter-spacing: 0.5px;
  }
  .kill:hover { background: #8b1a1a; }
  .disabled-banner {
    background: #2a1414; color: #ffb4b4; padding: 4px 8px;
    border-bottom: 1px solid #5a2020; font-size: 11px;
  }
  .ticket {
    display: flex; flex-wrap: wrap; align-items: flex-end; gap: 8px;
    padding: 8px; border-bottom: 1px solid #2d2d4a;
  }
  .ticket.off { opacity: 0.5; }
  .ticket label { display: flex; flex-direction: column; gap: 2px; font-size: 11px; opacity: 0.85; }
  .ticket select, .ticket input {
    background: #14142a; color: #ccc; border: 1px solid #444;
    border-radius: 4px; padding: 2px 6px; font-size: 12px; width: 110px;
  }
  .place {
    background: #3d5af1; color: #fff; border: 1px solid #3d5af1;
    border-radius: 4px; padding: 4px 14px; cursor: pointer;
  }
  .place:disabled, .x:disabled { opacity: 0.4; cursor: not-allowed; }
  .exec-lbl { align-self: center; font-size: 11px; font-weight: 600; opacity: 0.8; color: #9ab; }
  .limits { padding: 2px 8px; font-size: 11px; opacity: 0.6; }
  .sync { padding: 0 8px 3px; font-size: 11px; opacity: 0.85; }
  .sync-dot { font-size: 10px; }
  .sync-dot.ok { color: #4caf50; }
  .sync-dot.warn { color: #ff6b6b; }
  .sync-dot.unknown { color: #888; }
  .msg { padding: 4px 8px; font-size: 11px; color: #ffd27f; }
  .section-title {
    padding: 4px 8px; font-size: 11px; opacity: 0.7; text-transform: uppercase;
    border-top: 1px solid #2d2d4a;
  }
  .grid-wrap { overflow: auto; max-height: 180px; }
  table { border-collapse: collapse; width: 100%; }
  thead th {
    position: sticky; top: 0; background: #1a1a32; color: #9ab;
    text-align: left; padding: 3px 8px; border-bottom: 1px solid #2d2d4a;
    white-space: nowrap; font-weight: 600;
  }
  tbody td {
    padding: 2px 8px; border-bottom: 1px solid #20203a; white-space: nowrap;
  }
  td.buy, b.buy { color: #4caf50; }
  td.sell, b.sell { color: #f44336; }
  td.rej { color: #ff6b6b; font-weight: 600; }
  td.stuck { color: #ffb74d; font-weight: 600; }
  td.txt { max-width: 220px; overflow: hidden; text-overflow: ellipsis; opacity: 0.85; }
  .empty { opacity: 0.5; text-align: center; }
  .x {
    background: transparent; color: #f44336; border: 1px solid #5a2020;
    border-radius: 3px; cursor: pointer; padding: 0 6px;
  }
  .overlay {
    position: fixed; inset: 0; background: rgba(0, 0, 0, 0.6);
    display: flex; align-items: center; justify-content: center; z-index: 1000;
  }
  .dialog {
    background: #1a1a2e; border: 1px solid #3d5af1; border-radius: 8px;
    padding: 16px 20px; min-width: 280px; color: #ddd;
  }
  .dialog h3 { margin: 0 0 12px; font-size: 14px; }
  .dialog .row {
    display: flex; justify-content: space-between; gap: 24px;
    padding: 3px 0; font-size: 13px;
  }
  .dialog .row span { opacity: 0.7; }
  .actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 14px; }
  .actions .cancel {
    background: transparent; color: #ccc; border: 1px solid #444;
    border-radius: 4px; padding: 4px 14px; cursor: pointer;
  }
  .actions .ok {
    background: #2d6a2d; color: #fff; border: 1px solid #4caf50;
    border-radius: 4px; padding: 4px 14px; cursor: pointer;
  }
</style>
