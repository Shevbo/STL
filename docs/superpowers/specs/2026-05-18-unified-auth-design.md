# Unified Auth Design — Shectory Platform

**Date:** 2026-05-18  
**Status:** Approved  
**Scope:** Portal + Trader + Komissionka + OurDiary + PingMaster

---

## Problem

- shectory.ru (Portal) is fully public — any visitor sees all content without login
- Portal uses `ADMIN_TOKEN` (single static token), not `portal_users` email/password
- Each app has isolated auth: different session formats, different secrets, no shared identity
- Lost visual standard: login screen must show shectory-logo.gif + app badge
- PingMaster uses own bcrypt/JWT, not connected to portal user catalog

## Design: Distributed Identical Auth (Approach B)

Each app owns its own session. All apps delegate credential verification to the portal's bridge endpoint. The portal is the single source of truth for user identities.

```
portal_users (PostgreSQL on hoster)
  email, passwordHash(bcrypt), role, fullName
  Management: node scripts/create-portal-user.mjs <email> <pass> [role] [fullName]

Portal (shectory.ru) — identity source of truth
  POST /api/auth/login              — bcrypt verify → HMAC session cookie
  POST /api/auth/logout             — clear cookie
  GET  /api/auth/me                 — return current user
  POST /api/internal/verify-portal-credentials  — bridge for satellite apps
  middleware.ts                     — guard all routes (see allowlist below)

Satellite apps — each manages its own session after bridge verification:
  Trader (stl.shectory.ru)         — HMAC cookie (shectory_session)
  Komissionka                       — NextAuth JWT
  OurDiary                          — NextAuth JWT
  PingMaster                        — JWT (jose)
```

---

## Portal Auth Details

### Session token (portal own sessions)

```
Format:  email:expires_unix:sha256_hmac_hex
Secret:  AUTH_SESSION_SECRET env var
Cookie:  shectory_portal_session, httpOnly, SameSite=Lax, Secure(prod), 30d
```

### Middleware allowlist (no auth required)

```
/login
/api/auth/*
/api/internal/verify-portal-credentials
/_next/*
/brand/*        (static assets)
/favicon.ico
```

### Bridge endpoint (unchanged)

```
POST /api/internal/verify-portal-credentials
Authorization: Bearer <SHECTORY_AUTH_BRIDGE_SECRET>
Body: { email, password }
Response: { ok, email, role, fullName }
```

---

## Visual Standard — Login Screen

All login screens must follow this layout:

```
┌──────────────────────────────────────────┐
│ [shectory-logo.gif]        [APPNAME]     │  ← header row
│                                          │
│         [email input]                    │
│         [password input]                 │
│         [error message if any]           │
│         [Войти  button]                  │
│                                          │
└──────────────────────────────────────────┘

Background: #0f0f1e (dark navy)
Card/panel: #14142a with border #2d2d4a
Text: #ccc / #ddd
Button: #2a6a2a / #7fff7f (green accent)
Logo: /brand/shectory-logo.gif, height 48px
App badge: top-right, slate border, text "TRADER" / "KOMISSIONKA" etc.
```

Logo source: `CursorRPA/shectory-portal/public/brand/shectory-logo.gif` on smain.  
Must be copied to `public/brand/shectory-logo.gif` in each app.

---

## Env Vars Standard

### Portal (shectory.ru)

```env
AUTH_SESSION_SECRET=<random 32+ chars>
SHECTORY_AUTH_BRIDGE_SECRET=<shared secret, same as satellite apps>
DATABASE_URL=postgresql://...
```

### Satellite apps

```env
SHECTORY_AUTH_BRIDGE_SECRET=<same shared secret>
SHECTORY_PORTAL_URL=https://shectory.ru
```

### Satellite-specific

```env
# Komissionka / OurDiary (NextAuth):
NEXTAUTH_SECRET=<app-specific>
NEXTAUTH_URL=https://<app-domain>

# PingMaster (JWT jose):
JWT_SECRET=<app-specific>
```

---

## Bridge Integration Pattern (for satellite Next.js apps)

Copy `shectory-portal-auth.ts` from komissionka to any new satellite app:

```typescript
// src/lib/shectory-portal-auth.ts
export async function verifyShectoryPortalCredentials(
  email: string, password: string
): Promise<{ email: string; role: string; fullName: string } | null>
```

Required env: `SHECTORY_AUTH_BRIDGE_SECRET`, `SHECTORY_PORTAL_URL`

When `SHECTORY_AUTH_BRIDGE_SECRET` is set → bridge mode (portal catalog is authoritative).  
When not set → app falls back to its own local auth (dev/offline mode).

---

## Scope of Changes — Tier 1 (this iteration)

| App | File | Change |
|-----|------|--------|
| Portal | `src/lib/portal-auth.ts` | New: bcrypt verify, HMAC session |
| Portal | `src/app/api/auth/login/route.ts` | Replace ADMIN_TOKEN with email/password |
| Portal | `src/app/api/auth/me/route.ts` | New endpoint |
| Portal | `src/app/api/auth/logout/route.ts` | Update cookie name |
| Portal | `src/lib/admin-auth.ts` | Update to use new session |
| Portal | `src/middleware.ts` | New: guard all pages |
| Portal | `src/app/login/page.tsx` | New UI: logo + email/password |
| Portal | `public/brand/shectory-logo.gif` | Copy from CursorRPA |
| Trader | `frontend/public/brand/shectory-logo.gif` | Copy from CursorRPA |
| Trader | `frontend/src/components/LoginDialog.svelte` | Add logo |
| Komissionka | `src/app/login/page.tsx` | Add logo + unified styling |
| Komissionka | `public/brand/shectory-logo.gif` | Copy from CursorRPA |
| OurDiary | `src/components/LoginClient.tsx` | Add logo + unified styling |
| OurDiary | `public/brand/shectory-logo.gif` | Copy from CursorRPA |
| PingMaster | `src/lib/shectory-portal-auth.ts` | Add bridge lib |
| PingMaster | `src/lib/auth.ts` | Wire bridge when env set |
| PingMaster | `src/app/api/auth/login/route.ts` | Support bridge flow |
| PingMaster | `src/app/login/page.tsx` | Add logo + unified styling |
| PingMaster | `public/brand/shectory-logo.gif` | Copy from CursorRPA |

## Out of Scope

- register / forgot-password flows (YAGNI — admin-only user catalog)
- nginx config / HTTPS certbot
- CursorRPA, PiranhaAI, Shectory Assist, OpenClaw, Syslog (not web-login apps)
- shectory-dashboard (static HTML, no auth)
- Lineman agent (internal tool, no web login)

---

## How to Add Auth to a New Satellite App (agent reference)

1. Copy `src/lib/shectory-portal-auth.ts` from komissionka
2. Set env vars: `SHECTORY_AUTH_BRIDGE_SECRET`, `SHECTORY_PORTAL_URL=https://shectory.ru`
3. In auth provider, call `verifyShectoryPortalCredentials(email, password)` when bridge secret is set
4. Session: use app's own mechanism (NextAuth JWT, HMAC cookie, etc.)
5. Login page: use unified visual standard (logo + email/password, dark theme)
6. Copy `public/brand/shectory-logo.gif` from `smain:/home/shectory/workspaces/projects/CursorRPA/shectory-portal/public/brand/shectory-logo.gif`

---

## Session Cookie Names (for reference)

| App | Cookie |
|-----|--------|
| Portal | `shectory_portal_session` |
| Trader | `shectory_session` |
| Komissionka | NextAuth default (`next-auth.session-token`) |
| OurDiary | NextAuth default |
| PingMaster | `pm_session` |
