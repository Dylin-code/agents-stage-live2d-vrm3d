"""Windows TUI bridge session — pywinpty + ``tmux attach`` over WebSocket.

Mirrors :class:`UnixTuiBridgeSession` but uses ConPTY (via pywinpty)
because Windows doesn't expose ``pty.fork``/``fcntl``/``termios``.

Server-side dependency is the ``tmux`` binary on ``PATH`` — on Windows we
expect psmux, which installs a ``tmux.exe`` alias and supports the
subset of CLI we rely on (``new-session -d``, ``attach-session -d -t``,
``list-sessions -F``, ``has-session``, ``kill-session``). Verified
against psmux 3.3.3.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import time

from fastapi import WebSocket, WebSocketDisconnect

from .tui_session_manager import TuiSessionInfo, get_manager

logger = logging.getLogger(__name__)

_POLL_INTERVAL = 0.03


class WinTuiBridgeSession:
    """One ConPTY running ``tmux attach`` for the given tui session id."""

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
        self._cols = max(20, cols)
        self._rows = max(5, rows)
        self._process: object | None = None  # winpty.PTY
        self._reader_task: asyncio.Task[None] | None = None

    @property
    def session_id(self) -> str:
        return self._info.session_id

    async def start(self) -> None:
        try:
            from winpty import PTY  # type: ignore[import-untyped]
        except ImportError as exc:
            raise RuntimeError(
                "pywinpty is required on Windows. Install with: uv add pywinpty"
            ) from exc

        # Locate tmux/psmux. We need an absolute path because pywinpty's
        # spawn() does not search PATH the way os.execvpe does. Reuse the
        # manager's resolver so we also pick up WinGet install dirs when
        # PATH is trimmed by Make / VS Code terminal / launcher scripts.
        from .tui_session_manager import _resolve_tmux_path
        tmux_exe = _resolve_tmux_path()
        if not tmux_exe:
            raise RuntimeError("tmux/psmux not found on PATH")

        # NOTE on argv: in pywinpty 3.0.3 the combination of ``appname`` +
        # ``cmdline`` ends up duplicating the appname into argv[1] (psmux
        # then sees "unknown command: <path>"). Pass the whole thing as a
        # single ``appname`` string; pywinpty's fallback CreateProcessW
        # path handles the quoted-exe + args form correctly.
        argline = f'"{tmux_exe}" attach-session -d -t {self._info.session_id}'

        # Forward the parent env explicitly. With env=None, pywinpty 3.x
        # under certain launcher chains produces a child env that psmux
        # can't use to locate its server pipe — manifests as
        # "psmux: can't find session ... (no server running)" immediately
        # after tmux attach enters alt-screen, which also tears down the
        # psmux server.
        env = dict(os.environ)
        # psmux references HOME for server-pipe discovery. Git Bash sets it
        # automatically; PowerShell / cmd launches via Make may not. Fall
        # back to USERPROFILE to keep the env consistent across shells.
        if "HOME" not in env and "USERPROFILE" in env:
            env["HOME"] = env["USERPROFILE"]
        env_block = "\0".join(f"{k}={v}" for k, v in env.items()) + "\0"

        proc = PTY(self._cols, self._rows)
        try:
            proc.spawn(argline, env=env_block)
        except TypeError:
            logger.warning("tui-bridge[win]: pywinpty.spawn does not accept "
                           "env kwarg; psmux attach may fail to find server")
            proc.spawn(argline)

        self._process = proc
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
            "tui-bridge[win]: attached pid=%s cols=%d rows=%d session=%s",
            getattr(proc, "pid", "?"), self._cols, self._rows, self._info.session_id,
        )

    async def write(self, data: str) -> None:
        if self._process is None or not data:
            return
        try:
            self._process.write(data)  # type: ignore[union-attr]
        except Exception as exc:  # noqa: BLE001
            logger.warning("tui-bridge[win]: write error: %s", exc)

    def resize(self, cols: int, rows: int) -> None:
        self._cols = max(20, cols)
        self._rows = max(5, rows)
        if self._process is None:
            return
        try:
            self._process.set_size(self._cols, self._rows)  # type: ignore[union-attr]
        except Exception as exc:  # noqa: BLE001
            logger.warning("tui-bridge[win]: resize error: %s", exc)

    async def stop(self) -> None:
        """Detach the client process; tmux server / session live on."""
        if self._reader_task and not self._reader_task.done():
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass

        proc = self._process
        if proc is not None:
            try:
                pid = getattr(proc, "pid", None)
                if pid and proc.isalive():  # type: ignore[union-attr]
                    # Polite shutdown — the tmux client process will detach
                    # cleanly on SIGTERM (mapped to TerminateProcess on Win).
                    try:
                        os.kill(pid, signal.SIGTERM)
                    except (OSError, ProcessLookupError):
                        pass
            except Exception as exc:  # noqa: BLE001
                logger.debug("tui-bridge[win]: stop probe failed: %s", exc)
            self._process = None

        try:
            get_manager().touch(self._info.session_id)
        except Exception:  # noqa: BLE001
            pass
        logger.info("tui-bridge[win]: detached session=%s", self._info.session_id)

    # ------------------------------------------------------------------

    async def _read_loop(self) -> None:
        loop = asyncio.get_running_loop()
        captured: list[str] = []  # diagnostic — first ~2KB before EOF
        try:
            while self._process is not None:
                data = await loop.run_in_executor(None, self._blocking_read)
                if data is None:
                    blob = "".join(captured)[:2048]
                    logger.info(
                        "tui-bridge[win]: PTY EOF (tmux attach exited); session=%s remains alive. "
                        "last_bytes=%r",
                        self._info.session_id, blob.replace("\x1b", "\\e"),
                    )
                    try:
                        await self._ws.send_json({"type": "detached"})
                    except Exception:  # noqa: BLE001
                        pass
                    break
                if data:
                    if sum(len(c) for c in captured) < 2048:
                        captured.append(data)
                    await self._ws.send_json({"type": "output", "data": data})
        except (asyncio.CancelledError, WebSocketDisconnect):
            pass
        except Exception as exc:  # noqa: BLE001
            logger.debug("tui-bridge[win]: read loop ended: %s", exc)

    def _blocking_read(self) -> str | None:
        """Non-blocking poll into pywinpty; returns ``None`` once the client exits."""
        proc = self._process
        if proc is None:
            return None
        try:
            alive = proc.isalive()  # type: ignore[union-attr]
            try:
                chunk = proc.read(blocking=False)  # type: ignore[union-attr]
            except Exception:
                chunk = ""
            if chunk:
                return chunk
            if not alive:
                # Drain any tail buffered after exit before signaling EOF.
                try:
                    tail = proc.read(blocking=False)  # type: ignore[union-attr]
                except Exception:
                    tail = ""
                return tail or None
            # Nothing to read yet — avoid busy spinning the executor.
            time.sleep(_POLL_INTERVAL)
            return ""
        except Exception:  # noqa: BLE001
            return None
