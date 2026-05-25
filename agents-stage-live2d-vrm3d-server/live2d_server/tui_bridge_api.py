"""TUI Bridge HTTP API — list / create / kill TUI sessions + attach WS.

Endpoints
---------
GET    /api/tui/config              — feature flag + tmux availability
GET    /api/tui/sessions            — list live tmux-backed TUI sessions
POST   /api/tui/sessions            — create a new session
DELETE /api/tui/sessions/{id}       — kill a session (and its TUI)
WS     /api/tui/ws?session_id=...   — attach to a session over WebSocket

This router is intentionally independent of ``session_bridge`` so adding
it to ``main.py`` does not perturb any existing chat-session behavior.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import APIRouter, HTTPException, WebSocket
from pydantic import BaseModel, Field

from .tui_bridge import run_ws_session
from .tui_session_manager import (
    TuiBridgeError,
    TuiSessionInfo,
    get_manager,
    tmux_available,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tui", tags=["tui-bridge"])


def _is_enabled() -> bool:
    return os.getenv("TUI_BRIDGE_ENABLED", "false").lower() == "true"


def _max_sessions() -> int:
    try:
        return max(1, int(os.getenv("TUI_BRIDGE_MAX_SESSIONS", "8")))
    except ValueError:
        return 8


def _ensure_enabled() -> None:
    if not _is_enabled():
        raise HTTPException(status_code=403, detail="TUI bridge is disabled")
    if not tmux_available():
        raise HTTPException(status_code=503, detail="tmux is not installed on the server")


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class TuiCreateRequest(BaseModel):
    label: str = Field(default="", max_length=120)
    cwd: str = Field(default="", max_length=1024)
    command: str = Field(default="", max_length=1024)


class TuiSessionResponse(BaseModel):
    session_id: str
    label: str
    cwd: str
    command: str
    created_at: float
    attached_clients: int
    windows: int
    last_activity_at: float

    @classmethod
    def from_info(cls, info: TuiSessionInfo) -> "TuiSessionResponse":
        return cls(**info.to_dict())


class TuiConfigResponse(BaseModel):
    enabled: bool
    has_tmux: bool
    max_sessions: int
    active_sessions: int


class TuiListResponse(BaseModel):
    sessions: list[TuiSessionResponse]


class TuiKillResponse(BaseModel):
    session_id: str
    killed: bool


# ---------------------------------------------------------------------------
# REST endpoints
# ---------------------------------------------------------------------------

@router.get("/config", response_model=TuiConfigResponse)
async def get_config() -> TuiConfigResponse:
    """Always reachable — frontend uses this to decide whether to show the UI."""
    has = tmux_available()
    active = 0
    if has:
        try:
            active = len(get_manager().list_sessions())
        except Exception as exc:  # noqa: BLE001
            logger.debug("tui-bridge: list during config probe failed: %s", exc)
    return TuiConfigResponse(
        enabled=_is_enabled(),
        has_tmux=has,
        max_sessions=_max_sessions(),
        active_sessions=active,
    )


@router.get("/sessions", response_model=TuiListResponse)
async def list_sessions() -> TuiListResponse:
    _ensure_enabled()
    try:
        sessions = get_manager().list_sessions()
    except TuiBridgeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return TuiListResponse(sessions=[TuiSessionResponse.from_info(s) for s in sessions])


@router.post("/sessions", response_model=TuiSessionResponse)
async def create_session(payload: TuiCreateRequest) -> TuiSessionResponse:
    _ensure_enabled()
    try:
        info = get_manager().create_session(
            label=payload.label,
            cwd=payload.cwd,
            command=payload.command,
        )
    except TuiBridgeError as exc:
        # 400 — caller-fixable (bad cwd, capacity reached); 500 otherwise.
        status = 400 if "cwd" in str(exc).lower() or "max" in str(exc).lower() else 500
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    return TuiSessionResponse.from_info(info)


@router.delete("/sessions/{session_id}", response_model=TuiKillResponse)
async def kill_session(session_id: str) -> TuiKillResponse:
    _ensure_enabled()
    try:
        killed = get_manager().kill_session(session_id)
    except TuiBridgeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return TuiKillResponse(session_id=session_id, killed=killed)


# ---------------------------------------------------------------------------
# WebSocket endpoint
# ---------------------------------------------------------------------------

@router.websocket("/ws")
async def tui_ws(ws: WebSocket) -> None:
    if not _is_enabled():
        # Accept-then-close so the frontend sees code 4403 instead of an
        # opaque HTTP 403 reject (Starlette's default for pre-accept close).
        await ws.accept()
        await ws.close(code=4403, reason="TUI bridge is disabled")
        return
    await run_ws_session(ws)


__all__ = ["router"]
