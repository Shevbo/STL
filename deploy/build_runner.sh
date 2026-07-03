#!/usr/bin/env bash
# Build robot-runner.exe (Windows). RUN ON WINDOWS (PyInstaller can't cross-build).
# Output: dist/runner/robot-runner.exe. Then stage it for release:
#   scp dist/runner/robot-runner.exe hoster:~/quik_build/quik_agent/dist/
#   ssh hoster 'bash ~/apps/shectory-trader/deploy/publish_quik_agent.sh [AGENT_ID]'
set -euo pipefail
cd "$(dirname "$0")/.."
python -m pip show pyinstaller >/dev/null 2>&1 || python -m pip install pyinstaller
python -m PyInstaller --clean -y robot_runner/build.spec --distpath dist/runner
echo "built: dist/runner/robot-runner.exe"
