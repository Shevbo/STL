// Shared access to AGENT-HOSTED robots (the robots-mirror + agent local status)
// for the leaderboard (Showcase) and the LIVE tab. These robots run on the QUIK
// agent, NOT in the STL `robots` table, so they never appear in
// /api/v1/robots/showcase. This module maps the mirror into a compact row and
// carries the QUIK-link health + recon snapshot for the per-robot stand.
//
// P&L is taken from the runner's authoritative realized_pnl (PRICE POINTS) times
// the instrument point value — NEVER recomputed from recent_fills, which the
// mirror truncates to the last 20 (a client recompute would understate net).
import { fetchWithAuth } from './fetch-auth';

const EXEC = new Set(['paper', 'filled', 'submitted', 'executed']);

export interface AgentRobotRow {
  id: string;
  name: string;
  symbol: string;
  agent: true;
  mode: 'real' | 'paper';
  paper: boolean;
  deployed: boolean;        // running && !paused
  netRub: number | null;    // realized_pnl(points) * coef; null when coef unknown
  pnlPoints: number;
  position: number;
  working: number;          // resting/in-flight orders
  fillsWindow: number;      // fills visible in the mirror window (last <=20)
  heartbeatAgeSec: number | null;
}

// coef = step_cost / price_step (₽ per price point), keyed by instrument code.
async function fetchCoefs(agentId?: string | null): Promise<Record<string, number>> {
  try {
    const q = agentId ? `?agent_id=${encodeURIComponent(agentId)}` : '';
    const res = await fetchWithAuth(`/api/v1/quik/params${q}`,
      { signal: AbortSignal.timeout(4000) } as any);
    if (!res.ok) return {};
    const d = await res.json();
    const m: Record<string, number> = {};
    for (const r of d.rows ?? []) if (r.code && r.coef > 0) m[r.code] = Number(r.coef);
    return m;
  } catch { return {}; }
}

/** Fetch agent-hosted robots as compact rows. Returns [] on any failure (the
 *  caller degrades gracefully — an agent outage must not blank the page). */
export async function fetchAgentRobots(agentId?: string | null): Promise<AgentRobotRow[]> {
  try {
    const q = agentId ? `?agent_id=${encodeURIComponent(agentId)}` : '';
    const res = await fetchWithAuth(`/api/v1/quik/robots-mirror${q}`,
      { signal: AbortSignal.timeout(6000) } as any);
    if (!res.ok) return [];
    const data = await res.json();
    const robots: any[] = data?.robots ?? [];
    if (!robots.length) return [];
    const coefs = await fetchCoefs(agentId);
    const now = Date.now();
    // Drop id-less rows: an empty '' each-key would crash the keyed {#each}
    // (Svelte 5 throws each_key_duplicate on two '' keys).
    return robots.filter((r) => r.robot_id).map((r): AgentRobotRow => {
      const paper = !!r.paper;
      const pnlPoints = Number(r.realized_pnl ?? 0);
      const coef = coefs[r.symbol];
      const hb = Number(r.heartbeat_unix_ms ?? 0);
      return {
        id: String(r.robot_id ?? ''),
        name: String(r.display_name || r.robot_id || ''),   // operator override, else the raw id
        symbol: String(r.symbol ?? ''),
        agent: true,
        mode: paper ? 'paper' : 'real',
        paper,
        deployed: !!r.running && !r.paused,
        netRub: coef != null && coef > 0 ? pnlPoints * coef : null,
        pnlPoints,
        position: Number(r.position ?? 0),
        working: (r.working_orders ?? []).length,
        fillsWindow: (r.recent_fills ?? []).filter((f: any) => EXEC.has(f.status)).length,
        heartbeatAgeSec: hb ? Math.round((now - hb) / 1000) : null,
      };
    });
  } catch { return []; }
}

export interface AgentLocalStatus {
  agent: { link_up?: boolean; build_rev?: number; uptime_sec?: number; master_flag?: boolean; reconnects?: number } | null;
  health: {
    rtt_ms?: number; pong_age_ms?: number; clock_drift_ms?: number; exchange_lag_ms?: number;
    pos_age_ms?: number; ord_age_ms?: number; runner_healthy?: boolean; runner_report_age_ms?: number;
    feed?: { code: string; age_ms: number; last?: number; bid?: number; ask?: number }[];
  } | null;
  recon: { state?: string; robot_checks?: any[]; manual?: any; orders?: any; trades?: any; trans?: any } | null;
  receivedAgeSec: number | null;
}

/** Fetch the agent's local status mirror (QUIK-link health + recon vs QUIK
 *  account tables). Returns null on failure. */
export async function fetchAgentLocalStatus(agentId?: string | null): Promise<AgentLocalStatus | null> {
  try {
    const q = agentId ? `?agent_id=${encodeURIComponent(agentId)}` : '';
    const res = await fetchWithAuth(`/api/v1/quik/agent-local-status${q}`,
      { signal: AbortSignal.timeout(5000) } as any);
    if (!res.ok) return null;
    const d = await res.json();
    const recv = Number(d?._received_at_ms ?? 0);
    return {
      agent: d?.agent ?? null,
      health: d?.health ?? null,
      recon: d?.recon ?? null,
      receivedAgeSec: recv ? Math.round((Date.now() - recv) / 1000) : null,
    };
  } catch { return null; }
}

/** Navigate to a robot's full stand (AgentRobotScreen renders on ?agent_robot=). */
export function openAgentRobot(id: string, agentId?: string | null): void {
  const q = new URLSearchParams();
  q.set('agent_robot', id);
  if (agentId) q.set('agent', agentId);
  window.location.search = q.toString();
}
