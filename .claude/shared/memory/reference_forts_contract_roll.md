---
name: reference-forts-contract-roll
description: How to roll Live robots from an expiring FORTS contract to the next (e.g. M6->U6)
metadata: 
  node_type: memory
  type: reference
  originSessionId: a3701a33-3d27-427a-aa8b-04e822e31829
---

Rolling Live robots to the next FORTS contract month.

FORTS month letters: F G H J K M N Q U V X Z = Jan..Dec. Quarterlies (RI, Si, GZ, MX, MM, etc.) trade H/M/U/Z (Mar/Jun/Sep/Dec). Some (Brent BR/BM, NG) are MONTHLY — their front can be N6 (July) while quarterlies are M6 (June); a "M6->U6" roll must NOT touch N6 symbols.

The traded instrument lives in `robots.params_json.symbol` (jsonb). The scheduler caches each robot's `params_json` ONCE at startup ([[reference-ssh-hoster]] -> trader/lab/scheduler.py `_window_loop`/`start`), so a DB edit does NOT take effect until the service restarts.

Procedure (done 2026-06-17 for M->U, all 21 deployed M6 robots, all paper):
1. Dry-run SELECT first. SSH: `set -a; . ~/.shectory_trade.env; set +a; psql "$LAB_DB_URL" ...` (DB is local on the hoster; psql is installed; LAB_DB_URL is in that env file, NOT the systemd unit).
2. One atomic UPDATE, anchored to the suffix so the middle of a code isn't hit:
   `UPDATE robots SET params_json = jsonb_set(params_json,'{symbol}', to_jsonb(regexp_replace(params_json->>'symbol','M6$','U6'))), state_json='{}'::jsonb, updated_at=now() WHERE deployed AND params_json->>'symbol' LIKE '%M6' RETURNING id, params_json->>'symbol';`
3. Reset `state_json` to `{}` so the robot starts FLAT on the new contract (no phantom position carried from the old one). Safe only when no robot has `state_json->>'live_real'=true` (all were paper). live_trades history is preserved.
4. `sudo systemctl restart shectory-trader` so the scheduler reloads fresh params. Verify `... WHERE deployed AND symbol LIKE '%M6'` returns 0.

Caveats: robot `id`/`name` still contain the old month (e.g. `paper-fvg-GZM6` now trades GZU6) — only the symbol changed. Showcase position is summed from live_trades, so a robot that held an open M6 position shows a residual until offset. Next roll: U6->Z6 around Sept expiry.

Related: [[reference-commission-model]], robot cap is `_MAX_ACTIVE_ROBOTS` (default 50 in code as of 2026-06-18).
