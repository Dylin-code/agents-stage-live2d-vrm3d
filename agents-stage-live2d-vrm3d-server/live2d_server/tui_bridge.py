"""TUI Bridge — PTY attach over WebSocket, tmux-backed.

Spawns a child PTY running ``tmux attach-session -t <session_id>`` per
WebSocket connection. tmux owns the long-lived session; this module is a
thin transport that lets a browser xterm.js view *participate in* an
existing tmux session. Closing the WebSocket detaches the client but
leaves the tmux session — and any TUI running inside it — alive.

Protocol mirrors ``web_terminal.py`` so the frontend can reuse the same
xterm.js plumbing:

  → client sends: {"type": "input",  "data": "<bytes>"}
  → client sends: {"type": "resize", "cols": 80, "rows": 24}
  ← server sends: {"type": "output", "data": "<terminal output>"}
  ← server sends: {"type": "session", "session_id": "tui-...", "label": "..."}
  ← server sends: {"type": "error", "message": "..."}

Platform support: Unix-only (tmux dependency). On Windows the
``tui_bridge_api`` config endpoint will report ``has_tmux: false`` and
the frontend should hide the TUI entry point.
"""

from __future__ import annotations

import asyncio
import fcntl
import logging
import os
import pty
import select
import signal
import struct
import termios
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from .tui_session_manager import (
    TuiBridgeError,
    TuiSessionInfo,
    get_manager,
    tmux_available,
)

logger = logging.getLogger(__name__)

_READ_CHUNK = 8192
_SELECT_TIMEOUT = 0.5


class TuiBridgeSession:
    """One PTY child running ``tmux attach`` for the given tui session id."""

    def __init__(
        self,
        ws: WebSocket,
        session_info: TuiSessionInfo,
        *,
        cols: int = 80,
        rows: int = 24,
    ) -> None:
        self._ws = ws
        self._info = session_info
        self._cols = cols
        self._rows = rows
        self._master_fd: int | None = None
        self._child_pid: int | None = None
        self._reader_task: asyncio.Task[None] | None = None

    @property
    def session_id(self) -> str:
        return self._info.session_id

    async def start(self) -> None:
        pid, fd = pty.fork()
        if pid == 0:
            # Child — replace with tmux attach. ``-d`` detaches any other
            # client so two browser tabs don't fight over the same session;
            # remove if you'd rather support shared viewing.
            try:
                os.execvpe(
                    "tmux",
                    ["tmux", "attach-session", "-d", "-t", self._info.session_id],
                    os.environ,
                )
            except OSError:
                os._exit(127)
            os._exit(1)

        self._child_pid = pid
        self._master_fd = fd
        self._resize_pty(self._cols, self._rows)
        # Send a one-shot session info envelope so the client can render the title.
        await self._ws.send_json(
            {
                "type": "session",
                "session_id": self._info.session_id,
                "label": self._info.label,
                "cwd": self._info.cwd,
                "command": self._info.command,
            }
        )
        self._reader_task = asyncio.create_task(self._read_loop())
        logger.info(
            "tui-bridge: attached pid=%d fd=%d session=%s",
            pid, fd, self._info.session_id,
        )

    async def write(self, data: str) -> None:
        if self._master_fd is None or not data:
            return
        try:
            os.write(self._master_fd, data.encode("utf-8", errors="replace"))
        except OSError as exc:
            logger.warning("tui-bridge: write error: %s", exc)

    def resize(self, cols: int, rows: int) -> None:
        self._cols = cols
        self._rows = rows
        if self._master_fd is not None:
            self._resize_pty(cols, rows)

    async def stop(self) -> None:
        """Detach (does NOT kill the tmux session — that is the whole point)."""
        if self._reader_task and not self._reader_task.done():
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
        if self._master_fd is not None:
            try:
                os.close(self._master_fd)
            except OSError:
                pass
            self._master_fd = None
        if self._child_pid is not None:
            # Kill the ``tmux attach`` *client* — the tmux server / session live on.
            try:
                os.kill(self._child_pid, signal.SIGHUP)
                os.waitpid(self._child_pid, os.WNOHANG)
            except (OSError, ChildProcessError):
                pass
            self._child_pid = None
        try:
            get_manager().touch(self._info.session_id)
        except Exception:  # noqa: BLE001 — touch is best-effort metadata only
            pass
        logger.info("tui-bridge: detached session=%s", self._info.session_id)

    # ------------------------------------------------------------------

    def _resize_pty(self, cols: int, rows: int) -> None:
        if self._master_fd is None:
            return
        winsize = struct.pack("HHHH", rows, cols, 0, 0)
        try:
            fcntl.ioctl(self._master_fd, termios.TIOCSWINSZ, winsize)
        except OSError as exc:
            logger.warning("tui-bridge: resize failed: %s", exc)

    async def _read_loop(self) -> None:
        loop = asyncio.get_running_loop()
        fd = self._master_fd
        if fd is None:
            return
        try:
            while True:
                data = await loop.run_in_executor(None, self._blocking_read, fd)
                if data is None:
                    logger.info(
                        "tui-bridge: PTY EOF (tmux attach exited); session=%s remains alive",
                        self._info.session_id,
                    )
                    try:
                        await self._ws.send_json({"type": "detached"})
                    except Exception:  # noqa: BLE001
                        pass
                    break
                await self._ws.send_json({"type": "output", "data": data})
        except (asyncio.CancelledError, WebSocketDisconnect):
            pass
        except Exception as exc:  # noqa: BLE001
            logger.debug("tui-bridge: read loop ended: %s", exc)

    @staticmethod
    def _blocking_read(fd: int) -> str | None:
        try:
            while True:
                readable, _, _ = select.select([fd], [], [], _SELECT_TIMEOUT)
                if readable:
                    raw = os.read(fd, _READ_CHUNK)
                    if not raw:
                        return None
                    return raw.decode("utf-8", errors="replace")
        except OSError:
            return None


# ---------------------------------------------------------------------------
# WebSocket entrypoint — invoked by tui_bridge_api router
# ---------------------------------------------------------------------------

async def run_ws_session(ws: WebSocket) -> None:
    """Handle an /api/tui/ws connection end-to-end."""
    if not tmux_available():
        await ws.close(code=4503, reason="tmux is not installed on the server")
        return

    session_id = (ws.query_params.get("session_id") or "").strip()
    if not session_id:
        await ws.close(code=4400, reason="session_id query param required")
        return

    manager = get_manager()
    if not manager.has_session(session_id):
        await ws.close(code=4404, reason=f"unknown tui session: {session_id}")
        return

    # Look up metadata for the title envelope.
    try:
        info = next(s for s in manager.list_sessions() if s.session_id == session_id)
    except StopIteration:
        await ws.close(code=4404, reason="session vanished before attach")
        return

    cols = _coerce_int(ws.query_params.get("cols"), 80)
    rows = _coerce_int(ws.query_params.get("rows"), 24)

    await ws.accept()

    session = TuiBridgeSession(ws, info, cols=cols, rows=rows)
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
                # Keepalive helper — let the client poke the connection.
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
