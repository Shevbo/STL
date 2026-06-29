--[[
  shectory_trade.lua — QUIK QLua order bridge for the Shectory QUIK agent (Phase 2).

  ROLE: pure relay. Connects to the local Go agent over TCP, receives newline-delimited
  JSON commands (place/cancel), translates them into QUIK sendTransaction calls, and
  emits order/trade/trans_reply events back to the agent. It NEVER decides to trade. No
  strategy, no signals, no auto-placement. Every action originates from a command the
  agent sent (which in turn came from an operator-confirmed STL order). Live account.

  Protocol: authoritative spec in quik_agent/PHASE2.md, section "Lua <-> agent TCP protocol".
    agent -> Lua:
      {"cmd":"place","trans_id":N,"client_id":"..","class":"SPBFUT","sec":"RIU6",
       "op":"B|S","price":"..","qty":K,"type":"L","account":".."}
      {"cmd":"cancel","trans_id":N,"order_num":"..","class":"SPBFUT","sec":"RIU6"}
    Lua -> agent:
      {"event":"trans_reply","trans_id":N,"result_code":I,"order_num":"..","text":".."}
      {"event":"order","order_num":"..","trans_id":N,
       "state":"active|filled|cancelled|rejected","balance":B,"qty":Q,"price":"..","text":".."}
      {"event":"trade","order_num":"..","qty":Q,"price":"..","ts":..}

  Transport: LuaSocket (require("socket")). If absent, see the file-queue fallback flag
  below and README.md. The script connects as CLIENT to 127.0.0.1:<port> and reconnects
  on drop. Non-blocking poll loop driven from main().

  Tested against QUIK QLua API names: sendTransaction, message, OnTransReply, OnOrder,
  OnTrade, OnInit, main. See README.md for QUIK-version notes.
]]--

----------------------------------------------------------------------
-- OPERATOR CONFIG  (the only block the operator edits)
----------------------------------------------------------------------
-- Account / firm fields are NOT secrets, but they are deployment-specific. Fill these in
-- for the trading account this terminal logs into. They are used as a FALLBACK only when
-- the agent does not supply ACCOUNT/CLIENT_CODE in the command. Prefer letting the agent
-- (sourced from keymaster + STL) provide them; these defaults keep manual 1a tests simple.
local CONFIG = {
  HOST          = "127.0.0.1",
  PORT          = 50063,          -- must match agent trade_bridge_port

  -- QUIK transaction routing for the trading account. ASK THE OPERATOR.
  -- ACCOUNT     = торговый счёт (futures account / "Торговый счёт" in QUIK).
  -- CLIENT_CODE = код клиента (often empty for FORTS; set if your broker requires it).
  ACCOUNT       = "",             -- e.g. "SPBFUT00XXX" — fill in
  CLIENT_CODE   = "",             -- e.g. "" or your client code — fill in

  -- Transport
  USE_FILE_QUEUE = false,         -- true = bypass TCP, use file-queue fallback (README)
  QUEUE_DIR      = "",            -- dir for in/out queue files when USE_FILE_QUEUE=true

  RECONNECT_MS   = 2000,          -- delay between reconnect attempts
  POLL_SLEEP_MS  = 10,            -- main loop idle sleep
  LOG_TO_QUIK    = true,          -- mirror key events to the QUIK message() window
}

----------------------------------------------------------------------
-- Runtime state
----------------------------------------------------------------------
local running   = true
local sock      = nil            -- LuaSocket tcp object (nil when disconnected)
local rxbuf     = ""             -- partial-read buffer (bytes before a newline)
local connected = false
local last_connect_attempt = 0

-- trans_id (number, our correlation id from the agent) <-> order_num (string, QUIK key).
-- QUIK callbacks give us trans_id on order/trans_reply, so we can correlate without this,
-- but we keep both maps so a `cancel` referencing an order_num and an `order` event line up.
local transId_to_orderNum = {}   -- [trans_id] = order_num
local orderNum_to_transId = {}   -- [order_num] = trans_id

----------------------------------------------------------------------
-- Logging helper
----------------------------------------------------------------------
local function log(msg)
  if CONFIG.LOG_TO_QUIK and message then
    -- message(text, icon): 1 info, 2 warn, 3 error. Use info.
    pcall(message, "[shectory_trade] " .. tostring(msg), 1)
  end
end

----------------------------------------------------------------------
-- Minimal JSON encoder/decoder (no external deps; QLua has no json lib).
-- Scope: flat objects + arrays of scalars. Enough for this protocol. Numbers are
-- emitted as Lua numbers; strings are JSON-escaped. Decoder is a small recursive
-- descent parser tolerant of whitespace. It does NOT support unicode \uXXXX surrogate
-- pairs (not needed: commands are ASCII), but it does decode \uXXXX to a byte when < 256.
----------------------------------------------------------------------
local json = {}

local function json_escape_str(s)
  s = string.gsub(s, '[%z\1-\31\\"]', function(c)
    local map = {
      ['"'] = '\\"', ['\\'] = '\\\\', ['\b'] = '\\b', ['\f'] = '\\f',
      ['\n'] = '\\n', ['\r'] = '\\r', ['\t'] = '\\t',
    }
    return map[c] or string.format('\\u%04x', string.byte(c))
  end)
  return s
end

-- Encode a Lua value. Tables: if they have a special marker _array=true OR are a
-- 1..n sequence, encode as array; else as object. We mainly encode flat objects built
-- with explicit string keys, so order is non-deterministic — acceptable for JSON.
function json.encode(v)
  local t = type(v)
  if t == "nil" then
    return "null"
  elseif t == "number" then
    -- avoid locale decimal comma; QUIK Lua uses '.' but be safe
    if v ~= v then return "0" end            -- NaN guard
    if v == math.huge or v == -math.huge then return "0" end
    -- integers without trailing .0
    if math.floor(v) == v and math.abs(v) < 1e15 then
      return string.format("%d", v)
    end
    return string.format("%.10g", v)
  elseif t == "boolean" then
    return v and "true" or "false"
  elseif t == "string" then
    return '"' .. json_escape_str(v) .. '"'
  elseif t == "table" then
    -- array?
    local n = 0
    local is_array = true
    for k, _ in pairs(v) do
      n = n + 1
      if type(k) ~= "number" then is_array = false end
    end
    if is_array and n > 0 then
      local parts = {}
      for i = 1, #v do parts[i] = json.encode(v[i]) end
      return "[" .. table.concat(parts, ",") .. "]"
    else
      local parts = {}
      for k, val in pairs(v) do
        parts[#parts + 1] = '"' .. json_escape_str(tostring(k)) .. '":' .. json.encode(val)
      end
      return "{" .. table.concat(parts, ",") .. "}"
    end
  end
  return "null"
end

-- Decoder ---------------------------------------------------------------------
local function json_decode_value(s, i)
  -- skip whitespace
  local function skip_ws(p)
    local _, e = string.find(s, "^[ \t\r\n]*", p)
    return (e or (p - 1)) + 1
  end
  i = skip_ws(i)
  local c = string.sub(s, i, i)

  if c == "{" then
    local obj = {}
    i = skip_ws(i + 1)
    if string.sub(s, i, i) == "}" then return obj, i + 1 end
    while true do
      i = skip_ws(i)
      if string.sub(s, i, i) ~= '"' then return nil, i, "expected string key" end
      local key; key, i = json_decode_value(s, i)
      if key == nil then return nil, i, "bad key" end
      i = skip_ws(i)
      if string.sub(s, i, i) ~= ":" then return nil, i, "expected ':'" end
      local val; val, i = json_decode_value(s, i + 1)
      obj[key] = val
      i = skip_ws(i)
      local ch = string.sub(s, i, i)
      if ch == "," then i = i + 1
      elseif ch == "}" then return obj, i + 1
      else return nil, i, "expected ',' or '}'" end
    end

  elseif c == "[" then
    local arr = {}
    i = skip_ws(i + 1)
    if string.sub(s, i, i) == "]" then return arr, i + 1 end
    while true do
      local val; val, i = json_decode_value(s, i)
      arr[#arr + 1] = val
      i = skip_ws(i)
      local ch = string.sub(s, i, i)
      if ch == "," then i = i + 1
      elseif ch == "]" then return arr, i + 1
      else return nil, i, "expected ',' or ']'" end
    end

  elseif c == '"' then
    local res = {}
    local p = i + 1
    while true do
      local ch = string.sub(s, p, p)
      if ch == "" then return nil, p, "unterminated string" end
      if ch == '"' then
        return table.concat(res), p + 1
      elseif ch == "\\" then
        local esc = string.sub(s, p + 1, p + 1)
        if esc == "n" then res[#res + 1] = "\n"
        elseif esc == "t" then res[#res + 1] = "\t"
        elseif esc == "r" then res[#res + 1] = "\r"
        elseif esc == "b" then res[#res + 1] = "\b"
        elseif esc == "f" then res[#res + 1] = "\f"
        elseif esc == "/" then res[#res + 1] = "/"
        elseif esc == "\\" then res[#res + 1] = "\\"
        elseif esc == '"' then res[#res + 1] = '"'
        elseif esc == "u" then
          local hex = string.sub(s, p + 2, p + 5)
          local code = tonumber(hex, 16)
          if code and code < 256 then res[#res + 1] = string.char(code) end
          p = p + 4
        else
          res[#res + 1] = esc
        end
        p = p + 2
      else
        res[#res + 1] = ch
        p = p + 1
      end
    end

  elseif c == "t" then
    if string.sub(s, i, i + 3) == "true" then return true, i + 4 end
    return nil, i, "bad literal"
  elseif c == "f" then
    if string.sub(s, i, i + 4) == "false" then return false, i + 5 end
    return nil, i, "bad literal"
  elseif c == "n" then
    if string.sub(s, i, i + 3) == "null" then return nil, i + 4 end
    return nil, i, "bad literal"
  else
    -- number
    local num_str = string.match(s, "^%-?%d+%.?%d*[eE]?[%+%-]?%d*", i)
    if num_str and num_str ~= "" then
      return tonumber(num_str), i + #num_str
    end
    return nil, i, "unexpected char '" .. c .. "'"
  end
end

function json.decode(s)
  local ok, val, _ = pcall(function()
    local v, _ = json_decode_value(s, 1)
    return v
  end)
  if ok then return val end
  return nil
end

----------------------------------------------------------------------
-- Transport: TCP (LuaSocket) + file-queue fallback share one interface:
--   transport.connect()  -> bool
--   transport.send(line)  -> bool   (line WITHOUT trailing newline; adds it)
--   transport.recv_lines() -> { line, line, ... }   (complete lines, newline-stripped)
--   transport.close()
--   transport.is_open() -> bool
----------------------------------------------------------------------
local socket_lib = nil
do
  local ok, lib = pcall(require, "socket")
  if ok then socket_lib = lib end
end

local transport = {}

-- ----- TCP implementation -----
local tcp_transport = {}
function tcp_transport.is_open() return connected and sock ~= nil end

function tcp_transport.connect()
  if not socket_lib then return false end
  local now = (os.clock() * 1000)
  if (now - last_connect_attempt) < CONFIG.RECONNECT_MS then return false end
  last_connect_attempt = now

  local s = socket_lib.tcp()
  if not s then return false end
  -- blocking connect with short timeout, then switch to non-blocking for I/O
  s:settimeout(1)
  local ok, err = s:connect(CONFIG.HOST, CONFIG.PORT)
  if not ok then
    pcall(function() s:close() end)
    return false
  end
  s:settimeout(0)              -- non-blocking for subsequent send/receive
  sock = s
  connected = true
  rxbuf = ""
  log("connected to agent " .. CONFIG.HOST .. ":" .. CONFIG.PORT)
  return true
end

function tcp_transport.close()
  if sock then pcall(function() sock:close() end) end
  sock = nil
  connected = false
  rxbuf = ""
end

function tcp_transport.send(line)
  if not (connected and sock) then return false end
  local data = line .. "\n"
  local total = #data
  local sent = 0
  while sent < total do
    -- non-blocking send returns index of last byte sent, or nil,err,partial
    local i, err, partial = sock:send(data, sent + 1)
    if i then
      sent = i
    elseif err == "timeout" then
      sent = partial or sent
      -- socket buffer full; brief yield then retry. For our tiny lines this is rare.
      if sleep then sleep(1) end
    else
      log("send error: " .. tostring(err) .. " — dropping link")
      tcp_transport.close()
      return false
    end
  end
  return true
end

function tcp_transport.recv_lines()
  local lines = {}
  if not (connected and sock) then return lines end
  -- drain available bytes; '*a' in non-blocking mode returns partial + 'timeout'
  while true do
    local chunk, err, partial = sock:receive("*a")
    local got = chunk or partial
    if got and #got > 0 then
      rxbuf = rxbuf .. got
    end
    if err == "closed" then
      log("link closed by agent")
      tcp_transport.close()
      break
    end
    -- 'timeout' (no more data right now) or got==nil/empty -> stop draining
    if err == "timeout" or not got or #got == 0 then
      break
    end
  end
  -- split complete lines out of rxbuf
  while true do
    local nl = string.find(rxbuf, "\n", 1, true)
    if not nl then break end
    local line = string.sub(rxbuf, 1, nl - 1)
    rxbuf = string.sub(rxbuf, nl + 1)
    line = string.gsub(line, "\r$", "")     -- tolerate CRLF
    if #line > 0 then lines[#lines + 1] = line end
  end
  return lines
end

-- ----- File-queue fallback implementation -----
-- Same JSON schema. Agent writes commands (one JSON object per line) to <QUEUE_DIR>/cmd.jsonl
-- and we consume + truncate; we append events to <QUEUE_DIR>/evt.jsonl. A lock-free design:
-- we track the byte offset we have consumed from cmd.jsonl. See README "FALLBACK design".
local fq = { cmd_path = nil, evt_path = nil, cmd_offset = 0 }
function fq.is_open() return CONFIG.USE_FILE_QUEUE and fq.cmd_path ~= nil end
function fq.connect()
  if CONFIG.QUEUE_DIR == "" then
    log("file-queue enabled but QUEUE_DIR empty"); return false
  end
  local sep = "\\"
  fq.cmd_path = CONFIG.QUEUE_DIR .. sep .. "cmd.jsonl"
  fq.evt_path = CONFIG.QUEUE_DIR .. sep .. "evt.jsonl"
  -- ensure evt file exists
  local ef = io.open(fq.evt_path, "a"); if ef then ef:close() end
  -- Read only commands appended AFTER we start: seek cmd.jsonl to its current end so a
  -- Lua restart never REPLAYS the previous session's commands (replaying a stale
  -- cmd.jsonl re-placed an old order runaway on a live account). The agent also
  -- truncates the queue on its own start.
  local cf = io.open(fq.cmd_path, "r")
  if cf then cf:seek("end"); fq.cmd_offset = cf:seek(); cf:close() else fq.cmd_offset = 0 end
  log("file-queue ready: " .. fq.cmd_path .. " (offset=" .. fq.cmd_offset .. ")")
  return true
end
function fq.close() fq.cmd_path = nil end
function fq.send(line)
  if not fq.cmd_path then return false end
  local f = io.open(fq.evt_path, "a")
  if not f then return false end
  f:write(line .. "\n"); f:close()
  return true
end
function fq.recv_lines()
  local lines = {}
  if not fq.cmd_path then return lines end
  local f = io.open(fq.cmd_path, "r")
  if not f then return lines end
  -- Truncation detection: if cmd.jsonl is now smaller than our offset, the agent
  -- truncated/rotated it on a fresh session -> re-read from the start, never seek past EOF.
  local size = f:seek("end")
  if size < fq.cmd_offset then fq.cmd_offset = 0 end
  f:seek("set", fq.cmd_offset)
  for line in f:lines() do
    line = string.gsub(line, "\r$", "")
    if #line > 0 then lines[#lines + 1] = line end
  end
  fq.cmd_offset = f:seek()   -- remember where we stopped
  f:close()
  return lines
end

-- bind the active transport
if CONFIG.USE_FILE_QUEUE then
  transport = fq
else
  transport = tcp_transport
end

----------------------------------------------------------------------
-- Outbound event emitters (Lua -> agent)
----------------------------------------------------------------------
local function emit(tbl)
  local line = json.encode(tbl)
  transport.send(line)
end

local function emit_trans_reply(trans_id, result_code, order_num, text)
  emit({
    event = "trans_reply",
    trans_id = trans_id or 0,
    result_code = result_code or 0,
    order_num = order_num or "",
    text = text or "",
  })
end

local function emit_order(order_num, trans_id, state, balance, qty, price, text)
  emit({
    event = "order",
    order_num = order_num or "",
    trans_id = trans_id or 0,
    state = state or "",
    balance = balance or 0,
    qty = qty or 0,
    price = price or "",
    text = text or "",
  })
end

local function emit_trade(order_num, qty, price, ts)
  emit({
    event = "trade",
    order_num = order_num or "",
    qty = qty or 0,
    price = price or "",
    ts = ts or 0,
  })
end

----------------------------------------------------------------------
-- Command handlers (agent -> Lua -> QUIK)
----------------------------------------------------------------------

-- price arrives as a string in the protocol; QUIK sendTransaction wants PRICE as string too.
local function price_to_str(p)
  if type(p) == "number" then
    -- format without locale comma; keep integer prices clean
    if math.floor(p) == p then return string.format("%d", p) end
    return string.format("%.10g", p)
  end
  return tostring(p)
end

local function handle_place(cmd)
  local trans_id = cmd.trans_id
  if type(trans_id) ~= "number" then
    log("place: missing/invalid trans_id; ignoring")
    return
  end
  local account     = (cmd.account and cmd.account ~= "") and cmd.account or CONFIG.ACCOUNT
  local client_code = CONFIG.CLIENT_CODE   -- protocol carries client_id (STL id), not QUIK CLIENT_CODE
  local op          = (cmd.op == "S") and "S" or "B"   -- default Buy if malformed? No — validate:
  if cmd.op ~= "B" and cmd.op ~= "S" then
    log("place trans_id=" .. trans_id .. ": invalid op '" .. tostring(cmd.op) .. "'")
    emit_trans_reply(trans_id, -1, "", "lua: invalid op (expected B or S)")
    return
  end
  if account == "" then
    log("place trans_id=" .. trans_id .. ": ACCOUNT empty (set CONFIG.ACCOUNT or send account)")
    emit_trans_reply(trans_id, -1, "", "lua: ACCOUNT not configured")
    return
  end

  -- Build the QUIK transaction table. All values MUST be strings.
  local trans = {
    ACTION      = "NEW_ORDER",
    TRANS_ID    = tostring(trans_id),
    CLASSCODE   = tostring(cmd.class or ""),
    SECCODE     = tostring(cmd.sec or ""),
    OPERATION   = op,                       -- "B" or "S"
    PRICE       = price_to_str(cmd.price),
    QUANTITY    = tostring(cmd.qty or 0),
    TYPE        = (cmd.type == "M") and "M" or "L",   -- L = limit (default); M = market
    ACCOUNT     = tostring(account),
  }
  if client_code ~= "" then trans.CLIENT_CODE = tostring(client_code) end

  log(string.format("place trans_id=%d %s %s %s @%s x%s acct=%s",
    trans_id, trans.OPERATION, trans.CLASSCODE, trans.SECCODE,
    trans.PRICE, trans.QUANTITY, trans.ACCOUNT))

  -- sendTransaction returns "" on success (queued), or an error string on immediate reject.
  local res = sendTransaction(trans)
  if res ~= nil and res ~= "" then
    log("sendTransaction rejected: " .. tostring(res))
    -- surface as a trans_reply with a non-zero result so the agent marks REJECTED
    emit_trans_reply(trans_id, -1, "", "lua/sendTransaction: " .. tostring(res))
  end
end

local function handle_cancel(cmd)
  local trans_id  = cmd.trans_id
  local order_num = cmd.order_num
  if type(trans_id) ~= "number" then
    log("cancel: missing/invalid trans_id; ignoring"); return
  end
  if not order_num or order_num == "" then
    log("cancel trans_id=" .. trans_id .. ": missing order_num")
    emit_trans_reply(trans_id, -1, "", "lua: cancel missing order_num")
    return
  end
  local account = CONFIG.ACCOUNT
  local trans = {
    ACTION    = "KILL_ORDER",
    TRANS_ID  = tostring(trans_id),
    CLASSCODE = tostring(cmd.class or ""),
    SECCODE   = tostring(cmd.sec or ""),
    ORDER_KEY = tostring(order_num),
  }
  -- ACCOUNT is not strictly required for KILL_ORDER but include if known (some brokers want it).
  if account ~= "" then trans.ACCOUNT = tostring(account) end

  log("cancel trans_id=" .. trans_id .. " order_num=" .. tostring(order_num))
  local res = sendTransaction(trans)
  if res ~= nil and res ~= "" then
    log("KILL_ORDER rejected: " .. tostring(res))
    emit_trans_reply(trans_id, -1, tostring(order_num), "lua/KILL_ORDER: " .. tostring(res))
  end
end

local function dispatch_command(line)
  local cmd = json.decode(line)
  if type(cmd) ~= "table" then
    log("bad command JSON (dropped): " .. tostring(line))
    return
  end
  if cmd.cmd == "place" then
    handle_place(cmd)
  elseif cmd.cmd == "cancel" then
    handle_cancel(cmd)
  elseif cmd.cmd == "ping" then
    emit({ event = "pong", ts = os.time() })
  else
    log("unknown cmd '" .. tostring(cmd.cmd) .. "' (dropped)")
  end
end

----------------------------------------------------------------------
-- QUIK order/flags mapping
--
-- order.flags is a bitmask (QLua getOrder / OnOrder). Common bits (QUIK 7+):
--   bit0 (1)   : order is ACTIVE (still in trading system / book)
--   bit1 (2)   : order is CANCELLED (snyat)
--   bit2 (4)   : order is a SELL (set) / BUY (clear)   [we don't need side here]
--   bit5 (32)  : order is LIMIT (clear = market)
-- An order is FILLED when not active, not cancelled, and balance == 0.
-- A REJECTED order is reported via OnTransReply (result_code != 0/3) before any OnOrder;
-- we also treat balance>0 + cancelled as a (partially-filled-then-)cancelled order.
--
-- Defensive: we read both .flags and .balance. balance = unfilled remainder (contracts).
----------------------------------------------------------------------
local FLAG_ACTIVE    = 1
local FLAG_CANCELLED = 2

local function order_state_from(order)
  local flags   = tonumber(order.flags) or 0
  local balance = tonumber(order.balance) or 0
  local qty     = tonumber(order.qty) or 0

  local is_active    = (math.floor(flags / FLAG_ACTIVE) % 2) == 1
  local is_cancelled = (math.floor(flags / FLAG_CANCELLED) % 2) == 1

  if is_active then
    return "active"        -- resting; may be partially filled (balance < qty)
  elseif is_cancelled then
    return "cancelled"     -- killed; balance is the unfilled remainder at cancel time
  elseif balance == 0 and qty > 0 then
    return "filled"
  else
    -- not active, not cancelled, balance>0: treat as cancelled/withdrawn remainder.
    return "cancelled"
  end
end

----------------------------------------------------------------------
-- QUIK callbacks (registered automatically by QUIK by global name)
----------------------------------------------------------------------

-- OnTransReply: result of a sendTransaction. reply.trans_id matches our TRANS_ID.
function OnTransReply(reply)
  if not reply then return end
  local trans_id   = tonumber(reply.trans_id) or 0
  local result     = tonumber(reply.status) or 0     -- QUIK status code
  -- QUIK result_code field naming varies: prefer .status, fall back to .result_code
  if reply.status == nil and reply.result_code ~= nil then
    result = tonumber(reply.result_code) or 0
  end
  local order_num  = reply.order_num and tostring(math.floor(tonumber(reply.order_num) or 0)) or ""
  if order_num == "0" then order_num = "" end
  local text       = reply.result_msg or reply.description or ""

  if trans_id ~= 0 and order_num ~= "" then
    transId_to_orderNum[trans_id] = order_num
    orderNum_to_transId[order_num] = trans_id
  end

  emit_trans_reply(trans_id, result, order_num, tostring(text))
end

-- OnOrder: order lifecycle. Fires on register, fill progress, cancel.
function OnOrder(order)
  if not order then return end
  local order_num = tostring(math.floor(tonumber(order.order_num) or 0))
  local trans_id  = tonumber(order.trans_id) or (orderNum_to_transId[order_num] or 0)
  if trans_id ~= 0 and order_num ~= "" then
    transId_to_orderNum[trans_id] = order_num
    orderNum_to_transId[order_num] = trans_id
  end

  local balance = tonumber(order.balance) or 0
  local qty     = tonumber(order.qty) or 0
  local price   = order.price
  local state   = order_state_from(order)
  local text    = ""
  if order.reject_reason and order.reject_reason ~= "" then
    text = tostring(order.reject_reason)
    state = "rejected"
  end

  emit_order(order_num, trans_id, state, balance, qty, price_to_str(price), text)
end

-- OnTrade: a fill (our trade). Report contributing qty + price.
function OnTrade(trade)
  if not trade then return end
  local order_num = tostring(math.floor(tonumber(trade.order_num) or 0))
  local qty       = tonumber(trade.qty) or 0
  local price     = trade.price
  -- datetime -> epoch-ish: QUIK gives trade.datetime table; fall back to os.time().
  local ts = 0
  if type(trade.datetime) == "table" then
    local dt = trade.datetime
    local ok, t = pcall(os.time, {
      year = dt.year, month = dt.month, day = dt.day,
      hour = dt.hour or 0, min = dt.min or 0, sec = dt.sec or 0,
    })
    if ok and t then ts = t end
  end
  if ts == 0 then ts = os.time() end

  emit_trade(order_num, qty, price_to_str(price), ts)
end

----------------------------------------------------------------------
-- OnInit: QUIK calls this with the script path when the script starts (if defined).
----------------------------------------------------------------------
function OnInit(path)
  log("OnInit; LuaSocket=" .. (socket_lib and "yes" or "NO") ..
      " transport=" .. (CONFIG.USE_FILE_QUEUE and "file-queue" or "tcp"))
end

-- OnStop: QUIK calls this when the user stops the script. Clean shutdown.
function OnStop(signal)
  running = false
  transport.close()
  log("OnStop")
  return 1
end

----------------------------------------------------------------------
-- main(): QUIK runs this in its own coroutine. We poll the transport here.
-- Callbacks (OnOrder/OnTrade/OnTransReply) fire on QUIK's thread and call emit(),
-- which writes to the same socket. LuaSocket sends are short and synchronous; QUIK
-- serialises callback execution, so no extra locking is needed for our tiny writes.
----------------------------------------------------------------------
function main()
  if not socket_lib and not CONFIG.USE_FILE_QUEUE then
    log("LuaSocket NOT available and file-queue disabled. " ..
        "Set CONFIG.USE_FILE_QUEUE=true (see README) or install LuaSocket. Idling.")
  end

  while running do
    if not transport.is_open() then
      transport.connect()      -- rate-limited internally for TCP
    end

    if transport.is_open() then
      local lines = transport.recv_lines()
      for _, line in ipairs(lines) do
        dispatch_command(line)
      end
    end

    -- idle sleep; QUIK provides sleep(ms). Guard in case it is absent.
    if sleep then
      sleep(CONFIG.POLL_SLEEP_MS)
    end
  end
end
