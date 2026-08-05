<!-- RobotWindow: модальная ОБЁРТКА над единым стендом робота.

     До 05.08.2026 это был ВТОРОЙ экран со своей вёрсткой, своими панелями и своим
     набором кадров — и он неизбежно разъехался с агентским: не было ни системного
     монитора, ни журнала доходности, ни сигнала, ни истории прогонов. Правило
     оператора: стенд робота ОДИН — для бэктеста, бумаги и реала.

     Поэтому здесь больше нет ни одной панели. Окно даёт только рамку модалки и
     закрытие по Esc, а всё содержимое рисует AgentRobotScreen — тот же компонент,
     что открывается по /?agent_robot=. Источник данных (агентское зеркало или
     бумажный робот STL) выбирает СЕРВЕР в /api/v1/lab/robot-stand/{id}, а не
     фронтенд: ветвление во фронтенде и породило два экрана. -->
<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import AgentRobotScreen from './AgentRobotScreen.svelte';

  let { robotId, onClose }: { robotId: string; onClose: () => void } = $props();

  function onKey(e: KeyboardEvent) {
    if (e.key === 'Escape') onClose();
  }
  onMount(() => window.addEventListener('keydown', onKey));
  onDestroy(() => window.removeEventListener('keydown', onKey));
</script>

<div class="rw-backdrop" role="presentation"
     onclick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
  <div class="rw-modal">
    <button class="rw-close" onclick={onClose} title="Закрыть (Esc)">✕</button>
    <div class="rw-body">
      <AgentRobotScreen {robotId} embedded />
    </div>
  </div>
</div>

<style>
  .rw-backdrop { position: fixed; inset: 0; z-index: 60; background: rgba(4, 6, 14, .72);
    display: flex; align-items: stretch; justify-content: center; padding: 14px; }
  .rw-modal { position: relative; flex: 1; min-width: 0; max-width: 1900px;
    background: #0b0b16; border: 1px solid #23233d; border-radius: 6px;
    display: flex; flex-direction: column; overflow: hidden; }
  .rw-body { flex: 1; min-height: 0; display: flex; }
  .rw-close { position: absolute; top: 6px; right: 8px; z-index: 5;
    background: #16162c; border: 1px solid #2d2d4a; color: #99a; cursor: pointer;
    font-size: 14px; line-height: 1; padding: 4px 9px; border-radius: 3px; }
  .rw-close:hover { color: #e6e6f0; border-color: #4a4a70; }
  @media (max-width: 820px) { .rw-backdrop { padding: 0; } .rw-modal { border-radius: 0; } }
</style>
