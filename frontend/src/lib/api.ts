import type { OrderRequest, OrderResponse, Position } from './types';

export async function placeOrder(req: OrderRequest): Promise<OrderResponse> {
  const resp = await fetch('/api/v1/orders', {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  });
  if (!resp.ok) throw new Error(await resp.text());
  return resp.json() as Promise<OrderResponse>;
}

export async function fetchPortfolio(): Promise<Position[]> {
  const resp = await fetch('/api/v1/portfolio', { credentials: 'include' });
  if (!resp.ok) throw new Error(await resp.text());
  return resp.json() as Promise<Position[]>;
}
