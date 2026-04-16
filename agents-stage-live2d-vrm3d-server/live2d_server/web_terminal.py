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
"""

import logging
import platform
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/terminal", tags=["web-terminal"])

_IS_WINDOWS = platform.system() == "Windows"


def _create_session(ws: WebSocket, cols: int, rows: int) -> Any:
    """Factory: return platform-appropriate PtySession."""
    if _IS_WINDOWS:
        from .web_terminal_win import WinPtySession
        return WinPtySession(ws, cols=cols, rows=rows)
    else:
        from .web_terminal_unix import UnixPtySession
        return UnixPtySession(ws, cols=cols, rows=rows)


@router.websocket("/ws")
async def terminal_ws(ws: WebSocket) -> None:
    """WebSocket endpoint: /api/terminal/ws"""
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
