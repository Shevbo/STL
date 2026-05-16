<!-- frontend/src/components/CodeEditor.svelte -->
<script lang="ts">
  import { onMount, onDestroy } from 'svelte';

  let { scriptPath, onSave, onRun, onClose }: {
    scriptPath: string;
    onSave: (path: string, content: string) => Promise<void>;
    onRun: (path: string, content: string) => Promise<void>;
    onClose: () => void;
  } = $props();

  let containerEl: HTMLDivElement;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  let editor: { getValue: () => string; dispose: () => void } | null = null;
  let saving = $state(false);
  let running = $state(false);
  let errorMsg = $state('');

  let x = $state(80);
  let y = $state(80);
  let dragging = false;
  let dx = 0;
  let dy = 0;

  function onMouseDown(e: MouseEvent): void {
    dragging = true;
    dx = e.clientX - x;
    dy = e.clientY - y;
  }

  function onMouseMove(e: MouseEvent): void {
    if (!dragging) return;
    x = e.clientX - dx;
    y = e.clientY - dy;
  }

  function onMouseUp(): void { dragging = false; }

  onMount(async () => {
    const loader = (await import('@monaco-editor/loader')).default;
    const monaco = await loader.init();
    let content = '';
    try {
      const res = await fetch(`/api/scripts/${encodeURIComponent(scriptPath)}`);
      if (res.ok) content = await res.text();
    } catch {
      // file not found — start empty
    }
    editor = monaco.editor.create(containerEl, {
      value: content,
      language: 'python',
      theme: 'vs-dark',
      fontSize: 13,
      minimap: { enabled: false },
      automaticLayout: true,
      scrollBeyondLastLine: false,
    });
  });

  onDestroy(() => editor?.dispose());

  async function save(): Promise<void> {
    if (!editor) return;
    saving = true; errorMsg = '';
    try {
      await onSave(scriptPath, editor.getValue());
    } catch (e) { errorMsg = String(e); }
    saving = false;
  }

  async function run(): Promise<void> {
    if (!editor) return;
    running = true; errorMsg = '';
    try {
      await onRun(scriptPath, editor.getValue());
    } catch (e) { errorMsg = String(e); }
    running = false;
  }
</script>

<svelte:window onmousemove={onMouseMove} onmouseup={onMouseUp} />

<div class="editor-panel" style="left:{x}px; top:{y}px; width:700px; height:500px;">
  <div class="titlebar" role="banner" onmousedown={onMouseDown}>
    <span class="title">{scriptPath}</span>
    <div class="actions">
      <button onclick={save} disabled={saving}>{saving ? 'Saving…' : 'Save'}</button>
      <button onclick={run} disabled={running}>{running ? 'Running…' : 'Run'}</button>
      <button class="close-btn" onclick={onClose}>✕</button>
    </div>
  </div>
  {#if errorMsg}<div class="error">{errorMsg}</div>{/if}
  <div class="monaco-container" bind:this={containerEl}></div>
</div>

<style>
  .editor-panel {
    position: fixed; z-index: 100;
    display: flex; flex-direction: column;
    background: #1e1e1e; border: 1px solid #3d3d5a;
    border-radius: 6px; box-shadow: 0 8px 32px rgba(0,0,0,0.6);
    overflow: hidden;
  }
  .titlebar {
    display: flex; justify-content: space-between; align-items: center;
    padding: 6px 10px; background: #2d2d4a; cursor: grab;
    border-bottom: 1px solid #3d3d5a; user-select: none; flex-shrink: 0;
  }
  .titlebar:active { cursor: grabbing; }
  .title { font-size: 12px; color: #aaa; }
  .actions { display: flex; gap: 6px; }
  .monaco-container { flex: 1; }
  .error {
    padding: 4px 10px; background: #3d1414;
    color: #f44336; font-size: 11px; border-bottom: 1px solid #5a2020; flex-shrink: 0;
  }
  button {
    padding: 2px 10px; background: #3d3d5a; border: 1px solid #4d4d6a;
    color: #ccc; font-size: 11px; border-radius: 3px; cursor: pointer;
  }
  button.close-btn { background: transparent; border: none; color: #888; font-size: 14px; }
  button:disabled { opacity: 0.5; cursor: default; }
</style>
