"""Tests for stage-2 master-agent tools: query/list/wait/abort/approval/git."""

from __future__ import annotations

import asyncio
import subprocess
import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from live2d_server.master_agent.contracts.tool_port import ToolContext
from live2d_server.master_agent.shared import SubTask
from live2d_server.master_agent.task_tracker import SubTaskTracker
from live2d_server.master_agent.tools.abort_approval_tools import (
    AbortSessionTool,
    ApprovePendingTool,
)
from live2d_server.master_agent.tools.git_tools import (
    ListBranchesTool,
    SwitchBranchTool,
)
from live2d_server.master_agent.tools.session_query_tools import (
    GetSessionConversationTool,
    ListHistorySessionsTool,
    ListSessionsTool,
    ListSubTasksTool,
    QuerySessionStatusTool,
    SearchSessionsTool,
)
from live2d_server.master_agent.tools.subtask_tools import WaitForSubTaskTool


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _FakeChatService:
    def __init__(self) -> None:
        self.abort_calls: list[str] = []
        self.approval_calls: list[tuple[str, str, list[str] | None]] = []
        self.abort_result = True
        self.approval_result = True
        self.abort_exception: Exception | None = None
        self.approval_exception: Exception | None = None

    async def abort_session(self, session_id: str) -> bool:
        self.abort_calls.append(session_id)
        if self.abort_exception:
            raise self.abort_exception
        return self.abort_result

    async def submit_approval(self, pending_id, decision, prefix_rule=None):
        self.approval_calls.append((pending_id, decision, prefix_rule))
        if self.approval_exception:
            raise self.approval_exception
        return self.approval_result


class _FakeProvider:
    def __init__(self) -> None:
        self.codex = _FakeChatService()
        self.claude = _FakeChatService()

    def get_chat_service(self, brand: str):
        return self.codex if brand == "codex" else self.claude


def _ctx(
    args: dict,
    *,
    tracker=None,
    provider=None,
    bridge=None,
    conversation_id="c1",
    default_cwd=None,
    permit_full_access: bool = False,
) -> ToolContext:
    return ToolContext(
        conversation_id=conversation_id,
        arguments=args,
        services=SimpleNamespace(
            agent_provider=provider or _FakeProvider(),
            bridge_service=bridge or SimpleNamespace(),
            task_tracker=tracker or SubTaskTracker(),
            loop=None,
            permit_full_access=permit_full_access,
        ),
        default_cwd=default_cwd,
    )


def _make_subtask(conv_id: str = "c1", brand: str = "codex") -> SubTask:
    return SubTask.new(
        conversation_id=conv_id,
        agent_brand=brand,
        session_id="sess-1",
        prompt="hi",
        cwd="/tmp",
    )


# ---------------------------------------------------------------------------
# Query / list tools
# ---------------------------------------------------------------------------


class QuerySessionStatusToolTest(unittest.IsolatedAsyncioTestCase):
    async def test_lookup_by_subtask_id(self) -> None:
        tracker = SubTaskTracker()
        subtask = _make_subtask()
        await tracker.create(subtask)
        await tracker.update_status(subtask.id, status="running")
        result = await QuerySessionStatusTool().invoke(
            _ctx({"subtask_id": subtask.id}, tracker=tracker)
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.data["subtask"]["id"], subtask.id)
        self.assertEqual(result.data["subtask"]["status"], "running")

    async def test_lookup_by_session_id_uses_bridge_service(self) -> None:
        record = SimpleNamespace(
            session_id="abc", agent_brand="claude", state="THINKING",
            active=True, cwd="/tmp", branch="main", model="opus", effort="",
            permission_mode="default", plan_mode=False, last_event_type="user_message",
            display_name="session-abc",
        )
        bridge = SimpleNamespace(get_session_record=AsyncMock(return_value=record))
        result = await QuerySessionStatusTool().invoke(
            _ctx({"session_id": "abc"}, bridge=bridge)
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.data["session_id"], "abc")
        self.assertEqual(result.data["agent_brand"], "claude")

    async def test_requires_one_id(self) -> None:
        result = await QuerySessionStatusTool().invoke(_ctx({}))
        self.assertFalse(result.ok)

    async def test_rejects_both_ids(self) -> None:
        result = await QuerySessionStatusTool().invoke(
            _ctx({"session_id": "x", "subtask_id": "y"})
        )
        self.assertFalse(result.ok)


class ListSessionsToolTest(unittest.IsolatedAsyncioTestCase):
    async def test_returns_summaries_from_snapshot(self) -> None:
        bridge = SimpleNamespace(
            get_snapshot=AsyncMock(return_value={
                "sessions": [
                    {"session_id": "s1", "agent_brand": "codex", "state": "IDLE",
                     "cwd": "/x", "display_name": "n1", "last_event_type": "x",
                     "last_seen_at": "now"},
                ],
            }),
        )
        result = await ListSessionsTool().invoke(_ctx({}, bridge=bridge))
        self.assertTrue(result.ok)
        self.assertEqual(len(result.data["sessions"]), 1)
        self.assertEqual(result.data["sessions"][0]["session_id"], "s1")


class ListHistorySessionsToolTest(unittest.IsolatedAsyncioTestCase):
    async def test_returns_filtered_history(self) -> None:
        bridge = SimpleNamespace(
            get_history=AsyncMock(return_value={
                "sessions": [
                    {"session_id": "s1", "agent_brand": "codex", "cwd": "/a", "display_name": "n1"},
                    {"session_id": "s2", "agent_brand": "claude", "cwd": "/b", "display_name": "n2"},
                    {"session_id": "s3", "agent_brand": "codex", "cwd": "/a", "display_name": "n3"},
                ],
            }),
        )
        # No filter: all 3
        result_all = await ListHistorySessionsTool().invoke(_ctx({}, bridge=bridge))
        self.assertEqual(len(result_all.data["sessions"]), 3)
        # Brand filter
        result_codex = await ListHistorySessionsTool().invoke(
            _ctx({"agent_brand": "codex"}, bridge=bridge)
        )
        self.assertEqual(len(result_codex.data["sessions"]), 2)
        self.assertTrue(all(s["agent_brand"] == "codex" for s in result_codex.data["sessions"]))
        # cwd substring
        result_a = await ListHistorySessionsTool().invoke(
            _ctx({"cwd_substring": "/a"}, bridge=bridge)
        )
        self.assertEqual(len(result_a.data["sessions"]), 2)

    async def test_rejects_unknown_brand(self) -> None:
        result = await ListHistorySessionsTool().invoke(
            _ctx({"agent_brand": "mistral"}, bridge=SimpleNamespace(get_history=AsyncMock()))
        )
        self.assertFalse(result.ok)


class GetSessionConversationToolTest(unittest.IsolatedAsyncioTestCase):
    async def test_returns_trimmed_messages(self) -> None:
        bridge = SimpleNamespace(
            get_conversation=AsyncMock(return_value={
                "messages": [
                    {"role": "user", "content": "hi", "timestamp": "t1"},
                    {"role": "assistant", "content": "X" * 2000, "timestamp": "t2"},
                ],
            }),
        )
        result = await GetSessionConversationTool().invoke(
            _ctx({"session_id": "abc"}, bridge=bridge)
        )
        self.assertTrue(result.ok)
        self.assertEqual(len(result.data["messages"]), 2)
        # Long assistant message must be truncated.
        self.assertIn("...<truncated>", result.data["messages"][1]["content"])

    async def test_empty_conversation_fails(self) -> None:
        bridge = SimpleNamespace(
            get_conversation=AsyncMock(return_value={"messages": []}),
        )
        result = await GetSessionConversationTool().invoke(
            _ctx({"session_id": "abc"}, bridge=bridge)
        )
        self.assertFalse(result.ok)

    async def test_requires_session_id(self) -> None:
        result = await GetSessionConversationTool().invoke(_ctx({}))
        self.assertFalse(result.ok)


class ListSubTasksToolTest(unittest.IsolatedAsyncioTestCase):
    async def test_filters_by_status(self) -> None:
        tracker = SubTaskTracker()
        a = _make_subtask()
        b = _make_subtask()
        await tracker.create(a)
        await tracker.create(b)
        await tracker.update_status(a.id, status="done")
        result_all = await ListSubTasksTool().invoke(_ctx({}, tracker=tracker))
        self.assertEqual(len(result_all.data["subtasks"]), 2)
        result_done = await ListSubTasksTool().invoke(
            _ctx({"status": "done"}, tracker=tracker)
        )
        self.assertEqual(len(result_done.data["subtasks"]), 1)
        self.assertEqual(result_done.data["subtasks"][0]["status"], "done")


# ---------------------------------------------------------------------------
# wait_for_subtask
# ---------------------------------------------------------------------------


class WaitForSubTaskToolTest(unittest.IsolatedAsyncioTestCase):
    async def test_returns_final_text_when_terminal(self) -> None:
        tracker = SubTaskTracker()
        subtask = _make_subtask()
        await tracker.create(subtask)
        await tracker.update_status(subtask.id, status="running")

        async def _finisher() -> None:
            await asyncio.sleep(0.02)
            await tracker.append_event(subtask.id, {"type": "text", "content": "OK"})
            await tracker.update_status(subtask.id, status="done", final_text="OK")

        finisher = asyncio.create_task(_finisher())
        result = await WaitForSubTaskTool().invoke(
            _ctx({"subtask_id": subtask.id, "timeout_sec": 5}, tracker=tracker)
        )
        await finisher
        self.assertTrue(result.ok)
        self.assertTrue(result.data["terminal"])
        self.assertEqual(result.data["subtask"]["status"], "done")
        self.assertEqual(result.data["subtask"]["final_text"], "OK")

    async def test_reports_timeout_when_not_terminal(self) -> None:
        tracker = SubTaskTracker()
        subtask = _make_subtask()
        await tracker.create(subtask)
        await tracker.update_status(subtask.id, status="running")
        result = await WaitForSubTaskTool().invoke(
            _ctx({"subtask_id": subtask.id, "timeout_sec": 1.2}, tracker=tracker)
        )
        self.assertTrue(result.ok)
        self.assertFalse(result.data["terminal"])
        self.assertTrue(result.data["timed_out"])

    async def test_detached_status_includes_query_hint(self) -> None:
        """Regression: when subtask ends with status='detached', the wait
        result must direct the LLM to call query_session_status rather
        than reporting failure."""
        tracker = SubTaskTracker()
        subtask = SubTask.new(
            conversation_id="c1", agent_brand="codex", session_id="sess-X",
            prompt="p", cwd="/tmp",
        )
        await tracker.create(subtask)
        await tracker.update_status(
            subtask.id, status="detached",
            final_text="halfway done",
            error="stream ended; session still active on disk",
        )
        result = await WaitForSubTaskTool().invoke(
            _ctx({"subtask_id": subtask.id, "timeout_sec": 0.5}, tracker=tracker)
        )
        self.assertTrue(result.ok)
        self.assertTrue(result.data["terminal"])
        self.assertEqual(result.data["subtask"]["status"], "detached")
        self.assertIn("query_session_status", result.output_text)
        self.assertIn("sess-X", result.output_text)
        self.assertIn("halfway done", result.output_text)

    async def test_surfaces_partial_text_on_timeout(self) -> None:
        """Regression: when the wait times out mid-stream, the tool must
        still return the text chunks already collected so the master
        agent can show progress instead of empty "still running"."""
        tracker = SubTaskTracker()
        subtask = _make_subtask()
        await tracker.create(subtask)
        await tracker.update_status(subtask.id, status="running")

        async def _stream() -> None:
            await asyncio.sleep(0.02)
            await tracker.append_event(subtask.id, {"type": "text", "content": "step1 done"})
            await tracker.append_event(subtask.id, {"type": "text", "content": " step2 done"})
            # Subtask stays in "running" — never terminal within wait window.

        producer = asyncio.create_task(_stream())
        result = await WaitForSubTaskTool().invoke(
            _ctx({"subtask_id": subtask.id, "timeout_sec": 1.0}, tracker=tracker)
        )
        await producer
        self.assertTrue(result.ok)
        self.assertFalse(result.data["terminal"])
        self.assertIn("step1 done", result.data["partial_text"])
        self.assertIn("step2 done", result.data["partial_text"])
        self.assertIn("partial output", result.output_text)

    async def test_missing_subtask_fails(self) -> None:
        result = await WaitForSubTaskTool().invoke(
            _ctx({"subtask_id": "nope"})
        )
        self.assertFalse(result.ok)


# ---------------------------------------------------------------------------
# Abort / approval
# ---------------------------------------------------------------------------


class AbortSessionToolTest(unittest.IsolatedAsyncioTestCase):
    async def test_routes_to_correct_brand(self) -> None:
        provider = _FakeProvider()
        result = await AbortSessionTool().invoke(
            _ctx({"session_id": "abc", "agent_brand": "claude"}, provider=provider)
        )
        self.assertTrue(result.ok)
        self.assertTrue(result.data["aborted"])
        self.assertEqual(provider.claude.abort_calls, ["abc"])
        self.assertEqual(provider.codex.abort_calls, [])

    async def test_reports_no_live_process_gracefully(self) -> None:
        provider = _FakeProvider()
        provider.codex.abort_result = False
        result = await AbortSessionTool().invoke(
            _ctx({"session_id": "x", "agent_brand": "codex"}, provider=provider)
        )
        self.assertTrue(result.ok)
        self.assertFalse(result.data["aborted"])

    async def test_rejects_unknown_brand(self) -> None:
        result = await AbortSessionTool().invoke(
            _ctx({"session_id": "x", "agent_brand": "gemini"})
        )
        self.assertFalse(result.ok)


class ApprovePendingToolTest(unittest.IsolatedAsyncioTestCase):
    async def test_allow_once_passes_through(self) -> None:
        provider = _FakeProvider()
        result = await ApprovePendingTool().invoke(
            _ctx({"pending_id": "p1", "decision": "allow_once", "agent_brand": "codex"},
                 provider=provider)
        )
        self.assertTrue(result.ok)
        self.assertEqual(provider.codex.approval_calls, [("p1", "allow_once", [])])

    async def test_allow_prefix_requires_prefix_rule(self) -> None:
        result = await ApprovePendingTool().invoke(
            _ctx({"pending_id": "p", "decision": "allow_prefix", "agent_brand": "codex"})
        )
        self.assertFalse(result.ok)

    async def test_allow_prefix_with_rule(self) -> None:
        provider = _FakeProvider()
        result = await ApprovePendingTool().invoke(
            _ctx({
                "pending_id": "p2", "decision": "allow_prefix",
                "agent_brand": "claude", "prefix_rule": ["npm", "install"],
            }, provider=provider)
        )
        self.assertTrue(result.ok)
        self.assertEqual(
            provider.claude.approval_calls[0], ("p2", "allow_prefix", ["npm", "install"]),
        )

    async def test_invalid_decision_rejected(self) -> None:
        result = await ApprovePendingTool().invoke(
            _ctx({"pending_id": "p", "decision": "maybe", "agent_brand": "codex"})
        )
        self.assertFalse(result.ok)


# ---------------------------------------------------------------------------
# Git tools
# ---------------------------------------------------------------------------


def _fake_completed(returncode: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=["git"], returncode=returncode, stdout=stdout, stderr=stderr)


class SearchSessionsToolTest(unittest.IsolatedAsyncioTestCase):
    async def test_finds_session_by_content_substring(self) -> None:
        import json as _json
        from pathlib import Path
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as tmp:
            session_dir = Path(tmp)
            # Two codex sessions: one mentions "auth", one doesn't.
            (session_dir / "abc-auth.jsonl").write_text(
                "\n".join([
                    _json.dumps({"type": "event_msg", "payload": {
                        "type": "user_message", "message": "請改 auth middleware",
                    }}),
                    _json.dumps({"type": "event_msg", "payload": {
                        "type": "agent_message", "message": "好的我來看 auth",
                    }}),
                ]) + "\n",
                encoding="utf-8",
            )
            (session_dir / "def-other.jsonl").write_text(
                _json.dumps({"type": "event_msg", "payload": {
                    "type": "user_message", "message": "查一下日誌",
                }}) + "\n",
                encoding="utf-8",
            )
            bridge = SimpleNamespace(
                get_history=AsyncMock(return_value={
                    "sessions": [
                        {"session_id": "abc-auth", "agent_brand": "codex",
                         "cwd": "/proj/x", "display_name": "請改 auth middleware",
                         "last_seen_at": "2026-05-14"},
                        {"session_id": "def-other", "agent_brand": "codex",
                         "cwd": "/proj/x", "display_name": "查一下日誌",
                         "last_seen_at": "2026-05-13"},
                    ],
                }),
            )
            provider = SimpleNamespace(
                get_session_dir=MagicMock(return_value=session_dir),
            )
            result = await SearchSessionsTool().invoke(
                _ctx({"query": "auth"}, provider=provider, bridge=bridge),
            )
            self.assertTrue(result.ok)
            self.assertEqual(len(result.data["matches"]), 1)
            self.assertEqual(result.data["matches"][0]["session_id"], "abc-auth")
            self.assertIn("auth", result.data["matches"][0]["snippet"].lower())

    async def test_brand_filter_skips_other_brands(self) -> None:
        from pathlib import Path
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as tmp:
            session_dir = Path(tmp)
            (session_dir / "x.jsonl").write_text(
                '{"type":"event_msg","payload":{"message":"hello auth"}}\n',
                encoding="utf-8",
            )
            bridge = SimpleNamespace(
                get_history=AsyncMock(return_value={
                    "sessions": [
                        {"session_id": "x", "agent_brand": "claude",
                         "cwd": "/p", "display_name": "z", "last_seen_at": ""},
                    ],
                }),
            )
            provider = SimpleNamespace(
                get_session_dir=MagicMock(return_value=session_dir),
            )
            # Filter to codex but the only candidate is claude → no matches.
            result = await SearchSessionsTool().invoke(
                _ctx({"query": "auth", "agent_brand": "codex"},
                     provider=provider, bridge=bridge),
            )
            self.assertTrue(result.ok)
            self.assertEqual(len(result.data["matches"]), 0)

    async def test_cwd_substring_narrows_scan(self) -> None:
        bridge = SimpleNamespace(
            get_history=AsyncMock(return_value={
                "sessions": [
                    {"session_id": "a", "agent_brand": "codex", "cwd": "/x/repo-A"},
                    {"session_id": "b", "agent_brand": "codex", "cwd": "/x/repo-B"},
                ],
            }),
        )
        provider = SimpleNamespace(get_session_dir=MagicMock(return_value=None))
        # session_dir=None → _find_session_file returns None → empty matches,
        # but `scanned` should show the cwd filter narrowed candidates.
        result = await SearchSessionsTool().invoke(
            _ctx({"query": "anything", "cwd_substring": "repo-A"},
                 provider=provider, bridge=bridge),
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.data["scanned"], 1)

    async def test_requires_query(self) -> None:
        result = await SearchSessionsTool().invoke(_ctx({}))
        self.assertFalse(result.ok)

    async def test_rejects_unknown_brand(self) -> None:
        result = await SearchSessionsTool().invoke(
            _ctx({"query": "x", "agent_brand": "gemini"})
        )
        self.assertFalse(result.ok)


class ListAvailableModelsToolTest(unittest.IsolatedAsyncioTestCase):
    async def test_returns_full_catalog(self) -> None:
        from live2d_server.master_agent.tools.model_catalog_tool import (
            ListAvailableModelsTool,
        )
        provider = SimpleNamespace(
            brand_catalog=MagicMock(return_value=[
                {"brand": "codex", "display_name": "Codex",
                 "models": ["m1", "m2"], "default_permission_mode": "default"},
                {"brand": "claude", "display_name": "Claude",
                 "models": ["c1"], "default_permission_mode": "default"},
            ]),
        )
        result = await ListAvailableModelsTool().invoke(_ctx({}, provider=provider))
        self.assertTrue(result.ok)
        self.assertEqual(len(result.data["brands"]), 2)

    async def test_filters_by_brand(self) -> None:
        from live2d_server.master_agent.tools.model_catalog_tool import (
            ListAvailableModelsTool,
        )
        provider = SimpleNamespace(
            brand_catalog=MagicMock(return_value=[
                {"brand": "codex", "models": ["m"]},
                {"brand": "claude", "models": ["c"]},
            ]),
        )
        result = await ListAvailableModelsTool().invoke(
            _ctx({"agent_brand": "claude"}, provider=provider)
        )
        self.assertTrue(result.ok)
        self.assertEqual(len(result.data["brands"]), 1)
        self.assertEqual(result.data["brands"][0]["brand"], "claude")


class SessionParamsForwardingTest(unittest.IsolatedAsyncioTestCase):
    """Regression: new_session + send_prompt must forward all four tuning
    params (model, reasoning_effort, permission_mode, plan_mode) so the
    user can override per-turn behavior."""

    async def test_codex_new_session_forwards_all_params(self) -> None:
        from live2d_server.master_agent.tools.codex_session_tools import (
            CodexNewSessionTool,
        )

        captured: dict[str, object] = {}

        class _Capture:
            async def create_session(self, **kwargs):
                captured.update(kwargs)
                return {"session_id": "x", "cwd": kwargs.get("cwd")}

        provider = SimpleNamespace(get_chat_service=lambda brand: _Capture())
        result = await CodexNewSessionTool().invoke(_ctx({
            "cwd": "/tmp",
            "model": "gpt-5.2-codex",
            "reasoning_effort": "high",
            "permission_mode": "auto",
            "plan_mode": True,
        }, provider=provider))
        self.assertTrue(result.ok)
        self.assertEqual(captured["model"], "gpt-5.2-codex")
        self.assertEqual(captured["reasoning_effort"], "high")
        self.assertEqual(captured["permission_mode"], "auto")
        self.assertIs(captured["plan_mode"], True)

    async def test_codex_send_prompt_forwards_all_params_to_stream(self) -> None:
        from live2d_server.master_agent.tools.codex_session_tools import (
            CodexSendPromptTool,
        )

        captured: dict[str, object] = {}
        finished = asyncio.Event()

        class _Capture:
            async def stream_prompt(self, **kwargs):
                captured.update(kwargs)
                finished.set()
                return
                yield  # pragma: no cover — makes this a generator

        provider = SimpleNamespace(get_chat_service=lambda brand: _Capture())
        # permission_mode=full requires the gate to be open; we test it
        # here AND the auto path elsewhere. Use permit_full_access=True
        # via the services namespace.
        result = await CodexSendPromptTool().invoke(_ctx({
            "session_id": "s",
            "message": "hi",
            "cwd": "/tmp",
            "model": "gpt-5.2",
            "reasoning_effort": "xhigh",
            "permission_mode": "full",
            "plan_mode": False,
        }, provider=provider, permit_full_access=True))
        self.assertTrue(result.ok)
        await asyncio.wait_for(finished.wait(), timeout=2.0)
        self.assertEqual(captured["model"], "gpt-5.2")
        self.assertEqual(captured["reasoning_effort"], "xhigh")
        self.assertEqual(captured["permission_mode"], "full")
        self.assertIs(captured["plan_mode"], False)


class ListBranchesToolTest(unittest.IsolatedAsyncioTestCase):
    async def test_returns_current_and_list(self) -> None:
        outputs = [
            _fake_completed(0, "main\n"),
            _fake_completed(0, "main\nfeature/x\nbugfix/y\n"),
        ]

        def _fake(*_args, **_kwargs):
            return outputs.pop(0)

        with patch("live2d_server.master_agent.tools.git_tools._run_git_sync", side_effect=_fake):
            result = await ListBranchesTool().invoke(_ctx({"cwd": "/repo"}))
        self.assertTrue(result.ok)
        self.assertEqual(result.data["current"], "main")
        self.assertEqual(result.data["branches"], ["main", "feature/x", "bugfix/y"])

    async def test_requires_cwd(self) -> None:
        result = await ListBranchesTool().invoke(_ctx({}))
        self.assertFalse(result.ok)


class SwitchBranchToolTest(unittest.IsolatedAsyncioTestCase):
    async def test_switch_succeeds(self) -> None:
        with patch(
            "live2d_server.master_agent.tools.git_tools._run_git_sync",
            side_effect=[_fake_completed(0)],
        ) as mock:
            result = await SwitchBranchTool().invoke(
                _ctx({"branch": "feature/x", "cwd": "/repo"})
            )
        self.assertTrue(result.ok)
        self.assertEqual(result.data["via"], "switch")
        self.assertEqual(mock.call_count, 1)

    async def test_falls_back_to_checkout(self) -> None:
        with patch(
            "live2d_server.master_agent.tools.git_tools._run_git_sync",
            side_effect=[
                _fake_completed(1, "", "switch not found"),
                _fake_completed(0),
            ],
        ) as mock:
            result = await SwitchBranchTool().invoke(
                _ctx({"branch": "old", "cwd": "/repo"})
            )
        self.assertTrue(result.ok)
        self.assertEqual(result.data["via"], "checkout")
        self.assertEqual(mock.call_count, 2)

    async def test_surfaces_failure(self) -> None:
        with patch(
            "live2d_server.master_agent.tools.git_tools._run_git_sync",
            side_effect=[
                _fake_completed(1, "", "no such branch"),
                _fake_completed(1, "", "no such branch"),
            ],
        ):
            result = await SwitchBranchTool().invoke(
                _ctx({"branch": "ghost", "cwd": "/repo"})
            )
        self.assertFalse(result.ok)
        self.assertIn("no such branch", result.error)


# ---------------------------------------------------------------------------
# Registry sanity: every new tool actually registers and reports a schema.
# ---------------------------------------------------------------------------


class RegistryCoverageTest(unittest.TestCase):
    def test_all_stage2_tools_registered(self) -> None:
        from live2d_server.master_agent.api import _build_default_registry
        registry = _build_default_registry()
        names = {t.name for t in registry.all()}
        expected = {
            "codex_new_session", "codex_send_prompt",
            "claude_new_session", "claude_send_prompt",
            "query_session_status", "list_sessions", "list_subtasks",
            "list_history_sessions", "get_session_conversation",
            "search_sessions",
            "list_available_models",
            "wait_for_subtask", "abort_session", "approve_pending",
            "browse_directories",
            "list_projects", "resolve_project", "register_project",
            "list_branches", "switch_git_branch",
            "report_to_user",
        }
        self.assertEqual(names, expected)
        for tool in registry.all():
            self.assertTrue(tool.description, f"{tool.name} has no description")
            self.assertEqual(tool.parameters_schema.get("type"), "object")


if __name__ == "__main__":
    unittest.main()
