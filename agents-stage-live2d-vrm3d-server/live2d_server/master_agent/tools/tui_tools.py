"""Master-agent tools for tmux-backed TUI automation."""

from __future__ import annotations

import logging
from typing import Any

from ...tui_automation import get_tui_automation
from ...tui_session_manager import TuiBridgeError, TuiSessionInfo
from ..contracts.tool_port import ToolContext, ToolPort, ToolResult

_LOGGER = logging.getLogger(__name__)


def _automation(ctx: ToolContext):
    service = getattr(ctx.services, "tui_automation", None)
    return service or get_tui_automation()


def _optional_str(value: Any) -> str:
    return str(value or "").strip()


def _optional_int(value: Any, default: int, *, minimum: int, maximum: int) -> int:
    try:
        number = int(value) if value is not None else default
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(number, maximum))


def _optional_float(value: Any, default: float, *, minimum: float, maximum: float) -> float:
    try:
        number = float(value) if value is not None else default
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(number, maximum))


def _session_data(info: TuiSessionInfo) -> dict[str, Any]:
    return info.to_dict()


class TuiNewSessionTool(ToolPort):
    name = "tui_new_session"
    description = (
        "Create a tmux-backed interactive TUI session for tools such as "
        "claude or codex when their terminal UI must be operated directly. "
        "Use normal codex/claude send_prompt tools for non-interactive work."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "cwd": {"type": "string", "description": "Absolute working directory path"},
            "command": {
                "type": "string",
                "description": "Command to run, e.g. 'claude' or 'codex'. Empty uses shell default.",
            },
            "label": {"type": "string", "description": "Human-readable session label"},
        },
        "required": ["cwd"],
    }

    async def invoke(self, ctx: ToolContext) -> ToolResult:
        cwd = _optional_str(ctx.arguments.get("cwd") or ctx.default_cwd)
        if not cwd:
            return ToolResult.failure("cwd is required")
        command = _optional_str(ctx.arguments.get("command"))
        label = _optional_str(ctx.arguments.get("label"))
        try:
            info = _automation(ctx).create_session(label=label, cwd=cwd, command=command)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.exception("tui_new_session failed cwd=%s command=%s", cwd, command)
            return ToolResult.failure(f"tui_new_session failed: {exc}")
        return ToolResult.success(
            output_text=f"created TUI session {info.session_id} in {info.cwd}",
            data={"session": _session_data(info), "session_id": info.session_id},
        )


class TuiSendInputTool(ToolPort):
    name = "tui_send_input"
    description = (
        "Send literal text to an existing TUI session. Set submit=true to "
        "press Enter after the text. Follow with tui_wait_for_stable or "
        "tui_capture_screen before deciding the next step."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "session_id": {"type": "string", "description": "TUI session id from tui_new_session"},
            "text": {"type": "string", "description": "Literal text to send"},
            "submit": {"type": "boolean", "description": "Press Enter after sending text"},
        },
        "required": ["session_id", "text"],
    }

    async def invoke(self, ctx: ToolContext) -> ToolResult:
        session_id = _optional_str(ctx.arguments.get("session_id"))
        text = str(ctx.arguments.get("text") or "")
        submit = bool(ctx.arguments.get("submit"))
        if not session_id:
            return ToolResult.failure("session_id is required")
        try:
            _automation(ctx).send_input(session_id, text, submit=submit)
        except Exception as exc:  # noqa: BLE001
            return ToolResult.failure(f"tui_send_input failed: {exc}")
        return ToolResult.success(
            output_text=(
                f"sent {len(text)} char(s) to {session_id}"
                + (" and pressed Enter" if submit else "")
            ),
            data={"session_id": session_id, "chars": len(text), "submitted": submit},
        )


class TuiSendKeyTool(ToolPort):
    name = "tui_send_key"
    description = (
        "Send a single navigation/control key to a TUI session, e.g. "
        "Enter, Tab, Escape, Up, Down, Left, Right, PageUp, PageDown, "
        "Backspace, Delete, C-c, C-d, C-l."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "session_id": {"type": "string"},
            "key": {"type": "string", "description": "tmux key name or supported alias"},
        },
        "required": ["session_id", "key"],
    }

    async def invoke(self, ctx: ToolContext) -> ToolResult:
        session_id = _optional_str(ctx.arguments.get("session_id"))
        key = _optional_str(ctx.arguments.get("key"))
        if not session_id:
            return ToolResult.failure("session_id is required")
        if not key:
            return ToolResult.failure("key is required")
        try:
            _automation(ctx).send_key(session_id, key)
        except Exception as exc:  # noqa: BLE001
            return ToolResult.failure(f"tui_send_key failed: {exc}")
        return ToolResult.success(
            output_text=f"sent key {key} to {session_id}",
            data={"session_id": session_id, "key": key},
        )


class TuiCaptureScreenTool(ToolPort):
    name = "tui_capture_screen"
    description = (
        "Capture a TUI session's current pane text plus bounded scrollback. "
        "Returns text, tail_text, and delta_text since the previous capture "
        "for the same session."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "session_id": {"type": "string"},
            "history_lines": {
                "type": "integer",
                "description": "Scrollback lines to include (default 200, max 5000)",
            },
        },
        "required": ["session_id"],
    }

    async def invoke(self, ctx: ToolContext) -> ToolResult:
        session_id = _optional_str(ctx.arguments.get("session_id"))
        if not session_id:
            return ToolResult.failure("session_id is required")
        history_lines = _optional_int(
            ctx.arguments.get("history_lines"), 200, minimum=0, maximum=5000,
        )
        try:
            capture = _automation(ctx).capture_screen(
                session_id, history_lines=history_lines,
            )
        except TuiBridgeError as exc:
            return ToolResult.failure(f"tui_capture_screen failed: {exc}")
        except Exception as exc:  # noqa: BLE001
            _LOGGER.exception("tui_capture_screen failed")
            return ToolResult.failure(f"tui_capture_screen failed: {exc}")
        delta_hint = (
            f", delta={len(capture.delta_text)} chars"
            if capture.delta_text else ", no new delta"
        )
        return ToolResult.success(
            output_text=f"captured {len(capture.text)} chars from {session_id}{delta_hint}",
            data=capture.to_dict(),
        )


class TuiWaitForStableTool(ToolPort):
    name = "tui_wait_for_stable"
    description = (
        "Poll a TUI session until the captured screen stops changing, then "
        "return text, tail_text, and delta_text since the previous capture. "
        "Use after sending input before interpreting the result."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "session_id": {"type": "string"},
            "timeout_sec": {"type": "number", "description": "Default 20, max 120"},
            "stable_for_sec": {"type": "number", "description": "Default 1.5"},
            "min_wait_sec": {
                "type": "number",
                "description": "Minimum time to keep polling before declaring stable (default 1.0)",
            },
            "require_non_empty": {
                "type": "boolean",
                "description": "If true (default), do not declare stable while captured text is empty",
            },
            "poll_interval_sec": {"type": "number", "description": "Default 0.5"},
            "history_lines": {"type": "integer", "description": "Default 200, max 5000"},
        },
        "required": ["session_id"],
    }

    async def invoke(self, ctx: ToolContext) -> ToolResult:
        session_id = _optional_str(ctx.arguments.get("session_id"))
        if not session_id:
            return ToolResult.failure("session_id is required")
        try:
            capture = await _automation(ctx).wait_until_stable(
                session_id,
                timeout_sec=_optional_float(
                    ctx.arguments.get("timeout_sec"), 20.0, minimum=0.1, maximum=120.0,
                ),
                stable_for_sec=_optional_float(
                    ctx.arguments.get("stable_for_sec"), 1.5, minimum=0.2, maximum=20.0,
                ),
                min_wait_sec=_optional_float(
                    ctx.arguments.get("min_wait_sec"), 1.0, minimum=0.0, maximum=30.0,
                ),
                require_non_empty=bool(ctx.arguments.get("require_non_empty", True)),
                poll_interval_sec=_optional_float(
                    ctx.arguments.get("poll_interval_sec"), 0.5, minimum=0.1, maximum=5.0,
                ),
                history_lines=_optional_int(
                    ctx.arguments.get("history_lines"), 200, minimum=0, maximum=5000,
                ),
            )
        except Exception as exc:  # noqa: BLE001
            _LOGGER.exception("tui_wait_for_stable failed")
            return ToolResult.failure(f"tui_wait_for_stable failed: {exc}")
        status = "stable" if capture.stable else "timeout"
        return ToolResult.success(
            output_text=(
                f"TUI screen {status} for {session_id}; "
                f"captured {len(capture.text)} chars, delta={len(capture.delta_text)} chars"
            ),
            data=capture.to_dict(),
        )


__all__ = [
    "TuiCaptureScreenTool",
    "TuiNewSessionTool",
    "TuiSendInputTool",
    "TuiSendKeyTool",
    "TuiWaitForStableTool",
]
