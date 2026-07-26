<!-- Уникальный ID окна/субокна для точного дебага: мелкий ярлык (обычно вверху справа)
     с кодом окна и кнопкой «скопировать». Оператор называет код в баг-репорте, и мы
     оба ссылаемся на ОДИН экран. Родитель должен иметь position: relative/absolute.
     Использование: <ScreenTag id="LAB-CHART" name="график лидера" /> -->
<script lang="ts">
  let { id, name = '', corner = 'tr', inline = false }:
    { id: string; name?: string; corner?: 'tr' | 'tl' | 'br' | 'bl'; inline?: boolean } = $props();
  let copied = $state(false);
  async function copy(e: MouseEvent) {
    e.stopPropagation();
    try {
      await navigator.clipboard.writeText(id);
    } catch {
      // clipboard может быть недоступен (не https/нет фокуса) — деградируем на выделение
      const ta = document.createElement('textarea');
      ta.value = id; ta.style.position = 'fixed'; ta.style.opacity = '0';
      document.body.appendChild(ta); ta.select();
      try { document.execCommand('copy'); } catch { /* ignore */ }
      document.body.removeChild(ta);
    }
    copied = true;
    setTimeout(() => (copied = false), 1200);
  }
</script>

<button class="screen-tag {inline ? 'st-inline' : 'st-' + corner}" onclick={copy}
        title={'ID окна для дебага: ' + id + (name ? ' (' + name + ')' : '') + ' — клик копирует код'}>
  <span class="st-hash">#</span><span class="st-id">{id}</span>
  <span class="st-copy">{copied ? '✓' : '⧉'}</span>
</button>

<style>
  .screen-tag {
    position: absolute; z-index: 120; display: inline-flex; align-items: center; gap: 3px;
    background: #0c0c18cc; border: 1px solid #2a2a44; border-radius: 3px; padding: 0 5px;
    height: 15px; font-size: 9px; font-family: ui-monospace, monospace; color: #7a7a9a;
    cursor: pointer; line-height: 1; user-select: none; backdrop-filter: blur(2px);
  }
  .screen-tag:hover { color: #cde; border-color: #4a7ad0; background: #10101e; }
  .st-tr { top: 3px; right: 3px; }
  .st-tl { top: 3px; left: 3px; }
  .st-br { bottom: 3px; right: 3px; }
  .st-bl { bottom: 3px; left: 3px; }
  .st-inline { position: static; flex-shrink: 0; }
  .st-hash { color: #556; }
  .st-id { color: #6aa8ff; font-weight: 700; letter-spacing: 0.4px; }
  .st-copy { color: #667; font-size: 10px; }
  .screen-tag:hover .st-copy { color: #6aa8ff; }
</style>
