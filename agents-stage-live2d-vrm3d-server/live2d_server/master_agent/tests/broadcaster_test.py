"""Tests for the master-agent broadcaster + WS wiring."""

from __future__ import annotations

import asyncio
import json
import unittest
from typing import Any

from live2d_server.master_agent.broadcaster import (
    MasterAgentBroadcaster,
    subtask_event,
)
from live2d_server.master_agent.shared import SubTask
from live2d_server.master_agent.task_tracker import SubTaskTracker


class _FakeWs:
    """Minimal stand-in for FastAPI's WebSocket — just records sends."""

    def __init__(self, *, raise_on: int | None = None) -> None:
        self.sent: list[str] = []
        self._raise_on = raise_on
        self._writes = 0

    async def send_text(self, payload: str) -> None:
        self._writes += 1
        if self._raise_on is not None and self._writes >= self._raise_on:
            raise RuntimeError("simulated disconnect")
        self.sent.append(payload)


class BroadcasterTest(unittest.IsolatedAsyncioTestCase):
    async def test_publishes_to_all_connected(self) -> None:
        broadcaster = MasterAgentBroadcaster()
        ws1, ws2 = _FakeWs(), _FakeWs()
        await broadcaster.register(ws1)
        await broadcaster.register(ws2)
        envelope = {"event": "subtask", "type": "created", "x": 1}
        await broadcaster.publish(envelope)
        self.assertEqual(len(ws1.sent), 1)
        self.assertEqual(len(ws2.sent), 1)
        decoded = json.loads(ws1.sent[0])
        self.assertEqual(decoded["event"], "subtask")
        self.assertEqual(decoded["x"], 1)

    async def test_prunes_dead_client_on_failed_send(self) -> None:
        broadcaster = MasterAgentBroadcaster()
        good = _FakeWs()
        dead = _FakeWs(raise_on=1)  # first send fails
        await broadcaster.register(good)
        await broadcaster.register(dead)
        await broadcaster.publish({"event": "x"})
        self.assertEqual(broadcaster.client_count(), 1)
        # good still receives subsequent broadcasts
        await broadcaster.publish({"event": "y"})
        self.assertEqual(len(good.sent), 2)

    async def test_unregister_removes_client(self) -> None:
        broadcaster = MasterAgentBroadcaster()
        ws = _FakeWs()
        await broadcaster.register(ws)
        await broadcaster.unregister(ws)
        await broadcaster.publish({"event": "z"})
        self.assertEqual(ws.sent, [])


class TrackerHookTest(unittest.IsolatedAsyncioTestCase):
    async def test_hook_fires_on_create_and_status_change(self) -> None:
        events: list[tuple[str, str]] = []

        async def hook(kind: str, subtask: SubTask) -> None:
            events.append((kind, subtask.status))

        tracker = SubTaskTracker(state_change_hook=hook)
        subtask = SubTask.new(
            conversation_id="c1", agent_brand="codex",
            session_id="s", prompt="p", cwd="/",
        )
        await tracker.create(subtask)
        await tracker.update_status(subtask.id, status="running")
        await tracker.update_status(subtask.id, status="done", final_text="OK")
        self.assertEqual(events[0], ("created", "pending"))
        self.assertEqual(events[1], ("status", "running"))
        self.assertEqual(events[2], ("status", "done"))

    async def test_hook_failure_does_not_break_tracker(self) -> None:
        async def hook(kind: str, subtask: SubTask) -> None:
            raise RuntimeError("hook broken")

        tracker = SubTaskTracker(state_change_hook=hook)
        subtask = SubTask.new(
            conversation_id="c1", agent_brand="codex",
            session_id="s", prompt="p", cwd="/",
        )
        # Must not raise.
        await tracker.create(subtask)
        await tracker.update_status(subtask.id, status="running")
        fetched = await tracker.get(subtask.id)
        self.assertEqual(fetched.status, "running")


class SubtaskEventEnvelopeTest(unittest.TestCase):
    def test_envelope_carries_conversation_id_and_payload(self) -> None:
        envelope = subtask_event(
            "status", {"id": "x", "conversation_id": "c-123", "status": "done"},
        )
        self.assertEqual(envelope["event"], "subtask")
        self.assertEqual(envelope["type"], "status")
        self.assertEqual(envelope["conversation_id"], "c-123")
        self.assertEqual(envelope["subtask"]["status"], "done")


if __name__ == "__main__":
    unittest.main()
