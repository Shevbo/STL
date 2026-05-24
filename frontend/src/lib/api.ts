import type { OrderRequest, OrderResponse, Position } from './types';
import { fetchWithAuth } from './fetch-auth';

export async function placeOrder(req: OrderRequest): Promise<OrderResponse> {
  const resp = await fetchWithAuth('/api/v1/orders', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  });
  if (!resp.ok) throw new Error(await resp.text());
  return resp.json() as Promise<OrderResponse>;
}

export async function fetchPortfolio(): Promise<Position[]> {
  const resp = await fetchWithAuth('/api/v1/portfolio');
  if (!resp.ok) throw new Error(await resp.text());
  return resp.json() as Promise<Position[]>;
}
