import asyncio
import json
import sqlite3
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException

from live2d_server.session_bridge_shared import _format_subprocess_spawn_error
from live2d_server.session_bridge import (
    AGENT_BRAND_OPENCODE,
    AgentAbortRequest,
    AgentChatApprovalRequest,
    AgentChatRequest,
    AgentConversationRequest,
    AgentNewSessionRequest,
    AgentProviderRouter,
    ClaudeSessionChatService,
    CodexChatApprovalRequest,
    CodexChatRequest,
    CodexConversationRequest,
    CodexNewSessionRequest,
    CodexSessionChatError,
    CodexSessionChatService,
    GitBranchSwitchRequest,
    OpencodeSessionChatError,
    OpencodeSessionChatService,
    SessionBridgeService,
    _FileCursor,
    _SessionRecord,
    bridge_agent_chat_abort,
    bridge_codex_chat,
    bridge_codex_chat_approval,
    bridge_codex_new_session,
    bridge_agent_chat_approval,
    bridge_agent_brands,
    bridge_browse_directories,
    bridge_conversation,
    bridge_git_branches,
    bridge_git_switch,
)

EXPECTED_CODEX_AUTOMATION_SANDBOX = "danger-full-access" if sys.platform == "win32" else "workspace-write"


class SubprocessSpawnErrorFormattingTest(unittest.TestCase):
    def test_windows_selector_loop_not_implemented_error_is_actionable(self) -> None:
        detail = _format_subprocess_spawn_error(NotImplementedError())
        if sys.platform == "win32":
            self.assertIn("WindowsProactorEventLoopPolicy", detail)
        else:
            self.assertEqual(detail, "NotImplementedError")


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
        # asyncio.subprocess.Process exposes .pid; tests log it on abort, so the
        # fake needs one too. The exact value doesn't matter for these tests.
        self.pid = 0

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
        self.service.opencode_data_dir = Path("/tmp/session-bridge-test-missing-opencode-data")

    def _write_opencode_fixture_db(self, data_dir: Path) -> str:
        data_dir.mkdir(parents=True, exist_ok=True)
        session_id = "ses_testopencode123"
        conn = sqlite3.connect(str(data_dir / "opencode.db"))
        try:
            conn.executescript(
                """
                CREATE TABLE session (
                    id text PRIMARY KEY,
                    title text NOT NULL,
                    directory text NOT NULL,
                    model text,
                    agent text,
                    permission text,
                    time_created integer NOT NULL,
                    time_updated integer NOT NULL,
                    time_archived integer,
                    tokens_input integer DEFAULT 0,
                    tokens_output integer DEFAULT 0,
                    tokens_reasoning integer DEFAULT 0,
                    tokens_cache_read integer DEFAULT 0,
                    tokens_cache_write integer DEFAULT 0
                );
                CREATE TABLE message (
                    id text PRIMARY KEY,
                    session_id text NOT NULL,
                    time_created integer NOT NULL,
                    time_updated integer NOT NULL,
                    data text NOT NULL
                );
                CREATE TABLE part (
                    id text PRIMARY KEY,
                    message_id text NOT NULL,
                    session_id text NOT NULL,
                    time_created integer NOT NULL,
                    time_updated integer NOT NULL,
                    data text NOT NULL
                );
                """
            )
            model = {"id": "deepseek-v4-flash-free", "providerID": "opencode", "variant": "max"}
            conn.execute(
                """
                INSERT INTO session (
                    id, title, directory, model, agent, permission,
                    time_created, time_updated,
                    tokens_input, tokens_output, tokens_reasoning,
                    tokens_cache_read, tokens_cache_write
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    "OpenCode fixture",
                    "/tmp/opencode-project",
                    json.dumps(model),
                    "build",
                    "",
                    1780388390000,
                    1780388394000,
                    10,
                    20,
                    3,
                    4,
                    5,
                ),
            )
            bridge_prompt = json.dumps({
                "schema": "session_bridge_user_input_v1",
                "plan_mode": False,
                "personality": None,
                "user_input": "請介紹 OpenCode",
                "instructions": [],
            }, ensure_ascii=False)
            conn.execute(
                "INSERT INTO message (id, session_id, time_created, time_updated, data) VALUES (?, ?, ?, ?, ?)",
                ("msg_user", session_id, 1780388391000, 1780388391000, json.dumps({"role": "user"})),
            )
            conn.execute(
                "INSERT INTO part (id, message_id, session_id, time_created, time_updated, data) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    "prt_user",
                    "msg_user",
                    session_id,
                    1780388391001,
                    1780388391001,
                    json.dumps({"type": "text", "text": json.dumps(bridge_prompt, ensure_ascii=False)}, ensure_ascii=False),
                ),
            )
            conn.execute(
                "INSERT INTO message (id, session_id, time_created, time_updated, data) VALUES (?, ?, ?, ?, ?)",
                ("msg_assistant", session_id, 1780388392000, 1780388392000, json.dumps({"role": "assistant"})),
            )
            conn.execute(
                "INSERT INTO part (id, message_id, session_id, time_created, time_updated, data) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    "prt_assistant",
                    "msg_assistant",
                    session_id,
                    1780388392001,
                    1780388392001,
                    json.dumps({"type": "text", "text": "OpenCode 回覆內容"}, ensure_ascii=False),
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return session_id

    async def test_get_history_includes_opencode_db_sessions(self) -> None:
        with TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir)
            session_id = self._write_opencode_fixture_db(data_dir)
            self.service.opencode_data_dir = data_dir

            with patch.object(self.service, "_collect_history_from_files", return_value={}):
                payload = await self.service.get_history(limit=10)

        item = next(item for item in payload["sessions"] if item["session_id"] == session_id)
        self.assertEqual(item["agent_brand"], "opencode")
        self.assertEqual(item["originator"], "OpenCode")
        self.assertEqual(item["context"]["model"], "opencode/deepseek-v4-flash-free")
        self.assertEqual(item["context"]["effort"], "max")
        self.assertEqual(item["context"]["total_tokens"], 42)

    async def test_get_conversation_includes_opencode_db_messages(self) -> None:
        with TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir)
            session_id = self._write_opencode_fixture_db(data_dir)
            self.service.opencode_data_dir = data_dir

            with patch.object(self.service, "_collect_conversation_from_files", return_value=[]):
                with patch.object(self.service, "_collect_claude_conversation_from_files", return_value=[]):
                    payload = await self.service.get_conversation(session_id=session_id)

        self.assertEqual(
            [(item["role"], item["content"]) for item in payload["messages"]],
            [("user", "請介紹 OpenCode"), ("assistant", "OpenCode 回覆內容")],
        )

    async def test_build_state_event_uses_opencode_source(self) -> None:
        session = _SessionRecord(
            session_id="ses_opencode_source",
            display_name="OpenCode",
            agent_brand="opencode",
            originator="OpenCode",
        )
        event = self.service._build_state_event(session)
        self.assertEqual(event["source"], "opencode_db")

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

    async def test_get_conversation_unwraps_persona_prompt_envelope(self) -> None:
        session_id = "00000000-0000-0000-0000-000000000017"
        wrapped_prompt = json.dumps(
            {
                "schema": "session_bridge_user_input_v1",
                "plan_mode": False,
                "personality": {
                    "id": "persona-1",
                    "name": "冷靜 PM",
                    "content": "請條理分明地回覆。",
                },
                "user_input": "幫我整理今天要改的檔案",
                "instructions": [],
            },
            ensure_ascii=False,
        )
        with TemporaryDirectory() as codex_dir, TemporaryDirectory() as claude_dir:
            self.service.session_dir = Path(codex_dir)
            self.service.claude_session_dir = Path(claude_dir)
            file_path = self.service.session_dir / "2026" / "03" / "08" / f"{session_id}.jsonl"
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(
                "\n".join(
                    [
                        json.dumps({
                            "timestamp": "2026-03-08T10:40:00Z",
                            "type": "event_msg",
                            "payload": {
                                "type": "user_message",
                                "message": wrapped_prompt,
                            },
                        }, ensure_ascii=False),
                    ]
                ) + "\n",
                encoding="utf-8",
            )
            payload = await self.service.get_conversation(session_id, limit=50)
            history = await self.service.get_history(limit=50)
        self.assertEqual(payload["messages"][0]["content"], "幫我整理今天要改的檔案")
        self.assertEqual(history["sessions"][0]["display_name"], "幫我整理今天要改的檔案")
        self.assertEqual(history["sessions"][0]["context"]["persona_id"], "persona-1")
        self.assertEqual(history["sessions"][0]["context"]["persona_name"], "冷靜 PM")

    async def test_history_unwraps_persona_prompt_without_turn_context(self) -> None:
        session_id = "00000000-0000-0000-0000-000000000018"
        wrapped_prompt = json.dumps(
            {
                "schema": "session_bridge_user_input_v1",
                "plan_mode": False,
                "personality": {
                    "id": "persona-1",
                    "name": "冷靜 PM",
                    "content": "請條理分明地回覆。",
                },
                "user_input": "幫我整理今天要改的檔案",
                "instructions": [],
            },
            ensure_ascii=False,
        )
        with TemporaryDirectory() as codex_dir, TemporaryDirectory() as claude_dir:
            self.service.session_dir = Path(codex_dir)
            self.service.claude_session_dir = Path(claude_dir)
            file_path = self.service.session_dir / "2026" / "03" / "08" / f"{session_id}.jsonl"
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(
                json.dumps({
                    "timestamp": "2026-03-08T10:40:00Z",
                    "type": "event_msg",
                    "payload": {
                        "type": "user_message",
                        "message": wrapped_prompt,
                    },
                }, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            history = await self.service.get_history(limit=50)
        self.assertEqual(history["sessions"][0]["display_name"], "幫我整理今天要改的檔案")
        self.assertEqual(history["sessions"][0]["context"]["persona_id"], "persona-1")
        self.assertEqual(history["sessions"][0]["context"]["persona_name"], "冷靜 PM")
        self.assertEqual(history["sessions"][0]["context"]["persona_content"], "請條理分明地回覆。")

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

    async def test_get_conversation_appends_in_memory_draft_until_file_catches_up(self) -> None:
        session_id = "00000000-0000-0000-0000-000000000019"
        with TemporaryDirectory() as codex_dir, TemporaryDirectory() as claude_dir:
            self.service.session_dir = Path(codex_dir)
            self.service.claude_session_dir = Path(claude_dir)
            file_path = self.service.session_dir / "2026" / "03" / "08" / f"{session_id}.jsonl"
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(
                "\n".join(
                    [
                        '{"timestamp":"2026-03-08T10:00:02Z","type":"event_msg","payload":{"type":"user_message","message":"請幫我說明"}}',
                    ]
                ) + "\n",
                encoding="utf-8",
            )
            await self.service.append_conversation_draft(
                session_id,
                role="assistant",
                content="第一句。",
                timestamp="2026-03-08T10:00:03Z",
            )
            await self.service.append_conversation_draft(
                session_id,
                role="assistant",
                content="第二句。",
                timestamp="2026-03-08T10:00:04Z",
            )
            payload = await self.service.get_conversation(session_id, limit=50)
        self.assertEqual(len(payload["messages"]), 2)
        self.assertEqual(payload["messages"][1]["role"], "assistant")
        self.assertEqual(payload["messages"][1]["content"], "第一句。第二句。")

    async def test_lookup_claude_session_metadata_uses_common_project_root_cwd(self) -> None:
        session_id = "00000000-0000-0000-0000-000000000020"
        with TemporaryDirectory() as claude_dir:
            self.service.claude_session_dir = Path(claude_dir)
            project_dir = self.service.claude_session_dir / "-Users-dannylin-Desktop-agents-stage-live2d-vrm3d"
            project_dir.mkdir(parents=True, exist_ok=True)
            file_path = project_dir / f"{session_id}.jsonl"
            file_path.write_text(
                "\n".join(
                    [
                        json.dumps({
                            "type": "user",
                            "sessionId": session_id,
                            "timestamp": "2026-03-25T10:00:00Z",
                            "cwd": "/Users/dannylin/Desktop/agents-stage-live2d-vrm3d",
                            "message": {"role": "user", "content": "請幫我修 bridge"},
                        }, ensure_ascii=False),
                        json.dumps({
                            "type": "assistant",
                            "sessionId": session_id,
                            "timestamp": "2026-03-25T10:00:01Z",
                            "cwd": "/Users/dannylin/Desktop/agents-stage-live2d-vrm3d/agents-stage-live2d-vrm3d-server",
                            "message": {"role": "assistant", "model": "claude-opus-4-6", "content": "先看 server"},
                        }, ensure_ascii=False),
                    ]
                ) + "\n",
                encoding="utf-8",
            )

            payload = self.service.lookup_claude_session_metadata(session_id)

        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertEqual(payload["cwd"], "/Users/dannylin/Desktop/agents-stage-live2d-vrm3d")
        self.assertEqual(payload["cwd_basename"], "agents-stage-live2d-vrm3d")
        self.assertEqual(payload["context"]["model"], "claude-opus-4-6")

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

    async def test_consume_claude_file_offloads_io_to_thread(self) -> None:
        """Regression: sync ``stat``/``open``/``read`` must run in the
        thread pool so a long scan over hundreds of session files does
        NOT block the event loop. Verifies :func:`asyncio.to_thread` is
        used to invoke the blocking reader.

        Doesn't try to measure latency (flaky on busy CI). Instead asserts
        that the blocking helper was invoked via the threadpool dispatcher
        — proven by patching ``asyncio.to_thread`` and capturing the call.
        """
        session_id = "00000000-0000-0000-0000-000000000043"
        now = _now_iso()
        with TemporaryDirectory() as claude_dir:
            self.service.claude_session_dir = Path(claude_dir)
            project_dir = self.service.claude_session_dir / "project-thread"
            project_dir.mkdir(parents=True, exist_ok=True)
            file_path = project_dir / f"{session_id}.jsonl"
            file_path.write_text(
                json.dumps({
                    "type": "user", "sessionId": session_id, "timestamp": now,
                    "cwd": "/tmp/work", "message": {"role": "user", "content": "x"},
                }, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )

            real_to_thread = asyncio.to_thread
            calls: list[str] = []

            async def _record_to_thread(func, *args, **kwargs):
                calls.append(func.__name__)
                return await real_to_thread(func, *args, **kwargs)

            with patch("live2d_server.session_bridge_runtime.asyncio.to_thread", new=_record_to_thread):
                await self.service._consume_claude_file(file_path)
            self.assertIn("_read_claude_file_blocking", calls)

    async def test_consume_claude_file_only_reads_tail_on_first_encounter(self) -> None:
        """Regression: previously _consume_claude_file started at offset 0 on
        first encounter, replaying every historical line at startup and
        choking the event loop. It should mirror _consume_file and seek to
        ``max(size - initial_read_bytes, 0)`` for the first scan."""
        session_id = "00000000-0000-0000-0000-000000000041"
        now = _now_iso()
        with TemporaryDirectory() as claude_dir:
            self.service.claude_session_dir = Path(claude_dir)
            project_dir = self.service.claude_session_dir / "project-x"
            project_dir.mkdir(parents=True, exist_ok=True)
            file_path = project_dir / f"{session_id}.jsonl"
            # Lower the cap so we don't have to write megabytes in the test.
            self.service.initial_read_bytes = 512
            pad_line = json.dumps(
                {
                    "type": "user",
                    "sessionId": session_id,
                    "timestamp": now,
                    "cwd": "/tmp/work-old",
                    "message": {"role": "user", "content": "ancient-history"},
                },
                ensure_ascii=False,
            )
            tail_line = json.dumps(
                {
                    "type": "user",
                    "sessionId": session_id,
                    "timestamp": now,
                    "cwd": "/tmp/work-new",
                    "message": {"role": "user", "content": "recent-prompt"},
                },
                ensure_ascii=False,
            )
            # Build a file well over initial_read_bytes so seek must happen.
            lines = [pad_line] * 30 + [tail_line]
            file_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            self.assertGreater(file_path.stat().st_size, self.service.initial_read_bytes)

            with patch.object(self.service, "_ingest_claude_line", new=AsyncMock()) as ingest_mock:
                await self.service._consume_claude_file(file_path)

            ingested_lines = [call.args[0] for call in ingest_mock.await_args_list]
            # Tail must be present; we should NOT have replayed the whole prefix.
            self.assertTrue(any("recent-prompt" in line for line in ingested_lines))
            self.assertLess(
                len(ingested_lines), len(lines),
                "tail-only seek failed; ingested every historical line",
            )

    async def test_consume_claude_file_resumes_from_cursor_offset(self) -> None:
        """When the cursor already exists (subsequent scans), only the
        appended tail since last offset should be ingested."""
        session_id = "00000000-0000-0000-0000-000000000042"
        now = _now_iso()
        with TemporaryDirectory() as claude_dir:
            self.service.claude_session_dir = Path(claude_dir)
            project_dir = self.service.claude_session_dir / "project-y"
            project_dir.mkdir(parents=True, exist_ok=True)
            file_path = project_dir / f"{session_id}.jsonl"
            initial = json.dumps(
                {"type": "user", "sessionId": session_id, "timestamp": now,
                 "cwd": "/tmp/work", "message": {"role": "user", "content": "first"}},
                ensure_ascii=False,
            )
            file_path.write_text(initial + "\n", encoding="utf-8")

            # Make the file shorter than initial_read_bytes so first scan reads from 0.
            self.service.initial_read_bytes = 1024 * 1024
            await self.service._consume_claude_file(file_path)
            cursor_after_first = self.service._claude_files[str(file_path)]
            self.assertGreater(cursor_after_first.offset, 0)

            # Append a new line then re-scan; only the new line should be ingested.
            appended = json.dumps(
                {"type": "user", "sessionId": session_id, "timestamp": now,
                 "cwd": "/tmp/work", "message": {"role": "user", "content": "second"}},
                ensure_ascii=False,
            )
            with file_path.open("a", encoding="utf-8") as f:
                f.write(appended + "\n")

            with patch.object(self.service, "_ingest_claude_line", new=AsyncMock()) as ingest_mock:
                await self.service._consume_claude_file(file_path)
            ingested = [call.args[0] for call in ingest_mock.await_args_list]
            self.assertEqual(len(ingested), 1)
            self.assertIn("second", ingested[0])

    async def test_claude_ingest_broadcasts_session_state_event(self) -> None:
        session_id = "00000000-0000-0000-0000-000000000020"
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
                    "cwd": "/tmp/work",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "我先檢查 bridge 狀態"}],
                        "stop_reason": None,
                    },
                },
                ensure_ascii=False,
            )
            with patch.object(self.service, "_broadcast", new=AsyncMock()) as broadcast_mock:
                await self.service._ingest_claude_line(line, cursor)

        broadcast_mock.assert_awaited_once()
        event = broadcast_mock.await_args.args[0]
        self.assertEqual(event["event"], "session_state")
        self.assertEqual(event["agent_brand"], "claude")
        self.assertEqual(event["source"], "claude_jsonl")
        self.assertEqual(event["session_id"], session_id)
        self.assertEqual(event["state"], "RESPONDING")
        self.assertEqual(event["meta"]["last_event_type"], "assistant_message")


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
        self.assertIn("--sandbox", command)
        self.assertEqual(command[command.index("--sandbox") + 1], EXPECTED_CODEX_AUTOMATION_SANDBOX)
        self.assertNotIn("--full-auto", command)
        self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", command)
        self.assertGreater(command.index("--sandbox"), command.index("exec"))

    async def test_build_cli_command_wires_auto_review_for_auto_mode(self) -> None:
        """Regression: codex auto mode must produce ``-a on-request`` at
        the top level AND ``-c approvals_reviewer="auto_review"`` +
        ``--sandbox <automation sandbox>`` on the exec subcommand. This is the
        non-interactive equivalent of TUI Auto-review."""
        service = CodexSessionChatService(codex_bin="codex", default_cwd="/tmp/workspace")
        command = service._build_cli_command(
            session_id="00000000-0000-0000-0000-000000000123",
            prompt="hello",
            cwd="/tmp/workspace",
            image_paths=[],
            model="gpt-5-codex",
            reasoning_effort="high",
            permission_mode="auto",
            approval_policy=None,
            sandbox_mode=None,
        )
        self.assertIn("-a", command)
        self.assertEqual(command[command.index("-a") + 1], "on-request")
        # -a on-request must precede the exec subcommand.
        self.assertLess(command.index("-a"), command.index("exec"))
        # approvals_reviewer + --sandbox must follow exec.
        self.assertIn('approvals_reviewer="auto_review"', command)
        self.assertGreater(
            command.index('approvals_reviewer="auto_review"'),
            command.index("exec"),
        )
        self.assertIn("--sandbox", command)
        self.assertEqual(
            command[command.index("--sandbox") + 1], EXPECTED_CODEX_AUTOMATION_SANDBOX,
        )
        # Auto mode must NOT include --full-auto or --dangerously-bypass.
        self.assertNotIn("--full-auto", command)
        self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", command)

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
        # Same as --full-auto: belongs after `exec`.
        self.assertGreater(command.index("--dangerously-bypass-approvals-and-sandbox"), command.index("exec"))

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
        self.assertEqual(payload["approval_policy"], "never")
        self.assertEqual(payload["sandbox_mode"], EXPECTED_CODEX_AUTOMATION_SANDBOX)
        cmd = recorded_args.get("cmd", [])
        self.assertIn("--sandbox", cmd)
        self.assertEqual(cmd[cmd.index("--sandbox") + 1], EXPECTED_CODEX_AUTOMATION_SANDBOX)
        self.assertNotIn("--full-auto", cmd)
        self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", cmd)
        self.assertGreater(cmd.index("--sandbox"), cmd.index("exec"))
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
        self.assertEqual(payload["approval_policy"], "never")
        self.assertEqual(payload["sandbox_mode"], EXPECTED_CODEX_AUTOMATION_SANDBOX)

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

class ClaudeSessionChatServiceTest(unittest.IsolatedAsyncioTestCase):
    async def test_stream_prompt_tolerates_dict_usage_and_max_tokens_payloads(self) -> None:
        service = ClaudeSessionChatService(claude_bin="claude", idle_timeout_sec=5, max_timeout_sec=5, default_cwd="/tmp/workspace")
        events = [
            json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "result": "ok",
                    "usage": {
                        "input": {"tokens": 120},
                        "output": {"tokens": 30},
                        "cache_read": {"tokens": 50},
                        "cache_creation": {"tokens": 10},
                    },
                    "max_tokens": {"value": 200000},
                }
            ).encode("utf-8")
            + b"\n",
        ]

        async def _fake_create_subprocess_exec(*_args, **_kwargs):
            return _FakeStreamProcess(stdout_lines=events, returncode=0, stderr=b"")

        emitted: list[dict[str, object]] = []
        with patch("live2d_server.session_bridge_claude_chat.asyncio.create_subprocess_exec", side_effect=_fake_create_subprocess_exec):
            async for item in service.stream_prompt(
                session_id="claude-session-1",
                prompt="hello",
                permission_mode="default",
            ):
                emitted.append(item)

        self.assertEqual(emitted[0]["type"], "text")
        context_events = [item for item in emitted if item.get("type") == "context"]
        self.assertTrue(context_events)
        last_context = context_events[-1].get("content") if isinstance(context_events[-1].get("content"), dict) else {}
        self.assertEqual(last_context.get("total_tokens"), 210)
        self.assertEqual(last_context.get("model_context_window"), 200000)

    async def test_stream_prompt_reads_total_tokens_from_model_usage_result_payload(self) -> None:
        service = ClaudeSessionChatService(claude_bin="claude", idle_timeout_sec=5, max_timeout_sec=5, default_cwd="/tmp/workspace")
        events = [
            json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "modelUsage": {
                        "input_tokens": 1,
                        "cache_creation_input_tokens": 161,
                        "cache_read_input_tokens": 73204,
                        "output_tokens": 1,
                    },
                }
            ).encode("utf-8")
            + b"\n",
        ]

        async def _fake_create_subprocess_exec(*_args, **_kwargs):
            return _FakeStreamProcess(stdout_lines=events, returncode=0, stderr=b"")

        emitted: list[dict[str, object]] = []
        with patch("live2d_server.session_bridge_claude_chat.asyncio.create_subprocess_exec", side_effect=_fake_create_subprocess_exec):
            async for item in service.stream_prompt(
                session_id="claude-session-2",
                prompt="hello",
                permission_mode="default",
            ):
                emitted.append(item)

        context_events = [item for item in emitted if item.get("type") == "context"]
        self.assertTrue(context_events)
        last_context = context_events[-1].get("content") if isinstance(context_events[-1].get("content"), dict) else {}
        self.assertEqual(last_context.get("total_tokens"), 73367)

    async def test_stream_prompt_breaks_after_result_event_and_ignores_trailing_lines(self) -> None:
        """Regression: ``result`` is the terminal event in claude stream-json.
        Older code did ``continue`` after handling it, so when the CLI didn't
        close stdout we sat in the loop until idle timeout. Now we break and
        ignore any trailing events even if stdout is still open."""
        service = ClaudeSessionChatService(
            claude_bin="claude", idle_timeout_sec=5, max_timeout_sec=5,
            default_cwd="/tmp/workspace",
        )
        events = [
            json.dumps({"type": "result", "subtype": "success", "result": "OK"}).encode("utf-8") + b"\n",
            # Any post-result event must be ignored — we already broke out.
            json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "ghost"}]}}).encode("utf-8") + b"\n",
        ]

        async def _fake_create_subprocess_exec(*_args, **_kwargs):
            # returncode=0 simulates a CLI that exits cleanly after the
            # terminal event; behaviour with a hung CLI is exercised by
            # the post-loop kill path, hard to mock here without races.
            return _FakeStreamProcess(stdout_lines=events, returncode=0, stderr=b"")

        emitted: list[dict[str, object]] = []
        with patch(
            "live2d_server.session_bridge_claude_chat.asyncio.create_subprocess_exec",
            side_effect=_fake_create_subprocess_exec,
        ):
            async for item in service.stream_prompt(
                session_id="claude-session-break",
                prompt="hi",
                permission_mode="default",
            ):
                emitted.append(item)

        texts = [str(item.get("content")) for item in emitted if item.get("type") == "text"]
        self.assertIn("OK", texts)
        self.assertNotIn("ghost", texts, "post-result event leaked through")


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
        self.assertEqual(captured.get("approval_policy"), "never")
        self.assertEqual(captured.get("sandbox_mode"), EXPECTED_CODEX_AUTOMATION_SANDBOX)

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
        self.assertEqual(captured.get("approval_policy"), "never")
        self.assertEqual(captured.get("sandbox_mode"), EXPECTED_CODEX_AUTOMATION_SANDBOX)

    async def test_bridge_codex_chat_request_full_mode_uses_dangerous_runtime(self) -> None:
        session = _SessionRecord(
            session_id="00000000-0000-0000-0000-000000000129",
            display_name="session-129",
            cwd="/tmp/work",
            approval_policy="never",
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
        self.assertEqual(captured.get("approval_policy"), "never")
        self.assertEqual(captured.get("sandbox_mode"), EXPECTED_CODEX_AUTOMATION_SANDBOX)

    async def test_bridge_codex_chat_streams_text_and_done(self) -> None:
        session = _SessionRecord(
            session_id="00000000-0000-0000-0000-000000000126",
            display_name="session-126",
            cwd="/tmp/work",
            approval_policy="never",
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
            "approval_policy": "never",
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

    async def test_bridge_codex_new_session_persists_persona_context(self) -> None:
        fake_payload = {
            "session_id": "00000000-0000-0000-0000-000000000302",
            "cwd": "/tmp/work",
            "branch": "main",
            "model": "gpt-5-codex",
            "effort": "medium",
            "permission_mode": "default",
            "approval_policy": "never",
            "sandbox_mode": "workspace-write",
            "plan_mode": False,
            "plan_mode_fallback": False,
        }
        with patch(
            "live2d_server.session_bridge.codex_chat_service.create_session",
            new=AsyncMock(return_value=fake_payload),
        ):
            with patch("live2d_server.session_bridge.bridge_service.upsert_runtime_context", new=AsyncMock()) as upsert_mock:
                payload = await bridge_codex_new_session(
                    CodexNewSessionRequest(
                        cwd="/tmp/work",
                        persona_id="persona-1",
                        persona_name="冷靜 PM",
                        persona_content="請條理分明地回覆。",
                    )
                )
        upsert_mock.assert_awaited_once()
        self.assertEqual(payload["persona_id"], "persona-1")
        self.assertEqual(payload["persona_name"], "冷靜 PM")

    async def test_bridge_codex_new_session_request_permission_mode_overrides_history_runtime(self) -> None:
        fake_payload = {
            "session_id": "00000000-0000-0000-0000-000000000301",
            "cwd": "/tmp/work",
            "branch": "main",
            "model": "gpt-5-codex",
            "effort": "high",
            "permission_mode": "default",
            "approval_policy": "never",
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
        self.assertEqual(payload["approval_policy"], "never")
        self.assertEqual(payload["sandbox_mode"], EXPECTED_CODEX_AUTOMATION_SANDBOX)


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

    def test_default_permission_mode_uses_full_auto_default_for_codex(self) -> None:
        self.assertEqual(AgentProviderRouter.default_permission_mode("codex"), "default")
        self.assertEqual(AgentProviderRouter.default_permission_mode("claude"), "default")

    def test_supported_brands_expose_metadata_for_ui(self) -> None:
        payload = asyncio.run(bridge_agent_brands())
        self.assertIn("brands", payload)
        self.assertTrue(payload["brands"])
        codex = next(item for item in payload["brands"] if item["brand"] == "codex")
        self.assertEqual(codex["display_name"], "Codex")
        self.assertTrue(codex["models"])
        self.assertIn("gpt-5.5", codex["models"])
        self.assertIn("gpt-5.4-mini", codex["models"])
        self.assertEqual(codex["badge_icon"], "/brand/codex-badge.svg")

    def test_supported_brands_include_default_permission_mode(self) -> None:
        payload = asyncio.run(bridge_agent_brands())
        codex = next(item for item in payload["brands"] if item["brand"] == "codex")
        claude = next(item for item in payload["brands"] if item["brand"] == "claude")
        self.assertEqual(codex["default_permission_mode"], "default")
        self.assertEqual(claude["default_permission_mode"], "default")


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
        opencode_service = AsyncMock()
        opencode_service.submit_approval = AsyncMock(return_value=False)

        def _get_chat_service(brand: str):
            return {"codex": codex_service, "claude": claude_service, "opencode": opencode_service}[brand]

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


class SessionBridgeApiRecordResolutionTest(unittest.IsolatedAsyncioTestCase):
    async def test_ensure_session_record_refreshes_claude_runtime_from_disk_metadata(self) -> None:
        from live2d_server.session_bridge_api import _ensure_session_record

        session_id = "556dd05b-8a72-4528-a3dc-1bcf7f0fd757"
        service = SessionBridgeService()
        service._sessions[session_id] = _SessionRecord(
            session_id=session_id,
            display_name="stale claude session",
            cwd="/tmp/wrong-cwd",
            agent_brand="claude",
            originator="Claude Code",
        )

        claude_item = {
            "session_id": session_id,
            "display_name": "restored claude session",
            "state": "IDLE",
            "last_seen_at": "2026-03-25T10:00:00Z",
            "last_seen_epoch": 1_774_397_200.0,
            "originator": "Claude Code",
            "cwd": "/tmp/correct-cwd",
            "cwd_basename": "correct-cwd",
            "last_event_type": "assistant_message",
            "agent_brand": "claude",
            "has_real_user_input": True,
            "context": {
                "model": "sonnet",
                "effort": "",
                "persona_id": "",
                "persona_name": "",
                "persona_content": "",
                "permission_mode": "default",
                "approval_policy": "",
                "sandbox_mode": "",
                "plan_mode": None,
                "plan_mode_fallback": False,
                "total_tokens": 0,
                "model_context_window": 200000,
                "primary_rate_remaining_percent": None,
                "secondary_rate_remaining_percent": None,
            },
        }

        with patch("live2d_server.session_bridge_api.bridge_service", service):
            with patch.object(service, "_collect_history_from_files", return_value={}):
                with patch.object(service, "lookup_claude_session_metadata", return_value=claude_item) as lookup_mock:
                    session = await _ensure_session_record(session_id)

        self.assertIsNotNone(session)
        assert session is not None
        self.assertEqual(session.cwd, "/tmp/correct-cwd")
        self.assertEqual(session.display_name, "restored claude session")
        self.assertEqual(session.agent_brand, "claude")
        lookup_mock.assert_called_once_with(session_id)


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


class BridgeDirectoryBrowseApiTest(unittest.IsolatedAsyncioTestCase):
    async def test_bridge_browse_directories_lists_subdirectories(self) -> None:
        with TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            (base / "alpha").mkdir()
            (base / "beta").mkdir()
            (base / "notes.txt").write_text("demo", encoding="utf-8")

            payload = await bridge_browse_directories(str(base))

        self.assertEqual(payload.current_path, str(base.resolve()))
        self.assertEqual(payload.parent_path, str(base.resolve().parent))
        self.assertEqual([item.name for item in payload.directories], ["alpha", "beta"])
        self.assertEqual(payload.ancestors[-1].path, str(base.resolve()))

    async def test_bridge_browse_directories_raises_404_for_missing_path(self) -> None:
        with TemporaryDirectory() as temp_dir:
            missing_path = str((Path(temp_dir) / "missing").resolve())

            with self.assertRaises(HTTPException) as error_context:
                await bridge_browse_directories(missing_path)

        self.assertEqual(error_context.exception.status_code, 404)


def _fake_kill_tree(process) -> None:
    """Stand-in for _kill_process_tree that just records the kill on a fake
    process object — keeps abort tests independent of OS-level signal delivery."""
    if hasattr(process, "kill"):
        process.kill()


class CodexAbortSessionTest(unittest.IsolatedAsyncioTestCase):
    async def test_abort_session_kills_registered_process(self) -> None:
        service = CodexSessionChatService.__new__(CodexSessionChatService)
        service._active_processes = {}
        service._active_processes_lock = asyncio.Lock()
        process = _FakeStreamProcess([])
        process.returncode = None
        await service._register_active_process("session-1", process)

        with patch("live2d_server.session_bridge_chat._kill_process_tree", side_effect=_fake_kill_tree):
            aborted = await service.abort_session("session-1")
        self.assertTrue(aborted)
        self.assertTrue(process.killed)

    async def test_abort_session_returns_false_when_no_active_process(self) -> None:
        service = CodexSessionChatService.__new__(CodexSessionChatService)
        service._active_processes = {}
        service._active_processes_lock = asyncio.Lock()

        aborted = await service.abort_session("missing-session")
        self.assertFalse(aborted)

    async def test_abort_session_skips_already_finished_process(self) -> None:
        service = CodexSessionChatService.__new__(CodexSessionChatService)
        service._active_processes = {}
        service._active_processes_lock = asyncio.Lock()
        finished = _FakeStreamProcess([], returncode=0)
        await service._register_active_process("session-2", finished)

        aborted = await service.abort_session("session-2")
        self.assertFalse(aborted)
        self.assertFalse(finished.killed)

    async def test_register_kills_previous_in_flight_process(self) -> None:
        service = CodexSessionChatService.__new__(CodexSessionChatService)
        service._active_processes = {}
        service._active_processes_lock = asyncio.Lock()
        first = _FakeStreamProcess([])
        first.returncode = None
        second = _FakeStreamProcess([])

        await service._register_active_process("session-3", first)
        await service._register_active_process("session-3", second)

        self.assertTrue(first.killed, "previous process should be killed when a new one registers")
        self.assertFalse(second.killed)

    async def test_abort_session_invokes_kill_process_tree(self) -> None:
        service = CodexSessionChatService.__new__(CodexSessionChatService)
        service._active_processes = {}
        service._active_processes_lock = asyncio.Lock()
        process = _FakeStreamProcess([])
        process.returncode = None
        await service._register_active_process("tree-session", process)

        with patch("live2d_server.session_bridge_chat._kill_process_tree") as kill_mock:
            await service.abort_session("tree-session")

        # 確保 abort 走的是 process tree kill，而不是只殺父行程 — 避免 Claude
        # spawn 的 sub-agent / MCP server 變孤兒程序繼續執行。
        kill_mock.assert_called_once_with(process)


class ClaudeAbortSessionTest(unittest.IsolatedAsyncioTestCase):
    async def test_abort_session_kills_registered_process(self) -> None:
        service = ClaudeSessionChatService.__new__(ClaudeSessionChatService)
        service._active_processes = {}
        service._active_processes_lock = asyncio.Lock()
        process = _FakeStreamProcess([])
        process.returncode = None
        await service._register_active_process("claude-session", process)

        with patch("live2d_server.session_bridge_claude_chat._kill_process_tree", side_effect=_fake_kill_tree):
            aborted = await service.abort_session("claude-session")
        self.assertTrue(aborted)
        self.assertTrue(process.killed)

    async def test_abort_session_invokes_kill_process_tree(self) -> None:
        service = ClaudeSessionChatService.__new__(ClaudeSessionChatService)
        service._active_processes = {}
        service._active_processes_lock = asyncio.Lock()
        process = _FakeStreamProcess([])
        process.returncode = None
        await service._register_active_process("claude-tree", process)

        with patch("live2d_server.session_bridge_claude_chat._kill_process_tree") as kill_mock:
            await service.abort_session("claude-tree")

        kill_mock.assert_called_once_with(process)


class KillProcessTreeTest(unittest.TestCase):
    def test_returns_immediately_when_process_already_exited(self) -> None:
        from live2d_server.session_bridge_shared import _kill_process_tree

        process = _FakeStreamProcess([], returncode=0)
        # Should be a no-op — never call into OS-level kill primitives.
        # ``create=True`` because killpg is POSIX-only and missing on Windows.
        with patch("live2d_server.session_bridge_shared.subprocess.run") as run_mock, \
             patch("live2d_server.session_bridge_shared.os.killpg", create=True) as killpg_mock:
            _kill_process_tree(process)
        run_mock.assert_not_called()
        killpg_mock.assert_not_called()

    def test_windows_branch_calls_taskkill_with_tree_flag(self) -> None:
        from live2d_server.session_bridge_shared import _kill_process_tree

        process = _FakeStreamProcess([])
        process.returncode = None
        process.pid = 4242

        with patch("live2d_server.session_bridge_shared.sys") as sys_mock, \
             patch("live2d_server.session_bridge_shared.subprocess.run") as run_mock:
            sys_mock.platform = "win32"
            _kill_process_tree(process)

        run_mock.assert_called_once()
        args, kwargs = run_mock.call_args
        cmd = args[0]
        # Use /T to recurse the whole subprocess tree, /F to force.
        self.assertEqual(cmd[0], "taskkill")
        self.assertIn("/F", cmd)
        self.assertIn("/T", cmd)
        self.assertIn("4242", cmd)
        self.assertEqual(kwargs.get("check"), False)

    def test_posix_branch_calls_killpg_on_session_leader(self) -> None:
        from live2d_server.session_bridge_shared import _kill_process_tree

        process = _FakeStreamProcess([])
        process.returncode = None
        process.pid = 9999

        with patch("live2d_server.session_bridge_shared.sys") as sys_mock, \
             patch("live2d_server.session_bridge_shared.os.getpgid", create=True, return_value=9999) as getpgid_mock, \
             patch("live2d_server.session_bridge_shared.os.killpg", create=True) as killpg_mock:
            sys_mock.platform = "linux"
            _kill_process_tree(process)

        getpgid_mock.assert_called_once_with(9999)
        killpg_mock.assert_called_once()
        called_pgid, called_signal = killpg_mock.call_args[0]
        self.assertEqual(called_pgid, 9999)
        # SIGKILL — anything weaker can be ignored by a hung CLI. Fallback to
        # 9 (POSIX numeric value) when the constant is unavailable, e.g. on Windows.
        import signal as _signal
        self.assertEqual(called_signal, getattr(_signal, "SIGKILL", 9))

    def test_posix_swallows_process_lookup_error(self) -> None:
        from live2d_server.session_bridge_shared import _kill_process_tree

        process = _FakeStreamProcess([])
        process.returncode = None
        process.pid = 1234

        with patch("live2d_server.session_bridge_shared.sys") as sys_mock, \
             patch("live2d_server.session_bridge_shared.os.getpgid", create=True, return_value=1234), \
             patch("live2d_server.session_bridge_shared.os.killpg", create=True, side_effect=ProcessLookupError):
            sys_mock.platform = "linux"
            # Must not raise — process already gone is a benign race.
            _kill_process_tree(process)


class IsolatedSubprocessKwargsTest(unittest.TestCase):
    def test_windows_uses_create_new_process_group(self) -> None:
        from live2d_server.session_bridge_shared import _isolated_subprocess_kwargs

        with patch("live2d_server.session_bridge_shared.sys") as sys_mock:
            sys_mock.platform = "win32"
            kwargs = _isolated_subprocess_kwargs()

        self.assertIn("creationflags", kwargs)
        # Avoid asserting the exact int value — it's an OS-defined constant.
        self.assertIsInstance(kwargs["creationflags"], int)

    def test_posix_uses_start_new_session(self) -> None:
        from live2d_server.session_bridge_shared import _isolated_subprocess_kwargs

        with patch("live2d_server.session_bridge_shared.sys") as sys_mock:
            sys_mock.platform = "linux"
            kwargs = _isolated_subprocess_kwargs()

        self.assertEqual(kwargs, {"start_new_session": True})


class BridgeAgentChatAbortApiTest(unittest.IsolatedAsyncioTestCase):
    async def test_abort_dispatches_to_explicit_brand(self) -> None:
        codex_service = AsyncMock()
        codex_service.abort_session = AsyncMock(return_value=False)
        claude_service = AsyncMock()
        claude_service.abort_session = AsyncMock(return_value=True)
        opencode_service = AsyncMock()
        opencode_service.abort_session = AsyncMock(return_value=False)

        def _get_chat_service(brand: str):
            return {"codex": codex_service, "claude": claude_service, "opencode": opencode_service}[brand]

        with patch(
            "live2d_server.session_bridge_api.agent_provider.get_chat_service",
            side_effect=_get_chat_service,
        ):
            payload = await bridge_agent_chat_abort(
                AgentAbortRequest(session_id="abc-123", agent_brand="claude")
            )

        codex_service.abort_session.assert_not_awaited()
        claude_service.abort_session.assert_awaited_once_with("abc-123")
        self.assertTrue(payload["aborted"])
        self.assertEqual(payload["agent_brand"], "claude")
        self.assertEqual(payload["session_id"], "abc-123")

    async def test_abort_falls_back_to_iterating_brands_when_unspecified(self) -> None:
        codex_service = AsyncMock()
        codex_service.abort_session = AsyncMock(return_value=True)
        claude_service = AsyncMock()
        claude_service.abort_session = AsyncMock(return_value=False)
        opencode_service = AsyncMock()
        opencode_service.abort_session = AsyncMock(return_value=False)

        def _get_chat_service(brand: str):
            return {"codex": codex_service, "claude": claude_service, "opencode": opencode_service}[brand]

        with patch(
            "live2d_server.session_bridge_api.agent_provider.get_chat_service",
            side_effect=_get_chat_service,
        ):
            payload = await bridge_agent_chat_abort(AgentAbortRequest(session_id="xyz"))

        codex_service.abort_session.assert_awaited_once_with("xyz")
        # 一旦 codex 已成功中止，就不再嘗試 claude，避免誤殺其他 brand 同名 session。
        claude_service.abort_session.assert_not_awaited()
        self.assertTrue(payload["aborted"])
        self.assertEqual(payload["agent_brand"], "codex")

    async def test_abort_returns_aborted_false_when_nothing_to_kill(self) -> None:
        codex_service = AsyncMock()
        codex_service.abort_session = AsyncMock(return_value=False)
        claude_service = AsyncMock()
        claude_service.abort_session = AsyncMock(return_value=False)
        opencode_service = AsyncMock()
        opencode_service.abort_session = AsyncMock(return_value=False)

        def _get_chat_service(brand: str):
            return {"codex": codex_service, "claude": claude_service, "opencode": opencode_service}[brand]

        with patch(
            "live2d_server.session_bridge_api.agent_provider.get_chat_service",
            side_effect=_get_chat_service,
        ):
            payload = await bridge_agent_chat_abort(AgentAbortRequest(session_id="ghost"))

        self.assertFalse(payload["aborted"])
        self.assertEqual(payload["agent_brand"], "")

    async def test_abort_rejects_empty_session_id(self) -> None:
        with self.assertRaises(HTTPException) as ctx:
            await bridge_agent_chat_abort(AgentAbortRequest(session_id="   "))
        self.assertEqual(ctx.exception.status_code, 422)


# ===========================================================================
# OpencodeSessionChatService tests
# ===========================================================================


class OpencodeStreamEventFactory:
    """Helper to build opencode CLI stream-json events."""

    @staticmethod
    def step_start(session_id: str = "ses_test123") -> str:
        return json.dumps({
            "type": "step_start",
            "timestamp": 1780385503401,
            "sessionID": session_id,
            "part": {
                "id": "prt_test1",
                "messageID": "msg_test1",
                "sessionID": session_id,
                "snapshot": "abc123",
                "type": "step-start",
            },
        })

    @staticmethod
    def text(text: str, session_id: str = "ses_test123") -> str:
        return json.dumps({
            "type": "text",
            "timestamp": 1780385504940,
            "sessionID": session_id,
            "part": {
                "id": "prt_test2",
                "messageID": "msg_test1",
                "sessionID": session_id,
                "type": "text",
                "text": text,
            },
        })

    @staticmethod
    def step_finish(
        total_tokens: int = 9421,
        session_id: str = "ses_test123",
        text: str = "",
    ) -> str:
        ev = {
            "type": "step_finish",
            "timestamp": 1780385505094,
            "sessionID": session_id,
            "part": {
                "id": "prt_test3",
                "reason": "stop",
                "snapshot": "abc123",
                "messageID": "msg_test1",
                "sessionID": session_id,
                "type": "step-finish",
                "tokens": {
                    "total": total_tokens,
                    "input": 7495,
                    "output": 20,
                    "reasoning": 0,
                    "cache": {"write": 0, "read": 1906},
                },
                "cost": 0,
            },
        }
        if text:
            ev["part"]["text"] = text
        return json.dumps(ev)

    @staticmethod
    def error(message: str = "test error") -> str:
        return json.dumps({
            "type": "error",
            "timestamp": 1780385473396,
            "sessionID": "ses_test123",
            "error": {
                "name": "UnknownError",
                "data": {"message": message},
            },
        })


class OpencodeSessionChatServiceTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.service = OpencodeSessionChatService(
            opencode_bin="opencode",
            idle_timeout_sec=300,
            max_timeout_sec=600,
            default_cwd="/tmp",
        )

    async def test_constructor(self) -> None:
        self.assertEqual(self.service.opencode_bin, "opencode")
        self.assertEqual(self.service.idle_timeout_sec, 300)
        self.assertEqual(self.service.max_timeout_sec, 600)

    async def test_abort_session_returns_false_for_empty_session_id(self) -> None:
        result = await self.service.abort_session("")
        self.assertFalse(result)

    async def test_abort_session_returns_false_for_unknown_session(self) -> None:
        result = await self.service.abort_session("nonexistent")
        self.assertFalse(result)

    async def test_submit_approval_returns_false(self) -> None:
        result = await self.service.submit_approval("x", "allow_once")
        self.assertFalse(result)

    async def test_build_cli_command_does_not_skip_permissions_by_default(self) -> None:
        command = self.service._build_cli_command(
            session_id="ses_123",
            prompt="hello",
            cwd="/tmp",
            image_paths=[],
            model="opencode/test-model",
            reasoning_effort=None,
            permission_mode="default",
            approval_policy=None,
            sandbox_mode=None,
        )

        self.assertNotIn("--dangerously-skip-permissions", command)

    async def test_build_cli_command_skips_permissions_only_for_full_mode(self) -> None:
        command = self.service._build_cli_command(
            session_id="ses_123",
            prompt="hello",
            cwd="/tmp",
            image_paths=[],
            model="opencode/test-model",
            reasoning_effort=None,
            permission_mode="full",
            approval_policy=None,
            sandbox_mode=None,
        )

        self.assertIn("--dangerously-skip-permissions", command)

    @patch(
        "live2d_server.session_bridge_opencode_chat.asyncio.create_subprocess_exec",
        autospec=True,
    )
    async def test_create_session_extracts_session_id_from_step_start(
        self, mock_create_subprocess: MagicMock,
    ) -> None:
        mock_process = AsyncMock()
        mock_process.returncode = 0
        mock_process.stdout = AsyncMock()
        mock_process.stdout.readline = AsyncMock(side_effect=[b"", b""])
        mock_process.stdout.read = AsyncMock(return_value=b"")
        mock_process.stderr = AsyncMock()
        mock_process.stderr.read = AsyncMock(return_value=b"")
        mock_process.communicate = AsyncMock(return_value=(
            b"{}\n" + OpencodeStreamEventFactory.step_start("ses_new123").encode() + b"\n{}",
            b"",
        ))
        mock_create_subprocess.return_value = mock_process

        result = await self.service.create_session(cwd="/tmp", model="opencode/test-model")
        self.assertEqual(result["session_id"], "ses_new123")
        self.assertEqual(result["cwd"], "/tmp")
        self.assertEqual(result["model"], "opencode/test-model")
        self.assertEqual(result["permission_mode"], "default")

    @patch(
        "live2d_server.session_bridge_opencode_chat.asyncio.create_subprocess_exec",
        autospec=True,
    )
    async def test_create_session_raises_on_no_session_id(
        self, mock_create_subprocess: MagicMock,
    ) -> None:
        mock_process = AsyncMock()
        mock_process.returncode = 0
        mock_process.communicate = AsyncMock(return_value=(b"{}", b""))
        mock_create_subprocess.return_value = mock_process

        with self.assertRaises(OpencodeSessionChatError):
            await self.service.create_session(cwd="/tmp")

    @patch(
        "live2d_server.session_bridge_opencode_chat.asyncio.create_subprocess_exec",
        autospec=True,
    )
    async def test_create_session_raises_on_cli_failure(
        self, mock_create_subprocess: MagicMock,
    ) -> None:
        mock_process = AsyncMock()
        mock_process.returncode = 1
        mock_process.communicate = AsyncMock(return_value=(b"", b"some error"))
        mock_create_subprocess.return_value = mock_process

        with self.assertRaises(OpencodeSessionChatError):
            await self.service.create_session(cwd="/tmp")

    @patch(
        "live2d_server.session_bridge_opencode_chat.asyncio.create_subprocess_exec",
        autospec=True,
    )
    async def test_stream_prompt_emits_context_and_text_and_token_events(
        self, mock_create_subprocess: MagicMock,
    ) -> None:
        sid = "ses_stream123"
        events = [
            OpencodeStreamEventFactory.step_start(sid),
            OpencodeStreamEventFactory.text("Hello from opencode", sid),
            OpencodeStreamEventFactory.step_finish(total_tokens=9876, session_id=sid),
        ]
        encoded_lines = [ev.encode() + b"\n" for ev in events]

        mock_process = AsyncMock()
        mock_process.returncode = 0
        mock_process.stdout = AsyncMock()

        async def _readline():
            if encoded_lines:
                return encoded_lines.pop(0)
            return b""

        mock_process.stdout.readline = AsyncMock(side_effect=_readline)
        mock_process.stdout.read = AsyncMock(return_value=b"")
        mock_process.stderr = AsyncMock()
        mock_process.stderr.read = AsyncMock(return_value=b"")
        mock_process.wait = AsyncMock(return_value=0)
        mock_create_subprocess.return_value = mock_process

        results: list[dict] = []
        async for event in self.service.stream_prompt(sid, "hello"):
            results.append(event)

        types = [r["type"] for r in results]
        self.assertIn("context", types)
        self.assertIn("text", types)

        text_events = [r for r in results if r["type"] == "text"]
        self.assertTrue(any("Hello" in str(t.get("content", "")) for t in text_events))

        context_events = [r for r in results if r["type"] == "context"]
        token_ctx = [c for c in context_events if "total_tokens" in (c.get("content") or {})]
        if token_ctx:
            self.assertEqual(token_ctx[0]["content"]["total_tokens"], 9876)

    @patch(
        "live2d_server.session_bridge_opencode_chat.asyncio.create_subprocess_exec",
        autospec=True,
    )
    async def test_stream_prompt_emits_error_event_on_cli_error(
        self, mock_create_subprocess: MagicMock,
    ) -> None:
        sid = "ses_err123"
        mock_process = AsyncMock()
        mock_process.returncode = 1
        mock_process.stdout = AsyncMock()
        mock_process.stdout.readline = AsyncMock(return_value=b"")
        mock_process.stdout.read = AsyncMock(return_value=b"")
        mock_process.stderr = AsyncMock()
        mock_process.stderr.read = AsyncMock(return_value=b"error output")
        mock_create_subprocess.return_value = mock_process

        with self.assertRaises(OpencodeSessionChatError):
            async for _ in self.service.stream_prompt(sid, "hello"):
                pass

    @patch(
        "live2d_server.session_bridge_opencode_chat.asyncio.create_subprocess_exec",
        autospec=True,
    )
    async def test_stream_prompt_handles_json_error_event(
        self, mock_create_subprocess: MagicMock,
    ) -> None:
        sid = "ses_errevent"
        events = [
            OpencodeStreamEventFactory.step_start(sid),
            OpencodeStreamEventFactory.error("Model not found: test"),
        ]
        encoded_lines = [ev.encode() + b"\n" for ev in events]

        mock_process = AsyncMock()
        mock_process.returncode = None
        mock_process.stdout = AsyncMock()
        mock_process.wait = AsyncMock(return_value=0)

        async def _readline():
            if encoded_lines:
                return encoded_lines.pop(0)
            return b""

        mock_process.stdout.readline = AsyncMock(side_effect=_readline)
        mock_process.stdout.read = AsyncMock(return_value=b"")
        mock_process.stderr = AsyncMock()
        mock_process.stderr.read = AsyncMock(return_value=b"")
        mock_create_subprocess.return_value = mock_process

        results: list[dict] = []
        with patch("live2d_server.session_bridge_opencode_chat._kill_process_tree") as kill_mock:
            async for event in self.service.stream_prompt(sid, "hello"):
                results.append(event)

        error_events = [r for r in results if r["type"] == "error"]
        self.assertTrue(len(error_events) >= 1)
        self.assertIn("Model not found", str(error_events[0].get("content", "")))
        kill_mock.assert_called_once_with(mock_process)
        mock_process.wait.assert_awaited()

    async def test_stream_prompt_rejects_empty_session_id(self) -> None:
        with self.assertRaises(OpencodeSessionChatError):
            async for _ in self.service.stream_prompt("", "hello"):
                pass

    async def test_stream_prompt_rejects_empty_prompt(self) -> None:
        with self.assertRaises(OpencodeSessionChatError):
            async for _ in self.service.stream_prompt("ses_123", ""):
                pass


class AgentProviderRouterOpencodeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.router = AgentProviderRouter(default_cwd="/tmp")

    def test_get_chat_service_returns_opencode_service(self) -> None:
        service = self.router.get_chat_service(AGENT_BRAND_OPENCODE)
        from live2d_server.session_bridge_opencode_chat import OpencodeSessionChatService
        self.assertIsInstance(service, OpencodeSessionChatService)

    def test_get_chat_service_opencode_is_cached(self) -> None:
        s1 = self.router.get_chat_service(AGENT_BRAND_OPENCODE)
        s2 = self.router.get_chat_service(AGENT_BRAND_OPENCODE)
        self.assertIs(s1, s2)

    def test_normalize_brand_accepts_opencode(self) -> None:
        self.assertEqual(
            self.router.normalize_brand("opencode"),
            AGENT_BRAND_OPENCODE,
        )
        self.assertEqual(
            self.router.normalize_brand("OPENCODE"),
            AGENT_BRAND_OPENCODE,
        )

    def test_brand_catalog_includes_opencode(self) -> None:
        catalog = self.router.brand_catalog()
        brands = [b["brand"] for b in catalog]
        self.assertIn(AGENT_BRAND_OPENCODE, brands)

        opencode_entry = next(b for b in catalog if b["brand"] == AGENT_BRAND_OPENCODE)
        self.assertIn("display_name", opencode_entry)
        self.assertIn("models", opencode_entry)
        self.assertTrue(len(opencode_entry["models"]) > 0)

    def test_supported_brands_includes_opencode(self) -> None:
        brands = self.router.supported_brands()
        self.assertIn(AGENT_BRAND_OPENCODE, brands)

    def test_get_session_dir_returns_opencode_dir(self) -> None:
        d = self.router.get_session_dir(AGENT_BRAND_OPENCODE)
        self.assertTrue("opencode" in str(d).lower())

    def test_default_models_returns_opencode_models(self) -> None:
        models = self.router.default_models(AGENT_BRAND_OPENCODE)
        self.assertTrue(len(models) > 0)
        self.assertTrue(any("opencode/" in m for m in models))


# ===========================================================================
# Opencode API endpoint integration tests
# ===========================================================================


class BridgeAgentBrandsOpencodeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.router = AgentProviderRouter(default_cwd="/tmp")

    def test_agent_brands_endpoint_returns_opencode(self) -> None:
        catalog = AgentProviderRouter.brand_catalog()
        brands = [b["brand"] for b in catalog]
        self.assertIn(AGENT_BRAND_OPENCODE, brands)
        self.assertIn("codex", brands)
        self.assertIn("claude", brands)
        self.assertEqual(len(brands), 3)


if __name__ == "__main__":
    unittest.main()
