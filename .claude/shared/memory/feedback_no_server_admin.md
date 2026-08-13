---
name: feedback_no_server_admin
description: "STRICT: do NOT administer the hoster/servers (swap, disk cleanup, killing procs, stopping other services) — diagnose and REPORT, the operator decides and acts"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 7525bce9-2c09-4975-8992-6c434c6ffd84
  modified: 2026-07-21T10:33:26.029Z
---

Server/infra administration on the hoster is NOT my function. When I hit an
OS-level / resource problem (out of memory, swap full, disk full, another
service misbehaving, crash-loops in the operator's OTHER apps), I must STOP
after diagnosis and REPORT it concisely. The operator decides and acts.

Do NOT, on my own initiative:
- add/modify swap, delete files to free disk, `pm2 stop/kill` the operator's
  other apps, kill processes, or stop/restart services that aren't the exact
  thing I was asked about.

**Why:** on 2026-07-21, chasing a broken STL login, I stopped shectory-optimizer,
stopped shectory-trader's crash loop, then started trying to add swap and free
disk on a box that runs ~10 of the operator's OTHER projects (komissionka,
ourdiary, garden-manager, eschool, bots). The operator cut me off: "ЭТО НЕ ТВОЯ
ФУНКЦИЯ ЧИНИТЬ СЕРВЕРА — ПРОСТО СООБЩИ МНЕ Я РЕШУ." Box-wide resource decisions
affect his other projects; they are his to make.

**How to apply:** diagnose the root cause, report it in a few lines (what's
broken, why, what I touched), and stop. Deploy/git/STL-app tasks I still do
myself (see [[feedback_commit_full_cycle]]); OS/box administration I only
report. Distinguish "my STL service" from "the operator's server". See also
[[reference_vds_load]] [[reference_hoster_oom_kills_stl]].
