# PyInstaller spec: single-exe robot-runner for the QUIK VDS (no Python install
# needed there). BUILD ON WINDOWS (PyInstaller does not cross-compile):
#   python -m PyInstaller --clean -y robot_runner/build.spec --distpath dist/runner
# Then stage dist/runner/robot-runner.exe into hoster ~/quik_build/quik_agent/dist/
# so publish_quik_agent.sh ships it inside the agent release zip.
from PyInstaller.utils.hooks import collect_submodules

a = Analysis(
    ['robot_runner/main.py'],
    pathex=['.'],
    hiddenimports=(collect_submodules('trader.lab.strategies')
                   + collect_submodules('trader.lab')
                   + collect_submodules('trader.quik.pb')
                   + ['grpc', 'structlog']),
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas,
          name='robot-runner', console=True, onefile=True)
