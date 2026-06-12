"""Automation helpers for tmux-backed TUI sessions.

This module is intentionally below the master-agent tool layer and above
``tui_session_manager``. It owns transient automation state such as the
last captured tail per session so callers can ask "what changed since the
last capture?" without pushing full scrollback into the LLM every time.
"""

from __future__ import annotations

import asyncio
import os
import re
import shlex
import threading
import time
from dataclasses import asdict, dataclass
from typing import Protocol

from .tui_session_manager import TuiBridgeError, TuiSessionInfo, get_manager, tmux_available

_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_DEFAULT_TAIL_CHARS = 6000
_MAX_INPUT_CHARS = 12000
_MAX_CAPTURE_LINES = 5000
_KEY_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_DEFAULT_ALLOWED_COMMANDS = ("claude", "codex")

_KEY_ALIASES = {
    "enter": "Enter",
    "return": "Enter",
    "tab": "Tab",
    "backtab": "BTab",
    "btab": "BTab",
    "shift-tab": "BTab",
    "shift+tab": "BTab",
    "s-tab": "BTab",
    "esc": "Escape",
    "escape": "Escape",
    "up": "Up",
    "down": "Down",
    "left": "Left",
    "right": "Right",
    "home": "Home",
    "end": "End",
    "pageup": "PageUp",
    "pagedown": "PageDown",
    "backspace": "BSpace",
    "delete": "DC",
    "ctrl-c": "C-c",
    "ctrl-d": "C-d",
    "ctrl-l": "C-l",
}

_KEY_NAME_ALIASES = {
    "enter": "Enter",
    "return": "Enter",
    "tab": "Tab",
    "escape": "Escape",
    "esc": "Escape",
    "up": "Up",
    "down": "Down",
    "left": "Left",
    "right": "Right",
    "home": "Home",
    "end": "End",
    "pageup": "PageUp",
    "pagedown": "PageDown",
    "backspace": "BSpace",
    "delete": "DC",
}


class TuiManagerPort(Protocol):
    def list_sessions(self) -> list[TuiSessionInfo]: ...
    def create_session(self, *, label: str = "", cwd: str = "", command: str = "") -> TuiSessionInfo: ...
    def send_literal(self, session_id: str, text: str) -> None: ...
    def send_key(self, session_id: str, key: str) -> None: ...
    def capture_pane(self, session_id: str, *, history_lines: int = 200) -> str: ...


@dataclass(frozen=True, slots=True)
class TuiCapture:
    session_id: str
    text: str
    tail_text: str
    delta_text: str
    delta_matched_previous_tail: bool
    captured_at: float
    stable: bool = False
    stable_for_sec: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


class TuiAutomationService:
    """High-level TUI automation façade used by master-agent tools."""

    def __init__(
        self,
        manager: TuiManagerPort | None = None,
        *,
        remember_tail_chars: int = _DEFAULT_TAIL_CHARS,
    ) -> None:
        self._manager = manager or get_manager()
        self._remember_tail_chars = max(500, min(int(remember_tail_chars), 50000))
        self._last_tail_by_session: dict[str, str] = {}
        self._captured_sessions: set[str] = set()
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return tui_automation_enabled()

    def list_sessions(self) -> list[TuiSessionInfo]:
        self._ensure_ready()
        return self._manager.list_sessions()

    def create_session(self, *, label: str = "", cwd: str = "", command: str = "") -> TuiSessionInfo:
        self._ensure_ready()
        ensure_command_allowed(command)
        return self._manager.create_session(label=label, cwd=cwd, command=command)

    def send_input(self, session_id: str, text: str, *, submit: bool = False) -> None:
        self._ensure_ready()
        if len(text) > _MAX_INPUT_CHARS:
            raise TuiBridgeError(f"input too long; max {_MAX_INPUT_CHARS} chars")
        if text:
            self._manager.send_literal(session_id, text)
        if submit:
            self.send_key(session_id, "Enter")

    def send_key(self, session_id: str, key: str) -> None:
        self._ensure_ready()
        normalized = normalize_key(key)
        self._manager.send_key(session_id, normalized)

    def capture_screen(
        self,
        session_id: str,
        *,
        history_lines: int = 200,
        update_tail: bool = True,
    ) -> TuiCapture:
        self._ensure_ready()
        bounded_lines = max(0, min(int(history_lines), _MAX_CAPTURE_LINES))
        raw = self._manager.capture_pane(session_id, history_lines=bounded_lines)
        text = normalize_terminal_text(raw)
        with self._lock:
            has_previous_capture = session_id in self._captured_sessions
            previous_tail = self._last_tail_by_session.get(session_id, "")
            if has_previous_capture:
                delta, matched = compute_delta(previous_tail, text)
            else:
                delta, matched = "", False
            tail = text[-self._remember_tail_chars :]
            if update_tail:
                self._captured_sessions.add(session_id)
                self._last_tail_by_session[session_id] = tail
        return TuiCapture(
            session_id=session_id,
            text=text,
            tail_text=tail,
            delta_text=delta,
            delta_matched_previous_tail=matched,
            captured_at=time.time(),
        )

    async def wait_until_stable(
        self,
        session_id: str,
        *,
        timeout_sec: float = 20.0,
        stable_for_sec: float = 1.5,
        min_wait_sec: float = 1.0,
        require_non_empty: bool = True,
        poll_interval_sec: float = 0.5,
        history_lines: int = 200,
    ) -> TuiCapture:
        self._ensure_ready()
        started_at = time.monotonic()
        deadline = time.monotonic() + max(0.1, float(timeout_sec))
        stable_target = max(0.2, float(stable_for_sec))
        min_wait = max(0.0, min(float(min_wait_sec), float(timeout_sec)))
        interval = max(0.1, float(poll_interval_sec))
        last_text: str | None = None
        unchanged_since = time.monotonic()
        latest = self.capture_screen(session_id, history_lines=history_lines, update_tail=False)

        while time.monotonic() < deadline:
            latest = self.capture_screen(session_id, history_lines=history_lines, update_tail=False)
            now = time.monotonic()
            if latest.text == last_text:
                waited_long_enough = now - started_at >= min_wait
                has_required_content = bool(latest.text.strip()) or not require_non_empty
                if (
                    now - unchanged_since >= stable_target
                    and waited_long_enough
                    and has_required_content
                ):
                    final = self.capture_screen(session_id, history_lines=history_lines, update_tail=True)
                    return TuiCapture(
                        **{
                            **final.to_dict(),
                            "stable": True,
                            "stable_for_sec": now - unchanged_since,
                        }
                    )
            else:
                last_text = latest.text
                unchanged_since = now
            await asyncio.sleep(interval)

        final = self.capture_screen(session_id, history_lines=history_lines, update_tail=True)
        return TuiCapture(
            **{
                **final.to_dict(),
                "stable": False,
                "stable_for_sec": max(0.0, time.monotonic() - unchanged_since),
            }
        )

    def reset_session_tail(self, session_id: str) -> None:
        with self._lock:
            self._last_tail_by_session.pop(session_id, None)
            self._captured_sessions.discard(session_id)

    def _ensure_ready(self) -> None:
        if not self.enabled:
            raise TuiBridgeError(
                "TUI automation is disabled; set MASTER_AGENT_TUI_TOOLS_ENABLED=true "
                "or TUI_BRIDGE_ENABLED=true"
            )
        if not tmux_available():
            raise TuiBridgeError("tmux is not installed on this host")


def tui_automation_enabled() -> bool:
    value = os.getenv("MASTER_AGENT_TUI_TOOLS_ENABLED", "").strip().lower()
    if not value:
        value = os.getenv("TUI_BRIDGE_ENABLED", "false").strip().lower()
    return value in {"1", "true", "yes", "on"}


def any_command_allowed() -> bool:
    value = os.getenv("MASTER_AGENT_TUI_ALLOW_ANY_COMMAND", "false").strip().lower()
    return value in {"1", "true", "yes", "on"}


def allowed_commands() -> tuple[str, ...]:
    raw = os.getenv("MASTER_AGENT_TUI_ALLOWED_COMMANDS", "").strip()
    if not raw:
        return _DEFAULT_ALLOWED_COMMANDS
    values = tuple(
        item.strip().lower()
        for item in raw.split(",")
        if item.strip()
    )
    return values or _DEFAULT_ALLOWED_COMMANDS


def ensure_command_allowed(command: str) -> None:
    if any_command_allowed():
        return
    base = command_base_name(command)
    allowed = allowed_commands()
    if not base:
        raise TuiBridgeError(
            "empty command would start the default shell; TUI automation only allows "
            f"{', '.join(allowed)} by default. Set MASTER_AGENT_TUI_ALLOWED_COMMANDS "
            "or MASTER_AGENT_TUI_ALLOW_ANY_COMMAND=true to override."
        )
    if base.lower() not in allowed:
        raise TuiBridgeError(
            f"TUI command '{base}' is not allowed; allowed commands: "
            f"{', '.join(allowed)}. Set MASTER_AGENT_TUI_ALLOWED_COMMANDS or "
            "MASTER_AGENT_TUI_ALLOW_ANY_COMMAND=true to override."
        )


def command_base_name(command: str) -> str:
    text = (command or "").strip()
    if not text:
        return ""
    try:
        parts = shlex.split(text, posix=(os.name != "nt"))
    except ValueError:
        parts = text.split()
    if not parts:
        return ""
    executable = parts[0].strip().strip('"').strip("'")
    name = os.path.basename(executable)
    if name.lower().endswith(".exe"):
        name = name[:-4]
    return name.lower()


def normalize_terminal_text(raw: str) -> str:
    text = _ANSI_RE.sub("", raw or "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in text.split("\n")]
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)


def normalize_key(key: str) -> str:
    text = (key or "").strip()
    if not text:
        raise TuiBridgeError("key is required")
    lowered = text.lower()
    alias = _KEY_ALIASES.get(lowered)
    if alias:
        return alias
    combo = _normalize_combo_key(lowered)
    if combo:
        return combo
    if not _KEY_RE.match(text):
        raise TuiBridgeError(f"unsupported key name: {key}")
    return text


def _normalize_combo_key(lowered: str) -> str:
    if "+" not in lowered and "-" not in lowered:
        return ""
    separator = "+" if "+" in lowered else "-"
    parts = [part for part in lowered.split(separator) if part]
    if len(parts) < 2:
        return ""
    key = parts[-1]
    modifiers = parts[:-1]
    if "shift" in modifiers and key == "tab":
        return "BTab"
    prefix: list[str] = []
    for modifier in modifiers:
        if modifier in {"ctrl", "control", "c"}:
            prefix.append("C")
        elif modifier in {"alt", "meta", "m"}:
            prefix.append("M")
        elif modifier in {"shift", "s"}:
            prefix.append("S")
        else:
            return ""
    key_name = _KEY_NAME_ALIASES.get(key, key)
    if len(key_name) == 1:
        key_name = key_name.lower()
    return "-".join([*prefix, key_name])


def compute_delta(previous_tail: str, current_text: str) -> tuple[str, bool]:
    """Return new text after the previous captured tail.

    The previous value is only a tail, not a full transcript. If that tail
    appears in the new capture, everything after its last occurrence is new.
    If tmux scrollback moved and only a suffix still overlaps, use the
    longest suffix/prefix overlap as a fallback.
    """
    previous = previous_tail or ""
    current = current_text or ""
    if not previous:
        return current, False
    index = current.rfind(previous)
    if index >= 0:
        return current[index + len(previous) :].lstrip("\n"), True
    overlap = _suffix_prefix_overlap(previous, current)
    if overlap > 0:
        return current[overlap:].lstrip("\n"), True
    return current, False


def _suffix_prefix_overlap(previous: str, current: str) -> int:
    max_len = min(len(previous), len(current))
    for size in range(max_len, 0, -1):
        if previous[-size:] == current[:size]:
            return size
    return 0


_automation: TuiAutomationService | None = None


def get_tui_automation() -> TuiAutomationService:
    global _automation
    if _automation is None:
        _automation = TuiAutomationService()
    return _automation


__all__ = [
    "TuiAutomationService",
    "TuiCapture",
    "allowed_commands",
    "any_command_allowed",
    "command_base_name",
    "compute_delta",
    "ensure_command_allowed",
    "get_tui_automation",
    "normalize_key",
    "normalize_terminal_text",
    "tui_automation_enabled",
]
