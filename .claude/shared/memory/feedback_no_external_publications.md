---
name: no-external-publications
description: "STRICT: never publish artifacts/content to any network resource except the operator's own (STL, hoster) or ones he explicitly approved"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 9a04b06b-73aa-4774-b4c8-99559bf8ad84
---

Operator's order (2026-07-10, verbatim intent): «никаких публикаций артефактов на
ресурсы сети, кроме моих, либо согласованных мной».

**Why:** trading data (sweep results, robot P&L, configs) is sensitive; his platform
(STL) already implements results viewing "в полном объёме" — reports belong THERE,
not on claude.ai artifacts or any external host.

**How to apply:** deliverables = STL UI links (Botstore/BacktestLab), files in the
repo, or plain chat text/tables. The Artifact tool is OFF for this project unless he
explicitly asks or approves per-case. Applies to any external upload (gist, pastebin,
etc.). See [[robot-on-quik-agent]].
