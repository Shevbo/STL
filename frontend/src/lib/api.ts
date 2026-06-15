import type { OrderRequest, OrderResponse, Position } from './types';
import { fetchWithAuth } from './fetch-auth';
import { applyFeeConfig } from './lab-analytics';

/** Fetch the backend's authoritative commission model and apply it. Call once at
 *  startup; safe to ignore failures (lab-analytics keeps its fallback constants). */
export async function loadFeeConfig(): Promise<void> {
  try {
    const resp = await fetchWithAuth('/api/v1/lab/fee-config');
    if (resp.ok) applyFeeConfig(await resp.json());
  } catch {
    // keep fallback constants
  }
}

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
