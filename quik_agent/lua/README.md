# shectory_trade.lua — QUIK QLua order bridge (Phase 2, sub-agent L)

This QLua script is the **last hop** between the Go QUIK agent and QUIK's transaction
engine. It is a **pure relay**: it receives order commands from the agent over a local
TCP socket, turns them into `sendTransaction` calls, and streams the resulting order /
trade / transaction-reply events back to the agent.

It contains **no strategy and no auto-placement**. Every action it performs comes from a
command the agent sent — and the agent only sends commands for orders an operator has
explicitly confirmed in the STL UI. Guard 3 (human-only) holds: this file cannot place or
cancel anything on its own.

> LIVE ACCOUNT. The script merely relays. All hard limits (max qty, price collar,
> instrument whitelist, daily cap, kill-switch, master `quik_trading_enabled` flag) and
> the per-order operator confirmation live **upstream** — in the Go agent and in STL. Do
> not add limits here; do not bypass them by talking to this script directly.

Protocol source of truth: `quik_agent/PHASE2.md`, section *"Lua <-> agent TCP protocol"*.
gRPC enums it maps onto: `proto/shectory/quik/v1/quik_agent.proto`.

---

## 1. What it does

- On `OnInit` / `main()`: connect via **LuaSocket** (`require("socket")`, `socket.tcp()`)
  to `127.0.0.1:<port>` (default **50063**), non-blocking, with **reconnect-on-drop** and a
  small poll loop.
- Reads **newline-delimited JSON** commands from the agent:
  - `place` -> `sendTransaction{ACTION=NEW_ORDER, CLASSCODE, SECCODE, OPERATION=B/S,
    PRICE, QUANTITY, TYPE=L, ACCOUNT, CLIENT_CODE, TRANS_ID}`
  - `cancel` -> `sendTransaction{ACTION=KILL_ORDER, ORDER_KEY=<order_num>, TRANS_ID}`
  - The `TRANS_ID` is the **agent-assigned `trans_id`** from the command (correlation id).
- Registers `OnTransReply`, `OnOrder`, `OnTrade` and emits matching JSON events back:
  `trans_reply`, `order`, `trade`. Maps QUIK order flags/state to
  `active | filled | cancelled | rejected` and reports `balance` (the unfilled remainder).
- Robust to partial TCP reads: buffers bytes until a `\n` before parsing.

### JSON schema (verbatim from PHASE2.md)

agent -> Lua:
```json
{"cmd":"place","trans_id":123,"client_id":"..","class":"SPBFUT","sec":"RIU6","op":"B","price":"105230","qty":1,"type":"L","account":".."}
{"cmd":"cancel","trans_id":124,"order_num":"987654321","class":"SPBFUT","sec":"RIU6"}
```

Lua -> agent:
```json
{"event":"trans_reply","trans_id":123,"result_code":3,"order_num":"987654321","text":".."}
{"event":"order","order_num":"987654321","trans_id":123,"state":"active","balance":1,"qty":1,"price":"105230","text":".."}
{"event":"trade","order_num":"987654321","qty":1,"price":"105230","ts":1719600000}
```
(`balance` = unfilled remainder. `state` ∈ `active|filled|cancelled|rejected`.)

---

## 2. Install in QUIK

1. Copy `shectory_trade.lua` to a folder on the QUIK machine, e.g.
   `C:\QUIK\lua\shectory\shectory_trade.lua` (any path is fine).
2. Open the **operator config block** at the top of the file and fill in `ACCOUNT`
   (and `CLIENT_CODE` if your broker requires it). See section 4.
3. In QUIK: **Сервисы -> Lua-скрипты...** (Services -> Lua scripts).
4. Click **Добавить** (Add), select `shectory_trade.lua`.
5. Select the row, click **Запустить** (Run). Status should switch to *Выполняется*.
6. Verify in the QUIK message window: you should see
   `[shectory_trade] OnInit; LuaSocket=yes transport=tcp`. When the Go agent's trade
   bridge is up you'll then see `connected to agent 127.0.0.1:50063`.
7. To stop: select the row, **Остановить** (Stop). The script handles `OnStop` cleanly.

The script keeps running and reconnecting; you can start the QUIK script before or after
the Go agent — whichever comes up first will wait for the other.

---

## 3. LuaSocket requirement

The script needs **LuaSocket** (the `socket` module) for TCP. QUIK ships Lua but does
**not** always bundle LuaSocket.

**Check if your QUIK has it:** run the script and read the QUIK message window:
- `LuaSocket=yes` -> TCP transport works, you're done.
- `LuaSocket=NO`  -> LuaSocket is missing. Either install it, or use the **file-queue
  fallback** (section 5).

**Installing LuaSocket** (if missing): drop `socket` / `mime` Lua modules and the matching
`socket/core.dll` (and `mime/core.dll`) compiled for QUIK's Lua version (Lua 5.3 / 5.4 in
recent QUIK builds; older builds use 5.1) into QUIK's Lua `package.cpath`. Use binaries
that match QUIK's Lua **version** and **architecture** (32-bit vs 64-bit QUIK) or
`require("socket")` will fail to load the DLL. If you can't match the build, prefer the
file-queue fallback — it needs no native module.

> **Version assumption (unverified here):** I cannot run QUIK/Lua in this environment.
> The script assumes a QUIK build whose QLua exposes `sendTransaction`, `message`, `sleep`,
> and the `OnTransReply` / `OnOrder` / `OnTrade` / `OnInit` / `OnStop` / `main` callbacks —
> i.e. QUIK 7.x+ "QLua". Field names from QUIK callbacks (`order_num`, `trans_id`,
> `balance`, `qty`, `price`, `flags`, `status`/`result_code`, `result_msg`) and the order
> **flags bitmask** (bit0=active, bit1=cancelled) follow the standard QLua table layout but
> can differ slightly between broker QUIK builds. **Validate on a paper/1-contract order in
> stage 1a before relying on the state mapping.** See section 6.

---

## 4. ACCOUNT / CLIENT_CODE the operator must set

These are **deployment-specific routing fields**, not secrets, but they must match the
account this terminal trades:

| Field          | QUIK meaning                         | Notes                                            |
|----------------|--------------------------------------|--------------------------------------------------|
| `ACCOUNT`      | Торговый счёт (futures trade account) | e.g. `SPBFUT00XXX`. **Required.**                |
| `CLIENT_CODE`  | Код клиента                          | Often empty for FORTS; set only if broker needs. |

Two ways to supply them, in priority order:
1. **From the agent** — the `place` command may carry `"account":".."`. The agent sources
   this from keymaster/STL config. This is preferred: nothing lives in the script.
2. **From the config block** — `CONFIG.ACCOUNT` / `CONFIG.CLIENT_CODE` are used as a
   fallback when the command does not provide them. Handy for manual 1a smoke tests.

If `ACCOUNT` is empty in **both** places, the script rejects the `place` locally and emits
a `trans_reply` with `result_code = -1, text = "lua: ACCOUNT not configured"` (no
transaction is sent to QUIK). **Do not hardcode secret tokens here** — only the account /
client routing codes, which the operator fills in. The gRPC Bearer token and any secret
values stay in the Go agent (keymaster), never in this file.

---

## 5. File-queue FALLBACK (no LuaSocket)

If LuaSocket cannot be installed, set in the config block:
```lua
USE_FILE_QUEUE = true,
QUEUE_DIR      = "C:\\QUIK\\lua\\shectory\\queue",   -- a writable dir both sides agree on
```
Then the **same JSON schema** flows through two append-only files in `QUEUE_DIR`:

| File          | Writer | Reader | Content                                              |
|---------------|--------|--------|------------------------------------------------------|
| `cmd.jsonl`   | agent  | Lua    | one command JSON object per line (`place` / `cancel`) |
| `evt.jsonl`   | Lua    | agent  | one event JSON object per line (`trans_reply`/`order`/`trade`) |

Design / contract for the Go agent side (sub-agent A):
- **Newline-delimited JSON, append-only.** Never rewrite earlier bytes.
- The Lua side tracks a **byte offset** into `cmd.jsonl` and only reads bytes past it each
  poll, so already-consumed commands are not re-run. It never truncates `cmd.jsonl`.
- The agent likewise tracks its read offset into `evt.jsonl`.
- Because both files are append-only and each side advances its own offset, no locking is
  needed for the common case. The agent should `flush`/`fsync` after each appended line so
  Lua sees complete lines (the Lua reader ignores a trailing partial line — it splits on
  `\n` and only consumes up to the last newline).
- Optional hygiene: on a fresh session both sides may rotate/clear the files **before**
  the script starts trading; do not clear mid-session or you'll desync offsets.

The file-queue is a degraded mode: latency = poll interval (`POLL_SLEEP_MS`, default 10 ms,
plus filesystem flush). Fine for stage 1a manual place/cancel; the sub-second 1b maker loop
should use TCP.

---

## 6. Self-review / correctness risks (could not run QUIK here)

Verified by inspection against the QLua API and PHASE2.md:
- Callback names: `OnTransReply`, `OnOrder`, `OnTrade`, `OnInit`, `OnStop`, `main` — correct
  global names QUIK auto-binds.
- API calls: `sendTransaction(table)`, `message(text, icon)`, `sleep(ms)` — standard QLua.
- Transaction field names for NEW_ORDER: `ACTION, TRANS_ID, CLASSCODE, SECCODE, OPERATION,
  PRICE, QUANTITY, TYPE, ACCOUNT, CLIENT_CODE` — all **string** values (QUIK requires
  strings). KILL_ORDER uses `ACTION, TRANS_ID, CLASSCODE, SECCODE, ORDER_KEY`.
- `OPERATION` is `"B"`/`"S"`; `TYPE` is `"L"` (limit). Invalid `op` is rejected locally.
- JSON encode/decode is hand-rolled (QLua has no json lib) and partial-read safe.

Risks I could not verify without a live QUIK:
1. **Order flags bitmask.** I map bit0=active, bit1=cancelled and derive `filled` from
   `balance==0`. Standard for QUIK 7+, but broker builds vary. Confirm in 1a by watching a
   real order through register -> partial -> fill -> cancel and checking the emitted `state`.
2. **OnTransReply field naming.** I read `status` first, then `result_code`; `result_msg`
   then `description` for text. Some builds expose only one. Confirm the `result_code` you
   see on a successful queue (typically QUIK status `3` = "order registered").
3. **`order_num` numeric size.** QUIK order numbers are large integers; I stringify via
   `math.floor(tonumber(...))`. On Lua 5.1 (very old QUIK) `tonumber` is a double and could
   lose precision on >2^53 order numbers. Recent QUIK uses Lua 5.3/5.4 (64-bit int) — fine.
   If you run ancient QUIK, prefer reading `order.order_num` as a string field if available.
4. **LuaSocket presence** — see section 3; falls back to file-queue.
5. **Threading.** Callbacks and `main()` run on QUIK's serialized script thread, so the
   shared socket write from `emit()` is safe without explicit locks. If a future QUIK build
   parallelizes callbacks, add a queue.

None of these affect Guard 3: the script still only relays agent commands.
