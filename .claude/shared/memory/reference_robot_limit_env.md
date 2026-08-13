---
name: reference-robot-limit-env
description: Live robot concurrency cap LAB_MAX_ROBOTS — code default and the systemd manager-env gotcha
metadata: 
  node_type: memory
  type: reference
  originSessionId: a3701a33-3d27-427a-aa8b-04e822e31829
---

The live scheduler runs at most `_MAX_ACTIVE_ROBOTS = int(os.environ.get("LAB_MAX_ROBOTS","50"))` robots concurrently (trader/lab/scheduler.py). Extra deployed robots stay `deployed=true` in DB but never tick (log `lab.scheduler.max_robots_reached`); the UI still shows them LIVE.

Code default raised 12 -> 50 on 2026-06-18.

GOTCHA: the effective cap can be overridden by a systemd MANAGER environment var, not just the env file or the unit. It was set to 30 via `systemctl set-environment LAB_MAX_ROBOTS=30` (shows in `systemctl show-environment`, inherited by the service, visible in `/proc/<MainPID>/environ`) — NOT in `~/.shectory_trade.env` and NOT in the unit. That silently overrode the code default. Removed with `sudo systemctl unset-environment LAB_MAX_ROBOTS` + restart, so the committed code default (50) is now the single source of truth. NOTE: `~/.shectory_trade.env` is read by pydantic Settings (config.py), but the scheduler reads `os.environ` directly, so a var there would NOT reach it unless systemd injects it.

To change the cap: edit the code default and deploy, OR `sudo systemctl set-environment LAB_MAX_ROBOTS=<n>` + restart (runtime only, lost on reboot). Related: [[reference-forts-contract-roll]], [[reference-vds-load]].
