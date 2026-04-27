"""Web Terminal — PTY over WebSocket.

Spawns a pseudo-terminal per WebSocket connection and relays I/O between
the browser (xterm.js) and the server shell.

Protocol (JSON over WebSocket text frames):
  → client sends: {"type": "input", "data": "<keystrokes>"}
  → client sends: {"type": "resize", "cols": 80, "rows": 24}
  ← server sends: {"type": "output", "data": "<terminal output>"}

Platform support:
  - Unix (macOS/Linux): pty.fork + select
  - Windows: pywinpty (must be installed separately)

Environment variables:
  WEB_TERMINAL_ENABLED     — "true" to enable (default: "false")
  WEB_TERMINAL_MAX_SESSIONS — max concurrent sessions (default: 2)
"""

import logging
import os
import platform
import threading
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/terminal", tags=["web-terminal"])

_IS_WINDOWS = platform.system() == "Windows"

# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def _is_enabled() -> bool:
    return os.getenv("WEB_TERMINAL_ENABLED", "false").lower() == "true"


def _max_sessions() -> int:
    try:
        return max(1, int(os.getenv("WEB_TERMINAL_MAX_SESSIONS", "2")))
    except ValueError:
        return 2


# Active session counter (thread-safe)
_active_lock = threading.Lock()
_active_count = 0


def _acquire_slot() -> bool:
    """Try to take a session slot. Returns True on success."""
    global _active_count
    with _active_lock:
        if _active_count >= _max_sessions():
            return False
        _active_count += 1
        return True


def _release_slot() -> None:
    global _active_count
    with _active_lock:
        _active_count = max(0, _active_count - 1)


# ---------------------------------------------------------------------------
# REST endpoint — frontend queries this to decide whether to show the button
# ---------------------------------------------------------------------------

@router.get("/config")
async def terminal_config() -> dict[str, Any]:
    with _active_lock:
        current = _active_count
    return {
        "enabled": _is_enabled(),
        "max_sessions": _max_sessions(),
        "active_sessions": current,
        "is_windows": _IS_WINDOWS,
    }


# ---------------------------------------------------------------------------
# Platform factory
# ---------------------------------------------------------------------------

def _create_session(ws: WebSocket, cols: int, rows: int) -> Any:
    """Factory: return platform-appropriate PtySession."""
    if _IS_WINDOWS:
        from .web_terminal_win import WinPtySession
        return WinPtySession(ws, cols=cols, rows=rows)
    else:
        from .web_terminal_unix import UnixPtySession
        return UnixPtySession(ws, cols=cols, rows=rows)


# ---------------------------------------------------------------------------
# WebSocket endpoint
# ---------------------------------------------------------------------------

@router.websocket("/ws")
async def terminal_ws(ws: WebSocket) -> None:
    """WebSocket endpoint: /api/terminal/ws"""
    if not _is_enabled():
        await ws.close(code=4403, reason="Web terminal is disabled")
        return

    if not _acquire_slot():
        await ws.close(
            code=4429,
            reason=f"Max sessions reached ({_max_sessions()})",
        )
        return

    await ws.accept()

    cols = int(ws.query_params.get("cols", "80"))
    rows = int(ws.query_params.get("rows", "24"))

    session = _create_session(ws, cols, rows)
    try:
        await session.start()

        while True:
            msg: dict[str, Any] = await ws.receive_json()
            msg_type = msg.get("type", "")

            if msg_type == "input":
                await session.write(msg.get("data", ""))
            elif msg_type == "resize":
                session.resize(
                    int(msg.get("cols", 80)),
                    int(msg.get("rows", 24)),
                )
    except WebSocketDisconnect:
        logger.info("Terminal WebSocket disconnected")
    except Exception as exc:
        logger.error("Terminal WebSocket error: %s", exc)
    finally:
        await session.stop()
        _release_slot()
