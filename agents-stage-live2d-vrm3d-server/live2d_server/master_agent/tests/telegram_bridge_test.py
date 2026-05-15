"""Tests for the SSE-to-Telegram event bridge."""

from __future__ import annotations

import unittest
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from live2d_server.master_agent.shared import (
    MASTER_EVENT_ERROR,
    MASTER_EVENT_FINAL_TEXT,
    MASTER_EVENT_HOP_LIMIT,
    MASTER_EVENT_THINKING_DELTA,
    MASTER_EVENT_TOOL_CALL_BEGIN,
    MASTER_EVENT_TOOL_CALL_END,
    MasterEvent,
)
from live2d_server.master_agent.telegram.bridge import EventBridge


@dataclass
class _RecordedSend:
    text: str
    message_id: int


@dataclass
class _RecordedEdit:
    message_id: int
    text: str


@dataclass
class _FakeMessenger:
    """Records messenger calls and yields incrementing message ids."""

    sends: list[_RecordedSend] = field(default_factory=list)
    edits: list[_RecordedEdit] = field(default_factory=list)
    typing_count: int = 0
    edit_failures: set[int] = field(default_factory=set)
    _next_id: int = 100

    async def send_text(self, text: str) -> int:
        self._next_id += 1
        self.sends.append(_RecordedSend(text=text, message_id=self._next_id))
        return self._next_id

    async def edit_text(self, message_id: int, text: str) -> None:
        if message_id in self.edit_failures:
            raise RuntimeError("simulated edit failure")
        self.edits.append(_RecordedEdit(message_id=message_id, text=text))

    async def send_typing(self) -> None:
        self.typing_count += 1


async def _aiter(events: list[MasterEvent]) -> AsyncIterator[MasterEvent]:
    for e in events:
        yield e


class EventBridgeTest(unittest.IsolatedAsyncioTestCase):
    async def test_typing_indicator_sent_once_at_start(self) -> None:
        messenger = _FakeMessenger()
        bridge = EventBridge(messenger)
        await bridge.consume(_aiter([]))
        self.assertEqual(messenger.typing_count, 1)

    async def test_thinking_delta_is_suppressed(self) -> None:
        messenger = _FakeMessenger()
        bridge = EventBridge(messenger)
        await bridge.consume(_aiter([
            MasterEvent(type=MASTER_EVENT_THINKING_DELTA, content={"text": "..."}),
        ]))
        self.assertEqual(messenger.sends, [])
        self.assertEqual(messenger.edits, [])

    async def test_tool_call_lifecycle_edits_begin_message(self) -> None:
        messenger = _FakeMessenger()
        bridge = EventBridge(messenger)
        await bridge.consume(_aiter([
            MasterEvent(
                type=MASTER_EVENT_TOOL_CALL_BEGIN,
                content={"id": "call-1", "name": "codex_new_session"},
            ),
            MasterEvent(
                type=MASTER_EVENT_TOOL_CALL_END,
                content={
                    "id": "call-1",
                    "name": "codex_new_session",
                    "ok": True,
                    "output_text": "session ready",
                },
            ),
        ]))
        self.assertEqual(len(messenger.sends), 1)
        self.assertIn("🔧", messenger.sends[0].text)
        self.assertIn("啟動 Codex 子任務", messenger.sends[0].text)
        self.assertEqual(len(messenger.edits), 1)
        self.assertEqual(messenger.edits[0].message_id, messenger.sends[0].message_id)
        self.assertIn("✅", messenger.edits[0].text)
        self.assertIn("session ready", messenger.edits[0].text)

    async def test_tool_call_failure_shows_error(self) -> None:
        messenger = _FakeMessenger()
        bridge = EventBridge(messenger)
        await bridge.consume(_aiter([
            MasterEvent(
                type=MASTER_EVENT_TOOL_CALL_BEGIN,
                content={"id": "x", "name": "claude_send_prompt"},
            ),
            MasterEvent(
                type=MASTER_EVENT_TOOL_CALL_END,
                content={
                    "id": "x",
                    "name": "claude_send_prompt",
                    "ok": False,
                    "error": "session not found",
                },
            ),
        ]))
        self.assertIn("❌", messenger.edits[0].text)
        self.assertIn("session not found", messenger.edits[0].text)

    async def test_tool_end_without_matching_begin_falls_back_to_send(self) -> None:
        messenger = _FakeMessenger()
        bridge = EventBridge(messenger)
        await bridge.consume(_aiter([
            MasterEvent(
                type=MASTER_EVENT_TOOL_CALL_END,
                content={"id": "orphan", "name": "x", "ok": True},
            ),
        ]))
        self.assertEqual(len(messenger.sends), 1)
        self.assertEqual(messenger.edits, [])

    async def test_edit_failure_falls_back_to_send(self) -> None:
        messenger = _FakeMessenger()
        bridge = EventBridge(messenger)
        # First send returns msg 101 — mark it as un-editable.
        messenger.edit_failures.add(101)
        await bridge.consume(_aiter([
            MasterEvent(
                type=MASTER_EVENT_TOOL_CALL_BEGIN,
                content={"id": "c", "name": "report_to_user"},
            ),
            MasterEvent(
                type=MASTER_EVENT_TOOL_CALL_END,
                content={"id": "c", "name": "report_to_user", "ok": True},
            ),
        ]))
        # 1 begin + 1 fallback send.
        self.assertEqual(len(messenger.sends), 2)

    async def test_final_text_is_sent(self) -> None:
        messenger = _FakeMessenger()
        bridge = EventBridge(messenger)
        await bridge.consume(_aiter([
            MasterEvent(type=MASTER_EVENT_FINAL_TEXT, content={"text": "all done"}),
        ]))
        self.assertEqual(len(messenger.sends), 1)
        self.assertEqual(messenger.sends[0].text, "all done")

    async def test_final_text_is_chunked_when_huge(self) -> None:
        messenger = _FakeMessenger()
        bridge = EventBridge(messenger)
        # Build a body well over 3900 chars to force at least one split.
        body = ("paragraph\n" * 600).strip()
        self.assertGreater(len(body), 3900)
        await bridge.consume(_aiter([
            MasterEvent(type=MASTER_EVENT_FINAL_TEXT, content={"text": body}),
        ]))
        self.assertGreaterEqual(len(messenger.sends), 2)
        for s in messenger.sends:
            self.assertLessEqual(len(s.text), 3900)

    async def test_error_event_sends_notice(self) -> None:
        messenger = _FakeMessenger()
        bridge = EventBridge(messenger)
        await bridge.consume(_aiter([
            MasterEvent(type=MASTER_EVENT_ERROR, content="llm timeout"),
        ]))
        self.assertEqual(len(messenger.sends), 1)
        self.assertIn("❌", messenger.sends[0].text)
        self.assertIn("llm timeout", messenger.sends[0].text)

    async def test_hop_limit_event_sends_notice(self) -> None:
        messenger = _FakeMessenger()
        bridge = EventBridge(messenger)
        await bridge.consume(_aiter([
            MasterEvent(type=MASTER_EVENT_HOP_LIMIT, content="reached max hop limit (8)"),
        ]))
        self.assertEqual(len(messenger.sends), 1)
        self.assertIn("⚠️", messenger.sends[0].text)


if __name__ == "__main__":
    unittest.main()
