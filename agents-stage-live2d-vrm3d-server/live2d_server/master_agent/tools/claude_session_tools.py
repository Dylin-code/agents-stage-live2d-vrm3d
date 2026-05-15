"""Claude tools — mirror codex_session_tools using the same internal helper."""

from __future__ import annotations

from typing import Any

from ..contracts.tool_port import ToolContext, ToolPort, ToolResult
from .codex_session_tools import (
    _SESSION_PARAM_SCHEMA,
    _optional_bool,
    _optional_str,
    _resolve_default_permission_mode,
    _send_prompt_impl,
)

_BRAND = "claude"


class ClaudeNewSessionTool(ToolPort):
    name = "claude_new_session"
    description = (
        "Create a new Claude Code CLI session in a given working directory. "
        "Returns session_id; use it with claude_send_prompt. Model + "
        "reasoning_effort + permission_mode set here become the session "
        "defaults but can still be overridden per send_prompt call."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "cwd": {"type": "string", "description": "Absolute working directory path"},
            **_SESSION_PARAM_SCHEMA,
        },
        "required": ["cwd"],
    }

    async def invoke(self, ctx: ToolContext) -> ToolResult:
        args = ctx.arguments
        cwd = str(args.get("cwd") or ctx.default_cwd or "").strip()
        if not cwd:
            return ToolResult.failure("cwd is required")
        service = ctx.services.agent_provider.get_chat_service(_BRAND)
        permission_mode = _resolve_default_permission_mode(
            ctx, _BRAND, args.get("permission_mode"),
        )
        try:
            payload: dict[str, Any] = await service.create_session(
                cwd=cwd,
                model=_optional_str(args.get("model")),
                reasoning_effort=_optional_str(args.get("reasoning_effort")),
                permission_mode=permission_mode,
                plan_mode=_optional_bool(args.get("plan_mode")),
            )
        except Exception as exc:
            return ToolResult.failure(f"claude create_session failed: {exc}")
        session_id = str(payload.get("session_id") or "")
        return ToolResult.success(
            output_text=f"created claude session {session_id} in {payload.get('cwd', cwd)}",
            data={"agent_brand": _BRAND, **payload},
        )


class ClaudeSendPromptTool(ToolPort):
    name = "claude_send_prompt"
    description = (
        "Dispatch a prompt to a Claude session. Works for BOTH a fresh "
        "session_id from claude_new_session AND a historical session_id "
        "discovered via list_history_sessions — Claude Code CLI resumes "
        "the prior conversation automatically. Returns immediately with "
        "a subtask_id; track progress via wait_for_subtask. Model / "
        "reasoning_effort / permission_mode / plan_mode here override "
        "the session defaults for this single turn only."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "session_id": {"type": "string"},
            "message": {"type": "string"},
            "cwd": {"type": "string"},
            **_SESSION_PARAM_SCHEMA,
        },
        "required": ["session_id", "message"],
    }

    async def invoke(self, ctx: ToolContext) -> ToolResult:
        return await _send_prompt_impl(ctx, _BRAND)
