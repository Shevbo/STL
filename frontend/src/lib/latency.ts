// Exchange-latency helpers shared by the LIVE screen and the robot-window chart pane.
// The backend (/api/v1/live/latency) probes the Finam gateway every 5s and returns:
//   rtt_ms      = T3 - T1  (round-trip; clock-offset-free, the objective ping)
//   out_ms/in_ms = T2 - T1 / T3 - T2  (send->server / server->receive; carry the clock
//                  offset theta, exact only under NTP-synced clocks)
// theta_ms lets us show the split offset-corrected (out - theta, in + theta) so both are >= 0.
import { fetchWithAuth } from './fetch-auth';

export interface LatencySample {
  t: number;                 // epoch seconds (send time T1)
  out_ms: number | null;
  in_ms: number | null;
  rtt_ms: number | null;
  ok: boolean;
}

export interface LatencySummary {
  count?: number;
  ok_count?: number;
  theta_ms?: number;
  last_rtt_ms?: number | null;
  rtt_min_ms?: number | null;
  rtt_p50_ms?: number | null;
  rtt_p95_ms?: number | null;
}

export interface LatencyResponse {
  link: string;
  interval_s: number;
  theta_ms: number;
  summary: LatencySummary;
  samples: LatencySample[];
}

export async function fetchLatency(minutes = 180): Promise<LatencyResponse | null> {
  try {
    const res = await fetchWithAuth(`/api/v1/live/latency?minutes=${minutes}`);
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  }
}

// Colour thresholds for a retail HTTP/2 round-trip to the Finam Moscow gateway.
export function pingColor(ms: number | null | undefined): string {
  if (ms == null) return '#f44336';        // no reading = red
  if (ms < 50) return '#00e676';           // excellent
  if (ms < 150) return '#8bc34a';          // good
  if (ms < 400) return '#ffb300';          // fair
  return '#f44336';                        // poor
}

export function pingLabel(ms: number | null | undefined): string {
  if (ms == null) return 'нет связи';
  if (ms < 50) return 'отличный';
  if (ms < 150) return 'хороший';
  if (ms < 400) return 'средний';
  return 'высокий';
}

export function fmtMs(ms: number | null | undefined): string {
  if (ms == null) return '—';
  return `${ms < 10 ? ms.toFixed(1) : Math.round(ms)} мс`;
}
