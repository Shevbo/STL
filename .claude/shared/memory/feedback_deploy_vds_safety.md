---
name: feedback-deploy-vds-safety
description: deploy.sh remote npm build can thrash the shared VDS and take down prod + the live robot
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 3c63f384-9e12-493f-b9fa-d37d88534a00
---

`deploy/deploy.sh` runs `npm install && npm run build` AND `poetry install` ON the hoster
(83.69.248.175). On 2026-07-03 this thrashed the shared VDS: SSH banner-timeout + HTTP dead
for ~15 min (port 22 open but userland starved), needing an operator hard-reboot from the
hoster panel (no console/provider API available to Claude). The box has ~6 GB RAM but the
concurrent build + uvicorn + AI46 (20 streams) + optimizer tipped it over.

**Why it matters:** the LIVE real-money robot runs INSIDE the STL uvicorn process (see
[[project-live-fvg-robot]]), so an OOM/thrash during deploy takes the robot down too — a
single point of failure. This directly motivated [[project-robot-on-quik-agent]].

**How to apply — SAFE deploy for a frontend/backend change while the live robot runs:**
1. Build the frontend LOCALLY (`cd frontend && node ./node_modules/vite/bin/vite.js build`).
2. `ssh hoster 'cd ~/apps/shectory-trader && git pull origin main'` (backend code only).
3. Ship the built bundle with `scp` (NO `rsync` in local Git Bash): copy `dist/index.html`
   + the new hashed `dist/assets/index-*.js` and `index-*.css`. nginx serves `frontend/dist`
   statically — no restart for a FE-only change.
4. Restart backend ONLY if Python changed: `sudo systemctl restart shectory-trader`
   (robot resumes from `state_json`; brief reinit load spike).
Avoid the remote `npm build`/`poetry install` while trading is live. `state_json` for
live-fvg-RIU6 is double-encoded (`"{\"live_real\": true}"`) — `->>'live_real'` returns null;
read the whole JSON string, don't panic that the real flag was wiped.

**Update 2026-07-10:** cherry-picking changed assets BROKE prod CSS (built index.html
referenced a new css hash, only js was scp'd — page unstyled, operator noticed).
Rule: frontend deploy = scp index.html + THE WHOLE dist/assets/, then verify on the
hoster that every `assets/*` referenced by index.html exists (one-line for loop).
