---
name: reference_hoster_oom_kills_stl
description: "hoster earlyoom SIGTERM-kills STL (uvicorn) under memory pressure; clean shutdown, Restart=on-failure leaves it dead; live robot unaffected"
metadata: 
  node_type: memory
  type: reference
  originSessionId: b67e33cf-a0ef-48ee-80ae-f9c7bc8b6851
  modified: 2026-07-21T09:16:24.515Z
---

2026-07-21: STL (shectory-trader) died repeatedly during the day (11:50 after 1h41m, again 12:07 after ~13m).

**Root cause: hoster out of memory.** `earlyoom` (userspace OOM daemon, `EARLYOOM_ARGS=-m 8 -s 25 ... --prefer (next-server|node|python|python3|gunicorn|uvicorn|ollama)`) SIGTERM-kills the biggest `--prefer` match when available RAM <8% AND free swap <25%. STL runs as `uvicorn` → prime target. RAM+swap were both exhausted (available ~300-440MB of 5.9GB, swap 4GB 100% full).

**Diagnosis fingerprints:** `systemctl status` shows `code=killed, signal=TERM`; journal shows clean "Application shutdown complete" (NOT a crash, NOT kernel-OOM in dmesg — earlyoom acts in userspace first, sends SIGTERM); `journalctl` around death shows `earlyoom[..]: escalating to SIGKILL after N seconds`. `Restart=on-failure` treats SIGTERM as a clean stop → does NOT restart → STL stays dead.

**Aggravator:** the hoster is a SHARED box (komissionka + shectory-assist + 3× next-server + postgres + ai46 + STL). komissionka's `deploy-worker` churns `next-server` builds (~300MB spikes) that push the box over the edge.

**LIVE ROBOT UNAFFECTED:** robots run on the QUIK agent (VDS), not STL. STL down = monitoring/paper/control-plane blind, trading continues.

**Mitigation options (operator decision — shared box, other apps):** add uvicorn/STL to earlyoom `--avoid` (sacrifices another app); systemd `MemoryMin`/`MemoryLow` reservation for STL; `Restart=always` override so STL self-recovers after each kill; throttle komissionka deploy churn; add swap/RAM; dedicated box for STL. STL restart is safe (robots trade on agent; drops agent gRPC link briefly, agent redials).

Related: [[reference_vds_load]], [[reference_ssh_hoster]], [[reference_robot_perorder_cap_trap]].
