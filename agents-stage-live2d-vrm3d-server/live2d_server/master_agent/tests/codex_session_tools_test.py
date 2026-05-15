"""Tests for codex/claude session tools (mocked AgentProviderRouter)."""

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from live2d_server.master_agent.contracts.tool_port import ToolContext
from live2d_server.master_agent.task_tracker import SubTaskTracker
from live2d_server.master_agent.tools.codex_session_tools import (
    CodexNewSessionTool,
    CodexSendPromptTool,
)


class _FakeService:
    def __init__(self, *, brand: str) -> None:
        self.brand = brand
        self.create_calls: list[dict[str, Any]] = []
        self.stream_calls: list[dict[str, Any]] = []
        self.stream_events: list[dict[str, Any]] = [
            {"type": "context", "content": {"model": "gpt-5"}},
            {"type": "text", "content": "hello world"},
        ]

    async def create_session(self, **kwargs: Any) -> dict[str, Any]:
        self.create_calls.append(kwargs)
        return {
            "session_id": f"{self.brand}-sess",
            "cwd": kwargs.get("cwd", ""),
            "branch": "main",
            "model": kwargs.get("model") or "default",
        }

    async def stream_prompt(self, **kwargs: Any):
        self.stream_calls.append(kwargs)
        for event in self.stream_events:
            yield event


class _FakeProvider:
    def __init__(self, services: dict[str, _FakeService]) -> None:
        self._services = services

    def get_chat_service(self, brand: str):
        return self._services[brand]


def _ctx(args: dict, *, services, conversation_id: str = "c1") -> ToolContext:
    return ToolContext(
        conversation_id=conversation_id,
        arguments=args,
        services=services,
    )


class CodexNewSessionToolTest(unittest.IsolatedAsyncioTestCase):
    async def test_creates_session_via_provider(self) -> None:
        codex_service = _FakeService(brand="codex")
        provider = _FakeProvider({"codex": codex_service})
        tracker = SubTaskTracker()
        services = SimpleNamespace(
            agent_provider=provider,
            bridge_service=None,
            task_tracker=tracker,
            loop=None,
        )
        tool = CodexNewSessionTool()
        result = await tool.invoke(_ctx({"cwd": "/tmp/repo", "model": "gpt-5"}, services=services))
        self.assertTrue(result.ok)
        self.assertEqual(result.data["session_id"], "codex-sess")
        self.assertEqual(codex_service.create_calls[0]["cwd"], "/tmp/repo")
        self.assertEqual(codex_service.create_calls[0]["model"], "gpt-5")

    async def test_omitted_permission_mode_defaults_to_auto(self) -> None:
        """Master agent's default permission_mode is now 'auto'
        (auto-review classifier), not whatever the platform legacy
        path would pick."""
        codex_service = _FakeService(brand="codex")
        provider = _FakeProvider({"codex": codex_service})
        services = SimpleNamespace(
            agent_provider=provider, bridge_service=None,
            task_tracker=SubTaskTracker(), loop=None,
            permit_full_access=False,
        )
        result = await CodexNewSessionTool().invoke(_ctx({"cwd": "/tmp"}, services=services))
        self.assertTrue(result.ok)
        self.assertEqual(codex_service.create_calls[0]["permission_mode"], "auto")

    async def test_explicit_full_downgrades_to_auto_without_permit(self) -> None:
        """Security: the LLM cannot unilaterally pick full access."""
        codex_service = _FakeService(brand="codex")
        provider = _FakeProvider({"codex": codex_service})
        services = SimpleNamespace(
            agent_provider=provider, bridge_service=None,
            task_tracker=SubTaskTracker(), loop=None,
            permit_full_access=False,
        )
        result = await CodexNewSessionTool().invoke(
            _ctx({"cwd": "/tmp", "permission_mode": "full"}, services=services),
        )
        self.assertTrue(result.ok)
        self.assertEqual(codex_service.create_calls[0]["permission_mode"], "auto")

    async def test_explicit_full_honored_when_permit_set(self) -> None:
        """When the user typed #full and the API forwarded
        permit_full_access=True, the tool MUST honor the explicit
        permission_mode=full request."""
        codex_service = _FakeService(brand="codex")
        provider = _FakeProvider({"codex": codex_service})
        services = SimpleNamespace(
            agent_provider=provider, bridge_service=None,
            task_tracker=SubTaskTracker(), loop=None,
            permit_full_access=True,
        )
        result = await CodexNewSessionTool().invoke(
            _ctx({"cwd": "/tmp", "permission_mode": "full"}, services=services),
        )
        self.assertTrue(result.ok)
        self.assertEqual(codex_service.create_calls[0]["permission_mode"], "full")

    async def test_explicit_plan_preserved(self) -> None:
        codex_service = _FakeService(brand="codex")
        provider = _FakeProvider({"codex": codex_service})
        services = SimpleNamespace(
            agent_provider=provider, bridge_service=None,
            task_tracker=SubTaskTracker(), loop=None,
            permit_full_access=False,
        )
        result = await CodexNewSessionTool().invoke(
            _ctx({"cwd": "/tmp", "permission_mode": "plan"}, services=services),
        )
        self.assertTrue(result.ok)
        self.assertEqual(codex_service.create_calls[0]["permission_mode"], "plan")

    async def test_requires_cwd(self) -> None:
        services = SimpleNamespace(
            agent_provider=_FakeProvider({}),
            bridge_service=None,
            task_tracker=SubTaskTracker(),
            loop=None,
        )
        result = await CodexNewSessionTool().invoke(_ctx({}, services=services))
        self.assertFalse(result.ok)


class CodexSendPromptToolTest(unittest.IsolatedAsyncioTestCase):
    async def test_dispatches_subtask_and_streams_events(self) -> None:
        codex_service = _FakeService(brand="codex")
        provider = _FakeProvider({"codex": codex_service})
        tracker = SubTaskTracker()
        services = SimpleNamespace(
            agent_provider=provider,
            bridge_service=None,
            task_tracker=tracker,
            loop=None,
        )
        result = await CodexSendPromptTool().invoke(
            _ctx(
                {"session_id": "abc", "message": "do the thing", "cwd": "/tmp"},
                services=services,
            ),
        )
        self.assertTrue(result.ok)
        subtask_id = result.data["subtask_id"]
        # Drain progress until terminal — the background task should populate text + done.
        events: list[dict] = []
        async for event in tracker.wait_with_progress(subtask_id, timeout_sec=2.0):
            events.append(event)
        self.assertTrue(any(e.get("type") == "text" for e in events))
        final_status = next(
            (e for e in events if e.get("type") == "subtask_status"),
            None,
        )
        self.assertIsNotNone(final_status)
        self.assertEqual(final_status["content"]["status"], "done")
        self.assertEqual(codex_service.stream_calls[0]["session_id"], "abc")

    async def test_resolves_cwd_from_session_record_when_args_missing(self) -> None:
        """Regression: claude (and codex) ``send_prompt`` must reuse the
        session's original cwd when the LLM omits it. Without this Claude
        CLI's ``--resume`` raises "No conversation found with session ID"
        because it looks up the JSONL by cwd-encoded directory."""
        codex_service = _FakeService(brand="codex")
        provider = _FakeProvider({"codex": codex_service})
        tracker = SubTaskTracker()
        # Bridge service exposes the session's actual cwd.
        bridge = SimpleNamespace(
            get_session_record=AsyncMock(return_value=SimpleNamespace(
                session_id="abc", cwd="/project/repo",
            )),
        )
        services = SimpleNamespace(
            agent_provider=provider,
            bridge_service=bridge,
            task_tracker=tracker,
            loop=None,
        )
        result = await CodexSendPromptTool().invoke(
            _ctx({"session_id": "abc", "message": "go"}, services=services),
        )
        self.assertTrue(result.ok)
        # The background _run task drives stream_prompt; wait for the
        # subtask to drain so we can inspect what was passed in.
        subtask_id = result.data["subtask_id"]
        async for _ in tracker.wait_with_progress(subtask_id, timeout_sec=2.0):
            pass
        # The stream_prompt call must have received the session's cwd,
        # not the service default or an empty string.
        self.assertEqual(codex_service.stream_calls[0]["cwd"], "/project/repo")

    async def test_session_record_cwd_wins_over_conversation_default_cwd(self) -> None:
        """Regression: when the LLM resumes a session without passing cwd,
        the session's own cwd (from the bridge registry) must win over
        the conversation-level ``ctx.default_cwd`` hint. Otherwise Claude
        CLI's ``--resume`` runs in the wrong cwd and raises
        ``No conversation found with session ID``.

        Concrete case: a 導演 conversation hint of ``/master/cwd`` should
        not override a session that was actually spawned in ``/worker/cwd``.
        """
        codex_service = _FakeService(brand="codex")
        provider = _FakeProvider({"codex": codex_service})
        tracker = SubTaskTracker()
        bridge = SimpleNamespace(
            get_session_record=AsyncMock(return_value=SimpleNamespace(
                session_id="abc", cwd="/worker/cwd",
            )),
        )
        services = SimpleNamespace(
            agent_provider=provider,
            bridge_service=bridge,
            task_tracker=tracker,
            loop=None,
        )
        ctx = ToolContext(
            conversation_id="c1",
            arguments={"session_id": "abc", "message": "go"},
            services=services,
            default_cwd="/master/cwd",  # conversation hint that must NOT win
        )
        result = await CodexSendPromptTool().invoke(ctx)
        self.assertTrue(result.ok)
        subtask_id = result.data["subtask_id"]
        async for _ in tracker.wait_with_progress(subtask_id, timeout_sec=2.0):
            pass
        self.assertEqual(codex_service.stream_calls[0]["cwd"], "/worker/cwd")

    async def test_default_cwd_used_only_when_session_unknown(self) -> None:
        """If neither the bridge nor disk knows the session's cwd, fall
        back to ``ctx.default_cwd``. This keeps stale-codex-resume working
        when the user typed a sensible cwd hint on the page."""
        codex_service = _FakeService(brand="codex")
        provider = _FakeProvider({"codex": codex_service})
        tracker = SubTaskTracker()
        bridge = SimpleNamespace(
            get_session_record=AsyncMock(return_value=None),
        )
        services = SimpleNamespace(
            agent_provider=provider,
            bridge_service=bridge,
            task_tracker=tracker,
            loop=None,
        )
        ctx = ToolContext(
            conversation_id="c1",
            arguments={"session_id": "abc", "message": "go"},
            services=services,
            default_cwd="/fallback/cwd",
        )
        result = await CodexSendPromptTool().invoke(ctx)
        self.assertTrue(result.ok)
        subtask_id = result.data["subtask_id"]
        async for _ in tracker.wait_with_progress(subtask_id, timeout_sec=2.0):
            pass
        self.assertEqual(codex_service.stream_calls[0]["cwd"], "/fallback/cwd")

    async def test_claude_resume_uses_disk_metadata_cwd_over_default(self) -> None:
        """Regression for the actual bug: Claude session JSONL lives on
        disk under a cwd-encoded project dir; resuming it without the
        original cwd hits ``No conversation found with session ID``.

        When the bridge has no in-memory record but disk metadata knows
        the session's cwd, that disk cwd must override ``ctx.default_cwd``.
        """
        from live2d_server.master_agent.tools.claude_session_tools import (
            ClaudeSendPromptTool,
        )
        claude_service = _FakeService(brand="claude")
        provider = _FakeProvider({"claude": claude_service})
        tracker = SubTaskTracker()
        bridge = SimpleNamespace(
            get_session_record=AsyncMock(return_value=None),
            lookup_claude_session_metadata=lambda sid: {
                "session_id": sid, "cwd": "C:\\Users\\User\\Desktop\\Kokoro-Link",
            },
        )
        services = SimpleNamespace(
            agent_provider=provider,
            bridge_service=bridge,
            task_tracker=tracker,
            loop=None,
        )
        ctx = ToolContext(
            conversation_id="c1",
            arguments={"session_id": "1f20c720", "message": "continue"},
            services=services,
            default_cwd="C:\\Users\\User\\Desktop\\agents-stage-live2d-vrm3d",
        )
        result = await ClaudeSendPromptTool().invoke(ctx)
        self.assertTrue(result.ok)
        subtask_id = result.data["subtask_id"]
        async for _ in tracker.wait_with_progress(subtask_id, timeout_sec=2.0):
            pass
        self.assertEqual(
            claude_service.stream_calls[0]["cwd"],
            "C:\\Users\\User\\Desktop\\Kokoro-Link",
        )

    async def test_explicit_cwd_overrides_session_record(self) -> None:
        codex_service = _FakeService(brand="codex")
        provider = _FakeProvider({"codex": codex_service})
        tracker = SubTaskTracker()
        bridge = SimpleNamespace(
            get_session_record=AsyncMock(return_value=SimpleNamespace(
                session_id="abc", cwd="/from/registry",
            )),
        )
        services = SimpleNamespace(
            agent_provider=provider,
            bridge_service=bridge,
            task_tracker=tracker,
            loop=None,
        )
        result = await CodexSendPromptTool().invoke(
            _ctx({"session_id": "abc", "message": "go", "cwd": "/explicit"},
                 services=services),
        )
        self.assertTrue(result.ok)
        subtask_id = result.data["subtask_id"]
        async for _ in tracker.wait_with_progress(subtask_id, timeout_sec=2.0):
            pass
        self.assertEqual(codex_service.stream_calls[0]["cwd"], "/explicit")

    async def test_marks_detached_when_disk_session_still_active(self) -> None:
        """Regression: when stream_prompt dies but bridge_service shows
        the session still wrote events recently, mark subtask 'detached'
        rather than 'failed' so the LLM follows up via query_session_status
        instead of telling the user the work crashed."""
        import time as _time
        codex_service = _FakeService(brand="codex")
        # Stream raises mid-flight → simulates idle timeout.
        async def _boom_stream(**_kwargs):
            yield {"type": "text", "content": "partial-progress"}
            raise RuntimeError("codex cli idle timeout")
        codex_service.stream_prompt = _boom_stream  # type: ignore[assignment]
        provider = _FakeProvider({"codex": codex_service})
        tracker = SubTaskTracker()
        # Bridge says disk last_seen was 5 seconds ago = still alive.
        bridge = SimpleNamespace(
            get_session_record=AsyncMock(return_value=SimpleNamespace(
                session_id="abc", cwd="/p", last_seen_epoch=_time.time() - 5,
            )),
        )
        services = SimpleNamespace(
            agent_provider=provider,
            bridge_service=bridge,
            task_tracker=tracker,
            loop=None,
        )
        result = await CodexSendPromptTool().invoke(
            _ctx({"session_id": "abc", "message": "go"}, services=services),
        )
        subtask_id = result.data["subtask_id"]
        async for _ in tracker.wait_with_progress(subtask_id, timeout_sec=2.0):
            pass
        final = await tracker.get(subtask_id)
        self.assertEqual(final.status, "detached")
        self.assertIn("still active on disk", final.error)
        self.assertIn("partial-progress", final.final_text)

    async def test_marks_failed_when_disk_session_quiet(self) -> None:
        """Conversely: if disk last_seen is long ago, status=failed
        (don't pretend a dead worker is still running)."""
        import time as _time
        codex_service = _FakeService(brand="codex")
        async def _boom_stream(**_kwargs):
            raise RuntimeError("stream parse error")
            yield  # pragma: no cover — generator marker
        codex_service.stream_prompt = _boom_stream  # type: ignore[assignment]
        provider = _FakeProvider({"codex": codex_service})
        tracker = SubTaskTracker()
        bridge = SimpleNamespace(
            get_session_record=AsyncMock(return_value=SimpleNamespace(
                session_id="abc", cwd="/p", last_seen_epoch=_time.time() - 3600,
            )),
        )
        services = SimpleNamespace(
            agent_provider=provider,
            bridge_service=bridge,
            task_tracker=tracker,
            loop=None,
        )
        result = await CodexSendPromptTool().invoke(
            _ctx({"session_id": "abc", "message": "go"}, services=services),
        )
        subtask_id = result.data["subtask_id"]
        async for _ in tracker.wait_with_progress(subtask_id, timeout_sec=2.0):
            pass
        final = await tracker.get(subtask_id)
        self.assertEqual(final.status, "failed")

    async def test_handles_stream_error(self) -> None:
        codex_service = _FakeService(brand="codex")
        codex_service.stream_events = [
            {"type": "error", "content": "boom"},
        ]
        provider = _FakeProvider({"codex": codex_service})
        tracker = SubTaskTracker()
        services = SimpleNamespace(
            agent_provider=provider,
            bridge_service=None,
            task_tracker=tracker,
            loop=None,
        )
        result = await CodexSendPromptTool().invoke(
            _ctx(
                {"session_id": "abc", "message": "fail", "cwd": "/tmp"},
                services=services,
            ),
        )
        self.assertTrue(result.ok)
        subtask_id = result.data["subtask_id"]
        # Wait long enough for background task to flip status.
        await asyncio.sleep(0.05)
        task = await tracker.get(subtask_id)
        # status may be "failed" once the error event drains; allow either failed or running depending on timing.
        # Drain to confirm terminal.
        final_status_event = None
        async for event in tracker.wait_with_progress(subtask_id, timeout_sec=2.0):
            if event.get("type") == "subtask_status":
                final_status_event = event
        self.assertIsNotNone(final_status_event)
        self.assertEqual(final_status_event["content"]["status"], "failed")


if __name__ == "__main__":
    unittest.main()
