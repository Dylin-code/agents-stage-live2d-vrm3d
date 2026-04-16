"""Unix PTY session implementation (macOS / Linux)."""

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

logger = logging.getLogger(__name__)

_DEFAULT_SHELL = os.environ.get("SHELL", "/bin/bash")
_READ_CHUNK = 4096
_SELECT_TIMEOUT = 0.5


class UnixPtySession:
    """Manages a single PTY child process via pty.fork + select."""

    def __init__(self, ws: WebSocket, cols: int = 80, rows: int = 24):
        self._ws = ws
        self._cols = cols
        self._rows = rows
        self._master_fd: int | None = None
        self._child_pid: int | None = None
        self._reader_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        pid, fd = pty.fork()
        if pid == 0:
            os.execvpe(_DEFAULT_SHELL, [_DEFAULT_SHELL, "-l"], os.environ)
            os._exit(1)
        self._child_pid = pid
        self._master_fd = fd
        self._resize_pty(self._cols, self._rows)
        self._reader_task = asyncio.create_task(self._read_loop())
        logger.info("Unix PTY started: pid=%d, fd=%d, shell=%s", pid, fd, _DEFAULT_SHELL)

    async def write(self, data: str) -> None:
        if self._master_fd is None:
            return
        try:
            os.write(self._master_fd, data.encode("utf-8", errors="replace"))
        except OSError as exc:
            logger.warning("PTY write error: %s", exc)

    def resize(self, cols: int, rows: int) -> None:
        self._cols = cols
        self._rows = rows
        if self._master_fd is not None:
            self._resize_pty(cols, rows)

    async def stop(self) -> None:
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
            try:
                os.kill(self._child_pid, signal.SIGHUP)
                os.waitpid(self._child_pid, os.WNOHANG)
            except (OSError, ChildProcessError):
                pass
            self._child_pid = None
        logger.info("Unix PTY stopped")

    # ------------------------------------------------------------------

    def _resize_pty(self, cols: int, rows: int) -> None:
        if self._master_fd is None:
            return
        winsize = struct.pack("HHHH", rows, cols, 0, 0)
        try:
            fcntl.ioctl(self._master_fd, termios.TIOCSWINSZ, winsize)
        except OSError as exc:
            logger.warning("Failed to resize PTY: %s", exc)

    async def _read_loop(self) -> None:
        loop = asyncio.get_running_loop()
        fd = self._master_fd
        if fd is None:
            return
        try:
            while True:
                data = await loop.run_in_executor(None, self._blocking_read, fd)
                if data is None:
                    logger.info("PTY read EOF, child process likely exited")
                    break
                await self._ws.send_json({"type": "output", "data": data})
        except (asyncio.CancelledError, WebSocketDisconnect):
            pass
        except Exception as exc:
            logger.debug("PTY read loop ended: %s", exc)

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
