<!-- Профили доведения умной заявки до исполнения: оператор правит секунды фаз
     и заводит свои профили. Заказ 29.09.2026: секунды не универсальны — на
     тихом рынке и малой позиции спешить незачем, на быстром ждать нельзя.
     Движок и API — окно real-trade, экран наш. Вся логика, в которой легко
     соврать, лежит в $lib/exec-profiles и покрыта тестами. -->
<script lang="ts">
  import { onMount } from 'svelte';
  import { fetchWithAuth } from '$lib/fetch-auth';
  import { downloadCSV } from '$lib/csv';
  import ScreenTag from './lab/ScreenTag.svelte';
  import {
    BUILTIN, DEFAULT_PROFILE, guaranteeText, longWaitWarn, profileError,
    profileNames, type ExecProfile, type ExecProfiles,
  } from '$lib/exec-profiles';

  let { onClose }: { onClose?: () => void } = $props();

  let profiles = $state<ExecProfiles>({});
  let loading = $state(true);
  let err = $state('');
  let msg = $state('');
  let saving = $state(false);
  let newName = $state('');

  const names = $derived(profileNames(profiles));
  // Ошибки ВСЕХ строк сразу: сохранение уходит целиком, и молча отправить одну
  // битую строку значит переписать профиль, которым уже пользуются заявки.
  const errors = $derived(Object.fromEntries(
    Object.entries(profiles).map(([n, p]) => [n, profileError(p)])));
  const anyError = $derived(Object.values(errors).some(Boolean));

  async function load() {
    loading = true; err = '';
    try {
      const r = await fetchWithAuth('/api/v1/quik/smart-orders/exec-profiles');
      if (!r.ok) { err = `HTTP ${r.status}`; return; }
      const d = await r.json();
      profiles = (d?.profiles ?? {}) as ExecProfiles;
    } catch (e: any) { err = e?.message || 'ошибка'; } finally { loading = false; }
  }

  async function save() {
    if (anyError) return;
    saving = true; msg = ''; err = '';
    try {
      const r = await fetchWithAuth('/api/v1/quik/smart-orders/exec-profiles', {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ profiles }),
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) { err = d?.detail || `HTTP ${r.status}`; return; }
      // Показываем ТО, ЧТО ВЕРНУЛ СЕРВЕР, а не то, что отправили: движок
      // нормализует числа и досыпает штатные, и расхождение экрана с диском
      // означало бы, что заявки уходят не с теми секундами, что на экране.
      profiles = (d?.profiles ?? profiles) as ExecProfiles;
      msg = 'Сохранено. Новые заявки поедут с этими секундами.';
    } catch (e: any) { err = e?.message || 'ошибка'; } finally { saving = false; }
  }

  function addProfile() {
    const n = newName.trim();
    if (!n || n in profiles) return;
    profiles = { ...profiles, [n]: {
      hold_sec: 10, chase_sec: 10, chase_every_sec: 2, market: true, title: n } };
    newName = '';
  }

  // Штатные три не удаляем даже с экрана: движок их всё равно вернёт, а заявка,
  // сославшаяся на исчезнувший профиль, осталась бы без доведения молча.
  function removeProfile(n: string) {
    if (BUILTIN.includes(n)) return;
    const { [n]: _drop, ...rest } = profiles;
    profiles = rest;
  }

  function exportCsv() {
    downloadCSV('exec-profiles.csv',
      ['профиль', 'подпись', 'ждать у цены, с', 'догонять, с', 'шаг, с', 'добивать по рынку', 'итог'],
      names.map((n) => [n, profiles[n].title, profiles[n].hold_sec, profiles[n].chase_sec,
                        profiles[n].chase_every_sec, profiles[n].market ? 'да' : 'нет',
                        guaranteeText(profiles[n])]));
  }

  onMount(load);
</script>

<section class="ep">
  <ScreenTag id="EXEC-PROFILES" name="профили исполнения" corner="tl"
             copyText={typeof location !== 'undefined' ? location.href : '/?execprofiles=1'} />
  <header class="ep-head">
    <h2>Профили исполнения</h2>
    <button class="ep-b" onclick={load} disabled={loading}>обновить</button>
    <button class="ep-b" onclick={exportCsv} disabled={!names.length}>CSV</button>
    <span class="ep-sp"></span>
    <button class="ep-b prim" onclick={save} disabled={saving || anyError}>
      {saving ? 'сохраняю…' : 'Сохранить'}
    </button>
    {#if onClose}<button class="ep-b" onclick={onClose}>закрыть</button>{/if}
  </header>

  <p class="ep-lead">Заявка ставит ЛИМИТ, и на быстром движении его могут не налить.
    Профиль говорит, что делать дальше: постоять у цены заявки, пойти за ценой,
    добить остаток по рынку. Относится и ко ВХОДУ, и к ВЫХОДУ — не исполнившийся
    вход врёт так же, как выход: в книге «сработала», а в рынке ничего нет.</p>

  {#if err}<p class="ep-err">{err}</p>{/if}
  {#if msg}<p class="ep-ok">{msg}</p>{/if}
  {#if loading}<p class="ep-dim">загружаю…</p>{/if}

  {#each names as n}
    <article class="ep-row" class:builtin={BUILTIN.includes(n)}>
      <div class="ep-r1">
        <b class="ep-name mono">{n}</b>
        {#if BUILTIN.includes(n)}<span class="ep-tag" title="штатный профиль: движок вернёт его, даже если стереть">штатный</span>{/if}
        {#if n === DEFAULT_PROFILE}<span class="ep-tag" title="ставится, когда заявка не назвала профиль">по умолчанию</span>{/if}
        <input class="ep-in text" bind:value={profiles[n].title} aria-label="Подпись профиля"
               placeholder="подпись, по ней профиль выбирают на форме" />
        {#if !BUILTIN.includes(n)}
          <button class="ep-b" onclick={() => removeProfile(n)}>удалить</button>
        {/if}
      </div>
      <div class="ep-r2">
        <label>ждать у цены заявки
          <input class="ep-in num" type="number" step="1" min="0" bind:value={profiles[n].hold_sec} />
          <span>с</span>
        </label>
        <label>догонять цену
          <input class="ep-in num" type="number" step="1" min="0" bind:value={profiles[n].chase_sec} />
          <span>с</span>
        </label>
        <label>переставлять раз в
          <input class="ep-in num" type="number" step="1" min="0" bind:value={profiles[n].chase_every_sec} />
          <span>с</span>
        </label>
        <label class="ep-chk">
          <input type="checkbox" bind:checked={profiles[n].market} />
          добивать остаток ПО РЫНКУ
        </label>
      </div>
      <!-- Итог одной строкой: оператор ставит секунды, глядя именно на него.
           «Нормальный» здесь звучит иначе, чем нулевые фазы, и это не
           придирка к словам: ноль означает «сразу по рынку», а выключенное
           добивание — «по рынку никогда». -->
      <div class="ep-sum">{guaranteeText(profiles[n])}</div>
      {#if longWaitWarn(profiles[n])}<div class="ep-warn">{longWaitWarn(profiles[n])}</div>{/if}
      {#if errors[n]}<div class="ep-err">{errors[n]}</div>{/if}
    </article>
  {/each}

  <div class="ep-add">
    <input class="ep-in text" bind:value={newName} placeholder="имя нового профиля, латиницей"
           aria-label="Имя нового профиля" />
    <button class="ep-b" onclick={addProfile} disabled={!newName.trim() || newName.trim() in profiles}>
      добавить профиль
    </button>
  </div>
</section>

<style>
  .ep { position: relative; height: 100%; overflow: auto; padding: 8px 10px 16px;
        background: #0f0f1e; color: #d6dbe8; font-size: 12px; }
  .ep-head { display: flex; gap: 8px; align-items: center; padding-bottom: 6px; }
  .ep-head h2 { margin: 0; font-size: 14px; }
  .ep-sp { flex: 1; }
  .ep-b { font-size: 11px; padding: 3px 8px; border-radius: 4px; border: 1px solid #2d2d4a;
          background: #16162b; color: #d6dbe8; cursor: pointer; }
  .ep-b:hover:not(:disabled) { background: #1b1b34; }
  .ep-b:disabled { opacity: .5; cursor: default; }
  .ep-b.prim { border-color: #5ecfb1; color: #5ecfb1; }
  .ep-lead { margin: 0 0 8px; color: #9aa0b4; max-width: 70ch; }
  .ep-dim { color: #9aa0b4; }
  .ep-ok { color: #5ecfb1; }
  .ep-err { color: #ff8fb1; }
  .ep-row { border: 1px solid #23233f; border-radius: 6px; padding: 6px 8px; margin-bottom: 6px; }
  .ep-row.builtin { border-color: #2d2d4a; }
  .ep-r1, .ep-r2 { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
  .ep-r2 { margin-top: 4px; }
  .ep-r2 label { display: flex; gap: 4px; align-items: center; color: #9aa0b4; }
  .ep-name { color: #e8e8f0; min-width: 90px; }
  .ep-tag { font-size: 10px; padding: 1px 5px; border-radius: 3px; background: #1b1b34; color: #9aa0b4; }
  .ep-in { background: #0c0c18; border: 1px solid #2d2d4a; border-radius: 4px;
           color: #d6dbe8; font-size: 11px; padding: 2px 5px; }
  .ep-in.text { flex: 1; min-width: 180px; }
  .ep-in.num { width: 66px; }
  .ep-chk { color: #9aa0b4; }
  .ep-sum { margin-top: 4px; color: #9aa0b4; }
  .ep-warn { margin-top: 2px; color: #e0a35c; }
  .ep-add { display: flex; gap: 8px; align-items: center; margin-top: 8px; }
  .mono { font-family: ui-monospace, monospace; }
</style>
