"""QUIK agent self-update: release endpoints + on-demand update trigger.

The agent's selfupdate.HTTPSource polls:
  GET <base>/agent_release?arch=<amd64|386>      -> decimal build_rev (text)
  GET <base>/agent_release/zip?arch=<amd64|386>  -> the update ZIP (contains the exe)
authenticated with the agent's Bearer token (same QUIK_AGENT_TOKEN as the gRPC link).

The operator triggers an immediate update with:
  POST /api/v1/quik/agent/{agent_id}/self-update  (portal-authenticated)
which enqueues a COMMAND_TYPE_SELF_UPDATE on the agent's live session; the agent then
pulls the ZIP from the release endpoints and restarts itself. No manual file copying.

Release files live under settings.quik_agent_release_dir (default "agent_release"):
  <dir>/<arch>.rev   text file with the build_rev integer
  <dir>/<arch>.zip   the update archive (flat, containing quik-agent_<arch>.exe)
"""

from __future__ import annotations

from pathlib import Path

import structlog
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse

from trader.auth.guard import require_auth
from trader.quik.server import verify_agent_token

log = structlog.get_logger()

router = APIRouter(prefix="/api/v1/quik", tags=["quik-release"])

_ALLOWED_ARCH = {"amd64", "386"}


def _release_dir(request: Request) -> Path:
    settings = request.app.state.settings
    return Path(getattr(settings, "quik_agent_release_dir", "agent_release"))


def _agent_auth(request: Request) -> str:
    """Authenticate the agent's Bearer token (same secret as the gRPC link)."""
    settings = request.app.state.settings
    auth = request.headers.get("authorization", "")
    token = auth[7:] if auth.startswith("Bearer ") else None
    subject = verify_agent_token(
        token,
        settings.quik_agent_token.get_secret_value(),
        settings.shectory_auth_bridge_secret,
    )
    if subject is None:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return subject


def _arch(arch: str) -> str:
    if arch not in _ALLOWED_ARCH:
        raise HTTPException(status_code=400, detail="bad arch")
    return arch


@router.get("/agent_release", response_class=PlainTextResponse)
async def agent_release_rev(request: Request, arch: str = "amd64") -> str:
    _agent_auth(request)
    arch = _arch(arch)
    rev_file = _release_dir(request) / f"{arch}.rev"
    if not rev_file.is_file():
        raise HTTPException(status_code=404, detail="no release")
    return rev_file.read_text(encoding="utf-8").strip()


@router.get("/agent_release/zip")
async def agent_release_zip(request: Request, arch: str = "amd64") -> FileResponse:
    _agent_auth(request)
    arch = _arch(arch)
    zip_file = _release_dir(request) / f"{arch}.zip"
    if not zip_file.is_file():
        raise HTTPException(status_code=404, detail="no release zip")
    return FileResponse(
        str(zip_file),
        media_type="application/zip",
        filename=f"quik-agent_{arch}.zip",
    )


@router.post("/agent/{agent_id}/self-update")
async def trigger_self_update(agent_id: str, request: Request) -> dict:
    """Enqueue a SELF_UPDATE command on the agent's live session (operator only)."""
    require_auth(request.app.state.settings.shectory_auth_bridge_secret, request)
    server = getattr(request.app.state, "quik_server", None)
    if server is None:
        raise HTTPException(status_code=503, detail="quik agent link not enabled")

    # Imported lazily so the route module loads even before stubs are generated.
    import sys

    sys.path.insert(0, str(Path("trader/quik/pb")))
    from shectory.quik.v1 import quik_agent_pb2 as pb  # noqa: E402

    cmd = pb.Command(id="selfupdate", type=pb.CommandType.COMMAND_TYPE_SELF_UPDATE)
    server.enqueue_command(agent_id, cmd)
    log.info("quik.self_update.triggered", agent=agent_id)
    return {"ok": True, "agent_id": agent_id, "command": "self_update"}
