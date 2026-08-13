---
name: versioned-script-names
description: "STRICT: artifacts the operator loads by hand (Lua script) must carry the version IN THE FILENAME; delivery to the VDS is the agent's job, never the operator's"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 9a04b06b-73aa-4774-b4c8-99559bf8ad84
---

Operator's workflow for the QUIK Lua script is: delete old entry in QUIK -> load new file -> start -> verify version. He demanded twice (2026-07-09/10):

1. The agent on the VDS delivers the script itself (self-update zip -> `<exeDir>\lua\`); the operator ONLY loads/starts it in QUIK. Never write instructions that ask him to copy files.
2. The delivered filename must embed the version: `shectory_trade_v<SCRIPT_VERSION>.lua` (e.g. `shectory_trade_v2026.07.09-cc3.lua`), old versioned copies auto-deleted by the apply-.bat. A bare `shectory_trade.lua` in the load dialog is what he called "амнезия" when it reappeared.

**Why:** Lua runs from memory in QUIK; a same-named file on disk says nothing about what is running. The version in the filename + the `OnInit v...` log line are his two checkpoints.

**How to apply:** implemented in `quik_agent/internal/selfupdate` (LuaScriptName + restart_windows.go bat lines). Any NEW operator-loaded artifact gets the same treatment. See [[robot-on-quik-agent]].
