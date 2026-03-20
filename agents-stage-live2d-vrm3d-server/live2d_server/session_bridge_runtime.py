import asyncio
import json
import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Optional

from fastapi import WebSocket

from .session_bridge_shared import (
    PERMISSION_MODE_DEFAULT,
    SESSION_STATES,
    STATE_PRIORITY,
    UUID_PATTERN,
    WHITESPACE_PATTERN,
    _FileCursor,
    _SessionRecord,
    _basename_from_cwd,
    _claude_model_context_window,
    _env_bool,
    _extract_message_content,
    _is_auto_injected_message,
    _iso_now,
    _normalize_permission_mode,
    _normalize_ts,
    _resolve_permission_mode,
    _ts_to_epoch,
)

logger = logging.getLogger(__name__)


def _event_payload_or_item(event: dict[str, Any]) -> dict[str, Any]:
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    if payload:
        return payload
    if str(event.get("type") or "") == "item.completed" and isinstance(event.get("item"), dict):
        return event.get("item") or {}
    return {}


def _event_timestamp_or_none(event: dict[str, Any]) -> tuple[str, float] | None:
    timestamp = _normalize_ts(event.get("timestamp"))
    if not timestamp:
        return None
    event_epoch = _ts_to_epoch(timestamp)
    if event_epoch <= 0:
        return None
    return timestamp, event_epoch

class SessionBridgeService:
    """Bridge Codex and Claude local JSONL events into websocket session-state events."""

    def __init__(self) -> None:
        self.session_dir = Path(os.getenv("CODEX_SESSION_DIR", "~/.codex/sessions")).expanduser()
        self.claude_session_dir = Path(os.getenv("CLAUDE_SESSION_DIR", "~/.claude/projects")).expanduser()
        self.enabled = _env_bool("SESSION_BRIDGE_ENABLED", True)
        self.inactive_ttl_sec = int(os.getenv("SESSION_INACTIVE_TTL_SEC", "600"))

        self.scan_interval_sec = 0.5
        self.min_state_duration_sec = 0.6
        self.idle_after_task_complete_sec = 8.0
        self.initial_read_bytes = 256 * 1024

        self._watch_task: Optional[asyncio.Task] = None
        self._tick_task: Optional[asyncio.Task] = None

        self._files: dict[str, _FileCursor] = {}
        self._claude_files: dict[str, _FileCursor] = {}  # Claude session file cursors
        self._sessions: dict[str, _SessionRecord] = {}
        self._sessions_lock = asyncio.Lock()

        self._clients: set[WebSocket] = set()
        self._clients_lock = asyncio.Lock()

        self.events_ingested_total = 0
        self.events_dropped_total = 0
        self.latest_event_epoch = 0.0
        self.degraded_reason = ""
        self.source_version = "codex_jsonl.v1"
        self._session_id_pattern = UUID_PATTERN

    async def start(self) -> None:
        if not self.enabled:
            logger.info("Session bridge disabled by SESSION_BRIDGE_ENABLED")
            return
        if self._watch_task and not self._watch_task.done():
            return
        logger.info("Starting session bridge. codex_dir=%s claude_dir=%s", self.session_dir, self.claude_session_dir)
        self._watch_task = asyncio.create_task(self._watch_loop(), name="session-bridge-watch")
        self._tick_task = asyncio.create_task(self._tick_loop(), name="session-bridge-tick")

    async def stop(self) -> None:
        tasks = [self._watch_task, self._tick_task]
        for task in tasks:
            if task is not None:
                task.cancel()
        for task in tasks:
            if task is None:
                continue
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("Session bridge task stopped with error")
        self._watch_task = None
        self._tick_task = None

        async with self._clients_lock:
            clients = list(self._clients)
            self._clients.clear()
        for ws in clients:
            try:
                await ws.close()
            except Exception:
                pass

    async def register_ws_client(self, ws: WebSocket) -> None:
        async with self._clients_lock:
            self._clients.add(ws)

    async def unregister_ws_client(self, ws: WebSocket) -> None:
        async with self._clients_lock:
            if ws in self._clients:
                self._clients.remove(ws)

    async def get_snapshot(self) -> dict[str, Any]:
        now_epoch = time.time()
        async with self._sessions_lock:
            active_sessions = [
                {
                    "session_id": s.session_id,
                    "display_name": s.display_name,
                    "state": s.state,
                    "last_seen_at": s.last_seen_at,
                    "originator": s.originator,
                    "cwd": s.cwd,
                    "cwd_basename": s.cwd_basename,
                    "last_event_type": s.last_event_type,
                    "branch": s.branch,
                    "agent_brand": getattr(s, "agent_brand", "codex"),
                    "has_real_user_input": bool(getattr(s, "has_real_user_input", False)),
                    "context": self._context_payload(s),
                }
                for s in sorted(self._sessions.values(), key=lambda x: x.last_seen_epoch, reverse=True)
                if s.active and (now_epoch - s.last_seen_epoch) < self.inactive_ttl_sec
            ]
        return {
            "version": "1",
            "generated_at": _iso_now(),
            "sessions": active_sessions,
        }

    async def get_history(self, limit: int = 20) -> dict[str, Any]:
        safe_limit = max(1, min(int(limit or 20), 200))
        # Collect file-based Codex sessions.
        sessions = self._collect_history_from_files()
        now_epoch = time.time()
        # Merge in-memory sessions (e.g. Claude sessions that have no JSONL files).
        async with self._sessions_lock:
            for s in self._sessions.values():
                if s.session_id not in sessions:
                    sessions[s.session_id] = {
                        "session_id": s.session_id,
                        "display_name": s.display_name,
                        "state": s.state,
                        "last_seen_at": s.last_seen_at,
                        "last_seen_epoch": s.last_seen_epoch,
                        "originator": s.originator,
                        "cwd": s.cwd,
                        "cwd_basename": s.cwd_basename,
                        "last_event_type": s.last_event_type,
                        "branch": s.branch,
                        "agent_brand": getattr(s, "agent_brand", "codex"),
                        "has_real_user_input": bool(getattr(s, "has_real_user_input", False)),
                        "context": self._context_payload(s),
                    }
                else:
                    # Ensure agent_brand is present even for file-based sessions
                    # that were also registered in memory (e.g. after a chat).
                    existing = sessions[s.session_id]
                    if not existing.get("agent_brand"):
                        existing["agent_brand"] = getattr(s, "agent_brand", "codex")
                    if bool(getattr(s, "has_real_user_input", False)):
                        existing["has_real_user_input"] = True
        ordered = sorted(
            sessions.values(),
            key=lambda item: item["last_seen_epoch"],
            reverse=True,
        )[:safe_limit]
        for item in ordered:
            if item.get("branch"):
                continue
            cwd = str(item.get("cwd") or "").strip()
            if not cwd:
                continue
            try:
                completed = subprocess.run(
                    ["git", "-C", cwd, "branch", "--show-current"],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
            except Exception:
                continue
            if completed.returncode == 0:
                item["branch"] = (completed.stdout or "").strip()
        return {
            "version": "1",
            "generated_at": _iso_now(),
            "sessions": [
                {
                    "session_id": item["session_id"],
                    "display_name": item["display_name"],
                    "state": item["state"],
                    "last_seen_at": item["last_seen_at"],
                    "active": (now_epoch - item["last_seen_epoch"]) < self.inactive_ttl_sec,
                    "originator": item.get("originator", "Codex Desktop"),
                    "cwd": item.get("cwd", ""),
                    "cwd_basename": item.get("cwd_basename", ""),
                    "last_event_type": item.get("last_event_type", ""),
                    "branch": item.get("branch", ""),
                    "agent_brand": item.get("agent_brand", "codex"),
                    "has_real_user_input": bool(item.get("has_real_user_input", False)),
                    "context": item.get("context", {}),
                }
                for item in ordered
            ],
        }

    async def get_conversation(self, session_id: str, limit: int = 1000) -> dict[str, Any]:
        safe_limit = max(1, min(int(limit or 1000), 5000))
        normalized_session_id = (session_id or "").strip()
        if not normalized_session_id:
            return {
                "version": "1",
                "generated_at": _iso_now(),
                "session_id": "",
                "messages": [],
            }

        # Collect from both Codex and Claude sources and merge
        messages = (
            self._collect_conversation_from_files(normalized_session_id)
            + self._collect_claude_conversation_from_files(normalized_session_id)
        )
        ordered = sorted(messages, key=lambda item: (item["ts_epoch"], item["seq"]))
        deduped: list[dict[str, Any]] = []
        for item in ordered:
            if deduped and deduped[-1]["role"] == item["role"] and deduped[-1]["content"] == item["content"]:
                continue
            deduped.append(item)
        sliced = deduped[-safe_limit:]
        return {
            "version": "1",
            "generated_at": _iso_now(),
            "session_id": normalized_session_id,
            "messages": [
                {
                    "role": item["role"],
                    "content": item["content"],
                    "timestamp": item["timestamp"],
                }
                for item in sliced
            ],
        }

    async def get_session_record(self, session_id: str) -> Optional[_SessionRecord]:
        session_key = (session_id or "").strip()
        if not session_key:
            return None
        async with self._sessions_lock:
            session = self._sessions.get(session_key)
            if session is None:
                return None
            copied = _SessionRecord(
                session_id=session.session_id,
                display_name=session.display_name,
                state=session.state,
                last_seen_at=session.last_seen_at,
                last_seen_epoch=session.last_seen_epoch,
                last_state_change_mono=session.last_state_change_mono,
                pending_state=session.pending_state,
                pending_due_mono=session.pending_due_mono,
                idle_due_epoch=session.idle_due_epoch,
                active=session.active,
                originator=session.originator,
                agent_brand=getattr(session, "agent_brand", "codex"),
                cwd=session.cwd,
                cwd_basename=session.cwd_basename,
                branch=session.branch,
                model=session.model,
                effort=session.effort,
                permission_mode=session.permission_mode,
                approval_policy=session.approval_policy,
                sandbox_mode=session.sandbox_mode,
                plan_mode=session.plan_mode,
                plan_mode_fallback=session.plan_mode_fallback,
                total_tokens=session.total_tokens,
                model_context_window=session.model_context_window,
                primary_rate_remaining_percent=session.primary_rate_remaining_percent,
                secondary_rate_remaining_percent=session.secondary_rate_remaining_percent,
                last_event_type=session.last_event_type,
                has_real_user_input=session.has_real_user_input,
            )
        return copied

    async def upsert_runtime_context(
        self,
        session_id: str,
        *,
        cwd: Optional[str] = None,
        branch: Optional[str] = None,
        model: Optional[str] = None,
        effort: Optional[str] = None,
        permission_mode: Optional[str] = None,
        approval_policy: Optional[str] = None,
        sandbox_mode: Optional[str] = None,
        plan_mode: Optional[bool] = None,
        plan_mode_fallback: Optional[bool] = None,
        total_tokens: Optional[int] = None,
        model_context_window: Optional[int] = None,
        primary_rate_remaining_percent: Optional[float] = None,
        secondary_rate_remaining_percent: Optional[float] = None,
        agent_brand: Optional[str] = None,
    ) -> None:
        session_key = (session_id or "").strip()
        if not session_key:
            return
        now_ts = _iso_now()
        now_epoch = _ts_to_epoch(now_ts)
        async with self._sessions_lock:
            session = self._sessions.get(session_key)
            if session is None:
                session = self._new_session(session_key, now_ts)
                self._sessions[session_key] = session
            if cwd is not None:
                session.cwd = str(cwd)
                session.cwd_basename = _basename_from_cwd(session.cwd)
            if branch is not None:
                session.branch = str(branch)
            if model is not None:
                session.model = str(model)
            if effort is not None:
                session.effort = str(effort)
            if permission_mode is not None:
                session.permission_mode = _normalize_permission_mode(permission_mode)
            if approval_policy is not None:
                session.approval_policy = str(approval_policy)
            if sandbox_mode is not None:
                session.sandbox_mode = str(sandbox_mode)
            if plan_mode is not None:
                session.plan_mode = bool(plan_mode)
            if plan_mode_fallback is not None:
                session.plan_mode_fallback = bool(plan_mode_fallback)
            if total_tokens is not None:
                session.total_tokens = max(0, int(total_tokens))
            if model_context_window is not None:
                session.model_context_window = max(0, int(model_context_window))
            if primary_rate_remaining_percent is not None:
                session.primary_rate_remaining_percent = max(0.0, min(100.0, float(primary_rate_remaining_percent)))
            if secondary_rate_remaining_percent is not None:
                session.secondary_rate_remaining_percent = max(0.0, min(100.0, float(secondary_rate_remaining_percent)))
            if agent_brand is not None:
                session.agent_brand = str(agent_brand).strip().lower()
                if session.agent_brand == "claude":
                    session.originator = "Claude Code"
            session.last_seen_at = now_ts
            session.last_seen_epoch = now_epoch
            session.active = True
            event = self._build_state_event(session)
        # Broadcast outside the lock so subscribers see the update.
        await self._broadcast(event)

    async def try_refresh_session_branch(self, session_id: str, cwd: str) -> str:
        cwd_value = str(cwd or "").strip()
        if not cwd_value:
            return ""
        branch = ""
        try:
            completed = subprocess.run(
                ["git", "-C", cwd_value, "branch", "--show-current"],
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if completed.returncode == 0:
                branch = (completed.stdout or "").strip()
        except Exception:
            branch = ""
        if branch:
            await self.upsert_runtime_context(session_id, branch=branch)
        return branch

    def _collect_history_from_files(self) -> dict[str, dict[str, Any]]:
        if not self.session_dir.exists():
            return {}
        try:
            paths = [path for path in self.session_dir.rglob("*.jsonl") if path.is_file()]
        except PermissionError:
            return {}

        sessions: dict[str, dict[str, Any]] = {}
        for path in paths:
            cursor = _FileCursor(
                path=path,
                offset=0,
                inode=0,
                session_id=self._extract_session_id_from_path(path),
            )
            try:
                with path.open("r", encoding="utf-8", errors="ignore") as handle:
                    for line in handle:
                        self._collect_history_from_line(line, cursor, sessions)
            except OSError:
                continue
        return sessions

    def _collect_conversation_from_files(self, session_id: str) -> list[dict[str, Any]]:
        if not self.session_dir.exists():
            return []
        try:
            paths = [path for path in self.session_dir.rglob("*.jsonl") if path.is_file()]
        except PermissionError:
            return []

        results: list[dict[str, Any]] = []
        seq = 0
        for path in paths:
            cursor = _FileCursor(
                path=path,
                offset=0,
                inode=0,
                session_id=self._extract_session_id_from_path(path),
            )
            try:
                with path.open("r", encoding="utf-8", errors="ignore") as handle:
                    for line in handle:
                        item = self._collect_conversation_from_line(
                            line=line,
                            cursor=cursor,
                            session_id=session_id,
                            seq=seq,
                        )
                        seq += 1
                        if item is not None:
                            results.append(item)
            except OSError:
                continue
        return results

    def _collect_conversation_from_line(
        self,
        line: str,
        cursor: _FileCursor,
        session_id: str,
        seq: int,
    ) -> Optional[dict[str, Any]]:
        text = line.strip()
        if not text:
            return None
        try:
            event = json.loads(text)
        except json.JSONDecodeError:
            return None
        if not isinstance(event, dict):
            return None

        payload = _event_payload_or_item(event)
        current_session_id = self._extract_session_id(event, payload, cursor)
        if not current_session_id:
            return None
        cursor.session_id = current_session_id
        if current_session_id != session_id:
            return None

        top_type = str(event.get("type") or "")
        parsed_ts = _event_timestamp_or_none(event)
        if parsed_ts is None:
            return None
        ts, ts_epoch = parsed_ts

        if top_type == "event_msg" and str(payload.get("type") or "") == "user_message":
            content = str(payload.get("message") or "").strip()
            if content:
                if _is_auto_injected_message("user", content):
                    return None
                return {
                    "role": "user",
                    "content": content,
                    "timestamp": ts,
                    "ts_epoch": ts_epoch,
                    "seq": seq,
                }
            return None

        if top_type == "response_item" and str(payload.get("type") or "") == "message":
            role = str(payload.get("role") or "").strip().lower()
            if role not in {"user", "assistant"}:
                return None
            content = _extract_message_content(payload)
            if not content:
                return None
            if _is_auto_injected_message(role, content):
                return None
            return {
                "role": role,
                "content": content,
                "timestamp": ts,
                "ts_epoch": ts_epoch,
                "seq": seq,
            }
        return None

    def _extract_session_id_from_path(self, path: Path) -> Optional[str]:
        match_by_name = self._session_id_pattern.search(path.name)
        if match_by_name:
            return match_by_name.group(0)
        match_by_full_path = self._session_id_pattern.search(str(path))
        if match_by_full_path:
            return match_by_full_path.group(0)
        return None

    def _collect_claude_conversation_from_files(self, session_id: str) -> list[dict[str, Any]]:
        """Collect conversation messages for a Claude session from ~/.claude/projects/**/*.jsonl."""
        if not self.claude_session_dir.exists():
            return []
        try:
            paths = [p for p in self.claude_session_dir.rglob("*.jsonl") if p.is_file()]
        except PermissionError:
            return []

        results: list[dict[str, Any]] = []
        seq = 0
        for path in paths:
            try:
                with path.open("r", encoding="utf-8", errors="ignore") as handle:
                    for line in handle:
                        text = line.strip()
                        if not text:
                            seq += 1
                            continue
                        try:
                            event = json.loads(text)
                        except json.JSONDecodeError:
                            seq += 1
                            continue
                        if not isinstance(event, dict):
                            seq += 1
                            continue

                        # Claude uses camelCase sessionId
                        ev_session = str(
                            event.get("sessionId") or event.get("session_id") or ""
                        ).strip()
                        if ev_session != session_id:
                            seq += 1
                            continue

                        event_type = str(event.get("type") or "").lower()
                        parsed_ts = _event_timestamp_or_none(event)
                        if parsed_ts is None:
                            seq += 1
                            continue
                        ts, ts_epoch = parsed_ts
                        message = event.get("message") if isinstance(event.get("message"), dict) else {}

                        if event_type == "user":
                            content = message.get("content", "")
                            if isinstance(content, list):
                                texts = [
                                    c.get("text", "") for c in content
                                    if isinstance(c, dict) and c.get("type") == "text"
                                ]
                                content = " ".join(t for t in texts if t)
                            if isinstance(content, str) and content.strip():
                                if _is_auto_injected_message("user", content):
                                    seq += 1
                                    continue
                                results.append({
                                    "role": "user",
                                    "content": content.strip(),
                                    "timestamp": ts,
                                    "ts_epoch": ts_epoch,
                                    "seq": seq,
                                })

                        elif event_type == "assistant":
                            content_raw = message.get("content", "")
                            # Claude assistant content can be a list of blocks
                            if isinstance(content_raw, list):
                                texts = [
                                    c.get("text", "") for c in content_raw
                                    if isinstance(c, dict) and c.get("type") == "text"
                                ]
                                content = " ".join(t for t in texts if t)
                            elif isinstance(content_raw, str):
                                content = content_raw
                            else:
                                content = ""
                            if content.strip():
                                if _is_auto_injected_message("assistant", content):
                                    seq += 1
                                    continue
                                results.append({
                                    "role": "assistant",
                                    "content": content.strip(),
                                    "timestamp": ts,
                                    "ts_epoch": ts_epoch,
                                    "seq": seq,
                                })

                        seq += 1
            except OSError:
                continue
        return results

    def _collect_history_from_line(
        self,
        line: str,
        cursor: _FileCursor,
        sessions: dict[str, dict[str, Any]],
    ) -> None:
        text = line.strip()
        if not text:
            return
        try:
            event = json.loads(text)
        except json.JSONDecodeError:
            return

        top_type = event.get("type", "")
        payload = _event_payload_or_item(event)
        parsed_ts = _event_timestamp_or_none(event)
        if parsed_ts is None:
            return
        ts, ts_epoch = parsed_ts
        session_id = self._extract_session_id(event, payload, cursor)
        if not session_id:
            return
        cursor.session_id = session_id

        record = sessions.get(session_id)
        if record is None:
            record = {
                "session_id": session_id,
                "display_name": f"session-{session_id[:8]}",
                "state": "IDLE",
                "last_seen_at": ts,
                "last_seen_epoch": ts_epoch,
                "originator": "Codex Desktop",
                "cwd": "",
                "cwd_basename": "",
                "last_event_type": "",
                "has_real_user_input": False,
                "branch": "",
                "context": {
                    "model": "",
                    "effort": "",
                    "permission_mode": PERMISSION_MODE_DEFAULT,
                    "approval_policy": "",
                    "sandbox_mode": "",
                    "plan_mode": None,
                    "plan_mode_fallback": False,
                    "total_tokens": 0,
                    "model_context_window": 0,
                    "primary_rate_remaining_percent": None,
                    "secondary_rate_remaining_percent": None,
                },
            }
            sessions[session_id] = record

        if ts_epoch < (record["last_seen_epoch"] - 1e-6):
            return

        record["last_seen_at"] = ts
        record["last_seen_epoch"] = ts_epoch
        record["last_event_type"] = str(payload.get("type") or top_type)

        if top_type == "session_meta":
            display_name = payload.get("display_name") or payload.get("name")
            if isinstance(display_name, str) and display_name.strip():
                record["display_name"] = display_name.strip()
            record["originator"] = str(payload.get("originator") or record.get("originator") or "Codex Desktop")
            record["cwd"] = str(payload.get("cwd") or record.get("cwd") or "")
            record["cwd_basename"] = _basename_from_cwd(record["cwd"])
            git_info = payload.get("git") if isinstance(payload.get("git"), dict) else {}
            if isinstance(git_info, dict):
                branch = str(git_info.get("branch") or "").strip()
                if branch:
                    record["branch"] = branch

        if top_type == "turn_context":
            context = record.get("context") if isinstance(record.get("context"), dict) else {}
            context["model"] = str(payload.get("model") or context.get("model") or "")
            context["effort"] = str(payload.get("effort") or context.get("effort") or "")
            context["approval_policy"] = str(payload.get("approval_policy") or context.get("approval_policy") or "")
            sandbox = payload.get("sandbox_policy") if isinstance(payload.get("sandbox_policy"), dict) else {}
            if isinstance(sandbox, dict):
                context["sandbox_mode"] = str(sandbox.get("type") or context.get("sandbox_mode") or "")
            context["permission_mode"] = _resolve_permission_mode(
                None,
                approval_policy=context.get("approval_policy"),
                sandbox_mode=context.get("sandbox_mode"),
            )
            collaboration = payload.get("collaboration_mode") if isinstance(payload.get("collaboration_mode"), dict) else {}
            plan_mode = None
            if isinstance(collaboration, dict):
                mode = str(collaboration.get("mode") or "").strip().lower()
                if mode:
                    plan_mode = mode == "plan"
            if plan_mode is not None:
                context["plan_mode"] = plan_mode
            context["plan_mode_fallback"] = bool(context.get("plan_mode_fallback", False))
            record["context"] = context
            cwd_from_turn = str(payload.get("cwd") or "").strip()
            if cwd_from_turn:
                record["cwd"] = cwd_from_turn
                record["cwd_basename"] = _basename_from_cwd(cwd_from_turn)

        if top_type == "event_msg" and str(payload.get("type") or "") == "token_count":
            info = payload.get("info") if isinstance(payload.get("info"), dict) else {}
            if isinstance(info, dict):
                context = record.get("context") if isinstance(record.get("context"), dict) else {}
                last_usage = info.get("last_token_usage") if isinstance(info.get("last_token_usage"), dict) else {}
                total_tokens = self._coerce_non_negative_int(
                    last_usage.get("total_tokens") if isinstance(last_usage, dict) else None
                )
                model_context_window = self._coerce_non_negative_int(info.get("model_context_window"))
                rate_limits = payload.get("rate_limits") if isinstance(payload.get("rate_limits"), dict) else {}
                primary_remaining = self._remaining_percent_from_rate_limit(
                    rate_limits.get("primary") if isinstance(rate_limits, dict) else None
                )
                secondary_remaining = self._remaining_percent_from_rate_limit(
                    rate_limits.get("secondary") if isinstance(rate_limits, dict) else None
                )
                if total_tokens is not None:
                    context["total_tokens"] = total_tokens
                if model_context_window is not None:
                    context["model_context_window"] = model_context_window
                if primary_remaining is not None:
                    context["primary_rate_remaining_percent"] = primary_remaining
                if secondary_remaining is not None:
                    context["secondary_rate_remaining_percent"] = secondary_remaining
                record["context"] = context

        title = self._extract_title(top_type, payload)
        if title:
            record["display_name"] = title
        if top_type == "event_msg" and str(payload.get("type") or "") == "user_message":
            user_message = str(payload.get("message") or "").strip()
            if user_message and not _is_auto_injected_message("user", user_message):
                record["has_real_user_input"] = True
        elif top_type == "response_item" and str(payload.get("type") or "") == "message" and str(payload.get("role") or "") == "user":
            user_content = _extract_message_content(payload)
            if user_content and not _is_auto_injected_message("user", user_content):
                record["has_real_user_input"] = True

        state, is_task_complete, wait_for_user = self._map_to_state(top_type, payload)
        if wait_for_user:
            record["state"] = "WAITING"
        elif is_task_complete:
            record["state"] = "IDLE"
        elif state in SESSION_STATES:
            record["state"] = state

    async def get_health(self) -> dict[str, Any]:
        async with self._clients_lock:
            ws_clients = len(self._clients)
        async with self._sessions_lock:
            active_count = sum(1 for x in self._sessions.values() if x.active)
        ingest_lag_ms = None
        if self.latest_event_epoch > 0:
            ingest_lag_ms = int((time.time() - self.latest_event_epoch) * 1000)
        return {
            "status": "OK" if not self.degraded_reason else "DEGRADED",
            "enabled": self.enabled,
            "session_dir": str(self.session_dir),
            "source_version": self.source_version,
            "events_ingested_total": self.events_ingested_total,
            "events_dropped_total": self.events_dropped_total,
            "ingest_lag_ms": ingest_lag_ms,
            "ws_clients": ws_clients,
            "session_active_count": active_count,
            "degraded_reason": self.degraded_reason,
        }

    async def _watch_loop(self) -> None:
        while True:
            try:
                await self._scan_once()
                if self.degraded_reason.startswith("scan:"):
                    self.degraded_reason = ""
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.events_dropped_total += 1
                self.degraded_reason = f"scan:{exc}"
                logger.exception("Session bridge scan failed: %s", exc)
            try:
                await self._scan_once_claude()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("Claude session scan failed: %s", exc)
            await asyncio.sleep(self.scan_interval_sec)

    async def _tick_loop(self) -> None:
        while True:
            try:
                emitted: list[dict[str, Any]] = []
                now_epoch = time.time()
                now_mono = time.monotonic()

                async with self._sessions_lock:
                    for session in self._sessions.values():
                        if session.pending_state and now_mono >= session.pending_due_mono:
                            previous_state = session.state
                            session.state = session.pending_state
                            session.pending_state = None
                            session.pending_due_mono = 0.0
                            session.last_state_change_mono = now_mono
                            if previous_state != session.state:
                                logger.info("session state: %s %s -> %s", session.session_id, previous_state, session.state)
                            emitted.append(self._build_state_event(session))

                        if session.idle_due_epoch and now_epoch >= session.idle_due_epoch:
                            session.idle_due_epoch = None
                            previous_state = session.state
                            if self._schedule_state_transition(session, "IDLE"):
                                if previous_state != session.state:
                                    logger.info("session state: %s %s -> IDLE", session.session_id, previous_state)
                                emitted.append(self._build_state_event(session))

                        if session.active and (now_epoch - session.last_seen_epoch) >= self.inactive_ttl_sec:
                            session.active = False
                            session.pending_state = None
                            session.idle_due_epoch = None
                            if session.state != "IDLE":
                                session.state = "IDLE"
                                session.last_state_change_mono = now_mono
                            logger.info("session offline: %s", session.session_id)
                            emitted.append(self._build_state_event(session, inactive=True))

                for event in emitted:
                    await self._broadcast(event)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Session bridge tick loop failed")
            await asyncio.sleep(0.2)

    async def _scan_once(self) -> None:
        if not self.session_dir.exists():
            self.degraded_reason = f"scan:session dir not found ({self.session_dir})"
            return

        try:
            current_paths = {
                str(path): path
                for path in self.session_dir.rglob("*.jsonl")
                if path.is_file()
            }
        except PermissionError as exc:
            self.degraded_reason = f"scan:permission denied ({exc})"
            return

        removed_keys = set(self._files.keys()) - set(current_paths.keys())
        for key in removed_keys:
            self._files.pop(key, None)

        for key, path in current_paths.items():
            try:
                await self._consume_file(path)
            except (OSError, ValueError) as exc:
                self.events_dropped_total += 1
                self.degraded_reason = f"scan:file read failed ({exc})"
                logger.warning("Session bridge read failed: %s (%s)", path, exc)

    async def _scan_once_claude(self) -> None:
        """Scan ~/.claude/projects/**/*.jsonl for Claude Code sessions."""
        if not self.claude_session_dir.exists():
            return
        try:
            current_paths = {
                str(path): path
                for path in self.claude_session_dir.rglob("*.jsonl")
                if path.is_file()
            }
        except PermissionError:
            return

        removed_keys = set(self._claude_files.keys()) - set(current_paths.keys())
        for key in removed_keys:
            self._claude_files.pop(key, None)

        for key, path in current_paths.items():
            try:
                await self._consume_claude_file(path)
            except (OSError, ValueError) as exc:
                logger.warning("Claude session bridge read failed: %s (%s)", path, exc)

    async def _consume_claude_file(self, path: Path) -> None:
        """Read incremental lines from a Claude session JSONL file."""
        key = str(path)
        stat = path.stat()
        cursor = self._claude_files.get(key)
        if cursor is None:
            # Extract session_id from filename (Claude names files as {uuid}.jsonl)
            session_id_from_name = None
            matched = self._session_id_pattern.search(path.stem)
            if matched:
                session_id_from_name = matched.group(0)
            cursor = _FileCursor(
                path=path,
                offset=0,
                inode=stat.st_ino,
                session_id=session_id_from_name,
            )
            self._claude_files[key] = cursor
        elif cursor.inode != stat.st_ino or stat.st_size < cursor.offset:
            cursor.offset = 0
            cursor.inode = stat.st_ino

        with path.open("r", encoding="utf-8", errors="ignore") as f:
            f.seek(cursor.offset)
            for line in f:
                await self._ingest_claude_line(line, cursor)
            cursor.offset = f.tell()

    async def _ingest_claude_line(self, line: str, cursor: _FileCursor) -> None:
        """Parse a single line from a Claude Code session JSONL file.

        Claude Code stores conversations in ~/.claude/projects/{cwd-hash}/{session_id}.jsonl
        Each line is a JSON object with fields:
          - type: "user" | "assistant" | "summary"
          - sessionId: UUID
          - cwd: working directory
          - timestamp: ISO string
          - message: {role, content}  (for user/assistant)
        """
        text = line.strip()
        if not text:
            return
        try:
            event = json.loads(text)
        except json.JSONDecodeError:
            return
        if not isinstance(event, dict):
            return

        # Claude uses camelCase sessionId
        session_id = str(
            event.get("sessionId") or event.get("session_id") or cursor.session_id or ""
        ).strip()
        if not session_id or not self._session_id_pattern.fullmatch(session_id):
            return
        cursor.session_id = session_id

        parsed_ts = _event_timestamp_or_none(event)
        if parsed_ts is None:
            logger.debug("Ignoring Claude disk event without valid timestamp session=%s event=%s", session_id, event.get("type"))
            return
        timestamp, event_epoch = parsed_ts
        now_epoch = time.time()
        cwd = str(event.get("cwd") or "").strip()
        event_type = str(event.get("type") or "").lower()

        async with self._sessions_lock:
            session = self._sessions.get(session_id)
            if session is None:
                session = self._new_session(session_id, timestamp)
                session.agent_brand = "claude"
                self._sessions[session_id] = session
                logger.info("Claude session online (disk): %s", session_id)
            elif event_epoch < (session.last_seen_epoch - 1e-6):
                return

            # Only update disk-sourced sessions, don't overwrite active in-memory ones
            # that may have more recent data.
            if cwd:
                session.cwd = cwd
                session.cwd_basename = _basename_from_cwd(cwd)
            session.last_seen_at = timestamp
            session.last_seen_epoch = event_epoch
            session.active = (now_epoch - event_epoch) < self.inactive_ttl_sec
            session.agent_brand = "claude"
            session.originator = "Claude Code"

            message = event.get("message") if isinstance(event.get("message"), dict) else {}
            if event_type == "user":
                # Extract display name from first user message
                content = message.get("content", "")
                if isinstance(content, list):
                    texts = [
                        c.get("text", "") for c in content
                        if isinstance(c, dict) and c.get("type") == "text"
                    ]
                    content = " ".join(t for t in texts if t)
                if isinstance(content, str) and content.strip():
                    cleaned = WHITESPACE_PATTERN.sub(" ", content).strip()
                    if not _is_auto_injected_message("user", cleaned):
                        session.has_real_user_input = True
                        if len(cleaned) > 42:
                            cleaned = cleaned[:39].rstrip() + "..."
                        session.display_name = cleaned
                session.last_event_type = "user_message"
                # After user sends a message, the model is thinking.
                session.state = "THINKING"

            elif event_type == "assistant":
                session.last_event_type = "assistant_message"
                # Check for model info in message
                model = str(message.get("model") or "").strip()
                if model:
                    session.model = model
                    # Set context window from known Claude model sizes if not yet set.
                    if session.model_context_window == 0:
                        session.model_context_window = _claude_model_context_window(model)
                # Extract token usage from message.usage (present in Claude JSONL).
                # Each turn's usage already reflects the full context window size
                # (input + cache = total conversation history), so take the latest
                # value rather than accumulating across turns.
                usage = message.get("usage") if isinstance(message.get("usage"), dict) else {}
                if isinstance(usage, dict):
                    inp = usage.get("input_tokens") or 0
                    out = usage.get("output_tokens") or 0
                    cache_read = usage.get("cache_read_input_tokens") or 0
                    cache_create = usage.get("cache_creation_input_tokens") or 0
                    try:
                        total = int(inp) + int(out) + int(cache_read) + int(cache_create)
                        if total > 0:
                            session.total_tokens = total
                    except (ValueError, TypeError):
                        pass
                # Check content array for tool_use blocks → TOOLING state.
                content = message.get("content")
                has_tool_use = False
                has_waiting_tool = False
                if isinstance(content, list):
                    for c in content:
                        if not isinstance(c, dict):
                            continue
                        if c.get("type") != "tool_use":
                            continue
                        has_tool_use = True
                        tool_name = str(c.get("name") or "").strip().lower()
                        if tool_name in {"askuserquestion", "request_user_input"}:
                            has_waiting_tool = True
                if has_tool_use:
                    if has_waiting_tool:
                        session.state = "WAITING"
                        session.last_event_type = "request_user_input"
                    else:
                        session.state = "TOOLING"
                        session.last_event_type = "agent_tool_call_begin"
                else:
                    stop_reason = str(
                        message.get("stop_reason") or event.get("stop_reason") or ""
                    ).strip().lower()
                    if stop_reason in {"end_turn", "stop_sequence"}:
                        session.state = "WAITING"
                    else:
                        session.state = "RESPONDING"

            elif event_type == "tool_result":
                session.last_event_type = "agent_tool_call_finish"
                # After tool result, model is thinking again.
                session.state = "THINKING"

            elif event_type == "result":
                session.last_event_type = "task_complete"
                session.state = "IDLE"
                # Extract token usage from result event.
                usage = event.get("usage") if isinstance(event.get("usage"), dict) else {}
                if isinstance(usage, dict):
                    inp = usage.get("input_tokens") or usage.get("input") or 0
                    out = usage.get("output_tokens") or usage.get("output") or 0
                    cache_read = usage.get("cache_read_input_tokens") or usage.get("cache_read") or 0
                    cache_create = usage.get("cache_creation_input_tokens") or usage.get("cache_creation") or 0
                    try:
                        total = int(inp) + int(out) + int(cache_read) + int(cache_create)
                        if total > 0:
                            session.total_tokens = total
                    except (ValueError, TypeError):
                        pass
                # Also check top-level total_tokens
                top_total = event.get("total_tokens")
                if top_total is not None:
                    coerced = self._coerce_non_negative_int(top_total)
                    if coerced is not None and coerced > 0:
                        session.total_tokens = coerced
                # Model context window
                for key in ("model_context_window", "context_window", "max_tokens"):
                    val = event.get(key)
                    if val is not None:
                        coerced = self._coerce_non_negative_int(val)
                        if coerced is not None and coerced > 0:
                            session.model_context_window = coerced
                            break

            elif event_type == "rate_limit_event":
                rate_info = event.get("rate_limit_info") if isinstance(event.get("rate_limit_info"), dict) else {}
                if isinstance(rate_info, dict):
                    utilization = rate_info.get("utilization")
                    if utilization is not None:
                        try:
                            remaining = max(0.0, 100.0 - float(utilization))
                            session.primary_rate_remaining_percent = round(remaining, 2)
                        except (ValueError, TypeError):
                            pass

            elif event_type == "summary":
                # Claude sometimes writes a summary entry with the conversation title
                summary_text = str(event.get("summary") or "").strip()
                if summary_text:
                    if not _is_auto_injected_message("user", summary_text) and not _is_auto_injected_message("assistant", summary_text):
                        session.has_real_user_input = True
                        if len(summary_text) > 42:
                            summary_text = summary_text[:39].rstrip() + "..."
                        session.display_name = summary_text

            elif event_type in {"last-prompt", "last_prompt"}:
                # Claude persists a rolling lastPrompt near file tail; prefer it as latest user-facing title.
                last_prompt = str(event.get("lastPrompt") or event.get("last_prompt") or "").strip()
                if last_prompt:
                    if not _is_auto_injected_message("user", last_prompt):
                        session.has_real_user_input = True
                        if len(last_prompt) > 42:
                            last_prompt = last_prompt[:39].rstrip() + "..."
                        session.display_name = last_prompt

            elif event_type == "queue-operation":
                operation = str(event.get("operation") or "").strip().lower()
                queued_content = str(event.get("content") or "").strip()
                if operation == "enqueue" and queued_content and not _is_auto_injected_message("user", queued_content):
                    session.has_real_user_input = True
                    if len(queued_content) > 42:
                        queued_content = queued_content[:39].rstrip() + "..."
                    session.display_name = queued_content
                    session.last_event_type = "user_message"
                    session.state = "THINKING"

    async def _consume_file(self, path: Path) -> None:
        key = str(path)
        stat = path.stat()
        cursor = self._files.get(key)
        if cursor is None:
            start_offset = max(stat.st_size - self.initial_read_bytes, 0)
            session_id_from_name = None
            matched = self._session_id_pattern.search(path.name)
            if matched:
                session_id_from_name = matched.group(0)
            cursor = _FileCursor(
                path=path,
                offset=start_offset,
                inode=stat.st_ino,
                session_id=session_id_from_name,
                align_line_on_next_read=(start_offset > 0),
            )
            self._files[key] = cursor
        elif cursor.inode != stat.st_ino or stat.st_size < cursor.offset:
            cursor.offset = 0
            cursor.inode = stat.st_ino
            cursor.align_line_on_next_read = False

        with path.open("r", encoding="utf-8", errors="ignore") as f:
            f.seek(cursor.offset)
            if cursor.align_line_on_next_read:
                f.readline()
                cursor.align_line_on_next_read = False
            for line in f:
                await self._consume_line(line, cursor)
            cursor.offset = f.tell()

    async def _consume_line(self, line: str, cursor: _FileCursor) -> None:
        text = line.strip()
        if not text:
            return
        try:
            event = json.loads(text)
        except json.JSONDecodeError:
            self.events_dropped_total += 1
            logger.warning("Session bridge dropped malformed json line in %s", cursor.path)
            return
        self.events_ingested_total += 1
        self.latest_event_epoch = time.time()
        await self._ingest_event(event, cursor)

    async def _ingest_event(self, event: dict[str, Any], cursor: _FileCursor) -> None:
        top_type = event.get("type", "")
        payload = _event_payload_or_item(event)
        parsed_ts = _event_timestamp_or_none(event)
        if parsed_ts is None:
            logger.debug("Ignoring Codex event without valid timestamp event=%s", top_type)
            return
        timestamp, event_epoch = parsed_ts
        now_epoch = time.time()

        if top_type == "session_meta":
            session_id = self._extract_session_id(event, payload, cursor)
            if not session_id:
                logger.warning("session_meta without session id: %s", event)
                return
            cursor.session_id = session_id
            async with self._sessions_lock:
                session = self._sessions.get(session_id)
                if session is None:
                    session = self._new_session(session_id, timestamp)
                    self._sessions[session_id] = session
                    logger.info("session online: %s", session_id)
                elif event_epoch < (session.last_seen_epoch - 1e-6):
                    # 舊 session_meta 事件不覆蓋較新的 last_seen。
                    return
                session.originator = str(payload.get("originator") or session.originator)
                session.cwd = str(payload.get("cwd") or session.cwd)
                session.cwd_basename = _basename_from_cwd(session.cwd)
                git_info = payload.get("git") if isinstance(payload.get("git"), dict) else {}
                if isinstance(git_info, dict):
                    branch = str(git_info.get("branch") or "").strip()
                    if branch:
                        session.branch = branch
                session.last_event_type = "session_meta"
                session.last_seen_at = timestamp
                session.last_seen_epoch = event_epoch
                session.active = (now_epoch - session.last_seen_epoch) < self.inactive_ttl_sec
                display_name = payload.get("display_name") or payload.get("name")
                if isinstance(display_name, str) and display_name.strip():
                    session.display_name = display_name.strip()
            return

        session_id = cursor.session_id or self._extract_session_id(event, payload, cursor)
        if not session_id:
            logger.warning("Session bridge dropped event without session id. type=%s", top_type)
            self.events_dropped_total += 1
            return
        cursor.session_id = session_id

        state, is_task_complete, wait_for_user = self._map_to_state(top_type, payload)
        event_type = str(payload.get("type") or top_type)
        emitted_event: Optional[dict[str, Any]] = None

        async with self._sessions_lock:
            session = self._sessions.get(session_id)
            if session is None:
                session = self._new_session(session_id, timestamp)
                self._sessions[session_id] = session
                logger.info("session online: %s", session_id)
            elif event_epoch < (session.last_seen_epoch - 1e-6):
                # 忽略倒序舊事件，避免重啟掃描時把過期 session 重新標成活躍。
                return

            session.last_seen_at = timestamp
            session.last_seen_epoch = event_epoch
            session.active = (now_epoch - session.last_seen_epoch) < self.inactive_ttl_sec
            session.last_event_type = event_type
            if session.cwd:
                session.cwd_basename = _basename_from_cwd(session.cwd)
            title = self._extract_title(top_type, payload)
            if title:
                session.display_name = title
            if top_type == "event_msg" and event_type == "user_message":
                user_message = str(payload.get("message") or "").strip()
                if user_message and not _is_auto_injected_message("user", user_message):
                    session.has_real_user_input = True
            elif top_type == "response_item" and event_type == "message" and str(payload.get("role") or "") == "user":
                user_content = _extract_message_content(payload)
                if user_content and not _is_auto_injected_message("user", user_content):
                    session.has_real_user_input = True

            if top_type == "turn_context":
                session.model = str(payload.get("model") or session.model or "")
                session.effort = str(payload.get("effort") or session.effort or "")
                session.approval_policy = str(payload.get("approval_policy") or session.approval_policy or "")
                sandbox = payload.get("sandbox_policy") if isinstance(payload.get("sandbox_policy"), dict) else {}
                if isinstance(sandbox, dict):
                    session.sandbox_mode = str(sandbox.get("type") or session.sandbox_mode or "")
                session.permission_mode = _resolve_permission_mode(
                    None,
                    approval_policy=session.approval_policy,
                    sandbox_mode=session.sandbox_mode,
                )
                cwd_from_turn = str(payload.get("cwd") or "").strip()
                if cwd_from_turn:
                    session.cwd = cwd_from_turn
                    session.cwd_basename = _basename_from_cwd(cwd_from_turn)
                collaboration = payload.get("collaboration_mode") if isinstance(payload.get("collaboration_mode"), dict) else {}
                if isinstance(collaboration, dict):
                    mode = str(collaboration.get("mode") or "").strip().lower()
                    if mode:
                        session.plan_mode = mode == "plan"
                        session.plan_mode_fallback = False
                emitted_event = self._build_state_event(session)
                state = None

            if top_type == "event_msg" and event_type == "token_count":
                info = payload.get("info") if isinstance(payload.get("info"), dict) else {}
                if isinstance(info, dict):
                    last_usage = info.get("last_token_usage") if isinstance(info.get("last_token_usage"), dict) else {}
                    total_tokens = self._coerce_non_negative_int(
                        last_usage.get("total_tokens") if isinstance(last_usage, dict) else None
                    )
                    model_context_window = self._coerce_non_negative_int(info.get("model_context_window"))
                    rate_limits = payload.get("rate_limits") if isinstance(payload.get("rate_limits"), dict) else {}
                    primary_remaining = self._remaining_percent_from_rate_limit(
                        rate_limits.get("primary") if isinstance(rate_limits, dict) else None
                    )
                    secondary_remaining = self._remaining_percent_from_rate_limit(
                        rate_limits.get("secondary") if isinstance(rate_limits, dict) else None
                    )
                    if total_tokens is not None:
                        session.total_tokens = total_tokens
                    if model_context_window is not None:
                        session.model_context_window = model_context_window
                    if primary_remaining is not None:
                        session.primary_rate_remaining_percent = primary_remaining
                    if secondary_remaining is not None:
                        session.secondary_rate_remaining_percent = secondary_remaining
                    emitted_event = self._build_state_event(session)
                    state = None

            if is_task_complete:
                if session.pending_state == "RESPONDING" and session.state != "RESPONDING":
                    session.state = "RESPONDING"
                    session.last_state_change_mono = time.monotonic()
                    session.pending_state = None
                    session.pending_due_mono = 0.0
                    emitted_event = self._build_state_event(session)
                    session.idle_due_epoch = session.last_seen_epoch + self.idle_after_task_complete_sec
                elif session.state in {"THINKING", "TOOLING"}:
                    session.state = "IDLE"
                    session.last_state_change_mono = time.monotonic()
                    session.pending_state = None
                    session.pending_due_mono = 0.0
                    session.idle_due_epoch = None
                    emitted_event = self._build_state_event(session)
                else:
                    session.idle_due_epoch = session.last_seen_epoch + self.idle_after_task_complete_sec
            elif state in {"THINKING", "TOOLING", "RESPONDING", "WAITING"} or wait_for_user:
                # New active turn activity cancels any previous idle fallback countdown.
                session.idle_due_epoch = None

            if state in SESSION_STATES:
                previous_state = session.state
                if self._schedule_state_transition(session, state):
                    if previous_state != session.state:
                        logger.info("session state: %s %s -> %s", session_id, previous_state, session.state)
                    emitted_event = self._build_state_event(session)

            if wait_for_user and session.state != "WAITING":
                previous_state = session.state
                session.pending_state = None
                session.state = "WAITING"
                session.last_state_change_mono = time.monotonic()
                logger.info("session state: %s %s -> WAITING", session_id, previous_state)
                emitted_event = self._build_state_event(session)

        if emitted_event:
            await self._broadcast(emitted_event)
        elif state is None and not is_task_complete:
            logger.warning(
                "Session bridge ignored unknown event: top_type=%s payload_type=%s",
                top_type,
                payload.get("type"),
            )

    def _extract_title(self, top_type: str, payload: dict[str, Any]) -> str:
        text = ""
        payload_type = str(payload.get("type") or "")
        if top_type == "event_msg" and payload_type == "user_message":
            text = str(payload.get("message") or "")
        elif top_type == "response_item" and payload_type == "message" and str(payload.get("role") or "") == "user":
            content = payload.get("content")
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                chunks: list[str] = []
                for item in content:
                    if not isinstance(item, dict):
                        continue
                    candidate = item.get("text")
                    if not isinstance(candidate, str) or not candidate.strip():
                        candidate = item.get("content")
                    if isinstance(candidate, str) and candidate.strip():
                        chunks.append(candidate.strip())
                if chunks:
                    text = " ".join(chunks)
            if not text:
                text = str(payload.get("message") or "")
        cleaned = WHITESPACE_PATTERN.sub(" ", text).strip()
        if not cleaned:
            return ""
        if _is_auto_injected_message("user", cleaned):
            return ""
        max_len = 42
        if len(cleaned) > max_len:
            return cleaned[: max_len - 3].rstrip() + "..."
        return cleaned

    def _new_session(self, session_id: str, ts: str) -> _SessionRecord:
        ts_epoch = _ts_to_epoch(ts)
        return _SessionRecord(
            session_id=session_id,
            display_name=f"session-{session_id[:8]}",
            last_seen_at=ts,
            last_seen_epoch=ts_epoch,
            active=(time.time() - ts_epoch) < self.inactive_ttl_sec,
        )

    @staticmethod
    def _context_payload(session: _SessionRecord) -> dict[str, Any]:
        return {
            "model": session.model,
            "effort": session.effort,
            "permission_mode": _normalize_permission_mode(session.permission_mode),
            "approval_policy": session.approval_policy,
            "sandbox_mode": session.sandbox_mode,
            "plan_mode": session.plan_mode,
            "plan_mode_fallback": session.plan_mode_fallback,
            "total_tokens": session.total_tokens,
            "model_context_window": session.model_context_window,
            "primary_rate_remaining_percent": session.primary_rate_remaining_percent,
            "secondary_rate_remaining_percent": session.secondary_rate_remaining_percent,
        }

    @staticmethod
    def _coerce_non_negative_int(value: Any) -> Optional[int]:
        if value is None:
            return None
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, (int, float)):
            if isinstance(value, float) and value != value:
                return None
            return max(0, int(value))
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return None
            try:
                return max(0, int(float(text)))
            except ValueError:
                return None
        return None

    @staticmethod
    def _coerce_percent(value: Any) -> Optional[float]:
        if value is None:
            return None
        if isinstance(value, bool):
            return float(int(value))
        if isinstance(value, (int, float)):
            if isinstance(value, float) and value != value:
                return None
            return max(0.0, min(100.0, float(value)))
        if isinstance(value, str):
            text = value.strip().replace("%", "")
            if not text:
                return None
            try:
                return max(0.0, min(100.0, float(text)))
            except ValueError:
                return None
        return None

    @classmethod
    def _remaining_percent_from_rate_limit(cls, item: Any) -> Optional[float]:
        if not isinstance(item, dict):
            return None
        used = cls._coerce_percent(item.get("used_percent"))
        if used is None:
            return None
        return max(0.0, min(100.0, 100.0 - used))

    def _map_to_state(self, top_type: str, payload: dict[str, Any]) -> tuple[Optional[str], bool, bool]:
        payload_type = str(payload.get("type") or "")

        if top_type == "event_msg":
            if payload_type == "agent_reasoning":
                return "THINKING", False, False
            if payload_type == "agent_message":
                return "RESPONDING", True, False
            if payload_type == "task_complete":
                return None, True, False

        if top_type == "response_item":
            if payload_type == "reasoning":
                return "THINKING", False, False
            if payload_type in {"function_call", "custom_tool_call"}:
                name = str(payload.get("name") or "")
                args_text = json.dumps(payload.get("arguments", ""), ensure_ascii=False)
                wait_for_user = name == "request_user_input" or "request_user_input" in args_text
                return ("WAITING" if wait_for_user else "TOOLING"), False, wait_for_user
            if payload_type == "message" and str(payload.get("role") or "") == "assistant":
                return "RESPONDING", True, False

        if top_type == "item.completed":
            item_type = str(payload.get("type") or "")
            if item_type in {"agent_message", "assistant_message"}:
                return "RESPONDING", True, False
            if item_type == "message" and str(payload.get("role") or "").lower() == "assistant":
                return "RESPONDING", True, False

        if top_type == "custom_tool_call":
            name = str(payload.get("name") or "")
            wait_for_user = name == "request_user_input"
            return ("WAITING" if wait_for_user else "TOOLING"), False, wait_for_user

        return None, False, False

    def _extract_session_id(
        self,
        event: dict[str, Any],
        payload: dict[str, Any],
        cursor: _FileCursor,
    ) -> Optional[str]:
        candidates: list[Any] = [
            event.get("session_id"),
            event.get("sessionId"),
            payload.get("session_id"),
            payload.get("sessionId"),
        ]
        session_payload = payload.get("session")
        if isinstance(session_payload, dict):
            candidates.extend([session_payload.get("id"), session_payload.get("session_id")])

        if cursor.session_id:
            candidates.append(cursor.session_id)

        file_match = self._session_id_pattern.search(cursor.path.name)
        if file_match:
            candidates.append(file_match.group(0))
        path_match = self._session_id_pattern.search(str(cursor.path))
        if path_match:
            candidates.append(path_match.group(0))

        for candidate in candidates:
            if not isinstance(candidate, str):
                continue
            normalized = candidate.strip()
            if normalized and self._session_id_pattern.fullmatch(normalized):
                return normalized
        return None

    def _schedule_state_transition(self, session: _SessionRecord, new_state: str) -> bool:
        if session.state == new_state:
            session.pending_state = None
            session.pending_due_mono = 0.0
            return False

        now_mono = time.monotonic()
        if now_mono - session.last_state_change_mono >= self.min_state_duration_sec:
            session.state = new_state
            session.last_state_change_mono = now_mono
            session.pending_state = None
            session.pending_due_mono = 0.0
            return True

        if session.pending_state is None:
            session.pending_state = new_state
        elif STATE_PRIORITY[new_state] > STATE_PRIORITY[session.pending_state]:
            session.pending_state = new_state

        session.pending_due_mono = session.last_state_change_mono + self.min_state_duration_sec
        return False

    def _build_state_event(self, session: _SessionRecord, inactive: bool = False) -> dict[str, Any]:
        meta: dict[str, Any] = {
            "originator": session.originator,
            "cwd": session.cwd,
            "cwd_basename": session.cwd_basename,
            "last_event_type": session.last_event_type,
            "branch": session.branch,
            "context": self._context_payload(session),
        }
        if inactive:
            meta["inactive"] = True

        return {
            "version": "1",
            "event": "session_state",
            "session_id": session.session_id,
            "display_name": session.display_name,
            "state": session.state,
            "ts": session.last_seen_at,
            "source": "codex_jsonl",
            "agent_brand": getattr(session, "agent_brand", "codex"),
            "has_real_user_input": bool(getattr(session, "has_real_user_input", False)),
            "meta": meta,
        }

    async def _broadcast(self, event: dict[str, Any]) -> None:
        async with self._clients_lock:
            clients = list(self._clients)
        if not clients:
            return

        stale: list[WebSocket] = []
        for ws in clients:
            try:
                await ws.send_json(event)
            except Exception:
                stale.append(ws)

        if stale:
            async with self._clients_lock:
                for ws in stale:
                    if ws in self._clients:
                        self._clients.remove(ws)
