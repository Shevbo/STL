# Unified Auth Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Unified email/password auth across Portal (shectory.ru), Trader, Komissionka, OurDiary, and PingMaster — single `portal_users` catalog, unified login screen visual (shectory-logo.gif + app badge), portal fully gated behind auth.

**Architecture:** Each app manages its own session. All satellite apps call `POST /api/internal/verify-portal-credentials` on shectory.ru (Bearer secret) to verify credentials. Portal itself switches from ADMIN_TOKEN to `portal_users` (bcrypt). Next.js middleware on portal guards all pages.

**Tech Stack:** Next.js 15 (Portal, Komissionka, OurDiary, PingMaster), FastAPI + Svelte 5 (Trader), Prisma + PostgreSQL, bcrypt, HMAC-SHA256 sessions, systemd on smain/hoster.

---

## File Map

| File | Action | App |
|------|--------|-----|
| `public/brand/shectory-logo.gif` | Create (copy) | Portal, Komissionka, OurDiary, PingMaster |
| `frontend/public/brand/shectory-logo.gif` | Create (copy) | Trader |
| `src/lib/portal-auth.ts` | Create | Portal |
| `src/app/api/auth/login/route.ts` | Replace | Portal |
| `src/app/api/auth/logout/route.ts` | Modify | Portal |
| `src/app/api/auth/me/route.ts` | Create | Portal |
| `src/lib/admin-auth.ts` | Replace | Portal |
| `src/middleware.ts` | Create | Portal |
| `src/app/login/page.tsx` | Replace | Portal |
| `frontend/src/components/LoginDialog.svelte` | Modify | Trader |
| `src/app/login/page.tsx` | Modify | Komissionka |
| `src/components/LoginClient.tsx` | Modify | OurDiary |
| `src/lib/shectory-portal-auth.ts` | Create | PingMaster |
| `src/lib/auth.ts` | Modify | PingMaster |
| `src/app/api/auth/login/route.ts` | Modify | PingMaster |
| `src/app/login/page.tsx` | Replace | PingMaster |
| `docs/auth-standard.md` | Create | Portal repo |

---

## Task 0: Prerequisites — Create First Portal User

All work is on `ssh smain` unless otherwise noted.

- [ ] **Step 1: Verify portal_users is empty**

```bash
ssh smain 'cd /home/shectory/workspaces/projects/shectory-portal && node -e "
const {PrismaClient}=require(\"@prisma/client\");
const p=new PrismaClient();
p.portalUser.findMany({select:{email:true,role:true}}).then(r=>{console.log(JSON.stringify(r));p.\$disconnect()});
"'
```

Expected: `[]`

- [ ] **Step 2: Create admin user**

```bash
ssh smain 'cd /home/shectory/workspaces/projects/shectory-portal && node scripts/create-portal-user.mjs bshevelev@mail.ru YOUR_PASSWORD admin "Boris Shevelev"'
```

Replace `YOUR_PASSWORD` with a real password (8+ chars, letters + digits).

Expected: output confirming user created.

- [ ] **Step 3: Verify user exists**

```bash
ssh smain 'cd /home/shectory/workspaces/projects/shectory-portal && node -e "
const {PrismaClient}=require(\"@prisma/client\");
const p=new PrismaClient();
p.portalUser.findMany({select:{email:true,role:true}}).then(r=>{console.log(JSON.stringify(r));p.\$disconnect()});
"'
```

Expected: `[{"email":"bshevelev@mail.ru","role":"admin"}]`

- [ ] **Step 4: Verify AUTH_SESSION_SECRET env var is set in portal .env**

```bash
ssh smain 'grep "AUTH_SESSION_SECRET" /home/shectory/workspaces/projects/shectory-portal/.env'
```

If missing, generate and add:

```bash
ssh smain 'python3 -c "import secrets; print(\"AUTH_SESSION_SECRET=\"+secrets.token_hex(32))" >> /home/shectory/workspaces/projects/shectory-portal/.env'
```

- [ ] **Step 5: Verify SHECTORY_AUTH_BRIDGE_SECRET is set in portal .env**

```bash
ssh smain 'grep "SHECTORY_AUTH_BRIDGE_SECRET" /home/shectory/workspaces/projects/shectory-portal/.env'
```

This is needed for the bridge endpoint used by satellite apps.

---

## Task 1: Distribute shectory-logo.gif to All Apps

GIF source: `smain:/home/shectory/workspaces/projects/CursorRPA/shectory-portal/public/brand/shectory-logo.gif`

- [ ] **Step 1: Copy GIF to Portal**

```bash
ssh smain 'mkdir -p /home/shectory/workspaces/projects/shectory-portal/public/brand && cp /home/shectory/workspaces/projects/CursorRPA/shectory-portal/public/brand/shectory-logo.gif /home/shectory/workspaces/projects/shectory-portal/public/brand/shectory-logo.gif'
```

- [ ] **Step 2: Copy GIF to Komissionka**

```bash
ssh smain 'mkdir -p /home/shectory/workspaces/projects/komissionka/public/brand && cp /home/shectory/workspaces/projects/CursorRPA/shectory-portal/public/brand/shectory-logo.gif /home/shectory/workspaces/projects/komissionka/public/brand/shectory-logo.gif'
```

- [ ] **Step 3: Copy GIF to OurDiary**

```bash
ssh smain 'mkdir -p /home/shectory/workspaces/projects/ourdiary/public/brand && cp /home/shectory/workspaces/projects/CursorRPA/shectory-portal/public/brand/shectory-logo.gif /home/shectory/workspaces/projects/ourdiary/public/brand/shectory-logo.gif'
```

- [ ] **Step 4: Copy GIF to PingMaster**

```bash
ssh smain 'mkdir -p /home/shectory/workspaces/projects/PingMaster/public/brand && cp /home/shectory/workspaces/projects/CursorRPA/shectory-portal/public/brand/shectory-logo.gif /home/shectory/workspaces/projects/PingMaster/public/brand/shectory-logo.gif'
```

- [ ] **Step 5: Fetch GIF to local machine for Trader**

```bash
scp smain:/home/shectory/workspaces/projects/CursorRPA/shectory-portal/public/brand/shectory-logo.gif ~/workspaces/Shectory\ Trade\ \&\ Lab/frontend/public/brand/shectory-logo.gif
```

If `frontend/public/brand/` doesn't exist:
```bash
mkdir -p ~/workspaces/Shectory\ Trade\ \&\ Lab/frontend/public/brand
```

---

## Task 2: Portal — Create portal-auth.ts Library

**File:** `ssh smain /home/shectory/workspaces/projects/shectory-portal/src/lib/portal-auth.ts` (Create)

This module owns: bcrypt credential verification against `portal_users`, HMAC session token creation/verification. No other module imports `portal_users` directly for auth purposes.

- [ ] **Step 1: Write test script**

```bash
ssh smain 'cat > /tmp/test-portal-auth.mjs << '"'"'EOF'"'"'
import { createHmac } from "node:crypto";

const SESSION_TTL = 60 * 60 * 24 * 30;
const SESSION_COOKIE = "shectory_portal_session";

function sign(payload, secret) {
  return createHmac("sha256", secret).update(payload).digest("hex");
}

function makeSessionToken(email, secret) {
  const expires = Math.floor(Date.now() / 1000) + SESSION_TTL;
  const payload = `${email}:${expires}`;
  return `${payload}:${sign(payload, secret)}`;
}

function verifySessionToken(token, secret) {
  const parts = token.split(":");
  if (parts.length < 3) return null;
  const sig = parts.at(-1);
  const expires = parts.at(-2);
  const email = parts.slice(0, -2).join(":");
  const payload = `${email}:${expires}`;
  if (!timingSafeEqual(sig, sign(payload, secret))) return null;
  if (Math.floor(Date.now() / 1000) > parseInt(expires, 10)) return null;
  return email;
}

function timingSafeEqual(a, b) {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

// Tests
const SECRET = "test-secret-32chars-minimum-here";
const EMAIL = "test@example.com";

const token = makeSessionToken(EMAIL, SECRET);
console.assert(typeof token === "string" && token.includes(":"), "token is string with colons");

const verified = verifySessionToken(token, SECRET);
console.assert(verified === EMAIL, `verified email matches: got ${verified}`);

const badToken = token.slice(0, -3) + "xxx";
const badVerified = verifySessionToken(badToken, SECRET);
console.assert(badVerified === null, "tampered token rejected");

const wrongSecret = verifySessionToken(token, "wrong-secret");
console.assert(wrongSecret === null, "wrong secret rejected");

console.log("All portal-auth tests passed");
EOF'
```

- [ ] **Step 2: Run test to confirm logic**

```bash
ssh smain 'node /tmp/test-portal-auth.mjs'
```

Expected: `All portal-auth tests passed`

- [ ] **Step 3: Create portal-auth.ts**

```bash
ssh smain 'cat > /home/shectory/workspaces/projects/shectory-portal/src/lib/portal-auth.ts << '"'"'EOF'"'"'
import { createHmac, timingSafeEqual as cryptoTimingSafeEqual } from "node:crypto";
import bcrypt from "bcryptjs";
import { prisma } from "@/lib/prisma";

const SESSION_TTL = 60 * 60 * 24 * 30; // 30 days
export const SESSION_COOKIE = "shectory_portal_session";

function authSecret(): string | null {
  return process.env.AUTH_SESSION_SECRET?.trim() ?? null;
}

function sign(payload: string, secret: string): string {
  return createHmac("sha256", secret).update(payload).digest("hex");
}

function timingSafeEqualStr(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  return cryptoTimingSafeEqual(Buffer.from(a, "utf8"), Buffer.from(b, "utf8"));
}

export function makeSessionToken(email: string, secret: string): string {
  const expires = Math.floor(Date.now() / 1000) + SESSION_TTL;
  const payload = `${email}:${expires}`;
  return `${payload}:${sign(payload, secret)}`;
}

export function verifySessionToken(token: string, secret: string): string | null {
  const parts = token.split(":");
  if (parts.length < 3) return null;
  const sig = parts.at(-1)!;
  const expires = parts.at(-2)!;
  const email = parts.slice(0, -2).join(":");
  const payload = `${email}:${expires}`;
  if (!timingSafeEqualStr(sig, sign(payload, secret))) return null;
  if (Math.floor(Date.now() / 1000) > parseInt(expires, 10)) return null;
  return email;
}

export function getSessionFromCookie(cookieHeader: string | null): string | null {
  if (!cookieHeader) return null;
  const secret = authSecret();
  if (!secret) return null;
  const match = cookieHeader.match(/shectory_portal_session=([^;]+)/);
  if (!match) return null;
  return verifySessionToken(decodeURIComponent(match[1]), secret);
}

export async function verifyPortalCredentials(
  email: string,
  password: string
): Promise<{ email: string; role: string; fullName: string } | null> {
  const emailNorm = email.trim().toLowerCase();
  const user = await prisma.portalUser.findUnique({ where: { email: emailNorm } });
  if (!user || !user.passwordHash) return null;
  const ok = await bcrypt.compare(password, user.passwordHash);
  if (!ok) return null;
  return {
    email: user.email,
    role: user.role,
    fullName: user.fullName ?? "",
  };
}

export function makeSessionCookieHeader(token: string, secure: boolean): string {
  const attrs = [
    `shectory_portal_session=${encodeURIComponent(token)}`,
    "HttpOnly",
    "SameSite=Lax",
    `Max-Age=${SESSION_TTL}`,
    "Path=/",
    ...(secure ? ["Secure"] : []),
  ];
  return attrs.join("; ");
}
EOF'
```

- [ ] **Step 4: Verify TypeScript syntax**

```bash
ssh smain 'cd /home/shectory/workspaces/projects/shectory-portal && npx tsc --noEmit 2>&1 | head -20'
```

Expected: no errors (or only pre-existing errors unrelated to portal-auth.ts).

- [ ] **Step 5: Commit**

```bash
ssh smain 'cd /home/shectory/workspaces/projects/shectory-portal && git add src/lib/portal-auth.ts public/brand/shectory-logo.gif && git commit -m "feat(auth): add portal-auth lib + brand gif"'
```

---

## Task 3: Portal — Replace api/auth/login Route

**File:** `src/app/api/auth/login/route.ts` (Replace)

- [ ] **Step 1: Replace login route**

```bash
ssh smain 'cat > /home/shectory/workspaces/projects/shectory-portal/src/app/api/auth/login/route.ts << '"'"'EOF'"'"'
import { NextResponse } from "next/server";
import {
  makeSessionToken,
  makeSessionCookieHeader,
  SESSION_COOKIE,
  verifyPortalCredentials,
} from "@/lib/portal-auth";

export async function POST(req: Request) {
  const secret = process.env.AUTH_SESSION_SECRET?.trim();
  if (!secret) {
    return NextResponse.json({ error: "AUTH_SESSION_SECRET not configured" }, { status: 503 });
  }

  let body: { email?: string; password?: string };
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON" }, { status: 400 });
  }

  const email = String(body.email ?? "").trim().toLowerCase();
  const password = String(body.password ?? "");

  if (!email || !password) {
    return NextResponse.json({ error: "email и password обязательны" }, { status: 400 });
  }

  const user = await verifyPortalCredentials(email, password);
  if (!user) {
    return NextResponse.json({ error: "Неверный email или пароль" }, { status: 401 });
  }

  const token = makeSessionToken(user.email, secret);
  const secure = process.env.NODE_ENV === "production";
  const setCookie = makeSessionCookieHeader(token, secure);

  const res = NextResponse.json({ ok: true, email: user.email, role: user.role });
  res.headers.set("Set-Cookie", setCookie);
  return res;
}
EOF'
```

- [ ] **Step 2: Verify TypeScript**

```bash
ssh smain 'cd /home/shectory/workspaces/projects/shectory-portal && npx tsc --noEmit 2>&1 | grep "api/auth/login" | head -10'
```

Expected: no output (no errors in this file).

---

## Task 4: Portal — Add api/auth/me + Update Logout

- [ ] **Step 1: Create me route**

```bash
ssh smain 'mkdir -p /home/shectory/workspaces/projects/shectory-portal/src/app/api/auth/me && cat > /home/shectory/workspaces/projects/shectory-portal/src/app/api/auth/me/route.ts << '"'"'EOF'"'"'
import { NextResponse } from "next/server";
import { cookies } from "next/headers";
import { verifySessionToken, SESSION_COOKIE } from "@/lib/portal-auth";

export async function GET() {
  const secret = process.env.AUTH_SESSION_SECRET?.trim();
  if (!secret) return NextResponse.json({ ok: false }, { status: 401 });
  const jar = await cookies();
  const token = jar.get(SESSION_COOKIE)?.value;
  if (!token) return NextResponse.json({ ok: false }, { status: 401 });
  const email = verifySessionToken(token, secret);
  if (!email) return NextResponse.json({ ok: false }, { status: 401 });
  return NextResponse.json({ ok: true, email });
}
EOF'
```

- [ ] **Step 2: Update logout route to clear new cookie**

```bash
ssh smain 'cat > /home/shectory/workspaces/projects/shectory-portal/src/app/api/auth/logout/route.ts << '"'"'EOF'"'"'
import { NextResponse } from "next/server";

export async function POST() {
  const res = NextResponse.json({ ok: true });
  res.cookies.set("shectory_portal_session", "", {
    httpOnly: true, path: "/", maxAge: 0,
  });
  // Also clear legacy cookie if present
  res.cookies.set("shectory_admin", "", {
    httpOnly: true, path: "/", maxAge: 0,
  });
  return res;
}
EOF'
```

---

## Task 5: Portal — Update admin-auth.ts

All 8 API routes that call `adminAuthOk` will continue to work after this update.

- [ ] **Step 1: Replace admin-auth.ts**

```bash
ssh smain 'cat > /home/shectory/workspaces/projects/shectory-portal/src/lib/admin-auth.ts << '"'"'EOF'"'"'
import { cookies } from "next/headers";
import { verifySessionToken, SESSION_COOKIE } from "@/lib/portal-auth";

export async function adminAuthOk(): Promise<boolean> {
  const secret = process.env.AUTH_SESSION_SECRET?.trim();
  if (!secret) return true; // dev mode: no secret configured
  const jar = await cookies();
  const token = jar.get(SESSION_COOKIE)?.value;
  if (!token) return false;
  return verifySessionToken(token, secret) !== null;
}
EOF'
```

- [ ] **Step 2: Update all API routes that call adminAuthOk**

The function signature changed from sync `adminAuthOk(req)` to async `adminAuthOk()`. Update each of these files:

```
src/app/api/project/tests/route.ts
src/app/api/project/bot/route.ts
src/app/api/project/backlog/route.ts
src/app/api/project/backlog/[id]/route.ts
src/app/api/project/deploy/route.ts
src/app/api/project/deploy/[id]/route.ts
src/app/api/workspace/tree/route.ts
src/app/api/agent/chat/route.ts
```

For each file, find the call pattern and change it:

Old pattern (will appear in some variant):
```typescript
if (!adminAuthOk(req)) return NextResponse.json(...)
```

New pattern:
```typescript
if (!(await adminAuthOk())) return NextResponse.json(...)
```

Run this sed across all affected files:
```bash
ssh smain 'cd /home/shectory/workspaces/projects/shectory-portal && for f in \
  src/app/api/project/tests/route.ts \
  src/app/api/project/bot/route.ts \
  src/app/api/project/backlog/route.ts \
  "src/app/api/project/backlog/[id]/route.ts" \
  src/app/api/project/deploy/route.ts \
  "src/app/api/project/deploy/[id]/route.ts" \
  src/app/api/workspace/tree/route.ts \
  src/app/api/agent/chat/route.ts; do
  sed -i "s/adminAuthOk(req)/await adminAuthOk()/g" "$f"
  sed -i "s/adminAuthOk(request)/await adminAuthOk()/g" "$f"
  sed -i "s/if (!adminAuthOk/if (!(await adminAuthOk/g" "$f"
  echo "updated: $f"
done'
```

- [ ] **Step 3: Verify TypeScript**

```bash
ssh smain 'cd /home/shectory/workspaces/projects/shectory-portal && npx tsc --noEmit 2>&1 | grep -v "node_modules" | head -30'
```

Fix any type errors before continuing. Common issue: async functions that were previously sync now need to be made `async`.

---

## Task 6: Portal — Create middleware.ts

Middleware uses Web Crypto API (Edge-compatible, no `node:crypto`).

- [ ] **Step 1: Create middleware.ts**

```bash
ssh smain 'cat > /home/shectory/workspaces/projects/shectory-portal/src/middleware.ts << '"'"'EOF'"'"'
import { NextResponse, type NextRequest } from "next/server";

const PUBLIC_PREFIXES = ["/login", "/_next/", "/brand/", "/favicon.ico"];
const PUBLIC_API_PREFIXES = ["/api/auth/", "/api/internal/"];

async function hmacSha256Hex(payload: string, secret: string): Promise<string> {
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"]
  );
  const buf = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(payload));
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

async function verifyToken(token: string, secret: string): Promise<boolean> {
  const parts = token.split(":");
  if (parts.length < 3) return false;
  const sig = parts.at(-1)!;
  const expires = parts.at(-2)!;
  const email = parts.slice(0, -2).join(":");
  if (Math.floor(Date.now() / 1000) > parseInt(expires, 10)) return false;
  const payload = `${email}:${expires}`;
  const expected = await hmacSha256Hex(payload, secret);
  // timing-safe compare via length check + XOR
  if (sig.length !== expected.length) return false;
  let diff = 0;
  for (let i = 0; i < sig.length; i++) diff |= sig.charCodeAt(i) ^ expected.charCodeAt(i);
  return diff === 0;
}

export async function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;

  if (
    PUBLIC_PREFIXES.some((p) => pathname.startsWith(p)) ||
    PUBLIC_API_PREFIXES.some((p) => pathname.startsWith(p))
  ) {
    return NextResponse.next();
  }

  const secret = process.env.AUTH_SESSION_SECRET?.trim();
  if (!secret) return NextResponse.next(); // dev: no secret = open access

  const token = request.cookies.get("shectory_portal_session")?.value;
  if (!token || !(await verifyToken(token, secret))) {
    return NextResponse.redirect(new URL("/login", request.url));
  }

  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico|brand/).*)"],
};
EOF'
```

- [ ] **Step 2: Verify TypeScript**

```bash
ssh smain 'cd /home/shectory/workspaces/projects/shectory-portal && npx tsc --noEmit 2>&1 | grep "middleware" | head -10'
```

---

## Task 7: Portal — Replace Login Page UI

- [ ] **Step 1: Replace login/page.tsx**

```bash
ssh smain 'cat > /home/shectory/workspaces/projects/shectory-portal/src/app/login/page.tsx << '"'"'EOF'"'"'
"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(false);

  async function submit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setErr("");
    setLoading(true);
    try {
      const r = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ email: email.trim().toLowerCase(), password }),
      });
      const j = await r.json().catch(() => ({} as { error?: string }));
      if (!r.ok) throw new Error((j as { error?: string }).error ?? "Ошибка входа");
      router.replace("/");
      router.refresh();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="min-h-screen bg-[#0f0f1e] flex items-center justify-center px-4">
      <div className="w-full max-w-sm">
        <div className="flex items-center justify-between mb-6">
          <img
            src="/brand/shectory-logo.gif"
            alt="Shectory"
            className="h-12 w-auto"
          />
          <div className="border border-slate-700 rounded-lg px-3 py-1.5 text-sm font-bold text-white">
            PORTAL
          </div>
        </div>
        <div className="bg-[#14142a] border border-[#2d2d4a] rounded-xl p-6 flex flex-col gap-3">
          <h1 className="text-white font-semibold">Вход</h1>
          <form onSubmit={submit} className="flex flex-col gap-3">
            <input
              type="email"
              className="w-full bg-[#0f0f1e] border border-[#2d2d4a] rounded px-3 py-2 text-[#ccc] text-sm outline-none focus:border-[#4a4a7a]"
              placeholder="Email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              autoComplete="username"
              autoFocus
              required
            />
            <input
              type="password"
              className="w-full bg-[#0f0f1e] border border-[#2d2d4a] rounded px-3 py-2 text-[#ccc] text-sm outline-none focus:border-[#4a4a7a]"
              placeholder="Пароль"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
              required
            />
            {err && <p className="text-[#f66] text-xs">{err}</p>}
            <button
              type="submit"
              disabled={loading || !email.trim() || !password}
              className="w-full bg-[#2a6a2a] text-[#7fff7f] rounded px-3 py-2.5 text-sm font-medium disabled:opacity-50 hover:bg-[#3a8a3a] disabled:cursor-not-allowed transition-colors"
            >
              {loading ? "Вход..." : "Войти"}
            </button>
          </form>
        </div>
      </div>
    </main>
  );
}
EOF'
```

- [ ] **Step 2: Commit all portal changes**

```bash
ssh smain 'cd /home/shectory/workspaces/projects/shectory-portal && git add -A && git commit -m "feat(auth): unified auth — portal-auth lib, middleware, login page, me endpoint"'
```

---

## Task 8: Portal — Build and Deploy

- [ ] **Step 1: Build portal**

```bash
ssh smain 'cd /home/shectory/workspaces/projects/shectory-portal && npm run build 2>&1 | tail -20'
```

Expected: `✓ Compiled successfully` or similar. Fix any build errors before continuing.

- [ ] **Step 2: Restart portal service**

```bash
ssh smain 'sudo systemctl restart shectory-portal && sleep 3 && sudo systemctl status shectory-portal | head -10'
```

Expected: `Active: active (running)`.

- [ ] **Step 3: Smoke test — unauthenticated access redirects to /login**

```bash
curl -s -o /dev/null -w "%{http_code} %{redirect_url}" https://shectory.ru/projects
```

Expected: `302 https://shectory.ru/login` (or `301`).

- [ ] **Step 4: Smoke test — login with credentials**

```bash
curl -s -c /tmp/portal-cookies.txt -X POST https://shectory.ru/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"bshevelev@mail.ru","password":"YOUR_PASSWORD"}'
```

Expected: `{"ok":true,"email":"bshevelev@mail.ru","role":"admin"}`

- [ ] **Step 5: Smoke test — /api/auth/me with session cookie**

```bash
curl -s -b /tmp/portal-cookies.txt https://shectory.ru/api/auth/me
```

Expected: `{"ok":true,"email":"bshevelev@mail.ru"}`

- [ ] **Step 6: Smoke test — bridge endpoint still works**

```bash
BRIDGE_SECRET=$(ssh smain 'grep SHECTORY_AUTH_BRIDGE_SECRET /home/shectory/workspaces/projects/shectory-portal/.env | cut -d= -f2')
curl -s -X POST https://shectory.ru/api/internal/verify-portal-credentials \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $BRIDGE_SECRET" \
  -d '{"email":"bshevelev@mail.ru","password":"YOUR_PASSWORD"}'
```

Expected: `{"ok":true,"email":"bshevelev@mail.ru","role":"admin",...}`

---

## Task 9: Trader — Add Logo to LoginDialog

**Working directory:** `~/workspaces/Shectory Trade & Lab` (local machine)

- [ ] **Step 1: Verify GIF was copied in Task 1**

```bash
ls -la ~/workspaces/Shectory\ Trade\ \&\ Lab/frontend/public/brand/shectory-logo.gif
```

- [ ] **Step 2: Update LoginDialog.svelte**

Edit `frontend/src/components/LoginDialog.svelte`. Replace the `<div class="overlay">` section:

Current content around line 37-40:
```html
<div class="overlay">
  <div class="dialog">
    <div class="title">Shectory Trader</div>
```

Replace with:
```html
<div class="overlay">
  <div class="dialog">
    <div class="header-row">
      <img src="/brand/shectory-logo.gif" alt="Shectory" class="logo" />
      <div class="app-badge">TRADER</div>
    </div>
```

Remove the old `<div class="title">` line entirely (the logo replaces it).

Then update the `<style>` block — add after the `.title` rule (or replace it):
```css
  .header-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 4px;
  }
  .logo { height: 40px; width: auto; }
  .app-badge {
    border: 1px solid #2d2d4a;
    border-radius: 6px;
    padding: 2px 8px;
    font-size: 11px;
    font-weight: 600;
    color: #ccc;
  }
```

- [ ] **Step 3: Remove the now-unused .title CSS rule from the style block**

Delete:
```css
  .title { font-size: 16px; font-weight: 600; color: #ddd; margin-bottom: 4px; }
```

- [ ] **Step 4: Build Trader frontend**

```bash
cd ~/workspaces/Shectory\ Trade\ \&\ Lab/frontend && npm run build 2>&1 | tail -10
```

Expected: build succeeds.

- [ ] **Step 5: Deploy to hoster**

```bash
cd ~/workspaces/Shectory\ Trade\ \&\ Lab && bash deploy/deploy.sh
```

Or if deploy.sh doesn't copy `public/brand/`:
```bash
ssh hoster 'mkdir -p /home/ubuntu/apps/shectory-trader/frontend/dist/brand'
scp frontend/public/brand/shectory-logo.gif hoster:/home/ubuntu/apps/shectory-trader/frontend/dist/brand/
```

- [ ] **Step 6: Commit**

```bash
cd ~/workspaces/Shectory\ Trade\ \&\ Lab && git add frontend/public/brand/shectory-logo.gif frontend/src/components/LoginDialog.svelte && git commit -m "feat(ui): add shectory logo to Trader login screen"
```

---

## Task 10: Komissionka — Add Logo to Login Page

All work on `ssh smain`, directory `/home/shectory/workspaces/projects/komissionka`.

- [ ] **Step 1: Read current login page**

```bash
ssh smain 'cat /home/shectory/workspaces/projects/komissionka/src/app/login/page.tsx'
```

- [ ] **Step 2: Add logo header to login page**

The current login page uses shadcn `Card` with a `CardHeader` containing `<h1>Вход</h1>`. Add the logo row above the Card:

```bash
ssh smain 'cat > /home/shectory/workspaces/projects/komissionka/src/app/login/page.tsx << '"'"'EOF'"'"'
"use client";

import { useState } from "react";
import { signIn } from "next-auth/react";
import { Button } from "komiss/components/ui/button";
import { Input } from "komiss/components/ui/input";
import { Card, CardContent, CardHeader } from "komiss/components/ui/card";
import { useRouter } from "next/navigation";

export default function LoginPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const router = useRouter();

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const result = await signIn("credentials", {
        email,
        password,
        redirect: false,
      });
      if (result?.error) {
        setError(result.error === "CredentialsSignin" ? "Неверный email или пароль" : result.error);
        return;
      }
      if (!result?.ok) {
        setError("Вход не выполнен");
        return;
      }
      window.location.href = "/";
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-[#0f0f1e] px-4">
      <div className="w-full max-w-sm">
        <div className="flex items-center justify-between mb-6">
          <img src="/brand/shectory-logo.gif" alt="Shectory" className="h-12 w-auto" />
          <div className="border border-slate-700 rounded-lg px-3 py-1.5 text-sm font-bold text-white">
            KOMISSIONKA
          </div>
        </div>
        <Card className="bg-[#14142a] border-[#2d2d4a]">
          <CardHeader>
            <h1 className="text-lg font-semibold text-white">Вход</h1>
            <p className="text-xs text-slate-400">
              Единый вход через каталог Shectory Portal
            </p>
          </CardHeader>
          <CardContent>
            <form onSubmit={handleSubmit} className="space-y-4">
              {error && (
                <p className="rounded-md bg-red-900/30 p-3 text-sm text-red-400">{error}</p>
              )}
              <div>
                <label htmlFor="email" className="mb-1 block text-xs font-medium text-slate-300">
                  Email
                </label>
                <Input
                  id="email"
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="you@example.com"
                  className="bg-[#0f0f1e] border-[#2d2d4a] text-[#ccc]"
                  required
                />
              </div>
              <div>
                <label htmlFor="password" className="mb-1 block text-xs font-medium text-slate-300">
                  Пароль
                </label>
                <Input
                  id="password"
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  className="bg-[#0f0f1e] border-[#2d2d4a] text-[#ccc]"
                  required
                />
              </div>
              <Button
                type="submit"
                className="w-full bg-[#2a6a2a] text-[#7fff7f] hover:bg-[#3a8a3a]"
                disabled={loading}
              >
                {loading ? "Вход..." : "Войти"}
              </Button>
            </form>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
EOF'
```

- [ ] **Step 3: Build and deploy Komissionka**

```bash
ssh smain 'cd /home/shectory/workspaces/projects/komissionka && npm run build 2>&1 | tail -15'
```

Then deploy to hoster:
```bash
ssh smain 'rsync -av --exclude node_modules --exclude .next/cache /home/shectory/workspaces/projects/komissionka/.next/ hoster:/home/ubuntu/komissionka/.next/' 2>/dev/null || \
  ssh hoster 'cd /home/ubuntu/komissionka && git pull && npm run build 2>&1 | tail -10'
```

Also copy the brand gif to hoster:
```bash
scp smain:/home/shectory/workspaces/projects/komissionka/public/brand/shectory-logo.gif hoster:/home/ubuntu/komissionka/public/brand/shectory-logo.gif 2>/dev/null || \
  ssh hoster 'mkdir -p /home/ubuntu/komissionka/public/brand && cp /path/to/gif /home/ubuntu/komissionka/public/brand/shectory-logo.gif'
```

Check actual deployment path and restart command:
```bash
ssh hoster 'systemctl --user list-units --type=service | grep komiss || sudo systemctl list-units --type=service | grep komiss'
```

- [ ] **Step 4: Commit on smain**

```bash
ssh smain 'cd /home/shectory/workspaces/projects/komissionka && git add public/brand/shectory-logo.gif src/app/login/page.tsx && git commit -m "feat(auth): unified login screen — shectory logo + dark theme"'
```

---

## Task 11: OurDiary — Add Logo to LoginClient

- [ ] **Step 1: Read current LoginClient**

```bash
ssh smain 'cat /home/shectory/workspaces/projects/ourdiary/src/components/LoginClient.tsx'
```

- [ ] **Step 2: Add logo to LoginClient**

Find the top of the returned JSX — typically a `<div className="min-h-screen ...">`. Add the logo header inside, before the login form container.

Edit `/home/shectory/workspaces/projects/ourdiary/src/components/LoginClient.tsx`:

Locate the outer `return (` block. Find the first `<div` inside it and add a logo/badge header row. The exact insertion depends on current structure (read in Step 1). The pattern to add is:

```tsx
<div className="w-full max-w-sm mx-auto">
  {/* Header row with logo */}
  <div className="flex items-center justify-between mb-6">
    <img src="/brand/shectory-logo.gif" alt="Shectory" className="h-12 w-auto" />
    <div className="border border-slate-700 rounded-lg px-3 py-1.5 text-sm font-bold text-white">
      OURDIARY
    </div>
  </div>
  {/* existing login form below */}
  ...
</div>
```

Use the Edit tool or sed to insert after reading the file. The key requirement: `<img src="/brand/shectory-logo.gif">` must appear in the login UI.

- [ ] **Step 3: Build OurDiary**

```bash
ssh smain 'cd /home/shectory/workspaces/projects/ourdiary && npm run build 2>&1 | tail -15'
```

- [ ] **Step 4: Deploy to hoster**

```bash
ssh hoster 'cd /home/ubuntu/ourdiary && git pull && npm run build 2>&1 | tail -10 && sudo systemctl restart ourdiary 2>/dev/null || systemctl --user restart ourdiary'
```

Also ensure the gif is present:
```bash
ssh hoster 'mkdir -p /home/ubuntu/ourdiary/public/brand'
scp smain:/home/shectory/workspaces/projects/ourdiary/public/brand/shectory-logo.gif hoster:/home/ubuntu/ourdiary/public/brand/
```

- [ ] **Step 5: Commit**

```bash
ssh smain 'cd /home/shectory/workspaces/projects/ourdiary && git add public/brand/shectory-logo.gif src/components/LoginClient.tsx && git commit -m "feat(auth): unified login screen — shectory logo"'
```

---

## Task 12: PingMaster — Connect to Portal Bridge + Add Logo

PingMaster currently has its own bcrypt/JWT auth, NOT connected to portal_users. This task adds the bridge integration.

### 12a: Add Bridge Library

- [ ] **Step 1: Create shectory-portal-auth.ts**

```bash
ssh smain 'cat > /home/shectory/workspaces/projects/PingMaster/src/lib/shectory-portal-auth.ts << '"'"'EOF'"'"'
export type PortalVerifyOk = {
  email: string;
  role: string;
  fullName: string;
};

export async function verifyShectoryPortalCredentials(
  email: string,
  password: string
): Promise<PortalVerifyOk | null> {
  const secret = process.env.SHECTORY_AUTH_BRIDGE_SECRET?.trim();
  const base = (process.env.SHECTORY_PORTAL_URL ?? "https://shectory.ru").replace(/\/$/, "");
  if (!secret) return null;

  const emailNorm = email.trim().toLowerCase();

  try {
    const r = await fetch(`${base}/api/internal/verify-portal-credentials`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${secret}`,
      },
      body: JSON.stringify({ email: emailNorm, password }),
      signal: AbortSignal.timeout(15_000),
    });
    if (!r.ok) return null;
    const j = await r.json() as {
      ok?: boolean; email?: string; role?: string; fullName?: string;
    };
    if (!j.ok || !j.email) return null;
    return {
      email: j.email,
      role: j.role ?? "user",
      fullName: typeof j.fullName === "string" ? j.fullName : "",
    };
  } catch {
    return null;
  }
}
EOF'
```

### 12b: Wire Bridge into auth.ts

- [ ] **Step 2: Read current auth.ts**

```bash
ssh smain 'cat /home/shectory/workspaces/projects/PingMaster/src/lib/auth.ts'
```

- [ ] **Step 3: Find the `authenticate` function and add bridge support**

The current `authenticate` function fetches from local DB and compares bcrypt. Add portal bridge as primary path when env var is set.

Edit `/home/shectory/workspaces/projects/PingMaster/src/lib/auth.ts`. Find the `authenticate` function (searches local DB). Prepend:

```typescript
import { verifyShectoryPortalCredentials } from "@/lib/shectory-portal-auth";

export async function authenticate(email: string, password: string): Promise<SessionPayload | null> {
  const emailNorm = email.trim().toLowerCase();

  // Portal bridge mode: when SHECTORY_AUTH_BRIDGE_SECRET is set, use portal catalog
  if (process.env.SHECTORY_AUTH_BRIDGE_SECRET?.trim()) {
    const portal = await verifyShectoryPortalCredentials(emailNorm, password);
    if (!portal) return null;
    return {
      userId: portal.email,  // use email as userId in portal mode
      email: portal.email,
      role: portal.role,
    };
  }

  // Fallback: local bcrypt auth (dev/offline)
  // ... existing local auth code below ...
```

Preserve the existing local auth logic below the new bridge block.

### 12c: Update Login API Route

- [ ] **Step 4: Read current login route**

```bash
ssh smain 'cat /home/shectory/workspaces/projects/PingMaster/src/app/api/auth/login/route.ts'
```

The current route calls `authenticate(email, password)`. After Step 3, this already handles bridge mode — no changes needed to the route itself unless `email` vs `loginName` field names need adjustment.

Check that the route passes `email` field to `authenticate`. If it uses `loginName`, add email extraction:
```typescript
const email = body.email || body.loginName || "";
```

### 12d: Replace Login Page

- [ ] **Step 5: Replace PingMaster login page**

```bash
ssh smain 'cat > /home/shectory/workspaces/projects/PingMaster/src/app/login/page.tsx << '"'"'EOF'"'"'
"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

export default function LoginPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const router = useRouter();

  async function handleSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      const r = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: email.trim().toLowerCase(), password }),
      });
      const j = await r.json().catch(() => ({} as { error?: string }));
      if (!r.ok) throw new Error((j as { error?: string }).error ?? "Ошибка входа");
      router.replace("/");
      router.refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="min-h-screen bg-[#0f0f1e] flex items-center justify-center px-4">
      <div className="w-full max-w-sm">
        <div className="flex items-center justify-between mb-6">
          <img src="/brand/shectory-logo.gif" alt="Shectory" className="h-12 w-auto" />
          <div className="border border-slate-700 rounded-lg px-3 py-1.5 text-sm font-bold text-white">
            PINGMASTER
          </div>
        </div>
        <div className="bg-[#14142a] border border-[#2d2d4a] rounded-xl p-6 flex flex-col gap-3">
          <h1 className="text-white font-semibold">Вход</h1>
          <form onSubmit={handleSubmit} className="flex flex-col gap-3">
            <input
              type="email"
              className="w-full bg-[#0f0f1e] border border-[#2d2d4a] rounded px-3 py-2 text-[#ccc] text-sm outline-none focus:border-[#4a4a7a]"
              placeholder="Email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              autoComplete="username"
              autoFocus
              required
            />
            <input
              type="password"
              className="w-full bg-[#0f0f1e] border border-[#2d2d4a] rounded px-3 py-2 text-[#ccc] text-sm outline-none focus:border-[#4a4a7a]"
              placeholder="Пароль"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
              required
            />
            {error && <p className="text-[#f66] text-xs">{error}</p>}
            <button
              type="submit"
              disabled={loading || !email.trim() || !password}
              className="w-full bg-[#2a6a2a] text-[#7fff7f] rounded px-3 py-2.5 text-sm font-medium disabled:opacity-50 hover:bg-[#3a8a3a] disabled:cursor-not-allowed"
            >
              {loading ? "Вход..." : "Войти"}
            </button>
          </form>
        </div>
      </div>
    </main>
  );
}
EOF'
```

- [ ] **Step 6: Add SHECTORY_AUTH_BRIDGE_SECRET to PingMaster env on hoster**

```bash
BRIDGE_SECRET=$(ssh smain 'grep SHECTORY_AUTH_BRIDGE_SECRET /home/shectory/workspaces/projects/shectory-portal/.env | cut -d= -f2')
echo "SHECTORY_AUTH_BRIDGE_SECRET=$BRIDGE_SECRET"
# Then add to PingMaster env file on hoster:
ssh hoster "echo 'SHECTORY_AUTH_BRIDGE_SECRET=$BRIDGE_SECRET' >> /home/ubuntu/pingmaster/.env.local"
ssh hoster "echo 'SHECTORY_PORTAL_URL=https://shectory.ru' >> /home/ubuntu/pingmaster/.env.local"
```

- [ ] **Step 7: Build and deploy PingMaster**

```bash
ssh smain 'cd /home/shectory/workspaces/projects/PingMaster && npm run build 2>&1 | tail -15'
```

Deploy to hoster (check actual deploy method):
```bash
ssh hoster 'cd /home/ubuntu/pingmaster && git pull && npm run build 2>&1 | tail -10'
ssh hoster 'sudo systemctl restart pingmaster 2>/dev/null || systemctl --user restart pingmaster'
```

Copy brand gif:
```bash
ssh hoster 'mkdir -p /home/ubuntu/pingmaster/public/brand'
scp smain:/home/shectory/workspaces/projects/PingMaster/public/brand/shectory-logo.gif hoster:/home/ubuntu/pingmaster/public/brand/
```

- [ ] **Step 8: Commit**

```bash
ssh smain 'cd /home/shectory/workspaces/projects/PingMaster && git add public/brand/shectory-logo.gif src/lib/shectory-portal-auth.ts src/lib/auth.ts src/app/login/page.tsx && git commit -m "feat(auth): connect portal bridge + unified login screen"'
```

---

## Task 13: Write Auth Standard Documentation

**File:** `smain:/home/shectory/workspaces/projects/shectory-portal/docs/auth-standard.md`

- [ ] **Step 1: Create auth-standard.md**

```bash
ssh smain 'mkdir -p /home/shectory/workspaces/projects/shectory-portal/docs && cat > /home/shectory/workspaces/projects/shectory-portal/docs/auth-standard.md << '"'"'ENDDOC'"'"'
# Shectory Auth Standard

Version: 1.0.0 | Updated: 2026-05-18

## Identity Source

Single user catalog in PostgreSQL on hoster:

```
portal_users table:
  id           cuid
  email        unique, lowercase
  passwordHash bcrypt (bcryptjs)
  role         "admin" | "user"
  fullName     nullable
```

Create users:
```bash
ssh smain 'cd /home/shectory/workspaces/projects/shectory-portal && node scripts/create-portal-user.mjs EMAIL PASSWORD [role] [fullName]'
```

## Bridge Endpoint (shared by all satellite apps)

```
POST https://shectory.ru/api/internal/verify-portal-credentials
Authorization: Bearer <SHECTORY_AUTH_BRIDGE_SECRET>
Content-Type: application/json
Body: { "email": "user@example.com", "password": "..." }

Response 200: { "ok": true, "email": "...", "role": "...", "fullName": "..." }
Response 401: { "error": "Invalid credentials" }
Response 403: { "error": "Forbidden" }  (wrong or missing Bearer secret)
```

## Env Vars

### Portal (shectory.ru)
| Var | Purpose |
|-----|---------|
| `AUTH_SESSION_SECRET` | Signs portal own session cookies (32+ chars) |
| `SHECTORY_AUTH_BRIDGE_SECRET` | Shared secret for satellite app bridge calls |
| `DATABASE_URL` | PostgreSQL connection string |

### Satellite Apps
| Var | Purpose |
|-----|---------|
| `SHECTORY_AUTH_BRIDGE_SECRET` | Same value as portal |
| `SHECTORY_PORTAL_URL` | `https://shectory.ru` |

## Session Cookies

Each app manages its own session independently.

| App | Cookie | Format |
|-----|--------|--------|
| Portal | `shectory_portal_session` | `email:expires_unix:hmac_sha256_hex` |
| Trader | `shectory_session` | `email:expires_unix:hmac_sha256_hex` |
| Komissionka | `next-auth.session-token` | NextAuth JWT |
| OurDiary | `next-auth.session-token` | NextAuth JWT |
| PingMaster | `pm_session` | JWT (jose, HS256) |

All cookies: httpOnly, SameSite=Lax, Secure in production, 30-day max-age.

## Login Screen Visual Standard

All login screens must:
1. Show `<img src="/brand/shectory-logo.gif" alt="Shectory" style="height:48px">` top-left
2. Show app name badge top-right (`PORTAL`, `TRADER`, `KOMISSIONKA`, `OURDIARY`, `PINGMASTER`)
3. Use dark color scheme: bg `#0f0f1e`, card `#14142a`, border `#2d2d4a`
4. Fields: email (type="email") + password
5. Button: bg `#2a6a2a`, text `#7fff7f`

GIF source for copying: `smain:/home/shectory/workspaces/projects/CursorRPA/shectory-portal/public/brand/shectory-logo.gif`

## How to Add Auth to a New Satellite App

1. Copy `src/lib/shectory-portal-auth.ts` from PingMaster (or Komissionka)
2. Set env vars: `SHECTORY_AUTH_BRIDGE_SECRET`, `SHECTORY_PORTAL_URL=https://shectory.ru`
3. In auth provider: call `verifyShectoryPortalCredentials(email, password)` when env var is set; fall back to local auth when not set
4. Session: use app-native mechanism (NextAuth, custom JWT, custom HMAC)
5. Login page: follow visual standard above
6. Copy `public/brand/shectory-logo.gif`

## Fallback Mode (dev/offline)

All satellite apps must support running without `SHECTORY_AUTH_BRIDGE_SECRET`:
- Bridge functions return `null` when secret not configured
- App falls back to local credential store if available
- Portal itself requires `AUTH_SESSION_SECRET` (returns 503 if missing)

## Files by App

| App | Bridge lib | Auth config | Login page |
|-----|------------|-------------|------------|
| Portal | `src/lib/portal-auth.ts` | `src/middleware.ts` | `src/app/login/page.tsx` |
| Trader | `trader/auth/portal.py` | `trader/auth/guard.py` | `frontend/src/components/LoginDialog.svelte` |
| Komissionka | `src/lib/shectory-portal-auth.ts` | `src/lib/auth.ts` | `src/app/login/page.tsx` |
| OurDiary | `src/lib/shectory-portal-auth.ts` | `src/lib/auth.ts` | `src/components/LoginClient.tsx` |
| PingMaster | `src/lib/shectory-portal-auth.ts` | `src/lib/auth.ts` | `src/app/login/page.tsx` |
ENDDOC'
```

- [ ] **Step 2: Commit**

```bash
ssh smain 'cd /home/shectory/workspaces/projects/shectory-portal && git add docs/auth-standard.md && git commit -m "docs: auth standard for agents and future satellite apps"'
```

---

## Task 14: Final Verification

- [ ] **Step 1: Test portal login flow end-to-end**

Visit `https://shectory.ru` in browser → should redirect to `/login` → enter credentials → land on home page showing projects.

- [ ] **Step 2: Test Trader login**

Visit `https://stl.shectory.ru` → login dialog shows shectory-logo.gif → enter same credentials → trader loads.

- [ ] **Step 3: Verify portal_users bridge still works for Trader**

```bash
BRIDGE=$(ssh smain 'grep SHECTORY_AUTH_BRIDGE_SECRET /home/shectory/workspaces/projects/shectory-portal/.env | cut -d= -f2')
curl -s -X POST https://shectory.ru/api/internal/verify-portal-credentials \
  -H "Authorization: Bearer $BRIDGE" \
  -H "Content-Type: application/json" \
  -d '{"email":"bshevelev@mail.ru","password":"YOUR_PASSWORD"}' | python3 -m json.tool
```

- [ ] **Step 4: Update CLAUDE memory with new auth state**

In the local repo, update `/home/shevbo/.claude/projects/-home-shevbo-workspaces-Shectory-Trade---Lab/memory/pending_handoff.md` — mark unified auth as complete, remove old open questions, note current state.

- [ ] **Step 5: Commit final plan state in local repo**

```bash
cd ~/workspaces/Shectory\ Trade\ \&\ Lab
git add docs/superpowers/plans/2026-05-18-unified-auth.md
git commit -m "docs: unified auth implementation plan"
```

---

## Self-Review Checklist

- [x] Portal: own auth (ADMIN_TOKEN → portal_users) — Tasks 2-8
- [x] Portal: public pages blocked — Task 6 (middleware)
- [x] Portal: login screen with logo — Task 7
- [x] Trader: logo added — Task 9
- [x] Komissionka: logo added — Task 10
- [x] OurDiary: logo added — Task 11
- [x] PingMaster: bridge connected — Task 12
- [x] PingMaster: logo added — Task 12d
- [x] First user creation — Task 0
- [x] Auth standard doc — Task 13
- [x] Bridge endpoint preserved (verify-portal-credentials) — Task 3/4 (not touched, works)
