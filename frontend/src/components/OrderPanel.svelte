<!-- frontend/src/components/OrderPanel.svelte -->
<script lang="ts">
  import type { Quote, OrderRequest } from '$lib/types';

  let {
    symbol = '',
    quote = undefined,
    onSubmit,
  }: {
    symbol?: string;
    quote?: Quote;
    onSubmit: (order: OrderRequest) => void;
  } = $props();

  let side = $state<'buy' | 'sell'>('buy');
  let quantity = $state(1);

  let autoPrice = $derived(
    side === 'buy' ? (quote?.ask ?? 0) : (quote?.bid ?? 0)
  );
  let priceStr = $state('');
  let effectivePrice = $derived(
    priceStr ? parseFloat(priceStr) : autoPrice
  );

  $effect(() => {
    priceStr = autoPrice > 0 ? autoPrice.toFixed(1) : '';
  });

  function handleSubmit(): void {
    if (!symbol || quantity <= 0) return;
    onSubmit({
      symbol,
      side,
      quantity,
      order_type: 'limit',
      price: effectivePrice || undefined,
    });
  }
</script>

<aside class="order-panel">
  <div class="title">Заявка</div>

  <div class="side-toggle">
    <button
      class="side-btn buy"
      class:active={side === 'buy'}
      onclick={() => side = 'buy'}
    >Купить</button>
    <button
      class="side-btn sell"
      class:active={side === 'sell'}
      onclick={() => side = 'sell'}
    >Продать</button>
  </div>

  <div class="field">
    <div class="lbl">Инструмент</div>
    <div class="symbol">{symbol || '—'}</div>
  </div>

  <div class="field">
    <label class="lbl" for="qty">Кол-во (лот)</label>
    <input
      id="qty"
      type="number"
      min="1"
      step="1"
      bind:value={quantity}
      class="input"
    />
  </div>

  <div class="field">
    <label class="lbl" for="price">Цена</label>
    <input
      id="price"
      type="number"
      step="0.1"
      bind:value={priceStr}
      class="input"
      placeholder={autoPrice > 0 ? autoPrice.toFixed(1) : '—'}
    />
  </div>

  <button
    class="submit-btn"
    class:buy={side === 'buy'}
    class:sell={side === 'sell'}
    onclick={handleSubmit}
    disabled={!symbol || quantity <= 0}
  >
    {side === 'buy' ? 'Купить' : 'Продать'} {quantity} лот
  </button>
</aside>

<style>
  .order-panel {
    border-top: 1px solid #2d2d4a;
    padding: 12px 10px;
    font-size: 12px;
    flex-shrink: 0;
  }
  .title {
    font-weight: 600; color: #ddd;
    margin-bottom: 10px; font-size: 13px;
  }
  .side-toggle {
    display: flex; gap: 4px; margin-bottom: 10px;
  }
  .side-btn {
    flex: 1; padding: 5px 0; border: 1px solid #2d2d4a;
    background: #1a1a2e; color: #555; border-radius: 3px;
    cursor: pointer; font-size: 11px; font-weight: 500;
    transition: background 0.1s, color 0.1s;
  }
  .side-btn.buy.active  { background: #1b3a1b; color: #4caf50; border-color: #4caf50; }
  .side-btn.sell.active { background: #3a1b1b; color: #f44336; border-color: #f44336; }
  .field { margin-bottom: 8px; }
  .lbl { display: block; color: #555; font-size: 10px; text-transform: uppercase; letter-spacing: 0.04em; margin-bottom: 3px; }
  .symbol { color: #ccc; }
  .input {
    width: 100%; box-sizing: border-box;
    background: #0f0f1e; border: 1px solid #2d2d4a;
    color: #ccc; padding: 4px 6px; border-radius: 3px;
    font-size: 12px;
  }
  .input:focus { outline: none; border-color: #555; }
  .submit-btn {
    width: 100%; padding: 7px 0; border: none;
    border-radius: 3px; cursor: pointer; font-size: 12px;
    font-weight: 600; margin-top: 4px;
    transition: opacity 0.1s;
  }
  .submit-btn:disabled { opacity: 0.4; cursor: default; }
  .submit-btn.buy  { background: #4caf50; color: #fff; }
  .submit-btn.sell { background: #f44336; color: #fff; }
</style>
