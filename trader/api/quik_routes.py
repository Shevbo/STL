"""FastAPI routes for the QUIK agent link (sprint02 Phase 1, read-only).

Exposes agent status (link lamp / diagnostics / last_seen), securities, latest
tick + order book per code, and the "Интерфейс биржи" (exchange interface)
data-source selector (Finam Trade API vs QUIK agent), persisted in settings.

DATA SOURCE + status only. No order routing / placement (Guard 3).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from trader.auth.guard import require_auth

router = APIRouter(prefix="/api/v1/quik", tags=["quik"])

_VALID_INTERFACES = ("finam", "quik")


def _auth(request: Request) -> str:
    return require_auth(request.app.state.settings.shectory_auth_bridge_secret, request)


def _store(request: Request):
    return getattr(request.app.state, "quik_store", None)


@router.get("/status")
async def quik_status(request: Request, agent_id: str | None = None):
    """Per-agent link status: lamp (green/yellow/red), diagnostics, last_seen."""
    _auth(request)
    store = _store(request)
    settings = request.app.state.settings
    if store is None:
        return {
            "enabled": settings.quik_agent_enabled,
            "listen": settings.quik_agent_grpc_listen,
            "agents": [],
        }
    return {
        "enabled": settings.quik_agent_enabled,
        "listen": settings.quik_agent_grpc_listen,
        "agents": store.status(agent_id),
    }


@router.get("/securities")
async def quik_securities(request: Request, agent_id: str | None = None):
    """FORTS securities reference reported by the agent (code, step, step cost)."""
    _auth(request)
    store = _store(request)
    if store is None:
        return {"items": []}
    return {"items": store.securities(agent_id)}


@router.get("/tick/{code}")
async def quik_tick(code: str, request: Request, agent_id: str | None = None):
    """Latest tick (last/bid/ask/OI) for one security code."""
    _auth(request)
    store = _store(request)
    tick = store.tick(code, agent_id) if store else None
    if tick is None:
        raise HTTPException(status_code=404, detail=f"no tick for {code}")
    return tick


@router.get("/orderbook/{code}")
async def quik_order_book(code: str, request: Request, agent_id: str | None = None):
    """Latest order book (стакан) for one security code."""
    _auth(request)
    store = _store(request)
    ob = store.order_book(code, agent_id) if store else None
    if ob is None:
        raise HTTPException(status_code=404, detail=f"no order book for {code}")
    return ob


@router.get("/params")
async def quik_params(request: Request, agent_id: str | None = None):
    """Price step / step cost params (commission coef) reported by the agent."""
    _auth(request)
    store = _store(request)
    return store.params(agent_id) if store else None


# ---- "Интерфейс биржи" exchange-interface selector (data source only) ----

class ExchangeInterface(BaseModel):
    interface: str  # "finam" | "quik"


@router.get("/exchange-interface")
async def get_exchange_interface(request: Request):
    """Current exchange data-source interface selection."""
    _auth(request)
    settings = request.app.state.settings
    return {
        "interface": getattr(settings, "exchange_interface", "finam"),
        "options": [
            {"value": "finam", "label": "Finam Trade API"},
            {"value": "quik", "label": "QUIK агент"},
        ],
    }


@router.put("/exchange-interface")
async def set_exchange_interface(body: ExchangeInterface, request: Request):
    """Persist the exchange data-source interface selection.

    DATA SOURCE only. Switching to 'quik' does not route or place orders; it
    selects where market data / reference comes from for display.
    """
    _auth(request)
    if body.interface not in _VALID_INTERFACES:
        raise HTTPException(
            status_code=400,
            detail=f"interface must be one of {_VALID_INTERFACES}",
        )
    settings = request.app.state.settings
    # In-process persistence: pydantic-settings reads env at boot; we mutate the
    # live Settings object so the choice survives for this process. A durable
    # store (env/DB) is wired by sub-agent D's settings persistence layer.
    settings.exchange_interface = body.interface
    return {"interface": settings.exchange_interface, "ok": True}
