"""``wait_for_subtask`` — let the master agent block until a worker finishes.

The async tool relays the worker's progress events back to the master SSE
stream via :meth:`SubTaskTracker.wait_with_progress`, so the user sees
"codex thinking..." / "tool call X" flowing through the master agent
loop without re-implementing transport.

Bounded by ``timeout_sec``: if the subtask doesn't reach a terminal state
in time, returns the latest snapshot **plus any partial text that has
already streamed** so the LLM can keep the user informed and decide
whether to wait again on the next hop.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from ..contracts.tool_port import ToolContext, ToolPort, ToolResult

_LOGGER = logging.getLogger(__name__)


def _env_float(name: str, default: float) -> float:
    raw = (os.getenv(name, "") or "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default


# Codex / claude long tasks (e.g. multi-file edits, large refactors) easily
# run 5–10 minutes. The old 60s default forced the master agent to either
# spam re-poll or give up; raise to 5 min and let the env override push
# higher when the operator knows tasks will be long.
_DEFAULT_TIMEOUT_SEC = _env_float("MASTER_AGENT_WAIT_DEFAULT_SEC", 300.0)
_MAX_TIMEOUT_SEC = _env_float("MASTER_AGENT_WAIT_MAX_SEC", 1800.0)

# How much partial text to surface on timeout (and on done when final_text
# wasn't populated). Keep bounded so we don't blow the LLM context.
_PARTIAL_TEXT_TAIL_CHARS = 2000


class WaitForSubTaskTool(ToolPort):
    name = "wait_for_subtask"
    description = (
        "Block until a dispatched subtask reaches a terminal state "
        "(done/failed/aborted) or the timeout fires. Returns the final "
        "status + collected text. On timeout, returns whatever text has "
        "already streamed plus ``timed_out=true``; you can call this "
        "again to keep waiting, or report partial progress to the user. "
        "Default timeout is generous (5 min) because codex/claude tasks "
        "regularly take that long."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "subtask_id": {
                "type": "string",
                "description": "Subtask id returned by *_send_prompt.",
            },
            "timeout_sec": {
                "type": "number",
                "description": (
                    f"Max seconds to wait (default {int(_DEFAULT_TIMEOUT_SEC)}, "
                    f"capped at {int(_MAX_TIMEOUT_SEC)})."
                ),
            },
        },
        "required": ["subtask_id"],
    }

    async def invoke(self, ctx: ToolContext) -> ToolResult:
        subtask_id = str(ctx.arguments.get("subtask_id") or "").strip()
        if not subtask_id:
            return ToolResult.failure("subtask_id is required")

        raw_timeout = ctx.arguments.get("timeout_sec")
        try:
            timeout_sec = float(raw_timeout) if raw_timeout is not None else _DEFAULT_TIMEOUT_SEC
        except (TypeError, ValueError):
            timeout_sec = _DEFAULT_TIMEOUT_SEC
        timeout_sec = max(1.0, min(timeout_sec, _MAX_TIMEOUT_SEC))

        existing = await ctx.services.task_tracker.get(subtask_id)
        if existing is None:
            return ToolResult.failure(f"subtask {subtask_id} not found")

        progress_count = 0
        text_chunks: list[str] = []
        last_event_type = ""
        last_status_event: dict[str, Any] | None = None
        async for event in ctx.services.task_tracker.wait_with_progress(
            subtask_id, timeout_sec=timeout_sec,
        ):
            event_type = str(event.get("type") or "")
            if event_type == "subtask_status":
                last_status_event = event
                continue
            progress_count += 1
            if event_type:
                last_event_type = event_type
            if event_type == "text":
                chunk = event.get("content")
                if isinstance(chunk, str) and chunk:
                    text_chunks.append(chunk)
            elif event_type == "error":
                err_text = event.get("content")
                if isinstance(err_text, str) and err_text:
                    text_chunks.append(f"[error] {err_text}")

        # The wait_with_progress generator always yields a final
        # subtask_status event so we can build the result without a
        # second lookup. Fall back to fetching it if absent.
        if last_status_event is None:
            current = await ctx.services.task_tracker.get(subtask_id)
            if current is not None:
                last_status_event = {"type": "subtask_status", "content": current.to_dict()}

        snapshot = (last_status_event or {}).get("content") or {}
        status = str(snapshot.get("status") or "")
        final_text = str(snapshot.get("final_text") or "")
        error = str(snapshot.get("error") or "")
        terminal = status in {"done", "failed", "aborted", "detached"}

        # ``final_text`` is only populated by the dispatcher AFTER the
        # stream completes. If the subtask is mid-flight we still have
        # partial text in ``text_chunks`` from the events we just
        # drained — surface it so the master agent can show progress.
        partial_text = "".join(text_chunks).strip()
        partial_tail = partial_text[-_PARTIAL_TEXT_TAIL_CHARS:] if partial_text else ""

        if terminal and status == "done":
            output = final_text or partial_tail or f"subtask {subtask_id} finished"
        elif terminal and status == "failed":
            output = f"subtask {subtask_id} failed: {error or partial_tail or 'unknown error'}"
        elif terminal and status == "aborted":
            output = f"subtask {subtask_id} aborted"
        elif terminal and status == "detached":
            session_id = str(snapshot.get("session_id") or "")
            output = (
                f"subtask {subtask_id} detached — master agent's stream died but "
                f"the worker subprocess looks alive on disk. Call "
                f"query_session_status(session_id=\"{session_id}\") to check "
                f"progress, or *_send_prompt to resume."
            )
            if final_text or partial_tail:
                output += f"\n\npartial output so far:\n{final_text or partial_tail}"
        elif partial_tail:
            output = (
                f"subtask {subtask_id} still {status or 'pending'} after {timeout_sec:.0f}s; "
                f"partial output:\n{partial_tail}"
            )
        else:
            output = (
                f"subtask {subtask_id} still {status or 'pending'} after {timeout_sec:.0f}s "
                f"(no text yet; last_event_type={last_event_type or '-'}). "
                "Call wait_for_subtask again to keep waiting."
            )
        return ToolResult.success(
            output_text=output,
            data={
                "subtask": snapshot,
                "terminal": terminal,
                "progress_events": progress_count,
                "timed_out": not terminal,
                "partial_text": partial_tail,
                "last_event_type": last_event_type,
            },
        )
