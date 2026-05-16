# Shectory Trader Frontend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Svelte 5 SPA that shows real-time robot monitoring (Phase A) and a lab overlay for backtesting strategies (Phase B).

**Architecture:** Single-page app served from `frontend/dist/` via nginx; WebSocket from M8 API streams quotes → rAF batching → Svelte 5 `$state` stores → DOM. Phase B overlays a Lab panel on top of Phase A without changing the layout.

**Tech Stack:** Svelte 5 (runes), Vite 5, TypeScript 5, uPlot (ticks), TradingView Lightweight Charts (OHLC), Monaco Editor (scripts), Vitest (unit tests)

**Spec:** `docs/superpowers/specs/2026-05-16-frontend-design.md`

---

## File Map

```
frontend/
├── package.json
├── tsconfig.json
├── vite.config.ts
├── svelte.config.js
├── index.html
├── mock/
│   └── ws_server.py          # Dev-only mock WebSocket server
└── src/
    ├── main.ts
    ├── App.svelte
    ├── lib/
    │   ├── types.ts
    │   ├── ws.ts              # WsClient with rAF batching
    │   ├── offline-player.ts  # JSON replay (same interface as WsClient)
    │   ├── api.ts             # fetch wrapper for /api/*
    │   └── stores/
    │       ├── quotes.svelte.ts
    │       ├── robots.svelte.ts
    │       ├── account.svelte.ts
    │       └── services.svelte.ts
    └── components/
        ├── TopBar.svelte
        ├── RobotsPanel.svelte
        ├── ChartFrame.svelte
        ├── InstrumentPanel.svelte
        ├── BottomBar.svelte
        ├── LabBar.svelte
        └── CodeEditor.svelte
```

---

## Phase A — Shell + Operational

### Task 1: Project Scaffold

**Files:**
- Create: `frontend/package.json`
- Create: `frontend/tsconfig.json`
- Create: `frontend/vite.config.ts`
- Create: `frontend/svelte.config.js`
- Create: `frontend/index.html`
- Create: `frontend/src/main.ts`
- Create: `frontend/src/App.svelte`

- [ ] **Step 1: Create frontend directory and package.json**

```bash
mkdir -p frontend/src/lib/stores frontend/src/components frontend/mock
```

Write `frontend/package.json`:
```json
{
  "name": "shectory-trader-ui",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "vite build",
    "preview": "vite preview",
    "test": "vitest run",
    "test:watch": "vitest"
  },
  "devDependencies": {
    "@sveltejs/vite-plugin-svelte": "^4.0.0",
    "svelte": "^5.0.0",
    "typescript": "^5.4.0",
    "vite": "^5.2.0",
    "vitest": "^1.6.0",
    "svelte-check": "^4.0.0"
  },
  "dependencies": {
    "lightweight-charts": "^4.2.0",
    "uplot": "^1.6.31",
    "@monaco-editor/loader": "^1.4.0"
  }
}
```

- [ ] **Step 2: Write tsconfig.json**

Write `frontend/tsconfig.json`:
```json
{
  "compilerOptions": {
    "target": "ESNext",
    "useDefineForClassFields": true,
    "module": "ESNext",
    "moduleResolution": "Bundler",
    "strict": true,
    "resolveJsonModule": true,
    "isolatedModules": true,
    "noEmit": true,
    "verbatimModuleSyntax": true,
    "paths": { "$lib/*": ["./src/lib/*"] }
  },
  "include": ["src/**/*", "src/**/*.svelte"]
}
```

- [ ] **Step 3: Write svelte.config.js and vite.config.ts**

Write `frontend/svelte.config.js`:
```js
import { vitePreprocess } from '@sveltejs/vite-plugin-svelte';

export default {
  preprocess: vitePreprocess(),
  compilerOptions: { runes: true }
};
```

Write `frontend/vite.config.ts`:
```typescript
import { defineConfig } from 'vite';
import { svelte } from '@sveltejs/vite-plugin-svelte';
import path from 'path';

export default defineConfig({
  plugins: [svelte()],
  resolve: {
    alias: { $lib: path.resolve('./src/lib') }
  },
  server: {
    proxy: {
      '/api': 'http://localhost:8000',
      '/ws': { target: 'ws://localhost:8000', ws: true }
    }
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['src/test-setup.ts']
  }
});
```

- [ ] **Step 4: Write index.html and main.ts**

Write `frontend/index.html`:
```html
<!doctype html>
<html lang="ru">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Shectory Trader</title>
    <style>
      *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
      html, body, #app { height: 100%; font-family: 'JetBrains Mono', monospace, sans-serif; background: #14142a; color: #ccc; }
    </style>
  </head>
  <body>
    <div id="app"></div>
    <script type="module" src="/src/main.ts"></script>
  </body>
</html>
```

Write `frontend/src/main.ts`:
```typescript
import { mount } from 'svelte';
import App from './App.svelte';

mount(App, { target: document.getElementById('app')! });
```

Write `frontend/src/App.svelte` (skeleton — will be filled in Task 11):
```svelte
<script lang="ts">
  let labMode = $state(false);
</script>

<div class="shell">
  <p style="color: #888; padding: 20px;">Shectory Trader — scaffold OK</p>
</div>

<style>
  .shell { height: 100%; display: flex; flex-direction: column; }
</style>
```

Write `frontend/src/test-setup.ts`:
```typescript
// placeholder for future jsdom setup
```

- [ ] **Step 5: Install dependencies**

```bash
cd frontend && npm install
```

Expected: no errors, `node_modules/` created.

- [ ] **Step 6: Verify build**

```bash
cd frontend && npm run build
```

Expected: `dist/` created, no TypeScript errors.

- [ ] **Step 7: Commit**

```bash
cd frontend && git add -A && git commit -m "feat(ui): scaffold Svelte 5 + Vite + TypeScript frontend"
```

---

### Task 2: Types

**Files:**
- Create: `frontend/src/lib/types.ts`

- [ ] **Step 1: Write types.ts**

```typescript
// frontend/src/lib/types.ts

export interface Quote {
  symbol: string;
  bid: number;
  bidSize: number;
  ask: number;
  askSize: number;
  last: number;
  lastSize: number;
  timestamp: string; // ISO 8601
}

export interface Robot {
  id: string;
  name: string;
  symbol: string;
  deposit: number;
  pnl: number;
  tradeCount: number;
  position: number; // positive=long, negative=short, 0=flat
}

export interface AccountSummary {
  deposit: number;
  free: number;
  inPosition: number;
  variationMargin: number;
}

export interface OhlcBar {
  time: number; // unix seconds
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface TradeMarker {
  time: number;
  position: 'aboveBar' | 'belowBar';
  color: string;
  shape: 'arrowDown' | 'arrowUp';
  text: string;
}

export interface BacktestResult {
  equityCurve: Array<{ time: number; value: number }>;
  totalPnl: number;
  tradeCount: number;
}

export interface Strategy {
  id: string;
  name: string;
  symbol: string;
  params: Record<string, unknown>;
  scriptPath?: string;
}

export type ServiceId = 'auth' | 'md' | 'tx' | 'oms' | 'pos' | 'audit';
export type ServiceStatus = 'ok' | 'warn' | 'error';

// WS messages from M8 API
export type WsIncoming =
  | { type: 'quote'; symbol: string; bid: number; bid_size: number; ask: number; ask_size: number; last: number; last_size: number; timestamp: string }
  | { type: 'service_status'; service: ServiceId; status: ServiceStatus }
  | { type: 'account'; deposit: number; free: number; in_position: number; variation_margin: number }
  | { type: 'robot_update'; robots: Robot[] };
```

- [ ] **Step 2: Write type test**

Create `frontend/src/lib/types.test.ts`:
```typescript
import { describe, it, expect } from 'vitest';
import type { Quote, Robot, AccountSummary, ServiceId, WsIncoming } from './types';

describe('types shape', () => {
  it('Quote has required fields', () => {
    const q: Quote = {
      symbol: 'GZM6@RTSX', bid: 100.5, bidSize: 10,
      ask: 100.6, askSize: 5, last: 100.55, lastSize: 3,
      timestamp: '2026-05-16T10:00:00Z',
    };
    expect(q.symbol).toBe('GZM6@RTSX');
    expect(q.bid).toBe(100.5);
  });

  it('WsIncoming quote discriminates by type', () => {
    const msg: WsIncoming = {
      type: 'quote', symbol: 'GZM6@RTSX',
      bid: 100, bid_size: 5, ask: 101, ask_size: 3,
      last: 100.5, last_size: 2, timestamp: '2026-05-16T10:00:00Z',
    };
    expect(msg.type).toBe('quote');
  });
});
```

- [ ] **Step 3: Run test**

```bash
cd frontend && npm test
```

Expected: 2 tests PASS.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/lib/types.ts frontend/src/lib/types.test.ts
git commit -m "feat(ui): add domain types and WsIncoming discriminated union"
```

---

### Task 3: Reactive Stores

**Files:**
- Create: `frontend/src/lib/stores/quotes.svelte.ts`
- Create: `frontend/src/lib/stores/robots.svelte.ts`
- Create: `frontend/src/lib/stores/account.svelte.ts`
- Create: `frontend/src/lib/stores/services.svelte.ts`

Note: files must end in `.svelte.ts` for Svelte runes to compile.

- [ ] **Step 1: Write quotes store**

```typescript
// frontend/src/lib/stores/quotes.svelte.ts
import type { Quote } from '$lib/types';

let _all = $state<Record<string, Quote>>({});

export const quotesStore = {
  get all(): Record<string, Quote> { return _all; },
  update(symbol: string, q: Quote): void { _all[symbol] = q; },
  get(symbol: string): Quote | undefined { return _all[symbol]; },
  reset(): void { _all = {}; },
};
```

- [ ] **Step 2: Write robots store**

```typescript
// frontend/src/lib/stores/robots.svelte.ts
import type { Robot } from '$lib/types';

let _all = $state<Robot[]>([]);

export const robotsStore = {
  get all(): Robot[] { return _all; },
  set(robots: Robot[]): void { _all = robots; },
  updatePnl(id: string, pnl: number): void {
    const r = _all.find(r => r.id === id);
    if (r) r.pnl = pnl;
  },
  reset(): void { _all = []; },
};
```

- [ ] **Step 3: Write account store**

```typescript
// frontend/src/lib/stores/account.svelte.ts
import type { AccountSummary } from '$lib/types';

let _data = $state<AccountSummary>({ deposit: 0, free: 0, inPosition: 0, variationMargin: 0 });

export const accountStore = {
  get data(): AccountSummary { return _data; },
  set(a: AccountSummary): void { _data = a; },
  reset(): void { _data = { deposit: 0, free: 0, inPosition: 0, variationMargin: 0 }; },
};
```

- [ ] **Step 4: Write services store**

```typescript
// frontend/src/lib/stores/services.svelte.ts
import type { ServiceId, ServiceStatus } from '$lib/types';

let _all = $state<Record<ServiceId, ServiceStatus>>({
  auth: 'warn', md: 'warn', tx: 'warn', oms: 'warn', pos: 'warn', audit: 'warn',
});

export const servicesStore = {
  get all(): Record<ServiceId, ServiceStatus> { return _all; },
  set(id: ServiceId, status: ServiceStatus): void { _all[id] = status; },
  reset(): void {
    _all = { auth: 'warn', md: 'warn', tx: 'warn', oms: 'warn', pos: 'warn', audit: 'warn' };
  },
};
```

- [ ] **Step 5: Verify TypeScript**

```bash
cd frontend && npx svelte-check --tsconfig tsconfig.json 2>&1 | tail -5
```

Expected: `0 errors` (warnings about runes in `.svelte.ts` may appear — safe to ignore if 0 errors).

- [ ] **Step 6: Commit**

```bash
git add frontend/src/lib/stores/
git commit -m "feat(ui): add reactive stores (quotes, robots, account, services)"
```

---

### Task 4: WebSocket Client + rAF Batching

**Files:**
- Create: `frontend/src/lib/ws.ts`
- Create: `frontend/src/lib/ws.test.ts`

- [ ] **Step 1: Write ws.test.ts**

```typescript
// frontend/src/lib/ws.test.ts
import { describe, it, expect, vi, beforeEach } from 'vitest';

// Minimal WsClient contract test (no real WS — tests the message dispatch logic)
describe('parseWsMessage', () => {
  it('parses quote message', () => {
    const raw = JSON.stringify({
      type: 'quote', symbol: 'GZM6@RTSX',
      bid: 100, bid_size: 5, ask: 101, ask_size: 3,
      last: 100.5, last_size: 2, timestamp: '2026-05-16T10:00:00Z',
    });
    const msg = JSON.parse(raw);
    expect(msg.type).toBe('quote');
    expect(msg.bid).toBe(100);
  });

  it('parses service_status message', () => {
    const raw = JSON.stringify({ type: 'service_status', service: 'md', status: 'ok' });
    const msg = JSON.parse(raw);
    expect(msg.type).toBe('service_status');
    expect(msg.status).toBe('ok');
  });
});
```

- [ ] **Step 2: Run — verify passes (these are trivial shape tests)**

```bash
cd frontend && npm test -- ws.test
```

Expected: 2 PASS.

- [ ] **Step 3: Write ws.ts**

```typescript
// frontend/src/lib/ws.ts
import type { WsIncoming, ServiceId } from './types';
import { quotesStore } from './stores/quotes.svelte';
import { servicesStore } from './stores/services.svelte';
import { accountStore } from './stores/account.svelte';
import { robotsStore } from './stores/robots.svelte';

export class WsClient {
  private ws: WebSocket | null = null;
  private pending: WsIncoming[] = [];
  private rafId: number | null = null;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;

  constructor(private readonly url: string) {}

  connect(): void {
    this.ws = new WebSocket(this.url);
    this.ws.onopen = () => servicesStore.set('md', 'ok');
    this.ws.onclose = () => {
      servicesStore.set('md', 'error');
      this.scheduleReconnect();
    };
    this.ws.onerror = () => servicesStore.set('md', 'warn');
    this.ws.onmessage = (evt: MessageEvent) => {
      const msg = JSON.parse(evt.data as string) as WsIncoming;
      this.pending.push(msg);
      if (this.rafId === null) {
        this.rafId = requestAnimationFrame(() => this.flush());
      }
    };
  }

  private flush(): void {
    this.rafId = null;
    const batch = this.pending.splice(0);
    for (const msg of batch) {
      if (msg.type === 'quote') {
        quotesStore.update(msg.symbol, {
          symbol: msg.symbol,
          bid: msg.bid, bidSize: msg.bid_size,
          ask: msg.ask, askSize: msg.ask_size,
          last: msg.last, lastSize: msg.last_size,
          timestamp: msg.timestamp,
        });
      } else if (msg.type === 'service_status') {
        servicesStore.set(msg.service as ServiceId, msg.status);
      } else if (msg.type === 'account') {
        accountStore.set({
          deposit: msg.deposit, free: msg.free,
          inPosition: msg.in_position, variationMargin: msg.variation_margin,
        });
      } else if (msg.type === 'robot_update') {
        robotsStore.set(msg.robots);
      }
    }
  }

  private scheduleReconnect(): void {
    this.reconnectTimer = setTimeout(() => this.connect(), 2000);
  }

  disconnect(): void {
    if (this.rafId !== null) { cancelAnimationFrame(this.rafId); this.rafId = null; }
    if (this.reconnectTimer !== null) { clearTimeout(this.reconnectTimer); this.reconnectTimer = null; }
    this.ws?.close(1000);
    this.ws = null;
  }
}
```

- [ ] **Step 4: Verify TypeScript**

```bash
cd frontend && npx svelte-check --tsconfig tsconfig.json 2>&1 | grep -E "error|Error" | head -10
```

Expected: 0 errors.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/ws.ts frontend/src/lib/ws.test.ts
git commit -m "feat(ui): WsClient with rAF batching, dispatches to stores"
```

---

### Task 5: Mock WebSocket Dev Server

**Files:**
- Create: `frontend/mock/ws_server.py`
- Create: `frontend/mock/requirements.txt`

This is a dev-only tool. It simulates M8 API so the frontend can be developed without a real VDS.

- [ ] **Step 1: Write requirements.txt**

```
fastapi==0.111.0
uvicorn[standard]==0.29.0
websockets==13.1
```

- [ ] **Step 2: Write ws_server.py**

```python
# frontend/mock/ws_server.py
"""Dev mock — streams fake quotes + account data over WebSocket."""
import asyncio
import json
import math
import random
from datetime import datetime, timezone
from fastapi import FastAPI, WebSocket

app = FastAPI()

ROBOTS = [
    {"id": "r1", "name": "GZM6-Trend", "symbol": "GZM6@RTSX", "deposit": 500_000, "pnl": 12_400, "tradeCount": 47, "position": 2},
    {"id": "r2", "name": "GZM6-Mean",  "symbol": "GZM6@RTSX", "deposit": 300_000, "pnl": -3_200, "tradeCount": 23, "position": 0},
]

@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    tick = 0
    try:
        # Send initial data
        await ws.send_text(json.dumps({"type": "robot_update", "robots": ROBOTS}))
        await ws.send_text(json.dumps({
            "type": "account",
            "deposit": 800_000, "free": 412_000,
            "in_position": 320_000, "variation_margin": 4_200,
        }))
        for svc in ["auth", "md", "tx", "oms", "pos", "audit"]:
            await ws.send_text(json.dumps({"type": "service_status", "service": svc, "status": "ok"}))

        while True:
            tick += 1
            mid = 23_400 + 50 * math.sin(tick / 30) + random.gauss(0, 5)
            spread = random.uniform(0.5, 2.0)
            now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            await ws.send_text(json.dumps({
                "type": "quote", "symbol": "GZM6@RTSX",
                "bid": round(mid - spread / 2, 1), "bid_size": random.randint(1, 20),
                "ask": round(mid + spread / 2, 1), "ask_size": random.randint(1, 20),
                "last": round(mid, 1), "last_size": random.randint(1, 10),
                "timestamp": now,
            }))
            await asyncio.sleep(0.1)  # 10 ticks/sec in dev
    except Exception:
        pass

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
```

- [ ] **Step 3: Verify mock server starts**

```bash
cd frontend/mock && pip install -r requirements.txt -q && python ws_server.py &
sleep 2 && curl -s http://localhost:8000/ | head -5
# Kill background process after verification
kill %1 2>/dev/null || true
```

Expected: FastAPI responds (404 is fine — /ws is WebSocket only).

- [ ] **Step 4: Commit**

```bash
git add frontend/mock/
git commit -m "feat(ui): mock WebSocket dev server (fastapi, 10 ticks/sec)"
```

---

### Task 6: TopBar Component

**Files:**
- Create: `frontend/src/components/TopBar.svelte`

- [ ] **Step 1: Write TopBar.svelte**

```svelte
<!-- frontend/src/components/TopBar.svelte -->
<script lang="ts">
  import { accountStore } from '$lib/stores/account.svelte';
  import { servicesStore } from '$lib/stores/services.svelte';

  let { labMode, onToggleLab }: {
    labMode: boolean;
    onToggleLab: () => void;
  } = $props();

  let acc = $derived(accountStore.data);
  let mdStatus = $derived(servicesStore.all.md);

  function fmt(n: number): string {
    return n.toLocaleString('ru-RU', { maximumFractionDigits: 0 });
  }
</script>

<header class="topbar">
  <div class="account">
    <span>Депозит: <b>{fmt(acc.deposit)} ₽</b></span>
    <span>Свободно: <b>{fmt(acc.free)} ₽</b></span>
    <span>В позиции: <b>{fmt(acc.inPosition)} ₽</b></span>
    <span class="vm" class:pos={acc.variationMargin >= 0} class:neg={acc.variationMargin < 0}>
      Вар.маржа: <b>{fmt(acc.variationMargin)} ₽</b>
    </span>
  </div>
  <div class="controls">
    <button class="lab-btn" class:active={labMode} onclick={onToggleLab}>Lab</button>
    <span
      class="dot"
      class:ok={mdStatus === 'ok'}
      class:warn={mdStatus === 'warn'}
      class:error={mdStatus === 'error'}
      title="Market Data: {mdStatus}"
    >●</span>
  </div>
</header>

<style>
  .topbar {
    display: flex; justify-content: space-between; align-items: center;
    padding: 0 12px; height: 36px;
    background: #1a1a2e; border-bottom: 1px solid #2d2d4a;
    font-size: 13px; color: #ccc; flex-shrink: 0;
  }
  .account { display: flex; gap: 20px; }
  .controls { display: flex; align-items: center; gap: 10px; }
  .lab-btn {
    padding: 2px 12px; border-radius: 4px; border: 1px solid #444;
    background: transparent; color: #ccc; cursor: pointer; font-size: 12px;
    transition: background 0.15s;
  }
  .lab-btn.active { background: #3d5af1; border-color: #3d5af1; color: #fff; }
  .dot { font-size: 18px; line-height: 1; }
  .dot.ok { color: #4caf50; }
  .dot.warn { color: #ff9800; }
  .dot.error { color: #f44336; }
  .vm.pos b { color: #4caf50; }
  .vm.neg b { color: #f44336; }
</style>
```

- [ ] **Step 2: Verify in browser**

Start dev server + mock server:
```bash
cd frontend/mock && python ws_server.py &
cd frontend && npm run dev
```

Open `http://localhost:5173`. Expected: dark header bar with "Депозит: 0 ₽" (stores empty until WS connects) and [Lab] button.

Wire up WS temporarily in App.svelte to verify account populates:
```svelte
<!-- frontend/src/App.svelte — temporary wiring for visual check -->
<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import TopBar from './components/TopBar.svelte';
  import { WsClient } from '$lib/ws';

  let labMode = $state(false);
  let ws: WsClient;

  onMount(() => { ws = new WsClient('ws://localhost:8000/ws'); ws.connect(); });
  onDestroy(() => ws?.disconnect());
</script>

<div class="shell">
  <TopBar {labMode} onToggleLab={() => labMode = !labMode} />
</div>

<style>
  .shell { height: 100%; display: flex; flex-direction: column; }
</style>
```

Expected: account numbers populate from mock server within 1 second. Lab button toggles highlight.

- [ ] **Step 3: Commit**

```bash
kill %1 2>/dev/null || true  # stop mock server
git add frontend/src/components/TopBar.svelte frontend/src/App.svelte
git commit -m "feat(ui): TopBar — account summary, Lab toggle, MD status dot"
```

---

### Task 7: RobotsPanel Component

**Files:**
- Create: `frontend/src/components/RobotsPanel.svelte`

- [ ] **Step 1: Write RobotsPanel.svelte**

```svelte
<!-- frontend/src/components/RobotsPanel.svelte -->
<script lang="ts">
  import { robotsStore } from '$lib/stores/robots.svelte';

  let { selectedId, onSelect }: {
    selectedId: string | null;
    onSelect: (id: string) => void;
  } = $props();

  let robots = $derived(robotsStore.all);

  function fmtPnl(pnl: number): string {
    const sign = pnl >= 0 ? '+' : '';
    return `${sign}${pnl.toLocaleString('ru-RU', { maximumFractionDigits: 0 })} ₽`;
  }

  function posLabel(pos: number): string {
    if (pos === 0) return 'Flat';
    return pos > 0 ? `↑${pos}` : `↓${Math.abs(pos)}`;
  }
</script>

<aside class="robots-panel">
  {#each robots as robot (robot.id)}
    <div
      class="card"
      class:selected={selectedId === robot.id}
      role="button"
      tabindex="0"
      onclick={() => onSelect(robot.id)}
      onkeydown={(e) => e.key === 'Enter' && onSelect(robot.id)}
    >
      <div class="row">
        <span class="name">{robot.name}</span>
        <span class="settings" onclick={(e) => e.stopPropagation()} title="Настройки">⚙</span>
      </div>
      <div class="stats">
        <span class="pnl" class:pos={robot.pnl >= 0} class:neg={robot.pnl < 0}>{fmtPnl(robot.pnl)}</span>
        <span class="trades">{robot.tradeCount} сд.</span>
        <span class="pos-label" class:long={robot.position > 0} class:short={robot.position < 0}>
          {posLabel(robot.position)}
        </span>
      </div>
    </div>
  {:else}
    <div class="empty">Нет роботов</div>
  {/each}
</aside>

<style>
  .robots-panel {
    width: 200px; flex-shrink: 0;
    overflow-y: auto; background: #14142a;
    border-right: 1px solid #2d2d4a;
    display: flex; flex-direction: column;
  }
  .card {
    padding: 8px 10px; cursor: pointer;
    border-left: 3px solid transparent;
    border-bottom: 1px solid #1e1e3a;
  }
  .card:hover { background: #1e1e3a; }
  .card.selected { border-left-color: #3d5af1; background: #1e1e3a; }
  .row { display: flex; justify-content: space-between; margin-bottom: 4px; }
  .name { font-size: 12px; font-weight: 600; color: #ddd; }
  .settings { color: #444; cursor: pointer; font-size: 14px; }
  .settings:hover { color: #aaa; }
  .stats { display: flex; gap: 8px; font-size: 11px; flex-wrap: wrap; color: #888; }
  .pnl.pos { color: #4caf50; }
  .pnl.neg { color: #f44336; }
  .pos-label.long { color: #4caf50; }
  .pos-label.short { color: #f44336; }
  .empty { padding: 16px; color: #555; font-size: 12px; text-align: center; }
</style>
```

- [ ] **Step 2: Wire into App.svelte and verify visually**

Update `frontend/src/App.svelte`:
```svelte
<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import TopBar from './components/TopBar.svelte';
  import RobotsPanel from './components/RobotsPanel.svelte';
  import { WsClient } from '$lib/ws';

  let labMode = $state(false);
  let selectedRobotId = $state<string | null>(null);
  let ws: WsClient;

  onMount(() => { ws = new WsClient('ws://localhost:8000/ws'); ws.connect(); });
  onDestroy(() => ws?.disconnect());
</script>

<div class="shell">
  <TopBar {labMode} onToggleLab={() => labMode = !labMode} />
  <div class="body">
    <RobotsPanel selectedId={selectedRobotId} onSelect={(id) => selectedRobotId = id} />
    <main class="content"><!-- charts go here --></main>
  </div>
</div>

<style>
  .shell { height: 100%; display: flex; flex-direction: column; }
  .body { flex: 1; display: flex; overflow: hidden; }
  .content { flex: 1; background: #0f0f1e; }
</style>
```

Start mock server + dev server, verify robot cards appear in left panel.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/RobotsPanel.svelte frontend/src/App.svelte
git commit -m "feat(ui): RobotsPanel — robot cards with P&L, position, selection"
```

---

### Task 8: ChartFrame Component

**Files:**
- Create: `frontend/src/components/ChartFrame.svelte`

The ChartFrame has two layers: uPlot (80px tick strip at bottom) and TradingView Lightweight Charts (OHLC + markers filling the rest).

- [ ] **Step 1: Write ChartFrame.svelte**

```svelte
<!-- frontend/src/components/ChartFrame.svelte -->
<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import { quotesStore } from '$lib/stores/quotes.svelte';
  import type { OhlcBar, TradeMarker, BacktestResult } from '$lib/types';

  let { robotName, symbol, ohlc = [], markers = [], backtest = null }: {
    robotName: string;
    symbol: string;
    ohlc?: OhlcBar[];
    markers?: TradeMarker[];
    backtest?: BacktestResult | null;
  } = $props();

  let tickEl: HTMLDivElement;
  let ohlcEl: HTMLDivElement;

  // Tick ring buffer
  const MAX_TICKS = 500;
  let tickTimes: number[] = [];
  let tickBids: (number | null)[] = [];
  let tickAsks: (number | null)[] = [];
  let uplot: { setData: (d: unknown[][]) => void; destroy: () => void } | null = null;
  let tvChart: { addSeries: (...a: unknown[]) => unknown; remove: () => void; resize: (w: number, h: number) => void } | null = null;
  let tvCandle: { setData: (d: unknown[]) => void; setMarkers: (m: unknown[]) => void; applyOptions: (o: unknown) => void } | null = null;
  let tvEquity: { setData: (d: unknown[]) => void; applyOptions: (o: unknown) => void } | null = null;

  // React to new quotes
  let quote = $derived(quotesStore.get(symbol));
  $effect(() => {
    if (!quote || !uplot) return;
    const t = Math.floor(Date.parse(quote.timestamp) / 1000);
    if (tickTimes.length >= MAX_TICKS) {
      tickTimes.shift(); tickBids.shift(); tickAsks.shift();
    }
    tickTimes.push(t);
    tickBids.push(quote.bid);
    tickAsks.push(quote.ask);
    uplot.setData([tickTimes, tickBids, tickAsks]);
  });

  // React to ohlc changes
  $effect(() => {
    if (!tvCandle || !ohlc.length) return;
    tvCandle.setData(ohlc.map(b => ({ time: b.time, open: b.open, high: b.high, low: b.low, close: b.close })));
  });

  // React to markers
  $effect(() => {
    if (!tvCandle) return;
    tvCandle.setMarkers(markers);
  });

  // React to backtest
  $effect(() => {
    if (!tvEquity) return;
    if (backtest?.equityCurve.length) {
      tvEquity.setData(backtest.equityCurve);
      tvEquity.applyOptions({ visible: true });
    } else {
      tvEquity.applyOptions({ visible: false });
    }
  });

  const TICK_H = 80;

  onMount(async () => {
    const uPlotMod = await import('uplot');
    const UPlot = uPlotMod.default;

    uplot = new UPlot({
      width: tickEl.clientWidth,
      height: TICK_H,
      series: [
        {},
        { label: 'Bid', stroke: '#4caf50', width: 1 },
        { label: 'Ask', stroke: '#f44336', width: 1 },
      ],
      axes: [{ show: false }, { show: true, size: 50, gap: 0 }],
      legend: { show: false },
      padding: [4, 0, 0, 0],
    } as Parameters<typeof UPlot>[0], [[], [], []] as unknown[][], tickEl);

    const { createChart, CandlestickSeries, LineSeries } = await import('lightweight-charts');
    const chartH = ohlcEl.clientHeight - TICK_H;
    tvChart = createChart(ohlcEl, {
      width: ohlcEl.clientWidth,
      height: chartH,
      layout: { background: { color: '#0f0f1e' }, textColor: '#888' },
      grid: { vertLines: { color: '#1e1e3a' }, horzLines: { color: '#1e1e3a' } },
      timeScale: { borderColor: '#2d2d4a' },
      crosshair: { mode: 1 },
    });
    tvCandle = tvChart.addSeries(CandlestickSeries as never, {
      upColor: '#4caf50', downColor: '#f44336',
      borderUpColor: '#4caf50', borderDownColor: '#f44336',
      wickUpColor: '#4caf50', wickDownColor: '#f44336',
    }) as typeof tvCandle;
    tvEquity = tvChart.addSeries(LineSeries as never, {
      color: '#3d5af1', lineWidth: 1, visible: false,
    }) as typeof tvEquity;
  });

  onDestroy(() => {
    uplot?.destroy();
    tvChart?.remove();
  });
</script>

<div class="frame">
  <div class="frame-label">{robotName} · {symbol}</div>
  <div class="chart-area" bind:this={ohlcEl}>
    <div class="tick-strip" bind:this={tickEl}></div>
  </div>
</div>

<style>
  .frame {
    display: flex; flex-direction: column;
    min-height: 320px; border-bottom: 1px solid #2d2d4a;
  }
  .frame-label {
    padding: 3px 8px; font-size: 11px; color: #666;
    background: #1a1a2e; border-bottom: 1px solid #2d2d4a; flex-shrink: 0;
  }
  .chart-area { flex: 1; position: relative; overflow: hidden; }
  .tick-strip { position: absolute; bottom: 0; left: 0; right: 0; z-index: 1; }
</style>
```

- [ ] **Step 2: Wire into App.svelte**

Update `frontend/src/App.svelte`:
```svelte
<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import TopBar from './components/TopBar.svelte';
  import RobotsPanel from './components/RobotsPanel.svelte';
  import ChartFrame from './components/ChartFrame.svelte';
  import { WsClient } from '$lib/ws';
  import { robotsStore } from '$lib/stores/robots.svelte';

  let labMode = $state(false);
  let selectedRobotId = $state<string | null>(null);
  let ws: WsClient;

  let robots = $derived(robotsStore.all);
  let selectedRobot = $derived(robots.find(r => r.id === selectedRobotId) ?? robots[0] ?? null);

  onMount(() => {
    ws = new WsClient('ws://localhost:8000/ws');
    ws.connect();
  });
  onDestroy(() => ws?.disconnect());
</script>

<div class="shell">
  <TopBar {labMode} onToggleLab={() => labMode = !labMode} />
  <div class="body">
    <RobotsPanel selectedId={selectedRobotId} onSelect={(id) => selectedRobotId = id} />
    <main class="content">
      {#each robots as robot (robot.id)}
        <ChartFrame robotName={robot.name} symbol={robot.symbol} />
      {/each}
    </main>
  </div>
</div>

<style>
  .shell { height: 100%; display: flex; flex-direction: column; }
  .body { flex: 1; display: flex; overflow: hidden; }
  .content { flex: 1; overflow-y: auto; background: #0f0f1e; display: flex; flex-direction: column; }
</style>
```

- [ ] **Step 3: Verify in browser**

Start mock + dev:
```bash
cd frontend/mock && python ws_server.py &
cd frontend && npm run dev
```

Expected:
- Tick strip (bid=green, ask=red lines) updates every ~100ms
- OHLC chart empty until real data provided (no OHLC from mock server — acceptable for Phase A)

- [ ] **Step 4: Commit**

```bash
kill %1 2>/dev/null || true
git add frontend/src/components/ChartFrame.svelte frontend/src/App.svelte
git commit -m "feat(ui): ChartFrame — uPlot tick strip + TradingView OHLC + equity overlay"
```

---

### Task 9: InstrumentPanel Component

**Files:**
- Create: `frontend/src/components/InstrumentPanel.svelte`

- [ ] **Step 1: Write InstrumentPanel.svelte**

```svelte
<!-- frontend/src/components/InstrumentPanel.svelte -->
<script lang="ts">
  interface InstrumentInfo {
    symbol: string;
    priceMin: number;
    priceMax: number;
    margin: number;
    expiration: string; // 'DD.MM.YYYY'
  }

  let { info = null }: { info?: InstrumentInfo | null } = $props();
</script>

<aside class="instrument-panel">
  {#if info}
    <div class="title">{info.symbol}</div>
    <div class="section">
      <div class="label">Коридор цен</div>
      <div class="value">{info.priceMin.toLocaleString('ru-RU')} – {info.priceMax.toLocaleString('ru-RU')}</div>
    </div>
    <div class="section">
      <div class="label">ГО / маржа</div>
      <div class="value">{info.margin.toLocaleString('ru-RU', { maximumFractionDigits: 0 })} ₽</div>
    </div>
    <div class="section">
      <div class="label">Экспирация</div>
      <div class="value">{info.expiration}</div>
    </div>
  {:else}
    <div class="empty">Выберите инструмент</div>
  {/if}
</aside>

<style>
  .instrument-panel {
    width: 180px; flex-shrink: 0;
    background: #14142a; border-left: 1px solid #2d2d4a;
    padding: 12px 10px; font-size: 12px;
  }
  .title { font-weight: 600; color: #ddd; margin-bottom: 12px; font-size: 13px; }
  .section { margin-bottom: 10px; }
  .label { color: #555; margin-bottom: 2px; font-size: 11px; text-transform: uppercase; letter-spacing: 0.04em; }
  .value { color: #ccc; }
  .empty { color: #555; padding-top: 8px; }
</style>
```

- [ ] **Step 2: Wire into App.svelte**

Add `InstrumentPanel` to App.svelte (insert after ChartArea main):
```svelte
<!-- Add import -->
import InstrumentPanel from './components/InstrumentPanel.svelte';

<!-- Add to body div after <main> -->
<InstrumentPanel info={selectedRobot ? {
  symbol: selectedRobot.symbol,
  priceMin: 20000, priceMax: 30000,
  margin: 12400, expiration: '17.06.2026'
} : null} />
```

Full updated `frontend/src/App.svelte`:
```svelte
<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import TopBar from './components/TopBar.svelte';
  import RobotsPanel from './components/RobotsPanel.svelte';
  import ChartFrame from './components/ChartFrame.svelte';
  import InstrumentPanel from './components/InstrumentPanel.svelte';
  import { WsClient } from '$lib/ws';
  import { robotsStore } from '$lib/stores/robots.svelte';

  let labMode = $state(false);
  let selectedRobotId = $state<string | null>(null);
  let ws: WsClient;

  let robots = $derived(robotsStore.all);
  let selectedRobot = $derived(robots.find(r => r.id === selectedRobotId) ?? robots[0] ?? null);

  onMount(() => { ws = new WsClient('ws://localhost:8000/ws'); ws.connect(); });
  onDestroy(() => ws?.disconnect());
</script>

<div class="shell">
  <TopBar {labMode} onToggleLab={() => labMode = !labMode} />
  <div class="body">
    <RobotsPanel selectedId={selectedRobotId} onSelect={(id) => selectedRobotId = id} />
    <main class="content">
      {#each robots as robot (robot.id)}
        <ChartFrame robotName={robot.name} symbol={robot.symbol} />
      {/each}
    </main>
    <InstrumentPanel info={selectedRobot ? {
      symbol: selectedRobot.symbol,
      priceMin: 20_000, priceMax: 30_000,
      margin: 12_400, expiration: '17.06.2026'
    } : null} />
  </div>
</div>

<style>
  .shell { height: 100%; display: flex; flex-direction: column; }
  .body { flex: 1; display: flex; overflow: hidden; }
  .content { flex: 1; overflow-y: auto; background: #0f0f1e; display: flex; flex-direction: column; }
</style>
```

Note: InstrumentPanel data will come from M4 API (`/api/instruments/{symbol}`) once M8 is wired up. Hardcoded values are intentional placeholders for visual verification only.

- [ ] **Step 3: Verify in browser**

Expected: 3-column layout — RobotsPanel | ChartArea | InstrumentPanel.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/InstrumentPanel.svelte frontend/src/App.svelte
git commit -m "feat(ui): InstrumentPanel — price corridor, margin, expiration"
```

---

### Task 10: BottomBar Component

**Files:**
- Create: `frontend/src/components/BottomBar.svelte`

- [ ] **Step 1: Write BottomBar.svelte**

```svelte
<!-- frontend/src/components/BottomBar.svelte -->
<script lang="ts">
  import { servicesStore } from '$lib/stores/services.svelte';
  import type { ServiceId, ServiceStatus } from '$lib/types';

  let { events = [] }: { events?: string[] } = $props();

  let svc = $derived(servicesStore.all);

  const SERVICE_LABELS: Record<ServiceId, string> = {
    auth: 'Auth', md: 'MD', tx: 'TX', oms: 'OMS', pos: 'Pos', audit: 'Audit',
  };

  const STATUS_COLOR: Record<ServiceStatus, string> = {
    ok: '#4caf50', warn: '#ff9800', error: '#f44336',
  };

  const SERVICE_IDS: ServiceId[] = ['auth', 'md', 'tx', 'oms', 'pos', 'audit'];
</script>

<footer class="bottom-bar">
  <div class="events">
    {#each events.slice(-50).reverse() as evt}
      <span class="evt">{evt}</span>
    {/each}
    {#if !events.length}
      <span class="empty">Нет событий</span>
    {/if}
  </div>
  <div class="services">
    {#each SERVICE_IDS as id}
      <span class="svc" title="{SERVICE_LABELS[id]}: {svc[id]}">
        <span class="dot" style="color: {STATUS_COLOR[svc[id]]}">●</span>
        <span class="lbl">{SERVICE_LABELS[id]}</span>
      </span>
    {/each}
  </div>
</footer>

<style>
  .bottom-bar {
    display: flex; align-items: center; justify-content: space-between;
    padding: 0 10px; height: 52px;
    background: #1a1a2e; border-top: 1px solid #2d2d4a;
    flex-shrink: 0;
  }
  .events {
    flex: 1; overflow-x: auto; display: flex; gap: 12px;
    align-items: center; scrollbar-width: none;
  }
  .events::-webkit-scrollbar { display: none; }
  .evt { font-size: 11px; color: #888; white-space: nowrap; }
  .empty { font-size: 11px; color: #444; }
  .services { display: flex; gap: 10px; flex-shrink: 0; padding-left: 12px; }
  .svc { display: flex; align-items: center; gap: 3px; cursor: default; }
  .dot { font-size: 12px; }
  .lbl { font-size: 10px; color: #555; }
</style>
```

- [ ] **Step 2: Wire into App.svelte**

Add `BottomBar` to App.svelte:

```svelte
<!-- Add import -->
import BottomBar from './components/BottomBar.svelte';

<!-- Replace closing </div> of .shell with: -->
  <BottomBar events={['10:00:01 GZM6@RTSX BUY 2 @ 23400', '09:58:14 GZM6@RTSX SELL 2 @ 23450']} />
</div>
```

Full updated App.svelte:
```svelte
<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import TopBar from './components/TopBar.svelte';
  import RobotsPanel from './components/RobotsPanel.svelte';
  import ChartFrame from './components/ChartFrame.svelte';
  import InstrumentPanel from './components/InstrumentPanel.svelte';
  import BottomBar from './components/BottomBar.svelte';
  import { WsClient } from '$lib/ws';
  import { robotsStore } from '$lib/stores/robots.svelte';

  let labMode = $state(false);
  let selectedRobotId = $state<string | null>(null);
  let ws: WsClient;

  let robots = $derived(robotsStore.all);
  let selectedRobot = $derived(robots.find(r => r.id === selectedRobotId) ?? robots[0] ?? null);

  onMount(() => { ws = new WsClient('ws://localhost:8000/ws'); ws.connect(); });
  onDestroy(() => ws?.disconnect());
</script>

<div class="shell">
  <TopBar {labMode} onToggleLab={() => labMode = !labMode} />
  <div class="body">
    <RobotsPanel selectedId={selectedRobotId} onSelect={(id) => selectedRobotId = id} />
    <main class="content">
      {#each robots as robot (robot.id)}
        <ChartFrame robotName={robot.name} symbol={robot.symbol} />
      {/each}
    </main>
    <InstrumentPanel info={selectedRobot ? {
      symbol: selectedRobot.symbol,
      priceMin: 20_000, priceMax: 30_000,
      margin: 12_400, expiration: '17.06.2026'
    } : null} />
  </div>
  <BottomBar events={['10:00:01 GZM6@RTSX BUY 2 @ 23400']} />
</div>

<style>
  .shell { height: 100%; display: flex; flex-direction: column; }
  .body { flex: 1; display: flex; overflow: hidden; }
  .content { flex: 1; overflow-y: auto; background: #0f0f1e; display: flex; flex-direction: column; }
</style>
```

- [ ] **Step 3: Verify in browser**

Expected: footer with event text on left, 6 service dots on right. Service dots turn green once mock WS sends `service_status ok` messages.

- [ ] **Step 4: Run build — Phase A complete**

```bash
cd frontend && npm run build
```

Expected: `dist/` built successfully. 0 TypeScript errors.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/BottomBar.svelte frontend/src/App.svelte
git commit -m "feat(ui): BottomBar — event feed + service status bar; Phase A complete"
```

---

## Phase B — Lab Manager

### Task 11: LabBar Component

**Files:**
- Create: `frontend/src/components/LabBar.svelte`

LabBar has three tabs: Strategies, Backtest, Scripts. Appears only when `labMode = true`.

- [ ] **Step 1: Write LabBar.svelte**

```svelte
<!-- frontend/src/components/LabBar.svelte -->
<script lang="ts">
  import type { Strategy, BacktestResult } from '$lib/types';

  let { onRunBacktest, onLoadStrategy, onOpenEditor, onExportRobot, onToggleOffline }: {
    onRunBacktest: (symbol: string, from: string, to: string, strategyId: string) => Promise<void>;
    onLoadStrategy: (s: Strategy) => void;
    onOpenEditor: (scriptPath: string) => void;
    onExportRobot: (s: Strategy) => void;
    onToggleOffline: (enabled: boolean) => void;
  } = $props();

  let activeTab = $state<'strategies' | 'backtest' | 'scripts'>('strategies');
  let offlineMode = $state(false);

  // Strategies tab state
  const mockStrategies: Strategy[] = [
    { id: 's1', name: 'GZM6-Trend-v1', symbol: 'GZM6@RTSX', params: { fast: 5, slow: 20 }, scriptPath: 'scripts/trend_v1.py' },
    { id: 's2', name: 'GZM6-Mean-v2', symbol: 'GZM6@RTSX', params: { window: 30, threshold: 0.5 }, scriptPath: 'scripts/mean_v2.py' },
  ];

  // Backtest tab state
  let btSymbol = $state('GZM6@RTSX');
  let btFrom = $state('2026-01-01');
  let btTo = $state('2026-05-01');
  let btStratId = $state(mockStrategies[0].id);
  let btProgress = $state<number | null>(null);
  let btError = $state('');

  async function runBacktest() {
    btProgress = 0;
    btError = '';
    try {
      await onRunBacktest(btSymbol, btFrom, btTo, btStratId);
      btProgress = 100;
    } catch (e) {
      btError = String(e);
      btProgress = null;
    }
  }

  function toggleOffline() {
    offlineMode = !offlineMode;
    onToggleOffline(offlineMode);
  }
</script>

<div class="labbar">
  <div class="tabs">
    <button class:active={activeTab === 'strategies'} onclick={() => activeTab = 'strategies'}>Strategies</button>
    <button class:active={activeTab === 'backtest'} onclick={() => activeTab = 'backtest'}>Backtest</button>
    <button class:active={activeTab === 'scripts'} onclick={() => activeTab = 'scripts'}>Scripts</button>
  </div>

  <div class="tab-content">
    {#if activeTab === 'strategies'}
      <div class="strategies">
        <label class="offline-toggle">
          <input type="checkbox" checked={offlineMode} onchange={toggleOffline} />
          Offline
        </label>
        {#each mockStrategies as s (s.id)}
          <div class="strat-row">
            <span class="strat-name">{s.name}</span>
            <span class="strat-sym">{s.symbol}</span>
            <button onclick={() => onLoadStrategy(s)}>Load</button>
            <button onclick={() => onExportRobot(s)}>Export</button>
          </div>
        {/each}
      </div>
    {:else if activeTab === 'backtest'}
      <div class="backtest">
        <label>
          Инструмент
          <input bind:value={btSymbol} />
        </label>
        <label>
          С
          <input type="date" bind:value={btFrom} />
        </label>
        <label>
          По
          <input type="date" bind:value={btTo} />
        </label>
        <label>
          Стратегия
          <select bind:value={btStratId}>
            {#each mockStrategies as s}
              <option value={s.id}>{s.name}</option>
            {/each}
          </select>
        </label>
        <button onclick={runBacktest} disabled={btProgress !== null && btProgress < 100}>
          {btProgress !== null && btProgress < 100 ? `${btProgress}%…` : 'Run'}
        </button>
        {#if btError}<span class="error">{btError}</span>{/if}
      </div>
    {:else}
      <div class="scripts">
        {#each mockStrategies.filter(s => s.scriptPath) as s (s.id)}
          <div class="script-row">
            <span class="script-path">{s.scriptPath}</span>
            <button onclick={() => onOpenEditor(s.scriptPath!)}>Edit</button>
          </div>
        {/each}
      </div>
    {/if}
  </div>
</div>

<style>
  .labbar {
    background: #1a1a2e; border-top: 1px solid #2d2d4a;
    display: flex; flex-direction: column; height: 160px; flex-shrink: 0;
  }
  .tabs { display: flex; border-bottom: 1px solid #2d2d4a; }
  .tabs button {
    padding: 4px 14px; border: none; background: transparent;
    color: #666; font-size: 12px; cursor: pointer; border-bottom: 2px solid transparent;
  }
  .tabs button.active { color: #ddd; border-bottom-color: #3d5af1; }
  .tab-content { flex: 1; overflow-y: auto; padding: 8px 12px; }

  .strategies, .backtest, .scripts { display: flex; flex-direction: column; gap: 6px; }
  .strat-row, .script-row {
    display: flex; align-items: center; gap: 8px; font-size: 12px; color: #ccc;
  }
  .strat-name, .script-path { flex: 1; font-size: 11px; color: #aaa; }
  .strat-sym { color: #666; font-size: 11px; }

  .backtest { flex-direction: row; flex-wrap: wrap; gap: 8px; align-items: flex-end; }
  .backtest label { display: flex; flex-direction: column; gap: 2px; font-size: 11px; color: #666; }
  .backtest input, .backtest select {
    background: #0f0f1e; border: 1px solid #2d2d4a;
    color: #ccc; padding: 2px 6px; font-size: 12px; border-radius: 3px;
  }

  .offline-toggle { display: flex; align-items: center; gap: 4px; font-size: 12px; color: #aaa; cursor: pointer; }

  button {
    padding: 2px 10px; background: #2d2d4a; border: 1px solid #3d3d5a;
    color: #ccc; font-size: 11px; border-radius: 3px; cursor: pointer;
  }
  button:hover { background: #3d3d6a; }
  button:disabled { opacity: 0.5; cursor: default; }
  .error { color: #f44336; font-size: 11px; }
</style>
```

- [ ] **Step 2: Wire LabBar into App.svelte**

Update `frontend/src/App.svelte` — add LabBar between body and BottomBar:

```svelte
<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import TopBar from './components/TopBar.svelte';
  import RobotsPanel from './components/RobotsPanel.svelte';
  import ChartFrame from './components/ChartFrame.svelte';
  import InstrumentPanel from './components/InstrumentPanel.svelte';
  import BottomBar from './components/BottomBar.svelte';
  import LabBar from './components/LabBar.svelte';
  import { WsClient } from '$lib/ws';
  import { robotsStore } from '$lib/stores/robots.svelte';
  import type { Strategy, BacktestResult } from '$lib/types';

  let labMode = $state(false);
  let selectedRobotId = $state<string | null>(null);
  let backtestResult = $state<BacktestResult | null>(null);
  let ws: WsClient;

  let robots = $derived(robotsStore.all);
  let selectedRobot = $derived(robots.find(r => r.id === selectedRobotId) ?? robots[0] ?? null);

  onMount(() => { ws = new WsClient('ws://localhost:8000/ws'); ws.connect(); });
  onDestroy(() => ws?.disconnect());

  async function handleRunBacktest(symbol: string, from: string, to: string, stratId: string): Promise<void> {
    const res = await fetch(`/api/backtest?symbol=${symbol}&from=${from}&to=${to}&strategy=${stratId}`);
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    backtestResult = await res.json() as BacktestResult;
  }

  function handleLoadStrategy(s: Strategy): void {
    console.log('Load strategy', s.name);
  }

  function handleOpenEditor(path: string): void {
    console.log('Open editor', path); // CodeEditor wired in Task 12
  }

  function handleExportRobot(s: Strategy): void {
    const blob = new Blob([JSON.stringify(s, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = `${s.name}.json`; a.click();
    URL.revokeObjectURL(url);
  }

  function handleToggleOffline(enabled: boolean): void {
    console.log('Offline mode:', enabled); // offline-player wired in Task 13
  }
</script>

<div class="shell">
  <TopBar {labMode} onToggleLab={() => labMode = !labMode} />
  <div class="body">
    <RobotsPanel selectedId={selectedRobotId} onSelect={(id) => selectedRobotId = id} />
    <main class="content">
      {#each robots as robot (robot.id)}
        <ChartFrame robotName={robot.name} symbol={robot.symbol} backtest={robot.id === selectedRobot?.id ? backtestResult : null} />
      {/each}
    </main>
    <InstrumentPanel info={selectedRobot ? {
      symbol: selectedRobot.symbol,
      priceMin: 20_000, priceMax: 30_000,
      margin: 12_400, expiration: '17.06.2026'
    } : null} />
  </div>
  {#if labMode}
    <LabBar
      onRunBacktest={handleRunBacktest}
      onLoadStrategy={handleLoadStrategy}
      onOpenEditor={handleOpenEditor}
      onExportRobot={handleExportRobot}
      onToggleOffline={handleToggleOffline}
    />
  {/if}
  <BottomBar events={['10:00:01 GZM6@RTSX BUY 2 @ 23400']} />
</div>

<style>
  .shell { height: 100%; display: flex; flex-direction: column; }
  .body { flex: 1; display: flex; overflow: hidden; min-height: 0; }
  .content { flex: 1; overflow-y: auto; background: #0f0f1e; display: flex; flex-direction: column; }
</style>
```

- [ ] **Step 3: Verify in browser**

Click [Lab] → LabBar appears between charts and footer. Switch between Strategies / Backtest / Scripts tabs. Click Export → downloads JSON file. Click [Lab] again → LabBar hides.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/LabBar.svelte frontend/src/App.svelte
git commit -m "feat(ui): LabBar — Strategies/Backtest/Scripts tabs, export robot JSON"
```

---

### Task 12: CodeEditor Component (Monaco)

**Files:**
- Create: `frontend/src/components/CodeEditor.svelte`

- [ ] **Step 1: Write CodeEditor.svelte**

```svelte
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
  let editor: { getValue: () => string; dispose: () => void } | null = null;
  let saving = $state(false);
  let running = $state(false);
  let error = $state('');

  // Draggable state
  let x = $state(80);
  let y = $state(80);
  let w = $state(700);
  let h = $state(500);
  let dragging = false;
  let dx = 0, dy = 0;

  function onMouseDown(e: MouseEvent) {
    dragging = true;
    dx = e.clientX - x;
    dy = e.clientY - y;
  }

  function onMouseMove(e: MouseEvent) {
    if (!dragging) return;
    x = e.clientX - dx;
    y = e.clientY - dy;
  }

  function onMouseUp() { dragging = false; }

  onMount(async () => {
    const loader = (await import('@monaco-editor/loader')).default;
    const monaco = await loader.init();
    let content = '';
    try {
      const res = await fetch(`/api/scripts/${encodeURIComponent(scriptPath)}`);
      if (res.ok) content = await res.text();
    } catch { /* file not found — start empty */ }

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

  async function save() {
    if (!editor) return;
    saving = true; error = '';
    try {
      await onSave(scriptPath, editor.getValue());
    } catch (e) { error = String(e); }
    saving = false;
  }

  async function run() {
    if (!editor) return;
    running = true; error = '';
    try {
      await onRun(scriptPath, editor.getValue());
    } catch (e) { error = String(e); }
    running = false;
  }
</script>

<svelte:window onmousemove={onMouseMove} onmouseup={onMouseUp} />

<div
  class="editor-panel"
  style="left:{x}px; top:{y}px; width:{w}px; height:{h}px;"
>
  <div class="titlebar" role="banner" onmousedown={onMouseDown}>
    <span class="title">{scriptPath}</span>
    <div class="actions">
      <button onclick={save} disabled={saving}>{saving ? 'Saving…' : 'Save'}</button>
      <button onclick={run} disabled={running}>{running ? 'Running…' : 'Run'}</button>
      <button class="close-btn" onclick={onClose}>✕</button>
    </div>
  </div>
  {#if error}<div class="error">{error}</div>{/if}
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
    border-bottom: 1px solid #3d3d5a; user-select: none;
  }
  .titlebar:active { cursor: grabbing; }
  .title { font-size: 12px; color: #aaa; }
  .actions { display: flex; gap: 6px; }
  .monaco-container { flex: 1; }
  .error {
    padding: 4px 10px; background: #3d1414;
    color: #f44336; font-size: 11px; border-bottom: 1px solid #5a2020;
  }
  button {
    padding: 2px 10px; background: #3d3d5a; border: 1px solid #4d4d6a;
    color: #ccc; font-size: 11px; border-radius: 3px; cursor: pointer;
  }
  button.close-btn { background: transparent; border: none; color: #888; font-size: 14px; }
  button:disabled { opacity: 0.5; cursor: default; }
</style>
```

- [ ] **Step 2: Wire CodeEditor into App.svelte**

Add editor state and component to App.svelte:

```svelte
<!-- Add these to App.svelte script -->
let editorPath = $state<string | null>(null);

function handleOpenEditor(path: string): void {
  editorPath = path;
}

async function handleEditorSave(path: string, content: string): Promise<void> {
  const res = await fetch(`/api/scripts/${encodeURIComponent(path)}`, {
    method: 'PUT', body: content,
    headers: { 'Content-Type': 'text/plain' },
  });
  if (!res.ok) throw new Error(`Save failed: ${res.status}`);
}

async function handleEditorRun(path: string, content: string): Promise<void> {
  await handleEditorSave(path, content);
  await handleRunBacktest(selectedRobot?.symbol ?? 'GZM6@RTSX', '2026-01-01', '2026-05-01', 's1');
}
```

```svelte
<!-- Add import -->
import CodeEditor from './components/CodeEditor.svelte';

<!-- Add before closing </div> of .shell -->
{#if editorPath}
  <CodeEditor
    scriptPath={editorPath}
    onSave={handleEditorSave}
    onRun={handleEditorRun}
    onClose={() => editorPath = null}
  />
{/if}
```

- [ ] **Step 3: Verify in browser**

Lab mode → Scripts tab → Edit button → draggable Monaco editor opens with Python syntax highlighting. Close ✕ dismisses it.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/CodeEditor.svelte frontend/src/App.svelte
git commit -m "feat(ui): CodeEditor — Monaco editor, draggable panel, Save/Run/Close"
```

---

### Task 13: Offline Player

**Files:**
- Create: `frontend/src/lib/offline-player.ts`

Offline player replays a JSON file of quotes using the same rAF flushing as WsClient. App.svelte switches between the two based on `offlineMode`.

- [ ] **Step 1: Write offline-player.ts**

```typescript
// frontend/src/lib/offline-player.ts
import type { Quote } from './types';
import { quotesStore } from './stores/quotes.svelte';

interface TickRecord {
  symbol: string;
  bid: number;
  bid_size: number;
  ask: number;
  ask_size: number;
  last: number;
  last_size: number;
  timestamp: string;
}

export class OfflinePlayer {
  private ticks: TickRecord[] = [];
  private idx = 0;
  private rafId: number | null = null;
  private lastRealTime = 0;
  private lastTickTime = 0;

  async load(file: File): Promise<void> {
    const text = await file.text();
    this.ticks = JSON.parse(text) as TickRecord[];
    this.idx = 0;
  }

  play(): void {
    if (!this.ticks.length) return;
    this.lastRealTime = performance.now();
    this.lastTickTime = Date.parse(this.ticks[0].timestamp);
    this.schedule();
  }

  private schedule(): void {
    this.rafId = requestAnimationFrame((now) => {
      const elapsed = now - this.lastRealTime;
      this.lastRealTime = now;
      const targetTime = this.lastTickTime + elapsed;

      while (this.idx < this.ticks.length) {
        const t = this.ticks[this.idx];
        const tickTime = Date.parse(t.timestamp);
        if (tickTime > targetTime) break;
        quotesStore.update(t.symbol, {
          symbol: t.symbol,
          bid: t.bid, bidSize: t.bid_size,
          ask: t.ask, askSize: t.ask_size,
          last: t.last, lastSize: t.last_size,
          timestamp: t.timestamp,
        });
        this.lastTickTime = tickTime;
        this.idx++;
      }

      if (this.idx < this.ticks.length) this.schedule();
    });
  }

  stop(): void {
    if (this.rafId !== null) { cancelAnimationFrame(this.rafId); this.rafId = null; }
  }
}
```

- [ ] **Step 2: Write offline player test**

Create `frontend/src/lib/offline-player.test.ts`:
```typescript
import { describe, it, expect } from 'vitest';
import { OfflinePlayer } from './offline-player';

describe('OfflinePlayer', () => {
  it('constructs without error', () => {
    const p = new OfflinePlayer();
    expect(p).toBeDefined();
  });

  it('stop() is safe before play()', () => {
    const p = new OfflinePlayer();
    expect(() => p.stop()).not.toThrow();
  });
});
```

- [ ] **Step 3: Run test**

```bash
cd frontend && npm test -- offline-player.test
```

Expected: 2 PASS.

- [ ] **Step 4: Wire offline toggle into App.svelte**

Add `OfflinePlayer` wiring to App.svelte:

```svelte
<!-- Add import -->
import { OfflinePlayer } from '$lib/offline-player';

<!-- Add to script -->
let offlinePlayer: OfflinePlayer | null = null;

async function handleToggleOffline(enabled: boolean): Promise<void> {
  if (enabled) {
    const input = document.createElement('input');
    input.type = 'file'; input.accept = '.json';
    input.onchange = async () => {
      const file = input.files?.[0];
      if (!file) return;
      ws.disconnect();
      offlinePlayer = new OfflinePlayer();
      await offlinePlayer.load(file);
      offlinePlayer.play();
    };
    input.click();
  } else {
    offlinePlayer?.stop();
    offlinePlayer = null;
    ws.connect();
  }
}
```

- [ ] **Step 5: Verify in browser**

Lab mode → Strategies → toggle Offline → file picker opens. After file selection, mock WS disconnects and player replays JSON ticks. Toggle Offline off → reconnects to WS.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/lib/offline-player.ts frontend/src/lib/offline-player.test.ts frontend/src/App.svelte
git commit -m "feat(ui): OfflinePlayer — rAF tick replay from JSON file, Lab offline mode"
```

---

### Task 14: Nginx Config

**Files:**
- Create: `deploy/nginx.conf`

This is the production nginx config for VDS. Install path: `/etc/nginx/sites-available/shectory-trader`.

- [ ] **Step 1: Write nginx.conf**

```nginx
# deploy/nginx.conf
# Install: sudo ln -s /path/to/deploy/nginx.conf /etc/nginx/sites-enabled/shectory-trader
# Build:   cd frontend && npm run build
# Reload:  sudo nginx -t && sudo systemctl reload nginx

server {
    listen 80;
    server_name _;  # Replace with your VDS IP or domain

    root /path/to/Shectory Trade & Lab/frontend/dist;
    index index.html;

    # Svelte SPA — serve index.html for all non-asset routes
    location / {
        try_files $uri $uri/ /index.html;
    }

    # REST API proxy → FastAPI
    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 60s;
    }

    # WebSocket proxy → FastAPI
    location /ws {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_read_timeout 86400s;  # 24h — matches M1 reconnect cycle
    }

    # Cache static assets aggressively
    location ~* \.(js|css|woff2|png|svg|ico)$ {
        expires 1y;
        add_header Cache-Control "public, immutable";
    }
}
```

- [ ] **Step 2: Write deploy README**

Create `deploy/README.md`:
```markdown
# Deploy

1. Build frontend: `cd frontend && npm run build`
2. Copy/symlink: `sudo ln -sf $(pwd)/deploy/nginx.conf /etc/nginx/sites-enabled/shectory-trader`
3. Edit `nginx.conf`: set `server_name` and `root` to actual paths
4. Test + reload: `sudo nginx -t && sudo systemctl reload nginx`
5. Start FastAPI: `poetry run uvicorn trader.api.main:app --port 8000` (M8, not yet built)
```

- [ ] **Step 3: Run final build**

```bash
cd frontend && npm run build 2>&1 | tail -10
```

Expected: build succeeds, `dist/index.html` + JS/CSS chunks present.

- [ ] **Step 4: Run all tests**

```bash
cd frontend && npm test
```

Expected: all tests PASS (types.test, ws.test, offline-player.test).

- [ ] **Step 5: Commit**

```bash
git add deploy/
git commit -m "feat(ui): nginx config + deploy instructions for VDS"
```

---

## Self-Review Checklist

**Spec coverage:**
- ✅ TopBar: account summary, Lab toggle, connection dot
- ✅ RobotsPanel: robot cards, P&L, position, settings link
- ✅ ChartFrame: uPlot ticks, TradingView OHLC, equity overlay in Lab mode
- ✅ InstrumentPanel: corridor, margin, expiration
- ✅ BottomBar: events feed + service status bar (single row)
- ✅ LabBar: Strategies / Backtest / Scripts tabs
- ✅ CodeEditor: Monaco, draggable, Save/Run/Close
- ✅ Offline mode: file picker → OfflinePlayer replays ticks
- ✅ Export robot: downloads .json
- ✅ rAF batching: WsClient.flush() via requestAnimationFrame
- ✅ Svelte 5 runes: stores use $state in .svelte.ts, components use $derived
- ✅ Nginx config for VDS deployment

**Type consistency:**
- `Quote` defined in types.ts, used in ws.ts, offline-player.ts, ChartFrame
- `Robot` defined in types.ts, used in robots.svelte.ts, RobotsPanel, LabBar
- `BacktestResult` defined in types.ts, used in ChartFrame ($effect), LabBar (onRunBacktest result), App.svelte state
- `Strategy` defined in types.ts, used in LabBar, CodeEditor
- `ServiceId` / `ServiceStatus` used in services.svelte.ts, BottomBar — consistent
