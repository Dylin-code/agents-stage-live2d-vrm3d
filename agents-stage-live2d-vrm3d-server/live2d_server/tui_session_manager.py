"""TUI Session Manager — tmux-backed long-lived sessions for the TUI bridge.

Each TUI session corresponds to one tmux session named ``tui-<uuid8>``.
tmux owns the *process* lifecycle (survives WebSocket disconnects, server
restarts, and detach/attach cycles). This module owns the *metadata*
sidecar that tmux does not track (label, command, created_at) and exposes
a small CRUD surface used by ``tui_bridge_api``.

Design notes
------------
* tmux is the single source of truth for *which sessions exist*. The
  sidecar JSON is reconciled against ``tmux list-sessions`` on every read,
  so orphaned metadata entries are silently dropped.
* Session ids are generated server-side as ``tui-<uuid8>``; we never trust
  user input as a tmux target name.
* All shell-out goes through ``subprocess.run`` with an arg list — never a
  shell string — to keep injection-free guarantees.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_SESSION_PREFIX = "tui-"
_SESSION_ID_RE = re.compile(r"^tui-[a-f0-9]{8}$")
_TMUX_TIMEOUT_SEC = 5.0
_DEFAULT_MAX_SESSIONS = 8


def _max_sessions() -> int:
    try:
        return max(1, int(os.getenv("TUI_BRIDGE_MAX_SESSIONS", str(_DEFAULT_MAX_SESSIONS))))
    except ValueError:
        return _DEFAULT_MAX_SESSIONS


def _default_command() -> str:
    """Default command to run when the user does not specify one.

    Empty / unset → spawn an interactive login shell (matches what xterm does).
    """
    cmd = os.getenv("TUI_BRIDGE_DEFAULT_CMD", "").strip()
    if cmd:
        return cmd
    shell = os.environ.get("SHELL", "/bin/bash")
    return f"{shell} -l"


def _sidecar_path() -> Path:
    """Location of the metadata sidecar file."""
    override = os.getenv("TUI_BRIDGE_METADATA_PATH", "").strip()
    if override:
        return Path(override).expanduser()
    base = Path.home() / ".cache" / "agents-stage-live2d-vrm3d"
    return base / "tui-sessions.json"


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class TuiSessionInfo:
    session_id: str
    label: str
    cwd: str
    command: str
    created_at: float
    attached_clients: int = 0
    windows: int = 1
    last_activity_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# tmux shell-out helpers
# ---------------------------------------------------------------------------

def tmux_available() -> bool:
    return shutil.which("tmux") is not None


def _run_tmux(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    """Run a tmux command with safe defaults and a short timeout."""
    if not tmux_available():
        raise TuiBridgeError("tmux is not installed on this host")
    cmd = ["tmux", *args]
    try:
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=_TMUX_TIMEOUT_SEC,
            check=check,
        )
    except subprocess.CalledProcessError as exc:
        logger.debug("tmux %s failed: rc=%s stderr=%s", args, exc.returncode, exc.stderr)
        raise TuiBridgeError(f"tmux {' '.join(args)} failed: {exc.stderr.strip()}") from exc
    except subprocess.TimeoutExpired as exc:
        raise TuiBridgeError(f"tmux {' '.join(args)} timed out") from exc


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class TuiBridgeError(RuntimeError):
    """Raised by manager operations the caller is expected to handle."""


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------

class TuiSessionManager:
    """CRUD façade over tmux + a metadata sidecar file."""

    def __init__(self, sidecar_path: Path | None = None) -> None:
        self._sidecar = sidecar_path or _sidecar_path()
        self._lock = threading.Lock()

    # -- Public API -----------------------------------------------------

    def list_sessions(self) -> list[TuiSessionInfo]:
        """Return all live tui-* tmux sessions, hydrated with sidecar metadata."""
        live = self._list_tmux_sessions()
        if not live:
            self._prune_sidecar(set())
            return []

        live_ids = {s["session_id"] for s in live}
        meta = self._read_sidecar()
        # Drop entries whose tmux session no longer exists.
        meta = {sid: row for sid, row in meta.items() if sid in live_ids}
        self._write_sidecar(meta)

        results: list[TuiSessionInfo] = []
        for row in live:
            sid = row["session_id"]
            saved = meta.get(sid, {})
            results.append(
                TuiSessionInfo(
                    session_id=sid,
                    label=str(saved.get("label") or row["session_id"]),
                    cwd=str(saved.get("cwd") or row.get("cwd") or ""),
                    command=str(saved.get("command") or ""),
                    created_at=float(saved.get("created_at") or row.get("created_at") or time.time()),
                    attached_clients=int(row.get("attached_clients", 0)),
                    windows=int(row.get("windows", 1)),
                    last_activity_at=float(saved.get("last_activity_at") or row.get("created_at") or time.time()),
                )
            )
        # Newest first.
        results.sort(key=lambda info: info.created_at, reverse=True)
        return results

    def has_session(self, session_id: str) -> bool:
        if not _SESSION_ID_RE.match(session_id):
            return False
        result = _run_tmux(["has-session", "-t", session_id], check=False)
        return result.returncode == 0

    def create_session(
        self,
        *,
        label: str = "",
        cwd: str = "",
        command: str = "",
    ) -> TuiSessionInfo:
        """Create a fresh tmux session and persist metadata."""
        with self._lock:
            existing = self.list_sessions()
            if len(existing) >= _max_sessions():
                raise TuiBridgeError(
                    f"Reached max TUI sessions ({_max_sessions()}); kill one first"
                )

            session_id = self._mint_session_id()
            resolved_cwd = (cwd or "").strip() or os.path.expanduser("~")
            if not Path(resolved_cwd).is_dir():
                raise TuiBridgeError(f"cwd does not exist: {resolved_cwd}")

            resolved_command = (command or "").strip() or _default_command()

            args = [
                "new-session",
                "-d",                  # detached — we attach via PTY later
                "-s", session_id,
                "-c", resolved_cwd,
                resolved_command,
            ]
            _run_tmux(args)

            info = TuiSessionInfo(
                session_id=session_id,
                label=label.strip() or f"TUI {session_id[len(_SESSION_PREFIX):]}",
                cwd=resolved_cwd,
                command=resolved_command,
                created_at=time.time(),
                attached_clients=0,
                windows=1,
            )
            self._upsert_sidecar(info)
            logger.info("tui-bridge: created session %s (%s)", session_id, info.label)
            return info

    def kill_session(self, session_id: str) -> bool:
        if not _SESSION_ID_RE.match(session_id):
            raise TuiBridgeError(f"invalid session id: {session_id}")
        result = _run_tmux(["kill-session", "-t", session_id], check=False)
        ok = result.returncode == 0
        if ok:
            meta = self._read_sidecar()
            meta.pop(session_id, None)
            self._write_sidecar(meta)
            logger.info("tui-bridge: killed session %s", session_id)
        return ok

    def touch(self, session_id: str) -> None:
        """Update last_activity_at for a session (best-effort, no error if missing)."""
        if not _SESSION_ID_RE.match(session_id):
            return
        meta = self._read_sidecar()
        row = meta.get(session_id)
        if not row:
            return
        row["last_activity_at"] = time.time()
        meta[session_id] = row
        self._write_sidecar(meta)

    # -- Internal helpers ----------------------------------------------

    @staticmethod
    def _mint_session_id() -> str:
        return f"{_SESSION_PREFIX}{uuid.uuid4().hex[:8]}"

    @staticmethod
    def _list_tmux_sessions() -> list[dict]:
        """Return raw tmux list-sessions filtered to ``tui-*`` entries."""
        if not tmux_available():
            return []
        # Format: name|created|attached|windows|pane_path
        fmt = "#{session_name}|#{session_created}|#{session_attached}|#{session_windows}|#{pane_current_path}"
        try:
            result = _run_tmux(["list-sessions", "-F", fmt], check=False)
        except TuiBridgeError:
            return []
        # tmux returns rc=1 with empty stderr when no sessions exist.
        if result.returncode != 0 and "no server running" not in (result.stderr or "").lower():
            if result.stderr.strip() and "no server" not in result.stderr.lower():
                logger.debug("tmux list-sessions stderr: %s", result.stderr.strip())
        rows: list[dict] = []
        for raw in (result.stdout or "").splitlines():
            parts = raw.split("|")
            if len(parts) < 4:
                continue
            name = parts[0].strip()
            if not _SESSION_ID_RE.match(name):
                continue
            try:
                created = float(parts[1])
            except (ValueError, IndexError):
                created = time.time()
            try:
                attached = int(parts[2])
            except (ValueError, IndexError):
                attached = 0
            try:
                windows = int(parts[3])
            except (ValueError, IndexError):
                windows = 1
            cwd = parts[4].strip() if len(parts) > 4 else ""
            rows.append(
                {
                    "session_id": name,
                    "created_at": created,
                    "attached_clients": attached,
                    "windows": windows,
                    "cwd": cwd,
                }
            )
        return rows

    # -- Sidecar persistence -------------------------------------------

    def _read_sidecar(self) -> dict[str, dict]:
        try:
            raw = self._sidecar.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        except OSError as exc:
            logger.warning("tui-bridge: failed to read sidecar: %s", exc)
            return {}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.warning("tui-bridge: corrupt sidecar, ignoring: %s", exc)
            return {}
        if not isinstance(data, dict):
            return {}
        return {
            str(k): v for k, v in data.items() if isinstance(v, dict) and _SESSION_ID_RE.match(str(k))
        }

    def _write_sidecar(self, meta: dict[str, dict]) -> None:
        try:
            self._sidecar.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._sidecar.with_suffix(self._sidecar.suffix + ".tmp")
            tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self._sidecar)
        except OSError as exc:
            logger.warning("tui-bridge: failed to write sidecar: %s", exc)

    def _upsert_sidecar(self, info: TuiSessionInfo) -> None:
        meta = self._read_sidecar()
        meta[info.session_id] = info.to_dict()
        self._write_sidecar(meta)

    def _prune_sidecar(self, keep: set[str]) -> None:
        meta = self._read_sidecar()
        if not meta:
            return
        pruned = {sid: row for sid, row in meta.items() if sid in keep}
        if pruned != meta:
            self._write_sidecar(pruned)


# ---------------------------------------------------------------------------
# Module-level singleton (cheap; manager has no per-instance state worth scoping)
# ---------------------------------------------------------------------------

_manager: TuiSessionManager | None = None


def get_manager() -> TuiSessionManager:
    global _manager
    if _manager is None:
        _manager = TuiSessionManager()
    return _manager


# Convenience re-exports for ``tui_bridge.py`` to use.
__all__ = [
    "TuiBridgeError",
    "TuiSessionInfo",
    "TuiSessionManager",
    "get_manager",
    "tmux_available",
]
