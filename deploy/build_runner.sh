#!/usr/bin/env bash
# Build robot-runner.exe (Windows). RUN ON WINDOWS (PyInstaller can't cross-build).
# Output: dist/runner/robot-runner.exe + dist/runner/strategies_doc.json (Task 10:
# strategy titles/docs/default-params for the agent's local showcase page, read
# from next to the runner exe). Then stage BOTH for release:
#   scp dist/runner/robot-runner.exe dist/runner/strategies_doc.json hoster:~/quik_build/quik_agent/dist/
#   ssh hoster 'bash ~/apps/shectory-trader/deploy/publish_quik_agent.sh [AGENT_ID]'
set -euo pipefail
cd "$(dirname "$0")/.."
python -m pip show pyinstaller >/dev/null 2>&1 || python -m pip install pyinstaller
python -m PyInstaller --clean -y robot_runner/build.spec --distpath dist/runner
echo "built: dist/runner/robot-runner.exe"

python -m robot_runner.export_docs dist/runner/strategies_doc.json
echo "built: dist/runner/strategies_doc.json"
