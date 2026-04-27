"""Windows PTY session implementation using pywinpty."""

import asyncio
import logging
import os
import signal

from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)

_POLL_INTERVAL = 0.05


def _default_shell() -> str:
    """Prefer PowerShell 7 (pwsh); fall back to Windows PowerShell 5.1, then cmd."""
    candidates = [
        r"C:\Program Files\PowerShell\7\pwsh.exe",
        r"C:\Program Files\PowerShell\7-preview\pwsh.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\PowerShell\7\pwsh.exe"),
        os.path.expandvars(r"%ProgramFiles%\PowerShell\7\pwsh.exe"),
    ]
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    ps = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                      "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
    if os.path.isfile(ps):
        return ps
    return os.environ.get("COMSPEC", "cmd.exe")


def _refresh_env_from_registry() -> dict[str, str]:
    """Merge latest Machine + User environment (esp. PATH) from registry into a copy of os.environ.

    Windows processes snapshot env at launch; installers that update PATH via registry
    don't propagate into already-running processes. Re-reading ensures the PTY child
    sees tools installed after this server started.
    """
    env = dict(os.environ)
    if os.name != "nt":
        return env
    try:
        import winreg  # type: ignore[import-not-found]
    except ImportError:
        return env

    def _read(root: int, subkey: str) -> dict[str, str]:
        out: dict[str, str] = {}
        try:
            with winreg.OpenKey(root, subkey) as key:
                i = 0
                while True:
                    try:
                        name, value, _ = winreg.EnumValue(key, i)
                    except OSError:
                        break
                    if isinstance(value, str):
                        out[name] = value
                    i += 1
        except OSError:
            pass
        return out

    machine = _read(winreg.HKEY_LOCAL_MACHINE,
                    r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment")
    user = _read(winreg.HKEY_CURRENT_USER, r"Environment")

    # Merge: user overrides machine, except PATH which concatenates (Windows behavior).
    merged: dict[str, str] = {**machine}
    for k, v in user.items():
        if k.upper() == "PATH":
            machine_path = machine.get("Path") or machine.get("PATH", "")
            merged_path = ";".join(p for p in (machine_path, v) if p)
            merged["Path"] = os.path.expandvars(merged_path)
        else:
            merged[k] = os.path.expandvars(v)

    # Expand any %VAR% references in machine-only keys too.
    for k, v in list(merged.items()):
        if "%" in v:
            merged[k] = os.path.expandvars(v)

    # Overlay onto current env (preserves process-local vars like VIRTUAL_ENV),
    # but force-refresh PATH from registry.
    for k, v in merged.items():
        if k.upper() == "PATH":
            env["PATH"] = v
            env["Path"] = v
        else:
            env.setdefault(k, v)
    return env


def _env_to_winpty_block(env: dict[str, str]) -> str:
    """pywinpty expects env as a single string of KEY=VALUE pairs joined by \\0, terminated by \\0."""
    return "\0".join(f"{k}={v}" for k, v in env.items()) + "\0"


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
        env = _refresh_env_from_registry()
        proc = PTY(self._cols, self._rows)
        try:
            proc.spawn(shell, env=_env_to_winpty_block(env))
        except TypeError:
            # Older pywinpty without env kwarg — fall back silently.
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
                pid = self._process.pid  # type: ignore[union-attr]
                if pid and self._process.isalive():  # type: ignore[union-attr]
                    os.kill(pid, signal.SIGTERM)
            except (OSError, ProcessLookupError):
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
                    remaining = self._process.read(blocking=False)  # type: ignore[union-attr]
                    if remaining:
                        return remaining
                except Exception:
                    pass
                return None
            data = self._process.read(blocking=False)  # type: ignore[union-attr]
            if not data:
                # No data yet — small sleep to avoid busy-wait
                import time
                time.sleep(_POLL_INTERVAL)
                return ""
            return data
        except Exception:
            return None
