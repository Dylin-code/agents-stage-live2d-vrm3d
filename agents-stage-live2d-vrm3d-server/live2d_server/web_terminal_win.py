"""Windows PTY session implementation using pywinpty."""

import asyncio
import logging
import os

from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)

_READ_CHUNK = 4096
_POLL_INTERVAL = 0.05


def _default_shell() -> str:
    """Return PowerShell if available, otherwise cmd.exe."""
    ps = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                      "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
    if os.path.isfile(ps):
        return ps
    return os.environ.get("COMSPEC", "cmd.exe")


class WinPtySession:
    """Manages a single PTY process on Windows via pywinpty."""

    def __init__(self, ws: WebSocket, cols: int = 80, rows: int = 24):
        self._ws = ws
        self._cols = cols
        self._rows = rows
        self._process: object | None = None  # winpty.PTY
        self._reader_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        try:
            from winpty import PTY  # type: ignore[import-untyped]
        except ImportError as exc:
            raise RuntimeError(
                "pywinpty is required on Windows. Install it with: uv add pywinpty"
            ) from exc

        shell = _default_shell()
        proc = PTY(self._cols, self._rows)
        proc.spawn(shell)
        self._process = proc
        self._reader_task = asyncio.create_task(self._read_loop())
        logger.info("Windows PTY started: shell=%s, cols=%d, rows=%d", shell, self._cols, self._rows)

    async def write(self, data: str) -> None:
        if self._process is None:
            return
        try:
            self._process.write(data)  # type: ignore[union-attr]
        except Exception as exc:
            logger.warning("WinPTY write error: %s", exc)

    def resize(self, cols: int, rows: int) -> None:
        self._cols = cols
        self._rows = rows
        if self._process is not None:
            try:
                self._process.set_size(cols, rows)  # type: ignore[union-attr]
            except Exception as exc:
                logger.warning("WinPTY resize error: %s", exc)

    async def stop(self) -> None:
        if self._reader_task and not self._reader_task.done():
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
        if self._process is not None:
            try:
                self._process.close()  # type: ignore[union-attr]
            except Exception:
                pass
            self._process = None
        logger.info("Windows PTY stopped")

    # ------------------------------------------------------------------

    async def _read_loop(self) -> None:
        loop = asyncio.get_running_loop()
        try:
            while self._process is not None:
                data = await loop.run_in_executor(None, self._blocking_read)
                if data is None:
                    logger.info("WinPTY read EOF")
                    break
                if data:
                    await self._ws.send_json({"type": "output", "data": data})
        except (asyncio.CancelledError, WebSocketDisconnect):
            pass
        except Exception as exc:
            logger.debug("WinPTY read loop ended: %s", exc)

    def _blocking_read(self) -> str | None:
        """Read available output from the PTY. Returns None on process exit."""
        if self._process is None:
            return None
        try:
            if not self._process.isalive():  # type: ignore[union-attr]
                # Drain remaining output
                try:
                    remaining = self._process.read(_READ_CHUNK)  # type: ignore[union-attr]
                    if remaining:
                        return remaining
                except Exception:
                    pass
                return None
            data = self._process.read(_READ_CHUNK, blocking=False)  # type: ignore[union-attr]
            if not data:
                # No data yet — small sleep to avoid busy-wait
                import time
                time.sleep(_POLL_INTERVAL)
                return ""
            return data
        except Exception:
            return None
