<!-- Системный монитор робота: зелёная CRT-лента команд, ответов и переходов состояния.
     УПРАВЛЯЕМЫЙ компонент — ленту держит стенд, здесь только разметка, стили и
     прилипание к низу. Так у агентского и бумажного стендов одна консоль на двоих:
     раньше она была вшита в агентский экран, и бумажный остался вовсе без монитора
     (05.08.2026, сравнение стендов оператором).

     Строка ввода появляется, только если передан onAsk: у агентского робота есть
     LLM-напарник, у бумажного его нет, и рисовать неработающий промпт нельзя. -->
<script lang="ts">
  import { stickToBottom, type LogLine } from '../../lib/robot-console';

  let {
    lines = [],
    headline = 'SHECTORY TRADE & LAB · ROBOT CONSOLE',
    subline = '',
    prompt = '',
    onAsk = null,
    busy = false,
    busyText = 'напарник думает…',
  }: {
    lines?: LogLine[];
    headline?: string;
    subline?: string;
    prompt?: string;
    onAsk?: ((text: string) => void) | null;
    busy?: boolean;
    busyText?: string;
  } = $props();

  let box = $state<HTMLDivElement | null>(null);
  let stick = $state(true);
  let ask = $state('');

  const time = (t: number) => new Date(t).toLocaleTimeString('ru-RU');
  const stamp = (t: number) =>
    new Date(t).toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', year: '2-digit' });

  $effect(() => {
    lines.length;                       // зависимость: новая строка
    if (stick && box) box.scrollTop = box.scrollHeight;
  });
  function onScroll() {
    if (box) stick = stickToBottom(box.scrollHeight, box.scrollTop, box.clientHeight);
  }
  function send() {
    const t = ask.trim();
    if (!t || !onAsk) return;
    ask = '';
    onAsk(t);
  }
</script>

<div class="crt">
  <div class="crt-scan"></div>
  <div class="crt-body" bind:this={box} onscroll={onScroll}>
    <div class="crt-line dim">{headline}</div>
    {#if subline}<div class="crt-line dim">{subline}</div>{/if}
    {#each lines as l (l.t + l.text)}
      <div class="crt-line {l.kind}">
        <span class="crt-ts">[{stamp(l.t)} {time(l.t)}]</span>
        {l.text}
      </div>
    {/each}
    {#if busy}<div class="crt-line dim">{busyText}</div>{/if}
    {#if onAsk}
      <div class="crt-line prompt">
        <span class="crt-ps">STL: {prompt}&gt;</span>
        <!-- Ввод живёт ПРЯМО в ленте, на строке промпта: консоль, а не форма. -->
        <input class="crt-in" bind:value={ask} disabled={busy}
               placeholder="спросить напарника о торговле этого робота…"
               onkeydown={(e) => { if (e.key === 'Enter') send(); }} />
        {#if !ask && !busy}<span class="crt-cur">█</span>{/if}
      </div>
    {/if}
  </div>
</div>

<style>
  .crt { position: relative; background: #030803; overflow: hidden; height: 100%; }
  .crt-scan { position: absolute; inset: 0; pointer-events: none; z-index: 2;
    background: repeating-linear-gradient(to bottom, rgba(0,0,0,.28) 0 1px, transparent 1px 3px); }
  .crt-body { position: relative; z-index: 1; height: calc(8 * 19.5px + 8px); overflow-y: auto;
    padding: 5px 9px 3px;
    font: 13px/1.5 Consolas, "Lucida Console", "Courier New", monospace;
    color: #33ff66; text-shadow: 0 0 4px rgba(51,255,102,.45); letter-spacing: .02em; }
  .crt-body::-webkit-scrollbar { width: 9px; }
  .crt-body::-webkit-scrollbar-thumb { background: #1c4a2a; border-radius: 0; }
  .crt-body::-webkit-scrollbar-track { background: #061206; }
  .crt-line { white-space: pre-wrap; word-break: break-word; }
  .crt-line.dim { color: #1f8a44; text-shadow: none; }
  .crt-line.cmd { color: #9dffbe; }
  .crt-line.err { color: #ff9a3c; text-shadow: 0 0 4px rgba(255,154,60,.45); }
  .crt-line.sys { color: #66ffa6; }
  /* Реплики диалога: вопрос оператора ярче ленты команд, ответ напарника — обычный
     фосфор, но с отбивкой, чтобы абзац читался как ответ, а не как строка лога. */
  .crt-line.me { color: #d6ffe4; }
  .crt-line.ai { color: #4dffa0; margin: 2px 0 4px; padding-left: 10px; border-left: 1px solid #1c4a2a; }
  .crt-ts { color: #1f8a44; text-shadow: none; }
  .crt-in { flex: 1 1 auto; min-width: 120px; margin-left: 6px; background: none; border: none;
    outline: none; color: #9dffbe; font: inherit; text-shadow: inherit; caret-color: #33ff66; }
  .crt-in::placeholder { color: #1f8a44; }
  .crt-line.prompt { display: flex; align-items: center; }
  .crt-ps { color: #33ff66; }
  .crt-cur { display: inline-block; margin-left: 2px; animation: crtblink 1.05s step-end infinite; }
  @keyframes crtblink { 50% { opacity: 0; } }
  @media (prefers-reduced-motion: reduce) { .crt-cur { animation: none; } }
</style>
