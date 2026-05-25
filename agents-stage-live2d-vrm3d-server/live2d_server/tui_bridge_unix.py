"""Unix TUI bridge session — ``pty.fork`` + ``tmux attach`` over WebSocket.

Spawns a child PTY running ``tmux attach-session -d -t <session_id>`` per
connection. tmux owns the long-lived session; this class is a transport
that lets a browser xterm.js view *participate in* it. Closing the
WebSocket detaches the client but leaves the tmux session (and whatever
TUI is running inside) alive.
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

from fastapi import WebSocket, WebSocketDisconnect

from .tui_session_manager import TuiSessionInfo, get_manager

logger = logging.getLogger(__name__)

_READ_CHUNK = 8192
_SELECT_TIMEOUT = 0.5


class UnixTuiBridgeSession:
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
            "tui-bridge[unix]: attached pid=%d fd=%d session=%s",
            pid, fd, self._info.session_id,
        )

    async def write(self, data: str) -> None:
        if self._master_fd is None or not data:
            return
        try:
            os.write(self._master_fd, data.encode("utf-8", errors="replace"))
        except OSError as exc:
            logger.warning("tui-bridge[unix]: write error: %s", exc)

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
        logger.info("tui-bridge[unix]: detached session=%s", self._info.session_id)

    # ------------------------------------------------------------------

    def _resize_pty(self, cols: int, rows: int) -> None:
        if self._master_fd is None:
            return
        winsize = struct.pack("HHHH", rows, cols, 0, 0)
        try:
            fcntl.ioctl(self._master_fd, termios.TIOCSWINSZ, winsize)
        except OSError as exc:
            logger.warning("tui-bridge[unix]: resize failed: %s", exc)

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
                        "tui-bridge[unix]: PTY EOF (tmux attach exited); session=%s remains alive",
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
            logger.debug("tui-bridge[unix]: read loop ended: %s", exc)

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
