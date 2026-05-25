"""TUI Bridge — PTY attach over WebSocket, tmux-backed.

Per WebSocket connection we spawn a child running
``tmux attach-session -d -t <session_id>``. tmux owns the long-lived
session; this module is a thin transport that lets a browser xterm.js
view *participate in* an existing tmux session. Closing the WebSocket
detaches the client but leaves the tmux session — and any TUI running
inside it — alive.

Protocol (JSON over WebSocket text frames):

  → client sends: {"type": "input",  "data": "<bytes>"}
  → client sends: {"type": "resize", "cols": 80, "rows": 24}
  ← server sends: {"type": "output", "data": "<terminal output>"}
  ← server sends: {"type": "session", "session_id": "tui-...", "label": "..."}
  ← server sends: {"type": "detached"} (tmux attach exited; session lives on)
  ← server sends: {"type": "error", "message": "..."}

Platform support:
  - Unix (macOS/Linux): pty.fork + tmux       — :mod:`tui_bridge_unix`
  - Windows           : pywinpty + psmux/tmux — :mod:`tui_bridge_win`

On hosts without ``tmux``/``psmux`` on PATH the ``/api/tui/config``
endpoint reports ``has_tmux: false`` and the frontend hides the TUI
entry point.
"""

from __future__ import annotations

import logging
import platform
from typing import Any, Protocol

from fastapi import WebSocket, WebSocketDisconnect

from .tui_session_manager import (
    TuiBridgeError,
    TuiSessionInfo,
    get_manager,
    tmux_available,
)

logger = logging.getLogger(__name__)

_IS_WINDOWS = platform.system() == "Windows"


class _BridgeSession(Protocol):
    """Common shape for platform-specific bridge sessions."""

    @property
    def session_id(self) -> str: ...
    async def start(self) -> None: ...
    async def write(self, data: str) -> None: ...
    def resize(self, cols: int, rows: int) -> None: ...
    async def stop(self) -> None: ...


def _create_session(
    ws: WebSocket,
    info: TuiSessionInfo,
    *,
    cols: int,
    rows: int,
) -> _BridgeSession:
    """Return the bridge session class for the current platform."""
    if _IS_WINDOWS:
        from .tui_bridge_win import WinTuiBridgeSession
        return WinTuiBridgeSession(ws, info, cols=cols, rows=rows)
    from .tui_bridge_unix import UnixTuiBridgeSession
    return UnixTuiBridgeSession(ws, info, cols=cols, rows=rows)


async def run_ws_session(ws: WebSocket) -> None:
    """Handle an /api/tui/ws connection end-to-end.

    We *always* accept the WebSocket before doing any validation. Closing
    before ``accept()`` makes Starlette reject the upgrade with a bare
    HTTP 403, which the browser cannot distinguish from "feature off /
    auth blocked / route missing" and which our custom close codes never
    reach. Accept-then-close lets the frontend's ``onclose`` see the
    real 4400/4404/4503/4403 code and surface a useful message.
    """
    await ws.accept()

    if not tmux_available():
        await ws.close(code=4503, reason="tmux is not installed on the server")
        return

    session_id = (ws.query_params.get("session_id") or "").strip()
    if not session_id:
        await ws.close(code=4400, reason="session_id query param required")
        return

    manager = get_manager()
    if not manager.has_session(session_id):
        try:
            await ws.send_json({
                "type": "error",
                "message": f"unknown tui session: {session_id}",
            })
        except Exception:  # noqa: BLE001
            pass
        await ws.close(code=4404, reason=f"unknown tui session: {session_id}")
        return

    try:
        info = next(s for s in manager.list_sessions() if s.session_id == session_id)
    except StopIteration:
        await ws.close(code=4404, reason="session vanished before attach")
        return

    cols = _coerce_int(ws.query_params.get("cols"), 80)
    rows = _coerce_int(ws.query_params.get("rows"), 24)

    session = _create_session(ws, info, cols=cols, rows=rows)
    try:
        await session.start()
        while True:
            msg: dict[str, Any] = await ws.receive_json()
            msg_type = msg.get("type", "")
            if msg_type == "input":
                await session.write(msg.get("data", ""))
            elif msg_type == "resize":
                session.resize(
                    _coerce_int(msg.get("cols"), 80),
                    _coerce_int(msg.get("rows"), 24),
                )
            elif msg_type == "ping":
                try:
                    await ws.send_json({"type": "pong"})
                except Exception:  # noqa: BLE001
                    break
    except WebSocketDisconnect:
        logger.info("tui-bridge: ws disconnected (session=%s)", session_id)
    except TuiBridgeError as exc:
        logger.warning("tui-bridge: bridge error: %s", exc)
        try:
            await ws.send_json({"type": "error", "message": str(exc)})
        except Exception:  # noqa: BLE001
            pass
    except Exception as exc:  # noqa: BLE001
        logger.exception("tui-bridge: unexpected error: %s", exc)
    finally:
        await session.stop()


def _coerce_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
