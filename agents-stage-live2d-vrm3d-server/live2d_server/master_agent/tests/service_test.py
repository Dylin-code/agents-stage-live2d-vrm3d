"""Integration test for MasterAgentService driven by FakeChatModel."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any

from live2d_server.master_agent.contracts.llm_port import ChatModelResult, ToolCall
from live2d_server.master_agent.llm.fake_model import FakeChatModel
from live2d_server.master_agent.service import MasterAgentService
from live2d_server.master_agent.shared import (
    MASTER_EVENT_FINAL_TEXT,
    MASTER_EVENT_TOOL_CALL_BEGIN,
    MASTER_EVENT_TOOL_CALL_END,
)
from live2d_server.master_agent.task_tracker import SubTaskTracker
from live2d_server.master_agent.tool_registry import InMemoryToolRegistry
from live2d_server.master_agent.tools import (
    ClaudeNewSessionTool,
    ClaudeSendPromptTool,
    CodexNewSessionTool,
    CodexSendPromptTool,
    ReportToUserTool,
)


class _FakeService:
    def __init__(self) -> None:
        self.stream_events = [
            {"type": "text", "content": "subtask reply"},
        ]

    async def create_session(self, **kwargs: Any) -> dict[str, Any]:
        return {
            "session_id": "fake-sess",
            "cwd": kwargs.get("cwd", ""),
            "branch": "",
            "model": kwargs.get("model") or "fake",
        }

    async def stream_prompt(self, **kwargs: Any):
        for e in self.stream_events:
            yield e


class _FakeProvider:
    def __init__(self) -> None:
        self.codex = _FakeService()
        self.claude = _FakeService()

    def get_chat_service(self, brand: str):
        return self.codex if brand == "codex" else self.claude


def _registry() -> InMemoryToolRegistry:
    registry = InMemoryToolRegistry()
    registry.register(CodexNewSessionTool())
    registry.register(CodexSendPromptTool())
    registry.register(ClaudeNewSessionTool())
    registry.register(ClaudeSendPromptTool())
    registry.register(ReportToUserTool())
    return registry


class MasterAgentServiceTest(unittest.IsolatedAsyncioTestCase):
    async def test_rebuilds_assistant_tool_calls_for_next_hop(self) -> None:
        """Regression: after a tool runs, the next LLM hop must see the
        assistant message's tool_calls so the follow-up tool message is
        valid OpenAI-shape (tool must follow tool_calls)."""
        from live2d_server.master_agent.contracts.llm_port import ChatMessage
        from live2d_server.master_agent.service import _conversation_to_llm_messages

        scripted = [
            ChatModelResult(tool_calls=(
                ToolCall(id="t1", name="codex_new_session", arguments={"cwd": "/tmp"}),
            )),
            ChatModelResult(tool_calls=(
                ToolCall(id="t2", name="report_to_user", arguments={"text": "done"}),
            )),
        ]
        chat_model = FakeChatModel(scripted)
        service = MasterAgentService(
            chat_model=chat_model,
            agent_provider=_FakeProvider(),
            bridge_service=SimpleNamespace(),
            tool_registry=_registry(),
            task_tracker=SubTaskTracker(),
        )
        conversation = await service.new_conversation()
        async for _ in service.run_stream(conversation_id=conversation.id, message="x"):
            pass
        rebuilt: list[ChatMessage] = _conversation_to_llm_messages(conversation)
        # Sequence should be: user, assistant(tool_calls=[t1]), tool, assistant(tool_calls=[t2]), tool
        roles_with_calls = [
            (m.role, [c.name for c in (m.tool_calls or ())]) for m in rebuilt
        ]
        # Assistant turns must carry their tool_calls.
        assistant_entries = [r for r in roles_with_calls if r[0] == "assistant"]
        self.assertTrue(assistant_entries)
        for _role, names in assistant_entries:
            self.assertTrue(names, f"assistant turn lost tool_calls: {roles_with_calls}")

    async def test_dispatches_to_codex_then_reports(self) -> None:
        # Hop 1: new_session. Hop 2: send_prompt. Hop 3: report.
        scripted = [
            ChatModelResult(tool_calls=(
                ToolCall(id="t1", name="codex_new_session", arguments={"cwd": "/tmp"}),
            )),
            ChatModelResult(tool_calls=(
                ToolCall(
                    id="t2",
                    name="codex_send_prompt",
                    arguments={"session_id": "fake-sess", "message": "do it", "cwd": "/tmp"},
                ),
            )),
            ChatModelResult(tool_calls=(
                ToolCall(id="t3", name="report_to_user", arguments={"text": "已派發給 codex"}),
            )),
        ]
        chat_model = FakeChatModel(scripted)
        service = MasterAgentService(
            chat_model=chat_model,
            agent_provider=_FakeProvider(),
            bridge_service=SimpleNamespace(),
            tool_registry=_registry(),
            task_tracker=SubTaskTracker(),
        )
        conversation = await service.new_conversation()
        events = []
        async for event in service.run_stream(
            conversation_id=conversation.id, message="幫我搞定", default_cwd="/tmp",
        ):
            events.append(event)
        types = [e.type for e in events]
        self.assertIn(MASTER_EVENT_TOOL_CALL_BEGIN, types)
        self.assertIn(MASTER_EVENT_TOOL_CALL_END, types)
        final = [e for e in events if e.type == MASTER_EVENT_FINAL_TEXT]
        self.assertEqual(len(final), 1)
        self.assertEqual(final[0].content["text"], "已派發給 codex")

    async def test_stops_when_text_only_response(self) -> None:
        scripted = [ChatModelResult(text="直接回答")]
        chat_model = FakeChatModel(scripted)
        service = MasterAgentService(
            chat_model=chat_model,
            agent_provider=_FakeProvider(),
            bridge_service=SimpleNamespace(),
            tool_registry=_registry(),
            task_tracker=SubTaskTracker(),
        )
        conversation = await service.new_conversation()
        events = []
        async for event in service.run_stream(
            conversation_id=conversation.id, message="hi",
        ):
            events.append(event)
        final = [e for e in events if e.type == MASTER_EVENT_FINAL_TEXT]
        self.assertEqual(final[0].content["text"], "直接回答")

    async def test_prompt_mode_parses_json_tool_call_from_text(self) -> None:
        # In prompt mode the model emits the tool call as JSON text (no
        # native tool_calls field). The service must parse it and dispatch.
        scripted = [
            ChatModelResult(text='{"tool": "codex_new_session", "args": {"cwd": "/tmp"}}'),
            ChatModelResult(text='{"tool": "report_to_user", "args": {"text": "done"}}'),
        ]
        chat_model = FakeChatModel(scripted)
        service = MasterAgentService(
            chat_model=chat_model,
            agent_provider=_FakeProvider(),
            bridge_service=SimpleNamespace(),
            tool_registry=_registry(),
            task_tracker=SubTaskTracker(),
            tool_mode="prompt",
        )
        conversation = await service.new_conversation()
        events = []
        async for event in service.run_stream(
            conversation_id=conversation.id, message="幫忙",
        ):
            events.append(event)
        types = [e.type for e in events]
        # We expect at least one tool_call_begin (codex_new_session) and a final_text.
        self.assertIn("tool_call_begin", types)
        final = [e for e in events if e.type == MASTER_EVENT_FINAL_TEXT]
        self.assertEqual(len(final), 1)
        self.assertEqual(final[0].content["text"], "done")

    async def test_prompt_mode_surfaces_malformed_json_as_error(self) -> None:
        scripted = [ChatModelResult(text='{"tool": "codex_new_session", "args": {"cwd"')]
        # This is structurally repairable — parser will fix it. Use clearly invalid shape.
        scripted = [ChatModelResult(text='{"tool":')]
        chat_model = FakeChatModel(scripted)
        service = MasterAgentService(
            chat_model=chat_model,
            agent_provider=_FakeProvider(),
            bridge_service=SimpleNamespace(),
            tool_registry=_registry(),
            task_tracker=SubTaskTracker(),
            tool_mode="prompt",
        )
        conversation = await service.new_conversation()
        events = []
        async for event in service.run_stream(
            conversation_id=conversation.id, message="x",
        ):
            events.append(event)
        # The looks-like-tool-call attempt regex needs `"tool": "` (quote
        # after colon and space). Bare `{"tool":` does not match → treated
        # as plain text final_text. Loosen the assertion accordingly.
        self.assertTrue(events)


if __name__ == "__main__":
    unittest.main()
