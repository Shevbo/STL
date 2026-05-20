<!-- frontend/src/components/OrderConfirmDialog.svelte -->
<script lang="ts">
  import type { OrderRequest } from '$lib/types';

  let {
    order,
    onConfirm,
    onCancel,
  }: {
    order: OrderRequest;
    onConfirm: (order: OrderRequest) => Promise<void>;
    onCancel: () => void;
  } = $props();

  let loading = $state(false);

  const sideLabel = $derived(order.side === 'buy' ? 'BUY' : 'SELL');
  const sideColor = $derived(order.side === 'buy' ? '#4caf50' : '#f44336');
  const priceLabel = $derived(
    order.price != null ? order.price.toFixed(1) : 'рыночная'
  );

  async function confirm(): Promise<void> {
    loading = true;
    try {
      await onConfirm(order);
    } finally {
      loading = false;
    }
  }
</script>

<div class="overlay" role="dialog" aria-modal="true">
  <div class="dialog">
    <div class="header">Подтверждение заявки</div>

    <div class="summary">
      <span class="symbol">{order.symbol}</span>
      <span class="side" style="color: {sideColor}">· {sideLabel} ·</span>
      <span class="qty">{order.quantity} лот</span>
      <span class="price">· ~{priceLabel}</span>
    </div>

    <div class="actions">
      <button class="btn cancel" onclick={onCancel} disabled={loading}>
        Отмена
      </button>
      <button
        class="btn confirm"
        class:buy={order.side === 'buy'}
        class:sell={order.side === 'sell'}
        onclick={confirm}
        disabled={loading}
      >
        {loading ? 'Отправка…' : 'Подтвердить'}
      </button>
    </div>
  </div>
</div>

<style>
  .overlay {
    position: fixed; inset: 0;
    background: rgba(0,0,0,0.6);
    display: flex; align-items: center; justify-content: center;
    z-index: 1000;
  }
  .dialog {
    background: #14142a; border: 1px solid #2d2d4a;
    border-radius: 6px; padding: 24px 28px;
    min-width: 280px;
  }
  .header {
    font-size: 13px; font-weight: 600; color: #ddd;
    margin-bottom: 16px;
  }
  .summary {
    font-size: 14px; color: #ccc;
    display: flex; flex-wrap: wrap; gap: 4px; align-items: baseline;
    margin-bottom: 20px;
  }
  .symbol { font-weight: 600; color: #fff; }
  .side { font-weight: 700; }
  .price { color: #888; }
  .actions { display: flex; gap: 10px; justify-content: flex-end; }
  .btn {
    padding: 7px 18px; border: none; border-radius: 4px;
    font-size: 12px; font-weight: 600; cursor: pointer;
    transition: opacity 0.1s;
  }
  .btn:disabled { opacity: 0.5; cursor: default; }
  .cancel {
    background: #1a1a2e; color: #888;
    border: 1px solid #2d2d4a;
  }
  .confirm.buy  { background: #4caf50; color: #fff; }
  .confirm.sell { background: #f44336; color: #fff; }
</style>
