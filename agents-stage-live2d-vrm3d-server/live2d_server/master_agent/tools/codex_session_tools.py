"""Codex tools — create new sessions and dispatch prompts asynchronously."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from ..contracts.tool_port import ToolContext, ToolPort, ToolResult
from ..shared import SubTask

_LOGGER = logging.getLogger(__name__)
_BRAND = "codex"


_SESSION_PARAM_SCHEMA: dict[str, Any] = {
    "model": {
        "type": "string",
        "description": (
            "Model id, e.g. gpt-5.5 / gpt-5.4 / gpt-5.3-codex / claude-sonnet-4-6. Use "
            "list_available_models to see the full catalog per brand."
        ),
    },
    "reasoning_effort": {
        "type": "string",
        "description": (
            "Codex/Claude reasoning effort: minimal | low | medium | high | xhigh. "
            "Higher = better quality but slower + more tokens. Default is "
            "model-dependent."
        ),
    },
    "permission_mode": {
        "type": "string",
        "enum": ["default", "auto", "plan", "full"],
        "description": (
            "default/omitted = provider default (codex: exec with the "
            "platform automation sandbox; claude: --permission-mode auto). "
            "auto = automation sandbox + auto-review classifier decides if escalations are "
            "safe. plan = read-only, model produces a plan instead of "
            "editing. full = no sandbox, no approval — ONLY honored when "
            "the user typed '#full' in their chat message; otherwise "
            "downgraded to the provider default."
        ),
    },
    "plan_mode": {
        "type": "boolean",
        "description": "Start/continue in plan mode (read-only, produces a plan first).",
    },
}


class CodexNewSessionTool(ToolPort):
    name = "codex_new_session"
    description = (
        "Create a new Codex CLI session in a given working directory. "
        "Returns session_id; use it as the target for codex_send_prompt. "
        "Model + reasoning_effort + permission_mode set here become the "
        "session defaults but can still be overridden per send_prompt call."
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
        # Honor the master-agent default when the LLM doesn't override it.
        # For Codex this means the session bridge will use the platform
        # automation sandbox, not the dangerous bypass flag.
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
            _LOGGER.exception("codex_new_session failed cwd=%s", cwd)
            return ToolResult.failure(f"codex create_session failed: {exc}")

        session_id = str(payload.get("session_id") or "")
        return ToolResult.success(
            output_text=f"created codex session {session_id} in {payload.get('cwd', cwd)}",
            data={"agent_brand": _BRAND, **payload},
        )


class CodexSendPromptTool(ToolPort):
    name = "codex_send_prompt"
    description = (
        "Dispatch a prompt to a Codex session. Works for BOTH a fresh "
        "session_id from codex_new_session AND a historical session_id "
        "discovered via list_history_sessions — the codex CLI resumes "
        "the prior conversation automatically. Returns immediately with "
        "a subtask_id; track progress via wait_for_subtask. Model / "
        "reasoning_effort / permission_mode / plan_mode here override "
        "the session defaults for this single turn only."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "session_id": {"type": "string", "description": "Codex session id"},
            "message": {"type": "string", "description": "Prompt to send"},
            "cwd": {"type": "string", "description": "Working directory override"},
            **_SESSION_PARAM_SCHEMA,
        },
        "required": ["session_id", "message"],
    }

    async def invoke(self, ctx: ToolContext) -> ToolResult:
        return await _send_prompt_impl(ctx, _BRAND)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _master_agent_default_permission_mode_for_brand(brand: str) -> str:
    if brand == _BRAND:
        return "default"
    return "auto"


def _resolve_default_permission_mode(
    ctx: ToolContext, brand: str, explicit: Any,  # noqa: ARG001 — brand kept for future
) -> str:
    """Pick the ``permission_mode`` to forward to the service.

    Master-agent policy:

    - Codex default (no explicit value, or ``"default"``) → ``"default"``,
      which the Codex session bridge turns into the platform automation
      sandbox.
    - Claude default → ``"auto"`` so Claude Code uses its built-in
      auto classifier.
    - ``"plan"`` → kept (read-only planning).
    - ``"auto"`` → kept.
    - ``"full"`` → only honored when ``services.permit_full_access`` is
      True (set by the API when the user's chat message contained the
      ``#full`` keyword). Otherwise silently downgraded to the brand
      default so the LLM can never drop sandboxing on its own.
    """
    explicit_text = _optional_str(explicit)
    default_mode = _master_agent_default_permission_mode_for_brand(brand)
    permit_full = bool(getattr(ctx.services, "permit_full_access", False))
    if explicit_text == "full":
        if permit_full:
            return "full"
        _LOGGER.warning(
            "permission_mode=full blocked (no #full keyword in user message); "
            "downgrading to %s",
            default_mode,
        )
        return default_mode
    if explicit_text in ("plan", "auto"):
        return explicit_text
    if explicit_text == "default":
        return default_mode
    # Empty / "default" / unknown → master agent default.
    return default_mode


def _optional_bool(value: Any) -> bool | None:
    """Tristate: None when unset, otherwise coerced bool."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in ("1", "true", "yes", "on"):
        return True
    if text in ("0", "false", "no", "off"):
        return False
    return None


# If the disk-backed session record has been updated within this many
# seconds when our stream dies, treat the worker as still alive
# ("detached") rather than failed. Generous because Windows AV + scan
# interval (0.5s) + slow file writes can introduce a few seconds of lag
# even when the subprocess is writing actively.
_DETACHED_FRESHNESS_SEC = 60.0


async def _check_disk_still_active(
    ctx: ToolContext, session_id: str,
) -> str:
    """Return a short reason string when the on-disk session looks alive,
    or empty string when the CLI subprocess appears dead.

    Reads :meth:`SessionBridgeService.get_session_record` (populated by
    the background scan loop) and compares ``last_seen_epoch`` to the
    current time. Designed to be cheap and resilient — any exception
    degrades to "treat as dead" so we don't accidentally mark a truly
    failed subtask as detached.
    """
    import time as _time

    bridge_service = getattr(ctx.services, "bridge_service", None)
    if bridge_service is None:
        return ""
    try:
        record = await bridge_service.get_session_record(session_id)
    except Exception:  # noqa: BLE001 — degrade silently
        return ""
    if record is None:
        return ""
    last_seen_epoch = getattr(record, "last_seen_epoch", 0) or 0
    try:
        last_seen_epoch = float(last_seen_epoch)
    except (TypeError, ValueError):
        return ""
    if last_seen_epoch <= 0:
        return ""
    age_sec = _time.time() - last_seen_epoch
    if age_sec > _DETACHED_FRESHNESS_SEC:
        return ""
    return f"last disk event {age_sec:.1f}s ago"


async def _resolve_send_prompt_cwd(
    ctx: ToolContext, brand: str, session_id: str, explicit: Any,
) -> str:
    """Pick the cwd to use for a send_prompt invocation.

    Resolution order — **session-owned cwd wins over the conversation
    hint**, because Claude Code CLI computes the session JSONL location
    from cwd. Resuming with a different cwd raises ``No conversation
    found with session ID`` even if the UUID is correct.

    1. ``cwd`` passed in the tool args (explicit per-call override).
    2. Session-owned cwd from the bridge's in-memory session record —
       covers freshly-spawned sessions from this conversation.
    3. (Claude only) Session-owned cwd from the on-disk JSONL metadata
       — covers historical sessions resumed from disk.
    4. ``ctx.default_cwd`` (conversation-level hint) — only used when
       no session-owned cwd can be discovered, e.g. resuming a codex
       session the bridge never registered.

    Returns empty string if no cwd is discoverable (caller will then
    rely on the service's default).
    """
    explicit_text = str(explicit or "").strip()
    if explicit_text:
        return explicit_text

    bridge_service = getattr(ctx.services, "bridge_service", None)
    if bridge_service is not None:
        # In-memory session registry — covers fresh sessions the master
        # agent itself spawned this conversation.
        try:
            record = await bridge_service.get_session_record(session_id)
        except Exception:  # noqa: BLE001 — degrade silently
            record = None
        if record is not None:
            record_cwd = str(getattr(record, "cwd", "") or "").strip()
            if record_cwd:
                return record_cwd
        # Claude historical sessions encode cwd in their project dir;
        # resolve from disk so a resumed session lands in the right cwd
        # even when the bridge never registered the session in memory.
        if brand == "claude":
            lookup = getattr(bridge_service, "lookup_claude_session_metadata", None)
            if callable(lookup):
                try:
                    meta = lookup(session_id)
                except Exception:  # noqa: BLE001
                    meta = None
                if isinstance(meta, dict):
                    disk_cwd = str(meta.get("cwd") or "").strip()
                    if disk_cwd:
                        return disk_cwd

    # Fallback: conversation-level hint. Only reached when the session
    # itself doesn't own a cwd, which typically means a stale codex
    # resume the bridge can't introspect.
    if ctx.default_cwd:
        text = str(ctx.default_cwd).strip()
        if text:
            return text
    return ""


async def _send_prompt_impl(ctx: ToolContext, brand: str) -> ToolResult:
    args = ctx.arguments
    session_id = str(args.get("session_id") or "").strip()
    message = str(args.get("message") or "").strip()
    if not session_id:
        return ToolResult.failure("session_id is required")
    if not message:
        return ToolResult.failure("message is required")

    service = ctx.services.agent_provider.get_chat_service(brand)
    cwd = await _resolve_send_prompt_cwd(ctx, brand, session_id, args.get("cwd"))
    subtask = SubTask.new(
        conversation_id=ctx.conversation_id,
        agent_brand=brand,
        session_id=session_id,
        prompt=message,
        cwd=cwd,
    )
    await ctx.services.task_tracker.create(subtask)
    await ctx.services.task_tracker.update_status(subtask.id, status="running")

    async def _run() -> None:
        final_text_parts: list[str] = []
        try:
            stream_kwargs: dict[str, Any] = {
                "session_id": session_id,
                "prompt": message,
            }
            if cwd:
                stream_kwargs["cwd"] = cwd
            # Per-turn override params; honor platform default
            # permission_mode when the LLM doesn't specify one.
            permission_mode = _resolve_default_permission_mode(
                ctx, brand, args.get("permission_mode"),
            )
            if permission_mode is not None:
                stream_kwargs["permission_mode"] = permission_mode
            for key in (
                "model", "reasoning_effort",
                "approval_policy", "sandbox_mode",
            ):
                value = _optional_str(args.get(key))
                if value is not None:
                    stream_kwargs[key] = value
            plan_mode = _optional_bool(args.get("plan_mode"))
            if plan_mode is not None:
                stream_kwargs["plan_mode"] = plan_mode
            async for event in service.stream_prompt(**stream_kwargs):
                await ctx.services.task_tracker.append_event(subtask.id, event)
                if event.get("type") == "text":
                    chunk = str(event.get("content") or "")
                    if chunk:
                        final_text_parts.append(chunk)
                elif event.get("type") == "approval_request":
                    await ctx.services.task_tracker.update_status(
                        subtask.id, status="awaiting_approval", last_event_type="approval_request",
                    )
                elif event.get("type") == "error":
                    await ctx.services.task_tracker.update_status(
                        subtask.id,
                        status="failed",
                        last_event_type="error",
                        error=str(event.get("content") or "stream error"),
                    )
                    return
        except Exception as exc:  # noqa: BLE001 — surface any subprocess failure
            _LOGGER.exception("subtask %s stream failed", subtask.id)
            # Disk fallback: the stream may have died (idle timeout,
            # subprocess hung, parser glitch) while the underlying CLI
            # subprocess is still alive and writing to its session JSONL.
            # Peek bridge_service's disk-backed registry to decide
            # whether to mark this subtask "detached" (CLI still alive,
            # follow up via query_session_status) vs "failed" (CLI dead).
            detached_reason = await _check_disk_still_active(
                ctx, session_id,
            )
            partial_text = "\n\n".join(final_text_parts).strip()
            if detached_reason:
                await ctx.services.task_tracker.update_status(
                    subtask.id,
                    status="detached",
                    final_text=partial_text,
                    last_event_type="detached",
                    error=(
                        f"stream ended ({exc}); session still active on disk "
                        f"({detached_reason}). Use query_session_status to track."
                    ),
                )
            else:
                await ctx.services.task_tracker.update_status(
                    subtask.id,
                    status="failed",
                    final_text=partial_text,
                    error=str(exc),
                )
            return
        await ctx.services.task_tracker.update_status(
            subtask.id,
            status="done",
            final_text="\n\n".join(final_text_parts).strip(),
            last_event_type="completed",
        )

    asyncio.create_task(_run(), name=f"master-agent-subtask-{subtask.id}")
    return ToolResult.success(
        output_text=f"dispatched {brand} subtask {subtask.id}",
        data={
            "subtask_id": subtask.id,
            "session_id": session_id,
            "agent_brand": brand,
        },
    )
