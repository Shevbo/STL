---
name: reference-federation-access
description: "How this agent (Klod-STL) reaches the federation — keymaster, inbox, agent ID — via ssh smain"
metadata: 
  node_type: memory
  type: reference
  originSessionId: 54d0cf23-21ef-4926-81c5-db49d8bc51ce
---

I (this Claude on the trader Windows box) have federation access via **`ssh smain`** (user `shectory`,
Linux). The federation infra lives on smain, NOT on the Windows trader box.

**My agent ID:** `klod-stl` (identity "Klod", project Shectory Trade & Lab).

**Keymaster (secrets registry)** — on smain, HTTP port 9093 + CLI:
- Metadata only (never values): `ssh smain 'python3 ~/keymaster/keymaster.py --requester klod-stl query <NAME>'`
  or `... list`. Every query notifies Boris in Telegram + audits to ~/.keymaster/audit.log.
- HTTP from smain-local: `http://127.0.0.1:9093`; over WireGuard from other hosts: `http://10.66.0.1:9093`.
- `store NAME VALUE` accepts secrets ONLY from Boris/CLI — I do NOT self-store. To create a secret I
  send a task to the secrets-Claude (see inbox) who mints + stores it; Boris is notified/approves.
- Reading a value for code (never print it): `VAL=$(cat <location-from-keymaster>)` then use `$VAL`.
- request-value flow (value via Boris TG approval):
  `POST http://127.0.0.1:9093/keymaster/request-value?name=<N>&requester=klod-stl&purpose=<why>` → {request_id};
  Boris approves in TG → `GET .../keymaster/deliver?request_id=<id>` (self-deletes).
- NOTE: keymaster server here does NOT implement /inbox/notify (404); query already pings Boris.

**Inbox (federation message channel)** — dirs on smain under `~/workspaces/`:
- My inbox: `~/workspaces/inbox/klod-stl/`.
- Secrets-Claude / general Claude tasks: `~/workspaces/claude-inbox/` (answers come back as ANS_*.md).
- Send a task: `ssh smain "cat > ~/workspaces/claude-inbox/TASK_$(date +%s)_<TOPIC>.md" << 'EOF' ... EOF`.
- **My inbox-read endpoint (installed):** `ssh smain 'bash ~/workspaces/inbox/klod-stl/read_inbox.sh'`
  (lists+prints messages addressed to klod-stl / *_STL*.md / ANS_*OPT_AGENT_TOKEN*; `--ack` archives).
  Run it at session start / when expecting a reply.

**GOTCHA (Git-Bash on Windows):** a local `cat > path << EOF` heredoc runs on the WINDOWS side and
mangles ~ paths. To write a file ON smain, pipe via stdin: write /tmp/x locally, then
`ssh smain 'cat > ~/dest && chmod +x ~/dest' < /tmp/x`.

**Pending:** task TASK_1780503489_OPT_AGENT_TOKEN.md sent to claude-inbox asking to mint+store
`OPT_AGENT_TOKEN` and provision its value to VDS env (/home/ubuntu/.shectory_trade.env) + Windows
agent env. Waiting for ANS_* with the file path (value never shown). See [[reference-vds-load]],
[[feedback-secrets-protocol]].
