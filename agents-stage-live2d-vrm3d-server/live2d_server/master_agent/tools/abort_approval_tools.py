"""Abort + approval tools — control-plane operations on codex/claude workers."""

from __future__ import annotations

import logging
from typing import Any

from ..contracts.tool_port import ToolContext, ToolPort, ToolResult

_LOGGER = logging.getLogger(__name__)
_ALLOWED_BRANDS = {"codex", "claude"}
_ALLOWED_DECISIONS = {"allow_once", "deny_once", "allow_prefix"}


def _normalize_brand(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    return text if text in _ALLOWED_BRANDS else None


class AbortSessionTool(ToolPort):
    name = "abort_session"
    description = (
        "Force-kill the in-flight codex/claude subprocess for a given session. "
        "Use this when the user explicitly wants to stop a running worker "
        "or when wait_for_subtask reports the subtask is stuck."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "session_id": {"type": "string", "description": "codex/claude session UUID"},
            "agent_brand": {
                "type": "string",
                "enum": ["codex", "claude"],
                "description": "Which worker owns this session.",
            },
        },
        "required": ["session_id", "agent_brand"],
    }

    async def invoke(self, ctx: ToolContext) -> ToolResult:
        session_id = str(ctx.arguments.get("session_id") or "").strip()
        brand = _normalize_brand(ctx.arguments.get("agent_brand"))
        if not session_id:
            return ToolResult.failure("session_id is required")
        if brand is None:
            return ToolResult.failure("agent_brand must be 'codex' or 'claude'")
        service = ctx.services.agent_provider.get_chat_service(brand)
        try:
            aborted = await service.abort_session(session_id)
        except Exception as exc:  # noqa: BLE001 — surface as failure
            _LOGGER.exception("abort_session %s for %s failed", brand, session_id)
            return ToolResult.failure(f"abort failed: {exc}")
        if not aborted:
            return ToolResult.success(
                output_text=f"no live {brand} process for session {session_id}",
                data={"aborted": False, "session_id": session_id, "agent_brand": brand},
            )
        return ToolResult.success(
            output_text=f"killed {brand} subprocess for session {session_id}",
            data={"aborted": True, "session_id": session_id, "agent_brand": brand},
        )


class ApprovePendingTool(ToolPort):
    name = "approve_pending"
    description = (
        "Resolve an approval_request emitted by a worker. ``decision`` is "
        "allow_once / deny_once / allow_prefix. When allow_prefix, pass "
        "prefix_rule as the command prefix tokens to whitelist for the "
        "rest of the session."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "pending_id": {"type": "string"},
            "decision": {
                "type": "string",
                "enum": ["allow_once", "deny_once", "allow_prefix"],
            },
            "agent_brand": {
                "type": "string",
                "enum": ["codex", "claude"],
                "description": "Which worker emitted the approval_request.",
            },
            "prefix_rule": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Required when decision=allow_prefix; e.g. ['npm','install'].",
            },
        },
        "required": ["pending_id", "decision", "agent_brand"],
    }

    async def invoke(self, ctx: ToolContext) -> ToolResult:
        pending_id = str(ctx.arguments.get("pending_id") or "").strip()
        decision = str(ctx.arguments.get("decision") or "").strip().lower()
        brand = _normalize_brand(ctx.arguments.get("agent_brand"))
        prefix_rule_raw = ctx.arguments.get("prefix_rule") or []
        if not pending_id:
            return ToolResult.failure("pending_id is required")
        if decision not in _ALLOWED_DECISIONS:
            return ToolResult.failure(
                f"decision must be one of {sorted(_ALLOWED_DECISIONS)}"
            )
        if brand is None:
            return ToolResult.failure("agent_brand must be 'codex' or 'claude'")
        prefix_rule = [str(x) for x in (prefix_rule_raw or []) if str(x).strip()]
        if decision == "allow_prefix" and not prefix_rule:
            return ToolResult.failure("prefix_rule is required when decision=allow_prefix")

        service = ctx.services.agent_provider.get_chat_service(brand)
        try:
            accepted = await service.submit_approval(pending_id, decision, prefix_rule)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.exception("submit_approval %s for %s failed", brand, pending_id)
            return ToolResult.failure(f"approval submission failed: {exc}")
        if not accepted:
            return ToolResult.failure(
                f"no pending approval matching {pending_id} (already resolved or expired)"
            )
        return ToolResult.success(
            output_text=f"approval {pending_id} resolved with decision={decision}",
            data={
                "pending_id": pending_id,
                "decision": decision,
                "prefix_rule": prefix_rule,
                "agent_brand": brand,
            },
        )
