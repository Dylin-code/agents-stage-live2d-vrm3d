import asyncio
import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from live2d_server.session_bridge import (
    AgentChatApprovalRequest,
    AgentChatRequest,
    AgentConversationRequest,
    AgentNewSessionRequest,
    AgentProviderRouter,
    CodexChatApprovalRequest,
    CodexChatRequest,
    CodexConversationRequest,
    CodexNewSessionRequest,
    CodexSessionChatError,
    CodexSessionChatService,
    GitBranchSwitchRequest,
    SessionBridgeService,
    _FileCursor,
    _SessionRecord,
    bridge_codex_chat,
    bridge_codex_chat_approval,
    bridge_codex_new_session,
    bridge_agent_chat_approval,
    bridge_agent_brands,
    bridge_conversation,
    bridge_git_branches,
    bridge_git_switch,
)


class _Completed:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class _FakeCreateProcess:
    def __init__(self, returncode: int, stdout: bytes = b"", stderr: bytes = b"") -> None:
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr
        self.killed = False

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._stdout, self._stderr

    def kill(self) -> None:
        self.killed = True

    async def wait(self) -> int:
        return self.returncode


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class _FakeStreamReader:
    def __init__(self, lines: list[bytes]) -> None:
        self._lines = list(lines)

    async def readline(self) -> bytes:
        if self._lines:
            return self._lines.pop(0)
        return b""


class _FakeBytesReader:
    def __init__(self, content: bytes = b"") -> None:
        self._content = content

    async def read(self) -> bytes:
        return self._content


class _FakeStreamProcess:
    def __init__(self, stdout_lines: list[bytes], returncode: int = 0, stderr: bytes = b"") -> None:
        self.stdout = _FakeStreamReader(stdout_lines)
        self.stderr = _FakeBytesReader(stderr)
        self.returncode = returncode
        self.killed = False

    def kill(self) -> None:
        self.killed = True

    async def wait(self) -> int:
        return self.returncode


async def _collect_stream_body(response) -> str:
    chunks = []
    async for item in response.body_iterator:
        if isinstance(item, bytes):
            chunks.append(item.decode("utf-8"))
        else:
            chunks.append(str(item))
    return "".join(chunks)


class SessionBridgeServiceTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.service = SessionBridgeService()
        self.service.inactive_ttl_sec = 600

    async def test_map_to_state_rules(self) -> None:
        self.assertEqual(self.service._map_to_state("event_msg", {"type": "agent_reasoning"}), ("THINKING", False, False))
        self.assertEqual(self.service._map_to_state("response_item", {"type": "reasoning"}), ("THINKING", False, False))
        self.assertEqual(
            self.service._map_to_state("response_item", {"type": "function_call", "name": "exec_command"}),
            ("TOOLING", False, False),
        )
        self.assertEqual(
            self.service._map_to_state("response_item", {"type": "function_call", "name": "request_user_input"}),
            ("WAITING", False, True),
        )
        self.assertEqual(
            self.service._map_to_state("response_item", {"type": "message", "role": "assistant"}),
            ("RESPONDING", True, False),
        )
        self.assertEqual(
            self.service._map_to_state("event_msg", {"type": "agent_message"}),
            ("RESPONDING", True, False),
        )
        self.assertEqual(
            self.service._map_to_state("item.completed", {"type": "message", "role": "assistant"}),
            ("RESPONDING", True, False),
        )

    async def test_token_count_does_not_cancel_idle_due_countdown(self) -> None:
        session_id = "00000000-0000-0000-0000-000000000021"
        now = "2026-03-08T12:00:00Z"
        self.service._sessions[session_id] = _SessionRecord(
            session_id=session_id,
            display_name="session-00000000",
            state="RESPONDING",
            last_seen_at=now,
            last_seen_epoch=0.0,
            idle_due_epoch=123.0,
        )
        with TemporaryDirectory() as temp_dir:
            cursor = _FileCursor(
                path=Path(temp_dir) / f"{session_id}.jsonl",
                offset=0,
                inode=1,
                session_id=session_id,
            )
            await self.service._ingest_event(
                {
                    "timestamp": now,
                    "type": "event_msg",
                    "payload": {
                        "type": "token_count",
                        "info": {"last_token_usage": {"total_tokens": 10}},
                    },
                },
                cursor,
            )
        self.assertEqual(self.service._sessions[session_id].idle_due_epoch, 123.0)

    async def test_active_event_cancels_existing_idle_due_countdown(self) -> None:
        session_id = "00000000-0000-0000-0000-000000000022"
        now = "2026-03-08T12:00:00Z"
        self.service._sessions[session_id] = _SessionRecord(
            session_id=session_id,
            display_name="session-00000000",
            state="RESPONDING",
            last_seen_at=now,
            last_seen_epoch=0.0,
            idle_due_epoch=123.0,
        )
        with TemporaryDirectory() as temp_dir:
            cursor = _FileCursor(
                path=Path(temp_dir) / f"{session_id}.jsonl",
                offset=0,
                inode=1,
                session_id=session_id,
            )
            await self.service._ingest_event(
                {
                    "timestamp": now,
                    "type": "response_item",
                    "payload": {"type": "reasoning"},
                },
                cursor,
            )
        self.assertIsNone(self.service._sessions[session_id].idle_due_epoch)

    async def test_task_complete_clears_stale_thinking_state_immediately(self) -> None:
        session_id = "00000000-0000-0000-0000-000000000023"
        now = "2026-03-08T12:00:00Z"
        self.service._sessions[session_id] = _SessionRecord(
            session_id=session_id,
            display_name="session-00000000",
            state="THINKING",
            last_seen_at=now,
            last_seen_epoch=0.0,
        )
        with TemporaryDirectory() as temp_dir:
            cursor = _FileCursor(
                path=Path(temp_dir) / f"{session_id}.jsonl",
                offset=0,
                inode=1,
                session_id=session_id,
            )
            await self.service._ingest_event(
                {
                    "timestamp": now,
                    "type": "event_msg",
                    "payload": {"type": "task_complete"},
                },
                cursor,
            )
        self.assertEqual(self.service._sessions[session_id].state, "IDLE")
        self.assertIsNone(self.service._sessions[session_id].idle_due_epoch)

    async def test_item_completed_clears_stale_thinking_state_immediately(self) -> None:
        session_id = "00000000-0000-0000-0000-000000000024"
        now = "2026-03-08T12:00:00Z"
        self.service._sessions[session_id] = _SessionRecord(
            session_id=session_id,
            display_name="session-00000000",
            state="THINKING",
            last_seen_at=now,
            last_seen_epoch=0.0,
        )
        with TemporaryDirectory() as temp_dir:
            cursor = _FileCursor(
                path=Path(temp_dir) / f"{session_id}.jsonl",
                offset=0,
                inode=1,
                session_id=session_id,
            )
            await self.service._ingest_event(
                {
                    "timestamp": now,
                    "type": "item.completed",
                    "item": {"type": "message", "role": "assistant", "content": "done"},
                },
                cursor,
            )
        self.assertEqual(self.service._sessions[session_id].state, "IDLE")
        self.assertIsNotNone(self.service._sessions[session_id].idle_due_epoch)

    async def test_history_contains_context_and_cwd_fields(self) -> None:
        with TemporaryDirectory() as temp_dir:
            session_dir = Path(temp_dir)
            self.service.session_dir = session_dir
            session_id = "00000000-0000-0000-0000-000000000001"
            file_path = session_dir / "2026" / "02" / "27" / f"{session_id}.jsonl"
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(
                "\n".join(
                    [
                        '{"timestamp":"2026-02-27T09:00:00Z","type":"session_meta","payload":{"id":"%s","display_name":"demo","originator":"Codex Desktop","cwd":"/tmp/work","git":{"branch":"main"}}}'
                        % session_id,
                        '{"timestamp":"2026-02-27T09:00:01Z","type":"turn_context","payload":{"model":"gpt-5-codex","effort":"high","approval_policy":"on-request","sandbox_policy":{"type":"workspace-write"},"collaboration_mode":{"mode":"plan"}}}',
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            with patch("live2d_server.session_bridge.subprocess.run", return_value=_Completed(returncode=0, stdout="main\n")):
                history = await self.service.get_history(limit=20)
        self.assertEqual(len(history["sessions"]), 1)
        session = history["sessions"][0]
        self.assertEqual(session["cwd"], "/tmp/work")
        self.assertEqual(session["cwd_basename"], "work")
        self.assertEqual(session["branch"], "main")
        self.assertEqual(session["context"]["model"], "gpt-5-codex")
        self.assertEqual(session["context"]["effort"], "high")
        self.assertEqual(session["context"]["permission_mode"], "default")
        self.assertEqual(session["context"]["approval_policy"], "on-request")
        self.assertEqual(session["context"]["sandbox_mode"], "workspace-write")
        self.assertTrue(session["context"]["plan_mode"])

    async def test_history_extracts_last_token_usage_for_context_window(self) -> None:
        with TemporaryDirectory() as temp_dir:
            session_dir = Path(temp_dir)
            self.service.session_dir = session_dir
            session_id = "00000000-0000-0000-0000-000000000002"
            file_path = session_dir / "2026" / "03" / "02" / f"{session_id}.jsonl"
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(
                "\n".join(
                    [
                        '{"timestamp":"2026-03-02T06:38:17.300Z","type":"session_meta","payload":{"id":"%s","display_name":"demo","originator":"Codex Desktop","cwd":"/tmp/work"}}'
                        % session_id,
                        '{"timestamp":"2026-03-02T06:38:17.319Z","type":"event_msg","payload":{"type":"token_count","info":{"last_token_usage":{"total_tokens":66555},"model_context_window":258400},"rate_limits":{"primary":{"used_percent":16.0},"secondary":{"used_percent":68.0}}}}',
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            history = await self.service.get_history(limit=20)
        self.assertEqual(len(history["sessions"]), 1)
        context = history["sessions"][0]["context"]
        self.assertEqual(context["total_tokens"], 66555)
        self.assertEqual(context["model_context_window"], 258400)
        self.assertEqual(context["primary_rate_remaining_percent"], 84.0)
        self.assertEqual(context["secondary_rate_remaining_percent"], 32.0)

    async def test_ingest_event_updates_session_context_from_token_count(self) -> None:
        session_id = "00000000-0000-0000-0000-000000000004"
        now = _now_iso()
        with TemporaryDirectory() as temp_dir:
            cursor = _FileCursor(
                path=Path(temp_dir) / f"{session_id}.jsonl",
                offset=0,
                inode=1,
                session_id=session_id,
            )
            await self.service._ingest_event(
                {
                    "timestamp": now,
                    "type": "event_msg",
                    "payload": {
                        "type": "token_count",
                        "info": {
                            "last_token_usage": {"total_tokens": 66555},
                            "model_context_window": 258400,
                        },
                        "rate_limits": {
                            "primary": {"used_percent": 16.0},
                            "secondary": {"used_percent": 68.0},
                        },
                    },
                },
                cursor,
            )
        snapshot = await self.service.get_snapshot()
        self.assertEqual(len(snapshot["sessions"]), 1)
        context = snapshot["sessions"][0]["context"]
        self.assertEqual(context["total_tokens"], 66555)
        self.assertEqual(context["model_context_window"], 258400)
        self.assertEqual(context["primary_rate_remaining_percent"], 84.0)
        self.assertEqual(context["secondary_rate_remaining_percent"], 32.0)

    async def test_request_user_input_switches_waiting_immediately(self) -> None:
        session_id = "00000000-0000-0000-0000-000000000003"
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        self.service._sessions[session_id] = _SessionRecord(
            session_id=session_id,
            display_name="session-00000000",
            state="RESPONDING",
            last_seen_at=now,
            last_seen_epoch=0,
        )
        with TemporaryDirectory() as temp_dir:
            cursor = _FileCursor(
                path=Path(temp_dir) / f"{session_id}.jsonl",
                offset=0,
                inode=1,
                session_id=session_id,
            )
            await self.service._ingest_event(
                {
                    "timestamp": now,
                    "type": "response_item",
                    "payload": {"type": "function_call", "name": "request_user_input", "arguments": {"questions": []}},
                },
                cursor,
            )
        self.assertEqual(self.service._sessions[session_id].state, "WAITING")

    async def test_get_conversation_filters_auto_injected_bootstrap_messages(self) -> None:
        session_id = "00000000-0000-0000-0000-000000000010"
        with TemporaryDirectory() as codex_dir, TemporaryDirectory() as claude_dir:
            self.service.session_dir = Path(codex_dir)
            self.service.claude_session_dir = Path(claude_dir)
            file_path = self.service.session_dir / "2026" / "03" / "08" / f"{session_id}.jsonl"
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(
                "\n".join(
                    [
                        '{"timestamp":"2026-03-08T10:00:00Z","type":"event_msg","payload":{"type":"user_message","message":"Initialize a new codex session. Reply with: SESSION_READY"}}',
                        '{"timestamp":"2026-03-08T10:00:01Z","type":"response_item","payload":{"type":"message","role":"assistant","content":"SESSION_READY"}}',
                        '{"timestamp":"2026-03-08T10:00:02Z","type":"event_msg","payload":{"type":"user_message","message":"幫我修掉 websocket reconnect bug"}}',
                        '{"timestamp":"2026-03-08T10:00:03Z","type":"response_item","payload":{"type":"message","role":"assistant","content":"收到，我先檢查重連流程。"}}',
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            payload = await self.service.get_conversation(session_id, limit=50)
        self.assertEqual(len(payload["messages"]), 2)
        self.assertEqual(payload["messages"][0]["role"], "user")
        self.assertEqual(payload["messages"][0]["content"], "幫我修掉 websocket reconnect bug")
        self.assertEqual(payload["messages"][1]["role"], "assistant")
        self.assertEqual(payload["messages"][1]["content"], "收到，我先檢查重連流程。")

    async def test_history_title_ignores_auto_injected_bootstrap_prompt(self) -> None:
        session_id = "00000000-0000-0000-0000-000000000011"
        with TemporaryDirectory() as codex_dir, TemporaryDirectory() as claude_dir:
            self.service.session_dir = Path(codex_dir)
            self.service.claude_session_dir = Path(claude_dir)
            file_path = self.service.session_dir / "2026" / "03" / "08" / f"{session_id}.jsonl"
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(
                "\n".join(
                    [
                        '{"timestamp":"2026-03-08T10:10:00Z","type":"event_msg","payload":{"type":"user_message","message":"Initialize a new codex session. Reply with: SESSION_READY"}}',
                        '{"timestamp":"2026-03-08T10:10:01Z","type":"event_msg","payload":{"type":"user_message","message":"請幫我整理今天待辦"}}',
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            history = await self.service.get_history(limit=20)
        self.assertEqual(len(history["sessions"]), 1)
        self.assertEqual(history["sessions"][0]["display_name"], "請幫我整理今天待辦")

    async def test_history_title_ignores_agents_md_instructions_and_apply_patch_warning(self) -> None:
        session_id = "00000000-0000-0000-0000-000000000012"
        with TemporaryDirectory() as codex_dir, TemporaryDirectory() as claude_dir:
            self.service.session_dir = Path(codex_dir)
            self.service.claude_session_dir = Path(claude_dir)
            file_path = self.service.session_dir / "2026" / "03" / "08" / f"{session_id}.jsonl"
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(
                "\n".join(
                    [
                        '{"timestamp":"2026-03-08T10:20:00Z","type":"event_msg","payload":{"type":"user_message","message":"# AGENTS.md Instructions for /Users/dan..."}}',
                        '{"timestamp":"2026-03-08T10:20:01Z","type":"event_msg","payload":{"type":"user_message","message":"Warning: apply_patch was requested via..."}}',
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            history = await self.service.get_history(limit=20)
        self.assertEqual(len(history["sessions"]), 1)
        self.assertTrue(history["sessions"][0]["display_name"].startswith("session-"))

    async def test_get_conversation_filters_agents_md_instructions_and_apply_patch_warning(self) -> None:
        session_id = "00000000-0000-0000-0000-000000000013"
        with TemporaryDirectory() as codex_dir, TemporaryDirectory() as claude_dir:
            self.service.session_dir = Path(codex_dir)
            self.service.claude_session_dir = Path(claude_dir)
            file_path = self.service.session_dir / "2026" / "03" / "08" / f"{session_id}.jsonl"
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(
                "\n".join(
                    [
                        '{"timestamp":"2026-03-08T10:30:00Z","type":"event_msg","payload":{"type":"user_message","message":"# AGENTS.md Instructions for /Users/dan..."}}',
                        '{"timestamp":"2026-03-08T10:30:01Z","type":"event_msg","payload":{"type":"user_message","message":"Warning: apply_patch was requested via..."}}',
                        '{"timestamp":"2026-03-08T10:30:02Z","type":"event_msg","payload":{"type":"user_message","message":"真正的使用者輸入"}}',
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            payload = await self.service.get_conversation(session_id, limit=50)
        self.assertEqual(len(payload["messages"]), 1)
        self.assertEqual(payload["messages"][0]["content"], "真正的使用者輸入")

    async def test_get_conversation_filters_tool_loaded_noise(self) -> None:
        session_id = "00000000-0000-0000-0000-000000000015"
        with TemporaryDirectory() as codex_dir, TemporaryDirectory() as claude_dir:
            self.service.session_dir = Path(codex_dir)
            self.service.claude_session_dir = Path(claude_dir)
            file_path = self.service.session_dir / "2026" / "03" / "08" / f"{session_id}.jsonl"
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(
                "\n".join(
                    [
                        '{"timestamp":"2026-03-08T12:00:00Z","type":"event_msg","payload":{"type":"user_message","message":"Tool loaded."}}',
                        '{"timestamp":"2026-03-08T12:00:01Z","type":"response_item","payload":{"type":"message","role":"assistant","content":"Tool loaded."}}',
                        '{"timestamp":"2026-03-08T12:00:02Z","type":"event_msg","payload":{"type":"user_message","message":"請開始"}}',
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            payload = await self.service.get_conversation(session_id, limit=50)
        self.assertEqual(len(payload["messages"]), 1)
        self.assertEqual(payload["messages"][0]["content"], "請開始")

    async def test_claude_ingest_does_not_use_auto_injected_text_as_display_name(self) -> None:
        session_id = "00000000-0000-0000-0000-000000000014"
        now = _now_iso()
        with TemporaryDirectory() as temp_dir:
            cursor = _FileCursor(
                path=Path(temp_dir) / f"{session_id}.jsonl",
                offset=0,
                inode=1,
                session_id=session_id,
            )
            line = json.dumps(
                {
                    "type": "user",
                    "sessionId": session_id,
                    "timestamp": now,
                    "cwd": "/tmp/work",
                    "message": {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "# AGENTS.md Instructions for /Users/dan..."},
                        ],
                    },
                },
                ensure_ascii=False,
            )
            await self.service._ingest_claude_line(line, cursor)
        snapshot = await self.service.get_snapshot()
        self.assertEqual(len(snapshot["sessions"]), 1)
        self.assertEqual(snapshot["sessions"][0]["display_name"], f"session-{session_id[:8]}")

    async def test_claude_summary_tool_loaded_does_not_override_display_name(self) -> None:
        session_id = "00000000-0000-0000-0000-000000000016"
        now = _now_iso()
        with TemporaryDirectory() as temp_dir:
            cursor = _FileCursor(
                path=Path(temp_dir) / f"{session_id}.jsonl",
                offset=0,
                inode=1,
                session_id=session_id,
            )
            line = json.dumps(
                {
                    "type": "summary",
                    "sessionId": session_id,
                    "timestamp": now,
                    "cwd": "/tmp/work",
                    "summary": "Tool loaded.",
                },
                ensure_ascii=False,
            )
            await self.service._ingest_claude_line(line, cursor)
        snapshot = await self.service.get_snapshot()
        self.assertEqual(len(snapshot["sessions"]), 1)
        self.assertEqual(snapshot["sessions"][0]["display_name"], f"session-{session_id[:8]}")

    async def test_claude_last_prompt_updates_display_name(self) -> None:
        session_id = "00000000-0000-0000-0000-000000000017"
        now = _now_iso()
        with TemporaryDirectory() as temp_dir:
            cursor = _FileCursor(
                path=Path(temp_dir) / f"{session_id}.jsonl",
                offset=0,
                inode=1,
                session_id=session_id,
            )
            line = json.dumps(
                {
                    "type": "last-prompt",
                    "sessionId": session_id,
                    "timestamp": now,
                    "lastPrompt": "再試一次看看 我調整了權限",
                },
                ensure_ascii=False,
            )
            await self.service._ingest_claude_line(line, cursor)
        snapshot = await self.service.get_snapshot()
        self.assertEqual(len(snapshot["sessions"]), 1)
        self.assertEqual(snapshot["sessions"][0]["display_name"], "再試一次看看 我調整了權限")

    async def test_claude_queue_enqueue_updates_display_name_and_state(self) -> None:
        session_id = "00000000-0000-0000-0000-000000000017"
        now = _now_iso()
        with TemporaryDirectory() as temp_dir:
            cursor = _FileCursor(
                path=Path(temp_dir) / f"{session_id}.jsonl",
                offset=0,
                inode=1,
                session_id=session_id,
            )
            line = json.dumps(
                {
                    "type": "queue-operation",
                    "operation": "enqueue",
                    "timestamp": now,
                    "sessionId": session_id,
                    "content": "你是誰",
                },
                ensure_ascii=False,
            )
            await self.service._ingest_claude_line(line, cursor)
        snapshot = await self.service.get_snapshot()
        self.assertEqual(len(snapshot["sessions"]), 1)
        self.assertEqual(snapshot["sessions"][0]["display_name"], "你是誰")
        self.assertEqual(snapshot["sessions"][0]["state"], "THINKING")

    async def test_claude_invalid_timestamp_line_is_ignored(self) -> None:
        session_id = "00000000-0000-0000-0000-000000000017"
        with TemporaryDirectory() as temp_dir:
            cursor = _FileCursor(
                path=Path(temp_dir) / f"{session_id}.jsonl",
                offset=0,
                inode=1,
                session_id=session_id,
            )
            line = json.dumps(
                {
                    "type": "queue-operation",
                    "operation": "enqueue",
                    "timestamp": "",
                    "sessionId": session_id,
                    "content": "你是誰",
                },
                ensure_ascii=False,
            )
            await self.service._ingest_claude_line(line, cursor)
        snapshot = await self.service.get_snapshot()
        self.assertEqual(snapshot["sessions"], [])

    async def test_claude_assistant_end_turn_maps_to_idle(self) -> None:
        session_id = "00000000-0000-0000-0000-000000000018"
        now = _now_iso()
        with TemporaryDirectory() as temp_dir:
            cursor = _FileCursor(
                path=Path(temp_dir) / f"{session_id}.jsonl",
                offset=0,
                inode=1,
                session_id=session_id,
            )
            line = json.dumps(
                {
                    "type": "assistant",
                    "sessionId": session_id,
                    "timestamp": now,
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "完成"}],
                        "stop_reason": "end_turn",
                    },
                },
                ensure_ascii=False,
            )
            await self.service._ingest_claude_line(line, cursor)
        snapshot = await self.service.get_snapshot()
        self.assertEqual(len(snapshot["sessions"]), 1)
        self.assertEqual(snapshot["sessions"][0]["state"], "IDLE")

    async def test_claude_assistant_thinking_block_maps_to_thinking(self) -> None:
        session_id = "00000000-0000-0000-0000-00000000001a"
        now = _now_iso()
        with TemporaryDirectory() as temp_dir:
            cursor = _FileCursor(
                path=Path(temp_dir) / f"{session_id}.jsonl",
                offset=0,
                inode=1,
                session_id=session_id,
            )
            line = json.dumps(
                {
                    "type": "assistant",
                    "sessionId": session_id,
                    "timestamp": now,
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "thinking", "thinking": "先分析目前狀況"}],
                        "stop_reason": None,
                    },
                },
                ensure_ascii=False,
            )
            await self.service._ingest_claude_line(line, cursor)
        snapshot = await self.service.get_snapshot()
        self.assertEqual(len(snapshot["sessions"]), 1)
        self.assertEqual(snapshot["sessions"][0]["state"], "THINKING")

    async def test_claude_assistant_tool_use_maps_to_tooling(self) -> None:
        session_id = "00000000-0000-0000-0000-00000000001b"
        now = _now_iso()
        with TemporaryDirectory() as temp_dir:
            cursor = _FileCursor(
                path=Path(temp_dir) / f"{session_id}.jsonl",
                offset=0,
                inode=1,
                session_id=session_id,
            )
            line = json.dumps(
                {
                    "type": "assistant",
                    "sessionId": session_id,
                    "timestamp": now,
                    "message": {
                        "role": "assistant",
                        "content": [
                            {"type": "thinking", "thinking": "我要先查專案"},
                            {"type": "tool_use", "name": "Bash", "id": "tool-1", "input": {"command": "pwd"}},
                        ],
                    },
                },
                ensure_ascii=False,
            )
            await self.service._ingest_claude_line(line, cursor)
        snapshot = await self.service.get_snapshot()
        self.assertEqual(len(snapshot["sessions"]), 1)
        self.assertEqual(snapshot["sessions"][0]["state"], "TOOLING")

    async def test_claude_assistant_text_without_stop_reason_maps_to_responding(self) -> None:
        session_id = "00000000-0000-0000-0000-00000000001c"
        now = _now_iso()
        with TemporaryDirectory() as temp_dir:
            cursor = _FileCursor(
                path=Path(temp_dir) / f"{session_id}.jsonl",
                offset=0,
                inode=1,
                session_id=session_id,
            )
            line = json.dumps(
                {
                    "type": "assistant",
                    "sessionId": session_id,
                    "timestamp": now,
                    "message": {
                        "role": "assistant",
                        "content": [
                            {"type": "thinking", "thinking": "整理答案"},
                            {"type": "text", "text": "我先幫你檢查後端映射"},
                        ],
                        "stop_reason": None,
                    },
                },
                ensure_ascii=False,
            )
            await self.service._ingest_claude_line(line, cursor)
        snapshot = await self.service.get_snapshot()
        self.assertEqual(len(snapshot["sessions"]), 1)
        self.assertEqual(snapshot["sessions"][0]["state"], "RESPONDING")

    async def test_claude_ask_user_question_maps_to_waiting(self) -> None:
        session_id = "00000000-0000-0000-0000-000000000019"
        now = _now_iso()
        with TemporaryDirectory() as temp_dir:
            cursor = _FileCursor(
                path=Path(temp_dir) / f"{session_id}.jsonl",
                offset=0,
                inode=1,
                session_id=session_id,
            )
            line = json.dumps(
                {
                    "type": "assistant",
                    "sessionId": session_id,
                    "timestamp": now,
                    "message": {
                        "role": "assistant",
                        "content": [
                            {"type": "tool_use", "name": "AskUserQuestion", "id": "tool-1", "input": {"question": "?"}},
                        ],
                    },
                },
                ensure_ascii=False,
            )
            await self.service._ingest_claude_line(line, cursor)
        snapshot = await self.service.get_snapshot()
        self.assertEqual(len(snapshot["sessions"]), 1)
        self.assertEqual(snapshot["sessions"][0]["state"], "WAITING")


class CodexSessionChatServiceTest(unittest.IsolatedAsyncioTestCase):
    async def test_default_timeout_can_be_configured_via_env(self) -> None:
        with patch.dict(
            "live2d_server.session_bridge_chat.os.environ",
            {
                "CODEX_CLI_IDLE_TIMEOUT_SEC": "180",
                "CODEX_CLI_MAX_TIMEOUT_SEC": "1800",
                "CODEX_CLI_APPROVAL_TIMEOUT_SEC": "420",
            },
            clear=False,
        ):
            service = CodexSessionChatService(codex_bin="codex", default_cwd="/tmp/workspace")
        self.assertEqual(service.idle_timeout_sec, 180.0)
        self.assertEqual(service.max_timeout_sec, 1800.0)
        self.assertEqual(service.approval_timeout_sec, 420.0)

    async def test_build_codex_subprocess_env_strips_parent_codex_keys(self) -> None:
        service = CodexSessionChatService(codex_bin="codex", timeout_sec=5, default_cwd="/tmp/workspace")
        with patch.dict(
            "live2d_server.session_bridge.os.environ",
            {
                "CODEX_THREAD_ID": "thread-1",
                "CODEX_CI": "1",
                "CODEX_SANDBOX": "seatbelt",
                "CODEX_SANDBOX_NETWORK_DISABLED": "1",
                "CODEX_SHELL": "1",
                "CODEX_INTERNAL_ORIGINATOR_OVERRIDE": "Codex Desktop",
                "PATH": "/usr/bin",
            },
            clear=False,
        ):
            env = service._build_codex_subprocess_env()
        self.assertEqual(env.get("PATH"), "/usr/bin")
        self.assertNotIn("CODEX_THREAD_ID", env)
        self.assertNotIn("CODEX_CI", env)
        self.assertNotIn("CODEX_SANDBOX", env)
        self.assertNotIn("CODEX_SANDBOX_NETWORK_DISABLED", env)
        self.assertNotIn("CODEX_SHELL", env)
        self.assertNotIn("CODEX_INTERNAL_ORIGINATOR_OVERRIDE", env)

    async def test_build_cli_command_sets_default_approval_and_sandbox_for_default_mode(self) -> None:
        service = CodexSessionChatService(codex_bin="codex", timeout_sec=5, default_cwd="/tmp/workspace")
        command = service._build_cli_command(
            session_id="00000000-0000-0000-0000-000000000123",
            prompt="hello",
            cwd="/tmp/workspace",
            image_paths=[],
            model="gpt-5-codex",
            reasoning_effort="high",
            permission_mode="default",
            approval_policy=None,
            sandbox_mode=None,
        )
        self.assertIn("--full-auto", command)
        self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", command)
        self.assertLess(command.index("--full-auto"), command.index("exec"))

    async def test_build_cli_command_uses_dangerous_flag_for_full_mode(self) -> None:
        service = CodexSessionChatService(codex_bin="codex", timeout_sec=5, default_cwd="/tmp/workspace")
        command = service._build_cli_command(
            session_id="00000000-0000-0000-0000-000000000123",
            prompt="hello",
            cwd="/tmp/workspace",
            image_paths=[],
            model="gpt-5-codex",
            reasoning_effort="high",
            permission_mode="full",
            approval_policy=None,
            sandbox_mode=None,
        )
        self.assertIn("--dangerously-bypass-approvals-and-sandbox", command)
        self.assertNotIn("--full-auto", command)
        self.assertLess(command.index("--dangerously-bypass-approvals-and-sandbox"), command.index("exec"))

    async def test_run_prompt_aggregates_text_chunks(self) -> None:
        service = CodexSessionChatService(codex_bin="codex", timeout_sec=5, default_cwd="/tmp/workspace")

        async def _fake_stream(*_args, **_kwargs):
            yield {"type": "context", "content": {}}
            yield {"type": "text", "content": "第一段"}
            yield {"type": "tool_calls", "content": [{"name": "x", "arguments": {}}]}
            yield {"type": "text", "content": "第二段"}

        with patch.object(service, "stream_prompt", new=_fake_stream):
            reply = await service.run_prompt(
                session_id="00000000-0000-0000-0000-000000000123",
                prompt="hello",
            )
        self.assertEqual(reply, "第一段\n\n第二段")

    async def test_run_prompt_raises_when_no_text(self) -> None:
        service = CodexSessionChatService(codex_bin="codex", timeout_sec=5, default_cwd="/tmp/workspace")

        async def _fake_stream(*_args, **_kwargs):
            yield {"type": "context", "content": {}}
            yield {"type": "tool_calls", "content": [{"name": "x", "arguments": {}}]}

        with patch.object(service, "stream_prompt", new=_fake_stream):
            with self.assertRaises(CodexSessionChatError):
                await service.run_prompt(
                    session_id="00000000-0000-0000-0000-000000000124",
                    prompt="hello",
                )

    async def test_create_session_parses_thread_id_and_branch(self) -> None:
        service = CodexSessionChatService(codex_bin="codex", timeout_sec=5, default_cwd="/tmp/workspace")
        fake_output = b'{"type":"thread.started","thread_id":"00000000-0000-0000-0000-000000000abc"}\n'
        recorded_args = {}
        recorded_kwargs = {}

        async def _fake_create_subprocess_exec(*_args, **_kwargs):
            recorded_args["cmd"] = list(_args)
            recorded_kwargs.update(_kwargs)
            return _FakeCreateProcess(returncode=0, stdout=fake_output, stderr=b"")

        with patch("live2d_server.session_bridge_chat.asyncio.create_subprocess_exec", side_effect=_fake_create_subprocess_exec):
            with patch("live2d_server.session_bridge.subprocess.run", return_value=_Completed(returncode=0, stdout="feature/x\n")):
                payload = await service.create_session(
                    cwd="/tmp/workspace",
                    model="gpt-5-codex",
                    permission_mode="default",
                )

        self.assertEqual(payload["session_id"], "00000000-0000-0000-0000-000000000abc")
        self.assertEqual(payload["branch"], "feature/x")
        self.assertEqual(payload["cwd"], "/tmp/workspace")
        self.assertEqual(payload["model"], "gpt-5-codex")
        self.assertEqual(payload["permission_mode"], "default")
        self.assertEqual(payload["approval_policy"], "on-request")
        self.assertEqual(payload["sandbox_mode"], "workspace-write")
        cmd = recorded_args.get("cmd", [])
        self.assertIn("--full-auto", cmd)
        self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", cmd)
        self.assertLess(cmd.index("--full-auto"), cmd.index("exec"))
        self.assertIsInstance(recorded_kwargs.get("env"), dict)

    async def test_create_session_prefers_runtime_turn_context_values(self) -> None:
        service = CodexSessionChatService(codex_bin="codex", timeout_sec=5, default_cwd="/tmp/workspace")
        fake_output = (
            b'{"type":"thread.started","thread_id":"00000000-0000-0000-0000-000000000abc"}\n'
            b'{"type":"turn_context","payload":{"model":"gpt-5.3-codex","effort":"high","approval_policy":"never","sandbox_policy":{"type":"workspace-write"}}}\n'
        )

        async def _fake_create_subprocess_exec(*_args, **_kwargs):
            return _FakeCreateProcess(returncode=0, stdout=fake_output, stderr=b"")

        with patch("live2d_server.session_bridge_chat.asyncio.create_subprocess_exec", side_effect=_fake_create_subprocess_exec):
            with patch("live2d_server.session_bridge.subprocess.run", return_value=_Completed(returncode=0, stdout="main\n")):
                payload = await service.create_session(
                    cwd="/tmp/workspace",
                    model="gpt-5-codex",
                    reasoning_effort="low",
                    permission_mode="default",
                )

        self.assertEqual(payload["model"], "gpt-5.3-codex")
        self.assertEqual(payload["effort"], "high")
        self.assertEqual(payload["permission_mode"], "default")
        self.assertEqual(payload["approval_policy"], "on-request")
        self.assertEqual(payload["sandbox_mode"], "workspace-write")

    async def test_stream_prompt_skips_approval_request_in_full_mode(self) -> None:
        service = CodexSessionChatService(codex_bin="codex", timeout_sec=5, default_cwd="/tmp/workspace")
        events = [
            json.dumps(
                {
                    "type": "response_item",
                    "payload": {
                        "type": "function_call",
                        "name": "exec_command",
                        "arguments": {
                            "cmd": "touch /tmp/x",
                            "sandbox_permissions": "require_escalated",
                            "justification": "need write",
                        },
                    },
                }
            ).encode("utf-8")
            + b"\n",
            json.dumps(
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "assistant",
                        "content": "ok",
                    },
                }
            ).encode("utf-8")
            + b"\n",
        ]

        async def _fake_create_subprocess_exec(*_args, **_kwargs):
            return _FakeStreamProcess(stdout_lines=events, returncode=0, stderr=b"")

        with patch("live2d_server.session_bridge_chat.asyncio.create_subprocess_exec", side_effect=_fake_create_subprocess_exec):
            emitted: list[dict[str, object]] = []
            async for item in service.stream_prompt(
                session_id="00000000-0000-0000-0000-000000000777",
                prompt="hello",
                permission_mode="full",
            ):
                emitted.append(item)

        event_types = [str(item.get("type") or "") for item in emitted]
        self.assertIn("tool_calls", event_types)
        self.assertIn("text", event_types)
        self.assertNotIn("approval_request", event_types)

    async def test_stream_prompt_emits_approval_request_in_default_mode(self) -> None:
        service = CodexSessionChatService(codex_bin="codex", timeout_sec=5, default_cwd="/tmp/workspace")
        events = [
            json.dumps(
                {
                    "type": "response_item",
                    "payload": {
                        "type": "function_call",
                        "name": "exec_command",
                        "arguments": {
                            "cmd": "touch /tmp/approval-probe.txt",
                            "sandbox_permissions": "require_escalated",
                            "justification": "need write",
                        },
                    },
                }
            ).encode("utf-8")
            + b"\n",
            json.dumps(
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "assistant",
                        "content": "ok",
                    },
                }
            ).encode("utf-8")
            + b"\n",
        ]

        async def _fake_create_subprocess_exec(*_args, **_kwargs):
            return _FakeStreamProcess(stdout_lines=events, returncode=0, stderr=b"")

        emitted: list[dict[str, object]] = []
        with patch("live2d_server.session_bridge_chat.asyncio.create_subprocess_exec", side_effect=_fake_create_subprocess_exec):
            async for item in service.stream_prompt(
                session_id="00000000-0000-0000-0000-000000000778",
                prompt="hello",
                permission_mode="default",
            ):
                emitted.append(item)
                if item.get("type") == "approval_request":
                    content = item.get("content") if isinstance(item.get("content"), dict) else {}
                    pending_id = str(content.get("pending_id") or "")
                    self.assertTrue(pending_id)
                    ok = await service.submit_approval(pending_id=pending_id, decision="allow_once")
                    self.assertTrue(ok)

        event_types = [str(item.get("type") or "") for item in emitted]
        self.assertIn("tool_calls", event_types)
        self.assertIn("approval_request", event_types)
        self.assertIn("text", event_types)


class BridgeCodexChatApiTest(unittest.IsolatedAsyncioTestCase):
    def test_agent_request_aliases_keep_compatibility(self) -> None:
        self.assertIs(AgentChatRequest, CodexChatRequest)
        self.assertIs(AgentChatApprovalRequest, CodexChatApprovalRequest)
        self.assertIs(AgentNewSessionRequest, CodexNewSessionRequest)
        self.assertIs(AgentConversationRequest, CodexConversationRequest)

    async def test_bridge_codex_chat_passes_default_permission_settings(self) -> None:
        session = _SessionRecord(
            session_id="00000000-0000-0000-0000-000000000125",
            display_name="session-125",
            cwd="/tmp/work",
            approval_policy="",
            sandbox_mode="",
            permission_mode="default",
        )
        captured: dict[str, object] = {}

        async def _stream_capture(*_args, **_kwargs):
            captured.update(_kwargs)
            yield {"type": "context", "content": {"cwd": "/tmp/work"}}
            yield {"type": "text", "content": "ok"}

        with patch("live2d_server.session_bridge_api._ensure_session_record", new=AsyncMock(return_value=session)):
            with patch("live2d_server.session_bridge.codex_chat_service.stream_prompt", new=_stream_capture):
                with patch("live2d_server.session_bridge_api._run_git_command", return_value=_Completed(returncode=0, stdout="main\n")):
                    with patch("live2d_server.session_bridge.bridge_service.upsert_runtime_context", new=AsyncMock()):
                        response = await bridge_codex_chat(
                            CodexChatRequest(
                                session_id=session.session_id,
                                message="hello",
                            )
                        )
                        await _collect_stream_body(response)

        self.assertEqual(captured.get("permission_mode"), "default")
        self.assertEqual(captured.get("approval_policy"), "on-request")
        self.assertEqual(captured.get("sandbox_mode"), "workspace-write")

    async def test_bridge_codex_chat_request_permission_mode_overrides_session_runtime(self) -> None:
        session = _SessionRecord(
            session_id="00000000-0000-0000-0000-000000000128",
            display_name="session-128",
            cwd="/tmp/work",
            approval_policy="never",
            sandbox_mode="danger-full-access",
            permission_mode="full",
        )
        captured: dict[str, object] = {}

        async def _stream_capture(*_args, **_kwargs):
            captured.update(_kwargs)
            yield {"type": "context", "content": {"cwd": "/tmp/work"}}
            yield {"type": "text", "content": "ok"}

        with patch("live2d_server.session_bridge_api._ensure_session_record", new=AsyncMock(return_value=session)):
            with patch("live2d_server.session_bridge.codex_chat_service.stream_prompt", new=_stream_capture):
                with patch("live2d_server.session_bridge_api._run_git_command", return_value=_Completed(returncode=0, stdout="main\n")):
                    with patch("live2d_server.session_bridge.bridge_service.upsert_runtime_context", new=AsyncMock()):
                        response = await bridge_codex_chat(
                            CodexChatRequest(
                                session_id=session.session_id,
                                message="hello",
                                permission_mode="default",
                            )
                        )
                        await _collect_stream_body(response)

        self.assertEqual(captured.get("permission_mode"), "default")
        self.assertEqual(captured.get("approval_policy"), "on-request")
        self.assertEqual(captured.get("sandbox_mode"), "workspace-write")

    async def test_bridge_codex_chat_request_full_mode_uses_dangerous_runtime(self) -> None:
        session = _SessionRecord(
            session_id="00000000-0000-0000-0000-000000000129",
            display_name="session-129",
            cwd="/tmp/work",
            approval_policy="on-request",
            sandbox_mode="workspace-write",
            permission_mode="default",
        )
        captured: dict[str, object] = {}

        async def _stream_capture(*_args, **_kwargs):
            captured.update(_kwargs)
            yield {"type": "context", "content": {"cwd": "/tmp/work"}}
            yield {"type": "text", "content": "ok"}

        with patch("live2d_server.session_bridge_api._ensure_session_record", new=AsyncMock(return_value=session)):
            with patch("live2d_server.session_bridge.codex_chat_service.stream_prompt", new=_stream_capture):
                with patch("live2d_server.session_bridge_api._run_git_command", return_value=_Completed(returncode=0, stdout="main\n")):
                    with patch("live2d_server.session_bridge.bridge_service.upsert_runtime_context", new=AsyncMock()):
                        response = await bridge_codex_chat(
                            CodexChatRequest(
                                session_id=session.session_id,
                                message="hello",
                                permission_mode="full",
                            )
                        )
                        await _collect_stream_body(response)

        self.assertEqual(captured.get("permission_mode"), "full")
        self.assertEqual(captured.get("approval_policy"), "never")
        self.assertEqual(captured.get("sandbox_mode"), "danger-full-access")

    async def test_bridge_codex_chat_uses_session_permission_mode_when_request_missing(self) -> None:
        session = _SessionRecord(
            session_id="00000000-0000-0000-0000-000000000130",
            display_name="session-130",
            cwd="/tmp/work",
            approval_policy="never",
            sandbox_mode="danger-full-access",
            permission_mode="default",
        )
        captured: dict[str, object] = {}

        async def _stream_capture(*_args, **_kwargs):
            captured.update(_kwargs)
            yield {"type": "context", "content": {"cwd": "/tmp/work"}}
            yield {"type": "text", "content": "ok"}

        with patch("live2d_server.session_bridge_api._ensure_session_record", new=AsyncMock(return_value=session)):
            with patch("live2d_server.session_bridge.codex_chat_service.stream_prompt", new=_stream_capture):
                with patch("live2d_server.session_bridge_api._run_git_command", return_value=_Completed(returncode=0, stdout="main\n")):
                    with patch("live2d_server.session_bridge.bridge_service.upsert_runtime_context", new=AsyncMock()):
                        response = await bridge_codex_chat(
                            CodexChatRequest(
                                session_id=session.session_id,
                                message="hello",
                            )
                        )
                        await _collect_stream_body(response)

        self.assertEqual(captured.get("permission_mode"), "default")
        self.assertEqual(captured.get("approval_policy"), "on-request")
        self.assertEqual(captured.get("sandbox_mode"), "workspace-write")

    async def test_bridge_codex_chat_streams_text_and_done(self) -> None:
        session = _SessionRecord(
            session_id="00000000-0000-0000-0000-000000000126",
            display_name="session-126",
            cwd="/tmp/work",
            approval_policy="on-request",
            sandbox_mode="workspace-write",
        )

        async def _stream_ok(*_args, **_kwargs):
            yield {"type": "context", "content": {"cwd": "/tmp/work"}}
            yield {"type": "text", "content": "final reply"}

        with patch("live2d_server.session_bridge_api._ensure_session_record", new=AsyncMock(return_value=session)):
            with patch("live2d_server.session_bridge.codex_chat_service.stream_prompt", new=_stream_ok):
                with patch("live2d_server.session_bridge_api._run_git_command", return_value=_Completed(returncode=0, stdout="main\n")):
                    with patch("live2d_server.session_bridge.bridge_service.upsert_runtime_context", new=AsyncMock()):
                        response = await bridge_codex_chat(
                            CodexChatRequest(
                                session_id=session.session_id,
                                message="hello",
                            )
                        )
                        payload = await _collect_stream_body(response)
        self.assertIn('"type": "text"', payload)
        self.assertIn("final reply", payload)
        self.assertIn('"type": "done"', payload)
        self.assertIn('"agent_brand": "codex"', payload)

    async def test_bridge_codex_chat_streams_error(self) -> None:
        session = _SessionRecord(
            session_id="00000000-0000-0000-0000-000000000127",
            display_name="session-127",
            cwd="/tmp/work",
        )

        async def _stream_fail(*_args, **_kwargs):
            raise CodexSessionChatError("boom")
            yield {"type": "text", "content": "unused"}

        with patch("live2d_server.session_bridge_api._ensure_session_record", new=AsyncMock(return_value=session)):
            with patch("live2d_server.session_bridge.codex_chat_service.stream_prompt", new=_stream_fail):
                response = await bridge_codex_chat(
                    CodexChatRequest(
                        session_id=session.session_id,
                        message="hello",
                    )
                )
                payload = await _collect_stream_body(response)
        self.assertIn('"type": "error"', payload)
        self.assertIn("boom", payload)

    async def test_bridge_codex_chat_approval(self) -> None:
        with patch(
            "live2d_server.session_bridge.codex_chat_service.submit_approval",
            new=AsyncMock(return_value=True),
        ):
            payload = await bridge_codex_chat_approval(
                CodexChatApprovalRequest(
                    pending_id="pending-1",
                    decision="allow_once",
                )
            )
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["decision"], "allow_once")
        self.assertEqual(payload["agent_brand"], "codex")

    async def test_bridge_codex_new_session(self) -> None:
        fake_payload = {
            "session_id": "00000000-0000-0000-0000-000000000300",
            "cwd": "/tmp/work",
            "branch": "main",
            "model": "gpt-5-codex",
            "effort": "high",
            "permission_mode": "default",
            "approval_policy": "on-request",
            "sandbox_mode": "workspace-write",
            "plan_mode": False,
            "plan_mode_fallback": False,
        }
        with patch(
            "live2d_server.session_bridge.codex_chat_service.create_session",
            new=AsyncMock(return_value=fake_payload),
        ):
            with patch("live2d_server.session_bridge.bridge_service.upsert_runtime_context", new=AsyncMock()):
                payload = await bridge_codex_new_session(
                    CodexNewSessionRequest(
                        cwd="/tmp/work",
                        model="gpt-5-codex",
                    )
                )
        self.assertEqual(payload["session_id"], fake_payload["session_id"])
        self.assertEqual(payload["cwd"], "/tmp/work")
        self.assertEqual(payload["agent_brand"], "codex")

    async def test_bridge_codex_new_session_request_permission_mode_overrides_history_runtime(self) -> None:
        fake_payload = {
            "session_id": "00000000-0000-0000-0000-000000000301",
            "cwd": "/tmp/work",
            "branch": "main",
            "model": "gpt-5-codex",
            "effort": "high",
            "permission_mode": "default",
            "approval_policy": "on-request",
            "sandbox_mode": "workspace-write",
            "plan_mode": False,
            "plan_mode_fallback": False,
        }
        history_runtime = {
            "cwd": "/tmp/work",
            "branch": "main",
            "model": "gpt-5-codex",
            "effort": "high",
            "permission_mode": "default",
            "approval_policy": "never",
            "sandbox_mode": "read-only",
        }
        with patch(
            "live2d_server.session_bridge.codex_chat_service.create_session",
            new=AsyncMock(return_value=fake_payload),
        ):
            with patch(
                "live2d_server.session_bridge_api._read_history_runtime_snapshot",
                return_value=history_runtime,
            ):
                with patch("live2d_server.session_bridge.bridge_service.upsert_runtime_context", new=AsyncMock()):
                    payload = await bridge_codex_new_session(
                        CodexNewSessionRequest(
                            cwd="/tmp/work",
                            permission_mode="default",
                        )
                    )

        self.assertEqual(payload["permission_mode"], "default")
        self.assertEqual(payload["approval_policy"], "on-request")
        self.assertEqual(payload["sandbox_mode"], "workspace-write")


class BridgeGitApiTest(unittest.IsolatedAsyncioTestCase):
    async def test_bridge_git_branches(self) -> None:
        record = _SessionRecord(
            session_id="00000000-0000-0000-0000-000000000400",
            display_name="session-400",
            cwd="/tmp/work",
        )
        side_effect = [
            _Completed(returncode=0, stdout="main\nfeature/a\n"),
            _Completed(returncode=0, stdout="main\n"),
        ]
        with patch("live2d_server.session_bridge.bridge_service.get_session_record", new=AsyncMock(return_value=record)):
            with patch("live2d_server.session_bridge_api._run_git_command", side_effect=side_effect):
                with patch("live2d_server.session_bridge.bridge_service.upsert_runtime_context", new=AsyncMock()):
                    payload = await bridge_git_branches(session_id=record.session_id)
        self.assertEqual(payload["current"], "main")
        self.assertEqual(payload["branches"], ["main", "feature/a"])

    async def test_bridge_git_switch(self) -> None:
        record = _SessionRecord(
            session_id="00000000-0000-0000-0000-000000000401",
            display_name="session-401",
            cwd="/tmp/work",
        )
        side_effect = [
            _Completed(returncode=0, stdout="", stderr=""),  # git switch
            _Completed(returncode=0, stdout="feature/a\n", stderr=""),  # show-current
        ]
        with patch("live2d_server.session_bridge.bridge_service.get_session_record", new=AsyncMock(return_value=record)):
            with patch("live2d_server.session_bridge_api._run_git_command", side_effect=side_effect):
                with patch("live2d_server.session_bridge.bridge_service.upsert_runtime_context", new=AsyncMock()):
                    payload = await bridge_git_switch(
                        GitBranchSwitchRequest(session_id=record.session_id, branch="feature/a")
                    )
        self.assertEqual(payload["current"], "feature/a")


class AgentProviderRouterTest(unittest.TestCase):
    def test_normalize_brand_rejects_unsupported_value(self) -> None:
        with self.assertRaises(ValueError):
            AgentProviderRouter.normalize_brand("copilot")

    def test_supported_brands_expose_metadata_for_ui(self) -> None:
        payload = asyncio.run(bridge_agent_brands())
        self.assertIn("brands", payload)
        self.assertTrue(payload["brands"])
        codex = next(item for item in payload["brands"] if item["brand"] == "codex")
        self.assertEqual(codex["display_name"], "Codex")
        self.assertTrue(codex["models"])
        self.assertEqual(codex["badge_icon"], "/brand/codex-badge.svg")


class AgentProviderApiContractTest(unittest.IsolatedAsyncioTestCase):
    async def test_bridge_agent_brands_returns_all_registered_metadata(self) -> None:
        payload = await bridge_agent_brands()
        brands = {item["brand"]: item for item in payload["brands"]}
        self.assertIn("codex", brands)
        self.assertIn("claude", brands)
        self.assertEqual(brands["claude"]["display_name"], "Claude")
        self.assertEqual(brands["claude"]["badge_icon"], "/brand/claude-badge.svg")

    async def test_bridge_agent_new_session_rejects_unsupported_brand(self) -> None:
        from live2d_server.session_bridge import bridge_agent_new_session

        with self.assertRaises(HTTPException) as ctx:
            await bridge_agent_new_session(
                CodexNewSessionRequest(
                    cwd="/tmp/work",
                    agent_brand="copilot",
                )
            )
        self.assertEqual(ctx.exception.status_code, 400)

    async def test_bridge_agent_chat_approval_routes_to_explicit_brand(self) -> None:
        codex_service = AsyncMock()
        codex_service.submit_approval = AsyncMock(return_value=False)
        claude_service = AsyncMock()
        claude_service.submit_approval = AsyncMock(return_value=True)

        def _get_chat_service(brand: str):
            return {"codex": codex_service, "claude": claude_service}[brand]

        with patch("live2d_server.session_bridge_api.agent_provider.get_chat_service", side_effect=_get_chat_service):
            payload = await bridge_agent_chat_approval(
                CodexChatApprovalRequest(
                    pending_id="pending-1",
                    decision="allow_once",
                    agent_brand="claude",
                )
            )

        codex_service.submit_approval.assert_not_awaited()
        claude_service.submit_approval.assert_awaited_once()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["agent_brand"], "claude")


class BridgeConversationApiTest(unittest.IsolatedAsyncioTestCase):
    async def test_bridge_conversation_returns_messages(self) -> None:
        with patch(
            "live2d_server.session_bridge.bridge_service.get_conversation",
            new=AsyncMock(
                return_value={
                    "version": "1",
                    "generated_at": "2026-02-27T09:00:00Z",
                    "session_id": "00000000-0000-0000-0000-000000000199",
                    "messages": [
                        {"role": "user", "content": "hello", "timestamp": "2026-02-27T09:00:00Z"},
                    ],
                }
            ),
        ):
            payload = await bridge_conversation(
                session_id="00000000-0000-0000-0000-000000000199",
                request=CodexConversationRequest(limit=50),
            )
        self.assertEqual(payload["session_id"], "00000000-0000-0000-0000-000000000199")
        self.assertEqual(payload["messages"][0]["content"], "hello")


if __name__ == "__main__":
    unittest.main()
