"""Read-only diagnostic tools: query session/subtask state without side effects.

These let the master agent answer "what's running?" / "did codex finish?"
without polling the subprocess or making blocking calls. All data comes
from the in-memory SessionBridgeService snapshot + SubTaskTracker.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from ..contracts.tool_port import ToolContext, ToolPort, ToolResult

_LOGGER = logging.getLogger(__name__)

# Scan caps — prevent a runaway search across thousands of historical
# files. Adjust if real workloads need wider sweeps.
_SEARCH_MAX_SCAN_FILES = 200
_SEARCH_MAX_LINES_PER_FILE = 5000
_SEARCH_SNIPPET_CHARS = 240


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


class QuerySessionStatusTool(ToolPort):
    name = "query_session_status"
    description = (
        "Look up the current status of a codex/claude session OR a master-agent "
        "subtask. Pass exactly one of session_id or subtask_id. Useful before "
        "deciding whether to dispatch more work or report back to the user."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "session_id": {
                "type": "string",
                "description": "codex/claude session UUID; mutually exclusive with subtask_id",
            },
            "subtask_id": {
                "type": "string",
                "description": "master-agent subtask id returned by *_send_prompt",
            },
        },
    }

    async def invoke(self, ctx: ToolContext) -> ToolResult:
        session_id = _optional_str(ctx.arguments.get("session_id"))
        subtask_id = _optional_str(ctx.arguments.get("subtask_id"))
        if not session_id and not subtask_id:
            return ToolResult.failure("provide session_id or subtask_id")
        if session_id and subtask_id:
            return ToolResult.failure("pass exactly one of session_id or subtask_id")

        if subtask_id:
            subtask = await ctx.services.task_tracker.get(subtask_id)
            if subtask is None:
                return ToolResult.failure(f"subtask {subtask_id} not found")
            return ToolResult.success(
                output_text=(
                    f"subtask {subtask_id} brand={subtask.agent_brand} "
                    f"status={subtask.status} last_event={subtask.last_event_type or '-'}"
                ),
                data={"subtask": subtask.to_dict()},
            )

        record = await ctx.services.bridge_service.get_session_record(session_id)
        if record is None:
            return ToolResult.failure(f"session {session_id} not found in runtime")
        return ToolResult.success(
            output_text=(
                f"session {session_id} brand={getattr(record, 'agent_brand', '?')} "
                f"state={record.state} active={record.active} "
                f"cwd={record.cwd or '-'}"
            ),
            data={
                "session_id": record.session_id,
                "agent_brand": getattr(record, "agent_brand", ""),
                "state": record.state,
                "active": bool(record.active),
                "cwd": record.cwd,
                "branch": record.branch,
                "model": record.model,
                "effort": record.effort,
                "permission_mode": record.permission_mode,
                "plan_mode": bool(record.plan_mode),
                "last_event_type": record.last_event_type,
                "display_name": record.display_name,
            },
        )


class ListSessionsTool(ToolPort):
    name = "list_sessions"
    description = (
        "List currently active codex/claude sessions known to the bridge. "
        "Pass active_only=true (default) to filter out idle/offline sessions."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "active_only": {
                "type": "boolean",
                "description": "If true (default) only return sessions still active.",
            },
        },
    }

    async def invoke(self, ctx: ToolContext) -> ToolResult:
        active_only = ctx.arguments.get("active_only")
        active_only = True if active_only is None else bool(active_only)
        snapshot = await ctx.services.bridge_service.get_snapshot()
        sessions = list(snapshot.get("sessions") or [])
        # bridge_service.get_snapshot already filters to active sessions,
        # so active_only=False would need a separate call. For MVP we
        # surface what we have and note the filter.
        summaries = [
            {
                "session_id": s.get("session_id"),
                "agent_brand": s.get("agent_brand"),
                "state": s.get("state"),
                "cwd": s.get("cwd"),
                "display_name": s.get("display_name"),
                "last_event_type": s.get("last_event_type"),
                "last_seen_at": s.get("last_seen_at"),
            }
            for s in sessions
        ]
        return ToolResult.success(
            output_text=f"{len(summaries)} session(s) listed (active_only={active_only})",
            data={"sessions": summaries, "active_only": active_only},
        )


class ListHistorySessionsTool(ToolPort):
    name = "list_history_sessions"
    description = (
        "List past codex/claude sessions known to the bridge (read from "
        "the on-disk JSONL history + the in-memory registry). Use this to "
        "find an old session the user wants to resume; pass the returned "
        "session_id to codex_send_prompt / claude_send_prompt to continue "
        "the conversation. Filter with agent_brand=codex|claude to narrow "
        "the list."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "limit": {
                "type": "integer",
                "description": "Max number of sessions to return (default 20, max 200).",
            },
            "agent_brand": {
                "type": "string",
                "enum": ["codex", "claude"],
                "description": "Only return sessions for this brand.",
            },
            "cwd_substring": {
                "type": "string",
                "description": (
                    "Optional substring filter on session cwd, e.g. project name "
                    "to find sessions tied to a particular repo."
                ),
            },
        },
    }

    async def invoke(self, ctx: ToolContext) -> ToolResult:
        try:
            limit_raw = ctx.arguments.get("limit")
            limit = int(limit_raw) if limit_raw is not None else 20
        except (TypeError, ValueError):
            limit = 20
        limit = max(1, min(limit, 200))
        brand = _optional_str(ctx.arguments.get("agent_brand"))
        if brand and brand not in ("codex", "claude"):
            return ToolResult.failure("agent_brand must be 'codex' or 'claude'")
        cwd_substring = _optional_str(ctx.arguments.get("cwd_substring"))

        history = await ctx.services.bridge_service.get_history(limit)
        sessions = list(history.get("sessions") or [])
        if brand:
            sessions = [s for s in sessions if s.get("agent_brand") == brand]
        if cwd_substring:
            sessions = [s for s in sessions if cwd_substring in (s.get("cwd") or "")]
        # Trim payload to the fields the LLM needs to pick a resume target.
        summaries = [
            {
                "session_id": s.get("session_id"),
                "agent_brand": s.get("agent_brand"),
                "display_name": s.get("display_name"),
                "cwd": s.get("cwd"),
                "branch": s.get("branch"),
                "state": s.get("state"),
                "active": bool(s.get("active")),
                "last_seen_at": s.get("last_seen_at"),
                "last_event_type": s.get("last_event_type"),
                "has_real_user_input": bool(s.get("has_real_user_input")),
            }
            for s in sessions
        ]
        return ToolResult.success(
            output_text=(
                f"{len(summaries)} historical session(s) "
                f"(brand={brand or 'any'}, cwd~={cwd_substring or 'any'})"
            ),
            data={"sessions": summaries, "brand": brand, "cwd_substring": cwd_substring},
        )


class GetSessionConversationTool(ToolPort):
    name = "get_session_conversation"
    description = (
        "Read the last N messages of an existing session so the master "
        "agent can summarize what happened or decide how to continue. "
        "Use after list_history_sessions to inspect a candidate before "
        "resuming it via *_send_prompt."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "session_id": {"type": "string"},
            "limit": {
                "type": "integer",
                "description": "Max messages to return (default 20, max 200).",
            },
        },
        "required": ["session_id"],
    }

    async def invoke(self, ctx: ToolContext) -> ToolResult:
        session_id = _optional_str(ctx.arguments.get("session_id"))
        if not session_id:
            return ToolResult.failure("session_id is required")
        try:
            limit_raw = ctx.arguments.get("limit")
            limit = int(limit_raw) if limit_raw is not None else 20
        except (TypeError, ValueError):
            limit = 20
        limit = max(1, min(limit, 200))
        payload = await ctx.services.bridge_service.get_conversation(
            session_id=session_id, limit=limit,
        )
        messages = payload.get("messages") if isinstance(payload, dict) else None
        if not messages:
            return ToolResult.failure(
                f"no conversation messages for session {session_id}"
            )
        # Compact each message — full content can be huge for long sessions.
        compact = []
        for msg in messages[-limit:]:
            content = msg.get("content")
            if isinstance(content, str) and len(content) > 800:
                content = content[:800] + "...<truncated>"
            compact.append({
                "role": msg.get("role"),
                "content": content,
                "timestamp": msg.get("timestamp"),
            })
        return ToolResult.success(
            output_text=f"{len(compact)} message(s) from session {session_id}",
            data={"session_id": session_id, "messages": compact},
        )


class SearchSessionsTool(ToolPort):
    name = "search_sessions"
    description = (
        "Find historical sessions whose on-disk JSONL contains the given "
        "keyword. Use this when the user refers to a past task by topic "
        "rather than session_id — e.g. '上次改 auth middleware 那個怎麼了'. "
        "Scans codex (~/.codex/sessions) and claude (~/.claude/projects) "
        "JSONLs and returns up to ``limit`` matches with session_id, "
        "agent_brand, cwd, display_name, last_seen_at, and a snippet of "
        "the matching line so the LLM can decide which one to follow up on."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Substring to look for (case-insensitive).",
            },
            "agent_brand": {
                "type": "string",
                "enum": ["codex", "claude"],
                "description": "Optional: limit scan to one brand.",
            },
            "cwd_substring": {
                "type": "string",
                "description": (
                    "Optional cwd substring filter (e.g. project name) to "
                    "narrow the scan before opening files."
                ),
            },
            "limit": {
                "type": "integer",
                "description": "Max matches to return (default 10, max 50).",
            },
        },
        "required": ["query"],
    }

    async def invoke(self, ctx: ToolContext) -> ToolResult:
        query = str(ctx.arguments.get("query") or "").strip()
        if not query:
            return ToolResult.failure("query is required")
        brand = _optional_str(ctx.arguments.get("agent_brand"))
        if brand and brand not in ("codex", "claude"):
            return ToolResult.failure("agent_brand must be 'codex' or 'claude'")
        cwd_substring = _optional_str(ctx.arguments.get("cwd_substring"))
        try:
            limit_raw = ctx.arguments.get("limit")
            limit = int(limit_raw) if limit_raw is not None else 10
        except (TypeError, ValueError):
            limit = 10
        limit = max(1, min(limit, 50))

        # Discover candidate sessions cheaply via the in-memory history
        # snapshot — already keyed by cwd / display_name / agent_brand.
        bridge_service = ctx.services.bridge_service
        agent_provider = ctx.services.agent_provider
        history = await bridge_service.get_history(_SEARCH_MAX_SCAN_FILES)
        candidates = list(history.get("sessions") or [])
        if brand:
            candidates = [s for s in candidates if s.get("agent_brand") == brand]
        if cwd_substring:
            candidates = [
                s for s in candidates if cwd_substring in (s.get("cwd") or "")
            ]
        candidates = candidates[:_SEARCH_MAX_SCAN_FILES]
        if not candidates:
            return ToolResult.success(
                output_text=f"no sessions matched filters for query={query!r}",
                data={"matches": [], "query": query, "scanned": 0},
            )

        try:
            matches = await asyncio.to_thread(
                _scan_sessions_for_query, candidates, agent_provider, query, limit,
            )
        except Exception as exc:  # noqa: BLE001 — never let disk errors break agent
            _LOGGER.exception("search_sessions disk scan failed")
            return ToolResult.failure(f"search failed: {exc}")
        return ToolResult.success(
            output_text=(
                f"{len(matches)} match(es) for {query!r} "
                f"(scanned {len(candidates)} session(s))"
            ),
            data={"matches": matches, "query": query, "scanned": len(candidates)},
        )


# ---------------------------------------------------------------------------
# Disk-scan helpers (run inside asyncio.to_thread).
# ---------------------------------------------------------------------------


def _scan_sessions_for_query(
    candidates: list[dict[str, Any]],
    agent_provider: Any,
    query: str,
    limit: int,
) -> list[dict[str, Any]]:
    """Sync: walk candidate session JSONLs looking for ``query``.

    Stops after ``limit`` matches. Returns the most recently-active
    candidates first because ``get_history`` already sorts by
    last_seen_epoch desc.
    """
    query_lower = query.lower()
    matches: list[dict[str, Any]] = []
    for session in candidates:
        sid = str(session.get("session_id") or "").strip()
        brand = str(session.get("agent_brand") or "").strip()
        if not sid or brand not in ("codex", "claude"):
            continue
        file_path = _find_session_file(agent_provider, brand, sid)
        if file_path is None:
            continue
        snippet = _scan_file_for_snippet(file_path, query_lower)
        if snippet:
            matches.append({
                "session_id": sid,
                "agent_brand": brand,
                "cwd": session.get("cwd", ""),
                "display_name": session.get("display_name", ""),
                "last_seen_at": session.get("last_seen_at", ""),
                "snippet": snippet,
            })
            if len(matches) >= limit:
                break
    return matches


def _find_session_file(agent_provider: Any, brand: str, session_id: str) -> Path | None:
    try:
        session_dir = agent_provider.get_session_dir(brand)
    except (ValueError, KeyError):
        return None
    if not session_dir or not Path(session_dir).exists():
        return None
    try:
        return next(iter(Path(session_dir).rglob(f"*{session_id}*.jsonl")), None)
    except OSError:
        return None


def _scan_file_for_snippet(path: Path, query_lower: str) -> str:
    """Return the first matching line (extracted human-readable text)
    or empty string if nothing matched within the scan budget."""
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as f:
            for index, line in enumerate(f):
                if index >= _SEARCH_MAX_LINES_PER_FILE:
                    break
                if query_lower not in line.lower():
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    return line.strip()[:_SEARCH_SNIPPET_CHARS]
                text = _extract_human_text(event, query_lower)
                if text:
                    return text[:_SEARCH_SNIPPET_CHARS]
                return line.strip()[:_SEARCH_SNIPPET_CHARS]
    except OSError:
        return ""
    return ""


def _extract_human_text(event: Any, query_lower: str) -> str:
    """Best-effort: pull human-readable text from common JSONL shapes.

    Codex events look like ``{type: 'event_msg', payload: {message: '...'}}``
    or ``{type: 'response_item', payload: {content: '...'}}``. Claude
    events look like ``{type: 'user'/'assistant', message: {content:
    [{type: 'text', text: '...'}]}}``. We probe both — first match
    containing the query wins.
    """
    if not isinstance(event, dict):
        return ""
    candidates: list[str] = []
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else event
    for key in ("message", "text", "content", "result", "summary"):
        val = payload.get(key)
        if isinstance(val, str):
            candidates.append(val)
        elif isinstance(val, list):
            for item in val:
                if isinstance(item, dict):
                    sub = item.get("text")
                    if isinstance(sub, str):
                        candidates.append(sub)
                elif isinstance(item, str):
                    candidates.append(item)
    # Claude nested shape.
    msg = event.get("message")
    if isinstance(msg, dict):
        content = msg.get("content")
        if isinstance(content, str):
            candidates.append(content)
        elif isinstance(content, list):
            for item in content:
                if isinstance(item, dict):
                    sub = item.get("text")
                    if isinstance(sub, str):
                        candidates.append(sub)
    for text in candidates:
        if query_lower in text.lower():
            return text
    return ""


class ListSubTasksTool(ToolPort):
    name = "list_subtasks"
    description = (
        "List subtasks dispatched in the current master-agent conversation. "
        "Optionally filter by status (pending/running/awaiting_approval/done/failed/aborted)."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "status": {
                "type": "string",
                "enum": [
                    "pending", "running", "awaiting_approval",
                    "done", "failed", "aborted",
                ],
                "description": "Optional status filter.",
            },
        },
    }

    async def invoke(self, ctx: ToolContext) -> ToolResult:
        status = _optional_str(ctx.arguments.get("status"))
        items = await ctx.services.task_tracker.list_for_conversation(
            ctx.conversation_id, status=status,  # type: ignore[arg-type]
        )
        return ToolResult.success(
            output_text=f"{len(items)} subtask(s) in conversation {ctx.conversation_id}",
            data={"subtasks": [t.to_dict() for t in items], "status_filter": status},
        )
