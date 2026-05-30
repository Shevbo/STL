<!-- Robot settings editor: create from template or edit existing robot -->
<script lang="ts">
  import { fetchWithAuth } from '../../lib/fetch-auth';

  let {
    robot = null,        // existing robot to edit (null = create new)
    onSaved,             // (robot) => void
    onClose,
  }: {
    robot?: any;
    onSaved?: (r: any) => void;
    onClose?: () => void;
  } = $props();

  // ── state ───────────────────────────────────────────────────────────
  let strategies = $state<any[]>([]);
  let stlLinks = $state<any[]>([]);
  let selectedStrategyId = $state('donchian_breakout');
  let selectedStrategy = $derived(strategies.find(s => s.id === selectedStrategyId));

  let name = $state(robot?.name ?? '');
  let stlLinkId = $state(robot?.stl_link_id ?? '');
  let paramValues = $state<Record<string, any>>({});

  // Trading window (time-of-day when robot is active). Stored as "HH:MM-HH:MM".
  function parseWindow(s: string | undefined): [string, string] {
    if (s && /^\d{2}:\d{2}-\d{2}:\d{2}$/.test(s)) {
      const [a, b] = s.split('-');
      return [a, b];
    }
    return ['09:00', '23:55'];  // default trading window
  }
  let [initFrom, initTo] = parseWindow(robot?.schedule);
  let tradeFrom = $state(initFrom);
  let tradeTo = $state(initTo);
  let schedule = $derived(`${tradeFrom}-${tradeTo}`);

  let saving = $state(false);
  let error = $state('');
  let imported = $state(false);

  // ── load strategies and stl links ───────────────────────────────────
  async function loadStrategies() {
    const res = await fetchWithAuth('/api/v1/strategies');
    strategies = res.ok ? await res.json() : [];
    if (!robot && strategies.length) {
      applyStrategyDefaults(strategies[0]);
    }
  }

  async function loadStlLinks() {
    const res = await fetchWithAuth('/api/v1/stl-links');
    stlLinks = res.ok ? await res.json() : [];
    if (!stlLinkId && stlLinks.length) stlLinkId = stlLinks[0].id;
  }

  function applyStrategyDefaults(strategy: any) {
    selectedStrategyId = strategy.id;
    if (!name) name = strategy.name;
    paramValues = { ...strategy.default_params };
  }

  $effect(() => {
    // When strategy selection changes, merge defaults (don't overwrite user edits)
    if (selectedStrategy && !imported) {
      const defaults = selectedStrategy.default_params ?? {};
      for (const [k, v] of Object.entries(defaults)) {
        if (paramValues[k] === undefined) paramValues[k] = v;
      }
    }
  });

  // ── populate from existing robot ─────────────────────────────────────
  $effect(() => {
    if (robot) {
      name = robot.name ?? '';
      stlLinkId = robot.stl_link_id ?? '';
      const [wf, wt] = parseWindow(robot.schedule);
      tradeFrom = wf;
      tradeTo = wt;
      paramValues = { ...(robot.params_json ?? {}) };
      imported = true;
    }
  });

  // ── save ────────────────────────────────────────────────────────────
  async function save() {
    if (!name.trim()) { error = 'Введите название робота'; return; }
    if (!stlLinkId) { error = 'Выберите STL Link (коннектор счёта)'; return; }
    saving = true; error = '';
    try {
      const strategy = strategies.find(s => s.id === selectedStrategyId);
      const scriptCode = robot?.script_code ?? strategy?.script_code ?? '';

      const body = {
        name: name.trim(),
        userEmail: 'admin',
        stlLinkId,
        schedule,
        scriptCode,
        paramsJson: paramValues,
      };

      let res: Response;
      if (robot) {
        res = await fetchWithAuth(`/api/v1/robots/${robot.id}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ ...body, name: body.name, scriptCode: body.scriptCode, paramsJson: body.paramsJson, schedule: body.schedule }),
        });
      } else {
        res = await fetchWithAuth('/api/v1/robots', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });
      }
      if (!res.ok) throw new Error(await res.text());
      const data = await res.json();
      onSaved?.({ ...body, id: robot?.id ?? data.id });
    } catch (e) {
      error = String(e);
    }
    saving = false;
  }

  // ── schedule helpers ────────────────────────────────────────────────
  $effect(() => { loadStrategies(); loadStlLinks(); });
</script>

<div class="editor">
  <div class="editor-header">
    <span class="editor-title">{robot ? `Настройки: ${robot.name}` : 'Новый робот'}</span>
    {#if onClose}
      <button class="close-btn" onclick={onClose}>✕</button>
    {/if}
  </div>

  <div class="editor-body">

    <!-- Strategy selector (only for new robots) -->
    {#if !robot}
      <div class="section">
        <div class="section-title">Стратегия</div>
        <div class="strategy-list">
          {#each strategies as s}
            <div
              class="strategy-card"
              class:active={selectedStrategyId === s.id}
              role="button"
              tabindex="0"
              onclick={() => applyStrategyDefaults(s)}
              onkeydown={(e) => e.key === 'Enter' && applyStrategyDefaults(s)}
            >
              <div class="s-name">{s.name}</div>
              <div class="s-desc">{s.description}</div>
              {#if s.source}
                <div class="s-source">
                  <a href={s.source} target="_blank" rel="noopener">GitHub ↗</a>
                </div>
              {/if}
            </div>
          {/each}
        </div>
      </div>
    {/if}

    <!-- Basic fields -->
    <div class="section">
      <div class="section-title">Основное</div>
      <div class="field">
        <label>Название</label>
        <input type="text" bind:value={name} placeholder="Мой Donchian Breakout" />
      </div>

      <div class="field">
        <label>STL Link (счёт)</label>
        {#if stlLinks.length === 0}
          <div class="warn">Нет STL Links. Создайте коннектор счёта.</div>
        {:else}
          <select bind:value={stlLinkId}>
            {#each stlLinks as l}
              <option value={l.id}>{l.broker} / {l.account_id} ({l.exchange})</option>
            {/each}
          </select>
        {/if}
      </div>

      <div class="field">
        <label>Торговое окно (время работы робота)</label>
        <div class="window-row">
          <input type="time" bind:value={tradeFrom} />
          <span class="window-dash">—</span>
          <input type="time" bind:value={tradeTo} />
        </div>
        <div class="hint">
          Робот торгует только в этом окне (МСК). Вне окна — не торгует. По умолчанию 09:00–23:55.
        </div>
      </div>
    </div>

    <!-- Strategy parameters -->
    {#if selectedStrategy || robot}
      {@const schema = selectedStrategy?.params_schema ?? []}
      {#if schema.length}
        <div class="section">
          <div class="section-title">Параметры стратегии</div>
          {#each schema as p}
            <div class="field">
              <label>
                {p.label}
                {#if p.hint}<span class="param-hint"> — {p.hint}</span>{/if}
              </label>
              {#if p.type === 'number'}
                <input
                  type="number"
                  min={p.min}
                  max={p.max}
                  bind:value={paramValues[p.key]}
                  placeholder={String(p.default)}
                />
              {:else}
                <input
                  type="text"
                  bind:value={paramValues[p.key]}
                  placeholder={String(p.default)}
                />
              {/if}
            </div>
          {/each}
        </div>
      {/if}
    {/if}

    <!-- Error -->
    {#if error}
      <div class="error">{error}</div>
    {/if}

    <!-- Actions -->
    <div class="actions">
      <button class="save-btn" onclick={save} disabled={saving}>
        {saving ? 'Сохраняю…' : (robot ? 'Сохранить' : 'Создать робота')}
      </button>
      {#if onClose}
        <button class="cancel-btn" onclick={onClose}>Отмена</button>
      {/if}
    </div>

  </div>
</div>

<style>
  .editor {
    display: flex; flex-direction: column; height: 100%;
    background: #0f0f1e; color: #ccc;
  }
  .editor-header {
    display: flex; justify-content: space-between; align-items: center;
    padding: 10px 16px; background: #1a1a2e; border-bottom: 1px solid #2d2d4a;
    flex-shrink: 0;
  }
  .editor-title { font-size: 13px; color: #4caf50; font-weight: 600; }
  .close-btn { background: none; border: none; color: #888; cursor: pointer; font-size: 16px; }

  .editor-body { flex: 1; overflow-y: auto; padding: 16px; display: flex; flex-direction: column; gap: 16px; }

  .section { display: flex; flex-direction: column; gap: 10px; }
  .section-title {
    font-size: 10px; color: #555; text-transform: uppercase; letter-spacing: 0.5px;
    border-bottom: 1px solid #1e1e3a; padding-bottom: 4px;
  }

  /* Strategy cards */
  .strategy-list { display: flex; flex-direction: column; gap: 6px; }
  .strategy-card {
    padding: 10px 12px; border: 1px solid #2d2d4a; border-radius: 4px;
    cursor: pointer; transition: border-color 0.15s;
  }
  .strategy-card:hover { border-color: #4d4d6a; }
  .strategy-card.active { border-color: #4caf50; background: #0a1a0a; }
  .s-name { font-size: 12px; color: #ccc; font-weight: 600; margin-bottom: 3px; }
  .s-desc { font-size: 11px; color: #666; line-height: 1.4; }
  .s-source { margin-top: 4px; }
  .s-source a { font-size: 10px; color: #4caf5099; text-decoration: none; }
  .s-source a:hover { color: #4caf50; }

  /* Fields */
  .field { display: flex; flex-direction: column; gap: 4px; }
  .field label { font-size: 11px; color: #888; }
  .param-hint { color: #555; font-style: italic; }
  input, select {
    background: #0a0a15; border: 1px solid #2d2d4a; color: #ccc;
    padding: 5px 8px; font-size: 12px; border-radius: 3px; outline: none;
  }
  input:focus, select:focus { border-color: #4caf5066; }
  .hint { font-size: 10px; color: #666; }
  .warn { font-size: 11px; color: #f4433699; padding: 4px 8px; background: #1a0a0a; border-radius: 3px; }
  .window-row { display: flex; align-items: center; gap: 8px; }
  .window-row input { flex: 1; }
  .window-dash { color: #555; }

  /* Actions */
  .actions { display: flex; gap: 8px; margin-top: 8px; }
  .save-btn {
    flex: 1; padding: 8px; background: #4caf5020; border: 1px solid #4caf5066;
    color: #4caf50; cursor: pointer; border-radius: 4px; font-size: 12px;
  }
  .save-btn:hover { background: #4caf5030; }
  .save-btn:disabled { opacity: 0.5; cursor: default; }
  .cancel-btn {
    padding: 8px 16px; background: transparent; border: 1px solid #2d2d4a;
    color: #666; cursor: pointer; border-radius: 4px; font-size: 12px;
  }
  .error { color: #f44336; font-size: 11px; padding: 8px; background: #1a0808; border-radius: 3px; }
</style>
